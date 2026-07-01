from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

from evander.daemons.proxy_issue_sync import upsert_ado_proxy_issue_from_remote_payload
from evander.issues.service import IssueService
from evander.issue_owner import assigned_to_identity

from .base import EffectProviderBase


class AdoWorkItemTagEffectProvider(EffectProviderBase):
    ACTION: ClassVar[Literal["add", "remove"]]
    NODE_TYPE: ClassVar[str]

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
        tag = self._require_non_empty_string(payload.get("tag"), field_name="tag")
        sync_proxy_cache = self._coerce_bool(payload.get("sync_proxy_cache"), default=False)

        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(f"{self.NODE_TYPE} provider_config_resolver is not available")
        issue_service = IssueService(context.provider_config_resolver)
        current = issue_service.read_remote_issue(
            provider="azure_devops",
            remote_issue_id=work_item_id,
            directory_path=directory_path,
            project=project,
            include_comments=False,
        )
        previous_tags = self._extract_ado_tags(current)
        merged_tags, added_tags, removed_tags = self._merge_tags(previous_tags, tag)
        changed = bool(added_tags or removed_tags)

        result: dict[str, Any]
        if changed:
            result = issue_service.update_remote_issue(
                provider="azure_devops",
                remote_issue_id=work_item_id,
                directory_path=directory_path,
                project=project,
                state=None,
                fields={"System.Tags": "; ".join(merged_tags)},
            )
        else:
            result = {
                "provider": "azure_devops",
                "remote_issue_id": work_item_id,
                "updated": False,
            }

        output = {
            **self._coerce_result(result),
            "success": True,
            "updated": bool(result.get("updated")) if changed else False,
            "skipped": False,
            "skipped_reason": None,
            "provider": str(result.get("provider") or "azure_devops"),
            "work_item_id": str(result.get("remote_issue_id") or work_item_id),
            "tag": tag,
            "previous_tags": previous_tags,
            "tags": merged_tags,
            "added_tags": added_tags,
            "removed_tags": removed_tags,
        }
        if sync_proxy_cache and changed and output["updated"]:
            self._sync_proxy_cache(
                issue_service=issue_service,
                output=output,
                work_item_id=work_item_id,
                directory_path=directory_path,
                project=project,
            )
        return output

    def _merge_tags(
        self, previous_tags: list[str], tag: str
    ) -> tuple[list[str], list[str], list[str]]:
        tag_key = tag.casefold()
        existing_tag = next((item for item in previous_tags if item.casefold() == tag_key), None)
        if self.ACTION == "add":
            if existing_tag is not None:
                return previous_tags, [], []
            return [*previous_tags, tag], [tag], []
        if existing_tag is None:
            return previous_tags, [], []
        return [item for item in previous_tags if item.casefold() != tag_key], [], [existing_tag]

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "directory_path",
            "project",
            "work_item_id",
            "tag",
            "sync_proxy_cache",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError(f"{self.NODE_TYPE} has no runtime context")
        return self.runtime_context

    def _runtime_dir(self) -> Path:
        runtime_config = self._context().resolved_config
        return Path(getattr(runtime_config, "runtime_dir", Path("runtime")))

    def _sync_proxy_cache(
        self,
        *,
        issue_service: IssueService,
        output: dict[str, Any],
        work_item_id: str,
        directory_path: str,
        project: str,
    ) -> None:
        remote_payload = issue_service.read_remote_issue(
            provider="azure_devops",
            remote_issue_id=work_item_id,
            directory_path=directory_path,
            project=project,
            include_comments=True,
        )
        remote_fields = (
            remote_payload.get("fields")
            if isinstance(remote_payload.get("fields"), Mapping)
            else {}
        )
        output["remote_assigned_to_identity"] = assigned_to_identity(
            remote_fields.get("System.AssignedTo")
        )
        output["remote_work_item_rev"] = remote_payload.get("rev")
        ticket = upsert_ado_proxy_issue_from_remote_payload(
            runtime_dir=self._runtime_dir(),
            directory_path=directory_path,
            project=project,
            remote_payload=remote_payload,
        )
        output["ticket"] = ticket.model_dump(mode="json")

    @staticmethod
    def _extract_ado_tags(remote_payload: dict[str, Any]) -> list[str]:
        fields = (
            remote_payload.get("fields")
            if isinstance(remote_payload.get("fields"), Mapping)
            else {}
        )
        raw_tags = remote_payload.get("tags")
        if raw_tags is None:
            raw_tags = fields.get("System.Tags")
        return AdoWorkItemTagEffectProvider._normalize_ado_tag_list(raw_tags)

    @staticmethod
    def _normalize_ado_tag_list(value: Any) -> list[str]:
        raw_values = value if isinstance(value, list) else str(value or "").split(";")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tag in raw_values:
            tag = str(raw_tag).strip()
            if not tag:
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(tag)
        return normalized

    def _require_non_empty_string(self, value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"{self.NODE_TYPE} requires {field_name}")
        return text

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError(f"{self.NODE_TYPE} sync_proxy_cache must resolve to a boolean")

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


class AdoAddWorkItemTagEffectProvider(AdoWorkItemTagEffectProvider):
    ACTION = "add"
    NODE_TYPE = "effect:ado:add-work-item-tag"


class AdoRemoveWorkItemTagEffectProvider(AdoWorkItemTagEffectProvider):
    ACTION = "remove"
    NODE_TYPE = "effect:ado:remove-work-item-tag"
