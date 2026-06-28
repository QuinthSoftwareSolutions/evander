"""Approval tool registration helpers for the MCP gateway."""

from __future__ import annotations

from typing import Any, Callable

from agent_sandbox.models import McpToolName, ToolName
from agent_sandbox.policy import PolicyError


def register_approval_tools(
    server: Any,
    context: Any,
    deps: Any,
    *,
    audit_success: Callable[[str, McpToolName, dict[str, Any], dict[str, Any]], None],
    audit_failure: Callable[..., None],
) -> None:
    """Register approval tools with the MCP gateway server."""

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
