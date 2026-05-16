from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from agent_sandbox.orchestration.migration import migrate_orchestration_store_to_sqlite
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue, TriggerQueueRecord
from agent_sandbox.workflow.events import SqliteWorkflowEventJournal, WorkflowChangeEvent


def test_migrate_orchestration_store_imports_json_history_idempotently(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    trigger_path = tmp_path / "trigger-orchestrator-events.json"
    effect_path = tmp_path / "effect-orchestrator-executions.json"
    gate_path = tmp_path / "gate-orchestrator-executions.json"
    journal_path = tmp_path / "workflow-events.jsonl"
    db_path = tmp_path / "orchestration.db"

    trigger_record = TriggerQueueRecord(
        event_id="evt-1",
        trigger_id="trigger-1",
        trigger_type="trigger:test",
        trace_id="trace-1",
        occurred_at=now,
        payload={"x": 1},
        idempotency_key="trigger-idem-1",
        extra_field="preserved",
    )
    effect_record = EffectExecutionRecord(
        execution_id="exec-1",
        idempotency_key="effect-idem-1",
        event_id="evt-1",
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:test",
        effect_id="effect-1",
        effect_type="effect:test",
        status="running",
        started_at=now,
        finished_at=now,
        resolved_config_hash="hash-1",
        resolved_config={},
        input_payload={"x": 1},
        outputs={},
        extra_field="preserved",
    )
    gate_record = GateExecutionRecord(
        execution_id="gate-1",
        idempotency_key="gate-idem-1",
        event_id="evt-1",
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:test",
        gate_id="gate-1",
        gate_type="gate:test",
        status="waiting",
        started_at=now,
        finished_at=now,
        resolved_config_hash="hash-1",
        resolved_config={},
        input_payload={"x": 1},
        outputs={},
        inputs_by_source={},
        input_event_ids_by_source={},
        input_occurred_at_by_source={},
        extra_field="preserved",
    )
    workflow_event = WorkflowChangeEvent(
        event_id="journal-1",
        event_type="workflow.trigger_event.published",
        occurred_at=now,
        source="trigger_queue",
        subject_id="evt-1",
        payload={"trigger_id": "trigger-1"},
    )

    trigger_path.write_text(
        json.dumps({"evt-1": trigger_record.model_dump(mode="json")}),
        encoding="utf-8",
    )
    effect_path.write_text(
        json.dumps({"exec-1": effect_record.model_dump(mode="json")}),
        encoding="utf-8",
    )
    gate_path.write_text(
        json.dumps({"gate-1": gate_record.model_dump(mode="json")}),
        encoding="utf-8",
    )
    journal_path.write_text(
        json.dumps(workflow_event.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    first = migrate_orchestration_store_to_sqlite(
        database_path=db_path,
        trigger_events_path=trigger_path,
        effect_executions_path=effect_path,
        gate_executions_path=gate_path,
        workflow_events_path=journal_path,
        retention_days=3650,
    )
    second = migrate_orchestration_store_to_sqlite(
        database_path=db_path,
        trigger_events_path=trigger_path,
        effect_executions_path=effect_path,
        gate_executions_path=gate_path,
        workflow_events_path=journal_path,
        retention_days=3650,
    )

    assert first.trigger_events_imported == 1
    assert first.effect_executions_imported == 1
    assert first.gate_executions_imported == 1
    assert first.workflow_events_imported == 1
    assert second.trigger_events_existing == 1
    assert second.effect_executions_existing == 1
    assert second.gate_executions_existing == 1
    assert second.workflow_events_existing == 1

    migrated_trigger = SqliteTriggerEventQueue(db_path).read_all()["evt-1"]
    migrated_effect = SqliteExecutionStore(db_path).get_execution("exec-1")
    migrated_gate = SqliteGateExecutionStore(db_path).get_execution("gate-1")
    migrated_events, _cursor = SqliteWorkflowEventJournal(db_path).read_after(0)

    assert migrated_trigger.model_extra["extra_field"] == "preserved"
    assert migrated_effect.model_extra["extra_field"] == "preserved"
    assert migrated_gate.model_extra["extra_field"] == "preserved"
    assert [event.event_id for event in migrated_events] == ["journal-1"]
