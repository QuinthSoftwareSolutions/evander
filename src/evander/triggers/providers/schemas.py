from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.ado_query_mode import AdoQueryMode, normalize_ado_query_mode, validate_ado_query_mode
from evander.events.models import ConfigValue, TriggerDefinition
from evander.events.internal import validate_internal_event_type
from evander.models import DirectoryPath
from evander.events.provider_types import (
    EmailProvider,
    PrFeedbackProvider,
    validate_email_provider,
    validate_pr_feedback_provider,
)
from evander.host_directories.models import normalize_host_directory_id
from evander.host_directories.paths import normalize_relative_host_path
from evander.session_artifacts import normalize_artifact_relative_path
from evander.workflow.contracts import normalize_directory_paths, normalize_string_list

_WEBHOOK_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _migrate_legacy_pull_request_organization(data: object) -> object:
    if not isinstance(data, dict):
        return data
    payload = dict(data)
    if "ado_organization" not in payload and "organization_url" in payload:
        payload["ado_organization"] = payload["organization_url"]
    payload.pop("organization_url", None)
    return payload


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


class AgentArtifactStagedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:agent:artifact-staged"]
    artifact_path: str | None = None
    path_prefix: str | None = None

    @field_validator("artifact_path", "path_prefix", mode="before")
    @classmethod
    def normalize_artifact_path(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return normalize_artifact_relative_path(text) if text else None

    @model_validator(mode="after")
    def validate_path_filter(self) -> "AgentArtifactStagedTriggerDefinition":
        if self.artifact_path is not None and self.path_prefix is not None:
            raise ValueError("artifact_path and path_prefix are mutually exclusive")
        return self


class WebhookTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:http:webhook"]
    slug: str = Field(min_length=1)
    secret_env: str = Field(min_length=1)
    event_type: str | None = None

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, value: object) -> str:
        if value is None:
            raise ValueError("slug must be a non-empty string")
        text = str(value).strip()
        if not text:
            raise ValueError("slug must be a non-empty string")
        if not _WEBHOOK_SLUG_RE.fullmatch(text):
            raise ValueError("slug must match [A-Za-z0-9_.-]+")
        return text

    @field_validator("secret_env", mode="before")
    @classmethod
    def normalize_secret_env(cls, value: object) -> str:
        if value is None:
            raise ValueError("secret_env must be a non-empty string")
        text = str(value).strip()
        if not text:
            raise ValueError("secret_env must be a non-empty string")
        return text

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def sync_event_type_config(self) -> "WebhookTriggerDefinition":
        if self.event_type is None:
            self.event_type = f"webhook.{self.slug}"
        else:
            if self.event_type.startswith("external."):
                raise ValueError("event_type must not include the reserved 'external.' prefix")
            self.event_type = validate_internal_event_type(self.event_type)
        self.config = {
            **self.config,
            "slug": self.slug,
            "secret_env": self.secret_env,
            "event_type": self.event_type,
        }
        return self


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
    query_mode: AdoQueryMode | None = None
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

    @field_validator("query_mode", mode="before")
    @classmethod
    def normalize_query_mode(cls, value: object) -> AdoQueryMode | None:
        return normalize_ado_query_mode(value)

    @field_validator("states", "types", "tags_any", mode="before")
    @classmethod
    def normalize_filters(cls, value: object) -> object:
        return normalize_string_list(value)

    @model_validator(mode="after")
    def validate_query_mode(self) -> "AdoWorkItemMatchingTriggerDefinition":
        self.query_mode = validate_ado_query_mode(
            query_mode=self.query_mode,
            wiql=self.wiql,
            current_iteration=self.current_iteration,
            context="trigger:ado:work-item-matching",
        )
        return self


class AdoWorkItemCommentCreatedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:ado:work-item-comment-created"]
    directory_path: str | None = None
    project: str | None = None
    query_mode: AdoQueryMode | None = None
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
    comment_contains: str | None = None
    comment_contains_any: list[str] = Field(default_factory=list)
    comment_author_exclude_names: list[str] = Field(default_factory=list)
    comment_author_exclude_ids: list[str] = Field(default_factory=list)
    comment_case_sensitive: bool = False
    include_existing: bool = False

    @field_validator(
        "directory_path",
        "project",
        "wiql",
        "team",
        "iteration",
        "area_path",
        "text_search",
        "comment_contains",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("query_mode", mode="before")
    @classmethod
    def normalize_query_mode(cls, value: object) -> AdoQueryMode | None:
        return normalize_ado_query_mode(value)

    @field_validator(
        "states",
        "types",
        "tags_any",
        "comment_contains_any",
        "comment_author_exclude_names",
        "comment_author_exclude_ids",
        mode="before",
    )
    @classmethod
    def normalize_filters(cls, value: object) -> object:
        return normalize_string_list(value)

    @model_validator(mode="after")
    def validate_query_mode(self) -> "AdoWorkItemCommentCreatedTriggerDefinition":
        self.query_mode = validate_ado_query_mode(
            query_mode=self.query_mode,
            wiql=self.wiql,
            current_iteration=self.current_iteration,
            context="trigger:ado:work-item-comment-created",
        )
        return self


class RemoteIssueMatchingTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:remote-issue-matching"]
    provider: str
    directory_path: str | None = None
    project: str | None = None
    repo: str | None = None
    query_mode: AdoQueryMode | None = None
    wiql: str | None = None
    current_iteration: bool = False
    team: str | None = None
    iteration: str | None = None
    area_path: str | None = None
    states: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    text_search: str | None = None
    state: str | None = None
    labels_any: list[str] = Field(default_factory=list)
    labels_all: list[str] = Field(default_factory=list)
    assignee: str | None = None
    author: str | None = None
    mentioned: str | None = None
    milestone: str | None = None
    since: str | None = None
    top: int = Field(default=50, ge=1, le=200)

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        text = str(value or "").strip().lower()
        if text in {"ado", "azure_devops", "azure-devops"}:
            return "azure_devops"
        if text == "github":
            return "github"
        raise ValueError("provider must be azure_devops or github")

    @field_validator("query_mode", mode="before")
    @classmethod
    def normalize_query_mode(cls, value: object) -> AdoQueryMode | None:
        return normalize_ado_query_mode(value)

    @field_validator(
        "directory_path",
        "project",
        "repo",
        "wiql",
        "team",
        "iteration",
        "area_path",
        "text_search",
        "state",
        "assignee",
        "author",
        "mentioned",
        "milestone",
        "since",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator(
        "states",
        "types",
        "tags_any",
        "labels_any",
        "labels_all",
        mode="before",
    )
    @classmethod
    def normalize_filters(cls, value: object) -> object:
        return normalize_string_list(value)

    @model_validator(mode="after")
    def validate_provider_query_mode(self) -> "RemoteIssueMatchingTriggerDefinition":
        if self.provider == "github" and self.current_iteration:
            raise ValueError(
                "trigger:remote-issue-matching current_iteration is only valid for Azure DevOps"
            )
        if self.provider == "github" and self.wiql:
            raise ValueError("trigger:remote-issue-matching wiql is only valid for Azure DevOps")
        if self.provider == "github" and self.query_mode is not None:
            raise ValueError(
                "trigger:remote-issue-matching query_mode is only valid for Azure DevOps"
            )
        if self.provider == "azure_devops":
            self.query_mode = validate_ado_query_mode(
                query_mode=self.query_mode,
                wiql=self.wiql,
                current_iteration=self.current_iteration,
                context="trigger:remote-issue-matching",
            )
        return self


class RemoteIssueCommentCreatedTriggerDefinition(RemoteIssueMatchingTriggerDefinition):
    type: Literal["trigger:remote-issue-comment-created"]
    comment_contains: str | None = None
    comment_contains_any: list[str] = Field(default_factory=list)
    comment_author_exclude_names: list[str] = Field(default_factory=list)
    comment_author_exclude_ids: list[str] = Field(default_factory=list)
    comment_case_sensitive: bool = False
    include_existing: bool = False

    @field_validator("comment_contains", mode="before")
    @classmethod
    def normalize_optional_comment_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator(
        "comment_contains_any",
        "comment_author_exclude_names",
        "comment_author_exclude_ids",
        mode="before",
    )
    @classmethod
    def normalize_comment_filters(cls, value: object) -> object:
        return normalize_string_list(value)


class ScriptRunRequestTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:script-run-request"]


class HostDirectoryFileAddedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:host-directory:file-added"]
    directory_id: str = Field(min_length=1)
    path: str = ""
    recursive: bool = False
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    settle_seconds: float = Field(default=5.0, ge=0)

    @field_validator("directory_id", mode="before")
    @classmethod
    def normalize_directory_id(cls, value: object) -> str:
        return normalize_host_directory_id(value)

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, value: object) -> str:
        parts = normalize_relative_host_path(value, allow_empty=True)
        return PurePosixPath(*parts).as_posix() if parts else ""

    @field_validator("include_globs", "exclude_globs", mode="before")
    @classmethod
    def normalize_globs(cls, value: object) -> object:
        normalized = normalize_string_list(value)
        if not isinstance(normalized, list):
            return normalized
        for pattern in normalized:
            if "\x00" in pattern or "\\" in pattern or pattern.startswith("/"):
                raise ValueError("host directory globs must be relative POSIX patterns")
        return normalized


def _normalize_lower_string_list(value: object) -> object:
    normalized = normalize_string_list(value)
    if not isinstance(normalized, list):
        return normalized
    return [item.lower() for item in normalized]


class CommunicationNewMessageTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:communication:new-message"]
    communication_target_ids: list[str] = Field(min_length=1)
    directory_path: str | None = "root"
    author_ids: list[str] = Field(default_factory=list)
    content_contains: str | None = None
    content_starts_with: str | None = None
    case_sensitive: bool = False

    @field_validator("communication_target_ids", mode="before")
    @classmethod
    def normalize_communication_target_ids(cls, value: object) -> object:
        normalized = _normalize_lower_string_list(value)
        if isinstance(normalized, list) and not normalized:
            raise ValueError("communication_target_ids must contain at least one target")
        return normalized

    @field_validator("directory_path", "content_contains", "content_starts_with", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object, info) -> object:
        if value is None:
            return "root" if info.field_name == "directory_path" else None
        text = str(value).strip()
        if info.field_name == "directory_path":
            return text or "root"
        return text or None

    @field_validator("author_ids", mode="before")
    @classmethod
    def normalize_author_ids(cls, value: object) -> object:
        return _normalize_lower_string_list(value)


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


class TeamsDirectMessageTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:teams:direct-message"]
    targets: list[TeamsTargetDefinition] = Field(min_length=1)
    sender_user_ids: list[str] = Field(min_length=1)
    message_filter: TeamsMessageFilterDefinition | None = None

    @field_validator("sender_user_ids", mode="before")
    @classmethod
    def normalize_sender_user_ids(cls, value: object) -> object:
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
    mailbox_ids: list[str] = Field(min_length=1)
    folder: str = "inbox"
    provider: EmailProvider | None = None
    account_identifier: str | None = None
    auth_secret_refs: dict[str, str] = Field(default_factory=dict)

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: object) -> str | None:
        if value is None:
            return None
        return validate_email_provider(value)

    @field_validator("account_identifier", "folder", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object, info) -> object:
        if value is None:
            return "inbox" if info.field_name == "folder" else None
        text = str(value).strip()
        if info.field_name == "folder":
            return text or "inbox"
        return text or None

    @field_validator("mailbox_ids", mode="before")
    @classmethod
    def normalize_mailbox_ids(cls, value: object) -> object:
        normalized = normalize_string_list(value)
        if isinstance(normalized, list) and not normalized:
            raise ValueError("mailbox_ids must contain at least one mailbox")
        return normalized


class _PullRequestTriggerDefinition(TriggerDefinition):
    provider: PrFeedbackProvider
    repo: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None
    status: ConfigValue | None = None
    creator_id: ConfigValue | None = None
    reviewer_id: ConfigValue | None = None
    source_ref_name: ConfigValue | None = None
    target_ref_name: ConfigValue | None = None
    source_repository_id: ConfigValue | None = None
    repository_id: ConfigValue | None = None
    min_time: ConfigValue | None = None
    max_time: ConfigValue | None = None
    query_time_range_type: ConfigValue | None = None
    include_links: ConfigValue | None = None
    max_comment_length: ConfigValue | None = None
    tags: ConfigValue | None = None
    top: ConfigValue | None = None
    skip: ConfigValue | None = None
    cursor: ConfigValue | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: object) -> str:
        return validate_pr_feedback_provider(value)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("project", mode="before")
    @classmethod
    def normalize_project(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        text = str(value).strip()
        return text or None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return value
        raise ValueError("trigger:pull-request:* tags must be a string")


class PullRequestNewFeedbackTriggerDefinition(_PullRequestTriggerDefinition):
    type: Literal["trigger:pull-request:new-feedback"]
    agent_author_names: list[str] = Field(default_factory=list)
    agent_mention_names: list[str] = Field(default_factory=list)
    agent_author_ids: list[str] = Field(default_factory=list)
    agent_mention_ids: list[str] = Field(default_factory=list)
    suppress_agent_authored_feedback: bool = False

    @field_validator(
        "agent_author_names",
        "agent_mention_names",
        "agent_author_ids",
        "agent_mention_ids",
        mode="before",
    )
    @classmethod
    def normalize_agent_identities(cls, value: object) -> object:
        return normalize_string_list(value)


class PullRequestCompletedTriggerDefinition(_PullRequestTriggerDefinition):
    type: Literal["trigger:pull-request:completed"]


_CI_RUN_COMPLETED_PROVIDER_ALIASES = {
    "ado": "ado_pipelines",
    "ado_pipelines": "ado_pipelines",
    "azure_devops": "ado_pipelines",
    "azure_pipelines": "ado_pipelines",
    "github": "github_actions",
    "github_actions": "github_actions",
}

_CI_RUN_COMPLETED_TERMINAL_CONCLUSIONS = (
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "skipped",
)

_CI_RUN_COMPLETED_CONCLUSION_ALIASES = {
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "failed": "failure",
    "failure": "failure",
    "partiallysucceeded": "failure",
    "skipped": "skipped",
    "success": "success",
    "succeeded": "success",
    "timedout": "timed_out",
    "timeout": "timed_out",
    "timed_out": "timed_out",
}


def _normalize_ci_token(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _normalize_ci_conclusion_token(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return re.sub(r"[^a-z0-9]+", "", text)


class CiRunCompletedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:ci:run:completed"]
    provider: Literal["github_actions", "ado_pipelines"]
    repo: str | None = None
    workflow: str | None = None
    ado_organization: str | None = None
    project: str | None = None
    pipeline_id: int | None = None
    pipeline_name: str | None = None
    branches: list[str] = Field(default_factory=list)
    conclusions: list[str] = Field(
        default_factory=lambda: list(_CI_RUN_COMPLETED_TERMINAL_CONCLUSIONS)
    )
    top: int = Field(default=50, ge=1, le=100)
    include_existing: bool = False

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> str:
        token = _normalize_ci_token(value)
        if token is None:
            raise ValueError("provider is required")
        canonical = _CI_RUN_COMPLETED_PROVIDER_ALIASES.get(token)
        if canonical is None:
            allowed = ", ".join(sorted(_CI_RUN_COMPLETED_PROVIDER_ALIASES))
            raise ValueError(f"Unsupported CI provider: {token!r}. Allowed providers: {allowed}")
        return canonical

    @field_validator("repo", "workflow", "project", "pipeline_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("ado_organization", mode="before")
    @classmethod
    def normalize_ado_organization(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("branches", mode="before")
    @classmethod
    def normalize_branches(cls, value: object) -> object:
        return normalize_string_list(value)

    @field_validator("conclusions", mode="before")
    @classmethod
    def normalize_conclusions(cls, value: object) -> list[str]:
        if value is None:
            return list(_CI_RUN_COMPLETED_TERMINAL_CONCLUSIONS)
        normalized = normalize_string_list(value)
        if not normalized:
            return list(_CI_RUN_COMPLETED_TERMINAL_CONCLUSIONS)
        canonical: list[str] = []
        for item in normalized:
            token = _normalize_ci_conclusion_token(item)
            if token is None:
                continue
            conclusion = _CI_RUN_COMPLETED_CONCLUSION_ALIASES.get(token)
            if conclusion is None:
                allowed = ", ".join(_CI_RUN_COMPLETED_TERMINAL_CONCLUSIONS)
                raise ValueError(
                    f"Unsupported CI conclusion: {item!r}. Allowed conclusions: {allowed}"
                )
            canonical.append(conclusion)
        return list(dict.fromkeys(canonical))

    @model_validator(mode="after")
    def validate_provider_specific_fields(self) -> "CiRunCompletedTriggerDefinition":
        if self.provider == "github_actions" and not self.repo:
            raise ValueError("repo is required when provider is 'github_actions'")
        return self


class ReviewNewRequestTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:review:new-request"]


class ApprovalPendingCreatedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:approval:pending-created"]
    directory_path: str | None = "root"
    action_types: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path(cls, value: object) -> object:
        if value is None:
            return "root"
        text = str(value).strip()
        if not text:
            return "root"
        return DirectoryPath(segments=text).as_posix()

    @field_validator("action_types", "tool_names", mode="before")
    @classmethod
    def normalize_string_filters(cls, value: object) -> object:
        return normalize_string_list(value)


class GitPushTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:git:push"]
    managed_repo_id: str | None = None
    managed_repo_id_glob: str | None = None

    @field_validator("managed_repo_id", "managed_repo_id_glob", mode="before")
    @classmethod
    def normalize_optional_repo_id_field(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def validate_repo_selector(self) -> "GitPushTriggerDefinition":
        if not self.managed_repo_id and not self.managed_repo_id_glob:
            raise ValueError("trigger:git:push requires managed_repo_id or managed_repo_id_glob")
        if self.managed_repo_id and self.managed_repo_id_glob:
            raise ValueError(
                "trigger:git:push managed_repo_id and managed_repo_id_glob are mutually exclusive"
            )
        return self


class AgentRunCompletedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:agent:run:completed"]
    vm_user: str | None = "agent"


class AgentPromptNewTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:agent:prompt:new"]
