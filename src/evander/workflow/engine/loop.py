from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from .primitives import CompletionPublisher, DependencyResolver, EventFetcher, ExecutionRecorder

TEvent = TypeVar("TEvent")
TDependency = TypeVar("TDependency")
TExecution = TypeVar("TExecution")


@dataclass(frozen=True)
class OrchestrationLoopResult:
    events_scanned: int
    dependencies_resolved: int
    executions_recorded: int
    completions_published: int


class OrchestrationLoop(Generic[TEvent, TDependency, TExecution]):
    """Compatibility loop for callers of the original four-stage adapter API.

    Host workers own their polling and durable-checkpoint loops directly.
    """

    def __init__(
        self,
        *,
        event_fetcher: EventFetcher[TEvent],
        dependency_resolver: DependencyResolver[TEvent, TDependency],
        execution_recorder: ExecutionRecorder[TExecution],
        completion_publisher: CompletionPublisher[TExecution],
    ) -> None:
        self._event_fetcher = event_fetcher
        self._dependency_resolver = dependency_resolver
        self._execution_recorder = execution_recorder
        self._completion_publisher = completion_publisher

    def run_once(self, *, max_events: int, resolve_and_execute) -> OrchestrationLoopResult:
        events_scanned = 0
        dependencies_resolved = 0
        executions_recorded = 0
        completions_published = 0

        while events_scanned < max_events:
            event = self._event_fetcher.fetch()
            if event is None:
                break
            events_scanned += 1
            dependencies = list(self._dependency_resolver.resolve(event))
            dependencies_resolved += len(dependencies)
            for dependency in dependencies:
                execution = resolve_and_execute(event, dependency)
                if execution is None:
                    continue
                self._execution_recorder.record(execution)
                executions_recorded += 1
                self._completion_publisher.publish(execution)
                completions_published += 1

        return OrchestrationLoopResult(
            events_scanned=events_scanned,
            dependencies_resolved=dependencies_resolved,
            executions_recorded=executions_recorded,
            completions_published=completions_published,
        )
