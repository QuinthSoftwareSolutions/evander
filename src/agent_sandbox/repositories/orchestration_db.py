from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SQLITE_BUSY_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class OrchestrationRetentionResult:
    trigger_events_deleted: int = 0
    effect_executions_deleted: int = 0
    gate_executions_deleted: int = 0
    workflow_events_deleted: int = 0


def connect_orchestration_db(path: Path | str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_orchestration_schema(path: Path | str) -> None:
    with connect_orchestration_db(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orchestration_metadata (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trigger_events (
                event_id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                delivery_state TEXT NOT NULL,
                trigger_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                inflight_at TEXT,
                delivered_at TEXT,
                clear_reason TEXT,
                error TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trigger_events_state
                ON trigger_events(delivery_state, occurred_at, event_id);
            CREATE INDEX IF NOT EXISTS idx_trigger_events_trigger
                ON trigger_events(trigger_id, trigger_type, occurred_at, event_id);
            CREATE INDEX IF NOT EXISTS idx_trigger_events_occurred
                ON trigger_events(occurred_at, event_id);
            CREATE INDEX IF NOT EXISTS idx_trigger_events_trace
                ON trigger_events(trace_id, occurred_at);
            CREATE INDEX IF NOT EXISTS idx_trigger_events_idempotency
                ON trigger_events(idempotency_key);

            CREATE TABLE IF NOT EXISTS effect_executions (
                execution_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                event_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                source_trigger_type TEXT NOT NULL,
                status TEXT NOT NULL,
                effect_id TEXT NOT NULL,
                effect_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                cleared_at TEXT,
                dismissed_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_effect_executions_event
                ON effect_executions(event_id, status, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_status
                ON effect_executions(status, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_effect
                ON effect_executions(effect_id, effect_type, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_trace
                ON effect_executions(trace_id, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_started
                ON effect_executions(started_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_finished
                ON effect_executions(finished_at, execution_id);

            CREATE TABLE IF NOT EXISTS gate_executions (
                execution_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                event_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                source_trigger_type TEXT NOT NULL,
                status TEXT NOT NULL,
                gate_id TEXT NOT NULL,
                gate_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                cleared_at TEXT,
                dismissed_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_gate_executions_event
                ON gate_executions(event_id, status, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_status
                ON gate_executions(status, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_gate
                ON gate_executions(gate_id, gate_type, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_trace
                ON gate_executions(trace_id, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_started
                ON gate_executions(started_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_finished
                ON gate_executions(finished_at, execution_id);

            CREATE TABLE IF NOT EXISTS workflow_events (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                subject_id TEXT,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_events_type
                ON workflow_events(event_type, row_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_events_source
                ON workflow_events(source, row_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_events_subject
                ON workflow_events(subject_id, row_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_events_occurred
                ON workflow_events(occurred_at, row_id);

            CREATE TABLE IF NOT EXISTS workflow_config_snapshots (
                revision TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_config_snapshots_captured
                ON workflow_config_snapshots(captured_at, revision);
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(version, applied_at)
            VALUES (?, ?)
            """,
            (SCHEMA_VERSION, datetime_to_storage(datetime.now(UTC))),
        )


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def json_loads_object(payload: str | bytes | None) -> dict[str, Any]:
    if payload is None:
        return {}
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def datetime_to_storage(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def maybe_run_orchestration_retention(
    path: Path | str, *, retention_days: int | None
) -> OrchestrationRetentionResult:
    if retention_days is None:
        return OrchestrationRetentionResult()
    ensure_orchestration_schema(path)
    today = datetime.now(UTC).date().isoformat()
    with connect_orchestration_db(path) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key = ?",
            ("last_retention_date",),
        ).fetchone()
        if row is not None:
            payload = json_loads_object(row["value_json"])
            if payload.get("date") == today:
                return OrchestrationRetentionResult()
    return run_orchestration_retention(path, retention_days=retention_days)


def run_orchestration_retention(
    path: Path | str, *, retention_days: int
) -> OrchestrationRetentionResult:
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    ensure_orchestration_schema(path)
    now = datetime.now(UTC)
    cutoff = datetime_to_storage(now - timedelta(days=retention_days))
    assert cutoff is not None
    today = now.date().isoformat()

    with connect_orchestration_db(path) as connection:
        trigger_deleted = connection.execute(
            """
            DELETE FROM trigger_events
            WHERE delivery_state = 'delivered'
              AND COALESCE(delivered_at, occurred_at) < ?
            """,
            (cutoff,),
        ).rowcount
        effect_deleted = connection.execute(
            """
            DELETE FROM effect_executions
            WHERE (
                status IN ('succeeded', 'skipped', 'dropped')
                AND COALESCE(finished_at, started_at) < ?
            )
            OR (
                status = 'failed'
                AND (dismissed_at IS NOT NULL OR cleared_at IS NOT NULL)
                AND COALESCE(dismissed_at, cleared_at, finished_at, started_at) < ?
            )
            """,
            (cutoff, cutoff),
        ).rowcount
        gate_deleted = connection.execute(
            """
            DELETE FROM gate_executions
            WHERE (
                status IN ('succeeded', 'skipped', 'dropped')
                AND COALESCE(finished_at, started_at) < ?
            )
            OR (
                status = 'failed'
                AND (dismissed_at IS NOT NULL OR cleared_at IS NOT NULL)
                AND COALESCE(dismissed_at, cleared_at, finished_at, started_at) < ?
            )
            """,
            (cutoff, cutoff),
        ).rowcount
        workflow_deleted = connection.execute(
            "DELETE FROM workflow_events WHERE occurred_at < ?",
            (cutoff,),
        ).rowcount
        connection.execute(
            """
            INSERT INTO orchestration_metadata(key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("last_retention_date", json_dumps({"date": today})),
        )
    return OrchestrationRetentionResult(
        trigger_events_deleted=max(0, trigger_deleted),
        effect_executions_deleted=max(0, effect_deleted),
        gate_executions_deleted=max(0, gate_deleted),
        workflow_events_deleted=max(0, workflow_deleted),
    )
