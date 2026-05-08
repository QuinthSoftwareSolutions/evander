from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from agent_sandbox.directories import DirectoryService, DirectoryStore


def test_directory_config_resolver_merges_ancestor_overlays_and_tracks_provenance(
    tmp_path: Path,
) -> None:
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


def test_directory_config_resolver_validates_gateway_domain_without_changing_consumers(
    tmp_path: Path,
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryConfigResolver(
        service,
        base_gateway_config=GatewayConfig.model_validate(
            {"runtime_dir": str(tmp_path / "runtime")}
        ),
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


def test_directory_config_resolver_reports_validation_errors_for_invalid_gateway_override(
    tmp_path: Path,
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)
    resolver = DirectoryConfigResolver(
        service,
        base_gateway_config=GatewayConfig.model_validate(
            {"runtime_dir": str(tmp_path / "runtime")}
        ),
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


def test_directory_config_store_validates_delegated_teams_provider_fields(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")

    written = config_store.write(
        team.path,
        domain="providers",
        payload={
            "teams_backend": "delegated_graph_api",
            "teams_delegated_client_id": "client-123",
            "teams_delegated_identity_id": "team-a",
            "teams_delegated_credential_store_path": runtime_dir
            / "teams-delegated-credentials.json",
        },
    )

    assert written == {
        "teams_backend": "delegated_graph_api",
        "teams_delegated_client_id": "client-123",
        "teams_delegated_identity_id": "team-a",
        "teams_delegated_credential_store_path": str(
            runtime_dir / "teams-delegated-credentials.json"
        ),
    }

    resolution = resolver.resolve_domain(
        team.path,
        domain="providers",
        base_config=get_domain_base_config(GatewayConfig(), "providers"),
        validator=lambda candidate: validate_effective_domain_payload("providers", candidate),
    )

    assert resolution.effective_config["teams_backend"] == "delegated_graph_api"
    assert resolution.effective_config["teams_delegated_client_id"] == "client-123"
    assert resolution.effective_config["teams_delegated_identity_id"] == "team-a"
    assert resolution.effective_config["teams_delegated_credential_store_path"] == str(
        runtime_dir / "teams-delegated-credentials.json"
    )
    assert resolution.validation_errors == []


def test_directory_config_store_allows_retell_provider_fields(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")
    config_store.write(
        team.path,
        domain="providers",
        payload={
            "retell_api_base_url": "https://retell.test",
            "retell_api_key_env": "TEAM_RETELL_API_KEY",
            "retell_from_number": "+15550000001",
            "retell_default_agent_id": "agent-default",
            "retell_request_timeout_seconds": 12.5,
            "retell_phonebook": {
                "ops": {
                    "display_name": "Ops Duty",
                    "phone_number": "+15550000002",
                }
            },
        },
    )

    resolution = resolver.resolve_domain(
        team.path,
        domain="providers",
        base_config=get_domain_base_config(GatewayConfig(), "providers"),
        validator=lambda candidate: validate_effective_domain_payload("providers", candidate),
    )

    assert resolution.effective_config["retell_api_base_url"] == "https://retell.test"
    assert resolution.effective_config["retell_api_key_env"] == "TEAM_RETELL_API_KEY"
    assert resolution.effective_config["retell_phonebook"]["ops"]["display_name"] == "Ops Duty"
    assert resolution.validation_errors == []


def test_directory_config_store_prunes_legacy_openai_costs_provider_key_on_read(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")
    team_directory = store.get(team.id)
    assert team_directory is not None
    store.put(
        team_directory.model_copy(
            update={"metadata": {"directory_config": {"domains": ["providers"]}}}
        )
    )
    providers_path = runtime_dir / "directory-config" / "root" / "team" / "providers.json"
    providers_path.parent.mkdir(parents=True, exist_ok=True)
    providers_path.write_text(
        """
{
  "openai_api_key_env": "OPENAI_API_KEY",
  "openai_costs_api_key_env": "OPENAI_COSTS_API_KEY"
}
""".strip(),
        encoding="utf-8",
    )

    assert config_store.read(team.path, domain="providers") == {
        "openai_api_key_env": "OPENAI_API_KEY"
    }


def test_directory_config_store_writes_validated_domain_files_and_updates_directory_metadata(
    tmp_path: Path,
) -> None:
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


def test_directory_config_store_deletes_empty_domain_payload_and_clears_metadata(
    tmp_path: Path,
) -> None:
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
    assert [layer.directory_path for layer in resolution.applied_layers] == [
        "root/team",
        "root/team/child",
    ]


def test_directory_config_resolver_applies_root_overlay_as_first_directory_layer(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")

    config_store.write("root", domain="policy", payload={"default_session_ttl_seconds": 180})
    config_store.write(team.path, domain="policy", payload={"email_send_enabled": True})

    resolution = resolver.resolve_domain(team.path, domain="policy")

    assert resolution.effective_config == {
        "default_session_ttl_seconds": 180,
        "email_send_enabled": True,
    }
    assert [layer.directory_path for layer in resolution.applied_layers] == ["root", "root/team"]
    assert resolution.provenance["default_session_ttl_seconds"].directory_path == "root"


def test_directory_config_resolver_treats_root_as_top_of_chain_without_implicit_base(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    config_store.write("root", domain="policy", payload={"default_session_ttl_seconds": 180})

    resolution = resolver.resolve_domain("root", domain="policy")

    assert resolution.effective_config == {"default_session_ttl_seconds": 180}
    assert [layer.directory_path for layer in resolution.applied_layers] == ["root"]
    assert resolution.provenance["default_session_ttl_seconds"].directory_path == "root"


def test_directory_config_resolver_uses_explicit_merge_rules_for_maps_lists_scalars_and_nulls(
    tmp_path: Path,
) -> None:
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


def test_directory_config_store_validates_ado_worker_and_issue_setup_domains(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")

    worker_payload = config_store.write(
        team.path,
        domain="ado_worker",
        payload={
            "ado_project_or_solution": "/tmp/Example.slnx",
            "ado_repo_source": "/tmp/repo",
            "ado_setup_vms": False,
            "ado_bootstrap_mssql": False,
        },
    )
    issue_payload = config_store.write(
        team.path,
        domain="issue_setup",
        payload={
            "git_root": "/tmp/git",
            "git_host": "10.1.2.3",
            "git_user": "agent",
            "git_ssh_alias": "agent-git",
            "default_model": "gpt-5.3-codex",
        },
    )

    assert worker_payload == {
        "ado_project_or_solution": "/tmp/Example.slnx",
        "ado_repo_source": "/tmp/repo",
        "ado_setup_vms": False,
        "ado_bootstrap_mssql": False,
    }
    assert issue_payload == {
        "git_root": "/tmp/git",
        "git_host": "10.1.2.3",
        "git_user": "agent",
        "git_ssh_alias": "agent-git",
        "default_model": "gpt-5.3-codex",
    }


def test_directory_config_store_validates_automation_domain_and_updates_metadata(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")

    written = config_store.write(
        team.path,
        domain="automation",
        payload={
            "approved_issue_cap": 10,
            "originator_roles": ["idea_generator", "bug_finder"],
            "prioritizer_roles": ["issue_prioritizer"],
            "worker_roles": ["issue_worker"],
        },
    )

    assert written == {
        "approved_issue_cap": 10,
        "originator_roles": ["idea_generator", "bug_finder"],
        "prioritizer_roles": ["issue_prioritizer"],
        "worker_roles": ["issue_worker"],
    }
    file_path = runtime_dir / "directory-config" / "root" / "team" / "automation.json"
    assert file_path.exists()
    assert config_store.read(team.id, domain="automation") == written

    stored_directory = store.get(team.id)
    assert stored_directory is not None
    assert stored_directory.metadata["directory_config"]["domains"] == ["automation"]


def test_directory_config_resolver_reads_file_backed_automation_domain_without_metadata(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)
    team = service.create(parent="root", name="team")

    file_path = runtime_dir / "directory-config" / "root" / "team" / "automation.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        """
{
  "approved_issue_cap": 3,
  "originator_roles": ["idea_generator"],
  "prioritizer_roles": ["issue_prioritizer"],
  "worker_roles": ["issue_worker"]
}
""".strip(),
        encoding="utf-8",
    )

    assert config_store.read(team.path, domain="automation") == {
        "approved_issue_cap": 3,
        "originator_roles": ["idea_generator"],
        "prioritizer_roles": ["issue_prioritizer"],
        "worker_roles": ["issue_worker"],
    }

    resolution = resolver.resolve_domain(
        team.path,
        domain="automation",
        base_config=get_domain_base_config(GatewayConfig(), "automation"),
        validator=lambda candidate: validate_effective_domain_payload("automation", candidate),
    )

    assert resolution.effective_config == {
        "approved_issue_cap": 3,
        "originator_roles": ["idea_generator"],
        "prioritizer_roles": ["issue_prioritizer"],
        "worker_roles": ["issue_worker"],
    }
    assert resolution.provenance["approved_issue_cap"].directory_path == "root/team"
    assert resolution.validation_errors == []


def test_directory_config_store_rejects_duplicate_automation_roles(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")

    try:
        config_store.write(
            team.path,
            domain="automation",
            payload={
                "originator_roles": ["idea_generator", "idea_generator"],
            },
        )
    except ValueError as exc:
        assert "duplicate role name" in str(exc)
    else:
        raise AssertionError("expected write() to reject duplicate automation roles")


def test_directory_config_store_validates_pr_template_domain(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")
    child = service.create(parent=team.id, name="child")

    config_store.write(
        team.path, domain="pr_template", payload={"pr_template_markdown": "Team template"}
    )
    config_store.write(
        child.path, domain="pr_template", payload={"pr_template_markdown": "Child template"}
    )

    assert config_store.read(team.path, domain="pr_template") == {
        "pr_template_markdown": "Team template"
    }
    assert config_store.read(child.path, domain="pr_template") == {
        "pr_template_markdown": "Child template"
    }

    resolver = DirectoryConfigResolver(service, config_store=config_store)
    resolution = resolver.resolve_domain(
        child.path, domain="pr_template", base_config={"pr_template_markdown": ""}
    )
    assert resolution.effective_config == {"pr_template_markdown": "Child template"}


def test_directory_config_resolver_inherits_ado_worker_and_issue_setup_overlays(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)
    team = service.create(parent="root", name="team")
    child = service.create(parent=team.id, name="child")

    config_store.write(
        team.path,
        domain="ado_worker",
        payload={"ado_project_or_solution": "/tmp/Team.slnx", "ado_repo_source": "/tmp/team-repo"},
    )
    config_store.write(child.path, domain="ado_worker", payload={"ado_bootstrap_mssql": False})
    config_store.write(
        team.path,
        domain="issue_setup",
        payload={
            "git_root": "/tmp/team-git",
            "git_host": "10.1.2.3",
            "git_user": "team-bot",
            "git_ssh_alias": "team",
        },
    )
    config_store.write(child.path, domain="issue_setup", payload={"enable_guest_heartbeat": True})

    ado_resolution = resolver.resolve_domain(
        child.path,
        domain="ado_worker",
        base_config=get_domain_base_config(GatewayConfig(), "ado_worker"),
        validator=lambda candidate: validate_effective_domain_payload("ado_worker", candidate),
    )
    issue_resolution = resolver.resolve_domain(
        child.path,
        domain="issue_setup",
        base_config=get_domain_base_config(GatewayConfig(), "issue_setup"),
        validator=lambda candidate: validate_effective_domain_payload("issue_setup", candidate),
    )

    assert ado_resolution.effective_config["ado_project_or_solution"] == "/tmp/Team.slnx"
    assert ado_resolution.effective_config["ado_repo_source"] == "/tmp/team-repo"
    assert ado_resolution.effective_config["ado_bootstrap_mssql"] is False
    assert issue_resolution.effective_config["git_root"] == "/tmp/team-git"
    assert issue_resolution.effective_config["git_user"] == "team-bot"
    assert issue_resolution.effective_config["enable_guest_heartbeat"] is True


def test_directory_config_resolver_keeps_heartbeat_prompts_directory_scoped_only(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)
    team = service.create(parent="root", name="team")

    config_store.write(
        team.path,
        domain="heartbeat",
        payload={
            "host_heartbeat_pr_resume_prompt": "team PR prompt",
            "discord_resume_prompt": "team Discord prompt",
            "host_heartbeat_poll_interval_seconds": 12.0,
            "host_busy_check_script_path": "/tmp/check.sh",
        },
    )

    read_payload = config_store.read(team.path, domain="heartbeat")
    assert read_payload == {
        "host_heartbeat_pr_resume_prompt": "team PR prompt",
        "discord_resume_prompt": "team Discord prompt",
    }

    resolution = resolver.resolve_domain(
        team.path,
        domain="heartbeat",
        base_config=get_domain_base_config(GatewayConfig(), "heartbeat"),
        validator=lambda candidate: validate_effective_domain_payload("heartbeat", candidate),
    )

    assert resolution.effective_config["host_heartbeat_pr_resume_prompt"] == "team PR prompt"
    assert resolution.effective_config["discord_resume_prompt"] == "team Discord prompt"
    assert "host_heartbeat_poll_interval_seconds" not in resolution.effective_config
