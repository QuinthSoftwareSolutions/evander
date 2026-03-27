from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.storage import JsonStore
from agent_sandbox.vm_defaults_config import VmDefaultsConfigResolver


def test_vm_defaults_resolver_applies_root_and_child_overlays(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = DirectoryStore(runtime_dir / "directories.json")
    store.ensure_root()
    service = DirectoryService(store)
    config = GatewayConfig.model_validate({"runtime_dir": str(runtime_dir)})
    resolver = VmDefaultsConfigResolver(config, directory_store=store)

    team = service.create(parent="root", name="team")
    config_store = resolver.config_store
    config_store.write(
        "root",
        domain="vm-defaults",
        payload={"gateway_url": "http://10.0.0.1:9000/mcp", "openai_relay_port": 12000},
    )
    config_store.write(
        team.path,
        domain="vm-defaults",
        payload={"nuget_relay_port": 13000},
    )

    effective = resolver.resolve_for_directory(team.path)

    assert effective.gateway_url == "http://10.0.0.1:9000/mcp"
    assert effective.openai_relay_port == 12000
    assert effective.nuget_relay_port == 13000
    assert effective.guest_openai_base_url() == "http://10.0.0.1:12000/v1"
    assert effective.guest_nuget_relay_base_url() == "http://10.0.0.1:13000"


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
    resolver.config_store.write(team.path, domain="vm-defaults", payload={"gateway_url": "http://team-gw:8000/mcp"})

    sessions = JsonStore(config.session_store_path, SessionManifest)
    sessions.put(
        "session-1",
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
