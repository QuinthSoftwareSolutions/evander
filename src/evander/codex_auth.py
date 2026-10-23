from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.fast_mode import FastMode, FastModeSource
from evander.cyber_access import CyberAccessProgram
from evander.reasoning_effort import ReasoningEffort, ReasoningEffortSource

DEFAULT_SUBSCRIPTION_PROFILE_ID = "default"
SUBSCRIPTION_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


def normalize_subscription_profile_id(
    value: object,
    *,
    allow_none: bool = True,
) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError("subscription_profile_id is required")
    text = str(value).strip()
    if not text:
        if allow_none:
            return None
        raise ValueError("subscription_profile_id is required")
    if not SUBSCRIPTION_PROFILE_ID_PATTERN.fullmatch(text):
        raise ValueError(
            "subscription_profile_id must be a lowercase slug of 1-64 letters, "
            "numbers, dots, underscores, or dashes"
        )
    return text


def normalize_subscription_quota_limit_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("limit_id must be a non-empty string")
    return value.strip()


def normalize_subscription_quota_window_minutes(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("window_duration_minutes must be a positive integer")
    return value


class CodexAuthMode(str, Enum):
    METERED_API = "metered_api"
    CHATGPT_SUBSCRIPTION = "chatgpt_subscription"


class CodexRateLimitResetRequest(BaseModel):
    """One logical redemption; retries must retain the same idempotency key."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    idempotency_key: str
    credit_id: str | None = None
    expected_account_id: str | None = None

    @field_validator("idempotency_key", "credit_id", "expected_account_id")
    @classmethod
    def validate_nonempty_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must be a non-empty string")
        return value


class CodexSubscriptionResetRequest(CodexRateLimitResetRequest):
    """Bind Admin Web redemption to the worker and account the operator saw."""

    expected_worker_url: str
    expected_account_id: str

    @field_validator("expected_worker_url")
    @classmethod
    def validate_worker_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value


def normalize_codex_auth_mode(
    value: object,
    *,
    default: CodexAuthMode | None = None,
) -> CodexAuthMode | None:
    if value is None:
        return default
    if isinstance(value, CodexAuthMode):
        return value
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return default
    try:
        return CodexAuthMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in CodexAuthMode)
        raise ValueError(f"codex_auth_mode must be one of: {allowed}") from exc


class CodexTurnSpec(BaseModel):
    """Structured Codex turn data trusted by the container worker.

    The metered runner can continue to use its existing shell command. Subscription
    execution uses this structure so the worker never has to recover prompts or
    thread identifiers by parsing shell text.
    """

    model_config = ConfigDict(extra="forbid")

    auth_mode: CodexAuthMode = CodexAuthMode.METERED_API
    subscription_profile_id: str | None = None
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort | None = None
    fast_mode: FastMode | None = None
    cyber_access_program: CyberAccessProgram | None = None
    reasoning_effort_source: ReasoningEffortSource | None = None
    fast_mode_source: FastModeSource | None = None
    resume_thread_id: str | None = None
    resume_mode: Literal["new_context", "same_context"] = "new_context"
    mcp_gateway_url: AnyHttpUrl | None = None
    web_search_enabled: bool = False

    @field_validator("prompt", "model", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("resume_thread_id", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("subscription_profile_id", mode="before")
    @classmethod
    def normalize_profile_id(cls, value: object) -> str | None:
        return normalize_subscription_profile_id(value)

    @model_validator(mode="after")
    def validate_subscription_profile_mode(self) -> "CodexTurnSpec":
        if (
            self.subscription_profile_id is not None
            and self.auth_mode is not CodexAuthMode.CHATGPT_SUBSCRIPTION
        ):
            raise ValueError("subscription_profile_id requires auth_mode='chatgpt_subscription'")
        return self


class CodexRuntimeSpec(BaseModel):
    """Select an operator-installed runtime; grants no credentials to the command."""

    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    subscription_profile_id: str = DEFAULT_SUBSCRIPTION_PROFILE_ID
    cyber_access_program: CyberAccessProgram | None = None

    @field_validator("subscription_profile_id", mode="before")
    @classmethod
    def validate_profile(cls, value):
        return normalize_subscription_profile_id(value) or DEFAULT_SUBSCRIPTION_PROFILE_ID


class CodexSubscriptionDeferral(BaseModel):
    """Safe structured capacity result returned by a subscription worker."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    reason: Literal["signed_out", "disabled", "unavailable", "rate_limited", "model_capacity"]
    retry_at: datetime | None = None

    @field_validator("profile_id", mode="before")
    @classmethod
    def normalize_profile_id(cls, value: object) -> str:
        return str(normalize_subscription_profile_id(value, allow_none=False))

    @property
    def queue_reason(self) -> str:
        if self.reason == "model_capacity":
            return "model_capacity"
        return f"subscription_{self.reason}"


def codex_model_capacity_error(value: object) -> bool:
    """Recognize explicit provider overload without treating other failures as transient."""
    if isinstance(value, dict):
        for key, candidate in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized_key in {"code", "codexerrorinfo", "errorcode", "errortype", "reason", "type"}:
                code = re.sub(r"[^a-z0-9]", "", str(candidate).casefold())
                if code in {"modelatcapacity", "modelcapacityexceeded", "serveroverloaded"}:
                    return True
            if (
                normalized_key in {"message", "additionaldetails"}
                and codex_model_capacity_error(candidate)
            ):
                return True
            if isinstance(candidate, dict | list) and codex_model_capacity_error(candidate):
                return True
        return False
    if isinstance(value, list):
        return any(codex_model_capacity_error(candidate) for candidate in value)
    message = str(value or "").strip().casefold()
    return any(
        marker in message
        for marker in (
            "selected model is at capacity",
            "the model is overloaded",
            "the server is overloaded",
        )
    )


def legacy_worker_model_capacity_deferral(
    metadata: Mapping[str, object] | None, logs: str
) -> CodexSubscriptionDeferral | None:
    """Permit an idle worker upgrade after a control-plane-first rollout.

    Only a failed brokered subscription turn's last native error is evidence;
    ordinary tool output, successful jobs and existing structured outcomes win.
    """
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("codex_auth_mode") != "chatgpt_subscription"
        or metadata.get("codex_subscription_exit_code") not in (1, "1")
        or metadata.get("codex_subscription_deferral") is not None
    ):
        return None
    errors = [
        line.removeprefix("codex error: ")
        for line in logs.splitlines()
        if line.startswith("codex error: ")
    ]
    if not errors or not codex_model_capacity_error(errors[-1]):
        return None
    try:
        profile_id = normalize_subscription_profile_id(metadata.get("subscription_profile_id"))
    except ValueError:
        return None
    if profile_id is None:
        return None
    return CodexSubscriptionDeferral(profile_id=profile_id, reason="model_capacity")


class CodexSubscriptionDeferredError(RuntimeError):
    def __init__(self, deferral: CodexSubscriptionDeferral, message: str | None = None) -> None:
        self.deferral = deferral
        super().__init__(
            message or f"ChatGPT subscription profile {deferral.profile_id!r} is {deferral.reason}"
        )


def effective_subscription_profile_id(*, session: object | None) -> str | None:
    """Resolve profile-less legacy subscription sessions to the reserved default."""

    explicit = normalize_subscription_profile_id(
        getattr(session, "subscription_profile_id", None) if session is not None else None
    )
    if explicit is not None:
        return explicit
    mode = normalize_codex_auth_mode(
        getattr(session, "codex_auth_mode", None) if session is not None else None
    )
    if mode is CodexAuthMode.CHATGPT_SUBSCRIPTION:
        return DEFAULT_SUBSCRIPTION_PROFILE_ID
    return None


def effective_codex_auth_mode(*, session: object | None, config: object) -> CodexAuthMode:
    session_value = getattr(session, "codex_auth_mode", None) if session is not None else None
    if session_value is not None:
        return (
            normalize_codex_auth_mode(
                session_value,
                default=CodexAuthMode.METERED_API,
            )
            or CodexAuthMode.METERED_API
        )
    return (
        normalize_codex_auth_mode(
            getattr(config, "container_worker_codex_auth_mode", None),
            default=CodexAuthMode.METERED_API,
        )
        or CodexAuthMode.METERED_API
    )
