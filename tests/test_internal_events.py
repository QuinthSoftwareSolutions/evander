from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

from config_helpers import canonical_worker_config
import pytest

from evander.events.internal import InternalEventBus, InternalEventSubscriptionSpec
from evander.events.scheduled import ScheduledInternalEventStore
from evander.orchestration import internal_events as internal_event_builders
from evander.orchestration.host_polling import wake_socket_path_for_lock
from evander.orchestration.internal_events import (
    build_internal_event_bus,
    build_scheduled_internal_event_store,
)
from evander.repositories.orchestration_db import run_orchestration_retention
from evander.workflow.events import SqliteWorkflowEventJournal


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls = 0

    def notify(self) -> None:
        self.calls += 1


def _subscription(
    *, trigger_id: str = "listener", correlation_key: str | None = None
) -> InternalEventSubscriptionSpec:
    return InternalEventSubscriptionSpec(
        trigger_id=trigger_id,
        trigger_type="trigger:event",
        event_type="custom.operator_event",
        filter_payload={
            "event_type": "custom.operator_event",
            "correlation_key": correlation_key,
        },
    )


def test_internal_event_publish_is_atomic_with_workflow_journal(tmp_path: Path) -> None:
    database_path = tmp_path / "orchestration.db"
    bus = InternalEventBus(database_path)

    event = bus.publish(event_type="custom.operator_event", payload={"value": "ok"})

    assert [stored.event_id for stored in bus.list_events()] == [event.event_id]
    journal_events, _ = SqliteWorkflowEventJournal(database_path).read_after(0)
    assert [entry.event_type for entry in journal_events] == ["workflow.internal_event.published"]
    assert journal_events[0].subject_id == event.event_id


def test_concurrent_internal_event_publish_preserves_both_events(tmp_path: Path) -> None:
    database_path = tmp_path / "orchestration.db"
    start = Barrier(3)
    failures: list[str] = []

    def publish(value: str) -> None:
        try:
            bus = InternalEventBus(database_path)
            start.wait(timeout=2)
            bus.publish(event_type="custom.concurrent", payload={"value": value})
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            failures.append(repr(exc))

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
    events = InternalEventBus(database_path).list_events()
    assert sorted(event.payload["value"] for event in events) == ["one", "two"]
    journal_events, _ = SqliteWorkflowEventJournal(database_path).read_after(0)
    assert [event.event_type for event in journal_events].count(
        "workflow.internal_event.published"
    ) == 2


def test_internal_event_publish_notifies_only_for_new_event(tmp_path: Path) -> None:
    notifier = _RecordingNotifier()
    bus = InternalEventBus(tmp_path / "orchestration.db", wake_notifier=notifier)

    first = bus.publish(
        event_type="custom.operator_event",
        idempotency_key="operator-event-1",
    )
    duplicate = bus.publish(
        event_type="custom.changed_event",
        idempotency_key="operator-event-1",
    )

    assert duplicate == first
    assert notifier.calls == 1


def test_new_subscription_is_future_only_and_acknowledges_delivery(tmp_path: Path) -> None:
    bus = InternalEventBus(tmp_path / "orchestration.db")
    spec = _subscription()
    bus.publish(event_type=spec.event_type, payload={"value": "before"})
    bus.reconcile_subscription(spec, config_revision="revision-1")

    assert bus.poll_subscription(spec, matcher=lambda _event: True) == []

    after = bus.publish(event_type=spec.event_type, payload={"value": "after"})
    assert [
        event.event_id for event in bus.poll_subscription(spec, matcher=lambda _event: True)
    ] == [after.event_id]

    bus.acknowledge_delivery(spec.trigger_id, after.event_id)
    assert bus.poll_subscription(spec, matcher=lambda _event: True) == []


def test_legacy_subscription_keeps_unacknowledged_history(tmp_path: Path) -> None:
    bus = InternalEventBus(tmp_path / "orchestration.db")
    spec = _subscription()
    delivered = bus.publish(event_type=spec.event_type, payload={"value": "delivered"})
    pending = bus.publish(event_type=spec.event_type, payload={"value": "pending"})

    bus.seed_legacy_subscription(spec, config_revision="migration")
    bus.seed_delivered_event(spec.trigger_id, delivered.event_id)

    assert [
        event.event_id for event in bus.poll_subscription(spec, matcher=lambda _event: True)
    ] == [pending.event_id]


def test_changed_subscription_starts_at_current_tail(tmp_path: Path) -> None:
    bus = InternalEventBus(tmp_path / "orchestration.db")
    original = _subscription(correlation_key="one")
    bus.reconcile_subscription(original, config_revision="revision-1")
    old_event = bus.publish(
        event_type=original.event_type,
        correlation_key="one",
    )
    assert bus.poll_subscription(original, matcher=lambda _event: True) == [old_event]

    changed = _subscription(correlation_key="two")
    bus.reconcile_subscription(changed, config_revision="revision-2")
    assert bus.poll_subscription(changed, matcher=lambda _event: True) == []

    new_event = bus.publish(
        event_type=changed.event_type,
        correlation_key="two",
    )
    assert bus.poll_subscription(changed, matcher=lambda _event: True) == [new_event]


def test_duplicate_schedule_idempotency_returns_original_record_without_mutation(
    tmp_path: Path,
) -> None:
    store = ScheduledInternalEventStore(tmp_path / "orchestration.db")
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


def test_due_scheduled_events_are_sorted_and_next_pending_is_indexed(tmp_path: Path) -> None:
    store = ScheduledInternalEventStore(tmp_path / "orchestration.db")
    now = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    for value, run_at, created_at in (
        ("third", now - timedelta(minutes=15), now - timedelta(minutes=30)),
        ("first", now - timedelta(minutes=10), now - timedelta(minutes=20)),
        ("second", now - timedelta(minutes=10), now - timedelta(minutes=10)),
    ):
        store.schedule_event(
            event_type="custom.operator_event",
            payload={"value": value},
            run_at=run_at,
            idempotency_key=value,
            created_at=created_at,
        )

    assert [event.payload["value"] for event in store.list_due(now)] == [
        "third",
        "first",
        "second",
    ]
    assert store.next_pending_run_at() == now - timedelta(minutes=15)


def test_scheduling_notifies_primary_trigger_wake_surface(tmp_path: Path) -> None:
    notifier = _RecordingNotifier()
    store = ScheduledInternalEventStore(
        tmp_path / "orchestration.db",
        wake_notifier=notifier,
    )

    store.schedule_event(
        event_type="custom.operator_event",
        run_at=datetime.now(UTC) + timedelta(seconds=1),
        idempotency_key="wake-trigger-worker",
    )
    store.schedule_event(
        event_type="custom.operator_event",
        run_at=datetime.now(UTC) + timedelta(seconds=2),
        idempotency_key="wake-trigger-worker",
    )

    assert notifier.calls == 1


def test_runtime_internal_event_publish_uses_primary_trigger_wake_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_config = canonical_worker_config({"runtime_dir": tmp_path / "runtime"})
    notifier = _RecordingNotifier()
    paths: list[Path] = []

    def notifier_factory(path: Path | str) -> _RecordingNotifier:
        paths.append(Path(path))
        return notifier

    monkeypatch.setattr(internal_event_builders, "HostWakeNotifier", notifier_factory)

    build_internal_event_bus(worker_config).publish(event_type="custom.operator_event")

    assert paths == [wake_socket_path_for_lock(worker_config.lock_path)]
    assert notifier.calls == 1


def test_runtime_schedule_creation_uses_same_trigger_worker_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_config = canonical_worker_config({"runtime_dir": tmp_path / "runtime"})
    notifier = _RecordingNotifier()
    paths: list[Path] = []

    def notifier_factory(path: Path | str) -> _RecordingNotifier:
        paths.append(Path(path))
        return notifier

    monkeypatch.setattr(internal_event_builders, "HostWakeNotifier", notifier_factory)

    build_scheduled_internal_event_store(worker_config).schedule_event(
        event_type="custom.operator_event",
        run_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    assert paths == [wake_socket_path_for_lock(worker_config.lock_path)]
    assert notifier.calls == 1


def test_due_schedule_publication_and_completion_are_atomic(tmp_path: Path) -> None:
    database_path = tmp_path / "orchestration.db"
    notifier = _RecordingNotifier()
    store = ScheduledInternalEventStore(database_path)
    scheduled, _ = store.schedule_event(
        event_type="custom.operator_event",
        payload={"value": "ready"},
        run_at=datetime.now(UTC) - timedelta(seconds=1),
        idempotency_key="schedule-ready",
    )
    bus = InternalEventBus(database_path, wake_notifier=notifier)

    published, created = bus.publish_scheduled(scheduled.schedule_id)
    duplicate, duplicate_created = bus.publish_scheduled(scheduled.schedule_id)

    assert created is True
    assert duplicate_created is False
    assert duplicate == published
    assert notifier.calls == 1
    stored_schedule = store.list_all()[0]
    assert stored_schedule.status == "published"
    assert stored_schedule.published_event_id == published.event_id


def test_retention_preserves_unscanned_events_and_pending_schedules(tmp_path: Path) -> None:
    database_path = tmp_path / "orchestration.db"
    bus = InternalEventBus(database_path)
    old = datetime.now(UTC) - timedelta(days=60)
    retained = bus.publish(
        event_type="custom.operator_event",
        created_at=old,
    )
    bus.publish(event_type="custom.unobserved", created_at=old)
    spec = _subscription()
    bus.seed_legacy_subscription(spec, config_revision="migration")
    schedules = ScheduledInternalEventStore(database_path)
    schedules.schedule_event(
        event_type="custom.operator_event",
        run_at=datetime.now(UTC) + timedelta(days=1),
        created_at=old,
        idempotency_key="pending-old",
    )
    published_schedule, _ = schedules.schedule_event(
        event_type="custom.operator_event",
        run_at=old,
        created_at=old,
        idempotency_key="published-old",
    )
    schedules.mark_published(published_schedule.schedule_id, retained.event_id, old)

    result = run_orchestration_retention(
        database_path,
        retention_days=30,
        compact_history_retention_days=365,
    )

    assert result.internal_events_deleted == 1
    assert result.scheduled_internal_events_deleted == 1
    assert [event.event_id for event in bus.list_events()] == [retained.event_id]
    assert [event.status for event in schedules.list_all()] == ["pending"]
    assert bus.poll_subscription(spec, matcher=lambda _event: True) == [retained]
