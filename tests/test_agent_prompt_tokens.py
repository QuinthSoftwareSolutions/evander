from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_sandbox.agent_prompt_tokens import (
    AgentPromptTokenError,
    issue_agent_prompt_response_token,
    verify_agent_prompt_response_token,
)


def test_issue_and_verify_agent_prompt_token_round_trip() -> None:
    issued_at = datetime(2026, 4, 11, 0, 0, 0, tzinfo=timezone.utc)
    token, claims = issue_agent_prompt_response_token(
        secret="secret-1",
        message_id="m-1",
        sender_session_id="sender",
        target_session_id="target",
        issued_at=issued_at,
        ttl_seconds=60,
    )
    verified = verify_agent_prompt_response_token(
        token=token,
        secret="secret-1",
        expected_message_id="m-1",
        expected_target_session_id="target",
        now=issued_at + timedelta(seconds=30),
    )
    assert verified["nonce"]
    assert verified["issued_at"] == claims["issued_at"]
    assert verified["expires_at"] == claims["expires_at"]


def test_verify_agent_prompt_token_rejects_bad_signature() -> None:
    token, _ = issue_agent_prompt_response_token(
        secret="secret-1",
        message_id="m-1",
        sender_session_id="sender",
        target_session_id="target",
    )
    with pytest.raises(AgentPromptTokenError, match="signature is invalid"):
        verify_agent_prompt_response_token(
            token=token,
            secret="wrong-secret",
            expected_message_id="m-1",
            expected_target_session_id="target",
        )


def test_verify_agent_prompt_token_rejects_expired() -> None:
    issued_at = datetime(2026, 4, 11, 0, 0, 0, tzinfo=timezone.utc)
    token, _ = issue_agent_prompt_response_token(
        secret="secret-1",
        message_id="m-1",
        sender_session_id="sender",
        target_session_id="target",
        issued_at=issued_at,
        ttl_seconds=1,
    )
    with pytest.raises(AgentPromptTokenError, match="expired"):
        verify_agent_prompt_response_token(
            token=token,
            secret="secret-1",
            expected_message_id="m-1",
            expected_target_session_id="target",
            now=issued_at + timedelta(seconds=2),
        )
