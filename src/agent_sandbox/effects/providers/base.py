from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_sandbox.admin_service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.models import EffectDefinitionDiscriminated, materialize_node_definition


@dataclass(frozen=True)
class EffectRuntimeContext:
    """Host-side runtime inputs used by effect providers."""

    resolved_config: GatewayConfig
    admin_service: AdminService
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    secret_resolver: Callable[[str], str | None] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolve_secret(self, ref: str) -> str | None:
        normalized_ref = ref.strip()
        if not normalized_ref:
            return None
        if self.secret_resolver is not None:
            return self.secret_resolver(normalized_ref)
        return self.env.get(normalized_ref)


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
    @classmethod
    def validate_definition(cls, effect_definition: EffectDefinitionDiscriminated) -> EffectDefinitionDiscriminated:
        return materialize_node_definition(effect_definition)
