from __future__ import annotations

import importlib
import sys
from pathlib import Path

from agent_sandbox.workflow_plugins import load_provider_plugins


def test_load_provider_plugins_supports_explicit_module_list(tmp_path: Path, monkeypatch) -> None:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / "custom_plugin.py").write_text(
        """
from agent_sandbox.triggers.providers.base import TriggerProviderBase

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
    assert "agent_sandbox.providers.builtin" in catalog.loaded_plugins
    assert "custom_plugin" in catalog.loaded_plugins


def test_load_provider_plugins_supports_entry_points(monkeypatch) -> None:
    class _EntryPointStub:
        name = "custom"
        value = "custom.entry"

        @staticmethod
        def load():
            from agent_sandbox.queries.providers.base import QueryProviderBase

            class CustomQueryProvider(QueryProviderBase):
                pass

            return {"query_providers": {"query:custom:test": CustomQueryProvider}}

    monkeypatch.setattr("agent_sandbox.workflow_plugins._iter_provider_entry_points", lambda: (_EntryPointStub(),))

    catalog = load_provider_plugins(module_names=[], include_entry_points=True)

    assert "query:custom:test" in catalog.query_providers
    assert "entry_point:custom.entry" in catalog.loaded_plugins
