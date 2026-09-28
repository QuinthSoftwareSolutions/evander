"""Durable, bounded Git recovery contracts; no executable Git or code payloads."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .model_common import TeamModel
from evander.models import utc_now

GitRecoveryCause = Literal[
    "unknown",
    "transport_unavailable",
    "reviewed_object_unavailable",
    "source_ref_moved",
    "incompatible_source_base",
    "contradictory_provenance",
    "workspace_receipt_mismatch",
    "workspace_missing",
    "workspace_dirty",
    "inputs_changed",
    "merge_conflict",
    "destination_advanced",
    "policy_conflict",
]


class GitRecoveryPolicy(TeamModel):
    enabled: bool = True
    automatic_same_branch_renewal: bool = True
    preparation_retry_limit: int = Field(default=3, ge=0, le=10)
    candidate_generation_limit: int = Field(default=3, ge=0, le=10)


class GitBaselineConstraints(TeamModel):
    # Legacy pins have no recorded intent. Do not reinterpret them as renewable.
    intent: Literal["unknown", "snapshot", "required"] = "unknown"
    required_base_commit_sha: str | None = None
    excluded_ancestor_shas: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _required_intent(self):
        if self.required_base_commit_sha and self.intent != "required":
            raise ValueError("required_base_commit_sha requires required baseline intent")
        return self

    @field_validator("required_base_commit_sha", "excluded_ancestor_shas")
    @classmethod
    def _object_ids(cls, value):
        values = value if isinstance(value, list) else [value] if value else []
        if any(not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", item) for item in values):
            raise ValueError("baseline constraints require full Git object IDs")
        return (
            sorted(set(item.lower() for item in values))
            if isinstance(value, list)
            else value.lower()
            if value
            else None
        )


class GitRecoveryIncident(TeamModel):
    id: str
    blocker_id: str
    project_id: str
    task_id: str
    lineage_id: str
    cause: GitRecoveryCause = "unknown"
    stage: str = "preparation"
    input_fingerprint: str
    preparation_generation: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    state: Literal[
        "diagnosed", "preparing", "awaiting_review", "waiting", "resumed", "superseded"
    ] = "diagnosed"
    waiting_reason: str | None = None
    action_id: str | None = None
    preparation_retries: int = Field(default=0, ge=0)
    candidate_generations: int = Field(default=0, ge=0)
    recovery_task_ids: list[str] = Field(default_factory=list)
    # Holds survive incident resolution. Only explicit, evidenced retention release
    # permits cleanup; a successful replacement is not proof of a complete archive.
    preserved_attempt_ids: list[str] = Field(default_factory=list)
    preserved_context_ids: list[str] = Field(default_factory=list)
    replacement_deliverables: dict[str, str] = Field(default_factory=dict)
    applied_requests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    resumed_at: datetime | None = None
