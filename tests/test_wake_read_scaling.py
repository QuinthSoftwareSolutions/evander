from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evander.messaging.wake_store import WakeRequestStore
from evander.models import WakeDispatchRecord, WakeScheduleDefinition, WakeScheduledRequest
from read_scaling_probe import ReadProbe

NOW = datetime(2026, 10, 9, 10, tzinfo=UTC)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def freeze_clock(monkeypatch):
    monkeypatch.setattr("evander.messaging.wake_store.datetime", FixedClock)


def request(request_id, *, session="session-one", status="queued", updated=NOW, enabled=True):
    return WakeScheduledRequest(
        request_id=request_id,
        session_id=session,
        schedule=WakeScheduleDefinition(resume_at=NOW - timedelta(minutes=1)),
        resume_prompt={"prompt": "Synthetic wake prompt"},
        status=status,
        created_at=NOW,
        updated_at=updated,
        next_run_at=NOW - timedelta(minutes=1),
        enabled=enabled,
    )


def dispatch(dispatch_id, request_id, *, status="failed", at=NOW, metadata=None):
    return WakeDispatchRecord(
        dispatch_id=dispatch_id,
        request_id=request_id,
        session_id="session-one",
        status=status,
        resume_prompt={"prompt": "Synthetic wake prompt"},
        dispatched_at=at,
        metadata=metadata or {},
    )


def seed(root, *, requests=0, dispatches=0):
    store = WakeRequestStore(root / "wake.db")
    with store._connect() as db:
        db.executemany(
            "INSERT INTO wake_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                store._request_row(
                    request(f"old-{i:05}", updated=NOW - timedelta(minutes=i + 1), enabled=False)
                )
                for i in range(requests)
            ],
        )
        db.executemany(
            "INSERT INTO wake_dispatches VALUES (?,?,?,?,?,?,?)",
            [
                store._dispatch_row(dispatch(f"old-dispatch-{i}", f"unrelated-{i}"))
                for i in range(dispatches)
            ],
        )
    return store


def test_single_wake_entry_uses_latest_valid_scoped_dispatch(tmp_path: Path):
    store = seed(tmp_path, dispatches=500)
    wake = request("wanted")
    with store._connect() as db:
        db.executemany(
            "INSERT INTO wake_dispatches VALUES (?,?,?,?,?,?,?)",
            [
                store._dispatch_row(dispatch("a", "wanted", status="queued")),
                store._dispatch_row(dispatch("b", "wanted", status="failed")),
                store._dispatch_row(dispatch("c", "wanted", at=NOW + timedelta(seconds=1))),
            ],
        )
        db.execute("UPDATE wake_dispatches SET payload_json='invalid' WHERE dispatch_id='c'")
    with ReadProbe() as probe:
        entry = store.ledger_entry_for_request(wake)
    assert entry["latest_dispatch"] == {
        "dispatch_id": "b",
        "status": "failed",
        "dispatched_at": "2026-10-09T10:00:00.000000Z",
        "source": None,
        "error": None,
    }
    assert probe.payload_rows["wake_dispatches"] <= 3
    assert probe.selects["wake_dispatches"] <= 2
    assert store.ledger_entry_for_request(request("missing"))["latest_dispatch"] is None


def test_due_wakes_load_only_candidate_dispatches_and_retain_capacity_deferral(tmp_path: Path):
    store = seed(tmp_path, requests=500, dispatches=500)
    store.upsert_request(request("due", status="pending"))
    store.upsert_request(request("deferred", status="pending"))
    with store._connect() as db:
        db.executemany(
            "INSERT INTO wake_dispatches VALUES (?,?,?,?,?,?,?)",
            [
                store._dispatch_row(dispatch("due-dispatch", "due", status="failed")),
                store._dispatch_row(
                    dispatch(
                        "deferred-dispatch",
                        "deferred",
                        status="queued",
                        metadata={"capacity_deferral": True},
                    )
                ),
            ],
        )
    with ReadProbe() as probe:
        due = store.find_due_requests(now_utc=NOW)
    assert [wake.request_id for wake in due] == ["due"]
    assert probe.payload_rows["wake_dispatches"] == 2


@pytest.mark.parametrize("session", [None, "session-one"])
def test_wake_ledger_reads_only_requested_page_before_projection(tmp_path: Path, session):
    store = seed(tmp_path, requests=500)
    with ReadProbe() as probe:
        entries = store.list_ledger_entries(session_id=session, limit=3)
    assert [entry["request_id"] for entry in entries] == ["old-00000", "old-00001", "old-00002"]
    assert all(entry["status"] == "queued" for entry in entries)
    assert probe.rows["wake_requests"] == 3
    assert probe.vm_steps < 500


def test_wake_ledger_fills_page_past_corrupt_and_filtered_requests(tmp_path: Path):
    store = seed(tmp_path)
    records = [
        request("a-other", session="session-other", updated=NOW + timedelta(seconds=6)),
        request("b-corrupt", updated=NOW + timedelta(seconds=5)),
        request(
            "c-complete", status="completed_or_acknowledged", updated=NOW + timedelta(seconds=4)
        ),
        request("d-pending", status="pending", updated=NOW + timedelta(seconds=3)),
        request("e-queued", updated=NOW + timedelta(seconds=2)),
        request("f-queued", updated=NOW + timedelta(seconds=1)),
    ]
    for record in records:
        store.upsert_request(record)
    with store._connect() as db:
        db.execute("UPDATE wake_requests SET payload_json='invalid' WHERE request_id='b-corrupt'")
    entries = store.list_ledger_entries(
        session_id="session-one", statuses=[" queued "], include_completed=False, limit=2
    )
    assert [entry["request_id"] for entry in entries] == ["e-queued", "f-queued"]
    assert store.list_ledger_entries(limit=0) == []
    assert store.list_ledger_entries(statuses=["unknown"]) == []


def test_dispatch_batches_preserve_more_than_one_batch_of_due_requests(tmp_path: Path):
    store = seed(tmp_path, dispatches=500)
    with store._connect() as db:
        db.executemany(
            "INSERT INTO wake_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [store._request_row(request(f"due-{i:04}", status="pending")) for i in range(405)],
        )
        db.executemany(
            "INSERT INTO wake_dispatches VALUES (?,?,?,?,?,?,?)",
            [store._dispatch_row(dispatch(f"dispatch-{i}", f"due-{i:04}")) for i in range(405)],
        )
    with ReadProbe() as probe:
        due = store.find_due_requests(now_utc=NOW)
    assert len(due) == 405
    assert [wake.request_id for wake in due[:2]] == ["due-0000", "due-0001"]
    assert due[-1].request_id == "due-0404"
    assert probe.payload_rows["wake_dispatches"] == 405
    assert probe.selects["wake_dispatches"] == 2


def test_all_corrupt_dispatches_do_not_hide_due_wakes(tmp_path: Path):
    store = seed(tmp_path)
    wake = request("due", status="pending")
    store.upsert_request(wake)
    with store._connect() as db:
        db.execute(
            "INSERT INTO wake_dispatches VALUES (?,?,?,?,?,?,?)",
            store._dispatch_row(dispatch("corrupt", "due")),
        )
        db.execute("UPDATE wake_dispatches SET payload_json='{}'")
    assert store.ledger_entry_for_request(wake)["latest_dispatch"] is None
    assert [item.request_id for item in store.find_due_requests(now_utc=NOW)] == ["due"]
