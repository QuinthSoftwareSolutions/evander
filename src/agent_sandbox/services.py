from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator, Protocol
from urllib.parse import quote, unquote

import httpx
from .models import (
    DiscordCommandEnvelope,
    DiscordMessageEnvelope,
    DraftMessage,
    EmailMessage,
    HasPendingDiscordMessagesResponse,
    HasPendingFeedbackResponse,
    ListUnreadDiscordMessagesResponse,
    ListUnreadFeedbackResponse,
    PostPrFeedbackReplyResponse,
    PrFeedbackItem,
    TeamsMessageEnvelope,
)
from .discord_inbox_store import DiscordInboxStore
from .pr_feedback_worker import PrFeedbackStore, _stream_key
from .email.providers import EmailDraft, EmailMessageDetail, EmailMessageSummary, EmailProvider
from .storage import JsonStore

_IGNORED_FEEDBACK_RETENTION = timedelta(days=90)


def _parse_snowflake(value: str, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid {field_name} {value!r}: expected a numeric Discord snowflake string."
        ) from exc


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Invalid timestamp {value!r}: expected an ISO 8601 string.")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _message_sort_value(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, value)


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


class TeamsService(Protocol):
    def get_messages(self, since_cursor: str | None = None) -> list["TeamsMessageEnvelope"]: ...


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
        include_operator_dms_provider: Callable[[], bool] | None = None,
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
        self.include_operator_dms_provider = include_operator_dms_provider or (lambda: include_operator_dms)

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

def _sorted_teams_messages(messages: dict[str, TeamsMessageEnvelope]) -> list[TeamsMessageEnvelope]:
    return sorted(messages.values(), key=lambda item: (item.created_at, item.cursor))


def _filter_teams_messages_since_cursor(
    messages: list[TeamsMessageEnvelope], since_cursor: str | None
) -> list[TeamsMessageEnvelope]:
    if since_cursor is None:
        return messages
    if any(message.cursor == since_cursor for message in messages):
        seen_cursor = False
        filtered: list[TeamsMessageEnvelope] = []
        for message in messages:
            if seen_cursor:
                filtered.append(message)
                continue
            if message.cursor == since_cursor:
                seen_cursor = True
        return filtered
    return [message for message in messages if message.cursor > since_cursor]


def _parse_graph_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_teams_channel_target(target: str) -> tuple[str, str]:
    normalized = target.strip().strip("/")
    team_id, separator, channel_id = normalized.partition("/")
    if not separator or not team_id or not channel_id:
        raise ValueError(
            f"Invalid Teams channel target {target!r}: expected 'team-id/channel-id'."
        )
    return team_id, channel_id


class StubTeamsService:
    def __init__(self, message_store: JsonStore[TeamsMessageEnvelope]) -> None:
        self.message_store = message_store

    def get_messages(self, since_cursor: str | None = None) -> list[TeamsMessageEnvelope]:
        messages = list(self.message_store.read_all().values())
        for message in messages:
            message.metadata = {
                "source": message.metadata.get("source", "stub"),
                "team_id": message.metadata.get("team_id", message.team_id),
                "channel_id": message.metadata.get("channel_id", message.channel_id),
                "thread_id": message.metadata.get("thread_id", message.thread_id),
                "author_id": message.metadata.get("author_id", message.author_id),
                **{
                    key: value
                    for key, value in message.metadata.items()
                    if key not in {"source", "team_id", "channel_id", "thread_id", "author_id"}
                },
            }
        ordered = _sorted_teams_messages({message.cursor: message for message in messages})
        return _filter_teams_messages_since_cursor(ordered, since_cursor)


class GraphTeamsService:
    def __init__(
        self,
        message_store: JsonStore[TeamsMessageEnvelope],
        *,
        token: str,
        api_base_url: str,
        poll_limit: int,
        timeout_seconds: float,
        retry_count: int = 3,
        retry_base_backoff_seconds: float = 0.5,
        retry_max_backoff_seconds: float = 5.0,
        allowed_channel_targets_provider: Callable[[], list[str]] = lambda: [],
    ) -> None:
        self.message_store = message_store
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
        self.allowed_channel_targets_provider = allowed_channel_targets_provider

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _allowed_channel_targets(self) -> list[str]:
        return sorted(
            {
                target.strip().strip("/")
                for target in self.allowed_channel_targets_provider()
                if target.strip()
            }
        )

    def _request_with_retries(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
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
                if status_code not in {429} and not (500 <= status_code <= 599):
                    raise
                if attempt >= self.retry_count:
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
                raise TimeoutError("Retry budget exhausted before completing Teams API request")
            sleep_for = min(jittered_backoff, remaining_budget)
            time.sleep(sleep_for)
            attempt += 1

    @staticmethod
    def _author_id(payload: dict[str, Any]) -> str:
        sender = payload.get("from") or {}
        for source_key in ("user", "application", "device"):
            source = sender.get(source_key) or {}
            candidate = str(source.get("id") or "").strip()
            if candidate:
                return candidate
        return "unknown"

    def _to_envelope(self, payload: dict[str, Any], *, team_id: str, channel_id: str) -> TeamsMessageEnvelope:
        message_id = str(payload.get("id") or "").strip()
        thread_id = str(payload.get("replyToId") or message_id).strip() or None
        cursor = f"{team_id}/{channel_id}/{message_id}"
        body = payload.get("body") or {}
        author_id = self._author_id(payload)
        return TeamsMessageEnvelope(
            cursor=cursor,
            message_id=message_id,
            author_id=author_id,
            team_id=team_id,
            channel_id=channel_id,
            thread_id=thread_id,
            content=str(body.get("content") or ""),
            created_at=_parse_graph_datetime(str(payload.get("createdDateTime") or "")),
            metadata={
                "source": "teams_graph_api",
                "team_id": team_id,
                "channel_id": channel_id,
                "thread_id": thread_id,
                "author_id": author_id,
                "reply_to_id": str(payload.get("replyToId") or "").strip() or None,
                "web_url": str(payload.get("webUrl") or "").strip() or None,
                "content_type": str(body.get("contentType") or "").strip() or None,
                "message": payload,
            },
        )

    def _iter_channel_messages(
        self,
        client: httpx.Client,
        *,
        team_id: str,
        channel_id: str,
    ) -> Iterator[dict[str, Any]]:
        next_url = (
            f"{self.api_base_url}/teams/{quote(team_id, safe='')}/channels/"
            f"{quote(channel_id, safe='')}/messages"
        )
        params: dict[str, Any] | None = {"$top": self.poll_limit, "$expand": "replies"}
        seen_urls: set[str] = set()
        while next_url:
            if next_url in seen_urls:
                raise RuntimeError(f"Teams Graph pagination loop detected for channel {team_id}/{channel_id}")
            seen_urls.add(next_url)
            response = self._request_with_retries(client, "GET", next_url, params=params)
            payload = response.json()
            for message_payload in payload.get("value", []):
                yield message_payload
            raw_next_url = payload.get("@odata.nextLink")
            next_url = str(raw_next_url).strip() if raw_next_url else ""
            params = None

    def get_messages(self, since_cursor: str | None = None) -> list[TeamsMessageEnvelope]:
        messages: dict[str, TeamsMessageEnvelope] = dict(self.message_store.read_all())
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for target in self._allowed_channel_targets():
                team_id, channel_id = _parse_teams_channel_target(target)
                for message_payload in self._iter_channel_messages(
                    client,
                    team_id=team_id,
                    channel_id=channel_id,
                ):
                    envelope = self._to_envelope(message_payload, team_id=team_id, channel_id=channel_id)
                    messages[envelope.cursor] = envelope
                    self.message_store.put(envelope.cursor, envelope)
                    for reply_payload in message_payload.get("replies") or []:
                        reply_envelope = self._to_envelope(reply_payload, team_id=team_id, channel_id=channel_id)
                        messages[reply_envelope.cursor] = reply_envelope
                        self.message_store.put(reply_envelope.cursor, reply_envelope)

        ordered = _sorted_teams_messages(messages)
        return _filter_teams_messages_since_cursor(ordered, since_cursor)


class DiscordInboxService:
    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path
        self.store = DiscordInboxStore(store_path)

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

    def _list_messages(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None,
        cursor: str | None,
        limit: int | None,
        persist_session_cursor: bool,
    ) -> ListUnreadDiscordMessagesResponse:
        payload = self._read_store()
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
        for raw_value in payload.get("messages", {}).values():
            if not isinstance(raw_value, dict):
                continue
            candidate = DiscordMessageEnvelope.model_validate(raw_value)
            if candidate.channel_id not in allowed_channels:
                continue
            if session_id is not None and self._is_self_authored_message(candidate, session_id=session_id):
                continue
            effective_cursor = channel_cursors.get(candidate.channel_id)
            if effective_cursor and candidate.created_at <= self._parse_cursor(effective_cursor):
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
        return raw_source == "discord_bot_api_status" and raw_session_id == session_id

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


class PrFeedbackService:
    def __init__(self, store_path: Path, *, ignored_feedback_store_path: Path | None = None) -> None:
        self.store_path = store_path
        self.store = PrFeedbackStore(store_path)
        self.ignored_feedback_store_path = ignored_feedback_store_path

    def has_pending_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
    ) -> HasPendingFeedbackResponse:
        feedback = self._list_feedback(
            streams=streams,
            session_id=session_id,
            cursor=cursor,
            limit=None,
            persist_session_cursor=False,
        )
        return HasPendingFeedbackResponse(
            pending=bool(feedback.feedback),
            next_cursor=feedback.next_cursor,
        )

    def list_unread_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ListUnreadFeedbackResponse:
        return self._list_feedback(
            streams=streams,
            session_id=session_id,
            cursor=cursor,
            limit=limit,
            persist_session_cursor=True,
        )

    def list_all_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        limit: int | None = None,
    ) -> ListUnreadFeedbackResponse:
        return self._list_feedback(
            streams=streams,
            session_id=None,
            cursor=None,
            limit=limit,
            persist_session_cursor=False,
        )

    def _list_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None,
        cursor: str | None,
        limit: int | None,
        persist_session_cursor: bool,
    ) -> ListUnreadFeedbackResponse:
        payload = self._read_store()
        allowed_streams = {
            (stream["provider"], stream["repo"], stream["pr_id"])
            for stream in streams
        }
        stream_cursors = {
            (stream["provider"], stream["repo"], stream["pr_id"]): self._effective_cursor(
                session_id=session_id,
                stream=stream,
                explicit_cursor=cursor,
            )
            for stream in streams
        }
        items: list[PrFeedbackItem] = []
        ignored_feedback_ids = self._read_ignored_feedback_ids()
        for raw_value in payload.get("feedback", {}).values():
            if not isinstance(raw_value, dict):
                continue
            candidate = PrFeedbackItem.model_validate(raw_value)
            if candidate.feedback_id in ignored_feedback_ids:
                continue
            key = (candidate.provider, candidate.repo, candidate.pr_id)
            if key not in allowed_streams:
                continue
            effective_cursor = stream_cursors.get(key)
            if effective_cursor and candidate.created_at <= self._parse_cursor(effective_cursor):
                continue
            items.append(candidate)

        items.sort(key=lambda item: item.created_at)
        if limit is not None:
            items = items[:limit]
        next_cursor = (
            items[-1].created_at.isoformat().replace("+00:00", "Z")
            if items
            else self._max_cursors(stream_cursors.values())
        )
        if persist_session_cursor and session_id is not None:
            for stream in streams:
                self.store.advance_session_cursor(
                    session_id,
                    _stream_key(stream["provider"], stream["repo"], stream["pr_id"]),
                    next_cursor if next_cursor is not None else cursor,
                )
        return ListUnreadFeedbackResponse(feedback=items, next_cursor=next_cursor)

    def _read_store(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {"feedback": {}}
        return json.loads(self.store_path.read_text(encoding="utf-8"))

    def _read_ignored_feedback_ids(self) -> set[str]:
        path = self.ignored_feedback_store_path
        if path is None or not path.exists():
            return set()
        payload = json.loads(path.read_text(encoding="utf-8"))
        pruned_payload, feedback_ids, changed = _normalize_ignored_feedback_payload(payload)
        if changed:
            _write_json_atomic(path, pruned_payload)
        return feedback_ids

    def _effective_cursor(
        self,
        *,
        session_id: str | None,
        stream: dict[str, str],
        explicit_cursor: str | None,
    ) -> str | None:
        if session_id is None:
            return explicit_cursor
        return self._max_cursor(
            self.store.get_session_cursor(
                session_id,
                _stream_key(stream["provider"], stream["repo"], stream["pr_id"]),
            ),
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


class PrReplyProvider(Protocol):
    def post_reply(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
    ) -> PostPrFeedbackReplyResponse: ...


class GitHubPrReplyProvider:
    def __init__(
        self,
        *,
        token: str,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def post_reply(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
    ) -> PostPrFeedbackReplyResponse:
        if not comment_id:
            raise ValueError("GitHub reply posting requires comment_id")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/comments/{comment_id}/replies",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"body": body_text},
            )
            response.raise_for_status()
            payload = response.json()
        return PostPrFeedbackReplyResponse(
            status="posted",
            provider="github",
            repo=repo,
            pr_id=pr_id,
            thread_id=thread_id,
            comment_id=comment_id,
            reply_id=str(payload.get("id", "")) or None,
            reply_url=payload.get("html_url") or payload.get("url"),
        )


class AzureDevOpsPrReplyProvider:
    def __init__(
        self,
        *,
        organization_url: str,
        project: str,
        pat: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat
        self.timeout_seconds = timeout_seconds

    def post_reply(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
    ) -> PostPrFeedbackReplyResponse:
        if not thread_id:
            raise ValueError("Azure DevOps reply posting requires thread_id")
        if not comment_id:
            raise ValueError("Azure DevOps reply posting requires comment_id")
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                    f"pullRequests/{pr_id}/threads/{thread_id}/comments"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
                json={"content": body_text, "parentCommentId": int(comment_id)},
            )
            response.raise_for_status()
            payload = response.json()
        return PostPrFeedbackReplyResponse(
            status="posted",
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            thread_id=thread_id,
            comment_id=comment_id,
            reply_id=str(payload.get("id", "")) or None,
            reply_url=payload.get("_links", {}).get("web", {}).get("href"),
        )


def _normalize_azure_devops_repo_identifier(repo: str) -> str:
    normalized = unquote(repo.strip())
    if not normalized:
        return normalized
    if "/_git/" in normalized:
        return normalized.split("/_git/", 1)[1].split("/", 1)[0]
    if "/_apis/git/repositories/" in normalized:
        return normalized.split("/_apis/git/repositories/", 1)[1].split("/", 1)[0]
    if "ssh.dev.azure.com:v3/" in normalized:
        return normalized.split("ssh.dev.azure.com:v3/", 1)[1].rsplit("/", 1)[-1]
    if normalized.count("/") >= 2:
        return normalized.rsplit("/", 1)[-1]
    return normalized


def normalize_pr_feedback_provider(provider: str) -> str:
    normalized = provider.strip()
    if normalized in {"azure_devops", "azure-devops"}:
        return "azure_devops"
    return normalized


class PrReplyService:
    def __init__(
        self,
        *,
        providers: dict[str, PrReplyProvider],
        idempotency_store_path: Path,
        ignored_feedback_store_path: Path | None = None,
    ) -> None:
        self.providers = providers
        self.idempotency_store_path = idempotency_store_path
        self.ignored_feedback_store_path = ignored_feedback_store_path
        self._lock = Lock()
        self.idempotency_store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.idempotency_store_path.exists():
            self._write_raw({})

    def post_reply(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        idempotency_key: str | None = None,
    ) -> PostPrFeedbackReplyResponse:
        provider_name = normalize_pr_feedback_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR feedback provider {provider_name!r} is not configured for this gateway instance"
            )
        normalized_key = idempotency_key.strip() if idempotency_key else None
        cache_key = (
            f"{provider_name}:{repo}:{pr_id}:{normalized_key}" if normalized_key else None
        )
        if cache_key:
            with self._lock:
                existing = self._read_raw().get(cache_key)
            if isinstance(existing, dict):
                cached = PostPrFeedbackReplyResponse.model_validate(existing)
                return cached.model_copy(update={"status": "deduplicated"})

        response = self.providers[provider_name].post_reply(
            repo=repo,
            pr_id=pr_id,
            thread_id=thread_id,
            comment_id=comment_id,
            body_text=body_text.strip(),
        )
        response.idempotency_key = normalized_key
        self._record_ignored_feedback_id(response)
        if cache_key:
            with self._lock:
                payload = self._read_raw()
                payload[cache_key] = response.model_dump(mode="json")
                self._write_raw(payload)
        return response

    def _record_ignored_feedback_id(self, response: PostPrFeedbackReplyResponse) -> None:
        feedback_id = _feedback_id_for_reply_response(response)
        if feedback_id is None or self.ignored_feedback_store_path is None:
            return

        path = self.ignored_feedback_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                payload = {}
            normalized_payload, feedback_ids, _ = _normalize_ignored_feedback_payload(payload)
            if feedback_id in feedback_ids:
                return
            entries = normalized_payload.get("feedback_ids", [])
            entries.append(
                {
                    "feedback_id": feedback_id,
                    "ignored_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
            normalized_payload["feedback_ids"] = entries
            final_payload, _, _ = _normalize_ignored_feedback_payload(normalized_payload)
            _write_json_atomic(path, final_payload)

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.idempotency_store_path.read_text(encoding="utf-8"))

    def _write_raw(self, payload: dict[str, Any]) -> None:
        temp_path = self.idempotency_store_path.with_suffix(
            f"{self.idempotency_store_path.suffix}.tmp"
        )
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.idempotency_store_path)


def _feedback_id_for_reply_response(response: PostPrFeedbackReplyResponse) -> str | None:
    if not response.reply_id:
        return None
    if response.provider == "github":
        return f"github:review_comment:{response.reply_id}"
    if response.provider == "azure_devops" and response.thread_id:
        return f"azure_devops:thread_comment:{response.thread_id}:{response.reply_id}"
    return None
def _normalize_ignored_feedback_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], set[str], bool]:
    now = datetime.now(UTC)
    cutoff = now - _IGNORED_FEEDBACK_RETENTION
    raw_entries = payload.get("feedback_ids", [])
    normalized_entries: list[dict[str, str]] = []
    feedback_ids: set[str] = set()

    if isinstance(raw_entries, list):
        for raw_entry in raw_entries:
            if isinstance(raw_entry, str):
                feedback_id = raw_entry.strip()
                if not feedback_id:
                    continue
                normalized_entries.append({"feedback_id": feedback_id, "ignored_at": now.isoformat().replace("+00:00", "Z")})
                feedback_ids.add(feedback_id)
                continue
            if not isinstance(raw_entry, dict):
                continue
            feedback_id = str(raw_entry.get("feedback_id", "")).strip()
            if not feedback_id:
                continue
            ignored_at = str(raw_entry.get("ignored_at", "")).strip()
            if ignored_at:
                try:
                    ignored_at_dt = PrFeedbackService._parse_cursor(ignored_at)
                except ValueError:
                    ignored_at_dt = now
            else:
                ignored_at_dt = now
            if ignored_at_dt < cutoff:
                continue
            if feedback_id in feedback_ids:
                continue
            normalized_entries.append(
                {
                    "feedback_id": feedback_id,
                    "ignored_at": ignored_at_dt.isoformat().replace("+00:00", "Z"),
                }
            )
            feedback_ids.add(feedback_id)

    normalized_payload = {"feedback_ids": normalized_entries}
    changed = normalized_payload != payload
    return normalized_payload, feedback_ids, changed


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)
