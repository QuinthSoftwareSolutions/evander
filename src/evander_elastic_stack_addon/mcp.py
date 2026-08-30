from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext, AddonMcpTool
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.policy import PolicyError

from .client import ElasticStackApiError, ElasticStackConfigError
from .service import client_for_session


LIST_TARGETS_TOOL = "elastic_stack_list_targets"
ELASTICSEARCH_CLUSTER_HEALTH_TOOL = "elasticsearch_get_cluster_health"
ELASTICSEARCH_LIST_INDICES_TOOL = "elasticsearch_list_indices"
ELASTICSEARCH_GET_MAPPINGS_TOOL = "elasticsearch_get_mappings"
ELASTICSEARCH_BULK_INDEX_DOCUMENTS_TOOL = "elasticsearch_bulk_index_documents"
KIBANA_GET_STATUS_TOOL = "kibana_get_status"
KIBANA_LIST_DATA_VIEWS_TOOL = "kibana_list_data_views"
KIBANA_FIND_SAVED_OBJECTS_TOOL = "kibana_find_saved_objects"
KIBANA_UPSERT_DATA_VIEW_TOOL = "kibana_upsert_data_view"
KIBANA_IMPORT_SAVED_OBJECTS_TOOL = "kibana_import_saved_objects"
KIBANA_SET_DEFAULT_DATA_VIEW_TOOL = "kibana_set_default_data_view"

MCP_TOOL_NAMES = (
    LIST_TARGETS_TOOL,
    ELASTICSEARCH_CLUSTER_HEALTH_TOOL,
    ELASTICSEARCH_LIST_INDICES_TOOL,
    ELASTICSEARCH_GET_MAPPINGS_TOOL,
    ELASTICSEARCH_BULK_INDEX_DOCUMENTS_TOOL,
    KIBANA_GET_STATUS_TOOL,
    KIBANA_LIST_DATA_VIEWS_TOOL,
    KIBANA_FIND_SAVED_OBJECTS_TOOL,
    KIBANA_UPSERT_DATA_VIEW_TOOL,
    KIBANA_IMPORT_SAVED_OBJECTS_TOOL,
    KIBANA_SET_DEFAULT_DATA_VIEW_TOOL,
)


def _catalog(name: str, label: str, description: str) -> AddonMcpTool:
    return AddonMcpTool(name=name, label=label, description=description, group="Elastic Stack")


MCP_TOOL_CATALOG = (
    _catalog(LIST_TARGETS_TOOL, "List Elastic Stack targets", "List approved session targets."),
    _catalog(
        ELASTICSEARCH_CLUSTER_HEALTH_TOOL,
        "Get Elasticsearch cluster health",
        "Read cluster health from an approved target.",
    ),
    _catalog(
        ELASTICSEARCH_LIST_INDICES_TOOL,
        "List Elasticsearch indices",
        "List bounded index metadata from an approved target.",
    ),
    _catalog(
        ELASTICSEARCH_GET_MAPPINGS_TOOL,
        "Get Elasticsearch mappings",
        "Read mappings for an approved index pattern.",
    ),
    _catalog(
        ELASTICSEARCH_BULK_INDEX_DOCUMENTS_TOOL,
        "Bulk index Elasticsearch documents",
        "Index a bounded set of documents into an approved target and index.",
    ),
    _catalog(KIBANA_GET_STATUS_TOOL, "Get Kibana status", "Read Kibana service status."),
    _catalog(
        KIBANA_LIST_DATA_VIEWS_TOOL,
        "List Kibana data views",
        "List data views in an approved Kibana target.",
    ),
    _catalog(
        KIBANA_FIND_SAVED_OBJECTS_TOOL,
        "Find Kibana saved objects",
        "Find dashboards and visualizations in an approved Kibana target.",
    ),
    _catalog(
        KIBANA_UPSERT_DATA_VIEW_TOOL,
        "Upsert Kibana data view",
        "Create or replace a Kibana data view.",
    ),
    _catalog(
        KIBANA_IMPORT_SAVED_OBJECTS_TOOL,
        "Import Kibana saved objects",
        "Import a bounded, version-compatible Kibana NDJSON bundle.",
    ),
    _catalog(
        KIBANA_SET_DEFAULT_DATA_VIEW_TOOL,
        "Set default Kibana data view",
        "Set or clear the default Kibana data view.",
    ),
)


def register_mcp_tools(server: Any, context: AddonContext) -> tuple[str, ...]:
    def _transport_session_map() -> dict[str, str] | None:
        value = getattr(server, "_evander_transport_session_map", None)
        return value if isinstance(value, dict) else None

    def _session(
        tool_name: str,
        session_id: str | None,
        ctx: Context | None,
    ) -> Any:
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=_transport_session_map(),
        )
        runtime = context.service
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("Elastic Stack MCP tools require a runtime policy context")
        try:
            return runtime.policy.require_tool(resolved_session_id, tool_name)
        except PolicyError as exc:
            raise ToolError(str(exc)) from exc

    def _invoke(
        tool_name: str,
        session_id: str | None,
        ctx: Context | None,
        operation: Callable[[Any], Any],
    ) -> Any:
        session = _session(tool_name, session_id, ctx)
        try:
            return operation(client_for_session(context.service, session))
        except (ElasticStackApiError, ElasticStackConfigError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name=LIST_TARGETS_TOOL, description="List approved Elastic Stack targets.")
    def elastic_stack_list_targets(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        items = _invoke(
            LIST_TARGETS_TOOL,
            session_id,
            ctx,
            lambda client: client.list_targets(),
        )
        return {"items": items, "count": len(items)}

    @server.tool(
        name=ELASTICSEARCH_CLUSTER_HEALTH_TOOL,
        description="Read Elasticsearch cluster health from an approved target.",
    )
    def elasticsearch_get_cluster_health(
        target_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            ELASTICSEARCH_CLUSTER_HEALTH_TOOL,
            session_id,
            ctx,
            lambda client: client.elasticsearch_cluster_health(target_id),
        )

    @server.tool(
        name=ELASTICSEARCH_LIST_INDICES_TOOL,
        description="List bounded Elasticsearch index metadata from an approved target.",
    )
    def elasticsearch_list_indices(
        target_id: str,
        index_pattern: str = "*",
        include_hidden: bool = False,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        items = _invoke(
            ELASTICSEARCH_LIST_INDICES_TOOL,
            session_id,
            ctx,
            lambda client: client.elasticsearch_list_indices(
                target_id,
                index_pattern=index_pattern,
                include_hidden=include_hidden,
            ),
        )
        return {"items": items, "count": len(items)}

    @server.tool(
        name=ELASTICSEARCH_GET_MAPPINGS_TOOL,
        description="Read Elasticsearch mappings for an approved index pattern.",
    )
    def elasticsearch_get_mappings(
        target_id: str,
        index_pattern: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            ELASTICSEARCH_GET_MAPPINGS_TOOL,
            session_id,
            ctx,
            lambda client: client.elasticsearch_get_mappings(
                target_id,
                index_pattern=index_pattern,
            ),
        )

    @server.tool(
        name=ELASTICSEARCH_BULK_INDEX_DOCUMENTS_TOOL,
        description=(
            "Index bounded JSON documents into an approved Elasticsearch index. "
            "Use id_field for repeatable synthetic-data seeding."
        ),
    )
    def elasticsearch_bulk_index_documents(
        target_id: str,
        index_name: str,
        documents: list[dict[str, Any]],
        id_field: str | None = None,
        refresh: str = "wait_for",
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            ELASTICSEARCH_BULK_INDEX_DOCUMENTS_TOOL,
            session_id,
            ctx,
            lambda client: client.elasticsearch_bulk_index_documents(
                target_id,
                index_name=index_name,
                documents=documents,
                id_field=id_field,
                refresh=refresh,
            ),
        )

    @server.tool(name=KIBANA_GET_STATUS_TOOL, description="Read Kibana service status.")
    def kibana_get_status(
        target_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            KIBANA_GET_STATUS_TOOL,
            session_id,
            ctx,
            lambda client: client.kibana_status(target_id),
        )

    @server.tool(name=KIBANA_LIST_DATA_VIEWS_TOOL, description="List Kibana data views.")
    def kibana_list_data_views(
        target_id: str,
        space_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            KIBANA_LIST_DATA_VIEWS_TOOL,
            session_id,
            ctx,
            lambda client: client.kibana_list_data_views(target_id, space_id=space_id),
        )

    @server.tool(
        name=KIBANA_FIND_SAVED_OBJECTS_TOOL,
        description="Find Kibana dashboards, visualizations, and other saved objects.",
    )
    def kibana_find_saved_objects(
        target_id: str,
        types: list[str],
        search: str | None = None,
        page: int = 1,
        per_page: int = 100,
        space_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            KIBANA_FIND_SAVED_OBJECTS_TOOL,
            session_id,
            ctx,
            lambda client: client.kibana_find_saved_objects(
                target_id,
                types=types,
                search=search,
                page=page,
                per_page=per_page,
                space_id=space_id,
            ),
        )

    @server.tool(name=KIBANA_UPSERT_DATA_VIEW_TOOL, description="Create or replace a data view.")
    def kibana_upsert_data_view(
        target_id: str,
        data_view: dict[str, Any],
        override: bool = True,
        space_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            KIBANA_UPSERT_DATA_VIEW_TOOL,
            session_id,
            ctx,
            lambda client: client.kibana_upsert_data_view(
                target_id,
                data_view=data_view,
                override=override,
                space_id=space_id,
            ),
        )

    @server.tool(
        name=KIBANA_IMPORT_SAVED_OBJECTS_TOOL,
        description="Import a bounded, version-compatible Kibana NDJSON bundle.",
    )
    def kibana_import_saved_objects(
        target_id: str,
        ndjson: str,
        overwrite: bool = True,
        create_new_copies: bool = False,
        compatibility_mode: bool = False,
        space_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            KIBANA_IMPORT_SAVED_OBJECTS_TOOL,
            session_id,
            ctx,
            lambda client: client.kibana_import_saved_objects(
                target_id,
                ndjson=ndjson,
                overwrite=overwrite,
                create_new_copies=create_new_copies,
                compatibility_mode=compatibility_mode,
                space_id=space_id,
            ),
        )

    @server.tool(
        name=KIBANA_SET_DEFAULT_DATA_VIEW_TOOL,
        description="Set or clear the default Kibana data view.",
    )
    def kibana_set_default_data_view(
        target_id: str,
        data_view_id: str | None,
        force: bool = True,
        space_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            KIBANA_SET_DEFAULT_DATA_VIEW_TOOL,
            session_id,
            ctx,
            lambda client: client.kibana_set_default_data_view(
                target_id,
                data_view_id=data_view_id,
                force=force,
                space_id=space_id,
            ),
        )

    return MCP_TOOL_NAMES
