from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "DEFAULT_GATE_PROVIDER_REGISTRY",
    "GateProviderFactory",
    "GateProviderRegistry",
]

_EXPORT_MODULES = {
    "DEFAULT_GATE_PROVIDER_REGISTRY": ".registry",
    "GateProviderFactory": ".registry",
    "GateProviderRegistry": ".registry",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
