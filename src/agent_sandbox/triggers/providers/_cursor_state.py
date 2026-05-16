from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
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

    def update(
        self,
        trigger_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        with self._lock:
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

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


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
