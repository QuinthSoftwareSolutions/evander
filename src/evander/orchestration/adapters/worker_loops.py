"""Compatibility adapters for the original generic orchestration-loop API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from evander.events.models import TriggerDefinitionDiscriminated, TriggerEvent
from evander.repositories.sqlite_runtime import is_storage_busy
from evander.triggers.queue import TriggerQueueRecord


@dataclass(frozen=True)
class TriggerLoopExecution:
    published: bool


class TriggerEventFetcherAdapter:
    def __init__(self, *, triggers: list[TriggerDefinitionDiscriminated]) -> None:
        self._triggers = triggers
        self._cursor = 0

    def fetch(self) -> TriggerDefinitionDiscriminated | None:
        if self._cursor >= len(self._triggers):
            return None
        trigger = self._triggers[self._cursor]
        self._cursor += 1
        return trigger


class TriggerDependencyResolverAdapter:
    def __init__(
        self,
        *,
        providers_by_id: dict[str, Any],
        update_cursor_state: Callable[[TriggerDefinitionDiscriminated, int, list[str]], None],
        on_poll_error: Callable[[TriggerDefinitionDiscriminated], None] | None = None,
    ) -> None:
        self._providers_by_id = providers_by_id
        self._update_cursor_state = update_cursor_state
        self._on_poll_error = on_poll_error

    def resolve(self, event: TriggerDefinitionDiscriminated) -> list[TriggerEvent]:
        provider = self._providers_by_id.get(event.id)
        if provider is None:
            return []
        try:
            events = provider.poll()
        except Exception as error:
            if is_storage_busy(error):
                raise
            if self._on_poll_error is not None:
                self._on_poll_error(event)
            return []
        if not events:
            self._update_cursor_state(event, 0, [])
        return events


class TriggerExecutionRecorderAdapter:
    def record(self, execution: TriggerLoopExecution) -> None:
        _ = execution


class TriggerCompletionPublisherAdapter:
    def publish(self, execution: TriggerLoopExecution) -> None:
        _ = execution


@dataclass(frozen=True)
class EffectLoopDependency:
    graph: Any
    config_revision: str
    cursor_payload: dict[str, Any]


@dataclass(frozen=True)
class EffectLoopExecution:
    queue_record: TriggerQueueRecord
    execution_count: int
    published_count: int


class EffectEventFetcherAdapter:
    def __init__(self, *, fetch_pending: Callable[[], TriggerQueueRecord | None]) -> None:
        self._fetch_pending = fetch_pending

    def fetch(self) -> TriggerQueueRecord | None:
        return self._fetch_pending()


class EffectDependencyResolverAdapter:
    def __init__(
        self, *, dependency_factory: Callable[[TriggerQueueRecord], EffectLoopDependency]
    ) -> None:
        self._dependency_factory = dependency_factory

    def resolve(self, event: TriggerQueueRecord) -> list[EffectLoopDependency]:
        return [self._dependency_factory(event)]


class EffectExecutionRecorderAdapter:
    def __init__(self, *, record_event_processed: Callable[[TriggerQueueRecord], None]) -> None:
        self._record_event_processed = record_event_processed

    def record(self, execution: EffectLoopExecution) -> None:
        self._record_event_processed(execution.queue_record)


class EffectCompletionPublisherAdapter:
    def publish(self, execution: EffectLoopExecution) -> None:
        _ = execution
