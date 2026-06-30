from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

from pytest import MonkeyPatch

from evander.events.internal import InternalEventBus
from evander.events.scheduled import ScheduledInternalEventStore
from evander.storage import JsonStore


def test_internal_event_publish_uses_single_store_update(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    bus = InternalEventBus(tmp_path / "internal-events.json")

    def fail_write_all(*args, **kwargs) -> None:
        raise AssertionError("publish must not use split read/write store updates")

    monkeypatch.setattr(bus._store, "write_all", fail_write_all)

    event = bus.publish(event_type="custom.operator_event", payload={"value": "ok"})

    assert [stored.event_id for stored in bus.list_events()] == [event.event_id]


def test_concurrent_internal_event_publish_preserves_both_events(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    event_path = tmp_path / "internal-events.json"
    journal_path = tmp_path / "workflow-events.jsonl"
    start = Barrier(3)
    read_barrier = Barrier(2)
    failures: list[str] = []
    original_read_all = JsonStore.read_all

    def delayed_read_all(self):
        items = original_read_all(self)
        if self.path == event_path:
            read_barrier.wait(timeout=2)
        return items

    def publish(value: str) -> None:
        try:
            bus = InternalEventBus(event_path, journal_path=journal_path)
            start.wait(timeout=2)
            bus.publish(event_type="custom.concurrent", payload={"value": value})
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            failures.append(repr(exc))

    with monkeypatch.context() as context:
        context.setattr(JsonStore, "read_all", delayed_read_all)
        threads = [
            Thread(target=publish, args=("one",)),
            Thread(target=publish, args=("two",)),
        ]
        for thread in threads:
            thread.start()
        start.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()

    assert failures == []
    events = InternalEventBus(event_path, journal_path=journal_path).list_events()
    assert sorted(event.payload["value"] for event in events) == ["one", "two"]

    journal_events = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [event["event_type"] for event in journal_events].count(
        "workflow.internal_event.published"
    ) == 2


def test_scheduled_internal_event_store_uses_single_store_update(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store = ScheduledInternalEventStore(tmp_path / "scheduled-internal-events.json")

    def fail_write_all(*args, **kwargs) -> None:
        raise AssertionError("schedule_event must not use split read/write store updates")

    monkeypatch.setattr(store._store, "write_all", fail_write_all)

    scheduled, created = store.schedule_event(
        event_type="custom.operator_event",
        payload={"value": "ok"},
        run_at=datetime.now(UTC),
        idempotency_key="schedule-1",
    )
    published = store.mark_published(
        scheduled.schedule_id,
        "evt-1",
        datetime.now(UTC),
    )

    assert created is True
    assert published.status == "published"
    assert published.published_event_id == "evt-1"


def test_duplicate_schedule_idempotency_returns_original_record_without_mutation(
    tmp_path: Path,
) -> None:
    store = ScheduledInternalEventStore(tmp_path / "scheduled-internal-events.json")
    run_at = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)

    original, created = store.schedule_event(
        event_type="custom.operator_event",
        payload={"value": "original"},
        correlation_key="corr-1",
        run_at=run_at,
        idempotency_key="schedule-1",
        created_at=datetime(2030, 1, 1, 11, 0, tzinfo=UTC),
    )
    duplicate, duplicate_created = store.schedule_event(
        event_type="custom.changed_event",
        payload={"value": "changed"},
        correlation_key="corr-2",
        run_at=run_at + timedelta(hours=1),
        idempotency_key="schedule-1",
        created_at=datetime(2030, 1, 1, 11, 30, tzinfo=UTC),
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate == original
    assert duplicate.event_type == "custom.operator_event"
    assert duplicate.payload == {"value": "original"}
    assert duplicate.correlation_key == "corr-1"
    assert duplicate.run_at == run_at


def test_due_scheduled_events_are_sorted_by_run_at_then_created_at(tmp_path: Path) -> None:
    store = ScheduledInternalEventStore(tmp_path / "scheduled-internal-events.json")
    now = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)

    store.schedule_event(
        event_type="custom.operator_event",
        payload={"value": "third"},
        run_at=now - timedelta(minutes=15),
        idempotency_key="third",
        created_at=now - timedelta(minutes=30),
    )
    store.schedule_event(
        event_type="custom.operator_event",
        payload={"value": "first"},
        run_at=now - timedelta(minutes=10),
        idempotency_key="first",
        created_at=now - timedelta(minutes=20),
    )
    store.schedule_event(
        event_type="custom.operator_event",
        payload={"value": "second"},
        run_at=now - timedelta(minutes=10),
        idempotency_key="second",
        created_at=now - timedelta(minutes=10),
    )

    due = store.list_due(now)

    assert [event.payload["value"] for event in due] == ["third", "first", "second"]
