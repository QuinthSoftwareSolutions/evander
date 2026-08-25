from __future__ import annotations

from collections.abc import Callable, MutableMapping
from datetime import datetime
import re
from typing import Any

from ...models import (
    Deliverable,
    GitWorkContext,
    GitWorkContextState,
    TeamDispatchStatus,
    TeamState,
    TeamTaskState,
    WorktreeCleanupState,
)
from .common import _ensure_utc, _optional_mapping, _optional_text, utc_now


class GitWorkContextValidationError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        correction_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.correction_context = dict(correction_context or {})


GitCandidateCommitResolver = Callable[..., str | None]

_SERVER_VERIFIED_COMPOSITION_CANDIDATE_FIELD = (
    "_server_verified_composition_candidate_sha"
)


_TERMINAL_TASK_STATES = frozenset(
    {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
)


def require_authoritative_git_review_retry_candidate(
    state: TeamState,
    *,
    deliverable: Deliverable,
    reviewed_commit_sha: str | None,
    candidate_commit_resolver: GitCandidateCommitResolver | None,
) -> str | None:
    """Verify a retried Git review still names the server-owned branch tip."""

    git_refs = deliverable.git_refs
    recorded_candidate_shas = {
        candidate.casefold()
        for candidate in (
            reviewed_commit_sha,
            git_refs.reviewed_commit_sha,
            git_refs.submitted_commit_sha,
            git_refs.commit_sha,
        )
        if candidate is not None
    }
    has_git_metadata = any(
        (
            git_refs.branch_context_id,
            git_refs.agent_branch,
            git_refs.review_branch,
            git_refs.feature_branch,
            git_refs.release_branch,
            *recorded_candidate_shas,
        )
    )
    if not has_git_metadata:
        return None

    correction_context: dict[str, Any] = {
        "deliverable_id": deliverable.id,
        "task_id": deliverable.task_id,
        "required_action": "submit_replacement_deliverable",
    }
    if len(recorded_candidate_shas) != 1:
        raise GitWorkContextValidationError(
            code="git_review_candidate_inconsistent",
            message=(
                f"Git-backed deliverable {deliverable.id} does not have one consistent "
                "pinned candidate; submit a replacement deliverable before retrying review"
            ),
            correction_context={
                **correction_context,
                "recorded_commit_shas": sorted(recorded_candidate_shas),
            },
        )
    pinned_commit_sha = next(iter(recorded_candidate_shas))
    branch_context_id = git_refs.branch_context_id
    if branch_context_id is None:
        raise GitWorkContextValidationError(
            code="git_review_branch_context_required",
            message=(
                f"Git-backed deliverable {deliverable.id} has no branch context; "
                "submit a replacement deliverable before retrying review"
            ),
            correction_context=correction_context,
        )
    context = state.git_work_contexts.get(branch_context_id)
    if context is None:
        raise GitWorkContextValidationError(
            code="git_review_branch_context_not_found",
            message=(
                f"Git work context {branch_context_id} no longer exists; "
                "submit a replacement deliverable before retrying review"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
            },
        )
    if context.task_id != deliverable.task_id:
        raise GitWorkContextValidationError(
            code="git_review_branch_context_task_mismatch",
            message=(
                f"Git work context {branch_context_id} belongs to task "
                f"{context.task_id}, not {deliverable.task_id}"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "context_task_id": context.task_id,
            },
        )
    if git_refs.agent_branch is not None and git_refs.agent_branch != context.agent_branch:
        raise GitWorkContextValidationError(
            code="git_review_agent_branch_mismatch",
            message=(
                f"deliverable agent branch {git_refs.agent_branch!r} does not match "
                f"Git work context branch {context.agent_branch!r}"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "agent_branch": git_refs.agent_branch,
                "expected_agent_branch": context.agent_branch,
            },
        )
    task = state.tasks.get(deliverable.task_id)
    project = state.projects.get(task.project_id) if task is not None else None
    if project is None or project.git is None:
        raise GitWorkContextValidationError(
            code="git_review_project_configuration_required",
            message=(
                f"Git-backed deliverable {deliverable.id} has no managed Git project configuration"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "project_id": task.project_id if task is not None else None,
            },
        )
    if candidate_commit_resolver is None:
        raise GitWorkContextValidationError(
            code="git_review_candidate_resolution_unavailable",
            message=(
                "Git review retry cannot verify the pinned candidate because "
                "server-owned branch resolution is unavailable"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "agent_branch": context.agent_branch,
            },
        )
    try:
        authoritative_commit_sha = candidate_commit_resolver(
            managed_repo_id=project.git.managed_repo_id,
            directory_path=project.git.directory_path,
            agent_branch=context.agent_branch,
        )
    except GitWorkContextValidationError:
        raise
    except Exception as exc:
        raise GitWorkContextValidationError(
            code="git_review_candidate_resolution_failed",
            message=(f"could not resolve managed Git branch {context.agent_branch!r}: {exc}"),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "agent_branch": context.agent_branch,
            },
        ) from None
    if authoritative_commit_sha is None:
        raise GitWorkContextValidationError(
            code="git_review_candidate_branch_not_found",
            message=(
                f"managed Git branch {context.agent_branch!r} no longer exists; "
                "submit a replacement deliverable before retrying review"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "agent_branch": context.agent_branch,
            },
        )
    authoritative_commit_sha = authoritative_commit_sha.strip().casefold()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", authoritative_commit_sha) is None:
        raise GitWorkContextValidationError(
            code="git_review_candidate_resolution_failed",
            message=(
                "managed Git returned an invalid candidate commit for branch "
                f"{context.agent_branch!r}"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "agent_branch": context.agent_branch,
            },
        )
    if pinned_commit_sha != authoritative_commit_sha:
        raise GitWorkContextValidationError(
            code="git_review_candidate_mismatch",
            message=(
                f"review candidate {pinned_commit_sha!r} does not match verified "
                f"branch commit {authoritative_commit_sha!r}; submit a replacement "
                "deliverable before retrying review"
            ),
            correction_context={
                **correction_context,
                "branch_context_id": branch_context_id,
                "reviewed_commit_sha": pinned_commit_sha,
                "expected_commit_sha": authoritative_commit_sha,
                "agent_branch": context.agent_branch,
            },
        )
    return authoritative_commit_sha


def _referenced_git_work_context_ids(state: TeamState) -> set[str]:
    return {
        context_id
        for deliverable in state.deliverables.values()
        if (context_id := deliverable.git_refs.branch_context_id) is not None
    }


def authoritative_deliverable_git_base(
    state: TeamState,
    *,
    deliverable: Deliverable,
) -> tuple[str | None, str | None]:
    """Return the persisted branch/commit snapshot that owns a Git deliverable."""

    task = state.tasks.get(deliverable.task_id)
    if task is None:
        raise ValueError(
            f"deliverable {deliverable.id} references missing task {deliverable.task_id}"
        )
    context_id = deliverable.git_refs.branch_context_id
    if context_id is not None:
        context = state.git_work_contexts.get(context_id)
        if context is None:
            raise ValueError(
                f"deliverable {deliverable.id} references missing Git work context {context_id}"
            )
        if context.task_id != task.id:
            raise ValueError(
                f"deliverable {deliverable.id} Git work context {context.id} belongs "
                f"to task {context.task_id}"
            )
        if task.git_base_branch is not None and context.base_branch != task.git_base_branch:
            raise ValueError(
                f"deliverable {deliverable.id} Git work context base "
                f"{context.base_branch!r} does not match task snapshot "
                f"{task.git_base_branch!r}"
            )
        return context.base_branch or task.git_base_branch, context.base_commit

    promotion_snapshots = {
        (promotion.git_base_branch, promotion.base_commit_sha)
        for promotion_id in deliverable.git_refs.promotion_record_ids
        if (promotion := state.git_promotions.get(promotion_id)) is not None
        and promotion.git_base_branch is not None
    }
    if len(promotion_snapshots) > 1:
        raise ValueError(
            f"deliverable {deliverable.id} has conflicting Git promotion base snapshots"
        )
    if promotion_snapshots:
        branch, commit = next(iter(promotion_snapshots))
        if task.git_base_branch is not None and branch != task.git_base_branch:
            raise ValueError(
                f"deliverable {deliverable.id} promotion base {branch!r} does not "
                f"match task snapshot {task.git_base_branch!r}"
            )
        return branch, commit

    contexts = {
        (context.base_branch, context.base_commit)
        for context in state.git_work_contexts.values()
        if context.task_id == task.id and context.base_branch is not None
    }
    if len(contexts) > 1:
        raise ValueError(f"task {task.id} has conflicting authoritative Git work contexts")
    if contexts:
        branch, commit = next(iter(contexts))
        return branch, commit
    project = state.projects.get(task.project_id)
    legacy_project_base = (
        project.git.base_branch
        if task.git_base_branch is None and project is not None and project.git is not None
        else None
    )
    manifest_base = (
        deliverable.review_manifest.base_commit_sha
        if deliverable.review_manifest is not None
        else None
    )
    return task.git_base_branch or legacy_project_base, manifest_base


def mark_git_work_context_submitted(
    state: TeamState,
    *,
    branch_context_id: str | None,
    now: datetime,
) -> bool:
    """Record that a validated work context was consumed by a deliverable."""

    if branch_context_id is None:
        return False
    context = state.git_work_contexts.get(branch_context_id)
    if context is None:
        raise GitWorkContextValidationError(
            code="branch_context_not_found",
            message=f"Git work context {branch_context_id} does not exist",
            correction_context={"branch_context_id": branch_context_id},
        )
    if context.state == GitWorkContextState.SUBMITTED:
        return False
    if context.state != GitWorkContextState.ACTIVE:
        raise GitWorkContextValidationError(
            code="branch_context_not_active",
            message=f"Git work context {branch_context_id} is {context.state.value}",
            correction_context={
                "branch_context_id": branch_context_id,
                "state": context.state.value,
            },
        )
    context.state = GitWorkContextState.SUBMITTED
    context.updated_at = now
    return True


def finalize_active_git_work_contexts_for_dispatch(
    state: TeamState,
    *,
    agent_id: str,
    dispatch_id: str,
    now: datetime,
) -> dict[str, int]:
    """Close contexts that cannot be used after their owning dispatch ends."""

    referenced_context_ids = _referenced_git_work_context_ids(state)
    submitted = 0
    abandoned = 0
    for context in state.git_work_contexts.values():
        if (
            context.agent_id != agent_id
            or context.dispatch_id != dispatch_id
            or context.state != GitWorkContextState.ACTIVE
        ):
            continue
        if context.id in referenced_context_ids:
            context.state = GitWorkContextState.SUBMITTED
            submitted += 1
        else:
            context.state = GitWorkContextState.ABANDONED
            abandoned += 1
        context.updated_at = now
    return {"submitted": submitted, "abandoned": abandoned}


def finalize_active_git_work_contexts_for_task(
    state: TeamState,
    *,
    task_id: str,
    now: datetime,
) -> dict[str, int]:
    """Close active contexts when their task becomes terminal or changes owner."""

    referenced_context_ids = _referenced_git_work_context_ids(state)
    submitted = 0
    abandoned = 0
    for context in state.git_work_contexts.values():
        if context.task_id != task_id or context.state != GitWorkContextState.ACTIVE:
            continue
        if context.id in referenced_context_ids:
            context.state = GitWorkContextState.SUBMITTED
            submitted += 1
        else:
            context.state = GitWorkContextState.ABANDONED
            abandoned += 1
        context.updated_at = now
    return {"submitted": submitted, "abandoned": abandoned}


def reconcile_git_work_context_lifecycle(
    state: TeamState,
    *,
    now: datetime,
    prune_before: datetime | None = None,
) -> dict[str, int]:
    """Repair stale lifecycle state and prune old unreferenced terminal metadata."""

    effective_now = _ensure_utc(now)
    referenced_context_ids = _referenced_git_work_context_ids(state)
    submitted = 0
    abandoned = 0
    for context in state.git_work_contexts.values():
        if context.state != GitWorkContextState.ACTIVE:
            continue
        task = state.tasks.get(context.task_id)
        runtime = state.agent_runtimes.get(context.agent_id)
        dispatch = state.dispatches.get(context.dispatch_id or "")
        has_live_preparation_reservation = (
            dispatch is not None
            and dispatch.agent_id == context.agent_id
            and dispatch.status == TeamDispatchStatus.PREPARING
            and dispatch.preparation_expires_at is not None
            and _ensure_utc(dispatch.preparation_expires_at) > effective_now
        )
        should_abandon = (
            task is None
            or task.state in _TERMINAL_TASK_STATES
            or task.owner_agent_id != context.agent_id
            or (
                context.dispatch_id is not None
                and not has_live_preparation_reservation
                and (
                    runtime is None
                    or runtime.active_dispatch_id != context.dispatch_id
                    or runtime.wake_leased_until is None
                    or _ensure_utc(runtime.wake_leased_until) <= effective_now
                )
            )
        )
        if context.id in referenced_context_ids:
            context.state = GitWorkContextState.SUBMITTED
            context.updated_at = now
            submitted += 1
        elif should_abandon:
            context.state = GitWorkContextState.ABANDONED
            context.updated_at = now
            abandoned += 1

    pruned = 0
    if prune_before is not None:
        physically_cleaned_paths = {
            (
                candidate.task_id,
                candidate.agent_id,
                candidate.worktree_path,
            )
            for candidate in state.worktree_cleanup_candidates.values()
            if candidate.state == WorktreeCleanupState.SUCCEEDED
        }
        prunable_ids = [
            context.id
            for context in state.git_work_contexts.values()
            if context.state == GitWorkContextState.ABANDONED
            and context.id not in referenced_context_ids
            and context.updated_at <= prune_before
            and (
                context.worktree_path is None
                or (
                    context.task_id,
                    context.agent_id,
                    context.worktree_path,
                )
                in physically_cleaned_paths
            )
        ]
        for context_id in prunable_ids:
            del state.git_work_contexts[context_id]
        pruned = len(prunable_ids)

    return {
        "submitted": submitted,
        "abandoned": abandoned,
        "pruned": pruned,
    }


def bind_deliverable_git_refs_to_work_context(
    state: TeamState,
    *,
    agent_id: str,
    task_id: str,
    deliverable: MutableMapping[str, Any],
    dispatch_id: str | None = None,
    candidate_commit_resolver: GitCandidateCommitResolver | None = None,
) -> None:
    """Derive deliverable git_refs from a server-owned Git work context.

    Non-Git deliverables do not need a branch context. Once any Git metadata is
    present, the branch must come from Team state instead of agent-authored text.
    """

    git_refs = dict(_optional_mapping(deliverable.get("git_refs"), "git_refs") or {})
    branch_context_id = _optional_text(deliverable.get("branch_context_id")) or _optional_text(
        git_refs.get("branch_context_id")
    )
    supplied_agent_branch = _optional_text(git_refs.get("agent_branch"))
    supplied_commit_sha = _optional_text(git_refs.get("commit_sha"))
    has_git_metadata = any((branch_context_id, supplied_agent_branch, supplied_commit_sha))
    if not has_git_metadata:
        active_contexts = sorted(
            (
                context
                for context in state.git_work_contexts.values()
                if context.state == GitWorkContextState.ACTIVE
                and context.agent_id == agent_id
                and context.task_id == task_id
                and context.dispatch_id in {None, dispatch_id}
            ),
            key=lambda context: context.id,
        )
        if not active_contexts:
            return
        if len(active_contexts) != 1:
            raise GitWorkContextValidationError(
                code="git_submission_context_ambiguous",
                message=(
                    f"task {task_id} has multiple active Git work contexts for "
                    "this dispatch; include branch_context_id explicitly"
                ),
                correction_context={
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "dispatch_id": dispatch_id,
                    "branch_context_ids": [context.id for context in active_contexts],
                    "required_field": "branch_context_id",
                },
            )
        inferred_context = active_contexts[0]
        authoritative_commit_sha = _resolve_git_context_branch_commit(
            state,
            context=inferred_context,
            task_id=task_id,
            candidate_commit_resolver=candidate_commit_resolver,
            require_live_resolution=True,
        )
        inferred_context.commit_sha = authoritative_commit_sha
        inferred_context.updated_at = utc_now()
        base_commit_sha = _optional_text(inferred_context.base_commit)
        if base_commit_sha is None:
            raise GitWorkContextValidationError(
                code="git_submission_base_commit_unavailable",
                message=(
                    f"Git work context {inferred_context.id} has no pinned base "
                    "commit, so an artifact-only submission cannot prove that "
                    "the task branch is unmodified"
                ),
                correction_context={
                    "branch_context_id": inferred_context.id,
                    "task_id": task_id,
                    "agent_branch": inferred_context.agent_branch,
                    "expected_commit_sha": authoritative_commit_sha,
                    "required_action": "submit_with_git_refs",
                },
            )
        if authoritative_commit_sha == base_commit_sha.casefold():
            # A task may legitimately produce only an external artifact even
            # though its dispatch received a project worktree. Do not turn an
            # unchanged checkout into a Git-backed deliverable.
            return
        deliverable["branch_context_id"] = inferred_context.id
        deliverable["git_refs"] = {
            "branch_context_id": inferred_context.id,
            "agent_branch": inferred_context.agent_branch,
            "commit_sha": authoritative_commit_sha,
            "submitted_commit_sha": authoritative_commit_sha,
        }
        if inferred_context.dependency_composition_manifest is not None:
            deliverable[_SERVER_VERIFIED_COMPOSITION_CANDIDATE_FIELD] = (
                authoritative_commit_sha
            )
        return
    if branch_context_id is None:
        raise GitWorkContextValidationError(
            code="branch_context_required",
            message="Git deliverables require branch_context_id",
            correction_context={
                "task_id": task_id,
                "agent_id": agent_id,
                "agent_branch": supplied_agent_branch,
                "commit_sha": supplied_commit_sha,
            },
        )
    context = state.git_work_contexts.get(branch_context_id)
    if context is None:
        raise GitWorkContextValidationError(
            code="branch_context_not_found",
            message=f"Git work context {branch_context_id} does not exist",
            correction_context={
                "branch_context_id": branch_context_id,
                "task_id": task_id,
                "agent_id": agent_id,
            },
        )
    if context.state != GitWorkContextState.ACTIVE:
        raise GitWorkContextValidationError(
            code="branch_context_not_active",
            message=f"Git work context {branch_context_id} is {context.state.value}",
            correction_context={
                "branch_context_id": branch_context_id,
                "state": context.state.value,
            },
        )
    if context.agent_id != agent_id:
        raise GitWorkContextValidationError(
            code="branch_context_agent_mismatch",
            message=(
                f"Git work context {branch_context_id} belongs to agent "
                f"{context.agent_id}, not {agent_id}"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "context_agent_id": context.agent_id,
                "caller_agent_id": agent_id,
            },
        )
    if context.task_id != task_id:
        raise GitWorkContextValidationError(
            code="branch_context_task_mismatch",
            message=(
                f"Git work context {branch_context_id} belongs to task "
                f"{context.task_id}, not {task_id}"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "context_task_id": context.task_id,
                "deliverable_task_id": task_id,
            },
        )
    task = state.tasks.get(task_id)
    if task is None:
        raise GitWorkContextValidationError(
            code="branch_context_task_not_found",
            message=f"Git work context {branch_context_id} references missing task {task_id}",
            correction_context={
                "branch_context_id": branch_context_id,
                "task_id": task_id,
            },
        )
    if task.state in _TERMINAL_TASK_STATES:
        raise GitWorkContextValidationError(
            code="branch_context_task_terminal",
            message=(
                f"Git work context {branch_context_id} cannot be used because task "
                f"{task_id} is {task.state.value}"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "task_id": task_id,
                "task_state": task.state.value,
            },
        )
    if context.dispatch_id is not None:
        if dispatch_id is None:
            raise GitWorkContextValidationError(
                code="branch_context_dispatch_required",
                message=(
                    f"Git work context {branch_context_id} belongs to dispatch "
                    f"{context.dispatch_id}; an active dispatch is required"
                ),
                correction_context={
                    "branch_context_id": branch_context_id,
                    "context_dispatch_id": context.dispatch_id,
                    "active_dispatch_id": dispatch_id,
                },
            )
        if context.dispatch_id != dispatch_id:
            raise GitWorkContextValidationError(
                code="branch_context_dispatch_mismatch",
                message=(
                    f"Git work context {branch_context_id} belongs to dispatch "
                    f"{context.dispatch_id}, not {dispatch_id}"
                ),
                correction_context={
                    "branch_context_id": branch_context_id,
                    "context_dispatch_id": context.dispatch_id,
                    "active_dispatch_id": dispatch_id,
                },
            )
    if supplied_agent_branch is not None and supplied_agent_branch != context.agent_branch:
        raise GitWorkContextValidationError(
            code="agent_branch_mismatch",
            message=(
                f"agent_branch {supplied_agent_branch!r} does not match expected "
                f"branch {context.agent_branch!r}"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "agent_branch": supplied_agent_branch,
                "expected_agent_branch": context.agent_branch,
            },
        )
    if supplied_commit_sha is None:
        raise GitWorkContextValidationError(
            code="git_candidate_commit_required",
            message=(
                "Git deliverables require git_refs.commit_sha so the submitted "
                "candidate can be checked against the server-owned branch"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "task_id": task_id,
                "required_field": "git_refs.commit_sha",
            },
        )

    authoritative_commit_sha = _resolve_git_context_branch_commit(
        state,
        context=context,
        task_id=task_id,
        candidate_commit_resolver=candidate_commit_resolver,
        require_live_resolution=False,
    )
    context.commit_sha = authoritative_commit_sha
    context.updated_at = utc_now()

    if supplied_commit_sha.casefold() != authoritative_commit_sha:
        raise GitWorkContextValidationError(
            code="commit_sha_mismatch",
            message=(
                f"commit_sha {supplied_commit_sha!r} does not match verified "
                f"branch commit {authoritative_commit_sha!r}"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "commit_sha": supplied_commit_sha,
                "expected_commit_sha": authoritative_commit_sha,
                "agent_branch": context.agent_branch,
                "required_action": "publish_candidate_with_git_push_agent_then_retry",
                "publish_command": "git push-agent",
            },
        )

    git_refs["branch_context_id"] = context.id
    git_refs["agent_branch"] = context.agent_branch
    resolved_commit_sha = authoritative_commit_sha
    git_refs["commit_sha"] = resolved_commit_sha
    submitted_commit_sha = _optional_text(git_refs.get("submitted_commit_sha"))
    if submitted_commit_sha not in {None, resolved_commit_sha}:
        raise GitWorkContextValidationError(
            code="submitted_commit_sha_mismatch",
            message=(
                f"submitted_commit_sha {submitted_commit_sha!r} does not match "
                f"verified context commit {resolved_commit_sha!r}"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "submitted_commit_sha": submitted_commit_sha,
                "expected_commit_sha": resolved_commit_sha,
            },
        )
    git_refs["submitted_commit_sha"] = resolved_commit_sha
    deliverable["branch_context_id"] = context.id
    deliverable["git_refs"] = git_refs
    if context.dependency_composition_manifest is not None:
        deliverable[_SERVER_VERIFIED_COMPOSITION_CANDIDATE_FIELD] = resolved_commit_sha


def _resolve_git_context_branch_commit(
    state: TeamState,
    *,
    context: GitWorkContext,
    task_id: str,
    candidate_commit_resolver: GitCandidateCommitResolver | None,
    require_live_resolution: bool,
) -> str:
    recorded_commit_sha = _optional_text(context.commit_sha)
    prepared_base_commit = _optional_text(context.base_commit)
    # New Team preparation records full object IDs. Keep legacy contexts with
    # pre-object-ID placeholder values readable, but never treat those values
    # as ancestry evidence for managed Git.
    prepared_base_ancestor = (
        prepared_base_commit.casefold()
        if prepared_base_commit is not None
        and re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", prepared_base_commit)
        else None
    )
    composition_manifest = context.dependency_composition_manifest
    composition_ancestor_shas = (
        [
            composition_manifest.composed_commit_sha,
            *[
                source.commit_sha
                for source in composition_manifest.dependency_git_sources
            ],
        ]
        if composition_manifest is not None
        else []
    )
    required_ancestor_shas = list(
        dict.fromkeys(
            [
                *([prepared_base_ancestor] if prepared_base_ancestor is not None else []),
                *composition_ancestor_shas,
            ]
        )
    )
    if (
        candidate_commit_resolver is None
        and not require_live_resolution
        and recorded_commit_sha is not None
        and not required_ancestor_shas
    ):
        return recorded_commit_sha.casefold()
    task = state.tasks.get(task_id)
    project = state.projects.get(task.project_id) if task is not None else None
    if project is None or project.git is None:
        raise GitWorkContextValidationError(
            code="git_project_configuration_required",
            message=(
                f"task {task_id} has a Git work context but its project has no "
                "managed Git configuration"
            ),
            correction_context={
                "branch_context_id": context.id,
                "task_id": task_id,
                "project_id": task.project_id if task is not None else None,
            },
        )
    if candidate_commit_resolver is None:
        raise GitWorkContextValidationError(
            code="git_candidate_commit_resolution_unavailable",
            message=(
                "Git deliverable submission cannot verify the current task "
                "branch because server-owned branch resolution is unavailable"
            ),
            correction_context={
                "branch_context_id": context.id,
                "task_id": task_id,
                "agent_branch": context.agent_branch,
                "required_action": "retry_submission_when_git_resolution_is_available",
            },
        )
    try:
        resolver_kwargs: dict[str, Any] = {
            "managed_repo_id": project.git.managed_repo_id,
            "directory_path": project.git.directory_path,
            "agent_branch": context.agent_branch,
        }
        if required_ancestor_shas:
            resolver_kwargs["required_ancestor_shas"] = required_ancestor_shas
        authoritative_commit_sha = candidate_commit_resolver(
            **resolver_kwargs,
        )
    except GitWorkContextValidationError:
        raise
    except Exception as exc:
        ancestry_verification = bool(required_ancestor_shas)
        composition_ancestry_verification = composition_manifest is not None
        raise GitWorkContextValidationError(
            code=(
                "git_candidate_composition_ancestry_failed"
                if composition_ancestry_verification
                else "git_candidate_prepared_base_ancestry_failed"
                if ancestry_verification
                else "git_candidate_commit_resolution_failed"
            ),
            message=(
                f"could not verify dependency composition ancestry for managed Git "
                f"branch {context.agent_branch!r}: {exc}"
                if composition_ancestry_verification
                else f"could not verify prepared base ancestry for managed Git "
                f"branch {context.agent_branch!r}: {exc}"
                if ancestry_verification
                else f"could not resolve managed Git branch {context.agent_branch!r}: {exc}"
            ),
            correction_context={
                "branch_context_id": context.id,
                "task_id": task_id,
                "agent_branch": context.agent_branch,
                **(
                    {"prepared_base_commit": prepared_base_ancestor}
                    if prepared_base_ancestor is not None
                    else {}
                ),
                **(
                    {"required_ancestor_shas": required_ancestor_shas}
                    if ancestry_verification
                    else {}
                ),
            },
        ) from None
    if authoritative_commit_sha is None:
        raise GitWorkContextValidationError(
            code="git_candidate_branch_not_found",
            message=(
                f"managed Git branch {context.agent_branch!r} does not exist; "
                "push the task branch before submitting the deliverable"
            ),
            correction_context={
                "branch_context_id": context.id,
                "task_id": task_id,
                "agent_branch": context.agent_branch,
            },
        )
    authoritative_commit_sha = authoritative_commit_sha.strip().casefold()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", authoritative_commit_sha) is None:
        raise GitWorkContextValidationError(
            code="git_candidate_commit_resolution_failed",
            message=(
                "managed Git returned an invalid candidate commit for branch "
                f"{context.agent_branch!r}"
            ),
            correction_context={
                "branch_context_id": context.id,
                "task_id": task_id,
                "agent_branch": context.agent_branch,
            },
        )
    return authoritative_commit_sha


__all__ = [
    "GitCandidateCommitResolver",
    "GitWorkContextValidationError",
    "_SERVER_VERIFIED_COMPOSITION_CANDIDATE_FIELD",
    "authoritative_deliverable_git_base",
    "bind_deliverable_git_refs_to_work_context",
    "finalize_active_git_work_contexts_for_dispatch",
    "finalize_active_git_work_contexts_for_task",
    "mark_git_work_context_submitted",
    "reconcile_git_work_context_lifecycle",
    "require_authoritative_git_review_retry_candidate",
]
