from __future__ import annotations

import argparse
import contextlib
import logging
import os
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
import uvicorn

from .bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from .admin_service import AdminService
from .config import GatewayConfig
from .email.http_utils import ProviderRequestError
from .issue_service import IssueService
from .models import (
    AdoBacklogCreateRequest,
    AdoBacklogCreateResponse,
    AuditEvent,
    DiscordCommandEnvelope,
    HasPendingDiscordMessagesRequest,
    HasPendingFeedbackRequest,
    ListUnreadDiscordMessagesRequest,
    ListUnreadFeedbackRequest,
    PostPrCommentRequest,
    PostPrFeedbackReplyRequest,
    RequestPrReviewRequest,
    ScheduleResumeRequest,
    ScheduleResumeResponse,
    SubmitPrReviewRequest,
    WakeScheduleDefinition,
    TeamsMessageEnvelope,
    ToolName,
)
from .policy import PolicyError
from .runtime import RuntimeContext, build_runtime
from .services import normalize_pr_feedback_provider

logger = logging.getLogger(__name__)


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
        if not session_id:
            return set(context.policy.policy_config.resolve_for_directory("root").redacted_keys)
        session = context.sessions.get(session_id)
        if session is None:
            return set(context.policy.policy_config.resolve_for_directory("root").redacted_keys)
        return set(context.policy.effective_policy_for_session(session).redacted_keys)

    def audit_success(
        session_id: str,
        tool_name: ToolName,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        session = context.policy.get_session(session_id)
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=session.vm_id,
                tool_name=tool_name.value,
                decision="allowed",
                args=_redact_args(redacted_keys_for_session_id(session_id), args),
                result=result,
            )
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
        if session_id:
            session = context.sessions.get(session_id)
            vm_id = None if session is None else session.vm_id
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=vm_id,
                tool_name=tool_name.value,
                decision=decision,
                args=_redact_args(redacted_keys_for_session_id(session_id), args),
                result={"error": error, "error_class": error_class},
            )
        )

    @server.tool()
    def list_issues(
        session_id: str | None = None,
        status: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)
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
                    issue for issue in issues if str(issue.state or "").strip().lower() in status_filter
                ]
            else:
                filtered = [
                    issue for issue in issues if str(issue.state or "").strip().lower() not in status_filter
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
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)
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
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)
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
                issue_type=request_model.type.value,
                title=request_model.title,
                description=request_model.description,
                iteration_path=request_model.iteration,
                acceptance_criteria=request_model.acceptance_criteria,
                tags=request_model.tags,
                assigned_to=request_model.assigned_user,
                parent_backlog_item_id=request_model.parent_backlog_item_id,
            )
            response_model = AdoBacklogCreateResponse(
                created_item_id=str(remote_payload.get("remote_issue_id") or remote_payload.get("id") or "").strip(),
                url=str(remote_payload.get("url") or "").strip(),
                provider=str(remote_payload.get("provider") or "azure_devops").strip() or "azure_devops",
                project=str(remote_payload.get("project") or request_model.project).strip(),
                type=request_model.type,
                iteration=request_model.iteration,
                title=str(
                    (remote_payload.get("fields") or {}).get("System.Title") or request_model.title
                ).strip(),
                description=(remote_payload.get("fields") or {}).get("System.Description")
                or request_model.description,
                acceptance_criteria=(remote_payload.get("fields") or {}).get(
                    "Microsoft.VSTS.Common.AcceptanceCriteria"
                )
                or request_model.acceptance_criteria,
                tags=request_model.tags,
                assigned_user=(remote_payload.get("fields") or {}).get("System.AssignedTo") or request_model.assigned_user,
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
    def create_local_issue(
        title: str,
        body: str = "",
        repo_source: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)
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
            if metadata and _method_accepts_keyword_argument(admin_service.create_local_issue, "metadata"):
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
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)
        args = {
            "session_id": session_id,
            "issue_id": issue_id,
            "steps": steps,
            "summary": summary,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.SET_ISSUE_PLAN)
            result = AdminService(context.config.runtime_dir).set_issue_plan(issue_id, steps, summary=summary)
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
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)
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
    def claim_next_issue(session_id: str | None = None, *, ctx: Context | None = None) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)
        args = {"session_id": session_id}
        try:
            session = context.policy.require_tool(session_id, ToolName.CLAIM_NEXT_ISSUE)
            result = AdminService(context.config.runtime_dir).claim_next_issue(session_id=session_id)
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
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)

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
        session_id = _resolve_session_id_from_context(session_id, ctx, transport_session_map=transport_session_map)

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
                    summary.last_state_update_at.isoformat() if summary.last_state_update_at else None
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
        args = ListUnreadFeedbackRequest(session_id=session_id, cursor=cursor, limit=limit).model_dump(mode="json")
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
            context.policy.require_pr_feedback_stream(
                session,
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
            )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError("request_id is required when pr_reply_require_approval is enabled")
                context.policy.require_approved(request.request_id, payload=args)
            result = context.pr_reply_service_for_session(session_id).post_reply(
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
                thread_id=request.thread_id,
                comment_id=request.comment_id,
                body_text=request.body_text,
                idempotency_key=request.idempotency_key,
            ).model_dump(mode="json")
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
        provider: str,
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
            result = context.review_request_service_for_session(request.session_id).enqueue_request(
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
            ).model_dump(mode="json")
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
            context.policy.require_pr_feedback_stream(
                session,
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
            )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError("request_id is required when pr_reply_require_approval is enabled")
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
            context.policy.require_pr_feedback_stream(
                session,
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
            )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError("request_id is required when pr_reply_require_approval is enabled")
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
            raw_commands = context.discord_service_for_session(session_id).get_commands(since_cursor)
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
            result = context.discord_service_for_session(session_id).post_status(message, channel_id, session_id)
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
    def teams_get_messages(session_id: str, since_cursor: str | None = None) -> dict[str, Any]:
        args = {"session_id": session_id, "since_cursor": since_cursor}
        try:
            session = context.policy.require_tool(session_id, ToolName.TEAMS_GET_MESSAGES)
            raw_messages = context.teams_service_for_session(session_id).get_messages(since_cursor)
            messages, denied_count, next_cursor = _filter_teams_messages_for_session(
                context,
                session_id,
                session,
                raw_messages,
                args,
                audit_failure,
            )
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
            streams = [{"channel_id": channel_id} for channel_id in context.policy_config.discord_inbox_channel_ids_for_session_id(session_id)]
            result = context.discord_inbox_service_for_session(session_id).has_pending_discord_messages(
                streams=streams,
                session_id=session_id,
            ).model_dump(mode="json")
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
        args = ListUnreadDiscordMessagesRequest(session_id=session_id, cursor=cursor, limit=limit).model_dump(
            mode="json"
        )
        try:
            context.policy.require_tool(session_id, ToolName.LIST_UNREAD_DISCORD_MESSAGES)
            streams = [{"channel_id": channel_id} for channel_id in context.policy_config.discord_inbox_channel_ids_for_session_id(session_id)]
            response = context.discord_inbox_service_for_session(session_id).list_unread_discord_messages(
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
            streams = [{"channel_id": channel_id} for channel_id in context.policy_config.discord_inbox_channel_ids_for_session_id(session_id)]
            response = context.discord_inbox_service_for_session(session_id).list_all_discord_messages(
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
    def email_list_unread(
        session_id: str, mailbox_id: str, folder: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        args = {"session_id": session_id, "mailbox_id": mailbox_id, "folder": folder, "limit": limit}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_LIST_UNREAD)
            context.policy.require_email_mailbox(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, folder)
            email_service = context.email_service_for_session(session_id)
            messages = [
                message.model_dump(mode="json")
                for message in email_service.list_unread(mailbox_id=mailbox_id, folder=folder, limit=limit)
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
            audit_success(session_id, ToolName.EMAIL_CREATE_DRAFT, args, {"draft_id": draft.draft_id})
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
            approval = context.policy.create_approval(session_id, action_type, payload, human_summary)
            result = approval.model_dump(mode="json")
            audit_success(session_id, ToolName.APPROVAL_SUBMIT, args, {"request_id": approval.request_id})
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
                raise PolicyError(f"Approval request {request_id} does not belong to session {session_id}")
            result = approval.model_dump(mode="json")
            audit_success(session_id, ToolName.APPROVAL_CHECK, args, {"status": approval.status.value})
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.APPROVAL_CHECK, args, str(exc))
            raise

    @server.tool()
    def email_send_approved(session_id: str, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        args = {"session_id": session_id, "request_id": request_id, "payload": payload}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND_APPROVED)
            context.policy.require_email_send_enabled(session)
            context.policy.require_email_mailbox(session, str(payload.get("mailbox_id", "")))
            recipients = draft_recipients_from_payload(payload)
            context.policy.require_draft_recipients(session, recipients)
            approval = context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(f"Approval request {request_id} does not belong to session {session_id}")
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
        runtime_dir=resolve_runtime_dir(runtime_dir=_project_root() / "runtime", legacy_config_path=legacy_config_path),
        legacy_config_path=legacy_config_path,
    )


def create_app(context: RuntimeContext) -> Starlette:
    server = create_server(context)
    mcp_app = server.streamable_http_app()

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
