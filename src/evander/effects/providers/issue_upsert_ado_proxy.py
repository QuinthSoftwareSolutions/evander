from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.ado_proxy_issues import upsert_ado_proxy_issue_items

from .base import EffectProviderBase


class IssueUpsertAdoProxyEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = (
            self._normalize_optional_string(payload.get("directory_path"))
            or self._context_directory_path()
        )
        item = payload.get("item")
        items = payload.get("items")
        normalized_items = self._coerce_items(item=item, items=items)
        if not normalized_items:
            raise ValueError("effect:issue:upsert-ado-proxy requires item or items")

        resolved_directory = self._resolve_directory_config(directory_path)
        organization_url = self._normalize_optional_string(payload.get("organization_url"))
        if organization_url is None:
            organization_url = self._normalize_optional_string(
                getattr(resolved_directory, "azure_devops_organization_url", None)
            )
        project = self._normalize_optional_string(payload.get("project"))
        if project is None:
            project = self._normalize_optional_string(
                getattr(resolved_directory, "azure_devops_project", None)
            )
        repo_source = self._normalize_optional_string(payload.get("repo_source"))
        yaml_output_dir = self._normalize_path(payload.get("yaml_output_dir"))
        runtime_dir = self._runtime_dir()
        if yaml_output_dir is not None and not yaml_output_dir.is_absolute():
            yaml_output_dir = runtime_dir / yaml_output_dir

        result = upsert_ado_proxy_issue_items(
            runtime_dir=runtime_dir,
            directory_path=directory_path,
            organization_url=organization_url,
            project=project,
            repo_source=repo_source,
            yaml_output_dir=yaml_output_dir,
            source="ado_proxy_upsert",
            items=normalized_items,
        )
        return result.model_dump()

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "directory_path",
            "organization_url",
            "project",
            "repo_source",
            "yaml_output_dir",
            "item",
            "items",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("IssueUpsertAdoProxyEffectProvider has no runtime context")
        return self.runtime_context

    def _runtime_dir(self) -> Path:
        return Path(getattr(self._context().resolved_config, "runtime_dir", Path("runtime")))

    def _context_directory_path(self) -> str:
        return (
            self._normalize_optional_string(self._context().metadata.get("directory_path"))
            or "root"
        )

    def _resolve_directory_config(self, directory_path: str):
        resolver = self._context().provider_config_resolver
        if resolver is None:
            return self._context().resolved_config
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(directory_path)
        return self._context().resolved_config

    @classmethod
    def _coerce_items(cls, *, item: Any, items: Any) -> list[dict[str, Any]]:
        if item is not None and items is not None:
            raise ValueError(
                "effect:issue:upsert-ado-proxy item and items are mutually exclusive"
            )
        if item is not None:
            if isinstance(item, Mapping) and isinstance(item.get("items"), list):
                return cls._coerce_item_list(item.get("items"))
            if not isinstance(item, Mapping):
                raise ValueError("effect:issue:upsert-ado-proxy item must resolve to an object")
            return [dict(item)]
        if items is None:
            return []
        if isinstance(items, Mapping) and isinstance(items.get("items"), list):
            items = items.get("items")
        return cls._coerce_item_list(items)

    @staticmethod
    def _coerce_item_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ValueError("effect:issue:upsert-ado-proxy items must resolve to an array")
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ValueError(
                    "effect:issue:upsert-ado-proxy items must contain objects"
                )
            normalized.append(dict(item))
        return normalized

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_path(cls, value: Any) -> Path | None:
        text = cls._normalize_optional_string(value)
        if text is None:
            return None
        return Path(text).expanduser()
