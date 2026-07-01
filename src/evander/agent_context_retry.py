from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONTEXT_EXHAUSTION_FAILURE_KIND = "context_exhausted"
CONTEXT_RETRY_MODE_REDUCED = "reduced_context"
CODEX_CONTEXT_LIMIT_SUSPECTED_REASON = "codex_context_limit_suspected"

CONTEXT_EXHAUSTION_REASONS = frozenset(
    {
        CONTEXT_EXHAUSTION_FAILURE_KIND,
        "context_exhaustion",
        "context_limit",
        "context_limit_exceeded",
        CODEX_CONTEXT_LIMIT_SUSPECTED_REASON,
    }
)

DEFAULT_CONTEXT_RETRY_MAX_ATTEMPTS = 1
DEFAULT_CONTEXT_RETRY_TARGET_CHARS = 16_000
MIN_CONTEXT_RETRY_TARGET_CHARS = 4_000
_RETRY_PREAMBLE_TARGET_CHARS = 1_200


def context_exhaustion_reason(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("failure_kind", "failure_reason", "reason", "error_kind", "error_reason"):
        reason = _normalized_reason(payload.get(key))
        if reason in CONTEXT_EXHAUSTION_REASONS:
            return reason
    codex_outcome = payload.get("codex_outcome")
    if isinstance(codex_outcome, Mapping):
        reason = _normalized_reason(codex_outcome.get("reason"))
        if reason in CONTEXT_EXHAUSTION_REASONS:
            return reason
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        return context_exhaustion_reason(metadata)
    return None


def context_retry_enabled(value: object, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def context_retry_max_attempts(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_RETRY_MAX_ATTEMPTS


def context_retry_target_chars(value: object) -> int:
    try:
        target = int(value)
    except (TypeError, ValueError):
        target = DEFAULT_CONTEXT_RETRY_TARGET_CHARS
    return max(MIN_CONTEXT_RETRY_TARGET_CHARS, target)


def build_reduced_context_retry_prompt(
    *,
    original_prompt: str,
    failure_message: str | None = None,
    failure_reason: str | None = None,
    target_chars: int = DEFAULT_CONTEXT_RETRY_TARGET_CHARS,
    reduced_prompt: str | None = None,
) -> str:
    normalized_target = context_retry_target_chars(target_chars)
    prompt_source = str(reduced_prompt or "").strip() or str(original_prompt or "").strip()
    preamble = _retry_preamble(
        failure_message=failure_message,
        failure_reason=failure_reason,
    )
    available = max(1_000, normalized_target - len(preamble) - 2)
    compact = _compact_prompt(prompt_source, target_chars=available)
    return f"{preamble}\n\n{compact}".strip()


def _retry_preamble(*, failure_message: str | None, failure_reason: str | None) -> str:
    lines = [
        "Previous agent run hit the model context limit before producing a final response.",
        "Continue the same task using this reduced context.",
        "Avoid rediscovering tools unless necessary; if critical context is missing, say exactly what is missing.",
    ]
    reason = str(failure_reason or "").strip()
    message = str(failure_message or "").strip()
    if reason or message:
        detail = "; ".join(part for part in (reason, message) if part)
        lines.append(f"Failure detail: {_trim_text(detail, _RETRY_PREAMBLE_TARGET_CHARS)}")
    return "\n".join(lines)


def _compact_prompt(prompt: str, *, target_chars: int) -> str:
    text = str(prompt or "").strip()
    if len(text) <= target_chars:
        return text
    if target_chars <= 1_000:
        return _trim_text(text, target_chars)

    marker = "\n\n[... middle context omitted for reduced-context retry ...]\n\n"
    remaining = max(1, target_chars - len(marker))
    head_chars = max(500, int(remaining * 0.45))
    tail_chars = max(500, remaining - head_chars)
    if head_chars + tail_chars + len(marker) > target_chars:
        tail_chars = max(1, target_chars - len(marker) - head_chars)
    head = text[:head_chars].rstrip()
    tail = text[-tail_chars:].lstrip()
    return f"{head}{marker}{tail}".strip()


def _trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _normalized_reason(value: object) -> str:
    return str(value or "").strip().casefold().replace("-", "_")
