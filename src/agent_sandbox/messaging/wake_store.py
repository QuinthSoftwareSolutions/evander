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

SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_WAL_ENABLE_RETRIES = 8
_SQLITE_WAL_RETRY_DELAY_S = 0.05
_WAKE_RETENTION = timedelta(days=90)


def _datetime_to_storage(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class WakeRequestRepository(Protocol):
    """Repository contract for scheduled wake requests and wake dispatch history."""

    def upsert_request(self, request: WakeScheduledRequest) -> None: ...

    def get_request(self, request_id: str) -> WakeScheduledRequest | None: ...

    def delete_request(self, request_id: str) -> bool: ...

    def record_dispatch(self, dispatch: WakeDispatchRecord) -> None: ...

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

    def read_raw(self) -> dict[str, Any]: ...


class WakeRequestStore:
    """SQLite-backed store for scheduled wake requests and wake dispatch history."""

    def __init__(self, path: Path) -> None:
        if path.suffix == ".json":
            self.path = path.with_suffix(".db")
            self.legacy_json_path = path
        else:
            self.path = path
            self.legacy_json_path = None
        self._lock = RLock()
        self._initialized = False

    def upsert_request(self, request: WakeScheduledRequest) -> None:
        self._ensure_ready()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_or_replace_request(connection, request)
            self._compact_connection(connection, now=now)

    def get_request(self, request_id: str) -> WakeScheduledRequest | None:
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM wake_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        return self._row_to_request(row) if row is not None else None

    def delete_request(self, request_id: str) -> bool:
        self._ensure_ready()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rowcount = connection.execute(
                "DELETE FROM wake_requests WHERE request_id = ?",
                (request_id,),
            ).rowcount
            self._compact_connection(connection, now=now)
            return rowcount > 0

    def record_dispatch(self, dispatch: WakeDispatchRecord) -> None:
        self._ensure_ready()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_or_replace_dispatch(connection, dispatch)

            if dispatch.request_id:
                request = self._get_request_row(connection, dispatch.request_id)
                if request is not None:
                    request.updated_at = now
                    if dispatch.status == "dispatched":
                        request.last_dispatched_at = dispatch.dispatched_at
                        if request.schedule.resume_at is not None:
                            request.next_run_at = None
                            request.enabled = False
                    self._insert_or_replace_request(connection, request)

            message = WakeMessageEnvelope(
                message_id=f"wake:{dispatch.dispatch_id}",
                session_id=dispatch.session_id,
                request_id=dispatch.request_id,
                dispatch_id=dispatch.dispatch_id,
                status=dispatch.status,
                resume_prompt=dispatch.resume_prompt,
                resume_mode=dispatch.resume_mode,
                created_at=dispatch.dispatched_at,
                metadata=dispatch.metadata,
            )
            self._insert_or_replace_message(connection, message)
            self._compact_connection(connection, now=now)

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
        self._ensure_ready()
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_id:
                normalized_request_id = request_id
                if self._get_request_row(connection, normalized_request_id) is not None:
                    raise ValueError(f"Wake request already exists: {normalized_request_id}")
            else:
                normalized_request_id = f"wake-{uuid4().hex}"
                while self._get_request_row(connection, normalized_request_id) is not None:
                    normalized_request_id = f"wake-{uuid4().hex}"

            request = WakeScheduledRequest(
                request_id=normalized_request_id,
                session_id=session_id,
                schedule=schedule,
                resume_prompt=WakeResumePromptPayload(prompt=prompt, metadata=metadata),
                resume_mode=resume_mode,
                enabled=enabled,
                source=source,
                created_at=now,
                updated_at=now,
                next_run_at=schedule.resume_at,
            )
            self._insert_or_replace_request(connection, request)
            self._compact_connection(connection, now=now)
            return request

    def read_raw(self) -> dict[str, Any]:
        self._ensure_ready()
        with self._connect() as connection:
            requests = {
                row["request_id"]: json.loads(row["payload_json"])
                for row in connection.execute(
                    """
                    SELECT request_id, payload_json
                    FROM wake_requests
                    ORDER BY request_id
                    """
                ).fetchall()
            }
            dispatches = {
                row["dispatch_id"]: json.loads(row["payload_json"])
                for row in connection.execute(
                    """
                    SELECT dispatch_id, payload_json
                    FROM wake_dispatches
                    ORDER BY dispatch_id
                    """
                ).fetchall()
            }
            messages = {
                row["message_id"]: json.loads(row["payload_json"])
                for row in connection.execute(
                    """
                    SELECT message_id, payload_json
                    FROM wake_messages
                    ORDER BY message_id
                    """
                ).fetchall()
            }
        return {"requests": requests, "dispatches": dispatches, "messages": messages}

    def list_requests(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[WakeScheduledRequest]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM wake_requests
                ORDER BY updated_at, request_id
                """,
            ).fetchall()
        requests: list[WakeScheduledRequest] = []
        for row in rows:
            try:
                request = self._row_to_request(row)
            except Exception:
                continue
            if session_id and request.session_id != session_id:
                continue
            if not include_disabled and not request.enabled:
                continue
            requests.append(request)
        return requests

    def latest_dispatch_by_request_id(self) -> dict[str, WakeDispatchRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM wake_dispatches
                WHERE request_id IS NOT NULL AND request_id != ''
                ORDER BY request_id, dispatched_at DESC, dispatch_id DESC
                """,
            ).fetchall()
        latest: dict[str, WakeDispatchRecord] = {}
        for row in rows:
            try:
                dispatch = self._row_to_dispatch(row)
            except Exception:
                continue
            if not dispatch.request_id or dispatch.request_id in latest:
                continue
            latest[dispatch.request_id] = dispatch
        return latest

    def find_due_requests(self, *, now_utc: datetime) -> list[WakeScheduledRequest]:
        self._ensure_ready()
        due_requests: list[WakeScheduledRequest] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT payload_json
                FROM wake_requests
                WHERE enabled = 1
                ORDER BY COALESCE(next_run_at, created_at), request_id
                """
            ).fetchall()
            for row in rows:
                try:
                    request = self._row_to_request(row)
                except Exception:
                    continue
                next_run_at = request.next_run_at
                if next_run_at is None:
                    cron = (request.schedule.cron or "").strip()
                    if cron:
                        request.next_run_at = next_cron_time(cron, now_utc - timedelta(minutes=1))
                        request.updated_at = now_utc
                        self._insert_or_replace_request(connection, request)
                        next_run_at = request.next_run_at
                    else:
                        next_run_at = request.schedule.resume_at
                if next_run_at is None or next_run_at > now_utc:
                    continue
                due_requests.append(request)
            self._compact_connection(connection, now=now_utc)
        due_requests.sort(
            key=lambda request: (request.next_run_at or request.created_at, request.request_id)
        )
        return due_requests

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS wake_requests (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
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
                    ON wake_requests(enabled, updated_at, request_id);

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
                    ON wake_dispatches(dispatched_at, dispatch_id);

                CREATE TABLE IF NOT EXISTS wake_messages (
                    message_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    dispatch_id TEXT,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_wake_messages_created_at
                    ON wake_messages(created_at, message_id);
                """
            )

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self.ensure_schema()
            self._import_legacy_json_if_empty()
            self._initialized = True

    def _import_legacy_json_if_empty(self) -> None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return
        legacy_text = self.legacy_json_path.read_text(encoding="utf-8")
        if not legacy_text.strip():
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._has_any_rows(connection):
                return
            raw = json.loads(legacy_text)
            if not isinstance(raw, dict):
                raise ValueError(
                    f"legacy wake request store {self.legacy_json_path} must contain a JSON object"
                )
            requests = raw.get("requests", {})
            dispatches = raw.get("dispatches", {})
            messages = raw.get("messages", {})
            if not isinstance(requests, dict) or not isinstance(dispatches, dict) or not isinstance(
                messages, dict
            ):
                raise ValueError(
                    f"legacy wake request store {self.legacy_json_path} must contain object maps"
                )
            for value in requests.values():
                request = WakeScheduledRequest.model_validate(value)
                self._insert_or_replace_request(connection, request)
            for value in dispatches.values():
                dispatch = WakeDispatchRecord.model_validate(value)
                self._insert_or_replace_dispatch(connection, dispatch)
            for value in messages.values():
                message = WakeMessageEnvelope.model_validate(value)
                self._insert_or_replace_message(connection, message)

    def _has_any_rows(self, connection: sqlite3.Connection) -> bool:
        for table in ("wake_requests", "wake_dispatches", "wake_messages"):
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table}",
            ).fetchone()
            if row is not None and int(row["count"]) > 0:
                return True
        return False

    def _insert_or_replace_request(
        self, connection: sqlite3.Connection, request: WakeScheduledRequest
    ) -> None:
        payload = request.model_dump(mode="json")
        connection.execute(
            """
            INSERT OR REPLACE INTO wake_requests(
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
            (
                request.request_id,
                request.session_id,
                1 if request.enabled else 0,
                _datetime_to_storage(request.created_at),
                _datetime_to_storage(request.updated_at),
                _datetime_to_storage(request.last_dispatched_at),
                _datetime_to_storage(request.next_run_at),
                _datetime_to_storage(request.schedule.resume_at),
                request.schedule.cron,
                request.source,
                request.resume_mode,
                _json_dumps(payload),
            ),
        )

    def _insert_or_replace_dispatch(
        self, connection: sqlite3.Connection, dispatch: WakeDispatchRecord
    ) -> None:
        payload = dispatch.model_dump(mode="json")
        connection.execute(
            """
            INSERT OR REPLACE INTO wake_dispatches(
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
            (
                dispatch.dispatch_id,
                dispatch.request_id,
                dispatch.session_id,
                dispatch.status,
                _datetime_to_storage(dispatch.dispatched_at),
                dispatch.source,
                _json_dumps(payload),
            ),
        )

    def _insert_or_replace_message(
        self, connection: sqlite3.Connection, message: WakeMessageEnvelope
    ) -> None:
        payload = message.model_dump(mode="json")
        connection.execute(
            """
            INSERT OR REPLACE INTO wake_messages(
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
            (
                message.message_id,
                message.request_id,
                message.dispatch_id,
                message.session_id,
                message.status,
                _datetime_to_storage(message.created_at),
                _json_dumps(payload),
            ),
        )

    def _get_request_row(
        self, connection: sqlite3.Connection, request_id: str
    ) -> WakeScheduledRequest | None:
        row = connection.execute(
            """
            SELECT payload_json
            FROM wake_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        return self._row_to_request(row) if row is not None else None

    def _row_to_request(self, row: sqlite3.Row) -> WakeScheduledRequest:
        return WakeScheduledRequest.model_validate(json.loads(row["payload_json"]))

    def _row_to_dispatch(self, row: sqlite3.Row) -> WakeDispatchRecord:
        return WakeDispatchRecord.model_validate(json.loads(row["payload_json"]))

    def _row_to_message(self, row: sqlite3.Row) -> WakeMessageEnvelope:
        return WakeMessageEnvelope.model_validate(json.loads(row["payload_json"]))

    def _compact_connection(self, connection: sqlite3.Connection, *, now: datetime) -> None:
        cutoff = now - _WAKE_RETENTION

        for row in connection.execute(
            "SELECT dispatch_id, dispatched_at FROM wake_dispatches"
        ).fetchall():
            if self._parse_dt(row["dispatched_at"]) < cutoff:
                connection.execute(
                    "DELETE FROM wake_dispatches WHERE dispatch_id = ?",
                    (row["dispatch_id"],),
                )

        for row in connection.execute("SELECT message_id, created_at FROM wake_messages").fetchall():
            if self._parse_dt(row["created_at"]) < cutoff:
                connection.execute(
                    "DELETE FROM wake_messages WHERE message_id = ?",
                    (row["message_id"],),
                )

        for row in connection.execute(
            "SELECT request_id, enabled, updated_at FROM wake_requests"
        ).fetchall():
            if int(row["enabled"]) != 0:
                continue
            if self._parse_dt(row["updated_at"]) < cutoff:
                connection.execute(
                    "DELETE FROM wake_requests WHERE request_id = ?",
                    (row["request_id"],),
                )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        self._enable_wal_mode(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _enable_wal_mode(self, connection: sqlite3.Connection) -> None:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(_SQLITE_WAL_ENABLE_RETRIES):
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                last_error = exc
                time.sleep(_SQLITE_WAL_RETRY_DELAY_S * (attempt + 1))
        if last_error is not None:
            raise last_error

    def _compact(self, payload: dict[str, Any]) -> None:
        cutoff = datetime.now(UTC) - _WAKE_RETENTION

        dispatches = payload.get("dispatches")
        if isinstance(dispatches, dict):
            payload["dispatches"] = {
                dispatch_id: raw_value
                for dispatch_id, raw_value in dispatches.items()
                if self._parse_dt((raw_value or {}).get("dispatched_at")) >= cutoff
            }

        messages = payload.get("messages")
        if isinstance(messages, dict):
            payload["messages"] = {
                message_id: raw_value
                for message_id, raw_value in messages.items()
                if self._parse_dt((raw_value or {}).get("created_at")) >= cutoff
            }

        requests = payload.get("requests")
        if isinstance(requests, dict):
            compacted_requests: dict[str, Any] = {}
            for request_id, raw_value in requests.items():
                if not isinstance(raw_value, dict):
                    compacted_requests[request_id] = raw_value
                    continue
                updated_at = self._parse_dt(raw_value.get("updated_at"))
                enabled = bool(raw_value.get("enabled", True))
                if not enabled and updated_at < cutoff:
                    continue
                compacted_requests[request_id] = raw_value
            payload["requests"] = compacted_requests

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
