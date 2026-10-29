from __future__ import annotations

import pytest

from evander.git_pr_notes import (
    AGENT_PR_NOTES_REF,
    agent_pr_notes_ref_for_session,
    is_agent_pr_notes_ref,
)


def test_agent_pr_notes_ref_for_session_uses_stable_12_char_sha256_prefix() -> None:
    assert agent_pr_notes_ref_for_session("session-1") == "refs/notes/agent-pr/84097828fc31"


def test_agent_pr_notes_ref_for_session_rejects_empty_session_id() -> None:
    with pytest.raises(ValueError, match="session_id is required"):
        agent_pr_notes_ref_for_session(" ")


def test_is_agent_pr_notes_ref_accepts_legacy_and_per_session_refs() -> None:
    assert is_agent_pr_notes_ref(AGENT_PR_NOTES_REF) is True
    assert is_agent_pr_notes_ref("refs/notes/agent-pr/84097828fc31") is True
    assert is_agent_pr_notes_ref("refs/notes/agent-pr-cache/repo") is False
