from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase
from .vm_power import power_action_result, resolve_power_target


class VmStopEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(resolved_config or {})
        target = self._resolve_target(payload)
        timeout_seconds = self._as_int_or_none(payload.get("timeout_seconds")) or 60

        context = self._context()
        if context.vm_power_controller is None:
            raise RuntimeError("effect:vm:stop vm_power_controller is not available")
        result = context.vm_power_controller.shutdown_vm(target, timeout_seconds=timeout_seconds)
        return power_action_result(result, target=target, action="shutdown")

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmStopEffectProvider has no runtime context")
        return self.runtime_context

    def _resolve_target(self, payload: Mapping[str, Any]) -> str:
        return resolve_power_target(
            payload,
            effect_type="effect:vm:stop",
            normalize_optional_string=self._normalize_optional_string,
        )

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _as_int_or_none(value: Any) -> int | None:
        if value is None or value == "":
            return None
        return int(value)
