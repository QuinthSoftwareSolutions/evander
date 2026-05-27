from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from agent_sandbox.repositories.orchestration_db import (
    datetime_to_storage,
    json_dumps,
    json_loads_object,
)
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
    def __init__(
        self, path: Path, *, legacy_json_path: Path | None = None
    ) -> None:
        raw_path = Path(path)
        if raw_path.suffix == ".json":
            self.path = raw_path.with_name("container-worker-executions.db")
            self.legacy_json_path = raw_path
        else:
            self.path = raw_path
            self.legacy_json_path = Path(legacy_json_path) if legacy_json_path is not None else None
        self._lock = RLock()
        self._initialized = False

    def get(self, execution_id: str) -> ContainerWorkerExecutionRecord | None:
        if execution_id is None:
            return None
        normalized_execution_id = str(execution_id)
        self._ensure_ready()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM container_worker_executions
                WHERE execution_id = ?
                """,
                (normalized_execution_id,),
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def put(self, record: ContainerWorkerExecutionRecord) -> None:
        self._ensure_ready()
        record.metadata = sanitize_metadata(record.metadata)
        record.updated_at = now_utc()
        with self._lock, self._connect() as connection:
            self._upsert_record(connection, record)

    def delete(self, execution_id: str) -> None:
        if execution_id is None:
            return
        normalized_execution_id = str(execution_id)
        self._ensure_ready()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                DELETE FROM container_worker_executions
                WHERE execution_id = ?
                """,
                (normalized_execution_id,),
            )

    def read_all(self) -> dict[str, ContainerWorkerExecutionRecord]:
        return {
            record.execution_id: record
            for record in self.list_all_ordered()
        }

    def list_by_session(self, session_id: str) -> list[ContainerWorkerExecutionRecord]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return []
        return self._list_records(
            """
            SELECT payload_json
            FROM container_worker_executions
            WHERE session_id = ?
            ORDER BY created_at ASC, execution_id ASC
            """,
            (normalized_session_id,),
        )

    def list_active_by_session(
        self, session_id: str, active_statuses: Iterable[str]
    ) -> list[ContainerWorkerExecutionRecord]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return []
        statuses = self._normalize_statuses(active_statuses)
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        return self._list_records(
            f"""
            SELECT payload_json
            FROM container_worker_executions
            WHERE session_id = ?
              AND last_status IN ({placeholders})
            ORDER BY created_at ASC, execution_id ASC
            """,
            (normalized_session_id, *statuses),
        )

    def list_by_directory(
        self, directory_path: str | None = None
    ) -> list[ContainerWorkerExecutionRecord]:
        if directory_path is None:
            return self.list_all_ordered()
        normalized_directory_path = str(directory_path or "").strip()
        if not normalized_directory_path:
            return self.list_all_ordered()
        return self._list_records(
            """
            SELECT payload_json
            FROM container_worker_executions
            WHERE directory_path = ?
            ORDER BY created_at ASC, execution_id ASC
            """,
            (normalized_directory_path,),
        )

    def list_all_ordered(self) -> list[ContainerWorkerExecutionRecord]:
        return self._list_records(
            """
            SELECT payload_json
            FROM container_worker_executions
            ORDER BY created_at ASC, execution_id ASC
            """
        )

    def resolve(self, target: str) -> ContainerWorkerExecutionRecord:
        normalized = str(target or "").strip()
        if not normalized:
            raise ValueError("container worker execution target is required")
        records = self.list_all_ordered()
        for record in records:
            if normalized in {
                record.execution_id,
                record.worker_job_id,
                record.target_name,
            }:
                return record
        session_matches = [record for record in records if record.session_id == normalized]
        if len(session_matches) == 1:
            return session_matches[0]
        if len(session_matches) > 1:
            matches = ", ".join(sorted(record.execution_id for record in session_matches))
            raise ValueError(f"Ambiguous container worker session target {normalized}: {matches}")
        raise ValueError(f"Unknown container worker execution target: {normalized}")

    def ensure_schema(self) -> None:
        self._ensure_ready()

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._initialized:
                return
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
            self._import_legacy_json_if_empty()
            self._initialized = True

    def _import_legacy_json_if_empty(self) -> None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT 1
                FROM container_worker_executions
                LIMIT 1
                """
            ).fetchone()
            if row is not None:
                return
            raw = json.loads(self.legacy_json_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(raw, dict):
                raise ValueError(
                    f"legacy container worker execution store {self.legacy_json_path} must contain a JSON object"
                )
            for execution_id, value in raw.items():
                record = ContainerWorkerExecutionRecord.model_validate(value)
                if record.execution_id != execution_id:
                    record = record.model_copy(update={"execution_id": execution_id})
                self._upsert_record(connection, record)

    def _list_records(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[ContainerWorkerExecutionRecord]:
        self._ensure_ready()
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def _normalize_statuses(active_statuses: Iterable[str]) -> tuple[str, ...]:
        statuses = tuple(
            sorted(
                {
                    str(status or "").strip()
                    for status in active_statuses
                    if str(status or "").strip()
                }
            )
        )
        return statuses

    @staticmethod
    def _record_from_row(row: Any) -> ContainerWorkerExecutionRecord:
        return ContainerWorkerExecutionRecord.model_validate(
            json_loads_object(row["payload_json"])
        )

    @staticmethod
    def _upsert_record(
        connection: sqlite3.Connection, record: ContainerWorkerExecutionRecord
    ) -> None:
        payload = record.model_dump(mode="json")
        connection.execute(
            """
            INSERT INTO container_worker_executions(
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
            ON CONFLICT(execution_id) DO UPDATE SET
                session_id = excluded.session_id,
                worker_job_id = excluded.worker_job_id,
                target_name = excluded.target_name,
                last_status = excluded.last_status,
                cleanup_status = excluded.cleanup_status,
                directory_path = excluded.directory_path,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                finished_at = excluded.finished_at,
                payload_json = excluded.payload_json
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
