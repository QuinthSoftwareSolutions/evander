from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

import pytest

from agent_sandbox.messaging.wake_store import WakeRequestStore
from agent_sandbox.models import WakeDispatchRecord, WakeResumePromptPayload, WakeScheduleDefinition
from agent_sandbox.storage import JsonObjectStore


def test_schedule_resume_request_persists_expected_shape(tmp_path: Path) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")

    scheduled = store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume from pending feedback.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-1",
        source="gateway-tool",
        resume_mode="same_context",
        enabled=True,
        metadata={"trace_id": "trace-1"},
    )

    assert scheduled.request_id == "wake-1"
    payload = json.loads((tmp_path / "wake-store.json").read_text(encoding="utf-8"))
    assert payload["requests"]["wake-1"]["session_id"] == "session-1"
    assert payload["requests"]["wake-1"]["schedule"]["resume_at"] == "2026-04-01T10:30:00Z"
    assert (
        payload["requests"]["wake-1"]["resume_prompt"]["prompt"] == "Resume from pending feedback."
    )


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
    assert payload["requests"]["wake-1"]["enabled"] is False
    assert payload["requests"]["wake-1"]["last_dispatched_at"] == "2026-04-01T10:31:00Z"
    assert payload["dispatches"]["dispatch-1"]["status"] == "dispatched"
    assert payload["messages"]["wake:dispatch-1"]["session_id"] == "session-1"


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


def test_compaction_removes_old_disabled_requests_and_messages(tmp_path: Path) -> None:
    store = WakeRequestStore(tmp_path / "wake-store.json")
    old = datetime.now(UTC) - timedelta(days=120)
    store.upsert_request(
        store.schedule_resume_request(
            session_id="session-1",
            prompt="Resume work.",
            schedule=WakeScheduleDefinition(cron="0 * * * *"),
            request_id="wake-old",
            source="test",
            resume_mode="same_context",
            enabled=False,
            metadata={},
        )
    )

    payload = store.read_raw()
    payload["requests"]["wake-old"]["updated_at"] = old.isoformat().replace("+00:00", "Z")
    payload["dispatches"]["dispatch-old"] = {
        "dispatch_id": "dispatch-old",
        "session_id": "session-1",
        "status": "dispatched",
        "resume_prompt": {"prompt": "old", "metadata": {}},
        "dispatched_at": old.isoformat().replace("+00:00", "Z"),
    }
    payload["messages"]["wake:dispatch-old"] = {
        "message_id": "wake:dispatch-old",
        "session_id": "session-1",
        "status": "dispatched",
        "resume_prompt": {"prompt": "old", "metadata": {}},
        "created_at": old.isoformat().replace("+00:00", "Z"),
        "metadata": {},
    }
    (tmp_path / "wake-store.json").write_text(json.dumps(payload), encoding="utf-8")

    store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-new",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="new"),
        )
    )

    compacted = store.read_raw()
    assert "wake-old" not in compacted["requests"]
    assert "dispatch-old" not in compacted["dispatches"]
    assert "wake:dispatch-old" not in compacted["messages"]


def test_find_due_requests_advances_first_cron_run_and_sorts_results(tmp_path: Path) -> None:
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
    assert refreshed.next_run_at == datetime(2026, 4, 1, 10, 30, tzinfo=UTC)


def test_wake_request_store_initializes_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "wake-store.json"

    store = WakeRequestStore(path)

    assert store.read_raw() == {"requests": {}, "dispatches": {}, "messages": {}}


def test_wake_request_store_retains_independent_request_and_dispatch_updates_across_instances(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "wake-store.json"
    first_store = WakeRequestStore(path)
    second_store = WakeRequestStore(path)

    original_write = JsonObjectStore._write_json_payload

    def delayed_write(self, payload):
        time.sleep(0.05)
        return original_write(self, payload)

    monkeypatch.setattr(JsonObjectStore, "_write_json_payload", delayed_write)

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


@pytest.mark.parametrize("payload_text", ["", "{not json"])
def test_wake_request_store_raises_on_empty_or_invalid_json(
    tmp_path: Path,
    payload_text: str,
) -> None:
    path = tmp_path / "wake-store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload_text, encoding="utf-8")

    store = WakeRequestStore(path)

    with pytest.raises(json.JSONDecodeError):
        store.read_raw()

    with pytest.raises(json.JSONDecodeError):
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
