from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase


class GitPromoteAgentBranchEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        host_repo_path = self._normalize_optional_string(payload.get("host_repo_path"))
        if not host_repo_path:
            raise ValueError("effect:git:promote-agent-branch requires host_repo_path")
        bare_repo_path = self._normalize_optional_string(payload.get("bare_repo_path"))
        bare_repo_name = self._normalize_optional_string(payload.get("bare_repo_name"))
        if not bare_repo_path and not bare_repo_name:
            raise ValueError(
                "effect:git:promote-agent-branch requires bare_repo_path or bare_repo_name"
            )
        if bare_repo_path and bare_repo_name:
            raise ValueError(
                "effect:git:promote-agent-branch bare_repo_path and bare_repo_name are mutually exclusive"
            )

        agent_branch = self._normalize_optional_string(payload.get("agent_branch"))
        if not agent_branch:
            raise ValueError("effect:git:promote-agent-branch requires agent_branch")

        context = self._context()
        promote = getattr(context.git_branch_promoter, "promote_agent_branch", None)
        if not callable(promote):
            raise RuntimeError("AdminService.promote_agent_branch is not available")

        result = promote(
            host_repo_path=host_repo_path,
            bare_repo_path=bare_repo_path,
            bare_repo_name=bare_repo_name,
            agent_branch=agent_branch,
            review_branch=self._normalize_optional_string(payload.get("review_branch")),
            remote_name=self._normalize_optional_string(payload.get("remote_name")),
        )
        output = self._coerce_result(result)
        output.setdefault("success", False)
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "host_repo_path",
            "bare_repo_path",
            "bare_repo_name",
            "agent_branch",
            "review_branch",
            "remote_name",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("GitPromoteAgentBranchEffectProvider has no runtime context")
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
