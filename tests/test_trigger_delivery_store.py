from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.triggers.delivery import (
    TriggerDeliverySourceItem,
    TriggerDeliveryStore,
    TriggerDeliverySubscription,
)


def _subscription(trigger_id: str = "trigger-a") -> TriggerDeliverySubscription:
    return TriggerDeliverySubscription(
        trigger_type="trigger:review:new-request",
        trigger_id=trigger_id,
        directory_scope="root/team-a",
    )


def _item(
    item_id: str,
    occurred_at: datetime = datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
) -> TriggerDeliverySourceItem:
    return TriggerDeliverySourceItem(
        source_item_id=item_id,
        source_occurred_at=occurred_at,
    )


def test_trigger_delivery_store_suppresses_initial_history(tmp_path: Path) -> None:
    store = TriggerDeliveryStore(tmp_path / "trigger-deliveries.json")
    subscription = _subscription()
    existing = [_item("review-old")]
    initialized_at = datetime(2026, 4, 3, 12, 5, tzinfo=UTC)

    assert store.initialize_subscription_if_new(
        subscription,
        existing,
        now=initialized_at,
    )

    assert store.list_undelivered(subscription, existing) == []

    raw = store.read_raw()
    deliveries = raw["deliveries"]
    assert isinstance(deliveries, dict)
    assert len(deliveries) == 1
    assert next(iter(deliveries.values()))["status"] == "suppressed_initial_history"


def test_trigger_delivery_store_delivers_per_trigger_subscription(tmp_path: Path) -> None:
    store = TriggerDeliveryStore(tmp_path / "trigger-deliveries.json")
    first_subscription = _subscription("trigger-a")
    second_subscription = _subscription("trigger-b")
    started_at = datetime(2026, 4, 3, 12, 0, tzinfo=UTC)
    source_item = _item("review-1", started_at + timedelta(seconds=1))

    store.initialize_subscription_if_new(first_subscription, [], now=started_at)
    store.initialize_subscription_if_new(second_subscription, [], now=started_at)

    assert store.list_undelivered(first_subscription, [source_item]) == [source_item]
    store.mark_published(
        first_subscription,
        source_item,
        event_id="trigger-a:review-1",
        now=started_at + timedelta(seconds=2),
    )

    assert store.list_undelivered(first_subscription, [source_item]) == []
    assert store.list_undelivered(second_subscription, [source_item]) == [source_item]


def test_trigger_delivery_store_persists_delivery_state(tmp_path: Path) -> None:
    path = tmp_path / "trigger-deliveries.json"
    subscription = _subscription()
    started_at = datetime(2026, 4, 3, 12, 0, tzinfo=UTC)
    source_item = _item("review-1", started_at + timedelta(seconds=1))
    store = TriggerDeliveryStore(path)
    store.initialize_subscription_if_new(subscription, [], now=started_at)
    store.mark_published(
        subscription,
        source_item,
        event_id="trigger-a:review-1",
        now=started_at + timedelta(seconds=2),
    )

    reloaded = TriggerDeliveryStore(path)

    assert reloaded.list_undelivered(subscription, [source_item]) == []
    raw = reloaded.read_raw()
    deliveries = raw["deliveries"]
    assert isinstance(deliveries, dict)
    assert next(iter(deliveries.values()))["status"] == "published"
