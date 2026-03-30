from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.config import GatewayConfig, MailboxConfig
from agent_sandbox.email.gmail_provider import GmailEmailProvider
from agent_sandbox.email.providers import MultiMailboxEmailProvider
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.runtime import _resolve_allowed_discord_channel_ids, build_runtime
from agent_sandbox.services import (
    AzureDevOpsPrReplyProvider,
    DiscordBotApiService,
    GraphTeamsService,
    GitHubPrReplyProvider,
    StubDiscordService,
    StubTeamsService,
)


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


def write_provider_overlay(config: GatewayConfig, reference: str, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.write(reference, domain="providers", payload=payload)


def test_build_runtime_uses_stub_discord_backend_by_default(tmp_path: Path) -> None:
    runtime = build_runtime(make_config(tmp_path))
    assert isinstance(runtime.discord_service, StubDiscordService)
    assert isinstance(runtime.teams_service, StubTeamsService)


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


def test_build_runtime_requires_graph_token_for_teams_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRAPH_TEAMS_ACCESS_TOKEN", raising=False)
    config = make_config(tmp_path, teams_backend="graph_api")

    with pytest.raises(RuntimeError, match="requires an access token"):
        build_runtime(config)


def test_build_runtime_uses_graph_teams_backend_when_token_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = make_config(tmp_path, teams_backend="graph_api")

    runtime = build_runtime(config)

    assert isinstance(runtime.teams_service, GraphTeamsService)


def test_build_runtime_uses_root_provider_override_for_default_discord_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROOT_DISCORD_TOKEN", "root-token")
    config = make_config(tmp_path, discord_backend="stub")
    write_provider_overlay(
        config,
        "root",
        {
            "discord_backend": "bot_api",
            "discord_bot_token_env": "ROOT_DISCORD_TOKEN",
        },
    )

    runtime = build_runtime(config)

    assert isinstance(runtime.discord_service, DiscordBotApiService)
    assert runtime.discord_service.token == "root-token"


def test_runtime_discord_service_for_session_uses_directory_provider_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROOT_DISCORD_TOKEN", "root-token")
    monkeypatch.setenv("TEAM_DISCORD_TOKEN", "team-token")
    config = make_config(tmp_path, discord_backend="stub")
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        "root",
        {
            "discord_backend": "bot_api",
            "discord_bot_token_env": "ROOT_DISCORD_TOKEN",
        },
    )
    write_provider_overlay(
        config,
        team.path,
        {
            "discord_bot_token_env": "TEAM_DISCORD_TOKEN",
        },
    )
    runtime = build_runtime(config)
    runtime.sessions.put(
        "session-team",
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    discord_service = runtime.discord_service_for_session("session-team")

    assert isinstance(discord_service, DiscordBotApiService)
    assert discord_service.token == "team-token"


def test_runtime_email_service_for_session_uses_directory_provider_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEAM_MAILBOX_TOKEN", "mailbox-token")
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        team.path,
        {
            "email_provider_timeout_seconds": 12.5,
            "email_mailboxes": [
                {
                    "mailbox_id": "ops",
                    "provider": "gmail",
                    "account": "me",
                    "auth_env_var_names": {"access_token": "TEAM_MAILBOX_TOKEN"},
                    "folder_allowlist_defaults": ["inbox", "drafts"],
                }
            ],
        },
    )
    runtime = build_runtime(config)
    runtime.sessions.put(
        "session-team",
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    email_service = runtime.email_service_for_session("session-team")

    assert isinstance(email_service.provider, MultiMailboxEmailProvider)
    mailbox_provider = email_service.provider.providers_by_mailbox["ops"]
    assert isinstance(mailbox_provider, GmailEmailProvider)
    assert mailbox_provider.timeout_seconds == 12.5


def test_runtime_pr_reply_service_for_session_uses_directory_provider_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEAM_GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("TEAM_ADO_PR_PAT", "ado-token")
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        team.path,
        {
            "github_token_env": "TEAM_GITHUB_TOKEN",
            "azure_devops_pr_pat_env": "TEAM_ADO_PR_PAT",
            "azure_devops_organization_url": "https://dev.azure.com/team-org",
            "azure_devops_project": "team-project",
            "email_provider_timeout_seconds": 9.0,
        },
    )
    runtime = build_runtime(config)
    runtime.sessions.put(
        "session-team",
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pr_reply_service = runtime.pr_reply_service_for_session("session-team")

    github_provider = pr_reply_service.providers["github"]
    azure_provider = pr_reply_service.providers["azure_devops"]
    assert isinstance(github_provider, GitHubPrReplyProvider)
    assert github_provider.token == "gh-token"
    assert isinstance(azure_provider, AzureDevOpsPrReplyProvider)
    assert azure_provider.organization_url == "https://dev.azure.com/team-org"
    assert azure_provider.project == "team-project"
    assert azure_provider.pat == "ado-token"
    assert azure_provider.timeout_seconds == 9.0


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


def test_resolve_allowed_discord_channel_ids_uses_effective_directory_policy(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    runtime = build_runtime(config)
    directory_store = DirectoryStore(config.directories_store_path)
    directories = DirectoryService(directory_store)
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    team = directories.create(parent="root", name="team")
    config_store.write("root", domain="policy", payload={"discord_operator_channel_allowlist": ["chan-root"]})
    config_store.write(team.path, domain="policy", payload={"discord_operator_channel_allowlist": ["chan-team"]})
    runtime.sessions.put(
        "session-active",
        SessionManifest(
            session_id="session-active",
            directory_path=team.path,
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-session"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    channel_ids = _resolve_allowed_discord_channel_ids(
        runtime.sessions,
        config,
        policy_config=runtime.policy_config,
    )

    assert channel_ids == ["chan-root", "chan-session", "chan-team"]
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
