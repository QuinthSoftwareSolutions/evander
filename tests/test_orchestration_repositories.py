from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import evander.repositories.orchestration as orchestration_repository

from completion_recovery_support import (
    assert_completion_query_plans,
    bounded_completion_queries,
    completion_record,
    seed_completed_executions,
)
from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.events.models import TriggerEvent
from evander.repositories.orchestration import (
    JsonEffectCursorRepository,
    JsonGateCursorRepository,
    SqliteWorkflowConfigSnapshotRepository,
    read_latest_agent_prompt_execution,
)
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    maybe_checkpoint_orchestration_wal,
)
from evander.triggers.queue import SqliteTriggerEventQueue


def _effect_record(
    *, execution_id: str, idempotency_key: str, event_id: str = "evt-1"
) -> EffectExecutionRecord:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    return EffectExecutionRecord(
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        event_id=event_id,
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:test",
        effect_id="effect-1",
        effect_type="effect:test",
        status="running",
        started_at=now,
        finished_at=now,
        resolved_config_hash="hash-1",
        resolved_config={"message": "hello"},
        input_payload={"x": 1},
        outputs={},
    )


@pytest.mark.parametrize("session_location", ["outputs", "scope", "resolved_config"])
def test_latest_agent_prompt_receipt_is_scoped_and_ignores_queue_markers(tmp_path, session_location):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    for index, (execution_id, session_id, dispatch_id, queued, node_type) in enumerate([
        ("old", "session", "dispatch", False, "effect:agent:enqueue-prompt"),
        ("wanted", "session", "dispatch", False, "effect:agent:enqueue-prompt"),
        ("queue-marker", "session", "dispatch", True, "effect:agent:enqueue-prompt"),
        ("other-session", "other", "dispatch", False, "effect:agent:enqueue-prompt"),
        ("other-dispatch", "session", "other", False, "effect:agent:enqueue-prompt"),
        ("other-effect", "session", "dispatch", False, "effect:other"),
    ]):
        record = _effect_record(execution_id=execution_id, idempotency_key=execution_id)
        record.effect_type = node_type
        record.status = "waiting" if execution_id == "wanted" else "succeeded"
        record.finished_at = datetime(2026, 4, 23, 12, index, tzinfo=UTC)
        record.outputs = {"dispatch_id": dispatch_id, "queued": queued, "thread_id": execution_id}
        if session_location == "outputs":
            record.outputs["session_id"] = session_id
        elif session_location == "scope":
            record.outputs["scope"] = {"session_id": session_id}
        else:
            record.resolved_config["session_id"] = session_id
        store.put(record)
    result = read_latest_agent_prompt_execution(store.path, session_id="session", dispatch_id="dispatch")
    assert result["execution_id"] == "wanted"
    assert read_latest_agent_prompt_execution(store.path, session_id="absent", dispatch_id="dispatch") is None


def test_latest_agent_prompt_receipt_does_not_create_missing_database(tmp_path):
    path = tmp_path / "absent.db"
    assert read_latest_agent_prompt_execution(path, session_id="session", dispatch_id="dispatch") is None
    assert not path.exists()


def test_prompt_receipts_read_committed_state_during_write_transaction(tmp_path):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    record = _effect_record(execution_id="prompt", idempotency_key="prompt")
    record.effect_type = "effect:agent:enqueue-prompt"
    record.status = "waiting"
    record.outputs = {"dispatch_id": "dispatch"}
    store.put(record)
    with connect_orchestration_db(store.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("DELETE FROM effect_executions")
        assert store.list_prompt_records_for_dispatches({"dispatch"}) == [record]
    assert store.list_prompt_records_for_dispatches({"dispatch"}) == []


def test_orchestration_read_only_connection_rejects_writes_and_closes(tmp_path):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    with connect_orchestration_db_read_only(store.path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM effect_executions")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_orchestration_read_only_connection_does_not_create_database(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        with connect_orchestration_db_read_only(path):
            pytest.fail("opened a missing database")
    assert not path.exists()


def _gate_record(
    *,
    execution_id: str,
    idempotency_key: str,
    gate_id: str,
    finished_at: datetime,
) -> GateExecutionRecord:
    return GateExecutionRecord(
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        event_id=f"evt-{execution_id}",
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:test",
        gate_id=gate_id,
        gate_type="gate:merge",
        status="succeeded",
        started_at=finished_at,
        finished_at=finished_at,
        resolved_config_hash="hash-1",
        resolved_config={},
        input_payload={},
        outputs={"ok": True},
        inputs_by_source={},
        input_event_ids_by_source={},
        input_occurred_at_by_source={},
    )


def _effect_execution_record(
    *,
    execution_id: str,
    idempotency_key: str,
    effect_id: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    event_id: str,
    dismissed_at: datetime | None = None,
    dismiss_reason: str | None = None,
) -> EffectExecutionRecord:
    record = _effect_record(
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        event_id=event_id,
    )
    record.effect_id = effect_id
    record.status = status
    record.started_at = started_at
    record.finished_at = finished_at
    if dismissed_at is not None:
        record.dismissed_at = dismissed_at
    if dismiss_reason is not None:
        record.dismiss_reason = dismiss_reason
    return record


def _gate_execution_record(
    *,
    execution_id: str,
    idempotency_key: str,
    gate_id: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    dismissed_at: datetime | None = None,
    dismiss_reason: str | None = None,
) -> GateExecutionRecord:
    record = _gate_record(
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        gate_id=gate_id,
        finished_at=finished_at,
    )
    record.status = status
    record.started_at = started_at
    record.finished_at = finished_at
    if dismissed_at is not None:
        record.dismissed_at = dismissed_at
    if dismiss_reason is not None:
        record.dismiss_reason = dismiss_reason
    return record


def _sqlite_payload_json_by_execution_id(path: Path, table_name: str) -> dict[str, str]:
    with connect_orchestration_db(path) as connection:
        rows = connection.execute(
            f"SELECT execution_id, payload_json FROM {table_name} ORDER BY execution_id"
        ).fetchall()
    return {row["execution_id"]: row["payload_json"] for row in rows}


def _workflow_config_snapshot_payload(*, revision: str = "revision-1") -> dict[str, object]:
    return {
        "revision": revision,
        "captured_at": "2026-04-23T12:15:00Z",
        "snapshot_type": "compiled_runtime_graph",
        "source": "effect_orchestrator.run_once",
        "node_count": 4,
        "edge_count": 3,
        "triggers": [{"id": "trigger-1", "type": "trigger:test"}],
        "gates": [{"id": "gate-1", "type": "gate:test"}],
        "effects": [{"id": "effect-1", "type": "effect:test"}],
        "queries": [{"id": "query-1", "type": "query:test"}],
        "edges": [{"source_id": "trigger-1", "target_id": "effect-1"}],
    }


def test_orchestration_connection_is_closed_after_context_exit(tmp_path: Path) -> None:
    with connect_orchestration_db(tmp_path / "orchestration.db") as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_gate_cursor_repository_records_per_trigger_poll_metadata(tmp_path: Path) -> None:
    repository = JsonGateCursorRepository(tmp_path / "trigger-cursors.json")

    repository.record_trigger_poll(
        trigger_id="trigger-1",
        trigger_type="trigger:test",
        discovered=2,
        event_ids=["evt-1", "evt-2", "evt-3"],
        polled_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
    )

    assert repository.get_trigger_state("trigger-1") == {
        "trigger_type": "trigger:test",
        "last_polled_at": "2026-04-23T12:05:00Z",
        "last_discovered_count": 2,
        "last_event_ids": ["evt-1", "evt-2", "evt-3"],
        "last_event_id": "evt-3",
    }


def test_effect_cursor_repository_records_last_processed_event(tmp_path: Path) -> None:
    repository = JsonEffectCursorRepository(tmp_path / "effect-cursors.json")

    repository.record_processed_event(
        event_id="evt-9",
        source_node_id="gate-2",
        processed_at=datetime(2026, 4, 23, 12, 6, tzinfo=UTC),
    )

    assert repository.get_processing_checkpoint() == {
        "last_processed_event_id": "evt-9",
        "last_processed_source_node_id": "gate-2",
        "last_processed_at": "2026-04-23T12:06:00Z",
    }


def test_published_cursor_checkpoints_copy_only_the_retained_event_tail(tmp_path: Path) -> None:
    class CountedEventIds(list[str]):
        copied_elements = 0

        def __iter__(self):
            for value in super().__iter__():
                self.copied_elements += 1
                yield value

        def __getitem__(self, index):
            value = super().__getitem__(index)
            if isinstance(index, slice):
                self.copied_elements += len(value)
            return value

    path = tmp_path / "trigger-cursors.json"
    repository = JsonGateCursorRepository(path)
    event_ids = CountedEventIds()
    for number in range(1_000):
        event_ids.append(f"event-{number}")
        repository.record_trigger_poll(
            trigger_id="catch-up", trigger_type="trigger:teams:new-message",
            discovered=number + 1, event_ids=event_ids,
        )

    assert event_ids.copied_elements <= 20_000
    state = JsonGateCursorRepository(path).get_trigger_state("catch-up")
    assert state["last_discovered_count"] == 1_000
    assert state["last_event_ids"] == [f"event-{number}" for number in range(980, 1_000)]
    assert state["last_event_id"] == "event-999"


def test_execution_repository_supports_semantic_idempotency_and_gate_queries(
    tmp_path: Path,
) -> None:
    execution_store = SqliteExecutionStore(tmp_path / "effect-executions.json")
    gate_store = SqliteGateExecutionStore(tmp_path / "gate-executions.json")

    running = _effect_record(execution_id="exec-1", idempotency_key="idem-1")
    execution_store.record_execution(running)

    older_gate = _gate_record(
        execution_id="gate-1",
        idempotency_key="gate-idem-1",
        gate_id="merge-1",
        finished_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
    )
    newer_gate = _gate_record(
        execution_id="gate-2",
        idempotency_key="gate-idem-2",
        gate_id="merge-1",
        finished_at=datetime(2026, 4, 23, 12, 10, tzinfo=UTC),
    )
    gate_store.record_execution(older_gate)
    gate_store.record_execution(newer_gate)

    assert execution_store.find_execution_by_idempotency_key("idem-1") == running
    assert execution_store.get_execution("exec-1") == running
    assert gate_store.find_latest_execution_for_gate("merge-1") == newer_gate


def test_latest_gate_lookup_materializes_only_the_selected_payload(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    store = SqliteGateExecutionStore(path)
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    for execution_id, finished_minutes, started_minutes, gate_id in [
        ("older", 3, 3, "merge-1"),
        ("later-finish", 10, 2, "merge-1"),
        ("latest-a", 10, 4, "merge-1"),
        ("latest-z", 10, 4, "merge-1"),
        ("unrelated", 30, 30, "other"),
    ]:
        record = _gate_record(
            execution_id=execution_id,
            idempotency_key=execution_id,
            gate_id=gate_id,
            finished_at=now + timedelta(minutes=finished_minutes),
        )
        record.started_at = now + timedelta(minutes=started_minutes)
        store.put(record)

    # Observe payload reads without wall-clock timing or a particular query string.
    with connect_orchestration_db(path) as connection:
        connection.execute("ALTER TABLE gate_executions RENAME TO observed_gate_records")
        columns = [
            row["name"] for row in connection.execute("PRAGMA table_info(observed_gate_records)")
        ]
        projection = ", ".join(
            "observe_gate_payload(payload_json) AS payload_json" if name == "payload_json" else name
            for name in columns
        )
        connection.execute(
            f"CREATE VIEW gate_executions AS SELECT {projection} FROM observed_gate_records"
        )

    payload_reads = 0

    def observe_payload(payload):
        nonlocal payload_reads
        payload_reads += 1
        return payload

    @contextmanager
    def observed_connection(database_path):
        with connect_orchestration_db_read_only(database_path) as connection:
            connection.create_function("observe_gate_payload", 1, observe_payload)
            yield connection

    monkeypatch.setattr(
        orchestration_repository, "connect_orchestration_db_read_only", observed_connection
    )
    result = store.find_latest_execution_for_gate("merge-1")
    assert result is not None
    assert result.execution_id == "latest-z"
    assert payload_reads == 1
    payload_reads = 0
    assert store.find_latest_execution_for_gate("absent") is None
    assert payload_reads == 0


def test_gate_completion_and_successor_event_commit_atomically(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "orchestration.db"
    gate_store = SqliteGateExecutionStore(path)
    queue = SqliteTriggerEventQueue(path)
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    record = _gate_record(
        execution_id="gate-atomic",
        idempotency_key="gate-atomic-key",
        gate_id="map-1",
        finished_at=now,
    )
    event = TriggerEvent(
        event_id="event-after-gate",
        trigger_id="map-1",
        trigger_type="gate:map",
        trace_id="trace-1",
        occurred_at=now,
        payload={"ok": True},
    )
    record.published_event_id = event.event_id

    assert (
        gate_store.record_completion_and_publish(
            record=record,
            event_queue=queue,
            event=event,
            idempotency_key=record.idempotency_key,
        )
        == event.event_id
    )
    assert gate_store.get_execution(record.execution_id) == record
    assert queue.read_all()[event.event_id].delivery_state == "pending"
    # Replaying the same deterministic transaction is idempotent.
    gate_store.record_completion_and_publish(
        record=record,
        event_queue=queue,
        event=event,
        idempotency_key=record.idempotency_key,
    )
    assert list(queue.read_all()) == [event.event_id]

    failed_record = _gate_record(
        execution_id="gate-rollback",
        idempotency_key="gate-rollback-key",
        gate_id="map-2",
        finished_at=now,
    )
    failed_event = event.model_copy(update={"event_id": "event-rolled-back", "trigger_id": "map-2"})

    def fail_record(_connection, _record, **_kwargs):
        raise RuntimeError("synthetic gate persistence failure")

    monkeypatch.setattr(gate_store, "_upsert_record", fail_record)
    with pytest.raises(RuntimeError, match="synthetic gate persistence failure"):
        gate_store.record_completion_and_publish(
            record=failed_record,
            event_queue=queue,
            event=failed_event,
            idempotency_key=failed_record.idempotency_key,
        )
    assert "event-rolled-back" not in queue.read_all()


def test_sqlite_execution_repository_supports_indexed_queries(tmp_path: Path) -> None:
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    gate_store = SqliteGateExecutionStore(tmp_path / "orchestration.db")

    running = _effect_record(execution_id="exec-1", idempotency_key="idem-1")
    execution_store.record_execution(running)
    succeeded = _effect_record(execution_id="exec-2", idempotency_key="idem-2")
    succeeded.status = "succeeded"
    execution_store.record_execution(succeeded)

    older_gate = _gate_record(
        execution_id="gate-1",
        idempotency_key="gate-idem-1",
        gate_id="merge-1",
        finished_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
    )
    newer_gate = _gate_record(
        execution_id="gate-2",
        idempotency_key="gate-idem-2",
        gate_id="merge-1",
        finished_at=datetime(2026, 4, 23, 12, 10, tzinfo=UTC),
    )
    gate_store.record_execution(older_gate)
    gate_store.record_execution(newer_gate)

    assert execution_store.find_execution_by_idempotency_key("idem-1") == running
    assert execution_store.get_execution("exec-1") == running
    assert execution_store.has_running_execution_for_event("evt-1") is True
    assert [record.execution_id for record in execution_store.list_active_records()] == ["exec-1"]
    assert gate_store.find_latest_execution_for_gate("merge-1") == newer_gate

    with connect_orchestration_db(tmp_path / "orchestration.db") as connection:
        index_names = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                  AND name IN (
                      'idx_trigger_events_activity',
                      'idx_effect_executions_activity',
                      'idx_gate_executions_activity',
                      'idx_effect_executions_node_failed_dismissed',
                      'idx_gate_executions_node_failed_dismissed'
                  )
                """
            ).fetchall()
        }

    assert index_names == {
        "idx_trigger_events_activity",
        "idx_effect_executions_activity",
        "idx_gate_executions_activity",
        "idx_effect_executions_node_failed_dismissed",
        "idx_gate_executions_node_failed_dismissed",
    }


def test_sqlite_execution_repository_dismisses_failed_records_for_node(tmp_path: Path) -> None:
    db_path = tmp_path / "orchestration.db"
    execution_store = SqliteExecutionStore(db_path)
    dismissed_at = datetime(2026, 4, 23, 13, 0, tzinfo=UTC)

    target_failed_1 = _effect_execution_record(
        execution_id="exec-failed-1",
        idempotency_key="idem-failed-1",
        event_id="evt-target-1",
        effect_id="effect-prompt",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
    )
    target_failed_2 = _effect_execution_record(
        execution_id="exec-failed-2",
        idempotency_key="idem-failed-2",
        event_id="evt-target-2",
        effect_id="effect-prompt",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 3, tzinfo=UTC),
    )
    other_failed = _effect_execution_record(
        execution_id="exec-other-failed",
        idempotency_key="idem-other-failed",
        event_id="evt-other",
        effect_id="effect-other",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 4, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
    )
    target_running = _effect_execution_record(
        execution_id="exec-running",
        idempotency_key="idem-running",
        event_id="evt-running",
        effect_id="effect-prompt",
        status="running",
        started_at=datetime(2026, 4, 23, 12, 6, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 7, tzinfo=UTC),
    )
    target_succeeded = _effect_execution_record(
        execution_id="exec-succeeded",
        idempotency_key="idem-succeeded",
        event_id="evt-succeeded",
        effect_id="effect-prompt",
        status="succeeded",
        started_at=datetime(2026, 4, 23, 12, 8, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 9, tzinfo=UTC),
    )
    target_dismissed = _effect_execution_record(
        execution_id="exec-dismissed",
        idempotency_key="idem-dismissed",
        event_id="evt-dismissed",
        effect_id="effect-prompt",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 10, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 11, tzinfo=UTC),
        dismissed_at=datetime(2026, 4, 23, 12, 12, tzinfo=UTC),
        dismiss_reason="manual_dismiss_errored_workflow_run",
    )
    records = [
        target_failed_1,
        target_failed_2,
        other_failed,
        target_running,
        target_succeeded,
        target_dismissed,
    ]
    for record in records:
        execution_store.record_execution(record)

    before_payloads = _sqlite_payload_json_by_execution_id(db_path, "effect_executions")
    unchanged_before = {
        execution_id: before_payloads[execution_id]
        for execution_id in (
            "exec-other-failed",
            "exec-running",
            "exec-succeeded",
            "exec-dismissed",
        )
    }

    def fail_read_all() -> None:
        raise AssertionError("dismiss_failed_records_for_node should not read_all()")

    def fail_update_all(*_: object, **__: object) -> None:
        raise AssertionError("dismiss_failed_records_for_node should not update_all()")

    execution_store.read_all = fail_read_all  # type: ignore[method-assign]
    execution_store.update_all = fail_update_all  # type: ignore[method-assign]

    updated = execution_store.dismiss_failed_records_for_node(
        " effect-prompt ",
        dismissed_at=dismissed_at,
        dismiss_reason="manual_dismiss_node_failures",
    )

    after_payloads = _sqlite_payload_json_by_execution_id(db_path, "effect_executions")

    assert updated == 2
    assert execution_store.get_execution("exec-failed-1").dismissed_at == dismissed_at
    assert execution_store.get_execution("exec-failed-1").dismiss_reason == (
        "manual_dismiss_node_failures"
    )
    assert execution_store.get_execution("exec-failed-2").dismissed_at == dismissed_at
    assert execution_store.get_execution("exec-failed-2").dismiss_reason == (
        "manual_dismiss_node_failures"
    )
    assert execution_store.get_execution("exec-running").dismissed_at is None
    assert execution_store.get_execution("exec-succeeded").dismissed_at is None
    assert execution_store.get_execution("exec-dismissed").dismiss_reason == (
        "manual_dismiss_errored_workflow_run"
    )
    assert execution_store.get_execution("exec-other-failed").dismissed_at is None

    assert after_payloads["exec-failed-1"] != before_payloads["exec-failed-1"]
    assert after_payloads["exec-failed-2"] != before_payloads["exec-failed-2"]
    assert after_payloads["exec-failed-1"].count("manual_dismiss_node_failures") == 1
    assert after_payloads["exec-failed-2"].count("manual_dismiss_node_failures") == 1
    assert json.loads(after_payloads["exec-failed-1"])["dismiss_reason"] == (
        "manual_dismiss_node_failures"
    )
    assert json.loads(after_payloads["exec-failed-1"])["dismissed_at"] == "2026-04-23T13:00:00Z"
    assert {
        execution_id: after_payloads[execution_id] for execution_id in unchanged_before
    } == unchanged_before


def test_sqlite_gate_execution_repository_dismisses_failed_records_for_node(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "orchestration.db"
    gate_store = SqliteGateExecutionStore(db_path)
    dismissed_at = datetime(2026, 4, 23, 13, 0, tzinfo=UTC)

    target_failed_1 = _gate_execution_record(
        execution_id="gate-failed-1",
        idempotency_key="gate-idem-failed-1",
        gate_id="gate-review",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
    )
    target_failed_2 = _gate_execution_record(
        execution_id="gate-failed-2",
        idempotency_key="gate-idem-failed-2",
        gate_id="gate-review",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 3, tzinfo=UTC),
    )
    other_failed = _gate_execution_record(
        execution_id="gate-other-failed",
        idempotency_key="gate-idem-other-failed",
        gate_id="gate-other",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 4, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
    )
    target_running = _gate_execution_record(
        execution_id="gate-running",
        idempotency_key="gate-idem-running",
        gate_id="gate-review",
        status="running",
        started_at=datetime(2026, 4, 23, 12, 6, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 7, tzinfo=UTC),
    )
    target_succeeded = _gate_execution_record(
        execution_id="gate-succeeded",
        idempotency_key="gate-idem-succeeded",
        gate_id="gate-review",
        status="succeeded",
        started_at=datetime(2026, 4, 23, 12, 8, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 9, tzinfo=UTC),
    )
    target_dismissed = _gate_execution_record(
        execution_id="gate-dismissed",
        idempotency_key="gate-idem-dismissed",
        gate_id="gate-review",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 10, tzinfo=UTC),
        finished_at=datetime(2026, 4, 23, 12, 11, tzinfo=UTC),
        dismissed_at=datetime(2026, 4, 23, 12, 12, tzinfo=UTC),
        dismiss_reason="manual_dismiss_node_failures",
    )
    records = [
        target_failed_1,
        target_failed_2,
        other_failed,
        target_running,
        target_succeeded,
        target_dismissed,
    ]
    for record in records:
        gate_store.record_execution(record)

    before_payloads = _sqlite_payload_json_by_execution_id(db_path, "gate_executions")
    unchanged_before = {
        execution_id: before_payloads[execution_id]
        for execution_id in (
            "gate-other-failed",
            "gate-running",
            "gate-succeeded",
            "gate-dismissed",
        )
    }

    def fail_read_all() -> None:
        raise AssertionError("dismiss_failed_records_for_node should not read_all()")

    def fail_update_all(*_: object, **__: object) -> None:
        raise AssertionError("dismiss_failed_records_for_node should not update_all()")

    gate_store.read_all = fail_read_all  # type: ignore[method-assign]
    gate_store.update_all = fail_update_all  # type: ignore[method-assign]

    updated = gate_store.dismiss_failed_records_for_node(
        " gate-review ",
        dismissed_at=dismissed_at,
        dismiss_reason="manual_dismiss_node_failures",
    )

    after_payloads = _sqlite_payload_json_by_execution_id(db_path, "gate_executions")

    assert updated == 2
    assert gate_store.get_execution("gate-failed-1").dismissed_at == dismissed_at
    assert (
        gate_store.get_execution("gate-failed-1").dismiss_reason == "manual_dismiss_node_failures"
    )
    assert gate_store.get_execution("gate-failed-2").dismissed_at == dismissed_at
    assert (
        gate_store.get_execution("gate-failed-2").dismiss_reason == "manual_dismiss_node_failures"
    )
    assert gate_store.get_execution("gate-running").dismissed_at is None
    assert gate_store.get_execution("gate-succeeded").dismissed_at is None
    assert (
        gate_store.get_execution("gate-dismissed").dismiss_reason == "manual_dismiss_node_failures"
    )
    assert gate_store.get_execution("gate-other-failed").dismissed_at is None

    assert after_payloads["gate-failed-1"] != before_payloads["gate-failed-1"]
    assert after_payloads["gate-failed-2"] != before_payloads["gate-failed-2"]
    assert after_payloads["gate-failed-1"].count("manual_dismiss_node_failures") == 1
    assert after_payloads["gate-failed-2"].count("manual_dismiss_node_failures") == 1
    assert json.loads(after_payloads["gate-failed-1"])["dismiss_reason"] == (
        "manual_dismiss_node_failures"
    )
    assert json.loads(after_payloads["gate-failed-1"])["dismissed_at"] == "2026-04-23T13:00:00Z"
    assert {
        execution_id: after_payloads[execution_id] for execution_id in unchanged_before
    } == unchanged_before


@pytest.mark.parametrize("kind", ["effect", "gate"])
def test_completion_pending_pages_keep_timestamp_ties_and_skip_completed_records(tmp_path, kind):
    store = (SqliteExecutionStore if kind == "effect" else SqliteGateExecutionStore)(
        tmp_path / "orchestration.db"
    )
    at = datetime(2026, 1, 1, tzinfo=UTC)
    for key, offset, status in (
        ("later", 2, "completion_pending"),
        ("tie-b", 0, "completion_pending"),
        ("finished", 0, "succeeded"),
        ("tie-a", 0, "completion_pending"),
        ("middle", 1, "completion_pending"),
        ("waiting", 0, "waiting"),
    ):
        store.put(completion_record(kind, key, status=status, at=at + timedelta(seconds=offset)))
    assert [r.execution_id for r in store.list_completion_pending_records(limit=0)] == ["tie-a"]
    middle = store.get_execution("middle")
    middle.status = "succeeded"
    store.put(middle)
    after = ("2026-01-01T00:00:00Z", "tie-a")
    assert [r.execution_id for r in store.list_completion_pending_records(limit=1, after=after)] == [
        "tie-b"
    ]
    after = ("2026-01-01T00:00:00Z", "tie-b")
    assert [r.execution_id for r in store.list_completion_pending_records(after=after)] == ["later"]
    assert store.list_completion_pending_records(after=("2026-01-01T00:00:02Z", "later")) == []


@pytest.mark.parametrize("kind", ["effect", "gate"])
def test_completion_recovery_reads_only_an_indexed_page_after_analyze(tmp_path, monkeypatch, kind):
    store = (SqliteExecutionStore if kind == "effect" else SqliteGateExecutionStore)(
        tmp_path / "orchestration.db"
    )
    seed_completed_executions(store.path, kind=kind)
    with connect_orchestration_db(store.path) as connection:
        connection.execute("ANALYZE")
    with bounded_completion_queries(monkeypatch) as statements:
        assert store.list_completion_pending_records() == []
        assert store.list_completion_pending_records(after=("2026-01-01T00:00:00Z", "z")) == []
    with connect_orchestration_db_read_only(store.path) as connection:
        assert_completion_query_plans(connection, statements, kind=kind)

    # A dense pending backlog must also seek directly to the continuation page.
    with connect_orchestration_db(store.path) as connection:
        connection.execute(
            f"UPDATE {kind}_executions SET status='completion_pending', "
            "payload_json=json_set(payload_json,'$.status','completion_pending')"
        )
        connection.execute("ANALYZE")
    with bounded_completion_queries(monkeypatch) as statements:
        page = store.list_completion_pending_records(limit=1000)
        assert [r.execution_id for r in page] == [f"historical-{i:05d}" for i in range(100)]
        page = store.list_completion_pending_records(
            after=("2026-01-01T00:00:00Z", "historical-05899"), limit=2
        )
        assert [r.execution_id for r in page] == ["historical-05900", "historical-05901"]
    with connect_orchestration_db_read_only(store.path) as connection:
        assert_completion_query_plans(connection, statements, kind=kind)


def test_execution_repositories_list_waiting_and_completion_pending_records(
    tmp_path: Path,
) -> None:
    stores = [SqliteExecutionStore(tmp_path / "orchestration.db")]
    for index, execution_store in enumerate(stores):
        waiting_target = _effect_record(
            execution_id=f"exec-waiting-target-{index}",
            idempotency_key=f"idem-waiting-target-{index}",
        )
        waiting_target.status = "waiting"
        waiting_target.effect_type = "effect:agent:enqueue-prompt"
        waiting_other = _effect_record(
            execution_id=f"exec-waiting-other-{index}",
            idempotency_key=f"idem-waiting-other-{index}",
        )
        waiting_other.status = "waiting"
        waiting_other.effect_type = "effect:other"
        pending = _effect_record(
            execution_id=f"exec-pending-{index}",
            idempotency_key=f"idem-pending-{index}",
        )
        pending.status = "completion_pending"
        succeeded = _effect_record(
            execution_id=f"exec-succeeded-{index}",
            idempotency_key=f"idem-succeeded-{index}",
        )
        succeeded.status = "succeeded"

        for record in (waiting_target, waiting_other, pending, succeeded):
            execution_store.record_execution(record)

        assert [
            record.execution_id
            for record in execution_store.list_waiting_records("effect:agent:enqueue-prompt")
        ] == [waiting_target.execution_id]
        assert {record.execution_id for record in execution_store.list_waiting_records()} == {
            waiting_target.execution_id,
            waiting_other.execution_id,
        }
        assert [
            record.execution_id for record in execution_store.list_completion_pending_records()
        ] == [pending.execution_id]


def test_execution_repositories_list_records_by_node_type_and_statuses(
    tmp_path: Path,
) -> None:
    stores = [SqliteExecutionStore(tmp_path / "orchestration.db")]
    for index, execution_store in enumerate(stores):
        succeeded_target = _effect_record(
            execution_id=f"exec-succeeded-target-{index}",
            idempotency_key=f"idem-succeeded-target-{index}",
        )
        succeeded_target.status = "succeeded"
        succeeded_target.effect_type = "effect:agent:enqueue-prompt"
        failed_target = _effect_record(
            execution_id=f"exec-failed-target-{index}",
            idempotency_key=f"idem-failed-target-{index}",
        )
        failed_target.status = "failed"
        failed_target.effect_type = "effect:agent:enqueue-prompt"
        skipped_target = _effect_record(
            execution_id=f"exec-skipped-target-{index}",
            idempotency_key=f"idem-skipped-target-{index}",
        )
        skipped_target.status = "skipped"
        skipped_target.effect_type = "effect:agent:enqueue-prompt"
        succeeded_other = _effect_record(
            execution_id=f"exec-succeeded-other-{index}",
            idempotency_key=f"idem-succeeded-other-{index}",
        )
        succeeded_other.status = "succeeded"
        succeeded_other.effect_type = "effect:other"

        for record in (succeeded_target, failed_target, skipped_target, succeeded_other):
            execution_store.record_execution(record)

        assert {
            record.execution_id
            for record in execution_store.list_records_by_node_type_and_statuses(
                node_type="effect:agent:enqueue-prompt",
                statuses={"succeeded", "failed"},
            )
        } == {succeeded_target.execution_id, failed_target.execution_id}


def test_sqlite_execution_repository_filtered_query_does_not_read_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    record = _effect_record(execution_id="exec-target", idempotency_key="idem-target")
    record.status = "succeeded"
    record.effect_type = "effect:agent:enqueue-prompt"
    execution_store.record_execution(record)

    def fail_read_all(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("filtered execution query must not deserialize all records")

    monkeypatch.setattr(execution_store, "read_all", fail_read_all)

    assert execution_store.list_records_by_node_type_and_statuses(
        node_type="effect:agent:enqueue-prompt",
        statuses={"succeeded", "failed"},
    ) == [record]


def test_gate_execution_repositories_list_waiting_and_completion_pending_records(
    tmp_path: Path,
) -> None:
    stores = [SqliteGateExecutionStore(tmp_path / "orchestration.db")]
    for index, gate_store in enumerate(stores):
        waiting_target = _gate_record(
            execution_id=f"gate-waiting-target-{index}",
            idempotency_key=f"gate-idem-waiting-target-{index}",
            gate_id="gate-target",
            finished_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
        )
        waiting_target.status = "waiting"
        waiting_target.gate_type = "gate:await-agent"
        waiting_other = _gate_record(
            execution_id=f"gate-waiting-other-{index}",
            idempotency_key=f"gate-idem-waiting-other-{index}",
            gate_id="gate-other",
            finished_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
        )
        waiting_other.status = "waiting"
        waiting_other.gate_type = "gate:other"
        pending = _gate_record(
            execution_id=f"gate-pending-{index}",
            idempotency_key=f"gate-idem-pending-{index}",
            gate_id="gate-pending",
            finished_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
        )
        pending.status = "completion_pending"

        for record in (waiting_target, waiting_other, pending):
            gate_store.record_execution(record)

        assert [
            record.execution_id for record in gate_store.list_waiting_records("gate:await-agent")
        ] == [waiting_target.execution_id]
        assert {record.execution_id for record in gate_store.list_waiting_records()} == {
            waiting_target.execution_id,
            waiting_other.execution_id,
        }
        assert [record.execution_id for record in gate_store.list_completion_pending_records()] == [
            pending.execution_id
        ]


def test_sqlite_execution_repository_treats_completion_pending_as_active(
    tmp_path: Path,
) -> None:
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")

    running = _effect_record(execution_id="exec-1", idempotency_key="idem-1")
    pending = _effect_record(execution_id="exec-2", idempotency_key="idem-2")
    pending.status = "completion_pending"
    pending.started_at = datetime(2026, 4, 23, 12, 5, tzinfo=UTC)
    pending.finished_at = pending.started_at

    execution_store.record_execution(running)
    execution_store.record_execution(pending)

    assert execution_store.has_running_execution_for_event("evt-1") is True
    assert [record.execution_id for record in execution_store.list_active_records()] == [
        "exec-2",
        "exec-1",
    ]


def test_sqlite_gate_execution_repository_treats_completion_pending_as_active(
    tmp_path: Path,
) -> None:
    gate_store = SqliteGateExecutionStore(tmp_path / "orchestration.db")
    pending = _gate_record(
        execution_id="gate-1",
        idempotency_key="gate-idem-1",
        gate_id="merge-1",
        finished_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
    )
    pending.event_id = "evt-1"
    pending.status = "completion_pending"
    gate_store.record_execution(pending)

    assert gate_store.has_running_execution_for_event("evt-1") is True
    assert [record.execution_id for record in gate_store.list_active_records()] == ["gate-1"]

    cleared = gate_store.clear_event_records(
        "evt-1",
        cleared_at=datetime(2026, 4, 23, 13, 0, tzinfo=UTC),
        clear_reason="manual_clear_stale_workflow_runs",
        clear_error="cleared",
    )

    assert cleared == 1
    assert gate_store.get_execution("gate-1").status == "failed"
    assert gate_store.get_execution("gate-1").dismissed_at is not None


def test_orchestration_wal_checkpoint_records_maintenance_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "orchestration.db"
    execution_store = SqliteExecutionStore(db_path)
    execution_store.record_execution(
        _effect_record(execution_id="exec-1", idempotency_key="idem-1")
    )

    result = maybe_checkpoint_orchestration_wal(
        db_path,
        mode="truncate",
        min_bytes=0,
        interval_seconds=0,
        busy_timeout_ms=100,
    )

    assert result.attempted is True
    assert result.mode == "passive"
    with connect_orchestration_db(db_path) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key = ?",
            ("last_wal_checkpoint",),
        ).fetchone()
    assert row is not None
    payload = json.loads(row["value_json"])
    assert payload["mode"] == "passive"
    assert isinstance(payload["wal_size_bytes"], int)

    skipped = maybe_checkpoint_orchestration_wal(
        db_path,
        mode="truncate",
        min_bytes=0,
        interval_seconds=300,
        busy_timeout_ms=100,
    )
    assert skipped.attempted is False
    assert skipped.skipped_reason == "interval"


def test_sqlite_execution_repository_clears_target_event_without_full_file_store(
    tmp_path: Path,
) -> None:
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    target = _effect_record(execution_id="exec-1", idempotency_key="idem-1")
    other = _effect_record(execution_id="exec-2", idempotency_key="idem-2", event_id="evt-other")
    execution_store.record_execution(target)
    execution_store.record_execution(other)

    cleared = execution_store.clear_event_records(
        "evt-1",
        cleared_at=datetime(2026, 4, 23, 13, 0, tzinfo=UTC),
        clear_reason="manual_clear_stale_workflow_runs",
        clear_error="cleared",
    )

    assert cleared == 1
    assert execution_store.get_execution("exec-1").status == "failed"
    assert execution_store.get_execution("exec-1").dismissed_at is not None
    assert execution_store.get_execution("exec-2").status == "running"


def test_workflow_config_snapshot_repository_round_trips_snapshot(tmp_path: Path) -> None:
    snapshot = _workflow_config_snapshot_payload()
    repository = SqliteWorkflowConfigSnapshotRepository(tmp_path / "orchestration.db")

    repository.put_snapshot(snapshot)

    assert repository.get_snapshot("revision-1") == snapshot
    assert repository.list_snapshots() == [snapshot]


@pytest.mark.parametrize("indexed", [True, False])
def test_direct_prompt_receipt_is_opt_in_and_scoped_to_its_dispatch(tmp_path, indexed):
    from evander.repositories.orchestration import read_latest_agent_prompt_lifecycle_receipt

    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    record = _effect_record(execution_id="direct", idempotency_key="direct")
    record.effect_type = "effect:agent:prompt"
    record.status = "waiting"
    record.outputs = {"session_id": "owner", "dispatch_id": "direct-run", "thread_id": "native"}
    store.put(record)
    if not indexed:
        with connect_orchestration_db(store.path) as connection:
            connection.execute("DELETE FROM workflow_execution_projections")
    assert read_latest_agent_prompt_lifecycle_receipt(
        store.path, session_id="owner", dispatch_id="direct-run"
    ) is None
    found = read_latest_agent_prompt_lifecycle_receipt(
        store.path, session_id="owner", dispatch_id="direct-run", include_direct_prompts=True
    )
    assert found["outputs"]["thread_id"] == "native"
    assert read_latest_agent_prompt_lifecycle_receipt(
        store.path, session_id="other", dispatch_id="direct-run", include_direct_prompts=True
    ) is None
