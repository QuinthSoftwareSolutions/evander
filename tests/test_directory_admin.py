import json
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.admin.directory import DirectoryAdminService
from agent_sandbox.directories import DirectoryStore


def test_directory_admin_service_supports_tree_list_get_create_update_and_delete(
    tmp_path: Path,
) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    created = admin.create_child(parent="root", name="team")
    assert created.ok is True
    team = created.data.details.directory
    assert team.path == "root/team"

    child = admin.create_child(parent=team.id, name="sandbox")
    assert child.ok is True
    sandbox = child.data.details.directory

    listed = admin.list("root")
    assert listed.ok is True
    assert [item.path for item in listed.data.children] == ["root/team"]

    fetched = admin.get(team.id)
    assert fetched.ok is True
    assert [item.path for item in fetched.data.children] == ["root/team/sandbox"]

    moved = admin.update(sandbox.id, new_name="lab", new_parent="root")
    assert moved.ok is True
    assert moved.data.details.directory.path == "root/lab"

    tree = admin.tree()
    assert tree.ok is True
    assert [child.directory.path for child in tree.data.children] == ["root/lab", "root/team"]

    deleted = admin.delete(team.id)
    assert deleted.ok is True
    assert deleted.data.deleted_count == 1


def test_directory_admin_service_returns_structured_errors_for_invalid_operations(
    tmp_path: Path,
) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    rename_root = admin.update("root", new_name="other")
    assert rename_root.ok is False
    assert rename_root.errors[0].message == "root directory cannot be renamed"

    no_change = admin.update("root")
    assert no_change.ok is False
    assert no_change.errors[0].code == "invalid_update"

    delete_root = admin.delete("root", recursive=True)
    assert delete_root.ok is False
    assert delete_root.errors[0].message == "root directory cannot be deleted"


def test_directory_admin_service_reads_writes_and_resolves_directory_config(tmp_path: Path) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    parent = admin.create_child(parent="root", name="team").data.details.directory
    child = admin.create_child(parent=parent.id, name="child").data.details.directory

    parent_write = admin.write_domain_config(
        parent.id,
        domain="policy",
        payload={
            "default_session_ttl_seconds": 120,
            "approval_required_tools": ["approval_submit", "approval_submit"],
        },
    )
    assert parent_write.ok is True
    child_write = admin.write_domain_config(
        child.path,
        domain="policy",
        payload={"email_send_enabled": True},
    )
    assert child_write.ok is True

    read_parent = admin.read_domain_config(parent.path, domain="policy")
    assert read_parent.ok is True
    assert read_parent.data.exists is True
    assert read_parent.data.config == {
        "default_session_ttl_seconds": 120,
        "approval_required_tools": ["approval_submit"],
    }

    resolved = admin.compute_effective_config(child.id, domain="policy")
    assert resolved.ok is True
    assert resolved.data.resolution.effective_config["default_session_ttl_seconds"] == 120
    assert resolved.data.resolution.effective_config["approval_required_tools"] == [
        "approval_submit"
    ]
    assert resolved.data.resolution.effective_config["email_send_enabled"] is True
    assert resolved.validation_diagnostics == []


def test_directory_admin_service_validates_approval_notification_targets_on_write_and_effective_read(
    tmp_path: Path,
) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    admin._communication_targets_repository.write_overlay(
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
    team = admin.create_child(parent="root", name="team").data.details.directory

    written = admin.write_domain_config(
        team.path,
        domain="policy",
        payload={
            "default_session_ttl_seconds": 120,
            "approval_notification_target_ids": [" OPS-Approvals ", "ops-approvals"],
        },
    )
    assert written.ok is True
    assert written.data is not None
    assert written.data.config["approval_notification_target_ids"] == ["ops-approvals"]

    read_back = admin.read_domain_config(team.id, domain="policy")
    assert read_back.ok is True
    assert read_back.data is not None
    assert read_back.data.config == {
        "default_session_ttl_seconds": 120,
        "approval_notification_target_ids": ["ops-approvals"],
    }

    missing_target = admin.write_domain_config(
        team.path,
        domain="policy",
        payload={"approval_notification_target_ids": ["missing-target"]},
    )
    assert missing_target.ok is False
    assert "unknown communication target(s): missing-target" in missing_target.errors[0].message

    non_notify_target = admin.write_domain_config(
        team.path,
        domain="policy",
        payload={"approval_notification_target_ids": ["ops-read"]},
    )
    assert non_notify_target.ok is False
    assert "without notify capability: ops-read" in non_notify_target.errors[0].message

    policy_path = (
        config.runtime_dir / "directory-config" / "root" / "team" / "policy.json"
    )
    policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
    policy_payload["approval_notification_target_ids"] = ["missing-target"]
    policy_path.write_text(
        json.dumps(policy_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    resolved = admin.compute_effective_config(team.path, domain="policy")
    assert resolved.ok is True
    assert any(
        "unknown communication target(s): missing-target" in diagnostic.message
        for diagnostic in resolved.validation_diagnostics
    )

    legacy_read = admin.read_domain_config(team.path, domain="policy")
    assert legacy_read.ok is True
    assert legacy_read.data is not None
    assert legacy_read.data.config["approval_notification_target_ids"] == ["missing-target"]


def test_directory_admin_root_effective_config_has_no_implicit_global_base(tmp_path: Path) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    written = admin.write_domain_config(
        "root", domain="policy", payload={"default_session_ttl_seconds": 120}
    )
    assert written.ok is True

    resolved = admin.compute_effective_config("root", domain="policy")

    assert resolved.ok is True
    assert resolved.data.resolution.effective_config == {"default_session_ttl_seconds": 120}
    assert [layer.directory_path for layer in resolved.data.resolution.applied_layers] == ["root"]


def test_directory_admin_empty_write_removes_domain_file_and_existence(tmp_path: Path) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    team = admin.create_child(parent="root", name="team").data.details.directory
    assert (
        admin.write_domain_config(
            team.path, domain="policy", payload={"default_session_ttl_seconds": 120}
        ).ok
        is True
    )

    cleared = admin.write_domain_config(team.path, domain="policy", payload={})
    assert cleared.ok is True
    read_back = admin.read_domain_config(team.path, domain="policy")
    assert read_back.ok is True
    assert read_back.data.exists is False
    assert read_back.data.config is None


def test_directory_admin_delete_removes_directory_config_tree(tmp_path: Path) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    team = admin.create_child(parent="root", name="team").data.details.directory
    write = admin.write_domain_config(
        team.path, domain="policy", payload={"default_session_ttl_seconds": 120}
    )
    assert write.ok is True

    config_path = config.runtime_dir / "directory-config" / "root" / "team" / "policy.json"
    assert config_path.exists()

    deleted = admin.delete(team.path)
    assert deleted.ok is True
    assert not config_path.exists()
    assert not config_path.parent.exists()


def test_directory_admin_service_surfaces_validation_diagnostics_and_config_write_failures(
    tmp_path: Path,
) -> None:
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(tmp_path / "runtime"), "enable_directory_scoped_config": True}
    )
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    team = admin.create_child(parent="root", name="team").data.details.directory

    invalid_write = admin.write_domain_config(
        team.id, domain="wake", payload={"unexpected": True}
    )
    assert invalid_write.ok is False
    assert invalid_write.errors[0].message.startswith(
        "directory config domain 'wake' does not allow fields"
    )

    directory = store.get(team.id)
    assert directory is not None
    store.put(
        directory.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "gateway": {
                            "discord_poll_limit": 999,
                        }
                    }
                }
            }
        )
    )

    resolved = admin.compute_effective_config(team.id, domain="gateway")
    assert resolved.ok is True
    assert resolved.validation_diagnostics


def test_directory_admin_service_rejects_edit_surfaces_when_feature_flag_disabled(
    tmp_path: Path,
) -> None:
    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    store = DirectoryStore(config.directories_store_path)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)

    created = admin.create_child(parent="root", name="team")
    assert created.ok is False
    assert created.errors[0].code == "feature_disabled"

    write = admin.write_domain_config(
        "root", domain="policy", payload={"default_session_ttl_seconds": 120}
    )
    assert write.ok is False
    assert write.errors[0].code == "feature_disabled"
