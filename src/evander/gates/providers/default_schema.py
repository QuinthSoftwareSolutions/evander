from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from evander.events.models import (
    GateAggregateDefinition,
    GateAwaitAgentDefinition,
    GateBufferDefinition,
    GateCounterDefinition,
    GateClassifierSwitchDefinition,
    GateDebounceDefinition,
    GateDeserializeDefinition,
    GateDedupeDefinition,
    GateFilterDefinition,
    GateForDefinition,
    GateMapDefinition,
    GateMergeDefinition,
    GateSerializeDefinition,
    GateSwitchDefinition,
    GateThrottleDefinition,
    GateValidateDefinition,
    GateEventValidateDefinition,
    GateWhileDefinition,
)
from evander.schema_bootstrap import _GATE_OUTPUT_SCHEMAS
from evander.workflow.contracts import (
    materialize_node_model as _materialize_with_model,
    WorkflowNodeEnvelope,
    register_node_output_schema,
)

_GATE_MODEL_BY_TYPE = {
    "gate:filter": GateFilterDefinition,
    "gate:debounce": GateDebounceDefinition,
    "gate:buffer": GateBufferDefinition,
    "gate:counter": GateCounterDefinition,
    "gate:throttle": GateThrottleDefinition,
    "gate:deserialize": GateDeserializeDefinition,
    "gate:serialize": GateSerializeDefinition,
    "gate:map": GateMapDefinition,
    "gate:aggregate": GateAggregateDefinition,
    "gate:merge": GateMergeDefinition,
    "gate:dedupe": GateDedupeDefinition,
    "gate:await-agent": GateAwaitAgentDefinition,
    "gate:for": GateForDefinition,
    "gate:switch": GateSwitchDefinition,
    "gate:classifier:switch": GateClassifierSwitchDefinition,
    "gate:validate": GateValidateDefinition,
    "gate:event:validate": GateEventValidateDefinition,
    "gate:while": GateWhileDefinition,
}


def gate_node_schema_resolvers() -> dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in _GATE_MODEL_BY_TYPE.items()
    }


def register_gate_output_schemas() -> None:
    for node_type, outputs in _GATE_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
