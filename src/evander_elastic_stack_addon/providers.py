from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import WorkflowNodeEnvelope
from evander.queries.providers.base import QueryProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.plugins import ProviderPluginRegistration

from .models import (
    ElasticsearchClusterHealthQueryDefinition,
    ElasticsearchIndicesQueryDefinition,
    ElasticsearchMappingsQueryDefinition,
    KibanaDataViewsQueryDefinition,
    KibanaImportSavedObjectsEffectDefinition,
    KibanaSavedObjectsQueryDefinition,
    KibanaSetDefaultDataViewEffectDefinition,
    KibanaStatusQueryDefinition,
    KibanaUpsertDataViewEffectDefinition,
)
from .service import client_for_provider_context


_DEFINITIONS: dict[str, type[Any]] = {
    "query:elasticsearch:cluster-health": ElasticsearchClusterHealthQueryDefinition,
    "query:elasticsearch:indices": ElasticsearchIndicesQueryDefinition,
    "query:elasticsearch:mappings": ElasticsearchMappingsQueryDefinition,
    "query:kibana:status": KibanaStatusQueryDefinition,
    "query:kibana:data-views": KibanaDataViewsQueryDefinition,
    "query:kibana:saved-objects": KibanaSavedObjectsQueryDefinition,
    "effect:kibana:upsert-data-view": KibanaUpsertDataViewEffectDefinition,
    "effect:kibana:import-saved-objects": KibanaImportSavedObjectsEffectDefinition,
    "effect:kibana:set-default-data-view": KibanaSetDefaultDataViewEffectDefinition,
}


def register_providers() -> ProviderPluginRegistration:
    return ProviderPluginRegistration(
        query_providers={
            "query:elasticsearch:cluster-health": ElasticsearchClusterHealthQueryProvider,
            "query:elasticsearch:indices": ElasticsearchIndicesQueryProvider,
            "query:elasticsearch:mappings": ElasticsearchMappingsQueryProvider,
            "query:kibana:status": KibanaStatusQueryProvider,
            "query:kibana:data-views": KibanaDataViewsQueryProvider,
            "query:kibana:saved-objects": KibanaSavedObjectsQueryProvider,
        },
        effect_providers={
            "effect:kibana:upsert-data-view": KibanaUpsertDataViewEffectProvider,
            "effect:kibana:import-saved-objects": KibanaImportSavedObjectsEffectProvider,
            "effect:kibana:set-default-data-view": KibanaSetDefaultDataViewEffectProvider,
        },
        node_definition_resolvers={
            node_type: lambda node, model=model: _materialize(model, node)
            for node_type, model in _DEFINITIONS.items()
        },
        workflow_editor_catalog_entries=_CATALOG,
    )


class _ElasticStackProviderMixin:
    def _context(self) -> Any:
        context = getattr(self, "runtime_context", None)
        if context is None:
            raise RuntimeError("Elastic Stack provider requires runtime context")
        return context

    def _payload(self, resolved_config: Mapping[str, Any] | None) -> dict[str, Any]:
        definition = getattr(self, "effect_definition", None) or getattr(
            self, "query_definition", None
        )
        payload = _definition_payload(definition)
        if resolved_config:
            payload.update(dict(resolved_config))
        return payload

    def _client(self, payload: Mapping[str, Any]):
        return client_for_provider_context(
            self._context(),
            session_id=_optional(payload.get("session_id")),
        )


class ElasticsearchClusterHealthQueryProvider(_ElasticStackProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).elasticsearch_cluster_health(_required(payload, "target_id"))
        return {"result": result, **result}


class ElasticsearchIndicesQueryProvider(_ElasticStackProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        items = self._client(payload).elasticsearch_list_indices(
            _required(payload, "target_id"),
            index_pattern=str(payload.get("index_pattern") or "*"),
            include_hidden=bool(payload.get("include_hidden") or False),
        )
        return {"result": items, "items": items, "count": len(items)}


class ElasticsearchMappingsQueryProvider(_ElasticStackProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).elasticsearch_get_mappings(
            _required(payload, "target_id"),
            index_pattern=_required(payload, "index_pattern"),
        )
        return {"result": result, "indices": result, "count": len(result)}


class KibanaStatusQueryProvider(_ElasticStackProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).kibana_status(_required(payload, "target_id"))
        return {"result": result, **result}


class KibanaDataViewsQueryProvider(_ElasticStackProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).kibana_list_data_views(
            _required(payload, "target_id"),
            space_id=_optional(payload.get("space_id")),
        )
        items = result.get("data_view", []) if isinstance(result, dict) else []
        return {"result": result, "items": items, "count": len(items)}


class KibanaSavedObjectsQueryProvider(_ElasticStackProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).kibana_find_saved_objects(
            _required(payload, "target_id"),
            types=_strings(payload.get("types")),
            search=_optional(payload.get("search")),
            page=int(payload.get("page") or 1),
            per_page=int(payload.get("per_page") or 100),
            space_id=_optional(payload.get("space_id")),
        )
        items = result.get("saved_objects", []) if isinstance(result, dict) else []
        return {"result": result, "items": items, "count": len(items)}


class KibanaUpsertDataViewEffectProvider(_ElasticStackProviderMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).kibana_upsert_data_view(
            _required(payload, "target_id"),
            data_view=_object(payload.get("data_view"), "data_view"),
            override=bool(payload.get("override", True)),
            space_id=_optional(payload.get("space_id")),
        )
        return {"result": result, **result}


class KibanaImportSavedObjectsEffectProvider(_ElasticStackProviderMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).kibana_import_saved_objects(
            _required(payload, "target_id"),
            ndjson=_required(payload, "ndjson"),
            overwrite=bool(payload.get("overwrite", True)),
            create_new_copies=bool(payload.get("create_new_copies", False)),
            compatibility_mode=bool(payload.get("compatibility_mode", False)),
            space_id=_optional(payload.get("space_id")),
        )
        return {"result": result, **result}


class KibanaSetDefaultDataViewEffectProvider(_ElasticStackProviderMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._client(payload).kibana_set_default_data_view(
            _required(payload, "target_id"),
            data_view_id=_optional(payload.get("data_view_id")),
            force=bool(payload.get("force", True)),
            space_id=_optional(payload.get("space_id")),
        )
        return {"result": result, **result}


def _materialize(model_class: type[Any], node: WorkflowNodeEnvelope) -> Any:
    return model_class.model_validate({"type": node.type, **dict(node.config)})


def _definition_payload(definition: Any) -> dict[str, Any]:
    if definition is None:
        return {}
    model_dump = getattr(definition, "model_dump", None)
    payload = model_dump(mode="python") if callable(model_dump) else {}
    payload.pop("type", None)
    payload.pop("outputs", None)
    config = getattr(definition, "config", None)
    if isinstance(config, Mapping):
        payload.update(dict(config))
    return {key: value for key, value in payload.items() if value is not None}


def _required(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _field(
    key: str,
    label: str,
    kind: str = "text",
    *,
    required: bool = False,
    multiline: bool = False,
    default_value: Any = None,
) -> WorkflowEditorFieldDefinition:
    return WorkflowEditorFieldDefinition(
        key=key,
        label=label,
        kind=kind,
        required=required,
        bindable=True,
        multiline=multiline,
        default_value=default_value,
    )


_TARGET_FIELDS = [
    _field("target_id", "Target ID", required=True),
    _field("session_id", "Session ID"),
    _field("space_id", "Kibana space ID"),
]

_CATALOG = [
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:elasticsearch:cluster-health",
        label="Elasticsearch cluster health",
        description="Read health from an approved Elastic Stack target.",
        fields=_TARGET_FIELDS[:2],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:elasticsearch:indices",
        label="Elasticsearch indices",
        description="List bounded index metadata from an approved Elastic Stack target.",
        fields=_TARGET_FIELDS[:2]
        + [
            _field("index_pattern", "Index pattern", default_value="*"),
            _field("include_hidden", "Include hidden indices", "checkbox", default_value=False),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:elasticsearch:mappings",
        label="Elasticsearch mappings",
        description="Read mappings for an approved Elasticsearch index pattern.",
        fields=_TARGET_FIELDS[:2] + [_field("index_pattern", "Index pattern", required=True)],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:kibana:status",
        label="Kibana status",
        description="Read Kibana status from an approved Elastic Stack target.",
        fields=_TARGET_FIELDS[:2],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:kibana:data-views",
        label="Kibana data views",
        description="List data views in an approved Kibana target and space.",
        fields=_TARGET_FIELDS,
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:kibana:saved-objects",
        label="Kibana saved objects",
        description="Find dashboards and visualizations in an approved Kibana target.",
        fields=_TARGET_FIELDS
        + [
            _field("types", "Saved object types", "json", multiline=True),
            _field("search", "Title search"),
            _field("page", "Page", "number", default_value=1),
            _field("per_page", "Per page", "number", default_value=100),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:kibana:upsert-data-view",
        label="Upsert Kibana data view",
        description="Create or replace a data view through Kibana's public API.",
        fields=_TARGET_FIELDS
        + [
            _field("data_view", "Data view", "json", required=True, multiline=True),
            _field("override", "Override matching title", "checkbox", default_value=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:kibana:import-saved-objects",
        label="Import Kibana saved objects",
        description="Import a bounded, version-compatible Kibana NDJSON bundle.",
        fields=_TARGET_FIELDS
        + [
            _field("ndjson", "Saved objects NDJSON", required=True, multiline=True),
            _field("overwrite", "Overwrite", "checkbox", default_value=True),
            _field("create_new_copies", "Create new copies", "checkbox", default_value=False),
            _field("compatibility_mode", "Compatibility mode", "checkbox", default_value=False),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:kibana:set-default-data-view",
        label="Set default Kibana data view",
        description="Set or clear the default data view in an approved Kibana target.",
        fields=_TARGET_FIELDS
        + [
            _field("data_view_id", "Data view ID"),
            _field("force", "Force update", "checkbox", default_value=True),
        ],
    ),
]
