from __future__ import annotations

from pathlib import Path

import pytest

from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.messaging.targets import (
    CommunicationTargetsDirectoryResolver,
    CommunicationTargetsConfig,
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)


def test_communication_targets_reject_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="must be unique: alpha"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {
                        "id": "alpha",
                        "provider": "teams",
                        "kind": "teams",
                        "address": "team/channel",
                    },
                    {
                        "id": "alpha",
                        "provider": "discord",
                        "kind": "discord",
                        "address": "123456789012345678",
                    },
                ]
            }
        )


def test_communication_targets_reject_incompatible_provider_kind() -> None:
    with pytest.raises(ValueError, match="not compatible"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {"id": "bad", "provider": "teams", "kind": "email", "address": "team/channel"},
                ]
            }
        )


def test_communication_targets_apply_provider_address_validation() -> None:
    with pytest.raises(ValueError, match="expected 'team-id/channel-id'"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {"id": "bad-teams", "provider": "teams", "kind": "teams", "address": "nope"},
                ]
            }
        )

    config = CommunicationTargetsConfig.model_validate(
        {
            "targets": [
                {
                    "id": "teams-user",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "user:operator@example.com",
                    "policy": {"capabilities": ["notify"]},
                },
            ]
        }
    )
    assert config.targets[0].address == "user:operator@example.com"

    with pytest.raises(ValueError, match="discord address must be"):
        CommunicationTargetsConfig.model_validate(
            {
                "targets": [
                    {
                        "id": "bad-discord",
                        "provider": "discord",
                        "kind": "discord",
                        "address": "channel-name",
                    },
                ]
            }
        )


def test_communication_targets_reject_secret_like_address(tmp_path: Path) -> None:
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(tmp_path / "runtime")
    )

    with pytest.raises(ValueError, match="appears to contain a secret"):
        repository.write_overlay(
            "root",
            {
                "targets": [
                    {
                        "id": "mail",
                        "provider": "email",
                        "kind": "email",
                        "address": "token=super-secret",
                        "policy": {"auth_profile_ref": "email.default"},
                    }
                ]
            },
        )


def test_communication_targets_resolve_directory_inheritance_and_disabled_overrides(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    directory_store = DirectoryStore(runtime_dir / "directories.json")
    directory_service = DirectoryService(directory_store)
    team = directory_service.create(parent="root", name="team")
    child = directory_service.create(parent=team.id, name="child")
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(runtime_dir)
    )
    resolver = CommunicationTargetsDirectoryResolver(
        directory_store,
        repository=repository,
    )

    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "teams-primary",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["notify"]},
                },
                {
                    "id": "discord-ops",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["write"]},
                },
            ]
        },
    )
    repository.write_overlay(
        team.id,
        {
            "targets": [
                {
                    "id": "teams-primary",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-b/channel-2",
                    "policy": {"capabilities": ["reply"]},
                }
            ]
        },
    )
    repository.write_overlay(
        child.id,
        {"targets": [{"id": "discord-ops", "disabled": True}]},
    )

    resolved = resolver.resolve_config(child.path)

    assert resolved.by_id()["teams-primary"].address == "team-b/channel-2"
    assert resolved.by_id()["teams-primary"].policy.capabilities == ["reply"]
    assert "discord-ops" not in resolved.by_id()
