from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import field_validator

from evander.events.models import (
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
    run_global_maintenance: ConfigValue | None = None
    worktree_cleanup_limit: ConfigValue | None = None
    worktree_cleanup_grace_seconds: ConfigValue | None = None
    worktree_cleanup_retry_seconds: ConfigValue | None = None
    worktree_cleanup_recovery_seconds: ConfigValue | None = None
    worktree_cleanup_dry_run: ConfigValue | None = None
    resume_mode: ConfigValue | None = None


class TeamWakeGovernanceManagersEffectDefinition(EffectDefinition):
    type: Literal["effect:team:wake-governance-managers"]
    governance_group_id: ConfigValue | None = None
    limit: ConfigValue | None = None
    lease_seconds: ConfigValue | None = None
    wake_interval_seconds: ConfigValue | None = None
    # Accepted only so a persisted pre-v12 runtime pack can continue to load. Governance
    # resolves the threshold from each member Team's durable configuration.
    cost_warning_ratio: ConfigValue | None = None
    resume_mode: ConfigValue | None = None


class TeamPublishOutboxEventsEffectDefinition(EffectDefinition):
    type: Literal["effect:team:publish-outbox-events"]
    limit: ConfigValue | None = None
    intent_types: ConfigValue | None = None
    outbox_record_ids: ConfigValue | None = None
    claim_ttl_seconds: ConfigValue | None = None
    retry_after_seconds: ConfigValue | None = None
    worker_id: ConfigValue | None = None

    @field_validator("intent_types", "outbox_record_ids")
    @classmethod
    def validate_string_list(cls, value: ConfigValue | None, info) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError(
            "effect:team:publish-outbox-events "
            f"{info.field_name} must be a JSON array"
        )


class TeamFinalizeAgentDispatchEffectDefinition(EffectDefinition):
    type: Literal["effect:team:finalize-agent-dispatch"]
    session_id: ConfigValue
    dispatch_id: ConfigValue
    agent_id: ConfigValue | None = None
    expected_revision: ConfigValue | None = None


class TeamEnqueueOperatorChatMessageEffectDefinition(EffectDefinition):
    type: Literal["effect:team:enqueue-operator-chat-message"]
    conversation_id: ConfigValue | None = None
    agent_id: ConfigValue | None = None
    operator_user_id: ConfigValue
    operator_display_name: ConfigValue | None = None
    body: ConfigValue
    provider: ConfigValue
    communication_target_id: ConfigValue
    source_message_id: ConfigValue
    source_thread_id: ConfigValue | None = None
    source_author_id: ConfigValue
    source_author_name: ConfigValue | None = None
    allow_team_mutations: ConfigValue | None = None
    authorized_author_ids: ConfigValue | None = None


class TeamRecordExternalChatDeliveryEffectDefinition(EffectDefinition):
    type: Literal["effect:team:record-external-chat-delivery"]
    delivery_id: ConfigValue
    success: ConfigValue
    provider_message_id: ConfigValue | None = None
    error: ConfigValue | None = None


class TeamRecordOperatorEscalationDeliveryEffectDefinition(EffectDefinition):
    type: Literal["effect:team:record-operator-escalation-delivery"]
    escalation_id: ConfigValue
    success: ConfigValue
    provider_message_id: ConfigValue | None = None
    error: ConfigValue | None = None
    fallback_used: ConfigValue | None = None


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


class TeamCreateReviewCohortEffectDefinition(EffectDefinition):
    type: Literal["effect:team:create-review-cohort"]
    actor_agent_id: ConfigValue
    deliverable_id: ConfigValue
    reviews: ConfigValue
    publish_wake_events: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("reviews")
    @classmethod
    def validate_reviews(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, (list, str)):
            return value
        raise ValueError("effect:team:create-review-cohort reviews must be a JSON array")


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
    git_base_branch: ConfigValue | None = None
    dependency_task_ids: ConfigValue | None = None
    integration_boundary: ConfigValue | None = None
    deliverable_contract: ConfigValue | None = None
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


class TeamRegisterAgentEffectDefinition(EffectDefinition):
    type: Literal["effect:team:register-agent"]
    session_id: ConfigValue
    team_id: ConfigValue
    role_id: ConfigValue
    agent_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    reports_to_agent_id: ConfigValue | None = None
    display_name: ConfigValue | None = None
    expected_revision: ConfigValue | None = None


class TeamRetireAgentEffectDefinition(EffectDefinition):
    type: Literal["effect:team:retire-agent"]
    agent_id: ConfigValue
    expected_session_id: ConfigValue
    expected_team_id: ConfigValue
    expected_revision: ConfigValue | None = None


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


class TeamRecordHumanReviewPreparationResultEffectDefinition(EffectDefinition):
    type: Literal["effect:team:record-human-review-preparation-result"]
    human_review_request_id: ConfigValue
    candidate_commit_sha: ConfigValue
    candidate_tree_hash: ConfigValue
    preparation_generation: ConfigValue
    target: ConfigValue | None = None
    error: ConfigValue | None = None
    request_id: ConfigValue | None = None
    recorded_by: ConfigValue | None = None

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError(
            "effect:team:record-human-review-preparation-result target must be a JSON object"
        )


class TeamVerifyReleaseCandidateEffectDefinition(EffectDefinition):
    type: Literal["effect:team:verify-release-candidate"]
    release_package_id: ConfigValue
    verification_run_id: ConfigValue
    candidate_commit_sha: ConfigValue


class TeamPublishReviewBranchEffectDefinition(EffectDefinition):
    type: Literal["effect:team:publish-review-branch"]
    managed_repo_id: ConfigValue
    directory_path: ConfigValue | None = None
    target_branch: ConfigValue
    source_branch: ConfigValue
    expected_commit_sha: ConfigValue


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
    managed_repo_id: ConfigValue
    directory_path: ConfigValue | None = None
    source_branches: ConfigValue
    target_branch: ConfigValue
    base_branch: ConfigValue | None = None
    base_commit_sha: ConfigValue | None = None
    required_source_ancestor_commit_sha: ConfigValue | None = None
    expected_source_commits: ConfigValue
    allowed_changed_paths: ConfigValue | None = None
    commit_message: ConfigValue | None = None

    @field_validator("source_branches")
    @classmethod
    def validate_source_branches(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, (list, str)):
            return value
        raise ValueError(
            "effect:team:consolidate-git-branches source_branches must be a JSON array"
        )

    @field_validator("expected_source_commits")
    @classmethod
    def validate_expected_source_commits(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError(
            "effect:team:consolidate-git-branches expected_source_commits must be a JSON object"
        )

    @field_validator("allowed_changed_paths")
    @classmethod
    def validate_allowed_changed_paths(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError(
            "effect:team:consolidate-git-branches allowed_changed_paths must be a JSON array"
        )


class TeamIndexMemoryEffectDefinition(EffectDefinition):
    type: Literal["effect:team:index-memory"]
    memory_id: ConfigValue | None = None
    memory_ids: ConfigValue | None = None
    team_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    wait: ConfigValue | None = None

    @field_validator("memory_ids")
    @classmethod
    def validate_memory_ids(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError("effect:team:index-memory memory_ids must be a JSON array")


class TeamReindexMemoryEffectDefinition(EffectDefinition):
    type: Literal["effect:team:reindex-memory"]
    team_id: ConfigValue
    project_id: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    wait: ConfigValue | None = None


class TeamStateQueryDefinition(QueryDefinition):
    type: Literal["query:team:state"]
    team_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    agent_id: ConfigValue | None = None
    include_records: ConfigValue | None = None


class TeamMemoryQueryDefinition(QueryDefinition):
    type: Literal["query:team:memory"]
    team_id: ConfigValue | None = None
    project_id: ConfigValue | None = None
    codebase_id: ConfigValue | None = None
    agent_id: ConfigValue | None = None
    query_text: ConfigValue | None = None
    semantic: ConfigValue | None = None
    kinds: ConfigValue | None = None
    statuses: ConfigValue | None = None
    tags_any: ConfigValue | None = None
    limit: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    score_threshold: ConfigValue | None = None

    @field_validator("kinds", "statuses", "tags_any")
    @classmethod
    def validate_string_list(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (list, str)):
            return value
        raise ValueError("query:team:memory list filters must be JSON arrays")


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
        "lease_summary_version": {"type": "integer"},
        "lease_summaries": {"type": "array"},
        "leases": {"type": "array"},
        "lease_summaries_truncated": {"type": "boolean"},
        "coalesced": {"type": "boolean"},
        "chat_dispatches": {"type": "array"},
        "dispatches": {"type": "array"},
        "prepared_project_repos": {"type": "array"},
        "failed_project_repo_preparations": {"type": "array"},
        "git_work_contexts": {"type": "array"},
        "review_artifact_imports": {"type": "array"},
        "review_artifact_import_errors": {"type": "array"},
        "preparation_blocker_ids": {"type": "array"},
        "transient_conflicts": {"type": "array"},
        "retry_event_ids": {"type": "array"},
        "retry_scheduled_count": {"type": "integer"},
        "cost_guard_blocked_team_ids": {"type": "array"},
        "cost_guard_blocked_teams": {"type": "array"},
        "worktree_cleanup": {"type": "object"},
        "worktree_cleanup_error": {"type": ["string", "null"]},
        "errors": {"type": "array"},
    },
    "effect:team:wake-governance-managers": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "leased_count": {"type": "integer"},
        "lease_summary_version": {"type": "integer"},
        "lease_summaries": {"type": "array"},
        "leases": {"type": "array"},
        "lease_summaries_truncated": {"type": "boolean"},
        "coalesced": {"type": "boolean"},
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
    "effect:team:enqueue-operator-chat-message": {
        "success": {"type": "boolean"},
        "queued": {"type": "boolean"},
        "conversation_id": {"type": "string"},
        "message_id": {"type": "string"},
        "dispatch_id": {"type": "string"},
        "duplicate": {"type": "boolean"},
    },
    "effect:team:record-external-chat-delivery": {
        "success": {"type": "boolean"},
        "delivery_id": {"type": "string"},
        "status": {"type": "string"},
        "attempt_count": {"type": "integer"},
        "provider_message_id": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:record-operator-escalation-delivery": {
        "success": {"type": "boolean"},
        "escalation_id": {"type": "string"},
        "manager_agent_id": {"type": "string"},
        "status": {"type": "string"},
        "attempt_count": {"type": "integer"},
        "provider_message_id": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
        "fallback_used": {"type": "boolean"},
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
    "effect:team:create-review-cohort": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "review_cohort_id": {"type": ["string", "null"]},
        "review_cohort_sealed_at": {"type": ["string", "null"]},
        "deliverable_id": {"type": ["string", "null"]},
        "actor_agent_id": {"type": ["string", "null"]},
        "created": {"type": "boolean"},
        "created_review_request_ids": {"type": "array"},
        "existing_review_request_ids": {"type": "array"},
        "created_inbox_item_ids": {"type": "array"},
        "published_event_ids": {"type": "array"},
        "events": {"type": "array"},
        "review_requests": {"type": "array"},
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
    "effect:team:register-agent": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "created": {"type": "boolean"},
        "agent_id": {"type": ["string", "null"]},
        "session_id": {"type": ["string", "null"]},
        "team_id": {"type": ["string", "null"]},
        "project_id": {"type": ["string", "null"]},
        "agent": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
        "expected_revision": {"type": ["integer", "null"]},
        "current_revision": {"type": ["integer", "null"]},
    },
    "effect:team:retire-agent": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "retired": {"type": "boolean"},
        "already_retired": {"type": "boolean"},
        "agent_id": {"type": ["string", "null"]},
        "session_id": {"type": ["string", "null"]},
        "blockers": {"type": "array"},
        "agent": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
        "expected_revision": {"type": ["integer", "null"]},
        "current_revision": {"type": ["integer", "null"]},
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
    "effect:team:record-human-review-preparation-result": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "human_review_request_id": {"type": "string"},
        "outbox_record_ids": {"type": "array"},
        "idempotent_replay": {"type": "boolean"},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:verify-release-candidate": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "release_package_id": {"type": "string"},
        "verification_run_id": {"type": "string"},
        "verification_state": {"type": "string"},
        "created_review_request_ids": {"type": "array"},
        "created_inbox_item_ids": {"type": "array"},
        "published_event_ids": {"type": "array"},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:publish-review-branch": {
        "success": {"type": "boolean"},
        "managed_repo_id": {"type": ["string", "null"]},
        "review_branch": {"type": ["string", "null"]},
        "target_branch": {"type": ["string", "null"]},
        "commit_sha": {"type": ["string", "null"]},
        "message": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "effect:team:mark-release-published": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "release_package_id": {"type": "string"},
        "state": {"type": ["string", "null"]},
        "created_inbox_item_ids": {"type": "array"},
        "superseded_release_package_ids": {"type": "array"},
        "resolved_inbox_item_ids": {"type": "array"},
        "ignored_terminal_state": {"type": "boolean"},
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
        "error_code": {"type": ["string", "null"]},
        "unexpected_commit_shas": {"type": "array"},
        "base_commit_sha": {"type": ["string", "null"]},
        "source_manifests": {"type": "object"},
        "unexpected_paths": {"type": "array"},
        "tree_hash": {"type": ["string", "null"]},
    },
    "effect:team:index-memory": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "indexed_count": {"type": "integer"},
        "deleted_count": {"type": "integer"},
        "memory_ids": {"type": "array"},
        "index_results": {"type": "array"},
        "delete_results": {"type": "array"},
        "errors": {"type": "array"},
    },
    "effect:team:reindex-memory": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "team_id": {"type": "string"},
        "project_id": {"type": ["string", "null"]},
        "indexed_count": {"type": "integer"},
        "deleted_count": {"type": "integer"},
        "memory_ids": {"type": "array"},
        "index_results": {"type": "array"},
        "delete_results": {"type": "array"},
        "errors": {"type": "array"},
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
    "query:team:memory": {
        "success": {"type": "boolean"},
        "revision": {"type": "integer"},
        "memories": {"type": "array"},
        "records": {"type": "array"},
        "count": {"type": "integer"},
        "limit": {"type": "integer"},
        "semantic": {"type": "boolean"},
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
