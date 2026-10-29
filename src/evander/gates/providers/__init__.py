from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AggregateGateProvider",
    "AwaitAgentGateProvider",
    "BufferGateProvider",
    "CounterGateProvider",
    "FilterGateProvider",
    "ForGateProvider",
    "GateProviderBase",
    "GateRuntimeContext",
    "MapGateProvider",
    "MergeGateProvider",
    "SwitchGateProvider",
    "ThrottleGateProvider",
    "ValidateGateProvider",
    "WhileGateProvider",
]

_EXPORT_MODULES = {
    "AggregateGateProvider": ".aggregate_gate",
    "AwaitAgentGateProvider": ".await_agent",
    "BufferGateProvider": ".buffer_gate",
    "CounterGateProvider": ".counter_gate",
    "FilterGateProvider": ".filter_gate",
    "ForGateProvider": ".for_gate",
    "GateProviderBase": ".base",
    "GateRuntimeContext": ".base",
    "MapGateProvider": ".map_gate",
    "MergeGateProvider": ".merge_gate",
    "SwitchGateProvider": ".switch_gate",
    "ThrottleGateProvider": ".throttle_gate",
    "ValidateGateProvider": ".validate_gate",
    "WhileGateProvider": ".while_gate",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
