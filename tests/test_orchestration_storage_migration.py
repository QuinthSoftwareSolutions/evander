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
