from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.triggers.queue import TriggerEventQueue


def _event(*, event_id: str, trigger_id: str = "discord", offset_seconds: int = 0) -> TriggerEvent:
    return TriggerEvent(
        event_id=event_id,
        trigger_id=trigger_id,
        trigger_type="trigger:discord:new-message",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds),
        payload={"message_id": event_id},
    )


def test_publish_and_list_pending(tmp_path) -> None:
    queue = TriggerEventQueue(tmp_path / "events.json")

    published = queue.publish(_event(event_id="evt-1"))

    assert published.delivery_state == "pending"
    pending = queue.list_pending()
    assert [item.event_id for item in pending] == ["evt-1"]


def test_publish_honors_idempotency_key(tmp_path) -> None:
    queue = TriggerEventQueue(tmp_path / "events.json")

    first = queue.publish(_event(event_id="evt-1"), idempotency_key="provider:delivery-1")
    second = queue.publish(_event(event_id="evt-2"), idempotency_key="provider:delivery-1")

    assert second.event_id == first.event_id
    assert [item.event_id for item in queue.list_pending()] == ["evt-1"]


def test_publish_normalizes_blank_idempotency_key(tmp_path) -> None:
    queue = TriggerEventQueue(tmp_path / "events.json")

    first = queue.publish(_event(event_id="evt-1"), idempotency_key="   ")
    second = queue.publish(_event(event_id="evt-2"), idempotency_key=None)

    assert first.event_id != second.event_id
    assert [item.event_id for item in queue.list_pending()] == ["evt-1", "evt-2"]


def test_queue_persists_records_on_disk(tmp_path) -> None:
    queue_path = tmp_path / "events.json"
    queue = TriggerEventQueue(queue_path)
    queue.publish(_event(event_id="evt-1"))

    raw = json.loads(queue_path.read_text(encoding="utf-8"))
    assert "evt-1" in raw
    assert raw["evt-1"]["delivery_state"] == "pending"

    reloaded = TriggerEventQueue(queue_path)
    assert [item.event_id for item in reloaded.list_pending()] == ["evt-1"]


def test_list_pending_filters_and_ordering(tmp_path) -> None:
    queue = TriggerEventQueue(tmp_path / "events.json")
    queue.publish(_event(event_id="evt-2", trigger_id="discord", offset_seconds=2))
    queue.publish(_event(event_id="evt-1", trigger_id="discord", offset_seconds=1))
    queue.publish(_event(event_id="evt-3", trigger_id="email", offset_seconds=3))

    filtered = queue.list_pending(trigger_id="discord", limit=1)

    assert [item.event_id for item in filtered] == ["evt-1"]


def test_mark_delivered_updates_state(tmp_path) -> None:
    queue = TriggerEventQueue(tmp_path / "events.json")
    queue.publish(_event(event_id="evt-1"))

    assert queue.mark_delivered("evt-1") is True
    assert queue.mark_delivered("evt-missing") is False

    assert queue.list_pending() == []
