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
from agent_sandbox.messaging.teams_auth import JsonTeamsIdentityCredentialStore


class FakeTeamsService:
    def __init__(self, messages: list[TeamsMessageEnvelope]) -> None:
        self.messages = messages
        self.last_since_cursor: str | None = None
        self.last_before_cursor: str | None = None
        self.last_limit: int | None = None
        self.post_calls: list[tuple[str, str, str, str | None]] = []
        self.reaction_calls: list[tuple[str, str, str, str]] = []
        self.reaction_error_by_message_id: dict[str, Exception] = {}
        self.read_reaction_emoji = "👀"

    def get_messages(
        self,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
    ) -> list[TeamsMessageEnvelope]:
        self.last_since_cursor = since_cursor
        self.last_before_cursor = before_cursor
        self.last_limit = limit
        if since_cursor is not None and before_cursor is not None:
            raise ValueError("since_cursor and before_cursor are mutually exclusive")
        if before_cursor is not None:
            older = [message for message in self.messages if message.cursor < before_cursor]
            return older[-limit:] if limit is not None else older
        if since_cursor is None:
            return self.messages[-limit:] if limit is not None else self.messages
        seen_cursor = False
        filtered: list[TeamsMessageEnvelope] = []
        for message in self.messages:
            if seen_cursor:
                filtered.append(message)
                continue
            if message.cursor == since_cursor:
                seen_cursor = True
        if not seen_cursor:
            return self.messages[-limit:] if limit is not None else self.messages
        return filtered[:limit] if limit is not None else filtered

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

    def add_reaction(
        self,
        channel_target: str,
        message_id: str,
        reaction: str,
        session_id: str,
    ) -> dict[str, str]:
        self.reaction_calls.append((channel_target, message_id, reaction, session_id))
        if message_id in self.reaction_error_by_message_id:
            raise self.reaction_error_by_message_id[message_id]
        return {
            "status": "reacted",
            "channel_target": channel_target,
            "message_id": message_id,
            "reaction": reaction,
            "session_id": session_id,
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
    context.session_repository.upsert_session(
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
    assert context.teams_service.last_before_cursor is None
    assert [entry["cursor"] for entry in result["messages"]] == ["team-a/channel-1/msg-1"]
    assert result["next_cursor"] == "team-b/channel-9/msg-9"


def test_teams_get_messages_supports_limit_and_includes_session_authored_messages(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
    assert context.teams_service.last_limit is None
    assert context.teams_service.reaction_calls == []


def test_teams_get_messages_defaults_cursorless_reads_to_configured_poll_limit(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path, teams_poll_limit=2)
    context.session_repository.upsert_session(
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
                },
            ),
            TeamsMessageEnvelope(
                cursor="team-a/channel-1/msg-2",
                message_id="msg-2",
                author_id="user-2",
                team_id="team-a",
                channel_id="channel-1",
                thread_id="msg-1",
                content="middle",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
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
                },
            ),
        ]
    )
    server = create_server(context)

    result = call_tool(server, "teams_get_messages", {"session_id": "session-1"})

    assert context.teams_service.last_limit is None
    assert [entry["cursor"] for entry in result["messages"]] == [
        "team-a/channel-1/msg-2",
        "team-a/channel-1/msg-3",
    ]
    assert result["next_cursor"] == "team-a/channel-1/msg-3"


def test_teams_get_messages_applies_default_limit_after_session_filtering(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path, teams_poll_limit=1)
    context.session_repository.upsert_session(
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
                },
            ),
            TeamsMessageEnvelope(
                cursor="team-b/channel-9/msg-2",
                message_id="msg-2",
                author_id="user-2",
                team_id="team-b",
                channel_id="channel-9",
                thread_id="msg-2",
                content="blocked",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-b",
                    "channel_id": "channel-9",
                },
            ),
        ]
    )
    server = create_server(context)

    result = call_tool(server, "teams_get_messages", {"session_id": "session-1"})

    assert context.teams_service.last_limit is None
    assert [entry["cursor"] for entry in result["messages"]] == ["team-a/channel-1/msg-1"]
    assert result["next_cursor"] == "team-a/channel-1/msg-1"


def test_teams_get_messages_supports_before_cursor_for_older_history(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
                content="one",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                },
            ),
            TeamsMessageEnvelope(
                cursor="team-a/channel-1/msg-2",
                message_id="msg-2",
                author_id="user-2",
                team_id="team-a",
                channel_id="channel-1",
                thread_id="msg-1",
                content="two",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                },
            ),
            TeamsMessageEnvelope(
                cursor="team-a/channel-1/msg-3",
                message_id="msg-3",
                author_id="user-3",
                team_id="team-a",
                channel_id="channel-1",
                thread_id="msg-1",
                content="three",
                metadata={
                    "source": "teams_graph_api",
                    "team_id": "team-a",
                    "channel_id": "channel-1",
                },
            ),
        ]
    )
    server = create_server(context)

    result = call_tool(
        server,
        "teams_get_messages",
        {"session_id": "session-1", "before_cursor": "team-a/channel-1/msg-3", "limit": 2},
    )

    assert context.teams_service.last_since_cursor is None
    assert context.teams_service.last_before_cursor == "team-a/channel-1/msg-3"
    assert context.teams_service.last_limit is None
    assert [entry["cursor"] for entry in result["messages"]] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    ]
    assert result["next_cursor"] == "team-a/channel-1/msg-2"


def test_add_message_reaction_adds_teams_reaction_to_allowlisted_target(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.ADD_MESSAGE_REACTION],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_teams = FakeTeamsService(
        [
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
    context.teams_service = fake_teams
    server = create_server(context)

    result = call_tool(
        server,
        "add_message_reaction",
        {
            "session_id": "session-1",
            "provider": "teams",
            "channel_target": "team-a/channel-1",
            "message_id": "msg-3",
            "emoji": "heart",
        },
    )

    assert context.teams_service.reaction_calls == [
        ("team-a/channel-1", "msg-3", "heart", "session-1")
    ]
    assert result["status"] == "reacted"


def test_teams_get_messages_includes_status_messages_from_other_sessions_in_same_channel(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
    assert context.teams_service.reaction_calls == []


def test_add_message_reaction_audits_teams_adapter_failure(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.ADD_MESSAGE_REACTION],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_teams = FakeTeamsService(
        [
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
    request = httpx.Request("POST", "https://graph.microsoft.com/v1.0/test")
    response = httpx.Response(500, request=request, text='{"error":"failure"}')
    fake_teams.reaction_error_by_message_id["msg-3"] = httpx.HTTPStatusError(
        "boom",
        request=request,
        response=response,
    )
    context.teams_service = fake_teams
    server = create_server(context)

    with pytest.raises(ToolError):
        call_tool(
            server,
            "add_message_reaction",
            {
                "session_id": "session-1",
                "provider": "teams",
                "channel_target": "team-a/channel-1",
                "message_id": "msg-3",
                "emoji": "heart",
            },
        )

    events = read_audit_events(context.config.audit_log_path)
    assert any(
        event["tool_name"] == ToolName.ADD_MESSAGE_REACTION.value
        and event["result"].get("error_class") == "adapter_http_error"
        and event["args"].get("message_id") == "msg-3"
        for event in events
    )


def test_teams_get_messages_repeated_reads_with_cursor_do_not_retry_reactions(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_teams = FakeTeamsService(
        [
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
    request = httpx.Request("POST", "https://graph.microsoft.com/v1.0/test")
    response = httpx.Response(500, request=request, text='{"error":"failure"}')
    fake_teams.reaction_error_by_message_id["msg-3"] = httpx.HTTPStatusError(
        "boom",
        request=request,
        response=response,
    )
    context.teams_service = fake_teams
    server = create_server(context)

    first = call_tool(server, "teams_get_messages", {"session_id": "session-1"})
    second = call_tool(
        server,
        "teams_get_messages",
        {"session_id": "session-1", "since_cursor": first["next_cursor"]},
    )

    assert [entry["cursor"] for entry in first["messages"]] == ["team-a/channel-1/msg-3"]
    assert second["messages"] == []
    assert context.teams_service.reaction_calls == []
    reaction_failure_events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event["tool_name"] == ToolName.TEAMS_GET_MESSAGES.value
        and event["result"].get("error_class") == "reaction_side_effect_error"
    ]
    assert reaction_failure_events == []


def test_teams_post_status_posts_to_allowlisted_channel_and_thread(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
    context.session_repository.upsert_session(
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_POST_STATUS],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_teams = FakeTeamsService(messages=[])
    fake_teams.post_status = lambda *args, **kwargs: (_ for _ in ()).throw(
        httpx.ReadTimeout("teams timeout")
    )
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
    context.session_repository.upsert_session(
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
