"""Narrow worker-lifecycle port for codebase consultation effects."""

from __future__ import annotations

from typing import Protocol

from evander.events.provider_models import (
    AgentCloneRepoEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
)

from .agent_clone_repo import AgentCloneRepoEffectProvider
from .agent_create import AgentCreateEffectProvider
from .agent_destroy import AgentDestroyEffectProvider
from .base import EffectProviderBase, EffectRuntimeContext


class ConsultationWorkerLifecycle(Protocol):
    """External worker operations needed by consultation orchestration."""

    def create(self, definition: AgentCreateEffectDefinition) -> dict[str, object]: ...

    def clone(self, definition: AgentCloneRepoEffectDefinition) -> dict[str, object]: ...

    def destroy(self, definition: AgentDestroyEffectDefinition) -> dict[str, object]: ...


class NestedEffectConsultationWorker:
    """Adapt existing agent effect providers to ``ConsultationWorkerLifecycle``."""

    def __init__(self, context: EffectRuntimeContext) -> None:
        self._context = context

    def create(self, definition: AgentCreateEffectDefinition) -> dict[str, object]:
        return self._execute(AgentCreateEffectProvider(), definition)

    def clone(self, definition: AgentCloneRepoEffectDefinition) -> dict[str, object]:
        return self._execute(AgentCloneRepoEffectProvider(), definition)

    def destroy(self, definition: AgentDestroyEffectDefinition) -> dict[str, object]:
        return self._execute(AgentDestroyEffectProvider(), definition)

    def _execute(
        self,
        provider: EffectProviderBase,
        definition: (
            AgentCreateEffectDefinition
            | AgentCloneRepoEffectDefinition
            | AgentDestroyEffectDefinition
        ),
    ) -> dict[str, object]:
        provider.initialize(definition, self._context)
        return provider.execute()


__all__ = ["ConsultationWorkerLifecycle", "NestedEffectConsultationWorker"]
