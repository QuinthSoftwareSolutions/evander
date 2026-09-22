"""Provider git metadata."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import (
    Mapping,
)
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from evander.git_identity import (
    DEFAULT_AGENT_GIT_IDENTITY,
    GitIdentity,
)

from .models import (
    TeamGitPreparationAttempt,
    TeamGitPreparationAttemptState,
    TeamState,
    TeamTaskKind,
    WorktreeCleanupCandidate,
)
from .provider_projection import (
    _mapping_items,
)
from .provider_values import (
    _optional_text,
    _required_text,
)
from .repository import (
    TeamStateRepository,
)
from .services.domain.common import (
    _slug_id_part,
)
from .services.domain.preparation_failures import (
    task_git_work_context_generation,
)


def _project_repo_payload(
    *,
    project_id: str,
    managed_repo_id: str,
    clone_result: Mapping[str, Any],
    base_branch: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prepared": True,
        "project_id": project_id,
        "managed_repo_id": managed_repo_id,
    }
    for key in (
        "target_dir",
        "guest_repo_name",
        "remote_url",
        "pr_notes_ref",
        "execution_backend",
        "vm_name",
        "message",
        "commit_sha",
        "pinned_ref",
        "pinned_commit_sha",
    ):
        value = _optional_text(clone_result.get(key))
        if value is not None:
            payload[key] = value
    if base_branch is not None:
        payload["base_branch"] = base_branch
    return payload


def _resolved_project_git_identity(value: object) -> GitIdentity:
    if value is None:
        return DEFAULT_AGENT_GIT_IDENTITY
    if isinstance(value, Mapping):
        name = value.get("name")
        email = value.get("email")
    else:
        name = getattr(value, "name", None)
        email = getattr(value, "email", None)
    return GitIdentity(
        name=_required_text(name, "project Git identity name"),
        email=_required_text(email, "project Git identity email"),
    )


def _project_repo_task_preparation_generation(
    state: TeamState,
    *,
    task_id: str,
    effective_base_branch: str | None,
) -> str:
    generation_task = state.tasks.get(task_id)
    if (
        generation_task is None
        or generation_task.git_base_branch is not None
        or effective_base_branch is None
    ):
        return task_git_work_context_generation(state, task_id)
    generation_state = state.model_copy(
        update={
            "tasks": {
                **state.tasks,
                task_id: generation_task.model_copy(
                    update={"git_base_branch": effective_base_branch},
                    deep=True,
                ),
            }
        },
        deep=False,
    )
    return task_git_work_context_generation(generation_state, task_id)


def _project_repo_task_worktree_name(
    *,
    task_id: str,
    preparation_generation: str | None,
    identity_commit: str | None,
) -> str:
    task_slug = _slug_id_part(task_id, fallback="task")[:40]
    task_hash = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:8]
    candidate_suffix = f"-{identity_commit.casefold()[:12]}" if identity_commit else ""
    generation_suffix = ""
    if preparation_generation is not None:
        normalized_generation = preparation_generation.strip().casefold()
        if re.fullmatch(r"[0-9a-f]{20}", normalized_generation) is None:
            raise ValueError("preparation_generation must be a 20-character hex fingerprint")
        generation_suffix = f"-prep-{normalized_generation[:12]}"
    return f"{task_slug}-{task_hash}{candidate_suffix}{generation_suffix}"


def _project_repo_git_preparation_attempt_id(
    *,
    agent_id: str,
    session_id: str,
    task_id: str,
    managed_repo_id: str,
    branch: str,
    preparation_generation: str,
) -> str:
    material = json.dumps(
        {
            "agent_id": agent_id,
            "session_id": session_id,
            "task_id": task_id,
            "managed_repo_id": managed_repo_id,
            "branch": branch,
            "preparation_generation": preparation_generation,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"gitprep-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _ensure_project_repo_git_preparation_attempt(
    repo: TeamStateRepository,
    *,
    attempt_id: str,
    agent_id: str,
    session_id: str,
    task_id: str,
    project_id: str,
    dispatch_id: str,
    managed_repo_id: str,
    directory_path: str,
    execution_backend: str,
    worktree_path: str,
    branch: str,
    base_ref: str,
    preparation_generation: str,
) -> TeamState:
    now = datetime.now(UTC)

    def _mutate(state: TeamState) -> TeamState:
        for prior in state.git_preparation_attempts.values():
            if (
                prior.id == attempt_id
                or prior.agent_id != agent_id
                or prior.task_id != task_id
                or prior.state
                in {
                    TeamGitPreparationAttemptState.COMMITTED,
                    TeamGitPreparationAttemptState.CLEANUP_PENDING,
                    TeamGitPreparationAttemptState.OPERATOR_ACTION_REQUIRED,
                }
            ):
                continue
            prior.updated_at = now
            prior.last_error = "task preparation inputs changed before the prior attempt committed"
            if prior.base_commit_sha is None or prior.head_commit_sha is None:
                prior.state = TeamGitPreparationAttemptState.OPERATOR_ACTION_REQUIRED
                continue
            prior.state = TeamGitPreparationAttemptState.CLEANUP_PENDING
            cleanup_id = f"cleanup-{prior.id}"
            state.worktree_cleanup_candidates.setdefault(
                cleanup_id,
                WorktreeCleanupCandidate(
                    id=cleanup_id,
                    agent_id=prior.agent_id,
                    session_id=prior.session_id,
                    task_id=prior.task_id,
                    project_id=prior.project_id,
                    git_work_context_id=f"preparation:{prior.id}",
                    managed_repo_id=prior.managed_repo_id,
                    directory_path=prior.directory_path,
                    execution_backend=prior.execution_backend,
                    worktree_path=prior.worktree_path,
                    branch=prior.branch,
                    base_ref=prior.base_ref,
                    expected_head=prior.head_commit_sha,
                    retained_commit_sha=None,
                    reason="preparation_invalidated",
                    eligible_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            )

        expected = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id=agent_id,
            session_id=session_id,
            task_id=task_id,
            project_id=project_id,
            latest_dispatch_id=dispatch_id,
            managed_repo_id=managed_repo_id,
            directory_path=directory_path,
            execution_backend=execution_backend,
            worktree_path=worktree_path,
            branch=branch,
            base_ref=base_ref,
            preparation_generation=preparation_generation,
            created_at=now,
            updated_at=now,
        )
        existing = state.git_preparation_attempts.get(attempt_id)
        if existing is None:
            state.git_preparation_attempts[attempt_id] = expected
            return state
        identity_fields = (
            "agent_id",
            "session_id",
            "task_id",
            "project_id",
            "managed_repo_id",
            "directory_path",
            "execution_backend",
            "worktree_path",
            "branch",
            "base_ref",
            "preparation_generation",
        )
        if any(getattr(existing, name) != getattr(expected, name) for name in identity_fields):
            raise RuntimeError(
                f"Git preparation attempt {attempt_id} already exists with different identity"
            )
        if existing.state in {
            TeamGitPreparationAttemptState.CLEANUP_PENDING,
            TeamGitPreparationAttemptState.OPERATOR_ACTION_REQUIRED,
        }:
            raise RuntimeError(
                f"Git preparation attempt {attempt_id} requires recovery before reuse"
            )
        existing.latest_dispatch_id = dispatch_id
        existing.updated_at = now
        existing.last_error = None
        return state

    return repo.update(_mutate)


def _record_project_repo_git_preparation_receipt(
    repo: TeamStateRepository,
    *,
    attempt_id: str,
    receipt: Mapping[str, Any],
    base_commit_sha: str,
    worktree_path: str,
) -> TeamState:
    now = datetime.now(UTC)

    def _mutate(state: TeamState) -> TeamState:
        attempt = state.git_preparation_attempts.get(attempt_id)
        if attempt is None:
            raise RuntimeError(f"Git preparation attempt {attempt_id} is missing")
        if attempt.state in {
            TeamGitPreparationAttemptState.CLEANUP_PENDING,
            TeamGitPreparationAttemptState.OPERATOR_ACTION_REQUIRED,
        }:
            raise RuntimeError(f"Git preparation attempt {attempt_id} is no longer current")
        fingerprint = _required_text(
            receipt.get("request_fingerprint"),
            "preparation request fingerprint",
        )
        head_commit = _required_text(
            receipt.get("head_commit_sha"),
            "prepared head commit",
        ).casefold()
        if attempt.worker_request_fingerprint not in {None, fingerprint}:
            raise RuntimeError(
                f"Git preparation attempt {attempt_id} returned a different worker receipt"
            )
        if attempt.base_commit_sha not in {None, base_commit_sha.casefold()}:
            raise RuntimeError(
                f"Git preparation attempt {attempt_id} returned a different base commit"
            )
        attempt.worker_request_fingerprint = fingerprint
        attempt.worktree_path = _required_text(worktree_path, "prepared worktree path")
        attempt.base_commit_sha = base_commit_sha.casefold()
        attempt.head_commit_sha = head_commit
        attempt.state = TeamGitPreparationAttemptState.PREPARED
        attempt.prepared_at = attempt.prepared_at or now
        attempt.updated_at = now
        attempt.last_error = None
        return state

    return repo.update(_mutate)


def _project_repo_context_tasks(
    tasks: object,
    *,
    project_id: str,
    agent_id: str,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for task in _mapping_items(tasks):
        task_id = _optional_text(task.get("id"))
        if task_id is None or task_id in seen:
            continue
        if _optional_text(task.get("project_id")) != project_id:
            continue
        if _optional_text(task.get("owner_agent_id")) != agent_id:
            continue
        if _optional_text(task.get("task_kind")) == TeamTaskKind.COORDINATION.value:
            continue
        seen.add(task_id)
        rows.append(task)
    return rows


def _git_work_context_prompt_payload(context: Any) -> dict[str, Any]:
    payload = context.model_dump(mode="json") if hasattr(context, "model_dump") else dict(context)
    context_id = _required_text(payload.get("id"), "git_work_context.id")
    row: dict[str, Any] = {
        "id": context_id,
        "branch_context_id": context_id,
    }
    for key in (
        "agent_id",
        "task_id",
        "agent_branch",
        "base_branch",
        "base_commit",
        "commit_sha",
        "dispatch_id",
        "worktree_path",
        "state",
        "preparation_generation",
    ):
        value = _optional_text(payload.get(key))
        if value is not None:
            row[key] = value
    dependency_sources = _mapping_items(payload.get("dependency_git_sources"))
    if dependency_sources:
        row["dependency_git_sources"] = dependency_sources
    composition_manifest = payload.get("dependency_composition_manifest")
    if isinstance(composition_manifest, Mapping):
        row["dependency_composition_manifest"] = dict(composition_manifest)
    return row


def _project_repo_prepare_record(
    *,
    agent: Mapping[str, Any],
    dispatch_id: str,
    project_repo: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "agent_id": agent.get("id"),
        "session_id": agent.get("session_id"),
        "project_ids": agent.get("project_ids") or [],
        "dispatch_id": dispatch_id,
    }
    if project_repo is not None:
        record["project_repo"] = dict(project_repo)
        target_dir = _optional_text(project_repo.get("target_dir"))
        if target_dir is not None:
            record["target_dir"] = target_dir
    if error is not None:
        record["error"] = error
    return record
