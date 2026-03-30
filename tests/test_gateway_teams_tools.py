from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, TeamsMessageEnvelope, ToolName
from agent_sandbox.runtime import build_runtime


class FakeTeamsService:
    def __init__(self, messages: list[TeamsMessageEnvelope]) -> None:
        self.messages = messages
        self.last_since_cursor: str | None = None

    def get_messages(self, since_cursor: str | None = None) -> list[TeamsMessageEnvelope]:
        self.last_since_cursor = since_cursor
        return self.messages


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


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def test_teams_get_messages_passes_since_cursor_and_filters_unallowlisted_sources(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.teams_service = FakeTeamsService(
        [
            TeamsMessageEnvelope(
                cursor="team-a/channel-1/msg-1",
                message_id="msg-1",
                author_id="user-1",
                team_id="team-a",
                channel_id="channel-1",
                thread_id="msg-1",
                content="allowed",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                    "author_id": "user-1",
                },
            ),
            TeamsMessageEnvelope(
                cursor="team-b/channel-9/msg-9",
                message_id="msg-9",
                author_id="user-9",
                team_id="team-b",
                channel_id="channel-9",
                thread_id="msg-9",
                content="blocked",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-b",
                    "channel_id": "channel-9",
                    "author_id": "user-9",
                },
            ),
        ]
    )
    server = create_server(context)

    result = call_tool(
        server,
        "teams_get_messages",
        {"session_id": "session-1", "since_cursor": "team-a/channel-1/msg-0"},
    )

    assert context.teams_service.last_since_cursor == "team-a/channel-1/msg-0"
    assert [entry["cursor"] for entry in result["messages"]] == ["team-a/channel-1/msg-1"]
    assert result["next_cursor"] == "team-b/channel-9/msg-9"
