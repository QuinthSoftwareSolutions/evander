from __future__ import annotations

from agent_sandbox.agent_context_retry import (
    build_reduced_context_retry_prompt,
    context_exhaustion_reason,
)


def test_context_exhaustion_reason_detects_codex_outcome() -> None:
    assert (
        context_exhaustion_reason(
            {"codex_outcome": {"status": "failed", "reason": "codex_context_limit_suspected"}}
        )
        == "codex_context_limit_suspected"
    )


def test_reduced_context_retry_prompt_caps_large_prompt_and_preserves_edges() -> None:
    original = "Important task instructions\n" + ("middle history\n" * 1_000) + "Final action"

    prompt = build_reduced_context_retry_prompt(
        original_prompt=original,
        failure_reason="codex_context_limit_suspected",
        failure_message="Codex ended without a final agent message.",
        target_chars=4_000,
    )

    assert len(prompt) <= 4_000
    assert "Previous agent run hit the model context limit" in prompt
    assert "Important task instructions" in prompt
    assert "Final action" in prompt
    assert "middle context omitted" in prompt


def test_reduced_context_retry_prompt_prefers_explicit_reduced_prompt() -> None:
    prompt = build_reduced_context_retry_prompt(
        original_prompt="Original prompt that should not be used",
        reduced_prompt="Compact task state",
        target_chars=4_000,
    )

    assert "Compact task state" in prompt
    assert "Original prompt" not in prompt
