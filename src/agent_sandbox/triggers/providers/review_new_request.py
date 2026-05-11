from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import ReviewNewRequestTriggerDefinition
from agent_sandbox.reviews.request_store import (
    ReviewRequestRecord,
    ReviewRequestRepository,
    ReviewRequestStore,
)
from agent_sandbox.triggers.delivery import (
    TriggerDeliveryRepository,
    TriggerDeliverySourceItem,
    TriggerDeliveryStore,
    TriggerDeliverySubscription,
)
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class ReviewNewRequestTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        now = datetime.now(UTC)
        directory_scope = self._directory_scope()
        subscription = TriggerDeliverySubscription(
            trigger_type=trigger.type,
            trigger_id=trigger.id,
            directory_scope=directory_scope,
        )
        requests = [
            request
            for request in self._store().list_requests(directory_path=directory_scope)
            if request.status in {"pending", "leased"}
        ]
        source_items = [
            TriggerDeliverySourceItem(
                source_item_id=request.request_id,
                source_occurred_at=request.requested_at.astimezone(UTC),
            )
            for request in requests
        ]
        delivery_store = self._delivery_store()
        delivery_store.initialize_subscription_if_new(
            subscription,
            source_items,
            now=now,
        )
        deliverable_by_id = {
            item.source_item_id: item
            for item in delivery_store.list_undelivered(subscription, source_items)
        }

        events: list[TriggerEvent] = []
        for request in requests:
            source_item = deliverable_by_id.get(request.request_id)
            if source_item is None:
                continue
            event_id = f"{trigger.id}:{request.request_id}"
            events.append(
                TriggerEvent(
                    event_id=event_id,
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=request.requested_at.astimezone(UTC),
                    payload={"review_request": request.model_dump(mode="json")},
                )
            )
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        payload = event.payload.get("review_request")
        if not isinstance(payload, dict):
            return
        request = ReviewRequestRecord.model_validate(payload)
        subscription = TriggerDeliverySubscription(
            trigger_type=trigger.type,
            trigger_id=trigger.id,
            directory_scope=self._directory_scope(),
        )
        self._delivery_store().mark_published(
            subscription,
            TriggerDeliverySourceItem(
                source_item_id=request.request_id,
                source_occurred_at=request.requested_at.astimezone(UTC),
            ),
            event_id=event.event_id,
        )

    def _trigger(self) -> ReviewNewRequestTriggerDefinition:
        if not isinstance(self.trigger_definition, ReviewNewRequestTriggerDefinition):
            raise RuntimeError("ReviewNewRequestTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _store(self) -> ReviewRequestRepository:
        return ReviewRequestStore(self._runtime_dir() / "review-request-store.json")

    def _delivery_store(self) -> TriggerDeliveryRepository:
        return TriggerDeliveryStore(self._runtime_dir() / "trigger-deliveries.json")

    def _runtime_dir(self) -> Path:
        if self.runtime_context is None:
            raise RuntimeError("ReviewNewRequestTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _directory_scope(self) -> str:
        if self.runtime_context is None:
            return "root"
        directory_path = str(self.runtime_context.metadata.get("directory_path") or "").strip()
        return directory_path or "root"
