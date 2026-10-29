from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.events.internal import InternalEventSource, validate_internal_event_type
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
)


class WakeNotifier(Protocol):
    def notify(self) -> None: ...


class ScheduledInternalEvent(BaseModel):
    schedule_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_key: str | None = None
    run_at: datetime
    status: Literal["pending", "published"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    published_event_id: str | None = None
    idempotency_key: str | None = None
    source: InternalEventSource | None = None

    @field_validator(
        "schedule_id", "published_event_id", "correlation_key", "idempotency_key", mode="before"
    )
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        return _normalize_optional_string(value)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        return validate_internal_event_type(value)

    @field_validator("run_at", "created_at", "published_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: object | None) -> InternalEventSource | None:
        if value is None or isinstance(value, InternalEventSource):
            return value
        return InternalEventSource.model_validate(value)

    @model_validator(mode="after")
    def validate_publication_state(self) -> "ScheduledInternalEvent":
        if self.status == "pending":
            if self.published_at is not None or self.published_event_id is not None:
                raise ValueError(
                    "pending scheduled internal events must not include publication metadata"
                )
        elif self.published_at is None or self.published_event_id is None:
            raise ValueError(
                "published scheduled internal events require published_at and published_event_id"
            )
        return self


class ScheduledInternalEventStore:
    def __init__(
        self,
        database_path: Path | str,
        *,
        wake_notifier: WakeNotifier | None = None,
    ) -> None:
        self.path = Path(database_path)
        ensure_orchestration_schema(self.path)
        self._wake_notifier = wake_notifier

    def schedule_event(
        self,
        *,
        event_type: object,
        payload: Mapping[str, Any] | None = None,
        correlation_key: object | None = None,
        run_at: datetime,
        idempotency_key: str | None = None,
        source: InternalEventSource | dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> tuple[ScheduledInternalEvent, bool]:
        normalized_idempotency_key = _normalize_optional_string(idempotency_key)
        event = ScheduledInternalEvent(
            schedule_id=_schedule_id(normalized_idempotency_key),
            event_type=validate_internal_event_type(event_type),
            payload=_normalize_payload(payload),
            correlation_key=_normalize_optional_string(correlation_key),
            run_at=_normalize_datetime(run_at),
            created_at=_normalize_datetime(created_at or datetime.now(UTC)),
            idempotency_key=normalized_idempotency_key,
            source=(
                source
                if isinstance(source, InternalEventSource) or source is None
                else InternalEventSource.model_validate(source)
            ),
        )
        created = False
        with connect_orchestration_db(self.path) as connection:
            if normalized_idempotency_key is not None:
                existing = connection.execute(
                    "SELECT * FROM scheduled_internal_events WHERE idempotency_key = ?",
                    (normalized_idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return _scheduled_from_row(existing), False
            try:
                connection.execute(
                    """
                    INSERT INTO scheduled_internal_events(
                        schedule_id, event_type, correlation_key, run_at, status,
                        created_at, published_at, published_event_id, idempotency_key,
                        source_json, payload_json
                    ) VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        event.schedule_id,
                        event.event_type,
                        event.correlation_key,
                        datetime_to_storage(event.run_at),
                        datetime_to_storage(event.created_at),
                        event.idempotency_key,
                        json_dumps(event.source.model_dump(mode="json"))
                        if event.source is not None
                        else None,
                        json_dumps(event.payload),
                    ),
                )
                created = True
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    """
                    SELECT * FROM scheduled_internal_events
                    WHERE schedule_id = ? OR idempotency_key = ?
                    """,
                    (event.schedule_id, normalized_idempotency_key),
                ).fetchone()
                if existing is None:
                    raise
                event = _scheduled_from_row(existing)
        if created and self._wake_notifier is not None:
            self._wake_notifier.notify()
        return event, created

    def list_due(self, now_utc: datetime) -> list[ScheduledInternalEvent]:
        with connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM scheduled_internal_events
                WHERE status = 'pending' AND run_at <= ?
                ORDER BY run_at, created_at, schedule_id
                """,
                (datetime_to_storage(_normalize_datetime(now_utc)),),
            ).fetchall()
        return [_scheduled_from_row(row) for row in rows]

    def list_due_ids(self, now_utc: datetime, *, limit: int) -> list[str]:
        """Select a bounded publication batch without decoding scheduled payloads."""
        if limit <= 0:
            return []
        with connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT schedule_id FROM scheduled_internal_events
                WHERE status = 'pending' AND run_at <= ?
                ORDER BY run_at, created_at, schedule_id
                LIMIT ?
                """,
                (datetime_to_storage(_normalize_datetime(now_utc)), limit),
            ).fetchall()
        return [str(row["schedule_id"]) for row in rows]

    def import_event(self, event: ScheduledInternalEvent) -> None:
        with connect_orchestration_db(self.path) as connection:
            existing = connection.execute(
                "SELECT * FROM scheduled_internal_events WHERE schedule_id = ?",
                (event.schedule_id,),
            ).fetchone()
            if existing is not None:
                if _scheduled_from_row(existing) != event:
                    raise ValueError(f"conflicting scheduled event {event.schedule_id}")
                return
            connection.execute(
                """
                INSERT INTO scheduled_internal_events(
                    schedule_id, event_type, correlation_key, run_at, status,
                    created_at, published_at, published_event_id, idempotency_key,
                    source_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.schedule_id,
                    event.event_type,
                    event.correlation_key,
                    datetime_to_storage(event.run_at),
                    event.status,
                    datetime_to_storage(event.created_at),
                    datetime_to_storage(event.published_at),
                    event.published_event_id,
                    event.idempotency_key,
                    json_dumps(event.source.model_dump(mode="json"))
                    if event.source is not None
                    else None,
                    json_dumps(event.payload),
                ),
            )

    def list_all(self) -> list[ScheduledInternalEvent]:
        with connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                "SELECT * FROM scheduled_internal_events ORDER BY created_at, schedule_id"
            ).fetchall()
        return [_scheduled_from_row(row) for row in rows]

    def next_pending_run_at(self) -> datetime | None:
        with connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                """
                SELECT run_at FROM scheduled_internal_events
                WHERE status = 'pending'
                ORDER BY run_at, created_at, schedule_id
                LIMIT 1
                """
            ).fetchone()
        return _datetime_from_storage(row["run_at"]) if row is not None else None

    def mark_published(
        self,
        schedule_id: str,
        published_event_id: str,
        published_at: datetime,
    ) -> ScheduledInternalEvent:
        normalized_schedule_id = _normalize_required_string(schedule_id, field_name="schedule_id")
        normalized_published_event_id = _normalize_required_string(
            published_event_id, field_name="published_event_id"
        )
        with connect_orchestration_db(self.path) as connection:
            result = connection.execute(
                """
                UPDATE scheduled_internal_events
                SET status = 'published', published_at = ?, published_event_id = ?
                WHERE schedule_id = ?
                """,
                (
                    datetime_to_storage(_normalize_datetime(published_at)),
                    normalized_published_event_id,
                    normalized_schedule_id,
                ),
            )
            if result.rowcount == 0:
                raise KeyError(normalized_schedule_id)
            row = connection.execute(
                "SELECT * FROM scheduled_internal_events WHERE schedule_id = ?",
                (normalized_schedule_id,),
            ).fetchone()
        assert row is not None
        return _scheduled_from_row(row)


def _scheduled_from_row(row: sqlite3.Row) -> ScheduledInternalEvent:
    source_payload = json_loads_object(row["source_json"])
    return ScheduledInternalEvent(
        schedule_id=str(row["schedule_id"]),
        event_type=str(row["event_type"]),
        payload=json_loads_object(row["payload_json"]),
        correlation_key=_normalize_optional_string(row["correlation_key"]),
        run_at=_datetime_from_storage(row["run_at"]),
        status=str(row["status"]),
        created_at=_datetime_from_storage(row["created_at"]),
        published_at=(
            _datetime_from_storage(row["published_at"]) if row["published_at"] is not None else None
        ),
        published_event_id=_normalize_optional_string(row["published_event_id"]),
        idempotency_key=_normalize_optional_string(row["idempotency_key"]),
        source=InternalEventSource.model_validate(source_payload) if source_payload else None,
    )


def _schedule_id(idempotency_key: str | None) -> str:
    if idempotency_key is None:
        return f"sched-{uuid4()}"
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"sched-{digest[:32]}"


def _datetime_from_storage(value: object) -> datetime:
    return _normalize_datetime(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize_optional_string(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_payload(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("payload must be a JSON object")
    return dict(value)


def _normalize_required_string(value: object, *, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text
