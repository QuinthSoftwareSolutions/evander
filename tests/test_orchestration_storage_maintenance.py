from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

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

    def maintenance(config, *, run_garbage_collection):
        calls.append(config.orchestration_store_path)
        assert run_garbage_collection is True
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


def test_storage_lane_skips_idle_gc_without_delaying_history_work(tmp_path, monkeypatch):
    now = [10.0]
    calls = []

    class ImmediateExecutor:
        def __init__(self, **kwargs):
            pass

        def submit(self, function, config, *, run_garbage_collection):
            calls.append((now[0], run_garbage_collection))
            future = Future()
            future.set_result(
                OrchestrationWalCheckpointResult(
                    skipped_reason="disabled",
                    garbage_collection_pending=False if run_garbage_collection else None,
                    history_index_pending=1,
                )
            )
            return future

        def shutdown(self, *, wait):
            pass

    monkeypatch.setattr(wal_maintenance, "ThreadPoolExecutor", ImmediateExecutor)
    lane = wal_maintenance.OrchestrationWalCheckpointMaintenance(clock=lambda: now[0])
    config = _config(tmp_path / "orchestration.db")
    try:
        lane.run_if_due(config)
        # The history backlog permits an immediate next slice, while idle GC waits.
        assert lane.run_if_due(config).history_index_pending == 1
        now[0] = 39.0
        lane.run_if_due(config)
        now[0] = 40.0
        lane.run_if_due(config)
        assert calls == [(10.0, True), (10.0, False), (39.0, False), (40.0, True)]
    finally:
        lane.close()


@pytest.mark.parametrize("gc_pending", [True, None])
def test_storage_lane_retries_unfinished_gc_on_next_slice(tmp_path, monkeypatch, gc_pending):
    now = [10.0]
    calls = []

    class ImmediateExecutor:
        def __init__(self, **kwargs):
            pass

        def submit(self, function, config, *, run_garbage_collection):
            calls.append(run_garbage_collection)
            future = Future()
            future.set_result(
                OrchestrationWalCheckpointResult(garbage_collection_pending=gc_pending)
            )
            return future

        def shutdown(self, *, wait):
            pass

    monkeypatch.setattr(wal_maintenance, "ThreadPoolExecutor", ImmediateExecutor)
    lane = wal_maintenance.OrchestrationWalCheckpointMaintenance(clock=lambda: now[0])
    config = _config(tmp_path / "orchestration.db")
    try:
        lane.run_if_due(config)
        lane.run_if_due(config)
        assert calls == [True]
        now[0] = 11.0
        lane.run_if_due(config)
        assert calls == [True, True]
    finally:
        lane.close()


def _trace_maintenance_reads(monkeypatch):
    from evander.orchestration import storage_maintenance

    statements = []

    @contextmanager
    def traced(path):
        with connect_orchestration_db_read_only(path) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(storage_maintenance, "connect_orchestration_db_read_only", traced)
    return statements


def test_completed_backfill_does_not_query_the_trigger_queue(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('execution_history_backfill', ?)",
            (json.dumps({"completed": True}),),
        )
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('last_retention_date', ?)",
            (json.dumps({"date": datetime.now(UTC).date().isoformat()}),),
        )
    statements = _trace_maintenance_reads(monkeypatch)

    run_storage_maintenance(_config(path), run_garbage_collection=False)

    assert any("execution_history_backfill" in statement for statement in statements)
    assert not any("FROM trigger_events" in statement for statement in statements)


@pytest.mark.parametrize("delivery_state", ["pending", "inflight", "delivered"])
def test_backfill_waits_for_active_delivery_using_indexed_lookup(
    tmp_path, monkeypatch, delivery_state
):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        connection.execute(
            "INSERT INTO trigger_events "
            "(event_id, delivery_state, trigger_id, trigger_type, trace_id, occurred_at, payload_json) "
            "VALUES ('event', ?, 'trigger', 'trigger:test', 'trace', ?, '{}')",
            (delivery_state, datetime.now(UTC).isoformat()),
        )
    calls = []
    monkeypatch.setattr(
        execution_history,
        "run_execution_history_backfill",
        lambda *args, **kwargs: calls.append(kwargs) or {},
    )
    statements = _trace_maintenance_reads(monkeypatch)

    run_storage_maintenance(_config(path), run_garbage_collection=False)

    assert len(calls) == int(delivery_state == "delivered")
    queue_query = next(statement for statement in statements if "FROM trigger_events" in statement)
    with connect_orchestration_db_read_only(path) as connection:
        plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + queue_query)]
    assert any("SEARCH trigger_events USING COVERING INDEX idx_trigger_events_state" in row for row in plan)
    assert not any("SCAN trigger_events" in row for row in plan)


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


def test_skipping_gc_still_indexes_history_and_runs_retention(tmp_path, monkeypatch):
    from evander.repositories import operational_payloads

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
                "outputs": {"value": 42},
            },
        )
        connection.execute(
            "INSERT INTO workflow_events "
            "(event_id,event_type,source,occurred_at,payload_json) VALUES ('old','test','test',?,'{}')",
            ((datetime.now(UTC) - timedelta(days=40)).isoformat(),),
        )
        connection.execute(
            "INSERT INTO orchestration_metadata VALUES ('retention_progress', ?)",
            (json.dumps({
                "date": datetime.now(UTC).date().isoformat(),
                "phase": "workflow",
                "cutoff": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
                "compact_cutoff": (datetime.now(UTC) - timedelta(days=365)).isoformat(),
            }),),
        )

    gc_calls = []

    def unexpected_gc(*args, **kwargs):
        gc_calls.append(True)
        raise AssertionError("Idle GC must be skipped")

    monkeypatch.setattr(execution_history, "collect_superseded_history_values", unexpected_gc)
    monkeypatch.setattr(operational_payloads, "collect_unreferenced_payloads", unexpected_gc)

    result = run_storage_maintenance(_config(path), run_garbage_collection=False)

    assert result.garbage_collection_pending is None
    assert result.history_index_pending == 0
    assert gc_calls == []
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute(
            "SELECT numeric_value FROM workflow_node_execution_current_values "
            "WHERE execution_key='effect:pending' AND location='output' AND json_pointer='/value'"
        ).fetchone()[0] == 42
        assert connection.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 0


def test_gc_failure_remains_pending_for_a_later_slice(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)

    def failed_gc(*args, **kwargs):
        raise RuntimeError("temporary GC failure")

    monkeypatch.setattr(execution_history, "collect_superseded_history_values", failed_gc)

    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True


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
        assert result.garbage_collection_pending is True
        assert retention_calls == []
        assert foreground.in_transaction


def test_scalar_gc_continues_past_live_prefix_before_becoming_idle(tmp_path):
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
        connection.execute("DELETE FROM workflow_history_index_jobs")
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
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_history_index_values").fetchone()[0] == 160
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    with connect_orchestration_db_read_only(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_history_index_values").fetchone()[0]
            == 150
        )
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is False


def test_operational_gc_continues_past_referenced_prefix_before_becoming_idle(tmp_path):
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
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0] == 160
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    with connect_orchestration_db_read_only(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0]
            == 150
        )
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is False


def _seed_offset_gc_sweep(path, *, other_component):
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        for execution_id in ("current", "legacy"):
            execution_history.archive_execution_record(
                connection, kind="effect", record={
                    "execution_id": execution_id, "event_id": "event",
                    "effect_id": "effect", "effect_type": "effect:test",
                    "status": "succeeded", "started_at": datetime.now(UTC),
                    "input_payload": {}, "outputs": {},
                },
            )
        connection.execute("DELETE FROM workflow_history_index_jobs")
        connection.execute("INSERT INTO workflow_history_index_state VALUES ('effect:current','active')")
        connection.executemany(
            "INSERT INTO workflow_history_index_values VALUES (?,?,?,?,?,?,?,?,?)",
            [("effect:current", "active", "output", f"/{i}", "string", None, "value", None, None)
             for i in range(100)],
        )
        metadata = {
            "last_retention_date": {"date": datetime.now(UTC).date().isoformat()},
            "execution_history_backfill": {"completed": True},
        }
        if other_component == "legacy":
            connection.executemany(
                "INSERT INTO workflow_node_execution_history_values VALUES (?,?,?,?,?,?,?,?)",
                [("effect:legacy", "output", f"/{i}", "string", None, "value", None, None)
                 for i in range(300)],
            )
            metadata["history_scalar_gc_cursor"] = {"current": 0, "legacy": 100}
        else:
            connection.executemany(
                "INSERT INTO workflow_operational_blobs VALUES (?, 'zlib-json-v1', ?, 1)",
                [(f"{i:064x}", b"unused") for i in range(300)],
            )
            connection.executemany(
                "INSERT INTO workflow_operational_record_refs VALUES ('effect','retained',?)",
                [(f"{i:064x}",) for i in range(300)],
            )
            metadata["operational_payload_gc_cursor"] = {"after": f"{99:064x}"}
        connection.executemany(
            "INSERT INTO orchestration_metadata VALUES (?,?)",
            [(key, json.dumps(value)) for key, value in metadata.items()],
        )


def _read_gc_state(path, key):
    with connect_orchestration_db_read_only(path) as connection:
        return json.loads(connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key=?", (key,)
        ).fetchone()[0])


@pytest.mark.parametrize("other_component", ["legacy", "payload"])
def test_offset_gc_sweeps_resume_durable_component_completion(tmp_path, other_component):
    from evander.repositories.sqlite_runtime import close_sqlite_runtimes

    path = tmp_path / "orchestration.db"
    _seed_offset_gc_sweep(path, other_component=other_component)
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    state = _read_gc_state(path, "history_scalar_gc_cursor")
    assert state["current_completed"] is True
    assert state["current"] == 0
    # Reopening the store must retain the completed component while its peer
    # finishes, rather than restarting a sweep at a different phase offset.
    close_sqlite_runtimes(path)
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is False
    assert _read_gc_state(path, "history_scalar_gc_cursor")["current_completed"] is True
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_history_index_values").fetchone()[0] == 100
        table = ("workflow_node_execution_history_values" if other_component == "legacy"
                 else "workflow_operational_blobs")
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 300


def test_completed_real_gc_sweep_waits_then_collects_new_garbage(tmp_path, monkeypatch):
    now = [10.0]
    calls = []

    class ImmediateExecutor:
        def __init__(self, **kwargs):
            pass

        def submit(self, function, config, *, run_garbage_collection):
            calls.append((now[0], run_garbage_collection))
            future = Future()
            future.set_result(function(config, run_garbage_collection=run_garbage_collection))
            return future

        def shutdown(self, *, wait):
            pass

    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    monkeypatch.setattr(wal_maintenance, "ThreadPoolExecutor", ImmediateExecutor)
    lane = wal_maintenance.OrchestrationWalCheckpointMaintenance(clock=lambda: now[0])
    config = _config(path)
    try:
        lane.run_if_due(config)
        assert lane.run_if_due(config).garbage_collection_pending is False
        with connect_orchestration_db(path) as connection:
            connection.execute("INSERT INTO workflow_operational_blobs VALUES ('orphan','zlib-json-v1',X'',0)")
        for moment in (11.0, 39.0):
            now[0] = moment
            lane.run_if_due(config)
        with connect_orchestration_db_read_only(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0] == 1
        now[0] = 40.0
        lane.run_if_due(config)
        assert lane.run_if_due(config).garbage_collection_pending is True
        with connect_orchestration_db_read_only(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0] == 0
        now[0] = 41.0
        lane.run_if_due(config)
        assert lane.run_if_due(config).garbage_collection_pending is False
        assert calls == [(10.0, True), (11.0, False), (39.0, False), (40.0, True), (41.0, True)]
    finally:
        lane.close()


def test_payload_gc_remembers_earlier_parent_deletion_for_followup_sweep(tmp_path):
    path = tmp_path / "orchestration.db"
    _seed_offset_gc_sweep(path, other_component="payload")
    with connect_orchestration_db(path) as connection:
        connection.execute(
            "DELETE FROM workflow_operational_record_refs WHERE sha256 IN (?,?)",
            (f"{0:064x}", f"{199:064x}"),
        )
        connection.execute(
            "INSERT INTO workflow_operational_blob_edges VALUES (?,?)",
            (f"{199:064x}", f"{0:064x}"),
        )
    for _ in range(3):
        assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    # The parent was deleted before the clean tail window. Its earlier child
    # remains behind the cursor and must be reclaimed before the idle pause.
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT 1 FROM workflow_operational_blobs WHERE sha256=?", (f"{0:064x}",)).fetchone()
        assert connection.execute("SELECT 1 FROM workflow_operational_blobs WHERE sha256=?", (f"{199:064x}",)).fetchone() is None
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT 1 FROM workflow_operational_blobs WHERE sha256=?", (f"{0:064x}",)).fetchone() is None
    for _ in range(8):
        if run_storage_maintenance(_config(path)).garbage_collection_pending is False:
            break
    else:
        pytest.fail("A clean follow-up sweep must finish")
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0] == 298


@pytest.mark.parametrize("error", [sqlite3.OperationalError("database is locked"), RuntimeError("GC failed")])
def test_failed_gc_sweep_restart_rolls_back_completion_reset(tmp_path, monkeypatch, error):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is False
    before = _read_gc_state(path, "history_scalar_gc_cursor")
    with connect_orchestration_db(path) as connection:
        connection.execute("INSERT INTO workflow_operational_blobs VALUES ('orphan','zlib-json-v1',X'',0)")
    original = execution_history.collect_superseded_history_values

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(execution_history, "collect_superseded_history_values", fail)
    failed = run_storage_maintenance(_config(path))
    assert failed.garbage_collection_pending is True
    assert (failed.skipped_reason == "storage_busy") == isinstance(error, sqlite3.OperationalError)
    assert _read_gc_state(path, "history_scalar_gc_cursor") == before
    monkeypatch.setattr(execution_history, "collect_superseded_history_values", original)
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is True
    assert run_storage_maintenance(_config(path)).garbage_collection_pending is False
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0] == 0
