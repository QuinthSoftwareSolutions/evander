from __future__ import annotations


from collections.abc import Sequence
from typing import Any
from ...models import (
    TeamInboxPriority,
    _unique_text_list,
)
from ..domain.common import (
    _normalize_operator_user_id,
    _optional_text,
    _required_text,
)


def _team_record_note_normalized_payload(
    *,
    body: str,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "body": _optional_text(body) or "",
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_communication_normalized_payload(
    *,
    recipient_agent_id: str | None,
    task_id: str | None,
    question: str | None,
    message: str | None,
    summary: str | None,
    response_requested: bool,
    priority: TeamInboxPriority,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "recipient_agent_id": _optional_text(recipient_agent_id),
        "task_id": _optional_text(task_id),
        "question": _optional_text(question),
        "message": _optional_text(message),
        "summary": _optional_text(summary),
        "response_requested": bool(response_requested),
        "priority": priority.value,
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_chat_reply_normalized_payload(
    *,
    conversation_id: str,
    message_id: str | None,
    message_ids: Sequence[str],
    body: str,
    attachment_ids: Sequence[str],
    summary: str | None,
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    payload = {
        "conversation_id": _required_text(conversation_id, "conversation_id"),
        "body": _optional_text(body) or "",
        "attachment_ids": sorted(_unique_text_list(attachment_ids, "attachment_ids")),
        "summary": _optional_text(summary),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }
    normalized_message_id = _optional_text(message_id)
    normalized_message_ids = sorted(_unique_text_list(message_ids, "message_ids"))
    if normalized_message_id is not None:
        payload["message_id"] = normalized_message_id
    if normalized_message_ids and (
        normalized_message_id is None or normalized_message_ids != [normalized_message_id]
    ):
        payload["message_ids"] = normalized_message_ids
    return payload


def _team_operator_chat_normalized_payload(
    *,
    recipient_operator_user_id: str,
    body: str,
    attachment_ids: Sequence[str],
    source_message_ids: Sequence[str],
    summary: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "recipient_operator_user_id": _normalize_operator_user_id(recipient_operator_user_id),
        "body": _optional_text(body) or "",
        "attachment_ids": sorted(_unique_text_list(attachment_ids, "attachment_ids")),
        "source_message_ids": sorted(
            _unique_text_list(source_message_ids, "source_message_ids")
        ),
        "summary": _optional_text(summary),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_operator_escalation_normalized_payload(
    *,
    title: str,
    message: str,
    urgency: str,
    project_id: str | None,
    task_id: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "title": _optional_text(title) or "",
        "message": _optional_text(message) or "",
        "urgency": _optional_text(urgency) or "normal",
        "project_id": _optional_text(project_id),
        "task_id": _optional_text(task_id),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


__all__ = [
    "_team_record_note_normalized_payload",
    "_team_communication_normalized_payload",
    "_team_chat_reply_normalized_payload",
    "_team_operator_chat_normalized_payload",
    "_team_operator_escalation_normalized_payload",
]
