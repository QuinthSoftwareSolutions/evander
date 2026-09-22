"""Team deliverables git models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from evander.models import DirectoryPath, utc_now
from evander.worker.models import WorkerFailureCause, WorkerResourceLimits
from evander_team_addon.git_naming import validate_branch_name

from .model_common import (
    DependencyCompositionRecoveryState,
    GitWorkContextState,
    InitialReleaseDispositionState,
    TaskCompletionKind,
    TeamGitPreparationAttemptState,
    TeamModel,
    TeamRecordState,
    _optional_text,
    _required_text,
    _unique_text_list,
)
from .model_organization import (
    ReleaseVerificationCommand,
)
from .model_tasks import (
    OutcomeKindSnapshot,
    TaskCompletion,
)


class DeliverableGitRefs(TeamModel):
    branch_context_id: str | None = None
    agent_branch: str | None = None
    review_branch: str | None = None
    feature_branch: str | None = None
    release_branch: str | None = None
    commit_sha: str | None = None
    submitted_commit_sha: str | None = None
    reviewed_commit_sha: str | None = None
    feature_commit_sha: str | None = None
    release_commit_sha: str | None = None
    accepted_pinned_ref: str | None = None
    promotion_record_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "agent_branch",
        "review_branch",
        "feature_branch",
        "release_branch",
        "commit_sha",
        "submitted_commit_sha",
        "reviewed_commit_sha",
        "feature_commit_sha",
        "release_commit_sha",
        "accepted_pinned_ref",
        "branch_context_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("accepted_pinned_ref")
    @classmethod
    def _accepted_pinned_ref(cls, value: str | None) -> str | None:
        if (
            value is not None
            and re.fullmatch(
                r"refs/evander/team-reviews/[A-Za-z0-9_-]+",
                value,
            )
            is None
        ):
            raise ValueError("accepted_pinned_ref must be an immutable Team review ref")
        return value

    @field_validator("promotion_record_ids", mode="before")
    @classmethod
    def _promotion_record_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "promotion_record_ids")


class DeliverableAcceptanceVerificationWaiver(TeamModel):
    reason: str
    actor_kind: Literal["operator"] = "operator"
    actor_id: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("reason", "actor_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class DeliverableAcceptanceVerificationCommandResult(TeamModel):
    name: str
    state: Literal["pending", "running", "passed", "failed", "timed_out"] = "pending"
    failure_kind: Literal["candidate", "infrastructure", "timeout"] | None = None
    job_id: str | None = None
    exit_code: int | None = None
    failure_cause: WorkerFailureCause | None = None
    duration_seconds: float | None = None
    log_excerpt: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("job_id", "log_excerpt", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class DeliverableVerification(TeamModel):
    status: Literal[
        "pending",
        "running",
        "passed",
        "failed",
        "partial",
        "not_run",
        "unknown",
    ]
    summary: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: Literal["agent_reported", "server_release_equivalent"] = "agent_reported"
    run_id: str | None = None
    candidate_commit_sha: str | None = None
    verification_commands: list[ReleaseVerificationCommand] = Field(default_factory=list)
    command_results: list[DeliverableAcceptanceVerificationCommandResult] = Field(
        default_factory=list
    )
    container_image: str | None = None
    resource_limits: WorkerResourceLimits | None = None
    waiver: DeliverableAcceptanceVerificationWaiver | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator(
        "summary",
        "run_id",
        "candidate_commit_sha",
        "container_image",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[str]:
        return _unique_text_list(value, "evidence_refs")

    @model_validator(mode="after")
    def _validate_server_attestation(self) -> "DeliverableVerification":
        if self.provenance == "agent_reported":
            if (
                any(
                    value is not None
                    for value in (
                        self.run_id,
                        self.candidate_commit_sha,
                        self.container_image,
                        self.resource_limits,
                        self.waiver,
                        self.started_at,
                        self.finished_at,
                    )
                )
                or self.verification_commands
                or self.command_results
            ):
                raise ValueError(
                    "agent-reported verification cannot contain server-owned attestation fields"
                )
            if self.status in {"pending", "running"}:
                raise ValueError("agent-reported verification cannot be pending or running")
            return self
        if self.run_id is None or self.candidate_commit_sha is None:
            raise ValueError(
                "server release-equivalent verification requires run_id and candidate_commit_sha"
            )
        if len(self.command_results) != len(self.verification_commands):
            raise ValueError(
                "server release-equivalent verification commands and results must align"
            )
        if self.status == "passed" and not self.evidence_refs:
            raise ValueError("passed server verification requires evidence_refs")
        if self.waiver is not None and self.status not in {"partial", "not_run"}:
            raise ValueError("acceptance verification waivers apply only to partial or not_run")
        return self


class ReviewFindingRecord(TeamModel):
    id: str
    source_review_request_id: str
    source_deliverable_id: str
    owner_review_profile_id: str | None = None
    owner_role_id: str | None = None
    criterion_id: str | None = None
    text: str
    state: Literal["open", "resolved"] = "open"
    resolution: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    resolved_by_review_request_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    @field_validator(
        "id",
        "source_review_request_id",
        "source_deliverable_id",
        "text",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "owner_review_profile_id",
        "owner_role_id",
        "criterion_id",
        "resolution",
        "resolved_by_review_request_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[str]:
        return _unique_text_list(value, "evidence_refs")


class DeliverableReviewManifest(TeamModel):
    base_commit_sha: str
    reviewed_commit_sha: str
    tree_hash: str | None = None
    commit_shas: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)

    @field_validator("base_commit_sha", "reviewed_commit_sha", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("tree_hash", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("commit_shas", "changed_paths", mode="before")
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


class DeliverableOutcome(TeamModel):
    kind: str
    reference_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    definition_snapshot: OutcomeKindSnapshot | None = None

    @field_validator("kind", mode="before")
    @classmethod
    def _required_kind(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("reference_ids", "evidence_refs", mode="before")
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @model_validator(mode="after")
    def _validate_evidence(self) -> "DeliverableOutcome":
        if not self.reference_ids and not self.evidence_refs:
            raise ValueError("deliverable outcome requires reference_ids or evidence_refs")
        return self


class DeliverableAcceptanceDecision(TeamModel):
    decision: Literal["accepted", "rejected"]
    decided_by_agent_id: str
    decided_at: datetime = Field(default_factory=utc_now)
    reason: str | None = None

    @field_validator("decided_by_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _rejection_requires_reason(self) -> "DeliverableAcceptanceDecision":
        if self.decision == "rejected" and self.reason is None:
            raise ValueError("rejected deliverable decisions require a reason")
        return self


class DeliverableInitialReleaseDisposition(TeamModel):
    state: InitialReleaseDispositionState = InitialReleaseDispositionState.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by_actor: str | None = None
    reason: str | None = None
    release_package_id: str | None = None
    reopened_at: datetime | None = None
    reopened_by_actor: str | None = None
    reopen_reason: str | None = None

    @field_validator(
        "decided_by_actor",
        "reason",
        "release_package_id",
        "reopened_by_actor",
        "reopen_reason",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_state_evidence(self) -> "DeliverableInitialReleaseDisposition":
        reopen_values = (
            self.reopened_at,
            self.reopened_by_actor,
            self.reopen_reason,
        )
        if any(value is not None for value in reopen_values) and not all(
            value is not None for value in reopen_values
        ):
            raise ValueError(
                "initial release disposition reopen evidence requires actor, reason, and time"
            )
        if self.state == InitialReleaseDispositionState.PENDING:
            if any(
                value is not None
                for value in (
                    self.decided_at,
                    self.decided_by_actor,
                    self.reason,
                    self.release_package_id,
                )
            ):
                raise ValueError(
                    "pending initial release dispositions cannot contain decision evidence"
                )
            return self
        if self.decided_at is None or self.decided_by_actor is None:
            raise ValueError(
                "terminal initial release dispositions require actor and decision time"
            )
        if self.state == InitialReleaseDispositionState.RELEASE_REQUESTED:
            if self.release_package_id is None:
                raise ValueError(
                    "release-requested initial release dispositions require a release package"
                )
            return self
        if self.reason is None:
            raise ValueError("not-planned initial release dispositions require a reason")
        if self.release_package_id is not None:
            raise ValueError(
                "not-planned initial release dispositions cannot reference a release package"
            )
        return self


class Deliverable(TeamModel):
    id: str
    task_id: str
    producer_agent_id: str
    artifact_refs: list[str] = Field(default_factory=list)
    case_note_ids: list[str] = Field(default_factory=list)
    context_snapshot_ids: list[str] = Field(default_factory=list)
    summary: str
    state: TeamRecordState = TeamRecordState.PENDING
    review_request_ids: list[str] = Field(default_factory=list)
    git_refs: DeliverableGitRefs = Field(default_factory=DeliverableGitRefs)
    verification: DeliverableVerification | None = None
    outcomes: list[DeliverableOutcome] = Field(default_factory=list)
    source_deliverable_ids: list[str] = Field(default_factory=list)
    completion: TaskCompletion | None = None
    carried_findings: list[ReviewFindingRecord] = Field(default_factory=list)
    review_manifest: DeliverableReviewManifest | None = None
    dependency_composition_manifest: DependencyCompositionManifest | None = None
    acceptance_policy_version: Literal[1, 2] = 1
    acceptance_recipient_agent_id: str | None = None
    acceptance_decision: DeliverableAcceptanceDecision | None = None
    initial_release_disposition: DeliverableInitialReleaseDisposition | None = None
    supersedes_deliverable_id: str | None = None
    superseded_by_deliverable_id: str | None = None
    supersession_reason: str | None = None
    superseded_at: datetime | None = None
    superseded_by_actor: str | None = None
    review_cohort_id: str | None = None
    review_cohort_sealed_at: datetime | None = None
    rework_requested_at: datetime | None = None
    rework_requested_by_agent_id: str | None = None
    rework_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "task_id", "producer_agent_id", "summary", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "supersedes_deliverable_id",
        "superseded_by_deliverable_id",
        "supersession_reason",
        "superseded_by_actor",
        "review_cohort_id",
        "rework_requested_by_agent_id",
        "rework_reason",
        "acceptance_recipient_agent_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator(
        "artifact_refs",
        "review_request_ids",
        "source_deliverable_ids",
        mode="before",
    )
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @model_validator(mode="after")
    def _validate_acceptance_decision(self) -> "Deliverable":
        decision = self.acceptance_decision
        if decision is None:
            return self
        if self.acceptance_recipient_agent_id is None:
            raise ValueError("deliverable acceptance decisions require an acceptance recipient")
        if decision.decided_by_agent_id != self.acceptance_recipient_agent_id:
            raise ValueError(
                "deliverable acceptance decision must be made by its acceptance recipient"
            )
        if decision.decision == "rejected" and self.state != TeamRecordState.REJECTED:
            raise ValueError(
                "a deliverable rejected by its acceptance recipient must remain rejected"
            )
        # The decision records the original direct handoff. Later formal review or
        # promotion processing may legitimately move a previously accepted deliverable.
        return self

    @model_validator(mode="after")
    def _validate_rework_request(self) -> "Deliverable":
        values = (
            self.rework_requested_at,
            self.rework_requested_by_agent_id,
            self.rework_reason,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "deliverable rework request requires rework_requested_at, "
                "rework_requested_by_agent_id, and rework_reason"
            )
        if self.rework_requested_at is not None and self.state != TeamRecordState.ACCEPTED:
            raise ValueError("only an accepted deliverable can carry a rework request")
        return self

    @model_validator(mode="after")
    def _validate_review_cohort(self) -> "Deliverable":
        if (self.review_cohort_id is None) != (self.review_cohort_sealed_at is None):
            raise ValueError("review_cohort_id and review_cohort_sealed_at must be set together")
        return self

    @model_validator(mode="after")
    def _validate_completion(self) -> "Deliverable":
        if self.id in self.source_deliverable_ids:
            raise ValueError("deliverable cannot name itself as a source")
        if self.completion is not None:
            if self.completion.kind != TaskCompletionKind.EVIDENCED_NOOP:
                raise ValueError("deliverable completion currently supports evidenced_noop only")
            if self.outcomes:
                raise ValueError(
                    "evidenced-noop deliverable cannot also declare delivered outcomes"
                )
        return self


class DependencyGitSource(TeamModel):
    dependency_task_id: str
    deliverable_id: str
    branch: str
    commit_sha: str
    relationship_kind: Literal[
        "task_dependency",
        "required_contribution",
        "task_continuation",
    ] = "task_dependency"
    pinned_ref: str | None = None

    @field_validator(
        "dependency_task_id",
        "deliverable_id",
        "branch",
        "commit_sha",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("commit_sha")
    @classmethod
    def _full_commit_sha(cls, value: str) -> str:
        normalized = value.casefold()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized) is None:
            raise ValueError("commit_sha must be a full Git object ID")
        return normalized

    @field_validator("pinned_ref", mode="before")
    @classmethod
    def _optional_pinned_ref(cls, value: object) -> str | None:
        normalized = _optional_text(value)
        if (
            normalized is not None
            and re.fullmatch(
                r"refs/evander/team-reviews/[A-Za-z0-9_-]+",
                normalized,
            )
            is None
        ):
            raise ValueError("pinned_ref must be an immutable Team review ref")
        return normalized


class DependencyCompositionManifest(TeamModel):
    """Immutable evidence for the exact accepted Git inputs of one worktree."""

    policy_version: Literal[1] = 1
    target_task_id: str
    managed_repo_id: str
    directory_path: str = "root"
    base_branch: str
    base_commit_sha: str
    base_tree_hash: str
    dependency_git_sources: list[DependencyGitSource]
    source_fingerprint: str
    frontier_commit_shas: list[str] = Field(default_factory=list)
    reduced_source_deliverable_ids: list[str] = Field(default_factory=list)
    composed_commit_sha: str
    composed_tree_hash: str
    verified_candidate_commit_sha: str | None = None

    @field_validator(
        "target_task_id",
        "managed_repo_id",
        "directory_path",
        "base_branch",
        "source_fingerprint",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "base_commit_sha",
        "base_tree_hash",
        "composed_commit_sha",
        "composed_tree_hash",
        mode="before",
    )
    @classmethod
    def _git_object_id(cls, value: object, info) -> str:
        normalized = _required_text(value, info.field_name).casefold()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized) is None:
            raise ValueError(f"{info.field_name} must be a full Git object ID")
        return normalized

    @field_validator("verified_candidate_commit_sha", mode="before")
    @classmethod
    def _optional_verified_candidate_commit_sha(cls, value: object) -> str | None:
        normalized = _optional_text(value)
        if normalized is None:
            return None
        normalized = normalized.casefold()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized) is None:
            raise ValueError("verified_candidate_commit_sha must be a full Git object ID")
        return normalized

    @field_validator("frontier_commit_shas", mode="before")
    @classmethod
    def _frontier_commit_shas(cls, value: object) -> list[str]:
        values = _unique_text_list(value or [], "frontier_commit_shas")
        for item in values:
            if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", item) is None:
                raise ValueError("frontier_commit_shas entries must be full Git object IDs")
        return [item.casefold() for item in values]

    @field_validator("reduced_source_deliverable_ids", mode="before")
    @classmethod
    def _reduced_source_deliverable_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "reduced_source_deliverable_ids")

    @model_validator(mode="after")
    def _validate_sources(self) -> "DependencyCompositionManifest":
        if not self.dependency_git_sources:
            raise ValueError("dependency composition manifest requires Git sources")
        source_commits = {source.commit_sha for source in self.dependency_git_sources}
        if not set(self.frontier_commit_shas).issubset(source_commits):
            raise ValueError("composition frontier must be drawn from dependency sources")
        source_ids = {source.deliverable_id for source in self.dependency_git_sources}
        if not set(self.reduced_source_deliverable_ids).issubset(source_ids):
            raise ValueError("reduced sources must be drawn from dependency sources")
        return self


class DependencyCompositionRecovery(TeamModel):
    id: str
    blocker_id: str
    target_task_id: str
    project_id: str
    managed_repo_id: str
    directory_path: str
    base_branch: str
    base_commit_sha: str
    base_tree_hash: str
    dependency_git_sources: list[DependencyGitSource]
    source_fingerprint: str
    preparation_generation: str
    failed_dependency_task_id: str
    failed_deliverable_id: str
    conflict_paths: list[str] = Field(default_factory=list)
    recovery_task_id: str | None = None
    verified_deliverable_id: str | None = None
    verified_commit_sha: str | None = None
    verified_at: datetime | None = None
    accepted_deliverable_id: str | None = None
    resolved_commit_sha: str | None = None
    state: DependencyCompositionRecoveryState = (
        DependencyCompositionRecoveryState.AWAITING_ASSIGNMENT
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    @field_validator(
        "id",
        "blocker_id",
        "target_task_id",
        "project_id",
        "managed_repo_id",
        "directory_path",
        "base_branch",
        "source_fingerprint",
        "preparation_generation",
        "failed_dependency_task_id",
        "failed_deliverable_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "recovery_task_id",
        "verified_deliverable_id",
        "accepted_deliverable_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("base_branch")
    @classmethod
    def _base_branch(cls, value: str) -> str:
        return validate_branch_name(value)

    @field_validator(
        "base_commit_sha",
        "base_tree_hash",
        "resolved_commit_sha",
        "verified_commit_sha",
    )
    @classmethod
    def _git_object_id(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized) is None:
            raise ValueError(f"{info.field_name} must be a full Git object ID")
        return normalized

    @field_validator("conflict_paths", mode="before")
    @classmethod
    def _conflict_paths(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "conflict_paths")

    @model_validator(mode="after")
    def _validate_recovery_state(self) -> "DependencyCompositionRecovery":
        if not self.dependency_git_sources:
            raise ValueError("dependency composition recovery requires Git sources")
        if self.state == DependencyCompositionRecoveryState.AWAITING_ASSIGNMENT:
            if self.recovery_task_id is not None:
                raise ValueError("awaiting recovery cannot reference a recovery task")
        elif (
            self.state
            in {
                DependencyCompositionRecoveryState.IN_PROGRESS,
                DependencyCompositionRecoveryState.ACCEPTED,
            }
            and self.recovery_task_id is None
        ):
            raise ValueError("started dependency composition recovery requires recovery_task_id")
        if self.state == DependencyCompositionRecoveryState.ACCEPTED:
            if (
                self.accepted_deliverable_id is None
                or self.resolved_commit_sha is None
                or self.resolved_at is None
            ):
                raise ValueError(
                    "accepted dependency composition recovery requires deliverable, commit, and time"
                )
        elif self.accepted_deliverable_id is not None or self.resolved_commit_sha is not None:
            raise ValueError(
                "unaccepted dependency composition recovery cannot record a resolved deliverable"
            )
        if bool(self.verified_deliverable_id) != bool(self.verified_commit_sha):
            raise ValueError(
                "dependency composition recovery verification requires deliverable and commit"
            )
        if bool(self.verified_deliverable_id) != bool(self.verified_at):
            raise ValueError("dependency composition recovery verification requires verified_at")
        return self


class GitWorkContext(TeamModel):
    id: str
    agent_id: str
    task_id: str
    agent_branch: str
    base_branch: str | None = None
    base_commit: str | None = None
    commit_sha: str | None = None
    dispatch_id: str | None = None
    worktree_path: str | None = None
    dependency_git_sources: list[DependencyGitSource] = Field(default_factory=list)
    dependency_composition_recovery_id: str | None = None
    manual_dependency_git_sources: list[DependencyGitSource] = Field(default_factory=list)
    dependency_composition_manifest: DependencyCompositionManifest | None = None
    preparation_generation: str | None = None
    state: GitWorkContextState = GitWorkContextState.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "agent_id", "task_id", "agent_branch", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "base_branch",
        "base_commit",
        "commit_sha",
        "dispatch_id",
        "worktree_path",
        "dependency_composition_recovery_id",
        "preparation_generation",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("preparation_generation")
    @classmethod
    def _preparation_generation(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if re.fullmatch(r"[0-9a-f]{20}", normalized) is None:
            raise ValueError("preparation_generation must be a 20-character hex fingerprint")
        return normalized


class TeamGitPreparationAttempt(TeamModel):
    """Server-owned identity and receipt for one physical task-worktree preparation."""

    id: str
    agent_id: str
    session_id: str
    task_id: str
    project_id: str
    latest_dispatch_id: str
    managed_repo_id: str
    directory_path: str = "root"
    execution_backend: Literal["libvirt_vm", "container_worker_vm"]
    worktree_path: str
    branch: str
    base_ref: str
    preparation_generation: str
    state: TeamGitPreparationAttemptState = TeamGitPreparationAttemptState.PENDING
    worker_request_fingerprint: str | None = None
    base_commit_sha: str | None = None
    head_commit_sha: str | None = None
    git_work_context_id: str | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    prepared_at: datetime | None = None
    committed_at: datetime | None = None

    @field_validator(
        "id",
        "agent_id",
        "session_id",
        "task_id",
        "project_id",
        "latest_dispatch_id",
        "managed_repo_id",
        "execution_backend",
        "worktree_path",
        "branch",
        "base_ref",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("directory_path", mode="before")
    @classmethod
    def _directory_path(cls, value: object) -> str:
        return DirectoryPath(segments=str(value or "root").strip() or "root").as_posix()

    @field_validator("branch", mode="after")
    @classmethod
    def _branch(cls, value: str) -> str:
        return validate_branch_name(value)

    @field_validator("preparation_generation", mode="before")
    @classmethod
    def _preparation_generation(cls, value: object) -> str:
        normalized = _required_text(value, "preparation_generation").casefold()
        if re.fullmatch(r"[0-9a-f]{20}", normalized) is None:
            raise ValueError("preparation_generation must be a 20-character hex fingerprint")
        return normalized

    @field_validator(
        "worker_request_fingerprint",
        "base_commit_sha",
        "head_commit_sha",
        "git_work_context_id",
        "last_error",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_receipt_lifecycle(self) -> "TeamGitPreparationAttempt":
        if self.state in {
            TeamGitPreparationAttemptState.PREPARED,
            TeamGitPreparationAttemptState.COMMITTED,
            TeamGitPreparationAttemptState.CLEANUP_PENDING,
        }:
            if self.worker_request_fingerprint is None or self.base_commit_sha is None:
                raise ValueError("prepared Git attempt requires a worker receipt and base commit")
            if self.prepared_at is None:
                raise ValueError("prepared Git attempt requires prepared_at")
        if self.state == TeamGitPreparationAttemptState.COMMITTED:
            if self.git_work_context_id is None or self.committed_at is None:
                raise ValueError("committed Git attempt requires its work context and timestamp")
        return self
