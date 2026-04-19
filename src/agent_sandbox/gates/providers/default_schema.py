from __future__ import annotations

from agent_sandbox.events.models import (
    GateAwaitAgentDefinition,
    GateFilterDefinition,
    GateForDefinition,
    GateMapDefinition,
    GateMergeDefinition,
    register_node_output_schema,
    register_node_schema,
)

_GATE_MODEL_BY_TYPE = {
    "gate:filter": GateFilterDefinition,
    "gate:map": GateMapDefinition,
    "gate:merge": GateMergeDefinition,
    "gate:await-agent": GateAwaitAgentDefinition,
    "gate:for": GateForDefinition,
}



def register_gate_node_schemas() -> None:
    for node_type, model in _GATE_MODEL_BY_TYPE.items():
        register_node_schema(node_type=node_type, model_class=model)
    register_node_output_schema(
        node_type="gate:for",
        outputs={
            "current_element": {"type": "object"},
            "current_index": {"type": "integer"},
            "item_count": {"type": "integer"},
            "is_done": {"type": "boolean"},
        },
    )
