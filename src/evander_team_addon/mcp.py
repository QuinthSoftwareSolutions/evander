from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext, AddonMcpTool
from evander.events.internal import (
    InternalEventSource,
)
from evander.orchestration.internal_events import (
    build_internal_event_bus_for_runtime,
    build_scheduled_internal_event_store_for_runtime,
)
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.events.provider_models import AgentExecEffectDefinition
from evander.queries.providers.base import QueryRuntimeContext
from evander.review_snapshots import team_deliverable_snapshot_binding_id

from .mcp_context import (
    TeamMcpContextResolver,
    team_mcp_failure,
    team_mcp_success,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .outbox_publication import request_team_outbox_publication_for_records
from .operator_chat_steering import reconcile_operator_chat_steering_deliveries
from .chat_attachments import (
    PreparedChatAttachment,
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
    validate_attachment_count,
    validate_attachment_policy_content,
)
from .services.domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE
from .services.read_service import TeamReadService
from .services.runtime_service import TeamRuntimeService
from .services.chat_service import TeamChatService
from .services.review_dispatch_steering import steer_closed_review_dispatch
from .services.commands import (
    TeamAgentControlCommandService,
    TeamAgentMessageCommandService,
    TeamBlockerCommandService,
    TeamCaseCommandService,
    TeamChatCommandService,
    TeamDeliverableSubmissionCommandService,
    TeamEphemeralCommandService,
    TeamGitReleaseCommandService,
    TeamGovernanceCommandService,
    TeamMemoryCommandService,
    TeamNoteCommandService,
    TeamOperatorEscalationCommandService,
    TeamOutcomeCatalogCommandService,
    TeamReviewCommandService,
    TeamTaskCreationCommandService,
    TeamTaskUpdateCommandService,
)
from .services.memory_service import (
    TeamMemorySemanticService,
    list_memory_records,
    memory_record_payload,
)
from .services.human_review_assignment_service import (
    human_review_eligibility_resolver,
)


logger = logging.getLogger(__name__)

TEAM_RECORD_NOTE_TOOL = "team_record_note"
TEAM_RECORD_CONTEXT_HANDOFF_TOOL = "team_record_context_handoff"
TEAM_RECORD_MEMORY_TOOL = "team_record_memory"
TEAM_ARCHIVE_MEMORY_TOOL = "team_archive_memory"
TEAM_MARK_INBOX_HANDLED_TOOL = "team_mark_inbox_handled"
TEAM_REQUEST_STATUS_TOOL = "team_request_status"
TEAM_ASK_QUESTION_TOOL = "team_ask_question"
TEAM_SEND_PROGRESS_REPORT_TOOL = "team_send_progress_report"
TEAM_REPLY_TO_CHAT_TOOL = "team_reply_to_chat"
TEAM_STAGE_CHAT_ATTACHMENT_TOOL = "team_stage_chat_attachment"
TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL = "team_send_operator_chat_message"
TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL = "team_acknowledge_operator_chat_messages"
TEAM_ESCALATE_OPERATOR_TOOL = "team_escalate_operator"
TEAM_REPORT_BLOCKER_TOOL = "team_report_blocker"
TEAM_UPDATE_BLOCKER_TOOL = "team_update_blocker"
TEAM_START_DEPENDENCY_COMPOSITION_RECOVERY_TOOL = "team_start_dependency_composition_recovery"
TEAM_CREATE_CASE_TOOL = "team_create_case"
TEAM_UPDATE_CASE_TOOL = "team_update_case"
TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL = "team_propose_human_finding_resolution"
TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL = "team_route_human_finding_correction"
TEAM_CLOSE_POST_RELEASE_CASE_TOOL = "team_close_post_release_case"
TEAM_CREATE_TASK_TOOL = "team_create_task"
TEAM_CREATE_TASKS_TOOL = "team_create_tasks"
TEAM_CREATE_OUTCOME_KIND_TOOL = "team_create_outcome_kind"
TEAM_UPDATE_OUTCOME_KIND_TOOL = "team_update_outcome_kind"
TEAM_ARCHIVE_OUTCOME_KIND_TOOL = "team_archive_outcome_kind"
TEAM_UPDATE_TASK_TOOL = "team_update_task"
TEAM_REOPEN_TASK_FOR_REWORK_TOOL = "team_reopen_task_for_rework"
TEAM_CANCEL_TASK_TOOL = "team_cancel_task"
TEAM_REPAIR_CANCELLED_TASK_DEPENDENCIES_TOOL = "team_repair_cancelled_task_dependencies"
TEAM_RECOVER_SUBMITTED_INTEGRATION_BOUNDARY_TOOL = "team_recover_submitted_integration_boundary"
TEAM_UPDATE_GOAL_TOOL = "team_update_goal"
TEAM_STAND_DOWN_AGENT_TOOL = "team_stand_down_agent"
TEAM_REVOKE_STAND_DOWN_TOOL = "team_revoke_stand_down"
TEAM_SUBMIT_DELIVERABLE_TOOL = "team_submit_deliverable"
TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL = "team_submit_deliverable_for_review"
TEAM_DECIDE_DELIVERABLE_TOOL = "team_decide_deliverable"
TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL = "team_link_deliverable_replacement"
TEAM_REQUEST_REVIEW_TOOL = "team_request_review"
TEAM_DECIDE_REVIEW_TOOL = "team_decide_review"
TEAM_RETRY_REVIEW_TOOL = "team_retry_review"
TEAM_WITHDRAW_REVIEW_TOOL = "team_withdraw_review"
TEAM_RETIRE_REVIEW_TOOL = "team_retire_review"
TEAM_PUBLISH_REVIEW_BRANCH_TOOL = "team_publish_review_branch"
TEAM_UPDATE_FEATURE_BRANCH_TOOL = "team_update_feature_branch"
TEAM_REQUEST_RELEASE_TOOL = "team_request_release"
TEAM_DECIDE_INITIAL_RELEASE_TOOL = "team_decide_initial_release"
TEAM_RECOVER_RELEASE_TOOL = "team_recover_release"
TEAM_RETRY_RELEASE_GATE_TOOL = "team_retry_release_gate"
TEAM_REQUEST_EPHEMERAL_AGENT_TOOL = "team_request_ephemeral_agent"
TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL = "team_request_ephemeral_agents"
TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL = "team_accept_ephemeral_result"
TEAM_REJECT_EPHEMERAL_RESULT_TOOL = "team_reject_ephemeral_result"
TEAM_REPORT_EPHEMERAL_RESULT_TOOL = "team_report_ephemeral_result"
TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL = "team_create_operator_directive"
TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL = "team_mark_governance_agenda_handled"
TEAM_LIST_TASKS_TOOL = "team_list_tasks"
TEAM_GET_TASK_TOOL = "team_get_task"
TEAM_LIST_INBOX_TOOL = "team_list_inbox"
TEAM_GET_INBOX_ITEM_TOOL = "team_get_inbox_item"
TEAM_LIST_BLOCKERS_TOOL = "team_list_blockers"
TEAM_GET_BLOCKER_TOOL = "team_get_blocker"
TEAM_LIST_CASES_TOOL = "team_list_cases"
TEAM_GET_CASE_TOOL = "team_get_case"
TEAM_LIST_DELIVERABLES_TOOL = "team_list_deliverables"
TEAM_GET_DELIVERABLE_TOOL = "team_get_deliverable"
TEAM_IMPORT_ARTIFACT_TOOL = "team_import_artifact"
TEAM_LIST_REVIEW_REQUESTS_TOOL = "team_list_review_requests"
TEAM_GET_REVIEW_REQUEST_TOOL = "team_get_review_request"
TEAM_LIST_RELEASE_PACKAGES_TOOL = "team_list_release_packages"
TEAM_GET_RELEASE_PACKAGE_TOOL = "team_get_release_package"
TEAM_LIST_AGENTS_TOOL = "team_list_agents"
TEAM_GET_AGENT_TOOL = "team_get_agent"
TEAM_GET_MY_IDENTITY_TOOL = "team_get_my_identity"
TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL = "team_get_current_dispatch_context"
TEAM_LIST_PROJECTS_TOOL = "team_list_projects"
TEAM_GET_PROJECT_TOOL = "team_get_project"
TEAM_LIST_OUTCOME_KINDS_TOOL = "team_list_outcome_kinds"
TEAM_GET_OUTCOME_KIND_TOOL = "team_get_outcome_kind"
TEAM_LIST_RUNTIME_ATTENTION_TOOL = "team_list_runtime_attention"
TEAM_SEARCH_MEMORY_TOOL = "team_search_memory"

ORDINARY_TEAM_MCP_TOOL_NAMES = (
    TEAM_RECORD_NOTE_TOOL,
    TEAM_RECORD_MEMORY_TOOL,
    TEAM_ARCHIVE_MEMORY_TOOL,
    TEAM_MARK_INBOX_HANDLED_TOOL,
    TEAM_REQUEST_STATUS_TOOL,
    TEAM_ASK_QUESTION_TOOL,
    TEAM_SEND_PROGRESS_REPORT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_REPORT_BLOCKER_TOOL,
    TEAM_UPDATE_BLOCKER_TOOL,
    TEAM_START_DEPENDENCY_COMPOSITION_RECOVERY_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL,
    TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL,
    TEAM_CLOSE_POST_RELEASE_CASE_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_CREATE_OUTCOME_KIND_TOOL,
    TEAM_UPDATE_OUTCOME_KIND_TOOL,
    TEAM_ARCHIVE_OUTCOME_KIND_TOOL,
    TEAM_UPDATE_TASK_TOOL,
    TEAM_REOPEN_TASK_FOR_REWORK_TOOL,
    TEAM_CANCEL_TASK_TOOL,
    TEAM_REPAIR_CANCELLED_TASK_DEPENDENCIES_TOOL,
    TEAM_RECOVER_SUBMITTED_INTEGRATION_BOUNDARY_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
    TEAM_DECIDE_DELIVERABLE_TOOL,
    TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL,
    TEAM_REQUEST_REVIEW_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_RETRY_REVIEW_TOOL,
    TEAM_WITHDRAW_REVIEW_TOOL,
    TEAM_RETIRE_REVIEW_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_DECIDE_INITIAL_RELEASE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_RETRY_RELEASE_GATE_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
)
CHAT_TEAM_MCP_TOOL_NAMES = (TEAM_REPLY_TO_CHAT_TOOL, TEAM_STAGE_CHAT_ATTACHMENT_TOOL)
EPHEMERAL_WORKER_MCP_TOOL_NAMES = (TEAM_REPORT_EPHEMERAL_RESULT_TOOL,)
GOVERNANCE_TEAM_MCP_TOOL_NAMES = (
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
)
READ_TEAM_MCP_TOOL_NAMES = (
    TEAM_LIST_TASKS_TOOL,
    TEAM_GET_TASK_TOOL,
    TEAM_LIST_INBOX_TOOL,
    TEAM_GET_INBOX_ITEM_TOOL,
    TEAM_LIST_BLOCKERS_TOOL,
    TEAM_GET_BLOCKER_TOOL,
    TEAM_LIST_CASES_TOOL,
    TEAM_GET_CASE_TOOL,
    TEAM_LIST_DELIVERABLES_TOOL,
    TEAM_GET_DELIVERABLE_TOOL,
    TEAM_IMPORT_ARTIFACT_TOOL,
    TEAM_LIST_REVIEW_REQUESTS_TOOL,
    TEAM_GET_REVIEW_REQUEST_TOOL,
    TEAM_LIST_RELEASE_PACKAGES_TOOL,
    TEAM_GET_RELEASE_PACKAGE_TOOL,
    TEAM_LIST_AGENTS_TOOL,
    TEAM_GET_AGENT_TOOL,
    TEAM_GET_MY_IDENTITY_TOOL,
    TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
    TEAM_LIST_PROJECTS_TOOL,
    TEAM_GET_PROJECT_TOOL,
    TEAM_LIST_OUTCOME_KINDS_TOOL,
    TEAM_GET_OUTCOME_KIND_TOOL,
    TEAM_LIST_RUNTIME_ATTENTION_TOOL,
    TEAM_SEARCH_MEMORY_TOOL,
)
IMPLEMENTED_ORDINARY_TEAM_MCP_TOOL_NAMES = (
    TEAM_RECORD_NOTE_TOOL,
    TEAM_RECORD_MEMORY_TOOL,
    TEAM_ARCHIVE_MEMORY_TOOL,
    TEAM_MARK_INBOX_HANDLED_TOOL,
    TEAM_REQUEST_STATUS_TOOL,
    TEAM_ASK_QUESTION_TOOL,
    TEAM_SEND_PROGRESS_REPORT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_REPORT_BLOCKER_TOOL,
    TEAM_UPDATE_BLOCKER_TOOL,
    TEAM_START_DEPENDENCY_COMPOSITION_RECOVERY_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL,
    TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL,
    TEAM_CLOSE_POST_RELEASE_CASE_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_CREATE_OUTCOME_KIND_TOOL,
    TEAM_UPDATE_OUTCOME_KIND_TOOL,
    TEAM_ARCHIVE_OUTCOME_KIND_TOOL,
    TEAM_UPDATE_TASK_TOOL,
    TEAM_REOPEN_TASK_FOR_REWORK_TOOL,
    TEAM_CANCEL_TASK_TOOL,
    TEAM_REPAIR_CANCELLED_TASK_DEPENDENCIES_TOOL,
    TEAM_RECOVER_SUBMITTED_INTEGRATION_BOUNDARY_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
    TEAM_DECIDE_DELIVERABLE_TOOL,
    TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL,
    TEAM_REQUEST_REVIEW_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_RETRY_REVIEW_TOOL,
    TEAM_WITHDRAW_REVIEW_TOOL,
    TEAM_RETIRE_REVIEW_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_DECIDE_INITIAL_RELEASE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_RETRY_RELEASE_GATE_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
)
IMPLEMENTED_CHAT_TEAM_MCP_TOOL_NAMES = (
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
)
IMPLEMENTED_EPHEMERAL_WORKER_MCP_TOOL_NAMES = (TEAM_REPORT_EPHEMERAL_RESULT_TOOL,)

MCP_TOOL_NAMES = (
    TEAM_RECORD_CONTEXT_HANDOFF_TOOL,
    TEAM_RECORD_NOTE_TOOL,
    TEAM_RECORD_MEMORY_TOOL,
    TEAM_ARCHIVE_MEMORY_TOOL,
    TEAM_MARK_INBOX_HANDLED_TOOL,
    TEAM_REQUEST_STATUS_TOOL,
    TEAM_ASK_QUESTION_TOOL,
    TEAM_SEND_PROGRESS_REPORT_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_REPORT_BLOCKER_TOOL,
    TEAM_UPDATE_BLOCKER_TOOL,
    TEAM_START_DEPENDENCY_COMPOSITION_RECOVERY_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL,
    TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL,
    TEAM_CLOSE_POST_RELEASE_CASE_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_CREATE_OUTCOME_KIND_TOOL,
    TEAM_UPDATE_OUTCOME_KIND_TOOL,
    TEAM_ARCHIVE_OUTCOME_KIND_TOOL,
    TEAM_UPDATE_TASK_TOOL,
    TEAM_REOPEN_TASK_FOR_REWORK_TOOL,
    TEAM_CANCEL_TASK_TOOL,
    TEAM_REPAIR_CANCELLED_TASK_DEPENDENCIES_TOOL,
    TEAM_RECOVER_SUBMITTED_INTEGRATION_BOUNDARY_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_SUBMIT_DELIVERABLE_FOR_REVIEW_TOOL,
    TEAM_DECIDE_DELIVERABLE_TOOL,
    TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL,
    TEAM_REQUEST_REVIEW_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_RETRY_REVIEW_TOOL,
    TEAM_WITHDRAW_REVIEW_TOOL,
    TEAM_RETIRE_REVIEW_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_DECIDE_INITIAL_RELEASE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_RETRY_RELEASE_GATE_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_REJECT_EPHEMERAL_RESULT_TOOL,
    TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
    *READ_TEAM_MCP_TOOL_NAMES,
)
_MCP_TOOL_CATALOG_DESCRIPTIONS = {
    TEAM_RECORD_CONTEXT_HANDOFF_TOOL: (
        "Record one concise advisory handoff for the caller's pending context reset. "
        "Available only during the dedicated context-handoff dispatch."
    ),
    TEAM_DECIDE_DELIVERABLE_TOOL: (
        "Accept or reject a direct deliverable handoff as its recorded superior; "
        "formal review decisions remain separate."
    ),
    TEAM_ESCALATE_OPERATOR_TOOL: (
        "Queue a top-level team-manager escalation to the operator or governance. "
        "The command result is an asynchronous receipt, not delivery confirmation."
    ),
    TEAM_REPLY_TO_CHAT_TOOL: (
        "Reply to the specific human operator message that started the current Team Chat "
        "dispatch. Available only while that dispatch is active; use "
        "team_send_operator_chat_message for proactive messages to listed operators."
    ),
    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL: (
        "Confirm that operator messages injected into the current dispatch were acted on "
        "and require no human-facing reply."
    ),
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL: (
        "Stage one regular file from the current agent workspace for a Team Chat reply "
        "or proactive operator message. Reply-only Chat dispatches are text-only. "
        "Returns a session-owned attachment_id."
    ),
    TEAM_IMPORT_ARTIFACT_TOOL: (
        "Permission-check and import the latest published version of an artifact-space "
        "reference into the calling Team agent's own /artifacts tree. Use this after "
        "team_get_deliverable when another agent hands off an artifact in an ordinary "
        "task, status request, question, or progress report."
    ),
}
MCP_TOOL_CATALOG = tuple(
    AddonMcpTool(
        name=tool_name,
        label=tool_name.removeprefix("team_").replace("_", " ").capitalize(),
        description=_MCP_TOOL_CATALOG_DESCRIPTIONS.get(
            tool_name,
            f"Team Add-on tool for {tool_name.removeprefix('team_').replace('_', ' ')}.",
        ),
        group="Team Add-on",
    )
    for tool_name in MCP_TOOL_NAMES
)


def register_mcp_tools(server: FastMCP, context: AddonContext) -> tuple[str, ...]:
    repo = TeamStateRepository(context.runtime_dir)
    managed_git = _team_mcp_managed_git_admin(context)
    note_commands = TeamNoteCommandService(repo)
    memory_commands = TeamMemoryCommandService(repo)
    agent_message_commands = TeamAgentMessageCommandService(repo)
    chat_commands = TeamChatCommandService(repo)
    operator_escalation_commands = TeamOperatorEscalationCommandService(repo)
    blocker_commands = TeamBlockerCommandService(repo)
    case_commands = TeamCaseCommandService(repo)
    git_default_branch_resolver = _team_mcp_git_default_branch_resolver(
        context,
        managed_git=managed_git,
    )
    task_creation_commands = TeamTaskCreationCommandService(
        repo,
        git_default_branch_resolver=git_default_branch_resolver,
    )
    outcome_catalog_commands = TeamOutcomeCatalogCommandService(repo)
    task_update_commands = TeamTaskUpdateCommandService(
        repo,
        git_default_branch_resolver=git_default_branch_resolver,
    )
    agent_control_commands = TeamAgentControlCommandService(repo)
    governance_commands = TeamGovernanceCommandService(repo)
    deliverable_submission_commands = TeamDeliverableSubmissionCommandService(
        repo,
        git_candidate_commit_resolver=_team_mcp_git_candidate_commit_resolver(
            context,
            managed_git=managed_git,
        ),
        git_candidate_snapshot_pinner=_team_mcp_git_candidate_snapshot_pinner(
            context,
            managed_git=managed_git,
        ),
        dependency_recovery_commit_verifier=(
            _team_mcp_dependency_recovery_commit_verifier(
                context,
                managed_git=managed_git,
            )
        ),
    )
    review_commands = TeamReviewCommandService(
        repo,
        human_review_eligibility_resolver=human_review_eligibility_resolver(
            getattr(context.auth, "user_store", None)
        ),
        git_candidate_commit_resolver=_team_mcp_git_candidate_commit_resolver(
            context,
            managed_git=managed_git,
        ),
        review_dispatch_steering=lambda **kwargs: steer_closed_review_dispatch(
            runtime_dir=context.runtime_dir,
            **kwargs,
        ),
    )
    git_release_commands = TeamGitReleaseCommandService(repo)
    ephemeral_commands = TeamEphemeralCommandService(
        repo,
        session_lookup=getattr(context.service, "session_repository", None),
    )
    resolver = TeamMcpContextResolver(repo=repo, context=context)
    attachment_storage = TeamChatAttachmentStorage(context.runtime_dir)
    attachment_policy_service = TeamChatAttachmentPolicyService(repo)

    @server.tool()
    def team_record_context_handoff(
        summary: str,
        request_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del request_id  # The rotation is single-use and dispatch-bound.
        caller = resolver.require_context_handoff_context(
            session_id=session_id,
            tool_name=TEAM_RECORD_CONTEXT_HANDOFF_TOOL,
        )
        try:
            state = TeamRuntimeService(repo).record_agent_context_handoff(
                agent_id=caller.agent_id or "",
                dispatch_id=caller.active_dispatch_id or "",
                summary=summary,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_mcp_result_with_attention(
            team_mcp_success(
                revision=state.revision,
                progress_made=False,
                agent_id=caller.agent_id,
                context_rotation_pending=True,
                handoff_recorded=True,
            ),
            caller=caller,
            context=context,
        )

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
            _state, result = governance_commands.create_operator_directive_command(
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
            state, item = governance_commands.mark_governance_agenda_handled_by_session(
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
        return _team_mcp_result_with_attention(
            team_mcp_success(
                revision=state.revision,
                progress_made=True,
                agent_id=caller.agent_id,
                governance_group_id=item.governance_group_id,
                agenda_item_id=item.id,
                state=item.state.value,
                governance_agenda_item=item.model_dump(mode="json"),
            ),
            caller=caller,
            context=context,
        )

    _register_slice5_tools(
        server,
        note_commands,
        memory_commands,
        agent_message_commands,
        chat_commands,
        operator_escalation_commands,
        resolver,
        context,
        attachment_storage,
        attachment_policy_service,
    )
    _register_slice6_tools(
        server,
        case_commands,
        task_creation_commands,
        task_update_commands,
        outcome_catalog_commands,
        blocker_commands,
        agent_control_commands,
        resolver,
        context,
    )
    _register_slice7_tools(
        server,
        deliverable_submission_commands,
        review_commands,
        resolver,
        context,
    )
    _register_slice8_tools(server, git_release_commands, resolver, context)
    _register_slice9_tools(server, ephemeral_commands, resolver, context)
    _register_read_tools(server, resolver, context)

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
    note_commands: TeamNoteCommandService,
    memory_commands: TeamMemoryCommandService,
    agent_message_commands: TeamAgentMessageCommandService,
    chat_commands: TeamChatCommandService,
    operator_escalation_commands: TeamOperatorEscalationCommandService,
    resolver: TeamMcpContextResolver,
    context: AddonContext,
    attachment_storage: TeamChatAttachmentStorage,
    attachment_policy_service: TeamChatAttachmentPolicyService,
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
            _state, result = note_commands.record_team_note_command(
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

    def team_record_memory(
        kind: str,
        title: str,
        summary: str,
        request_id: str,
        id: str | None = None,
        project_id: str | None = None,
        codebase_id: str | None = None,
        agent_id: str | None = None,
        scope: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        source_refs: list[str] | None = None,
        artifact_refs: list[str] | None = None,
        artifact_paths: list[str] | None = None,
        artifact_space_ref: str | None = None,
        confidence: float | None = None,
        supersedes_memory_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_RECORD_MEMORY_TOOL,
        )
        try:
            _state, result = memory_commands.record_memory_command(
                session_id=caller.session_id,
                kind=kind,
                title=title,
                summary=summary,
                request_id=request_id,
                id=id,
                project_id=project_id,
                codebase_id=codebase_id,
                agent_id=agent_id,
                scope=scope,
                body=body,
                tags=tags,
                source_refs=source_refs,
                artifact_refs=artifact_refs,
                artifact_paths=artifact_paths,
                artifact_space_ref=artifact_space_ref,
                confidence=confidence,
                supersedes_memory_id=supersedes_memory_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
                artifact_publisher=_team_mcp_memory_artifact_publisher(context),
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_record_memory,
        name=TEAM_RECORD_MEMORY_TOOL,
        description=(
            "Record or update a durable Team memory. Normal wakes do not include "
            "memories automatically; use team_search_memory when recall is needed."
        ),
    )

    def team_archive_memory(
        memory_id: str,
        request_id: str,
        reason: str | None = None,
        superseded_by_memory_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_ARCHIVE_MEMORY_TOOL,
        )
        try:
            _state, result = memory_commands.archive_memory_command(
                session_id=caller.session_id,
                memory_id=memory_id,
                request_id=request_id,
                reason=reason,
                superseded_by_memory_id=superseded_by_memory_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_archive_memory,
        name=TEAM_ARCHIVE_MEMORY_TOOL,
        description="Archive or supersede a visible Team memory by id.",
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
            _state, result = note_commands.mark_inbox_handled_command(
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
            _state, result = agent_message_commands.request_status_command(
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
            _state, result = agent_message_commands.ask_question_command(
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
            "Ask a question, defaulting to the caller's direct superior. With "
            "task_id, recipient_agent_id may select the active same-Team task "
            "owner, linked case owner, or project lead when the current dispatch "
            "authorizes that project. Top-level managers must use "
            "team_escalate_operator for operator escalation. Requires question "
            "and request_id."
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
            _state, result = agent_message_commands.send_progress_report_command(
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

    def team_stage_chat_attachment(
        source_path: str,
        filename: str | None = None,
        mime_type: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_chat_attachment_context(
            session_id=session_id,
            tool_name=TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
        )
        policy = attachment_policy_service.effective_policy(
            _team_session_directory_path(context, caller.session_id),
            state=caller.state,
        )
        if not policy.agent_to_operator_enabled:
            raise ToolError("Agent-to-operator Team Chat attachments are disabled")
        try:
            workspace_file = _read_team_chat_workspace_file(
                context,
                session_id=caller.session_id,
                source_path=source_path,
                max_bytes=policy.max_attachment_bytes,
            )
            resolved_filename = str(filename or workspace_file["filename"])
            resolved_mime = str(mime_type or workspace_file.get("mime_type") or "") or None
            content = base64.b64decode(str(workspace_file["content_base64"]), validate=True)
            validate_attachment_policy_content(
                policy,
                direction="agent_to_operator",
                filename=resolved_filename,
                mime_type=resolved_mime,
                byte_count=len(content),
            )
            staged = attachment_storage.stage_agent_file(
                session_id=caller.session_id,
                source_path=str(workspace_file["source_path"]),
                content=content,
                filename=resolved_filename,
                mime_type=resolved_mime,
            )
        except Exception as exc:
            raise ToolError(str(exc)) from exc
        return _team_mcp_result_with_attention(
            {
                "success": True,
                "revision": caller.state.revision,
                "attachment": staged.payload(),
            },
            caller=caller,
            context=context,
        )

    server.add_tool(
        team_stage_chat_attachment,
        name=TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
        description=(
            "Stage one regular file under /home/agent for an attachment-enabled Team Chat "
            "message. Reply-only Chat dispatches cannot stage files. The returned attachment "
            "id is bound to the current session and expires."
        ),
    )

    def team_reply_to_chat(
        conversation_id: str,
        body: str,
        request_id: str,
        message_id: str | None = None,
        message_ids: list[str] | None = None,
        attachment_ids: list[str] | None = None,
        approval_request_id: str | None = None,
        summary: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_chat_reply_context(
            session_id=session_id,
            tool_name=TEAM_REPLY_TO_CHAT_TOOL,
        )
        normalized_attachment_ids = list(attachment_ids or [])
        if normalized_attachment_ids:
            resolver.require_chat_attachment_context(
                session_id=caller.session_id,
                tool_name=TEAM_REPLY_TO_CHAT_TOOL,
            )
        approval_payload = {
            "tool_name": TEAM_REPLY_TO_CHAT_TOOL,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "body": body,
            "attachment_ids": normalized_attachment_ids,
        }
        if message_ids:
            approval_payload["message_ids"] = list(message_ids)
        prepared_attachments = _prepare_team_chat_agent_attachments(
            context,
            storage=attachment_storage,
            policy_service=attachment_policy_service,
            caller=caller,
            attachment_ids=normalized_attachment_ids,
            approval_request_id=approval_request_id,
            approval_payload=approval_payload,
        )
        newly_promoted_attachments = [
            attachment
            for attachment in prepared_attachments
            if attachment.id not in caller.state.chat_attachments
        ]
        try:
            _state, result = chat_commands.reply_to_chat_command(
                session_id=caller.session_id,
                conversation_id=conversation_id,
                message_id=message_id,
                message_ids=list(message_ids or []),
                body=body,
                attachments=prepared_attachments,
                source_attachment_ids=normalized_attachment_ids,
                summary=summary,
                request_id=request_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except Exception as exc:
            _delete_uncommitted_team_chat_attachments(
                repository=chat_commands.repository,
                storage=attachment_storage,
                attachments=newly_promoted_attachments,
            )
            raise ToolError(str(exc)) from exc
        if not bool(result.get("success")):
            _delete_uncommitted_team_chat_attachments(
                repository=chat_commands.repository,
                storage=attachment_storage,
                attachments=newly_promoted_attachments,
            )
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_reply_to_chat,
        name=TEAM_REPLY_TO_CHAT_TOOL,
        description=(
            "Reply to a human Team Chat operator message for the current chat "
            "dispatch. Use message_ids to cover a coalesced chat batch; message_id remains "
            "a compatible shorthand for the active batch. Requires conversation_id, request_id, "
            "and either body "
            "or staged attachment_ids. Reply-only dispatches accept text only; agent file "
            "sends may require exact approval."
        ),
    )

    def team_send_operator_chat_message(
        recipient_operator_user_id: str,
        body: str,
        request_id: str,
        attachment_ids: list[str] | None = None,
        source_message_ids: list[str] | None = None,
        approval_request_id: str | None = None,
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
        normalized_attachment_ids = list(attachment_ids or [])
        prepared_attachments = _prepare_team_chat_agent_attachments(
            context,
            storage=attachment_storage,
            policy_service=attachment_policy_service,
            caller=caller,
            attachment_ids=normalized_attachment_ids,
            approval_request_id=approval_request_id,
            approval_payload={
                "tool_name": TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
                "recipient_operator_user_id": recipient_operator_user_id,
                "body": body,
                "attachment_ids": normalized_attachment_ids,
                "source_message_ids": list(source_message_ids or []),
            },
        )
        newly_promoted_attachments = [
            attachment
            for attachment in prepared_attachments
            if attachment.id not in caller.state.chat_attachments
        ]
        try:
            _state, result = chat_commands.send_operator_chat_message_command(
                session_id=caller.session_id,
                recipient_operator_user_id=recipient_operator_user_id,
                body=body,
                attachments=prepared_attachments,
                source_attachment_ids=normalized_attachment_ids,
                source_message_ids=source_message_ids or [],
                summary=summary,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except Exception as exc:
            _delete_uncommitted_team_chat_attachments(
                repository=chat_commands.repository,
                storage=attachment_storage,
                attachments=newly_promoted_attachments,
            )
            raise ToolError(str(exc)) from exc
        if not bool(result.get("success")):
            _delete_uncommitted_team_chat_attachments(
                repository=chat_commands.repository,
                storage=attachment_storage,
                attachments=newly_promoted_attachments,
            )
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_send_operator_chat_message,
        name=TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        description=(
            "Send a Team Chat message to an operator in the caller's existing direct "
            "conversation. Requires recipient_operator_user_id, body, and request_id. "
            "When responding to operator attention injected during this dispatch, pass "
            "the exact source_message_ids from the attention notice so those messages are "
            "atomically marked replied."
        ),
    )

    def team_acknowledge_operator_chat_messages(
        message_ids: list[str],
        summary: str,
        request_id: str,
        disposition: str = "acted_no_reply",
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
        )
        try:
            _state, result = chat_commands.acknowledge_operator_chat_messages_command(
                session_id=caller.session_id,
                message_ids=message_ids,
                disposition=disposition,
                summary=summary,
                request_id=request_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_acknowledge_operator_chat_messages,
        name=TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
        description=(
            "Acknowledge that text-only operator messages injected into the current dispatch "
            "were acted on and need no human-facing reply. Requires the exact message_ids, "
            "disposition='acted_no_reply', a short summary, and request_id."
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
            _state, result = operator_escalation_commands.escalate_operator_command(
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
            "Queue a top-level team-manager issue for the operator or governance. "
            "Requires title, message, and request_id. The result is a receipt, not "
            "delivery confirmation: while async_effect_pending is true and "
            "downstream_effect_completed is false, report the escalation as queued "
            "or pending, never delivered."
        ),
    )


def _register_slice6_tools(
    server: FastMCP,
    case_commands: TeamCaseCommandService,
    task_creation_commands: TeamTaskCreationCommandService,
    task_update_commands: TeamTaskUpdateCommandService,
    outcome_catalog_commands: TeamOutcomeCatalogCommandService,
    blocker_commands: TeamBlockerCommandService,
    agent_control_commands: TeamAgentControlCommandService,
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
            _state, result = case_commands.create_case_command(
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
            _state, result = case_commands.update_case_command(
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

    def team_propose_human_finding_resolution(
        finding_id: str,
        proposed_resolution: str,
        evidence_refs: list[str],
        proposed_by_deliverable_ids: list[str],
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL,
        )
        try:
            _state, result = case_commands.propose_human_finding_resolution_command(
                session_id=caller.session_id,
                finding_id=finding_id,
                proposed_resolution=proposed_resolution,
                evidence_refs=evidence_refs,
                proposed_by_deliverable_ids=proposed_by_deliverable_ids,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_propose_human_finding_resolution,
        name=TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL,
        description=(
            "Propose resolution of an open human-review finding from accepted "
            "correction deliverables. Requires evidence and request_id; this does "
            "not make the human decision."
        ),
    )

    def team_route_human_finding_correction(
        finding_id: str,
        owner_agent_id: str,
        request_id: str,
        target_deliverable_ids: list[str] | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL,
        )
        try:
            _state, result = case_commands.route_human_finding_correction_command(
                session_id=caller.session_id,
                finding_id=finding_id,
                owner_agent_id=owner_agent_id,
                request_id=request_id,
                target_deliverable_ids=target_deliverable_ids,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_route_human_finding_correction,
        name=TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL,
        description=(
            "Recover an existing correction child for a legacy human-review "
            "finding that still carries ambiguous-routing metadata by assigning "
            "it to a project assignee and optionally recording affected "
            "deliverables. This does not create or rewrite the task."
        ),
    )

    def team_close_post_release_case(
        case_id: str,
        reason: str,
        evidence_refs: list[str],
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_CLOSE_POST_RELEASE_CASE_TOOL,
        )
        try:
            _state, result = case_commands.close_post_release_feedback_case_command(
                session_id=caller.session_id,
                case_id=case_id,
                reason=reason,
                evidence_refs=evidence_refs,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_close_post_release_case,
        name=TEAM_CLOSE_POST_RELEASE_CASE_TOOL,
        description=(
            "Close a post-release feedback case after all linked tasks resolve. "
            "Requires a reason, evidence, and request_id; it does not roll back "
            "the published release."
        ),
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
        originating_task_id: str | None = None,
        git_base_branch: str | None = None,
        dependency_task_ids: list[str] | None = None,
        task_kind: str = "support",
        outcome_contract: dict[str, Any] | None = None,
        integration_boundary: bool = False,
        deliverable_contract: dict[str, Any] | None = None,
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
            _state, result = task_creation_commands.create_task_command(
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
                originating_task_id=originating_task_id,
                git_base_branch=git_base_branch,
                dependency_task_ids=dependency_task_ids,
                task_kind=task_kind,
                outcome_contract=outcome_contract,
                integration_boundary=integration_boundary,
                deliverable_contract=deliverable_contract,
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
        description=(
            "Create one Team task after permission and graph validation. Set "
            "integration_boundary=true with deliverable_contract to declare an "
            "explicit final-integration boundary. The contract may require Git "
            "provenance and explicitly name direct-child or dependency task "
            "contributions. A non-integration leaf may also use a deliverable_contract "
            "with no contribution task IDs when it must resolve through an accepted "
            "deliverable. git_base_branch optionally selects this task's immutable "
            "starting workstream; otherwise a child inherits its parent and a root "
            "snapshots the project default. originating_task_id records lineage only and does not "
            "seed Git content; when a correction must edit accepted predecessor "
            "content, also put that predecessor in dependency_task_ids so its exact "
            "accepted commit is verified and composed before work starts."
        ),
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
            _state, result = task_creation_commands.create_tasks_command(
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

    def team_create_outcome_kind(
        team_id: str,
        kind: str,
        name: str,
        request_id: str,
        project_id: str | None = None,
        definition_id: str | None = None,
        description: str = "",
        reference_requirements: str = "",
        evidence_requirements: str = "",
        handled_inbox_item_ids: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_CREATE_OUTCOME_KIND_TOOL,
        )
        try:
            _state, result = outcome_catalog_commands.create_outcome_kind_command(
                session_id=caller.session_id,
                team_id=team_id,
                project_id=project_id,
                definition_id=definition_id,
                kind=kind,
                name=name,
                description=description,
                reference_requirements=reference_requirements,
                evidence_requirements=evidence_requirements,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_create_outcome_kind,
        name=TEAM_CREATE_OUTCOME_KIND_TOOL,
        description=(
            "Create a defined Team outcome kind. Requires manage_outcome_kinds "
            "and matching team-manager or project-lead scope."
        ),
    )

    def team_update_outcome_kind(
        definition_id: str,
        request_id: str,
        name: str | None = None,
        description: str | None = None,
        reference_requirements: str | None = None,
        evidence_requirements: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_UPDATE_OUTCOME_KIND_TOOL,
        )
        try:
            _state, result = outcome_catalog_commands.update_outcome_kind_command(
                session_id=caller.session_id,
                definition_id=definition_id,
                name=name,
                description=description,
                reference_requirements=reference_requirements,
                evidence_requirements=evidence_requirements,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_update_outcome_kind,
        name=TEAM_UPDATE_OUTCOME_KIND_TOOL,
        description=(
            "Update an outcome kind's human meaning and reference/evidence "
            "requirements. Existing task and review snapshots do not change."
        ),
    )

    def team_archive_outcome_kind(
        definition_id: str,
        request_id: str,
        archived: bool = True,
        handled_inbox_item_ids: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_ARCHIVE_OUTCOME_KIND_TOOL,
        )
        try:
            _state, result = outcome_catalog_commands.archive_outcome_kind_command(
                session_id=caller.session_id,
                definition_id=definition_id,
                archived=archived,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_archive_outcome_kind,
        name=TEAM_ARCHIVE_OUTCOME_KIND_TOOL,
        description=(
            "Archive or restore an outcome kind definition. Active review "
            "profile references must be removed before archival."
        ),
    )

    def team_update_task(
        task_id: str,
        state: str | None = None,
        priority: int | None = None,
        title: str | None = None,
        description: str | None = None,
        owner_agent_id: str | None = None,
        dependency_task_ids: list[str] | None = None,
        git_base_branch: str | None = None,
        integration_boundary: bool | None = None,
        deliverable_contract: dict[str, Any] | None = None,
        completion: dict[str, Any] | None = None,
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
            "git_base_branch": git_base_branch,
            "integration_boundary": integration_boundary,
            "deliverable_contract": deliverable_contract,
            "completion": completion,
        }.items():
            if value is not None:
                updates[name] = value
        try:
            _state, result = task_update_commands.update_task_command(
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
        description=(
            "Update a Team task without accepting or rejecting review work. "
            "Before any deliverable submission, an authorized task-graph editor "
            "can supply a deliverable_contract to require accepted work, and can "
            "also designate a composite as an integration boundary with "
            "integration_boundary=true. git_base_branch may be changed only before "
            "Git preparation, deliverable submission, or promotion creation."
        ),
    )

    def team_reopen_task_for_rework(
        task_id: str,
        accepted_deliverable_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REOPEN_TASK_FOR_REWORK_TOOL,
        )
        try:
            _state, result = task_update_commands.reopen_task_for_rework_command(
                session_id=caller.session_id,
                task_id=task_id,
                accepted_deliverable_id=accepted_deliverable_id,
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
        team_reopen_task_for_rework,
        name=TEAM_REOPEN_TASK_FOR_REWORK_TOOL,
        description=(
            "Reopen a finished or accepted task when one of its current accepted "
            "deliverables later proves invalid. The old acceptance and reviews remain "
            "immutable; the original owner receives one high-priority rework wake, and "
            "the task cannot resolve again until a fresh formally reviewed deliverable "
            "supersedes accepted_deliverable_id. The task owner, its project lead, or "
            "same-team manager may use this. Requires task_id, "
            "accepted_deliverable_id, reason, and request_id."
        ),
    )

    def team_cancel_task(
        task_id: str,
        reason_code: str,
        reason: str,
        request_id: str,
        successor_task_id: str | None = None,
        rebind_dependents: bool = False,
        expected_dependent_task_ids: list[str] | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_CANCEL_TASK_TOOL,
        )
        try:
            _state, result = task_update_commands.cancel_task_command(
                session_id=caller.session_id,
                task_id=task_id,
                reason_code=reason_code,
                reason=reason,
                successor_task_id=successor_task_id,
                rebind_dependents=rebind_dependents,
                expected_dependent_task_ids=expected_dependent_task_ids,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_cancel_task,
        name=TEAM_CANCEL_TASK_TOOL,
        description=(
            "Cancel a Team task with an audited reason and optional same-project "
            "successor. Set rebind_dependents=true to atomically replace every "
            "nonterminal dependent task's edge from the cancelled task to that "
            "successor, including required contribution frontiers. The complete "
            "resulting graph is validated before mutation; stale active integration "
            "Git preparations are abandoned and explicitly queued for re-preparation. "
            "When rebinding, supply the sorted expected_dependent_task_ids from the "
            "dispatch snapshot; concurrent graph expansion fails closed. "
            "Only the project lead or Team manager may rebind dependents; an ordinary "
            "task owner may still cancel without rewriting the graph. "
            "Unresolved descendants must be handled first. When cancellation fulfills "
            "a task-linked operator directive, include that directive ID in "
            "handled_inbox_item_ids so disposition is atomic with cancellation."
        ),
    )

    def team_repair_cancelled_task_dependencies(
        predecessor_task_id: str,
        successor_task_id: str,
        expected_dependent_task_ids: list[str],
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REPAIR_CANCELLED_TASK_DEPENDENCIES_TOOL,
        )
        try:
            _state, result = task_update_commands.repair_cancelled_task_dependencies_command(
                session_id=caller.session_id,
                predecessor_task_id=predecessor_task_id,
                successor_task_id=successor_task_id,
                expected_dependent_task_ids=expected_dependent_task_ids,
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
        team_repair_cancelled_task_dependencies,
        name=TEAM_REPAIR_CANCELLED_TASK_DEPENDENCIES_TOOL,
        description=(
            "Repair active dependent tasks stranded behind an already-cancelled "
            "predecessor. The requested successor must exactly match the immutable "
            "successor recorded by the original cancellation. Supply the frozen, "
            "sorted expected_dependent_task_ids from the current task graph so a "
            "concurrent graph expansion fails closed. The project lead or Team manager "
            "may run this; the original cancellation and terminal history remain "
            "unchanged, and Team stores durable repair action evidence."
        ),
    )

    def team_recover_submitted_integration_boundary(
        predecessor_task_id: str,
        successor_task_id: str,
        integration_task_id: str,
        replacement_task_id: str,
        expected_dependency_task_ids: list[str],
        expected_required_contribution_task_ids: list[str],
        reason: str,
        request_id: str,
        git_base_commit_sha: str | None = None,
        git_base_branch: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_RECOVER_SUBMITTED_INTEGRATION_BOUNDARY_TOOL,
        )
        try:
            _state, result = task_update_commands.recover_submitted_integration_boundary_command(
                session_id=caller.session_id,
                predecessor_task_id=predecessor_task_id,
                successor_task_id=successor_task_id,
                integration_task_id=integration_task_id,
                replacement_task_id=replacement_task_id,
                expected_dependency_task_ids=expected_dependency_task_ids,
                expected_required_contribution_task_ids=(expected_required_contribution_task_ids),
                reason=reason,
                request_id=request_id,
                git_base_commit_sha=git_base_commit_sha,
                git_base_branch=git_base_branch,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_recover_submitted_integration_boundary,
        name=TEAM_RECOVER_SUBMITTED_INTEGRATION_BOUNDARY_TOOL,
        description=(
            "Recover a submitted integration boundary whose immutable contribution "
            "frontier contains an already-cancelled predecessor. The predecessor's "
            "recorded successor must match exactly. Supply the frozen dependency and "
            "required-contribution sets plus a caller-chosen fresh boundary ID. Team "
            "creates a new boundary originating from the submitted one and never "
            "rewrites old deliverables, reviews, or Git contexts. Git-provenance "
            "boundaries require an explicit full pinned base commit; non-Git "
            "boundaries omit Git fields. Only the project lead or Team manager may "
            "run this operation. Exact semantic replay is a no-op."
        ),
    )

    def team_report_blocker(
        source_kind: str,
        source_id: str,
        summary: str,
        request_id: str,
        details: str | None = None,
        reason_code: str = "unspecified",
        priority: str = "normal",
        project_id: str | None = None,
        task_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_REPORT_BLOCKER_TOOL,
        )
        try:
            _state, result = blocker_commands.report_blocker_command(
                session_id=caller.session_id,
                source_kind=source_kind,
                source_id=source_id,
                summary=summary,
                details=details,
                reason_code=reason_code,
                priority=priority,
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
        team_report_blocker,
        name=TEAM_REPORT_BLOCKER_TOOL,
        description=(
            "Report an explicit cannot-proceed condition and wake the nearest available "
            "superior as its accountable owner."
        ),
    )

    def team_update_blocker(
        blocker_id: str,
        action: str,
        note: str,
        request_id: str,
        delegate_agent_id: str | None = None,
        follow_up_seconds: int | None = None,
        resolution_refs: list[str] | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_UPDATE_BLOCKER_TOOL,
        )
        try:
            _state, result = blocker_commands.update_blocker_command(
                session_id=caller.session_id,
                blocker_id=blocker_id,
                action=action,
                note=note,
                delegate_agent_id=delegate_agent_id,
                follow_up_seconds=follow_up_seconds,
                resolution_refs=resolution_refs,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_update_blocker,
        name=TEAM_UPDATE_BLOCKER_TOOL,
        description=(
            "Record blocker progress, delegate locally, return delegated work, resolve, "
            "or escalate one reporting level. Manual resolution is limited to active "
            "agents on the blocker's reporter-to-manager escalation line."
        ),
    )

    def team_start_dependency_composition_recovery(
        blocker_id: str,
        resolver_agent_id: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_START_DEPENDENCY_COMPOSITION_RECOVERY_TOOL,
        )
        try:
            _state, result = blocker_commands.start_dependency_composition_recovery_command(
                session_id=caller.session_id,
                blocker_id=blocker_id,
                resolver_agent_id=resolver_agent_id,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_start_dependency_composition_recovery,
        name=TEAM_START_DEPENDENCY_COMPOSITION_RECOVERY_TOOL,
        description=(
            "Start the typed recovery for a deterministic dependency Git composition "
            "conflict and assign its immutable, review-gated merge task to an active "
            "project agent. Only agents on the blocker's escalation line may start it."
        ),
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
            _state, result = task_update_commands.update_goal_command(
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
        scope: Literal["agent", "subtree"] = "subtree",
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
            _state, result = agent_control_commands.stand_down_agent_command(
                session_id=caller.session_id,
                agent_id=agent_id,
                reason=reason,
                request_id=request_id,
                scope=scope,
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
        description=(
            "Stand down a direct subordinate. scope='subtree' (the default) also "
            "holds every subordinate; scope='agent' holds only the named agent."
        ),
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
            _state, result = agent_control_commands.revoke_stand_down_command(
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
    deliverable_commands: TeamDeliverableSubmissionCommandService,
    review_commands: TeamReviewCommandService,
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
        branch_context_id: str | None = None,
        git_refs: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        outcomes: list[dict[str, Any]] | None = None,
        source_deliverable_ids: list[str] | None = None,
        completion: dict[str, Any] | None = None,
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
            _state, result = deliverable_commands.submit_deliverable_command(
                session_id=caller.session_id,
                id=id,
                task_id=task_id,
                summary=summary,
                supersedes_deliverable_id=supersedes_deliverable_id,
                artifact_paths=artifact_paths,
                artifact_refs=artifact_refs,
                artifact_space_ref=artifact_space_ref,
                branch_context_id=branch_context_id,
                git_refs=git_refs,
                verification=verification,
                outcomes=outcomes,
                source_deliverable_ids=source_deliverable_ids,
                completion=completion,
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
            "Submit a work product for configured acceptance or review routing. "
            "This always moves the task to waiting_for_review. When the caller has "
            "a direct superior, it sends that superior a deliverable notice; when "
            "the caller has no superior, workflow or operator policy must attach "
            "the required review. Finish administrative, delegation, "
            "coordination, or acknowledgement-only work with team_update_task "
            "state=finished instead. Publishes any artifact_paths from /artifacts "
            "before recording state. Optional "
            "verification records are optional diagnostic reports with status "
            "passed, failed, partial, not_run, or unknown and a summary; they never "
            "gate acceptance or release intake. Git deliverables must include "
            "branch_context_id; git_refs are provenance only and must match that "
            "context. git_refs accepts exactly branch_context_id, agent_branch, "
            "and commit_sha; use commit_sha, not commit, to pin the submitted "
            "candidate. Before submission, publish the local commit to the managed "
            "agent branch with git push-agent and require the push to succeed. "
            "Call team_publish_review_branch separately only when a "
            "durable review branch is needed. Set supersedes_deliverable_id when "
            "reworking a rejected or blocked same-task deliverable; its open "
            "findings carry forward. Use team_submit_deliverable_for_review only "
            "when explicit team, role, task, or operator manual-routing guidance "
            "requires the caller to select reviewers. Accepted-deliverable recovery "
            "must use that manual tool. "
            "Requires task_id, summary, "
            "and request_id."
        ),
    )

    def team_decide_deliverable(
        deliverable_id: str,
        decision: Literal["accepted", "rejected"],
        request_id: str,
        reason: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_DECIDE_DELIVERABLE_TOOL,
        )
        try:
            _state, result = deliverable_commands.decide_deliverable_command(
                session_id=caller.session_id,
                deliverable_id=deliverable_id,
                decision=decision,
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
        team_decide_deliverable,
        name=TEAM_DECIDE_DELIVERABLE_TOOL,
        description=(
            "Accept or reject a deliverable handed directly to the caller for "
            "superior acceptance. Only the superior recorded on that handoff can "
            "decide it. Acceptance can complete the task; rejection requires a "
            "concrete reason and returns the task to its owner for rework. Include "
            "the matching deliverable_notice id in handled_inbox_item_ids. Do not "
            "use this for a deliverable with formal review requests; use "
            "team_decide_review for an assigned review instead. Requires "
            "deliverable_id, decision, and request_id."
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
            _state, result = deliverable_commands.submit_deliverable_for_review_command(
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
            "verification status and git_refs. Git deliverables must include "
            "deliverable.branch_context_id; git_refs do not publish a review "
            "branch by themselves and must match that context. "
            "deliverable.git_refs accepts exactly branch_context_id, agent_branch, "
            "and commit_sha; use commit_sha, not commit, to pin the submitted "
            "candidate. Before submission, publish the local commit to the managed "
            "agent branch with git push-agent and require the push to succeed. "
            "A rework may set "
            "deliverable.supersedes_deliverable_id to a rejected, blocked, or "
            "explicitly reopened accepted same-task predecessor; its open findings "
            "carry forward and fresh review requests are always created."
        ),
    )

    def team_link_deliverable_replacement(
        predecessor_deliverable_id: str,
        replacement_deliverable_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL,
        )
        try:
            _state, result = deliverable_commands.link_deliverable_replacement_command(
                session_id=caller.session_id,
                predecessor_deliverable_id=predecessor_deliverable_id,
                replacement_deliverable_id=replacement_deliverable_id,
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
        team_link_deliverable_replacement,
        name=TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL,
        description=(
            "Record immutable structured lineage from a rejected or blocked "
            "deliverable to an accepted replacement in the same project. This "
            "does not rewrite either review decision. Project leads and team "
            "managers may use it across tasks. Requires predecessor_deliverable_id, "
            "replacement_deliverable_id, reason, and request_id."
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
            _state, result = review_commands.request_review_command(
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
        block_kind: str | None = None,
        criteria: list[dict[str, Any]] | None = None,
        finding_resolutions: list[dict[str, Any]] | None = None,
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
            _state, result = review_commands.decide_review_command(
                session_id=caller.session_id,
                review_request_id=review_request_id,
                state=state,
                block_kind=block_kind,
                findings=findings,
                criteria=criteria,
                finding_resolutions=finding_resolutions,
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
            "decision. Decisions are immutable. For release reviews, rejected means "
            "the candidate failed review; blocked is reserved for a non-candidate "
            "process or evidence-access blocker and requires a finding. For blocked "
            "decisions, block_kind may identify dependency, input_unavailable, policy, "
            "system, or unspecified. An accepted review must use finding_resolutions "
            "for every open carried finding owned by its profile or reviewer role; "
            "criterion entries may include optional evidence_refs; the immutable "
            "repository snapshot is server-owned provenance rather than reviewer-supplied "
            "evidence. Agent-reported verification and links are diagnostic only; "
            "acceptance cascades only when all current reviews are accepted and no "
            "finding remains open. Requires review_request_id, state, and request_id."
        ),
    )

    def team_retry_review(
        review_request_id: str,
        reason: str,
        request_id: str,
        reviewer_agent_id: str | None = None,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_RETRY_REVIEW_TOOL,
        )
        try:
            _state, result = review_commands.retry_review_command(
                session_id=caller.session_id,
                review_request_id=review_request_id,
                reason=reason,
                reviewer_agent_id=reviewer_agent_id,
                request_id=request_id,
                handled_inbox_item_ids=handled_inbox_item_ids,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_command_mcp_result(result, caller=caller, context=context)

    server.add_tool(
        team_retry_review,
        name=TEAM_RETRY_REVIEW_TOOL,
        description=(
            "Create a new pending review attempt that supersedes a current blocked "
            "non-release review without rewriting its immutable decision. The producer, "
            "supervisor, project lead, or team manager may retry it and optionally choose "
            "another eligible reviewer. A Git-backed retry fails closed unless its pinned "
            "candidate still matches the server-resolved task branch; submit a replacement "
            "deliverable when it does not. Requires review_request_id, reason, and request_id."
        ),
    )

    def team_withdraw_review(
        review_request_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_WITHDRAW_REVIEW_TOOL,
        )
        try:
            _state, result = review_commands.withdraw_review_command(
                session_id=caller.session_id,
                review_request_id=review_request_id,
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
        team_withdraw_review,
        name=TEAM_WITHDRAW_REVIEW_TOOL,
        description=(
            "Withdraw a mistaken or unnecessary current pending non-release review "
            "without recording a decision. The requester, deliverable producer, "
            "producer's supervisor, project lead, or team manager may withdraw it. "
            "History is preserved. Requires review_request_id, reason, and request_id."
        ),
    )

    def team_retire_review(
        review_request_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_RETIRE_REVIEW_TOOL,
        )
        try:
            _state, result = review_commands.retire_review_command(
                session_id=caller.session_id,
                review_request_id=review_request_id,
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
        team_retire_review,
        name=TEAM_RETIRE_REVIEW_TOOL,
        description=(
            "Retire a current accepted, rejected, or blocked non-release review. "
            "Only a project lead or team manager may do this. The decision and "
            "history remain; open findings are not resolved or waived. Requires "
            "review_request_id, reason, and request_id."
        ),
    )


def _register_slice8_tools(
    server: FastMCP,
    command_service: TeamGitReleaseCommandService,
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
        supersedes_release_package_id: str | None = None,
        post_release_feedback_case_id: str | None = None,
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
                supersedes_release_package_id=supersedes_release_package_id,
                post_release_feedback_case_id=post_release_feedback_case_id,
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
            "deliverables. Candidate verification runs afterward on the frozen "
            "candidate and is the authoritative build/test result. Requires "
            "project_id, deliverable_ids, and request_id."
        ),
    )

    def team_decide_initial_release(
        deliverable_ids: list[str],
        decision: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_DECIDE_INITIAL_RELEASE_TOOL,
        )
        try:
            _state, result = command_service.decide_initial_release_command(
                session_id=caller.session_id,
                deliverable_ids=deliverable_ids,
                decision=decision,
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
        team_decide_initial_release,
        name=TEAM_DECIDE_INITIAL_RELEASE_TOOL,
        description=(
            "Record a project lead or team manager decision that pending root "
            "deliverables are not planned for an initial release, or reopen an "
            "earlier not-planned decision. Requires deliverable_ids, decision "
            "(not_planned or reopen), reason, and request_id."
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
            "release package's failed feature or release promotion. Requires "
            "release_package_id, agent_branch, "
            "commit_sha, and request_id."
        ),
    )

    def team_retry_release_gate(
        release_package_id: str,
        gate_kind: str,
        gate_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_action_context(
            session_id=session_id,
            tool_name=TEAM_RETRY_RELEASE_GATE_TOOL,
        )
        try:
            _state, result = command_service.retry_release_gate_command(
                session_id=caller.session_id,
                release_package_id=release_package_id,
                gate_kind=gate_kind,
                gate_id=gate_id,
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
        team_retry_release_gate,
        name=TEAM_RETRY_RELEASE_GATE_TOOL,
        description=(
            "Retry the current gate for a blocked release against its unchanged pinned "
            "candidate. Only infrastructure-failed or timed-out verification and "
            "process-blocked reviews are eligible. A verification retry requires concrete "
            "evidence that the failure recovered and a reason stating what changed; "
            "otherwise escalate it. An agent cannot retry a failed agent-requested "
            "verification run again."
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


def _team_mcp_managed_git_admin(context: AddonContext):
    managed_git = getattr(context.service, "managed_git_admin", None)
    if managed_git is None:
        from evander.admin.composition import build_admin_runtime

        managed_git = build_admin_runtime(context.runtime_dir).managed_git_admin
    return managed_git


def _team_mcp_git_candidate_commit_resolver(
    context: AddonContext,
    *,
    managed_git=None,
):
    if managed_git is None:
        managed_git = _team_mcp_managed_git_admin(context)

    def _resolve(
        *,
        managed_repo_id: str,
        directory_path: str,
        agent_branch: str,
        required_ancestor_shas: Sequence[str] | None = None,
    ) -> str | None:
        resolve_branch_commit = getattr(managed_git, "resolve_branch_commit", None)
        if not callable(resolve_branch_commit):
            raise RuntimeError("managed Git branch resolution is unavailable")
        commit_sha = resolve_branch_commit(
            managed_repo_id,
            directory_path=directory_path,
            branch=agent_branch,
        )
        if required_ancestor_shas and commit_sha is not None:
            verify_ancestry = getattr(managed_git, "verify_branch_commit_ancestry", None)
            if not callable(verify_ancestry):
                raise RuntimeError("managed Git ancestry verification is unavailable")
            verify_ancestry(
                managed_repo_id,
                directory_path=directory_path,
                branch=agent_branch,
                commit_sha=commit_sha,
                required_ancestor_shas=required_ancestor_shas,
            )
        return commit_sha

    return _resolve


def _team_mcp_git_candidate_snapshot_pinner(
    context: AddonContext,
    *,
    managed_git=None,
):
    if managed_git is None:
        managed_git = _team_mcp_managed_git_admin(context)

    def _pin(
        *,
        managed_repo_id: str,
        directory_path: str,
        deliverable_id: str,
        commit_sha: str,
        source_ref: str | None,
        expected_pinned_ref: str,
    ) -> Mapping[str, Any]:
        pin_snapshot = getattr(managed_git, "pin_team_review_snapshot", None)
        if not callable(pin_snapshot):
            raise RuntimeError("managed Git candidate snapshot pinning is unavailable")
        return pin_snapshot(
            managed_repo_id,
            directory_path,
            review_id=team_deliverable_snapshot_binding_id(deliverable_id),
            commit_sha=commit_sha,
            source_ref=source_ref,
            pinned_ref=expected_pinned_ref,
        )

    return _pin


def _team_mcp_git_default_branch_resolver(
    context: AddonContext,
    *,
    managed_git=None,
):
    if managed_git is None:
        managed_git = _team_mcp_managed_git_admin(context)

    def _resolve(
        *,
        managed_repo_id: str,
        directory_path: str,
    ) -> object:
        get_repository = getattr(managed_git, "get_repository", None)
        if not callable(get_repository):
            raise RuntimeError("managed Git repository lookup is unavailable")
        record = get_repository(
            managed_repo_id,
            directory_path=directory_path,
        )
        if not isinstance(record, Mapping):
            raise RuntimeError("managed Git repository lookup returned an invalid record")
        return record.get("default_branch")

    return _resolve


def _team_mcp_dependency_recovery_commit_verifier(
    context: AddonContext,
    *,
    managed_git=None,
):
    if managed_git is None:
        managed_git = _team_mcp_managed_git_admin(context)

    def _verify(recovery, deliverable_id: str, branch: str, commit_sha: str) -> None:
        del deliverable_id
        verify_ancestry = getattr(managed_git, "verify_branch_commit_ancestry", None)
        if not callable(verify_ancestry):
            raise RuntimeError("managed Git ancestry verification is unavailable")
        verify_ancestry(
            recovery.managed_repo_id,
            directory_path=recovery.directory_path,
            branch=branch,
            commit_sha=commit_sha,
            required_ancestor_shas=[
                recovery.base_commit_sha,
                *[source.commit_sha for source in recovery.dependency_git_sources],
            ],
        )

    return _verify


def _team_mcp_memory_artifact_publisher(context: AddonContext):
    def _publish(
        *,
        agent: Any,
        team_id: str,
        project_id: str | None,
        memory_id: str,
        dispatch_id: str,
        source_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        from .providers import _publish_memory_artifact_bundle

        runtime_context = EffectRuntimeContext(
            resolved_config={"runtime_dir": str(context.runtime_dir)},
            admin_service=context.service,
        )
        agent_payload = {
            "id": getattr(agent, "id", None),
            "session_id": getattr(agent, "session_id", None),
        }
        return _publish_memory_artifact_bundle(
            runtime_context,
            agent=agent_payload,
            team_id=team_id,
            project_id=project_id,
            memory_id=memory_id,
            dispatch_id=dispatch_id,
            source_paths=list(source_paths or []),
        )

    return _publish


def _register_slice9_tools(
    server: FastMCP,
    command_service: TeamEphemeralCommandService,
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


def _register_read_tools(
    server: FastMCP,
    resolver: TeamMcpContextResolver,
    context: AddonContext,
) -> None:
    def team_list_tasks(
        session_id: str | None = None,
        project_id: str | None = None,
        owner_agent_id: str | None = None,
        state: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_TASKS_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_tasks(
                project_id=project_id,
                owner_agent_id=owner_agent_id,
                state=state,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_tasks,
        name=TEAM_LIST_TASKS_TOOL,
        description="List visible Team tasks as compact rows with pagination.",
    )

    def team_get_task(task_id: str, session_id: str | None = None) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_TASK_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_task(task_id),
            caller=caller,
            record_name="task",
            record_id=task_id,
        )

    server.add_tool(
        team_get_task,
        name=TEAM_GET_TASK_TOOL,
        description="Get one visible Team task with derived graph context.",
    )

    def team_list_inbox(
        session_id: str | None = None,
        state: str | None = None,
        include_handled: bool = False,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_INBOX_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_inbox(
                state=state,
                include_handled=include_handled,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_inbox,
        name=TEAM_LIST_INBOX_TOOL,
        description="List visible Team inbox items as compact rows with pagination.",
    )

    def team_get_inbox_item(
        inbox_item_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_INBOX_ITEM_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_inbox_item(inbox_item_id),
            caller=caller,
            record_name="inbox_item",
            record_id=inbox_item_id,
        )

    server.add_tool(
        team_get_inbox_item,
        name=TEAM_GET_INBOX_ITEM_TOOL,
        description="Get one visible Team inbox item.",
    )

    def team_list_blockers(
        session_id: str | None = None,
        team_id: str | None = None,
        project_id: str | None = None,
        owner_agent_id: str | None = None,
        delegate_agent_id: str | None = None,
        state: str | None = None,
        source_kind: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_BLOCKERS_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_blockers(
                team_id=team_id,
                project_id=project_id,
                owner_agent_id=owner_agent_id,
                delegate_agent_id=delegate_agent_id,
                state=state,
                source_kind=source_kind,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_blockers,
        name=TEAM_LIST_BLOCKERS_TOOL,
        description="List visible Team blockers and their current routing state.",
    )

    def team_get_blocker(
        blocker_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_BLOCKER_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_blocker(blocker_id),
            caller=caller,
            record_name="blocker",
            record_id=blocker_id,
        )

    server.add_tool(
        team_get_blocker,
        name=TEAM_GET_BLOCKER_TOOL,
        description="Get one visible Team blocker with its complete event history.",
    )

    def team_list_cases(
        session_id: str | None = None,
        project_id: str | None = None,
        owner_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_CASES_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_cases(
                project_id=project_id,
                owner_agent_id=owner_agent_id,
                status=status,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_cases,
        name=TEAM_LIST_CASES_TOOL,
        description="List visible Team cases as compact rows with pagination.",
    )

    def team_get_case(case_id: str, session_id: str | None = None) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_CASE_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_case(case_id),
            caller=caller,
            record_name="case",
            record_id=case_id,
        )

    server.add_tool(
        team_get_case,
        name=TEAM_GET_CASE_TOOL,
        description="Get one visible Team case.",
    )

    def team_list_deliverables(
        session_id: str | None = None,
        task_id: str | None = None,
        producer_agent_id: str | None = None,
        state: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_DELIVERABLES_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_deliverables(
                task_id=task_id,
                producer_agent_id=producer_agent_id,
                state=state,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_deliverables,
        name=TEAM_LIST_DELIVERABLES_TOOL,
        description="List visible Team deliverables as compact rows with pagination.",
    )

    def team_get_deliverable(
        deliverable_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_DELIVERABLE_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_deliverable(deliverable_id),
            caller=caller,
            record_name="deliverable",
            record_id=deliverable_id,
        )

    server.add_tool(
        team_get_deliverable,
        name=TEAM_GET_DELIVERABLE_TOOL,
        description="Get one visible Team deliverable.",
    )

    def team_import_artifact(
        artifact_ref: str,
        artifact_id: str = "bundle",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_IMPORT_ARTIFACT_TOOL,
        )
        from .providers import _import_team_artifact_for_session

        try:
            imported = _import_team_artifact_for_session(
                _team_artifact_import_runtime_context(context, caller.session_id),
                artifact_ref=artifact_ref,
                artifact_id=artifact_id,
                session_id=caller.session_id,
                subject=f"team-agent:{caller.agent_id}",
                team_id=caller.agent.team_id,
            )
        except (FileNotFoundError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return _team_mcp_result_with_attention(
            team_mcp_success(
                revision=caller.state.revision,
                progress_made=False,
                agent_id=caller.agent_id,
                **imported,
            ),
            caller=caller,
            context=context,
        )

    server.add_tool(
        team_import_artifact,
        name=TEAM_IMPORT_ARTIFACT_TOOL,
        description=_MCP_TOOL_CATALOG_DESCRIPTIONS[TEAM_IMPORT_ARTIFACT_TOOL],
    )

    def team_list_review_requests(
        session_id: str | None = None,
        deliverable_id: str | None = None,
        reviewer_agent_id: str | None = None,
        state: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_REVIEW_REQUESTS_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_review_requests(
                deliverable_id=deliverable_id,
                reviewer_agent_id=reviewer_agent_id,
                state=state,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_review_requests,
        name=TEAM_LIST_REVIEW_REQUESTS_TOOL,
        description="List visible Team review requests as compact rows with pagination.",
    )

    def team_get_review_request(
        review_request_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_REVIEW_REQUEST_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_review_request(review_request_id),
            caller=caller,
            record_name="review_request",
            record_id=review_request_id,
        )

    server.add_tool(
        team_get_review_request,
        name=TEAM_GET_REVIEW_REQUEST_TOOL,
        description="Get one visible Team review request.",
    )

    def team_list_release_packages(
        session_id: str | None = None,
        project_id: str | None = None,
        state: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_RELEASE_PACKAGES_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_release_packages(
                project_id=project_id,
                state=state,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_release_packages,
        name=TEAM_LIST_RELEASE_PACKAGES_TOOL,
        description="List visible Team release packages as compact rows with pagination.",
    )

    def team_get_release_package(
        release_package_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_RELEASE_PACKAGE_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_release_package(release_package_id),
            caller=caller,
            record_name="release_package",
            record_id=release_package_id,
        )

    server.add_tool(
        team_get_release_package,
        name=TEAM_GET_RELEASE_PACKAGE_TOOL,
        description="Get one visible Team release package.",
    )

    def team_list_agents(
        session_id: str | None = None,
        team_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_AGENTS_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_agents(
                team_id=team_id,
                project_id=project_id,
                status=status,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_agents,
        name=TEAM_LIST_AGENTS_TOOL,
        description="List visible Team agents as compact rows with pagination.",
    )

    def team_get_agent(agent_id: str, session_id: str | None = None) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_AGENT_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_agent(agent_id),
            caller=caller,
            record_name="agent",
            record_id=agent_id,
        )

    server.add_tool(
        team_get_agent,
        name=TEAM_GET_AGENT_TOOL,
        description="Get one visible Team agent.",
    )

    def team_get_my_identity(session_id: str | None = None) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_MY_IDENTITY_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_my_identity(),
            caller=caller,
            record_name="identity",
            record_id=caller.agent.id if caller.agent is not None else "self",
        )

    server.add_tool(
        team_get_my_identity,
        name=TEAM_GET_MY_IDENTITY_TOOL,
        description="Get the calling Team agent's complete identity and role context.",
    )

    def team_get_current_dispatch_context(
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
        )
        dispatch_id = caller.active_dispatch_id or "current"
        return _team_read_get_result(
            _read_service(caller).get_current_dispatch_context(),
            caller=caller,
            record_name="dispatch",
            record_id=dispatch_id,
        )

    server.add_tool(
        team_get_current_dispatch_context,
        name=TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
        description=(
            "Get the calling agent's active immutable dispatch scope and hydrate "
            "the scoped task, inbox, blocker, review, project, and Git records."
        ),
    )

    def team_list_projects(
        session_id: str | None = None,
        team_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_PROJECTS_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_projects(
                team_id=team_id,
                status=status,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_projects,
        name=TEAM_LIST_PROJECTS_TOOL,
        description="List visible Team projects as compact rows with pagination.",
    )

    def team_get_project(project_id: str, session_id: str | None = None) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_PROJECT_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_project(project_id),
            caller=caller,
            record_name="project",
            record_id=project_id,
        )

    server.add_tool(
        team_get_project,
        name=TEAM_GET_PROJECT_TOOL,
        description="Get one visible Team project.",
    )

    def team_list_outcome_kinds(
        session_id: str | None = None,
        project_id: str | None = None,
        team_id: str | None = None,
        include_archived: bool = False,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_OUTCOME_KINDS_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_outcome_kinds(
                project_id=project_id,
                team_id=team_id,
                include_archived=include_archived,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_outcome_kinds,
        name=TEAM_LIST_OUTCOME_KINDS_TOOL,
        description=(
            "List the effective, defined outcome vocabulary for a visible "
            "project or the team-wide catalog."
        ),
    )

    def team_get_outcome_kind(
        definition_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_GET_OUTCOME_KIND_TOOL,
        )
        return _team_read_get_result(
            _read_service(caller).get_outcome_kind(definition_id),
            caller=caller,
            record_name="outcome_kind",
            record_id=definition_id,
        )

    server.add_tool(
        team_get_outcome_kind,
        name=TEAM_GET_OUTCOME_KIND_TOOL,
        description=(
            "Get one visible outcome kind definition including its durable "
            "reference and evidence requirements."
        ),
    )

    def team_list_runtime_attention(
        session_id: str | None = None,
        kind: str | None = None,
        severity: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_LIST_RUNTIME_ATTENTION_TOOL,
        )
        return _team_read_list_result(
            _read_service(caller).list_runtime_attention(
                kind=kind,
                severity=severity,
                limit=limit,
                cursor=cursor,
                offset=offset,
            ),
            caller=caller,
        )

    server.add_tool(
        team_list_runtime_attention,
        name=TEAM_LIST_RUNTIME_ATTENTION_TOOL,
        description="List visible Team runtime attention rows with pagination.",
    )

    def team_search_memory(
        session_id: str | None = None,
        query_text: str | None = None,
        team_id: str | None = None,
        project_id: str | None = None,
        codebase_id: str | None = None,
        agent_id: str | None = None,
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
        tags_any: list[str] | None = None,
        semantic: bool = False,
        limit: int = 20,
        offset: int = 0,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name=TEAM_SEARCH_MEMORY_TOOL,
        )
        effective_team_id = team_id or caller.agent.team_id
        if effective_team_id is None:
            return team_mcp_failure(
                revision=caller.state.revision,
                agent_id=caller.agent_id,
                errors="team_search_memory requires a team_id for teamless agents",
                correction_context=caller.correction_context(),
            )
        semantic_error = None
        if semantic and query_text:
            try:
                semantic_result = TeamMemorySemanticService(
                    QueryRuntimeContext(
                        resolved_config={"runtime_dir": str(context.runtime_dir)},
                        admin_service=context.service,
                    )
                ).semantic_search(
                    caller.state,
                    query_text=query_text,
                    actor=caller.agent,
                    team_id=effective_team_id,
                    project_id=project_id,
                    codebase_id=codebase_id,
                    agent_id=agent_id,
                    kinds=kinds,
                    statuses=statuses,
                    tags_any=tags_any,
                    limit=limit,
                    score_threshold=score_threshold,
                )
                return _team_mcp_result_with_attention(
                    team_mcp_success(
                        revision=caller.state.revision,
                        progress_made=False,
                        agent_id=caller.agent_id,
                        semantic=True,
                        **semantic_result,
                    ),
                    caller=caller,
                    context=context,
                )
            except Exception as exc:
                semantic_error = str(exc)
        result = list_memory_records(
            caller.state,
            actor=caller.agent,
            team_id=effective_team_id,
            project_id=project_id,
            codebase_id=codebase_id,
            agent_id=agent_id,
            kinds=kinds,
            statuses=statuses,
            tags_any=tags_any,
            query_text=query_text,
            limit=limit,
            offset=offset,
        )
        records = [
            memory_record_payload(memory, include_body=False) for memory in result.pop("records")
        ]
        return _team_mcp_result_with_attention(
            team_mcp_success(
                revision=caller.state.revision,
                progress_made=False,
                agent_id=caller.agent_id,
                semantic=False,
                semantic_error=semantic_error,
                records=records,
                **result,
            ),
            caller=caller,
            context=context,
        )

    server.add_tool(
        team_search_memory,
        name=TEAM_SEARCH_MEMORY_TOOL,
        description=(
            "Explicitly search visible Team memory. Returns compact records only; "
            "use filters to keep recall scoped to the current project or codebase."
        ),
    )


def _read_service(caller: Any) -> TeamReadService:
    return TeamReadService(state=caller.state, actor=caller.agent)


def _team_read_list_result(result: Mapping[str, Any], *, caller: Any) -> dict[str, Any]:
    return _team_mcp_result_with_attention(
        team_mcp_success(
            revision=caller.state.revision,
            progress_made=False,
            agent_id=caller.agent_id,
            **dict(result),
        ),
        caller=caller,
    )


def _team_read_get_result(
    record: Mapping[str, Any] | None,
    *,
    caller: Any,
    record_name: str,
    record_id: str,
) -> dict[str, Any]:
    if record is None:
        return team_mcp_failure(
            revision=caller.state.revision,
            agent_id=caller.agent_id,
            errors=f"{record_name} {record_id} was not found or is outside the visible scope",
            correction_context=caller.correction_context(),
            record_name=record_name,
            record_id=record_id,
        )
    return _team_mcp_result_with_attention(
        team_mcp_success(
            revision=caller.state.revision,
            progress_made=False,
            agent_id=caller.agent_id,
            record=dict(record),
        ),
        caller=caller,
    )


def _team_command_mcp_result(
    result: dict[str, Any],
    *,
    caller: Any,
    context: AddonContext | None = None,
) -> dict[str, Any]:
    payload = dict(result)
    if context is not None:
        completed_outbox_ids: list[str] = []
        wake_request_events = _wake_request_events(payload.get("events"))
        try:
            published_event_ids = _publish_team_events(context, wake_request_events)
        except Exception as exc:
            if wake_request_events:
                payload["event_publish_error"] = str(exc)
        else:
            if published_event_ids:
                payload["published_event_ids"] = published_event_ids
                try:
                    completed_outbox_ids = _complete_published_wake_outbox_records(
                        context,
                        payload=payload,
                        wake_request_events=wake_request_events,
                    )
                except Exception as exc:
                    payload["outbox_ack_error"] = str(exc)
                else:
                    if completed_outbox_ids:
                        payload["completed_outbox_record_ids"] = completed_outbox_ids
        raw_outbox_ids = payload.get("outbox_record_ids")
        outbox_ids = (
            {text for record_id in raw_outbox_ids if (text := str(record_id).strip())}
            if isinstance(raw_outbox_ids, list)
            else set()
        )
        pending_publication_ids = sorted(outbox_ids - set(completed_outbox_ids))
        publication_wake = request_team_outbox_publication_for_records(
            context,
            outbox_record_ids=pending_publication_ids,
        )
        if publication_wake is not None:
            payload["outbox_publication_wake"] = publication_wake
    payload.setdefault("agent_id", caller.agent_id)
    payload.setdefault("governance_group_id", None)
    payload.setdefault("assignment_id", None)
    payload.setdefault("error", None)
    if payload.get("errors") and payload.get("error") is None:
        first = payload["errors"][0]
        payload["error"] = first.get("message") if isinstance(first, dict) else str(first)
    return _team_mcp_result_with_attention(payload, caller=caller, context=context)


def _team_mcp_result_with_attention(
    result: Mapping[str, Any],
    *,
    caller: Any,
    context: AddonContext | None = None,
) -> dict[str, Any]:
    payload = dict(result)
    agent_id = getattr(caller, "agent_id", None)
    dispatch_id = getattr(caller, "active_dispatch_id", None)
    repository = getattr(caller, "team_repository", None)
    if repository is None and context is not None:
        repository = TeamStateRepository(context.runtime_dir)
    if not agent_id or not dispatch_id or not isinstance(repository, TeamStateRepository):
        return payload
    try:
        reconcile_operator_chat_steering_deliveries(repository)
        state, messages = TeamChatService(repository).list_pending_operator_attention_messages(
            agent_id=agent_id,
            dispatch_id=dispatch_id,
        )
        queued_message_ids = [
            message.id for message in messages if message.status == "queued"
        ]
        if queued_message_ids:
            state, _delivered_messages = TeamChatService(
                repository
            ).mark_operator_attention_messages_delivered(
                agent_id=agent_id,
                dispatch_id=dispatch_id,
                message_ids=queued_message_ids,
            )
            messages = [
                state.conversation_messages[message.id]
                for message in messages
                if message.id in state.conversation_messages
            ]
    except Exception as exc:  # Attention must never hide the primary tool result.
        payload["attention_error"] = str(exc)
        return payload
    if not messages:
        return payload
    payload["revision"] = state.revision
    payload["attention"] = {
        "message_ids": [message.id for message in messages],
        "required_action": (
            "Account for every operator message before finishing this dispatch. Reply with "
            "team_send_operator_chat_message and the exact source_message_ids, or acknowledge "
            "acted-without-reply messages with team_acknowledge_operator_chat_messages."
        ),
        "messages": [
            {
                "message_id": message.id,
                "conversation_id": message.conversation_id,
                "body": message.body,
                "created_at": message.created_at.isoformat(),
                "operator_user_id": state.conversations[
                    message.conversation_id
                ].operator_user_id,
                "operator_display_name": state.conversations[
                    message.conversation_id
                ].operator_display_name,
                "status": message.status,
            }
            for message in messages
        ],
    }
    return payload


def _wake_request_events(events: object) -> list[Mapping[str, Any]]:
    if not isinstance(events, list):
        return []
    return [
        event
        for event in events
        if isinstance(event, Mapping) and event.get("event_type") == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]


def _complete_published_wake_outbox_records(
    context: AddonContext,
    *,
    payload: Mapping[str, Any],
    wake_request_events: list[Mapping[str, Any]],
) -> list[str]:
    raw_ids = payload.get("outbox_record_ids")
    if not isinstance(raw_ids, list):
        return []
    outbox_ids = {str(record_id) for record_id in raw_ids if str(record_id).strip()}
    idempotency_keys = {
        str(event["idempotency_key"])
        for event in wake_request_events
        if event.get("idempotency_key") is not None
    }
    if not outbox_ids or not idempotency_keys:
        return []

    repo = TeamStateRepository(context.runtime_dir)
    completed_ids: list[str] = []
    for record in repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE):
        if record.id not in outbox_ids or record.idempotency_key not in idempotency_keys:
            continue
        if record.status.value != "completed":
            repo.complete_outbox_record(record_id=record.id)
        completed_ids.append(record.id)
    return completed_ids


def _publish_team_events(context: AddonContext, events: object) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = build_internal_event_bus_for_runtime(
        context.runtime_dir,
        project_dir=context.project_dir,
        base_config=context.config,
    )
    scheduled_store = build_scheduled_internal_event_store_for_runtime(
        context.runtime_dir,
        project_dir=context.project_dir,
        base_config=context.config,
    )
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        run_at = _optional_event_datetime(event.get("run_at"))
        delay_seconds = _optional_event_float(event.get("delay_seconds"))
        if run_at is not None or (delay_seconds is not None and delay_seconds > 0):
            scheduled, _created = scheduled_store.schedule_event(
                event_type=_required_event_text(event.get("event_type"), "event_type"),
                payload=dict(payload),
                correlation_key=_optional_event_text(event.get("correlation_key")),
                run_at=(
                    run_at
                    if run_at is not None
                    else datetime.now(UTC) + timedelta(seconds=delay_seconds or 0.0)
                ),
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


def _optional_event_datetime(value: object) -> datetime | None:
    text = _optional_event_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run_at must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


_TEAM_CHAT_FILE_BEGIN = "__EVANDER_TEAM_CHAT_FILE_BEGIN__"
_TEAM_CHAT_FILE_END = "__EVANDER_TEAM_CHAT_FILE_END__"
_TEAM_CHAT_FILE_READER = r"""
import base64, json, mimetypes, os, sys
requested = sys.argv[1]
max_bytes = int(sys.argv[2])
resolved = os.path.realpath(requested)
home = os.path.realpath('/home/agent')
if resolved != home and not resolved.startswith(home + os.sep):
    raise SystemExit('source_path must stay under /home/agent')
if not os.path.isfile(resolved):
    raise SystemExit('source_path must be a regular file')
size = os.path.getsize(resolved)
if max_bytes >= 0 and size > max_bytes:
    raise SystemExit(f'file exceeds Team Chat limit: {size} > {max_bytes} bytes')
with open(resolved, 'rb') as handle:
    content = handle.read(max_bytes + 1 if max_bytes >= 0 else -1)
if max_bytes >= 0 and len(content) > max_bytes:
    raise SystemExit(f'file exceeds Team Chat limit: {len(content)} > {max_bytes} bytes')
payload = {
    'source_path': resolved,
    'filename': os.path.basename(resolved),
    'mime_type': mimetypes.guess_type(resolved)[0],
    'byte_count': len(content),
    'content_base64': base64.b64encode(content).decode('ascii'),
}
print('__EVANDER_TEAM_CHAT_FILE_BEGIN__')
print(json.dumps(payload, sort_keys=True))
print('__EVANDER_TEAM_CHAT_FILE_END__')
"""


def _team_session_directory_path(context: AddonContext, session_id: str) -> str:
    repository = getattr(context.service, "session_repository", None)
    get_session = getattr(repository, "get_session", None)
    session = get_session(session_id) if callable(get_session) else None
    return str(getattr(session, "directory_path", None) or "root")


def _team_artifact_import_runtime_context(
    context: AddonContext,
    session_id: str,
) -> EffectRuntimeContext:
    from evander.admin.composition import build_admin_runtime
    from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter

    runtime = context.service
    admin_service = AdminRuntimeProviderAdapter(build_admin_runtime(context.runtime_dir))
    return EffectRuntimeContext(
        resolved_config=runtime.resolve_provider_config_for_session(session_id),
        provider_config_resolver=getattr(runtime, "provider_config", None),
        admin_service=admin_service,
        session_lookup=admin_service,
        vm_executor=admin_service,
        vm_file_copier=admin_service,
        metadata={"directory_path": _team_session_directory_path(context, session_id)},
    )


def _read_team_chat_workspace_file(
    context: AddonContext,
    *,
    session_id: str,
    source_path: str,
    max_bytes: int | None,
) -> dict[str, Any]:
    normalized_path = str(PurePosixPath(str(source_path or "").strip()))
    if not normalized_path.startswith("/home/agent/"):
        raise ValueError("source_path must be an absolute path under /home/agent")
    runtime = context.service
    resolved_config = runtime.resolve_provider_config_for_session(session_id)
    provider = AgentExecEffectProvider()
    provider.initialize(
        AgentExecEffectDefinition(
            id="team-stage-chat-attachment",
            display_name="Stage Team Chat attachment",
            type="effect:agent:exec",
            session_id=session_id,
            command=[
                "python3",
                "-c",
                _TEAM_CHAT_FILE_READER,
                normalized_path,
                str(max_bytes if max_bytes is not None else -1),
            ],
            working_directory="/home/agent",
            timeout_seconds=30,
            check=True,
        ),
        EffectRuntimeContext(
            resolved_config=resolved_config,
            provider_config_resolver=getattr(runtime, "provider_config", None),
            admin_service=runtime,
            metadata={"directory_path": _team_session_directory_path(context, session_id)},
        ),
    )
    result = provider.execute()
    stdout = str(result.get("stdout") or "")
    start = stdout.find(_TEAM_CHAT_FILE_BEGIN)
    end = stdout.find(_TEAM_CHAT_FILE_END)
    if start < 0 or end <= start:
        raise ValueError("workspace file reader did not return a Team Chat file payload")
    payload = json.loads(stdout[start + len(_TEAM_CHAT_FILE_BEGIN) : end].strip())
    if not isinstance(payload, dict):
        raise ValueError("workspace file reader returned an invalid Team Chat file payload")
    return payload


def _prepare_team_chat_agent_attachments(
    context: AddonContext,
    *,
    storage: TeamChatAttachmentStorage,
    policy_service: TeamChatAttachmentPolicyService,
    caller: Any,
    attachment_ids: list[str],
    approval_request_id: str | None,
    approval_payload: dict[str, Any],
) -> list[PreparedChatAttachment]:
    if not attachment_ids:
        return []
    policy = policy_service.effective_policy(
        _team_session_directory_path(context, caller.session_id),
        state=caller.state,
    )
    validate_attachment_count(policy, len(attachment_ids))
    staged = [
        storage.get_staged(attachment_id, session_id=caller.session_id)
        for attachment_id in attachment_ids
    ]
    for attachment in staged:
        validate_attachment_policy_content(
            policy,
            direction="agent_to_operator",
            filename=attachment.filename,
            mime_type=attachment.mime_type,
            byte_count=attachment.byte_count,
        )
    exact_payload = {
        **approval_payload,
        "attachments": [
            {
                "id": attachment.id,
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "byte_count": attachment.byte_count,
                "sha256": attachment.sha256,
            }
            for attachment in staged
        ],
    }
    if policy.agent_send_requires_approval:
        approval_id = str(approval_request_id or "").strip()
        if not approval_id:
            raise ToolError(
                "Agent Team Chat attachments require exact approval; request approval for "
                f"this payload: {json.dumps(exact_payload, sort_keys=True)}"
            )
        approval = context.service.policy.require_approved(approval_id, exact_payload)
        if approval.session_id != caller.session_id:
            raise ToolError("attachment approval belongs to another session")
        if str(getattr(approval, "tool_name", "") or "").strip() != str(
            approval_payload["tool_name"]
        ):
            raise ToolError("attachment approval is for a different tool")
    return storage.promote_staged(attachment_ids, session_id=caller.session_id)


def _delete_uncommitted_team_chat_attachments(
    *,
    repository: TeamStateRepository,
    storage: TeamChatAttachmentStorage,
    attachments: list[PreparedChatAttachment],
) -> None:
    """Delete promoted files only when current durable state has not claimed them."""

    try:
        committed_attachment_ids = set(repository.read().chat_attachments)
    except Exception as exc:  # noqa: BLE001 - cleanup must fail closed on repository errors.
        logger.warning(
            "team_chat_attachment_cleanup_state_read_failed exception_class=%s",
            exc.__class__.__name__,
        )
        return
    storage.delete_prepared(
        attachment.id for attachment in attachments if attachment.id not in committed_attachment_ids
    )
