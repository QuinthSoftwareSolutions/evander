from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.models import (
    ApprovalRequest,
    ApprovalStatus,
    DiscordCommandEnvelope,
    SessionManifest,
    ToolName,
)
from agent_sandbox.policy import PolicyEngine, PolicyError
from agent_sandbox.storage import JsonStore


def make_engine(tmp_path: Path) -> PolicyEngine:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    sessions = JsonStore(config.session_store_path, SessionManifest)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    return PolicyEngine(config, sessions, approvals)


def test_require_tool_rejects_unknown_tool(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    manifest = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.put("s1", manifest)

    try:
        engine.require_tool("s1", ToolName.DISCORD_POST_STATUS)
    except PolicyError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected PolicyError")


def test_require_approved_rejects_payload_mismatch(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    approval = ApprovalRequest(
        request_id="apr1",
        session_id="s1",
        action_type="send_email",
        payload_digest="abc",
        human_summary="test",
        status=ApprovalStatus.APPROVED,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.approval_store.put("apr1", approval)

    try:
        engine.require_approved("apr1", {"subject": "different"})
    except PolicyError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("Expected PolicyError")


def test_require_discord_command_source_allows_session_channel(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-unlisted",
        channel_id="chan-control",
        content="!status",
        metadata={"source": "discord_bot_api", "channel_id": "chan-control", "author_id": "user-unlisted"},
    )

    engine.require_discord_command_source(session, command)


def test_require_discord_command_source_allows_operator_user(tmp_path: Path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        discord_operator_user_allowlist=["user-operator"],
    )
    sessions = JsonStore(config.session_store_path, SessionManifest)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    engine = PolicyEngine(config, sessions, approvals)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-operator",
        channel_id="chan-dm",
        content="!run",
        metadata={"source": "discord_bot_api", "channel_id": "chan-dm", "author_id": "user-operator"},
    )

    engine.require_discord_command_source(session, command)


def test_require_discord_command_source_rejects_untrusted_source(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-operator",
        channel_id="chan-control",
        content="!run",
        metadata={"source": "stub", "channel_id": "chan-control", "author_id": "user-operator"},
    )

    try:
        engine.require_discord_command_source(session, command)
    except PolicyError as exc:
        assert "untrusted source" in str(exc)
    else:
        raise AssertionError("Expected PolicyError")
