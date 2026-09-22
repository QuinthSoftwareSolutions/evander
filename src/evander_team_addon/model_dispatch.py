"""Team dispatch models."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from evander.models import DirectoryPath, utc_now
from evander_team_addon.git_naming import validate_branch_name

from .model_common import (
    WORKTREE_CLEANUP_INVENTORY_SAMPLE_LIMIT,
    EphemeralAssignmentState,
    TeamModel,
    _optional_text,
    _required_text,
    _unique_text_list,
    summarize_worktree_cleanup_inventory,
)


class TeamDispatchStatus(str, Enum):
    PREPARING = "preparing"
    LEASED = "leased"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"


TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS = 300


class TeamDispatchMcpCapabilityStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    MISSING = "missing"


class WorktreeCleanupState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    SUPERSEDED = "superseded"
    OPERATOR_ACTION_REQUIRED = "operator_action_required"
    # Legacy persisted value. Model and SQLite migrations translate it to
    # either pending automatic retry or operator action required.
    RECOVERY_REQUIRED = "recovery_required"


WORKTREE_CLEANUP_TRANSIENT_FAILURE_CHECKS = frozenset(
    {
        "active_dispatch_reference",
        "active_git_context_reference",
        "backend_cleanup_failed",
        "ignored_path_cleanup_failed",
        "processing_lease_expired",
        "task_not_cleanup_eligible",
        "worktree_path_changed",
        "worktree_registration_active",
        "worktree_removal_failed",
        "worktree_removal_incomplete",
    }
)


class TeamDispatchMcpCapabilityReceipt(TeamModel):
    """Compact evidence for MCP capabilities required by one Team dispatch."""

    required_tool_names: list[str] = Field(default_factory=list)
    attempt: int = Field(default=1, ge=1)
    grant_confirmed_at: datetime | None = None
    verification_status: TeamDispatchMcpCapabilityStatus = TeamDispatchMcpCapabilityStatus.PENDING
    observed_transport_session_id: str | None = None
    observed_at: datetime | None = None
    observed_tool_fingerprint: str | None = None
    missing_tool_names: list[str] = Field(default_factory=list)
    diagnostic: str | None = Field(default=None, max_length=1_000)

    @field_validator("required_tool_names", "missing_tool_names", mode="before")
    @classmethod
    def _tool_names(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @field_validator(
        "observed_transport_session_id",
        "observed_tool_fingerprint",
        "diagnostic",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class TeamDispatchReviewRoutingAuthority(TeamModel):
    kind: Literal["reviewer_without_assigned_review_work"]
    reviewer_agent_id: str
    project_id: str
    task_id: str
    deliverable_id: str
    consumed_at: datetime | None = None
    consumed_by_review_request_id: str | None = None

    @field_validator(
        "reviewer_agent_id",
        "project_id",
        "task_id",
        "deliverable_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("consumed_by_review_request_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_consumption(self) -> "TeamDispatchReviewRoutingAuthority":
        if (self.consumed_at is None) != (self.consumed_by_review_request_id is None):
            raise ValueError(
                "dispatch review routing authority consumption requires both "
                "consumed_at and consumed_by_review_request_id"
            )
        return self


class TeamDispatch(TeamModel):
    id: str
    agent_id: str
    session_id: str
    cause_kind: str
    cause_id: str | None = None
    work_lineage_id: str | None = None
    native_thread_id: str | None = None
    native_thread_observed_at: datetime | None = None
    task_ids: list[str] = Field(default_factory=list)
    # Only this dispatch may release these claims after a pre-submission failure.
    claimed_role_task_ids: list[str] = Field(default_factory=list)
    blocker_ids: list[str] = Field(default_factory=list)
    review_request_ids: list[str] = Field(default_factory=list)
    inbox_item_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    managed_repo_ids: list[str] = Field(default_factory=list)
    source_evidence_snapshot_ids: list[str] = Field(default_factory=list)
    chat_message_ids: list[str] = Field(default_factory=list)
    review_routing_authorities: list[TeamDispatchReviewRoutingAuthority] = Field(
        default_factory=list
    )
    initial_release_decision_deliverable_ids: list[str] = Field(default_factory=list)
    mcp_capability_receipt: TeamDispatchMcpCapabilityReceipt | None = None
    status: TeamDispatchStatus = TeamDispatchStatus.PREPARING
    created_at: datetime = Field(default_factory=utc_now)
    preparation_expires_at: datetime | None = None
    queued_at: datetime | None = None
    requested_lease_seconds: int = Field(default=900, ge=1)
    activation_completed_at: datetime | None = None
    session_metadata_mirrored_at: datetime | None = None
    role_description_fingerprint: str | None = None
    wake_work_fingerprint: str | None = None
    wake_work_record_fingerprints: dict[str, str] = Field(default_factory=dict)
    action_feedback_dispatch_ids: list[str] = Field(default_factory=list)
    prepared_at: datetime | None = None
    leased_at: datetime | None = None
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    @field_validator("id", "agent_id", "session_id", "cause_kind", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "cause_id",
        "work_lineage_id",
        "native_thread_id",
        "error",
        "role_description_fingerprint",
        "wake_work_fingerprint",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("wake_work_record_fingerprints", mode="before")
    @classmethod
    def _wake_work_record_fingerprints(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("wake_work_record_fingerprints must be an object")
        return {
            _required_text(key, "wake work record id"): _required_text(
                fingerprint,
                "wake work record fingerprint",
            )
            for key, fingerprint in value.items()
        }

    @field_validator(
        "task_ids",
        "claimed_role_task_ids",
        "blocker_ids",
        "review_request_ids",
        "inbox_item_ids",
        "project_ids",
        "managed_repo_ids",
        "source_evidence_snapshot_ids",
        "chat_message_ids",
        "action_feedback_dispatch_ids",
        "initial_release_decision_deliverable_ids",
        mode="before",
    )
    @classmethod
    def _scope_ids(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "TeamDispatch":
        if self.chat_message_ids and self.cause_kind != "team_chat":
            raise ValueError("chat message ids require a team_chat cause")
        authority_keys = [
            (authority.reviewer_agent_id, authority.deliverable_id)
            for authority in self.review_routing_authorities
        ]
        if len(authority_keys) != len(set(authority_keys)):
            raise ValueError("dispatch review routing authorities must be unique")
        if self.review_routing_authorities and self.cause_kind != "manager_fallback":
            raise ValueError("dispatch review routing authorities require manager_fallback cause")
        if self.initial_release_decision_deliverable_ids and self.cause_kind != "manager_fallback":
            raise ValueError("dispatch initial release decisions require manager_fallback cause")
        if self.status == TeamDispatchStatus.LEASED:
            if self.leased_at is None or self.lease_expires_at is None:
                raise ValueError("leased dispatch requires leased_at and lease_expires_at")
        if (
            self.status
            in {
                TeamDispatchStatus.COMPLETED,
                TeamDispatchStatus.EXPIRED,
                TeamDispatchStatus.FAILED,
            }
            and self.completed_at is None
        ):
            raise ValueError("terminal dispatch requires completed_at")
        if self.status == TeamDispatchStatus.FAILED and self.error is None:
            raise ValueError("failed dispatch requires error")
        return self


class WorktreeCleanupCandidate(TeamModel):
    id: str
    agent_id: str
    session_id: str
    task_id: str
    project_id: str
    git_work_context_id: str
    managed_repo_id: str
    directory_path: str = "root"
    execution_backend: Literal["libvirt_vm", "container_worker_vm"]
    worktree_path: str
    branch: str
    base_ref: str | None = None
    expected_head: str | None = None
    retained_commit_sha: str | None = None
    state: WorktreeCleanupState = WorktreeCleanupState.PENDING
    reason: str
    eligible_at: datetime = Field(default_factory=utc_now)
    attempt_count: int = Field(default=0, ge=0)
    automatic_retry_count: int = Field(default=0, ge=0)
    last_safety_check: str | None = None
    last_error: str | None = None
    operator_action_required_at: datetime | None = None
    operator_action_reason: str | None = None
    superseded_by_git_work_context_id: str | None = None
    last_operator_retry_at: datetime | None = None
    last_operator_retry_by: str | None = None
    last_operator_retry_reason: str | None = None
    ignored_file_inventory: list[str] = Field(default_factory=list)
    ignored_file_count: int = Field(default=0, ge=0)
    ignored_file_inventory_sha256: str | None = None
    ignored_file_inventory_truncated: bool = False
    ignored_bytes: int = Field(default=0, ge=0)
    worktree_bytes: int = Field(default=0, ge=0)
    reclaimed_bytes: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _bounded_ignored_file_evidence(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        migrated = dict(data)
        if not str(migrated.get("git_work_context_id") or "").strip():
            candidate_id = str(migrated.get("id") or "unknown").strip() or "unknown"
            migrated["git_work_context_id"] = f"legacy:{candidate_id}"
        inventory = migrated.get("ignored_file_inventory") or []
        if not all(
            field_name in migrated
            for field_name in (
                "ignored_file_count",
                "ignored_file_inventory_sha256",
                "ignored_file_inventory_truncated",
            )
        ):
            sample, count, digest, truncated = summarize_worktree_cleanup_inventory(inventory)
            migrated["ignored_file_inventory"] = sample
            migrated["ignored_file_count"] = count
            migrated["ignored_file_inventory_sha256"] = digest
            migrated["ignored_file_inventory_truncated"] = truncated
        raw_state = migrated.get("state")
        state = raw_state.value if isinstance(raw_state, WorktreeCleanupState) else raw_state
        if state == WorktreeCleanupState.RECOVERY_REQUIRED.value:
            failed_check = str(migrated.get("last_safety_check") or "").strip()
            if failed_check in WORKTREE_CLEANUP_TRANSIENT_FAILURE_CHECKS:
                migrated["state"] = WorktreeCleanupState.PENDING.value
                migrated.setdefault("automatic_retry_count", 0)
            else:
                migrated["state"] = WorktreeCleanupState.OPERATOR_ACTION_REQUIRED.value
                if migrated.get("operator_action_required_at") is None:
                    migrated["operator_action_required_at"] = (
                        migrated.get("updated_at") or migrated.get("eligible_at") or utc_now()
                    )
                if migrated.get("operator_action_reason") is None:
                    migrated["operator_action_reason"] = failed_check or "legacy_recovery_required"
        return migrated

    @field_validator(
        "id",
        "agent_id",
        "session_id",
        "task_id",
        "project_id",
        "git_work_context_id",
        "managed_repo_id",
        "worktree_path",
        "branch",
        "reason",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("directory_path", mode="before")
    @classmethod
    def _directory_path(cls, value: object) -> str:
        return DirectoryPath(segments=str(value or "root").strip() or "root").as_posix()

    @field_validator(
        "base_ref",
        "expected_head",
        "retained_commit_sha",
        "last_safety_check",
        "last_error",
        "operator_action_reason",
        "superseded_by_git_work_context_id",
        "last_operator_retry_by",
        "last_operator_retry_reason",
        "ignored_file_inventory_sha256",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("ignored_file_inventory", mode="before")
    @classmethod
    def _ignored_file_inventory(cls, value: object) -> list[str]:
        inventory = sorted(_unique_text_list(value or [], "ignored_file_inventory"))
        if len(inventory) > WORKTREE_CLEANUP_INVENTORY_SAMPLE_LIMIT:
            raise ValueError("ignored_file_inventory exceeds the bounded evidence sample limit")
        return inventory

    @model_validator(mode="after")
    def _validate_operator_action_evidence(self) -> "WorktreeCleanupCandidate":
        if self.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED:
            if self.operator_action_required_at is None:
                raise ValueError(
                    "operator-action-required cleanup requires operator_action_required_at"
                )
            if self.operator_action_reason is None:
                raise ValueError("operator-action-required cleanup requires operator_action_reason")
        if self.state == WorktreeCleanupState.SUPERSEDED:
            if self.superseded_by_git_work_context_id is None:
                raise ValueError("superseded cleanup requires superseded_by_git_work_context_id")
            if self.completed_at is None:
                raise ValueError("superseded cleanup requires completed_at")
        return self


class TeamDispatchActivityStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class TeamOutboxStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class TeamDispatchActivity(TeamModel):
    id: str
    tool_name: str
    caller_session_id: str
    caller_identity: str
    agent_id: str | None = None
    governance_group_id: str | None = None
    assignment_id: str | None = None
    dispatch_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    status: TeamDispatchActivityStatus = TeamDispatchActivityStatus.SUCCESS
    progress_classification: str = "none"
    action_record_id: str | None = None
    audit_record_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    correction_context_summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "tool_name", "caller_session_id", "caller_identity", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "agent_id",
        "governance_group_id",
        "assignment_id",
        "dispatch_id",
        "request_id",
        "correlation_id",
        "causation_id",
        "action_record_id",
        "audit_record_id",
        "error_code",
        "error_message",
        "correction_context_summary",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("progress_classification", mode="before")
    @classmethod
    def _progress_classification(cls, value: object) -> str:
        return _optional_text(value) or "none"

    @model_validator(mode="after")
    def _validate_domain_identity(self) -> "TeamDispatchActivity":
        if not (self.agent_id or self.governance_group_id or self.assignment_id):
            raise ValueError(
                "dispatch activity requires agent_id, governance_group_id, or assignment_id"
            )
        return self


class TeamIdempotencyRecord(TeamModel):
    id: str
    tool_name: str
    caller_identity: str
    scope_kind: str
    scope_id: str
    request_id: str
    normalized_payload_digest: str
    response_payload: dict[str, Any] = Field(default_factory=dict)
    created_domain_ids: list[str] = Field(default_factory=list)
    created_event_ids: list[str] = Field(default_factory=list)
    status: str = "succeeded"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "tool_name",
        "caller_identity",
        "scope_kind",
        "scope_id",
        "request_id",
        "normalized_payload_digest",
        "status",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("response_payload", mode="before")
    @classmethod
    def _response_payload(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("response_payload must be a JSON object")
        return dict(value)

    @field_validator("created_domain_ids", "created_event_ids", mode="before")
    @classmethod
    def _created_ids(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


class TeamOutboxRecord(TeamModel):
    id: str
    intent_type: str
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: TeamOutboxStatus = TeamOutboxStatus.PENDING
    available_at: datetime = Field(default_factory=utc_now)
    attempt_count: int = Field(default=0, ge=0)
    claimed_at: datetime | None = None
    claimed_by: str | None = None
    claim_token: str | None = None
    claim_expires_at: datetime | None = None
    completed_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "intent_type", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "aggregate_type",
        "aggregate_id",
        "idempotency_key",
        "claimed_by",
        "claim_token",
        "last_error_code",
        "last_error_message",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("payload", mode="before")
    @classmethod
    def _payload(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("payload must be a JSON object")
        return dict(value)


class EphemeralAssignment(TeamModel):
    id: str
    parent_agent_id: str
    project_id: str
    task_id: str
    session_id: str
    title: str
    prompt: str
    state: EphemeralAssignmentState = EphemeralAssignmentState.REQUESTED
    artifact_refs: list[str] = Field(default_factory=list)
    agent_branch: str | None = None
    commit_sha: str | None = None
    summary: str | None = None
    error: str | None = None
    model: str | None = None
    agent_provider: str | None = None
    execution_backend: str | None = None
    container_image: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    ttl_hours: int | None = Field(default=None, ge=1)
    directory_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "parent_agent_id",
        "project_id",
        "task_id",
        "session_id",
        "title",
        "prompt",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "agent_branch",
        "commit_sha",
        "summary",
        "error",
        "model",
        "agent_provider",
        "execution_backend",
        "container_image",
        "directory_path",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("artifact_refs", "allowed_tools", mode="before")
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        return dict(value)


class GitPromotionSource(TeamModel):
    source_branch: str
    expected_commit_sha: str

    @field_validator("source_branch", "expected_commit_sha", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class GitPromotionRecord(TeamModel):
    id: str
    project_id: str
    actor_agent_id: str
    source_branch: str
    sources: list[GitPromotionSource] = Field(default_factory=list)
    target_branch: str
    promotion_kind: Literal[
        "review",
        "feature",
        "correction_aggregate",
        "release",
        "canonical_push",
    ]
    task_id: str | None = None
    deliverable_id: str | None = None
    release_package_id: str | None = None
    git_base_branch: str | None = None
    base_commit_sha: str | None = None
    state: Literal["pending", "succeeded", "failed", "retired"] = "pending"
    commit_sha: str | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "project_id",
        "actor_agent_id",
        "source_branch",
        "target_branch",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "task_id",
        "deliverable_id",
        "release_package_id",
        "base_commit_sha",
        "commit_sha",
        "error",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("git_base_branch", mode="before")
    @classmethod
    def _git_base_branch(cls, value: object) -> str | None:
        normalized = _optional_text(value)
        return validate_branch_name(normalized) if normalized is not None else None

    @field_validator("outputs", mode="before")
    @classmethod
    def _outputs(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("outputs must be a JSON object")
        return dict(value)
