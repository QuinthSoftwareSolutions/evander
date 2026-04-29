from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from pydantic import ValidationError

from agent_sandbox.models import SessionManifest
from agent_sandbox.storage import JsonStore


class SessionManifestRepository(Protocol):
    """Repository contract for persisted session lifecycle state."""

    def get_session(self, session_id: str) -> SessionManifest | None: ...

    def list_sessions(self) -> list[SessionManifest]: ...

    def list_active_sessions(self, *, now: datetime | None = None) -> list[SessionManifest]: ...

    def list_sessions_for_vm(self, vm_name: str) -> list[SessionManifest]: ...

    def list_sessions_for_issue(self, issue_number: str) -> list[SessionManifest]: ...

    def resolve_session_target(self, target: str) -> SessionManifest | None: ...

    def resolve_vm_name_from_target(self, target: str) -> str: ...

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

    def get(self, session_id: str) -> SessionManifest | None:
        return self.get_session(session_id)

    def list_sessions(self) -> list[SessionManifest]:
        return list(self.read_all_safe().values())

    def list_active_sessions(self, *, now: datetime | None = None) -> list[SessionManifest]:
        effective_now = now or datetime.now(UTC)
        return [session for session in self.list_sessions() if session.expires_at > effective_now]

    def list_sessions_for_vm(self, vm_name: str) -> list[SessionManifest]:
        normalized_vm_name = str(vm_name or "").strip()
        if not normalized_vm_name:
            return []
        sessions = [
            session
            for session in self.list_sessions()
            if str(getattr(session, "vm_id", "") or "").strip() == normalized_vm_name
        ]
        sessions.sort(key=_session_manifest_sort_key)
        return sessions

    def list_sessions_for_issue(self, issue_number: str) -> list[SessionManifest]:
        normalized_issue_number = str(issue_number or "").strip()
        if not normalized_issue_number:
            return []
        vm_name = f"vm-{normalized_issue_number}"
        prefix = _issue_session_id_prefix(normalized_issue_number)
        sessions = [
            session
            for session in self.list_sessions()
            if str(getattr(session, "vm_id", "") or "").strip() == vm_name
            or str(getattr(session, "session_id", "") or "").strip() == normalized_issue_number
            or str(getattr(session, "session_id", "") or "").strip().startswith(prefix)
        ]
        sessions.sort(key=_session_manifest_sort_key)
        return sessions

    def resolve_session_target(self, target: str) -> SessionManifest | None:
        normalized = str(target or "").strip()
        if not normalized:
            return None
        direct_match = self.get_session(normalized)
        if direct_match is not None:
            return direct_match

        issue_number = _extract_issue_number(normalized)
        matches: list[SessionManifest] = []
        if issue_number is not None:
            matches.extend(self.list_sessions_for_issue(issue_number))
        matches.extend(self.list_sessions_for_vm(normalized))
        if not matches:
            return None
        return max(matches, key=_session_manifest_sort_key)

    def resolve_vm_name_from_target(self, target: str) -> str:
        normalized = str(target or "").strip()
        if not normalized:
            return ""
        session = self.get_session(normalized)
        if session is not None:
            return str(getattr(session, "vm_id", "") or "").strip() or normalized
        return normalized

    def upsert_session(self, manifest: SessionManifest) -> None:
        with self._lock:
            payload = self._read_raw()
            payload[manifest.session_id] = manifest.model_dump(mode="json")
            self._atomic_write(payload)

    def put(self, session_id: str, manifest: SessionManifest) -> None:
        resolved_manifest = manifest
        if manifest.session_id != session_id:
            resolved_manifest = manifest.model_copy(update={"session_id": session_id})
        self.upsert_session(resolved_manifest)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            payload = self._read_raw()
            if session_id not in payload:
                return False
            payload.pop(session_id, None)
            self._atomic_write(payload)
            return True

    def delete(self, session_id: str) -> None:
        self.delete_session(session_id)

    def write_all(self, payload: dict[str, SessionManifest]) -> None:
        with self._lock:
            self._atomic_write(
                {
                    session_id: manifest.model_dump(mode="json")
                    for session_id, manifest in payload.items()
                }
            )

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
    def _validate_manifests(
        raw: dict[str, object], *, ignore_invalid: bool
    ) -> dict[str, SessionManifest]:
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


class SessionManifestStoreAdapter:
    """Compatibility adapter over legacy JsonStore-based session access."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def get_session(self, session_id: str) -> SessionManifest | None:
        return self._store.get(session_id)

    def get(self, session_id: str) -> SessionManifest | None:
        return self.get_session(session_id)

    def list_sessions(self) -> list[SessionManifest]:
        return list(self._store.read_all().values())

    def list_active_sessions(self, *, now: datetime | None = None) -> list[SessionManifest]:
        effective_now = now or datetime.now(UTC)
        return [session for session in self.list_sessions() if session.expires_at > effective_now]

    def list_sessions_for_vm(self, vm_name: str) -> list[SessionManifest]:
        normalized_vm_name = str(vm_name or "").strip()
        if not normalized_vm_name:
            return []
        sessions = [
            session
            for session in self.list_sessions()
            if str(getattr(session, "vm_id", "") or "").strip() == normalized_vm_name
        ]
        sessions.sort(key=_session_manifest_sort_key)
        return sessions

    def list_sessions_for_issue(self, issue_number: str) -> list[SessionManifest]:
        normalized_issue_number = str(issue_number or "").strip()
        if not normalized_issue_number:
            return []
        vm_name = f"vm-{normalized_issue_number}"
        prefix = _issue_session_id_prefix(normalized_issue_number)
        sessions = [
            session
            for session in self.list_sessions()
            if str(getattr(session, "vm_id", "") or "").strip() == vm_name
            or str(getattr(session, "session_id", "") or "").strip() == normalized_issue_number
            or str(getattr(session, "session_id", "") or "").strip().startswith(prefix)
        ]
        sessions.sort(key=_session_manifest_sort_key)
        return sessions

    def resolve_session_target(self, target: str) -> SessionManifest | None:
        normalized = str(target or "").strip()
        if not normalized:
            return None
        direct_match = self.get_session(normalized)
        if direct_match is not None:
            return direct_match

        issue_number = _extract_issue_number(normalized)
        matches: list[SessionManifest] = []
        if issue_number is not None:
            matches.extend(self.list_sessions_for_issue(issue_number))
        matches.extend(self.list_sessions_for_vm(normalized))
        if not matches:
            return None
        return max(matches, key=_session_manifest_sort_key)

    def resolve_vm_name_from_target(self, target: str) -> str:
        normalized = str(target or "").strip()
        if not normalized:
            return ""
        session = self.get_session(normalized)
        if session is not None:
            return str(getattr(session, "vm_id", "") or "").strip() or normalized
        return normalized

    def upsert_session(self, manifest: SessionManifest) -> None:
        self._store.put(manifest.session_id, manifest)

    def put(self, session_id: str, manifest: SessionManifest) -> None:
        self._store.put(session_id, manifest)

    def delete_session(self, session_id: str) -> bool:
        existing = self._store.get(session_id)
        if existing is None:
            return False
        self._store.delete(session_id)
        return True

    def delete(self, session_id: str) -> None:
        self._store.delete(session_id)

    def read_all(self) -> dict[str, SessionManifest]:
        return self._store.read_all()

    def read_all_safe(self) -> dict[str, SessionManifest]:
        return self._store.read_all()

    def write_all(self, payload: dict[str, SessionManifest]) -> None:
        self._store.write_all(payload)


def as_session_manifest_repository(
    repository: SessionManifestRepository | JsonStore[SessionManifest] | Any,
) -> SessionManifestRepository:
    if hasattr(repository, "get_session") and hasattr(repository, "list_sessions"):
        return repository
    if isinstance(repository, JsonStore):
        return SessionManifestStoreAdapter(repository)
    if hasattr(repository, "get") and hasattr(repository, "read_all"):
        return SessionManifestStoreAdapter(repository)
    return repository


def _session_manifest_sort_key(session: SessionManifest) -> tuple[datetime, datetime, str]:
    return (session.created_at, session.expires_at, session.session_id)


def _issue_session_id_prefix(issue_number: str) -> str:
    return f"issue-{issue_number}-"


def _extract_issue_number(value: str) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    if normalized.startswith("issue-"):
        suffix = normalized.removeprefix("issue-")
        if suffix.isdigit():
            return suffix
        if "-" in suffix:
            candidate = suffix.split("-", 1)[0]
            if candidate.isdigit():
                return candidate
    return None
