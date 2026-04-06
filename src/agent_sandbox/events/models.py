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
    "trigger:discord:new-message",
    "trigger:email:new-message",
    "trigger:pull-request:new-feedback",
    "trigger:git:push",
    "trigger:agent:run:completed",
]

GateType = Literal["gate:filter", "gate:map", "gate:merge", "gate:await-agent"]
EffectType = Literal[
    "effect:vm:create",
    "effect:vm:start",
    "effect:vm:stop",
    "effect:agent:prompt",
    "effect:vm:exec",
    "effect:vm:copy-files",
    "effect:pr:create",
]
QueryType = Literal[
    "query:vm:run-state",
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


class DiscordNewMessageTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:discord:new-message"]
    discord_bot_token_env: str = Field(min_length=1)
    channel_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)

    @field_validator("channel_ids", "user_ids", mode="before")
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


class GitPushTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:git:push"]
    path_to_repo: str = Field(min_length=1)


class AgentRunCompletedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:agent:run:completed"]
    vm_user: str | None = "agent"


TriggerDefinitionDiscriminated = Annotated[
    CronTriggerDefinition
    | TicketCreatedTriggerDefinition
    | TicketChangedTriggerDefinition
    | DiscordNewMessageTriggerDefinition
    | EmailNewMessageTriggerDefinition
    | PullRequestNewFeedbackTriggerDefinition
    | GitPushTriggerDefinition
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
    completion_trigger_id: str = Field(min_length=1)


GateDefinitionDiscriminated = Annotated[
    GateFilterDefinition | GateMapDefinition | GateMergeDefinition | GateAwaitAgentDefinition,
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
    discord_channels: list[str] | None = None
    email_mailboxes: list[str] | None = None
    email_folders: list[str] | None = None
    python: ConfigValue | None = None
    dotnet: ConfigValue | None = None
    nuget: ConfigValue | None = None
    mssql: ConfigValue | None = None
    mssql_init_root: ConfigValue | None = None
    playwright: ConfigValue | None = None
    directory_path: ConfigValue | None = None


class AgentPromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:prompt"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    prompt: ConfigValue
    vm_user: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None
    output_mode: Literal["immediate", "on_completion"] = "immediate"


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


class PrCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:pr:create"]
    host_repo_path: ConfigValue
    local_branch: ConfigValue | None = None
    origin_branch: ConfigValue | None = None
    provider: ConfigValue | None = None
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
    | VmExecEffectDefinition
    | VmCopyFilesEffectDefinition
    | PrCreateEffectDefinition,
    Field(discriminator="type"),
]


class EdgeDefinition(BaseModel):
    source_id: str
    target_id: str

    @field_validator("source_id", "target_id", mode="before")
    @classmethod
    def normalize_node_id(cls, value: object, info) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text


class QueryDefinition(WorkflowNodeDefinition):
    type: QueryType


class VmRunStateQueryDefinition(QueryDefinition):
    type: Literal["query:vm:run-state"]
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None


QueryDefinitionDiscriminated = Annotated[
    VmRunStateQueryDefinition,
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
    "trigger:git:push": {
        "branch_name": {"type": "string"},
        "commit_hash": {"type": "string"},
        "old_commit_hash": {"type": "string"},
        "ref_name": {"type": "string"},
        "repo_path": {"type": "string"},
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
        key for key in ("equals", "not_equals", "in", "not_in", "is_null", "is_not_null") if key in value
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
        raise ValueError(f"{path} must contain a logical operator or one of: equals, in, is_not_null, is_null, not_equals, not_in")
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
