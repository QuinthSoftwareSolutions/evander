from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class SpecialEventResult:
    consume_event: bool = False


class SpecialEventHandler(Protocol):
    def handle_event(
        self,
        *,
        worker: Any,
        queue_record: Any,
        graph: Any,
        phase: Literal["pre", "post"],
        source_node_exists: bool,
    ) -> SpecialEventResult | None:
        ...

    def force_complete(self, *, worker: Any, dispatch_id: str) -> bool:
        ...


class LifecycleReconciliationHook(Protocol):
    def reconcile(self, *, worker: Any, graph: Any) -> int:
        ...


class CompletionDispatchHook(Protocol):
    def dispatch(self, *, worker: Any) -> int:
        ...


class NoOpSpecialEventHandler:
    def handle_event(
        self,
        *,
        worker: Any,
        queue_record: Any,
        graph: Any,
        phase: Literal["pre", "post"],
        source_node_exists: bool,
    ) -> SpecialEventResult | None:
        del worker, queue_record, graph, phase, source_node_exists
        return None

    def force_complete(self, *, worker: Any, dispatch_id: str) -> bool:
        del worker, dispatch_id
        return False


class NoOpLifecycleReconciliationHook:
    def reconcile(self, *, worker: Any, graph: Any) -> int:
        del worker, graph
        return 0


class NoOpCompletionDispatchHook:
    def dispatch(self, *, worker: Any) -> int:
        del worker
        return 0
