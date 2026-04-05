from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_sandbox.storage import JsonStore
from agent_sandbox.events.models import TriggerEvent, TriggerType

DeliveryState = Literal["pending", "delivered"]


class TriggerQueueRecord(BaseModel):
    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: TriggerType
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery_state: DeliveryState = "pending"
    delivered_at: datetime | None = None
    idempotency_key: str | None = None


class TriggerEventQueue:
    """Durable trigger event queue backed by the shared JSON store conventions."""

    def __init__(self, path: Path | str = Path("runtime") / "trigger-event-queue.json") -> None:
        self.path = Path(path)
        self._store = JsonStore(path=self.path, model=TriggerQueueRecord)
        self._lock = RLock()

    def publish(self, event: TriggerEvent, *, idempotency_key: str | None = None) -> TriggerQueueRecord:
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        with self._lock:
            items = self._store.read_all()
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
                occurred_at=event.occurred_at,
                payload=event.payload,
                delivery_state="pending",
                delivered_at=None,
                idempotency_key=normalized_key,
            )
            items[event.event_id] = created
            self._store.write_all(items)
            return created

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
        trigger_type: TriggerType | None = None,
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

        with self._lock:
            items = self._store.read_all()
            record = items.get(normalized_event_id)
            if record is None:
                return False
            if record.delivery_state == "delivered":
                return True

            record.delivery_state = "delivered"
            record.delivered_at = delivered_at or datetime.now(UTC)
            items[normalized_event_id] = record
            self._store.write_all(items)
            return True

    @staticmethod
    def _normalize_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
