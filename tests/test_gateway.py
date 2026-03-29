from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.gateway import _filter_discord_commands_for_session, _resolve_session_id_from_context
from agent_sandbox.models import DiscordCommandEnvelope, SessionManifest, ToolName
from agent_sandbox.policy import PolicyError


def _command(command_id: str, *, source: str) -> DiscordCommandEnvelope:
    return DiscordCommandEnvelope(
        command_id=command_id,
        author_id="user-1",
        channel_id="chan-1",
        content="!status",
        created_at=datetime.now(UTC),
        metadata={"source": source, "channel_id": "chan-1", "author_id": "user-1"},
    )


def test_filter_discord_commands_returns_raw_stream_cursor_with_denials() -> None:
    allowed = _command("cmd_allowed", source="discord_bot_api")
    denied = _command("cmd_denied", source="discord_bot_api_status")
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class AllowOnlyTrustedPolicy:
        def require_discord_command_source(self, _session: SessionManifest, command: DiscordCommandEnvelope) -> None:
            if command.metadata.get("source") != "discord_bot_api":
                raise PolicyError(f"Discord command {command.command_id} has untrusted source")

    denied_audits: list[dict[str, str]] = []

    def audit_failure(
        _session_id: str,
        _tool_name: ToolName,
        denied_args: dict[str, str],
        _error: str,
    ) -> None:
        denied_audits.append(denied_args)

    commands, denied_count, next_cursor = _filter_discord_commands_for_session(
        SimpleNamespace(policy=AllowOnlyTrustedPolicy()),
        "s1",
        session,
        [allowed, denied],
        {"session_id": "s1", "since_cursor": "cmd_prev"},
        audit_failure,
    )

    assert [command["command_id"] for command in commands] == ["cmd_allowed"]
    assert denied_count == 1
    assert next_cursor == "cmd_denied"
    assert denied_audits[0]["command_id"] == "cmd_denied"


def test_resolve_session_id_from_context_prefers_explicit_value() -> None:
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(query_params={"session_id": "query-session"}, headers={})
        )
    )

    assert _resolve_session_id_from_context("explicit-session", ctx) == "explicit-session"


def test_resolve_session_id_from_context_reads_query_param_then_header() -> None:
    transport_map: dict[str, str] = {}
    query_ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                query_params={"session_id": "query-session"},
                headers={"mcp-session-id": "transport-1"},
            )
        )
    )
    header_ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(query_params={}, headers={"x-agent-sandbox-session-id": "header-session"})
        )
    )
    mapped_ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(query_params={}, headers={"mcp-session-id": "transport-1"})
        )
    )

    assert (
        _resolve_session_id_from_context(None, query_ctx, transport_session_map=transport_map) == "query-session"
    )
    assert transport_map == {"transport-1": "query-session"}
    assert _resolve_session_id_from_context(None, header_ctx) == "header-session"
    assert (
        _resolve_session_id_from_context(None, mapped_ctx, transport_session_map=transport_map) == "query-session"
    )


def test_resolve_session_id_from_context_requires_value() -> None:
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(query_params={}, headers={})))

    with pytest.raises(ToolError, match="session_id is required"):
        _resolve_session_id_from_context(None, ctx)
