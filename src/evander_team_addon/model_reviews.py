"""Team reviews models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from evander.models import DirectoryPath, utc_now

from .model_common import (
    ReviewBlockKind,
    TeamModel,
    TeamRecordState,
    _ensure_unique,
    _optional_text,
    _required_text,
    _text_list,
    _unique_text_list,
)
from .model_tasks import (
    OutcomeKindSnapshot,
)


class ReviewProfileCriterion(TeamModel):
    id: str
    label: str
    description: str = ""
    required: bool = True

    @field_validator("id", "label", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class ReviewProfile(TeamModel):
    id: str
    team_id: str
    project_id: str | None = None
    name: str
    description: str = ""
    applicable_role_ids: list[str] = Field(default_factory=list)
    criteria: list[ReviewProfileCriterion] = Field(default_factory=list)
    required_outcome_kinds: list[str] = Field(default_factory=list)
    archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "team_id", "name", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("project_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("applicable_role_ids", mode="before")
    @classmethod
    def _applicable_role_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "applicable_role_ids")

    @field_validator("required_outcome_kinds", mode="before")
    @classmethod
    def _required_outcome_kinds(cls, value: object) -> list[str]:
        return _unique_text_list(value, "required_outcome_kinds")

    @model_validator(mode="after")
    def _validate_criterion_ids(self) -> "ReviewProfile":
        _ensure_unique([criterion.id for criterion in self.criteria], "review profile criteria")
        return self


class ReviewCriterionResult(TeamModel):
    criterion_id: str
    state: Literal["passed", "failed", "skipped"]
    finding: str = ""
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("criterion_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "evidence_refs")


class ReviewRepositorySnapshot(TeamModel):
    """Server-owned immutable source provenance for one Team review."""

    managed_repo_id: str
    directory_path: str
    commit_sha: str
    pinned_ref: str
    source_kind: Literal["deliverable_candidate", "task_base", "release_candidate"]
    source_ref: str | None = None

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

    @field_validator("commit_sha", mode="before")
    @classmethod
    def _commit_sha(cls, value: object) -> str:
        normalized = _required_text(value, "commit_sha").casefold()
        # The managed-Git pin operation is the authoritative object-ID
        # validator. Retaining this permissive field validator keeps old
        # persisted Team rows and offline migration fixtures readable; an
        # incomplete value fails closed before a reviewer can receive a
        # checkout.
        return normalized

    @field_validator("pinned_ref", mode="before")
    @classmethod
    def _pinned_ref(cls, value: object) -> str:
        normalized = _required_text(value, "pinned_ref")
        if re.fullmatch(r"refs/evander/team-reviews/[A-Za-z0-9_-]+", normalized) is None:
            raise ValueError("pinned_ref must be a Team review snapshot ref")
        return normalized

    @field_validator("source_ref", mode="before")
    @classmethod
    def _source_ref(cls, value: object) -> str | None:
        return _optional_text(value)


class ReviewRequest(TeamModel):
    id: str
    deliverable_id: str | None = None
    release_package_id: str | None = None
    candidate_commit_sha: str | None = None
    reviewer_agent_id: str
    requested_by_agent_id: str | None = None
    requested_by_actor: str | None = None
    state: TeamRecordState = TeamRecordState.PENDING
    findings: list[str] = Field(default_factory=list)
    review_profile_id: str | None = None
    criteria_snapshot: list[ReviewProfileCriterion] | None = None
    required_outcome_kind_definitions: list[OutcomeKindSnapshot] = Field(default_factory=list)
    instructions: str | None = None
    criteria: list[ReviewCriterionResult] = Field(default_factory=list)
    reviewed_commit_sha: str | None = None
    repository_snapshot: ReviewRepositorySnapshot | None = None
    review_cohort_id: str | None = None
    supersedes_review_request_id: str | None = None
    superseded_by_review_request_id: str | None = None
    retry_reason: str | None = None
    retry_requested_by: str | None = None
    block_kind: ReviewBlockKind | None = None
    retired_at: datetime | None = None
    retired_by: str | None = None
    retirement_reason: str | None = None
    created_at: datetime | None = None
    decision_at: datetime | None = None

    @field_validator("id", "reviewer_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value: object) -> list[str]:
        return _text_list(value, "findings")

    @field_validator(
        "review_profile_id",
        "instructions",
        "reviewed_commit_sha",
        "review_cohort_id",
        "deliverable_id",
        "release_package_id",
        "candidate_commit_sha",
        "requested_by_agent_id",
        "requested_by_actor",
        "supersedes_review_request_id",
        "superseded_by_review_request_id",
        "retry_reason",
        "retry_requested_by",
        "retired_by",
        "retirement_reason",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_target(self) -> "ReviewRequest":
        targets = int(self.deliverable_id is not None) + int(self.release_package_id is not None)
        if targets != 1:
            raise ValueError(
                "review request requires exactly one of deliverable_id or release_package_id"
            )
        if self.requested_by_agent_id is not None and self.requested_by_actor is not None:
            raise ValueError(
                "review request cannot set both requested_by_agent_id and requested_by_actor"
            )
        if self.release_package_id is not None and self.candidate_commit_sha is None:
            raise ValueError("release review request requires candidate_commit_sha")
        if self.supersedes_review_request_id is not None and (
            self.retry_reason is None or self.retry_requested_by is None
        ):
            raise ValueError("superseding review requires retry_reason and retry_requested_by")
        if self.criteria_snapshot is not None:
            _ensure_unique(
                [criterion.id for criterion in self.criteria_snapshot],
                "review criteria snapshot",
            )
        retirement_fields = (
            self.retired_at,
            self.retired_by,
            self.retirement_reason,
        )
        if any(value is not None for value in retirement_fields) and not all(
            value is not None for value in retirement_fields
        ):
            raise ValueError(
                "retired review request requires retired_at, retired_by, and retirement_reason"
            )
        if self.block_kind is not None and self.state != TeamRecordState.BLOCKED:
            raise ValueError("review block_kind is only valid when state is blocked")
        return self
