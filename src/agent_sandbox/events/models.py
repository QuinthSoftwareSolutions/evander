from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator
from typing_extensions import TypeAliasType

from agent_sandbox.models import DirectoryPath

TriggerType = Literal[
    "trigger:cron",
    "trigger:ticket:created",
    "trigger:ticket:changed",
    "trigger:script-run-request",
    "trigger:discord:new-message",
    "trigger:teams:new-message",
    "trigger:email:new-message",
    "trigger:pull-request:new-feedback",
    "trigger:review:new-request",
    "trigger:git:push",
    "trigger:agent:prompt:new",
    "trigger:agent:run:completed",
]

GateType = Literal["gate:filter", "gate:map", "gate:merge", "gate:await-agent", "gate:for"]
EffectType = Literal[
    "effect:vm:create",
    "effect:vm:start",
    "effect:vm:stop",
    "effect:agent:prompt",
    "effect:agent:enqueue-prompt",
    "effect:bash:script",
    "effect:vm:exec",
    "effect:vm:copy-files",
    "effect:vm:delete-files",
    "effect:issue:update-state",
    "effect:session:update-metadata",
    "effect:git:promote-agent-branch",
    "effect:pr:create",
]
QueryType = Literal[
    "query:vm:run-state",
    "query:git:pr-note",
    "query:session-context",
    "query:issue:get",
]

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


class WorkflowNodeDefinition(BaseModel):
    id: str
    display_name: str
    type: str
    outputs: dict[str, dict[str, Any]] | None = None

    @field_validator("id", "display_name", mode="before")
    @classmethod
    def normalize_non_empty_fields(cls, value: object, info) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @model_validator(mode="after")
    def validate_declared_output_names(self) -> "WorkflowNodeDefinition":
        if self.outputs is not None:
            validate_output_mapping_keys(
                self.outputs,
                context=f"{self.type} {self.id} declared outputs",
            )
        return self


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


TriggerDefinitionDiscriminated = Annotated[
    CronTriggerDefinition
    | TicketCreatedTriggerDefinition
    | TicketChangedTriggerDefinition
    | ScriptRunRequestTriggerDefinition
    | DiscordNewMessageTriggerDefinition
    | TeamsNewMessageTriggerDefinition
    | EmailNewMessageTriggerDefinition
    | PullRequestNewFeedbackTriggerDefinition
    | ReviewNewRequestTriggerDefinition
    | GitPushTriggerDefinition
    | AgentPromptNewTriggerDefinition
    | AgentRunCompletedTriggerDefinition,
    Field(discriminator="type"),
]


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


GateDefinitionDiscriminated = Annotated[
    GateFilterDefinition | GateMapDefinition | GateMergeDefinition | GateAwaitAgentDefinition | GateForDefinition,
    Field(discriminator="type"),
]


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


EffectDefinitionDiscriminated = Annotated[
    VmCreateEffectDefinition
    | VmStartEffectDefinition
    | VmStopEffectDefinition
    | AgentPromptEffectDefinition
    | AgentEnqueuePromptEffectDefinition
    | BashScriptEffectDefinition
    | VmExecEffectDefinition
    | VmCopyFilesEffectDefinition
    | VmDeleteFilesEffectDefinition
    | IssueUpdateStateEffectDefinition
    | SessionUpdateMetadataEffectDefinition
    | GitPromoteAgentBranchEffectDefinition
    | PrCreateEffectDefinition,
    Field(discriminator="type"),
]


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


QueryDefinitionDiscriminated = Annotated[
    VmRunStateQueryDefinition | GitPrNoteQueryDefinition | SessionContextQueryDefinition | IssueGetQueryDefinition,
    Field(discriminator="type"),
]


WorkflowNodeType = TriggerType | GateType | EffectType | QueryType


class TriggerEvent(BaseModel):
    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: WorkflowNodeType | str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, min_length=1)


_STATIC_OUTPUT_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "trigger:ticket:created": {
        "ticket": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "issue_number": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "description": {"type": "string"},
                "reason": {"type": "string"},
                "rejection_reason": {"type": "string"},
                "yaml_path": {"type": "string"},
                "directory_path": {"type": "string"},
                "repo_source": {"type": "string"},
                "source_type": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "state": {"type": "string"},
                "url": {"type": "string"},
                "comments": {"type": "array"},
                "source": {"type": "string"},
                "metadata": {"type": "object"},
                "created_at": {"type": "string"},
                "updated_at": {"type": "string"},
            },
        }
    },
    "trigger:ticket:changed": {
        "ticket": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "issue_number": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "description": {"type": "string"},
                "reason": {"type": "string"},
                "rejection_reason": {"type": "string"},
                "yaml_path": {"type": "string"},
                "directory_path": {"type": "string"},
                "repo_source": {"type": "string"},
                "source_type": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "state": {"type": "string"},
                "url": {"type": "string"},
                "comments": {"type": "array"},
                "source": {"type": "string"},
                "metadata": {"type": "object"},
                "created_at": {"type": "string"},
                "updated_at": {"type": "string"},
            },
        }
    },
    "trigger:script-run-request": {
        "request_id": {"type": "string"},
        "session_id": {"type": "string"},
        "script_name": {"type": "string"},
        "parameters": {"type": "array", "items": {"type": "string"}},
    },
    "trigger:discord:new-message": {
        "message": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "channel_id": {"type": "string"},
                "author_id": {"type": "string"},
                "author_name": {"type": "string"},
                "content": {"type": "string"},
                "created_at": {"type": "string"},
                "in_reply_to": {"type": "string"},
                "raw": {"type": "object"},
            },
        }
    },
    "trigger:teams:new-message": {},
    "trigger:email:new-message": {
        "message": {
            "type": "object",
            "properties": {
                "mailbox_id": {"type": "string"},
                "provider": {"type": "string"},
                "folder": {"type": "string"},
                "message_id": {"type": "string"},
                "thread_id": {"type": "string"},
                "sender": {"type": "string"},
                "recipients": {"type": "array", "items": {"type": "string"}},
                "received_at": {"type": "string"},
                "unread": {"type": "boolean"},
                "body_snippet": {"type": "string"},
                "metadata": {"type": "object"},
            },
        }
    },
    "trigger:pull-request:new-feedback": {
        "session_id": {"type": "string"},
        "pr": {
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "repo": {"type": "string"},
                "pr_id": {"type": "string"},
            },
        },
        "feedback": {
            "type": "object",
            "properties": {
                "feedback_id": {"type": "string"},
                "provider": {"type": "string"},
                "repo": {"type": "string"},
                "pr_id": {"type": "string"},
                "thread_id": {"type": "string"},
                "comment_id": {"type": "string"},
                "author": {"type": "string"},
                "body": {"type": "string"},
                "file_path": {"type": "string"},
                "line": {"type": "integer"},
                "created_at": {"type": "string"},
                "in_reply_to": {"type": "string"},
                "resolved": {"type": "boolean"},
            },
        },
    },
    "trigger:review:new-request": {
        "review_request": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "requested_at": {"type": "string"},
                "requested_by": {"type": "string"},
                "request_channel": {"type": "string"},
                "provider": {"type": "string"},
                "repo": {"type": "string"},
                "pr_id": {"type": "string"},
                "canonical_url": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "base_branch": {"type": "string"},
                "head_branch": {"type": "string"},
                "base_commit": {"type": "string"},
                "head_commit": {"type": "string"},
                "review_type": {"type": "string"},
                "instructions": {"type": "string"},
                "directory_path": {"type": "string"},
                "model": {"type": "string"},
                "labels": {"type": "object"},
                "metadata": {"type": "object"},
                "status": {"type": "string"},
                "updated_at": {"type": "string"},
                "lease_owner": {"type": "string"},
                "lease_expires_at": {"type": "string"},
                "dispatch_count": {"type": "integer"},
            },
        }
    },
    "trigger:git:push": {
        "branch_name": {"type": "string"},
        "commit_hash": {"type": "string"},
        "old_commit_hash": {"type": "string"},
        "ref_name": {"type": "string"},
        "repo_path": {"type": "string"},
        "repo_name": {"type": "string"},
        "session_id": {"type": "string"},
        "review_branch": {"type": "string"},
        "trusted_repo_path": {"type": "string"},
        "base_branch": {"type": "string"},
        "canonical_origin_url": {"type": "string"},
    },
    "trigger:agent:prompt:new": {
        "session_id": {"type": "string"},
        "agent_prompt": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "sender_session_id": {"type": "string"},
                "target_session_id": {"type": "string"},
                "prompt": {"type": "string"},
                "metadata": {"type": "object"},
                "status": {"type": "string"},
                "created_at": {"type": "string"},
            },
        },
    },
    "trigger:agent:run:completed": {
        "vm": {
            "type": "object",
            "properties": {
                "vm_name": {"type": "string"},
                "issue_number": {"type": "string"},
                "session_ids": {"type": "array", "items": {"type": "string"}},
                "labels": {"type": "object"},
                "directory_paths": {"type": "array", "items": {"type": "string"}},
                "libvirt_state": {"type": "string"},
                "ip_address": {"type": "string"},
                "agent_state": {"type": "string"},
                "agent_repo_names": {"type": "array", "items": {"type": "string"}},
                "last_state_update_at": {"type": "string"},
            },
        },
        "vm_name": {"type": "string"},
        "session_ids": {"type": "array"},
        "labels": {"type": "object"},
        "directory_paths": {"type": "array"},
        "agent_repo_names": {"type": "array"},
        "libvirt_state": {"type": "string"},
        "agent_state": {"type": "string"},
        "previous_agent_state": {"type": "string"},
        "transition": {
            "type": "object",
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
        },
        "last_state_update_at": {"type": "string"},
        "dispatch_id": {"type": "string"},
        "scope": {
            "type": "object",
            "properties": {
                "directory_path": {"type": "string"},
                "vm_name": {"type": "string"},
                "session_id": {"type": "string"},
                "vm_user": {"type": "string"},
            },
        },
        "ip_address": {"type": "string"},
    },
    "effect:vm:create": {
        "session_id": {"type": "string"},
        "vm_name": {"type": "string"},
        "labels": {"type": "object"},
        "success": {"type": "boolean"},
        "target_dir": {"type": "string"},
        "guest_repo_name": {"type": "string"},
        "remote_url": {"type": "string"},
    },
    "effect:vm:start": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "target": {"type": "string"},
        "vm_name": {"type": "string"},
        "message": {"type": "string"},
    },
    "effect:vm:stop": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "target": {"type": "string"},
        "vm_name": {"type": "string"},
        "message": {"type": "string"},
    },
    "effect:agent:prompt": {
        "dispatch_id": {"type": "string"},
        "thread_id": {"type": "string"},
        "success": {"type": "boolean"},
        "output_mode": {"type": "string"},
    },
    "effect:agent:enqueue-prompt": {
        "dispatch_id": {"type": "string"},
        "thread_id": {"type": "string"},
        "success": {"type": "boolean"},
        "queued": {"type": "boolean"},
        "queue_key": {"type": "string"},
        "vm_name": {"type": "string"},
        "session_id": {"type": "string"},
        "coalesced_count": {"type": "integer"},
    },
    "effect:bash:script": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "script_path": {"type": "string"},
        "message": {"type": "string"},
        "exit_code": {"type": "integer"},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
    },
    "effect:vm:exec": {
        "success": {"type": "boolean"},
        "vm_name": {"type": "string"},
        "session_id": {"type": "string"},
        "action": {"type": "string"},
        "message": {"type": "string"},
        "exit_code": {"type": "integer"},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
    },
    "effect:vm:copy-files": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "message": {"type": "string"},
        "target": {"type": "string"},
        "vm_name": {"type": "string"},
        "vm_ip": {"type": "string"},
        "vm_user": {"type": "string"},
        "directory_path": {"type": "string"},
        "copied_count": {"type": "integer"},
        "copied_paths": {"type": "array"},
        "file_count": {"type": "integer"},
        "copied_files": {"type": "array"},
    },
    "effect:vm:delete-files": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "message": {"type": "string"},
        "target": {"type": "string"},
        "vm_name": {"type": "string"},
        "vm_user": {"type": "string"},
        "deleted_count": {"type": "integer"},
        "deleted_paths": {"type": "array"},
    },
    "effect:issue:update-state": {
        "success": {"type": "boolean"},
        "issue_number": {"type": "string"},
        "source_type": {"type": "string"},
        "updated": {"type": "boolean"},
        "state": {"type": "string"},
        "provider": {"type": "string"},
        "remote_issue_id": {"type": "string"},
    },
    "effect:session:update-metadata": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "message": {"type": "string"},
        "session_id": {"type": "string"},
        "vm_name": {"type": "string"},
        "directory_path": {"type": "string"},
        "metadata": {"type": "object"},
    },
    "effect:git:promote-agent-branch": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "message": {"type": "string"},
        "host_repo_path": {"type": "string"},
        "bare_repo_path": {"type": "string"},
        "agent_branch": {"type": "string"},
        "review_branch": {"type": "string"},
        "remote_name": {"type": "string"},
    },
    "effect:pr:create": {
        "success": {"type": "boolean"},
        "action": {"type": "string"},
        "message": {"type": "string"},
        "pr_id": {"type": "string"},
        "pr_url": {"type": "string"},
    },
    "query:vm:run-state": {
        "vm_name": {"type": "string"},
        "session_ids": {"type": "array"},
        "libvirt_state": {"type": "string"},
        "state_code": {"type": "string"},
        "is_running": {"type": "boolean"},
        "is_terminal": {"type": "boolean"},
    },
    "query:git:pr-note": {
        "body": {"type": "string"},
        "has_note": {"type": "boolean"},
        "local_branch": {"type": "string"},
        "host_repo_path": {"type": "string"},
        "notes_ref": {"type": "string"},
        "local_notes_ref": {"type": "string"},
        "remote_name": {"type": "string"},
        "commit_sha": {"type": "string"},
    },
    "query:session-context": {
        "session_id": {"type": "string"},
        "vm_name": {"type": "string"},
        "directory_path": {"type": "string"},
        "metadata": {"type": "object"},
        "created_at": {"type": "string"},
        "expires_at": {"type": "string"},
        "model": {"type": "string"},
    },
    "query:issue:get": {
        "issue_id": {"type": "string"},
        "issue_number": {"type": "string"},
        "title": {"type": "string"},
        "body": {"type": "string"},
        "state": {"type": "string"},
        "url": {"type": "string"},
        "source": {"type": "string"},
        "source_type": {"type": "string"},
        "directory_path": {"type": "string"},
        "yaml_path": {"type": "string"},
        "repo_source": {"type": "string"},
        "metadata": {"type": "object"},
        "comments": {"type": "array"},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
    },
    "gate:for": {
        "current_element": {"type": "object"},
        "current_index": {"type": "integer"},
        "item_count": {"type": "integer"},
        "is_done": {"type": "boolean"},
    },
}


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
    if isinstance(node, Mapping):
        node_type = str(node.get("type") or "").strip()
        raw_outputs = node.get("outputs")
        template = node.get("template")
    else:
        node_type = str(getattr(node, "type", "") or "").strip()
        raw_outputs = getattr(node, "outputs", None)
        template = getattr(node, "template", None)

    schema: dict[str, dict[str, Any]] = {}
    static_schema = _STATIC_OUTPUT_SCHEMAS.get(node_type, {})
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
