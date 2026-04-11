from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.events.models import TeamsNewMessageTriggerDefinition, TriggerEvent
from agent_sandbox.services import normalize_teams_target, teams_target_from_message
from agent_sandbox.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class TeamsNewMessageTriggerProvider(TriggerProviderBase):
    _RECENT_CURSOR_LIMIT = 512

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        cursor = _parse_dt(state.get("last_created_at"))
        recent_cursors = [
            value
            for value in state.get("recent_cursors", [])
            if isinstance(value, str) and value.strip()
        ]
        recent_cursor_set = set(recent_cursors)

        inbox_store = TeamsInboxStore(self._resolved_config().runtime_dir / "teams-inbox-store.json")
        raw_messages = inbox_store.list_all_messages()

        channel_target_filter = {_normalize_channel_target(value) for value in trigger.channel_targets if value.strip()}
        user_filter = {value.strip() for value in trigger.user_ids if value.strip()}

        candidates: list[TeamsInboxMessage] = []
        for message in raw_messages:
            channel_target = _message_channel_target(message)
            author_id = message.author_id.strip()
            if channel_target_filter and channel_target not in channel_target_filter:
                continue
            if user_filter and author_id not in user_filter:
                continue
            occurred_at = message.created_at.astimezone(UTC)
            if cursor is not None and occurred_at < cursor:
                continue
            if cursor is not None and occurred_at == cursor and message.cursor in recent_cursor_set:
                continue
            candidates.append(message)

        candidates.sort(key=lambda message: (message.created_at, message.cursor))

        events: list[TriggerEvent] = []
        max_created_at = cursor
        new_cursors = list(recent_cursors)
        for message in candidates:
            occurred_at = message.created_at.astimezone(UTC)
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{message.cursor}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={"message": message.model_dump(mode="json")},
                )
            )
            new_cursors.append(message.cursor)
            if max_created_at is None or occurred_at > max_created_at:
                max_created_at = occurred_at

        if max_created_at is not None:
            cursor_store.put(
                trigger.id,
                {
                    "last_created_at": max_created_at.isoformat().replace("+00:00", "Z"),
                    "recent_cursors": new_cursors[-self._RECENT_CURSOR_LIMIT :],
                },
            )
        return events

    def _trigger(self) -> TeamsNewMessageTriggerDefinition:
        if not isinstance(self.trigger_definition, TeamsNewMessageTriggerDefinition):
            raise RuntimeError("TeamsNewMessageTriggerProvider initialized with a non-Teams trigger definition")
        return self.trigger_definition

    def _resolved_config(self):
        if self.runtime_context is None:
            raise RuntimeError("TeamsNewMessageTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._resolved_config().runtime_dir / "trigger-cursors-teams.json")


def _message_channel_target(message: TeamsInboxMessage) -> str:
    return _normalize_channel_target(
        teams_target_from_message(
            team_id=message.team_id,
            channel_id=message.channel_id,
            chat_id=message.chat_id,
            metadata=message.metadata,
        )
    )


def _normalize_channel_target(value: str) -> str:
    return normalize_teams_target(value)


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
