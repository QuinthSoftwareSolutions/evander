"""Discord provider and inbox services."""

from __future__ import annotations
import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote
import httpx
from evander.models import (
    DiscordCommandEnvelope,
    DiscordMessageEnvelope,
    HasPendingDiscordMessagesResponse,
    ListUnreadDiscordMessagesResponse,
)
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.discord_webhooks import (
    DiscordWebhookAvatar,
    DiscordWebhookCredential,
    DiscordWebhookCredentialStore,
    discord_webhook_credential_key,
    prepare_discord_webhook_avatar,
)
from evander.outbound_identity import OutboundIdentity
from evander.repositories.cursors import DiscordInboxCursorRepository
from evander.repositories.records import DiscordRecordsRepository

from .common import (
    _LOGGER,
    _artifact_bytes,
    _artifact_content_type,
    _artifact_filename,
    _buffer_streaming_response,
    _message_sort_value,
    _parse_snowflake,
    _sanitize_attachment_metadata,
)


class DiscordService(Protocol):
    def get_commands(self, since_cursor: str | None = None) -> list["DiscordCommandEnvelope"]: ...

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]: ...

    def list_attachments(
        self, message_id: str, *, channel_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        *,
        channel_id: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]: ...

    def post_file(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
        session_id: str,
    ) -> dict[str, Any]: ...


class StubDiscordService:
    def __init__(
        self,
        command_store: DiscordRecordsRepository,
        *,
        read_reaction_emoji: str = "👀",
    ) -> None:
        self.command_store = command_store
        self.read_reaction_emoji = read_reaction_emoji

    def get_commands(self, since_cursor: str | None = None) -> list[DiscordCommandEnvelope]:
        commands = list(self.command_store.read_all().values())
        for command in commands:
            command.metadata = {
                "source": command.metadata.get("source", "stub"),
                "guild_id": command.metadata.get("guild_id"),
                "channel_id": command.metadata.get("channel_id", command.channel_id),
                "author_id": command.metadata.get("author_id", command.author_id),
                **{
                    key: value
                    for key, value in command.metadata.items()
                    if key not in {"source", "guild_id", "channel_id", "author_id"}
                },
            }
        commands.sort(key=lambda item: _parse_snowflake(item.command_id, field_name="command_id"))
        if since_cursor is None:
            return commands
        since_snowflake = _parse_snowflake(since_cursor, field_name="since_cursor")
        return [
            command
            for command in commands
            if _parse_snowflake(command.command_id, field_name="command_id") > since_snowflake
        ]

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]:
        return {
            "status": "stubbed",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_preview": message[:120],
        }

    def list_attachments(
        self, message_id: str, *, channel_id: str | None = None
    ) -> list[dict[str, Any]]:
        command = self.command_store.get(message_id)
        if command is None:
            return []
        if channel_id is not None and command.channel_id != channel_id:
            return []
        message = command.metadata.get("message")
        raw_attachments = command.metadata.get("attachments")
        if raw_attachments is None and isinstance(message, dict):
            raw_attachments = message.get("attachments")
        return [_sanitize_attachment_metadata(item) for item in raw_attachments or []]

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        *,
        channel_id: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        for attachment in self.list_attachments(message_id, channel_id=channel_id):
            if str(attachment.get("id") or attachment.get("attachment_id") or "") == attachment_id:
                content = attachment.get("content") or attachment.get("content_base64")
                if isinstance(content, str):
                    return {**attachment, "content": content}
        raise ValueError(f"Unknown Discord attachment: {attachment_id}")

    def post_file(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "status": "stubbed",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_preview": message[:120],
            "artifact_count": len(artifacts),
        }

    def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
        session_id: str,
    ) -> dict[str, Any]:
        return {
            "status": "stubbed",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_id": message_id,
            "emoji": emoji,
            "reaction": emoji,
        }


class DiscordBotApiService:
    def __init__(
        self,
        command_store: DiscordRecordsRepository,
        *,
        token: str,
        api_base_url: str,
        poll_limit: int,
        timeout_seconds: float,
        retry_count: int = 3,
        retry_base_backoff_seconds: float = 0.5,
        retry_max_backoff_seconds: float = 5.0,
        allowed_channel_ids_provider: Callable[[], list[str]] = lambda: [],
        poll_channel_ids_provider: Callable[[], list[str]] | None = None,
        allowed_webhook_channel_ids_provider: Callable[[], list[str]] = lambda: [],
        operator_user_ids_provider: Callable[[], list[str]] | None = None,
        include_operator_dms_provider: Callable[[], bool] | None = None,
        include_operator_dms: bool = False,
        read_reaction_emoji: str = "👀",
        bot_token_env: str = "DISCORD_BOT_TOKEN",
        webhook_store_path: str | Path | None = None,
        persona_webhook_cache_limit: int = 10,
    ) -> None:
        self.command_store = command_store
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.poll_limit = poll_limit
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.retry_base_backoff_seconds = retry_base_backoff_seconds
        self.retry_max_backoff_seconds = retry_max_backoff_seconds
        self.max_total_retry_time_seconds = sum(
            min(self.retry_max_backoff_seconds, self.retry_base_backoff_seconds * (2**attempt))
            for attempt in range(self.retry_count)
        )
        self.allowed_channel_ids_provider = allowed_channel_ids_provider
        self.poll_channel_ids_provider = poll_channel_ids_provider or allowed_channel_ids_provider
        self.allowed_webhook_channel_ids_provider = allowed_webhook_channel_ids_provider
        self.operator_user_ids_provider = operator_user_ids_provider or (lambda: [])
        self.include_operator_dms_provider = include_operator_dms_provider or (
            lambda: include_operator_dms
        )
        self.read_reaction_emoji = read_reaction_emoji
        self.bot_token_env = bot_token_env
        self.webhook_store = (
            DiscordWebhookCredentialStore(webhook_store_path)
            if webhook_store_path is not None
            else None
        )
        self.persona_webhook_cache_limit = max(1, min(10, int(persona_webhook_cache_limit)))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _cursor_sort_value(command_id: str) -> tuple[int, int | str]:
        if command_id.isdigit():
            return (0, int(command_id))
        return (1, command_id)

    @classmethod
    def _is_after_cursor(cls, command_id: str, since_cursor: str) -> bool:
        if command_id.isdigit() and since_cursor.isdigit():
            return int(command_id) > int(since_cursor)
        return command_id > since_cursor

    @classmethod
    def _sorted_commands(
        cls,
        envelopes: dict[str, DiscordCommandEnvelope],
    ) -> list[DiscordCommandEnvelope]:
        return sorted(
            envelopes.values(),
            key=lambda item: (item.created_at, cls._cursor_sort_value(item.command_id)),
        )

    def _known_channel_ids(self) -> list[str]:
        channel_ids = {command.channel_id for command in self.command_store.read_all().values()}
        return sorted(channel_ids)

    def _allowed_channel_ids(self) -> list[str]:
        return sorted(
            {
                channel_id.strip()
                for channel_id in self.allowed_channel_ids_provider()
                if channel_id.strip()
            }
        )

    def _poll_channel_ids(self) -> list[str]:
        return sorted(
            {
                channel_id.strip()
                for channel_id in self.poll_channel_ids_provider()
                if channel_id.strip()
            }
        )

    def _allowed_webhook_channel_ids(self) -> list[str]:
        return sorted(
            {
                channel_id.strip()
                for channel_id in self.allowed_webhook_channel_ids_provider()
                if channel_id.strip()
            }
        )

    def _operator_user_ids(self) -> list[str]:
        return sorted(
            {user_id.strip() for user_id in self.operator_user_ids_provider() if user_id.strip()}
        )

    def _operator_dm_channel_ids(self, client: httpx.Client) -> list[str]:
        if not self.include_operator_dms_provider():
            return []
        operator_user_ids = set(self._operator_user_ids())
        if not operator_user_ids:
            return []
        response = self._request_with_retries(
            client,
            "GET",
            f"{self.api_base_url}/users/@me/channels",
            params={"limit": self.poll_limit},
        )
        dm_channel_ids: list[str] = []
        for payload in response.json():
            recipients = payload.get("recipients") or []
            recipient_user_ids = {
                str(recipient.get("id", "")).strip()
                for recipient in recipients
                if isinstance(recipient, dict)
            }
            if recipient_user_ids & operator_user_ids:
                channel_id = str(payload.get("id", "")).strip()
                if channel_id:
                    dm_channel_ids.append(channel_id)
        return sorted(set(dm_channel_ids))

    def _should_retry_http_status(self, status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    def _request_with_retries(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
        max_response_bytes: int | None = None,
    ) -> httpx.Response:
        start_time = time.monotonic()
        attempt = 0
        last_error: Exception | None = None
        while True:
            try:
                request_headers = self._headers() if headers is None else headers
                if max_response_bytes is None:
                    response = client.request(
                        method,
                        url,
                        headers=request_headers,
                        params=params,
                        json=json,
                        data=data,
                        files=files,
                        timeout=self.timeout_seconds,
                    )
                    response.raise_for_status()
                else:
                    request = client.build_request(
                        method,
                        url,
                        headers=request_headers,
                        params=params,
                        json=json,
                        data=data,
                        files=files,
                        timeout=self.timeout_seconds,
                    )
                    response = _buffer_streaming_response(
                        client.send(request, stream=True),
                        max_bytes=max_response_bytes,
                    )
                return response
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                if attempt >= self.retry_count:
                    raise
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if not self._should_retry_http_status(status_code) or attempt >= self.retry_count:
                    raise
                last_error = exc

            planned_backoff = min(
                self.retry_max_backoff_seconds,
                self.retry_base_backoff_seconds * (2**attempt),
            )
            jittered_backoff = random.uniform(0.5 * planned_backoff, 1.5 * planned_backoff)
            elapsed = time.monotonic() - start_time
            remaining_budget = self.max_total_retry_time_seconds - elapsed
            if remaining_budget <= 0:
                if last_error is not None:
                    raise last_error
                raise TimeoutError("Retry budget exhausted before completing Discord API request")
            sleep_for = min(jittered_backoff, remaining_budget)
            time.sleep(sleep_for)
            attempt += 1

    def get_commands(self, since_cursor: str | None = None) -> list[DiscordCommandEnvelope]:
        envelopes: dict[str, DiscordCommandEnvelope] = dict(self.command_store.read_all())
        fetched_commands: dict[str, DiscordCommandEnvelope] = {}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            channel_ids = sorted(
                set(self._poll_channel_ids()) | set(self._operator_dm_channel_ids(client))
            )
            for channel_id in channel_ids:
                response = self._request_with_retries(
                    client,
                    "GET",
                    f"{self.api_base_url}/channels/{channel_id}/messages",
                    params={"limit": self.poll_limit},
                )
                for payload in response.json():
                    attachments = [
                        _sanitize_attachment_metadata(item)
                        for item in payload.get("attachments") or []
                        if isinstance(item, dict)
                    ]
                    safe_payload = dict(payload)
                    safe_payload["attachments"] = attachments
                    metadata: dict[str, Any] = {
                        "source": "discord_bot_api",
                        "guild_id": payload.get("guild_id"),
                        "channel_id": channel_id,
                        "author_id": payload["author"]["id"],
                        "attachments": attachments,
                        "message": safe_payload,
                    }
                    existing = envelopes.get(str(payload["id"]))
                    if existing is not None and existing.metadata.get("source") in {
                        "discord_bot_api_status",
                        "discord_webhook_status",
                    }:
                        for key in (
                            "source",
                            "session_id",
                            "transport",
                            "identity_source",
                            "identity_status",
                            "persona_name_applied",
                            "persona_avatar_applied",
                            "fallback_reason",
                        ):
                            if key in existing.metadata:
                                metadata[key] = existing.metadata[key]
                    envelope = DiscordCommandEnvelope(
                        command_id=payload["id"],
                        author_id=payload["author"]["id"],
                        channel_id=channel_id,
                        content=payload.get("content", ""),
                        metadata=metadata,
                    )
                    envelopes[envelope.command_id] = envelope
                    fetched_commands[envelope.command_id] = envelope

        _put_discord_commands(self.command_store, fetched_commands)

        commands = sorted(
            envelopes.values(),
            key=lambda item: _parse_snowflake(item.command_id, field_name="command_id"),
        )
        if since_cursor is None:
            return commands
        since_snowflake = _parse_snowflake(since_cursor, field_name="since_cursor")
        return [
            command
            for command in commands
            if _parse_snowflake(command.command_id, field_name="command_id") > since_snowflake
        ]

    def _require_allowed_channel_id(self, channel_id: str) -> None:
        allowed = set(self._allowed_channel_ids())
        if allowed and channel_id not in allowed:
            raise ValueError(f"Discord channel is not allowlisted: {channel_id}")

    def _require_allowed_webhook_channel_id(self, channel_id: str) -> None:
        allowed = set(self._allowed_channel_ids()) | set(self._allowed_webhook_channel_ids())
        if allowed and channel_id not in allowed:
            raise ValueError(f"Discord webhook channel is not allowlisted: {channel_id}")

    def _find_message_attachment(
        self,
        message_id: str,
        attachment_id: str | None = None,
        *,
        channel_id: str | None = None,
    ) -> tuple[DiscordCommandEnvelope, list[dict[str, Any]], dict[str, Any] | None]:
        command = self.command_store.get(message_id)
        if command is None:
            raise ValueError(f"Unknown Discord message_id: {message_id}")
        if channel_id is not None and command.channel_id != channel_id:
            raise ValueError("Discord message does not belong to the requested channel.")
        self._require_allowed_channel_id(command.channel_id)
        message = command.metadata.get("message")
        raw_attachments = command.metadata.get("attachments")
        if raw_attachments is None and isinstance(message, dict):
            raw_attachments = message.get("attachments")
        attachments = [
            _sanitize_attachment_metadata(item)
            for item in raw_attachments or []
            if isinstance(item, dict)
        ]
        if attachment_id is None:
            return command, attachments, None
        for attachment in attachments:
            if str(attachment.get("id") or attachment.get("attachment_id") or "") == attachment_id:
                return command, attachments, attachment
        raise ValueError(f"Unknown Discord attachment: {attachment_id}")

    def list_attachments(
        self, message_id: str, *, channel_id: str | None = None
    ) -> list[dict[str, Any]]:
        _, attachments, _ = self._find_message_attachment(message_id, channel_id=channel_id)
        return attachments

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        *,
        channel_id: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        _, _, attachment = self._find_message_attachment(
            message_id,
            attachment_id,
            channel_id=channel_id,
        )
        assert attachment is not None
        download_url = str(attachment.get("url") or "").strip()
        if not download_url:
            raise ValueError(f"Discord attachment {attachment_id} does not include a download URL")
        headers = {"Authorization": f"Bot {self.token}"}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = self._request_with_retries(
                client,
                "GET",
                download_url,
                headers=headers,
                max_response_bytes=max_bytes,
            )
            content_type = response.headers.get("content-type") or attachment.get("content_type")
            return {
                **attachment,
                "filename": attachment.get("filename") or attachment.get("name") or attachment_id,
                "mime_type": content_type,
                "content": response.content,
            }

    def _persist_posted_message(
        self,
        payload: dict[str, Any],
        *,
        channel_id: str,
        session_id: str,
        source: str,
        transport: str,
        identity: OutboundIdentity | None,
        identity_status: str,
        fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        attachments = [
            _sanitize_attachment_metadata(item)
            for item in payload.get("attachments") or []
            if isinstance(item, dict)
        ]
        safe_payload = dict(payload)
        safe_payload["attachments"] = attachments
        author = payload.get("author") if isinstance(payload.get("author"), dict) else {}
        author_id = str(author.get("id") or "")
        envelope = DiscordCommandEnvelope(
            command_id=str(payload["id"]),
            author_id=author_id,
            channel_id=channel_id,
            content=str(payload.get("content") or ""),
            metadata={
                "source": source,
                "guild_id": payload.get("guild_id"),
                "channel_id": channel_id,
                "author_id": author_id,
                "session_id": session_id,
                "attachments": attachments,
                "message": safe_payload,
                "transport": transport,
                "identity_source": identity.source if identity is not None else None,
                "identity_status": identity_status,
                "persona_name_applied": identity is not None,
                "persona_avatar_applied": bool(
                    identity is not None and identity.avatar is not None
                ),
                "fallback_reason": fallback_reason,
            },
        )
        self.command_store.put(envelope.command_id, envelope)
        result: dict[str, Any] = {
            "status": "posted",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_id": envelope.command_id,
        }
        if identity_status != "not_requested" or fallback_reason:
            result.update(
                {
                    "transport": transport,
                    "identity_status": identity_status,
                    "identity_source": identity.source if identity is not None else None,
                    "persona_name_applied": identity is not None,
                    "persona_avatar_applied": bool(
                        identity is not None and identity.avatar is not None
                    ),
                }
            )
        if fallback_reason:
            result["fallback_reason"] = fallback_reason
        if attachments:
            result["artifact_count"] = len(attachments)
        return result

    def webhook_status(self, channel_id: str) -> dict[str, Any]:
        if self.webhook_store is None:
            return {
                "configured": False,
                "channel_id": channel_id,
                "channel_webhook_count": 0,
                "persona_webhook_count": 0,
                "persona_webhook_cache_limit": self.persona_webhook_cache_limit,
                "persona_webhooks": [],
            }
        credentials = self.webhook_store.list_for_channel(
            api_base_url=self.api_base_url,
            bot_token_env=self.bot_token_env,
            channel_id=channel_id,
        )
        platform_credential = next(
            (credential for credential in credentials if credential.avatar_sha256 is None),
            None,
        )
        persona_credentials = [
            credential for credential in credentials if credential.avatar_sha256 is not None
        ]
        selected = platform_credential or (credentials[-1] if credentials else None)
        result = (
            selected.safe_status()
            if selected is not None
            else {"configured": False, "channel_id": channel_id}
        )
        result.update(
            {
                "configured": bool(credentials),
                "channel_webhook_count": len(credentials),
                "persona_webhook_count": len(persona_credentials),
                "persona_webhook_cache_limit": self.persona_webhook_cache_limit,
                "persona_webhooks": [
                    credential.safe_status() for credential in persona_credentials
                ],
            }
        )
        return result

    def provision_webhook(
        self,
        channel_id: str,
        *,
        force: bool = False,
        avatar: DiscordWebhookAvatar | None = None,
    ) -> dict[str, Any]:
        if self.webhook_store is None:
            raise RuntimeError("Discord webhook credential storage is not configured")
        self._require_allowed_webhook_channel_id(channel_id)
        avatar_sha256 = avatar.sha256 if avatar is not None else None
        with self.webhook_store.provision_lock():
            existing = self.webhook_store.get(
                api_base_url=self.api_base_url,
                bot_token_env=self.bot_token_env,
                channel_id=channel_id,
                avatar_sha256=avatar_sha256,
            )
            if existing is not None and not force:
                return existing.safe_status()
            request_payload = {"name": "Evander"}
            if avatar is not None:
                request_payload["avatar"] = avatar.image_data
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.api_base_url}/channels/{channel_id}/webhooks",
                    headers=self._headers(),
                    json=request_payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            now = datetime.now(UTC)
            credential = DiscordWebhookCredential(
                key=discord_webhook_credential_key(
                    api_base_url=self.api_base_url,
                    bot_token_env=self.bot_token_env,
                    channel_id=channel_id,
                    avatar_sha256=avatar_sha256,
                ),
                channel_id=channel_id,
                api_base_url=self.api_base_url,
                bot_token_env=self.bot_token_env,
                webhook_id=str(payload.get("id") or ""),
                token=str(payload.get("token") or ""),
                avatar_sha256=avatar_sha256,
                created_at=now,
                updated_at=now,
                last_verified_at=now,
            )
            self.webhook_store.put(credential)
            result = credential.safe_status()
            if existing is not None and existing.webhook_id != credential.webhook_id:
                try:
                    with httpx.Client(timeout=self.timeout_seconds) as client:
                        delete_response = client.delete(
                            f"{self.api_base_url}/webhooks/{existing.webhook_id}",
                            headers=self._headers(),
                            timeout=self.timeout_seconds,
                        )
                        if delete_response.status_code not in {204, 404}:
                            delete_response.raise_for_status()
                except httpx.HTTPError as exc:
                    result["replacement_cleanup_warning"] = type(exc).__name__
            if avatar is not None:
                cleanup_warnings = self._trim_persona_webhooks(
                    channel_id,
                    keep_key=credential.key,
                )
                if cleanup_warnings:
                    result["persona_cleanup_warnings"] = cleanup_warnings
                    _LOGGER.warning(
                        "Discord persona webhook cleanup failed for channel %s: %s",
                        channel_id,
                        ", ".join(cleanup_warnings),
                    )
            return result

    def _trim_persona_webhooks(self, channel_id: str, *, keep_key: str) -> list[str]:
        if self.webhook_store is None:
            return []
        persona_credentials = [
            credential
            for credential in self.webhook_store.list_for_channel(
                api_base_url=self.api_base_url,
                bot_token_env=self.bot_token_env,
                channel_id=channel_id,
            )
            if credential.avatar_sha256 is not None
        ]
        excess = len(persona_credentials) - self.persona_webhook_cache_limit
        if excess <= 0:
            return []
        candidates = [
            credential for credential in persona_credentials if credential.key != keep_key
        ]
        warnings: list[str] = []
        for credential in candidates[:excess]:
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.delete(
                        f"{self.api_base_url}/webhooks/{credential.webhook_id}",
                        headers=self._headers(),
                        timeout=self.timeout_seconds,
                    )
                    if response.status_code not in {204, 404}:
                        response.raise_for_status()
                self.webhook_store.delete_if_current(credential)
            except httpx.HTTPError as exc:
                warnings.append(f"{credential.webhook_id}:{type(exc).__name__}")
        return warnings

    def _webhook_credential(
        self,
        channel_id: str,
        *,
        avatar: DiscordWebhookAvatar | None = None,
    ) -> DiscordWebhookCredential:
        if self.webhook_store is None:
            raise RuntimeError("Discord webhook credential storage is not configured")
        avatar_sha256 = avatar.sha256 if avatar is not None else None
        credential = self.webhook_store.get(
            api_base_url=self.api_base_url,
            bot_token_env=self.bot_token_env,
            channel_id=channel_id,
            avatar_sha256=avatar_sha256,
        )
        if credential is None:
            self.provision_webhook(channel_id, avatar=avatar)
            credential = self.webhook_store.get(
                api_base_url=self.api_base_url,
                bot_token_env=self.bot_token_env,
                channel_id=channel_id,
                avatar_sha256=avatar_sha256,
            )
        if credential is None:
            raise RuntimeError("Discord webhook provisioning did not return a credential")
        return credential

    def _webhook_payload(
        self, message: str, identity: OutboundIdentity | None
    ) -> tuple[
        dict[str, Any],
        OutboundIdentity | None,
        str | None,
        DiscordWebhookAvatar | None,
    ]:
        payload: dict[str, Any] = {"content": message}
        if identity is None:
            return payload, None, None, None
        payload["username"] = identity.display_name
        fallback_reason = None
        applied_identity = identity
        webhook_avatar = None
        if identity.avatar is not None:
            try:
                webhook_avatar = prepare_discord_webhook_avatar(identity.avatar)
            except (RuntimeError, OSError, ValueError) as exc:
                fallback_reason = f"persona_avatar_unavailable:{type(exc).__name__}"
                applied_identity = OutboundIdentity(
                    display_name=identity.display_name,
                    source=identity.source,
                )
        return payload, applied_identity, fallback_reason, webhook_avatar

    def _execute_webhook(
        self,
        *,
        credential: DiscordWebhookCredential,
        payload: dict[str, Any],
        files: Any = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_base_url}/webhooks/{credential.webhook_id}/{credential.token}?wait=true"
        if thread_id is not None:
            url = f"{url}&thread_id={quote(str(thread_id), safe='')}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                if files is None:
                    response = self._request_with_retries(
                        client,
                        "POST",
                        url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                else:
                    response = self._request_with_retries(
                        client,
                        "POST",
                        url,
                        data={"payload_json": json.dumps(payload)},
                        files=files,
                        headers={},
                    )
        except httpx.HTTPStatusError as exc:
            self._record_webhook_error(
                credential,
                f"http_{exc.response.status_code}",
            )
            safe_request = self._safe_webhook_request(credential)
            safe_response = httpx.Response(exc.response.status_code, request=safe_request)
            raise httpx.HTTPStatusError(
                f"Discord webhook request failed with status {exc.response.status_code}",
                request=safe_request,
                response=safe_response,
            ) from None
        except httpx.ConnectError:
            self._record_webhook_error(credential, "connect_error")
            safe_request = self._safe_webhook_request(credential)
            raise httpx.ConnectError(
                "Discord webhook connection failed",
                request=safe_request,
            ) from None
        except httpx.TimeoutException:
            self._record_webhook_error(credential, "timeout")
            safe_request = self._safe_webhook_request(credential)
            raise httpx.TimeoutException(
                "Discord webhook request timed out",
                request=safe_request,
            ) from None
        except httpx.RequestError:
            self._record_webhook_error(credential, "request_error")
            safe_request = self._safe_webhook_request(credential)
            raise httpx.RequestError(
                "Discord webhook request failed",
                request=safe_request,
            ) from None
        if self.webhook_store is not None:
            now = datetime.now(UTC)
            self.webhook_store.put_if_current(
                credential.model_copy(
                    update={
                        "updated_at": now,
                        "last_verified_at": now,
                        "last_error": None,
                    }
                )
            )
        return response.json()

    def _safe_webhook_request(self, credential: DiscordWebhookCredential) -> httpx.Request:
        return httpx.Request(
            "POST",
            f"{self.api_base_url}/webhooks/{credential.webhook_id}/<redacted>",
        )

    def _record_webhook_error(
        self,
        credential: DiscordWebhookCredential,
        error: str,
    ) -> None:
        if self.webhook_store is None:
            return
        self.webhook_store.put_if_current(
            credential.model_copy(
                update={
                    "updated_at": datetime.now(UTC),
                    "last_error": error,
                }
            )
        )

    def _execute_identity_webhook(
        self,
        *,
        channel_id: str,
        payload: dict[str, Any],
        avatar: DiscordWebhookAvatar | None = None,
        files: Any = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        credential = self._webhook_credential(channel_id, avatar=avatar)
        try:
            return self._execute_webhook(
                credential=credential,
                payload=payload,
                files=files,
                thread_id=thread_id,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or self.webhook_store is None:
                raise
            self.webhook_store.delete_if_current(credential)
            return self._execute_webhook(
                credential=self._webhook_credential(channel_id, avatar=avatar),
                payload=payload,
                files=files,
                thread_id=thread_id,
            )

    def test_webhook(
        self,
        channel_id: str,
        *,
        message: str = "Evander Discord webhook test",
    ) -> dict[str, Any]:
        self._require_allowed_channel_id(channel_id)
        payload = self._execute_identity_webhook(
            channel_id=channel_id,
            payload={"content": message},
        )
        return self._persist_posted_message(
            payload,
            channel_id=channel_id,
            session_id="admin-webhook-test",
            source="discord_webhook_status",
            transport="webhook",
            identity=None,
            identity_status="test",
        )

    @staticmethod
    def _definite_webhook_failure(exc: httpx.HTTPStatusError) -> bool:
        return exc.response.status_code in {400, 401, 403, 404, 429}

    def _post_file(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
        *,
        identity: OutboundIdentity | None = None,
        use_resolved_identity: bool = False,
        identity_status: str = "not_requested",
        webhook_channel_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_allowed_channel_id(channel_id)
        resolved_webhook_channel_id = webhook_channel_id or channel_id
        if resolved_webhook_channel_id != channel_id:
            self._require_allowed_webhook_channel_id(resolved_webhook_channel_id)
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, artifact in enumerate(artifacts):
            filename = _artifact_filename(artifact, index=index)
            files.append(
                (
                    f"files[{index}]",
                    (
                        filename,
                        _artifact_bytes(artifact),
                        _artifact_content_type(artifact, filename),
                    ),
                )
            )
        if use_resolved_identity and identity is not None:
            payload, applied_identity, avatar_fallback, webhook_avatar = self._webhook_payload(
                message, identity
            )
            try:
                response_payload = self._execute_identity_webhook(
                    channel_id=resolved_webhook_channel_id,
                    payload=payload,
                    avatar=webhook_avatar,
                    files=files,
                    thread_id=thread_id,
                )
                result = self._persist_posted_message(
                    response_payload,
                    channel_id=channel_id,
                    session_id=session_id,
                    source="discord_webhook_status",
                    transport="webhook",
                    identity=applied_identity,
                    identity_status=identity_status,
                    fallback_reason=avatar_fallback,
                )
                result["artifact_count"] = len(artifacts)
                return result
            except httpx.HTTPStatusError as exc:
                if not self._definite_webhook_failure(exc):
                    raise
                fallback_reason = f"webhook_http_{exc.response.status_code}"
            except (RuntimeError, httpx.ConnectError) as exc:
                fallback_reason = f"webhook_unavailable:{type(exc).__name__}"
        elif use_resolved_identity:
            fallback_reason = f"identity_{identity_status}"
        else:
            fallback_reason = None
        payload = {"content": message}
        headers = {"Authorization": f"Bot {self.token}"}
        with httpx.Client(timeout=None) as client:
            response = self._request_with_retries(
                client,
                "POST",
                f"{self.api_base_url}/channels/{channel_id}/messages",
                data={"payload_json": json.dumps(payload)},
                files=files,
                headers=headers,
            )
            response_payload = response.json()
        result = self._persist_posted_message(
            response_payload,
            channel_id=channel_id,
            session_id=session_id,
            source="discord_bot_api_status",
            transport="bot_api",
            identity=None,
            identity_status=identity_status,
            fallback_reason=fallback_reason,
        )
        result["artifact_count"] = len(artifacts)
        return result

    def post_file(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._post_file(message, channel_id, session_id, artifacts)

    def post_file_with_identity(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
        *,
        identity: OutboundIdentity | None,
        identity_status: str,
        webhook_channel_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        return self._post_file(
            message,
            channel_id,
            session_id,
            artifacts,
            identity=identity,
            use_resolved_identity=True,
            identity_status=identity_status,
            webhook_channel_id=webhook_channel_id,
            thread_id=thread_id,
        )

    def _post_status(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        *,
        identity: OutboundIdentity | None = None,
        use_resolved_identity: bool = False,
        identity_status: str = "not_requested",
        webhook_channel_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_allowed_channel_id(channel_id)
        resolved_webhook_channel_id = webhook_channel_id or channel_id
        if resolved_webhook_channel_id != channel_id:
            self._require_allowed_webhook_channel_id(resolved_webhook_channel_id)
        if use_resolved_identity and identity is not None:
            (
                webhook_payload,
                applied_identity,
                avatar_fallback,
                webhook_avatar,
            ) = self._webhook_payload(message, identity)
            try:
                payload = self._execute_identity_webhook(
                    channel_id=resolved_webhook_channel_id,
                    payload=webhook_payload,
                    avatar=webhook_avatar,
                    thread_id=thread_id,
                )
                return self._persist_posted_message(
                    payload,
                    channel_id=channel_id,
                    session_id=session_id,
                    source="discord_webhook_status",
                    transport="webhook",
                    identity=applied_identity,
                    identity_status=identity_status,
                    fallback_reason=avatar_fallback,
                )
            except httpx.HTTPStatusError as exc:
                if not self._definite_webhook_failure(exc):
                    raise
                fallback_reason = f"webhook_http_{exc.response.status_code}"
            except (RuntimeError, httpx.ConnectError) as exc:
                fallback_reason = f"webhook_unavailable:{type(exc).__name__}"
        elif use_resolved_identity:
            fallback_reason = f"identity_{identity_status}"
        else:
            fallback_reason = None
        with httpx.Client(timeout=None) as client:
            response = self._request_with_retries(
                client,
                "POST",
                f"{self.api_base_url}/channels/{channel_id}/messages",
                json={"content": message},
            )
            payload = response.json()
        return self._persist_posted_message(
            payload,
            channel_id=channel_id,
            session_id=session_id,
            source="discord_bot_api_status",
            transport="bot_api",
            identity=None,
            identity_status=identity_status,
            fallback_reason=fallback_reason,
        )

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]:
        return self._post_status(message, channel_id, session_id)

    def post_status_with_identity(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        *,
        identity: OutboundIdentity | None,
        identity_status: str,
        webhook_channel_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        return self._post_status(
            message,
            channel_id,
            session_id,
            identity=identity,
            use_resolved_identity=True,
            identity_status=identity_status,
            webhook_channel_id=webhook_channel_id,
            thread_id=thread_id,
        )

    def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
        session_id: str,
    ) -> dict[str, Any]:
        normalized_emoji = str(emoji).strip()
        if not normalized_emoji:
            raise ValueError("Discord emoji must be a non-empty string.")
        encoded_emoji = quote(normalized_emoji, safe="")
        with httpx.Client(timeout=None) as client:
            self._request_with_retries(
                client,
                "PUT",
                f"{self.api_base_url}/channels/{channel_id}/messages/{message_id}/reactions/"
                f"{encoded_emoji}/@me",
            )
        return {
            "status": "reacted",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_id": message_id,
            "emoji": normalized_emoji,
            "reaction": normalized_emoji,
        }


def _put_discord_commands(
    command_store: DiscordRecordsRepository,
    commands: Mapping[str, DiscordCommandEnvelope],
) -> None:
    if not commands:
        return
    put_many = getattr(command_store, "put_many", None)
    if callable(put_many):
        put_many(commands)
        return
    for command_id, envelope in commands.items():
        command_store.put(command_id, envelope)


class DiscordInboxService:
    def __init__(
        self,
        store_path: Path,
        *,
        repository: DiscordInboxCursorRepository | None = None,
    ) -> None:
        self.store_path = store_path
        self.store: DiscordInboxCursorRepository = repository or DiscordInboxStore(store_path)

    def has_pending_discord_messages(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
    ) -> HasPendingDiscordMessagesResponse:
        messages = self._list_messages(
            streams=streams,
            session_id=session_id,
            cursor=cursor,
            limit=None,
            persist_session_cursor=False,
        )
        return HasPendingDiscordMessagesResponse(
            pending=bool(messages.messages),
            next_cursor=messages.next_cursor,
        )

    def list_unread_discord_messages(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ListUnreadDiscordMessagesResponse:
        return self._list_messages(
            streams=streams,
            session_id=session_id,
            cursor=cursor,
            limit=limit,
            persist_session_cursor=True,
        )

    def list_all_discord_messages(
        self,
        *,
        streams: list[dict[str, str]],
        limit: int | None = None,
    ) -> ListUnreadDiscordMessagesResponse:
        return self._list_messages(
            streams=streams,
            session_id=None,
            cursor=None,
            limit=limit,
            persist_session_cursor=False,
        )

    def fast_forward_session_cursors_to_latest(
        self,
        *,
        session_id: str,
        channel_ids: list[str],
    ) -> dict[str, str]:
        return self.store.fast_forward_session_cursors_to_latest(session_id, channel_ids)

    def _list_messages(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None,
        cursor: str | None,
        limit: int | None,
        persist_session_cursor: bool,
    ) -> ListUnreadDiscordMessagesResponse:
        allowed_channels = {stream["channel_id"] for stream in streams}
        channel_cursors = {
            stream["channel_id"]: self._effective_cursor(
                session_id=session_id,
                channel_id=stream["channel_id"],
                explicit_cursor=cursor,
            )
            for stream in streams
        }
        items: list[DiscordMessageEnvelope] = []
        if allowed_channels:
            candidates = self.store.list_all_messages(
                channel_ids=sorted(allowed_channels),
                limit=None,
            ).messages
            for candidate in candidates:
                if candidate.channel_id not in allowed_channels:
                    continue
                if session_id is not None and self._is_self_authored_message(
                    candidate, session_id=session_id
                ):
                    continue
                effective_cursor = channel_cursors.get(candidate.channel_id)
                if effective_cursor and candidate.created_at <= self._parse_cursor(
                    effective_cursor
                ):
                    continue
                items.append(candidate)

        items.sort(
            key=lambda item: (
                item.created_at,
                _message_sort_value(item.message_id),
            )
        )
        if limit is not None:
            items = items[:limit]
        next_cursor = (
            items[-1].created_at.isoformat().replace("+00:00", "Z")
            if items
            else self._max_cursors(channel_cursors.values())
        )
        if persist_session_cursor and session_id is not None:
            for stream in streams:
                self.store.advance_session_cursor(
                    session_id,
                    stream["channel_id"],
                    next_cursor if next_cursor is not None else cursor,
                )
        return ListUnreadDiscordMessagesResponse(messages=items, next_cursor=next_cursor)

    def _read_store(self) -> dict[str, Any]:
        return self.store.read_raw()

    @staticmethod
    def _is_self_authored_message(message: DiscordMessageEnvelope, *, session_id: str) -> bool:
        raw_source = str(message.raw.get("source", "")).strip()
        raw_session_id = str(message.raw.get("session_id", "")).strip()
        return (
            raw_source
            in {
                "discord_bot_api_status",
                "discord_webhook_status",
            }
            and raw_session_id == session_id
        )

    def _effective_cursor(
        self,
        *,
        session_id: str | None,
        channel_id: str,
        explicit_cursor: str | None,
    ) -> str | None:
        if session_id is None:
            return explicit_cursor
        return self._max_cursor(
            self.store.get_session_cursor(session_id, channel_id),
            explicit_cursor,
        )

    def _max_cursor(self, first: str | None, second: str | None) -> str | None:
        if first is None:
            return second
        if second is None:
            return first
        if self._parse_cursor(first) >= self._parse_cursor(second):
            return first
        return second

    def _max_cursors(self, cursors: Any) -> str | None:
        maximum: str | None = None
        for cursor in cursors:
            maximum = self._max_cursor(maximum, cursor)
        return maximum

    @staticmethod
    def _parse_cursor(cursor: str) -> datetime:
        normalized = cursor.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
