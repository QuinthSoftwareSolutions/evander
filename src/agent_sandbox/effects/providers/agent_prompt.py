from __future__ import annotations

from collections.abc import Mapping
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_sandbox.config import GatewayConfig
from agent_sandbox.execution import (
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
    build_execution_backend,
)
from agent_sandbox.events.models import NODE_OUTPUTS_CONTEXT_KEY
from agent_sandbox.events.provider_models import AgentPromptEffectDefinition
from agent_sandbox.model_usage.runtime import (
    clear_model_usage_correlation_context,
    register_model_usage_correlation_context,
    resolve_runtime_dir,
)
from agent_sandbox.triggers.queue import TriggerQueueRecord
from agent_sandbox.orchestration.effect_extensions import list_vm_summaries_for_agent_reconciliation
from agent_sandbox.workflow.migrations import (
    AGENT_ENQUEUE_PROMPT_EFFECT_TYPE,
    AGENT_PROMPT_COMPLETED_PORT,
    AGENT_PROMPT_ERROR_PORT,
    AGENT_PROMPT_STARTED_PORT,
)
from agent_sandbox.workflow.validation import NodeCapability
from agent_sandbox.worker.security import sanitize_text

from .agent_session import (
    container_backend,
    container_prompt_command,
    container_thread_id_from_logs,
    container_worker_guest_gateway_url,
    container_worker_image_for_session,
    default_agent_model_for_provider,
    default_agent_provider_for_config,
    effective_config_for_session,
    gateway_url_with_session_context,
    require_container_session_id,
    resolve_active_session_if_available,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
)
from .base import EffectProviderBase, EffectProviderExecutionError
from .resume_thread_metadata import (
    normalize_resume_thread_metadata_collection,
    resume_thread_id_from_metadata,
    resume_thread_metadata_key as normalize_resume_thread_metadata_key,
    resume_thread_metadata_patch,
)

logger = logging.getLogger(__name__)

_AWAIT_AGENT_STALE_GRACE_SECONDS = 120
_DEFAULT_CONTAINER_WORKER_JOB_TIMEOUT_SECONDS = 1800.0
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"
CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY = "interrupt_in_progress"


def _prompt_error_outputs(
    message: str,
    *,
    error_class: str,
    failed_phase: str,
    dispatch_id: str | None = None,
    session_id: str | None = None,
    vm_name: str | None = None,
    queue_key: str | None = None,
    queued: bool | None = None,
    coalesced_count: int | None = None,
    retry_count: int | None = None,
    max_retries: int | None = None,
    execution_backend: str | None = None,
) -> dict[str, Any]:
    outputs: dict[str, Any] = {
        "success": False,
        "error_message": sanitize_text(str(message or error_class)),
        "error_class": sanitize_text(str(error_class or "RuntimeError")),
        "failed_phase": sanitize_text(str(failed_phase or "dispatch")),
    }
    for key, value in (
        ("dispatch_id", dispatch_id),
        ("session_id", session_id),
        ("vm_name", vm_name),
        ("queue_key", queue_key),
        ("execution_backend", execution_backend),
    ):
        text = str(value or "").strip()
        if text:
            outputs[key] = sanitize_text(text)
    if queued is not None:
        outputs["queued"] = bool(queued)
    if coalesced_count is not None:
        outputs["coalesced_count"] = int(coalesced_count)
    if retry_count is not None:
        outputs["retry_count"] = int(retry_count)
    if max_retries is not None:
        outputs["max_retries"] = int(max_retries)
    return outputs


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


class AgentPromptEffectProvider(EffectProviderBase):
    capability = NodeCapability(
        min_inbound=1,
        max_inbound=1,
        allowed_source_ports=frozenset(
            {
                AGENT_PROMPT_ERROR_PORT,
                AGENT_PROMPT_STARTED_PORT,
                AGENT_PROMPT_COMPLETED_PORT,
            }
        ),
    )

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
            "mcp_tool_approval_lease_id": effect.mcp_tool_approval_lease_id,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        prompt = self._require_string(payload.get("prompt"), field_name="prompt")
        target = self._resolve_target(payload)
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        model = self._normalize_optional_string(payload.get("model"))

        dispatch_id = self._make_dispatch_id(effect_id=effect.id)
        resume_thread_id = self._normalize_optional_string(payload.get("resume_thread_id"))
        resume_thread_metadata_key = self._normalize_optional_string(
            payload.get("resume_thread_metadata_key")
        )
        resume_thread_metadata_collection = normalize_resume_thread_metadata_collection(
            payload.get("resume_thread_metadata_collection")
        )
        mcp_tool_approval_lease_id = self._resolve_mcp_tool_approval_lease_id(
            payload=payload,
            execution_context=execution_context,
        )
        workflow_run_id = self._workflow_run_id(execution_context)

        execution_backend, config = self._resolve_execution_backend(payload, target=target)
        if execution_backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            return self._execute_container_prompt(
                payload=payload,
                prompt=prompt,
                model=model,
                dispatch_id=dispatch_id,
                resume_thread_id=resume_thread_id,
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                config=config,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                execution_context=execution_context,
            )
        if execution_backend != ExecutionBackendKind.LIBVIRT_VM.value:
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")

        active_session_id, active_session = resolve_active_session_if_available(
            context,
            target,
            config=config,
        )
        session_id = active_session_id or self._resolve_session_id_if_available(target)
        if context.agent_resumer is None:
            message = "effect:agent:prompt agent_resumer is not available"
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class="RuntimeError",
                    failed_phase="runtime_services",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    vm_name=self._normalize_optional_string(payload.get("vm_name")),
                ),
            )
        effective_resume_thread_id = self._effective_resume_thread_id(
            resume_thread_id=resume_thread_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            target=target,
            session_id=session_id,
            session=active_session,
        )
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
            result_message = (
                self._normalize_optional_string(getattr(result, "message", None)) or "<empty>"
            )
            message = (
                "effect:agent:prompt resume_agent failed "
                f"(target={target}, dispatch_id={dispatch_id}, message={result_message})"
            )
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class="RuntimeError",
                    failed_phase="dispatch",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    vm_name=self._normalize_optional_string(payload.get("vm_name")),
                ),
            )
        thread_id = self._normalize_optional_string(getattr(result, "thread_id", None))
        if not thread_id:
            message = (
                "effect:agent:prompt did not receive a thread_id from resume_agent "
                "(success=True, message=<empty-or-missing-thread-id>)"
            )
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class="RuntimeError",
                    failed_phase="response_validation",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    vm_name=self._normalize_optional_string(payload.get("vm_name")),
                ),
            )
        session = active_session or self._session_for_metadata_lookup(
            target=target, session_id=session_id
        )
        directory_path = self._normalize_optional_string(getattr(session, "directory_path", None))
        if directory_path is None:
            directory_path = str(self._context().metadata.get("directory_path") or "root")
        runtime_dir = resolve_runtime_dir(self._context())
        if runtime_dir is not None:
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=workflow_run_id,
                effect_id=effect.id,
                execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
                directory_path=directory_path,
                model=model,
                trace_id=self._trace_id(execution_context),
                agent_harness=self._agent_harness_from_session(session),
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
        }

    def _execute_container_prompt(
        self,
        *,
        payload: dict[str, Any],
        prompt: str,
        model: str | None,
        dispatch_id: str,
        resume_thread_id: str | None,
        resume_thread_metadata_key: str | None,
        resume_thread_metadata_collection: str,
        config: GatewayConfig,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
        execution_context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        context = self._context()
        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:prompt",
            config=config,
        )
        effective_config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=session.directory_path if session is not None else None,
        )
        agent_harness = self._agent_harness_from_session(session, config=effective_config)
        resolved_model = (
            model
            or getattr(session, "model", None)
            or default_agent_model_for_provider(agent_harness, effective_config)
        )
        effective_resume_thread_id = self._effective_resume_thread_id(
            resume_thread_id=resume_thread_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            target=self._normalize_optional_string(payload.get("target")) or session_id,
            session_id=session_id,
            session=session,
        )
        backend = container_backend(context, effective_config)
        execution_id = safe_effect_execution_id("agent", session_id)
        command = self._container_prompt_command(
            prompt=prompt,
            model=resolved_model,
            resume_thread_id=effective_resume_thread_id,
            agent_provider=agent_harness,
            mcp_gateway_url=self._mcp_gateway_url(
                config=effective_config,
                target=session_id,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                base_gateway_url=container_worker_guest_gateway_url(effective_config),
            ),
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        try:
            handle = backend.start(
                backend.prepare(
                    ExecutionSpec(
                        backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                        target_name=execution_id,
                        session_id=session_id,
                        directory_path=session.directory_path if session is not None else "root",
                        image=container_worker_image_for_session(session, effective_config),
                        command=command,
                        metadata={
                            "effect_type": self.node_type(),
                            "dispatch_id": dispatch_id,
                            "resume_thread_id": effective_resume_thread_id,
                            "agent_provider": agent_harness,
                            "model": resolved_model,
                            "model_provider_mode": effective_config.model_provider_mode,
                        },
                    )
                )
            )
        except Exception as exc:
            detail = self._normalize_optional_string(exc) or type(exc).__name__
            message = (
                "effect:agent:prompt container dispatch failed "
                f"(session_id={session_id}, dispatch_id={dispatch_id}, message={detail})"
            )
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class=type(exc).__name__,
                    failed_phase="container_dispatch",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                ),
            ) from exc
        runtime_dir = resolve_runtime_dir(context)
        if runtime_dir is not None:
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=workflow_run_id,
                effect_id=self._effect().id,
                execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                directory_path=str(getattr(session, "directory_path", None) or "root"),
                model=resolved_model,
                trace_id=self._trace_id(execution_context),
                agent_harness=agent_harness,
            )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": effective_resume_thread_id or dispatch_id,
            "success": True,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "execution_id": handle.execution_id,
            "session_id": session_id,
        }

    @classmethod
    def on_execution_started(cls, **kwargs: Any) -> bool:
        record = kwargs.get("record")
        outputs = kwargs.get("outputs")
        if record is None or not isinstance(outputs, Mapping):
            return False
        if outputs.get("success") is False:
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
        if worker is None or not isinstance(queue_record, TriggerQueueRecord):
            return 0
        dispatch_id = str(queue_record.payload.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return 0

        finalized = 0
        for record in _waiting_execution_records(worker, cls.node_type()):
            record_dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
            if record_dispatch_id != dispatch_id:
                continue
            cls._finalize_waiting_record(worker=worker, record=record, queue_record=queue_record)
            finalized += 1
        return finalized

    @classmethod
    def reconcile_waiting(cls, **kwargs: Any) -> int:
        worker = kwargs.get("worker")
        if worker is None:
            return 0
        waiting_records = _waiting_execution_records(worker, cls.node_type())
        if not waiting_records:
            return 0

        completed = 0
        vm_records: list[Any] = []
        for record in waiting_records:
            if cls._is_container_waiting_record(record):
                completed += cls._reconcile_container_waiting_record(worker=worker, record=record)
                continue
            vm_records.append(record)
        if not vm_records:
            return completed
        try:
            summaries = list_vm_summaries_for_agent_reconciliation(worker)
        except Exception:
            logger.exception(
                "AgentPromptEffectProvider inventory reconciliation skipped "
                "phase=waiting_reconciliation waiting_vm_records=%d",
                len(vm_records),
            )
            return completed
        for record in vm_records:
            queue_record = cls._build_completion_queue_record_for_waiting_record(
                record=record, summaries=summaries
            )
            if queue_record is None:
                continue
            cls._finalize_waiting_record(worker=worker, record=record, queue_record=queue_record)
            completed += 1
        return completed

    @classmethod
    def _is_container_waiting_record(cls, record: Any) -> bool:
        return (
            str(record.outputs.get("execution_backend") or "").strip()
            == ExecutionBackendKind.CONTAINER_WORKER_VM.value
        )

    @classmethod
    def _reconcile_container_waiting_record(cls, *, worker: Any, record: Any) -> int:
        if bool(record.outputs.get(CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY)):
            return 0
        execution_id = str(record.outputs.get("execution_id") or "").strip()
        session_id = str(record.outputs.get("session_id") or "").strip()
        if not execution_id or not session_id:
            return 0
        backend = cls._container_backend_for_worker(worker=worker, session_id=session_id)
        if backend is None:
            return 0
        try:
            status = backend.inspect(execution_id)
        except Exception:
            return 0
        if status.status not in {
            ExecutionStatusKind.STOPPED,
            ExecutionStatusKind.FAILED,
            ExecutionStatusKind.DESTROYED,
        }:
            timeout_result = cls._timeout_container_waiting_record_if_expired(
                worker=worker,
                record=record,
                backend=backend,
                status=status,
                session_id=session_id,
                execution_id=execution_id,
            )
            if timeout_result:
                return timeout_result
            cls._capture_container_thread_id_from_running_logs(
                worker=worker,
                record=record,
                backend=backend,
                session_id=session_id,
                execution_id=execution_id,
            )
            return 0
        logs = ""
        try:
            logs = backend.get_log_snapshot(execution_id, lines=400)
        except Exception:
            logs = ""
        thread_id = cls._thread_id_from_logs(logs)
        if thread_id:
            cls._store_container_thread_id(
                worker=worker,
                record=record,
                session_id=session_id,
                thread_id=thread_id,
            )
        if status.status != ExecutionStatusKind.DESTROYED:
            try:
                backend.destroy(execution_id)
            except Exception:
                pass
        queue_record = cls._build_container_completion_queue_record(
            record=record,
            session_id=session_id,
            execution_id=execution_id,
            worker_status=status.status.value,
            success=status.status == ExecutionStatusKind.STOPPED,
            message=status.message,
            logs=logs,
        )
        cls._finalize_waiting_record(worker=worker, record=record, queue_record=queue_record)
        return 1

    @classmethod
    def _timeout_container_waiting_record_if_expired(
        cls,
        *,
        worker: Any,
        record: Any,
        backend: Any,
        status: Any,
        session_id: str,
        execution_id: str,
    ) -> int:
        timeout_seconds = cls._container_job_timeout_seconds(backend)
        started_at = (
            cls._container_status_started_at(status)
            or record.finished_at
            or record.started_at
        )
        elapsed_seconds = cls._elapsed_seconds_since(started_at)
        if elapsed_seconds is None or elapsed_seconds <= timeout_seconds:
            return 0

        logs = ""
        try:
            logs = backend.get_log_snapshot(execution_id, lines=400)
        except Exception:
            logs = ""
        thread_id = cls._thread_id_from_logs(logs)
        if thread_id:
            cls._store_container_thread_id(
                worker=worker,
                record=record,
                session_id=session_id,
                thread_id=thread_id,
            )

        stop_error = cls._stop_and_destroy_container_job(backend=backend, execution_id=execution_id)
        message = cls._container_timeout_message(
            execution_id=execution_id,
            elapsed_seconds=elapsed_seconds,
            timeout_seconds=timeout_seconds,
            logs=logs,
            stop_error=stop_error,
        )
        cls._fail_waiting_record_without_completion_event(
            worker=worker,
            record=record,
            outputs={
                "dispatch_id": str(record.outputs.get("dispatch_id") or "").strip(),
                "thread_id": thread_id or str(record.outputs.get("thread_id") or ""),
                "session_id": session_id,
                "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "worker_execution_id": execution_id,
                "worker_status": "timeout",
                "success": False,
                "message": message,
                "logs": logs,
                "agent_state": "failed",
                "scope": {
                    "directory_path": str(record.resolved_config.get("directory_path") or "root"),
                    "session_id": session_id,
                    "vm_name": None,
                    "vm_user": str(record.resolved_config.get("vm_user") or "agent"),
                },
            },
            error=message,
        )
        return 1

    @staticmethod
    def _container_job_timeout_seconds(backend: Any) -> float:
        config = getattr(backend, "config", None)
        value = getattr(config, "container_worker_job_timeout_seconds", None)
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            timeout = _DEFAULT_CONTAINER_WORKER_JOB_TIMEOUT_SECONDS
        return timeout if timeout > 0 else _DEFAULT_CONTAINER_WORKER_JOB_TIMEOUT_SECONDS

    @staticmethod
    def _container_status_started_at(status: Any) -> datetime | None:
        value = getattr(status, "started_at", None)
        return value if isinstance(value, datetime) else None

    @staticmethod
    def _elapsed_seconds_since(started_at: datetime | None) -> float | None:
        if started_at is None:
            return None
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        else:
            started_at = started_at.astimezone(UTC)
        return max(0.0, (datetime.now(UTC) - started_at).total_seconds())

    @staticmethod
    def _stop_and_destroy_container_job(*, backend: Any, execution_id: str) -> str | None:
        stop_error: str | None = None
        try:
            backend.stop(execution_id, timeout_seconds=30)
        except Exception as exc:  # noqa: BLE001
            stop_error = str(exc)
        try:
            backend.destroy(execution_id)
        except Exception as exc:  # noqa: BLE001
            destroy_error = str(exc)
            stop_error = (
                f"{stop_error}; destroy failed: {destroy_error}"
                if stop_error
                else destroy_error
            )
        return stop_error

    @classmethod
    def _container_timeout_message(
        cls,
        *,
        execution_id: str,
        elapsed_seconds: float,
        timeout_seconds: float,
        logs: str,
        stop_error: str | None,
    ) -> str:
        lines = [
            "container worker prompt job timed out "
            f"(execution_id={execution_id}, elapsed_seconds={elapsed_seconds:.1f}, "
            f"timeout_seconds={timeout_seconds:.1f})"
        ]
        if stop_error:
            lines.append(f"stop/remove error: {stop_error}")
        last_lines = cls._last_useful_log_lines(logs, max_lines=20)
        if last_lines:
            lines.append("last log lines:")
            lines.append(last_lines)
        else:
            lines.append("last log lines: <none>")
        return "\n".join(lines)

    @staticmethod
    def _last_useful_log_lines(logs: str, *, max_lines: int) -> str:
        useful = [line.rstrip() for line in logs.splitlines() if line.strip()]
        return "\n".join(useful[-max(1, max_lines) :])

    @classmethod
    def _fail_waiting_record_without_completion_event(
        cls,
        *,
        worker: Any,
        record: Any,
        outputs: dict[str, Any],
        error: str,
    ) -> None:
        finished_at = datetime.now(UTC)
        record.status = "failed"
        record.outputs = {**record.outputs, **outputs}
        record.error = error
        record.published_event_id = None
        record.completion_event_enqueued_at = None
        record.finished_at = finished_at
        worker.execution_store.put(record)
        worker._update_event_delivery_state(record.event_id)

    @classmethod
    def _capture_container_thread_id_from_running_logs(
        cls,
        *,
        worker: Any,
        record: Any,
        backend: Any,
        session_id: str,
        execution_id: str,
    ) -> None:
        current_thread_id = str(record.outputs.get("thread_id") or "").strip()
        if current_thread_id and not current_thread_id.startswith("dispatch-"):
            return
        try:
            logs = backend.get_log_snapshot(execution_id, lines=80)
        except Exception:
            return
        thread_id = cls._thread_id_from_logs(logs)
        if not thread_id:
            return
        cls._store_container_thread_id(
            worker=worker,
            record=record,
            session_id=session_id,
            thread_id=thread_id,
        )

    @classmethod
    def _store_container_thread_id(
        cls,
        *,
        worker: Any,
        record: Any,
        session_id: str,
        thread_id: str,
    ) -> None:
        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id or normalized_thread_id.startswith("dispatch-"):
            return
        current_thread_id = str(record.outputs.get("thread_id") or "").strip()
        if current_thread_id != normalized_thread_id:
            record.outputs = {**record.outputs, "thread_id": normalized_thread_id}
            store = getattr(worker, "execution_store", None)
            put = getattr(store, "put", None)
            record_execution = getattr(store, "record_execution", None)
            try:
                if callable(put):
                    put(record)
                elif callable(record_execution):
                    record_execution(record)
            except Exception:
                pass
        cls._persist_container_session_thread_id(
            worker=worker,
            record=record,
            session_id=session_id,
            thread_id=normalized_thread_id,
        )

    @classmethod
    def _persist_container_session_thread_id(
        cls,
        *,
        worker: Any,
        record: Any,
        session_id: str,
        thread_id: str,
    ) -> None:
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        cls._persist_chat_assistant_mapping_thread_id(
            admin_service=admin_service,
            session_id=session_id,
            thread_id=thread_id,
        )
        resume_thread_metadata_key = cls._resume_thread_metadata_key_from_record(record)
        resume_thread_metadata_collection = (
            cls._resume_thread_metadata_collection_from_record(record)
        )
        metadata_patch = resume_thread_metadata_patch(
            collection=resume_thread_metadata_collection,
            key=resume_thread_metadata_key,
            thread_id=thread_id,
        )
        if not metadata_patch:
            return
        update_fn = getattr(admin_service, "session_update_metadata", None)
        if callable(update_fn):
            try:
                update_fn(target=session_id, metadata=metadata_patch)
                return
            except Exception:
                pass

        repository = getattr(getattr(admin_service, "runtime", None), "session_repository", None)
        get_session = getattr(repository, "get_session", None)
        upsert_session = getattr(repository, "upsert_session", None)
        if not callable(get_session) or not callable(upsert_session):
            return
        try:
            manifest = get_session(session_id)
            if manifest is None:
                return
            metadata = getattr(manifest, "metadata", {})
            if not isinstance(metadata, Mapping):
                metadata = {}
            merged_metadata = cls._deep_merge_metadata(dict(metadata), metadata_patch)
            updated = manifest.model_copy(update={"metadata": merged_metadata})
            upsert_session(updated)
        except Exception:
            pass

    @staticmethod
    def _resume_thread_metadata_key_from_record(record: Any) -> str | None:
        for source_name in ("resolved_config", "input_payload", "outputs"):
            source = getattr(record, source_name, None)
            if not isinstance(source, Mapping):
                continue
            text = str(source.get("resume_thread_metadata_key") or "").strip()
            if text:
                return text
        return None

    @staticmethod
    def _resume_thread_metadata_collection_from_record(record: Any) -> str:
        for source_name in ("resolved_config", "input_payload", "outputs"):
            source = getattr(record, source_name, None)
            if not isinstance(source, Mapping):
                continue
            text = str(source.get("resume_thread_metadata_collection") or "").strip()
            if text:
                return text
        return normalize_resume_thread_metadata_collection(None)

    @classmethod
    def _deep_merge_metadata(
        cls, base: Mapping[str, Any], update: Mapping[str, Any]
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in update.items():
            existing = merged.get(key)
            if isinstance(existing, Mapping) and isinstance(value, Mapping):
                merged[key] = cls._deep_merge_metadata(existing, value)
                continue
            merged[key] = value
        return merged

    @classmethod
    def _persist_chat_assistant_mapping_thread_id(
        cls,
        *,
        admin_service: Any,
        session_id: str,
        thread_id: str,
    ) -> None:
        vm_agent_admin = getattr(admin_service, "vm_agent_admin", None)
        persist = getattr(vm_agent_admin, "persist_chat_assistant_agent_thread_id", None)
        if not callable(persist):
            return
        try:
            persist(
                session_id=session_id,
                agent_thread_id=thread_id,
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                assistant_scope="admin_chat",
            )
        except Exception:
            pass

    @classmethod
    def _container_backend_for_worker(cls, *, worker: Any, session_id: str) -> Any | None:
        config: GatewayConfig | None = None
        resolver = getattr(worker, "provider_config_resolver", None)
        resolve_for_session_id = getattr(resolver, "resolve_for_session_id", None)
        if callable(resolve_for_session_id):
            try:
                config = resolve_for_session_id(session_id)
            except Exception:
                config = None
        if config is None:
            config = getattr(worker, "config", None)
        if config is None:
            return None
        try:
            admin_service = getattr(
                getattr(worker, "provider_factory", None), "admin_service", None
            )
            return build_execution_backend(
                config.model_copy(
                    update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
                ),
                admin_service=admin_service,
            )
        except Exception:
            return None

    @classmethod
    def _build_container_completion_queue_record(
        cls,
        *,
        record: Any,
        session_id: str,
        execution_id: str,
        worker_status: str,
        success: bool,
        message: str | None,
        logs: str,
    ) -> TriggerQueueRecord:
        finished_at = datetime.now(UTC)
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        wake_dispatch_id = str(record.outputs.get("wake_dispatch_id") or "").strip()
        thread_id = cls._thread_id_from_logs(logs) or str(record.outputs.get("thread_id") or "")
        payload: dict[str, object] = {
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "worker_execution_id": execution_id,
            "worker_status": worker_status,
            "success": success,
            "message": message or f"container worker job status={worker_status}",
            "agent_state": "idle" if success else "failed",
            "scope": {
                "directory_path": str(record.resolved_config.get("directory_path") or "root"),
                "session_id": session_id,
                "vm_name": None,
                "vm_user": str(record.resolved_config.get("vm_user") or "agent"),
            },
        }
        if wake_dispatch_id:
            payload["wake_dispatch_id"] = wake_dispatch_id
        return TriggerQueueRecord(
            event_id=f"__await-agent-completion__:{dispatch_id}:{finished_at.isoformat()}",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            trace_id=record.trace_id,
            occurred_at=finished_at,
            payload=payload,
            delivery_state="delivered",
            delivered_at=finished_at,
            idempotency_key=f"__await-agent-completion__:{dispatch_id}",
        )

    @classmethod
    def node_type(cls) -> str:
        return "effect:agent:prompt"

    @classmethod
    def _finalize_waiting_record(
        cls, *, worker: Any, record: Any, queue_record: TriggerQueueRecord
    ) -> None:
        finished_at = datetime.now(UTC)
        merged_outputs = {
            **record.outputs,
            **{
                key: value
                for key, value in queue_record.payload.items()
                if key
                not in {
                    NODE_OUTPUTS_CONTEXT_KEY,
                    _BRANCH_PORT_PAYLOAD_KEY,
                    _FOR_EXECUTION_IDS_PAYLOAD_KEY,
                }
            },
        }
        branch_port = (
            AGENT_PROMPT_COMPLETED_PORT
            if record.effect_type in {cls.node_type(), AGENT_ENQUEUE_PROMPT_EFFECT_TYPE}
            else None
        )
        published_event_id = worker._publish_completion_event_from_record(
            record=record,
            outputs=merged_outputs,
            branch_port=branch_port,
        )
        record.status = "succeeded"
        record.outputs = merged_outputs
        record.published_event_id = published_event_id
        record.completion_event_enqueued_at = finished_at
        record.finished_at = finished_at
        worker.execution_store.put(record)
        worker._update_event_delivery_state(record.event_id)
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        runtime_dir = getattr(getattr(worker, "config", None), "runtime_dir", None)
        if runtime_dir is not None and dispatch_id:
            clear_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                dispatch_id=dispatch_id,
            )
        wake_dispatch_id = str(queue_record.payload.get("wake_dispatch_id") or "").strip()
        if wake_dispatch_id:
            wake_store = getattr(getattr(worker, "runtime", None), "wake_store", None)
            mark_completed = getattr(wake_store, "mark_completed_by_dispatch_id", None)
            if callable(mark_completed):
                scope = queue_record.payload.get("scope")
                if not isinstance(scope, Mapping):
                    scope = {}
                completed_at = finished_at
                evidence = {
                    "trigger_event_id": queue_record.event_id,
                    "trigger_type": queue_record.trigger_type,
                    "dispatch_id": wake_dispatch_id,
                    "session_id": queue_record.payload.get("session_id") or scope.get(
                        "session_id"
                    ),
                    "directory_path": scope.get("directory_path"),
                    "vm_user": scope.get("vm_user"),
                    "agent_state": queue_record.payload.get("agent_state"),
                    "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
                }
                try:
                    mark_completed(
                        wake_dispatch_id,
                        completed_at=completed_at,
                        evidence={key: value for key, value in evidence.items() if value is not None},
                    )
                except Exception:
                    logger.warning(
                        "Failed to mark queued container wake completed dispatch_id=%s event_id=%s",
                        wake_dispatch_id,
                        queue_record.event_id,
                        exc_info=True,
                    )

    @classmethod
    def _build_completion_queue_record_for_waiting_record(
        cls,
        *,
        record: Any,
        summaries: list[Any],
    ) -> TriggerQueueRecord | None:
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return None
        session_id, vm_name = cls._waiting_target(record)
        summary = cls._match_reconciliation_summary(
            summaries=summaries,
            dispatch_id=dispatch_id,
            session_id=session_id,
            vm_name=vm_name,
            record=record,
        )
        if summary is None:
            return None
        if str(getattr(summary, "agent_state", "") or "").strip() == "running":
            return None

        from agent_sandbox.daemons.agent_completion_watcher import build_agent_run_completed_event

        completion_event = build_agent_run_completed_event(
            trigger_id="__await-agent-completion__",
            dispatch_id=dispatch_id,
            vm_user=str(record.resolved_config.get("vm_user") or "agent"),
            summary=summary,
            session_id=session_id,
            directory_path=(getattr(summary, "directory_paths", None) or [None])[0],
        )
        return TriggerQueueRecord(
            event_id=completion_event.event_id,
            trigger_id=completion_event.trigger_id,
            trigger_type=completion_event.trigger_type,
            trace_id=completion_event.trace_id or record.trace_id,
            occurred_at=completion_event.occurred_at,
            payload=completion_event.payload,
            delivery_state="delivered",
            delivered_at=datetime.now(UTC),
            idempotency_key=f"__await-agent-completion__:{dispatch_id}",
        )

    @staticmethod
    def _waiting_target(record: Any) -> tuple[str | None, str | None]:
        session_id = str(record.resolved_config.get("session_id") or "").strip() or None
        vm_name = str(record.resolved_config.get("vm_name") or "").strip() or None
        target = str(record.resolved_config.get("target") or "").strip() or None
        if target and vm_name is None and target.startswith("vm-"):
            vm_name = target
        if target and session_id is None and not target.startswith("vm-"):
            session_id = target
        return session_id, vm_name

    @staticmethod
    def _match_reconciliation_summary(
        *,
        summaries: list[Any],
        dispatch_id: str,
        session_id: str | None,
        vm_name: str | None,
        record: Any,
    ) -> Any | None:
        exact_match = next(
            (
                summary
                for summary in summaries
                if str(getattr(summary, "dispatch_id", "") or "").strip() == dispatch_id
                and (
                    vm_name is None or str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                )
                and (
                    session_id is None
                    or session_id
                    in {
                        str(item or "").strip()
                        for item in (getattr(summary, "session_ids", []) or [])
                    }
                )
            ),
            None,
        )
        if exact_match is not None:
            return exact_match

        if (
            datetime.now(UTC) - record.finished_at
        ).total_seconds() < _AWAIT_AGENT_STALE_GRACE_SECONDS:
            return None

        if vm_name is not None:
            vm_match = next(
                (
                    summary
                    for summary in summaries
                    if str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                    and not str(getattr(summary, "dispatch_id", "") or "").strip()
                    and str(getattr(summary, "agent_state", "") or "").strip() != "running"
                ),
                None,
            )
            if vm_match is not None:
                return vm_match

        if session_id is None:
            return None

        return next(
            (
                summary
                for summary in summaries
                if session_id
                in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
                and not str(getattr(summary, "dispatch_id", "") or "").strip()
                and str(getattr(summary, "agent_state", "") or "").strip() != "running"
            ),
            None,
        )

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

    def _resolve_execution_backend(
        self,
        payload: dict[str, Any],
        *,
        target: str,
    ) -> tuple[str, GatewayConfig]:
        context = self._context()
        payload_for_resolution = dict(payload)
        payload_for_resolution.setdefault("target", target)
        return selected_execution_backend_for_target(
            context,
            payload_for_resolution,
            directory_path=str(context.metadata.get("directory_path") or "root"),
        )

    def _mcp_gateway_url(
        self,
        *,
        config: GatewayConfig,
        target: str,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
        base_gateway_url: str | None = None,
    ) -> str | None:
        if not mcp_tool_approval_lease_id:
            return None
        session_id = self._session_id_from_target(target)
        return gateway_url_with_session_context(
            base_gateway_url or config.gateway_url,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )

    def _session_id_from_target(self, target: str) -> str:
        sessions = self._read_all_sessions_safe()
        if target in sessions:
            return target
        matches = [
            session_id
            for session_id, session in sessions.items()
            if self._normalize_optional_string(getattr(session, "vm_id", None)) == target
        ]
        if len(matches) == 1:
            return matches[0]
        return target

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

    def _trace_id(self, execution_context: Mapping[str, Any] | None) -> str | None:
        if not isinstance(execution_context, Mapping):
            return None
        for key in ("trace_id", "workflow_trace_id"):
            text = self._normalize_optional_string(execution_context.get(key))
            if text:
                return text
        return None

    def _read_all_sessions_safe(self) -> Mapping[str, Any]:
        context = self._context()
        if context.session_lookup is None:
            return {}
        try:
            return context.session_lookup.read_all_sessions()
        except Exception:
            return {}

    def _resolve_session_id_if_available(self, target: str) -> str | None:
        sessions = self._read_all_sessions_safe()
        if target in sessions:
            return target
        try:
            from agent_sandbox.admin.service import _resolve_session_target

            session_id, _session = _resolve_session_target(target, sessions)
        except Exception:
            return None
        return session_id

    def _session_for_metadata_lookup(self, *, target: str, session_id: str | None) -> Any | None:
        sessions = self._read_all_sessions_safe()
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
        return self._usable_resume_thread_id(
            resume_thread_id_from_metadata(
                metadata,
                collection=resume_thread_metadata_collection,
                key=key,
            )
        )

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

    def _effect(self) -> AgentPromptEffectDefinition:
        if not isinstance(self.effect_definition, AgentPromptEffectDefinition):
            raise RuntimeError(
                "AgentPromptEffectProvider initialized with a non-agent-prompt effect definition"
            )
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
    def _usable_resume_thread_id(cls, value: Any) -> str | None:
        text = cls._normalize_optional_string(value)
        if not text:
            return None
        if text.startswith("dispatch-") or text.startswith("chat-thread-"):
            return None
        return text

    @classmethod
    def _require_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    @staticmethod
    def _make_dispatch_id(*, effect_id: str) -> str:
        return f"dispatch-{effect_id}-{uuid4().hex[:12]}"

    @staticmethod
    def _container_prompt_command(
        *,
        prompt: str,
        model: str,
        resume_thread_id: str | None,
        agent_provider: str | None = None,
        resume_mode: str | None = None,
        mcp_gateway_url: str | None = None,
        web_search_enabled: bool = False,
    ) -> str:
        return container_prompt_command(
            prompt=prompt,
            model=model,
            resume_thread_id=resume_thread_id,
            agent_provider=agent_provider,
            resume_mode=resume_mode,
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=web_search_enabled,
        )

    @staticmethod
    def _agent_harness_from_session(session: Any, *, config: GatewayConfig | None = None) -> str:
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if isinstance(metadata, Mapping):
            candidate = str(
                metadata.get("agent_provider") or metadata.get("agent_harness") or ""
            ).strip()
            if candidate:
                return candidate
        return default_agent_provider_for_config(config)

    @classmethod
    def _thread_id_from_logs(cls, logs: str) -> str | None:
        return container_thread_id_from_logs(logs)
