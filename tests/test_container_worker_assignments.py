from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from typer.testing import CliRunner

from evander.admin.services.container_worker_assignment_service import (
    ContainerWorkerAssignmentError,
    ContainerWorkerAssignmentService,
)
from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryStore
from evander.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from evander.execution.worker_assignments import ContainerWorkerAssignmentRepository
from evander.models import SessionManifest
from evander.provider_config import ProviderConfigResolver
from evander.repositories.session_manifests import JsonSessionManifestRepository


def config_for(tmp_path, worker="old"):
    return GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url=f"http://{worker}.test:11888",
        container_worker_token_env=f"{worker.upper()}_WORKER_TOKEN",
        container_worker_guest_gateway_url=f"http://gateway-{worker}.test:8000/mcp",
        container_worker_git_allowed_from=f"{worker}.test",
        container_worker_mssql_host=f"sql-{worker}.test",
        container_worker_mssql_port=1434 if worker == "old" else 1433,
    )


def add_session(config, session_id="existing", **kwargs):
    session = SessionManifest(
        session_id=session_id,
        vm_provider="container_worker_vm",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        **kwargs,
    )
    JsonSessionManifestRepository(config.session_store_path).upsert_session(session)
    return session


def test_existing_assignment_survives_default_change_restart_and_metadata_edit(
    tmp_path, monkeypatch
):
    old = config_for(tmp_path)
    session = add_session(old, metadata={"worker_url": old.container_worker_base_url})
    monkeypatch.setenv("OLD_WORKER_TOKEN", "secret-must-not-be-persisted")
    ProviderConfigResolver(old).resolve_for_session_id(session.session_id)
    new = config_for(tmp_path, "new").model_copy(
        update={"model_provider_mode": "anthropic", "container_worker_job_timeout_seconds": 42}
    )
    session.metadata["worker_url"] = new.container_worker_base_url
    JsonSessionManifestRepository(old.session_store_path).upsert_session(session)

    resolved = ProviderConfigResolver(new).resolve_for_session_id(session.session_id)

    assert resolved.container_worker_base_url == old.container_worker_base_url
    assert resolved.container_worker_token_env == "OLD_WORKER_TOKEN"
    assert resolved.container_worker_guest_gateway_url == old.container_worker_guest_gateway_url
    assert resolved.container_worker_git_allowed_from == old.container_worker_git_allowed_from
    assert resolved.container_worker_mssql_host == old.container_worker_mssql_host
    assert resolved.container_worker_mssql_port == 1434
    assert resolved.model_provider_mode == "anthropic"
    assert resolved.container_worker_job_timeout_seconds == 42
    assert (
        ProviderConfigResolver(new).resolve_for_directory("root").container_worker_base_url
        == new.container_worker_base_url
    )
    assert (
        ContainerWorkerAssignmentService(new).resolve("fresh").container_worker_base_url
        == new.container_worker_base_url
    )
    data = ContainerWorkerAssignmentRepository(tmp_path).path.read_bytes()
    assert b"secret-must-not-be-persisted" not in data


def test_legacy_execution_evidence_cannot_be_rebound_to_new_default(tmp_path):
    old = config_for(tmp_path)
    add_session(old)
    repository = ContainerWorkerExecutionRepository(tmp_path / "container-worker-executions.db")
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="old-job",
            session_id="existing",
            worker_job_id="old-job",
            worker_url=old.container_worker_base_url,
            target_name="existing",
            last_status="exited",
        )
    )
    with pytest.raises(ContainerWorkerAssignmentError, match="pin-worker-assignments"):
        ProviderConfigResolver(config_for(tmp_path, "new")).resolve_for_session_id("existing")
    assert ContainerWorkerAssignmentRepository(tmp_path).get("existing") is None

    # The explicit upgrade step captures the old connection before cutover.
    ProviderConfigResolver(old).resolve_for_session_id("existing")
    assert (
        ProviderConfigResolver(config_for(tmp_path, "new"))
        .resolve_for_session_id("existing")
        .container_worker_token_env
        == "OLD_WORKER_TOKEN"
    )


def test_legacy_metadata_worker_uses_retained_connection_not_new_token(tmp_path):
    old = config_for(tmp_path)
    ContainerWorkerAssignmentService(old).resolve("known-old-agent")
    session = add_session(old, metadata={"worker_url": old.container_worker_base_url})
    resolved = ContainerWorkerAssignmentService(config_for(tmp_path, "new")).resolve(
        session.session_id, session=session
    )
    assert resolved.container_worker_token_env == "OLD_WORKER_TOKEN"


def test_concurrent_first_assignment_has_one_durable_winner(tmp_path):
    barrier = Barrier(2)

    def assign(worker):
        service = ContainerWorkerAssignmentService(config_for(tmp_path, worker))
        barrier.wait()
        return service.resolve("same-session").container_worker_base_url

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(assign, ["old", "new"]))
    assert results[0] == results[1]
    assert (
        ContainerWorkerAssignmentRepository(tmp_path).get("same-session").worker_url == results[0]
    )


def test_directory_default_change_preserves_pinned_sessions(tmp_path):
    config = config_for(tmp_path)
    store = DirectoryStore(config.directories_store_path)
    store.ensure_root()
    add_session(config)
    resolver = ProviderConfigResolver(config, directory_store=store)
    resolver.resolve_for_session_id("existing")
    DirectoryConfigStore(store, runtime_dir=tmp_path).write(
        "root",
        domain="providers",
        payload={
            "container_worker_base_url": "http://new.test:11888",
            "container_worker_token_env": "NEW_WORKER_TOKEN",
        },
    )
    resolver = ProviderConfigResolver(config, directory_store=store)
    assert resolver.resolve_for_directory("root").container_worker_token_env == "NEW_WORKER_TOKEN"
    assert (
        resolver.resolve_for_session_id("existing").container_worker_token_env == "OLD_WORKER_TOKEN"
    )


def test_pin_command_previews_then_persists_legacy_agents(tmp_path, monkeypatch):
    from evander import cli

    config = config_for(tmp_path)
    add_session(config)  # Older manifests can lack worker metadata and execution history.
    monkeypatch.setattr(cli, "_load_runtime_config_from_option", lambda _path: config)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["execution", "pin-worker-assignments"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["applied"] is False
    assert not ContainerWorkerAssignmentRepository(tmp_path).path.exists()
    result = runner.invoke(cli.app, ["execution", "pin-worker-assignments", "--apply"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["count"] == 1
    assert (
        ContainerWorkerAssignmentRepository(tmp_path).get("existing").worker_url
        == config.container_worker_base_url
    )


def test_pin_command_validates_all_agents_before_writing(tmp_path, monkeypatch):
    from evander import cli

    config = config_for(tmp_path)
    add_session(config, "a-valid")
    add_session(config, "z-unknown-worker", metadata={"worker_url": "http://unknown.test"})
    monkeypatch.setattr(cli, "_load_runtime_config_from_option", lambda _path: config)
    result = CliRunner().invoke(cli.app, ["execution", "pin-worker-assignments", "--apply"])
    assert result.exit_code != 0
    assert "original worker configuration" in result.output
    assert not ContainerWorkerAssignmentRepository(tmp_path).path.exists()


def test_admin_default_change_pins_unobserved_legacy_agents_before_saving(tmp_path):
    from evander.admin.directory import DirectoryAdminService

    config = config_for(tmp_path).model_copy(update={"enable_directory_scoped_config": True})
    store = DirectoryStore(config.directories_store_path)
    store.ensure_root()
    add_session(config)
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)
    result = admin.write_domain_config(
        "root",
        domain="providers",
        payload={
            "container_worker_base_url": "http://new.test:11888",
            "container_worker_token_env": "NEW_WORKER_TOKEN",
        },
    )
    assert result.ok, result.errors
    resolver = ProviderConfigResolver(config, directory_store=store)
    assert (
        resolver.resolve_for_session_id("existing").container_worker_token_env == "OLD_WORKER_TOKEN"
    )
    assert resolver.resolve_for_directory("root").container_worker_token_env == "NEW_WORKER_TOKEN"


def test_admin_rejects_cutover_when_legacy_worker_connection_is_unknown(tmp_path):
    from evander.admin.directory import DirectoryAdminService

    config = config_for(tmp_path).model_copy(update={"enable_directory_scoped_config": True})
    store = DirectoryStore(config.directories_store_path)
    store.ensure_root()
    add_session(config, metadata={"worker_url": "http://unknown.test"})
    admin = DirectoryAdminService.from_store(directory_store=store, gateway_config=config)
    result = admin.write_domain_config(
        "root",
        domain="providers",
        payload={
            "container_worker_base_url": "http://new.test:11888",
        },
    )
    assert not result.ok
    assert "original worker configuration" in result.errors[0].message
    assert (
        ProviderConfigResolver(config, directory_store=store)
        .resolve_for_directory("root")
        .container_worker_token_env
        == "OLD_WORKER_TOKEN"
    )


def test_cli_existing_job_uses_assigned_token_even_when_new_token_is_unset(tmp_path, monkeypatch):
    from evander import cli

    old = config_for(tmp_path)
    ContainerWorkerAssignmentService(old).resolve("existing")
    ContainerWorkerExecutionRepository(tmp_path / "container-worker-executions.db").put(
        ContainerWorkerExecutionRecord(
            execution_id="job",
            session_id="existing",
            worker_job_id="job",
            worker_url=old.container_worker_base_url,
            target_name="existing",
        )
    )
    monkeypatch.setenv("OLD_WORKER_TOKEN", "test-token")
    monkeypatch.delenv("NEW_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(
        cli, "_load_runtime_config_from_option", lambda _path: config_for(tmp_path, "new")
    )
    backend = cli._build_execution_backend_for_kind(tmp_path, target="job")
    assert backend.client.base_url == old.container_worker_base_url
    assert backend.client.token_env == "OLD_WORKER_TOKEN"


def test_resolver_preserves_derived_relay_endpoints_after_gateway_default_changes(tmp_path):
    from evander.effects.providers.agent_session import container_worker_guest_openai_base_url

    old = config_for(tmp_path).model_copy(
        update={"gateway_url": "http://private-old.test:8000/mcp"}
    )
    ContainerWorkerAssignmentService(old).resolve("existing")
    new = config_for(tmp_path, "new").model_copy(
        update={"gateway_url": "http://vpn-new.test:8000/mcp"}
    )
    resolved = ContainerWorkerAssignmentService(new).resolve("existing")
    assert container_worker_guest_openai_base_url(resolved) == "http://private-old.test:11434/v1"
    assert container_worker_guest_openai_base_url(new) == "http://vpn-new.test:11434/v1"


def test_named_worker_inherits_omitted_settings_through_config_roundtrips(tmp_path):
    config = GatewayConfig.model_validate(
        {
            **config_for(tmp_path).model_dump(mode="json"),
            "container_worker_default_image": "shared:latest",
            "container_workers": {
                "fast": {
                    "display_name": "Fast worker",
                    "base_url": "http://fast.test:11888/",
                    "token_env": "FAST_TOKEN",
                    "guest_gateway_url": "http://vpn.test:8000/mcp",
                    "git_allowed_from": None,
                },
            },
        }
    )
    config = GatewayConfig.model_validate_json(config.model_dump_json())
    resolver = ProviderConfigResolver(config)
    config = resolver.resolve_for_directory("root")
    service = ContainerWorkerAssignmentService(config)

    selected = service.resolve("chosen", worker_id="fast")
    assert selected.container_worker_base_url == "http://fast.test:11888"
    assert selected.container_worker_token_env == "FAST_TOKEN"
    assert selected.container_worker_guest_gateway_url == "http://vpn.test:8000/mcp"
    assert selected.container_worker_git_allowed_from is None
    assert selected.container_worker_default_image == "shared:latest"
    assert service.repository.get("chosen").worker_id == "fast"
    assert service.resolve("default").container_worker_token_env == "OLD_WORKER_TOKEN"

    # Even removal or replacement of the catalog entry cannot move a retry.
    changed = config.model_copy(update={"container_workers": {}})
    retry = ContainerWorkerAssignmentService(changed).resolve("chosen", worker_id="fast")
    assert retry.container_worker_token_env == "FAST_TOKEN"
    assert (
        ContainerWorkerAssignmentService(changed).resolve("chosen").container_worker_token_env
        == "FAST_TOKEN"
    )
    with pytest.raises(ContainerWorkerAssignmentError, match="different worker assignment"):
        service.resolve("chosen", worker_id="other")
    with pytest.raises(ContainerWorkerAssignmentError, match="different worker assignment"):
        service.resolve("default", worker_id="fast")


def test_named_worker_override_rejects_unknown_or_legacy_without_pinning(tmp_path):
    config = GatewayConfig.model_validate(
        {
            **config_for(tmp_path).model_dump(mode="json"),
            "container_workers": {
                "fast": {"base_url": "http://fast.test", "token_env": "FAST_TOKEN"}
            },
        }
    )
    service = ContainerWorkerAssignmentService(config)
    with pytest.raises(ContainerWorkerAssignmentError, match="Unknown configured worker_id"):
        service.resolve("fresh", worker_id="http://arbitrary.test")
    session = add_session(config)
    with pytest.raises(ContainerWorkerAssignmentError, match="cannot change an existing session"):
        service.resolve(session.session_id, worker_id="fast")
    assert not service.repository.path.exists()


def test_named_workers_respect_directory_inheritance(tmp_path):
    config = config_for(tmp_path).model_copy(update={"enable_directory_scoped_config": True})
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    from evander.admin.directory import DirectoryAdminService

    admin = DirectoryAdminService.from_store(directory_store=directories, gateway_config=config)
    assert admin.create_child(parent="root", name="team").ok
    assert admin.write_domain_config(
        "root",
        domain="providers",
        payload={
            "container_workers": {
                "fast": {"base_url": "http://fast.test", "token_env": "FAST_TOKEN"}
            },
        },
    ).ok
    assert admin.write_domain_config(
        "root/team",
        domain="providers",
        payload={
            "container_workers": {
                "team": {
                    "display_name": "Team worker",
                    "base_url": "http://team.test",
                    "token_env": "TEAM_TOKEN",
                }
            },
        },
    ).ok
    team = ProviderConfigResolver(config, directory_store=directories).resolve_for_directory(
        "root/team"
    )
    assert team.container_workers["team"].display_name == "Team worker"
    assert set(team.container_workers) == {"fast", "team"}
    assert (
        ContainerWorkerAssignmentService(team)
        .resolve("team", worker_id="fast")
        .container_worker_token_env
        == "FAST_TOKEN"
    )


@pytest.mark.parametrize(
    "profile",
    [
        {"base_url": "http://fast.test"},
        {"base_url": "http://user:secret@fast.test", "token_env": "FAST_TOKEN"},
        {"base_url": "http://fast.test", "token_env": "invalid token"},
        {"base_url": "http://fast.test", "token_env": "FAST_TOKEN", "token": "secret"},
    ],
)
def test_named_worker_config_rejects_incomplete_or_secret_values(tmp_path, profile):
    with pytest.raises(ValueError):
        GatewayConfig(runtime_dir=tmp_path, container_workers={"fast": profile})


def test_telemetry_does_not_send_default_credentials_to_an_unresolved_worker(monkeypatch):
    from types import SimpleNamespace

    from evander.admin.services.capacity_health_service import CapacityHealthAdminService
    from evander.capacity_health import TelemetryStatus

    monkeypatch.setenv("EVANDER_WORKER_TOKEN", "default-worker-secret")

    def unexpected_client(**kwargs):
        pytest.fail("must not contact a worker without its own connection settings")

    monkeypatch.setattr(
        "evander.admin.services.capacity_health_service.ContainerWorkerClient", unexpected_client
    )
    entry = CapacityHealthAdminService(SimpleNamespace())._capacity_worker_telemetry_entry(
        {
            "worker_url": "http://unresolved.test",
            "token_env": None,
        }
    )
    assert entry.healthz_status == TelemetryStatus.MISSING
    assert entry.telemetry_status == TelemetryStatus.MISSING
    assert "connection settings" in entry.errors[0].message


def test_pin_command_includes_execution_only_sessions_and_directory_evidence(tmp_path, monkeypatch):
    from evander import cli
    from evander.admin.directory import DirectoryAdminService

    config = config_for(tmp_path).model_copy(update={"enable_directory_scoped_config": True})
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    admin = DirectoryAdminService.from_store(directory_store=directories, gateway_config=config)
    assert admin.create_child(parent="root", name="team").ok
    assert admin.create_child(parent="root/team", name="child").ok
    DirectoryConfigStore(directories, runtime_dir=tmp_path).write(
        "root/team",
        domain="providers",
        payload={
            "container_worker_base_url": "http://team.test:11888",
            "container_worker_token_env": "TEAM_TOKEN",
        },
    )
    repository = ContainerWorkerExecutionRepository(tmp_path / "container-worker-executions.db")
    for execution_id, session_id, directory in [
        ("first", "cli-only", "root/team/child"),
        ("second", "cli-only", "root/team/child"),
        ("manifest-job", "manifest", "root/team"),
        ("outside", "outside", "root"),
    ]:
        repository.put(
            ContainerWorkerExecutionRecord(
                execution_id=execution_id,
                session_id=session_id,
                worker_job_id=execution_id,
                worker_url=(
                    config.container_worker_base_url
                    if directory == "root"
                    else "http://team.test:11888"
                ),
                target_name=session_id,
                directory_path=directory,
            )
        )
    add_session(config, "manifest", directory_path="root/team")
    monkeypatch.setattr(cli, "_load_runtime_config_from_option", lambda _path: config)
    command = ["execution", "pin-worker-assignments", "--directory-path", "root/team"]
    runner = CliRunner()
    preview = runner.invoke(cli.app, command)
    assert preview.exit_code == 0, preview.output
    preview_payload = json.loads(preview.output)
    assert preview_payload["count"] == 2
    assert not ContainerWorkerAssignmentRepository(tmp_path).path.exists()

    applied = runner.invoke(cli.app, [*command, "--apply"])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output) == {**preview_payload, "applied": True}
    assignments = ContainerWorkerAssignmentRepository(tmp_path)
    assert {item.session_id for item in assignments.list_assignments()} == {"cli-only", "manifest"}
    assignment = assignments.get("cli-only")
    assert assignment.directory_path == "root/team/child"
    assert assignment.worker_url == "http://team.test:11888"
    assert assignment.connection["container_worker_token_env"] == "TEAM_TOKEN"
    assert JsonSessionManifestRepository(config.session_store_path).get_session("cli-only") is None

    # Existing CLI jobs can still construct their backend after a default cutover.
    monkeypatch.setenv("TEAM_TOKEN", "test-token")
    monkeypatch.delenv("NEW_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(
        cli, "_load_runtime_config_from_option", lambda _path: config_for(tmp_path, "new")
    )
    backend = cli._build_execution_backend_for_kind(tmp_path, target="second")
    assert backend.client.base_url == "http://team.test:11888"
    assert backend.client.token_env == "TEAM_TOKEN"


@pytest.mark.parametrize("persist", [False, True])
def test_backfill_rejects_unknown_execution_only_worker_before_any_pins(tmp_path, persist):
    config = config_for(tmp_path)
    add_session(config, "a-valid")
    ContainerWorkerExecutionRepository(tmp_path / "container-worker-executions.db").put(
        ContainerWorkerExecutionRecord(
            execution_id="job",
            session_id="z-cli-only",
            worker_job_id="job",
            worker_url="http://unknown.test:11888",
            target_name="cli-target",
        )
    )
    with pytest.raises(ContainerWorkerAssignmentError, match="original worker configuration"):
        ContainerWorkerAssignmentService(config).pin_existing_sessions(persist=persist)
    assert not ContainerWorkerAssignmentRepository(tmp_path).path.exists()


def test_admin_default_change_pins_execution_only_jobs(tmp_path):
    from evander.admin.directory import DirectoryAdminService

    config = config_for(tmp_path).model_copy(update={"enable_directory_scoped_config": True})
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    ContainerWorkerExecutionRepository(tmp_path / "container-worker-executions.db").put(
        ContainerWorkerExecutionRecord(
            execution_id="job",
            session_id="cli-only",
            worker_job_id="job",
            worker_url=config.container_worker_base_url,
            target_name="cli-target",
        )
    )
    admin = DirectoryAdminService.from_store(directory_store=directories, gateway_config=config)
    result = admin.write_domain_config(
        "root",
        domain="providers",
        payload={
            "container_worker_base_url": "http://new.test:11888",
            "container_worker_token_env": "NEW_WORKER_TOKEN",
        },
    )
    assert result.ok, result.errors
    current = ProviderConfigResolver(config, directory_store=directories).resolve_for_directory(
        "root"
    )
    assert current.container_worker_token_env == "NEW_WORKER_TOKEN"
    assert (
        ContainerWorkerAssignmentService(current).resolve("cli-only").container_worker_token_env
        == "OLD_WORKER_TOKEN"
    )
    assert JsonSessionManifestRepository(config.session_store_path).read_all() == {}
