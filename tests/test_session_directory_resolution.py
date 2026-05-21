from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryScopeResolver
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.provider_config import (
    ProviderConfigResolver,
    ProviderSessionManifest,
)
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.storage import JsonStore
from agent_sandbox.models import SessionManifest


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
