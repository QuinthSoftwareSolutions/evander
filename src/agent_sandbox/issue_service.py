from __future__ import annotations

import base64
import os
import re
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from .provider_config import ProviderConfigResolver


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
            raise ValueError("Azure DevOps provider config must define organization URL and project.")
        if not pat_value:
            raise ValueError(f"Environment variable {pat_env_name} is required for Azure DevOps issue operations.")
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

    def _fetch_comments(
        self,
        client: httpx.Client,
        *,
        organization_url: str,
        project: str,
        auth_header: str,
        remote_issue_id: str,
    ) -> list[dict[str, Any]]:
        comments_url = f"{organization_url}/{project}/_apis/wit/workItems/{remote_issue_id}/comments"
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
                    "created_date": self._normalize_datetime(str(raw.get("createdDate", "")).strip()),
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
        comments_url = f"{organization_url}/{project}/_apis/wit/workItems/{remote_issue_id}/comments"
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
                    "url": str((payload.get("_links") or {}).get("web", {}).get("href") or "").strip(),
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
            return {"provider": self.provider_name, "remote_issue_id": str(remote_issue_id), "updated": False}

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

    def is_proxy_issue(self, *, issue_number: str, issue_source: str, metadata: dict[str, Any]) -> bool:
        return self.resolve_proxy_target(issue_number=issue_number, issue_source=issue_source, metadata=metadata) is not None

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


def _extract_ado_work_item_id(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    for pattern in (r"/workItems/edit/([0-9]+)", r"\b([0-9]{2,})\b"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""
