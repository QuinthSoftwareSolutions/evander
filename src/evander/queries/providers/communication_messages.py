from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from evander.conversation_context import ConversationContextStore
from evander.integration_services.teams import teams_conversation_key
from evander.config import GatewayConfig, MailboxConfig
from evander.directories import DirectoryStore
from evander.email.providers import EmailMessageSummary
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.messaging.notifications import (
    _email_mailbox_id_from_auth_profile_ref,
    _require_target_capability,
)
from evander.messaging.targets import (
    CommunicationTargetDefinition,
    load_communication_targets_config,
)
from evander.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from evander.models import DiscordMessageEnvelope, DraftMessage
from evander.repositories.records import JsonEmailRecordsRepository
from evander.runtime import _build_email_provider
from evander.services import (
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)
from evander.storage import JsonStore

from .base import QueryProviderBase, scoped_query_directory_path


class CommunicationMessagesQueryProvider(QueryProviderBase):
    NODE_TYPE = "query:communication:messages"
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 200

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

        if target.provider not in {"teams", "discord", "email"}:
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

        self._context_session_id = self._normalize_optional_string(payload.get("context_session_id"))
        self._conversation_key = self._normalize_optional_string(payload.get("conversation_key"))
        if bool(self._context_session_id) != bool(self._conversation_key):
            raise ValueError("context_session_id and conversation_key must be supplied together")
        if self._context_session_id:
            if target.provider != "teams":
                raise ValueError("Conversation context history currently supports Teams targets")
            directory_path = scoped_query_directory_path(self._context(), directory_path)
            runtime = getattr(self._context().admin_service, "runtime", None)
            if runtime is None:
                raise RuntimeError("Conversation history binding requires the host session policy")
            session = runtime.session_repository.resolve_session_target(self._context_session_id)
            if session is None:
                raise ValueError("Unknown conversation context session")
            session_directory = session.directory_path
            if session_directory != directory_path and not session_directory.startswith(
                directory_path + "/"
            ):
                raise ValueError("History context session is outside query directory scope")
            normalized_target = normalize_teams_target(target.address)
            if self._conversation_key != teams_conversation_key(normalized_target):
                raise ValueError("History conversation key does not match communication target")
            runtime.policy.require_teams_channel_target(session, normalized_target)
            self._context_session_id = session.session_id
        thread_id = self._normalize_optional_string(payload.get("thread_id"))
        if target.provider == "teams":
            result = self._teams_messages(
                runtime_dir=runtime_dir,
                target=target,
                limit=limit,
                cursor=cursor,
                before_cursor=before_cursor,
                thread_id=thread_id,
            )
        elif target.provider == "discord":
            result = self._discord_messages(
                runtime_dir=runtime_dir,
                target=target,
                limit=limit,
                cursor=cursor,
                before_cursor=before_cursor,
                thread_id=thread_id,
            )
        else:
            result = self._email_messages(
                directory_path=directory_path,
                target=target,
                limit=limit,
                cursor=cursor,
                before_cursor=before_cursor,
                thread_id=thread_id,
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
            "context_session_id",
            "conversation_key",
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
            for message in TeamsInboxStore(
                runtime_dir / "teams-inbox-store.db"
            ).list_all_messages(channel_targets=[normalized_target])
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

        if self._context_session_id:
            target_messages = ConversationContextStore(runtime_dir).filter_history(
                target_messages, session_id=self._context_session_id,
                conversation_key=self._conversation_key, classification=True,
            )
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
        thread_id: str | None,
    ) -> dict[str, Any]:
        channel_id = target.address.split("/")[-1].strip()
        if not channel_id:
            raise ValueError("query:communication:messages requires a Discord channel target")

        store = DiscordInboxStore(runtime_dir / "discord-inbox-store.db")
        scope = target.policy.discord_conversation_scope
        if thread_id is not None:
            binding = store.get_thread_binding(thread_id)
            if (
                scope not in {"public_threads", "channel_and_public_threads"}
                or binding is None
                or binding.parent_channel_id != channel_id
            ):
                raise ValueError(
                    "query:communication:messages Discord thread_id is not authorized by the selected target"
                )
            selected_channel_ids = [thread_id]
        else:
            selected_channel_ids = [] if scope == "public_threads" else [channel_id]
            if scope in {"public_threads", "channel_and_public_threads"}:
                selected_channel_ids.extend(
                    binding.thread_id
                    for binding in store.list_thread_bindings(
                        parent_channel_ids=[channel_id],
                        include_archived=True,
                    )
                )
        candidates = store.list_all_messages(channel_ids=selected_channel_ids).messages
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
        ordered = sorted(messages, key=self._discord_message_sort_key)
        if cursor is not None:
            cursor_key = self._parse_discord_cursor(cursor, field_name="cursor")
            ordered = [
                message
                for message in ordered
                if self._discord_message_is_after_cursor(message, cursor_key)
            ]
            if limit is not None:
                ordered = ordered[:limit]
            return ordered

        if before_cursor is not None:
            before_key = self._parse_discord_cursor(before_cursor, field_name="before_cursor")
            ordered = [
                message
                for message in ordered
                if self._discord_message_is_before_cursor(message, before_key)
            ]
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
            "thread_id": self._normalize_optional_string(message.thread_id),
            "in_reply_to": self._normalize_optional_string(message.in_reply_to),
            "parent_channel_id": self._normalize_optional_string(message.parent_channel_id),
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
        created_at = message.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        timestamp = created_at.isoformat().replace("+00:00", "Z")
        return f"{timestamp}|{quote(message.message_id, safe='')}"

    def _parse_discord_cursor(self, value: str, *, field_name: str) -> tuple[datetime, str | None]:
        text = self._normalize_required_string(value, field_name=field_name)
        timestamp_text, separator, encoded_message_id = text.partition("|")
        cursor_dt = self._parse_timestamp_cursor(timestamp_text, field_name=field_name)
        if not separator:
            return cursor_dt, None
        message_id = unquote(encoded_message_id)
        if not message_id:
            raise ValueError(
                f"{self.NODE_TYPE} {field_name} must include a non-empty Discord message id"
            )
        return cursor_dt, message_id

    @classmethod
    def _discord_message_is_after_cursor(
        cls,
        message: DiscordMessageEnvelope,
        cursor_key: tuple[datetime, str | None],
    ) -> bool:
        cursor_dt, cursor_message_id = cursor_key
        message_key = cls._discord_message_sort_key(message)
        if cursor_message_id is None:
            return message_key[0] > cursor_dt
        return message_key > (cursor_dt, cursor_message_id)

    @classmethod
    def _discord_message_is_before_cursor(
        cls,
        message: DiscordMessageEnvelope,
        cursor_key: tuple[datetime, str | None],
    ) -> bool:
        cursor_dt, cursor_message_id = cursor_key
        message_key = cls._discord_message_sort_key(message)
        if cursor_message_id is None:
            return message_key[0] < cursor_dt
        return message_key < (cursor_dt, cursor_message_id)

    @staticmethod
    def _discord_message_sort_key(message: DiscordMessageEnvelope) -> tuple[datetime, str]:
        created_at = message.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        return created_at, message.message_id

    def _email_messages(
        self,
        *,
        directory_path: str,
        target: CommunicationTargetDefinition,
        limit: int | None,
        cursor: str | None,
        before_cursor: str | None,
        thread_id: str | None,
    ) -> dict[str, Any]:
        mailbox_id = self._email_mailbox_id(target=target, directory_path=directory_path)
        provider = self._email_provider(directory_path=directory_path)
        candidates = provider.list_unread(
            mailbox_id=mailbox_id,
            folder="inbox",
            limit=max(limit or self.DEFAULT_LIMIT, self.MAX_LIMIT),
        )
        if thread_id is not None:
            candidates = [
                message
                for message in candidates
                if self._normalize_optional_string(message.thread_id) == thread_id
            ]
        paged_messages = self._page_email_messages(
            messages=candidates,
            limit=limit,
            cursor=cursor,
            before_cursor=before_cursor,
        )
        target_address = self._normalize_optional_string(target.address) or mailbox_id
        return {
            "target": target_address,
            "target_type": "email",
            "messages": [
                self._email_message_payload(message, target_address=target_address)
                for message in paged_messages
            ],
            "count": len(paged_messages),
            "next_cursor": self._email_next_cursor(
                messages=paged_messages,
                cursor=cursor,
                before_cursor=before_cursor,
            ),
        }

    def _page_email_messages(
        self,
        *,
        messages: Sequence[EmailMessageSummary],
        limit: int | None,
        cursor: str | None,
        before_cursor: str | None,
    ) -> list[EmailMessageSummary]:
        ordered = sorted(messages, key=self._email_message_sort_key)
        if cursor is not None:
            cursor_key = self._parse_email_cursor(cursor, field_name="cursor")
            ordered = [
                message
                for message in ordered
                if self._email_message_is_after_cursor(message, cursor_key)
            ]
            if limit is not None:
                ordered = ordered[:limit]
            return ordered

        if before_cursor is not None:
            before_key = self._parse_email_cursor(before_cursor, field_name="before_cursor")
            ordered = [
                message
                for message in ordered
                if self._email_message_is_before_cursor(message, before_key)
            ]
            if limit is not None:
                ordered = ordered[-limit:]
            return ordered

        if limit is not None:
            ordered = ordered[:limit]
        return ordered

    def _email_next_cursor(
        self,
        *,
        messages: Sequence[EmailMessageSummary],
        cursor: str | None,
        before_cursor: str | None,
    ) -> str | None:
        if messages:
            return self._email_message_cursor(messages[-1])
        return before_cursor if before_cursor is not None else cursor

    def _email_message_payload(
        self,
        message: EmailMessageSummary,
        *,
        target_address: str,
    ) -> dict[str, Any]:
        created_at = message.received_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return {
            "message_id": message.message_id,
            "cursor": self._email_message_cursor(message),
            "created_at": created_at,
            "author_id": message.sender,
            "author_name": message.sender,
            "content": message.body_snippet,
            "thread_id": self._normalize_optional_string(message.thread_id),
            "metadata": dict(message.metadata),
            "channel_target": target_address,
            "target": target_address,
            "target_type": "email",
            "team_id": "",
            "channel_id": message.mailbox_id,
            "chat_id": None,
            "mailbox_id": message.mailbox_id,
            "email_provider": message.provider,
            "sender": message.sender,
            "recipients": list(message.recipients),
        }

    @staticmethod
    def _email_message_cursor(message: EmailMessageSummary) -> str:
        received_at = message.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        else:
            received_at = received_at.astimezone(UTC)
        timestamp = received_at.isoformat().replace("+00:00", "Z")
        return f"{timestamp}|{quote(message.message_id, safe='')}"

    def _parse_email_cursor(self, value: str, *, field_name: str) -> tuple[datetime, str | None]:
        text = self._normalize_required_string(value, field_name=field_name)
        timestamp_text, separator, encoded_message_id = text.partition("|")
        cursor_dt = self._parse_timestamp_cursor(timestamp_text, field_name=field_name)
        if not separator:
            return cursor_dt, None
        message_id = unquote(encoded_message_id)
        if not message_id:
            raise ValueError(
                f"{self.NODE_TYPE} {field_name} must include a non-empty email message id"
            )
        return cursor_dt, message_id

    @classmethod
    def _email_message_is_after_cursor(
        cls,
        message: EmailMessageSummary,
        cursor_key: tuple[datetime, str | None],
    ) -> bool:
        cursor_dt, cursor_message_id = cursor_key
        message_key = cls._email_message_sort_key(message)
        if cursor_message_id is None:
            return message_key[0] > cursor_dt
        return message_key > (cursor_dt, cursor_message_id)

    @classmethod
    def _email_message_is_before_cursor(
        cls,
        message: EmailMessageSummary,
        cursor_key: tuple[datetime, str | None],
    ) -> bool:
        cursor_dt, cursor_message_id = cursor_key
        message_key = cls._email_message_sort_key(message)
        if cursor_message_id is None:
            return message_key[0] < cursor_dt
        return message_key < (cursor_dt, cursor_message_id)

    @staticmethod
    def _email_message_sort_key(message: EmailMessageSummary) -> tuple[datetime, str]:
        received_at = message.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        else:
            received_at = received_at.astimezone(UTC)
        return received_at, message.message_id

    def _email_provider(self, *, directory_path: str):
        admin_service = getattr(self._context(), "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        config = self._gateway_config(directory_path=directory_path)
        if runtime is not None:
            return _build_email_provider(config, runtime.emails, runtime.drafts, None)
        return _build_email_provider(
            config,
            JsonEmailRecordsRepository(config.email_store_path),
            JsonStore(config.draft_store_path, DraftMessage),
            None,
        )

    def _gateway_config(self, *, directory_path: str) -> GatewayConfig:
        context = self._context()
        admin_service = getattr(context, "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        provider_config = getattr(runtime, "provider_config", None)
        resolve_for_directory = getattr(provider_config, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(directory_path)

        resolver = getattr(context, "provider_config_resolver", None)
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(directory_path)

        raw_provider_config = getattr(context, "provider_config", None)
        if raw_provider_config:
            return GatewayConfig.model_validate(dict(raw_provider_config))
        resolved_config = getattr(context, "resolved_config", None)
        model_dump = getattr(resolved_config, "model_dump", None)
        if callable(model_dump):
            return GatewayConfig.model_validate(model_dump(mode="json"))
        return GatewayConfig.model_validate(vars(resolved_config))

    def _email_mailbox_id(
        self,
        *,
        target: CommunicationTargetDefinition,
        directory_path: str,
    ) -> str:
        mailbox_id = _email_mailbox_id_from_auth_profile_ref(target.policy.auth_profile_ref)
        mailbox_configs = self._gateway_config(directory_path=directory_path).email_mailboxes or [
            MailboxConfig(
                mailbox_id="default",
                provider="json_store",
                account_identifier="default",
            )
        ]
        mailbox_ids = {mailbox.mailbox_id for mailbox in mailbox_configs}
        if mailbox_id not in mailbox_ids:
            raise ValueError(
                f"email communication target {target.id!r} references unknown mailbox "
                f"{mailbox_id!r}"
            )
        return mailbox_id

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
            return self.DEFAULT_LIMIT
        if isinstance(value, bool):
            raise ValueError(f"{self.NODE_TYPE} limit must be between 1 and {self.MAX_LIMIT}")
        try:
            limit = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.NODE_TYPE} limit must be between 1 and {self.MAX_LIMIT}"
            ) from exc
        if limit < 1 or limit > self.MAX_LIMIT:
            raise ValueError(f"{self.NODE_TYPE} limit must be between 1 and {self.MAX_LIMIT}")
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
