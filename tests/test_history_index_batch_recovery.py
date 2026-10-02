"""Failed field batches must roll back, shrink durably and recover without lost work."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from evander.repositories import execution_history as history
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    ensure_orchestration_schema,
)
from evander.repositories.sqlite_runtime import (
    SqliteOperationDeadlineExceeded,
    close_sqlite_runtimes,
    is_storage_contention,
)


def _seed_job(path, *, limit=None):
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        history.archive_execution_record(
            connection, kind="effect",
            record={
                "execution_id": "index-job", "event_id": "event", "effect_id": "node",
                "effect_type": "effect:test", "status": "succeeded", "started_at": datetime.now(UTC),
                "input_payload": {f"field-{i:03d}": i for i in range(220)}, "outputs": {},
            },
        )
        if limit is not None:
            _set_limit(connection, limit)


def _set_limit(connection, rows):
    connection.execute(
        "INSERT OR REPLACE INTO orchestration_metadata VALUES ('history_index_batch_limit',?)",
        (json.dumps({"rows": rows}),),
    )


def _read_limit(path):
    with connect_orchestration_db_read_only(path) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='history_index_batch_limit'"
        ).fetchone()
    return json.loads(row[0])["rows"] if row else None


@pytest.mark.parametrize("initial_limit,concurrent_limit,expected_limit", [
    (None, None, 50), (1, None, 1), (None, 2, 2),
])
def test_deadline_rolls_back_then_persists_backoff_and_recovers_after_restart(
    tmp_path, monkeypatch, initial_limit, concurrent_limit, expected_limit,
):
    path = tmp_path / "orchestration.db"
    _seed_job(path, limit=initial_limit)
    original_prepare = history.prepare_pending_history_indexing
    original_apply = history.run_pending_history_indexing

    def prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        if concurrent_limit is not None:
            # Another worker can reduce the limit while preparation holds no writer.
            with connect_orchestration_db(path) as connection:
                _set_limit(connection, concurrent_limit)
        return prepared

    def interrupted(connection, **kwargs):
        original_apply(connection, **kwargs)
        cause = sqlite3.OperationalError("interrupted")
        cause.sqlite_errorcode = sqlite3.SQLITE_INTERRUPT
        raise SqliteOperationDeadlineExceeded("Indexing execution timed out") from cause

    monkeypatch.setattr(history, "prepare_pending_history_indexing", prepare)
    monkeypatch.setattr(history, "run_pending_history_indexing", interrupted)
    with pytest.raises(SqliteOperationDeadlineExceeded) as caught:
        history.run_history_indexing_slice(path, writer_time_budget_seconds=5)
    assert not is_storage_contention(caught.value)
    assert _read_limit(path) == expected_limit
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT next_offset FROM workflow_history_index_jobs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_history_index_values").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_history_index_state").fetchone()[0] == 0

    monkeypatch.setattr(history, "prepare_pending_history_indexing", original_prepare)
    monkeypatch.setattr(history, "run_pending_history_indexing", original_apply)
    close_sqlite_runtimes(path)
    # A fresh connection must honor the durable limit. Full, fast batches then
    # recover toward the original cap without exposing an incomplete generation.
    first = history.run_history_indexing_slice(path, writer_time_budget_seconds=5)
    assert first == {"indexed_values": expected_limit, "completed": 0}
    assert _read_limit(path) == min(100, expected_limit * 2)
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_node_execution_current_values").fetchone()[0] == 0
    total = first["indexed_values"]
    for _ in range(15):
        result = history.run_history_indexing_slice(path, writer_time_budget_seconds=5)
        total += result["indexed_values"]
        if result["completed"]:
            break
    else:
        pytest.fail("Bounded batches must finish the queued generation")
    assert total == 220 and _read_limit(path) == 100
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_history_index_jobs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_node_execution_current_values").fetchone()[0] == 220


def test_writer_contention_keeps_the_field_limit_and_cursor_unchanged(tmp_path):
    path = tmp_path / "orchestration.db"
    _seed_job(path, limit=32)
    with connect_orchestration_db(path) as foreground:
        foreground.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError) as caught:
            history.run_history_indexing_slice(path)
        assert is_storage_contention(caught.value)
        assert foreground.in_transaction
    assert _read_limit(path) == 32
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT next_offset FROM workflow_history_index_jobs").fetchone()[0] == 0


def test_fast_indexing_preserves_a_concurrent_limit_reduction(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    _seed_job(path, limit=16)
    original = history.prepare_pending_history_indexing

    def prepare(*args, **kwargs):
        prepared = original(*args, **kwargs)
        with connect_orchestration_db(path) as connection:
            _set_limit(connection, 2)
        return prepared

    monkeypatch.setattr(history, "prepare_pending_history_indexing", prepare)
    assert history.run_history_indexing_slice(path, writer_time_budget_seconds=5)["indexed_values"] == 16
    assert _read_limit(path) == 2
