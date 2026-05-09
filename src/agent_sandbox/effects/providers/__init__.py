from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentBootstrapFrontendEffectProvider",
    "AgentCloneRepoEffectProvider",
    "AgentCopyFilesEffectProvider",
    "AgentCreateEffectProvider",
    "AgentDestroyEffectProvider",
    "AgentExecEffectProvider",
    "AgentPromptEffectProvider",
    "BashScriptEffectProvider",
    "ChatAssistantEnsureSessionEffectProvider",
    "EffectProvider",
    "EffectProviderBase",
    "EffectRuntimeContext",
    "IssueUpdateMetadataEffectProvider",
    "IssueUpdateStateEffectProvider",
    "NotificationSendEffectProvider",
    "PrCreateEffectProvider",
    "VmCopyFilesEffectProvider",
    "VmCreateEffectProvider",
    "VmExecEffectProvider",
    "VmStartEffectProvider",
    "VmStopEffectProvider",
]

_EXPORT_MODULES = {
    "AgentBootstrapFrontendEffectProvider": ".agent_bootstrap_frontend",
    "AgentCloneRepoEffectProvider": ".agent_clone_repo",
    "AgentCopyFilesEffectProvider": ".agent_copy_files",
    "AgentCreateEffectProvider": ".agent_create",
    "AgentDestroyEffectProvider": ".agent_destroy",
    "AgentExecEffectProvider": ".agent_exec",
    "AgentPromptEffectProvider": ".agent_prompt",
    "BashScriptEffectProvider": ".bash_script",
    "ChatAssistantEnsureSessionEffectProvider": ".chat_assistant",
    "EffectProvider": ".base",
    "EffectProviderBase": ".base",
    "EffectRuntimeContext": ".base",
    "IssueUpdateMetadataEffectProvider": ".issue_update_metadata",
    "IssueUpdateStateEffectProvider": ".issue_update_state",
    "NotificationSendEffectProvider": ".notification_send",
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
