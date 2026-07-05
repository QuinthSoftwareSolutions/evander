from __future__ import annotations

import importlib
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.routing import Route

from evander.addons import (
    AddonContext,
    AddonLoaderConfig,
    AddonRegistration,
    addon_json_object_store,
    addon_runtime_dir,
    addon_storage_path,
    discover_addons,
    load_addons,
    register_addon_mcp_tools,
    resolve_addon_admin_api_routes,
)
from evander.workflow.plugins import load_provider_plugins

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_ADDON_MANIFESTS = {
    "team-addon": "evander_team_addon",
    "bokio-addon": "evander_bokio_addon",
    "skatteverket-addon": "evander_skatteverket_addon",
    "mssql-seed-addon": "evander_mssql_seed_addon",
}


def test_load_addons_defaults_to_empty_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    catalog = load_addons()

    assert catalog.registrations == ()
    assert catalog.loaded_addons == ()


def test_bundled_team_addon_packaging_contract_is_declared() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    addon_entry_points = pyproject["project"]["entry-points"]["evander.addons"]
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert addon_entry_points["team"] == "evander_team_addon:register_addon"
    assert package_data["evander_team_addon"] == ["addon.json", "ui_static/*"]
    assert json.loads(
        (PROJECT_ROOT / "src/evander_team_addon/addon.json").read_text(encoding="utf-8")
    ) == {"addon_id": "team-addon", "module": "evander_team_addon"}
    assert (PROJECT_ROOT / "src/evander_team_addon/ui_static/index.html").is_file()
    assert (PROJECT_ROOT / "src/evander_team_addon/ui_static/team-ui.js").is_file()
    assert (PROJECT_ROOT / "src/evander_team_addon/ui_static/team-ui.css").is_file()
    assert (PROJECT_ROOT / "src/evander_team_addon/ui_static/team-org-flow.js").is_file()
    assert (PROJECT_ROOT / "src/evander_team_addon/ui_static/team-org-flow.css").is_file()
    assert (PROJECT_ROOT / "src/evander_team_addon/ui_static/team-agent-create.js").is_file()
    assert (PROJECT_ROOT / "src/evander_team_addon/ui_static/team-agent-create.css").is_file()

    addon = importlib.import_module("evander_team_addon").register_addon()
    assert addon.mcp_tools is not None


def test_bundled_bokio_addon_packaging_contract_is_declared() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    addon_entry_points = pyproject["project"]["entry-points"]["evander.addons"]
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert addon_entry_points["bokio"] == "evander_bokio_addon:register_addon"
    assert package_data["evander_bokio_addon"] == ["addon.json", "ui_static/*"]
    assert json.loads(
        (PROJECT_ROOT / "src/evander_bokio_addon/addon.json").read_text(encoding="utf-8")
    ) == {"addon_id": "bokio-addon", "module": "evander_bokio_addon"}
    assert (PROJECT_ROOT / "src/evander_bokio_addon/ui_static/index.html").is_file()
    assert (PROJECT_ROOT / "src/evander_bokio_addon/ui_static/bokio-ui.js").is_file()
    assert (PROJECT_ROOT / "src/evander_bokio_addon/ui_static/bokio-ui.css").is_file()


def test_bundled_addon_manifests_are_scannable_from_src() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    records = discover_addons(AddonLoaderConfig(package_dirs=["src"]), base_dir=PROJECT_ROOT)
    discovered = {
        record.registration.addon_id: record
        for record in records
        if record.registration is not None
    }

    assert set(discovered) == set(BUNDLED_ADDON_MANIFESTS)
    for addon_id, module_name in BUNDLED_ADDON_MANIFESTS.items():
        manifest_path = PROJECT_ROOT / "src" / module_name / "addon.json"
        assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
            "addon_id": addon_id,
            "module": module_name,
        }
        assert package_data[module_name] == ["addon.json", "ui_static/*"]
        assert discovered[addon_id].source == f"package:src/{module_name}"
        assert discovered[addon_id].error is None

    catalog = load_addons(
        AddonLoaderConfig(
            package_dirs=["src"],
            enabled_addon_ids=list(BUNDLED_ADDON_MANIFESTS),
        ),
        base_dir=PROJECT_ROOT,
    )

    assert {registration.addon_id for registration in catalog.registrations} == set(
        BUNDLED_ADDON_MANIFESTS
    )


def test_discover_addons_preserves_imported_bundled_package_alias(monkeypatch) -> None:
    public_module = importlib.import_module("evander_mssql_seed_addon")
    public_mcp_module = importlib.import_module("evander_mssql_seed_addon.mcp")

    def patched_register_addon() -> AddonRegistration:
        return AddonRegistration(
            addon_id="mssql-seed-addon",
            display_name="Patched MSSQL Seed Add-on",
        )

    monkeypatch.setattr(public_module, "register_addon", patched_register_addon)

    records = discover_addons(AddonLoaderConfig(package_dirs=["src"]), base_dir=PROJECT_ROOT)
    discovered = {
        record.registration.addon_id: record
        for record in records
        if record.registration is not None
    }

    assert discovered["mssql-seed-addon"].registration.display_name == (
        "Patched MSSQL Seed Add-on"
    )
    assert sys.modules["evander_mssql_seed_addon"] is public_module
    assert sys.modules["evander_mssql_seed_addon.mcp"] is public_mcp_module


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


def test_discover_addons_scans_package_manifests_from_base_dir(tmp_path: Path) -> None:
    package_dir = tmp_path / "addons" / "package-addon"
    package_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text(
        json.dumps({"addon_id": "package-addon", "module": "package_addon"}),
        encoding="utf-8",
    )
    (package_dir / "package_addon.py").write_text(
        """
def register_addon():
    return {"addon_id": "package-addon", "display_name": "Package Add-on"}
""",
        encoding="utf-8",
    )
    sys.modules.pop("package_addon", None)

    records = discover_addons(AddonLoaderConfig(), base_dir=tmp_path)

    assert len(records) == 1
    assert records[0].source == "package:addons/package-addon"
    assert records[0].source_type == "package"
    assert records[0].registration is not None
    assert records[0].registration.addon_id == "package-addon"
    assert records[0].error is None


def test_load_addons_does_not_enable_scanned_packages_by_default(tmp_path: Path) -> None:
    package_dir = tmp_path / "addons" / "package-addon"
    package_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text(
        json.dumps({"addon_id": "package-addon", "module": "package_addon"}),
        encoding="utf-8",
    )
    (package_dir / "package_addon.py").write_text(
        """
def register_addon():
    return {"addon_id": "package-addon", "display_name": "Package Add-on"}
""",
        encoding="utf-8",
    )
    sys.modules.pop("package_addon", None)

    catalog = load_addons(AddonLoaderConfig(), base_dir=tmp_path)

    assert catalog.registrations == ()
    assert catalog.loaded_addons == ()


def test_load_addons_can_enable_scanned_package_by_id(tmp_path: Path) -> None:
    package_dir = tmp_path / "addons" / "package-addon"
    package_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text(
        json.dumps({"addon_id": "package-addon", "module": "package_addon"}),
        encoding="utf-8",
    )
    (package_dir / "package_addon.py").write_text(
        """
def register_addon():
    return {"addon_id": "package-addon", "display_name": "Package Add-on"}
""",
        encoding="utf-8",
    )
    sys.modules.pop("package_addon", None)

    catalog = load_addons(
        AddonLoaderConfig(enabled_addon_ids=["package-addon"]),
        base_dir=tmp_path,
    )

    assert catalog.loaded_addons == ("package:addons/package-addon",)
    assert catalog.registrations[0].addon_id == "package-addon"


def test_package_addons_with_same_module_name_are_imported_from_each_package(
    tmp_path: Path,
) -> None:
    for package_name, display_name in (
        ("alpha-addon", "Alpha Add-on"),
        ("bravo-addon", "Bravo Add-on"),
    ):
        package_dir = tmp_path / "addons" / package_name
        package_dir.mkdir(parents=True)
        (package_dir / "addon.json").write_text(
            json.dumps({"addon_id": package_name, "module": "addon"}),
            encoding="utf-8",
        )
        (package_dir / "addon.py").write_text(
            f"""
def register_addon():
    return {{"addon_id": {package_name!r}, "display_name": {display_name!r}}}
""",
            encoding="utf-8",
        )
    sys.modules.pop("addon", None)

    records = discover_addons(AddonLoaderConfig(), base_dir=tmp_path)
    catalog = load_addons(
        AddonLoaderConfig(enabled_addon_ids=["alpha-addon", "bravo-addon"]),
        base_dir=tmp_path,
    )

    assert [record.registration.addon_id for record in records if record.registration] == [
        "alpha-addon",
        "bravo-addon",
    ]
    assert [registration.display_name for registration in catalog.registrations] == [
        "Alpha Add-on",
        "Bravo Add-on",
    ]


def test_package_addon_supports_absolute_imports_within_declared_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_dir = tmp_path / "addons" / "absolute-addon"
    module_dir = package_dir / "my_addon"
    module_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text(
        json.dumps({"addon_id": "absolute-addon", "module": "my_addon"}),
        encoding="utf-8",
    )
    (module_dir / "__init__.py").write_text(
        """
from my_addon.api import DISPLAY_NAME


def register_addon():
    def lazy_value():
        from my_addon.helper import HELPER_VALUE
        return HELPER_VALUE

    return {
        "addon_id": "absolute-addon",
        "display_name": DISPLAY_NAME,
        "metadata": {"lazy_value": lazy_value},
    }
""",
        encoding="utf-8",
    )
    (module_dir / "api.py").write_text(
        'DISPLAY_NAME = "Absolute Add-on"\n',
        encoding="utf-8",
    )
    (module_dir / "helper.py").write_text(
        'HELPER_VALUE = "Lazy Absolute Add-on"\n',
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "my_addon", raising=False)
    monkeypatch.delitem(sys.modules, "my_addon.helper", raising=False)
    stale_api = type(sys)("my_addon.api")
    stale_api.DISPLAY_NAME = "Stale Add-on"
    monkeypatch.setitem(sys.modules, "my_addon.api", stale_api)

    records = discover_addons(AddonLoaderConfig(), base_dir=tmp_path)

    assert len(records) == 1
    assert records[0].registration is not None
    assert records[0].registration.addon_id == "absolute-addon"
    assert records[0].registration.display_name == "Absolute Add-on"
    assert records[0].error is None
    assert records[0].registration.metadata["lazy_value"]() == "Lazy Absolute Add-on"
    assert "my_addon" in sys.modules
    assert sys.modules["my_addon.api"] is not stale_api
    assert sys.modules["my_addon.api"].DISPLAY_NAME == "Absolute Add-on"
    assert sys.modules["my_addon.helper"].HELPER_VALUE == "Lazy Absolute Add-on"


def test_package_addon_can_declare_package_directory_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_dir = tmp_path / "addons" / "source_style_addon"
    package_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text(
        json.dumps({"addon_id": "source-style-addon", "module": "source_style_addon"}),
        encoding="utf-8",
    )
    (package_dir / "__init__.py").write_text(
        """
from source_style_addon.api import DISPLAY_NAME


def register_addon():
    return {"addon_id": "source-style-addon", "display_name": DISPLAY_NAME}
""",
        encoding="utf-8",
    )
    (package_dir / "api.py").write_text(
        'DISPLAY_NAME = "Source Style Add-on"\n',
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, "source_style_addon", raising=False)
    monkeypatch.delitem(sys.modules, "source_style_addon.api", raising=False)

    records = discover_addons(AddonLoaderConfig(), base_dir=tmp_path)
    catalog = load_addons(
        AddonLoaderConfig(enabled_addon_ids=["source-style-addon"]),
        base_dir=tmp_path,
    )

    assert len(records) == 1
    assert records[0].registration is not None
    assert records[0].registration.addon_id == "source-style-addon"
    assert records[0].registration.display_name == "Source Style Add-on"
    assert records[0].error is None
    assert catalog.loaded_addons == ("package:addons/source_style_addon",)
    assert catalog.registrations[0].addon_id == "source-style-addon"


def test_discover_addons_ignores_directories_without_package_manifests(tmp_path: Path) -> None:
    (tmp_path / "addons" / "not-an-addon").mkdir(parents=True)

    records = discover_addons(AddonLoaderConfig(), base_dir=tmp_path)

    assert records == ()


def test_discover_addons_reports_invalid_package_manifests(tmp_path: Path) -> None:
    package_dir = tmp_path / "addons" / "broken-addon"
    package_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text("{", encoding="utf-8")

    records = discover_addons(AddonLoaderConfig(), base_dir=tmp_path)

    assert len(records) == 1
    assert records[0].source == "package:addons/broken-addon"
    assert records[0].source_type == "package"
    assert records[0].registration is None
    assert records[0].error is not None
    assert "Invalid add-on package manifest" in records[0].error


def test_explicit_module_takes_precedence_over_scanned_package_duplicate(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "explicit_addon.py").write_text(
        """
def register_addon():
    return {"addon_id": "duplicate-addon", "display_name": "Explicit Add-on"}
""",
        encoding="utf-8",
    )
    package_dir = tmp_path / "addons" / "duplicate-addon"
    package_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text(
        json.dumps({"addon_id": "duplicate-addon", "module": "package_addon"}),
        encoding="utf-8",
    )
    (package_dir / "package_addon.py").write_text(
        """
def register_addon():
    return {"addon_id": "duplicate-addon", "display_name": "Package Add-on"}
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("explicit_addon", None)
    sys.modules.pop("package_addon", None)

    records = discover_addons(
        AddonLoaderConfig(modules=["explicit_addon"]),
        base_dir=tmp_path,
    )
    catalog = load_addons(
        AddonLoaderConfig(
            modules=["explicit_addon"],
            enabled_addon_ids=["duplicate-addon"],
        ),
        base_dir=tmp_path,
    )

    assert len(records) == 1
    assert records[0].source == "explicit_addon"
    assert records[0].error is None
    assert catalog.loaded_addons == ("explicit_addon",)
    assert catalog.registrations[0].display_name == "Explicit Add-on"


def test_discover_addons_reports_duplicate_scanned_package_ids(tmp_path: Path) -> None:
    for package_name, display_name in (
        ("alpha-addon", "Alpha Add-on"),
        ("bravo-addon", "Bravo Add-on"),
    ):
        package_dir = tmp_path / "addons" / package_name
        package_dir.mkdir(parents=True)
        (package_dir / "addon.json").write_text(
            json.dumps({"addon_id": "duplicate-addon", "module": "package_addon"}),
            encoding="utf-8",
        )
        (package_dir / "package_addon.py").write_text(
            f"""
def register_addon():
    return {{"addon_id": "duplicate-addon", "display_name": {display_name!r}}}
""",
            encoding="utf-8",
        )

    records = discover_addons(AddonLoaderConfig(), base_dir=tmp_path)

    assert len(records) == 2
    assert records[0].source == "package:addons/alpha-addon"
    assert records[0].error is None
    assert records[1].source == "package:addons/bravo-addon"
    assert records[1].error is not None
    assert "already discovered from package:addons/alpha-addon" in records[1].error


def test_discover_addons_reports_candidates_without_enabled_filter(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "custom_addon.py").write_text(
        """
def register_addon():
    return {"addon_id": "custom-addon", "display_name": "Custom Add-on"}
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("custom_addon", None)

    records = discover_addons(
        AddonLoaderConfig(
            modules=["custom_addon"],
            package_dirs=[],
            enabled_addon_ids=[],
        )
    )

    assert len(records) == 1
    assert records[0].source == "custom_addon"
    assert records[0].source_type == "module"
    assert records[0].registration is not None
    assert records[0].registration.addon_id == "custom-addon"
    assert records[0].error is None


def test_discover_addons_reports_load_errors() -> None:
    records = discover_addons(
        AddonLoaderConfig(modules=["missing_addon_module"], package_dirs=[])
    )

    assert len(records) == 1
    assert records[0].registration is None
    assert records[0].error is not None
    assert "missing_addon_module" in records[0].error


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
        "evander.addons._iter_addon_entry_points", lambda: (_EntryPointStub(),)
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
from evander.triggers.providers.base import TriggerProviderBase

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
            modules=["evander_team_addon"],
            enabled_addon_ids=["team-addon"],
        ),
    )

    assert "addon:team-addon" in enabled.loaded_plugins
    assert "effect:team:wake-due-agents" in enabled.effect_providers
    assert "effect:team:apply-agent-action" not in enabled.effect_providers
    assert "effect:team:apply-ephemeral-action" not in enabled.effect_providers
    assert "effect:team:finalize-agent-dispatch" in enabled.effect_providers
    assert "effect:team:create-case" in enabled.effect_providers
    assert "effect:team:update-case" in enabled.effect_providers
    assert "effect:team:mark-ephemeral-cleaned-up" in enabled.effect_providers
    assert "effect:team:record-git-promotion" in enabled.effect_providers
    assert "effect:team:mark-release-published" in enabled.effect_providers
    assert "effect:team:consolidate-git-branches" in enabled.effect_providers
    assert "effect:team:create-case" in enabled.node_definition_resolvers
    assert "effect:team:finalize-agent-dispatch" in enabled.node_definition_resolvers
    assert "effect:team:record-git-promotion" in enabled.node_definition_resolvers
    assert "query:team:agent-action-artifact" not in enabled.query_providers
    assert "query:team:session-routing" in enabled.query_providers
    assert "effect:team:wake-due-agents" in enabled.node_definition_resolvers
    catalog_entries = {entry.type: entry for entry in enabled.workflow_editor_catalog_entries}
    assert "effect:team:wake-due-agents" in catalog_entries
    assert "effect:team:apply-agent-action" not in catalog_entries
    assert "effect:team:apply-ephemeral-action" not in catalog_entries
    assert "effect:team:finalize-agent-dispatch" in catalog_entries
    assert "effect:team:create-case" in catalog_entries
    assert "effect:team:update-case" in catalog_entries
    assert "query:team:agent-action-artifact" not in catalog_entries
    assert "query:team:state" in catalog_entries
    assert catalog_entries["effect:team:wake-due-agents"].label == "Wake Team agents"
    assert catalog_entries["effect:team:create-case"].label == "Create Team case"
    assert catalog_entries["effect:team:wake-due-agents"].description
    assert all(
        field.description
        for entry in catalog_entries.values()
        for field in entry.fields
    )


def test_workflow_provider_loader_uses_addon_base_dir_for_scanned_addons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    enabled = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        include_builtin=False,
        addon_loader=AddonLoaderConfig(enabled_addon_ids=["team-addon"]),
        addon_base_dir=PROJECT_ROOT,
    )

    assert "addon:team-addon" in enabled.loaded_plugins
    assert "effect:team:wake-due-agents" in enabled.effect_providers


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
