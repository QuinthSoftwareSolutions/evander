from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_sandbox.daemons.proxy_issue_sync import upsert_ado_proxy_issue_from_remote_payload
from agent_sandbox.issues.service import IssueService
from agent_sandbox.issue_owner import assigned_to_identity

from .base import EffectProviderBase


class AdoUpdateWorkItemEffectProvider(EffectProviderBase):
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
        state = self._normalize_optional_string(payload.get("state"))
        fields = self._coerce_fields(payload.get("fields"))
        expected_states = self._coerce_string_list(payload.get("expected_states"))
        sync_proxy_cache = self._coerce_bool(payload.get("sync_proxy_cache"), default=False)

        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(
                "effect:ado:update-work-item provider_config_resolver is not available"
            )
        issue_service = IssueService(context.provider_config_resolver)
        current = issue_service.read_remote_issue(
            provider="azure_devops",
            remote_issue_id=work_item_id,
            directory_path=directory_path,
            project=project,
            include_comments=False,
        )
        previous_state = self._normalize_optional_string(current.get("state"))
        if expected_states is not None and previous_state not in expected_states:
            return self._skipped_output(
                work_item_id=work_item_id,
                previous_state=previous_state,
                state=previous_state,
                fields=fields,
                reason="unexpected_state",
            )

        try:
            result = issue_service.update_remote_issue(
                provider="azure_devops",
                remote_issue_id=work_item_id,
                directory_path=directory_path,
                project=project,
                state=state,
                fields=fields,
                expected_state=previous_state if expected_states is not None else None,
            )
        except Exception:
            if expected_states is None:
                raise
            latest = issue_service.read_remote_issue(
                provider="azure_devops",
                remote_issue_id=work_item_id,
                directory_path=directory_path,
                project=project,
                include_comments=False,
            )
            latest_state = self._normalize_optional_string(latest.get("state"))
            if latest_state != previous_state or latest_state not in expected_states:
                return self._skipped_output(
                    work_item_id=work_item_id,
                    previous_state=previous_state,
                    state=latest_state,
                    fields=fields,
                    reason="state_conflict",
                )
            raise

        output = self._coerce_result(result)
        updated = bool(output.get("updated"))
        output.update(
            {
                "success": True,
                "updated": updated,
                "skipped": False,
                "skipped_reason": None,
                "work_item_id": str(output.get("remote_issue_id") or work_item_id),
                "previous_state": previous_state,
                "state": self._normalize_optional_string(output.get("state"))
                or state
                or previous_state,
                "fields": dict(output.get("fields") or fields),
            }
        )
        if sync_proxy_cache and updated:
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
            output["state"] = self._normalize_optional_string(remote_payload.get("state")) or output[
                "state"
            ]
            ticket = upsert_ado_proxy_issue_from_remote_payload(
                runtime_dir=self._runtime_dir(),
                directory_path=directory_path,
                project=project,
                remote_payload=remote_payload,
            )
            output["ticket"] = ticket.model_dump(mode="json")
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "directory_path",
            "project",
            "work_item_id",
            "state",
            "fields",
            "expected_states",
            "sync_proxy_cache",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AdoUpdateWorkItemEffectProvider has no runtime context")
        return self.runtime_context

    def _runtime_dir(self) -> Path:
        runtime_config = self._context().resolved_config
        return Path(getattr(runtime_config, "runtime_dir", Path("runtime")))

    @classmethod
    def _skipped_output(
        cls,
        *,
        work_item_id: str,
        previous_state: str | None,
        state: str | None,
        fields: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "updated": False,
            "skipped": True,
            "skipped_reason": reason,
            "work_item_id": work_item_id,
            "previous_state": previous_state,
            "state": state,
            "fields": fields,
        }

    @staticmethod
    def _require_non_empty_string(value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"effect:ado:update-work-item requires {field_name}")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_fields(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("effect:ado:update-work-item fields must resolve to an object")
        return {str(key).strip(): item for key, item in value.items() if str(key).strip()}

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str] | None:
        if value is None:
            return None
        raw_items = [value] if isinstance(value, str) else value
        if not isinstance(raw_items, (list, tuple, set)):
            raise ValueError("effect:ado:update-work-item expected_states must resolve to a list")
        normalized = [str(item).strip() for item in raw_items if str(item).strip()]
        return normalized or None

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
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
        raise ValueError(
            "effect:ado:update-work-item sync_proxy_cache must resolve to a boolean"
        )

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
