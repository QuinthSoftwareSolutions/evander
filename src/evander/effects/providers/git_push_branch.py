from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase, scoped_effect_directory_path
from .git_results import coerce_git_result


class GitPushBranchEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        managed_repo_id = self._normalize_required_string(
            payload.get("managed_repo_id"), field_name="managed_repo_id"
        )
        source_ref = self._normalize_required_string(
            payload.get("source_ref"), field_name="source_ref"
        )
        expected_commit_sha = self._normalize_required_string(
            payload.get("expected_commit_sha"), field_name="expected_commit_sha"
        )
        expected_destination_commit_sha = self._normalize_optional_string(
            payload.get("expected_destination_commit_sha")
        )
        destination_branch = self._normalize_required_string(
            payload.get("destination_branch"), field_name="destination_branch"
        )
        context = self._context()
        directory_path = scoped_effect_directory_path(
            context,
            self._normalize_optional_string(payload.get("directory_path")),
        )
        push_branch = getattr(context.git_branch_pusher, "push_branch", None)
        if not callable(push_branch):
            raise RuntimeError("Admin runtime push_branch is not available")

        push_kwargs = {
            "managed_repo_id": managed_repo_id,
            "source_ref": source_ref,
            "expected_commit_sha": expected_commit_sha,
            "destination_branch": destination_branch,
            "directory_path": directory_path,
        }
        if expected_destination_commit_sha is not None:
            push_kwargs["expected_destination_commit_sha"] = expected_destination_commit_sha
        result = push_branch(
            **push_kwargs,
        )
        output = self._coerce_result(result)
        output.setdefault("success", False)
        output.setdefault("managed_repo_id", managed_repo_id)
        output.setdefault("source_ref", source_ref)
        output.setdefault("expected_commit_sha", expected_commit_sha)
        if expected_destination_commit_sha is not None:
            output.setdefault("expected_destination_commit_sha", expected_destination_commit_sha)
        output.setdefault("destination_branch", destination_branch)
        if output["success"] is False and not self._normalize_optional_string(output.get("error")):
            message = self._normalize_optional_string(output.get("message"))
            if message is not None:
                output["error"] = message
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "managed_repo_id",
            "directory_path",
            "source_ref",
            "expected_commit_sha",
            "expected_destination_commit_sha",
            "destination_branch",
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

    @classmethod
    def _normalize_required_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(f"effect:git:push-branch requires {field_name}")
        return text

    _coerce_result = staticmethod(coerce_git_result)
