from __future__ import annotations

from datetime import UTC, datetime

from evander.repositories.cursors import JsonTeamsInboxCursorRepository
from evander.events.models import TriggerEvent
from evander.events.provider_models import TeamsDirectMessageTriggerDefinition
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from evander.triggers.providers.base import TriggerProviderBase
from evander.triggers.providers.teams_message_events import (
    TeamsDirectMessageEventMatcher,
    parse_trigger_datetime,
)


class TeamsDirectMessageTriggerProvider(TriggerProviderBase):
    _RECENT_CURSOR_LIMIT = 512

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.update(trigger.id, _initialize_cursor_if_needed)
        matcher = TeamsDirectMessageEventMatcher.from_cursor_state(trigger, state)

        inbox_store = JsonTeamsInboxCursorRepository(
            self._resolved_config().runtime_dir / "teams-inbox-store.db"
        )
        raw_messages = inbox_store.list_all_messages(
            channel_targets=list(matcher.target_filters),
            created_at_from=matcher.cursor,
            direct_messages_only=True,
        )
        events = [event for message in raw_messages if (event := matcher.event_for(message))]
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload
        cursor = str(payload.get("cursor") or "").strip()
        created_at = parse_trigger_datetime(payload.get("created_at")) or event.occurred_at
        if not cursor or created_at is None:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_message(state, cursor, created_at),
        )

    def _trigger(self) -> TeamsDirectMessageTriggerDefinition:
        if not isinstance(self.trigger_definition, TeamsDirectMessageTriggerDefinition):
            raise RuntimeError(
                "TeamsDirectMessageTriggerProvider initialized with a non-direct-message definition"
            )
        return self.trigger_definition

    def _resolved_config(self):
        if self.runtime_context is None:
            raise RuntimeError("TeamsDirectMessageTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(
            self._resolved_config().runtime_dir / "trigger-cursors-teams.json"
        )


def _initialize_cursor_if_needed(state: dict[str, object]) -> dict[str, object]:
    if parse_trigger_datetime(state.get("last_created_at")) is not None:
        return state
    initialized_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    state["initialized_at"] = initialized_at
    state["last_created_at"] = initialized_at
    state["recent_cursors"] = merge_cursor_ids(
        state.get("recent_cursors"),
        [],
        limit=TeamsDirectMessageTriggerProvider._RECENT_CURSOR_LIMIT,
    )
    return state


def _acknowledge_message(
    state: dict[str, object],
    cursor: str,
    occurred_at: datetime,
) -> dict[str, object]:
    state["last_created_at"] = latest_cursor_timestamp_text(
        state.get("last_created_at"),
        occurred_at,
    )
    state["recent_cursors"] = merge_cursor_ids(
        state.get("recent_cursors"),
        [cursor],
        limit=TeamsDirectMessageTriggerProvider._RECENT_CURSOR_LIMIT,
    )
    return state
