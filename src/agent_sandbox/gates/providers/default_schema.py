from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agent_sandbox.events.models import (
    GateAwaitAgentDefinition,
    GateFilterDefinition,
    GateForDefinition,
    GateMapDefinition,
    GateMergeDefinition,
)
from agent_sandbox.schema_bootstrap import _GATE_OUTPUT_SCHEMAS
from agent_sandbox.workflow.contracts import WorkflowNodeEnvelope, register_node_output_schema

_GATE_MODEL_BY_TYPE = {
    "gate:filter": GateFilterDefinition,
    "gate:map": GateMapDefinition,
    "gate:merge": GateMergeDefinition,
    "gate:await-agent": GateAwaitAgentDefinition,
    "gate:for": GateForDefinition,
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


def gate_node_schema_resolvers() -> dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in _GATE_MODEL_BY_TYPE.items()
    }


def register_gate_output_schemas() -> None:
    for node_type, outputs in _GATE_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
