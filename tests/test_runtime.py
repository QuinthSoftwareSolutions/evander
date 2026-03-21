from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.runtime import _resolve_allowed_discord_channel_ids, build_runtime
from agent_sandbox.config import GatewayConfig, MailboxConfig
from agent_sandbox.services import DiscordBotApiService, StubDiscordService


def make_config(tmp_path: Path, **kwargs: object) -> GatewayConfig:
    return GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        **kwargs,
    )


def test_build_runtime_uses_stub_discord_backend_by_default(tmp_path: Path) -> None:
    runtime = build_runtime(make_config(tmp_path))
    assert isinstance(runtime.discord_service, StubDiscordService)


def test_build_runtime_requires_bot_token_for_bot_api_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    config = make_config(tmp_path, discord_backend="bot_api")

    with pytest.raises(RuntimeError, match="requires a bot token"):
        build_runtime(config)


def test_build_runtime_uses_bot_api_discord_backend_when_token_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token-value")
    config = make_config(tmp_path, discord_backend="bot_api")

    runtime = build_runtime(config)

    assert isinstance(runtime.discord_service, DiscordBotApiService)


def test_build_runtime_reads_bot_token_from_configured_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_SANDBOX_DISCORD_TOKEN", "token-value")
    config = make_config(
        tmp_path,
        discord_backend="bot_api",
        discord_bot_token_env="AGENT_SANDBOX_DISCORD_TOKEN",
    )

    runtime = build_runtime(config)

    assert isinstance(runtime.discord_service, DiscordBotApiService)


def test_resolve_allowed_discord_channel_ids_uses_active_sessions_and_config(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(make_config(tmp_path))
    runtime.sessions.put(
        "session-active",
        SessionManifest(
            session_id="session-active",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-session"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    runtime.sessions.put(
        "session-stale",
        SessionManifest(
            session_id="session-stale",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-stale"],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        ),
    )
    runtime.sessions.put(
        "session-without-tool",
        SessionManifest(
            session_id="session-without-tool",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            discord_channel_allowlist=["chan-no-tool"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    config = make_config(tmp_path, discord_operator_channel_allowlist=["chan-operator"])

    channel_ids = _resolve_allowed_discord_channel_ids(runtime.sessions, config)

    assert channel_ids == ["chan-operator", "chan-session"]
@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_build_runtime_requires_access_token_secret_ref_for_non_json_mailboxes(
    tmp_path: Path, provider: str
) -> None:
    config = make_config(
        tmp_path,
        email_mailboxes=[MailboxConfig(mailbox_id="ops", provider=provider, auth_secret_refs={})],
    )

    with pytest.raises(RuntimeError, match="requires auth_secret_refs.access_token"):
        build_runtime(config)


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_build_runtime_requires_access_token_env_var_value_for_non_json_mailboxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.delenv("OPS_TOKEN", raising=False)
    config = make_config(
        tmp_path,
        email_mailboxes=[
            MailboxConfig(
                mailbox_id="ops",
                provider=provider,
                auth_secret_refs={"access_token": "OPS_TOKEN"},
            )
        ],
    )

    with pytest.raises(RuntimeError, match="requires a non-empty access token"):
        build_runtime(config)


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_build_runtime_accepts_access_token_env_var_for_non_json_mailboxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.setenv("OPS_TOKEN", "abc123")
    config = make_config(
        tmp_path,
        email_mailboxes=[
            MailboxConfig(
                mailbox_id="ops",
                provider=provider,
                auth_secret_refs={"access_token": "OPS_TOKEN"},
            )
        ],
    )

    runtime = build_runtime(config)

    assert runtime.email_service is not None
