from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    JsonExecutionStore,
    JsonGateExecutionStore,
)
from agent_sandbox.orchestration_repositories import JsonEffectCursorRepository, JsonGateCursorRepository


def _effect_record(*, execution_id: str, idempotency_key: str, event_id: str = "evt-1") -> EffectExecutionRecord:
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


def test_execution_repository_supports_semantic_idempotency_and_gate_queries(tmp_path: Path) -> None:
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
