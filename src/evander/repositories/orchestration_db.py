from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_WAL_CHECKPOINT_BUSY_TIMEOUT_MS = 1_000
SQLITE_WAL_CHECKPOINT_MIN_BYTES = 64 * 1024 * 1024
SQLITE_WAL_CHECKPOINT_INTERVAL_SECONDS = 5 * 60
SQLITE_WAL_CHECKPOINT_MODES = {
    "passive": "PASSIVE",
    "full": "FULL",
    "restart": "RESTART",
    "truncate": "TRUNCATE",
}


def _compare_execution_history_numbers(left: str | None, right: str | None) -> int | None:
    if left is None or right is None:
        return None
    try:
        left_value = Decimal(left)
        right_value = Decimal(right)
        if not left_value.is_finite() or not right_value.is_finite():
            return None
        return (left_value > right_value) - (left_value < right_value)
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True)
class OrchestrationRetentionResult:
    trigger_events_deleted: int = 0
    effect_executions_deleted: int = 0
    gate_executions_deleted: int = 0
    workflow_events_deleted: int = 0
    compact_executions_deleted: int = 0
    compact_payload_blobs_deleted: int = 0
    internal_events_deleted: int = 0
    scheduled_internal_events_deleted: int = 0


@dataclass(frozen=True)
class OrchestrationWalCheckpointResult:
    attempted: bool = False
    skipped_reason: str | None = None
    mode: str = "truncate"
    wal_size_bytes: int = 0
    busy: int | None = None
    log_frames: int | None = None
    checkpointed_frames: int | None = None
    error: str | None = None


@contextmanager
def connect_orchestration_db(path: Path | str) -> Iterator[sqlite3.Connection]:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30.0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function(
            "execution_history_numeric_compare", 2, _compare_execution_history_numbers
        )
        with connection:
            yield connection
    finally:
        connection.close()


@contextmanager
def _try_orchestration_retention_lock(path: Path | str) -> Iterator[bool]:
    """Try to own daily retention across all processes using this database."""

    db_path = Path(path)
    lock_path = db_path.with_name(f"{db_path.name}.retention.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    acquired = False
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        acquired = True
        yield True
    finally:
        try:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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
            CREATE INDEX IF NOT EXISTS idx_trigger_events_activity
                ON trigger_events(COALESCE(delivered_at, inflight_at, occurred_at), event_id);
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
            CREATE INDEX IF NOT EXISTS idx_effect_executions_status_type
                ON effect_executions(status, effect_type, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_effect
                ON effect_executions(effect_id, effect_type, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_node_failed_dismissed
                ON effect_executions(effect_id, status, dismissed_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_trace
                ON effect_executions(trace_id, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_started
                ON effect_executions(started_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_finished
                ON effect_executions(finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_effect_executions_activity
                ON effect_executions(COALESCE(finished_at, started_at), execution_id);

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
            CREATE INDEX IF NOT EXISTS idx_gate_executions_status_type
                ON gate_executions(status, gate_type, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_gate
                ON gate_executions(gate_id, gate_type, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_node_failed_dismissed
                ON gate_executions(gate_id, status, dismissed_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_trace
                ON gate_executions(trace_id, finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_started
                ON gate_executions(started_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_finished
                ON gate_executions(finished_at, execution_id);
            CREATE INDEX IF NOT EXISTS idx_gate_executions_activity
                ON gate_executions(COALESCE(finished_at, started_at), execution_id);

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

            CREATE TABLE IF NOT EXISTS internal_events (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                correlation_key TEXT,
                created_at TEXT NOT NULL,
                source_json TEXT,
                idempotency_key TEXT UNIQUE,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_internal_events_type_row
                ON internal_events(event_type, row_id);
            CREATE INDEX IF NOT EXISTS idx_internal_events_created
                ON internal_events(created_at, row_id);
            CREATE INDEX IF NOT EXISTS idx_internal_events_idempotency
                ON internal_events(idempotency_key);

            CREATE TABLE IF NOT EXISTS internal_event_subscriptions (
                trigger_id TEXT PRIMARY KEY,
                trigger_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                filter_hash TEXT NOT NULL,
                filter_json TEXT NOT NULL,
                scan_cursor_row_id INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                config_revision TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_internal_event_subscriptions_active
                ON internal_event_subscriptions(active, event_type, scan_cursor_row_id);

            CREATE TABLE IF NOT EXISTS internal_event_deliveries (
                trigger_id TEXT NOT NULL,
                internal_event_row_id INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending', 'delivered')),
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                PRIMARY KEY(trigger_id, internal_event_row_id),
                FOREIGN KEY(trigger_id)
                    REFERENCES internal_event_subscriptions(trigger_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(internal_event_row_id)
                    REFERENCES internal_events(row_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_internal_event_deliveries_pending
                ON internal_event_deliveries(trigger_id, state, internal_event_row_id);

            CREATE TABLE IF NOT EXISTS scheduled_internal_events (
                schedule_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                correlation_key TEXT,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'published')),
                created_at TEXT NOT NULL,
                published_at TEXT,
                published_event_id TEXT,
                idempotency_key TEXT UNIQUE,
                source_json TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_scheduled_internal_events_due
                ON scheduled_internal_events(status, run_at, created_at, schedule_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_internal_events_published
                ON scheduled_internal_events(status, published_at, schedule_id);

            CREATE TABLE IF NOT EXISTS workflow_config_snapshots (
                revision TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_config_snapshots_captured
                ON workflow_config_snapshots(captured_at, revision);

            CREATE TABLE IF NOT EXISTS workflow_execution_payload_blobs (
                sha256 TEXT PRIMARY KEY,
                codec TEXT NOT NULL,
                original_bytes INTEGER NOT NULL,
                compressed_bytes INTEGER NOT NULL,
                payload_blob BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_node_execution_history (
                execution_key TEXT PRIMARY KEY,
                execution_kind TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                trace_id TEXT,
                node_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                source_node_id TEXT,
                source_trigger_type TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                config_revision TEXT,
                config_hash TEXT,
                directory_path TEXT,
                published_event_id TEXT,
                error TEXT,
                input_blob_sha256 TEXT,
                output_blob_sha256 TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(execution_kind, execution_id),
                FOREIGN KEY(input_blob_sha256)
                    REFERENCES workflow_execution_payload_blobs(sha256),
                FOREIGN KEY(output_blob_sha256)
                    REFERENCES workflow_execution_payload_blobs(sha256)
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_node_history_node_started
                ON workflow_node_execution_history(node_id, started_at DESC, execution_key DESC);
            CREATE INDEX IF NOT EXISTS idx_workflow_node_history_node_status_started
                ON workflow_node_execution_history(
                    node_id, status, started_at DESC, execution_key DESC
                );
            CREATE INDEX IF NOT EXISTS idx_workflow_node_history_event
                ON workflow_node_execution_history(event_id, execution_key);
            CREATE INDEX IF NOT EXISTS idx_workflow_node_history_finished
                ON workflow_node_execution_history(finished_at, execution_key);

            CREATE TABLE IF NOT EXISTS workflow_node_execution_history_sources (
                execution_key TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                source_event_id TEXT,
                occurred_at TEXT,
                PRIMARY KEY(execution_key, source_node_id),
                FOREIGN KEY(execution_key)
                    REFERENCES workflow_node_execution_history(execution_key)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS workflow_node_execution_history_values (
                execution_key TEXT NOT NULL,
                location TEXT NOT NULL,
                json_pointer TEXT NOT NULL,
                value_type TEXT NOT NULL,
                value_hash TEXT,
                text_value TEXT,
                numeric_value REAL,
                boolean_value INTEGER,
                PRIMARY KEY(execution_key, location, json_pointer),
                FOREIGN KEY(execution_key)
                    REFERENCES workflow_node_execution_history(execution_key)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_node_history_values_lookup
                ON workflow_node_execution_history_values(
                    location, json_pointer, value_type, value_hash, execution_key
                );
            CREATE INDEX IF NOT EXISTS idx_workflow_node_history_values_numeric
                ON workflow_node_execution_history_values(
                    location, json_pointer, value_type, numeric_value, execution_key
                );
            """
        )
        v3_applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (3,)
        ).fetchone()
        if v3_applied is None:
            history_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(workflow_node_execution_history)")
            }
            if "directory_path" not in history_columns:
                connection.execute(
                    "ALTER TABLE workflow_node_execution_history ADD COLUMN directory_path TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_node_history_directory_started
                ON workflow_node_execution_history(
                    directory_path, node_id, started_at DESC, execution_key DESC
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_node_history_values_exact
                ON workflow_node_execution_history_values(
                    location, json_pointer, value_type, text_value, execution_key
                )
                """
            )
            # Execution-history comparisons require a fixed-width UTC representation.
            # Rows written by schema v2 at an exact second used the shorter ``...00Z``
            # spelling, which is not lexicographically ordered with fractional seconds.
            connection.execute(
                """
                UPDATE workflow_node_execution_history
                SET started_at = substr(started_at, 1, 19) || '.000000Z'
                WHERE length(started_at) = 20 AND substr(started_at, -1) = 'Z'
                """
            )
            connection.execute(
                """
                UPDATE workflow_node_execution_history
                SET finished_at = substr(finished_at, 1, 19) || '.000000Z'
                WHERE length(finished_at) = 20 AND substr(finished_at, -1) = 'Z'
                """
            )
        applied_at = datetime_to_storage(datetime.now(UTC))
        connection.executemany(
            """
            INSERT OR IGNORE INTO schema_migrations(version, applied_at)
            VALUES (?, ?)
            """,
            [(version, applied_at) for version in range(1, SCHEMA_VERSION + 1)],
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
    path: Path | str,
    *,
    retention_days: int | None,
    compact_history_retention_days: int | None = None,
) -> OrchestrationRetentionResult:
    if retention_days is None:
        return OrchestrationRetentionResult()
    if compact_history_retention_days is None:
        raise ValueError(
            "compact_history_retention_days is required when retention_days is configured"
        )
    with _try_orchestration_retention_lock(path) as acquired:
        if not acquired:
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
        return run_orchestration_retention(
            path,
            retention_days=retention_days,
            compact_history_retention_days=compact_history_retention_days,
        )


def maybe_checkpoint_orchestration_wal(
    path: Path | str,
    *,
    mode: str,
    min_bytes: int = SQLITE_WAL_CHECKPOINT_MIN_BYTES,
    interval_seconds: int = SQLITE_WAL_CHECKPOINT_INTERVAL_SECONDS,
    busy_timeout_ms: int = SQLITE_WAL_CHECKPOINT_BUSY_TIMEOUT_MS,
) -> OrchestrationWalCheckpointResult:
    normalized_mode = str(mode or "").strip().casefold()
    if normalized_mode in {"", "disabled", "none", "off"}:
        return OrchestrationWalCheckpointResult(
            skipped_reason="disabled",
            mode=normalized_mode or "disabled",
        )
    checkpoint_mode = SQLITE_WAL_CHECKPOINT_MODES.get(normalized_mode)
    if checkpoint_mode is None:
        valid_modes = ", ".join([*SQLITE_WAL_CHECKPOINT_MODES, "disabled"])
        raise ValueError(f"Unsupported WAL checkpoint mode {mode!r}; expected one of {valid_modes}")
    if min_bytes < 0:
        raise ValueError("min_bytes must be non-negative")
    if interval_seconds < 0:
        raise ValueError("interval_seconds must be non-negative")
    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be non-negative")

    ensure_orchestration_schema(path)
    db_path = Path(path)
    wal_path = db_path.with_name(f"{db_path.name}-wal")
    wal_size_bytes = wal_path.stat().st_size if wal_path.exists() else 0
    if min_bytes > 0 and wal_size_bytes < min_bytes:
        return OrchestrationWalCheckpointResult(
            skipped_reason="below_min_bytes",
            mode=normalized_mode,
            wal_size_bytes=wal_size_bytes,
        )

    now = datetime.now(UTC)
    with connect_orchestration_db(path) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key = ?",
            ("last_wal_checkpoint",),
        ).fetchone()
        if interval_seconds > 0 and row is not None:
            payload = json_loads_object(row["value_json"])
            last_checkpoint = _datetime_from_storage(payload.get("checked_at"))
            if (
                last_checkpoint is not None
                and (now - last_checkpoint).total_seconds() < interval_seconds
            ):
                return OrchestrationWalCheckpointResult(
                    skipped_reason="interval",
                    mode=normalized_mode,
                    wal_size_bytes=wal_size_bytes,
                )

        connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        try:
            checkpoint_row = connection.execute(
                f"PRAGMA wal_checkpoint({checkpoint_mode})"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            return OrchestrationWalCheckpointResult(
                attempted=True,
                skipped_reason="checkpoint_failed",
                mode=normalized_mode,
                wal_size_bytes=wal_size_bytes,
                error=str(exc),
            )
        busy = int(checkpoint_row[0]) if checkpoint_row is not None else None
        log_frames = int(checkpoint_row[1]) if checkpoint_row is not None else None
        checkpointed_frames = int(checkpoint_row[2]) if checkpoint_row is not None else None
        result = OrchestrationWalCheckpointResult(
            attempted=True,
            mode=normalized_mode,
            wal_size_bytes=wal_size_bytes,
            busy=busy,
            log_frames=log_frames,
            checkpointed_frames=checkpointed_frames,
        )
        try:
            connection.execute(
                """
                INSERT INTO orchestration_metadata(key, value_json)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (
                    "last_wal_checkpoint",
                    json_dumps(
                        {
                            "checked_at": datetime_to_storage(now),
                            "mode": result.mode,
                            "wal_size_bytes": result.wal_size_bytes,
                            "busy": result.busy,
                            "log_frames": result.log_frames,
                            "checkpointed_frames": result.checkpointed_frames,
                        }
                    ),
                ),
            )
        except sqlite3.OperationalError as exc:
            return OrchestrationWalCheckpointResult(
                attempted=True,
                skipped_reason="metadata_update_failed",
                mode=normalized_mode,
                wal_size_bytes=wal_size_bytes,
                busy=busy,
                log_frames=log_frames,
                checkpointed_frames=checkpointed_frames,
                error=str(exc),
            )
    return result


def run_orchestration_retention(
    path: Path | str,
    *,
    retention_days: int,
    compact_history_retention_days: int,
    execution_history_batch_size: int = 250,
) -> OrchestrationRetentionResult:
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    if compact_history_retention_days < 0:
        raise ValueError("compact_history_retention_days must be non-negative")
    if execution_history_batch_size < 1 or execution_history_batch_size > 10_000:
        raise ValueError("execution_history_batch_size must be between 1 and 10000")
    ensure_orchestration_schema(path)
    now = datetime.now(UTC)
    cutoff = datetime_to_storage(now - timedelta(days=retention_days))
    assert cutoff is not None
    compact_cutoff = datetime_to_storage(now - timedelta(days=compact_history_retention_days))
    assert compact_cutoff is not None
    today = now.date().isoformat()

    trigger_deleted = 0
    effect_deleted = 0
    gate_deleted = 0
    while True:
        # Keep retention writes bounded: each batch commits before the next
        # source-key batch is read. Execution rows receive a compact history
        # copy; completion transport envelopes are deliberately not archived
        # because they must not create a duplicate node-history entry.
        with connect_orchestration_db(path) as connection:
            # Import locally to keep the low-level schema module independent
            # of the history query surface at import time.
            from evander.repositories.execution_history import archive_records_for_retention

            archived_sources = archive_records_for_retention(
                connection,
                cutoff=cutoff,
                batch_size=execution_history_batch_size,
            )
            if not archived_sources:
                break
            deleted_by_kind = _delete_archived_execution_sources(connection, archived_sources)
            trigger_deleted += deleted_by_kind["trigger"] + deleted_by_kind["transport"]
            effect_deleted += deleted_by_kind["effect"]
            gate_deleted += deleted_by_kind["gate"]

    with connect_orchestration_db(path) as connection:
        # Full execution records are already copied and deleted in bounded
        # transactions above. The remaining retention tables contain no
        # archival payload work.
        workflow_deleted = connection.execute(
            "DELETE FROM workflow_events WHERE occurred_at < ?",
            (cutoff,),
        ).rowcount
        compact_deleted = connection.execute(
            """
            DELETE FROM workflow_node_execution_history
            WHERE finished_at < ?
            """,
            (compact_cutoff,),
        ).rowcount
        blob_deleted = connection.execute(
            """
            DELETE FROM workflow_execution_payload_blobs
            WHERE sha256 NOT IN (
                SELECT input_blob_sha256
                FROM workflow_node_execution_history
                WHERE input_blob_sha256 IS NOT NULL
                UNION
                SELECT output_blob_sha256
                FROM workflow_node_execution_history
                WHERE output_blob_sha256 IS NOT NULL
            )
            """
        ).rowcount
        internal_deleted = connection.execute(
            """
            DELETE FROM internal_events
            WHERE created_at < ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM internal_event_deliveries AS delivery
                  WHERE delivery.internal_event_row_id = internal_events.row_id
                    AND delivery.state = 'pending'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM internal_event_subscriptions AS subscription
                  WHERE subscription.active = 1
                    AND subscription.event_type = internal_events.event_type
                    AND subscription.scan_cursor_row_id < internal_events.row_id
              )
            """,
            (cutoff,),
        ).rowcount
        scheduled_deleted = connection.execute(
            """
            DELETE FROM scheduled_internal_events
            WHERE status = 'published'
              AND published_at < ?
            """,
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
        compact_executions_deleted=max(0, compact_deleted),
        compact_payload_blobs_deleted=max(0, blob_deleted),
        internal_events_deleted=max(0, internal_deleted),
        scheduled_internal_events_deleted=max(0, scheduled_deleted),
    )


def _delete_archived_execution_sources(
    connection: sqlite3.Connection,
    sources: list[tuple[str, str]],
) -> dict[str, int]:
    """Delete one processed retention batch in the current transaction."""
    deleted = {"trigger": 0, "transport": 0, "effect": 0, "gate": 0}
    source_tables = {
        "trigger": ("trigger_events", "event_id"),
        "transport": ("trigger_events", "event_id"),
        "effect": ("effect_executions", "execution_id"),
        "gate": ("gate_executions", "execution_id"),
    }
    for kind, (table, key_column) in source_tables.items():
        source_ids = [source_id for source_kind, source_id in sources if source_kind == kind]
        if not source_ids:
            continue
        placeholders = ", ".join("?" for _ in source_ids)
        result = connection.execute(
            f"DELETE FROM {table} WHERE {key_column} IN ({placeholders})",
            source_ids,
        )
        deleted[kind] = max(0, result.rowcount)
    return deleted


def _datetime_from_storage(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
