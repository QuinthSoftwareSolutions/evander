from __future__ import annotations

import fcntl
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    JsonExecutionStore,
    JsonGateExecutionStore,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from agent_sandbox.repositories.orchestration import (
    JsonEffectCursorRepository,
    JsonGateCursorRepository,
    JsonWorkflowConfigSnapshotRepository,
    SqliteWorkflowConfigSnapshotRepository,
)
from agent_sandbox.repositories.orchestration_db import (
    connect_orchestration_db,
    maybe_checkpoint_orchestration_wal,
)


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


def test_execution_repository_supports_semantic_idempotency_and_gate_queries(
    tmp_path: Path,
) -> None:
    execution_store = JsonExecutionStore(tmp_path / "effect-executions.json")
    gate_store = JsonGateExecutionStore(tmp_path / "gate-executions.json")

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


def test_execution_repository_put_uses_shared_file_lock(tmp_path: Path) -> None:
    path = tmp_path / "effect-executions.json"
    execution_store = JsonExecutionStore(path)
    lock_path = path.with_name(f".{path.name}.lock")
    writer_entered = threading.Event()
    writer_finished = threading.Event()

    def write_record() -> None:
        writer_entered.set()
        execution_store.record_execution(
            _effect_record(execution_id="exec-locked", idempotency_key="idem-locked")
        )
        writer_finished.set()

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        writer = threading.Thread(target=write_record)
        writer.start()
        assert writer_entered.wait(timeout=1)
        assert not writer_finished.wait(timeout=0.1)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    writer.join(timeout=1)
    assert writer_finished.is_set()
    assert execution_store.get_execution("exec-locked") is not None


def test_execution_repository_update_all_preserves_records_written_after_stale_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "effect-executions.json"
    execution_store = JsonExecutionStore(path)
    execution_store.record_execution(_effect_record(execution_id="exec-old", idempotency_key="old"))

    stale_snapshot = json.loads(path.read_text(encoding="utf-8"))
    assert list(stale_snapshot) == ["exec-old"]

    execution_store.record_execution(_effect_record(execution_id="exec-new", idempotency_key="new"))

    def fail_old_record(records: dict[str, EffectExecutionRecord]) -> int:
        record = records["exec-old"]
        record.status = "failed"
        records["exec-old"] = record
        return 1

    assert execution_store.update_all(fail_old_record) == 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["exec-old"]["status"] == "failed"
    assert payload["exec-new"]["status"] == "running"


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
    assert [record.execution_id for record in execution_store.list_active_records()] == [
        "exec-1"
    ]
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
                      'idx_effect_executions_node_failed_dismissed',
                      'idx_gate_executions_node_failed_dismissed'
                  )
                """
            ).fetchall()
        }

    assert index_names == {
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
        execution_id: after_payloads[execution_id]
        for execution_id in unchanged_before
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
    assert gate_store.get_execution("gate-failed-1").dismiss_reason == "manual_dismiss_node_failures"
    assert gate_store.get_execution("gate-failed-2").dismissed_at == dismissed_at
    assert gate_store.get_execution("gate-failed-2").dismiss_reason == "manual_dismiss_node_failures"
    assert gate_store.get_execution("gate-running").dismissed_at is None
    assert gate_store.get_execution("gate-succeeded").dismissed_at is None
    assert gate_store.get_execution("gate-dismissed").dismiss_reason == "manual_dismiss_node_failures"
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
        execution_id: after_payloads[execution_id]
        for execution_id in unchanged_before
    } == unchanged_before


def test_execution_repositories_list_waiting_and_completion_pending_records(
    tmp_path: Path,
) -> None:
    stores = [
        JsonExecutionStore(tmp_path / "effect-executions.json"),
        SqliteExecutionStore(tmp_path / "orchestration.db"),
    ]
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
            for record in execution_store.list_waiting_records(
                "effect:agent:enqueue-prompt"
            )
        ] == [waiting_target.execution_id]
        assert {record.execution_id for record in execution_store.list_waiting_records()} == {
            waiting_target.execution_id,
            waiting_other.execution_id,
        }
        assert [
            record.execution_id
            for record in execution_store.list_completion_pending_records()
        ] == [pending.execution_id]


def test_gate_execution_repositories_list_waiting_and_completion_pending_records(
    tmp_path: Path,
) -> None:
    stores = [
        JsonGateExecutionStore(tmp_path / "gate-executions.json"),
        SqliteGateExecutionStore(tmp_path / "orchestration.db"),
    ]
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
            record.execution_id
            for record in gate_store.list_waiting_records("gate:await-agent")
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
    assert [record.execution_id for record in gate_store.list_active_records()] == [
        "gate-1"
    ]

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
    assert result.mode == "truncate"
    with connect_orchestration_db(db_path) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key = ?",
            ("last_wal_checkpoint",),
        ).fetchone()
    assert row is not None
    payload = json.loads(row["value_json"])
    assert payload["mode"] == "truncate"
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
    other = _effect_record(
        execution_id="exec-2", idempotency_key="idem-2", event_id="evt-other"
    )
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


def test_workflow_config_snapshot_repository_supports_json_and_sqlite_backends(
    tmp_path: Path,
) -> None:
    snapshot = _workflow_config_snapshot_payload()
    json_repo = JsonWorkflowConfigSnapshotRepository(
        tmp_path / "workflow-config-snapshots.json"
    )
    sqlite_repo = SqliteWorkflowConfigSnapshotRepository(tmp_path / "orchestration.db")

    json_repo.put_snapshot(snapshot)
    sqlite_repo.put_snapshot(snapshot)

    assert json_repo.get_snapshot("revision-1") == snapshot
    assert sqlite_repo.get_snapshot("revision-1") == snapshot
    assert json_repo.list_snapshots() == [snapshot]
    assert sqlite_repo.list_snapshots() == [snapshot]
