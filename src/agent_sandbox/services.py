from __future__ import annotations

import random
import time
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

import httpx
from .models import DiscordCommandEnvelope, DraftMessage, EmailMessage
from .email.providers import EmailDraft, EmailMessageDetail, EmailMessageSummary, EmailProvider
from .storage import JsonStore


def _parse_snowflake(value: str, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid {field_name} {value!r}: expected a numeric Discord snowflake string."
        ) from exc


class EmailService:
    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        return self.provider.list_unread(mailbox_id=mailbox_id, folder=folder, limit=limit)

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        return self.provider.get_message(mailbox_id=mailbox_id, message_id=message_id)

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
    ) -> EmailDraft:
        return self.provider.create_draft(
            mailbox_id=mailbox_id,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
        )

    def send_from_draft(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "stubbed",
            "request_id": request_id,
            "sent_payload": payload,
        }


class DiscordService(Protocol):
    def get_commands(self, since_cursor: str | None = None) -> list["DiscordCommandEnvelope"]: ...

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]: ...


class StubDiscordService:
    def __init__(self, command_store: JsonStore[DiscordCommandEnvelope]) -> None:
        self.command_store = command_store

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


class DiscordBotApiService:
    def __init__(
        self,
        command_store: JsonStore[DiscordCommandEnvelope],
        *,
        token: str,
        api_base_url: str,
        poll_limit: int,
        timeout_seconds: float,
        retry_count: int = 3,
        retry_base_backoff_seconds: float = 0.5,
        retry_max_backoff_seconds: float = 5.0,
        allowed_channel_ids_provider: Callable[[], list[str]] = lambda: [],
        operator_user_ids_provider: Callable[[], list[str]] | None = None,
        include_operator_dms: bool = False,
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
        self.operator_user_ids_provider = operator_user_ids_provider or (lambda: [])
        self.include_operator_dms = include_operator_dms

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

    def _operator_user_ids(self) -> list[str]:
        return sorted(
            {user_id.strip() for user_id in self.operator_user_ids_provider() if user_id.strip()}
        )

    def _operator_dm_channel_ids(self, client: httpx.Client) -> list[str]:
        if not self.include_operator_dms:
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
    ) -> httpx.Response:
        start_time = time.monotonic()
        attempt = 0
        last_error: Exception | None = None
        while True:
            try:
                response = client.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
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
        with httpx.Client(timeout=self.timeout_seconds) as client:
            channel_ids = sorted(
                set(self._allowed_channel_ids()) | set(self._operator_dm_channel_ids(client))
            )
            for channel_id in channel_ids:
                response = self._request_with_retries(
                    client,
                    "GET",
                    f"{self.api_base_url}/channels/{channel_id}/messages",
                    params={"limit": self.poll_limit},
                )
                for payload in response.json():
                    envelope = DiscordCommandEnvelope(
                        command_id=payload["id"],
                        author_id=payload["author"]["id"],
                        channel_id=channel_id,
                        content=payload.get("content", ""),
                        metadata={
                            "source": "discord_bot_api",
                            "guild_id": payload.get("guild_id"),
                            "channel_id": channel_id,
                            "author_id": payload["author"]["id"],
                            "message": payload,
                        },
                    )
                    envelopes[envelope.command_id] = envelope
                    self.command_store.put(envelope.command_id, envelope)

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

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=None) as client:
            response = self._request_with_retries(
                client,
                "POST",
                f"{self.api_base_url}/channels/{channel_id}/messages",
                json={"content": message},
            )
            payload = response.json()

        envelope = DiscordCommandEnvelope(
            command_id=payload["id"],
            author_id=payload["author"]["id"],
            channel_id=channel_id,
            content=payload.get("content", ""),
            metadata={
                "source": "discord_bot_api_status",
                "guild_id": payload.get("guild_id"),
                "channel_id": channel_id,
                "author_id": payload["author"]["id"],
                "session_id": session_id,
                "message": payload,
            },
        )
        self.command_store.put(envelope.command_id, envelope)
        return {
            "status": "posted",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_id": payload["id"],
        }
