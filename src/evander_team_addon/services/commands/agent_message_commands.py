from __future__ import annotations

from agent_sandbox.models import utc_now
from collections.abc import Sequence
from typing import Any
from uuid import uuid4
from .communication_helpers import _team_communication_normalized_payload
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import (
    _require_command_agent,
    _require_command_agent_by_id,
    _required_command_text,
    _resolve_upward_command_recipient,
    _team_command_priority,
    _validate_command_recipient_active,
    _validate_command_same_team,
    _validate_command_task_scope,
)
from ..domain_helpers import (
    AgentInboxItem,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamState,
    _append_agent_wake_requested_event_for_inbox_item,
    _is_direct_subordinate,
    _mark_agent_work_due,
    _optional_bool,
    _optional_text,
    _required_text,
    _unique_text_list,
)

from .executor import TeamCommandExecutor


class TeamAgentMessageCommandService(TeamCommandExecutor):
    def request_status_command(
        self,
        *,
        session_id: str,
        recipient_agent_id: str,
        question: str,
        request_id: str,
        task_id: str | None = None,
        response_requested: bool = True,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_recipient_id = _required_text(recipient_agent_id, "recipient_agent_id")
        normalized_question = _optional_text(question) or ""
        normalized_task_id = _optional_text(task_id)
        normalized_priority = _team_command_priority(
            priority,
            field_name="priority",
            default=TeamInboxPriority.NORMAL,
        )
        normalized_response_requested = (
            _optional_bool(response_requested, "response_requested")
            if response_requested is not None
            else True
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_request_status",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="status_request",
        )
        normalized_payload = _team_communication_normalized_payload(
            recipient_agent_id=normalized_recipient_id,
            task_id=normalized_task_id,
            question=normalized_question,
            message=None,
            summary=None,
            response_requested=normalized_response_requested,
            priority=normalized_priority,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_request_status")
            question_text = _required_command_text(
                normalized_question,
                field_name="question",
                code="empty_question",
            )
            recipient = _require_command_agent_by_id(
                state,
                normalized_recipient_id,
                field_name="recipient_agent_id",
            )
            if not _is_direct_subordinate(agent, recipient.id):
                raise TeamCommandValidationError(
                    code="recipient_not_direct_subordinate",
                    message=(f"agent {agent.id} can only request status from a direct subordinate"),
                    correction_context={
                        "caller_agent_id": agent.id,
                        "recipient_agent_id": recipient.id,
                        "direct_subordinate_agent_ids": list(agent.subordinate_agent_ids),
                    },
                )
            _validate_command_recipient_active(recipient)
            _validate_command_same_team(agent, recipient)
            _validate_command_task_scope(state, agent=agent, task_id=normalized_task_id)
            inbox_id = f"inbox-{uuid4().hex}"
            now = utc_now()
            item = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient.id,
                sender_agent_id=agent.id,
                task_id=normalized_task_id,
                item_type=TeamInboxItemType.STATUS_REQUEST,
                response_requested=normalized_response_requested,
                priority=normalized_priority,
                payload={"question": question_text},
                created_at=now,
                updated_at=now,
            )
            state.inbox[inbox_id] = item
            _mark_agent_work_due(state, agent_id=recipient.id, now=now)
            response_payload = {
                "inbox_item_id": inbox_id,
                "created_inbox_item_ids": [inbox_id],
                "recipient_agent_id": recipient.id,
                "intent_recorded": True,
                "async_effect_pending": True,
                "progress_made": True,
            }
            _append_agent_wake_requested_event_for_inbox_item(
                state,
                item=item,
                result=response_payload,
                now=now,
            )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[inbox_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def ask_question_command(
        self,
        *,
        session_id: str,
        question: str,
        request_id: str,
        recipient_agent_id: str | None = None,
        task_id: str | None = None,
        message: str | None = None,
        summary: str | None = None,
        response_requested: bool = True,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_recipient_id = _optional_text(recipient_agent_id)
        normalized_question = _optional_text(question) or ""
        normalized_message = _optional_text(message)
        normalized_summary = _optional_text(summary)
        normalized_task_id = _optional_text(task_id)
        normalized_priority = _team_command_priority(
            priority,
            field_name="priority",
            default=TeamInboxPriority.NORMAL,
        )
        normalized_response_requested = (
            _optional_bool(response_requested, "response_requested")
            if response_requested is not None
            else True
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_ask_question",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="question",
        )
        normalized_payload = _team_communication_normalized_payload(
            recipient_agent_id=normalized_recipient_id,
            task_id=normalized_task_id,
            question=normalized_question,
            message=normalized_message,
            summary=normalized_summary,
            response_requested=normalized_response_requested,
            priority=normalized_priority,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_ask_question")
            question_text = _required_command_text(
                normalized_question,
                field_name="question",
                code="empty_question",
            )
            recipient = _resolve_upward_command_recipient(
                state,
                agent=agent,
                recipient_agent_id=normalized_recipient_id,
                tool_name="team_ask_question",
                communication_kind="questions",
            )
            _validate_command_task_scope(state, agent=agent, task_id=normalized_task_id)
            payload: dict[str, Any] = {"question": question_text}
            if normalized_message is not None:
                payload["message"] = normalized_message
            if normalized_summary is not None:
                payload["summary"] = normalized_summary
            inbox_id = f"inbox-{uuid4().hex}"
            now = utc_now()
            item = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient.id,
                sender_agent_id=agent.id,
                task_id=normalized_task_id,
                item_type=TeamInboxItemType.QUESTION,
                response_requested=normalized_response_requested,
                priority=normalized_priority,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            state.inbox[inbox_id] = item
            _mark_agent_work_due(state, agent_id=recipient.id, now=now)
            response_payload = {
                "inbox_item_id": inbox_id,
                "created_inbox_item_ids": [inbox_id],
                "recipient_agent_id": recipient.id,
                "intent_recorded": True,
                "async_effect_pending": True,
                "progress_made": True,
            }
            _append_agent_wake_requested_event_for_inbox_item(
                state,
                item=item,
                result=response_payload,
                now=now,
            )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[inbox_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def send_progress_report_command(
        self,
        *,
        session_id: str,
        message: str,
        request_id: str,
        recipient_agent_id: str | None = None,
        task_id: str | None = None,
        summary: str | None = None,
        response_requested: bool = False,
        priority: TeamInboxPriority | str = TeamInboxPriority.LOW,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_recipient_id = _optional_text(recipient_agent_id)
        normalized_message = _optional_text(message) or ""
        normalized_summary = _optional_text(summary)
        normalized_task_id = _optional_text(task_id)
        normalized_priority = _team_command_priority(
            priority,
            field_name="priority",
            default=TeamInboxPriority.LOW,
        )
        normalized_response_requested = (
            _optional_bool(response_requested, "response_requested")
            if response_requested is not None
            else False
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_send_progress_report",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress_report",
        )
        normalized_payload = _team_communication_normalized_payload(
            recipient_agent_id=normalized_recipient_id,
            task_id=normalized_task_id,
            question=None,
            message=normalized_message,
            summary=normalized_summary,
            response_requested=normalized_response_requested,
            priority=normalized_priority,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_send_progress_report")
            message_text = _required_command_text(
                normalized_message,
                field_name="message",
                code="empty_message",
            )
            recipient = _resolve_upward_command_recipient(
                state,
                agent=agent,
                recipient_agent_id=normalized_recipient_id,
                tool_name="team_send_progress_report",
                communication_kind="progress reports",
            )
            _validate_command_task_scope(state, agent=agent, task_id=normalized_task_id)
            payload: dict[str, Any] = {"message": message_text}
            if normalized_summary is not None:
                payload["summary"] = normalized_summary
            inbox_id = f"inbox-{uuid4().hex}"
            now = utc_now()
            item = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient.id,
                sender_agent_id=agent.id,
                task_id=normalized_task_id,
                item_type=TeamInboxItemType.PROGRESS_REPORT,
                response_requested=normalized_response_requested,
                priority=normalized_priority,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            state.inbox[inbox_id] = item
            _mark_agent_work_due(state, agent_id=recipient.id, now=now)
            response_payload = {
                "inbox_item_id": inbox_id,
                "created_inbox_item_ids": [inbox_id],
                "recipient_agent_id": recipient.id,
                "context_rotation_activity": False,
                "intent_recorded": True,
                "async_effect_pending": True,
                "progress_made": True,
            }
            _append_agent_wake_requested_event_for_inbox_item(
                state,
                item=item,
                result=response_payload,
                now=now,
            )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[inbox_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamAgentMessageCommandService"]
