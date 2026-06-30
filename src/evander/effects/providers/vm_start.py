from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase


class VmStartEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(resolved_config or {})
        target = self._resolve_target(payload)

        context = self._context()
        if context.vm_power_controller is None:
            raise RuntimeError("effect:vm:start vm_power_controller is not available")
        result = context.vm_power_controller.start_vm(target)
        return {
            "success": bool(result.success),
            "action": "start",
            "target": target,
            "vm_name": result.vm_name or target,
            "message": result.message or "",
        }

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmStartEffectProvider has no runtime context")
        return self.runtime_context

    def _resolve_target(self, payload: Mapping[str, Any]) -> str:
        for field_name in ("target", "vm_name", "session_id"):
            value = self._normalize_optional_string(payload.get(field_name))
            if value:
                return value
        raise ValueError("effect:vm:start requires target, vm_name, or session_id")

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
