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
from typing import Any, Callable, Iterator, Literal, Mapping, Protocol
from urllib.parse import quote, unquote, urlparse, urlunparse

import httpx
from evander.ado_mentions import (
    AdoResolvedMention,
    AdoMentionResolver,
    normalize_ado_mention_refs,
    render_ado_mentions_prefix,
    resolve_ado_mentions,
)
from evander.models import (
    AdoIdentitySummary,
    AdoPrCommentActivityResponse,
    AdoPrCommitActivityResponse,
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
    PullRequestWorkItem,
    PullRequestWorkItemsResponse,
    PrFeedbackItem,
    ResolveAdoIdentityResponse,
    SessionManifest,
    TeamsMessageEnvelope,
    UpdatePullRequestResponse,
)
from evander.issue_owner import assigned_to_identity
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.communication_artifact_links import (
    CommunicationArtifactLinkError,
    issue_communication_persona_avatar_link,
)
from evander.discord_webhooks import (
    CommunicationPersonaAvatarStore,
    DiscordWebhookCredential,
    DiscordWebhookCredentialStore,
    discord_webhook_credential_key,
)
from evander.outbound_identity import OutboundIdentity
from evander.repositories.cursors import DiscordInboxCursorRepository
from evander.inline_media import (
    InlineMediaDownload,
    inline_media_download_from_embedded_payload,
    sanitize_inline_media_metadata,
)
from evander.email.providers import (
    EmailAttachmentContent,
    EmailAttachmentMetadata,
    EmailDraft,
    EmailDraftAttachment,
    EmailMessageDetail,
    EmailMessagePage,
    EmailMessageSearchQuery,
    EmailMessageSummary,
    EmailProvider,
    EmailSendResult,
)
from evander.repositories.records import DiscordRecordsRepository, TeamsRecordsRepository
from evander.text_formatting import render_markdownish_text_to_html
from evander.pr_feedback.providers import (
    _ado_line_from_context,
    _ado_should_skip_comment,
    _ado_thread_resolved,
    pr_feedback_item_payload,
)
from evander.pr_feedback.store import PrFeedbackRepository, PrFeedbackStore, _stream_key

_LOGGER = logging.getLogger(__name__)
_IGNORED_FEEDBACK_RETENTION = timedelta(days=90)
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


def _parse_optional_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    return _parse_dt(text)


def _iso_z(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _datetime_in_range(value: datetime | None, *, since: datetime, until: datetime | None) -> bool:
    if value is None:
        return False
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    start = since.astimezone(UTC) if since.tzinfo is not None else since.replace(tzinfo=UTC)
    if normalized < start:
        return False
    if until is not None:
        end = until.astimezone(UTC) if until.tzinfo is not None else until.replace(tzinfo=UTC)
        if normalized > end:
            return False
    return True


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _identity_property(identity: Mapping[str, Any], key: str) -> str | None:
    properties = identity.get("properties")
    if not isinstance(properties, Mapping):
        return None
    value = properties.get(key)
    if isinstance(value, Mapping):
        value = value.get("$value")
    return _text_or_none(value)


def _identity_email(identity: Mapping[str, Any]) -> str | None:
    for key in ("Mail", "SignInAddress", "Account"):
        value = _identity_property(identity, key)
        if value and "@" in value:
            return value
    for key in ("uniqueName", "mailAddress", "email"):
        value = _text_or_none(identity.get(key))
        if value and "@" in value:
            return value
    return None


def _ado_identity_summary(query: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    display_name = (
        _text_or_none(identity.get("providerDisplayName"))
        or _text_or_none(identity.get("displayName"))
        or _text_or_none(identity.get("customDisplayName"))
        or _text_or_none(identity.get("uniqueName"))
    )
    unique_name = (
        _text_or_none(identity.get("uniqueName"))
        or _identity_property(identity, "Account")
        or _identity_property(identity, "Mail")
        or _identity_property(identity, "SignInAddress")
    )
    descriptor = _text_or_none(identity.get("descriptor")) or _text_or_none(
        identity.get("subjectDescriptor")
    )
    return {
        "id": _text_or_none(identity.get("id")),
        "descriptor": descriptor,
        "display_name": display_name or query,
        "unique_name": unique_name,
        "email": _identity_email(identity),
        "raw": dict(identity),
    }


def _unique_positive_ints(values: Iterator[Any]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            candidate = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if candidate <= 0 or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _pull_request_work_item_assignee(value: Any) -> AdoIdentitySummary | None:
    identity = assigned_to_identity(value)
    if identity is None:
        return None
    payload = dict(identity)
    if isinstance(value, Mapping):
        payload["raw"] = dict(value)
    return AdoIdentitySummary.model_validate(payload)


def _pull_request_work_item_from_reference(
    *,
    reference: Mapping[str, Any],
    details: Mapping[str, Any] | None,
) -> PullRequestWorkItem:
    work_item_id = str(reference.get("id") or "").strip()
    detail_payload = dict(details) if isinstance(details, Mapping) else {}
    fields = detail_payload.get("fields")
    fields = fields if isinstance(fields, Mapping) else {}
    web_links = detail_payload.get("_links")
    web_links = web_links if isinstance(web_links, Mapping) else {}
    web_link = web_links.get("web")
    web_link = web_link if isinstance(web_link, Mapping) else {}
    return PullRequestWorkItem(
        work_item_id=work_item_id,
        title=_text_or_none(fields.get("System.Title")),
        url=(
            _text_or_none(web_link.get("href"))
            or _text_or_none(reference.get("url"))
            or _text_or_none(detail_payload.get("url"))
        ),
        assignee=_pull_request_work_item_assignee(fields.get("System.AssignedTo")),
        raw={"reference": dict(reference), "work_item": detail_payload},
    )


def _identity_match_values(value: Any) -> set[str]:
    values: set[str] = set()

    def add(candidate: Any) -> None:
        text = str(candidate or "").strip()
        if text:
            values.add(text.casefold())

    if isinstance(value, Mapping):
        for key in (
            "id",
            "descriptor",
            "subjectDescriptor",
            "displayName",
            "display_name",
            "providerDisplayName",
            "customDisplayName",
            "uniqueName",
            "unique_name",
            "email",
            "mailAddress",
            "name",
        ):
            add(value.get(key))
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            for key in ("Account", "Mail", "SignInAddress"):
                raw = properties.get(key)
                if isinstance(raw, Mapping):
                    raw = raw.get("$value")
                add(raw)
    else:
        add(value)
    return values


def _identity_matches(query: str | None, value: Any) -> bool:
    text = str(query or "").strip()
    if not text:
        return True
    expected = text.casefold()
    return expected in _identity_match_values(value)


def _snippet(text: str | None, *, limit: int = 240) -> str | None:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "..."


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

    def list_messages(
        self,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        cursor: str | None,
        limit: int,
    ) -> EmailMessagePage:
        return self.provider.list_messages(
            mailbox_id=mailbox_id,
            folder=folder,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        return self.provider.get_message(mailbox_id=mailbox_id, message_id=message_id)

    def list_attachments(self, mailbox_id: str, message_id: str) -> list[EmailAttachmentMetadata]:
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

    def send_draft(self, mailbox_id: str, draft_id: str) -> EmailSendResult:
        return self.provider.send_draft(mailbox_id=mailbox_id, draft_id=draft_id)

    def send_from_draft(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        mailbox_id = str(payload.get("mailbox_id") or "").strip()
        draft_id = str(payload.get("draft_id") or payload.get("provider_draft_id") or "").strip()
        if not mailbox_id:
            raise ValueError("email send payload must include mailbox_id")
        if not draft_id:
            raise ValueError("email send payload must include draft_id")
        result = self.send_draft(mailbox_id=mailbox_id, draft_id=draft_id)
        output = result.model_dump(mode="json")
        output["request_id"] = request_id
        return output


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
        mentions: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def ensure_one_on_one_chat(self, user_ref: str) -> str: ...

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
        bot_token_env: str = "DISCORD_BOT_TOKEN",
        webhook_store_path: str | Path | None = None,
        runtime_dir: str | Path | None = None,
        persona_avatar_public_base_url: str | None = None,
        persona_avatar_secret_env: str = "EVANDER_COMMUNICATION_ARTIFACT_LINK_SECRET",
        persona_avatar_link_ttl_seconds: int = 7 * 24 * 60 * 60,
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
        self.bot_token_env = bot_token_env
        self.webhook_store = (
            DiscordWebhookCredentialStore(webhook_store_path)
            if webhook_store_path is not None
            else None
        )
        self.persona_avatar_store = (
            CommunicationPersonaAvatarStore(runtime_dir) if runtime_dir is not None else None
        )
        self.persona_avatar_public_base_url = persona_avatar_public_base_url
        self.persona_avatar_secret_env = persona_avatar_secret_env
        self.persona_avatar_link_ttl_seconds = persona_avatar_link_ttl_seconds

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
                    headers=self._headers() if headers is None else headers,
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
        fetched_commands: dict[str, DiscordCommandEnvelope] = {}
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
                "persona_avatar_applied": bool(identity is not None and identity.avatar is not None),
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
            return {"configured": False, "channel_id": channel_id}
        credential = self.webhook_store.get(
            api_base_url=self.api_base_url,
            bot_token_env=self.bot_token_env,
            channel_id=channel_id,
        )
        return (
            credential.safe_status()
            if credential is not None
            else {"configured": False, "channel_id": channel_id}
        )

    def provision_webhook(self, channel_id: str, *, force: bool = False) -> dict[str, Any]:
        if self.webhook_store is None:
            raise RuntimeError("Discord webhook credential storage is not configured")
        self._require_allowed_channel_id(channel_id)
        with self.webhook_store.provision_lock():
            existing = self.webhook_store.get(
                api_base_url=self.api_base_url,
                bot_token_env=self.bot_token_env,
                channel_id=channel_id,
            )
            if existing is not None and not force:
                return existing.safe_status()
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.api_base_url}/channels/{channel_id}/webhooks",
                    headers=self._headers(),
                    json={"name": "Evander"},
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
                ),
                channel_id=channel_id,
                api_base_url=self.api_base_url,
                bot_token_env=self.bot_token_env,
                webhook_id=str(payload.get("id") or ""),
                token=str(payload.get("token") or ""),
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
            return result

    def _webhook_credential(self, channel_id: str) -> DiscordWebhookCredential:
        if self.webhook_store is None:
            raise RuntimeError("Discord webhook credential storage is not configured")
        credential = self.webhook_store.get(
            api_base_url=self.api_base_url,
            bot_token_env=self.bot_token_env,
            channel_id=channel_id,
        )
        if credential is None:
            self.provision_webhook(channel_id)
            credential = self.webhook_store.get(
                api_base_url=self.api_base_url,
                bot_token_env=self.bot_token_env,
                channel_id=channel_id,
            )
        if credential is None:
            raise RuntimeError("Discord webhook provisioning did not return a credential")
        return credential

    def _persona_avatar_url(self, identity: OutboundIdentity | None) -> str | None:
        if identity is None or identity.avatar is None or self.persona_avatar_store is None:
            return None
        self.persona_avatar_store.put(identity.avatar)
        link = issue_communication_persona_avatar_link(
            sha256=identity.avatar.sha256,
            public_base_url=self.persona_avatar_public_base_url,
            secret_env=self.persona_avatar_secret_env,
            ttl_seconds=self.persona_avatar_link_ttl_seconds,
        )
        return link.url

    def _webhook_payload(
        self, message: str, identity: OutboundIdentity | None
    ) -> tuple[dict[str, Any], OutboundIdentity | None, str | None]:
        payload: dict[str, Any] = {"content": message}
        if identity is None:
            return payload, None, None
        payload["username"] = identity.display_name
        fallback_reason = None
        applied_identity = identity
        try:
            avatar_url = self._persona_avatar_url(identity)
        except (CommunicationArtifactLinkError, OSError, ValueError) as exc:
            avatar_url = None
            fallback_reason = f"persona_avatar_unavailable:{type(exc).__name__}"
            applied_identity = OutboundIdentity(
                display_name=identity.display_name,
                source=identity.source,
            )
        if avatar_url:
            payload["avatar_url"] = avatar_url
        return payload, applied_identity, fallback_reason

    def _execute_webhook(
        self,
        *,
        credential: DiscordWebhookCredential,
        payload: dict[str, Any],
        files: Any = None,
    ) -> dict[str, Any]:
        url = (
            f"{self.api_base_url}/webhooks/{credential.webhook_id}/{credential.token}"
            "?wait=true"
        )
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
            self.webhook_store.put(
                credential.model_copy(
                    update={
                        "updated_at": now,
                        "last_verified_at": now,
                        "last_error": None,
                    }
                )
            )
        return response.json()

    def _safe_webhook_request(
        self, credential: DiscordWebhookCredential
    ) -> httpx.Request:
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
        self.webhook_store.put(
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
        files: Any = None,
    ) -> dict[str, Any]:
        credential = self._webhook_credential(channel_id)
        try:
            return self._execute_webhook(
                credential=credential,
                payload=payload,
                files=files,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or self.webhook_store is None:
                raise
            self.webhook_store.delete(credential)
            return self._execute_webhook(
                credential=self._webhook_credential(channel_id),
                payload=payload,
                files=files,
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
        if use_resolved_identity and identity is not None:
            payload, applied_identity, avatar_fallback = self._webhook_payload(message, identity)
            try:
                response_payload = self._execute_identity_webhook(
                    channel_id=channel_id,
                    payload=payload,
                    files=files,
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
    ) -> dict[str, Any]:
        return self._post_file(
            message,
            channel_id,
            session_id,
            artifacts,
            identity=identity,
            use_resolved_identity=True,
            identity_status=identity_status,
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
    ) -> dict[str, Any]:
        self._require_allowed_channel_id(channel_id)
        if use_resolved_identity and identity is not None:
            webhook_payload, applied_identity, avatar_fallback = self._webhook_payload(
                message, identity
            )
            try:
                payload = self._execute_identity_webhook(
                    channel_id=channel_id,
                    payload=webhook_payload,
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
    ) -> dict[str, Any]:
        return self._post_status(
            message,
            channel_id,
            session_id,
            identity=identity,
            use_resolved_identity=True,
            identity_status=identity_status,
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


class StubTeamsService:
    def __init__(
        self,
        message_store: TeamsRecordsRepository,
        *,
        allowed_user_targets_provider: Callable[[], list[str]] = lambda: [],
        read_reaction_emoji: str = "👀",
    ) -> None:
        self.message_store = message_store
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
        target = parse_teams_target(channel_target)
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before posting.")
        normalize_teams_mention_refs(mentions)
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
        allowed_user_targets_provider: Callable[[], list[str]] = lambda: [],
        user_chat_cache: dict[str, str] | None = None,
        user_chat_cache_namespace: str = "default",
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
        self.allowed_user_targets_provider = allowed_user_targets_provider
        self.user_chat_cache = user_chat_cache if user_chat_cache is not None else {}
        self.user_chat_cache_namespace = str(user_chat_cache_namespace or "default")
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

    def _allowed_user_targets(self) -> list[str]:
        return sorted(
            {
                normalize_teams_user_target(target)
                for target in self.allowed_user_targets_provider()
                if str(target or "").strip()
            }
        )

    def _user_chat_cache_key(self, user_target: str) -> str:
        normalized_user_target = normalize_teams_user_target(user_target)
        return f"{self.user_chat_cache_namespace}:{normalized_user_target}"

    def _user_id_cache_key(self, user_ref: str) -> str:
        normalized_user_target = normalize_teams_user_target(user_ref)
        return f"{self.user_chat_cache_namespace}:user-id:{normalized_user_target}"

    def _current_user_cache_key(self) -> str:
        return f"{self.user_chat_cache_namespace}:me"

    @staticmethod
    def _graph_user_bind_url(api_base_url: str, user_ref: str) -> str:
        escaped_ref = str(user_ref).replace("'", "''")
        return f"{api_base_url}/users('{escaped_ref}')"

    @staticmethod
    def _graph_user_lookup_url(api_base_url: str, user_ref: str) -> str:
        return f"{api_base_url}/users/{quote(user_ref, safe='')}"

    @staticmethod
    def _graph_user_ref(user_ref: str) -> str:
        normalized = str(user_ref or "").strip().strip("/")
        if normalized.startswith("user:"):
            normalized = normalized.removeprefix("user:").strip()
        if (
            not normalized
            or "/" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError(f"Invalid Teams user target {user_ref!r}: {_TEAMS_TARGET_ERROR}")
        return normalized

    @staticmethod
    def _graph_user_payload_id(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        user_id = str(payload.get("id") or "").strip()
        return user_id or None

    @staticmethod
    def _graph_user_payload_mention_text(payload: object, fallback: str) -> str:
        if not isinstance(payload, dict):
            return fallback
        for key in ("displayName", "mail", "userPrincipalName", "id"):
            text = str(payload.get(key) or "").strip()
            if text:
                return text
        return fallback

    def _graph_user_payload_mention(
        self, payload: object, *, user_ref: str
    ) -> _TeamsMentionTarget | None:
        user_id = self._graph_user_payload_id(payload)
        if not user_id:
            return None
        self.user_chat_cache[self._user_id_cache_key(user_ref)] = user_id
        return _TeamsMentionTarget(
            user_ref=user_ref,
            user_id=user_id,
            mention_text=self._graph_user_payload_mention_text(payload, user_ref),
        )

    def resolve_user_id(self, client: httpx.Client, user_ref: str) -> str | None:
        normalized_ref = self._graph_user_ref(user_ref)
        cache_key = self._user_id_cache_key(normalized_ref)
        cached = self.user_chat_cache.get(cache_key)
        if cached:
            return cached

        params = {"$select": "id,userPrincipalName,mail"}
        try:
            response = self._request_with_retries(
                client,
                "GET",
                self._graph_user_lookup_url(self.api_base_url, normalized_ref),
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or "@" not in normalized_ref:
                raise
        else:
            user_id = self._graph_user_payload_id(response.json())
            if user_id:
                self.user_chat_cache[cache_key] = user_id
                return user_id

        if "@" not in normalized_ref:
            return None

        escaped_ref = normalized_ref.replace("'", "''")
        response = self._request_with_retries(
            client,
            "GET",
            f"{self.api_base_url}/users",
            params={
                **params,
                "$filter": (f"mail eq '{escaped_ref}' or userPrincipalName eq '{escaped_ref}'"),
                "$top": 1,
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        for item in payload.get("value", []):
            user_id = self._graph_user_payload_id(item)
            if user_id:
                self.user_chat_cache[cache_key] = user_id
                return user_id
        return None

    def resolve_user_mention(
        self, client: httpx.Client, user_ref: str
    ) -> _TeamsMentionTarget | None:
        normalized_ref = self._graph_user_ref(user_ref)
        params = {"$select": "id,displayName,userPrincipalName,mail"}
        try:
            response = self._request_with_retries(
                client,
                "GET",
                self._graph_user_lookup_url(self.api_base_url, normalized_ref),
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or "@" not in normalized_ref:
                raise
        else:
            mention = self._graph_user_payload_mention(
                response.json(),
                user_ref=normalized_ref,
            )
            if mention is not None:
                return mention

        if "@" not in normalized_ref:
            return None

        escaped_ref = normalized_ref.replace("'", "''")
        response = self._request_with_retries(
            client,
            "GET",
            f"{self.api_base_url}/users",
            params={
                **params,
                "$filter": (f"mail eq '{escaped_ref}' or userPrincipalName eq '{escaped_ref}'"),
                "$top": 1,
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        for item in payload.get("value", []):
            mention = self._graph_user_payload_mention(item, user_ref=normalized_ref)
            if mention is not None:
                return mention
        return None

    def _require_allowed_user_target(self, user_ref: str) -> str:
        normalized = normalize_teams_user_target(user_ref)
        if normalized not in set(self._allowed_user_targets()):
            raise ValueError(f"Teams user target is not allowlisted: {normalized}")
        return normalized

    def _current_user_ref(self, client: httpx.Client) -> str:
        cache_key = self._current_user_cache_key()
        cached = self.user_chat_cache.get(cache_key)
        if cached:
            return cached
        response = self._request_with_retries(
            client,
            "GET",
            f"{self.api_base_url}/me",
            params={"$select": "id,userPrincipalName"},
        )
        payload = response.json()
        user_ref = str(payload.get("id") or payload.get("userPrincipalName") or "").strip()
        if not user_ref:
            raise ValueError("Teams Graph /me response did not include id or userPrincipalName.")
        self.user_chat_cache[cache_key] = user_ref
        return user_ref

    def ensure_one_on_one_chat(self, user_ref: str) -> str:
        normalized_user_target = self._require_allowed_user_target(user_ref)
        target_user_ref = normalized_user_target.removeprefix("user:")
        cache_key = self._user_chat_cache_key(normalized_user_target)
        cached_chat_id = self.user_chat_cache.get(cache_key)
        if cached_chat_id:
            return normalize_teams_target(f"chat:{cached_chat_id}")

        with httpx.Client(timeout=self.timeout_seconds) as client:
            current_user_ref = self._current_user_ref(client)
            if current_user_ref.casefold() == target_user_ref.casefold():
                raise ValueError("Cannot create a one-on-one Teams chat with the signed-in user.")
            response = self._request_with_retries(
                client,
                "POST",
                f"{self.api_base_url}/chats",
                json={
                    "chatType": "oneOnOne",
                    "members": [
                        {
                            "@odata.type": "#microsoft.graph.aadUserConversationMember",
                            "roles": ["owner"],
                            "user@odata.bind": self._graph_user_bind_url(
                                self.api_base_url, current_user_ref
                            ),
                        },
                        {
                            "@odata.type": "#microsoft.graph.aadUserConversationMember",
                            "roles": ["owner"],
                            "user@odata.bind": self._graph_user_bind_url(
                                self.api_base_url, target_user_ref
                            ),
                        },
                    ],
                },
            )
        payload = response.json()
        chat_id = str(payload.get("id") or "").strip()
        if not chat_id:
            raise ValueError("Teams Graph create chat response did not include an id.")
        self.user_chat_cache[cache_key] = chat_id
        return normalize_teams_target(f"chat:{chat_id}")

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

    def _iter_one_on_one_chats(
        self,
        client: httpx.Client,
        *,
        budget: _TeamsGraphPaginationBudget | None = None,
    ) -> Iterator[dict[str, Any]]:
        if budget is None:
            budget = self.new_pagination_budget()
        next_url = f"{self.api_base_url}/me/chats"
        params: dict[str, Any] | None = {
            "$filter": "chatType eq 'oneOnOne'",
            "$top": self.poll_limit,
        }
        seen_urls: set[str] = set()
        while next_url:
            if not budget.can_request_page():
                break
            remaining_seconds = budget.remaining_seconds()
            if remaining_seconds <= 0:
                break
            if next_url in seen_urls:
                raise RuntimeError("Teams Graph pagination loop detected for one-on-one chats")
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
            for chat_payload in payload.get("value", []):
                if isinstance(chat_payload, dict):
                    yield chat_payload
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
        fetched_messages: dict[str, TeamsMessageEnvelope] = {}
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
                if parsed_target.kind == "user":
                    raise ValueError("Teams user targets must be resolved before reading messages.")
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
                        fetched_messages[envelope.cursor] = envelope

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

        _put_teams_messages(self.message_store, fetched_messages)
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
        target = parse_teams_target(normalized)
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before this operation.")
        return target

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
            (item for item in attachments if _teams_attachment_id(item) == attachment_id),
            None,
        )
        if attachment is None and self._record_has_hosted_content_hint(record):
            attachments = self._list_attachments_for_record(
                record,
                target,
                include_remote_hosted=True,
            )
            attachment = next(
                (item for item in attachments if _teams_attachment_id(item) == attachment_id),
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
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before posting files.")
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
        download_links: list[tuple[str, str]] = []
        for index, artifact in enumerate(artifacts):
            temporary_id = str(index + 1)
            filename = _artifact_filename(artifact, index=index)
            content_type = _artifact_content_type(artifact, filename)
            if _artifact_is_inline_teams_image(content_type):
                hosted_content_url = f"../hostedContents/{temporary_id}/$value"
                hosted_contents.append(
                    {
                        "@microsoft.graph.temporaryId": temporary_id,
                        "contentBytes": base64.b64encode(_artifact_bytes(artifact)).decode("ascii"),
                        "contentType": content_type,
                    }
                )
                inline_images.append((filename, hosted_content_url))
                continue

            reference_content_url = _artifact_teams_reference_content_url(artifact)
            if not reference_content_url:
                download_url = _artifact_control_plane_download_url(artifact)
                if download_url:
                    download_links.append((filename, download_url))
                    continue
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
                f'<p><attachment id="{html.escape(attachment_id, quote=True)}"></attachment></p>'
            )
        for filename, hosted_content_url in inline_images:
            body_html += (
                '<p><img src="'
                f"{html.escape(hosted_content_url, quote=True)}"
                '" alt="'
                f"{html.escape(filename, quote=True)}"
                '"></p>'
            )
        for filename, download_url in download_links:
            escaped_filename = html.escape(filename, quote=False)
            body_html += (
                f'<p><a href="{html.escape(download_url, quote=True)}">{escaped_filename}</a></p>'
            )
        payload: dict[str, Any] = {
            "body": {"content": body_html, "contentType": "html"},
        }
        if attachments:
            payload["attachments"] = attachments
        if hosted_contents:
            payload["hostedContents"] = hosted_contents
        return payload

    def _resolve_mention_targets(
        self, client: httpx.Client, mentions: list[str] | None
    ) -> list[_TeamsMentionTarget]:
        resolved: list[_TeamsMentionTarget] = []
        seen_user_ids: set[str] = set()
        for user_ref in normalize_teams_mention_refs(mentions):
            mention = self.resolve_user_mention(client, user_ref)
            if mention is None:
                raise ValueError(f"Unable to resolve Teams mention user: {user_ref}")
            if mention.user_id in seen_user_ids:
                continue
            seen_user_ids.add(mention.user_id)
            resolved.append(mention)
        return resolved

    def _teams_status_message_payload(
        self,
        message: str,
        mentions: list[_TeamsMentionTarget],
    ) -> dict[str, Any]:
        body_html = _teams_message_to_html(message)
        payload: dict[str, Any] = {
            "body": {"content": body_html, "contentType": "html"},
        }
        if not mentions:
            return payload

        mention_tags: list[str] = []
        graph_mentions: list[dict[str, Any]] = []
        for index, mention in enumerate(mentions):
            mention_text = mention.mention_text
            mention_tags.append(f'<at id="{index}">{html.escape(mention_text, quote=False)}</at>')
            graph_mentions.append(
                {
                    "id": index,
                    "mentionText": mention_text,
                    "mentioned": {
                        "user": {
                            "id": mention.user_id,
                            "displayName": mention_text,
                            "userIdentityType": "aadUser",
                        }
                    },
                }
            )
        payload["body"]["content"] = f"<p>{' '.join(mention_tags)}</p>{body_html}"
        payload["mentions"] = graph_mentions
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
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        target = parse_teams_target(channel_target)
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before posting.")
        normalized_mentions = normalize_teams_mention_refs(mentions)
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
            mention_targets = self._resolve_mention_targets(client, normalized_mentions)
            response = self._request_with_retries(
                client,
                "POST",
                url,
                json=self._teams_status_message_payload(message, mention_targets),
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
        if target.kind == "user":
            raise ValueError("Teams user targets must be resolved before adding reactions.")
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
        return raw_source in {
            "discord_bot_api_status",
            "discord_webhook_status",
        } and raw_session_id == session_id

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
            exclude_ignored_feedback=True,
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
            exclude_ignored_feedback=True,
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
            exclude_ignored_feedback=False,
        )

    def _list_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None,
        cursor: str | None,
        limit: int | None,
        persist_session_cursor: bool,
        exclude_ignored_feedback: bool,
    ) -> ListUnreadFeedbackResponse:
        stream_cursors = {
            (stream["provider"], stream["repo"], stream["pr_id"]): self._effective_cursor(
                session_id=session_id,
                stream=stream,
                explicit_cursor=cursor,
            )
            for stream in streams
        }
        ignored_feedback_ids = (
            self._read_ignored_feedback_ids() if exclude_ignored_feedback else None
        )
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


def _pr_artifact_filename(artifact: dict[str, Any]) -> str:
    return (
        str(
            artifact.get("normalized_safe_filename")
            or artifact.get("filename")
            or artifact.get("original_filename")
            or "attachment"
        ).strip()
        or "attachment"
    )


def _pr_artifact_mime_type(artifact: dict[str, Any]) -> str | None:
    mime_type = str(artifact.get("mime_type") or "").strip()
    return mime_type or mimetypes.guess_type(_pr_artifact_filename(artifact))[0]


def _pr_artifact_is_image(artifact: dict[str, Any]) -> bool:
    mime_type = _pr_artifact_mime_type(artifact)
    if mime_type and mime_type.lower().startswith("image/"):
        return True
    guessed, _encoding = mimetypes.guess_type(_pr_artifact_filename(artifact))
    return bool(guessed and guessed.lower().startswith("image/"))


def _pr_artifact_bytes(artifact: dict[str, Any]) -> bytes:
    path = str(artifact.get("path") or "").strip()
    if not path:
        raise ValueError("Published artifact payload is missing path")
    return Path(path).read_bytes()


def _append_markdown_attachment_links(
    body_text: str, uploaded_attachments: list[dict[str, Any]]
) -> str:
    if not uploaded_attachments:
        return body_text
    lines = [body_text.rstrip(), ""]
    for item in uploaded_attachments:
        filename = str(item.get("filename") or "attachment").strip() or "attachment"
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        escaped_label = filename.replace("[", "\\[").replace("]", "\\]")
        if item.get("is_image"):
            lines.append(f"![{escaped_label}]({url})")
        else:
            lines.append(f"[{escaped_label}]({url})")
    return "\n".join(lines).rstrip()


def _reject_unsupported_attachments(
    provider: str, attachments: list[dict[str, Any]] | None
) -> None:
    if attachments:
        raise ValueError(f"{provider} PR publishing does not support artifact attachments")


def _reject_unsupported_comment_attachments(provider: str, comments: list[dict[str, Any]]) -> None:
    if any(comment.get("attachments") for comment in comments):
        raise ValueError(f"{provider} PR review comments do not support artifact attachments")


def _reject_unsupported_mentions(provider: str, mentions: list[str] | None) -> None:
    if normalize_ado_mention_refs(mentions):
        raise ValueError(f"{provider} PR comments do not support ADO mentions")


def _ado_resolved_mention_payloads(
    mentions: list[AdoResolvedMention],
) -> list[dict[str, str]]:
    return [mention.audit_payload() for mention in mentions]


def _upload_azure_devops_pr_attachments(
    *,
    client: httpx.Client,
    organization_url: str,
    project: str,
    pat: str,
    repo: str,
    pr_id: str,
    attachments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    uploaded: list[dict[str, Any]] = []
    for artifact in attachments or []:
        filename = _pr_artifact_filename(artifact)
        response = client.post(
            (
                f"{organization_url}/{project}/_apis/git/repositories/"
                f"{_normalize_azure_devops_repo_identifier(repo)}/pullRequests/{pr_id}/"
                f"attachments/{quote(filename, safe='')}"
            ),
            params={"api-version": "7.1"},
            auth=("", pat),
            content=_pr_artifact_bytes(artifact),
            headers={"Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()
        payload = response.json()
        uploaded.append(
            {
                "provider": "azure_devops",
                "surface": "pull_request",
                "artifact_id": artifact.get("artifact_id"),
                "filename": filename,
                "mime_type": _pr_artifact_mime_type(artifact),
                "byte_count": artifact.get("byte_count"),
                "url": str(payload.get("url") or "").strip(),
                "id": payload.get("id"),
                "is_image": _pr_artifact_is_image(artifact),
                "raw": payload,
            }
        )
    return uploaded


class PrReplyProvider(Protocol):
    def post_reply(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PostPrFeedbackReplyResponse: ...


class PrQueryProvider(Protocol):
    def list_pull_requests(
        self,
        *,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
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

    def get_pull_request_work_items(
        self,
        *,
        repo: str,
        pr_id: str,
    ) -> PullRequestWorkItemsResponse: ...


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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PostPrFeedbackReplyResponse:
        _reject_unsupported_mentions("GitHub", mentions)
        _reject_unsupported_attachments("GitHub", attachments)
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PostPrFeedbackReplyResponse:
        if not thread_id:
            raise ValueError("Azure DevOps reply posting requires thread_id")
        if not comment_id:
            raise ValueError("Azure DevOps reply posting requires comment_id")
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resolved_mentions = resolve_ado_mentions(
                client=client,
                organization_url=self.organization_url,
                pat=self.pat,
                mention_refs=mentions,
            )
            uploaded_attachments = _upload_azure_devops_pr_attachments(
                client=client,
                organization_url=self.organization_url,
                project=self.project,
                pat=self.pat,
                repo=repo,
                pr_id=pr_id,
                attachments=attachments,
            )
            body_with_mentions = render_ado_mentions_prefix(body_text, resolved_mentions)
            content = _append_markdown_attachment_links(body_with_mentions, uploaded_attachments)
            response = client.post(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                    f"pullRequests/{pr_id}/threads/{thread_id}/comments"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
                json={"content": content, "parentCommentId": int(comment_id)},
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
            uploaded_attachments=uploaded_attachments,
            resolved_mentions=_ado_resolved_mention_payloads(resolved_mentions),
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

    def resolve_identity(
        self,
        *,
        query: str,
        project: str | None = None,
    ) -> ResolveAdoIdentityResponse:
        del project
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query is required")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            identities: list[dict[str, Any]]
            if normalized_query.casefold() == "me":
                response = client.get(
                    f"{self.organization_url}/_apis/connectionData",
                    params={"api-version": "7.1-preview.1"},
                    auth=("", self.pat),
                )
                response.raise_for_status()
                payload = response.json()
                authenticated = (
                    payload.get("authenticatedUser") if isinstance(payload, dict) else None
                )
                identities = [authenticated] if isinstance(authenticated, dict) else []
            else:
                resolver = AdoMentionResolver(
                    client=client,
                    organization_url=self.organization_url,
                    pat=self.pat,
                )
                try:
                    resolved = resolver.resolve(normalized_query)
                    matches = [
                        {
                            "id": resolved.id,
                            "descriptor": resolved.descriptor,
                            "display_name": resolved.display_name,
                            "unique_name": resolved.unique_name,
                            "email": resolved.unique_name
                            if resolved.unique_name and "@" in resolved.unique_name
                            else None,
                            "raw": resolved.audit_payload(),
                        }
                    ]
                    return ResolveAdoIdentityResponse(
                        query=normalized_query,
                        confidence="exact",
                        id=resolved.id,
                        descriptor=resolved.descriptor,
                        display_name=resolved.display_name,
                        unique_name=resolved.unique_name,
                        email=matches[0]["email"],
                        matches=matches,
                    )
                except ValueError:
                    identities = resolver._read_identities(  # noqa: SLF001 - reuse ADO identity API wrapper.
                        {
                            "searchFilter": "General",
                            "filterValue": normalized_query,
                            "queryMembership": "None",
                        }
                    )

        matches = [
            _ado_identity_summary(normalized_query, identity)
            for identity in identities
            if isinstance(identity, dict)
            and identity.get("isContainer") is not True
            and identity.get("isActive") is not False
            and _text_or_none(identity.get("id"))
        ]
        exact_matches = [
            item for item in matches if normalized_query.casefold() in _identity_match_values(item)
        ]
        selected = exact_matches[0] if len(exact_matches) == 1 else None
        if selected is None and len(matches) == 1:
            selected = matches[0]
        confidence = "none"
        if selected is not None:
            confidence = "exact" if len(matches) == 1 or len(exact_matches) == 1 else "low"
        elif matches:
            confidence = "ambiguous"
        return ResolveAdoIdentityResponse(
            query=normalized_query,
            confidence=confidence,
            id=selected.get("id") if selected else None,
            descriptor=selected.get("descriptor") if selected else None,
            display_name=selected.get("display_name") if selected else None,
            unique_name=selected.get("unique_name") if selected else None,
            email=selected.get("email") if selected else None,
            matches=matches,
        )

    def list_pr_comment_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        include_system_comments: bool = False,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommentActivityResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        status_values = _activity_statuses(statuses)
        pull_requests, candidate_scan_complete = self._activity_pull_request_candidates(
            repo=repo,
            project=project_name,
            statuses=status_values,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )
        comments: list[dict[str, Any]] = []
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for pull_request in pull_requests:
                response = client.get(
                    (
                        f"{self.organization_url}/{project_name}/_apis/git/repositories/"
                        f"{repo_identifier}/pullRequests/{pull_request.pr_id}/threads"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                )
                response.raise_for_status()
                payload = response.json()
                raw_threads = payload.get("value") if isinstance(payload, dict) else None
                for thread in raw_threads if isinstance(raw_threads, list) else []:
                    if not isinstance(thread, dict):
                        continue
                    thread_id = str(thread.get("id") or "").strip() or None
                    thread_context = (
                        thread.get("threadContext")
                        if isinstance(thread.get("threadContext"), dict)
                        else {}
                    )
                    file_path = _text_or_none(thread_context.get("filePath"))
                    line = _ado_line_from_context(thread_context)
                    resolved = _ado_thread_resolved(thread.get("status"))
                    raw_comments = [
                        item for item in thread.get("comments", []) if isinstance(item, dict)
                    ]
                    for comment in raw_comments:
                        if comment.get("isDeleted"):
                            continue
                        created_at = _parse_optional_dt(
                            comment.get("publishedDate") or comment.get("lastUpdatedDate")
                        )
                        if not _datetime_in_range(created_at, since=since, until=until):
                            continue
                        author_identity = (
                            comment.get("author") if isinstance(comment.get("author"), dict) else {}
                        )
                        author_name = _text_or_none(author_identity.get("displayName"))
                        body = _text_or_none(comment.get("content")) or ""
                        if not include_system_comments and _ado_should_skip_comment(
                            author=author_name or "unknown",
                            body=body,
                            comment_type=comment.get("commentType"),
                            file_path=file_path,
                        ):
                            continue
                        if not _identity_matches(author, author_identity or author_name):
                            continue
                        comment_id = str(comment.get("id") or "").strip() or None
                        comment_links = (
                            comment.get("_links") if isinstance(comment.get("_links"), dict) else {}
                        )
                        comment_web_link = (
                            comment_links.get("web")
                            if isinstance(comment_links.get("web"), dict)
                            else {}
                        )
                        comments.append(
                            {
                                "repo": repo,
                                "project": project_name,
                                "pr_id": pull_request.pr_id,
                                "pr_title": pull_request.title,
                                "pr_status": pull_request.status,
                                "pr_url": pull_request.url,
                                "thread_id": thread_id,
                                "comment_id": comment_id,
                                "author": author_name,
                                "author_identity": author_identity,
                                "created_at": created_at,
                                "body": body,
                                "body_snippet": _snippet(body),
                                "file_path": file_path,
                                "line": line,
                                "resolved": resolved,
                                "url": _text_or_none(comment_web_link.get("href"))
                                or pull_request.url,
                                "raw": comment,
                            }
                        )
        comments.sort(key=lambda item: item["created_at"], reverse=True)
        total_activity_count = len(comments)
        paged_comments = comments[skip : skip + top]
        next_skip = (
            skip + len(paged_comments)
            if skip + len(paged_comments) < total_activity_count
            else None
        )
        has_more = next_skip is not None or not candidate_scan_complete
        return AdoPrCommentActivityResponse(
            repo=repo,
            project=project_name,
            comments=paged_comments,
            count=len(paged_comments),
            paging={
                "skip": skip,
                "top": top,
                "candidate_count": len(pull_requests),
                "candidate_scanned_count": len(pull_requests),
                "candidate_scan_complete": candidate_scan_complete,
                "total_activity_count": total_activity_count,
                "total_activity_count_complete": candidate_scan_complete,
                "returned_count": len(paged_comments),
                "has_more": has_more,
                "next_skip": next_skip,
                "statuses": status_values,
                "exhaustive": exhaustive,
                "max_candidates": _activity_max_candidates(max_candidates),
            },
        )

    def list_pr_commit_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommitActivityResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        status_values = _activity_statuses(statuses)
        pull_requests, candidate_scan_complete = self._activity_pull_request_candidates(
            repo=repo,
            project=project_name,
            statuses=status_values,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )
        commits: list[dict[str, Any]] = []
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for pull_request in pull_requests:
                response = client.get(
                    (
                        f"{self.organization_url}/{project_name}/_apis/git/repositories/"
                        f"{repo_identifier}/pullRequests/{pull_request.pr_id}/commits"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                )
                response.raise_for_status()
                payload = response.json()
                raw_commits = payload.get("value") if isinstance(payload, dict) else None
                for commit in raw_commits if isinstance(raw_commits, list) else []:
                    if not isinstance(commit, dict):
                        continue
                    commit_author = (
                        commit.get("author") if isinstance(commit.get("author"), dict) else {}
                    )
                    committer = (
                        commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
                    )
                    if author and not (
                        _identity_matches(author, commit_author)
                        or _identity_matches(author, committer)
                    ):
                        continue
                    authored_at = _parse_optional_dt(commit_author.get("date"))
                    committed_at = _parse_optional_dt(committer.get("date"))
                    best_date = authored_at or committed_at
                    unverified_date = False
                    if best_date is None:
                        pr_date = pull_request.updated_at or pull_request.created_at
                        if not _datetime_in_range(pr_date, since=since, until=until):
                            continue
                        unverified_date = True
                    elif not _datetime_in_range(best_date, since=since, until=until):
                        continue
                    commit_id = str(commit.get("commitId") or commit.get("commit_id") or "").strip()
                    if not commit_id:
                        continue
                    commits.append(
                        {
                            "repo": repo,
                            "project": project_name,
                            "pr_id": pull_request.pr_id,
                            "pr_title": pull_request.title,
                            "pr_status": pull_request.status,
                            "pr_url": pull_request.url,
                            "commit_id": commit_id,
                            "comment": _text_or_none(commit.get("comment")),
                            "url": _text_or_none(commit.get("url")),
                            "author": commit_author,
                            "committer": committer,
                            "authored_at": authored_at,
                            "committed_at": committed_at,
                            "unverified_date": unverified_date,
                            "raw": commit,
                        }
                    )
        commits.sort(
            key=lambda item: (
                item.get("authored_at")
                or item.get("committed_at")
                or datetime.min.replace(tzinfo=UTC)
            ),
            reverse=True,
        )
        total_activity_count = len(commits)
        paged_commits = commits[skip : skip + top]
        next_skip = (
            skip + len(paged_commits) if skip + len(paged_commits) < total_activity_count else None
        )
        has_more = next_skip is not None or not candidate_scan_complete
        return AdoPrCommitActivityResponse(
            repo=repo,
            project=project_name,
            commits=paged_commits,
            count=len(paged_commits),
            paging={
                "skip": skip,
                "top": top,
                "candidate_count": len(pull_requests),
                "candidate_scanned_count": len(pull_requests),
                "candidate_scan_complete": candidate_scan_complete,
                "total_activity_count": total_activity_count,
                "total_activity_count_complete": candidate_scan_complete,
                "returned_count": len(paged_commits),
                "has_more": has_more,
                "next_skip": next_skip,
                "statuses": status_values,
                "exhaustive": exhaustive,
                "max_candidates": _activity_max_candidates(max_candidates),
            },
        )

    def _activity_pull_request_candidates(
        self,
        *,
        repo: str,
        project: str,
        statuses: list[str],
        exhaustive: bool,
        max_candidates: int,
    ) -> tuple[list[PullRequestItem], bool]:
        page_size = 200
        candidate_limit = None if exhaustive else _activity_max_candidates(max_candidates)
        candidates: dict[tuple[str, str, str], PullRequestItem] = {}
        for status_index, status in enumerate(statuses):
            page_skip = 0
            page_cursor: str | None = None
            seen_pages: set[tuple[int, str | None]] = set()
            while True:
                remaining = (
                    None if candidate_limit is None else max(0, candidate_limit - len(candidates))
                )
                if remaining == 0:
                    return list(candidates.values()), False
                page_key = (page_skip, page_cursor)
                if page_key in seen_pages:
                    break
                seen_pages.add(page_key)
                request_top = page_size if remaining is None else min(page_size, remaining)
                response = self.list_pull_requests(
                    repo=repo,
                    project=project,
                    status=status,
                    top=request_top,
                    skip=page_skip,
                    cursor=page_cursor,
                    include_links=True,
                )
                for item in response.pull_requests:
                    candidates[(item.provider, item.repo, item.pr_id)] = item
                paging = response.paging or {}
                if candidate_limit is not None and len(candidates) >= candidate_limit:
                    more_statuses = status_index < len(statuses) - 1
                    return list(candidates.values()), not (paging.get("has_more") or more_statuses)
                next_cursor = str(response.next_cursor or paging.get("next_cursor") or "").strip()
                if next_cursor and next_cursor != page_cursor:
                    page_cursor = next_cursor
                    continue
                raw_next_skip = paging.get("next_skip")
                next_skip = raw_next_skip if isinstance(raw_next_skip, int) else None
                if next_skip is not None and next_skip > page_skip:
                    page_skip = next_skip
                    page_cursor = None
                    continue
                if not paging.get("has_more"):
                    break
                if not response.pull_requests:
                    break
                page_skip += len(response.pull_requests)
                page_cursor = None
        return list(candidates.values()), True

    def list_pull_requests(
        self,
        *,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        tag_filter = _parse_pull_request_tag_expression(tags)
        params: dict[str, Any] = {"api-version": "7.1"}
        for param_name, value in (
            ("searchCriteria.status", status.strip().lower() if status else None),
            ("searchCriteria.creatorId", creator_id),
            ("searchCriteria.reviewerId", reviewer_id),
            ("searchCriteria.sourceRefName", source_ref_name),
            ("searchCriteria.targetRefName", target_ref_name),
            ("searchCriteria.sourceRepositoryId", source_repository_id),
            ("searchCriteria.repositoryId", repository_id),
            ("searchCriteria.minTime", min_time),
            ("searchCriteria.maxTime", max_time),
            ("searchCriteria.queryTimeRangeType", query_time_range_type),
        ):
            text = _normalize_optional_text(value)
            if text:
                params[param_name] = text
        if include_links is not None:
            params["searchCriteria.includeLinks"] = include_links
        if max_comment_length is not None:
            params["maxCommentLength"] = max_comment_length
        if top is not None:
            params["$top"] = top
        if skip is not None:
            params["$skip"] = skip
        if cursor and cursor.strip():
            params["continuationToken"] = cursor.strip()

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                (
                    f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                    "pullrequests"
                ),
                params=params,
                auth=("", self.pat),
            )
            response.raise_for_status()
            payload = response.json()
            raw_items = payload.get("value")
            pr_items = raw_items if isinstance(raw_items, list) else []
            normalized_items = [
                self._normalize_pull_request_item(item, repo=repo)
                for item in pr_items
                if isinstance(item, dict)
            ]
            if tag_filter is not None:
                for item in normalized_items:
                    item.tags = self._list_pr_labels(
                        client=client,
                        repo_identifier=repo_identifier,
                        pr_id=item.pr_id,
                        project=project_name,
                    )

        filtered_items = [
            item
            for item in normalized_items
            if tag_filter is None or _pull_request_tags_match(item.tags, tag_filter=tag_filter)
        ]

        raw_count = payload.get("count")
        candidate_count = len(normalized_items)
        next_cursor_header = response.headers.get("x-ms-continuationtoken")
        next_cursor_payload = payload.get("continuationToken")
        next_cursor = str(next_cursor_header or next_cursor_payload or "").strip() or None
        next_skip = None
        if top is not None and candidate_count >= top:
            next_skip = (skip or 0) + candidate_count
        paging = {
            "cursor": cursor,
            "next_cursor": next_cursor,
            "skip": skip,
            "top": top,
            "next_skip": next_skip,
            "candidate_count": candidate_count,
            "returned_count": len(filtered_items),
            "server_count": raw_count if isinstance(raw_count, int) else None,
            "tag_filter_client_side": tag_filter is not None,
            "has_more": bool(next_cursor or next_skip is not None),
        }
        return ListPullRequestsResponse(
            provider="azure_devops",
            repo=repo,
            project=project_name,
            pull_requests=filtered_items,
            count=len(filtered_items),
            paging=paging,
            next_cursor=next_cursor,
        )

    def _normalize_pull_request_item(self, item: dict[str, Any], *, repo: str) -> PullRequestItem:
        created_by = item.get("createdBy") if isinstance(item.get("createdBy"), dict) else {}
        creation_date = _parse_dt(item["creationDate"]) if item.get("creationDate") else None
        closed_date = _parse_dt(item["closedDate"]) if item.get("closedDate") else None
        return PullRequestItem(
            provider="azure_devops",
            repo=repo,
            pr_id=str(item.get("pullRequestId", "")).strip(),
            title=(str(item.get("title", "")).strip() or None),
            description=(str(item.get("description", "")).strip() or None),
            status=(str(item.get("status", "")).strip() or None),
            source_branch=str(item.get("sourceRefName", "")).strip() or None,
            target_branch=str(item.get("targetRefName", "")).strip() or None,
            created_at=creation_date,
            updated_at=closed_date or creation_date,
            closed_at=closed_date,
            created_by=(
                str(created_by.get("displayName") or created_by.get("uniqueName") or "").strip()
                or None
            ),
            url=_azure_pr_web_url(item),
            raw=item,
        )

    def _list_pr_labels(
        self,
        *,
        client: httpx.Client,
        repo_identifier: str,
        pr_id: str,
        project: str | None = None,
    ) -> list[str]:
        if not pr_id:
            return []
        project_name = _normalize_optional_text(project) or self.project
        response = client.get(
            (
                f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                f"pullRequests/{pr_id}/labels"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
        )
        response.raise_for_status()
        payload = response.json()
        raw_items = payload.get("value")
        label_items = raw_items if isinstance(raw_items, list) else []
        return [
            name
            for name in (
                str(item.get("name", "")).strip() for item in label_items if isinstance(item, dict)
            )
            if name
        ]

    def _add_pr_label(
        self,
        *,
        client: httpx.Client,
        repo_identifier: str,
        pr_id: str,
        tag: str,
        project: str | None = None,
    ) -> None:
        project_name = _normalize_optional_text(project) or self.project
        response = client.post(
            (
                f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                f"pullRequests/{pr_id}/labels"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
            json={"name": tag},
        )
        response.raise_for_status()

    def _remove_pr_label(
        self,
        *,
        client: httpx.Client,
        repo_identifier: str,
        pr_id: str,
        tag: str,
        project: str | None = None,
    ) -> None:
        project_name = _normalize_optional_text(project) or self.project
        response = client.delete(
            (
                f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                f"pullRequests/{pr_id}/labels/{quote(tag, safe='')}"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
        )
        response.raise_for_status()

    def update_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        project: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        expected_statuses: list[str] | None = None,
    ) -> UpdatePullRequestResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        expected = _normalize_string_list(expected_statuses)
        tags_to_add = _normalize_string_list(add_tags)
        tags_to_remove = _normalize_string_list(remove_tags)

        with httpx.Client(timeout=self.timeout_seconds) as client:
            current_response = client.get(
                (
                    f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                    f"pullRequests/{pr_id}"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
            )
            current_response.raise_for_status()
            current_payload = current_response.json()
            current_status = str(current_payload.get("status", "")).strip() or None
            current_tags = self._list_pr_labels(
                client=client,
                repo_identifier=repo_identifier,
                pr_id=pr_id,
                project=project_name,
            )
            if expected and (current_status or "").casefold() not in {
                item.casefold() for item in expected
            }:
                return UpdatePullRequestResponse(
                    success=True,
                    updated=False,
                    skipped=True,
                    skipped_reason="unexpected_status",
                    provider="azure_devops",
                    repo=repo,
                    pr_id=pr_id,
                    status=current_status,
                    title=str(current_payload.get("title", "")).strip() or None,
                    url=_azure_pr_web_url(current_payload),
                    tags=current_tags,
                    raw=current_payload,
                )

            patch_payload: dict[str, Any] = {}
            if title is not None:
                patch_payload["title"] = _normalize_optional_text(title) or ""
            if description is not None:
                patch_payload["description"] = _normalize_optional_text(description) or ""
            normalized_status = _normalize_optional_text(status)
            if normalized_status:
                patch_payload["status"] = normalized_status.lower()

            latest_payload = current_payload
            if patch_payload:
                patch_response = client.patch(
                    (
                        f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                        f"pullrequests/{pr_id}"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                    json=patch_payload,
                )
                patch_response.raise_for_status()
                latest_payload = patch_response.json()

            existing_by_key = {tag.casefold(): tag for tag in current_tags}
            added_tags: list[str] = []
            for tag in tags_to_add:
                if tag.casefold() in existing_by_key:
                    continue
                self._add_pr_label(
                    client=client,
                    repo_identifier=repo_identifier,
                    pr_id=pr_id,
                    tag=tag,
                    project=project_name,
                )
                existing_by_key[tag.casefold()] = tag
                added_tags.append(tag)

            removed_tags: list[str] = []
            for tag in tags_to_remove:
                existing_tag = existing_by_key.get(tag.casefold())
                if not existing_tag:
                    continue
                self._remove_pr_label(
                    client=client,
                    repo_identifier=repo_identifier,
                    pr_id=pr_id,
                    tag=existing_tag,
                    project=project_name,
                )
                existing_by_key.pop(tag.casefold(), None)
                removed_tags.append(existing_tag)

            latest_tags = (
                self._list_pr_labels(
                    client=client,
                    repo_identifier=repo_identifier,
                    pr_id=pr_id,
                    project=project_name,
                )
                if added_tags or removed_tags
                else current_tags
            )

        return UpdatePullRequestResponse(
            success=True,
            updated=bool(patch_payload or added_tags or removed_tags),
            skipped=False,
            skipped_reason=None,
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            status=str(latest_payload.get("status", "")).strip() or current_status,
            title=str(latest_payload.get("title", "")).strip()
            or str(current_payload.get("title", "")).strip()
            or None,
            url=_azure_pr_web_url(latest_payload) or _azure_pr_web_url(current_payload),
            tags=latest_tags,
            added_tags=added_tags,
            removed_tags=removed_tags,
            raw=latest_payload if isinstance(latest_payload, dict) else {},
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

    def get_pull_request_work_items(
        self,
        *,
        repo: str,
        pr_id: str,
    ) -> PullRequestWorkItemsResponse:
        """Return the Azure DevOps work items associated with a pull request.

        Azure DevOps returns work-item references from the Git API. Fetch the
        lightweight work-item records separately so workflow configuration can
        use the assignee's normalized name and email without exposing the PAT
        to an agent.
        """

        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            references_response = client.get(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/"
                    f"{repo_identifier}/pullRequests/{pr_id}/workitems"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
            )
            references_response.raise_for_status()
            references_payload = references_response.json()

            raw_references = (
                references_payload.get("value") if isinstance(references_payload, dict) else None
            )
            references = [item for item in raw_references or [] if isinstance(item, dict)]
            numeric_ids = _unique_positive_ints(item.get("id") for item in references)
            details_by_id = self._get_work_item_details(client=client, work_item_ids=numeric_ids)

        work_items = [
            _pull_request_work_item_from_reference(
                reference=reference,
                details=details_by_id.get(str(reference.get("id") or "").strip()),
            )
            for reference in references
            if str(reference.get("id") or "").strip()
        ]
        return PullRequestWorkItemsResponse(
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            work_items=work_items,
            count=len(work_items),
            raw={"references": references},
        )

    def _get_work_item_details(
        self,
        *,
        client: httpx.Client,
        work_item_ids: list[int],
    ) -> dict[str, dict[str, Any]]:
        details_by_id: dict[str, dict[str, Any]] = {}
        for start in range(0, len(work_item_ids), 200):
            response = client.post(
                f"{self.organization_url}/_apis/wit/workitemsbatch",
                params={"api-version": "7.1"},
                headers={"Content-Type": "application/json"},
                auth=("", self.pat),
                json={
                    "ids": work_item_ids[start : start + 200],
                    "fields": ["System.Id", "System.Title", "System.AssignedTo"],
                    "errorPolicy": "Omit",
                },
            )
            response.raise_for_status()
            payload = response.json()
            values = payload.get("value") if isinstance(payload, dict) else None
            for item in values or []:
                if not isinstance(item, dict):
                    continue
                work_item_id = str(item.get("id") or "").strip()
                if work_item_id:
                    details_by_id[work_item_id] = item
        return details_by_id


class GitHubPrQueryProvider:
    def __init__(
        self,
        *,
        token: str | None = None,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.token = token.strip() if token else None
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def list_pull_requests(
        self,
        *,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        raise ValueError("GitHub PR listing is not supported")

    def get_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()

            base_payload = payload if isinstance(payload, dict) else {}
            raw_payload: dict[str, Any] = dict(base_payload)

            commits_payload: list[dict[str, Any]] = []
            if include_commits:
                commits_payload = self._get_paginated_items(
                    client,
                    f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/commits",
                )
                raw_payload["commits"] = commits_payload

            review_payload: list[dict[str, Any]] = []
            if include_reviewers:
                review_payload = self._get_paginated_items(
                    client,
                    f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/reviews",
                )
                raw_payload["reviews"] = review_payload

        return GetPullRequestResponse(
            provider="github",
            repo=repo,
            pr_id=pr_id,
            title=str(base_payload.get("title", "")).strip() or None,
            status=self._normalize_status(base_payload),
            url=(
                str(base_payload.get("html_url") or base_payload.get("url") or "").strip() or None
            ),
            source_branch=self._normalize_pull_branch(base_payload, branch_name="head", key="ref"),
            target_branch=self._normalize_pull_branch(base_payload, branch_name="base", key="ref"),
            source_commit=self._normalize_pull_branch(base_payload, branch_name="head", key="sha"),
            target_commit=self._normalize_pull_branch(base_payload, branch_name="base", key="sha"),
            created_by=self._normalize_created_by(base_payload),
            reviewers=self._normalize_reviewers(base_payload, review_payload, include_reviewers),
            commits=self._normalize_commits(commits_payload),
            raw=raw_payload,
        )

    def _get_paginated_items(self, client: httpx.Client, url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        params: dict[str, str] | None = {"per_page": "100"}
        while next_url:
            response = client.get(next_url, params=params, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                raw_items = payload
            elif isinstance(payload, dict):
                raw_items = payload.get("value")
            else:
                raw_items = []
            if isinstance(raw_items, list):
                items.extend(item for item in raw_items if isinstance(item, dict))
            next_url = self._next_page_url(response)
            params = None
        return items

    @staticmethod
    def _next_page_url(response: httpx.Response) -> str | None:
        next_link = response.links.get("next")
        if not isinstance(next_link, dict):
            return None
        next_url = str(next_link.get("url") or "").strip()
        return next_url or None

    def _normalize_status(self, payload: dict[str, Any]) -> str | None:
        merged = payload.get("merged")
        merged_at = payload.get("merged_at")
        if merged is True or str(merged_at or "").strip():
            return "merged"
        status = str(payload.get("state", "")).strip().lower()
        return status or None

    def _normalize_pull_branch(
        self,
        payload: dict[str, Any],
        *,
        branch_name: str,
        key: str,
    ) -> str | None:
        branch = payload.get(branch_name)
        if not isinstance(branch, dict):
            return None
        value = str(branch.get(key, "")).strip()
        return value or None

    def _normalize_created_by(self, payload: dict[str, Any]) -> str | None:
        user = payload.get("user")
        if not isinstance(user, dict):
            return None
        value = str(user.get("login", "")).strip()
        return value or None

    def _normalize_commits(self, commits_payload: list[dict[str, Any]]) -> list[PullRequestCommit]:
        commits: list[PullRequestCommit] = []
        for item in commits_payload:
            commit_id = str(item.get("sha", "")).strip()
            if not commit_id:
                continue
            commit_info = item.get("commit") if isinstance(item.get("commit"), dict) else {}
            message = str(commit_info.get("message", "")).strip() or None
            commits.append(
                PullRequestCommit(
                    commit_id=commit_id,
                    url=str(item.get("html_url") or item.get("url") or "").strip() or None,
                    comment=message,
                )
            )
        return commits

    def _normalize_reviewers(
        self,
        payload: dict[str, Any],
        review_payload: list[dict[str, Any]],
        include_reviewers: bool | None,
    ) -> list[PullRequestReviewer]:
        if include_reviewers is False:
            return []
        if include_reviewers is None:
            requested = payload.get("requested_reviewers")
            reviewer_items = requested if isinstance(requested, list) else []
            reviewers: list[PullRequestReviewer] = []
            for item in reviewer_items:
                if not isinstance(item, dict):
                    continue
                reviewer_id = self._github_reviewer_id(item)
                if reviewer_id is None:
                    continue
                reviewers.append(
                    PullRequestReviewer(
                        reviewer_id=reviewer_id,
                        display_name=self._github_reviewer_display_name(item),
                        status="requested",
                    )
                )
            return reviewers

        latest_review_by_reviewer: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
        for index, review in enumerate(review_payload):
            reviewer_id = self._github_reviewer_id(review)
            if reviewer_id is None:
                continue
            sort_key = self._github_review_sort_key(review, index)
            existing = latest_review_by_reviewer.get(reviewer_id)
            if existing is None or sort_key > existing[:2]:
                latest_review_by_reviewer[reviewer_id] = (sort_key[0], sort_key[1], review)

        reviewers = [
            self._review_to_reviewer(reviewer_id=reviewer_id, review=review)
            for reviewer_id, (_sort_time, _sort_index, review) in latest_review_by_reviewer.items()
        ]

        requested = payload.get("requested_reviewers")
        requested_reviewers = requested if isinstance(requested, list) else []
        present_ids = {
            reviewer.reviewer_id.casefold() for reviewer in reviewers if reviewer.reviewer_id
        }
        for item in requested_reviewers:
            if not isinstance(item, dict):
                continue
            reviewer_id = self._github_reviewer_id(item)
            if reviewer_id is None or reviewer_id.casefold() in present_ids:
                continue
            reviewers.append(
                PullRequestReviewer(
                    reviewer_id=reviewer_id,
                    display_name=self._github_reviewer_display_name(item),
                    status="requested",
                )
            )
            present_ids.add(reviewer_id.casefold())
        return reviewers

    def _github_reviewer_id(self, payload: dict[str, Any]) -> str | None:
        user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
        if not isinstance(user, dict):
            return None
        value = str(user.get("login") or user.get("id") or "").strip()
        return value or None

    def _github_reviewer_display_name(self, payload: dict[str, Any]) -> str | None:
        user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
        if not isinstance(user, dict):
            return None
        value = str(user.get("login") or user.get("name") or user.get("id") or "").strip()
        return value or None

    def _github_review_sort_key(self, review: dict[str, Any], index: int) -> tuple[datetime, int]:
        submitted_at = str(review.get("submitted_at") or review.get("submittedAt") or "").strip()
        if submitted_at:
            try:
                return (_parse_dt(submitted_at), index)
            except ValueError:
                pass
        return (datetime.min.replace(tzinfo=UTC), index)

    def _review_to_reviewer(
        self, *, reviewer_id: str, review: dict[str, Any]
    ) -> PullRequestReviewer:
        user = review.get("user") if isinstance(review.get("user"), dict) else {}
        state = str(review.get("state", "")).strip().lower() or None
        vote_by_state = {
            "approved": 10,
            "changes_requested": -10,
            "commented": 0,
            "dismissed": None,
            "pending": None,
        }
        return PullRequestReviewer(
            reviewer_id=reviewer_id,
            display_name=str(user.get("login") or user.get("name") or reviewer_id or "").strip()
            or None,
            vote=vote_by_state.get(state),
            status=state,
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


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = [value] if isinstance(value, str) else value
    if not isinstance(raw_items, (list, tuple, set)):
        raise ValueError("pull request tag/status filters must be a string or list of strings")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _expand_pull_request_statuses(
    *,
    status: str | None = None,
    statuses: list[str] | None = None,
) -> list[str | None]:
    raw_values = _normalize_string_list(statuses)
    if not raw_values and status is not None:
        raw_values = _normalize_string_list(status)
    if not raw_values:
        return [None]
    normalized: list[str | None] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = raw.strip().lower()
        expanded = ["completed", "abandoned"] if value == "closed" else [value]
        for item in expanded:
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
    return normalized or [None]


def _activity_statuses(statuses: list[str] | None = None) -> list[str]:
    raw = _normalize_string_list(statuses) or ["active", "completed", "abandoned"]
    return [item for item in _expand_pull_request_statuses(statuses=raw) if item is not None]


def _activity_max_candidates(value: int) -> int:
    return max(1, min(int(value), 1000))


@dataclass(frozen=True)
class _PullRequestTagExpression:
    kind: Literal["tag", "and", "or", "not"]
    value: str | None = None
    children: tuple["_PullRequestTagExpression", ...] = ()


@dataclass(frozen=True)
class _PullRequestTagToken:
    kind: Literal["tag", "operator", "left_paren", "right_paren"]
    value: str


class _PullRequestTagExpressionParser:
    def __init__(self, expression: str) -> None:
        self._tokens = _tokenize_pull_request_tag_expression(expression)
        self._index = 0

    def parse(self) -> _PullRequestTagExpression:
        if not self._tokens:
            raise ValueError("pull request tag expression must not be empty")
        expression = self._parse_or()
        trailing = self._peek()
        if trailing is not None:
            raise ValueError(f"unexpected token {trailing.value!r} in pull request tag expression")
        return expression

    def _parse_or(self) -> _PullRequestTagExpression:
        expression = self._parse_and()
        while self._match_operator("or"):
            expression = _PullRequestTagExpression(
                kind="or",
                children=(expression, self._parse_and()),
            )
        return expression

    def _parse_and(self) -> _PullRequestTagExpression:
        expression = self._parse_not()
        while self._match_operator("and"):
            expression = _PullRequestTagExpression(
                kind="and",
                children=(expression, self._parse_not()),
            )
        return expression

    def _parse_not(self) -> _PullRequestTagExpression:
        if self._match_operator("not"):
            return _PullRequestTagExpression(kind="not", children=(self._parse_not(),))
        return self._parse_primary()

    def _parse_primary(self) -> _PullRequestTagExpression:
        token = self._peek()
        if token is None:
            raise ValueError("expected tag or '(' in pull request tag expression")
        if token.kind == "tag":
            self._index += 1
            return _PullRequestTagExpression(kind="tag", value=token.value)
        if token.kind == "left_paren":
            self._index += 1
            expression = self._parse_or()
            closing = self._peek()
            if closing is None or closing.kind != "right_paren":
                raise ValueError("missing ')' in pull request tag expression")
            self._index += 1
            return expression
        raise ValueError(f"expected tag or '(' before {token.value!r}")

    def _match_operator(self, operator: str) -> bool:
        token = self._peek()
        if token is None or token.kind != "operator" or token.value != operator:
            return False
        self._index += 1
        return True

    def _peek(self) -> _PullRequestTagToken | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]


def _parse_pull_request_tag_expression(
    expression: str | None,
) -> _PullRequestTagExpression | None:
    if expression is None:
        return None
    if not isinstance(expression, str):
        raise ValueError("pull request tags must be a string")
    text = expression.strip()
    if not text:
        return None
    return _PullRequestTagExpressionParser(text).parse()


def _tokenize_pull_request_tag_expression(expression: str) -> list[_PullRequestTagToken]:
    tokens: list[_PullRequestTagToken] = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char.isspace():
            index += 1
            continue
        if char == "(":
            tokens.append(_PullRequestTagToken("left_paren", char))
            index += 1
            continue
        if char == ")":
            tokens.append(_PullRequestTagToken("right_paren", char))
            index += 1
            continue
        if char in {"'", '"'}:
            value, index = _read_quoted_pull_request_tag(expression, index)
            tokens.append(_PullRequestTagToken("tag", value))
            continue

        start = index
        while index < len(expression) and not expression[index].isspace():
            if expression[index] in {"(", ")"}:
                break
            index += 1
        raw_value = expression[start:index].strip()
        if not raw_value:
            continue
        normalized = raw_value.casefold()
        if normalized in {"and", "or", "not"}:
            tokens.append(_PullRequestTagToken("operator", normalized))
        else:
            tokens.append(_PullRequestTagToken("tag", raw_value))
    return tokens


def _read_quoted_pull_request_tag(expression: str, start: int) -> tuple[str, int]:
    quote_char = expression[start]
    index = start + 1
    value_chars: list[str] = []
    while index < len(expression):
        char = expression[index]
        if char == "\\" and index + 1 < len(expression):
            value_chars.append(expression[index + 1])
            index += 2
            continue
        if char == quote_char:
            value = "".join(value_chars).strip()
            if not value:
                raise ValueError("pull request tag expression contains an empty quoted tag")
            return value, index + 1
        value_chars.append(char)
        index += 1
    raise ValueError("unterminated quoted tag in pull request tag expression")


def _pull_request_tags_match(
    tags: list[str],
    *,
    tag_filter: _PullRequestTagExpression,
) -> bool:
    present = {tag.casefold() for tag in tags}
    return _evaluate_pull_request_tag_expression(tag_filter, present)


def _evaluate_pull_request_tag_expression(
    expression: _PullRequestTagExpression,
    present_tags: set[str],
) -> bool:
    if expression.kind == "tag":
        return bool(expression.value and expression.value.casefold() in present_tags)
    if expression.kind == "and":
        return all(
            _evaluate_pull_request_tag_expression(child, present_tags)
            for child in expression.children
        )
    if expression.kind == "or":
        return any(
            _evaluate_pull_request_tag_expression(child, present_tags)
            for child in expression.children
        )
    if expression.kind == "not":
        return not _evaluate_pull_request_tag_expression(expression.children[0], present_tags)
    raise ValueError(f"unknown pull request tag expression node: {expression.kind}")


def _azure_pr_web_url(payload: dict[str, Any]) -> str | None:
    return (
        str(payload.get("_links", {}).get("web", {}).get("href") or "").strip()
        or str(payload.get("remoteUrl") or payload.get("url") or "").strip()
        or None
    )


def normalize_pr_automation_provider(provider: str) -> str:
    normalized = provider.strip()
    if normalized in {"ado", "azure_devops", "azure-devops"}:
        return "azure_devops"
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
        project: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        provider_name = normalize_pr_automation_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR query provider {provider_name!r} is not configured for this gateway instance"
            )
        status_values = _expand_pull_request_statuses(status=status, statuses=statuses)
        merged_page = len(status_values) > 1
        merged_skip = max(0, int(skip or 0)) if merged_page else skip
        merged_fetch_top = (merged_skip or 0) + top if merged_page and top is not None else top
        responses = [
            self.providers[provider_name].list_pull_requests(
                repo=repo,
                project=project,
                status=status_value,
                creator_id=creator_id,
                reviewer_id=reviewer_id,
                source_ref_name=source_ref_name,
                target_ref_name=target_ref_name,
                source_repository_id=source_repository_id,
                repository_id=repository_id,
                min_time=min_time,
                max_time=max_time,
                query_time_range_type=query_time_range_type,
                include_links=include_links,
                max_comment_length=max_comment_length,
                tags=tags,
                top=merged_fetch_top,
                skip=0 if merged_page else skip,
                cursor=None if merged_page else cursor,
            )
            for status_value in status_values
        ]
        if len(responses) == 1:
            response = responses[0]
            response.paging = {**response.paging, "statuses": status_values}
            return response
        merged: dict[tuple[str, str, str], PullRequestItem] = {}
        paging_by_status: dict[str, dict[str, Any]] = {}
        for status_value, response in zip(status_values, responses, strict=True):
            paging_by_status[str(status_value)] = response.paging
            for item in response.pull_requests:
                merged[(item.provider, item.repo, item.pr_id)] = item
        items = list(merged.values())
        items.sort(
            key=lambda item: item.updated_at or item.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        total_merged_count = len(items)
        page_start = int(merged_skip or 0)
        page_end = page_start + top if top is not None else None
        limited_items = items[page_start:page_end]
        has_more = (page_end is not None and total_merged_count > page_end) or any(
            bool(response.paging.get("has_more")) for response in responses
        )
        next_skip = (
            page_start + len(limited_items)
            if has_more and top is not None and limited_items
            else None
        )
        return ListPullRequestsResponse(
            provider=responses[0].provider,
            repo=responses[0].repo,
            project=responses[0].project,
            pull_requests=limited_items,
            count=len(limited_items),
            paging={
                "statuses": status_values,
                "by_status": paging_by_status,
                "returned_count": len(limited_items),
                "total_merged_count": total_merged_count,
                "has_more": has_more,
                "next_skip": next_skip,
            },
            next_cursor=None,
        )

    def resolve_ado_identity(
        self,
        *,
        query: str,
        project: str | None = None,
    ) -> ResolveAdoIdentityResponse:
        provider_name = "azure_devops"
        if provider_name not in self.providers:
            raise ValueError(
                "PR query provider 'azure_devops' is not configured for this gateway instance"
            )
        resolve_identity = getattr(self.providers[provider_name], "resolve_identity", None)
        if not callable(resolve_identity):
            raise ValueError(
                "PR query provider 'azure_devops' does not support identity resolution"
            )
        return resolve_identity(query=query, project=project)

    def list_ado_pr_comment_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        include_system_comments: bool = False,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommentActivityResponse:
        provider_name = "azure_devops"
        if provider_name not in self.providers:
            raise ValueError(
                "PR query provider 'azure_devops' is not configured for this gateway instance"
            )
        list_activity = getattr(self.providers[provider_name], "list_pr_comment_activity", None)
        if not callable(list_activity):
            raise ValueError(
                "PR query provider 'azure_devops' does not support PR comment activity"
            )
        return list_activity(
            repo=repo,
            author=author,
            since=since,
            until=until,
            project=project,
            statuses=statuses,
            include_system_comments=include_system_comments,
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )

    def list_ado_pr_commit_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommitActivityResponse:
        provider_name = "azure_devops"
        if provider_name not in self.providers:
            raise ValueError(
                "PR query provider 'azure_devops' is not configured for this gateway instance"
            )
        list_activity = getattr(self.providers[provider_name], "list_pr_commit_activity", None)
        if not callable(list_activity):
            raise ValueError("PR query provider 'azure_devops' does not support PR commit activity")
        return list_activity(
            repo=repo,
            author=author,
            since=since,
            until=until,
            project=project,
            statuses=statuses,
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )

    def update_pull_request(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        project: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        expected_statuses: list[str] | None = None,
    ) -> UpdatePullRequestResponse:
        provider_name = normalize_pr_automation_provider(provider)
        if provider_name not in self.providers:
            raise ValueError(
                f"PR query provider {provider_name!r} is not configured for this gateway instance"
            )
        update_pull_request = getattr(self.providers[provider_name], "update_pull_request", None)
        if not callable(update_pull_request):
            raise ValueError(f"PR update provider {provider_name!r} does not support updates")
        return update_pull_request(
            repo=repo,
            pr_id=pr_id,
            project=project,
            title=title,
            description=description,
            status=status,
            add_tags=add_tags,
            remove_tags=remove_tags,
            expected_statuses=expected_statuses,
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
        provider_name = normalize_pr_automation_provider(provider)
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
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

        normalized_mentions = normalize_ado_mention_refs(mentions)
        reply_kwargs: dict[str, Any] = {
            "repo": repo,
            "pr_id": pr_id,
            "thread_id": thread_id,
            "comment_id": comment_id,
            "body_text": body_text.strip(),
            "attachments": attachments,
        }
        if normalized_mentions:
            reply_kwargs["mentions"] = normalized_mentions
        response = self.providers[provider_name].post_reply(**reply_kwargs)
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

    def submit_review(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _reject_unsupported_mentions("GitHub", mentions)
        _reject_unsupported_attachments("GitHub", attachments)
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _reject_unsupported_mentions("GitHub", mentions)
        _reject_unsupported_attachments("GitHub", attachments)
        normalized_body = body_text.strip()
        normalized_comments = _normalize_pr_review_comments(comments)
        if any(comment.get("mentions") for comment in normalized_comments):
            raise ValueError("GitHub PR review comments do not support ADO mentions")
        _reject_unsupported_comment_attachments("GitHub", normalized_comments)
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
        mentions: list[str] | None = None,
        resolved_mentions: list[AdoResolvedMention] | None = None,
        thread_context: dict[str, Any] | None = None,
        pull_request_thread_context: dict[str, Any] | None = None,
        error_context: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if resolved_mentions is None:
            resolved_mentions = resolve_ado_mentions(
                client=client,
                organization_url=self.organization_url,
                pat=self.pat,
                mention_refs=mentions,
            )
        uploaded_attachments = _upload_azure_devops_pr_attachments(
            client=client,
            organization_url=self.organization_url,
            project=self.project,
            pat=self.pat,
            repo=repo,
            pr_id=pr_id,
            attachments=attachments,
        )
        body_with_mentions = render_ado_mentions_prefix(body_text, resolved_mentions)
        content = _append_markdown_attachment_links(body_with_mentions, uploaded_attachments)
        payload: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": content.strip(), "commentType": "text"}],
            "status": "active",
        }
        if thread_context is not None:
            payload["threadContext"] = thread_context
        if pull_request_thread_context is not None:
            payload["pullRequestThreadContext"] = pull_request_thread_context
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
            "uploaded_attachments": uploaded_attachments,
            "resolved_mentions": _ado_resolved_mention_payloads(resolved_mentions),
        }

    def post_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
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
                mentions=mentions,
                error_context="top-level PR comment",
                attachments=attachments,
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
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
        summary_resolved_mentions: list[dict[str, str]] = []
        inline_comment_ids: list[str] = []
        inline_comment_urls: list[str] = []
        inline_published_feedback_ids: list[str] = []
        inline_resolved_mentions: list[list[dict[str, str]]] = []
        uploaded_attachments: list[dict[str, Any]] = []

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resolved_summary_mentions = resolve_ado_mentions(
                client=client,
                organization_url=self.organization_url,
                pat=self.pat,
                mention_refs=mentions,
            )
            summary_resolved_mentions = _ado_resolved_mention_payloads(resolved_summary_mentions)
            iteration_context = (
                _azure_devops_pr_iteration_context(
                    client=client,
                    organization_url=self.organization_url,
                    project=self.project,
                    repo_identifier=self._repo_identifier(repo),
                    pr_id=pr_id,
                    pat=self.pat,
                )
                if normalized_comments
                else None
            )
            total_inline_comments = len(normalized_comments)
            prepared_inline_comments: list[
                tuple[
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any] | None,
                    str,
                    list[AdoResolvedMention],
                ]
            ] = []
            for index, comment in enumerate(normalized_comments, start=1):
                resolved_comment_mentions = resolve_ado_mentions(
                    client=client,
                    organization_url=self.organization_url,
                    pat=self.pat,
                    mention_refs=comment.get("mentions"),
                )
                thread_context = _azure_devops_thread_context_for_review_comment(comment)
                pull_request_thread_context = (
                    _azure_devops_pull_request_thread_context_for_review_comment(
                        comment,
                        iteration_context=iteration_context,
                    )
                    if iteration_context is not None
                    else None
                )
                error_context = _azure_devops_inline_comment_error_context(
                    comment,
                    index=index,
                    total=total_inline_comments,
                )
                prepared_inline_comments.append(
                    (
                        comment,
                        thread_context,
                        pull_request_thread_context,
                        error_context,
                        resolved_comment_mentions,
                    )
                )

            for (
                comment,
                thread_context,
                pull_request_thread_context,
                error_context,
                resolved_comment_mentions,
            ) in prepared_inline_comments:
                try:
                    comment_result = self._post_thread_comment(
                        client=client,
                        repo=repo,
                        pr_id=pr_id,
                        body_text=comment["body_text"],
                        resolved_mentions=resolved_comment_mentions,
                        thread_context=thread_context,
                        pull_request_thread_context=pull_request_thread_context,
                        error_context=error_context,
                        attachments=comment.get("attachments"),
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
                inline_resolved_mentions.append(list(comment_result.get("resolved_mentions") or []))
                uploaded_attachments.extend(comment_result.get("uploaded_attachments") or [])

            if normalized_body:
                summary_result = self._post_thread_comment(
                    client=client,
                    repo=repo,
                    pr_id=pr_id,
                    body_text=normalized_body,
                    resolved_mentions=resolved_summary_mentions,
                    error_context="review summary comment",
                    attachments=attachments,
                )
                summary_thread_id = summary_result["thread_id"]
                summary_comment_id = summary_result["comment_id"]
                summary_comment_url = summary_result["comment_url"]
                if summary_comment_id is not None:
                    comment_ids.append(summary_comment_id)
                if summary_comment_url:
                    comment_urls.append(summary_comment_url)
                published_feedback_ids.extend(summary_result["published_feedback_ids"])
                summary_resolved_mentions = list(summary_result.get("resolved_mentions") or [])
                uploaded_attachments.extend(summary_result.get("uploaded_attachments") or [])

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
            "resolved_mentions": summary_resolved_mentions,
            "inline_resolved_mentions": inline_resolved_mentions,
            "comment_ids": comment_ids,
            "comment_urls": comment_urls,
            "published_feedback_ids": published_feedback_ids,
            "uploaded_attachments": uploaded_attachments,
        }


@dataclass(frozen=True)
class _AzureDevOpsPrChange:
    change_tracking_id: int
    change_type: str
    current_paths: tuple[str, ...]
    original_paths: tuple[str, ...]


@dataclass(frozen=True)
class _AzureDevOpsPrIterationContext:
    first_iteration_id: int
    latest_iteration_id: int
    changes: tuple[_AzureDevOpsPrChange, ...]


def _azure_devops_thread_context_for_review_comment(comment: dict[str, Any]) -> dict[str, Any]:
    thread_context: dict[str, Any] = {"filePath": _azure_devops_comment_file_path(comment["path"])}
    line = comment.get("line")
    if line is None:
        return thread_context

    side = str(comment.get("side") or "RIGHT").strip().upper()
    position_prefix = "leftFile" if side == "LEFT" else "rightFile"
    start_line = comment.get("start_line") or line
    thread_context[f"{position_prefix}Start"] = {"line": start_line, "offset": 1}
    thread_context[f"{position_prefix}End"] = {"line": line, "offset": 1}
    return thread_context


def _azure_devops_pr_iteration_context(
    *,
    client: httpx.Client,
    organization_url: str,
    project: str,
    repo_identifier: str,
    pr_id: str,
    pat: str,
) -> _AzureDevOpsPrIterationContext:
    base_url = (
        f"{organization_url}/{project}/_apis/git/repositories/{repo_identifier}/"
        f"pullRequests/{pr_id}/iterations"
    )
    response = client.get(
        base_url,
        params={"api-version": "7.1"},
        auth=("", pat),
    )
    _raise_azure_devops_pr_publish_error(
        response,
        action="resolve PR iterations for inline comment anchoring",
        context=f"pr_id={pr_id}",
    )
    payload = response.json()
    raw_iterations = payload.get("value") if isinstance(payload, dict) else payload
    if not isinstance(raw_iterations, list):
        raw_iterations = []
    iteration_ids = sorted(
        {
            iteration_id
            for item in raw_iterations
            if isinstance(item, dict)
            for iteration_id in [_as_int_or_none(item.get("id"))]
            if iteration_id is not None and iteration_id > 0
        }
    )
    if not iteration_ids:
        raise ValueError(
            f"Azure DevOps PR {pr_id} has no iterations; cannot anchor inline comments"
        )

    latest_iteration_id = iteration_ids[-1]
    changes: list[_AzureDevOpsPrChange] = []
    skip = 0
    while True:
        params: dict[str, Any] = {
            "api-version": "7.1",
            "$top": 2000,
        }
        if skip:
            params["$skip"] = skip
        changes_response = client.get(
            f"{base_url}/{latest_iteration_id}/changes",
            params=params,
            auth=("", pat),
        )
        _raise_azure_devops_pr_publish_error(
            changes_response,
            action="resolve PR iteration changes for inline comment anchoring",
            context=f"pr_id={pr_id} iteration={latest_iteration_id}",
        )
        changes_payload = changes_response.json()
        raw_changes = (
            changes_payload.get("changeEntries") or changes_payload.get("value")
            if isinstance(changes_payload, dict)
            else []
        )
        if not isinstance(raw_changes, list):
            raw_changes = []
        for raw_change in raw_changes:
            change = _azure_devops_pr_change_from_payload(raw_change)
            if change is not None:
                changes.append(change)
        next_skip = (
            _as_int_or_none(changes_payload.get("nextSkip"))
            if isinstance(changes_payload, dict)
            else None
        )
        if not next_skip:
            break
        skip = next_skip

    if not changes:
        raise ValueError(
            f"Azure DevOps PR {pr_id} latest iteration {latest_iteration_id} has no changes; "
            "cannot anchor inline comments"
        )

    return _AzureDevOpsPrIterationContext(
        first_iteration_id=iteration_ids[0],
        latest_iteration_id=latest_iteration_id,
        changes=tuple(changes),
    )


def _azure_devops_pr_change_from_payload(raw_change: Any) -> _AzureDevOpsPrChange | None:
    if not isinstance(raw_change, dict):
        return None
    change_tracking_id = _as_int_or_none(raw_change.get("changeTrackingId"))
    if change_tracking_id is None:
        return None
    current_paths = []
    item = raw_change.get("item")
    if isinstance(item, dict):
        current_paths.append(item.get("path"))
    current_paths.append(raw_change.get("sourceServerItem"))
    original_paths = [raw_change.get("originalPath")]
    normalized_current_paths = tuple(
        dict.fromkeys(
            path
            for raw_path in current_paths
            for path in [_normalize_azure_devops_pr_path(raw_path)]
            if path is not None
        )
    )
    normalized_original_paths = tuple(
        dict.fromkeys(
            path
            for raw_path in original_paths
            for path in [_normalize_azure_devops_pr_path(raw_path)]
            if path is not None
        )
    )
    if not normalized_current_paths and not normalized_original_paths:
        return None
    change_type = str(raw_change.get("changeType") or "").strip().lower()
    return _AzureDevOpsPrChange(
        change_tracking_id=change_tracking_id,
        change_type=change_type,
        current_paths=normalized_current_paths,
        original_paths=normalized_original_paths,
    )


def _azure_devops_pull_request_thread_context_for_review_comment(
    comment: dict[str, Any],
    *,
    iteration_context: _AzureDevOpsPrIterationContext,
) -> dict[str, Any]:
    path = _normalize_azure_devops_pr_path(comment["path"])
    if path is None:
        raise ValueError("Azure DevOps inline comment requires a file path")
    side = str(comment.get("side") or "RIGHT").strip().upper()
    matching_change = next(
        (
            change
            for change in iteration_context.changes
            if path
            in (
                (*change.original_paths, *change.current_paths)
                if side == "LEFT"
                else change.current_paths
            )
        ),
        None,
    )
    if matching_change is None:
        raise ValueError(
            f"Azure DevOps inline comment path {comment['path']!r} is not present in the latest "
            f"PR iteration {iteration_context.latest_iteration_id}; refusing to post a detached "
            "inline comment"
        )

    change_types = {
        part.strip()
        for part in matching_change.change_type.replace(";", ",").split(",")
        if part.strip()
    }
    if side != "LEFT" and "delete" in change_types:
        raise ValueError(
            f"Azure DevOps inline comment path {comment['path']!r} is deleted in the latest "
            "PR iteration; use side=LEFT or a top-level comment instead"
        )

    return {
        "changeTrackingId": matching_change.change_tracking_id,
        "iterationContext": {
            "firstComparingIteration": iteration_context.first_iteration_id,
            "secondComparingIteration": iteration_context.latest_iteration_id,
        },
    }


def _normalize_azure_devops_pr_path(path: Any) -> str | None:
    normalized = _normalize_optional_text(path)
    if normalized is None:
        return None
    return "/" + normalized.lstrip("/")


def _azure_devops_comment_file_path(path: Any) -> str:
    normalized = _normalize_azure_devops_pr_path(path)
    if normalized is None:
        raise ValueError("Azure DevOps inline comment requires a file path")
    return normalized


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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._publish(
            operation="post_comment",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text=body_text,
            mentions=mentions,
            attachments=attachments,
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._publish(
            operation="submit_review",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text=body_text,
            mentions=mentions,
            attachments=attachments,
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
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
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
        normalized_mentions = normalize_ado_mention_refs(mentions)
        if normalized_mentions:
            comment["mentions"] = normalized_mentions
        if attachments:
            comment["attachments"] = attachments
        return self._publish(
            operation="post_inline_comment",
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            body_text="",
            mentions=None,
            attachments=None,
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
        mentions: list[str] | None,
        attachments: list[dict[str, Any]] | None,
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
        normalized_mentions = normalize_ado_mention_refs(mentions)
        if operation == "post_comment":
            comment_kwargs: dict[str, Any] = {
                "repo": repo,
                "pr_id": pr_id,
                "body_text": body_text.strip(),
                "attachments": attachments,
            }
            if normalized_mentions:
                comment_kwargs["mentions"] = normalized_mentions
            response = provider.post_comment(**comment_kwargs)
        elif operation in {"submit_review", "post_inline_comment"}:
            review_kwargs: dict[str, Any] = {
                "repo": repo,
                "pr_id": pr_id,
                "body_text": body_text.strip(),
                "comments": comments,
                "event": event,
                "attachments": attachments,
            }
            if normalized_mentions:
                review_kwargs["mentions"] = normalized_mentions
            response = provider.submit_review(**review_kwargs)
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
        mentions = normalize_ado_mention_refs(raw_comment.get("mentions"))
        if mentions:
            normalized_comment["mentions"] = mentions
        raw_attachments = raw_comment.get("attachments")
        if raw_attachments is None:
            raw_attachments = raw_comment.get("artifacts")
        if raw_attachments is not None:
            if not isinstance(raw_attachments, list):
                raise ValueError(f"PR review comment at index {index} attachments must be a list")
            normalized_comment["attachments"] = [
                item for item in raw_attachments if isinstance(item, dict)
            ]
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
