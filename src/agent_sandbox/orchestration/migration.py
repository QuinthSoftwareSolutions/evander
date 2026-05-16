from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_sandbox.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from agent_sandbox.repositories.orchestration_db import (
    connect_orchestration_db,
    ensure_orchestration_schema,
    run_orchestration_retention,
)
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue, TriggerQueueRecord
from agent_sandbox.workflow.events import SqliteWorkflowEventJournal, WorkflowChangeEvent


@dataclass(frozen=True)
class OrchestrationStoreMigrationResult:
    database_path: str
    trigger_events_imported: int
    trigger_events_existing: int
    trigger_events_skipped: int
    effect_executions_imported: int
    effect_executions_existing: int
    effect_executions_skipped: int
    gate_executions_imported: int
    gate_executions_existing: int
    gate_executions_skipped: int
    workflow_events_imported: int
    workflow_events_existing: int
    workflow_events_skipped: int
    retention_trigger_events_deleted: int = 0
    retention_effect_executions_deleted: int = 0
    retention_gate_executions_deleted: int = 0
    retention_workflow_events_deleted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def migrate_orchestration_store_to_sqlite(
    *,
    database_path: Path,
    trigger_events_path: Path,
    effect_executions_path: Path,
    gate_executions_path: Path,
    workflow_events_path: Path,
    retention_days: int | None,
) -> OrchestrationStoreMigrationResult:
    database_path = Path(database_path)
    ensure_orchestration_schema(database_path)

    trigger_imported, trigger_existing, trigger_skipped = _import_trigger_events(
        database_path, trigger_events_path
    )
    effect_imported, effect_existing, effect_skipped = _import_execution_records(
        SqliteExecutionStore(database_path, retention_days=None),
        effect_executions_path,
        EffectExecutionRecord,
    )
    gate_imported, gate_existing, gate_skipped = _import_execution_records(
        SqliteGateExecutionStore(database_path, retention_days=None),
        gate_executions_path,
        GateExecutionRecord,
    )
    workflow_imported, workflow_existing, workflow_skipped = _import_workflow_events(
        database_path, workflow_events_path
    )

    retention = None
    if retention_days is not None:
        retention = run_orchestration_retention(database_path, retention_days=retention_days)

    return OrchestrationStoreMigrationResult(
        database_path=str(database_path),
        trigger_events_imported=trigger_imported,
        trigger_events_existing=trigger_existing,
        trigger_events_skipped=trigger_skipped,
        effect_executions_imported=effect_imported,
        effect_executions_existing=effect_existing,
        effect_executions_skipped=effect_skipped,
        gate_executions_imported=gate_imported,
        gate_executions_existing=gate_existing,
        gate_executions_skipped=gate_skipped,
        workflow_events_imported=workflow_imported,
        workflow_events_existing=workflow_existing,
        workflow_events_skipped=workflow_skipped,
        retention_trigger_events_deleted=(
            retention.trigger_events_deleted if retention is not None else 0
        ),
        retention_effect_executions_deleted=(
            retention.effect_executions_deleted if retention is not None else 0
        ),
        retention_gate_executions_deleted=(
            retention.gate_executions_deleted if retention is not None else 0
        ),
        retention_workflow_events_deleted=(
            retention.workflow_events_deleted if retention is not None else 0
        ),
    )


def _import_trigger_events(database_path: Path, source_path: Path) -> tuple[int, int, int]:
    payload = _read_json_object(source_path)
    imported = 0
    existing = 0
    skipped = 0
    with connect_orchestration_db(database_path) as connection:
        for record_key, raw_record in payload.items():
            if not isinstance(raw_record, dict):
                skipped += 1
                continue
            candidate = dict(raw_record)
            candidate.setdefault("event_id", record_key)
            try:
                record = TriggerQueueRecord.model_validate(candidate)
            except ValidationError:
                skipped += 1
                continue
            existing_row = connection.execute(
                "SELECT 1 FROM trigger_events WHERE event_id = ?",
                (record.event_id,),
            ).fetchone()
            if existing_row is not None:
                existing += 1
                continue
            if record.idempotency_key is not None:
                idempotency_row = connection.execute(
                    "SELECT 1 FROM trigger_events WHERE idempotency_key = ?",
                    (record.idempotency_key,),
                ).fetchone()
                if idempotency_row is not None:
                    existing += 1
                    continue
            SqliteTriggerEventQueue._upsert_record(connection, record)
            imported += 1
    return imported, existing, skipped


def _import_execution_records(
    repository: SqliteExecutionStore | SqliteGateExecutionStore,
    source_path: Path,
    model: type[EffectExecutionRecord] | type[GateExecutionRecord],
) -> tuple[int, int, int]:
    payload = _read_json_object(source_path)
    imported = 0
    existing = 0
    skipped = 0
    for record_key, raw_record in payload.items():
        if not isinstance(raw_record, dict):
            skipped += 1
            continue
        candidate = dict(raw_record)
        candidate.setdefault("execution_id", record_key)
        try:
            record = model.model_validate(candidate)
        except ValidationError:
            skipped += 1
            continue
        if repository.get_execution(record.execution_id) is not None:
            existing += 1
            continue
        if repository.find_execution_by_idempotency_key(record.idempotency_key) is not None:
            existing += 1
            continue
        repository.record_execution(record)
        imported += 1
    return imported, existing, skipped


def _import_workflow_events(database_path: Path, source_path: Path) -> tuple[int, int, int]:
    if not source_path.exists():
        return 0, 0, 0
    journal = SqliteWorkflowEventJournal(database_path, retention_days=None)
    imported = 0
    existing = 0
    skipped = 0
    with source_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                raw_event = json.loads(text)
                event = WorkflowChangeEvent.model_validate(raw_event)
            except (json.JSONDecodeError, ValidationError):
                skipped += 1
                continue
            with connect_orchestration_db(database_path) as connection:
                existing_row = connection.execute(
                    "SELECT 1 FROM workflow_events WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
            if existing_row is not None:
                existing += 1
                continue
            journal.put(event)
            imported += 1
    return imported, existing, skipped


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
