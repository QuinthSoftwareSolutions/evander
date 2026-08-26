from __future__ import annotations

from evander.models import utc_now
from collections.abc import Sequence
from typing import Any
from uuid import uuid4
from .communication_helpers import (
    _team_chat_reply_normalized_payload,
    _team_operator_chat_normalized_payload,
)
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import (
    _require_command_agent,
    _require_command_chat_message,
    _require_command_conversation,
)
from ...models import (
    ExternalChatDeliveryRecord,
    TeamChatAttachment,
    TeamConversationMessage,
    TeamState,
    _unique_text_list,
)
from ...chat_attachments import PreparedChatAttachment
from ..domain.chat import (
    EXTERNAL_CHAT_REPLY_REQUESTED_EVENT_TYPE,
    _chat_preview,
    _publish_admin_chat_notifications,
    _touch_conversation,
)
from ..domain.common import (
    _normalize_operator_user_id,
    _optional_text,
    _required_text,
)

from .executor import TeamCommandExecutor


class TeamChatCommandService(TeamCommandExecutor):
    def reply_to_chat_command(
        self,
        *,
        session_id: str,
        conversation_id: str,
        body: str,
        request_id: str,
        message_id: str | None = None,
        message_ids: Sequence[str] = (),
        attachments: Sequence[PreparedChatAttachment] = (),
        source_attachment_ids: Sequence[str] = (),
        summary: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_message_id = _optional_text(message_id)
        requested_message_ids = _unique_text_list(message_ids, "message_ids")
        if normalized_message_id is not None:
            requested_message_ids = _unique_text_list(
                [*requested_message_ids, normalized_message_id],
                "message_ids",
            )
        if not requested_message_ids:
            raise ValueError("message_id or message_ids is required")
        normalized_body = _optional_text(body) or ""
        normalized_summary = _optional_text(summary)
        prepared_attachments = list(attachments)
        normalized_source_attachment_ids = _unique_text_list(
            source_attachment_ids,
            "attachment_ids",
        )
        if len(prepared_attachments) != len(normalized_source_attachment_ids):
            raise ValueError("prepared attachments must match attachment_ids")
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_reply_to_chat",
            request_id=normalized_request_id,
            wake_session_class="team_chat",
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="chat_reply",
        )
        normalized_payload = _team_chat_reply_normalized_payload(
            conversation_id=normalized_conversation_id,
            message_id=normalized_message_id,
            message_ids=requested_message_ids,
            body=normalized_body,
            attachment_ids=normalized_source_attachment_ids,
            summary=normalized_summary,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_reply_to_chat")
            if resolved.runtime is None or resolved.runtime.wake_reason != "team_chat":
                raise TeamCommandValidationError(
                    code="not_chat_dispatch",
                    message="team_reply_to_chat requires an active Team Chat dispatch",
                    correction_context={
                        "agent_id": agent.id,
                        "wake_reason": (
                            resolved.runtime.wake_reason if resolved.runtime is not None else None
                        ),
                    },
                )
            body_text = _optional_text(normalized_body)
            if body_text is None and not prepared_attachments:
                raise TeamCommandValidationError(
                    code="empty_body",
                    message="body or at least one attachment is required",
                )
            conversation = _require_command_conversation(
                state,
                conversation_id=normalized_conversation_id,
                agent=agent,
            )
            dispatch = state.dispatches.get(resolved.dispatch_id or "")
            dispatch_message_ids = list(dispatch.chat_message_ids) if dispatch else []
            if not dispatch_message_ids and dispatch is not None and dispatch.cause_id:
                dispatch_message_ids = [dispatch.cause_id]
            if dispatch_message_ids:
                requested_set = set(requested_message_ids)
                dispatch_set = set(dispatch_message_ids)
                if not requested_set.issubset(dispatch_set):
                    raise TeamCommandValidationError(
                        code="chat_batch_message_ids_mismatch",
                        message="message_ids must belong to the active Team Chat dispatch batch",
                        correction_context={
                            "active_dispatch_id": resolved.dispatch_id,
                            "message_ids": dispatch_message_ids,
                        },
                    )
                if len(requested_message_ids) > 1 and requested_set != dispatch_set:
                    raise TeamCommandValidationError(
                        code="chat_batch_message_ids_mismatch",
                        message="message_ids must cover the full active Team Chat dispatch batch",
                        correction_context={
                            "active_dispatch_id": resolved.dispatch_id,
                            "message_ids": dispatch_message_ids,
                        },
                    )
                source_message_ids = dispatch_message_ids
            else:
                source_message_ids = requested_message_ids
            source_messages = [
                _require_command_chat_message(
                    state,
                    message_id=source_message_id,
                    conversation_id=conversation.id,
                )
                for source_message_id in source_message_ids
            ]
            for source_message in source_messages:
                if source_message.sender != "operator":
                    raise TeamCommandValidationError(
                        code="chat_message_not_operator",
                        message=f"message {source_message.id} is not an operator message",
                        correction_context={"message_id": source_message.id},
                    )
                if source_message.status == "replied":
                    raise TeamCommandValidationError(
                        code="chat_message_already_replied",
                        message=f"message {source_message.id} has already been replied to",
                        correction_context={"message_id": source_message.id},
                    )
            _require_unclaimed_chat_attachments(state, prepared_attachments)
            now = utc_now()
            for source_message in source_messages:
                source_message.status = "replied"
                source_message.error = None
                source_message.updated_at = now
            source_message = source_messages[-1]
            reply_id = f"chatmsg-{uuid4().hex}"
            metadata: dict[str, Any] = {"sender_kind": "team_agent"}
            if normalized_summary is not None:
                metadata["summary"] = normalized_summary
            state.conversation_messages[reply_id] = TeamConversationMessage(
                id=reply_id,
                conversation_id=conversation.id,
                sender="agent",
                message_type="text",
                body=body_text,
                attachment_ids=[attachment.id for attachment in prepared_attachments],
                status="sent",
                source_message_id=source_message.id,
                source_message_ids=source_message_ids,
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )
            for attachment in prepared_attachments:
                state.chat_attachments[attachment.id] = TeamChatAttachment(
                    **attachment.payload(),
                    message_id=reply_id,
                    conversation_id=conversation.id,
                    sender="agent",
                    created_by_session_id=normalized_session_id,
                    created_at=now,
                )
            _touch_conversation(
                conversation,
                now=now,
                preview=_chat_message_preview(body_text, len(prepared_attachments)),
            )
            external_reply_body = _external_chat_reply_body(
                body_text,
                prepared_attachments,
            )
            events: list[dict[str, Any]] = []
            external_origin = source_message.metadata.get("external_origin")
            if isinstance(external_origin, dict):
                provider = _optional_text(external_origin.get("provider"))
                communication_target_id = _optional_text(
                    external_origin.get("communication_target_id")
                )
                if provider is not None and communication_target_id is not None:
                    delivery_id = f"external-chat-delivery-{uuid4().hex}"
                    state.external_chat_deliveries[delivery_id] = ExternalChatDeliveryRecord(
                        id=delivery_id,
                        agent_reply_message_id=reply_id,
                        operator_message_id=source_message.id,
                        provider=provider,
                        communication_target_id=communication_target_id,
                        source_thread_id=_optional_text(external_origin.get("source_thread_id")),
                        created_at=now,
                        updated_at=now,
                    )
                    metadata["external_delivery"] = {
                        "delivery_id": delivery_id,
                        "provider": provider,
                        "communication_target_id": communication_target_id,
                        "state": "pending",
                        "attempt_count": 0,
                    }
                    state.conversation_messages[reply_id].metadata = metadata
                    events.append(
                        {
                            "event_type": EXTERNAL_CHAT_REPLY_REQUESTED_EVENT_TYPE,
                            "correlation_key": delivery_id,
                            "idempotency_key": (
                                f"team-chat-external-reply:{reply_id}:{communication_target_id}"
                            ),
                            "payload": {
                                "delivery_id": delivery_id,
                                "conversation_id": conversation.id,
                                "operator_message_id": source_message.id,
                                "operator_message_ids": source_message_ids,
                                "agent_reply_message_id": reply_id,
                                "agent_id": agent.id,
                                "sender_session_id": normalized_session_id,
                                "provider": provider,
                                "communication_target_id": communication_target_id,
                                "source_thread_id": _optional_text(
                                    external_origin.get("source_thread_id")
                                ),
                                "body": external_reply_body,
                                "team_chat_body": body_text,
                                "attachment_ids": [
                                    attachment.id for attachment in prepared_attachments
                                ],
                                "attachments": [
                                    attachment.payload() for attachment in prepared_attachments
                                ],
                            },
                        }
                    )
            return TeamCommandApplyResult(
                response_payload={
                    "chat_message_id": reply_id,
                    "conversation_id": conversation.id,
                    "replied_message_id": source_message.id,
                    "replied_message_ids": source_message_ids,
                    "created_chat_reply_message_ids": [reply_id],
                    "replied_chat_message_ids": source_message_ids,
                    "progress_made": True,
                    "events": events,
                },
                created_domain_ids=[
                    reply_id,
                    *[
                        str(event["correlation_key"])
                        for event in events
                        if event.get("correlation_key") is not None
                    ],
                ],
            )

        state, response = self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=[],
            apply_command=_apply,
            require_active_dispatch=True,
        )
        _publish_admin_chat_notifications(
            runtime_dir=self._repository.runtime_dir,
            state=state,
            message_ids=[
                str(message_id) for message_id in response.get("created_chat_reply_message_ids", [])
            ],
        )
        return state, response

    def send_operator_chat_message_command(
        self,
        *,
        session_id: str,
        recipient_operator_user_id: str,
        body: str,
        request_id: str,
        attachments: Sequence[PreparedChatAttachment] = (),
        source_attachment_ids: Sequence[str] = (),
        source_message_ids: Sequence[str] = (),
        summary: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_operator_id = _normalize_operator_user_id(recipient_operator_user_id)
        normalized_body = _optional_text(body) or ""
        normalized_summary = _optional_text(summary)
        prepared_attachments = list(attachments)
        normalized_source_attachment_ids = _unique_text_list(
            source_attachment_ids,
            "attachment_ids",
        )
        normalized_source_message_ids = _unique_text_list(
            source_message_ids,
            "source_message_ids",
        )
        if len(prepared_attachments) != len(normalized_source_attachment_ids):
            raise ValueError("prepared attachments must match attachment_ids")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_send_operator_chat_message",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="operator_chat",
        )
        normalized_payload = _team_operator_chat_normalized_payload(
            recipient_operator_user_id=normalized_operator_id,
            body=normalized_body,
            attachment_ids=normalized_source_attachment_ids,
            source_message_ids=normalized_source_message_ids,
            summary=normalized_summary,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_send_operator_chat_message")
            body_text = _optional_text(normalized_body)
            if body_text is None and not prepared_attachments:
                raise TeamCommandValidationError(
                    code="empty_body",
                    message="body or at least one attachment is required",
                )
            conversation = next(
                (
                    item
                    for item in state.conversations.values()
                    if item.agent_id == agent.id and item.operator_user_id == normalized_operator_id
                ),
                None,
            )
            if conversation is None:
                operator_user_ids = sorted(
                    {
                        item.operator_user_id
                        for item in state.conversations.values()
                        if item.agent_id == agent.id
                    }
                )
                raise TeamCommandValidationError(
                    code="operator_chat_not_member",
                    message=(
                        f"agent {agent.id} is not a member of an operator chat with "
                        f"{normalized_operator_id}"
                    ),
                    correction_context={
                        "agent_id": agent.id,
                        "recipient_operator_user_id": normalized_operator_id,
                        "operator_user_ids": operator_user_ids,
                    },
                )
            source_messages = _require_active_delivery_source_messages(
                state,
                conversation_id=conversation.id,
                message_ids=normalized_source_message_ids,
                active_dispatch_id=resolved.dispatch_id,
            )
            _require_unclaimed_chat_attachments(state, prepared_attachments)
            now = utc_now()
            known_display_name = conversation.operator_display_name
            message_id = f"chatmsg-{uuid4().hex}"
            metadata: dict[str, Any] = {
                "sender_kind": "team_agent",
                "recipient_operator_user_id": normalized_operator_id,
            }
            if known_display_name is not None:
                metadata["recipient_operator_display_name"] = known_display_name
            if normalized_summary is not None:
                metadata["summary"] = normalized_summary
            if normalized_source_message_ids:
                metadata["source_kind"] = "operator_message_during_dispatch"
            state.conversation_messages[message_id] = TeamConversationMessage(
                id=message_id,
                conversation_id=conversation.id,
                sender="agent",
                message_type="text",
                body=body_text,
                attachment_ids=[attachment.id for attachment in prepared_attachments],
                status="sent",
                source_message_id=(
                    normalized_source_message_ids[-1]
                    if normalized_source_message_ids
                    else None
                ),
                source_message_ids=normalized_source_message_ids,
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )
            for attachment in prepared_attachments:
                state.chat_attachments[attachment.id] = TeamChatAttachment(
                    **attachment.payload(),
                    message_id=message_id,
                    conversation_id=conversation.id,
                    sender="agent",
                    created_by_session_id=normalized_session_id,
                    created_at=now,
                )
            _touch_conversation(
                conversation,
                now=now,
                preview=_chat_message_preview(body_text, len(prepared_attachments)),
            )
            for source_message in source_messages:
                source_message.status = "replied"
                source_message.error = None
                source_message.updated_at = now
            events: list[dict[str, Any]] = []
            if source_messages:
                external_origin = source_messages[-1].metadata.get("external_origin")
                if isinstance(external_origin, dict):
                    provider = _optional_text(external_origin.get("provider"))
                    communication_target_id = _optional_text(
                        external_origin.get("communication_target_id")
                    )
                    if provider is not None and communication_target_id is not None:
                        delivery_id = f"external-chat-delivery-{uuid4().hex}"
                        state.external_chat_deliveries[delivery_id] = ExternalChatDeliveryRecord(
                            id=delivery_id,
                            agent_reply_message_id=message_id,
                            operator_message_id=source_messages[-1].id,
                            provider=provider,
                            communication_target_id=communication_target_id,
                            source_thread_id=_optional_text(
                                external_origin.get("source_thread_id")
                            ),
                            created_at=now,
                            updated_at=now,
                        )
                        metadata["external_delivery"] = {
                            "delivery_id": delivery_id,
                            "provider": provider,
                            "communication_target_id": communication_target_id,
                            "state": "pending",
                            "attempt_count": 0,
                        }
                        state.conversation_messages[message_id].metadata = metadata
                        events.append(
                            {
                                "event_type": EXTERNAL_CHAT_REPLY_REQUESTED_EVENT_TYPE,
                                "correlation_key": delivery_id,
                                "idempotency_key": (
                                    f"team-chat-external-reply:{message_id}:"
                                    f"{communication_target_id}"
                                ),
                                "payload": {
                                    "delivery_id": delivery_id,
                                    "conversation_id": conversation.id,
                                    "operator_message_id": source_messages[-1].id,
                                    "operator_message_ids": normalized_source_message_ids,
                                    "agent_reply_message_id": message_id,
                                    "agent_id": agent.id,
                                    "sender_session_id": normalized_session_id,
                                    "provider": provider,
                                    "communication_target_id": communication_target_id,
                                    "source_thread_id": _optional_text(
                                        external_origin.get("source_thread_id")
                                    ),
                                    "body": _external_chat_reply_body(
                                        body_text,
                                        prepared_attachments,
                                    ),
                                    "team_chat_body": body_text,
                                    "attachment_ids": [
                                        attachment.id for attachment in prepared_attachments
                                    ],
                                    "attachments": [
                                        attachment.payload() for attachment in prepared_attachments
                                    ],
                                },
                            }
                        )
            return TeamCommandApplyResult(
                response_payload={
                    "chat_message_id": message_id,
                    "conversation_id": conversation.id,
                    "conversation_created": False,
                    "created_operator_chat_message_ids": [message_id],
                    "replied_chat_message_ids": normalized_source_message_ids,
                    "progress_made": True,
                    "events": events,
                },
                created_domain_ids=[
                    message_id,
                    *[
                        str(event["correlation_key"])
                        for event in events
                        if event.get("correlation_key") is not None
                    ],
                ],
            )

        state, response = self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )
        _publish_admin_chat_notifications(
            runtime_dir=self._repository.runtime_dir,
            state=state,
            message_ids=[
                str(message_id)
                for message_id in response.get("created_operator_chat_message_ids", [])
            ],
        )
        return state, response

    def acknowledge_operator_chat_messages_command(
        self,
        *,
        session_id: str,
        message_ids: Sequence[str],
        disposition: str,
        summary: str,
        request_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_message_ids = _unique_text_list(message_ids, "message_ids")
        if not normalized_message_ids:
            raise ValueError("message_ids is required")
        normalized_disposition = _required_text(disposition, "disposition").lower()
        if normalized_disposition != "acted_no_reply":
            raise ValueError("disposition must be acted_no_reply")
        normalized_summary = _required_text(summary, "summary")
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_acknowledge_operator_chat_messages",
            request_id=_required_text(request_id, "request_id"),
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="operator_chat_acknowledgement",
        )
        normalized_payload = {
            "message_ids": sorted(normalized_message_ids),
            "source_message_ids": sorted(normalized_message_ids),
            "disposition": normalized_disposition,
            "summary": normalized_summary,
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(
                resolved,
                "team_acknowledge_operator_chat_messages",
            )
            source_messages: list[TeamConversationMessage] = []
            conversation_ids: set[str] = set()
            for message_id in normalized_message_ids:
                message = state.conversation_messages.get(message_id)
                if message is None:
                    raise TeamCommandValidationError(
                        code="chat_message_not_found",
                        message=f"conversation message {message_id} was not found",
                    )
                conversation = state.conversations.get(message.conversation_id)
                if conversation is None or conversation.agent_id != agent.id:
                    raise TeamCommandValidationError(
                        code="operator_chat_not_member",
                        message=f"message {message_id} is outside the caller's operator chats",
                    )
                source_messages.extend(
                    _require_active_delivery_source_messages(
                        state,
                        conversation_id=conversation.id,
                        message_ids=[message_id],
                        active_dispatch_id=resolved.dispatch_id,
                    )
                )
                conversation_ids.add(conversation.id)
            now = utc_now()
            for message in source_messages:
                metadata = dict(message.metadata)
                metadata["operator_attention_acknowledgement"] = {
                    "dispatch_id": resolved.dispatch_id,
                    "disposition": normalized_disposition,
                    "summary": normalized_summary,
                    "acknowledged_at": now.isoformat(),
                }
                message.metadata = metadata
                message.status = "acknowledged"
                message.error = None
                message.updated_at = now
            return TeamCommandApplyResult(
                response_payload={
                    "acknowledged_message_ids": normalized_message_ids,
                    "conversation_ids": sorted(conversation_ids),
                    "disposition": normalized_disposition,
                    "summary": normalized_summary,
                    "progress_made": True,
                },
                created_domain_ids=normalized_message_ids,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=[],
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamChatCommandService"]


def _chat_message_preview(body: str | None, attachment_count: int) -> str:
    if body:
        return _chat_preview(body)
    return f"Sent {attachment_count} attachment{'s' if attachment_count != 1 else ''}"


def _external_chat_reply_body(
    body: str | None,
    attachments: Sequence[PreparedChatAttachment],
) -> str:
    lines = [body] if body else []
    if attachments:
        if lines:
            lines.append("")
        heading = (
            "Team Chat attachment reference:"
            if len(attachments) == 1
            else "Team Chat attachment references:"
        )
        lines.append(heading)
        for attachment in attachments:
            details = [
                value
                for value in (
                    attachment.mime_type,
                    f"{attachment.byte_count} bytes",
                )
                if value
            ]
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(f"- {attachment.filename}{suffix}")
    return "\n".join(lines)


def _require_unclaimed_chat_attachments(
    state: TeamState,
    attachments: Sequence[PreparedChatAttachment],
) -> None:
    claimed = [
        state.chat_attachments[attachment.id]
        for attachment in attachments
        if attachment.id in state.chat_attachments
    ]
    if not claimed:
        return
    attachment_ids = sorted({attachment.id for attachment in claimed})
    message_ids = sorted({attachment.message_id for attachment in claimed})
    raise TeamCommandValidationError(
        code="chat_attachment_already_claimed",
        message=(
            "Team Chat attachment is already assigned to another message: "
            + ", ".join(attachment_ids)
        ),
        correction_context={
            "attachment_ids": attachment_ids,
            "message_ids": message_ids,
        },
    )


def _require_active_delivery_source_messages(
    state: TeamState,
    *,
    conversation_id: str,
    message_ids: Sequence[str],
    active_dispatch_id: str | None,
) -> list[TeamConversationMessage]:
    messages: list[TeamConversationMessage] = []
    for message_id in message_ids:
        message = _require_command_chat_message(
            state,
            message_id=message_id,
            conversation_id=conversation_id,
        )
        if message.sender != "operator":
            raise TeamCommandValidationError(
                code="chat_message_not_operator",
                message=f"message {message.id} is not an operator message",
            )
        if message.status in {"replied", "acknowledged"}:
            raise TeamCommandValidationError(
                code="chat_message_already_handled",
                message=f"message {message.id} has already been handled",
            )
        delivery = message.metadata.get("operator_steering")
        delivery_dispatch_id = (
            _optional_text(delivery.get("dispatch_id")) if isinstance(delivery, dict) else None
        )
        if (
            message.status not in {"queued", "delivered_in_dispatch"}
            or delivery_dispatch_id is None
            or delivery_dispatch_id != active_dispatch_id
        ):
            raise TeamCommandValidationError(
                code="chat_message_not_delivered_in_dispatch",
                message=(
                    f"message {message.id} was not delivered into the current active dispatch"
                ),
                correction_context={
                    "message_id": message.id,
                    "active_dispatch_id": active_dispatch_id,
                    "delivery_dispatch_id": delivery_dispatch_id,
                    "status": message.status,
                },
            )
        messages.append(message)
    return messages
