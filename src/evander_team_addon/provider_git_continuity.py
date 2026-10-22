"""Preserve task work across specification edits and clean ownership handoffs."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.events.provider_models import AgentExecEffectDefinition

from .models import (
    DependencyGitSource,
    RetainedTaskWork,
    TeamDispatchStatus,
    TeamGitPreparationAttemptState,
)
from .provider_git_errors import DependencyGitPreparationError


# Both backends execute this through the existing session-owned exec surface.
# Source bytes stay in Git; the host receives only commit/provenance metadata.
CONTINUITY_SCRIPT = r"""
import json, os, pathlib, subprocess, sys

class Failure(Exception):
    def __init__(self, cause, message, evidence=None):
        self.cause, self.message, self.evidence = cause, message, evidence or {}

def git(path, *args, check=True):
    result = subprocess.run(['git', '-C', path, *args], capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise Failure('workspace_receipt_mismatch', 'Retained Git provenance could not be verified: '+result.stderr.strip())
    return result

def clean(path, evidence):
    changes = git(path, 'status', '--porcelain', '--untracked-files=all').stdout.splitlines()
    if any(row and not (row.startswith('?? ') and row[3:].startswith('.evander/')) for row in changes):
        raise Failure('workspace_dirty', 'Retained task work has uncommitted changes; preserve it for recovery.', evidence)

def ancestor(path, first, second):
    return git(path, 'merge-base', '--is-ancestor', first, second, check=False).returncode == 0

def common(path):
    value = git(path, 'rev-parse', '--git-common-dir').stdout.strip()
    return str((pathlib.Path(path)/value).resolve())

def run(action, payload):
    if action == 'probe':
        candidates = []
        for source in payload['sources']:
            path = source['source_worktree_path']
            evidence = {'attempt_id':source['source_attempt_id'], 'retained_context_id':source['source_context_id'], 'worktree_path':path}
            if not pathlib.Path(path).is_dir():
                raise Failure('workspace_missing', 'A prior task checkout is unavailable; recover its recorded branch before preparing fresh work.', evidence)
            if common(path) != common(payload['repo_dir']):
                raise Failure('workspace_receipt_mismatch', 'Retained checkout belongs to a different repository.', evidence)
            if git(path, 'branch', '--show-current').stdout.strip() != source['source_branch']:
                raise Failure('workspace_receipt_mismatch', 'Retained checkout branch differs from its server-owned context.', evidence)
            clean(path, evidence)
            head = git(path, 'rev-parse', 'HEAD').stdout.strip()
            base = git(path, 'config', '--worktree', '--get', 'evander.baseCommit').stdout.strip()
            if base != source['base_commit_sha']:
                # Older implicit-contribution contracts could advance a clean
                # checkout's composed base without replacing its first receipt.
                # Only the latest server context can establish that no owned
                # commits need carrying; never infer this from live config alone.
                if not payload.get('empty_only') and head == base == source['context_base_commit_sha'] and ancestor(path, source['base_commit_sha'], head):
                    continue
                raise Failure('workspace_receipt_mismatch', 'Retained checkout base differs from its preparation receipt.', evidence)
            if payload.get('empty_only') and head != source['base_commit_sha']:
                raise Failure('policy_conflict', 'A previous owner has committed task work; explicit recovery is required before handoff.', evidence)
            if not ancestor(path, source['base_commit_sha'], head):
                raise Failure('contradictory_provenance', 'Retained commits do not descend from their recorded preparation base.', evidence)
            if head == source['base_commit_sha']:
                continue
            source['commit_sha'] = head
            source['tree_hash'] = git(path, 'rev-parse', head+'^{tree}').stdout.strip()
            candidates.append(source)
        frontier = [s for s in candidates if all(ancestor(s['source_worktree_path'], other['commit_sha'], s['commit_sha']) for other in candidates)]
        if candidates and not frontier:
            raise Failure('incompatible_source_base', 'Prior task generations contain divergent committed work; choose a reviewed recovery instead of dropping a branch.')
        compatible = [source for source in frontier if source['compatible']]
        if frontier and not compatible:
            raise Failure('incompatible_source_base', 'Committed task work exists, but its baseline or dependency pins changed; reviewed recovery is required.')
        selected = compatible[0] if compatible else None
        if selected is not None:
            selected.pop('compatible')
            selected.pop('context_base_commit_sha')
        return {'retained_work':selected}
    if action == 'target':
        path = payload['worktree_path']
        if not pathlib.Path(path).exists():
            return {'base_commit_sha':None}
        clean(path, {'worktree_path':path})
        if common(path) != common(payload['repo_dir']) or git(path, 'branch', '--show-current').stdout.strip() != payload['branch']:
            raise Failure('workspace_receipt_mismatch', 'The retry checkout does not match its recorded repository and branch.')
        base = git(path, 'config', '--worktree', '--get', 'evander.baseCommit').stdout.strip()
        if base not in payload['allowed_bases']:
            raise Failure('workspace_receipt_mismatch', 'The retry checkout has an unexpected prepared base; preserve it for recovery.')
        return {'base_commit_sha':base}
    if action != 'apply':
        raise Failure('policy_conflict', 'Unknown task continuity operation.')
    path, source = payload['worktree_path'], payload['retained_work']
    clean(path, {'worktree_path':path})
    if git(path, 'branch', '--show-current').stdout.strip() != payload['branch']:
        raise Failure('workspace_receipt_mismatch', 'The new checkout is on a different branch.')
    if git(path, 'config', '--worktree', '--get', 'evander.baseCommit').stdout.strip() != payload['base_commit_sha']:
        raise Failure('workspace_receipt_mismatch', 'The new checkout lost its immutable preparation base.')
    if git(path, 'rev-parse', source['commit_sha']+'^{tree}').stdout.strip() != source['tree_hash']:
        raise Failure('contradictory_provenance', 'The pinned retained commit has a different tree.')
    before = git(path, 'rev-parse', 'HEAD').stdout.strip()
    if ancestor(path, before, source['commit_sha']):
        git(path, 'merge', '--ff-only', '--no-edit', source['commit_sha'])
    elif not ancestor(path, source['commit_sha'], before):
        raise Failure('incompatible_source_base', 'The fresh checkout cannot fast-forward to the retained work; preserve both for reviewed recovery.')
    clean(path, {'worktree_path':path})
    return {'head_commit_sha':git(path, 'rev-parse', 'HEAD').stdout.strip()}

try:
    print(json.dumps({'success':True, **run(sys.argv[1], json.loads(sys.argv[2]))}))
except Failure as exc:
    print(json.dumps({'success':False, 'cause':exc.cause, 'message':exc.message, 'evidence':exc.evidence}))
"""


def _execute(
    preparation: Any, action: str, payload: dict[str, Any], *, session_id: str | None = None
) -> dict[str, Any]:
    provider = AgentExecEffectProvider()
    provider.initialize(
        AgentExecEffectDefinition(
            id=f"team-retained-work-{action}-{preparation.preparation_attempt_id}",
            type="effect:agent:exec",
            display_name="Preserve committed task work across preparation generations",
            session_id=session_id or preparation.batch.session_id,
            command=["python3", "-c", CONTINUITY_SCRIPT, action, json.dumps(payload)],
            working_directory="/home/agent",
            timeout_seconds=120,
            check=True,
        ),
        preparation.batch.context,
    )
    result = json.loads(
        provider.execute(resolved_config={"execution_backend": preparation.execution_backend})[
            "stdout"
        ]
    )
    if not result["success"]:
        raise DependencyGitPreparationError(
            task_id=preparation.task_id,
            cause=result["cause"],
            message=result["message"],
            evidence=result.get("evidence"),
        )
    return result


def pin_retained_task_work(preparation: Any) -> RetainedTaskWork | None:
    """Select and persist an exact source before any target checkout is changed."""

    p = preparation
    state = p.batch.state
    attempt = state.git_preparation_attempts[p.preparation_attempt_id]
    if attempt.retained_work is not None or attempt.state != TeamGitPreparationAttemptState.PENDING:
        return attempt.retained_work
    if p.dependency_composition_mode != "automatic":
        return None  # Explicit reviewed recovery already supplies its own exact source.
    contexts = [
        c
        for c in state.git_work_contexts.values()
        if c.task_id == p.task_id
        and c.worktree_path is not None
        and (c.agent_branch, c.worktree_path) != (p.agent_branch, p.expected_worktree_path)
    ]
    sources = {}
    for context in sorted(contexts, key=lambda item: (item.created_at, item.updated_at, item.id)):
        matches = [
            a
            for a in state.git_preparation_attempts.values()
            if a.task_id == p.task_id
            and a.agent_id == context.agent_id
            and a.branch == context.agent_branch
            and a.worktree_path == context.worktree_path
            and a.preparation_generation == context.preparation_generation
            and a.state == TeamGitPreparationAttemptState.COMMITTED
        ]
        evidence = {"retained_context_id": context.id, "worktree_path": context.worktree_path}
        if len(matches) != 1:
            raise DependencyGitPreparationError(
                task_id=p.task_id,
                cause="workspace_receipt_mismatch",
                evidence=evidence,
                message="Prior task work has no unique committed preparation receipt; recover its provenance before creating a fresh checkout.",
            )
        previous = matches[0]
        owner = state.agents.get(context.agent_id)
        foreign = context.agent_id != p.batch.agent_id
        namespace = f"agents/{previous.session_id}/"
        root = PurePosixPath(f"/home/agent/worktrees/{p.batch.task_worktree_repo_name}")
        path = PurePosixPath(context.worktree_path)
        if (
            owner is None
            or owner.session_id != previous.session_id
            or owner.team_id != state.projects[p.batch.project_id].team_id
            or previous.project_id != p.batch.project_id
            or (previous.session_id != p.batch.session_id) != foreign
            or previous.managed_repo_id != p.managed_repo_id
            or previous.directory_path != p.directory_path
            or previous.execution_backend != p.execution_backend
            or not context.agent_branch.startswith(namespace)
            or not path.is_relative_to(root)
            or ".." in path.parts
            or path == root
        ):
            raise DependencyGitPreparationError(
                task_id=p.task_id,
                cause="policy_conflict",
                evidence=evidence,
                message="Prior task work has incompatible owner, session, project or repository provenance; explicit recovery is required.",
            )
        if not previous.base_commit_sha:
            raise DependencyGitPreparationError(
                task_id=p.task_id,
                cause="workspace_receipt_mismatch",
                evidence=evidence,
                message="Prior task work has no authoritative preparation base.",
            )
        if foreign and (
            previous.head_commit_sha != previous.base_commit_sha
            or any(
                dispatch.status in (TeamDispatchStatus.PREPARING, TeamDispatchStatus.LEASED)
                and (
                    dispatch.agent_id == context.agent_id
                    or dispatch.session_id == previous.session_id
                )
                and (
                    p.task_id in dispatch.task_ids
                    or dispatch.id in (context.dispatch_id, previous.latest_dispatch_id)
                )
                for dispatch in state.dispatches.values()
            )
        ):
            raise DependencyGitPreparationError(
                task_id=p.task_id,
                cause="policy_conflict",
                evidence=evidence,
                message="A previous owner has retained work or an active task dispatch; explicit recovery is required before handoff.",
            )
        source_root = (
            context.dependency_composition_manifest.base_commit_sha
            if context.dependency_composition_manifest is not None
            else context.base_commit
        )
        compatible = (
            context.base_branch == p.effective_base_branch
            and (p.pinned_base_commit is None or source_root == p.pinned_base_commit)
            and [s.model_dump(mode="json") for s in context.dependency_git_sources]
            == [
                DependencyGitSource.model_validate(s).model_dump(mode="json")
                for s in p.dependency_git_sources
            ]
            and context.dependency_composition_recovery_id == p.dependency_composition_recovery_id
        )
        sources[(previous.session_id, context.agent_branch, context.worktree_path)] = {
            "source_context_id": context.id,
            "source_attempt_id": previous.id,
            "source_generation": previous.preparation_generation,
            "source_branch": context.agent_branch,
            "source_worktree_path": context.worktree_path,
            "base_commit_sha": previous.base_commit_sha,
            "context_base_commit_sha": context.base_commit,
            "root_commit_sha": source_root or previous.base_commit_sha,
            "compatible": compatible,
        }
    if not sources:
        return None
    if len(sources) > 32:
        raise DependencyGitPreparationError(
            task_id=p.task_id,
            cause="policy_conflict",
            message="Too many retained task branches for automatic continuity; explicit recovery is required.",
        )
    # A clean checkout with no owned commits is not work to transfer. Inspect
    # every historical owner in its original session before preparing a new one.
    # Foreign commits remain an explicit recovery, never an automatic import.
    by_session: dict[str, list[dict[str, Any]]] = {}
    for (session_id, _, _), source in sources.items():
        by_session.setdefault(session_id, []).append(source)
    for session_id, session_sources in by_session.items():
        if session_id != p.batch.session_id:
            _execute(
                p,
                "probe",
                {
                    "sources": session_sources,
                    "repo_dir": p.batch.project_repo["target_dir"],
                    "empty_only": True,
                },
                session_id=session_id,
            )
    owned_sources = by_session.get(p.batch.session_id)
    if not owned_sources:
        return None
    selected = _execute(
        p,
        "probe",
        {"sources": owned_sources, "repo_dir": p.batch.project_repo["target_dir"]},
    )["retained_work"]
    if selected is None:
        return None
    selected = RetainedTaskWork.model_validate(selected)

    def pin(current):
        p._validate_prepared_dependency_provenance(current)
        if current.agent_runtimes[p.batch.agent_id].active_dispatch_id != p.batch.dispatch_id:
            raise RuntimeError("The dispatch no longer owns task preparation")
        live = current.git_preparation_attempts[p.preparation_attempt_id]
        if live.state != TeamGitPreparationAttemptState.PENDING or live.retained_work not in (
            None,
            selected,
        ):
            raise RuntimeError("Retained task work changed during preparation")
        live.retained_work = selected
        return current

    p.batch.state = p.batch.repo.update(pin)
    return selected


def apply_retained_task_work(preparation: Any, *, worktree_path: str, base_commit: str) -> None:
    _execute(
        preparation,
        "apply",
        {
            "worktree_path": worktree_path,
            "branch": preparation.agent_branch,
            "base_commit_sha": base_commit,
            "retained_work": preparation.retained_work.model_dump(mode="json"),
        },
    )


def retained_target_base(preparation: Any) -> str:
    source = preparation.retained_work
    result = _execute(
        preparation,
        "target",
        {
            "worktree_path": preparation.expected_worktree_path,
            "branch": preparation.agent_branch,
            "repo_dir": preparation.batch.project_repo["target_dir"],
            "allowed_bases": [source.root_commit_sha, source.base_commit_sha],
        },
    )
    return result["base_commit_sha"] or source.root_commit_sha
