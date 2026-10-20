from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator
from evander.classification.models import EvaluationRequest, Question
from evander.codex_auth import (
    normalize_subscription_profile_id,
    normalize_subscription_quota_limit_id,
    normalize_subscription_quota_window_minutes,
)

from evander.events.models import ConfigValue, QueryDefinition


def _migrate_legacy_pull_request_organization(data: object) -> object:
    if not isinstance(data, dict):
        return data
    payload = dict(data)
    if "ado_organization" not in payload and "organization_url" in payload:
        payload["ado_organization"] = payload["organization_url"]
    payload.pop("organization_url", None)
    return payload


class VmRunStateQueryDefinition(QueryDefinition):
    type: Literal["query:vm:run-state"]
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None


class GitPrNoteQueryDefinition(QueryDefinition):
    type: Literal["query:git:pr-note"]
    managed_repo_id: ConfigValue
    commit_sha: ConfigValue
    notes_ref: ConfigValue


class GitMergeBaseQueryDefinition(QueryDefinition):
    type: Literal["query:git:merge-base"]
    managed_repo_id: ConfigValue
    target_branch: ConfigValue
    candidate_commit_sha: ConfigValue
    directory_path: ConfigValue | None = None


class SessionContextQueryDefinition(QueryDefinition):
    type: Literal["query:session-context"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    include_activity: ConfigValue = False

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionContextQueryDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("query:session-context requires target, session_id, or vm_name")
        return self


class WebsiteSessionResolveProfileQueryDefinition(QueryDefinition):
    type: Literal["query:website-session:resolve-profile"]
    browser_origin: str
    login_origin: str
    directory_path: str | None = None

    @field_validator("browser_origin", "login_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 4096:
            raise ValueError("website origin must be a nonempty HTTPS URL or binding")
        if "${{" in value:
            return value
        from evander.website_sessions import website_origin
        return website_origin(value)


class StateGetQueryDefinition(QueryDefinition):
    type: Literal["query:state:get"]
    namespace: ConfigValue
    key: ConfigValue


class ArtifactSpaceListQueryDefinition(QueryDefinition):
    type: Literal["query:artifact-space:list"]
    path: ConfigValue | None = None
    recursive: ConfigValue | None = None
    subject: ConfigValue | None = None
    include_versions: ConfigValue | None = None


class ArtifactSpaceLatestQueryDefinition(QueryDefinition):
    type: Literal["query:artifact-space:latest"]
    space_path: ConfigValue
    artifact_id: ConfigValue | None = None
    subject: ConfigValue | None = None


class SessionListQueryDefinition(QueryDefinition):
    type: Literal["query:session:list"]
    directory_path: ConfigValue | None = None
    metadata_equals: ConfigValue | None = None
    metadata_exists: ConfigValue | None = None
    metadata_prefix: ConfigValue | None = None
    state: ConfigValue | None = None
    active_only: ConfigValue | None = None
    limit: ConfigValue | None = None

    @field_validator("metadata_equals", "metadata_prefix")
    @classmethod
    def validate_metadata_map(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (dict, str)):
            return value
        raise ValueError("query:session:list metadata filters must be JSON objects")

    @field_validator("metadata_exists")
    @classmethod
    def validate_metadata_exists(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (str, list, dict)):
            return value
        raise ValueError("query:session:list metadata_exists must be a string, array, or object")


class SecurityScanQueryDefinition(QueryDefinition):
    type: Literal["query:security:scan"]
    scan_id: ConfigValue


class CodexSubscriptionQuotaQueryDefinition(QueryDefinition):
    type: Literal["query:codex:subscription-quota"]
    profile_id: ConfigValue
    directory_path: ConfigValue | None = None
    limit_id: ConfigValue = "codex"
    window_duration_minutes: ConfigValue = 10080

    @field_validator("directory_path")
    @classmethod
    def validate_quota_directory(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("directory_path must be a string")
        return value.strip() or None

    @field_validator("profile_id", "limit_id", "window_duration_minutes")
    @classmethod
    def validate_quota_selector(cls, value: ConfigValue, info) -> ConfigValue:
        if isinstance(value, str) and "${{" in value:
            return value
        if info.field_name == "window_duration_minutes":
            return normalize_subscription_quota_window_minutes(value)
        if info.field_name == "limit_id":
            return normalize_subscription_quota_limit_id(value)
        if not isinstance(value, str):
            raise ValueError("profile_id must be a subscription profile slug")
        return normalize_subscription_profile_id(value, allow_none=False)


class ModelUsageListQueryDefinition(QueryDefinition):
    type: Literal["query:model-usage:list"]
    session_id: ConfigValue | None = None
    workflow_run_id: ConfigValue | None = None
    effect_id: ConfigValue | None = None
    dispatch_id: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    include_directory_descendants: ConfigValue | None = None
    provider: ConfigValue | None = None
    model: ConfigValue | None = None
    agent_harness: ConfigValue | None = None
    execution_backend: ConfigValue | None = None
    normalized_usage_status: ConfigValue | None = None
    request_status: ConfigValue | None = None
    occurred_after: ConfigValue | None = None
    occurred_before: ConfigValue | None = None
    limit: ConfigValue | None = None
    include_costs: ConfigValue | None = None
    cost_currency: ConfigValue | None = None


class IssueListQueryDefinition(QueryDefinition):
    type: Literal["query:issue:list"]
    directory_path: ConfigValue | None = None
    direct_only: ConfigValue | None = None
    states: ConfigValue | None = None
    exclude_states: ConfigValue | None = None
    source: ConfigValue | None = None
    source_type: ConfigValue | None = None
    tags_any: ConfigValue | None = None
    tags_all: ConfigValue | None = None
    metadata_equals: ConfigValue | None = None
    metadata_exists: ConfigValue | None = None
    metadata_prefix: ConfigValue | None = None
    limit: ConfigValue | None = None

    @field_validator("metadata_equals", "metadata_prefix")
    @classmethod
    def validate_metadata_map(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (dict, str)):
            return value
        raise ValueError("query:issue:list metadata filters must be JSON objects")

    @field_validator("metadata_exists")
    @classmethod
    def validate_metadata_exists(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (str, list, dict)):
            return value
        raise ValueError("query:issue:list metadata_exists must be a string, array, or object")


class AgentArtifactTextQueryDefinition(QueryDefinition):
    type: Literal["query:agent:artifact-text"]
    session_id: ConfigValue
    path: ConfigValue
    vm_user: ConfigValue | None = None


class IssueGetQueryDefinition(QueryDefinition):
    type: Literal["query:issue:get"]
    issue_id: ConfigValue


class AdoBuildsQueryDefinition(QueryDefinition):
    type: Literal["query:ado:builds"]
    directory_path: ConfigValue
    pipeline_id: ConfigValue
    repository_id: ConfigValue
    filters: ConfigValue
    limit: ConfigValue = 100
    repository_type: ConfigValue = "TfsGit"
    project: ConfigValue | None = None


class TextRegexQueryDefinition(QueryDefinition):
    type: Literal["query:text:regex"]
    text: ConfigValue
    pattern: ConfigValue
    operation: ConfigValue = "fullmatch"
    replacement: ConfigValue = ""
    require_match: ConfigValue = False


class AdoWorkItemsQueryDefinition(QueryDefinition):
    type: Literal["query:ado:work-items"]
    directory_path: ConfigValue | None = None
    project: ConfigValue | None = None
    query_mode: ConfigValue | None = None
    wiql: ConfigValue | None = None
    current_iteration: ConfigValue | None = None
    team: ConfigValue | None = None
    iteration: ConfigValue | None = None
    area_path: ConfigValue | None = None
    states: ConfigValue | None = None
    types: ConfigValue | None = None
    tags_any: ConfigValue | None = None
    text_search: ConfigValue | None = None
    top: ConfigValue | None = None
    skip: ConfigValue | None = None


class RemoteIssuesQueryDefinition(QueryDefinition):
    type: Literal["query:remote-issues"]
    provider: ConfigValue
    directory_path: ConfigValue | None = None
    project: ConfigValue | None = None
    repo: ConfigValue | None = None
    query_mode: ConfigValue | None = None
    wiql: ConfigValue | None = None
    current_iteration: ConfigValue | None = None
    team: ConfigValue | None = None
    iteration: ConfigValue | None = None
    area_path: ConfigValue | None = None
    states: ConfigValue | None = None
    types: ConfigValue | None = None
    tags_any: ConfigValue | None = None
    text_search: ConfigValue | None = None
    state: ConfigValue | None = None
    labels_any: ConfigValue | None = None
    labels_all: ConfigValue | None = None
    assignee: ConfigValue | None = None
    author: ConfigValue | None = None
    mentioned: ConfigValue | None = None
    milestone: ConfigValue | None = None
    since: ConfigValue | None = None
    top: ConfigValue | None = None
    skip: ConfigValue | None = None


class PullRequestListQueryDefinition(QueryDefinition):
    type: Literal["query:pull-request:list"]
    provider: ConfigValue
    repo: ConfigValue
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

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip()
            if normalized in {"ado", "azure-devops", "azure_devops"}:
                return "azure_devops"
        return value

    @field_validator("tags")
    @classmethod
    def validate_tag_filter(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, str):
            return value
        raise ValueError("query:pull-request:list tags must be a string")


class PullRequestFeedbackQueryDefinition(QueryDefinition):
    type: Literal["query:pull-request:feedback"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None
    cursor: ConfigValue | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip()
            if normalized in {"ado", "azure-devops", "azure_devops"}:
                return "azure_devops"
        return value


class CommunicationMessagesQueryDefinition(QueryDefinition):
    type: Literal["query:communication:messages"]
    communication_target_id: str
    limit: ConfigValue | None = None
    cursor: ConfigValue | None = None
    before_cursor: ConfigValue | None = None
    thread_id: ConfigValue | None = None
    directory_path: ConfigValue | None = None

    @field_validator("communication_target_id", mode="before")
    @classmethod
    def normalize_communication_target_id(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError("query:communication:messages requires communication_target_id")
        return text

    @model_validator(mode="after")
    def validate_cursor_filters(self) -> "CommunicationMessagesQueryDefinition":
        if (
            self.cursor is not None
            and self.before_cursor is not None
            and _is_static_config_value(self.cursor)
            and _is_static_config_value(self.before_cursor)
        ):
            raise ValueError(
                "query:communication:messages cursor and before_cursor are mutually exclusive"
            )
        return self


class PullRequestGetQueryDefinition(QueryDefinition):
    type: Literal["query:pull-request:get"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None
    include_reviewers: ConfigValue | None = None
    include_commits: ConfigValue | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip()
            if normalized in {"ado", "azure-devops", "azure_devops"}:
                return "azure_devops"
        return value


class PullRequestWorkItemsQueryDefinition(QueryDefinition):
    type: Literal["query:pull-request:work-items"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip()
            if normalized in {"ado", "azure-devops", "azure_devops"}:
                return "azure_devops"
        return value


class ClassificationEvaluateQueryDefinition(QueryDefinition):
    type: Literal["query:classification:evaluate"]
    state: ConfigValue
    questions: ConfigValue | None = None
    profile_id: ConfigValue | None = None
    provider: ConfigValue | None = None
    model: ConfigValue | None = None

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, value):
        if value is None or (isinstance(value, str) and value.startswith("${{")):
            return value
        if not isinstance(value, dict) or not value or any(not key.strip() for key in value):
            raise ValueError("questions must be a nonempty map or a binding")
        for question in value.values():
            if not _classification_has_binding(question):
                Question.model_validate(question)
        return value

    @model_validator(mode="after")
    def validate_classification_source(self):
        if (self.questions is None) == (self.profile_id is None):
            raise ValueError("provide exactly one of questions or profile_id")
        if self.profile_id is not None and (self.provider is not None or self.model is not None):
            raise ValueError("profile calls cannot override provider or model")
        payload = {key: getattr(self, key) for key in ("state", "questions", "profile_id", "provider", "model")}
        if not _classification_has_binding(payload):
            EvaluationRequest.model_validate(payload)
        return self


def _classification_has_binding(value):
    if isinstance(value, str):
        return "${{" in value
    if isinstance(value, dict):
        return any(_classification_has_binding(item) for item in value.values())
    if isinstance(value, list):
        return any(_classification_has_binding(item) for item in value)
    return False


class SemanticStoreStatusQueryDefinition(QueryDefinition):
    type: Literal["query:semantic:store-status"]
    collection_name: ConfigValue | None = None
    directory_path: ConfigValue | None = None


class SemanticSearchQueryDefinition(QueryDefinition):
    type: Literal["query:semantic:search"]
    collection_name: ConfigValue
    query_text: ConfigValue
    filter: ConfigValue | None = None
    limit: ConfigValue | None = None
    score_threshold: ConfigValue | None = None
    directory_path: ConfigValue | None = None


class SemanticGetQueryDefinition(QueryDefinition):
    type: Literal["query:semantic:get"]
    collection_name: ConfigValue
    point_ids: ConfigValue
    directory_path: ConfigValue | None = None


def _is_static_config_value(value: ConfigValue | None) -> bool:
    return not (isinstance(value, str) and "${{" in value)
