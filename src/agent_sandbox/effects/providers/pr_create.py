from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase


class PrCreateEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        host_repo_path = self._resolve_host_repo_path(resolved_config or {})
        if not host_repo_path:
            host_repo_path = self._resolve_host_repo_path(payload)
        if not host_repo_path:
            raise ValueError("effect:pr:create requires host_repo_path, repo_path, or target")

        context = self._context()
        pr_create = getattr(context.admin_service, "pr_create", None)
        if not callable(pr_create):
            raise RuntimeError("AdminService.pr_create is not available")

        result = pr_create(
            host_repo_path=host_repo_path,
            local_branch=self._normalize_optional_string(payload.get("local_branch")),
            origin_branch=self._normalize_optional_string(payload.get("origin_branch")),
            provider=self._normalize_optional_string(payload.get("provider")) or "auto",
            base_branch=self._normalize_optional_string(payload.get("base_branch")),
            session_id=self._normalize_optional_string(payload.get("session_id")),
            work_item_id=self._normalize_optional_string(payload.get("work_item_id")),
            title=self._normalize_optional_string(payload.get("title")),
            body=self._normalize_optional_string(payload.get("body")),
            body_file=self._normalize_optional_string(payload.get("body_file")),
            draft=self._as_optional_bool(payload.get("draft")),
            fill=self._resolve_fill(payload),
        )
        output = self._coerce_result(result)
        if "success" in output and "succeeded" not in output:
            output["succeeded"] = bool(output["success"])
        if "succeeded" in output and "success" not in output:
            output["success"] = bool(output["succeeded"])
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "host_repo_path",
            "repo_path",
            "target",
            "local_branch",
            "origin_branch",
            "provider",
            "base_branch",
            "session_id",
            "work_item_id",
            "title",
            "body",
            "body_file",
            "draft",
            "fill",
            "no_fill",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _resolve_host_repo_path(self, payload: Mapping[str, Any]) -> str | None:
        for field_name in ("host_repo_path", "repo_path", "target"):
            value = self._normalize_optional_string(payload.get(field_name))
            if value:
                return value
        return None

    def _resolve_fill(self, payload: Mapping[str, Any]) -> bool:
        if "fill" in payload:
            return self._as_bool(payload.get("fill"), default=True)
        if "no_fill" in payload:
            return not self._as_bool(payload.get("no_fill"), default=False)
        return True

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PrCreateEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _as_bool(cls, value: Any, *, default: bool = False) -> bool:
        parsed = cls._as_optional_bool(value)
        if parsed is None:
            return default
        return parsed

    @staticmethod
    def _as_optional_bool(value: Any) -> bool | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if not normalized:
            return None
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return bool(normalized)

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
