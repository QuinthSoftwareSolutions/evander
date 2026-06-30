from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.pull_request_automation import build_pull_request_provider

from .base import QueryProviderBase


class PullRequestListQueryProvider(QueryProviderBase):
    """List provider pull requests from workflow nodes."""

    NODE_TYPE = "query:pull-request:list"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        provider_name = self._require_string(payload.get("provider"), field_name="provider")
        repo = self._require_string(payload.get("repo"), field_name="repo")
        directory_path = self._normalize_optional_string(payload.get("directory_path"))
        if directory_path is None:
            directory_path = self._context_directory_path()
        organization_url = self._normalize_optional_string(payload.get("organization_url"))
        project = self._normalize_optional_string(payload.get("project"))

        _, resolved_project, provider = build_pull_request_provider(
            context=self._context(),
            provider=provider_name,
            directory_path=directory_path,
            organization_url=organization_url,
            project=project,
            node_type=self.NODE_TYPE,
        )
        response = provider.list_pull_requests(
            repo=repo,
            project=project or resolved_project,
            status=self._normalize_optional_string(payload.get("status")),
            creator_id=self._normalize_optional_string(payload.get("creator_id")),
            reviewer_id=self._normalize_optional_string(payload.get("reviewer_id")),
            source_ref_name=self._normalize_optional_string(payload.get("source_ref_name")),
            target_ref_name=self._normalize_optional_string(payload.get("target_ref_name")),
            source_repository_id=self._normalize_optional_string(
                payload.get("source_repository_id")
            ),
            repository_id=self._normalize_optional_string(payload.get("repository_id")),
            min_time=self._normalize_optional_string(payload.get("min_time")),
            max_time=self._normalize_optional_string(payload.get("max_time")),
            query_time_range_type=self._normalize_optional_string(
                payload.get("query_time_range_type")
            ),
            include_links=self._coerce_optional_bool(payload.get("include_links")),
            max_comment_length=self._coerce_optional_int(
                payload.get("max_comment_length"), field_name="max_comment_length"
            ),
            tags=self._coerce_optional_tag_expression(payload.get("tags")),
            top=self._coerce_optional_int(payload.get("top"), field_name="top"),
            skip=self._coerce_optional_int(payload.get("skip"), field_name="skip"),
            cursor=self._normalize_optional_string(payload.get("cursor")),
        )
        return response.model_dump(mode="json")

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "provider",
            "repo",
            "directory_path",
            "organization_url",
            "project",
            "status",
            "creator_id",
            "reviewer_id",
            "source_ref_name",
            "target_ref_name",
            "source_repository_id",
            "repository_id",
            "min_time",
            "max_time",
            "query_time_range_type",
            "include_links",
            "max_comment_length",
            "tags",
            "top",
            "skip",
            "cursor",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PullRequestListQueryProvider has no runtime context")
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
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_optional_tag_expression(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("query:pull-request:list tags must resolve to a string")
        return value.strip() or None

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool | None:
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
        raise ValueError("query:pull-request:list include_links must resolve to a boolean")

    @staticmethod
    def _coerce_optional_int(value: Any, *, field_name: str) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"query:pull-request:list {field_name} must resolve to an integer"
            ) from exc
