from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from pydantic import ValidationError

from evander.models import SessionManifest, ToolName
from evander.storage import JsonObjectStore, JsonStore, _no_write


class SessionManifestRepository(Protocol):
    """Repository contract for persisted session lifecycle state."""

    def get_session(self, session_id: str) -> SessionManifest | None: ...

    def list_sessions(self) -> list[SessionManifest]: ...

    def list_active_sessions(self, *, now: datetime | None = None) -> list[SessionManifest]: ...

    def list_sessions_for_vm(self, vm_name: str) -> list[SessionManifest]: ...

    def list_sessions_for_issue(self, issue_number: str) -> list[SessionManifest]: ...

    def resolve_session_target(self, target: str) -> SessionManifest | None: ...

    def resolve_vm_name_from_target(self, target: str) -> str: ...

    def mutate_session(
        self, session_id: str, updater: Callable[[SessionManifest], SessionManifest]
    ) -> SessionManifest: ...

    def upsert_session(self, manifest: SessionManifest) -> None: ...

    def delete_session(self, session_id: str) -> bool: ...

    def read_all(self) -> dict[str, SessionManifest]: ...

    def read_all_safe(self) -> dict[str, SessionManifest]: ...


class JsonSessionManifestRepository:
    """JSON-backed repository for session lifecycle manifests."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._store = JsonObjectStore(path, {})
        self._lookup_lock = RLock()
        self._lookup_revision: tuple[int, ...] | None = None
        self._lookup_sessions: dict[str, SessionManifest] = {}

    def _file_revision(self) -> tuple[int, ...]:
        stat = self.path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def read_all(self) -> dict[str, SessionManifest]:
        return self._validate_manifests(self._read_raw(), ignore_invalid=False)

    def read_all_safe(self) -> dict[str, SessionManifest]:
        try:
            raw = self._read_raw()
        except (OSError, json.JSONDecodeError):
            return {}
        return self._validate_manifests(raw, ignore_invalid=True)

    def get_session(self, session_id: str) -> SessionManifest | None:
        # Authorization callers must see replacements, in-place changes and
        # writes from other processes immediately on their next lookup. A TTL
        # cache would extend revoked grants. Retain only one bounded revision,
        # and never expose its mutable models to callers.
        with self._lookup_lock:
            try:
                revision = self._file_revision()
                if revision == self._lookup_revision:
                    session = self._lookup_sessions.get(session_id)
                    return session.model_copy(deep=True) if session is not None else None
                self._lookup_revision = None
                self._lookup_sessions = {}
                sessions = self._validate_manifests(self._read_raw(), ignore_invalid=True)
                if (
                    revision[2] <= 16 * 1024 * 1024
                    and len(sessions) <= 10_000
                    and self._file_revision() == revision
                ):
                    self._lookup_sessions = sessions
                    self._lookup_revision = revision
                session = sessions.get(session_id)
                return session.model_copy(deep=True) if session is not None else None
            except (OSError, json.JSONDecodeError):
                self._lookup_revision = None
                self._lookup_sessions = {}
                return None

    def get(self, session_id: str) -> SessionManifest | None:
        return self.get_session(session_id)

    def list_sessions(self) -> list[SessionManifest]:
        return list(self.read_all_safe().values())

    def list_active_sessions(self, *, now: datetime | None = None) -> list[SessionManifest]:
        return _list_active_sessions(self, now=now)

    def list_sessions_for_vm(self, vm_name: str) -> list[SessionManifest]:
        return _list_sessions_for_vm(self, vm_name)

    def list_sessions_for_issue(self, issue_number: str) -> list[SessionManifest]:
        return _list_sessions_for_issue(self, issue_number)

    def resolve_session_target(self, target: str) -> SessionManifest | None:
        return _resolve_session_target(self, target)

    def resolve_vm_name_from_target(self, target: str) -> str:
        return _resolve_vm_name_from_target(self, target)

    def mutate_session(
        self, session_id: str, updater: Callable[[SessionManifest], SessionManifest]
    ) -> SessionManifest:
        """Update the latest manifest under the store lock, without resurrecting a session."""
        def mutate(payload: dict[str, Any]) -> SessionManifest | object:
            matches = [
                key for key, value in payload.items()
                if isinstance(value, dict) and value.get("session_id", key) == session_id
            ]
            if not matches:
                raise ValueError(f"Unknown session: {session_id}")
            key = matches[-1]
            before = SessionManifest.model_validate(payload[key])
            after = updater(before.model_copy(deep=True))
            after = SessionManifest.model_validate(after.model_dump(mode="json"))
            if after.session_id != before.session_id or after.created_at != before.created_at:
                raise ValueError("Session mutation cannot replace session identity")
            if after == before:
                return _no_write(after)
            payload[key] = after.model_dump(mode="json")
            return after

        return self._store.update_object(mutate)

    def upsert_session(self, manifest: SessionManifest) -> None:
        def _upsert(payload: dict[str, Any]) -> None:
            payload[manifest.session_id] = manifest.model_dump(mode="json")

        self._store.update_object(_upsert)

    def put(self, session_id: str, manifest: SessionManifest) -> None:
        resolved_manifest = manifest
        if manifest.session_id != session_id:
            resolved_manifest = manifest.model_copy(update={"session_id": session_id})
        self.upsert_session(resolved_manifest)

    def delete_session(self, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return False

        def _delete(payload: dict[str, Any]) -> bool | object:
            removed = False
            for key, value in list(payload.items()):
                if str(key) == normalized_session_id:
                    payload.pop(key, None)
                    removed = True
                    continue
                if not isinstance(value, dict):
                    continue
                value_session_id = str(value.get("session_id") or "").strip()
                if value_session_id == normalized_session_id:
                    payload.pop(key, None)
                    removed = True
            if not removed:
                return _no_write(False)
            return True

        return bool(self._store.update_object(_delete))

    def delete(self, session_id: str) -> None:
        self.delete_session(session_id)

    def write_all(self, payload: dict[str, SessionManifest]) -> None:
        self._store.write_object(
            {
                session_id: manifest.model_dump(mode="json")
                for session_id, manifest in payload.items()
            }
        )

    def _read_raw(self) -> dict[str, object]:
        try:
            payload = self._store.read_object()
        except ValueError as exc:
            if "must contain an object at the top level" in str(exc):
                return {}
            raise
        if not isinstance(payload, dict):
            return {}
        return payload

    @staticmethod
    def _validate_manifests(
        raw: dict[str, object], *, ignore_invalid: bool
    ) -> dict[str, SessionManifest]:
        manifests: dict[str, SessionManifest] = {}
        for session_id, value in raw.items():
            try:
                manifest = SessionManifest.model_validate(value)
            except ValidationError:
                if not ignore_invalid:
                    raise
                sanitized = _session_manifest_with_known_tool_names(value)
                if sanitized is None:
                    continue
                try:
                    manifest = SessionManifest.model_validate(sanitized)
                except ValidationError:
                    continue
            manifest_session_id = str(getattr(manifest, "session_id", "") or "").strip()
            manifests[manifest_session_id or str(session_id)] = manifest
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
        return _list_active_sessions(self, now=now)

    def list_sessions_for_vm(self, vm_name: str) -> list[SessionManifest]:
        return _list_sessions_for_vm(self, vm_name)

    def list_sessions_for_issue(self, issue_number: str) -> list[SessionManifest]:
        return _list_sessions_for_issue(self, issue_number)

    def resolve_session_target(self, target: str) -> SessionManifest | None:
        return _resolve_session_target(self, target)

    def resolve_vm_name_from_target(self, target: str) -> str:
        return _resolve_vm_name_from_target(self, target)

    def mutate_session(
        self, session_id: str, updater: Callable[[SessionManifest], SessionManifest]
    ) -> SessionManifest:
        def mutate(items: dict[str, SessionManifest]) -> SessionManifest:
            before = items.get(session_id)
            if before is None:
                raise ValueError(f"Unknown session: {session_id}")
            after = updater(before.model_copy(deep=True))
            after = SessionManifest.model_validate(after.model_dump(mode="json"))
            if after.session_id != before.session_id or after.created_at != before.created_at:
                raise ValueError("Session mutation cannot replace session identity")
            items[session_id] = after
            return after

        return self._store.update_all(mutate)

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


def _list_active_sessions(
    repository: SessionManifestRepository, *, now: datetime | None = None
) -> list[SessionManifest]:
    effective_now = now or datetime.now(UTC)
    return [session for session in repository.list_sessions() if session.expires_at > effective_now]


def _list_sessions_for_vm(
    repository: SessionManifestRepository, vm_name: str
) -> list[SessionManifest]:
    normalized_vm_name = str(vm_name or "").strip()
    if not normalized_vm_name:
        return []
    sessions = [
        session
        for session in repository.list_sessions()
        if str(getattr(session, "vm_id", "") or "").strip() == normalized_vm_name
    ]
    sessions.sort(key=_session_manifest_sort_key)
    return sessions


def _list_sessions_for_issue(
    repository: SessionManifestRepository, issue_number: str
) -> list[SessionManifest]:
    normalized_issue_number = str(issue_number or "").strip()
    if not normalized_issue_number:
        return []
    vm_name = f"vm-{normalized_issue_number}"
    prefix = _issue_session_id_prefix(normalized_issue_number)
    sessions = [
        session
        for session in repository.list_sessions()
        if str(getattr(session, "vm_id", "") or "").strip() == vm_name
        or str(getattr(session, "session_id", "") or "").strip() == normalized_issue_number
        or str(getattr(session, "session_id", "") or "").strip().startswith(prefix)
    ]
    sessions.sort(key=_session_manifest_sort_key)
    return sessions


def _resolve_session_target(
    repository: SessionManifestRepository, target: str
) -> SessionManifest | None:
    normalized = str(target or "").strip()
    if not normalized:
        return None
    direct_match = repository.get_session(normalized)
    if direct_match is not None:
        return direct_match

    issue_number = _extract_issue_number(normalized)
    matches: list[SessionManifest] = []
    if issue_number is not None:
        matches.extend(repository.list_sessions_for_issue(issue_number))
    matches.extend(repository.list_sessions_for_vm(normalized))
    if not matches:
        return None
    return max(matches, key=_session_manifest_sort_key)


def _resolve_vm_name_from_target(repository: SessionManifestRepository, target: str) -> str:
    normalized = str(target or "").strip()
    if not normalized:
        return ""
    session = repository.get_session(normalized)
    if session is not None:
        return str(getattr(session, "vm_id", "") or "").strip() or normalized
    return normalized


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


def _session_manifest_with_known_tool_names(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    sanitized = dict(value)
    changed = False
    for field_name in ("allowed_tools", "approval_required_tools"):
        filtered, field_changed = _known_tool_name_list(sanitized.get(field_name))
        if field_changed:
            sanitized[field_name] = filtered
            changed = True
    for field_name in ("approval_tool_settings", "tool_argument_policies"):
        filtered, field_changed = _known_tool_name_mapping(sanitized.get(field_name))
        if field_changed:
            sanitized[field_name] = filtered
            changed = True
    return sanitized if changed else None


def _known_tool_name_list(value: object) -> tuple[list[str], bool]:
    if value is None:
        return [], False
    if isinstance(value, (str, ToolName)):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return [], False
    filtered: list[str] = []
    seen: set[str] = set()
    changed = False
    for item in raw_items:
        candidate = item.value if isinstance(item, ToolName) else str(item).strip()
        if not candidate:
            continue
        tool = ToolName._value2member_map_.get(candidate)
        if tool is None:
            changed = True
            continue
        if tool.value in seen:
            continue
        seen.add(tool.value)
        filtered.append(tool.value)
    return filtered, changed


def _known_tool_name_mapping(value: object) -> tuple[dict[str, object], bool]:
    if value is None:
        return {}, False
    if not isinstance(value, dict):
        return {}, False
    filtered: dict[str, object] = {}
    changed = False
    for raw_tool_name, raw_settings in value.items():
        candidate = (
            raw_tool_name.value
            if isinstance(raw_tool_name, ToolName)
            else str(raw_tool_name).strip()
        )
        if not candidate:
            changed = True
            continue
        tool = ToolName._value2member_map_.get(candidate)
        if tool is None:
            changed = True
            continue
        filtered[tool.value] = raw_settings
    return filtered, changed
