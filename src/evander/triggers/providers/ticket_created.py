from __future__ import annotations

from datetime import UTC, datetime

from evander.events.models import TriggerEvent
from evander.events.provider_models import TicketCreatedTriggerDefinition
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from evander.triggers.providers._local_tickets import (
    RECENT_ISSUE_IDS_FIELD,
    TICKET_CREATED_CURSOR_FIELD,
    RECENT_ISSUE_LIMIT,
    local_ticket_candidates,
    parse_ticket_timestamp,
    ticket_cursor_path,
)
from evander.triggers.providers.base import TriggerProviderBase


class TicketCreatedTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        cursor = parse_ticket_timestamp(state.get(TICKET_CREATED_CURSOR_FIELD))
        recent_issue_ids = [
            value
            for value in state.get(RECENT_ISSUE_IDS_FIELD, [])
            if isinstance(value, str) and value.strip()
        ]
        recent_issue_id_set = set(recent_issue_ids)

        candidates = local_ticket_candidates(
            self._runtime_dir(),
            trigger.directories,
            "created_at",
        )

        events: list[TriggerEvent] = []
        for issue in candidates:
            occurred_at = issue.created_at.astimezone(UTC)
            if cursor is not None and occurred_at < cursor:
                continue
            if (
                cursor is not None
                and occurred_at == cursor
                and issue.issue_id in recent_issue_id_set
            ):
                continue
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{issue.issue_id}:{occurred_at.isoformat()}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={"ticket": issue.model_dump(mode="json")},
                )
            )

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload.get("ticket")
        if not isinstance(payload, dict):
            return
        issue_id = str(payload.get("issue_id") or "").strip()
        occurred_at = parse_ticket_timestamp(payload.get("created_at")) or event.occurred_at
        if not issue_id or occurred_at is None:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_issue(state, issue_id, occurred_at),
        )

    def _trigger(self) -> TicketCreatedTriggerDefinition:
        if not isinstance(self.trigger_definition, TicketCreatedTriggerDefinition):
            raise RuntimeError(
                "TicketCreatedTriggerProvider initialized with a non-ticket-created trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("TicketCreatedTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(ticket_cursor_path(self._runtime_dir(), "created"))


def _acknowledge_issue(
    state: dict[str, object],
    issue_id: str,
    occurred_at: datetime,
) -> dict[str, object]:
    state[TICKET_CREATED_CURSOR_FIELD] = latest_cursor_timestamp_text(
        state.get(TICKET_CREATED_CURSOR_FIELD),
        occurred_at,
    )
    state[RECENT_ISSUE_IDS_FIELD] = merge_cursor_ids(
        state.get(RECENT_ISSUE_IDS_FIELD),
        [issue_id],
        limit=RECENT_ISSUE_LIMIT,
    )
    return state
