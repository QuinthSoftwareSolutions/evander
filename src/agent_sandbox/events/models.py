from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from typing import Annotated, Any, Callable, Literal, TypeAlias

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator
from typing_extensions import TypeAliasType

from agent_sandbox.models import DirectoryPath

TriggerType: TypeAlias = str
GateType: TypeAlias = str
EffectType: TypeAlias = str
QueryType: TypeAlias = str

EmailProvider = Literal["json_store", "gmail", "graph"]
PrFeedbackProvider = Literal["local", "github", "ado"]
NODE_OUTPUTS_CONTEXT_KEY = "__node_outputs"
RESERVED_OUTPUT_NAMES = frozenset({NODE_OUTPUTS_CONTEXT_KEY})

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


class WorkflowNodeEnvelope(BaseModel):
    id: str
    display_name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, dict[str, Any]] | None = None

    @model_validator(mode="before")
    @classmethod
    def collect_legacy_top_level_config(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        config = payload.get("config")
        if config is None:
            config = {}
        if not isinstance(config, dict):
            return value
        for key in list(payload.keys()):
            if key in {"id", "display_name", "type", "config", "outputs"}:
                continue
            config[key] = payload.pop(key)
        payload["config"] = config
        return payload

    @field_validator("id", "display_name", mode="before")
    @classmethod
    def normalize_non_empty_fields(cls, value: object, info) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @model_validator(mode="after")
    def validate_declared_output_names(self) -> "WorkflowNodeEnvelope":
        if self.outputs is not None:
            validate_output_mapping_keys(
                self.outputs,
                context=f"{self.type} {self.id} declared outputs",
            )
        return self

    def __getattr__(self, item: str) -> Any:
        config = object.__getattribute__(self, "config")
        if isinstance(config, dict) and item in config:
            return config[item]
        raise AttributeError(item)


class WorkflowNodeDefinition(WorkflowNodeEnvelope):
    """Backward-compatible alias for historical node model naming."""


@dataclass(frozen=True)
class NodeSchemaRegistration:
    node_type: str
    model_class: type[BaseModel]
    materialize: Callable[[WorkflowNodeEnvelope], BaseModel]


def _materialize_with_model(model_class: type[BaseModel], node: WorkflowNodeEnvelope) -> BaseModel:
    payload = {
        "id": node.id,
        "display_name": node.display_name,
        "type": node.type,
        "outputs": node.outputs,
        **dict(node.config),
    }
    return model_class.model_validate(payload)


_NODE_SCHEMA_REGISTRY: dict[str, NodeSchemaRegistration] = {}
_NODE_OUTPUT_SCHEMA_REGISTRY: dict[str, dict[str, dict[str, Any]]] = {}
_SCHEMA_BOOTSTRAP_ATTEMPTED = False


def register_node_schema(
    *,
    node_type: str,
    model_class: type[BaseModel],
    materialize: Callable[[WorkflowNodeEnvelope], BaseModel] | None = None,
) -> None:
    _NODE_SCHEMA_REGISTRY[node_type] = NodeSchemaRegistration(
        node_type=node_type,
        model_class=model_class,
        materialize=materialize or (lambda node: _materialize_with_model(model_class, node)),
    )


def get_node_schema_registry() -> Mapping[str, NodeSchemaRegistration]:
    return _NODE_SCHEMA_REGISTRY


def register_node_output_schema(*, node_type: str, outputs: Mapping[str, Mapping[str, Any]]) -> None:
    _NODE_OUTPUT_SCHEMA_REGISTRY[node_type] = {str(key): dict(value) for key, value in outputs.items()}


def get_node_output_schema_registry() -> Mapping[str, dict[str, dict[str, Any]]]:
    return _NODE_OUTPUT_SCHEMA_REGISTRY


def materialize_node_definition(node: WorkflowNodeEnvelope) -> BaseModel:
    _ensure_schema_bootstrap()
    registration = _NODE_SCHEMA_REGISTRY.get(node.type)
    if registration is None:
        return node
    return registration.materialize(node)


class TriggerDefinition(WorkflowNodeDefinition):
    type: TriggerType


class CronTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:cron"]
    expression: str = Field(min_length=1)


class TicketCreatedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:ticket:created"]
    directories: list[str]

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        return normalize_directory_paths(value)


class TicketChangedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:ticket:changed"]
    directories: list[str]

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        return normalize_directory_paths(value)


class ScriptRunRequestTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:script-run-request"]


class DiscordNewMessageTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:discord:new-message"]
    discord_bot_token_env: str = Field(min_length=1)
    channel_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)

    @field_validator("channel_ids", "user_ids", mode="before")
    @classmethod
    def normalize_optional_lists(cls, value: object) -> object:
        return normalize_string_list(value)


class TeamsContentFilterDefinition(BaseModel):
    contains: str = Field(min_length=1)
    case_sensitive: bool = False

    @field_validator("contains", mode="before")
    @classmethod
    def normalize_contains(cls, value: object) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("contains must be a non-empty string")
        return text


class TeamsMentionFilterDefinition(BaseModel):
    user_ids: list[str] = Field(min_length=1)

    @field_validator("user_ids", mode="before")
    @classmethod
    def normalize_user_ids(cls, value: object) -> object:
        return normalize_string_list(value)


class TeamsMessageFilterDefinition(BaseModel):
    content: TeamsContentFilterDefinition | None = None
    mentions: TeamsMentionFilterDefinition | None = None

    @model_validator(mode="after")
    def validate_at_least_one_filter(self) -> "TeamsMessageFilterDefinition":
        if self.content is None and self.mentions is None:
            raise ValueError("message_filter must define at least one of content or mentions")
        return self


class TeamsTargetDefinition(BaseModel):
    target: str = Field(min_length=1)
    message_filter: TeamsMessageFilterDefinition | None = None

    @field_validator("target", mode="before")
    @classmethod
    def normalize_target(cls, value: object) -> str:
        text = str(value).strip().strip("/")
        if not text:
            raise ValueError("target must be a non-empty string")
        return text


class TeamsNewMessageTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:teams:new-message"]
    targets: list[TeamsTargetDefinition] = Field(min_length=1)
    user_ids: list[str] = Field(default_factory=list)

    @field_validator("targets", mode="before")
    @classmethod
    def normalize_targets(cls, value: object) -> object:
        if isinstance(value, list):
            return value
        return value

    @field_validator("user_ids", mode="before")
    @classmethod
    def normalize_optional_lists(cls, value: object) -> object:
        return normalize_string_list(value)


class EmailNewMessageTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:email:new-message"]
    provider: EmailProvider
    account_identifier: str = Field(min_length=1)
    mailbox_ids: list[str]
    auth_secret_refs: dict[str, str] = Field(default_factory=dict)

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: object) -> str:
        return validate_email_provider(value)

    @field_validator("mailbox_ids", mode="before")
    @classmethod
    def normalize_mailbox_ids(cls, value: object) -> object:
        return normalize_string_list(value)


class PullRequestNewFeedbackTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:pull-request:new-feedback"]
    provider: PrFeedbackProvider
    organization_url: str | None = None
    project: str | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: object) -> str:
        return validate_pr_feedback_provider(value)

    @field_validator("organization_url", mode="before")
    @classmethod
    def normalize_organization_url(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip().rstrip("/")
        return text or None

    @field_validator("project", mode="before")
    @classmethod
    def normalize_project(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def validate_provider_specific_fields(self) -> "PullRequestNewFeedbackTriggerDefinition":
        if self.provider == "ado" and not self.organization_url:
            raise ValueError("organization_url is required when provider is 'ado'")
        return self


class ReviewNewRequestTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:review:new-request"]


class GitPushTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:git:push"]
    path_to_repo: str | None = None
    path_glob: str | None = None

    @field_validator("path_to_repo", "path_glob", mode="before")
    @classmethod
    def normalize_optional_path_field(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def validate_repo_selector(self) -> "GitPushTriggerDefinition":
        if not self.path_to_repo and not self.path_glob:
            raise ValueError("trigger:git:push requires path_to_repo or path_glob")
        if self.path_to_repo and self.path_glob:
            raise ValueError("trigger:git:push path_to_repo and path_glob are mutually exclusive")
        return self


class AgentRunCompletedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:agent:run:completed"]
    vm_user: str | None = "agent"


class AgentPromptNewTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:agent:prompt:new"]


TriggerDefinitionDiscriminated = WorkflowNodeEnvelope


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


GateDefinitionDiscriminated = (
    Annotated[
        GateFilterDefinition | GateMapDefinition | GateMergeDefinition | GateAwaitAgentDefinition | GateForDefinition,
        Field(discriminator="type"),
    ]
    | WorkflowNodeEnvelope
)


class EffectDefinition(WorkflowNodeDefinition):
    type: EffectType


class VmCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:create"]
    vm_name: ConfigValue | None = None
    session_id: ConfigValue | None = None
    labels: ConfigValue | None = None
    model: ConfigValue | None = None
    repo_source: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    ttl_hours: ConfigValue | None = None
    allowed_tools: list[str] | None = None
    allowed_script_names: list[str] | None = None
    discord_channels: list[str] | None = None
    email_mailboxes: list[str] | None = None
    email_folders: list[str] | None = None
    python: ConfigValue | None = None
    dotnet: ConfigValue | None = None
    nuget: ConfigValue | None = None
    mssql: ConfigValue | None = None
    mssql_init_root: ConfigValue | None = None
    playwright: ConfigValue | None = None
    codex_skills: ConfigValue | None = None
    frontend_deps: ConfigValue | None = None
    frontend_host_checkout: ConfigValue | None = None
    frontend_install_roots: ConfigValue | None = None
    directory_path: ConfigValue | None = None


class AgentPromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:prompt"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    prompt: ConfigValue
    model: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None
    output_mode: Literal["immediate", "on_completion"] = "immediate"


class AgentEnqueuePromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:enqueue-prompt"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    prompt: ConfigValue
    model: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None


class BashScriptEffectDefinition(EffectDefinition):
    type: Literal["effect:bash:script"]
    script_path: ConfigValue
    args: list[ConfigValue] = Field(default_factory=list)
    working_directory: ConfigValue | None = None
    environment: ConfigValue | None = None
    stdin: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    check: ConfigValue | None = True


class VmStartEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:start"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None


class VmStopEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:stop"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None


class VmExecEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:exec"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    command: ConfigValue
    working_directory: ConfigValue | None = None
    environment: ConfigValue | None = None
    stdin: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    check: ConfigValue | None = True


class VmCopyFilesItemDefinition(BaseModel):
    source_path: ConfigValue | None = None
    source_format: ConfigValue | None = None
    content: ConfigValue | None = None
    content_format: ConfigValue | None = None
    destination_path: ConfigValue
    mode: ConfigValue | None = None
    overwrite: ConfigValue | None = True

    @model_validator(mode="after")
    def validate_source_or_content(self) -> "VmCopyFilesItemDefinition":
        if self.source_path is None and self.content is None:
            raise ValueError("either source_path or content is required")
        if self.source_path is not None and self.content is not None:
            raise ValueError("source_path and content are mutually exclusive")
        return self


class VmCopyFilesEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:copy-files"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    files: list[VmCopyFilesItemDefinition]


class VmDeleteFilesEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:delete-files"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    paths: list[ConfigValue]
    recursive: ConfigValue | None = False
    missing_ok: ConfigValue | None = True


class IssueUpdateStateEffectDefinition(EffectDefinition):
    type: Literal["effect:issue:update-state"]
    issue_id: ConfigValue
    state: ConfigValue
    sync_proxy_cache: ConfigValue | None = True


class SessionUpdateMetadataEffectDefinition(EffectDefinition):
    type: Literal["effect:session:update-metadata"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    metadata: ConfigValue

    @field_validator("metadata")
    @classmethod
    def validate_metadata_is_object(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, (Mapping, str)):
            raise ValueError("effect:session:update-metadata metadata must be a JSON object")
        return value

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionUpdateMetadataEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:session:update-metadata requires target, session_id, or vm_name")
        return self


class GitPromoteAgentBranchEffectDefinition(EffectDefinition):
    type: Literal["effect:git:promote-agent-branch"]
    host_repo_path: ConfigValue
    bare_repo_path: ConfigValue | None = None
    bare_repo_name: ConfigValue | None = None
    agent_branch: ConfigValue
    review_branch: ConfigValue | None = None
    remote_name: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_bare_repo_selector(self) -> "GitPromoteAgentBranchEffectDefinition":
        if self.bare_repo_path is None and self.bare_repo_name is None:
            raise ValueError("effect:git:promote-agent-branch requires bare_repo_path or bare_repo_name")
        if self.bare_repo_path is not None and self.bare_repo_name is not None:
            raise ValueError("effect:git:promote-agent-branch bare_repo_path and bare_repo_name are mutually exclusive")
        return self


class PrCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:pr:create"]
    host_repo_path: ConfigValue
    local_branch: ConfigValue | None = None
    origin_branch: ConfigValue | None = None
    canonical_origin_url: ConfigValue | None = None
    provider: ConfigValue | None = None
    project: ConfigValue | None = None
    base_branch: ConfigValue | None = None
    session_id: ConfigValue | None = None
    work_item_id: ConfigValue | None = None
    title: ConfigValue | None = None
    body: ConfigValue | None = None
    body_file: ConfigValue | None = None
    draft: ConfigValue | None = None
    fill: ConfigValue | None = True


EffectDefinitionDiscriminated = WorkflowNodeEnvelope


class EdgeDefinition(BaseModel):
    source_id: str
    target_id: str
    source_port: str | None = None
    allow_cycle: bool = False

    @field_validator("source_id", "target_id", mode="before")
    @classmethod
    def normalize_node_id(cls, value: object, info) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("source_port", mode="before")
    @classmethod
    def normalize_source_port(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class QueryDefinition(WorkflowNodeDefinition):
    type: QueryType


class VmRunStateQueryDefinition(QueryDefinition):
    type: Literal["query:vm:run-state"]
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None


class GitPrNoteQueryDefinition(QueryDefinition):
    type: Literal["query:git:pr-note"]
    host_repo_path: ConfigValue
    local_branch: ConfigValue
    remote_name: ConfigValue | None = None
    notes_ref: ConfigValue | None = None


class SessionContextQueryDefinition(QueryDefinition):
    type: Literal["query:session-context"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionContextQueryDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("query:session-context requires target, session_id, or vm_name")
        return self


class IssueGetQueryDefinition(QueryDefinition):
    type: Literal["query:issue:get"]
    issue_id: ConfigValue


QueryDefinitionDiscriminated = WorkflowNodeEnvelope


WorkflowNodeType = TriggerType | GateType | EffectType | QueryType

class TriggerEvent(BaseModel):
    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: WorkflowNodeType | str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, min_length=1)


def validate_output_mapping_keys(payload: Mapping[str, Any], *, context: str) -> None:
    reserved = tuple(sorted(name for name in payload if name in RESERVED_OUTPUT_NAMES))
    if not reserved:
        return
    reserved_list = ", ".join(reserved)
    raise ValueError(f"{context} must not use reserved output name(s): {reserved_list}")


def validate_filter_when_expression(value: Any, *, context: str = "gate:filter when") -> None:
    _validate_filter_when_node(value, path=context)


def _validate_filter_when_node(value: Any, *, path: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a JSON object")

    logical_keys = tuple(key for key in ("all", "any", "not") if key in value)
    operator_keys = tuple(
        key
        for key in ("equals", "not_equals", "in", "not_in", "is_null", "is_not_null", "starts_with", "ends_with", "contains")
        if key in value
    )

    if logical_keys:
        if len(logical_keys) != 1 or len(value) != 1:
            raise ValueError(f"{path} must contain exactly one logical operator")
        logical_key = logical_keys[0]
        nested = value[logical_key]
        if logical_key == "not":
            _validate_filter_when_node(nested, path=f"{path}.not")
            return
        if not isinstance(nested, list) or not nested:
            raise ValueError(f"{path}.{logical_key} must be a non-empty array")
        for index, item in enumerate(nested):
            _validate_filter_when_node(item, path=f"{path}.{logical_key}[{index}]")
        return

    if not operator_keys:
        raise ValueError(
            f"{path} must contain a logical operator or one of: contains, ends_with, equals, in, is_not_null, is_null, not_equals, not_in, starts_with"
        )
    if len(operator_keys) != 1:
        raise ValueError(f"{path} must contain exactly one comparison operator")
    if "left" not in value:
        raise ValueError(f"{path}.left is required")
    if len(value) != 2:
        raise ValueError(f"{path} must contain only left and its comparison operator")

    operator = operator_keys[0]
    operator_value = value[operator]
    if operator in {"is_null", "is_not_null"}:
        if operator_value is not True:
            raise ValueError(f"{path}.{operator} must be true")
        return
    if operator in {"starts_with", "ends_with", "contains"}:
        if not isinstance(operator_value, str):
            raise ValueError(f"{path}.{operator} must be a string")



def effective_node_output_schema(node: Any) -> dict[str, dict[str, Any]]:
    _ensure_schema_bootstrap()
    if isinstance(node, Mapping):
        node_type = str(node.get("type") or "").strip()
        raw_outputs = node.get("outputs")
        template = node.get("template")
    else:
        node_type = str(getattr(node, "type", "") or "").strip()
        raw_outputs = getattr(node, "outputs", None)
        template = getattr(node, "template", None)

    schema: dict[str, dict[str, Any]] = {}
    static_schema = _NODE_OUTPUT_SCHEMA_REGISTRY.get(node_type, {})
    if static_schema:
        schema.update({key: dict(value) for key, value in static_schema.items()})

    if node_type == "gate:map" and isinstance(template, Mapping):
        for key, value in template.items():
            name = str(key).strip()
            if not name:
                continue
            schema[name] = _infer_schema_from_template_value(value)
        return schema

    if isinstance(raw_outputs, Mapping):
        schema.update({str(key): dict(value) for key, value in raw_outputs.items() if isinstance(value, Mapping)})
    return schema


def _ensure_schema_bootstrap() -> None:
    global _SCHEMA_BOOTSTRAP_ATTEMPTED
    if _SCHEMA_BOOTSTRAP_ATTEMPTED:
        return
    _SCHEMA_BOOTSTRAP_ATTEMPTED = True
    try:
        from agent_sandbox.schema_bootstrap import bootstrap_default_node_schemas
    except Exception:
        return
    bootstrap_default_node_schemas()


def _infer_schema_from_template_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, Mapping):
        return {"type": "object"}
    if isinstance(value, list):
        return {"type": "array"}
    return {"type": "object"}


def normalize_directory_paths(value: object) -> object:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return value

    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        normalized.append(DirectoryPath(segments=text).as_posix())
    return normalized


def normalize_string_list(value: object) -> object:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return value

    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def validate_email_provider(value: object) -> str:
    normalized = str(value).strip().lower()
    allowed = {"json_store", "gmail", "graph"}
    if normalized not in allowed:
        ordered = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported email provider: {normalized!r}. Allowed providers: {ordered}")
    return normalized


def validate_pr_feedback_provider(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"local", "github", "ado"}:
        raise ValueError(
            f"Unsupported pull-request feedback provider: {normalized!r}. Allowed providers: ado, github, local"
        )
    return normalized
