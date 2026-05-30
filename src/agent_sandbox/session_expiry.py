from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class SessionExpiredError(ValueError):
    """Raised when a session has already expired."""


class _SessionLike(Protocol):
    session_id: str
    expires_at: datetime


def ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def session_expires_at_utc(session: _SessionLike) -> datetime:
    expires_at = getattr(session, "expires_at", None)
    if not isinstance(expires_at, datetime):
        raise TypeError("session expires_at must be a datetime")
    return ensure_utc_datetime(expires_at)


def is_session_expired(session: _SessionLike, *, now: datetime | None = None) -> bool:
    effective_now = ensure_utc_datetime(now or datetime.now(UTC))
    return session_expires_at_utc(session) <= effective_now


def session_expired_message(session: _SessionLike) -> str:
    session_id = str(getattr(session, "session_id", "") or "").strip() or "<unknown>"
    expires_at = _format_utc_datetime(session_expires_at_utc(session))
    return f"session expired: session_id={session_id} expires_at={expires_at}"


def ensure_session_not_expired(session: _SessionLike, *, now: datetime | None = None) -> None:
    if is_session_expired(session, now=now):
        raise SessionExpiredError(session_expired_message(session))


def _format_utc_datetime(value: datetime) -> str:
    return ensure_utc_datetime(value).isoformat().replace("+00:00", "Z")
