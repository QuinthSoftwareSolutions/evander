from importlib import import_module
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from config_helpers import write_runtime_config
from evander.addons import AddonContext, AddonLoaderConfig, LoadedAddonCatalog
from evander.config import GatewayConfig
from evander.integration_registry import IntegrationRegistry
from evander_azure_devops_addon import register_addon


def test_readiness_registration_and_worker_creation_do_not_make_provider_requests(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Readiness and worker construction must not call the provider")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setenv("TEST_WORK", "never-in-inventory")
    monkeypatch.setenv("TEST_PR", "never-in-inventory")
    config = GatewayConfig(runtime_dir=tmp_path, ado_organization="example", azure_devops_project="Project", azure_devops_pat_env="TEST_WORK", azure_devops_pr_pat_env="TEST_PR")
    addon = register_addon()
    context = AddonContext("azure-devops", tmp_path, tmp_path / "addon", config=config)
    registry = IntegrationRegistry(LoadedAddonCatalog((addon,), ("azure-devops",)))
    assert registry.readiness("azure-devops", context)["status"] == "ready"
    assert "never-in-inventory" not in str(registry.describe())
    assert set(addon.required_restart_services) == {"evander-ado-worker", "evander-proxy-issue-sync"}
    registry.create_worker("azure-devops", "ado-pr-events", context)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["pr_events", "proxy_sync"])
def test_disabling_ado_stops_standalone_workers_before_polling(kind, tmp_path, monkeypatch):
    module = import_module(f"evander_azure_devops_addon.workers.{kind}")
    runtime = write_runtime_config(tmp_path, addon_loader=AddonLoaderConfig(enabled_addon_ids=[]))
    monkeypatch.chdir(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled worker must not poll")
    if kind == "pr_events":
        monkeypatch.setattr(module, "run_worker", forbidden)
        module.run_worker_command()
    else:
        monkeypatch.setattr(module, "run_proxy_issue_sync_worker", forbidden)
        monkeypatch.setattr(module, "run_once_from_runtime", forbidden)
        monkeypatch.setattr("sys.argv", ["evander-proxy-issue-sync", "--runtime-dir", str(runtime)])
        module.main()


@pytest.mark.parametrize("kind", ["pr_events", "proxy_sync"])
def test_host_supervisor_can_cancel_before_worker_start(kind, monkeypatch):
    module = import_module(f"evander_azure_devops_addon.workers.{kind}")
    def forbidden(*args, **kwargs):
        pytest.fail("Cancelled worker must not load provider configuration")
    monkeypatch.setattr(module, "load_runtime_gateway_config", forbidden)
    stop = Event()
    stop.set()
    if kind == "pr_events":
        module.run_worker(SimpleNamespace(), stop)
    else:
        module.run_proxy_issue_sync_worker(runtime_dir="unused", stop=stop)
