from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from agent_sandbox.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from agent_sandbox.messaging.agent_prompt_enqueue import enqueue_agent_prompt
from agent_sandbox.messaging.agent_prompt_queue import QueuedAgentPromptRecord
from agent_sandbox.events.provider_models import AgentEnqueuePromptEffectDefinition
from agent_sandbox.orchestration.effect_extensions import (
    list_vm_summaries_for_agent_reconciliation,
)
from agent_sandbox.workflow.migrations import (
    AGENT_PROMPT_COMPLETED_PORT,
    AGENT_PROMPT_STARTED_PORT,
)
from agent_sandbox.workflow.contracts import NODE_OUTPUTS_CONTEXT_KEY
from agent_sandbox.workflow.validation import NodeCapability

from .agent_prompt import AgentPromptEffectProvider
from .agent_session import (
    container_backend,
    container_prompt_command,
    container_worker_guest_gateway_url,
    effective_config_for_session,
    gateway_url_with_session_context,
    require_container_session_id,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
)
from .base import EffectProviderBase
from .lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
from .resume_thread_metadata import (
    normalize_resume_thread_metadata_collection,
    resume_thread_id_from_metadata,
    resume_thread_metadata_key as normalize_resume_thread_metadata_key,
    resume_thread_metadata_patch,
)

_ACTIVE_CONTAINER_STATUSES = {
    ExecutionStatusKind.PREPARING.value,
    ExecutionStatusKind.STARTING.value,
    ExecutionStatusKind.RUNNING.value,
    "created",
    "starting",
    "running",
}


def _waiting_execution_records(worker: Any, effect_type: str) -> list[Any]:
    execution_store = getattr(worker, "execution_store", None)
    list_waiting = getattr(execution_store, "list_waiting_records", None)
    if callable(list_waiting):
        return list(list_waiting(effect_type))
    read_all = getattr(execution_store, "read_all", None)
    if not callable(read_all):
        return []
    return [
        record
        for record in read_all().values()
        if str(getattr(record, "effect_type", "") or "").strip() == effect_type
        and str(getattr(record, "status", "") or "").strip() == "waiting"
    ]


class AgentEnqueuePromptEffectProvider(EffectProviderBase):
    capability = NodeCapability(
        min_inbound=1,
        max_inbound=1,
        allowed_source_ports=frozenset(
            {
                AGENT_PROMPT_STARTED_PORT,
                AGENT_PROMPT_COMPLETED_PORT,
            }
        ),
    )

    @classmethod
    def lifecycle_hooks(cls) -> tuple[type[AgentEnqueuePromptQueueDispatchHook], ...]:
        return (AgentEnqueuePromptQueueDispatchHook,)

    def execute(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        execution_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
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
            "resume_thread_metadata_key": effect.resume_thread_metadata_key,
            "resume_thread_metadata_collection": effect.resume_thread_metadata_collection,
            "queue_key": effect.queue_key,
            "mcp_tool_approval_lease_id": effect.mcp_tool_approval_lease_id,
        }
        if resolved_config:
            payload.update(dict(resolved_config))
        workflow_context = self._workflow_context_from_sources(
            execution_context=execution_context,
            input_payload=payload,
        )

        prompt = self._require_string(payload.get("prompt"), field_name="prompt")
        target = self._resolve_target(payload)
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        model = self._normalize_optional_string(payload.get("model"))
        resume_thread_id = self._normalize_optional_string(payload.get("resume_thread_id"))
        resume_thread_metadata_key = self._normalize_optional_string(
            payload.get("resume_thread_metadata_key")
        )
        resume_thread_metadata_collection = normalize_resume_thread_metadata_collection(
            payload.get("resume_thread_metadata_collection")
        )
        queue_key = self._normalize_optional_string(payload.get("queue_key"))
        mcp_tool_approval_lease_id = self._resolve_mcp_tool_approval_lease_id(
            payload=payload,
            execution_context=execution_context,
        )
        workflow_run_id = self._workflow_run_id(execution_context)

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
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                queue_key=queue_key,
                config=config,
                effect_id=effect.id,
                workflow_context=workflow_context,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
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
        effective_resume_thread_id = self._effective_resume_thread_id(
            resume_thread_id=resume_thread_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            target=target,
            session_id=session_id,
            session=None,
        )

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
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                effect_id=effect.id,
                queue_key=queue_key,
                workflow_context=workflow_context,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
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
            resume_thread_id=effective_resume_thread_id,
            persist_thread_id=resume_thread_metadata_key is None,
            mcp_gateway_url=self._mcp_gateway_url(
                config=config,
                target=target,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            ),
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
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                effect_id=effect.id,
                queue_key=queue_key,
                workflow_context=workflow_context,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
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
        self._persist_resume_thread_metadata_key(
            target=target,
            session_id=session_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            thread_id=thread_id,
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
        resume_thread_metadata_key: str | None,
        resume_thread_metadata_collection: str,
        queue_key: str | None,
        config: Any,
        effect_id: str,
        workflow_context: dict[str, dict[str, Any]] | None,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
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
        effective_resume_thread_id = self._effective_resume_thread_id(
            resume_thread_id=resume_thread_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            target=target,
            session_id=session_id,
            session=session,
        )

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
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                effect_id=effect_id,
                queue_key=queue_key or session_id,
                workflow_context=workflow_context,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
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
                            resume_thread_id=effective_resume_thread_id,
                            mcp_gateway_url=self._mcp_gateway_url(
                                config=effective_config,
                                target=session_id,
                                workflow_run_id=workflow_run_id,
                                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                                base_gateway_url=container_worker_guest_gateway_url(
                                    effective_config
                                ),
                            ),
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
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                effect_id=effect_id,
                queue_key=queue_key or session_id,
                workflow_context=workflow_context,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            )
            message = self._normalize_optional_string(exc) or type(exc).__name__
            raise RuntimeError(
                "effect:agent:enqueue-prompt container dispatch failed; queued prompt for retry "
                f"(target={target}, session_id={session_id}, queue_key={record.queue_key}, "
                f"coalesced_count={record.coalesced_count}, message={message})"
            ) from exc
        return {
            "dispatch_id": dispatch_id,
            "thread_id": effective_resume_thread_id or dispatch_id,
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
        record = kwargs.get("record")
        outputs = kwargs.get("outputs")
        if record is None or not isinstance(outputs, Mapping):
            return False
        if outputs.get("queued") is True or outputs.get("success") is False:
            return False
        return bool(str(outputs.get("dispatch_id") or "").strip())

    @classmethod
    def on_execution_waiting(cls, **kwargs: Any) -> None:
        worker = kwargs.get("worker")
        record = kwargs.get("record")
        if worker is None or record is None:
            return
        publish_or_mark_pending = getattr(
            worker, "_publish_effect_completion_event_or_mark_pending", None
        )
        if callable(publish_or_mark_pending):
            publish_or_mark_pending(
                record=record,
                outputs=dict(record.outputs),
                branch_port=AGENT_PROMPT_STARTED_PORT,
                pending_status="waiting",
            )
            return
        worker._publish_completion_event_from_record(
            record=record,
            outputs=dict(record.outputs),
            branch_port=AGENT_PROMPT_STARTED_PORT,
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
        for record in _waiting_execution_records(worker, cls.node_type()):
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
        vm_records: list[Any] = []
        for record in _waiting_execution_records(worker, cls.node_type()):
            if (
                str(record.outputs.get("execution_backend") or "").strip()
                == ExecutionBackendKind.CONTAINER_WORKER_VM.value
            ):
                completed += AgentPromptEffectProvider._reconcile_container_waiting_record(
                    worker=worker,
                    record=record,
                )
                continue
            vm_records.append(record)
        if vm_records:
            try:
                summaries = list_vm_summaries_for_agent_reconciliation(worker)
            except Exception:
                summaries = []
            for record in vm_records:
                queue_record = (
                    AgentPromptEffectProvider._build_completion_queue_record_for_waiting_record(
                        record=record,
                        summaries=summaries,
                    )
                )
                if queue_record is None:
                    continue
                AgentPromptEffectProvider._finalize_waiting_record(
                    worker=worker,
                    record=record,
                    queue_record=queue_record,
                )
                completed += 1
        return completed

    @classmethod
    def node_type(cls) -> str:
        return "effect:agent:enqueue-prompt"

    @classmethod
    def container_session_is_busy_for_backend(cls, *, backend: Any, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return False
        repository = getattr(backend, "repository", None)
        list_active_by_session = getattr(repository, "list_active_by_session", None)
        if callable(list_active_by_session):
            records = list_active_by_session(normalized_session_id, _ACTIVE_CONTAINER_STATUSES)
        else:
            read_all = getattr(repository, "read_all", None)
            if not callable(read_all):
                return False
            records = [
                record
                for record in read_all().values()
                if str(getattr(record, "session_id", "") or "").strip() == normalized_session_id
                and str(getattr(record, "last_status", "") or "").strip().casefold()
                in _ACTIVE_CONTAINER_STATUSES
            ]

        for record in records:
            status = str(getattr(record, "last_status", "") or "").strip().casefold()
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

    def _effective_resume_thread_id(
        self,
        *,
        resume_thread_id: str | None,
        resume_thread_metadata_key: str | None,
        resume_thread_metadata_collection: str,
        target: str,
        session_id: str | None,
        session: Any | None,
    ) -> str | None:
        explicit = self._usable_resume_thread_id(resume_thread_id)
        if explicit:
            return explicit
        key = normalize_resume_thread_metadata_key(resume_thread_metadata_key)
        if not key:
            return None
        if session is None:
            session = self._session_for_metadata_lookup(target=target, session_id=session_id)
        if session is None:
            return None
        metadata = getattr(session, "metadata", {})
        if not isinstance(metadata, Mapping):
            return None
        return self._usable_resume_thread_id(
            resume_thread_id_from_metadata(
                metadata,
                collection=resume_thread_metadata_collection,
                key=key,
            )
        )

    def _session_for_metadata_lookup(self, *, target: str, session_id: str | None) -> Any | None:
        context = self._context()
        if context.session_lookup is None:
            return None
        sessions = context.session_lookup.read_all_sessions()
        if session_id:
            session = sessions.get(session_id)
            if session is not None:
                return session
        try:
            from agent_sandbox.admin.service import _resolve_session_target

            _resolved_session_id, session = _resolve_session_target(target, sessions)
        except Exception:
            return None
        return session

    def _persist_resume_thread_metadata_key(
        self,
        *,
        target: str,
        session_id: str | None,
        resume_thread_metadata_key: str | None,
        resume_thread_metadata_collection: str,
        thread_id: str | None,
    ) -> None:
        key = normalize_resume_thread_metadata_key(resume_thread_metadata_key)
        value = self._usable_resume_thread_id(thread_id)
        if not key or not value:
            return
        context = self._context()
        update_fn = getattr(context.session_metadata_updater, "session_update_metadata", None)
        if not callable(update_fn):
            return
        metadata = resume_thread_metadata_patch(
            collection=resume_thread_metadata_collection,
            key=key,
            thread_id=value,
        )
        if not metadata:
            return
        try:
            update_fn(target=session_id or target, metadata=metadata)
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

    def _mcp_gateway_url(
        self,
        *,
        config: Any,
        target: str,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
        base_gateway_url: str | None = None,
    ) -> str | None:
        if not mcp_tool_approval_lease_id:
            return None
        return gateway_url_with_session_context(
            base_gateway_url or config.gateway_url,
            session_id=self._session_id_from_target(target),
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )

    def _session_id_from_target(self, target: str) -> str:
        return self._resolve_session_id(target) or target

    def _resolve_mcp_tool_approval_lease_id(
        self,
        *,
        payload: Mapping[str, Any],
        execution_context: Mapping[str, Any] | None,
    ) -> str | None:
        text = self._normalize_optional_string(payload.get("mcp_tool_approval_lease_id"))
        if text:
            return text
        input_payload = (
            execution_context.get("input_payload")
            if isinstance(execution_context, Mapping)
            else None
        )
        if isinstance(input_payload, Mapping):
            for key in ("mcp_tool_approval_lease_id", "lease_id"):
                text = self._normalize_optional_string(input_payload.get(key))
                if text:
                    return text
        return None

    def _workflow_run_id(self, execution_context: Mapping[str, Any] | None) -> str | None:
        if not isinstance(execution_context, Mapping):
            return None
        for key in ("workflow_run_id", "workflow_trace_id", "trace_id"):
            text = self._normalize_optional_string(execution_context.get(key))
            if text:
                return text
        return None

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
        workflow_context: dict[str, dict[str, Any]] | None = None,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
        resume_thread_metadata_key: str | None = None,
        resume_thread_metadata_collection: str | None = None,
    ) -> QueuedAgentPromptRecord:
        context = self._context()
        metadata = {
            key: value
            for key, value in {
                "workflow_run_id": self._normalize_optional_string(workflow_run_id),
                "mcp_tool_approval_lease_id": self._normalize_optional_string(
                    mcp_tool_approval_lease_id
                ),
            }.items()
            if value
        }
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
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            effect_id=effect_id,
            metadata=metadata,
            workflow_context=workflow_context,
        )

    @classmethod
    def _workflow_context_from_sources(
        cls,
        *,
        execution_context: Mapping[str, Any] | None,
        input_payload: Mapping[str, Any] | None,
    ) -> dict[str, dict[str, Any]] | None:
        for source in (execution_context, input_payload):
            if not isinstance(source, Mapping):
                continue
            workflow_context = cls._copy_workflow_context(source.get(NODE_OUTPUTS_CONTEXT_KEY))
            if workflow_context:
                return workflow_context
            nested_input = source.get("input_payload")
            if isinstance(nested_input, Mapping):
                workflow_context = cls._copy_workflow_context(
                    nested_input.get(NODE_OUTPUTS_CONTEXT_KEY)
                )
                if workflow_context:
                    return workflow_context
        return None

    @staticmethod
    def _copy_workflow_context(raw_context: Any) -> dict[str, dict[str, Any]] | None:
        if not isinstance(raw_context, Mapping):
            return None
        workflow_context: dict[str, dict[str, Any]] = {}
        for node_id, node_payload in raw_context.items():
            if not isinstance(node_id, str) or not isinstance(node_payload, Mapping):
                continue
            if not all(isinstance(key, str) for key in node_payload):
                continue
            try:
                copied_payload = json.loads(json.dumps(dict(node_payload)))
            except (TypeError, ValueError):
                continue
            if isinstance(copied_payload, dict):
                workflow_context[node_id] = copied_payload
        return workflow_context or None

    @classmethod
    def _usable_resume_thread_id(cls, value: Any) -> str | None:
        text = cls._normalize_optional_string(value)
        if text is None:
            return None
        if text.startswith("dispatch-") or text.startswith("chat-thread-"):
            return None
        return text

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
