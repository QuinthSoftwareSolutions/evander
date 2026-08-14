from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from evander.events.models import TriggerEvent
from evander.events.provider_models import (
    CommunicationNewMessageTriggerDefinition,
    TeamsDirectMessageTriggerDefinition,
    TeamsMessageFilterDefinition,
    TeamsNewMessageTriggerDefinition,
)
from evander.messaging.targets import CommunicationTargetDefinition
from evander.messaging.teams_inbox_store import TeamsInboxMessage
from evander.services import (
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)


@dataclass(frozen=True)
class TeamsNewMessageEventMatcher:
    trigger: TeamsNewMessageTriggerDefinition
    cursor: datetime | None
    recent_cursors: frozenset[str]
    target_filters: Mapping[str, TeamsMessageFilterDefinition | None]
    user_filter: frozenset[str]

    @classmethod
    def from_cursor_state(
        cls,
        trigger: TeamsNewMessageTriggerDefinition,
        state: Mapping[str, object],
    ) -> TeamsNewMessageEventMatcher:
        return cls(
            trigger=trigger,
            cursor=parse_trigger_datetime(state.get("last_created_at")),
            recent_cursors=_text_set(state.get("recent_cursors")),
            target_filters=MappingProxyType(
                {
                    normalize_teams_channel_target(item.target): item.message_filter
                    for item in trigger.targets
                    if item.target.strip()
                }
            ),
            user_filter=frozenset(value.strip() for value in trigger.user_ids if value.strip()),
        )

    def event_for(self, message: TeamsInboxMessage) -> TriggerEvent | None:
        channel_target = teams_message_channel_target(message)
        if self.target_filters and channel_target not in self.target_filters:
            return None
        if self.user_filter and message.author_id.strip() not in self.user_filter:
            return None
        occurred_at = message.created_at.astimezone(UTC)
        if message_is_behind_cursor(
            self.cursor,
            occurred_at=occurred_at,
            recent_id=message.cursor,
            recent_ids=self.recent_cursors,
        ):
            return None
        if not teams_message_matches_filter(message, self.target_filters.get(channel_target)):
            return None
        return TriggerEvent(
            event_id=f"{self.trigger.id}:{message.cursor}",
            trigger_id=self.trigger.id,
            trigger_type=self.trigger.type,
            occurred_at=occurred_at,
            payload=teams_message_payload(message),
        )


@dataclass(frozen=True)
class TeamsDirectMessageEventMatcher:
    trigger: TeamsDirectMessageTriggerDefinition
    cursor: datetime | None
    recent_cursors: frozenset[str]
    sender_filter: frozenset[str]
    target_filters: Mapping[str, TeamsMessageFilterDefinition | None]

    @classmethod
    def from_cursor_state(
        cls,
        trigger: TeamsDirectMessageTriggerDefinition,
        state: Mapping[str, object],
        *,
        missing_cursor_baseline: datetime | None = None,
    ) -> TeamsDirectMessageEventMatcher:
        cursor = parse_trigger_datetime(state.get("last_created_at"))
        if cursor is None:
            cursor = missing_cursor_baseline
        return cls(
            trigger=trigger,
            cursor=cursor,
            recent_cursors=_text_set(state.get("recent_cursors")),
            sender_filter=frozenset(
                teams_sender_ref_match_key(value)
                for value in trigger.sender_user_ids
                if value.strip()
            ),
            target_filters=MappingProxyType(
                {
                    normalize_teams_channel_target(item.target): item.message_filter
                    for item in trigger.targets
                    if item.target.strip()
                }
            ),
        )

    def event_for(self, message: TeamsInboxMessage) -> TriggerEvent | None:
        if not is_teams_direct_message(message):
            return None
        channel_target = teams_message_channel_target(message)
        if channel_target not in self.target_filters:
            return None
        if self.sender_filter and not self.sender_filter.intersection(
            teams_message_sender_ref_keys(message)
        ):
            return None
        occurred_at = message.created_at.astimezone(UTC)
        if message_is_behind_cursor(
            self.cursor,
            occurred_at=occurred_at,
            recent_id=message.cursor,
            recent_ids=self.recent_cursors,
        ):
            return None
        if not teams_message_matches_filter(message, self.target_filters[channel_target]):
            return None
        if not teams_message_matches_filter(message, self.trigger.message_filter):
            return None
        return TriggerEvent(
            event_id=f"{self.trigger.id}:{message.cursor}",
            trigger_id=self.trigger.id,
            trigger_type=self.trigger.type,
            occurred_at=occurred_at,
            payload=teams_message_payload(message, target_type="direct_message"),
        )


@dataclass(frozen=True)
class CommunicationTeamsMessageEventMatcher:
    trigger: CommunicationNewMessageTriggerDefinition
    cursor: datetime | None
    recent_keys: frozenset[str]
    targets_by_id: Mapping[str, CommunicationTargetDefinition]
    teams_targets_by_address: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_cursor_state(
        cls,
        trigger: CommunicationNewMessageTriggerDefinition,
        state: Mapping[str, object],
        *,
        targets_by_id: Mapping[str, CommunicationTargetDefinition],
        teams_targets_by_address: Mapping[str, Sequence[str]] | None = None,
    ) -> CommunicationTeamsMessageEventMatcher:
        if teams_targets_by_address is None:
            teams_targets_by_address = communication_teams_target_index(
                trigger,
                targets_by_id=targets_by_id,
            )
        return cls(
            trigger=trigger,
            cursor=parse_trigger_datetime(state.get("last_created_at")),
            recent_keys=_text_set(state.get("recent_message_ids")),
            targets_by_id=MappingProxyType(dict(targets_by_id)),
            teams_targets_by_address=MappingProxyType(
                {
                    address: tuple(target_ids)
                    for address, target_ids in teams_targets_by_address.items()
                }
            ),
        )

    def events_for(self, message: TeamsInboxMessage) -> tuple[TriggerEvent, ...]:
        if not self.teams_targets_by_address:
            return ()
        message_target = teams_message_channel_target(message)
        target_ids = self.teams_targets_by_address.get(message_target, ())
        if not target_ids or not communication_message_matches_filters(
            content=message.content,
            author_id=message.author_id,
            trigger=self.trigger,
        ):
            return ()

        occurred_at = message.created_at.astimezone(UTC)
        events: list[TriggerEvent] = []
        for target_id in target_ids:
            cursor_key = communication_message_cursor_key("teams", target_id, message.message_id)
            if message_is_behind_cursor(
                self.cursor,
                occurred_at=occurred_at,
                recent_id=cursor_key,
                recent_ids=self.recent_keys,
            ):
                continue
            target = self.targets_by_id.get(target_id)
            target_address = (
                normalize_teams_target(target.address) if target is not None else message_target
            )
            events.append(
                build_communication_message_event(
                    trigger=self.trigger,
                    provider="teams",
                    communication_target_id=target_id,
                    message_id=message.message_id,
                    thread_id=message.thread_id,
                    content=message.content,
                    author_id=message.author_id,
                    author_name=message.author_name,
                    target=target_address,
                    occurred_at=occurred_at,
                    metadata=dict(message.metadata),
                )
            )
        return tuple(events)


def communication_teams_target_index(
    trigger: CommunicationNewMessageTriggerDefinition,
    *,
    targets_by_id: Mapping[str, CommunicationTargetDefinition],
) -> Mapping[str, tuple[str, ...]]:
    teams_targets_by_address: dict[str, list[str]] = defaultdict(list)
    for target_id in trigger.communication_target_ids:
        target = targets_by_id.get(target_id)
        if target is None or target.provider != "teams":
            continue
        if "read" not in set(target.policy.capabilities):
            continue
        if parse_teams_target(target.address).kind == "user":
            continue
        teams_targets_by_address[normalize_teams_target(target.address)].append(target.id)
    return MappingProxyType(
        {address: tuple(target_ids) for address, target_ids in teams_targets_by_address.items()}
    )


def teams_message_channel_target(message: TeamsInboxMessage) -> str:
    return normalize_teams_channel_target(
        teams_target_from_message(
            team_id=message.team_id,
            channel_id=message.channel_id,
            chat_id=message.chat_id,
            metadata=message.metadata,
        )
    )


def teams_message_payload(
    message: TeamsInboxMessage,
    *,
    target_type: str | None = None,
) -> dict[str, object]:
    channel_target = teams_message_channel_target(message)
    parsed_target = parse_teams_target(channel_target)
    resolved_target_type = target_type or parsed_target.kind
    created_at = timestamp_text(message.created_at)
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


def normalize_teams_channel_target(value: str) -> str:
    target = normalize_teams_target(value)
    if target.startswith("user:"):
        raise ValueError("Teams message trigger targets must be channel or chat targets")
    return target


def teams_message_matches_filter(
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


def is_teams_direct_message(message: TeamsInboxMessage) -> bool:
    if not message.chat_id:
        return False
    target_type = str(message.metadata.get("target_type") or "").strip()
    chat_type = str(message.metadata.get("chat_type") or "").strip()
    return target_type == "direct_message" or chat_type == "oneOnOne"


def teams_message_sender_ref_keys(message: TeamsInboxMessage) -> set[str]:
    refs = {
        message.author_id,
        str(message.metadata.get("author_id") or ""),
    }
    metadata_refs = message.metadata.get("direct_message_sender_refs")
    if isinstance(metadata_refs, str):
        refs.add(metadata_refs)
    elif isinstance(metadata_refs, (list, tuple, set)):
        refs.update(str(value) for value in metadata_refs)
    return {teams_sender_ref_match_key(ref) for ref in refs if str(ref).strip()}


def teams_sender_ref_match_key(value: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if normalized.startswith("user:"):
        normalized = normalized.removeprefix("user:").strip()
    return normalized.casefold()


def build_communication_message_event(
    *,
    trigger: CommunicationNewMessageTriggerDefinition,
    provider: str,
    communication_target_id: str,
    message_id: str,
    thread_id: str | None,
    content: str,
    author_id: str,
    author_name: str,
    target: str,
    occurred_at: datetime,
    metadata: dict[str, Any],
) -> TriggerEvent:
    timestamp = timestamp_text(occurred_at)
    message_payload = {
        "provider": provider,
        "communication_target_id": communication_target_id,
        "message_id": message_id,
        "thread_id": _normalized_optional_text(thread_id),
        "content": content,
        "author_id": author_id,
        "author_name": author_name or author_id,
        "created_at": timestamp,
        "received_at": timestamp,
        "target": target,
        "metadata": metadata,
    }
    payload = {key: value for key, value in message_payload.items() if key != "metadata"}
    payload["message"] = dict(message_payload)
    return TriggerEvent(
        event_id=(f"{trigger.id}:{provider}:{communication_target_id}:{message_id}:{timestamp}"),
        trigger_id=trigger.id,
        trigger_type=trigger.type,
        occurred_at=occurred_at.astimezone(UTC),
        payload=payload,
    )


def communication_message_matches_filters(
    *,
    content: str,
    author_id: str,
    trigger: CommunicationNewMessageTriggerDefinition,
) -> bool:
    author_filter = {value.strip().casefold() for value in trigger.author_ids if value.strip()}
    if author_filter and author_id.strip().casefold() not in author_filter:
        return False

    if trigger.content_contains is not None:
        haystack = content
        needle = trigger.content_contains
        if not trigger.case_sensitive:
            haystack = haystack.casefold()
            needle = needle.casefold()
        if needle not in haystack:
            return False

    if trigger.content_starts_with is None:
        return True

    haystack = content
    needle = trigger.content_starts_with
    if not trigger.case_sensitive:
        haystack = haystack.casefold()
        needle = needle.casefold()
    return haystack.startswith(needle)


def message_is_behind_cursor(
    cursor: datetime | None,
    *,
    occurred_at: datetime,
    recent_id: str,
    recent_ids: frozenset[str] | set[str],
) -> bool:
    if cursor is None:
        return False
    occurred_at = occurred_at.astimezone(UTC)
    if occurred_at < cursor:
        return True
    return occurred_at == cursor and recent_id in recent_ids


def communication_message_cursor_key(
    provider: str,
    communication_target_id: str,
    message_id: str,
) -> str:
    return f"{provider}:{communication_target_id}:{message_id}"


def parse_trigger_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _text_set(value: object) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(item for raw in value if (item := str(raw).strip()))


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


def _normalized_optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
