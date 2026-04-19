from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_sandbox.admin_service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.models import TriggerDefinitionDiscriminated, TriggerEvent, materialize_node_definition


TriggerHookHandler = Callable[[TriggerEvent], None]


@dataclass(frozen=True)
class TriggerRuntimeContext:
    """Host-side runtime inputs used by trigger providers.

    Keeping provider construction and secret lookup in this context ensures workers
    retain a single host-side control point for environment and secret access.
    """

    resolved_config: GatewayConfig
    admin_service: AdminService | None = None
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


class TriggerProvider(Protocol):
    @classmethod
    def validate_definition(cls, trigger_definition: TriggerDefinitionDiscriminated) -> TriggerDefinitionDiscriminated:
        ...

    def initialize(
        self,
        trigger_definition: TriggerDefinitionDiscriminated,
        runtime_context: TriggerRuntimeContext,
    ) -> None:
        ...

    def poll(self) -> list[TriggerEvent]:
        ...


class PushTriggerProvider(TriggerProvider, Protocol):
    def register_hook(self, handler: TriggerHookHandler) -> None:
        ...


class TriggerProviderBase:
    """Convenience base class for provider implementations."""

    def __init__(self) -> None:
        self.trigger_definition: TriggerDefinitionDiscriminated | None = None
        self.runtime_context: TriggerRuntimeContext | None = None

    def initialize(
        self,
        trigger_definition: TriggerDefinitionDiscriminated,
        runtime_context: TriggerRuntimeContext,
    ) -> None:
        self.trigger_definition = trigger_definition
        self.runtime_context = runtime_context

    def poll(self) -> list[TriggerEvent]:
        return []
    @classmethod
    def validate_definition(cls, trigger_definition: TriggerDefinitionDiscriminated) -> TriggerDefinitionDiscriminated:
        return materialize_node_definition(trigger_definition)
