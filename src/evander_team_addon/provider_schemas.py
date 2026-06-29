from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import field_validator

from agent_sandbox.events.models import (
    ConfigValue,
    EffectDefinition,
    QueryDefinition,
    TriggerDefinition,
)


class TeamDeliverableCreatedTriggerDefinition(TriggerDefinition):
    type: Literal["trigger:team:deliverable-created"]
    team_id: str | None = None
    project_id: str | None = None
    producer_agent_id: str | None = None


class TeamWakeDueAgentsEffectDefinition(EffectDefinition):
    type: Literal["effect:team:wake-due-agents"]
    team_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    agent_id: ConfigValue | None = None
    limit: ConfigValue | None = None
    lease_seconds: ConfigValue | None = None
    wake_interval_seconds: ConfigValue | None = None
    max_no_progress_count: ConfigValue | None = None
    low_priority_wake_after_cycles: ConfigValue | None = None
    respect_wake_interval: ConfigValue | None = None
    resume_mode: ConfigValue | None = None


class TeamWakeGovernanceManagersEffectDefinition(EffectDefinition):
    type: Literal["effect:team:wake-governance-managers"]
    governance_group_id: ConfigValue | None = None
    limit: ConfigValue | None = None
    lease_seconds: ConfigValue | None = None
    wake_interval_seconds: ConfigValue | None = None
    cost_warning_ratio: ConfigValue | None = None
    resume_mode: ConfigValue | None = None


class TeamPublishOutboxEventsEffectDefinition(EffectDefinition):
    type: Literal["effect:team:publish-outbox-events"]
    limit: ConfigValue | None = None
    intent_types: ConfigValue | None = None
    claim_ttl_seconds: ConfigValue | None = None
    retry_after_seconds: ConfigValue | None = None
    worker_id: ConfigValue | None = None

    @field_validator("intent_types")
    @classmethod
    def validate_intent_types(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError("effect:team:publish-outbox-events intent_types must be a JSON array")


class TeamFinalizeAgentDispatchEffectDefinition(EffectDefinition):
    type: Literal["effect:team:finalize-agent-dispatch"]
    session_id: ConfigValue
    dispatch_id: ConfigValue
    agent_id: ConfigValue | None = None
    expected_revision: ConfigValue | None = None


class TeamCreateOperatorDirectiveEffectDefinition(EffectDefinition):
    type: Literal["effect:team:create-operator-directive"]
    team_id: ConfigValue
    message: ConfigValue
    recipient_agent_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    task_id: ConfigValue | None = None
    title: ConfigValue | None = None
    source: ConfigValue | None = None
    source_message_id: ConfigValue | None = None
    created_by: ConfigValue | None = None
    requesting_session_id: ConfigValue | None = None
    expected_revision: ConfigValue | None = None


class TeamCreateReviewRequestEffectDefinition(EffectDefinition):
    type: Literal["effect:team:create-review-request"]
    actor_agent_id: ConfigValue
    deliverable_id: ConfigValue
    reviewer_agent_id: ConfigValue
    review_request_id: ConfigValue | None = None
    review_profile_id: ConfigValue | None = None
    instructions: ConfigValue | None = None
    skip_if_existing: ConfigValue | None = None
    publish_wake_events: ConfigValue | None = None
    expected_revision: ConfigValue | None = None


class TeamCreateTaskEffectDefinition(EffectDefinition):
    type: Literal["effect:team:create-task"]
    team_id: ConfigValue
    project_id: ConfigValue
    owner_agent_id: ConfigValue
    priority: ConfigValue
    title: ConfigValue
    description: ConfigValue
    task_id: ConfigValue | None = None
    case_id: ConfigValue | None = None
    state: ConfigValue | None = None
    parent_task_id: ConfigValue | None = None
    dependency_task_ids: ConfigValue | None = None
    created_by: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("dependency_task_ids")
    @classmethod
    def validate_dependency_task_ids(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError("effect:team:create-task dependency_task_ids must be a JSON array")


class TeamCreateCaseEffectDefinition(EffectDefinition):
    type: Literal["effect:team:create-case"]
    case_id: ConfigValue | None = None
    team_id: ConfigValue
    title: ConfigValue
    description: ConfigValue | None = None
    project_id: ConfigValue | None = None
    status: ConfigValue | None = None
    priority: ConfigValue | None = None
    owner_agent_id: ConfigValue | None = None
    source: ConfigValue | None = None
    source_ref: ConfigValue | None = None
    tags: ConfigValue | None = None
    metadata: ConfigValue | None = None
    task_ids: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("tags", "task_ids")
    @classmethod
    def validate_string_list(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError("effect:team:create-case list fields must be JSON arrays")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:team:create-case metadata must be a JSON object")


class TeamUpdateCaseEffectDefinition(EffectDefinition):
    type: Literal["effect:team:update-case"]
    case_id: ConfigValue
    title: ConfigValue | None = None
    description: ConfigValue | None = None
    project_id: ConfigValue | None = None
    status: ConfigValue | None = None
    priority: ConfigValue | None = None
    owner_agent_id: ConfigValue | None = None
    source: ConfigValue | None = None
    source_ref: ConfigValue | None = None
    tags: ConfigValue | None = None
    metadata: ConfigValue | None = None
    task_ids: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("tags", "task_ids")
    @classmethod
    def validate_string_list(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError("effect:team:update-case list fields must be JSON arrays")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:team:update-case metadata must be a JSON object")


class TeamRequestEphemeralAgentsEffectDefinition(EffectDefinition):
    type: Literal["effect:team:request-ephemeral-agents"]
    parent_agent_id: ConfigValue
    requests: ConfigValue
    dispatch_id: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("requests")
    @classmethod
    def validate_requests(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, (list, str)):
            return value
        raise ValueError("effect:team:request-ephemeral-agents requests must be a JSON array")


class TeamMarkEphemeralSpawnedEffectDefinition(EffectDefinition):
    type: Literal["effect:team:mark-ephemeral-spawned"]
    assignment_id: ConfigValue
    session_id: ConfigValue | None = None
    create_result: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("create_result")
    @classmethod
    def validate_create_result(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:team:mark-ephemeral-spawned create_result must be a JSON object")


class TeamAcceptEphemeralDeliverableEffectDefinition(EffectDefinition):
    type: Literal["effect:team:accept-ephemeral-deliverable"]
    assignment_id: ConfigValue
    accepted: ConfigValue | None = None
    deliverable_id: ConfigValue | None = None
    rejection_reason: ConfigValue | None = None
    expected_revision: ConfigValue | None = None


class TeamMarkEphemeralCleanedUpEffectDefinition(EffectDefinition):
    type: Literal["effect:team:mark-ephemeral-cleaned-up"]
    assignment_id: ConfigValue | None = None
    session_id: ConfigValue | None = None
    destroy_result: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("destroy_result")
    @classmethod
    def validate_destroy_result(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError(
            "effect:team:mark-ephemeral-cleaned-up destroy_result must be a JSON object"
        )


class TeamRecordGitPromotionEffectDefinition(EffectDefinition):
    type: Literal["effect:team:record-git-promotion"]
    promotion_id: ConfigValue
    success: ConfigValue | None = None
    result_outputs: ConfigValue | None = None
    error: ConfigValue | None = None
    commit_sha: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("result_outputs")
    @classmethod
    def validate_result_outputs(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:team:record-git-promotion result_outputs must be a JSON object")


class TeamPublishReviewBranchEffectDefinition(EffectDefinition):
    type: Literal["effect:team:publish-review-branch"]
    host_repo_path: ConfigValue
    target_branch: ConfigValue
    bare_repo_name: ConfigValue | None = None
    bare_repo_path: ConfigValue | None = None
    source_branch: ConfigValue | None = None
    commit_sha: ConfigValue | None = None


class TeamMarkReleasePublishedEffectDefinition(EffectDefinition):
    type: Literal["effect:team:mark-release-published"]
    release_package_id: ConfigValue
    success: ConfigValue | None = None
    canonical_branch: ConfigValue | None = None
    published_commit_sha: ConfigValue | None = None
    result_outputs: ConfigValue | None = None
    error: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("result_outputs")
    @classmethod
    def validate_result_outputs(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:team:mark-release-published result_outputs must be a JSON object")


class TeamConsolidateGitBranchesEffectDefinition(EffectDefinition):
    type: Literal["effect:team:consolidate-git-branches"]
    host_repo_path: ConfigValue
    source_branches: ConfigValue
    target_branch: ConfigValue
    base_branch: ConfigValue | None = None
    bare_repo_path: ConfigValue | None = None
    bare_repo_name: ConfigValue | None = None

    @field_validator("source_branches")
    @classmethod
    def validate_source_branches(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, (list, str)):
            return value
        raise ValueError(
            "effect:team:consolidate-git-branches source_branches must be a JSON array"
        )


class TeamStateQueryDefinition(QueryDefinition):
    type: Literal["query:team:state"]
    team_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    agent_id: ConfigValue | None = None
    include_records: ConfigValue | None = None


class TeamSessionRoutingQueryDefinition(QueryDefinition):
    type: Literal["query:team:session-routing"]
    session_id: ConfigValue
    dispatch_id: ConfigValue | None = None


TEAM_OUTPUT_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "trigger:team:deliverable-created": {
        "deliverable_id": {"type": "string"},
        "team_id": {"type": "string"},
        "project_id": {"type": "string"},
        "task_id": {"type": "string"},
        "producer_agent_id": {"type": "string"},
        "state": {"type": "string"},
        "created_at": {"type": "string"},
        "artifact_refs": {"type": "array"},
        "review_request_ids": {"type": "array"},
        "deliverable": {"type": "object"},
        "task": {"type": ["object", "null"]},
        "project": {"type": ["object", "null"]},
        "team": {"type": ["object", "null"]},
        "git_refs": {"type": ["object", "null"]},
        "verification": {"type": ["object", "null"]},
    },
    "effect:team:wake-due-agents": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "leased_count": {"type": "integer"},
        "leases": {"type": "array"},
        "chat_dispatches": {"type": "array"},
        "dispatches": {"type": "array"},
        "prepared_project_repos": {"type": "array"},
        "failed_project_repo_preparations": {"type": "array"},
        "review_artifact_imports": {"type": "array"},
        "review_artifact_import_errors": {"type": "array"},
        "cost_guard_blocked_team_ids": {"type": "array"},
        "cost_guard_blocked_teams": {"type": "array"},
        "errors": {"type": "array"},
    },
    "effect:team:wake-governance-managers": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "leased_count": {"type": "integer"},
        "leases": {"type": "array"},
        "dispatches": {"type": "array"},
        "errors": {"type": "array"},
    },
    "effect:team:publish-outbox-events": {
        "success": {"type": "boolean"},
        "claimed_count": {"type": "integer"},
        "published_count": {"type": "integer"},
        "published_event_ids": {"type": "array"},
        "completed_outbox_record_ids": {"type": "array"},
        "failed_outbox_record_ids": {"type": "array"},
        "errors": {"type": "array"},
    },
    "effect:team:finalize-agent-dispatch": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "agent_id": {"type": ["string", "null"]},
        "dispatch_id": {"type": "string"},
        "finalized": {"type": "boolean"},
        "applied": {"type": "boolean"},
        "progress_made": {"type": "boolean"},
        "activity_count": {"type": "integer"},
        "successful_activity_count": {"type": "integer"},
        "failed_activity_count": {"type": "integer"},
        "context_activity": {"type": "boolean"},
        "events": {"type": "array"},
        "errors": {"type": "array"},
    },
    "effect:team:create-operator-directive": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "inbox_item_id": {"type": ["string", "null"]},
        "recipient_agent_id": {"type": ["string", "null"]},
        "team_id": {"type": ["string", "null"]},
        "project_id": {"type": ["string", "null"]},
        "task_id": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:create-review-request": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "review_request_id": {"type": ["string", "null"]},
        "deliverable_id": {"type": ["string", "null"]},
        "reviewer_agent_id": {"type": ["string", "null"]},
        "actor_agent_id": {"type": ["string", "null"]},
        "created": {"type": "boolean"},
        "created_review_request_ids": {"type": "array"},
        "existing_review_request_ids": {"type": "array"},
        "created_inbox_item_ids": {"type": "array"},
        "published_event_ids": {"type": "array"},
        "events": {"type": "array"},
        "review_request": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:create-task": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "task_id": {"type": ["string", "null"]},
        "team_id": {"type": ["string", "null"]},
        "project_id": {"type": ["string", "null"]},
        "owner_agent_id": {"type": ["string", "null"]},
        "created": {"type": "boolean"},
        "task": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:create-case": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "case_id": {"type": ["string", "null"]},
        "team_id": {"type": ["string", "null"]},
        "project_id": {"type": ["string", "null"]},
        "task_ids": {"type": "array"},
        "case": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:update-case": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "case_id": {"type": ["string", "null"]},
        "team_id": {"type": ["string", "null"]},
        "project_id": {"type": ["string", "null"]},
        "task_ids": {"type": "array"},
        "case": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:request-ephemeral-agents": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "created_ephemeral_assignment_ids": {"type": "array"},
        "events": {"type": "array"},
        "errors": {"type": "array"},
    },
    "effect:team:mark-ephemeral-spawned": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "assignment_id": {"type": "string"},
        "session_id": {"type": ["string", "null"]},
        "state": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:accept-ephemeral-deliverable": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "assignment_id": {"type": "string"},
        "accepted": {"type": "boolean"},
        "deliverable_id": {"type": ["string", "null"]},
        "state": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:mark-ephemeral-cleaned-up": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "assignment_id": {"type": ["string", "null"]},
        "session_id": {"type": ["string", "null"]},
        "state": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:record-git-promotion": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "promotion_id": {"type": "string"},
        "promotion_state": {"type": ["string", "null"]},
        "deliverable_id": {"type": ["string", "null"]},
        "release_package_id": {"type": ["string", "null"]},
        "created_deliverable_ids": {"type": "array"},
        "created_inbox_item_ids": {"type": "array"},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:publish-review-branch": {
        "success": {"type": "boolean"},
        "review_branch": {"type": ["string", "null"]},
        "target_branch": {"type": ["string", "null"]},
        "commit_sha": {"type": ["string", "null"]},
        "host_repo_path": {"type": ["string", "null"]},
        "message": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:mark-release-published": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "release_package_id": {"type": "string"},
        "state": {"type": ["string", "null"]},
        "created_inbox_item_ids": {"type": "array"},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:consolidate-git-branches": {
        "success": {"type": "boolean"},
        "target_branch": {"type": ["string", "null"]},
        "merged_branches": {"type": "array"},
        "commit_sha": {"type": ["string", "null"]},
        "conflict_branch": {"type": ["string", "null"]},
        "output": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "query:team:state": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "state": {"type": "object"},
        "teams": {"type": "object"},
        "projects": {"type": "object"},
        "agents": {"type": "object"},
        "tasks": {"type": "object"},
    },
    "query:team:session-routing": {
        "success": {"type": "boolean"},
        "session_id": {"type": "string"},
        "dispatch_id": {"type": ["string", "null"]},
        "kind": {"type": "string"},
        "agent_id": {"type": ["string", "null"]},
        "assignment_id": {"type": ["string", "null"]},
        "persistent": {"type": "boolean"},
        "ephemeral": {"type": "boolean"},
        "ignored": {"type": "boolean"},
        "reason": {"type": ["string", "null"]},
        "expected_dispatch_id": {"type": ["string", "null"]},
    },
}
