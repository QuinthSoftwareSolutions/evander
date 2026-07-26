from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime
from typing import Any

from ...models import (
    GitWorkContextState,
    TeamState,
    TeamTaskState,
    WorktreeCleanupState,
)
from .common import _ensure_utc, _optional_mapping, _optional_text


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


_TERMINAL_TASK_STATES = frozenset(
    {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
    }
)


def _referenced_git_work_context_ids(state: TeamState) -> set[str]:
    return {
        context_id
        for deliverable in state.deliverables.values()
        if (context_id := deliverable.git_refs.branch_context_id) is not None
    }


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
        should_abandon = (
            task is None
            or task.state in _TERMINAL_TASK_STATES
            or task.owner_agent_id != context.agent_id
            or (
                context.dispatch_id is not None
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
    if context.commit_sha is not None and supplied_commit_sha not in {None, context.commit_sha}:
        raise GitWorkContextValidationError(
            code="commit_sha_mismatch",
            message=(
                f"commit_sha {supplied_commit_sha!r} does not match verified "
                f"context commit {context.commit_sha!r}"
            ),
            correction_context={
                "branch_context_id": branch_context_id,
                "commit_sha": supplied_commit_sha,
                "expected_commit_sha": context.commit_sha,
            },
        )

    git_refs["branch_context_id"] = context.id
    git_refs["agent_branch"] = context.agent_branch
    resolved_commit_sha = supplied_commit_sha or context.commit_sha
    if resolved_commit_sha is not None:
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


__all__ = [
    "GitWorkContextValidationError",
    "bind_deliverable_git_refs_to_work_context",
    "finalize_active_git_work_contexts_for_dispatch",
    "finalize_active_git_work_contexts_for_task",
    "mark_git_work_context_submitted",
    "reconcile_git_work_context_lifecycle",
]
