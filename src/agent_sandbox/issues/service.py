from __future__ import annotations

import base64
from dataclasses import dataclass
import html
from html.parser import HTMLParser
import mimetypes
import os
from pathlib import Path
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from agent_sandbox.ado_mentions import (
    render_ado_mentions_prefix,
    resolve_ado_mentions,
)
from agent_sandbox.ado_reports import DEFAULT_EFFORT_FIELDS
from agent_sandbox.inline_media import (
    InlineMediaDownload,
    download_url_with_limit,
    inline_media_download_from_embedded_payload,
    require_provider_media_url,
)
from agent_sandbox.models import InlineMediaRef
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.text_formatting import looks_like_html, render_markdownish_text_to_html

_ADO_HTML_MEDIA_FIELDS = (
    "System.Description",
    "Microsoft.VSTS.TCM.ReproSteps",
    "Microsoft.VSTS.TCM.SystemInfo",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
)
_ADO_PROXY_SYNC_FIELDS = (
    "System.Id",
    "System.Title",
    "System.Description",
    "System.State",
    "System.WorkItemType",
    "System.AreaPath",
    "System.IterationPath",
    "System.AssignedTo",
    "System.Tags",
    "System.ChangedDate",
)
_ADO_WORK_ITEMS_BATCH_LIMIT = 200
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
    parser = _AdoHtmlImageParser()
    parser.feed(html or "")
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(parser.images):
        fallback = item.get("alt") or item.get("title") or f"ado-inline-image-{index}"
        url = item["url"]
        filename = _filename_from_media_url(url, fallback=fallback)
        metadata = {
            "source": "html_image",
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
    for field_name in _ADO_HTML_MEDIA_FIELDS:
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


def _artifact_filename(artifact: dict[str, Any]) -> str:
    return str(
        artifact.get("normalized_safe_filename")
        or artifact.get("filename")
        or artifact.get("original_filename")
        or "attachment"
    ).strip() or "attachment"


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


def _append_html_attachment_links(text: str | None, attachments: list[dict[str, Any]]) -> str | None:
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


class AzureDevOpsIssueAdapter:
    provider_name = "azure_devops"

    def __init__(
        self,
        config_resolver: ProviderConfigResolver,
        *,
        timeout_seconds: float = 30.0,
        comments_max_count: int = 50,
    ) -> None:
        self.config_resolver = config_resolver
        self.timeout_seconds = timeout_seconds
        self.comments_max_count = comments_max_count

    def _resolve_connection(self, directory_path: str) -> tuple[str, str, str]:
        config = self.config_resolver.resolve_for_directory(directory_path)
        organization_url = str(config.azure_devops_organization_url).rstrip("/")
        project = str(config.azure_devops_project).strip()
        pat_env_name = str(config.azure_devops_pat_env).strip() or "AZURE_DEVOPS_PAT"
        pat_value = os.environ.get(pat_env_name, "").strip()
        if not organization_url or not project:
            raise ValueError(
                "Azure DevOps provider config must define organization URL and project."
            )
        if not pat_value:
            raise ValueError(
                f"Environment variable {pat_env_name} is required for Azure DevOps issue operations."
            )
        return organization_url, project, pat_value

    @staticmethod
    def _auth_header(pat: str) -> str:
        token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    @staticmethod
    def _normalize_datetime(value: str | None) -> str | None:
        if not value:
            return None
        normalized = value
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_assigned_to(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        if isinstance(value, dict):
            for key in ("displayName", "uniqueName", "mailAddress", "signInAddress", "name"):
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    return candidate
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _split_tags(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            raw_values = value
        else:
            raw_values = str(value).split(";")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_values:
            tag = str(item).strip()
            if not tag:
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(tag)
        return normalized

    @staticmethod
    def _resolve_work_item_type(issue_type: str) -> str:
        normalized = str(issue_type).strip()
        if not normalized:
            raise ValueError("issue_type is required for Azure DevOps issue creation.")
        key = re.sub(r"[\s_-]+", " ", normalized.lower()).strip()
        aliases = {
            "bug": "Bug",
            "task": "Task",
            "issue": "Issue",
            "epic": "Epic",
            "feature": "Feature",
            "user story": "User Story",
            "story": "User Story",
            "product backlog item": "Product Backlog Item",
            "pbi": "Product Backlog Item",
            "requirement": "Requirement",
        }
        return aliases.get(key, normalized)

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> str | None:
        if not tags:
            return None
        values = [str(tag).strip() for tag in tags if str(tag).strip()]
        if not values:
            return None
        return "; ".join(values)

    @staticmethod
    def _extract_ado_error_message(response: httpx.Response) -> str:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "application/json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                message = payload.get("message") or payload.get("error") or payload.get("typeName")
                if isinstance(message, str) and message.strip():
                    return message.strip()
        text = response.text.strip()
        if text:
            return text[:500]
        return "No response body."

    @staticmethod
    def _normalize_parent_backlog_item_id(parent_backlog_item_id: object) -> str | None:
        if parent_backlog_item_id is None:
            return None
        normalized = str(parent_backlog_item_id).strip()
        if not normalized:
            raise ValueError(
                "Parent relation validation failed: parent_backlog_item_id must be a non-empty numeric value."
            )
        if not normalized.isdigit():
            raise ValueError(
                "Parent relation validation failed: parent_backlog_item_id must contain only digits."
            )
        return normalized

    @staticmethod
    def _wiql_escape(value: str) -> str:
        return value.replace("'", "''")

    @classmethod
    def _wiql_quote(cls, value: str) -> str:
        return f"'{cls._wiql_escape(value)}'"

    @classmethod
    def _wiql_in_clause(cls, field_name: str, values: list[str]) -> str | None:
        normalized = [str(value).strip() for value in values if str(value).strip()]
        if not normalized:
            return None
        quoted = ", ".join(cls._wiql_quote(item) for item in normalized)
        return f"[{field_name}] IN ({quoted})"

    @classmethod
    def _wiql_not_equals_clauses(cls, field_name: str, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values if str(value).strip()]
        return [f"[{field_name}] <> {cls._wiql_quote(value)}" for value in normalized]

    @staticmethod
    def _is_safe_wiql_field_reference(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_.]+", value))

    @classmethod
    def _wiql_team_field_clause(cls, values: list[_AdoTeamFieldValue]) -> str | None:
        clauses: list[str] = []
        for item in values:
            field_reference_name = item.field_reference_name.strip()
            if not cls._is_safe_wiql_field_reference(field_reference_name):
                continue
            operator = "UNDER" if item.include_children else "="
            clauses.append(
                f"[{field_reference_name}] {operator} {cls._wiql_quote(item.value)}"
            )
        if not clauses:
            return None
        return "(" + " OR ".join(clauses) + ")"

    def _build_backlog_wiql_query(
        self,
        *,
        project: str,
        iteration: str | None = None,
        area_path: str | None = None,
        team_field_values: list[_AdoTeamFieldValue] | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
    ) -> str:
        where_clauses: list[str] = [f"[System.TeamProject] = {self._wiql_quote(project)}"]
        if iteration:
            where_clauses.append(f"[System.IterationPath] UNDER {self._wiql_quote(iteration)}")
        if area_path:
            where_clauses.append(f"[System.AreaPath] UNDER {self._wiql_quote(area_path)}")
        team_field_clause = self._wiql_team_field_clause(team_field_values or [])
        if team_field_clause:
            where_clauses.append(team_field_clause)
        state_clause = self._wiql_in_clause("System.State", states or [])
        if state_clause:
            where_clauses.append(state_clause)
        type_clause = self._wiql_in_clause("System.WorkItemType", types or [])
        if type_clause:
            where_clauses.append(type_clause)
        where_clauses.extend(
            self._wiql_not_equals_clauses("System.WorkItemType", exclude_types or [])
        )
        if tags_any:
            tag_clauses = [
                f"[System.Tags] CONTAINS {self._wiql_quote(tag)}"
                for tag in tags_any
                if str(tag).strip()
            ]
            if tag_clauses:
                where_clauses.append("(" + " OR ".join(tag_clauses) + ")")
        if text_search:
            query_text = self._wiql_quote(text_search)
            where_clauses.append(
                f"([System.Title] CONTAINS {query_text} OR [System.Description] CONTAINS {query_text})"
            )
        where = " AND ".join(where_clauses)
        return (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE {where} "
            "ORDER BY [System.ChangedDate] DESC, [System.Id] DESC"
        )

    def _raise_ado_http_error(self, *, action: str, response: httpx.Response) -> None:
        message = self._extract_ado_error_message(response)
        raise RuntimeError(
            f"Azure DevOps {action} failed (status {response.status_code}): {message}"
        )

    def _upload_work_item_attachments(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        attachments: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        uploaded: list[dict[str, Any]] = []
        for artifact in attachments or []:
            filename = _artifact_filename(artifact)
            response = client.post(
                f"{organization_url}/{project}/_apis/wit/attachments",
                params={
                    "api-version": "7.1",
                    "fileName": filename,
                    "uploadType": "Simple",
                },
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/octet-stream",
                },
                content=_artifact_bytes(artifact),
            )
            if response.is_error:
                self._raise_ado_http_error(action="upload work item attachment", response=response)
            payload = response.json()
            uploaded.append(
                {
                    "provider": self.provider_name,
                    "surface": "work_item",
                    "artifact_id": artifact.get("artifact_id"),
                    "filename": filename,
                    "mime_type": _artifact_mime_type(artifact),
                    "byte_count": artifact.get("byte_count"),
                    "url": str(payload.get("url") or "").strip(),
                    "id": payload.get("id"),
                    "is_image": _artifact_is_image(artifact),
                    "raw": payload,
                }
            )
        return uploaded

    @staticmethod
    def _attachment_relation_operations(
        uploaded_attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        for item in uploaded_attachments:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            operations.append(
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "AttachedFile",
                        "url": url,
                        "attributes": {"name": str(item.get("filename") or "attachment")},
                    },
                }
            )
        return operations

    def _fetch_comments(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        remote_issue_id: str,
    ) -> list[dict[str, Any]]:
        comments_url = (
            f"{organization_url}/{project}/_apis/wit/workItems/{remote_issue_id}/comments"
        )
        response = client.get(
            comments_url,
            params={
                "api-version": "7.1-preview.4",
                "$top": str(self.comments_max_count),
                "order": "desc",
            },
            headers={"Authorization": auth_header},
        )
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("comments")
        if not isinstance(entries, list):
            return []
        normalized: list[dict[str, Any]] = []
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            comment_id = str(raw.get("id", "")).strip()
            raw_text = str(raw.get("text", "")).strip()
            html_content = str(
                raw.get("html") or raw.get("renderedText") or raw.get("text") or ""
            ).strip()
            created_by = raw.get("createdBy") if isinstance(raw.get("createdBy"), dict) else {}
            normalized.append(
                {
                    "id": comment_id,
                    "text": raw_text,
                    "html": html_content,
                    "raw_text": raw_text,
                    "created_by": str(created_by.get("displayName", "")).strip(),
                    "created_by_identity": self._json_safe(created_by),
                    "created_date": self._normalize_datetime(
                        str(raw.get("createdDate", "")).strip()
                    ),
                    "url": str((raw.get("_links") or {}).get("web", {}).get("href", "")).strip(),
                    "inline_media": extract_ado_html_image_refs(
                        html_content,
                        provider=self.provider_name,
                        resource_id=remote_issue_id,
                        comment_id=comment_id or None,
                        media_id_prefix=f"comment-{comment_id or len(normalized)}-image",
                    ),
                }
            )
        return normalized

    def _fetch_issue(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        remote_issue_id: str,
    ) -> dict[str, Any]:
        work_item_url = f"{organization_url}/{project}/_apis/wit/workitems/{remote_issue_id}"
        response = client.get(
            work_item_url,
            params={"api-version": "7.1", "$expand": "All"},
            headers={"Authorization": auth_header},
        )
        response.raise_for_status()
        payload = response.json()
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        api_url = str(payload.get("url") or "").strip()
        web_url = str((payload.get("_links") or {}).get("web", {}).get("href") or "").strip()
        return {
            "provider": self.provider_name,
            "remote_issue_id": str(payload.get("id") or remote_issue_id),
            "id": str(payload.get("id") or remote_issue_id),
            "project": str(fields.get("System.TeamProject") or project).strip() or project,
            "organization_url": organization_url,
            "rev": payload.get("rev"),
            "title": str(fields.get("System.Title") or ""),
            "description": str(fields.get("System.Description") or ""),
            "state": str(fields.get("System.State") or ""),
            "url": web_url or api_url,
            "web_url": web_url or None,
            "api_url": api_url or None,
            "updated_at": self._normalize_datetime(str(fields.get("System.ChangedDate") or "")),
            "fields": {k: v for k, v in fields.items()},
        }

    def _normalize_work_item_summary(
        self,
        raw_item: dict[str, Any],
        *,
        fallback_id: str | None = None,
    ) -> dict[str, Any]:
        fields = raw_item.get("fields") if isinstance(raw_item.get("fields"), dict) else {}
        remote_id = str(raw_item.get("id") or fields.get("System.Id") or fallback_id or "").strip()
        changed_at = self._normalize_datetime(str(fields.get("System.ChangedDate") or ""))
        web_url = str((raw_item.get("_links") or {}).get("web", {}).get("href") or "").strip()
        api_url = str(raw_item.get("url") or "").strip()
        return {
            "provider": self.provider_name,
            "remote_issue_id": remote_id,
            "id": remote_id,
            "title": str(fields.get("System.Title") or "").strip(),
            "description": str(fields.get("System.Description") or "").strip(),
            "state": str(fields.get("System.State") or "").strip() or None,
            "type": str(fields.get("System.WorkItemType") or "").strip() or None,
            "area": str(fields.get("System.AreaPath") or "").strip() or None,
            "iteration": str(fields.get("System.IterationPath") or "").strip() or None,
            "assigned_to": self._normalize_assigned_to(fields.get("System.AssignedTo")),
            "tags": self._split_tags(fields.get("System.Tags")),
            "url": web_url or api_url or None,
            "updated_at": changed_at,
            "changed_at": changed_at,
            "fields": {k: v for k, v in fields.items()},
        }

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(value, list):
            return [AzureDevOpsIssueAdapter._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): AzureDevOpsIssueAdapter._json_safe(item) for key, item in value.items()
            }
        return str(value)

    def _normalize_relations(
        self, relations: object, *, resource_id: str | None = None
    ) -> list[dict[str, Any]]:
        if not isinstance(relations, list):
            return []
        normalized: list[dict[str, Any]] = []
        for index, relation in enumerate(relations):
            if not isinstance(relation, dict):
                continue
            attrs = (
                relation.get("attributes") if isinstance(relation.get("attributes"), dict) else {}
            )
            rel = str(relation.get("rel") or "").strip()
            url = str(relation.get("url") or "").strip()
            safe_attrs = self._json_safe(attrs)
            normalized_relation: dict[str, Any] = {
                "rel": rel or None,
                "url": url or None,
                "attributes": safe_attrs,
            }
            if url and resource_id and _relation_is_media_reference(rel, url, safe_attrs):
                filename = str(
                    safe_attrs.get("name")
                    or safe_attrs.get("fileName")
                    or safe_attrs.get("filename")
                    or safe_attrs.get("resourceName")
                    or ""
                ).strip()
                if not filename:
                    filename = _filename_from_media_url(url, fallback=f"ado-relation-media-{index}")
                normalized_relation["inline_media"] = _make_ado_media_ref(
                    provider=self.provider_name,
                    resource_id=resource_id,
                    media_id=f"relation-{index}",
                    url=url,
                    filename=filename,
                    metadata={
                        "source": "work_item_relation",
                        "rel": rel,
                        "attributes": safe_attrs,
                    },
                )
            normalized.append(normalized_relation)
        return normalized

    def _fetch_updates(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        backlog_item_id: str,
    ) -> list[dict[str, Any]]:
        updates_url = f"{organization_url}/{project}/_apis/wit/workItems/{backlog_item_id}/updates"
        continuation_token: str | None = None
        updates: list[dict[str, Any]] = []
        while True:
            params: dict[str, str] = {"api-version": "7.1-preview.3", "$top": "200"}
            if continuation_token:
                params["continuationToken"] = continuation_token
            response = client.get(
                updates_url,
                params=params,
                headers={"Authorization": auth_header},
            )
            response.raise_for_status()
            payload = response.json()
            entries = payload.get("value") if isinstance(payload, dict) else None
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    updates.append(
                        {
                            "id": str(entry.get("id") or "").strip() or None,
                            "rev": entry.get("rev"),
                            "revised_at": self._normalize_datetime(
                                str(entry.get("revisedDate") or "").strip()
                            ),
                            "revised_by": self._normalize_assigned_to(entry.get("revisedBy")),
                            "fields": self._json_safe(
                                entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
                            ),
                            "relations": self._json_safe(
                                entry.get("relations")
                                if isinstance(entry.get("relations"), dict)
                                else {}
                            ),
                            "url": str(entry.get("url") or "").strip() or None,
                        }
                    )
            continuation_token = (
                str(response.headers.get("x-ms-continuationtoken") or "").strip() or None
            )
            if continuation_token is None:
                break
        return updates

    def read_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        project: str | None = None,
        include_comments: bool = True,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps issue reads.")
        auth_header = self._auth_header(pat)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            issue = self._fetch_issue(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                remote_issue_id=remote_issue_id,
            )
            if include_comments:
                issue["comments"] = self._fetch_comments(
                    client,
                    organization_url=organization_url,
                    project=normalized_project,
                    auth_header=auth_header,
                    remote_issue_id=remote_issue_id,
                )
            else:
                issue["comments"] = []
        return issue

    def read_issues_batch(
        self,
        *,
        remote_issue_ids: list[str],
        directory_path: str,
        project: str | None = None,
        organization_url: str | None = None,
    ) -> dict[str, Any]:
        configured_organization_url, configured_project, pat = self._resolve_connection(
            directory_path
        )
        normalized_organization_url = str(
            organization_url or configured_organization_url
        ).rstrip("/")
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps batch issue reads.")
        auth_header = self._auth_header(pat)

        requested_by_numeric_id: dict[int, str] = {}
        invalid_ids: list[str] = []
        for raw_id in remote_issue_ids:
            text = str(raw_id).strip()
            if not text:
                continue
            if not text.isdigit():
                invalid_ids.append(text)
                continue
            requested_by_numeric_id.setdefault(int(text), text)

        items: dict[str, dict[str, Any]] = {}
        batch_url = f"{normalized_organization_url}/_apis/wit/workitemsbatch"
        numeric_ids = list(requested_by_numeric_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for index in range(0, len(numeric_ids), _ADO_WORK_ITEMS_BATCH_LIMIT):
                chunk_ids = numeric_ids[index : index + _ADO_WORK_ITEMS_BATCH_LIMIT]
                response = client.post(
                    batch_url,
                    params={"api-version": "7.1"},
                    headers={"Authorization": auth_header, "Content-Type": "application/json"},
                    json={
                        "ids": chunk_ids,
                        "fields": list(_ADO_PROXY_SYNC_FIELDS),
                        "errorPolicy": "Omit",
                    },
                )
                if response.is_error:
                    self._raise_ado_http_error(
                        action="fetch proxy issue summaries batch", response=response
                    )
                payload = response.json()
                values = payload.get("value") if isinstance(payload, dict) else None
                for raw_item in values or []:
                    if not isinstance(raw_item, dict):
                        continue
                    item_id = str(raw_item.get("id") or "").strip()
                    if not item_id:
                        continue
                    items[item_id] = self._normalize_work_item_summary(
                        raw_item,
                        fallback_id=item_id,
                    )

        requested_ids = [requested_by_numeric_id[item_id] for item_id in numeric_ids]
        missing_ids = [item_id for item_id in requested_ids if item_id not in items]
        missing_ids.extend(invalid_ids)
        return {
            "provider": self.provider_name,
            "project": normalized_project,
            "organization_url": normalized_organization_url,
            "items": items,
            "missing_ids": missing_ids,
            "requested_count": len(remote_issue_ids),
            "returned_count": len(items),
        }

    def post_comment(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        comment: str,
        project: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps issue comments.")
        auth_header = self._auth_header(pat)
        comments_url = (
            f"{organization_url}/{normalized_project}/_apis/wit/workItems/{remote_issue_id}/comments"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resolved_mentions = resolve_ado_mentions(
                client=client,
                organization_url=organization_url,
                pat=pat,
                mention_refs=mentions,
            )
            uploaded_attachments = self._upload_work_item_attachments(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                attachments=attachments,
            )
            comment_with_mentions = render_ado_mentions_prefix(comment, resolved_mentions)
            formatted_comment = (
                _append_html_attachment_links(comment_with_mentions, uploaded_attachments) or ""
            )
            response = client.post(
                comments_url,
                params={"api-version": "7.1-preview.4"},
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                json={"text": formatted_comment},
            )
            response.raise_for_status()
            payload = response.json()
            if uploaded_attachments:
                work_item_url = (
                    f"{organization_url}/{normalized_project}/_apis/wit/workitems/{remote_issue_id}"
                )
                relation_response = client.patch(
                    work_item_url,
                    params={"api-version": "7.1"},
                    headers={
                        "Authorization": auth_header,
                        "Content-Type": "application/json-patch+json",
                    },
                    json=self._attachment_relation_operations(uploaded_attachments),
                )
                relation_response.raise_for_status()
            return {
                "provider": self.provider_name,
                "remote_issue_id": str(remote_issue_id),
                "comment": {
                    "id": str(payload.get("id") or "").strip(),
                    "text": str(payload.get("text") or formatted_comment),
                    "url": str(
                        (payload.get("_links") or {}).get("web", {}).get("href") or ""
                    ).strip(),
                },
                "uploaded_attachments": uploaded_attachments,
                "resolved_mentions": [mention.audit_payload() for mention in resolved_mentions],
            }

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
    ) -> dict[str, Any]:
        fields = fields or {}
        field_attachments = field_attachments or {}
        normalized_field_names = {
            str(field_name).strip()
            for field_name in [*fields.keys(), *field_attachments.keys()]
            if str(field_name).strip()
        }
        if state is None and not normalized_field_names:
            return {
                "provider": self.provider_name,
                "remote_issue_id": str(remote_issue_id),
                "updated": False,
            }

        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps issue updates.")
        auth_header = self._auth_header(pat)
        work_item_url = f"{organization_url}/{normalized_project}/_apis/wit/workitems/{remote_issue_id}"
        uploaded_attachments: list[dict[str, Any]] = []
        operations: list[dict[str, Any]] = []
        normalized_expected_state = str(expected_state).strip() if expected_state is not None else ""
        if normalized_expected_state:
            operations.append(
                {
                    "op": "test",
                    "path": "/fields/System.State",
                    "value": normalized_expected_state,
                }
            )
        if state is not None:
            operations.append({"op": "add", "path": "/fields/System.State", "value": state})
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for field_name in sorted(normalized_field_names):
                attachments = self._upload_work_item_attachments(
                    client,
                    organization_url=organization_url,
                    project=normalized_project,
                    auth_header=auth_header,
                    attachments=field_attachments.get(field_name),
                )
                uploaded_attachments.extend(attachments)
                value = fields.get(field_name)
                if attachments:
                    value = _append_html_attachment_links(
                        str(value or ""),
                        attachments,
                    )
                operations.append({"op": "add", "path": f"/fields/{field_name}", "value": value})
            operations.extend(self._attachment_relation_operations(uploaded_attachments))
            response = client.patch(
                work_item_url,
                params={"api-version": "7.1"},
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json-patch+json",
                },
                json=operations,
            )
            if response.is_error:
                self._raise_ado_http_error(action="update work item", response=response)
        return {
            "provider": self.provider_name,
            "remote_issue_id": str(remote_issue_id),
            "updated": True,
            "expected_state": normalized_expected_state or None,
            "state": state,
            "fields": fields or {},
            "uploaded_attachments": uploaded_attachments,
        }

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
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps issue creation.")
        auth_header = self._auth_header(pat)
        work_item_type = self._resolve_work_item_type(issue_type)
        create_url = (
            f"{organization_url}/{normalized_project}/_apis/wit/workitems/${work_item_type}"
        )

        operations: list[dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.Title", "value": title}
        ]
        if description is not None and not description_attachments:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/System.Description",
                    "value": _format_ado_html_field(description),
                }
            )
        if iteration_path is not None:
            operations.append(
                {"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path}
            )
        if acceptance_criteria is not None and not acceptance_criteria_attachments:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
                    "value": _format_ado_html_field(acceptance_criteria),
                }
            )
        normalized_tags = self._normalize_tags(tags)
        if normalized_tags is not None:
            operations.append(
                {"op": "add", "path": "/fields/System.Tags", "value": normalized_tags}
            )
        if assigned_to is not None:
            operations.append(
                {"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to}
            )
        for field_name, value in (fields or {}).items():
            normalized_field_name = str(field_name).strip()
            if normalized_field_name:
                operations.append(
                    {"op": "add", "path": f"/fields/{normalized_field_name}", "value": value}
                )
        normalized_parent_backlog_item_id = self._normalize_parent_backlog_item_id(
            parent_backlog_item_id
        )
        if normalized_parent_backlog_item_id is not None:
            operations.append(
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "System.LinkTypes.Hierarchy-Reverse",
                        "url": f"{organization_url}/{normalized_project}/_apis/wit/workItems/{normalized_parent_backlog_item_id}",
                    },
                }
            )

        uploaded_attachments: list[dict[str, Any]] = []
        with httpx.Client(timeout=self.timeout_seconds) as client:
            description_uploaded = self._upload_work_item_attachments(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                attachments=description_attachments,
            )
            uploaded_attachments.extend(description_uploaded)
            if description_uploaded:
                operations.append(
                    {
                        "op": "add",
                        "path": "/fields/System.Description",
                        "value": _append_html_attachment_links(
                            description or "", description_uploaded
                        ),
                    }
                )
            acceptance_uploaded = self._upload_work_item_attachments(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                attachments=acceptance_criteria_attachments,
            )
            uploaded_attachments.extend(acceptance_uploaded)
            if acceptance_uploaded:
                operations.append(
                    {
                        "op": "add",
                        "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
                        "value": _append_html_attachment_links(
                            acceptance_criteria or "", acceptance_uploaded
                        ),
                    }
                )
            operations.extend(self._attachment_relation_operations(uploaded_attachments))
            response = client.patch(
                create_url,
                params={"api-version": "7.1"},
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json-patch+json",
                },
                json=operations,
            )
            if response.is_error:
                if normalized_parent_backlog_item_id is not None:
                    self._raise_ado_http_error(
                        action="link parent relation during create work item", response=response
                    )
                self._raise_ado_http_error(action="create work item", response=response)
            payload = response.json()

        payload_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        return {
            "provider": self.provider_name,
            "remote_issue_id": str(payload.get("id") or ""),
            "id": payload.get("id"),
            "url": str(
                (payload.get("_links") or {}).get("web", {}).get("href") or payload.get("url") or ""
            ),
            "type": str(payload_fields.get("System.WorkItemType") or work_item_type),
            "project": str((payload_fields.get("System.TeamProject") or normalized_project)),
            "fields": {
                "System.Title": payload_fields.get("System.Title"),
                "System.Description": payload_fields.get("System.Description"),
                "System.IterationPath": payload_fields.get("System.IterationPath"),
                "Microsoft.VSTS.Common.AcceptanceCriteria": payload_fields.get(
                    "Microsoft.VSTS.Common.AcceptanceCriteria"
                ),
                "System.Tags": payload_fields.get("System.Tags"),
                "System.AssignedTo": self._normalize_assigned_to(
                    payload_fields.get("System.AssignedTo")
                ),
                **{k: v for k, v in (fields or {}).items() if k in payload_fields},
            },
            "uploaded_attachments": uploaded_attachments,
        }

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
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps backlog queries.")
        auth_header = self._auth_header(pat)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return self._query_backlog_items_core(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
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
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError(
                "project is required for Azure DevOps current-iteration backlog queries."
            )
        auth_header = self._auth_header(pat)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            current_iteration = self._resolve_current_iteration(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                team=team,
            )
            team_field_values = self._resolve_team_field_values(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                team=current_iteration.team,
            )
            query_result = self._query_backlog_items_core(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                iteration=current_iteration.path,
                area_path=area_path,
                team_field_values=team_field_values,
                states=states,
                types=types,
                exclude_types=exclude_types,
                tags_any=tags_any,
                text_search=text_search,
                top=top,
                skip=skip,
                include_relations=include_relations,
            )
        return {
            "provider": self.provider_name,
            "project": normalized_project,
            "team": current_iteration.team,
            "iteration_path": current_iteration.path,
            "iteration_start_date": current_iteration.start_date,
            "iteration_finish_date": current_iteration.finish_date,
            "items": query_result.get("items") or [],
            "count": int(query_result.get("count") or 0),
            "paging": query_result.get("paging"),
        }

    @staticmethod
    def _normalize_ado_team_name(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _resolve_current_iteration_path(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        team: str | None = None,
    ) -> tuple[str, str]:
        current_iteration = self._resolve_current_iteration(
            client,
            organization_url=organization_url,
            project=project,
            auth_header=auth_header,
            team=team,
        )
        return current_iteration.team, current_iteration.path

    def _resolve_current_iteration(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        team: str | None = None,
    ) -> _AdoCurrentIteration:
        normalized_team = self._normalize_ado_team_name(team)
        if normalized_team is None:
            normalized_team = (
                self._resolve_project_default_team_name(
                    client,
                    organization_url=organization_url,
                    project=project,
                    auth_header=auth_header,
                )
                or project
            )
        iterations_url = (
            f"{organization_url}/{quote(project, safe='')}/{quote(normalized_team, safe='')}"
            "/_apis/work/teamsettings/iterations"
        )
        response = client.get(
            iterations_url,
            params={"$timeframe": "current", "api-version": "7.1"},
            headers={"Authorization": auth_header},
        )
        if response.is_error:
            self._raise_ado_http_error(action="resolve current iteration", response=response)
        payload = response.json()
        values = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not values:
            raise RuntimeError(
                f"Azure DevOps team '{normalized_team}' in project '{project}' has no current iteration configured."
            )
        first_iteration = values[0] if isinstance(values[0], dict) else {}
        iteration_path = str(first_iteration.get("path") or "").strip()
        if not iteration_path:
            raise RuntimeError(
                f"Azure DevOps team '{normalized_team}' in project '{project}' returned no current iteration path."
            )
        attrs = (
            first_iteration.get("attributes")
            if isinstance(first_iteration.get("attributes"), dict)
            else {}
        )
        return _AdoCurrentIteration(
            team=normalized_team,
            path=iteration_path,
            start_date=self._normalize_datetime(str(attrs.get("startDate") or "")),
            finish_date=self._normalize_datetime(str(attrs.get("finishDate") or "")),
        )

    def _resolve_project_default_team_name(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
    ) -> str | None:
        project_url = f"{organization_url}/_apis/projects/{quote(project, safe='')}"
        response = client.get(
            project_url,
            params={"includeCapabilities": "true", "api-version": "7.1-preview.4"},
            headers={"Authorization": auth_header},
        )
        if response.is_error:
            self._raise_ado_http_error(action="resolve default team", response=response)
        payload = response.json()
        default_team = payload.get("defaultTeam") if isinstance(payload, dict) else None
        if not isinstance(default_team, dict):
            return None
        return self._normalize_ado_team_name(
            default_team.get("name")
        ) or self._normalize_ado_team_name(
            default_team.get("id"),
        )

    def _resolve_team_field_values(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        team: str,
    ) -> list[_AdoTeamFieldValue]:
        team_field_values_url = (
            f"{organization_url}/{quote(project, safe='')}/{quote(team, safe='')}"
            "/_apis/work/teamsettings/teamfieldvalues"
        )
        response = client.get(
            team_field_values_url,
            params={"api-version": "7.1"},
            headers={"Authorization": auth_header},
        )
        if response.is_error:
            self._raise_ado_http_error(action="resolve team field values", response=response)
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        field = payload.get("field") if isinstance(payload.get("field"), dict) else {}
        field_reference_name = str(field.get("referenceName") or "").strip()
        if not field_reference_name:
            return []
        raw_values = payload.get("values") if isinstance(payload.get("values"), list) else []
        if not raw_values:
            default_value = str(payload.get("defaultValue") or "").strip()
            raw_values = [{"value": default_value, "includeChildren": False}]
        values: list[_AdoTeamFieldValue] = []
        seen: set[tuple[str, str, bool]] = set()
        for raw_value in raw_values:
            if not isinstance(raw_value, dict):
                continue
            value = str(raw_value.get("value") or "").strip()
            if not value:
                continue
            include_children = bool(raw_value.get("includeChildren"))
            key = (field_reference_name.casefold(), value.casefold(), include_children)
            if key in seen:
                continue
            seen.add(key)
            values.append(
                _AdoTeamFieldValue(
                    field_reference_name=field_reference_name,
                    value=value,
                    include_children=include_children,
                )
            )
        return values

    def _query_backlog_items_core(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        wiql: str | None = None,
        iteration: str | None = None,
        area_path: str | None = None,
        team_field_values: list[_AdoTeamFieldValue] | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        include_relations: bool = False,
    ) -> dict[str, Any]:
        normalized_top = max(1, min(int(top), 200))
        normalized_skip = max(0, int(skip))
        normalized_wiql = str(wiql or "").strip()
        if not normalized_wiql:
            normalized_wiql = self._build_backlog_wiql_query(
                project=project,
                iteration=iteration,
                area_path=area_path,
                team_field_values=team_field_values,
                states=states,
                types=types,
                exclude_types=exclude_types,
                tags_any=tags_any,
                text_search=text_search,
            )
        wiql_url = f"{organization_url}/{quote(project, safe='')}/_apis/wit/wiql"
        select_fields = [
            "System.Id",
            "System.Title",
            "System.Description",
            "System.State",
            "System.WorkItemType",
            "System.IterationPath",
            "System.AreaPath",
            "System.AssignedTo",
            "System.Tags",
            "System.ChangedDate",
            "Microsoft.VSTS.TCM.ReproSteps",
            "Microsoft.VSTS.TCM.SystemInfo",
            "Microsoft.VSTS.Common.AcceptanceCriteria",
            *DEFAULT_EFFORT_FIELDS,
        ]
        wiql_response = client.post(
            wiql_url,
            params={"api-version": "7.1"},
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            json={"query": normalized_wiql},
        )
        if wiql_response.is_error:
            self._raise_ado_http_error(action="query backlog items", response=wiql_response)
        wiql_payload = wiql_response.json()
        raw_work_items = wiql_payload.get("workItems") if isinstance(wiql_payload, dict) else None
        work_item_ids = [
            int(item.get("id"))
            for item in (raw_work_items or [])
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        ]
        total_matches = len(work_item_ids)
        paged_ids = work_item_ids[normalized_skip : normalized_skip + normalized_top]
        items: list[dict[str, Any]] = []
        if paged_ids:
            batch_url = f"{organization_url}/_apis/wit/workitemsbatch"
            for index in range(0, len(paged_ids), 200):
                chunk_ids = paged_ids[index : index + 200]
                batch_body: dict[str, Any] = {
                    "ids": chunk_ids,
                    "fields": select_fields,
                    "errorPolicy": "Omit",
                }
                batch_response = client.post(
                    batch_url,
                    params={"api-version": "7.1"},
                    headers={"Authorization": auth_header, "Content-Type": "application/json"},
                    json=batch_body,
                )
                if batch_response.is_error:
                    self._raise_ado_http_error(
                        action="fetch backlog items batch", response=batch_response
                    )
                batch_payload = batch_response.json()
                relations_by_id: dict[str, list[dict[str, Any]]] = {}
                if include_relations:
                    relations_response = client.post(
                        batch_url,
                        params={"api-version": "7.1"},
                        headers={"Authorization": auth_header, "Content-Type": "application/json"},
                        json={
                            "ids": chunk_ids,
                            "$expand": "Relations",
                            "errorPolicy": "Omit",
                        },
                    )
                    if relations_response.is_error:
                        self._raise_ado_http_error(
                            action="fetch backlog item relations batch",
                            response=relations_response,
                        )
                    relations_payload = relations_response.json()
                    relation_values = (
                        relations_payload.get("value")
                        if isinstance(relations_payload, dict)
                        else None
                    )
                    for relation_item in relation_values or []:
                        if not isinstance(relation_item, dict):
                            continue
                        relation_item_id = str(relation_item.get("id") or "").strip()
                        if not relation_item_id:
                            continue
                        relations_by_id[relation_item_id] = self._normalize_relations(
                            relation_item.get("relations"),
                            resource_id=relation_item_id,
                        )
                values = batch_payload.get("value") if isinstance(batch_payload, dict) else None
                for raw_item in values or []:
                    if not isinstance(raw_item, dict):
                        continue
                    raw_fields = (
                        raw_item.get("fields") if isinstance(raw_item.get("fields"), dict) else {}
                    )
                    item = {
                        "id": str(raw_item.get("id") or "").strip(),
                        "title": str(raw_fields.get("System.Title") or "").strip(),
                        "state": str(raw_fields.get("System.State") or "").strip() or None,
                        "type": str(raw_fields.get("System.WorkItemType") or "").strip()
                        or None,
                        "iteration": str(raw_fields.get("System.IterationPath") or "").strip()
                        or None,
                        "area": str(raw_fields.get("System.AreaPath") or "").strip() or None,
                        "assigned_to": self._normalize_assigned_to(
                            raw_fields.get("System.AssignedTo")
                        ),
                        "url": str(
                            (raw_item.get("_links") or {}).get("web", {}).get("href") or ""
                        ).strip()
                        or str(raw_item.get("url") or "").strip()
                        or None,
                        "fields": {k: v for k, v in raw_fields.items()},
                    }
                    if include_relations:
                        item["relations"] = relations_by_id.get(item["id"], [])
                    items.append(item)
        id_order = {str(item_id): index for index, item_id in enumerate(paged_ids)}
        items.sort(key=lambda item: id_order.get(str(item.get("id") or ""), len(id_order)))
        returned_count = len(items)
        next_skip = (
            normalized_skip + returned_count
            if normalized_skip + returned_count < total_matches
            else None
        )
        return {
            "provider": self.provider_name,
            "project": project,
            "items": items,
            "count": returned_count,
            "paging": {
                "skip": normalized_skip,
                "top": normalized_top,
                "returned_count": returned_count,
                "total_matches": total_matches,
                "has_more": next_skip is not None,
                "next_skip": next_skip,
            },
        }

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
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps backlog item retrieval.")
        auth_header = self._auth_header(pat)
        work_item_url = f"{organization_url}/{quote(normalized_project, safe='')}/_apis/wit/workitems/{backlog_item_id}"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                work_item_url,
                params={"api-version": "7.1", "$expand": "All"},
                headers={"Authorization": auth_header},
            )
            if response.is_error:
                self._raise_ado_http_error(action="get backlog item by id", response=response)
            payload = response.json()
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            project_name = (
                str(fields.get("System.TeamProject") or normalized_project).strip() or None
            )
            item: dict[str, Any] = {
                "provider": self.provider_name,
                "id": str(payload.get("id") or backlog_item_id).strip(),
                "project": project_name,
                "rev": payload.get("rev"),
                "url": str(payload.get("url") or "").strip() or None,
                "web_url": str(
                    (payload.get("_links") or {}).get("web", {}).get("href") or ""
                ).strip()
                or None,
                "title": str(fields.get("System.Title") or "").strip() or None,
                "state": str(fields.get("System.State") or "").strip() or None,
                "type": str(fields.get("System.WorkItemType") or "").strip() or None,
                "created_at": self._normalize_datetime(
                    str(fields.get("System.CreatedDate") or "").strip()
                ),
                "changed_at": self._normalize_datetime(
                    str(fields.get("System.ChangedDate") or "").strip()
                ),
                "closed_at": self._normalize_datetime(
                    str(fields.get("Microsoft.VSTS.Common.ClosedDate") or "").strip()
                ),
                "fields": self._json_safe(fields),
                "relations": [],
                "inline_media": [],
            }
            item["relations"] = (
                self._normalize_relations(payload.get("relations"), resource_id=item["id"])
                if include_relations
                else []
            )
            item["comments"] = (
                self._fetch_comments(
                    client,
                    organization_url=organization_url,
                    project=project_name or normalized_project,
                    auth_header=auth_header,
                    remote_issue_id=item["id"],
                )
                if include_comments
                else []
            )
            item["updates"] = (
                self._fetch_updates(
                    client,
                    organization_url=organization_url,
                    project=project_name or normalized_project,
                    auth_header=auth_header,
                    backlog_item_id=item["id"],
                )
                if include_updates
                else []
            )
            inline_media = extract_ado_field_inline_media_refs(
                fields, provider=self.provider_name, resource_id=item["id"]
            )
            for relation in item["relations"]:
                if isinstance(relation, dict) and isinstance(relation.get("inline_media"), dict):
                    inline_media.append(relation["inline_media"])
            for comment in item["comments"]:
                if isinstance(comment, dict) and isinstance(comment.get("inline_media"), list):
                    inline_media.extend(comment["inline_media"])
            item["inline_media"] = inline_media
            return item


class AzureDevOpsIssueInlineMediaProvider:
    """Inline-media adapter for Azure DevOps work item/backlog media."""

    provider_name = "azure_devops"

    def __init__(
        self,
        config_resolver: ProviderConfigResolver,
        *,
        directory_path: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.issue_adapter = AzureDevOpsIssueAdapter(
            config_resolver, timeout_seconds=timeout_seconds
        )
        self.directory_path = directory_path
        self.timeout_seconds = timeout_seconds

    def list_inline_media(
        self,
        *,
        source_kind: str,
        resource_id: str,
        resource: dict[str, Any] | None = None,
        comment_id: str | None = None,
    ) -> list[InlineMediaRef]:
        if not isinstance(resource, dict):
            return []
        refs: list[InlineMediaRef] = []
        raw_items = resource.get("inline_media")
        if not isinstance(raw_items, list):
            raw_items = []
        for item in raw_items:
            try:
                ref = (
                    item
                    if isinstance(item, InlineMediaRef)
                    else InlineMediaRef.model_validate(item)
                )
            except Exception:
                continue
            if comment_id and ref.comment_id != comment_id:
                continue
            refs.append(
                ref.model_copy(
                    update={
                        "source_kind": source_kind,
                        "provider": self.provider_name,
                        "resource_id": resource_id,
                    }
                )
            )
        return refs

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
            raise ValueError("Azure DevOps backlog inline media does not include a download URL")
        organization_url, _project, pat = self.issue_adapter._resolve_connection(
            self.directory_path
        )
        allowed_host = urlparse(organization_url).hostname
        require_provider_media_url(
            provider=self.provider_name,
            url=media.url,
            allowed_hosts=[allowed_host] if allowed_host else [],
        )
        content, mime_type = download_url_with_limit(
            url=media.url,
            headers={"Authorization": AzureDevOpsIssueAdapter._auth_header(pat)},
            timeout_seconds=self.timeout_seconds,
            max_bytes=max_bytes,
            provider=self.provider_name,
            allowed_hosts=[allowed_host] if allowed_host else [],
        )
        return InlineMediaDownload(content, media.filename, mime_type or media.mime_type)


class IssueService:
    def __init__(self, config_resolver: ProviderConfigResolver) -> None:
        self.adapters: dict[str, IssueProviderAdapter] = {
            "azure_devops": AzureDevOpsIssueAdapter(config_resolver)
        }

    def resolve_proxy_target(
        self,
        *,
        issue_number: str,
        issue_source: str,
        metadata: dict[str, Any],
    ) -> tuple[str, str] | None:
        remote = metadata.get("remote") if isinstance(metadata.get("remote"), dict) else {}
        provider = str(remote.get("provider") or "").strip()
        remote_issue_id = str(remote.get("issue_id") or "").strip()

        # Compatibility reader (one release window): older records stored these keys at
        # metadata root instead of metadata.remote.
        if not provider:
            provider = str(metadata.get("provider") or "").strip()
        if not remote_issue_id:
            remote_issue_id = str(metadata.get("issue_id") or "").strip()
        if not remote_issue_id:
            for legacy_key in ("work_item_url", "url"):
                legacy_value = str(metadata.get(legacy_key) or "").strip()
                if not legacy_value:
                    continue
                parsed = _extract_ado_work_item_id(legacy_value)
                if parsed:
                    remote_issue_id = parsed
                    provider = provider or "azure_devops"
                    break

        if not provider:
            normalized_source = issue_source.strip().lower()
            if normalized_source in {"proxy_azure_devops", "azure_devops", "ado_proxy"}:
                provider = "azure_devops"

        if not remote_issue_id and provider:
            remote_issue_id = issue_number

        if provider and remote_issue_id:
            return provider, remote_issue_id
        return None

    def is_proxy_issue(
        self, *, issue_number: str, issue_source: str, metadata: dict[str, Any]
    ) -> bool:
        return (
            self.resolve_proxy_target(
                issue_number=issue_number, issue_source=issue_source, metadata=metadata
            )
            is not None
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
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.get_backlog_item_by_id(
            directory_path=directory_path,
            backlog_item_id=backlog_item_id,
            project=project,
            include_comments=include_comments,
            include_relations=include_relations,
            include_updates=include_updates,
        )


def _extract_ado_work_item_id(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    for pattern in (r"/workItems/edit/([0-9]+)", r"\b([0-9]{2,})\b"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""
