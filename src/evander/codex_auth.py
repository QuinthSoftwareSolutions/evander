from __future__ import annotations

from evander.reasoning_effort import ReasoningEffort, ReasoningEffortSource

from datetime import datetime
from enum import Enum
import re
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


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


class CodexAuthMode(str, Enum):
    METERED_API = "metered_api"
    CHATGPT_SUBSCRIPTION = "chatgpt_subscription"


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
    reasoning_effort_source: ReasoningEffortSource | None = None
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


class CodexSubscriptionDeferral(BaseModel):
    """Safe structured capacity result returned by a subscription worker."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    reason: Literal["signed_out", "disabled", "unavailable", "rate_limited"]
    retry_at: datetime | None = None

    @field_validator("profile_id", mode="before")
    @classmethod
    def normalize_profile_id(cls, value: object) -> str:
        return str(normalize_subscription_profile_id(value, allow_none=False))

    @property
    def queue_reason(self) -> str:
        return f"subscription_{self.reason}"


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
