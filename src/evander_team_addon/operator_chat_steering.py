from __future__ import annotations

from datetime import datetime
from typing import Any

from evander.model_usage.runtime import MODEL_USAGE_AGENT_HARNESS, model_usage_store
from evander.models import utc_now
from evander.operator_steering import (
    OPERATOR_STEERING_NOTE_MAX_LENGTH,
    operator_steering_note_store,
)

from .models import TeamConversationMessage, TeamDispatchStatus, TeamState
from .repository import TeamStateRepository
from .services.domain.common import _ensure_utc, _optional_text


OPERATOR_CHAT_STEERING_METADATA_KEY = "operator_steering"


def queue_operator_chat_steering(
    repository: TeamStateRepository,
    *,
    message_id: str,
) -> tuple[TeamState, TeamConversationMessage, dict[str, Any] | None]:
    """Queue a text-only operator message for the active dispatch's next model call.

    The Team message remains queued until the relay confirms that it injected the
    steering note. Failure here intentionally leaves the ordinary Team Chat wake as
    the durable fallback.
    """

    state = repository.read()
    message = state.conversation_messages.get(str(message_id).strip())
    if message is None:
        raise ValueError(f"conversation message {str(message_id).strip()} not found")
    active = _active_delivery_target(state, message, now=utc_now())
    if active is None:
        return state, message, None
    agent, dispatch = active

    existing = message.metadata.get(OPERATOR_CHAT_STEERING_METADATA_KEY)
    if isinstance(existing, dict) and _optional_text(existing.get("note_id")) is not None:
        state = reconcile_operator_chat_steering_deliveries(repository)
        return state, state.conversation_messages[message.id], dict(existing)

    usage_store = model_usage_store(repository.runtime_dir)
    correlation = usage_store.get_active_correlation_context(
        session_id=agent.session_id,
        dispatch_id=dispatch.id,
        agent_harness=MODEL_USAGE_AGENT_HARNESS,
    )
    if correlation is None:
        correlation = usage_store.get_active_correlation_context(
            session_id=agent.session_id,
            dispatch_id=dispatch.id,
        )
    if correlation is None:
        return state, message, None

    steering_store = operator_steering_note_store(repository.runtime_dir)
    request_key = f"team-chat-attention:{dispatch.id}:{message.id}"
    claim, owns_claim = steering_store.claim_request_once(
        request_key=request_key,
        session_id=agent.session_id,
        dispatch_id=dispatch.id,
    )
    if not owns_claim:
        existing_note_id = _optional_text(claim.outcome_note_id)
        if existing_note_id is None:
            return state, message, None
        note = steering_store.get_note(existing_note_id)
        if note is None:
            return state, message, None
    else:
        try:
            note = steering_store.create_note(
                session_id=agent.session_id,
                dispatch_id=dispatch.id,
                message=_steering_prompt(state, message, dispatch_id=dispatch.id),
                created_by=_optional_text(message.metadata.get("created_by")),
            )
        except Exception as exc:
            steering_store.finish_request_claim(
                request_key=request_key,
                owner_token=claim.owner_token,
                succeeded=False,
                error=str(exc),
            )
            raise

    link = {
        "note_id": note.note_id,
        "dispatch_id": dispatch.id,
        "session_id": agent.session_id,
        "state": note.status,
        "queued_at": note.created_at.isoformat(),
    }

    def _link(state_to_update: TeamState) -> TeamState:
        current = state_to_update.conversation_messages.get(message.id)
        current_active = _active_delivery_target(state_to_update, current, now=utc_now())
        if current is None or current_active is None or current_active[1].id != dispatch.id:
            raise ValueError("operator message is no longer eligible for active-dispatch delivery")
        metadata = dict(current.metadata)
        prior = metadata.get(OPERATOR_CHAT_STEERING_METADATA_KEY)
        if isinstance(prior, dict) and _optional_text(prior.get("note_id")) is not None:
            return state_to_update
        metadata[OPERATOR_CHAT_STEERING_METADATA_KEY] = link
        current.metadata = metadata
        current.updated_at = utc_now()
        return state_to_update

    try:
        linked_state = repository.update(_link)
    except Exception as exc:
        if owns_claim:
            steering_store.cancel_note(note.note_id)
            steering_store.finish_request_claim(
                request_key=request_key,
                owner_token=claim.owner_token,
                succeeded=False,
                error=str(exc),
            )
        return repository.read(), repository.read().conversation_messages[message.id], None
    if owns_claim:
        steering_store.finish_request_claim(
            request_key=request_key,
            owner_token=claim.owner_token,
            succeeded=True,
            outcome_note_id=note.note_id,
        )
    return linked_state, linked_state.conversation_messages[message.id], link


def reconcile_operator_chat_steering_deliveries(
    repository: TeamStateRepository,
) -> TeamState:
    """Project relay delivery receipts into Team Chat message state."""

    state = repository.read()
    steering_store = operator_steering_note_store(repository.runtime_dir)
    receipts: dict[str, Any] = {}
    for message in state.conversation_messages.values():
        if message.sender != "operator" or message.message_type != "text":
            continue
        metadata = message.metadata.get(OPERATOR_CHAT_STEERING_METADATA_KEY)
        if not isinstance(metadata, dict):
            continue
        note_id = _optional_text(metadata.get("note_id"))
        if note_id is None:
            continue
        note = steering_store.get_note(note_id)
        if note is not None:
            receipts[message.id] = note
    if not receipts:
        return state

    needs_change = any(
        (
            isinstance(
                state.conversation_messages[message_id].metadata.get(
                    OPERATOR_CHAT_STEERING_METADATA_KEY
                ),
                dict,
            )
            and state.conversation_messages[message_id].metadata[
                OPERATOR_CHAT_STEERING_METADATA_KEY
            ].get("delivery_channel")
            != "team_mcp_result"
            and state.conversation_messages[message_id].metadata[
                OPERATOR_CHAT_STEERING_METADATA_KEY
            ].get("state")
            != note.status
        )
        or (
            note.status == "delivered"
            and state.conversation_messages[message_id].status == "queued"
            and (
                (active := _active_delivery_target(
                    state,
                    state.conversation_messages[message_id],
                    now=utc_now(),
                ))
                is not None
                and active[1].id == note.dispatch_id
            )
        )
        for message_id, note in receipts.items()
    )
    if not needs_change:
        return state

    def _reconcile(state_to_update: TeamState) -> TeamState:
        now = utc_now()
        for message_id, note in receipts.items():
            message = state_to_update.conversation_messages.get(message_id)
            if message is None:
                continue
            metadata = dict(message.metadata)
            delivery = metadata.get(OPERATOR_CHAT_STEERING_METADATA_KEY)
            if not isinstance(delivery, dict) or delivery.get("note_id") != note.note_id:
                continue
            updated_delivery = dict(delivery)
            message_changed = False
            tool_result_delivered = (
                updated_delivery.get("delivery_channel") == "team_mcp_result"
                and message.status == "delivered_in_dispatch"
            )
            if not tool_result_delivered and updated_delivery.get("state") != note.status:
                updated_delivery["state"] = note.status
                message_changed = True
            if note.error and updated_delivery.get("error") != note.error:
                updated_delivery["error"] = note.error
                message_changed = True
            active = _active_delivery_target(state_to_update, message, now=now)
            if note.status == "delivered" and active is not None and active[1].id == note.dispatch_id:
                delivered_at = note.delivered_at or note.updated_at
                delivered_text = delivered_at.isoformat()
                if message.status == "queued":
                    message.status = "delivered_in_dispatch"
                    message.seen_at = delivered_at
                    message.seen_dispatch_id = note.dispatch_id
                    message.error = None
                    message_changed = True
                if updated_delivery.get("delivered_at") != delivered_text:
                    updated_delivery["delivered_at"] = delivered_text
                    message_changed = True
            if message_changed:
                metadata[OPERATOR_CHAT_STEERING_METADATA_KEY] = updated_delivery
                message.metadata = metadata
                message.updated_at = now
        return state_to_update

    return repository.update(_reconcile)


def _active_delivery_target(
    state: TeamState,
    message: TeamConversationMessage | None,
    *,
    now: datetime,
) -> tuple[Any, Any] | None:
    if (
        message is None
        or message.sender != "operator"
        or message.message_type != "text"
        or message.status not in {"queued", "delivered_in_dispatch"}
        or not message.body
        or message.attachment_ids
    ):
        return None
    conversation = state.conversations.get(message.conversation_id)
    agent = state.agents.get(conversation.agent_id) if conversation is not None else None
    runtime = state.agent_runtimes.get(agent.id) if agent is not None else None
    dispatch = (
        state.dispatches.get(runtime.active_dispatch_id)
        if runtime is not None and runtime.active_dispatch_id is not None
        else None
    )
    if (
        agent is None
        or runtime is None
        or dispatch is None
        or dispatch.id == message.dispatch_id
        or dispatch.agent_id != agent.id
        or dispatch.session_id != agent.session_id
        or dispatch.status != TeamDispatchStatus.LEASED
        or runtime.wake_leased_until is None
        or dispatch.lease_expires_at is None
        or _ensure_utc(runtime.wake_leased_until) <= _ensure_utc(now)
        or _ensure_utc(dispatch.lease_expires_at) <= _ensure_utc(now)
    ):
        return None
    return agent, dispatch


def _steering_prompt(
    state: TeamState,
    message: TeamConversationMessage,
    *,
    dispatch_id: str,
) -> str:
    conversation = state.conversations[message.conversation_id]
    operator_label = conversation.operator_display_name or conversation.operator_user_id
    body = message.body or ""
    prefix = (
        "A human operator sent a Team Chat message during your current dispatch. "
        "Read it now and account for it before finishing this dispatch.\n"
        f"Operator: {operator_label} ({conversation.operator_user_id})\n"
        f"Conversation ID: {conversation.id}\n"
        f"Source message ID: {message.id}\n"
        f"Current dispatch ID: {dispatch_id}\n"
        "Message:\n"
    )
    suffix = (
        "\n\nIf you reply, call team_send_operator_chat_message with "
        f"recipient_operator_user_id={conversation.operator_user_id!r} and "
        f"source_message_ids={[message.id]!r}. If you acted on the message and no reply "
        "is needed, call team_acknowledge_operator_chat_messages with "
        f"message_ids={[message.id]!r}, disposition='acted_no_reply', and a short summary."
    )
    available = max(0, OPERATOR_STEERING_NOTE_MAX_LENGTH - len(prefix) - len(suffix))
    if len(body) > available:
        body = f"{body[: max(0, available - 3)].rstrip()}..."
    return f"{prefix}{body}{suffix}"


__all__ = [
    "OPERATOR_CHAT_STEERING_METADATA_KEY",
    "queue_operator_chat_steering",
    "reconcile_operator_chat_steering_deliveries",
]
