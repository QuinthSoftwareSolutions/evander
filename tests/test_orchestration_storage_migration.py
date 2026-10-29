import json
import sqlite3
from contextlib import contextmanager
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
from evander.repositories import orchestration_db
from evander.repositories.maintenance_indexes import RETENTION_SOURCE_INDEXES
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


@pytest.mark.parametrize("options", [
    [], ["--apply"], ["--check"],
    ["--apply", "--offline", "--downgrade"],
    ["--apply", "--offline", "--max-batches", "1"],
])
def test_indexes_only_rejects_unsafe_or_conflicting_options_without_writes(tmp_path, options):
    path = tmp_path / "orchestration.db"
    SqliteExecutionStore(path)
    before = path.stat().st_mtime_ns
    result = CliRunner().invoke(app, [
        "migrate-orchestration-storage", "--database", str(path), "--indexes-only", *options,
    ])
    assert result.exit_code != 0
    assert path.stat().st_mtime_ns == before


def test_indexes_only_builds_indexes_without_rewriting_payloads_or_progress(tmp_path):
    path = tmp_path / "orchestration.db"
    SqliteExecutionStore(path)
    with orchestration_db.connect_orchestration_db(path) as connection:
        for source in RETENTION_SOURCE_INDEXES:
            connection.execute(f"DROP INDEX {source.index.name}")
        # A lossless inline payload must stay inline even though the full
        # conversion command would externalize this large subtree.
        connection.execute("""INSERT INTO effect_executions
            (execution_id,idempotency_key,event_id,trace_id,source_node_id,source_trigger_type,
             status,effect_id,effect_type,started_at,finished_at,payload_json)
            VALUES ('kept','kept','event','trace','node','trigger:test',
                'succeeded','effect','effect:test','2026-01-01','2026-01-01',?)""",
                           (json.dumps({"outputs": {"large": "x" * 20000}}),))
        connection.execute("INSERT INTO orchestration_metadata VALUES ('retention_progress','{}')")

    def snapshot():
        with connect_orchestration_db_read_only(path) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            )]
            return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                    for table in tables}

    before = snapshot()
    runner = CliRunner()
    checked = runner.invoke(app, ["migrate-orchestration-storage", "--database", str(path), "--check"])
    assert checked.exit_code == 0
    assert json.loads(checked.output)["maintenance"]["ready"] is False
    assert snapshot() == before
    options = ["migrate-orchestration-storage", "--database", str(path),
               "--indexes-only", "--apply", "--offline"]
    for _ in range(2):
        built = runner.invoke(app, options)
        assert built.exit_code == 0, built.output
        result = json.loads(built.output)
        assert result["direction"] == "indexes_only"
        assert result["complete"] and result["indexes"]["ready"]
        assert result["processed"] == result["batches"] == 0
        assert snapshot() == before


def test_interrupted_index_build_preserves_completed_indexes_and_resumes(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    SqliteExecutionStore(path)
    with orchestration_db.connect_orchestration_db(path) as connection:
        for source in RETENTION_SOURCE_INDEXES:
            connection.execute(f"DROP INDEX {source.index.name}")
    connect = orchestration_db.connect_orchestration_db

    @contextmanager
    def interrupt_second_build(*args, **kwargs):
        with connect(*args, **kwargs) as connection:
            def authorizer(action, name, *rest):
                if action == sqlite3.SQLITE_CREATE_INDEX and name == "idx_effect_retention_completed":
                    connection.set_progress_handler(lambda: 1, 1)
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorizer)
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(orchestration_db, "connect_orchestration_db", interrupt_second_build)
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            orchestration_db.build_orchestration_maintenance_indexes(path)
    with connect_orchestration_db_read_only(path) as connection:
        built = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%retention%'",
        )]
    assert built == ["idx_trigger_retention"]
    assert not orchestration_db.orchestration_maintenance_index_status(path)["ready"]
    assert orchestration_db.build_orchestration_maintenance_indexes(path)["ready"]
