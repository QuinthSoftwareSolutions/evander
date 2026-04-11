from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator, Literal, Protocol
from urllib.parse import quote, unquote

import httpx
from .models import (
    DiscordCommandEnvelope,
    DiscordMessageEnvelope,
    DraftMessage,
    EmailMessage,
    HasPendingDiscordMessagesResponse,
    HasPendingFeedbackResponse,
    GetPullRequestResponse,
    ListPullRequestsResponse,
    ListUnreadDiscordMessagesResponse,
    ListUnreadFeedbackResponse,
    PostPrFeedbackReplyResponse,
    PullRequestCommit,
    PullRequestItem,
    PullRequestReviewer,
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

    def post_status(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]: ...


class TeamsAuthenticationError(httpx.HTTPError):
    """Raised when Teams delegated or static credentials cannot be resolved."""


@dataclass(frozen=True)
class TeamsTarget:
    kind: Literal["channel", "chat"]
    normalized: str
    team_id: str | None = None
    channel_id: str | None = None
    chat_id: str | None = None


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


def _augment_http_status_error(exc: httpx.HTTPStatusError) -> httpx.HTTPStatusError:
    details = _graph_error_details(exc.response)
    if not details:
        return exc
    return httpx.HTTPStatusError(
        f"{exc}. Graph error: {details}",
        request=exc.request,
        response=exc.response,
    )


def _graph_error_details(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:500] if text else ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""

    parts: list[str] = []
    code = str(error.get("code") or "").strip()
    message = str(error.get("message") or "").strip()
    if code:
        parts.append(f"code={code}")
    if message:
        parts.append(f"message={message}")

    inner = error.get("innerError")
    if isinstance(inner, dict):
        request_id = str(inner.get("request-id") or inner.get("requestId") or "").strip()
        date = str(inner.get("date") or "").strip()
        if request_id:
            parts.append(f"request-id={request_id}")
        if date:
            parts.append(f"date={date}")

    return ", ".join(parts)


def normalize_teams_target(target: str) -> str:
    normalized = str(target or "").strip().strip("/")
    if normalized.startswith("chat:"):
        chat_id = normalized.removeprefix("chat:").strip()
        if not chat_id:
            raise ValueError(
                f"Invalid Teams target {target!r}: expected 'team-id/channel-id' or 'chat:<chat-id>'."
            )
        return f"chat:{chat_id}"
    team_id, separator, channel_id = normalized.partition("/")
    if not separator or not team_id or not channel_id:
        raise ValueError(
            f"Invalid Teams target {target!r}: expected 'team-id/channel-id' or 'chat:<chat-id>'."
        )
    return f"{team_id}/{channel_id}"


def parse_teams_target(target: str) -> TeamsTarget:
    normalized = normalize_teams_target(target)
    if normalized.startswith("chat:"):
        return TeamsTarget(kind="chat", normalized=normalized, chat_id=normalized.removeprefix("chat:"))
    team_id, _, channel_id = normalized.partition("/")
    return TeamsTarget(
        kind="channel",
        normalized=normalized,
        team_id=team_id,
        channel_id=channel_id,
    )


def teams_target_from_message(
    *,
    team_id: str | None = None,
    channel_id: str | None = None,
    chat_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = metadata if isinstance(metadata, dict) else {}
    raw_target = str(payload.get("target") or "").strip()
    if raw_target:
        return normalize_teams_target(raw_target)
    resolved_chat_id = str(payload.get("chat_id") or chat_id or "").strip()
    if resolved_chat_id:
        return normalize_teams_target(f"chat:{resolved_chat_id}")
    resolved_team_id = str(payload.get("team_id") or team_id or "").strip()
    resolved_channel_id = str(payload.get("channel_id") or channel_id or "").strip()
    return normalize_teams_target(f"{resolved_team_id}/{resolved_channel_id}")


class StubTeamsService:
    def __init__(self, message_store: JsonStore[TeamsMessageEnvelope]) -> None:
        self.message_store = message_store

    def get_messages(self, since_cursor: str | None = None) -> list[TeamsMessageEnvelope]:
        messages = list(self.message_store.read_all().values())
        for message in messages:
            normalized_target = teams_target_from_message(
                team_id=message.team_id,
                channel_id=message.channel_id,
                chat_id=message.chat_id,
                metadata=message.metadata,
            )
            parsed_target = parse_teams_target(normalized_target)
            message.metadata = {
                "source": message.metadata.get("source", "stub"),
                "target": normalized_target,
                "target_type": parsed_target.kind,
                "team_id": message.metadata.get("team_id", message.team_id),
                "channel_id": message.metadata.get("channel_id", message.channel_id),
                "chat_id": message.metadata.get("chat_id", message.chat_id),
                "thread_id": message.metadata.get("thread_id", message.thread_id),
                "author_id": message.metadata.get("author_id", message.author_id),
                **{
                    key: value
                    for key, value in message.metadata.items()
                    if key
                    not in {
                        "source",
                        "target",
                        "target_type",
                        "team_id",
                        "channel_id",
                        "chat_id",
                        "thread_id",
                        "author_id",
                    }
                },
            }
        ordered = _sorted_teams_messages({message.cursor: message for message in messages})
        return _filter_teams_messages_since_cursor(ordered, since_cursor)

    def post_status(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        normalized_thread_id = str(thread_id or "").strip() or None
        result: dict[str, Any] = {
            "status": "stubbed",
            "channel_target": target.normalized,
            "thread_id": normalized_thread_id,
            "session_id": session_id,
            "message_preview": message[:120],
        }
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result


class GraphTeamsService:
    def __init__(
        self,
        message_store: JsonStore[TeamsMessageEnvelope],
        *,
        token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        api_base_url: str,
        poll_limit: int,
        timeout_seconds: float,
        retry_count: int = 3,
        retry_base_backoff_seconds: float = 0.5,
        retry_max_backoff_seconds: float = 5.0,
        allowed_channel_targets_provider: Callable[[], list[str]] = lambda: [],
    ) -> None:
        self.message_store = message_store
        self.token = str(token or "").strip()
        self.token_provider = token_provider or (lambda: self.token)
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

    def _resolve_token(self) -> str:
        try:
            token = str(self.token_provider() or "").strip()
        except TeamsAuthenticationError:
            raise
        except Exception as exc:
            raise TeamsAuthenticationError(str(exc)) from exc
        if not token:
            raise TeamsAuthenticationError("Teams Graph access token provider returned an empty token.")
        self.token = token
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolve_token()}",
            "Content-Type": "application/json",
        }

    def _allowed_channel_targets(self) -> list[str]:
        return sorted(
            {
                normalize_teams_target(target)
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
                if status_code not in {429} and not (500 <= status_code <= 599):
                    raise _augment_http_status_error(exc) from exc
                if attempt >= self.retry_count:
                    raise _augment_http_status_error(exc) from exc
                last_error = _augment_http_status_error(exc)

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

    def _to_envelope(
        self,
        payload: dict[str, Any],
        *,
        target: TeamsTarget,
        source: str = "teams_graph_api",
        session_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> TeamsMessageEnvelope:
        message_id = str(payload.get("id") or "").strip()
        resolved_reply_to_id = str(payload.get("replyToId") or reply_to_id or "").strip() or None
        thread_id = resolved_reply_to_id or (message_id if target.kind == "channel" else None)
        cursor = f"{target.normalized}/{message_id}"
        body = payload.get("body") or {}
        author_id = self._author_id(payload)
        metadata = {
            "source": source,
            "target": target.normalized,
            "target_type": target.kind,
            "team_id": target.team_id,
            "channel_id": target.channel_id,
            "chat_id": target.chat_id,
            "thread_id": thread_id,
            "author_id": author_id,
            "reply_to_id": resolved_reply_to_id,
            "web_url": str(payload.get("webUrl") or "").strip() or None,
            "content_type": str(body.get("contentType") or "").strip() or None,
            "message": payload,
        }
        if session_id is not None:
            metadata["session_id"] = session_id
        return TeamsMessageEnvelope(
            cursor=cursor,
            message_id=message_id,
            author_id=author_id,
            team_id=target.team_id or "",
            channel_id=target.channel_id or "",
            chat_id=target.chat_id,
            thread_id=thread_id,
            content=str(body.get("content") or ""),
            created_at=_parse_graph_datetime(str(payload.get("createdDateTime") or "")),
            metadata=metadata,
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

    def _iter_chat_messages(
        self,
        client: httpx.Client,
        *,
        chat_id: str,
    ) -> Iterator[dict[str, Any]]:
        next_url = f"{self.api_base_url}/chats/{quote(chat_id, safe='')}/messages"
        params: dict[str, Any] | None = {"$top": self.poll_limit}
        seen_urls: set[str] = set()
        while next_url:
            if next_url in seen_urls:
                raise RuntimeError(f"Teams Graph pagination loop detected for chat {chat_id}")
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
                parsed_target = parse_teams_target(target)
                if parsed_target.kind == "chat":
                    iterator = self._iter_chat_messages(client, chat_id=parsed_target.chat_id or "")
                else:
                    iterator = self._iter_channel_messages(
                        client,
                        team_id=parsed_target.team_id or "",
                        channel_id=parsed_target.channel_id or "",
                    )
                for message_payload in iterator:
                    envelope = self._to_envelope(message_payload, target=parsed_target)
                    messages[envelope.cursor] = envelope
                    self.message_store.put(envelope.cursor, envelope)
                    for reply_payload in message_payload.get("replies") or []:
                        reply_envelope = self._to_envelope(reply_payload, target=parsed_target)
                        messages[reply_envelope.cursor] = reply_envelope
                        self.message_store.put(reply_envelope.cursor, reply_envelope)

        ordered = _sorted_teams_messages(messages)
        return _filter_teams_messages_since_cursor(ordered, since_cursor)

    def post_status(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        normalized_thread_id = str(thread_id or "").strip() or None
        if target.kind == "chat":
            if normalized_thread_id is not None:
                raise ValueError("Teams chat targets do not support thread replies.")
            url = f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages"
        else:
            encoded_team_id = quote(target.team_id or "", safe="")
            encoded_channel_id = quote(target.channel_id or "", safe="")
            if normalized_thread_id is None:
                url = (
                    f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages"
                )
            else:
                url = (
                    f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages/"
                    f"{quote(normalized_thread_id, safe='')}/replies"
                )

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = self._request_with_retries(
                client,
                "POST",
                url,
                json={"body": {"content": message, "contentType": "text"}},
            )
            payload = response.json()

        envelope = self._to_envelope(
            payload,
            target=target,
            source="teams_graph_api_status",
            session_id=session_id,
            reply_to_id=normalized_thread_id,
        )
        self.message_store.put(envelope.cursor, envelope)
        result = {
            "status": "posted",
            "channel_target": target.normalized,
            "thread_id": envelope.thread_id,
            "reply_to_message_id": normalized_thread_id,
            "session_id": session_id,
            "message_id": envelope.message_id,
            "web_url": envelope.metadata.get("web_url"),
        }
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result


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


class PrQueryProvider(Protocol):
    def list_pull_requests(
        self,
        *,
        repo: str,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        top: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse: ...

    def get_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse: ...


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


class AzureDevOpsPrQueryProvider:
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

    def list_pull_requests(
        self,
        *,
        repo: str,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        top: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        params: dict[str, Any] = {"api-version": "7.1"}
        if status and status.strip():
            params["searchCriteria.status"] = status.strip().lower()
        if creator_id and creator_id.strip():
            params["searchCriteria.creatorId"] = creator_id.strip()
        if reviewer_id and reviewer_id.strip():
            params["searchCriteria.reviewerId"] = reviewer_id.strip()
        if top is not None:
            params["$top"] = top
        if cursor and cursor.strip():
            params["continuationToken"] = cursor.strip()

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                    "pullrequests"
                ),
                params=params,
                auth=("", self.pat),
            )
            response.raise_for_status()
            payload = response.json()

        raw_items = payload.get("value")
        pr_items = raw_items if isinstance(raw_items, list) else []
        normalized_items: list[PullRequestItem] = []
        for item in pr_items:
            if not isinstance(item, dict):
                continue
            created_by = item.get("createdBy") if isinstance(item.get("createdBy"), dict) else {}
            normalized_items.append(
                PullRequestItem(
                    provider="azure_devops",
                    repo=repo,
                    pr_id=str(item.get("pullRequestId", "")).strip(),
                    title=(str(item.get("title", "")).strip() or None),
                    status=(str(item.get("status", "")).strip() or None),
                    created_at=_parse_dt(item["creationDate"]) if item.get("creationDate") else None,
                    updated_at=_parse_dt(item["creationDate"]) if item.get("creationDate") else None,
                    created_by=(
                        str(created_by.get("displayName") or created_by.get("uniqueName") or "").strip() or None
                    ),
                    url=(
                        str(item.get("url") or item.get("remoteUrl") or "").strip()
                        or item.get("_links", {}).get("web", {}).get("href")
                    ),
                    raw=item,
                )
            )
        next_cursor_header = response.headers.get("x-ms-continuationtoken")
        next_cursor_payload = payload.get("continuationToken")
        next_cursor = str(next_cursor_header or next_cursor_payload or "").strip() or None
        return ListPullRequestsResponse(
            pull_requests=normalized_items,
            next_cursor=next_cursor,
        )

    def get_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                    f"pullRequests/{pr_id}"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
            )
            response.raise_for_status()
            payload = response.json()

            commits_payload: list[dict[str, Any]] = []
            if include_commits:
                commits_response = client.get(
                    (
                        f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                        f"pullRequests/{pr_id}/commits"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                )
                commits_response.raise_for_status()
                commits_raw = commits_response.json()
                raw_items = commits_raw.get("value")
                if isinstance(raw_items, list):
                    commits_payload = [item for item in raw_items if isinstance(item, dict)]

        created_by = payload.get("createdBy") if isinstance(payload.get("createdBy"), dict) else {}
        raw_payload = payload if isinstance(payload, dict) else {}
        if include_commits:
            raw_payload = {**raw_payload, "commits": commits_payload}

        reviewer_items = payload.get("reviewers")
        reviewer_payload = reviewer_items if isinstance(reviewer_items, list) else []
        normalized_reviewers = (
            [
                PullRequestReviewer(
                    reviewer_id=str(item.get("id", "")).strip() or None,
                    display_name=str(item.get("displayName", "")).strip() or None,
                    vote=int(item["vote"]) if isinstance(item.get("vote"), int) else None,
                    status=str(item.get("vote", "")).strip() or None,
                    is_required=item.get("isRequired") if isinstance(item.get("isRequired"), bool) else None,
                )
                for item in reviewer_payload
                if isinstance(item, dict)
            ]
            if include_reviewers is not False
            else []
        )
        normalized_commits = [
            PullRequestCommit(
                commit_id=str(item.get("commitId", "")).strip(),
                url=str(item.get("url", "")).strip() or None,
                comment=str(item.get("comment", "")).strip() or None,
            )
            for item in commits_payload
            if str(item.get("commitId", "")).strip()
        ]
        return GetPullRequestResponse(
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            title=str(payload.get("title", "")).strip() or None,
            status=str(payload.get("status", "")).strip() or None,
            url=(
                str(payload.get("url") or payload.get("remoteUrl") or "").strip()
                or payload.get("_links", {}).get("web", {}).get("href")
            ),
            source_branch=str(payload.get("sourceRefName", "")).strip() or None,
            target_branch=str(payload.get("targetRefName", "")).strip() or None,
            source_commit=str(payload.get("lastMergeSourceCommit", {}).get("commitId", "")).strip() or None,
            target_commit=str(payload.get("lastMergeTargetCommit", {}).get("commitId", "")).strip() or None,
            created_by=(str(created_by.get("displayName") or created_by.get("uniqueName") or "").strip() or None),
            reviewers=normalized_reviewers,
            commits=normalized_commits,
            raw=raw_payload,
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


class PrQueryService:
    def __init__(self, *, providers: dict[str, PrQueryProvider]) -> None:
        self.providers = providers

    def list_pull_requests(
        self,
        *,
        provider: str,
        repo: str,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        top: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        provider_name = normalize_pr_feedback_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(f"PR query provider {provider_name!r} is not configured for this gateway instance")
        return self.providers[provider_name].list_pull_requests(
            repo=repo,
            status=status,
            creator_id=creator_id,
            reviewer_id=reviewer_id,
            top=top,
            cursor=cursor,
        )

    def get_pull_request(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse:
        provider_name = normalize_pr_feedback_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(f"PR query provider {provider_name!r} is not configured for this gateway instance")
        return self.providers[provider_name].get_pull_request(
            repo=repo,
            pr_id=pr_id,
            include_reviewers=include_reviewers,
            include_commits=include_commits,
        )


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


class PrPublishProvider(Protocol):
    def post_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
    ) -> dict[str, Any]: ...

    def submit_review(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
    ) -> dict[str, Any]: ...


class GitHubPrPublishProvider:
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

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def post_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
    ) -> dict[str, Any]:
        normalized_body = body_text.strip()
        if not normalized_body:
            raise ValueError("GitHub PR comment publishing requires body_text")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.api_base_url}/repos/{repo}/issues/{pr_id}/comments",
                headers=self._headers(),
                json={"body": normalized_body},
            )
            response.raise_for_status()
            payload = response.json()
        comment_id = str(payload.get("id", "")).strip() or None
        return {
            "status": "posted",
            "provider": "github",
            "repo": repo,
            "pr_id": pr_id,
            "comment_id": comment_id,
            "comment_url": payload.get("html_url") or payload.get("url"),
            "published_feedback_ids": [f"github:issue_comment:{comment_id}"] if comment_id else [],
        }

    def submit_review(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
    ) -> dict[str, Any]:
        normalized_body = body_text.strip()
        normalized_comments = _normalize_pr_review_comments(comments)
        normalized_event = _normalize_pr_review_event(event)
        if not normalized_body and not normalized_comments:
            raise ValueError("GitHub PR review publishing requires body_text or comments")

        review_payload: dict[str, Any] = {"event": normalized_event}
        if normalized_body:
            review_payload["body"] = normalized_body

        with httpx.Client(timeout=self.timeout_seconds) as client:
            review_response = client.post(
                f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/reviews",
                headers=self._headers(),
                json=review_payload,
            )
            review_response.raise_for_status()
            review_payload_json = review_response.json()

            comment_ids: list[str] = []
            comment_urls: list[str] = []
            for comment in normalized_comments:
                review_comment_payload: dict[str, Any] = {
                    "body": comment["body_text"],
                    "path": comment["path"],
                }
                for field_name in ("line", "start_line", "side", "start_side", "commit_id"):
                    value = comment.get(field_name)
                    if value is not None:
                        review_comment_payload[field_name] = value
                review_comment_response = client.post(
                    f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/comments",
                    headers=self._headers(),
                    json=review_comment_payload,
                )
                review_comment_response.raise_for_status()
                review_comment_json = review_comment_response.json()
                review_comment_id = str(review_comment_json.get("id", "")).strip() or None
                if review_comment_id is not None:
                    comment_ids.append(review_comment_id)
                comment_url = review_comment_json.get("html_url") or review_comment_json.get("url")
                if comment_url:
                    comment_urls.append(str(comment_url))

        review_id = str(review_payload_json.get("id", "")).strip() or None
        review_url = review_payload_json.get("html_url") or review_payload_json.get("url")
        return {
            "status": "posted",
            "provider": "github",
            "repo": repo,
            "pr_id": pr_id,
            "review_id": review_id,
            "review_url": review_url,
            "event": normalized_event,
            "comment_ids": comment_ids,
            "comment_urls": comment_urls,
            "published_feedback_ids": [
                *([f"github:review:{review_id}"] if review_id else []),
                *[f"github:review_comment:{comment_id}" for comment_id in comment_ids],
            ],
        }


class AzureDevOpsPrPublishProvider:
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

    def _repo_identifier(self, repo: str) -> str:
        return _normalize_azure_devops_repo_identifier(repo)

    def _post_thread_comment(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        body_text: str,
        thread_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": body_text.strip(), "commentType": "text"}],
            "status": "active",
        }
        if thread_context is not None:
            payload["threadContext"] = thread_context
        response = client.post(
            (
                f"{self.organization_url}/{self.project}/_apis/git/repositories/{self._repo_identifier(repo)}/"
                f"pullRequests/{pr_id}/threads"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
            json=payload,
        )
        response.raise_for_status()
        response_payload = response.json()
        thread_id = str(response_payload.get("id", "")).strip() or None
        comments = response_payload.get("comments") if isinstance(response_payload.get("comments"), list) else []
        first_comment = comments[0] if comments and isinstance(comments[0], dict) else {}
        comment_id = str(first_comment.get("id", "")).strip() or None
        comment_url = response_payload.get("_links", {}).get("web", {}).get("href")
        published_feedback_ids = []
        if thread_id and comment_id:
            published_feedback_ids.append(f"azure_devops:thread_comment:{thread_id}:{comment_id}")
        return {
            "thread_id": thread_id,
            "comment_id": comment_id,
            "comment_url": comment_url,
            "published_feedback_ids": published_feedback_ids,
        }

    def post_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
    ) -> dict[str, Any]:
        normalized_body = body_text.strip()
        if not normalized_body:
            raise ValueError("Azure DevOps PR comment publishing requires body_text")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            result = self._post_thread_comment(
                client=client,
                repo=repo,
                pr_id=pr_id,
                body_text=normalized_body,
            )
        return {
            "status": "posted",
            "provider": "azure_devops",
            "repo": repo,
            "pr_id": pr_id,
            **result,
        }

    def submit_review(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
    ) -> dict[str, Any]:
        normalized_body = body_text.strip()
        normalized_comments = _normalize_pr_review_comments(comments)
        if not normalized_body and not normalized_comments:
            raise ValueError("Azure DevOps PR review publishing requires body_text or comments")

        comment_ids: list[str] = []
        comment_urls: list[str] = []
        published_feedback_ids: list[str] = []
        summary_thread_id: str | None = None
        summary_comment_id: str | None = None
        summary_comment_url: str | None = None

        with httpx.Client(timeout=self.timeout_seconds) as client:
            if normalized_body:
                summary_result = self._post_thread_comment(
                    client=client,
                    repo=repo,
                    pr_id=pr_id,
                    body_text=normalized_body,
                )
                summary_thread_id = summary_result["thread_id"]
                summary_comment_id = summary_result["comment_id"]
                summary_comment_url = summary_result["comment_url"]
                if summary_comment_id is not None:
                    comment_ids.append(summary_comment_id)
                if summary_comment_url:
                    comment_urls.append(summary_comment_url)
                published_feedback_ids.extend(summary_result["published_feedback_ids"])

            for comment in normalized_comments:
                thread_context = {
                    "filePath": comment["path"],
                    "rightFileStart": {"line": comment["line"]} if comment.get("line") is not None else {},
                }
                if not thread_context["rightFileStart"]:
                    thread_context.pop("rightFileStart", None)
                comment_result = self._post_thread_comment(
                    client=client,
                    repo=repo,
                    pr_id=pr_id,
                    body_text=comment["body_text"],
                    thread_context=thread_context,
                )
                if comment_result["comment_id"] is not None:
                    comment_ids.append(comment_result["comment_id"])
                if comment_result["comment_url"]:
                    comment_urls.append(comment_result["comment_url"])
                published_feedback_ids.extend(comment_result["published_feedback_ids"])

        review_url = summary_comment_url or (comment_urls[0] if comment_urls else None)
        return {
            "status": "posted",
            "provider": "azure_devops",
            "repo": repo,
            "pr_id": pr_id,
            "event": event.strip().upper() if isinstance(event, str) and event.strip() else None,
            "review_id": None,
            "review_url": review_url,
            "summary_thread_id": summary_thread_id,
            "summary_comment_id": summary_comment_id,
            "summary_comment_url": summary_comment_url,
            "comment_ids": comment_ids,
            "comment_urls": comment_urls,
            "published_feedback_ids": published_feedback_ids,
        }


class PrPublishService:
    def __init__(
        self,
        *,
        providers: dict[str, PrPublishProvider],
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

    def post_comment(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._publish(
            operation="post_comment",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text=body_text,
            comments=None,
            event=None,
            idempotency_key=idempotency_key,
        )

    def submit_review(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._publish(
            operation="submit_review",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text=body_text,
            comments=comments,
            event=event,
            idempotency_key=idempotency_key,
        )

    def _publish(
        self,
        *,
        operation: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None,
        event: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        provider_name = normalize_pr_feedback_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR publish provider {provider_name!r} is not configured for this gateway instance"
            )
        normalized_key = idempotency_key.strip() if idempotency_key else None
        cache_key = (
            f"{operation}:{provider_name}:{repo}:{pr_id}:{normalized_key}" if normalized_key else None
        )
        if cache_key:
            with self._lock:
                existing = self._read_raw().get(cache_key)
            if isinstance(existing, dict):
                cached = dict(existing)
                cached["status"] = "deduplicated"
                return cached

        provider = self.providers[provider_name]
        if operation == "post_comment":
            response = provider.post_comment(repo=repo, pr_id=pr_id, body_text=body_text.strip())
        elif operation == "submit_review":
            response = provider.submit_review(
                repo=repo,
                pr_id=pr_id,
                body_text=body_text.strip(),
                comments=comments,
                event=event,
            )
        else:
            raise ValueError(f"Unsupported publish operation: {operation!r}")
        response = dict(response)
        response["idempotency_key"] = normalized_key
        self._record_ignored_feedback_ids(response)
        if cache_key:
            with self._lock:
                payload = self._read_raw()
                payload[cache_key] = response
                self._write_raw(payload)
        return response

    def _record_ignored_feedback_ids(self, response: dict[str, Any]) -> None:
        feedback_ids = _publication_feedback_ids(response)
        if not feedback_ids or self.ignored_feedback_store_path is None:
            return

        path = self.ignored_feedback_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                payload = {}
            normalized_payload, current_feedback_ids, _ = _normalize_ignored_feedback_payload(payload)
            entries = normalized_payload.get("feedback_ids", [])
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            for feedback_id in feedback_ids:
                if feedback_id in current_feedback_ids:
                    continue
                entries.append({"feedback_id": feedback_id, "ignored_at": now})
                current_feedback_ids.add(feedback_id)
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


def _normalize_pr_review_comments(comments: Any) -> list[dict[str, Any]]:
    if comments is None:
        return []
    if isinstance(comments, dict):
        comments = [comments]
    if not isinstance(comments, list):
        raise ValueError("PR review comments must be a list of objects")

    normalized: list[dict[str, Any]] = []
    for index, raw_comment in enumerate(comments):
        if not isinstance(raw_comment, dict):
            raise ValueError(f"PR review comment at index {index} must be an object")
        body_text = _normalize_optional_text(raw_comment.get("body_text") or raw_comment.get("body"))
        path = _normalize_optional_text(raw_comment.get("path"))
        if not body_text:
            raise ValueError(f"PR review comment at index {index} requires body_text")
        if not path:
            raise ValueError(f"PR review comment at index {index} requires path")
        normalized_comment: dict[str, Any] = {"body_text": body_text, "path": path}
        line = _as_int_or_none(raw_comment.get("line"))
        if line is not None:
            normalized_comment["line"] = line
        start_line = _as_int_or_none(raw_comment.get("start_line"))
        if start_line is not None:
            normalized_comment["start_line"] = start_line
        side = _normalize_optional_text(raw_comment.get("side"))
        if side is not None:
            normalized_comment["side"] = side.upper()
        start_side = _normalize_optional_text(raw_comment.get("start_side"))
        if start_side is not None:
            normalized_comment["start_side"] = start_side.upper()
        commit_id = _normalize_optional_text(raw_comment.get("commit_id"))
        if commit_id is not None:
            normalized_comment["commit_id"] = commit_id
        normalized.append(normalized_comment)
    return normalized


def _normalize_pr_review_event(event: Any) -> str:
    normalized = _normalize_optional_text(event)
    if normalized is None:
        return "COMMENT"
    upper = normalized.upper()
    if upper not in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
        raise ValueError(
            "PR review event must be one of: COMMENT, APPROVE, REQUEST_CHANGES"
        )
    return upper


def _publication_feedback_ids(response: dict[str, Any]) -> list[str]:
    feedback_ids = response.get("published_feedback_ids")
    if not isinstance(feedback_ids, list):
        return []
    normalized: list[str] = []
    for feedback_id in feedback_ids:
        if feedback_id is None:
            continue
        text = str(feedback_id).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
