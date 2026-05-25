from __future__ import annotations

from datetime import UTC
from pathlib import Path

from agent_sandbox.events.internal import InternalEvent, InternalEventBus, internal_event_store_path
from agent_sandbox.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    WorkflowNodeEnvelope,
)
from agent_sandbox.events.provider_models import WebhookTriggerDefinition
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore, merge_cursor_ids
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class WebhookTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-http-webhook.json"

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
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        emitted_ids = {
            str(value).strip()
            for value in state.get("emitted_internal_event_ids", [])
            if str(value).strip()
        }

        matched = [event for event in self._event_bus().list_events() if _matches(trigger, event)]
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
                    trace_id=(
                        event.source.workflow_trace_id if event.source is not None else None
                    ),
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
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_internal_event_id(state, internal_event_id),
        )

    def _trigger(self) -> WebhookTriggerDefinition:
        if not isinstance(self.trigger_definition, WebhookTriggerDefinition):
            raise RuntimeError("WebhookTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _event_bus(self) -> InternalEventBus:
        if self.runtime_context is None:
            raise RuntimeError("WebhookTriggerProvider has no runtime context")
        metadata_path = self.runtime_context.metadata.get("internal_event_store_path")
        path = Path(metadata_path) if metadata_path else internal_event_store_path(
            self.runtime_context.resolved_config.runtime_dir
        )
        journal_path = self.runtime_context.metadata.get("workflow_event_journal_path")
        return InternalEventBus(
            path, journal_path=Path(journal_path) if journal_path else None
        )

    def _cursor_store(self) -> TriggerCursorStore:
        if self.runtime_context is None:
            raise RuntimeError("WebhookTriggerProvider has no runtime context")
        return TriggerCursorStore(
            self.runtime_context.resolved_config.runtime_dir / self._CURSOR_FILENAME
        )


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


def _acknowledge_internal_event_id(state: dict[str, object], internal_event_id: str) -> dict[str, object]:
    state["emitted_internal_event_ids"] = merge_cursor_ids(
        state.get("emitted_internal_event_ids"),
        [internal_event_id],
    )
    return state
