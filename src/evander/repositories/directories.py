from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, TypeVar

from evander.models import Directory
from evander.storage import JsonStore

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


class ReadOnlyJsonDirectoryRepository:
    """One registry snapshot, without creating, repairing or locking any file.

    Registry writers publish with atomic replacement. Read the complete document
    once so one policy resolution sees a consistent directory tree; construct a
    fresh snapshot for the next dispatch to observe newly published policy.
    """

    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Directory registry must contain an object")
        self._items = {key: Directory.model_validate(value) for key, value in raw.items()}

    def read_all(self) -> dict[str, Directory]:
        return {key: value.model_copy(deep=True) for key, value in self._items.items()}

    def get(self, key: str) -> Directory | None:
        return self.read_all().get(key)

    def write_all(self, items: dict[str, Directory]) -> None:
        raise PermissionError("Directory snapshot is read-only")

    def update_all(self, updater: Callable[[dict[str, Directory]], R]) -> R:
        raise PermissionError("Directory snapshot is read-only")
