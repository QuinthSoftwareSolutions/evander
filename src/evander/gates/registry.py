from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from evander.events.models import GateType
from evander.provider_config import GateProviderConfig, ProviderConfigResolver
from evander.provider_runtime.adapters import (
    build_provider_runtime_ports,
    gate_provider_config_blob,
)
from evander.workflow.plugins import GATE_PLUGIN_CLASSES

from .providers.base import GateProviderBase, GateRuntimeContext

GateProviderClass = type[GateProviderBase]

DEFAULT_GATE_PROVIDER_REGISTRY: dict[GateType, GateProviderClass] = dict(GATE_PLUGIN_CLASSES)


class GateProviderRegistry:
    def __init__(self, initial: Mapping[GateType, GateProviderClass] | None = None) -> None:
        self._providers: dict[GateType, GateProviderClass] = dict(
            DEFAULT_GATE_PROVIDER_REGISTRY if initial is None else initial
        )

    def register(self, gate_type: GateType, provider_class: GateProviderClass) -> None:
        self._providers[gate_type] = provider_class

    def resolve(self, gate_type: GateType) -> GateProviderClass:
        provider_class = self._providers.get(gate_type)
        if provider_class is None:
            raise KeyError(f"No gate provider registered for type {gate_type!r}")
        return provider_class

    def registered_types(self) -> tuple[GateType, ...]:
        return tuple(self._providers.keys())


class GateProviderFactory:
    def __init__(
        self,
        provider_config_resolver: ProviderConfigResolver,
        admin_service: object | None = None,
        *,
        registry: GateProviderRegistry | None = None,
        provider_runtime: object | None = None,
        admin_adapter: object | None = None,
        gate_adapter: object | None = None,
        env: Mapping[str, str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider_config_resolver = provider_config_resolver
        self._provider_runtime_ports = build_provider_runtime_ports(
            provider_runtime=provider_runtime,
            admin_service=admin_service,
            admin_adapter=admin_adapter,
            gate_adapter=gate_adapter,
        )
        self._gate_adapter = (
            self._provider_runtime_ports.gate_vm_inventory
            or self._provider_runtime_ports.vm_inventory
        )
        self.admin_service = (
            self._provider_runtime_ports.admin_service
            or admin_service
            or admin_adapter
            or provider_runtime
        )
        self.registry = registry or GateProviderRegistry()
        self.env = env or dict(os.environ)
        self.secret_resolver = secret_resolver

    def build(self, gate_type: GateType, *, directory_path: str | None = None) -> GateProviderBase:
        provider_class = self.registry.resolve(gate_type)
        resolved_config = _resolve_gate_provider_config(
            self.provider_config_resolver, directory_path
        )
        runtime_context = GateRuntimeContext(
            provider_config=gate_provider_config_blob(resolved_config),
            vm_inventory=self._gate_adapter,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata={"directory_path": directory_path or "root"},
        )
        provider = provider_class()
        provider.initialize(runtime_context=runtime_context)
        return provider


def _resolve_gate_provider_config(
    provider_config_resolver: ProviderConfigResolver,
    directory_path: str | None,
) -> GateProviderConfig:
    resolve_provider_config = getattr(
        provider_config_resolver, "resolve_gate_provider_config", None
    )
    if callable(resolve_provider_config):
        return resolve_provider_config(directory_path)
    resolve_for_directory = getattr(provider_config_resolver, "resolve_for_directory", None)
    if callable(resolve_for_directory):
        return GateProviderConfig.model_validate(_model_dump(resolve_for_directory(directory_path)))
    raise AttributeError("provider_config_resolver must resolve gate provider config")


def _model_dump(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value
