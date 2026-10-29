from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase
from .git_results import coerce_git_result


class GitPromoteAgentBranchEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        managed_repo_id = self._normalize_required_string(
            payload.get("managed_repo_id"), field_name="managed_repo_id"
        )
        agent_branch = self._normalize_required_string(
            payload.get("agent_branch"), field_name="agent_branch"
        )
        target_branch = self._normalize_required_string(
            payload.get("target_branch"), field_name="target_branch"
        )
        expected_commit_sha = self._normalize_required_string(
            payload.get("expected_commit_sha"), field_name="expected_commit_sha"
        )

        context = self._context()
        promote = getattr(context.git_branch_promoter, "promote_agent_branch", None)
        if not callable(promote):
            raise RuntimeError("Admin runtime promote_agent_branch is not available")

        result = promote(
            managed_repo_id=managed_repo_id,
            agent_branch=agent_branch,
            target_branch=target_branch,
            expected_commit_sha=expected_commit_sha,
            directory_path=str(context.metadata.get("directory_path") or "root"),
        )
        output = self._coerce_result(result)
        output.setdefault("success", False)
        output.setdefault("managed_repo_id", managed_repo_id)
        output.setdefault("agent_branch", agent_branch)
        output.setdefault("target_branch", target_branch)
        output.setdefault("expected_commit_sha", expected_commit_sha)

        promoted_commit = self._normalize_optional_string(
            output.get("commit_sha") or output.get("promoted_commit_sha")
        )
        if (
            output.get("success") is True
            and promoted_commit is not None
            and promoted_commit != expected_commit_sha
        ):
            output["success"] = False
            output["error"] = (
                f"promoted commit {promoted_commit} did not match expected {expected_commit_sha}"
            )
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "managed_repo_id",
            "agent_branch",
            "target_branch",
            "expected_commit_sha",
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

    @classmethod
    def _normalize_required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(f"effect:git:promote-agent-branch requires {field_name}")
        return text

    _coerce_result = staticmethod(coerce_git_result)
