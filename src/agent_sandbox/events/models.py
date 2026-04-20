from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator
from typing_extensions import TypeAliasType

from agent_sandbox.workflow.contracts import (
    EdgeDefinition,
    NODE_OUTPUTS_CONTEXT_KEY,
    WorkflowNodeDefinition,
    WorkflowNodeEnvelope,
    effective_node_output_schema,
    normalize_directory_paths,
    normalize_string_list,
    validate_filter_when_expression,
    validate_output_mapping_keys,
)

TriggerType: TypeAlias = str
GateType: TypeAlias = str
EffectType: TypeAlias = str
QueryType: TypeAlias = str

OutputName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
OutputReferenceString = Annotated[str, StringConstraints(pattern=r"^\$\([A-Za-z_][A-Za-z0-9_]*\)$")]
InterpolatedString = Annotated[str, StringConstraints(pattern=r".*\$\([A-Za-z_][A-Za-z0-9_]*\).*")]

ConfigScalarValue: TypeAlias = str | int | float | bool | None
ConfigValue = TypeAliasType(
    "ConfigValue",
    ConfigScalarValue
    | OutputReferenceString
    | InterpolatedString
    | list["ConfigValue"]
    | dict[str, "ConfigValue"],
)


class TriggerDefinition(WorkflowNodeDefinition):
    type: TriggerType


class GateDefinition(WorkflowNodeDefinition):
    type: GateType
    outputs: dict[str, dict[str, Any]] | None = None


class GateFilterDefinition(GateDefinition):
    type: Literal["gate:filter"]
    match: ConfigValue | None = None
    when: ConfigValue | None = None

    @field_validator("match", "when")
    @classmethod
    def validate_filter_field_is_object(cls, value: ConfigValue | None, info) -> ConfigValue | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(f"gate:filter {info.field_name} must be a JSON object")
        return value

    @model_validator(mode="after")
    def validate_match_or_when(self) -> "GateFilterDefinition":
        if self.match is None and self.when is None:
            raise ValueError("gate:filter requires exactly one of match or when")
        if self.match is not None and self.when is not None:
            raise ValueError("gate:filter match and when are mutually exclusive")
        if self.when is not None:
            validate_filter_when_expression(self.when)
        return self


class GateMapDefinition(GateDefinition):
    type: Literal["gate:map"]
    template: ConfigValue

    @field_validator("template")
    @classmethod
    def validate_template_is_object(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, dict):
            raise ValueError("gate:map template must be a JSON object")
        return value


class GateMergeDefinition(GateDefinition):
    type: Literal["gate:merge"]


class GateAwaitAgentDefinition(GateDefinition):
    type: Literal["gate:await-agent"]
    completion_trigger_id: str | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "GateAwaitAgentDefinition":
        selectors = [self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("gate:await-agent requires session_id or vm_name")
        return self


class GateForDefinition(GateDefinition):
    type: Literal["gate:for"]
    items: ConfigValue

    @model_validator(mode="after")
    def validate_default_outputs(self) -> "GateForDefinition":
        if self.outputs is None:
            self.outputs = effective_node_output_schema(self)
        return self


class EffectDefinition(WorkflowNodeDefinition):
    type: EffectType


class QueryDefinition(WorkflowNodeDefinition):
    type: QueryType


TriggerDefinitionDiscriminated = WorkflowNodeEnvelope
EffectDefinitionDiscriminated = WorkflowNodeEnvelope
QueryDefinitionDiscriminated = WorkflowNodeEnvelope
GateDefinitionDiscriminated = (
    Annotated[
        GateFilterDefinition | GateMapDefinition | GateMergeDefinition | GateAwaitAgentDefinition | GateForDefinition,
        Field(discriminator="type"),
    ]
    | WorkflowNodeEnvelope
)

WorkflowNodeType = TriggerType | GateType | EffectType | QueryType


class TriggerEvent(BaseModel):
    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: WorkflowNodeType | str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, min_length=1)


_NODE_SCHEMA_RESOLVERS: dict[str, Any] | None = None


def materialize_node_definition(node: WorkflowNodeEnvelope) -> BaseModel:
    global _NODE_SCHEMA_RESOLVERS
    if _NODE_SCHEMA_RESOLVERS is None:
        from agent_sandbox.workflow_plugins import NODE_DEFINITION_RESOLVERS

        _NODE_SCHEMA_RESOLVERS = dict(NODE_DEFINITION_RESOLVERS)

    resolver = _NODE_SCHEMA_RESOLVERS.get(node.type)
    if resolver is None:
        return node
    return resolver(node)

