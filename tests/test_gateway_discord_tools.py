from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def test_email_tools_require_and_return_mailbox_id(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.EMAIL_LIST_UNREAD,
                ToolName.EMAIL_GET_MESSAGE,
                ToolName.EMAIL_CREATE_DRAFT,
            ],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox", "drafts"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    context.email_service.provider.email_store.put(
        "msg-1",
        context.email_service.provider.email_store.model(
            message_id="msg-1",
            mailbox_id="primary",
            folder="inbox",
            sender="from@example.com",
            recipients=["to@example.com"],
            subject="hello",
            body_text="world",
        ),
    )

    server = create_server(context)

    listed = asyncio.run(
        server.call_tool(
            "email_list_unread",
            {"session_id": "session-1", "mailbox_id": "primary", "folder": "inbox", "limit": 10},
        )
    )
    assert listed[0]["mailbox_id"] == "primary"

    fetched = asyncio.run(
        server.call_tool(
            "email_get_message",
            {"session_id": "session-1", "mailbox_id": "primary", "message_id": "msg-1"},
        )
    )
    assert fetched["mailbox_id"] == "primary"

    draft = asyncio.run(
        server.call_tool(
            "email_create_draft",
            {
                "session_id": "session-1",
                "mailbox_id": "primary",
                "body": "draft body",
                "subject": "Re: hello",
            },
        )
    )
    assert draft["mailbox_id"] == "primary"
