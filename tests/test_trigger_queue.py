from __future__ import annotations

from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from evander.events.models import TriggerEvent
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.events import SqliteWorkflowEventJournal


def _event(*, event_id: str, trigger_id: str = "discord", offset_seconds: int = 0) -> TriggerEvent:
    return TriggerEvent(
        event_id=event_id,
        trigger_id=trigger_id,
        trigger_type="trigger:discord:new-message",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds),
        payload={"message_id": event_id},
    )


def test_publish_and_list_pending(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")

    published = queue.publish(_event(event_id="evt-1"))

    assert published.delivery_state == "pending"
    assert published.trace_id
    pending = queue.list_pending()
    assert [item.event_id for item in pending] == ["evt-1"]


def test_publish_preserves_trace_id_when_provided(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")

    published = queue.publish(
        _event(event_id="evt-1").model_copy(update={"trace_id": "trace-123"}),
    )

    assert published.trace_id == "trace-123"


def test_publish_honors_idempotency_key(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")

    first = queue.publish(_event(event_id="evt-1"), idempotency_key="provider:delivery-1")
    second = queue.publish(_event(event_id="evt-2"), idempotency_key="provider:delivery-1")

    assert second.event_id == first.event_id
    assert [item.event_id for item in queue.list_pending()] == ["evt-1"]


def test_publish_result_atomically_reports_one_creator_under_race(tmp_path) -> None:
    path = tmp_path / "orchestration.db"
    queues = [SqliteTriggerEventQueue(path) for _ in range(8)]
    barrier = Barrier(len(queues))

    def publish(queue):
        barrier.wait()
        return queue.publish_result(
            _event(event_id="evt-race"),
            idempotency_key="provider:race",
        )

    with ThreadPoolExecutor(max_workers=len(queues)) as executor:
        results = list(executor.map(publish, queues))

    assert sum(result.created for result in results) == 1
    assert {result.record.event_id for result in results} == {"evt-race"}
    assert [record.event_id for record in queues[0].list_pending()] == ["evt-race"]


def test_publish_normalizes_blank_idempotency_key(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")

    first = queue.publish(_event(event_id="evt-1"), idempotency_key="   ")
    second = queue.publish(_event(event_id="evt-2"), idempotency_key=None)

    assert first.event_id != second.event_id
    assert [item.event_id for item in queue.list_pending()] == ["evt-1", "evt-2"]


def test_queue_persists_records_on_disk(tmp_path) -> None:
    queue_path = tmp_path / "orchestration.db"
    queue = SqliteTriggerEventQueue(queue_path)
    queue.publish(_event(event_id="evt-1"))

    reloaded = SqliteTriggerEventQueue(queue_path)
    assert reloaded.read_all()["evt-1"].delivery_state == "pending"
    assert [item.event_id for item in reloaded.list_pending()] == ["evt-1"]


def test_list_pending_filters_and_ordering(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")
    queue.publish(_event(event_id="evt-2", trigger_id="discord", offset_seconds=2))
    queue.publish(_event(event_id="evt-1", trigger_id="discord", offset_seconds=1))
    queue.publish(_event(event_id="evt-3", trigger_id="email", offset_seconds=3))

    filtered = queue.list_pending(trigger_id="discord", limit=1)

    assert [item.event_id for item in filtered] == ["evt-1"]


def test_mark_delivered_updates_state(tmp_path) -> None:
    database_path = tmp_path / "orchestration.db"
    queue = SqliteTriggerEventQueue(database_path)
    queue.publish(_event(event_id="evt-1"))

    assert queue.mark_delivered("evt-1") is True
    assert queue.mark_delivered("evt-missing") is False

    assert queue.list_pending() == []
    journal_events, _ = SqliteWorkflowEventJournal(database_path).read_after(0)
    assert [event.event_type for event in journal_events] == [
        "workflow.trigger_event.published",
        "workflow.trigger_event.delivered",
    ]


def test_mark_inflight_and_reset_inflight(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")
    queue.publish(_event(event_id="evt-1"))

    assert queue.mark_inflight("evt-1") is True
    record = queue.read_all()["evt-1"]
    assert record.delivery_state == "inflight"
    assert record.inflight_at is not None
    assert queue.list_pending() == []

    assert queue.reset_inflight() == 1
    record = queue.read_all()["evt-1"]
    assert record.delivery_state == "pending"
    assert record.inflight_at is None
    assert [item.event_id for item in queue.list_pending()] == ["evt-1"]


def test_sqlite_trigger_queue_publish_idempotency_and_transitions(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")

    first = queue.publish(_event(event_id="evt-1"), idempotency_key="provider:delivery-1")
    second = queue.publish(_event(event_id="evt-2"), idempotency_key="provider:delivery-1")

    assert second.event_id == first.event_id
    assert [item.event_id for item in queue.list_pending()] == ["evt-1"]

    assert queue.mark_inflight("evt-1") is True
    assert queue.list_pending() == []
    assert queue.reset_inflight() == 1
    assert [item.event_id for item in queue.list_pending()] == ["evt-1"]
    assert queue.mark_delivered("evt-1") is True
    assert queue.list_pending() == []


def test_sqlite_trigger_queue_filters_pending_with_indexed_order(tmp_path) -> None:
    queue = SqliteTriggerEventQueue(tmp_path / "orchestration.db")
    queue.publish(_event(event_id="evt-2", trigger_id="discord", offset_seconds=2))
    queue.publish(_event(event_id="evt-1", trigger_id="discord", offset_seconds=1))
    queue.publish(_event(event_id="evt-3", trigger_id="email", offset_seconds=3))

    filtered = queue.list_pending(trigger_id="discord", limit=1)

    assert [item.event_id for item in filtered] == ["evt-1"]
