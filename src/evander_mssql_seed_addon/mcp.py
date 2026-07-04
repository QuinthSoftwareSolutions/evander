from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext
from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import AuditEvent
from evander.policy import PolicyError

from .client import MssqlSeedSidecarClient, MssqlSeedSidecarClientError
from .state import (
    MssqlSeedDatabaseRecord,
    MssqlSeedStateError,
    MssqlSeedStore,
    utc_now_iso,
)

PROVISION_DATABASE_TOOL = "mssql_seed_provision_database"
RESET_DATABASE_TOOL = "mssql_seed_reset_database"
LIST_DATABASES_TOOL = "mssql_seed_list_databases"

MCP_TOOL_NAMES = (
    PROVISION_DATABASE_TOOL,
    RESET_DATABASE_TOOL,
    LIST_DATABASES_TOOL,
)


def register_mcp_tools(server: FastMCP, context: AddonContext) -> tuple[str, ...]:
    store = MssqlSeedStore(context.runtime_dir)

    def _runtime() -> Any:
        return context.service

    def _transport_session_map() -> dict[str, str] | None:
        value = getattr(server, "_evander_transport_session_map", None)
        return value if isinstance(value, dict) else None

    def _resolve_session_id(session_id: str | None, ctx: Context | None) -> str:
        return _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=_transport_session_map(),
        )

    def _require_tool(session_id: str, tool_name: str) -> None:
        runtime = _runtime()
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("MSSQL seed MCP tools require a runtime policy context")
        runtime.policy.require_tool(session_id, tool_name)

    def _backend_for_session(session_id: str) -> ContainerWorkerVmExecutionBackend:
        runtime = _runtime()
        session = runtime.policy.get_session(session_id)
        config = runtime.provider_config.resolve_for_session_manifest(session)
        if config.execution_backend != "container_worker_vm":
            raise ToolError("MSSQL seed restore is only supported for container_worker_vm sessions")
        if not config.container_worker_mssql_enabled:
            raise ToolError("container worker MSSQL provisioning is not enabled")
        return ContainerWorkerVmExecutionBackend(config=config)

    def _restore_seed(
        *,
        session_id: str,
        logical_name: str,
        seed_alias: str,
        tool_name: str,
    ) -> tuple[dict[str, Any], MssqlSeedDatabaseRecord]:
        seed = store.get_seed(seed_alias)
        sidecar = store.get_sidecar(seed.sidecar_id)
        backend = _backend_for_session(session_id)
        record = backend.provision_mssql_database(session_id=session_id, logical_name=logical_name)
        restore = MssqlSeedSidecarClient(
            base_url=sidecar.base_url,
            token_env=sidecar.token_env,
            timeout_seconds=sidecar.restore_timeout_seconds,
        ).restore_database(
            session_id=session_id,
            logical_name=logical_name,
            database_name=record.database_name,
            login_name=record.username,
            backup_path=seed.backup_path,
        )
        now = utc_now_iso()
        previous = None
        try:
            previous = store.get_database(session_id, logical_name)
        except MssqlSeedStateError:
            pass
        database = MssqlSeedDatabaseRecord(
            session_id=session_id,
            logical_name=logical_name,
            seed_alias=seed.seed_alias,
            sidecar_id=sidecar.sidecar_id,
            database_name=record.database_name,
            username=record.username,
            server=record.server,
            port=record.port,
            restored_at=now if previous is None else previous.restored_at,
            last_reset_at=now if tool_name == RESET_DATABASE_TOOL else None,
            reset_count=0 if previous is None else previous.reset_count,
        )
        if tool_name == RESET_DATABASE_TOOL:
            database.reset_count = 1 if previous is None else previous.reset_count + 1
        database = store.upsert_database(database)
        payload = record.model_dump(mode="json")
        payload["seed_alias"] = seed.seed_alias
        payload["seed_restore"] = {
            "status": restore.get("status", "restored"),
            "sidecar_id": sidecar.sidecar_id,
            "file_count": restore.get("file_count"),
            "reset_count": database.reset_count,
            "restored_at": database.restored_at,
            "last_reset_at": database.last_reset_at,
        }
        return payload, database

    @server.tool()
    def mssql_seed_provision_database(
        logical_name: str,
        seed_alias: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = str(session_id or "").strip()
        args = {
            "session_id": resolved_session_id,
            "logical_name": logical_name,
            "seed_alias": seed_alias,
        }
        try:
            resolved_session_id = _resolve_session_id(resolved_session_id, ctx)
            args["session_id"] = resolved_session_id
            _require_tool(resolved_session_id, PROVISION_DATABASE_TOOL)
            payload, _database = _restore_seed(
                session_id=resolved_session_id,
                logical_name=logical_name,
                seed_alias=seed_alias,
                tool_name=PROVISION_DATABASE_TOOL,
            )
            _audit_success(resolved_session_id, PROVISION_DATABASE_TOOL, args, payload)
            return payload
        except (
            PolicyError,
            ToolError,
            ValueError,
            MssqlSeedStateError,
            MssqlSeedSidecarClientError,
        ) as exc:
            _audit_failure(resolved_session_id, PROVISION_DATABASE_TOOL, args, str(exc))
            raise ToolError(str(exc)) from exc

    @server.tool()
    def mssql_seed_reset_database(
        logical_name: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = str(session_id or "").strip()
        args = {"session_id": resolved_session_id, "logical_name": logical_name}
        try:
            resolved_session_id = _resolve_session_id(resolved_session_id, ctx)
            args["session_id"] = resolved_session_id
            _require_tool(resolved_session_id, RESET_DATABASE_TOOL)
            existing = store.get_database(resolved_session_id, logical_name)
            payload, _database = _restore_seed(
                session_id=resolved_session_id,
                logical_name=logical_name,
                seed_alias=existing.seed_alias,
                tool_name=RESET_DATABASE_TOOL,
            )
            _audit_success(resolved_session_id, RESET_DATABASE_TOOL, args, payload)
            return payload
        except (
            PolicyError,
            ToolError,
            ValueError,
            MssqlSeedStateError,
            MssqlSeedSidecarClientError,
        ) as exc:
            _audit_failure(resolved_session_id, RESET_DATABASE_TOOL, args, str(exc))
            raise ToolError(str(exc)) from exc

    @server.tool()
    def mssql_seed_list_databases(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = str(session_id or "").strip()
        args = {"session_id": resolved_session_id}
        try:
            resolved_session_id = _resolve_session_id(resolved_session_id, ctx)
            args["session_id"] = resolved_session_id
            _require_tool(resolved_session_id, LIST_DATABASES_TOOL)
            databases = [
                database.model_dump(mode="json")
                for database in store.list_databases(session_id=resolved_session_id)
            ]
            payload = {"session_id": resolved_session_id, "databases": databases}
            _audit_success(
                resolved_session_id,
                LIST_DATABASES_TOOL,
                args,
                {"count": len(databases)},
            )
            return payload
        except (PolicyError, ToolError, ValueError, MssqlSeedStateError) as exc:
            _audit_failure(resolved_session_id, LIST_DATABASES_TOOL, args, str(exc))
            raise ToolError(str(exc)) from exc

    def _audit_success(
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        runtime = _runtime()
        audit = getattr(runtime, "audit", None)
        log = getattr(audit, "log", None)
        if not callable(log):
            return
        session = getattr(runtime, "session_repository", None)
        manifest = session.get_session(session_id) if session is not None else None
        log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=None if manifest is None else manifest.vm_id,
                directory_path=None if manifest is None else manifest.directory_path,
                tool_name=tool_name,
                decision="allowed",
                args=dict(args),
                result=_redacted_audit_result(result),
            )
        )

    def _audit_failure(
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        error: str,
    ) -> None:
        runtime = _runtime()
        audit = getattr(runtime, "audit", None)
        log = getattr(audit, "log", None)
        if not callable(log):
            return
        log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id or None,
                tool_name=tool_name,
                decision="denied",
                reason=error,
                args=dict(args),
                result={"status": "failed"},
            )
        )

    return MCP_TOOL_NAMES


def _redacted_audit_result(result: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(result)
    for key in ("password", "connection_string"):
        if key in redacted:
            redacted[key] = "[redacted]"
    return redacted


__all__ = ["MCP_TOOL_NAMES", "register_mcp_tools"]
