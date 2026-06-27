from __future__ import annotations

import base64
from contextvars import ContextVar
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
from starlette.requests import Request

from agent_sandbox.addons import (
    AddonContext,
    addon_runtime_dir,
    load_addons,
    register_addon_mcp_tools,
)
from agent_sandbox.admin.service import AdminService
from agent_sandbox.ado_reports import (
    DEFAULT_DONE_STATES,
    DEFAULT_EFFORT_FIELDS,
    DEFAULT_IN_PROGRESS_STATES,
    build_ado_daily_status_report_data,
)
from agent_sandbox.communication_artifacts import (
    CommunicationArtifactStore,
    normalize_safe_filename,
    validate_artifact_id,
)
from agent_sandbox.communication_artifact_links import (
    CommunicationArtifactLinkError,
    communication_artifact_link_secret_env,
    communication_artifact_public_base_url,
    issue_communication_artifact_download_link,
)
from agent_sandbox.config import GatewayConfig, InboundPhoneRoute, _normalize_phone_number
from agent_sandbox.directories import DirectoryService
from agent_sandbox.effects.providers.artifacts import ArtifactStageEffectProvider
from agent_sandbox.effects.providers.agent_exec import AgentExecEffectProvider
from agent_sandbox.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.schemas import (
    AgentCopyFilesEffectDefinition,
    AgentExecEffectDefinition,
    ArtifactStageEffectDefinition,
    VmCopyFilesItemDefinition,
)
from agent_sandbox.email.http_utils import ProviderRequestError
from agent_sandbox.inline_media import (
    download_url_with_limit,
    inline_media_download_from_embedded_payload,
    normalize_inline_media_provider,
    require_provider_media_url,
    sanitize_inline_media_metadata,
)
from agent_sandbox.email.providers import EmailDraft, EmailDraftAttachment
from agent_sandbox.issues.service import AzureDevOpsIssueInlineMediaProvider, IssueService
from agent_sandbox.pr_feedback.providers import (
    AzureDevOpsFeedbackAdapter,
    GitHubFeedbackAdapter,
)
from agent_sandbox.models import (
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
    AdoBacklogTagsUpdateRequest,
    AdoBacklogTextUpdateRequest,
    AdoBacklogTitleUpdateRequest,
    AdoWorkItemActivityRequest,
    AdoWorkItemActivityResponse,
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
from agent_sandbox.policy import (
    PolicyError,
    ToolArgumentPolicyError,
    reset_tool_approval_context,
    set_tool_approval_context,
)
from agent_sandbox.retell_phone import (
    RetellWebhookSignatureError,
    retell_api_key_from_config,
    sanitize_retell_metadata,
    verify_retell_webhook_signature,
)
from agent_sandbox.teams_meeting import voice_session_transcript_from_teams_events
from agent_sandbox.voice_sessions import (
    VoiceQueryDelegator,
    disable_pending_voice_query_wake,
    voice_query_payload,
)
from agent_sandbox.voice_sessions import (
    VoiceProjectionService,
    realtime_session_config,
    sanitize_voice_metadata,
)
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.services import (
    TeamsInlineMediaProvider,
    normalize_pr_feedback_provider,
    normalize_teams_mention_refs,
    normalize_teams_target,
    parse_teams_target,
    resolve_teams_send_target,
    teams_target_from_message,
)
from agent_sandbox.gateway.dependencies import GatewayToolDependencies
from agent_sandbox.gateway.admin_tools import register_admin_tools
from agent_sandbox.gateway.agent_tools import register_agent_tools
from agent_sandbox.gateway.ado_tools import register_ado_tools
from agent_sandbox.gateway.approval_tools import register_approval_tools
from agent_sandbox.gateway.chat_tools import register_chat_tools
from agent_sandbox.gateway.communication_tools import register_communication_tools
from agent_sandbox.gateway.issue_tools import register_issue_tools
from agent_sandbox.gateway.mssql_tools import register_mssql_tools
from agent_sandbox.gateway.pr_tools import register_pr_tools
from agent_sandbox.gateway.semantic_tools import register_semantic_tools
from agent_sandbox.gateway.approval_diagnostics import (
    _approval_gate_arguments,
    _approval_gate_blocked_diagnostic,
    _approval_gate_human_summary,
    _approval_gate_intended_effect,
    _approval_gate_payload,
    _approval_gate_pending_diagnostic,
)
from agent_sandbox.gateway.request_context import (
    _first_string_in_payload,
    _request_from_context,
    _string_from_headers,
    _string_from_query_params,
    _string_from_raw_query,
)
from agent_sandbox.gateway.session_context import (
    _SESSION_ID_CONTEXT_CONFLICT_MESSAGE,
    _SESSION_ID_PARAMETER_DESCRIPTION,
    _ambient_session_id_from_context,
    _ambient_session_id_from_request,
    _resolve_bound_session_id,
    _resolve_session_id_from_context,
    _resolve_session_id_from_request,
)
from agent_sandbox.gateway.tool_audit import (
    _TOOL_CALL_AUDIT_CONTEXT,
    _ToolCallAuditContext,
    _mark_tool_call_audited,
    _redact_args,
    _redact_nested_value,
    _tool_call_arguments_dict,
    _tool_call_audit_fields,
    _tool_call_context,
    _tool_call_duration_ms,
)

logger = logging.getLogger(__name__)

_WORKSPACE_FILE_PAYLOAD_BEGIN = "__AGENT_SANDBOX_WORKSPACE_FILE_JSON_BEGIN__"
_WORKSPACE_FILE_PAYLOAD_END = "__AGENT_SANDBOX_WORKSPACE_FILE_JSON_END__"
_MCP_REQUEST_CONTEXT: ContextVar[Request | None] = ContextVar(
    "agent_sandbox_mcp_request_context",
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
_WORKSPACE_FILE_READER_SCRIPT = r"""
import base64
import json
import mimetypes
import pathlib
import stat
import sys

try:
    raw_path = sys.argv[1]
    max_bytes = int(sys.argv[2]) if len(sys.argv) > 2 else -1
    base = pathlib.Path("/home/agent").resolve()
    candidate = pathlib.Path(raw_path)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=True)
    resolved.relative_to(base)
    file_stat = resolved.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("source_path must resolve to a regular file")
    if max_bytes >= 0 and file_stat.st_size > max_bytes:
        raise ValueError(
            f"source_path exceeds communication_file_max_bytes: {file_stat.st_size} > {max_bytes}"
        )
    content = resolved.read_bytes()
    payload = {
        "source_path": str(resolved),
        "filename": resolved.name or "attachment",
        "mime_type": mimetypes.guess_type(resolved.name)[0],
        "byte_count": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    print("__AGENT_SANDBOX_WORKSPACE_FILE_JSON_BEGIN__")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    print("__AGENT_SANDBOX_WORKSPACE_FILE_JSON_END__")
except Exception as exc:
    print(f"communication_stage_workspace_file: {exc}", file=sys.stderr)
    raise SystemExit(2)
""".strip()


_APPROVAL_REQUEST_ID_PARAMETER_SCHEMA = {
    "anyOf": [{"type": "string"}, {"type": "null"}],
    "default": None,
    "description": (
        "Optional approval request id for retrying an approval-gated tool invocation."
    ),
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
            "x-agent-sandbox-workflow-run-id",
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
            "x-agent-sandbox-mcp-tool-approval-lease-id",
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
            "x-agent-sandbox-workflow-run-id",
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
            "x-agent-sandbox-mcp-tool-approval-lease-id",
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


def _filter_discord_commands_for_session(
    context: RuntimeContext,
    session_id: str,
    session: Any,
    raw_commands: list[DiscordCommandEnvelope],
    args: dict[str, Any],
    audit_failure_cb: Any,
) -> tuple[list[dict[str, Any]], int, str | None]:
    commands: list[dict[str, Any]] = []
    denied_count = 0
    next_cursor = raw_commands[-1].command_id if raw_commands else args.get("since_cursor")
    for command in raw_commands:
        try:
            context.policy.require_discord_command_source(session, command)
        except PolicyError as exc:
            denied_count += 1
            audit_failure_cb(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                {
                    **args,
                    "command_id": command.command_id,
                    "source_metadata": command.metadata,
                },
                str(exc),
            )
            continue
        commands.append(command.model_dump(mode="json"))
    return commands, denied_count, next_cursor


def _filter_teams_messages_for_session(
    context: RuntimeContext,
    session_id: str,
    session: Any,
    raw_messages: list[TeamsMessageEnvelope],
    args: dict[str, Any],
    audit_failure_cb: Any,
) -> tuple[list[dict[str, Any]], int, str | None]:
    messages: list[dict[str, Any]] = []
    denied_count = 0
    next_cursor = raw_messages[-1].cursor if raw_messages else args.get("since_cursor")
    for message in raw_messages:
        try:
            context.policy.require_teams_message_source(session, message)
        except PolicyError as exc:
            denied_count += 1
            audit_failure_cb(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                {
                    **args,
                    "cursor": message.cursor,
                    "source_metadata": message.metadata,
                },
                str(exc),
            )
            continue
        messages.append(message.model_dump(mode="json"))
    return messages, denied_count, next_cursor


def _teams_cursor_for_target(
    cursor: str | None,
    *,
    channel_target: str,
    field_name: str,
) -> str | None:
    if cursor is None:
        return None
    normalized_cursor = str(cursor or "").strip()
    if not normalized_cursor:
        raise ToolError(f"{field_name} must be non-empty when provided")
    if "/" not in normalized_cursor:
        return f"{channel_target}/{normalized_cursor}"
    cursor_target, message_id = normalized_cursor.rsplit("/", 1)
    if not cursor_target or not message_id:
        raise ToolError(
            f"{field_name} must be a Teams cursor or message id scoped by channel_target"
        )
    try:
        normalized_cursor_target = normalize_teams_target(cursor_target)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if normalized_cursor_target != channel_target:
        raise ToolError(f"{field_name} target must match channel_target")
    return f"{channel_target}/{message_id}"


def _visible_vm_summaries_for_session(
    context: RuntimeContext,
    session: Any,
    *,
    vm_user: str,
    admin_service: AdminService | None = None,
) -> list[Any]:
    service = admin_service or AdminService(context.config.runtime_dir)
    return service.list_vms(
        include_inactive=True,
        vm_user=vm_user,
        directory_path=session.directory_path,
        direct_only=True,
    )


def _require_vm_visible_in_session_directory(
    context: RuntimeContext,
    session: Any,
    *,
    vm_name: str,
    vm_user: str,
) -> Any:
    summaries = _visible_vm_summaries_for_session(context, session, vm_user=vm_user)
    summary = next((item for item in summaries if item.vm_name == vm_name), None)
    if summary is None:
        raise PolicyError(
            f"VM '{vm_name}' is outside the session directory scope '{session.directory_path}'."
        )
    return summary


def _require_target_session_visible_within_directory(
    context: RuntimeContext,
    source_session: Any,
    *,
    target_session_id: str,
    vm_user: str,
) -> Any:
    target_session = context.policy.get_session(target_session_id)
    if target_session.directory_path == source_session.directory_path:
        return target_session

    target_vm_id = str(getattr(target_session, "vm_id", "") or "").strip()
    if not target_vm_id:
        raise PolicyError(
            f"Session '{target_session_id}' is outside the session directory scope "
            f"'{source_session.directory_path}'."
        )

    visible_vms = _visible_vm_summaries_for_session(context, source_session, vm_user=vm_user)
    if any(target_session_id in summary.session_ids for summary in visible_vms):
        return target_session

    raise PolicyError(
        f"Session '{target_session_id}' is outside the session directory scope "
        f"'{source_session.directory_path}'."
    )


def create_server(context: RuntimeContext) -> FastMCP:
    server = FastMCP(
        "Agent Sandbox Gateway",
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
    setattr(server, "_agent_sandbox_transport_session_map", transport_session_map)
    deps = GatewayToolDependencies.from_context(context)
    register_ado_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        artifact_audit_summaries=_artifact_audit_summaries,
        ado_artifacts_for_ids=_ado_artifacts_for_ids,
        ado_artifact_approval_payload=_ado_artifact_approval_payload,
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
    register_chat_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        require_vm_visible_in_session_directory=_require_vm_visible_in_session_directory,
        require_target_session_visible_within_directory=(
            _require_target_session_visible_within_directory
        ),
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
        artifact_audit_summaries=_artifact_audit_summaries,
        ado_artifacts_for_ids=_ado_artifacts_for_ids,
        ado_artifact_approval_payload=_ado_artifact_approval_payload,
    )
    register_semantic_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
    )
    addon_mcp_tool_names: list[str] = []
    addon_catalog = load_addons(context.config.addon_loader)
    for addon in addon_catalog.registrations:
        addon_context = AddonContext(
            addon_id=addon.addon_id,
            runtime_dir=context.config.runtime_dir,
            addon_runtime_dir=addon_runtime_dir(context.config.runtime_dir, addon.addon_id),
            config=context.config,
            service=context,
        )
        addon_mcp_tool_names.extend(
            register_addon_mcp_tools(addon, server, addon_context)
        )
    if addon_mcp_tool_names:
        context.policy.set_extra_mcp_tool_names(addon_mcp_tool_names)

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
            last_mcp_tool_approval_lease_id=(
                str(mcp_tool_approval_lease_id or "").strip() or None
            ),
        )
        transport_session_map[normalized_transport_session_id] = normalized_session_id

    def draft_recipients_from_payload(payload: dict[str, Any]) -> list[str]:
        recipients: list[str] = []
        for key in ("recipients", "to", "cc", "bcc"):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                recipients.append(value)
            elif isinstance(value, list):
                recipients.extend([item for item in value if isinstance(item, str)])
        return recipients

    def _normalized_email_recipients(recipients: list[str]) -> list[str]:
        return sorted(address.strip().casefold() for address in recipients if address.strip())

    def _record_email_draft_snapshot(
        *,
        session_id: str,
        draft: EmailDraft,
        body: str,
        artifact_ids: list[str] | None = None,
    ) -> None:
        snapshot = DraftMessage(
            draft_id=draft.draft_id,
            mailbox_id=draft.mailbox_id,
            provider=draft.provider,
            provider_draft_id=draft.draft_id,
            thread_or_message_id=draft.thread_or_message_id,
            subject=draft.subject,
            body=body,
            body_digest=sha256(body.encode("utf-8")).hexdigest(),
            recipients=list(draft.recipients),
            attachment_ids=list(artifact_ids or []),
            metadata={**draft.metadata, "session_id": session_id},
            created_at=draft.created_at,
        )
        context.drafts.put(snapshot.draft_id, snapshot)

    def _load_email_draft_snapshot(
        *, session_id: str, mailbox_id: str, draft_id: str
    ) -> DraftMessage:
        normalized_draft_id = str(draft_id or "").strip()
        if not normalized_draft_id:
            raise PolicyError("email send requires draft_id")
        snapshot = context.drafts.get(normalized_draft_id)
        if snapshot is None:
            raise PolicyError(
                "Email send requires a host-recorded draft snapshot; create the draft first."
            )
        if snapshot.mailbox_id != mailbox_id:
            raise PolicyError(
                f"Email draft {normalized_draft_id!r} belongs to mailbox "
                f"{snapshot.mailbox_id!r}, not {mailbox_id!r}"
            )
        owner_session_id = str(snapshot.metadata.get("session_id") or "").strip()
        if not owner_session_id:
            raise PolicyError(
                "Email draft snapshot does not include session ownership; recreate the draft before sending."
            )
        if owner_session_id != session_id:
            raise PolicyError(
                f"Email draft {normalized_draft_id!r} belongs to session {owner_session_id!r}"
            )
        return snapshot

    def _validate_email_send_snapshot(
        *,
        snapshot: DraftMessage,
        recipients: list[str],
        subject: str | None,
        artifact_ids: list[str] | None,
    ) -> None:
        if _normalized_email_recipients(recipients) != _normalized_email_recipients(
            snapshot.recipients
        ):
            raise PolicyError("Email send recipients must match the recorded draft recipients.")
        if subject is not None and subject != snapshot.subject:
            raise PolicyError("Email send subject must match the recorded draft subject.")
        if artifact_ids is None:
            if snapshot.attachment_ids:
                raise PolicyError(
                    "Email draft has attachments; use email_send_with_attachments with "
                    "matching artifact_ids."
                )
            return
        if sorted(artifact_ids) != sorted(snapshot.attachment_ids):
            raise PolicyError("Email send attachments must match the recorded draft attachments.")

    def _mark_email_draft_sent(
        *, draft_id: str, sent_at: datetime, status: str, provider_message_id: str | None
    ) -> None:
        def updater(items: dict[str, DraftMessage]) -> None:
            snapshot = items.get(draft_id)
            if snapshot is None:
                return
            items[draft_id] = snapshot.model_copy(
                update={
                    "sent_at": sent_at,
                    "send_status": status,
                    "provider_message_id": provider_message_id,
                    "metadata": {
                        **snapshot.metadata,
                        "send_status": status,
                        "provider_message_id": provider_message_id,
                    },
                }
            )

        context.drafts.update_all(updater)

    def _send_email_draft(
        *,
        session_id: str,
        session: Any,
        mailbox_id: str,
        draft_id: str,
        recipients: list[str],
        subject: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        context.policy.require_email_send_enabled(session)
        context.policy.require_email_mailbox(session, mailbox_id)
        context.policy.require_draft_recipients(session, recipients)
        snapshot = _load_email_draft_snapshot(
            session_id=session_id, mailbox_id=mailbox_id, draft_id=draft_id
        )
        _validate_email_send_snapshot(
            snapshot=snapshot,
            recipients=recipients,
            subject=subject,
            artifact_ids=artifact_ids,
        )
        result = context.email_service_for_session(session_id).send_draft(
            mailbox_id=mailbox_id,
            draft_id=draft_id,
        )
        _mark_email_draft_sent(
            draft_id=draft_id,
            sent_at=result.sent_at,
            status=result.status,
            provider_message_id=result.message_id,
        )
        output = result.model_dump(mode="json")
        output["recipients"] = list(snapshot.recipients)
        if artifact_ids is not None:
            output["artifact_ids"] = list(artifact_ids)
        return output

    def redacted_keys_for_session_id(session_id: str | None) -> set[str]:
        token_redacted_keys = {"response_token", "issued_token", "token_hash"}
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

    def _communication_artifact_store() -> CommunicationArtifactStore:
        return deps.communication_artifact_store_factory()

    def _safe_artifact_id(value: str) -> str:
        try:
            return validate_artifact_id(value)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    def _artifact_to_model(record: Any) -> CommunicationFileArtifact:
        payload = (
            record.model_dump(mode="json")
            if hasattr(record, "model_dump")
            else dict(record)
        )
        return CommunicationFileArtifact.model_validate(payload)

    def _normalize_workspace_source_path(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise PolicyError("source_path is required")
        if "\x00" in text:
            raise PolicyError("source_path contains a NUL byte")
        if "\\" in text:
            raise PolicyError("source_path must use POSIX separators")
        windows_path = PureWindowsPath(text)
        if windows_path.drive:
            raise PolicyError("source_path must be under /home/agent")
        posix_path = PurePosixPath(text)
        if any(part == ".." for part in posix_path.parts):
            raise PolicyError("source_path must not contain parent traversal")
        if posix_path.is_absolute():
            try:
                posix_path.relative_to(PurePosixPath("/home/agent"))
            except ValueError as exc:
                raise PolicyError("source_path must be under /home/agent") from exc
        return text

    def _communication_file_max_bytes_for_session(session: Any) -> int | None:
        session_value = getattr(session, "communication_file_max_bytes", None)
        if session_value is not None:
            return int(session_value)
        effective_value = context.policy.effective_policy_for_session(
            session
        ).communication_file_max_bytes
        return None if effective_value is None else int(effective_value)

    def _parse_workspace_file_payload(stdout: str) -> dict[str, Any]:
        text = str(stdout or "")
        start = text.find(_WORKSPACE_FILE_PAYLOAD_BEGIN)
        end = text.find(_WORKSPACE_FILE_PAYLOAD_END)
        if start >= 0 and end > start:
            json_text = text[start + len(_WORKSPACE_FILE_PAYLOAD_BEGIN) : end].strip()
        else:
            json_text = text.strip()
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ToolError(
                "Workspace file reader did not return a valid artifact payload"
            ) from exc
        if not isinstance(payload, dict):
            raise ToolError("Workspace file reader returned an invalid artifact payload")
        return payload

    def _read_agent_workspace_file(
        session: Any,
        *,
        source_path: str,
    ) -> dict[str, Any]:
        max_bytes = _communication_file_max_bytes_for_session(session)
        effect = AgentExecEffectDefinition(
            id="communication-stage-workspace-file",
            display_name="Stage workspace communication file",
            type="effect:agent:exec",
            session_id=session.session_id,
            command=[
                "python3",
                "-c",
                _WORKSPACE_FILE_READER_SCRIPT,
                source_path,
                str(max_bytes if max_bytes is not None else -1),
            ],
            working_directory="/home/agent",
            timeout_seconds=30,
            check=True,
        )
        effect_context = EffectRuntimeContext(
            provider_config=context.resolve_provider_config_for_session(
                session.session_id
            ).model_dump(mode="json"),
            admin_service=_admin_service(),
            metadata={"directory_path": session.directory_path or "root"},
        )
        provider = AgentExecEffectProvider()
        provider.initialize(effect_definition=effect, runtime_context=effect_context)
        exec_result = provider.execute()
        return _parse_workspace_file_payload(str(exec_result.get("stdout") or ""))

    def _load_artifacts(artifact_ids: list[str]) -> list[CommunicationFileArtifact]:
        artifacts: list[CommunicationFileArtifact] = []
        store = _communication_artifact_store()
        for artifact_id in artifact_ids:
            safe_id = _safe_artifact_id(artifact_id)
            try:
                artifacts.append(_artifact_to_model(store.get(safe_id).metadata))
            except FileNotFoundError as exc:
                raise ToolError(str(exc)) from exc
        return artifacts

    def _artifact_payloads(artifact_ids: list[str]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        store = _communication_artifact_store()
        for artifact_id in artifact_ids:
            safe_id = _safe_artifact_id(artifact_id)
            try:
                payloads.append(store.payload(safe_id))
            except FileNotFoundError as exc:
                raise ToolError(str(exc)) from exc
        return payloads

    def _artifact_audit_summaries(artifact_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "provider": artifact.provider,
                "message_id": artifact.source_message_id,
                "attachment_id": artifact.source_attachment_id,
                "artifact_id": artifact.artifact_id,
                "byte_count": artifact.byte_count,
                "size": artifact.byte_count,
                "mime_type": artifact.mime_type,
                "filename": artifact.filename,
                "normalized_safe_filename": artifact.normalized_safe_filename,
                "expires_at": artifact.expires_at,
            }
            for artifact in _load_artifacts(artifact_ids)
        ]

    _TEAMS_INLINE_IMAGE_MIME_TYPES = {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }

    def _artifact_mime_type(artifact: dict[str, Any]) -> str:
        return str(artifact.get("mime_type") or "").split(";", 1)[0].strip().lower()

    def _artifact_is_inline_teams_image(artifact: dict[str, Any]) -> bool:
        return _artifact_mime_type(artifact) in _TEAMS_INLINE_IMAGE_MIME_TYPES

    def _artifact_has_teams_reference_url(artifact: dict[str, Any]) -> bool:
        candidates: list[Any] = [artifact.get("contentUrl"), artifact.get("content_url")]
        metadata = artifact.get("metadata")
        if isinstance(metadata, dict):
            candidates.extend([metadata.get("contentUrl"), metadata.get("content_url")])
            attachment_ref = metadata.get("attachment_ref")
            if isinstance(attachment_ref, dict):
                candidates.extend(
                    [attachment_ref.get("contentUrl"), attachment_ref.get("content_url")]
                )
                ref_metadata = attachment_ref.get("metadata")
                if isinstance(ref_metadata, dict):
                    candidates.extend(
                        [ref_metadata.get("contentUrl"), ref_metadata.get("content_url")]
                    )
        return any(
            str(candidate or "").strip().startswith(("http://", "https://"))
            for candidate in candidates
        )

    def _artifact_expires_at(artifact: dict[str, Any]) -> datetime:
        raw_value = artifact.get("expires_at")
        if isinstance(raw_value, datetime):
            value = raw_value
        else:
            text = str(raw_value or "").strip()
            if not text:
                raise ToolError("Communication file artifact does not include an expiry")
            try:
                value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ToolError("Communication file artifact has an invalid expiry") from exc
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _teams_artifacts_with_public_download_links(
        session_id: str,
        artifacts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        resolved_config = context.resolve_provider_config_for_session(session_id)
        updated_artifacts: list[dict[str, Any]] = []
        published_links: list[dict[str, Any]] = []
        audit_links: list[dict[str, Any]] = []
        for artifact in artifacts:
            updated = dict(artifact)
            if (
                not _artifact_is_inline_teams_image(updated)
                and not _artifact_has_teams_reference_url(updated)
            ):
                try:
                    link = issue_communication_artifact_download_link(
                        artifact_id=str(updated.get("artifact_id") or ""),
                        artifact_expires_at=_artifact_expires_at(updated),
                        public_base_url=communication_artifact_public_base_url(resolved_config),
                        secret_env=communication_artifact_link_secret_env(resolved_config),
                        ttl_seconds=resolved_config.communication_artifact_link_ttl_seconds,
                    )
                except CommunicationArtifactLinkError as exc:
                    raise ToolError(str(exc)) from exc
                updated["download_url"] = link.url
                published_links.append(
                    {
                        "artifact_id": link.artifact_id,
                        "filename": str(
                            updated.get("normalized_safe_filename")
                            or updated.get("filename")
                            or link.artifact_id
                        ),
                        "url": link.url,
                        "expires_at": link.expires_at.isoformat(),
                    }
                )
                audit_links.append(
                    {
                        "artifact_id": link.artifact_id,
                        "filename": str(
                            updated.get("normalized_safe_filename")
                            or updated.get("filename")
                            or link.artifact_id
                        ),
                        "byte_count": int(updated.get("byte_count") or 0),
                        "mime_type": updated.get("mime_type"),
                        "expires_at": link.expires_at.isoformat(),
                    }
                )
            updated_artifacts.append(updated)
        return updated_artifacts, published_links, audit_links

    def _require_ado_artifact_send(
        session: Any, artifact_payloads: list[dict[str, Any]]
    ) -> None:
        if not artifact_payloads:
            return
        context.policy.require_communication_file_send_enabled(session)
        for artifact in artifact_payloads:
            artifact_session_id = artifact.get("session_id")
            if artifact_session_id is not None and str(artifact_session_id) != session.session_id:
                raise PolicyError(
                    f"Communication file artifact is not owned by session {session.session_id}"
                )
            context.policy.require_communication_file_content(
                session,
                filename=str(
                    artifact.get("normalized_safe_filename")
                    or artifact.get("filename")
                    or "attachment"
                ),
                mime_type=artifact.get("mime_type"),
                byte_count=int(artifact.get("byte_count") or 0),
            )

    def _ado_artifacts_for_ids(
        session: Any, artifact_ids: list[str]
    ) -> list[dict[str, Any]]:
        artifacts = _artifact_payloads(artifact_ids)
        _require_ado_artifact_send(session, artifacts)
        return artifacts

    def _ado_artifact_approval_payload(payload: Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json", exclude={"request_id"}, exclude_none=True)
        if isinstance(payload, dict):
            return {key: value for key, value in payload.items() if key != "request_id"}
        return {}

    def _extract_ado_tags(remote_payload: dict[str, Any]) -> list[str]:
        fields = remote_payload.get("fields") if isinstance(remote_payload.get("fields"), dict) else {}
        raw_tags = remote_payload.get("tags")
        if raw_tags is None:
            raw_tags = fields.get("System.Tags")
        return _normalize_ado_tag_list(raw_tags)

    def _normalize_ado_tag_list(value: Any) -> list[str]:
        raw_values = value if isinstance(value, list) else str(value or "").split(";")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tag in raw_values:
            tag = str(raw_tag).strip()
            if not tag:
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(tag)
        return normalized

    def _merge_ado_tags(
        previous_tags: list[str], *, add_tags: list[str], remove_tags: list[str]
    ) -> list[str]:
        remove_keys = {tag.casefold() for tag in remove_tags}
        merged: list[str] = []
        seen: set[str] = set()
        for tag in previous_tags:
            key = tag.casefold()
            if key in remove_keys or key in seen:
                continue
            seen.add(key)
            merged.append(tag)
        for tag in add_tags:
            key = tag.casefold()
            if key in remove_keys or key in seen:
                continue
            seen.add(key)
            merged.append(tag)
        return merged

    def _communication_target_audit_fields(
        *,
        provider: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
        approval_request_id: str | None = None,
        approval_decision: str | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": provider,
            "mailbox_id": mailbox_id,
            "channel_id": channel_id,
            "channel_target": channel_target,
            "approval_request_id": approval_request_id,
            "approval_decision": approval_decision,
            "decision": "allowed",
        }

    def _coerce_attachment_refs(
        provider: str,
        message_id: str,
        raw_attachments: Any,
        *,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> list[CommunicationAttachmentRef]:
        if raw_attachments is None:
            return []
        refs: list[CommunicationAttachmentRef] = []
        for index, raw in enumerate(raw_attachments if isinstance(raw_attachments, list) else []):
            if isinstance(raw, CommunicationAttachmentRef):
                refs.append(raw)
                continue
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            attachment_id = str(
                raw.get("attachment_id")
                or raw.get("id")
                or raw.get("content_id")
                or raw.get("name")
                or index
            ).strip()
            filename = str(raw.get("filename") or raw.get("name") or attachment_id).strip()
            if not attachment_id or not filename:
                continue
            byte_count_raw = raw.get("byte_count", raw.get("size", raw.get("contentSize")))
            byte_count = None
            if byte_count_raw is not None:
                try:
                    byte_count = int(byte_count_raw)
                except (TypeError, ValueError):
                    byte_count = None
            mime_type = raw.get("mime_type") or raw.get("content_type") or raw.get("contentType")
            safe_metadata = {
                key: value
                for key, value in raw.items()
                if key
                not in {
                    "url",
                    "proxy_url",
                    "download_url",
                    "web_url",
                    "content",
                    "content_bytes",
                    "content_base64",
                    "data",
                    "authorization",
                    "token",
                    "access_token",
                }
            }
            refs.append(
                CommunicationAttachmentRef(
                    provider=normalize_inline_media_provider(provider),
                    message_id=message_id,
                    attachment_id=attachment_id,
                    filename=filename,
                    mime_type=str(mime_type).strip() if mime_type else None,
                    byte_count=byte_count,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                    metadata=safe_metadata,
                )
            )
        return refs

    def _attachments_from_metadata(
        provider: str,
        message_id: str,
        metadata: dict[str, Any],
        *,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> list[CommunicationAttachmentRef]:
        raw_attachments = metadata.get("attachments")
        if raw_attachments is None and isinstance(metadata.get("message"), dict):
            raw_attachments = metadata["message"].get("attachments")
        return _coerce_attachment_refs(
            provider,
            message_id,
            raw_attachments,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )

    def _clean_teams_channel_input(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if "://" in raw:
            parsed = urlparse(raw)
            path_parts = [part for part in parsed.path.split("/") if part]
            if "channel" in path_parts:
                index = path_parts.index("channel")
                if index + 1 < len(path_parts):
                    return unquote(path_parts[index + 1]).strip()
        cleaned = unquote(raw).strip().strip("/")
        if cleaned.startswith("chat:"):
            return cleaned
        if "/" in cleaned:
            _team_id, _separator, channel_id = cleaned.partition("/")
            return channel_id.strip()
        return cleaned

    def _teams_message_records_for_id(message_id: str) -> list[TeamsMessageEnvelope]:
        records: list[TeamsMessageEnvelope] = []
        direct = context.teams_messages.get(message_id)
        if direct is not None:
            records.append(direct)
        seen_cursors = {record.cursor for record in records}
        for record in context.teams_messages.read_all().values():
            if record.message_id != message_id or record.cursor in seen_cursors:
                continue
            records.append(record)
            seen_cursors.add(record.cursor)
        return records

    def _teams_target_for_message_record(record: TeamsMessageEnvelope) -> str:
        return teams_target_from_message(
            team_id=record.team_id,
            channel_id=record.channel_id,
            chat_id=record.chat_id,
            metadata=record.metadata,
        )

    def _teams_target_channel_id(target: str) -> str | None:
        parsed = parse_teams_target(target)
        return parsed.channel_id if parsed.kind == "channel" else None

    def _select_allowlisted_teams_target(
        session: Any,
        candidates: list[str],
        *,
        message_id: str,
    ) -> str:
        normalized: list[str] = []
        for candidate in candidates:
            try:
                normalized.append(context.policy.require_teams_channel_target(session, candidate))
            except PolicyError:
                continue
            except ValueError:
                continue
        unique = sorted(set(normalized))
        if len(unique) == 1:
            return unique[0]
        if not unique:
            raise PolicyError(
                f"Teams target is not allowlisted for session {session.session_id}"
            )
        raise PolicyError(
            f"Teams message {message_id} matches multiple allowlisted targets; "
            "pass channel_target"
        )

    def _resolve_teams_target_from_message(
        session: Any,
        *,
        message_id: str,
        channel_id: str | None = None,
    ) -> str:
        requested_channel_id = _clean_teams_channel_input(channel_id)
        candidates: list[str] = []
        for record in _teams_message_records_for_id(message_id):
            try:
                target = _teams_target_for_message_record(record)
            except ValueError:
                continue
            if requested_channel_id and _teams_target_channel_id(target) != requested_channel_id:
                continue
            candidates.append(target)
        if candidates:
            return _select_allowlisted_teams_target(
                session,
                candidates,
                message_id=message_id,
            )

        if requested_channel_id:
            for normalized in context.policy.policy_config.resolved_teams_channel_targets_for_session(
                session,
                capability="read",
            ):
                if _teams_target_channel_id(normalized) == requested_channel_id:
                    candidates.append(normalized)
            if candidates:
                return _select_allowlisted_teams_target(
                    session,
                    candidates,
                    message_id=message_id,
                )

        raise PolicyError(
            "Teams channel_target or an allowlisted stored Teams message is required "
            f"for message {message_id}"
        )

    def _resolve_teams_attachment_target(
        session: Any,
        request: CommunicationListAttachmentsRequest,
    ) -> str:
        raw_target = str(request.channel_target or "").strip()
        if raw_target:
            cleaned_target = unquote(raw_target).strip().strip("/")
            try:
                target = context.policy.require_teams_channel_target(session, cleaned_target)
                requested_channel_id = _clean_teams_channel_input(request.channel_id)
                if (
                    requested_channel_id
                    and _teams_target_channel_id(target) != requested_channel_id
                ):
                    raise PolicyError(
                        "Teams channel_id does not match the requested channel_target"
                    )
                return target
            except PolicyError as exc:
                channel_id = _clean_teams_channel_input(raw_target)
                if channel_id and (
                    channel_id != cleaned_target
                    or ("/" not in cleaned_target and not cleaned_target.startswith("chat:"))
                ):
                    return _resolve_teams_target_from_message(
                        session,
                        message_id=request.message_id,
                        channel_id=channel_id,
                    )
                raise exc

        requested_channel_id = _clean_teams_channel_input(request.channel_id)
        if requested_channel_id:
            return _resolve_teams_target_from_message(
                session,
                message_id=request.message_id,
                channel_id=requested_channel_id,
            )
        return _resolve_teams_target_from_message(session, message_id=request.message_id)

    def _resolve_communication_attachments(
        request: CommunicationListAttachmentsRequest,
        *,
        tool_name: ToolName = ToolName.COMMUNICATION_LIST_ATTACHMENTS,
    ) -> list[CommunicationAttachmentRef]:
        provider = request.provider.strip().lower()
        if provider == "discord":
            session = context.policy.require_tool(request.session_id, tool_name)
            context.policy.require_communication_file_receive_enabled(session)
            channel_id = str(request.channel_id or "").strip()
            service = context.discord_service_for_session(request.session_id)
            if hasattr(service, "list_attachments"):
                resolved_channel_id = context.policy.require_communication_file_scope(
                    session, provider=provider, channel_id=channel_id
                )
                return _coerce_attachment_refs(
                    provider,
                    request.message_id,
                    service.list_attachments(request.message_id, channel_id=resolved_channel_id),
                    channel_id=resolved_channel_id,
                )
            record = context.discord_commands.get(request.message_id)
            if record is None:
                raise ToolError(f"Unknown Discord message_id: {request.message_id}")
            resolved_channel_id = context.policy.require_communication_file_scope(
                session, provider=provider, channel_id=channel_id or record.channel_id
            )
            return _attachments_from_metadata(
                provider,
                request.message_id,
                record.metadata,
                channel_id=resolved_channel_id,
            )
        if provider == "teams":
            session = context.policy.require_tool(request.session_id, tool_name)
            context.policy.require_communication_file_receive_enabled(session)
            target = _resolve_teams_attachment_target(session, request)
            service = context.teams_service_for_session(request.session_id)
            if hasattr(service, "list_attachments"):
                return _coerce_attachment_refs(
                    provider,
                    request.message_id,
                    service.list_attachments(request.message_id, channel_target=target),
                    channel_id=_teams_target_channel_id(target),
                    channel_target=target,
                )
            record = context.teams_messages.get(request.message_id)
            if record is None:
                record = next(
                    (
                        message
                        for message in context.teams_messages.read_all().values()
                        if message.message_id == request.message_id
                    ),
                    None,
                )
            if record is None:
                raise ToolError(f"Unknown Teams message_id: {request.message_id}")
            return _attachments_from_metadata(
                provider,
                request.message_id,
                record.metadata,
                channel_id=_teams_target_channel_id(target),
                channel_target=target,
            )
        if provider == "email":
            session = context.policy.require_tool(request.session_id, tool_name)
            context.policy.require_communication_file_receive_enabled(session)
            mailbox_id = str(request.mailbox_id or "").strip()
            context.policy.require_communication_file_scope(
                session, provider=provider, mailbox_id=mailbox_id
            )
            service = context.email_service_for_session(request.session_id)
            message = service.get_message(
                mailbox_id=mailbox_id,
                message_id=request.message_id,
            )
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, message.folder)
            if hasattr(service, "list_attachments"):
                return _coerce_attachment_refs(
                    provider,
                    request.message_id,
                    service.list_attachments(
                        mailbox_id=mailbox_id, message_id=request.message_id
                    ),
                    mailbox_id=mailbox_id,
                )
            if getattr(message, "attachments", None):
                return _coerce_attachment_refs(
                    provider,
                    request.message_id,
                    message.attachments,
                    mailbox_id=mailbox_id,
                )
            return _attachments_from_metadata(
                provider,
                request.message_id,
                message.metadata,
                mailbox_id=mailbox_id,
            )
        raise ToolError("provider must be one of: discord, teams, email")

    def _provider_download_attachment(
        request: CommunicationDownloadAttachmentRequest,
        ref: CommunicationAttachmentRef,
    ) -> tuple[bytes, str, str | None]:
        provider = request.provider.strip().lower()
        service: Any
        kwargs: dict[str, Any] = {
            "message_id": request.message_id,
            "attachment_id": request.attachment_id,
        }
        if provider == "discord":
            service = context.discord_service_for_session(request.session_id)
            kwargs["channel_id"] = ref.channel_id or request.channel_id
        elif provider == "teams":
            service = context.teams_service_for_session(request.session_id)
            kwargs["channel_target"] = ref.channel_target or request.channel_target
        elif provider == "email":
            service = context.email_service_for_session(request.session_id)
            kwargs["mailbox_id"] = request.mailbox_id
        else:
            raise ToolError("provider must be one of: discord, teams, email")
        if hasattr(service, "download_attachment"):
            raw = service.download_attachment(**kwargs)
            if isinstance(raw, bytes):
                return raw, ref.filename, ref.mime_type
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if isinstance(raw, dict):
                data = raw.get("content") or raw.get("content_bytes") or raw.get("bytes")
                if isinstance(data, str):
                    if raw.get("encoding") == "base64" or raw.get("content_base64"):
                        data_bytes = base64.b64decode(raw.get("content_base64") or data)
                    else:
                        data_bytes = data.encode("utf-8")
                elif isinstance(data, bytes):
                    data_bytes = data
                else:
                    data_bytes = base64.b64decode(str(raw.get("content_base64") or ""))
                return (
                    data_bytes,
                    str(raw.get("filename") or ref.filename),
                    str(
                        raw.get("mime_type")
                        or raw.get("content_type")
                        or ref.mime_type
                        or ""
                    )
                    or None,
                )
        raw_payload = ref.metadata.get("content_base64") or ref.metadata.get("content")
        if isinstance(raw_payload, str) and raw_payload:
            try:
                return base64.b64decode(raw_payload), ref.filename, ref.mime_type
            except Exception:
                return raw_payload.encode("utf-8"), ref.filename, ref.mime_type
        raise ToolError(
            "Attachment content is not available from the configured provider adapter"
        )

    def _stage_communication_artifact(
        request: CommunicationDownloadAttachmentRequest,
        ref: CommunicationAttachmentRef,
        content: bytes,
        *,
        filename: str,
        mime_type: str | None,
    ) -> CommunicationFileArtifact:
        store = _communication_artifact_store()
        source_channel = (
            ref.channel_target or ref.channel_id or request.channel_id or request.channel_target
        )
        record = store.stage(
            provider=request.provider,
            session_id=request.session_id,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            source_message_id=request.message_id,
            source_attachment_id=request.attachment_id,
            source_channel=source_channel,
            source_mailbox=request.mailbox_id,
            metadata={
                "attachment_ref": ref.model_dump(mode="json"),
            },
        )
        return _artifact_to_model(record)

    _INLINE_MEDIA_SOURCE_KINDS = {"pr_feedback", "ado_backlog", "communication"}
    _INLINE_MEDIA_SECRET_FIELDS = {
        "authorization",
        "token",
        "access_token",
        "content",
        "content_bytes",
        "content_base64",
        "data",
        "bytes",
    }

    def _normalize_inline_media_source_kind(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _INLINE_MEDIA_SOURCE_KINDS:
            raise ToolError("source_kind must be one of: pr_feedback, ado_backlog, communication")
        return normalized

    def _inline_media_id(raw: dict[str, Any], *, fallback: str) -> str:
        return str(
            raw.get("media_id")
            or raw.get("attachment_id")
            or raw.get("id")
            or raw.get("content_id")
            or raw.get("url")
            or raw.get("filename")
            or raw.get("name")
            or fallback
        ).strip()

    def _azure_devops_inline_media_provider(
        session_id: str | None = None,
        *,
        source_kind: str = "pr_feedback",
    ) -> AzureDevOpsFeedbackAdapter:
        resolved_config = (
            context.resolve_provider_config_for_session(session_id)
            if session_id is not None
            else context.config
        )
        if source_kind == "ado_backlog":
            pat_env_name = (
                str(resolved_config.azure_devops_pat_env or "").strip()
                or "AZURE_DEVOPS_PAT"
            )
        else:
            pat_env_name = (
                str(resolved_config.azure_devops_pr_pat_env or "").strip()
                or "AZURE_DEVOPS_PR_PAT"
            )
        pat = os.environ.get(pat_env_name, "").strip()
        return AzureDevOpsFeedbackAdapter(
            organization_url=resolved_config.azure_devops_organization_url,
            project=resolved_config.azure_devops_project,
            pat=pat,
            pat_env_name=pat_env_name,
        )

    def _github_inline_media_provider(session_id: str | None = None) -> GitHubFeedbackAdapter:
        resolved_config = (
            context.resolve_provider_config_for_session(session_id)
            if session_id is not None
            else context.config
        )
        token = os.environ.get(resolved_config.github_token_env, "").strip()
        return GitHubFeedbackAdapter(token=token)

    def _inline_media_provider_for_request(
        request: ListInlineMediaRequest | DownloadInlineMediaRequest,
        ref: InlineMediaRef | None = None,
    ) -> Any:
        provider = normalize_inline_media_provider(
            str((ref.provider if ref is not None else request.provider) or "")
        )
        if provider == "github":
            return _github_inline_media_provider(request.session_id)
        if provider in {"azure_devops", "ado"}:
            if request.source_kind == "ado_backlog":
                session = context.policy.get_session(request.session_id)
                return AzureDevOpsIssueInlineMediaProvider(
                    context.provider_config,
                    directory_path=session.directory_path or "root",
                )
            return _azure_devops_inline_media_provider(
                request.session_id, source_kind=request.source_kind
            )
        if provider == "teams":
            return TeamsInlineMediaProvider(context.teams_service_for_session(request.session_id))
        return None

    def _raw_inline_media_items(resource: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        raw_media = resource.get("inline_media")
        if isinstance(raw_media, list):
            items.extend(item for item in raw_media if isinstance(item, dict))
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        metadata_media = metadata.get("inline_media") or metadata.get("images")
        if isinstance(metadata_media, list):
            items.extend(item for item in metadata_media if isinstance(item, dict))
        return items

    def _raw_inline_media_for_id(
        resource: dict[str, Any], media_id: str
    ) -> dict[str, Any] | None:
        for item in _raw_inline_media_items(resource):
            if _inline_media_id(item, fallback="") == media_id:
                return item
        return None

    def _inline_media_refs_from_raw(
        *,
        source_kind: str,
        provider: str,
        resource_id: str,
        raw_media: object,
        comment_id: str | None = None,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        if raw_media is None:
            return []
        raw_items = raw_media if isinstance(raw_media, list) else [raw_media]
        refs: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        for index, raw_item in enumerate(raw_items):
            if isinstance(raw_item, InlineMediaRef):
                refs.append((raw_item, raw_item.model_dump(mode="json")))
                continue
            if hasattr(raw_item, "model_dump"):
                raw_item = raw_item.model_dump()
            if not isinstance(raw_item, dict):
                continue
            raw_comment_id = raw_item.get("comment_id") or comment_id
            media_id = _inline_media_id(raw_item, fallback=str(index))
            filename = str(
                raw_item.get("filename")
                or raw_item.get("name")
                or raw_item.get("title")
                or f"inline-media-{index}"
            ).strip()
            if not media_id or not filename:
                continue
            byte_count_raw = raw_item.get(
                "byte_count", raw_item.get("size", raw_item.get("contentSize"))
            )
            byte_count: int | None = None
            if byte_count_raw is not None:
                try:
                    byte_count = int(byte_count_raw)
                except (TypeError, ValueError):
                    byte_count = None
            mime_type = (
                raw_item.get("mime_type")
                or raw_item.get("content_type")
                or raw_item.get("contentType")
            )
            safe_metadata = sanitize_inline_media_metadata(raw_item)
            ref = InlineMediaRef(
                source_kind=source_kind,
                provider=normalize_inline_media_provider(provider),
                resource_id=resource_id,
                comment_id=str(raw_comment_id).strip() if raw_comment_id else None,
                media_id=media_id,
                filename=filename,
                mime_type=str(mime_type).strip() if mime_type else None,
                byte_count=byte_count,
                url=(
                    str(
                        raw_item.get("url")
                        or raw_item.get("download_url")
                        or raw_item.get("web_url")
                        or ""
                    ).strip()
                    or None
                ),
                metadata=safe_metadata,
            )
            refs.append((ref, raw_item))
        return refs

    def _inline_media_from_feedback(
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        session = context.policy.require_tool(request.session_id, tool_name)
        context.policy.require_inline_media_receive_enabled(
            session, source_kind="pr_feedback"
        )
        allowed_streams = {
            (stream["provider"], stream["repo"], stream["pr_id"])
            for stream in context.policy.require_pr_feedback_streams(session)
        }
        raw_feedback = context.pr_feedback_service.store.read_raw().get("feedback", {})
        if not isinstance(raw_feedback, dict):
            return []
        raw_item = raw_feedback.get(request.resource_id)
        if not isinstance(raw_item, dict):
            raise ToolError(f"Unknown PR feedback resource_id: {request.resource_id}")
        key = (
            str(raw_item.get("provider") or ""),
            str(raw_item.get("repo") or ""),
            str(raw_item.get("pr_id") or ""),
        )
        if key not in allowed_streams:
            raise PolicyError("PR feedback stream is not allowlisted for this session")
        provider = str(raw_item.get("provider") or request.provider).strip().lower()
        comment_id = str(raw_item.get("comment_id") or "").strip() or None
        adapter = _inline_media_provider_for_request(
            request.model_copy(update={"provider": provider})
        )
        if adapter is not None and hasattr(adapter, "list_inline_media"):
            refs = adapter.list_inline_media(
                source_kind="pr_feedback",
                resource_id=request.resource_id,
                resource=raw_item,
                comment_id=comment_id,
            )
            if refs:
                return [
                    (
                        ref,
                        _raw_inline_media_for_id(raw_item, ref.media_id)
                        or ref.model_dump(mode="json"),
                    )
                    for ref in refs
                ]
        media: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        media.extend(
            _inline_media_refs_from_raw(
                source_kind="pr_feedback",
                provider=provider,
                resource_id=request.resource_id,
                raw_media=raw_item.get("inline_media"),
                comment_id=comment_id,
            )
        )
        metadata = raw_item.get("metadata") if isinstance(raw_item.get("metadata"), dict) else {}
        media.extend(
            _inline_media_refs_from_raw(
                source_kind="pr_feedback",
                provider=provider,
                resource_id=request.resource_id,
                raw_media=metadata.get("inline_media") or metadata.get("images"),
                comment_id=comment_id,
            )
        )
        return media

    def _inline_media_from_ado_backlog(
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        session = context.policy.require_tool(request.session_id, tool_name)
        context.policy.require_inline_media_receive_enabled(
            session, source_kind="ado_backlog"
        )
        issue_service = deps.issue_service_factory()
        payload = issue_service.get_remote_backlog_item_by_id(
            provider=normalize_inline_media_provider(request.provider),
            directory_path=session.directory_path or "root",
            backlog_item_id=request.resource_id,
            project=request.project,
            include_comments=True,
            include_relations=True,
            include_updates=False,
        )
        response = AdoBacklogGetResponse.model_validate(payload)
        aggregate_media = _inline_media_refs_from_raw(
            source_kind="ado_backlog",
            provider=normalize_inline_media_provider(response.provider),
            resource_id=response.id,
            raw_media=response.inline_media or response.metadata.get("inline_media"),
        )
        comment_media: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        for comment in response.comments:
            if not isinstance(comment, dict):
                continue
            comment_id = str(comment.get("comment_id") or comment.get("id") or "").strip() or None
            if request.comment_id and comment_id != request.comment_id:
                continue
            comment_media.extend(
                _inline_media_refs_from_raw(
                    source_kind="ado_backlog",
                    provider=normalize_inline_media_provider(response.provider),
                    resource_id=response.id,
                    raw_media=comment.get("inline_media") or comment.get("images"),
                    comment_id=comment_id,
                )
            )
        if request.comment_id:
            return comment_media or [
                item for item in aggregate_media if item[0].comment_id == request.comment_id
            ]
        return [item for item in aggregate_media if item[0].comment_id is None] + comment_media

    def _inline_media_from_communication(
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        list_request = CommunicationListAttachmentsRequest(
            session_id=request.session_id,
            provider=request.provider,
            message_id=request.resource_id,
            mailbox_id=request.mailbox_id,
            channel_id=request.channel_id,
            channel_target=request.channel_target,
        )
        attachments = _resolve_communication_attachments(list_request, tool_name=tool_name)
        pairs: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        for item in attachments:
            raw = item.model_dump(mode="json")
            ref = InlineMediaRef(
                source_kind="communication",
                provider=normalize_inline_media_provider(item.provider),
                resource_id=item.message_id,
                media_id=item.attachment_id,
                filename=item.filename,
                mime_type=item.mime_type,
                byte_count=item.byte_count,
                url=(
                    str(
                        item.metadata.get("url")
                        or item.metadata.get("download_url")
                        or ""
                    ).strip()
                    or None
                ),
                metadata=sanitize_inline_media_metadata(item.metadata),
            )
            pairs.append((ref, raw))
        return pairs

    def _resolve_inline_media(
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        source_kind = _normalize_inline_media_source_kind(request.source_kind)
        if source_kind == "pr_feedback":
            return _inline_media_from_feedback(request, tool_name=tool_name)
        if source_kind == "ado_backlog":
            return _inline_media_from_ado_backlog(request, tool_name=tool_name)
        return _inline_media_from_communication(request, tool_name=tool_name)

    def _inline_media_max_bytes_for_session(session: Any) -> int | None:
        return _communication_file_max_bytes_for_session(session)

    def _preflight_inline_media_content(session: Any, ref: InlineMediaRef) -> None:
        mime_type = ref.mime_type
        if not mime_type:
            allowed_mime_types = context.policy._communication_file_policy_value(
                session, "communication_file_allowed_mime_types"
            )
            if allowed_mime_types:
                # Preserve cheap filename/extension/declared-size checks without
                # rejecting solely because the HTTP response MIME type has not been
                # observed yet; the post-download content policy enforces MIME.
                mime_type = next(iter(allowed_mime_types), None)
        context.policy.require_inline_media_content(
            session,
            source_kind=ref.source_kind,
            filename=ref.filename,
            mime_type=mime_type,
            byte_count=ref.byte_count if ref.byte_count is not None else 0,
        )

    def _allowed_inline_media_hosts_for_request(
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
    ) -> list[str]:
        provider = normalize_inline_media_provider(ref.provider or request.provider)
        if provider != "azure_devops":
            return []
        resolved_config = context.resolve_provider_config_for_session(request.session_id)
        host = urlparse(resolved_config.azure_devops_organization_url).hostname
        return [host] if host else []

    def _call_inline_media_provider_download(
        provider: Any,
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
        raw: dict[str, Any],
        *,
        max_bytes: int | None,
    ) -> Any:
        kwargs: dict[str, Any] = {"media": ref, "raw": raw}
        try:
            parameters = signature(provider.download_inline_media).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "channel_target" in parameters:
            kwargs["channel_target"] = request.channel_target
        if "max_bytes" in parameters:
            kwargs["max_bytes"] = max_bytes
        return provider.download_inline_media(**kwargs)

    def _download_inline_media_bytes(
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
        raw: dict[str, Any],
    ) -> tuple[bytes, str, str | None]:
        session = context.policy.get_session(request.session_id)
        _preflight_inline_media_content(session, ref)
        max_bytes = _inline_media_max_bytes_for_session(session)
        provider = _inline_media_provider_for_request(request, ref)
        if provider is not None and hasattr(provider, "download_inline_media"):
            downloaded = _call_inline_media_provider_download(
                provider, request, ref, raw, max_bytes=max_bytes
            )
            if max_bytes is not None and len(downloaded.content) > max_bytes:
                raise PolicyError(
                    f"Inline media exceeds max bytes for session {request.session_id}: "
                    f"{len(downloaded.content)} > {max_bytes}"
                )
            return downloaded.content, downloaded.filename, downloaded.mime_type
        embedded = inline_media_download_from_embedded_payload(ref, raw)
        if embedded is not None:
            if max_bytes is not None and len(embedded.content) > max_bytes:
                raise PolicyError(
                    f"Inline media exceeds max bytes for session {request.session_id}: "
                    f"{len(embedded.content)} > {max_bytes}"
                )
            return embedded.content, embedded.filename, embedded.mime_type
        if ref.url:
            require_provider_media_url(
                provider=ref.provider,
                url=ref.url,
                allowed_hosts=_allowed_inline_media_hosts_for_request(request, ref),
            )
            content, mime_type = download_url_with_limit(
                url=ref.url,
                max_bytes=max_bytes,
                provider=ref.provider,
                allowed_hosts=_allowed_inline_media_hosts_for_request(request, ref),
            )
            return content, ref.filename, mime_type or ref.mime_type
        raise ToolError("Inline media content is not available from the configured provider metadata")

    def _stage_inline_media_artifact(
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
        content: bytes,
        *,
        filename: str,
        mime_type: str | None,
    ) -> CommunicationFileArtifact:
        record = _communication_artifact_store().stage(
            provider=ref.provider,
            session_id=request.session_id,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            source_message_id=ref.resource_id,
            source_attachment_id=ref.media_id,
            metadata={
                "source_kind": ref.source_kind,
                "inline_media_ref": ref.model_dump(mode="json"),
            },
        )
        return _artifact_to_model(record)

    def _inline_media_source_audit_fields(
        request: ListInlineMediaRequest | DownloadInlineMediaRequest,
    ) -> dict[str, Any]:
        source_kind = _normalize_inline_media_source_kind(request.source_kind)
        fields: dict[str, Any] = {
            "source_kind": source_kind,
            "provider": normalize_inline_media_provider(str(request.provider or "")),
        }
        if source_kind == "pr_feedback":
            raw_feedback = context.pr_feedback_service.store.read_raw().get("feedback", {})
            raw_item = (
                raw_feedback.get(request.resource_id) if isinstance(raw_feedback, dict) else None
            )
            if isinstance(raw_item, dict):
                fields.update(
                    {
                        "feedback_id": request.resource_id,
                        "repo": str(raw_item.get("repo") or "").strip() or None,
                        "pr_id": str(raw_item.get("pr_id") or "").strip() or None,
                        "comment_id": str(raw_item.get("comment_id") or "").strip() or None,
                    }
                )
            else:
                fields["feedback_id"] = request.resource_id
        elif source_kind == "ado_backlog":
            fields.update(
                {
                    "work_item_id": request.resource_id,
                    "project": request.project,
                    "comment_id": request.comment_id,
                }
            )
        else:
            fields.update(
                {
                    "message_id": request.resource_id,
                    "mailbox_id": request.mailbox_id,
                    "channel_id": request.channel_id,
                    "channel_target": request.channel_target,
                }
            )
        return fields

    def _inline_media_ref_audit_payload(ref: InlineMediaRef) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": ref.provider,
            "source_kind": ref.source_kind,
            "resource_id": ref.resource_id,
            "media_id": ref.media_id,
            "comment_id": ref.comment_id,
            "filename": ref.filename,
            "mime_type": ref.mime_type,
            "byte_count": ref.byte_count,
        }
        if ref.source_kind == "pr_feedback":
            payload["feedback_id"] = ref.resource_id
        elif ref.source_kind == "ado_backlog":
            payload["work_item_id"] = ref.resource_id
        else:
            payload["message_id"] = ref.resource_id
        return payload

    def _audit_inline_media_operation(
        *,
        event_type: str,
        session_id: str,
        request: ListInlineMediaRequest | DownloadInlineMediaRequest,
        media: list[InlineMediaRef],
        result: dict[str, Any],
        decision: str = "allowed",
    ) -> None:
        session = context.policy.get_session(session_id)
        source_fields = _inline_media_source_audit_fields(request)
        context.audit.log(
            AuditEvent(
                event_type=event_type,
                session_id=session_id,
                vm_id=session.vm_id,
                directory_path=session.directory_path,
                agent_role=_session_actor_role(session),
                provider=source_fields.get("provider"),
                repo=source_fields.get("repo"),
                pr_id=source_fields.get("pr_id"),
                tool_name=(
                    ToolName.LIST_INLINE_MEDIA.value
                    if event_type == "inline_media_list"
                    else ToolName.DOWNLOAD_INLINE_MEDIA.value
                ),
                actor_user_id=_session_actor_user_id(session),
                decision=decision,
                **_tool_call_audit_fields(
                    session=session,
                    tool_name=(
                        ToolName.LIST_INLINE_MEDIA
                        if event_type == "inline_media_list"
                        else ToolName.DOWNLOAD_INLINE_MEDIA
                    ),
                    args={**request.model_dump(mode="json"), **source_fields},
                    result={**result, "media": [_inline_media_ref_audit_payload(item) for item in media]},
                ),
                args={**request.model_dump(mode="json"), **source_fields},
                result={
                    **result,
                    "media": [_inline_media_ref_audit_payload(item) for item in media],
                },
            )
        )
        _mark_tool_call_audited()


    register_agent_tools(
        server,
        context,
        deps,
        transport_session_map=transport_session_map,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        require_vm_visible_in_session_directory=_require_vm_visible_in_session_directory,
    )


    register_communication_tools(
        server,
        context,
        deps,
        audit_success=lambda *args, **kwargs: audit_success(*args, **kwargs),
        audit_failure=lambda *args, **kwargs: audit_failure(*args, **kwargs),
        artifact_payloads=_artifact_payloads,
        artifact_audit_summaries=_artifact_audit_summaries,
        communication_target_audit_fields=_communication_target_audit_fields,
        record_email_draft_snapshot=_record_email_draft_snapshot,
        send_email_draft=_send_email_draft,
        audit_inline_media_operation=_audit_inline_media_operation,
        resolve_inline_media=_resolve_inline_media,
        download_inline_media_bytes=_download_inline_media_bytes,
        provider_download_attachment=_provider_download_attachment,
        stage_inline_media_artifact=_stage_inline_media_artifact,
        inline_media_ref_audit_payload=_inline_media_ref_audit_payload,
        normalize_inline_media_source_kind=_normalize_inline_media_source_kind,
        resolve_communication_attachments=_resolve_communication_attachments,
        stage_communication_artifact=_stage_communication_artifact,
        admin_service=_admin_service,
        artifact_to_model=_artifact_to_model,
        communication_artifact_store=_communication_artifact_store,
        safe_artifact_id=_safe_artifact_id,
        normalize_workspace_source_path=_normalize_workspace_source_path,
        read_agent_workspace_file=_read_agent_workspace_file,
        teams_artifacts_with_public_download_links=_teams_artifacts_with_public_download_links,
        draft_recipients_from_payload=draft_recipients_from_payload,
        filter_discord_commands_for_session=_filter_discord_commands_for_session,
        filter_teams_messages_for_session=_filter_teams_messages_for_session,
        teams_cursor_for_target=_teams_cursor_for_target,
    )


    @server.tool()

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
        if not context.policy.approval_required_for_tool(session, tool_name):
            return None
        redacted_keys = redacted_keys_for_session_id(session_id)

        approval_payload = _approval_gate_payload(
            tool_name=tool_value,
            session_id=session_id,
            arguments=sanitized_arguments,
        )
        grant_settings = context.policy.approval_grant_settings_for_tool(session, tool_name)
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
                    f"Approval request {supplied_request_id} is not for tool "
                    f"{tool_value}"
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
                dispatch_arguments["approval_request_id"] = raw_arguments.get(
                    "approval_request_id"
                )
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
