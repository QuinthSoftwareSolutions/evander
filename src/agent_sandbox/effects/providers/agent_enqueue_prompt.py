from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from agent_sandbox.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    QueuedAgentPromptRecord,
)
from agent_sandbox.events.provider_models import AgentEnqueuePromptEffectDefinition

from .base import EffectProviderBase
from .lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook


class AgentEnqueuePromptEffectProvider(EffectProviderBase):
    @classmethod
    def lifecycle_hooks(cls) -> tuple[type[AgentEnqueuePromptQueueDispatchHook], ...]:
        return (AgentEnqueuePromptQueueDispatchHook,)

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        if (
            context.agent_resumer is None
            or context.vm_inventory is None
            or context.session_lookup is None
        ):
            raise RuntimeError("effect:agent:enqueue-prompt runtime services are not available")

        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "prompt": effect.prompt,
            "model": effect.model,
            "vm_user": effect.vm_user,
            "resume_thread_id": effect.resume_thread_id,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        prompt = self._require_string(payload.get("prompt"), field_name="prompt")
        target = self._resolve_target(payload)
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        model = self._normalize_optional_string(payload.get("model"))
        resume_thread_id = self._normalize_optional_string(payload.get("resume_thread_id"))
        vm_name = self._resolve_vm_name(target)
        session_id = self._resolve_session_id(target)

        summary = self._find_vm_summary(vm_name=vm_name, vm_user=vm_user)
        if (
            summary is not None
            and str(getattr(summary, "agent_state", "") or "").strip() == "running"
        ):
            record = self._queue_prompt(
                target=target,
                vm_name=vm_name,
                session_id=session_id,
                prompt=prompt,
                model=model,
                vm_user=vm_user,
                resume_thread_id=resume_thread_id,
                effect_id=effect.id,
            )
            return {
                "success": True,
                "queued": True,
                "queue_key": record.queue_key,
                "vm_name": vm_name,
                "session_id": session_id,
                "coalesced_count": record.coalesced_count,
            }

        dispatch_id = self._make_dispatch_id(effect_id=effect.id)
        result = context.agent_resumer.resume_agent(
            target=target,
            prompt=prompt,
            model=model,
            vm_user=vm_user,
            dispatch_id=dispatch_id,
            resume_thread_id=resume_thread_id,
        )
        if not bool(getattr(result, "success", False)):
            record = self._queue_prompt(
                target=target,
                vm_name=vm_name,
                session_id=session_id,
                prompt=prompt,
                model=model,
                vm_user=vm_user,
                resume_thread_id=resume_thread_id,
                effect_id=effect.id,
            )
            message = self._normalize_optional_string(getattr(result, "message", None)) or "<empty>"
            raise RuntimeError(
                "effect:agent:enqueue-prompt resume_agent failed; queued prompt for retry "
                f"(vm_name={vm_name}, session_id={session_id or '<none>'}, queue_key={record.queue_key}, "
                f"coalesced_count={record.coalesced_count}, message={message})"
            )
        thread_id = self._normalize_optional_string(getattr(result, "thread_id", None))
        if not thread_id:
            raise RuntimeError(
                "effect:agent:enqueue-prompt did not receive a thread_id from resume_agent"
            )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "success": bool(result.success),
            "queued": False,
            "vm_name": vm_name,
            "session_id": session_id,
        }

    def _find_vm_summary(self, *, vm_name: str, vm_user: str) -> Any | None:
        context = self._context()
        if context.vm_inventory is None:
            return None
        for summary in context.vm_inventory.list_vms(include_inactive=True, vm_user=vm_user):
            if str(getattr(summary, "vm_name", "") or "").strip() == vm_name:
                return summary
        return None

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
        raise ValueError(
            "effect:agent:enqueue-prompt requires one of target, session_id, or vm_name"
        )

    def _resolve_vm_name(self, target: str) -> str:
        context = self._context()
        if context.session_lookup is None:
            raise RuntimeError("effect:agent:enqueue-prompt session_lookup is not available")

        from agent_sandbox.admin.service import _resolve_vm_name_from_target

        vm_name = _resolve_vm_name_from_target(target, context.session_lookup.read_all_sessions())
        if not vm_name:
            raise ValueError(f"Unknown VM target: {target}")
        return vm_name

    def _resolve_session_id(self, target: str) -> str | None:
        context = self._context()
        try:
            if context.session_lookup is None:
                return None

            from agent_sandbox.admin.service import _resolve_session_target

            session_id, _manifest = _resolve_session_target(
                target, context.session_lookup.read_all_sessions()
            )
        except ValueError:
            return None
        return session_id

    def _effect(self) -> AgentEnqueuePromptEffectDefinition:
        if not isinstance(self.effect_definition, AgentEnqueuePromptEffectDefinition):
            raise RuntimeError(
                "AgentEnqueuePromptEffectProvider initialized with a non-agent-enqueue-prompt effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentEnqueuePromptEffectProvider has no runtime context")
        return self.runtime_context

    def _queue_prompt(
        self,
        *,
        target: str,
        vm_name: str,
        session_id: str | None,
        prompt: str,
        model: str | None,
        vm_user: str,
        resume_thread_id: str | None,
        effect_id: str,
    ) -> QueuedAgentPromptRecord:
        context = self._context()
        queue_store = AgentPromptQueueStore(
            context.resolved_config.runtime_dir / "queued-agent-prompts.json"
        )
        return queue_store.put(
            QueuedAgentPromptRecord(
                queue_key=vm_name,
                target=target,
                vm_name=vm_name,
                session_id=session_id,
                prompt=prompt,
                model=model,
                vm_user=vm_user,
                resume_thread_id=resume_thread_id,
                effect_id=effect_id,
            )
        )

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
