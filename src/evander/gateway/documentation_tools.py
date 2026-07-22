"""Read-only project documentation tools for MCP agent sessions."""

from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import Context

from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import McpToolName, ToolName
from evander.policy import PolicyError


def register_documentation_tools(
    server: Any,
    context: Any,
    deps: Any,
    *,
    transport_session_map: dict[str, str],
    audit_success: Callable[[str, McpToolName, dict[str, Any], dict[str, Any]], None],
    audit_failure: Callable[..., None],
) -> None:
    """Register live, policy-gated documentation discovery and read tools."""

    @server.tool()
    def list_documentation(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List current project documentation available to this agent session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=transport_session_map,
        )
        args = {"session_id": resolved_session_id}
        try:
            context.policy.require_tool(resolved_session_id, ToolName.DOCUMENTATION_LIST)
            result = deps.admin_runtime_factory().documentation_admin.list_documentation()
            audit_success(
                resolved_session_id,
                ToolName.DOCUMENTATION_LIST,
                args,
                {"count": result.get("count", 0)},
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.DOCUMENTATION_LIST, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.DOCUMENTATION_LIST,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def search_documentation(
        query: str,
        path: str | None = None,
        max_results: int = 20,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Search current project documentation using case-insensitive literal text."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=transport_session_map,
        )
        args = {
            "session_id": resolved_session_id,
            "query": query,
            "path": path,
            "max_results": max_results,
        }
        try:
            context.policy.require_tool(resolved_session_id, ToolName.DOCUMENTATION_SEARCH)
            result = deps.admin_runtime_factory().documentation_admin.search_documentation(
                query,
                path=path,
                max_results=max_results,
            )
            audit_success(
                resolved_session_id,
                ToolName.DOCUMENTATION_SEARCH,
                args,
                {
                    "count": result.get("count", 0),
                    "path": result.get("path"),
                    "truncated": result.get("truncated", False),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.DOCUMENTATION_SEARCH, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.DOCUMENTATION_SEARCH,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def read_documentation(
        path: str,
        start_line: int = 1,
        line_count: int = 200,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read a bounded line range from one current project documentation file."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=transport_session_map,
        )
        args = {
            "session_id": resolved_session_id,
            "path": path,
            "start_line": start_line,
            "line_count": line_count,
        }
        try:
            context.policy.require_tool(resolved_session_id, ToolName.DOCUMENTATION_READ)
            result = deps.admin_runtime_factory().documentation_admin.read_documentation(
                path,
                start_line=start_line,
                line_count=line_count,
            )
            audit_success(
                resolved_session_id,
                ToolName.DOCUMENTATION_READ,
                args,
                {
                    "path": result.get("path"),
                    "start_line": result.get("start_line", 0),
                    "end_line": result.get("end_line", 0),
                    "has_more": result.get("has_more", False),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.DOCUMENTATION_READ, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.DOCUMENTATION_READ,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise


__all__ = ["register_documentation_tools"]
