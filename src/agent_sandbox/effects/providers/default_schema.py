from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agent_sandbox.effects.providers.schemas import (
    AdoAddWorkItemTagEffectDefinition,
    AdoPipelineRunEffectDefinition,
    AdoPostWorkItemCommentEffectDefinition,
    AdoRemoveWorkItemTagEffectDefinition,
    AdoUpdateWorkItemEffectDefinition,
    AgentBootstrapFrontendEffectDefinition,
    AgentCloneRepoEffectDefinition,
    AgentCopyFilesEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
    ArtifactImportPublishedEffectDefinition,
    ArtifactPublishArtifactsEffectDefinition,
    ArtifactStageEffectDefinition,
    EventEmitEffectDefinition,
    EventScheduleEffectDefinition,
    AgentEnqueuePromptEffectDefinition,
    AgentExecEffectDefinition,
    AgentPromptEffectDefinition,
    ChatAssistantEnsureSessionEffectDefinition,
    BashScriptEffectDefinition,
    HttpRequestEffectDefinition,
    CommunicationSendMessageEffectDefinition,
    GitPromoteAgentBranchEffectDefinition,
    GitPushBranchEffectDefinition,
    IssueUpsertAdoProxyEffectDefinition,
    IssueUpdateMetadataEffectDefinition,
    IssueUpdateStateEffectDefinition,
    NotificationSendEffectDefinition,
    PrCreateEffectDefinition,
    PullRequestAddTagEffectDefinition,
    PullRequestRemoveTagEffectDefinition,
    PullRequestUpdateEffectDefinition,
    RuntimeDiagnosticsSnapshotEffectDefinition,
    SemanticDeleteEffectDefinition,
    SemanticEnsureCollectionEffectDefinition,
    SemanticUpsertEffectDefinition,
    StateUpdateEffectDefinition,
    SessionApproveMcpToolsForRunEffectDefinition,
    SessionRenewIfExpiredEffectDefinition,
    SessionRevokeMcpToolApprovalLeaseEffectDefinition,
    SessionUpdateMetadataEffectDefinition,
    VmBootstrapFrontendEffectDefinition,
    VmBootstrapPythonEffectDefinition,
    VmCopyFilesEffectDefinition,
    VmCloneRepoEffectDefinition,
    VmCreateEffectDefinition,
    VmDeleteFilesEffectDefinition,
    VmExecEffectDefinition,
    VmStartEffectDefinition,
    VmStopEffectDefinition,
)
from agent_sandbox.schema_bootstrap import _EFFECT_OUTPUT_SCHEMAS
from agent_sandbox.workflow.contracts import WorkflowNodeEnvelope, register_node_output_schema

_EFFECT_MODEL_BY_TYPE = {
    "effect:event:emit": EventEmitEffectDefinition,
    "effect:event:schedule": EventScheduleEffectDefinition,
    "effect:vm:create": VmCreateEffectDefinition,
    "effect:vm:clone-repo": VmCloneRepoEffectDefinition,
    "effect:vm:bootstrap-python": VmBootstrapPythonEffectDefinition,
    "effect:vm:bootstrap-frontend": VmBootstrapFrontendEffectDefinition,
    "effect:vm:start": VmStartEffectDefinition,
    "effect:vm:stop": VmStopEffectDefinition,
    "effect:agent:create": AgentCreateEffectDefinition,
    "effect:agent:destroy": AgentDestroyEffectDefinition,
    "effect:agent:clone-repo": AgentCloneRepoEffectDefinition,
    "effect:agent:bootstrap-frontend": AgentBootstrapFrontendEffectDefinition,
    "effect:agent:copy-files": AgentCopyFilesEffectDefinition,
    "effect:artifact:stage": ArtifactStageEffectDefinition,
    "effect:artifact:publish-artifacts": ArtifactPublishArtifactsEffectDefinition,
    "effect:artifact:import-published": ArtifactImportPublishedEffectDefinition,
    "effect:agent:prompt": AgentPromptEffectDefinition,
    "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectDefinition,
    "effect:chat-assistant:ensure-session": ChatAssistantEnsureSessionEffectDefinition,
    "effect:agent:exec": AgentExecEffectDefinition,
    "effect:semantic:ensure-collection": SemanticEnsureCollectionEffectDefinition,
    "effect:semantic:upsert": SemanticUpsertEffectDefinition,
    "effect:semantic:delete": SemanticDeleteEffectDefinition,
    "effect:state:update": StateUpdateEffectDefinition,
    "effect:bash:script": BashScriptEffectDefinition,
    "effect:http:request": HttpRequestEffectDefinition,
    "effect:runtime:diagnostics-snapshot": RuntimeDiagnosticsSnapshotEffectDefinition,
    "effect:vm:exec": VmExecEffectDefinition,
    "effect:vm:copy-files": VmCopyFilesEffectDefinition,
    "effect:vm:delete-files": VmDeleteFilesEffectDefinition,
    "effect:issue:update-state": IssueUpdateStateEffectDefinition,
    "effect:issue:update-metadata": IssueUpdateMetadataEffectDefinition,
    "effect:issue:upsert-ado-proxy": IssueUpsertAdoProxyEffectDefinition,
    "effect:ado:pipeline-run": AdoPipelineRunEffectDefinition,
    "effect:ado:post-work-item-comment": AdoPostWorkItemCommentEffectDefinition,
    "effect:ado:add-work-item-tag": AdoAddWorkItemTagEffectDefinition,
    "effect:ado:remove-work-item-tag": AdoRemoveWorkItemTagEffectDefinition,
    "effect:ado:update-work-item": AdoUpdateWorkItemEffectDefinition,
    "effect:pull-request:add-tag": PullRequestAddTagEffectDefinition,
    "effect:pull-request:remove-tag": PullRequestRemoveTagEffectDefinition,
    "effect:pull-request:update": PullRequestUpdateEffectDefinition,
    "effect:communication:send-message": CommunicationSendMessageEffectDefinition,
    "effect:notification:send": NotificationSendEffectDefinition,
    "effect:session:approve-mcp-tools-for-run": SessionApproveMcpToolsForRunEffectDefinition,
    "effect:session:revoke-mcp-tool-approval-lease": (
        SessionRevokeMcpToolApprovalLeaseEffectDefinition
    ),
    "effect:session:update-metadata": SessionUpdateMetadataEffectDefinition,
    "effect:session:renew-if-expired": SessionRenewIfExpiredEffectDefinition,
    "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectDefinition,
    "effect:git:push-branch": GitPushBranchEffectDefinition,
    "effect:pr:create": PrCreateEffectDefinition,
}


def _materialize_with_model(model_class: type[BaseModel], node: WorkflowNodeEnvelope) -> BaseModel:
    payload = {
        "id": node.id,
        "display_name": node.display_name,
        "type": node.type,
        "outputs": node.outputs,
        **dict(node.config),
    }
    return model_class.model_validate(payload)


def effect_node_schema_resolvers() -> dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in _EFFECT_MODEL_BY_TYPE.items()
    }


def register_effect_output_schemas() -> None:
    for node_type, outputs in _EFFECT_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
