from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase


class SessionSetImageGenerationEnabledEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        target = self._resolve_target(payload)
        if not target:
            raise ValueError(
                "effect:session:set-image-generation-enabled requires target, session_id, or vm_name"
            )
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError(
                "effect:session:set-image-generation-enabled enabled must resolve to a boolean"
            )

        context = self._context()
        update_fn = getattr(
            context.session_image_generation_policy_updater,
            "session_set_image_generation_enabled",
            None,
        )
        if not callable(update_fn):
            raise RuntimeError(
                "Admin runtime session_set_image_generation_enabled is not available"
            )

        result = update_fn(target=target, enabled=enabled)
        output = self._coerce_result(result)
        output.setdefault("success", False)
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in ("target", "session_id", "vm_name", "enabled"):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    @staticmethod
    def _resolve_target(payload: Mapping[str, Any]) -> str | None:
        for field_name in ("target", "session_id", "vm_name"):
            value = payload.get(field_name)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError(
                "SessionSetImageGenerationEnabledEffectProvider has no runtime context"
            )
        return self.runtime_context

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
        return {"result": result}
