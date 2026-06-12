from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from agent_sandbox.cron import next_cron_time
from agent_sandbox.models import (
    WakeDispatchRecord,
    WakeMessageEnvelope,
    WakeResumePromptPayload,
    WakeScheduledRequest,
    WakeScheduleDefinition,
)

_WAKE_RETENTION = timedelta(days=90)
_SQLITE_WAKE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0


def wake_store_db_path(path: Path) -> Path:
    if path.suffix.lower() in _SQLITE_WAKE_SUFFIXES:
        return path
    return path.with_suffix(".db")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class WakeRequestRepository(Protocol):
    """Repository contract for scheduled wake requests and dispatch history."""

    def upsert_request(self, request: WakeScheduledRequest) -> None: ...

    def get_request(self, request_id: str) -> WakeScheduledRequest | None: ...

    def delete_request(self, request_id: str) -> bool: ...

    def record_dispatch(self, dispatch: WakeDispatchRecord) -> None: ...

    def mark_request_due_or_missed(
        self, *, now_utc: datetime, late_after: timedelta
    ) -> list[WakeScheduledRequest]: ...

    def mark_request_dispatching(
        self,
        request_id: str,
        *,
        now_utc: datetime,
        reason: str | None = None,
    ) -> WakeScheduledRequest | None: ...

    def mark_completed_by_dispatch_id(
        self,
        dispatch_id: str,
        *,
        completed_at: datetime,
        evidence: dict[str, Any],
    ) -> WakeScheduledRequest | None: ...

    def schedule_resume_request(
        self,
        *,
        session_id: str,
        prompt: str,
        schedule: WakeScheduleDefinition,
        request_id: str | None,
        source: str | None,
        resume_mode: str,
        enabled: bool,
        metadata: dict[str, Any],
    ) -> WakeScheduledRequest: ...

    def list_requests(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[WakeScheduledRequest]: ...

    def find_due_requests(self, *, now_utc: datetime) -> list[WakeScheduledRequest]: ...

    def latest_dispatch_by_request_id(self) -> dict[str, WakeDispatchRecord]: ...

    def list_ledger_entries(
        self,
        *,
        session_id: str | None = None,
        statuses: list[str] | None = None,
        include_completed: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def read_raw(self) -> dict[str, Any]: ...


class WakeRequestStore:
    """SQLite-backed store for scheduled wake requests and wake dispatch history."""

    def __init__(self, path: Path, *, legacy_json_path: Path | None = None) -> None:
        original_path = Path(path)
        self.path = wake_store_db_path(original_path)
        if legacy_json_path is None and self.path != original_path:
            legacy_json_path = original_path
        self.legacy_json_path = legacy_json_path
        self._lock = RLock()
        self.ensure_schema()
        self._import_legacy_json_if_empty()

    def upsert_request(self, request: WakeScheduledRequest) -> None:
        validated = WakeScheduledRequest.model_validate(request)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO wake_requests(
                    request_id,
                    session_id,
                    enabled,
                    created_at,
                    updated_at,
                    last_dispatched_at,
                    next_run_at,
                    schedule_resume_at,
                    schedule_cron,
                    source,
                    resume_mode,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    enabled = excluded.enabled,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    last_dispatched_at = excluded.last_dispatched_at,
                    next_run_at = excluded.next_run_at,
                    schedule_resume_at = excluded.schedule_resume_at,
                    schedule_cron = excluded.schedule_cron,
                    source = excluded.source,
                    resume_mode = excluded.resume_mode,
                    payload_json = excluded.payload_json
                """,
                self._request_row(validated),
            )
            self._compact(connection)

    def get_request(self, request_id: str) -> WakeScheduledRequest | None:
        normalized_request_id = str(request_id or "")
        if not normalized_request_id:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM wake_requests
                WHERE request_id = ?
                """,
                (normalized_request_id,),
            ).fetchone()
        if row is None:
            return None
        return WakeScheduledRequest.model_validate_json(row["payload_json"])

    def delete_request(self, request_id: str) -> bool:
        normalized_request_id = str(request_id or "")
        if not normalized_request_id:
            return False
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM wake_requests WHERE request_id = ?",
                (normalized_request_id,),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                self._compact(connection)
            return deleted

    def record_dispatch(self, dispatch: WakeDispatchRecord) -> None:
        validated = WakeDispatchRecord.model_validate(dispatch)
        now = datetime.now(UTC)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO wake_dispatches(
                    dispatch_id,
                    request_id,
                    session_id,
                    status,
                    dispatched_at,
                    source,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dispatch_id) DO UPDATE SET
                    request_id = excluded.request_id,
                    session_id = excluded.session_id,
                    status = excluded.status,
                    dispatched_at = excluded.dispatched_at,
                    source = excluded.source,
                    payload_json = excluded.payload_json
                """,
                self._dispatch_row(validated),
            )

            if validated.request_id:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM wake_requests
                    WHERE request_id = ?
                    """,
                    (validated.request_id,),
                ).fetchone()
                if row is not None:
                    request = WakeScheduledRequest.model_validate_json(row["payload_json"])
                    request.updated_at = now
                    request.status_updated_at = validated.dispatched_at
                    request.last_dispatch_id = validated.dispatch_id
                    request.last_dispatch_status = validated.status
                    request.last_error = None
                    request.last_error_at = None
                    if validated.status in {"failed", "timed_out"}:
                        request.status = "failed"
                        request.status_reason = self._dispatch_failure_reason(validated)
                        request.last_error = request.status_reason
                        request.last_error_at = validated.dispatched_at
                    elif validated.status == "queued":
                        request.status = "queued"
                        request.status_reason = None
                    else:
                        request.status = "dispatched"
                        request.status_reason = None
                        request.last_dispatched_at = validated.dispatched_at
                        if request.schedule.resume_at is not None:
                            request.next_run_at = None
                            request.enabled = False
                    connection.execute(
                        """
                        UPDATE wake_requests
                        SET session_id = ?,
                            enabled = ?,
                            created_at = ?,
                            updated_at = ?,
                            last_dispatched_at = ?,
                            next_run_at = ?,
                            schedule_resume_at = ?,
                            schedule_cron = ?,
                            source = ?,
                            resume_mode = ?,
                            payload_json = ?
                        WHERE request_id = ?
                        """,
                        self._request_update_row(request),
                    )

            message = WakeMessageEnvelope(
                message_id=f"wake:{validated.dispatch_id}",
                session_id=validated.session_id,
                request_id=validated.request_id,
                dispatch_id=validated.dispatch_id,
                status=validated.status,
                resume_prompt=validated.resume_prompt,
                resume_mode=validated.resume_mode,
                created_at=validated.dispatched_at,
                metadata=validated.metadata,
            )
            connection.execute(
                """
                INSERT INTO wake_messages(
                    message_id,
                    request_id,
                    dispatch_id,
                    session_id,
                    status,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    request_id = excluded.request_id,
                    dispatch_id = excluded.dispatch_id,
                    session_id = excluded.session_id,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    payload_json = excluded.payload_json
                """,
                self._message_row(message),
            )
            self._compact(connection)

    def schedule_resume_request(
        self,
        *,
        session_id: str,
        prompt: str,
        schedule: WakeScheduleDefinition,
        request_id: str | None,
        source: str | None,
        resume_mode: str,
        enabled: bool,
        metadata: dict[str, Any],
    ) -> WakeScheduledRequest:
        now = datetime.now(UTC)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_id:
                normalized_request_id = str(request_id)
                if self._request_exists(connection, normalized_request_id):
                    raise ValueError(f"Wake request already exists: {normalized_request_id}")
            else:
                normalized_request_id = f"wake-{uuid4().hex}"
                while self._request_exists(connection, normalized_request_id):
                    normalized_request_id = f"wake-{uuid4().hex}"

            request = WakeScheduledRequest(
                request_id=normalized_request_id,
                session_id=session_id,
                schedule=schedule,
                resume_prompt=WakeResumePromptPayload(prompt=prompt, metadata=metadata),
                resume_mode=resume_mode,
                enabled=enabled,
                status="pending",
                status_reason=None,
                status_updated_at=now,
                completion_evidence={},
                source=source,
                created_at=now,
                updated_at=now,
                next_run_at=schedule.resume_at,
            )
            connection.execute(
                """
                INSERT INTO wake_requests(
                    request_id,
                    session_id,
                    enabled,
                    created_at,
                    updated_at,
                    last_dispatched_at,
                    next_run_at,
                    schedule_resume_at,
                    schedule_cron,
                    source,
                    resume_mode,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._request_row(request),
            )
            self._compact(connection)
            return request

    def mark_request_due_or_missed(
        self, *, now_utc: datetime, late_after: timedelta
    ) -> list[WakeScheduledRequest]:
        grace = max(late_after, timedelta(0))
        latest_dispatches = self.latest_dispatch_by_request_id()
        updated_requests: list[WakeScheduledRequest] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT payload_json
                FROM wake_requests
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY COALESCE(next_run_at, created_at) ASC, request_id ASC
                """,
                (self._datetime_to_storage(now_utc),),
            ).fetchall()
            for row in rows:
                try:
                    request = WakeScheduledRequest.model_validate_json(row["payload_json"])
                except Exception:
                    continue
                status, reason = self._derived_due_status(
                    request,
                    now_utc=now_utc,
                    grace=grace,
                    latest_dispatch=latest_dispatches.get(request.request_id),
                )
                if status == request.status and reason == request.status_reason:
                    continue
                request.status = status
                request.status_reason = reason
                request.status_updated_at = now_utc
                request.updated_at = now_utc
                if status == "missed_or_late" and not request.last_error:
                    request.last_error = reason
                    request.last_error_at = now_utc
                connection.execute(
                    """
                    UPDATE wake_requests
                    SET session_id = ?,
                        enabled = ?,
                        created_at = ?,
                        updated_at = ?,
                        last_dispatched_at = ?,
                        next_run_at = ?,
                        schedule_resume_at = ?,
                        schedule_cron = ?,
                        source = ?,
                        resume_mode = ?,
                        payload_json = ?
                    WHERE request_id = ?
                    """,
                    self._request_update_row(request),
                )
                updated_requests.append(request)
            self._compact(connection)
        return updated_requests

    def mark_request_dispatching(
        self,
        request_id: str,
        *,
        now_utc: datetime,
        reason: str | None = None,
    ) -> WakeScheduledRequest | None:
        normalized_request_id = str(request_id or "")
        if not normalized_request_id:
            return None
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_json
                FROM wake_requests
                WHERE request_id = ?
                """,
                (normalized_request_id,),
            ).fetchone()
            if row is None:
                return None
            request = WakeScheduledRequest.model_validate_json(row["payload_json"])
            request.status = "dispatching"
            request.status_reason = reason or "dispatch attempt started"
            request.status_updated_at = now_utc
            request.updated_at = now_utc
            connection.execute(
                """
                UPDATE wake_requests
                SET session_id = ?,
                    enabled = ?,
                    created_at = ?,
                    updated_at = ?,
                    last_dispatched_at = ?,
                    next_run_at = ?,
                    schedule_resume_at = ?,
                    schedule_cron = ?,
                    source = ?,
                    resume_mode = ?,
                    payload_json = ?
                WHERE request_id = ?
                """,
                self._request_update_row(request),
            )
            self._compact(connection)
        return request

    def mark_completed_by_dispatch_id(
        self,
        dispatch_id: str,
        *,
        completed_at: datetime,
        evidence: dict[str, Any],
    ) -> WakeScheduledRequest | None:
        normalized_dispatch_id = str(dispatch_id or "")
        if not normalized_dispatch_id:
            return None
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_json
                FROM wake_dispatches
                WHERE dispatch_id = ?
                """,
                (normalized_dispatch_id,),
            ).fetchone()
            if row is None:
                return None
            dispatch = WakeDispatchRecord.model_validate_json(row["payload_json"])
            normalized_request_id = str(dispatch.request_id or "")
            if not normalized_request_id:
                return None
            request_row = connection.execute(
                """
                SELECT payload_json
                FROM wake_requests
                WHERE request_id = ?
                """,
                (normalized_request_id,),
            ).fetchone()
            if request_row is None:
                return None
            request = WakeScheduledRequest.model_validate_json(request_row["payload_json"])
            request.status = "completed_or_acknowledged"
            request.status_reason = None
            request.status_updated_at = completed_at
            request.completed_at = completed_at
            request.completion_evidence = dict(evidence)
            request.last_dispatch_id = dispatch.dispatch_id
            request.last_dispatch_status = dispatch.status
            request.last_error = None
            request.last_error_at = None
            request.updated_at = completed_at
            connection.execute(
                """
                UPDATE wake_requests
                SET session_id = ?,
                    enabled = ?,
                    created_at = ?,
                    updated_at = ?,
                    last_dispatched_at = ?,
                    next_run_at = ?,
                    schedule_resume_at = ?,
                    schedule_cron = ?,
                    source = ?,
                    resume_mode = ?,
                    payload_json = ?
                WHERE request_id = ?
                """,
                self._request_update_row(request),
            )
            self._compact(connection)
        return request

    def list_ledger_entries(
        self,
        *,
        session_id: str | None = None,
        statuses: list[str] | None = None,
        include_completed: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_session_id = str(session_id or "").strip()
        normalized_statuses = {
            normalized
            for normalized in (self._normalize_status(status) for status in (statuses or []))
            if normalized
        }
        latest_dispatches = self.latest_dispatch_by_request_id()
        with self._lock, self._connect() as connection:
            params: list[Any] = []
            sql = "SELECT payload_json FROM wake_requests"
            if normalized_session_id:
                sql += " WHERE session_id = ?"
                params.append(normalized_session_id)
            sql += " ORDER BY updated_at DESC, request_id DESC"
            rows = connection.execute(sql, params).fetchall()

        entries: list[dict[str, Any]] = []
        for row in rows:
            try:
                request = WakeScheduledRequest.model_validate_json(row["payload_json"])
            except Exception:
                continue
            latest_dispatch = latest_dispatches.get(request.request_id)
            status = self._ledger_status_for_request(request, latest_dispatch=latest_dispatch)
            if normalized_statuses and status not in normalized_statuses:
                continue
            if not include_completed and status == "completed_or_acknowledged":
                continue
            entries.append(
                self._ledger_entry_for_request(
                    request,
                    latest_dispatch=latest_dispatch,
                    status=status,
                )
            )
            if len(entries) >= max(0, int(limit)):
                break
        return entries

    def read_raw(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            request_rows = connection.execute(
                """
                SELECT request_id, payload_json
                FROM wake_requests
                ORDER BY request_id ASC
                """
            ).fetchall()
            dispatch_rows = connection.execute(
                """
                SELECT dispatch_id, payload_json
                FROM wake_dispatches
                ORDER BY dispatch_id ASC
                """
            ).fetchall()
            message_rows = connection.execute(
                """
                SELECT message_id, payload_json
                FROM wake_messages
                ORDER BY message_id ASC
                """
            ).fetchall()
        return {
            "requests": {
                str(row["request_id"]): WakeScheduledRequest.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in request_rows
            },
            "dispatches": {
                str(row["dispatch_id"]): WakeDispatchRecord.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in dispatch_rows
            },
            "messages": {
                str(row["message_id"]): WakeMessageEnvelope.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in message_rows
            },
        }

    def list_requests(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[WakeScheduledRequest]:
        where: list[str] = []
        params: list[Any] = []
        normalized_session_id = str(session_id or "")
        if normalized_session_id:
            where.append("session_id = ?")
            params.append(normalized_session_id)
        if not include_disabled:
            where.append("enabled = 1")

        sql = "SELECT payload_json FROM wake_requests"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at ASC, request_id ASC"

        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()

        requests: list[WakeScheduledRequest] = []
        for row in rows:
            try:
                request = WakeScheduledRequest.model_validate_json(row["payload_json"])
            except Exception:
                continue
            if normalized_session_id and request.session_id != normalized_session_id:
                continue
            if not include_disabled and not request.enabled:
                continue
            requests.append(request)
        return requests

    def latest_dispatch_by_request_id(self) -> dict[str, WakeDispatchRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_id, payload_json
                FROM wake_dispatches
                WHERE request_id IS NOT NULL AND request_id != ''
                ORDER BY request_id ASC, dispatched_at DESC, dispatch_id DESC
                """
            ).fetchall()

        latest: dict[str, WakeDispatchRecord] = {}
        for row in rows:
            request_id = str(row["request_id"])
            if request_id in latest:
                continue
            try:
                dispatch = WakeDispatchRecord.model_validate_json(row["payload_json"])
            except Exception:
                continue
            latest[request_id] = dispatch
        return latest

    def find_due_requests(self, *, now_utc: datetime) -> list[WakeScheduledRequest]:
        now_storage = self._datetime_to_storage(now_utc)
        due_requests: list[WakeScheduledRequest] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT request_id, payload_json, next_run_at
                FROM wake_requests
                WHERE enabled = 1
                  AND (next_run_at <= ? OR next_run_at IS NULL)
                ORDER BY COALESCE(next_run_at, created_at) ASC, request_id ASC
                """,
                (now_storage,),
            ).fetchall()
            for row in rows:
                try:
                    request = WakeScheduledRequest.model_validate_json(row["payload_json"])
                except Exception:
                    continue
                next_run_at = request.next_run_at
                if next_run_at is None:
                    cron = (request.schedule.cron or "").strip()
                    if cron:
                        request.next_run_at = next_cron_time(cron, now_utc - timedelta(minutes=1))
                        request.updated_at = now_utc
                        connection.execute(
                            """
                            UPDATE wake_requests
                            SET session_id = ?,
                                enabled = ?,
                                created_at = ?,
                                updated_at = ?,
                                last_dispatched_at = ?,
                                next_run_at = ?,
                                schedule_resume_at = ?,
                                schedule_cron = ?,
                            source = ?,
                            resume_mode = ?,
                            payload_json = ?
                        WHERE request_id = ?
                        """,
                            self._request_update_row(request),
                        )
                        next_run_at = request.next_run_at
                    else:
                        next_run_at = request.schedule.resume_at
                if next_run_at is None or next_run_at > now_utc:
                    continue
                due_requests.append(request)
        due_requests.sort(key=lambda request: (request.next_run_at or request.created_at, request.request_id))
        return due_requests

    def ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS wake_requests (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_dispatched_at TEXT,
                    next_run_at TEXT,
                    schedule_resume_at TEXT,
                    schedule_cron TEXT,
                    source TEXT,
                    resume_mode TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wake_requests_due
                    ON wake_requests(enabled, next_run_at, request_id);
                CREATE INDEX IF NOT EXISTS idx_wake_requests_listing
                    ON wake_requests(session_id, enabled, updated_at, request_id);
                CREATE INDEX IF NOT EXISTS idx_wake_requests_retention
                    ON wake_requests(enabled, updated_at);

                CREATE TABLE IF NOT EXISTS wake_dispatches (
                    dispatch_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dispatched_at TEXT NOT NULL,
                    source TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wake_dispatches_latest
                    ON wake_dispatches(request_id, dispatched_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_wake_dispatches_retention
                    ON wake_dispatches(dispatched_at);

                CREATE TABLE IF NOT EXISTS wake_messages (
                    message_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    dispatch_id TEXT,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wake_messages_retention
                    ON wake_messages(created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
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

    def _import_legacy_json_if_empty(self) -> None:
        legacy_json_path = self.legacy_json_path
        if (
            legacy_json_path is None
            or legacy_json_path == self.path
            or legacy_json_path.suffix.lower() in _SQLITE_WAKE_SUFFIXES
            or not legacy_json_path.exists()
        ):
            return
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM wake_requests) AS request_count,
                    (SELECT COUNT(*) FROM wake_dispatches) AS dispatch_count,
                    (SELECT COUNT(*) FROM wake_messages) AS message_count
                """
            ).fetchone()
            if row is not None and (
                int(row["request_count"] or 0) > 0
                or int(row["dispatch_count"] or 0) > 0
                or int(row["message_count"] or 0) > 0
            ):
                return

            payload = self._read_legacy_payload(legacy_json_path)
            raw_requests = payload.get("requests", {})
            if not isinstance(raw_requests, dict):
                raise ValueError("Wake JSON store requests must be an object")
            for raw_value in raw_requests.values():
                request = WakeScheduledRequest.model_validate(raw_value)
                connection.execute(
                    """
                    INSERT INTO wake_requests(
                        request_id,
                        session_id,
                        enabled,
                        created_at,
                        updated_at,
                        last_dispatched_at,
                        next_run_at,
                        schedule_resume_at,
                        schedule_cron,
                        source,
                        resume_mode,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._request_row(request),
                )

            raw_dispatches = payload.get("dispatches", {})
            if not isinstance(raw_dispatches, dict):
                raise ValueError("Wake JSON store dispatches must be an object")
            for raw_value in raw_dispatches.values():
                dispatch = WakeDispatchRecord.model_validate(raw_value)
                connection.execute(
                    """
                    INSERT INTO wake_dispatches(
                        dispatch_id,
                        request_id,
                        session_id,
                        status,
                        dispatched_at,
                        source,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._dispatch_row(dispatch),
                )

            raw_messages = payload.get("messages", {})
            if not isinstance(raw_messages, dict):
                raise ValueError("Wake JSON store messages must be an object")
            for raw_value in raw_messages.values():
                message = WakeMessageEnvelope.model_validate(raw_value)
                connection.execute(
                    """
                    INSERT INTO wake_messages(
                        message_id,
                        request_id,
                        dispatch_id,
                        session_id,
                        status,
                        created_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._message_row(message),
                )

    @staticmethod
    def _read_legacy_payload(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {"requests": {}, "dispatches": {}, "messages": {}}
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError(f"Wake JSON store at {path} must contain an object")
        return raw

    def _compact(self, connection: sqlite3.Connection) -> None:
        cutoff = self._datetime_to_storage(datetime.now(UTC) - _WAKE_RETENTION)
        connection.execute(
            """
            DELETE FROM wake_dispatches
            WHERE dispatched_at < ?
            """,
            (cutoff,),
        )
        connection.execute(
            """
            DELETE FROM wake_messages
            WHERE created_at < ?
            """,
            (cutoff,),
        )
        connection.execute(
            """
            DELETE FROM wake_requests
            WHERE enabled = 0
              AND updated_at < ?
            """,
            (cutoff,),
        )

    @staticmethod
    def _validate_request(raw_value: object) -> WakeScheduledRequest | None:
        if not isinstance(raw_value, dict):
            return None
        return WakeScheduledRequest.model_validate(raw_value)

    @staticmethod
    def _validate_dispatch(raw_value: object) -> WakeDispatchRecord | None:
        if not isinstance(raw_value, dict):
            return None
        return WakeDispatchRecord.model_validate(raw_value)

    @staticmethod
    def _validate_message(raw_value: object) -> WakeMessageEnvelope | None:
        if not isinstance(raw_value, dict):
            return None
        return WakeMessageEnvelope.model_validate(raw_value)

    @staticmethod
    def _request_exists(connection: sqlite3.Connection, request_id: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM wake_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _request_row(request: WakeScheduledRequest) -> tuple[Any, ...]:
        payload = request.model_dump(mode="json")
        return (
            request.request_id,
            request.session_id,
            int(bool(request.enabled)),
            WakeRequestStore._datetime_to_storage(request.created_at),
            WakeRequestStore._datetime_to_storage(request.updated_at),
            WakeRequestStore._datetime_to_storage(request.last_dispatched_at),
            WakeRequestStore._datetime_to_storage(request.next_run_at),
            WakeRequestStore._datetime_to_storage(request.schedule.resume_at),
            request.schedule.cron,
            request.source,
            request.resume_mode,
            _json_dumps(payload),
        )

    @staticmethod
    def _request_update_row(request: WakeScheduledRequest) -> tuple[Any, ...]:
        payload = request.model_dump(mode="json")
        return (
            request.session_id,
            int(bool(request.enabled)),
            WakeRequestStore._datetime_to_storage(request.created_at),
            WakeRequestStore._datetime_to_storage(request.updated_at),
            WakeRequestStore._datetime_to_storage(request.last_dispatched_at),
            WakeRequestStore._datetime_to_storage(request.next_run_at),
            WakeRequestStore._datetime_to_storage(request.schedule.resume_at),
            request.schedule.cron,
            request.source,
            request.resume_mode,
            _json_dumps(payload),
            request.request_id,
        )

    @staticmethod
    def _dispatch_row(dispatch: WakeDispatchRecord) -> tuple[Any, ...]:
        payload = dispatch.model_dump(mode="json")
        return (
            dispatch.dispatch_id,
            dispatch.request_id,
            dispatch.session_id,
            dispatch.status,
            WakeRequestStore._datetime_to_storage(dispatch.dispatched_at),
            dispatch.source,
            _json_dumps(payload),
        )

    @staticmethod
    def _message_row(message: WakeMessageEnvelope) -> tuple[Any, ...]:
        payload = message.model_dump(mode="json")
        return (
            message.message_id,
            message.request_id,
            message.dispatch_id,
            message.session_id,
            message.status,
            WakeRequestStore._datetime_to_storage(message.created_at),
            _json_dumps(payload),
        )

    @staticmethod
    def _datetime_to_storage(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _parse_dt(value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            return datetime.min.replace(tzinfo=UTC)
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _normalize_status(value: object) -> str:
        normalized = str(value or "").strip().casefold()
        return normalized

    @staticmethod
    def _sanitize_text(value: object, *, limit: int = 240) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _dispatch_failure_reason(dispatch: WakeDispatchRecord) -> str:
        if dispatch.error:
            reason = dispatch.error
        elif dispatch.status == "timed_out":
            reason = "wake dispatch timed out"
        else:
            reason = f"wake dispatch failed with status {dispatch.status}"
        sanitized = WakeRequestStore._sanitize_text(reason, limit=240)
        return sanitized or dispatch.status

    @staticmethod
    def _prompt_summary(prompt: str) -> str:
        sanitized = WakeRequestStore._sanitize_text(prompt, limit=160)
        if sanitized is None:
            return ""
        return sanitized

    @staticmethod
    def _request_status_from_dispatch(dispatch: WakeDispatchRecord) -> str:
        status = WakeRequestStore._normalize_status(dispatch.status)
        if status in {"failed", "timed_out"}:
            return "failed"
        if status in {"queued", "dispatched"}:
            return status
        return status or "pending"

    def _derived_due_status(
        self,
        request: WakeScheduledRequest,
        *,
        now_utc: datetime,
        grace: timedelta,
        latest_dispatch: WakeDispatchRecord | None,
    ) -> tuple[str, str | None]:
        if request.completed_at is not None:
            return "completed_or_acknowledged", request.status_reason

        current_status = self._normalize_status(request.status) or "pending"
        if current_status == "dispatching":
            next_run_at = request.next_run_at or request.schedule.resume_at
            if next_run_at is not None and next_run_at > now_utc:
                return "dispatching", request.status_reason
            if latest_dispatch is None:
                return "missed_or_late", "Previous dispatch attempt did not complete."
            latest_dispatch_status = self._request_status_from_dispatch(latest_dispatch)
            if latest_dispatch_status in {"dispatched", "queued", "failed"}:
                return latest_dispatch_status, request.status_reason
            return "missed_or_late", "Previous dispatch attempt did not complete."

        if current_status in {
            "queued",
            "dispatched",
            "failed",
            "missed_or_late",
            "completed_or_acknowledged",
        }:
            return current_status, request.status_reason

        next_run_at = request.next_run_at or request.schedule.resume_at
        if next_run_at is None:
            return current_status, request.status_reason

        if next_run_at > now_utc:
            return "pending", request.status_reason

        late_seconds = (now_utc - next_run_at).total_seconds()
        if late_seconds <= grace.total_seconds():
            return "due", request.status_reason
        return "missed_or_late", request.status_reason or "Scheduled wake missed its window."

    def _ledger_status_for_request(
        self,
        request: WakeScheduledRequest,
        *,
        latest_dispatch: WakeDispatchRecord | None,
    ) -> str:
        derived_status, _reason = self._derived_due_status(
            request,
            now_utc=datetime.now(UTC),
            grace=timedelta(0),
            latest_dispatch=latest_dispatch,
        )
        return derived_status

    def _ledger_entry_for_request(
        self,
        request: WakeScheduledRequest,
        *,
        latest_dispatch: WakeDispatchRecord | None,
        status: str,
    ) -> dict[str, Any]:
        latest_dispatch_summary: dict[str, Any] | None = None
        if latest_dispatch is not None:
            latest_dispatch_summary = {
                "dispatch_id": latest_dispatch.dispatch_id,
                "status": latest_dispatch.status,
                "dispatched_at": self._datetime_to_storage(latest_dispatch.dispatched_at),
                "source": latest_dispatch.source,
                "error": self._sanitize_text(latest_dispatch.error),
            }
        prompt = request.resume_prompt.prompt
        failure_reason = self._sanitize_text(request.last_error or request.status_reason)
        return {
            "request_id": request.request_id,
            "session_id": request.session_id,
            "source": request.source,
            "resume_at": self._datetime_to_storage(request.schedule.resume_at),
            "next_run_at": self._datetime_to_storage(request.next_run_at),
            "scheduled_for": self._datetime_to_storage(request.next_run_at or request.schedule.resume_at),
            "schedule_cron": request.schedule.cron,
            "created_at": self._datetime_to_storage(request.created_at),
            "updated_at": self._datetime_to_storage(request.updated_at),
            "status": status,
            "status_reason": self._sanitize_text(request.status_reason),
            "status_updated_at": self._datetime_to_storage(request.status_updated_at),
            "prompt_summary": self._prompt_summary(prompt),
            "redacted_prompt": "[redacted]" if prompt else None,
            "latest_dispatch": latest_dispatch_summary,
            "latest_dispatch_id": request.last_dispatch_id or getattr(latest_dispatch, "dispatch_id", None),
            "latest_dispatch_status": request.last_dispatch_status
            or getattr(latest_dispatch, "status", None),
            "last_error": self._sanitize_text(request.last_error),
            "last_error_at": self._datetime_to_storage(request.last_error_at),
            "completed_at": self._datetime_to_storage(request.completed_at),
            "completion_evidence": dict(request.completion_evidence or {}),
            "retry_available": status in {"failed", "missed_or_late"},
            "failure_reason": failure_reason,
            "enabled": request.enabled,
        }
