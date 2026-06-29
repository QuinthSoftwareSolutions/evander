from __future__ import annotations

from agent_sandbox.models import utc_now
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
    _required_command_text,
)
from ..domain_helpers import (
    TeamConversation,
    TeamConversationMessage,
    TeamState,
    _chat_preview,
    _generated_id,
    _normalize_operator_user_id,
    _operator_display_name_for_user,
    _optional_text,
    _publish_admin_chat_notifications,
    _required_text,
    _touch_conversation,
    _unique_text_list,
)

from .executor import TeamCommandExecutor


class TeamChatCommandService(TeamCommandExecutor):
    def reply_to_chat_command(
        self,
        *,
        session_id: str,
        conversation_id: str,
        message_id: str,
        body: str,
        request_id: str,
        summary: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_message_id = _required_text(message_id, "message_id")
        normalized_body = _optional_text(body) or ""
        normalized_summary = _optional_text(summary)
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
            body=normalized_body,
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
            body_text = _required_command_text(
                normalized_body,
                field_name="body",
                code="empty_body",
            )
            conversation = _require_command_conversation(
                state,
                conversation_id=normalized_conversation_id,
                agent=agent,
            )
            source_message = _require_command_chat_message(
                state,
                message_id=normalized_message_id,
                conversation_id=conversation.id,
            )
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
            now = utc_now()
            source_message.status = "replied"
            source_message.error = None
            source_message.updated_at = now
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
                status="sent",
                source_message_id=source_message.id,
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )
            _touch_conversation(conversation, now=now, preview=_chat_preview(body_text))
            return TeamCommandApplyResult(
                response_payload={
                    "chat_message_id": reply_id,
                    "conversation_id": conversation.id,
                    "replied_message_id": source_message.id,
                    "created_chat_reply_message_ids": [reply_id],
                    "replied_chat_message_ids": [source_message.id],
                    "progress_made": True,
                },
                created_domain_ids=[reply_id],
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
            body_text = _required_command_text(
                normalized_body,
                field_name="body",
                code="empty_body",
            )
            known_display_name = _operator_display_name_for_user(state, normalized_operator_id)
            known_operator_ids = {
                conversation.operator_user_id for conversation in state.conversations.values()
            }
            if normalized_operator_id not in known_operator_ids:
                raise TeamCommandValidationError(
                    code="unknown_operator_participant",
                    message=f"operator {normalized_operator_id} is not a current Team Chat participant",
                    correction_context={
                        "recipient_operator_user_id": normalized_operator_id,
                        "operator_user_ids": sorted(known_operator_ids),
                    },
                )
            conversation = next(
                (
                    item
                    for item in state.conversations.values()
                    if item.agent_id == agent.id and item.operator_user_id == normalized_operator_id
                ),
                None,
            )
            now = utc_now()
            conversation_created = False
            if conversation is None:
                conversation_id = _generated_id(
                    prefix="conversation",
                    labels=[agent.id, normalized_operator_id],
                    existing_ids=state.conversations,
                )
                conversation = TeamConversation(
                    id=conversation_id,
                    agent_id=agent.id,
                    operator_user_id=normalized_operator_id,
                    operator_display_name=known_display_name,
                    created_by=f"agent:{agent.id}",
                    last_message_preview="",
                    created_at=now,
                    updated_at=now,
                    last_interaction_at=now,
                )
                state.conversations[conversation.id] = conversation
                conversation_created = True
            message_id = f"chatmsg-{uuid4().hex}"
            metadata: dict[str, Any] = {
                "sender_kind": "team_agent",
                "recipient_operator_user_id": normalized_operator_id,
            }
            if known_display_name is not None:
                metadata["recipient_operator_display_name"] = known_display_name
            if normalized_summary is not None:
                metadata["summary"] = normalized_summary
            state.conversation_messages[message_id] = TeamConversationMessage(
                id=message_id,
                conversation_id=conversation.id,
                sender="agent",
                message_type="text",
                body=body_text,
                status="sent",
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )
            _touch_conversation(conversation, now=now, preview=_chat_preview(body_text))
            created_domain_ids = [message_id]
            if conversation_created:
                created_domain_ids.append(conversation.id)
            return TeamCommandApplyResult(
                response_payload={
                    "chat_message_id": message_id,
                    "conversation_id": conversation.id,
                    "conversation_created": conversation_created,
                    "created_operator_chat_message_ids": [message_id],
                    "progress_made": True,
                },
                created_domain_ids=created_domain_ids,
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


__all__ = ["TeamChatCommandService"]
