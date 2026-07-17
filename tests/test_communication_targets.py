from __future__ import annotations

from pathlib import Path

import pytest

from evander.approval_notification_targets import (
    resolve_approval_notification_target_ids_for_directory,
)
from evander.directories import DirectoryService, DirectoryStore
from evander.messaging.targets import (
    CommunicationTargetsDirectoryResolver,
    CommunicationTargetsConfig,
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
    validate_communication_new_message_trigger_targets,
)
from evander.events.provider_models import CommunicationNewMessageTriggerDefinition


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


def test_approval_notification_target_resolution_requires_notify_capability(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    directory_store = DirectoryStore(runtime_dir / "directories.json")
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "ops-approvals",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["notify"]},
                },
                {
                    "id": "ops-read",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read"]},
                },
            ]
        },
    )

    assert resolve_approval_notification_target_ids_for_directory(
        runtime_dir=runtime_dir,
        directory_store=directory_store,
        directory_path="root",
        target_ids=[" Ops-Approvals ", "ops-approvals"],
    ) == ["ops-approvals"]

    with pytest.raises(ValueError, match="unknown communication target\\(s\\): missing-target"):
        resolve_approval_notification_target_ids_for_directory(
            runtime_dir=runtime_dir,
            directory_store=directory_store,
            directory_path="root",
            target_ids=["missing-target"],
        )

    with pytest.raises(ValueError, match="without notify capability: ops-read"):
        resolve_approval_notification_target_ids_for_directory(
            runtime_dir=runtime_dir,
            directory_store=directory_store,
            directory_path="root",
            target_ids=["ops-read"],
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
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
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


def test_communication_trigger_target_validation_accepts_read_capable_targets(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    directory_store = DirectoryStore(runtime_dir / "directories.json")
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "teams-primary",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "discord-ops",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read"]},
                },
            ]
        },
    )

    trigger = CommunicationNewMessageTriggerDefinition.model_validate(
        {
            "id": "comm-1",
            "display_name": "Communication",
            "type": "trigger:communication:new-message",
            "communication_target_ids": ["teams-primary", "discord-ops"],
        }
    )

    validate_communication_new_message_trigger_targets(
        trigger,
        runtime_dir=runtime_dir,
        directory_store=directory_store,
    )


def test_communication_trigger_target_validation_rejects_teams_user_targets(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    directory_store = DirectoryStore(runtime_dir / "directories.json")
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "teams-user",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "user:operator@example.com",
                    "policy": {"capabilities": ["read"]},
                }
            ]
        },
    )
    trigger = CommunicationNewMessageTriggerDefinition.model_validate(
        {
            "id": "comm-1",
            "display_name": "Communication",
            "type": "trigger:communication:new-message",
            "communication_target_ids": ["teams-user"],
        }
    )

    with pytest.raises(ValueError, match="Teams user target"):
        validate_communication_new_message_trigger_targets(
            trigger,
            runtime_dir=runtime_dir,
            directory_store=directory_store,
        )
