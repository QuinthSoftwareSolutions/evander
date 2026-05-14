from __future__ import annotations

from typing import Literal

from pydantic import model_validator

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
