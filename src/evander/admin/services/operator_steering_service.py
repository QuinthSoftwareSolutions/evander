from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from evander.execution.models import (
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
)
from evander.model_usage.models import ModelUsageCorrelationContext
from evander.model_usage.runtime import register_model_usage_correlation_context
from evander.models import SessionManifest
from evander.operator_steering import OperatorSteeringNote, operator_steering_note_store
from evander.worker.security import sanitize_text


@dataclass(frozen=True)
class OperatorSteeringAdminDependencies:
    runtime_dir: Path
    normalize_agent_record_directory_path: Callable[[Any], str]
    workflow_run_store_paths: Callable[[], dict[str, Any]]
    container_execution_records_for_session: Callable[[str], list[Any]]
    container_worker_backend: Callable[..., Any]
    container_worker_url_for_target: Callable[..., str | None]


class OperatorSteeringAdminService:
    """Operator-steering note delivery and container interrupt ownership."""

    def __init__(self, dependencies: OperatorSteeringAdminDependencies) -> None:
        self._deps = dependencies

    def interrupt_container_operator_steering_note(
        self,
        *,
        session: SessionManifest,
        context: ModelUsageCorrelationContext,
        message: str,
        worker_execution_id: str | None,
        created_by: str | None,
    ) -> dict[str, Any]:
        from evander.effects.providers.agent_prompt import (
            CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY,
            AgentPromptEffectProvider,
        )
        from evander.effects.providers.agent_session import (
            container_codex_turn_spec,
            container_prompt_command,
            container_worker_guest_gateway_url,
            container_worker_image_for_session,
            container_worker_resource_limits_for_session,
            default_agent_model_for_provider,
            gateway_url_with_session_context,
            safe_effect_execution_id,
        )
        from evander.session_auth import session_mcp_auth_token

        session_id = session.session_id
        dispatch_id = context.dispatch_id
        directory_path = self._deps.normalize_agent_record_directory_path(session.directory_path)
        execution_store = self._operator_steering_execution_store()
        record = self._find_operator_steering_container_waiting_record(
            execution_store=execution_store,
            session_id=session_id,
            dispatch_id=dispatch_id,
            worker_execution_id=worker_execution_id,
        )
        original_outputs = dict(record.outputs)
        current_execution_id = str(original_outputs.get("execution_id") or "").strip()
        if not current_execution_id:
            raise ValueError(
                "Active container prompt execution is missing its worker execution id."
            )

        now = datetime.now(UTC)
        now_text = now.isoformat().replace("+00:00", "Z")
        in_progress_outputs = dict(original_outputs)
        in_progress_outputs[CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY] = True
        in_progress_outputs["interrupt_started_at"] = now_text
        in_progress_outputs["interrupting_execution_id"] = current_execution_id
        in_progress_outputs["operator_steering_message_preview"] = sanitize_text(message[:180])
        in_progress_record = record.model_copy(
            update={"outputs": in_progress_outputs, "finished_at": now}
        )
        execution_store.put(in_progress_record)

        execution_records = self._deps.container_execution_records_for_session(session_id)
        current_record = next(
            (
                candidate
                for candidate in execution_records
                if str(getattr(candidate, "execution_id", "") or "").strip() == current_execution_id
            ),
            None,
        )
        backend = self._deps.container_worker_backend(
            directory_path=directory_path,
            worker_url=self._deps.container_worker_url_for_target(
                session=session,
                record=current_record,
                records=execution_records,
            ),
        )
        current_logs = self._safe_container_log_snapshot(
            backend,
            current_execution_id,
            lines=120,
        )
        resume_thread_id = (
            AgentPromptEffectProvider._usable_resume_thread_id(original_outputs.get("thread_id"))
            or AgentPromptEffectProvider._usable_resume_thread_id(
                self._container_session_agent_thread_id(session)
            )
            or AgentPromptEffectProvider._thread_id_from_logs(current_logs)
        )

        try:
            backend.stop(current_execution_id)
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            if self._container_execution_is_terminal(backend, current_execution_id):
                failure_message = (
                    "Operator steering stop failed after the container execution reached "
                    "a terminal state "
                    f"(session_id={session_id}, dispatch_id={dispatch_id}, "
                    f"execution_id={current_execution_id}, message={detail})"
                )
                failed_outputs = dict(in_progress_outputs)
                failed_outputs.pop(CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY, None)
                failed_outputs.update(
                    {
                        "success": False,
                        "execution_backend": "container_worker_vm",
                        "execution_id": current_execution_id,
                        "worker_execution_id": current_execution_id,
                        "agent_state": "failed",
                        "message": failure_message,
                        "operator_steering_failed": True,
                    }
                )
                execution_store.put(
                    in_progress_record.model_copy(
                        update={
                            "status": "failed",
                            "finished_at": datetime.now(UTC),
                            "outputs": failed_outputs,
                            "error": failure_message,
                        }
                    )
                )
            else:
                restored_record = record.model_copy(update={"outputs": original_outputs})
                execution_store.put(restored_record)
            raise RuntimeError(
                "Could not stop the active container execution for operator steering "
                f"(execution_id={current_execution_id}, message={detail})"
            ) from exc

        cancelled_legacy_note_ids = self._cancel_pending_operator_steering_notes_for_dispatch(
            session_id=session_id,
            dispatch_id=dispatch_id,
        )
        effective_config = backend.config
        agent_harness = AgentPromptEffectProvider._agent_harness_from_session(
            session,
            config=effective_config,
        )
        resolved_model = (
            str(context.model or "").strip()
            or str(record.resolved_config.get("model") or "").strip()
            or str(original_outputs.get("model") or "").strip()
            or str(getattr(session, "model", None) or "").strip()
            or default_agent_model_for_provider(agent_harness, effective_config)
        )
        approval_lease_id = (
            str(record.resolved_config.get("mcp_tool_approval_lease_id") or "").strip()
            or str(original_outputs.get("mcp_tool_approval_lease_id") or "").strip()
            or None
        )
        mcp_gateway_url = (
            gateway_url_with_session_context(
                container_worker_guest_gateway_url(effective_config),
                session_id=session_id,
                workflow_run_id=context.workflow_run_id,
                mcp_tool_approval_lease_id=approval_lease_id,
                mcp_session_token=session_mcp_auth_token(session),
            )
            if approval_lease_id
            else None
        )
        replacement_execution_id = safe_effect_execution_id("agent", session_id)
        replacement_prompt = self._operator_steering_interrupt_prompt(message)
        command = container_prompt_command(
            prompt=replacement_prompt,
            model=resolved_model,
            resume_thread_id=resume_thread_id,
            session=session,
            agent_provider=agent_harness,
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        codex_turn = container_codex_turn_spec(
            prompt=replacement_prompt,
            model=resolved_model,
            resume_thread_id=resume_thread_id,
            session=session,
            config=effective_config,
            agent_provider=agent_harness,
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        try:
            handle = backend.start(
                backend.prepare(
                    ExecutionSpec(
                        backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                        target_name=replacement_execution_id,
                        session_id=session_id,
                        directory_path=directory_path,
                        image=container_worker_image_for_session(session, effective_config),
                        resource_limits=container_worker_resource_limits_for_session(session),
                        command=command,
                        codex_turn=codex_turn,
                        metadata={
                            "effect_type": str(record.effect_type or ""),
                            "dispatch_id": dispatch_id,
                            "resume_thread_id": resume_thread_id,
                            "operator_steering": "interrupt_and_resume",
                            "interrupted_execution_id": current_execution_id,
                            "agent_provider": agent_harness,
                            "model": resolved_model,
                            "model_provider_mode": effective_config.model_provider_mode,
                            **(
                                {"codex_auth_mode": codex_turn.auth_mode.value}
                                if codex_turn is not None
                                else {}
                            ),
                            **(
                                {"created_by": str(created_by).strip()}
                                if str(created_by or "").strip()
                                else {}
                            ),
                        },
                    )
                )
            )
        except Exception as exc:
            failure_message = (
                "Operator steering stopped the active container execution but could not "
                "start the replacement "
                f"(session_id={session_id}, dispatch_id={dispatch_id}, "
                f"interrupted_execution_id={current_execution_id}, message={exc})"
            )
            failed_outputs = dict(in_progress_outputs)
            failed_outputs.pop(CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY, None)
            failed_outputs.update(
                {
                    "success": False,
                    "execution_backend": "container_worker_vm",
                    "execution_id": current_execution_id,
                    "worker_execution_id": current_execution_id,
                    "worker_status": "interrupted",
                    "agent_state": "failed",
                    "message": failure_message,
                    "operator_steering_failed": True,
                }
            )
            execution_store.put(
                in_progress_record.model_copy(
                    update={
                        "status": "failed",
                        "finished_at": datetime.now(UTC),
                        "outputs": failed_outputs,
                        "error": failure_message,
                    }
                )
            )
            raise RuntimeError(failure_message) from exc

        replacement_thread_id = resume_thread_id or str(original_outputs.get("thread_id") or "")
        interrupted_execution_ids = self._append_unique_text_value(
            original_outputs.get("interrupted_execution_ids"),
            current_execution_id,
        )
        replacement_outputs = dict(original_outputs)
        replacement_outputs.pop(CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY, None)
        replacement_outputs.update(
            {
                "success": True,
                "queued": False,
                "output_mode": "on_completion",
                "execution_backend": "container_worker_vm",
                "execution_id": handle.execution_id,
                "worker_execution_id": handle.execution_id,
                "session_id": session_id,
                "dispatch_id": dispatch_id,
                "thread_id": replacement_thread_id or dispatch_id,
                "operator_steering_status": "interrupted_and_resumed",
                "operator_interrupt_count": self._safe_positive_int(
                    original_outputs.get("operator_interrupt_count")
                )
                + 1,
                "last_operator_interrupt_at": now_text,
                "interrupted_execution_id": current_execution_id,
                "interrupted_execution_ids": interrupted_execution_ids,
                "message": (
                    "Interrupted active container execution and started replacement "
                    f"{handle.execution_id}"
                ),
                **(
                    {"cancelled_legacy_operator_steering_note_ids": cancelled_legacy_note_ids}
                    if cancelled_legacy_note_ids
                    else {}
                ),
            }
        )
        execution_store.put(
            in_progress_record.model_copy(
                update={"outputs": replacement_outputs, "finished_at": datetime.now(UTC)}
            )
        )
        self._annotate_interrupted_container_execution(
            backend=backend,
            execution_id=current_execution_id,
            replacement_execution_id=handle.execution_id,
            interrupted_at=now_text,
            created_by=created_by,
        )
        register_model_usage_correlation_context(
            runtime_dir=self._deps.runtime_dir,
            session_id=session_id,
            dispatch_id=dispatch_id,
            workflow_run_id=context.workflow_run_id,
            effect_id=context.effect_id or record.effect_id,
            node_id=context.node_id or record.effect_id,
            execution_backend="container_worker_vm",
            directory_path=directory_path,
            model=resolved_model,
            trace_id=context.trace_id or record.trace_id,
            agent_harness=agent_harness,
        )

        note = operator_steering_note_store(self._deps.runtime_dir).create_delivered_note(
            session_id=session_id,
            dispatch_id=dispatch_id,
            worker_execution_id=handle.execution_id,
            message=message,
            created_by=created_by,
            provider="container_worker_vm",
            provider_endpoint="interrupt-and-resume",
            now=datetime.now(UTC),
        )
        return {
            "mode": "interrupt_and_resume",
            "note": note,
            "interrupt": {
                "session_id": session_id,
                "dispatch_id": dispatch_id,
                "interrupted_execution_id": current_execution_id,
                "replacement_execution_id": handle.execution_id,
                "thread_id": replacement_thread_id or None,
                "message": (
                    "Interrupted the active container run and started a replacement "
                    "with the steering note."
                ),
            },
        }

    def list_operator_steering_notes(
        self,
        *,
        session_id: str,
        dispatch_id: str | None = None,
        limit: int = 50,
    ) -> list[OperatorSteeringNote]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        return operator_steering_note_store(self._deps.runtime_dir).list_notes(
            session_id=normalized_session_id,
            dispatch_id=str(dispatch_id or "").strip() or None,
            limit=limit,
        )

    def get_operator_steering_note(self, note_id: str) -> OperatorSteeringNote:
        note = operator_steering_note_store(self._deps.runtime_dir).get_note(note_id)
        if note is None:
            raise ValueError(f"Operator steering note not found: {note_id}")
        return note

    def cancel_operator_steering_note(self, note_id: str) -> OperatorSteeringNote:
        note = operator_steering_note_store(self._deps.runtime_dir).cancel_note(note_id)
        if note is None:
            raise ValueError(f"Operator steering note not found: {note_id}")
        if note.status != "cancelled":
            raise ValueError(
                f"Operator steering note cannot be cancelled from status: {note.status}"
            )
        return note

    def _cancel_pending_operator_steering_notes_for_dispatch(
        self,
        *,
        session_id: str,
        dispatch_id: str,
    ) -> list[str]:
        store = operator_steering_note_store(self._deps.runtime_dir)
        cancelled: list[str] = []
        for note in store.list_notes(session_id=session_id, dispatch_id=dispatch_id, limit=200):
            if note.status != "pending":
                continue
            try:
                updated = store.cancel_note(note.note_id)
            except Exception:
                continue
            if updated is not None and updated.status == "cancelled":
                cancelled.append(updated.note_id)
        return cancelled

    def _operator_steering_execution_store(self):
        from evander.daemons.effect_orchestrator import SqliteExecutionStore

        stores = self._deps.workflow_run_store_paths()
        return SqliteExecutionStore(Path(stores["orchestration_store_path"]))

    def _find_operator_steering_container_waiting_record(
        self,
        *,
        execution_store: Any,
        session_id: str,
        dispatch_id: str,
        worker_execution_id: str | None,
    ) -> Any:
        normalized_worker_execution_id = str(worker_execution_id or "").strip()
        candidates: list[Any] = []
        for record in execution_store.list_waiting_records():
            effect_type = str(getattr(record, "effect_type", "") or "").strip()
            if effect_type not in {"effect:agent:prompt", "effect:agent:enqueue-prompt"}:
                continue
            outputs = record.outputs if isinstance(record.outputs, Mapping) else {}
            resolved_config = (
                record.resolved_config if isinstance(record.resolved_config, Mapping) else {}
            )
            execution_backend = str(
                outputs.get("execution_backend") or resolved_config.get("execution_backend") or ""
            ).strip()
            if execution_backend != "container_worker_vm":
                continue
            if str(outputs.get("session_id") or "").strip() != session_id:
                continue
            if str(outputs.get("dispatch_id") or "").strip() != dispatch_id:
                continue
            candidates.append(record)
        if not candidates:
            raise ValueError(
                "No waiting container prompt execution found for this session and dispatch."
            )
        if normalized_worker_execution_id:
            exact = [
                record
                for record in candidates
                if str(record.outputs.get("execution_id") or "").strip()
                == normalized_worker_execution_id
            ]
            if len(exact) == 1:
                return exact[0]
        if len(candidates) == 1:
            return candidates[0]
        candidates.sort(
            key=lambda record: (
                getattr(record, "finished_at", None)
                or getattr(record, "started_at", None)
                or datetime.min.replace(tzinfo=UTC),
                str(getattr(record, "execution_id", "") or ""),
            ),
            reverse=True,
        )
        return candidates[0]

    @staticmethod
    def _safe_container_log_snapshot(backend: Any, execution_id: str, *, lines: int) -> str:
        try:
            return str(backend.get_log_snapshot(execution_id, lines=lines) or "")
        except Exception:
            return ""

    @staticmethod
    def _container_execution_is_terminal(backend: Any, execution_id: str) -> bool:
        inspect = getattr(backend, "inspect", None)
        if not callable(inspect):
            return False
        try:
            status = inspect(execution_id)
        except Exception:
            return False
        value = getattr(status, "status", None)
        normalized = str(getattr(value, "value", value) or "").strip()
        return normalized in {
            ExecutionStatusKind.STOPPED.value,
            ExecutionStatusKind.FAILED.value,
            ExecutionStatusKind.DESTROYED.value,
        }

    @staticmethod
    def _container_session_agent_thread_id(session: SessionManifest) -> str | None:
        metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
        for key in ("agent_thread_id", "thread_id", "resume_thread_id"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _operator_steering_interrupt_prompt(message: str) -> str:
        return (
            "The operator interrupted the previous container run for this same task. "
            "Continue from the current workspace state and prior thread context, and "
            "apply this operator instruction now:\n\n"
            f"{message}"
        )

    @staticmethod
    def _append_unique_text_value(value: Any, new_value: str) -> list[str]:
        result: list[str] = []
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text and text not in result:
                    result.append(text)
        text = str(new_value or "").strip()
        if text and text not in result:
            result.append(text)
        return result

    @staticmethod
    def _safe_positive_int(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed > 0 else 0

    @staticmethod
    def _annotate_interrupted_container_execution(
        *,
        backend: Any,
        execution_id: str,
        replacement_execution_id: str,
        interrupted_at: str,
        created_by: str | None,
    ) -> None:
        repository = getattr(backend, "repository", None)
        get_record = getattr(repository, "get", None)
        put_record = getattr(repository, "put", None)
        if not callable(get_record) or not callable(put_record):
            return
        try:
            record = get_record(execution_id)
        except Exception:
            return
        if record is None:
            return
        metadata = dict(getattr(record, "metadata", {}) or {})
        metadata.update(
            {
                "interrupted_by_operator": True,
                "interrupted_at": interrupted_at,
                "interrupt_replaced_by_execution_id": replacement_execution_id,
            }
        )
        if str(created_by or "").strip():
            metadata["interrupted_by"] = str(created_by).strip()
        try:
            put_record(record.model_copy(update={"metadata": metadata}))
        except Exception:
            return
