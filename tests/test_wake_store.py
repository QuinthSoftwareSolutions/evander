from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

import pytest

from evander.cron import next_cron_time
from evander.messaging.wake_store import WakeRequestStore
from evander.models import (
    WakeDispatchRecord,
    WakeMessageEnvelope,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
    WakeScheduledRequest,
)


class _FrozenDateTime(datetime):
    frozen: datetime = datetime.now(UTC)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        if tz is None:
            return cls.frozen
        return cls.frozen.astimezone(tz)


@pytest.fixture(autouse=True)
def _freeze_wake_store_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _FrozenDateTime.frozen = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("evander.messaging.wake_store.datetime", _FrozenDateTime)


def test_wake_request_store_resolves_json_path_to_sibling_db_and_creates_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wake-store.json"
    store = WakeRequestStore(path)

    assert store.path == tmp_path / "wake-store.db"

    assert store.read_raw() == {"requests": {}, "dispatches": {}, "messages": {}}
    assert not path.exists()
    assert store.path.exists()

    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
            )
        }

    assert {"wake_requests", "wake_dispatches", "wake_messages"} <= tables
    assert {
        "idx_wake_requests_due",
        "idx_wake_requests_listing",
        "idx_wake_requests_retention",
        "idx_wake_dispatches_latest",
        "idx_wake_dispatches_retention",
        "idx_wake_messages_retention",
    } <= indexes


def test_wake_request_store_imports_legacy_json_into_sibling_db_and_leaves_json_untouched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wake-store.json"
    request = WakeScheduledRequest(
        request_id="wake-1",
        session_id="session-1",
        schedule=WakeScheduleDefinition(
            resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        ),
        resume_prompt=WakeResumePromptPayload(
            prompt="Resume from pending feedback.",
            metadata={"trace_id": "trace-1"},
        ),
        source="gateway-tool",
        created_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        last_dispatched_at=datetime(2026, 4, 1, 10, 1, tzinfo=UTC),
        next_run_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
    )
    dispatch = WakeDispatchRecord(
        dispatch_id="dispatch-1",
        request_id="wake-1",
        session_id="session-1",
        status="dispatched",
        resume_prompt=WakeResumePromptPayload(
            prompt="Resume from pending feedback.",
            metadata={"trace_id": "trace-1"},
        ),
        dispatched_at=datetime(2026, 4, 1, 10, 31, tzinfo=UTC),
        source="gateway-tool",
        metadata={"trace_id": "trace-1"},
    )
    message = WakeMessageEnvelope(
        message_id="wake:dispatch-1",
        request_id="wake-1",
        dispatch_id="dispatch-1",
        session_id="session-1",
        status="dispatched",
        resume_prompt=WakeResumePromptPayload(
            prompt="Resume from pending feedback.",
            metadata={"trace_id": "trace-1"},
        ),
        created_at=datetime(2026, 4, 1, 10, 31, tzinfo=UTC),
        metadata={"trace_id": "trace-1"},
    )
    legacy_payload = {
        "requests": {"wake-1": request.model_dump(mode="json")},
        "dispatches": {"dispatch-1": dispatch.model_dump(mode="json")},
        "messages": {"wake:dispatch-1": message.model_dump(mode="json")},
    }
    original_text = json.dumps(legacy_payload)
    path.write_text(original_text, encoding="utf-8")

    store = WakeRequestStore(path)
    assert store.path == tmp_path / "wake-store.db"

    assert store.read_raw() == legacy_payload
    assert store.list_requests() == [request]
    assert store.latest_dispatch_by_request_id() == {"wake-1": dispatch}
    assert path.read_text(encoding="utf-8") == original_text


@pytest.mark.parametrize(
    ("raw_status", "expected_status"),
    [
        (None, "pending"),
        ("", "pending"),
        ("  ", "pending"),
        ("  missed  ", "missed"),
    ],
)
def test_wake_scheduled_request_normalizes_legacy_status_values(
    raw_status: object,
    expected_status: str,
) -> None:
    request = WakeScheduledRequest.model_validate(
        {
            "request_id": "wake-legacy-status",
            "session_id": "session-1",
            "schedule": {"resume_at": datetime(2026, 4, 1, 10, 30, tzinfo=UTC).isoformat()},
            "resume_prompt": {"prompt": "Resume work."},
            "status": raw_status,
        }
    )

    assert request.status == expected_status


def test_wake_scheduled_request_backfills_legacy_retry_state_defaults() -> None:
    request = WakeScheduledRequest.model_validate(
        {
            "request_id": "wake-legacy-retry-state",
            "session_id": "session-1",
            "schedule": {"resume_at": datetime(2026, 4, 1, 10, 30, tzinfo=UTC).isoformat()},
            "resume_prompt": {"prompt": "Resume work."},
            "status": "pending",
        }
    )

    assert request.auto_dispatch_attempts == 0
    assert request.auto_retry_started_at is None
    assert request.auto_retry_last_backoff_seconds is None
    assert request.auto_retry_exhausted_at is None
    assert request.directory_path == "root"


def test_wake_request_store_skips_legacy_import_when_db_already_has_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wake-store.json"
    store = WakeRequestStore(path)
    seeded_request = store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-seeded",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    legacy_request = WakeScheduledRequest(
        request_id="wake-import",
        session_id="session-2",
        schedule=WakeScheduleDefinition(
            resume_at=datetime(2026, 4, 1, 11, 0, tzinfo=UTC),
        ),
        resume_prompt=WakeResumePromptPayload(prompt="Imported request."),
        created_at=datetime(2026, 4, 1, 10, 5, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, 10, 5, tzinfo=UTC),
        next_run_at=datetime(2026, 4, 1, 11, 0, tzinfo=UTC),
    )
    legacy_dispatch = WakeDispatchRecord(
        dispatch_id="dispatch-import",
        request_id="wake-import",
        session_id="session-2",
        status="dispatched",
        resume_prompt=WakeResumePromptPayload(prompt="Imported request."),
        dispatched_at=datetime(2026, 4, 1, 11, 1, tzinfo=UTC),
    )
    legacy_message = WakeMessageEnvelope(
        message_id="wake:dispatch-import",
        request_id="wake-import",
        dispatch_id="dispatch-import",
        session_id="session-2",
        status="dispatched",
        resume_prompt=WakeResumePromptPayload(prompt="Imported request."),
        created_at=datetime(2026, 4, 1, 11, 1, tzinfo=UTC),
    )
    legacy_payload = {
        "requests": {"wake-import": legacy_request.model_dump(mode="json")},
        "dispatches": {"dispatch-import": legacy_dispatch.model_dump(mode="json")},
        "messages": {"wake:dispatch-import": legacy_message.model_dump(mode="json")},
    }
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    reopened = WakeRequestStore(path)
    payload = reopened.read_raw()

    assert payload["requests"] == {"wake-seeded": seeded_request.model_dump(mode="json")}
    assert "wake-import" not in payload["requests"]
    assert "dispatch-import" not in payload["dispatches"]
    assert "wake:dispatch-import" not in payload["messages"]
    assert path.read_text(encoding="utf-8") == json.dumps(legacy_payload)


def test_record_dispatch_updates_request_and_appends_message(tmp_path: Path) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    dispatch_time = datetime.now(UTC)
    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-1",
            request_id="wake-1",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=dispatch_time,
        )
    )

    payload = store.read_raw()
    request = store.get_request("wake-1")

    assert request is not None
    assert request.enabled is True
    assert request.status == "dispatched"
    assert request.last_dispatched_at == dispatch_time
    assert request.next_run_at == scheduled_for
    assert request.completed_at is None
    assert request.completion_evidence == {}
    assert payload["dispatches"]["dispatch-1"]["status"] == "dispatched"
    assert payload["messages"]["wake:dispatch-1"]["session_id"] == "session-1"

    ledger_entry = store.list_ledger_entries(session_id="session-1")[0]
    assert ledger_entry["status"] == "dispatched"
    assert ledger_entry["confirmation_state"] == "awaiting_confirmation"


@pytest.mark.parametrize("status", ["failed", "timed_out"])
def test_record_dispatch_preserves_due_request_for_failed_dispatches(
    tmp_path: Path, status: str
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    due_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(cron="* * * * *"),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    request = store.get_request("wake-1")
    assert request is not None
    request.next_run_at = due_time
    store.upsert_request(request)

    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id=f"dispatch-{status}",
            request_id="wake-1",
            session_id="session-1",
            status=status,
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=due_time,
        )
    )

    payload = store.read_raw()
    latest = store.latest_dispatch_by_request_id()["wake-1"]

    assert payload["requests"]["wake-1"]["enabled"] is True
    assert payload["requests"]["wake-1"]["next_run_at"] == "2026-04-01T10:30:00Z"
    assert payload["requests"]["wake-1"]["last_dispatched_at"] is None
    assert payload["dispatches"][f"dispatch-{status}"]["status"] == status
    assert payload["messages"][f"wake:dispatch-{status}"]["status"] == status
    assert latest.status == status
    assert latest.request_id == "wake-1"


def test_record_dispatch_keeps_failed_timestamp_cleared_until_success(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    due_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    success_time = datetime(2026, 4, 1, 10, 31, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(cron="* * * * *"),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    request = store.get_request("wake-1")
    assert request is not None
    request.next_run_at = due_time
    store.upsert_request(request)

    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-failed",
            request_id="wake-1",
            session_id="session-1",
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=due_time,
        )
    )
    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-success",
            request_id="wake-1",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=success_time,
        )
    )

    payload = store.read_raw()
    latest = store.latest_dispatch_by_request_id()["wake-1"]

    assert payload["requests"]["wake-1"]["last_dispatched_at"] == "2026-04-01T10:31:00Z"
    assert latest.status == "dispatched"
    assert latest.dispatched_at == success_time


def test_wake_request_store_imports_legacy_requests_with_lifecycle_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wake-store.json"
    legacy_payload = {
        "requests": {
            "wake-legacy": {
                "request_id": "wake-legacy",
                "session_id": "session-1",
                "schedule": {
                    "resume_at": "2026-04-01T10:30:00Z",
                },
                "resume_prompt": {
                    "prompt": "Resume work.",
                    "metadata": {},
                },
                "resume_mode": "same_context",
                "enabled": True,
                "source": "gateway-tool",
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
                "last_dispatched_at": None,
                "next_run_at": "2026-04-01T10:30:00Z",
            }
        },
        "dispatches": {},
        "messages": {},
    }
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    store = WakeRequestStore(path)
    request = store.get_request("wake-legacy")

    assert request is not None
    assert request.status == "pending"
    assert request.status_reason is None
    assert request.status_updated_at is None
    assert request.last_dispatch_id is None
    assert request.last_dispatch_status is None
    assert request.last_error is None
    assert request.last_error_at is None
    assert request.completed_at is None
    assert request.completion_evidence == {}


@pytest.mark.parametrize(
    (
        "dispatch_status",
        "expected_request_status",
        "expected_last_dispatched_at",
        "expected_enabled",
    ),
    [
        ("dispatched", "dispatched", True, True),
        ("queued", "queued", False, True),
        ("failed", "failed", False, True),
        ("timed_out", "failed", False, True),
    ],
)
def test_record_dispatch_updates_request_lifecycle_state(
    tmp_path: Path,
    dispatch_status: str,
    expected_request_status: str,
    expected_last_dispatched_at: bool,
    expected_enabled: bool,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    scheduled_for = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    dispatch_time = datetime(2026, 4, 1, 10, 31, tzinfo=UTC)
    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id=f"dispatch-{dispatch_status}",
            request_id="wake-1",
            session_id="session-1",
            status=dispatch_status,
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=dispatch_time,
            error="stack trace\nsecret token"
            if dispatch_status in {"failed", "timed_out"}
            else None,
        )
    )

    request = store.get_request("wake-1")
    assert request is not None
    assert request.status == expected_request_status
    assert request.last_dispatch_id == f"dispatch-{dispatch_status}"
    assert request.last_dispatch_status == dispatch_status
    assert request.enabled is expected_enabled
    if expected_last_dispatched_at:
        assert request.last_dispatched_at == dispatch_time
    else:
        assert request.last_dispatched_at is None
    if dispatch_status in {"failed", "timed_out"}:
        assert request.last_error is not None
        assert "\n" not in request.last_error
        assert request.last_error_at == dispatch_time
    else:
        assert request.last_error is None
        assert request.last_error_at is None


def test_mark_request_due_or_missed_refreshes_due_and_stale_dispatching_requests(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    now = datetime(2026, 4, 1, 10, 40, tzinfo=UTC)
    grace = timedelta(minutes=5)

    store.schedule_resume_request(
        session_id="session-due",
        prompt="Resume due work.",
        schedule=WakeScheduleDefinition(resume_at=now - timedelta(minutes=2)),
        request_id="wake-due",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    stale_request = store.schedule_resume_request(
        session_id="session-stale",
        prompt="Resume stale work.",
        schedule=WakeScheduleDefinition(resume_at=now - timedelta(minutes=10)),
        request_id="wake-stale",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    stale_request.status = "dispatching"
    stale_request.status_reason = None
    stale_request.status_updated_at = now - timedelta(minutes=20)
    stale_request.last_dispatch_id = "dispatch-stale"
    store.upsert_request(stale_request)

    refreshed = store.mark_request_due_or_missed(now_utc=now, late_after=grace)

    assert {request.request_id for request in refreshed} == {"wake-due", "wake-stale"}
    due = store.get_request("wake-due")
    stale = store.get_request("wake-stale")
    assert due is not None
    assert due.status == "due"
    assert due.status_reason is None
    assert stale is not None
    assert stale.status == "missed_or_late"
    assert stale.status_reason is not None
    assert "dispatch" in stale.status_reason.casefold()
    assert stale.last_error is not None


def test_mark_request_due_or_missed_overwrites_stale_last_error_reason(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    now = datetime(2026, 4, 1, 10, 40, tzinfo=UTC)

    request = store.schedule_resume_request(
        session_id="session-stale",
        prompt="Resume stale work.",
        schedule=WakeScheduleDefinition(resume_at=now - timedelta(minutes=10)),
        request_id="wake-stale",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    request.status = "dispatching"
    request.status_reason = None
    request.status_updated_at = now - timedelta(minutes=20)
    request.last_dispatch_id = "dispatch-stale"
    request.last_error = "older failure text"
    request.last_error_at = now - timedelta(minutes=30)
    store.upsert_request(request)

    refreshed = store.mark_request_due_or_missed(now_utc=now, late_after=timedelta(minutes=5))
    assert [item.request_id for item in refreshed] == ["wake-stale"]

    updated = store.get_request("wake-stale")
    assert updated is not None
    assert updated.status == "missed_or_late"
    assert updated.last_error is not None
    assert "older failure text" not in updated.last_error
    assert "dispatch" in updated.last_error.casefold()
    assert updated.last_error_at == now


def test_mark_request_due_or_missed_recovers_stale_unconfirmed_dispatch(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    now = datetime(2026, 4, 1, 10, 40, tzinfo=UTC)
    due_at = now - timedelta(minutes=10)
    dispatch_at = now - timedelta(minutes=8)
    store.schedule_resume_request(
        session_id="session-stale",
        prompt="Resume stale work.",
        schedule=WakeScheduleDefinition(resume_at=due_at),
        request_id="wake-stale-dispatch",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-stale",
            request_id="wake-stale-dispatch",
            session_id="session-stale",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume stale work."),
            dispatched_at=dispatch_at,
        )
    )
    request = store.get_request("wake-stale-dispatch")
    assert request is not None
    request.next_run_at = now - timedelta(minutes=1)
    store.upsert_request(request)

    refreshed = store.mark_request_due_or_missed(now_utc=now, late_after=timedelta(0))

    assert [item.request_id for item in refreshed] == ["wake-stale-dispatch"]
    updated = store.get_request("wake-stale-dispatch")
    assert updated is not None
    assert updated.enabled is True
    assert updated.status == "missed_or_late"
    assert updated.status_reason is not None
    assert "not confirmed" in updated.status_reason
    assert updated.last_error == updated.status_reason
    assert updated.completed_at is None


def test_mark_completed_by_dispatch_id_and_list_ledger_entries_include_operator_summary(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    scheduled_for = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume the plan and keep the private details hidden.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    dispatch_time = datetime(2026, 4, 1, 10, 31, tzinfo=UTC)
    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-1",
            request_id="wake-1",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(
                prompt="Resume the plan and keep the private details hidden."
            ),
            dispatched_at=dispatch_time,
            metadata={"trace_id": "trace-1"},
        )
    )

    completed_at = datetime(2026, 4, 1, 10, 45, tzinfo=UTC)
    completed = store.mark_completed_by_dispatch_id(
        "dispatch-1",
        completed_at=completed_at,
        evidence={
            "trigger_event_id": "event-1",
            "trigger_type": "agent_run_completed",
            "vm_name": "vm-1",
            "session_id": "session-1",
            "dispatch_id": "dispatch-1",
        },
    )

    assert completed is not None
    assert completed.status == "completed_or_acknowledged"
    assert completed.completed_at == completed_at
    assert completed.completion_evidence["dispatch_id"] == "dispatch-1"
    assert completed.last_dispatch_id == "dispatch-1"
    assert completed.last_dispatch_status == "dispatched"
    assert completed.enabled is False
    assert completed.next_run_at is None

    entries = store.list_ledger_entries(session_id="session-1")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "completed_or_acknowledged"
    assert entry["retry_available"] is False
    assert entry["prompt_summary"].startswith("Resume the plan")
    assert entry["redacted_prompt"] == "[redacted]"
    assert entry["latest_dispatch"]["status"] == "dispatched"
    assert entry["failure_reason"] is None

    failure_store = WakeRequestStore(tmp_path / "failure-store.json")
    failure_store.schedule_resume_request(
        session_id="session-2",
        prompt="Resume after failure.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-failure",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    failure_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-failure",
            request_id="wake-failure",
            session_id="session-2",
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt="Resume after failure."),
            dispatched_at=dispatch_time,
            error="first line\nsecond line with token=secret",
        )
    )

    failure_entry = failure_store.list_ledger_entries(session_id="session-2")[0]
    assert failure_entry["status"] == "failed"
    assert failure_entry["retry_available"] is True
    assert "\n" not in (failure_entry["failure_reason"] or "")
    assert "second line" in (failure_entry["failure_reason"] or "")
    assert "token=secret" not in (failure_entry["failure_reason"] or "")
    assert "token=[redacted]" in (failure_entry["failure_reason"] or "")


def test_list_ledger_entries_returns_no_rows_for_nonpositive_limit(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    assert store.list_ledger_entries(limit=0) == []


def test_cancel_request_marks_request_cancelled_and_is_idempotent(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    due_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=due_time),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    result = store.cancel_request(
        "wake-1",
        caller_session_id="session-1",
        reason="obsolete wake",
        metadata={"source": "test"},
        now_utc=due_time,
    )

    assert result.status == "cancelled"
    assert result.request is not None
    assert result.request.request_id == "wake-1"
    assert result.request.status == "cancelled"
    assert result.request.enabled is False
    assert result.request.next_run_at is None
    assert result.request.cancelled_at == due_time
    assert result.request.cancelled_by_session_id == "session-1"
    assert result.request.cancel_reason == "obsolete wake"
    assert result.request.cancel_metadata == {"source": "test"}
    assert result.request.updated_at == due_time

    payload = store.read_raw()
    assert payload["requests"]["wake-1"]["status"] == "cancelled"
    assert payload["requests"]["wake-1"]["cancelled_at"] == "2026-04-01T10:30:00Z"
    assert payload["requests"]["wake-1"]["cancelled_by_session_id"] == "session-1"
    assert payload["requests"]["wake-1"]["cancel_reason"] == "obsolete wake"
    assert payload["requests"]["wake-1"]["cancel_metadata"] == {"source": "test"}
    assert store.list_requests(include_disabled=False) == []
    assert store.find_due_requests(now_utc=due_time + timedelta(minutes=1)) == []

    repeated = store.cancel_request(
        "wake-1",
        caller_session_id="session-1",
        reason="obsolete wake",
        metadata={"source": "test"},
        now_utc=due_time + timedelta(minutes=5),
    )

    assert repeated.status == "already_cancelled"
    assert repeated.request is not None
    assert repeated.request.cancelled_at == due_time
    assert repeated.request.cancelled_by_session_id == "session-1"


def test_find_due_requests_ignores_cancelled_request_even_if_enabled(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    due_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=due_time),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    store.cancel_request(
        "wake-1",
        caller_session_id="session-1",
        reason="obsolete wake",
        metadata={"source": "test"},
        now_utc=due_time,
    )
    cancelled = store.get_request("wake-1")
    assert cancelled is not None
    stale_payload = cancelled.model_copy(
        update={"enabled": True, "next_run_at": due_time},
        deep=True,
    ).model_dump(mode="json")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE wake_requests
            SET enabled = 1,
                next_run_at = ?,
                payload_json = ?
            WHERE request_id = ?
            """,
            (
                WakeRequestStore._datetime_to_storage(due_time),
                json.dumps(stale_payload, sort_keys=True, separators=(",", ":")),
                "wake-1",
            ),
        )

    assert store.find_due_requests(now_utc=due_time + timedelta(minutes=1)) == []
    assert store.list_requests(include_disabled=False) == []
    refreshed = store.get_request("wake-1")
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    assert refreshed.enabled is False
    assert refreshed.next_run_at is None


def test_cancel_request_denies_cross_session_without_mutation(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    before = store.read_raw()
    result = store.cancel_request(
        "wake-1",
        caller_session_id="session-2",
        reason="not mine",
        metadata={"source": "test"},
    )

    assert result.status == "permission_denied"
    assert result.request is None
    assert store.read_raw() == before


@pytest.mark.parametrize(
    "request_state",
    [
        "dispatching",
        "queued",
        "dispatched",
        "failed",
        "missed_or_late",
    ],
)
def test_cancel_request_allows_nonterminal_one_time_dispatches(
    tmp_path: Path, request_state: str
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    due_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=due_time),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    if request_state == "dispatching":
        current_request = store.mark_request_dispatching(
            "wake-1",
            now_utc=due_time,
            reason="dispatch attempt started",
        )
    elif request_state == "missed_or_late":
        current_request = store.get_request("wake-1")
        assert current_request is not None
        current_request.status = "missed_or_late"
        current_request.enabled = False
        current_request.next_run_at = None
        current_request.status_reason = "Scheduled wake missed its window."
        current_request.status_updated_at = due_time
        current_request.updated_at = due_time
        store.upsert_request(current_request)
        current_request = store.get_request("wake-1")
    else:
        store.record_dispatch(
            WakeDispatchRecord(
                dispatch_id=f"dispatch-{request_state}",
                request_id="wake-1",
                session_id="session-1",
                status=request_state,
                resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
                dispatched_at=due_time,
            )
        )
        current_request = store.get_request("wake-1")

    before = store.read_raw()
    assert current_request is not None

    result = store.cancel_request(
        "wake-1",
        caller_session_id="session-1",
        reason="obsolete wake",
        metadata={"source": "test"},
        now_utc=due_time + timedelta(minutes=1),
    )

    assert result.status == "cancelled"
    assert result.request is not None
    assert result.request.status == "cancelled"
    assert result.request.enabled is False
    assert result.request.next_run_at is None
    assert result.request.cancelled_by_session_id == "session-1"
    assert result.request.cancel_reason == "obsolete wake"
    assert result.request.cancel_metadata == {"source": "test"}

    saved_request = store.get_request("wake-1")
    assert saved_request is not None
    assert saved_request.status == "cancelled"
    assert saved_request.enabled is False
    assert saved_request.next_run_at is None
    assert saved_request.cancelled_by_session_id == "session-1"
    assert saved_request.cancel_reason == "obsolete wake"
    assert saved_request.cancel_metadata == {"source": "test"}
    if request_state in {"queued", "dispatched", "failed"}:
        assert saved_request.last_dispatch_status == request_state
        assert saved_request.last_dispatch_id == f"dispatch-{request_state}"
    assert store.read_raw() != before


def test_cancel_request_rejects_completed_one_time_dispatches(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    due_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=due_time),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-completed",
            request_id="wake-1",
            session_id="session-1",
            status="completed",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=due_time,
        )
    )
    before = store.read_raw()
    current_request = store.get_request("wake-1")
    assert current_request is not None

    result = store.cancel_request(
        "wake-1",
        caller_session_id="session-1",
        reason="obsolete wake",
        metadata={"source": "test"},
        now_utc=due_time + timedelta(minutes=1),
    )

    assert result.status == "already_completed"
    assert result.request is not None
    assert result.request == current_request
    assert store.read_raw() == before


def test_cancel_request_allows_recurring_cron_after_prior_dispatch(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    due_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(cron="* * * * *"),
        request_id="wake-cron",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    request = store.get_request("wake-cron")
    assert request is not None
    request.next_run_at = due_time
    store.upsert_request(request)
    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-cron",
            request_id="wake-cron",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=due_time,
        )
    )

    result = store.cancel_request(
        "wake-cron",
        caller_session_id="session-1",
        reason="obsolete wake",
        metadata={"source": "test"},
        now_utc=due_time + timedelta(minutes=1),
    )

    assert result.status == "cancelled"
    assert result.request is not None
    assert result.request.status == "cancelled"
    assert result.request.enabled is False
    assert result.request.next_run_at is None
    assert result.request.last_dispatched_at == due_time
    assert store.find_due_requests(now_utc=due_time + timedelta(minutes=2)) == []


@pytest.mark.parametrize(
    ("request_id", "expected_status"),
    [
        ("", "missing_request_id"),
        ("wake-missing", "not_found"),
    ],
)
def test_cancel_request_reports_missing_or_unknown_request_ids(
    tmp_path: Path, request_id: str, expected_status: str
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")

    result = store.cancel_request(
        request_id,
        caller_session_id="session-1",
        reason="obsolete wake",
        metadata={"source": "test"},
    )

    assert result.status == expected_status
    assert result.request is None


def test_schedule_resume_request_rejects_duplicate_request_id(tmp_path: Path) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    schedule = WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC))

    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=schedule,
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    with pytest.raises(ValueError, match="Wake request already exists: wake-1"):
        store.schedule_resume_request(
            session_id="session-2",
            prompt="Overwrite attempt.",
            schedule=schedule,
            request_id="wake-1",
            source="gateway-tool",
            resume_mode="same_context",
            enabled=True,
            metadata={},
        )


def test_compaction_removes_old_disabled_requests_dispatches_and_messages_via_repository_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "wake-store.json"
    old_now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    current_now = old_now + timedelta(days=91)

    monkeypatch.setattr("evander.messaging.wake_store.datetime", _FrozenDateTime)

    _FrozenDateTime.frozen = old_now
    store = WakeRequestStore(path)
    store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=old_now + timedelta(minutes=30)),
        request_id="wake-old",
        source="test",
        resume_mode="same_context",
        enabled=False,
        metadata={},
    )
    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-old",
            request_id="wake-old",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="old"),
            dispatched_at=old_now,
        )
    )

    _FrozenDateTime.frozen = current_now
    store.schedule_resume_request(
        session_id="session-2",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=current_now + timedelta(minutes=30)),
        request_id="wake-new",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    compacted = store.read_raw()
    assert "wake-old" not in compacted["requests"]
    assert "dispatch-old" not in compacted["dispatches"]
    assert "wake:dispatch-old" not in compacted["messages"]
    assert "wake-new" in compacted["requests"]


def test_find_due_requests_advances_first_cron_run_and_sorts_results(
    tmp_path: Path,
) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    now = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)

    later = store.schedule_resume_request(
        session_id="session-2",
        prompt="Later wake",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 29, tzinfo=UTC)),
        request_id="wake-later",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    store.upsert_request(later)
    cron = store.schedule_resume_request(
        session_id="session-1",
        prompt="Cron wake",
        schedule=WakeScheduleDefinition(cron="*/15 * * * 0-6"),
        request_id="wake-cron",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    store.upsert_request(cron)

    due = store.find_due_requests(now_utc=now)

    assert [request.request_id for request in due] == ["wake-later", "wake-cron"]
    refreshed = store.get_request("wake-cron")
    assert refreshed is not None
    assert refreshed.next_run_at == next_cron_time("*/15 * * * 0-6", now - timedelta(minutes=1))


@pytest.mark.parametrize("legacy_text", [None, "", "   \n"])
def test_wake_request_store_treats_missing_or_empty_legacy_json_as_empty_store(
    tmp_path: Path, legacy_text: str | None
) -> None:
    path = tmp_path / "wake-store.json"
    if legacy_text is not None:
        path.write_text(legacy_text, encoding="utf-8")

    store = WakeRequestStore(path)

    assert store.read_raw() == {"requests": {}, "dispatches": {}, "messages": {}}


@pytest.mark.parametrize(
    ("legacy_text", "expected_exception"),
    [
        ("{not json", json.JSONDecodeError),
        (json.dumps(["not", "an", "object"]), ValueError),
    ],
)
def test_wake_request_store_rejects_invalid_legacy_payload_when_db_is_empty(
    tmp_path: Path,
    legacy_text: str,
    expected_exception: type[Exception],
) -> None:
    path = tmp_path / "wake-store.json"
    path.write_text(legacy_text, encoding="utf-8")

    with pytest.raises(expected_exception):
        WakeRequestStore(path)

    db_path = tmp_path / "wake-store.db"
    with sqlite3.connect(db_path) as connection:
        counts = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT 'wake_requests' AS table_name, COUNT(*) AS row_count
                FROM wake_requests
                UNION ALL
                SELECT 'wake_dispatches' AS table_name, COUNT(*) AS row_count
                FROM wake_dispatches
                UNION ALL
                SELECT 'wake_messages' AS table_name, COUNT(*) AS row_count
                FROM wake_messages
                """
            )
        }

    assert counts == {
        "wake_requests": 0,
        "wake_dispatches": 0,
        "wake_messages": 0,
    }


def test_wake_request_store_rejects_invalid_legacy_wake_records_when_db_is_empty(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wake-store.json"
    payload = {
        "requests": {
            "wake-1": {
                "request_id": "wake-1",
            }
        },
        "dispatches": {},
        "messages": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception):
        WakeRequestStore(path)

    db_path = tmp_path / "wake-store.db"
    with sqlite3.connect(db_path) as connection:
        counts = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT 'wake_requests' AS table_name, COUNT(*) AS row_count
                FROM wake_requests
                UNION ALL
                SELECT 'wake_dispatches' AS table_name, COUNT(*) AS row_count
                FROM wake_dispatches
                UNION ALL
                SELECT 'wake_messages' AS table_name, COUNT(*) AS row_count
                FROM wake_messages
                """
            )
        }

    assert counts == {
        "wake_requests": 0,
        "wake_dispatches": 0,
        "wake_messages": 0,
    }


def test_wake_request_store_skips_invalid_legacy_json_after_db_is_seeded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wake-store.json"
    store = WakeRequestStore(path)
    seeded = store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-seeded",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    path.write_text("{not json", encoding="utf-8")

    reopened = WakeRequestStore(path)
    payload = reopened.read_raw()

    assert payload["requests"] == {"wake-seeded": seeded.model_dump(mode="json")}
    assert path.read_text(encoding="utf-8") == "{not json"


def test_wake_request_store_retains_independent_request_and_dispatch_updates_across_instances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wake-store.json"
    first_store = WakeRequestStore(path)
    second_store = WakeRequestStore(path)

    barrier = Barrier(3)

    def schedule_request() -> None:
        barrier.wait(timeout=5)
        first_store.schedule_resume_request(
            session_id="session-1",
            prompt="Resume work.",
            schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
            request_id="wake-1",
            source="gateway-tool",
            resume_mode="same_context",
            enabled=True,
            metadata={},
        )

    def record_dispatch() -> None:
        barrier.wait(timeout=5)
        second_store.record_dispatch(
            WakeDispatchRecord(
                dispatch_id="dispatch-1",
                session_id="session-2",
                status="dispatched",
                resume_prompt=WakeResumePromptPayload(prompt="Dispatch work."),
                dispatched_at=datetime(2026, 4, 1, 10, 31, tzinfo=UTC),
            )
        )

    threads = [Thread(target=schedule_request), Thread(target=record_dispatch)]

    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    for thread in threads:
        assert not thread.is_alive()

    payload = WakeRequestStore(path).read_raw()
    assert payload["requests"]["wake-1"]["session_id"] == "session-1"
    assert payload["dispatches"]["dispatch-1"]["session_id"] == "session-2"
    assert payload["messages"]["wake:dispatch-1"]["status"] == "dispatched"
