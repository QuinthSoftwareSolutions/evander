from pathlib import Path

import pytest

from agent_sandbox.directory_config import DirectoryScopeResolver
from agent_sandbox.directories import DirectoryService, DirectoryStore


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
