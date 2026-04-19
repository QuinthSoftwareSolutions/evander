from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agent_sandbox.events.models import TriggerDefinitionDiscriminated, TriggerEvent, materialize_node_definition
from agent_sandbox.provider_runtime.contracts import ProviderRuntimeBase


TriggerHookHandler = Callable[[TriggerEvent], None]


@dataclass(frozen=True)
class TriggerRuntimeContext(ProviderRuntimeBase):
    """Host-side runtime inputs used by trigger providers.

    Keeping provider construction and secret lookup in this context ensures workers
    retain a single host-side control point for environment and secret access.
    """

    pass


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
