from pathlib import Path

import pytest

from evander.config import GatewayConfig
from evander.directories import DirectoryService, DirectoryStore
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.vm.defaults_config import VmDefaultsConfigResolver, render_vm_defaults_exports


def test_vm_defaults_resolver_applies_root_and_child_overlays(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    store.ensure_root()
    service = DirectoryService(store)
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(runtime_dir), "http_target_relay_port": 14000}
    )
    resolver = VmDefaultsConfigResolver(config, directory_store=store)

    team = service.create(parent="root", name="team")
    config_store = resolver.config_store
    config_store.write(
        "root",
        domain="vm-defaults",
        payload={
            "gateway_url": "http://10.0.0.1:9000/mcp",
            "openai_relay_port": 12000,
            "libvirt_vm_default_template": " default ",
            "libvirt_vm_templates": {
                " default ": " ubuntu-24.04-server-cloudimg-amd64.img ",
                "ubuntu-noble": " noble-root.qcow2 ",
            },
        },
    )
    config_store.write(
        team.path,
        domain="vm-defaults",
        payload={
            "nuget_relay_port": 13000,
            "libvirt_vm_templates": {
                "ubuntu-jammy": " jammy-team.qcow2 ",
                "ubuntu-noble": " noble-team.qcow2 ",
            },
        },
    )

    effective = resolver.resolve_for_directory(team.path)

    assert effective.gateway_url == "http://10.0.0.1:9000/mcp"
    assert effective.openai_relay_port == 12000
    assert effective.http_target_relay_port == 14000
    assert effective.nuget_relay_port == 13000
    assert effective.libvirt_vm_default_template == "default"
    assert effective.libvirt_vm_templates == {
        "default": "ubuntu-24.04-server-cloudimg-amd64.img",
        "ubuntu-jammy": "jammy-team.qcow2",
        "ubuntu-noble": "noble-team.qcow2",
    }
    assert effective.guest_http_target_relay_base_url() == "http://10.0.0.1:14000"
    assert effective.guest_openai_base_url() == "http://10.0.0.1:12000/v1"
    assert effective.guest_nuget_relay_base_url() == "http://10.0.0.1:13000"


def test_vm_defaults_resolver_propagates_npm_audit_facade_bind_settings(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    store.ensure_root()
    service = DirectoryService(store)
    config = GatewayConfig.model_validate({"runtime_dir": str(runtime_dir)})
    resolver = VmDefaultsConfigResolver(config, directory_store=store)

    team = service.create(parent="root", name="team")
    resolver.config_store.write(
        team.path,
        domain="vm-defaults",
        payload={
            "npm_audit_facade_host": "127.0.0.1",
            "npm_audit_facade_port": 18181,
        },
    )

    effective = resolver.resolve_for_directory(team.path)

    assert effective.npm_audit_facade_host == "127.0.0.1"
    assert effective.npm_audit_facade_port == 18181


def test_vm_defaults_resolver_uses_root_for_empty_directory_and_rejects_missing_paths(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    store.ensure_root()
    config = GatewayConfig.model_validate({"runtime_dir": str(runtime_dir)})
    resolver = VmDefaultsConfigResolver(config, directory_store=store)
    resolver.config_store.write(
        "root",
        domain="vm-defaults",
        payload={"gateway_url": "http://root-gw:9000/mcp"},
    )

    assert resolver.resolve_for_directory(None).gateway_url == "http://root-gw:9000/mcp"
    assert resolver.resolve_for_directory("").gateway_url == "http://root-gw:9000/mcp"

    with pytest.raises(
        ValueError,
        match=r"directory path segment 'missing' does not exist under 'root'",
    ):
        resolver.resolve_for_directory("root/missing")


def test_vm_defaults_resolver_uses_session_directory_path(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(runtime_dir),
            "session_store_path": str(runtime_dir / "sessions.json"),
            "directories_store_path": str(runtime_dir / "directories.json"),
        }
    )
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    service = DirectoryService(directory_store)
    team = service.create(parent="root", name="team")
    resolver = VmDefaultsConfigResolver(config, directory_store=directory_store)
    resolver.config_store.write(
        team.path, domain="vm-defaults", payload={"gateway_url": "http://team-gw:8000/mcp"}
    )

    sessions = JsonSessionManifestRepository(config.session_store_path)
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path=team.path,
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
        ),
    )

    resolved = VmDefaultsConfigResolver(
        config,
        directory_store=directory_store,
        session_store=sessions,
    ).resolve_for_session_id("session-1")

    assert resolved.gateway_url == "http://team-gw:8000/mcp"


def test_vm_defaults_resolver_reports_stale_session_directory_and_render_falls_back(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(runtime_dir),
            "session_store_path": str(runtime_dir / "sessions.json"),
            "directories_store_path": str(runtime_dir / "directories.json"),
        }
    )
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    service = DirectoryService(directory_store)
    team = service.create(parent="root", name="team")
    resolver = VmDefaultsConfigResolver(config, directory_store=directory_store)
    resolver.config_store.write(
        "root",
        domain="vm-defaults",
        payload={"gateway_url": "http://root-gw:9000/mcp"},
    )
    resolver.config_store.write(
        team.path, domain="vm-defaults", payload={"gateway_url": "http://team-gw:8000/mcp"}
    )

    sessions = JsonSessionManifestRepository(config.session_store_path)
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path=team.path,
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
        ),
    )

    assert resolver.resolve_for_session_id("session-1").gateway_url == "http://team-gw:8000/mcp"

    directory_store.delete(team.id)

    with pytest.raises(
        ValueError,
        match=(
            r"vm defaults config session 'session-1' references missing directory_path "
            r"'root/team': directory path segment 'team' does not exist under 'root'"
        ),
    ):
        resolver.resolve_for_session_id("session-1")

    exports = render_vm_defaults_exports(
        runtime_dir=runtime_dir,
        session_id="session-1",
        fallback_directory="root",
    )

    assert "export GATEWAY_URL=http://root-gw:9000/mcp" in exports
    assert "export HTTP_TARGET_RELAY_BASE_URL=http://root-gw:11777" in exports
    assert "export LLM_BASE_URL=http://root-gw:11434/v1" in exports
    assert "export NUGET_RELAY_BASE_URL=http://root-gw:11666" in exports

    with pytest.raises(
        ValueError,
        match=r"directory path segment 'missing' does not exist under 'root'",
    ):
        render_vm_defaults_exports(
            runtime_dir=runtime_dir,
            session_id="session-1",
            fallback_directory="root/missing",
        )


def test_render_vm_defaults_exports_uses_session_scope_and_shell_exports(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(runtime_dir),
            "session_store_path": str(runtime_dir / "sessions.json"),
            "directories_store_path": str(runtime_dir / "directories.json"),
        }
    )
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    service = DirectoryService(directory_store)
    team = service.create(parent="root", name="team")
    resolver = VmDefaultsConfigResolver(config, directory_store=directory_store)
    resolver.config_store.write(
        team.path,
        domain="vm-defaults",
        payload={
            "gateway_url": "http://10.0.0.8:9000/mcp",
            "openai_relay_port": 12000,
            "nuget_relay_port": 13000,
        },
    )

    sessions = JsonSessionManifestRepository(config.session_store_path)
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path=team.path,
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
        ),
    )

    exports = render_vm_defaults_exports(runtime_dir=runtime_dir, session_id="session-1")

    assert "export GATEWAY_URL=http://10.0.0.8:9000/mcp" in exports
    assert "export HTTP_TARGET_RELAY_BASE_URL=http://10.0.0.8:11777" in exports
    assert "export LLM_BASE_URL=http://10.0.0.8:12000/v1" in exports
    assert "export NUGET_RELAY_BASE_URL=http://10.0.0.8:13000" in exports


def test_render_vm_defaults_exports_rejects_malicious_environment_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadVmDefaults:
        def guest_environment(self) -> dict[str, str]:
            return {"BAD; touch /tmp/vm-defaults-pwned #": "1"}

    class BadResolver:
        def __init__(self, config: GatewayConfig) -> None:
            self.config = config

        def resolve_for_directory(self, directory_path: str | None) -> BadVmDefaults:
            return BadVmDefaults()

        def resolve_for_session_id(self, session_id: str) -> BadVmDefaults:
            return BadVmDefaults()

    monkeypatch.setattr("evander.vm.defaults_config.VmDefaultsConfigResolver", BadResolver)

    with pytest.raises(ValueError, match="Invalid environment variable name"):
        render_vm_defaults_exports(runtime_dir=tmp_path / "runtime")


def test_vm_defaults_resolver_supports_provider_and_azure_overlay(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    store.ensure_root()
    service = DirectoryService(store)
    config = GatewayConfig.model_validate({"runtime_dir": str(runtime_dir)})
    resolver = VmDefaultsConfigResolver(config, directory_store=store)
    team = service.create(parent="root", name="team")

    resolver.config_store.write(
        team.path,
        domain="vm",
        payload={
            "provider": "azure",
            "provider_defaults": {"ssh_port": "22"},
            "azure": {
                "subscription": "sub-123",
                "resource_group": "rg-agents",
                "region": "westeurope",
                "virtual_network": "vnet-agents",
                "subnet": "snet-private",
            },
        },
    )

    resolved = resolver.resolve_for_directory(team.path)

    assert resolved.provider == "azure"
    assert resolved.provider_defaults == {"ssh_port": "22"}
    assert resolved.azure.subscription == "sub-123"
    assert resolved.azure.resource_group == "rg-agents"
