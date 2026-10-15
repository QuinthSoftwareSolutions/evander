"""Restarting a disabled integration exits before constructing provider workers."""

import importlib
from types import SimpleNamespace

import pytest

from evander.addons import AddonLoaderConfig
from config_helpers import write_runtime_config


@pytest.mark.parametrize("worker", ["ingest", "notifications", "meeting_ingest"])
def test_disabled_worker_main_exits_before_building_provider(worker, tmp_path, monkeypatch):
    module = importlib.import_module(f"evander_teams_graph_addon.messaging.{worker}")
    runtime_dir = write_runtime_config(
        tmp_path, addon_loader=AddonLoaderConfig(enabled_addon_ids=[])
    )
    monkeypatch.setattr("sys.argv", [f"evander-teams-{worker}", "--runtime-dir", str(runtime_dir)])

    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "A disabled worker must not construct a provider, start polling, or open a listener"
        )

    monkeypatch.setattr(
        module,
        "build_teams_ingest_worker"
        if worker != "meeting_ingest"
        else "build_teams_meeting_ingest_worker",
        forbidden,
    )
    if worker == "notifications":
        monkeypatch.setattr(module.uvicorn, "run", forbidden)
    module.main()


@pytest.mark.parametrize("worker", ["ingest", "notifications", "meeting_ingest"])
def test_enabled_worker_main_reaches_existing_builder(worker, tmp_path, monkeypatch):
    module = importlib.import_module(f"evander_teams_graph_addon.messaging.{worker}")
    monkeypatch.setattr("sys.argv", [f"evander-teams-{worker}", "--runtime-dir", str(tmp_path)])
    monkeypatch.setattr(
        module,
        "load_runtime_gateway_config",
        lambda **_: SimpleNamespace(
            addon_loader=AddonLoaderConfig(enabled_addon_ids=["teams-graph"]),
        ),
    )

    class BuilderReached(Exception):
        pass

    def reached(*_args, **_kwargs):
        raise BuilderReached

    monkeypatch.setattr(
        module,
        "build_teams_ingest_worker"
        if worker != "meeting_ingest"
        else "build_teams_meeting_ingest_worker",
        reached,
    )
    if worker == "notifications":
        monkeypatch.setattr(
            module,
            "_load_teams_ingest_worker_global_config",
            lambda *_args, **_kwargs: SimpleNamespace(
                change_notification_listener_host="127.0.0.1"
            ),
        )
    with pytest.raises(BuilderReached):
        module.main()
