from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase, scoped_effect_directory_path


class GitEnsureManagedRepoEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = scoped_effect_directory_path(
            self._context(),
            self._normalize_required_string(
                payload.get("directory_path"), field_name="directory_path"
            ),
        )
        repo_source = self._normalize_required_string(
            payload.get("repo_source"), field_name="repo_source"
        )

        admin_service = self._context().admin_service
        ensure = getattr(admin_service, "ensure_managed_git_repository", None)
        if not callable(ensure):
            raise RuntimeError("Admin runtime ensure_managed_git_repository is not available")

        result = ensure(directory_path=directory_path, repo_source=repo_source)
        service_output = self._coerce_result(result)
        managed_repo_id = self._normalize_optional_string(service_output.get("managed_repo_id"))
        output: dict[str, Any] = {
            "success": bool(service_output.get("success", managed_repo_id is not None)),
            "managed_repo_id": managed_repo_id,
        }
        for key in ("reason", "message", "error"):
            value = service_output.get(key)
            if value is not None:
                output[key] = value
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in ("directory_path", "repo_source"):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("GitEnsureManagedRepoEffectProvider has no runtime context")
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
            raise ValueError(f"effect:git:ensure-managed-repo requires {field_name}")
        return text

    @staticmethod
    def _coerce_result(result: Any) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, str):
            return {"managed_repo_id": result}
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
