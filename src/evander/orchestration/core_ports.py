from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol


class EventQueuePort(Protocol):
    def reset_inflight(self) -> None: ...


class ExecutionJournalPort(Protocol):
    def append(self, entry: dict[str, Any]) -> None: ...


class ProviderFactoryPort(Protocol):
    def build(self, definition: Any) -> Any: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class SleepPort(Protocol):
    def sleep(self, seconds: float) -> None: ...


class LockManagerPort(Protocol):
    def lock(self, path: Any) -> AbstractContextManager[Any]: ...


class CursorStorePort(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, payload: dict[str, Any]) -> None: ...
