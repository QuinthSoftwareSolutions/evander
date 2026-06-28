from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agent_sandbox.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)
from agent_sandbox.model_usage.runtime import model_usage_store
from agent_sandbox.operator_steering import (
    OPERATOR_STEERING_NOTE_MAX_LENGTH,
    OperatorSteeringNote,
    operator_steering_note_store,
)
from agent_sandbox.models import SessionManifest


class _ModelUsageAdminServiceHost(Protocol):
    config: Any
    runtime: Any

    def _agent_record_directory_path(self, value: Any) -> str: ...
    def _is_container_session(self, session: SessionManifest) -> bool: ...
    def _interrupt_container_operator_steering_note(
        self,
        *,
        session: SessionManifest,
        context: ModelUsageCorrelationContext,
        message: str,
        worker_execution_id: str | None,
        created_by: str | None,
    ) -> dict[str, Any]: ...


class ModelUsageAdminService:
    """Model usage and operator-steering operations extracted from AdminService."""

    def __init__(self, host: _ModelUsageAdminServiceHost) -> None:
        self._host = host

    @property
    def _runtime_dir(self) -> Path:
        return self._host.config.runtime_dir

    def list_model_usage_events(
        self,
        *,
        session_id: str | None = None,
        workflow_run_id: str | None = None,
        effect_id: str | None = None,
        dispatch_id: str | None = None,
        directory_path: str | None = None,
        include_directory_descendants: bool = False,
        provider: str | None = None,
        model: str | None = None,
        agent_harness: str | None = None,
        execution_backend: str | None = None,
        normalized_usage_status: str | None = None,
        request_status: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int | None = None,
    ) -> list[ModelInferenceUsageEvent]:
        return model_usage_store(self._runtime_dir).list_events(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            effect_id=effect_id,
            dispatch_id=dispatch_id,
            directory_path=directory_path,
            include_directory_descendants=include_directory_descendants,
            provider=provider,
            model=model,
            agent_harness=agent_harness,
            execution_backend=execution_backend,
            normalized_usage_status=normalized_usage_status,
            request_status=request_status,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            limit=limit,
        )

    def operator_steering_directory_path(
        self, *, session_id: str, dispatch_id: str | None = None
    ) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        normalized_dispatch_id = str(dispatch_id or "").strip() or None
        context = model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
        )
        if context is not None and context.directory_path:
            return self._host._agent_record_directory_path(context.directory_path)
        session = self._host.runtime.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"unknown session: {normalized_session_id}")
        return self._host._agent_record_directory_path(session.directory_path)

    def active_operator_steering_context(
        self, *, session_id: str
    ) -> ModelUsageCorrelationContext | None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        return model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
        )

    def create_operator_steering_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
    ) -> OperatorSteeringNote:
        normalized_session_id = str(session_id or "").strip()
        normalized_dispatch_id = str(dispatch_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not normalized_dispatch_id:
            raise ValueError("dispatch_id is required")
        context = model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
        )
        if context is None:
            raise ValueError(
                "No active model-correlation context found for this session and dispatch."
            )
        return operator_steering_note_store(self._runtime_dir).create_note(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
            worker_execution_id=worker_execution_id,
            message=message,
            created_by=created_by,
        )

    def submit_operator_steering_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        normalized_dispatch_id = str(dispatch_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not normalized_dispatch_id:
            raise ValueError("dispatch_id is required")
        normalized_message = self._validate_operator_steering_message(message)
        context = model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
        )
        if context is None:
            raise ValueError(
                "No active model-correlation context found for this session and dispatch."
            )
        session = self._host.runtime.session_repository.get_session(normalized_session_id)
        if context.execution_backend == "container_worker_vm":
            if session is None:
                raise ValueError(f"unknown session: {normalized_session_id}")
            return self._host._interrupt_container_operator_steering_note(
                session=session,
                context=context,
                message=normalized_message,
                worker_execution_id=worker_execution_id,
                created_by=created_by,
            )
        if session is not None and self._host._is_container_session(session):
            return self._host._interrupt_container_operator_steering_note(
                session=session,
                context=context,
                message=normalized_message,
                worker_execution_id=worker_execution_id,
                created_by=created_by,
            )
        note = self.create_operator_steering_note(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
            worker_execution_id=worker_execution_id,
            message=normalized_message,
            created_by=created_by,
        )
        return {"mode": "queued_note", "note": note}

    @staticmethod
    def _validate_operator_steering_message(message: str) -> str:
        text = str(message or "").strip()
        if not text:
            raise ValueError("message is required")
        if len(text) > OPERATOR_STEERING_NOTE_MAX_LENGTH:
            raise ValueError(
                f"message must be at most {OPERATOR_STEERING_NOTE_MAX_LENGTH} characters"
            )
        return text
