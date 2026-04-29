from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.repositories.cursors import JsonDiscordInboxCursorRepository
from agent_sandbox.models import DiscordMessageEnvelope
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import DiscordNewMessageTriggerDefinition
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class DiscordNewMessageTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        cursor = _parse_dt(state.get("last_created_at"))

        inbox_store = JsonDiscordInboxCursorRepository(
            self._runtime_dir() / "discord-inbox-store.json"
        )
        raw_messages = inbox_store.read_raw().get("messages", {})

        channel_filter = {value.strip() for value in trigger.channel_ids if value.strip()}
        user_filter = {value.strip() for value in trigger.user_ids if value.strip()}

        candidates: list[DiscordMessageEnvelope] = []
        for raw in raw_messages.values():
            if not isinstance(raw, dict):
                continue
            message = DiscordMessageEnvelope.model_validate(raw)
            if channel_filter and message.channel_id not in channel_filter:
                continue
            if user_filter and message.author_id not in user_filter:
                continue
            if cursor is not None and message.created_at <= cursor:
                continue
            candidates.append(message)

        candidates.sort(key=lambda message: (message.created_at, message.message_id))

        events: list[TriggerEvent] = []
        max_created_at = cursor
        for message in candidates:
            occurred_at = message.created_at.astimezone(UTC)
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{message.message_id}:{occurred_at.isoformat()}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={"message": message.model_dump(mode="json")},
                )
            )
            if max_created_at is None or occurred_at > max_created_at:
                max_created_at = occurred_at

        if max_created_at is not None:
            cursor_store.put(
                trigger.id, {"last_created_at": max_created_at.isoformat().replace("+00:00", "Z")}
            )
        return events

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
