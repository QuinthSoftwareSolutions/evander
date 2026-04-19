from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_sandbox.events.models import EffectDefinitionDiscriminated, materialize_node_definition
from agent_sandbox.provider_runtime.contracts import (
    AgentResumer,
    GitBranchPromoter,
    IssueUpdater,
    ProviderRuntimeBase,
    PullRequestCreator,
    SessionLookup,
    SessionMetadataUpdater,
    VmCreator,
    VmExecutor,
    VmFileCopier,
    VmFileDeleter,
    VmInventory,
    VmPowerController,
)


@dataclass(frozen=True)
class EffectRuntimeContext(ProviderRuntimeBase):
    """Host-side runtime inputs used by effect providers."""

    vm_creator: VmCreator | None = None
    agent_resumer: AgentResumer | None = None
    vm_power_controller: VmPowerController | None = None
    vm_inventory: VmInventory | None = None
    vm_executor: VmExecutor | None = None
    vm_file_copier: VmFileCopier | None = None
    vm_file_deleter: VmFileDeleter | None = None
    session_lookup: SessionLookup | None = None
    issue_updater: IssueUpdater | None = None
    session_metadata_updater: SessionMetadataUpdater | None = None
    git_branch_promoter: GitBranchPromoter | None = None
    pull_request_creator: PullRequestCreator | None = None


class EffectProvider(Protocol):
    @classmethod
    def validate_definition(cls, effect_definition: EffectDefinitionDiscriminated) -> EffectDefinitionDiscriminated:
        ...

    def initialize(
        self,
        effect_definition: EffectDefinitionDiscriminated,
        runtime_context: EffectRuntimeContext,
    ) -> None:
        ...

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ...

    def normalize_resolved_config(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        ...

    def load_state(self, **kwargs: Any) -> Any:
        ...

    def save_state(self, **kwargs: Any) -> None:
        ...

    def cleanup(self, **kwargs: Any) -> None:
        ...


class EffectProviderBase:
    """Convenience base class for effect provider implementations."""

    def __init__(self) -> None:
        self.effect_definition: EffectDefinitionDiscriminated | None = None
        self.runtime_context: EffectRuntimeContext | None = None

    def initialize(
        self,
        effect_definition: EffectDefinitionDiscriminated,
        runtime_context: EffectRuntimeContext,
    ) -> None:
        self.effect_definition = effect_definition
        self.runtime_context = runtime_context

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del resolved_config
        return {}

    def normalize_resolved_config(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return dict(resolved_config) if isinstance(resolved_config, Mapping) else {}

    def load_state(self, **kwargs: Any) -> Any:
        del kwargs
        return None

    def save_state(self, **kwargs: Any) -> None:
        del kwargs
        return None

    def cleanup(self, **kwargs: Any) -> None:
        del kwargs
        return None
    @classmethod
    def validate_definition(cls, effect_definition: EffectDefinitionDiscriminated) -> EffectDefinitionDiscriminated:
        return materialize_node_definition(effect_definition)
