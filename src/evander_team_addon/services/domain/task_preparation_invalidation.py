from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ...models import (
    GitWorkContextState,
    TeamInboxPriority,
    TeamState,
)
from .preparation_failures import task_git_work_context_generation
from .review_handoff import queue_actionable_inbox_item


def invalidate_task_preparation_after_edit(
    state: TeamState,
    *,
    task_id: str,
    preparation_generation_before: str,
    changed_fields: Sequence[str],
    sender_agent_id: str | None,
    result: dict[str, Any],
    now: datetime,
) -> list[str]:
    """Retire active preparation whose immutable task inputs changed.

    The old context and physical worktree remain durable evidence.  A later
    wake prepares a new generation, while cleanup applies its normal safety
    checks to the abandoned context.
    """

    task = state.tasks.get(task_id)
    if task is None:
        return []
    preparation_generation_after = task_git_work_context_generation(state, task.id)
    if preparation_generation_after == preparation_generation_before:
        return []

    invalidated_context_ids: list[str] = []
    for context in sorted(state.git_work_contexts.values(), key=lambda item: item.id):
        if context.task_id != task.id or context.state != GitWorkContextState.ACTIVE:
            continue
        context.state = GitWorkContextState.ABANDONED
        context.updated_at = now
        invalidated_context_ids.append(context.id)
    if not invalidated_context_ids:
        return []

    item = queue_actionable_inbox_item(
        state,
        recipient_agent_id=task.owner_agent_id,
        sender_agent_id=sender_agent_id,
        task_id=task.id,
        priority=TeamInboxPriority.HIGH,
        payload={
            "kind": "task_preparation_invalidated",
            "task_id": task.id,
            "changed_fields": sorted(set(changed_fields)),
            "invalidated_git_work_context_ids": invalidated_context_ids,
            "preparation_generation_before": preparation_generation_before,
            "preparation_generation_after": preparation_generation_after,
            "required_action": "reprepare_from_current_task_specification",
            "summary": (
                "The task specification changed after Git preparation. The prior "
                "context was abandoned; prepare a fresh context before submitting."
            ),
        },
        result=result,
        now=now,
    )
    result.setdefault("created_preparation_invalidation_inbox_item_ids", []).append(
        item.id
    )
    result.setdefault("invalidated_git_work_context_ids", []).extend(
        invalidated_context_ids
    )
    result["preparation_generation_before"] = preparation_generation_before
    result["preparation_generation_after"] = preparation_generation_after
    return invalidated_context_ids


__all__ = ["invalidate_task_preparation_after_edit"]
