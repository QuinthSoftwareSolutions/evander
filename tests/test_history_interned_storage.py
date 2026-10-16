from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3

import pytest

from completion_recovery_support import (
    assert_completion_query_plans,
    bounded_completion_queries,
    seed_completed_executions,
)
from history_storage_support import seed_indexed_history
from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.orchestration.history_index_migration import copy_history_index_storage
from evander.repositories import execution_history as history
from evander.repositories import history_index_storage as storage
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    ensure_orchestration_schema,
    run_orchestration_retention_slice,
)
from evander.repositories.sqlite_runtime import close_sqlite_runtimes


def record(key, outputs, *, age=0):
    at = datetime.now(UTC) - timedelta(days=age)
    return EffectExecutionRecord(
        execution_id=key,
        idempotency_key=key,
        event_id="event-" + key,
        trace_id="trace-" + key,
        source_node_id="source",
        source_trigger_type="trigger:test",
        effect_id="node",
        effect_type="effect:test",
        status="succeeded",
        started_at=at,
        finished_at=at,
        resolved_config_hash="hash",
        resolved_config={},
        input_payload={},
        outputs=outputs,
    )


def drain(path):
    for _ in range(100):
        result = history.run_history_indexing_slice(path, writer_time_budget_seconds=5)
        with connect_orchestration_db(path) as connection:
            if not connection.execute("SELECT 1 FROM workflow_history_index_jobs").fetchone():
                return
        assert result["indexed_values"] or result["completed"]
    pytest.fail("Indexing did not finish")


def intern(path):
    with connect_orchestration_db(path) as connection:
        storage.convert_history_index_storage(connection)


def search(path, filters=()):
    now = datetime.now(UTC)
    return history.search_node_executions(
        path,
        node_id="node",
        started_from=now - timedelta(days=1000),
        started_to=now + timedelta(days=1),
        statuses=[],
        filters=list(filters),
        limit=100,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )


def logical_values(connection):
    return {
        table: [
            tuple(row)
            for row in connection.execute(
                f"SELECT rowid,{('execution_key,generation,' if table == storage.OLD_CURRENT else 'execution_key,')}"
                f"location,json_pointer,{storage.VALUE_COLUMNS} FROM {table} ORDER BY rowid"
            )
        ]
        for table in (storage.OLD_CURRENT, storage.OLD_LEGACY)
    }


def test_conversion_roundtrip_preserves_values_rowids_and_payloads(tmp_path):
    path = tmp_path / "history.db"
    repository = SqliteExecutionStore(path)
    repository.put(
        record(
            "first",
            {
                "unicode/tilde~": "Å",
                "n": 2**100,
                "null": None,
                "bool": True,
                "array": [1, 2],
                "nested": {"n": -0.0},
            },
        )
    )
    repository.put(record("legacy", {"n": 1.25, "text": "z" * 1025}))
    drain(path)
    with connect_orchestration_db(path) as c:
        c.execute(
            f"INSERT INTO {storage.OLD_LEGACY} SELECT execution_key,location,json_pointer,{storage.VALUE_COLUMNS} FROM {storage.OLD_CURRENT} WHERE execution_key='effect:legacy'"
        )
        c.execute(f"DELETE FROM {storage.OLD_CURRENT} WHERE execution_key='effect:legacy'")
        c.execute("DELETE FROM workflow_history_index_state WHERE execution_key='effect:legacy'")
        c.execute(
            f"UPDATE {storage.OLD_CURRENT} SET rowid=-2 WHERE rowid=(SELECT MIN(rowid) FROM {storage.OLD_CURRENT})"
        )
        c.execute(
            f"UPDATE {storage.OLD_LEGACY} SET rowid=1000000 WHERE rowid=(SELECT MAX(rowid) FROM {storage.OLD_LEGACY})"
        )
        before = logical_values(c)
        payloads = [
            tuple(row)
            for row in c.execute("SELECT * FROM workflow_execution_payload_blobs ORDER BY sha256")
        ]
        storage.convert_history_index_storage(c)
        assert logical_values(c) == before
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    ensure_orchestration_schema(path, force=True)
    with connect_orchestration_db(path) as c:
        assert storage.interned_schema_is_current(c)
        assert [
            tuple(row)
            for row in c.execute("SELECT * FROM workflow_execution_payload_blobs ORDER BY sha256")
        ] == payloads
        storage.convert_history_index_storage(c, interned=False)
        assert logical_values(c) == before
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_batch_rollback_stale_work_and_dictionary_reuse(tmp_path):
    path = tmp_path / "history.db"
    repository = SqliteExecutionStore(path)
    intern(path)
    entry = record("work", {f"field-{n:03}": n for n in range(220)})
    repository.put(entry)
    prepared = history.prepare_pending_history_indexing(path)
    with pytest.raises(RuntimeError), connect_orchestration_db(path) as c:
        history.run_pending_history_indexing(c, prepared=prepared)
        raise RuntimeError("rollback")
    with connect_orchestration_db(path) as c:
        assert c.execute(f"SELECT COUNT(*) FROM {storage.CURRENT}").fetchone()[0] == 0
        assert c.execute(f"SELECT COUNT(*) FROM {storage.PATHS}").fetchone()[0] == 0
        assert c.execute("SELECT next_offset FROM workflow_history_index_jobs").fetchone()[0] == 0
        history.run_pending_history_indexing(c, prepared=prepared)
    old_second = history.prepare_pending_history_indexing(path)
    entry.outputs = {"replacement": 2**100}
    repository.put(entry)
    assert search(path)["indexing_complete"] is False
    assert (
        search(path, [{"location": "output", "path": "/replacement", "operator": "exists"}])[
            "matched"
        ]
        == 0
    )
    with connect_orchestration_db(path) as c:
        assert history.run_pending_history_indexing(c, prepared=old_second) == {
            "indexed_values": 0,
            "completed": 0,
        }
    drain(path)
    assert (
        search(
            path,
            [{"location": "output", "path": "/replacement", "operator": "eq", "value": 2**100}],
        )["matched"]
        == 1
    )
    assert (
        search(path, [{"location": "output", "path": "/field-000", "operator": "exists"}])[
            "matched"
        ]
        == 0
    )
    with connect_orchestration_db(path) as c:
        for _ in range(20):
            history.collect_superseded_history_values(c)
        assert c.execute(f"SELECT COUNT(*) FROM {storage.CURRENT}").fetchone()[0] == 1
        assert c.execute(f"SELECT COUNT(*) FROM {storage.PATHS}").fetchone()[0] == 1
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_reused_reports_follow_format_conversion_and_generation_changes(tmp_path):
    path = tmp_path / "history.db"
    repository = SqliteExecutionStore(path)
    entry = record("changing", {"amount": 2**100})
    repository.put(entry)
    drain(path)
    old_filter = [{"location": "output", "path": "/amount", "operator": "eq", "value": 2**100}]
    assert search(path, old_filter)["matched"] == 1
    assert history.read_node_execution_detail(path, execution_key="effect:changing")["output"] == {"amount": 2**100}
    # Keep the inode and the report's idle connection while changing its schema.
    intern(path)
    assert search(path, old_filter)["matched"] == 1
    entry.outputs = {"amount": 2**100 + 1}
    repository.put(entry)
    assert search(path, old_filter)["matched"] == 0
    drain(path)
    assert search(path, old_filter)["matched"] == 0
    new_filter = [{"location": "output", "path": "/amount", "operator": "eq", "value": 2**100 + 1}]
    assert search(path, new_filter)["matched"] == 1
    with connect_orchestration_db(path) as connection:
        storage.convert_history_index_storage(connection, interned=False)
    assert search(path, new_filter)["matched"] == 1
    assert history.read_node_execution_detail(path, execution_key="effect:changing")["output"] == {"amount": 2**100 + 1}
    close_sqlite_runtimes(path)


def test_direct_reads_cover_current_and_legacy_without_compatibility_view(tmp_path, monkeypatch):
    path = tmp_path / "history.db"
    repository = SqliteExecutionStore(path)
    for key in ("current", "legacy"):
        repository.put(record(key, {"n": 2**100, "present": None}))
    drain(path)
    with connect_orchestration_db(path) as c:
        c.execute(
            f"INSERT INTO {storage.OLD_LEGACY} SELECT execution_key,location,json_pointer,{storage.VALUE_COLUMNS} FROM {storage.OLD_CURRENT} WHERE execution_key='effect:legacy'"
        )
        c.execute(f"DELETE FROM {storage.OLD_CURRENT} WHERE execution_key='effect:legacy'")
        c.execute("DELETE FROM workflow_history_index_state WHERE execution_key='effect:legacy'")
        storage.convert_history_index_storage(c)
    original = history.connect_orchestration_db_read_only
    statements = []

    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as c:
            c.set_trace_callback(statements.append)
            yield c

    monkeypatch.setattr(history, "connect_orchestration_db_read_only", traced)
    for operator, value in [("eq", 2**100), ("gte", 2**100), ("lt", 2**100 + 1)]:
        assert (
            search(
                path, [{"location": "output", "path": "/n", "operator": operator, "value": value}]
            )["matched"]
            == 2
        )
    assert (
        search(path, [{"location": "output", "path": "/missing", "operator": "not_exists"}])[
            "matched"
        ]
        == 2
    )
    assert (
        search(path, [{"location": "output", "path": "/missing", "operator": "exists"}])["matched"]
        == 0
    )
    now = datetime.now(UTC)
    fields = history.list_node_execution_fields(
        path,
        node_id="node",
        started_from=now - timedelta(days=1),
        started_to=now + timedelta(days=1),
    )["fields"]
    assert fields == [
        {"location": "output", "json_pointer": "/n", "value_type": "number", "observed_count": 2},
        {
            "location": "output",
            "json_pointer": "/present",
            "value_type": "null",
            "observed_count": 2,
        },
    ]
    assert not any("FROM workflow_node_execution_current_values" in sql for sql in statements)


def test_retention_remains_bounded_and_preserves_live_shared_paths(tmp_path):
    path = tmp_path / "history.db"
    repository = SqliteExecutionStore(path)
    intern(path)
    outputs = {f"shared-{n}": n for n in range(45)}
    repository.put(record("expired", outputs, age=800))
    repository.put(record("live", outputs))
    drain(path)
    with connect_orchestration_db(path) as c:
        c.execute(
            "INSERT INTO orchestration_metadata VALUES('retention_batch_limits',?)",
            (json.dumps({"records": 2, "index_rows": 7}),),
        )
    for _ in range(100):
        with connect_orchestration_db(path) as c:
            limit = json.loads(
                c.execute(
                    "SELECT value_json FROM orchestration_metadata WHERE key='retention_batch_limits'"
                ).fetchone()[0]
            )["index_rows"]
        result = run_orchestration_retention_slice(
            path,
            retention_days=30,
            compact_history_retention_days=365,
            batch_size=2,
            time_budget_seconds=5,
            busy_timeout_ms=0,
        )
        assert result.compact_index_rows_deleted <= limit
        if not result.remaining:
            break
    else:
        pytest.fail("Retention did not finish")
    with pytest.raises(ValueError):
        history.read_node_execution_detail(path, execution_key="effect:expired")
    assert (
        history.read_node_execution_detail(path, execution_key="effect:live")["output"] == outputs
    )
    with connect_orchestration_db(path) as c:
        for _ in range(10):
            history.collect_superseded_history_values(c)
        assert c.execute(f"SELECT COUNT(*) FROM {storage.EXECUTIONS}").fetchone()[0] == 1
        assert c.execute(f"SELECT COUNT(*) FROM {storage.PATHS}").fetchone()[0] == 45
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("source_interned,target_interned", [(False, True), (True, False),
                                                           (False, False), (True, True)])
@pytest.mark.parametrize("analyzed", [False, True])
def test_copy_preserves_unrelated_statistics_and_bounded_recovery(
    tmp_path, monkeypatch, source_interned, target_interned, analyzed
):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    seed_indexed_history(source, executions=2, fields=3)
    if source_interned:
        intern(source)
    for kind in ("effect", "gate"):
        seed_completed_executions(source, kind=kind)
    rebuilt = {storage.EXECUTIONS, storage.GENERATIONS, storage.PATHS, storage.CURRENT,
               storage.LEGACY, storage.OLD_CURRENT, storage.OLD_LEGACY}

    def statistics(connection):
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'"
        ).fetchone():
            return []
        return sorted(tuple(row) for row in connection.execute("SELECT * FROM sqlite_stat1")
                      if source_interned == target_interned or row[0] not in rebuilt)

    with connect_orchestration_db(source) as connection:
        if analyzed:
            connection.execute("ANALYZE")
        original_stats = statistics(connection)
    close_sqlite_runtimes(source)
    result = copy_history_index_storage(source, target, interned=target_interned)
    assert result["changed"] == (source_interned != target_interned)
    with sqlite3.connect(target) as connection:
        assert statistics(connection) == original_stats
        if result["changed"]:
            history_tables = (
                {storage.EXECUTIONS, storage.GENERATIONS, storage.PATHS,
                 storage.CURRENT, storage.LEGACY}
                if target_interned else {storage.OLD_CURRENT, storage.OLD_LEGACY}
            )
            assert history_tables <= {row[0] for row in connection.execute("SELECT * FROM sqlite_stat1")}
    for kind, model in (("effect", SqliteExecutionStore), ("gate", SqliteGateExecutionStore)):
        store = model(target)
        with bounded_completion_queries(monkeypatch) as statements:
            assert store.list_completion_pending_records() == []
            assert store.list_completion_pending_records(after=("2026-01-01T00:00:00Z", "z")) == []
        with sqlite3.connect(target) as connection:
            assert_completion_query_plans(connection, statements, kind=kind)


def test_copy_migration_never_changes_source_or_overwrites_destination(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    repository = SqliteExecutionStore(source)
    repository.put(record("copy", {"n": 17}))
    drain(source)
    close_sqlite_runtimes(source)
    with sqlite3.connect(source) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
    before = hashlib.sha256(source.read_bytes()).digest()
    copy_history_index_storage(source, target)
    assert hashlib.sha256(source.read_bytes()).digest() == before
    assert (
        search(target, [{"location": "output", "path": "/n", "operator": "eq", "value": 17}])[
            "matched"
        ]
        == 1
    )
    with pytest.raises(ValueError):
        copy_history_index_storage(source, target)
    legacy = tmp_path / "legacy.db"
    close_sqlite_runtimes(target)
    copy_history_index_storage(target, legacy, interned=False)
    with sqlite3.connect(legacy) as c:
        assert not storage.uses_interned_history(c)
    assert search(legacy)["matched"] == 1


def test_failed_conversion_leaves_source_intact_and_no_published_output(tmp_path, monkeypatch):
    from evander.orchestration import history_index_migration

    source, target = tmp_path / "source.db", tmp_path / "target.db"
    SqliteExecutionStore(source).put(record("copy", {"value": "retained"}))
    close_sqlite_runtimes(source)
    with sqlite3.connect(source) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
    before = hashlib.sha256(source.read_bytes()).digest()

    def interrupted(connection, **kwargs):
        connection.execute("DELETE FROM workflow_node_execution_history")
        raise RuntimeError("interrupted conversion")

    monkeypatch.setattr(history_index_migration, "convert_history_index_storage", interrupted)
    with pytest.raises(RuntimeError, match="interrupted conversion"):
        copy_history_index_storage(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".history-index-*"))
    assert hashlib.sha256(source.read_bytes()).digest() == before
    assert history.read_node_execution_detail(source, execution_key="effect:copy")["output"] == {
        "value": "retained"
    }


@pytest.mark.parametrize("damage", ["source_reference", "converted_reference"])
def test_deferred_foreign_keys_cannot_publish_invalid_copy(tmp_path, monkeypatch, damage):
    from evander.orchestration import history_index_migration

    source, target = tmp_path / "source.db", tmp_path / "target.db"
    SqliteExecutionStore(source).put(record("copy", {"value": "retained"}))
    drain(source)
    close_sqlite_runtimes(source)
    with sqlite3.connect(source) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
        if damage == "source_reference":
            c.execute("UPDATE workflow_node_execution_history SET output_blob_sha256='missing'")
    before = hashlib.sha256(source.read_bytes()).digest()
    if damage == "converted_reference":
        original = history_index_migration.convert_history_index_storage

        def corrupt(connection, **kwargs):
            result = original(connection, **kwargs)
            connection.execute(f"UPDATE {storage.CURRENT} SET path_ref=-999")
            return result

        monkeypatch.setattr(history_index_migration, "convert_history_index_storage", corrupt)
    phases = []

    def observe(phase):
        assert not target.exists()
        phases.append(phase)

    with pytest.raises(ValueError, match="integrity or foreign-key"):
        copy_history_index_storage(source, target, cache_mib=8, progress=observe)
    assert "foreign_key_check" in phases
    assert not target.exists()
    assert not list(tmp_path.glob(".history-index-*"))
    assert hashlib.sha256(source.read_bytes()).digest() == before


@pytest.mark.parametrize("compact", [False, True])
def test_missing_scalar_reference_cannot_be_hidden_by_conversion_join(tmp_path, compact):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    SqliteExecutionStore(source).put(record("copy", {"value": "retained"}))
    drain(source)
    if compact:
        intern(source)
    close_sqlite_runtimes(source)
    with sqlite3.connect(source) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
        table = storage.CURRENT if compact else storage.OLD_CURRENT
        column, missing = ("path_ref", -999) if compact else ("execution_key", "missing")
        c.execute(
            f"UPDATE {table} SET {column}=? WHERE rowid=(SELECT MIN(rowid) FROM {table})",
            (missing,),
        )
    before = hashlib.sha256(source.read_bytes()).digest()
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        copy_history_index_storage(source, target, interned=not compact, cache_mib=8)
    assert not target.exists()
    assert not list(tmp_path.glob(".history-index-*"))
    assert hashlib.sha256(source.read_bytes()).digest() == before


def test_conversion_cli_requires_separate_offline_output(tmp_path):
    from typer.testing import CliRunner

    from evander.cli import app

    source, target = tmp_path / "source.db", tmp_path / "target.db"
    SqliteExecutionStore(source).put(record("copy", {"n": 17}))
    runner = CliRunner()
    args = ["convert-history-index", "--database", str(source), "--output", str(target)]
    refused = runner.invoke(app, args)
    assert refused.exit_code != 0
    assert not target.exists()
    converted = runner.invoke(app, [*args, "--offline", "--cache-mib", "8"])
    assert converted.exit_code == 0, converted.output
    result = json.loads(converted.stdout)
    assert result["format"] == "interned"
    assert result["integrity_check"] == "ok"
    assert result["cache_mib"] == 8
    assert "history-index: integrity_check" in converted.stderr
    assert "integrity_check" in result["phase_seconds"]
    assert history.read_node_execution_detail(target, execution_key="effect:copy")["output"] == {
        "n": 17
    }


def test_payload_downgrade_refuses_interned_format_without_mutation(tmp_path):
    from evander.orchestration.storage_migration import (
        check_orchestration_storage,
        migrate_orchestration_storage,
    )

    path = tmp_path / "history.db"
    SqliteExecutionStore(path).put(record("copy", {"n": 17}))
    intern(path)
    close_sqlite_runtimes(path)
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
    before = hashlib.sha256(path.read_bytes()).digest()
    assert check_orchestration_storage(path)["history_index_format"] == "interned"
    assert not check_orchestration_storage(path)["downgrade_ready"]
    with pytest.raises(ValueError, match="convert-history-index --legacy"):
        migrate_orchestration_storage(path, downgrade=True)
    assert hashlib.sha256(path.read_bytes()).digest() == before


@pytest.mark.parametrize("damage", ["missing_index", "unsupported_version"])
def test_startup_refuses_incomplete_or_unsupported_interned_schema(tmp_path, damage):
    path = tmp_path / "history.db"
    SqliteExecutionStore(path).put(record("copy", {"value": "retained"}))
    intern(path)
    with connect_orchestration_db(path) as c:
        if damage == "missing_index":
            c.execute("DROP INDEX idx_history_entries_path")
        else:
            c.execute(
                "UPDATE orchestration_metadata SET value_json=? WHERE key=?",
                ('{"format":"interned","version":999}', storage.FORMAT_KEY),
            )
    with pytest.raises(ValueError, match="Incomplete or unsupported interned history index"):
        ensure_orchestration_schema(path, force=True)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM workflow_node_execution_history").fetchone()[0] == 1
        assert not storage.interned_schema_is_current(c)


def test_maintenance_finishes_dictionary_sweeps_before_becoming_idle(tmp_path):
    from types import SimpleNamespace

    from evander.orchestration.storage_maintenance import run_storage_maintenance

    path = tmp_path / "history.db"
    repository = SqliteExecutionStore(path)
    intern(path)
    repository.put(record("live", {"retained": 17}))
    drain(path)
    with connect_orchestration_db(path) as c:
        # Old scalar sweeps can already be complete when dictionary cleanup has
        # work left after conversion or bounded retention.
        for table in (storage.PATHS, storage.GENERATIONS):
            c.executemany(
                f"INSERT INTO {table}(value) VALUES(?)",
                [(f"unreferenced-{n}",) for n in range(250)],
            )
        c.execute(
            "INSERT INTO orchestration_metadata VALUES('history_scalar_gc_cursor',?)",
            (json.dumps({"current_completed": True, "legacy_completed": True}),),
        )
        c.execute(
            "INSERT INTO orchestration_metadata VALUES('operational_payload_gc_cursor',?)",
            (json.dumps({"completed": True}),),
        )
    config = SimpleNamespace(
        orchestration_store_path=path,
        orchestration_wal_checkpoint_mode="disabled",
        orchestration_wal_checkpoint_min_bytes=0,
        orchestration_wal_checkpoint_interval_seconds=300,
        orchestration_wal_checkpoint_busy_timeout_ms=100,
        orchestration_retention_days=30,
        orchestration_compact_history_retention_days=365,
    )
    assert run_storage_maintenance(config).garbage_collection_pending is True
    close_sqlite_runtimes(path)
    for _ in range(10):
        if run_storage_maintenance(config).garbage_collection_pending is False:
            break
    else:
        pytest.fail("Dictionary sweeps did not finish")
    with connect_orchestration_db(path) as c:
        assert c.execute(f"SELECT COUNT(*) FROM {storage.PATHS}").fetchone()[0] == 1
        assert c.execute(f"SELECT COUNT(*) FROM {storage.GENERATIONS}").fetchone()[0] == 1
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    assert history.read_node_execution_detail(path, execution_key="effect:live")["output"] == {
        "retained": 17
    }
