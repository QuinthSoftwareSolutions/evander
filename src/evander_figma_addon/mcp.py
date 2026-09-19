from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext, AddonMcpTool
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.policy import PolicyError

from .models import DesignRequest, FigmaError
from .service import Actor, FigmaService

QUERY_OPERATIONS = {"pages", "inspect", "variables", "comments", "history"}
MCP_TOOL_CATALOG = (
    AddonMcpTool(
        name="figma_list_targets",
        label="List Figma targets",
        group="Figma",
        description="List files and capabilities granted to this session.",
    ),
    AddonMcpTool(
        name="figma_query",
        label="Read Figma",
        group="Figma",
        description="Read pages, inspect a design and its revision, variables/styles, comments, or operation history.",
    ),
    AddonMcpTool(
        name="figma_execute",
        label="Work on Figma designs",
        group="Figma",
        description="Create areas, edit native designs, compose pinned snapshots, comment, snapshot, import or reconcile. "
        "Reuse request_id on retry. Check status; uncertain is not success. "
        "Use figma_query inspect before edits and include its revision.",
    ),
)


def session_directory(session):
    value = getattr(session, "directory_path", "root")
    return value.as_posix() if hasattr(value, "as_posix") else str(value)


def register_mcp_tools(server: Any, context: AddonContext):
    def service(tool_name, session_id, ctx):
        runtime = context.service
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("Figma tools require a runtime policy context")
        mapping = getattr(server, "_evander_transport_session_map", None)
        resolved = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=mapping if isinstance(mapping, dict) else None
        )
        session = runtime.policy.require_tool(resolved, tool_name)
        return FigmaService(
            context.runtime_dir, Actor(session_directory(session), session.session_id)
        )

    @server.tool(name="figma_list_targets", description=MCP_TOOL_CATALOG[0].description)
    def figma_list_targets(session_id: str | None = None, ctx: Context | None = None):
        try:
            return {"targets": service("figma_list_targets", session_id, ctx).list_targets()}
        except (PolicyError, FigmaError, ValueError) as exc:
            raise ToolError(str(exc)) from None

    @server.tool(name="figma_query", description=MCP_TOOL_CATALOG[1].description)
    def figma_query(
        request: DesignRequest, session_id: str | None = None, ctx: Context | None = None
    ):
        try:
            if request.operation not in QUERY_OPERATIONS:
                raise FigmaError("Use figma_execute for this operation")
            return service("figma_query", session_id, ctx).execute(request)
        except (PolicyError, FigmaError, ValueError) as exc:
            raise ToolError(str(exc)) from None

    @server.tool(name="figma_execute", description=MCP_TOOL_CATALOG[2].description)
    def figma_execute(
        request: DesignRequest, session_id: str | None = None, ctx: Context | None = None
    ):
        try:
            return service("figma_execute", session_id, ctx).execute(request)
        except (PolicyError, FigmaError, ValueError) as exc:
            raise ToolError(str(exc)) from None

    return tuple(tool.name for tool in MCP_TOOL_CATALOG)
