from __future__ import annotations

from datetime import UTC
from typing import Any

from evander.events.internal import InternalEvent, InternalEventBus
from evander.orchestration.internal_events import build_internal_event_bus_from_metadata
from evander.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    WorkflowNodeEnvelope,
)
from evander.events.provider_models import EventTriggerDefinition
from evander.triggers.providers._cursor_state import TriggerCursorStore, merge_cursor_ids
from evander.triggers.providers.base import TriggerProviderBase


class EventTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-event.json"

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
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        emitted_ids = {
            str(value).strip()
            for value in state.get("emitted_internal_event_ids", [])
            if str(value).strip()
        }

        matched = [event for event in self._event_bus().list_events() if self._matches(event)]
        events: list[TriggerEvent] = []
        for event in matched:
            if event.event_id in emitted_ids:
                continue
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{event.event_id}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=event.created_at.astimezone(UTC),
                    trace_id=(event.source.workflow_trace_id if event.source is not None else None),
                    payload=_trigger_payload(event),
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
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_internal_event_id(state, internal_event_id),
        )

    def _trigger(self) -> EventTriggerDefinition:
        if not isinstance(self.trigger_definition, EventTriggerDefinition):
            raise RuntimeError("EventTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _matches(self, event: InternalEvent) -> bool:
        return _matches(self._trigger(), event)

    def _event_bus(self) -> InternalEventBus:
        if self.runtime_context is None:
            raise RuntimeError("EventTriggerProvider has no runtime context")
        return build_internal_event_bus_from_metadata(
            self.runtime_context.metadata,
            runtime_dir=self.runtime_context.resolved_config.runtime_dir,
        )

    def _cursor_store(self) -> TriggerCursorStore:
        if self.runtime_context is None:
            raise RuntimeError("EventTriggerProvider has no runtime context")
        return TriggerCursorStore(
            self.runtime_context.resolved_config.runtime_dir / self._CURSOR_FILENAME
        )


def _matches(trigger: EventTriggerDefinition, event: InternalEvent) -> bool:
    if event.event_type != trigger.event_type:
        return False
    if trigger.correlation_key is not None and event.correlation_key != trigger.correlation_key:
        return False
    return True


def _trigger_payload(event: InternalEvent) -> dict[str, Any]:
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


def _acknowledge_internal_event_id(
    state: dict[str, object], internal_event_id: str
) -> dict[str, object]:
    state["emitted_internal_event_ids"] = merge_cursor_ids(
        state.get("emitted_internal_event_ids"),
        [internal_event_id],
    )
    return state
