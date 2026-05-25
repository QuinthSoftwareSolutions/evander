from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator

from agent_sandbox.events.models import ConfigValue, QueryDefinition


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


class AgentArtifactTextQueryDefinition(QueryDefinition):
    type: Literal["query:agent:artifact-text"]
    session_id: ConfigValue
    path: ConfigValue
    vm_user: ConfigValue | None = None


class IssueGetQueryDefinition(QueryDefinition):
    type: Literal["query:issue:get"]
    issue_id: ConfigValue


class AdoWorkItemsQueryDefinition(QueryDefinition):
    type: Literal["query:ado:work-items"]
    directory_path: ConfigValue | None = None
    project: ConfigValue | None = None
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


class PullRequestListQueryDefinition(QueryDefinition):
    type: Literal["query:pull-request:list"]
    provider: ConfigValue
    repo: ConfigValue
    directory_path: ConfigValue | None = None
    organization_url: ConfigValue | None = None
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
    organization_url: ConfigValue | None = None
    project: ConfigValue | None = None
    cursor: ConfigValue | None = None

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip()
            if normalized in {"ado", "azure-devops", "azure_devops"}:
                return "azure_devops"
        return value


class PullRequestGetQueryDefinition(QueryDefinition):
    type: Literal["query:pull-request:get"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    directory_path: ConfigValue | None = None
    organization_url: ConfigValue | None = None
    project: ConfigValue | None = None
    include_reviewers: ConfigValue | None = None
    include_commits: ConfigValue | None = None

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip()
            if normalized in {"ado", "azure-devops", "azure_devops"}:
                return "azure_devops"
        return value


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
