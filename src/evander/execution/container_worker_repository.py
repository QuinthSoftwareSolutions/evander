from __future__ import annotations

import fcntl
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager
from threading import RLock
from types import SimpleNamespace
from typing import Any, Iterable

from pydantic import BaseModel, Field

from evander.repositories.orchestration_db import datetime_to_storage, json_dumps
from evander.worker.models import now_utc
from evander.worker.security import sanitize_metadata

SQLITE_BUSY_TIMEOUT_MS = 30_000
_MODEL_USAGE_PROGRESS_FIELDS = ("model_usage_cursor", "model_usage_complete", "model_usage_sync_error")


class ContainerWorkerExecutionRecord(BaseModel):
    execution_id: str
    session_id: str
    worker_job_id: str
    worker_url: str
    target_name: str
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_status: str = "unknown"
    cleanup_status: str = "unknown"
    cleanup_attempted_at: datetime | None = None
    cleanup_completed_at: datetime | None = None
    cleanup_error: str | None = None
    directory_path: str = "root"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContainerWorkerSessionExecutionSummary(BaseModel):
    """The inexpensive inventory fields for one container-worker session."""

    session_id: str
    execution_count: int = 0
    active_execution_count: int = 0
    active_running_count: int = 0
    latest_execution: ContainerWorkerExecutionRecord | None = None


class ContainerWorkerExecutionRepository:
    def __init__(self, path: Path) -> None:
        original_path = Path(path)
        if original_path.suffix == ".json":
            self.path = original_path.with_name("container-worker-executions.db")
        else:
            self.path = original_path
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock = RLock()
        self._initialized = False
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, execution_id: str) -> ContainerWorkerExecutionRecord | None:
        normalized = self._normalize_key(execution_id)
        if normalized is None:
            return None
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM container_worker_executions
                WHERE execution_id = ?
                """,
                (normalized,),
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def put(self, record: ContainerWorkerExecutionRecord) -> None:
        record.metadata = sanitize_metadata(record.metadata)
        record.updated_at = now_utc()
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current_execution(connection, record.execution_id)
            if current is not None and self._same_worker_job(current, record):
                # Foreground inspection/cleanup may have started before a receipt
                # delivery advanced its cursor or sealed the export.
                if self._newer_progress(current.metadata, record.metadata):
                    self._copy_model_usage_progress(current.metadata, record.metadata)
            self._insert_record(connection, record)
            self._update_delivery_queue(connection, record)

    def save_model_usage_progress(self, record: ContainerWorkerExecutionRecord) -> None:
        """Merge only receipt progress; never restore a deleted or replaced job."""
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current_execution(connection, record.execution_id)
            if current is None or not self._same_worker_job(current, record):
                return
            if not self._newer_progress(current.metadata, record.metadata):
                self._copy_model_usage_progress(record.metadata, current.metadata)
                current.updated_at = now_utc()
                self._insert_record(connection, current)
                self._update_delivery_queue(connection, current)
            self._copy_model_usage_progress(current.metadata, record.metadata)

    def _current_execution(
        self, connection: sqlite3.Connection, execution_id: str,
    ) -> ContainerWorkerExecutionRecord | None:
        return self._record_from_row(connection.execute(
            "SELECT payload_json FROM container_worker_executions WHERE execution_id=?",
            (execution_id,),
        ).fetchone())

    @staticmethod
    def _same_worker_job(
        first: ContainerWorkerExecutionRecord, second: ContainerWorkerExecutionRecord,
    ) -> bool:
        return (
            first.worker_job_id == second.worker_job_id
            and first.worker_url.rstrip("/") == second.worker_url.rstrip("/")
            and first.session_id == second.session_id
        )

    @staticmethod
    def _newer_progress(current: dict, incoming: dict) -> bool:
        return bool(current.get("model_usage_complete")) or (
            int(current.get("model_usage_cursor", 0)) > int(incoming.get("model_usage_cursor", 0))
        )

    @staticmethod
    def _copy_model_usage_progress(source: dict, target: dict) -> None:
        for field in _MODEL_USAGE_PROGRESS_FIELDS:
            target.pop(field, None)
            if field in source:
                target[field] = source[field]

    @staticmethod
    def _update_delivery_queue(
        connection: sqlite3.Connection, record: ContainerWorkerExecutionRecord,
    ) -> None:
        if record.metadata.get("model_usage_capture") == 1:
            if record.metadata.get("model_usage_complete"):
                connection.execute(
                    "DELETE FROM pending_model_usage_deliveries WHERE execution_id=?",
                    (record.execution_id,),
                )
            else:
                connection.execute(
                    "INSERT OR IGNORE INTO pending_model_usage_deliveries "
                    "(execution_id,next_attempt_at) VALUES (?,?)",
                    (record.execution_id, datetime_to_storage(record.updated_at)),
                )

    def claim_model_usage_delivery(
        self, *, now: datetime | None = None, retry_seconds: float = 30,
    ) -> ContainerWorkerExecutionRecord | None:
        """Lease one prospective delivery, including terminal executions.

        The queue starts empty. Only newly captured jobs enter it through put();
        there is no startup scan or historical job conversion.
        """
        now = now or now_utc()
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT e.payload_json, q.execution_id FROM pending_model_usage_deliveries q "
                "JOIN container_worker_executions e ON e.execution_id=q.execution_id "
                "WHERE q.next_attempt_at<=? ORDER BY q.next_attempt_at,q.execution_id LIMIT 1",
                (datetime_to_storage(now),),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE pending_model_usage_deliveries SET next_attempt_at=? WHERE execution_id=?",
                (datetime_to_storage(now + timedelta(seconds=max(1, retry_seconds))), row["execution_id"]),
            )
            return self._record_from_row(row)

    def delete(self, execution_id: str) -> None:
        normalized = self._normalize_key(execution_id)
        if normalized is None:
            return
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM container_worker_executions WHERE execution_id = ?",
                (normalized,),
            )
            connection.execute(
                "DELETE FROM pending_model_usage_deliveries WHERE execution_id=?", (normalized,),
            )

    def read_all(self) -> dict[str, ContainerWorkerExecutionRecord]:
        return {record.execution_id: record for record in self.list_all_ordered()}

    def list_all_ordered(self) -> list[ContainerWorkerExecutionRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM container_worker_executions
                ORDER BY created_at ASC, execution_id ASC
                """
            ).fetchall()
        return [record for row in rows if (record := self._record_from_row(row)) is not None]

    def list_by_session(self, session_id: str) -> list[ContainerWorkerExecutionRecord]:
        normalized = self._normalize_key(session_id)
        if normalized is None:
            return []
        return self._list_records(
            """
            SELECT payload_json
            FROM container_worker_executions
            WHERE session_id = ?
            ORDER BY created_at ASC, execution_id ASC
            """,
            (normalized,),
        )

    def list_lifecycle_records(self, session_ids: Iterable[str]) -> dict[str, list[Any]]:
        """Batch only the fields used for session lifecycle; omit execution payloads.

        Historic cleanup metadata can still affect the current lifecycle, so it
        must be included even when it belongs to an older execution.
        """
        ids = list(dict.fromkeys(str(value) for value in session_ids))
        result: dict[str, list[Any]] = {value: [] for value in ids}
        if not ids:
            return result
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN")
            for start in range(0, len(ids), 400):
                batch = ids[start : start + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT execution_id, session_id, last_status, created_at, updated_at,
                           finished_at, json_extract(payload_json, '$.started_at') AS started_at,
                           json_extract(
                               payload_json, '$.metadata.container_lifecycle', '$.metadata.state',
                               '$.metadata.cleanup_status', '$.metadata.cleanup_attempted_at',
                               '$.metadata.cleanup_completed_at', '$.metadata.cleanup_error'
                           ) AS lifecycle_fields
                    FROM container_worker_executions
                    WHERE session_id IN ({placeholders})
                    """,
                    batch,
                )
                for row in rows:
                    fields = dict(row)
                    for name in ("created_at", "updated_at", "started_at", "finished_at"):
                        value = fields[name]
                        fields[name] = datetime.fromisoformat(value) if value else None
                    # Multiple paths return a JSON array, retaining booleans
                    # and other scalar types that single-path extraction would
                    # coerce to SQLite integers/text before Python sees them.
                    fields["metadata"] = dict(
                        zip(
                            (
                                "container_lifecycle",
                                "state",
                                "cleanup_status",
                                "cleanup_attempted_at",
                                "cleanup_completed_at",
                                "cleanup_error",
                            ),
                            json.loads(fields.pop("lifecycle_fields")),
                        )
                    )
                    result[row["session_id"]].append(SimpleNamespace(**fields))
        return result

    def get_session_summary(self, session_id: str) -> ContainerWorkerSessionExecutionSummary:
        normalized = self._normalize_key(session_id)
        if normalized is None:
            return ContainerWorkerSessionExecutionSummary(session_id="")
        summaries = self._list_session_summaries("WHERE session_id = ?", (normalized,))
        return (
            summaries[0]
            if summaries
            else ContainerWorkerSessionExecutionSummary(session_id=normalized)
        )

    def list_session_summaries(
        self, directory_path: str | None = None
    ) -> list[ContainerWorkerSessionExecutionSummary]:
        if directory_path is None:
            return self._list_session_summaries("", ())
        normalized = self._normalize_key(directory_path)
        if normalized is None:
            return []
        return self._list_session_summaries("WHERE directory_path = ?", (normalized,))

    def list_page_by_session(
        self,
        session_id: str,
        *,
        limit: int,
        before_created_at: str | None = None,
        before_execution_id: str | None = None,
    ) -> list[ContainerWorkerExecutionRecord]:
        normalized = self._normalize_key(session_id)
        if normalized is None:
            return []
        normalized_limit = max(1, int(limit))
        where_clause = "WHERE session_id = ?"
        parameters: tuple[Any, ...] = (normalized,)
        if before_created_at is not None and before_execution_id is not None:
            where_clause += " AND (created_at < ? OR (created_at = ? AND execution_id < ?))"
            parameters += (before_created_at, before_created_at, before_execution_id)
        return self._list_records(
            f"""
            SELECT payload_json
            FROM container_worker_executions
            {where_clause}
            ORDER BY created_at DESC, execution_id DESC
            LIMIT ?
            """,
            (*parameters, normalized_limit),
        )

    def list_active_by_session(
        self, session_id: str, active_statuses: Iterable[str] | None
    ) -> list[ContainerWorkerExecutionRecord]:
        normalized_session_id = self._normalize_key(session_id)
        if normalized_session_id is None:
            return []
        statuses = self._normalized_statuses(active_statuses)
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        return self._list_records(
            f"""
            SELECT payload_json
            FROM container_worker_executions
            WHERE session_id = ? AND last_status IN ({placeholders})
            ORDER BY created_at ASC, execution_id ASC
            """,
            (normalized_session_id, *statuses),
        )

    def list_by_directory(
        self, directory_path: str | None = None
    ) -> list[ContainerWorkerExecutionRecord]:
        if directory_path is None:
            return self.list_all_ordered()
        normalized = self._normalize_key(directory_path)
        if normalized is None:
            return []
        return self._list_records(
            """
            SELECT payload_json
            FROM container_worker_executions
            WHERE directory_path = ?
            ORDER BY created_at ASC, execution_id ASC
            """,
            (normalized,),
        )

    def resolve(self, target: str) -> ContainerWorkerExecutionRecord:
        normalized = self._normalize_key(target)
        if normalized is None:
            raise ValueError("container worker execution target is required")

        record = self.get(normalized)
        if record is not None:
            return record

        for column in ("worker_job_id", "target_name"):
            record = self._first_by_exact_column(column, normalized)
            if record is not None:
                return record

        session_records = self.list_by_session(normalized)
        if len(session_records) == 1:
            return session_records[0]
        if len(session_records) > 1:
            matches = ", ".join(sorted(record.execution_id for record in session_records))
            raise ValueError(f"Ambiguous container worker session target {normalized}: {matches}")

        raise ValueError(f"Unknown container worker execution target: {normalized}")

    def ensure_schema(self) -> None:
        self._ensure_ready()

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._initialized:
                return
            with self._initialization_lock():
                self._create_schema()
            self._initialized = True

    @contextmanager
    def _initialization_lock(self) -> Any:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS container_worker_executions (
                    execution_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    worker_job_id TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    last_status TEXT NOT NULL,
                    cleanup_status TEXT NOT NULL,
                    directory_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_container_worker_executions_session_status_updated
                    ON container_worker_executions(session_id, last_status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_container_worker_executions_session_created_execution
                    ON container_worker_executions(session_id, created_at, execution_id);
                CREATE INDEX IF NOT EXISTS idx_container_worker_executions_worker_job_id
                    ON container_worker_executions(worker_job_id);
                CREATE INDEX IF NOT EXISTS idx_container_worker_executions_target_name
                    ON container_worker_executions(target_name);
                CREATE INDEX IF NOT EXISTS idx_container_worker_executions_directory_created
                    ON container_worker_executions(directory_path, created_at);

                CREATE TABLE IF NOT EXISTS pending_model_usage_deliveries (
                    execution_id TEXT PRIMARY KEY,
                    next_attempt_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pending_model_usage_deliveries_due
                    ON pending_model_usage_deliveries(next_attempt_at, execution_id);
                """
            )

    def _insert_record(
        self, connection: sqlite3.Connection, record: ContainerWorkerExecutionRecord
    ) -> None:
        payload = record.model_dump(mode="json")
        connection.execute(
            """
            INSERT OR REPLACE INTO container_worker_executions(
                execution_id,
                session_id,
                worker_job_id,
                target_name,
                last_status,
                cleanup_status,
                directory_path,
                created_at,
                updated_at,
                finished_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.execution_id,
                record.session_id,
                record.worker_job_id,
                record.target_name,
                record.last_status,
                record.cleanup_status,
                record.directory_path,
                datetime_to_storage(record.created_at),
                datetime_to_storage(record.updated_at),
                datetime_to_storage(record.finished_at),
                json_dumps(payload),
            ),
        )

    def _record_from_row(self, row: sqlite3.Row | None) -> ContainerWorkerExecutionRecord | None:
        if row is None:
            return None
        payload = row["payload_json"]
        if payload is None:
            return None
        return ContainerWorkerExecutionRecord.model_validate_json(payload)

    def _list_records(
        self, sql: str, parameters: tuple[Any, ...]
    ) -> list[ContainerWorkerExecutionRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [record for row in rows if (record := self._record_from_row(row)) is not None]

    def _list_session_summaries(
        self, where_clause: str, parameters: tuple[Any, ...]
    ) -> list[ContainerWorkerSessionExecutionSummary]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                WITH scoped AS (
                    SELECT
                        session_id,
                        last_status,
                        created_at,
                        execution_id,
                        payload_json,
                        ROW_NUMBER() OVER (
                            PARTITION BY session_id
                            ORDER BY created_at DESC, execution_id DESC
                        ) AS sequence_number,
                        COUNT(*) OVER (PARTITION BY session_id) AS execution_count,
                        SUM(
                            CASE WHEN last_status IN ('created', 'starting', 'running')
                            THEN 1 ELSE 0 END
                        ) OVER (PARTITION BY session_id) AS active_execution_count,
                        SUM(CASE WHEN last_status = 'running' THEN 1 ELSE 0 END)
                            OVER (PARTITION BY session_id) AS active_running_count
                    FROM container_worker_executions
                    {where_clause}
                )
                SELECT session_id, execution_count, active_execution_count,
                       active_running_count, payload_json
                FROM scoped
                WHERE sequence_number = 1
                ORDER BY session_id ASC
                """,
                parameters,
            ).fetchall()
        summaries: list[ContainerWorkerSessionExecutionSummary] = []
        for row in rows:
            latest = self._record_from_row(row)
            if latest is None:
                continue
            summaries.append(
                ContainerWorkerSessionExecutionSummary(
                    session_id=str(row["session_id"]),
                    execution_count=int(row["execution_count"] or 0),
                    active_execution_count=int(row["active_execution_count"] or 0),
                    active_running_count=int(row["active_running_count"] or 0),
                    latest_execution=latest,
                )
            )
        return summaries

    def _first_by_exact_column(
        self, column: str, value: str
    ) -> ContainerWorkerExecutionRecord | None:
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM container_worker_executions
                WHERE {column} = ?
                ORDER BY created_at ASC, execution_id ASC
                LIMIT 1
                """,
                (value,),
            ).fetchone()
        return self._record_from_row(row)

    def _row_count(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM container_worker_executions"
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    @staticmethod
    def _normalize_key(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _normalized_statuses(statuses: Iterable[str] | None) -> list[str]:
        if statuses is None:
            return []
        normalized = [str(status or "").strip() for status in statuses]
        return [status for status in dict.fromkeys(normalized) if status]

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection
