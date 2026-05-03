from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from agent_sandbox.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from agent_sandbox.messaging.agent_prompt_enqueue import enqueue_agent_prompt
from agent_sandbox.messaging.agent_prompt_queue import QueuedAgentPromptRecord
from agent_sandbox.events.provider_models import AgentEnqueuePromptEffectDefinition

from .agent_prompt import AgentPromptEffectProvider
from .agent_session import (
    container_backend,
    container_prompt_command,
    effective_config_for_session,
    require_container_session_id,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
)
from .base import EffectProviderBase
from .lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook

_ACTIVE_CONTAINER_STATUSES = {
    ExecutionStatusKind.PREPARING.value,
    ExecutionStatusKind.STARTING.value,
    ExecutionStatusKind.RUNNING.value,
    "created",
    "starting",
    "running",
}


class AgentEnqueuePromptEffectProvider(EffectProviderBase):
    @classmethod
    def lifecycle_hooks(cls) -> tuple[type[AgentEnqueuePromptQueueDispatchHook], ...]:
        return (AgentEnqueuePromptQueueDispatchHook,)

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()

        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "prompt": effect.prompt,
            "model": effect.model,
            "vm_user": effect.vm_user,
            "resume_thread_id": effect.resume_thread_id,
            "queue_key": effect.queue_key,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        prompt = self._require_string(payload.get("prompt"), field_name="prompt")
        target = self._resolve_target(payload)
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        model = self._normalize_optional_string(payload.get("model"))
        resume_thread_id = self._normalize_optional_string(payload.get("resume_thread_id"))
        queue_key = self._normalize_optional_string(payload.get("queue_key"))

        execution_backend, config = selected_execution_backend_for_target(
            context,
            {**payload, "target": target},
            directory_path=str(context.metadata.get("directory_path") or "root"),
        )
        if execution_backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            return self._execute_container(
                payload={**payload, "target": target},
                prompt=prompt,
                model=model,
                vm_user=vm_user,
                resume_thread_id=resume_thread_id,
                queue_key=queue_key,
                config=config,
                effect_id=effect.id,
            )
        if execution_backend != ExecutionBackendKind.LIBVIRT_VM.value:
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")
        if (
            context.agent_resumer is None
            or context.vm_inventory is None
            or context.session_lookup is None
        ):
            raise RuntimeError("effect:agent:enqueue-prompt runtime services are not available")

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
                queue_key=queue_key,
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
                queue_key=queue_key,
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
        self._persist_chat_assistant_thread_id(
            session_id=session_id,
            thread_id=thread_id,
            queue_key=queue_key,
            backend=ExecutionBackendKind.LIBVIRT_VM.value,
        )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "success": bool(result.success),
            "queued": False,
            "vm_name": vm_name,
            "session_id": session_id,
        }

    def _execute_container(
        self,
        *,
        payload: dict[str, Any],
        prompt: str,
        model: str | None,
        vm_user: str,
        resume_thread_id: str | None,
        queue_key: str | None,
        config: Any,
        effect_id: str,
    ) -> dict[str, Any]:
        context = self._context()
        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:enqueue-prompt",
            config=config,
        )
        effective_config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=getattr(session, "directory_path", None),
        )
        target_name = self._container_target_name(session=session, session_id=session_id)
        target = self._normalize_optional_string(payload.get("target")) or session_id

        if self._container_session_is_busy(
            context=context,
            config=effective_config,
            session_id=session_id,
        ):
            record = self._queue_prompt(
                target=target,
                vm_name=target_name,
                session_id=session_id,
                prompt=prompt,
                model=model,
                vm_user=vm_user,
                resume_thread_id=resume_thread_id,
                effect_id=effect_id,
                queue_key=queue_key or session_id,
            )
            return {
                "success": True,
                "queued": True,
                "queue_key": record.queue_key,
                "vm_name": target_name,
                "session_id": session_id,
                "coalesced_count": record.coalesced_count,
                "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            }

        dispatch_id = self._make_dispatch_id(effect_id=effect_id)
        resolved_model = model or getattr(session, "model", None) or "gpt-5.4"
        backend = container_backend(context, effective_config)
        execution_id = safe_effect_execution_id("agent", session_id)
        try:
            handle = backend.start(
                backend.prepare(
                    ExecutionSpec(
                        backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                        target_name=execution_id,
                        session_id=session_id,
                        directory_path=getattr(session, "directory_path", None) or "root",
                        command=container_prompt_command(
                            prompt=prompt,
                            model=resolved_model,
                            resume_thread_id=resume_thread_id,
                        ),
                        metadata={
                            "effect_type": self.node_type(),
                            "dispatch_id": dispatch_id,
                        },
                    )
                )
            )
        except Exception as exc:
            record = self._queue_prompt(
                target=target,
                vm_name=target_name,
                session_id=session_id,
                prompt=prompt,
                model=model,
                vm_user=vm_user,
                resume_thread_id=resume_thread_id,
                effect_id=effect_id,
                queue_key=queue_key or session_id,
            )
            message = self._normalize_optional_string(exc) or type(exc).__name__
            raise RuntimeError(
                "effect:agent:enqueue-prompt container dispatch failed; queued prompt for retry "
                f"(target={target}, session_id={session_id}, queue_key={record.queue_key}, "
                f"coalesced_count={record.coalesced_count}, message={message})"
            ) from exc
        return {
            "dispatch_id": dispatch_id,
            "thread_id": resume_thread_id or dispatch_id,
            "success": True,
            "queued": False,
            "output_mode": "on_completion",
            "vm_name": target_name,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "execution_id": handle.execution_id,
        }

    @classmethod
    def on_execution_started(cls, **kwargs: Any) -> bool:
        outputs = kwargs.get("outputs")
        if not isinstance(outputs, Mapping):
            return False
        if outputs.get("queued") is True or outputs.get("success") is False:
            return False
        return (
            str(outputs.get("execution_backend") or "").strip()
            == ExecutionBackendKind.CONTAINER_WORKER_VM.value
            and str(outputs.get("output_mode") or "").strip() == "on_completion"
            and bool(str(outputs.get("dispatch_id") or "").strip())
        )

    @classmethod
    def on_completion_event(cls, **kwargs: Any) -> int:
        worker = kwargs.get("worker")
        queue_record = kwargs.get("queue_record")
        if worker is None or queue_record is None:
            return 0
        dispatch_id = str(getattr(queue_record, "payload", {}).get("dispatch_id") or "").strip()
        if not dispatch_id:
            return 0

        finalized = 0
        for record in worker.execution_store.read_all().values():
            if record.effect_type != cls.node_type() or record.status != "waiting":
                continue
            if str(record.outputs.get("dispatch_id") or "").strip() != dispatch_id:
                continue
            AgentPromptEffectProvider._finalize_waiting_record(
                worker=worker,
                record=record,
                queue_record=queue_record,
            )
            finalized += 1
        return finalized

    @classmethod
    def reconcile_waiting(cls, **kwargs: Any) -> int:
        worker = kwargs.get("worker")
        if worker is None:
            return 0
        completed = 0
        for record in worker.execution_store.read_all().values():
            if record.effect_type != cls.node_type() or record.status != "waiting":
                continue
            if (
                str(record.outputs.get("execution_backend") or "").strip()
                != ExecutionBackendKind.CONTAINER_WORKER_VM.value
            ):
                continue
            completed += AgentPromptEffectProvider._reconcile_container_waiting_record(
                worker=worker,
                record=record,
            )
        return completed

    @classmethod
    def node_type(cls) -> str:
        return "effect:agent:enqueue-prompt"

    @classmethod
    def container_session_is_busy_for_backend(cls, *, backend: Any, session_id: str) -> bool:
        repository = getattr(backend, "repository", None)
        read_all = getattr(repository, "read_all", None)
        if not callable(read_all):
            return False
        for record in read_all().values():
            if str(getattr(record, "session_id", "") or "").strip() != session_id:
                continue
            status = str(getattr(record, "last_status", "") or "").strip().casefold()
            if status not in _ACTIVE_CONTAINER_STATUSES:
                continue
            try:
                inspected = backend.inspect(getattr(record, "execution_id"))
                inspected_status = getattr(inspected, "status", None)
                status = str(getattr(inspected_status, "value", inspected_status) or "").strip()
            except Exception:
                pass
            if status.casefold() in _ACTIVE_CONTAINER_STATUSES:
                return True
        return False

    def _container_session_is_busy(
        self,
        *,
        context: Any,
        config: Any,
        session_id: str,
    ) -> bool:
        return self.container_session_is_busy_for_backend(
            backend=container_backend(context, config),
            session_id=session_id,
        )

    def _persist_chat_assistant_thread_id(
        self,
        *,
        session_id: str,
        thread_id: str,
        queue_key: str | None,
        backend: str,
    ) -> None:
        if not (queue_key or "").startswith("chat:"):
            return
        context = self._context()
        vm_agent_admin = getattr(getattr(context, "admin_service", None), "vm_agent_admin", None)
        persist = getattr(vm_agent_admin, "persist_chat_assistant_agent_thread_id", None)
        if not callable(persist):
            return
        try:
            persist(
                session_id=session_id,
                agent_thread_id=thread_id,
                backend=backend,
                assistant_scope="admin_chat",
                queue_key=queue_key,
            )
        except Exception:
            pass

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
        queue_key: str | None = None,
    ) -> QueuedAgentPromptRecord:
        context = self._context()
        return enqueue_agent_prompt(
            runtime_dir=context.resolved_config.runtime_dir,
            queue_key=queue_key,
            target=target,
            vm_name=vm_name,
            session_id=session_id,
            prompt=prompt,
            model=model,
            vm_user=vm_user,
            resume_thread_id=resume_thread_id,
            effect_id=effect_id,
        )

    @staticmethod
    def _container_target_name(*, session: Any | None, session_id: str) -> str:
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if isinstance(metadata, Mapping):
            target_name = str(metadata.get("target_name") or "").strip()
            if target_name:
                return target_name
        provider_id = str(getattr(session, "vm_provider_id", "") or "").strip()
        return provider_id or session_id

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
