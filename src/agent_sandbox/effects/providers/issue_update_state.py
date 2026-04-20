from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase


class IssueUpdateStateEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        issue_id = self._require_non_empty_string(payload.get("issue_id"), field_name="issue_id")
        state = self._require_non_empty_string(payload.get("state"), field_name="state")
        sync_proxy_cache = self._coerce_bool(payload.get("sync_proxy_cache"), default=True)

        context = self._context()
        update_fn = getattr(context.issue_updater, "update_issue", None)
        if not callable(update_fn):
            raise RuntimeError("AdminService.update_issue is not available")

        result = update_fn(issue_id, state=state, sync_proxy_cache=sync_proxy_cache)
        output = self._coerce_result(result)
        output.setdefault("success", bool(output.get("updated")))
        output.setdefault("issue_number", issue_id)
        output.setdefault("state", state)
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in ("issue_id", "state", "sync_proxy_cache"):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("IssueUpdateStateEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _require_non_empty_string(value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"effect:issue:update-state requires {field_name}")
        return text

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("effect:issue:update-state sync_proxy_cache must resolve to a boolean")

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
