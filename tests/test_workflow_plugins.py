from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from evander.workflow.plugins import load_provider_plugins


def test_load_provider_plugins_supports_explicit_module_list(tmp_path: Path, monkeypatch) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "custom_plugin.py").write_text(
        """
from evander.triggers.providers.base import TriggerProviderBase

class CustomTriggerProvider(TriggerProviderBase):
    pass

def register_providers():
    return {"trigger_providers": {"trigger:custom:test": CustomTriggerProvider}}
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("custom_plugin", None)

    catalog = load_provider_plugins(module_names=["custom_plugin"], include_entry_points=False)

    assert "trigger:custom:test" in catalog.trigger_providers
    assert "evander.providers.builtin" not in catalog.loaded_plugins
    assert "custom_plugin" in catalog.loaded_plugins


def test_load_provider_plugins_supports_entry_points(monkeypatch) -> None:
    class _EntryPointStub:
        name = "custom"
        value = "custom.entry"

        @staticmethod
        def load():
            from evander.queries.providers.base import QueryProviderBase

            class CustomQueryProvider(QueryProviderBase):
                pass

            return {"query_providers": {"query:custom:test": CustomQueryProvider}}

    monkeypatch.setattr(
        "evander.workflow.plugins._iter_provider_entry_points", lambda: (_EntryPointStub(),)
    )

    catalog = load_provider_plugins(module_names=[], include_entry_points=True)

    assert "query:custom:test" in catalog.query_providers
    assert "entry_point:custom.entry" in catalog.loaded_plugins


def test_load_provider_plugins_can_include_builtin_explicitly() -> None:
    catalog = load_provider_plugins(
        module_names=[], include_entry_points=False, include_builtin=True
    )

    assert "trigger:cron" in catalog.trigger_providers
    assert "effect:http:request" in catalog.effect_providers
    assert "evander.providers.builtin" in catalog.loaded_plugins


def test_load_provider_plugins_exposes_workflow_editor_catalog_entries(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "catalog_plugin.py").write_text(
        """
from evander.triggers.providers.base import TriggerProviderBase

class CatalogTriggerProvider(TriggerProviderBase):
    pass

def register_providers():
    return {
        "trigger_providers": {"trigger:catalog:test": CatalogTriggerProvider},
        "workflow_editor_catalog_entries": [
            {
                "kind": "trigger",
                "type": "trigger:catalog:test",
                "label": "Catalog test",
                "description": "Catalog test trigger.",
                "fields": [
                    {
                        "key": "message",
                        "label": "Message",
                        "kind": "string",
                        "required": True,
                        "description": "Message to emit.",
                    }
                ],
            }
        ],
    }
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("catalog_plugin", None)

    catalog = load_provider_plugins(
        module_names=["catalog_plugin"],
        include_entry_points=False,
        include_builtin=False,
    )

    assert [entry.type for entry in catalog.workflow_editor_catalog_entries] == [
        "trigger:catalog:test"
    ]
    entry = catalog.workflow_editor_catalog_entries[0]
    assert entry.label == "Catalog test"
    assert entry.fields[0].description == "Message to emit."


def test_load_provider_plugins_exposes_internal_event_catalog_entries(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "event_catalog_plugin.py").write_text(
        """
def register_providers():
    return {
        "internal_event_catalog_entries": [
            {
                "event_type": "custom.ready",
                "label": "Custom ready",
                "description": "Custom ready event.",
                "source": "Test plugin",
                "metadata": {"domain": "custom"},
            }
        ],
    }
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("event_catalog_plugin", None)

    catalog = load_provider_plugins(
        module_names=["event_catalog_plugin"],
        include_entry_points=False,
        include_builtin=False,
    )

    assert [entry.event_type for entry in catalog.internal_event_catalog_entries] == [
        "custom.ready"
    ]
    entry = catalog.internal_event_catalog_entries[0]
    assert entry.label == "Custom ready"
    assert entry.description == "Custom ready event."
    assert entry.source == "Test plugin"
    assert entry.metadata == {"domain": "custom"}


def test_load_provider_plugins_rejects_catalog_entry_without_provider(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "bad_catalog_plugin.py").write_text(
        """
from evander.triggers.providers.base import TriggerProviderBase

class CatalogTriggerProvider(TriggerProviderBase):
    pass

def register_providers():
    return {
        "trigger_providers": {"trigger:catalog:test": CatalogTriggerProvider},
        "workflow_editor_catalog_entries": [
            {
                "kind": "trigger",
                "type": "trigger:catalog:missing",
                "label": "Missing",
            }
        ],
    }
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("bad_catalog_plugin", None)

    with pytest.raises(ValueError, match="does not match a registered trigger provider"):
        load_provider_plugins(
            module_names=["bad_catalog_plugin"],
            include_entry_points=False,
            include_builtin=False,
        )


def test_load_provider_plugins_rejects_duplicate_catalog_entries(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    for module_name in ("catalog_one", "catalog_two"):
        (module_root / f"{module_name}.py").write_text(
            """
from evander.triggers.providers.base import TriggerProviderBase

class CatalogTriggerProvider(TriggerProviderBase):
    pass

def register_providers():
    return {
        "trigger_providers": {"trigger:catalog:duplicate": CatalogTriggerProvider},
        "workflow_editor_catalog_entries": [
            {
                "kind": "trigger",
                "type": "trigger:catalog:duplicate",
                "label": "Duplicate",
            }
        ],
    }
""",
            encoding="utf-8",
        )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("catalog_one", None)
    sys.modules.pop("catalog_two", None)

    with pytest.raises(ValueError, match="Duplicate workflow editor catalog entry"):
        load_provider_plugins(
            module_names=["catalog_one", "catalog_two"],
            include_entry_points=False,
            include_builtin=False,
        )


def test_load_provider_plugins_rejects_duplicate_internal_event_catalog_entries(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    for module_name in ("event_catalog_one", "event_catalog_two"):
        (module_root / f"{module_name}.py").write_text(
            """
def register_providers():
    return {
        "internal_event_catalog_entries": [
            {
                "event_type": "custom.duplicate",
                "label": "Duplicate",
            }
        ],
    }
""",
            encoding="utf-8",
        )
    monkeypatch.syspath_prepend(str(module_root))
    importlib.invalidate_caches()
    sys.modules.pop("event_catalog_one", None)
    sys.modules.pop("event_catalog_two", None)

    with pytest.raises(ValueError, match="Duplicate internal event catalog entry"):
        load_provider_plugins(
            module_names=["event_catalog_one", "event_catalog_two"],
            include_entry_points=False,
            include_builtin=False,
        )
