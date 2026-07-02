from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from ...models import GitWorkContextState, TeamState
from .common import _optional_mapping, _optional_text


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
    if (
        context.dispatch_id is not None
        and dispatch_id is not None
        and context.dispatch_id != dispatch_id
    ):
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
    deliverable["branch_context_id"] = context.id
    deliverable["git_refs"] = git_refs


__all__ = [
    "GitWorkContextValidationError",
    "bind_deliverable_git_refs_to_work_context",
]
