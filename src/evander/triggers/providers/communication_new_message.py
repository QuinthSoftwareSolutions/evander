from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig, MailboxConfig
from evander.directories import DirectoryStore
from evander.events.models import TriggerEvent
from evander.events.provider_models import CommunicationNewMessageTriggerDefinition
from evander.models import DraftMessage
from evander.repositories.records import JsonEmailRecordsRepository
from evander.runtime import _build_email_provider
from evander.storage import JsonStore
from evander.messaging.targets import (
    CommunicationTargetDefinition,
    load_communication_targets_config,
)
from evander.repositories.cursors import (
    JsonTeamsInboxCursorRepository,
)
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.services import (
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)
from evander.messaging.notifications import _email_mailbox_id_from_auth_profile_ref
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from evander.triggers.providers.base import TriggerProviderBase


@dataclass(frozen=True)
class _PendingCommunicationEvent:
    occurred_at: datetime
    sort_key: str
    event: TriggerEvent
    cursor_key: str


class CommunicationNewMessageTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-communication.json"
    _RECENT_MESSAGE_LIMIT = 512

    def __init__(self) -> None:
        super().__init__()
        self._targets_by_id: dict[str, CommunicationTargetDefinition] | None = None
        self._teams_targets_by_address: dict[str, list[str]] | None = None
        self._discord_targets_by_channel_id: dict[str, list[str]] | None = None
        self._email_targets_by_mailbox_id: dict[str, list[str]] | None = None
        self._runtime_dir_path: Path | None = None
        self._directories_store_path: Path | None = None

    def initialize(
        self,
        trigger_definition: CommunicationNewMessageTriggerDefinition | Any,
        runtime_context: Any,
    ) -> None:
        if type(trigger_definition) is not CommunicationNewMessageTriggerDefinition:
            trigger_definition = self.validate_definition(trigger_definition)

        directory_path = str(getattr(trigger_definition, "directory_path", None) or "root").strip()
        if not directory_path:
            directory_path = "root"

        resolved_config = runtime_context.resolved_config
        if getattr(resolved_config, "directories_store_path", None) is None:
            resolver = runtime_context.provider_config_resolver
            if resolver is None:
                raise RuntimeError("CommunicationNewMessageTriggerProvider has no config resolver")
            resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
            if not callable(resolve_for_directory):
                raise RuntimeError(
                    "CommunicationNewMessageTriggerProvider cannot resolve directory config"
                )
            resolved_config = resolve_for_directory(directory_path)

        self._runtime_dir_path = Path(resolved_config.runtime_dir)
        self._directories_store_path = Path(resolved_config.directories_store_path)
        self.trigger_definition = trigger_definition
        self.runtime_context = runtime_context
        self._load_target_indexes()

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        self._ensure_target_indexes()

        state = self._cursor_store().get(trigger.id)
        cursor = _parse_dt(state.get("last_created_at"))
        recent_keys = {
            value
            for value in state.get("recent_message_ids", [])
            if isinstance(value, str) and value.strip()
        }

        candidates = self._collect_candidates(trigger, cursor=cursor, recent_keys=recent_keys)
        candidates.sort(key=lambda item: (item.occurred_at, item.sort_key))
        return [candidate.event for candidate in candidates]

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return

        payload = event.payload if isinstance(event.payload, dict) else {}
        message_payload = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        provider = _normalized_lookup_key(
            payload.get("provider") or message_payload.get("provider")
        )
        communication_target_id = _normalized_lookup_key(
            payload.get("communication_target_id") or message_payload.get("communication_target_id")
        )
        message_id = _normalized_message_id(
            payload.get("message_id") or message_payload.get("message_id")
        )
        occurred_at = _parse_dt(payload.get("created_at") or message_payload.get("created_at"))
        if occurred_at is None:
            occurred_at = event.occurred_at
        if not provider or not communication_target_id or not message_id or occurred_at is None:
            return

        cursor_key = _cursor_key(provider, communication_target_id, message_id)
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_message(state, cursor_key, occurred_at),
        )

    def _trigger(self) -> CommunicationNewMessageTriggerDefinition:
        if not isinstance(self.trigger_definition, CommunicationNewMessageTriggerDefinition):
            raise RuntimeError(
                "CommunicationNewMessageTriggerProvider initialized with the wrong trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self) -> Path:
        if self._runtime_dir_path is not None:
            return self._runtime_dir_path
        if self.runtime_context is None:
            raise RuntimeError("CommunicationNewMessageTriggerProvider has no runtime context")
        return Path(self.runtime_context.resolved_config.runtime_dir)

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / self._CURSOR_FILENAME)

    def _load_target_indexes(self) -> None:
        trigger = self._trigger()
        runtime_dir = self._runtime_dir()
        if self._directories_store_path is not None:
            directories_store_path = self._directories_store_path
        elif self.runtime_context is not None:
            directories_store_path = Path(
                self.runtime_context.resolved_config.directories_store_path
            )
        else:
            raise RuntimeError("CommunicationNewMessageTriggerProvider has no runtime context")

        directory_path = str(trigger.directory_path or "root").strip() or "root"
        config = load_communication_targets_config(
            runtime_dir=runtime_dir,
            directory_store=DirectoryStore(directories_store_path),
            directory_path=directory_path,
        )
        targets_by_id = config.by_id()
        teams_targets_by_address: dict[str, list[str]] = defaultdict(list)
        discord_targets_by_channel_id: dict[str, list[str]] = defaultdict(list)
        email_targets_by_mailbox_id: dict[str, list[str]] = defaultdict(list)
        configured_email_mailbox_ids = self._configured_email_mailbox_ids()

        for target_id in trigger.communication_target_ids:
            target = targets_by_id.get(target_id)
            if target is None:
                raise ValueError(
                    "trigger:communication:new-message target "
                    f"{target_id!r} is not defined for directory {directory_path!r}"
                )
            if "read" not in set(target.policy.capabilities):
                raise ValueError(
                    "trigger:communication:new-message target "
                    f"{target_id!r} does not allow read capability"
                )
            if target.provider == "teams":
                if parse_teams_target(target.address).kind == "user":
                    raise ValueError(
                        "trigger:communication:new-message target "
                        f"{target_id!r} is a Teams user target; only channel and chat targets are supported"
                    )
                teams_targets_by_address[normalize_teams_target(target.address)].append(target.id)
            elif target.provider == "discord":
                discord_targets_by_channel_id[_discord_channel_id(target.address)].append(target.id)
            elif target.provider == "email":
                mailbox_id = _email_mailbox_id_from_auth_profile_ref(target.policy.auth_profile_ref)
                if mailbox_id not in configured_email_mailbox_ids:
                    raise ValueError(
                        "trigger:communication:new-message target "
                        f"{target_id!r} references unknown email mailbox {mailbox_id!r}"
                    )
                email_targets_by_mailbox_id[mailbox_id].append(target.id)
            else:
                raise ValueError(
                    "trigger:communication:new-message target "
                    f"{target_id!r} uses provider {target.provider!r}; "
                    "only teams, discord, and email are supported"
                )

        self._targets_by_id = targets_by_id
        self._teams_targets_by_address = dict(teams_targets_by_address)
        self._discord_targets_by_channel_id = dict(discord_targets_by_channel_id)
        self._email_targets_by_mailbox_id = dict(email_targets_by_mailbox_id)

    def _ensure_target_indexes(self) -> None:
        if self._targets_by_id is None or (
            self._teams_targets_by_address is None
            and self._discord_targets_by_channel_id is None
            and self._email_targets_by_mailbox_id is None
        ):
            self._load_target_indexes()

    def _collect_candidates(
        self,
        trigger: CommunicationNewMessageTriggerDefinition,
        *,
        cursor: datetime | None,
        recent_keys: set[str],
    ) -> list[_PendingCommunicationEvent]:
        candidates: list[_PendingCommunicationEvent] = []
        candidates.extend(
            self._collect_teams_candidates(trigger, cursor=cursor, recent_keys=recent_keys)
        )
        candidates.extend(
            self._collect_discord_candidates(trigger, cursor=cursor, recent_keys=recent_keys)
        )
        candidates.extend(
            self._collect_email_candidates(trigger, cursor=cursor, recent_keys=recent_keys)
        )
        return candidates

    def _collect_teams_candidates(
        self,
        trigger: CommunicationNewMessageTriggerDefinition,
        *,
        cursor: datetime | None,
        recent_keys: set[str],
    ) -> list[_PendingCommunicationEvent]:
        if not self._teams_targets_by_address:
            return []

        inbox_store = JsonTeamsInboxCursorRepository(self._runtime_dir() / "teams-inbox-store.json")
        raw_messages = inbox_store.list_all_messages(
            channel_targets=list(self._teams_targets_by_address),
            created_at_from=cursor,
        )
        candidates: list[_PendingCommunicationEvent] = []

        for message in raw_messages:
            message_target = normalize_teams_target(
                teams_target_from_message(
                    team_id=message.team_id,
                    channel_id=message.channel_id,
                    chat_id=message.chat_id,
                    metadata=message.metadata,
                )
            )
            target_ids = self._teams_targets_by_address.get(message_target, [])
            if not target_ids:
                continue
            if not _message_matches_filters(
                content=message.content,
                author_id=message.author_id,
                trigger=trigger,
            ):
                continue

            occurred_at = message.created_at.astimezone(UTC)
            for target_id in target_ids:
                target = self._targets_by_id.get(target_id) if self._targets_by_id else None
                target_address = (
                    normalize_teams_target(target.address) if target is not None else message_target
                )
                cursor_key = _cursor_key("teams", target_id, message.message_id)
                if _message_is_behind_cursor(
                    cursor,
                    occurred_at=occurred_at,
                    cursor_key=cursor_key,
                    recent_keys=recent_keys,
                ):
                    continue
                candidates.append(
                    _PendingCommunicationEvent(
                        occurred_at=occurred_at,
                        sort_key=_candidate_sort_key("teams", target_id, message.message_id),
                        event=_build_event(
                            trigger=trigger,
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
                        ),
                        cursor_key=cursor_key,
                    )
                )
        return candidates

    def _collect_email_candidates(
        self,
        trigger: CommunicationNewMessageTriggerDefinition,
        *,
        cursor: datetime | None,
        recent_keys: set[str],
    ) -> list[_PendingCommunicationEvent]:
        if not self._email_targets_by_mailbox_id:
            return []

        provider = self._email_provider()
        candidates: list[_PendingCommunicationEvent] = []
        for mailbox_id, target_ids in self._email_targets_by_mailbox_id.items():
            for message in provider.list_unread(mailbox_id=mailbox_id, folder="inbox", limit=200):
                if not _message_matches_filters(
                    content=message.body_snippet,
                    author_id=message.sender,
                    trigger=trigger,
                ):
                    continue
                occurred_at = message.received_at.astimezone(UTC)
                for target_id in target_ids:
                    target = self._targets_by_id.get(target_id) if self._targets_by_id else None
                    target_address = target.address if target is not None else mailbox_id
                    cursor_key = _cursor_key("email", target_id, message.message_id)
                    if _message_is_behind_cursor(
                        cursor,
                        occurred_at=occurred_at,
                        cursor_key=cursor_key,
                        recent_keys=recent_keys,
                    ):
                        continue
                    candidates.append(
                        _PendingCommunicationEvent(
                            occurred_at=occurred_at,
                            sort_key=_candidate_sort_key("email", target_id, message.message_id),
                            event=_build_event(
                                trigger=trigger,
                                provider="email",
                                communication_target_id=target_id,
                                message_id=message.message_id,
                                thread_id=message.thread_id,
                                content=message.body_snippet,
                                author_id=message.sender,
                                author_name=message.sender,
                                target=target_address,
                                occurred_at=occurred_at,
                                metadata=dict(message.metadata),
                            ),
                            cursor_key=cursor_key,
                        )
                    )
        return candidates

    def _email_provider(self):
        admin_service = getattr(self.runtime_context, "admin_service", None)
        if admin_service is None and self.runtime_context is not None:
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
            raise RuntimeError("CommunicationNewMessageTriggerProvider has no runtime context")
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
        trigger = self.trigger_definition
        value = getattr(trigger, "directory_path", None)
        if not value and self.runtime_context is not None:
            value = self.runtime_context.metadata.get("directory_path")
        text = str(value or "root").strip()
        return text or "root"

    def _configured_email_mailbox_ids(self) -> set[str]:
        config = self._gateway_config()
        mailbox_configs = config.email_mailboxes or [
            MailboxConfig(
                mailbox_id="default",
                provider="json_store",
                account_identifier="default",
            )
        ]
        return {mailbox.mailbox_id for mailbox in mailbox_configs}

    def _collect_discord_candidates(
        self,
        trigger: CommunicationNewMessageTriggerDefinition,
        *,
        cursor: datetime | None,
        recent_keys: set[str],
    ) -> list[_PendingCommunicationEvent]:
        if not self._discord_targets_by_channel_id:
            return []

        inbox_store = DiscordInboxStore(self._runtime_dir() / "discord-inbox-store.json")
        raw_messages = inbox_store.list_all_messages(
            channel_ids=list(self._discord_targets_by_channel_id),
        ).messages
        candidates: list[_PendingCommunicationEvent] = []

        for message in raw_messages:
            channel_id = message.channel_id.strip()
            target_ids = self._discord_targets_by_channel_id.get(channel_id, [])
            if not target_ids:
                continue
            if not _message_matches_filters(
                content=message.content,
                author_id=message.author_id,
                trigger=trigger,
            ):
                continue

            occurred_at = message.created_at.astimezone(UTC)
            for target_id in target_ids:
                target = self._targets_by_id.get(target_id) if self._targets_by_id else None
                target_address = target.address if target is not None else channel_id
                cursor_key = _cursor_key("discord", target_id, message.message_id)
                if _message_is_behind_cursor(
                    cursor,
                    occurred_at=occurred_at,
                    cursor_key=cursor_key,
                    recent_keys=recent_keys,
                ):
                    continue
                candidates.append(
                    _PendingCommunicationEvent(
                        occurred_at=occurred_at,
                        sort_key=_candidate_sort_key("discord", target_id, message.message_id),
                        event=_build_event(
                            trigger=trigger,
                            provider="discord",
                            communication_target_id=target_id,
                            message_id=message.message_id,
                            thread_id=message.in_reply_to,
                            content=message.content,
                            author_id=message.author_id,
                            author_name=message.author_name,
                            target=target_address,
                            occurred_at=occurred_at,
                            metadata=dict(message.raw),
                        ),
                        cursor_key=cursor_key,
                    )
                )
        return candidates


def _build_event(
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
    timestamp = _timestamp_text(occurred_at)
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
        event_id=f"{trigger.id}:{provider}:{communication_target_id}:{message_id}:{timestamp}",
        trigger_id=trigger.id,
        trigger_type=trigger.type,
        occurred_at=occurred_at.astimezone(UTC),
        payload=payload,
    )


def _message_matches_filters(
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


def _message_is_behind_cursor(
    cursor: datetime | None,
    *,
    occurred_at: datetime,
    cursor_key: str,
    recent_keys: set[str],
) -> bool:
    if cursor is None:
        return False
    occurred_at = occurred_at.astimezone(UTC)
    if occurred_at < cursor:
        return True
    return occurred_at == cursor and cursor_key in recent_keys


def _acknowledge_message(
    state: dict[str, object],
    cursor_key: str,
    occurred_at: datetime,
) -> dict[str, object]:
    state["last_created_at"] = latest_cursor_timestamp_text(
        state.get("last_created_at"),
        occurred_at,
    )
    state["recent_message_ids"] = merge_cursor_ids(
        state.get("recent_message_ids"),
        [cursor_key],
        limit=CommunicationNewMessageTriggerProvider._RECENT_MESSAGE_LIMIT,
    )
    return state


def _cursor_key(provider: str, communication_target_id: str, message_id: str) -> str:
    return f"{provider}:{communication_target_id}:{message_id}"


def _candidate_sort_key(provider: str, communication_target_id: str, message_id: str) -> str:
    return f"{provider}:{communication_target_id}:{message_id}"


def _discord_channel_id(address: str) -> str:
    return str(address).strip().split("/")[-1]


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


def _normalized_optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_lookup_key(value: object | None) -> str:
    return str(value or "").strip().casefold()


def _normalized_message_id(value: object | None) -> str:
    return str(value or "").strip()


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
