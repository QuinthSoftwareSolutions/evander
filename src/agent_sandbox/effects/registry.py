from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from agent_sandbox.events.models import (
    EffectDefinitionDiscriminated,
    EffectType,
    WorkflowNodeEnvelope,
)
from agent_sandbox.provider_config import EffectProviderConfig, ProviderConfigResolver
from agent_sandbox.provider_runtime.adapters import (
    build_provider_runtime_ports,
    effect_provider_config_blob,
)
from agent_sandbox.workflow.plugins import EFFECT_PLUGIN_CLASSES

from .providers.base import EffectProviderBase, EffectRuntimeContext

EffectProviderClass = type[EffectProviderBase]


class NoopEffectProvider(EffectProviderBase):
    """Default provider scaffold for effect types awaiting concrete implementations."""


DEFAULT_EFFECT_PROVIDER_REGISTRY: dict[EffectType, EffectProviderClass] = dict(
    EFFECT_PLUGIN_CLASSES
)


class EffectProviderRegistry:
    def __init__(self, initial: Mapping[EffectType, EffectProviderClass] | None = None) -> None:
        self._providers: dict[EffectType, EffectProviderClass] = dict(
            initial or DEFAULT_EFFECT_PROVIDER_REGISTRY
        )

    def register(self, effect_type: EffectType, provider_class: EffectProviderClass) -> None:
        self._providers[effect_type] = provider_class

    def resolve(self, effect_type: EffectType) -> EffectProviderClass:
        provider_class = self._providers.get(effect_type)
        if provider_class is None:
            raise KeyError(f"No effect provider registered for type {effect_type!r}")
        return provider_class

    def registered_types(self) -> tuple[EffectType, ...]:
        return tuple(self._providers.keys())


class EffectProviderFactory:
    """Centralized provider constructor to keep env/secret handling host-side."""

    def __init__(
        self,
        provider_config_resolver: ProviderConfigResolver,
        admin_service: object | None = None,
        *,
        registry: EffectProviderRegistry | None = None,
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
        self.admin_service = (
            self._provider_runtime_ports.admin_service
            or admin_service
            or admin_adapter
            or provider_runtime
        )
        self._admin_adapter = self.admin_service
        self.registry = registry or EffectProviderRegistry()
        self.env = env or dict(os.environ)
        self.secret_resolver = secret_resolver

    def build(
        self,
        effect_definition: EffectDefinitionDiscriminated,
        *,
        directory_path: str | None = None,
    ) -> EffectProviderBase:
        provider_class = self.registry.resolve(effect_definition.type)
        resolved_config = _resolve_effect_provider_config(
            self.provider_config_resolver, directory_path
        )
        runtime_context = EffectRuntimeContext(
            provider_config=effect_provider_config_blob(resolved_config),
            vm_creator=self._provider_runtime_ports.vm_creator,
            vm_repo_cloner=self._provider_runtime_ports.vm_repo_cloner,
            vm_python_bootstrapper=self._provider_runtime_ports.vm_python_bootstrapper,
            vm_frontend_bootstrapper=self._provider_runtime_ports.vm_frontend_bootstrapper,
            agent_resumer=self._provider_runtime_ports.agent_resumer,
            vm_power_controller=self._provider_runtime_ports.vm_power_controller,
            vm_inventory=self._provider_runtime_ports.vm_inventory
            or self._provider_runtime_ports.gate_vm_inventory,
            vm_executor=self._provider_runtime_ports.vm_executor,
            vm_file_copier=self._provider_runtime_ports.vm_file_copier,
            vm_file_deleter=self._provider_runtime_ports.vm_file_deleter,
            session_lookup=self._provider_runtime_ports.session_lookup,
            issue_updater=self._provider_runtime_ports.issue_updater,
            session_metadata_updater=self._provider_runtime_ports.session_metadata_updater,
            git_branch_promoter=self._provider_runtime_ports.git_branch_promoter,
            pull_request_creator=self._provider_runtime_ports.pull_request_creator,
            admin_service=self._admin_adapter,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata={"directory_path": directory_path or "root"},
        )
        provider = provider_class()
        validated_definition = _validate_effect_definition(provider_class, effect_definition)
        provider.initialize(effect_definition=validated_definition, runtime_context=runtime_context)
        return provider


def _validate_effect_definition(
    provider_class: EffectProviderClass,
    effect_definition: EffectDefinitionDiscriminated,
) -> EffectDefinitionDiscriminated:
    if type(effect_definition) is WorkflowNodeEnvelope:
        return provider_class.validate_definition(effect_definition)
    return effect_definition


def _resolve_effect_provider_config(
    provider_config_resolver: ProviderConfigResolver,
    directory_path: str | None,
) -> EffectProviderConfig:
    resolve_provider_config = getattr(
        provider_config_resolver, "resolve_effect_provider_config", None
    )
    if callable(resolve_provider_config):
        return resolve_provider_config(directory_path)
    resolve_for_directory = getattr(provider_config_resolver, "resolve_for_directory", None)
    if callable(resolve_for_directory):
        return EffectProviderConfig.model_validate(
            _model_dump(resolve_for_directory(directory_path))
        )
    raise AttributeError("provider_config_resolver must resolve effect provider config")


def _model_dump(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value
