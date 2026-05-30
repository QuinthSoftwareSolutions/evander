from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .base import QueryProviderBase


class ModelUsageListQueryProvider(QueryProviderBase):
    NODE_TYPE = "query:model-usage:list"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        admin_service = self._admin_service()
        events = admin_service.list_model_usage_events(
            session_id=self._normalize_optional_string(payload.get("session_id")),
            workflow_run_id=self._normalize_optional_string(payload.get("workflow_run_id")),
            effect_id=self._normalize_optional_string(payload.get("effect_id")),
            dispatch_id=self._normalize_optional_string(payload.get("dispatch_id")),
            directory_path=self._normalize_optional_string(payload.get("directory_path")),
            include_directory_descendants=self._coerce_optional_bool(
                payload.get("include_directory_descendants")
            )
            or False,
            provider=self._normalize_optional_string(payload.get("provider")),
            model=self._normalize_optional_string(payload.get("model")),
            agent_harness=self._normalize_optional_string(payload.get("agent_harness")),
            execution_backend=self._normalize_optional_string(payload.get("execution_backend")),
            normalized_usage_status=self._normalize_optional_string(
                payload.get("normalized_usage_status")
            ),
            request_status=self._normalize_optional_string(payload.get("request_status")),
            occurred_after=self._coerce_optional_datetime(payload.get("occurred_after")),
            occurred_before=self._coerce_optional_datetime(payload.get("occurred_before")),
            limit=self._coerce_optional_limit(payload.get("limit")),
        )

        if isinstance(events, Mapping):
            events = list(events.values())
        elif isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray)):
            events = list(events)
        else:
            raise RuntimeError(
                f"{self.NODE_TYPE} admin_service.list_model_usage_events must return a mapping or sequence"
            )

        items = [self._event_payload(event) for event in events]
        return {"items": items, "count": len(items)}

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        if query is None:
            return payload
        for field_name in (
            "session_id",
            "workflow_run_id",
            "effect_id",
            "dispatch_id",
            "directory_path",
            "include_directory_descendants",
            "provider",
            "model",
            "agent_harness",
            "execution_backend",
            "normalized_usage_status",
            "request_status",
            "occurred_after",
            "occurred_before",
            "limit",
        ):
            if hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _admin_service(self):
        context = self._context()
        admin_service = getattr(context, "admin_service", None)
        if admin_service is None:
            raise RuntimeError("query:model-usage:list requires admin_service")
        list_model_usage_events = getattr(admin_service, "list_model_usage_events", None)
        if not callable(list_model_usage_events):
            raise RuntimeError("query:model-usage:list requires admin_service.list_model_usage_events")
        return admin_service

    def _event_payload(self, event: Any) -> dict[str, Any]:
        model_dump = getattr(event, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if isinstance(event, Mapping):
            return dict(event)
        raise RuntimeError("query:model-usage:list returned event values must be mappings or models")

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("ModelUsageListQueryProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

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
        raise ValueError("query:model-usage:list include_directory_descendants must resolve to a boolean")

    @staticmethod
    def _coerce_optional_limit(value: Any) -> int | None:
        if value is None:
            return None
        try:
            limit = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("query:model-usage:list limit must resolve to an integer") from exc
        if limit < 1:
            raise ValueError("query:model-usage:list limit must be at least 1")
        return limit

    @staticmethod
    def _coerce_optional_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        text = str(value).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
