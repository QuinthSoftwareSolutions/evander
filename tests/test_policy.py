from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-control",
            "author_id": "user-unlisted",
        },
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
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-dm",
            "author_id": "user-operator",
        },
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


def test_require_discord_command_source_rejects_missing_channel_metadata(tmp_path: Path) -> None:
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
        metadata={"source": "discord_bot_api", "channel_id": "", "author_id": "user-operator"},
    )

    with pytest.raises(PolicyError, match="missing required source metadata"):
        engine.require_discord_command_source(session, command)


def test_require_discord_command_source_rejects_missing_author_metadata(tmp_path: Path) -> None:
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
        metadata={"source": "discord_bot_api", "channel_id": "chan-control", "author_id": "  "},
    )

    with pytest.raises(PolicyError, match="missing required source metadata"):
        engine.require_discord_command_source(session, command)


def test_require_discord_command_source_rejects_non_allowlisted_channel_and_author(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-unknown",
        channel_id="chan-random",
        content="!run",
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-random",
            "author_id": "user-unknown",
        },
    )

    with pytest.raises(PolicyError, match="source is not allowlisted"):
        engine.require_discord_command_source(session, command)


def test_require_discord_channel_rejects_unrestricted_target(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_POST_STATUS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="not allowlisted"):
        engine.require_discord_channel(session, "chan-unrestricted")


def test_require_discord_command_source_rejects_unrestricted_polling_target(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-unknown",
        channel_id="chan-unrestricted",
        content="!run",
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-unrestricted",
            "author_id": "user-unknown",
        },
    )

    with pytest.raises(PolicyError, match="source is not allowlisted"):
        engine.require_discord_command_source(session, command)
def test_require_email_mailbox_and_folder_for_mailbox(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
        email_mailbox_allowlist=["primary"],
        email_folder_allowlist_by_mailbox={"primary": ["inbox", "drafts"]},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    engine.require_email_mailbox(session, "primary")
    engine.require_email_folder_for_mailbox(session, "primary", "inbox")
    with pytest.raises(PolicyError, match="mailbox is not allowlisted"):
        engine.require_email_mailbox(session, "secondary")
    with pytest.raises(PolicyError, match="folder is not allowlisted"):
        engine.require_email_folder_for_mailbox(session, "primary", "sent")


def test_require_tool_for_feedback_tools_requires_stream_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.put(
        "s1",
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.HAS_PENDING_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(PolicyError, match="missing metadata.pr_feedback_streams"):
        engine.require_tool("s1", ToolName.HAS_PENDING_FEEDBACK)


def test_require_tool_for_feedback_tools_accepts_valid_stream_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.put(
        "s1",
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.LIST_UNREAD_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    )

    session = engine.require_tool("s1", ToolName.LIST_UNREAD_FEEDBACK)
    streams = engine.require_pr_feedback_streams(session)
    assert streams == [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]


def test_require_pr_feedback_stream_enforces_allowlist(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
    )

    engine.require_pr_feedback_stream(session, provider="github", repo="org/repo", pr_id="17")
    with pytest.raises(PolicyError, match="is not allowlisted"):
        engine.require_pr_feedback_stream(session, provider="github", repo="org/repo", pr_id="18")


def test_require_draft_recipients_enforces_address_and_domain_allowlists(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_CREATE_DRAFT],
        email_draft_recipient_allowlist=["approved@example.com"],
        email_draft_domain_allowlist=["example.com"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    engine.require_draft_recipients(session, ["approved@example.com"])
    with pytest.raises(PolicyError, match="Draft recipients are not allowlisted"):
        engine.require_draft_recipients(session, ["denied@example.com"])
    with pytest.raises(PolicyError, match="Draft recipient domains are not allowlisted"):
        engine.require_draft_recipients(
            SessionManifest(
                session_id="s2",
                allowed_tools=[ToolName.EMAIL_CREATE_DRAFT],
                email_draft_domain_allowlist=["example.com"],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            ["person@other.com"],
        )
