from __future__ import annotations

import fcntl
import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic
from typing import Any

from evander.repositories.maintenance_indexes import (
    MAINTENANCE_INDEXES,
    MaintenanceIndexesRequired,
)
from evander.repositories.sqlite_runtime import (
    DEFAULT_BUSY_TIMEOUT_MS,
    MAINTENANCE_TIMEOUT_MS,
    SqliteOperationDeadlineExceeded,
    ensure_sqlite_initialized,
    is_storage_contention,
    open_sqlite_connection,
)

SCHEMA_VERSION = 7
WORKFLOW_REPORT_READ_DEADLINE_SECONDS = 30.0
logger = logging.getLogger(__name__)
_warned_checkpoint_modes: set[tuple[str, str]] = set()
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_WAL_CHECKPOINT_BUSY_TIMEOUT_MS = 1_000
SQLITE_WAL_CHECKPOINT_MIN_BYTES = 64 * 1024 * 1024
SQLITE_WAL_CHECKPOINT_INTERVAL_SECONDS = 5 * 60
_HISTORY_PAYLOAD_SCAN_BATCH_SIZE = 1000
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
    compact_index_rows_deleted: int = 0
    compact_payload_blobs_deleted: int = 0
    internal_events_deleted: int = 0
    scheduled_internal_events_deleted: int = 0
    remaining: bool = False
    skipped_reason: str | None = None


@dataclass(frozen=True)
class OrchestrationWalCheckpointResult:
    attempted: bool = False
    skipped_reason: str | None = None
    mode: str = "passive"
    wal_size_bytes: int = 0
    busy: int | None = None
    log_frames: int | None = None
    checkpointed_frames: int | None = None
    error: str | None = None
    history_index_pending: int = 0
    history_index_oldest_age_seconds: float | None = None
    # None means collection was skipped; false means both bounded sweeps are idle.
    garbage_collection_pending: bool | None = None


@contextmanager
def connect_orchestration_db(
    path: Path | str,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    deadline_seconds: float | None = None,
    operation: str = "orchestration_write",
) -> Iterator[sqlite3.Connection]:
    # Bootstrap remains supported for callers constructing a fresh store.
    if not Path(path).exists():
        ensure_orchestration_schema(path)
    with open_sqlite_connection(
        path,
        timeout_ms=busy_timeout_ms,
        deadline_seconds=deadline_seconds,
        operation=operation,
        managed_checkpoints=True,
    ) as connection:
        connection.create_function(
            "execution_history_numeric_compare", 2, _compare_execution_history_numbers
        )
        yield connection


@contextmanager
def connect_orchestration_db_read_only(
    path: Path | str,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    deadline_seconds: float | None = None,
    operation: str = "orchestration_read",
) -> Iterator[sqlite3.Connection]:
    """Read initialized state without schema writes or WAL negotiation."""
    with open_sqlite_connection(
        path,
        read_only=True,
        timeout_ms=busy_timeout_ms,
        deadline_seconds=deadline_seconds,
        operation=operation,
    ) as connection:
        connection.create_function(
            "execution_history_numeric_compare", 2, _compare_execution_history_numbers
        )
        yield connection


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


def _orchestration_schema_current(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    if row is None or int(row[0] or 0) < SCHEMA_VERSION:
        return False
    from evander.repositories.operational_payloads import operational_schema_is_current

    return operational_schema_is_current(connection)


def ensure_orchestration_schema(path: Path | str, *, force: bool = False) -> None:
    ensure_sqlite_initialized(
        path,
        schema_key=f"orchestration:{SCHEMA_VERSION}",
        is_current=_orchestration_schema_current,
        initialize=_initialize_orchestration_schema,
        force=force,
    )


def _initialize_orchestration_schema(connection: sqlite3.Connection) -> None:
    fresh = (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        is None
    )
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
        CREATE INDEX IF NOT EXISTS idx_effect_executions_prompt_dispatch
            ON effect_executions(effect_type, json_extract(payload_json, '$.outputs.dispatch_id'), status);
        CREATE INDEX IF NOT EXISTS idx_effect_executions_waiting_page
            ON effect_executions(effect_type, execution_id) WHERE status = 'waiting';
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
    from evander.repositories.operational_payloads import initialize_payload_schema

    initialize_payload_schema(connection)
    if fresh:
        _create_maintenance_indexes(connection)
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


def _create_maintenance_indexes(connection: sqlite3.Connection) -> None:
    for index in MAINTENANCE_INDEXES:
        row = connection.execute(
            "SELECT tbl_name,sql FROM sqlite_master WHERE type='index' AND name=?", (index.name,)
        ).fetchone()
        if row is not None:
            if row[0] != index.table or not index.matches_definition(row[1]):
                raise ValueError(f"Maintenance index {index.name} does not match the expected schema")
            continue
        connection.execute(index.create_sql)


def _maintenance_indexes_ready(connection: sqlite3.Connection) -> bool:
    for index in MAINTENANCE_INDEXES:
        row = connection.execute(
            "SELECT tbl_name,sql FROM sqlite_master WHERE type='index' AND name=?", (index.name,)
        ).fetchone()
        if row is None or row[0] != index.table or not index.matches_definition(row[1]):
            return False
    return True


def orchestration_maintenance_index_status(path: Path | str) -> dict[str, Any]:
    with connect_orchestration_db_read_only(
        path, operation="maintenance_index_status"
    ) as connection:
        ready = _maintenance_indexes_ready(connection)
        indexed_schema = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_history_index_jobs'"
        ).fetchone()
        pending, oldest = (
            connection.execute(
                "SELECT COUNT(*), MIN(enqueued_at) FROM workflow_history_index_jobs"
            ).fetchone()
            if indexed_schema
            else (None, None)
        )
    db_path = Path(path)
    try:
        wal_bytes = db_path.with_name(db_path.name + "-wal").stat().st_size
    except FileNotFoundError:
        wal_bytes = 0
    oldest_at = _datetime_from_storage(oldest)
    return {
        "ready": ready,
        "required_indexes": [index.name for index in MAINTENANCE_INDEXES],
        "database_bytes": db_path.stat().st_size,
        "wal_bytes": wal_bytes,
        "history_index_pending": pending,
        "history_index_oldest_age_seconds": max(0, (datetime.now(UTC) - oldest_at).total_seconds())
        if oldest_at
        else None,
    }


def build_orchestration_maintenance_indexes(path: Path | str) -> dict[str, Any]:
    """Explicit offline migration: index builds may scan large legacy tables."""
    ensure_orchestration_schema(path)
    with connect_orchestration_db(
        path,
        busy_timeout_ms=30_000,
        deadline_seconds=3600,
        operation="offline_maintenance_indexes",
    ) as connection:
        _create_maintenance_indexes(connection)
        if not _maintenance_indexes_ready(connection):
            raise ValueError("Maintenance index definitions do not match the expected schema")
    return orchestration_maintenance_index_status(path)


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
    offline: bool = False,
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
    if checkpoint_mode != "PASSIVE" and not offline:
        # Old configurations commonly persisted the former truncate default.
        # Automatic maintenance remains nonblocking; explicit offline callers
        # may still request the configured blocking checkpoint mode.
        warning_key = (str(Path(path).resolve()), normalized_mode)
        if warning_key not in _warned_checkpoint_modes:
            _warned_checkpoint_modes.add(warning_key)
            logger.warning(
                "Automatic orchestration WAL checkpoint mode=%s normalized to passive; "
                "blocking modes require explicit offline maintenance",
                normalized_mode,
            )
        checkpoint_mode = "PASSIVE"
        normalized_mode = "passive"
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
    with connect_orchestration_db(
        path,
        busy_timeout_ms=min(busy_timeout_ms, MAINTENANCE_TIMEOUT_MS)
        if not offline
        else busy_timeout_ms,
        deadline_seconds=3600 if offline else max(0.1, busy_timeout_ms / 1000),
        operation="wal_checkpoint",
    ) as connection:
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


def run_orchestration_retention_slice(
    path: Path | str,
    *,
    retention_days: int,
    compact_history_retention_days: int,
    batch_size: int = 100,
    time_budget_seconds: float = 0.25,
    busy_timeout_ms: int = MAINTENANCE_TIMEOUT_MS,
) -> OrchestrationRetentionResult:
    """Resume bounded maintenance, adapting work to transaction deadlines."""
    if retention_days < 0 or compact_history_retention_days < 0:
        raise ValueError("retention days must be non-negative")
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    with connect_orchestration_db_read_only(path, deadline_seconds=time_budget_seconds) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_batch_limits'"
        ).fetchone()
    limits = json_loads_object(row[0]) if row else {}
    effective_batch = min(batch_size, max(1, int(limits.get("records", batch_size))))
    index_batch = max(1, min(10000, int(limits.get("index_rows", 10000))))
    try:
        return _run_orchestration_retention_slice(
            path, retention_days=retention_days,
            compact_history_retention_days=compact_history_retention_days,
            batch_size=effective_batch, index_batch_size=index_batch,
            time_budget_seconds=time_budget_seconds, busy_timeout_ms=busy_timeout_ms,
            batch_size_limit=batch_size, batch_limits_json=row[0] if row else None,
        )
    except SqliteOperationDeadlineExceeded as error:
        # Lock contention is not evidence that a deletion batch is too large.
        if is_storage_contention(error):
            raise
        # The failed batch has rolled back. Persist adaptation separately so a
        # new process does not retry the same oversized transaction forever.
        with connect_orchestration_db(path, busy_timeout_ms=busy_timeout_ms) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value_json FROM orchestration_metadata WHERE key='retention_batch_limits'"
            ).fetchone()
            current = json_loads_object(row[0]) if row else {}
            reduced = {
                "records": min(int(current.get("records", effective_batch)), max(1, effective_batch // 2)),
                "index_rows": min(int(current.get("index_rows", index_batch)), max(1, index_batch // 2)),
            }
            connection.execute(
                """INSERT INTO orchestration_metadata VALUES ('retention_batch_limits',?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (json_dumps(reduced),),
            )
        return OrchestrationRetentionResult(remaining=True, skipped_reason="batch_deadline_reduced")


def _delete_compact_history_batch(
    connection: sqlite3.Connection, keys: list[str], *, index_batch_size: int,
) -> tuple[int, int]:
    if not keys:
        return 0, 0
    placeholders = ",".join("?" for _ in keys)
    connection.executemany(
        "INSERT OR IGNORE INTO workflow_history_retirements VALUES (?)", [(key,) for key in keys]
    )
    tables = (
        "workflow_node_execution_history_values", "workflow_history_index_values",
    )
    removed = 0
    for table in tables:
        if removed >= index_batch_size:
            break
        removed += connection.execute(
            f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
            f"WHERE execution_key IN ({placeholders}) LIMIT ?)",
            [*keys, index_batch_size - removed],
        ).rowcount
    # Scalar indexes can contain thousands of rows per execution. Drain them
    # before parent cascades; retirement hides partial indexes from readers.
    empty = " AND ".join(
        f"NOT EXISTS(SELECT 1 FROM {table} v WHERE v.execution_key=h.execution_key)"
        for table in tables
    )
    deleted = connection.execute(
        f"DELETE FROM workflow_node_execution_history AS h "
        f"WHERE execution_key IN ({placeholders}) AND {empty}", keys,
    ).rowcount
    return deleted, removed


def _delete_history_payload_batch(
    connection: sqlite3.Connection,
    progress: dict[str, Any],
    *,
    batch_size: int,
) -> tuple[int, bool]:
    """Scan one bounded key range inside the retention writer transaction."""
    if "blob_high_water" not in progress:
        row = connection.execute(
            "SELECT sha256 FROM workflow_execution_payload_blobs ORDER BY sha256 DESC LIMIT 1"
        ).fetchone()
        # Fix the sweep boundary so new writes cannot keep extending this phase.
        progress["blob_high_water"] = row[0] if row else ""
    after = progress.get("blob_cursor", "")
    high_water = progress["blob_high_water"]
    page = [
        row[0]
        for row in connection.execute(
            """SELECT sha256 FROM workflow_execution_payload_blobs
               WHERE sha256>? AND sha256<=? ORDER BY sha256 LIMIT ?""",
            (after, high_water, _HISTORY_PAYLOAD_SCAN_BATCH_SIZE),
        ).fetchall()
    ]
    keys = []
    complete = not page
    if page:
        keys = [
            row[0]
            for row in connection.execute(
                """SELECT b.sha256 FROM workflow_execution_payload_blobs b
                   WHERE b.sha256>? AND b.sha256<=?
                     AND NOT EXISTS (SELECT 1 FROM workflow_node_execution_history h
                                     WHERE h.input_blob_sha256=b.sha256)
                     AND NOT EXISTS (SELECT 1 FROM workflow_node_execution_history h
                                     WHERE h.output_blob_sha256=b.sha256)
                   ORDER BY b.sha256 LIMIT ?""",
                (after, page[-1], batch_size),
            ).fetchall()
        ]
        connection.executemany(
            "DELETE FROM workflow_execution_payload_blobs WHERE sha256=?",
            [(key,) for key in keys],
        )
        # At the deletion limit, the rest of the range may still have orphans.
        # Otherwise advance past retained blobs even when nothing was deleted.
        progress["blob_cursor"] = keys[-1] if len(keys) == batch_size else page[-1]
        complete = progress["blob_cursor"] >= high_water
    if complete:
        progress.pop("blob_cursor", None)
        progress.pop("blob_high_water", None)
    return len(keys), complete


def _run_orchestration_retention_slice(
    path: Path | str,
    *,
    retention_days: int,
    compact_history_retention_days: int,
    batch_size: int = 100,
    index_batch_size: int = 10000,
    time_budget_seconds: float = 0.25,
    busy_timeout_ms: int = MAINTENANCE_TIMEOUT_MS,
    batch_size_limit: int = 100,
    batch_limits_json: str | None = None,
) -> OrchestrationRetentionResult:
    """Commit at most one bounded retention batch and durably retain its phase.

    A restarted worker resumes the same sweep cutoff. Eligibility remains the
    existing policy; missing legacy FK indexes defer deletion rather than scan
    large child tables while holding the only writer lock.
    """
    started = monotonic()
    now = datetime.now(UTC)
    today = now.date().isoformat()
    counts: dict[str, Any] = {}
    phases = ("sources", "workflow", "compact", "blobs", "internal", "scheduled")
    with connect_orchestration_db_read_only(
        path, deadline_seconds=time_budget_seconds, operation="retention_progress"
    ) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_progress'"
        ).fetchone()
        expected_progress = row[0] if row else None
    progress = json_loads_object(expected_progress) if expected_progress else {}
    if not progress:
        progress = {
            "date": today,
            "phase": "sources",
            "cutoff": datetime_to_storage(now - timedelta(days=retention_days)),
            "compact_cutoff": datetime_to_storage(
                now - timedelta(days=compact_history_retention_days)
            ),
        }
    phase = progress["phase"]
    prepared_sources = None
    if phase == "sources":
        from evander.repositories.execution_history import prepare_records_for_retention

        try:
            prepared_sources = prepare_records_for_retention(
                path, cutoff=progress["cutoff"], batch_size=batch_size
            )
        except MaintenanceIndexesRequired:
            return OrchestrationRetentionResult(
                remaining=True, skipped_reason="maintenance_indexes_required"
            )
    with connect_orchestration_db(
        path,
        busy_timeout_ms=busy_timeout_ms,
        deadline_seconds=time_budget_seconds,
        operation="retention_batch",
    ) as connection:
        if phase in {"compact", "blobs", "internal"} and not _maintenance_indexes_ready(connection):
            return OrchestrationRetentionResult(
                remaining=True, skipped_reason="maintenance_indexes_required"
            )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='retention_progress'"
        ).fetchone()
        if (row[0] if row else None) != expected_progress:
            return OrchestrationRetentionResult(remaining=True)
        deleted = 0
        waiting_for_history = False
        if phase == "sources":
            from evander.repositories.execution_history import archive_records_for_retention

            sources = archive_records_for_retention(
                connection,
                cutoff=progress["cutoff"],
                batch_size=batch_size,
                prepared=prepared_sources,
            )
            by_kind = _delete_archived_execution_sources(connection, sources)
            counts.update(
                trigger_events_deleted=by_kind["trigger"] + by_kind["transport"],
                effect_executions_deleted=by_kind["effect"],
                gate_executions_deleted=by_kind["gate"],
            )
            deleted = len(sources)
            waiting_for_history = _has_pending_retention_sources(connection, progress["cutoff"])
        elif phase == "blobs":
            deleted, blobs_complete = _delete_history_payload_batch(
                connection, progress, batch_size=batch_size,
            )
            counts["compact_payload_blobs_deleted"] = deleted
        else:
            definitions = {
                "workflow": (
                    "workflow_events",
                    "row_id",
                    "occurred_at < ?",
                    "workflow_events_deleted",
                    "idx_workflow_events_occurred",
                    "occurred_at,row_id",
                ),
                "compact": (
                    "workflow_node_execution_history",
                    "execution_key",
                    """finished_at < ?
                    AND NOT EXISTS (SELECT 1 FROM workflow_history_index_jobs j
                        WHERE j.execution_key=workflow_node_execution_history.execution_key)""",
                    "compact_executions_deleted",
                    "idx_workflow_node_history_finished",
                    "finished_at,execution_key",
                ),
                "scheduled": (
                    "scheduled_internal_events",
                    "schedule_id",
                    "status='published' AND published_at < ?",
                    "scheduled_internal_events_deleted",
                    "idx_scheduled_internal_events_published",
                    "published_at,schedule_id",
                ),
                "internal": (
                    "internal_events",
                    "row_id",
                    """created_at < ?
                    AND NOT EXISTS (SELECT 1 FROM internal_event_deliveries d
                        WHERE d.internal_event_row_id=internal_events.row_id AND d.state='pending')
                    AND NOT EXISTS (SELECT 1 FROM internal_event_subscriptions s
                        WHERE s.active=1 AND s.event_type=internal_events.event_type
                          AND s.scan_cursor_row_id < internal_events.row_id)""",
                    "internal_events_deleted",
                    "idx_internal_events_created",
                    "created_at,row_id",
                ),
            }
            table, key, where, result_key, index, order = definitions[phase]
            cutoff = progress["compact_cutoff" if phase == "compact" else "cutoff"]
            keys = [
                row[0]
                for row in connection.execute(
                    f"SELECT {key} FROM {table} INDEXED BY {index} "
                    f"WHERE {where} ORDER BY {order} LIMIT ?",
                    (cutoff, batch_size),
                ).fetchall()
            ]
            if phase == "compact":
                deleted, index_deleted = _delete_compact_history_batch(
                    connection, keys, index_batch_size=index_batch_size,
                )
                counts["compact_index_rows_deleted"] = index_deleted
            else:
                connection.executemany(f"DELETE FROM {table} WHERE {key}=?", [(key,) for key in keys])
                deleted = len(keys)
            counts[result_key] = deleted
        # A byte-limited batch may contain fewer rows than the count limit.
        # Advance only after an empty source snapshot, including after CAS skips.
        phase_complete = (
            not prepared_sources.sources if phase == "sources"
            else blobs_complete if phase == "blobs"
            else not keys if phase == "compact" else deleted < batch_size
        )
        next_index = phases.index(phase) + (phase_complete and not waiting_for_history)
        remaining = next_index < len(phases)
        if remaining:
            progress["phase"] = phases[next_index]
            connection.execute(
                """INSERT INTO orchestration_metadata(key,value_json) VALUES ('retention_progress',?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (json_dumps(progress),),
            )
        else:
            connection.execute("DELETE FROM orchestration_metadata WHERE key='retention_progress'")
            connection.execute(
                """INSERT INTO orchestration_metadata(key,value_json) VALUES ('last_retention_date',?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (json_dumps({"date": progress["date"]}),),
            )
        # Recover only limits fully exercised with time to spare. Empty or
        # deferred phases do not establish that a larger batch is affordable.
        if (
            batch_limits_json is not None
            and not waiting_for_history
            and monotonic() - started < time_budget_seconds / 2
        ):
            limits = json_loads_object(batch_limits_json)
            recovered = dict(limits)
            records_deleted = sum(
                count for name, count in counts.items() if name != "compact_index_rows_deleted"
            )
            if records_deleted >= batch_size and batch_size < batch_size_limit:
                recovered["records"] = min(batch_size_limit, batch_size * 2)
            if (
                counts.get("compact_index_rows_deleted", 0) >= index_batch_size
                and index_batch_size < 10000
            ):
                recovered["index_rows"] = min(10000, index_batch_size * 2)
            if recovered != limits:
                # Commit recovery with the cleanup it measured; never overwrite
                # a reduction persisted by another worker since the initial read.
                connection.execute(
                    """UPDATE orchestration_metadata SET value_json=?
                       WHERE key='retention_batch_limits' AND value_json=?""",
                    (json_dumps(recovered), batch_limits_json),
                )
    return OrchestrationRetentionResult(
        **counts,
        remaining=remaining,
        skipped_reason="history_index_pending" if waiting_for_history else None,
    )


def _has_pending_retention_sources(connection: sqlite3.Connection, cutoff: str) -> bool:
    # Drive primary-key lookups from queued jobs while retention holds the writer lock.
    return (
        connection.execute(
            """SELECT 1 FROM workflow_history_index_jobs j
           WHERE (j.execution_key GLOB 'trigger:*' AND EXISTS (
               SELECT 1 FROM trigger_events t
               WHERE t.event_id=substr(j.execution_key,9)
                 AND t.delivery_state='delivered'
                 AND COALESCE(t.delivered_at,t.occurred_at) < ?
           )) OR (j.execution_key GLOB 'effect:*' AND EXISTS (
               SELECT 1 FROM effect_executions e
               WHERE e.execution_id=substr(j.execution_key,8)
                 AND ((e.status IN ('succeeded','skipped','dropped')
                       AND COALESCE(e.finished_at,e.started_at) < ?)
                   OR (e.status='failed'
                       AND (e.dismissed_at IS NOT NULL OR e.cleared_at IS NOT NULL)
                       AND COALESCE(e.dismissed_at,e.cleared_at,e.finished_at,e.started_at) < ?))
           )) OR (j.execution_key GLOB 'gate:*' AND EXISTS (
               SELECT 1 FROM gate_executions g
               WHERE g.execution_id=substr(j.execution_key,6)
                 AND ((g.status IN ('succeeded','skipped','dropped')
                       AND COALESCE(g.finished_at,g.started_at) < ?)
                   OR (g.status='failed'
                       AND (g.dismissed_at IS NOT NULL OR g.cleared_at IS NOT NULL)
                       AND COALESCE(g.dismissed_at,g.cleared_at,g.finished_at,g.started_at) < ?))
           )) LIMIT 1""",
            (cutoff, cutoff, cutoff, cutoff, cutoff),
        ).fetchone()
        is not None
    )


def run_orchestration_retention(
    path: Path | str,
    *,
    retention_days: int,
    compact_history_retention_days: int,
    execution_history_batch_size: int = 250,
) -> OrchestrationRetentionResult:
    """Explicit maintenance operation; foreground constructors never call it."""
    ensure_orchestration_schema(path)
    totals = {
        name: 0
        for name in OrchestrationRetentionResult.__dataclass_fields__
        if name not in {"remaining", "skipped_reason"}
    }
    while True:
        result = run_orchestration_retention_slice(
            path,
            retention_days=retention_days,
            compact_history_retention_days=compact_history_retention_days,
            batch_size=execution_history_batch_size,
        )
        for name in totals:
            totals[name] += getattr(result, name)
        if result.skipped_reason == "history_index_pending":
            from evander.repositories.execution_history import (
                run_history_indexing_slice,
            )

            run_history_indexing_slice(
                path,
                busy_timeout_ms=100,
                writer_time_budget_seconds=0.25,
            )
            continue
        if not result.remaining or result.skipped_reason:
            return OrchestrationRetentionResult(
                **totals,
                remaining=result.remaining,
                skipped_reason=result.skipped_reason,
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
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
