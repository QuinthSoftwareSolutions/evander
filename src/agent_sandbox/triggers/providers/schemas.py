from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.events.models import TriggerDefinition
from agent_sandbox.events.internal import validate_internal_event_type
from agent_sandbox.events.provider_types import (
    EmailProvider,
    PrFeedbackProvider,
    validate_email_provider,
    validate_pr_feedback_provider,
)
from agent_sandbox.workflow.contracts import normalize_directory_paths, normalize_string_list


class EventTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:event"]
    event_type: str = Field(min_length=1)
    correlation_key: str | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        return validate_internal_event_type(value)

    @field_validator("correlation_key", mode="before")
    @classmethod
    def normalize_correlation_key(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class CronTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:cron"]
    expression: str = Field(min_length=1)

    @model_validator(mode="after")
    def sync_expression_config(self) -> "CronTriggerDefinition":
        self.config = {**self.config, "expression": self.expression}
        return self


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


class IssueCommentCreatedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:issue:comment-created"]
    provider: str | None = None
    organization_url: str | None = None
    project: str | None = None
    directories: list[str] = Field(default_factory=list)

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if text in {"ado", "azure_devops", "azure-devops"}:
            return "ado"
        raise ValueError("Unsupported issue comment provider: %r. Allowed providers: ado" % text)

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

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        return normalize_directory_paths(value)


class AdoWorkItemMatchingTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:ado:work-item-matching"]
    directory_path: str | None = None
    project: str | None = None
    wiql: str | None = None
    current_iteration: bool = False
    team: str | None = None
    iteration: str | None = None
    area_path: str | None = None
    states: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    text_search: str | None = None
    top: int = Field(default=50, ge=1, le=200)

    @field_validator(
        "directory_path",
        "project",
        "wiql",
        "team",
        "iteration",
        "area_path",
        "text_search",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("states", "types", "tags_any", mode="before")
    @classmethod
    def normalize_filters(cls, value: object) -> object:
        return normalize_string_list(value)

    @model_validator(mode="after")
    def validate_query_mode(self) -> "AdoWorkItemMatchingTriggerDefinition":
        if self.wiql and self.current_iteration:
            raise ValueError(
                "trigger:ado:work-item-matching wiql and current_iteration are mutually exclusive"
            )
        return self


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

    @field_validator("user_ids", mode="before")
    @classmethod
    def normalize_optional_lists(cls, value: object) -> object:
        return normalize_string_list(value)


class TeamsMeetingNewTranscriptTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:teams-meeting:new-transcript"]
    session_ids: list[str] = Field(default_factory=list)

    @field_validator("session_ids", mode="before")
    @classmethod
    def normalize_session_ids(cls, value: object) -> object:
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


class PullRequestCompletedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:pull-request:completed"]
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
    def validate_provider_specific_fields(self) -> "PullRequestCompletedTriggerDefinition":
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
