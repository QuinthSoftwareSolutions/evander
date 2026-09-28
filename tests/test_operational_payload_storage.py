import json
import sqlite3
import hashlib
import zlib
from datetime import UTC, datetime, timedelta

import pytest

from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord, GateExecutionRecord, SqliteExecutionStore, SqliteGateExecutionStore,
)
from evander.events.models import TriggerEvent
from evander.orchestration.storage_migration import (
    check_orchestration_storage,
    migrate_orchestration_storage,
)
from evander.repositories.execution_history import (
    read_node_execution_detail,
    run_pending_history_indexing,
    search_node_executions,
)
from evander.repositories.operational_payloads import (
    FORMAT_KEY,
    collect_unreferenced_payloads,
)
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    connect_orchestration_db_read_only,
)
from evander.triggers.queue import SqliteTriggerEventQueue


def record(**changes):
    now = datetime.now(UTC)
    return EffectExecutionRecord.model_validate(
        {
            "execution_id": "effect-1",
            "idempotency_key": "key-1",
            "event_id": "event-1",
            "trace_id": "trace-1",
            "source_node_id": "source",
            "source_trigger_type": "trigger:test",
            "effect_id": "effect",
            "effect_type": "effect:test",
            "status": "succeeded",
            "started_at": now,
            "finished_at": now,
            "resolved_config_hash": "hash",
            "resolved_config": {},
            "input_payload": {},
            "outputs": {},
            **changes,
        }
    )


def drain(path):
    for _ in range(100):
        with connect_orchestration_db(path) as connection:
            run_pending_history_indexing(connection)
            if not connection.execute("SELECT 1 FROM workflow_history_index_jobs").fetchone():
                return
    raise AssertionError("History projection did not converge")


def search(path, *, filters=None):
    now = datetime.now(UTC)
    return search_node_executions(
        path,
        node_id="effect",
        started_from=now - timedelta(days=1),
        started_to=now + timedelta(days=1),
        statuses=[],
        filters=filters or [],
        limit=50,
        cursor=None,
        full_retention_days=7,
        compact_retention_days=30,
    )


def test_payload_deduplicates_ancestors_and_delivery_does_not_hydrate(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    shared = {"items": [{"key": str(i), "value": "content" * 50} for i in range(300)]}
    payload = {
        "source": shared,
        "__node_outputs": {"source": shared},
        "literal": ["r", "user-value"],
    }
    original = record(input_payload=payload, outputs={"copy": shared, "token": "private-value"})
    store.put(original)
    assert store.get_execution(original.execution_id) == original
    with connect_orchestration_db_read_only(path) as connection:
        raw = connection.execute("SELECT payload_json FROM effect_executions").fetchone()[0]
        assert len(raw) < 5000
        assert FORMAT_KEY in raw
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_operational_blobs").fetchone()[0] < 10
        )
    detail = read_node_execution_detail(path, execution_key="effect:effect-1")
    assert detail["output"]["token"] == "[REDACTED]"
    assert detail["input"]["source"] == shared
    assert "__node_outputs" not in detail["input"]
    queue = SqliteTriggerEventQueue(path)
    event = TriggerEvent(
        event_id="successor",
        trigger_id="effect",
        trigger_type="effect:test",
        occurred_at=datetime.now(UTC),
        payload=payload,
    )
    queue.publish(event)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Lifecycle update hydrated or encoded immutable data")

    monkeypatch.setattr("evander.triggers.queue.load_record", unexpected)
    monkeypatch.setattr("evander.triggers.queue.prepare_record", unexpected)
    assert queue.mark_inflight(event.event_id)
    assert queue.reset_inflight() == 1
    assert queue.mark_event_cleared(event.event_id, cleared_at=datetime.now(UTC),
                                    clear_reason="test-clear", error="cleared") == 1
    assert queue.mark_delivered(event.event_id)
    monkeypatch.setattr("evander.repositories.orchestration.load_record", unexpected)
    monkeypatch.setattr("evander.repositories.orchestration.prepare_record", unexpected)
    assert store.update_lifecycle(
        original.execution_id, status="failed", error="finalization failed"
    )


def test_history_freshness_and_superseded_partial_generation(tmp_path):
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    original = record(outputs={f"field{i}": i for i in range(250)})
    store.put(original)
    assert search(path)["returned"] == 1
    pending = search(
        path, filters=[{"location": "output", "path": "/field1", "operator": "eq", "value": 1}]
    )
    assert pending["returned"] == 0
    assert pending["indexing_pending_count"] == 1
    with connect_orchestration_db(path) as connection:
        run_pending_history_indexing(connection, batch_size=100)
    original.outputs = {"field1": 999}
    store.put(original)
    drain(path)
    assert search(path)["indexing_complete"]
    assert (
        search(
            path, filters=[{"location": "output", "path": "/field1", "operator": "eq", "value": 1}]
        )["returned"]
        == 0
    )
    assert (
        search(
            path,
            filters=[{"location": "output", "path": "/field1", "operator": "eq", "value": 999}],
        )["returned"]
        == 1
    )
    assert (
        search(path, filters=[{"location": "output", "path": "/field249", "operator": "exists"}])[
            "returned"
        ]
        == 0
    )


def _externalize_legacy_error(path, *, kind, record_id, error):
    # Persist the first codec version's externalized lifecycle field. Its
    # wire format is the compatibility contract being exercised here.
    table, key = ("trigger_events", "event_id") if kind == "trigger" else (
        f"{kind}_executions", "execution_id"
    )
    raw = json.dumps(["v", error], separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    with connect_orchestration_db(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO workflow_operational_blobs VALUES (?, 'zlib-json-v1', ?, ?)",
            (digest, zlib.compress(raw), len(raw)),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workflow_operational_record_refs VALUES (?, ?, ?)",
            (kind, record_id, digest),
        )
        connection.execute(
            f"UPDATE {table} SET payload_json=json_set(payload_json, '$.error', '', ?, json(?)) "
            f"WHERE {key}=?",
            (f"$.{FORMAT_KEY}.fields.error", json.dumps(["r", digest]), record_id),
        )


@pytest.mark.parametrize("kind", ["effect", "gate"])
@pytest.mark.parametrize("legacy_reference", [False, True])
def test_large_error_remains_visible_and_clearing_replaces_it(tmp_path, legacy_reference, kind):
    path = tmp_path / "orchestration.db"
    store = (SqliteExecutionStore if kind == "effect" else SqliteGateExecutionStore)(path)
    error = "Provider could not complete the request. " * 800
    original = record(status="waiting", error=error, outputs={"detail": error})
    if kind == "gate":
        fields = original.model_dump()
        fields["gate_id"] = fields.pop("effect_id")
        fields["gate_type"] = fields.pop("effect_type")
        original = GateExecutionRecord.model_validate(fields)
    store.put(original)
    if legacy_reference:
        _externalize_legacy_error(path, kind=kind, record_id="effect-1", error=error)
    assert store.list_active_summaries()[0]["error"] == error
    if legacy_reference:
        # Older lifecycle updates could already have left this stale reference.
        with connect_orchestration_db(path) as connection:
            connection.execute(
                f"UPDATE {kind}_executions SET payload_json=json_set(payload_json, '$.error', ?)",
                ("Previously updated error",),
            )
        assert store.get_execution("effect-1").error == "Previously updated error"
        assert store.list_active_summaries()[0]["error"] == "Previously updated error"
    assert store.clear_event_records(
        "event-1", cleared_at=datetime.now(UTC), clear_reason="Operator requested clear",
        clear_error="Execution cleared by operator",
    ) == 1
    saved = store.get_execution("effect-1")
    assert saved.error == "Execution cleared by operator"
    assert saved.clear_reason == "Operator requested clear"
    assert saved.status == "failed"
    with connect_orchestration_db(path) as connection:
        for _ in range(10):
            collect_unreferenced_payloads(connection)
    assert store.get_execution("effect-1").outputs == {"detail": error}
    assert store.list_activity_summaries_between(
        start_at=original.started_at - timedelta(seconds=1), end_at=datetime.now(UTC)
    )[0]["error"] == "Execution cleared by operator"
    store.update_lifecycle("effect-1", error="")
    assert store.get_execution("effect-1").error == ""


def test_trigger_clearing_removes_legacy_error_reference(tmp_path):
    path = tmp_path / "orchestration.db"
    queue = SqliteTriggerEventQueue(path)
    error = "Delivery could not complete. " * 1000
    queue.publish(TriggerEvent(
        event_id="event", trigger_id="trigger", trigger_type="trigger:test",
        occurred_at=datetime.now(UTC), payload={"detail": error},
    ))
    _externalize_legacy_error(path, kind="trigger", record_id="event", error=error)
    assert queue.read_all()["event"].error == error
    assert queue.mark_event_cleared(
        "event", cleared_at=datetime.now(UTC), clear_reason="Resolved by operator", error="",
    ) == 1
    cleared = queue.read_all()["event"]
    assert cleared.error == ""
    assert cleared.clear_reason == "Resolved by operator"
    assert cleared.payload == {"detail": error}


def test_offline_conversion_resume_verification_and_inline_rollback(tmp_path):
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    original = record(input_payload={"large": "synthetic-content" * 5000})
    store.put(original)
    # Simulate the old executable's inline storage without changing the model.
    with connect_orchestration_db(path) as connection:
        connection.execute(
            "UPDATE effect_executions SET payload_json=?", (original.model_dump_json(),)
        )
        connection.execute("DELETE FROM workflow_operational_record_refs")
    assert check_orchestration_storage(path, verify=True)["tables"]["effect"]["referenced_records"] == 0
    first = migrate_orchestration_storage(path, max_batches=1)
    assert not first["complete"]
    assert migrate_orchestration_storage(path)["complete"]
    assert check_orchestration_storage(path, verify=True)["verified"]
    assert not check_orchestration_storage(path)["downgrade_ready"]
    assert store.get_execution("effect-1") == original
    assert migrate_orchestration_storage(path, downgrade=True)["complete"]
    assert check_orchestration_storage(path, verify=True)["downgrade_ready"]
    with connect_orchestration_db_read_only(path) as connection:
        raw = connection.execute("SELECT payload_json FROM effect_executions").fetchone()[0]
    assert EffectExecutionRecord.model_validate(json.loads(raw)) == original


def test_corrupt_blob_fails_visibly_and_gc_preserves_shared_references(tmp_path):
    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    store.put(record(outputs={"large": "data" * 10000}))
    store.put(
        record(execution_id="effect-2", idempotency_key="key-2", outputs={"large": "data" * 10000})
    )
    with connect_orchestration_db(path) as connection:
        connection.execute("DELETE FROM effect_executions WHERE execution_id='effect-1'")
        assert collect_unreferenced_payloads(connection) == 0
    assert store.get_execution("effect-2").outputs == {"large": "data" * 10000}
    with connect_orchestration_db(path) as connection:
        connection.execute("UPDATE workflow_operational_blobs SET codec='unknown'")
    with pytest.raises(ValueError, match="unsupported"):
        store.get_execution("effect-2")


@pytest.mark.parametrize("operation", ["execution", "trigger", "completion"])
def test_payload_preparation_does_not_consume_writer_deadline(tmp_path, monkeypatch, operation):
    """Slow compression must neither hold a writer nor use its 100 ms SQL budget."""
    import time
    import evander.repositories.operational_payloads as codec

    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    queue = SqliteTriggerEventQueue(path)
    event = TriggerEvent(
        event_id="successor",
        trigger_id="effect",
        trigger_type="effect:test",
        occurred_at=datetime.now(UTC),
        payload={"large": "content" * 10000},
    )
    original = record(outputs={"large": "content" * 10000})
    compress = codec.zlib.compress
    checked = False

    def slow_compress(*args, **kwargs):
        nonlocal checked
        if not checked:
            checked = True
            with sqlite3.connect(path, timeout=0) as competing_writer:
                competing_writer.execute("BEGIN IMMEDIATE")
                time.sleep(0.12)
        return compress(*args, **kwargs)

    monkeypatch.setattr(codec.zlib, "compress", slow_compress)
    if operation == "execution":
        store.put(original)
    elif operation == "trigger":
        queue.publish(event)
    else:
        store.record_completion_and_publish(
            record=original, event_queue=queue, event=event, idempotency_key="completion"
        )
    assert checked
    if operation != "trigger":
        assert store.get_execution(original.execution_id) == original
    if operation != "execution":
        assert queue.list_pending()[0].payload == event.payload


def test_hydration_preserves_snapshot_when_payload_is_replaced_and_collected(tmp_path, monkeypatch):
    import evander.repositories.orchestration as repositories

    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    original = record(outputs={"large": "original" * 10000})
    store.put(original)
    replacement = record(outputs={"large": "replacement" * 10000})
    load = repositories.load_record
    replaced = False

    def replace_before_hydration(connection, payload):
        nonlocal replaced
        if not replaced:
            replaced = True
            store.put(replacement)
            with connect_orchestration_db(path) as writer:
                while collect_unreferenced_payloads(writer):
                    pass
        return load(connection, payload)

    monkeypatch.setattr(repositories, "load_record", replace_before_hydration)
    assert store.get_execution(original.execution_id) == original
    assert store.get_execution(original.execution_id) == replacement


def test_queue_journal_failure_rolls_back_publication_and_lifecycle(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    queue = SqliteTriggerEventQueue(path)
    event = TriggerEvent(
        event_id="event",
        trigger_id="trigger",
        trigger_type="trigger:test",
        occurred_at=datetime.now(UTC),
        payload={},
    )
    append = queue._append_journal

    def fail(*args, **kwargs):
        raise RuntimeError("journal unavailable")

    monkeypatch.setattr(queue, "_append_journal", fail)
    with pytest.raises(RuntimeError, match="journal unavailable"):
        queue.publish(event)
    assert not queue.contains(event.event_id)
    monkeypatch.setattr(queue, "_append_journal", append)
    queue.publish(event)
    monkeypatch.setattr(queue, "_append_journal", fail)
    with pytest.raises(RuntimeError, match="journal unavailable"):
        queue.mark_inflight(event.event_id)
    assert queue.list_pending()[0].event_id == event.event_id


def test_preexisting_completion_event_still_gets_one_execution_journal_entry(tmp_path):
    path = tmp_path / "orchestration.db"
    from evander.workflow.events import SqliteWorkflowEventJournal

    store = SqliteExecutionStore(path, journal=SqliteWorkflowEventJournal(path))
    queue = SqliteTriggerEventQueue(path)
    event = TriggerEvent(
        event_id="successor",
        trigger_id="effect",
        trigger_type="effect:test",
        occurred_at=datetime.now(UTC),
        payload={},
    )
    queue.publish(event, idempotency_key="completion")
    for _ in range(2):
        store.record_completion_and_publish(
            record=record(), event_queue=queue, event=event, idempotency_key="completion"
        )
    with connect_orchestration_db_read_only(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workflow_events WHERE event_type='workflow.execution.updated'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workflow_events WHERE event_type='workflow.trigger_event.published'"
            ).fetchone()[0]
            == 1
        )


def test_prompt_receipt_queries_use_projection_index_and_keep_legacy_fallback(
    tmp_path, monkeypatch
):
    from contextlib import contextmanager
    import evander.repositories.orchestration as repositories

    path = tmp_path / "orchestration.db"
    store = SqliteExecutionStore(path)
    now = datetime.now(UTC)
    modern = record(
        effect_type="effect:agent:enqueue-prompt",
        outputs={"dispatch_id": "dispatch", "session_id": "session", "large": "content" * 10000},
        finished_at=now,
    )
    legacy = record(
        execution_id="legacy",
        idempotency_key="legacy-key",
        effect_type="effect:agent:enqueue-prompt",
        outputs={"dispatch_id": "dispatch", "session_id": "session"},
        finished_at=now - timedelta(seconds=1),
    )
    store.put(modern)
    store.put(legacy)
    with connect_orchestration_db(path) as connection:
        connection.execute("DELETE FROM workflow_execution_projections WHERE record_id='legacy'")
    queries = []
    connect = repositories.connect_orchestration_db_read_only

    @contextmanager
    def traced(*args, **kwargs):
        with connect(*args, **kwargs) as connection:
            connection.set_trace_callback(queries.append)
            yield connection

    monkeypatch.setattr(repositories, "connect_orchestration_db_read_only", traced)
    assert [
        item.execution_id for item in store.list_prompt_records_for_dispatches({"dispatch"})
    ] == ["legacy", "effect-1"]
    assert (
        repositories.read_latest_agent_prompt_execution(
            path, session_id="session", dispatch_id="dispatch"
        )["execution_id"]
        == "effect-1"
    )
    with connect(path) as connection:
        for query in [sql for sql in queries if "UNION ALL" in sql]:
            plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query)]
            assert any(
                "SEARCH p USING" in line
                and "INDEX idx_execution_projection_dispatch (dispatch_id=" in line
                for line in plan
            ), plan
