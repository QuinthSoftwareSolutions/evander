from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import QueryProviderBase


class SessionContextQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        target = self._resolve_target(payload)
        if not target:
            raise ValueError("query:session-context requires target, session_id, or vm_name")

        context = self._context()
        query_fn = getattr(context.session_context_reader, "session_context_query", None)
        if not callable(query_fn):
            raise RuntimeError("Admin runtime session_context_query is not available")
        result = query_fn(target=target)
        return self._coerce_result(result)

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in ("target", "session_id", "vm_name"):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _resolve_target(self, payload: Mapping[str, Any]) -> str | None:
        for field_name in ("target", "session_id", "vm_name"):
            value = self._normalize_optional_string(payload.get(field_name))
            if value:
                return value
        return None

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SessionContextQueryProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

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
