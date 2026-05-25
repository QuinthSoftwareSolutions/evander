from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agent_sandbox.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    WorkflowNodeEnvelope,
    materialize_node_definition,
)
from agent_sandbox.provider_runtime.contracts import ProviderRuntimeBase


TriggerHookHandler = Callable[[TriggerEvent], None]


@dataclass(frozen=True)
class TriggerRuntimeContext(ProviderRuntimeBase):
    """Host-side runtime inputs used by trigger providers.

    Keeping provider construction and secret lookup in this context ensures workers
    retain a single host-side control point for environment and secret access.
    """

    admin_service: Any | None = None


class TriggerProvider(Protocol):
    @classmethod
    def validate_definition(
        cls, trigger_definition: TriggerDefinitionDiscriminated
    ) -> TriggerDefinitionDiscriminated: ...

    def initialize(
        self,
        trigger_definition: TriggerDefinitionDiscriminated,
        runtime_context: TriggerRuntimeContext,
    ) -> None: ...

    def poll(self) -> list[TriggerEvent]: ...


class PushTriggerProvider(TriggerProvider, Protocol):
    def register_hook(self, handler: TriggerHookHandler) -> None: ...


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
        if type(trigger_definition) is WorkflowNodeEnvelope:
            trigger_definition = self.validate_definition(trigger_definition)
        if getattr(trigger_definition, "type", None) == "trigger:communication:new-message":
            from agent_sandbox.directories import DirectoryStore
            from agent_sandbox.messaging.targets import (
                validate_communication_new_message_trigger_targets,
            )

            directory_path = str(runtime_context.metadata.get("directory_path") or "root")
            resolved_config = runtime_context.resolved_config
            if getattr(resolved_config, "directories_store_path", None) is None:
                resolver = runtime_context.provider_config_resolver
                if resolver is None:
                    raise RuntimeError("TriggerProviderBase has no provider config resolver")
                resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
                if not callable(resolve_for_directory):
                    raise RuntimeError(
                        "TriggerProviderBase provider config resolver cannot resolve directories"
                    )
                resolved_config = resolve_for_directory(directory_path)
            validate_communication_new_message_trigger_targets(
                trigger_definition,
                runtime_dir=resolved_config.runtime_dir,
                directory_store=DirectoryStore(resolved_config.directories_store_path),
            )
        self.trigger_definition = trigger_definition
        self.runtime_context = runtime_context

    def poll(self) -> list[TriggerEvent]:
        return []

    @classmethod
    def validate_definition(
        cls, trigger_definition: TriggerDefinitionDiscriminated
    ) -> TriggerDefinitionDiscriminated:
        if type(trigger_definition) is not WorkflowNodeEnvelope:
            return trigger_definition
        return materialize_node_definition(trigger_definition)
