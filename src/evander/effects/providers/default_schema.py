from __future__ import annotations

from evander.code_index.providers import CodeIndexEnsureDefinition, CodeIndexReconcileDefinition

from collections.abc import Callable

from pydantic import BaseModel

from evander.effects.providers.schemas import (
    AdoBuildWaitEffectDefinition,
    AgentInstallSkillsEffectDefinition,
    AdoAddWorkItemTagEffectDefinition,
    AdoPipelineRunEffectDefinition,
    AdoPostWorkItemCommentEffectDefinition,
    AdoRemoveWorkItemTagEffectDefinition,
    AdoUpdateWorkItemEffectDefinition,
    AgentBootstrapFrontendEffectDefinition,
    AgentCloneRepoEffectDefinition,
    AgentPrepareReferenceRepositoriesEffectDefinition,
    AgentGrantRepoAccessEffectDefinition,
    AgentRevokeRepoAccessEffectDefinition,
    AgentCopyFilesEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
    ArtifactSpaceCreateEffectDefinition,
    ArtifactSpacePublishVersionEffectDefinition,
    ArtifactImportPublishedEffectDefinition,
    ArtifactPublishArtifactsEffectDefinition,
    ArtifactStageEffectDefinition,
    EventEmitEffectDefinition,
    EventScheduleEffectDefinition,
    AgentEnqueuePromptEffectDefinition,
    AgentExecEffectDefinition,
    AgentPromptEffectDefinition,
    ChatAssistantEnsureSessionEffectDefinition,
    CodeConsultationEffectDefinition,
    HttpRequestEffectDefinition,
    CommunicationSendMessageEffectDefinition,
    GitEnsureManagedRepoEffectDefinition,
    GitPromoteAgentBranchEffectDefinition,
    GitPushBranchEffectDefinition,
    IssueUpsertAdoProxyEffectDefinition,
    IssueUpdateMetadataEffectDefinition,
    IssueUpdateStateEffectDefinition,
    NotificationSendEffectDefinition,
    PrCreateEffectDefinition,
    PullRequestAddTagEffectDefinition,
    PullRequestReplyEffectDefinition,
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
    SessionSetImageGenerationEnabledEffectDefinition,
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
from evander.schema_bootstrap import _EFFECT_OUTPUT_SCHEMAS
from evander.workflow.contracts import (
    materialize_node_model as _materialize_with_model,
    WorkflowNodeEnvelope,
    register_node_output_schema,
)

_EFFECT_MODEL_BY_TYPE = {
    "effect:code-index:ensure": CodeIndexEnsureDefinition,
    "effect:code-index:reconcile": CodeIndexReconcileDefinition,
    "effect:event:emit": EventEmitEffectDefinition,
    "effect:event:schedule": EventScheduleEffectDefinition,
    "effect:artifact-space:create": ArtifactSpaceCreateEffectDefinition,
    "effect:artifact-space:publish-version": ArtifactSpacePublishVersionEffectDefinition,
    "effect:vm:create": VmCreateEffectDefinition,
    "effect:vm:clone-repo": VmCloneRepoEffectDefinition,
    "effect:vm:bootstrap-python": VmBootstrapPythonEffectDefinition,
    "effect:vm:bootstrap-frontend": VmBootstrapFrontendEffectDefinition,
    "effect:vm:start": VmStartEffectDefinition,
    "effect:vm:stop": VmStopEffectDefinition,
    "effect:agent:create": AgentCreateEffectDefinition,
    "effect:agent:destroy": AgentDestroyEffectDefinition,
    "effect:agent:clone-repo": AgentCloneRepoEffectDefinition,
    "effect:agent:prepare-reference-repositories": (
        AgentPrepareReferenceRepositoriesEffectDefinition
    ),
    "effect:agent:grant-repo-access": AgentGrantRepoAccessEffectDefinition,
    "effect:agent:revoke-repo-access": AgentRevokeRepoAccessEffectDefinition,
    "effect:agent:bootstrap-frontend": AgentBootstrapFrontendEffectDefinition,
    "effect:agent:copy-files": AgentCopyFilesEffectDefinition,
    "effect:artifact:stage": ArtifactStageEffectDefinition,
    "effect:artifact:publish-artifacts": ArtifactPublishArtifactsEffectDefinition,
    "effect:artifact:import-published": ArtifactImportPublishedEffectDefinition,
    "effect:agent:prompt": AgentPromptEffectDefinition,
    "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectDefinition,
    "effect:chat-assistant:ensure-session": ChatAssistantEnsureSessionEffectDefinition,
    "effect:agent:code-consultation": CodeConsultationEffectDefinition,
    "effect:agent:exec": AgentExecEffectDefinition,
    "effect:semantic:ensure-collection": SemanticEnsureCollectionEffectDefinition,
    "effect:semantic:upsert": SemanticUpsertEffectDefinition,
    "effect:semantic:delete": SemanticDeleteEffectDefinition,
    "effect:state:update": StateUpdateEffectDefinition,
    "effect:http:request": HttpRequestEffectDefinition,
    "effect:runtime:diagnostics-snapshot": RuntimeDiagnosticsSnapshotEffectDefinition,
    "effect:vm:exec": VmExecEffectDefinition,
    "effect:vm:copy-files": VmCopyFilesEffectDefinition,
    "effect:vm:delete-files": VmDeleteFilesEffectDefinition,
    "effect:issue:update-state": IssueUpdateStateEffectDefinition,
    "effect:issue:update-metadata": IssueUpdateMetadataEffectDefinition,
    "effect:issue:upsert-ado-proxy": IssueUpsertAdoProxyEffectDefinition,
    "effect:ado:pipeline-run": AdoPipelineRunEffectDefinition,
    "effect:ado:build-wait": AdoBuildWaitEffectDefinition,
    "effect:agent:install-skills": AgentInstallSkillsEffectDefinition,
    "effect:ado:post-work-item-comment": AdoPostWorkItemCommentEffectDefinition,
    "effect:ado:add-work-item-tag": AdoAddWorkItemTagEffectDefinition,
    "effect:ado:remove-work-item-tag": AdoRemoveWorkItemTagEffectDefinition,
    "effect:ado:update-work-item": AdoUpdateWorkItemEffectDefinition,
    "effect:pull-request:add-tag": PullRequestAddTagEffectDefinition,
    "effect:pull-request:reply": PullRequestReplyEffectDefinition,
    "effect:pull-request:remove-tag": PullRequestRemoveTagEffectDefinition,
    "effect:pull-request:update": PullRequestUpdateEffectDefinition,
    "effect:communication:send-message": CommunicationSendMessageEffectDefinition,
    "effect:notification:send": NotificationSendEffectDefinition,
    "effect:session:approve-mcp-tools-for-run": SessionApproveMcpToolsForRunEffectDefinition,
    "effect:session:revoke-mcp-tool-approval-lease": (
        SessionRevokeMcpToolApprovalLeaseEffectDefinition
    ),
    "effect:session:update-metadata": SessionUpdateMetadataEffectDefinition,
    "effect:session:set-image-generation-enabled": (
        SessionSetImageGenerationEnabledEffectDefinition
    ),
    "effect:session:renew-if-expired": SessionRenewIfExpiredEffectDefinition,
    "effect:git:ensure-managed-repo": GitEnsureManagedRepoEffectDefinition,
    "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectDefinition,
    "effect:git:push-branch": GitPushBranchEffectDefinition,
    "effect:pr:create": PrCreateEffectDefinition,
}


def effect_node_schema_resolvers() -> dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in _EFFECT_MODEL_BY_TYPE.items()
    }


def register_effect_output_schemas() -> None:
    for node_type, outputs in _EFFECT_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
