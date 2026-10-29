from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class TriggerConfigDocument:
    path: str
    config: dict[str, object]


class TriggerRepository(Protocol):
    """Repository contract for persisted trigger definitions."""

    def read_document(self) -> TriggerConfigDocument: ...

    def write_document(self, config: dict[str, object]) -> TriggerConfigDocument: ...


class WorkerConfigTriggerRepository:
    """Trigger repository backed by worker global config read/write callables."""

    def __init__(
        self,
        *,
        read_config: Callable[[], TriggerConfigDocument],
        write_config: Callable[[dict[str, object]], TriggerConfigDocument],
    ) -> None:
        self._read_config = read_config
        self._write_config = write_config

    def read_document(self) -> TriggerConfigDocument:
        return self._read_config()

    def write_document(self, config: dict[str, object]) -> TriggerConfigDocument:
        return self._write_config(config)
