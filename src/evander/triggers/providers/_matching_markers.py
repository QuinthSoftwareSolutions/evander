"""Cursor-marker ordering for remote issue matching triggers."""

from __future__ import annotations

from datetime import UTC, datetime


def _marker_is_newer(candidate: str, previous: str) -> bool:
    if not previous:
        return True
    candidate_dt = _parse_dt(candidate)
    previous_dt = _parse_dt(previous)
    if candidate_dt is None or previous_dt is None:
        return candidate != previous
    return candidate_dt > previous_dt


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "__seen__":
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
