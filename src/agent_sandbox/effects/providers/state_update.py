from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_sandbox.events.models import EffectDefinitionDiscriminated, WorkflowNodeEnvelope
from agent_sandbox.events.provider_models import StateUpdateEffectDefinition
from agent_sandbox.workflow.state_store import (
    WorkflowStateMutationResult,
    WorkflowStateStore,
)

from .base import EffectProviderBase


class StateUpdateEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(
        cls, effect_definition: EffectDefinitionDiscriminated
    ) -> EffectDefinitionDiscriminated:
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return StateUpdateEffectDefinition.model_validate(
                {
                    "id": effect_definition.id,
                    "display_name": effect_definition.display_name,
                    "type": effect_definition.type,
                    "outputs": effect_definition.outputs,
                    **dict(effect_definition.config),
                }
            )
        return super().validate_definition(effect_definition)

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        namespace = self._require_text(payload.get("namespace"), field_name="namespace")
        key = self._require_text(payload.get("key"), field_name="key")
        operation = self._normalize_operation(payload.get("operation"))
        expected_version = self._coerce_expected_version(payload.get("expected_version"))
        directory_path, graph_id = self._runtime_scope()
        store = self._state_store()

        if operation == "set":
            result = store.set(
                directory_path,
                graph_id,
                namespace,
                key,
                value=payload.get("value"),
                expected_version=expected_version,
            )
        elif operation == "patch":
            patch = payload.get("patch")
            if not isinstance(patch, Mapping):
                raise ValueError("effect:state:update patch must resolve to a JSON object")
            result = store.patch(
                directory_path,
                graph_id,
                namespace,
                key,
                patch=patch,
                expected_version=expected_version,
            )
        elif operation == "delete":
            result = store.delete(
                directory_path,
                graph_id,
                namespace,
                key,
                expected_version=expected_version,
            )
        else:  # pragma: no cover - guarded by schema validation
            raise ValueError("effect:state:update operation must be set, patch, or delete")

        return self._build_output(
            result,
            operation=operation,
            namespace=namespace,
            key=key,
            directory_path=directory_path,
            graph_id=graph_id,
        )

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        if effect is None:
            return payload
        for field_name in ("namespace", "key", "operation", "value", "patch", "expected_version"):
            if hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _state_store(self) -> WorkflowStateStore:
        context = self._context()
        runtime_dir = Path(context.config_path("runtime_dir"))
        return WorkflowStateStore(runtime_dir)

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("StateUpdateEffectProvider has no runtime context")
        self._runtime_scope()
        return self.runtime_context

    def _runtime_scope(self) -> tuple[str, str]:
        if self.runtime_context is None:
            raise RuntimeError("StateUpdateEffectProvider has no runtime context")
        metadata = dict(self.runtime_context.metadata or {})
        return (
            self._require_text(metadata.get("directory_path"), field_name="directory_path"),
            self._require_text(metadata.get("graph_id"), field_name="graph_id"),
        )

    def _build_output(
        self,
        result: WorkflowStateMutationResult,
        *,
        operation: str,
        namespace: str,
        key: str,
        directory_path: str,
        graph_id: str,
    ) -> dict[str, Any]:
        record = result.record
        if record is None:
            value = None
            version = result.previous_version
            created_at = None
            updated_at = None
        else:
            serialized = record.model_dump(mode="json")
            value = serialized.get("value")
            version = serialized.get("version")
            created_at = serialized.get("created_at")
            updated_at = serialized.get("updated_at")

        return {
            "success": not result.conflict,
            "found": result.found,
            "directory_path": directory_path,
            "graph_id": graph_id,
            "namespace": namespace,
            "key": key,
            "value": value,
            "version": version,
            "created_at": created_at,
            "updated_at": updated_at,
            "operation": operation,
            "updated": result.updated,
            "deleted": result.deleted,
            "previous_version": result.previous_version,
            "conflict": result.conflict,
            "conflict_reason": result.conflict_reason,
        }

    @staticmethod
    def _normalize_operation(value: Any) -> str:
        text = "" if value is None else str(value).strip().lower()
        if text not in {"set", "patch", "delete"}:
            raise ValueError("effect:state:update operation must be set, patch, or delete")
        return text

    @staticmethod
    def _coerce_expected_version(value: Any) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            version = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("effect:state:update expected_version must be an integer") from exc
        if version < 0:
            raise ValueError("effect:state:update expected_version must be greater than or equal to zero")
        return version

    @staticmethod
    def _require_text(value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise RuntimeError(
                "effect:state:update requires graph_id and directory_path runtime metadata"
            )
        return text
