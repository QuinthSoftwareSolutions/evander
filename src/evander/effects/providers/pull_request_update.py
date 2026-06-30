from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Literal

from evander.pull_request_automation import build_pull_request_provider

from .base import EffectProviderBase


class PullRequestUpdateEffectProvider(EffectProviderBase):
    """Update pull request metadata or tags from workflow nodes."""

    NODE_TYPE = "effect:pull-request:update"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))
        return self._execute_update(payload)

    def _execute_update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        provider_name = self._require_string(payload.get("provider"), field_name="provider")
        repo = self._require_string(payload.get("repo"), field_name="repo")
        pr_id = self._require_string(payload.get("pr_id"), field_name="pr_id")
        directory_path = self._normalize_optional_string(payload.get("directory_path"))
        if directory_path is None:
            directory_path = self._context_directory_path()
        organization_url = self._normalize_optional_string(payload.get("organization_url"))
        project = self._normalize_optional_string(payload.get("project"))

        _, resolved_project, provider = build_pull_request_provider(
            context=self._context(),
            provider=provider_name,
            directory_path=directory_path,
            organization_url=organization_url,
            project=project,
            node_type=self.NODE_TYPE,
        )
        response = provider.update_pull_request(
            repo=repo,
            pr_id=pr_id,
            project=project or resolved_project,
            title=self._optional_raw_string(payload.get("title")),
            description=self._optional_raw_string(payload.get("description")),
            status=self._normalize_optional_string(payload.get("status")),
            add_tags=self._coerce_string_list(payload.get("add_tags")),
            remove_tags=self._coerce_string_list(payload.get("remove_tags")),
            expected_statuses=self._coerce_string_list(payload.get("expected_statuses")),
        )
        return response.model_dump(mode="json")

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "provider",
            "repo",
            "pr_id",
            "directory_path",
            "organization_url",
            "project",
            "title",
            "description",
            "status",
            "add_tags",
            "remove_tags",
            "expected_statuses",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PullRequestUpdateEffectProvider has no runtime context")
        return self.runtime_context

    def _context_directory_path(self) -> str:
        metadata = self._context().metadata
        return self._normalize_optional_string(metadata.get("directory_path")) or "root"

    @classmethod
    def _require_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if not text:
            raise ValueError(f"{cls.NODE_TYPE} requires {field_name}")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _optional_raw_string(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    def _coerce_string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        raw_items = [value] if isinstance(value, str) else value
        if not isinstance(raw_items, (list, tuple, set)):
            raise ValueError(f"{self.NODE_TYPE} lists must resolve to a string or list")
        return [str(item).strip() for item in raw_items if str(item).strip()]


class PullRequestTagEffectProvider(PullRequestUpdateEffectProvider):
    ACTION: ClassVar[Literal["add", "remove"]]
    NODE_TYPE: ClassVar[str]

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))
        tag = self._require_string(payload.get("tag"), field_name="tag")
        if self.ACTION == "add":
            payload["add_tags"] = [tag]
            payload["remove_tags"] = []
        else:
            payload["add_tags"] = []
            payload["remove_tags"] = [tag]
        response = self._execute_update(payload)
        response["tag"] = tag
        return response

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "provider",
            "repo",
            "pr_id",
            "tag",
            "directory_path",
            "organization_url",
            "project",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload


class PullRequestAddTagEffectProvider(PullRequestTagEffectProvider):
    ACTION = "add"
    NODE_TYPE = "effect:pull-request:add-tag"


class PullRequestRemoveTagEffectProvider(PullRequestTagEffectProvider):
    ACTION = "remove"
    NODE_TYPE = "effect:pull-request:remove-tag"
