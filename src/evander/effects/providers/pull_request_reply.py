from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.pull_request_automation import build_pull_request_reply_service_for_context

from .base import EffectProviderBase, scoped_effect_directory_path


class PullRequestReplyEffectProvider(EffectProviderBase):
    """Post one host-owned reply to a pull-request feedback comment."""

    NODE_TYPE = "effect:pull-request:reply"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        context = self._context()
        directory_path = scoped_effect_directory_path(
            context,
            self._normalize_optional_string(payload.get("directory_path"))
            or self._context_directory_path(),
        )
        response = build_pull_request_reply_service_for_context(
            context=context,
            directory_path=directory_path,
            node_type=self.NODE_TYPE,
        ).post_reply(
            provider=self._require_string(payload.get("provider"), field_name="provider"),
            repo=self._require_string(payload.get("repo"), field_name="repo"),
            pr_id=self._require_string(payload.get("pr_id"), field_name="pr_id"),
            thread_id=self._normalize_optional_string(payload.get("thread_id")),
            comment_id=self._require_string(payload.get("comment_id"), field_name="comment_id"),
            body_text=self._require_string(payload.get("body_text"), field_name="body_text"),
            idempotency_key=self._require_string(
                payload.get("idempotency_key"), field_name="idempotency_key"
            ),
        )
        return {"success": True, **response.model_dump(mode="json")}

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "provider",
            "repo",
            "pr_id",
            "thread_id",
            "comment_id",
            "body_text",
            "idempotency_key",
            "directory_path",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PullRequestReplyEffectProvider has no runtime context")
        return self.runtime_context

    def _context_directory_path(self) -> str:
        return self._normalize_optional_string(
            self._context().metadata.get("directory_path")
        ) or "root"

    @classmethod
    def _require_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(f"{cls.NODE_TYPE} requires {field_name}")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
