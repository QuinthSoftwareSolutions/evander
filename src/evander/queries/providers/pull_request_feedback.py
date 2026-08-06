from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from evander.azure_devops import resolve_azure_devops_organization_url
from evander.pr_feedback import (
    AzureDevOpsFeedbackAdapter,
    GitHubFeedbackAdapter,
    pr_feedback_item_payload,
)

from .base import QueryProviderBase


class PullRequestFeedbackQueryProvider(QueryProviderBase):
    """Fetch provider pull-request feedback without owning cursor state."""

    NODE_TYPE = "query:pull-request:feedback"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        provider_name = self._normalize_provider(
            self._require_string(payload.get("provider"), field_name="provider")
        )
        repo = self._require_string(payload.get("repo"), field_name="repo")
        pr_id = self._require_string(payload.get("pr_id"), field_name="pr_id")
        cursor = self._normalize_optional_string(payload.get("cursor"))
        directory_path = self._normalize_optional_string(payload.get("directory_path"))
        if directory_path is None:
            directory_path = self._context_directory_path()

        adapter, resolved_project, timeout_seconds = self._build_adapter(
            provider_name=provider_name,
            repo=repo,
            directory_path=directory_path,
            ado_organization=self._normalize_optional_string(payload.get("ado_organization")),
            project=self._normalize_optional_string(payload.get("project")),
        )
        with httpx.Client(timeout=timeout_seconds) as client:
            result = adapter.poll(client=client, repo=repo, pr_id=pr_id, cursor=cursor)

        feedback = [pr_feedback_item_payload(envelope) for envelope in result.feedback]
        return {
            "provider": provider_name,
            "repo": repo,
            "pr_id": pr_id,
            "project": resolved_project,
            "feedback": feedback,
            "count": len(feedback),
            "next_cursor": result.next_cursor,
        }

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "provider",
            "repo",
            "pr_id",
            "directory_path",
            "ado_organization",
            "project",
            "cursor",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _build_adapter(
        self,
        *,
        provider_name: str,
        repo: str,
        directory_path: str,
        ado_organization: str | None,
        project: str | None,
    ) -> tuple[Any, str | None, float]:
        config = self._resolve_directory_config(directory_path)
        timeout_seconds = float(getattr(config, "email_provider_timeout_seconds", 30.0) or 30.0)
        if provider_name == "github":
            token_env = self._normalize_optional_string(getattr(config, "github_token_env", None))
            token = self._context().resolve_secret(token_env) if token_env else None
            return GitHubFeedbackAdapter(token=token), project, timeout_seconds

        if provider_name != "azure_devops":
            raise ValueError(f"{self.NODE_TYPE} provider {provider_name!r} is not supported")

        resolved_organization_url = resolve_azure_devops_organization_url(
            ado_organization=ado_organization,
            legacy_organization_url=getattr(config, "azure_devops_organization_url", None),
            node_type=self.NODE_TYPE,
        )
        configured_project = self._normalize_optional_string(
            getattr(config, "azure_devops_project", None)
        )
        resolved_project = project or configured_project or self._project_from_composite_repo(repo)
        if not resolved_project:
            raise ValueError(f"{self.NODE_TYPE} requires project for Azure DevOps")
        pat_env = self._normalize_optional_string(getattr(config, "azure_devops_pr_pat_env", None))
        if not pat_env:
            raise ValueError(f"{self.NODE_TYPE} requires azure_devops_pr_pat_env")
        pat = self._context().resolve_secret(pat_env)
        if not pat:
            raise RuntimeError(f"{self.NODE_TYPE} Azure DevOps PAT is not configured in {pat_env}")
        return (
            AzureDevOpsFeedbackAdapter(
                organization_url=resolved_organization_url,
                project=resolved_project,
                pat=pat,
                pat_env_name=pat_env,
            ),
            resolved_project,
            timeout_seconds,
        )

    def _resolve_directory_config(self, directory_path: str):
        resolver = getattr(self._context(), "provider_config_resolver", None)
        if resolver is None:
            raise RuntimeError(f"{self.NODE_TYPE} provider_config_resolver is not available")
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if not callable(resolve_for_directory):
            raise RuntimeError(
                f"{self.NODE_TYPE} provider_config_resolver cannot resolve directories"
            )
        return resolve_for_directory(directory_path)

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PullRequestFeedbackQueryProvider has no runtime context")
        return self.runtime_context

    def _context_directory_path(self) -> str:
        metadata = self._context().metadata
        return self._normalize_optional_string(metadata.get("directory_path")) or "root"

    @classmethod
    def _require_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if not text:
            raise ValueError(f"{cls.NODE_TYPE} requires {field_name}")
        return text

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.strip()
        if normalized in {"ado", "azure-devops", "azure_devops"}:
            return "azure_devops"
        return normalized

    @staticmethod
    def _project_from_composite_repo(repo: str) -> str | None:
        if "://" in repo or repo.startswith("git@"):
            return None
        parts = [part.strip() for part in repo.split("/") if part.strip()]
        if len(parts) == 3:
            return parts[-2]
        return None

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
