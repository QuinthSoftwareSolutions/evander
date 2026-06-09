from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.routing import Route

from agent_sandbox.addons import (
    AddonContext,
    AddonLoaderConfig,
    AddonRegistration,
    addon_json_object_store,
    addon_runtime_dir,
    addon_storage_path,
    load_addons,
    resolve_addon_admin_api_routes,
)
from agent_sandbox.workflow.plugins import load_provider_plugins


def test_load_addons_defaults_to_empty_catalog() -> None:
    catalog = load_addons()

    assert catalog.registrations == ()
    assert catalog.loaded_addons == ()


def test_load_addons_supports_explicit_module_list(tmp_path: Path, monkeypatch) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "custom_addon.py").write_text(
        """
def register_addon():
    return {
        "addon_id": "custom-addon",
        "display_name": "Custom Add-on",
        "admin_navigation": {
            "label": "Custom",
            "short_label": "Custom",
            "description": "Custom add-on view.",
        },
    }
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("custom_addon", None)

    catalog = load_addons(AddonLoaderConfig(modules=["custom_addon"]))

    assert catalog.loaded_addons == ("custom_addon",)
    assert catalog.registrations[0].addon_id == "custom-addon"
    assert catalog.registrations[0].admin_navigation is not None


def test_load_addons_supports_entry_points(monkeypatch) -> None:
    class _EntryPointStub:
        name = "custom"
        value = "custom.entry"

        @staticmethod
        def load():
            return AddonRegistration(
                addon_id="entry-addon",
                display_name="Entry Add-on",
            )

    monkeypatch.setattr(
        "agent_sandbox.addons._iter_addon_entry_points", lambda: (_EntryPointStub(),)
    )

    catalog = load_addons(AddonLoaderConfig(enabled_entry_points=True))

    assert catalog.loaded_addons == ("entry_point:custom.entry",)
    assert catalog.registrations[0].addon_id == "entry-addon"


def test_load_addons_rejects_duplicate_ids(tmp_path: Path, monkeypatch) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    for module_name in ("addon_one", "addon_two"):
        (module_root / f"{module_name}.py").write_text(
            """
def register_addon():
    return {"addon_id": "duplicate-addon", "display_name": "Duplicate"}
""",
            encoding="utf-8",
        )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()

    with pytest.raises(ValueError, match="Duplicate add-on id"):
        load_addons(AddonLoaderConfig(modules=["addon_one", "addon_two"]))


def test_addon_storage_paths_are_scoped_to_addon_runtime_dir(tmp_path: Path) -> None:
    root = addon_runtime_dir(tmp_path, "team-addon")

    assert root == tmp_path / "addons" / "team-addon"
    assert addon_storage_path(tmp_path, "team-addon", "state.json") == root / "state.json"
    assert addon_storage_path(tmp_path, "team-addon", "nested", "state.json") == (
        root / "nested" / "state.json"
    )
    with pytest.raises(ValueError):
        addon_storage_path(tmp_path, "team-addon", "../state.json")
    with pytest.raises(ValueError):
        addon_storage_path(tmp_path, "TeamAddon", "state.json")


def test_addon_json_object_store_uses_locked_json_store(tmp_path: Path) -> None:
    store = addon_json_object_store(tmp_path, "team-addon", "state.json")

    store.update_object(lambda payload: payload.update({"task": {"state": "pending"}}))

    assert store.read_object() == {"task": {"state": "pending"}}
    assert (tmp_path / "addons" / "team-addon" / "state.json").is_file()


def test_resolve_addon_routes_accepts_relative_starlette_routes(tmp_path: Path) -> None:
    async def ping(_request):
        raise AssertionError("route handler should not be called")

    registration = AddonRegistration(
        addon_id="route-addon",
        display_name="Route Add-on",
        admin_api_routes=[Route("/ping", ping, methods=["GET"])],
    )
    context = AddonContext(
        addon_id="route-addon",
        runtime_dir=tmp_path,
        addon_runtime_dir=addon_runtime_dir(tmp_path, "route-addon"),
    )

    routes = resolve_addon_admin_api_routes(registration, context)

    assert len(routes) == 1
    assert routes[0].path == "/ping"


def test_resolve_addon_routes_rejects_invalid_paths(tmp_path: Path) -> None:
    registration = AddonRegistration(
        addon_id="route-addon",
        display_name="Route Add-on",
        admin_api_routes=[SimpleNamespace(path="../bad")],
    )
    context = AddonContext(
        addon_id="route-addon",
        runtime_dir=tmp_path,
        addon_runtime_dir=addon_runtime_dir(tmp_path, "route-addon"),
    )

    with pytest.raises(ValueError, match="start with /"):
        resolve_addon_admin_api_routes(registration, context)


def test_workflow_provider_loader_can_include_addon_provider(tmp_path: Path, monkeypatch) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "provider_addon.py").write_text(
        """
from agent_sandbox.triggers.providers.base import TriggerProviderBase

class AddonTriggerProvider(TriggerProviderBase):
    pass

def register_addon():
    return {
        "addon_id": "provider-addon",
        "display_name": "Provider Add-on",
        "workflow_providers": {
            "trigger_providers": {
                "trigger:addon:test": AddonTriggerProvider,
            },
        },
    }
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("provider_addon", None)

    catalog = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        include_builtin=False,
        addon_loader=AddonLoaderConfig(modules=["provider_addon"]),
    )

    assert "trigger:addon:test" in catalog.trigger_providers
    assert "addon:provider-addon" in catalog.loaded_plugins
