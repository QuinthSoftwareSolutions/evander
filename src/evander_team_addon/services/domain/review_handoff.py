from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from ...models import (
    AgentInboxItem,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamState,
)
from .wake import _append_agent_wake_requested_event_for_inbox_item
from .work_due import _mark_agent_work_due


def queue_actionable_inbox_item(
    state: TeamState,
    *,
    recipient_agent_id: str,
    sender_agent_id: str,
    payload: Mapping[str, Any],
    result: dict[str, Any] | None,
    now: datetime,
    priority: TeamInboxPriority,
    task_id: str | None = None,
) -> AgentInboxItem:
    """Queue actionable work and apply the shared work-due/wake lifecycle."""

    item = AgentInboxItem(
        id=f"inbox-{uuid4().hex}",
        recipient_agent_id=recipient_agent_id,
        sender_agent_id=sender_agent_id,
        task_id=task_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=True,
        priority=priority,
        payload=dict(payload),
        created_at=now,
        updated_at=now,
    )
    state.inbox[item.id] = item
    if result is not None:
        result.setdefault("created_inbox_item_ids", []).append(item.id)
    _mark_agent_work_due(state, agent_id=recipient_agent_id, now=now)
    if result is not None:
        _append_agent_wake_requested_event_for_inbox_item(
            state,
            item=item,
            result=result,
            now=now,
        )
    return item
