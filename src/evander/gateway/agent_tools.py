"""Agent observability tool registration helpers for the MCP gateway."""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.mcpserver import Context

from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import McpToolName, ToolName
from evander.policy import PolicyError


def _normalize_agent_status(state: str | None) -> str:
    normalized = str(state or "").strip().lower()
    if normalized == "running":
        return "active"
    if normalized in {"idle", "not_started"}:
        return "idle"
    return "unknown"


def register_agent_tools(
    server: Any,
    context: Any,
    deps: Any,
    *,
    transport_session_map: dict[str, str],
    audit_success: Callable[[str, McpToolName, dict[str, Any], dict[str, Any]], None],
    audit_failure: Callable[..., None],
    require_vm_visible_in_session_directory: Callable[..., Any],
) -> None:
    """Register agent observability tools with the MCP gateway server."""

    @server.tool()
    def get_agent_status(
        vm_name: str,
        vm_user: str = "agent",
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "vm_name": vm_name,
            "vm_user": vm_user,
        }
        try:
            session = context.policy.require_tool(resolved_session_id, ToolName.GET_AGENT_STATUS)
            summary = require_vm_visible_in_session_directory(
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
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
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
            require_vm_visible_in_session_directory(
                context,
                session,
                vm_name=vm_name,
                vm_user=vm_user,
            )
            safe_lines = max(1, min(lines, 1000))
            logs = deps.admin_runtime_factory().vm_agent_admin.get_agent_log_snapshot(
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
