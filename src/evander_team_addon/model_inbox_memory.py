"""Team inbox memory models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from evander.models import utc_now

from .model_common import (
    TeamBlockerEventType,
    TeamBlockerResolutionScope,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamMemoryKind,
    TeamMemoryScope,
    TeamMemoryStatus,
    TeamModel,
    _optional_text,
    _required_text,
    _unique_text_list,
)


class AgentInboxItem(TeamModel):
    id: str
    recipient_agent_id: str
    item_type: TeamInboxItemType
    sender_agent_id: str | None = None
    task_id: str | None = None
    response_requested: bool = True
    priority: TeamInboxPriority = TeamInboxPriority.NORMAL
    payload: dict[str, Any] = Field(default_factory=dict)
    state: TeamInboxItemState = TeamInboxItemState.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "recipient_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("sender_agent_id", "task_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class TeamMcpCapabilityBlockerIdentity(TeamModel):
    """Unambiguous identity for a review MCP capability incident."""

    agent_id: str
    review_request_ids: list[str] = Field(default_factory=list)

    @field_validator("agent_id", mode="before")
    @classmethod
    def _agent_id(cls, value: object) -> str:
        return _required_text(value, "agent_id")

    @field_validator("review_request_ids", mode="before")
    @classmethod
    def _review_request_ids(cls, value: object) -> list[str]:
        review_request_ids = _unique_text_list(value or [], "review_request_ids")
        if not review_request_ids:
            raise ValueError("review_request_ids must not be empty")
        return review_request_ids


class TeamBlocker(TeamModel):
    id: str
    team_id: str
    project_id: str | None = None
    task_id: str | None = None
    source_kind: TeamBlockerSourceKind
    source_id: str
    mcp_capability_identity: TeamMcpCapabilityBlockerIdentity | None = None
    reason_code: str = "unspecified"
    reporter_agent_id: str | None = None
    reporter_actor: str | None = None
    owner_agent_id: str | None = None
    delegate_agent_id: str | None = None
    affected_task_ids: list[str] = Field(default_factory=list)
    resolution_scope: TeamBlockerResolutionScope = TeamBlockerResolutionScope.TASK
    state: TeamBlockerState = TeamBlockerState.OPEN
    route: TeamBlockerRoute = TeamBlockerRoute.OWNED
    summary: str
    details: str = ""
    priority: TeamInboxPriority = TeamInboxPriority.NORMAL
    escalation_depth: int = Field(default=0, ge=0)
    active_dispatch_id: str | None = None
    follow_up_at: datetime | None = None
    last_progress_at: datetime | None = None
    terminal_reference_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    @field_validator(
        "id",
        "team_id",
        "source_id",
        "reason_code",
        "summary",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "project_id",
        "task_id",
        "reporter_agent_id",
        "reporter_actor",
        "owner_agent_id",
        "delegate_agent_id",
        "active_dispatch_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("details", mode="before")
    @classmethod
    def _details(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("terminal_reference_ids", mode="before")
    @classmethod
    def _terminal_reference_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "terminal_reference_ids")

    @field_validator("affected_task_ids", mode="before")
    @classmethod
    def _affected_task_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "affected_task_ids")

    @model_validator(mode="after")
    def _validate_route(self) -> "TeamBlocker":
        if (
            self.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
            and self.resolution_scope != TeamBlockerResolutionScope.DOWNSTREAM
        ):
            raise ValueError("candidate correction successor blocker must use downstream scope")
        if self.route == TeamBlockerRoute.DELEGATED and self.delegate_agent_id is None:
            raise ValueError("delegated blocker requires delegate_agent_id")
        if self.route != TeamBlockerRoute.DELEGATED and self.delegate_agent_id is not None:
            raise ValueError("non-delegated blocker cannot have delegate_agent_id")
        if self.state == TeamBlockerState.RESOLVED:
            if self.route != TeamBlockerRoute.RESOLVED:
                raise ValueError("resolved blocker must use the resolved route")
            if self.resolved_at is None:
                raise ValueError("resolved blocker requires resolved_at")
        elif self.route == TeamBlockerRoute.RESOLVED:
            raise ValueError("open blocker cannot use the resolved route")
        if self.route in {TeamBlockerRoute.OWNED, TeamBlockerRoute.DELEGATED}:
            if self.owner_agent_id is None:
                raise ValueError("locally routed blocker requires owner_agent_id")
        return self


class TeamBlockerEvent(TeamModel):
    id: str
    blocker_id: str
    event_type: TeamBlockerEventType
    actor_agent_id: str | None = None
    from_agent_id: str | None = None
    to_agent_id: str | None = None
    dispatch_id: str | None = None
    note: str = ""
    reference_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "blocker_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "actor_agent_id",
        "from_agent_id",
        "to_agent_id",
        "dispatch_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("reference_ids", mode="before")
    @classmethod
    def _reference_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "reference_ids")

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        return dict(value)


class AgentActionRecord(TeamModel):
    id: str
    agent_id: str
    dispatch_id: str
    action: dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    dismissed_at: datetime | None = None
    dismissed_by: str | None = None
    dismissed_reason: str | None = None
    feedback_delivered_at: datetime | None = None
    feedback_delivered_by_dispatch_id: str | None = None

    @field_validator("id", "agent_id", "dispatch_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "error",
        "dismissed_by",
        "dismissed_reason",
        "feedback_delivered_by_dispatch_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class TeamMemoryRecord(TeamModel):
    id: str
    team_id: str
    project_id: str | None = None
    codebase_id: str | None = None
    agent_id: str | None = None
    scope: TeamMemoryScope = TeamMemoryScope.TEAM_GLOBAL
    kind: TeamMemoryKind = TeamMemoryKind.OTHER
    title: str
    summary: str
    body: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    status: TeamMemoryStatus = TeamMemoryStatus.ACTIVE
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    supersedes_memory_id: str | None = None
    superseded_by_memory_id: str | None = None
    created_by_agent_id: str | None = None
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "team_id", "title", "summary", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "project_id",
        "codebase_id",
        "agent_id",
        "body",
        "supersedes_memory_id",
        "superseded_by_memory_id",
        "created_by_agent_id",
        "created_by",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, value: object) -> list[str]:
        return [tag.casefold() for tag in _unique_text_list(value or [], "tags")]

    @field_validator("source_refs", "artifact_refs", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @model_validator(mode="after")
    def _validate_scope(self) -> "TeamMemoryRecord":
        if self.scope == TeamMemoryScope.PROJECT and self.project_id is None:
            raise ValueError("project-scoped memory requires project_id")
        if self.scope == TeamMemoryScope.AGENT_PRIVATE and self.agent_id is None:
            raise ValueError("agent-private memory requires agent_id")
        return self
