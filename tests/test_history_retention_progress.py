import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from history_storage_support import seed_indexed_history
from evander.repositories import orchestration_db as db
from evander.repositories import execution_history as history
from evander.repositories.sqlite_runtime import (
    SqliteOperationDeadlineExceeded,
    close_sqlite_runtimes,
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
