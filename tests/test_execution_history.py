from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.events.models import TriggerEvent
from evander.repositories.execution_history import (
    read_node_execution_detail,
    run_execution_history_backfill,
    search_node_executions,
)
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    run_orchestration_retention,
)
from evander.triggers.queue import SqliteTriggerEventQueue


def _effect_record(
    *,
    execution_id: str = "execution-1",
    started_at: datetime | None = None,
    input_payload: dict | None = None,
    outputs: dict | None = None,
) -> EffectExecutionRecord:
    started_at = started_at or datetime(2026, 8, 20, 10, tzinfo=UTC)
    return EffectExecutionRecord(
        execution_id=execution_id,
        idempotency_key=f"key-{execution_id}",
        event_id=f"event-{execution_id}",
        trace_id=f"trace-{execution_id}",
        source_node_id="source-1",
        source_trigger_type="trigger:test",
        effect_id="effect-1",
        effect_type="effect:test",
        status="succeeded",
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=2),
        resolved_config_hash="config-hash",
        resolved_config={"token": "do-not-retain", "large": "x" * 1000},
        input_payload=input_payload or {"request": {"name": "Jonathan"}},
        outputs=outputs or {"user": {"name": "Mikael"}, "score": 7},
        config_revision="revision-1",
    )


def _insert_legacy_effect(path, record: EffectExecutionRecord) -> None:
    payload = record.model_dump(mode="json")
    with connect_orchestration_db(path) as connection:
        connection.execute(
            """
            INSERT INTO effect_executions(
                execution_id, idempotency_key, event_id, trace_id, source_node_id,
                source_trigger_type, status, effect_id, effect_type, started_at,
                finished_at, cleared_at, dismissed_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.execution_id,
                record.idempotency_key,
                record.event_id,
                record.trace_id,
                record.source_node_id,
                record.source_trigger_type,
                record.status,
                record.effect_id,
                record.effect_type,
                datetime_to_storage(record.started_at),
                datetime_to_storage(record.finished_at),
                None,
                None,
                json_dumps(payload),
            ),
        )


def test_execution_history_schema_is_idempotent(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    ensure_orchestration_schema(path)

    with connect_orchestration_db(path) as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "workflow_node_execution_history" in tables
    assert "workflow_execution_payload_blobs" in tables
    assert "workflow_node_execution_history_values" in tables


def test_execution_history_schema_upgrade_does_not_rewrite_v1_records(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    record = _effect_record(execution_id="v1-record")
    _insert_legacy_effect(path, record)
    with connect_orchestration_db(path) as connection:
        original = connection.execute(
            "SELECT payload_json FROM effect_executions WHERE execution_id = 'v1-record'"
        ).fetchone()[0]
        connection.executescript(
            """
            DROP TABLE workflow_node_execution_history_values;
            DROP TABLE workflow_node_execution_history_sources;
            DROP TABLE workflow_node_execution_history;
            DROP TABLE workflow_execution_payload_blobs;
            DELETE FROM schema_migrations;
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            (datetime_to_storage(datetime.now(UTC)),),
        )

    ensure_orchestration_schema(path)

    with connect_orchestration_db(path) as connection:
        upgraded = connection.execute(
            "SELECT payload_json FROM effect_executions WHERE execution_id = 'v1-record'"
        ).fetchone()[0]
        migration_versions = [
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
    assert upgraded == original
    assert migration_versions == [1, 2]


def test_execution_write_archives_sanitized_queryable_payload(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path, retention_days=None)
    store.put(
        _effect_record(
            input_payload={
                "__node_outputs": {"large": "not-retained"},
                "user": {"name": "Jonathan", "password": "secret"},
                "items": [{"hidden": "array-element"}],
            },
            outputs={"user": {"name": "Mikael"}, "score": 7, "optional": None},
        )
    )

    result = search_node_executions(
        path,
        node_id="effect-1",
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 21, tzinfo=UTC),
        statuses=["succeeded"],
        filters=[
            {"location": "output", "path": "/user/name", "operator": "eq", "value": "Mikael"},
            {"location": "output", "path": "/score", "operator": "gte", "value": 7},
        ],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    assert result["matched"] == 1
    assert result["executions"][0]["full_record_available"] is True

    detail = read_node_execution_detail(path, execution_key="effect:execution-1")
    assert "__node_outputs" not in detail["input"]
    assert detail["input"]["user"]["password"] == "[REDACTED]"
    assert detail["input"]["items"] == [{"hidden": "array-element"}]
    assert detail["record_tier"] == "full"
    assert detail["full_record"]["resolved_config"]["token"] == "[REDACTED]"

    no_array_predicate = search_node_executions(
        path,
        node_id="effect-1",
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 21, tzinfo=UTC),
        statuses=[],
        filters=[{"location": "input", "path": "/items/0/hidden", "operator": "exists"}],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    assert no_array_predicate["matched"] == 0

    null_and_missing = search_node_executions(
        path,
        node_id="effect-1",
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 21, tzinfo=UTC),
        statuses=[],
        filters=[
            {"location": "output", "path": "/optional", "operator": "eq", "value": None},
            {"location": "output", "path": "/missing", "operator": "not_exists"},
        ],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    wrong_case = search_node_executions(
        path,
        node_id="effect-1",
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 21, tzinfo=UTC),
        statuses=[],
        filters=[{"location": "output", "path": "/user/name", "operator": "eq", "value": "mikael"}],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    assert null_and_missing["matched"] == 1
    assert wrong_case["matched"] == 0


def test_internal_completion_transport_is_not_a_duplicate_node_execution(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    occurred_at = datetime(2026, 8, 20, 10, tzinfo=UTC)
    queue = SqliteTriggerEventQueue(path, retention_days=None)
    queue.publish(
        TriggerEvent(
            event_id="effect-completion-event",
            trigger_id="effect-1",
            trigger_type="effect:test",
            trace_id="trace-completion",
            occurred_at=occurred_at,
            payload={"ok": True},
        )
    )
    SqliteExecutionStore(path, retention_days=None).put(
        _effect_record(execution_id="effect-execution", started_at=occurred_at)
    )

    result = search_node_executions(
        path,
        node_id="effect-1",
        started_from=occurred_at - timedelta(minutes=1),
        started_to=occurred_at + timedelta(minutes=1),
        statuses=[],
        filters=[],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    assert [item["execution_id"] for item in result["executions"]] == ["effect-execution"]


def test_execution_history_backfill_is_bounded_resumable_and_copy_only(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    first = _effect_record(execution_id="legacy-1")
    second = _effect_record(
        execution_id="legacy-2", started_at=first.started_at + timedelta(minutes=1)
    )
    _insert_legacy_effect(path, first)
    _insert_legacy_effect(path, second)
    with connect_orchestration_db(path) as connection:
        original_payloads = [
            row["payload_json"]
            for row in connection.execute(
                "SELECT payload_json FROM effect_executions ORDER BY execution_id"
            )
        ]

    first_batch = run_execution_history_backfill(path, batch_size=1)
    assert first_batch["last_batch_size"] == 1
    assert first_batch["completed"] is False
    with connect_orchestration_db(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM effect_executions").fetchone()[0] == 2
        archived = connection.execute(
            "SELECT execution_id FROM workflow_node_execution_history ORDER BY started_at"
        ).fetchall()
        assert [row["execution_id"] for row in archived] == ["legacy-1"]
        assert [
            row["payload_json"]
            for row in connection.execute(
                "SELECT payload_json FROM effect_executions ORDER BY execution_id"
            )
        ] == original_payloads

    second_batch = run_execution_history_backfill(path, batch_size=1)
    final_batch = run_execution_history_backfill(path, batch_size=1)
    assert second_batch["last_batch_size"] == 1
    assert final_batch["completed"] is True
    assert run_execution_history_backfill(path, batch_size=1) == final_batch


def test_retention_archives_before_full_record_delete(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    old_record = _effect_record(
        execution_id="expired-full",
        started_at=datetime.now(UTC) - timedelta(days=40),
    )
    _insert_legacy_effect(path, old_record)

    result = run_orchestration_retention(
        path,
        retention_days=30,
        compact_history_retention_days=365,
    )
    assert result.effect_executions_deleted == 1
    with connect_orchestration_db(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM effect_executions").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_node_execution_history").fetchone()[0]
            == 1
        )
    detail = read_node_execution_detail(path, execution_key="effect:expired-full")
    assert detail["record_tier"] == "compact"
    assert detail["full_record"] is None


def test_execution_history_uses_stable_keyset_pagination(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path, retention_days=None)
    base = datetime(2026, 8, 20, 10, tzinfo=UTC)
    for index in range(3):
        store.put(
            _effect_record(
                execution_id=f"page-{index}",
                started_at=base + timedelta(minutes=index),
            )
        )

    first = search_node_executions(
        path,
        node_id="effect-1",
        started_from=base - timedelta(minutes=1),
        started_to=base + timedelta(hours=1),
        statuses=[],
        filters=[],
        limit=2,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    second = search_node_executions(
        path,
        node_id="effect-1",
        started_from=base - timedelta(minutes=1),
        started_to=base + timedelta(hours=1),
        statuses=[],
        filters=[],
        limit=2,
        cursor=first["next_cursor"],
        full_retention_days=30,
        compact_retention_days=365,
    )
    assert [item["execution_id"] for item in first["executions"]] == ["page-2", "page-1"]
    assert [item["execution_id"] for item in second["executions"]] == ["page-0"]
    assert first["matched"] == second["matched"] == 3


def test_compact_retention_cascades_indexes_and_garbage_collects_blobs(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path, retention_days=None)
    store.put(
        _effect_record(
            execution_id="compact-expired",
            started_at=datetime.now(UTC) - timedelta(days=400),
        )
    )

    result = run_orchestration_retention(
        path,
        retention_days=30,
        compact_history_retention_days=365,
    )
    assert result.effect_executions_deleted == 1
    assert result.compact_executions_deleted == 1
    assert result.compact_payload_blobs_deleted >= 1
    with connect_orchestration_db(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workflow_node_execution_history_values"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_execution_payload_blobs").fetchone()[
                0
            ]
            == 0
        )


def test_gate_merge_compact_storage_reduces_duplicate_payload_by_ninety_percent(
    tmp_path,
) -> None:
    path = tmp_path / "orchestration.db"
    store = SqliteGateExecutionStore(path, retention_days=None)
    large_payload = {"messages": ["same payload" * 1000], "user": "Mikael"}
    started = datetime(2026, 8, 20, 10, tzinfo=UTC)
    record = GateExecutionRecord(
        execution_id="gate-large",
        idempotency_key="gate-large-key",
        event_id="gate-large-event",
        trace_id="gate-large-trace",
        source_node_id="source-0",
        source_trigger_type="trigger:test",
        gate_id="gate-merge-2",
        gate_type="gate:merge",
        status="succeeded",
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        resolved_config_hash="hash",
        resolved_config={"large_unused_config": "x" * 20_000},
        input_payload=large_payload,
        outputs={"user": "Mikael"},
        inputs_by_source={f"source-{index}": large_payload for index in range(20)},
        input_event_ids_by_source={f"source-{index}": f"event-{index}" for index in range(20)},
    )
    store.put(record)

    with connect_orchestration_db(path) as connection:
        full_size = connection.execute(
            "SELECT length(payload_json) FROM gate_executions WHERE execution_id = ?",
            (record.execution_id,),
        ).fetchone()[0]
        compact_size = connection.execute(
            """
            SELECT SUM(b.compressed_bytes)
            FROM workflow_execution_payload_blobs b
            WHERE b.sha256 IN (
                SELECT input_blob_sha256 FROM workflow_node_execution_history
                WHERE execution_key = 'gate:gate-large'
                UNION
                SELECT output_blob_sha256 FROM workflow_node_execution_history
                WHERE execution_key = 'gate:gate-large'
            )
            """
        ).fetchone()[0]
    assert compact_size < full_size * 0.1
