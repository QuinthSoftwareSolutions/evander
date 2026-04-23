from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Protocol

from pydantic import ValidationError

from .models import SessionManifest


class SessionManifestRepository(Protocol):
    """Repository contract for persisted session lifecycle state."""

    def get_session(self, session_id: str) -> SessionManifest | None: ...

    def list_sessions(self) -> list[SessionManifest]: ...

    def list_active_sessions(self, *, now: datetime | None = None) -> list[SessionManifest]: ...

    def upsert_session(self, manifest: SessionManifest) -> None: ...

    def delete_session(self, session_id: str) -> bool: ...

    def read_all(self) -> dict[str, SessionManifest]: ...

    def read_all_safe(self) -> dict[str, SessionManifest]: ...


class JsonSessionManifestRepository:
    """JSON-backed repository for session lifecycle manifests."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def read_all(self) -> dict[str, SessionManifest]:
        with self._lock:
            raw = self._read_raw()
        return self._validate_manifests(raw, ignore_invalid=False)

    def read_all_safe(self) -> dict[str, SessionManifest]:
        with self._lock:
            try:
                raw = self._read_raw()
            except (OSError, json.JSONDecodeError):
                return {}
        return self._validate_manifests(raw, ignore_invalid=True)

    def get_session(self, session_id: str) -> SessionManifest | None:
        return self.read_all_safe().get(session_id)

    def list_sessions(self) -> list[SessionManifest]:
        return list(self.read_all_safe().values())

    def list_active_sessions(self, *, now: datetime | None = None) -> list[SessionManifest]:
        effective_now = now or datetime.now(UTC)
        return [session for session in self.list_sessions() if session.expires_at > effective_now]

    def upsert_session(self, manifest: SessionManifest) -> None:
        with self._lock:
            payload = self._read_raw()
            payload[manifest.session_id] = manifest.model_dump(mode="json")
            self._atomic_write(payload)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            payload = self._read_raw()
            if session_id not in payload:
                return False
            payload.pop(session_id, None)
            self._atomic_write(payload)
            return True

    def _read_raw(self) -> dict[str, object]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload

    def _atomic_write(self, payload: dict[str, object]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _validate_manifests(raw: dict[str, object], *, ignore_invalid: bool) -> dict[str, SessionManifest]:
        manifests: dict[str, SessionManifest] = {}
        for session_id, value in raw.items():
            try:
                manifest = SessionManifest.model_validate(value)
            except ValidationError:
                if ignore_invalid:
                    continue
                raise
            manifests[str(session_id)] = manifest
        return manifests
