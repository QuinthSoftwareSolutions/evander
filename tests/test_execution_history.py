from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import evander.repositories.orchestration_db as orchestration_db
from evander.admin.payloads import WorkflowNodeExecutionFilterPayload
from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.events.models import TriggerEvent
from evander.repositories.execution_history import (
    list_node_execution_authorization_paths,
    list_node_execution_fields,
    read_node_execution_detail,
    read_node_execution_authorization_path,
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
from evander.workflow.events import SqliteWorkflowEventJournal


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
    assert migration_versions == [1, 2, 3]


def test_execution_history_v3_timestamp_rewrite_runs_only_during_migration(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.execute(
            """
            INSERT INTO workflow_node_execution_history(
                execution_key, execution_kind, execution_id, event_id, trace_id,
                node_id, node_type, status, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "effect:legacy-timestamp",
                "effect",
                "legacy-timestamp",
                "event-legacy-timestamp",
                None,
                "effect-1",
                "effect:test",
                "succeeded",
                "2026-08-20T10:00:00Z",
                "2026-08-20T10:00:01Z",
                datetime_to_storage(datetime.now(UTC)),
            ),
        )

    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        normalized = connection.execute(
            """
            SELECT started_at, finished_at
            FROM workflow_node_execution_history
            WHERE execution_key = 'effect:legacy-timestamp'
            """
        ).fetchone()
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        monkeypatch.setattr(orchestration_db, "connect_orchestration_db", lambda _path: connection)
        orchestration_db.ensure_orchestration_schema(path)
        connection.set_trace_callback(None)

    assert tuple(normalized) == ("2026-08-20T10:00:00.000000Z", "2026-08-20T10:00:01.000000Z")
    assert not any(
        "UPDATE workflow_node_execution_history" in statement for statement in statements
    )


def test_execution_write_archives_sanitized_queryable_payload(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path, retention_days=None)
    store.put(
        _effect_record(
            input_payload={
                "__node_outputs": {"large": "not-retained"},
                "user": {
                    "name": "Jonathan",
                    "password": "password-value",
                    "pat": "pat-value",
                    "pwd": "pwd-value",
                    "ssh_key": "ssh-key-value",
                    "privateKey": "private-key-value",
                    "sshKey": "ssh-key-camel-value",
                    "adoPat": "ado-pat-value",
                    "path": "/safe/path",
                },
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
    assert result["executions"][0]["config_hash"] == "config-hash"

    detail = read_node_execution_detail(path, execution_key="effect:execution-1")
    assert "__node_outputs" not in detail["input"]
    assert detail["input"]["user"]["password"] == "[REDACTED]"
    assert detail["input"]["user"]["pat"] == "[REDACTED]"
    assert detail["input"]["user"]["pwd"] == "[REDACTED]"
    assert detail["input"]["user"]["ssh_key"] == "[REDACTED]"
    assert detail["input"]["user"]["privateKey"] == "[REDACTED]"
    assert detail["input"]["user"]["sshKey"] == "[REDACTED]"
    assert detail["input"]["user"]["adoPat"] == "[REDACTED]"
    assert detail["input"]["user"]["path"] == "/safe/path"
    assert detail["input"]["items"] == [{"hidden": "array-element"}]
    assert detail["record_tier"] == "full"
    assert detail["config_hash"] == "config-hash"
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

    with connect_orchestration_db(path) as connection:
        indexed_secrets = connection.execute(
            """
            SELECT COUNT(*)
            FROM workflow_node_execution_history_values
            WHERE text_value IN (
                'password-value', 'pat-value', 'pwd-value', 'ssh-key-value',
                'private-key-value', 'ssh-key-camel-value', 'ado-pat-value'
            )
            """
        ).fetchone()[0]
    assert indexed_secrets == 0

    containers = search_node_executions(
        path,
        node_id="effect-1",
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 21, tzinfo=UTC),
        statuses=[],
        filters=[
            {"location": "input", "path": "/user", "operator": "exists"},
            {"location": "input", "path": "/items", "operator": "exists"},
        ],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    missing_array = search_node_executions(
        path,
        node_id="effect-1",
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 21, tzinfo=UTC),
        statuses=[],
        filters=[{"location": "input", "path": "/items", "operator": "not_exists"}],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    fields = list_node_execution_fields(
        path,
        node_id="effect-1",
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 21, tzinfo=UTC),
    )
    assert containers["matched"] == 1
    assert containers["executions"][0]["matched_values"] == {
        "input:/items": "array",
        "input:/user": "object",
    }
    assert missing_array["matched"] == 0
    assert {
        item["value_type"] for item in fields["fields"] if item["json_pointer"] == "/items"
    } == {"container"}


def test_execution_history_numeric_equality_is_exact_and_huge_values_do_not_fail(
    tmp_path,
) -> None:
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path, retention_days=None)
    started_at = datetime(2026, 8, 20, 10, tzinfo=UTC)
    lower = 9_007_199_254_740_992
    upper = 9_007_199_254_740_993
    enormous = 10**400
    store.put(
        _effect_record(
            execution_id="exact-lower",
            started_at=started_at,
            outputs={"number": lower},
        )
    )
    store.put(
        _effect_record(
            execution_id="exact-upper",
            started_at=started_at + timedelta(seconds=1),
            outputs={"number": upper},
        )
    )
    store.put(
        _effect_record(
            execution_id="exact-enormous",
            started_at=started_at + timedelta(seconds=2),
            outputs={"number": enormous},
        )
    )
    store.put(
        _effect_record(
            execution_id="safe-float",
            started_at=started_at + timedelta(seconds=3),
            outputs={"number": 7.0},
        )
    )

    def matching(value: int) -> list[str]:
        result = search_node_executions(
            path,
            node_id="effect-1",
            started_from=started_at - timedelta(seconds=1),
            started_to=started_at + timedelta(minutes=1),
            statuses=[],
            filters=[{"location": "output", "path": "/number", "operator": "eq", "value": value}],
            limit=50,
            cursor=None,
            full_retention_days=30,
            compact_retention_days=365,
        )
        return [item["execution_id"] for item in result["executions"]]

    assert matching(lower) == ["exact-lower"]
    assert matching(upper) == ["exact-upper"]
    assert matching(enormous) == ["exact-enormous"]
    assert matching(7) == ["safe-float"]
    greater_than_lower = search_node_executions(
        path,
        node_id="effect-1",
        started_from=started_at - timedelta(seconds=1),
        started_to=started_at + timedelta(minutes=1),
        statuses=[],
        filters=[{"location": "output", "path": "/number", "operator": "gt", "value": lower}],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    assert [item["execution_id"] for item in greater_than_lower["executions"]] == [
        "exact-enormous",
        "exact-upper",
    ]
    assert (
        WorkflowNodeExecutionFilterPayload(
            location="output", path="/number", operator="gt", value=enormous
        ).value
        == enormous
    )
    with connect_orchestration_db(path) as connection:
        numeric_value = connection.execute(
            """
            SELECT numeric_value
            FROM workflow_node_execution_history_values
            WHERE execution_key = 'effect:exact-enormous' AND json_pointer = '/number'
            """
        ).fetchone()[0]
    assert numeric_value is None

    with connect_orchestration_db(path) as connection:
        connection.execute(
            """
            UPDATE workflow_node_execution_history_values
            SET text_value = NULL
            WHERE execution_key = 'effect:safe-float' AND json_pointer = '/number'
            """
        )
    with pytest.raises(ValueError, match="legacy history values"):
        search_node_executions(
            path,
            node_id="effect-1",
            started_from=started_at - timedelta(seconds=1),
            started_to=started_at + timedelta(minutes=1),
            statuses=[],
            filters=[{"location": "output", "path": "/number", "operator": "gte", "value": 0}],
            limit=50,
            cursor=None,
            full_retention_days=30,
            compact_retention_days=365,
        )


def test_execution_history_timestamp_windows_are_chronological_at_exact_seconds(
    tmp_path,
) -> None:
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path, retention_days=None)
    exact_second = datetime(2026, 8, 20, 10, tzinfo=UTC)
    store.put(
        _effect_record(
            execution_id="fractional",
            started_at=exact_second + timedelta(microseconds=500_000),
        )
    )

    included = search_node_executions(
        path,
        node_id="effect-1",
        started_from=exact_second,
        started_to=exact_second + timedelta(seconds=1),
        statuses=[],
        filters=[],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    excluded = search_node_executions(
        path,
        node_id="effect-1",
        started_from=exact_second - timedelta(seconds=1),
        started_to=exact_second,
        statuses=[],
        filters=[],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )

    assert [item["execution_id"] for item in included["executions"]] == ["fractional"]
    assert excluded["matched"] == 0


def test_execution_history_records_snapshot_directory_and_legacy_scope_is_root(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        connection.execute(
            """
            INSERT INTO workflow_config_snapshots(revision, captured_at, payload_json)
            VALUES (?, ?, ?)
            """,
            (
                "revision-1",
                "2026-08-20T09:59:00Z",
                json_dumps(
                    {
                        "revision": "revision-1",
                        "captured_at": "2026-08-20T09:59:00Z",
                        "workflow_graphs": [
                            {
                                "id": "graph-old",
                                "directory_path": "root/old",
                                "node_ids": ["effect-1"],
                            }
                        ],
                    }
                ),
            ),
        )
    started_at = datetime(2026, 8, 20, 10, tzinfo=UTC)
    SqliteExecutionStore(path, retention_days=None).put(
        _effect_record(execution_id="scoped", started_at=started_at)
    )
    SqliteExecutionStore(path, retention_days=None).put(
        _effect_record(execution_id="legacy-root", started_at=started_at).model_copy(
            update={"config_revision": None}
        )
    )

    paths = list_node_execution_authorization_paths(
        path,
        node_id="effect-1",
        started_from=started_at - timedelta(seconds=1),
        started_to=started_at + timedelta(minutes=1),
    )
    assert paths == ("root", "root/old")
    assert read_node_execution_authorization_path(path, execution_key="effect:scoped") == "root/old"
    assert (
        read_node_execution_authorization_path(path, execution_key="effect:legacy-root") == "root"
    )

    event = TriggerEvent(
        event_id="trigger-scoped",
        trigger_id="effect-1",
        trigger_type="trigger:test",
        trace_id="trigger-trace",
        occurred_at=started_at,
        payload={"value": "ok"},
    )
    SqliteTriggerEventQueue(path, retention_days=None).publish(
        event,
        config_revision="revision-1",
    )
    trigger_history = search_node_executions(
        path,
        node_id="effect-1",
        started_from=started_at - timedelta(seconds=1),
        started_to=started_at + timedelta(minutes=1),
        statuses=[],
        filters=[],
        limit=50,
        cursor=None,
        full_retention_days=30,
        compact_retention_days=365,
    )
    trigger = next(
        item
        for item in trigger_history["executions"]
        if item["execution_key"] == "trigger:trigger-scoped"
    )
    assert trigger["config_revision"] == "revision-1"
    assert (
        read_node_execution_authorization_path(path, execution_key="trigger:trigger-scoped")
        == "root/old"
    )


def test_retention_configuration_requires_compact_retention_when_enabled(tmp_path) -> None:
    with pytest.raises(ValueError, match="compact_history_retention_days is required"):
        SqliteWorkflowEventJournal(tmp_path / "orchestration.db", retention_days=30)


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
    assert first_batch["last_source_key"] == "effect:legacy-1"
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
    assert second_batch["last_source_key"] == "effect:legacy-2"
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
