from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from evander.models import SessionManifest, ToolName
from evander.session_expiry import (
    SessionExpiredError,
    ensure_session_not_expired,
    ensure_utc_datetime,
    is_session_expired,
    session_expired_message,
    session_expires_at_utc,
)


def _manifest(*, expires_at: datetime) -> SessionManifest:
    return SessionManifest(
        session_id="session-1",
        vm_id="vm-1",
        allowed_tools=[ToolName.GET_AGENT_STATUS],
        expires_at=expires_at,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2030, 1, 1, 12, 30), datetime(2030, 1, 1, 12, 30, tzinfo=UTC)),
        (
            datetime(2030, 1, 1, 12, 30, tzinfo=timezone(timedelta(hours=-5))),
            datetime(2030, 1, 1, 17, 30, tzinfo=UTC),
        ),
    ],
)
def test_ensure_utc_datetime_normalizes_naive_and_aware_datetimes(
    value: datetime, expected: datetime
) -> None:
    assert ensure_utc_datetime(value) == expected


def test_session_expiry_message_uses_utc_and_does_not_mutate_manifest() -> None:
    expires_at = datetime(2030, 1, 1, 12, 30)
    manifest = _manifest(expires_at=expires_at)

    assert session_expires_at_utc(manifest) == datetime(2030, 1, 1, 12, 30, tzinfo=UTC)
    assert session_expired_message(manifest) == (
        "session expired: session_id=session-1 expires_at=2030-01-01T12:30:00Z"
    )
    assert manifest.expires_at == expires_at


def test_session_expiry_guard_raises_consistent_error_message() -> None:
    manifest = _manifest(expires_at=datetime(2030, 1, 1, 12, 30, tzinfo=timezone.utc))

    with pytest.raises(
        SessionExpiredError,
        match=r"session expired: session_id=session-1 expires_at=2030-01-01T12:30:00Z",
    ):
        ensure_session_not_expired(
            manifest,
            now=datetime(2030, 1, 1, 12, 31),
        )

    assert is_session_expired(manifest, now=datetime(2030, 1, 1, 12, 31, tzinfo=UTC))
