from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.storage import JsonStore
from agent_sandbox.workflow.events import WorkflowEventJournal, workflow_event_journal_path

_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class InternalEventValidationError(ValueError):
    """Raised when an operator-defined internal event is invalid."""


class InternalEventSource(BaseModel):
    workflow_run_id: str | None = None
    workflow_event_id: str | None = None
    workflow_trace_id: str | None = None
    node_id: str | None = None
    node_type: str | None = None


class InternalEvent(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_key: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: InternalEventSource | None = None
    idempotency_key: str | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        return validate_internal_event_type(value)

    @field_validator("correlation_key", "idempotency_key", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def validate_internal_event_type(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise InternalEventValidationError("event_type must be a non-empty string")
    if not _EVENT_TYPE_RE.fullmatch(text):
        raise InternalEventValidationError(
            "event_type must start with an alphanumeric character and contain only "
            "letters, numbers, '.', '_', ':', or '-'"
        )
    return text


class InternalEventBus:
    """Durable JSON-backed bus for generic operator-defined internal events."""

    def __init__(
        self,
        path: Path | str,
        *,
        journal_path: Path | str | None = None,
    ) -> None:
        self.path = Path(path)
        self._store = JsonStore(path=self.path, model=InternalEvent)
        self._journal = WorkflowEventJournal(
            journal_path or workflow_event_journal_path(self.path.parent)
        )
        self._lock = RLock()

    def publish(
        self,
        *,
        event_type: object,
        payload: dict[str, Any] | None = None,
        correlation_key: object | None = None,
        source: InternalEventSource | dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        created_at: datetime | None = None,
    ) -> InternalEvent:
        normalized_type = validate_internal_event_type(event_type)
        normalized_correlation = _normalize_optional_string(correlation_key)
        normalized_idempotency = _normalize_optional_string(idempotency_key)
        normalized_payload = dict(payload or {})
        normalized_source = (
            source
            if isinstance(source, InternalEventSource) or source is None
            else InternalEventSource.model_validate(source)
        )
        published = False
        with self._lock:
            def update(items: dict[str, InternalEvent]) -> InternalEvent:
                nonlocal published
                if normalized_idempotency is not None:
                    for event in items.values():
                        if event.idempotency_key == normalized_idempotency:
                            return event
                event_id = _event_id(
                    event_type=normalized_type,
                    payload=normalized_payload,
                    correlation_key=normalized_correlation,
                    idempotency_key=normalized_idempotency,
                )
                existing = items.get(event_id)
                if existing is not None:
                    return existing
                event = InternalEvent(
                    event_id=event_id,
                    event_type=normalized_type,
                    payload=normalized_payload,
                    correlation_key=normalized_correlation,
                    created_at=created_at or datetime.now(UTC),
                    source=normalized_source,
                    idempotency_key=normalized_idempotency,
                )
                items[event.event_id] = event
                published = True
                return event

            event = self._store.update_all(update)
            if not published:
                return event
            self._journal.append(
                "workflow.internal_event.published",
                source="internal_event_bus",
                subject_id=event.event_id,
                payload={
                    "event_type": event.event_type,
                    "correlation_key": event.correlation_key,
                    "idempotency_key": event.idempotency_key,
                },
            )
            return event

    def list_events(self) -> list[InternalEvent]:
        with self._lock:
            events = list(self._store.read_all().values())
        events.sort(key=lambda event: (event.created_at, event.event_id))
        return events


def internal_event_store_path(runtime_dir: Path | str) -> Path:
    return Path(runtime_dir) / "internal-events.json"


def _event_id(
    *,
    event_type: str,
    payload: dict[str, Any],
    correlation_key: str | None,
    idempotency_key: str | None,
) -> str:
    if idempotency_key is None:
        return f"evt-{uuid4()}"
    material = json.dumps(
        {
            "event_type": event_type,
            "payload": payload,
            "correlation_key": correlation_key,
            "idempotency_key": idempotency_key,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"evt-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def _normalize_optional_string(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
