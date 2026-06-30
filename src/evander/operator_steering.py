from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.models import utc_now

SQLITE_BUSY_TIMEOUT_MS = 30_000
OPERATOR_STEERING_NOTE_MAX_LENGTH = 2000
OPERATOR_STEERING_INJECTING_STALE_AFTER = timedelta(minutes=5)

OperatorSteeringNoteStatus = Literal[
    "pending",
    "injecting",
    "delivered",
    "cancelled",
    "failed",
]


def _storage_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_text(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class OperatorSteeringNote(BaseModel):
    note_id: str = Field(default_factory=lambda: f"steer-{uuid4().hex}")
    session_id: str
    dispatch_id: str
    message: str
    status: OperatorSteeringNoteStatus = "pending"
    worker_execution_id: str | None = None
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    provider: str | None = None
    provider_endpoint: str | None = None
    error: str | None = None
    delivery_attempts: int = 0

    @field_validator(
        "note_id",
        "session_id",
        "dispatch_id",
        "status",
        mode="before",
    )
    @classmethod
    def normalize_required_strings(cls, value: object, info) -> str:
        text = _required_text(value, field_name=info.field_name)
        return text.lower() if info.field_name == "status" else text

    @field_validator(
        "worker_execution_id",
        "created_by",
        "provider",
        "provider_endpoint",
        "error",
        mode="before",
    )
    @classmethod
    def normalize_optional_strings(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: object) -> str:
        text = _required_text(value, field_name="message")
        if len(text) > OPERATOR_STEERING_NOTE_MAX_LENGTH:
            raise ValueError(
                f"message must be at most {OPERATOR_STEERING_NOTE_MAX_LENGTH} characters"
            )
        return text

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_required_datetimes(cls, value: object) -> datetime:
        parsed = _parse_datetime(value)
        if parsed is None:
            raise ValueError("datetime is required")
        return parsed

    @field_validator("delivered_at", "cancelled_at", mode="before")
    @classmethod
    def normalize_optional_datetimes(cls, value: object) -> datetime | None:
        return _parse_datetime(value)

    @field_validator("delivery_attempts", mode="before")
    @classmethod
    def normalize_delivery_attempts(cls, value: object) -> int:
        if value is None or value == "":
            return 0
        attempts = int(value)
        if attempts < 0:
            raise ValueError("delivery_attempts must be greater than or equal to 0")
        return attempts

    @model_validator(mode="after")
    def validate_status_timestamps(self) -> "OperatorSteeringNote":
        if self.status == "delivered" and self.delivered_at is None:
            raise ValueError("delivered notes require delivered_at")
        if self.status == "cancelled" and self.cancelled_at is None:
            raise ValueError("cancelled notes require cancelled_at")
        return self


class OperatorSteeringNoteStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS operator_steering_notes (
                    note_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    worker_execution_id TEXT,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    delivered_at TEXT,
                    cancelled_at TEXT,
                    provider TEXT,
                    provider_endpoint TEXT,
                    error TEXT,
                    delivery_attempts INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_operator_steering_session_dispatch_status
                    ON operator_steering_notes(session_id, dispatch_id, status, created_at, note_id);
                CREATE INDEX IF NOT EXISTS idx_operator_steering_status_updated
                    ON operator_steering_notes(status, updated_at, note_id);
                CREATE INDEX IF NOT EXISTS idx_operator_steering_worker_execution
                    ON operator_steering_notes(worker_execution_id, created_at, note_id);
                """
            )

    def create_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
        now: datetime | None = None,
    ) -> OperatorSteeringNote:
        timestamp = now or utc_now()
        note = OperatorSteeringNote(
            session_id=session_id,
            dispatch_id=dispatch_id,
            worker_execution_id=worker_execution_id,
            message=message,
            created_by=created_by,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO operator_steering_notes(
                    note_id,
                    session_id,
                    dispatch_id,
                    worker_execution_id,
                    status,
                    message,
                    created_by,
                    created_at,
                    updated_at,
                    delivered_at,
                    cancelled_at,
                    provider,
                    provider_endpoint,
                    error,
                    delivery_attempts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._row_values(note),
            )
        return note

    def create_delivered_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        provider: str,
        provider_endpoint: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
        now: datetime | None = None,
    ) -> OperatorSteeringNote:
        timestamp = now or utc_now()
        note = OperatorSteeringNote(
            session_id=session_id,
            dispatch_id=dispatch_id,
            worker_execution_id=worker_execution_id,
            message=message,
            status="delivered",
            created_by=created_by,
            created_at=timestamp,
            updated_at=timestamp,
            delivered_at=timestamp,
            provider=provider,
            provider_endpoint=provider_endpoint,
            delivery_attempts=1,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO operator_steering_notes(
                    note_id,
                    session_id,
                    dispatch_id,
                    worker_execution_id,
                    status,
                    message,
                    created_by,
                    created_at,
                    updated_at,
                    delivered_at,
                    cancelled_at,
                    provider,
                    provider_endpoint,
                    error,
                    delivery_attempts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._row_values(note),
            )
        return note

    def get_note(self, note_id: object) -> OperatorSteeringNote | None:
        normalized_note_id = _optional_text(note_id)
        if normalized_note_id is None:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM operator_steering_notes WHERE note_id = ?",
                (normalized_note_id,),
            ).fetchone()
        return self._note_from_row(row) if row is not None else None

    def list_notes(
        self,
        *,
        session_id: str,
        dispatch_id: str | None = None,
        limit: int = 50,
    ) -> list[OperatorSteeringNote]:
        normalized_session_id = _required_text(session_id, field_name="session_id")
        normalized_dispatch_id = _optional_text(dispatch_id)
        bounded_limit = max(1, min(200, int(limit)))
        sql = ["SELECT * FROM operator_steering_notes WHERE session_id = ?"]
        params: list[object] = [normalized_session_id]
        if normalized_dispatch_id is not None:
            sql.append(" AND dispatch_id = ?")
            params.append(normalized_dispatch_id)
        sql.append(" ORDER BY created_at DESC, note_id DESC LIMIT ?")
        params.append(bounded_limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute("".join(sql), params).fetchall()
        return [self._note_from_row(row) for row in rows]

    def cancel_note(
        self,
        note_id: object,
        *,
        now: datetime | None = None,
    ) -> OperatorSteeringNote | None:
        normalized_note_id = _optional_text(note_id)
        if normalized_note_id is None:
            return None
        timestamp = now or utc_now()
        timestamp_text = _storage_datetime(timestamp)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE operator_steering_notes
                SET status = 'cancelled',
                    cancelled_at = ?,
                    updated_at = ?,
                    error = NULL
                WHERE note_id = ?
                  AND status = 'pending'
                """,
                (timestamp_text, timestamp_text, normalized_note_id),
            )
            row = connection.execute(
                "SELECT * FROM operator_steering_notes WHERE note_id = ?",
                (normalized_note_id,),
            ).fetchone()
        return self._note_from_row(row) if row is not None else None

    def claim_next_pending(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        now: datetime | None = None,
        stale_after: timedelta = OPERATOR_STEERING_INJECTING_STALE_AFTER,
    ) -> OperatorSteeringNote | None:
        normalized_session_id = _required_text(session_id, field_name="session_id")
        normalized_dispatch_id = _required_text(dispatch_id, field_name="dispatch_id")
        timestamp = now or utc_now()
        timestamp_text = _storage_datetime(timestamp)
        stale_cutoff = timestamp - stale_after
        stale_cutoff_text = _storage_datetime(stale_cutoff)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE operator_steering_notes
                SET status = 'pending',
                    updated_at = ?,
                    error = 'delivery claim expired before injection completed'
                WHERE status = 'injecting'
                  AND updated_at < ?
                """,
                (timestamp_text, stale_cutoff_text),
            )
            row = connection.execute(
                """
                SELECT *
                FROM operator_steering_notes
                WHERE session_id = ?
                  AND dispatch_id = ?
                  AND status = 'pending'
                ORDER BY created_at ASC, note_id ASC
                LIMIT 1
                """,
                (normalized_session_id, normalized_dispatch_id),
            ).fetchone()
            if row is None:
                return None
            note_id = row["note_id"]
            connection.execute(
                """
                UPDATE operator_steering_notes
                SET status = 'injecting',
                    updated_at = ?,
                    error = NULL,
                    delivery_attempts = delivery_attempts + 1
                WHERE note_id = ?
                  AND status = 'pending'
                """,
                (timestamp_text, note_id),
            )
            claimed = connection.execute(
                "SELECT * FROM operator_steering_notes WHERE note_id = ?",
                (note_id,),
            ).fetchone()
        return self._note_from_row(claimed) if claimed is not None else None

    def mark_delivered(
        self,
        note_id: object,
        *,
        provider: str,
        provider_endpoint: str,
        now: datetime | None = None,
    ) -> OperatorSteeringNote | None:
        normalized_note_id = _optional_text(note_id)
        if normalized_note_id is None:
            return None
        timestamp = now or utc_now()
        timestamp_text = _storage_datetime(timestamp)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE operator_steering_notes
                SET status = 'delivered',
                    delivered_at = ?,
                    updated_at = ?,
                    provider = ?,
                    provider_endpoint = ?,
                    error = NULL
                WHERE note_id = ?
                  AND status = 'injecting'
                """,
                (
                    timestamp_text,
                    timestamp_text,
                    _optional_text(provider),
                    _optional_text(provider_endpoint),
                    normalized_note_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM operator_steering_notes WHERE note_id = ?",
                (normalized_note_id,),
            ).fetchone()
        return self._note_from_row(row) if row is not None else None

    def release_claim(
        self,
        note_id: object,
        *,
        error: str | None = None,
        now: datetime | None = None,
    ) -> OperatorSteeringNote | None:
        normalized_note_id = _optional_text(note_id)
        if normalized_note_id is None:
            return None
        timestamp = now or utc_now()
        timestamp_text = _storage_datetime(timestamp)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE operator_steering_notes
                SET status = 'pending',
                    updated_at = ?,
                    error = ?
                WHERE note_id = ?
                  AND status = 'injecting'
                """,
                (timestamp_text, _optional_text(error), normalized_note_id),
            )
            row = connection.execute(
                "SELECT * FROM operator_steering_notes WHERE note_id = ?",
                (normalized_note_id,),
            ).fetchone()
        return self._note_from_row(row) if row is not None else None

    @staticmethod
    def _row_values(note: OperatorSteeringNote) -> tuple[object, ...]:
        return (
            note.note_id,
            note.session_id,
            note.dispatch_id,
            note.worker_execution_id,
            note.status,
            note.message,
            note.created_by,
            _storage_datetime(note.created_at),
            _storage_datetime(note.updated_at),
            _storage_datetime(note.delivered_at),
            _storage_datetime(note.cancelled_at),
            note.provider,
            note.provider_endpoint,
            note.error,
            note.delivery_attempts,
        )

    @staticmethod
    def _note_from_row(row: sqlite3.Row) -> OperatorSteeringNote:
        return OperatorSteeringNote(
            note_id=row["note_id"],
            session_id=row["session_id"],
            dispatch_id=row["dispatch_id"],
            worker_execution_id=row["worker_execution_id"],
            status=row["status"],
            message=row["message"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            delivered_at=row["delivered_at"],
            cancelled_at=row["cancelled_at"],
            provider=row["provider"],
            provider_endpoint=row["provider_endpoint"],
            error=row["error"],
            delivery_attempts=row["delivery_attempts"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


def operator_steering_note_store(runtime_dir: Path | str) -> OperatorSteeringNoteStore:
    return OperatorSteeringNoteStore(Path(runtime_dir) / "operator-steering-notes.db")
