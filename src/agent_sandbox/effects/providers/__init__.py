from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AdoUpdateWorkItemEffectProvider",
    "AgentBootstrapFrontendEffectProvider",
    "AgentCloneRepoEffectProvider",
    "AgentCopyFilesEffectProvider",
    "AgentCreateEffectProvider",
    "AgentDestroyEffectProvider",
    "AgentExecEffectProvider",
    "AgentPromptEffectProvider",
    "ArtifactImportPublishedEffectProvider",
    "ArtifactPublishArtifactsEffectProvider",
    "ArtifactStageEffectProvider",
    "BashScriptEffectProvider",
    "ChatAssistantEnsureSessionEffectProvider",
    "CommunicationSendMessageEffectProvider",
    "EffectProvider",
    "EffectProviderBase",
    "EffectRuntimeContext",
    "HttpRequestEffectProvider",
    "IssueUpdateMetadataEffectProvider",
    "IssueUpdateStateEffectProvider",
    "NotificationSendEffectProvider",
    "PrCreateEffectProvider",
    "PullRequestUpdateEffectProvider",
    "SemanticDeleteEffectProvider",
    "SemanticEnsureCollectionEffectProvider",
    "SemanticUpsertEffectProvider",
    "StateUpdateEffectProvider",
    "SessionApproveMcpToolsForRunEffectProvider",
    "SessionRenewIfExpiredEffectProvider",
    "EventScheduleEffectProvider",
    "VmCopyFilesEffectProvider",
    "VmCreateEffectProvider",
    "VmExecEffectProvider",
    "VmStartEffectProvider",
    "VmStopEffectProvider",
]

_EXPORT_MODULES = {
    "AdoUpdateWorkItemEffectProvider": ".ado_update_work_item",
    "AgentBootstrapFrontendEffectProvider": ".agent_bootstrap_frontend",
    "AgentCloneRepoEffectProvider": ".agent_clone_repo",
    "AgentCopyFilesEffectProvider": ".agent_copy_files",
    "AgentCreateEffectProvider": ".agent_create",
    "AgentDestroyEffectProvider": ".agent_destroy",
    "AgentExecEffectProvider": ".agent_exec",
    "AgentPromptEffectProvider": ".agent_prompt",
    "ArtifactImportPublishedEffectProvider": ".artifacts",
    "ArtifactPublishArtifactsEffectProvider": ".artifacts",
    "ArtifactStageEffectProvider": ".artifacts",
    "BashScriptEffectProvider": ".bash_script",
    "ChatAssistantEnsureSessionEffectProvider": ".chat_assistant",
    "CommunicationSendMessageEffectProvider": ".communication_send_message",
    "EffectProvider": ".base",
    "EffectProviderBase": ".base",
    "EffectRuntimeContext": ".base",
    "HttpRequestEffectProvider": ".http_request",
    "IssueUpdateMetadataEffectProvider": ".issue_update_metadata",
    "IssueUpdateStateEffectProvider": ".issue_update_state",
    "EventScheduleEffectProvider": ".event_schedule",
    "NotificationSendEffectProvider": ".notification_send",
    "PrCreateEffectProvider": ".pr_create",
    "PullRequestUpdateEffectProvider": ".pull_request_update",
    "SemanticDeleteEffectProvider": ".semantic_delete",
    "SemanticEnsureCollectionEffectProvider": ".semantic_ensure_collection",
    "SemanticUpsertEffectProvider": ".semantic_upsert",
    "StateUpdateEffectProvider": ".state_update",
    "SessionApproveMcpToolsForRunEffectProvider": ".session_approve_mcp_tools",
    "SessionRenewIfExpiredEffectProvider": ".session_renew_if_expired",
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
