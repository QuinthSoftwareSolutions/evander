from __future__ import annotations

import re
from collections.abc import Mapping
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
from agent_sandbox.triggers.queue import TriggerQueueRecord
from agent_sandbox.orchestration.effect_extensions import list_vm_summaries_for_agent_reconciliation

from .agent_session import (
    container_backend,
    container_prompt_command,
    effective_config_for_session,
    require_container_session_id,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
)
from .base import EffectProviderBase

_AWAIT_AGENT_STALE_GRACE_SECONDS = 120
_CONTAINER_THREAD_ID_LOG_PATTERN = re.compile(r"^session id:\s*(?P<thread_id>\S+)\s*$", re.M)
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class AgentPromptEffectProvider(EffectProviderBase):
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

        execution_backend, config = self._resolve_execution_backend(payload, target=target)
        if execution_backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            return self._execute_container_prompt(
                payload=payload,
                prompt=prompt,
                model=model,
                dispatch_id=dispatch_id,
                resume_thread_id=resume_thread_id,
                config=config,
            )
        if execution_backend != ExecutionBackendKind.LIBVIRT_VM.value:
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")

        if context.agent_resumer is None:
            raise RuntimeError("effect:agent:prompt agent_resumer is not available")
        result = context.agent_resumer.resume_agent(
            target=target,
            prompt=prompt,
            model=model,
            vm_user=vm_user,
            dispatch_id=dispatch_id,
            resume_thread_id=resume_thread_id,
        )
        if not bool(getattr(result, "success", False)):
            result_message = (
                self._normalize_optional_string(getattr(result, "message", None)) or "<empty>"
            )
            raise RuntimeError(
                "effect:agent:prompt resume_agent failed "
                f"(target={target}, dispatch_id={dispatch_id}, message={result_message})"
            )
        thread_id = self._normalize_optional_string(getattr(result, "thread_id", None))
        if not thread_id:
            raise RuntimeError(
                "effect:agent:prompt did not receive a thread_id from resume_agent "
                "(success=True, message=<empty-or-missing-thread-id>)"
            )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "success": bool(result.success),
            "output_mode": output_mode,
        }

    def _execute_container_prompt(
        self,
        *,
        payload: dict[str, Any],
        prompt: str,
        model: str | None,
        dispatch_id: str,
        resume_thread_id: str | None,
        config: GatewayConfig,
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
        resolved_model = model or getattr(session, "model", None) or "gpt-5.4"
        backend = container_backend(context, effective_config)
        execution_id = safe_effect_execution_id("agent", session_id)
        command = self._container_prompt_command(
            prompt=prompt,
            model=resolved_model,
            resume_thread_id=resume_thread_id,
        )
        handle = backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                    target_name=execution_id,
                    session_id=session_id,
                    directory_path=session.directory_path if session is not None else "root",
                    command=command,
                    metadata={
                        "effect_type": self.node_type(),
                        "dispatch_id": dispatch_id,
                    },
                )
            )
        )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": resume_thread_id or dispatch_id,
            "success": True,
            "output_mode": "on_completion",
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
        if str(outputs.get("output_mode") or "").strip() != "on_completion":
            return False
        return bool(str(outputs.get("dispatch_id") or "").strip())

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
        for record in worker.execution_store.read_all().values():
            if record.effect_type != cls.node_type() or record.status != "waiting":
                continue
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
        waiting_records = [
            record
            for record in worker.execution_store.read_all().values()
            if record.effect_type == cls.node_type() and record.status == "waiting"
        ]
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
            return 0
        logs = ""
        try:
            logs = backend.get_log_snapshot(execution_id, lines=400)
        except Exception:
            logs = ""
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
        thread_id = cls._thread_id_from_logs(logs) or str(record.outputs.get("thread_id") or "")
        return TriggerQueueRecord(
            event_id=f"__await-agent-completion__:{dispatch_id}:{finished_at.isoformat()}",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            trace_id=record.trace_id,
            occurred_at=finished_at,
            payload={
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
            },
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
        published_event_id = worker._publish_completion_event_from_record(
            record=record, outputs=merged_outputs
        )
        record.status = "succeeded"
        record.outputs = merged_outputs
        record.published_event_id = published_event_id
        record.completion_event_enqueued_at = finished_at
        record.finished_at = finished_at
        worker.execution_store.put(record)
        worker._update_event_delivery_state(record.event_id)

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
    ) -> str:
        return container_prompt_command(
            prompt=prompt,
            model=model,
            resume_thread_id=resume_thread_id,
        )

    @classmethod
    def _thread_id_from_logs(cls, logs: str) -> str | None:
        match = _CONTAINER_THREAD_ID_LOG_PATTERN.search(logs)
        if match is None:
            return None
        return match.group("thread_id").strip() or None
