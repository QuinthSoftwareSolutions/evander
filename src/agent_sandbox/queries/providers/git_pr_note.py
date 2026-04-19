from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import QueryProviderBase


class GitPrNoteQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))
        host_repo_path = self._normalize_required_string(payload.get("host_repo_path"), field_name="host_repo_path")
        local_branch = self._normalize_required_string(payload.get("local_branch"), field_name="local_branch")
        remote_name = self._normalize_optional_string(payload.get("remote_name"))
        notes_ref = self._normalize_optional_string(payload.get("notes_ref"))

        context = self._context()
        query_fn = getattr(context.git_pr_note_reader, "git_pr_note_query", None)
        if not callable(query_fn):
            raise RuntimeError("AdminService.git_pr_note_query is not available")

        result = query_fn(
            host_repo_path=host_repo_path,
            local_branch=local_branch,
            remote_name=remote_name,
            notes_ref=notes_ref,
        )
        return self._coerce_result(result)

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in ("host_repo_path", "local_branch", "remote_name", "notes_ref"):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("GitPrNoteQueryProvider has no runtime context")
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
        if not text:
            raise ValueError(f"query:git:pr-note requires {field_name}")
        return text

    @staticmethod
    def _coerce_result(result: Any) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, Mapping):
            return dict(result)
        model_dump = getattr(result, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                return dict(dumped)
        if hasattr(result, "__dict__"):
            return dict(vars(result))
        return {"result": result}
