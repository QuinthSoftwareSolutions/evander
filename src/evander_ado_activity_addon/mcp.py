from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from evander.addons import AddonContext, AddonMcpTool
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.policy import PolicyError

from .service import AdoActivityIndexService

SYNC_ADO_ACTIVITY_INDEX_TOOL = "sync_ado_activity_index"
QUERY_ADO_ACTIVITY_INDEX_TOOL = "query_ado_activity_index"
MCP_TOOL_NAMES = (SYNC_ADO_ACTIVITY_INDEX_TOOL, QUERY_ADO_ACTIVITY_INDEX_TOOL)
MCP_TOOL_CATALOG = (
    AddonMcpTool(
        name=SYNC_ADO_ACTIVITY_INDEX_TOOL,
        label="Sync ADO activity index",
        description="Synchronize durable Azure DevOps work-item activity into the local index.",
    ),
    AddonMcpTool(
        name=QUERY_ADO_ACTIVITY_INDEX_TOOL,
        label="Query ADO activity index",
        description="Query the durable Azure DevOps work-item activity index.",
    ),
)


def register_mcp_tools(server: Any, context: AddonContext) -> tuple[str, ...]:
    def _runtime() -> Any:
        return context.service

    def _transport_session_map() -> dict[str, str] | None:
        value = getattr(server, "_evander_transport_session_map", None)
        return value if isinstance(value, dict) else None

    def _require_tool(
        session_id: str | None,
        ctx: Context | None,
        tool_name: str,
    ) -> Any:
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=_transport_session_map(),
        )
        runtime = _runtime()
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("ADO activity MCP tools require a runtime policy context")
        try:
            return runtime.policy.require_tool(resolved_session_id, tool_name)
        except PolicyError as exc:
            raise ToolError(str(exc)) from exc

    def _service() -> AdoActivityIndexService:
        runtime = _runtime()
        resolver = getattr(runtime, "provider_config", None)
        return AdoActivityIndexService(
            addon_runtime_dir=context.addon_runtime_dir,
            provider_config_resolver=resolver,
        )

    @server.tool(
        name=SYNC_ADO_ACTIVITY_INDEX_TOOL,
        description="Backfill Azure DevOps activity into the durable add-on index.",
    )
    def sync_ado_activity_index(
        since: str,
        until: str | None = None,
        directory_path: str | None = None,
        scope: str = "all",
        actor: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        statuses: list[str] | None = None,
        include_system_comments: bool = False,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session = _require_tool(session_id, ctx, SYNC_ADO_ACTIVITY_INDEX_TOOL)
        effective_directory_path = _session_directory_path(session, requested=directory_path)
        return _service().sync(
            since=_parse_datetime(since),
            until=_parse_optional_datetime(until),
            directory_path=effective_directory_path,
            scope=scope,
            actor=_optional(actor),
            project=_optional(project),
            repo=_optional(repo),
            states=states or [],
            types=types or [],
            tags_any=tags_any or [],
            statuses=statuses or [],
            include_system_comments=include_system_comments,
        )

    @server.tool(
        name=QUERY_ADO_ACTIVITY_INDEX_TOOL,
        description="Query Azure DevOps activity from the durable add-on index.",
    )
    def query_ado_activity_index(
        since: str,
        until: str | None = None,
        actor: str | None = None,
        activity_kinds: list[str] | None = None,
        directory_path: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        work_item_id: str | None = None,
        pr_id: str | None = None,
        top: int = 100,
        cursor: str | None = None,
        include_raw: bool = False,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session = _require_tool(session_id, ctx, QUERY_ADO_ACTIVITY_INDEX_TOOL)
        effective_directory_path = _session_directory_path(session, requested=directory_path)
        return _service().query(
            since=_parse_datetime(since),
            until=_parse_optional_datetime(until),
            actor=_optional(actor),
            activity_kinds=activity_kinds or [],
            directory_path=effective_directory_path,
            project=_optional(project),
            repo=_optional(repo),
            work_item_id=_optional(work_item_id),
            pr_id=_optional(pr_id),
            top=top,
            cursor=_optional(cursor),
            include_raw=include_raw,
        )

    return MCP_TOOL_NAMES


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if _optional(value) is None:
        return None
    return _parse_datetime(str(value))


def _parse_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("datetime value is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _session_directory_path(session: Any, *, requested: str | None) -> str:
    authorized = _optional(getattr(session, "directory_path", None)) or "root"
    requested_path = _optional(requested)
    if requested_path is not None and requested_path != authorized:
        raise ToolError("directory_path must match the authorized session directory")
    return authorized


def _optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
