from __future__ import annotations

from datetime import datetime

from evander.events.models import TriggerEvent
from evander.events.provider_models import TicketChangedTriggerDefinition
from evander.triggers.providers._cursor_state import TriggerCursorStore
from evander.triggers.providers._local_tickets import (
    TICKET_CHANGED_CURSOR_FIELD,
    acknowledge_ticket,
    local_ticket_events,
    parse_ticket_timestamp,
    ticket_cursor_path,
)
from evander.triggers.providers.base import TriggerProviderBase


class TicketChangedTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        return local_ticket_events(
            trigger,
            runtime_dir=self._runtime_dir(),
            state=state,
            cursor_field=TICKET_CHANGED_CURSOR_FIELD,
            timestamp_field="updated_at",
        )

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload.get("ticket")
        if not isinstance(payload, dict):
            return
        issue_id = str(payload.get("issue_id") or "").strip()
        occurred_at = parse_ticket_timestamp(payload.get("updated_at")) or event.occurred_at
        if not issue_id or occurred_at is None:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_issue(state, issue_id, occurred_at),
        )

    def _trigger(self) -> TicketChangedTriggerDefinition:
        if not isinstance(self.trigger_definition, TicketChangedTriggerDefinition):
            raise RuntimeError(
                "TicketChangedTriggerProvider initialized with a non-ticket-changed trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("TicketChangedTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(ticket_cursor_path(self._runtime_dir(), "changed"))


def _acknowledge_issue(
    state: dict[str, object],
    issue_id: str,
    occurred_at: datetime,
) -> dict[str, object]:
    return acknowledge_ticket(
        state, issue_id, occurred_at, cursor_field=TICKET_CHANGED_CURSOR_FIELD
    )
