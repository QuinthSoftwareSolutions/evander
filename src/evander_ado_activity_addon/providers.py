from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import WorkflowNodeEnvelope
from evander.queries.providers.base import QueryProviderBase
from evander.workflow.catalog import (
    WorkflowEditorFieldDefinition,
    WorkflowEditorFieldOption,
    WorkflowEditorNodeCatalogEntry,
)
from evander.workflow.plugins import ProviderPluginRegistration

from .models import AdoActivityQueryDefinition, AdoActivitySyncEffectDefinition
from .service import AdoActivityIndexService


def register_providers() -> ProviderPluginRegistration:
    return ProviderPluginRegistration(
        effect_providers={
            "effect:ado:sync-activity-index": AdoActivitySyncEffectProvider,
        },
        query_providers={
            "query:ado:activity": AdoActivityQueryProvider,
        },
        node_definition_resolvers={
            "query:ado:activity": lambda node: _materialize(AdoActivityQueryDefinition, node),
            "effect:ado:sync-activity-index": lambda node: _materialize(
                AdoActivitySyncEffectDefinition,
                node,
            ),
        },
        workflow_editor_catalog_entries=_CATALOG,
    )


class _AdoActivityProviderMixin:
    def _runtime_context(self) -> Any:
        runtime_context = getattr(self, "runtime_context", None)
        if runtime_context is None:
            raise RuntimeError("ADO activity provider requires runtime context")
        return runtime_context

    def _payload(self, resolved_config: Mapping[str, Any] | None) -> dict[str, Any]:
        definition = getattr(self, "effect_definition", None) or getattr(
            self,
            "query_definition",
            None,
        )
        payload = _definition_payload(definition)
        if resolved_config:
            payload.update(dict(resolved_config))
        return payload

    def _service(self) -> AdoActivityIndexService:
        runtime_context = self._runtime_context()
        return AdoActivityIndexService(
            addon_runtime_dir=_addon_runtime_dir(runtime_context),
            provider_config_resolver=getattr(runtime_context, "provider_config_resolver", None),
        )

    def _directory_path(self, payload: Mapping[str, Any]) -> str:
        directory_path = str(payload.get("directory_path") or "").strip()
        if directory_path:
            return directory_path
        metadata = getattr(self._runtime_context(), "metadata", {})
        if isinstance(metadata, Mapping):
            directory_path = str(metadata.get("directory_path") or "").strip()
        return directory_path or "root"


class AdoActivityQueryProvider(_AdoActivityProviderMixin, QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._service().query(
            since=_required(payload, "since"),
            until=payload.get("until"),
            actor=_optional(payload.get("actor")),
            activity_kinds=_strings(payload.get("activity_kinds")),
            directory_path=self._directory_path(payload),
            project=_optional(payload.get("project")),
            repo=_optional(payload.get("repo")),
            work_item_id=_optional(payload.get("work_item_id")),
            pr_id=_optional(payload.get("pr_id")),
            top=int(payload.get("top") or 100),
            cursor=_optional(payload.get("cursor")),
            include_raw=bool(payload.get("include_raw") or False),
        )
        return {"result": result, **result}


class AdoActivitySyncEffectProvider(_AdoActivityProviderMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        result = self._service().sync(
            since=_required(payload, "since"),
            until=payload.get("until"),
            directory_path=self._directory_path(payload),
            scope=str(payload.get("scope") or "all"),
            actor=_optional(payload.get("actor")),
            project=_optional(payload.get("project")),
            repo=_optional(payload.get("repo")),
            states=_strings(payload.get("states")),
            types=_strings(payload.get("types")),
            tags_any=_strings(payload.get("tags_any")),
            statuses=_strings(payload.get("statuses")),
            include_system_comments=bool(payload.get("include_system_comments") or False),
        )
        return {"result": result, **result}


def _materialize(model_class: type[Any], node: WorkflowNodeEnvelope) -> Any:
    return model_class.model_validate({"type": node.type, **dict(node.config)})


def _definition_payload(definition: Any) -> dict[str, Any]:
    if definition is None:
        return {}
    payload: dict[str, Any] = {}
    for key, value in _model_mapping(definition).items():
        if key not in {"type", "outputs"} and value is not None:
            payload[key] = value
    config = getattr(definition, "config", None)
    if isinstance(config, Mapping):
        payload.update(dict(config))
    return payload


def _model_mapping(value: Any) -> dict[str, Any]:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python")
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _addon_runtime_dir(runtime_context: Any) -> Path:
    provider_config = getattr(runtime_context, "provider_config", {})
    runtime_dir = None
    if isinstance(provider_config, Mapping):
        runtime_dir = provider_config.get("runtime_dir")
    if runtime_dir is None:
        resolved_config = getattr(runtime_context, "resolved_config", None)
        runtime_dir = getattr(resolved_config, "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("ADO activity provider requires runtime_dir")
    return Path(runtime_dir) / "addons" / "ado-activity-addon"


def _required(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{key} is required")
    return value


def _optional(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _field(
    key: str,
    label: str,
    kind: str = "text",
    *,
    required: bool = False,
    bindable: bool = True,
    multiline: bool = False,
    default_value: Any = None,
    options: list[WorkflowEditorFieldOption] | None = None,
) -> WorkflowEditorFieldDefinition:
    return WorkflowEditorFieldDefinition(
        key=key,
        label=label,
        kind=kind,
        required=required,
        bindable=bindable,
        multiline=multiline,
        default_value=default_value,
        options=options or [],
    )


_SCOPE_OPTIONS = [
    WorkflowEditorFieldOption(value="all", label="All"),
    WorkflowEditorFieldOption(value="work_items", label="Work items"),
    WorkflowEditorFieldOption(value="pr_comments", label="PR comments"),
    WorkflowEditorFieldOption(value="pr_commits", label="PR commits"),
]

_CATALOG = [
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:ado:activity",
        label="ADO activity index query",
        description="Read Azure DevOps activity from the durable add-on index.",
        fields=[
            _field("since", "Since", required=True),
            _field("until", "Until"),
            _field("directory_path", "Directory path"),
            _field("actor", "Actor"),
            _field("activity_kinds", "Activity kinds", "json", multiline=True),
            _field("project", "Project"),
            _field("repo", "Repository"),
            _field("work_item_id", "Work item ID"),
            _field("pr_id", "PR ID"),
            _field("top", "Top", "number", default_value=100),
            _field("cursor", "Cursor"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:sync-activity-index",
        label="Sync ADO activity index",
        description="Backfill Azure DevOps activity into the durable add-on index.",
        fields=[
            _field("since", "Since", required=True),
            _field("until", "Until"),
            _field("directory_path", "Directory path"),
            _field("scope", "Scope", "select", default_value="all", options=_SCOPE_OPTIONS),
            _field("actor", "Actor"),
            _field("project", "Project"),
            _field("repo", "Repository"),
            _field("states", "Work item states", "json", multiline=True),
            _field("types", "Work item types", "json", multiline=True),
            _field("tags_any", "Work item tags", "json", multiline=True),
            _field("statuses", "PR statuses", "json", multiline=True),
        ],
    ),
]
