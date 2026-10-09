from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.pull_request_automation import build_pull_request_provider
from evander.services import GitHubPrQueryProvider

from ._pull_request import PullRequestQueryProviderBase


class PullRequestGetQueryProvider(PullRequestQueryProviderBase):
    """Fetch a single provider pull request from workflow nodes."""

    NODE_TYPE = "query:pull-request:get"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        provider_name = self._normalize_provider(
            self._require_string(payload.get("provider"), field_name="provider")
        )
        repo = self._require_string(payload.get("repo"), field_name="repo")
        pr_id = self._require_string(payload.get("pr_id"), field_name="pr_id")
        directory_path = self._normalize_optional_string(payload.get("directory_path"))
        if directory_path is None:
            directory_path = self._context_directory_path()
        ado_organization = self._normalize_optional_string(payload.get("ado_organization"))
        project = self._normalize_optional_string(payload.get("project"))
        include_reviewers = self._coerce_optional_bool(
            payload.get("include_reviewers"),
            field_name="include_reviewers",
        )
        include_commits = self._coerce_optional_bool(
            payload.get("include_commits"),
            field_name="include_commits",
        )

        if provider_name == "github":
            config = self._resolve_directory_config(directory_path)
            token_env = self._normalize_optional_string(getattr(config, "github_token_env", None))
            token = self._context().resolve_secret(token_env) if token_env else None
            timeout_seconds = float(getattr(config, "email_provider_timeout_seconds", 30.0) or 30.0)
            response = GitHubPrQueryProvider(
                token=token,
                timeout_seconds=timeout_seconds,
            ).get_pull_request(
                repo=repo,
                pr_id=pr_id,
                include_reviewers=include_reviewers,
                include_commits=include_commits,
            )
            return response.model_dump(mode="json")

        if provider_name != "azure_devops":
            raise ValueError(f"{self.NODE_TYPE} provider {provider_name!r} is not supported")

        _, _resolved_project, provider = build_pull_request_provider(
            context=self._context(),
            provider=provider_name,
            directory_path=directory_path,
            ado_organization=ado_organization,
            project=project,
            node_type=self.NODE_TYPE,
        )
        response = provider.get_pull_request(
            repo=repo,
            pr_id=pr_id,
            include_reviewers=include_reviewers,
            include_commits=include_commits,
        )
        return response.model_dump(mode="json")

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
            "include_reviewers",
            "include_commits",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PullRequestGetQueryProvider has no runtime context")
        return self.runtime_context

    @classmethod
    def _coerce_optional_bool(cls, value: Any, *, field_name: str) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError(f"{cls.NODE_TYPE} {field_name} must resolve to a boolean")
