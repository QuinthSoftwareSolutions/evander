from pathlib import Path
from threading import Event
from types import SimpleNamespace

import httpx

from evander.addons import AddonContext, LoadedAddonCatalog
from evander.config import GatewayConfig
from evander.integration_registry import IntegrationRegistry
from evander_teams_graph_addon import register_addon
from evander_teams_graph_addon.workers import PollingWorker


def test_registration_and_readiness_do_not_resolve_tokens_or_start_workers(tmp_path, monkeypatch):
    import threading
    from evander_teams_graph_addon.auth import TeamsDeviceCodeTokenProvider

    def forbidden(*args, **kwargs):
        raise AssertionError("Registration/readiness must not execute provider operations")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(TeamsDeviceCodeTokenProvider, "get_bearer_token", forbidden)
    monkeypatch.setenv("SYNTHETIC_GRAPH_TOKEN", "not-returned-by-readiness")
    config = GatewayConfig(
        runtime_dir=tmp_path,
        teams_backend="graph_api",
        teams_graph_access_token_env="SYNTHETIC_GRAPH_TOKEN",
    )
    addon = register_addon()
    registry = IntegrationRegistry(LoadedAddonCatalog((addon,), ("test",)))
    context = AddonContext("teams-graph", tmp_path, tmp_path / "addons/teams-graph", config=config)
    assert registry.readiness("teams-graph", context)["status"] == "ready"
    assert {item["id"] for item in registry.describe()[0]["workers"]} == {
        "teams-ingest",
        "teams-notifications",
        "teams-meeting-ingest",
    }
    assert "not-returned-by-readiness" not in str(registry.describe())
    assert list(tmp_path.iterdir()) == []


def test_legacy_imports_retain_provider_and_credential_class_identity():
    from evander.services import GraphTeamsService
    from evander.email.graph_provider import GraphEmailProvider
    from evander.messaging.teams_auth import TeamsAuthCredentials
    from evander_teams_graph_addon.service import GraphTeamsService as NewTeamsService
    from evander_teams_graph_addon.email import GraphEmailProvider as NewEmailProvider
    from evander_teams_graph_addon.auth import TeamsAuthCredentials as NewCredentials

    assert GraphTeamsService is NewTeamsService
    assert GraphEmailProvider is NewEmailProvider
    assert TeamsAuthCredentials is NewCredentials


def test_polling_adapter_uses_existing_lock_and_stops_between_cycles():
    stop = Event()
    calls = []

    def cycle():
        calls.append("locked-cycle")
        stop.set()
        return SimpleNamespace(next_sleep_seconds=300)

    PollingWorker(SimpleNamespace(run_once_with_lock=cycle)).run(stop)
    assert calls == ["locked-cycle"]


def test_distributed_deployment_assets_include_existing_service_names():
    from evander_teams_graph_addon import __file__ as package_file

    root = Path(package_file).parent / "assets"
    for service in ("teams-ingest", "teams-notifications", "teams-meeting-ingest"):
        template = (root / "systemd" / f"evander-{service}.service.template").read_text()
        assert f"/.venv/bin/evander-{service}" in template
