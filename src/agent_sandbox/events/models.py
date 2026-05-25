from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import TypeAliasType

from agent_sandbox.gate_runtime import GateRuntimeError, parse_iso8601_duration
from agent_sandbox.workflow.contracts import (
    EdgeDefinition as EdgeDefinition,
    NODE_OUTPUTS_CONTEXT_KEY as NODE_OUTPUTS_CONTEXT_KEY,
    WorkflowNodeDefinition,
    WorkflowNodeEnvelope,
    effective_node_output_schema,
    normalize_directory_paths as normalize_directory_paths,
    normalize_string_list as normalize_string_list,
    validate_filter_when_expression,
    validate_output_mapping_keys as validate_output_mapping_keys,
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


class GateDeserializeDefinition(GateDefinition):
    type: Literal["gate:deserialize"]
    content: ConfigValue
    format: Literal["auto", "json", "yaml"] = "auto"

    @field_validator("content")
    @classmethod
    def validate_content_present(cls, value: ConfigValue) -> ConfigValue:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("gate:deserialize content must be a non-empty string or binding")
        return value


class GateSerializeDefinition(GateDefinition):
    type: Literal["gate:serialize"]
    value: ConfigValue
    format: Literal["json", "yaml"] = "json"

    @field_validator("value")
    @classmethod
    def validate_value_present(cls, value: ConfigValue) -> ConfigValue:
        if value is None:
            raise ValueError("gate:serialize value is required")
        if isinstance(value, str) and not value.strip():
            raise ValueError("gate:serialize value must be a non-empty string or binding")
        return value


class GateMergeDefinition(GateDefinition):
    type: Literal["gate:merge"]


class GateDebounceDefinition(GateDefinition):
    type: Literal["gate:debounce"]
    key: ConfigValue
    quiet_period: str
    max_delay: str

    @field_validator("quiet_period", "max_delay")
    @classmethod
    def validate_duration(cls, value: str, info) -> str:
        try:
            parse_iso8601_duration(value, field_name=info.field_name)
        except GateRuntimeError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("gate:debounce key must be a non-empty string or binding template")
        return value

    @model_validator(mode="after")
    def validate_delay_order(self) -> "GateDebounceDefinition":
        max_delay = parse_iso8601_duration(self.max_delay, field_name="max_delay")
        quiet_period = parse_iso8601_duration(self.quiet_period, field_name="quiet_period")
        if max_delay < quiet_period:
            raise ValueError(
                "gate:debounce max_delay must be greater than or equal to quiet_period"
            )
        return self


class GateDedupeDefinition(GateDefinition):
    type: Literal["gate:dedupe"]
    key: ConfigValue
    ttl: ConfigValue

    @field_validator("key")
    @classmethod
    def validate_key_present(cls, value: ConfigValue) -> ConfigValue:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("gate:dedupe key must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError("gate:dedupe key must be a string")
        return value

    @field_validator("ttl")
    @classmethod
    def validate_ttl_present(cls, value: ConfigValue) -> ConfigValue:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("gate:dedupe ttl must be a non-empty ISO-8601 duration")
        if not isinstance(value, str):
            raise ValueError("gate:dedupe ttl must be a string")
        return value


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


def validate_provider_node_definition(node: WorkflowNodeEnvelope) -> WorkflowNodeEnvelope:
    materialize_node_definition(node)
    return node


TriggerDefinitionDiscriminated = Annotated[
    WorkflowNodeEnvelope, AfterValidator(validate_provider_node_definition)
]
EffectDefinitionDiscriminated = Annotated[
    WorkflowNodeEnvelope, AfterValidator(validate_provider_node_definition)
]
QueryDefinitionDiscriminated = Annotated[
    WorkflowNodeEnvelope, AfterValidator(validate_provider_node_definition)
]
GateDefinitionDiscriminated = Annotated[
    WorkflowNodeEnvelope, AfterValidator(validate_provider_node_definition)
]

WorkflowNodeType = TriggerType | GateType | EffectType | QueryType


class TriggerEvent(BaseModel):
    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: WorkflowNodeType | str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, min_length=1)


_NODE_SCHEMA_RESOLVERS: dict[str, Any] | None = None


def _materialize_with_model(model_class: type[BaseModel], node: WorkflowNodeEnvelope) -> BaseModel:
    payload = {
        "id": node.id,
        "display_name": node.display_name,
        "type": node.type,
        "outputs": node.outputs,
        **dict(node.config),
    }
    return model_class.model_validate(payload)


def _builtin_node_schema_resolvers() -> dict[str, Any]:

    from agent_sandbox.events import provider_models

    models_by_type: dict[str, type[BaseModel]] = {
        "trigger:event": provider_models.EventTriggerDefinition,
        "trigger:cron": provider_models.CronTriggerDefinition,
        "trigger:ticket:created": provider_models.TicketCreatedTriggerDefinition,
        "trigger:ticket:changed": provider_models.TicketChangedTriggerDefinition,
        "trigger:issue:comment-created": provider_models.IssueCommentCreatedTriggerDefinition,
        "trigger:ado:work-item-matching": provider_models.AdoWorkItemMatchingTriggerDefinition,
        "trigger:script-run-request": provider_models.ScriptRunRequestTriggerDefinition,
        "trigger:discord:new-message": provider_models.DiscordNewMessageTriggerDefinition,
        "trigger:teams:new-message": provider_models.TeamsNewMessageTriggerDefinition,
        "trigger:teams-meeting:new-transcript": (
            provider_models.TeamsMeetingNewTranscriptTriggerDefinition
        ),
        "trigger:email:new-message": provider_models.EmailNewMessageTriggerDefinition,
        "trigger:pull-request:new-feedback": provider_models.PullRequestNewFeedbackTriggerDefinition,
        "trigger:pull-request:completed": provider_models.PullRequestCompletedTriggerDefinition,
        "trigger:review:new-request": provider_models.ReviewNewRequestTriggerDefinition,
        "trigger:git:push": provider_models.GitPushTriggerDefinition,
        "trigger:agent:run:completed": provider_models.AgentRunCompletedTriggerDefinition,
        "trigger:agent:prompt:new": provider_models.AgentPromptNewTriggerDefinition,
        "gate:filter": GateFilterDefinition,
        "gate:map": GateMapDefinition,
        "gate:deserialize": GateDeserializeDefinition,
        "gate:serialize": GateSerializeDefinition,
        "gate:merge": GateMergeDefinition,
        "gate:debounce": GateDebounceDefinition,
        "gate:dedupe": GateDedupeDefinition,
        "gate:await-agent": GateAwaitAgentDefinition,
        "gate:for": GateForDefinition,
        "effect:event:emit": provider_models.EventEmitEffectDefinition,
        "effect:vm:create": provider_models.VmCreateEffectDefinition,
        "effect:vm:clone-repo": provider_models.VmCloneRepoEffectDefinition,
        "effect:vm:bootstrap-python": provider_models.VmBootstrapPythonEffectDefinition,
        "effect:vm:bootstrap-frontend": provider_models.VmBootstrapFrontendEffectDefinition,
        "effect:vm:start": provider_models.VmStartEffectDefinition,
        "effect:vm:stop": provider_models.VmStopEffectDefinition,
        "effect:agent:create": provider_models.AgentCreateEffectDefinition,
        "effect:agent:destroy": provider_models.AgentDestroyEffectDefinition,
        "effect:agent:clone-repo": provider_models.AgentCloneRepoEffectDefinition,
        "effect:agent:bootstrap-frontend": provider_models.AgentBootstrapFrontendEffectDefinition,
        "effect:agent:copy-files": provider_models.AgentCopyFilesEffectDefinition,
        "effect:artifact:stage": provider_models.ArtifactStageEffectDefinition,
        "effect:artifact:publish-artifacts": (
            provider_models.ArtifactPublishArtifactsEffectDefinition
        ),
        "effect:agent:prompt": provider_models.AgentPromptEffectDefinition,
        "effect:agent:enqueue-prompt": provider_models.AgentEnqueuePromptEffectDefinition,
        "effect:chat-assistant:ensure-session": (
            provider_models.ChatAssistantEnsureSessionEffectDefinition
        ),
        "effect:agent:exec": provider_models.AgentExecEffectDefinition,
        "effect:semantic:ensure-collection": (
            provider_models.SemanticEnsureCollectionEffectDefinition
        ),
        "effect:semantic:upsert": provider_models.SemanticUpsertEffectDefinition,
        "effect:semantic:delete": provider_models.SemanticDeleteEffectDefinition,
        "effect:bash:script": provider_models.BashScriptEffectDefinition,
        "effect:vm:exec": provider_models.VmExecEffectDefinition,
        "effect:vm:copy-files": provider_models.VmCopyFilesEffectDefinition,
        "effect:vm:delete-files": provider_models.VmDeleteFilesEffectDefinition,
        "effect:issue:update-state": provider_models.IssueUpdateStateEffectDefinition,
        "effect:issue:update-metadata": provider_models.IssueUpdateMetadataEffectDefinition,
        "effect:issue:upsert-ado-proxy": provider_models.IssueUpsertAdoProxyEffectDefinition,
        "effect:ado:update-work-item": provider_models.AdoUpdateWorkItemEffectDefinition,
        "effect:pull-request:update": provider_models.PullRequestUpdateEffectDefinition,
        "effect:communication:send-message": (
            provider_models.CommunicationSendMessageEffectDefinition
        ),
        "effect:notification:send": provider_models.NotificationSendEffectDefinition,
        "effect:session:approve-mcp-tools-for-run": (
            provider_models.SessionApproveMcpToolsForRunEffectDefinition
        ),
        "effect:session:update-metadata": provider_models.SessionUpdateMetadataEffectDefinition,
        "effect:session:renew-if-expired": (
            provider_models.SessionRenewIfExpiredEffectDefinition
        ),
        "effect:git:promote-agent-branch": provider_models.GitPromoteAgentBranchEffectDefinition,
        "effect:pr:create": provider_models.PrCreateEffectDefinition,
        "query:vm:run-state": provider_models.VmRunStateQueryDefinition,
        "query:git:pr-note": provider_models.GitPrNoteQueryDefinition,
        "query:session-context": provider_models.SessionContextQueryDefinition,
        "query:session:list": provider_models.SessionListQueryDefinition,
        "query:agent:artifact-text": provider_models.AgentArtifactTextQueryDefinition,
        "query:issue:get": provider_models.IssueGetQueryDefinition,
        "query:issue:list": provider_models.IssueListQueryDefinition,
        "query:ado:work-items": provider_models.AdoWorkItemsQueryDefinition,
        "query:pull-request:list": provider_models.PullRequestListQueryDefinition,
        "query:pull-request:feedback": provider_models.PullRequestFeedbackQueryDefinition,
        "query:semantic:store-status": provider_models.SemanticStoreStatusQueryDefinition,
        "query:semantic:search": provider_models.SemanticSearchQueryDefinition,
        "query:semantic:get": provider_models.SemanticGetQueryDefinition,
    }
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in models_by_type.items()
    }


def materialize_node_definition(node: WorkflowNodeEnvelope) -> BaseModel:
    global _NODE_SCHEMA_RESOLVERS
    if _NODE_SCHEMA_RESOLVERS is None:
        _NODE_SCHEMA_RESOLVERS = _builtin_node_schema_resolvers()

    resolver = _NODE_SCHEMA_RESOLVERS.get(node.type)
    if resolver is None:
        return node
    return resolver(node)
