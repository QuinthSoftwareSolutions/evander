from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.models import utc_now

ModelUsageRequestStatus = Literal["success", "failed", "error", "cancelled"]
ModelUsageNormalizedStatus = Literal["available", "usage_unavailable"]


def _normalize_required_text(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_optional_positive_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    number = int(text)
    if number < 0:
        raise ValueError("value must be greater than or equal to 0")
    return number


def _normalize_utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value or "").strip()
    if not text:
        raise ValueError("datetime is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class ModelInferenceUsageEvent(BaseModel):
    event_id: str
    occurred_at: datetime = Field(default_factory=utc_now)
    provider: str
    provider_endpoint_identifier: str
    model: str | None = None
    deployment: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    request_status: ModelUsageRequestStatus
    duration_ms: int | None = None
    normalized_usage_status: ModelUsageNormalizedStatus
    raw_usage_metadata: dict[str, Any] = Field(default_factory=dict)
    request_body_bytes: int | None = None
    request_body_sha256: str | None = None
    response_body_bytes: int | None = None
    response_body_sha256: str | None = None
    session_id: str | None = None
    workflow_run_id: str | None = None
    effect_id: str | None = None
    node_id: str | None = None
    dispatch_id: str | None = None
    agent_harness: str | None = None
    execution_backend: str | None = None
    directory_path: str | None = None
    trace_id: str | None = None
    request_id: str | None = None

    @field_validator(
        "event_id",
        "provider",
        "provider_endpoint_identifier",
        "model",
        "deployment",
        "request_status",
        "normalized_usage_status",
        "request_body_sha256",
        "response_body_sha256",
        "session_id",
        "workflow_run_id",
        "effect_id",
        "node_id",
        "dispatch_id",
        "agent_harness",
        "execution_backend",
        "directory_path",
        "trace_id",
        "request_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: object, info) -> object:
        if info.field_name in {"event_id", "provider", "provider_endpoint_identifier"}:
            return _normalize_required_text(value, field_name=info.field_name)
        if info.field_name in {"request_status", "normalized_usage_status"}:
            return _normalize_required_text(value, field_name=info.field_name).lower()
        return _normalize_optional_text(value)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def normalize_occurred_at(cls, value: object) -> datetime:
        return _normalize_utc_datetime(value)

    @field_validator("input_tokens", "output_tokens", "total_tokens", "duration_ms", mode="before")
    @classmethod
    def normalize_optional_ints(cls, value: object) -> int | None:
        return _normalize_optional_positive_int(value)

    @field_validator("raw_usage_metadata", mode="before")
    @classmethod
    def normalize_raw_usage_metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("raw_usage_metadata must be a JSON object")
        return value


class ModelUsageCorrelationContext(BaseModel):
    session_id: str
    dispatch_id: str
    workflow_run_id: str | None = None
    effect_id: str | None = None
    node_id: str | None = None
    agent_harness: str
    execution_backend: str | None = None
    directory_path: str | None = None
    model: str | None = None
    team_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    trace_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    @field_validator(
        "session_id",
        "dispatch_id",
        "workflow_run_id",
        "effect_id",
        "node_id",
        "agent_harness",
        "execution_backend",
        "directory_path",
        "model",
        "team_id",
        "project_id",
        "agent_id",
        "trace_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: object, info) -> object:
        if info.field_name in {"session_id", "dispatch_id", "agent_harness"}:
            return _normalize_required_text(value, field_name=info.field_name)
        return _normalize_optional_text(value)

    @field_validator("started_at", "expires_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: object) -> datetime:
        return _normalize_utc_datetime(value)

    @model_validator(mode="after")
    def validate_expiry(self) -> "ModelUsageCorrelationContext":
        if self.expires_at < self.started_at:
            raise ValueError("expires_at must be greater than or equal to started_at")
        return self
