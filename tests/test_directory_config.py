from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.policy_config import PolicyConfigResolver


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
    assert "retell_phonebook" not in resolution.effective_config
    assert resolution.validation_errors == []


def test_directory_config_store_allows_partial_azure_openai_provider_overlays(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")
    config_store.write(
        "root",
        domain="providers",
        payload={
            "openai_provider_mode": "azure_openai",
            "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
            "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            "azure_openai_model_deployments": {
                "gpt-5.4": "deployment-root",
                "gpt-4.1-mini": "deployment-root-mini",
            },
        },
    )

    written = config_store.write(
        team.path,
        domain="providers",
        payload={
            "azure_openai_model_deployments": {
                "gpt-5.4": "deployment-team",
            }
        },
    )

    assert written == {
        "azure_openai_model_deployments": {"gpt-5.4": "deployment-team"}
    }

    resolution = resolver.resolve_domain(
        team.path,
        domain="providers",
        base_config=get_domain_base_config(GatewayConfig(), "providers"),
        validator=lambda candidate: validate_effective_domain_payload("providers", candidate),
    )

    assert resolution.effective_config["openai_provider_mode"] == "azure_openai"
    assert resolution.effective_config["azure_openai_endpoint"] == (
        "https://example-resource.openai.azure.com"
    )
    assert resolution.effective_config["azure_openai_api_key_env"] == "AZURE_OPENAI_API_KEY"
    assert resolution.effective_config["azure_openai_model_deployments"] == {
        "gpt-5.4": "deployment-team",
        "gpt-4.1-mini": "deployment-root-mini",
    }
    assert resolution.validation_errors == []


def test_directory_config_resolver_reports_incomplete_effective_azure_openai_config(
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
        domain="providers",
        payload={"openai_provider_mode": "azure_openai"},
    )

    resolution = resolver.resolve_domain(
        team.path,
        domain="providers",
        base_config=get_domain_base_config(GatewayConfig(), "providers"),
        validator=lambda candidate: validate_effective_domain_payload("providers", candidate),
    )

    assert resolution.effective_config["openai_provider_mode"] == "azure_openai"
    assert resolution.validation_errors
    message = resolution.validation_errors[0]
    assert "azure_openai_endpoint" in message
    assert "azure_openai_api_key_env" in message
    assert "azure_openai_model_deployments" in message


def test_directory_config_store_allows_npm_audit_facade_provider_fields(
    tmp_path: Path,
) -> None:
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
            "npm_audit_facade_timeout_seconds": 12.5,
            "npm_audit_facade_audit_registry_url": "https://registry.npmjs.org/",
            "npm_audit_facade_package_registry_url": "http://nexus.test:8081/repository/npm-all/",
        },
    )

    assert written == {
        "npm_audit_facade_timeout_seconds": 12.5,
        "npm_audit_facade_audit_registry_url": "https://registry.npmjs.org",
        "npm_audit_facade_package_registry_url": "http://nexus.test:8081/repository/npm-all",
    }

    resolution = resolver.resolve_domain(
        team.path,
        domain="providers",
        base_config=get_domain_base_config(GatewayConfig(), "providers"),
        validator=lambda candidate: validate_effective_domain_payload("providers", candidate),
    )

    assert resolution.effective_config["npm_audit_facade_timeout_seconds"] == 12.5
    assert (
        resolution.effective_config["npm_audit_facade_audit_registry_url"]
        == "https://registry.npmjs.org"
    )
    assert (
        resolution.effective_config["npm_audit_facade_package_registry_url"]
        == "http://nexus.test:8081/repository/npm-all"
    )
    assert resolution.validation_errors == []


def test_directory_config_store_rejects_npm_audit_facade_host_fields_in_providers_domain(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    team = service.create(parent="root", name="team")

    try:
        config_store.write(
            team.path,
            domain="providers",
            payload={"npm_audit_facade_host": "127.0.0.1", "npm_audit_facade_port": 18181},
        )
    except ValueError as exc:
        assert "does not allow fields" in str(exc)
    else:
        raise AssertionError("expected write() to reject npm audit facade host fields")


def test_directory_config_store_allows_npm_audit_facade_vm_fields(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")
    written = config_store.write(
        team.path,
        domain="vm",
        payload={"npm_audit_facade_host": "127.0.0.1", "npm_audit_facade_port": 18181},
    )

    assert written == {
        "npm_audit_facade_host": "127.0.0.1",
        "npm_audit_facade_port": 18181,
    }

    resolution = resolver.resolve_domain(
        team.path,
        domain="vm",
        base_config=get_domain_base_config(GatewayConfig(), "vm"),
        validator=lambda candidate: validate_effective_domain_payload("vm", candidate),
    )

    assert resolution.effective_config["npm_audit_facade_host"] == "127.0.0.1"
    assert resolution.effective_config["npm_audit_facade_port"] == 18181
    assert resolution.validation_errors == []


def test_directory_config_store_allows_semantic_provider_fields(tmp_path: Path) -> None:
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
            "semantic_store_provider": "qdrant",
            "qdrant_base_url": "http://qdrant.internal:6333/",
            "qdrant_api_key_env": "TEAM_QDRANT_API_KEY",
            "semantic_embedding_provider": "openai",
            "semantic_embedding_model": "text-embedding-3-small",
            "semantic_embedding_dimensions": 1536,
            "semantic_vector_distance": "cosine",
        },
    )

    assert written["qdrant_base_url"] == "http://qdrant.internal:6333"
    assert written["semantic_vector_distance"] == "Cosine"

    resolution = resolver.resolve_domain(
        team.path,
        domain="providers",
        base_config=get_domain_base_config(GatewayConfig(), "providers"),
        validator=lambda candidate: validate_effective_domain_payload("providers", candidate),
    )

    assert resolution.effective_config["semantic_store_provider"] == "qdrant"
    assert resolution.effective_config["qdrant_api_key_env"] == "TEAM_QDRANT_API_KEY"
    assert resolution.effective_config["semantic_embedding_dimensions"] == 1536
    assert resolution.validation_errors == []


def test_directory_config_store_allows_directory_contacts(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")
    config_store.write(
        team.path,
        domain="contacts",
        payload={
            "contacts": {
                "ops": {
                    "name": "Ops Duty",
                    "description": "Deployment window escalation contact",
                    "email_addresses": ["ops@example.com"],
                    "phone_numbers": [
                        {"label": "mobile", "number": "+15550000002", "primary": True}
                    ],
                }
            },
            "user_contact_links": {"Operator@Example.com": "ops"},
        },
    )

    resolution = resolver.resolve_domain(
        team.path,
        domain="contacts",
        base_config=get_domain_base_config(GatewayConfig(), "contacts"),
        validator=lambda candidate: validate_effective_domain_payload("contacts", candidate),
    )

    assert resolution.effective_config["contacts"]["ops"]["name"] == "Ops Duty"
    assert resolution.effective_config["user_contact_links"]["operator@example.com"] == "ops"
    assert resolution.validation_errors == []


def test_directory_contact_links_support_inherited_clear(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = DirectoryConfigResolver(service, config_store=config_store)

    team = service.create(parent="root", name="team")
    config_store.write(
        "root",
        domain="contacts",
        payload={
            "contacts": {
                "ops": {
                    "name": "Ops Duty",
                    "phone_numbers": [
                        {"label": "mobile", "number": "+15550000002", "primary": True}
                    ],
                }
            },
            "user_contact_links": {"operator@example.com": "ops"},
        },
    )
    config_store.write(
        team.path,
        domain="contacts",
        payload={"user_contact_links": {"operator@example.com": None}},
    )

    resolution = resolver.resolve_domain(
        team.path,
        domain="contacts",
        base_config=get_domain_base_config(GatewayConfig(), "contacts"),
        validator=lambda candidate: validate_effective_domain_payload("contacts", candidate),
    )

    assert resolution.effective_config["contacts"]["ops"]["name"] == "Ops Duty"
    assert resolution.effective_config["user_contact_links"]["operator@example.com"] is None
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


def test_policy_config_resolver_inherits_approval_notification_targets(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = PolicyConfigResolver(GatewayConfig(runtime_dir=runtime_dir))

    team = service.create(parent="root", name="team")
    sandbox = service.create(parent=team.id, name="sandbox")

    config_store.write(
        "root",
        domain="policy",
        payload={
            "approval_notification_target_ids": [
                " ROOT-OPS ",
                "root-ops",
                "ROOT-BACKUP",
            ]
        },
    )
    config_store.write(team.path, domain="policy", payload={"default_session_ttl_seconds": 120})
    config_store.write(
        sandbox.path,
        domain="policy",
        payload={
            "approval_notification_target_ids": [
                " CHILD-OPS ",
                "child-ops",
                "",
                "CHILD-BACKUP",
            ]
        },
    )

    assert resolver.resolve_for_directory("root").approval_notification_target_ids == [
        "root-ops",
        "root-backup",
    ]
    assert resolver.resolve_for_directory(team.path).approval_notification_target_ids == [
        "root-ops",
        "root-backup",
    ]
    assert resolver.resolve_for_directory(sandbox.path).approval_notification_target_ids == [
        "child-ops",
        "child-backup",
    ]


def test_policy_config_resolver_inherits_tool_argument_policies(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    service = DirectoryService(store)
    config_store = DirectoryConfigStore(store, runtime_dir=runtime_dir)
    resolver = PolicyConfigResolver(GatewayConfig(runtime_dir=runtime_dir))

    team = service.create(parent="root", name="team")
    sandbox = service.create(parent=team.id, name="sandbox")

    policy_payload = {
        "tool_argument_policies": {
            "create_ado_backlog_item": [
                {
                    "id": "ado-item-policy",
                    "name": "ADO backlog item guardrails",
                    "constraints": [
                        {
                            "id": "project",
                            "path": "project",
                            "operator": "fixed",
                            "value": "Example Project",
                        },
                        {
                            "id": "type",
                            "path": "type",
                            "operator": "enum",
                            "values": ["Task", "Product Backlog Item"],
                        },
                        {
                            "id": "title",
                            "path": "title",
                            "operator": "required",
                        },
                        {
                            "id": "iteration",
                            "path": "iteration",
                            "operator": "required",
                        },
                        {
                            "id": "tags",
                            "path": "tags[]",
                            "operator": "denylist",
                            "values": ["blocked-tag"],
                        },
                    ],
                }
            ]
        }
    }

    expected_policy_payload = GatewayConfig.model_validate(policy_payload).model_dump(
        mode="json", include={"tool_argument_policies"}
    )

    written = config_store.write(team.path, domain="policy", payload=policy_payload)

    assert written == expected_policy_payload
    assert config_store.read(team.path, domain="policy") == expected_policy_payload
    assert (
        resolver.resolve_for_directory(team.path).model_dump(
            mode="json", include={"tool_argument_policies"}
        )
        == expected_policy_payload
    )

    config_store.write(sandbox.path, domain="policy", payload={"default_session_ttl_seconds": 120})
    assert (
        resolver.resolve_for_directory(sandbox.path).model_dump(
            mode="json", include={"tool_argument_policies"}
        )
        == expected_policy_payload
    )


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
        config_store.write(team.path, domain="wake", payload={"unexpected": True})
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
        payload={"ado_repo_source": "/tmp/repo"},
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
        "ado_repo_source": "/tmp/repo",
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
        payload={"ado_repo_source": "/tmp/team-repo"},
    )
    config_store.write(child.path, domain="ado_worker", payload={"create_prs_as_draft": True})
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

    assert ado_resolution.effective_config["ado_repo_source"] == "/tmp/team-repo"
    assert ado_resolution.effective_config["create_prs_as_draft"] is True
    assert "ado_project_or_solution" not in ado_resolution.effective_config
    assert "ado_bootstrap_mssql" not in ado_resolution.effective_config
    assert issue_resolution.effective_config["git_root"] == "/tmp/team-git"
    assert issue_resolution.effective_config["git_user"] == "team-bot"
    assert issue_resolution.effective_config["enable_guest_heartbeat"] is True


def test_directory_config_resolver_keeps_wake_prompts_directory_scoped_only(
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
        domain="wake",
        payload={
            "pr_feedback_resume_prompt": "team PR prompt",
            "discord_resume_prompt": "team Discord prompt",
        },
    )

    read_payload = config_store.read(team.path, domain="wake")
    assert read_payload == {
        "pr_feedback_resume_prompt": "team PR prompt",
        "discord_resume_prompt": "team Discord prompt",
    }

    resolution = resolver.resolve_domain(
        team.path,
        domain="wake",
        base_config=get_domain_base_config(GatewayConfig(), "wake"),
        validator=lambda candidate: validate_effective_domain_payload("wake", candidate),
    )

    assert resolution.effective_config["pr_feedback_resume_prompt"] == "team PR prompt"
    assert resolution.effective_config["discord_resume_prompt"] == "team Discord prompt"
    assert "wake_poll_interval_seconds" not in resolution.effective_config

    with pytest.raises(ValueError, match="wake_poll_interval_seconds"):
        config_store.write(
            team.path,
            domain="wake",
            payload={"wake_poll_interval_seconds": 12.0},
        )
