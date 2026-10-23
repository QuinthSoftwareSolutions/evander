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

from evander.classification.switch import ClassifierSwitchConfig
from evander.gate_runtime import GateRuntimeError, parse_iso8601_duration
from evander.workflow.contracts import (
    materialize_node_model as _materialize_with_model,
    EdgeDefinition as EdgeDefinition,
    FOR_EXECUTION_IDS_CONTEXT_KEY as FOR_EXECUTION_IDS_CONTEXT_KEY,
    NODE_OUTPUTS_CONTEXT_KEY as NODE_OUTPUTS_CONTEXT_KEY,
    WorkflowNodeDefinition,
    WorkflowNodeEnvelope,
    effective_node_output_schema,
    normalize_directory_paths as normalize_directory_paths,
    normalize_string_list as normalize_string_list,
    validate_filter_when_expression,
    validate_output_mapping_keys as validate_output_mapping_keys,
)
from evander.workflow.lifecycle_ports import AGENT_PROMPT_ERROR_PORT

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


class GateWhileDefinition(GateDefinition):
    type: Literal["gate:while"]
    when: ConfigValue
    max_iterations: ConfigValue

    @field_validator("when")
    @classmethod
    def validate_when_expression(cls, value: ConfigValue) -> ConfigValue:
        validate_filter_when_expression(value, context="gate:while when")
        return value

    @field_validator("max_iterations")
    @classmethod
    def validate_max_iterations(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, bool):
            raise ValueError("gate:while max_iterations must be a positive integer")
        if isinstance(value, int):
            if value < 1:
                raise ValueError("gate:while max_iterations must be a positive integer")
            return value
        if isinstance(value, str):
            return value
        raise ValueError("gate:while max_iterations must be a positive integer or binding")

    @model_validator(mode="after")
    def validate_default_outputs(self) -> "GateWhileDefinition":
        if self.outputs is None:
            self.outputs = effective_node_output_schema(self)
        return self


class GateClassifierSwitchDefinition(GateDefinition, ClassifierSwitchConfig):
    type: Literal["gate:classifier:switch"]


class GateSwitchCaseDefinition(BaseModel):
    port: str
    label: str | None = None
    when: ConfigValue | None = None
    match: ConfigValue | None = None

    @field_validator("port", mode="before")
    @classmethod
    def validate_port(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("gate:switch case port must be a non-empty string")
        text = str(value).strip()
        if not text:
            raise ValueError("gate:switch case port must be a non-empty string")
        if text == AGENT_PROMPT_ERROR_PORT:
            raise ValueError(
                "gate:switch case port 'error' is reserved for lifecycle failures"
            )
        return text

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("match")
    @classmethod
    def validate_match_is_object(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("gate:switch case match must be a JSON object")
        return value

    @field_validator("when")
    @classmethod
    def validate_when_expression(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        validate_filter_when_expression(value, context="gate:switch case when")
        return value

    @model_validator(mode="after")
    def validate_exactly_one_predicate(self) -> "GateSwitchCaseDefinition":
        if self.when is None and self.match is None:
            raise ValueError("gate:switch case requires exactly one of when or match")
        if self.when is not None and self.match is not None:
            raise ValueError("gate:switch case when and match are mutually exclusive")
        return self


class GateSwitchDefinition(GateDefinition):
    type: Literal["gate:switch"]
    cases: list[GateSwitchCaseDefinition]
    default_port: str | None = None

    @field_validator("default_port", mode="before")
    @classmethod
    def validate_default_port(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("gate:switch default_port must be a non-empty string")
        text = str(value).strip()
        if not text:
            raise ValueError("gate:switch default_port must be a non-empty string")
        if text == AGENT_PROMPT_ERROR_PORT:
            raise ValueError(
                "gate:switch default_port 'error' is reserved for lifecycle failures"
            )
        return text

    @model_validator(mode="after")
    def validate_cases(self) -> "GateSwitchDefinition":
        if not self.cases:
            raise ValueError("gate:switch requires at least one case")
        return self


class GateValidateCheckDefinition(BaseModel):
    code: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    when: ConfigValue

    @field_validator("when")
    @classmethod
    def validate_when_expression(cls, value: ConfigValue) -> ConfigValue:
        validate_filter_when_expression(value, context="gate:validate check when")
        return value


class GateValidateDefinition(GateDefinition):
    type: Literal["gate:validate"]
    checks: list[GateValidateCheckDefinition]

    @model_validator(mode="after")
    def validate_checks(self) -> "GateValidateDefinition":
        if not self.checks:
            raise ValueError("gate:validate requires at least one check")
        codes = [check.code for check in self.checks]
        duplicate_codes = sorted({code for code in codes if codes.count(code) > 1})
        if duplicate_codes:
            raise ValueError(
                "gate:validate check codes must be unique: " + ", ".join(duplicate_codes)
            )
        return self


class GateEventValidateDefinition(GateDefinition):
    type: Literal["gate:event:validate"]
    event_type: ConfigValue
    payload: ConfigValue


class GateMapDefinition(GateDefinition):
    type: Literal["gate:map"]
    template: ConfigValue

    @field_validator("template")
    @classmethod
    def validate_template_is_object(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, dict):
            raise ValueError("gate:map template must be a JSON object")
        return value


class GateAggregateMetricDefinition(BaseModel):
    operation: Literal["sum", "min", "max", "avg"]
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = str(value or "").strip()
        if not path or any(not segment for segment in path.split(".")):
            raise ValueError("gate:aggregate metric path must be a non-empty dotted object path")
        return path


class GateAggregateDefinition(GateDefinition):
    type: Literal["gate:aggregate"]
    items: ConfigValue
    metrics: dict[str, GateAggregateMetricDefinition]

    @field_validator("metrics")
    @classmethod
    def validate_metrics(
        cls, value: dict[str, GateAggregateMetricDefinition]
    ) -> dict[str, GateAggregateMetricDefinition]:
        if not value:
            raise ValueError("gate:aggregate metrics must be a non-empty object")
        validate_output_mapping_keys(value, context="gate:aggregate metrics")
        for raw_name in value:
            name = str(raw_name).strip()
            if not name:
                raise ValueError("gate:aggregate metric names must be non-empty strings")
            if name == "count":
                raise ValueError("gate:aggregate metric name 'count' is reserved")
        return value

    @model_validator(mode="after")
    def validate_default_outputs(self) -> "GateAggregateDefinition":
        if self.outputs is None:
            self.outputs = effective_node_output_schema(self)
        return self


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


class GateBufferDefinition(GateDefinition):
    type: Literal["gate:buffer"]
    key: ConfigValue
    count: ConfigValue
    timeout: ConfigValue | None = None

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("gate:buffer key must be a non-empty string or binding template")
        return value

    @field_validator("count")
    @classmethod
    def validate_count(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, bool):
            raise ValueError("gate:buffer count must be a positive integer or binding")
        if isinstance(value, int):
            if value < 1:
                raise ValueError("gate:buffer count must be a positive integer or binding")
            return value
        if isinstance(value, str) and value.strip():
            return value
        raise ValueError("gate:buffer count must be a positive integer or binding")

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        if not isinstance(value, str):
            raise ValueError("gate:buffer timeout must be an ISO-8601 duration string or binding")
        if "${{" in value:
            return value
        try:
            parse_iso8601_duration(value, field_name="timeout", gate_type="gate:buffer")
        except GateRuntimeError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def validate_default_outputs(self) -> "GateBufferDefinition":
        if self.outputs is None:
            self.outputs = effective_node_output_schema(self)
        return self


class GateThrottleDefinition(GateDefinition):
    type: Literal["gate:throttle"]
    key: ConfigValue
    window: str
    limit: ConfigValue

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("gate:throttle key must be a non-empty string or binding template")
        return value

    @field_validator("window")
    @classmethod
    def validate_window(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or "${{" in value:
            raise ValueError("gate:throttle window must be a non-empty ISO-8601 duration")
        try:
            window = parse_iso8601_duration(value, field_name="window", gate_type="gate:throttle")
        except GateRuntimeError as exc:
            raise ValueError(str(exc)) from exc
        if window.total_seconds() <= 0:
            raise ValueError("gate:throttle window must be greater than zero")
        return value

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, bool):
            raise ValueError("gate:throttle limit must be a positive integer or binding")
        if isinstance(value, int):
            if value < 1:
                raise ValueError("gate:throttle limit must be a positive integer or binding")
            return value
        if isinstance(value, str) and value.strip():
            return value
        raise ValueError("gate:throttle limit must be a positive integer or binding")


class GateCounterDefinition(GateDefinition):
    type: Literal["gate:counter"]
    key: ConfigValue
    window: str

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("gate:counter key must be a non-empty string or binding template")
        return value

    @field_validator("window")
    @classmethod
    def validate_window(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or "${{" in value:
            raise ValueError("gate:counter window must be a non-empty ISO-8601 duration")
        try:
            window = parse_iso8601_duration(value, field_name="window", gate_type="gate:counter")
        except GateRuntimeError as exc:
            raise ValueError(str(exc)) from exc
        if window.total_seconds() <= 0:
            raise ValueError("gate:counter window must be greater than zero")
        return value


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
    WorkflowNodeEnvelope,
    AfterValidator(validate_provider_node_definition),
]
EffectDefinitionDiscriminated = Annotated[
    WorkflowNodeEnvelope,
    AfterValidator(validate_provider_node_definition),
]
QueryDefinitionDiscriminated = Annotated[
    WorkflowNodeEnvelope,
    AfterValidator(validate_provider_node_definition),
]
GateDefinitionDiscriminated = Annotated[
    WorkflowNodeEnvelope,
    AfterValidator(validate_provider_node_definition),
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


def _builtin_node_schema_resolvers() -> dict[str, Any]:

    from evander.events import provider_models

    models_by_type: dict[str, type[BaseModel]] = {
        "query:ado:builds": provider_models.AdoBuildsQueryDefinition,
        "query:text:regex": provider_models.TextRegexQueryDefinition,
        "effect:ado:build-wait": provider_models.AdoBuildWaitEffectDefinition,
        "effect:agent:install-skills": provider_models.AgentInstallSkillsEffectDefinition,
        "trigger:event": provider_models.EventTriggerDefinition,
        "trigger:agent:artifact-staged": provider_models.AgentArtifactStagedTriggerDefinition,
        "trigger:http:webhook": provider_models.WebhookTriggerDefinition,
        "trigger:cron": provider_models.CronTriggerDefinition,
        "trigger:ticket:created": provider_models.TicketCreatedTriggerDefinition,
        "trigger:ticket:changed": provider_models.TicketChangedTriggerDefinition,
        "trigger:issue:comment-created": provider_models.IssueCommentCreatedTriggerDefinition,
        "trigger:ado:work-item-matching": provider_models.AdoWorkItemMatchingTriggerDefinition,
        "trigger:ado:work-item-comment-created": (
            provider_models.AdoWorkItemCommentCreatedTriggerDefinition
        ),
        "trigger:remote-issue-matching": provider_models.RemoteIssueMatchingTriggerDefinition,
        "trigger:remote-issue-comment-created": (
            provider_models.RemoteIssueCommentCreatedTriggerDefinition
        ),
        "trigger:script-run-request": provider_models.ScriptRunRequestTriggerDefinition,
        "trigger:communication:new-message": provider_models.CommunicationNewMessageTriggerDefinition,
        "trigger:discord:new-message": provider_models.DiscordNewMessageTriggerDefinition,
        "trigger:teams:new-message": provider_models.TeamsNewMessageTriggerDefinition,
        "trigger:teams:direct-message": provider_models.TeamsDirectMessageTriggerDefinition,
        "trigger:teams-meeting:new-transcript": (
            provider_models.TeamsMeetingNewTranscriptTriggerDefinition
        ),
        "trigger:email:new-message": provider_models.EmailNewMessageTriggerDefinition,
        "trigger:pull-request:new-feedback": provider_models.PullRequestNewFeedbackTriggerDefinition,
        "trigger:pull-request:completed": provider_models.PullRequestCompletedTriggerDefinition,
        "trigger:review:new-request": provider_models.ReviewNewRequestTriggerDefinition,
        "trigger:approval:pending-created": provider_models.ApprovalPendingCreatedTriggerDefinition,
        "trigger:git:push": provider_models.GitPushTriggerDefinition,
        "trigger:host-directory:file-added": (
            provider_models.HostDirectoryFileAddedTriggerDefinition
        ),
        "trigger:ci:run:completed": provider_models.CiRunCompletedTriggerDefinition,
        "trigger:agent:run:completed": provider_models.AgentRunCompletedTriggerDefinition,
        "trigger:agent:prompt:new": provider_models.AgentPromptNewTriggerDefinition,
        "gate:filter": GateFilterDefinition,
        "gate:switch": GateSwitchDefinition,
        "gate:classifier:switch": GateClassifierSwitchDefinition,
        "gate:validate": GateValidateDefinition,
        "gate:event:validate": GateEventValidateDefinition,
        "gate:map": GateMapDefinition,
        "gate:aggregate": GateAggregateDefinition,
        "gate:deserialize": GateDeserializeDefinition,
        "gate:serialize": GateSerializeDefinition,
        "gate:merge": GateMergeDefinition,
        "gate:debounce": GateDebounceDefinition,
        "gate:buffer": GateBufferDefinition,
        "gate:counter": GateCounterDefinition,
        "gate:dedupe": GateDedupeDefinition,
        "gate:await-agent": GateAwaitAgentDefinition,
        "gate:for": GateForDefinition,
        "gate:while": GateWhileDefinition,
        "effect:event:emit": provider_models.EventEmitEffectDefinition,
        "effect:artifact-space:create": provider_models.ArtifactSpaceCreateEffectDefinition,
        "effect:artifact-space:publish-version": (
            provider_models.ArtifactSpacePublishVersionEffectDefinition
        ),
        "effect:security:scan-repo": provider_models.SecurityScanEffectDefinition,
        "effect:vm:create": provider_models.VmCreateEffectDefinition,
        "effect:vm:clone-repo": provider_models.VmCloneRepoEffectDefinition,
        "effect:vm:bootstrap-python": provider_models.VmBootstrapPythonEffectDefinition,
        "effect:vm:bootstrap-frontend": provider_models.VmBootstrapFrontendEffectDefinition,
        "effect:vm:start": provider_models.VmStartEffectDefinition,
        "effect:vm:stop": provider_models.VmStopEffectDefinition,
        "effect:agent:create": provider_models.AgentCreateEffectDefinition,
        "effect:agent:destroy": provider_models.AgentDestroyEffectDefinition,
        "effect:agent:clone-repo": provider_models.AgentCloneRepoEffectDefinition,
        "effect:agent:prepare-reference-repositories": (
            provider_models.AgentPrepareReferenceRepositoriesEffectDefinition
        ),
        "effect:agent:grant-repo-access": provider_models.AgentGrantRepoAccessEffectDefinition,
        "effect:agent:revoke-repo-access": provider_models.AgentRevokeRepoAccessEffectDefinition,
        "effect:agent:bootstrap-frontend": provider_models.AgentBootstrapFrontendEffectDefinition,
        "effect:agent:copy-files": provider_models.AgentCopyFilesEffectDefinition,
        "effect:artifact:stage": provider_models.ArtifactStageEffectDefinition,
        "gate:throttle": GateThrottleDefinition,
        "effect:artifact:publish-artifacts": (
            provider_models.ArtifactPublishArtifactsEffectDefinition
        ),
        "effect:artifact:import-published": (
            provider_models.ArtifactImportPublishedEffectDefinition
        ),
        "effect:agent:prompt": provider_models.AgentPromptEffectDefinition,
        "effect:agent:enqueue-prompt": provider_models.AgentEnqueuePromptEffectDefinition,
        "effect:chat-assistant:ensure-session": (
            provider_models.ChatAssistantEnsureSessionEffectDefinition
        ),
        "effect:agent:code-consultation": (provider_models.CodeConsultationEffectDefinition),
        "effect:agent:exec": provider_models.AgentExecEffectDefinition,
        "effect:semantic:ensure-collection": (
            provider_models.SemanticEnsureCollectionEffectDefinition
        ),
        "effect:semantic:upsert": provider_models.SemanticUpsertEffectDefinition,
        "effect:semantic:delete": provider_models.SemanticDeleteEffectDefinition,
        "effect:state:update": provider_models.StateUpdateEffectDefinition,
        "effect:http:request": provider_models.HttpRequestEffectDefinition,
        "effect:runtime:diagnostics-snapshot": (
            provider_models.RuntimeDiagnosticsSnapshotEffectDefinition
        ),
        "effect:vm:exec": provider_models.VmExecEffectDefinition,
        "effect:vm:copy-files": provider_models.VmCopyFilesEffectDefinition,
        "effect:vm:delete-files": provider_models.VmDeleteFilesEffectDefinition,
        "effect:issue:update-state": provider_models.IssueUpdateStateEffectDefinition,
        "effect:issue:update-metadata": provider_models.IssueUpdateMetadataEffectDefinition,
        "effect:issue:upsert-ado-proxy": provider_models.IssueUpsertAdoProxyEffectDefinition,
        "effect:ado:pipeline-run": provider_models.AdoPipelineRunEffectDefinition,
        "effect:ado:post-work-item-comment": (
            provider_models.AdoPostWorkItemCommentEffectDefinition
        ),
        "effect:ado:add-work-item-tag": provider_models.AdoAddWorkItemTagEffectDefinition,
        "effect:ado:remove-work-item-tag": provider_models.AdoRemoveWorkItemTagEffectDefinition,
        "effect:ado:update-work-item": provider_models.AdoUpdateWorkItemEffectDefinition,
        "effect:pull-request:add-tag": provider_models.PullRequestAddTagEffectDefinition,
        "effect:pull-request:reply": provider_models.PullRequestReplyEffectDefinition,
        "effect:pull-request:remove-tag": provider_models.PullRequestRemoveTagEffectDefinition,
        "effect:pull-request:update": provider_models.PullRequestUpdateEffectDefinition,
        "effect:communication:send-message": (
            provider_models.CommunicationSendMessageEffectDefinition
        ),
        "effect:notification:send": provider_models.NotificationSendEffectDefinition,
        "effect:session:approve-mcp-tools-for-run": (
            provider_models.SessionApproveMcpToolsForRunEffectDefinition
        ),
        "effect:session:revoke-mcp-tool-approval-lease": (
            provider_models.SessionRevokeMcpToolApprovalLeaseEffectDefinition
        ),
        "effect:session:update-metadata": provider_models.SessionUpdateMetadataEffectDefinition,
        "effect:session:reconcile-policy": provider_models.SessionReconcilePolicyEffectDefinition,
        "effect:session:set-reasoning-effort": provider_models.SessionSetReasoningEffortEffectDefinition,
        "effect:website-session:ensure-profile": provider_models.WebsiteSessionEnsureProfileEffectDefinition,
        "effect:session:set-image-generation-enabled": (
            provider_models.SessionSetImageGenerationEnabledEffectDefinition
        ),
        "effect:session:renew-if-expired": (provider_models.SessionRenewIfExpiredEffectDefinition),
        "effect:git:ensure-managed-repo": provider_models.GitEnsureManagedRepoEffectDefinition,
        "effect:git:promote-agent-branch": provider_models.GitPromoteAgentBranchEffectDefinition,
        "effect:git:push-branch": provider_models.GitPushBranchEffectDefinition,
        "effect:pr:create": provider_models.PrCreateEffectDefinition,
        "effect:event:schedule": provider_models.EventScheduleEffectDefinition,
        "query:artifact-space:list": provider_models.ArtifactSpaceListQueryDefinition,
        "query:artifact-space:latest": provider_models.ArtifactSpaceLatestQueryDefinition,
        "query:vm:run-state": provider_models.VmRunStateQueryDefinition,
        "query:git:merge-base": provider_models.GitMergeBaseQueryDefinition,
        "query:git:pr-note": provider_models.GitPrNoteQueryDefinition,
        "query:session-context": provider_models.SessionContextQueryDefinition,
        "query:website-session:resolve-profile": provider_models.WebsiteSessionResolveProfileQueryDefinition,
        "query:session:list": provider_models.SessionListQueryDefinition,
        "query:model-usage:list": provider_models.ModelUsageListQueryDefinition,
        "query:codex:subscription-quota": provider_models.CodexSubscriptionQuotaQueryDefinition,
        "query:agent:artifact-text": provider_models.AgentArtifactTextQueryDefinition,
        "query:issue:get": provider_models.IssueGetQueryDefinition,
        "query:issue:list": provider_models.IssueListQueryDefinition,
        "query:ado:work-items": provider_models.AdoWorkItemsQueryDefinition,
        "query:remote-issues": provider_models.RemoteIssuesQueryDefinition,
        "query:pull-request:list": provider_models.PullRequestListQueryDefinition,
        "query:pull-request:feedback": provider_models.PullRequestFeedbackQueryDefinition,
        "query:communication:messages": provider_models.CommunicationMessagesQueryDefinition,
        "query:pull-request:get": provider_models.PullRequestGetQueryDefinition,
        "query:pull-request:work-items": provider_models.PullRequestWorkItemsQueryDefinition,
        "query:state:get": provider_models.StateGetQueryDefinition,
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
