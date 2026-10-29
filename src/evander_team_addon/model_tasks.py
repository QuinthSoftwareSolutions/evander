"""Team tasks models."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.models import utc_now
from evander_team_addon.git_naming import validate_branch_name

from .model_git_recovery import GitBaselineConstraints

from .model_common import (
    ExternalSourceEvidenceFreshnessStatus,
    TaskAssessmentReadiness,
    TaskCompletionKind,
    TaskResolutionStatus,
    TeamCaseStatus,
    TeamInboxPriority,
    TeamModel,
    TeamTaskKind,
    TeamTaskState,
    _ensure_utc,
    _optional_text,
    _required_text,
    _unique_text_list,
)
from .model_organization import (
    ProjectGitIdentity,
)


class ExternalSourceEvidenceBody(TeamModel):
    content: str
    format: str = "plain_text"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content", "format", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a JSON object")
        return dict(value)


class ExternalSourceEvidenceAcceptanceCriterion(TeamModel):
    text: str
    external_id: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("external_id", "status", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a JSON object")
        return dict(value)


class ExternalSourceEvidenceDiscussionEntry(TeamModel):
    body: str
    external_id: str | None = None
    author: str | None = None
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("body", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("external_id", "author", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a JSON object")
        return dict(value)


class ExternalSourceEvidenceLink(TeamModel):
    url: str
    label: str | None = None
    relation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("label", "relation", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a JSON object")
        return dict(value)


def external_source_evidence_content_digest(
    *,
    title: str | None,
    body: ExternalSourceEvidenceBody | Mapping[str, Any] | None,
    acceptance_criteria: Sequence[ExternalSourceEvidenceAcceptanceCriterion | Mapping[str, Any]],
    discussion: Sequence[ExternalSourceEvidenceDiscussionEntry | Mapping[str, Any]],
    links: Sequence[ExternalSourceEvidenceLink | Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> str:
    def _json_payload(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Mapping):
            return {str(key): _json_payload(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [_json_payload(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        return value

    content = {
        "title": _optional_text(title),
        "body": _json_payload(body),
        "acceptance_criteria": _json_payload(acceptance_criteria),
        "discussion": _json_payload(discussion),
        "links": _json_payload(links),
        "metadata": _json_payload(metadata),
    }
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class ExternalSourceEvidenceSnapshot(TeamModel):
    id: str
    team_id: str
    project_id: str | None = None
    provider_type: str
    source_type: str
    external_identifier: str
    external_version: str
    fetched_at: datetime
    expires_at: datetime | None = None
    freshness_status: ExternalSourceEvidenceFreshnessStatus = (
        ExternalSourceEvidenceFreshnessStatus.UNKNOWN
    )
    access_classification: str
    content_digest: str
    title: str | None = None
    body: ExternalSourceEvidenceBody | None = None
    acceptance_criteria: list[ExternalSourceEvidenceAcceptanceCriterion] = Field(
        default_factory=list
    )
    discussion: list[ExternalSourceEvidenceDiscussionEntry] = Field(default_factory=list)
    links: list[ExternalSourceEvidenceLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_actor: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "team_id",
        "provider_type",
        "source_type",
        "external_identifier",
        "external_version",
        "access_classification",
        "created_by_actor",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("project_id", "title", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("content_digest", mode="before")
    @classmethod
    def _content_digest(cls, value: object) -> str:
        normalized = _required_text(value, "content_digest").casefold()
        if re.fullmatch(r"sha256:[0-9a-f]{64}", normalized) is None:
            raise ValueError("content_digest must be a sha256:<64 lowercase hex> digest")
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a JSON object")
        return dict(value)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> "ExternalSourceEvidenceSnapshot":
        expected_digest = external_source_evidence_content_digest(
            title=self.title,
            body=self.body,
            acceptance_criteria=self.acceptance_criteria,
            discussion=self.discussion,
            links=self.links,
            metadata=self.metadata,
        )
        if self.content_digest != expected_digest:
            raise ValueError(
                "content_digest does not match the normalized evidence snapshot content"
            )
        if self.expires_at is not None and _ensure_utc(self.expires_at) < _ensure_utc(
            self.fetched_at
        ):
            if self.freshness_status not in {
                ExternalSourceEvidenceFreshnessStatus.EXPIRED,
                ExternalSourceEvidenceFreshnessStatus.STALE,
            }:
                raise ValueError(
                    "expires_at before fetched_at requires stale or expired freshness_status"
                )
        return self


def external_source_evidence_snapshot_payload(
    snapshot: ExternalSourceEvidenceSnapshot,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    effective_now = _ensure_utc(now or utc_now())
    effective_status = snapshot.freshness_status
    if snapshot.expires_at is not None and _ensure_utc(snapshot.expires_at) <= effective_now:
        effective_status = ExternalSourceEvidenceFreshnessStatus.EXPIRED
    payload["effective_freshness_status"] = effective_status.value
    return payload


class TeamCase(TeamModel):
    id: str
    team_id: str
    project_id: str | None = None
    title: str
    description: str = ""
    status: TeamCaseStatus = TeamCaseStatus.OPEN
    priority: TeamInboxPriority = TeamInboxPriority.NORMAL
    owner_agent_id: str | None = None
    source: str | None = None
    source_ref: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    task_ids: list[str] = Field(default_factory=list)
    source_evidence_snapshot_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None

    @field_validator("id", "team_id", "title", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "project_id",
        "owner_agent_id",
        "source",
        "source_ref",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("description", mode="before")
    @classmethod
    def _description_text(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("tags", "task_ids", "source_evidence_snapshot_ids", mode="before")
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


class OutcomeKindSnapshot(TeamModel):
    kind: str
    name: str
    description: str = ""
    reference_requirements: str = ""
    evidence_requirements: str = ""

    @field_validator("kind", "name", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "description",
        "reference_requirements",
        "evidence_requirements",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str:
        return _optional_text(value) or ""


class OutcomeKindDefinition(OutcomeKindSnapshot):
    id: str
    team_id: str
    project_id: str | None = None
    archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "team_id", mode="before")
    @classmethod
    def _required_identity(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("project_id", mode="before")
    @classmethod
    def _optional_project_id(cls, value: object) -> str | None:
        return _optional_text(value)

    def snapshot(self) -> OutcomeKindSnapshot:
        return OutcomeKindSnapshot.model_validate(
            self.model_dump(
                include={
                    "kind",
                    "name",
                    "description",
                    "reference_requirements",
                    "evidence_requirements",
                }
            )
        )


class TaskOutcomeContract(TeamModel):
    required_outcome_kinds: list[str] = Field(default_factory=list)
    match: Literal["any", "all"] = "all"
    allow_evidenced_noop: bool = False
    forbidden_outcome_kinds: list[str] = Field(default_factory=list)
    outcome_kind_definitions: list[OutcomeKindSnapshot] = Field(default_factory=list)

    @field_validator(
        "required_outcome_kinds",
        "forbidden_outcome_kinds",
        mode="before",
    )
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @model_validator(mode="after")
    def _validate_contract(self) -> "TaskOutcomeContract":
        if not self.required_outcome_kinds and not self.allow_evidenced_noop:
            raise ValueError(
                "outcome contract requires required_outcome_kinds or allow_evidenced_noop"
            )
        overlap = set(self.required_outcome_kinds).intersection(self.forbidden_outcome_kinds)
        if overlap:
            raise ValueError(
                "outcome kinds cannot be both required and forbidden: " + ", ".join(sorted(overlap))
            )
        return self


class TaskDeliverableContract(TeamModel):
    require_git_provenance: bool = False
    required_contribution_task_ids: list[str] = Field(default_factory=list)
    pinned_source_deliverable_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "required_contribution_task_ids", "pinned_source_deliverable_ids", mode="before"
    )
    @classmethod
    def _required_contribution_task_ids(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


class TaskCompletion(TeamModel):
    kind: TaskCompletionKind
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    outcome_reference_ids: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _required_summary(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("evidence_refs", "outcome_reference_ids", mode="before")
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @model_validator(mode="after")
    def _validate_evidence(self) -> "TaskCompletion":
        if self.kind == TaskCompletionKind.EVIDENCED_NOOP and not self.evidence_refs:
            raise ValueError("evidenced_noop completion requires evidence_refs")
        if self.kind == TaskCompletionKind.DELIVERED and not self.outcome_reference_ids:
            raise ValueError("delivered completion requires outcome_reference_ids")
        return self


class TaskCancellation(TeamModel):
    reason_code: str
    reason: str
    actor_kind: Literal["agent", "operator", "system"]
    actor_id: str
    successor_task_id: str | None = None
    rebind_dependents: bool = False
    rebound_dependent_task_ids: list[str] = Field(default_factory=list)
    rebound_integration_boundary_task_ids: list[str] = Field(default_factory=list)
    invalidated_git_work_context_ids: list[str] = Field(default_factory=list)
    cancelled_at: datetime = Field(default_factory=utc_now)

    @field_validator("reason_code", "reason", "actor_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("successor_task_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator(
        "rebound_dependent_task_ids",
        "rebound_integration_boundary_task_ids",
        "invalidated_git_work_context_ids",
        mode="before",
    )
    @classmethod
    def _replacement_ids(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @model_validator(mode="after")
    def _validate_dependency_rebinding(self) -> "TaskCancellation":
        if self.rebind_dependents and self.successor_task_id is None:
            raise ValueError("rebind_dependents requires successor_task_id")
        if (
            self.rebound_dependent_task_ids
            or self.rebound_integration_boundary_task_ids
            or self.invalidated_git_work_context_ids
        ) and not self.rebind_dependents:
            raise ValueError("task replacement audit IDs require rebind_dependents")
        return self


_TASK_MUTATION_POLICY_FIELDS = frozenset(
    {
        "dependency_task_ids",
        "deliverable_contract",
        "git_base_branch",
        "integration_boundary",
        "parent_task_id",
        "task_kind",
    }
)


class TaskMutationPolicy(TeamModel):
    """Immutable workflow-owned constraints on otherwise mutable task fields."""

    policy_id: str
    reason: str
    locked_fields: list[str] = Field(default_factory=list)
    allow_child_tasks: bool = True
    allow_agent_cancellation: bool = True

    @field_validator("policy_id", "reason", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("locked_fields", mode="before")
    @classmethod
    def _locked_fields(cls, value: object) -> list[str]:
        fields = _unique_text_list(value or [], "locked_fields")
        unsupported = sorted(set(fields) - _TASK_MUTATION_POLICY_FIELDS)
        if unsupported:
            raise ValueError("unsupported task mutation policy fields: " + ", ".join(unsupported))
        return fields


class TaskAssessment(TeamModel):
    task_id: str
    resolution_status: TaskResolutionStatus
    resolution_reason: str
    readiness: TaskAssessmentReadiness
    direct_child_ids: list[str] = Field(default_factory=list)
    unresolved_descendant_ids: list[str] = Field(default_factory=list)
    unsatisfied_dependency_ids: list[str] = Field(default_factory=list)
    active_acceptance_ids: list[str] = Field(default_factory=list)
    active_review_request_ids: list[str] = Field(default_factory=list)
    active_blocker_ids: list[str] = Field(default_factory=list)
    integration_ready: bool = False
    contribution_coverage: dict[str, Any] = Field(default_factory=dict)
    invalid_reason_codes: list[str] = Field(default_factory=list)

    @field_validator("task_id", "resolution_reason", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "direct_child_ids",
        "unresolved_descendant_ids",
        "unsatisfied_dependency_ids",
        "active_acceptance_ids",
        "active_review_request_ids",
        "active_blocker_ids",
        "invalid_reason_codes",
        mode="before",
    )
    @classmethod
    def _id_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)


class AdoWorkItemAttribution(TeamModel):
    """An intake snapshot, written by the host and inherited by task descendants."""

    organization: str
    work_item_id: str
    assignee_id: str | None = None
    display_name: str | None = None
    email: str | None = None




class AgentTask(TeamModel):
    git_constraints: GitBaselineConstraints = Field(default_factory=GitBaselineConstraints)
    id: str
    owner_agent_id: str | None
    assignment_mode: Literal["agent", "role"] = "agent"
    assignment_role_id: str | None = None
    assignment_started_at: datetime | None = None
    project_id: str
    case_id: str | None = None
    priority: int = Field(ge=0)
    state: TeamTaskState = TeamTaskState.PENDING
    task_kind: TeamTaskKind = TeamTaskKind.SUPPORT
    outcome_contract: TaskOutcomeContract | None = None
    integration_boundary: bool = False
    deliverable_contract: TaskDeliverableContract | None = None
    completion: TaskCompletion | None = None
    state_entered_at: datetime = Field(default_factory=utc_now)
    title: str
    description: str
    created_by_agent_id: str | None = None
    created_by_actor: str | None = None
    parent_task_id: str | None = None
    originating_task_id: str | None = None
    authored_by: ProjectGitIdentity | None = None
    work_item_attributions: list[AdoWorkItemAttribution] | None = None
    release_base_package_id: str | None = None
    work_lineage_id: str | None = None
    git_base_branch: str | None = None
    git_base_commit_sha: str | None = None
    mutation_policy: TaskMutationPolicy | None = None
    cancellation: TaskCancellation | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)
    deliverable_ids: list[str] = Field(default_factory=list)
    source_evidence_snapshot_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def _default_state_entered_at(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("state_entered_at"):
            data = dict(data)
            data["state_entered_at"] = data.get("updated_at") or data.get("created_at") or utc_now()
        return data

    @field_validator(
        "id",
        "project_id",
        "title",
        "description",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "owner_agent_id",
        "assignment_role_id",
        "case_id",
        "created_by_agent_id",
        "created_by_actor",
        "parent_task_id",
        "originating_task_id",
        "release_base_package_id",
        "work_lineage_id",
        "git_base_commit_sha",
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

    @field_validator("git_base_commit_sha")
    @classmethod
    def _git_base_commit_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized) is None:
            raise ValueError("git_base_commit_sha must be a full Git object ID")
        return normalized

    @field_validator("dependency_task_ids", mode="before")
    @classmethod
    def _dependency_task_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "dependency_task_ids")

    @field_validator("deliverable_ids", mode="before")
    @classmethod
    def _deliverable_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "deliverable_ids")

    @field_validator("source_evidence_snapshot_ids", mode="before")
    @classmethod
    def _source_evidence_snapshot_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "source_evidence_snapshot_ids")

    @model_validator(mode="after")
    def _validate_self_links(self) -> "AgentTask":
        if self.assignment_mode == "agent":
            if self.owner_agent_id is None or self.assignment_role_id is not None:
                raise ValueError("agent assignment requires an owner and no role target")
        elif self.assignment_role_id is None:
            raise ValueError("role assignment requires assignment_role_id")
        if self.owner_agent_id is None and (
            self.assignment_started_at is not None
            or self.state
            in {
                TeamTaskState.IN_PROGRESS,
                TeamTaskState.WAITING_FOR_REVIEW,
                TeamTaskState.FINISHED,
                TeamTaskState.ACCEPTED,
            }
        ):
            raise ValueError("executed tasks require a concrete owner")
        if self.created_by_agent_id is None and self.created_by_actor is None:
            raise ValueError("task creator is required")
        if self.parent_task_id == self.id:
            raise ValueError("task cannot be its own parent")
        if self.originating_task_id == self.id:
            raise ValueError("task cannot originate from itself")
        if self.id in self.dependency_task_ids:
            raise ValueError("task cannot depend on itself")
        if self.git_base_commit_sha is not None and self.git_base_branch is None:
            raise ValueError("git_base_commit_sha requires git_base_branch")
        if self.git_constraints.required_base_commit_sha and self.git_base_commit_sha != self.git_constraints.required_base_commit_sha:
            raise ValueError("task Git base must equal its required baseline")
        if self.task_kind == TeamTaskKind.OUTCOME and self.outcome_contract is None:
            raise ValueError("outcome task requires outcome_contract")
        if self.task_kind != TeamTaskKind.OUTCOME and self.outcome_contract is not None:
            raise ValueError(f"{self.task_kind.value} task cannot define outcome_contract")
        if self.task_kind != TeamTaskKind.OUTCOME and self.completion is not None:
            raise ValueError(f"{self.task_kind.value} task cannot define structured completion")
        if self.task_kind == TeamTaskKind.COORDINATION:
            if self.integration_boundary:
                raise ValueError("coordination task cannot be an integration boundary")
            if self.deliverable_contract is not None:
                raise ValueError("coordination task cannot define deliverable_contract")
            if self.git_base_commit_sha is not None:
                raise ValueError("coordination task cannot pin a Git base commit")
            if self.deliverable_ids:
                raise ValueError("coordination task cannot carry deliverables")
            if self.state == TeamTaskState.ACCEPTED:
                raise ValueError("coordination task must finish directly, not be accepted")
        if self.integration_boundary and self.deliverable_contract is None:
            raise ValueError("integration boundary task requires deliverable_contract")
        if (
            not self.integration_boundary
            and self.deliverable_contract is not None
            and self.deliverable_contract.required_contribution_task_ids
        ):
            raise ValueError("leaf deliverable_contract cannot require contribution tasks")
        if (
            self.deliverable_contract is not None
            and self.id in self.deliverable_contract.required_contribution_task_ids
        ):
            raise ValueError("task cannot require itself as a deliverable contribution")
        if self.state == TeamTaskState.CANCELLED and self.cancellation is None:
            raise ValueError("cancelled task requires a cancellation disposition")
        if self.state != TeamTaskState.CANCELLED and self.cancellation is not None:
            raise ValueError("task cancellation is only valid for cancelled tasks")
        return self
