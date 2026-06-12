from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

import pytest

from agent_sandbox.cron import next_cron_time
from agent_sandbox.messaging.wake_store import WakeRequestStore
from agent_sandbox.models import (
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

    dispatch_time = datetime(2026, 4, 1, 10, 31, tzinfo=UTC)
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
    assert request.enabled is False
    assert request.last_dispatched_at == dispatch_time
    assert request.next_run_at is None
    assert payload["dispatches"]["dispatch-1"]["status"] == "dispatched"
    assert payload["messages"]["wake:dispatch-1"]["session_id"] == "session-1"


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
    ("dispatch_status", "expected_status"),
    [
        ("dispatched", "already_dispatched"),
        ("queued", "already_dispatched"),
        ("completed", "already_completed"),
    ],
)
def test_cancel_request_rejects_terminal_one_time_dispatches(
    tmp_path: Path, dispatch_status: str, expected_status: str
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
            dispatch_id=f"dispatch-{dispatch_status}",
            request_id="wake-1",
            session_id="session-1",
            status=dispatch_status,
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

    assert result.status == expected_status
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

    monkeypatch.setattr("agent_sandbox.messaging.wake_store.datetime", _FrozenDateTime)

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
