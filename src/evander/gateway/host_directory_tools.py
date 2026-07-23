from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander.gateway.session_context import _resolve_session_id_from_context
from evander.host_directories.service import (
    HostDirectoryError,
    HostDirectoryPolicyError,
    HostDirectoryService,
)
from evander.models import ToolName
from evander.policy import PolicyError
from evander.runtime import RuntimeContext


def register_host_directory_tools(
    server: FastMCP,
    context: RuntimeContext,
    service: HostDirectoryService,
) -> None:
    def session_for(
        session_id: str | None,
        ctx: Context | None,
        tool_name: ToolName,
    ) -> Any:
        transport_session_map = getattr(server, "_evander_transport_session_map", None)
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=(
                transport_session_map if isinstance(transport_session_map, dict) else None
            ),
        )
        try:
            return context.policy.require_tool(resolved_session_id, tool_name)
        except PolicyError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name=ToolName.HOST_DIRECTORY_LIST.value,
        description=(
            "List regular files and folders below a named host directory granted to this "
            "agent. Paths are relative and host roots are never disclosed."
        ),
    )
    def host_directory_list(
        directory_id: str,
        path: str = "",
        recursive: bool = False,
        limit: int = 100,
        cursor: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session = session_for(session_id, ctx, ToolName.HOST_DIRECTORY_LIST)
        try:
            return service.list_for_session(
                session=session,
                directory_id=directory_id,
                path=path,
                recursive=recursive,
                limit=limit,
                cursor=cursor,
            )
        except (ValueError, OSError, HostDirectoryError, HostDirectoryPolicyError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name=ToolName.HOST_DIRECTORY_IMPORT.value,
        description=(
            "Import one regular file or folder from a named host directory into the "
            "session-owned artifact store. Folders are returned as tar.gz artifacts."
        ),
    )
    def host_directory_import(
        directory_id: str,
        path: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session = session_for(session_id, ctx, ToolName.HOST_DIRECTORY_IMPORT)
        try:
            return service.import_for_session(
                session=session,
                directory_id=directory_id,
                path=path,
            )
        except (ValueError, OSError, HostDirectoryError, HostDirectoryPolicyError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name=ToolName.HOST_DIRECTORY_DELETE.value,
        description=(
            "Move one file or folder from a named host directory into recoverable host "
            "quarantine. Use the exact revision returned by listing or import."
        ),
    )
    def host_directory_delete(
        directory_id: str,
        path: str,
        expected_revision: str,
        recursive: bool = False,
        approval_request_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        del approval_request_id
        session = session_for(session_id, ctx, ToolName.HOST_DIRECTORY_DELETE)
        try:
            return service.delete_for_session(
                session=session,
                directory_id=directory_id,
                path=path,
                expected_revision=expected_revision,
                recursive=recursive,
            )
        except (ValueError, OSError, HostDirectoryError, HostDirectoryPolicyError) as exc:
            raise ToolError(str(exc)) from exc
