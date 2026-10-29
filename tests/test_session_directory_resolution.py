from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path

import pytest

from evander.config import GatewayConfig
from evander.directory_config import DirectoryScopeResolver
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryService, DirectoryStore
from evander.policy_config import PolicyConfigResolver
from evander.provider_config import (
    ProviderConfigResolver,
    ProviderSessionManifest,
)
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.models import SessionManifest, ToolName
from evander.storage import JsonStore


def make_config(tmp_path: Path) -> GatewayConfig:
    return GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )


def test_directory_scope_resolver_resolves_root_for_none_and_empty_path(
    tmp_path: Path,
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryScopeResolver(service)

    assert resolver.resolve_directory_path(None) == "root"
    assert resolver.resolve_directory_path("") == "root"


def test_directory_scope_resolver_canonicalizes_existing_paths_via_service_show(
    tmp_path: Path,
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryScopeResolver(service)

    team = service.create(parent="root", name="team")

    assert resolver.resolve_directory_path(team.id) == "root/team"


def test_directory_scope_resolver_raises_for_missing_explicit_directory_path(
    tmp_path: Path,
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryScopeResolver(service)

    with pytest.raises(ValueError, match=r"directory path segment 'missing' does not exist"):
        resolver.resolve_directory_path("root/missing")


def test_directory_scope_resolver_wraps_session_directory_errors_with_context(
    tmp_path: Path,
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryScopeResolver(service)

    with pytest.raises(
        ValueError,
        match=(
            r"provider config session 'session-123' references missing directory_path "
            r"'root/missing': directory path segment 'missing' does not exist under 'root'"
        ),
    ):
        resolver.resolve_session_directory_path(
            context="provider config",
            session_id="session-123",
            directory_path="root/missing",
        )


def test_provider_config_resolver_resolves_root_for_none_empty_and_rejects_missing_paths(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    resolver = ProviderConfigResolver(config)

    root_config = resolver.resolve_for_directory("root").model_dump(mode="json")
    root_contacts = resolver.resolve_contacts_for_directory("root").model_dump(mode="json")

    assert resolver.resolve_for_directory(None).model_dump(mode="json") == root_config
    assert resolver.resolve_for_directory("").model_dump(mode="json") == root_config
    assert resolver.resolve_contacts_for_directory(None).model_dump(mode="json") == root_contacts
    assert resolver.resolve_contacts_for_directory("").model_dump(mode="json") == root_contacts

    with pytest.raises(ValueError, match=r"directory path segment 'missing' does not exist"):
        resolver.resolve_for_directory("root/missing")

    with pytest.raises(ValueError, match=r"directory path segment 'missing' does not exist"):
        resolver.resolve_contacts_for_directory("root/missing")


def test_provider_config_resolver_rejects_stale_session_directory_paths(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    session_store = JsonSessionManifestRepository(config.session_store_path)
    session_store.upsert_session(
        SessionManifest(
            session_id="session-123",
            allowed_tools=[],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resolver = ProviderConfigResolver(config, session_store=session_store)

    directories.delete(team.id)

    with pytest.raises(
        ValueError,
        match=(
            r"provider config session 'session-123' references missing directory_path "
            r"'root/team': directory path segment 'team' does not exist under 'root'"
        ),
    ):
        resolver.resolve_for_session_id("session-123")

    with pytest.raises(
        ValueError,
        match=(
            r"contacts config session 'session-123' references missing directory_path "
            r"'root/team': directory path segment 'team' does not exist under 'root'"
        ),
    ):
        resolver.resolve_contacts_for_session_id("session-123")


def test_provider_config_resolver_rejects_stale_provider_session_store_directory_paths(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    provider_session_store = JsonStore(
        config.session_store_path,
        ProviderSessionManifest,
    )
    provider_session_store.put(
        "session-fallback",
        ProviderSessionManifest(session_id="session-fallback", directory_path=team.path),
    )
    resolver = ProviderConfigResolver(
        config,
        provider_session_store=provider_session_store,
    )

    directories.delete(team.id)

    with pytest.raises(
        ValueError,
        match=(
            r"provider config session 'session-fallback' references missing directory_path "
            r"'root/team': directory path segment 'team' does not exist under 'root'"
        ),
    ):
        resolver.resolve_for_session_id("session-fallback")

    with pytest.raises(
        ValueError,
        match=(
            r"contacts config session 'session-fallback' references missing directory_path "
            r"'root/team': directory path segment 'team' does not exist under 'root'"
        ),
    ):
        resolver.resolve_contacts_for_session_id("session-fallback")


def test_provider_config_resolver_resolves_azure_openai_mode_from_child_session_overlay(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directories = DirectoryService(directory_store)
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    team = directories.create(parent="root", name="team")
    session_store = JsonSessionManifestRepository(config.session_store_path)
    session_store.upsert_session(
        SessionManifest(
            session_id="session-azure",
            allowed_tools=[],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    config_store.write(
        "root",
        domain="providers",
        payload={
            "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
            "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            "azure_openai_model_deployments": {
                "gpt-5.4": "deployment-root",
                "gpt-4.1-mini": "deployment-root-mini",
            },
        },
    )
    config_store.write(
        team.path,
        domain="providers",
        payload={"model_provider_mode": "azure_openai"},
    )

    resolver = ProviderConfigResolver(
        config,
        directory_store=directory_store,
        session_store=session_store,
    )

    resolved = resolver.resolve_for_session_id("session-azure")

    assert resolved.model_provider_mode == "azure_openai"
    assert resolved.azure_openai_endpoint == "https://example-resource.openai.azure.com"
    assert resolved.azure_openai_api_key_env == "AZURE_OPENAI_API_KEY"
    assert resolved.azure_openai_model_deployments == {
        "gpt-5.4": "deployment-root",
        "gpt-4.1-mini": "deployment-root-mini",
    }


def test_provider_config_resolver_resolves_effective_contacts_for_session_id(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directories = DirectoryService(directory_store)
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    team = directories.create(parent="root", name="team")
    config_store.write(
        team.path,
        domain="contacts",
        payload={
            "contacts": {
                "ops": {
                    "name": "Ops",
                    "email_addresses": ["ops@example.com"],
                    "phone_numbers": [{"label": "main", "number": "+15550001", "primary": True}],
                },
                "support": {
                    "name": "Support",
                    "email_addresses": ["support@example.com"],
                    "phone_numbers": [{"label": "main", "number": "+15550002", "primary": True}],
                },
            },
            "user_contact_links": {"agent": "ops"},
        },
    )
    session_store = JsonSessionManifestRepository(config.session_store_path)
    session_store.upsert_session(
        SessionManifest(
            session_id="session-contacts",
            allowed_tools=[],
            allowed_contact_ids=["ops"],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resolver = ProviderConfigResolver(
        config,
        directory_store=directory_store,
        session_store=session_store,
    )

    effective = resolver.resolve_effective_contacts_for_session_id("session-contacts")

    assert list(effective.contacts) == ["ops"]
    assert effective.user_contact_links == {"agent": "ops"}


def test_provider_config_resolver_resolves_effective_contacts_for_provider_session_store(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directories = DirectoryService(directory_store)
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    team = directories.create(parent="root", name="team")
    config_store.write(
        team.path,
        domain="contacts",
        payload={
            "contacts": {
                "ops": {"name": "Ops"},
                "support": {"name": "Support"},
            }
        },
    )
    provider_session_store = JsonStore(
        config.session_store_path,
        ProviderSessionManifest,
    )
    provider_session_store.put(
        "session-fallback",
        ProviderSessionManifest(
            session_id="session-fallback",
            directory_path=team.path,
            allowed_contact_ids=["ops"],
        ),
    )
    resolver = ProviderConfigResolver(
        config,
        directory_store=directory_store,
        provider_session_store=provider_session_store,
    )

    effective = resolver.resolve_effective_contacts_for_session_id("session-fallback")

    assert list(effective.contacts) == ["ops"]


def test_policy_config_resolver_resolves_root_for_none_empty_and_rejects_missing_paths(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    resolver = PolicyConfigResolver(config)

    root_config = resolver.resolve_for_directory("root").model_dump(mode="json")

    assert resolver.resolve_for_directory(None).model_dump(mode="json") == root_config
    assert resolver.resolve_for_directory("").model_dump(mode="json") == root_config

    with pytest.raises(ValueError, match=r"directory path segment 'missing' does not exist"):
        resolver.resolve_for_directory("root/missing")


def test_policy_config_resolver_rejects_stale_session_directory_paths(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    session_store = JsonSessionManifestRepository(config.session_store_path)
    session_store.upsert_session(
        SessionManifest(
            session_id="session-123",
            allowed_tools=[],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_id="vm-stale",
            metadata={"owner": "ops"},
        )
    )
    resolver = PolicyConfigResolver(config, session_store=session_store)

    directories.delete(team.id)

    with pytest.raises(
        ValueError,
        match=(
            r"policy config session 'session-123' references missing directory_path "
            r"'root/team': directory path segment 'team' does not exist under 'root'"
        ),
    ):
        resolver.resolve_for_session_id("session-123")

    with pytest.raises(
        ValueError,
        match=(
            r"policy config session 'session-123' references missing directory_path "
            r"'root/team': directory path segment 'team' does not exist under 'root'"
        ),
    ):
        resolver.resolve_for_session_manifest(
            SessionManifest(
                session_id="session-123",
                allowed_tools=[],
                directory_path=team.path,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )


def test_policy_config_resolver_skips_stale_active_sessions_and_warns_once_per_resolution(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directories = DirectoryService(directory_store)
    team = directories.create(parent="root", name="team")
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
    session_store = JsonSessionManifestRepository(config.session_store_path)
    session_store.upsert_session(
        SessionManifest(
            session_id="session-root",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["session-root-channel"],
            directory_path="root",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session_store.upsert_session(
        SessionManifest(
            session_id="session-stale",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["session-stale-channel"],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_id="vm-stale",
            metadata={"owner": "ops", "notes": "should not leak"},
        )
    )
    resolver = PolicyConfigResolver(config, session_store=session_store)

    directories.delete(team.id)

    with caplog.at_level(logging.WARNING):
        active_policies = resolver.active_session_policies()
        operator_user_ids = resolver.operator_user_ids_for_active_sessions()
        include_operator_dms = resolver.include_operator_dms_for_active_sessions()
        channel_ids = resolver.allowed_discord_channel_ids_for_active_sessions()

    assert [policy.discord_operator_channel_allowlist for policy in active_policies] == [
        ["root-ops"]
    ]
    assert operator_user_ids == ["root-user"]
    assert include_operator_dms is False
    assert channel_ids == ["root-ops", "session-root-channel"]
    assert any(
        "session-stale" in record.message and "root/team" in record.message
        for record in caplog.records
    )
    assert not any("vm-stale" in record.message for record in caplog.records)
    assert not any("should not leak" in record.message for record in caplog.records)
