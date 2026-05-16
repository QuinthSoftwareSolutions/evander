from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agent_sandbox.effects.providers.schemas import (
    AdoUpdateWorkItemEffectDefinition,
    AgentBootstrapFrontendEffectDefinition,
    AgentCloneRepoEffectDefinition,
    AgentCopyFilesEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
    EventEmitEffectDefinition,
    AgentEnqueuePromptEffectDefinition,
    AgentExecEffectDefinition,
    AgentPromptEffectDefinition,
    ChatAssistantEnsureSessionEffectDefinition,
    BashScriptEffectDefinition,
    CommunicationSendMessageEffectDefinition,
    GitPromoteAgentBranchEffectDefinition,
    IssueUpdateMetadataEffectDefinition,
    IssueUpdateStateEffectDefinition,
    NotificationSendEffectDefinition,
    PrCreateEffectDefinition,
    SemanticDeleteEffectDefinition,
    SemanticEnsureCollectionEffectDefinition,
    SemanticUpsertEffectDefinition,
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
    "effect:agent:prompt": AgentPromptEffectDefinition,
    "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectDefinition,
    "effect:chat-assistant:ensure-session": ChatAssistantEnsureSessionEffectDefinition,
    "effect:agent:exec": AgentExecEffectDefinition,
    "effect:semantic:ensure-collection": SemanticEnsureCollectionEffectDefinition,
    "effect:semantic:upsert": SemanticUpsertEffectDefinition,
    "effect:semantic:delete": SemanticDeleteEffectDefinition,
    "effect:bash:script": BashScriptEffectDefinition,
    "effect:vm:exec": VmExecEffectDefinition,
    "effect:vm:copy-files": VmCopyFilesEffectDefinition,
    "effect:vm:delete-files": VmDeleteFilesEffectDefinition,
    "effect:issue:update-state": IssueUpdateStateEffectDefinition,
    "effect:issue:update-metadata": IssueUpdateMetadataEffectDefinition,
    "effect:ado:update-work-item": AdoUpdateWorkItemEffectDefinition,
    "effect:communication:send-message": CommunicationSendMessageEffectDefinition,
    "effect:notification:send": NotificationSendEffectDefinition,
    "effect:session:update-metadata": SessionUpdateMetadataEffectDefinition,
    "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectDefinition,
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
