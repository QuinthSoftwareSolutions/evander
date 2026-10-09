from __future__ import annotations

from datetime import UTC, datetime

from evander.config import GatewayConfig
from evander.email.providers import EmailMessageSummary
from evander.models import DraftMessage
from evander.repositories.records import JsonEmailRecordsRepository
from evander.runtime import _build_email_provider
from evander.storage import JsonStore
from evander.events.models import TriggerEvent
from evander.events.provider_models import EmailNewMessageTriggerDefinition
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    parse_cursor_timestamp_text as _parse_dt,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from evander.triggers.providers.base import TriggerProviderBase


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
                provider.list_unread(mailbox_id=mailbox_id, folder=trigger.folder, limit=200)
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
        if self.runtime_context is None:
            raise RuntimeError("EmailNewMessageTriggerProvider has no runtime context")
        admin_service = getattr(self.runtime_context, "admin_service", None)
        if admin_service is None:
            admin_service = self.runtime_context.metadata.get("admin_service")
        runtime = getattr(admin_service, "runtime", None)
        config = self._gateway_config()
        if runtime is not None:
            return _build_email_provider(config, runtime.emails, runtime.drafts, None)
        return _build_email_provider(
            config,
            JsonEmailRecordsRepository(config.email_store_path),
            JsonStore(config.draft_store_path, DraftMessage),
            None,
        )

    def _gateway_config(self) -> GatewayConfig:
        if self.runtime_context is None:
            raise RuntimeError("EmailNewMessageTriggerProvider has no runtime context")
        admin_service = getattr(self.runtime_context, "admin_service", None)
        if admin_service is None:
            admin_service = self.runtime_context.metadata.get("admin_service")
        runtime = getattr(admin_service, "runtime", None)
        runtime_provider_config = getattr(runtime, "provider_config", None)
        resolve_for_directory = getattr(runtime_provider_config, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(self._directory_path())

        resolver = getattr(self.runtime_context, "provider_config_resolver", None)
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(self._directory_path())

        provider_config = getattr(self.runtime_context, "provider_config", None)
        if provider_config:
            return GatewayConfig.model_validate(dict(provider_config))
        resolved_config = getattr(self.runtime_context, "resolved_config", None)
        model_dump = getattr(resolved_config, "model_dump", None)
        if callable(model_dump):
            return GatewayConfig.model_validate(model_dump(mode="json"))
        return GatewayConfig.model_validate(vars(resolved_config))

    def _directory_path(self) -> str:
        if self.runtime_context is None:
            raise RuntimeError("EmailNewMessageTriggerProvider has no runtime context")
        text = str(self.runtime_context.metadata.get("directory_path") or "root").strip()
        return text or "root"


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
