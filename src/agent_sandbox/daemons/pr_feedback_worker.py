from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import BaseModel, Field

from agent_sandbox.audit import AuditEventType, AuditLogger
from agent_sandbox.config import GatewayConfig
from agent_sandbox.inline_media import (
    InlineMediaDownload,
    download_url_with_limit,
    inline_media_download_from_embedded_payload,
    require_provider_media_url,
    sanitize_inline_media_metadata,
)
from agent_sandbox.models import InlineMediaRef
from agent_sandbox.worker_directory_context import (
    WorkerDirectoryContextResolver,
    log_shadow_context,
)

logger = logging.getLogger(__name__)

_FEEDBACK_RETENTION = timedelta(days=90)


_GITHUB_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_INLINE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


class _HtmlInlineMediaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self._active_anchor_index: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        normalized_tag = tag.lower()
        if normalized_tag == "img":
            src = attr_map.get("src", "").strip()
            if not src:
                return
            self.items.append(
                {
                    "tag": "img",
                    "url": src,
                    "alt": attr_map.get("alt", "").strip(),
                    "title": attr_map.get("title", "").strip(),
                }
            )
            return
        if normalized_tag != "a":
            return
        href = attr_map.get("href", "").strip()
        if not href or not _looks_like_ado_attachment_url(href):
            return
        self.items.append(
            {
                "tag": "a",
                "url": href,
                "title": attr_map.get("title", "").strip(),
                "text": "",
            }
        )
        self._active_anchor_index = len(self.items) - 1

    def handle_data(self, data: str) -> None:
        if self._active_anchor_index is None:
            return
        item = self.items[self._active_anchor_index]
        item["text"] = (item.get("text", "") + data).strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self._active_anchor_index = None


def _looks_like_ado_attachment_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return "attachment" in path or "_apis/wit/attachments" in path


def _filename_from_url(url: str, *, fallback: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("fileName", "filename", "name"):
        values = query.get(key) or query.get(key.lower())
        if values:
            candidate = unquote(values[0]).strip()
            if candidate:
                return candidate
    filename = unquote(Path(parsed.path).name).strip()
    fallback = fallback.strip()
    if filename and (Path(filename).suffix or not Path(fallback).suffix):
        return filename
    return fallback or filename or "inline-media"


def _mime_type_from_filename(filename: str) -> str | None:
    mime_type, _encoding = mimetypes.guess_type(filename)
    return mime_type


def _media_ref(
    *,
    provider: str,
    feedback_id: str,
    comment_id: str | None,
    media_id: str,
    url: str,
    filename: str,
    mime_type: str | None,
    metadata: dict[str, Any],
) -> InlineMediaRef:
    return InlineMediaRef(
        source_kind="pr_feedback",
        provider=provider,
        resource_id=feedback_id,
        comment_id=comment_id,
        media_id=media_id,
        filename=filename,
        mime_type=mime_type,
        url=url,
        metadata=metadata,
    )


def _inline_media_id(raw: dict[str, Any], *, fallback: str) -> str:
    return str(
        raw.get("media_id")
        or raw.get("attachment_id")
        or raw.get("id")
        or raw.get("content_id")
        or raw.get("url")
        or raw.get("filename")
        or raw.get("name")
        or fallback
    ).strip()


def _raw_inline_media_ref(
    *,
    source_kind: str,
    provider: str,
    resource_id: str,
    raw_item: Any,
    index: int,
    comment_id: str | None,
) -> InlineMediaRef | None:
    if isinstance(raw_item, InlineMediaRef):
        return raw_item.model_copy(
            update={
                "source_kind": source_kind,
                "provider": provider,
                "resource_id": resource_id,
                "comment_id": raw_item.comment_id or comment_id,
                "metadata": sanitize_inline_media_metadata(raw_item.metadata),
            }
        )
    if hasattr(raw_item, "model_dump"):
        raw_item = raw_item.model_dump()
    if not isinstance(raw_item, dict):
        return None
    media_id = _inline_media_id(raw_item, fallback=str(index))
    filename = str(
        raw_item.get("filename") or raw_item.get("name") or raw_item.get("title") or ""
    ).strip()
    url = str(
        raw_item.get("url") or raw_item.get("download_url") or raw_item.get("web_url") or ""
    ).strip()
    if not filename and url:
        filename = _filename_from_url(url, fallback=f"inline-media-{index}")
    if not filename:
        filename = f"inline-media-{index}"
    if not media_id or not filename:
        return None
    raw_comment_id = raw_item.get("comment_id") or comment_id
    byte_count_raw = raw_item.get("byte_count", raw_item.get("size", raw_item.get("contentSize")))
    byte_count: int | None = None
    if byte_count_raw is not None:
        try:
            byte_count = int(byte_count_raw)
        except (TypeError, ValueError):
            byte_count = None
    mime_type = (
        raw_item.get("mime_type") or raw_item.get("content_type") or raw_item.get("contentType")
    )
    return InlineMediaRef(
        source_kind=source_kind,
        provider=provider,
        resource_id=resource_id,
        comment_id=str(raw_comment_id).strip() if raw_comment_id else None,
        media_id=media_id,
        filename=filename,
        mime_type=str(mime_type).strip() if mime_type else _mime_type_from_filename(filename),
        byte_count=byte_count,
        url=url or None,
        metadata=sanitize_inline_media_metadata(raw_item),
    )


def _inline_media_refs_from_items(
    *,
    source_kind: str,
    provider: str,
    resource_id: str,
    raw_media: object,
    comment_id: str | None,
) -> list[InlineMediaRef]:
    if not isinstance(raw_media, list):
        return []
    refs: list[InlineMediaRef] = []
    for index, item in enumerate(raw_media):
        ref = _raw_inline_media_ref(
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            raw_item=item,
            index=index,
            comment_id=comment_id,
        )
        if ref is None:
            continue
        if comment_id and ref.comment_id != comment_id:
            continue
        refs.append(ref)
    return refs


def _extract_github_markdown_inline_media(
    *,
    body: str,
    provider: str,
    feedback_id: str,
    comment_id: str | None,
) -> list[InlineMediaRef]:
    refs: list[InlineMediaRef] = []
    for index, match in enumerate(_GITHUB_MARKDOWN_IMAGE_RE.finditer(body)):
        alt_text = match.group(1).strip()
        url = match.group(2).strip()
        fallback = alt_text or f"github-inline-image-{index}"
        filename = _filename_from_url(url, fallback=fallback)
        refs.append(
            _media_ref(
                provider=provider,
                feedback_id=feedback_id,
                comment_id=comment_id,
                media_id=f"markdown-image-{index}",
                url=url,
                filename=filename,
                mime_type=_mime_type_from_filename(filename),
                metadata=sanitize_inline_media_metadata(
                    {"source": "markdown_image", "alt": alt_text}
                ),
            )
        )
    return refs


def _extract_ado_inline_media(
    *,
    content: str,
    provider: str,
    feedback_id: str,
    comment_id: str | None,
) -> list[InlineMediaRef]:
    parser = _HtmlInlineMediaParser()
    parser.feed(content)
    refs: list[InlineMediaRef] = []
    for index, item in enumerate(parser.items):
        url = item["url"].strip()
        fallback = (
            item.get("alt")
            or item.get("title")
            or item.get("text")
            or f"azure-devops-inline-media-{index}"
        ).strip()
        filename = _filename_from_url(url, fallback=fallback)
        mime_type = _mime_type_from_filename(filename)
        if item.get("tag") == "a" and mime_type is not None:
            extension = Path(filename).suffix.lower()
            if extension and extension not in _INLINE_IMAGE_EXTENSIONS:
                continue
        refs.append(
            _media_ref(
                provider=provider,
                feedback_id=feedback_id,
                comment_id=comment_id,
                media_id=f"ado-{item.get('tag', 'media')}-{index}",
                url=url,
                filename=filename,
                mime_type=mime_type,
                metadata=sanitize_inline_media_metadata(
                    {
                        "source": "html_image" if item.get("tag") == "img" else "ado_attachment_link",
                        "alt": item.get("alt", ""),
                        "title": item.get("title", ""),
                        "text": item.get("text", ""),
                    }
                ),
            )
        )
    return refs


def _inline_media_refs_from_resource(
    *,
    source_kind: str,
    provider: str,
    resource_id: str,
    resource: dict[str, Any] | None,
    comment_id: str | None = None,
) -> list[InlineMediaRef]:
    if not isinstance(resource, dict):
        return []
    refs = _inline_media_refs_from_items(
        source_kind=source_kind,
        provider=provider,
        resource_id=resource_id,
        raw_media=resource.get("inline_media"),
        comment_id=comment_id,
    )
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    metadata_media = metadata.get("inline_media") or metadata.get("images")
    refs.extend(
        _inline_media_refs_from_items(
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            raw_media=metadata_media,
            comment_id=comment_id,
        )
    )
    return refs


def pr_feedback_item_payload(raw_feedback: dict[str, Any] | "FeedbackEnvelope") -> dict[str, Any]:
    payload = (
        raw_feedback.model_dump(mode="json")
        if isinstance(raw_feedback, FeedbackEnvelope)
        else dict(raw_feedback)
    )
    provider = str(payload.get("provider") or "").strip()
    feedback_id = str(payload.get("feedback_id") or "").strip()
    comment_id = str(payload.get("comment_id") or "").strip() or None
    payload["inline_media"] = [
        ref.model_dump(mode="json")
        for ref in _inline_media_refs_from_resource(
            source_kind="pr_feedback",
            provider=provider,
            resource_id=feedback_id,
            resource=payload,
            comment_id=comment_id,
        )
    ]
    return payload


def _download_url_with_optional_headers(
    *,
    url: str,
    headers: dict[str, str],
    timeout_seconds: float = 30.0,
    max_bytes: int | None = None,
    provider: str | None = None,
    allowed_hosts: Iterable[str] | None = None,
) -> tuple[bytes, str | None]:
    return download_url_with_limit(
        url=url,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        provider=provider,
        allowed_hosts=allowed_hosts,
    )


class FeedbackEnvelope(BaseModel):
    feedback_id: str
    provider: str
    repo: str
    pr_id: str
    thread_id: str | None = None
    comment_id: str | None = None
    author: str
    body: str
    file_path: str | None = None
    line: int | None = None
    created_at: datetime
    in_reply_to: str | None = None
    resolved: bool | None = None
    inline_media: list[dict[str, Any] | InlineMediaRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ProviderPollResult:
    feedback: list[FeedbackEnvelope]
    next_cursor: str | None


class FeedbackProviderConfigurationError(RuntimeError):
    """Raised when a feedback provider cannot poll due to local configuration."""


class FeedbackProviderAuthError(RuntimeError):
    """Raised when a feedback provider rejects the configured credentials."""


class PrFeedbackRepository(Protocol):
    """Repository contract for persisted PR feedback state."""

    def get_cursor(self, stream_key: str) -> str | None: ...

    def set_cursor(self, stream_key: str, cursor: str | None) -> None: ...

    def add_feedback_if_new(self, envelope: FeedbackEnvelope) -> bool: ...

    def get_session_cursor(self, session_id: str, stream_key: str) -> str | None: ...

    def advance_session_cursor(
        self, session_id: str, stream_key: str, cursor: str | None
    ) -> str | None: ...

    def list_feedback_for_streams(
        self,
        *,
        streams: list[dict[str, str]],
        after: dict[tuple[str, str, str], str | None],
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]: ...

    def mark_session_streams_read(
        self,
        *,
        session_id: str,
        streams: list[dict[str, str]],
        cursor: str | None,
    ) -> dict[str, str | None]: ...

    def has_unread_feedback(self, *, session_id: str, streams: list[dict[str, str]]) -> bool: ...

    def read_raw(self) -> dict[str, Any]: ...

    def prune_streams(self, streams: set[tuple[str, str, str]]) -> tuple[int, int]: ...


class PrFeedbackStore:
    """Atomic JSON-backed store for feedback, provider cursors, and session read cursors."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"cursors": {}, "feedback": {}, "session_reads": {}})

    def get_cursor(self, stream_key: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            cursor = payload.get("cursors", {}).get(stream_key)
            return str(cursor) if cursor is not None else None

    def set_cursor(self, stream_key: str, cursor: str | None) -> None:
        with self._lock:
            payload = self._read_raw()
            cursors = payload.setdefault("cursors", {})
            if cursor is None:
                cursors.pop(stream_key, None)
            else:
                cursors[stream_key] = cursor
            self._compact_feedback(payload)
            self._atomic_write(payload)

    def add_feedback_if_new(self, envelope: FeedbackEnvelope) -> bool:
        with self._lock:
            payload = self._read_raw()
            stored_feedback = payload.setdefault("feedback", {})
            if envelope.feedback_id in stored_feedback:
                return False
            stored_feedback[envelope.feedback_id] = envelope.model_dump(mode="json")
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return True

    def get_session_cursor(self, session_id: str, stream_key: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            raw_session_reads = payload.get("session_reads", {})
            if not isinstance(raw_session_reads, dict):
                return None
            session_reads = raw_session_reads.get(session_id, {})
            if not isinstance(session_reads, dict):
                return None
            cursor = session_reads.get(stream_key)
            return str(cursor) if cursor is not None else None

    def advance_session_cursor(
        self, session_id: str, stream_key: str, cursor: str | None
    ) -> str | None:
        with self._lock:
            payload = self._read_raw()
            session_reads = payload.setdefault("session_reads", {})
            if not isinstance(session_reads, dict):
                session_reads = {}
                payload["session_reads"] = session_reads
            current_reads = session_reads.get(session_id)
            if not isinstance(current_reads, dict):
                current_reads = {}
                session_reads[session_id] = current_reads

            current_cursor = current_reads.get(stream_key)
            next_cursor = _latest_cursor(
                [
                    str(current_cursor) if current_cursor is not None else None,
                    cursor,
                ]
            )
            if next_cursor is None:
                current_reads.pop(stream_key, None)
            else:
                current_reads[stream_key] = next_cursor
            if not current_reads:
                session_reads.pop(session_id, None)
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return next_cursor

    def has_unread_feedback(self, *, session_id: str, streams: list[dict[str, str]]) -> bool:
        with self._lock:
            payload = self._read_raw()
            feedback_items = payload.get("feedback", {})
            if not isinstance(feedback_items, dict):
                return False
            for stream in streams:
                stream_key = _stream_key(stream["provider"], stream["repo"], stream["pr_id"])
                session_cursor = self._get_session_cursor_from_payload(
                    payload, session_id, stream_key
                )
                if self._stream_has_unread_feedback(
                    feedback_items=feedback_items,
                    provider=stream["provider"],
                    repo=stream["repo"],
                    pr_id=stream["pr_id"],
                    cursor=session_cursor,
                ):
                    return True
            return False

    def list_feedback_for_streams(
        self,
        *,
        streams: list[dict[str, str]],
        after: dict[tuple[str, str, str], str | None],
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]:
        with self._lock:
            payload = self._read_raw()
            feedback_items = payload.get("feedback", {})
            if not isinstance(feedback_items, dict):
                return []
            allowed_streams = {
                (stream["provider"], stream["repo"], stream["pr_id"]) for stream in streams
            }
            ignored_feedback_ids = exclude_feedback_ids or set()
            items: list[FeedbackEnvelope] = []
            for raw_value in feedback_items.values():
                if not isinstance(raw_value, dict):
                    continue
                candidate = FeedbackEnvelope.model_validate(raw_value)
                if candidate.feedback_id in ignored_feedback_ids:
                    continue
                key = (candidate.provider, candidate.repo, candidate.pr_id)
                if key not in allowed_streams:
                    continue
                effective_cursor = after.get(key)
                if effective_cursor and candidate.created_at <= _parse_dt(effective_cursor):
                    continue
                items.append(candidate)
            items.sort(key=lambda item: item.created_at)
            if limit is not None:
                return items[:limit]
            return items

    def mark_session_streams_read(
        self,
        *,
        session_id: str,
        streams: list[dict[str, str]],
        cursor: str | None,
    ) -> dict[str, str | None]:
        with self._lock:
            payload = self._read_raw()
            session_reads = payload.setdefault("session_reads", {})
            if not isinstance(session_reads, dict):
                session_reads = {}
                payload["session_reads"] = session_reads
            current_reads = session_reads.get(session_id)
            if not isinstance(current_reads, dict):
                current_reads = {}
                session_reads[session_id] = current_reads

            updated: dict[str, str | None] = {}
            for stream in streams:
                stream_key = _stream_key(stream["provider"], stream["repo"], stream["pr_id"])
                current_cursor = current_reads.get(stream_key)
                next_cursor = _latest_cursor(
                    [
                        str(current_cursor) if current_cursor is not None else None,
                        cursor,
                    ]
                )
                if next_cursor is None:
                    current_reads.pop(stream_key, None)
                else:
                    current_reads[stream_key] = next_cursor
                updated[stream_key] = next_cursor

            if not current_reads:
                session_reads.pop(session_id, None)
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return updated

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def prune_streams(self, streams: set[tuple[str, str, str]]) -> tuple[int, int]:
        if not streams:
            return 0, 0
        with self._lock:
            payload = self._read_raw()
            removed_cursor_count = 0
            cursors = payload.get("cursors")
            if isinstance(cursors, dict):
                for provider, repo, pr_id in streams:
                    stream_key = _stream_key(provider, repo, pr_id)
                    if stream_key in cursors:
                        cursors.pop(stream_key, None)
                        removed_cursor_count += 1

            removed_feedback_count = 0
            feedback = payload.get("feedback")
            if isinstance(feedback, dict):
                kept_feedback: dict[str, object] = {}
                for feedback_id, raw_feedback in feedback.items():
                    if not isinstance(raw_feedback, dict):
                        kept_feedback[feedback_id] = raw_feedback
                        continue
                    key = (
                        str(raw_feedback.get("provider", "")).strip(),
                        str(raw_feedback.get("repo", "")).strip(),
                        str(raw_feedback.get("pr_id", "")).strip(),
                    )
                    if key in streams:
                        removed_feedback_count += 1
                        continue
                    kept_feedback[feedback_id] = raw_feedback
                payload["feedback"] = kept_feedback

            if removed_cursor_count or removed_feedback_count:
                self._atomic_write(payload)
            return removed_cursor_count, removed_feedback_count

    @staticmethod
    def _get_session_cursor_from_payload(
        payload: dict[str, Any], session_id: str, stream_key: str
    ) -> str | None:
        raw_session_reads = payload.get("session_reads", {})
        if not isinstance(raw_session_reads, dict):
            return None
        session_reads = raw_session_reads.get(session_id, {})
        if not isinstance(session_reads, dict):
            return None
        cursor = session_reads.get(stream_key)
        return str(cursor) if cursor is not None else None

    @staticmethod
    def _stream_has_unread_feedback(
        *,
        feedback_items: dict[str, Any],
        provider: str,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> bool:
        cursor_dt = _parse_dt(cursor) if cursor else None
        for raw_value in feedback_items.values():
            if not isinstance(raw_value, dict):
                continue
            if (
                str(raw_value.get("provider")) != provider
                or str(raw_value.get("repo")) != repo
                or str(raw_value.get("pr_id")) != pr_id
            ):
                continue
            created_at = _parse_dt(raw_value.get("created_at"))
            if cursor_dt and created_at <= cursor_dt:
                continue
            return True
        return False

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _compact_feedback(self, payload: dict[str, Any]) -> None:
        feedback = payload.get("feedback")
        cursors = payload.get("cursors")
        if not isinstance(feedback, dict) or not isinstance(cursors, dict):
            return

        cutoff = datetime.now(timezone.utc) - _FEEDBACK_RETENTION
        compacted: dict[str, Any] = {}
        changed = False
        for feedback_id, raw_value in feedback.items():
            if not isinstance(raw_value, dict):
                compacted[feedback_id] = raw_value
                continue
            provider = str(raw_value.get("provider", "")).strip()
            repo = str(raw_value.get("repo", "")).strip()
            pr_id = str(raw_value.get("pr_id", "")).strip()
            if not provider or not repo or not pr_id:
                compacted[feedback_id] = raw_value
                continue
            created_at = _parse_dt(raw_value.get("created_at"))
            stream_cursor = cursors.get(_stream_key(provider, repo, pr_id))
            if (
                created_at < cutoff
                and stream_cursor is not None
                and created_at <= _parse_dt(stream_cursor)
            ):
                changed = True
                continue
            compacted[feedback_id] = raw_value
        if changed:
            payload["feedback"] = compacted

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


def _stream_key(provider: str, repo: str, pr_id: str) -> str:
    return f"{provider}:{repo}:{pr_id}"


class FeedbackProviderAdapter(Protocol):
    provider_name: str

    def poll(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> ProviderPollResult: ...


class GitHubFeedbackAdapter:
    provider_name = "github"

    def __init__(
        self, api_base_url: str = "https://api.github.com", token: str | None = None
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.token = token.strip() if token else None

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def poll(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> ProviderPollResult:
        issue_comments = self._get_issue_comments(client, repo, pr_id, cursor)
        review_comments = self._get_review_comments(client, repo, pr_id, cursor)
        reviews = self._get_reviews(client, repo, pr_id)

        feedback: list[FeedbackEnvelope] = []
        feedback.extend(self._map_issue_comments(repo=repo, pr_id=pr_id, comments=issue_comments))
        feedback.extend(self._map_review_comments(repo=repo, pr_id=pr_id, comments=review_comments))
        feedback.extend(self._map_reviews(repo=repo, pr_id=pr_id, reviews=reviews, cursor=cursor))

        ordered = sorted(feedback, key=lambda item: item.created_at)
        next_cursor = _latest_cursor([cursor, *[item.created_at.isoformat() for item in ordered]])
        return ProviderPollResult(feedback=ordered, next_cursor=next_cursor)

    def list_inline_media(
        self,
        *,
        source_kind: str,
        resource_id: str,
        resource: dict[str, Any] | None = None,
        comment_id: str | None = None,
    ) -> list[InlineMediaRef]:
        return _inline_media_refs_from_resource(
            source_kind=source_kind,
            provider=self.provider_name,
            resource_id=resource_id,
            resource=resource,
            comment_id=comment_id,
        )

    def download_inline_media(
        self,
        *,
        media: InlineMediaRef,
        raw: dict[str, Any] | None = None,
        max_bytes: int | None = None,
    ) -> InlineMediaDownload:
        embedded = inline_media_download_from_embedded_payload(media, raw)
        if embedded is not None:
            return embedded
        if not media.url:
            raise ValueError("GitHub inline media does not include a download URL")
        require_provider_media_url(provider=self.provider_name, url=media.url)
        media_host = urlparse(media.url).hostname or ""
        api_host = urlparse(self.api_base_url).hostname or ""
        headers = self._headers() if media_host.lower() == api_host.lower() else {}
        content, mime_type = _download_url_with_optional_headers(
            url=media.url,
            headers=headers,
            max_bytes=max_bytes,
            provider=self.provider_name,
        )
        return InlineMediaDownload(content, media.filename, mime_type or media.mime_type)

    def _get_issue_comments(
        self, client: httpx.Client, repo: str, pr_id: str, cursor: str | None
    ) -> list[dict[str, Any]]:
        url = f"{self.api_base_url}/repos/{repo}/issues/{pr_id}/comments"
        params: dict[str, str] = {"sort": "updated", "direction": "asc", "per_page": "100"}
        if cursor:
            params["since"] = cursor
        response = client.get(url, params=params, headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def _get_review_comments(
        self, client: httpx.Client, repo: str, pr_id: str, cursor: str | None
    ) -> list[dict[str, Any]]:
        url = f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/comments"
        params: dict[str, str] = {"sort": "updated", "direction": "asc", "per_page": "100"}
        if cursor:
            params["since"] = cursor
        response = client.get(url, params=params, headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def _get_reviews(self, client: httpx.Client, repo: str, pr_id: str) -> list[dict[str, Any]]:
        url = f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}/reviews"
        response = client.get(url, params={"per_page": "100"}, headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _map_issue_comments(
        *, repo: str, pr_id: str, comments: list[dict[str, Any]]
    ) -> list[FeedbackEnvelope]:
        mapped: list[FeedbackEnvelope] = []
        for comment in comments:
            comment_id = str(comment.get("id"))
            feedback_id = f"github:issue_comment:{comment_id}"
            body = (comment.get("body") or "").strip()
            created_at = _parse_dt(comment.get("created_at") or comment.get("updated_at"))
            mapped.append(
                FeedbackEnvelope(
                    feedback_id=feedback_id,
                    provider="github",
                    repo=repo,
                    pr_id=str(pr_id),
                    thread_id=f"issue_comment:{comment_id}",
                    comment_id=comment_id,
                    author=_github_author(comment.get("user")),
                    body=body,
                    created_at=created_at,
                    inline_media=_extract_github_markdown_inline_media(
                        body=body,
                        provider="github",
                        feedback_id=feedback_id,
                        comment_id=comment_id,
                    ),
                )
            )
        return mapped

    @staticmethod
    def _map_review_comments(
        *, repo: str, pr_id: str, comments: list[dict[str, Any]]
    ) -> list[FeedbackEnvelope]:
        mapped: list[FeedbackEnvelope] = []
        for comment in comments:
            comment_id = str(comment.get("id"))
            feedback_id = f"github:review_comment:{comment_id}"
            body = (comment.get("body") or "").strip()
            review_id = comment.get("pull_request_review_id")
            line = (
                comment.get("line")
                if isinstance(comment.get("line"), int)
                else comment.get("original_line")
            )
            mapped.append(
                FeedbackEnvelope(
                    feedback_id=feedback_id,
                    provider="github",
                    repo=repo,
                    pr_id=str(pr_id),
                    thread_id=str(review_id) if review_id is not None else None,
                    comment_id=comment_id,
                    author=_github_author(comment.get("user")),
                    body=body,
                    file_path=comment.get("path"),
                    line=line if isinstance(line, int) else None,
                    created_at=_parse_dt(comment.get("created_at") or comment.get("updated_at")),
                    in_reply_to=(
                        str(comment.get("in_reply_to_id"))
                        if comment.get("in_reply_to_id") is not None
                        else None
                    ),
                    inline_media=_extract_github_markdown_inline_media(
                        body=body,
                        provider="github",
                        feedback_id=feedback_id,
                        comment_id=comment_id,
                    ),
                )
            )
        return mapped

    @staticmethod
    def _map_reviews(
        *, repo: str, pr_id: str, reviews: list[dict[str, Any]], cursor: str | None
    ) -> list[FeedbackEnvelope]:
        mapped: list[FeedbackEnvelope] = []
        cursor_dt = _parse_dt(cursor) if cursor else None
        for review in reviews:
            submitted_or_created = _parse_dt(review.get("submitted_at") or review.get("created_at"))
            if cursor_dt and submitted_or_created <= cursor_dt:
                continue
            review_id = str(review.get("id"))
            feedback_id = f"github:review:{review_id}"
            body = (review.get("body") or "").strip()
            mapped.append(
                FeedbackEnvelope(
                    feedback_id=feedback_id,
                    provider="github",
                    repo=repo,
                    pr_id=str(pr_id),
                    thread_id=review_id,
                    comment_id=review_id,
                    author=_github_author(review.get("user")),
                    body=body,
                    created_at=submitted_or_created,
                    inline_media=_extract_github_markdown_inline_media(
                        body=body,
                        provider="github",
                        feedback_id=feedback_id,
                        comment_id=review_id,
                    ),
                    resolved=None,
                )
            )
        return mapped


class AzureDevOpsFeedbackAdapter:
    provider_name = "azure_devops"

    def __init__(
        self,
        organization_url: str,
        project: str,
        pat: str | None = None,
        *,
        pat_env_name: str = "AZURE_DEVOPS_PR_PAT",
    ) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat.strip() if pat else None
        self.pat_env_name = pat_env_name.strip() or "AZURE_DEVOPS_PR_PAT"

    def _headers(self) -> dict[str, str]:
        if not self.pat:
            return {}
        token = base64.b64encode(f":{self.pat}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def list_inline_media(
        self,
        *,
        source_kind: str,
        resource_id: str,
        resource: dict[str, Any] | None = None,
        comment_id: str | None = None,
    ) -> list[InlineMediaRef]:
        return _inline_media_refs_from_resource(
            source_kind=source_kind,
            provider=self.provider_name,
            resource_id=resource_id,
            resource=resource,
            comment_id=comment_id,
        )

    def download_inline_media(
        self,
        *,
        media: InlineMediaRef,
        raw: dict[str, Any] | None = None,
        max_bytes: int | None = None,
    ) -> InlineMediaDownload:
        if not self.pat:
            raise FeedbackProviderConfigurationError(
                "Azure DevOps inline media downloads require a PAT in "
                f"environment variable {self.pat_env_name!r}."
            )
        embedded = inline_media_download_from_embedded_payload(media, raw)
        if embedded is not None:
            return embedded
        if not media.url:
            raise ValueError("Azure DevOps inline media does not include a download URL")
        allowed_host = urlparse(self.organization_url).hostname
        require_provider_media_url(
            provider=self.provider_name,
            url=media.url,
            allowed_hosts=[allowed_host] if allowed_host else [],
        )
        content, mime_type = _download_url_with_optional_headers(
            url=media.url,
            headers=self._headers(),
            max_bytes=max_bytes,
            provider=self.provider_name,
            allowed_hosts=[allowed_host] if allowed_host else [],
        )
        return InlineMediaDownload(content, media.filename, mime_type or media.mime_type)

    def poll(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> ProviderPollResult:
        if not self.pat:
            raise FeedbackProviderConfigurationError(
                "Azure DevOps feedback polling requires a PAT in "
                f"environment variable {self.pat_env_name!r}."
            )
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        url = (
            f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
            f"pullRequests/{pr_id}/threads"
        )
        response = client.get(url, params={"api-version": "7.1"}, headers=self._headers())
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise FeedbackProviderAuthError(
                    "Azure DevOps feedback polling was rejected with 401 Unauthorized. "
                    f"Check environment variable {self.pat_env_name!r}."
                ) from exc
            raise
        payload = response.json()
        threads = payload.get("value", []) if isinstance(payload, dict) else []
        cursor_dt = _parse_dt(cursor) if cursor else None

        feedback: list[FeedbackEnvelope] = []
        seen_timestamps: list[str] = []
        for thread in threads:
            thread_id = str(thread.get("id"))
            resolved = _ado_thread_resolved(thread.get("status"))
            thread_context = (
                thread.get("threadContext") if isinstance(thread.get("threadContext"), dict) else {}
            )
            file_path = thread_context.get("filePath")
            line = _ado_line_from_context(thread_context)
            for comment in thread.get("comments", []):
                if comment.get("isDeleted"):
                    continue
                created_at = _parse_dt(
                    comment.get("publishedDate") or comment.get("lastUpdatedDate")
                )
                if cursor_dt and created_at <= cursor_dt:
                    continue
                seen_timestamps.append(created_at.isoformat())
                comment_id = str(comment.get("id"))
                parent_comment_id = comment.get("parentCommentId")
                in_reply_to = (
                    str(parent_comment_id)
                    if isinstance(parent_comment_id, int) and parent_comment_id > 0
                    else None
                )
                author = (comment.get("author", {}).get("displayName") or "unknown").strip()
                body = (comment.get("content") or "").strip()
                feedback_id = f"azure_devops:thread_comment:{thread_id}:{comment_id}"
                if _ado_should_skip_comment(
                    author=author,
                    body=body,
                    comment_type=comment.get("commentType"),
                    file_path=file_path,
                ):
                    continue
                feedback.append(
                    FeedbackEnvelope(
                        feedback_id=feedback_id,
                        provider="azure_devops",
                        repo=repo,
                        pr_id=str(pr_id),
                        thread_id=thread_id,
                        comment_id=comment_id,
                        author=author,
                        body=body,
                        file_path=file_path,
                        line=line,
                        created_at=created_at,
                        in_reply_to=in_reply_to,
                        resolved=resolved,
                        inline_media=_extract_ado_inline_media(
                            content=body,
                            provider="azure_devops",
                            feedback_id=feedback_id,
                            comment_id=comment_id,
                        ),
                    )
                )

        ordered = sorted(feedback, key=lambda item: item.created_at)
        next_cursor = _latest_cursor([cursor, *seen_timestamps])
        return ProviderPollResult(feedback=ordered, next_cursor=next_cursor)


_ADO_SYSTEM_STATE_CHANGE_PATTERNS = (
    re.compile(r"^.+ voted -?\d+$"),
    re.compile(r"^.+ approved the changes$"),
    re.compile(r"^.+ approved with suggestions$"),
    re.compile(r"^.+ waiting for author$"),
    re.compile(r"^.+ declined to review$"),
    re.compile(r"^.+ reset all code reviewer votes$"),
    re.compile(r"^.+ rejected this pull request$"),
    re.compile(r"^.+ updated the pull request status to .+$"),
    re.compile(r"^the reference refs/heads/.+ was updated\.$"),
    re.compile(r"^.+ joined as a reviewer$"),
    re.compile(r"^.+ added .+ as a reviewer$"),
    re.compile(r"^.+ is changed to be a required reviewer$"),
    re.compile(r"^.+ is no longer a required reviewer$"),
    re.compile(r"^.+ removed .+ as a reviewer$"),
)


def _ado_should_skip_comment(
    *,
    author: str,
    body: str,
    comment_type: object,
    file_path: str | None,
) -> bool:
    comment_type_value = str(comment_type or "").strip().lower()
    if comment_type_value == "system":
        return True
    if file_path:
        return False
    normalized_body = body.strip().casefold()
    if not normalized_body:
        return False
    return any(pattern.fullmatch(normalized_body) for pattern in _ADO_SYSTEM_STATE_CHANGE_PATTERNS)


class PullRequestFeedbackWorker:
    def __init__(
        self,
        *,
        store: PrFeedbackStore,
        adapters: dict[str, FeedbackProviderAdapter],
        audit_logger: AuditLogger,
        directory_context_resolver: WorkerDirectoryContextResolver | None = None,
    ) -> None:
        self.store = store
        self.adapters = adapters
        self.audit_logger = audit_logger
        self.directory_context_resolver = directory_context_resolver

    def poll_once(
        self,
        *,
        client: httpx.Client,
        provider: str,
        repo: str,
        pr_id: str,
        stream_bindings: list[dict[str, str | None]] | None = None,
        adapter: FeedbackProviderAdapter | None = None,
    ) -> list[FeedbackEnvelope]:
        adapter = adapter or self.adapters[provider]
        stream_key = f"{provider}:{repo}:{pr_id}"
        cursor = self.store.get_cursor(stream_key)
        result = adapter.poll(client=client, repo=repo, pr_id=pr_id, cursor=cursor)
        resolved_bindings = self._resolve_stream_bindings(
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            stream_bindings=stream_bindings,
        )

        discovered: list[FeedbackEnvelope] = []
        for envelope in result.feedback:
            if not self.store.add_feedback_if_new(envelope):
                continue
            discovered.append(envelope)
            self._log_discovery(envelope, stream_bindings=resolved_bindings)

        self.store.set_cursor(stream_key, result.next_cursor)
        return discovered

    def _resolve_stream_bindings(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        stream_bindings: list[dict[str, str | None]] | None,
    ) -> list[dict[str, Any]]:
        bindings = [dict(binding) for binding in (stream_bindings or [])]
        if self.directory_context_resolver is None:
            return bindings

        resolved: list[dict[str, Any]] = []
        for binding in bindings:
            session_id = str(binding.get("session_id") or "").strip() or None
            fallback_directory_path = str(binding.get("directory_path") or "").strip() or None
            source = (
                str(binding.get("source") or "pr_feedback_worker").strip() or "pr_feedback_worker"
            )
            context = self.directory_context_resolver.resolve_for_session(
                session_id,
                fallback_directory_path=fallback_directory_path,
                source=source,
            )
            binding["directory_path"] = context.directory_path
            binding["directory_context"] = context.as_log_dict()
            log_shadow_context(
                logger,
                prefix=f"provider={provider} repo={repo} pr={pr_id} session_id={session_id or 'unknown'} shadow_readiness",
                context=context,
            )
            resolved.append(binding)
        return resolved

    def _log_discovery(
        self,
        envelope: FeedbackEnvelope,
        *,
        stream_bindings: list[dict[str, str | None]] | None = None,
    ) -> None:
        bindings = stream_bindings or []
        logger.info(
            "Discovered new PR feedback provider=%s repo=%s pr=%s feedback_id=%s stream_bindings=%s",
            envelope.provider,
            envelope.repo,
            envelope.pr_id,
            envelope.feedback_id,
            bindings,
        )
        self.audit_logger.log_decision(
            event_type=AuditEventType.FEEDBACK_DETECTED,
            session_id=bindings[0]["session_id"] if bindings else None,
            vm_id=bindings[0]["vm_id"] if bindings else None,
            provider=envelope.provider,
            repo=envelope.repo,
            pr_id=envelope.pr_id,
            decision="detected",
            reason="new-feedback",
            args={
                "feedback": envelope.model_dump(mode="json"),
                "stream_bindings": bindings,
            },
        )


def _github_author(user: Any) -> str:
    if not isinstance(user, dict):
        return "unknown"
    return (user.get("login") or user.get("name") or "unknown").strip()


def _ado_thread_resolved(status: Any) -> bool | None:
    if not isinstance(status, str):
        return None
    normalized = status.strip().lower()
    if normalized in {"closed", "fixed", "wontfix", "bydesign"}:
        return True
    if normalized in {"active", "pending"}:
        return False
    return None


def _ado_line_from_context(thread_context: dict[str, Any]) -> int | None:
    right = thread_context.get("rightFileStart")
    if isinstance(right, dict) and isinstance(right.get("line"), int):
        return right["line"]
    left = thread_context.get("leftFileStart")
    if isinstance(left, dict) and isinstance(left.get("line"), int):
        return left["line"]
    return None


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


def _latest_cursor(candidates: list[str | None]) -> str | None:
    datetimes = [_parse_dt(value) for value in candidates if value]
    if not datetimes:
        return None
    latest = max(datetimes)
    return latest.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.min.replace(tzinfo=timezone.utc)
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_pr_feedback_worker(
    runtime_dir: Path,
    *,
    adapters: dict[str, FeedbackProviderAdapter],
    gateway_config: GatewayConfig | None = None,
) -> PullRequestFeedbackWorker:
    store = PrFeedbackStore(runtime_dir / "pr-feedback-store.json")
    audit = AuditLogger(runtime_dir / "audit.log")
    directory_context_resolver = (
        WorkerDirectoryContextResolver(gateway_config)
        if gateway_config is not None and gateway_config.enable_directory_scoped_config
        else None
    )
    return PullRequestFeedbackWorker(
        store=store,
        adapters=adapters,
        audit_logger=audit,
        directory_context_resolver=directory_context_resolver,
    )
