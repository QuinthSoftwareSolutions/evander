from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from agent_sandbox.admin_service import AdminService
from agent_sandbox.events.models import EffectDefinitionDiscriminated, EffectType
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.provider_runtime.adapters import AdminServiceProviderAdapter, effect_provider_config_blob
from agent_sandbox.workflow_plugins import EFFECT_PLUGIN_CLASSES

from .providers.base import EffectProviderBase, EffectRuntimeContext

EffectProviderClass = type[EffectProviderBase]


class NoopEffectProvider(EffectProviderBase):
    """Default provider scaffold for effect types awaiting concrete implementations."""


DEFAULT_EFFECT_PROVIDER_REGISTRY: dict[EffectType, EffectProviderClass] = dict(EFFECT_PLUGIN_CLASSES)


class EffectProviderRegistry:
    def __init__(self, initial: Mapping[EffectType, EffectProviderClass] | None = None) -> None:
        self._providers: dict[EffectType, EffectProviderClass] = dict(initial or DEFAULT_EFFECT_PROVIDER_REGISTRY)

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
        admin_service: AdminService,
        *,
        registry: EffectProviderRegistry | None = None,
        env: Mapping[str, str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider_config_resolver = provider_config_resolver
        self.admin_service = admin_service
        self._admin_adapter = AdminServiceProviderAdapter(admin_service)
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
        resolved_config = self.provider_config_resolver.resolve_for_directory(directory_path)
        runtime_context = EffectRuntimeContext(
            provider_config=effect_provider_config_blob(resolved_config),
            vm_creator=self._admin_adapter,
            agent_resumer=self._admin_adapter,
            vm_power_controller=self._admin_adapter,
            vm_inventory=self._admin_adapter,
            vm_executor=self._admin_adapter,
            vm_file_copier=self._admin_adapter,
            vm_file_deleter=self._admin_adapter,
            session_lookup=self._admin_adapter,
            issue_updater=self._admin_adapter,
            session_metadata_updater=self._admin_adapter,
            git_branch_promoter=self._admin_adapter,
            pull_request_creator=self._admin_adapter,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata={"directory_path": directory_path or "root"},
        )
        provider = provider_class()
        validated_definition = provider_class.validate_definition(effect_definition)
        provider.initialize(effect_definition=validated_definition, runtime_context=runtime_context)
        return provider
