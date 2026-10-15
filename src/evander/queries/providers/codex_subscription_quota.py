from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import QueryProviderBase, scoped_query_directory_path
from .schemas import CodexSubscriptionQuotaQueryDefinition


class CodexSubscriptionQuotaQueryProvider(QueryProviderBase):
    """Read a named subscription's quota through the host-owned auth service."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.query_definition is None or self.runtime_context is None:
            raise RuntimeError("CodexSubscriptionQuotaQueryProvider has no runtime context")
        payload = {
            field: getattr(self.query_definition, field)
            for field in ("profile_id", "directory_path", "limit_id", "window_duration_minutes")
        }
        if resolved_config:
            payload.update(resolved_config)
        definition = CodexSubscriptionQuotaQueryDefinition.model_validate(
            {
                "id": self.query_definition.id,
                "display_name": self.query_definition.display_name,
                "type": "query:codex:subscription-quota",
                **payload,
            }
        )
        if any(isinstance(value, str) and "${{" in value for value in payload.values()):
            raise ValueError("query:codex:subscription-quota has unresolved bindings")
        directory_path = scoped_query_directory_path(
            self.runtime_context, definition.directory_path or None
        )
        reader = getattr(self.runtime_context.admin_service, "read_codex_subscription_quota", None)
        if not callable(reader):
            raise RuntimeError(
                "query:codex:subscription-quota requires a subscription quota reader"
            )
        return reader(
            directory_path=directory_path,
            profile_id=definition.profile_id,
            limit_id=definition.limit_id,
            window_duration_minutes=definition.window_duration_minutes,
        )
