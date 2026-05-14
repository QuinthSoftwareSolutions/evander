from __future__ import annotations

from uuid import uuid4
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_sandbox.storage import JsonStore
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.workflow.events import WorkflowEventJournal, workflow_event_journal_path

DeliveryState = Literal["pending", "inflight", "delivered"]


class TriggerQueueRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: str
    trace_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery_state: DeliveryState = "pending"
    inflight_at: datetime | None = None
    delivered_at: datetime | None = None
    idempotency_key: str | None = None
    clear_reason: str | None = None
    error: str | None = None


class TriggerEventQueue:
    """Durable trigger event queue backed by the shared JSON store conventions."""

    def __init__(
        self,
        path: Path | str = Path("runtime") / "trigger-event-queue.json",
        *,
        journal_path: Path | str | None = None,
    ) -> None:
        self.path = Path(path)
        self._store = JsonStore(path=self.path, model=TriggerQueueRecord)
        self._journal = WorkflowEventJournal(
            journal_path or workflow_event_journal_path(self.path.parent)
        )
        self._lock = RLock()

    def publish(
        self, event: TriggerEvent, *, idempotency_key: str | None = None
    ) -> TriggerQueueRecord:
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        trace_id = self._normalize_trace_id(event.trace_id)
        published = False
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> TriggerQueueRecord:
                nonlocal published
                if normalized_key is not None:
                    for record in items.values():
                        if record.idempotency_key == normalized_key:
                            return record
                existing = items.get(event.event_id)
                if existing is not None:
                    return existing

                created = TriggerQueueRecord(
                    event_id=event.event_id,
                    trigger_id=event.trigger_id,
                    trigger_type=event.trigger_type,
                    trace_id=trace_id,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                    delivery_state="pending",
                    delivered_at=None,
                    idempotency_key=normalized_key,
                )
                items[event.event_id] = created
                published = True
                return created

            record = self._store.update_all(update)
            if not published:
                return record
            self._journal.append(
                "workflow.trigger_event.published",
                source="trigger_queue",
                subject_id=event.event_id,
                payload={"trigger_id": event.trigger_id, "trigger_type": event.trigger_type},
            )
            return record

    def contains(self, event_id: str) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        with self._lock:
            return normalized_event_id in self._store.read_all()

    def list_pending(
        self,
        *,
        trigger_id: str | None = None,
        trigger_type: str | None = None,
        limit: int | None = None,
    ) -> list[TriggerQueueRecord]:
        if limit is not None and limit <= 0:
            return []

        with self._lock:
            pending = [
                record
                for record in self._store.read_all().values()
                if record.delivery_state == "pending"
                and (trigger_id is None or record.trigger_id == trigger_id)
                and (trigger_type is None or record.trigger_type == trigger_type)
            ]

        pending.sort(key=lambda record: (record.occurred_at, record.event_id))
        if limit is not None:
            return pending[:limit]
        return pending

    def mark_delivered(self, event_id: str, *, delivered_at: datetime | None = None) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        delivered = False
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> bool:
                nonlocal delivered
                record = items.get(normalized_event_id)
                if record is None:
                    return False
                if record.delivery_state == "delivered":
                    return True

                record.delivery_state = "delivered"
                record.inflight_at = None
                record.delivered_at = delivered_at or datetime.now(UTC)
                items[normalized_event_id] = record
                delivered = True
                return True

            found = self._store.update_all(update)
            if not found:
                return False
            if not delivered:
                return True
            self._journal.append(
                "workflow.trigger_event.delivered",
                source="trigger_queue",
                subject_id=normalized_event_id,
            )
            return True

    def mark_inflight(self, event_id: str, *, inflight_at: datetime | None = None) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        marked = False
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> bool:
                nonlocal marked
                record = items.get(normalized_event_id)
                if record is None:
                    return False
                if record.delivery_state == "delivered":
                    return False

                record.delivery_state = "inflight"
                record.inflight_at = inflight_at or datetime.now(UTC)
                record.delivered_at = None
                items[normalized_event_id] = record
                marked = True
                return True

            if not self._store.update_all(update):
                return False
            if not marked:
                return False
            self._journal.append(
                "workflow.trigger_event.inflight",
                source="trigger_queue",
                subject_id=normalized_event_id,
            )
            return True

    def reset_inflight(self) -> int:
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> int:
                reset_count = 0
                for event_id, record in items.items():
                    if record.delivery_state != "inflight":
                        continue
                    record.delivery_state = "pending"
                    record.inflight_at = None
                    record.delivered_at = None
                    items[event_id] = record
                    reset_count += 1
                return reset_count

            reset_count = self._store.update_all(update)
            if reset_count > 0:
                self._journal.append(
                    "workflow.trigger_event.reset_inflight",
                    source="trigger_queue",
                    payload={"reset_count": reset_count},
                )
        return reset_count

    @staticmethod
    def _normalize_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_trace_id(value: str | None) -> str:
        if value is None:
            return str(uuid4())
        normalized = value.strip()
        return normalized or str(uuid4())
