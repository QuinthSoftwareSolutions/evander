from __future__ import annotations

from datetime import UTC
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
from evander.events.provider_models import WebhookTriggerDefinition
from evander.triggers.providers.base import TriggerProviderBase


class WebhookTriggerProvider(TriggerProviderBase):
    def __init__(self) -> None:
        super().__init__()
        self._cached_event_bus: InternalEventBus | None = None

    @classmethod
    def validate_definition(
        cls, trigger_definition: TriggerDefinitionDiscriminated
    ) -> TriggerDefinitionDiscriminated:
        if isinstance(trigger_definition, WorkflowNodeEnvelope):
            return WebhookTriggerDefinition.model_validate(
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
        matched = bus.poll_subscription(
            spec,
            matcher=lambda event: _matches(trigger, event),
        )
        events: list[TriggerEvent] = []
        for event in matched:
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{event.event_id}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=event.created_at.astimezone(UTC),
                    trace_id=(event.source.workflow_trace_id if event.source is not None else None),
                    payload=dict(event.payload),
                )
            )

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        internal_event_id = _internal_event_id_from_trigger_event_id(event.event_id, trigger.id)
        if not internal_event_id:
            return
        self._event_bus().acknowledge_delivery(trigger.id, internal_event_id)

    def subscription_spec(self) -> InternalEventSubscriptionSpec:
        trigger = self._trigger()
        event_type = f"external.{trigger.event_type}"
        return InternalEventSubscriptionSpec(
            trigger_id=trigger.id,
            trigger_type=trigger.type,
            event_type=event_type,
            filter_payload={
                "event_type": event_type,
                "source_type": "webhook",
                "source_name": trigger.slug,
                "node_type": "trigger:http:webhook",
                "node_id": trigger.id,
            },
        )

    def _trigger(self) -> WebhookTriggerDefinition:
        if not isinstance(self.trigger_definition, WebhookTriggerDefinition):
            raise RuntimeError("WebhookTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _event_bus(self) -> InternalEventBus:
        if self.runtime_context is None:
            raise RuntimeError("WebhookTriggerProvider has no runtime context")
        if self._cached_event_bus is None:
            self._cached_event_bus = build_internal_event_bus_from_metadata(
                self.runtime_context.metadata,
                runtime_dir=self.runtime_context.resolved_config.runtime_dir,
            )
        return self._cached_event_bus


def _matches(trigger: WebhookTriggerDefinition, event: InternalEvent) -> bool:
    if event.event_type != f"external.{trigger.event_type}":
        return False
    if event.source is None:
        return False
    if event.source.source_type != "webhook":
        return False
    if event.source.source_name != trigger.slug:
        return False
    if event.source.node_type != "trigger:http:webhook":
        return False
    if event.source.node_id is not None and event.source.node_id != trigger.id:
        return False
    return True


def _internal_event_id_from_trigger_event_id(event_id: str, trigger_id: str) -> str | None:
    prefix = f"{trigger_id}:"
    if not event_id.startswith(prefix):
        return None
    internal_event_id = event_id[len(prefix) :].strip()
    return internal_event_id or None
