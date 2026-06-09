from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase


class GitPushBranchEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        host_repo_path = self._normalize_optional_string(payload.get("host_repo_path"))
        if not host_repo_path:
            raise ValueError("effect:git:push-branch requires host_repo_path")
        local_branch = self._normalize_optional_string(payload.get("local_branch"))
        if not local_branch:
            raise ValueError("effect:git:push-branch requires local_branch")
        origin_branch = self._normalize_optional_string(payload.get("origin_branch"))
        if not origin_branch:
            raise ValueError("effect:git:push-branch requires origin_branch")

        context = self._context()
        push_branch = getattr(context.git_branch_pusher, "push_branch", None)
        if not callable(push_branch):
            raise RuntimeError("AdminService.push_branch is not available")

        result = push_branch(
            host_repo_path=host_repo_path,
            local_branch=local_branch,
            origin_branch=origin_branch,
            canonical_origin_url=self._normalize_optional_string(
                payload.get("canonical_origin_url")
            ),
            provider=self._normalize_optional_string(payload.get("provider")),
            base_branch=self._normalize_optional_string(payload.get("base_branch")),
            session_id=self._normalize_optional_string(payload.get("session_id")),
            work_item_id=self._normalize_optional_string(payload.get("work_item_id")),
        )
        output = self._coerce_result(result)
        output.setdefault("success", False)
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "host_repo_path",
            "local_branch",
            "origin_branch",
            "canonical_origin_url",
            "provider",
            "base_branch",
            "session_id",
            "work_item_id",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("GitPushBranchEffectProvider has no runtime context")
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
