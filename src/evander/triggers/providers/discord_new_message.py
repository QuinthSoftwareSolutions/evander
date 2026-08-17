from __future__ import annotations

from datetime import UTC, datetime

from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.models import DiscordMessageEnvelope
from evander.events.models import TriggerEvent
from evander.events.provider_models import DiscordNewMessageTriggerDefinition
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from evander.triggers.providers.base import TriggerProviderBase


class DiscordNewMessageTriggerProvider(TriggerProviderBase):
    _RECENT_MESSAGE_LIMIT = 512

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

        candidates: list[DiscordMessageEnvelope] = []
        if channel_filter:
            messages = inbox_store.list_all_messages(channel_ids=sorted(channel_filter)).messages
        else:
            # Preserve the legacy compatibility path for unfiltered Discord triggers.
            messages = [
                DiscordMessageEnvelope.model_validate(raw)
                for raw in inbox_store.read_raw().get("messages", {}).values()
                if isinstance(raw, dict)
            ]

        for message in messages:
            if channel_filter and message.channel_id not in channel_filter:
                continue
            if user_filter and message.author_id not in user_filter:
                continue
            if cursor is not None and message.created_at < cursor:
                continue
            candidates.append(message)

        candidates.sort(key=lambda message: (message.created_at, message.message_id))

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
    state["last_created_at"] = latest_cursor_timestamp_text(
        state.get("last_created_at"),
        occurred_at,
    )
    state["recent_message_ids"] = merge_cursor_ids(
        state.get("recent_message_ids"),
        [message_id],
        limit=DiscordNewMessageTriggerProvider._RECENT_MESSAGE_LIMIT,
    )
    return state
