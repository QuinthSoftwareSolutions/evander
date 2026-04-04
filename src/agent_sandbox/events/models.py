from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from agent_sandbox.models import DirectoryPath

TriggerType = Literal[
    "trigger:cron",
    "trigger:ticket:created",
    "trigger:ticket:changed",
    "trigger:discord:new-message",
    "trigger:email:new-message",
    "trigger:pull-request:new-feedback",
    "trigger:git:push",
]

EffectType = Literal["effect:vm:create", "effect:agent:prompt"]

EmailProvider = Literal["json_store", "gmail", "graph"]
PrFeedbackProvider = Literal["local", "github", "ado"]

OutputName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
OutputReferenceString = Annotated[str, StringConstraints(pattern=r"^\$\([A-Za-z_][A-Za-z0-9_]*\)$")]
InterpolatedString = Annotated[str, StringConstraints(pattern=r".*\$\([A-Za-z_][A-Za-z0-9_]*\).*")]

ConfigScalarValue: TypeAlias = str | int | float | bool | None
ConfigValue: TypeAlias = (
    ConfigScalarValue
    | OutputReferenceString
    | InterpolatedString
    | list["ConfigValue"]
    | dict[str, "ConfigValue"]
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


TriggerDefinitionDiscriminated = Annotated[
    CronTriggerDefinition
    | TicketCreatedTriggerDefinition
    | TicketChangedTriggerDefinition
    | DiscordNewMessageTriggerDefinition
    | EmailNewMessageTriggerDefinition
    | PullRequestNewFeedbackTriggerDefinition
    | GitPushTriggerDefinition,
    Field(discriminator="type"),
]


class EffectDefinition(WorkflowNodeDefinition):
    type: EffectType
    upstream_id: str

    @field_validator("upstream_id", mode="before")
    @classmethod
    def normalize_upstream_id(cls, value: object) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("upstream_id must be a non-empty string")
        return text

    @model_validator(mode="after")
    def validate_upstream_not_self(self) -> "EffectDefinition":
        if self.upstream_id == self.id:
            raise ValueError("effect cannot reference itself as upstream")
        return self


class VmCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:create"]
    vm_name: ConfigValue | None = None


class AgentPromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:prompt"]
    prompt: ConfigValue


EffectDefinitionDiscriminated = Annotated[
    VmCreateEffectDefinition | AgentPromptEffectDefinition,
    Field(discriminator="type"),
]


class TriggerEvent(BaseModel):
    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: TriggerType
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


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
