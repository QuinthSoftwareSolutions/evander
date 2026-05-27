from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from pydantic import BaseModel, Field

from agent_sandbox.repositories.orchestration_db import datetime_to_storage, json_dumps
from agent_sandbox.worker.models import now_utc
from agent_sandbox.worker.security import sanitize_metadata

SQLITE_BUSY_TIMEOUT_MS = 30_000


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


class ContainerWorkerExecutionRepository:
    def __init__(self, path: Path) -> None:
        original_path = Path(path)
        if original_path.suffix == ".json":
            self.path = original_path.with_name("container-worker-executions.db")
            self.legacy_json_path = original_path
        else:
            self.path = original_path
            self.legacy_json_path = None
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
        payload = record.model_dump(mode="json")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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

    def read_all(self) -> dict[str, ContainerWorkerExecutionRecord]:
        return {
            record.execution_id: record
            for record in self.list_all_ordered()
        }

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
            self._create_schema()
            self._import_legacy_json_if_empty()
            self._initialized = True

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
                """
            )

    def _import_legacy_json_if_empty(self) -> None:
        legacy_json_path = self.legacy_json_path
        if legacy_json_path is None or not legacy_json_path.exists():
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._row_count(connection) > 0:
                return
            raw = json.loads(legacy_json_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(raw, dict):
                raise ValueError(
                    f"legacy container worker execution store {legacy_json_path} must contain a JSON object"
                )
            for execution_id, value in raw.items():
                if not isinstance(value, dict):
                    continue
                payload = dict(value)
                payload["execution_id"] = str(execution_id)
                record = ContainerWorkerExecutionRecord.model_validate(payload)
                self._insert_record(connection, record)

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

    def _record_from_row(
        self, row: sqlite3.Row | None
    ) -> ContainerWorkerExecutionRecord | None:
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
