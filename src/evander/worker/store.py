from __future__ import annotations

import json
import hashlib
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
_INFLIGHT_STATUSES = ("created", "preparing", "starting", "running")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _worker_jobs_db_path(path: Path) -> Path:
    if path.suffix.lower() in _SQLITE_WORKER_JOB_SUFFIXES:
        return path
    return path.with_suffix(".db")


class WorkerJobStore:
    def __init__(self, path: Path) -> None:
        self._lock = RLock()
        self.path = _worker_jobs_db_path(path)
        self.ensure_schema()

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
                    CREATE TABLE IF NOT EXISTS worker_steering (
                        job_id TEXT NOT NULL,
                        delivery_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        PRIMARY KEY(job_id, delivery_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_session_status_started_job
                        ON worker_jobs(session_id, status, started_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_session_started_job
                        ON worker_jobs(session_id, started_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_status_started_job
                        ON worker_jobs(status, started_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_cleanup_status_started_job
                        ON worker_jobs(cleanup_status, started_at, job_id);
                    CREATE INDEX IF NOT EXISTS idx_worker_jobs_status_cleanup
                        ON worker_jobs(status, cleanup_status);
                    """
                )

    def recover_steering(self) -> None:
        """A crashed sender cannot prove whether app-server accepted its write."""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT job_id,delivery_id,payload_json FROM worker_steering"
            ).fetchall()
            for row in rows:
                receipt = json.loads(row["payload_json"])
                if receipt["status"] == "sending":
                    receipt.update(status="delivery_unknown", reason="worker_restarted")
                    db.execute(
                        "UPDATE worker_steering SET payload_json=? WHERE job_id=? AND delivery_id=?",
                        (_json_dumps(receipt), row["job_id"], row["delivery_id"]),
                    )

    def claim_steering(self, job_id: str, request: Any) -> tuple[dict[str, Any], bool]:
        fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT fingerprint,payload_json FROM worker_steering WHERE job_id=? AND delivery_id=?",
                (job_id, request.delivery_id),
            ).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise ValueError("Steering delivery identity conflict")
                return json.loads(row["payload_json"]), False
            receipt = {
                "job_id": job_id,
                "delivery_id": request.delivery_id,
                "session_id": request.session_id,
                "thread_id": request.expected_thread_id,
                "turn_id": request.expected_turn_id,
                "status": "sending",
            }
            db.execute(
                "INSERT INTO worker_steering VALUES(?,?,?,?)",
                (job_id, request.delivery_id, fingerprint, _json_dumps(receipt)),
            )
            return receipt, True

    def steering(self, job_id: str, delivery_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT payload_json FROM worker_steering WHERE job_id=? AND delivery_id=?",
                (job_id, delivery_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def finish_steering(
        self, job_id: str, delivery_id: str, status: str, reason: str = ""
    ) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload_json FROM worker_steering WHERE job_id=? AND delivery_id=?",
                (job_id, delivery_id),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown steering delivery")
            receipt = json.loads(row[0])
            # An acknowledgement wins over a racing timeout or connection close.
            if receipt["status"] not in {"accepted", "rejected"}:
                receipt.update(status=status, reason=reason)
                db.execute(
                    "UPDATE worker_steering SET payload_json=? WHERE job_id=? AND delivery_id=?",
                    (_json_dumps(receipt), job_id, delivery_id),
                )
            return receipt

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
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM worker_steering WHERE job_id=?", (job_id,))
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

    def list_inflight(self) -> list[WorkerJobRecord]:
        placeholders = ", ".join("?" for _ in _INFLIGHT_STATUSES)
        return self._list_records(
            f"SELECT job_id, payload_json FROM worker_jobs WHERE status IN ({placeholders}) "
            "ORDER BY started_at IS NULL, started_at, job_id",
            _INFLIGHT_STATUSES,
        )

    def status_counts(self) -> tuple[dict[str, int], int]:
        """Aggregate persisted status fields without loading job payloads."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count,
                       SUM(status = 'cleanup_failed' OR cleanup_status = 'cleanup_failed')
                           AS cleanup_failures
                FROM worker_jobs
                GROUP BY status
                """
            ).fetchall()
        return (
            {row["status"]: int(row["count"]) for row in rows},
            sum(int(row["cleanup_failures"]) for row in rows),
        )

    def _list_records(self, query: str, params: tuple[Any, ...]) -> list[WorkerJobRecord]:
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(query, params).fetchall()
        return [WorkerJobRecord.model_validate_json(row["payload_json"]) for row in rows]
