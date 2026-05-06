from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AdoWorkItemsQueryProvider",
    "IssueGetQueryProvider",
    "QueryProviderBase",
    "QueryRuntimeContext",
    "VmRunStateQueryProvider",
]

_EXPORT_MODULES = {
    "AdoWorkItemsQueryProvider": ".ado_work_items",
    "IssueGetQueryProvider": ".issue_get",
    "QueryProviderBase": ".base",
    "QueryRuntimeContext": ".base",
    "VmRunStateQueryProvider": ".vm_run_state",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
