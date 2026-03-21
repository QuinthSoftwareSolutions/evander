from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from agent_sandbox.gateway import _filter_discord_commands_for_session
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
