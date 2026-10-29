"""Native agent effort settings, capability hints, and dispatch-time inheritance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator

LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
HARNESS_LEVELS = {
    "codex": LEVELS,
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "copilot": ("low", "medium", "high", "xhigh", "max"),
}
# Deliberately match released families, not future model versions or deployment aliases.
# Sources: Codex config reference/model catalog, Claude Code model-config, Copilot CLI reference.
MODEL_RULES = (
    (r"^(?:anthropic/)?claude-opus-4[.-]5(?:-\d{8})?$", ("low", "medium", "high")),
    (
        r"^(?:anthropic/)?claude-(?:opus|sonnet)-4[.-]6(?:-\d{8})?$",
        ("low", "medium", "high", "max"),
    ),
    (
        r"^(?:anthropic/)?claude-(?:opus-4[.-][78]|opus-5(?:[.-]5)?|sonnet-5|fable-5(?:[.-]1)?)(?:-\d{8})?$",
        ("low", "medium", "high", "xhigh", "max"),
    ),
    (r"^(?:anthropic/)?claude-(?:haiku-4[.-]5|sonnet-4[.-]5)(?:-\d{8})?$", ()),
    (
        r"^(?:openai/)?gpt-6-astra(?:-\d{4}-\d{2}-\d{2})?$",
        ("low", "medium", "high", "xhigh", "max", "ultra"),
    ),
    (
        r"^(?:openai/)?gpt-(?:6-sol|5[.]6-(?:sol|terra))(?:-\d{4}-\d{2}-\d{2})?$",
        LEVELS[0:1] + LEVELS[2:],
    ),
    (
        r"^(?:openai/)?gpt-(?:6|5[.]6)-luna(?:-\d{4}-\d{2}-\d{2})?$",
        ("none", "low", "medium", "high", "xhigh", "max"),
    ),
    (r"^(?:openai/)?gpt-5(?:-\d{4}-\d{2}-\d{2})?$", ("minimal", "low", "medium", "high")),
    (r"^(?:openai/)?gpt-5[.]1(?:-\d{4}-\d{2}-\d{2})?$", ("none", "low", "medium", "high")),
    (
        r"^(?:openai/)?gpt-5[.](?:2|4(?:-mini)?)(?:-\d{4}-\d{2}-\d{2})?$",
        ("none", "low", "medium", "high", "xhigh"),
    ),
    (r"^(?:openai/)?gpt-5[.]3-codex(?:-\d{4}-\d{2}-\d{2})?$", ("low", "medium", "high", "xhigh")),
    (r"^(?:openai/)?gpt-4(?:o|[.]1)(?:-mini|-nano)?(?:-\d{4}-\d{2}-\d{2})?$", ()),
)


# Claude Code exposes adaptive effort only; the Opus 4.5 API supports effort separately.
HARNESS_MODEL_RULES = {"claude": ((r"^(?:anthropic/)?claude-opus-4[.-]5(?:-\d{8})?$", ()),)}


def normalize_reasoning_effort(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("reasoning_effort must be a native level or null")
    value = value.strip().lower()
    if not value:
        return None
    if value not in LEVELS:
        raise ValueError("Invalid reasoning_effort; choose one of: " + ", ".join(LEVELS))
    return value


ReasoningEffort = Annotated[
    Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
    BeforeValidator(normalize_reasoning_effort),
]
ReasoningEffortSource = Literal["prompt", "session", "default"]


def reasoning_effort_catalog() -> dict[str, Any]:
    return {
        "harness_levels": HARNESS_LEVELS,
        "harness_model_rules": {
            harness: [{"pattern": pattern, "levels": levels} for pattern, levels in rules]
            for harness, rules in HARNESS_MODEL_RULES.items()
        },
        "model_rules": [{"pattern": pattern, "levels": levels} for pattern, levels in MODEL_RULES],
    }


def reasoning_effort_capabilities(
    harness: str,
    model: str | None,
    route: str | None = None,
) -> dict[str, Any]:
    levels = list(HARNESS_LEVELS.get(harness, ()))
    known = False
    # Local/custom routes may serve unrelated weights under a familiar model name.
    if route != "local_openai":
        for pattern, supported in (*HARNESS_MODEL_RULES.get(harness, ()), *MODEL_RULES):
            if re.fullmatch(pattern, (model or "").lower()):
                levels = [level for level in levels if level in supported]
                known = True
                break
    if route == "openrouter":
        levels = [level for level in levels if level != "ultra"]
    return {"levels": levels, "model_support_known": known}


def validate_reasoning_effort(
    value: Any,
    *,
    harness: str,
    model: str | None,
    route: str | None = None,
) -> str | None:
    effort = normalize_reasoning_effort(value)
    if (
        effort is not None
        and effort not in reasoning_effort_capabilities(harness, model, route)["levels"]
    ):
        raise ValueError(
            f"Reasoning effort {effort!r} is not supported for {harness} model {model!r}"
        )
    return effort


def session_agent_harness(session: Any, config: Any = None) -> str:
    metadata = getattr(session, "metadata", {}) or {}
    return str(
        metadata.get("agent_provider")
        or metadata.get("agent_harness")
        or (
            "claude"
            if (metadata.get("model_provider_mode") or getattr(config, "model_provider_mode", None))
            == "anthropic"
            else "codex"
        )
    )


def validate_session_reasoning_effort(session: Any, config: Any = None) -> None:
    metadata = getattr(session, "metadata", {}) or {}
    validate_reasoning_effort(
        getattr(session, "reasoning_effort", None),
        harness=session_agent_harness(session, config),
        model=getattr(session, "model", None),
        route=metadata.get("model_provider_mode") or getattr(config, "model_provider_mode", None),
    )


@dataclass(frozen=True)
class ResolvedReasoningEffort:
    value: str | None
    source: ReasoningEffortSource

    def fields(self) -> dict[str, Any]:
        return {"reasoning_effort": self.value, "reasoning_effort_source": self.source}


def resolve_reasoning_effort(
    payload: Mapping[str, Any],
    *,
    session: Any,
    harness: str,
    model: str | None,
    route: str | None = None,
) -> ResolvedReasoningEffort:
    source = payload.get("reasoning_effort_source")
    effort = normalize_reasoning_effort(payload.get("reasoning_effort"))
    if source not in ("prompt", "session", "default"):
        if effort is not None:
            source = "prompt"
        elif getattr(session, "reasoning_effort", None) is not None:
            effort = session.reasoning_effort
            source = "session"
        else:
            effort = "high" if harness == "codex" else None
            source = "default"
    # The implicit legacy fallback must not make existing model selections invalid.
    if source != "default" or effort not in (None, "high"):
        validate_reasoning_effort(effort, harness=harness, model=model, route=route)
    return ResolvedReasoningEffort(effort, source)


def recorded_reasoning_effort(record: Any) -> dict[str, Any]:
    """Read the captured value and provenance together, including a captured null default."""
    sources = [
        getattr(record, key, None) for key in ("outputs", "resolved_config", "input_payload")
    ]
    for source in sources:
        if isinstance(source, Mapping) and source.get("reasoning_effort_source") in {
            "prompt",
            "session",
            "default",
        }:
            return {key: source.get(key) for key in ("reasoning_effort", "reasoning_effort_source")}
    # Legacy executions only have an authored override, or no setting at all.
    for source in sources[1:] + sources[:1]:
        if isinstance(source, Mapping) and source.get("reasoning_effort") is not None:
            return {"reasoning_effort": source["reasoning_effort"]}
    return {}
