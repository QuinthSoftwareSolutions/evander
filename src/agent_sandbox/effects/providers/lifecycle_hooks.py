from __future__ import annotations

import hashlib
import logging
import json
from datetime import UTC, datetime
from typing import Any

from agent_sandbox.execution import ExecutionBackendKind, ExecutionSpec, build_execution_backend
from agent_sandbox.effects.providers.base import EffectLifecycleHookBase
from agent_sandbox.effects.providers.agent_session import (
    container_worker_guest_gateway_url,
    container_prompt_command,
    gateway_url_with_session_context,
    safe_effect_execution_id,
)
from agent_sandbox.model_usage.runtime import (
    register_model_usage_correlation_context,
    resolve_runtime_dir,
)
from agent_sandbox.effects.providers.resume_thread_metadata import (
    normalize_resume_thread_metadata_collection,
    resume_thread_id_from_metadata,
    resume_thread_metadata_key as normalize_resume_thread_metadata_key,
    resume_thread_metadata_patch,
)
from agent_sandbox.orchestration.effect_extensions import (
    dispatch_agent_prompt,
    list_vm_summaries_for_agent_reconciliation,
    resolve_agent_prompt_dispatch_port,
)
from agent_sandbox.workflow.contracts import NODE_OUTPUTS_CONTEXT_KEY
from agent_sandbox.workflow.migrations import AGENT_PROMPT_STARTED_PORT

logger = logging.getLogger(__name__)

_ADMIN_AGENT_PROMPT_EFFECT_ID_PREFIX = "admin-agent-prompt:"


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
            if self._has_waiting_enqueue_prompt_for_record(worker=worker, record=record):
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

        try:
            summaries = {
                str(getattr(item, "vm_name", "") or "").strip(): item
                for item in list_vm_summaries_for_agent_reconciliation(worker)
            }
        except Exception:
            logger.exception(
                "AgentEnqueuePromptQueueDispatchHook inventory reconciliation skipped "
                "phase=post_completion_dispatch queued_non_container_records=%d",
                len(queued_records),
            )
            return dispatched

        for queue_key, record in sorted(queued_records.items()):
            graph = worker._build_execution_graph()
            effect = graph.effects_by_id.get(record.effect_id)
            if not self._is_dispatchable_libvirt_record(effect=effect, record=record):
                continue
            if self._has_waiting_enqueue_prompt_for_record(worker=worker, record=record):
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
            _session_id, session = self._session_context_for_libvirt_record(
                worker=worker,
                record=record,
            )
            effective_resume_thread_id = self._effective_record_resume_thread_id(
                record=record,
                session=session,
            )
            result = dispatch_agent_prompt(
                worker,
                target=record.target,
                prompt=record.prompt,
                model=record.model,
                vm_user=record.vm_user,
                dispatch_id=dispatch_id,
                resume_thread_id=effective_resume_thread_id,
                persist_thread_id=self._resume_thread_metadata_key(record) is None,
                mcp_gateway_url=self._mcp_gateway_url_for_libvirt_record(
                    worker=worker,
                    record=record,
                ),
            )
            if result is not None and bool(getattr(result, "success", False)):
                self._persist_chat_assistant_thread_id(
                    worker=worker,
                    record=record,
                    result=result,
                    queue_key=queue_key,
                )
                self._persist_resume_thread_metadata_key(
                    worker=worker,
                    record=record,
                    thread_id=str(getattr(result, "thread_id", "") or "").strip(),
                )
                self._record_libvirt_waiting_execution(
                    worker=worker,
                    record=record,
                    queue_key=queue_key,
                    dispatch_id=dispatch_id,
                    result=result,
                    effective_resume_thread_id=effective_resume_thread_id,
                )
                queue_store.delete(queue_key)
                dispatched += 1

        return dispatched

    @staticmethod
    def _is_dispatchable_libvirt_record(*, effect: Any | None, record: Any) -> bool:
        if effect is not None and getattr(effect, "type", "") == "effect:agent:enqueue-prompt":
            return True
        effect_id = str(getattr(record, "effect_id", "") or "").strip()
        queue_key = str(getattr(record, "queue_key", "") or "").strip()
        return effect_id.startswith(_ADMIN_AGENT_PROMPT_EFFECT_ID_PREFIX) and queue_key.startswith(
            _ADMIN_AGENT_PROMPT_EFFECT_ID_PREFIX
        )

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
        record_metadata = getattr(record, "metadata", {})
        workflow_run_id = (
            str(record_metadata.get("workflow_run_id") or "").strip()
            if isinstance(record_metadata, dict)
            else ""
        )
        mcp_tool_approval_lease_id = (
            str(record_metadata.get("mcp_tool_approval_lease_id") or "").strip()
            if isinstance(record_metadata, dict)
            else ""
        )
        mcp_gateway_url = None
        if mcp_tool_approval_lease_id:
            mcp_gateway_url = gateway_url_with_session_context(
                container_worker_guest_gateway_url(config),
                session_id=session_id,
                workflow_run_id=workflow_run_id or None,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            )
        effective_resume_thread_id = self._effective_record_resume_thread_id(
            record=record,
            session=session,
        )
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
                            resume_thread_id=effective_resume_thread_id,
                            resume_mode=getattr(record, "resume_mode", None),
                            mcp_gateway_url=mcp_gateway_url,
                            web_search_enabled=bool(
                                getattr(session, "web_search_enabled", False)
                            ),
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
            effective_resume_thread_id=effective_resume_thread_id,
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
        effective_resume_thread_id: str | None,
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
            "resume_thread_id": effective_resume_thread_id,
            "resume_mode": getattr(record, "resume_mode", None),
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "directory_path": directory_path,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "queue_key": queue_key,
        }
        outputs = {
            "dispatch_id": dispatch_id,
            "thread_id": effective_resume_thread_id or dispatch_id,
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
        input_payload = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": model,
            "vm_user": vm_user,
            "resume_thread_id": effective_resume_thread_id,
            "resume_mode": getattr(record, "resume_mode", None),
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "queue_key": queue_key,
        }
        self._restore_workflow_context(input_payload=input_payload, record=record)
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
            input_payload=input_payload,
            outputs=outputs,
            config_revision=getattr(worker, "_last_graph_revision", None),
        )
        try:
            record_execution(execution_record)
        except Exception:
            return False
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is not None:
            record_metadata = getattr(record, "metadata", {})
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=(
                    str(record_metadata.get("workflow_run_id") or "").strip()
                    if isinstance(record_metadata, dict)
                    else None
                ),
                effect_id=record.effect_id,
                execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                directory_path=directory_path,
                model=model,
                trace_id=execution_record.trace_id,
            )
        self._publish_started_event_for_waiting_record(
            worker=worker,
            execution_record=execution_record,
        )
        return True

    def _record_libvirt_waiting_execution(
        self,
        *,
        worker: Any,
        record: Any,
        queue_key: str,
        dispatch_id: str,
        result: Any,
        effective_resume_thread_id: str | None,
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
        session_id, session = self._session_context_for_libvirt_record(
            worker=worker,
            record=record,
        )
        vm_user = str(getattr(record, "vm_user", None) or "agent")
        thread_id = str(getattr(result, "thread_id", "") or "").strip()
        effective_thread_id = thread_id or str(effective_resume_thread_id or "").strip()
        model = str(record.model or getattr(session, "model", None) or "gpt-5.4").strip()
        record_metadata = getattr(record, "metadata", {})
        directory_path = (
            str(getattr(session, "directory_path", None) or "").strip()
            or (
                str(record_metadata.get("directory_path") or "").strip()
                if isinstance(record_metadata, dict)
                else ""
            )
            or "root"
        )
        resolved_config = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": record.model,
            "vm_user": vm_user,
            "resume_thread_id": effective_resume_thread_id,
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "execution_backend": ExecutionBackendKind.LIBVIRT_VM.value,
            "queue_key": queue_key,
        }
        outputs = {
            "dispatch_id": dispatch_id,
            "success": True,
            "queued": False,
            "dispatched_from_queue": True,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.LIBVIRT_VM.value,
            "queue_key": queue_key,
        }
        if effective_thread_id:
            outputs["thread_id"] = effective_thread_id
        input_payload = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": record.model,
            "vm_user": vm_user,
            "resume_thread_id": effective_resume_thread_id,
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "queue_key": queue_key,
        }
        self._restore_workflow_context(input_payload=input_payload, record=record)
        execution_record = EffectExecutionRecord(
            execution_id=(
                f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"
            ),
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
            input_payload=input_payload,
            outputs=outputs,
            config_revision=getattr(worker, "_last_graph_revision", None),
        )
        try:
            record_execution(execution_record)
        except Exception:
            return False
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is not None:
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=(
                    str(record_metadata.get("workflow_run_id") or "").strip()
                    if isinstance(record_metadata, dict)
                    else None
                ),
                effect_id=record.effect_id,
                execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
                directory_path=directory_path,
                model=model,
                trace_id=execution_record.trace_id,
            )
        self._publish_started_event_for_waiting_record(
            worker=worker,
            execution_record=execution_record,
        )
        return True

    def _publish_started_event_for_waiting_record(
        self,
        *,
        worker: Any,
        execution_record: Any,
    ) -> None:
        publish_or_mark_pending = getattr(
            worker, "_publish_effect_completion_event_or_mark_pending", None
        )
        if callable(publish_or_mark_pending):
            publish_or_mark_pending(
                record=execution_record,
                outputs=dict(execution_record.outputs),
                branch_port=AGENT_PROMPT_STARTED_PORT,
                pending_status="waiting",
            )
            return
        publish = getattr(worker, "_publish_completion_event_from_record", None)
        if not callable(publish):
            return
        try:
            publish(
                record=execution_record,
                outputs=dict(execution_record.outputs),
                branch_port=AGENT_PROMPT_STARTED_PORT,
            )
        except Exception:
            return

    @staticmethod
    def _restore_workflow_context(*, input_payload: dict[str, Any], record: Any) -> None:
        workflow_context = AgentEnqueuePromptQueueDispatchHook._workflow_context_from_record(
            record
        )
        if workflow_context:
            input_payload[NODE_OUTPUTS_CONTEXT_KEY] = workflow_context

    def _has_waiting_enqueue_prompt_for_record(self, *, worker: Any, record: Any) -> bool:
        execution_store = getattr(worker, "execution_store", None)
        list_waiting = getattr(execution_store, "list_waiting_records", None)
        read_all = getattr(execution_store, "read_all", None)
        session_id = str(getattr(record, "session_id", "") or "").strip()
        vm_name = str(getattr(record, "vm_name", "") or "").strip()
        target = str(getattr(record, "target", "") or "").strip()
        try:
            if callable(list_waiting):
                records = list_waiting("effect:agent:enqueue-prompt")
            elif callable(read_all):
                records = [
                    execution
                    for execution in read_all().values()
                    if str(getattr(execution, "effect_type", "") or "").strip()
                    == "effect:agent:enqueue-prompt"
                    and str(getattr(execution, "status", "") or "").strip() == "waiting"
                ]
            else:
                return False
        except Exception:
            return False
        for execution in records:
            outputs = getattr(execution, "outputs", None)
            if not isinstance(outputs, dict):
                outputs = {}
            resolved_config = getattr(execution, "resolved_config", None)
            if not isinstance(resolved_config, dict):
                resolved_config = {}
            input_payload = getattr(execution, "input_payload", None)
            if not isinstance(input_payload, dict):
                input_payload = {}
            waiting_session_id = str(
                outputs.get("session_id") or resolved_config.get("session_id") or ""
            ).strip()
            waiting_vm_name = str(
                outputs.get("vm_name") or resolved_config.get("vm_name") or ""
            ).strip()
            waiting_target = str(
                resolved_config.get("target") or input_payload.get("target") or ""
            ).strip()
            if session_id and waiting_session_id == session_id:
                return True
            if vm_name and waiting_vm_name == vm_name:
                return True
            if target and waiting_target == target:
                return True
        return False

    def _effective_record_resume_thread_id(
        self,
        *,
        record: Any,
        session: Any | None,
    ) -> str | None:
        explicit = self._usable_resume_thread_id(getattr(record, "resume_thread_id", None))
        if explicit:
            return explicit
        key = self._resume_thread_metadata_key(record)
        if not key or session is None:
            return None
        metadata = getattr(session, "metadata", {})
        if not isinstance(metadata, dict):
            return None
        return self._usable_resume_thread_id(
            resume_thread_id_from_metadata(
                metadata,
                collection=self._resume_thread_metadata_collection(record),
                key=key,
            )
        )

    def _persist_resume_thread_metadata_key(
        self,
        *,
        worker: Any,
        record: Any,
        thread_id: str | None,
    ) -> None:
        key = self._resume_thread_metadata_key(record)
        value = self._usable_resume_thread_id(thread_id)
        session_id = str(getattr(record, "session_id", "") or "").strip()
        target = str(getattr(record, "target", "") or "").strip()
        if not key or not value or not (session_id or target):
            return
        metadata_patch = resume_thread_metadata_patch(
            collection=self._resume_thread_metadata_collection(record),
            key=key,
            thread_id=value,
        )
        if not metadata_patch:
            return
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        update_fn = getattr(admin_service, "session_update_metadata", None)
        if callable(update_fn):
            try:
                update_fn(target=session_id or target, metadata=metadata_patch)
                return
            except Exception:
                pass
        repository = self._session_repository_for_worker(worker)
        get_session = getattr(repository, "get_session", None)
        upsert_session = getattr(repository, "upsert_session", None)
        if not callable(get_session) or not callable(upsert_session):
            return
        try:
            resolved_session_id = session_id
            if not resolved_session_id:
                resolved_session_id, _session = self._session_context_for_libvirt_record(
                    worker=worker,
                    record=record,
                )
            if not resolved_session_id:
                return
            session = get_session(resolved_session_id)
            if session is None:
                return
            metadata = getattr(session, "metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            merged_metadata = self._deep_merge_metadata(metadata, metadata_patch)
            upsert_session(
                session.model_copy(update={"metadata": merged_metadata})
            )
        except Exception:
            return

    @staticmethod
    def _resume_thread_metadata_key(record: Any) -> str | None:
        return normalize_resume_thread_metadata_key(
            getattr(record, "resume_thread_metadata_key", None)
        )

    @staticmethod
    def _resume_thread_metadata_collection(record: Any) -> str:
        return normalize_resume_thread_metadata_collection(
            getattr(record, "resume_thread_metadata_collection", None)
        )

    @classmethod
    def _deep_merge_metadata(
        cls, base: dict[str, Any], update: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in update.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = cls._deep_merge_metadata(existing, value)
                continue
            merged[key] = value
        return merged

    @staticmethod
    def _usable_resume_thread_id(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.startswith("dispatch-") or text.startswith("chat-thread-"):
            return None
        return text

    @staticmethod
    def _workflow_context_from_record(record: Any) -> dict[str, dict[str, Any]] | None:
        raw_context = getattr(record, "workflow_context", None)
        if not isinstance(raw_context, dict):
            return None
        workflow_context: dict[str, dict[str, Any]] = {}
        for node_id, node_payload in raw_context.items():
            if isinstance(node_id, str) and isinstance(node_payload, dict):
                workflow_context[node_id] = dict(node_payload)
        return workflow_context or None

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

    def _mcp_gateway_url_for_libvirt_record(
        self,
        *,
        worker: Any,
        record: Any,
    ) -> str | None:
        record_metadata = getattr(record, "metadata", {})
        if not isinstance(record_metadata, dict):
            return None
        mcp_tool_approval_lease_id = str(
            record_metadata.get("mcp_tool_approval_lease_id") or ""
        ).strip()
        if not mcp_tool_approval_lease_id:
            return None

        workflow_run_id = str(record_metadata.get("workflow_run_id") or "").strip() or None
        session_id, session = self._session_context_for_libvirt_record(
            worker=worker,
            record=record,
        )
        if not session_id:
            return None
        config = self._config_for_libvirt_record(
            worker=worker,
            session_id=session_id,
            session=session,
        )
        base_gateway_url = str(getattr(config, "gateway_url", "") or "").strip()
        if not base_gateway_url:
            return None
        return gateway_url_with_session_context(
            base_gateway_url,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )

    def _session_context_for_libvirt_record(
        self,
        *,
        worker: Any,
        record: Any,
    ) -> tuple[str | None, Any | None]:
        repository = self._session_repository_for_worker(worker)
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if repository is not None and session_id:
            get_session = getattr(repository, "get_session", None)
            if callable(get_session):
                try:
                    return session_id, get_session(session_id)
                except Exception:
                    return session_id, None
        if repository is not None:
            resolve_session_target = getattr(repository, "resolve_session_target", None)
            if callable(resolve_session_target):
                try:
                    session = resolve_session_target(str(getattr(record, "target", "") or ""))
                except Exception:
                    session = None
                resolved_session_id = str(getattr(session, "session_id", "") or "").strip()
                if resolved_session_id:
                    return resolved_session_id, session
        return session_id or None, None

    @staticmethod
    def _session_repository_for_worker(worker: Any) -> Any | None:
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        return getattr(runtime, "session_repository", None)

    def _config_for_libvirt_record(
        self,
        *,
        worker: Any,
        session_id: str,
        session: Any | None,
    ) -> Any | None:
        for resolver in self._provider_config_resolvers_for_worker(worker):
            resolve_for_session_id = getattr(resolver, "resolve_for_session_id", None)
            if callable(resolve_for_session_id):
                try:
                    return resolve_for_session_id(session_id)
                except Exception:
                    pass
            directory_path = getattr(session, "directory_path", None)
            resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
            if directory_path and callable(resolve_for_directory):
                try:
                    return resolve_for_directory(directory_path)
                except Exception:
                    pass
        return getattr(worker, "config", None)

    @staticmethod
    def _provider_config_resolvers_for_worker(worker: Any) -> tuple[Any, ...]:
        resolvers: list[Any] = []
        direct_resolver = getattr(worker, "provider_config_resolver", None)
        if direct_resolver is not None:
            resolvers.append(direct_resolver)
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        runtime_resolver = getattr(getattr(admin_service, "runtime", None), "provider_config", None)
        if runtime_resolver is not None and runtime_resolver is not direct_resolver:
            resolvers.append(runtime_resolver)
        return tuple(resolvers)


def build_effect_lifecycle_hooks(*, provider_factory: Any) -> tuple[EffectLifecycleHookBase, ...]:
    hooks: list[EffectLifecycleHookBase] = []

    for effect_type in provider_factory.registry.registered_types():
        provider_class = provider_factory.registry.resolve(effect_type)
        hooks.append(ProviderCompletionLifecycleHook(provider_class))
        for hook_class in provider_class.lifecycle_hooks():
            hooks.append(hook_class())
    return tuple(hooks)
