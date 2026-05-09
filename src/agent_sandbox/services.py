from __future__ import annotations

import base64
import html
import json
import logging
import mimetypes
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterator, Literal, Protocol
from urllib.parse import quote, unquote, urlparse, urlunparse

import httpx
from agent_sandbox.models import (
    DiscordCommandEnvelope,
    DiscordMessageEnvelope,
    HasPendingDiscordMessagesResponse,
    HasPendingFeedbackResponse,
    GetPullRequestResponse,
    InlineMediaRef,
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
from agent_sandbox.messaging.discord_inbox_store import DiscordInboxStore
from agent_sandbox.repositories.cursors import DiscordInboxCursorRepository
from agent_sandbox.daemons.pr_feedback_worker import (
    PrFeedbackRepository,
    PrFeedbackStore,
    _stream_key,
    pr_feedback_item_payload,
)
from agent_sandbox.inline_media import (
    InlineMediaDownload,
    inline_media_download_from_embedded_payload,
    sanitize_inline_media_metadata,
)
from agent_sandbox.email.providers import (
    EmailAttachmentContent,
    EmailAttachmentMetadata,
    EmailDraft,
    EmailDraftAttachment,
    EmailMessageDetail,
    EmailMessageSummary,
    EmailProvider,
)
from agent_sandbox.repositories.records import DiscordRecordsRepository, TeamsRecordsRepository
from agent_sandbox.text_formatting import render_markdownish_text_to_html

_LOGGER = logging.getLogger(__name__)
_IGNORED_FEEDBACK_RETENTION = timedelta(days=90)
_GRAPH_TEAMS_REACTION_TYPE_BY_VALUE = {
    "like": "like",
    "thumbsup": "like",
    "thumbs_up": "like",
    "thumbs-up": "like",
    "heart": "heart",
    "love": "heart",
    "laugh": "laugh",
    "haha": "laugh",
    "joy": "laugh",
    "surprised": "surprised",
    "wow": "surprised",
    "eyes": "surprised",
    "sad": "sad",
    "cry": "sad",
    "angry": "angry",
    "mad": "angry",
}
_ATTACHMENT_SECRET_KEYS = {
    "authorization",
    "token",
    "access_token",
    "content",
    "contentBytes",
    "contentbytes",
    "content_bytes",
    "content_base64",
    "data",
}


@dataclass
class _TeamsGraphPaginationBudget:
    page_cap: int
    deadline_seconds: float
    started_at: float
    pages_read: int = 0

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_seconds - (time.monotonic() - self.started_at))

    def can_request_page(self) -> bool:
        return self.pages_read < self.page_cap and self.remaining_seconds() > 0

    def record_page(self) -> None:
        self.pages_read += 1


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


def _normalize_graph_teams_reaction_type(reaction: str) -> str:
    normalized = str(reaction).strip()
    if not normalized:
        raise ValueError("Teams reaction must be a non-empty string.")
    if not normalized.isascii():
        return normalized
    return _GRAPH_TEAMS_REACTION_TYPE_BY_VALUE.get(normalized.casefold(), normalized)


def _message_sort_value(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, value)


def _teams_message_to_html(message: str) -> str:
    rendered = render_markdownish_text_to_html(message)
    return rendered or "<p></p>"


def _sanitize_attachment_metadata(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): sanitize_inline_media_metadata(value)
        for key, value in raw.items()
        if str(key).replace("-", "_").lower() not in _ATTACHMENT_SECRET_KEYS
    }


def _teams_attachment_id(item: dict[str, Any]) -> str:
    return str(
        item.get("attachment_id")
        or item.get("id")
        or item.get("hosted_content_id")
        or item.get("temporaryId")
        or item.get("@microsoft.graph.temporaryId")
        or ""
    ).strip()


def _teams_hosted_content_id_from_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    path = parsed.path or text
    parts = [unquote(part) for part in path.split("/") if part and part != ".."]
    for index, part in enumerate(parts):
        if part.lower() == "hostedcontents" and index + 1 < len(parts):
            hosted_content_id = parts[index + 1].strip()
            return hosted_content_id or None
    return None


def _teams_content_type_extension(value: Any) -> str:
    content_type = str(value or "").split(";", 1)[0].strip().lower()
    if not content_type:
        return ""
    extension = mimetypes.guess_extension(content_type) or ""
    return ".jpg" if extension == ".jpe" else extension


def _teams_hosted_content_filename(item: dict[str, Any]) -> str:
    existing = str(item.get("filename") or item.get("name") or "").strip()
    if existing:
        return existing
    hosted_content_id = _teams_attachment_id(item) or "hosted-content"
    extension = _teams_content_type_extension(item.get("contentType") or item.get("content_type"))
    return f"hosted-content-{hosted_content_id}{extension}"


def _teams_hosted_content_attachment(raw: Any) -> dict[str, Any]:
    item = _sanitize_attachment_metadata(raw)
    if not item:
        return {}
    content_url = item.get("contentUrl") or item.get("content_url")
    hosted_content_id = _teams_hosted_content_id_from_url(content_url) or _teams_attachment_id(item)
    if hosted_content_id:
        original_id = str(item.get("id") or "").strip()
        if original_id and original_id != hosted_content_id:
            item.setdefault("graph_attachment_id", original_id)
        item["id"] = hosted_content_id
        item.setdefault("attachment_id", hosted_content_id)
        item.setdefault("hosted_content_id", hosted_content_id)
    item["attachment_kind"] = "hosted_content"
    item.setdefault("name", _teams_hosted_content_filename(item))
    item.setdefault("filename", item["name"])
    return item


def _sanitize_message_payload_attachments(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    payload = dict(raw)
    if isinstance(payload.get("attachments"), list):
        payload["attachments"] = [
            _sanitize_attachment_metadata(item)
            for item in payload["attachments"]
            if isinstance(item, dict)
        ]
    if isinstance(payload.get("hostedContents"), list):
        payload["hostedContents"] = [
            _sanitize_attachment_metadata(item)
            for item in payload["hostedContents"]
            if isinstance(item, dict)
        ]
    if isinstance(payload.get("replies"), list):
        payload["replies"] = [
            _sanitize_message_payload_attachments(item)
            for item in payload["replies"]
            if isinstance(item, dict)
        ]
    return payload


def _teams_payload_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for raw in payload.get("attachments") or []:
        if not isinstance(raw, dict):
            continue
        item = _sanitize_attachment_metadata(raw)
        if _teams_hosted_content_id_from_url(item.get("contentUrl") or item.get("content_url")):
            item = _teams_hosted_content_attachment(item)
        else:
            item.setdefault("attachment_kind", "graph_attachment")
        attachments.append(item)
    for raw in payload.get("hostedContents") or []:
        if not isinstance(raw, dict):
            continue
        item = _teams_hosted_content_attachment(raw)
        attachments.append(item)
    return attachments


def _artifact_filename(artifact: dict[str, Any], *, index: int) -> str:
    filename = str(artifact.get("filename") or artifact.get("name") or "").strip()
    return filename or f"attachment-{index + 1}"


def _artifact_content_type(artifact: dict[str, Any], filename: str) -> str:
    return str(
        artifact.get("mime_type")
        or artifact.get("content_type")
        or artifact.get("contentType")
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )


def _artifact_is_inline_teams_image(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower() in {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }


def _quote_teams_reference_content_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            quote(unquote(parsed.path), safe="/:@"),
            quote(unquote(parsed.params), safe="=;&:@"),
            quote(unquote(parsed.query), safe="=&?/:@%+,$;()-_.*!~'"),
            quote(unquote(parsed.fragment), safe="=&?/:@%+,$;()-_.*!~'"),
        )
    )


def _artifact_teams_reference_content_url(artifact: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        artifact.get("contentUrl"),
        artifact.get("content_url"),
    ]
    metadata = artifact.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("contentUrl"), metadata.get("content_url")])
        attachment_ref = metadata.get("attachment_ref")
        if isinstance(attachment_ref, dict):
            candidates.extend(
                [attachment_ref.get("contentUrl"), attachment_ref.get("content_url")]
            )
            ref_metadata = attachment_ref.get("metadata")
            if isinstance(ref_metadata, dict):
                candidates.extend(
                    [ref_metadata.get("contentUrl"), ref_metadata.get("content_url")]
                )
    for candidate in candidates:
        normalized = _quote_teams_reference_content_url(candidate)
        if normalized:
            return normalized
    return None


def _artifact_bytes(artifact: dict[str, Any]) -> bytes:
    path = artifact.get("path")
    if path:
        return Path(str(path)).read_bytes()
    data = artifact.get("content") or artifact.get("content_bytes") or artifact.get("bytes")
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        if artifact.get("encoding") == "base64" or artifact.get("content_base64"):
            return base64.b64decode(str(artifact.get("content_base64") or data))
        return data.encode("utf-8")
    content_base64 = artifact.get("content_base64")
    if isinstance(content_base64, str):
        return base64.b64decode(content_base64)
    raise ValueError("Artifact payload must include a readable path or inline content bytes.")


class EmailService:
    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        return self.provider.list_unread(mailbox_id=mailbox_id, folder=folder, limit=limit)

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        return self.provider.get_message(mailbox_id=mailbox_id, message_id=message_id)

    def list_attachments(
        self, mailbox_id: str, message_id: str
    ) -> list[EmailAttachmentMetadata]:
        return self.provider.list_attachments(mailbox_id=mailbox_id, message_id=message_id)

    def download_attachment(
        self, mailbox_id: str, message_id: str, attachment_id: str
    ) -> EmailAttachmentContent:
        return self.provider.download_attachment(
            mailbox_id=mailbox_id, message_id=message_id, attachment_id=attachment_id
        )

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
        attachments: list[EmailDraftAttachment] | None = None,
    ) -> EmailDraft:
        return self.provider.create_draft(
            mailbox_id=mailbox_id,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
            attachments=attachments,
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

    def list_attachments(
        self, message_id: str, *, channel_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    def download_attachment(
        self, message_id: str, attachment_id: str, *, channel_id: str | None = None
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


class TeamsService(Protocol):
    def get_messages(
        self,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
        channel_target: str | None = None,
    ) -> list["TeamsMessageEnvelope"]: ...

    def post_status(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]: ...

    def list_attachments(
        self, message_id: str, *, channel_target: str | None = None
    ) -> list[dict[str, Any]]: ...

    def download_attachment(
        self, message_id: str, attachment_id: str, *, channel_target: str | None = None
    ) -> dict[str, Any]: ...

    def post_file(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> dict[str, Any]: ...

    def add_reaction(
        self,
        channel_target: str,
        message_id: str,
        reaction: str,
        session_id: str,
    ) -> dict[str, Any]: ...


class TeamsInlineMediaProvider:
    """Inline-media adapter backed by the existing Teams attachment APIs."""

    provider_name = "teams"

    def __init__(self, service: TeamsService) -> None:
        self.service = service

    def list_inline_media(
        self,
        *,
        source_kind: str,
        resource_id: str,
        resource: dict[str, Any] | None = None,
        comment_id: str | None = None,
        channel_target: str | None = None,
    ) -> list[InlineMediaRef]:
        refs: list[InlineMediaRef] = []
        for index, item in enumerate(
            self.service.list_attachments(resource_id, channel_target=channel_target)
        ):
            mime_type = str(
                item.get("mime_type") or item.get("content_type") or item.get("contentType") or ""
            ).split(";", 1)[0].strip() or None
            filename = str(
                item.get("filename") or item.get("name") or f"teams-inline-media-{index}"
            ).strip()
            attachment_id = _teams_attachment_id(item) or str(index)
            byte_count: int | None = None
            raw_size = item.get("byte_count", item.get("size", item.get("contentSize")))
            if raw_size is not None:
                try:
                    byte_count = int(raw_size)
                except (TypeError, ValueError):
                    byte_count = None
            refs.append(
                InlineMediaRef(
                    source_kind=source_kind,
                    provider=self.provider_name,
                    resource_id=resource_id,
                    comment_id=comment_id,
                    media_id=attachment_id,
                    filename=filename,
                    mime_type=mime_type,
                    byte_count=byte_count,
                    url=(
                        str(item.get("contentUrl") or item.get("content_url") or "").strip()
                        or None
                    ),
                    metadata=_sanitize_attachment_metadata(item),
                )
            )
        return refs

    def download_inline_media(
        self,
        *,
        media: InlineMediaRef,
        raw: dict[str, Any] | None = None,
        channel_target: str | None = None,
        max_bytes: int | None = None,
    ) -> InlineMediaDownload:
        embedded = inline_media_download_from_embedded_payload(media, raw)
        if embedded is not None:
            if max_bytes is not None and len(embedded.content) > max_bytes:
                raise ValueError("Inline media exceeds max bytes")
            return embedded
        downloaded = self.service.download_attachment(
            media.resource_id, media.media_id, channel_target=channel_target
        )
        data = downloaded.get("content")
        if not isinstance(data, bytes):
            embedded = inline_media_download_from_embedded_payload(media, downloaded)
            if embedded is not None:
                data = embedded.content
            else:
                raise ValueError("Teams inline media download did not return bytes")
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("Inline media exceeds max bytes")
        return InlineMediaDownload(
            data,
            str(downloaded.get("filename") or downloaded.get("name") or media.filename),
            (
                str(
                    downloaded.get("mime_type")
                    or downloaded.get("contentType")
                    or media.mime_type
                    or ""
                )
                or None
            ),
        )


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
        self, message_id: str, attachment_id: str, *, channel_id: str | None = None
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
        operator_user_ids_provider: Callable[[], list[str]] | None = None,
        include_operator_dms_provider: Callable[[], bool] | None = None,
        include_operator_dms: bool = False,
        read_reaction_emoji: str = "👀",
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
        self.include_operator_dms_provider = include_operator_dms_provider or (
            lambda: include_operator_dms
        )
        self.read_reaction_emoji = read_reaction_emoji

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
        data: dict[str, Any] | None = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        start_time = time.monotonic()
        attempt = 0
        last_error: Exception | None = None
        while True:
            try:
                response = client.request(
                    method,
                    url,
                    headers=headers or self._headers(),
                    params=params,
                    json=json,
                    data=data,
                    files=files,
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
                    attachments = [
                        _sanitize_attachment_metadata(item)
                        for item in payload.get("attachments") or []
                        if isinstance(item, dict)
                    ]
                    safe_payload = dict(payload)
                    safe_payload["attachments"] = attachments
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
                            "attachments": attachments,
                            "message": safe_payload,
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

    def _require_allowed_channel_id(self, channel_id: str) -> None:
        allowed = set(self._allowed_channel_ids())
        if allowed and channel_id not in allowed:
            raise ValueError(f"Discord channel is not allowlisted: {channel_id}")

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
        self, message_id: str, attachment_id: str, *, channel_id: str | None = None
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
            response = self._request_with_retries(client, "GET", download_url, headers=headers)
            content_type = response.headers.get("content-type") or attachment.get("content_type")
            return {
                **attachment,
                "filename": attachment.get("filename") or attachment.get("name") or attachment_id,
                "mime_type": content_type,
                "content": response.content,
            }

    def post_file(
        self,
        message: str,
        channel_id: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._require_allowed_channel_id(channel_id)
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
        response_attachments = [
            _sanitize_attachment_metadata(item)
            for item in response_payload.get("attachments") or []
            if isinstance(item, dict)
        ]
        safe_response_payload = dict(response_payload)
        safe_response_payload["attachments"] = response_attachments

        envelope = DiscordCommandEnvelope(
            command_id=response_payload["id"],
            author_id=response_payload["author"]["id"],
            channel_id=channel_id,
            content=response_payload.get("content", ""),
            metadata={
                "source": "discord_bot_api_status",
                "guild_id": response_payload.get("guild_id"),
                "channel_id": channel_id,
                "author_id": response_payload["author"]["id"],
                "session_id": session_id,
                "attachments": response_attachments,
                "message": safe_response_payload,
            },
        )
        self.command_store.put(envelope.command_id, envelope)
        return {
            "status": "posted",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_id": response_payload["id"],
            "artifact_count": len(artifacts),
        }

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=None) as client:
            response = self._request_with_retries(
                client,
                "POST",
                f"{self.api_base_url}/channels/{channel_id}/messages",
                json={"content": message},
            )
            payload = response.json()
        attachments = [
            _sanitize_attachment_metadata(item)
            for item in payload.get("attachments") or []
            if isinstance(item, dict)
        ]
        safe_payload = dict(payload)
        safe_payload["attachments"] = attachments

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
                "attachments": attachments,
                "message": safe_payload,
            },
        )
        self.command_store.put(envelope.command_id, envelope)
        return {
            "status": "posted",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_id": payload["id"],
        }

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


def _parse_teams_cursor(cursor: str | None) -> tuple[str, str] | None:
    normalized = str(cursor or "").strip()
    if not normalized:
        return None
    if "/" not in normalized:
        return None
    target, message_id = normalized.rsplit("/", 1)
    if not target or not message_id:
        return None
    return target, message_id


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
        return TeamsTarget(
            kind="chat", normalized=normalized, chat_id=normalized.removeprefix("chat:")
        )
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
    def __init__(
        self,
        message_store: TeamsRecordsRepository,
        *,
        read_reaction_emoji: str = "👀",
    ) -> None:
        self.message_store = message_store
        self.read_reaction_emoji = read_reaction_emoji

    def get_messages(
        self,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
        channel_target: str | None = None,
    ) -> list[TeamsMessageEnvelope]:
        if since_cursor and before_cursor:
            raise ValueError("since_cursor and before_cursor are mutually exclusive")
        normalized_requested_target = (
            normalize_teams_target(channel_target) if channel_target is not None else None
        )
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
        if normalized_requested_target is not None:
            ordered = [
                message
                for message in ordered
                if teams_target_from_message(
                    team_id=message.team_id,
                    channel_id=message.channel_id,
                    chat_id=message.chat_id,
                    metadata=message.metadata,
                )
                == normalized_requested_target
            ]
        if before_cursor is not None:
            if any(message.cursor == before_cursor for message in ordered):
                ordered = [message for message in ordered if message.cursor < before_cursor]
            else:
                ordered = [message for message in ordered if message.cursor < before_cursor]
            if limit is not None:
                ordered = ordered[-limit:]
            return ordered
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

    def list_attachments(
        self, message_id: str, *, channel_target: str | None = None
    ) -> list[dict[str, Any]]:
        record = self.message_store.get(message_id)
        if record is None:
            record = next(
                (
                    message
                    for message in self.message_store.read_all().values()
                    if message.message_id == message_id
                ),
                None,
            )
        if record is None:
            return []
        if channel_target is not None:
            requested_target = normalize_teams_target(channel_target)
            actual_target = teams_target_from_message(
                team_id=record.team_id,
                channel_id=record.channel_id,
                chat_id=record.chat_id,
                metadata=record.metadata,
            )
            if requested_target != actual_target:
                return []
        raw_attachments = record.metadata.get("attachments")
        if raw_attachments is None and isinstance(record.metadata.get("message"), dict):
            raw_attachments = record.metadata["message"].get("attachments")
        return [_sanitize_attachment_metadata(item) for item in raw_attachments or []]

    def download_attachment(
        self, message_id: str, attachment_id: str, *, channel_target: str | None = None
    ) -> dict[str, Any]:
        for attachment in self.list_attachments(message_id, channel_target=channel_target):
            if str(attachment.get("id") or attachment.get("attachment_id") or "") == attachment_id:
                content = attachment.get("content") or attachment.get("content_base64")
                if isinstance(content, str):
                    return {**attachment, "content": content}
        raise ValueError(f"Unknown Teams attachment: {attachment_id}")

    def post_file(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        result = self.post_status(message, target.normalized, session_id, thread_id=thread_id)
        result["artifact_count"] = len(artifacts)
        return result

    def add_reaction(
        self,
        channel_target: str,
        message_id: str,
        reaction: str,
        session_id: str,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        normalized_reaction = str(reaction).strip()
        result: dict[str, Any] = {
            "status": "stubbed",
            "channel_target": target.normalized,
            "session_id": session_id,
            "message_id": message_id,
            "reaction": normalized_reaction,
            "emoji": normalized_reaction,
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
        message_store: TeamsRecordsRepository,
        *,
        token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        api_base_url: str,
        poll_limit: int,
        timeout_seconds: float,
        history_page_cap: int = 1,
        history_deadline_seconds: float = 5.0,
        retry_count: int = 3,
        retry_base_backoff_seconds: float = 0.5,
        retry_max_backoff_seconds: float = 5.0,
        allowed_channel_targets_provider: Callable[[], list[str]] = lambda: [],
        read_reaction_emoji: str = "👀",
    ) -> None:
        self.message_store = message_store
        self.token = str(token or "").strip()
        self.token_provider = token_provider or (lambda: self.token)
        self.api_base_url = api_base_url.rstrip("/")
        self.poll_limit = poll_limit
        self.history_page_cap = max(1, int(history_page_cap))
        self.history_deadline_seconds = max(0.001, float(history_deadline_seconds))
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.retry_base_backoff_seconds = retry_base_backoff_seconds
        self.retry_max_backoff_seconds = retry_max_backoff_seconds
        self.max_total_retry_time_seconds = sum(
            min(self.retry_max_backoff_seconds, self.retry_base_backoff_seconds * (2**attempt))
            for attempt in range(self.retry_count)
        )
        self.allowed_channel_targets_provider = allowed_channel_targets_provider
        self.read_reaction_emoji = read_reaction_emoji

    def _resolve_token(self) -> str:
        try:
            token = str(self.token_provider() or "").strip()
        except TeamsAuthenticationError:
            raise
        except Exception as exc:
            raise TeamsAuthenticationError(str(exc)) from exc
        if not token:
            raise TeamsAuthenticationError(
                "Teams Graph access token provider returned an empty token."
            )
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
        timeout_seconds: float | None = None,
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
                    timeout=timeout_seconds
                    if timeout_seconds is not None
                    else self.timeout_seconds,
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

    def _is_graph_api_origin(self, url: str) -> bool:
        parsed_url = urlparse(url)
        parsed_api_base = urlparse(self.api_base_url)
        return (
            parsed_url.scheme.lower() == parsed_api_base.scheme.lower()
            and parsed_url.netloc.lower() == parsed_api_base.netloc.lower()
        )

    @staticmethod
    def _is_sharepoint_content_url(url: str) -> bool:
        parsed_url = urlparse(url)
        host = parsed_url.netloc.lower()
        return any(
            host.endswith(suffix)
            for suffix in (
                ".sharepoint.com",
                ".sharepoint.us",
                ".sharepoint.de",
                ".sharepoint.cn",
                ".sharepoint-df.com",
            )
        )

    def _graph_share_content_url(self, url: str) -> str:
        encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        return f"{self.api_base_url}/shares/u!{encoded}/driveItem/content"

    def _download_graph_content_url(self, client: httpx.Client, url: str) -> httpx.Response:
        response = client.request("GET", url, headers=self._headers(), timeout=self.timeout_seconds)
        if response.is_redirect:
            redirect_url = str(response.headers.get("location") or "").strip()
            if not redirect_url:
                response.raise_for_status()
            redirected = client.request("GET", redirect_url, timeout=self.timeout_seconds)
            redirected.raise_for_status()
            return redirected
        response.raise_for_status()
        return response

    def _download_attachment_content_url(
        self,
        client: httpx.Client,
        url: str,
    ) -> httpx.Response:
        parsed_url = urlparse(url)
        if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Teams attachment contentUrl must be an absolute http(s) URL.")
        if self._is_graph_api_origin(url):
            return self._download_graph_content_url(client, url)
        if self._is_sharepoint_content_url(url):
            return self._download_graph_content_url(client, self._graph_share_content_url(url))
        response = client.request("GET", url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response

    def new_pagination_budget(self) -> _TeamsGraphPaginationBudget:
        return _TeamsGraphPaginationBudget(
            page_cap=self.history_page_cap,
            deadline_seconds=self.history_deadline_seconds,
            started_at=time.monotonic(),
        )

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
        attachments = _teams_payload_attachments(payload)
        safe_payload = _sanitize_message_payload_attachments(payload)
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
            "attachments": attachments,
            "message": safe_payload,
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
        budget: _TeamsGraphPaginationBudget | None = None,
    ) -> Iterator[dict[str, Any]]:
        if budget is None:
            budget = self.new_pagination_budget()
        next_url = (
            f"{self.api_base_url}/teams/{quote(team_id, safe='')}/channels/"
            f"{quote(channel_id, safe='')}/messages"
        )
        params: dict[str, Any] | None = {"$top": self.poll_limit, "$expand": "replies"}
        seen_urls: set[str] = set()
        while next_url:
            if not budget.can_request_page():
                break
            remaining_seconds = budget.remaining_seconds()
            if remaining_seconds <= 0:
                break
            if next_url in seen_urls:
                raise RuntimeError(
                    f"Teams Graph pagination loop detected for channel {team_id}/{channel_id}"
                )
            seen_urls.add(next_url)
            response = self._request_with_retries(
                client,
                "GET",
                next_url,
                params=params,
                timeout_seconds=min(self.timeout_seconds, remaining_seconds),
            )
            budget.record_page()
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
        budget: _TeamsGraphPaginationBudget | None = None,
    ) -> Iterator[dict[str, Any]]:
        if budget is None:
            budget = self.new_pagination_budget()
        next_url = f"{self.api_base_url}/chats/{quote(chat_id, safe='')}/messages"
        params: dict[str, Any] | None = {"$top": self.poll_limit}
        seen_urls: set[str] = set()
        while next_url:
            if not budget.can_request_page():
                break
            remaining_seconds = budget.remaining_seconds()
            if remaining_seconds <= 0:
                break
            if next_url in seen_urls:
                raise RuntimeError(f"Teams Graph pagination loop detected for chat {chat_id}")
            seen_urls.add(next_url)
            response = self._request_with_retries(
                client,
                "GET",
                next_url,
                params=params,
                timeout_seconds=min(self.timeout_seconds, remaining_seconds),
            )
            budget.record_page()
            payload = response.json()
            for message_payload in payload.get("value", []):
                yield message_payload
            raw_next_url = payload.get("@odata.nextLink")
            next_url = str(raw_next_url).strip() if raw_next_url else ""
            params = None

    def get_messages(
        self,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
        channel_target: str | None = None,
    ) -> list[TeamsMessageEnvelope]:
        if since_cursor and before_cursor:
            raise ValueError("since_cursor and before_cursor are mutually exclusive")
        effective_limit = limit
        if since_cursor is None and before_cursor is None and effective_limit is None:
            effective_limit = self.poll_limit
        messages: dict[str, TeamsMessageEnvelope] = dict(self.message_store.read_all())
        since_cursor_parts = _parse_teams_cursor(since_cursor)
        before_cursor_parts = _parse_teams_cursor(before_cursor)
        requested_target = None
        target_list = self._allowed_channel_targets()
        if channel_target is not None:
            requested_target = normalize_teams_target(channel_target)
            if requested_target not in set(target_list):
                return []
            target_list = [requested_target]
        budget = self.new_pagination_budget()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for target in target_list:
                if not budget.can_request_page():
                    break
                parsed_target = parse_teams_target(target)
                target_cursor = None
                target_mode = "all"
                if (
                    since_cursor_parts is not None
                    and since_cursor_parts[0] == parsed_target.normalized
                ):
                    target_cursor = since_cursor_parts[1]
                    target_mode = "since"
                elif (
                    before_cursor_parts is not None
                    and before_cursor_parts[0] == parsed_target.normalized
                ):
                    target_cursor = before_cursor_parts[1]
                    target_mode = "before"
                if parsed_target.kind == "chat":
                    iterator = self._iter_chat_messages(
                        client,
                        chat_id=parsed_target.chat_id or "",
                        budget=budget,
                    )
                else:
                    iterator = self._iter_channel_messages(
                        client,
                        team_id=parsed_target.team_id or "",
                        channel_id=parsed_target.channel_id or "",
                        budget=budget,
                    )
                target_count = 0
                before_seen = target_mode != "before"
                for message_payload in iterator:
                    payloads = [message_payload, *(message_payload.get("replies") or [])]
                    stop_target = False
                    for payload in payloads:
                        envelope = self._to_envelope(payload, target=parsed_target)
                        envelope = self._preserve_status_source_metadata(
                            known=messages.get(envelope.cursor),
                            fetched=envelope,
                        )
                        messages[envelope.cursor] = envelope
                        self.message_store.put(envelope.cursor, envelope)

                        if target_mode == "since":
                            if envelope.message_id == target_cursor:
                                stop_target = True
                                break
                            target_count += 1
                            if effective_limit is not None and target_count >= effective_limit:
                                stop_target = True
                                break
                            continue

                        if target_mode == "before":
                            if not before_seen:
                                if envelope.message_id == target_cursor:
                                    before_seen = True
                                continue
                            target_count += 1
                            if effective_limit is not None and target_count >= effective_limit:
                                stop_target = True
                                break
                            continue

                        target_count += 1
                        if effective_limit is not None and target_count >= effective_limit:
                            stop_target = True
                            break

                    if stop_target:
                        break

        ordered = _sorted_teams_messages(messages)
        if requested_target is not None:
            ordered = [
                message
                for message in ordered
                if teams_target_from_message(
                    team_id=message.team_id,
                    channel_id=message.channel_id,
                    chat_id=message.chat_id,
                    metadata=message.metadata,
                )
                == requested_target
            ]
        if before_cursor is not None:
            ordered = [message for message in ordered if message.cursor < before_cursor]
            if effective_limit is not None:
                ordered = ordered[-effective_limit:]
            return ordered
        if effective_limit is not None and since_cursor is None:
            ordered = ordered[-effective_limit:]
        return _filter_teams_messages_since_cursor(ordered, since_cursor)

    @staticmethod
    def _preserve_status_source_metadata(
        *,
        known: TeamsMessageEnvelope | None,
        fetched: TeamsMessageEnvelope,
    ) -> TeamsMessageEnvelope:
        if known is None:
            return fetched
        known_source = str(known.metadata.get("source") or "").strip()
        if known_source != "teams_graph_api_status":
            return fetched
        merged_metadata = dict(fetched.metadata)
        merged_metadata["source"] = "teams_graph_api_status"
        known_session_id = str(known.metadata.get("session_id") or "").strip()
        if known_session_id:
            merged_metadata["session_id"] = known_session_id
        return fetched.model_copy(update={"metadata": merged_metadata})

    def _require_allowed_target(self, channel_target: str) -> TeamsTarget:
        normalized = normalize_teams_target(channel_target)
        allowed = set(self._allowed_channel_targets())
        if allowed and normalized not in allowed:
            raise ValueError(f"Teams target is not allowlisted: {normalized}")
        return parse_teams_target(normalized)

    def _find_message_record(
        self, message_id: str, *, channel_target: str | None = None
    ) -> tuple[TeamsMessageEnvelope, TeamsTarget]:
        requested_target = self._require_allowed_target(channel_target) if channel_target else None
        record = self.message_store.get(message_id)
        if record is None:
            record = next(
                (
                    message
                    for message in self.message_store.read_all().values()
                    if message.message_id == message_id
                ),
                None,
            )
        if record is None:
            raise ValueError(f"Unknown Teams message_id: {message_id}")
        actual_target = parse_teams_target(
            teams_target_from_message(
                team_id=record.team_id,
                channel_id=record.channel_id,
                chat_id=record.chat_id,
                metadata=record.metadata,
            )
        )
        if requested_target is not None and requested_target.normalized != actual_target.normalized:
            raise ValueError("Teams message does not belong to the requested target.")
        self._require_allowed_target(actual_target.normalized)
        return record, actual_target

    def list_attachments(
        self, message_id: str, *, channel_target: str | None = None
    ) -> list[dict[str, Any]]:
        record, target = self._find_message_record(message_id, channel_target=channel_target)
        return self._list_attachments_for_record(record, target, include_remote_hosted=True)

    def _stored_attachment_candidates(self, record: TeamsMessageEnvelope) -> list[dict[str, Any]]:
        raw_attachments = record.metadata.get("attachments")
        if raw_attachments is None and isinstance(record.metadata.get("message"), dict):
            raw_attachments = _teams_payload_attachments(record.metadata["message"])
        return [
            _sanitize_attachment_metadata(item)
            for item in raw_attachments or []
            if isinstance(item, dict)
        ]

    def _record_has_hosted_content_hint(self, record: TeamsMessageEnvelope) -> bool:
        values = [record.content]
        raw_message = record.metadata.get("message")
        if isinstance(raw_message, dict):
            body = raw_message.get("body")
            if isinstance(body, dict):
                values.append(str(body.get("content") or ""))
            values.append(str(raw_message.get("content") or ""))
        for attachment in self._stored_attachment_candidates(record):
            values.append(str(attachment.get("contentUrl") or attachment.get("content_url") or ""))
        return "hostedcontents/" in " ".join(values).lower()

    def _merge_attachment_lists(
        self,
        stored_attachments: list[dict[str, Any]],
        hosted_attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        indexes: dict[str, int] = {}
        for attachment in [*stored_attachments, *hosted_attachments]:
            attachment_id = _teams_attachment_id(attachment)
            if attachment_id and attachment_id in indexes:
                existing = dict(merged[indexes[attachment_id]])
                for key, value in attachment.items():
                    if existing.get(key) in (None, "") and value not in (None, ""):
                        existing[key] = value
                merged[indexes[attachment_id]] = existing
                continue
            if attachment_id:
                indexes[attachment_id] = len(merged)
            merged.append(attachment)
        return merged

    def _list_hosted_content_attachments(
        self,
        record: TeamsMessageEnvelope,
        target: TeamsTarget,
    ) -> list[dict[str, Any]]:
        url = (
            f"{self._teams_message_url(target, record.message_id, thread_id=record.thread_id)}"
            "/hostedContents"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = self._request_with_retries(client, "GET", url)
        payload = response.json()
        return [
            _teams_hosted_content_attachment(item)
            for item in payload.get("value", [])
            if isinstance(item, dict)
        ]

    def _cache_record_attachments(
        self,
        record: TeamsMessageEnvelope,
        attachments: list[dict[str, Any]],
    ) -> TeamsMessageEnvelope:
        metadata = dict(record.metadata)
        metadata["attachments"] = attachments
        updated = record.model_copy(update={"metadata": metadata})
        self.message_store.put(updated.cursor, updated)
        return updated

    def _list_attachments_for_record(
        self,
        record: TeamsMessageEnvelope,
        target: TeamsTarget,
        *,
        include_remote_hosted: bool,
    ) -> list[dict[str, Any]]:
        stored_attachments = self._stored_attachment_candidates(record)
        if not include_remote_hosted or not self._record_has_hosted_content_hint(record):
            return stored_attachments
        hosted_attachments = self._list_hosted_content_attachments(record, target)
        if not hosted_attachments:
            return stored_attachments
        merged = self._merge_attachment_lists(stored_attachments, hosted_attachments)
        self._cache_record_attachments(record, merged)
        return merged

    def _teams_message_url(
        self, target: TeamsTarget, message_id: str, *, thread_id: str | None = None
    ) -> str:
        if target.kind == "chat":
            return (
                f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages/"
                f"{quote(message_id, safe='')}"
            )
        root_url = (
            f"{self.api_base_url}/teams/{quote(target.team_id or '', safe='')}/channels/"
            f"{quote(target.channel_id or '', safe='')}/messages"
        )
        if thread_id and thread_id != message_id:
            return f"{root_url}/{quote(thread_id, safe='')}/replies/{quote(message_id, safe='')}"
        return f"{root_url}/{quote(message_id, safe='')}"

    def download_attachment(
        self, message_id: str, attachment_id: str, *, channel_target: str | None = None
    ) -> dict[str, Any]:
        record, target = self._find_message_record(message_id, channel_target=channel_target)
        attachments = self._list_attachments_for_record(
            record,
            target,
            include_remote_hosted=False,
        )
        attachment = next(
            (
                item
                for item in attachments
                if _teams_attachment_id(item) == attachment_id
            ),
            None,
        )
        if attachment is None and self._record_has_hosted_content_hint(record):
            attachments = self._list_attachments_for_record(
                record,
                target,
                include_remote_hosted=True,
            )
            attachment = next(
                (
                    item
                    for item in attachments
                    if _teams_attachment_id(item) == attachment_id
                ),
                None,
            )
        if attachment is None:
            raise ValueError(f"Unknown Teams attachment: {attachment_id}")

        content_url = str(
            attachment.get("contentUrl") or attachment.get("content_url") or ""
        ).strip()
        hosted_content_id = str(attachment.get("hosted_content_id") or attachment_id).strip()
        if (
            attachment.get("attachment_kind") == "hosted_content"
            or _teams_hosted_content_id_from_url(content_url)
            or not content_url
        ):
            url = (
                f"{self._teams_message_url(target, record.message_id, thread_id=record.thread_id)}"
                f"/hostedContents/{quote(hosted_content_id, safe='')}/$value"
            )
        else:
            url = content_url
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = (
                self._request_with_retries(client, "GET", url)
                if attachment.get("attachment_kind") == "hosted_content" or not content_url
                else self._download_attachment_content_url(client, url)
            )
            content_type = response.headers.get("content-type") or attachment.get("contentType")
        filename = str(attachment.get("name") or attachment.get("filename") or attachment_id)
        if attachment.get("attachment_kind") == "hosted_content" and not Path(filename).suffix:
            filename = f"{filename}{_teams_content_type_extension(content_type)}"
        return {
            **attachment,
            "filename": filename,
            "mime_type": content_type,
            "content": response.content,
        }

    def _post_file_url(self, target: TeamsTarget, thread_id: str | None) -> str:
        if target.kind == "chat":
            if thread_id:
                raise ValueError("Teams chat targets do not support thread replies.")
            return f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages"
        encoded_team_id = quote(target.team_id or "", safe="")
        encoded_channel_id = quote(target.channel_id or "", safe="")
        if not thread_id:
            return (
                f"{self.api_base_url}/teams/{encoded_team_id}/channels/"
                f"{encoded_channel_id}/messages"
            )
        return (
            f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages/"
            f"{quote(thread_id, safe='')}/replies"
        )

    def _teams_file_message_payload(
        self, message: str, artifacts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        hosted_contents: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        inline_images: list[tuple[str, str]] = []
        attachment_references: list[str] = []
        for index, artifact in enumerate(artifacts):
            temporary_id = str(index + 1)
            filename = _artifact_filename(artifact, index=index)
            content_type = _artifact_content_type(artifact, filename)
            if _artifact_is_inline_teams_image(content_type):
                hosted_content_url = f"../hostedContents/{temporary_id}/$value"
                hosted_contents.append(
                    {
                        "@microsoft.graph.temporaryId": temporary_id,
                        "contentBytes": base64.b64encode(_artifact_bytes(artifact)).decode(
                            "ascii"
                        ),
                        "contentType": content_type,
                    }
                )
                inline_images.append((filename, hosted_content_url))
                continue

            reference_content_url = _artifact_teams_reference_content_url(artifact)
            if not reference_content_url:
                raise ValueError(
                    "Teams file attachments require a SharePoint/OneDrive contentUrl; "
                    "staged bytes can only be sent as inline image hosted content."
                )
            attachments.append(
                {
                    "id": temporary_id,
                    "name": filename,
                    "contentType": "reference",
                    "contentUrl": reference_content_url,
                }
            )
            attachment_references.append(temporary_id)

        body_html = _teams_message_to_html(message)
        for attachment_id in attachment_references:
            body_html += (
                '<p><attachment id="'
                f'{html.escape(attachment_id, quote=True)}'
                '"></attachment></p>'
            )
        for filename, hosted_content_url in inline_images:
            body_html += (
                '<p><img src="'
                f'{html.escape(hosted_content_url, quote=True)}'
                '" alt="'
                f'{html.escape(filename, quote=True)}'
                '"></p>'
            )
        payload: dict[str, Any] = {
            "body": {"content": body_html, "contentType": "html"},
        }
        if attachments:
            payload["attachments"] = attachments
        if hosted_contents:
            payload["hostedContents"] = hosted_contents
        return payload

    def post_file(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        artifacts: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        target = self._require_allowed_target(channel_target)
        normalized_thread_id = str(thread_id or "").strip() or None
        url = self._post_file_url(target, normalized_thread_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = self._request_with_retries(
                client,
                "POST",
                url,
                json=self._teams_file_message_payload(message, artifacts),
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
            "artifact_count": len(artifacts),
        }
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result

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
                url = f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages"
            else:
                url = (
                    f"{self.api_base_url}/teams/{encoded_team_id}/channels/{encoded_channel_id}/messages/"
                    f"{quote(normalized_thread_id, safe='')}/replies"
                )

        with httpx.Client(timeout=self.timeout_seconds) as client:
            formatted_message = _teams_message_to_html(message)
            response = self._request_with_retries(
                client,
                "POST",
                url,
                json={"body": {"content": formatted_message, "contentType": "html"}},
            )
            payload = response.json()

        envelope = self._to_envelope(
            payload,
            target=target,
            # Historical name: this marks a self-authored Teams status post from
            # this agent session, not an inbound user-authored Graph message.
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

    def add_reaction(
        self,
        channel_target: str,
        message_id: str,
        reaction: str,
        session_id: str,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        normalized_reaction = str(reaction).strip()
        graph_reaction_type = _normalize_graph_teams_reaction_type(normalized_reaction)
        encoded_message_id = quote(message_id, safe="")
        if target.kind == "chat":
            url = (
                f"{self.api_base_url}/chats/{quote(target.chat_id or '', safe='')}/messages/"
                f"{encoded_message_id}/setReaction"
            )
        else:
            url = (
                f"{self.api_base_url}/teams/{quote(target.team_id or '', safe='')}/channels/"
                f"{quote(target.channel_id or '', safe='')}/messages/{encoded_message_id}/setReaction"
            )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            self._request_with_retries(
                client,
                "POST",
                url,
                json={"reactionType": graph_reaction_type},
            )
        result = {
            "status": "reacted",
            "channel_target": target.normalized,
            "session_id": session_id,
            "message_id": message_id,
            "reaction": normalized_reaction,
            "emoji": normalized_reaction,
        }
        if graph_reaction_type != normalized_reaction:
            result["graph_reaction_type"] = graph_reaction_type
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result


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
            if session_id is not None and self._is_self_authored_message(
                candidate, session_id=session_id
            ):
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
    def __init__(
        self,
        store_path: Path,
        *,
        ignored_feedback_store_path: Path | None = None,
        repository: PrFeedbackRepository | None = None,
    ) -> None:
        self.store_path = store_path
        self.store: PrFeedbackRepository = repository or PrFeedbackStore(store_path)
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
        stream_cursors = {
            (stream["provider"], stream["repo"], stream["pr_id"]): self._effective_cursor(
                session_id=session_id,
                stream=stream,
                explicit_cursor=cursor,
            )
            for stream in streams
        }
        ignored_feedback_ids = self._read_ignored_feedback_ids()
        items = [
            PrFeedbackItem.model_validate(pr_feedback_item_payload(item))
            for item in self.store.list_feedback_for_streams(
                streams=streams,
                after=stream_cursors,
                exclude_feedback_ids=ignored_feedback_ids,
                limit=limit,
            )
        ]
        next_cursor = (
            items[-1].created_at.isoformat().replace("+00:00", "Z")
            if items
            else self._max_cursors(stream_cursors.values())
        )
        if persist_session_cursor and session_id is not None:
            self.store.mark_session_streams_read(
                session_id=session_id,
                streams=streams,
                cursor=next_cursor if next_cursor is not None else cursor,
            )
        return ListUnreadFeedbackResponse(feedback=items, next_cursor=next_cursor)

    def _read_store(self) -> dict[str, Any]:
        payload = self.store.read_raw()
        if "feedback" not in payload:
            payload["feedback"] = {}
        return payload

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
                    created_at=_parse_dt(item["creationDate"])
                    if item.get("creationDate")
                    else None,
                    updated_at=_parse_dt(item["creationDate"])
                    if item.get("creationDate")
                    else None,
                    created_by=(
                        str(
                            created_by.get("displayName") or created_by.get("uniqueName") or ""
                        ).strip()
                        or None
                    ),
                    url=(
                        str(item.get("_links", {}).get("web", {}).get("href") or "").strip()
                        or str(item.get("remoteUrl") or item.get("url") or "").strip()
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
                    is_required=item.get("isRequired")
                    if isinstance(item.get("isRequired"), bool)
                    else None,
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
                str(payload.get("_links", {}).get("web", {}).get("href") or "").strip()
                or str(payload.get("remoteUrl") or payload.get("url") or "").strip()
            ),
            source_branch=str(payload.get("sourceRefName", "")).strip() or None,
            target_branch=str(payload.get("targetRefName", "")).strip() or None,
            source_commit=str(payload.get("lastMergeSourceCommit", {}).get("commitId", "")).strip()
            or None,
            target_commit=str(payload.get("lastMergeTargetCommit", {}).get("commitId", "")).strip()
            or None,
            created_by=(
                str(created_by.get("displayName") or created_by.get("uniqueName") or "").strip()
                or None
            ),
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
            raise ValueError(
                f"PR query provider {provider_name!r} is not configured for this gateway instance"
            )
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
            raise ValueError(
                f"PR query provider {provider_name!r} is not configured for this gateway instance"
            )
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
        cache_key = f"{provider_name}:{repo}:{pr_id}:{normalized_key}" if normalized_key else None
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


def _normalize_ignored_feedback_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], set[str], bool]:
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
                normalized_entries.append(
                    {
                        "feedback_id": feedback_id,
                        "ignored_at": now.isoformat().replace("+00:00", "Z"),
                    }
                )
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
        error_context: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "comments": [
                {"parentCommentId": 0, "content": body_text.strip(), "commentType": "text"}
            ],
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
        _raise_azure_devops_pr_publish_error(
            response,
            action="post PR thread comment",
            context=error_context,
        )
        response_payload = response.json()
        thread_id = str(response_payload.get("id", "")).strip() or None
        comments = (
            response_payload.get("comments")
            if isinstance(response_payload.get("comments"), list)
            else []
        )
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
                error_context="top-level PR comment",
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
        inline_comment_ids: list[str] = []
        inline_comment_urls: list[str] = []
        inline_published_feedback_ids: list[str] = []

        with httpx.Client(timeout=self.timeout_seconds) as client:
            total_inline_comments = len(normalized_comments)
            for index, comment in enumerate(normalized_comments, start=1):
                thread_context = _azure_devops_thread_context_for_review_comment(comment)
                error_context = _azure_devops_inline_comment_error_context(
                    comment,
                    index=index,
                    total=total_inline_comments,
                )
                try:
                    comment_result = self._post_thread_comment(
                        client=client,
                        repo=repo,
                        pr_id=pr_id,
                        body_text=comment["body_text"],
                        thread_context=thread_context,
                        error_context=error_context,
                    )
                except httpx.HTTPStatusError as exc:
                    posted_count = len(inline_comment_ids)
                    message = (
                        f"{exc}; Azure DevOps submit_review stopped at {error_context}. "
                        f"Posted {posted_count} of {total_inline_comments} inline comment(s); "
                        "summary comment was not posted."
                    )
                    raise httpx.HTTPStatusError(
                        message,
                        request=exc.request,
                        response=exc.response,
                    ) from exc
                if comment_result["comment_id"] is not None:
                    inline_comment_ids.append(comment_result["comment_id"])
                if comment_result["comment_url"]:
                    inline_comment_urls.append(comment_result["comment_url"])
                inline_published_feedback_ids.extend(comment_result["published_feedback_ids"])

            if normalized_body:
                summary_result = self._post_thread_comment(
                    client=client,
                    repo=repo,
                    pr_id=pr_id,
                    body_text=normalized_body,
                    error_context="review summary comment",
                )
                summary_thread_id = summary_result["thread_id"]
                summary_comment_id = summary_result["comment_id"]
                summary_comment_url = summary_result["comment_url"]
                if summary_comment_id is not None:
                    comment_ids.append(summary_comment_id)
                if summary_comment_url:
                    comment_urls.append(summary_comment_url)
                published_feedback_ids.extend(summary_result["published_feedback_ids"])

            comment_ids.extend(inline_comment_ids)
            comment_urls.extend(inline_comment_urls)
            published_feedback_ids.extend(inline_published_feedback_ids)

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


def _azure_devops_thread_context_for_review_comment(comment: dict[str, Any]) -> dict[str, Any]:
    thread_context: dict[str, Any] = {"filePath": comment["path"]}
    line = comment.get("line")
    if line is None:
        return thread_context

    side = str(comment.get("side") or "RIGHT").strip().upper()
    position_prefix = "leftFile" if side == "LEFT" else "rightFile"
    start_line = comment.get("start_line") or line
    thread_context[f"{position_prefix}Start"] = {"line": start_line}
    thread_context[f"{position_prefix}End"] = {"line": line}
    return thread_context


def _azure_devops_inline_comment_error_context(
    comment: dict[str, Any],
    *,
    index: int,
    total: int,
) -> str:
    parts = [f"inline comment {index}/{total}", f"path={comment['path']}"]
    line = comment.get("line")
    if line is not None:
        parts.append(f"line={line}")
    start_line = comment.get("start_line")
    if start_line is not None and start_line != line:
        parts.append(f"start_line={start_line}")
    side = str(comment.get("side") or "").strip()
    if side:
        parts.append(f"side={side}")
    return " ".join(parts)


def _raise_azure_devops_pr_publish_error(
    response: httpx.Response,
    *,
    action: str,
    context: str | None = None,
) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        details = _azure_devops_error_details(response)
        context_suffix = f" ({context})" if context else ""
        detail_suffix = f": {details}" if details else ""
        message = (
            f"Azure DevOps {action} failed{context_suffix} "
            f"(status {response.status_code}) for url {response.url}{detail_suffix}"
        )
        _LOGGER.warning(
            "Azure DevOps PR publish failed: action=%s status=%s url=%s context=%s error=%s",
            action,
            response.status_code,
            response.url,
            context,
            details or str(exc),
        )
        raise httpx.HTTPStatusError(
            message,
            request=exc.request,
            response=exc.response,
        ) from exc


def _azure_devops_error_details(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:1000] if text else ""

    if not isinstance(payload, dict):
        return ""

    error = payload.get("error")
    if isinstance(error, dict):
        parts: list[str] = []
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
        if code:
            parts.append(f"code={code}")
        if message:
            parts.append(f"message={message}")
        if parts:
            return ", ".join(parts)

    parts = []
    message = str(payload.get("message") or "").strip()
    type_key = str(payload.get("typeKey") or "").strip()
    type_name = str(payload.get("typeName") or "").strip()
    error_code = str(payload.get("errorCode") or "").strip()
    event_id = str(payload.get("eventId") or "").strip()
    if message:
        parts.append(f"message={message}")
    if type_key:
        parts.append(f"typeKey={type_key}")
    elif type_name:
        parts.append(f"typeName={type_name}")
    if error_code:
        parts.append(f"errorCode={error_code}")
    if event_id:
        parts.append(f"eventId={event_id}")
    if parts:
        return ", ".join(parts)
    text = response.text.strip()
    return text[:1000] if text else ""


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

    def post_inline_comment(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        path: str,
        line: int,
        body_text: str,
        start_line: int | None = None,
        side: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        comment: dict[str, Any] = {
            "path": path,
            "line": line,
            "body_text": body_text,
        }
        if start_line is not None:
            comment["start_line"] = start_line
        if side is not None:
            comment["side"] = side
        return self._publish(
            operation="post_inline_comment",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text="",
            comments=[comment],
            event=None,
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
            f"{operation}:{provider_name}:{repo}:{pr_id}:{normalized_key}"
            if normalized_key
            else None
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
        elif operation in {"submit_review", "post_inline_comment"}:
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
            normalized_payload, current_feedback_ids, _ = _normalize_ignored_feedback_payload(
                payload
            )
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
        body_text = _normalize_optional_text(
            raw_comment.get("body_text") or raw_comment.get("body")
        )
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
            normalized_side = side.upper()
            if normalized_side not in {"LEFT", "RIGHT"}:
                raise ValueError(f"PR review comment at index {index} side must be LEFT or RIGHT")
            normalized_comment["side"] = normalized_side
        start_side = _normalize_optional_text(raw_comment.get("start_side"))
        if start_side is not None:
            normalized_start_side = start_side.upper()
            if normalized_start_side not in {"LEFT", "RIGHT"}:
                raise ValueError(
                    f"PR review comment at index {index} start_side must be LEFT or RIGHT"
                )
            normalized_comment["start_side"] = normalized_start_side
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
        raise ValueError("PR review event must be one of: COMMENT, APPROVE, REQUEST_CHANGES")
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
