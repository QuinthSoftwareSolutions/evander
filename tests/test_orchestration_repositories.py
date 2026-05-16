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
