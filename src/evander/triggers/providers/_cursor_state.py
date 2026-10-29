from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Protocol


class TriggerCursorRepository(Protocol):
    """Repository contract for persisted trigger cursor state."""

    def get(self, trigger_id: str) -> dict[str, Any]: ...

    def put(self, trigger_id: str, value: dict[str, Any]) -> None: ...

    def update(
        self,
        trigger_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]: ...


class TriggerCursorStore:
    """Atomic JSON cursor store keyed by trigger_id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._file_lock():
                if not self.path.exists():
                    self._atomic_write({})

    def get(self, trigger_id: str) -> dict[str, Any]:
        with self._lock:
            with self._file_lock():
                payload = self._read_raw()
                raw = payload.get(trigger_id)
                if isinstance(raw, dict):
                    return dict(raw)
                return {}

    def put(self, trigger_id: str, value: dict[str, Any]) -> None:
        with self._lock:
            with self._file_lock():
                payload = self._read_raw()
                payload[trigger_id] = value
                self._atomic_write(payload)

    def update(
        self,
        trigger_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        with self._lock:
            with self._file_lock():
                payload = self._read_raw()
                current = payload.get(trigger_id)
                state = dict(current) if isinstance(current, dict) else {}
                updated = updater(state)
                if updated is None:
                    updated = state
                if not isinstance(updated, dict):
                    raise TypeError("Trigger cursor updater must return a mapping or None")
                payload[trigger_id] = updated
                self._atomic_write(payload)
                return dict(updated)

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    @contextmanager
    def _file_lock(self) -> Iterable[None]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if os.geteuid() == 0:
                owner_source = self.path if self.path.exists() else self.path.parent
                owner_stat = owner_source.stat()
                os.fchown(lock_file.fileno(), owner_stat.st_uid, owner_stat.st_gid)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        owner_source = self.path if self.path.exists() else self.path.parent
        owner_stat = owner_source.stat()
        mode = (
            stat.S_IMODE(self.path.stat().st_mode) if self.path.exists() else _default_file_mode()
        )
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        tmp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, mode)
            if os.geteuid() == 0:
                os.chown(tmp_path, owner_stat.st_uid, owner_stat.st_gid)
            os.replace(tmp_path, self.path)
            dir_fd = os.open(
                self.path.parent,
                getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY,
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def _default_file_mode() -> int:
    current_umask = os.umask(0)
    try:
        return 0o666 & ~current_umask
    finally:
        os.umask(current_umask)


def merge_cursor_ids(
    existing: object,
    new_ids: Iterable[object],
    *,
    limit: int | None = None,
) -> list[str]:
    """Merge cursor ids while preserving order and removing duplicates."""

    merged: list[str] = []
    seen: set[str] = set()

    for value in _iter_text_values(existing):
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)

    for value in _iter_text_values(new_ids):
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)

    if limit is not None:
        merged = merged[-max(limit, 0) :]
    return merged


def latest_cursor_timestamp_text(
    existing: object,
    candidate: object | None,
) -> str | None:
    """Return the newer of the existing and candidate UTC timestamps."""

    current_dt = _parse_utc_datetime(existing)
    candidate_dt = _parse_utc_datetime(candidate)
    winner = current_dt
    if candidate_dt is not None and (winner is None or candidate_dt > winner):
        winner = candidate_dt
    if winner is None:
        return None
    return winner.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_cursor_timestamp_text(value: object) -> datetime | None:
    """Parse a cursor string as UTC, preserving errors for malformed timestamps."""

    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iter_text_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, Mapping) or not isinstance(value, Iterable):
        return
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            yield text


def _parse_utc_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
