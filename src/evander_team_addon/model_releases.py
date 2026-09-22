"""Team releases models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from evander.models import DirectoryPath, utc_now
from evander.worker.models import WorkerFailureCause, WorkerResourceLimits
from evander_team_addon.git_naming import validate_branch_name

from .model_common import (
    HumanReviewFindingTargetScope,
    ReleaseGateKind,
    TeamModel,
    TeamRecordState,
    _ensure_unique,
    _ensure_utc,
    _optional_text,
    _required_text,
    _unique_text_list,
)
from .model_deliverables_git import (
    DeliverableReviewManifest,
    DeliverableVerification,
)
from .model_organization import (
    HumanReviewChecklistItem,
    ReleaseGateStage,
    ReleaseReviewRequirement,
    ReleaseVerificationCommand,
    _legacy_release_gate_sequence,
)
from .model_reviews import (
    ReviewCriterionResult,
)


class ReleaseVerificationWaiver(TeamModel):
    deliverable_id: str
    verification_status: Literal["partial", "not_run", "unknown", "missing"]
    reason: str
    actor_kind: Literal["agent", "operator"]
    actor_id: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("deliverable_id", "reason", "actor_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class ReleaseScopeValidation(TeamModel):
    state: Literal["pending", "passed", "blocked", "not_required"] = "pending"
    base_commit_sha: str | None = None
    release_commit_sha: str | None = None
    reviewed_commit_shas: list[str] = Field(default_factory=list)
    unexpected_commit_shas: list[str] = Field(default_factory=list)
    unexpected_paths: list[str] = Field(default_factory=list)
    validated_at: datetime | None = None

    @field_validator("base_commit_sha", "release_commit_sha", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator(
        "reviewed_commit_shas",
        "unexpected_commit_shas",
        "unexpected_paths",
        mode="before",
    )
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


class ReleaseVerificationCommandResult(TeamModel):
    name: str
    state: Literal["pending", "running", "passed", "failed", "timed_out"] = "pending"
    failure_kind: Literal["candidate", "command", "infrastructure", "timeout"] | None = None
    job_id: str | None = None
    exit_code: int | None = None
    failure_cause: WorkerFailureCause | None = None
    duration_seconds: float | None = None
    log_excerpt: str | None = None
    execution_generation: int = Field(default=0, ge=0)
    execution_outcome: Literal["success", "command_failure", "infrastructure", "timeout"] | None = (
        None
    )
    policy_outcome: Literal["passed", "baseline_exception", "failed", "infrastructure"] | None = (
        None
    )
    evidence: dict[str, Any] | None = None
    log_artifact: str | None = None
    toolchain_mounts: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("job_id", "log_excerpt", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class ReleaseVerificationRun(TeamModel):
    id: str
    release_package_id: str
    candidate_commit_sha: str
    session_id: str
    state: Literal["pending", "running", "passed", "failed", "timed_out", "cancelled"] = "pending"
    provenance: Literal["server_executed", "server_acceptance_reuse"] = "server_executed"
    evidence_refs: list[str] = Field(default_factory=list)
    container_image: str | None = None
    resource_limits: WorkerResourceLimits | None = None
    verifier_settings_snapshotted: bool = False
    attempt_number: int = Field(default=1, ge=1)
    execution_generation: int = Field(default=0, ge=0)
    generation_history: list[dict[str, Any]] = Field(default_factory=list)
    failure_kind: Literal["candidate", "command", "infrastructure", "timeout"] | None = None
    supersedes_verification_run_id: str | None = None
    superseded_by_verification_run_id: str | None = None
    retry_reason: str | None = None
    retry_requested_by: str | None = None
    correction_task_id: str | None = None
    command_results: list[ReleaseVerificationCommandResult] = Field(default_factory=list)
    current_command_index: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None

    @field_validator(
        "id", "release_package_id", "candidate_commit_sha", "session_id", mode="before"
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "error",
        "supersedes_verification_run_id",
        "superseded_by_verification_run_id",
        "retry_reason",
        "retry_requested_by",
        "correction_task_id",
        "container_image",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "evidence_refs")

    @model_validator(mode="after")
    def _validate_retry_audit(self) -> "ReleaseVerificationRun":
        if self.supersedes_verification_run_id is not None and (
            self.retry_reason is None or self.retry_requested_by is None
        ):
            raise ValueError(
                "superseding release verification requires retry_reason and retry_requested_by"
            )
        return self

    @model_validator(mode="after")
    def _validate_reused_evidence(self) -> "ReleaseVerificationRun":
        if self.provenance == "server_acceptance_reuse" and (
            self.state != "passed" or not self.evidence_refs
        ):
            raise ValueError("reused server acceptance verification requires passed evidence")
        return self


class ReleaseGateFailure(TeamModel):
    stage: Literal[
        "verification",
        "review",
        "human_review",
        "configuration",
        "lineage",
        "gate_obligation",
    ]
    kind: Literal[
        "candidate",
        "infrastructure",
        "timeout",
        "blocked",
        "rejected",
        "changes_requested",
        "configuration",
        "convergence",
        "waiver_required",
    ]
    source_id: str | None = None
    message: str
    occurred_at: datetime = Field(default_factory=utc_now)

    @field_validator("message", mode="before")
    @classmethod
    def _required_message(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("source_id", mode="before")
    @classmethod
    def _optional_source_id(cls, value: object) -> str | None:
        return _optional_text(value)


class ReleaseCandidateReviewEvidence(TeamModel):
    review_request_id: str
    reviewer_agent_id: str
    review_profile_id: str | None = None
    reviewed_commit_sha: str
    criteria: list[ReviewCriterionResult] = Field(default_factory=list)

    @field_validator("review_request_id", "reviewer_agent_id", "reviewed_commit_sha", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("review_profile_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class ReleaseCandidateDeliverableEvidence(TeamModel):
    deliverable_id: str
    artifact_refs: list[str] = Field(default_factory=list)
    case_note_ids: list[str] = Field(default_factory=list)
    context_snapshot_ids: list[str] = Field(default_factory=list)
    verification: DeliverableVerification | None = None
    verification_waiver: ReleaseVerificationWaiver | None = None
    accepted_reviews: list[ReleaseCandidateReviewEvidence] = Field(default_factory=list)
    review_manifest: DeliverableReviewManifest | None = None
    feature_branch: str | None = None
    feature_commit_sha: str | None = None

    @field_validator("deliverable_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def _artifact_refs(cls, value: object) -> list[str]:
        return _unique_text_list(value, "artifact_refs")

    @field_validator("feature_branch", "feature_commit_sha", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class HumanReviewRequirementSnapshot(TeamModel):
    directory_path: str
    configured_group_ids: list[str]
    initially_eligible_user_ids: list[str]
    checklist: list[HumanReviewChecklistItem] = Field(default_factory=list)
    test_instructions: str = ""
    preparation_mode: Literal["manual", "workflow"] = "manual"

    @field_validator("directory_path", mode="before")
    @classmethod
    def _directory_path(cls, value: object) -> str:
        return DirectoryPath(segments=_required_text(value, "directory_path")).as_posix()

    @field_validator(
        "configured_group_ids",
        "initially_eligible_user_ids",
        mode="before",
    )
    @classmethod
    def _id_lists(cls, value: object, info) -> list[str]:
        values = _unique_text_list(value or [], info.field_name)
        if info.field_name == "configured_group_ids" and not values:
            raise ValueError("human review snapshot requires configured groups")
        return values

    @field_validator("test_instructions", mode="before")
    @classmethod
    def _test_instructions(cls, value: object) -> str:
        return _optional_text(value) or ""

    @model_validator(mode="after")
    def _unique_checklist_ids(self) -> "HumanReviewRequirementSnapshot":
        _ensure_unique([item.id for item in self.checklist], "human review checklist items")
        return self


class HumanReviewAssignment(TeamModel):
    eligible_user_ids: list[str] = Field(default_factory=list)
    explicitly_assigned_user_ids: list[str] = Field(default_factory=list)
    assigned_by_user_id: str | None = None
    assigned_at: datetime | None = None

    @field_validator(
        "eligible_user_ids",
        "explicitly_assigned_user_ids",
        mode="before",
    )
    @classmethod
    def _id_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @field_validator("assigned_by_user_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class HumanReviewTestTarget(TeamModel):
    url: str | None = None
    alias: str | None = None
    expires_at: datetime | None = None
    instructions: str = ""
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("url", "alias", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("instructions", mode="before")
    @classmethod
    def _instructions(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("expires_at", mode="after")
    @classmethod
    def _expires_at(cls, value: datetime | None) -> datetime | None:
        return _ensure_utc(value) if value is not None else None

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "evidence_refs")

    @model_validator(mode="after")
    def _require_safe_target(self) -> "HumanReviewTestTarget":
        if self.url is None and self.alias is None:
            raise ValueError("human review test target requires a URL or alias")
        return self


class HumanReviewFindingSnapshot(TeamModel):
    finding_id: str
    text: str
    target_scope: HumanReviewFindingTargetScope = HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED
    target_deliverable_ids: list[str] = Field(default_factory=list)
    correction_task_id: str | None = None
    proposed_resolution: str | None = None
    resolution_evidence_refs: list[str] = Field(default_factory=list)
    proposed_by_deliverable_ids: list[str] = Field(default_factory=list)

    @field_validator("finding_id", "text", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "target_deliverable_ids",
        "resolution_evidence_refs",
        "proposed_by_deliverable_ids",
        mode="before",
    )
    @classmethod
    def _id_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @field_validator("correction_task_id", "proposed_resolution", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="before")
    @classmethod
    def _infer_legacy_target_scope(cls, value: object) -> object:
        if not isinstance(value, Mapping) or value.get("target_scope") is not None:
            return value
        normalized = dict(value)
        if normalized.get("target_deliverable_ids"):
            normalized["target_scope"] = HumanReviewFindingTargetScope.DELIVERABLES
        return normalized

    @model_validator(mode="after")
    def _target_scope_matches_ids(self) -> "HumanReviewFindingSnapshot":
        if (
            self.target_scope == HumanReviewFindingTargetScope.RELEASE_CANDIDATE
            and self.target_deliverable_ids
        ):
            raise ValueError("release-candidate human findings cannot target deliverables")
        if (
            self.target_scope == HumanReviewFindingTargetScope.DELIVERABLES
            and not self.target_deliverable_ids
        ):
            raise ValueError("deliverable-scoped human findings require target deliverables")
        return self


class HumanReviewRequest(TeamModel):
    id: str
    release_package_id: str
    candidate_commit_sha: str
    candidate_tree_hash: str
    requirement_snapshot: HumanReviewRequirementSnapshot
    current_assignment: HumanReviewAssignment = Field(default_factory=HumanReviewAssignment)
    assignment_generation: int = Field(default=1, ge=1)
    state: Literal[
        "preparing",
        "pending",
        "blocked",
        "approved",
        "changes_requested",
        "cancelled",
    ] = "pending"
    preparation_generation: int = Field(default=1, ge=1)
    test_target: HumanReviewTestTarget | None = None
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    ready_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator(
        "id",
        "release_package_id",
        "candidate_commit_sha",
        "candidate_tree_hash",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "HumanReviewRequest":
        terminal = self.state in {"approved", "changes_requested", "cancelled"}
        if terminal and self.completed_at is None:
            raise ValueError("terminal human review request requires completed_at")
        if not terminal and self.completed_at is not None:
            raise ValueError("nonterminal human review request cannot have completed_at")
        if self.state == "pending" and self.ready_at is None:
            raise ValueError("pending human review request requires ready_at")
        return self


class HumanReviewDecision(TeamModel):
    id: str
    human_review_request_id: str
    reviewer_user_id: str
    reviewer_display_name_snapshot: str
    decision: Literal["approved", "changes_requested", "blocked"]
    checklist_results: dict[str, Any] = Field(default_factory=dict)
    confirmed_finding_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    request_id: str
    decided_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "human_review_request_id",
        "reviewer_user_id",
        "reviewer_display_name_snapshot",
        "request_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("confirmed_finding_ids", "evidence_refs", mode="before")
    @classmethod
    def _id_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)


class HumanReviewFinding(TeamModel):
    id: str
    source_human_review_request_id: str
    text: str
    target_scope: HumanReviewFindingTargetScope = HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED
    target_deliverable_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    correction_task_id: str
    state: Literal["open", "resolved"] = "open"
    proposed_resolution: str | None = None
    resolution_evidence_refs: list[str] = Field(default_factory=list)
    proposed_by_deliverable_ids: list[str] = Field(default_factory=list)
    resolved_by_human_review_request_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    @field_validator(
        "id",
        "source_human_review_request_id",
        "text",
        "correction_task_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "target_deliverable_ids",
        "evidence_refs",
        "resolution_evidence_refs",
        "proposed_by_deliverable_ids",
        mode="before",
    )
    @classmethod
    def _id_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @field_validator(
        "proposed_resolution",
        "resolved_by_human_review_request_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="before")
    @classmethod
    def _infer_legacy_target_scope(cls, value: object) -> object:
        if not isinstance(value, Mapping) or value.get("target_scope") is not None:
            return value
        normalized = dict(value)
        if normalized.get("target_deliverable_ids"):
            normalized["target_scope"] = HumanReviewFindingTargetScope.DELIVERABLES
        return normalized

    @model_validator(mode="after")
    def _target_scope_matches_ids(self) -> "HumanReviewFinding":
        if (
            self.target_scope == HumanReviewFindingTargetScope.RELEASE_CANDIDATE
            and self.target_deliverable_ids
        ):
            raise ValueError("release-candidate human findings cannot target deliverables")
        if (
            self.target_scope == HumanReviewFindingTargetScope.DELIVERABLES
            and not self.target_deliverable_ids
        ):
            raise ValueError("deliverable-scoped human findings require target deliverables")
        return self

    @model_validator(mode="after")
    def _validate_resolution(self) -> "HumanReviewFinding":
        if self.state == "resolved":
            if self.resolved_at is None or self.resolved_by_human_review_request_id is None:
                raise ValueError("resolved human finding requires resolving request and time")
        elif self.resolved_at is not None or self.resolved_by_human_review_request_id is not None:
            raise ValueError("open human finding cannot have resolution audit fields")
        return self


class ReleaseGateObligationWaiver(TeamModel):
    id: str
    gate_kind: ReleaseGateKind
    predecessor_release_package_id: str
    candidate_commit_sha: str
    source_ids: list[str] = Field(default_factory=list)
    reason: str
    authorized_by: str
    authorized_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "predecessor_release_package_id",
        "candidate_commit_sha",
        "reason",
        "authorized_by",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("source_ids", mode="before")
    @classmethod
    def _source_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "source_ids")


class ReleaseCandidateRepositoryScope(TeamModel):
    managed_repo_id: str
    directory_path: str = "root"
    base_branch: str | None = None
    canonical_branch: str

    @field_validator("managed_repo_id", mode="before")
    @classmethod
    def _managed_repo_id(cls, value: object) -> str:
        normalized = _required_text(value, "managed_repo_id")
        if re.fullmatch(r"mr_[0-9a-f]{20}", normalized) is None:
            raise ValueError("managed_repo_id must match mr_<20-hex>")
        return normalized

    @field_validator("directory_path", mode="before")
    @classmethod
    def _directory_path(cls, value: object) -> str:
        return DirectoryPath(segments=str(value or "root").strip() or "root").as_posix()

    @field_validator("base_branch", mode="before")
    @classmethod
    def _base_branch(cls, value: object) -> str | None:
        normalized = _optional_text(value)
        return validate_branch_name(normalized) if normalized is not None else None

    @field_validator("canonical_branch", mode="before")
    @classmethod
    def _canonical_branch(cls, value: object) -> str:
        return validate_branch_name(_required_text(value, "canonical_branch"))


class ReleaseCandidateManifest(TeamModel):
    schema_version: Literal[1, 2, 3, 4] = 4
    evidence_status: Literal["complete", "legacy_partial"] = "complete"
    project_id: str
    release_package_id: str
    base_commit_sha: str | None = None
    candidate_commit_sha: str
    candidate_tree_hash: str | None = None
    release_branch: str
    repository_scope: ReleaseCandidateRepositoryScope | None = None
    automatic_release_branch_publication: bool = True
    deliverables: list[ReleaseCandidateDeliverableEvidence] = Field(default_factory=list)
    scope_validation: ReleaseScopeValidation | None = None
    gate_sequence: list[ReleaseGateStage] = Field(default_factory=list)
    verification_commands: list[ReleaseVerificationCommand] = Field(default_factory=list)
    verification_max_generations: int = Field(default=3, ge=1, le=10)
    verification_log_retention_days: int = Field(default=30, ge=1, le=365)
    review_requirements: list[ReleaseReviewRequirement] = Field(default_factory=list)
    human_review_requirement: HumanReviewRequirementSnapshot | None = None
    predecessor_release_package_id: str | None = None
    correction_deliverable_ids: list[str] = Field(default_factory=list)
    carried_human_findings: list[HumanReviewFindingSnapshot] = Field(default_factory=list)
    contribution_graph: dict[str, list[str]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_gate_sequence(cls, value: object) -> object:
        if not isinstance(value, Mapping) or "gate_sequence" in value:
            return value
        normalized = dict(value)
        normalized["gate_sequence"] = _legacy_release_gate_sequence(value)
        return normalized

    @model_validator(mode="after")
    def _validate_gate_sequence(self) -> "ReleaseCandidateManifest":
        kinds = [stage.kind for stage in self.gate_sequence]
        expected = set(ReleaseGateKind)
        if len(kinds) != len(expected) or set(kinds) != expected:
            raise ValueError(
                "release manifest gate_sequence must contain each gate kind exactly once"
            )
        return self

    @field_validator(
        "project_id",
        "release_package_id",
        "candidate_commit_sha",
        "release_branch",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "base_commit_sha",
        "candidate_tree_hash",
        "predecessor_release_package_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("correction_deliverable_ids", mode="before")
    @classmethod
    def _correction_deliverable_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "correction_deliverable_ids")


class ReleaseCancellation(TeamModel):
    actor_id: str
    reason: str = ""
    cancelled_at: datetime = Field(default_factory=utc_now)

    @field_validator("actor_id", mode="before")
    @classmethod
    def _actor_id(cls, value: object) -> str:
        return _required_text(value, "actor_id")


class ReleasePackage(TeamModel):
    id: str
    project_id: str
    owner_agent_id: str | None = None
    work_lineage_id: str | None = None
    summary: str | None = None
    supersedes_release_package_id: str | None = None
    lineage_root_release_package_id: str | None = None
    lineage_sequence: int = Field(default=0, ge=0)
    deliverable_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    feature_branches: list[str] = Field(default_factory=list)
    release_branch: str | None = None
    canonical_branch: str | None = None
    git_base_branch: str | None = None
    published_commit_sha: str | None = None
    base_commit_sha: str | None = None
    canonical_base_commit_sha: str | None = None
    candidate_commit_sha: str | None = None
    candidate_tree_hash: str | None = None
    approved_commit_sha: str | None = None
    review_correction_task_id: str | None = None
    verification_run_ids: list[str] = Field(default_factory=list)
    review_request_ids: list[str] = Field(default_factory=list)
    review_cohort_id: str | None = None
    review_cohort_sealed_at: datetime | None = None
    manifest: ReleaseCandidateManifest | None = None
    gate_failure: ReleaseGateFailure | None = None
    git_promotion_record_ids: list[str] = Field(default_factory=list)
    policy_version: int = Field(default=1, ge=1)
    verification_waivers: list[ReleaseVerificationWaiver] = Field(default_factory=list)
    gate_obligation_waivers: list[ReleaseGateObligationWaiver] = Field(default_factory=list)
    scope_validation: ReleaseScopeValidation | None = None
    state: TeamRecordState = TeamRecordState.PENDING
    cancellation: ReleaseCancellation | None = None
    published_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_cancellation(self) -> "ReleasePackage":
        if (self.state == TeamRecordState.CANCELLED) != (self.cancellation is not None):
            raise ValueError(
                "cancelled release requires cancellation audit; cancellation is terminal"
            )
        if self.cancellation is not None and (self.published_at or self.published_commit_sha):
            raise ValueError("published release cannot be cancelled")
        return self

    @model_validator(mode="before")
    @classmethod
    def _infer_initial_canonical_base(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        if (
            value.get("canonical_base_commit_sha") is not None
            or value.get("supersedes_release_package_id") is not None
            or value.get("base_commit_sha") is None
        ):
            return value
        normalized = dict(value)
        normalized["canonical_base_commit_sha"] = value.get("base_commit_sha")
        return normalized

    @field_validator("id", "project_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "deliverable_ids",
        "artifact_refs",
        "feature_branches",
        "git_promotion_record_ids",
        "verification_run_ids",
        "review_request_ids",
        mode="before",
    )
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @field_validator(
        "work_lineage_id",
        "release_branch",
        "canonical_branch",
        "owner_agent_id",
        "supersedes_release_package_id",
        "lineage_root_release_package_id",
        "published_commit_sha",
        "base_commit_sha",
        "canonical_base_commit_sha",
        "candidate_commit_sha",
        "candidate_tree_hash",
        "approved_commit_sha",
        "review_correction_task_id",
        "review_cohort_id",
        "summary",
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

    @field_validator("manifest", mode="before")
    @classmethod
    def _empty_manifest_is_legacy(cls, value: object) -> object | None:
        if value in ({}, None):
            return None
        if isinstance(value, Mapping) and "schema_version" not in value:
            deliverable_ids = _unique_text_list(
                value.get("deliverable_ids"),
                "manifest.deliverable_ids",
            )
            feature_branches = _unique_text_list(
                value.get("feature_branches"),
                "manifest.feature_branches",
            )
            paired_branches = (
                feature_branches if len(feature_branches) == len(deliverable_ids) else []
            )
            return {
                "schema_version": 1,
                "evidence_status": "legacy_partial",
                "project_id": value.get("project_id"),
                "release_package_id": value.get("release_package_id"),
                "candidate_commit_sha": value.get("candidate_commit_sha"),
                "candidate_tree_hash": value.get("candidate_tree_hash"),
                "release_branch": value.get("release_branch"),
                "deliverables": [
                    {
                        "deliverable_id": deliverable_id,
                        "feature_branch": (paired_branches[index] if paired_branches else None),
                    }
                    for index, deliverable_id in enumerate(deliverable_ids)
                ],
                "verification_commands": value.get("verification_commands") or [],
                "review_requirements": value.get("review_requirements") or [],
            }
        return value

    @model_validator(mode="after")
    def _validate_review_cohort(self) -> "ReleasePackage":
        if (self.review_cohort_id is None) != (self.review_cohort_sealed_at is None):
            raise ValueError(
                "release review_cohort_id and review_cohort_sealed_at must be set together"
            )
        return self
