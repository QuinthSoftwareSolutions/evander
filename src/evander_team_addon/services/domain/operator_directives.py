from __future__ import annotations

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


def append_operator_directive(
    state: TeamState,
    *,
    team_id: str,
    recipient_agent_id: str,
    message: str,
    result: dict[str, Any],
    now: datetime,
    project_id: str | None = None,
    task_id: str | None = None,
    title: str | None = None,
    source: str | None = None,
    source_message_id: str | None = None,
    created_by: str | None = None,
    requesting_session_id: str | None = None,
) -> AgentInboxItem:
    """Append a validated directive and its durable-wake intent to Team state."""

    payload: dict[str, Any] = {
        "title": title or "Operator directive",
        "message": message,
        "team_id": team_id,
    }
    if project_id is not None:
        payload["project_id"] = project_id
    if source is not None:
        payload["source"] = source
    if source_message_id is not None:
        payload["source_message_id"] = source_message_id
    if created_by is not None:
        payload["created_by"] = created_by
    if requesting_session_id is not None:
        payload["requesting_session_id"] = requesting_session_id

    inbox_id = f"inbox-{uuid4().hex}"
    item = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=recipient_agent_id,
        sender_agent_id=None,
        task_id=task_id,
        item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
        response_requested=True,
        priority=TeamInboxPriority.NORMAL,
        payload=payload,
        created_at=now,
        updated_at=now,
    )
    state.inbox[inbox_id] = item
    _mark_agent_work_due(state, agent_id=recipient_agent_id, now=now)
    _append_agent_wake_requested_event_for_inbox_item(
        state,
        item=item,
        result=result,
        now=now,
    )
    return item


__all__ = ["append_operator_directive"]
