from __future__ import annotations

from typing import Any, Protocol


class TriggerEventQueueInterface(Protocol):
    def publish(self, event: Any) -> Any: ...

    def pending(self) -> list[Any]: ...


class ExecutionStoreInterface(Protocol):
    def read_all(self) -> dict[str, Any]: ...

    def put(self, record: Any) -> None: ...


class TriggerSchedulerInterface(Protocol):
    def run_once(self) -> Any: ...


class EffectSchedulerInterface(Protocol):
    def run_once(self) -> Any: ...
