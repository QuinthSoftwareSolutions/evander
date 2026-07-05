from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from evander.addons import AddonLoaderConfig, load_addons
from evander.workflow.catalog import (
    WorkflowEditorNodeCatalogEntry,
    WorkflowInternalEventCatalogEntry,
)
from evander.workflow.contracts import WorkflowNodeEnvelope

from evander.effects.providers.base import EffectProviderBase
from evander.gates.providers.base import GateProviderBase
from evander.queries.providers.base import QueryProviderBase
from evander.triggers.providers.base import TriggerProviderBase


class ProviderPluginContract(Protocol):
    """Contract for workflow provider plugins."""

    plugin_name: str

    def register_providers(self) -> "ProviderPluginRegistration": ...


@dataclass(frozen=True)
class ProviderPluginRegistration:
    trigger_providers: Mapping[str, type[TriggerProviderBase]] = field(default_factory=dict)
    gate_providers: Mapping[str, type[GateProviderBase]] = field(default_factory=dict)
    effect_providers: Mapping[str, type[EffectProviderBase]] = field(default_factory=dict)
    query_providers: Mapping[str, type[QueryProviderBase]] = field(default_factory=dict)
    node_definition_resolvers: Mapping[str, Callable[[WorkflowNodeEnvelope], BaseModel]] = field(
        default_factory=dict
    )
    workflow_editor_catalog_entries: Sequence[WorkflowEditorNodeCatalogEntry] = field(
        default_factory=tuple
    )
    internal_event_catalog_entries: Sequence[WorkflowInternalEventCatalogEntry] = field(
        default_factory=tuple
    )


@dataclass(frozen=True)
class LoadedProviderCatalog:
    trigger_providers: Mapping[str, type[TriggerProviderBase]]
    gate_providers: Mapping[str, type[GateProviderBase]]
    effect_providers: Mapping[str, type[EffectProviderBase]]
    query_providers: Mapping[str, type[QueryProviderBase]]
    node_definition_resolvers: Mapping[str, Callable[[WorkflowNodeEnvelope], BaseModel]]
    workflow_editor_catalog_entries: tuple[WorkflowEditorNodeCatalogEntry, ...]
    internal_event_catalog_entries: tuple[WorkflowInternalEventCatalogEntry, ...]
    loaded_plugins: tuple[str, ...]


def load_provider_plugins(
    *,
    module_names: Sequence[str] | None = None,
    include_entry_points: bool = True,
    include_builtin: bool = False,
    addon_loader: AddonLoaderConfig | Mapping[str, Any] | None = None,
    addon_base_dir: str | Path | None = None,
) -> LoadedProviderCatalog:
    trigger_providers: dict[str, type[TriggerProviderBase]] = {}
    gate_providers: dict[str, type[GateProviderBase]] = {}
    effect_providers: dict[str, type[EffectProviderBase]] = {}
    query_providers: dict[str, type[QueryProviderBase]] = {}
    node_definition_resolvers: dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]] = {}
    workflow_editor_catalog_entries: dict[str, WorkflowEditorNodeCatalogEntry] = {}
    internal_event_catalog_entries: dict[str, WorkflowInternalEventCatalogEntry] = {}
    loaded_plugins: list[str] = []

    registration_specs: list[tuple[str, Any]] = []
    if include_builtin:
        registration_specs.append(
            (
                "evander.providers.builtin",
                _load_registration_from_module("evander.providers.builtin"),
            )
        )

    for module_name in module_names or ():
        normalized = str(module_name).strip()
        if not normalized or normalized == "evander.providers.builtin":
            continue
        registration_specs.append((normalized, _load_registration_from_module(normalized)))

    if include_entry_points:
        for discovered in _iter_provider_entry_points():
            registration_specs.append(
                (f"entry_point:{discovered.value}", _load_registration_from_entry_point(discovered))
            )

    if addon_loader is not None:
        addon_catalog = load_addons(addon_loader, base_dir=addon_base_dir)
        for addon in addon_catalog.registrations:
            if addon.workflow_providers is None:
                continue
            registration_specs.append((f"addon:{addon.addon_id}", addon.workflow_providers))

    for source, raw_registration in registration_specs:
        registration = _coerce_registration(source=source, value=raw_registration)
        trigger_providers.update(registration.trigger_providers)
        gate_providers.update(registration.gate_providers)
        effect_providers.update(registration.effect_providers)
        query_providers.update(registration.query_providers)
        node_definition_resolvers.update(registration.node_definition_resolvers)
        for entry in registration.workflow_editor_catalog_entries:
            if entry.type in workflow_editor_catalog_entries:
                raise ValueError(
                    f"Duplicate workflow editor catalog entry for {entry.type!r} from {source}"
                )
            workflow_editor_catalog_entries[entry.type] = entry
        for entry in registration.internal_event_catalog_entries:
            event_type = str(entry.event_type or "").strip()
            if event_type in internal_event_catalog_entries:
                raise ValueError(
                    f"Duplicate internal event catalog entry for {event_type!r} from {source}"
                )
            internal_event_catalog_entries[event_type] = entry
        loaded_plugins.append(source)

    return LoadedProviderCatalog(
        trigger_providers=trigger_providers,
        gate_providers=gate_providers,
        effect_providers=effect_providers,
        query_providers=query_providers,
        node_definition_resolvers=node_definition_resolvers,
        workflow_editor_catalog_entries=tuple(workflow_editor_catalog_entries.values()),
        internal_event_catalog_entries=tuple(internal_event_catalog_entries.values()),
        loaded_plugins=tuple(loaded_plugins),
    )


def _iter_provider_entry_points() -> tuple[EntryPoint, ...]:
    discovered = entry_points(group="evander.providers")
    return tuple(sorted(discovered, key=lambda item: item.name))


def _load_registration_from_entry_point(provider_entry_point: EntryPoint) -> Any:
    return provider_entry_point.load()


def _load_registration_from_module(module_name: str) -> Any:
    module = import_module(module_name)
    register = getattr(module, "register_providers", None)
    if callable(register):
        return register()
    return module


def _coerce_registration(*, source: str, value: Any) -> ProviderPluginRegistration:
    if callable(value):
        value = value()
    if isinstance(value, ProviderPluginRegistration):
        return _build_registration(
            source=source,
            trigger_providers=value.trigger_providers,
            gate_providers=value.gate_providers,
            effect_providers=value.effect_providers,
            query_providers=value.query_providers,
            node_definition_resolvers=value.node_definition_resolvers,
            workflow_editor_catalog_entries=value.workflow_editor_catalog_entries,
            internal_event_catalog_entries=value.internal_event_catalog_entries,
        )
    register = getattr(value, "register_providers", None)
    if callable(register):
        candidate = register()
        if isinstance(candidate, ProviderPluginRegistration):
            return _build_registration(
                source=source,
                trigger_providers=candidate.trigger_providers,
                gate_providers=candidate.gate_providers,
                effect_providers=candidate.effect_providers,
                query_providers=candidate.query_providers,
                node_definition_resolvers=candidate.node_definition_resolvers,
                workflow_editor_catalog_entries=candidate.workflow_editor_catalog_entries,
                internal_event_catalog_entries=candidate.internal_event_catalog_entries,
            )
        value = candidate

    if isinstance(value, Mapping):
        return _build_registration(
            source=source,
            trigger_providers=value.get("trigger_providers"),
            gate_providers=value.get("gate_providers"),
            effect_providers=value.get("effect_providers"),
            query_providers=value.get("query_providers"),
            node_definition_resolvers=value.get("node_definition_resolvers"),
            workflow_editor_catalog_entries=value.get("workflow_editor_catalog_entries"),
            internal_event_catalog_entries=value.get("internal_event_catalog_entries"),
        )

    return _build_registration(
        source=source,
        trigger_providers=getattr(value, "trigger_providers", None),
        gate_providers=getattr(value, "gate_providers", None),
        effect_providers=getattr(value, "effect_providers", None),
        query_providers=getattr(value, "query_providers", None),
        node_definition_resolvers=getattr(value, "node_definition_resolvers", None),
        workflow_editor_catalog_entries=getattr(value, "workflow_editor_catalog_entries", None),
        internal_event_catalog_entries=getattr(value, "internal_event_catalog_entries", None),
    )


def _build_registration(
    *,
    source: str,
    trigger_providers: Mapping[str, type[Any]] | None,
    gate_providers: Mapping[str, type[Any]] | None,
    effect_providers: Mapping[str, type[Any]] | None,
    query_providers: Mapping[str, type[Any]] | None,
    node_definition_resolvers: Mapping[str, Callable[[WorkflowNodeEnvelope], BaseModel]] | None,
    workflow_editor_catalog_entries: Sequence[Any] | None,
    internal_event_catalog_entries: Sequence[Any] | None,
) -> ProviderPluginRegistration:
    normalized_trigger_providers = _coerce_provider_mapping(
        source=source, mapping=trigger_providers
    )
    normalized_gate_providers = _coerce_provider_mapping(source=source, mapping=gate_providers)
    normalized_effect_providers = _coerce_provider_mapping(
        source=source, mapping=effect_providers
    )
    normalized_query_providers = _coerce_provider_mapping(source=source, mapping=query_providers)
    normalized_node_definition_resolvers = _coerce_node_definition_resolvers(
        source=source, mapping=node_definition_resolvers
    )
    normalized_catalog_entries = _coerce_workflow_editor_catalog_entries(
        source=source,
        entries=workflow_editor_catalog_entries,
        trigger_providers=normalized_trigger_providers,
        gate_providers=normalized_gate_providers,
        effect_providers=normalized_effect_providers,
        query_providers=normalized_query_providers,
    )
    normalized_internal_event_catalog_entries = _coerce_internal_event_catalog_entries(
        source=source,
        entries=internal_event_catalog_entries,
    )
    return ProviderPluginRegistration(
        trigger_providers=normalized_trigger_providers,
        gate_providers=normalized_gate_providers,
        effect_providers=normalized_effect_providers,
        query_providers=normalized_query_providers,
        node_definition_resolvers=normalized_node_definition_resolvers,
        workflow_editor_catalog_entries=normalized_catalog_entries,
        internal_event_catalog_entries=normalized_internal_event_catalog_entries,
    )


def _coerce_workflow_editor_catalog_entries(
    *,
    source: str,
    entries: Sequence[Any] | None,
    trigger_providers: Mapping[str, type[Any]],
    gate_providers: Mapping[str, type[Any]],
    effect_providers: Mapping[str, type[Any]],
    query_providers: Mapping[str, type[Any]],
) -> tuple[WorkflowEditorNodeCatalogEntry, ...]:
    if entries is None:
        return ()
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise TypeError(f"Workflow editor catalog entries from {source} must be a sequence")
    provider_maps = {
        "trigger": trigger_providers,
        "gate": gate_providers,
        "effect": effect_providers,
        "query": query_providers,
    }
    seen: set[str] = set()
    normalized: list[WorkflowEditorNodeCatalogEntry] = []
    for raw_entry in entries:
        entry = (
            raw_entry
            if isinstance(raw_entry, WorkflowEditorNodeCatalogEntry)
            else WorkflowEditorNodeCatalogEntry.model_validate(raw_entry)
        )
        node_type = str(entry.type or "").strip()
        if not node_type:
            raise ValueError(
                f"Workflow editor catalog entry from {source} contains an empty node type"
            )
        if node_type in seen:
            raise ValueError(
                f"Duplicate workflow editor catalog entry for {node_type!r} from {source}"
            )
        seen.add(node_type)
        providers = provider_maps[entry.kind]
        if node_type not in providers:
            raise ValueError(
                f"Workflow editor catalog entry {node_type!r} from {source} "
                f"does not match a registered {entry.kind} provider"
            )
        normalized.append(entry.model_copy(update={"type": node_type}))
    return tuple(normalized)


def _coerce_internal_event_catalog_entries(
    *, source: str, entries: Sequence[Any] | None
) -> tuple[WorkflowInternalEventCatalogEntry, ...]:
    if entries is None:
        return ()
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise TypeError(f"Internal event catalog entries from {source} must be a sequence")
    seen: set[str] = set()
    normalized: list[WorkflowInternalEventCatalogEntry] = []
    for raw_entry in entries:
        entry = (
            raw_entry
            if isinstance(raw_entry, WorkflowInternalEventCatalogEntry)
            else WorkflowInternalEventCatalogEntry.model_validate(raw_entry)
        )
        event_type = str(entry.event_type or "").strip()
        if not event_type:
            raise ValueError(
                f"Internal event catalog entry from {source} contains an empty event_type"
            )
        if event_type in seen:
            raise ValueError(
                f"Duplicate internal event catalog entry for {event_type!r} from {source}"
            )
        seen.add(event_type)
        normalized.append(entry.model_copy(update={"event_type": event_type}))
    return tuple(normalized)


def _coerce_node_definition_resolvers(
    *,
    source: str,
    mapping: Mapping[str, Callable[[WorkflowNodeEnvelope], BaseModel]] | None,
) -> Mapping[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    if mapping is None:
        return {}
    if not isinstance(mapping, Mapping):
        raise TypeError(f"Node definition resolvers from {source} must be a mapping")
    normalized: dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]] = {}
    for raw_type, raw_resolver in mapping.items():
        node_type = str(raw_type).strip()
        if not node_type:
            raise ValueError(
                f"Node resolver registration from {source} contains an empty node type key"
            )
        if not callable(raw_resolver):
            raise TypeError(f"Node resolver for {node_type!r} from {source} must be callable")
        normalized[node_type] = raw_resolver
    return normalized


def _coerce_provider_mapping(
    *,
    source: str,
    mapping: Mapping[str, type[Any]] | None,
) -> Mapping[str, type[Any]]:
    if mapping is None:
        return {}
    if not isinstance(mapping, Mapping):
        raise TypeError(f"Provider registration from {source} must expose mapping values")
    normalized: dict[str, type[Any]] = {}
    for raw_type, raw_provider in mapping.items():
        node_type = str(raw_type).strip()
        if not node_type:
            raise ValueError(f"Provider registration from {source} contains an empty node type key")
        if not isinstance(raw_provider, type):
            raise TypeError(f"Provider {node_type!r} from {source} must be a class")
        normalized[node_type] = raw_provider
    return normalized


_DEFAULT_PROVIDER_CATALOG = load_provider_plugins(
    module_names=(),
    include_entry_points=False,
    include_builtin=True,
)

TRIGGER_PLUGIN_CLASSES: Mapping[str, type[TriggerProviderBase]] = (
    _DEFAULT_PROVIDER_CATALOG.trigger_providers
)
GATE_PLUGIN_CLASSES: Mapping[str, type[GateProviderBase]] = _DEFAULT_PROVIDER_CATALOG.gate_providers
EFFECT_PLUGIN_CLASSES: Mapping[str, type[EffectProviderBase]] = (
    _DEFAULT_PROVIDER_CATALOG.effect_providers
)
QUERY_PLUGIN_CLASSES: Mapping[str, type[QueryProviderBase]] = (
    _DEFAULT_PROVIDER_CATALOG.query_providers
)


NODE_DEFINITION_RESOLVERS: Mapping[str, Callable[[WorkflowNodeEnvelope], BaseModel]] = (
    _DEFAULT_PROVIDER_CATALOG.node_definition_resolvers
)
