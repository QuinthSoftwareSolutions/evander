import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from evander.cli import app
from evander.daemons.effect_orchestrator import EffectExecutionRecord, SqliteExecutionStore
from evander.orchestration.storage_migration import (
    check_orchestration_storage,
    migrate_orchestration_storage,
)
from evander.repositories.orchestration_db import connect_orchestration_db_read_only
from history_storage_support import seed_indexed_history


def test_offline_rollback_restores_partially_retired_history(tmp_path):
    from evander.repositories.orchestration_db import (
        connect_orchestration_db, run_orchestration_retention_slice,
    )

    path = tmp_path / "orchestration.db"
    seed_indexed_history(path, executions=2, fields=200)
    with connect_orchestration_db(path) as connection:
        connection.execute("INSERT INTO orchestration_metadata VALUES ('retention_progress',?)", (
            json.dumps({"date": "2026-01-01", "phase": "compact",
                        "cutoff": datetime.now(UTC).isoformat(),
                        "compact_cutoff": datetime.now(UTC).isoformat()}),
        ))
        connection.execute("INSERT INTO orchestration_metadata VALUES ('retention_batch_limits',?)",
                           (json.dumps({"index_rows": 100}),))
    partial = run_orchestration_retention_slice(
        path, retention_days=3, compact_history_retention_days=7,
    )
    assert partial.compact_executions_deleted == 0
    assert partial.compact_index_rows_deleted == 100
    assert not check_orchestration_storage(path)["downgrade_ready"]
    for _ in range(30):
        result = migrate_orchestration_storage(path, downgrade=True, max_batches=1)
        if result["complete"]:
            break
    assert result["complete"]
    assert check_orchestration_storage(path, verify=True)["downgrade_ready"]
    with connect_orchestration_db_read_only(path) as connection:
        # The previous executable reads this table directly. Both legacy and
        # generation-indexed executions must regain every searchable field.
        assert [tuple(row) for row in connection.execute(
            """SELECT execution_key,COUNT(*) FROM workflow_node_execution_history_values
               GROUP BY execution_key ORDER BY execution_key"""
        )] == [("effect:0", 200), ("effect:1", 200)]
        assert not connection.execute("SELECT 1 FROM workflow_history_retirements").fetchone()


def test_offline_rollback_restores_old_binary_payload_and_scalar_search_contract(tmp_path):
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    original = EffectExecutionRecord(
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
        outputs={"answer": 42, "large": "payload" * 10000},
    )
    store.put(original)
    assert not check_orchestration_storage(path)["downgrade_ready"]
    first = migrate_orchestration_storage(path, downgrade=True, max_batches=1)
    assert first["complete"] is False
    with pytest.raises(ValueError, match="opposite-direction"):
        migrate_orchestration_storage(path, max_batches=1)
    # Each invocation can stop and the next resumes both row and index conversion.
    for _ in range(30):
        result = migrate_orchestration_storage(path, downgrade=True, max_batches=1)
        if result["complete"]:
            break
    assert result["complete"]
    assert check_orchestration_storage(path, verify=True)["downgrade_ready"]
    with connect_orchestration_db_read_only(path) as connection:
        old_row = json.loads(
            connection.execute("SELECT payload_json FROM effect_executions").fetchone()[0]
        )
        assert EffectExecutionRecord.model_validate(old_row) == original
        legacy_values = connection.execute("""SELECT text_value FROM workflow_node_execution_history_values
            WHERE execution_key='effect:receipt' AND location='output' AND json_pointer='/answer'""").fetchall()
    assert [row[0] for row in legacy_values] == ["42"]


def test_storage_cli_requires_explicit_offline_apply_and_check_does_not_migrate(tmp_path):
    path = tmp_path / "orchestration.db"
    SqliteExecutionStore(path)
    before = path.stat().st_mtime_ns
    runner = CliRunner()
    denied = runner.invoke(
        app, ["migrate-orchestration-storage", "--database", str(path), "--apply"]
    )
    assert denied.exit_code != 0
    assert "--offline" in denied.output
    checked = runner.invoke(
        app, ["migrate-orchestration-storage", "--database", str(path), "--check", "--verify"]
    )
    assert checked.exit_code == 0
    assert json.loads(checked.output)["verified"] is True
    assert path.stat().st_mtime_ns == before
