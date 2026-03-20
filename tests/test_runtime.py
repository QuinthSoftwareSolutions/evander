from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.runtime import build_runtime
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
