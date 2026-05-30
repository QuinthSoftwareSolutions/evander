from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
    ModelUsageRequestStatus,
    _normalize_optional_positive_int,
    _normalize_optional_text,
    _normalize_required_text,
    _normalize_utc_datetime,
)
from agent_sandbox.models import utc_now


class ModelUsageExtractionRequest(BaseModel):
    provider: str
    provider_endpoint_identifier: str
    request_status: ModelUsageRequestStatus
    occurred_at: datetime = Field(default_factory=utc_now)
    duration_ms: int | None = None
    model: str | None = None
    deployment: str | None = None
    request_body_bytes: int | None = None
    request_body_sha256: str | None = None
    response_body_bytes: int | None = None
    response_body_sha256: str | None = None
    response_payload: dict[str, Any] = Field(default_factory=dict)
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    correlation_context: ModelUsageCorrelationContext | None = None
    trace_id: str | None = None
    request_id: str | None = None

    @field_validator("provider", "provider_endpoint_identifier", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: object, info) -> str:
        return _normalize_required_text(value, field_name=info.field_name)

    @field_validator("request_status", mode="before")
    @classmethod
    def normalize_request_status(cls, value: object) -> str:
        return _normalize_required_text(value, field_name="request_status").lower()

    @field_validator(
        "model",
        "deployment",
        "request_body_sha256",
        "response_body_sha256",
        "trace_id",
        "request_id",
        mode="before",
    )
    @classmethod
    def normalize_optional_text_fields(cls, value: object) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def normalize_occurred_at(cls, value: object) -> datetime:
        return _normalize_utc_datetime(value)

    @field_validator("request_body_bytes", "response_body_bytes", "duration_ms", mode="before")
    @classmethod
    def normalize_optional_int_fields(cls, value: object) -> int | None:
        return _normalize_optional_positive_int(value)

    @field_validator("response_payload", "request_metadata", "response_metadata", mode="before")
    @classmethod
    def normalize_payloads(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("payload fields must be JSON objects")
        return value


class ModelUsageExtractionResult(BaseModel):
    event: ModelInferenceUsageEvent


class ModelUsageExtractor(Protocol):
    def extract(self, request: ModelUsageExtractionRequest) -> ModelUsageExtractionResult: ...


class MappingModelUsageExtractor:
    """A small adapter scaffold for providers that already expose a JSON payload."""

    provider_name: str

    def __init__(self, *, provider_name: str) -> None:
        self.provider_name = provider_name

    def extract(self, request: ModelUsageExtractionRequest) -> ModelUsageExtractionResult:
        response_payload = request.response_payload
        usage = response_payload.get("usage")
        usage_payload = usage if isinstance(usage, dict) else {}
        context = request.correlation_context

        event = ModelInferenceUsageEvent(
            event_id=str(response_payload.get("id") or request.request_id or "model-usage-event"),
            occurred_at=request.occurred_at,
            provider=request.provider or self.provider_name,
            provider_endpoint_identifier=request.provider_endpoint_identifier,
            model=response_payload.get("model") or request.model,
            deployment=response_payload.get("deployment") or request.deployment,
            input_tokens=usage_payload.get("input_tokens"),
            output_tokens=usage_payload.get("output_tokens"),
            total_tokens=usage_payload.get("total_tokens"),
            request_status=request.request_status,
            duration_ms=request.duration_ms,
            normalized_usage_status=(
                "available" if usage_payload else "usage_unavailable"
            ),
            raw_usage_metadata={
                "provider_response_kind": response_payload.get("type"),
                "usage": usage_payload,
                "response_metadata": request.response_metadata,
                "request_metadata": request.request_metadata,
            },
            request_body_bytes=request.request_body_bytes,
            request_body_sha256=request.request_body_sha256,
            response_body_bytes=request.response_body_bytes,
            response_body_sha256=request.response_body_sha256,
            session_id=context.session_id if context is not None else None,
            workflow_run_id=context.workflow_run_id if context is not None else None,
            effect_id=context.effect_id if context is not None else None,
            node_id=context.node_id if context is not None else None,
            dispatch_id=context.dispatch_id if context is not None else None,
            agent_harness=context.agent_harness if context is not None else None,
            execution_backend=context.execution_backend if context is not None else None,
            directory_path=context.directory_path if context is not None else None,
            trace_id=request.trace_id or (context.trace_id if context is not None else None),
            request_id=request.request_id,
        )
        return ModelUsageExtractionResult(event=event)


def extract_model_usage(
    extractor: ModelUsageExtractor, request: ModelUsageExtractionRequest
) -> ModelUsageExtractionResult:
    return extractor.extract(request)
