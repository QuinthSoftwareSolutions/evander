from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from agent_sandbox.model_usage.models import (
        ModelInferenceUsageEvent,
        ModelUsageCorrelationContext,
    )
    from agent_sandbox.operator_steering import OperatorSteeringNote


class _ModelUsageAdminServiceHost(Protocol):
    def _list_model_usage_events_impl(self, **kwargs: Any) -> list["ModelInferenceUsageEvent"]: ...
    def _operator_steering_directory_path_impl(self, **kwargs: Any) -> str: ...
    def _active_operator_steering_context_impl(
        self, **kwargs: Any
    ) -> "ModelUsageCorrelationContext" | None: ...
    def _create_operator_steering_note_impl(self, **kwargs: Any) -> "OperatorSteeringNote": ...
    def _submit_operator_steering_note_impl(self, **kwargs: Any) -> dict[str, Any]: ...


class ModelUsageAdminService:
    """Model usage and operator-steering operations extracted from AdminService."""

    def __init__(self, host: _ModelUsageAdminServiceHost) -> None:
        self._host = host

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
    ) -> list["ModelInferenceUsageEvent"]:
        return self._host._list_model_usage_events_impl(
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
        return self._host._operator_steering_directory_path_impl(
            session_id=session_id, dispatch_id=dispatch_id
        )

    def active_operator_steering_context(
        self, *, session_id: str
    ) -> "ModelUsageCorrelationContext" | None:
        return self._host._active_operator_steering_context_impl(session_id=session_id)

    def create_operator_steering_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
    ) -> "OperatorSteeringNote":
        return self._host._create_operator_steering_note_impl(
            session_id=session_id,
            dispatch_id=dispatch_id,
            message=message,
            worker_execution_id=worker_execution_id,
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
        return self._host._submit_operator_steering_note_impl(
            session_id=session_id,
            dispatch_id=dispatch_id,
            message=message,
            worker_execution_id=worker_execution_id,
            created_by=created_by,
        )
