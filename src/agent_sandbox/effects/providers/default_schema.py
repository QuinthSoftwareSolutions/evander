from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agent_sandbox.effects.providers.schemas import (
    AgentEnqueuePromptEffectDefinition,
    AgentPromptEffectDefinition,
    BashScriptEffectDefinition,
    GitPromoteAgentBranchEffectDefinition,
    IssueUpdateStateEffectDefinition,
    PrCreateEffectDefinition,
    SessionUpdateMetadataEffectDefinition,
    VmCopyFilesEffectDefinition,
    VmCreateEffectDefinition,
    VmDeleteFilesEffectDefinition,
    VmExecEffectDefinition,
    VmStartEffectDefinition,
    VmStopEffectDefinition,
)
from agent_sandbox.workflow.contracts import WorkflowNodeEnvelope, register_node_output_schema

_EFFECT_MODEL_BY_TYPE = {
    "effect:vm:create": VmCreateEffectDefinition,
    "effect:vm:start": VmStartEffectDefinition,
    "effect:vm:stop": VmStopEffectDefinition,
    "effect:agent:prompt": AgentPromptEffectDefinition,
    "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectDefinition,
    "effect:bash:script": BashScriptEffectDefinition,
    "effect:vm:exec": VmExecEffectDefinition,
    "effect:vm:copy-files": VmCopyFilesEffectDefinition,
    "effect:vm:delete-files": VmDeleteFilesEffectDefinition,
    "effect:issue:update-state": IssueUpdateStateEffectDefinition,
    "effect:session:update-metadata": SessionUpdateMetadataEffectDefinition,
    "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectDefinition,
    "effect:pr:create": PrCreateEffectDefinition,
}

_EFFECT_OUTPUT_SCHEMAS = {
    "effect:vm:create": {"success": {"type": "boolean"}, "session_id": {"type": "string"}, "vm_name": {"type": "string"}},
    "effect:vm:start": {"success": {"type": "boolean"}, "message": {"type": "string"}},
    "effect:vm:stop": {"success": {"type": "boolean"}, "message": {"type": "string"}},
    "effect:agent:prompt": {"dispatch_id": {"type": "string"}, "thread_id": {"type": "string"}, "success": {"type": "boolean"}},
    "effect:agent:enqueue-prompt": {"dispatch_id": {"type": "string"}, "thread_id": {"type": "string"}, "success": {"type": "boolean"}},
    "effect:bash:script": {"success": {"type": "boolean"}, "exit_code": {"type": "integer"}, "stdout": {"type": "string"}, "stderr": {"type": "string"}},
    "effect:vm:exec": {"success": {"type": "boolean"}, "exit_code": {"type": "integer"}, "stdout": {"type": "string"}, "stderr": {"type": "string"}},
    "effect:vm:copy-files": {"success": {"type": "boolean"}, "copied_count": {"type": "integer"}},
    "effect:vm:delete-files": {"success": {"type": "boolean"}, "deleted_count": {"type": "integer"}},
    "effect:issue:update-state": {"success": {"type": "boolean"}, "state": {"type": "string"}},
    "effect:session:update-metadata": {"success": {"type": "boolean"}, "metadata": {"type": "object"}},
    "effect:git:promote-agent-branch": {"success": {"type": "boolean"}, "review_branch": {"type": "string"}},
    "effect:pr:create": {"success": {"type": "boolean"}, "pr_id": {"type": "string"}, "pr_url": {"type": "string"}},
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
