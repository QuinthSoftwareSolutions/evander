"""Cleanup must progress through age indexes even at a one-record batch limit."""

import json
from contextlib import contextmanager

import pytest

from evander.repositories import orchestration_db as db

OLD = "2026-08-01T00:00:00.000000Z"
CUTOFF = "2026-09-01T00:00:00.000000Z"
RECENT = "2026-09-15T00:00:00.000000Z"
PHASES = {
    "workflow": ("workflow_events", "occurred_at", "workflow_events_deleted", "compact"),
    "internal": ("internal_events", "created_at", "internal_events_deleted", "scheduled"),
    "scheduled": ("scheduled_internal_events", "published_at", "scheduled_internal_events_deleted", None),
}


def _seed(connection, phase, prefix, count, at):
    if phase == "workflow":
        sql = """INSERT INTO workflow_events(event_id,event_type,source,occurred_at,payload_json)
            VALUES (?,'test','test',?,'{}')"""
    elif phase == "internal":
        sql = """INSERT INTO internal_events(event_id,event_type,created_at,payload_json)
            VALUES (?,'test',?,'{}')"""
    else:
        sql = """INSERT INTO scheduled_internal_events
            (schedule_id,event_type,run_at,status,created_at,published_at,payload_json)
            VALUES (?,'test',?,'published',?,?,'{}')"""
    connection.executemany(
        sql,
        ((f"{prefix}-{i:05d}", *((at,) * (3 if phase == "scheduled" else 1))) for i in range(count)),
    )


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("expired_count", [0, 5000])
def test_cleanup_selection_stays_bounded_with_recent_or_expired_backlogs(
    tmp_path, monkeypatch, phase, expired_count,
):
    path = tmp_path / "orchestration.db"
    db.ensure_orchestration_schema(path)
    with db.connect_orchestration_db(path) as connection:
        # Insert recent rows first so a row-ID scan must cross the whole recent
        # prefix. A large expired range also makes sorting before LIMIT fail.
        _seed(connection, phase, "recent", 5000, RECENT)
        _seed(connection, phase, "expired", expired_count, OLD)
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_progress',?)",
            (json.dumps({"date": "2026-09-30", "phase": phase,
                         "cutoff": CUTOFF, "compact_cutoff": OLD}),),
        )
    original = db.connect_orchestration_db
    steps = 0

    def budget():
        nonlocal steps
        steps += 100
        return int(steps > 10_000)

    @contextmanager
    def bounded(path, **kwargs):
        with original(path, **kwargs) as connection:
            connection.set_progress_handler(budget, 100)
            try:
                yield connection
            finally:
                connection.set_progress_handler(None, 0)

    monkeypatch.setattr(db, "connect_orchestration_db", bounded)
    result = db.run_orchestration_retention_slice(
        path, retention_days=30, compact_history_retention_days=365,
        batch_size=1, time_budget_seconds=5,
    )
    table, age, result_field, next_phase = PHASES[phase]
    assert getattr(result, result_field) == int(expired_count > 0)
    assert result.skipped_reason is None and steps <= 10_000
    with db.connect_orchestration_db_read_only(path) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {age}>=?", (CUTOFF,)).fetchone()[0] == 5000
        assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {age}<?", (CUTOFF,)).fetchone()[0] == max(0, expired_count - 1)
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_progress'"
        ).fetchone()
        if expired_count:
            assert json.loads(row[0])["phase"] == phase
        elif next_phase is None:
            assert row is None and not result.remaining
        else:
            assert json.loads(row[0])["phase"] == next_phase
