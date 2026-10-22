from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from evander.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerType,
    WorkflowNodeEnvelope,
)
from evander.provider_config import ProviderConfigResolver, TriggerProviderConfig
from evander.provider_runtime.adapters import (
    build_provider_runtime_ports,
    trigger_provider_config_blob,
)
from evander.triggers.providers.base import TriggerProviderBase, TriggerRuntimeContext
from evander.workflow.plugins import TRIGGER_PLUGIN_CLASSES

TriggerProviderClass = type[TriggerProviderBase]


class NoopTriggerProvider(TriggerProviderBase):
    """Default provider scaffold for trigger types awaiting concrete implementations."""


DEFAULT_TRIGGER_PROVIDER_REGISTRY: dict[TriggerType, TriggerProviderClass] = dict(
    TRIGGER_PLUGIN_CLASSES
)


class TriggerProviderRegistry:
    def __init__(
        self,
        initial: Mapping[TriggerType, TriggerProviderClass] | None = None,
        *,
        defaults_when_empty: bool = True,
    ) -> None:
        if initial is None or (defaults_when_empty and not initial):
            initial = DEFAULT_TRIGGER_PROVIDER_REGISTRY
        self._providers: dict[TriggerType, TriggerProviderClass] = dict(initial)

    def register(self, trigger_type: TriggerType, provider_class: TriggerProviderClass) -> None:
        self._providers[trigger_type] = provider_class

    def resolve(self, trigger_type: TriggerType) -> TriggerProviderClass:
        provider_class = self._providers.get(trigger_type)
        if provider_class is None:
            raise KeyError(f"No trigger provider registered for type {trigger_type!r}")
        return provider_class

    def registered_types(self) -> tuple[TriggerType, ...]:
        return tuple(self._providers.keys())


class TriggerProviderFactory:
    """Centralized provider constructor to keep env/secret handling host-side."""

    def __init__(
        self,
        provider_config_resolver: ProviderConfigResolver,
        *,
        registry: TriggerProviderRegistry | None = None,
        provider_runtime: object | None = None,
        admin_adapter: object | None = None,
        gate_adapter: object | None = None,
        admin_service: object | None = None,
        env: Mapping[str, str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.provider_config_resolver = provider_config_resolver
        self.registry = registry or TriggerProviderRegistry()
        self._provider_runtime_ports = build_provider_runtime_ports(
            provider_runtime=provider_runtime,
            admin_service=admin_service,
            admin_adapter=admin_adapter,
            gate_adapter=gate_adapter,
        )
        self.admin_service = (
            self._provider_runtime_ports.admin_service
            or admin_service
            or admin_adapter
            or provider_runtime
        )
        self.env = env or dict(os.environ)
        self.secret_resolver = secret_resolver
        self.metadata = dict(metadata or {})

    def build(
        self,
        trigger_definition: TriggerDefinitionDiscriminated,
        *,
        directory_path: str | None = None,
    ) -> TriggerProviderBase:
        provider_class = self.registry.resolve(trigger_definition.type)
        resolved_config = _resolve_trigger_provider_config(
            self.provider_config_resolver, directory_path
        )
        runtime_context = TriggerRuntimeContext(
            provider_config=trigger_provider_config_blob(resolved_config),
            provider_config_resolver=self.provider_config_resolver,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata={
                **self.metadata,
                "directory_path": directory_path or "root",
                "admin_service": self.admin_service,
                "vm_inventory": self._provider_runtime_ports.gate_vm_inventory
                or self._provider_runtime_ports.vm_inventory,
            },
        )
        provider = provider_class()
        validated_definition = _validate_trigger_definition(provider_class, trigger_definition)
        provider.initialize(
            trigger_definition=validated_definition, runtime_context=runtime_context
        )
        return provider


def _validate_trigger_definition(
    provider_class: TriggerProviderClass,
    trigger_definition: TriggerDefinitionDiscriminated,
) -> TriggerDefinitionDiscriminated:
    if type(trigger_definition) is WorkflowNodeEnvelope:
        return provider_class.validate_definition(trigger_definition)
    return trigger_definition


def _resolve_trigger_provider_config(
    provider_config_resolver: ProviderConfigResolver,
    directory_path: str | None,
) -> TriggerProviderConfig:
    resolve_provider_config = getattr(
        provider_config_resolver, "resolve_trigger_provider_config", None
    )
    if callable(resolve_provider_config):
        return resolve_provider_config(directory_path)
    resolve_for_directory = getattr(provider_config_resolver, "resolve_for_directory", None)
    if callable(resolve_for_directory):
        return TriggerProviderConfig.model_validate(
            _model_dump(resolve_for_directory(directory_path))
        )
    raise AttributeError("provider_config_resolver must resolve trigger provider config")


def _model_dump(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value
