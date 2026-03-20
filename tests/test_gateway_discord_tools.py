from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import DiscordCommandEnvelope, SessionManifest, ToolName
from agent_sandbox.policy import PolicyError
from agent_sandbox.runtime import build_runtime


class FakeDiscordService:
    def __init__(self, commands: list[DiscordCommandEnvelope]) -> None:
        self.commands = commands
        self.last_since_cursor: str | None = None
        self.post_calls: list[tuple[str, str, str]] = []

    def get_commands(self, since_cursor: str | None = None) -> list[DiscordCommandEnvelope]:
        self.last_since_cursor = since_cursor
        return self.commands

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, str]:
        self.post_calls.append((message, channel_id, session_id))
        return {
            "status": "posted",
            "message_id": "discord-msg-1",
            "channel_id": channel_id,
            "session_id": session_id,
        }


def make_context(tmp_path: Path):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    return build_runtime(config)


def read_audit_events(audit_log_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in audit_log_path.read_text(encoding="utf-8").splitlines() if line]


def test_discord_get_commands_passes_since_cursor_and_filters_unallowlisted_sources(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(
        commands=[
            DiscordCommandEnvelope(
                command_id="cmd-1",
                author_id="user-1",
                channel_id="chan-allowlisted",
                content="!status",
                metadata={
                    "source": "discord_bot_api",
                    "channel_id": "chan-allowlisted",
                    "author_id": "user-1",
                },
            ),
            DiscordCommandEnvelope(
                command_id="cmd-2",
                author_id="user-2",
                channel_id="chan-blocked",
                content="!status",
                metadata={
                    "source": "discord_bot_api",
                    "channel_id": "chan-blocked",
                    "author_id": "user-2",
                },
            ),
        ]
    )
    context.discord_service = fake_discord
    server = create_server(context)

    result = asyncio.run(
        server.call_tool(
            "discord_get_commands",
            {"session_id": "session-1", "since_cursor": "cursor-42"},
        )
    )

    assert fake_discord.last_since_cursor == "cursor-42"
    assert isinstance(result, list)
    assert [entry["command_id"] for entry in result] == ["cmd-1"]


def test_discord_post_status_posts_to_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    context.discord_service = fake_discord
    server = create_server(context)

    result = asyncio.run(
        server.call_tool(
            "discord_post_status",
            {
                "session_id": "session-1",
                "channel_id": "chan-allowlisted",
                "message": "Host relay is healthy.",
            },
        )
    )

    assert result["status"] == "posted"
    assert fake_discord.post_calls == [
        ("Host relay is healthy.", "chan-allowlisted", "session-1"),
    ]


def test_discord_post_status_rejects_non_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.discord_service = FakeDiscordService(commands=[])
    server = create_server(context)

    with pytest.raises(PolicyError, match="not allowlisted"):
        asyncio.run(
            server.call_tool(
                "discord_post_status",
                {
                    "session_id": "session-1",
                    "channel_id": "chan-blocked",
                    "message": "This should be denied.",
                },
            )
        )


def test_discord_get_commands_audits_adapter_http_error(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    fake_discord.get_commands = lambda since_cursor=None: (_ for _ in ()).throw(
        httpx.ReadTimeout("discord timeout")
    )
    context.discord_service = fake_discord
    server = create_server(context)

    with pytest.raises(httpx.ReadTimeout, match="discord timeout"):
        asyncio.run(
            server.call_tool(
                "discord_get_commands",
                {"session_id": "session-1", "since_cursor": "cursor-42"},
            )
        )

    events = read_audit_events(context.config.audit_log_path)
    assert events[-1]["decision"] == "error"
    assert events[-1]["tool_name"] == "discord_get_commands"
    assert events[-1]["result"] == {
        "error": "discord timeout",
        "error_class": "adapter_http_error",
    }


def test_discord_post_status_audits_unexpected_adapter_error(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    fake_discord.post_status = lambda message, channel_id, session_id: (_ for _ in ()).throw(
        RuntimeError("discord adapter exploded")
    )
    context.discord_service = fake_discord
    server = create_server(context)

    with pytest.raises(RuntimeError, match="discord adapter exploded"):
        asyncio.run(
            server.call_tool(
                "discord_post_status",
                {
                    "session_id": "session-1",
                    "channel_id": "chan-allowlisted",
                    "message": "Host relay is healthy.",
                },
            )
        )

    events = read_audit_events(context.config.audit_log_path)
    assert events[-1]["decision"] == "error"
    assert events[-1]["tool_name"] == "discord_post_status"
    assert events[-1]["result"] == {
        "error": "discord adapter exploded",
        "error_class": "unexpected_error",
    }


def test_discord_get_commands_emits_denied_and_allowed_audit_events(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.discord_service = FakeDiscordService(
        commands=[
            DiscordCommandEnvelope(
                command_id="cmd-1",
                author_id="user-1",
                channel_id="chan-blocked",
                content="!status",
                metadata={
                    "source": "discord_bot_api",
                    "channel_id": "chan-blocked",
                    "author_id": "user-1",
                },
            )
        ]
    )
    server = create_server(context)

    result = asyncio.run(server.call_tool("discord_get_commands", {"session_id": "session-1"}))

    assert result == []
    events = read_audit_events(context.config.audit_log_path)
    decisions = [event["decision"] for event in events]
    assert decisions == ["denied", "allowed"]
