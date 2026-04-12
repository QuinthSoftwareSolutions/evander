import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, TeamsMessageEnvelope, ToolName
from agent_sandbox.runtime import build_runtime
from agent_sandbox.teams_auth import JsonTeamsIdentityCredentialStore


class FakeTeamsService:
    def __init__(self, messages: list[TeamsMessageEnvelope]) -> None:
        self.messages = messages
        self.last_since_cursor: str | None = None
        self.post_calls: list[tuple[str, str, str, str | None]] = []

    def get_messages(self, since_cursor: str | None = None) -> list[TeamsMessageEnvelope]:
        self.last_since_cursor = since_cursor
        return self.messages

    def post_status(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        thread_id: str | None = None,
    ) -> dict[str, str | None]:
        self.post_calls.append((message, channel_target, session_id, thread_id))
        return {
            "status": "posted",
            "channel_target": channel_target,
            "team_id": "team-a",
            "channel_id": "channel-1",
            "thread_id": thread_id or "msg-root",
            "reply_to_message_id": thread_id,
            "session_id": session_id,
            "message_id": "teams-msg-1",
            "web_url": "https://teams.example/message",
        }


def make_context(tmp_path: Path, **kwargs: object):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        **kwargs,
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


def test_teams_get_messages_supports_limit_and_includes_session_authored_messages(
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
                content="oldest",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                    "author_id": "user-1",
                },
            ),
            TeamsMessageEnvelope(
                cursor="team-a/channel-1/msg-2",
                message_id="msg-2",
                author_id="agent-user",
                team_id="team-a",
                channel_id="channel-1",
                thread_id="msg-1",
                content="session-authored status",
                metadata={
                    "source": "teams_graph_api_status",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                    "author_id": "agent-user",
                    "session_id": "session-1",
                },
            ),
            TeamsMessageEnvelope(
                cursor="team-a/channel-1/msg-3",
                message_id="msg-3",
                author_id="user-3",
                team_id="team-a",
                channel_id="channel-1",
                thread_id="msg-1",
                content="newest",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                    "author_id": "user-3",
                },
            ),
        ]
    )
    server = create_server(context)

    result = call_tool(
        server,
        "teams_get_messages",
        {"session_id": "session-1", "limit": 2},
    )

    assert [entry["cursor"] for entry in result["messages"]] == [
        "team-a/channel-1/msg-2",
        "team-a/channel-1/msg-3",
    ]
    assert result["next_cursor"] == "team-a/channel-1/msg-3"


def test_teams_get_messages_includes_status_messages_from_other_sessions_in_same_channel(tmp_path: Path) -> None:
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
                cursor="team-a/channel-1/msg-2",
                message_id="msg-2",
                author_id="agent-user",
                team_id="team-a",
                channel_id="channel-1",
                thread_id="msg-1",
                content="foreign session status",
                metadata={
                    "source": "teams_graph_api_status",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                    "author_id": "agent-user",
                    "session_id": "session-2",
                },
            )
        ]
    )
    server = create_server(context)

    result = call_tool(
        server,
        "teams_get_messages",
        {"session_id": "session-1"},
    )

    assert [entry["cursor"] for entry in result["messages"]] == ["team-a/channel-1/msg-2"]
    assert result["next_cursor"] == "team-a/channel-1/msg-2"


def test_teams_post_status_posts_to_allowlisted_channel_and_thread(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_POST_STATUS],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_teams = FakeTeamsService(messages=[])
    context.teams_service = fake_teams
    server = create_server(context)

    result = call_tool(
        server,
        "teams_post_status",
        {
            "session_id": "session-1",
            "channel_target": "team-a/channel-1",
            "message": "Host relay is healthy.",
            "thread_id": "msg-root",
        },
    )

    assert result["status"] == "posted"
    assert fake_teams.post_calls == [
        ("Host relay is healthy.", "team-a/channel-1", "session-1", "msg-root"),
    ]


def test_teams_post_status_rejects_non_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_POST_STATUS],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.teams_service = FakeTeamsService(messages=[])
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowlisted"):
        asyncio.run(
            server.call_tool(
                "teams_post_status",
                {
                    "session_id": "session-1",
                    "channel_target": "team-b/channel-9",
                    "message": "This should be denied.",
                },
            )
        )


def test_teams_post_status_audits_adapter_http_error(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_POST_STATUS],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_teams = FakeTeamsService(messages=[])
    fake_teams.post_status = lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("teams timeout"))
    context.teams_service = fake_teams
    server = create_server(context)

    with pytest.raises(ToolError, match="teams timeout"):
        asyncio.run(
            server.call_tool(
                "teams_post_status",
                {
                    "session_id": "session-1",
                    "channel_target": "team-a/channel-1",
                    "message": "relay health check",
                },
            )
        )

    events = read_audit_events(context.config.audit_log_path)
    assert events[-1]["decision"] == "error"
    assert events[-1]["tool_name"] == "teams_post_status"
    assert events[-1]["result"] == {
        "error": "teams timeout",
        "error_class": "adapter_http_error",
    }


def test_teams_get_messages_fails_clearly_when_delegated_credentials_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAPH_TEAMS_ACCESS_TOKEN", raising=False)
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        teams_backend="delegated_graph_api",
        teams_delegated_identity_id="root-identity",
        teams_delegated_client_id="client-123",
    )
    context = build_runtime(config)
    JsonTeamsIdentityCredentialStore(
        config.teams_delegated_credential_store_path,
        "root-identity",
    ).clear()
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="No Teams delegated credentials are available"):
        asyncio.run(
            server.call_tool(
                "teams_get_messages",
                {"session_id": "session-1"},
            )
        )

    events = read_audit_events(context.config.audit_log_path)
    assert events[-1]["decision"] == "error"
    assert events[-1]["tool_name"] == "teams_get_messages"
    assert events[-1]["result"]["error_class"] == "adapter_http_error"
