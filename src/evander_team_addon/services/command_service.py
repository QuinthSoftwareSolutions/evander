from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_sandbox.models import utc_now

from ..sqlite_store import (
    TeamIdempotencyConflictError,
    TeamStateSqliteCommandTransaction,
)
from ..repository import (
    TeamStateRepository,
)
from pathlib import PurePosixPath
from ..models import (
    AGENT_OPERATOR_ONLY_FIELDS,
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    AgentStatus,
    AgentTask,
    Deliverable,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GovernanceAgendaItem,
    GovernanceAgendaItemState,
    GovernanceAgendaItemType,
    GovernanceGroup,
    GovernanceRuntime,
    PersistentAgent,
    ReleasePackage,
    ReviewRequest,
    TeamCaseStatus,
    TeamConversation,
    TeamConversationMessage,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamIdempotencyRecord,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamOutboxRecord,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    _unique_text_list,
    release_package_payload_with_derived,
    task_payload_with_derived,
)
from ..git_naming import (
    ephemeral_agent_branch,
    validate_branch_name,
)
from .domain.agent_access import (
    _agent_for_session,
    _is_direct_subordinate,
    _is_project_lead_for,
    _is_team_manager,
    _supervises,
)
from .domain.case_task import (
    _TaskCreationPlan,
    _apply_case_update,
    _apply_goal_update,
    _apply_task_creation_plan,
    _plan_task_creation,
    _restore_case_task_state,
    _snapshot_case_task_state,
    _subordinate_task_finish_allowed,
    _task_creation_batch_graph_errors,
    _task_graph_edit_error,
    _task_owner_reassignment_error,
)
from .domain.chat import (
    _chat_preview,
    _publish_admin_chat_notifications,
    _touch_conversation,
)
from .domain.common import (
    DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    EPHEMERAL_WORKER_REPORT_TOOL,
    _MCP_TASK_UPDATE_STATE_VALUES,
    _NON_TERMINAL_TASK_STATES,
    _UNSET,
    _append_unique,
    _ensure_utc,
    _generated_id,
    _list_of_mappings,
    _mapping,
    _normalize_operator_user_id,
    _operator_display_name_for_user,
    _optional_bool,
    _optional_mapping,
    _optional_text,
    _require_present,
    _required_text,
    _set_task_state,
    _string_list,
)
from .domain.context_rotation import _maybe_mark_idle_context_rotation_due
from .domain.deliverables import (
    _deliverable_git_refs_from_payload,
    _deliverable_submission_next_action_hints,
    _deliverable_verification_from_payload,
    _deliverable_verification_payload,
    _normalize_deliverable_verification_mapping,
    _superseded_deliverable_for_rework,
)
from .domain.ephemeral import (
    EPHEMERAL_COMPLETED_EVENT_TYPE,
    EPHEMERAL_REQUESTED_EVENT_TYPE,
    _apply_ephemeral_acceptance,
    _create_ephemeral_assignments,
    _create_ephemeral_parent_notice,
    _ephemeral_assignment_event_payload,
    _ephemeral_request_result,
)
from .domain.git_promotion import (
    GIT_PROMOTION_EVENT_TYPE,
    _apply_git_feature_update,
    _apply_git_review_promotion,
    _normalize_agent_branch_ref,
)
from .domain.governance import _governance_groups_for_team
from .domain.manager_fallback import _manager_fallback_review_decision_guard_error
from .domain.operator_escalation import OPERATOR_ESCALATION_EVENT_TYPE
from .domain.project_status import _recalculate_project_status
from .domain.release_packages import (
    GIT_RELEASE_RECOVERY_EVENT_TYPE,
    _apply_git_release_recovery,
    _apply_git_release_request,
    _failed_release_promotion_for_recovery,
)
from .domain.review import (
    _apply_review_decision,
    _apply_review_request,
    _review_profile_for_request,
    _review_request_was_created,
)
from .domain.stand_down import (
    _active_stand_downs_for_agent,
    _apply_superior_stand_down,
    _apply_superior_stand_down_revocation,
    _stand_down_error_detail,
)
from .domain.wake import (
    _append_agent_wake_requested_event,
    _append_agent_wake_requested_event_for_inbox_item,
)
from .domain.work_due import _mark_agent_work_due


@dataclass(frozen=True)
class TeamCommandContext:
    ambient_session_id: str
    tool_name: str
    request_id: str | None = None
    caller_agent_id: str | None = None
    governance_group_id: str | None = None
    assignment_id: str | None = None
    active_dispatch_id: str | None = None
    wake_session_class: str = "team"
    correlation_id: str | None = None
    causation_id: str | None = None
    progress_classification: str = "none"


@dataclass(frozen=True)
class _ResolvedTeamCommandContext:
    context: TeamCommandContext
    caller_identity: str
    scope_kind: str
    scope_id: str
    agent: PersistentAgent | None = None
    runtime: AgentRuntime | None = None
    governance_group_id: str | None = None
    assignment_id: str | None = None
    dispatch_id: str | None = None


@dataclass(frozen=True)
class TeamCommandApplyResult:
    response_payload: dict[str, Any]
    created_domain_ids: list[str] = field(default_factory=list)
    created_event_ids: list[str] = field(default_factory=list)
    outbox_records: list[TeamOutboxRecord | Mapping[str, Any]] = field(default_factory=list)
    progress_classification: str | None = None


TeamDeliverableArtifactPublisher = Callable[..., Mapping[str, Any]]


class TeamCommandValidationError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        correction_context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.correction_context = dict(correction_context or {})


class TeamCommandService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def record_team_note_command(
        self,
        *,
        session_id: str,
        body: str,
        request_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_body = _optional_text(body) or ""
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_record_note",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="context",
        )
        normalized_payload = _team_record_note_normalized_payload(
            body=normalized_body,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = resolved.agent
            if agent is None or resolved.dispatch_id is None:
                raise TeamCommandValidationError(
                    code="invalid_command_scope",
                    message="team_record_note requires an active agent dispatch",
                )
            body_text = _optional_text(normalized_body)
            if body_text is None:
                raise TeamCommandValidationError(
                    code="empty_body",
                    message="body must be a non-empty string",
                    correction_context={"field": "body"},
                )
            action_record_id = f"action-{uuid4().hex}"
            action_payload: dict[str, Any] = {
                "tool_name": context.tool_name,
                "body": body_text,
                "handled_inbox_item_ids": list(normalized_handled_ids),
            }
            if context.correlation_id is not None:
                action_payload["correlation_id"] = context.correlation_id
            if context.causation_id is not None:
                action_payload["causation_id"] = context.causation_id
            state.action_records[action_record_id] = AgentActionRecord(
                id=action_record_id,
                agent_id=agent.id,
                dispatch_id=resolved.dispatch_id,
                action=action_payload,
                success=True,
            )
            return TeamCommandApplyResult(
                response_payload={
                    "note_id": action_record_id,
                    "action_record_id": action_record_id,
                    "progress_made": False,
                },
                created_domain_ids=[action_record_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def mark_inbox_handled_command(
        self,
        *,
        session_id: str,
        inbox_item_ids: Sequence[str],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_inbox_ids = _unique_text_list(inbox_item_ids, "inbox_item_ids")
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_mark_inbox_handled",
            request_id=None,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="inbox_handled",
        )
        normalized_payload = {
            "inbox_item_ids": sorted(normalized_inbox_ids),
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            if resolved.agent is None or resolved.dispatch_id is None:
                raise TeamCommandValidationError(
                    code="invalid_command_scope",
                    message="team_mark_inbox_handled requires an active agent dispatch",
                )
            if not normalized_inbox_ids:
                raise TeamCommandValidationError(
                    code="empty_inbox_item_ids",
                    message="inbox_item_ids must contain at least one inbox item id",
                    correction_context={"field": "inbox_item_ids"},
                )
            return TeamCommandApplyResult(
                response_payload={
                    "handled_inbox_item_ids": list(normalized_inbox_ids),
                    "progress_made": True,
                },
                created_domain_ids=list(normalized_inbox_ids),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_inbox_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

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

    def escalate_operator_command(
        self,
        *,
        session_id: str,
        title: str,
        message: str,
        request_id: str,
        urgency: str = "normal",
        project_id: str | None = None,
        task_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_title = _optional_text(title) or ""
        normalized_message = _optional_text(message) or ""
        normalized_urgency = _optional_text(urgency) or "normal"
        normalized_project_id = _optional_text(project_id)
        normalized_task_id = _optional_text(task_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_escalate_operator",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="operator_escalation",
        )
        normalized_payload = _team_operator_escalation_normalized_payload(
            title=normalized_title,
            message=normalized_message,
            urgency=normalized_urgency,
            project_id=normalized_project_id,
            task_id=normalized_task_id,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_escalate_operator")
            title_text = _required_command_text(
                normalized_title,
                field_name="title",
                code="empty_title",
            )
            message_text = _required_command_text(
                normalized_message,
                field_name="message",
                code="empty_message",
            )
            if not _is_team_manager(state, agent):
                raise TeamCommandValidationError(
                    code="not_top_level_team_manager",
                    message="team_escalate_operator can only be called by the top-level team manager",
                    correction_context={"caller_agent_id": agent.id},
                )
            team = _require_present(state.teams, agent.team_id or "", "team")
            _validate_command_project_scope(
                state,
                team_id=team.id,
                project_id=normalized_project_id,
            )
            _validate_command_task_scope(state, agent=agent, task_id=normalized_task_id)
            escalation_id = f"escalation-{uuid4().hex}"
            payload: dict[str, Any] = {
                "team_id": team.id,
                "manager_agent_id": agent.id,
                "title": title_text,
                "message": message_text,
                "urgency": normalized_urgency,
            }
            if normalized_project_id is not None:
                payload["project_id"] = normalized_project_id
            if normalized_task_id is not None:
                payload["task_id"] = normalized_task_id
            if context.correlation_id is not None:
                payload["correlation_id"] = context.correlation_id
            if context.causation_id is not None:
                payload["causation_id"] = context.causation_id

            now = utc_now()
            if not team.escalate_to_governance:
                target = _optional_text(team.operator_communication_target_id)
                if target is None:
                    raise TeamCommandValidationError(
                        code="operator_escalation_target_missing",
                        message=(
                            f"team {team.id} has no operator_communication_target_id "
                            "for escalations"
                        ),
                        correction_context={"team_id": team.id},
                    )
                payload["communication_target_id"] = target
                inbox_id = f"inbox-{uuid4().hex}"
                state.inbox[inbox_id] = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=agent.id,
                    sender_agent_id=agent.id,
                    task_id=normalized_task_id,
                    item_type=TeamInboxItemType.ESCALATION,
                    response_requested=False,
                    priority=TeamInboxPriority.NORMAL,
                    payload={"escalation_id": escalation_id, **payload},
                    created_at=now,
                    updated_at=now,
                )
                outbox_id = f"outbox-{uuid4().hex}"
                outbox_record = TeamOutboxRecord(
                    id=outbox_id,
                    intent_type=OPERATOR_ESCALATION_EVENT_TYPE,
                    aggregate_type="team_operator_escalation",
                    aggregate_id=escalation_id,
                    idempotency_key=(
                        f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:{escalation_id}"
                    ),
                    payload={
                        "event_type": OPERATOR_ESCALATION_EVENT_TYPE,
                        "correlation_key": escalation_id,
                        "payload": payload,
                    },
                    created_at=now,
                    updated_at=now,
                )
                return TeamCommandApplyResult(
                    response_payload={
                        "escalation_id": escalation_id,
                        "operator_event_ids": [escalation_id],
                        "operator_escalation_event_ids": [escalation_id],
                        "outbox_record_ids": [outbox_id],
                        "created_inbox_item_ids": [inbox_id],
                        "intent_recorded": True,
                        "async_effect_pending": True,
                        "downstream_effect_completed": False,
                        "progress_made": True,
                    },
                    created_domain_ids=[escalation_id, inbox_id, outbox_id],
                    created_event_ids=[escalation_id],
                    outbox_records=[outbox_record],
                )

            governance_groups = _governance_groups_for_team(state, team.id)
            if not governance_groups:
                raise TeamCommandValidationError(
                    code="governance_group_not_found",
                    message=(
                        f"team {team.id} is configured for governance escalation "
                        "but is not in a governance group"
                    ),
                    correction_context={"team_id": team.id},
                )
            agenda_ids: list[str] = []
            for group in governance_groups:
                agenda_id = f"governance-agenda-{uuid4().hex}"
                group_runtime = state.governance_runtimes.setdefault(
                    group.id,
                    GovernanceRuntime(governance_group_id=group.id),
                )
                state.governance_agenda[agenda_id] = GovernanceAgendaItem(
                    id=agenda_id,
                    governance_group_id=group.id,
                    item_type=GovernanceAgendaItemType.TEAM_ESCALATION,
                    title=title_text,
                    summary=message_text,
                    team_id=team.id,
                    project_id=normalized_project_id,
                    payload=payload
                    | {
                        "escalation_id": escalation_id,
                        "governance_group_id": group.id,
                    },
                    created_at=now,
                    updated_at=now,
                )
                group_runtime.next_wake_at = None
                group_runtime.updated_at = now
                agenda_ids.append(agenda_id)
            return TeamCommandApplyResult(
                response_payload={
                    "escalation_id": escalation_id,
                    "governance_agenda_item_ids": agenda_ids,
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "progress_made": True,
                },
                created_domain_ids=[escalation_id, *agenda_ids],
                created_event_ids=agenda_ids,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def publish_review_branch_command(
        self,
        *,
        session_id: str,
        request_id: str,
        assignment_id: str | None = None,
        deliverable_id: str | None = None,
        source_branch: str | None = None,
        commit_sha: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_assignment_id = _optional_text(assignment_id)
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_source_branch = _optional_text(source_branch)
        normalized_commit_sha = _optional_text(commit_sha)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_publish_review_branch",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_review_promotion",
        )
        normalized_payload = _team_publish_review_branch_normalized_payload(
            assignment_id=normalized_assignment_id,
            deliverable_id=normalized_deliverable_id,
            source_branch=normalized_source_branch,
            commit_sha=normalized_commit_sha,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_publish_review_branch")
            has_assignment = normalized_assignment_id is not None
            has_deliverable = normalized_deliverable_id is not None
            if has_assignment == has_deliverable:
                raise TeamCommandValidationError(
                    code="invalid_review_promotion_shape",
                    message=(
                        "team_publish_review_branch requires exactly one of "
                        "assignment_id or deliverable_id"
                    ),
                    correction_context={
                        "assignment_id": normalized_assignment_id,
                        "deliverable_id": normalized_deliverable_id,
                    },
                )
            if has_assignment and (
                normalized_source_branch is not None or normalized_commit_sha is not None
            ):
                raise TeamCommandValidationError(
                    code="invalid_review_promotion_shape",
                    message=(
                        "assignment_id review promotions must not include "
                        "source_branch or commit_sha"
                    ),
                    correction_context={
                        "assignment_id": normalized_assignment_id,
                        "source_branch": normalized_source_branch,
                        "commit_sha": normalized_commit_sha,
                    },
                )
            request: dict[str, Any] = {}
            if normalized_assignment_id is not None:
                request["assignment_id"] = normalized_assignment_id
            if normalized_deliverable_id is not None:
                request["deliverable_id"] = normalized_deliverable_id
            if normalized_source_branch is not None:
                request["source_branch"] = normalized_source_branch
            if normalized_commit_sha is not None:
                request["commit_sha"] = normalized_commit_sha
            intent_result = _empty_git_command_result()
            promotion_id = _apply_git_review_promotion(
                state,
                actor=agent,
                request=request,
                result=intent_result,
            )
            if promotion_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_review_promotion_invalid",
                )
            promotion = state.git_promotions[promotion_id]
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "promotion_id": promotion_id,
                    "promotion_event_id": promotion_id,
                    "promotion_event_ids": [promotion_id],
                    "git_promotion_event_ids": [promotion_id],
                    "target_review_branch": promotion.target_branch,
                    "target_branch": promotion.target_branch,
                    "source_branch": promotion.source_branch,
                    "commit_sha": promotion.commit_sha,
                    "project_id": promotion.project_id,
                    "task_id": promotion.task_id,
                    "deliverable_id": promotion.deliverable_id,
                    "assignment_id": normalized_assignment_id,
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": [
                        "Review branch promotion intent recorded; wait for the async Git promotion result before assuming the branch exists."
                    ],
                    "progress_made": True,
                },
                created_domain_ids=[promotion_id, *outbox_ids],
                created_event_ids=[promotion_id],
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_feature_branch_command(
        self,
        *,
        session_id: str,
        task_id: str,
        source_review_branches: Sequence[str],
        request_id: str,
        feature_branch: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_sources = _string_list(
            source_review_branches,
            "source_review_branches",
        )
        normalized_feature_branch = _optional_text(feature_branch)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_feature_branch",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_feature_promotion",
        )
        normalized_payload = _team_update_feature_branch_normalized_payload(
            task_id=normalized_task_id,
            source_review_branches=normalized_sources,
            feature_branch=normalized_feature_branch,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_feature_branch")
            intent_result = _empty_git_command_result()
            promotion_id = _apply_git_feature_update(
                state,
                actor=agent,
                update={
                    "task_id": normalized_task_id,
                    "source_review_branches": list(normalized_sources),
                    "feature_branch": normalized_feature_branch,
                },
                result=intent_result,
            )
            if promotion_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_feature_update_invalid",
                )
            promotion = state.git_promotions[promotion_id]
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "promotion_id": promotion_id,
                    "promotion_event_id": promotion_id,
                    "promotion_event_ids": [promotion_id],
                    "git_promotion_event_ids": [promotion_id],
                    "target_feature_branch": promotion.target_branch,
                    "target_branch": promotion.target_branch,
                    "source_review_branches": list(normalized_sources),
                    "project_id": promotion.project_id,
                    "task_id": promotion.task_id,
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": [
                        "Feature branch promotion intent recorded; wait for the async Git promotion result before requesting release from this branch."
                    ],
                    "progress_made": True,
                },
                created_domain_ids=[promotion_id, *outbox_ids],
                created_event_ids=[promotion_id],
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def request_release_command(
        self,
        *,
        session_id: str,
        project_id: str,
        deliverable_ids: Sequence[str],
        request_id: str,
        id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        summary: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_release_id = _optional_text(id)
        normalized_deliverable_ids = _string_list(
            deliverable_ids,
            "deliverable_ids",
        )
        normalized_release_branch = _optional_text(release_branch)
        normalized_canonical_branch = _optional_text(canonical_branch)
        normalized_summary = _optional_text(summary)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_request_release",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_release_request",
        )
        normalized_payload = _team_request_release_normalized_payload(
            release_id=normalized_release_id,
            project_id=normalized_project_id,
            deliverable_ids=normalized_deliverable_ids,
            release_branch=normalized_release_branch,
            canonical_branch=normalized_canonical_branch,
            summary=normalized_summary,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_request_release")
            if normalized_release_branch is not None:
                try:
                    validate_branch_name(normalized_release_branch)
                except ValueError as exc:
                    raise TeamCommandValidationError(
                        code="invalid_release_branch",
                        message=f"release_branch is invalid: {exc}",
                        correction_context={"release_branch": normalized_release_branch},
                    ) from None
            if normalized_canonical_branch is not None:
                try:
                    validate_branch_name(normalized_canonical_branch)
                except ValueError as exc:
                    raise TeamCommandValidationError(
                        code="invalid_canonical_branch",
                        message=f"canonical_branch is invalid: {exc}",
                        correction_context={"canonical_branch": normalized_canonical_branch},
                    ) from None
            intent_result = _empty_git_command_result()
            projects_to_recalc: set[str] = set()
            project = state.projects.get(normalized_project_id)
            if project is None:
                raise TeamCommandValidationError(
                    code="project_not_found",
                    message=(
                        f"git release request references missing project {normalized_project_id}"
                    ),
                    correction_context={"project_id": normalized_project_id},
                )
            release_id = _apply_git_release_request(
                state,
                actor=agent,
                request={
                    "id": normalized_release_id,
                    "project_id": normalized_project_id,
                    "deliverable_ids": list(normalized_deliverable_ids),
                    "release_branch": normalized_release_branch,
                    "canonical_branch": normalized_canonical_branch,
                    "summary": normalized_summary,
                },
                result=intent_result,
                projects_to_recalc=projects_to_recalc,
            )
            if release_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_release_request_invalid",
                )
            for recalc_id in sorted(projects_to_recalc):
                _recalculate_project_status(state, recalc_id)
            release = state.release_packages[release_id]
            promotion_event_ids = list(intent_result["git_promotion_event_ids"])
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            release_payload = release_package_payload_with_derived(state, release)
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": bool(promotion_event_ids),
                    "downstream_effect_completed": False,
                    "release_package_id": release_id,
                    "release_package_ids": [release_id],
                    "promotion_event_ids": promotion_event_ids,
                    "git_promotion_event_ids": promotion_event_ids,
                    "release_branch": release.release_branch,
                    "target_release_branch": release.release_branch,
                    "canonical_branch": release.canonical_branch,
                    "release_package": release_payload,
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": _release_command_next_action_hints(
                        state,
                        release,
                        promotion_event_ids=promotion_event_ids,
                    ),
                    "progress_made": True,
                },
                created_domain_ids=[release_id, *promotion_event_ids, *outbox_ids],
                created_event_ids=promotion_event_ids,
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def recover_release_command(
        self,
        *,
        session_id: str,
        release_package_id: str,
        agent_branch: str,
        commit_sha: str,
        request_id: str,
        summary: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_release_id = _required_text(
            release_package_id,
            "release_package_id",
        )
        normalized_agent_branch = _required_text(agent_branch, "agent_branch")
        normalized_commit_sha = _required_text(commit_sha, "commit_sha")
        normalized_summary = _optional_text(summary)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_recover_release",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_release_recovery",
        )
        normalized_payload = _team_recover_release_normalized_payload(
            release_package_id=normalized_release_id,
            agent_branch=normalized_agent_branch,
            commit_sha=normalized_commit_sha,
            summary=normalized_summary,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_recover_release")
            intent_result = _empty_git_command_result()
            release_id = _apply_git_release_recovery(
                state,
                actor=agent,
                request={
                    "release_package_id": normalized_release_id,
                    "agent_branch": normalized_agent_branch,
                    "commit_sha": normalized_commit_sha,
                    "summary": normalized_summary,
                },
                result=intent_result,
            )
            if release_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_release_recovery_invalid",
                )
            release = state.release_packages[release_id]
            failed_promotion = _failed_release_promotion_for_recovery(state, release)
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            recovery_event_ids = list(intent_result["release_recovery_event_ids"])
            if not recovery_event_ids:
                recovery_event_ids = [release_id]
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "release_package_id": release_id,
                    "release_recovery_event_ids": recovery_event_ids,
                    "promotion_id": failed_promotion.id if failed_promotion else None,
                    "promotion_event_ids": ([failed_promotion.id] if failed_promotion else []),
                    "agent_branch": _normalize_agent_branch_ref(normalized_agent_branch),
                    "target_branch": (
                        release.release_branch
                        or (failed_promotion.target_branch if failed_promotion else None)
                    ),
                    "release_package": release_package_payload_with_derived(
                        state,
                        release,
                    ),
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": [
                        "Release recovery intent recorded; wait for the async Git recovery effect before retrying or marking the release published."
                    ],
                    "progress_made": True,
                },
                created_domain_ids=[release_id, *outbox_ids],
                created_event_ids=recovery_event_ids,
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def request_ephemeral_agent_command(
        self,
        *,
        session_id: str,
        task_id: str,
        title: str,
        prompt: str,
        request_id: str,
        id: str | None = None,
        worker_session_id: str | None = None,
        model: str | None = None,
        agent_provider: str | None = None,
        execution_backend: str | None = None,
        container_image: str | None = None,
        allowed_tools: Sequence[str] | None = None,
        ttl_hours: int | None = None,
        directory_path: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        return self._request_ephemeral_assignments_command(
            session_id=session_id,
            request_id=request_id,
            assignments=[
                {
                    "id": id,
                    "task_id": task_id,
                    "worker_session_id": worker_session_id,
                    "title": title,
                    "prompt": prompt,
                    "model": model,
                    "agent_provider": agent_provider,
                    "execution_backend": execution_backend,
                    "container_image": container_image,
                    "allowed_tools": list(allowed_tools or []),
                    "ttl_hours": ttl_hours,
                    "directory_path": directory_path,
                }
            ],
            handled_inbox_item_ids=handled_inbox_item_ids,
            correlation_id=correlation_id,
            causation_id=causation_id,
            tool_name="team_request_ephemeral_agent",
            single=True,
        )

    def request_ephemeral_agents_command(
        self,
        *,
        session_id: str,
        assignments: Sequence[Mapping[str, Any]],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        return self._request_ephemeral_assignments_command(
            session_id=session_id,
            request_id=request_id,
            assignments=assignments,
            handled_inbox_item_ids=handled_inbox_item_ids,
            correlation_id=correlation_id,
            causation_id=causation_id,
            tool_name="team_request_ephemeral_agents",
            single=False,
        )

    def _request_ephemeral_assignments_command(
        self,
        *,
        session_id: str,
        request_id: str,
        assignments: Sequence[Mapping[str, Any]],
        handled_inbox_item_ids: Sequence[str] | None,
        correlation_id: str | None,
        causation_id: str | None,
        tool_name: str,
        single: bool,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_assignments = [
            _normalize_ephemeral_request_mapping(item, field_name=f"assignments[{index}]")
            for index, item in enumerate(assignments)
        ]
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name=tool_name,
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_request",
        )
        normalized_payload = _team_request_ephemeral_normalized_payload(
            assignments=normalized_assignments,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, tool_name)
            if not normalized_assignments:
                raise TeamCommandValidationError(
                    code="empty_ephemeral_assignments",
                    message="assignments must contain at least one assignment",
                    correction_context={"assignments": []},
                )
            result = _ephemeral_request_result()
            try:
                _create_ephemeral_assignments(
                    state,
                    parent=agent,
                    requests=[dict(item) for item in normalized_assignments],
                    dispatch_id=resolved.dispatch_id,
                    result=result,
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="ephemeral_request_failed",
                    correction_context={
                        "caller_agent_id": agent.id,
                        "assignments": list(normalized_assignments),
                    },
                ) from exc

            assignment_ids = list(result["created_ephemeral_assignment_ids"])
            created_assignments = [
                _require_present(
                    state.ephemeral_assignments,
                    assignment_id,
                    "ephemeral assignment",
                )
                for assignment_id in assignment_ids
            ]
            events = [
                _ephemeral_assignment_event_payload(state, assignment_id)
                for assignment_id in assignment_ids
            ]
            outbox_records = _team_event_outbox_records(events)
            outbox_ids = [record.id for record in outbox_records]
            worker_session_ids = [assignment.session_id for assignment in created_assignments]
            worker_branch_names = [
                ephemeral_agent_branch(assignment.session_id, assignment.id)
                for assignment in created_assignments
            ]
            response_payload: dict[str, Any] = {
                "intent_recorded": True,
                "async_effect_pending": True,
                "downstream_effect_completed": False,
                "created_ephemeral_assignment_ids": assignment_ids,
                "assignment_ids": assignment_ids,
                "worker_session_ids": worker_session_ids,
                "worker_branch_names": worker_branch_names,
                "worker_start_intent_ids": assignment_ids,
                "ephemeral_event_ids": assignment_ids,
                "outbox_record_ids": outbox_ids,
                "events": events,
                "progress_made": True,
            }
            if single and created_assignments:
                assignment = created_assignments[0]
                response_payload.update(
                    {
                        "assignment_id": assignment.id,
                        "worker_session_id": assignment.session_id,
                        "worker_branch_name": ephemeral_agent_branch(
                            assignment.session_id,
                            assignment.id,
                        ),
                    }
                )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[*assignment_ids, *outbox_ids],
                created_event_ids=assignment_ids,
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def accept_ephemeral_result_command(
        self,
        *,
        session_id: str,
        assignment_id: str,
        request_id: str,
        deliverable_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_accept_ephemeral_result",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_acceptance",
        )
        normalized_payload = _team_accept_ephemeral_result_normalized_payload(
            assignment_id=normalized_assignment_id,
            deliverable_id=normalized_deliverable_id,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_accept_ephemeral_result")
            assignment = _require_present(
                state.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            _require_parent_assignment_owner(
                assignment,
                agent=agent,
                tool_name="team_accept_ephemeral_result",
            )
            try:
                created_deliverable_id = _apply_ephemeral_acceptance(
                    state,
                    assignment=assignment,
                    accepted=True,
                    deliverable_id=normalized_deliverable_id,
                    rejection_reason=None,
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="ephemeral_acceptance_failed",
                    correction_context={
                        "assignment_id": assignment.id,
                        "caller_agent_id": agent.id,
                        "state": assignment.state.value,
                    },
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    "assignment_id": assignment.id,
                    "deliverable_id": created_deliverable_id,
                    "accepted_ephemeral_assignment_ids": [assignment.id],
                    "state": assignment.state.value,
                    "progress_made": True,
                },
                created_domain_ids=[
                    item for item in [assignment.id, created_deliverable_id] if item is not None
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def reject_ephemeral_result_command(
        self,
        *,
        session_id: str,
        assignment_id: str,
        request_id: str,
        rejection_reason: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_rejection_reason = _optional_text(rejection_reason)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_reject_ephemeral_result",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_rejection",
        )
        normalized_payload = _team_reject_ephemeral_result_normalized_payload(
            assignment_id=normalized_assignment_id,
            rejection_reason=normalized_rejection_reason,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_reject_ephemeral_result")
            assignment = _require_present(
                state.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            _require_parent_assignment_owner(
                assignment,
                agent=agent,
                tool_name="team_reject_ephemeral_result",
            )
            _apply_ephemeral_acceptance(
                state,
                assignment=assignment,
                accepted=False,
                deliverable_id=None,
                rejection_reason=normalized_rejection_reason,
            )
            return TeamCommandApplyResult(
                response_payload={
                    "assignment_id": assignment.id,
                    "rejected_ephemeral_assignment_ids": [assignment.id],
                    "rejection_reason": assignment.error,
                    "state": assignment.state.value,
                    "progress_made": True,
                },
                created_domain_ids=[assignment.id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def report_ephemeral_result_command(
        self,
        *,
        session_id: str,
        request_id: str,
        assignment_id: str | None = None,
        summary: str | None = None,
        artifact_refs: Sequence[str] | None = None,
        agent_branch: str | None = None,
        commit_sha: str | None = None,
        error: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_assignment_id = _optional_text(assignment_id)
        normalized_summary = _optional_text(summary)
        normalized_artifact_refs = _string_list(artifact_refs or [], "artifact_refs")
        normalized_agent_branch = _optional_text(agent_branch)
        normalized_commit_sha = _optional_text(commit_sha)
        normalized_error = _optional_text(error)
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name=EPHEMERAL_WORKER_REPORT_TOOL,
            request_id=normalized_request_id,
            assignment_id=normalized_assignment_id,
            wake_session_class="ephemeral_worker",
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_result",
        )
        normalized_payload = _team_report_ephemeral_result_normalized_payload(
            assignment_id=normalized_assignment_id,
            summary=normalized_summary,
            artifact_refs=normalized_artifact_refs,
            agent_branch=normalized_agent_branch,
            commit_sha=normalized_commit_sha,
            error=normalized_error,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            if resolved.assignment_id is None:
                raise TeamCommandValidationError(
                    code="assignment_not_resolved",
                    message="ephemeral assignment could not be resolved from the worker session",
                    correction_context={"session_id": normalized_session_id},
                )
            assignment = _require_present(
                state.ephemeral_assignments,
                resolved.assignment_id,
                "ephemeral assignment",
            )
            _validate_ephemeral_result_report(
                assignment,
                summary=normalized_summary,
                agent_branch=normalized_agent_branch,
                error=normalized_error,
            )
            created_inbox_item_ids: list[str] = []
            events: list[dict[str, Any]] = []
            outbox_records: list[TeamOutboxRecord] = []
            if normalized_error is not None:
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = normalized_error
                assignment.updated_at = utc_now()
                reported_success = False
            else:
                assignment.summary = _required_text(normalized_summary, "summary")
                assignment.artifact_refs = list(normalized_artifact_refs)
                assignment.agent_branch = normalized_agent_branch
                assignment.commit_sha = normalized_commit_sha
                assignment.error = None
                assignment.state = EphemeralAssignmentState.COMPLETED
                inbox_id = _create_ephemeral_parent_notice(state, assignment)
                created_inbox_item_ids.append(inbox_id)
                assignment.updated_at = utc_now()
                events.append(
                    _ephemeral_assignment_event_payload(
                        state,
                        assignment.id,
                        event_type=EPHEMERAL_COMPLETED_EVENT_TYPE,
                    )
                )
                outbox_records = _team_event_outbox_records(events)
                reported_success = True
            outbox_ids = [record.id for record in outbox_records]
            return TeamCommandApplyResult(
                response_payload={
                    "assignment_id": assignment.id,
                    "state": assignment.state.value,
                    "reported_success": reported_success,
                    "assignment_error": assignment.error,
                    "created_inbox_item_ids": created_inbox_item_ids,
                    "intent_recorded": reported_success,
                    "async_effect_pending": reported_success,
                    "downstream_effect_completed": False,
                    "completed_event_ids": [assignment.id] if reported_success else [],
                    "ephemeral_event_ids": [assignment.id] if reported_success else [],
                    "outbox_record_ids": outbox_ids,
                    "events": events,
                    "progress_made": True,
                },
                created_domain_ids=[
                    assignment.id,
                    *created_inbox_item_ids,
                    *outbox_ids,
                ],
                created_event_ids=[assignment.id] if reported_success else [],
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=[],
            apply_command=_apply,
            require_active_dispatch=False,
        )

    def create_case_command(
        self,
        *,
        session_id: str,
        title: str,
        request_id: str,
        case_id: str | None = None,
        project_id: str | None = None,
        description: str | None = None,
        status: TeamCaseStatus | str = TeamCaseStatus.OPEN,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        owner_agent_id: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        tags: Sequence[str] | None = None,
        task_ids: Sequence[str] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_case_id = _optional_text(case_id)
        normalized_project_id = _optional_text(project_id)
        normalized_title = _optional_text(title) or ""
        normalized_description = _optional_text(description) or ""
        normalized_status = _team_case_status_value(status, default=TeamCaseStatus.OPEN)
        normalized_priority = _team_case_priority_value(
            priority,
            default=TeamInboxPriority.NORMAL,
        )
        normalized_owner_id = _optional_text(owner_agent_id)
        normalized_source = _optional_text(source)
        normalized_source_ref = _optional_text(source_ref)
        normalized_tags = _unique_text_list(tags or [], "tags")
        normalized_task_ids = _unique_text_list(task_ids or [], "task_ids")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_case",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = _team_create_case_normalized_payload(
            case_id=normalized_case_id,
            project_id=normalized_project_id,
            title=normalized_title,
            description=normalized_description,
            status=normalized_status,
            priority=normalized_priority,
            owner_agent_id=normalized_owner_id,
            source=normalized_source,
            source_ref=normalized_source_ref,
            tags=normalized_tags,
            task_ids=normalized_task_ids,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_create_case")
            if normalized_case_id is not None and normalized_case_id in state.cases:
                raise TeamCommandValidationError(
                    code="case_already_exists",
                    message=f"case {normalized_case_id} already exists",
                    correction_context={"case_id": normalized_case_id},
                )
            update: dict[str, Any] = {
                "title": normalized_title,
                "description": normalized_description,
                "status": normalized_status,
                "priority": normalized_priority,
                "tags": list(normalized_tags),
                "task_ids": list(normalized_task_ids),
            }
            if normalized_case_id is not None:
                update["id"] = normalized_case_id
            if normalized_project_id is not None:
                update["project_id"] = normalized_project_id
            if normalized_owner_id is not None:
                update["owner_agent_id"] = normalized_owner_id
            if normalized_source is not None:
                update["source"] = normalized_source
            if normalized_source_ref is not None:
                update["source_ref"] = normalized_source_ref
            snapshot = _snapshot_case_task_state(state)
            try:
                created_case_id, created = _apply_case_update(
                    state,
                    actor=agent,
                    update=update,
                )
            except ValueError as exc:
                _restore_case_task_state(state, snapshot)
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="case_validation_failed",
                    correction_context={
                        "case_id": normalized_case_id,
                        "project_id": normalized_project_id,
                        "owner_agent_id": normalized_owner_id,
                        "task_ids": list(normalized_task_ids),
                    },
                ) from exc
            if not created:
                _restore_case_task_state(state, snapshot)
                raise TeamCommandValidationError(
                    code="case_already_exists",
                    message=f"case {created_case_id} already exists",
                    correction_context={"case_id": created_case_id},
                )
            return TeamCommandApplyResult(
                response_payload={
                    "case_id": created_case_id,
                    "created_case_ids": [created_case_id],
                    "progress_made": True,
                },
                created_domain_ids=[created_case_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_case_command(
        self,
        *,
        session_id: str,
        case_id: str,
        request_id: str | None = None,
        project_id: object = _UNSET,
        title: object = _UNSET,
        description: object = _UNSET,
        status: object = _UNSET,
        priority: object = _UNSET,
        owner_agent_id: object = _UNSET,
        source: object = _UNSET,
        source_ref: object = _UNSET,
        tags: object = _UNSET,
        task_ids: object = _UNSET,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_case_id = _required_text(case_id, "case_id")
        normalized_request_id = _optional_text(request_id)
        normalized_updates = _normalize_case_update_command_fields(
            project_id=project_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            owner_agent_id=owner_agent_id,
            source=source,
            source_ref=source_ref,
            tags=tags,
            task_ids=task_ids,
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_case",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "case_id": normalized_case_id,
            "updates": normalized_updates,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_case")
            if normalized_case_id not in state.cases:
                raise TeamCommandValidationError(
                    code="case_not_found",
                    message=f"case {normalized_case_id} does not exist",
                    correction_context={"case_id": normalized_case_id},
                )
            before = _case_command_fingerprint(state, normalized_case_id)
            snapshot = _snapshot_case_task_state(state)
            try:
                updated_case_id, created = _apply_case_update(
                    state,
                    actor=agent,
                    update={"case_id": normalized_case_id, **normalized_updates},
                )
            except ValueError as exc:
                _restore_case_task_state(state, snapshot)
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="case_validation_failed",
                    correction_context={
                        "case_id": normalized_case_id,
                        "updates": dict(normalized_updates),
                    },
                ) from exc
            if created:
                _restore_case_task_state(state, snapshot)
                raise TeamCommandValidationError(
                    code="case_not_found",
                    message=f"case {normalized_case_id} does not exist",
                    correction_context={"case_id": normalized_case_id},
                )
            changed = before != _case_command_fingerprint(state, updated_case_id)
            return TeamCommandApplyResult(
                response_payload={
                    "case_id": updated_case_id,
                    "updated_case_ids": [updated_case_id] if changed else [],
                    "progress_made": changed,
                },
                created_domain_ids=[updated_case_id] if changed else [],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def create_task_command(
        self,
        *,
        session_id: str,
        owner_agent_id: str,
        project_id: str,
        title: str,
        description: str,
        request_id: str,
        task_id: str | None = None,
        case_id: str | None = None,
        priority: int = 0,
        state: TeamTaskState | str = TeamTaskState.PENDING,
        parent_task_id: str | None = None,
        dependency_task_ids: Sequence[str] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        creation = _normalize_task_creation_command_payload(
            task_id=task_id,
            owner_agent_id=owner_agent_id,
            project_id=project_id,
            case_id=case_id,
            priority=priority,
            title=title,
            description=description,
            state=state,
            parent_task_id=parent_task_id,
            dependency_task_ids=dependency_task_ids,
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_task",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = _team_create_task_normalized_payload(
            creation=creation,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_create_task")
            plan = _command_plan_task_creation(
                state,
                actor=agent,
                creation=creation,
                reserved_task_ids=set(),
            )
            graph_errors = _task_creation_batch_graph_errors(
                state,
                [plan],
                actor=agent,
            )
            if graph_errors:
                error = graph_errors.get(plan.task_id) or next(iter(graph_errors.values()))
                raise TeamCommandValidationError(
                    code="task_graph_invalid",
                    message=error,
                    correction_context=_task_graph_correction_context(
                        state,
                        project_id=plan.project_id,
                        task_id=plan.task_id,
                        parent_task_id=plan.parent_task_id,
                        dependency_task_ids=plan.dependency_task_ids,
                    ),
                )
            created_id = _apply_task_creation_plan(state, plan=plan, actor=agent)
            _recalculate_project_status(state, plan.project_id)
            response_payload = {
                "task_id": created_id,
                "created_task_ids": [created_id],
                "progress_made": True,
            }
            _append_agent_wake_requested_event(
                state,
                agent_id=plan.owner_id,
                result=response_payload,
                reason="task_ready",
                now=utc_now(),
                delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
            )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[created_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def create_tasks_command(
        self,
        *,
        session_id: str,
        tasks: Sequence[Mapping[str, Any]],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_tasks = [
            _normalize_task_creation_mapping(item, field_name=f"tasks[{index}]")
            for index, item in enumerate(_list_of_mappings(list(tasks), "tasks"))
        ]
        if not normalized_tasks:
            raise ValueError("tasks must contain at least one task")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_tasks",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "tasks": normalized_tasks,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_create_tasks")
            plans: list[_TaskCreationPlan] = []
            reserved_task_ids: set[str] = set()
            for creation in normalized_tasks:
                plan = _command_plan_task_creation(
                    state,
                    actor=agent,
                    creation=creation,
                    reserved_task_ids=reserved_task_ids,
                )
                reserved_task_ids.add(plan.task_id)
                plans.append(plan)
            graph_errors = _task_creation_batch_graph_errors(
                state,
                plans,
                actor=agent,
            )
            if graph_errors:
                raise TeamCommandValidationError(
                    code="task_graph_invalid",
                    message="; ".join(graph_errors[task_id] for task_id in sorted(graph_errors)),
                    correction_context=_task_graph_correction_context_for_plans(
                        state,
                        plans,
                        graph_errors=graph_errors,
                    ),
                )
            created_ids: list[str] = []
            recalc_project_ids: set[str] = set()
            for plan in plans:
                created_id = _apply_task_creation_plan(state, plan=plan, actor=agent)
                created_ids.append(created_id)
                recalc_project_ids.add(plan.project_id)
            for recalc_id in sorted(recalc_project_ids):
                _recalculate_project_status(state, recalc_id)
            response_payload = {
                "created_task_ids": created_ids,
                "progress_made": True,
            }
            now = utc_now()
            for plan in plans:
                _append_agent_wake_requested_event(
                    state,
                    agent_id=plan.owner_id,
                    result=response_payload,
                    reason="task_ready",
                    now=now,
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=list(created_ids),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_task_command(
        self,
        *,
        session_id: str,
        task_id: str,
        request_id: str | None = None,
        state: object = _UNSET,
        priority: object = _UNSET,
        title: object = _UNSET,
        description: object = _UNSET,
        owner_agent_id: object = _UNSET,
        dependency_task_ids: object = _UNSET,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_request_id = _optional_text(request_id)
        normalized_updates = _normalize_task_update_command_fields(
            state=state,
            priority=priority,
            title=title,
            description=description,
            owner_agent_id=owner_agent_id,
            dependency_task_ids=dependency_task_ids,
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_task",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "task_id": normalized_task_id,
            "updates": normalized_updates,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_task")
            task = state_model.tasks.get(normalized_task_id)
            if task is None:
                raise TeamCommandValidationError(
                    code="task_not_found",
                    message=f"task {normalized_task_id} does not exist",
                    correction_context={"task_id": normalized_task_id},
                )
            is_owner = task.owner_agent_id == agent.id
            raw_state = normalized_updates.get("state")
            has_state = "state" in normalized_updates
            has_priority = "priority" in normalized_updates
            has_title = "title" in normalized_updates
            has_description = "description" in normalized_updates
            new_owner_id = normalized_updates.get("owner_agent_id")
            has_owner_reassignment = (
                isinstance(new_owner_id, str) and new_owner_id != task.owner_agent_id
            )
            has_dependency_edit = "dependency_task_ids" in normalized_updates
            subordinate_finish_allowed = (
                has_state
                and not has_priority
                and not has_title
                and not has_description
                and _subordinate_task_finish_allowed(
                    state_model,
                    actor=agent,
                    task=task,
                    raw_state=raw_state,
                    new_owner_id=new_owner_id if has_owner_reassignment else None,
                )
            )
            if (
                (has_state or has_priority or has_title or has_description)
                and not is_owner
                and not subordinate_finish_allowed
            ):
                raise TeamCommandValidationError(
                    code="task_update_not_owner",
                    message=f"agent {agent.id} cannot update task {task.id}",
                    correction_context={
                        "task_id": task.id,
                        "caller_agent_id": agent.id,
                        "owner_agent_id": task.owner_agent_id,
                    },
                )
            next_state: TeamTaskState | None = None
            if has_state:
                try:
                    next_state = TeamTaskState(str(raw_state))
                except ValueError as exc:
                    raise TeamCommandValidationError(
                        code="invalid_task_state",
                        message=f"task update has invalid state {raw_state}",
                        correction_context={
                            "state": raw_state,
                            "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                        },
                    ) from exc
                if next_state.value not in _MCP_TASK_UPDATE_STATE_VALUES:
                    raise TeamCommandValidationError(
                        code="invalid_task_state",
                        message=(
                            "team_update_task.state must be pending, in_progress, "
                            "blocked, waiting_for_review, or finished"
                        ),
                        correction_context={
                            "state": next_state.value,
                            "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                        },
                    )
            if has_owner_reassignment and isinstance(new_owner_id, str):
                reassignment_error = _task_owner_reassignment_error(
                    state_model,
                    actor=agent,
                    task=task,
                    new_owner_id=new_owner_id,
                )
                if reassignment_error is not None:
                    raise TeamCommandValidationError(
                        code="task_reassignment_not_permitted",
                        message=reassignment_error,
                        correction_context={
                            "task_id": task.id,
                            "caller_agent_id": agent.id,
                            "owner_agent_id": task.owner_agent_id,
                            "new_owner_agent_id": new_owner_id,
                        },
                    )
            new_dependency_ids: list[str] | None = None
            if has_dependency_edit:
                graph_permitted = (
                    is_owner
                    or _supervises(state_model, agent.id, task.owner_agent_id)
                    or _is_project_lead_for(state_model, agent.id, task.project_id)
                    or _is_team_manager(state_model, agent)
                )
                if not graph_permitted:
                    raise TeamCommandValidationError(
                        code="task_graph_not_permitted",
                        message=(
                            f"agent {agent.id} is not permitted to edit the task graph "
                            f"for task {task.id}"
                        ),
                        correction_context={
                            "task_id": task.id,
                            "caller_agent_id": agent.id,
                            "owner_agent_id": task.owner_agent_id,
                        },
                    )
                new_dependency_ids = list(normalized_updates["dependency_task_ids"])
                graph_error = _task_graph_edit_error(
                    state_model,
                    task_id=task.id,
                    project_id=task.project_id,
                    parent_task_id=task.parent_task_id,
                    dependency_task_ids=new_dependency_ids,
                )
                if graph_error is not None:
                    raise TeamCommandValidationError(
                        code="task_graph_invalid",
                        message=graph_error,
                        correction_context=_task_graph_correction_context(
                            state_model,
                            project_id=task.project_id,
                            task_id=task.id,
                            parent_task_id=task.parent_task_id,
                            dependency_task_ids=new_dependency_ids,
                        ),
                    )
            changed = False
            now = utc_now()
            if next_state is not None:
                changed = _set_task_state(task, next_state, now=now) or changed
            if has_priority:
                next_priority = int(normalized_updates["priority"])
                if task.priority != next_priority:
                    task.priority = next_priority
                    changed = True
            if has_title and isinstance(normalized_updates["title"], str):
                if task.title != normalized_updates["title"]:
                    task.title = normalized_updates["title"]
                    changed = True
            if has_description and isinstance(normalized_updates["description"], str):
                if task.description != normalized_updates["description"]:
                    task.description = normalized_updates["description"]
                    changed = True
            if has_owner_reassignment and isinstance(new_owner_id, str):
                old_owner_id = task.owner_agent_id
                task.owner_agent_id = new_owner_id
                _maybe_mark_idle_context_rotation_due(
                    state_model,
                    agent_id=old_owner_id,
                    now=now,
                )
                if task.state in _NON_TERMINAL_TASK_STATES:
                    _mark_agent_work_due(state_model, agent_id=new_owner_id, now=now)
                changed = True
            if new_dependency_ids is not None and task.dependency_task_ids != new_dependency_ids:
                task.dependency_task_ids = new_dependency_ids
                changed = True
            if changed:
                task.updated_at = now
                if task.state in _NON_TERMINAL_TASK_STATES:
                    _mark_agent_work_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
                else:
                    _maybe_mark_idle_context_rotation_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
                if next_state is not None:
                    _recalculate_project_status(state_model, task.project_id)
            response_payload = {
                "task_id": task.id,
                "updated_task_ids": [task.id] if changed else [],
                "progress_made": changed,
            }
            if changed and task.state in _NON_TERMINAL_TASK_STATES:
                _append_agent_wake_requested_event(
                    state_model,
                    agent_id=task.owner_agent_id,
                    result=response_payload,
                    reason="task_ready",
                    now=now,
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[task.id] if changed else [],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_goal_command(
        self,
        *,
        session_id: str,
        project_id: str,
        goal_id: str,
        state: TeamRecordState | str,
        request_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_goal_id = _required_text(goal_id, "goal_id")
        normalized_state = _team_record_state_value(state)
        normalized_request_id = _optional_text(request_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_goal",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "project_id": normalized_project_id,
            "goal_id": normalized_goal_id,
            "state": normalized_state,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_goal")
            project = state_model.projects.get(normalized_project_id)
            before_state = None
            if project is not None:
                goal = next(
                    (item for item in project.goals if item.id == normalized_goal_id),
                    None,
                )
                before_state = goal.state.value if goal is not None else None
            result: dict[str, Any] = {"errors": []}
            projects_to_recalc: set[str] = set()
            updated_id = _apply_goal_update(
                state_model,
                actor=agent,
                update={
                    "project_id": normalized_project_id,
                    "goal_id": normalized_goal_id,
                    "state": normalized_state,
                },
                result=result,
                projects_to_recalc=projects_to_recalc,
            )
            if result["errors"] or updated_id is None:
                raise TeamCommandValidationError(
                    code="goal_update_failed",
                    message="; ".join(result["errors"]) or "goal update failed",
                    correction_context={
                        "project_id": normalized_project_id,
                        "goal_id": normalized_goal_id,
                        "state": normalized_state,
                    },
                )
            for recalc_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, recalc_id)
            changed = before_state != normalized_state
            return TeamCommandApplyResult(
                response_payload={
                    "project_id": normalized_project_id,
                    "goal_id": updated_id,
                    "updated_goal_ids": [updated_id] if changed else [],
                    "progress_made": changed,
                },
                created_domain_ids=[updated_id] if changed else [],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def stand_down_agent_command(
        self,
        *,
        session_id: str,
        agent_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_reason = _optional_text(reason) or ""
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_stand_down_agent",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "agent_id": normalized_agent_id,
            "reason": normalized_reason,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_stand_down_agent")
            target = _require_command_agent_by_id(
                state,
                normalized_agent_id,
                field_name="agent_id",
            )
            if target.status != AgentStatus.ACTIVE:
                raise TeamCommandValidationError(
                    code="stand_down_target_not_active",
                    message=f"agent {target.id} is {target.status.value}",
                    correction_context={
                        "agent_id": target.id,
                        "status": target.status.value,
                    },
                )
            try:
                stand_down_id = _apply_superior_stand_down(
                    state,
                    actor=agent,
                    request={"agent_id": normalized_agent_id, "reason": normalized_reason},
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="stand_down_failed",
                    correction_context={
                        "caller_agent_id": agent.id,
                        "agent_id": normalized_agent_id,
                    },
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    "stand_down_id": stand_down_id,
                    "created_stand_down_ids": [stand_down_id],
                    "progress_made": True,
                },
                created_domain_ids=[stand_down_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def revoke_stand_down_command(
        self,
        *,
        session_id: str,
        agent_id: str,
        request_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _optional_text(request_id)
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_revoke_stand_down",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "agent_id": normalized_agent_id,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_revoke_stand_down")
            try:
                revoked_ids = _apply_superior_stand_down_revocation(
                    state,
                    actor=agent,
                    request={"agent_id": normalized_agent_id},
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="stand_down_revocation_failed",
                    correction_context={
                        "caller_agent_id": agent.id,
                        "agent_id": normalized_agent_id,
                    },
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    "revoked_stand_down_ids": revoked_ids,
                    "progress_made": bool(revoked_ids),
                },
                created_domain_ids=list(revoked_ids),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def submit_deliverable_command(
        self,
        *,
        session_id: str,
        task_id: str,
        summary: str,
        request_id: str,
        id: str | None = None,
        supersedes_deliverable_id: str | None = None,
        artifact_paths: Sequence[str] | None = None,
        artifact_refs: Sequence[str] | None = None,
        artifact_space_ref: str | None = None,
        git_refs: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_publisher: TeamDeliverableArtifactPublisher | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable = _normalize_deliverable_submission_mapping(
            {
                "id": id,
                "task_id": task_id,
                "summary": summary,
                "supersedes_deliverable_id": supersedes_deliverable_id,
                "artifact_paths": list(artifact_paths or []),
                "artifact_refs": list(artifact_refs or []),
                "artifact_space_ref": artifact_space_ref,
                "git_refs": dict(git_refs or {}),
                "verification": dict(verification) if verification is not None else None,
            },
            field_name="deliverable",
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_submit_deliverable",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="deliverable",
        )
        normalized_payload = _team_submit_deliverable_normalized_payload(
            deliverable=normalized_deliverable,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_submit_deliverable")
            task = _command_deliverable_task_for_submission(
                state,
                agent=agent,
                task_id=normalized_deliverable["task_id"],
            )
            deliverable_id = _command_deliverable_id(
                state,
                deliverable=normalized_deliverable,
                request_id=normalized_request_id,
            )
            _validate_command_deliverable_submission_pre_publication(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
            )
            published_records = _publish_command_deliverable_artifacts(
                state,
                agent=agent,
                task=task,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id or "manual",
                artifact_paths=normalized_deliverable["artifact_paths"],
                artifact_publisher=artifact_publisher,
            )
            created_inbox_ids = _apply_command_deliverable_submission(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=False,
            )
            published_refs = [
                _required_text(record.get("artifact_ref"), "artifact_ref")
                for record in published_records
            ]
            return TeamCommandApplyResult(
                response_payload={
                    "deliverable_id": deliverable_id,
                    "created_deliverable_ids": [deliverable_id],
                    "created_inbox_item_ids": created_inbox_ids,
                    "published_artifact_refs": published_refs,
                    "published_deliverable_artifacts": [
                        dict(record) for record in published_records
                    ],
                    "task_id": task.id,
                    "task_state": state.tasks[task.id].state.value,
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "verification": normalized_deliverable.get("verification"),
                    "next_action_hints": _deliverable_submission_next_action_hints(
                        deliverable_id=deliverable_id,
                        deliverable=normalized_deliverable,
                        created_inbox_ids=created_inbox_ids,
                        created_review_request_ids=[],
                    ),
                    "progress_made": True,
                },
                created_domain_ids=[deliverable_id, *created_inbox_ids],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def submit_deliverable_for_review_command(
        self,
        *,
        session_id: str,
        deliverable: Mapping[str, Any],
        reviews: Sequence[Mapping[str, Any]],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_publisher: TeamDeliverableArtifactPublisher | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable = _normalize_deliverable_submission_mapping(
            deliverable,
            field_name="deliverable",
        )
        normalized_reviews = [
            _normalize_review_request_mapping(item, field_name=f"reviews[{index}]")
            for index, item in enumerate(reviews)
        ]
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_submit_deliverable_for_review",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="deliverable_review_request",
        )
        normalized_payload = _team_submit_deliverable_for_review_normalized_payload(
            deliverable=normalized_deliverable,
            reviews=normalized_reviews,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(
                resolved,
                "team_submit_deliverable_for_review",
            )
            task = _command_deliverable_task_for_submission(
                state,
                agent=agent,
                task_id=normalized_deliverable["task_id"],
            )
            deliverable_id = _command_deliverable_id(
                state,
                deliverable=normalized_deliverable,
                request_id=normalized_request_id,
            )
            _validate_command_deliverable_submission_pre_publication(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
            )
            if not normalized_reviews:
                raise TeamCommandValidationError(
                    code="empty_reviews",
                    message="reviews must contain at least one review request",
                    correction_context={"field": "reviews"},
                )
            planned_reviews = _plan_command_review_requests_for_deliverable(
                state,
                actor=agent,
                deliverable_id=deliverable_id,
                task=task,
                reviews=normalized_reviews,
                request_id=normalized_request_id,
            )
            published_records = _publish_command_deliverable_artifacts(
                state,
                agent=agent,
                task=task,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id or "manual",
                artifact_paths=normalized_deliverable["artifact_paths"],
                artifact_publisher=artifact_publisher,
            )
            _apply_command_deliverable_submission(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=True,
            )
            review_result = {"errors": [], "created_inbox_item_ids": []}
            created_review_ids: list[str] = []
            for index, request_payload in enumerate(planned_reviews):
                created_id = _apply_review_request(
                    state,
                    actor=agent,
                    request=request_payload,
                    result=review_result,
                )
                if created_id is None:
                    raise TeamCommandValidationError(
                        code="review_request_failed",
                        message="; ".join(review_result["errors"])
                        or f"reviews[{index}] could not be created",
                        correction_context={
                            "deliverable_id": deliverable_id,
                            "review": dict(request_payload),
                            "errors": list(review_result["errors"]),
                        },
                    )
                if _review_request_was_created(review_result, created_id):
                    created_review_ids.append(created_id)
            for review_id in created_review_ids:
                review = state.review_requests[review_id]
                _mark_agent_work_due(
                    state,
                    agent_id=review.reviewer_agent_id,
                    now=utc_now(),
                )
            published_refs = [
                _required_text(record.get("artifact_ref"), "artifact_ref")
                for record in published_records
            ]
            created_inbox_ids = list(review_result["created_inbox_item_ids"])
            return TeamCommandApplyResult(
                response_payload={
                    "deliverable_id": deliverable_id,
                    "created_deliverable_ids": [deliverable_id],
                    "created_review_request_ids": created_review_ids,
                    "existing_review_request_ids": list(
                        review_result.get("existing_review_request_ids", [])
                    ),
                    "created_inbox_item_ids": created_inbox_ids,
                    "published_artifact_refs": published_refs,
                    "published_deliverable_artifacts": [
                        dict(record) for record in published_records
                    ],
                    "task_id": task.id,
                    "task_state": state.tasks[task.id].state.value,
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "verification": normalized_deliverable.get("verification"),
                    "next_action_hints": _deliverable_submission_next_action_hints(
                        deliverable_id=deliverable_id,
                        deliverable=normalized_deliverable,
                        created_inbox_ids=created_inbox_ids,
                        created_review_request_ids=created_review_ids,
                    ),
                    "progress_made": True,
                },
                created_domain_ids=[
                    deliverable_id,
                    *created_review_ids,
                    *created_inbox_ids,
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def request_review_command(
        self,
        *,
        session_id: str,
        deliverable_id: str,
        reviewer_agent_id: str,
        request_id: str,
        id: str | None = None,
        review_profile_id: str | None = None,
        instructions: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_review = _normalize_review_request_mapping(
            {
                "id": id,
                "deliverable_id": deliverable_id,
                "reviewer_agent_id": reviewer_agent_id,
                "review_profile_id": review_profile_id,
                "instructions": instructions,
            },
            field_name="review",
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_request_review",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="review_request",
        )
        normalized_payload = _team_request_review_normalized_payload(
            review=normalized_review,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_request_review")
            reviewer = _require_command_agent_by_id(
                state,
                normalized_review["reviewer_agent_id"],
                field_name="reviewer_agent_id",
            )
            _validate_command_recipient_active(reviewer)
            review_payload = dict(normalized_review)
            if review_payload.get("id") is None:
                review_payload["id"] = _command_review_request_id(
                    state,
                    deliverable_id=normalized_review["deliverable_id"],
                    review_request=normalized_review,
                    request_id=normalized_request_id,
                )
            review_result = {"errors": [], "created_inbox_item_ids": []}
            created_id = _apply_review_request(
                state,
                actor=agent,
                request=review_payload,
                result=review_result,
            )
            if created_id is None:
                raise TeamCommandValidationError(
                    code="review_request_failed",
                    message="; ".join(review_result["errors"])
                    or "review request could not be created",
                    correction_context={
                        "review": dict(normalized_review),
                        "errors": list(review_result["errors"]),
                    },
                )
            review = state.review_requests[created_id]
            existing_review_ids = list(review_result.get("existing_review_request_ids", []))
            created_review_ids = (
                [created_id] if _review_request_was_created(review_result, created_id) else []
            )
            if created_review_ids:
                _mark_agent_work_due(state, agent_id=review.reviewer_agent_id, now=utc_now())
            created_inbox_ids = list(review_result["created_inbox_item_ids"])
            if existing_review_ids:
                next_action_hints = [
                    "Review request already exists for this deliverable and reviewer; "
                    "wait for the assigned reviewer to decide it before treating the "
                    "deliverable or task as accepted."
                ]
            else:
                next_action_hints = [
                    "Review request created; wait for the assigned reviewer to "
                    "decide it before treating the deliverable or task as accepted."
                ]
            progress_made = bool(created_review_ids or normalized_handled_ids)
            return TeamCommandApplyResult(
                response_payload={
                    "review_request_id": created_id,
                    "created_review_request_ids": created_review_ids,
                    "existing_review_request_ids": existing_review_ids,
                    "created_inbox_item_ids": created_inbox_ids,
                    "deliverable_id": review.deliverable_id,
                    "reviewer_agent_id": review.reviewer_agent_id,
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "next_action_hints": next_action_hints,
                    "progress_made": progress_made,
                },
                created_domain_ids=[*created_review_ids, *created_inbox_ids],
                progress_classification="review_request" if progress_made else "none",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def decide_review_command(
        self,
        *,
        session_id: str,
        review_request_id: str,
        state: str,
        request_id: str,
        findings: Sequence[str] | None = None,
        criteria: Sequence[Mapping[str, Any]] | None = None,
        override_routing_gap_reason: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_decision = _normalize_review_decision_mapping(
            {
                "review_request_id": review_request_id,
                "state": state,
                "findings": list(findings or []),
                "criteria": [dict(item) for item in criteria or []],
                "override_routing_gap_reason": override_routing_gap_reason,
            },
            field_name="review_decision",
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_decide_review",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="review_decision",
        )
        normalized_payload = _team_decide_review_normalized_payload(
            decision=normalized_decision,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            command_state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_decide_review")
            review = command_state.review_requests.get(normalized_decision["review_request_id"])
            if review is None:
                raise TeamCommandValidationError(
                    code="review_request_not_found",
                    message=(
                        "review decision references missing review request "
                        f"{normalized_decision['review_request_id']}"
                    ),
                    correction_context={
                        "review_request_id": normalized_decision["review_request_id"],
                    },
                )
            guard_error = (
                _manager_fallback_review_decision_guard_error(
                    command_state,
                    actor=agent,
                    review=review,
                )
                if _is_team_manager(command_state, agent)
                and resolved.runtime is not None
                and resolved.runtime.wake_reason == "manager_fallback"
                else None
            )
            override_reason = _optional_text(normalized_decision.get("override_routing_gap_reason"))
            if override_reason is not None and guard_error is None:
                raise TeamCommandValidationError(
                    code="override_routing_gap_not_required",
                    message=(
                        "override_routing_gap_reason is only allowed when current "
                        "manager-fallback routing rules require an override"
                    ),
                    correction_context=_review_decision_correction_context(
                        command_state,
                        review=review,
                        reviewer=command_state.agents.get(review.reviewer_agent_id),
                    ),
                )
            decision_result = {"errors": [], "created_inbox_item_ids": []}
            projects_to_recalc: set[str] = set()
            decided_id = _apply_review_decision(
                command_state,
                actor=agent,
                decision=normalized_decision,
                result=decision_result,
                projects_to_recalc=projects_to_recalc,
                enforce_manager_fallback_routing_guard=guard_error is not None,
            )
            if decided_id is None:
                raise TeamCommandValidationError(
                    code="review_decision_failed",
                    message="; ".join(decision_result["errors"])
                    or "review decision could not be recorded",
                    correction_context=_review_decision_correction_context(
                        command_state,
                        review=review,
                        reviewer=command_state.agents.get(review.reviewer_agent_id),
                    )
                    | {"errors": list(decision_result["errors"])},
                )
            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(command_state, project_id)
            decided_review = command_state.review_requests[decided_id]
            deliverable = command_state.deliverables[decided_review.deliverable_id]
            task = command_state.tasks[deliverable.task_id]
            return TeamCommandApplyResult(
                response_payload={
                    "review_request_id": decided_id,
                    "decided_review_request_ids": [decided_id],
                    "created_inbox_item_ids": list(decision_result["created_inbox_item_ids"]),
                    "review": decided_review.model_dump(mode="json"),
                    "deliverable": deliverable.model_dump(mode="json"),
                    "task": task_payload_with_derived(command_state, task),
                    "deliverable_id": deliverable.id,
                    "task_id": task.id,
                    "intent_recorded": True,
                    "async_effect_pending": bool(decision_result["created_inbox_item_ids"]),
                    "progress_made": True,
                },
                created_domain_ids=[
                    decided_id,
                    *list(decision_result["created_inbox_item_ids"]),
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def _execute_team_command(
        self,
        *,
        context: TeamCommandContext,
        normalized_payload: Mapping[str, Any],
        handled_inbox_item_ids: Sequence[str],
        apply_command: Callable[
            [TeamState, _ResolvedTeamCommandContext],
            TeamCommandApplyResult,
        ],
        require_active_dispatch: bool,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_context = _normalize_team_command_context(context)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids,
            "handled_inbox_item_ids",
        )
        payload_digest = self._repository.normalized_payload_digest(normalized_payload)

        def _transaction(
            payload: dict[str, Any],
            tx: TeamStateSqliteCommandTransaction,
        ) -> tuple[TeamState, dict[str, Any]]:
            state = TeamState.model_validate(payload)
            resolved, resolve_error = self._resolve_team_command_context(
                state,
                normalized_context,
                require_active_dispatch=require_active_dispatch,
            )
            if resolve_error is not None:
                activity = self._append_command_failure_activity(
                    tx,
                    resolved=resolved,
                    error=resolve_error,
                )
                return state, self._command_failure_response(
                    state=state,
                    error=resolve_error,
                    activity=activity,
                )
            if resolved is None:  # pragma: no cover - defensive guard
                raise RuntimeError("command context resolution did not produce a result")

            if normalized_context.request_id is not None:
                try:
                    replay = tx.get_idempotency_record(
                        tool_name=normalized_context.tool_name,
                        caller_identity=resolved.caller_identity,
                        scope_kind=resolved.scope_kind,
                        scope_id=resolved.scope_id,
                        request_id=normalized_context.request_id,
                    )
                    if replay is not None:
                        if replay.normalized_payload_digest != payload_digest:
                            raise TeamIdempotencyConflictError(
                                tool_name=normalized_context.tool_name,
                                caller_identity=resolved.caller_identity,
                                scope_kind=resolved.scope_kind,
                                scope_id=resolved.scope_id,
                                request_id=normalized_context.request_id,
                                existing_digest=replay.normalized_payload_digest,
                                attempted_digest=payload_digest,
                            )
                        return state, dict(replay.response_payload)
                except TeamIdempotencyConflictError as exc:
                    return state, _command_idempotency_conflict_response(
                        state=state,
                        conflict=exc,
                    )

            try:
                self._validate_command_handled_inbox_items(
                    state,
                    resolved=resolved,
                    handled_inbox_item_ids=normalized_handled_ids,
                )
                apply_result = apply_command(state, resolved)
            except TeamCommandValidationError as exc:
                activity = self._append_command_failure_activity(
                    tx,
                    resolved=resolved,
                    error=exc,
                )
                return state, self._command_failure_response(
                    state=state,
                    error=exc,
                    activity=activity,
                )
            except ValueError as exc:
                error = TeamCommandValidationError(
                    code="validation_failed",
                    message=str(exc),
                )
                activity = self._append_command_failure_activity(
                    tx,
                    resolved=resolved,
                    error=error,
                )
                return state, self._command_failure_response(
                    state=state,
                    error=error,
                    activity=activity,
                )

            now = utc_now()
            for inbox_item_id in normalized_handled_ids:
                item = state.inbox[inbox_item_id]
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = now

            next_state = state.model_copy(
                update={"revision": state.revision + 1, "updated_at": now},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))

            activity_id = f"activity-{uuid4().hex}"
            outbox_records = _team_command_outbox_records(apply_result)
            stored_outbox_records = [
                tx.enqueue_outbox_record(outbox_record) for outbox_record in outbox_records
            ]
            response = _command_success_response(
                state=validated,
                context=normalized_context,
                activity_id=activity_id,
                handled_inbox_item_ids=normalized_handled_ids,
                apply_result=apply_result,
            )
            activity_progress_classification = (
                _optional_text(apply_result.progress_classification)
                or normalized_context.progress_classification
            )
            _append_response_outbox_record_ids(response, stored_outbox_records)
            tx.append_dispatch_activity(
                TeamDispatchActivity(
                    id=activity_id,
                    tool_name=normalized_context.tool_name,
                    caller_session_id=normalized_context.ambient_session_id,
                    caller_identity=resolved.caller_identity,
                    agent_id=resolved.agent.id if resolved.agent is not None else None,
                    governance_group_id=resolved.governance_group_id,
                    assignment_id=resolved.assignment_id,
                    dispatch_id=resolved.dispatch_id,
                    request_id=normalized_context.request_id,
                    correlation_id=normalized_context.correlation_id,
                    causation_id=normalized_context.causation_id,
                    status=TeamDispatchActivityStatus.SUCCESS,
                    progress_classification=activity_progress_classification,
                    action_record_id=_optional_text(response.get("action_record_id")),
                    audit_record_id=_optional_text(response.get("audit_record_id")),
                )
            )
            if normalized_context.request_id is not None:
                created_domain_ids = list(apply_result.created_domain_ids)
                for outbox_record in stored_outbox_records:
                    if outbox_record.id not in created_domain_ids:
                        created_domain_ids.append(outbox_record.id)
                tx.store_idempotency_record(
                    TeamIdempotencyRecord(
                        id=f"idempotency-{uuid4().hex}",
                        tool_name=normalized_context.tool_name,
                        caller_identity=resolved.caller_identity,
                        scope_kind=resolved.scope_kind,
                        scope_id=resolved.scope_id,
                        request_id=normalized_context.request_id,
                        normalized_payload_digest=payload_digest,
                        response_payload=response,
                        created_domain_ids=created_domain_ids,
                        created_event_ids=apply_result.created_event_ids,
                    )
                )
            return validated, response

        return self._repository.store.update_object_with_command_transaction(_transaction)

    def _resolve_team_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        try:
            scope = _team_command_context_scope(context)
        except TeamCommandValidationError as exc:
            return None, exc
        if scope == "governance":
            return self._resolve_governance_command_context(
                state,
                context,
                require_active_dispatch=require_active_dispatch,
            )
        if scope == "assignment":
            return self._resolve_assignment_command_context(
                state,
                context,
                require_active_dispatch=require_active_dispatch,
            )
        return self._resolve_agent_command_context(
            state,
            context,
            require_active_dispatch=require_active_dispatch,
        )

    def _resolve_agent_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        try:
            agent = _resolve_command_agent(state, context)
        except TeamCommandValidationError as exc:
            return None, exc
        caller_identity = f"agent:{agent.id}"
        runtime = state.agent_runtimes.get(agent.id)
        dispatch_id = context.active_dispatch_id or (
            runtime.active_dispatch_id if runtime else None
        )
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=caller_identity,
            scope_kind="dispatch" if dispatch_id is not None else "agent",
            scope_id=dispatch_id or agent.id,
            agent=agent,
            runtime=runtime,
            dispatch_id=dispatch_id,
        )
        if agent.status != AgentStatus.ACTIVE:
            return resolved, TeamCommandValidationError(
                code="agent_not_active",
                message=f"agent {agent.id} is {agent.status.value}",
                correction_context={"agent_id": agent.id, "status": agent.status.value},
            )
        if agent.team_id is None:
            return resolved, TeamCommandValidationError(
                code="teamless_agent",
                message=f"agent {agent.id} is not assigned to a team",
                correction_context={"agent_id": agent.id},
            )
        active_stand_downs = _active_stand_downs_for_agent(state, agent.id)
        if active_stand_downs:
            return resolved, TeamCommandValidationError(
                code="agent_stood_down",
                message=f"agent {agent.id} is stood down: {_stand_down_error_detail(active_stand_downs)}",
                correction_context={"agent_id": agent.id},
            )
        if not require_active_dispatch:
            return resolved, None
        if runtime is None or runtime.active_dispatch_id is None:
            return resolved, TeamCommandValidationError(
                code="no_active_dispatch",
                message=f"agent {agent.id} has no active Team dispatch lease",
                correction_context={"agent_id": agent.id},
            )
        if (
            context.active_dispatch_id is not None
            and context.active_dispatch_id != runtime.active_dispatch_id
        ):
            return resolved, TeamCommandValidationError(
                code="dispatch_mismatch",
                message=(
                    f"dispatch_id {context.active_dispatch_id} does not match active "
                    f"Team dispatch {runtime.active_dispatch_id}"
                ),
                correction_context={
                    "agent_id": agent.id,
                    "active_dispatch_id": runtime.active_dispatch_id,
                    "requested_dispatch_id": context.active_dispatch_id,
                },
            )
        if runtime.wake_leased_until is None or _ensure_utc(runtime.wake_leased_until) <= utc_now():
            return resolved, TeamCommandValidationError(
                code="dispatch_lease_expired",
                message=f"active Team dispatch {runtime.active_dispatch_id} is not leased",
                correction_context={
                    "agent_id": agent.id,
                    "active_dispatch_id": runtime.active_dispatch_id,
                    "wake_leased_until": (
                        runtime.wake_leased_until.isoformat()
                        if runtime.wake_leased_until is not None
                        else None
                    ),
                },
            )
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=caller_identity,
            scope_kind="dispatch",
            scope_id=runtime.active_dispatch_id,
            agent=agent,
            runtime=runtime,
            dispatch_id=runtime.active_dispatch_id,
        )
        return resolved, None

    def _resolve_governance_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        try:
            agent = _resolve_command_agent(state, context)
        except TeamCommandValidationError as exc:
            return None, exc
        group, group_error = _resolve_command_governance_group(state, context, agent)
        group_id = group.id if group is not None else context.governance_group_id
        runtime = state.governance_runtimes.get(group.id) if group is not None else None
        dispatch_id = context.active_dispatch_id or (
            runtime.active_dispatch_id if runtime else None
        )
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=(
                f"governance:{group_id}:{agent.id}"
                if group_id is not None
                else f"governance:{agent.id}"
            ),
            scope_kind="dispatch" if dispatch_id is not None else "governance_group",
            scope_id=dispatch_id or group_id or agent.id,
            agent=agent,
            governance_group_id=group_id,
            dispatch_id=dispatch_id,
        )
        if agent.status != AgentStatus.ACTIVE:
            return resolved, TeamCommandValidationError(
                code="agent_not_active",
                message=f"governance manager {agent.id} is {agent.status.value}",
                correction_context={"agent_id": agent.id, "status": agent.status.value},
            )
        if group_error is not None:
            return resolved, group_error
        if group is None:  # pragma: no cover - defensive guard
            raise RuntimeError("governance command resolution produced no group")
        if require_active_dispatch:
            if runtime is None or runtime.active_dispatch_id is None:
                return resolved, TeamCommandValidationError(
                    code="no_active_governance_dispatch",
                    message=f"governance group {group.id} has no active dispatch lease",
                    correction_context={"governance_group_id": group.id},
                )
            if (
                context.active_dispatch_id is not None
                and context.active_dispatch_id != runtime.active_dispatch_id
            ):
                return resolved, TeamCommandValidationError(
                    code="dispatch_mismatch",
                    message=(
                        f"dispatch_id {context.active_dispatch_id} does not match active "
                        f"governance dispatch {runtime.active_dispatch_id}"
                    ),
                    correction_context={
                        "governance_group_id": group.id,
                        "active_dispatch_id": runtime.active_dispatch_id,
                        "requested_dispatch_id": context.active_dispatch_id,
                    },
                )
            if (
                runtime.wake_leased_until is None
                or _ensure_utc(runtime.wake_leased_until) <= utc_now()
            ):
                return resolved, TeamCommandValidationError(
                    code="dispatch_lease_expired",
                    message=f"active governance dispatch {runtime.active_dispatch_id} is not leased",
                    correction_context={
                        "governance_group_id": group.id,
                        "active_dispatch_id": runtime.active_dispatch_id,
                        "wake_leased_until": (
                            runtime.wake_leased_until.isoformat()
                            if runtime.wake_leased_until is not None
                            else None
                        ),
                    },
                )
            resolved = _ResolvedTeamCommandContext(
                context=context,
                caller_identity=f"governance:{group.id}:{agent.id}",
                scope_kind="dispatch",
                scope_id=runtime.active_dispatch_id,
                agent=agent,
                governance_group_id=group.id,
                dispatch_id=runtime.active_dispatch_id,
            )
        return resolved, None

    def _resolve_assignment_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        assignment, assignment_error = _resolve_command_assignment(state, context)
        assignment_id = assignment.id if assignment is not None else context.assignment_id
        resolved = (
            _ResolvedTeamCommandContext(
                context=context,
                caller_identity=(
                    f"assignment:{assignment_id}"
                    if assignment_id is not None
                    else f"worker:{context.ambient_session_id}"
                ),
                scope_kind="assignment",
                scope_id=assignment_id or context.ambient_session_id,
                assignment_id=assignment_id,
            )
            if assignment_id is not None
            else None
        )
        if assignment_error is not None:
            return resolved, assignment_error
        if assignment is None:  # pragma: no cover - defensive guard
            raise RuntimeError("assignment command resolution produced no assignment")
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=f"assignment:{assignment.id}",
            scope_kind="assignment",
            scope_id=assignment.id,
            assignment_id=assignment.id,
        )
        if require_active_dispatch:
            return resolved, TeamCommandValidationError(
                code="assignment_dispatch_not_supported",
                message="ephemeral assignment commands do not use active Team dispatch leases",
                correction_context={"assignment_id": assignment.id},
            )
        return resolved, None

    def _validate_command_handled_inbox_items(
        self,
        state: TeamState,
        *,
        resolved: _ResolvedTeamCommandContext,
        handled_inbox_item_ids: Sequence[str],
    ) -> None:
        if not handled_inbox_item_ids:
            return
        if resolved.agent is None:
            raise TeamCommandValidationError(
                code="invalid_inbox_scope",
                message="handled_inbox_item_ids requires an agent caller",
            )
        for inbox_item_id in handled_inbox_item_ids:
            item = state.inbox.get(inbox_item_id)
            if item is None:
                raise TeamCommandValidationError(
                    code="inbox_item_not_found",
                    message=f"inbox item {inbox_item_id} does not exist",
                    correction_context={"inbox_item_id": inbox_item_id},
                )
            if item.recipient_agent_id != resolved.agent.id:
                raise TeamCommandValidationError(
                    code="inbox_item_not_owned",
                    message=f"agent {resolved.agent.id} cannot handle inbox item {inbox_item_id}",
                    correction_context={
                        "inbox_item_id": inbox_item_id,
                        "recipient_agent_id": item.recipient_agent_id,
                        "caller_agent_id": resolved.agent.id,
                    },
                )
            if item.state != TeamInboxItemState.PENDING:
                raise TeamCommandValidationError(
                    code="inbox_item_not_pending",
                    message=f"inbox item {inbox_item_id} is {item.state.value}",
                    correction_context={
                        "inbox_item_id": inbox_item_id,
                        "state": item.state.value,
                    },
                )

    def _append_command_failure_activity(
        self,
        tx: TeamStateSqliteCommandTransaction,
        *,
        resolved: _ResolvedTeamCommandContext | None,
        error: TeamCommandValidationError,
    ) -> TeamDispatchActivity | None:
        if resolved is None:
            return None
        context = resolved.context
        if not (resolved.agent or resolved.governance_group_id or resolved.assignment_id):
            return None
        return tx.append_dispatch_activity(
            TeamDispatchActivity(
                id=f"activity-{uuid4().hex}",
                tool_name=context.tool_name,
                caller_session_id=context.ambient_session_id,
                caller_identity=resolved.caller_identity,
                agent_id=resolved.agent.id if resolved.agent is not None else None,
                governance_group_id=resolved.governance_group_id,
                assignment_id=resolved.assignment_id,
                dispatch_id=resolved.dispatch_id,
                request_id=context.request_id,
                correlation_id=context.correlation_id,
                causation_id=context.causation_id,
                status=TeamDispatchActivityStatus.FAILURE,
                progress_classification="none",
                error_code=error.code,
                error_message=error.message,
                correction_context_summary=_command_correction_context_summary(
                    error.correction_context,
                ),
            )
        )

    @staticmethod
    def _command_failure_response(
        *,
        state: TeamState,
        error: TeamCommandValidationError,
        activity: TeamDispatchActivity | None,
    ) -> dict[str, Any]:
        error_payload: dict[str, Any] = {
            "code": error.code,
            "message": error.message,
        }
        if error.correction_context:
            error_payload["correction_context"] = dict(error.correction_context)
        return {
            "success": False,
            "applied": False,
            "progress_made": False,
            "revision": state.revision,
            "activity_id": activity.id if activity is not None else None,
            "action_record_id": None,
            "errors": [error_payload],
            "correction_context": dict(error.correction_context),
        }

    def mark_governance_agenda_handled_by_session(
        self,
        *,
        agenda_item_id: str,
        requesting_session_id: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, GovernanceAgendaItem]:
        normalized_item_id = _required_text(agenda_item_id, "agenda_item_id")
        normalized_session_id = _required_text(
            requesting_session_id,
            "requesting_session_id",
        )

        def _mutate(state: TeamState) -> TeamState:
            item = _require_present(
                state.governance_agenda,
                normalized_item_id,
                "governance agenda item",
            )
            group = _require_present(
                state.governance_groups,
                item.governance_group_id,
                "governance group",
            )
            requester = _agent_for_session(state, session_id=normalized_session_id)
            if (
                requester.status != AgentStatus.ACTIVE
                or group.archived
                or group.manager_agent_id != requester.id
            ):
                raise ValueError(
                    "governance agenda handling requires the active governance "
                    "manager session for the agenda item's governance group"
                )
            item.state = GovernanceAgendaItemState.HANDLED
            item.updated_at = utc_now()
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        return state, state.governance_agenda[normalized_item_id]

    def create_operator_directive_command(
        self,
        *,
        session_id: str,
        team_id: str,
        message: str,
        request_id: str,
        governance_group_id: str | None = None,
        recipient_agent_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        title: str | None = None,
        source: str | None = None,
        source_message_id: str | None = None,
        created_by: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_message = _required_text(message, "message")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_group_id = _optional_text(governance_group_id)
        normalized_recipient_id = _optional_text(recipient_agent_id)
        normalized_project_id = _optional_text(project_id)
        normalized_task_id = _optional_text(task_id)
        normalized_title = _optional_text(title)
        normalized_source = _optional_text(source)
        normalized_source_message_id = _optional_text(source_message_id)
        normalized_created_by = _optional_text(created_by)
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_operator_directive",
            request_id=normalized_request_id,
            governance_group_id=normalized_group_id,
            wake_session_class="governance",
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "team_id": normalized_team_id,
            "message": normalized_message,
            "recipient_agent_id": normalized_recipient_id,
            "project_id": normalized_project_id,
            "task_id": normalized_task_id,
            "title": normalized_title,
            "source": normalized_source,
            "source_message_id": normalized_source_message_id,
            "created_by": normalized_created_by,
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            if resolved.agent is None or resolved.governance_group_id is None:
                raise TeamCommandValidationError(
                    code="invalid_governance_scope",
                    message="team_create_operator_directive requires a governance manager scope",
                )
            group = _require_present(
                state_model.governance_groups,
                resolved.governance_group_id,
                "governance group",
            )
            team = _require_present(state_model.teams, normalized_team_id, "team")
            if team.archived:
                raise TeamCommandValidationError(
                    code="team_archived",
                    message=f"team {team.id} is archived",
                    correction_context={"team_id": team.id},
                )
            if team.id not in group.member_team_ids:
                raise TeamCommandValidationError(
                    code="team_not_in_governance_group",
                    message=f"team {team.id} is not managed by governance group {group.id}",
                    correction_context={
                        "team_id": team.id,
                        "governance_group_id": group.id,
                    },
                )
            recipient_id = normalized_recipient_id or team.manager_agent_id
            if recipient_id != team.manager_agent_id:
                raise TeamCommandValidationError(
                    code="invalid_recipient",
                    message=(
                        "governance manager sessions can only enqueue operator "
                        "directives for the target team manager inbox"
                    ),
                    correction_context={
                        "team_id": team.id,
                        "recipient_agent_id": recipient_id,
                        "manager_agent_id": team.manager_agent_id,
                    },
                )
            recipient = _require_present(state_model.agents, recipient_id, "recipient agent")
            if recipient.team_id != team.id:
                raise TeamCommandValidationError(
                    code="recipient_team_mismatch",
                    message=f"recipient agent {recipient.id} is not in team {team.id}",
                    correction_context={
                        "team_id": team.id,
                        "recipient_agent_id": recipient.id,
                    },
                )
            if recipient.status != AgentStatus.ACTIVE:
                raise TeamCommandValidationError(
                    code="recipient_not_active",
                    message=f"recipient agent {recipient.id} is not active",
                    correction_context={
                        "recipient_agent_id": recipient.id,
                        "status": recipient.status.value,
                    },
                )

            effective_project_id = normalized_project_id
            if normalized_task_id is not None:
                task = _require_present(state_model.tasks, normalized_task_id, "task")
                project = _require_present(state_model.projects, task.project_id, "project")
                if project.team_id != team.id:
                    raise TeamCommandValidationError(
                        code="task_team_mismatch",
                        message=f"task {task.id} is not in team {team.id}",
                        correction_context={"task_id": task.id, "team_id": team.id},
                    )
                if effective_project_id is not None and effective_project_id != task.project_id:
                    raise TeamCommandValidationError(
                        code="task_project_mismatch",
                        message=(
                            f"task {task.id} belongs to project {task.project_id}, "
                            f"not project {effective_project_id}"
                        ),
                        correction_context={
                            "task_id": task.id,
                            "project_id": effective_project_id,
                            "task_project_id": task.project_id,
                        },
                    )
                effective_project_id = task.project_id
            if effective_project_id is not None:
                project = _require_present(
                    state_model.projects,
                    effective_project_id,
                    "project",
                )
                if project.team_id != team.id:
                    raise TeamCommandValidationError(
                        code="project_team_mismatch",
                        message=f"project {project.id} is not in team {team.id}",
                        correction_context={
                            "project_id": project.id,
                            "team_id": team.id,
                        },
                    )

            now = utc_now()
            payload: dict[str, Any] = {
                "title": normalized_title or "Operator directive",
                "message": normalized_message,
                "team_id": team.id,
                "requesting_session_id": normalized_session_id,
            }
            if effective_project_id is not None:
                payload["project_id"] = effective_project_id
            if normalized_source is not None:
                payload["source"] = normalized_source
            if normalized_source_message_id is not None:
                payload["source_message_id"] = normalized_source_message_id
            if normalized_created_by is not None:
                payload["created_by"] = normalized_created_by

            inbox_id = f"inbox-{uuid4().hex}"
            item = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient.id,
                sender_agent_id=None,
                task_id=normalized_task_id,
                item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
                response_requested=True,
                priority=TeamInboxPriority.NORMAL,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            state_model.inbox[inbox_id] = item
            _mark_agent_work_due(state_model, agent_id=recipient.id, now=now)
            return TeamCommandApplyResult(
                response_payload={
                    "inbox_item_id": item.id,
                    "recipient_agent_id": item.recipient_agent_id,
                    "team_id": team.id,
                    "project_id": effective_project_id,
                    "task_id": item.task_id,
                    "operator_directive": item.model_dump(mode="json"),
                    "governance_group_id": group.id,
                    "progress_made": True,
                },
                created_domain_ids=[item.id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=[],
            apply_command=_apply,
            require_active_dispatch=False,
        )


def _normalize_team_command_context(context: TeamCommandContext) -> TeamCommandContext:
    return TeamCommandContext(
        ambient_session_id=_required_text(
            context.ambient_session_id,
            "ambient_session_id",
        ),
        tool_name=_required_text(context.tool_name, "tool_name"),
        request_id=_optional_text(context.request_id),
        caller_agent_id=_optional_text(context.caller_agent_id),
        governance_group_id=_optional_text(context.governance_group_id),
        assignment_id=_optional_text(context.assignment_id),
        active_dispatch_id=_optional_text(context.active_dispatch_id),
        wake_session_class=_optional_text(context.wake_session_class) or "team",
        correlation_id=_optional_text(context.correlation_id),
        causation_id=_optional_text(context.causation_id),
        progress_classification=_optional_text(context.progress_classification) or "none",
    )


def _team_command_context_scope(context: TeamCommandContext) -> str:
    wake_class = _normalized_wake_session_class(context.wake_session_class)
    governance_scope = context.governance_group_id is not None or wake_class in {
        "governance",
        "governance_manager",
        "team_governance",
    }
    assignment_scope = context.assignment_id is not None or wake_class in {
        "ephemeral",
        "ephemeral_agent",
        "ephemeral_worker",
        "worker",
        "worker_assignment",
    }
    if governance_scope and assignment_scope:
        raise TeamCommandValidationError(
            code="ambiguous_command_scope",
            message="command context cannot be both governance and assignment scoped",
            correction_context={"wake_session_class": context.wake_session_class},
        )
    if governance_scope:
        return "governance"
    if assignment_scope:
        return "assignment"
    return "agent"


def _normalized_wake_session_class(value: object) -> str:
    text = _optional_text(value) or "team"
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _resolve_command_agent(
    state: TeamState,
    context: TeamCommandContext,
) -> PersistentAgent:
    if context.caller_agent_id is not None:
        agent = state.agents.get(context.caller_agent_id)
        if agent is None:
            raise TeamCommandValidationError(
                code="agent_not_found",
                message=f"agent {context.caller_agent_id} does not exist",
                correction_context={"agent_id": context.caller_agent_id},
            )
        if agent.session_id != context.ambient_session_id:
            raise TeamCommandValidationError(
                code="ambient_identity_conflict",
                message=(
                    f"ambient session {context.ambient_session_id} does not belong "
                    f"to agent {agent.id}"
                ),
                correction_context={
                    "agent_id": agent.id,
                    "ambient_session_id": context.ambient_session_id,
                    "agent_session_id": agent.session_id,
                },
            )
        return agent
    try:
        return _agent_for_session(state, session_id=context.ambient_session_id)
    except ValueError as exc:
        raise TeamCommandValidationError(
            code="unknown_session",
            message=str(exc),
            correction_context={"session_id": context.ambient_session_id},
        ) from None


def _resolve_command_governance_group(
    state: TeamState,
    context: TeamCommandContext,
    agent: PersistentAgent,
) -> tuple[GovernanceGroup | None, TeamCommandValidationError | None]:
    if context.governance_group_id is not None:
        group = state.governance_groups.get(context.governance_group_id)
        if group is None:
            return None, TeamCommandValidationError(
                code="governance_group_not_found",
                message=f"governance group {context.governance_group_id} does not exist",
                correction_context={"governance_group_id": context.governance_group_id},
            )
        if group.archived:
            return group, TeamCommandValidationError(
                code="governance_group_archived",
                message=f"governance group {group.id} is archived",
                correction_context={"governance_group_id": group.id},
            )
        if group.manager_agent_id != agent.id:
            return group, TeamCommandValidationError(
                code="governance_group_not_managed_by_caller",
                message=(f"agent {agent.id} is not the manager for governance group {group.id}"),
                correction_context={
                    "governance_group_id": group.id,
                    "manager_agent_id": group.manager_agent_id,
                    "caller_agent_id": agent.id,
                },
            )
        return group, None

    candidates = [
        group
        for group in state.governance_groups.values()
        if group.manager_agent_id == agent.id and not group.archived
    ]
    if context.active_dispatch_id is not None:
        candidates = [
            group
            for group in candidates
            if (runtime := state.governance_runtimes.get(group.id)) is not None
            and runtime.active_dispatch_id == context.active_dispatch_id
        ]
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, TeamCommandValidationError(
            code="governance_group_not_found",
            message="no active governance group is managed by the ambient session",
            correction_context={"caller_agent_id": agent.id},
        )
    return None, TeamCommandValidationError(
        code="governance_group_required",
        message="governance_group_id is required when the manager owns multiple groups",
        correction_context={
            "caller_agent_id": agent.id,
            "governance_group_ids": sorted(group.id for group in candidates),
        },
    )


def _resolve_command_assignment(
    state: TeamState,
    context: TeamCommandContext,
) -> tuple[EphemeralAssignment | None, TeamCommandValidationError | None]:
    explicit_assignment_id = context.assignment_id
    if explicit_assignment_id is not None:
        assignment = state.ephemeral_assignments.get(explicit_assignment_id)
        if assignment is None:
            return None, TeamCommandValidationError(
                code="assignment_not_found",
                message=f"ephemeral assignment {explicit_assignment_id} does not exist",
                correction_context={"assignment_id": explicit_assignment_id},
            )
        if assignment.session_id != context.ambient_session_id:
            return assignment, TeamCommandValidationError(
                code="assignment_session_mismatch",
                message=(
                    f"ambient session {context.ambient_session_id} does not match "
                    f"ephemeral assignment {assignment.id}"
                ),
                correction_context={
                    "assignment_id": assignment.id,
                    "assignment_session_id": assignment.session_id,
                    "ambient_session_id": context.ambient_session_id,
                },
            )
        return assignment, None

    matches = [
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.session_id == context.ambient_session_id
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, TeamCommandValidationError(
            code="unknown_worker_session",
            message="no ephemeral assignment matches the ambient worker session",
            correction_context={"session_id": context.ambient_session_id},
        )
    return None, TeamCommandValidationError(
        code="ambiguous_worker_session",
        message="multiple ephemeral assignments match the ambient worker session",
        correction_context={
            "session_id": context.ambient_session_id,
            "assignment_ids": sorted(assignment.id for assignment in matches),
        },
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


def _normalize_ephemeral_request_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return {
        "id": _optional_text(item.get("id")),
        "task_id": _optional_text(item.get("task_id")),
        "session_id": (
            _optional_text(item.get("worker_session_id")) or _optional_text(item.get("session_id"))
        ),
        "title": _optional_text(item.get("title")),
        "prompt": _optional_text(item.get("prompt")),
        "model": _optional_text(item.get("model")),
        "agent_provider": _optional_text(item.get("agent_provider")),
        "execution_backend": _optional_text(item.get("execution_backend")),
        "container_image": _optional_text(item.get("container_image")),
        "allowed_tools": _unique_text_list(item.get("allowed_tools") or [], "allowed_tools"),
        "ttl_hours": item.get("ttl_hours"),
        "directory_path": _optional_text(item.get("directory_path")),
    }


def _team_request_ephemeral_normalized_payload(
    *,
    assignments: Sequence[Mapping[str, Any]],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignments": [dict(item) for item in assignments],
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_accept_ephemeral_result_normalized_payload(
    *,
    assignment_id: str,
    deliverable_id: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _required_text(assignment_id, "assignment_id"),
        "deliverable_id": _optional_text(deliverable_id),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_reject_ephemeral_result_normalized_payload(
    *,
    assignment_id: str,
    rejection_reason: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _required_text(assignment_id, "assignment_id"),
        "rejection_reason": _optional_text(rejection_reason),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_report_ephemeral_result_normalized_payload(
    *,
    assignment_id: str | None,
    summary: str | None,
    artifact_refs: Sequence[str],
    agent_branch: str | None,
    commit_sha: str | None,
    error: str | None,
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _optional_text(assignment_id),
        "summary": _optional_text(summary),
        "artifact_refs": _string_list(artifact_refs, "artifact_refs"),
        "agent_branch": _optional_text(agent_branch),
        "commit_sha": _optional_text(commit_sha),
        "error": _optional_text(error),
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
    message_id: str,
    body: str,
    summary: str | None,
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "conversation_id": _required_text(conversation_id, "conversation_id"),
        "message_id": _required_text(message_id, "message_id"),
        "body": _optional_text(body) or "",
        "summary": _optional_text(summary),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_operator_chat_normalized_payload(
    *,
    recipient_operator_user_id: str,
    body: str,
    summary: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "recipient_operator_user_id": _normalize_operator_user_id(recipient_operator_user_id),
        "body": _optional_text(body) or "",
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


def _normalize_deliverable_submission_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(item, field_name)
    git_refs = _optional_mapping(payload.get("git_refs"), f"{field_name}.git_refs") or {}
    normalized = {
        "id": _optional_text(payload.get("id")),
        "task_id": _required_text(payload.get("task_id"), f"{field_name}.task_id"),
        "summary": _optional_text(payload.get("summary")) or "",
        "supersedes_deliverable_id": _optional_text(payload.get("supersedes_deliverable_id")),
        "artifact_paths": _string_list(
            payload.get("artifact_paths"),
            f"{field_name}.artifact_paths",
        ),
        "artifact_refs": _string_list(
            payload.get("artifact_refs"),
            f"{field_name}.artifact_refs",
        ),
        "artifact_space_ref": _optional_text(payload.get("artifact_space_ref")),
        "git_refs": {
            "agent_branch": _optional_text(git_refs.get("agent_branch")),
            "commit_sha": _optional_text(git_refs.get("commit_sha")),
        },
    }
    verification = _normalize_deliverable_verification_mapping(
        payload.get("verification"),
        field_name=f"{field_name}.verification",
    )
    if verification is not None:
        normalized["verification"] = verification
    return normalized


def _normalize_review_request_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(item, field_name)
    return {
        "id": _optional_text(payload.get("id")),
        "deliverable_id": _optional_text(payload.get("deliverable_id")),
        "reviewer_agent_id": _required_text(
            payload.get("reviewer_agent_id"),
            f"{field_name}.reviewer_agent_id",
        ),
        "review_profile_id": _optional_text(payload.get("review_profile_id")),
        "instructions": _optional_text(payload.get("instructions")),
    }


def _normalize_review_decision_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(item, field_name)
    criteria = _list_of_mappings(payload.get("criteria"), f"{field_name}.criteria")
    return {
        "review_request_id": _required_text(
            payload.get("review_request_id"),
            f"{field_name}.review_request_id",
        ),
        "state": _required_text(payload.get("state"), f"{field_name}.state"),
        "findings": _string_list(payload.get("findings"), f"{field_name}.findings"),
        "criteria": [
            {
                "criterion_id": _optional_text(criterion.get("criterion_id")),
                "state": _optional_text(criterion.get("state")),
                "finding": _optional_text(criterion.get("finding")),
            }
            for criterion in criteria
        ],
        "override_routing_gap_reason": _optional_text(payload.get("override_routing_gap_reason")),
    }


def _team_submit_deliverable_normalized_payload(
    *,
    deliverable: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "deliverable": dict(deliverable),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_submit_deliverable_for_review_normalized_payload(
    *,
    deliverable: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "deliverable": dict(deliverable),
        "reviews": [dict(item) for item in reviews],
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_request_review_normalized_payload(
    *,
    review: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "review": dict(review),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_decide_review_normalized_payload(
    *,
    decision: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "decision": dict(decision),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _command_deliverable_task_for_submission(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task_id: str,
) -> AgentTask:
    _validate_command_task_scope(state, agent=agent, task_id=task_id)
    task = _require_present(state.tasks, task_id, "task")
    if task.owner_agent_id != agent.id:
        raise TeamCommandValidationError(
            code="task_not_owned",
            message=f"agent {agent.id} cannot deliver task {task.id}",
            correction_context={
                "task_id": task.id,
                "task_owner_agent_id": task.owner_agent_id,
                "caller_agent_id": agent.id,
            },
        )
    return task


def _command_deliverable_id(
    state: TeamState,
    *,
    deliverable: Mapping[str, Any],
    request_id: str,
) -> str:
    explicit_id = _optional_text(deliverable.get("id"))
    if explicit_id is not None:
        return explicit_id
    return _generated_id(
        prefix="deliverable",
        labels=[deliverable.get("task_id"), request_id],
        existing_ids=state.deliverables,
    )


def _command_review_request_id(
    state: TeamState,
    *,
    deliverable_id: str,
    review_request: Mapping[str, Any],
    request_id: str,
    existing_ids: Mapping[str, Any] | set[str] | None = None,
) -> str:
    explicit_id = _optional_text(review_request.get("id"))
    if explicit_id is not None:
        return explicit_id
    return _generated_id(
        prefix="review",
        labels=[
            deliverable_id,
            review_request.get("reviewer_agent_id"),
            review_request.get("review_profile_id") or "review",
            request_id,
        ],
        existing_ids=existing_ids if existing_ids is not None else state.review_requests,
    )


def _plan_command_review_requests_for_deliverable(
    state: TeamState,
    *,
    actor: PersistentAgent,
    deliverable_id: str,
    task: AgentTask,
    reviews: Sequence[Mapping[str, Any]],
    request_id: str,
) -> list[dict[str, Any]]:
    reserved_review_ids: set[str] = set(state.review_requests)
    planned: list[dict[str, Any]] = []
    for review_request in reviews:
        reviewer = _require_command_agent_by_id(
            state,
            _required_text(
                review_request.get("reviewer_agent_id"),
                "reviewer_agent_id",
            ),
            field_name="reviewer_agent_id",
        )
        _validate_command_recipient_active(reviewer)
        _validate_command_same_team(actor, reviewer)
        profile_id = _optional_text(review_request.get("review_profile_id"))
        _profile, profile_error = _review_profile_for_request(
            state,
            profile_id=profile_id,
            expected_team_id=actor.team_id or "",
            project_id=task.project_id,
            reviewer=reviewer,
        )
        if profile_error is not None:
            raise TeamCommandValidationError(
                code="review_profile_invalid",
                message=profile_error,
                correction_context={
                    "deliverable_id": deliverable_id,
                    "reviewer_agent_id": reviewer.id,
                    "review_profile_id": profile_id,
                    "project_id": task.project_id,
                },
            )
        review_id = _command_review_request_id(
            state,
            deliverable_id=deliverable_id,
            review_request=review_request,
            request_id=request_id,
            existing_ids=reserved_review_ids,
        )
        if review_id in reserved_review_ids:
            raise TeamCommandValidationError(
                code="review_request_already_exists",
                message=f"review request {review_id} already exists",
                correction_context={"review_request_id": review_id},
            )
        reserved_review_ids.add(review_id)
        planned.append(
            {
                **dict(review_request),
                "id": review_id,
                "deliverable_id": deliverable_id,
            }
        )
    return planned


def _publish_command_deliverable_artifacts(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task: AgentTask,
    deliverable_id: str,
    dispatch_id: str,
    artifact_paths: Sequence[str],
    artifact_publisher: TeamDeliverableArtifactPublisher | None,
) -> list[dict[str, Any]]:
    del state
    safe_paths = _safe_deliverable_artifact_paths(artifact_paths)
    if not safe_paths:
        return []
    if artifact_publisher is None:
        raise TeamCommandValidationError(
            code="artifact_publisher_unavailable",
            message="artifact_paths were provided but no host artifact publisher is available",
            correction_context={
                "deliverable_id": deliverable_id,
                "artifact_paths": list(safe_paths),
            },
        )
    try:
        record = artifact_publisher(
            agent=agent,
            team_id=agent.team_id,
            project_id=task.project_id,
            deliverable_id=deliverable_id,
            dispatch_id=dispatch_id,
            artifact_paths=list(safe_paths),
            source_paths=list(safe_paths),
        )
    except Exception as exc:
        raise TeamCommandValidationError(
            code="artifact_publication_failed",
            message=f"failed to publish artifact_paths: {exc}",
            correction_context={
                "deliverable_id": deliverable_id,
                "artifact_paths": list(safe_paths),
            },
        ) from None
    record_payload = dict(record)
    artifact_ref = _optional_text(record_payload.get("artifact_ref"))
    if artifact_ref is None:
        raise TeamCommandValidationError(
            code="artifact_publication_failed",
            message="artifact publisher did not return artifact_ref",
            correction_context={
                "deliverable_id": deliverable_id,
                "artifact_paths": list(safe_paths),
                "published_artifact": record_payload,
            },
        )
    return [record_payload]


def _validate_command_deliverable_submission_pre_publication(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task: AgentTask,
    deliverable: Mapping[str, Any],
    deliverable_id: str,
) -> None:
    if deliverable_id in state.deliverables:
        raise TeamCommandValidationError(
            code="deliverable_already_exists",
            message=f"deliverable {deliverable_id} already exists",
            correction_context={"deliverable_id": deliverable_id},
        )
    _required_command_text(
        deliverable.get("summary"),
        field_name="summary",
        code="empty_summary",
    )
    supersedes_deliverable_id = _optional_text(deliverable.get("supersedes_deliverable_id"))
    if deliverable_id == supersedes_deliverable_id:
        raise TeamCommandValidationError(
            code="invalid_supersession",
            message="deliverable id cannot match supersedes_deliverable_id",
            correction_context={
                "deliverable_id": deliverable_id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        )
    try:
        _superseded_deliverable_for_rework(
            state,
            task=task,
            producer_agent_id=agent.id,
            supersedes_deliverable_id=supersedes_deliverable_id,
        )
        _deliverable_git_refs_from_payload(deliverable.get("git_refs"))
        _deliverable_verification_from_payload(deliverable.get("verification"))
    except ValueError as exc:
        raise TeamCommandValidationError(
            code="deliverable_validation_failed",
            message=str(exc),
            correction_context={
                "deliverable_id": deliverable_id,
                "task_id": task.id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        ) from None


def _safe_deliverable_artifact_paths(paths: Sequence[str]) -> list[str]:
    normalized = _unique_text_list(paths, "artifact_paths")
    for value in normalized:
        text = value.replace("\\", "/")
        path = PurePosixPath(text)
        if (
            not text
            or "\x00" in text
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise TeamCommandValidationError(
                code="unsafe_artifact_path",
                message=(
                    "artifact_paths entries must be relative to /artifacts and "
                    "must not contain dot segments"
                ),
                correction_context={"artifact_path": value},
            )
    return [value.replace("\\", "/") for value in normalized]


def _apply_command_deliverable_submission(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task: AgentTask,
    deliverable: Mapping[str, Any],
    deliverable_id: str,
    published_records: Sequence[Mapping[str, Any]],
    suppress_superior_notice: bool,
) -> list[str]:
    if deliverable_id in state.deliverables:
        raise TeamCommandValidationError(
            code="deliverable_already_exists",
            message=f"deliverable {deliverable_id} already exists",
            correction_context={"deliverable_id": deliverable_id},
        )
    summary = _required_command_text(
        deliverable.get("summary"),
        field_name="summary",
        code="empty_summary",
    )
    supersedes_deliverable_id = _optional_text(deliverable.get("supersedes_deliverable_id"))
    if deliverable_id == supersedes_deliverable_id:
        raise TeamCommandValidationError(
            code="invalid_supersession",
            message="deliverable id cannot match supersedes_deliverable_id",
            correction_context={
                "deliverable_id": deliverable_id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        )
    try:
        superseded_deliverable = _superseded_deliverable_for_rework(
            state,
            task=task,
            producer_agent_id=agent.id,
            supersedes_deliverable_id=supersedes_deliverable_id,
        )
        git_refs = _deliverable_git_refs_from_payload(deliverable.get("git_refs"))
        verification = _deliverable_verification_from_payload(deliverable.get("verification"))
    except ValueError as exc:
        raise TeamCommandValidationError(
            code="deliverable_validation_failed",
            message=str(exc),
            correction_context={
                "deliverable_id": deliverable_id,
                "task_id": task.id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        ) from None
    artifact_refs = _command_deliverable_artifact_refs(
        deliverable,
        published_records=published_records,
    )
    new_deliverable = Deliverable(
        id=deliverable_id,
        task_id=task.id,
        producer_agent_id=agent.id,
        summary=summary,
        artifact_refs=artifact_refs,
        git_refs=git_refs,
        verification=verification,
        state=TeamRecordState.PENDING,
        supersedes_deliverable_id=supersedes_deliverable_id,
    )
    state.deliverables[new_deliverable.id] = new_deliverable
    if superseded_deliverable is not None:
        superseded_deliverable.superseded_by_deliverable_id = new_deliverable.id
    task.deliverable_ids = _append_unique(task.deliverable_ids, new_deliverable.id)
    now = utc_now()
    _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
    task.updated_at = now
    _recalculate_project_status(state, task.project_id)
    created_inbox_ids: list[str] = []
    if not suppress_superior_notice and agent.reports_to_agent_id is not None:
        inbox_id = f"inbox-{uuid4().hex}"
        payload = {
            "deliverable_id": new_deliverable.id,
            "summary": new_deliverable.summary,
            "artifact_refs": list(new_deliverable.artifact_refs),
        }
        verification_payload = _deliverable_verification_payload(new_deliverable)
        if verification_payload is not None:
            payload["verification"] = verification_payload
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=agent.reports_to_agent_id,
            sender_agent_id=agent.id,
            task_id=task.id,
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            response_requested=True,
            priority=TeamInboxPriority.LOW,
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        created_inbox_ids.append(inbox_id)
        _mark_agent_work_due(state, agent_id=agent.reports_to_agent_id, now=now)
    return created_inbox_ids


def _command_deliverable_artifact_refs(
    deliverable: Mapping[str, Any],
    *,
    published_records: Sequence[Mapping[str, Any]],
) -> list[str]:
    refs = _string_list(deliverable.get("artifact_refs"), "artifact_refs")
    artifact_space_ref = _optional_text(deliverable.get("artifact_space_ref"))
    if artifact_space_ref is not None:
        refs = _append_unique(refs, artifact_space_ref)
    for record in published_records:
        refs = _append_unique(
            refs,
            _required_text(record.get("artifact_ref"), "artifact_ref"),
        )
    return refs


def _review_decision_correction_context(
    state: TeamState,
    *,
    review: ReviewRequest | None,
    reviewer: PersistentAgent | None,
) -> dict[str, Any]:
    deliverable = state.deliverables.get(review.deliverable_id) if review is not None else None
    task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
    return {
        "review": review.model_dump(mode="json") if review is not None else None,
        "deliverable": (deliverable.model_dump(mode="json") if deliverable is not None else None),
        "task": (task_payload_with_derived(state, task) if task is not None else None),
        "reviewer": (
            reviewer.model_dump(mode="json", exclude=AGENT_OPERATOR_ONLY_FIELDS)
            if reviewer is not None
            else None
        ),
    }


def _team_create_case_normalized_payload(
    *,
    case_id: str | None,
    project_id: str | None,
    title: str,
    description: str,
    status: str,
    priority: str,
    owner_agent_id: str | None,
    source: str | None,
    source_ref: str | None,
    tags: Sequence[str],
    task_ids: Sequence[str],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "id": _optional_text(case_id),
        "project_id": _optional_text(project_id),
        "title": _optional_text(title) or "",
        "description": _optional_text(description) or "",
        "status": _optional_text(status) or TeamCaseStatus.OPEN.value,
        "priority": _optional_text(priority) or TeamInboxPriority.NORMAL.value,
        "owner_agent_id": _optional_text(owner_agent_id),
        "source": _optional_text(source),
        "source_ref": _optional_text(source_ref),
        "tags": sorted(_unique_text_list(tags, "tags")),
        "task_ids": sorted(_unique_text_list(task_ids, "task_ids")),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _normalize_case_update_command_fields(**fields: object) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, value in fields.items():
        if value is _UNSET:
            continue
        if name in {"project_id", "owner_agent_id", "source", "source_ref"}:
            normalized[name] = _optional_text(value)
        elif name in {"title", "description", "status", "priority"}:
            normalized[name] = _optional_text(value)
        elif name in {"tags", "task_ids"}:
            normalized[name] = _unique_text_list(value, name)
    return normalized


def _case_command_fingerprint(state: TeamState, case_id: str) -> dict[str, Any]:
    case = state.cases.get(case_id)
    if case is None:
        return {}
    case_payload = case.model_dump(mode="json")
    case_payload.pop("updated_at", None)
    task_ids = set(case.task_ids)
    task_links = [
        {"task_id": task.id, "case_id": task.case_id}
        for task in state.tasks.values()
        if task.id in task_ids or task.case_id == case.id
    ]
    return {
        "case": case_payload,
        "task_links": sorted(task_links, key=lambda item: item["task_id"]),
    }


def _team_case_status_value(
    value: TeamCaseStatus | str | None,
    *,
    default: TeamCaseStatus,
) -> str:
    raw = value.value if isinstance(value, TeamCaseStatus) else value
    return _optional_text(raw) or default.value


def _team_case_priority_value(
    value: TeamInboxPriority | str | None,
    *,
    default: TeamInboxPriority,
) -> str:
    raw = value.value if isinstance(value, TeamInboxPriority) else value
    return _optional_text(raw) or default.value


def _normalize_task_creation_command_payload(
    *,
    task_id: str | None,
    owner_agent_id: str,
    project_id: str,
    case_id: str | None,
    priority: int,
    title: str,
    description: str,
    state: TeamTaskState | str,
    parent_task_id: str | None,
    dependency_task_ids: Sequence[str] | None,
) -> dict[str, Any]:
    return _normalize_task_creation_mapping(
        {
            "task_id": task_id,
            "owner_agent_id": owner_agent_id,
            "project_id": project_id,
            "case_id": case_id,
            "priority": priority,
            "title": title,
            "description": description,
            "state": state.value if isinstance(state, TeamTaskState) else state,
            "parent_task_id": parent_task_id,
            "dependency_task_ids": list(dependency_task_ids or []),
        },
        field_name="task",
    )


def _normalize_task_creation_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return {
        "task_id": _optional_text(item.get("task_id")),
        "owner_agent_id": _optional_text(item.get("owner_agent_id")),
        "project_id": _optional_text(item.get("project_id")),
        "case_id": _optional_text(item.get("case_id")),
        "priority": item.get("priority", 0),
        "title": _optional_text(item.get("title")),
        "description": _optional_text(item.get("description")),
        "state": _optional_text(item.get("state")) or TeamTaskState.PENDING.value,
        "parent_task_id": _optional_text(item.get("parent_task_id")),
        "dependency_task_ids": _unique_text_list(
            item.get("dependency_task_ids") or [],
            "dependency_task_ids",
        ),
    }


def _team_create_task_normalized_payload(
    *,
    creation: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "task": dict(creation),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _command_plan_task_creation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    creation: dict[str, Any],
    reserved_task_ids: set[str],
) -> _TaskCreationPlan:
    result: dict[str, Any] = {"errors": []}
    try:
        plan = _plan_task_creation(
            state,
            actor=actor,
            creation=creation,
            result=result,
            reserved_task_ids=reserved_task_ids,
        )
    except ValueError as exc:
        raise _team_command_validation_error_from_exception(
            exc,
            code="task_creation_failed",
            correction_context={"task": dict(creation)},
        ) from exc
    if result["errors"] or plan is None:
        raise TeamCommandValidationError(
            code="task_creation_failed",
            message="; ".join(result["errors"]) or "task creation failed",
            correction_context={"task": dict(creation)},
        )
    return plan


def _normalize_task_update_command_fields(**fields: object) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, value in fields.items():
        if value is _UNSET:
            continue
        if name == "state":
            normalized[name] = _optional_text(
                value.value if isinstance(value, TeamTaskState) else value
            )
        elif name == "priority":
            normalized[name] = value
        elif name in {"title", "description", "owner_agent_id"}:
            normalized[name] = _optional_text(value)
        elif name == "dependency_task_ids":
            normalized[name] = _unique_text_list(value, "dependency_task_ids")
    return normalized


def _team_record_state_value(value: TeamRecordState | str) -> str:
    raw = value.value if isinstance(value, TeamRecordState) else value
    return _optional_text(raw) or ""


def _team_command_validation_error_from_exception(
    exc: Exception,
    *,
    code: str,
    correction_context: Mapping[str, Any] | None = None,
) -> TeamCommandValidationError:
    return TeamCommandValidationError(
        code=code,
        message=str(exc),
        correction_context=correction_context,
    )


def _task_graph_correction_context(
    state: TeamState,
    *,
    project_id: str | None,
    task_id: str | None,
    parent_task_id: str | None,
    dependency_task_ids: Sequence[str],
) -> dict[str, Any]:
    referenced_task_ids = {
        item for item in [task_id, parent_task_id, *list(dependency_task_ids)] if item is not None
    }
    return {
        "task_id": task_id,
        "project_id": project_id,
        "parent_task_id": parent_task_id,
        "dependency_task_ids": list(dependency_task_ids),
        "current_tasks": _task_graph_current_tasks(
            state,
            project_ids={project_id} if project_id is not None else set(),
            referenced_task_ids=referenced_task_ids,
        ),
    }


def _task_graph_correction_context_for_plans(
    state: TeamState,
    plans: Sequence[_TaskCreationPlan],
    *,
    graph_errors: Mapping[str, str],
) -> dict[str, Any]:
    project_ids = {plan.project_id for plan in plans}
    referenced_task_ids: set[str] = set()
    planned_tasks: list[dict[str, Any]] = []
    for plan in plans:
        referenced_task_ids.add(plan.task_id)
        if plan.parent_task_id is not None:
            referenced_task_ids.add(plan.parent_task_id)
        referenced_task_ids.update(plan.dependency_task_ids)
        planned_tasks.append(
            {
                "task_id": plan.task_id,
                "project_id": plan.project_id,
                "owner_agent_id": plan.owner_id,
                "parent_task_id": plan.parent_task_id,
                "dependency_task_ids": list(plan.dependency_task_ids),
                "state": plan.task_state.value,
            }
        )
    return {
        "graph_errors": dict(graph_errors),
        "planned_tasks": sorted(planned_tasks, key=lambda item: item["task_id"]),
        "current_tasks": _task_graph_current_tasks(
            state,
            project_ids=project_ids,
            referenced_task_ids=referenced_task_ids,
        ),
    }


def _task_graph_current_tasks(
    state: TeamState,
    *,
    project_ids: set[str],
    referenced_task_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in state.tasks.values():
        if task.project_id not in project_ids and task.id not in referenced_task_ids:
            continue
        rows.append(
            {
                "task_id": task.id,
                "project_id": task.project_id,
                "owner_agent_id": task.owner_agent_id,
                "parent_task_id": task.parent_task_id,
                "dependency_task_ids": list(task.dependency_task_ids),
                "state": task.state.value,
            }
        )
    return sorted(rows, key=lambda item: item["task_id"])


def _team_publish_review_branch_normalized_payload(
    *,
    assignment_id: str | None,
    deliverable_id: str | None,
    source_branch: str | None,
    commit_sha: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _optional_text(assignment_id),
        "deliverable_id": _optional_text(deliverable_id),
        "source_branch": _optional_text(source_branch),
        "commit_sha": _optional_text(commit_sha),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_update_feature_branch_normalized_payload(
    *,
    task_id: str,
    source_review_branches: Sequence[str],
    feature_branch: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "task_id": _required_text(task_id, "task_id"),
        "source_review_branches": _string_list(
            source_review_branches,
            "source_review_branches",
        ),
        "feature_branch": _optional_text(feature_branch),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_request_release_normalized_payload(
    *,
    release_id: str | None,
    project_id: str,
    deliverable_ids: Sequence[str],
    release_branch: str | None,
    canonical_branch: str | None,
    summary: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "id": _optional_text(release_id),
        "project_id": _required_text(project_id, "project_id"),
        "deliverable_ids": _string_list(deliverable_ids, "deliverable_ids"),
        "release_branch": _optional_text(release_branch),
        "canonical_branch": _optional_text(canonical_branch),
        "summary": _optional_text(summary),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_recover_release_normalized_payload(
    *,
    release_package_id: str,
    agent_branch: str,
    commit_sha: str,
    summary: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "release_package_id": _required_text(
            release_package_id,
            "release_package_id",
        ),
        "agent_branch": _required_text(agent_branch, "agent_branch"),
        "commit_sha": _required_text(commit_sha, "commit_sha"),
        "summary": _optional_text(summary),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _empty_git_command_result() -> dict[str, Any]:
    return {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
        "release_package_ids": [],
        "release_recovery_event_ids": [],
        "created_deliverable_ids": [],
        "updated_deliverable_ids": [],
    }


def _git_command_validation_error(
    result: Mapping[str, Any],
    *,
    code: str,
) -> TeamCommandValidationError:
    errors = result.get("errors")
    if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
        messages = [str(item) for item in errors if str(item)]
    else:
        messages = [str(errors)] if errors else []
    message = "; ".join(messages) or "Git intent validation failed"
    return TeamCommandValidationError(
        code=code,
        message=message,
        correction_context={"errors": messages},
    )


def _team_event_outbox_records(
    events: Sequence[Mapping[str, Any]],
) -> list[TeamOutboxRecord]:
    records: list[TeamOutboxRecord] = []
    now = utc_now()
    for event in events:
        event_type = _required_text(event.get("event_type"), "event_type")
        payload = event.get("payload")
        payload_mapping = dict(payload) if isinstance(payload, Mapping) else {}
        aggregate_type = "team_event"
        aggregate_id = _optional_text(event.get("correlation_key"))
        if event_type == GIT_PROMOTION_EVENT_TYPE:
            aggregate_type = "git_promotion"
            aggregate_id = _optional_text(payload_mapping.get("promotion_id")) or aggregate_id
        elif event_type == GIT_RELEASE_RECOVERY_EVENT_TYPE:
            aggregate_type = "git_release_recovery"
            aggregate_id = _optional_text(payload_mapping.get("release_package_id")) or aggregate_id
        elif event_type in {
            EPHEMERAL_REQUESTED_EVENT_TYPE,
            EPHEMERAL_COMPLETED_EVENT_TYPE,
        }:
            aggregate_type = "ephemeral_assignment"
            aggregate_id = _optional_text(payload_mapping.get("assignment_id")) or aggregate_id
        outbox_payload: dict[str, Any] = {
            "event_type": event_type,
            "correlation_key": _optional_text(event.get("correlation_key")),
            "payload": payload_mapping,
        }
        if "delay_seconds" in event:
            outbox_payload["delay_seconds"] = event.get("delay_seconds")
        records.append(
            TeamOutboxRecord(
                id=f"outbox-{uuid4().hex}",
                intent_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                idempotency_key=_optional_text(event.get("idempotency_key")),
                payload=outbox_payload,
                created_at=now,
                updated_at=now,
            )
        )
    return records


def _team_command_outbox_records(
    apply_result: TeamCommandApplyResult,
) -> list[TeamOutboxRecord]:
    records = [TeamOutboxRecord.model_validate(record) for record in apply_result.outbox_records]
    events = _response_event_mappings(apply_result.response_payload)
    if not events:
        return records
    seen = {_outbox_record_dedupe_key(record) for record in records}
    for record in _team_event_outbox_records(events):
        key = _outbox_record_dedupe_key(record)
        if key in seen:
            continue
        records.append(record)
        seen.add(key)
    return records


def _response_event_mappings(response_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    events = response_payload.get("events")
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes, bytearray)):
        return []
    return [event for event in events if isinstance(event, Mapping)]


def _outbox_record_dedupe_key(record: TeamOutboxRecord) -> tuple[Any, ...]:
    if record.idempotency_key is not None:
        return ("idempotency", record.idempotency_key)
    return (
        "aggregate",
        record.intent_type,
        record.aggregate_type,
        record.aggregate_id,
    )


def _append_response_outbox_record_ids(
    response: dict[str, Any],
    outbox_records: Sequence[TeamOutboxRecord],
) -> None:
    if not outbox_records:
        return
    raw_ids = response.get("outbox_record_ids")
    if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes, bytearray)):
        outbox_ids = [str(item) for item in raw_ids]
    else:
        outbox_ids = []
    for record in outbox_records:
        if record.id not in outbox_ids:
            outbox_ids.append(record.id)
    response["outbox_record_ids"] = outbox_ids


def _release_command_next_action_hints(
    state: TeamState,
    release: ReleasePackage,
    *,
    promotion_event_ids: Sequence[str],
) -> list[str]:
    if not promotion_event_ids:
        return [
            "Release package recorded without pending Git promotion effects; inspect artifact_refs and package state before publishing externally."
        ]
    promotion_kinds = {
        state.git_promotions[promotion_id].promotion_kind
        for promotion_id in promotion_event_ids
        if promotion_id in state.git_promotions
    }
    if "feature" in promotion_kinds:
        return [
            "Prerequisite feature promotion intent recorded; release promotion will be queued after the async feature promotion succeeds."
        ]
    if "release" in promotion_kinds:
        return [
            "Release promotion intent recorded; wait for async Git promotion result before expecting canonical publication."
        ]
    return [
        "Git promotion intent recorded; wait for async effect completion before taking the next release step."
    ]


def _team_command_priority(
    value: TeamInboxPriority | str | None,
    *,
    field_name: str,
    default: TeamInboxPriority,
) -> TeamInboxPriority:
    if value is None:
        return default
    if isinstance(value, TeamInboxPriority):
        return value
    try:
        return TeamInboxPriority(str(value or "").strip())
    except ValueError:
        allowed = ", ".join(priority.value for priority in TeamInboxPriority)
        raise ValueError(f"{field_name} must be one of: {allowed}") from None


def _require_command_agent(
    resolved: _ResolvedTeamCommandContext,
    tool_name: str,
) -> PersistentAgent:
    if resolved.agent is None or resolved.dispatch_id is None:
        raise TeamCommandValidationError(
            code="invalid_command_scope",
            message=f"{tool_name} requires an active agent dispatch",
        )
    return resolved.agent


def _required_command_text(
    value: object,
    *,
    field_name: str,
    code: str,
) -> str:
    text = _optional_text(value)
    if text is None:
        raise TeamCommandValidationError(
            code=code,
            message=f"{field_name} must be a non-empty meaningful string",
            correction_context={"field": field_name},
        )
    return text


def _require_parent_assignment_owner(
    assignment: EphemeralAssignment,
    *,
    agent: PersistentAgent,
    tool_name: str,
) -> None:
    if assignment.parent_agent_id != agent.id:
        raise TeamCommandValidationError(
            code="ephemeral_assignment_not_owned",
            message=(
                f"{tool_name} cannot mutate ephemeral assignment {assignment.id} "
                f"owned by agent {assignment.parent_agent_id}"
            ),
            correction_context={
                "assignment_id": assignment.id,
                "parent_agent_id": assignment.parent_agent_id,
                "caller_agent_id": agent.id,
            },
        )


def _validate_ephemeral_result_report(
    assignment: EphemeralAssignment,
    *,
    summary: str | None,
    agent_branch: str | None,
    error: str | None,
) -> None:
    if assignment.state not in {
        EphemeralAssignmentState.REQUESTED,
        EphemeralAssignmentState.SPAWN_PENDING,
        EphemeralAssignmentState.RUNNING,
    }:
        raise TeamCommandValidationError(
            code="ephemeral_assignment_already_reported",
            message=(f"ephemeral assignment {assignment.id} is already {assignment.state.value}"),
            correction_context={
                "assignment_id": assignment.id,
                "state": assignment.state.value,
            },
        )
    if error is None and _optional_text(summary) is None:
        raise TeamCommandValidationError(
            code="summary_required",
            message="summary is required for successful ephemeral results",
            correction_context={"assignment_id": assignment.id, "field": "summary"},
        )
    if agent_branch is not None:
        expected_branch = ephemeral_agent_branch(assignment.session_id, assignment.id)
        if agent_branch != expected_branch:
            raise TeamCommandValidationError(
                code="agent_branch_mismatch",
                message=(
                    f"agent_branch {agent_branch!r} does not match expected "
                    f"ephemeral branch {expected_branch!r}"
                ),
                correction_context={
                    "assignment_id": assignment.id,
                    "agent_branch": agent_branch,
                    "expected_agent_branch": expected_branch,
                },
            )


def _require_command_agent_by_id(
    state: TeamState,
    agent_id: str,
    *,
    field_name: str,
) -> PersistentAgent:
    agent = state.agents.get(agent_id)
    if agent is None:
        raise TeamCommandValidationError(
            code="agent_not_found",
            message=f"agent {agent_id} does not exist",
            correction_context={field_name: agent_id},
        )
    return agent


def _validate_command_recipient_active(agent: PersistentAgent) -> None:
    if agent.status != AgentStatus.ACTIVE:
        raise TeamCommandValidationError(
            code="recipient_not_active",
            message=f"recipient agent {agent.id} is {agent.status.value}",
            correction_context={"recipient_agent_id": agent.id, "status": agent.status.value},
        )


def _validate_command_same_team(actor: PersistentAgent, recipient: PersistentAgent) -> None:
    if recipient.team_id != actor.team_id:
        raise TeamCommandValidationError(
            code="recipient_outside_team",
            message=f"recipient agent {recipient.id} is not in team {actor.team_id}",
            correction_context={
                "caller_agent_id": actor.id,
                "caller_team_id": actor.team_id,
                "recipient_agent_id": recipient.id,
                "recipient_team_id": recipient.team_id,
            },
        )


def _resolve_upward_command_recipient(
    state: TeamState,
    *,
    agent: PersistentAgent,
    recipient_agent_id: str | None,
    tool_name: str,
    communication_kind: str,
) -> PersistentAgent:
    if agent.reports_to_agent_id is None:
        raise TeamCommandValidationError(
            code="top_level_manager_requires_operator_escalation",
            message=(
                f"agent {agent.id} is a top-level manager and must use "
                "team_escalate_operator for operator escalation"
            ),
            correction_context={
                "caller_agent_id": agent.id,
                "recommended_tool": "team_escalate_operator",
                "tool_name": tool_name,
            },
        )
    effective_recipient_id = recipient_agent_id or agent.reports_to_agent_id
    if effective_recipient_id != agent.reports_to_agent_id:
        raise TeamCommandValidationError(
            code="recipient_not_direct_superior",
            message=(
                f"agent {agent.id} can only send {communication_kind} to its "
                f"direct superior {agent.reports_to_agent_id}"
            ),
            correction_context={
                "caller_agent_id": agent.id,
                "recipient_agent_id": effective_recipient_id,
                "direct_superior_agent_id": agent.reports_to_agent_id,
            },
        )
    recipient = _require_command_agent_by_id(
        state,
        effective_recipient_id,
        field_name="recipient_agent_id",
    )
    _validate_command_recipient_active(recipient)
    _validate_command_same_team(agent, recipient)
    return recipient


def _validate_command_project_scope(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
) -> None:
    if project_id is None:
        return
    project = state.projects.get(project_id)
    if project is None:
        raise TeamCommandValidationError(
            code="project_not_found",
            message=f"project {project_id} does not exist",
            correction_context={"project_id": project_id},
        )
    if project.team_id != team_id:
        raise TeamCommandValidationError(
            code="project_outside_team",
            message=f"project {project.id} is not in team {team_id}",
            correction_context={
                "project_id": project.id,
                "project_team_id": project.team_id,
                "caller_team_id": team_id,
            },
        )


def _validate_command_task_scope(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task_id: str | None,
) -> None:
    if task_id is None:
        return
    task = state.tasks.get(task_id)
    if task is None:
        raise TeamCommandValidationError(
            code="task_not_found",
            message=f"task {task_id} does not exist",
            correction_context={"task_id": task_id},
        )
    project = state.projects.get(task.project_id)
    if project is None:
        raise TeamCommandValidationError(
            code="task_project_not_found",
            message=f"task {task.id} references missing project {task.project_id}",
            correction_context={"task_id": task.id, "project_id": task.project_id},
        )
    if project.team_id != agent.team_id:
        raise TeamCommandValidationError(
            code="task_outside_team",
            message=f"task {task.id} is not in team {agent.team_id}",
            correction_context={
                "task_id": task.id,
                "project_id": project.id,
                "project_team_id": project.team_id,
                "caller_team_id": agent.team_id,
            },
        )


def _require_command_conversation(
    state: TeamState,
    *,
    conversation_id: str,
    agent: PersistentAgent,
) -> TeamConversation:
    conversation = state.conversations.get(conversation_id)
    if conversation is None:
        raise TeamCommandValidationError(
            code="conversation_not_found",
            message=f"conversation {conversation_id} does not exist",
            correction_context={"conversation_id": conversation_id},
        )
    if conversation.agent_id != agent.id:
        raise TeamCommandValidationError(
            code="conversation_not_owned",
            message=f"agent {agent.id} cannot reply to conversation {conversation.id}",
            correction_context={
                "conversation_id": conversation.id,
                "conversation_agent_id": conversation.agent_id,
                "caller_agent_id": agent.id,
            },
        )
    return conversation


def _require_command_chat_message(
    state: TeamState,
    *,
    message_id: str,
    conversation_id: str,
) -> TeamConversationMessage:
    message = state.conversation_messages.get(message_id)
    if message is None:
        raise TeamCommandValidationError(
            code="chat_message_not_found",
            message=f"conversation message {message_id} does not exist",
            correction_context={"message_id": message_id},
        )
    if message.conversation_id != conversation_id:
        raise TeamCommandValidationError(
            code="chat_message_wrong_conversation",
            message=f"message {message.id} is not in conversation {conversation_id}",
            correction_context={
                "message_id": message.id,
                "message_conversation_id": message.conversation_id,
                "conversation_id": conversation_id,
            },
        )
    return message


def _command_success_response(
    *,
    state: TeamState,
    context: TeamCommandContext,
    activity_id: str,
    handled_inbox_item_ids: Sequence[str],
    apply_result: TeamCommandApplyResult,
) -> dict[str, Any]:
    response = dict(apply_result.response_payload)
    progress_made = bool(response.get("progress_made", False))
    response.update(
        {
            "success": True,
            "applied": True,
            "progress_made": progress_made,
            "revision": state.revision,
            "activity_id": activity_id,
            "tool_name": context.tool_name,
            "action_record_id": _optional_text(response.get("action_record_id")),
            "handled_inbox_item_ids": list(handled_inbox_item_ids),
            "errors": [],
        }
    )
    return response


def _command_idempotency_conflict_response(
    *,
    state: TeamState,
    conflict: TeamIdempotencyConflictError,
) -> dict[str, Any]:
    return {
        "success": False,
        "applied": False,
        "progress_made": False,
        "revision": state.revision,
        "activity_id": None,
        "action_record_id": None,
        "errors": [
            {
                "code": "idempotency_conflict",
                "message": str(conflict),
                "existing_digest": conflict.existing_digest,
                "attempted_digest": conflict.attempted_digest,
            }
        ],
    }


def _command_correction_context_summary(
    correction_context: Mapping[str, Any],
) -> str | None:
    if not correction_context:
        return None
    summary = "; ".join(
        f"{key}={value}" for key, value in sorted(correction_context.items()) if value is not None
    )
    return summary[:240] if summary else None
