from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AwaitAgentGateProvider",
    "FilterGateProvider",
    "ForGateProvider",
    "GateProviderBase",
    "GateRuntimeContext",
    "MapGateProvider",
    "MergeGateProvider",
    "SwitchGateProvider",
]

_EXPORT_MODULES = {
    "AwaitAgentGateProvider": ".await_agent",
    "FilterGateProvider": ".filter_gate",
    "ForGateProvider": ".for_gate",
    "GateProviderBase": ".base",
    "GateRuntimeContext": ".base",
    "MapGateProvider": ".map_gate",
    "MergeGateProvider": ".merge_gate",
    "SwitchGateProvider": ".switch_gate",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
