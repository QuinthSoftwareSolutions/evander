from __future__ import annotations

import base64
from dataclasses import dataclass
import os
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.text_formatting import looks_like_html, render_markdownish_text_to_html


def _format_ado_html_field(value: str | None) -> str | None:
    if value is None:
        return None
    if not value:
        return value
    if looks_like_html(value):
        return value
    return render_markdownish_text_to_html(value)


@dataclass(frozen=True)
class _AdoTeamFieldValue:
    field_reference_name: str
    value: str
    include_children: bool


class IssueProviderAdapter(Protocol):
    provider_name: str

    def read_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        include_comments: bool = True,
    ) -> dict[str, Any]: ...

    def post_comment(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        comment: str,
    ) -> dict[str, Any]: ...

    def update_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        state: str | None,
        fields: dict[str, Any] | None,
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
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
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
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
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
            normalized.append(
                {
                    "id": str(raw.get("id", "")).strip(),
                    "text": str(raw.get("text", "")).strip(),
                    "created_by": str((raw.get("createdBy") or {}).get("displayName", "")).strip(),
                    "created_date": self._normalize_datetime(
                        str(raw.get("createdDate", "")).strip()
                    ),
                    "url": str((raw.get("_links") or {}).get("web", {}).get("href", "")).strip(),
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
        return {
            "provider": self.provider_name,
            "remote_issue_id": str(payload.get("id") or remote_issue_id),
            "title": str(fields.get("System.Title") or ""),
            "description": str(fields.get("System.Description") or ""),
            "state": str(fields.get("System.State") or ""),
            "url": str(payload.get("url") or ""),
            "updated_at": self._normalize_datetime(str(fields.get("System.ChangedDate") or "")),
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

    def _normalize_relations(self, relations: object) -> list[dict[str, Any]]:
        if not isinstance(relations, list):
            return []
        normalized: list[dict[str, Any]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            attrs = (
                relation.get("attributes") if isinstance(relation.get("attributes"), dict) else {}
            )
            normalized.append(
                {
                    "rel": str(relation.get("rel") or "").strip() or None,
                    "url": str(relation.get("url") or "").strip() or None,
                    "attributes": self._json_safe(attrs),
                }
            )
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
        include_comments: bool = True,
    ) -> dict[str, Any]:
        organization_url, project, pat = self._resolve_connection(directory_path)
        auth_header = self._auth_header(pat)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            issue = self._fetch_issue(
                client,
                organization_url=organization_url,
                project=project,
                auth_header=auth_header,
                remote_issue_id=remote_issue_id,
            )
            if include_comments:
                issue["comments"] = self._fetch_comments(
                    client,
                    organization_url=organization_url,
                    project=project,
                    auth_header=auth_header,
                    remote_issue_id=remote_issue_id,
                )
            else:
                issue["comments"] = []
        return issue

    def post_comment(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        comment: str,
    ) -> dict[str, Any]:
        organization_url, project, pat = self._resolve_connection(directory_path)
        auth_header = self._auth_header(pat)
        comments_url = (
            f"{organization_url}/{project}/_apis/wit/workItems/{remote_issue_id}/comments"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                comments_url,
                params={"api-version": "7.1-preview.4"},
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                json={"text": comment},
            )
            response.raise_for_status()
            payload = response.json()
            return {
                "provider": self.provider_name,
                "remote_issue_id": str(remote_issue_id),
                "comment": {
                    "id": str(payload.get("id") or "").strip(),
                    "text": str(payload.get("text") or comment),
                    "url": str(
                        (payload.get("_links") or {}).get("web", {}).get("href") or ""
                    ).strip(),
                },
            }

    def update_issue(
        self,
        *,
        remote_issue_id: str,
        directory_path: str,
        state: str | None,
        fields: dict[str, Any] | None,
    ) -> dict[str, Any]:
        operations: list[dict[str, Any]] = []
        if state is not None:
            operations.append({"op": "add", "path": "/fields/System.State", "value": state})
        for field_name, value in (fields or {}).items():
            normalized = str(field_name).strip()
            if not normalized:
                continue
            operations.append({"op": "add", "path": f"/fields/{normalized}", "value": value})
        if not operations:
            return {
                "provider": self.provider_name,
                "remote_issue_id": str(remote_issue_id),
                "updated": False,
            }

        organization_url, project, pat = self._resolve_connection(directory_path)
        auth_header = self._auth_header(pat)
        work_item_url = f"{organization_url}/{project}/_apis/wit/workitems/{remote_issue_id}"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.patch(
                work_item_url,
                params={"api-version": "7.1"},
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json-patch+json",
                },
                json=operations,
            )
            response.raise_for_status()
        return {
            "provider": self.provider_name,
            "remote_issue_id": str(remote_issue_id),
            "updated": True,
            "state": state,
            "fields": fields or {},
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
        if description is not None:
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
        if acceptance_criteria is not None:
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

        with httpx.Client(timeout=self.timeout_seconds) as client:
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
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
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
                tags_any=tags_any,
                text_search=text_search,
                top=top,
                skip=skip,
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
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> dict[str, Any]:
        organization_url, configured_project, pat = self._resolve_connection(directory_path)
        normalized_project = str(project or configured_project).strip()
        if not normalized_project:
            raise ValueError(
                "project is required for Azure DevOps current-iteration backlog queries."
            )
        auth_header = self._auth_header(pat)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            normalized_team, iteration_path = self._resolve_current_iteration_path(
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
                team=normalized_team,
            )
            query_result = self._query_backlog_items_core(
                client,
                organization_url=organization_url,
                project=normalized_project,
                auth_header=auth_header,
                iteration=iteration_path,
                area_path=area_path,
                team_field_values=team_field_values,
                states=states,
                types=types,
                tags_any=tags_any,
                text_search=text_search,
                top=top,
                skip=skip,
            )
        return {
            "provider": self.provider_name,
            "project": normalized_project,
            "team": normalized_team,
            "iteration_path": iteration_path,
            "items": query_result.get("items") or [],
            "count": int(query_result.get("count") or 0),
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
        return normalized_team, iteration_path

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
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
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
                tags_any=tags_any,
                text_search=text_search,
            )
        wiql_url = f"{organization_url}/{quote(project, safe='')}/_apis/wit/wiql"
        select_fields = [
            "System.Id",
            "System.Title",
            "System.State",
            "System.WorkItemType",
            "System.IterationPath",
            "System.AreaPath",
            "System.AssignedTo",
            "System.Tags",
            "System.ChangedDate",
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
                batch_response = client.post(
                    batch_url,
                    params={"api-version": "7.1"},
                    headers={"Authorization": auth_header, "Content-Type": "application/json"},
                    json={
                        "ids": chunk_ids,
                        "fields": select_fields,
                        "errorPolicy": "Omit",
                    },
                )
                if batch_response.is_error:
                    self._raise_ado_http_error(
                        action="fetch backlog items batch", response=batch_response
                    )
                batch_payload = batch_response.json()
                values = batch_payload.get("value") if isinstance(batch_payload, dict) else None
                for raw_item in values or []:
                    if not isinstance(raw_item, dict):
                        continue
                    raw_fields = (
                        raw_item.get("fields") if isinstance(raw_item.get("fields"), dict) else {}
                    )
                    items.append(
                        {
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
                    )
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
                "relations": self._normalize_relations(payload.get("relations"))
                if include_relations
                else [],
            }
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
            return item


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
        include_comments: bool = True,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.read_issue(
            remote_issue_id=remote_issue_id,
            directory_path=directory_path,
            include_comments=include_comments,
        )

    def post_remote_comment(
        self,
        *,
        provider: str,
        remote_issue_id: str,
        directory_path: str,
        comment: str,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.post_comment(
            remote_issue_id=remote_issue_id,
            directory_path=directory_path,
            comment=comment,
        )

    def update_remote_issue(
        self,
        *,
        provider: str,
        remote_issue_id: str,
        directory_path: str,
        state: str | None,
        fields: dict[str, Any] | None,
    ) -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"Unsupported issue provider: {provider}")
        return adapter.update_issue(
            remote_issue_id=remote_issue_id,
            directory_path=directory_path,
            state=state,
            fields=fields,
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
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
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
            tags_any=tags_any,
            text_search=text_search,
            top=top,
            skip=skip,
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
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
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
            tags_any=tags_any,
            text_search=text_search,
            top=top,
            skip=skip,
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
