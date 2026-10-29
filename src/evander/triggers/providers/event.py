from __future__ import annotations

from datetime import UTC
from typing import Any

from evander.events.internal import (
    InternalEvent,
    InternalEventBus,
    InternalEventSubscriptionSpec,
)
from evander.orchestration.internal_events import build_internal_event_bus_from_metadata
from evander.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    WorkflowNodeEnvelope,
)
from evander.events.provider_models import EventTriggerDefinition
from evander.triggers.providers.base import TriggerProviderBase


class EventTriggerProvider(TriggerProviderBase):
    def __init__(self) -> None:
        super().__init__()
        self._cached_event_bus: InternalEventBus | None = None

    @classmethod
    def validate_definition(
        cls, trigger_definition: TriggerDefinitionDiscriminated
    ) -> TriggerDefinitionDiscriminated:
        if isinstance(trigger_definition, WorkflowNodeEnvelope):
            return EventTriggerDefinition.model_validate(
                {
                    "id": trigger_definition.id,
                    "display_name": trigger_definition.display_name,
                    "type": trigger_definition.type,
                    "outputs": trigger_definition.outputs,
                    **dict(trigger_definition.config),
                }
            )
        return super().validate_definition(trigger_definition)

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        bus = self._event_bus()
        spec = self.subscription_spec()
        bus.reconcile_subscription(spec, config_revision=None)
        matched = bus.poll_subscription(spec, matcher=self._matches)
        events: list[TriggerEvent] = []
        for event in matched:
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{event.event_id}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=event.created_at.astimezone(UTC),
                    trace_id=(event.source.workflow_trace_id if event.source is not None else None),
                    payload=event_trigger_payload(event),
                )
            )

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        internal_event_id = str(event.payload.get("event_id") or "").strip()
        if not internal_event_id:
            return
        self._event_bus().acknowledge_delivery(trigger.id, internal_event_id)

    def subscription_spec(self) -> InternalEventSubscriptionSpec:
        trigger = self._trigger()
        return InternalEventSubscriptionSpec(
            trigger_id=trigger.id,
            trigger_type=trigger.type,
            event_type=self._subscription_event_type(),
            filter_payload=self._subscription_filter_payload(),
        )

    def _subscription_event_type(self) -> str:
        return self._trigger().event_type

    def _subscription_filter_payload(self) -> dict[str, object]:
        trigger = self._trigger()
        return {
            "event_type": trigger.event_type,
            "correlation_key": trigger.correlation_key,
        }

    def _trigger(self) -> EventTriggerDefinition:
        if not isinstance(self.trigger_definition, EventTriggerDefinition):
            raise RuntimeError("EventTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _matches(self, event: InternalEvent) -> bool:
        return _matches(self._trigger(), event)

    def _event_bus(self) -> InternalEventBus:
        if self.runtime_context is None:
            raise RuntimeError("EventTriggerProvider has no runtime context")
        if self._cached_event_bus is None:
            self._cached_event_bus = build_internal_event_bus_from_metadata(
                self.runtime_context.metadata,
                runtime_dir=self.runtime_context.resolved_config.runtime_dir,
            )
        return self._cached_event_bus


def _matches(trigger: EventTriggerDefinition, event: InternalEvent) -> bool:
    if event.event_type != trigger.event_type:
        return False
    if trigger.correlation_key is not None and event.correlation_key != trigger.correlation_key:
        return False
    return True


def event_trigger_payload(event: InternalEvent) -> dict[str, Any]:
    source = event.source.model_dump(mode="json") if event.source is not None else None
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "payload": dict(event.payload),
        "correlation_key": event.correlation_key,
        "created_at": event.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "source": source,
        "idempotency_key": event.idempotency_key,
    }
