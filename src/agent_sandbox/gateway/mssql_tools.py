"""MSSQL tool registration for the MCP gateway."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from agent_sandbox.models import ToolName
from agent_sandbox.policy import PolicyError
from agent_sandbox.gateway.session_context import _resolve_session_id_from_context


def register_mssql_tools(
    server: Any,
    context: Any,
    deps: Any,
    *,
    transport_session_map: dict[str, str] | None = None,
    audit_success: Any,
    audit_failure: Any,
) -> None:
    """Register MSSQL tools with the MCP gateway server."""

    def _container_worker_backend_for_session(session_id: str) -> ContainerWorkerVmExecutionBackend:
        session = context.policy.get_session(session_id)
        config = context.provider_config.resolve_for_session_manifest(session)
        if config.execution_backend != "container_worker_vm":
            raise ToolError(
                "MSSQL provisioning is only supported for container_worker_vm sessions"
            )
        if not config.container_worker_mssql_enabled:
            raise ToolError("container worker MSSQL provisioning is not enabled")
        return ContainerWorkerVmExecutionBackend(config=config)

    @server.tool()
    def mssql_provision_database(
        logical_name: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id, "logical_name": logical_name}
        try:
            context.policy.require_tool(
                resolved_session_id,
                ToolName.MSSQL_PROVISION_DATABASE,
            )
            backend = _container_worker_backend_for_session(resolved_session_id)
            record = backend.provision_mssql_database(
                session_id=resolved_session_id,
                logical_name=logical_name,
            )
            payload = record.model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.MSSQL_PROVISION_DATABASE,
                args,
                payload,
            )
            return payload
        except (PolicyError, ToolError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MSSQL_PROVISION_DATABASE,
                args,
                str(exc),
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MSSQL_PROVISION_DATABASE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def mssql_list_databases(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id}
        try:
            context.policy.require_tool(resolved_session_id, ToolName.MSSQL_LIST_DATABASES)
            backend = _container_worker_backend_for_session(resolved_session_id)
            record = backend.list_mssql_databases(session_id=resolved_session_id)
            payload = record.model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.MSSQL_LIST_DATABASES,
                args,
                {"count": len(payload.get("databases", []))},
            )
            return payload
        except (PolicyError, ToolError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MSSQL_LIST_DATABASES,
                args,
                str(exc),
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MSSQL_LIST_DATABASES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def mssql_drop_database(
        logical_name: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id, "logical_name": logical_name}
        try:
            context.policy.require_tool(resolved_session_id, ToolName.MSSQL_DROP_DATABASE)
            backend = _container_worker_backend_for_session(resolved_session_id)
            record = backend.drop_mssql_database(
                session_id=resolved_session_id,
                logical_name=logical_name,
            )
            payload = record.model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.MSSQL_DROP_DATABASE,
                args,
                payload,
            )
            return payload
        except (PolicyError, ToolError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MSSQL_DROP_DATABASE,
                args,
                str(exc),
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MSSQL_DROP_DATABASE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
