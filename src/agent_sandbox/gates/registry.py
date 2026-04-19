from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from agent_sandbox.admin_service import AdminService
from agent_sandbox.events.models import GateType
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.provider_runtime.adapters import GateProviderAdapter, gate_provider_config_blob
from agent_sandbox.workflow_plugins import GATE_PLUGIN_CLASSES

from .providers.base import GateProviderBase, GateRuntimeContext

GateProviderClass = type[GateProviderBase]

DEFAULT_GATE_PROVIDER_REGISTRY: dict[GateType, GateProviderClass] = dict(GATE_PLUGIN_CLASSES)


class GateProviderRegistry:
    def __init__(self, initial: Mapping[GateType, GateProviderClass] | None = None) -> None:
        self._providers: dict[GateType, GateProviderClass] = dict(initial or DEFAULT_GATE_PROVIDER_REGISTRY)

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
        admin_service: AdminService,
        *,
        registry: GateProviderRegistry | None = None,
        env: Mapping[str, str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider_config_resolver = provider_config_resolver
        self.admin_service = admin_service
        self._gate_adapter = GateProviderAdapter(admin_service)
        self.registry = registry or GateProviderRegistry()
        self.env = env or dict(os.environ)
        self.secret_resolver = secret_resolver

    def build(self, gate_type: GateType, *, directory_path: str | None = None) -> GateProviderBase:
        provider_class = self.registry.resolve(gate_type)
        resolved_config = self.provider_config_resolver.resolve_for_directory(directory_path)
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
