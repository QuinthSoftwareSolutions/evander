from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from agent_sandbox.events.models import AgentPromptEffectDefinition

from .base import EffectProviderBase


class AgentPromptEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        if context.agent_resumer is None:
            raise RuntimeError("effect:agent:prompt agent_resumer is not available")

        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "prompt": effect.prompt,
            "model": effect.model,
            "vm_user": effect.vm_user,
            "resume_thread_id": effect.resume_thread_id,
            "output_mode": effect.output_mode,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        prompt = self._require_string(payload.get("prompt"), field_name="prompt")
        target = self._resolve_target(payload)
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        model = self._normalize_optional_string(payload.get("model"))
        output_mode = self._normalize_optional_string(payload.get("output_mode")) or "immediate"
        if output_mode not in {"immediate", "on_completion"}:
            raise ValueError(f"Unsupported output_mode: {output_mode}")

        dispatch_id = self._make_dispatch_id(effect_id=effect.id)
        resume_thread_id = self._normalize_optional_string(payload.get("resume_thread_id"))
        result = context.agent_resumer.resume_agent(
            target=target,
            prompt=prompt,
            model=model,
            vm_user=vm_user,
            dispatch_id=dispatch_id,
            resume_thread_id=resume_thread_id,
        )
        thread_id = self._normalize_optional_string(getattr(result, "thread_id", None))
        if not thread_id:
            result_success = bool(getattr(result, "success", False))
            result_message = self._normalize_optional_string(getattr(result, "message", None)) or "<empty>"
            raise RuntimeError(
                "effect:agent:prompt did not receive a thread_id from resume_agent "
                f"(success={result_success}, message={result_message})"
            )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "success": bool(result.success),
            "output_mode": output_mode,
        }

    def _resolve_target(self, payload: dict[str, Any]) -> str:
        explicit_target = self._normalize_optional_string(payload.get("target"))
        if explicit_target:
            return explicit_target
        session_id = self._normalize_optional_string(payload.get("session_id"))
        if session_id:
            return session_id
        vm_name = self._normalize_optional_string(payload.get("vm_name"))
        if vm_name:
            return vm_name
        raise ValueError("effect:agent:prompt requires one of target, session_id, or vm_name")

    def _effect(self) -> AgentPromptEffectDefinition:
        if not isinstance(self.effect_definition, AgentPromptEffectDefinition):
            raise RuntimeError("AgentPromptEffectProvider initialized with a non-agent-prompt effect definition")
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentPromptEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _require_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    @staticmethod
    def _make_dispatch_id(*, effect_id: str) -> str:
        return f"dispatch-{effect_id}-{uuid4().hex[:12]}"
