from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from evander.orchestration import wal_maintenance
from evander.orchestration.storage_maintenance import run_storage_maintenance
from evander.repositories import execution_history
from evander.repositories.orchestration_db import (
    OrchestrationWalCheckpointResult,
    build_orchestration_maintenance_indexes,
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    ensure_orchestration_schema,
    orchestration_maintenance_index_status,
    run_orchestration_retention_slice,
)


def _config(path, **overrides):
    return SimpleNamespace(
        orchestration_store_path=path,
        orchestration_wal_checkpoint_mode=overrides.get("mode", "disabled"),
        orchestration_wal_checkpoint_min_bytes=0,
        orchestration_wal_checkpoint_interval_seconds=300,
        orchestration_wal_checkpoint_busy_timeout_ms=100,
        orchestration_retention_days=30,
        orchestration_compact_history_retention_days=365,
    )


def test_storage_lane_never_blocks_dispatch_and_remains_single_flight(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def maintenance(config):
        calls.append(config.orchestration_store_path)
        entered.set()
        assert release.wait(2)
        return OrchestrationWalCheckpointResult(skipped_reason="disabled")

    monkeypatch.setattr(wal_maintenance, "run_storage_maintenance", maintenance)
    lane = wal_maintenance.OrchestrationWalCheckpointMaintenance(clock=lambda: 10.0)
    config = _config(tmp_path / "orchestration.db")
    try:
        assert lane.run_if_due(config) is None
        assert entered.wait(1)
        assert lane.run_if_due(config) is None
        assert calls == [config.orchestration_store_path]
    finally:
        release.set()
        lane.close()


def test_disabled_checkpoint_still_runs_history_projection(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    calls = []

    def index(connection, **kwargs):
        calls.append(kwargs)
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] >= 6
        return {"indexed_values": 0, "completed": 0}

    monkeypatch.setattr(execution_history, "run_pending_history_indexing", index)
    result = run_storage_maintenance(_config(path))
    assert calls == [
        {
            "batch_size": 100,
            "time_budget_seconds": calls[0]["time_budget_seconds"],
            "prepared": None,
        }
    ]
    assert 0 < calls[0]["time_budget_seconds"] <= 0.25
    assert result.skipped_reason == "disabled"


def test_retention_resumes_bounded_batches_with_one_cutoff(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    progress = {
        "date": "2026-01-01",
        "phase": "workflow",
        "cutoff": cutoff,
        "compact_cutoff": (datetime.now(UTC) - timedelta(days=365)).isoformat(),
    }
    with connect_orchestration_db(path) as connection:
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_progress', ?)",
            (json.dumps(progress),),
        )
        connection.executemany(
            """INSERT INTO workflow_events
            (event_id,event_type,source,occurred_at,payload_json) VALUES (?, 'test','test',?,'{}')""",
            [(str(i), old) for i in range(5)],
        )
    first = run_orchestration_retention_slice(
        path, retention_days=30, compact_history_retention_days=365, batch_size=2
    )
    assert first.workflow_events_deleted == 2 and first.remaining
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 3
        persisted = json.loads(
            connection.execute(
                "SELECT value_json FROM orchestration_metadata WHERE key='retention_progress'"
            ).fetchone()[0]
        )
        assert persisted == progress
    second = run_orchestration_retention_slice(
        path, retention_days=60, compact_history_retention_days=365, batch_size=2
    )
    # The existing sweep retains its cutoff even if configuration changes.
    assert second.workflow_events_deleted == 2


def test_legacy_missing_fk_indexes_defer_gc_until_explicit_offline_migration(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    now = datetime.now(UTC).isoformat()
    with connect_orchestration_db(path) as connection:
        connection.execute("DROP INDEX idx_workflow_history_input_blob")
        connection.execute(
            "INSERT INTO workflow_execution_payload_blobs VALUES ('orphan','zlib',0,0,X'',?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_progress',?)",
            (
                json.dumps(
                    {"date": "2026-01-01", "phase": "blobs", "cutoff": now, "compact_cutoff": now}
                ),
            ),
        )
    assert not orchestration_maintenance_index_status(path)["ready"]
    result = run_orchestration_retention_slice(
        path, retention_days=30, compact_history_retention_days=365
    )
    assert result.skipped_reason == "maintenance_indexes_required" and result.remaining
    with connect_orchestration_db_read_only(path) as connection:
        assert (
            connection.execute("SELECT sha256 FROM workflow_execution_payload_blobs").fetchone()[0]
            == "orphan"
        )
    assert build_orchestration_maintenance_indexes(path)["ready"]
    result = run_orchestration_retention_slice(
        path, retention_days=30, compact_history_retention_days=365
    )
    assert result.compact_payload_blobs_deleted == 1
    with connect_orchestration_db_read_only(path) as connection:
        plan = [
            row[3]
            for row in connection.execute(
                "EXPLAIN QUERY PLAN DELETE FROM workflow_execution_payload_blobs WHERE sha256='orphan'"
            )
        ]
    assert not any("SCAN workflow_node_execution_history" in line for line in plan)


def test_maintenance_yields_remaining_write_phases_to_foreground_writer(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        execution_history.archive_execution_record(
            connection,
            kind="effect",
            record={
                "execution_id": "pending",
                "event_id": "event",
                "effect_id": "effect",
                "effect_type": "effect:test",
                "status": "succeeded",
                "started_at": datetime.now(UTC),
                "input_payload": {},
                "outputs": {"value": 1},
            },
        )
    retention_calls = []
    from evander.orchestration import storage_maintenance

    monkeypatch.setattr(
        storage_maintenance,
        "run_orchestration_retention_slice",
        lambda *args, **kwargs: retention_calls.append(kwargs),
    )
    with connect_orchestration_db(path, deadline_seconds=2) as foreground:
        foreground.execute("BEGIN IMMEDIATE")
        result = run_storage_maintenance(_config(path))
        # The occupied writer is deferred once, leaving subsequent phases for
        # a later pass instead of accumulating repeated busy-timeout waits.
        assert result.skipped_reason == "storage_busy"
        assert retention_calls == []
        assert foreground.in_transaction


def test_scalar_gc_advances_past_live_prefix_between_connections(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        execution_history.archive_execution_record(
            connection,
            kind="effect",
            record={
                "execution_id": "retained",
                "event_id": "event",
                "effect_id": "effect",
                "effect_type": "effect:test",
                "status": "succeeded",
                "started_at": datetime.now(UTC),
                "input_payload": {},
                "outputs": {},
            },
        )
        connection.execute(
            "INSERT INTO workflow_history_index_state VALUES ('effect:retained','active')"
        )
        connection.executemany(
            "INSERT INTO workflow_history_index_values VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    "effect:retained",
                    "active" if index < 150 else "superseded",
                    "output",
                    f"/{index}",
                    "string",
                    None,
                    "value",
                    None,
                    None,
                )
                for index in range(160)
            ],
        )
    with connect_orchestration_db(path) as connection:
        assert execution_history.collect_superseded_history_values(connection) == 0
    with connect_orchestration_db(path) as connection:
        assert execution_history.collect_superseded_history_values(connection) == 10
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_history_index_values").fetchone()[0]
            == 150
        )
    with connect_orchestration_db(path) as connection:
        assert execution_history.collect_superseded_history_values(connection) == 0


def test_operational_gc_advances_past_referenced_prefix_between_connections(tmp_path):
    from evander.repositories.operational_payloads import collect_unreferenced_payloads

    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        connection.executemany(
            "INSERT INTO workflow_operational_blobs VALUES (?, 'zlib-json-v1', ?, 1)",
            [(f"{index:064x}", b"unused") for index in range(160)],
        )
        connection.executemany(
            "INSERT INTO workflow_operational_record_refs VALUES ('effect', 'retained', ?)",
            [(f"{index:064x}",) for index in range(150)],
        )
    with connect_orchestration_db(path) as connection:
        assert collect_unreferenced_payloads(connection) == 0
    with connect_orchestration_db(path) as connection:
        assert collect_unreferenced_payloads(connection) == 10
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0]
            == 150
        )
    with connect_orchestration_db(path) as connection:
        assert collect_unreferenced_payloads(connection) == 0
