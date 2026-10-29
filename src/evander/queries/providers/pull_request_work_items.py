from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.pull_request_automation import build_pull_request_provider

from ._pull_request import PullRequestQueryProviderBase


class PullRequestWorkItemsQueryProvider(PullRequestQueryProviderBase):
    """Fetch Azure DevOps work items linked to one pull request."""

    NODE_TYPE = "query:pull-request:work-items"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        provider_name = self._normalize_provider(
            self._require_string(payload.get("provider"), field_name="provider")
        )
        if provider_name != "azure_devops":
            raise ValueError(f"{self.NODE_TYPE} provider {provider_name!r} is not supported")
        repo = self._require_string(payload.get("repo"), field_name="repo")
        pr_id = self._require_string(payload.get("pr_id"), field_name="pr_id")
        directory_path = self._normalize_optional_string(payload.get("directory_path"))
        if directory_path is None:
            directory_path = self._context_directory_path()
        ado_organization = self._normalize_optional_string(payload.get("ado_organization"))
        project = self._normalize_optional_string(payload.get("project"))

        _, _, provider = build_pull_request_provider(
            context=self._context(),
            provider=provider_name,
            directory_path=directory_path,
            ado_organization=ado_organization,
            project=project,
            node_type=self.NODE_TYPE,
        )
        get_work_items = getattr(provider, "get_pull_request_work_items", None)
        if not callable(get_work_items):
            raise ValueError(f"{self.NODE_TYPE} provider {provider_name!r} is not supported")
        return get_work_items(repo=repo, pr_id=pr_id).model_dump(mode="json")

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
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PullRequestWorkItemsQueryProvider has no runtime context")
        return self.runtime_context
