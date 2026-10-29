from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from evander.connectors.drivers import ConnectorInvocationError
from evander.connectors.service import ConnectorPolicyError, ConnectorService
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName
from evander.policy import PolicyError
from evander.runtime import RuntimeContext


def register_connector_tools(
    server: MCPServer,
    context: RuntimeContext,
    service: ConnectorService,
) -> None:
    def _transport_session_map() -> dict[str, str] | None:
        value = getattr(server, "_evander_transport_session_map", None)
        return value if isinstance(value, dict) else None

    def _session(
        session_id: str | None,
        ctx: Context | None,
        tool_name: ToolName,
    ) -> Any:
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=_transport_session_map(),
        )
        try:
            return context.policy.require_tool(resolved_session_id, tool_name)
        except PolicyError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name=ToolName.LIST_CONNECTORS.value,
        description=(
            "List the named connector operations granted to this agent session, including "
            "their descriptions, JSON input schemas, result modes, and approval requirements."
        ),
    )
    def list_connectors(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session = _session(session_id, ctx, ToolName.LIST_CONNECTORS)
        try:
            connectors = service.list_for_session(session)
        except (ValueError, ConnectorPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        return {"connectors": connectors}

    @server.tool(
        name=ToolName.CALL_CONNECTOR.value,
        description=(
            "Invoke one named operation on a connector granted to this agent session. "
            "Use list_connectors first to obtain the operation input schema."
        ),
    )
    async def call_connector(
        connector_id: str,
        operation: str,
        arguments: dict[str, Any] | None = None,
        approval_request_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        del approval_request_id
        session = _session(session_id, ctx, ToolName.CALL_CONNECTOR)
        try:
            result = await service.invoke(
                session=session,
                connector_id=connector_id,
                operation_name=operation,
                arguments=dict(arguments or {}),
            )
        except (ValueError, ConnectorPolicyError, ConnectorInvocationError) as exc:
            raise ToolError(str(exc)) from exc
        return result.model_dump(mode="json", exclude_none=True)
