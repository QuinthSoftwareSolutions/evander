from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.email.gmail_provider import GmailEmailProvider
from agent_sandbox.email.graph_provider import GraphEmailProvider
from agent_sandbox.email.providers import EmailMessageSummary, JsonEmailProvider
from agent_sandbox.models import DraftMessage
from agent_sandbox.repositories.records import JsonEmailRecordsRepository
from agent_sandbox.storage import JsonStore
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import EmailNewMessageTriggerDefinition
from agent_sandbox.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class EmailNewMessageTriggerProvider(TriggerProviderBase):
    _RECENT_MESSAGE_LIMIT = 512

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
            candidates.extend(
                provider.list_unread(mailbox_id=mailbox_id, folder="inbox", limit=200)
            )

        candidates.sort(key=lambda item: (item.received_at, item.message_id))

        events: list[TriggerEvent] = []
        for message in candidates:
            if _message_is_behind_cursor(
                cursor,
                received_at=message.received_at,
                message_id=message.message_id,
                recent_ids=recent_ids,
            ):
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

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload.get("message")
        if not isinstance(payload, dict):
            return
        message_id = str(payload.get("message_id") or "").strip()
        received_at = _parse_dt(payload.get("received_at")) or event.occurred_at
        if not message_id or received_at is None:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_message(state, message_id, received_at),
        )

    def _trigger(self) -> EmailNewMessageTriggerDefinition:
        if not isinstance(self.trigger_definition, EmailNewMessageTriggerDefinition):
            raise RuntimeError(
                "EmailNewMessageTriggerProvider initialized with a non-email trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("EmailNewMessageTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-email.json")

    def _build_provider(self, trigger: EmailNewMessageTriggerDefinition):
        if trigger.provider == "json_store":
            config = self.runtime_context.resolved_config
            return JsonEmailProvider(
                JsonEmailRecordsRepository(config.email_store_path),
                JsonStore(config.draft_store_path, DraftMessage),
            )

        auth_secret_refs = {
            key: self.runtime_context.resolve_secret(value) or value
            for key, value in trigger.auth_secret_refs.items()
        }
        if trigger.provider == "gmail":
            return GmailEmailProvider(
                account_identifier=trigger.account_identifier,
                folder_mapping={},
                auth_secret_refs=auth_secret_refs,
                timeout_seconds=self.runtime_context.resolved_config.email_provider_timeout_seconds,
                cursor_store=None,
            )
        if trigger.provider == "graph":
            return GraphEmailProvider(
                account_identifier=trigger.account_identifier,
                folder_mapping={},
                auth_secret_refs=auth_secret_refs,
                timeout_seconds=self.runtime_context.resolved_config.email_provider_timeout_seconds,
                cursor_store=None,
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


def _message_is_behind_cursor(
    cursor: datetime | None,
    *,
    received_at: datetime,
    message_id: str,
    recent_ids: set[str],
) -> bool:
    if cursor is None:
        return False
    received_at = received_at.astimezone(UTC)
    if received_at < cursor:
        return True
    return received_at == cursor and message_id in recent_ids


def _acknowledge_message(
    state: dict[str, object],
    message_id: str,
    received_at: datetime,
) -> dict[str, object]:
    state["last_received_at"] = latest_cursor_timestamp_text(
        state.get("last_received_at"),
        received_at,
    )
    state["recent_message_ids"] = merge_cursor_ids(
        state.get("recent_message_ids"),
        [message_id],
        limit=EmailNewMessageTriggerProvider._RECENT_MESSAGE_LIMIT,
    )
    return state
