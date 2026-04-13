from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.events.models import TeamsMessageFilterDefinition, TeamsNewMessageTriggerDefinition, TriggerEvent
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

        target_filters = {
            _normalize_channel_target(item.target): item.message_filter
            for item in trigger.targets
            if item.target.strip()
        }
        user_filter = {value.strip() for value in trigger.user_ids if value.strip()}

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

        max_created_at = cursor
        new_cursors = list(recent_cursors)
        latest_cursor: str | None = None
        for message in candidates:
            occurred_at = message.created_at.astimezone(UTC)
            new_cursors.append(message.cursor)
            latest_cursor = message.cursor
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
        if not candidates or max_created_at is None or latest_cursor is None:
            return []
        return [
            TriggerEvent(
                event_id=f"{trigger.id}:{latest_cursor}",
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                occurred_at=max_created_at,
                payload={},
            )
        ]

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
