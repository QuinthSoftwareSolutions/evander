from __future__ import annotations

import base64
import hashlib
import html
import mimetypes
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from evander.inline_media import (
    InlineMediaDownload,
    download_url_with_limit,
    inline_media_download_from_embedded_payload,
    require_provider_media_url,
    sanitize_inline_media_metadata,
)
from evander.models import InlineMediaRef, PrFeedbackAttachmentRef
from evander.pr_feedback.store import (
    FeedbackEnvelope,
    FeedbackProviderAuthError,
    FeedbackProviderConfigurationError,
    ProviderPollResult,
    _latest_cursor,
    _parse_dt,
)

_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MARKDOWN_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_ADO_COMPACT_MENTION_ID_RE = re.compile(
    r"@<\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*>"
)
_INLINE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_MAX_ADO_PATH_DECODE_PASSES = 5
ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY = "ado_pr_feedback_attachment_scope"


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


class _AdoMentionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.mentioned_names: list[str] = []
        self._capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            normalized_name = name.lower()
            normalized_value = (value or "").lower()
            if normalized_name == "data-vss-mention" or "mention" in normalized_value:
                self._capture_depth += 1
                return

    def handle_data(self, data: str) -> None:
        if not self._capture_depth:
            return
        text = html.unescape(data).strip()
        if text:
            self.mentioned_names.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._capture_depth:
            self._capture_depth -= 1


def _unique_non_empty(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = re.sub(r"\s+", " ", text).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _ado_author_field(comment: dict[str, Any], key: str) -> str | None:
    author = comment.get("author")
    if not isinstance(author, dict):
        return None
    value = author.get(key)
    text = str(value or "").strip()
    return text or None


def _ado_comment_is_before(
    candidate: dict[str, Any],
    current: dict[str, Any],
    *,
    candidate_index: int,
    current_index: int,
) -> bool:
    candidate_id = str(candidate.get("id") or "").strip()
    current_id = str(current.get("id") or "").strip()
    if candidate_id and current_id:
        if candidate_id == current_id:
            return False
        try:
            return int(candidate_id) < int(current_id)
        except ValueError:
            return candidate_index < current_index
    return candidate_index < current_index


def _extract_ado_mention_names(content: str) -> list[str]:
    parser = _AdoMentionParser()
    parser.feed(content or "")
    return _unique_non_empty(parser.mentioned_names)


def _extract_ado_mention_ids(content: str) -> list[str]:
    return _unique_non_empty(
        match.group(1).casefold() for match in _ADO_COMPACT_MENTION_ID_RE.finditer(content or "")
    )


def _ado_comment_metadata(
    *,
    comments: list[dict[str, Any]],
    current_comment: dict[str, Any],
    current_index: int,
    body: str,
) -> dict[str, Any]:
    prior_comments = [
        candidate
        for candidate_index, candidate in enumerate(comments)
        if not candidate.get("isDeleted")
        and _ado_comment_is_before(
            candidate,
            current_comment,
            candidate_index=candidate_index,
            current_index=current_index,
        )
    ]
    return {
        "comment_author_names": _unique_non_empty(
            [_ado_author_field(current_comment, "displayName")]
        ),
        "comment_author_unique_names": _unique_non_empty(
            [_ado_author_field(current_comment, "uniqueName")]
        ),
        "comment_author_ids": _unique_non_empty([_ado_author_field(current_comment, "id")]),
        "prior_thread_comment_author_names": _unique_non_empty(
            _ado_author_field(comment, "displayName") for comment in prior_comments
        ),
        "prior_thread_comment_author_unique_names": _unique_non_empty(
            _ado_author_field(comment, "uniqueName") for comment in prior_comments
        ),
        "prior_thread_comment_author_ids": _unique_non_empty(
            _ado_author_field(comment, "id") for comment in prior_comments
        ),
        "mentioned_names": _extract_ado_mention_names(body),
        "mentioned_ids": _extract_ado_mention_ids(body),
        "thread_comment_count": len(
            [comment for comment in comments if not comment.get("isDeleted")]
        ),
    }


def _looks_like_ado_attachment_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return "/_apis/wit/attachments/" in path or (
        "/_apis/git/repositories/" in path and "/pullrequests/" in path and "/attachments/" in path
    )


def _fully_unquote_ado_path_segment(segment: str) -> str:
    decoded = segment
    for _ in range(_MAX_ADO_PATH_DECODE_PASSES):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded
    if unquote(decoded) != decoded:
        raise ValueError("Azure DevOps attachment URL path is too deeply percent-encoded")
    return decoded


def _canonical_ado_path_segments(path: str) -> list[str]:
    if not path.startswith("/") or path.startswith("//") or path.endswith("/"):
        raise ValueError("Azure DevOps attachment URL path is not canonical")
    raw_segments = path[1:].split("/")
    if not raw_segments or any(not segment for segment in raw_segments):
        raise ValueError("Azure DevOps attachment URL path is not canonical")
    segments: list[str] = []
    for raw_segment in raw_segments:
        segment = _fully_unquote_ado_path_segment(raw_segment)
        if segment in {".", ".."}:
            raise ValueError("Azure DevOps attachment URL path must not contain dot segments")
        if "/" in segment or "\\" in segment:
            raise ValueError(
                "Azure DevOps attachment URL path must not contain encoded path separators"
            )
        segments.append(segment)
    return segments


def _ado_expected_segment(value: str, *, field_name: str) -> str:
    segment = _fully_unquote_ado_path_segment(str(value or "").strip())
    if not segment or segment in {".", ".."} or "/" in segment or "\\" in segment:
        raise ValueError(f"Azure DevOps {field_name} is not a canonical path segment")
    return segment


def require_ado_pr_feedback_attachment_url(
    *,
    url: str,
    organization_url: str,
    project: str,
    repo: str,
    pr_id: str,
) -> None:
    """Require one canonical, feedback-scoped Azure DevOps attachment endpoint."""

    configured = urlparse(organization_url)
    candidate = urlparse(url)
    allowed_host = configured.hostname
    require_provider_media_url(
        provider="azure_devops",
        url=url,
        allowed_hosts=[allowed_host] if allowed_host else [],
    )
    try:
        candidate_port = candidate.port
    except ValueError as exc:
        raise ValueError("Azure DevOps attachment URL has an invalid port") from exc
    if (
        candidate.username is not None
        or candidate.password is not None
        or candidate_port is not None
        or candidate.params
        or candidate.fragment
    ):
        raise ValueError("Azure DevOps attachment URL contains unsupported authority or URL parts")

    configured_segments = (
        _canonical_ado_path_segments(configured.path.rstrip("/"))
        if configured.path.rstrip("/")
        else []
    )
    if (configured.hostname or "").casefold() == "dev.azure.com":
        if len(configured_segments) != 1:
            raise ValueError("Azure DevOps organization URL must identify exactly one organization")
        root_segments = [configured_segments[0]]
    elif (configured.hostname or "").casefold().endswith(".visualstudio.com"):
        if configured_segments:
            raise ValueError("Azure DevOps organization URL must not include a path")
        root_segments = []
    else:
        raise ValueError("Azure DevOps organization URL host is not supported")

    expected_project = _ado_expected_segment(project, field_name="project")
    expected_repo = _ado_expected_segment(
        _normalize_azure_devops_repo_identifier(repo),
        field_name="repository",
    )
    expected_pr_id = _ado_expected_segment(pr_id, field_name="pull request id")
    segments = _canonical_ado_path_segments(candidate.path)
    if [segment.casefold() for segment in segments[: len(root_segments)]] != [
        segment.casefold() for segment in root_segments
    ]:
        raise ValueError(
            "Azure DevOps attachment URL organization does not match the feedback stream"
        )
    route = segments[len(root_segments) :]
    folded_route = [segment.casefold() for segment in route]
    if len(route) == 4 and folded_route[:3] == ["_apis", "wit", "attachments"]:
        return
    if not route or route[0].casefold() != expected_project.casefold():
        raise ValueError("Azure DevOps attachment URL project does not match the feedback stream")
    route = route[1:]
    folded_route = [segment.casefold() for segment in route]
    if len(route) == 4 and folded_route[:3] == ["_apis", "wit", "attachments"]:
        return
    if (
        len(route) == 8
        and folded_route[:3] == ["_apis", "git", "repositories"]
        and route[3].casefold() == expected_repo.casefold()
        and folded_route[4] == "pullrequests"
        and route[5].casefold() == expected_pr_id.casefold()
        and folded_route[6] == "attachments"
    ):
        return
    raise ValueError(
        "Azure DevOps attachment URL is not a canonical attachment route for the feedback stream"
    )


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


def _extract_markdown_inline_media(
    *,
    body: str,
    provider: str,
    feedback_id: str,
    comment_id: str | None,
    media_id_prefix: str = "markdown-image",
    fallback_prefix: str = "github-inline-image",
    metadata_source: str = "markdown_image",
    url_filter: Callable[[str], bool] | None = None,
) -> list[InlineMediaRef]:
    refs: list[InlineMediaRef] = []
    for index, match in enumerate(_MARKDOWN_IMAGE_RE.finditer(body)):
        alt_text = match.group(1).strip()
        url = match.group(2).strip()
        if url_filter is not None and not url_filter(url):
            continue
        fallback = alt_text or f"{fallback_prefix}-{index}"
        filename = _filename_from_url(url, fallback=fallback)
        refs.append(
            _media_ref(
                provider=provider,
                feedback_id=feedback_id,
                comment_id=comment_id,
                media_id=f"{media_id_prefix}-{index}",
                url=url,
                filename=filename,
                mime_type=_mime_type_from_filename(filename),
                metadata=sanitize_inline_media_metadata(
                    {"source": metadata_source, "alt": alt_text}
                ),
            )
        )
    return refs


def _extract_github_markdown_inline_media(
    *,
    body: str,
    provider: str,
    feedback_id: str,
    comment_id: str | None,
) -> list[InlineMediaRef]:
    return _extract_markdown_inline_media(
        body=body,
        provider=provider,
        feedback_id=feedback_id,
        comment_id=comment_id,
    )


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
    seen_urls: set[str] = set()
    for index, item in enumerate(parser.items):
        url = item["url"].strip()
        seen_urls.add(url)
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
                        "source": "html_image"
                        if item.get("tag") == "img"
                        else "ado_attachment_link",
                        "alt": item.get("alt", ""),
                        "title": item.get("title", ""),
                        "text": item.get("text", ""),
                    }
                ),
            )
        )
    markdown_refs = _extract_markdown_inline_media(
        body=content,
        provider=provider,
        feedback_id=feedback_id,
        comment_id=comment_id,
        media_id_prefix="ado-markdown-image",
        fallback_prefix="ado-markdown-image",
        metadata_source="ado_markdown_image",
        url_filter=lambda url: _looks_like_ado_attachment_url(url) and url not in seen_urls,
    )
    refs.extend(markdown_refs)
    return refs


def extract_ado_pr_feedback_attachments(
    *,
    content: str,
    organization_url: str,
    project: str,
    feedback_id: str,
    repo: str,
    pr_id: str,
    thread_id: str | None,
    comment_id: str | None,
) -> list[PrFeedbackAttachmentRef]:
    """Extract stable Azure DevOps attachment references from one feedback body."""

    parser = _HtmlInlineMediaParser()
    parser.feed(content or "")
    candidates: list[tuple[str, str, str]] = []
    for item in parser.items:
        url = item["url"].strip()
        if not _looks_like_ado_attachment_url(url):
            continue
        fallback = (
            item.get("alt") or item.get("title") or item.get("text") or "azure-devops-attachment"
        ).strip()
        source = "html_image" if item.get("tag") == "img" else "ado_attachment_link"
        candidates.append((url, fallback, source))

    for match in _MARKDOWN_LINK_RE.finditer(content or ""):
        is_image = bool(match.group(1))
        label = match.group(2).strip()
        url = match.group(3).strip()
        if not _looks_like_ado_attachment_url(url):
            continue
        candidates.append(
            (
                url,
                label or "azure-devops-attachment",
                "ado_markdown_image" if is_image else "ado_markdown_attachment",
            )
        )

    refs: list[PrFeedbackAttachmentRef] = []
    seen_urls: set[str] = set()
    for url, fallback, source in candidates:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            require_ado_pr_feedback_attachment_url(
                url=url,
                organization_url=organization_url,
                project=project,
                repo=repo,
                pr_id=pr_id,
            )
        except ValueError:
            continue
        filename = _filename_from_url(url, fallback=fallback)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        refs.append(
            PrFeedbackAttachmentRef(
                provider="azure_devops",
                feedback_id=feedback_id,
                repo=repo,
                pr_id=pr_id,
                thread_id=thread_id,
                comment_id=comment_id,
                attachment_id=f"ado-attachment-{digest}",
                filename=filename,
                mime_type=_mime_type_from_filename(filename),
                url=url,
                metadata={"source": source},
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


def pr_feedback_item_payload(raw_feedback: dict[str, Any] | FeedbackEnvelope) -> dict[str, Any]:
    payload = (
        raw_feedback.model_dump(mode="json")
        if isinstance(raw_feedback, FeedbackEnvelope)
        else dict(raw_feedback)
    )
    provider = str(payload.get("provider") or "").strip()
    feedback_id = str(payload.get("feedback_id") or "").strip()
    payload["feedback_kind"] = _feedback_kind_from_id(feedback_id)
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


def _feedback_kind_from_id(feedback_id: str) -> str | None:
    parts = feedback_id.split(":", 2)
    if len(parts) < 3:
        return None
    return parts[1].strip() or None


def _download_url_with_optional_headers(
    *,
    url: str,
    headers: dict[str, str],
    timeout_seconds: float = 30.0,
    max_bytes: int | None = None,
    provider: str | None = None,
    allowed_hosts: Iterable[str] | None = None,
    url_validator: Callable[[str], None] | None = None,
) -> tuple[bytes, str | None]:
    return download_url_with_limit(
        url=url,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        provider=provider,
        allowed_hosts=allowed_hosts,
        url_validator=url_validator,
    )


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
        url_validator: Callable[[str], None] | None = None
        if media.source_kind in {"pr_feedback", "pr_feedback_attachment"}:
            scope = media.metadata.get(ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY)
            if not isinstance(scope, dict):
                raise ValueError("Azure DevOps PR feedback media is missing attachment scope")
            scope_repo = str(scope.get("repo") or "").strip()
            scope_pr_id = str(scope.get("pr_id") or "").strip()
            if not scope_repo or not scope_pr_id:
                raise ValueError("Azure DevOps PR feedback media has incomplete attachment scope")

            def validate_scoped_url(candidate_url: str) -> None:
                require_ado_pr_feedback_attachment_url(
                    url=candidate_url,
                    organization_url=self.organization_url,
                    project=self.project,
                    repo=scope_repo,
                    pr_id=scope_pr_id,
                )

            url_validator = validate_scoped_url
        content, mime_type = _download_url_with_optional_headers(
            url=media.url,
            headers=self._headers(),
            max_bytes=max_bytes,
            provider=self.provider_name,
            allowed_hosts=[allowed_host] if allowed_host else [],
            url_validator=url_validator,
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
            raw_comments = [
                comment for comment in thread.get("comments", []) if isinstance(comment, dict)
            ]
            for comment_index, comment in enumerate(raw_comments):
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
                        metadata=_ado_comment_metadata(
                            comments=raw_comments,
                            current_comment=comment,
                            current_index=comment_index,
                            body=body,
                        ),
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
