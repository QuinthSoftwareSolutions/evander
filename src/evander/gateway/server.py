# This module intentionally retains legacy imports as compatibility exports through
# ``evander.gateway``. Keep F401 disabled until that compatibility surface is retired.
# ruff: noqa: F401

from __future__ import annotations

import base64
from contextvars import ContextVar
from inspect import signature
import json
import logging
from hashlib import sha256
import os
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlparse
from uuid import uuid4

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field as PydanticField
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from evander.addons import (
    AddonContext,
    addon_runtime_dir,
    load_addons,
    register_addon_mcp_tools,
)
from evander.admin.composition import build_admin_runtime
from evander.ado_reports import (
    DEFAULT_DONE_STATES,
    DEFAULT_EFFORT_FIELDS,
    DEFAULT_IN_PROGRESS_STATES,
    build_ado_daily_status_report_data,
)
from evander.communication_artifacts import (
    CommunicationArtifactStore,
    normalize_safe_filename,
    validate_artifact_id,
)
from evander.connectors.service import (
    ConnectorPolicyError,
    ConnectorService,
    connector_registry_from_addons,
)
from evander.host_directories.service import (
    HostDirectoryError,
    HostDirectoryPolicyError,
    HostDirectoryService,
)
from evander.communication_artifact_links import (
    CommunicationArtifactLinkError,
    communication_artifact_link_secret_env,
    communication_artifact_public_base_url,
    issue_communication_artifact_download_link,
)
from evander.config import GatewayConfig, InboundPhoneRoute, _normalize_phone_number
from evander.directories import DirectoryService
from evander.effects.providers.artifacts import ArtifactStageEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.schemas import (
    AgentCopyFilesEffectDefinition,
    AgentExecEffectDefinition,
    ArtifactStageEffectDefinition,
    VmCopyFilesItemDefinition,
)
from evander.email.http_utils import ProviderRequestError
from evander.inline_media import (
    download_url_with_limit,
    inline_media_download_from_embedded_payload,
    normalize_inline_media_provider,
    require_provider_media_url,
    sanitize_inline_media_metadata,
)
from evander.email.providers import EmailDraft, EmailDraftAttachment
from evander.issues.service import AzureDevOpsIssueInlineMediaProvider, IssueService
from evander.pr_feedback.providers import (
    AzureDevOpsFeedbackAdapter,
    GitHubFeedbackAdapter,
)
from evander.models import (
    ADO_DAILY_STATUS_REPORT_DEFAULT_MAX_ITEMS,
    AdoBacklogCreateRequest,
    AdoBacklogCreateResponse,
    AdoPrCommentActivityRequest,
    AdoPrCommentActivityResponse,
    AdoPrCommitActivityRequest,
    AdoPrCommitActivityResponse,
    AdoBacklogCommentRequest,
    AdoBacklogDeleteRequest,
    AdoBacklogDeleteResponse,
    AdoCurrentIterationBacklogRequest,
    AdoCurrentIterationBacklogResponse,
    AdoDailyStatusReportRequest,
    AdoBacklogGetRequest,
    AdoBacklogGetResponse,
    AdoBacklogQueryRequest,
    AdoBacklogQueryResponse,
    AdoBacklogStateUpdateRequest,
    AdoBacklogTagsUpdateRequest,
    AdoBacklogTextUpdateRequest,
    AdoBacklogTitleUpdateRequest,
    AdoWorkItemActivityRequest,
    AdoWorkItemActivityResponse,
    ApprovalGrantSettings,
    ApprovalPayloadMode,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    CommunicationAttachmentRef,
    CommunicationCopyArtifactToWorkspaceRequest,
    CommunicationCopyArtifactToWorkspaceResponse,
    CommunicationDownloadAttachmentRequest,
    CommunicationDownloadAttachmentResponse,
    CommunicationFileArtifact,
    DownloadInlineMediaRequest,
    DownloadInlineMediaResponse,
    DraftMessage,
    InlineMediaRef,
    ListInlineMediaRequest,
    ListInlineMediaResponse,
    CommunicationListAttachmentsRequest,
    CommunicationListAttachmentsResponse,
    CommunicationSendMessageWithAttachmentsRequest,
    CommunicationSendMessageWithAttachmentsResponse,
    CommunicationStageWorkspaceFileRequest,
    CommunicationStageWorkspaceFileResponse,
    ContactSummary,
    DiscordCommandEnvelope,
    GetPullRequestRequest,
    HasPendingDiscordMessagesRequest,
    HasPendingFeedbackRequest,
    GetPhoneCallResultRequest,
    ListContactsRequest,
    ListContactsResponse,
    ListUnreadDiscordMessagesRequest,
    ListPullRequestsRequest,
    ListUnreadFeedbackRequest,
    MakePhoneCallRequest,
    MakePhoneCallResponse,
    PostPrCommentRequest,
    PostPrFeedbackReplyRequest,
    PostPrInlineCommentRequest,
    ResolveAdoIdentityRequest,
    ResolveAdoIdentityResponse,
    RunScriptRequest,
    RunScriptResponse,
    RequestPrReviewRequest,
    CancelScheduledWakeResponse,
    ScheduleResumeRequest,
    ScheduleResumeResponse,
    StageArtifactRequest,
    StageArtifactResponse,
    SubmitPrReviewRequest,
    SubmitVoiceQueryResponseRequest,
    SubmitVoiceQueryResponseResponse,
    WakeScheduleDefinition,
    TeamsMessageEnvelope,
    McpToolName,
    ToolName,
    digest_payload,
    tool_name_value,
)
from evander.policy import (
    PolicyError,
    ToolArgumentPolicyError,
    reset_tool_approval_context,
    set_tool_approval_context,
)
from evander.retell_phone import (
    RetellWebhookSignatureError,
    retell_api_key_from_config,
    sanitize_retell_metadata,
    verify_retell_webhook_signature,
)
from evander.teams_meeting import voice_session_transcript_from_teams_events
from evander.voice_sessions import (
    VoiceQueryDelegator,
    disable_pending_voice_query_wake,
    voice_query_payload,
)
from evander.voice_sessions import (
    VoiceProjectionService,
    realtime_session_config,
    sanitize_voice_metadata,
)
from evander.runtime import RuntimeContext
from evander.services import (
    TeamsInlineMediaProvider,
    normalize_pr_feedback_provider,
    normalize_teams_mention_refs,
    normalize_teams_target,
    parse_teams_target,
    resolve_teams_send_target,
    teams_target_from_message,
)
from evander.gateway.dependencies import GatewayToolDependencies, _compat_gateway_attr
from evander.gateway.admin_tools import register_admin_tools
from evander.gateway.agent_tools import register_agent_tools
from evander.gateway.ado_tools import register_ado_tools
from evander.gateway.approval_tools import register_approval_tools
from evander.gateway.chat_tools import register_chat_tools
from evander.gateway.communication_artifact_helpers import (
    CommunicationArtifactHelpers,
    _WORKSPACE_FILE_PAYLOAD_BEGIN,
    _WORKSPACE_FILE_PAYLOAD_END,
    _WORKSPACE_FILE_READER_SCRIPT,
)
from evander.gateway.communication_attachment_helpers import (
    CommunicationAttachmentHelpers,
    _filter_discord_commands_for_session,
    _filter_teams_messages_for_session,
    _teams_cursor_for_target,
)
from evander.gateway.communication_tools import (
    CommunicationToolHelpers,
    register_communication_tools,
)
from evander.gateway.connector_tools import register_connector_tools
from evander.gateway.host_directory_tools import register_host_directory_tools
from evander.gateway.documentation_tools import register_documentation_tools
from evander.gateway.consultation_tools import register_consultation_tools
from evander.gateway.email_draft_helpers import (
    EmailDraftHelpers,
    draft_recipients_from_payload,
)
from evander.gateway.inline_media_helpers import (
    InlineMediaHelpers,
    _normalize_inline_media_source_kind,
)
from evander.gateway.issue_tools import register_issue_tools
from evander.gateway.mssql_tools import register_mssql_tools
from evander.gateway.pr_tools import register_pr_tools
from evander.gateway.reference_repository_tools import register_reference_repository_tools
from evander.gateway.semantic_tools import register_semantic_tools
from evander.gateway.visibility_helpers import (
    VisibilityHelpers,
    _require_target_session_visible_within_directory,
    _require_vm_visible_in_session_directory,
    _visible_vm_summaries_for_session,
)
from evander.gateway.approval_diagnostics import (
    _approval_gate_arguments,
    _approval_gate_blocked_diagnostic,
    _approval_gate_human_summary,
    _approval_gate_intended_effect,
    _approval_gate_payload,
    _approval_gate_pending_diagnostic,
)
from evander.gateway.request_context import (
    _first_string_in_payload,
    _request_from_context,
    _string_from_headers,
    _string_from_query_params,
    _string_from_raw_query,
)
from evander.gateway.session_context import (
    _SESSION_ID_CONTEXT_CONFLICT_MESSAGE,
    _SESSION_ID_PARAMETER_DESCRIPTION,
    _ambient_session_id_from_context,
    _ambient_session_id_from_request,
    _mcp_session_token_from_request,
    _resolve_bound_session_id,
    _resolve_session_id_from_context,
    _resolve_session_id_from_request,
)
from evander.gateway.tool_audit import (
    _TOOL_CALL_AUDIT_CONTEXT,
    _ToolCallAuditContext,
    _mark_tool_call_audited,
    _tool_call_arguments_dict,
    _tool_call_audit_fields,
    _tool_call_context,
    _tool_call_duration_ms,
)
from evander.session_auth import verify_mcp_session_proof_token, verify_mcp_session_token

logger = logging.getLogger(__name__)

_MCP_REQUEST_CONTEXT: ContextVar[Request | None] = ContextVar(
    "evander_mcp_request_context",
    default=None,
)
TeamsMentionRefsArgument = Annotated[
    list[str] | None,
    PydanticField(
        description=(
            "Optional Teams user refs to mention in a channel post, chat post, or channel thread reply. "
            "Use user:<graph-user-id-or-upn> or a bare Graph user ID, UPN, or mail address. "
            "Plain @name text in message does not notify users."
        )
    ),
]
AdoMentionRefsArgument = Annotated[
    list[str] | None,
    PydanticField(
        description=(
            "Optional Azure DevOps user refs to mention. Use an ADO identity ID, descriptor, "
            "email/UPN, or display-name search text. The gateway resolves refs and prepends "
            "provider-native mention tokens; plain @name text does not reliably notify users."
        )
    ),
]


_APPROVAL_REQUEST_ID_PARAMETER_SCHEMA = {
    "anyOf": [{"type": "string"}, {"type": "null"}],
    "default": None,
    "description": ("Optional approval request id for retrying an approval-gated tool invocation."),
    "title": "Approval Request Id",
}

_APPROVAL_REQUEST_ID_SCHEMA_EXCLUDED_TOOLS = {
    ToolName.APPROVAL_SUBMIT.value,
    ToolName.APPROVAL_CHECK.value,
}


def _session_actor_role(session: Any) -> str | None:
    metadata = session.metadata if isinstance(getattr(session, "metadata", None), dict) else {}
    for key in ("automation_role", "agent_role", "role"):
        candidate = str(metadata.get(key) or "").strip()
        if candidate:
            return candidate
    return None


def _session_actor_user_id(session: Any) -> str | None:
    metadata = session.metadata if isinstance(getattr(session, "metadata", None), dict) else {}
    for key in ("user_id", "actor_user_id"):
        candidate = str(metadata.get(key) or "").strip()
        if candidate:
            return candidate
    return None


def _tool_call_context_from_request(
    tool_name: str,
    args: dict[str, Any],
    ctx: Any | None,
    transport_session_map: dict[str, str] | None,
) -> _ToolCallAuditContext:
    request = _request_from_context(ctx)
    headers = getattr(request, "headers", None)
    query_params = getattr(request, "query_params", None)
    transport_session_id = _string_from_headers(headers, "mcp-session-id")
    argument_session_id = str(args.get("session_id") or "").strip() or None
    ambient_session_id = _ambient_session_id_from_request(
        request,
        transport_session_map=transport_session_map,
    )
    session_id = ambient_session_id or argument_session_id
    if session_id is None and isinstance(args.get("target_session_id"), str):
        candidate = str(args.get("target_session_id") or "").strip()
        session_id = candidate or None
    request_id = _string_from_headers(headers, "x-request-id", "request-id")
    correlation_id = _string_from_headers(headers, "x-correlation-id", "correlation-id")
    if request_id is None:
        request_id = _first_string_in_payload(args, ("request_id",))
    if correlation_id is None:
        correlation_id = _first_string_in_payload(args, ("correlation_id",))
    workflow_run_id = _string_from_query_params(query_params, "workflow_run_id")
    if workflow_run_id is None:
        workflow_run_id = _string_from_headers(
            headers,
            "x-evander-workflow-run-id",
            "x-workflow-run-id",
        )
    if workflow_run_id is None:
        workflow_run_id = _first_string_in_payload(args, ("workflow_run_id",))
    mcp_tool_approval_lease_id = _string_from_query_params(
        query_params,
        "mcp_tool_approval_lease_id",
        "tool_approval_lease_id",
    )
    if mcp_tool_approval_lease_id is None:
        mcp_tool_approval_lease_id = _string_from_headers(
            headers,
            "x-evander-mcp-tool-approval-lease-id",
            "x-mcp-tool-approval-lease-id",
        )
    if mcp_tool_approval_lease_id is None:
        mcp_tool_approval_lease_id = _first_string_in_payload(
            args,
            ("mcp_tool_approval_lease_id", "tool_approval_lease_id"),
        )
    return _ToolCallAuditContext(
        tool_name=tool_name,
        started_at=time.perf_counter(),
        raw_args=dict(args),
        session_id=session_id,
        argument_session_id=argument_session_id,
        ambient_session_id=ambient_session_id,
        request_id=request_id,
        correlation_id=correlation_id,
        transport_session_id=transport_session_id,
        mcp_session_token=_mcp_session_token_from_request(request),
        workflow_run_id=workflow_run_id,
        mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
    )


def _tool_policy_context_from_request(
    request: Request | None,
    transport_session_map: dict[str, str] | None = None,
) -> tuple[str | None, str | None, str | None]:
    if request is None:
        return None, None, None
    headers = getattr(request, "headers", None)
    query_params = getattr(request, "query_params", None)
    raw_query = str(getattr(getattr(request, "url", None), "query", "") or "").strip()
    session_id = _ambient_session_id_from_request(
        request,
        transport_session_map=transport_session_map,
    )
    workflow_run_id = _string_from_query_params(query_params, "workflow_run_id")
    if workflow_run_id is None:
        workflow_run_id = _string_from_raw_query(raw_query, "workflow_run_id")
    if workflow_run_id is None:
        workflow_run_id = _string_from_headers(
            headers,
            "x-evander-workflow-run-id",
            "x-workflow-run-id",
        )
    mcp_tool_approval_lease_id = _string_from_query_params(
        query_params,
        "mcp_tool_approval_lease_id",
        "tool_approval_lease_id",
    )
    if mcp_tool_approval_lease_id is None:
        mcp_tool_approval_lease_id = _string_from_raw_query(
            raw_query,
            "mcp_tool_approval_lease_id",
            "tool_approval_lease_id",
        )
    if mcp_tool_approval_lease_id is None:
        mcp_tool_approval_lease_id = _string_from_headers(
            headers,
            "x-evander-mcp-tool-approval-lease-id",
            "x-mcp-tool-approval-lease-id",
        )
    return session_id, workflow_run_id, mcp_tool_approval_lease_id


def _payload_from_result(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    return {"value": result}


def _redact_nested_value(redacted_keys: set[str], value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested_value in value.items():
            if key in redacted_keys:
                redacted[key] = "<redacted>"
                continue
            redacted[key] = _redact_nested_value(redacted_keys, nested_value)
        return redacted

    if isinstance(value, list):
        return [_redact_nested_value(redacted_keys, item) for item in value]

    return value


def _redact_args(redacted_keys: set[str], args: dict[str, Any]) -> dict[str, Any]:
    return _redact_nested_value(redacted_keys, args)


_ASSISTANT_MUTATION_TOOLS = frozenset(
    {
        ToolName.ASSISTANT_UPSERT_CONFIG,
        ToolName.ASSISTANT_CREATE_WORKFLOW,
        ToolName.ASSISTANT_RUN_SAFE_OPERATION,
    }
)


def _assistant_mutation_audit_result(
    tool_name: McpToolName | None,
    args: dict[str, Any],
    result: dict[str, Any],
    redacted_keys: set[str],
    *,
    default_status: str,
) -> dict[str, Any]:
    audit_result = _redact_nested_value(redacted_keys, dict(result))
    if not isinstance(tool_name, ToolName) or tool_name not in _ASSISTANT_MUTATION_TOOLS:
        return audit_result

    audit_result.setdefault("status", default_status)
    before_after_summary = audit_result.get("before_after_summary")
    if before_after_summary is None:
        before_after_summary = {
            "before": args.get("before"),
            "after": args.get("after"),
        }
    audit_result["before_after_summary"] = _redact_nested_value(
        redacted_keys,
        before_after_summary,
    )
    return audit_result


def create_server(context: RuntimeContext) -> FastMCP:
    server = FastMCP(
        "Evander Gateway",
        host=context.config.host,
        port=context.config.port,
        streamable_http_path="/mcp",
    )
    transport_session_map: dict[str, str] = {}
    if context.mcp_transport_bindings is not None:
        for transport_session_id, binding in context.mcp_transport_bindings.read_all().items():
            bound_session_id = str(getattr(binding, "bound_session_id", "") or "").strip()
            if bound_session_id:
                transport_session_map[transport_session_id] = bound_session_id
    setattr(server, "_evander_transport_session_map", transport_session_map)
    deps = GatewayToolDependencies.from_context(context)
    artifact_helpers = CommunicationArtifactHelpers(context, deps)
    attachment_helpers = CommunicationAttachmentHelpers(context, artifact_helpers)
    email_helpers = EmailDraftHelpers(context)
    visibility_helpers = VisibilityHelpers(context, deps.admin_runtime_factory)
    connector_service = ConnectorService(
        runtime_dir=context.config.runtime_dir,
        provider_config=context.provider_config,
        artifact_store_factory=deps.communication_artifact_store_factory,
        registry=connector_registry_from_addons(
            context.config.addon_loader,
            base_dir=context.config.runtime_dir.parent,
        ),
    )
    host_directory_service = HostDirectoryService(
        runtime_dir=context.config.runtime_dir,
        provider_config=context.provider_config,
        artifact_store_factory=deps.communication_artifact_store_factory,
    )

    def _record_visible_tools(
        *,
        transport_session_id: str | None,
        session_id: str | None,
        visible_tool_names: list[str],
        protocol_version: str | None,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
    ) -> None:
        if context.mcp_transport_bindings is None:
            return
        normalized_transport_session_id = str(transport_session_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if not normalized_transport_session_id or not normalized_session_id:
            return
        context.mcp_transport_bindings.record_visible_tools(
            transport_session_id=normalized_transport_session_id,
            bound_session_id=normalized_session_id,
            visible_tool_names=visible_tool_names,
            last_protocol_version=str(protocol_version or "").strip() or None,
            last_workflow_run_id=str(workflow_run_id or "").strip() or None,
            last_mcp_tool_approval_lease_id=(str(mcp_tool_approval_lease_id or "").strip() or None),
        )
        transport_session_map[normalized_transport_session_id] = normalized_session_id

    def redacted_keys_for_session_id(session_id: str | None) -> set[str]:
        token_redacted_keys = {
            "response_token",
            "issued_token",
            "token_hash",
            "mcp_session_token",
            "evander_mcp_session_token",
            "result_token",
            "consultation_token",
        }
        if not session_id:
            return (
                set(context.policy.policy_config.resolve_for_directory("root").redacted_keys)
                | token_redacted_keys
            )
        session = context.session_repository.get_session(session_id)
        if session is None:
            return (
                set(context.policy.policy_config.resolve_for_directory("root").redacted_keys)
                | token_redacted_keys
            )
        return (
            set(context.policy.effective_policy_for_session(session).redacted_keys)
            | token_redacted_keys
        )

    def _tool_input_parameters(raw_tool_name: str) -> dict[str, Any] | None:
        tools = getattr(server._tool_manager, "_tools", {})
        tool = tools.get(raw_tool_name) if isinstance(tools, dict) else None
        if tool is None:
            for candidate in server._tool_manager.list_tools():
                if getattr(candidate, "name", None) == raw_tool_name:
                    tool = candidate
                    break
        parameters = getattr(tool, "parameters", None)
        return parameters if isinstance(parameters, dict) else None

    def _tool_accepts_session_id(raw_tool_name: str) -> bool:
        parameters = _tool_input_parameters(raw_tool_name)
        properties = parameters.get("properties") if isinstance(parameters, dict) else None
        return isinstance(properties, dict) and "session_id" in properties

    def _bind_caller_session_argument(
        *,
        raw_tool_name: str,
        arguments: dict[str, Any],
        call_context: _ToolCallAuditContext,
    ) -> None:
        if not _tool_accepts_session_id(raw_tool_name):
            return
        resolved_session_id = _resolve_bound_session_id(
            call_context.argument_session_id,
            call_context.ambient_session_id,
        )
        arguments["session_id"] = resolved_session_id
        call_context.session_id = resolved_session_id

    def _mcp_session_token_valid(
        session_id: str,
        supplied_token: str | None,
        *,
        require_proof: bool = False,
    ) -> bool:
        session = context.session_repository.get_session(session_id)
        if session is None:
            return True
        if require_proof:
            return verify_mcp_session_proof_token(session, supplied_token)
        return verify_mcp_session_token(session, supplied_token)

    def audit_success(
        session_id: str,
        tool_name: McpToolName,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        session = context.session_repository.get_session(session_id)
        actor_user_id = _session_actor_user_id(session) if session is not None else None
        redacted_keys = redacted_keys_for_session_id(session_id)
        redacted_args = _redact_args(redacted_keys, args)
        audit_result = _assistant_mutation_audit_result(
            tool_name,
            args,
            result,
            redacted_keys,
            default_status="success",
        )
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=None if session is None else session.vm_id,
                directory_path=None if session is None else session.directory_path,
                agent_role=_session_actor_role(session),
                tool_name=tool_name_value(tool_name),
                actor_user_id=actor_user_id,
                **_tool_call_audit_fields(
                    session=session,
                    tool_name=tool_name,
                    args=args,
                    result=audit_result,
                ),
                decision="allowed",
                args=redacted_args,
                result=audit_result,
            )
        )
        _mark_tool_call_audited()
        logger.info(
            "metric_mcp_tool_decision outcome=allowed tool=%s session_id=%s",
            tool_name_value(tool_name),
            session_id,
        )

    def audit_failure(
        session_id: str | None,
        tool_name: McpToolName,
        args: dict[str, Any],
        error: str,
        *,
        decision: str = "denied",
        error_class: str = "policy_error",
    ) -> None:
        session = context.session_repository.get_session(session_id) if session_id else None
        vm_id = None if session is None else session.vm_id
        actor_user_id = _session_actor_user_id(session) if session is not None else None
        redacted_keys = redacted_keys_for_session_id(session_id)
        redacted_args = _redact_args(redacted_keys, args)
        failure_result = _assistant_mutation_audit_result(
            tool_name,
            args,
            {"error": error, "error_class": error_class},
            redacted_keys,
            default_status="failed",
        )
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=vm_id,
                directory_path=None if session is None else session.directory_path,
                agent_role=_session_actor_role(session),
                tool_name=tool_name_value(tool_name),
                actor_user_id=actor_user_id,
                **_tool_call_audit_fields(
                    session=session,
                    tool_name=tool_name,
                    args=args,
                    result=failure_result,
                ),
                decision=decision,
                args=redacted_args,
                result=failure_result,
            )
        )
        _mark_tool_call_audited()
        logger.info(
            "metric_mcp_tool_decision outcome=%s tool=%s session_id=%s reason=%s error_class=%s",
            decision,
            tool_name_value(tool_name),
            session_id or "",
            error,
            error_class,
        )

    inline_media_helpers = InlineMediaHelpers(
        context=context,
        deps=deps,
        artifact_helpers=artifact_helpers,
        attachment_helpers=attachment_helpers,
        session_actor_role=_session_actor_role,
        session_actor_user_id=_session_actor_user_id,
    )

    register_ado_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        artifact_audit_summaries=artifact_helpers.artifact_audit_summaries,
        ado_artifacts_for_ids=artifact_helpers.ado_artifacts_for_ids,
        ado_artifact_approval_payload=artifact_helpers.ado_artifact_approval_payload,
    )
    register_approval_tools(
        server,
        context,
        deps,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
    )
    register_admin_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        payload_from_result=_payload_from_result,
    )
    register_documentation_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
    )
    register_chat_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        require_vm_visible_in_session_directory=(
            visibility_helpers.require_vm_visible_in_session_directory
        ),
        require_target_session_visible_within_directory=(
            visibility_helpers.require_target_session_visible_within_directory
        ),
    )
    register_consultation_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
    )
    register_issue_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        payload_from_result=_payload_from_result,
    )
    register_mssql_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
    )
    register_pr_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        artifact_audit_summaries=artifact_helpers.artifact_audit_summaries,
        ado_artifacts_for_ids=artifact_helpers.ado_artifacts_for_ids,
        ado_artifact_approval_payload=artifact_helpers.ado_artifact_approval_payload,
    )
    register_reference_repository_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
    )
    register_semantic_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
    )
    register_agent_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        require_vm_visible_in_session_directory=(
            visibility_helpers.require_vm_visible_in_session_directory
        ),
    )
    register_connector_tools(server, context, connector_service)
    register_host_directory_tools(server, context, host_directory_service)

    register_communication_tools(
        server,
        context,
        deps,
        helpers=CommunicationToolHelpers(
            transport_session_map=transport_session_map,
            audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
            audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
            artifact_payloads=artifact_helpers.artifact_payloads,
            artifact_audit_summaries=artifact_helpers.artifact_audit_summaries,
            communication_target_audit_fields=(artifact_helpers.communication_target_audit_fields),
            record_email_draft_snapshot=email_helpers.record_email_draft_snapshot,
            send_email_draft=email_helpers.send_email_draft,
            audit_inline_media_operation=inline_media_helpers.audit_inline_media_operation,
            resolve_inline_media=inline_media_helpers.resolve_inline_media,
            download_inline_media_bytes=inline_media_helpers.download_inline_media_bytes,
            provider_download_attachment=attachment_helpers.provider_download_attachment,
            stage_inline_media_artifact=inline_media_helpers.stage_inline_media_artifact,
            inline_media_ref_audit_payload=inline_media_helpers.inline_media_ref_audit_payload,
            normalize_inline_media_source_kind=_normalize_inline_media_source_kind,
            resolve_communication_attachments=(
                attachment_helpers.resolve_communication_attachments
            ),
            stage_communication_artifact=attachment_helpers.stage_communication_artifact,
            admin_service=artifact_helpers.admin_service,
            artifact_to_model=artifact_helpers.artifact_to_model,
            communication_artifact_store=artifact_helpers.communication_artifact_store,
            safe_artifact_id=artifact_helpers.safe_artifact_id,
            normalize_workspace_source_path=artifact_helpers.normalize_workspace_source_path,
            read_agent_workspace_file=artifact_helpers.read_agent_workspace_file,
            teams_artifacts_with_public_download_links=(
                artifact_helpers.teams_artifacts_with_public_download_links
            ),
            draft_recipients_from_payload=draft_recipients_from_payload,
            filter_discord_commands_for_session=_filter_discord_commands_for_session,
            filter_teams_messages_for_session=_filter_teams_messages_for_session,
            teams_cursor_for_target=_teams_cursor_for_target,
        ),
    )

    addon_mcp_tool_names: list[str] = []
    addon_base_dir = context.config.runtime_dir.parent
    addon_catalog = load_addons(context.config.addon_loader, base_dir=addon_base_dir)
    for addon in addon_catalog.registrations:
        addon_context = AddonContext(
            addon_id=addon.addon_id,
            runtime_dir=context.config.runtime_dir,
            addon_runtime_dir=addon_runtime_dir(context.config.runtime_dir, addon.addon_id),
            project_dir=addon_base_dir,
            config=context.config,
            service=context,
        )
        if addon.outbound_identity_resolver is not None:
            context.register_outbound_identity_resolver(
                addon.addon_id,
                addon.outbound_identity_resolver(addon_context),
            )
        registered_names = register_addon_mcp_tools(addon, server, addon_context)
        declared_names = tuple(tool.name for tool in addon.mcp_tool_catalog)
        if declared_names and set(registered_names) != set(declared_names):
            missing = sorted(set(declared_names) - set(registered_names))
            unexpected = sorted(set(registered_names) - set(declared_names))
            raise ValueError(
                f"add-on {addon.addon_id} MCP tool catalog does not match registration; "
                f"missing={missing} unexpected={unexpected}"
            )
        addon_mcp_tool_names.extend(registered_names)
    if addon_mcp_tool_names:
        existing_tool_names = set(context.policy.extra_mcp_tool_names)
        context.policy.set_extra_mcp_tool_names(existing_tool_names.union(addon_mcp_tool_names))

    def _deprecate_session_id_parameter_schema() -> None:
        for tool in server._tool_manager.list_tools():
            parameters = getattr(tool, "parameters", None)
            if not isinstance(parameters, dict):
                continue
            properties = parameters.get("properties")
            if not isinstance(properties, dict):
                continue
            session_schema = properties.get("session_id")
            if not isinstance(session_schema, dict):
                continue
            session_schema["description"] = _SESSION_ID_PARAMETER_DESCRIPTION
            required = parameters.get("required")
            if isinstance(required, list) and "session_id" in required:
                parameters["required"] = [item for item in required if item != "session_id"]

    def _inject_approval_request_id_schema() -> None:
        for tool in server._tool_manager.list_tools():
            if tool.name in _APPROVAL_REQUEST_ID_SCHEMA_EXCLUDED_TOOLS:
                continue
            parameters = getattr(tool, "parameters", None)
            if not isinstance(parameters, dict):
                continue
            properties = parameters.get("properties")
            if not isinstance(properties, dict):
                continue
            if "approval_request_id" not in properties:
                properties["approval_request_id"] = dict(_APPROVAL_REQUEST_ID_PARAMETER_SCHEMA)

    _deprecate_session_id_parameter_schema()
    _inject_approval_request_id_schema()

    def _offload_synchronous_tools() -> None:
        """Keep blocking MCP tool implementations off the transport event loop."""

        def _threaded_tool(sync_fn: Any) -> Any:
            async def _run(**kwargs: Any) -> Any:
                if _MCP_REQUEST_CONTEXT.get() is None:
                    # Direct in-process calls are used by administrative code and
                    # unit tests; only transport requests share the MCP event loop.
                    return sync_fn(**kwargs)
                return await run_in_threadpool(sync_fn, **kwargs)

            return _run

        for tool in server._tool_manager.list_tools():
            if tool.is_async:
                continue
            tool.fn = _threaded_tool(tool.fn)
            tool.is_async = True

    _offload_synchronous_tools()

    original_list_tools = server._tool_manager.list_tools

    def _list_tools_for_request() -> list[Any]:
        tools = list(original_list_tools())
        request = _MCP_REQUEST_CONTEXT.get()
        if request is None:
            return tools

        headers = getattr(request, "headers", None)
        session_id, workflow_run_id, mcp_tool_approval_lease_id = _tool_policy_context_from_request(
            request,
            transport_session_map=transport_session_map,
        )
        if not session_id:
            return tools

        try:
            session = context.policy.get_session(session_id)
        except PolicyError:
            return []
        if not verify_mcp_session_proof_token(
            session,
            _mcp_session_token_from_request(request),
        ):
            return []

        visible_tool_names = {
            tool_name_value(visibility.tool)
            for visibility in context.policy.visible_tools_for_session(
                session,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            )
        }
        _record_visible_tools(
            transport_session_id=_string_from_headers(headers, "mcp-session-id"),
            session_id=session_id,
            visible_tool_names=sorted(visible_tool_names),
            protocol_version=_string_from_headers(headers, "mcp-protocol-version"),
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )
        return [
            tool
            for tool in tools
            if str(getattr(tool, "name", "") or "").strip() in visible_tool_names
        ]

    server._tool_manager.list_tools = _list_tools_for_request

    def audit_approval_gate(
        *,
        session_id: str,
        session: Any,
        tool_name: McpToolName,
        raw_args: dict[str, Any],
        result: dict[str, Any],
        decision: str,
        reason: str | None = None,
    ) -> None:
        tool_value = tool_name_value(tool_name)
        redacted_keys = redacted_keys_for_session_id(session_id)
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=None if session is None else session.vm_id,
                directory_path=None if session is None else session.directory_path,
                agent_role=_session_actor_role(session),
                tool_name=tool_value,
                actor_user_id=_session_actor_user_id(session),
                **_tool_call_audit_fields(
                    session=session,
                    tool_name=tool_name,
                    args=raw_args,
                    result=result,
                ),
                decision=decision,
                reason=reason,
                args=_redact_args(redacted_keys, raw_args),
                result=_redact_nested_value(redacted_keys, result),
            )
        )
        _mark_tool_call_audited()

    def audit_tool_argument_policy_denial(
        *,
        session_id: str,
        session: Any,
        tool_name: McpToolName,
        raw_args: dict[str, Any],
        sanitized_arguments: dict[str, Any],
        error: ToolArgumentPolicyError,
    ) -> None:
        tool_value = tool_name_value(tool_name)
        redacted_keys = redacted_keys_for_session_id(session_id)
        denial_result = {
            "status": "denied",
            "error": str(error),
            "error_class": "tool_argument_policy_violation",
            "policy_rule_id": error.rule_id,
            "policy_rule_name": error.rule_name,
            "constraint_id": error.constraint_id,
            "field_path": error.path,
            "payload_summary": _redact_nested_value(redacted_keys, sanitized_arguments),
        }
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=None if session is None else session.vm_id,
                directory_path=None if session is None else session.directory_path,
                agent_role=_session_actor_role(session),
                tool_name=tool_value,
                actor_user_id=_session_actor_user_id(session),
                **_tool_call_audit_fields(
                    session=session,
                    tool_name=tool_name,
                    args=raw_args,
                    result=denial_result,
                ),
                decision="denied",
                reason=str(error),
                args=_redact_args(redacted_keys, raw_args),
                result=denial_result,
            )
        )
        _mark_tool_call_audited()
        logger.info(
            "metric_mcp_tool_decision outcome=denied tool=%s session_id=%s reason=%s error_class=%s",
            tool_value,
            session_id,
            error,
            "tool_argument_policy_violation",
        )

    def enforce_tool_argument_policy(
        *,
        raw_tool_name: str,
        raw_arguments: dict[str, Any],
        sanitized_arguments: dict[str, Any],
        call_context: _ToolCallAuditContext,
    ) -> None:
        tool_name: McpToolName = ToolName._value2member_map_.get(raw_tool_name) or raw_tool_name
        if not tool_name:
            return
        session_id = call_context.session_id
        if not session_id:
            return
        try:
            session = context.policy.require_tool(
                session_id,
                tool_name,
                workflow_run_id=call_context.workflow_run_id,
                mcp_tool_approval_lease_id=call_context.mcp_tool_approval_lease_id,
            )
        except PolicyError:
            return
        try:
            context.policy.require_tool_arguments(session, tool_name, sanitized_arguments)
        except ToolArgumentPolicyError as exc:
            audit_tool_argument_policy_denial(
                session_id=session_id,
                session=session,
                tool_name=tool_name,
                raw_args=raw_arguments,
                sanitized_arguments=sanitized_arguments,
                error=exc,
            )
            raise ToolError(f"tool_argument_policy_denied: {exc}") from exc

    def maybe_require_invocation_approval(
        *,
        raw_tool_name: str,
        raw_arguments: dict[str, Any],
        sanitized_arguments: dict[str, Any],
        call_context: _ToolCallAuditContext,
    ) -> ApprovalRequest | None:
        if raw_tool_name in _APPROVAL_REQUEST_ID_SCHEMA_EXCLUDED_TOOLS:
            return None
        tool_name: McpToolName = ToolName._value2member_map_.get(raw_tool_name) or raw_tool_name
        tool_value = tool_name_value(tool_name)
        if not tool_value:
            return None
        session_id = call_context.session_id
        if not session_id:
            return None
        try:
            session = context.policy.require_tool(
                session_id,
                tool_name,
                workflow_run_id=call_context.workflow_run_id,
                mcp_tool_approval_lease_id=call_context.mcp_tool_approval_lease_id,
            )
        except PolicyError:
            return None
        resource_requires_approval = False
        if tool_value == ToolName.CALL_CONNECTOR.value:
            try:
                _, _, connector_operation = connector_service.require_operation(
                    session,
                    str(sanitized_arguments.get("connector_id") or ""),
                    str(sanitized_arguments.get("operation") or ""),
                    dict(sanitized_arguments.get("arguments") or {}),
                )
            except (ValueError, ConnectorPolicyError) as exc:
                raise ToolError(str(exc)) from exc
            resource_requires_approval = bool(connector_operation.requires_approval)
        elif tool_value == ToolName.HOST_DIRECTORY_DELETE.value:
            try:
                resource_requires_approval = host_directory_service.delete_requires_approval(
                    session,
                    str(sanitized_arguments.get("directory_id") or ""),
                )
            except (ValueError, HostDirectoryError, HostDirectoryPolicyError) as exc:
                raise ToolError(str(exc)) from exc
        if not (
            resource_requires_approval
            or context.policy.approval_required_for_tool(session, tool_name)
        ):
            return None
        redacted_keys = redacted_keys_for_session_id(session_id)

        approval_payload = _approval_gate_payload(
            tool_name=tool_value,
            session_id=session_id,
            arguments=sanitized_arguments,
        )
        grant_settings = context.policy.approval_grant_settings_for_tool(session, tool_name)
        if resource_requires_approval:
            grant_settings = ApprovalGrantSettings(
                ttl_seconds=grant_settings.ttl_seconds,
                max_uses=1,
                payload_mode=ApprovalPayloadMode.EXACT,
            )
        payload_digest = digest_payload(approval_payload)
        supplied_request_id = str(raw_arguments.get("approval_request_id") or "").strip()
        if not supplied_request_id:
            approval = context.policy.create_approval(
                session_id,
                "mcp_tool_invocation",
                approval_payload,
                _approval_gate_human_summary(tool_value, session_id),
                tool_name=tool_value,
                intended_effect=_approval_gate_intended_effect(tool_value, session_id),
                approval_settings=grant_settings,
            )
            status = (
                "pending_approval"
                if approval.status == ApprovalStatus.PENDING
                else approval.status.value
            )
            result = {
                "status": status,
                "approval_request_id": approval.request_id,
                "payload_digest": payload_digest,
                "approval_diagnostic": _approval_gate_pending_diagnostic(
                    approval_request_id=approval.request_id,
                    approval_payload=approval_payload,
                    redacted_keys=redacted_keys,
                    status=status,
                    reason=(
                        f"Tool {tool_value} requires approval before execution. "
                        f"Retry the exact same payload with approval_request_id={approval.request_id} "
                        "after a human approves it."
                    ),
                ),
                "approval_settings": grant_settings.model_dump(mode="json"),
                "used_count": approval.used_count,
                "message": (
                    f"Tool {tool_value} requires approval before execution. "
                    f"Retry the exact same payload with approval_request_id={approval.request_id} "
                    "after a human approves it."
                ),
            }
            audit_approval_gate(
                session_id=session_id,
                session=session,
                tool_name=tool_name,
                raw_args=raw_arguments,
                result=result,
                decision=status,
            )
            raise ToolError(result["message"])

        try:
            approval = context.policy.require_approved(
                supplied_request_id,
                approval_payload,
                consume=True,
            )
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {supplied_request_id} does not belong to session "
                    f"{session_id}"
                )
            if str(approval.tool_name or approval.action_type).strip() != tool_value:
                raise PolicyError(
                    f"Approval request {supplied_request_id} is not for tool {tool_value}"
                )
        except PolicyError as exc:
            approval_request_id = getattr(exc, "approval_request_id", None) or supplied_request_id
            result = {
                "status": "blocked",
                "approval_request_id": approval_request_id,
                "payload_digest": payload_digest,
                "error": str(exc),
                "error_class": "approval_blocked",
                "rejection_kind": getattr(exc, "rejection_kind", None),
                "approval_diagnostic": _approval_gate_blocked_diagnostic(
                    approval_request_id=approval_request_id,
                    attempted_payload=approval_payload,
                    error=exc,
                ),
            }
            audit_approval_gate(
                session_id=session_id,
                session=session,
                tool_name=tool_name,
                raw_args=raw_arguments,
                result=result,
                decision="blocked",
                reason=str(exc),
            )
            raise ToolError(f"approval_blocked: {exc}") from exc
        return approval

    def maybe_require_registered_addon_tool(
        *,
        raw_tool_name: str,
        sanitized_arguments: dict[str, Any],
        call_context: _ToolCallAuditContext,
    ) -> None:
        tool_value = str(raw_tool_name or "").strip()
        if not tool_value or tool_value in ToolName._value2member_map_:
            return
        registered_addon_tools = getattr(context.policy, "extra_mcp_tool_names", set())
        if tool_value not in registered_addon_tools:
            return
        if not call_context.session_id:
            message = "session_id is required"
            audit_failure(None, tool_value, sanitized_arguments, message)
            raise ToolError(message)
        try:
            context.policy.require_tool(
                call_context.session_id,
                tool_value,
                workflow_run_id=call_context.workflow_run_id,
                mcp_tool_approval_lease_id=call_context.mcp_tool_approval_lease_id,
            )
        except PolicyError as exc:
            audit_failure(call_context.session_id, tool_value, sanitized_arguments, str(exc))
            raise ToolError(str(exc)) from exc

    def require_mcp_session_token(
        *,
        raw_tool_name: str,
        sanitized_arguments: dict[str, Any],
        call_context: _ToolCallAuditContext,
    ) -> None:
        if not call_context.session_id:
            return
        if _mcp_session_token_valid(
            call_context.session_id,
            call_context.mcp_session_token,
            require_proof=_MCP_REQUEST_CONTEXT.get() is not None,
        ):
            return
        tool_value: McpToolName = ToolName._value2member_map_.get(raw_tool_name) or raw_tool_name
        message = "MCP session token is required for this session"
        audit_failure(
            call_context.session_id,
            tool_value,
            sanitized_arguments,
            message,
            error_class="auth_error",
        )
        raise ToolError(message)

    original_call_tool = server._tool_manager.call_tool

    async def audited_call_tool(*call_args: Any, **call_kwargs: Any) -> Any:
        raw_tool_name = ""
        if call_args:
            raw_tool_name = str(call_args[0] or "").strip()
        else:
            raw_tool_name = str(
                call_kwargs.get("name") or call_kwargs.get("tool_name") or ""
            ).strip()
        raw_arguments = _tool_call_arguments_dict(
            call_args[1] if len(call_args) > 1 else call_kwargs.get("arguments")
        )
        request_context = (
            call_kwargs.get("context")
            or call_kwargs.get("ctx")
            or (call_args[2] if len(call_args) > 2 else None)
        )
        call_context = _tool_call_context_from_request(
            raw_tool_name,
            raw_arguments,
            request_context,
            transport_session_map,
        )
        sanitized_arguments = _approval_gate_arguments(raw_arguments)
        token = _TOOL_CALL_AUDIT_CONTEXT.set(call_context)
        try:
            _bind_caller_session_argument(
                raw_tool_name=raw_tool_name,
                arguments=sanitized_arguments,
                call_context=call_context,
            )
            dispatch_arguments = dict(sanitized_arguments)
            tool = server._tool_manager._tools.get(raw_tool_name)
            arg_model = getattr(getattr(tool, "fn_metadata", None), "arg_model", None)
            model_fields = getattr(arg_model, "model_fields", {})
            if (
                isinstance(model_fields, dict)
                and "approval_request_id" in model_fields
                and "approval_request_id" in raw_arguments
            ):
                dispatch_arguments["approval_request_id"] = raw_arguments.get("approval_request_id")
            dispatch_call_args = list(call_args)
            dispatch_call_kwargs = dict(call_kwargs)
            if len(dispatch_call_args) > 1:
                dispatch_call_args[1] = dispatch_arguments
                dispatch_call_kwargs.pop("arguments", None)
            else:
                dispatch_call_kwargs["arguments"] = dispatch_arguments
            approval_context_tokens = [
                set_tool_approval_context(
                    session_id=call_context.session_id,
                    workflow_run_id=call_context.workflow_run_id,
                    lease_id=call_context.mcp_tool_approval_lease_id,
                )
            ]
            try:
                require_mcp_session_token(
                    raw_tool_name=raw_tool_name,
                    sanitized_arguments=sanitized_arguments,
                    call_context=call_context,
                )
                maybe_require_registered_addon_tool(
                    raw_tool_name=raw_tool_name,
                    sanitized_arguments=sanitized_arguments,
                    call_context=call_context,
                )
                enforce_tool_argument_policy(
                    raw_tool_name=raw_tool_name,
                    raw_arguments=raw_arguments,
                    sanitized_arguments=sanitized_arguments,
                    call_context=call_context,
                )
                consumed_approval = maybe_require_invocation_approval(
                    raw_tool_name=raw_tool_name,
                    raw_arguments=raw_arguments,
                    sanitized_arguments=sanitized_arguments,
                    call_context=call_context,
                )
                tool_name: McpToolName = (
                    ToolName._value2member_map_.get(raw_tool_name) or raw_tool_name
                )
                if tool_name:
                    try:
                        lease_reserved = context.policy.consume_tool_approval_lease_use(
                            session_id=call_context.session_id,
                            tool_name=tool_name,
                            workflow_run_id=call_context.workflow_run_id,
                            mcp_tool_approval_lease_id=call_context.mcp_tool_approval_lease_id,
                            per_invocation_approval_consumed=consumed_approval is not None,
                        )
                    except PolicyError as exc:
                        raise ToolError(str(exc)) from exc
                    if lease_reserved:
                        approval_context_tokens.append(
                            set_tool_approval_context(
                                session_id=call_context.session_id,
                                workflow_run_id=call_context.workflow_run_id,
                                lease_id=call_context.mcp_tool_approval_lease_id,
                                reserved_tool_name=tool_name,
                            )
                        )
                result = await original_call_tool(*dispatch_call_args, **dispatch_call_kwargs)
                if not call_context.audit_emitted:
                    tool_value = tool_name_value(tool_name) if tool_name else raw_tool_name
                    session = (
                        context.session_repository.get_session(call_context.session_id)
                        if call_context.session_id
                        else None
                    )
                    redacted_keys = redacted_keys_for_session_id(call_context.session_id)
                    result_payload = result if isinstance(result, dict) else {"result": result}
                    audit_result = _assistant_mutation_audit_result(
                        tool_name,
                        raw_arguments,
                        result_payload,
                        redacted_keys,
                        default_status="success",
                    )
                    context.audit.log(
                        AuditEvent(
                            event_type="tool_call",
                            session_id=call_context.session_id,
                            vm_id=None if session is None else session.vm_id,
                            directory_path=None if session is None else session.directory_path,
                            agent_role=_session_actor_role(session),
                            tool_name=tool_value or None,
                            actor_user_id=_session_actor_user_id(session),
                            **_tool_call_audit_fields(
                                session=session,
                                tool_name=tool_name,
                                args=raw_arguments,
                                result=audit_result,
                            ),
                            decision="allowed",
                            args=_redact_args(redacted_keys, raw_arguments),
                            result=_redact_nested_value(redacted_keys, audit_result),
                        )
                    )
                    _mark_tool_call_audited()
                    logger.info(
                        "metric_mcp_tool_decision outcome=allowed tool=%s session_id=%s",
                        tool_value,
                        call_context.session_id or "",
                    )
                return result
            finally:
                for approval_context_token in reversed(approval_context_tokens):
                    reset_tool_approval_context(approval_context_token)
        except Exception as exc:
            if not call_context.audit_emitted:
                tool_name: McpToolName | None = (
                    ToolName._value2member_map_.get(raw_tool_name) or raw_tool_name or None
                )
                session = (
                    context.session_repository.get_session(call_context.session_id)
                    if call_context.session_id
                    else None
                )
                redacted_keys = redacted_keys_for_session_id(call_context.session_id)
                redacted_args = _redact_args(redacted_keys, raw_arguments)
                error_result = _assistant_mutation_audit_result(
                    tool_name,
                    raw_arguments,
                    {
                        "status": "error",
                        "error": str(exc),
                        "error_class": "tool_dispatch_error",
                    },
                    redacted_keys,
                    default_status="error",
                )
                context.audit.log(
                    AuditEvent(
                        event_type="tool_call",
                        session_id=call_context.session_id,
                        vm_id=None if session is None else session.vm_id,
                        directory_path=None if session is None else session.directory_path,
                        agent_role=_session_actor_role(session),
                        tool_name=raw_tool_name or None,
                        actor_user_id=_session_actor_user_id(session),
                        **_tool_call_audit_fields(
                            session=session,
                            tool_name=tool_name,
                            args=raw_arguments,
                            result=error_result,
                        ),
                        decision="error",
                        reason=str(exc),
                        args=redacted_args,
                        result=error_result,
                    )
                )
                _mark_tool_call_audited()
            raise
        finally:
            _TOOL_CALL_AUDIT_CONTEXT.reset(token)

    server._tool_manager.call_tool = audited_call_tool

    return server
