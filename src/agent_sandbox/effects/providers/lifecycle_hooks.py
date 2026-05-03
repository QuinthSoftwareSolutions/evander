from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from agent_sandbox.execution import ExecutionBackendKind, ExecutionSpec, build_execution_backend
from agent_sandbox.effects.providers.base import EffectLifecycleHookBase
from agent_sandbox.effects.providers.agent_session import (
    container_prompt_command,
    safe_effect_execution_id,
)
from agent_sandbox.orchestration.effect_extensions import (
    dispatch_agent_prompt,
    list_vm_summaries_for_agent_reconciliation,
    resolve_agent_prompt_dispatch_port,
)


class ProviderCompletionLifecycleHook(EffectLifecycleHookBase):
    def __init__(self, provider_class: type[Any]) -> None:
        self._provider_class = provider_class

    def on_waiting_reconciliation(self, *, worker: Any) -> int:
        return int(self._provider_class.reconcile_waiting(worker=worker) or 0)

    def on_completion_fan_out(self, *, worker: Any, queue_record: Any, graph: Any) -> int:
        del graph
        return int(
            self._provider_class.on_completion_event(worker=worker, queue_record=queue_record) or 0
        )


class AgentEnqueuePromptQueueDispatchHook(EffectLifecycleHookBase):
    def on_post_completion_dispatch(self, *, worker: Any) -> int:
        queue_store = getattr(worker, "queued_agent_prompt_store", None)
        dispatch_port = resolve_agent_prompt_dispatch_port(worker)
        if queue_store is None or dispatch_port is None:
            return 0

        queued_records = queue_store.read_all()
        if not queued_records:
            return 0

        dispatched = 0
        container_queue_keys = self._container_queue_keys(worker=worker, records=queued_records)
        for queue_key in sorted(container_queue_keys):
            record = queued_records.get(queue_key)
            if record is None:
                continue
            if self._dispatch_container_record_if_idle(
                worker=worker,
                queue_store=queue_store,
                queue_key=queue_key,
                record=record,
            ):
                dispatched += 1

        queued_records = {
            queue_key: record
            for queue_key, record in queue_store.read_all().items()
            if queue_key not in container_queue_keys
        }
        if not queued_records:
            return dispatched

        summaries = {
            str(getattr(item, "vm_name", "") or "").strip(): item
            for item in list_vm_summaries_for_agent_reconciliation(worker)
        }

        for queue_key, record in sorted(queued_records.items()):
            graph = worker._build_execution_graph()
            effect = graph.effects_by_id.get(record.effect_id)
            if effect is None or getattr(effect, "type", "") != "effect:agent:enqueue-prompt":
                continue
            vm_name = str(record.vm_name or "").strip()
            summary = summaries.get(vm_name)
            if (
                summary is not None
                and str(getattr(summary, "agent_state", "") or "").strip() == "running"
            ):
                continue

            dispatch_id = (
                f"dispatch-{record.effect_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
            )
            result = dispatch_agent_prompt(
                worker,
                target=record.target,
                prompt=record.prompt,
                model=record.model,
                vm_user=record.vm_user,
                dispatch_id=dispatch_id,
                resume_thread_id=record.resume_thread_id,
            )
            if result is not None and bool(getattr(result, "success", False)):
                self._persist_chat_assistant_thread_id(
                    worker=worker,
                    record=record,
                    result=result,
                    queue_key=queue_key,
                )
                queue_store.delete(queue_key)
                dispatched += 1

        return dispatched

    def _persist_chat_assistant_thread_id(
        self,
        *,
        worker: Any,
        record: Any,
        result: Any,
        queue_key: str,
    ) -> None:
        if not str(queue_key or "").startswith("chat:"):
            return
        thread_id = str(getattr(result, "thread_id", "") or "").strip()
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if not thread_id or not session_id:
            return
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        vm_agent_admin = getattr(admin_service, "vm_agent_admin", None)
        persist = getattr(vm_agent_admin, "persist_chat_assistant_agent_thread_id", None)
        if not callable(persist):
            return
        try:
            persist(
                session_id=session_id,
                agent_thread_id=thread_id,
                backend=ExecutionBackendKind.LIBVIRT_VM.value,
                assistant_scope="admin_chat",
                queue_key=queue_key,
            )
        except Exception:
            pass

    def _container_queue_keys(self, *, worker: Any, records: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        for queue_key, record in records.items():
            if self._container_record_context(worker=worker, record=record) is not None:
                keys.add(queue_key)
        return keys

    def _dispatch_container_record_if_idle(
        self,
        *,
        worker: Any,
        queue_store: Any,
        queue_key: str,
        record: Any,
    ) -> bool:
        context = self._container_record_context(worker=worker, record=record)
        if context is None:
            return False
        session_id, session, config, backend = context
        from agent_sandbox.effects.providers.agent_enqueue_prompt import (
            AgentEnqueuePromptEffectProvider,
        )

        if AgentEnqueuePromptEffectProvider.container_session_is_busy_for_backend(
            backend=backend,
            session_id=session_id,
        ):
            return False

        dispatch_id = f"dispatch-{record.effect_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        model = str(record.model or getattr(session, "model", None) or "gpt-5.4").strip()
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
                            prompt=record.prompt,
                            model=model,
                            resume_thread_id=record.resume_thread_id,
                        ),
                        metadata={
                            "effect_type": "effect:agent:enqueue-prompt",
                            "dispatch_id": dispatch_id,
                            "queued": True,
                        },
                    )
                )
            )
        except Exception:
            return False
        if not self._record_container_waiting_execution(
            worker=worker,
            record=record,
            queue_key=queue_key,
            session=session,
            session_id=session_id,
            dispatch_id=dispatch_id,
            execution_id=handle.execution_id,
            model=model,
        ):
            try:
                backend.destroy(handle.execution_id)
            except Exception:
                pass
            return False
        queue_store.delete(queue_key)
        return True

    def _record_container_waiting_execution(
        self,
        *,
        worker: Any,
        record: Any,
        queue_key: str,
        session: Any,
        session_id: str,
        dispatch_id: str,
        execution_id: str,
        model: str,
    ) -> bool:
        execution_store = getattr(worker, "execution_store", None)
        record_execution = getattr(execution_store, "record_execution", None)
        if not callable(record_execution):
            return False

        from agent_sandbox.daemons.effect_orchestrator import EffectExecutionRecord

        event_id = f"queued-agent-prompt:{queue_key}:{dispatch_id}"
        idempotency_key = f"{event_id}::{record.effect_id}"
        find_by_idempotency_key = getattr(
            execution_store,
            "find_execution_by_idempotency_key",
            None,
        )
        if (
            callable(find_by_idempotency_key)
            and find_by_idempotency_key(idempotency_key) is not None
        ):
            return True

        now = datetime.now(UTC)
        directory_path = str(getattr(session, "directory_path", None) or "root")
        vm_user = str(getattr(record, "vm_user", None) or "agent")
        resolved_config = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": model,
            "vm_user": vm_user,
            "resume_thread_id": record.resume_thread_id,
            "directory_path": directory_path,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "queue_key": queue_key,
        }
        outputs = {
            "dispatch_id": dispatch_id,
            "thread_id": record.resume_thread_id or dispatch_id,
            "success": True,
            "queued": False,
            "dispatched_from_queue": True,
            "output_mode": "on_completion",
            "vm_name": record.vm_name,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "execution_id": execution_id,
            "queue_key": queue_key,
        }
        execution_record = EffectExecutionRecord(
            execution_id=(f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"),
            idempotency_key=idempotency_key,
            event_id=event_id,
            trace_id=f"queued-agent-prompt:{queue_key}",
            source_node_id=record.effect_id,
            source_trigger_type="effect:agent:enqueue-prompt",
            effect_id=record.effect_id,
            effect_type="effect:agent:enqueue-prompt",
            status="waiting",
            started_at=now,
            finished_at=now,
            resolved_config_hash=self._json_sha256(resolved_config),
            resolved_config=resolved_config,
            input_payload={
                "target": record.target,
                "vm_name": record.vm_name,
                "session_id": session_id,
                "prompt": record.prompt,
                "model": model,
                "vm_user": vm_user,
                "resume_thread_id": record.resume_thread_id,
                "queue_key": queue_key,
            },
            outputs=outputs,
            config_revision=getattr(worker, "_last_graph_revision", None),
        )
        try:
            record_execution(execution_record)
        except Exception:
            return False
        return True

    @staticmethod
    def _json_sha256(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _container_record_context(self, *, worker: Any, record: Any) -> tuple[Any, ...] | None:
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if not session_id:
            return None
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        repository = getattr(runtime, "session_repository", None)
        get_session = getattr(repository, "get_session", None)
        if not callable(get_session):
            return None
        session = get_session(session_id)
        if session is None:
            return None
        metadata = getattr(session, "metadata", {})
        provider = str(getattr(session, "vm_provider", "") or "").strip()
        if provider != ExecutionBackendKind.CONTAINER_WORKER_VM.value and not (
            isinstance(metadata, dict)
            and str(metadata.get("execution_backend") or "").strip()
            == ExecutionBackendKind.CONTAINER_WORKER_VM.value
        ):
            return None
        config = self._config_for_container_record(
            worker=worker,
            session=session,
            session_id=session_id,
        )
        if config is None:
            return None
        try:
            backend = build_execution_backend(
                config.model_copy(
                    update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
                ),
                admin_service=admin_service,
            )
        except Exception:
            return None
        return session_id, session, config, backend

    def _config_for_container_record(
        self,
        *,
        worker: Any,
        session: Any,
        session_id: str,
    ) -> Any | None:
        resolver = getattr(worker, "provider_config_resolver", None)
        for method_name, argument in (
            ("resolve_for_session_id", session_id),
            ("resolve_for_directory", getattr(session, "directory_path", None)),
        ):
            resolver_method = getattr(resolver, method_name, None)
            if not callable(resolver_method):
                continue
            try:
                return resolver_method(argument)
            except Exception:
                continue
        return getattr(worker, "config", None)


def build_effect_lifecycle_hooks(*, provider_factory: Any) -> tuple[EffectLifecycleHookBase, ...]:
    hooks: list[EffectLifecycleHookBase] = []

    for effect_type in provider_factory.registry.registered_types():
        provider_class = provider_factory.registry.resolve(effect_type)
        hooks.append(ProviderCompletionLifecycleHook(provider_class))
        for hook_class in provider_class.lifecycle_hooks():
            hooks.append(hook_class())
    return tuple(hooks)
