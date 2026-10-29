"""Interpret startup objectives consistently across voice projections and bridges."""

from __future__ import annotations

import re


def voice_task_looks_actionable(prompt: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(prompt or "").strip())
    if not normalized:
        return False
    lowered = normalized.casefold().rstrip(".!?")
    if len(lowered.split()) < 5:
        return False
    task_markers = {
        "ask",
        "tell",
        "say",
        "explain",
        "summarize",
        "request",
        "confirm",
        "verify",
        "collect",
        "give",
        "open",
        "start",
        "begin",
        "discuss",
        "approve",
        "approval",
        "script",
        "objective",
        "prompt",
    }
    if any(marker in lowered.split() for marker in task_markers):
        return True
    if re.fullmatch(
        r"(?:please\s+)?(?:call|phone|contact|reach out to)\s+[\w\s'.-]+",
        lowered,
    ):
        return False
    return False
