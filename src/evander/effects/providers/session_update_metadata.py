from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase


class SessionUpdateMetadataEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError(
                "effect:session:update-metadata requires metadata to resolve to an object"
            )

        target = self._resolve_target(payload)
        if not target:
            raise ValueError(
                "effect:session:update-metadata requires target, session_id, or vm_name"
            )

        context = self._context()
        update_fn = getattr(context.session_metadata_updater, "session_update_metadata", None)
        if not callable(update_fn):
            raise RuntimeError("Admin runtime session_update_metadata is not available")

        result = update_fn(target=target, metadata=dict(metadata))
        output = self._coerce_result(result)
        output.setdefault("success", False)
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in ("target", "session_id", "vm_name", "metadata"):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _resolve_target(self, payload: Mapping[str, Any]) -> str | None:
        for field_name in ("target", "session_id", "vm_name"):
            value = self._normalize_optional_string(payload.get(field_name))
            if value:
                return value
        return None

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SessionUpdateMetadataEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

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
