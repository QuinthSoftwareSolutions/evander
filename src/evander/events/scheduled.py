from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.events.internal import InternalEventSource, validate_internal_event_type
from evander.storage import JsonStore


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
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        return validate_internal_event_type(value)

    @field_validator("run_at", "created_at", "published_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

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
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._store = JsonStore(path=self.path, model=ScheduledInternalEvent)
        self._lock = RLock()

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
        normalized_event_type = validate_internal_event_type(event_type)
        normalized_payload = _normalize_payload(payload)
        normalized_correlation_key = _normalize_optional_string(correlation_key)
        normalized_idempotency_key = _normalize_optional_string(idempotency_key)
        normalized_run_at = _normalize_datetime(run_at)
        normalized_created_at = _normalize_datetime(created_at or datetime.now(UTC))
        normalized_source = (
            source
            if isinstance(source, InternalEventSource) or source is None
            else InternalEventSource.model_validate(source)
        )

        with self._lock:
            created = False

            def update(items: dict[str, ScheduledInternalEvent]) -> ScheduledInternalEvent:
                nonlocal created
                if normalized_idempotency_key is not None:
                    for event in items.values():
                        if event.idempotency_key == normalized_idempotency_key:
                            created = False
                            return event

                schedule_id = _schedule_id(normalized_idempotency_key)
                existing = items.get(schedule_id)
                if existing is not None:
                    created = False
                    return existing

                event = ScheduledInternalEvent(
                    schedule_id=schedule_id,
                    event_type=normalized_event_type,
                    payload=normalized_payload,
                    correlation_key=normalized_correlation_key,
                    run_at=normalized_run_at,
                    created_at=normalized_created_at,
                    idempotency_key=normalized_idempotency_key,
                    source=normalized_source,
                )
                items[schedule_id] = event
                created = True
                return event

            event = self._store.update_all(update)
            return event, created

    def list_due(self, now_utc: datetime) -> list[ScheduledInternalEvent]:
        normalized_now = _normalize_datetime(now_utc)
        with self._lock:
            events = list(self._store.read_all().values())
        due = [
            event
            for event in events
            if event.status == "pending" and event.run_at <= normalized_now
        ]
        due.sort(key=lambda event: (event.run_at, event.created_at, event.schedule_id))
        return due

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
        normalized_published_at = _normalize_datetime(published_at)

        with self._lock:

            def update(items: dict[str, ScheduledInternalEvent]) -> ScheduledInternalEvent:
                event = items.get(normalized_schedule_id)
                if event is None:
                    raise KeyError(normalized_schedule_id)
                updated = event.model_copy(
                    update={
                        "status": "published",
                        "published_at": normalized_published_at,
                        "published_event_id": normalized_published_event_id,
                    }
                )
                items[normalized_schedule_id] = updated
                return updated

            return self._store.update_all(update)


def scheduled_internal_event_store_path(runtime_dir: Path | str) -> Path:
    return Path(runtime_dir) / "scheduled-internal-events.json"


def _schedule_id(idempotency_key: str | None) -> str:
    if idempotency_key is None:
        return f"sched-{uuid4()}"
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"sched-{digest[:32]}"


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
