from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.email.cursor_store import DurableCursorStore
from agent_sandbox.email.gmail_provider import GmailEmailProvider
from agent_sandbox.email.graph_provider import GraphEmailProvider
from agent_sandbox.email.providers import EmailMessageSummary, JsonEmailProvider
from agent_sandbox.models import DraftMessage, EmailMessage
from agent_sandbox.storage import JsonStore
from agent_sandbox.triggers.models import EmailNewMessageTriggerDefinition, TriggerEvent
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class EmailNewMessageTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        state_store = self._cursor_store()
        state = state_store.get(trigger.id)
        cursor = _parse_dt(state.get("last_received_at"))
        recent_ids = {
            value
            for value in state.get("recent_message_ids", [])
            if isinstance(value, str) and value.strip()
        }

        provider = self._build_provider(trigger)
        candidates: list[EmailMessageSummary] = []
        for mailbox_id in trigger.mailbox_ids:
            candidates.extend(provider.list_unread(mailbox_id=mailbox_id, folder="inbox", limit=200))

        candidates.sort(key=lambda item: (item.received_at, item.message_id))

        events: list[TriggerEvent] = []
        max_received_at = cursor
        new_ids = list(recent_ids)
        for message in candidates:
            if cursor is not None and message.received_at <= cursor and message.message_id in recent_ids:
                continue
            occurred_at = message.received_at.astimezone(UTC)
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{message.mailbox_id}:{message.message_id}:{occurred_at.isoformat()}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={"message": message.model_dump(mode="json")},
                )
            )
            new_ids.append(message.message_id)
            if max_received_at is None or occurred_at > max_received_at:
                max_received_at = occurred_at

        if max_received_at is not None:
            state_store.put(
                trigger.id,
                {
                    "last_received_at": max_received_at.isoformat().replace("+00:00", "Z"),
                    "recent_message_ids": new_ids[-512:],
                },
            )
        return events

    def _trigger(self) -> EmailNewMessageTriggerDefinition:
        if not isinstance(self.trigger_definition, EmailNewMessageTriggerDefinition):
            raise RuntimeError("EmailNewMessageTriggerProvider initialized with a non-email trigger definition")
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("EmailNewMessageTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-email.json")

    def _build_provider(self, trigger: EmailNewMessageTriggerDefinition):
        runtime_dir = self._runtime_dir()
        if trigger.provider == "json_store":
            config = self.runtime_context.resolved_config
            return JsonEmailProvider(
                JsonStore(config.email_store_path, EmailMessage),
                JsonStore(config.draft_store_path, DraftMessage),
            )

        auth_secret_refs = {
            key: self.runtime_context.resolve_secret(value) or value
            for key, value in trigger.auth_secret_refs.items()
        }
        provider_cursor_store = DurableCursorStore(runtime_dir / "trigger-email-provider-cursors.json")
        if trigger.provider == "gmail":
            return GmailEmailProvider(
                account_identifier=trigger.account_identifier,
                folder_mapping={},
                auth_secret_refs=auth_secret_refs,
                timeout_seconds=self.runtime_context.resolved_config.email_provider_timeout_seconds,
                cursor_store=provider_cursor_store,
            )
        if trigger.provider == "graph":
            return GraphEmailProvider(
                account_identifier=trigger.account_identifier,
                folder_mapping={},
                auth_secret_refs=auth_secret_refs,
                timeout_seconds=self.runtime_context.resolved_config.email_provider_timeout_seconds,
                cursor_store=provider_cursor_store,
            )
        raise RuntimeError(f"Unsupported email provider: {trigger.provider!r}")


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
