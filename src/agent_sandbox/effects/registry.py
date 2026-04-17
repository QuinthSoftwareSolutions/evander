from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from agent_sandbox.admin_service import AdminService
from agent_sandbox.events.models import EffectDefinitionDiscriminated, EffectType
from agent_sandbox.provider_config import ProviderConfigResolver

from .providers.agent_prompt import AgentPromptEffectProvider
from .providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from .providers.bash_script import BashScriptEffectProvider
from .providers.base import EffectProviderBase, EffectRuntimeContext
from .providers.git_promote_agent_branch import GitPromoteAgentBranchEffectProvider
from .providers.issue_update_state import IssueUpdateStateEffectProvider
from .providers.pr_create import PrCreateEffectProvider
from .providers.session_update_metadata import SessionUpdateMetadataEffectProvider
from .providers.vm_copy_files import VmCopyFilesEffectProvider
from .providers.vm_create import VmCreateEffectProvider
from .providers.vm_delete_files import VmDeleteFilesEffectProvider
from .providers.vm_exec import VmExecEffectProvider
from .providers.vm_start import VmStartEffectProvider
from .providers.vm_stop import VmStopEffectProvider

EffectProviderClass = type[EffectProviderBase]


class NoopEffectProvider(EffectProviderBase):
    """Default provider scaffold for effect types awaiting concrete implementations."""


DEFAULT_EFFECT_PROVIDER_REGISTRY: dict[EffectType, EffectProviderClass] = {
    "effect:vm:create": VmCreateEffectProvider,
    "effect:vm:start": VmStartEffectProvider,
    "effect:vm:stop": VmStopEffectProvider,
    "effect:agent:prompt": AgentPromptEffectProvider,
    "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectProvider,
    "effect:bash:script": BashScriptEffectProvider,
    "effect:vm:exec": VmExecEffectProvider,
    "effect:vm:copy-files": VmCopyFilesEffectProvider,
    "effect:vm:delete-files": VmDeleteFilesEffectProvider,
    "effect:issue:update-state": IssueUpdateStateEffectProvider,
    "effect:session:update-metadata": SessionUpdateMetadataEffectProvider,
    "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectProvider,
    "effect:pr:create": PrCreateEffectProvider,
}


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
            resolved_config=resolved_config,
            admin_service=self.admin_service,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata={"directory_path": directory_path or "root"},
        )
        provider = provider_class()
        provider.initialize(effect_definition=effect_definition, runtime_context=runtime_context)
        return provider
