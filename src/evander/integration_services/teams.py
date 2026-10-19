"""Microsoft Teams targets, media, and Graph service."""

from __future__ import annotations
import mimetypes
import time
import html
from uuid import uuid4
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Literal, Protocol
from urllib.parse import quote, unquote, urlparse, urlunparse
import httpx
from evander.models import (
    InlineMediaRef,
    SessionManifest,
    TeamsMessageEnvelope,
)
from evander.inline_media import (
    InlineMediaDownload,
    inline_media_download_from_embedded_payload,
)
from evander.repositories.records import TeamsRecordsRepository, teams_records_with_outbound
from evander.text_formatting import render_markdownish_text_to_html
from evander.teams_outbound import TeamsOutboundReceipt, TeamsOutboundEditState

from .common import (
    _sanitize_attachment_metadata,
)


_GRAPH_TEAMS_REACTION_TYPE_BY_VALUE = {
    "like": "👍",
    "thumbsup": "👍",
    "thumbs_up": "👍",
    "thumbs-up": "👍",
    "heart": "❤️",
    "love": "❤️",
    "laugh": "😆",
    "haha": "😆",
    "joy": "😆",
    "surprised": "😮",
    "wow": "😮",
    "eyes": "👀",
    "sad": "😢",
    "cry": "😢",
    "angry": "😡",
    "mad": "😡",
}


_GRAPH_TEAMS_SUPPORTED_REACTION_ALIASES = ", ".join(_GRAPH_TEAMS_REACTION_TYPE_BY_VALUE)


@dataclass
class _TeamsGraphPaginationBudget:
    page_cap: int
    deadline_seconds: float
    started_at: float
    pages_read: int = 0

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_seconds - (time.monotonic() - self.started_at))

    @property
    def deadline_monotonic(self) -> float:
        return self.started_at + self.deadline_seconds

    def can_request_page(self) -> bool:
        return self.pages_read < self.page_cap and self.remaining_seconds() > 0

    def record_page(self) -> None:
        self.pages_read += 1


def _normalize_graph_teams_reaction_type(reaction: str) -> str:
    normalized = str(reaction).strip()
    if not normalized:
        raise ValueError("Teams reaction must be a non-empty string.")
    if not normalized.isascii():
        return normalized
    graph_reaction_type = _GRAPH_TEAMS_REACTION_TYPE_BY_VALUE.get(normalized.casefold())
    if graph_reaction_type is not None:
        return graph_reaction_type
    raise ValueError(
        f"Unsupported Teams reaction: {normalized}. Supported aliases include "
        f"{_GRAPH_TEAMS_SUPPORTED_REACTION_ALIASES}."
    )


def _teams_message_to_html(message: str) -> str:
    rendered = render_markdownish_text_to_html(message)
    return rendered or "<p></p>"


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
            candidates.extend([attachment_ref.get("contentUrl"), attachment_ref.get("content_url")])
            ref_metadata = attachment_ref.get("metadata")
            if isinstance(ref_metadata, dict):
                candidates.extend([ref_metadata.get("contentUrl"), ref_metadata.get("content_url")])
    for candidate in candidates:
        normalized = _quote_teams_reference_content_url(candidate)
        if normalized:
            return normalized
    return None


def _artifact_control_plane_download_url(artifact: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        artifact.get("download_url"),
        artifact.get("public_url"),
    ]
    metadata = artifact.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("download_url"), metadata.get("public_url")])
    for candidate in candidates:
        normalized = _quote_teams_reference_content_url(candidate)
        if normalized:
            return normalized
    return None


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
        mentions: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def ensure_one_on_one_chat(self, user_ref: str) -> str: ...

    def update_message(
        self,
        session_id: str,
        channel_target: str,
        message_id: str,
        message: str,
        thread_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]: ...

    def list_attachments(
        self, message_id: str, *, channel_target: str | None = None
    ) -> list[dict[str, Any]]: ...

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        *,
        channel_target: str | None = None,
        max_bytes: int | None = None,
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
            mime_type = (
                str(
                    item.get("mime_type")
                    or item.get("content_type")
                    or item.get("contentType")
                    or ""
                )
                .split(";", 1)[0]
                .strip()
                or None
            )
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
                        str(item.get("contentUrl") or item.get("content_url") or "").strip() or None
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
    kind: Literal["channel", "chat", "user"]
    normalized: str
    team_id: str | None = None
    channel_id: str | None = None
    chat_id: str | None = None
    user_ref: str | None = None


@dataclass(frozen=True)
class _TeamsMentionTarget:
    user_ref: str
    user_id: str
    mention_text: str


def _sorted_teams_messages(messages: dict[str, TeamsMessageEnvelope]) -> list[TeamsMessageEnvelope]:
    return sorted(messages.values(), key=lambda item: (item.created_at, item.cursor))


def _put_teams_messages(
    message_store: TeamsRecordsRepository, messages: dict[str, TeamsMessageEnvelope]
) -> None:
    if not messages:
        return
    put_many = getattr(message_store, "put_many", None)
    if callable(put_many):
        put_many(messages)
        return
    for cursor, message in messages.items():
        message_store.put(cursor, message)


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


_TEAMS_TARGET_ERROR = "expected 'team-id/channel-id', 'chat:<chat-id>', or 'user:<user-id-or-upn>'."


def normalize_teams_user_target(target: str) -> str:
    normalized = str(target or "").strip().strip("/")
    user_ref = normalized.removeprefix("user:").strip()
    if not user_ref:
        raise ValueError(f"Invalid Teams user target {target!r}: {_TEAMS_TARGET_ERROR}")
    if "/" in user_ref or any(character.isspace() for character in user_ref):
        raise ValueError(f"Invalid Teams user target {target!r}: {_TEAMS_TARGET_ERROR}")
    return f"user:{user_ref.casefold()}"


def normalize_teams_mention_refs(value: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_ref in value or []:
        text = str(raw_ref or "").strip()
        if not text:
            continue
        user_target = normalize_teams_user_target(text)
        if user_target in seen:
            continue
        seen.add(user_target)
        normalized.append(user_target)
    return normalized


def normalize_teams_target(target: str) -> str:
    normalized = str(target or "").strip().strip("/")
    if normalized.startswith("chat:"):
        chat_id = normalized.removeprefix("chat:").strip()
        if not chat_id:
            raise ValueError(f"Invalid Teams target {target!r}: {_TEAMS_TARGET_ERROR}")
        return f"chat:{chat_id}"
    if normalized.startswith("user:"):
        return normalize_teams_user_target(normalized)
    team_id, separator, channel_id = normalized.partition("/")
    if not separator or not team_id or not channel_id:
        raise ValueError(f"Invalid Teams target {target!r}: {_TEAMS_TARGET_ERROR}")
    return f"{team_id}/{channel_id}"


def _normalize_teams_allowlist_targets(
    value: list[str] | None,
    *,
    field_name: str,
    allowed_kind: str | None = None,
    forbid_user_targets: bool = True,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_target in value or []:
        text = str(raw_target).strip()
        if not text:
            continue
        target = normalize_teams_target(text)
        parsed_target = parse_teams_target(target)
        if allowed_kind is not None and parsed_target.kind != allowed_kind:
            if allowed_kind == "chat":
                raise ValueError(
                    f"{field_name} accepts chat targets only; use teams_channel_allowlist "
                    "for channel targets and teams_user_allowlist for user targets"
                )
            raise ValueError(f"{field_name} accepts {allowed_kind} targets only")
        if forbid_user_targets and parsed_target.kind == "user":
            raise ValueError(
                f"{field_name} accepts channel/chat targets only; use teams_user_allowlist for user targets"
            )
        if target in seen:
            continue
        seen.add(target)
        normalized.append(target)
    return normalized


def normalize_teams_channel_targets(value: list[str] | None) -> list[str]:
    return _normalize_teams_allowlist_targets(value, field_name="teams_channel_allowlist")


def normalize_teams_chat_targets(value: list[str] | None) -> list[str]:
    return _normalize_teams_allowlist_targets(
        value,
        field_name="teams_chat_allowlist",
        allowed_kind="chat",
    )


def _normalize_teams_or_target_refs(
    value: list[str] | None,
    *,
    field_name: str,
    allowed_kind: str | None = None,
    forbid_user_targets: bool = True,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_target in value or []:
        text = str(raw_target).strip()
        if not text:
            continue
        try:
            target = normalize_teams_target(text)
        except ValueError:
            target = text
        else:
            parsed_target = parse_teams_target(target)
            if allowed_kind is not None and parsed_target.kind != allowed_kind:
                if allowed_kind == "chat":
                    raise ValueError(
                        f"{field_name} accepts chat targets only; use teams_channel_allowlist "
                        "for channel targets and teams_user_allowlist for user targets"
                    )
                raise ValueError(f"{field_name} accepts {allowed_kind} targets only")
            if forbid_user_targets and parsed_target.kind == "user":
                raise ValueError(
                    f"{field_name} accepts channel/chat targets only; use teams_user_allowlist "
                    "for user targets"
                )
        if target in seen:
            continue
        seen.add(target)
        normalized.append(target)
    return normalized


def normalize_teams_channel_or_target_refs(value: list[str] | None) -> list[str]:
    return _normalize_teams_or_target_refs(value, field_name="teams_channel_allowlist")


def normalize_teams_chat_or_target_refs(value: list[str] | None) -> list[str]:
    return _normalize_teams_or_target_refs(
        value,
        field_name="teams_chat_allowlist",
        allowed_kind="chat",
    )


def normalize_teams_user_or_target_refs(value: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_target in value or []:
        text = str(raw_target).strip()
        if not text:
            continue
        target = (
            normalize_teams_user_target(text) if text.startswith("user:") or "@" in text else text
        )
        if target in seen:
            continue
        seen.add(target)
        normalized.append(target)
    return normalized


def effective_teams_channel_targets(
    session: SessionManifest,
    *,
    skip_user_targets: bool = False,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_target in list(session.teams_channel_allowlist or []) + list(
        session.teams_chat_allowlist or []
    ):
        text = str(raw_target).strip()
        if not text:
            continue
        target = normalize_teams_target(text)
        parsed_target = parse_teams_target(target)
        if parsed_target.kind == "user":
            if skip_user_targets:
                continue
            raise ValueError(
                "effective_teams_channel_targets does not accept user targets; use "
                "teams_user_allowlist for user targets"
            )
        if target in seen:
            continue
        seen.add(target)
        normalized.append(target)
    return normalized


def parse_teams_target(target: str) -> TeamsTarget:
    normalized = normalize_teams_target(target)
    if normalized.startswith("chat:"):
        return TeamsTarget(
            kind="chat", normalized=normalized, chat_id=normalized.removeprefix("chat:")
        )
    if normalized.startswith("user:"):
        return TeamsTarget(
            kind="user",
            normalized=normalized,
            user_ref=normalized.removeprefix("user:"),
        )
    team_id, _, channel_id = normalized.partition("/")
    return TeamsTarget(
        kind="channel",
        normalized=normalized,
        team_id=team_id,
        channel_id=channel_id,
    )


def resolve_teams_send_target(service: TeamsService, channel_target: str) -> str:
    parsed_target = parse_teams_target(channel_target)
    if parsed_target.kind != "user":
        return parsed_target.normalized
    ensure_chat = getattr(service, "ensure_one_on_one_chat", None)
    if not callable(ensure_chat):
        raise ValueError("Configured Teams adapter does not support user targets.")
    return normalize_teams_target(str(ensure_chat(parsed_target.user_ref or "")))


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


class ManagedTeamsService:
    """Marker for services rebuilt using directory-scoped provider configuration."""


class StubTeamsService(ManagedTeamsService):
    def __init__(
        self,
        message_store: TeamsRecordsRepository,
        *,
        allowed_user_targets_provider: Callable[[], list[str]] = lambda: [],
        read_reaction_emoji: str = "👀",
    ) -> None:
        self.message_store = teams_records_with_outbound(message_store)
        self.allowed_user_targets_provider = allowed_user_targets_provider
        self.read_reaction_emoji = read_reaction_emoji

    def _allowed_user_targets(self) -> set[str]:
        return {
            normalize_teams_user_target(target)
            for target in self.allowed_user_targets_provider()
            if str(target or "").strip()
        }

    def ensure_one_on_one_chat(self, user_ref: str) -> str:
        user_target = normalize_teams_user_target(user_ref)
        if user_target not in self._allowed_user_targets():
            raise ValueError(f"Teams user target is not allowlisted: {user_target}")
        stub_chat_id = user_target.removeprefix("user:").replace("@", "_at_")
        return f"chat:stub-user-{stub_chat_id}"

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
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._post_message(message, channel_target, session_id, thread_id, mentions)

    def _post_message(
        self, message, channel_target, session_id, thread_id=None, mentions=None, artifacts=None
    ):
        target = parse_teams_target(channel_target)
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before posting.")
        normalized_mentions = normalize_teams_mention_refs(mentions)
        normalized_thread_id = str(thread_id or "").strip() or None
        if target.kind == "chat" and normalized_thread_id is not None:
            raise ValueError("Teams chat targets do not support thread replies.")
        message_id = "stub-" + uuid4().hex
        effective_thread = (
            (normalized_thread_id or message_id) if target.kind == "channel" else None
        )
        prefix = ""
        if normalized_mentions:
            prefix = (
                "<p>"
                + " ".join(
                    f'<at id="{i}">{html.escape(ref.removeprefix("user:"))}</at>'
                    for i, ref in enumerate(normalized_mentions)
                )
                + "</p>"
            )
        text_html = _teams_message_to_html(message)
        attachments = []
        suffix = ""
        for index, artifact in enumerate(artifacts or []):
            attachment = {
                "id": f"stub-attachment-{index}",
                "name": str(artifact.get("filename") or artifact.get("name") or "file"),
            }
            attachments.append(attachment)
            suffix += f'<p><attachment id="{attachment["id"]}"></attachment></p>'
        receipt = TeamsOutboundReceipt(
            channel_target=target.normalized,
            message_id=message_id,
            session_id=session_id,
            thread_id=effective_thread,
            author={"kind": "user", "id": "stub-sender"},
            body_prefix=prefix,
            body_suffix=suffix,
            protected={"mentions": normalized_mentions, "attachments": attachments},
        )
        self.message_store.create_outbound_receipt(
            receipt,
            TeamsOutboundEditState(text_html=text_html, body_html=prefix + text_html + suffix),
        )
        envelope = TeamsMessageEnvelope(
            cursor=f"{target.normalized}/{message_id}",
            message_id=message_id,
            author_id="stub-sender",
            team_id=target.team_id or "",
            channel_id=target.channel_id or "",
            chat_id=target.chat_id,
            thread_id=effective_thread,
            content=prefix + text_html + suffix,
            metadata={
                "source": "teams_graph_api_status",
                "backend": "stub",
                "target": target.normalized,
                "session_id": session_id,
                "mentions": normalized_mentions,
                "attachments": attachments,
            },
        )
        self.message_store.put(envelope.cursor, envelope)
        result: dict[str, Any] = {
            "status": "stubbed",
            "channel_target": target.normalized,
            "thread_id": normalized_thread_id,
            "session_id": session_id,
            "message_preview": message[:120],
            "message_id": message_id,
            "message_revision": 1,
            "web_url": None,
        }
        if target.kind == "chat":
            result["chat_id"] = target.chat_id
        else:
            result["team_id"] = target.team_id
            result["channel_id"] = target.channel_id
        return result

    def update_message(
        self,
        session_id: str,
        channel_target: str,
        message_id: str,
        message: str,
        thread_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        if target.kind == "user":
            raise ValueError("Use an existing Teams channel or chat target")
        store = self.message_store
        with store.lock_outbound_message(target.normalized, message_id):
            receipt = store.get_outbound_receipt(target.normalized, message_id)
            if receipt is None or receipt.session_id != session_id:
                raise ValueError("No outbound ownership receipt for this session and conversation")
            if (target.kind == "chat" and thread_id is not None) or (
                thread_id is not None and thread_id != receipt.thread_id
            ):
                raise ValueError("thread_id does not match the recorded Teams message")
            state = store.get_outbound_edit_state(target.normalized, message_id)
            if state is None or not receipt.editable:
                raise ValueError("Teams message is uneditable")
            text_html = _teams_message_to_html(message)
            status = "unchanged"
            if text_html != state.text_html:
                if expected_revision is not None and expected_revision != state.revision:
                    raise ValueError(
                        f"Stale Teams message revision; current revision is {state.revision}"
                    )
                state = TeamsOutboundEditState(
                    revision=state.revision + 1,
                    text_html=text_html,
                    body_html=receipt.body_prefix + text_html + receipt.body_suffix,
                )
                store.save_outbound_edit_state(target.normalized, message_id, state)
                envelope = store.get(f"{target.normalized}/{message_id}")
                if envelope is not None:
                    store.put(
                        envelope.cursor, envelope.model_copy(update={"content": state.body_html})
                    )
                status = "stubbed"
            return {
                "success": True,
                "status": status,
                "session_id": session_id,
                "channel_target": target.normalized,
                "message_id": message_id,
                "thread_id": receipt.thread_id,
                "message_revision": state.revision,
                "web_url": receipt.web_url,
            }

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
        self,
        message_id: str,
        attachment_id: str,
        *,
        channel_target: str | None = None,
        max_bytes: int | None = None,
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
        result = self._post_message(
            message, target.normalized, session_id, thread_id, artifacts=artifacts
        )
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
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before adding reactions.")
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



def __getattr__(name: str):
    if name == "GraphTeamsService":
        from evander.optional_integrations import require_teams_graph_module

        return require_teams_graph_module("evander_teams_graph_addon.service").GraphTeamsService
    raise AttributeError(name)
