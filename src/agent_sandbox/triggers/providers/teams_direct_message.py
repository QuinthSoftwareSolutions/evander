from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.repositories.cursors import JsonTeamsInboxCursorRepository
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import TeamsDirectMessageTriggerDefinition
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage
from agent_sandbox.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from agent_sandbox.triggers.providers.base import TriggerProviderBase
from agent_sandbox.triggers.providers.teams_new_message import (
    _message_matches_filter,
    _message_payload,
    _parse_dt,
)


class TeamsDirectMessageTriggerProvider(TriggerProviderBase):
    _RECENT_CURSOR_LIMIT = 512

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.update(trigger.id, _initialize_cursor_if_needed)
        cursor = _parse_dt(state.get("last_created_at"))
        recent_cursors = [
            value
            for value in state.get("recent_cursors", [])
            if isinstance(value, str) and value.strip()
        ]
        recent_cursor_set = set(recent_cursors)
        sender_filter = {
            _sender_ref_match_key(value) for value in trigger.sender_user_ids if value.strip()
        }

        inbox_store = JsonTeamsInboxCursorRepository(
            self._resolved_config().runtime_dir / "teams-inbox-store.json"
        )
        candidates: list[TeamsInboxMessage] = []
        for message in inbox_store.list_all_messages(
            created_at_from=cursor,
            direct_messages_only=True,
        ):
            if not _is_direct_message(message):
                continue
            if sender_filter and not sender_filter.intersection(_message_sender_ref_keys(message)):
                continue
            occurred_at = message.created_at.astimezone(UTC)
            if cursor is not None and occurred_at < cursor:
                continue
            if cursor is not None and occurred_at == cursor and message.cursor in recent_cursor_set:
                continue
            if not _message_matches_filter(message, trigger.message_filter):
                continue
            candidates.append(message)

        candidates.sort(key=lambda message: (message.created_at, message.cursor))
        return [
            TriggerEvent(
                event_id=f"{trigger.id}:{message.cursor}",
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                occurred_at=message.created_at.astimezone(UTC),
                payload=_message_payload(message, target_type="direct_message"),
            )
            for message in candidates
        ]

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload
        cursor = str(payload.get("cursor") or "").strip()
        created_at = _parse_dt(payload.get("created_at")) or event.occurred_at
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


def _is_direct_message(message: TeamsInboxMessage) -> bool:
    if not message.chat_id:
        return False
    target_type = str(message.metadata.get("target_type") or "").strip()
    chat_type = str(message.metadata.get("chat_type") or "").strip()
    return target_type == "direct_message" or chat_type == "oneOnOne"


def _message_sender_ref_keys(message: TeamsInboxMessage) -> set[str]:
    refs = {
        message.author_id,
        str(message.metadata.get("author_id") or ""),
    }
    metadata_refs = message.metadata.get("direct_message_sender_refs")
    if isinstance(metadata_refs, str):
        refs.add(metadata_refs)
    elif isinstance(metadata_refs, (list, tuple, set)):
        refs.update(str(value) for value in metadata_refs)
    return {_sender_ref_match_key(ref) for ref in refs if str(ref).strip()}


def _sender_ref_match_key(value: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if normalized.startswith("user:"):
        normalized = normalized.removeprefix("user:").strip()
    return normalized.casefold()


def _initialize_cursor_if_needed(state: dict[str, object]) -> dict[str, object]:
    if _parse_dt(state.get("last_created_at")) is not None:
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
