from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from pydantic import ValidationError

from .models import SessionManifest


class SessionManifestRepository(Protocol):
    """Repository contract for persisted session manifests."""

    def read_all(self) -> dict[str, SessionManifest]: ...

    def read_all_safe(self) -> dict[str, SessionManifest]: ...


class JsonSessionManifestRepository:
    """JSON-backed repository for session manifests."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def read_all(self) -> dict[str, SessionManifest]:
        with self._lock:
            raw = self._read_raw()
        manifests: dict[str, SessionManifest] = {}
        for session_id, value in raw.items():
            manifests[str(session_id)] = SessionManifest.model_validate(value)
        return manifests

    def read_all_safe(self) -> dict[str, SessionManifest]:
        with self._lock:
            try:
                raw = self._read_raw()
            except (OSError, json.JSONDecodeError):
                return {}
        manifests: dict[str, SessionManifest] = {}
        for session_id, value in raw.items():
            try:
                manifest = SessionManifest.model_validate(value)
            except ValidationError:
                continue
            manifests[str(session_id)] = manifest
        return manifests

    def _read_raw(self) -> dict[str, object]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload
