import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.policy import PolicyError
from agent_sandbox.runtime import build_runtime


def make_context(tmp_path: Path):
    os.environ["TEST_AGENT_PROMPT_TOKEN_SECRET"] = "test-agent-prompt-secret"
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        agent_prompt_token_secret_env="TEST_AGENT_PROMPT_TOKEN_SECRET",
        agent_prompt_token_ttl_seconds=60,
    )
    return build_runtime(config)


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def read_audit_events(path: Path) -> list[dict[str, object]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_prompt_agent_and_respond_to_agent_round_trip(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "sender",
        SessionManifest(
            session_id="sender",
            allowed_tools=[ToolName.PROMPT_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "target",
        SessionManifest(
            session_id="target",
            allowed_tools=[ToolName.RESPOND_TO_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    prompt_result = call_tool(
        server,
        "prompt_agent",
        {
            "session_id": "sender",
            "target_session_id": "target",
            "prompt": "Please run tests and report failures.",
            "metadata": {"ticket": "123"},
        },
    )
    assert prompt_result["target_session_id"] == "target"
    assert prompt_result["status"] == "unread"
    assert prompt_result["response_token"]

    respond_result = call_tool(
        server,
        "respond_to_agent",
        {
            "session_id": "target",
            "message_id": prompt_result["message_id"],
            "response_token": prompt_result["response_token"],
            "response_text": "All tests pass.",
        },
    )
    assert respond_result["status"] == "responded"
    assert respond_result["message_id"] == prompt_result["message_id"]
    assert respond_result["delivered_at"] is not None

    with pytest.raises(PolicyError, match="invalid, already consumed"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "target",
                "message_id": prompt_result["message_id"],
                "response_token": prompt_result["response_token"],
                "response_text": "Replay should fail",
            },
        )


def test_prompt_agent_rejects_cross_directory_dispatch(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "sender",
        SessionManifest(
            session_id="sender",
            allowed_tools=[ToolName.PROMPT_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "target",
        SessionManifest(
            session_id="target",
            allowed_tools=[ToolName.RESPOND_TO_AGENT],
            directory_path="root/beta",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(PolicyError, match="outside the session directory scope"):
        call_tool(
            server,
            "prompt_agent",
            {
                "session_id": "sender",
                "target_session_id": "target",
                "prompt": "This should be blocked.",
            },
        )


def test_agent_prompt_tools_enforce_tool_permissions(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "sender",
        SessionManifest(
            session_id="sender",
            allowed_tools=[],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "target",
        SessionManifest(
            session_id="target",
            allowed_tools=[],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(PolicyError, match="does not allow tool"):
        call_tool(
            server,
            "prompt_agent",
            {
                "session_id": "sender",
                "target_session_id": "target",
                "prompt": "No permission",
            },
        )

    with pytest.raises(PolicyError, match="does not allow tool"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "target",
                "message_id": "message-id",
                "response_token": "invalid-token",
                "response_text": "No permission",
            },
        )


def test_agent_prompt_tools_redact_response_token_in_audit_logs(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "sender",
        SessionManifest(
            session_id="sender",
            allowed_tools=[ToolName.PROMPT_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "target",
        SessionManifest(
            session_id="target",
            allowed_tools=[ToolName.RESPOND_TO_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    prompt_result = call_tool(
        server,
        "prompt_agent",
        {
            "session_id": "sender",
            "target_session_id": "target",
            "prompt": "Token redaction check.",
        },
    )
    call_tool(
        server,
        "respond_to_agent",
        {
            "session_id": "target",
            "message_id": prompt_result["message_id"],
            "response_token": prompt_result["response_token"],
            "response_text": "Acknowledged.",
        },
    )

    events = read_audit_events(tmp_path / "audit.jsonl")
    prompt_event = next(event for event in events if event.get("tool_name") == ToolName.PROMPT_AGENT.value)
    respond_event = next(event for event in events if event.get("tool_name") == ToolName.RESPOND_TO_AGENT.value)
    assert prompt_event["result"]["response_token"] == "<redacted>"
    assert respond_event["args"]["response_token"] == "<redacted>"
    assert respond_event["result"]["response_token"] == "<redacted>"


def test_respond_to_agent_rejects_bad_signature_token(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "sender",
        SessionManifest(
            session_id="sender",
            allowed_tools=[ToolName.PROMPT_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "target",
        SessionManifest(
            session_id="target",
            allowed_tools=[ToolName.RESPOND_TO_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    prompt_result = call_tool(
        server,
        "prompt_agent",
        {"session_id": "sender", "target_session_id": "target", "prompt": "hello"},
    )
    tampered = f"{prompt_result['response_token']}x"
    with pytest.raises(Exception, match="signature is invalid"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "target",
                "message_id": prompt_result["message_id"],
                "response_token": tampered,
                "response_text": "fail",
            },
        )


def test_respond_to_agent_rejects_expired_token(tmp_path: Path) -> None:
    os.environ["TEST_AGENT_PROMPT_TOKEN_SECRET"] = "test-agent-prompt-secret"
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        agent_prompt_token_secret_env="TEST_AGENT_PROMPT_TOKEN_SECRET",
        agent_prompt_token_ttl_seconds=1,
    )
    context = build_runtime(config)
    context.sessions.put(
        "sender",
        SessionManifest(
            session_id="sender",
            allowed_tools=[ToolName.PROMPT_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "target",
        SessionManifest(
            session_id="target",
            allowed_tools=[ToolName.RESPOND_TO_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    prompt_result = call_tool(
        server,
        "prompt_agent",
        {"session_id": "sender", "target_session_id": "target", "prompt": "hello"},
    )
    time.sleep(1.2)
    with pytest.raises(Exception, match="expired"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "target",
                "message_id": prompt_result["message_id"],
                "response_token": prompt_result["response_token"],
                "response_text": "late",
            },
        )


def test_respond_to_agent_rejects_wrong_session_binding(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "sender",
        SessionManifest(
            session_id="sender",
            allowed_tools=[ToolName.PROMPT_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "target",
        SessionManifest(
            session_id="target",
            allowed_tools=[ToolName.RESPOND_TO_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "wrong-target",
        SessionManifest(
            session_id="wrong-target",
            allowed_tools=[ToolName.RESPOND_TO_AGENT],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    prompt_result = call_tool(
        server,
        "prompt_agent",
        {"session_id": "sender", "target_session_id": "target", "prompt": "hello"},
    )
    with pytest.raises(Exception, match="target_session_id claim does not match request session"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "wrong-target",
                "message_id": prompt_result["message_id"],
                "response_token": prompt_result["response_token"],
                "response_text": "wrong",
            },
        )
