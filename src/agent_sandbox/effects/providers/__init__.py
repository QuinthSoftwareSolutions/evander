from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentPromptEffectProvider",
    "BashScriptEffectProvider",
    "EffectProvider",
    "EffectProviderBase",
    "EffectRuntimeContext",
    "IssueUpdateStateEffectProvider",
    "PrCreateEffectProvider",
    "VmCopyFilesEffectProvider",
    "VmCreateEffectProvider",
    "VmExecEffectProvider",
    "VmStartEffectProvider",
    "VmStopEffectProvider",
]

_EXPORT_MODULES = {
    "AgentPromptEffectProvider": ".agent_prompt",
    "BashScriptEffectProvider": ".bash_script",
    "EffectProvider": ".base",
    "EffectProviderBase": ".base",
    "EffectRuntimeContext": ".base",
    "IssueUpdateStateEffectProvider": ".issue_update_state",
    "PrCreateEffectProvider": ".pr_create",
    "VmCopyFilesEffectProvider": ".vm_copy_files",
    "VmCreateEffectProvider": ".vm_create",
    "VmExecEffectProvider": ".vm_exec",
    "VmStartEffectProvider": ".vm_start",
    "VmStopEffectProvider": ".vm_stop",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
