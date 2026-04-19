from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_sandbox.agent_completion_watcher import build_agent_run_completed_event
from agent_sandbox.events.models import AgentPromptEffectDefinition
from agent_sandbox.events.models import NODE_OUTPUTS_CONTEXT_KEY
from agent_sandbox.triggers.queue import TriggerQueueRecord

from .base import EffectProviderBase

_AWAIT_AGENT_STALE_GRACE_SECONDS = 120
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


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

        try:
            summaries = worker._list_vm_summaries_for_reconciliation()
        except Exception:
            return 0

        completed = 0
        for record in waiting_records:
            queue_record = cls._build_completion_queue_record_for_waiting_record(record=record, summaries=summaries)
            if queue_record is None:
                continue
            cls._finalize_waiting_record(worker=worker, record=record, queue_record=queue_record)
            completed += 1
        return completed

    @classmethod
    def node_type(cls) -> str:
        return "effect:agent:prompt"

    @classmethod
    def _finalize_waiting_record(cls, *, worker: Any, record: Any, queue_record: TriggerQueueRecord) -> None:
        finished_at = datetime.now(UTC)
        merged_outputs = {
            **record.outputs,
            **{
                key: value
                for key, value in queue_record.payload.items()
                if key not in {NODE_OUTPUTS_CONTEXT_KEY, _BRANCH_PORT_PAYLOAD_KEY, _FOR_EXECUTION_IDS_PAYLOAD_KEY}
            },
        }
        published_event_id = worker._publish_completion_event_from_record(record=record, outputs=merged_outputs)
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
                and (vm_name is None or str(getattr(summary, "vm_name", "") or "").strip() == vm_name)
                and (
                    session_id is None
                    or session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
                )
            ),
            None,
        )
        if exact_match is not None:
            return exact_match

        if (datetime.now(UTC) - record.finished_at).total_seconds() < _AWAIT_AGENT_STALE_GRACE_SECONDS:
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
                if session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
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
