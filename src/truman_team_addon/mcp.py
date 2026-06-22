from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.addons import AddonContext

from .repository import TeamStateConflictError, TeamStateRepository

TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL = "team_create_operator_directive"

MCP_TOOL_NAMES = (TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,)


def register_mcp_tools(server: FastMCP, context: AddonContext) -> tuple[str, ...]:
    repo = TeamStateRepository(context.runtime_dir)

    def _runtime() -> Any:
        return context.service

    def _require_tool(session_id: str | None, tool_name: str) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ToolError("session_id is required")
        runtime = _runtime()
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("Team MCP tools require a runtime policy context")
        runtime.policy.require_tool(normalized_session_id, tool_name)
        return normalized_session_id

    @server.tool()
    def team_create_operator_directive(
        team_id: str,
        message: str,
        session_id: str | None = None,
        recipient_agent_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        title: str | None = None,
        source_message_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(
            session_id,
            TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        )
        try:
            state, item = repo.create_operator_directive(
                team_id=team_id,
                message=message,
                recipient_agent_id=recipient_agent_id,
                project_id=project_id,
                task_id=task_id,
                title=title,
                source="team_governance_mcp",
                source_message_id=source_message_id,
                created_by=f"agent-session:{resolved_session_id}",
                requesting_session_id=resolved_session_id,
                expected_revision=expected_revision,
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "revision": exc.current_revision,
                "inbox_item_id": None,
                "recipient_agent_id": None,
                "team_id": team_id,
                "project_id": project_id,
                "task_id": task_id,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        except (TypeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        return {
            "success": True,
            "revision": state.revision,
            "inbox_item_id": item.id,
            "recipient_agent_id": item.recipient_agent_id,
            "team_id": item.payload.get("team_id"),
            "project_id": item.payload.get("project_id"),
            "task_id": item.task_id,
            "operator_directive": item.model_dump(mode="json"),
            "error": None,
        }

    return MCP_TOOL_NAMES
