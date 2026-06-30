from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any

from .models import WorkerJobRecord

SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0
_SQLITE_WORKER_JOB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_TERMINAL_STATUSES = {"removed", "stopped", "failed", "exited", "cleanup_failed"}


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _worker_jobs_db_path(path: Path) -> Path:
    if path.suffix.lower() in _SQLITE_WORKER_JOB_SUFFIXES:
        return path
    return path.with_suffix(".db")


def _read_legacy_worker_jobs_json(path: Path) -> dict[str, WorkerJobRecord]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"Worker job JSON store at {path} must contain an object at the top level")
    return {key: WorkerJobRecord.model_validate(value) for key, value in raw.items()}


class WorkerJobStore:
    def __init__(self, path: Path) -> None:
        self._lock = RLock()
        self._legacy_json_path = (
            path if path.suffix.lower() not in _SQLITE_WORKER_JOB_SUFFIXES else None
        )
        self.path = _worker_jobs_db_path(path)
        self.ensure_schema()
        self._import_legacy_json_if_empty()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        self._enable_wal(connection)
        return connection

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + _SQLITE_WAL_ENABLE_TIMEOUT_SECONDS
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    @staticmethod
    def _record_row(
        record: WorkerJobRecord,
    ) -> tuple[str, str, str, str, str, str | None, str | None, str]:
        payload = record.model_dump(mode="json")
        return (
            record.job_id,
            record.session_id,
            record.container_name,
            record.status,
            record.cleanup_status,
            payload["started_at"],
            payload["finished_at"],
            _json_dumps(payload),
        )

    @staticmethod
    def _ordered_records_query(
        *,
        session_id: str | None = None,
        active_only: bool = False,
    ) -> tuple[str, tuple[Any, ...]]:
        query = [
            "SELECT job_id, payload_json",
            "FROM worker_jobs",
        ]
        params: list[Any] = []
        if session_id is not None:
            query.append("WHERE session_id = ?")
            params.append(session_id)
            if active_only:
                placeholders = ", ".join("?" for _ in sorted(_TERMINAL_STATUSES))
                query[-1] += f" AND status NOT IN ({placeholders})"
                params.extend(sorted(_TERMINAL_STATUSES))
        elif active_only:
            placeholders = ", ".join("?" for _ in sorted(_TERMINAL_STATUSES))
            query.append(f"WHERE status NOT IN ({placeholders})")
            params.extend(sorted(_TERMINAL_STATUSES))
        query.append("ORDER BY started_at IS NULL, started_at, job_id")
        return " ".join(query), tuple(params)

    def ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS worker_jobs (
                        job_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        container_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        cleanup_status TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_session_status_started_job
                        ON worker_jobs(session_id, status, started_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_session_started_job
                        ON worker_jobs(session_id, started_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_status_started_job
                        ON worker_jobs(status, started_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_cleanup_status_started_job
                        ON worker_jobs(cleanup_status, started_at, job_id);
                    """
                )

    def get(self, job_id: str) -> WorkerJobRecord | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM worker_jobs
                    WHERE job_id = ?
                    """,
                    (normalized_job_id,),
                ).fetchone()
        if row is None:
            return None
        return WorkerJobRecord.model_validate_json(row["payload_json"])

    def put(self, record: WorkerJobRecord) -> None:
        validated = WorkerJobRecord.model_validate(record)
        row = self._record_row(validated)
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO worker_jobs(
                        job_id,
                        session_id,
                        container_name,
                        status,
                        cleanup_status,
                        started_at,
                        finished_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        container_name = excluded.container_name,
                        status = excluded.status,
                        cleanup_status = excluded.cleanup_status,
                        started_at = excluded.started_at,
                        finished_at = excluded.finished_at,
                        payload_json = excluded.payload_json
                    """,
                    row,
                )

    def delete(self, job_id: str) -> None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    DELETE FROM worker_jobs
                    WHERE job_id = ?
                    """,
                    (normalized_job_id,),
                )

    def read_all(self) -> dict[str, WorkerJobRecord]:
        return {record.job_id: record for record in self.list_all_ordered()}

    def list_by_session(self, session_id: str) -> list[WorkerJobRecord]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return []
        query, params = self._ordered_records_query(session_id=normalized_session_id)
        return self._list_records(query, params)

    def list_active_by_session(self, session_id: str) -> list[WorkerJobRecord]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return []
        query, params = self._ordered_records_query(
            session_id=normalized_session_id,
            active_only=True,
        )
        return self._list_records(query, params)

    def list_all_ordered(self) -> list[WorkerJobRecord]:
        query, params = self._ordered_records_query()
        return self._list_records(query, params)

    def _list_records(self, query: str, params: tuple[Any, ...]) -> list[WorkerJobRecord]:
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(query, params).fetchall()
        return [WorkerJobRecord.model_validate_json(row["payload_json"]) for row in rows]

    def _import_legacy_json_if_empty(self) -> None:
        legacy_json_path = self._legacy_json_path
        if legacy_json_path is None:
            return
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS record_count FROM worker_jobs"
                ).fetchone()
                if row is not None and int(row["record_count"]) > 0:
                    return
                records = _read_legacy_worker_jobs_json(legacy_json_path)
                if not records:
                    return
                connection.executemany(
                    """
                    INSERT INTO worker_jobs(
                        job_id,
                        session_id,
                        container_name,
                        status,
                        cleanup_status,
                        started_at,
                        finished_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        container_name = excluded.container_name,
                        status = excluded.status,
                        cleanup_status = excluded.cleanup_status,
                        started_at = excluded.started_at,
                        finished_at = excluded.finished_at,
                        payload_json = excluded.payload_json
                    """,
                    (self._record_row(record) for record in records.values()),
                )
