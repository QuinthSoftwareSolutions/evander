from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.events.provider_models import VmBootstrapPythonEffectDefinition

from .base import EffectProviderBase
from .vm_targeting import VmTargetingMixin


class VmBootstrapPythonEffectProvider(VmTargetingMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "host_checkout": effect.host_checkout,
            "target_dir": effect.target_dir,
            "vm_user": effect.vm_user,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        context = self._context()
        bootstrapper = getattr(context.vm_python_bootstrapper, "bootstrap_python_in_vm", None)
        if not callable(bootstrapper):
            raise RuntimeError("effect:vm:bootstrap-python vm_python_bootstrapper is not available")

        vm_name, session_id = self._resolve_target(payload, effect_type=self._effect_type())
        result = bootstrapper(
            vm_name=vm_name,
            session_id=session_id,
            host_checkout=self._required_string(
                payload.get("host_checkout"),
                "effect:vm:bootstrap-python requires host_checkout",
            ),
            target_dir=self._required_string(
                payload.get("target_dir"),
                "effect:vm:bootstrap-python requires target_dir",
            ),
            vm_user=self._normalize_optional_string(payload.get("vm_user")) or "agent",
        )
        return {
            "success": bool(result.success),
            "message": self._normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id,
            "vm_name": result.vm_name or vm_name,
        }

    def _effect(self) -> VmBootstrapPythonEffectDefinition:
        if not isinstance(self.effect_definition, VmBootstrapPythonEffectDefinition):
            raise RuntimeError(
                "VmBootstrapPythonEffectProvider initialized with a non-vm-bootstrap-python effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmBootstrapPythonEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _effect_type() -> str:
        return "effect:vm:bootstrap-python"
