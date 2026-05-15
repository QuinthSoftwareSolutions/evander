from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Protocol, TypeVar

from agent_sandbox.models import Directory
from agent_sandbox.storage import JsonStore

R = TypeVar("R")


class DirectoryRepository(Protocol):
    """Repository contract for persisted directory records."""

    def read_all(self) -> dict[str, Directory]: ...

    def write_all(self, items: dict[str, Directory]) -> None: ...

    def get(self, key: str) -> Directory | None: ...

    def update_all(self, updater: Callable[[dict[str, Directory]], R]) -> R: ...


class JsonDirectoryRepository:
    """JSON-backed repository for directory records."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._store = JsonStore(path, Directory)

    def read_all(self) -> dict[str, Directory]:
        return self._store.read_all()

    def write_all(self, items: dict[str, Directory]) -> None:
        self._store.write_all(items)

    def get(self, key: str) -> Directory | None:
        return self._store.get(key)

    def update_all(self, updater: Callable[[dict[str, Directory]], R]) -> R:
        return self._store.update_all(updater)
