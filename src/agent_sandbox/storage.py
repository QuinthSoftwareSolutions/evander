from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class JsonStore(Generic[T]):
    def __init__(self, path: Path, model: type[T]) -> None:
        self.path = path
        self.model = model
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def read_all(self) -> dict[str, T]:
        with self._lock:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {key: self.model.model_validate(value) for key, value in raw.items()}

    def write_all(self, items: dict[str, T]) -> None:
        payload = {key: value.model_dump(mode="json") for key, value in items.items()}
        with self._lock:
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def get(self, key: str) -> T | None:
        return self.read_all().get(key)

    def put(self, key: str, value: T) -> None:
        with self._lock:
            items = self.read_all()
            items[key] = value
            self.write_all(items)

    def delete(self, key: str) -> None:
        with self._lock:
            items = self.read_all()
            items.pop(key, None)
            self.write_all(items)
