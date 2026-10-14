from __future__ import annotations
from evander.optional_integrations import optional_azure_devops_class, require_azure_devops_module, azure_devops_enabled

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import html
from html.parser import HTMLParser
import mimetypes
import logging
import os
from pathlib import Path
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from markdown_it import MarkdownIt
from markdown_it.token import Token

from evander.ado_query_mode import ADO_QUERY_MODE_MANUAL_WIQL, validate_ado_query_mode
from evander.issues.activity_cache import AdoActivityScanCache
from evander.provider_config import ProviderConfigResolver
from evander.text_formatting import looks_like_html, render_markdownish_text_to_html

LOGGER = logging.getLogger(__name__)

_ADO_RICH_TEXT_MEDIA_FIELDS = (
    "System.Description",
    "Microsoft.VSTS.TCM.ReproSteps",
    "Microsoft.VSTS.TCM.SystemInfo",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
)
_ADO_PROXY_SYNC_FIELDS = (
    "System.Id",
    "System.Title",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "System.State",
    "System.WorkItemType",
    "System.AreaPath",
    "System.IterationPath",
    "System.AssignedTo",
    "System.Tags",
    "System.ChangedDate",
)
_ADO_WORK_ITEMS_BATCH_LIMIT = 200
_ADO_ACTIVITY_FETCH_WORKERS = 8
_ADO_ACTIVITY_BOOKKEEPING_FIELDS = {
    "Microsoft.VSTS.Common.StateChangeDate",
    "System.AuthorizedAs",
    "System.AuthorizedDate",
    "System.BoardColumn",
    "System.BoardColumnDone",
    "System.ChangedBy",
    "System.ChangedDate",
    "System.CommentCount",
    "System.History",
    "System.PersonId",
    "System.Rev",
    "System.RevisedDate",
    "System.Watermark",
}
_ADO_ACTIVITY_BOOKKEEPING_FIELD_PREFIXES = ("WEF_",)
_IMAGE_EXTENSIONS = {
    ".apng",
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}


class _AdoHtmlImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attributes = {name.lower(): value or "" for name, value in attrs}
        src = attributes.get("src", "").strip()
        if not src:
            return
        self.images.append(
            {
                "url": src,
                "alt": attributes.get("alt", "").strip(),
                "title": attributes.get("title", "").strip(),
            }
        )


def _filename_from_media_url(url: str, *, fallback: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("fileName", "filename", "name"):
        values = query.get(key) or query.get(key.lower())
        if values:
            candidate = unquote(values[0]).strip()
            if candidate:
                return candidate
    candidate = unquote(Path(parsed.path).name).strip()
    fallback = fallback.strip()
    if candidate and (Path(candidate).suffix or not Path(fallback).suffix):
        return candidate
    return fallback or candidate or "inline-media"


def _mime_type_from_filename(filename: str) -> str | None:
    mime_type, _encoding = mimetypes.guess_type(filename)
    return mime_type


def _looks_like_media_filename(value: object) -> bool:
    filename = str(value or "").strip()
    if not filename:
        return False
    suffix = Path(filename).suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return True
    mime_type = _mime_type_from_filename(filename)
    return bool(mime_type and mime_type.startswith("image/"))


def _looks_like_ado_attachment_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return "_apis/wit/attachments" in path or "attachment" in path


def _relation_is_media_reference(rel: str, url: str, attributes: dict[str, Any]) -> bool:
    if rel.lower() == "attachedfile":
        return True
    if _looks_like_ado_attachment_url(url):
        return True
    if _looks_like_media_filename(url):
        return True
    if _looks_like_media_filename(_filename_from_media_url(url, fallback="")):
        return True
    for key in ("name", "fileName", "filename", "resourceName"):
        if _looks_like_media_filename(attributes.get(key)):
            return True
    return False


def _make_ado_media_ref(
    *,
    provider: str,
    resource_id: str,
    media_id: str,
    url: str,
    filename: str,
    comment_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source_kind": "ado_backlog",
        "provider": provider,
        "resource_id": resource_id,
        "comment_id": comment_id,
        "media_id": media_id,
        "filename": filename,
        "mime_type": _mime_type_from_filename(filename),
        "url": url,
        "metadata": metadata or {},
    }


def extract_ado_html_image_refs(
    html: str,
    *,
    provider: str,
    resource_id: str,
    media_id_prefix: str,
    field_reference_name: str | None = None,
    comment_id: str | None = None,
) -> list[dict[str, Any]]:
    """Extract ADO rich-text images, including Markdown and embedded HTML.

    Keep the existing media IDs for HTML fields. Parsing Markdown tokens also
    handles reference images, escaped destinations and code examples correctly.
    URL authorization remains the download provider's responsibility.
    """
    images: list[dict[str, str]] = []

    def collect(tokens: list[Token]) -> None:
        for token in tokens:
            if token.type == "image":
                images.append(
                    {
                        "url": str(token.attrGet("src") or ""),
                        "alt": token.content,
                        "title": str(token.attrGet("title") or ""),
                        "source": "markdown_image",
                    }
                )
            elif token.type in {"html_inline", "html_block"}:
                parser = _AdoHtmlImageParser()
                parser.feed(token.content)
                images.extend({**image, "source": "html_image"} for image in parser.images)
            elif token.children:
                collect(token.children)

    collect(MarkdownIt("commonmark").parse(html or ""))
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        fallback = item.get("alt") or item.get("title") or f"ado-inline-image-{index}"
        url = item["url"]
        filename = _filename_from_media_url(url, fallback=fallback)
        metadata = {
            "source": item["source"],
            "alt": item.get("alt", ""),
            "title": item.get("title", ""),
        }
        if field_reference_name:
            metadata["field_reference_name"] = field_reference_name
        refs.append(
            _make_ado_media_ref(
                provider=provider,
                resource_id=resource_id,
                comment_id=comment_id,
                media_id=f"{media_id_prefix}-{index}",
                url=url,
                filename=filename,
                metadata=metadata,
            )
        )
    return refs


def extract_ado_field_inline_media_refs(
    fields: dict[str, Any], *, provider: str, resource_id: str
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for field_name in _ADO_RICH_TEXT_MEDIA_FIELDS:
        value = fields.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        refs.extend(
            extract_ado_html_image_refs(
                value,
                provider=provider,
                resource_id=resource_id,
                field_reference_name=field_name,
                media_id_prefix=f"field-{field_name.replace('.', '-').lower()}-image",
            )
        )
    return refs


def _format_ado_html_field(value: str | None) -> str | None:
    if value is None:
        return None
    if not value:
        return value
    if looks_like_html(value):
        return value
    return render_markdownish_text_to_html(value)


def _parse_ado_datetime_optional(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _datetime_in_range(value: datetime | None, *, since: datetime, until: datetime | None) -> bool:
    if value is None:
        return False
    start = since.astimezone(UTC) if since.tzinfo is not None else since.replace(tzinfo=UTC)
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if normalized < start:
        return False
    if until is not None:
        end = until.astimezone(UTC) if until.tzinfo is not None else until.replace(tzinfo=UTC)
        if normalized > end:
            return False
    return True


def _ado_activity_iso(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _ado_activity_identity_values(value: Any) -> set[str]:
    values: set[str] = set()

    def add(candidate: Any) -> None:
        text = str(candidate or "").strip()
        if text:
            values.add(text.casefold())

    if isinstance(value, dict):
        for key in (
            "id",
            "descriptor",
            "subjectDescriptor",
            "displayName",
            "display_name",
            "uniqueName",
            "unique_name",
            "email",
            "name",
        ):
            add(value.get(key))
        properties = value.get("properties")
        if isinstance(properties, dict):
            for key in ("Account", "Mail", "SignInAddress"):
                raw = properties.get(key)
                if isinstance(raw, dict):
                    raw = raw.get("$value")
                add(raw)
    else:
        add(value)
    return values


def _ado_activity_identity_matches(query: str | None, value: Any) -> bool:
    text = str(query or "").strip()
    if not text:
        return True
    return text.casefold() in _ado_activity_identity_values(value)


def _ado_activity_max_candidates(value: int) -> int:
    return max(1, min(int(value), 1000))


def _ado_update_field_new_value(fields: dict[str, Any], name: str) -> Any:
    payload = fields.get(name)
    if not isinstance(payload, dict):
        return None
    return payload.get("newValue")


def _ado_activity_is_bookkeeping_field(name: str) -> bool:
    normalized = str(name or "").strip()
    if not normalized:
        return True
    if normalized in _ADO_ACTIVITY_BOOKKEEPING_FIELDS:
        return True
    return any(normalized.startswith(prefix) for prefix in _ADO_ACTIVITY_BOOKKEEPING_FIELD_PREFIXES)


def _ado_meaningful_update_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        name: payload
        for name, payload in fields.items()
        if not _ado_activity_is_bookkeeping_field(name)
    }


def _artifact_filename(artifact: dict[str, Any]) -> str:
    return (
        str(
            artifact.get("normalized_safe_filename")
            or artifact.get("filename")
            or artifact.get("original_filename")
            or "attachment"
        ).strip()
        or "attachment"
    )


def _artifact_mime_type(artifact: dict[str, Any]) -> str | None:
    mime_type = str(artifact.get("mime_type") or "").strip()
    return mime_type or _mime_type_from_filename(_artifact_filename(artifact))


def _artifact_is_image(artifact: dict[str, Any]) -> bool:
    mime_type = _artifact_mime_type(artifact)
    return bool(mime_type and mime_type.lower().startswith("image/"))


def _artifact_bytes(artifact: dict[str, Any]) -> bytes:
    path = str(artifact.get("path") or "").strip()
    if not path:
        raise ValueError("Published artifact payload is missing path")
    return Path(path).read_bytes()


def _append_html_attachment_links(
    text: str | None, attachments: list[dict[str, Any]]
) -> str | None:
    if not attachments:
        return text
    rendered = _format_ado_html_field(text or "")
    lines = [rendered or ""]
    for item in attachments:
        filename = str(item.get("filename") or "attachment").strip() or "attachment"
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        escaped_url = html.escape(url, quote=True)
        escaped_name = html.escape(filename)
        if item.get("is_image"):
            lines.append(f'<p><img src="{escaped_url}" alt="{escaped_name}" /></p>')
        else:
            lines.append(f'<p><a href="{escaped_url}">{escaped_name}</a></p>')
    return "\n".join(line for line in lines if line is not None).strip()


@dataclass(frozen=True)
class _AdoTeamFieldValue:
    field_reference_name: str
    value: str
    include_children: bool


@dataclass(frozen=True)
class _AdoCurrentIteration:
    team: str
    path: str
    start_date: str | None = None
    finish_date: str | None = None


class IssueProviderAdapter(Protocol):
    provider_name: str

    def read_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        project: str | None = None,
        include_comments: bool = True,
    ) -> dict[str, Any]: ...

    def read_issues_batch(
        self,
        *,
        remote_issue_ids: list[str],
        directory_path: str,
        project: str | None = None,
        organization_url: str | None = None,
    ) -> dict[str, Any]: ...

    def post_comment(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        comment: str,
        project: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        correlation_marker: str | None = None,
    ) -> dict[str, Any]: ...

    def delete_comment(
        self,
        *,
        remote_issue_id: str,
        comment_id: str,
        directory_path: str,
        project: str | None = None,
    ) -> dict[str, Any]: ...

    def update_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        state: str | None,
        fields: dict[str, Any] | None,
        project: str | None = None,
        expected_state: str | None = None,
        field_attachments: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]: ...

    def create_issue(
        self,
        *,
        directory_path: str,
        project: str | None = None,
        issue_type: str,
        title: str,
        description: str | None = None,
        iteration_path: str | None = None,
        acceptance_criteria: str | None = None,
        tags: list[str] | None = None,
        assigned_to: str | None = None,
        parent_backlog_item_id: str | None = None,
        fields: dict[str, Any] | None = None,
        description_attachments: list[dict[str, Any]] | None = None,
        acceptance_criteria_attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

    def query_backlog_items(
        self,
        *,
        directory_path: str,
        project: str | None = None,
        wiql: str | None = None,
        iteration: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        include_relations: bool = False,
    ) -> dict[str, Any]: ...

    def query_work_item_activity(
        self,
        *,
        directory_path: str,
        project: str | None = None,
        actor: str | None = None,
        since: datetime,
        until: datetime | None = None,
        activity_types: list[str] | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> dict[str, Any]: ...

    def list_current_iteration_backlog_items(
        self,
        *,
        directory_path: str,
        project: str | None = None,
        team: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        include_relations: bool = False,
    ) -> dict[str, Any]: ...

    def get_backlog_item_by_id(
        self,
        *,
        directory_path: str,
        backlog_item_id: str,
        project: str | None = None,
        include_comments: bool = True,
        include_relations: bool = True,
        include_updates: bool = False,
    ) -> dict[str, Any]: ...

    def delete_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        project: str | None = None,
        destroy: bool = False,
    ) -> dict[str, Any]: ...






class GitHubIssueAdapter:
    provider_name = "github"

    def __init__(
        self,
        config_resolver: ProviderConfigResolver,
        *,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.config_resolver = config_resolver
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _resolve_token(self, directory_path: str) -> str | None:
        config = self.config_resolver.resolve_for_directory(directory_path)
        token_env_name = str(getattr(config, "github_token_env", "") or "").strip()
        if not token_env_name:
            return None
        return os.environ.get(token_env_name, "").strip() or None

    def _headers(self, directory_path: str) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._resolve_token(directory_path)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def read_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        project: str | None = None,
        include_comments: bool = True,
    ) -> dict[str, Any]:
        repo = self._require_repo(project)
        issue_number = self._require_issue_number(remote_issue_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.api_base_url}/repos/{repo}/issues/{issue_number}",
                headers=self._headers(directory_path),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("GitHub issue response must be an object")
            issue = self._normalize_issue(payload, repo=repo, directory_path=directory_path)
            issue["comments"] = (
                self._fetch_comments(
                    client,
                    repo=repo,
                    issue_number=issue["issue_number"],
                    directory_path=directory_path,
                )
                if include_comments
                else []
            )
            return issue

    def read_issues_batch(
        self,
        *,
        remote_issue_ids: list[str],
        directory_path: str,
        project: str | None = None,
        organization_url: str | None = None,
    ) -> dict[str, Any]:
        del organization_url
        items: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        for remote_issue_id in remote_issue_ids:
            issue_id = str(remote_issue_id or "").strip()
            if not issue_id:
                continue
            try:
                items[issue_id] = self.read_issue(
                    remote_issue_id=issue_id,
                    directory_path=directory_path,
                    project=project,
                    include_comments=False,
                )
            except Exception as exc:  # pragma: no cover - surfaced in response for callers
                errors[issue_id] = str(exc)
        return {
            "provider": self.provider_name,
            "repo": str(project or "").strip() or None,
            "items": items,
            "count": len(items),
            "errors": errors,
        }

    def query_remote_issues(
        self,
        *,
        directory_path: str,
        repo: str | None,
        state: str | None = None,
        labels_any: list[str] | None = None,
        labels_all: list[str] | None = None,
        assignee: str | None = None,
        author: str | None = None,
        mentioned: str | None = None,
        milestone: str | None = None,
        since: str | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> dict[str, Any]:
        normalized_repo = self._require_repo(repo)
        normalized_top = max(1, min(int(top), 200))
        normalized_skip = max(0, int(skip))
        labels_any_set = {_match_text(label) for label in labels_any or [] if str(label).strip()}
        labels_all_set = {_match_text(label) for label in labels_all or [] if str(label).strip()}

        with httpx.Client(timeout=self.timeout_seconds) as client:
            if str(text_search or "").strip():
                raw_items, total_count, has_more = self._search_issues(
                    client,
                    directory_path=directory_path,
                    repo=normalized_repo,
                    state=state,
                    labels_all=labels_all or [],
                    assignee=assignee,
                    author=author,
                    mentioned=mentioned,
                    milestone=milestone,
                    since=since,
                    text_search=str(text_search or "").strip(),
                    labels_any_set=labels_any_set,
                    labels_all_set=labels_all_set,
                    top=normalized_top,
                    skip=normalized_skip,
                )
            else:
                raw_items, total_count, has_more = self._list_issues(
                    client,
                    directory_path=directory_path,
                    repo=normalized_repo,
                    state=state,
                    labels_all=labels_all or [],
                    assignee=assignee,
                    author=author,
                    mentioned=mentioned,
                    milestone=milestone,
                    since=since,
                    labels_any_set=labels_any_set,
                    labels_all_set=labels_all_set,
                    top=normalized_top,
                    skip=normalized_skip,
                )

        normalized_items = [
            self._normalize_issue(item, repo=normalized_repo, directory_path=directory_path)
            for item in raw_items
            if isinstance(item, dict)
            and not isinstance(item.get("pull_request"), dict)
            and self._labels_match(
                item, labels_any_set=labels_any_set, labels_all_set=labels_all_set
            )
        ]
        normalized_items = normalized_items[:normalized_top]
        returned_count = len(normalized_items)
        next_skip = normalized_skip + returned_count if has_more else None
        return {
            "provider": self.provider_name,
            "directory_path": directory_path,
            "repo": normalized_repo,
            "items": normalized_items,
            "count": returned_count,
            "paging": {
                "skip": normalized_skip,
                "top": normalized_top,
                "returned_count": returned_count,
                "total_matches": total_count,
                "has_more": has_more,
                "next_skip": next_skip,
            },
        }

    def post_comment(self, **_: Any) -> dict[str, Any]:
        raise ValueError("GitHub issue comment posting is not supported by IssueService")

    def delete_comment(self, **_: Any) -> dict[str, Any]:
        raise ValueError("GitHub issue comment deletion is not supported by IssueService")

    def update_issue(self, **_: Any) -> dict[str, Any]:
        raise ValueError("GitHub issue updates are not supported by IssueService")

    def create_issue(self, **_: Any) -> dict[str, Any]:
        raise ValueError("GitHub issue creation is not supported by IssueService")

    def query_backlog_items(self, **kwargs: Any) -> dict[str, Any]:
        return self.query_remote_issues(
            directory_path=str(kwargs.get("directory_path") or "root"),
            repo=str(kwargs.get("project") or "").strip() or None,
            state=None,
            labels_any=kwargs.get("tags_any") if isinstance(kwargs.get("tags_any"), list) else [],
            text_search=str(kwargs.get("text_search") or "").strip() or None,
            top=int(kwargs.get("top") or 50),
            skip=int(kwargs.get("skip") or 0),
        )

    def query_work_item_activity(self, **_: Any) -> dict[str, Any]:
        raise ValueError("GitHub issue activity queries are not supported by IssueService")

    def list_current_iteration_backlog_items(self, **_: Any) -> dict[str, Any]:
        raise ValueError("GitHub issues do not support current-iteration backlog queries")

    def get_backlog_item_by_id(
        self,
        *,
        directory_path: str,
        backlog_item_id: str,
        project: str | None = None,
        include_comments: bool = True,
        include_relations: bool = True,
        include_updates: bool = False,
    ) -> dict[str, Any]:
        del include_relations, include_updates
        return self.read_issue(
            remote_issue_id=backlog_item_id,
            directory_path=directory_path,
            project=project,
            include_comments=include_comments,
        )

    def delete_issue(self, **_: Any) -> dict[str, Any]:
        raise ValueError("GitHub issue deletion is not supported by IssueService")

    def _list_issues(
        self,
        client: httpx.Client,
        *,
        directory_path: str,
        repo: str,
        state: str | None,
        labels_all: list[str],
        assignee: str | None,
        author: str | None,
        mentioned: str | None,
        milestone: str | None,
        since: str | None,
        labels_any_set: set[str],
        labels_all_set: set[str],
        top: int,
        skip: int,
    ) -> tuple[list[dict[str, Any]], int | None, bool]:
        needs_local_label_filter = bool(labels_any_set)
        per_page = min(100, max(top + (skip if needs_local_label_filter else 0), 1))
        page = 1
        collected: list[dict[str, Any]] = []
        skipped = 0
        next_url: str | None = f"{self.api_base_url}/repos/{repo}/issues"
        params: dict[str, str] | None = {
            "state": str(state or "open").strip() or "open",
            "per_page": str(per_page),
            "page": str(page),
            "sort": "updated",
            "direction": "desc",
        }
        if labels_all:
            params["labels"] = ",".join(
                str(label).strip() for label in labels_all if str(label).strip()
            )
        if assignee:
            params["assignee"] = str(assignee).strip()
        if author:
            params["creator"] = str(author).strip()
        if mentioned:
            params["mentioned"] = str(mentioned).strip()
        if milestone:
            params["milestone"] = str(milestone).strip()
        if since:
            params["since"] = str(since).strip()

        has_more = False
        while next_url and len(collected) < top:
            response = client.get(next_url, params=params, headers=self._headers(directory_path))
            response.raise_for_status()
            payload = response.json()
            raw_items = payload if isinstance(payload, list) else []
            page_has_more_matches = False
            for item in raw_items:
                if not isinstance(item, dict) or isinstance(item.get("pull_request"), dict):
                    continue
                if not self._labels_match(
                    item,
                    labels_any_set=labels_any_set,
                    labels_all_set=labels_all_set,
                ):
                    continue
                if skipped < skip:
                    skipped += 1
                    continue
                if len(collected) >= top:
                    page_has_more_matches = True
                    break
                collected.append(item)
            next_page_url = self._next_page_url(response)
            params = None
            has_more = page_has_more_matches or bool(next_page_url)
            if not raw_items or len(collected) >= top:
                break
            next_url = next_page_url
        return collected, None, has_more

    def _search_issues(
        self,
        client: httpx.Client,
        *,
        directory_path: str,
        repo: str,
        state: str | None,
        labels_all: list[str],
        assignee: str | None,
        author: str | None,
        mentioned: str | None,
        milestone: str | None,
        since: str | None,
        text_search: str,
        labels_any_set: set[str],
        labels_all_set: set[str],
        top: int,
        skip: int,
    ) -> tuple[list[dict[str, Any]], int | None, bool]:
        qualifiers = [text_search, f"repo:{repo}", "is:issue"]
        state_qualifier = str(state or "").strip().lower()
        if state_qualifier and state_qualifier != "all":
            qualifiers.append(f"state:{state_qualifier}")
        for label in labels_all:
            label_text = str(label).strip()
            if label_text:
                qualifiers.append(f'label:"{label_text}"')
        if assignee:
            qualifiers.append(f"assignee:{str(assignee).strip()}")
        if author:
            qualifiers.append(f"author:{str(author).strip()}")
        if mentioned:
            qualifiers.append(f"mentions:{str(mentioned).strip()}")
        if milestone:
            qualifiers.append(f'milestone:"{str(milestone).strip()}"')
        if since:
            qualifiers.append(f"updated:>={str(since).strip()}")
        needs_local_label_filter = bool(labels_any_set)
        per_page = min(100, max(top + (skip if needs_local_label_filter else 0), 1))
        params: dict[str, str] | None = {
            "q": " ".join(qualifiers),
            "sort": "updated",
            "order": "desc",
            "per_page": str(per_page),
            "page": "1" if needs_local_label_filter else str((skip // max(top, 1)) + 1),
        }
        next_url: str | None = f"{self.api_base_url}/search/issues"
        collected: list[dict[str, Any]] = []
        remaining_skip = skip if needs_local_label_filter else skip % max(top, 1)
        total_count: int | None = None
        has_more = False
        while next_url and len(collected) < top:
            response = client.get(next_url, params=params, headers=self._headers(directory_path))
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
                if isinstance(payload.get("total_count"), int):
                    total_count = int(payload["total_count"])
            else:
                raw_items = []
            page_has_more_matches = False
            for item in raw_items:
                if not isinstance(item, dict) or isinstance(item.get("pull_request"), dict):
                    continue
                if not self._labels_match(
                    item,
                    labels_any_set=labels_any_set,
                    labels_all_set=labels_all_set,
                ):
                    continue
                if remaining_skip:
                    remaining_skip -= 1
                    continue
                if len(collected) >= top:
                    page_has_more_matches = True
                    break
                collected.append(item)
            next_page_url = self._next_page_url(response)
            params = None
            has_more = page_has_more_matches or bool(next_page_url)
            if not raw_items or len(collected) >= top:
                break
            next_url = next_page_url
        return collected, total_count, has_more

    def _fetch_comments(
        self,
        client: httpx.Client,
        *,
        repo: str,
        issue_number: str,
        directory_path: str,
    ) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        next_url: str | None = f"{self.api_base_url}/repos/{repo}/issues/{issue_number}/comments"
        params: dict[str, str] | None = {"per_page": "100"}
        while next_url:
            response = client.get(next_url, params=params, headers=self._headers(directory_path))
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                comments.extend(
                    self._normalize_comment(item) for item in payload if isinstance(item, dict)
                )
            next_url = self._next_page_url(response)
            params = None
        return comments

    def _normalize_issue(
        self, payload: dict[str, Any], *, repo: str, directory_path: str
    ) -> dict[str, Any]:
        number = str(payload.get("number") or "").strip()
        labels = self._label_names(payload.get("labels"))
        assignees = [
            self._user_name(item)
            for item in payload.get("assignees") or []
            if isinstance(item, dict) and self._user_name(item)
        ]
        author = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        normalized = {
            "provider": self.provider_name,
            "directory_path": directory_path,
            "repo": repo,
            "remote_issue_id": number,
            "issue_id": number,
            "issue_number": number,
            "number": int(number) if number.isdigit() else number,
            "title": str(payload.get("title") or "").strip(),
            "state": str(payload.get("state") or "").strip() or None,
            "labels": labels,
            "assignees": assignees,
            "author": self._normalize_user(author),
            "created_at": self._normalize_datetime(payload.get("created_at")),
            "updated_at": self._normalize_datetime(payload.get("updated_at")),
            "closed_at": self._normalize_datetime(payload.get("closed_at")),
            "url": str(payload.get("html_url") or payload.get("url") or "").strip() or None,
            "web_url": str(payload.get("html_url") or "").strip() or None,
            "api_url": str(payload.get("url") or "").strip() or None,
            "body": str(payload.get("body") or ""),
            "comments": [],
            "raw": self._json_safe(payload),
        }
        return normalized

    def _normalize_comment(self, payload: dict[str, Any]) -> dict[str, Any]:
        author = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        comment_id = str(payload.get("id") or "").strip()
        return {
            "id": comment_id,
            "comment_id": comment_id,
            "body": str(payload.get("body") or ""),
            "text": str(payload.get("body") or ""),
            "created_at": self._normalize_datetime(payload.get("created_at")),
            "updated_at": self._normalize_datetime(payload.get("updated_at")),
            "url": str(payload.get("html_url") or payload.get("url") or "").strip() or None,
            "web_url": str(payload.get("html_url") or "").strip() or None,
            "author": self._normalize_user(author),
            "created_by": self._user_name(author),
            "created_by_identity": self._normalize_user(author),
            "raw": self._json_safe(payload),
        }

    @staticmethod
    def _require_repo(value: str | None) -> str:
        repo = str(value or "").strip()
        if not repo:
            raise ValueError("GitHub issue operations require repo")
        if "/" not in repo:
            raise ValueError("GitHub repo must be in owner/name form")
        return repo

    @staticmethod
    def _require_issue_number(value: str) -> str:
        issue_number = str(value or "").strip()
        if not issue_number:
            raise ValueError("GitHub issue operations require issue number")
        return issue_number

    @staticmethod
    def _labels_match(
        payload: dict[str, Any],
        *,
        labels_any_set: set[str],
        labels_all_set: set[str],
    ) -> bool:
        issue_labels = {
            _match_text(label) for label in GitHubIssueAdapter._label_names(payload.get("labels"))
        }
        if labels_all_set and not labels_all_set.issubset(issue_labels):
            return False
        if labels_any_set and not labels_any_set.intersection(issue_labels):
            return False
        return True

    @staticmethod
    def _label_names(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        labels: list[str] = []
        for item in value:
            if isinstance(item, dict):
                label = str(item.get("name") or "").strip()
            else:
                label = str(item or "").strip()
            if label:
                labels.append(label)
        return labels

    @staticmethod
    def _normalize_user(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "login": str(value.get("login") or "").strip() or None,
            "id": str(value.get("id") or "").strip() or None,
            "name": str(value.get("name") or value.get("login") or "").strip() or None,
            "url": str(value.get("html_url") or value.get("url") or "").strip() or None,
        }

    @staticmethod
    def _user_name(value: dict[str, Any]) -> str:
        return str(value.get("login") or value.get("name") or "").strip()

    @staticmethod
    def _normalize_datetime(value: object) -> str | None:
        if value is None:
            return None
        return AzureDevOpsIssueAdapter._normalize_datetime(str(value))

    @staticmethod
    def _next_page_url(response: httpx.Response) -> str | None:
        next_link = response.links.get("next")
        if not isinstance(next_link, dict):
            return None
        next_url = str(next_link.get("url") or "").strip()
        return next_url or None

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return AzureDevOpsIssueAdapter._json_safe(value)




class IssueService:
    def __init__(self, config_resolver: ProviderConfigResolver, *, adapters: Mapping[str, IssueProviderAdapter] | None = None) -> None:
        if adapters is not None:
            self.adapters = dict(adapters)
            return
        azure_devops = (
            AzureDevOpsIssueAdapter(config_resolver)
            if azure_devops_enabled(getattr(config_resolver, "config", None))
            else _OptionalAzureDevOpsIssueAdapter(config_resolver)
        )
        github = GitHubIssueAdapter(config_resolver)
        self.adapters: dict[str, IssueProviderAdapter] = {
            "azure_devops": azure_devops,
            "azure-devops": azure_devops,
            "ado": azure_devops,
            "github": github,
        }

    @contextmanager
    def ado_comment_poll(self, *, directory_path: str) -> Iterator[AdoCommentPollReader]:
        adapter = self.adapters["azure_devops"]
        if isinstance(adapter, _OptionalAzureDevOpsIssueAdapter):
            adapter = adapter.resolve(directory_path)
        implementation = require_azure_devops_module("evander_azure_devops_addon.issues")
        if not isinstance(adapter, implementation.AzureDevOpsIssueAdapter):
            raise ValueError("ADO comment polling requires the Azure DevOps adapter")
        connection = adapter._resolve_connection(directory_path)
        with httpx.Client(timeout=adapter.timeout_seconds) as client:
            yield AdoCommentPollReader(
                adapter, directory_path=directory_path, connection=connection, client=client
            )

    def read_remote_issue(
        self,
        *,
        provider: str,
        remote_issue_id: str,
        directory_path: str,
        project: str | None = None,
        include_comments: bool = True,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.read_issue(
            remote_issue_id=remote_issue_id,
            directory_path=directory_path,
            project=project,
            include_comments=include_comments,
        )

    def read_remote_issues_batch(
        self,
        *,
        provider: str,
        remote_issue_ids: list[str],
        directory_path: str,
        project: str | None = None,
        organization_url: str | None = None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.read_issues_batch(
            remote_issue_ids=remote_issue_ids,
            directory_path=directory_path,
            project=project,
            organization_url=organization_url,
        )

    def post_remote_comment(
        self,
        *,
        provider: str,
        remote_issue_id: str,
        directory_path: str,
        comment: str,
        project: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        correlation_marker: str | None = None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.post_comment(
            remote_issue_id=remote_issue_id,
            directory_path=directory_path,
            comment=comment,
            project=project,
            mentions=mentions,
            attachments=attachments,
            correlation_marker=correlation_marker,
        )

    def delete_remote_comment(
        self,
        *,
        provider: str,
        remote_issue_id: str,
        comment_id: str,
        directory_path: str,
        project: str | None = None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.delete_comment(
            remote_issue_id=remote_issue_id,
            comment_id=comment_id,
            directory_path=directory_path,
            project=project,
        )

    def resolve_ado_comment_mentions(
        self,
        *,
        directory_path: str,
        mentions: list[str] | None,
    ) -> list[dict[str, str]]:
        adapter = self.adapters["azure_devops"]
        if isinstance(adapter, _OptionalAzureDevOpsIssueAdapter):
            adapter = adapter.resolve(directory_path)
        implementation = require_azure_devops_module("evander_azure_devops_addon.issues")
        if not isinstance(adapter, implementation.AzureDevOpsIssueAdapter):
            raise RuntimeError("Azure DevOps issue adapter is unavailable")
        return adapter.resolve_comment_mentions(
            directory_path=directory_path,
            mentions=mentions,
        )

    def update_remote_issue(
        self,
        *,
        provider: str,
        remote_issue_id: str,
        directory_path: str,
        state: str | None,
        fields: dict[str, Any] | None,
        project: str | None = None,
        expected_state: str | None = None,
        field_attachments: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.update_issue(
            remote_issue_id=remote_issue_id,
            directory_path=directory_path,
            state=state,
            fields=fields,
            project=project,
            expected_state=expected_state,
            field_attachments=field_attachments,
        )

    def create_remote_issue(
        self,
        *,
        provider: str,
        directory_path: str,
        project: str | None = None,
        issue_type: str,
        title: str,
        description: str | None = None,
        iteration_path: str | None = None,
        acceptance_criteria: str | None = None,
        tags: list[str] | None = None,
        assigned_to: str | None = None,
        parent_backlog_item_id: str | None = None,
        fields: dict[str, Any] | None = None,
        description_attachments: list[dict[str, Any]] | None = None,
        acceptance_criteria_attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.create_issue(
            directory_path=directory_path,
            project=project,
            issue_type=issue_type,
            title=title,
            description=description,
            iteration_path=iteration_path,
            acceptance_criteria=acceptance_criteria,
            tags=tags,
            assigned_to=assigned_to,
            parent_backlog_item_id=parent_backlog_item_id,
            fields=fields,
            description_attachments=description_attachments,
            acceptance_criteria_attachments=acceptance_criteria_attachments,
        )

    def query_remote_issues(
        self,
        *,
        provider: str,
        directory_path: str,
        project: str | None = None,
        repo: str | None = None,
        query_mode: str | None = None,
        wiql: str | None = None,
        current_iteration: bool = False,
        team: str | None = None,
        iteration: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        state: str | None = None,
        labels_any: list[str] | None = None,
        labels_all: list[str] | None = None,
        assignee: str | None = None,
        author: str | None = None,
        mentioned: str | None = None,
        milestone: str | None = None,
        since: str | None = None,
        top: int = 50,
        skip: int = 0,
        include_relations: bool = False,
    ) -> dict[str, Any]:
        normalized_provider = _normalize_issue_provider(provider)
        if normalized_provider == "github":
            if str(query_mode or "").strip():
                raise ValueError("query_mode is only valid for Azure DevOps remote issue queries")
            adapter = self.adapters.get("github")
            if adapter is None or not hasattr(adapter, "query_remote_issues"):
                raise ValueError("Unsupported issue provider: github")
            return adapter.query_remote_issues(  # type: ignore[attr-defined]
                directory_path=directory_path,
                repo=repo or project,
                state=state,
                labels_any=labels_any,
                labels_all=labels_all,
                assignee=assignee,
                author=author,
                mentioned=mentioned,
                milestone=milestone,
                since=since,
                text_search=text_search,
                top=top,
                skip=skip,
            )

        if normalized_provider != "azure_devops":
            raise ValueError(f"Unsupported issue provider: {provider}")
        resolved_query_mode = validate_ado_query_mode(
            query_mode=query_mode,
            wiql=wiql,
            current_iteration=current_iteration,
            context="Azure DevOps remote issue query",
        )
        if current_iteration:
            result = self.list_remote_current_iteration_backlog_items(
                provider="azure_devops",
                directory_path=directory_path,
                project=project,
                team=team,
                area_path=area_path,
                states=states,
                types=types,
                tags_any=tags_any,
                text_search=text_search,
                top=top,
                skip=skip,
                include_relations=include_relations,
            )
        else:
            result = self.query_remote_backlog_items(
                provider="azure_devops",
                directory_path=directory_path,
                project=project,
                wiql=wiql if resolved_query_mode == ADO_QUERY_MODE_MANUAL_WIQL else None,
                iteration=iteration,
                area_path=area_path,
                states=states,
                types=types,
                tags_any=tags_any,
                text_search=text_search,
                top=top,
                skip=skip,
                include_relations=include_relations,
            )
        return _canonicalize_remote_issue_result(
            result,
            provider="azure_devops",
            directory_path=directory_path,
        )

    def query_remote_backlog_items(
        self,
        *,
        provider: str,
        directory_path: str,
        project: str | None = None,
        wiql: str | None = None,
        iteration: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        include_relations: bool = False,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.query_backlog_items(
            directory_path=directory_path,
            project=project,
            wiql=wiql,
            iteration=iteration,
            area_path=area_path,
            states=states,
            types=types,
            exclude_types=exclude_types,
            tags_any=tags_any,
            text_search=text_search,
            top=top,
            skip=skip,
            include_relations=include_relations,
        )

    def query_remote_work_item_activity(
        self,
        *,
        provider: str,
        directory_path: str,
        project: str | None = None,
        actor: str | None = None,
        since: datetime,
        until: datetime | None = None,
        activity_types: list[str] | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
        _activity_cache: AdoActivityScanCache | None = None,
        _cache_scope: str | None = None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        cache_kwargs: dict[str, Any] = {}
        if _activity_cache is not None and isinstance(adapter, AzureDevOpsIssueAdapter):
            cache_kwargs = {"_activity_cache": _activity_cache, "_cache_scope": _cache_scope}
        return adapter.query_work_item_activity(
            directory_path=directory_path,
            project=project,
            actor=actor,
            since=since,
            until=until,
            activity_types=activity_types,
            states=states,
            types=types,
            tags_any=tags_any,
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
            **cache_kwargs,
        )

    def list_remote_current_iteration_backlog_items(
        self,
        *,
        provider: str,
        directory_path: str,
        project: str | None = None,
        team: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        include_relations: bool = False,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.list_current_iteration_backlog_items(
            directory_path=directory_path,
            project=project,
            team=team,
            area_path=area_path,
            states=states,
            types=types,
            exclude_types=exclude_types,
            tags_any=tags_any,
            text_search=text_search,
            top=top,
            skip=skip,
            include_relations=include_relations,
        )

    def get_remote_backlog_item_by_id(
        self,
        *,
        provider: str,
        directory_path: str,
        backlog_item_id: str,
        project: str | None = None,
        include_comments: bool = True,
        include_relations: bool = True,
        include_updates: bool = False,
        comment_page_limit: int = 1,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        comment_options = {}
        if comment_page_limit != 1:
            if provider != "azure_devops":
                raise ValueError("comment pagination is only supported for Azure DevOps")
            comment_options["comment_page_limit"] = comment_page_limit
        return adapter.get_backlog_item_by_id(
            directory_path=directory_path,
            backlog_item_id=backlog_item_id,
            project=project,
            include_comments=include_comments,
            include_relations=include_relations,
            include_updates=include_updates,
            **comment_options,
        )

    def delete_remote_issue(
        self,
        *,
        provider: str,
        remote_issue_id: str,
        directory_path: str,
        project: str | None = None,
        destroy: bool = False,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.delete_issue(
            remote_issue_id=remote_issue_id,
            directory_path=directory_path,
            project=project,
            destroy=destroy,
        )


def _normalize_issue_provider(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"ado", "azure_devops", "azure-devops"}:
        return "azure_devops"
    if text == "github":
        return "github"
    return text


def _canonicalize_remote_issue_result(
    result: dict[str, Any], *, provider: str, directory_path: str
) -> dict[str, Any]:
    normalized = dict(result)
    raw_items = result.get("items")
    if isinstance(raw_items, list):
        normalized["items"] = [
            _canonicalize_remote_issue_item(item, provider=provider, directory_path=directory_path)
            for item in raw_items
            if isinstance(item, dict)
        ]
    normalized["provider"] = _normalize_issue_provider(normalized.get("provider") or provider)
    normalized.setdefault("directory_path", directory_path)
    return normalized


def _canonicalize_remote_issue_item(
    item: dict[str, Any], *, provider: str, directory_path: str
) -> dict[str, Any]:
    if _normalize_issue_provider(provider) != "azure_devops":
        return item
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    remote_issue_id = _first_non_empty(
        item.get("remote_issue_id"),
        item.get("work_item_id"),
        item.get("id"),
        fields.get("System.Id"),
    )
    updated_at = _first_non_empty(
        item.get("updated_at"),
        item.get("changed_at"),
        item.get("updated_date"),
        fields.get("System.ChangedDate"),
    )
    labels = item.get("labels")
    if not isinstance(labels, list):
        labels = item.get("tags") if isinstance(item.get("tags"), list) else []
    if not labels:
        labels = _split_semicolon_tags(fields.get("System.Tags"))
    assignees = item.get("assignees") if isinstance(item.get("assignees"), list) else []
    assigned_to = _first_non_empty(item.get("assigned_to"), fields.get("System.AssignedTo"))
    if assigned_to and not assignees:
        assignees = [assigned_to]
    canonical = {
        "provider": "azure_devops",
        "directory_path": directory_path,
        "remote_issue_id": remote_issue_id,
        "issue_id": remote_issue_id,
        "issue_number": remote_issue_id,
        "title": _first_non_empty(item.get("title"), fields.get("System.Title")),
        "state": _first_non_empty(item.get("state"), fields.get("System.State")) or None,
        "labels": labels,
        "assignees": assignees,
        "author": item.get("author") if isinstance(item.get("author"), dict) else {},
        "created_at": _first_non_empty(item.get("created_at"), fields.get("System.CreatedDate")),
        "updated_at": updated_at,
        "closed_at": _first_non_empty(
            item.get("closed_at"), fields.get("Microsoft.VSTS.Common.ClosedDate")
        ),
        "url": _first_non_empty(item.get("web_url"), item.get("url")),
        "body": _first_non_empty(
            item.get("body"), item.get("description"), fields.get("System.Description")
        ),
        "raw": {key: value for key, value in item.items() if key != "raw"},
        "work_item_id": remote_issue_id,
        "changed_at": updated_at,
    }
    return {**item, **{key: value for key, value in canonical.items() if value is not None}}


def _first_non_empty(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _split_semicolon_tags(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def _match_text(value: object) -> str:
    return str(value or "").strip().casefold()



AzureDevOpsIssueAdapter = optional_azure_devops_class('evander_azure_devops_addon.issues', 'AzureDevOpsIssueAdapter')

AzureDevOpsIssueInlineMediaProvider = optional_azure_devops_class('evander_azure_devops_addon.issues', 'AzureDevOpsIssueInlineMediaProvider')

AdoCommentPollReader = optional_azure_devops_class('evander_azure_devops_addon.issues', 'AdoCommentPollReader')


class _OptionalAzureDevOpsIssueAdapter:
    def __init__(self, resolver):
        self.resolver = resolver

    def resolve(self, directory_path):
        resolve = getattr(self.resolver, "resolve_for_directory", None)
        config = resolve(directory_path) if callable(resolve) else getattr(self.resolver, "config", None)
        module = require_azure_devops_module("evander_azure_devops_addon.issues", config)
        return module.AzureDevOpsIssueAdapter(self.resolver)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            adapter = self.resolve(kwargs.get("directory_path", "root"))
            return getattr(adapter, name)(*args, **kwargs)
        return call
