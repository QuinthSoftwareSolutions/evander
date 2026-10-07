"""Payload cleanup must finish bounded sweeps even when few blobs are orphaned."""

import json
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from evander.repositories import orchestration_db as db
from evander.repositories.sqlite_runtime import (
    SqliteOperationDeadlineExceeded,
    close_sqlite_runtimes,
)


def _key(index):
    return f"{index:064x}"


def _insert_blobs(connection, keys, at):
    payload = zlib.compress(b"{}")
    connection.executemany(
        """INSERT INTO workflow_execution_payload_blobs
           (sha256,codec,original_bytes,compressed_bytes,payload_blob,created_at)
           VALUES (?,'zlib',2,?,?,?)""",
        ((key, len(payload), payload, at) for key in keys),
    )


def _seed(path, *, retained=6000, orphaned=3):
    db.ensure_orchestration_schema(path)
    now = datetime.now(UTC)
    recent = db.datetime_to_storage(now)
    old = db.datetime_to_storage(now - timedelta(days=60))
    with db.connect_orchestration_db(path) as connection:
        _insert_blobs(connection, (_key(i) for i in range(retained + orphaned + 1)), recent)
        # Alternate references so both input-only and output-only blobs must
        # survive collection. Every execution also shares the empty payload.
        connection.executemany(
            """INSERT INTO workflow_node_execution_history
               (execution_key,execution_kind,execution_id,event_id,node_id,node_type,
                status,started_at,finished_at,input_blob_sha256,output_blob_sha256,updated_at)
               VALUES (?,'effect',?,'event','node','effect:test','succeeded',?,?,?,?,?)""",
            (
                (f"effect:{i}", str(i), recent, recent,
                 _key(i) if i % 2 else _key(0),
                 _key(0) if i % 2 else _key(i), recent)
                for i in range(1, retained + 1)
            ),
        )
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_progress',?)",
            (json.dumps({
                "date": "2026-09-30", "phase": "blobs",
                "cutoff": db.datetime_to_storage(now - timedelta(days=30)),
                "compact_cutoff": db.datetime_to_storage(now - timedelta(days=365)),
            }),),
        )
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_batch_limits',?)",
            (json.dumps({"records": 1, "index_rows": 1}),),
        )
        connection.execute(
            """INSERT INTO internal_events(event_id,event_type,created_at,payload_json)
               VALUES ('expired','test',?,'{}')""", (old,),
        )
        connection.executemany(
            """INSERT INTO scheduled_internal_events
               (schedule_id,event_type,run_at,status,created_at,published_at,payload_json)
               VALUES (?,'test',?,?,?,?,'{}')""",
            [("expired", old, "published", old, old),
             ("pending", old, "pending", old, None)],
        )


def _slice(path, *, batch_size=1):
    return db.run_orchestration_retention_slice(
        path, retention_days=30, compact_history_retention_days=365,
        batch_size=batch_size, time_budget_seconds=5,
    )


def _progress(path):
    with db.connect_orchestration_db_read_only(path) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_progress'"
        ).fetchone()
        return json.loads(row[0]) if row else None


def _finish(path, *, batch_size=1):
    deleted = 0
    for _ in range(32):
        close_sqlite_runtimes(path)
        result = _slice(path, batch_size=batch_size)
        assert result.skipped_reason is None
        assert result.compact_payload_blobs_deleted <= batch_size
        deleted += result.compact_payload_blobs_deleted
        if not result.remaining:
            return deleted
    pytest.fail("The sweep did not finish within bounded passes")


@pytest.mark.parametrize("orphaned", [0, 3])
def test_sparse_payload_cleanup_finishes_with_a_bounded_scan_and_restarts(
    tmp_path, monkeypatch, orphaned,
):
    path = tmp_path / "orchestration.db"
    _seed(path, orphaned=orphaned)
    original = db.connect_orchestration_db
    measured_steps = []

    @contextmanager
    def bounded(path, **kwargs):
        with original(path, **kwargs) as connection:
            if kwargs.get("operation") != "retention_batch":
                yield connection
                return
            steps = 0

            def budget():
                nonlocal steps
                steps += 100
                return int(steps > 40_000)

            connection.set_progress_handler(budget, 100)
            try:
                yield connection
            finally:
                connection.set_progress_handler(None, 0)
                measured_steps.append(steps)

    monkeypatch.setattr(db, "connect_orchestration_db", bounded)
    first = _slice(path)
    assert first.compact_payload_blobs_deleted == 0 and first.remaining
    progress = _progress(path)
    assert progress["phase"] == "blobs"
    assert _key(0) < progress["blob_cursor"] < progress["blob_high_water"]
    assert _finish(path) == orphaned
    assert max(measured_steps) <= 40_000
    with db.connect_orchestration_db_read_only(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_execution_payload_blobs"
        ).fetchone()[0] == 6001
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_node_execution_history"
        ).fetchone()[0] == 6000
        assert connection.execute("SELECT COUNT(*) FROM internal_events").fetchone()[0] == 0
        assert [row[0] for row in connection.execute(
            "SELECT schedule_id FROM scheduled_internal_events"
        )] == ["pending"]
        assert json.loads(connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='last_retention_date'"
        ).fetchone()[0])["date"] == "2026-09-30"
    assert _progress(path) is None


def test_payload_deadline_rolls_back_deletions_and_saved_scan_position(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    _seed(path, retained=1000, orphaned=6)
    assert _slice(path).compact_payload_blobs_deleted == 0
    saved_progress = _progress(path)
    with db.connect_orchestration_db(path) as connection:
        connection.execute(
            "UPDATE orchestration_metadata SET value_json=? WHERE key='retention_batch_limits'",
            (json.dumps({"records": 4, "index_rows": 8}),),
        )
    original = db.connect_orchestration_db
    fail_commit = True

    @contextmanager
    def interrupted(path, **kwargs):
        nonlocal fail_commit
        with original(path, **kwargs) as connection:
            yield connection
            if kwargs.get("operation") == "retention_batch" and fail_commit:
                fail_commit = False
                assert connection.execute(
                    "SELECT COUNT(*) FROM workflow_execution_payload_blobs"
                ).fetchone()[0] == 1003
                pending = json.loads(connection.execute(
                    "SELECT value_json FROM orchestration_metadata WHERE key='retention_progress'"
                ).fetchone()[0])
                assert pending["blob_cursor"] > saved_progress["blob_cursor"]
                raise SqliteOperationDeadlineExceeded("simulated commit deadline")

    monkeypatch.setattr(db, "connect_orchestration_db", interrupted)
    assert _slice(path, batch_size=4).skipped_reason == "batch_deadline_reduced"
    close_sqlite_runtimes(path)
    assert _progress(path) == saved_progress
    with db.connect_orchestration_db_read_only(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_execution_payload_blobs"
        ).fetchone()[0] == 1007
        assert json.loads(connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_batch_limits'"
        ).fetchone()[0]) == {"records": 2, "index_rows": 4}
    assert _finish(path, batch_size=4) == 6


def test_payload_sweep_defers_new_orphans_behind_cursor_or_above_high_water(tmp_path):
    path = tmp_path / "orchestration.db"
    _seed(path, retained=2000, orphaned=2)
    assert _slice(path).compact_payload_blobs_deleted == 0
    progress = _progress(path)
    assert _key(1) < progress["blob_cursor"] < progress["blob_high_water"]
    new_key = "f" * 64
    with db.connect_orchestration_db(path) as connection:
        connection.execute(
            "DELETE FROM workflow_node_execution_history WHERE execution_key='effect:1'"
        )
        _insert_blobs(connection, [new_key], db.datetime_to_storage(datetime.now(UTC)))
    assert _finish(path) == 2
    with db.connect_orchestration_db_read_only(path) as connection:
        assert [row[0] for row in connection.execute(
            "SELECT sha256 FROM workflow_execution_payload_blobs WHERE sha256 IN (?,?) "
            "ORDER BY sha256", (_key(1), new_key),
        )] == [_key(1), new_key]
    # A new daily sweep starts at the beginning and captures a fresh boundary.
    assert _finish(path) == 2
    with db.connect_orchestration_db_read_only(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_execution_payload_blobs"
        ).fetchone()[0] == 2000
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_node_execution_history"
        ).fetchone()[0] == 1999
