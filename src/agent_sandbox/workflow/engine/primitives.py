from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, TypeVar

TEvent = TypeVar("TEvent")
TDependency = TypeVar("TDependency")
TExecution = TypeVar("TExecution")


class EventFetcher(Protocol[TEvent]):
    def fetch(self) -> TEvent | None:
        """Return the next event to process, or None when idle."""


class DependencyResolver(Protocol[TEvent, TDependency]):
    def resolve(self, event: TEvent) -> Iterable[TDependency]:
        """Return executable dependencies for a single event."""


class ExecutionRecorder(Protocol[TExecution]):
    def record(self, execution: TExecution) -> None:
        """Persist execution state transitions."""


class CompletionPublisher(Protocol[TExecution]):
    def publish(self, execution: TExecution) -> Any:
        """Publish completion side-effects for successful execution records."""
