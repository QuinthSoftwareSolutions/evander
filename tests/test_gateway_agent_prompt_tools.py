import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, ToolName
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


def write_provider_overlay(context, directory_path: str, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(context.config.directories_store_path)
    directory_store.ensure_root()
    service = DirectoryService(directory_store)
    current = "root"
    for segment in [part for part in directory_path.split("/")[1:] if part]:
        next_path = f"{current}/{segment}"
        try:
            service.show(next_path)
        except ValueError:
            service.create(parent=current, name=segment)
        current = next_path
    DirectoryConfigStore(directory_store, runtime_dir=context.config.runtime_dir).write(
        directory_path,
        domain="providers",
        payload=payload,
    )


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

    inbox_result = call_tool(
        server,
        "list_pending_agent_prompts",
        {
            "session_id": "target",
        },
    )
    assert len(inbox_result["prompts"]) == 1
    fetched_prompt = inbox_result["prompts"][0]
    assert fetched_prompt["message_id"] == prompt_result["message_id"]
    assert fetched_prompt["prompt"] == "Please run tests and report failures."
    assert fetched_prompt["metadata"] == {"ticket": "123"}
    assert fetched_prompt["response_token"]

    respond_result = call_tool(
        server,
        "respond_to_agent",
        {
            "session_id": "target",
            "message_id": fetched_prompt["message_id"],
            "response_token": fetched_prompt["response_token"],
            "response_text": "All tests pass.",
        },
    )
    assert respond_result["status"] == "responded"
    assert respond_result["message_id"] == fetched_prompt["message_id"]
    assert respond_result["delivered_at"] is not None

    with pytest.raises(ToolError, match="invalid, already consumed"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "target",
                "message_id": fetched_prompt["message_id"],
                "response_token": fetched_prompt["response_token"],
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

    with pytest.raises(ToolError, match="outside the session directory scope"):
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

    with pytest.raises(ToolError, match="not allowed"):
        call_tool(
            server,
            "prompt_agent",
            {
                "session_id": "sender",
                "target_session_id": "target",
                "prompt": "No permission",
            },
        )

    with pytest.raises(ToolError, match="not allowed"):
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

    with pytest.raises(ToolError, match="not allowed"):
        call_tool(
            server,
            "list_pending_agent_prompts",
            {
                "session_id": "target",
            },
        )


def test_prompt_agent_rejects_target_without_respond_permission(tmp_path: Path) -> None:
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
            allowed_tools=[],
            directory_path="root/alpha",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="respond_to_agent is not allowed"):
        call_tool(
            server,
            "prompt_agent",
            {
                "session_id": "sender",
                "target_session_id": "target",
                "prompt": "This should fail fast.",
            },
        )


def test_list_pending_agent_prompts_reuses_issued_token_between_polls(tmp_path: Path) -> None:
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

    call_tool(
        server,
        "prompt_agent",
        {
            "session_id": "sender",
            "target_session_id": "target",
            "prompt": "Keep the token stable.",
        },
    )

    first = call_tool(server, "list_pending_agent_prompts", {"session_id": "target"})["prompts"][0]
    second = call_tool(server, "list_pending_agent_prompts", {"session_id": "target"})["prompts"][0]

    assert first["message_id"] == second["message_id"]
    assert first["response_token"] == second["response_token"]

    respond_result = call_tool(
        server,
        "respond_to_agent",
        {
            "session_id": "target",
            "message_id": first["message_id"],
            "response_token": first["response_token"],
            "response_text": "First poll token still works.",
        },
    )
    assert respond_result["status"] == "responded"


def test_prompt_agent_honors_idempotency_key_for_retries(tmp_path: Path) -> None:
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

    first = call_tool(
        server,
        "prompt_agent",
        {
            "session_id": "sender",
            "target_session_id": "target",
            "prompt": "Retry-safe prompt.",
            "metadata": {"attempt": 1},
            "idempotency_key": "retry-safe-key",
        },
    )
    second = call_tool(
        server,
        "prompt_agent",
        {
            "session_id": "sender",
            "target_session_id": "target",
            "prompt": "Retry-safe prompt.",
            "metadata": {"attempt": 1},
            "idempotency_key": "retry-safe-key",
        },
    )

    assert second["message_id"] == first["message_id"]
    assert second["response_token"] == first["response_token"]

    inbox = call_tool(server, "list_pending_agent_prompts", {"session_id": "target"})
    assert len(inbox["prompts"]) == 1


def test_prompt_agent_rejects_idempotency_key_reuse_for_different_request(tmp_path: Path) -> None:
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

    call_tool(
        server,
        "prompt_agent",
        {
            "session_id": "sender",
            "target_session_id": "target",
            "prompt": "First prompt.",
            "idempotency_key": "retry-safe-key",
        },
    )

    with pytest.raises(ToolError, match="idempotency_key is already bound to a different prompt"):
        call_tool(
            server,
            "prompt_agent",
            {
                "session_id": "sender",
                "target_session_id": "target",
                "prompt": "Different prompt.",
                "idempotency_key": "retry-safe-key",
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
    fetched_prompt = call_tool(server, "list_pending_agent_prompts", {"session_id": "target"})["prompts"][0]
    call_tool(
        server,
        "respond_to_agent",
        {
            "session_id": "target",
            "message_id": fetched_prompt["message_id"],
            "response_token": fetched_prompt["response_token"],
            "response_text": "Acknowledged.",
        },
    )

    events = read_audit_events(tmp_path / "audit.jsonl")
    prompt_event = next(event for event in events if event.get("tool_name") == ToolName.PROMPT_AGENT.value)
    inbox_event = next(event for event in events if event.get("tool_name") == ToolName.LIST_PENDING_AGENT_PROMPTS.value)
    respond_event = next(event for event in events if event.get("tool_name") == ToolName.RESPOND_TO_AGENT.value)
    assert prompt_event["result"]["response_token"] == "<redacted>"
    assert inbox_event["result"]["count"] == 1
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
    fetched_prompt = call_tool(server, "list_pending_agent_prompts", {"session_id": "target"})["prompts"][0]
    time.sleep(1.2)
    with pytest.raises(Exception, match="expired"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "target",
                "message_id": fetched_prompt["message_id"],
                "response_token": fetched_prompt["response_token"],
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
    fetched_prompt = call_tool(server, "list_pending_agent_prompts", {"session_id": "target"})["prompts"][0]
    with pytest.raises(Exception, match="target_session_id claim does not match request session"):
        call_tool(
            server,
            "respond_to_agent",
            {
                "session_id": "wrong-target",
                "message_id": fetched_prompt["message_id"],
                "response_token": fetched_prompt["response_token"],
                "response_text": "wrong",
            },
        )


def test_list_pending_agent_prompts_uses_directory_scoped_token_policy(tmp_path: Path) -> None:
    os.environ["TEST_AGENT_PROMPT_TOKEN_SECRET"] = "root-secret"
    os.environ["TEAM_ALPHA_AGENT_PROMPT_TOKEN_SECRET"] = "alpha-secret"
    context = make_context(tmp_path)
    write_provider_overlay(
        context,
        "root/alpha",
        {
            "agent_prompt_token_secret_env": "TEAM_ALPHA_AGENT_PROMPT_TOKEN_SECRET",
            "agent_prompt_token_ttl_seconds": 600,
        },
    )
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
    direct_respond = call_tool(
        server,
        "respond_to_agent",
        {
            "session_id": "target",
            "message_id": prompt_result["message_id"],
            "response_token": prompt_result["response_token"],
            "response_text": "prompt_agent used directory override",
        },
    )
    assert direct_respond["status"] == "responded"

    call_tool(
        server,
        "prompt_agent",
        {"session_id": "sender", "target_session_id": "target", "prompt": "hello again"},
    )

    fetched_prompt = call_tool(server, "list_pending_agent_prompts", {"session_id": "target"})["prompts"][0]
    claims = fetched_prompt["response_token_claims"]
    ttl_seconds = claims["expires_at"] - claims["issued_at"]
    assert ttl_seconds == 600

    respond_result = call_tool(
        server,
        "respond_to_agent",
        {
            "session_id": "target",
            "message_id": fetched_prompt["message_id"],
            "response_token": fetched_prompt["response_token"],
            "response_text": "using directory override",
        },
    )
    assert respond_result["status"] == "responded"
