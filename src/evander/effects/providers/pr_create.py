from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase, scoped_effect_directory_path


class PrCreateEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        managed_repo_id = self._normalize_required_string(
            payload.get("managed_repo_id"), "managed_repo_id"
        )
        source_ref = self._normalize_required_string(payload.get("source_ref"), "source_ref")
        expected_commit_sha = self._normalize_required_string(
            payload.get("expected_commit_sha"), "expected_commit_sha"
        )
        destination_branch = self._normalize_optional_string(payload.get("destination_branch"))
        if destination_branch is None:
            destination_branch = self._normalize_required_string(
                payload.get("fallback_destination_branch"), "destination_branch"
            )

        context = self._context()
        directory_path = scoped_effect_directory_path(
            context, self._normalize_optional_string(payload.get("directory_path"))
        )
        pr_create = getattr(context.pull_request_creator, "pr_create", None)
        if not callable(pr_create):
            raise RuntimeError("Admin runtime pr_create is not available")

        title = self._normalize_optional_string(payload.get("title"))
        if title is None:
            title = self._normalize_optional_string(payload.get("fallback_title"))

        result = pr_create(
            managed_repo_id=managed_repo_id,
            source_ref=source_ref,
            expected_commit_sha=expected_commit_sha,
            destination_branch=destination_branch,
            directory_path=directory_path,
            base_branch=self._normalize_optional_string(payload.get("base_branch")),
            session_id=self._normalize_optional_string(payload.get("session_id")),
            work_item_id=self._normalize_optional_string(payload.get("work_item_id")),
            **({"publication_context_id": self._normalize_optional_string(payload.get("publication_context_id"))}
               if payload.get("publication_context_id") else {}),
            **({"publication_policy": payload["publication_policy"]}
               if "publication_policy" in payload else {}),
            title=title,
            body=self._normalize_optional_string(payload.get("body")),
            draft=self._as_optional_bool(payload.get("draft")),
            fill=self._resolve_fill(payload),
        )
        output = self._coerce_result(result)
        output.setdefault("success", False)
        output.setdefault("managed_repo_id", managed_repo_id)
        output.setdefault("directory_path", directory_path)
        output.setdefault("source_ref", source_ref)
        output.setdefault("expected_commit_sha", expected_commit_sha)
        output.setdefault("destination_branch", destination_branch)
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "managed_repo_id",
            "directory_path",
            "source_ref",
            "expected_commit_sha",
            "destination_branch",
            "fallback_destination_branch",
            "base_branch",
            "session_id",
            "work_item_id",
            "publication_context_id",
            "publication_policy",
            "title",
            "fallback_title",
            "body",
            "draft",
            "fill",
            "no_fill",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

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
    def _normalize_required_string(cls, value: Any, field_name: str) -> str:
        normalized = cls._normalize_optional_string(value)
        if normalized is None:
            raise ValueError(f"effect:pr:create requires {field_name}")
        return normalized

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
