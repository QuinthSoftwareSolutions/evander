import sqlite3
from datetime import UTC, datetime

from evander.daemons.effect_orchestrator import EffectExecutionRecord, SqliteExecutionStore
from evander.repositories import execution_history as history
from evander.repositories.orchestration_db import connect_orchestration_db


def test_index_preparation_releases_database_and_rejects_superseded_batch(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    record = EffectExecutionRecord(
        execution_id="receipt",
        idempotency_key="receipt-key",
        event_id="event",
        trace_id="trace",
        source_node_id="source",
        source_trigger_type="trigger:test",
        effect_id="effect",
        effect_type="effect:test",
        status="succeeded",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        resolved_config_hash="hash",
        resolved_config={},
        input_payload={},
        outputs={"nested": {f"value-{i:03}": i for i in range(300)}},
    )
    store.put(record)
    scalar_columns = history._scalar_columns
    probes = 0

    def verify_no_writer(value):
        nonlocal probes
        if probes == 0:
            with sqlite3.connect(path, timeout=0) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.rollback()
        probes += 1
        return scalar_columns(value)

    monkeypatch.setattr(history, "_scalar_columns", verify_no_writer)
    prepared = history.prepare_pending_history_indexing(path)
    assert len(prepared.rows) == 100
    with connect_orchestration_db(path) as connection:
        assert (
            history.run_pending_history_indexing(connection, prepared=prepared)["indexed_values"]
            == 100
        )
    # A new process can resume the durable traversal without hashing the prefix.
    probes = 0
    second = history.prepare_pending_history_indexing(path)
    assert len(second.rows) == 100
    assert probes == 100
    assert second.rows[0][3] == "/nested/value-099"
    record.outputs = {"new": 42}
    store.put(record)
    with connect_orchestration_db(path) as connection:
        assert history.run_pending_history_indexing(connection, prepared=second) == {
            "indexed_values": 0,
            "completed": 0,
        }
    current = history.prepare_pending_history_indexing(path)
    with connect_orchestration_db(path) as connection:
        assert history.run_pending_history_indexing(connection, prepared=current)["completed"] == 1
        values = connection.execute(
            "SELECT json_pointer,numeric_value FROM workflow_node_execution_current_values"
        ).fetchall()
    assert [tuple(row) for row in values] == [("/new", 42.0)]
