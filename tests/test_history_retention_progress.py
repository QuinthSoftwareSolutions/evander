import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from history_storage_support import seed_indexed_history
from evander.repositories import orchestration_db as db
from evander.repositories import execution_history as history
from evander.repositories.sqlite_runtime import (
    SqliteOperationDeadlineExceeded,
    close_sqlite_runtimes,
    foreground_storage_wait_budget,
    is_storage_contention,
    storage_lock,
)


def _start_compact(path):
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    with db.connect_orchestration_db(path) as connection:
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_progress',?)",
            (
                json.dumps(
                    {
                        "date": "2026-01-01",
                        "phase": "compact",
                        "cutoff": cutoff,
                        "compact_cutoff": cutoff,
                    }
                ),
            ),
        )


def _slice(path, **kwargs):
    return db.run_orchestration_retention_slice(
        path,
        retention_days=3,
        compact_history_retention_days=7,
        **kwargs,
    )


def _seed_sources(connection, kind, source_ids, at):
    timestamp = db.datetime_to_storage(at)
    if kind == "trigger":
        sql = """INSERT INTO trigger_events(
            event_id,idempotency_key,delivery_state,trigger_id,trigger_type,trace_id,
            occurred_at,delivered_at,payload_json)
            VALUES (?,?,'delivered','node','trigger:test','trace',?,?,'{}')"""
    else:
        sql = f"""INSERT INTO {kind}_executions(
            execution_id,idempotency_key,event_id,trace_id,source_node_id,source_trigger_type,
            status,{kind}_id,{kind}_type,started_at,finished_at,payload_json)
            VALUES (?,?,'event','trace','source','trigger:test','succeeded',
                    'node','{kind}:test',?,?,'{{}}')"""
    connection.executemany(sql, ((key, key, timestamp, timestamp) for key in source_ids))


def _queue_source_history(connection, kind, source_id, at, *, status="succeeded"):
    if kind == "trigger":
        record = {
            "event_id": source_id,
            "trigger_id": "node",
            "trigger_type": "trigger:test",
            "delivery_state": "delivered",
            "occurred_at": at,
            "delivered_at": at,
        }
    else:
        record = {
            "execution_id": source_id,
            f"{kind}_id": "node",
            f"{kind}_type": f"{kind}:test",
            "status": status,
            "started_at": at,
            "finished_at": at,
        }
    history.archive_execution_record(connection, kind=kind, record=record)


@pytest.mark.parametrize("kind", ["trigger", "effect", "gate"])
def test_pending_retention_check_is_bounded_by_jobs_instead_of_source_history(tmp_path, kind):
    path = tmp_path / "orchestration.db"
    db.ensure_orchestration_schema(path)
    now = datetime.now(UTC)
    old = now - timedelta(days=40)
    cutoff = db.datetime_to_storage(now - timedelta(days=30))
    with db.connect_orchestration_db(path) as connection:
        _seed_sources(connection, kind, (f"node:{index}" for index in range(5000)), old)
        _seed_sources(connection, kind, ["node:recent"], now)
        _queue_source_history(connection, kind, "node:recent", now)

    with db.connect_orchestration_db_read_only(path) as connection:
        # A VM work budget makes an execution-table scan fail without relying on elapsed time.
        connection.set_progress_handler(lambda: 1, 2000)
        try:
            assert not db._has_pending_retention_sources(connection, cutoff)
        finally:
            connection.set_progress_handler(None, 0)

    with db.connect_orchestration_db(path) as connection:
        _queue_source_history(connection, kind, "node:4999", old)
    with db.connect_orchestration_db_read_only(path) as connection:
        connection.set_progress_handler(lambda: 1, 2000)
        try:
            assert db._has_pending_retention_sources(connection, cutoff)
        finally:
            connection.set_progress_handler(None, 0)


@pytest.mark.parametrize("kind", ["effect", "gate"])
def test_pending_failed_retention_requires_an_old_acknowledgement(tmp_path, kind):
    path = tmp_path / "orchestration.db"
    db.ensure_orchestration_schema(path)
    now = datetime.now(UTC)
    old = now - timedelta(days=40)
    cutoff = db.datetime_to_storage(now - timedelta(days=30))
    with db.connect_orchestration_db(path) as connection:
        _seed_sources(connection, kind, ["failed"], old)
        connection.execute(f"UPDATE {kind}_executions SET status='failed'")
        _queue_source_history(connection, kind, "failed", old, status="failed")
        assert not db._has_pending_retention_sources(connection, cutoff)

        connection.execute(
            f"UPDATE {kind}_executions SET dismissed_at=?", (db.datetime_to_storage(now),)
        )
        assert not db._has_pending_retention_sources(connection, cutoff)
        connection.execute(
            f"UPDATE {kind}_executions SET dismissed_at=?", (db.datetime_to_storage(old),)
        )
        assert db._has_pending_retention_sources(connection, cutoff)
        connection.execute(
            f"UPDATE {kind}_executions SET dismissed_at=NULL,cleared_at=?",
            (db.datetime_to_storage(old),),
        )
        assert db._has_pending_retention_sources(connection, cutoff)


def test_dense_history_retention_commits_progress_and_reaches_later_phases(tmp_path):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=100, fields=3000)
    # Upgrade an already populated database from the previous schema before
    # running maintenance; no bulk index rebuild belongs in startup.
    with db.connect_orchestration_db(path) as connection:
        connection.execute("DROP TRIGGER workflow_history_cancel_retirement")
        connection.execute("DROP TABLE workflow_history_retirements")
        connection.execute("DELETE FROM schema_migrations WHERE version=7")
    close_sqlite_runtimes(path)
    db.ensure_orchestration_schema(path)
    _start_compact(path)
    indexed = deleted = 0
    for _ in range(1000):
        result = _slice(path)
        indexed += result.compact_index_rows_deleted
        deleted += result.compact_executions_deleted
        if not result.remaining:
            break
    assert not result.remaining
    assert indexed == 300000
    assert deleted == 100
    with db.connect_orchestration_db_read_only(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_history_retirements").fetchone()[0]
            == 0
        )
        assert connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='last_retention_date'"
        ).fetchone()


def test_retention_deadline_rollback_reduces_next_batch_across_reopen(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=4, fields=20)
    _start_compact(path)
    delete = db._delete_compact_history_batch
    attempts = []

    def limited(connection, keys, **kwargs):
        result = delete(connection, keys, **kwargs)
        attempts.append(len(keys))
        if len(keys) > 1:
            raise SqliteOperationDeadlineExceeded("simulated large cascade deadline")
        return result

    monkeypatch.setattr(db, "_delete_compact_history_batch", limited)
    assert _slice(path, batch_size=4).skipped_reason == "batch_deadline_reduced"
    close_sqlite_runtimes(path)
    with db.connect_orchestration_db_read_only(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_node_execution_history").fetchone()[0]
            == 4
        )
    assert _slice(path, batch_size=4).skipped_reason == "batch_deadline_reduced"
    assert _slice(path, batch_size=4).compact_executions_deleted == 1
    assert attempts == [4, 2, 1]


def test_writer_contention_does_not_reduce_retention_workload(tmp_path):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=2, fields=20)
    _start_compact(path)
    blocker = sqlite3.connect(path)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            _slice(path, time_budget_seconds=0.05)
    finally:
        blocker.close()
    with db.connect_orchestration_db_read_only(path) as connection:
        assert not connection.execute(
            "SELECT 1 FROM orchestration_metadata WHERE key='retention_batch_limits'"
        ).fetchone()
    assert _slice(path).compact_executions_deleted == 2


def test_repository_lock_contention_does_not_reduce_retention_workload(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=2, fields=20)
    _start_compact(path)
    _set_batch_limits(path, records=8, index_rows=32)
    blocker = threading.Lock()
    blocker.acquire()

    def blocked(*args, **kwargs):
        with foreground_storage_wait_budget(0), storage_lock(blocker):
            pytest.fail("The occupied repository lock must yield")

    monkeypatch.setattr(db, "_delete_compact_history_batch", blocked)
    try:
        with pytest.raises(SqliteOperationDeadlineExceeded) as caught:
            _slice(path)
        assert is_storage_contention(caught.value)
    finally:
        blocker.release()
    with db.connect_orchestration_db_read_only(path) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_batch_limits'"
        ).fetchone()
        assert json.loads(row[0]) == {"records": 8, "index_rows": 32}
        assert connection.execute("SELECT COUNT(*) FROM workflow_node_execution_history").fetchone()[0] == 2


def _set_batch_limits(path, *, records=1, index_rows=1):
    with db.connect_orchestration_db(path) as connection:
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_batch_limits',?)",
            (json.dumps({"records": records, "index_rows": index_rows}),),
        )


def test_successful_retention_recovers_record_batches_across_reopen(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=20, fields=1)
    _start_compact(path)
    _set_batch_limits(path)
    # Control adaptation timing independently of SQLite's real transaction deadlines.
    monkeypatch.setattr(db, "monotonic", lambda: 0.0)
    deleted = []
    for _ in range(5):
        deleted.append(_slice(path, batch_size=8, time_budget_seconds=5).compact_executions_deleted)
        close_sqlite_runtimes(path)

    assert deleted == [1, 2, 4, 8, 5]
    with db.connect_orchestration_db_read_only(path) as connection:
        assert not connection.execute("SELECT 1 FROM workflow_node_execution_history").fetchone()


def test_successful_retention_recovers_dense_index_batches_to_cap(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=1, fields=30000)
    _start_compact(path)
    _set_batch_limits(path)
    monkeypatch.setattr(db, "monotonic", lambda: 0.0)
    index_batches = []
    for _ in range(20):
        result = _slice(path, batch_size=2, time_budget_seconds=5)
        index_batches.append(result.compact_index_rows_deleted)
        close_sqlite_runtimes(path)
        if result.compact_executions_deleted:
            break

    assert result.compact_executions_deleted == 1
    assert sum(index_batches) == 30000
    assert max(index_batches) == 10000


def test_slow_success_does_not_increase_retention_limits(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=10, fields=1)
    _start_compact(path)
    _set_batch_limits(path)
    elapsed = iter([0.0, 3.0, 0.0, 3.0])
    monkeypatch.setattr(db, "monotonic", lambda: next(elapsed))
    for _ in range(2):
        assert _slice(path, batch_size=8, time_budget_seconds=5).compact_executions_deleted == 1
        close_sqlite_runtimes(path)

    monkeypatch.setattr(db, "monotonic", lambda: 0.0)
    assert _slice(path, batch_size=8, time_budget_seconds=5).compact_executions_deleted == 1
    assert _slice(path, batch_size=8, time_budget_seconds=5).compact_executions_deleted == 2


@pytest.mark.parametrize("deferred", [False, True])
def test_empty_or_deferred_retention_does_not_increase_limits(tmp_path, monkeypatch, deferred):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=1, fields=1, age_days=40 if deferred else 0)
    _start_compact(path)
    _set_batch_limits(path)
    monkeypatch.setattr(db, "monotonic", lambda: 0.0)
    if deferred:
        with db.connect_orchestration_db(path) as connection:
            connection.execute("DROP INDEX idx_workflow_history_input_blob")

    result = _slice(path, batch_size=8, time_budget_seconds=5)
    assert result.compact_executions_deleted == 0
    assert result.compact_index_rows_deleted == 0
    if deferred:
        assert result.skipped_reason == "maintenance_indexes_required"
    with db.connect_orchestration_db_read_only(path) as connection:
        limits = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_batch_limits'"
        ).fetchone()[0]
    assert json.loads(limits) == {"records": 1, "index_rows": 1}


def test_retention_growth_does_not_overwrite_concurrent_reduction(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=20, fields=1)
    _start_compact(path)
    _set_batch_limits(path, records=4, index_rows=4)
    monkeypatch.setattr(db, "monotonic", lambda: 0.0)
    run_slice = db._run_orchestration_retention_slice

    def concurrently_reduced(*args, **kwargs):
        with db.connect_orchestration_db(path) as connection:
            connection.execute(
                "UPDATE orchestration_metadata SET value_json=? WHERE key='retention_batch_limits'",
                (json.dumps({"records": 1, "index_rows": 1}),),
            )
        return run_slice(*args, **kwargs)

    monkeypatch.setattr(db, "_run_orchestration_retention_slice", concurrently_reduced)
    assert _slice(path, batch_size=8, time_budget_seconds=5).compact_executions_deleted == 4
    close_sqlite_runtimes(path)
    monkeypatch.setattr(db, "_run_orchestration_retention_slice", run_slice)
    assert _slice(path, batch_size=8, time_budget_seconds=5).compact_executions_deleted == 1


def test_partial_retirement_is_hidden_and_updated_record_gets_complete_index(tmp_path):
    path = tmp_path / "orchestration.db"
    record = seed_indexed_history(path, executions=1, fields=3000)
    _start_compact(path)
    with db.connect_orchestration_db(path) as connection:
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_batch_limits',?)",
            (json.dumps({"index_rows": 100}),),
        )
    assert _slice(path).compact_executions_deleted == 0
    start, end = record["started_at"] - timedelta(days=1), datetime.now(UTC) + timedelta(days=1)
    assert (
        history.list_node_execution_fields(
            path, node_id="history-node", started_from=start, started_to=end
        )["fields"]
        == []
    )
    with pytest.raises(ValueError, match="not found"):
        history.read_node_execution_detail(path, execution_key="effect:0")
    # The same payload can be updated while cleanup is between transactions.
    record["finished_at"] = datetime.now(UTC)
    with db.connect_orchestration_db(path, deadline_seconds=2) as connection:
        history.archive_execution_record(connection, kind="effect", record=record)
    for _ in range(50):
        with db.connect_orchestration_db(path, deadline_seconds=2) as connection:
            history.run_pending_history_indexing(connection, batch_size=100)
            if not connection.execute("SELECT 1 FROM workflow_history_index_jobs").fetchone():
                break
    with db.connect_orchestration_db_read_only(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workflow_node_execution_current_values"
            ).fetchone()[0]
            == 3000
        )
        assert not connection.execute("SELECT 1 FROM workflow_history_retirements").fetchone()
    assert (
        history.read_node_execution_detail(path, execution_key="effect:0")["output"]["field-2999"]
        == 2999
    )
