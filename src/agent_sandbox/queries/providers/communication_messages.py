from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_sandbox.directories import DirectoryStore
from agent_sandbox.messaging.discord_inbox_store import DiscordInboxStore
from agent_sandbox.messaging.notifications import _require_target_capability
from agent_sandbox.messaging.targets import (
    CommunicationTargetDefinition,
    load_communication_targets_config,
)
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from agent_sandbox.models import DiscordMessageEnvelope
from agent_sandbox.services import (
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)

from .base import QueryProviderBase


class CommunicationMessagesQueryProvider(QueryProviderBase):
    NODE_TYPE = "query:communication:messages"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        communication_target_id = self._require_target_id(payload.get("communication_target_id"))
        directory_path = self._normalize_optional_string(payload.get("directory_path"))
        if directory_path is None:
            directory_path = self._context_directory_path()

        runtime_dir, directory_store = self._runtime_dir_and_directory_store()
        target = self._resolve_target(
            runtime_dir=runtime_dir,
            directory_store=directory_store,
            directory_path=directory_path,
            communication_target_id=communication_target_id,
        )

        if target.provider not in {"teams", "discord"}:
            raise ValueError(
                f"{self.NODE_TYPE} does not support communication target provider {target.provider!r}"
            )

        _require_target_capability(target, "read")

        limit = self._coerce_limit(payload.get("limit"))
        cursor = self._coerce_optional_cursor(payload.get("cursor"), field_name="cursor")
        before_cursor = self._coerce_optional_cursor(
            payload.get("before_cursor"), field_name="before_cursor"
        )
        if cursor is not None and before_cursor is not None:
            raise ValueError(f"{self.NODE_TYPE} cursor and before_cursor are mutually exclusive")

        thread_id = self._normalize_optional_string(payload.get("thread_id"))
        if target.provider == "discord" and thread_id is not None:
            raise ValueError("query:communication:messages does not support thread_id for Discord")

        if target.provider == "teams":
            result = self._teams_messages(
                runtime_dir=runtime_dir,
                target=target,
                limit=limit,
                cursor=cursor,
                before_cursor=before_cursor,
                thread_id=thread_id,
            )
        else:
            result = self._discord_messages(
                runtime_dir=runtime_dir,
                target=target,
                limit=limit,
                cursor=cursor,
                before_cursor=before_cursor,
            )

        return {
            "success": True,
            "provider": target.provider,
            "communication_target_id": target.id,
            **result,
        }

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "communication_target_id",
            "limit",
            "cursor",
            "before_cursor",
            "thread_id",
            "directory_path",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("CommunicationMessagesQueryProvider has no runtime context")
        return self.runtime_context

    def _context_directory_path(self) -> str:
        metadata = self._context().metadata
        return self._normalize_optional_string(metadata.get("directory_path")) or "root"

    def _runtime_dir_and_directory_store(self) -> tuple[Path, DirectoryStore]:
        context = self._context()
        admin_service = getattr(context, "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        runtime_config = getattr(runtime, "config", None)
        runtime_dir = getattr(runtime_config, "runtime_dir", None)
        directories = getattr(runtime, "directories", None)
        if runtime_dir is not None and directories is not None:
            return Path(runtime_dir), directories

        resolver = getattr(context, "provider_config_resolver", None)
        resolver_config = getattr(resolver, "config", None)
        runtime_dir = getattr(resolver_config, "runtime_dir", None)
        directory_store = getattr(resolver, "directory_store", None)
        if runtime_dir is not None and directory_store is not None:
            return Path(runtime_dir), directory_store

        resolved_config = getattr(context, "resolved_config", None)
        runtime_dir = getattr(resolved_config, "runtime_dir", None)
        if runtime_dir is None:
            raise RuntimeError("query:communication:messages requires runtime_dir")
        runtime_dir_path = Path(runtime_dir)
        return runtime_dir_path, DirectoryStore(runtime_dir_path / "directories.json")

    def _resolve_target(
        self,
        *,
        runtime_dir: Path,
        directory_store: DirectoryStore,
        directory_path: str,
        communication_target_id: str,
    ) -> CommunicationTargetDefinition:
        targets = load_communication_targets_config(
            runtime_dir=runtime_dir,
            directory_store=directory_store,
            directory_path=directory_path,
        ).by_id()
        target = targets.get(communication_target_id)
        if target is None:
            raise ValueError(f"unknown communication target: {communication_target_id}")
        return target

    def _teams_messages(
        self,
        *,
        runtime_dir: Path,
        target: CommunicationTargetDefinition,
        limit: int | None,
        cursor: str | None,
        before_cursor: str | None,
        thread_id: str | None,
    ) -> dict[str, Any]:
        normalized_target = normalize_teams_target(target.address)
        target_messages = [
            message
            for message in TeamsInboxStore(runtime_dir / "teams-inbox-store.json").list_all_messages()
            if teams_target_from_message(
                team_id=message.team_id,
                channel_id=message.channel_id,
                chat_id=message.chat_id,
                metadata=message.metadata,
            )
            == normalized_target
        ]
        if thread_id is not None:
            target_messages = [
                message
                for message in target_messages
                if self._normalize_optional_string(message.thread_id) == thread_id
            ]

        paged_messages = self._page_teams_messages(
            messages=target_messages,
            limit=limit,
            cursor=cursor,
            before_cursor=before_cursor,
        )
        return {
            "target": normalized_target,
            "target_type": parse_teams_target(normalized_target).kind,
            "messages": [self._teams_message_payload(message) for message in paged_messages],
            "count": len(paged_messages),
            "next_cursor": self._teams_next_cursor(
                messages=paged_messages,
                cursor=cursor,
                before_cursor=before_cursor,
            ),
        }

    def _page_teams_messages(
        self,
        *,
        messages: Sequence[TeamsInboxMessage],
        limit: int | None,
        cursor: str | None,
        before_cursor: str | None,
    ) -> list[TeamsInboxMessage]:
        ordered = list(messages)
        if cursor is not None:
            if any(message.cursor == cursor for message in ordered):
                seen_cursor = False
                filtered: list[TeamsInboxMessage] = []
                for message in ordered:
                    if seen_cursor:
                        filtered.append(message)
                        continue
                    if message.cursor == cursor:
                        seen_cursor = True
                ordered = filtered
            else:
                ordered = [message for message in ordered if message.cursor > cursor]
            if limit is not None:
                ordered = ordered[:limit]
            return ordered

        if before_cursor is not None:
            ordered = [message for message in ordered if message.cursor < before_cursor]
            if limit is not None:
                ordered = ordered[-limit:]
            return ordered

        if limit is not None:
            ordered = ordered[:limit]
        return ordered

    def _teams_next_cursor(
        self,
        *,
        messages: Sequence[TeamsInboxMessage],
        cursor: str | None,
        before_cursor: str | None,
    ) -> str | None:
        if messages:
            return messages[-1].cursor
        return before_cursor if before_cursor is not None else cursor

    def _teams_message_payload(self, message: TeamsInboxMessage) -> dict[str, Any]:
        channel_target = self._teams_channel_target(message)
        parsed_target = parse_teams_target(channel_target)
        created_at = message.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return {
            "message_id": message.message_id,
            "cursor": message.cursor,
            "created_at": created_at,
            "author_id": message.author_id,
            "author_name": message.author_name,
            "content": message.content,
            "thread_id": message.thread_id,
            "metadata": dict(message.metadata),
            "channel_target": channel_target,
            "target": channel_target,
            "target_type": parsed_target.kind,
            "team_id": message.team_id,
            "channel_id": message.channel_id,
            "chat_id": message.chat_id,
        }

    @staticmethod
    def _teams_channel_target(message: TeamsInboxMessage) -> str:
        return normalize_teams_target(
            teams_target_from_message(
                team_id=message.team_id,
                channel_id=message.channel_id,
                chat_id=message.chat_id,
                metadata=message.metadata,
            )
        )

    def _discord_messages(
        self,
        *,
        runtime_dir: Path,
        target: CommunicationTargetDefinition,
        limit: int | None,
        cursor: str | None,
        before_cursor: str | None,
    ) -> dict[str, Any]:
        channel_id = target.address.split("/")[-1].strip()
        if not channel_id:
            raise ValueError("query:communication:messages requires a Discord channel target")

        raw_messages = DiscordInboxStore(runtime_dir / "discord-inbox-store.json").read_raw()
        raw_message_items = raw_messages.get("messages", {})
        if not isinstance(raw_message_items, dict):
            raw_message_items = {}
        candidates = [
            DiscordMessageEnvelope.model_validate(raw_value)
            for raw_value in raw_message_items.values()
            if isinstance(raw_value, dict)
            and str(raw_value.get("channel_id") or "").strip() == channel_id
        ]
        paged_messages = self._page_discord_messages(
            messages=candidates,
            limit=limit,
            cursor=cursor,
            before_cursor=before_cursor,
        )
        return {
            "target": self._normalize_optional_string(target.address) or channel_id,
            "target_type": "discord",
            "messages": [
                self._discord_message_payload(message, target_address=target.address)
                for message in paged_messages
            ],
            "count": len(paged_messages),
            "next_cursor": self._discord_next_cursor(
                messages=paged_messages,
                cursor=cursor,
                before_cursor=before_cursor,
            ),
        }

    def _page_discord_messages(
        self,
        *,
        messages: Sequence[DiscordMessageEnvelope],
        limit: int | None,
        cursor: str | None,
        before_cursor: str | None,
    ) -> list[DiscordMessageEnvelope]:
        ordered = sorted(messages, key=lambda item: (item.created_at, item.message_id))
        if cursor is not None:
            cursor_dt = self._parse_timestamp_cursor(cursor, field_name="cursor")
            ordered = [message for message in ordered if message.created_at > cursor_dt]
            if limit is not None:
                ordered = ordered[:limit]
            return ordered

        if before_cursor is not None:
            before_dt = self._parse_timestamp_cursor(before_cursor, field_name="before_cursor")
            ordered = [message for message in ordered if message.created_at < before_dt]
            if limit is not None:
                ordered = ordered[-limit:]
            return ordered

        if limit is not None:
            ordered = ordered[:limit]
        return ordered

    def _discord_next_cursor(
        self,
        *,
        messages: Sequence[DiscordMessageEnvelope],
        cursor: str | None,
        before_cursor: str | None,
    ) -> str | None:
        if messages:
            return self._discord_message_cursor(messages[-1])
        return before_cursor if before_cursor is not None else cursor

    def _discord_message_payload(
        self,
        message: DiscordMessageEnvelope,
        *,
        target_address: str,
    ) -> dict[str, Any]:
        channel_id = message.channel_id.strip()
        normalized_target = self._normalize_optional_string(target_address) or channel_id
        created_at = message.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return {
            "message_id": message.message_id,
            "cursor": self._discord_message_cursor(message),
            "created_at": created_at,
            "author_id": message.author_id,
            "author_name": message.author_name,
            "content": message.content,
            "thread_id": self._normalize_optional_string(message.in_reply_to),
            "metadata": dict(message.raw),
            "channel_target": normalized_target,
            "target": normalized_target,
            "target_type": "discord",
            "team_id": "",
            "channel_id": channel_id,
            "chat_id": None,
        }

    @staticmethod
    def _discord_message_cursor(message: DiscordMessageEnvelope) -> str:
        return message.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _parse_timestamp_cursor(self, value: str, *, field_name: str) -> datetime:
        text = self._normalize_required_string(value, field_name=field_name)
        normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                f"{self.NODE_TYPE} {field_name} must be an ISO-8601 UTC timestamp"
            ) from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _coerce_limit(self, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"{self.NODE_TYPE} limit must be a positive integer")
        try:
            limit = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.NODE_TYPE} limit must be a positive integer") from exc
        if limit <= 0:
            raise ValueError(f"{self.NODE_TYPE} limit must be a positive integer")
        return limit

    def _coerce_optional_cursor(self, value: Any, *, field_name: str) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            raise ValueError(f"{self.NODE_TYPE} {field_name} must be a non-empty string")
        return text

    @staticmethod
    def _require_target_id(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError("query:communication:messages requires communication_target_id")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(f"{cls.NODE_TYPE} {field_name} must be a non-empty string")
        return text
