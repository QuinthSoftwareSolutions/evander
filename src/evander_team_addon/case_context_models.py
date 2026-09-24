"""Provider-neutral case history and deterministic context contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evander.models import utc_now

ContextSectionName = Literal[
    "request",
    "sources",
    "plans",
    "decisions",
    "delivery",
    "review",
    "verification",
    "conversation",
]
SECTION_NAMES = (
    "request",
    "sources",
    "plans",
    "decisions",
    "delivery",
    "review",
    "verification",
    "conversation",
)


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeamNote(ContextModel):
    id: str
    team_id: str | None = None
    project_id: str | None = None
    case_id: str | None = None
    task_ids: list[str] = Field(default_factory=list)
    deliverable_ids: list[str] = Field(default_factory=list)
    review_request_ids: list[str] = Field(default_factory=list)
    source_evidence_snapshot_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    kind: str = Field(default="note", pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    summary: str = Field(default="", max_length=1000)
    body: str = Field(min_length=1)
    rationale: str = Field(default="", max_length=16000)
    supersedes_note_id: str | None = None
    created_by_agent_id: str
    dispatch_id: str
    created_at: datetime = Field(default_factory=utc_now)
    legacy: bool = False

    @field_validator("body")
    @classmethod
    def nonblank_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body must be a non-empty string")
        return value


class CaseContextSection(ContextModel):
    name: ContextSectionName
    limit: int = Field(default=20, ge=1, le=100)
    text_limit: int = Field(default=2000, ge=100, le=16000)
    kinds: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    order: Literal["newest_first", "oldest_first"] = "newest_first"


class CaseContextView(ContextModel):
    id: str
    view_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    directory_path: str = "root"
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=200)
    sections: list[CaseContextSection] = Field(min_length=1, max_length=8)
    text_budget: int = Field(default=12000, ge=1000, le=64000)
    created_at: datetime = Field(default_factory=utc_now)
    created_by: str = "builtin"

    @field_validator("sections")
    @classmethod
    def unique_sections(cls, value: list[CaseContextSection]) -> list[CaseContextSection]:
        if len({section.name for section in value}) != len(value):
            raise ValueError("context sections must not be repeated")
        return value


class CaseContextSnapshot(ContextModel):
    id: str
    request_id: str
    request_digest: str
    directory_path: str
    team_ids: list[str]
    case_ids: list[str]
    task_id: str | None = None
    release_package_id: str | None = None
    dispatch_id: str | None = None
    recipient_session_id: str | None = None
    created_by_agent_id: str | None = None
    source_revision: int
    view: CaseContextView
    content_digest: str
    context: dict[str, Any]
    # Full authorized records are retained independently of prompt display limits.
    evidence: dict[str, list[dict[str, Any]]]
    baseline_snapshot_id: str | None = None
    wake_delta_context: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
