from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigResolver, DirectoryConfigStore
from agent_sandbox.directories import DirectoryService, DirectoryStore


def test_directory_config_resolver_merges_ancestor_overlays_and_tracks_provenance(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryConfigResolver(service)

    team = service.create(parent="root", name="team")
    sandbox = service.create(parent=team.id, name="sandbox")

    root = store.get("root")
    assert root is not None
    store.put(
        root.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "custom": {
                            "limits": {"ttl": 120},
                            "roles": ["root"],
                        }
                    }
                }
            }
        )
    )
    team_directory = store.get(team.id)
    assert team_directory is not None
    store.put(
        team_directory.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "custom": {
                            "limits": {"burst": 5},
                            "roles": ["team"],
                            "optional": None,
                        }
                    }
                }
            }
        )
    )
    sandbox_directory = store.get(sandbox.id)
    assert sandbox_directory is not None
    store.put(
        sandbox_directory.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "custom": {
                            "mode": "relaxed",
                        }
                    }
                }
            }
        )
    )

    resolution = resolver.resolve_domain(
        sandbox.path,
        domain="custom",
        base_config={
            "limits": {"ttl": 60},
            "roles": ["base"],
            "mode": "strict",
            "optional": "present",
        },
    )

    assert resolution.effective_config == {
        "limits": {"ttl": 120, "burst": 5},
        "roles": ["team"],
        "mode": "relaxed",
        "optional": None,
    }
    assert resolution.provenance["limits.ttl"].directory_path == "root"
    assert resolution.provenance["limits.burst"].directory_path == "root/team"
    assert resolution.provenance["roles"].directory_path == "root/team"
    assert resolution.provenance["mode"].directory_path == "root/team/sandbox"
    assert resolution.provenance["optional"].directory_path == "root/team"


def test_directory_config_resolver_validates_gateway_domain_without_changing_consumers(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryConfigResolver(
        service,
        base_gateway_config=GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
    )

    team = service.create(parent="root", name="team")
    team_directory = store.get(team.id)
    assert team_directory is not None
    store.put(
        team_directory.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "gateway": {
                            "discord_poll_limit": 5,
                            "email_folder_allowlist_by_mailbox": {"default": ["inbox"]},
                        }
                    }
                }
            }
        )
    )

    resolution = resolver.resolve_gateway(team.path)

    assert resolution.effective_config["discord_poll_limit"] == 5
    assert resolution.provenance["discord_poll_limit"].directory_path == "root/team"
    assert resolution.validation_errors == []


def test_directory_config_resolver_reports_validation_errors_for_invalid_gateway_override(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryConfigResolver(
        service,
        base_gateway_config=GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
    )

    team = service.create(parent="root", name="team")
    team_directory = store.get(team.id)
    assert team_directory is not None
    store.put(
        team_directory.model_copy(
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

    resolution = resolver.resolve_gateway(team.id)

    assert resolution.effective_config["discord_poll_limit"] == 999
    assert resolution.validation_errors


def test_directory_config_store_writes_validated_domain_files_and_updates_directory_metadata(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)

    team = service.create(parent="root", name="team")
    sandbox = service.create(parent=team.id, name="sandbox")

    written = config_store.write(
        sandbox.path,
        domain="policy",
        payload={
            "default_session_ttl_seconds": 120,
            "email_mailbox_allowlist": ["ops"],
        },
    )

    assert written == {
        "default_session_ttl_seconds": 120,
        "email_mailbox_allowlist": ["ops"],
    }
    file_path = runtime_dir / "directory-config" / "root" / "team" / "sandbox" / "policy.json"
    assert file_path.exists()
    assert config_store.read(sandbox.id, domain="policy") == written

    stored_directory = store.get(sandbox.id)
    assert stored_directory is not None
    assert stored_directory.metadata["directory_config"]["domains"] == ["policy"]


def test_directory_config_store_deletes_empty_domain_payload_and_clears_metadata(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)

    team = service.create(parent="root", name="team")
    config_store.write(team.path, domain="policy", payload={"default_session_ttl_seconds": 120})

    file_path = runtime_dir / "directory-config" / "root" / "team" / "policy.json"
    assert file_path.exists()

    written = config_store.write(team.path, domain="policy", payload={})

    assert written == {}
    assert not file_path.exists()
    assert not file_path.parent.exists()
    stored_directory = store.get(team.id)
    assert stored_directory is not None
    assert "directory_config" not in stored_directory.metadata


def test_directory_config_store_rejects_unknown_domain_fields(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")

    try:
        config_store.write(team.path, domain="workers", payload={"unexpected": True})
    except ValueError as exc:
        assert "does not allow fields" in str(exc)
    else:
        raise AssertionError("expected write() to reject unknown fields")


def test_directory_config_store_metadata_remains_source_of_truth_for_reads(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")

    stray_path = runtime_dir / "directory-config" / "root" / "team" / "policy.json"
    stray_path.parent.mkdir(parents=True, exist_ok=True)
    stray_path.write_text('{"default_session_ttl_seconds": 60}', encoding="utf-8")

    assert config_store.read(team.path, domain="policy") is None


def test_directory_config_resolver_prefers_file_backed_domain_overlays(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")
    child = service.create(parent=team.id, name="child")

    config_store.write(team.path, domain="policy", payload={"default_session_ttl_seconds": 90})
    config_store.write(child.path, domain="policy", payload={"email_send_enabled": True})

    resolution = resolver.resolve_domain(child.path, domain="policy", base_config={})

    assert resolution.effective_config == {
        "default_session_ttl_seconds": 90,
        "email_send_enabled": True,
    }
    assert [layer.directory_path for layer in resolution.applied_layers] == ["root/team", "root/team/child"]


def test_directory_config_resolver_applies_root_overlay_as_first_directory_layer(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")

    config_store.write("root", domain="policy", payload={"default_session_ttl_seconds": 180})
    config_store.write(team.path, domain="policy", payload={"email_send_enabled": True})

    resolution = resolver.resolve_domain(team.path, domain="policy", base_config={"default_session_ttl_seconds": 60})

    assert resolution.effective_config == {
        "default_session_ttl_seconds": 180,
        "email_send_enabled": True,
    }
    assert [layer.directory_path for layer in resolution.applied_layers] == ["root", "root/team"]
    assert resolution.provenance["default_session_ttl_seconds"].directory_path == "root"


def test_directory_config_resolver_uses_explicit_merge_rules_for_maps_lists_scalars_and_nulls(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryConfigResolver(service)

    team = service.create(parent="root", name="team")
    child = service.create(parent=team.id, name="child")

    root = store.get("root")
    assert root is not None
    store.put(
        root.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "custom": {
                            "scalar": "root",
                            "map": {"a": 1, "nested": {"x": 1}},
                            "list_value": ["root"],
                            "nullable": "set",
                        }
                    }
                }
            }
        )
    )
    team_directory = store.get(team.id)
    assert team_directory is not None
    store.put(
        team_directory.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "custom": {
                            "scalar": "team",
                            "map": {"b": 2, "nested": {"y": 2}},
                            "list_value": ["team"],
                        }
                    }
                }
            }
        )
    )
    child_directory = store.get(child.id)
    assert child_directory is not None
    store.put(
        child_directory.model_copy(
            update={
                "metadata": {
                    "config_overlays": {
                        "custom": {
                            "scalar": "child",
                            "map": {"nested": {"z": 3}},
                            "list_value": ["child"],
                            "nullable": None,
                        }
                    }
                }
            }
        )
    )

    resolution = resolver.resolve_domain(
        child.path,
        domain="custom",
        base_config={
            "scalar": "base",
            "map": {"base": 0, "nested": {"base": 0}},
            "list_value": ["base"],
            "nullable": "base",
        },
    )

    assert resolution.effective_config == {
        "scalar": "child",
        "map": {"base": 0, "a": 1, "b": 2, "nested": {"base": 0, "x": 1, "y": 2, "z": 3}},
        "list_value": ["child"],
        "nullable": None,
    }
    assert resolution.provenance["scalar"].directory_path == "root/team/child"
    assert resolution.provenance["map.a"].directory_path == "root"
    assert resolution.provenance["map.b"].directory_path == "root/team"
    assert resolution.provenance["map.nested.z"].directory_path == "root/team/child"
    assert resolution.provenance["list_value"].directory_path == "root/team/child"
    assert resolution.provenance["nullable"].directory_path == "root/team/child"
