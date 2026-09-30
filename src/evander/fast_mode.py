"""Codex speed selection and dispatch-time session inheritance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator

from evander.reasoning_effort import session_agent_harness


# Creation upserts must distinguish an omitted default from an explicit null reset.
FAST_MODE_UNSET = object()


def normalize_fast_mode(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("fast_mode must be true, false, or null")


FastMode = Annotated[bool, BeforeValidator(normalize_fast_mode)]
FastModeSource = Literal["prompt", "session", "default"]


def validate_fast_mode(value: Any, *, harness: str, route: str | None = None) -> bool | None:
    enabled = normalize_fast_mode(value)
    if enabled and (harness != "codex" or route not in (None, "openai_public")):
        raise ValueError("fast_mode is supported only for public OpenAI Codex sessions")
    return enabled


def validate_session_fast_mode(session: Any, config: Any = None) -> None:
    metadata = getattr(session, "metadata", {}) or {}
    validate_fast_mode(
        getattr(session, "fast_mode", None),
        harness=session_agent_harness(session, config),
        route=metadata.get("model_provider_mode") or getattr(config, "model_provider_mode", None),
    )


@dataclass(frozen=True)
class ResolvedFastMode:
    value: bool
    source: FastModeSource

    def fields(self) -> dict[str, Any]:
        return {"fast_mode": self.value, "fast_mode_source": self.source}

    @property
    def service_tier(self) -> str:
        return "fast" if self.value else "default"


def resolve_fast_mode(
    payload: Mapping[str, Any],
    *,
    session: Any,
    harness: str,
    route: str | None = None,
) -> ResolvedFastMode:
    enabled = normalize_fast_mode(payload.get("fast_mode"))
    source = payload.get("fast_mode_source")
    if source not in ("prompt", "session", "default"):
        if enabled is not None:
            source = "prompt"
        elif getattr(session, "fast_mode", None) is not None:
            enabled = normalize_fast_mode(session.fast_mode)
            source = "session"
        else:
            source = "default"
    enabled = enabled if enabled is not None else False
    validate_fast_mode(enabled, harness=harness, route=route)
    return ResolvedFastMode(enabled, source)


def recorded_fast_mode(record: Any) -> dict[str, Any]:
    sources = [
        getattr(record, key, None) for key in ("outputs", "resolved_config", "input_payload")
    ]
    for source in sources:
        if isinstance(source, Mapping) and source.get("fast_mode_source") in {
            "prompt",
            "session",
            "default",
        }:
            return {key: source.get(key) for key in ("fast_mode", "fast_mode_source")}
    for source in sources[1:] + sources[:1]:
        if isinstance(source, Mapping) and source.get("fast_mode") is not None:
            return {"fast_mode": source["fast_mode"]}
    return {}
