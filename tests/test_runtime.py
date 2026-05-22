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
    AzureDevOpsPrQueryProvider,
    AzureDevOpsPrPublishProvider,
    DiscordBotApiService,
    GraphTeamsService,
    DiscordInboxService,
    GitHubPrReplyProvider,
    GitHubPrPublishProvider,
    StubDiscordService,
    StubTeamsService,
)
from agent_sandbox.messaging.teams_auth import (
    JsonTeamsIdentityCredentialStore,
    TeamsAuthCredentials,
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


def write_provider_overlay(
    config: GatewayConfig, reference: str, payload: dict[str, object]
) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.write(reference, domain="providers", payload=payload)


def test_build_runtime_uses_stub_discord_backend_by_default(tmp_path: Path) -> None:
    runtime = build_runtime(make_config(tmp_path))
    assert isinstance(runtime.discord_service, StubDiscordService)
    assert isinstance(runtime.teams_service, StubTeamsService)
    assert runtime.discord_service.read_reaction_emoji == "👀"
    assert runtime.teams_service.read_reaction_emoji == "👀"


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


def test_build_runtime_threads_discord_read_reaction_emoji_into_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token-value")
    config = make_config(
        tmp_path,
        discord_backend="bot_api",
        discord_read_reaction_emoji="✅",
    )

    runtime = build_runtime(config)

    assert isinstance(runtime.discord_service, DiscordBotApiService)
    assert runtime.discord_service.read_reaction_emoji == "✅"


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


def test_build_runtime_threads_teams_read_reaction_emoji_into_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = make_config(
        tmp_path,
        teams_backend="graph_api",
        teams_read_reaction_emoji="✅",
    )

    runtime = build_runtime(config)

    assert isinstance(runtime.teams_service, GraphTeamsService)
    assert runtime.teams_service.read_reaction_emoji == "✅"


def test_build_runtime_supports_delegated_teams_backend_without_static_env_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRAPH_TEAMS_ACCESS_TOKEN", raising=False)
    config = make_config(
        tmp_path,
        teams_backend="delegated_graph_api",
        teams_delegated_identity_id="root-identity",
        teams_delegated_client_id="client-123",
    )

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


def test_build_runtime_initializes_discord_inbox_service(tmp_path: Path) -> None:
    runtime = build_runtime(make_config(tmp_path))

    assert isinstance(runtime.discord_inbox_service, DiscordInboxService)
    assert runtime.discord_inbox_service.store_path == tmp_path / "discord-inbox-store.json"


def test_build_runtime_initializes_agent_prompt_store(tmp_path: Path) -> None:
    runtime = build_runtime(make_config(tmp_path))

    assert runtime.agent_prompt_store.path == tmp_path / "agent-prompt-store.json"
    assert runtime.agent_prompt_store.path.exists()


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
    runtime.session_repository.upsert_session(
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


def test_runtime_discord_service_for_session_uses_session_scoped_policy_inputs(
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
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.write(
        "root",
        domain="policy",
        payload={
            "discord_operator_channel_allowlist": ["root-ops"],
            "discord_operator_user_allowlist": ["root-user"],
            "discord_poll_operator_dms": False,
        },
    )
    config_store.write(
        team.path,
        domain="policy",
        payload={
            "discord_operator_channel_allowlist": ["team-ops"],
            "discord_operator_user_allowlist": ["team-user"],
            "discord_poll_operator_dms": True,
        },
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-root",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["root-control"],
            directory_path="root",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["team-control"],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    root_service = runtime.discord_service_for_session("session-root")
    team_service = runtime.discord_service_for_session("session-team")

    assert isinstance(root_service, DiscordBotApiService)
    assert isinstance(team_service, DiscordBotApiService)
    assert root_service.token == "root-token"
    assert team_service.token == "team-token"
    assert root_service._allowed_channel_ids() == ["root-control", "root-ops"]
    assert team_service._allowed_channel_ids() == ["team-control", "team-ops"]
    assert root_service._operator_user_ids() == ["root-user"]
    assert team_service._operator_user_ids() == ["team-user"]
    assert root_service.include_operator_dms_provider() is False
    assert team_service.include_operator_dms_provider() is True


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
    runtime.session_repository.upsert_session(
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


def test_runtime_teams_service_for_session_uses_directory_provider_override_and_session_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "root-token")
    monkeypatch.setenv("ROOT_TEAMS_TOKEN", "root-token")
    monkeypatch.setenv("TEAM_A_TEAMS_TOKEN", "team-a-token")
    config = make_config(tmp_path, teams_backend="graph_api")
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team_a = directories.create(parent="root", name="team-a")
    team_b = directories.create(parent="root", name="team-b")
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            directory_path=team_a.path,
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-b",
            directory_path=team_b.path,
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-b/channel-9"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    monkeypatch.setattr(
        type(runtime),
        "resolve_provider_config_for_session",
        lambda self, session_id: (
            self.config.model_copy(update={"teams_graph_access_token_env": "TEAM_A_TEAMS_TOKEN"})
            if session_id == "session-a"
            else self.config
        ),
    )

    teams_service = runtime.teams_service_for_session("session-a")

    assert isinstance(teams_service, GraphTeamsService)
    assert teams_service.token == "team-a-token"
    assert teams_service._allowed_channel_targets() == ["team-a/channel-1"]


def test_runtime_teams_service_for_session_scopes_root_graph_service_to_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "root-token")
    config = make_config(tmp_path, teams_backend="graph_api")
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-b",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-b/channel-9"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    teams_service = runtime.teams_service_for_session("session-a")

    assert isinstance(teams_service, GraphTeamsService)
    assert teams_service._allowed_channel_targets() == ["team-a/channel-1"]


def test_runtime_teams_service_for_session_includes_chat_allowlist_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "root-token")
    config = make_config(tmp_path, teams_backend="graph_api")
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_chat_allowlist=["chat:chat-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-b",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_chat_allowlist=["chat:chat-2"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    teams_service = runtime.teams_service_for_session("session-a")

    assert isinstance(teams_service, GraphTeamsService)
    assert teams_service._allowed_channel_targets() == ["chat:chat-1"]


def test_runtime_teams_service_for_session_uses_directory_delegated_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRAPH_TEAMS_ACCESS_TOKEN", raising=False)
    config = make_config(tmp_path, teams_backend="stub")
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        team.path,
        {
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "team-identity",
            "teams_delegated_client_id": "client-123",
        },
    )
    JsonTeamsIdentityCredentialStore(
        tmp_path / "teams-delegated-credentials.json",
        "team-identity",
    ).save(
        TeamsAuthCredentials(
            client_id="client-123",
            tenant_id="tenant-abc",
            access_token="delegated-team-token",
            refresh_token="refresh-token",
            scopes=["offline_access"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-team",
            directory_path=team.path,
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    teams_service = runtime.teams_service_for_session("session-team")

    assert isinstance(teams_service, GraphTeamsService)
    assert teams_service._headers()["Authorization"] == "Bearer delegated-team-token"
    assert teams_service._allowed_channel_targets() == ["team-a/channel-1"]


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
    runtime.session_repository.upsert_session(
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


def test_runtime_pr_publish_service_for_session_uses_directory_provider_override(
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
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pr_publish_service = runtime.pr_publish_service_for_session("session-team")

    github_provider = pr_publish_service.providers["github"]
    azure_provider = pr_publish_service.providers["azure_devops"]
    assert isinstance(github_provider, GitHubPrPublishProvider)
    assert github_provider.token == "gh-token"
    assert isinstance(azure_provider, AzureDevOpsPrPublishProvider)
    assert azure_provider.organization_url == "https://dev.azure.com/team-org"
    assert azure_provider.project == "team-project"
    assert azure_provider.pat == "ado-token"
    assert azure_provider.timeout_seconds == 9.0


def test_runtime_pr_query_service_for_session_uses_directory_provider_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEAM_ADO_PR_PAT", "ado-token")
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        team.path,
        {
            "azure_devops_pr_pat_env": "TEAM_ADO_PR_PAT",
            "azure_devops_organization_url": "https://dev.azure.com/team-org",
            "azure_devops_project": "team-project",
            "email_provider_timeout_seconds": 9.0,
        },
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.LIST_PULL_REQUESTS],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pr_query_service = runtime.pr_query_service_for_session("session-team")

    azure_provider = pr_query_service.providers["azure_devops"]
    assert isinstance(azure_provider, AzureDevOpsPrQueryProvider)
    assert azure_provider.organization_url == "https://dev.azure.com/team-org"
    assert azure_provider.project == "team-project"
    assert azure_provider.pat == "ado-token"
    assert azure_provider.timeout_seconds == 9.0


def test_resolve_allowed_discord_channel_ids_uses_active_sessions_and_config(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(make_config(tmp_path))
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-active",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-session"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-stale",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-stale"],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        ),
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-without-tool",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            discord_channel_allowlist=["chan-no-tool"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    config = make_config(tmp_path, discord_operator_channel_allowlist=["chan-operator"])

    channel_ids = _resolve_allowed_discord_channel_ids(runtime.session_repository, config)

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
    config_store.write(
        "root", domain="policy", payload={"discord_operator_channel_allowlist": ["chan-root"]}
    )
    config_store.write(
        team.path, domain="policy", payload={"discord_operator_channel_allowlist": ["chan-team"]}
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-active",
            directory_path=team.path,
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-session"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    channel_ids = _resolve_allowed_discord_channel_ids(
        runtime.session_repository,
        config,
        policy_config=runtime.policy_config,
    )

    assert channel_ids == ["chan-root", "chan-session", "chan-team"]


def test_resolve_allowed_discord_channel_ids_uses_effective_directory_policy_without_stale_session_leak(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    runtime = build_runtime(config)
    directory_store = DirectoryStore(config.directories_store_path)
    directories = DirectoryService(directory_store)
    team = directories.create(parent="root", name="team")
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.write(
        "root",
        domain="policy",
        payload={"discord_operator_channel_allowlist": ["chan-root"]},
    )
    config_store.write(
        team.path,
        domain="policy",
        payload={"discord_operator_channel_allowlist": ["chan-team"]},
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-active",
            directory_path="root",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-session-root"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-stale",
            directory_path=team.path,
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-stale"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    directories.delete(team.id)

    channel_ids = _resolve_allowed_discord_channel_ids(
        runtime.session_repository,
        config,
        policy_config=runtime.policy_config,
    )

    assert channel_ids == ["chan-root", "chan-session-root"]


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
