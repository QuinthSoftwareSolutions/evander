from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol

from pydantic import BaseModel

from agent_sandbox.addons import AddonLoaderConfig, load_addons
from agent_sandbox.workflow.contracts import WorkflowNodeEnvelope

from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.queries.providers.base import QueryProviderBase
from agent_sandbox.triggers.providers.base import TriggerProviderBase


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


@dataclass(frozen=True)
class LoadedProviderCatalog:
    trigger_providers: Mapping[str, type[TriggerProviderBase]]
    gate_providers: Mapping[str, type[GateProviderBase]]
    effect_providers: Mapping[str, type[EffectProviderBase]]
    query_providers: Mapping[str, type[QueryProviderBase]]
    node_definition_resolvers: Mapping[str, Callable[[WorkflowNodeEnvelope], BaseModel]]
    loaded_plugins: tuple[str, ...]


def load_provider_plugins(
    *,
    module_names: Sequence[str] | None = None,
    include_entry_points: bool = True,
    include_builtin: bool = False,
    addon_loader: AddonLoaderConfig | Mapping[str, Any] | None = None,
) -> LoadedProviderCatalog:
    trigger_providers: dict[str, type[TriggerProviderBase]] = {}
    gate_providers: dict[str, type[GateProviderBase]] = {}
    effect_providers: dict[str, type[EffectProviderBase]] = {}
    query_providers: dict[str, type[QueryProviderBase]] = {}
    node_definition_resolvers: dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]] = {}
    loaded_plugins: list[str] = []

    registration_specs: list[tuple[str, Any]] = []
    if include_builtin:
        registration_specs.append(
            (
                "agent_sandbox.providers.builtin",
                _load_registration_from_module("agent_sandbox.providers.builtin"),
            )
        )

    for module_name in module_names or ():
        normalized = str(module_name).strip()
        if not normalized or normalized == "agent_sandbox.providers.builtin":
            continue
        registration_specs.append((normalized, _load_registration_from_module(normalized)))

    if include_entry_points:
        for discovered in _iter_provider_entry_points():
            registration_specs.append(
                (f"entry_point:{discovered.value}", _load_registration_from_entry_point(discovered))
            )

    if addon_loader is not None:
        addon_catalog = load_addons(addon_loader)
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
        loaded_plugins.append(source)

    return LoadedProviderCatalog(
        trigger_providers=trigger_providers,
        gate_providers=gate_providers,
        effect_providers=effect_providers,
        query_providers=query_providers,
        node_definition_resolvers=node_definition_resolvers,
        loaded_plugins=tuple(loaded_plugins),
    )


def _iter_provider_entry_points() -> tuple[EntryPoint, ...]:
    discovered = entry_points(group="agent_sandbox.providers")
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
        return value
    register = getattr(value, "register_providers", None)
    if callable(register):
        candidate = register()
        if isinstance(candidate, ProviderPluginRegistration):
            return candidate
        value = candidate

    if isinstance(value, Mapping):
        return ProviderPluginRegistration(
            trigger_providers=_coerce_provider_mapping(
                source=source, mapping=value.get("trigger_providers")
            ),
            gate_providers=_coerce_provider_mapping(
                source=source, mapping=value.get("gate_providers")
            ),
            effect_providers=_coerce_provider_mapping(
                source=source, mapping=value.get("effect_providers")
            ),
            query_providers=_coerce_provider_mapping(
                source=source, mapping=value.get("query_providers")
            ),
            node_definition_resolvers=_coerce_node_definition_resolvers(
                source=source, mapping=value.get("node_definition_resolvers")
            ),
        )

    return ProviderPluginRegistration(
        trigger_providers=_coerce_provider_mapping(
            source=source, mapping=getattr(value, "trigger_providers", None)
        ),
        gate_providers=_coerce_provider_mapping(
            source=source, mapping=getattr(value, "gate_providers", None)
        ),
        effect_providers=_coerce_provider_mapping(
            source=source, mapping=getattr(value, "effect_providers", None)
        ),
        query_providers=_coerce_provider_mapping(
            source=source, mapping=getattr(value, "query_providers", None)
        ),
        node_definition_resolvers=_coerce_node_definition_resolvers(
            source=source, mapping=getattr(value, "node_definition_resolvers", None)
        ),
    )


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
