from __future__ import annotations
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import time
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse
import httpx
from evander.ado_builds import positive_id
from evander.ado_mentions import render_ado_mentions_html_prefix
from evander_azure_devops_addon.mentions import resolve_ado_mentions
from evander_azure_devops_addon.reports import DEFAULT_EFFORT_FIELDS
from evander.inline_media import (
    InlineMediaDownload,
    download_url_with_limit,
    inline_media_download_from_embedded_payload,
    require_provider_media_url,
)
from evander.issue_owner import assigned_to_identity
from evander.issues.activity_cache import AdoActivityScanCache, ActivityScanResult
from evander.models import InlineMediaRef
from evander.provider_config import ProviderConfigResolver

from evander.issues.service import (
    LOGGER,
    _ADO_PROXY_SYNC_FIELDS,
    _ADO_WORK_ITEMS_BATCH_LIMIT,
    _ADO_ACTIVITY_FETCH_WORKERS,
    _filename_from_media_url,
    _relation_is_media_reference,
    _make_ado_media_ref,
    extract_ado_html_image_refs,
    extract_ado_field_inline_media_refs,
    _format_ado_html_field,
    _parse_ado_datetime_optional,
    _datetime_in_range,
    _ado_activity_iso,
    _ado_activity_identity_matches,
    _ado_activity_max_candidates,
    _ado_update_field_new_value,
    _ado_meaningful_update_fields,
    _artifact_filename,
    _artifact_mime_type,
    _artifact_is_image,
    _artifact_bytes,
    _append_html_attachment_links,
    _AdoTeamFieldValue,
    _AdoCurrentIteration,
    _normalize_issue_provider,
 )

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
        from evander.optional_integrations import require_azure_devops_module
        require_azure_devops_module(config=config)
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
            clauses.append(f"[{field_reference_name}] {operator} {cls._wiql_quote(item.value)}")
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
        page_limit: int = 1,
        paging: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        comments_url = (
            f"{organization_url}/{project}/_apis/wit/workItems/{remote_issue_id}/comments"
        )
        entries = []
        continuation = None
        seen_tokens = set()
        if not 1 <= page_limit <= 10:
            raise ValueError("comment page limit must be between 1 and 10")
        for page_index in range(page_limit):
            params = {
                "api-version": "7.1-preview.4",
                "$top": str(self.comments_max_count),
                "order": "desc",
            }
            if continuation:
                params["continuationToken"] = continuation
            response = client.get(
                comments_url, params=params, headers={"Authorization": auth_header}
            )
            response.raise_for_status()
            payload = response.json()
            page_entries = payload.get("comments")
            if not isinstance(page_entries, list):
                raise ValueError("invalid ADO comments response")
            entries.extend(page_entries)
            continuation = payload.get("continuationToken") or response.headers.get(
                "x-ms-continuationtoken"
            )
            if not continuation or continuation in seen_tokens:
                break
            seen_tokens.add(continuation)
        if paging is not None:
            paging.update(
                complete=not bool(continuation),
                has_more=bool(continuation),
                pages=page_index + 1,
                page_limit=page_limit,
                returned_count=len(entries),
            )
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
                    "modified_at": self._normalize_datetime(
                        str(raw.get("modifiedDate", "")).strip()
                    ),
                    "version": raw.get("version"),
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
            "acceptance_criteria": str(
                fields.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""
            ),
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
            "acceptance_criteria": str(
                fields.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""
            ).strip(),
            "state": str(fields.get("System.State") or "").strip() or None,
            "type": str(fields.get("System.WorkItemType") or "").strip() or None,
            "area": str(fields.get("System.AreaPath") or "").strip() or None,
            "iteration": str(fields.get("System.IterationPath") or "").strip() or None,
            "assigned_to": self._normalize_assigned_to(fields.get("System.AssignedTo")),
            "assigned_to_identity": assigned_to_identity(fields.get("System.AssignedTo")),
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
                            "revised_by_identity": self._json_safe(entry.get("revisedBy")),
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
        normalized_organization_url = str(organization_url or configured_organization_url).rstrip(
            "/"
        )
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
        correlation_marker: str | None = None,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps issue comments.")
        auth_header = self._auth_header(pat)
        comments_url = f"{organization_url}/{normalized_project}/_apis/wit/workItems/{remote_issue_id}/comments"
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
            formatted_comment = render_ado_mentions_html_prefix(
                _format_ado_html_field(comment) or "",
                resolved_mentions,
            )
            if correlation_marker:
                formatted_comment = f"{formatted_comment}\n{correlation_marker.strip()}"
            if uploaded_attachments:
                formatted_comment = (
                    _append_html_attachment_links(formatted_comment, uploaded_attachments) or ""
                )
            response = client.post(
                comments_url,
                params={"api-version": "7.1-preview.4", "format": "html"},
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

    def delete_comment(
        self,
        *,
        remote_issue_id: str,
        comment_id: str,
        directory_path: str,
        project: str | None = None,
    ) -> dict[str, Any]:
        work_item_id = str(positive_id(remote_issue_id.strip(), "id"))
        comment_id = str(positive_id(comment_id.strip(), "comment_id"))
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps comment deletion.")
        url = (
            f"{organization_url}/{quote(normalized_project, safe='')}/_apis/wit/"
            f"workItems/{work_item_id}/comments/{comment_id}"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.delete(
                url,
                params={"api-version": "7.1-preview.4"},
                headers={"Authorization": self._auth_header(pat)},
            )
            if response.is_error:
                self._raise_ado_http_error(action="delete work item comment", response=response)
            response.raise_for_status()
        return {
            "status": "deleted",
            "provider": self.provider_name,
            "work_item_id": work_item_id,
            "comment_id": comment_id,
            "project": normalized_project,
            "deleted": True,
        }

    def resolve_comment_mentions(
        self,
        *,
        directory_path: str,
        mentions: list[str] | None,
    ) -> list[dict[str, str]]:
        """Resolve mention references without posting a comment."""
        organization_url, _project, pat = self._resolve_connection(directory_path)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resolved_mentions = resolve_ado_mentions(
                client=client,
                organization_url=organization_url,
                pat=pat,
                mention_refs=mentions,
            )
        return [mention.audit_payload() for mention in resolved_mentions]

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
        work_item_url = (
            f"{organization_url}/{normalized_project}/_apis/wit/workitems/{remote_issue_id}"
        )
        uploaded_attachments: list[dict[str, Any]] = []
        operations: list[dict[str, Any]] = []
        normalized_expected_state = (
            str(expected_state).strip() if expected_state is not None else ""
        )
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
        _client: httpx.Client | None = None,
        _connection: tuple[str, str, str] | None = None,
        _full_details: bool = False,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = (
            _connection if _connection is not None else self._resolve_connection(directory_path)
        )
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps backlog queries.")
        auth_header = self._auth_header(pat)
        with (
            nullcontext(_client)
            if _client is not None
            else httpx.Client(timeout=self.timeout_seconds) as client
        ):
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
                full_details=_full_details,
            )

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
        _activity_cache: AdoActivityScanCache | None = None,
        _cache_scope: str | None = None,
    ) -> dict[str, Any]:
        connection = self._resolve_connection(directory_path)
        organization_url, configured_project, pat = connection
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps work item activity queries.")
        normalized_top = max(1, min(int(top), 200))
        normalized_skip = max(0, int(skip))
        normalized_max_candidates = _ado_activity_max_candidates(max_candidates)
        normalized_types = sorted(
            {str(value or "").strip().casefold() for value in (activity_types or [])}
        )
        normalized_types = normalized_types or ["created", "changed", "commented"]

        def load() -> ActivityScanResult:
            return self._scan_work_item_activity(
                directory_path=directory_path,
                project=normalized_project,
                actor=actor,
                since=since,
                until=until,
                activity_types=normalized_types,
                states=states,
                types=types,
                tags_any=tags_any,
                exhaustive=exhaustive,
                max_candidates=normalized_max_candidates,
                _connection=connection,
            )

        if _activity_cache is None or _cache_scope is None:
            return load().page(top=normalized_top, skip=normalized_skip)
        return _activity_cache.query(
            directory_path=directory_path,
            key_parts=[
                _cache_scope,
                directory_path,
                organization_url,
                normalized_project,
                pat,
                str(actor or "").strip().casefold(),
                _ado_activity_iso(since),
                _ado_activity_iso(until) if until is not None else None,
                normalized_types,
                sorted(set(states or [])),
                sorted(set(types or [])),
                sorted(set(tags_any or [])),
                exhaustive,
                normalized_max_candidates,
                self.comments_max_count,
            ],
            load=load,
            top=normalized_top,
            skip=normalized_skip,
        )

    def _fetch_activity_candidates(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        auth_header: str,
        ids: list[int],
    ) -> list[dict[str, Any]]:
        response = client.post(
            f"{organization_url}/_apis/wit/workitemsbatch",
            params={"api-version": "7.1"},
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            json={"ids": ids, "$expand": "All", "errorPolicy": "Omit"},
        )
        if response.is_error:
            self._raise_ado_http_error(action="fetch activity candidates batch", response=response)
        by_id = {
            str(item.get("id")): item
            for item in response.json().get("value", [])
            if isinstance(item, dict)
        }
        return [by_id[str(item_id)] for item_id in ids if str(item_id) in by_id]

    def _scan_work_item_activity(
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
        exhaustive: bool = False,
        max_candidates: int = 50,
        _connection: tuple[str, str, str],
    ) -> ActivityScanResult:
        scan_started = time.perf_counter()
        organization_url, configured_project, pat = _connection
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps work item activity queries.")
        normalized_types = {str(item or "").strip().casefold() for item in (activity_types or [])}
        if not normalized_types:
            normalized_types = {"created", "changed", "commented"}
        auth_header = self._auth_header(pat)
        wiql = self._build_activity_wiql_query(
            project=normalized_project,
            since=since,
            until=until,
            activity_types=sorted(normalized_types),
            states=states or [],
            types=types or [],
            tags_any=tags_any or [],
        )
        normalized_max_candidates = _ado_activity_max_candidates(max_candidates)
        candidate_page_size = 200 if exhaustive else min(200, normalized_max_candidates)
        candidate_scanned_count = 0
        matched_items: list[dict[str, Any]] = []
        batch_ms = 0.0
        histories_ms = 0.0

        def load_activity_item(candidate: dict[str, Any]) -> dict[str, Any] | None:
            item_id = str(candidate.get("id") or "").strip()
            if not item_id:
                return None
            detail = self._backlog_item_from_payload(
                candidate,
                client=client,
                organization_url=organization_url,
                auth_header=auth_header,
                backlog_item_id=item_id,
                project=normalized_project,
                include_comments="commented" in normalized_types,
                include_relations=False,
                include_updates="changed" in normalized_types,
            )
            activities = self._work_item_activity_records(
                detail,
                actor=actor,
                since=since,
                until=until,
                activity_types=normalized_types,
            )
            if not activities:
                return None
            return {
                "provider": self.provider_name,
                "id": detail.get("id"),
                "project": detail.get("project") or normalized_project,
                "title": detail.get("title"),
                "state": detail.get("state"),
                "type": detail.get("type"),
                "url": detail.get("url"),
                "web_url": detail.get("web_url"),
                "activities": activities,
                "fields": detail.get("fields") or {},
            }

        with (
            httpx.Client(timeout=self.timeout_seconds) as client,
            ThreadPoolExecutor(max_workers=_ADO_ACTIVITY_FETCH_WORKERS) as workers,
        ):
            candidate_ids = self._run_work_item_wiql(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                wiql=wiql,
            )
            wiql_ms = (time.perf_counter() - scan_started) * 1000
            candidate_total_matches = len(candidate_ids)
            candidate_limit = (
                candidate_total_matches
                if exhaustive
                else min(candidate_total_matches, normalized_max_candidates)
            )
            candidate_scan_complete = candidate_limit == candidate_total_matches
            # Omitted/deleted IDs still consume their positions in the frozen WIQL list.
            for candidate_skip in range(0, candidate_limit, candidate_page_size):
                batch_started = time.perf_counter()
                ids = candidate_ids[
                    candidate_skip : min(candidate_skip + candidate_page_size, candidate_limit)
                ]
                candidate_items = self._fetch_activity_candidates(
                    client,
                    organization_url=organization_url,
                    auth_header=auth_header,
                    ids=ids,
                )
                batch_ms += (time.perf_counter() - batch_started) * 1000
                history_started = time.perf_counter()
                candidate_scanned_count += len(candidate_items)
                page_results = (
                    workers.map(load_activity_item, candidate_items)
                    if len(candidate_items) > 1
                    else map(load_activity_item, candidate_items)
                )
                matched_items.extend(item for item in page_results if item is not None)
                histories_ms += (time.perf_counter() - history_started) * 1000
        total_activity_matches = len(matched_items)
        paging = {
            "total_matches": total_activity_matches,
            "total_activity_matches": total_activity_matches,
            "candidate_total_matches": candidate_total_matches,
            "candidate_scanned_count": candidate_scanned_count,
            "candidate_scan_complete": candidate_scan_complete,
            "total_matches_complete": candidate_scan_complete,
            "total_activity_matches_complete": candidate_scan_complete,
            "activity_types": sorted(normalized_types),
            "exhaustive": exhaustive,
            "max_candidates": normalized_max_candidates,
        }
        LOGGER.info(
            "ado_activity_scan duration_ms=%.3f wiql_ms=%.3f batch_ms=%.3f histories_ms=%.3f "
            "candidate_total=%d candidate_scanned=%d matched=%d",
            (time.perf_counter() - scan_started) * 1000,
            wiql_ms,
            batch_ms,
            histories_ms,
            candidate_total_matches,
            candidate_scanned_count,
            total_activity_matches,
        )
        return ActivityScanResult(
            items=matched_items,
            metadata={
                "provider": self.provider_name,
                "project": normalized_project,
                "paging": paging,
            },
        )

    def _build_activity_wiql_query(
        self,
        *,
        project: str,
        since: datetime,
        until: datetime | None,
        activity_types: list[str],
        states: list[str],
        types: list[str],
        tags_any: list[str],
    ) -> str:
        where_clauses: list[str] = [f"[System.TeamProject] = {self._wiql_quote(project)}"]
        state_clause = self._wiql_in_clause("System.State", states)
        if state_clause:
            where_clauses.append(state_clause)
        type_clause = self._wiql_in_clause("System.WorkItemType", types)
        if type_clause:
            where_clauses.append(type_clause)
        if tags_any:
            tag_clauses = [
                f"[System.Tags] CONTAINS {self._wiql_quote(tag)}"
                for tag in tags_any
                if str(tag).strip()
            ]
            if tag_clauses:
                where_clauses.append("(" + " OR ".join(tag_clauses) + ")")
        since_text = self._wiql_quote(_ado_activity_iso(since))
        until_text = self._wiql_quote(_ado_activity_iso(until)) if until is not None else None
        date_clauses: list[str] = []
        if "created" in activity_types:
            clause = f"[System.CreatedDate] >= {since_text}"
            if until_text:
                clause = f"({clause} AND [System.CreatedDate] <= {until_text})"
            date_clauses.append(clause)
        if "changed" in activity_types or "commented" in activity_types:
            clause = f"[System.ChangedDate] >= {since_text}"
            if until_text:
                clause = f"({clause} AND [System.ChangedDate] <= {until_text})"
            date_clauses.append(clause)
        if date_clauses:
            where_clauses.append("(" + " OR ".join(date_clauses) + ")")
        where = " AND ".join(where_clauses)
        return (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE {where} "
            "ORDER BY [System.ChangedDate] DESC, [System.Id] DESC"
        )

    def _work_item_activity_records(
        self,
        item: dict[str, Any],
        *,
        actor: str | None,
        since: datetime,
        until: datetime | None,
        activity_types: set[str],
    ) -> list[dict[str, Any]]:
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        records: list[dict[str, Any]] = []
        if "created" in activity_types:
            created_at = _parse_ado_datetime_optional(fields.get("System.CreatedDate"))
            created_by = fields.get("System.CreatedBy")
            if _datetime_in_range(
                created_at, since=since, until=until
            ) and _ado_activity_identity_matches(actor, created_by):
                identity = assigned_to_identity(created_by) or {}
                records.append(
                    {
                        "activity_type": "created",
                        "provenance": "created_field",
                        "occurred_at": created_at,
                        "actor": self._normalize_assigned_to(created_by),
                        "actor_identity": identity,
                        "summary": "Work item created",
                        "raw": {
                            "field": "System.CreatedDate",
                            "value": fields.get("System.CreatedDate"),
                        },
                    }
                )
        if "changed" in activity_types:
            changed_at = _parse_ado_datetime_optional(fields.get("System.ChangedDate"))
            changed_by = fields.get("System.ChangedBy")
            if _datetime_in_range(
                changed_at, since=since, until=until
            ) and _ado_activity_identity_matches(actor, changed_by):
                identity = assigned_to_identity(changed_by) or {}
                records.append(
                    {
                        "activity_type": "changed",
                        "provenance": "changed_field",
                        "occurred_at": changed_at,
                        "actor": self._normalize_assigned_to(changed_by),
                        "actor_identity": identity,
                        "summary": "Work item latest change",
                        "raw": {
                            "field": "System.ChangedDate",
                            "value": fields.get("System.ChangedDate"),
                        },
                    }
                )
            for update in item.get("updates") or []:
                if not isinstance(update, dict):
                    continue
                changed_fields = (
                    update.get("fields") if isinstance(update.get("fields"), dict) else {}
                )
                meaningful_fields = _ado_meaningful_update_fields(changed_fields)
                if changed_fields and not meaningful_fields:
                    continue
                semantic_changed_by = _ado_update_field_new_value(
                    changed_fields, "System.ChangedBy"
                )
                semantic_changed_at = _ado_update_field_new_value(
                    changed_fields, "System.ChangedDate"
                )
                revised_at = _parse_ado_datetime_optional(
                    semantic_changed_at
                ) or _parse_ado_datetime_optional(update.get("revised_at"))
                revised_by = (
                    self._normalize_assigned_to(semantic_changed_by)
                    if semantic_changed_by is not None
                    else None
                ) or update.get("revised_by")
                revised_identity = (
                    assigned_to_identity(semantic_changed_by)
                    if semantic_changed_by is not None
                    else None
                )
                if not revised_identity:
                    revised_identity = (
                        update.get("revised_by_identity")
                        if isinstance(update.get("revised_by_identity"), dict)
                        else {}
                    )
                if not _datetime_in_range(revised_at, since=since, until=until):
                    continue
                if not _ado_activity_identity_matches(actor, revised_identity or revised_by):
                    continue
                records.append(
                    {
                        "activity_type": "changed",
                        "provenance": "update",
                        "occurred_at": revised_at,
                        "actor": str(revised_by or "").strip() or None,
                        "actor_identity": revised_identity,
                        "summary": "Updated fields: " + ", ".join(sorted(meaningful_fields.keys()))
                        if meaningful_fields
                        else "Work item update",
                        "fields": meaningful_fields,
                        "raw": update,
                    }
                )
        if "commented" in activity_types:
            for comment in item.get("comments") or []:
                if not isinstance(comment, dict):
                    continue
                created_at = _parse_ado_datetime_optional(comment.get("created_date"))
                identity = (
                    comment.get("created_by_identity")
                    if isinstance(comment.get("created_by_identity"), dict)
                    else {}
                )
                actor_value: Any = identity or comment.get("created_by")
                if not _datetime_in_range(created_at, since=since, until=until):
                    continue
                if not _ado_activity_identity_matches(actor, actor_value):
                    continue
                records.append(
                    {
                        "activity_type": "commented",
                        "provenance": "comment",
                        "occurred_at": created_at,
                        "actor": comment.get("created_by"),
                        "actor_identity": identity,
                        "summary": " ".join(str(comment.get("text") or "").split())[:240] or None,
                        "comment_id": comment.get("id"),
                        "raw": comment,
                    }
                )
        records.sort(key=lambda record: record["occurred_at"], reverse=True)
        return records

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
        _client: httpx.Client | None = None,
        _connection: tuple[str, str, str] | None = None,
        _full_details: bool = False,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = (
            _connection if _connection is not None else self._resolve_connection(directory_path)
        )
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError(
                "project is required for Azure DevOps current-iteration backlog queries."
            )
        auth_header = self._auth_header(pat)
        with (
            nullcontext(_client)
            if _client is not None
            else httpx.Client(timeout=self.timeout_seconds) as client
        ):
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
                full_details=_full_details,
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
        work_item_ids: list[int] | None = None,
        full_details: bool = False,
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
        select_fields = [
            "System.Id",
            "System.Title",
            "System.Description",
            "System.State",
            "System.WorkItemType",
            "System.CreatedDate",
            "System.CreatedBy",
            "System.IterationPath",
            "System.AreaPath",
            "System.AssignedTo",
            "System.Tags",
            "System.ChangedDate",
            "System.ChangedBy",
            "Microsoft.VSTS.TCM.ReproSteps",
            "Microsoft.VSTS.TCM.SystemInfo",
            "Microsoft.VSTS.Common.AcceptanceCriteria",
            *DEFAULT_EFFORT_FIELDS,
        ]
        if work_item_ids is None:
            work_item_ids = self._run_work_item_wiql(
                client,
                organization_url=organization_url,
                project=project,
                auth_header=auth_header,
                wiql=normalized_wiql,
            )
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
                if full_details:
                    # Comment polling needs the same fields and relations as a
                    # detail read, supplied once for the whole selected batch.
                    batch_body.pop("fields")
                    batch_body["$expand"] = "All"
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
                if include_relations and not full_details:
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
                    if full_details:
                        items.append(
                            self._backlog_item_from_payload(
                                raw_item,
                                client=client,
                                organization_url=organization_url,
                                project=project,
                                auth_header=auth_header,
                                backlog_item_id=str(raw_item.get("id") or ""),
                                include_comments=False,
                                include_relations=True,
                                include_updates=False,
                            )
                        )
                        continue
                    raw_fields = (
                        raw_item.get("fields") if isinstance(raw_item.get("fields"), dict) else {}
                    )
                    item = {
                        "id": str(raw_item.get("id") or "").strip(),
                        "title": str(raw_fields.get("System.Title") or "").strip(),
                        "description": str(raw_fields.get("System.Description") or "").strip(),
                        "acceptance_criteria": str(
                            raw_fields.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""
                        ).strip(),
                        "state": str(raw_fields.get("System.State") or "").strip() or None,
                        "type": str(raw_fields.get("System.WorkItemType") or "").strip() or None,
                        "iteration": str(raw_fields.get("System.IterationPath") or "").strip()
                        or None,
                        "area": str(raw_fields.get("System.AreaPath") or "").strip() or None,
                        "assigned_to": self._normalize_assigned_to(
                            raw_fields.get("System.AssignedTo")
                        ),
                        "assigned_to_identity": assigned_to_identity(
                            raw_fields.get("System.AssignedTo")
                        ),
                        "rev": raw_item.get("rev"),
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
        consumed_count = len(paged_ids)
        next_skip = (
            normalized_skip + consumed_count
            if normalized_skip + consumed_count < total_matches
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

    def _run_work_item_wiql(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        wiql: str,
    ) -> list[int]:
        wiql_url = f"{organization_url}/{quote(project, safe='')}/_apis/wit/wiql"
        response = client.post(
            wiql_url,
            params={"api-version": "7.1"},
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            json={"query": wiql},
        )
        if response.is_error:
            self._raise_ado_http_error(action="query backlog items", response=response)
        payload = response.json()
        raw_work_items = payload.get("workItems") if isinstance(payload, dict) else None
        return [
            item["id"]
            for item in (raw_work_items or [])
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        ]

    def get_backlog_item_by_id(
        self,
        *,
        directory_path: str,
        backlog_item_id: str,
        project: str | None = None,
        include_comments: bool = True,
        include_relations: bool = True,
        include_updates: bool = False,
        comment_page_limit: int = 1,
        _client: httpx.Client | None = None,
        _connection: tuple[str, str, str] | None = None,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = (
            _connection if _connection is not None else self._resolve_connection(directory_path)
        )
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps backlog item retrieval.")
        auth_header = self._auth_header(pat)
        work_item_url = f"{organization_url}/{quote(normalized_project, safe='')}/_apis/wit/workitems/{backlog_item_id}"
        client_context = (
            nullcontext(_client)
            if _client is not None
            else httpx.Client(timeout=self.timeout_seconds)
        )
        with client_context as client:
            response = client.get(
                work_item_url,
                params={"api-version": "7.1", "$expand": "All"},
                headers={"Authorization": auth_header},
            )
            if response.is_error:
                self._raise_ado_http_error(action="get backlog item by id", response=response)
            payload = response.json()
            return self._backlog_item_from_payload(
                payload,
                client=client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                backlog_item_id=backlog_item_id,
                include_comments=include_comments,
                include_relations=include_relations,
                include_updates=include_updates,
                comment_page_limit=comment_page_limit,
            )

    def _backlog_item_from_payload(
        self,
        payload: dict[str, Any],
        *,
        client: httpx.Client,
        organization_url: str,
        project: str,
        auth_header: str,
        backlog_item_id: str,
        include_comments: bool,
        include_relations: bool,
        include_updates: bool,
        comment_page_limit: int = 1,
    ) -> dict[str, Any]:
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        project_name = str(fields.get("System.TeamProject") or project).strip() or None
        item: dict[str, Any] = {
            "provider": self.provider_name,
            "organization_url": organization_url,
            "id": str(payload.get("id") or backlog_item_id).strip(),
            "project": project_name,
            "rev": payload.get("rev"),
            "url": str(payload.get("url") or "").strip() or None,
            "web_url": str((payload.get("_links") or {}).get("web", {}).get("href") or "").strip()
            or None,
            "title": str(fields.get("System.Title") or "").strip() or None,
            "description": str(fields.get("System.Description") or "").strip(),
            "acceptance_criteria": str(
                fields.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""
            ).strip(),
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
        item["comments_paging"] = {"complete": False, "reason": "not_requested"}
        item["comments"] = (
            self._fetch_comments(
                client,
                organization_url=organization_url,
                project=project_name or project,
                auth_header=auth_header,
                remote_issue_id=item["id"],
                page_limit=comment_page_limit,
                paging=item["comments_paging"],
            )
            if include_comments
            else []
        )
        item["updates"] = (
            self._fetch_updates(
                client,
                organization_url=organization_url,
                project=project_name or project,
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

    def delete_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        project: str | None = None,
        destroy: bool = False,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError("project is required for Azure DevOps backlog item deletion.")
        auth_header = self._auth_header(pat)
        delete_url = f"{organization_url}/{quote(normalized_project, safe='')}/_apis/wit/workItems/{remote_issue_id}"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.delete(
                delete_url,
                params={
                    "api-version": "7.1",
                    "destroy": "true" if destroy else "false",
                },
                headers={"Authorization": auth_header},
            )
            if response.is_error:
                self._raise_ado_http_error(action="delete work item", response=response)
            payload: dict[str, Any] = {}
            if response.content:
                try:
                    candidate = response.json()
                except ValueError:
                    candidate = {}
                if isinstance(candidate, dict):
                    payload = candidate
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            project_name = (
                str(
                    payload.get("project") or fields.get("System.TeamProject") or normalized_project
                ).strip()
                or normalized_project
            )
            return {
                "provider": self.provider_name,
                "work_item_id": str(payload.get("id") or remote_issue_id).strip(),
                "project": project_name,
                "deleted": True,
                "destroy": destroy,
                "delete_kind": "permanent_destroy" if destroy else "soft_delete",
            }


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


class AdoCommentPollReader:
    """One directory's query, detail projection and comments share a short-lived client."""

    def __init__(
        self,
        adapter: AzureDevOpsIssueAdapter,
        *,
        directory_path: str,
        connection: tuple[str, str, str],
        client: httpx.Client,
    ) -> None:
        self.adapter = adapter
        self.directory_path = directory_path
        self.connection = connection
        self.client = client
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.with_comments: set[tuple[str, str]] = set()

    def _check_scope(self, provider: str, directory_path: str) -> None:
        if _normalize_issue_provider(provider) != "azure_devops":
            raise ValueError("ADO comment polling only supports Azure DevOps")
        if directory_path != self.directory_path:
            raise ValueError("ADO comment poll directory cannot change")

    def _remember(self, result: dict[str, Any]) -> dict[str, Any]:
        for item in result.get("items", []):
            key = (str(item.get("project") or result.get("project") or ""), str(item["id"]))
            self.items[key] = item
        return result

    def query_remote_backlog_items(
        self, *, provider: str, directory_path: str, **query: Any
    ) -> dict[str, Any]:
        self._check_scope(provider, directory_path)
        return self._remember(
            self.adapter.query_backlog_items(
                directory_path=directory_path,
                _client=self.client,
                _connection=self.connection,
                _full_details=True,
                **query,
            )
        )

    def list_remote_current_iteration_backlog_items(
        self, *, provider: str, directory_path: str, **query: Any
    ) -> dict[str, Any]:
        self._check_scope(provider, directory_path)
        return self._remember(
            self.adapter.list_current_iteration_backlog_items(
                directory_path=directory_path,
                _client=self.client,
                _connection=self.connection,
                _full_details=True,
                **query,
            )
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
        self._check_scope(provider, directory_path)
        if not include_comments or not include_relations or include_updates:
            raise ValueError("ADO comment poll requires comments and relations without updates")
        organization_url, configured_project, pat = self.connection
        normalized_project = str(project or configured_project).strip()
        key = (normalized_project, backlog_item_id)
        item = self.items[key]
        if key not in self.with_comments:
            paging = dict(item["comments_paging"])
            comments = self.adapter._fetch_comments(
                self.client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=self.adapter._auth_header(pat),
                remote_issue_id=backlog_item_id,
                paging=paging,
            )
            item = dict(item)
            item["comments"] = comments
            item["comments_paging"] = paging
            item["inline_media"] = [
                *item["inline_media"],
                *(media for comment in comments for media in comment.get("inline_media", [])),
            ]
            self.items[key] = item
            self.with_comments.add(key)
        return item
