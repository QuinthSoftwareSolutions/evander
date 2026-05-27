from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.repositories.cursors import JsonTeamsInboxCursorRepository
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import (
    TeamsMessageFilterDefinition,
    TeamsNewMessageTriggerDefinition,
)
from agent_sandbox.services import (
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage
from agent_sandbox.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
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

        target_filters = {
            _normalize_channel_target(item.target): item.message_filter
            for item in trigger.targets
            if item.target.strip()
        }
        user_filter = {value.strip() for value in trigger.user_ids if value.strip()}
        inbox_store = JsonTeamsInboxCursorRepository(
            self._resolved_config().runtime_dir / "teams-inbox-store.json"
        )
        raw_messages = inbox_store.list_all_messages(
            channel_targets=list(target_filters) if target_filters else None,
            created_at_from=cursor,
        )

        candidates: list[TeamsInboxMessage] = []
        for message in raw_messages:
            channel_target = _message_channel_target(message)
            author_id = message.author_id.strip()
            if target_filters and channel_target not in target_filters:
                continue
            if user_filter and author_id not in user_filter:
                continue
            occurred_at = message.created_at.astimezone(UTC)
            if cursor is not None and occurred_at < cursor:
                continue
            if cursor is not None and occurred_at == cursor and message.cursor in recent_cursor_set:
                continue
            if not _message_matches_filter(message, target_filters.get(channel_target)):
                continue
            candidates.append(message)

        candidates.sort(key=lambda message: (message.created_at, message.cursor))

        events: list[TriggerEvent] = []
        for message in candidates:
            occurred_at = message.created_at.astimezone(UTC)
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{message.cursor}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload=_message_payload(message),
                )
            )
        return events

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

    def _trigger(self) -> TeamsNewMessageTriggerDefinition:
        if not isinstance(self.trigger_definition, TeamsNewMessageTriggerDefinition):
            raise RuntimeError(
                "TeamsNewMessageTriggerProvider initialized with a non-Teams trigger definition"
            )
        return self.trigger_definition

    def _resolved_config(self):
        if self.runtime_context is None:
            raise RuntimeError("TeamsNewMessageTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(
            self._resolved_config().runtime_dir / "trigger-cursors-teams.json"
        )


def _message_channel_target(message: TeamsInboxMessage) -> str:
    return _normalize_channel_target(
        teams_target_from_message(
            team_id=message.team_id,
            channel_id=message.channel_id,
            chat_id=message.chat_id,
            metadata=message.metadata,
        )
    )


def _message_payload(
    message: TeamsInboxMessage,
    *,
    target_type: str | None = None,
) -> dict[str, object]:
    channel_target = _message_channel_target(message)
    parsed_target = parse_teams_target(channel_target)
    resolved_target_type = target_type or parsed_target.kind
    created_at = message.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    message_payload: dict[str, object] = {
        "cursor": message.cursor,
        "message_id": message.message_id,
        "channel_target": channel_target,
        "target": channel_target,
        "target_type": resolved_target_type,
        "team_id": message.team_id,
        "channel_id": message.channel_id,
        "chat_id": message.chat_id,
        "thread_id": message.thread_id,
        "author_id": message.author_id,
        "author_name": message.author_name,
        "content": message.content,
        "created_at": created_at,
        "metadata": dict(message.metadata),
    }
    return {
        "channel_target": channel_target,
        "target": channel_target,
        "target_type": resolved_target_type,
        "cursor": message.cursor,
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "created_at": created_at,
        "message": message_payload,
    }


def _normalize_channel_target(value: str) -> str:
    target = normalize_teams_target(value)
    if target.startswith("user:"):
        raise ValueError("trigger:teams:new-message targets must be channel or chat targets")
    return target


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
        limit=TeamsNewMessageTriggerProvider._RECENT_CURSOR_LIMIT,
    )
    return state


def _message_matches_filter(
    message: TeamsInboxMessage,
    message_filter: TeamsMessageFilterDefinition | None,
) -> bool:
    if message_filter is None:
        return True
    content_filter = message_filter.content
    if content_filter is not None:
        haystack = message.content
        needle = content_filter.contains
        if not content_filter.case_sensitive:
            haystack = haystack.casefold()
            needle = needle.casefold()
        if needle not in haystack:
            return False

    mention_filter = message_filter.mentions
    if mention_filter is not None:
        mentioned_user_ids = _mentioned_user_ids(message)
        allowed_user_ids = {value.strip() for value in mention_filter.user_ids if value.strip()}
        if not mentioned_user_ids.intersection(allowed_user_ids):
            return False

    return True


def _mentioned_user_ids(message: TeamsInboxMessage) -> set[str]:
    raw_message = message.metadata.get("message")
    if not isinstance(raw_message, dict):
        return set()
    mentions = raw_message.get("mentions")
    if not isinstance(mentions, list):
        return set()
    user_ids: set[str] = set()
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        mentioned = mention.get("mentioned")
        if not isinstance(mentioned, dict):
            continue
        user = mentioned.get("user")
        if not isinstance(user, dict):
            continue
        user_id = str(user.get("id") or "").strip()
        if user_id:
            user_ids.add(user_id)
    return user_ids
