from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.issues.service import IssueService

from .base import EffectProviderBase


class AdoPostWorkItemCommentEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = self._require_non_empty_string(
            payload.get("directory_path"), field_name="directory_path"
        )
        project = self._require_non_empty_string(payload.get("project"), field_name="project")
        work_item_id = self._require_non_empty_string(
            payload.get("work_item_id"), field_name="work_item_id"
        )
        comment = self._require_non_empty_string(payload.get("comment"), field_name="comment")

        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(
                "effect:ado:post-work-item-comment provider_config_resolver is not available"
            )
        result = IssueService(context.provider_config_resolver).post_remote_comment(
            provider="azure_devops",
            remote_issue_id=work_item_id,
            directory_path=directory_path,
            project=project,
            comment=comment,
        )
        comment_payload = result.get("comment") if isinstance(result.get("comment"), Mapping) else {}
        return {
            "success": True,
            "provider": str(result.get("provider") or "azure_devops"),
            "work_item_id": str(result.get("remote_issue_id") or work_item_id),
            "comment_id": self._normalize_optional_string(comment_payload.get("id")),
            "comment_url": self._normalize_optional_string(comment_payload.get("url")),
            "comment": comment_payload,
            "message": f"Posted comment to Azure DevOps work item {work_item_id}",
        }

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in ("directory_path", "project", "work_item_id", "comment"):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AdoPostWorkItemCommentEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _require_non_empty_string(value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"effect:ado:post-work-item-comment requires {field_name}")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
