from __future__ import annotations

from evander.admin.user_notifications import AdminUserNotificationStore
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from ...models import (
    AgentRuntime,
    PersistentAgent,
    TeamConversation,
    TeamConversationMessage,
    TeamDispatchStatus,
    TeamDispatchActivity,
    TeamState,
)

from .common import (
    _ensure_utc,
    _generated_id,
    _normalize_operator_user_id,
    _operator_display_name_for_user,
    _optional_text,
    _require_present,
    _required_text,
    utc_now,
)


CHAT_MESSAGE_QUEUED_EVENT_TYPE = "team.chat.message_queued"
EXTERNAL_CHAT_REPLY_REQUESTED_EVENT_TYPE = "team.chat.external_reply_requested"


def _operator_chat_participant_payloads(
    state: TeamState,
    *,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    participants: dict[str, dict[str, Any]] = {}
    for conversation in state.conversations.values():
        if agent_id is not None and conversation.agent_id != agent_id:
            continue
        participant = participants.setdefault(
            conversation.operator_user_id,
            {
                "operator_user_id": conversation.operator_user_id,
                "operator_display_name": conversation.operator_display_name,
                "conversation_ids": [],
            },
        )
        participant["conversation_ids"].append(conversation.id)
        if not participant.get("operator_display_name") and conversation.operator_display_name:
            participant["operator_display_name"] = conversation.operator_display_name
    return sorted(
        participants.values(),
        key=lambda item: str(
            item.get("operator_display_name") or item.get("operator_user_id") or ""
        ),
    )


def _chat_preview(value: object, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}..."


def _publish_admin_chat_notification(
    *,
    runtime_dir: Path,
    state: TeamState,
    message_id: str,
) -> None:
    message = state.conversation_messages.get(message_id)
    if message is None or message.sender != "agent":
        return
    conversation = state.conversations.get(message.conversation_id)
    if conversation is None:
        return
    agent = state.agents.get(conversation.agent_id)
    agent_label = (
        _optional_text(agent.display_name if agent is not None else None) or conversation.agent_id
    )
    try:
        AdminUserNotificationStore(runtime_dir).push(
            user_id=conversation.operator_user_id,
            title="New Team chat message",
            body=f"{agent_label}: {_chat_preview(message.body, limit=140)}",
            source="team-chat",
            source_id=message.id,
            target_url="#addon:team-addon",
            metadata={
                "conversation_id": conversation.id,
                "message_id": message.id,
                "agent_id": conversation.agent_id,
            },
        )
    except Exception:
        return


def _publish_admin_chat_notifications(
    *,
    runtime_dir: Path,
    state: TeamState,
    message_ids: Sequence[str],
) -> None:
    for message_id in message_ids:
        _publish_admin_chat_notification(
            runtime_dir=runtime_dir,
            state=state,
            message_id=message_id,
        )


def _touch_conversation(
    conversation: TeamConversation,
    *,
    now: datetime,
    preview: str,
) -> None:
    conversation.last_message_preview = _chat_preview(preview)
    conversation.last_interaction_at = now
    conversation.updated_at = now


def _append_pending_chat_message_queued_event_for_agent(
    state: TeamState,
    *,
    agent: PersistentAgent,
    completed_dispatch_id: str,
    result: dict[str, Any],
) -> None:
    pending_message: TeamConversationMessage | None = None
    pending_conversation: TeamConversation | None = None
    for message in sorted(
        state.conversation_messages.values(),
        key=lambda item: (item.created_at, item.id),
    ):
        if (
            message.sender != "operator"
            or message.message_type != "text"
            or message.status != "queued"
        ):
            continue
        conversation = state.conversations.get(message.conversation_id)
        if conversation is None or conversation.agent_id != agent.id:
            continue
        pending_message = message
        pending_conversation = conversation
        break
    if pending_message is None or pending_conversation is None:
        return
    dispatch_id = pending_message.dispatch_id or f"team-chat:{pending_message.id}"
    pending_message.dispatch_id = dispatch_id
    result["events"].append(
        {
            "event_type": CHAT_MESSAGE_QUEUED_EVENT_TYPE,
            "correlation_key": dispatch_id,
            "idempotency_key": (
                "team-addon:"
                f"{CHAT_MESSAGE_QUEUED_EVENT_TYPE}:agent-ready:"
                f"{completed_dispatch_id}:{pending_message.id}"
            ),
            "payload": {
                "team_id": agent.team_id,
                "agent_id": agent.id,
                "session_id": agent.session_id,
                "conversation_id": pending_conversation.id,
                "message_id": pending_message.id,
                "dispatch_id": dispatch_id,
            },
        }
    )


def _apply_chat_reply(
    state: TeamState,
    *,
    actor: PersistentAgent,
    reply: Mapping[str, Any],
    result: dict[str, Any],
) -> str:
    conversation_id = _required_text(reply.get("conversation_id"), "conversation_id")
    source_message_id = _required_text(reply.get("message_id"), "message_id")
    body = _required_text(reply.get("body"), "body")
    conversation = _require_present(state.conversations, conversation_id, "conversation")
    if conversation.agent_id != actor.id:
        raise ValueError(f"agent {actor.id} cannot reply to conversation {conversation_id}")
    source_message = _require_present(
        state.conversation_messages,
        source_message_id,
        "conversation message",
    )
    if source_message.conversation_id != conversation.id:
        raise ValueError(f"message {source_message_id} is not in conversation {conversation_id}")
    if source_message.sender != "operator":
        raise ValueError(f"message {source_message_id} is not an operator message")
    now = utc_now()
    source_message.status = "replied"
    source_message.error = None
    source_message.updated_at = now
    reply_id = f"chatmsg-{uuid4().hex}"
    metadata: dict[str, Any] = {}
    summary = _optional_text(reply.get("summary"))
    if summary is not None:
        metadata["summary"] = summary
    state.conversation_messages[reply_id] = TeamConversationMessage(
        id=reply_id,
        conversation_id=conversation.id,
        sender="agent",
        message_type="text",
        body=body,
        status="sent",
        source_message_id=source_message.id,
        metadata=metadata,
        created_at=now,
        updated_at=now,
    )
    _touch_conversation(conversation, now=now, preview=_chat_preview(body))
    result["replied_chat_message_ids"].append(source_message.id)
    return reply_id


def _apply_operator_chat_message(
    state: TeamState,
    *,
    actor: PersistentAgent,
    message: Mapping[str, Any],
) -> str:
    recipient_operator_user_id = _normalize_operator_user_id(
        message.get("recipient_operator_user_id")
    )
    known_display_name = _operator_display_name_for_user(
        state,
        recipient_operator_user_id,
    )
    known_operator_ids = {
        conversation.operator_user_id for conversation in state.conversations.values()
    }
    if recipient_operator_user_id not in known_operator_ids:
        raise ValueError(f"unknown operator {recipient_operator_user_id}")
    conversation = next(
        (
            item
            for item in state.conversations.values()
            if item.agent_id == actor.id and item.operator_user_id == recipient_operator_user_id
        ),
        None,
    )
    now = utc_now()
    if conversation is None:
        conversation_id = _generated_id(
            prefix="conversation",
            labels=[actor.id, recipient_operator_user_id],
            existing_ids=state.conversations,
        )
        conversation = TeamConversation(
            id=conversation_id,
            agent_id=actor.id,
            operator_user_id=recipient_operator_user_id,
            operator_display_name=known_display_name,
            created_by=f"agent:{actor.id}",
            last_message_preview="",
            created_at=now,
            updated_at=now,
            last_interaction_at=now,
        )
        state.conversations[conversation.id] = conversation
    body = _required_text(message.get("body"), "body")
    summary = _optional_text(message.get("summary"))
    message_id = f"chatmsg-{uuid4().hex}"
    state.conversation_messages[message_id] = TeamConversationMessage(
        id=message_id,
        conversation_id=conversation.id,
        sender="agent",
        message_type="text",
        body=body,
        status="sent",
        metadata={
            key: value
            for key, value in {
                "sender_kind": "team_agent",
                "recipient_operator_user_id": recipient_operator_user_id,
                "recipient_operator_display_name": known_display_name,
                "summary": summary,
            }.items()
            if value
        },
        created_at=now,
        updated_at=now,
    )
    _touch_conversation(conversation, now=now, preview=_chat_preview(body))
    return message_id


def _mark_chat_dispatch_message_failed(
    state: TeamState,
    *,
    dispatch_id: str | None,
    error: str,
) -> str | None:
    normalized_dispatch_id = _optional_text(dispatch_id)
    if normalized_dispatch_id is None or not normalized_dispatch_id.startswith("team-chat:"):
        return None
    source_message = next(
        (
            message
            for message in state.conversation_messages.values()
            if message.dispatch_id == normalized_dispatch_id
            and message.sender == "operator"
            and message.status != "replied"
        ),
        None,
    )
    if source_message is None:
        return None
    conversation = state.conversations.get(source_message.conversation_id)
    now = utc_now()
    source_message.status = "failed"
    source_message.error = error
    source_message.updated_at = now
    if conversation is not None:
        _touch_conversation(
            conversation,
            now=now,
            preview=f"Reply failed: {_chat_preview(error)}",
        )
    return source_message.id


def _mark_chat_dispatch_message_replied_from_activity(
    state: TeamState,
    *,
    dispatch_id: str,
) -> str | None:
    source_message = next(
        (
            message
            for message in state.conversation_messages.values()
            if message.dispatch_id == dispatch_id
            and message.sender == "operator"
            and message.status != "failed"
        ),
        None,
    )
    if source_message is None:
        return None
    if source_message.status != "replied":
        now = utc_now()
        source_message.status = "replied"
        source_message.error = None
        source_message.updated_at = now
        conversation = state.conversations.get(source_message.conversation_id)
        if conversation is not None:
            _touch_conversation(
                conversation,
                now=now,
                preview=f"Replied: {_chat_preview(source_message.body)}",
            )
    return source_message.id


def _clear_completed_chat_dispatch_lease(
    state: TeamState,
    runtime: AgentRuntime,
    *,
    now: datetime,
) -> bool:
    dispatch_id = _optional_text(runtime.active_dispatch_id)
    if dispatch_id is None or not dispatch_id.startswith("team-chat:"):
        return False
    if runtime.wake_leased_until is not None and _ensure_utc(
        runtime.wake_leased_until
    ) > _ensure_utc(now):
        return False
    message = next(
        (
            item
            for item in state.conversation_messages.values()
            if item.dispatch_id == dispatch_id
            and item.sender == "operator"
            and item.status in {"replied", "failed"}
        ),
        None,
    )
    if message is None:
        return False
    dispatch = state.dispatches.get(dispatch_id)
    if (
        dispatch is not None
        and dispatch.status
        in {TeamDispatchStatus.PREPARING, TeamDispatchStatus.LEASED}
    ):
        dispatch.status = (
            TeamDispatchStatus.COMPLETED
            if message.status == "replied"
            else TeamDispatchStatus.FAILED
        )
        dispatch.completed_at = now
        dispatch.error = message.error if message.status == "failed" else None
    runtime.active_dispatch_id = None
    runtime.wake_leased_until = None
    runtime.wake_reason = None
    runtime.updated_at = now
    return True


def _chat_dispatch_finalization_error(
    failed_activities: Sequence[TeamDispatchActivity],
) -> str:
    reply_failure = next(
        (activity for activity in failed_activities if activity.tool_name == "team_reply_to_chat"),
        None,
    )
    if reply_failure is not None:
        return (
            reply_failure.error_message or reply_failure.error_code or "team_reply_to_chat failed"
        )
    return "Team chat dispatch completed without a successful team_reply_to_chat call"


__all__ = [
    "CHAT_MESSAGE_QUEUED_EVENT_TYPE",
    "EXTERNAL_CHAT_REPLY_REQUESTED_EVENT_TYPE",
    "_operator_chat_participant_payloads",
    "_chat_preview",
    "_publish_admin_chat_notification",
    "_publish_admin_chat_notifications",
    "_touch_conversation",
    "_append_pending_chat_message_queued_event_for_agent",
    "_apply_chat_reply",
    "_apply_operator_chat_message",
    "_mark_chat_dispatch_message_failed",
    "_mark_chat_dispatch_message_replied_from_activity",
    "_clear_completed_chat_dispatch_lease",
    "_chat_dispatch_finalization_error",
]
