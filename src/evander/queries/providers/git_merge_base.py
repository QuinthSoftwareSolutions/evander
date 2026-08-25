from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import QueryProviderBase, scoped_query_directory_path


class GitMergeBaseQueryProvider(QueryProviderBase):
    """Resolve an immutable target tip and merge base for one candidate commit."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))
        managed_repo_id = self._normalize_required_string(
            payload.get("managed_repo_id"), field_name="managed_repo_id"
        )
        target_branch = self._normalize_required_string(
            payload.get("target_branch"), field_name="target_branch"
        )
        candidate_commit_sha = self._normalize_required_string(
            payload.get("candidate_commit_sha"), field_name="candidate_commit_sha"
        )

        context = self._context()
        directory_path = scoped_query_directory_path(
            context,
            self._normalize_optional_string(payload.get("directory_path")),
        )
        query_fn = getattr(
            context.git_merge_base_reader,
            "resolve_managed_git_merge_base",
            None,
        )
        if not callable(query_fn):
            raise RuntimeError("Admin runtime resolve_managed_git_merge_base is not available")

        result = query_fn(
            managed_repo_id=managed_repo_id,
            target_branch=target_branch,
            candidate_commit_sha=candidate_commit_sha,
            directory_path=directory_path,
        )
        return self._coerce_result(result)

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "managed_repo_id",
            "target_branch",
            "candidate_commit_sha",
            "directory_path",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("GitMergeBaseQueryProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(f"query:git:merge-base requires {field_name}")
        return text

    @staticmethod
    def _coerce_result(result: Any) -> dict[str, Any]:
        if isinstance(result, Mapping):
            return dict(result)
        model_dump = getattr(result, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                return dict(dumped)
        raise RuntimeError("Admin runtime resolve_managed_git_merge_base returned no result")
