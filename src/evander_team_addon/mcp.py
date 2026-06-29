from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.addons import AddonContext
from agent_sandbox.events.internal import (
    InternalEventBus,
    InternalEventSource,
    internal_event_store_path,
)
from agent_sandbox.events.scheduled import (
    ScheduledInternalEventStore,
    scheduled_internal_event_store_path,
)
from agent_sandbox.effects.providers.base import EffectRuntimeContext

from .mcp_context import (
    TeamMcpContextResolver,
    team_mcp_failure,
    team_mcp_success,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.domain_helpers import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
)
from .services.command_service import TeamCommandService

TEAM_RECORD_NOTE_TOOL = "team_record_note"
TEAM_MARK_INBOX_HANDLED_TOOL = "team_mark_inbox_handled"
TEAM_REQUEST_STATUS_TOOL = "team_request_status"
TEAM_ASK_QUESTION_TOOL = "team_ask_question"
TEAM_SEND_PROGRESS_REPORT_TOOL = "team_send_progress_report"
TEAM_REPLY_TO_CHAT_TOOL = "team_reply_to_chat"
TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL = "team_send_operator_chat_message"
TEAM_ESCALATE_OPERATOR_TOOL = "team_escalate_operator"
TEAM_CREATE_CASE_TOOL = "team_create_case"
TEAM_UPDATE_CASE_TOOL = "team_update_case"
TEAM_CREATE_TASK_TOOL = "team_create_task"
TEAM_CREATE_TASKS_TOOL = "team_create_tasks"
TEAM_UPDATE_TASK_TOOL = "team_update_task"
TEAM_UPDATE_GOAL_TOOL = "team_update_goal"
TEAM_STAND_DOWN_AGENT_TOOL = "team_stand_down_agent"
TEAM_REVOKE_STAND_DOWN_TOOL = "team_revoke_stand_down"
TEAM_SUBMIT_DELIVERABLE_TOOL = "team_submit_deliverable"
TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL = "team_submit_deliverable_for_review"
TEAM_REQUEST_REVIEW_TOOL = "team_request_review"
TEAM_DECIDE_REVIEW_TOOL = "team_decide_review"
TEAM_PUBLISH_REVIEW_BRANCH_TOOL = "team_publish_review_branch"
TEAM_UPDATE_FEATURE_BRANCH_TOOL = "team_update_feature_branch"
TEAM_REQUEST_RELEASE_TOOL = "team_request_release"
TEAM_RECOVER_RELEASE_TOOL = "team_recover_release"
TEAM_REQUEST_EPHEMERAL_AGENT_TOOL = "team_request_ephemeral_agent"
TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL = "team_request_ephemeral_agents"
TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL = "team_accept_ephemeral_result"
TEAM_REJECT_EPHEMERAL_RESULT_TOOL = "team_reject_ephemeral_result"
TEAM_REPORT_EPHEMERAL_RESULT_TOOL = "team_report_ephemeral_result"
TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL = "team_create_operator_directive"
TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL = "team_mark_governance_agenda_handled"

ORDINARY_TEAM_MCP_TOOL_NAMES = (
    TEAM_RECORD_NOTE_TOOL,
    TEAM_MARK_INBOX_HANDLED_TOOL,
    TEAM_REQUEST_STATUS_TOOL,
    TEAM_ASK_QUESTION_TOOL,
    TEAM_SEND_PROGRESS_REPORT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_UPDATE_TASK_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
    TEAM_REQUEST_REVIEW_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
)
CHAT_TEAM_MCP_TOOL_NAMES = (TEAM_REPLY_TO_CHAT_TOOL,)
EPHEMERAL_WORKER_MCP_TOOL_NAMES = (TEAM_REPORT_EPHEMERAL_RESULT_TOOL,)
GOVERNANCE_TEAM_MCP_TOOL_NAMES = (
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
)
IMPLEMENTED_ORDINARY_TEAM_MCP_TOOL_NAMES = (
    TEAM_RECORD_NOTE_TOOL,
    TEAM_MARK_INBOX_HANDLED_TOOL,
    TEAM_REQUEST_STATUS_TOOL,
    TEAM_ASK_QUESTION_TOOL,
    TEAM_SEND_PROGRESS_REPORT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_UPDATE_TASK_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
    TEAM_REQUEST_REVIEW_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
)
IMPLEMENTED_CHAT_TEAM_MCP_TOOL_NAMES = (TEAM_REPLY_TO_CHAT_TOOL,)
IMPLEMENTED_EPHEMERAL_WORKER_MCP_TOOL_NAMES = (TEAM_REPORT_EPHEMERAL_RESULT_TOOL,)

MCP_TOOL_NAMES = (
    TEAM_RECORD_NOTE_TOOL,
    TEAM_MARK_INBOX_HANDLED_TOOL,
    TEAM_REQUEST_STATUS_TOOL,
    TEAM_ASK_QUESTION_TOOL,
    TEAM_SEND_PROGRESS_REPORT_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_UPDATE_TASK_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
    TEAM_REQUEST_REVIEW_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
    TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
)


def register_mcp_tools(server: FastMCP, context: AddonContext) -> tuple[str, ...]:
    repo = TeamStateRepository(context.runtime_dir)
    command_service = TeamCommandService(repo)
    resolver = TeamMcpContextResolver(repo=repo, context=context)

    @server.tool()
    def team_create_operator_directive(
        team_id: str,
        message: str,
        request_id: str,
        session_id: str | None = None,
        recipient_agent_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        title: str | None = None,
        source_message_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_governance_context(
            session_id=session_id,
            tool_name=TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        )
        try:
            _state, result = command_service.create_operator_directive_command(
                session_id=caller.session_id,
                team_id=team_id,
                message=message,
                request_id=request_id,
                governance_group_id=_governance_group_id_for_team(caller, team_id),
                recipient_agent_id=recipient_agent_id,
                project_id=project_id,
                task_id=task_id,
                title=title,
                source="team_governance_mcp",
                source_message_id=source_message_id,
                created_by=f"agent-session:{caller.session_id}",
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    @server.tool()
    def team_mark_governance_agenda_handled(
        agenda_item_id: str,
        session_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_governance_context(
            session_id=session_id,
            tool_name=TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
        )
        try:
            state, item = command_service.mark_governance_agenda_handled_by_session(
                agenda_item_id=agenda_item_id,
                requesting_session_id=caller.session_id,
                expected_revision=expected_revision,
            )
        except TeamStateConflictError as exc:
            return team_mcp_failure(
                revision=exc.current_revision,
                agent_id=caller.agent_id,
                errors=str(exc),
                correction_context=caller.correction_context(),
                agenda_item_id=agenda_item_id,
                governance_group_id=None,
                state=None,
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return team_mcp_success(
            revision=state.revision,
            progress_made=True,
            agent_id=caller.agent_id,
            governance_group_id=item.governance_group_id,
            agenda_item_id=item.id,
            state=item.state.value,
            governance_agenda_item=item.model_dump(mode="json"),
        )

    _register_slice5_tools(server, command_service, resolver, context)
    _register_slice6_tools(server, command_service, resolver, context)
    _register_slice7_tools(server, command_service, resolver, context)
    _register_slice8_tools(server, command_service, resolver, context)
    _register_slice9_tools(server, command_service, resolver, context)

    for tool_name in ORDINARY_TEAM_MCP_TOOL_NAMES:
        if tool_name in IMPLEMENTED_ORDINARY_TEAM_MCP_TOOL_NAMES:
            continue
        _register_persistent_placeholder(server, resolver, tool_name)
    for tool_name in CHAT_TEAM_MCP_TOOL_NAMES:
        if tool_name in IMPLEMENTED_CHAT_TEAM_MCP_TOOL_NAMES:
            continue
        _register_chat_placeholder(server, resolver, tool_name)
    for tool_name in EPHEMERAL_WORKER_MCP_TOOL_NAMES:
        if tool_name in IMPLEMENTED_EPHEMERAL_WORKER_MCP_TOOL_NAMES:
            continue
        _register_ephemeral_placeholder(server, resolver, tool_name)

    return MCP_TOOL_NAMES


def _register_slice5_tools(
    server: FastMCP,
    command_service: TeamCommandService,
    resolver: TeamMcpContextResolver,
    context: AddonContext,
) -> None:
    def team_record_note(
        body: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_RECORD_NOTE_TOOL,
        )
        try:
            _state, result = command_service.record_team_note_command(
                session_id=caller.session_id,
                body=body,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_record_note,
        name=TEAM_RECORD_NOTE_TOOL,
        description=(
            "Record a durable non-progress note for the current Team dispatch. "
            "Requires body and request_id."
        ),
    )

    def team_mark_inbox_handled(
        inbox_item_ids: list[str],
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_MARK_INBOX_HANDLED_TOOL,
        )
        try:
            _state, result = command_service.mark_inbox_handled_command(
                session_id=caller.session_id,
                inbox_item_ids=inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_mark_inbox_handled,
        name=TEAM_MARK_INBOX_HANDLED_TOOL,
        description=(
            "Mark one or more caller-owned pending Team inbox items handled. "
            "All listed inbox_item_ids are handled atomically."
        ),
    )

    def team_request_status(
        recipient_agent_id: str,
        question: str,
        request_id: str,
        task_id: str | None = None,
        response_requested: bool = True,
        priority: str = "normal",
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REQUEST_STATUS_TOOL,
        )
        try:
            _state, result = command_service.request_status_command(
                session_id=caller.session_id,
                recipient_agent_id=recipient_agent_id,
                task_id=task_id,
                question=question,
                response_requested=response_requested,
                priority=priority,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_request_status,
        name=TEAM_REQUEST_STATUS_TOOL,
        description=(
            "Ask a direct subordinate for status. Creates a STATUS_REQUEST inbox "
            "item and requires recipient_agent_id, question, and request_id."
        ),
    )

    def team_ask_question(
        question: str,
        request_id: str,
        recipient_agent_id: str | None = None,
        task_id: str | None = None,
        message: str | None = None,
        summary: str | None = None,
        response_requested: bool = True,
        priority: str = "normal",
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_ASK_QUESTION_TOOL,
        )
        try:
            _state, result = command_service.ask_question_command(
                session_id=caller.session_id,
                recipient_agent_id=recipient_agent_id,
                task_id=task_id,
                question=question,
                message=message,
                summary=summary,
                response_requested=response_requested,
                priority=priority,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_ask_question,
        name=TEAM_ASK_QUESTION_TOOL,
        description=(
            "Ask the caller's direct superior a question. Top-level managers "
            "must use team_escalate_operator. Requires question and request_id."
        ),
    )

    def team_send_progress_report(
        message: str,
        request_id: str,
        recipient_agent_id: str | None = None,
        task_id: str | None = None,
        summary: str | None = None,
        response_requested: bool = False,
        priority: str = "low",
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_SEND_PROGRESS_REPORT_TOOL,
        )
        try:
            _state, result = command_service.send_progress_report_command(
                session_id=caller.session_id,
                recipient_agent_id=recipient_agent_id,
                task_id=task_id,
                message=message,
                summary=summary,
                response_requested=response_requested,
                priority=priority,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_send_progress_report,
        name=TEAM_SEND_PROGRESS_REPORT_TOOL,
        description=(
            "Send a progress report to the caller's direct superior. Requires "
            "message and request_id; defaults to low priority with no response requested."
        ),
    )

    def team_reply_to_chat(
        conversation_id: str,
        message_id: str,
        body: str,
        request_id: str,
        summary: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_chat_reply_context(
            session_id=session_id,
            tool_name=TEAM_REPLY_TO_CHAT_TOOL,
        )
        try:
            _state, result = command_service.reply_to_chat_command(
                session_id=caller.session_id,
                conversation_id=conversation_id,
                message_id=message_id,
                body=body,
                summary=summary,
                request_id=request_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_reply_to_chat,
        name=TEAM_REPLY_TO_CHAT_TOOL,
        description=(
            "Reply to a human Team Chat operator message for the current chat "
            "dispatch. Requires conversation_id, message_id, body, and request_id."
        ),
    )

    def team_send_operator_chat_message(
        recipient_operator_user_id: str,
        body: str,
        request_id: str,
        summary: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        )
        try:
            _state, result = command_service.send_operator_chat_message_command(
                session_id=caller.session_id,
                recipient_operator_user_id=recipient_operator_user_id,
                body=body,
                summary=summary,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_send_operator_chat_message,
        name=TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        description=(
            "Send a Team Chat message to a known operator participant. Requires "
            "recipient_operator_user_id, body, and request_id."
        ),
    )

    def team_escalate_operator(
        title: str,
        message: str,
        request_id: str,
        urgency: str = "normal",
        project_id: str | None = None,
        task_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_ESCALATE_OPERATOR_TOOL,
        )
        try:
            _state, result = command_service.escalate_operator_command(
                session_id=caller.session_id,
                title=title,
                message=message,
                urgency=urgency,
                project_id=project_id,
                task_id=task_id,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_escalate_operator,
        name=TEAM_ESCALATE_OPERATOR_TOOL,
        description=(
            "Escalate a top-level team-manager issue to the operator or governance. "
            "Requires title, message, and request_id."
        ),
    )


def _register_slice6_tools(
    server: FastMCP,
    command_service: TeamCommandService,
    resolver: TeamMcpContextResolver,
    context: AddonContext,
) -> None:
    def team_create_case(
        title: str,
        request_id: str,
        id: str | None = None,
        project_id: str | None = None,
        description: str | None = None,
        status: str = "open",
        priority: str = "normal",
        owner_agent_id: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        tags: list[str] | None = None,
        task_ids: list[str] | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_CREATE_CASE_TOOL,
        )
        try:
            _state, result = command_service.create_case_command(
                session_id=caller.session_id,
                case_id=id,
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
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_create_case,
        name=TEAM_CREATE_CASE_TOOL,
        description=(
            "Create a Team case in the caller's active team. Requires title and request_id."
        ),
    )

    def team_update_case(
        case_id: str,
        project_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        owner_agent_id: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        tags: list[str] | None = None,
        task_ids: list[str] | None = None,
        request_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_UPDATE_CASE_TOOL,
        )
        updates: dict[str, Any] = {}
        for name, value in {
            "project_id": project_id,
            "title": title,
            "description": description,
            "status": status,
            "priority": priority,
            "owner_agent_id": owner_agent_id,
            "source": source,
            "source_ref": source_ref,
            "tags": tags,
            "task_ids": task_ids,
        }.items():
            if value is not None:
                updates[name] = value
        try:
            _state, result = command_service.update_case_command(
                session_id=caller.session_id,
                case_id=case_id,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
                **updates,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_update_case,
        name=TEAM_UPDATE_CASE_TOOL,
        description="Update fields or task links on an existing Team case.",
    )

    def team_create_task(
        owner_agent_id: str,
        project_id: str,
        priority: int,
        title: str,
        description: str,
        request_id: str,
        task_id: str | None = None,
        case_id: str | None = None,
        state: str = "pending",
        parent_task_id: str | None = None,
        dependency_task_ids: list[str] | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_CREATE_TASK_TOOL,
        )
        try:
            _state, result = command_service.create_task_command(
                session_id=caller.session_id,
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
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_create_task,
        name=TEAM_CREATE_TASK_TOOL,
        description="Create one Team task after permission and graph validation.",
    )

    def team_create_tasks(
        tasks: list[dict[str, Any]],
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_CREATE_TASKS_TOOL,
        )
        try:
            _state, result = command_service.create_tasks_command(
                session_id=caller.session_id,
                tasks=tasks,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_create_tasks,
        name=TEAM_CREATE_TASKS_TOOL,
        description="Create a batch of Team tasks all-or-nothing.",
    )

    def team_update_task(
        task_id: str,
        state: str | None = None,
        priority: int | None = None,
        title: str | None = None,
        description: str | None = None,
        owner_agent_id: str | None = None,
        dependency_task_ids: list[str] | None = None,
        request_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_UPDATE_TASK_TOOL,
        )
        updates: dict[str, Any] = {}
        for name, value in {
            "state": state,
            "priority": priority,
            "title": title,
            "description": description,
            "owner_agent_id": owner_agent_id,
            "dependency_task_ids": dependency_task_ids,
        }.items():
            if value is not None:
                updates[name] = value
        try:
            _state, result = command_service.update_task_command(
                session_id=caller.session_id,
                task_id=task_id,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
                **updates,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_update_task,
        name=TEAM_UPDATE_TASK_TOOL,
        description="Update a Team task without accepting or rejecting review work.",
    )

    def team_update_goal(
        project_id: str,
        goal_id: str,
        state: str,
        request_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_UPDATE_GOAL_TOOL,
        )
        try:
            _state, result = command_service.update_goal_command(
                session_id=caller.session_id,
                project_id=project_id,
                goal_id=goal_id,
                state=state,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_update_goal,
        name=TEAM_UPDATE_GOAL_TOOL,
        description="Update a project goal state as the project lead or team manager.",
    )

    def team_stand_down_agent(
        agent_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_STAND_DOWN_AGENT_TOOL,
        )
        try:
            _state, result = command_service.stand_down_agent_command(
                session_id=caller.session_id,
                agent_id=agent_id,
                reason=reason,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_stand_down_agent,
        name=TEAM_STAND_DOWN_AGENT_TOOL,
        description="Stand down a direct subordinate and its subtree.",
    )

    def team_revoke_stand_down(
        agent_id: str,
        request_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REVOKE_STAND_DOWN_TOOL,
        )
        try:
            _state, result = command_service.revoke_stand_down_command(
                session_id=caller.session_id,
                agent_id=agent_id,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_revoke_stand_down,
        name=TEAM_REVOKE_STAND_DOWN_TOOL,
        description="Revoke superior-sourced stand-downs previously set by the caller.",
    )


def _register_slice7_tools(
    server: FastMCP,
    command_service: TeamCommandService,
    resolver: TeamMcpContextResolver,
    context: AddonContext,
) -> None:
    artifact_publisher = _team_mcp_deliverable_artifact_publisher(context)

    def team_submit_deliverable(
        task_id: str,
        summary: str,
        request_id: str,
        id: str | None = None,
        supersedes_deliverable_id: str | None = None,
        artifact_paths: list[str] | None = None,
        artifact_refs: list[str] | None = None,
        artifact_space_ref: str | None = None,
        git_refs: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_SUBMIT_DELIVERABLE_TOOL,
        )
        try:
            _state, result = command_service.submit_deliverable_command(
                session_id=caller.session_id,
                id=id,
                task_id=task_id,
                summary=summary,
                supersedes_deliverable_id=supersedes_deliverable_id,
                artifact_paths=artifact_paths,
                artifact_refs=artifact_refs,
                artifact_space_ref=artifact_space_ref,
                git_refs=git_refs,
                verification=verification,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
                artifact_publisher=artifact_publisher,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_submit_deliverable,
        name=TEAM_SUBMIT_DELIVERABLE_TOOL,
        description=(
            "Submit a deliverable for the caller's owned task. Publishes any "
            "artifact_paths from /artifacts before recording state. Optional "
            "verification records status passed, failed, partial, not_run, or "
            "unknown with a summary. git_refs are provenance only; call "
            "team_publish_review_branch separately only when a durable review "
            "branch is needed. Requires task_id, summary, and request_id."
        ),
    )

    def team_submit_deliverable_for_review(
        deliverable: dict[str, Any],
        reviews: list[dict[str, Any]],
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
        )
        try:
            _state, result = command_service.submit_deliverable_for_review_command(
                session_id=caller.session_id,
                deliverable=deliverable,
                reviews=reviews,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
                artifact_publisher=artifact_publisher,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_submit_deliverable_for_review,
        name=TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
        description=(
            "Submit one deliverable and explicit review requests atomically. "
            "Suppresses the normal superior deliverable notice and requires "
            "deliverable, reviews, and request_id. The deliverable may include "
            "verification status and git_refs; git_refs do not publish a review "
            "branch by themselves."
        ),
    )

    def team_request_review(
        deliverable_id: str,
        reviewer_agent_id: str,
        request_id: str,
        id: str | None = None,
        review_profile_id: str | None = None,
        instructions: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REQUEST_REVIEW_TOOL,
        )
        try:
            _state, result = command_service.request_review_command(
                session_id=caller.session_id,
                id=id,
                deliverable_id=deliverable_id,
                reviewer_agent_id=reviewer_agent_id,
                review_profile_id=review_profile_id,
                instructions=instructions,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_request_review,
        name=TEAM_REQUEST_REVIEW_TOOL,
        description=(
            "Create a review request for an existing deliverable. Caller must be "
            "the producer, supervisor, project lead, or team manager; requires "
            "deliverable_id, reviewer_agent_id, and request_id."
        ),
    )

    def team_decide_review(
        review_request_id: str,
        state: str,
        request_id: str,
        findings: list[str] | None = None,
        criteria: list[dict[str, Any]] | None = None,
        override_routing_gap_reason: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_DECIDE_REVIEW_TOOL,
        )
        try:
            _state, result = command_service.decide_review_command(
                session_id=caller.session_id,
                review_request_id=review_request_id,
                state=state,
                findings=findings,
                criteria=criteria,
                override_routing_gap_reason=override_routing_gap_reason,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_decide_review,
        name=TEAM_DECIDE_REVIEW_TOOL,
        description=(
            "Record the assigned reviewer's accepted, rejected, or blocked "
            "decision. Requires review_request_id, state, and request_id."
        ),
    )


def _register_slice8_tools(
    server: FastMCP,
    command_service: TeamCommandService,
    resolver: TeamMcpContextResolver,
    context: AddonContext,
) -> None:
    def team_publish_review_branch(
        request_id: str,
        assignment_id: str | None = None,
        deliverable_id: str | None = None,
        source_branch: str | None = None,
        commit_sha: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
        )
        try:
            _state, result = command_service.publish_review_branch_command(
                session_id=caller.session_id,
                assignment_id=assignment_id,
                deliverable_id=deliverable_id,
                source_branch=source_branch,
                commit_sha=commit_sha,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_publish_review_branch,
        name=TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
        description=(
            "Record asynchronous Git review-branch promotion intent. Requires "
            "request_id and exactly one of assignment_id or deliverable_id. Use "
            "with deliverable_id only after a deliverable exists and a durable "
            "review branch is needed; it is not required merely because "
            "team_submit_deliverable included git_refs."
        ),
    )

    def team_update_feature_branch(
        task_id: str,
        source_review_branches: list[str],
        request_id: str,
        feature_branch: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_UPDATE_FEATURE_BRANCH_TOOL,
        )
        try:
            _state, result = command_service.update_feature_branch_command(
                session_id=caller.session_id,
                task_id=task_id,
                source_review_branches=source_review_branches,
                feature_branch=feature_branch,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_update_feature_branch,
        name=TEAM_UPDATE_FEATURE_BRANCH_TOOL,
        description=(
            "Record asynchronous Git feature-branch promotion intent. Requires "
            "task_id, source_review_branches, and request_id."
        ),
    )

    def team_request_release(
        project_id: str,
        deliverable_ids: list[str],
        request_id: str,
        id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        summary: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REQUEST_RELEASE_TOOL,
        )
        try:
            _state, result = command_service.request_release_command(
                session_id=caller.session_id,
                id=id,
                project_id=project_id,
                deliverable_ids=deliverable_ids,
                release_branch=release_branch,
                canonical_branch=canonical_branch,
                summary=summary,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_request_release,
        name=TEAM_REQUEST_RELEASE_TOOL,
        description=(
            "Record asynchronous Git release-package intent from accepted "
            "deliverables. Requires project_id, deliverable_ids, and request_id."
        ),
    )

    def team_recover_release(
        release_package_id: str,
        agent_branch: str,
        commit_sha: str,
        request_id: str,
        summary: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_RECOVER_RELEASE_TOOL,
        )
        try:
            _state, result = command_service.recover_release_command(
                session_id=caller.session_id,
                release_package_id=release_package_id,
                agent_branch=agent_branch,
                commit_sha=commit_sha,
                summary=summary,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_recover_release,
        name=TEAM_RECOVER_RELEASE_TOOL,
        description=(
            "Record asynchronous Git release-recovery intent for a blocked "
            "release package. Requires release_package_id, agent_branch, "
            "commit_sha, and request_id."
        ),
    )


def _team_mcp_deliverable_artifact_publisher(context: AddonContext):
    def _publish(
        *,
        agent: Any,
        team_id: str,
        project_id: str,
        deliverable_id: str,
        dispatch_id: str,
        artifact_paths: list[str] | None = None,
        source_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        del artifact_paths
        from .providers import _publish_deliverable_artifact_bundle

        runtime_context = EffectRuntimeContext(
            resolved_config={"runtime_dir": str(context.runtime_dir)},
            admin_service=context.service,
        )
        agent_payload = {
            "id": getattr(agent, "id", None),
            "session_id": getattr(agent, "session_id", None),
        }
        return _publish_deliverable_artifact_bundle(
            runtime_context,
            agent=agent_payload,
            team_id=team_id,
            project_id=project_id,
            deliverable_id=deliverable_id,
            dispatch_id=dispatch_id,
            source_paths=list(source_paths or []),
        )

    return _publish


def _register_slice9_tools(
    server: FastMCP,
    command_service: TeamCommandService,
    resolver: TeamMcpContextResolver,
    context: AddonContext,
) -> None:
    def team_request_ephemeral_agent(
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
        allowed_tools: list[str] | None = None,
        ttl_hours: int | None = None,
        directory_path: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
        )
        try:
            _state, result = command_service.request_ephemeral_agent_command(
                session_id=caller.session_id,
                id=id,
                task_id=task_id,
                worker_session_id=worker_session_id,
                title=title,
                prompt=prompt,
                model=model,
                agent_provider=agent_provider,
                execution_backend=execution_backend,
                container_image=container_image,
                allowed_tools=allowed_tools or [],
                ttl_hours=ttl_hours,
                directory_path=directory_path,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_request_ephemeral_agent,
        name=TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
        description=(
            "Record an asynchronous Team ephemeral worker-start intent. "
            "Requires task_id, title, prompt, and request_id."
        ),
    )

    def team_request_ephemeral_agents(
        assignments: list[dict[str, Any]],
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
        )
        try:
            _state, result = command_service.request_ephemeral_agents_command(
                session_id=caller.session_id,
                assignments=assignments,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_request_ephemeral_agents,
        name=TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
        description=(
            "Record an all-or-nothing batch of asynchronous Team ephemeral "
            "worker-start intents. Requires assignments and request_id."
        ),
    )

    def team_accept_ephemeral_result(
        assignment_id: str,
        request_id: str,
        deliverable_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
        )
        try:
            _state, result = command_service.accept_ephemeral_result_command(
                session_id=caller.session_id,
                assignment_id=assignment_id,
                deliverable_id=deliverable_id,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_accept_ephemeral_result,
        name=TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
        description=(
            "Accept a completed ephemeral worker result, create a parent-owned "
            "deliverable, and move the task to waiting_for_review."
        ),
    )

    def team_reject_ephemeral_result(
        assignment_id: str,
        request_id: str,
        rejection_reason: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
        )
        try:
            _state, result = command_service.reject_ephemeral_result_command(
                session_id=caller.session_id,
                assignment_id=assignment_id,
                rejection_reason=rejection_reason,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_reject_ephemeral_result,
        name=TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
        description="Reject an ephemeral worker result owned by the caller.",
    )

    def team_report_ephemeral_result(
        request_id: str,
        assignment_id: str | None = None,
        summary: str | None = None,
        artifact_refs: list[str] | None = None,
        agent_branch: str | None = None,
        commit_sha: str | None = None,
        error: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_ephemeral_assignment_context(
            session_id=session_id,
            tool_name=TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
            assignment_id=assignment_id,
        )
        try:
            _state, result = command_service.report_ephemeral_result_command(
                session_id=caller.session_id,
                assignment_id=assignment_id,
                summary=summary,
                artifact_refs=artifact_refs or [],
                agent_branch=agent_branch,
                commit_sha=commit_sha,
                error=error,
                request_id=request_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_report_ephemeral_result,
        name=TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
        description=(
            "Worker-scoped Team ephemeral result report. Success requires "
            "summary and request_id; failure requires error and request_id."
        ),
    )


def _team_command_mcp_result(
    result: dict[str, Any],
    *,
    caller: Any,
    context: AddonContext | None = None,
) -> dict[str, Any]:
    payload = dict(result)
    if context is not None:
        wake_request_events = _wake_request_events(payload.get("events"))
        try:
            published_event_ids = _publish_team_events(context, wake_request_events)
        except Exception as exc:
            if wake_request_events:
                payload["event_publish_error"] = str(exc)
        else:
            if published_event_ids:
                payload["published_event_ids"] = published_event_ids
    payload.setdefault("agent_id", caller.agent_id)
    payload.setdefault("governance_group_id", None)
    payload.setdefault("assignment_id", None)
    payload.setdefault("error", None)
    if payload.get("errors") and payload.get("error") is None:
        first = payload["errors"][0]
        payload["error"] = first.get("message") if isinstance(first, dict) else str(first)
    return payload


def _wake_request_events(events: object) -> list[Mapping[str, Any]]:
    if not isinstance(events, list):
        return []
    return [
        event
        for event in events
        if isinstance(event, Mapping) and event.get("event_type") == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]


def _publish_team_events(context: AddonContext, events: object) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = InternalEventBus(internal_event_store_path(context.runtime_dir))
    scheduled_store = ScheduledInternalEventStore(
        scheduled_internal_event_store_path(context.runtime_dir)
    )
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        delay_seconds = _optional_event_float(event.get("delay_seconds"))
        if delay_seconds is not None and delay_seconds > 0:
            scheduled, _created = scheduled_store.schedule_event(
                event_type=_required_event_text(event.get("event_type"), "event_type"),
                payload=dict(payload),
                correlation_key=_optional_event_text(event.get("correlation_key")),
                run_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                idempotency_key=_optional_event_text(event.get("idempotency_key")),
                source=InternalEventSource(
                    source_type="team-addon",
                    source_name="team-addon",
                ),
            )
            published_ids.append(scheduled.schedule_id)
            continue
        published = bus.publish(
            event_type=_required_event_text(event.get("event_type"), "event_type"),
            payload=dict(payload),
            correlation_key=_optional_event_text(event.get("correlation_key")),
            idempotency_key=_optional_event_text(event.get("idempotency_key")),
            source=InternalEventSource(
                source_type="team-addon",
                source_name="team-addon",
            ),
        )
        published_ids.append(published.event_id)
    return published_ids


def _required_event_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_event_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_event_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("number value is required")
    return float(value)


def _register_persistent_placeholder(
    server: FastMCP,
    resolver: TeamMcpContextResolver,
    tool_name: str,
) -> None:
    def placeholder(session_id: str | None = None) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=tool_name,
        )
        return _pending_implementation_result(tool_name=tool_name, caller=caller)

    placeholder.__name__ = tool_name
    server.add_tool(
        placeholder,
        name=tool_name,
        description=f"Team Add-on MCP tool {tool_name}.",
    )


def _register_chat_placeholder(
    server: FastMCP,
    resolver: TeamMcpContextResolver,
    tool_name: str,
) -> None:
    def placeholder(session_id: str | None = None) -> dict[str, Any]:
        caller = resolver.require_chat_reply_context(
            session_id=session_id,
            tool_name=tool_name,
        )
        return _pending_implementation_result(tool_name=tool_name, caller=caller)

    placeholder.__name__ = tool_name
    server.add_tool(
        placeholder,
        name=tool_name,
        description=f"Team Add-on MCP tool {tool_name}.",
    )


def _register_ephemeral_placeholder(
    server: FastMCP,
    resolver: TeamMcpContextResolver,
    tool_name: str,
) -> None:
    def placeholder(
        session_id: str | None = None,
        assignment_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_ephemeral_assignment_context(
            session_id=session_id,
            tool_name=tool_name,
            assignment_id=assignment_id,
        )
        return _pending_implementation_result(
            tool_name=tool_name,
            caller=caller,
            assignment_id=assignment_id or caller.assignment_ids[0],
        )

    placeholder.__name__ = tool_name
    server.add_tool(
        placeholder,
        name=tool_name,
        description=f"Team Add-on MCP tool {tool_name}.",
    )


def _pending_implementation_result(
    *,
    tool_name: str,
    caller: Any,
    assignment_id: str | None = None,
) -> dict[str, Any]:
    return team_mcp_failure(
        revision=caller.state.revision,
        agent_id=caller.agent_id,
        assignment_id=assignment_id,
        errors=(
            f"{tool_name} is registered for the Team MCP migration, "
            "but its mutation implementation belongs to a later slice"
        ),
        correction_context=caller.correction_context(),
    )


def _governance_group_id_for_team(caller: Any, team_id: object) -> str | None:
    normalized_team_id = str(team_id or "").strip()
    for group in caller.governance_groups:
        if normalized_team_id in group.member_team_ids:
            return group.id
    if len(caller.governance_groups) == 1:
        return caller.governance_groups[0].id
    return None
