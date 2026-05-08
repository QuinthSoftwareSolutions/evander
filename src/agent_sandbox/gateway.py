from __future__ import annotations

import argparse
import base64
import contextlib
import json
import logging
import os
from datetime import datetime, timezone
from inspect import Parameter, signature
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import unquote, urlparse
from uuid import uuid4

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
import uvicorn

from agent_sandbox.bootstrap import (
    load_host_bootstrap_config,
    load_runtime_gateway_config,
    resolve_runtime_dir,
)
from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.chat import ChatStore, admin_chat_store_path
from agent_sandbox.directory_config import DIRECTORY_CONFIG_DOMAIN_FILENAMES
from agent_sandbox.messaging.agent_prompt_store import AgentPromptRecord
from agent_sandbox.messaging.agent_prompt_tokens import (
    encode_agent_prompt_response_token_from_claims,
    hash_agent_prompt_response_token,
    issue_agent_prompt_response_token,
    verify_agent_prompt_response_token,
)
from agent_sandbox.communication_artifacts import (
    CommunicationArtifactStore,
    normalize_safe_filename,
    validate_artifact_id,
)
from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.agent_exec import AgentExecEffectProvider
from agent_sandbox.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.schemas import (
    AgentCopyFilesEffectDefinition,
    AgentExecEffectDefinition,
    VmCopyFilesItemDefinition,
)
from agent_sandbox.daemons.pr_feedback_worker import (
    AzureDevOpsFeedbackAdapter,
    GitHubFeedbackAdapter,
)
from agent_sandbox.email.http_utils import ProviderRequestError
from agent_sandbox.inline_media import inline_media_download_from_embedded_payload
from agent_sandbox.email.providers import EmailDraftAttachment
from agent_sandbox.issues.service import AzureDevOpsIssueInlineMediaProvider, IssueService
from agent_sandbox.models import (
    AdoBacklogCreateRequest,
    AdoBacklogCreateResponse,
    AdoCurrentIterationBacklogRequest,
    AdoCurrentIterationBacklogResponse,
    AdoBacklogGetRequest,
    AdoBacklogGetResponse,
    AdoBacklogQueryRequest,
    AdoBacklogQueryResponse,
    AuditEvent,
    CommunicationAttachmentRef,
    CommunicationCopyArtifactToWorkspaceRequest,
    CommunicationCopyArtifactToWorkspaceResponse,
    CommunicationDownloadAttachmentRequest,
    CommunicationDownloadAttachmentResponse,
    CommunicationFileArtifact,
    DownloadInlineMediaRequest,
    DownloadInlineMediaResponse,
    InlineMediaRef,
    ListInlineMediaRequest,
    ListInlineMediaResponse,
    CommunicationListAttachmentsRequest,
    CommunicationListAttachmentsResponse,
    CommunicationSendMessageWithAttachmentsRequest,
    CommunicationSendMessageWithAttachmentsResponse,
    CommunicationStageWorkspaceFileRequest,
    CommunicationStageWorkspaceFileResponse,
    DiscordCommandEnvelope,
    GetPullRequestRequest,
    HasPendingDiscordMessagesRequest,
    HasPendingFeedbackRequest,
    ListPendingAgentPromptsRequest,
    ListPendingAgentPromptsResponse,
    ListUnreadDiscordMessagesRequest,
    ListPullRequestsRequest,
    ListUnreadFeedbackRequest,
    MakePhoneCallRequest,
    MakePhoneCallResponse,
    PendingAgentPrompt,
    PostPrCommentRequest,
    PostPrFeedbackReplyRequest,
    PostPrInlineCommentRequest,
    PromptAgentRequest,
    PromptAgentResponse,
    RunScriptRequest,
    RunScriptResponse,
    GetPhoneCallResultRequest,
    RequestPrReviewRequest,
    RespondToAgentRequest,
    RespondToAgentResponse,
    ScheduleResumeRequest,
    ScheduleResumeResponse,
    SubmitPrReviewRequest,
    WakeScheduleDefinition,
    TeamsMessageEnvelope,
    ToolName,
)
from agent_sandbox.policy import PolicyError
from agent_sandbox.retell_phone import (
    RetellWebhookSignatureError,
    retell_api_key_from_config,
    sanitize_retell_metadata,
    verify_retell_webhook_signature,
)
from agent_sandbox.runtime import RuntimeContext, build_runtime
from agent_sandbox.services import (
    TeamsInlineMediaProvider,
    normalize_pr_feedback_provider,
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)

logger = logging.getLogger(__name__)

_WORKSPACE_FILE_PAYLOAD_BEGIN = "__AGENT_SANDBOX_WORKSPACE_FILE_JSON_BEGIN__"
_WORKSPACE_FILE_PAYLOAD_END = "__AGENT_SANDBOX_WORKSPACE_FILE_JSON_END__"
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


def _normalize_issue_status_filters(statuses: list[str] | None) -> set[str]:
    if not statuses:
        return {"closed", "completed"}
    normalized = {str(status).strip().lower() for status in statuses if str(status).strip()}
    if not normalized:
        return {"closed", "completed"}
    return normalized


def _issue_kind_for_role(role: str | None) -> str | None:
    normalized_role = str(role or "").strip()
    if normalized_role == "idea_generator":
        return "feature"
    if normalized_role == "bug_finder":
        return "bug"
    if normalized_role == "refactor_finder":
        return "refactor"
    return None


def _normalize_agent_status(state: str | None) -> str:
    normalized = str(state or "").strip().lower()
    if normalized == "running":
        return "active"
    if normalized in {"idle", "not_started"}:
        return "idle"
    return "unknown"


def _load_agent_prompt_token_secret(config: GatewayConfig) -> str:
    env_var_name = str(config.agent_prompt_token_secret_env or "").strip()
    if not env_var_name:
        raise ToolError("agent_prompt_token_secret_env must be configured")
    secret = os.environ.get(env_var_name, "").strip()
    if not secret:
        raise ToolError(
            f"Agent prompt token secret is missing in environment variable {env_var_name!r}"
        )
    return secret


def _issue_agent_prompt_response_token_for_session(
    context: RuntimeContext,
    *,
    sender_session_id: str,
    target_session_id: str,
    message_id: str,
    issued_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    resolved_config = context.resolve_provider_config_for_session(target_session_id)
    return issue_agent_prompt_response_token(
        secret=_load_agent_prompt_token_secret(resolved_config),
        message_id=message_id,
        sender_session_id=sender_session_id,
        target_session_id=target_session_id,
        issued_at=issued_at,
        ttl_seconds=resolved_config.agent_prompt_token_ttl_seconds,
    )


def _get_or_refresh_agent_prompt_response_token(
    context: RuntimeContext,
    *,
    message: AgentPromptRecord,
    refresh_expired: bool,
) -> tuple[AgentPromptRecord, str, dict[str, Any]]:
    resolved_config = context.resolve_provider_config_for_session(message.target_session_id)
    secret = _load_agent_prompt_token_secret(resolved_config)
    allow_refresh = refresh_expired and message.status == "unread" and message.token_used_at is None
    stored_claims = dict(message.issued_token_claims or {})
    if stored_claims:
        try:
            issued_response_token = encode_agent_prompt_response_token_from_claims(
                secret=secret,
                claims=stored_claims,
            )
            issued_token_hash = hash_agent_prompt_response_token(issued_response_token)
            if issued_token_hash == message.issued_token_hash:
                expires_at = int(stored_claims["expires_at"])
                if datetime.now(timezone.utc).timestamp() < float(expires_at) or not allow_refresh:
                    return message, issued_response_token, stored_claims
        except (ValueError, KeyError, TypeError):
            pass
    if not allow_refresh:
        raise ToolError(
            f"No active response token is available for message_id: {message.message_id}"
        )

    issued_response_token, issued_claims = _issue_agent_prompt_response_token_for_session(
        context,
        sender_session_id=message.sender_session_id,
        target_session_id=message.target_session_id,
        message_id=message.message_id,
    )
    issued_token_hash = hash_agent_prompt_response_token(issued_response_token)
    refreshed = context.agent_prompt_store.replace_issued_token(
        message.message_id,
        issued_token_hash=issued_token_hash,
        issued_token_claims=issued_claims,
    )
    if refreshed is None:
        raise ToolError(f"Unknown message_id: {message.message_id}")
    return refreshed, issued_response_token, issued_claims


def _issue_creation_metadata(session: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "generated_by_session_id": session.session_id,
    }
    if session.vm_id:
        metadata["generated_by_vm_id"] = session.vm_id
    automation_role = None
    if isinstance(session.metadata, dict):
        automation_role = session.metadata.get("automation_role")
    if automation_role:
        metadata["generated_by_role"] = str(automation_role)
        issue_kind = _issue_kind_for_role(str(automation_role))
        if issue_kind is not None:
            metadata["issue_kind"] = issue_kind
    return metadata


def _method_accepts_keyword_argument(method: Any, parameter_name: str) -> bool:
    try:
        method_signature = signature(method)
    except (TypeError, ValueError):
        return False
    for parameter in method_signature.parameters.values():
        if parameter.kind == Parameter.VAR_KEYWORD:
            return True
        if parameter.name == parameter_name and parameter.kind in {
            Parameter.POSITIONAL_OR_KEYWORD,
            Parameter.KEYWORD_ONLY,
        }:
            return True
    return False


def _payload_from_result(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    return {"value": result}


def _admin_result_payload_or_raise(result: Any, action: str) -> dict[str, Any]:
    payload = _payload_from_result(result) or {}
    if payload.get("ok", True):
        return payload
    errors = payload.get("errors")
    messages: list[str] = []
    if isinstance(errors, list):
        for error in errors:
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                if message:
                    messages.append(message)
    detail = "; ".join(messages) or f"{action} failed"
    raise ValueError(detail)


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
    tool_name: ToolName,
    args: dict[str, Any],
    result: dict[str, Any],
    redacted_keys: set[str],
    *,
    default_status: str,
) -> dict[str, Any]:
    if tool_name not in _ASSISTANT_MUTATION_TOOLS:
        return dict(result)

    audit_result = _redact_nested_value(redacted_keys, dict(result))
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


def _assistant_allowed_chat_thread(session: Any, thread_id: str) -> bool:
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    if not isinstance(metadata, dict):
        return False
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        return False
    chat_threads = metadata.get("admin_chat_threads")
    if not isinstance(chat_threads, dict):
        return False
    for raw_mapping in chat_threads.values():
        if not isinstance(raw_mapping, dict):
            continue
        if str(raw_mapping.get("thread_id") or "").strip() == normalized_thread_id:
            return True
    return False


def _assistant_require_writable_scope(session: Any, requested_scope: str) -> None:
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    mutation_policy = metadata.get("assistant_mutation_policy") if isinstance(metadata, dict) else None
    if not isinstance(mutation_policy, dict):
        raise PolicyError(
            f"Session {getattr(session, 'session_id', '<unknown>')} is missing metadata.assistant_mutation_policy"
        )
    raw_scopes = mutation_policy.get("writable_scopes")
    if not isinstance(raw_scopes, list):
        raise PolicyError(
            f"Session {getattr(session, 'session_id', '<unknown>')} has no assistant writable scopes"
        )
    normalized_scope = str(requested_scope or "").strip()
    allowed_scopes = [str(scope or "").strip() for scope in raw_scopes if str(scope or "").strip()]
    if normalized_scope not in allowed_scopes:
        raise PolicyError(
            f"Assistant mutation scope {normalized_scope!r} is not writable for session {getattr(session, 'session_id', '<unknown>')}"
        )


def _resolve_session_id_from_context(
    session_id: str | None,
    ctx: Context | None,
    *,
    transport_session_map: dict[str, str] | None = None,
) -> str:
    normalized = str(session_id or "").strip()
    if normalized:
        return normalized
    if ctx is None:
        raise ToolError("session_id is required")
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        raise ToolError("session_id is required")
    headers = getattr(request, "headers", None)
    transport_session_id = ""
    if headers is not None:
        transport_session_id = str(headers.get("mcp-session-id") or "").strip()
    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        candidate = str(query_params.get("session_id") or "").strip()
        if candidate:
            if transport_session_id and transport_session_map is not None:
                transport_session_map[transport_session_id] = candidate
            return candidate
    if headers is not None:
        candidate = str(headers.get("x-agent-sandbox-session-id") or "").strip()
        if candidate:
            if transport_session_id and transport_session_map is not None:
                transport_session_map[transport_session_id] = candidate
            return candidate
    if transport_session_id and transport_session_map is not None:
        candidate = str(transport_session_map.get(transport_session_id) or "").strip()
        if candidate:
            return candidate
    raise ToolError("session_id is required")


def _resolve_session_id_from_request(request: Request) -> str:
    candidate = str(request.headers.get("x-agent-sandbox-session-id") or "").strip()
    if candidate:
        return candidate
    candidate = str(request.query_params.get("session_id") or "").strip()
    if candidate:
        return candidate
    raise PolicyError("session_id is required")


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


def _visible_vm_summaries_for_session(
    context: RuntimeContext,
    session: Any,
    *,
    vm_user: str,
) -> list[Any]:
    return AdminService(context.config.runtime_dir).list_vms(
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

    def audit_success(
        session_id: str,
        tool_name: ToolName,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        session = context.policy.get_session(session_id)
        actor_user_id = str(
            session.metadata.get("user_id") or session.metadata.get("actor_user_id") or ""
        ).strip()
        if not actor_user_id:
            actor_user_id = None
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
                vm_id=session.vm_id,
                tool_name=tool_name.value,
                actor_user_id=actor_user_id,
                decision="allowed",
                args=redacted_args,
                result=audit_result,
            )
        )
        logger.info(
            "metric_mcp_tool_decision outcome=allowed tool=%s session_id=%s",
            tool_name.value,
            session_id,
        )

    def audit_failure(
        session_id: str | None,
        tool_name: ToolName,
        args: dict[str, Any],
        error: str,
        *,
        decision: str = "denied",
        error_class: str = "policy_error",
    ) -> None:
        vm_id = None
        actor_user_id = None
        if session_id:
            session = context.session_repository.get_session(session_id)
            vm_id = None if session is None else session.vm_id
            if session is not None:
                candidate = str(
                    session.metadata.get("user_id") or session.metadata.get("actor_user_id") or ""
                ).strip()
                actor_user_id = candidate or None
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
                tool_name=tool_name.value,
                actor_user_id=actor_user_id,
                decision=decision,
                args=redacted_args,
                result=failure_result,
            )
        )
        logger.info(
            "metric_mcp_tool_decision outcome=%s tool=%s session_id=%s reason=%s error_class=%s",
            decision,
            tool_name.value,
            session_id or "",
            error,
            error_class,
        )


    def _communication_artifact_store() -> CommunicationArtifactStore:
        store = CommunicationArtifactStore(context.config.runtime_dir)
        store.cleanup_expired()
        return store

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
                    provider=provider,
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
            for target in session.teams_channel_allowlist:
                try:
                    normalized = normalize_teams_target(target)
                except ValueError:
                    continue
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
                context.policy.require_communication_file_scope(
                    session, provider=provider, channel_id=channel_id
                )
                return _coerce_attachment_refs(
                    provider,
                    request.message_id,
                    service.list_attachments(request.message_id, channel_id=channel_id),
                    channel_id=channel_id,
                )
            record = context.discord_commands.get(request.message_id)
            if record is None:
                raise ToolError(f"Unknown Discord message_id: {request.message_id}")
            context.policy.require_communication_file_scope(
                session, provider=provider, channel_id=channel_id or record.channel_id
            )
            return _attachments_from_metadata(
                provider,
                request.message_id,
                record.metadata,
                channel_id=channel_id or record.channel_id,
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
            kwargs["channel_id"] = request.channel_id
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
        source_channel = ref.channel_target or request.channel_id or request.channel_target
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
    _INLINE_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif"}
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
        provider = (
            str((ref.provider if ref is not None else request.provider) or "")
            .strip()
            .lower()
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
            safe_metadata = {
                str(key): value
                for key, value in raw_item.items()
                if str(key) not in _INLINE_MEDIA_SECRET_FIELDS
            }
            ref = InlineMediaRef(
                source_kind=source_kind,
                provider=provider,
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
        context.policy.require_communication_file_receive_enabled(session)
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
        context.policy.require_communication_file_receive_enabled(session)
        issue_service = IssueService(context.provider_config)
        payload = issue_service.get_remote_backlog_item_by_id(
            provider=request.provider.strip().lower(),
            directory_path=session.directory_path or "root",
            backlog_item_id=request.resource_id,
            project=request.project,
            include_comments=True,
            include_relations=True,
            include_updates=False,
        )
        response = AdoBacklogGetResponse.model_validate(payload)
        media: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        media.extend(
            _inline_media_refs_from_raw(
                source_kind="ado_backlog",
                provider=response.provider,
                resource_id=response.id,
                raw_media=response.inline_media or response.metadata.get("inline_media"),
            )
        )
        for comment in response.comments:
            if not isinstance(comment, dict):
                continue
            comment_id = str(comment.get("comment_id") or comment.get("id") or "").strip() or None
            if request.comment_id and comment_id != request.comment_id:
                continue
            media.extend(
                _inline_media_refs_from_raw(
                    source_kind="ado_backlog",
                    provider=response.provider,
                    resource_id=response.id,
                    raw_media=comment.get("inline_media") or comment.get("images"),
                    comment_id=comment_id,
                )
            )
        return media

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
                provider=item.provider,
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
                metadata=item.metadata,
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

    def _download_inline_media_bytes(
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
        raw: dict[str, Any],
    ) -> tuple[bytes, str, str | None]:
        provider = _inline_media_provider_for_request(request, ref)
        if provider is not None and hasattr(provider, "download_inline_media"):
            downloaded = provider.download_inline_media(media=ref, raw=raw)
            return downloaded.content, downloaded.filename, downloaded.mime_type
        embedded = inline_media_download_from_embedded_payload(ref, raw)
        if embedded is not None:
            return embedded.content, embedded.filename, embedded.mime_type
        if ref.url:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(ref.url)
                response.raise_for_status()
                mime_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or ref.mime_type
                return response.content, ref.filename, mime_type
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

    def _admin_service() -> AdminService:
        return AdminService(context.config.runtime_dir)

    @server.tool()
    def assistant_read_status(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id}
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_READ_STATUS
            )
            service = _admin_service()
            settings = service.read_assistant_settings()
            workflow_runs = service.read_workflow_run_snapshot()
            result = {
                "assistant": {
                    "enabled": bool(settings.get("enabled")),
                    "read_only": bool(settings.get("read_only", True)),
                    "default_model": settings.get("default_model"),
                    "capability_profile": session.metadata.get("capability_profile"),
                },
                "session_id": session.session_id,
                "directory_path": session.directory_path,
                "active_workflow_runs": len(workflow_runs.runs),
            }
            audit_success(resolved_session_id, ToolName.ASSISTANT_READ_STATUS, args, result)
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_READ_STATUS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_READ_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_list_agents(
        backend: str | None = None,
        include_inactive: bool = True,
        directory_path: str | None = None,
        all_directories: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "backend": backend,
            "include_inactive": include_inactive,
            "directory_path": directory_path,
            "all_directories": all_directories,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_LIST_AGENTS
            )
            list_all_directories = bool(all_directories and not directory_path)
            target_directory = directory_path or session.directory_path
            records = _admin_service().list_agents(
                directory_path=target_directory,
                direct_only=False,
                include_inactive=include_inactive,
                backend=backend,
                all_directories=list_all_directories,
            )
            agents = [
                record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
                for record in records
            ]
            result = {
                "agents": agents,
                "count": len(agents),
                "directory_path": None if list_all_directories else target_directory,
                "all_directories": list_all_directories,
            }
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_LIST_AGENTS,
                args,
                {
                    "count": len(agents),
                    "directory_path": result["directory_path"],
                    "all_directories": list_all_directories,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_LIST_AGENTS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_LIST_AGENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_inspect_workflows(
        include_runs: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id, "include_runs": include_runs}
        try:
            context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_INSPECT_WORKFLOWS
            )
            service = _admin_service()
            document = service.read_workflow_editor_document()
            result = {
                "config_path": document.config_path,
                "revision": document.revision,
                "config": document.config,
            }
            if include_runs:
                runs = service.read_workflow_run_snapshot()
                result["runs"] = runs.model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_INSPECT_WORKFLOWS,
                args,
                {"revision": document.revision, "include_runs": include_runs},
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id, ToolName.ASSISTANT_INSPECT_WORKFLOWS, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_INSPECT_WORKFLOWS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_read_config(
        scope: Literal["directory", "global", "worker_global"] = "directory",
        domain: str | None = None,
        directory_path: str | None = None,
        worker_id: str | None = None,
        include_effective: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read directory-scoped, assistant/global, or worker-global config."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "scope": scope,
            "domain": domain,
            "directory_path": directory_path,
            "worker_id": worker_id,
            "include_effective": include_effective,
        }

        def directory_snapshot(
            service: AdminService,
            target_directory: str,
            target_domain: str,
        ) -> dict[str, Any]:
            snapshot: dict[str, Any] = {
                "scope": "directory",
                "domain": target_domain,
                "directory_path": target_directory,
            }
            if target_domain == "gateway":
                snapshot["raw"] = None
            else:
                raw_result = service.read_directory_domain_config(
                    target_directory, target_domain
                )
                raw_payload = _admin_result_payload_or_raise(
                    raw_result, f"read directory config {target_domain!r}"
                )
                raw_data = raw_payload.get("data") if isinstance(raw_payload, dict) else None
                snapshot["raw"] = {
                    "exists": bool(raw_data.get("exists")) if isinstance(raw_data, dict) else False,
                    "config": raw_data.get("config") if isinstance(raw_data, dict) else None,
                    "result": raw_payload,
                }

            if include_effective or target_domain == "gateway":
                effective_result = service.compute_directory_effective_config(
                    target_directory, target_domain
                )
                effective_payload = _admin_result_payload_or_raise(
                    effective_result,
                    f"compute effective directory config {target_domain!r}",
                )
                effective_data = (
                    effective_payload.get("data")
                    if isinstance(effective_payload, dict)
                    else None
                )
                resolution = (
                    effective_data.get("resolution")
                    if isinstance(effective_data, dict)
                    else None
                )
                effective_config = (
                    resolution.get("effective_config")
                    if isinstance(resolution, dict)
                    else None
                )
                snapshot["effective"] = {
                    "config": effective_config,
                    "resolution": resolution,
                    "result": effective_payload,
                }
                snapshot["validation_diagnostics"] = effective_payload.get(
                    "validation_diagnostics", []
                )
            return snapshot

        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_READ_CONFIG
            )
            service = _admin_service()
            normalized_scope = str(scope or "directory").strip() or "directory"
            normalized_domain = str(domain or "").strip()

            if normalized_scope == "global":
                target_domain = normalized_domain or "assistant_settings"
                if target_domain == "assistant_settings":
                    path = service.assistant_settings_path()
                    result = {
                        "scope": "global",
                        "domain": target_domain,
                        "path": str(path),
                        "exists": path.exists(),
                        "config": service.read_assistant_settings(),
                    }
                elif target_domain == "host":
                    host_config = load_host_bootstrap_config(
                        runtime_dir=service.config.runtime_dir
                    )
                    result = {
                        "scope": "global",
                        "domain": target_domain,
                        "path": str(host_config.runtime_dir / "host-config.json"),
                        "config": host_config.model_dump(mode="json"),
                    }
                elif target_domain == "gateway":
                    result = {
                        "scope": "global",
                        "domain": target_domain,
                        "config_kind": "effective_root_gateway",
                        "config": service.config.model_dump(mode="json"),
                    }
                else:
                    raise ValueError(
                        "Unsupported global config domain. Use one of: "
                        "assistant_settings, host, gateway."
                    )
                audit_success(
                    resolved_session_id,
                    ToolName.ASSISTANT_READ_CONFIG,
                    args,
                    {"scope": "global", "domain": target_domain},
                )
                return result

            if normalized_scope == "worker_global":
                target_worker_id = str(worker_id or normalized_domain or "").strip()
                if not target_worker_id:
                    raise ValueError(
                        "worker_id is required for worker_global config reads"
                    )
                payload = service.read_worker_global_config(target_worker_id).model_dump(
                    mode="json"
                )
                result = {"scope": "worker_global", **payload}
                audit_success(
                    resolved_session_id,
                    ToolName.ASSISTANT_READ_CONFIG,
                    args,
                    {
                        "scope": "worker_global",
                        "worker_id": target_worker_id,
                        "exists": payload.get("exists"),
                    },
                )
                return result

            if normalized_scope != "directory":
                raise ValueError(
                    "Unsupported config scope. Use one of: directory, global, worker_global."
                )

            target_directory = directory_path or session.directory_path
            if normalized_domain:
                result = directory_snapshot(service, target_directory, normalized_domain)
                audit_result = {
                    "scope": "directory",
                    "directory_path": target_directory,
                    "domain": normalized_domain,
                    "include_effective": include_effective,
                }
            else:
                snapshots = {
                    target_domain: directory_snapshot(
                        service,
                        target_directory,
                        target_domain,
                    )
                    for target_domain in sorted(DIRECTORY_CONFIG_DOMAIN_FILENAMES)
                }
                result = {
                    "scope": "directory",
                    "directory_path": target_directory,
                    "domains": list(snapshots),
                    "snapshots": snapshots,
                }
                audit_result = {
                    "scope": "directory",
                    "directory_path": target_directory,
                    "domain_count": len(snapshots),
                    "include_effective": include_effective,
                }
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_READ_CONFIG,
                args,
                audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_READ_CONFIG, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_READ_CONFIG,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_upsert_config(
        domain: str,
        payload: dict[str, Any],
        directory_path: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "domain": domain,
            "directory_path": directory_path,
            "after": payload,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_UPSERT_CONFIG
            )
            service = _admin_service()
            normalized_domain = str(domain or "").strip()
            if normalized_domain == "assistant_settings":
                _assistant_require_writable_scope(session, "config/assistant")
                before = service.read_assistant_settings()
                after = service.write_assistant_settings(payload)
                result = {
                    "status": "success",
                    "domain": normalized_domain,
                    "config": after,
                    "before_after_summary": {"before": before, "after": after},
                }
            else:
                _assistant_require_writable_scope(session, "config/directory")
                target_directory = directory_path or session.directory_path
                before_payload = service.read_directory_domain_config(
                    target_directory, normalized_domain
                )
                write_result = service.write_directory_domain_config(
                    target_directory, normalized_domain, payload
                )
                after_payload = (
                    write_result.model_dump(mode="json")
                    if hasattr(write_result, "model_dump")
                    else _payload_from_result(write_result)
                )
                result = {
                    "status": "success",
                    "domain": normalized_domain,
                    "directory_path": target_directory,
                    "config": after_payload,
                    "before_after_summary": {
                        "before": _payload_from_result(before_payload),
                        "after": after_payload,
                    },
                }
            args["before"] = result["before_after_summary"]["before"]
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_UPSERT_CONFIG,
                args,
                result,
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_UPSERT_CONFIG, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_UPSERT_CONFIG,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_create_workflow(
        config: dict[str, Any],
        layout: dict[str, Any] | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id, "after": config}
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_CREATE_WORKFLOW
            )
            _assistant_require_writable_scope(session, "config/workflow")
            service = _admin_service()
            before = service.read_workflow_editor_document().config
            saved = service.save_workflow_editor_document(config=config, layout=layout)
            result = {
                "status": "success",
                "config_path": saved.config_path,
                "revision": saved.revision,
                "config": saved.config,
                "before_after_summary": {"before": before, "after": saved.config},
            }
            args["before"] = before
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                args,
                result,
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id, ToolName.ASSISTANT_CREATE_WORKFLOW, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_run_safe_operation(
        operation: str,
        parameters: dict[str, Any] | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "operation": operation,
            "parameters": parameters or {},
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_RUN_SAFE_OPERATION
            )
            _assistant_require_writable_scope(session, "runtime/agents")
            service = _admin_service()
            params = parameters or {}
            normalized_operation = str(operation or "").strip()
            if normalized_operation == "clear_workflow_run":
                result_payload = service.clear_workflow_run(str(params.get("event_id") or ""))
            elif normalized_operation == "resume_workflow_run":
                result_payload = service.resume_workflow_run(str(params.get("event_id") or ""))
            else:
                raise ToolError(f"Unsupported safe operation: {normalized_operation}")
            result = (
                result_payload.model_dump(mode="json")
                if hasattr(result_payload, "model_dump")
                else _payload_from_result(result_payload) or {}
            )
            result.setdefault("status", "success")
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_RUN_SAFE_OPERATION,
                args,
                result,
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id, ToolName.ASSISTANT_RUN_SAFE_OPERATION, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_RUN_SAFE_OPERATION,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_post_chat_reply(
        thread_id: str,
        content: str,
        user_message_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "thread_id": thread_id,
            "user_message_id": user_message_id,
            "content": content,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_POST_CHAT_REPLY
            )
            if not _assistant_allowed_chat_thread(session, thread_id):
                raise PolicyError(
                    f"Chat thread {thread_id!r} is not mapped to assistant session {resolved_session_id}"
                )
            normalized_content = str(content or "").strip()
            if not normalized_content:
                raise ToolError("content is required")
            store = ChatStore(admin_chat_store_path(context.config.runtime_dir))
            message = store.append_assistant_reply(
                thread_id=thread_id,
                content=normalized_content,
                user_message_id=user_message_id,
            )
            result = {"thread_id": thread_id, "message": message}
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_POST_CHAT_REPLY,
                args,
                {"thread_id": thread_id, "message_id": message.get("id")},
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id, ToolName.ASSISTANT_POST_CHAT_REPLY, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_POST_CHAT_REPLY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def prompt_agent(
        session_id: str | None = None,
        target_vm_name: str | None = None,
        target_session_id: str | None = None,
        prompt: str = "",
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        vm_user: str = "agent",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "target_vm_name": target_vm_name,
            "target_session_id": target_session_id,
            "prompt": prompt,
            "metadata": metadata or {},
            "idempotency_key": idempotency_key,
            "vm_user": vm_user,
        }
        try:
            request = PromptAgentRequest(
                session_id=resolved_session_id,
                target_vm_name=target_vm_name,
                target_session_id=target_session_id,
                prompt=prompt,
                metadata=metadata or {},
                idempotency_key=idempotency_key.strip()
                if isinstance(idempotency_key, str)
                else None,
            )
            args = request.model_dump(mode="json")
            args["vm_user"] = vm_user
            source_session = context.policy.require_tool(request.session_id, ToolName.PROMPT_AGENT)

            resolved_target_session_id = ""
            if request.target_vm_name:
                summary = _require_vm_visible_in_session_directory(
                    context,
                    source_session,
                    vm_name=request.target_vm_name,
                    vm_user=vm_user,
                )
                if not summary.session_ids:
                    raise ToolError(
                        f"VM '{request.target_vm_name}' has no visible target session bound for prompt dispatch."
                    )
                if len(summary.session_ids) > 1:
                    raise ToolError(
                        f"VM '{request.target_vm_name}' has multiple visible sessions; use target_session_id instead."
                    )
                resolved_target_session_id = summary.session_ids[0]
            else:
                resolved_target_session_id = str(request.target_session_id or "").strip()
                if not resolved_target_session_id:
                    raise ToolError("target_session_id is required")
                _require_target_session_visible_within_directory(
                    context,
                    source_session,
                    target_session_id=resolved_target_session_id,
                    vm_user=vm_user,
                )
            context.policy.require_tool(resolved_target_session_id, ToolName.RESPOND_TO_AGENT)

            if request.idempotency_key:
                existing = context.agent_prompt_store.find_prompt_by_idempotency_key(
                    sender_session_id=request.session_id,
                    idempotency_key=request.idempotency_key,
                )
                if existing is not None:
                    if existing.target_session_id != resolved_target_session_id:
                        raise ToolError(
                            "idempotency_key is already bound to a different target_session_id"
                        )
                    existing_payload = dict(existing.prompt_payload or {})
                    if str(existing_payload.get("prompt") or "").strip() != request.prompt:
                        raise ToolError("idempotency_key is already bound to a different prompt")
                    if dict(existing_payload.get("metadata") or {}) != request.metadata:
                        raise ToolError("idempotency_key is already bound to different metadata")
                    existing, issued_response_token, _issued_claims = (
                        _get_or_refresh_agent_prompt_response_token(
                            context,
                            message=existing,
                            refresh_expired=True,
                        )
                    )
                    response = PromptAgentResponse(
                        message_id=existing.message_id,
                        target_session_id=existing.target_session_id,
                        status=existing.status,
                        response_token=issued_response_token,
                    ).model_dump(mode="json")
                    audit_success(
                        resolved_session_id,
                        ToolName.PROMPT_AGENT,
                        args,
                        {
                            "message_id": response["message_id"],
                            "target_session_id": response["target_session_id"],
                            "status": response["status"],
                            "response_token": "<redacted>",
                        },
                    )
                    return response

            now = datetime.now(timezone.utc)
            message_id = f"agent-prompt-{uuid4().hex}"
            issued_response_token, issued_claims = _issue_agent_prompt_response_token_for_session(
                context,
                message_id=message_id,
                sender_session_id=request.session_id,
                target_session_id=resolved_target_session_id,
                issued_at=now,
            )
            issued_token_hash = hash_agent_prompt_response_token(issued_response_token)
            created = context.agent_prompt_store.create_prompt_message(
                AgentPromptRecord(
                    message_id=message_id,
                    sender_session_id=request.session_id,
                    sender_vm_id=source_session.vm_id,
                    target_session_id=resolved_target_session_id,
                    target_vm_id=None,
                    prompt_payload={
                        "prompt": request.prompt,
                        "metadata": request.metadata,
                        "idempotency_key": request.idempotency_key,
                    },
                    issued_token_hash=issued_token_hash,
                    issued_token_claims=issued_claims,
                    status="unread",
                    created_at=now,
                    updated_at=now,
                )
            )
            if not created:
                raise ToolError("Failed to persist agent prompt message")

            response = PromptAgentResponse(
                message_id=message_id,
                target_session_id=resolved_target_session_id,
                status="unread",
                response_token=issued_response_token,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                {
                    "message_id": response["message_id"],
                    "target_session_id": response["target_session_id"],
                    "status": response["status"],
                    "response_token": "<redacted>",
                },
            )
            return response
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.PROMPT_AGENT, args, str(exc))
            raise
        except ValueError as exc:
            message = str(exc)
            audit_failure(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                message,
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(message) from exc
        except ToolError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.PROMPT_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_pending_agent_prompts(
        session_id: str | None = None,
        limit: int | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "limit": limit,
        }
        try:
            request = ListPendingAgentPromptsRequest(session_id=resolved_session_id, limit=limit)
            args = request.model_dump(mode="json")
            context.policy.require_tool(request.session_id, ToolName.LIST_PENDING_AGENT_PROMPTS)
            messages = context.agent_prompt_store.list_unread_prompts(
                target_session_id=request.session_id,
                limit=request.limit,
            )

            prompts: list[PendingAgentPrompt] = []
            for message in messages:
                message, issued_response_token, issued_claims = (
                    _get_or_refresh_agent_prompt_response_token(
                        context,
                        message=message,
                        refresh_expired=False,
                    )
                )
                prompt_payload = dict(message.prompt_payload or {})
                prompts.append(
                    PendingAgentPrompt(
                        message_id=message.message_id,
                        sender_session_id=message.sender_session_id,
                        target_session_id=message.target_session_id,
                        prompt=str(prompt_payload.get("prompt") or "").strip(),
                        metadata=dict(prompt_payload.get("metadata") or {}),
                        status=message.status,
                        created_at=message.created_at,
                        expires_at=datetime.fromtimestamp(
                            int(issued_claims["expires_at"]),
                            tz=timezone.utc,
                        ),
                        response_token=issued_response_token,
                        response_token_claims=issued_claims,
                    )
                )

            result = ListPendingAgentPromptsResponse(prompts=prompts).model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                {"count": len(prompts)},
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.LIST_PENDING_AGENT_PROMPTS, args, str(exc))
            raise
        except ValueError as exc:
            message = str(exc)
            audit_failure(
                resolved_session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                message,
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(message) from exc
        except ToolError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_PENDING_AGENT_PROMPTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def respond_to_agent(
        session_id: str | None = None,
        message_id: str = "",
        response_token: str = "",
        response_text: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "message_id": message_id,
            "response_token": response_token,
            "response_text": response_text,
        }
        try:
            request = RespondToAgentRequest(
                session_id=resolved_session_id,
                message_id=message_id,
                response_token=response_token,
                response_text=response_text,
            )
            args = request.model_dump(mode="json")
            context.policy.require_tool(request.session_id, ToolName.RESPOND_TO_AGENT)
            message = context.agent_prompt_store.get_prompt(request.message_id)
            if message is None:
                raise ToolError(f"Unknown message_id: {request.message_id}")
            resolved_config = context.resolve_provider_config_for_session(request.session_id)
            claims = verify_agent_prompt_response_token(
                token=request.response_token,
                secret=_load_agent_prompt_token_secret(resolved_config),
                expected_message_id=request.message_id,
                expected_target_session_id=request.session_id,
            )
            if claims["sender_session_id"] != message.sender_session_id:
                raise PolicyError(
                    "response_token sender_session_id claim does not match original message"
                )

            token_hash = hash_agent_prompt_response_token(request.response_token)
            consumed = context.agent_prompt_store.validate_and_consume_token(
                token_hash,
                expected_message_id=request.message_id,
            )
            if consumed is None:
                raise PolicyError(
                    "response_token is invalid, already consumed, or does not match message_id"
                )
            if consumed.target_session_id != request.session_id:
                raise PolicyError("response token is not valid for this session")

            updated = context.agent_prompt_store.record_response(
                request.message_id,
                response_text=request.response_text,
                responder_session_id=request.session_id,
            )
            if updated is None:
                raise ToolError(f"Unknown message_id: {request.message_id}")

            result = RespondToAgentResponse(
                status="responded",
                message_id=request.message_id,
                delivered_at=updated.updated_at,
            ).model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                {
                    "status": result["status"],
                    "message_id": result["message_id"],
                    "delivered_at": result["delivered_at"],
                    "response_token": "<redacted>",
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.RESPOND_TO_AGENT, args, str(exc))
            raise
        except ValueError as exc:
            message = str(exc)
            audit_failure(
                resolved_session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                message,
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(message) from exc
        except ToolError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RESPOND_TO_AGENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_issues(
        session_id: str | None = None,
        status: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "status": status,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.LIST_ISSUES)
            status_filter = _normalize_issue_status_filters(status)
            issues = AdminService(context.config.runtime_dir).list_local_issues(
                directory_path=session.directory_path,
                direct_only=True,
            )
            if status:
                filtered = [
                    issue
                    for issue in issues
                    if str(issue.state or "").strip().lower() in status_filter
                ]
            else:
                filtered = [
                    issue
                    for issue in issues
                    if str(issue.state or "").strip().lower() not in status_filter
                ]
            result = {
                "issues": [issue.model_dump(mode="json") for issue in filtered],
            }
            audit_success(
                session_id,
                ToolName.LIST_ISSUES,
                args,
                {
                    "directory_path": session.directory_path,
                    "count": len(filtered),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_ISSUES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ISSUES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_issue(
        issue_id: str,
        include_remote_comments: bool = True,
        sync_proxy_cache: bool = True,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "issue_id": issue_id,
            "include_remote_comments": include_remote_comments,
            "sync_proxy_cache": sync_proxy_cache,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.GET_ISSUE)
            result = AdminService(context.config.runtime_dir).get_issue(
                issue_id,
                include_remote_comments=include_remote_comments,
                sync_proxy_cache=sync_proxy_cache,
            )
            audit_success(
                session_id,
                ToolName.GET_ISSUE,
                args,
                {
                    "issue_number": result.get("issue_number"),
                    "source_type": result.get("source_type"),
                    "state": result.get("state"),
                    "directory_path": session.directory_path,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.GET_ISSUE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.GET_ISSUE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def create_ado_backlog_item(
        type: str,
        project: str,
        iteration: str,
        title: str,
        description: str | None = None,
        acceptance_criteria: str | None = None,
        tags: list[str] | None = None,
        assigned_user: str | None = None,
        parent_backlog_item_id: int | str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "type": type,
            "project": project,
            "iteration": iteration,
            "title": title,
            "description": description,
            "acceptance_criteria": acceptance_criteria,
            "tags": tags,
            "assigned_user": assigned_user,
            "parent_backlog_item_id": parent_backlog_item_id,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.CREATE_ADO_BACKLOG_ITEM)
            request_model = AdoBacklogCreateRequest(
                type=type,
                project=project,
                iteration=iteration,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                tags=tags or [],
                assigned_user=assigned_user,
                parent_backlog_item_id=parent_backlog_item_id,
            )
            args = {
                "session_id": session_id,
                **request_model.model_dump(mode="json"),
            }
            issue_service = IssueService(context.provider_config)
            remote_payload = issue_service.create_remote_issue(
                provider="azure_devops",
                directory_path=session.directory_path,
                project=request_model.project,
                issue_type=request_model.type.value,
                title=request_model.title,
                description=request_model.description,
                iteration_path=request_model.iteration,
                acceptance_criteria=request_model.acceptance_criteria,
                tags=request_model.tags,
                assigned_to=request_model.assigned_user,
                parent_backlog_item_id=request_model.parent_backlog_item_id,
            )
            remote_fields = (
                remote_payload.get("fields")
                if isinstance(remote_payload.get("fields"), dict)
                else {}
            )
            assigned_to_value = remote_fields.get("System.AssignedTo")
            if isinstance(assigned_to_value, dict):
                normalized_assigned_to = next(
                    (
                        candidate
                        for key in (
                            "displayName",
                            "uniqueName",
                            "mailAddress",
                            "signInAddress",
                            "name",
                        )
                        if (candidate := str(assigned_to_value.get(key) or "").strip())
                    ),
                    None,
                )
            elif assigned_to_value is None:
                normalized_assigned_to = None
            else:
                normalized_assigned_to = str(assigned_to_value).strip() or None
            response_model = AdoBacklogCreateResponse(
                created_item_id=str(
                    remote_payload.get("remote_issue_id") or remote_payload.get("id") or ""
                ).strip(),
                url=str(remote_payload.get("url") or "").strip(),
                provider=str(remote_payload.get("provider") or "azure_devops").strip()
                or "azure_devops",
                project=str(remote_payload.get("project") or request_model.project).strip(),
                type=request_model.type,
                iteration=request_model.iteration,
                title=str((remote_fields.get("System.Title") or request_model.title)).strip(),
                description=remote_fields.get("System.Description") or request_model.description,
                acceptance_criteria=remote_fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")
                or request_model.acceptance_criteria,
                tags=request_model.tags,
                assigned_user=normalized_assigned_to or request_model.assigned_user,
                parent_backlog_item_id=request_model.parent_backlog_item_id,
            )
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.CREATE_ADO_BACKLOG_ITEM,
                args,
                {
                    "created_item_id": payload.get("created_item_id"),
                    "project": payload.get("project", request_model.project),
                    "type": payload.get("type", request_model.type.value),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.CREATE_ADO_BACKLOG_ITEM, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.CREATE_ADO_BACKLOG_ITEM,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def query_ado_backlog_items(
        project: str | None = None,
        iteration: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "project": project,
            "iteration": iteration,
            "area_path": area_path,
            "states": states,
            "types": types,
            "tags_any": tags_any,
            "text_search": text_search,
            "top": top,
            "skip": skip,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.QUERY_ADO_BACKLOG_ITEMS)
            request_model = AdoBacklogQueryRequest(
                project=project,
                iteration=iteration,
                area_path=area_path,
                states=states or [],
                types=types or [],
                tags_any=tags_any or [],
                text_search=text_search,
                top=top,
                skip=skip,
            )
            args = {"session_id": session_id, **request_model.model_dump(mode="json")}
            issue_service = IssueService(context.provider_config)
            remote_payload = issue_service.query_remote_backlog_items(
                provider="azure_devops",
                directory_path=session.directory_path,
                project=request_model.project,
                iteration=request_model.iteration,
                area_path=request_model.area_path,
                states=request_model.states,
                types=[item_type.value for item_type in request_model.types],
                tags_any=request_model.tags_any,
                text_search=request_model.text_search,
                top=request_model.top,
                skip=request_model.skip,
            )
            response_model = AdoBacklogQueryResponse.model_validate(
                {
                    "items": remote_payload.get("items") or [],
                    "count": remote_payload.get("count", 0),
                    "paging": remote_payload.get("paging")
                    or {
                        "skip": request_model.skip,
                        "top": request_model.top,
                        "returned_count": 0,
                        "total_matches": 0,
                        "has_more": False,
                        "next_skip": None,
                    },
                }
            )
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.QUERY_ADO_BACKLOG_ITEMS,
                args,
                {
                    "project": request_model.project,
                    "count": payload.get("count", 0),
                    "has_more": ((payload.get("paging") or {}).get("has_more", False)),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.QUERY_ADO_BACKLOG_ITEMS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.QUERY_ADO_BACKLOG_ITEMS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_ado_backlog_item(
        id: int | str,
        project: str | None = None,
        include_comments: bool = True,
        include_relations: bool = True,
        include_updates: bool = False,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "id": id,
            "project": project,
            "include_comments": include_comments,
            "include_relations": include_relations,
            "include_updates": include_updates,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.GET_ADO_BACKLOG_ITEM)
            request_model = AdoBacklogGetRequest(
                id=id,
                project=project,
                include_comments=include_comments,
                include_relations=include_relations,
                include_updates=include_updates,
            )
            args = {"session_id": session_id, **request_model.model_dump(mode="json")}
            issue_service = IssueService(context.provider_config)
            remote_payload = issue_service.get_remote_backlog_item_by_id(
                provider="azure_devops",
                directory_path=session.directory_path,
                backlog_item_id=request_model.id,
                project=request_model.project,
                include_comments=request_model.include_comments,
                include_relations=request_model.include_relations,
                include_updates=request_model.include_updates,
            )
            response_model = AdoBacklogGetResponse.model_validate(remote_payload)
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.GET_ADO_BACKLOG_ITEM,
                args,
                {
                    "id": payload.get("id"),
                    "project": payload.get("project"),
                    "include_comments": request_model.include_comments,
                    "include_relations": request_model.include_relations,
                    "include_updates": request_model.include_updates,
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.GET_ADO_BACKLOG_ITEM, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.GET_ADO_BACKLOG_ITEM,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_ado_current_iteration_backlog_items(
        project: str | None = None,
        team: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "project": project,
            "team": team,
            "area_path": area_path,
            "states": states,
            "types": types,
            "tags_any": tags_any,
            "text_search": text_search,
            "top": top,
            "skip": skip,
        }
        try:
            session = context.policy.require_tool(
                session_id, ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS
            )
            request_model = AdoCurrentIterationBacklogRequest(
                project=project,
                team=team,
                area_path=area_path,
                states=states or [],
                types=types or [],
                tags_any=tags_any or [],
                text_search=text_search,
                top=top,
                skip=skip,
            )
            args = {"session_id": session_id, **request_model.model_dump(mode="json")}
            issue_service = IssueService(context.provider_config)
            remote_payload = issue_service.list_remote_current_iteration_backlog_items(
                provider="azure_devops",
                directory_path=session.directory_path,
                project=request_model.project,
                team=request_model.team,
                area_path=request_model.area_path,
                states=request_model.states,
                types=[item_type.value for item_type in request_model.types],
                tags_any=request_model.tags_any,
                text_search=request_model.text_search,
                top=request_model.top,
                skip=request_model.skip,
            )
            response_model = AdoCurrentIterationBacklogResponse.model_validate(
                {
                    "project": remote_payload.get("project"),
                    "team": remote_payload.get("team"),
                    "iteration_path": remote_payload.get("iteration_path") or "",
                    "items": remote_payload.get("items") or [],
                    "count": remote_payload.get("count", 0),
                }
            )
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS,
                args,
                {
                    "project": payload.get("project") or request_model.project,
                    "requested_team": request_model.team,
                    "team": payload.get("team") or request_model.team,
                    "iteration_path": payload.get("iteration_path"),
                    "count": payload.get("count", 0),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(
                session_id, ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def create_local_issue(
        title: str,
        body: str = "",
        repo_source: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "title": title,
            "body": body,
            "repo_source": repo_source,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.CREATE_LOCAL_ISSUE)
            admin_service = AdminService(context.config.runtime_dir)
            metadata = _issue_creation_metadata(session)
            issue_kwargs: dict[str, Any] = {
                "title": title,
                "body": body,
                "repo_source": repo_source,
                "directory_path": session.directory_path,
            }
            if metadata and _method_accepts_keyword_argument(
                admin_service.create_local_issue, "metadata"
            ):
                issue_kwargs["metadata"] = metadata
            issue = admin_service.create_local_issue(**issue_kwargs)
            result = _payload_from_result(issue) or {}
            audit_success(
                session_id,
                ToolName.CREATE_LOCAL_ISSUE,
                args,
                {
                    "issue_id": result.get("issue_id"),
                    "issue_number": result.get("issue_number"),
                    "directory_path": result.get("directory_path", session.directory_path),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.CREATE_LOCAL_ISSUE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.CREATE_LOCAL_ISSUE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def set_issue_plan(
        issue_id: str,
        steps: list[str],
        summary: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "issue_id": issue_id,
            "steps": steps,
            "summary": summary,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.SET_ISSUE_PLAN)
            result = AdminService(context.config.runtime_dir).set_issue_plan(
                issue_id, steps, summary=summary
            )
            audit_success(
                session_id,
                ToolName.SET_ISSUE_PLAN,
                args,
                {
                    "issue_number": result.get("issue_number"),
                    "source_type": result.get("source_type"),
                    "step_count": result.get("step_count"),
                    "has_summary": bool(result.get("summary")),
                    "directory_path": session.directory_path,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.SET_ISSUE_PLAN, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.SET_ISSUE_PLAN,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def approve_issue(
        issue_id: str,
        priority: str | None = None,
        reason: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "issue_id": issue_id,
            "priority": priority,
            "reason": reason,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.APPROVE_ISSUE)
            fields: dict[str, object] | None = None
            if reason is not None or priority is not None:
                fields = {}
                if reason is not None:
                    fields["Reason"] = reason
                if priority is not None:
                    fields["Priority"] = priority
            result = AdminService(context.config.runtime_dir).update_issue(
                issue_id,
                state="Approved",
                fields=fields,
            )
            audit_success(
                session_id,
                ToolName.APPROVE_ISSUE,
                args,
                {
                    "issue_number": result.get("issue_number"),
                    "source_type": result.get("source_type"),
                    "directory_path": session.directory_path,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.APPROVE_ISSUE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.APPROVE_ISSUE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def claim_next_issue(
        session_id: str | None = None, *, ctx: Context | None = None
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": session_id}
        try:
            session = context.policy.require_tool(session_id, ToolName.CLAIM_NEXT_ISSUE)
            result = AdminService(context.config.runtime_dir).claim_next_issue(
                session_id=session_id
            )
            payload = _payload_from_result(result)
            audit_success(
                session_id,
                ToolName.CLAIM_NEXT_ISSUE,
                args,
                {
                    "issue_number": None if payload is None else payload.get("issue_number"),
                    "source_type": None if payload is None else payload.get("source_type"),
                    "directory_path": session.directory_path,
                    "claimed": payload is not None,
                },
            )
            return payload or {}
        except PolicyError as exc:
            audit_failure(session_id, ToolName.CLAIM_NEXT_ISSUE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.CLAIM_NEXT_ISSUE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def complete_issue(
        issue_id: str,
        summary: str,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )

        args = {"session_id": session_id, "issue_id": issue_id, "summary": summary}
        try:
            session = context.policy.require_tool(session_id, ToolName.COMPLETE_ISSUE)
            result = AdminService(context.config.runtime_dir).complete_issue(
                session_id=session_id,
                issue_id=issue_id,
                summary=summary,
            )
            payload = _payload_from_result(result) or {}
            audit_success(
                session_id,
                ToolName.COMPLETE_ISSUE,
                args,
                {
                    "issue_number": payload.get("issue_number"),
                    "source_type": payload.get("source_type"),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMPLETE_ISSUE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMPLETE_ISSUE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def reject_issue(
        issue_id: str,
        reason: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )

        args = {
            "session_id": session_id,
            "issue_id": issue_id,
            "reason": reason,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.REJECT_ISSUE)
            fields: dict[str, object] | None = None
            if reason is not None:
                fields = {"Reason": reason}
            result = AdminService(context.config.runtime_dir).update_issue(
                issue_id,
                state="Closed",
                fields=fields,
            )
            audit_success(
                session_id,
                ToolName.REJECT_ISSUE,
                args,
                {
                    "issue_number": result.get("issue_number"),
                    "source_type": result.get("source_type"),
                    "directory_path": session.directory_path,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.REJECT_ISSUE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.REJECT_ISSUE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def commit_issue(
        issue_id: str,
        reason: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(session_id, ctx)
        args = {
            "session_id": resolved_session_id,
            "issue_id": issue_id,
            "reason": reason,
        }
        try:
            session = context.policy.require_tool(resolved_session_id, ToolName.COMMIT_ISSUE)
            fields: dict[str, object] | None = None
            if reason is not None:
                fields = {"Reason": reason}
            result = AdminService(context.config.runtime_dir).update_issue(
                issue_id,
                state="Committed",
                fields=fields,
            )
            audit_success(
                resolved_session_id,
                ToolName.COMMIT_ISSUE,
                args,
                {
                    "issue_number": result.get("issue_number"),
                    "source_type": result.get("source_type"),
                    "directory_path": session.directory_path,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.COMMIT_ISSUE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.COMMIT_ISSUE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_agent_status(
        vm_name: str,
        vm_user: str = "agent",
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(session_id, ctx)
        args = {
            "session_id": resolved_session_id,
            "vm_name": vm_name,
            "vm_user": vm_user,
        }
        try:
            session = context.policy.require_tool(resolved_session_id, ToolName.GET_AGENT_STATUS)
            summary = _require_vm_visible_in_session_directory(
                context,
                session,
                vm_name=vm_name,
                vm_user=vm_user,
            )
            payload = {
                "vm_name": vm_name,
                "status": _normalize_agent_status(summary.agent_state),
                "agent_state": summary.agent_state,
                "libvirt_state": summary.libvirt_state,
                "ip_address": summary.ip_address,
                "agent_repo_names": summary.agent_repo_names,
                "last_state_update_at": (
                    summary.last_state_update_at.isoformat()
                    if summary.last_state_update_at
                    else None
                ),
            }
            audit_success(
                resolved_session_id,
                ToolName.GET_AGENT_STATUS,
                args,
                {
                    "vm_name": vm_name,
                    "status": payload["status"],
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.GET_AGENT_STATUS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_AGENT_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_agent_logs(
        vm_name: str,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(session_id, ctx)
        args = {
            "session_id": resolved_session_id,
            "vm_name": vm_name,
            "lines": lines,
            "repo_name": repo_name,
            "vm_user": vm_user,
        }
        try:
            context.policy.require_tool(resolved_session_id, ToolName.GET_AGENT_LOGS)
            session = context.policy.get_session(resolved_session_id)
            _require_vm_visible_in_session_directory(
                context,
                session,
                vm_name=vm_name,
                vm_user=vm_user,
            )
            safe_lines = max(1, min(lines, 1000))
            logs = AdminService(context.config.runtime_dir).get_agent_log_snapshot(
                vm_name,
                lines=safe_lines,
                repo_name=repo_name,
                vm_user=vm_user,
            )
            payload = {"vm_name": vm_name, "lines": safe_lines, "repo_name": repo_name, "log": logs}
            audit_success(
                resolved_session_id,
                ToolName.GET_AGENT_LOGS,
                args,
                {"vm_name": vm_name, "lines": safe_lines, "has_output": bool(str(logs).strip())},
            )
            return payload
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.GET_AGENT_LOGS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_AGENT_LOGS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_pull_requests(
        session_id: str,
        provider: str,
        repo: str,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        top: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        request = ListPullRequestsRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            status=status.strip() if isinstance(status, str) else None,
            creator_id=creator_id.strip() if isinstance(creator_id, str) else None,
            reviewer_id=reviewer_id.strip() if isinstance(reviewer_id, str) else None,
            top=top,
            cursor=cursor.strip() if isinstance(cursor, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.LIST_PULL_REQUESTS)
            response = context.pr_query_service_for_session(request.session_id).list_pull_requests(
                provider=request.provider,
                repo=request.repo,
                status=request.status,
                creator_id=request.creator_id,
                reviewer_id=request.reviewer_id,
                top=request.top,
                cursor=request.cursor,
            )
            result = response.model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.LIST_PULL_REQUESTS,
                args,
                {"count": len(response.pull_requests), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.LIST_PULL_REQUESTS, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_PULL_REQUESTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_PULL_REQUESTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_pull_request(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> dict[str, Any]:
        request = GetPullRequestRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            include_reviewers=include_reviewers,
            include_commits=include_commits,
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.GET_PULL_REQUEST)
            response = context.pr_query_service_for_session(request.session_id).get_pull_request(
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
                include_reviewers=request.include_reviewers,
                include_commits=request.include_commits,
            )
            result = response.model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.GET_PULL_REQUEST,
                args,
                {"pr_id": response.pr_id, "status": response.status},
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.GET_PULL_REQUEST, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.GET_PULL_REQUEST,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.GET_PULL_REQUEST,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def has_pending_feedback(session_id: str) -> dict[str, Any]:
        args = HasPendingFeedbackRequest(session_id=session_id).model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.HAS_PENDING_FEEDBACK)
            streams = context.policy.require_pr_feedback_streams(session)
            result = context.pr_feedback_service.has_pending_feedback(
                streams=streams,
                session_id=session_id,
            ).model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.HAS_PENDING_FEEDBACK,
                args,
                {"pending": result["pending"]},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.HAS_PENDING_FEEDBACK, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.HAS_PENDING_FEEDBACK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_unread_feedback(
        session_id: str, cursor: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        args = ListUnreadFeedbackRequest(
            session_id=session_id, cursor=cursor, limit=limit
        ).model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.LIST_UNREAD_FEEDBACK)
            streams = context.policy.require_pr_feedback_streams(session)
            response = context.pr_feedback_service.list_unread_feedback(
                streams=streams,
                session_id=session_id,
                cursor=cursor,
                limit=limit,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_UNREAD_FEEDBACK,
                args,
                {"count": len(response.feedback), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_UNREAD_FEEDBACK, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_UNREAD_FEEDBACK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_all_feedback(session_id: str, limit: int | None = None) -> dict[str, Any]:
        args = {"session_id": session_id, "limit": limit}
        try:
            session = context.policy.require_tool(session_id, ToolName.LIST_ALL_FEEDBACK)
            streams = context.policy.require_pr_feedback_streams(session)
            response = context.pr_feedback_service.list_all_feedback(streams=streams, limit=limit)
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_ALL_FEEDBACK,
                args,
                {"count": len(response.feedback), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_ALL_FEEDBACK, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ALL_FEEDBACK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def schedule_resume(
        session_id: str | None = None,
        resume_at: str = "",
        prompt: str = "",
        request_id: str | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "resume_at": resume_at,
            "prompt": prompt,
            "request_id": request_id,
            "source": source,
            "metadata": metadata or {},
        }
        try:
            request = ScheduleResumeRequest(
                session_id=resolved_session_id,
                resume_at=resume_at,
                cron=None,
                prompt=prompt,
                resume_mode="same_context",
                enabled=True,
                request_id=request_id.strip() if isinstance(request_id, str) else None,
                source=source.strip() if isinstance(source, str) else None,
                metadata=metadata or {},
            )
            args = request.model_dump(mode="json")
            context.policy.require_tool(resolved_session_id, ToolName.SCHEDULE_RESUME)
            scheduled = context.wake_store.schedule_resume_request(
                session_id=request.session_id,
                prompt=request.prompt,
                schedule=WakeScheduleDefinition(resume_at=request.resume_at, cron=request.cron),
                request_id=request.request_id,
                source=request.source,
                resume_mode=request.resume_mode,
                enabled=request.enabled,
                metadata=request.metadata,
            )
            result = ScheduleResumeResponse(
                session_id=scheduled.session_id,
                resume_at=scheduled.schedule.resume_at,
                cron=scheduled.schedule.cron,
                request_id=scheduled.request_id,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.SCHEDULE_RESUME,
                args,
                {
                    "session_id": request.session_id,
                    "resume_at": result["resume_at"],
                    "request_id": request.request_id,
                    "scheduled": True,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.SCHEDULE_RESUME, args, str(exc))
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SCHEDULE_RESUME,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SCHEDULE_RESUME,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def run_script(
        session_id: str | None = None,
        script_name: str = "",
        parameters: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "script_name": script_name,
            "parameters": list(parameters or []),
        }
        try:
            request = RunScriptRequest(
                session_id=resolved_session_id,
                script_name=script_name,
                parameters=parameters or [],
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(resolved_session_id, ToolName.RUN_SCRIPT)
            normalized_script_name = context.policy.require_script_name(
                session, request.script_name
            )
            queued = context.script_run_request_store.enqueue(
                session_id=resolved_session_id,
                directory_path=session.directory_path,
                script_name=normalized_script_name,
                parameters=list(request.parameters),
                source="mcp_tool",
            )
            result = RunScriptResponse(
                request_id=queued.request_id,
                script_name=queued.script_name,
                parameters=list(queued.parameters),
                queued=True,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.RUN_SCRIPT,
                args,
                {
                    "request_id": queued.request_id,
                    "script_name": queued.script_name,
                    "queued": True,
                    "parameter_count": len(queued.parameters),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.RUN_SCRIPT, args, str(exc))
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RUN_SCRIPT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RUN_SCRIPT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def post_pr_feedback_reply(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        thread_id: str | None = None,
        comment_id: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_thread_id = thread_id.strip() if isinstance(thread_id, str) else None
        normalized_comment_id = comment_id.strip() if isinstance(comment_id, str) else None
        request = PostPrFeedbackReplyRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            body_text=body_text.strip(),
            thread_id=normalized_thread_id,
            comment_id=normalized_comment_id,
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        if not request.thread_id and not request.comment_id:
            message = "Either thread_id or comment_id is required for post_pr_feedback_reply"
            audit_failure(session_id, ToolName.POST_PR_FEEDBACK_REPLY, args, message)
            raise PolicyError(message)
        try:
            session = context.policy.require_tool(session_id, ToolName.POST_PR_FEEDBACK_REPLY)
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=args)
            result = (
                context.pr_reply_service_for_session(session_id)
                .post_reply(
                    provider=request.provider,
                    repo=request.repo,
                    pr_id=request.pr_id,
                    thread_id=request.thread_id,
                    comment_id=request.comment_id,
                    body_text=request.body_text,
                    idempotency_key=request.idempotency_key,
                )
                .model_dump(mode="json")
            )
            audit_success(
                session_id,
                ToolName.POST_PR_FEEDBACK_REPLY,
                args,
                {
                    "status": result.get("status"),
                    "provider": result.get("provider"),
                    "reply_id": result.get("reply_id"),
                    "reply_url": result.get("reply_url"),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.POST_PR_FEEDBACK_REPLY, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                session_id,
                ToolName.POST_PR_FEEDBACK_REPLY,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.POST_PR_FEEDBACK_REPLY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def request_pr_review(
        session_id: str,
        provider: Literal["github", "azure_devops"],
        repo: str,
        pr_id: str,
        requested_by: str | None = None,
        request_channel: str | None = None,
        review_type: str | None = None,
        instructions: str | None = None,
        directory_path: str | None = None,
        model: str | None = None,
        labels: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = RequestPrReviewRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            requested_by=requested_by.strip() if isinstance(requested_by, str) else None,
            request_channel=request_channel.strip() if isinstance(request_channel, str) else None,
            review_type=review_type.strip() if isinstance(review_type, str) else None,
            instructions=instructions.strip() if isinstance(instructions, str) else None,
            directory_path=directory_path.strip() if isinstance(directory_path, str) else None,
            model=model.strip() if isinstance(model, str) else None,
            labels=labels or {},
            metadata=metadata or {},
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.REQUEST_PR_REVIEW)
            result = (
                context.review_request_service_for_session(request.session_id)
                .enqueue_request(
                    session_id=request.session_id,
                    provider=request.provider,
                    repo=request.repo,
                    pr_id=request.pr_id,
                    requested_by=request.requested_by,
                    request_channel=request.request_channel,
                    review_type=request.review_type,
                    instructions=request.instructions,
                    directory_path=request.directory_path,
                    model=request.model,
                    labels=request.labels,
                    metadata=request.metadata,
                )
                .model_dump(mode="json")
            )
            audit_success(
                request.session_id,
                ToolName.REQUEST_PR_REVIEW,
                args,
                {
                    "status": result.get("status"),
                    "request_id": result.get("request_id"),
                    "provider": result.get("provider"),
                    "repo": result.get("repo"),
                    "pr_id": result.get("pr_id"),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.REQUEST_PR_REVIEW, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.REQUEST_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.REQUEST_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def post_pr_comment(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = PostPrCommentRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            body_text=body_text.strip(),
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(request.session_id, ToolName.POST_PR_COMMENT)
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=args)
            result = context.pr_publish_service_for_session(request.session_id).post_comment(
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
                body_text=request.body_text,
                idempotency_key=request.idempotency_key,
            )
            audit_success(
                request.session_id,
                ToolName.POST_PR_COMMENT,
                args,
                {
                    "status": result.get("status"),
                    "provider": result.get("provider"),
                    "comment_id": result.get("comment_id"),
                    "comment_url": result.get("comment_url"),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.POST_PR_COMMENT, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def post_pr_inline_comment(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        path: str,
        line: int,
        body_text: str,
        start_line: int | None = None,
        side: str | None = "RIGHT",
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = PostPrInlineCommentRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            path=path.strip(),
            line=line,
            body_text=body_text.strip(),
            start_line=start_line,
            side=side.strip().upper() if isinstance(side, str) else None,
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                request.session_id, ToolName.POST_PR_INLINE_COMMENT
            )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=args)
            result = context.pr_publish_service_for_session(request.session_id).post_inline_comment(
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
                path=request.path,
                line=request.line,
                body_text=request.body_text,
                start_line=request.start_line,
                side=request.side,
                idempotency_key=request.idempotency_key,
            )
            audit_success(
                request.session_id,
                ToolName.POST_PR_INLINE_COMMENT,
                args,
                {
                    "status": result.get("status"),
                    "provider": result.get("provider"),
                    "review_id": result.get("review_id"),
                    "review_url": result.get("review_url"),
                    "comment_count": len(result.get("comment_ids") or []),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.POST_PR_INLINE_COMMENT, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_INLINE_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_INLINE_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def submit_pr_review(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        event: str | None = None,
        comments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = SubmitPrReviewRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            body_text=body_text.strip(),
            event=event.strip() if isinstance(event, str) else None,
            comments=comments or [],
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(request.session_id, ToolName.SUBMIT_PR_REVIEW)
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=args)
            result = context.pr_publish_service_for_session(request.session_id).submit_review(
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
                body_text=request.body_text,
                event=request.event,
                comments=[item.model_dump(mode="json") for item in request.comments],
                idempotency_key=request.idempotency_key,
            )
            audit_success(
                request.session_id,
                ToolName.SUBMIT_PR_REVIEW,
                args,
                {
                    "status": result.get("status"),
                    "provider": result.get("provider"),
                    "review_id": result.get("review_id"),
                    "review_url": result.get("review_url"),
                    "comment_count": len(result.get("comment_ids") or []),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.SUBMIT_PR_REVIEW, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.SUBMIT_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.SUBMIT_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def discord_get_commands(session_id: str, since_cursor: str | None = None) -> dict[str, Any]:
        args = {"session_id": session_id, "since_cursor": since_cursor}
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_GET_COMMANDS)
            raw_commands = context.discord_service_for_session(session_id).get_commands(
                since_cursor
            )
            commands, denied_count, next_cursor = _filter_discord_commands_for_session(
                context,
                session_id,
                session,
                raw_commands,
                args,
                audit_failure,
            )
            result = {"commands": commands, "next_cursor": next_cursor}
            audit_success(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                args,
                {"count": len(commands), "denied_count": denied_count, "next_cursor": next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_GET_COMMANDS, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def discord_post_status(session_id: str, channel_id: str, message: str) -> dict[str, Any]:
        args = {"session_id": session_id, "channel_id": channel_id, "message": message}
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_POST_STATUS)
            context.policy.require_discord_channel(session, channel_id)
            result = context.discord_service_for_session(session_id).post_status(
                message, channel_id, session_id
            )
            audit_success(session_id, ToolName.DISCORD_POST_STATUS, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_POST_STATUS, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_get_messages(
        session_id: str,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "since_cursor": since_cursor,
            "before_cursor": before_cursor,
            "limit": limit,
            "channel_target": channel_target,
        }
        try:
            if limit is not None and limit < 1:
                raise ToolError("limit must be >= 1 when provided")
            if since_cursor is not None and before_cursor is not None:
                raise ToolError("since_cursor and before_cursor are mutually exclusive")
            session = context.policy.require_tool(session_id, ToolName.TEAMS_GET_MESSAGES)
            normalized_target = None
            if channel_target is not None:
                normalized_target = context.policy.require_teams_channel_target(
                    session,
                    str(channel_target or "").strip(),
                )
            effective_limit = limit
            if since_cursor is None and before_cursor is None and effective_limit is None:
                effective_limit = context.config.teams_poll_limit
            raw_messages = context.teams_service_for_session(session_id).get_messages(
                since_cursor=since_cursor,
                before_cursor=before_cursor,
                limit=None,
                channel_target=normalized_target,
            )
            messages, denied_count, next_cursor = _filter_teams_messages_for_session(
                context,
                session_id,
                session,
                raw_messages,
                args,
                audit_failure,
            )
            if effective_limit is not None:
                messages = messages[-effective_limit:]
                fallback_cursor = before_cursor if before_cursor is not None else since_cursor
                next_cursor = messages[-1]["cursor"] if messages else fallback_cursor
            result = {"messages": messages, "next_cursor": next_cursor}
            audit_success(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                args,
                {"count": len(messages), "denied_count": denied_count, "next_cursor": next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_GET_MESSAGES, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise

    @server.tool()
    def has_pending_discord_messages(session_id: str) -> dict[str, Any]:
        args = HasPendingDiscordMessagesRequest(session_id=session_id).model_dump(mode="json")
        try:
            context.policy.require_tool(session_id, ToolName.HAS_PENDING_DISCORD_MESSAGES)
            streams = [
                {"channel_id": channel_id}
                for channel_id in context.policy_config.discord_inbox_channel_ids_for_session_id(
                    session_id
                )
            ]
            result = (
                context.discord_inbox_service_for_session(session_id)
                .has_pending_discord_messages(
                    streams=streams,
                    session_id=session_id,
                )
                .model_dump(mode="json")
            )
            audit_success(
                session_id,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                args,
                {"pending": result["pending"]},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.HAS_PENDING_DISCORD_MESSAGES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_unread_discord_messages(
        session_id: str, cursor: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        args = ListUnreadDiscordMessagesRequest(
            session_id=session_id, cursor=cursor, limit=limit
        ).model_dump(mode="json")
        try:
            context.policy.require_tool(session_id, ToolName.LIST_UNREAD_DISCORD_MESSAGES)
            streams = [
                {"channel_id": channel_id}
                for channel_id in context.policy_config.discord_inbox_channel_ids_for_session_id(
                    session_id
                )
            ]
            response = context.discord_inbox_service_for_session(
                session_id
            ).list_unread_discord_messages(
                streams=streams,
                session_id=session_id,
                cursor=cursor,
                limit=limit,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                args,
                {"count": len(response.messages), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_UNREAD_DISCORD_MESSAGES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_all_discord_messages(session_id: str, limit: int | None = None) -> dict[str, Any]:
        args = {"session_id": session_id, "limit": limit}
        try:
            context.policy.require_tool(session_id, ToolName.LIST_ALL_DISCORD_MESSAGES)
            streams = [
                {"channel_id": channel_id}
                for channel_id in context.policy_config.discord_inbox_channel_ids_for_session_id(
                    session_id
                )
            ]
            response = context.discord_inbox_service_for_session(
                session_id
            ).list_all_discord_messages(
                streams=streams,
                limit=limit,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
                args,
                {"count": len(response.messages), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_ALL_DISCORD_MESSAGES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def add_message_reaction(
        session_id: str,
        provider: str,
        message_id: str,
        emoji: str,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "provider": provider,
            "message_id": message_id,
            "emoji": emoji,
            "channel_id": channel_id,
            "channel_target": channel_target,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.ADD_MESSAGE_REACTION)
            normalized_provider = str(provider or "").strip().lower()
            normalized_message_id = str(message_id or "").strip()
            normalized_emoji = str(emoji or "").strip()
            if not normalized_message_id:
                raise ToolError("message_id is required")
            if not normalized_emoji:
                raise ToolError("emoji is required")
            if normalized_provider == "discord":
                normalized_channel_id = str(channel_id or "").strip()
                if not normalized_channel_id:
                    raise ToolError("channel_id is required for Discord reactions")
                context.policy.require_discord_channel(session, normalized_channel_id)
                result = context.discord_service_for_session(session_id).add_reaction(
                    channel_id=normalized_channel_id,
                    message_id=normalized_message_id,
                    emoji=normalized_emoji,
                    session_id=session_id,
                )
            elif normalized_provider == "teams":
                normalized_target = context.policy.require_teams_channel_target(
                    session,
                    str(channel_target or "").strip(),
                )
                result = context.teams_service_for_session(session_id).add_reaction(
                    channel_target=normalized_target,
                    message_id=normalized_message_id,
                    reaction=normalized_emoji,
                    session_id=session_id,
                )
            else:
                raise ToolError("provider must be either 'discord' or 'teams'")
            audit_success(session_id, ToolName.ADD_MESSAGE_REACTION, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.ADD_MESSAGE_REACTION, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.ADD_MESSAGE_REACTION,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.ADD_MESSAGE_REACTION,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def make_phone_call(
        contact: str,
        prompt: str,
        session_id: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Start a host-side Retell phone call to a configured contact alias."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        safe_metadata = sanitize_retell_metadata(metadata or {})
        args = {
            "session_id": resolved_session_id,
            "contact": contact,
            "prompt": prompt,
            "request_id": request_id,
            "metadata": safe_metadata,
        }
        try:
            request = MakePhoneCallRequest(
                session_id=resolved_session_id,
                contact=contact,
                prompt=prompt,
                request_id=request_id.strip() if isinstance(request_id, str) else None,
                metadata=safe_metadata,
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
            )
            record = context.retell_phone_call_service_for_session(
                resolved_session_id
            ).make_phone_call(
                session=session,
                contact=request.contact,
                prompt=request.prompt,
                request_id=request.request_id,
                metadata=request.metadata,
            )
            result = MakePhoneCallResponse(
                request_id=record.request_id,
                call_id=record.call_id,
                contact=record.contact_alias,
                status=record.status or "registered",
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                {
                    "request_id": result["request_id"],
                    "call_id": result.get("call_id"),
                    "contact": result["contact"],
                    "status": result["status"],
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.MAKE_PHONE_CALL, args, str(exc))
            raise
        except (ValueError, ProviderRequestError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_phone_call_result(
        session_id: str | None = None,
        request_id: str | None = None,
        call_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read a Retell phone call result owned by this session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "request_id": request_id,
            "call_id": call_id,
        }
        try:
            request = GetPhoneCallResultRequest(
                session_id=resolved_session_id,
                request_id=request_id,
                call_id=call_id,
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
            )
            result = context.retell_phone_call_service_for_session(
                resolved_session_id
            ).get_phone_call_result(
                session=session,
                request_id=request.request_id,
                call_id=request.call_id,
            )
            audit_success(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                {
                    "request_id": result.get("request_id"),
                    "call_id": result.get("call_id"),
                    "contact": result.get("contact"),
                    "status": result.get("status"),
                    "has_transcript": bool(result.get("transcript")),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.GET_PHONE_CALL_RESULT, args, str(exc))
            raise
        except (ValueError, ProviderRequestError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_post_status(
        session_id: str,
        channel_target: str,
        message: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "channel_target": channel_target,
            "message": message,
            "thread_id": thread_id,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.TEAMS_POST_STATUS)
            normalized_target = context.policy.require_teams_channel_target(session, channel_target)
            result = context.teams_service_for_session(session_id).post_status(
                message,
                normalized_target,
                session_id,
                thread_id=thread_id,
            )
            audit_success(session_id, ToolName.TEAMS_POST_STATUS, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_POST_STATUS, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_meeting_get_events(
        session_id: str,
        since_sequence: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "since_sequence": since_sequence,
            "limit": limit,
        }
        try:
            context.policy.require_tool(session_id, ToolName.TEAMS_MEETING_GET_EVENTS)
            if since_sequence is not None and since_sequence < 0:
                raise ToolError("since_sequence must be >= 0 when provided")
            if limit < 1 or limit > 200:
                raise ToolError("limit must be between 1 and 200")
            events = context.teams_meeting_store.list_events_for_session(
                session_id,
                since_sequence=since_sequence,
                limit=limit,
            )
            next_sequence = events[-1].sequence if events else since_sequence
            result = {
                "events": [event.model_dump(mode="json") for event in events],
                "next_sequence": next_sequence,
            }
            audit_success(
                session_id,
                ToolName.TEAMS_MEETING_GET_EVENTS,
                args,
                {"count": len(events), "next_sequence": next_sequence},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_MEETING_GET_EVENTS, args, str(exc))
            raise
        except (ToolError, ValueError) as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_GET_EVENTS,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_GET_EVENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_meeting_speak(
        session_id: str,
        text: str,
        voice: str | None = None,
        interrupt: bool = False,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "text": text,
            "voice": voice,
            "interrupt": interrupt,
        }
        meeting_id_for_error: str | None = None
        try:
            context.policy.require_tool(session_id, ToolName.TEAMS_MEETING_SPEAK)
            normalized_text = str(text or "").strip()
            if not normalized_text:
                raise ToolError("text is required")
            binding = context.teams_meeting_store.get_binding_for_session(session_id)
            if binding is None:
                raise ToolError(f"Unknown Teams meeting session: {session_id}")
            meeting_id_for_error = binding.meeting_id
            if binding.status != "active":
                raise ToolError(f"Teams meeting {binding.meeting_id} is not active")
            resolved_voice = (
                str(voice or "").strip()
                or binding.default_voice
                or context.config.teams_meeting_default_voice
            )
            bridge_result = context.teams_meeting_bridge_client.speak(
                meeting_id=binding.meeting_id,
                text=normalized_text,
                voice=resolved_voice,
                interrupt=bool(interrupt),
            )
            operation_id = str(
                bridge_result.get("operation_id")
                or bridge_result.get("speech_operation_id")
                or ""
            ).strip() or None
            speech_status = str(bridge_result.get("status") or "queued").strip() or "queued"
            event = context.teams_meeting_store.append_local_event(
                meeting_id=binding.meeting_id,
                event_type="speech_status",
                text=normalized_text,
                speech_operation_id=operation_id,
                speech_status=speech_status,
                metadata={"bridge": bridge_result, "voice": resolved_voice},
            )
            result = {
                "status": speech_status,
                "meeting_id": binding.meeting_id,
                "speech_operation_id": operation_id,
                "event": event.model_dump(mode="json"),
                "bridge": bridge_result,
            }
            audit_success(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                {
                    "status": speech_status,
                    "meeting_id": binding.meeting_id,
                    "speech_operation_id": operation_id,
                    "event_sequence": event.sequence,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_MEETING_SPEAK, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            if meeting_id_for_error is not None:
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_speak"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except (ToolError, RuntimeError, ValueError) as exc:
            if meeting_id_for_error is not None and not isinstance(exc, ToolError):
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_speak"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_meeting_stop(session_id: str) -> dict[str, Any]:
        args = {"session_id": session_id}
        meeting_id_for_error: str | None = None
        try:
            context.policy.require_tool(session_id, ToolName.TEAMS_MEETING_STOP)
            binding = context.teams_meeting_store.get_binding_for_session(session_id)
            if binding is None:
                raise ToolError(f"Unknown Teams meeting session: {session_id}")
            meeting_id_for_error = binding.meeting_id
            bridge_result = context.teams_meeting_bridge_client.leave(
                meeting_id=binding.meeting_id
            )
            context.teams_meeting_store.close_binding(binding.meeting_id, status="stopped")
            event = context.teams_meeting_store.append_local_event(
                meeting_id=binding.meeting_id,
                event_type="meeting_status",
                speech_status="stopped",
                metadata={"bridge": bridge_result},
            )
            manifest = context.session_repository.get_session(session_id)
            if manifest is not None:
                metadata = dict(manifest.metadata or {})
                teams_metadata = dict(metadata.get("teams_meeting") or {})
                teams_metadata["status"] = "stopped"
                teams_metadata["stopped_at"] = (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                )
                metadata["teams_meeting"] = teams_metadata
                context.session_repository.upsert_session(
                    manifest.model_copy(update={"metadata": metadata})
                )
            result = {
                "status": "stopped",
                "meeting_id": binding.meeting_id,
                "event": event.model_dump(mode="json"),
                "bridge": bridge_result,
            }
            audit_success(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                {"status": "stopped", "meeting_id": binding.meeting_id},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_MEETING_STOP, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            if meeting_id_for_error is not None:
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_stop"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except (ToolError, RuntimeError, ValueError) as exc:
            if meeting_id_for_error is not None and not isinstance(exc, ToolError):
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_stop"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_list_unread(
        session_id: str, mailbox_id: str, folder: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "folder": folder,
            "limit": limit,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_LIST_UNREAD)
            context.policy.require_email_mailbox(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, folder)
            email_service = context.email_service_for_session(session_id)
            messages = [
                message.model_dump(mode="json")
                for message in email_service.list_unread(
                    mailbox_id=mailbox_id, folder=folder, limit=limit
                )
            ]
            audit_success(session_id, ToolName.EMAIL_LIST_UNREAD, args, {"count": len(messages)})
            return messages
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_LIST_UNREAD, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_UNREAD,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_UNREAD,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_get_message(session_id: str, mailbox_id: str, message_id: str) -> dict[str, Any]:
        args = {"session_id": session_id, "mailbox_id": mailbox_id, "message_id": message_id}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_GET_MESSAGE)
            context.policy.require_email_mailbox(session, mailbox_id)
            message = context.email_service_for_session(session_id).get_message(
                mailbox_id=mailbox_id,
                message_id=message_id,
            )
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, message.folder)
            result = message.model_dump(mode="json")
            audit_success(session_id, ToolName.EMAIL_GET_MESSAGE, args, {"message_id": message_id})
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_GET_MESSAGE, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_GET_MESSAGE,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_GET_MESSAGE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_create_draft(
        session_id: str,
        body: str,
        mailbox_id: str,
        thread_or_message_id: str | None = None,
        subject: str | None = None,
        recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "thread_or_message_id": thread_or_message_id,
            "subject": subject,
            "recipients": recipients,
            "body": body,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_CREATE_DRAFT)
            context.policy.require_email_mailbox(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, "drafts")
            normalized_recipients = recipients or []
            context.policy.require_draft_recipients(session, normalized_recipients)
            draft = context.email_service_for_session(session_id).create_draft(
                mailbox_id, thread_or_message_id, subject, body, normalized_recipients
            )
            result = draft.model_dump(mode="json")
            audit_success(
                session_id, ToolName.EMAIL_CREATE_DRAFT, args, {"draft_id": draft.draft_id}
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_CREATE_DRAFT, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise


    @server.tool()
    def list_inline_media(
        session_id: str,
        source_kind: str,
        provider: str,
        resource_id: str,
        comment_id: str | None = None,
        project: str | None = None,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = ListInlineMediaRequest(
            session_id=session_id,
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            comment_id=comment_id,
            project=project,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            media = [ref for ref, _raw in _resolve_inline_media(request)]
            response = ListInlineMediaResponse(
                source_kind=_normalize_inline_media_source_kind(source_kind),
                provider=provider.strip().lower(),
                resource_id=resource_id,
                media=media,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_INLINE_MEDIA,
                args,
                {"source_kind": response.source_kind, "provider": response.provider, "resource_id": resource_id, "count": len(media)},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_INLINE_MEDIA, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(session_id, ToolName.LIST_INLINE_MEDIA, args, str(exc), decision="error", error_class="adapter_http_error")
            raise
        except Exception as exc:
            audit_failure(session_id, ToolName.LIST_INLINE_MEDIA, args, str(exc), decision="error", error_class="unexpected_error")
            raise

    @server.tool()
    def download_inline_media(
        session_id: str,
        source_kind: str,
        provider: str,
        resource_id: str,
        media_id: str,
        comment_id: str | None = None,
        project: str | None = None,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = DownloadInlineMediaRequest(
            session_id=session_id,
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            media_id=media_id,
            comment_id=comment_id,
            project=project,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            list_request = ListInlineMediaRequest(
                session_id=session_id,
                source_kind=source_kind,
                provider=provider,
                resource_id=resource_id,
                comment_id=comment_id,
                project=project,
                mailbox_id=mailbox_id,
                channel_id=channel_id,
                channel_target=channel_target,
            )
            matches = _resolve_inline_media(list_request, tool_name=ToolName.DOWNLOAD_INLINE_MEDIA)
            selected = next((item for item in matches if item[0].media_id == media_id), None)
            if selected is None:
                raise ToolError(f"Unknown inline media_id for resource: {media_id}")
            ref, raw = selected
            if ref.source_kind == "communication" and ref.provider == "teams":
                content, filename, mime_type = _download_inline_media_bytes(request, ref, raw)
            elif ref.source_kind == "communication":
                attachment_request = CommunicationDownloadAttachmentRequest(
                    session_id=session_id,
                    provider=provider,
                    message_id=resource_id,
                    attachment_id=media_id,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                )
                attachment_ref = CommunicationAttachmentRef(
                    provider=ref.provider,
                    message_id=ref.resource_id,
                    attachment_id=ref.media_id,
                    filename=ref.filename,
                    mime_type=ref.mime_type,
                    byte_count=ref.byte_count,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                    metadata=ref.metadata,
                )
                content, filename, mime_type = _provider_download_attachment(
                    attachment_request, attachment_ref
                )
            else:
                content, filename, mime_type = _download_inline_media_bytes(request, ref, raw)
            normalized_mime = str(mime_type or "").strip().lower()
            if normalized_mime not in _INLINE_IMAGE_MIME_TYPES:
                raise PolicyError("Inline media downloads are limited to image/png, image/jpeg, and image/gif")
            session = context.policy.get_session(session_id)
            context.policy.require_communication_file_content(
                session,
                filename=filename,
                mime_type=mime_type,
                byte_count=len(content),
            )
            artifact = _stage_inline_media_artifact(
                request,
                ref,
                content,
                filename=filename,
                mime_type=mime_type,
            )
            response = DownloadInlineMediaResponse(artifact=artifact)
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                {**args, "filename": filename, "mime_type": mime_type, "byte_count": len(content)},
                {
                    "source_kind": ref.source_kind,
                    "provider": ref.provider,
                    "resource_id": ref.resource_id,
                    "media_id": ref.media_id,
                    "artifact_id": artifact.artifact_id,
                    "byte_count": artifact.byte_count,
                    "mime_type": artifact.mime_type,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DOWNLOAD_INLINE_MEDIA, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(session_id, ToolName.DOWNLOAD_INLINE_MEDIA, args, str(exc), decision="error", error_class="adapter_http_error")
            raise
        except Exception as exc:
            audit_failure(session_id, ToolName.DOWNLOAD_INLINE_MEDIA, args, str(exc), decision="error", error_class="unexpected_error")
            raise


    @server.tool()
    def communication_list_attachments(
        session_id: str,
        provider: str,
        message_id: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationListAttachmentsRequest(
            session_id=session_id,
            provider=provider,
            message_id=message_id,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            attachments = _resolve_communication_attachments(request)
            response = CommunicationListAttachmentsResponse(
                provider=request.provider.strip().lower(),
                message_id=request.message_id,
                attachments=attachments,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider=response.provider,
                        mailbox_id=mailbox_id,
                        channel_id=channel_id,
                        channel_target=channel_target,
                    ),
                    "message_id": response.message_id,
                    "count": len(response.attachments),
                    "attachments": [
                        {
                            "attachment_id": item.attachment_id,
                            "filename": item.filename,
                            "mime_type": item.mime_type,
                            "byte_count": item.byte_count,
                            "size": item.byte_count,
                        }
                        for item in response.attachments
                    ],
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_LIST_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def communication_download_attachment(
        session_id: str,
        provider: str,
        message_id: str,
        attachment_id: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationDownloadAttachmentRequest(
            session_id=session_id,
            provider=provider,
            message_id=message_id,
            attachment_id=attachment_id,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            list_request = CommunicationListAttachmentsRequest(
                session_id=session_id,
                provider=provider,
                message_id=message_id,
                mailbox_id=mailbox_id,
                channel_id=channel_id,
                channel_target=channel_target,
            )
            attachments = _resolve_communication_attachments(
                list_request,
                tool_name=ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
            )
            ref = next((item for item in attachments if item.attachment_id == attachment_id), None)
            if ref is None:
                raise ToolError(f"Unknown attachment_id for message: {attachment_id}")
            content, filename, mime_type = _provider_download_attachment(request, ref)
            args.update(
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "byte_count": len(content),
                    "size": len(content),
                }
            )
            context.policy.require_communication_file_content(
                context.policy.get_session(session_id),
                filename=filename,
                mime_type=mime_type,
                byte_count=len(content),
            )
            artifact = _stage_communication_artifact(
                request,
                ref,
                content,
                filename=filename,
                mime_type=mime_type,
            )
            response = CommunicationDownloadAttachmentResponse(artifact=artifact)
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                {
                    **_communication_target_audit_fields(
                        provider=artifact.provider,
                        mailbox_id=mailbox_id,
                        channel_id=channel_id,
                        channel_target=channel_target,
                    ),
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "artifact_id": artifact.artifact_id,
                    "byte_count": artifact.byte_count,
                    "size": artifact.byte_count,
                    "mime_type": artifact.mime_type,
                    "filename": artifact.filename,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def communication_copy_artifact_to_workspace(
        session_id: str,
        artifact_id: str,
        destination_filename: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        request = CommunicationCopyArtifactToWorkspaceRequest(
            session_id=session_id,
            artifact_id=artifact_id,
            destination_filename=destination_filename,
            overwrite=overwrite,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                session_id, ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE
            )
            context.policy.require_communication_file_receive_enabled(session)
            staged = _communication_artifact_store().get(_safe_artifact_id(artifact_id))
            artifact = _artifact_to_model(staged.metadata)
            if artifact.session_id != session_id:
                raise ToolError("artifact_id is not owned by this session")
            requested_name = (
                destination_filename or artifact.normalized_safe_filename or artifact.filename
            )
            safe_name = context.policy.require_safe_communication_filename(session, requested_name)
            # Provider filenames are never used as paths; the only destination is this
            # controlled workspace drop directory plus a policy-validated basename.
            safe_name = normalize_safe_filename(safe_name)
            destination_path = f"/home/agent/communication-artifacts/{safe_name}"

            effect = AgentCopyFilesEffectDefinition(
                id="communication-copy-artifact-to-workspace",
                display_name="Copy communication artifact to workspace",
                type="effect:agent:copy-files",
                session_id=session_id,
                files=[
                    VmCopyFilesItemDefinition(
                        source_path=str(staged.blob_path),
                        destination_path=destination_path,
                        overwrite=overwrite,
                    )
                ],
            )
            effect_context = EffectRuntimeContext(
                provider_config=context.resolve_provider_config_for_session(session_id).model_dump(
                    mode="json"
                ),
                admin_service=_admin_service(),
                metadata={"directory_path": session.directory_path or "root"},
            )
            provider = AgentCopyFilesEffectProvider()
            provider.initialize(effect_definition=effect, runtime_context=effect_context)
            copy_result = provider.execute()
            response = CommunicationCopyArtifactToWorkspaceResponse(
                artifact=artifact,
                destination_path=destination_path,
                copied_count=int(copy_result.get("copied_count") or 0),
                copied_paths=list(copy_result.get("copied_paths") or []),
                result=copy_result,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
                args,
                {
                    "decision": "allowed",
                    "artifact_id": artifact.artifact_id,
                    "destination_path": destination_path,
                    "copied_count": response.copied_count,
                    "copied_paths": response.copied_paths,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                session_id, ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def communication_stage_workspace_file(
        session_id: str,
        source_path: str,
        filename: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Stage a regular file under /home/agent for outbound file-send tools."""
        request = CommunicationStageWorkspaceFileRequest(
            session_id=session_id,
            source_path=source_path,
            filename=filename,
            mime_type=mime_type,
        )
        args = request.model_dump(mode="json", exclude_none=True)
        try:
            session = context.policy.require_tool(
                session_id, ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE
            )
            context.policy.require_communication_file_send_enabled(session)
            normalized_source_path = _normalize_workspace_source_path(source_path)
            if filename is not None:
                context.policy.require_safe_communication_filename(session, filename)
            payload = _read_agent_workspace_file(
                session,
                source_path=normalized_source_path,
            )
            content_base64 = str(payload.get("content_base64") or "")
            if not content_base64:
                raise ToolError("Workspace file reader did not return file content")
            try:
                content = base64.b64decode(content_base64, validate=True)
            except Exception as exc:
                raise ToolError("Workspace file reader returned invalid base64 content") from exc
            resolved_source_path = str(payload.get("source_path") or normalized_source_path)
            resolved_filename = str(filename or payload.get("filename") or "attachment")
            resolved_mime_type = str(mime_type or payload.get("mime_type") or "").strip() or None
            context.policy.require_communication_file_content(
                session,
                filename=resolved_filename,
                mime_type=resolved_mime_type,
                byte_count=len(content),
            )
            record = _communication_artifact_store().stage(
                provider="workspace",
                session_id=session_id,
                content=content,
                original_filename=resolved_filename,
                mime_type=resolved_mime_type,
                metadata={
                    "source": "agent_workspace",
                    "source_path": resolved_source_path,
                    "requested_source_path": normalized_source_path,
                },
            )
            artifact = _artifact_to_model(record)
            response = CommunicationStageWorkspaceFileResponse(
                artifact=artifact,
                source_path=resolved_source_path,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE,
                args,
                {
                    "decision": "allowed",
                    "artifact_id": artifact.artifact_id,
                    "source_path": resolved_source_path,
                    "byte_count": artifact.byte_count,
                    "size": artifact.byte_count,
                    "mime_type": artifact.mime_type,
                    "filename": artifact.filename,
                    "sha256": artifact.sha256,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def discord_post_file(
        session_id: str,
        channel_id: str,
        message: str,
        artifact_ids: list[str],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="discord",
            channel_id=channel_id,
            message=message,
            artifact_ids=artifact_ids,
            request_id=request_id,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_POST_FILE)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="discord", channel_id=channel_id
            )
            approval_payload = request.model_dump(
                mode="json", exclude={"request_id"}, exclude_none=True
            )
            approval = context.policy.require_communication_file_send_approval(
                session, request_id=request_id, payload=approval_payload
            )
            service = context.discord_service_for_session(session_id)
            if not hasattr(service, "post_file"):
                raise ToolError("Configured Discord adapter does not support file posts")
            result = service.post_file(message, channel_id, session_id, artifacts)
            response = CommunicationSendMessageWithAttachmentsResponse(
                provider="discord",
                status=str(result.get("status", "posted")),
                message_id=result.get("message_id"),
                artifact_ids=artifact_ids,
                result=result,
            )
            audit_success(
                session_id,
                ToolName.DISCORD_POST_FILE,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="discord",
                        channel_id=channel_id,
                        approval_request_id=request_id,
                        approval_decision=None if approval is None else approval.status.value,
                    ),
                    "message_id": response.message_id,
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                },
            )
            return response.model_dump(mode="json")
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_POST_FILE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_POST_FILE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_post_file(
        session_id: str,
        channel_target: str,
        message: str,
        artifact_ids: list[str],
        thread_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="teams",
            channel_target=channel_target,
            message=message,
            artifact_ids=artifact_ids,
            thread_id=thread_id,
            request_id=request_id,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.TEAMS_POST_FILE)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="teams", channel_target=channel_target
            )
            target = context.policy.require_teams_channel_target(session, channel_target)
            approval_payload = request.model_dump(
                mode="json", exclude={"request_id"}, exclude_none=True
            )
            approval = context.policy.require_communication_file_send_approval(
                session, request_id=request_id, payload=approval_payload
            )
            service = context.teams_service_for_session(session_id)
            if not hasattr(service, "post_file"):
                raise ToolError("Configured Teams adapter does not support file posts")
            result = service.post_file(message, target, session_id, artifacts, thread_id=thread_id)
            response = CommunicationSendMessageWithAttachmentsResponse(
                provider="teams",
                status=str(result.get("status", "posted")),
                message_id=result.get("message_id"),
                artifact_ids=artifact_ids,
                result=result,
            )
            audit_success(
                session_id,
                ToolName.TEAMS_POST_FILE,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="teams",
                        channel_target=target,
                        approval_request_id=request_id,
                        approval_decision=None if approval is None else approval.status.value,
                    ),
                    "message_id": response.message_id,
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                },
            )
            return response.model_dump(mode="json")
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_POST_FILE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_POST_FILE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_create_draft_with_attachments(
        session_id: str,
        body: str,
        mailbox_id: str,
        artifact_ids: list[str],
        thread_or_message_id: str | None = None,
        subject: str | None = None,
        recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="email",
            mailbox_id=mailbox_id,
            message=body,
            artifact_ids=artifact_ids,
            thread_id=thread_or_message_id,
            subject=subject,
            recipients=recipients or [],
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                session_id, ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS
            )
            context.policy.require_email_mailbox(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, "drafts")
            normalized_recipients = recipients or []
            context.policy.require_draft_recipients(session, normalized_recipients)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=mailbox_id
            )
            service = context.email_service_for_session(session_id)
            if hasattr(service, "create_draft_with_attachments"):
                draft = service.create_draft_with_attachments(
                    mailbox_id,
                    thread_or_message_id,
                    subject,
                    body,
                    normalized_recipients,
                    artifacts,
                )
            else:
                draft_attachments = [
                    EmailDraftAttachment(
                        filename=str(artifact["filename"]),
                        content=Path(str(artifact["path"])).read_bytes(),
                        mime_type=artifact.get("mime_type"),
                    )
                    for artifact in artifacts
                ]
                draft = service.create_draft(
                    mailbox_id,
                    thread_or_message_id,
                    subject,
                    body,
                    normalized_recipients,
                    attachments=draft_attachments,
                )
            result = (
                draft.model_dump(mode="json")
                if hasattr(draft, "model_dump")
                else dict(draft)
            )
            result["artifact_ids"] = artifact_ids
            audit_success(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=mailbox_id,
                    ),
                    "draft_id": result.get("draft_id"),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                session_id, ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS, args, str(exc)
            )
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_send_approved_with_attachments(
        session_id: str,
        request_id: str,
        payload: dict[str, Any],
        artifact_ids: list[str],
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "request_id": request_id,
            "payload": payload,
            "artifact_ids": artifact_ids,
        }
        try:
            session = context.policy.require_tool(
                session_id, ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS
            )
            context.policy.require_email_send_enabled(session)
            context.policy.require_email_mailbox(session, str(payload.get("mailbox_id", "")))
            recipients = draft_recipients_from_payload(payload)
            context.policy.require_draft_recipients(session, recipients)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=str(payload.get("mailbox_id", ""))
            )
            file_approval_payload = {**payload, "artifact_ids": artifact_ids}
            file_approval = context.policy.require_communication_file_send_approval(
                session, request_id=request_id, payload=file_approval_payload
            )
            approval = file_approval or context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {request_id} does not belong to session {session_id}"
                )
            send_payload = {**payload, "artifact_ids": artifact_ids, "attachments": artifacts}
            result = context.email_service.send_from_draft(
                request_id=request_id, payload=send_payload
            )
            audit_success(
                session_id,
                ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=str(payload.get("mailbox_id", "")),
                        approval_request_id=request_id,
                        approval_decision=(
                            approval.status.value
                        ),
                    ),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                    **result,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                session_id, ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS, args, str(exc)
            )
            raise

    @server.tool()
    def approval_submit(
        session_id: str,
        action_type: str,
        payload: dict[str, Any],
        human_summary: str,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "action_type": action_type,
            "payload": payload,
            "human_summary": human_summary,
        }
        try:
            context.policy.require_tool(session_id, ToolName.APPROVAL_SUBMIT)
            approval = context.policy.create_approval(
                session_id, action_type, payload, human_summary
            )
            result = approval.model_dump(mode="json")
            audit_success(
                session_id, ToolName.APPROVAL_SUBMIT, args, {"request_id": approval.request_id}
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.APPROVAL_SUBMIT, args, str(exc))
            raise

    @server.tool()
    def approval_check(session_id: str, request_id: str) -> dict[str, Any]:
        args = {"session_id": session_id, "request_id": request_id}
        try:
            context.policy.require_tool(session_id, ToolName.APPROVAL_CHECK)
            approval = context.policy.get_approval(request_id)
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {request_id} does not belong to session {session_id}"
                )
            result = approval.model_dump(mode="json")
            audit_success(
                session_id, ToolName.APPROVAL_CHECK, args, {"status": approval.status.value}
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.APPROVAL_CHECK, args, str(exc))
            raise

    @server.tool()
    def email_send_approved(
        session_id: str, request_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        args = {"session_id": session_id, "request_id": request_id, "payload": payload}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND_APPROVED)
            context.policy.require_email_send_enabled(session)
            context.policy.require_email_mailbox(session, str(payload.get("mailbox_id", "")))
            recipients = draft_recipients_from_payload(payload)
            context.policy.require_draft_recipients(session, recipients)
            approval = context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {request_id} does not belong to session {session_id}"
                )
            result = context.email_service.send_from_draft(request_id=request_id, payload=payload)
            audit_success(session_id, ToolName.EMAIL_SEND_APPROVED, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_APPROVED, args, str(exc))
            raise

    return server


def _install_mcp_http_logging(app: Starlette) -> None:
    class McpHttpLoggingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[override]
            if request.url.path != "/mcp":
                return await call_next(request)

            logger.info(
                "gateway_mcp_request method=%s path=%s query=%s accept=%s content_type=%s mcp_session_id=%s protocol_version=%s",
                request.method,
                request.url.path,
                request.url.query,
                request.headers.get("accept", ""),
                request.headers.get("content-type", ""),
                request.headers.get("mcp-session-id", ""),
                request.headers.get("mcp-protocol-version", ""),
            )
            response = await call_next(request)
            logger.info(
                "gateway_mcp_response method=%s path=%s status=%s content_type=%s mcp_session_id=%s",
                request.method,
                request.url.path,
                response.status_code,
                response.headers.get("content-type", ""),
                response.headers.get("mcp-session-id", ""),
            )
            return response

    app.add_middleware(McpHttpLoggingMiddleware)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bootstrap_runtime_config(*, legacy_config_path: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(
            runtime_dir=_project_root() / "runtime", legacy_config_path=legacy_config_path
        ),
        legacy_config_path=legacy_config_path,
    )


def create_app(context: RuntimeContext) -> Starlette:
    server = create_server(context)
    mcp_app = server.streamable_http_app()

    async def retell_webhook(request: Request) -> PlainTextResponse | JSONResponse:
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Retell webhook payload must be an object")
            call = payload.get("call")
            if call is not None and not isinstance(call, dict):
                raise ValueError("Retell webhook call payload must be an object")
            call_id = str((call or {}).get("call_id") or "").strip()
            record = (
                context.retell_phone_call_store.get_by_call_id(call_id) if call_id else None
            )
            signature_config = (
                context.provider_config.resolve_for_directory(record.directory_path)
                if record is not None
                else context.provider_config.resolve_for_directory("root")
            )
            verify_retell_webhook_signature(
                raw_body=raw_body,
                signature_header=request.headers.get("x-retell-signature"),
                api_key=retell_api_key_from_config(signature_config),
            )
        except RetellWebhookSignatureError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        except json.JSONDecodeError:
            return JSONResponse(
                {"error": "Retell webhook payload must be valid JSON"},
                status_code=400,
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 503 if "API key is missing" in message else 400
            return JSONResponse({"error": message}, status_code=status_code)

        event = str(payload.get("event") or "").strip()
        if event in {"call_ended", "call_analyzed", "transcript_updated"} and call_id and call:
            context.retell_phone_call_store.update_from_retell_call(
                call_id,
                call,
                event=event,
            )
        return PlainTextResponse("", status_code=204)

    async def copilot_auth_token(request: Request) -> PlainTextResponse | JSONResponse:
        session_id: str | None = None
        vm_id: str | None = None
        try:
            session_id = _resolve_session_id_from_request(request)
            session = context.policy.require_tool(session_id, ToolName.COPILOT_HOST_AUTH)
            vm_id = session.vm_id
            resolved_config = context.resolve_provider_config_for_session(session_id)
            env_var_name = str(resolved_config.github_token_env or "").strip()
            if not env_var_name:
                raise RuntimeError("github_token_env is not configured for Copilot host auth")
            token = os.environ.get(env_var_name, "").strip()
            if not token:
                context.audit.log_decision(
                    event_type="copilot_auth_request",
                    session_id=session_id,
                    vm_id=vm_id,
                    decision="error",
                    reason=f"missing host token env {env_var_name}",
                    args={"env_var": env_var_name},
                    result={},
                )
                return JSONResponse(
                    {"error": f"Host environment variable {env_var_name} is not set."},
                    status_code=503,
                )
            context.audit.log_decision(
                event_type="copilot_auth_request",
                session_id=session_id,
                vm_id=vm_id,
                decision="allowed",
                args={"env_var": env_var_name},
                result={"status": "issued"},
            )
            return PlainTextResponse(token, headers={"Cache-Control": "no-store"})
        except PolicyError as exc:
            context.audit.log_decision(
                event_type="copilot_auth_request",
                session_id=session_id,
                vm_id=vm_id,
                decision="denied",
                reason=str(exc),
                args={},
                result={},
            )
            return JSONResponse({"error": str(exc)}, status_code=403)
        except Exception as exc:
            context.audit.log_decision(
                event_type="copilot_auth_request",
                session_id=session_id,
                vm_id=vm_id,
                decision="error",
                reason=str(exc),
                args={},
                result={},
            )
            return JSONResponse({"error": str(exc)}, status_code=500)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        # Starlette does not automatically run lifespan handlers for mounted sub-apps.
        # The FastMCP streamable HTTP transport requires its lifespan to initialize the
        # session manager task group before any /mcp request is served.
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(
        debug=False,
        routes=[
            Route("/retell/webhook", retell_webhook, methods=["POST"]),
            Route("/copilot-auth/token", copilot_auth_token, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    _install_mcp_http_logging(app)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Agent Sandbox MCP gateway.")
    parser.add_argument(
        "--legacy-config",
        help="Optional legacy gateway YAML used only to import root config if root is missing.",
    )
    parser.add_argument(
        "--config",
        dest="legacy_config",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    legacy_config_path = Path(args.legacy_config) if args.legacy_config else None
    config = _bootstrap_runtime_config(legacy_config_path=legacy_config_path)
    context = build_runtime(config)
    app = create_app(context)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
