from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class TriggerCursorStore:
    """Simple atomic JSON cursor store keyed by trigger_id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def get(self, trigger_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._read_raw()
            raw = payload.get(trigger_id)
            if isinstance(raw, dict):
                return dict(raw)
            return {}

    def put(self, trigger_id: str, value: dict[str, Any]) -> None:
        with self._lock:
            payload = self._read_raw()
            payload[trigger_id] = value
            self._atomic_write(payload)

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)
