from datetime import UTC, datetime, timedelta


from pathlib import Path


import pytest


from evander.directory_config import DirectoryConfigStore


from evander.directories import DirectoryService, DirectoryStore


from evander.config import GatewayConfig, MailboxConfig


from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)


from evander.models import SessionManifest, ToolName


from evander.repositories.records import (
    SqliteTeamsRecordsRepository,
)


from evander.runtime import build_runtime


from evander.services import (
    GraphTeamsService,
)


from evander.messaging.teams_auth import (
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


def write_communication_targets(
    config: GatewayConfig, reference: str, targets: list[dict[str, object]]
) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    directory = DirectoryService(directory_store).show(reference).directory
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(config.runtime_dir)
    )
    repository.write_overlay(directory.id, {"targets": targets})


def test_build_runtime_initializes_sqlite_teams_message_store(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    runtime = build_runtime(config)

    assert isinstance(runtime.teams_messages, SqliteTeamsRecordsRepository)
    assert runtime.teams_messages.path == config.teams_message_store_path
    assert runtime.teams_messages.path.exists()


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


def test_runtime_teams_service_for_session_resolves_communication_target_allowlists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "root-token")
    config = make_config(tmp_path, teams_backend="graph_api")
    write_communication_targets(
        config,
        "root",
        [
            {
                "id": "teams-inbox",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read", "write"]},
            },
            {
                "id": "teams-operator",
                "provider": "teams",
                "kind": "teams",
                "address": "user:operator@example.com",
                "policy": {"capabilities": ["write"]},
            },
        ],
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["teams-inbox"],
            teams_user_allowlist=["teams-operator"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    teams_service = runtime.teams_service_for_session("session-a")

    assert isinstance(teams_service, GraphTeamsService)
    assert teams_service._allowed_channel_targets() == ["team-a/channel-1"]
    assert teams_service._allowed_user_targets() == ["user:operator@example.com"]


def test_runtime_teams_service_for_session_skips_target_without_read_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "root-token")
    config = make_config(tmp_path, teams_backend="graph_api")
    write_communication_targets(
        config,
        "root",
        [
            {
                "id": "teams-write-only",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["write"]},
            }
        ],
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["teams-write-only"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    teams_service = runtime.teams_service_for_session("session-a")

    assert isinstance(teams_service, GraphTeamsService)
    assert teams_service._allowed_channel_targets() == []


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


@pytest.mark.parametrize("provider", ["graph"])
def test_build_runtime_requires_access_token_secret_ref_for_non_json_mailboxes(
    tmp_path: Path, provider: str
) -> None:
    config = make_config(
        tmp_path,
        email_mailboxes=[MailboxConfig(mailbox_id="ops", provider=provider, auth_secret_refs={})],
    )

    with pytest.raises(RuntimeError, match="requires auth_secret_refs.access_token"):
        build_runtime(config)


@pytest.mark.parametrize("provider", ["graph"])
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


def test_build_runtime_rejects_incomplete_graph_oauth_client_credentials(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        email_mailboxes=[
            MailboxConfig(
                mailbox_id="ops",
                provider="graph",
                auth_secret_refs={"client_secret": "GRAPH_CLIENT_SECRET"},
            )
        ],
    )

    with pytest.raises(RuntimeError, match="complete OAuth client credential refs"):
        build_runtime(config)


def test_build_runtime_rejects_empty_graph_oauth_client_credential_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "client-id")
    monkeypatch.delenv("GRAPH_CLIENT_SECRET", raising=False)
    config = make_config(
        tmp_path,
        email_mailboxes=[
            MailboxConfig(
                mailbox_id="ops",
                provider="graph",
                auth_secret_refs={
                    "tenant_id": "GRAPH_TENANT_ID",
                    "client_id": "GRAPH_CLIENT_ID",
                    "client_secret": "GRAPH_CLIENT_SECRET",
                },
            )
        ],
    )

    with pytest.raises(RuntimeError, match="GRAPH_CLIENT_SECRET"):
        build_runtime(config)
