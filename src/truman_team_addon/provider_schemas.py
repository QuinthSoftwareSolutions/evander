from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import field_validator

from agent_sandbox.events.models import ConfigValue, EffectDefinition, QueryDefinition


class TeamWakeDueAgentsEffectDefinition(EffectDefinition):
    type: Literal["effect:team:wake-due-agents"]
    team_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    agent_id: ConfigValue | None = None
    limit: ConfigValue | None = None
    lease_seconds: ConfigValue | None = None
    wake_interval_seconds: ConfigValue | None = None
    max_no_progress_count: ConfigValue | None = None
    action_artifact_path: ConfigValue | None = None
    resume_mode: ConfigValue | None = None


class TeamApplyAgentActionEffectDefinition(EffectDefinition):
    type: Literal["effect:team:apply-agent-action"]
    action: ConfigValue | None = None
    valid: ConfigValue | None = None
    error: ConfigValue | None = None
    session_id: ConfigValue | None = None
    dispatch_id: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:team:apply-agent-action action must be a JSON object")


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


class TeamApplyEphemeralActionEffectDefinition(EffectDefinition):
    type: Literal["effect:team:apply-ephemeral-action"]
    action: ConfigValue | None = None
    valid: ConfigValue | None = None
    error: ConfigValue | None = None
    session_id: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:team:apply-ephemeral-action action must be a JSON object")


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
        raise ValueError(
            "effect:team:record-git-promotion result_outputs must be a JSON object"
        )


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
        raise ValueError(
            "effect:team:mark-release-published result_outputs must be a JSON object"
        )


class TeamConsolidateGitBranchesEffectDefinition(EffectDefinition):
    type: Literal["effect:team:consolidate-git-branches"]
    host_repo_path: ConfigValue
    source_branches: ConfigValue
    target_branch: ConfigValue
    base_branch: ConfigValue | None = None

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


class TeamAgentActionArtifactQueryDefinition(QueryDefinition):
    type: Literal["query:team:agent-action-artifact"]
    session_id: ConfigValue
    path: ConfigValue | None = None
    vm_user: ConfigValue | None = None


class TeamSessionRoutingQueryDefinition(QueryDefinition):
    type: Literal["query:team:session-routing"]
    session_id: ConfigValue
    dispatch_id: ConfigValue | None = None


TEAM_OUTPUT_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "effect:team:wake-due-agents": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "leased_count": {"type": "integer"},
        "leases": {"type": "array"},
        "dispatches": {"type": "array"},
        "errors": {"type": "array"},
    },
    "effect:team:apply-agent-action": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "agent_id": {"type": ["string", "null"]},
        "applied": {"type": "boolean"},
        "errors": {"type": "array"},
        "created_inbox_item_ids": {"type": "array"},
        "created_deliverable_ids": {"type": "array"},
        "created_task_ids": {"type": "array"},
        "created_status_request_ids": {"type": "array"},
        "created_review_request_ids": {"type": "array"},
        "decided_review_request_ids": {"type": "array"},
        "updated_goal_ids": {"type": "array"},
        "operator_escalation_event_ids": {"type": "array"},
        "git_promotion_event_ids": {"type": "array"},
        "release_package_ids": {"type": "array"},
        "created_ephemeral_assignment_ids": {"type": "array"},
        "ephemeral_events": {"type": "array"},
        "accepted_ephemeral_assignment_ids": {"type": "array"},
        "rejected_ephemeral_assignment_ids": {"type": "array"},
        "updated_task_ids": {"type": "array"},
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
    "effect:team:apply-ephemeral-action": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "assignment_id": {"type": ["string", "null"]},
        "session_id": {"type": ["string", "null"]},
        "state": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
        "event": {"type": ["object", "null"]},
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
    "query:team:agent-action-artifact": {
        "success": {"type": "boolean"},
        "valid": {"type": "boolean"},
        "session_id": {"type": "string"},
        "path": {"type": "string"},
        "action": {"type": "object"},
        "error": {"type": ["string", "null"]},
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
