from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.session_artifacts import normalize_artifact_relative_path

from .base import QueryProviderBase


class AgentArtifactTextQueryProvider(QueryProviderBase):
    """Read a UTF-8 text artifact from an agent session artifacts folder."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        session_id = self._required_string(
            payload.get("session_id"),
            field_name="session_id",
        )
        relative_path = normalize_artifact_relative_path(payload.get("path"))
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"

        context = self._context()
        reader = context.session_artifact_reader
        read_fn = getattr(reader, "read_session_artifact_text", None)
        if not callable(read_fn):
            raise RuntimeError("query:agent:artifact-text session artifact reader is not available")
        result = read_fn(session_id=session_id, path=relative_path, vm_user=vm_user)
        if isinstance(result, Mapping):
            output = dict(result)
        else:
            output = {"content": "" if result is None else str(result)}
        output.setdefault("session_id", session_id)
        output.setdefault("path", relative_path)
        output.setdefault("content", "")
        output.setdefault("byte_count", len(str(output.get("content") or "").encode("utf-8")))
        return output

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in ("session_id", "path", "vm_user"):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentArtifactTextQueryProvider has no runtime context")
        return self.runtime_context

    @classmethod
    def _required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(f"query:agent:artifact-text {field_name} is required")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
