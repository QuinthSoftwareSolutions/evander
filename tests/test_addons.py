from __future__ import annotations

import importlib
import sys
import tomllib
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
    register_addon_mcp_tools,
    resolve_addon_admin_api_routes,
)
from agent_sandbox.workflow.plugins import load_provider_plugins

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_load_addons_defaults_to_empty_catalog() -> None:
    catalog = load_addons()

    assert catalog.registrations == ()
    assert catalog.loaded_addons == ()


def test_bundled_team_addon_packaging_contract_is_declared() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    addon_entry_points = pyproject["project"]["entry-points"]["agent_sandbox.addons"]
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert addon_entry_points["team"] == "truman_team_addon:register_addon"
    assert package_data["truman_team_addon"] == ["ui_static/*"]
    assert (PROJECT_ROOT / "src/truman_team_addon/ui_static/index.html").is_file()
    assert (PROJECT_ROOT / "src/truman_team_addon/ui_static/team-ui.js").is_file()
    assert (PROJECT_ROOT / "src/truman_team_addon/ui_static/team-ui.css").is_file()


def test_bundled_bokio_addon_packaging_contract_is_declared() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    addon_entry_points = pyproject["project"]["entry-points"]["agent_sandbox.addons"]
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert addon_entry_points["bokio"] == "evander_bokio_addon:register_addon"
    assert package_data["evander_bokio_addon"] == ["ui_static/*"]
    assert (PROJECT_ROOT / "src/evander_bokio_addon/ui_static/index.html").is_file()
    assert (PROJECT_ROOT / "src/evander_bokio_addon/ui_static/bokio-ui.js").is_file()
    assert (PROJECT_ROOT / "src/evander_bokio_addon/ui_static/bokio-ui.css").is_file()


def test_addon_mcp_tool_registrar_returns_registered_names(tmp_path: Path) -> None:
    calls: list[tuple[object, str]] = []

    def registrar(server, context):
        calls.append((server, context.addon_id))
        return ["custom_tool", "custom_tool"]

    registration = AddonRegistration(
        addon_id="custom-addon",
        display_name="Custom Add-on",
        mcp_tools=registrar,
    )
    server = object()
    context = AddonContext(
        addon_id="custom-addon",
        runtime_dir=tmp_path,
        addon_runtime_dir=tmp_path / "addons" / "custom-addon",
    )

    names = register_addon_mcp_tools(registration, server, context)

    assert names == ("custom_tool",)
    assert calls == [(server, "custom-addon")]


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


def test_bundled_team_addon_workflow_providers_are_optional() -> None:
    disabled = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        include_builtin=False,
    )
    assert "effect:team:wake-due-agents" not in disabled.effect_providers
    assert "effect:team:record-git-promotion" not in disabled.effect_providers
    assert "effect:team:mark-release-published" not in disabled.effect_providers
    assert "effect:team:consolidate-git-branches" not in disabled.effect_providers
    assert "query:team:state" not in disabled.query_providers
    assert "effect:team:wake-due-agents" not in disabled.node_definition_resolvers
    assert not any(
        entry.type.startswith(("effect:team:", "query:team:"))
        for entry in disabled.workflow_editor_catalog_entries
    )

    enabled = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        include_builtin=False,
        addon_loader=AddonLoaderConfig(
            modules=["truman_team_addon"],
            enabled_addon_ids=["team-addon"],
        ),
    )

    assert "addon:team-addon" in enabled.loaded_plugins
    assert "effect:team:wake-due-agents" in enabled.effect_providers
    assert "effect:team:apply-agent-action" in enabled.effect_providers
    assert "effect:team:mark-ephemeral-cleaned-up" in enabled.effect_providers
    assert "effect:team:record-git-promotion" in enabled.effect_providers
    assert "effect:team:mark-release-published" in enabled.effect_providers
    assert "effect:team:consolidate-git-branches" in enabled.effect_providers
    assert "effect:team:record-git-promotion" in enabled.node_definition_resolvers
    assert "query:team:agent-action-artifact" in enabled.query_providers
    assert "query:team:session-routing" in enabled.query_providers
    assert "effect:team:wake-due-agents" in enabled.node_definition_resolvers
    catalog_entries = {entry.type: entry for entry in enabled.workflow_editor_catalog_entries}
    assert "effect:team:wake-due-agents" in catalog_entries
    assert "effect:team:apply-agent-action" in catalog_entries
    assert "query:team:state" in catalog_entries
    assert catalog_entries["effect:team:wake-due-agents"].label == "Wake Team agents"
    assert catalog_entries["effect:team:wake-due-agents"].description
    assert all(
        field.description
        for entry in catalog_entries.values()
        for field in entry.fields
    )


def test_bundled_bokio_addon_workflow_providers_are_optional() -> None:
    disabled = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        include_builtin=False,
    )
    assert "query:bokio:company" not in disabled.query_providers
    assert "effect:bokio:prepare-journal-entry" not in disabled.effect_providers

    enabled = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        include_builtin=False,
        addon_loader=AddonLoaderConfig(
            modules=["evander_bokio_addon"],
            enabled_addon_ids=["bokio-addon"],
        ),
    )

    assert "addon:bokio-addon" in enabled.loaded_plugins
    assert "query:bokio:company" in enabled.query_providers
    assert "query:bokio:chart-of-accounts" in enabled.query_providers
    assert "effect:bokio:prepare-journal-entry" in enabled.effect_providers
    assert "effect:bokio:create-bank-payment" in enabled.effect_providers
    catalog_entries = {entry.type: entry for entry in enabled.workflow_editor_catalog_entries}
    assert catalog_entries["query:bokio:company"].label == "Bokio company"
    assert catalog_entries["effect:bokio:create-bank-payment"].description
