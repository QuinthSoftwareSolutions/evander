from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


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
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1)
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
