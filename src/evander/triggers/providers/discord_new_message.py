from __future__ import annotations

from datetime import UTC, datetime

from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.events.models import TriggerEvent
from evander.events.provider_models import DiscordNewMessageTriggerDefinition
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
)
from evander.triggers.providers.base import TriggerProviderBase


class DiscordNewMessageTriggerProvider(TriggerProviderBase):
    _POLL_MESSAGE_LIMIT = 200

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        cursor = _parse_dt(state.get("last_created_at"))
        recent_message_ids = [
            value
            for value in state.get("recent_message_ids", [])
            if isinstance(value, str) and value.strip()
        ]
        recent_message_id_set = set(recent_message_ids)

        inbox_store = DiscordInboxStore(self._runtime_dir() / "discord-inbox-store.db")

        channel_filter = {value.strip() for value in trigger.channel_ids if value.strip()}
        user_filter = {value.strip() for value in trigger.user_ids if value.strip()}

        candidates = inbox_store.list_trigger_messages(
            channel_ids=sorted(channel_filter),
            author_ids=sorted(user_filter),
            created_at=cursor,
            acknowledged_ids=recent_message_ids,
            limit=self._POLL_MESSAGE_LIMIT,
        )

        events: list[TriggerEvent] = []
        for message in candidates:
            occurred_at = message.created_at.astimezone(UTC)
            if (
                cursor is not None
                and occurred_at == cursor
                and message.message_id in recent_message_id_set
            ):
                continue
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{message.message_id}:{occurred_at.isoformat()}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={"message": message.model_dump(mode="json")},
                )
            )

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload.get("message")
        if not isinstance(payload, dict):
            return
        message_id = str(payload.get("message_id") or "").strip()
        occurred_at = _parse_dt(payload.get("created_at")) or event.occurred_at
        if not message_id or occurred_at is None:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_message(state, message_id, occurred_at),
        )

    def _trigger(self) -> DiscordNewMessageTriggerDefinition:
        if not isinstance(self.trigger_definition, DiscordNewMessageTriggerDefinition):
            raise RuntimeError(
                "DiscordNewMessageTriggerProvider initialized with a non-discord trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("DiscordNewMessageTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-discord.json")


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


def _acknowledge_message(
    state: dict[str, object],
    message_id: str,
    occurred_at: datetime,
) -> dict[str, object]:
    previous = _parse_dt(state.get("last_created_at"))
    if previous is not None and occurred_at < previous:
        return state
    # Keep the whole boundary timestamp until it advances. A bounded page can split
    # a timestamp group larger than the old 512-ID window; evicting IDs would replay
    # its first page forever. Older timestamp IDs no longer affect delivery.
    previous_ids = state.get("recent_message_ids", []) if previous == occurred_at else []
    state["last_created_at"] = occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    state["recent_message_ids"] = list(dict.fromkeys([*previous_ids, message_id]))
    return state
