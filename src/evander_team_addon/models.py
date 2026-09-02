from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from evander.models import DirectoryPath, utc_now
from evander.worker.models import WorkerFailureCause, WorkerResourceLimits
from evander_team_addon.git_naming import (
    DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX,
    normalize_canonical_release_branch_prefix,
    validate_branch_name,
)

SCHEMA_VERSION = 21
DEFAULT_TEAM_COST_WARNING_RATIO = Decimal("0.8")
WORKTREE_CLEANUP_INVENTORY_SAMPLE_LIMIT = 200
TEAM_CHAT_ATTACHMENT_DEFAULT_MIME_TYPES = (
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
)
TEAM_CHAT_ATTACHMENT_DEFAULT_EXTENSIONS = (
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
)
TEAM_CHAT_ATTACHMENT_DEFAULT_BLOCKED_EXTENSIONS = (
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".js",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
)
TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_FILES = 5
AGENT_OPERATOR_ONLY_FIELDS = {"avatar", "voice_profile"}
REALTIME_BUILT_IN_VOICE_IDS = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)
_DEFAULT_LOW_PRIORITY_WAKE_AFTER_CYCLES = 4
_DEFAULT_MAX_NO_PROGRESS_COUNT = 3
_ACTIVITY_SUMMARY_HEADLINE_MAX = 90
_ACTIVITY_SUMMARY_LINE_MAX = 80
_ACTIVITY_SUMMARY_REF_MAX = 5


class TeamTaskState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_REVIEW = "waiting_for_review"
    FINISHED = "finished"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class TeamRecordState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class InitialReleaseDispositionState(str, Enum):
    PENDING = "pending"
    RELEASE_REQUESTED = "release_requested"
    NOT_PLANNED = "not_planned"


class HumanReviewFindingTargetScope(str, Enum):
    LEGACY_UNSPECIFIED = "legacy_unspecified"
    RELEASE_CANDIDATE = "release_candidate"
    DELIVERABLES = "deliverables"


class ReviewBlockKind(str, Enum):
    DEPENDENCY = "dependency"
    INPUT_UNAVAILABLE = "input_unavailable"
    POLICY = "policy"
    SYSTEM = "system"
    UNSPECIFIED = "unspecified"


class TeamTaskKind(str, Enum):
    OUTCOME = "outcome"
    SUPPORT = "support"


class TaskCompletionKind(str, Enum):
    DELIVERED = "delivered"
    EVIDENCED_NOOP = "evidenced_noop"


class TaskResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"


class TaskAssessmentReadiness(str, Enum):
    ACTIONABLE = "actionable"
    WAITING_ON_CHILDREN = "waiting_on_children"
    WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_ACCEPTANCE = "waiting_for_acceptance"
    WAITING_FOR_REVIEW = "waiting_for_review"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    INVALID = "invalid"


class TeamCaseStatus(str, Enum):
    OPEN = "open"
    TRIAGE = "triage"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ExternalSourceEvidenceFreshnessStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


ACTIVE_TEAM_CASE_STATUS_VALUES = frozenset(
    {
        TeamCaseStatus.OPEN.value,
        TeamCaseStatus.TRIAGE.value,
        TeamCaseStatus.IN_PROGRESS.value,
        TeamCaseStatus.WAITING.value,
    }
)


class EphemeralAssignmentState(str, Enum):
    REQUESTED = "requested"
    SPAWN_PENDING = "spawn_pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CLEANUP_PENDING = "cleanup_pending"
    CLEANED_UP = "cleaned_up"
    FAILED = "failed"


class GitWorkContextState(str, Enum):
    ACTIVE = "active"
    SUBMITTED = "submitted"
    ABANDONED = "abandoned"


class TeamGitPreparationAttemptState(str, Enum):
    PENDING = "pending"
    PREPARED = "prepared"
    COMMITTED = "committed"
    CLEANUP_PENDING = "cleanup_pending"
    OPERATOR_ACTION_REQUIRED = "operator_action_required"


class DependencyCompositionRecoveryState(str, Enum):
    AWAITING_ASSIGNMENT = "awaiting_assignment"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AgentContextClearingPolicy(str, Enum):
    OFF = "off"
    IDLE = "idle"
    EVERY_WAKE = "every_wake"
    WORK_LINEAGE = "work_lineage"


class AgentProjectAssignmentKind(str, Enum):
    PROJECT_LEAD = "project_lead"
    PROJECT_MEMBER = "project_member"


class RolePermission(str, Enum):
    CREATE_ROOT_TASK = "create_root_task"
    MANAGE_OUTCOME_KINDS = "manage_outcome_kinds"
    PREPARE_BOOKKEEPING_ACTION = "prepare_bookkeeping_action"
    REQUEST_PAYMENT_PREPARATION = "request_payment_preparation"
    REQUEST_RELEASE = "request_release"
    REVIEW_BOOKKEEPING_ACTION = "review_bookkeeping_action"


class AgentStandDownSource(str, Enum):
    OPERATOR = "operator"
    SUPERIOR = "superior"


class TeamInboxItemType(str, Enum):
    OPERATOR_DIRECTIVE = "operator_directive"
    QUESTION = "question"
    STATUS_REQUEST = "status_request"
    PROGRESS_REPORT = "progress_report"
    DELIVERABLE_NOTICE = "deliverable_notice"
    ESCALATION = "escalation"


class TeamInboxItemState(str, Enum):
    PENDING = "pending"
    HANDLED = "handled"


class TeamInboxPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TeamBlockerSourceKind(str, Enum):
    REVIEW_REQUEST = "review_request"
    TASK = "task"
    DELIVERABLE = "deliverable"
    RELEASE_PACKAGE = "release_package"
    HUMAN_REVIEW_REQUEST = "human_review_request"
    AGENT_EXECUTION_STALL = "agent_execution_stall"
    MCP_CAPABILITY = "mcp_capability"
    CANDIDATE_CORRECTION_SUCCESSOR = "candidate_correction_successor"
    EXPLICIT = "explicit"


class TeamBlockerState(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class TeamBlockerResolutionScope(str, Enum):
    TASK = "task"
    DOWNSTREAM = "downstream"


class TeamBlockerRoute(str, Enum):
    OWNED = "owned"
    DELEGATED = "delegated"
    GOVERNANCE = "governance"
    OPERATOR = "operator"
    ROUTING_FAILED = "routing_failed"
    RESOLVED = "resolved"


class TeamBlockerEventType(str, Enum):
    REPORTED = "reported"
    PROGRESS = "progress"
    DELEGATED = "delegated"
    DELEGATE_RESULT = "delegate_result"
    DELEGATION_STALLED = "delegation_stalled"
    OWNER_TRANSFERRED = "owner_transferred"
    RESOLVED = "resolved"
    TERMINAL_ROUTED = "terminal_routed"
    ROUTING_FAILED = "routing_failed"


class GovernanceAgendaItemType(str, Enum):
    OPERATOR_DIRECTIVE = "operator_directive"
    TEAM_ESCALATION = "team_escalation"
    COST_CAP_WARNING = "cost_cap_warning"
    COST_CAP_REACHED = "cost_cap_reached"
    STALE_TEAM = "stale_team"
    BLOCKED_TEAM = "blocked_team"
    SCHEDULED_CHECKIN = "scheduled_checkin"
    POLICY_DECISION = "policy_decision"


class GovernanceAgendaItemState(str, Enum):
    PENDING = "pending"
    HANDLED = "handled"


class TeamCostBudgetRenewalMode(str, Enum):
    MANUAL = "manual"
    MONTHLY_UTC = "monthly_utc"


class TeamCostBudgetWindowKind(str, Enum):
    CONFIGURED = "configured"
    MANUAL_RESET = "manual_reset"
    MONTHLY_RENEWAL = "monthly_renewal"


class TeamMemoryKind(str, Enum):
    DECISION = "decision"
    PREFERENCE = "preference"
    FACT = "fact"
    RUNBOOK = "runbook"
    POSTMORTEM = "postmortem"
    RESEARCH = "research"
    HANDOFF = "handoff"
    OTHER = "other"


class TeamMemoryScope(str, Enum):
    TEAM_GLOBAL = "team_global"
    PROJECT = "project"
    AGENT_PRIVATE = "agent_private"
    GOVERNANCE = "governance"


class TeamMemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class TeamModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


def summarize_worktree_cleanup_inventory(
    value: object,
) -> tuple[list[str], int, str | None, bool]:
    """Return bounded, deterministic evidence for a complete ignored-path inventory."""
    inventory = sorted(set(_text_list(value or [], "ignored_file_inventory")))
    count = len(inventory)
    digest = (
        hashlib.sha256(
            json.dumps(inventory, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if inventory
        else None
    )
    return (
        inventory[:WORKTREE_CLEANUP_INVENTORY_SAMPLE_LIMIT],
        count,
        digest,
        count > WORKTREE_CLEANUP_INVENTORY_SAMPLE_LIMIT,
    )


class EphemeralAgentPolicy(TeamModel):
    max_concurrent: int = Field(default=0, ge=0)


class AgentAvatar(TeamModel):
    asset_id: str
    source: Literal["uploaded", "generated"]
    mime_type: str = "image/webp"
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)
    style_preset: str | None = None
    prompt_note: str | None = None
    model: str | None = None
    preview_id: str | None = None

    @field_validator("asset_id", "source", "mime_type", "sha256", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("style_preset", "prompt_note", "model", "preview_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class AgentVoiceProfile(TeamModel):
    voice: str | None = None
    speaking_style: str | None = Field(default=None, max_length=1200)

    @field_validator("voice", mode="before")
    @classmethod
    def _voice(cls, value: object) -> str | None:
        text = _optional_text(value)
        if text is None:
            return None
        normalized = text.casefold()
        if normalized not in REALTIME_BUILT_IN_VOICE_IDS:
            choices = ", ".join(REALTIME_BUILT_IN_VOICE_IDS)
            raise ValueError(f"voice must be one of: {choices}")
        return normalized

    @field_validator("speaking_style", mode="before")
    @classmethod
    def _speaking_style(cls, value: object) -> str | None:
        return _optional_text(value)

    @property
    def configured(self) -> bool:
        return self.voice is not None or self.speaking_style is not None


class TeamRole(TeamModel):
    id: str
    team_id: str
    project_id: str | None = None
    name: str
    description: str = ""
    permissions: list[RolePermission] = Field(default_factory=list)
    ephemeral_policy: EphemeralAgentPolicy = Field(default_factory=EphemeralAgentPolicy)
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

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str:
        return _optional_text(value) or ""

    @field_validator("permissions", mode="before")
    @classmethod
    def _permissions(cls, value: object) -> list[RolePermission]:
        if isinstance(value, (list, tuple)):
            return [
                item if isinstance(item, RolePermission) else RolePermission(str(item))
                for item in value
            ]
        items = _unique_text_list(value, "permissions")
        return [RolePermission(item) for item in items]


class ProjectGoal(TeamModel):
    id: str
    description: str
    state: TeamRecordState = TeamRecordState.PENDING

    @field_validator("id", "description", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class GovernanceGoal(TeamModel):
    id: str
    name: str
    description: str
    state: TeamRecordState = TeamRecordState.PENDING

    @model_validator(mode="before")
    @classmethod
    def _legacy_name(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        if _optional_text(payload.get("name")) is None:
            payload["name"] = _optional_text(payload.get("id")) or _optional_text(
                payload.get("description")
            )
        return payload

    @field_validator("id", "name", "description", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class GovernanceGroup(TeamModel):
    id: str
    name: str
    manager_agent_id: str
    member_team_ids: list[str] = Field(default_factory=list)
    goals: list[GovernanceGoal] = Field(default_factory=list)
    max_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "name", "manager_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("member_team_ids", mode="before")
    @classmethod
    def _member_team_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "member_team_ids")

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _optional_cost_limit(cls, value: object) -> Decimal | None:
        text = _optional_text(value)
        if text is None:
            return None
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValueError("max_cost_usd must be a non-negative decimal") from None
        if amount < 0:
            raise ValueError("max_cost_usd must be a non-negative decimal")
        return amount

    @field_serializer("max_cost_usd", when_used="json")
    def _serialize_max_cost_usd(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_goal_ids(self) -> "GovernanceGroup":
        _ensure_unique([goal.id for goal in self.goals], "governance goals")
        return self


class GovernanceAgendaItem(TeamModel):
    id: str
    governance_group_id: str
    item_type: GovernanceAgendaItemType
    title: str
    summary: str
    team_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    state: GovernanceAgendaItemState = GovernanceAgendaItemState.PENDING
    priority: TeamInboxPriority = TeamInboxPriority.NORMAL
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "governance_group_id", "title", "summary", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("team_id", "project_id", "agent_id", mode="before")
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


class GovernanceRuntime(TeamModel):
    governance_group_id: str
    wake_leased_until: datetime | None = None
    active_dispatch_id: str | None = None
    preparation_expires_at: datetime | None = None
    queued_at: datetime | None = None
    requested_lease_seconds: int = Field(default=900, ge=1)
    wake_reason: str | None = None
    last_wake_at: datetime | None = None
    next_wake_at: datetime | None = None
    last_cost_signal_fingerprints: list[str] = Field(default_factory=list)
    last_blocked_signal_fingerprints: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def id(self) -> str:
        return self.governance_group_id

    @field_validator("governance_group_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("active_dispatch_id", "wake_reason", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator(
        "last_cost_signal_fingerprints",
        "last_blocked_signal_fingerprints",
        mode="before",
    )
    @classmethod
    def _fingerprints(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


class TeamCostBudgetWindow(TeamModel):
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    kind: TeamCostBudgetWindowKind
    actor: str
    reason: str
    max_cost_usd: Decimal = Field(ge=Decimal("0"))
    monthly_renewal_not_before: datetime | None = None

    @field_validator("id", "actor", "reason", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _cost_limit(cls, value: object) -> Decimal:
        text = _optional_text(value)
        if text is None:
            raise ValueError("max_cost_usd must be a non-negative decimal")
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValueError("max_cost_usd must be a non-negative decimal") from None
        if amount < 0:
            raise ValueError("max_cost_usd must be a non-negative decimal")
        return amount

    @field_serializer("max_cost_usd", when_used="json")
    def _serialize_max_cost_usd(self, value: Decimal) -> float:
        return float(value)

    @model_validator(mode="after")
    def _validate_window_bounds(self) -> "TeamCostBudgetWindow":
        if self.ended_at is not None and self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class Team(TeamModel):
    id: str
    name: str
    manager_agent_id: str
    operator_communication_target_id: str | None = None
    max_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    cost_warning_ratio: Decimal = Field(
        default=DEFAULT_TEAM_COST_WARNING_RATIO,
        gt=Decimal("0"),
        le=Decimal("1"),
    )
    cost_budget_renewal_mode: TeamCostBudgetRenewalMode = TeamCostBudgetRenewalMode.MANUAL
    cost_budget_windows: list[TeamCostBudgetWindow] = Field(default_factory=list)
    escalate_to_governance: bool = False
    project_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    archived: bool = False

    @field_validator("id", "name", "manager_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("operator_communication_target_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _optional_cost_limit(cls, value: object) -> Decimal | None:
        text = _optional_text(value)
        if text is None:
            return None
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValueError("max_cost_usd must be a non-negative decimal") from None
        if amount < 0:
            raise ValueError("max_cost_usd must be a non-negative decimal")
        return amount

    @field_validator("cost_warning_ratio", mode="before")
    @classmethod
    def _cost_warning_ratio(cls, value: object) -> Decimal:
        text = _optional_text(value)
        if text is None:
            raise ValueError("cost_warning_ratio must be greater than 0 and no more than 1")
        try:
            ratio = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValueError(
                "cost_warning_ratio must be greater than 0 and no more than 1"
            ) from None
        if ratio <= 0 or ratio > 1:
            raise ValueError("cost_warning_ratio must be greater than 0 and no more than 1")
        return ratio

    @field_serializer("max_cost_usd", when_used="json")
    def _serialize_max_cost_usd(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    @field_serializer("cost_warning_ratio", when_used="json")
    def _serialize_cost_warning_ratio(self, value: Decimal) -> float:
        return float(value)

    @field_validator("cost_budget_windows", mode="before")
    @classmethod
    def _cost_budget_windows(cls, value: object) -> list[object]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("cost_budget_windows must be a list")
        return list(value)

    @model_validator(mode="after")
    def _validate_cost_budget_windows(self) -> "Team":
        _ensure_unique([window.id for window in self.cost_budget_windows], "cost budget windows")
        active_windows = [window for window in self.cost_budget_windows if window.ended_at is None]
        if len(active_windows) > 1:
            raise ValueError("a team may have only one active cost budget window")
        return self

    @field_validator("project_ids", mode="before")
    @classmethod
    def _project_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "project_ids")


class ProjectGitIdentity(TeamModel):
    name: str
    email: str

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        normalized = _required_text(value, "name")
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("name must be single-line text")
        return normalized

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value: object) -> str:
        normalized = _required_text(value, "email")
        if (
            "\n" in normalized
            or "\r" in normalized
            or re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", normalized) is None
        ):
            raise ValueError("email must be a valid email address")
        return normalized


class ProjectGitConfig(TeamModel):
    managed_repo_id: str
    directory_path: str = "root"
    base_branch: str | None = None
    canonical_release_branch_prefix: str = DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX
    agent_author_identity: ProjectGitIdentity | None = None
    agent_committer_identity: ProjectGitIdentity | None = None

    @field_validator("managed_repo_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        normalized = _required_text(value, info.field_name)
        if re.fullmatch(r"mr_[0-9a-f]{20}", normalized) is None:
            raise ValueError("managed_repo_id must match mr_<20-hex>")
        return normalized

    @field_validator("directory_path", mode="before")
    @classmethod
    def _directory_path(cls, value: object) -> str:
        return DirectoryPath(segments=str(value or "root").strip() or "root").as_posix()

    @field_validator("base_branch", mode="before")
    @classmethod
    def _optional_base_branch(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("canonical_release_branch_prefix", mode="before")
    @classmethod
    def _canonical_release_branch_prefix(cls, value: object) -> str:
        return normalize_canonical_release_branch_prefix(value)


class ReleaseVerificationCommand(TeamModel):
    name: str
    cwd: str = "."
    argv: list[str]
    timeout_seconds: int = Field(default=3600, ge=1, le=14400)

    @field_validator("name", "cwd", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("argv", mode="before")
    @classmethod
    def _argv(cls, value: object) -> list[str]:
        values = _text_list(value, "argv")
        if not values:
            raise ValueError("argv must contain at least one command argument")
        return values


class ReleaseReviewRequirement(TeamModel):
    reviewer_agent_id: str
    review_profile_id: str

    @field_validator("reviewer_agent_id", "review_profile_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class HumanReviewChecklistItem(TeamModel):
    id: str
    label: str
    description: str | None = None
    required: bool = True

    @field_validator("id", "label", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class HumanReviewRequirement(TeamModel):
    reviewer_group_ids: list[str]
    checklist: list[HumanReviewChecklistItem] = Field(default_factory=list)
    test_instructions: str = ""
    preparation_mode: Literal["manual", "workflow"] = "manual"
    # These two fields are resolved by the Admin boundary and then snapshotted
    # into the release manifest. They are never trusted from a human decision.
    directory_path: str | None = None
    resolved_reviewer_user_ids: list[str] = Field(default_factory=list)

    @field_validator("reviewer_group_ids", mode="before")
    @classmethod
    def _reviewer_group_ids(cls, value: object) -> list[str]:
        values = _unique_text_list(value, "reviewer_group_ids")
        if not values:
            raise ValueError("human review requires at least one reviewer group")
        return values

    @field_validator("resolved_reviewer_user_ids", mode="before")
    @classmethod
    def _resolved_reviewer_user_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "resolved_reviewer_user_ids")

    @field_validator("directory_path", mode="before")
    @classmethod
    def _directory_path(cls, value: object) -> str | None:
        text = _optional_text(value)
        return DirectoryPath(segments=text).as_posix() if text is not None else None

    @field_validator("test_instructions", mode="before")
    @classmethod
    def _test_instructions(cls, value: object) -> str:
        return _optional_text(value) or ""

    @model_validator(mode="after")
    def _unique_checklist_ids(self) -> "HumanReviewRequirement":
        _ensure_unique([item.id for item in self.checklist], "human review checklist items")
        return self


class ProjectReleasePolicy(TeamModel):
    verification_backend: Literal["container_worker_vm"] = "container_worker_vm"
    container_image: str | None = None
    resource_limits: WorkerResourceLimits | None = None
    verification_commands: list[ReleaseVerificationCommand] = Field(default_factory=list)
    review_requirements: list[ReleaseReviewRequirement] = Field(default_factory=list)
    human_review_requirement: HumanReviewRequirement | None = None
    allow_ungated: bool = False

    @field_validator("container_image", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _require_gate(self) -> "ProjectReleasePolicy":
        if not self.allow_ungated and not (
            self.verification_commands
            or self.review_requirements
            or self.human_review_requirement is not None
        ):
            raise ValueError(
                "release policy requires verification_commands, review_requirements, "
                "or human_review_requirement unless allow_ungated is true"
            )
        review_keys = [
            (requirement.reviewer_agent_id, requirement.review_profile_id)
            for requirement in self.review_requirements
        ]
        if len(review_keys) != len(set(review_keys)):
            raise ValueError(
                "release policy review_requirements must use unique reviewer/profile pairs"
            )
        return self


class Project(TeamModel):
    id: str
    team_id: str
    name: str
    lead_agent_id: str
    directory_path: str = "root"
    goals: list[ProjectGoal] = Field(default_factory=list)
    git: ProjectGitConfig | None = None
    release_policy: ProjectReleasePolicy | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "team_id", "name", "lead_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def _default_directory_path_from_legacy_git(cls, value: object) -> object:
        if not isinstance(value, Mapping) or "directory_path" in value:
            return value
        git = value.get("git")
        if not isinstance(git, Mapping):
            return value
        git_directory_path = _optional_text(git.get("directory_path"))
        if git_directory_path is None:
            return value
        return {**value, "directory_path": git_directory_path}

    @field_validator("directory_path", mode="before")
    @classmethod
    def _directory_path(cls, value: object) -> str:
        return DirectoryPath(segments=str(value or "root").strip() or "root").as_posix()

    @model_validator(mode="after")
    def _validate_goal_ids(self) -> "Project":
        _ensure_unique([goal.id for goal in self.goals], "project goals")
        if self.git is not None and self.git.directory_path != self.directory_path:
            raise ValueError(
                "project git directory_path must match the provider-neutral project directory_path"
            )
        return self


class AgentProjectAssignment(TeamModel):
    id: str
    team_id: str
    project_id: str
    agent_id: str
    role_id: str | None = None
    kind: AgentProjectAssignmentKind = AgentProjectAssignmentKind.PROJECT_MEMBER
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "team_id", "project_id", "agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("role_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class PersistentAgent(TeamModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    team_id: str | None = None
    display_name: str | None = None
    avatar: AgentAvatar | None = None
    voice_profile: AgentVoiceProfile | None = None
    role_id: str | None = None
    session_id: str
    reports_to_agent_id: str | None = None
    retired_team_id: str | None = None
    subordinate_agent_ids: list[str] = Field(default_factory=list)
    ephemeral_policy: EphemeralAgentPolicy = Field(default_factory=EphemeralAgentPolicy)
    status: AgentStatus = AgentStatus.ACTIVE

    @field_validator("id", "session_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "team_id",
        "display_name",
        "role_id",
        "reports_to_agent_id",
        "retired_team_id",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("subordinate_agent_ids", mode="before")
    @classmethod
    def _subordinate_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "subordinate_agent_ids")

    @model_validator(mode="after")
    def _validate_self_links(self) -> "PersistentAgent":
        if self.reports_to_agent_id == self.id:
            raise ValueError("agent cannot report to itself")
        if self.id in self.subordinate_agent_ids:
            raise ValueError("agent cannot list itself as a subordinate")
        if self.team_id is None:
            if self.role_id is not None:
                raise ValueError("teamless agent cannot have a team-scoped role")
            if self.reports_to_agent_id is not None:
                raise ValueError("teamless agent cannot report to another agent")
            if self.subordinate_agent_ids:
                raise ValueError("teamless agent cannot have subordinate agents")
        elif self.role_id is None:
            raise ValueError("team-bound agent must have a role")
        if self.team_id is not None and self.retired_team_id is not None:
            raise ValueError("active team agent cannot retain a retired team id")
        return self


class AgentRuntime(TeamModel):
    agent_id: str
    wake_leased_until: datetime | None = None
    active_dispatch_id: str | None = None
    wake_reason: str | None = None
    last_wake_at: datetime | None = None
    next_wake_at: datetime | None = None
    no_progress_count: int = Field(default=0, ge=0)
    last_progress_at: datetime | None = None
    low_priority_batch_started_at: datetime | None = None
    low_priority_batch_cycle_count: int = Field(default=0, ge=0)
    low_priority_batch_due_after_cycles: int | None = Field(default=None, ge=0)
    context_clearing_policy: AgentContextClearingPolicy = AgentContextClearingPolicy.OFF
    context_clearing_idle_grace_seconds: int = Field(default=0, ge=0, le=604800)
    context_idle_since_at: datetime | None = None
    context_idle_rotation_due_at: datetime | None = None
    auto_context_clearing_enabled: bool = False
    context_rotation_pending: bool = False
    context_rotation_requested_by: str | None = None
    context_rotation_requested_at: datetime | None = None
    context_rotation_reason: Literal["idle", "manual", "every_wake"] | None = None
    context_rotation_request_note: str | None = None
    last_handoff_summary: str | None = None
    context_handoff_preparation_pending: bool = False
    context_handoff_preparation_status: (
        Literal["pending", "recorded", "failed", "timed_out"] | None
    ) = None
    last_agent_handoff_summary: str | None = None
    last_agent_handoff_at: datetime | None = None
    last_context_rotated_at: datetime | None = None
    last_thread_id: str | None = None
    work_lineage_thread_ids: dict[str, str] = Field(default_factory=dict)
    last_delivered_role_description_fingerprint: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "active_dispatch_id",
        "wake_reason",
        "context_rotation_requested_by",
        "context_rotation_request_note",
        "last_handoff_summary",
        "last_agent_handoff_summary",
        "last_thread_id",
        "last_delivered_role_description_fingerprint",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("work_lineage_thread_ids", mode="before")
    @classmethod
    def _work_lineage_thread_ids(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("work_lineage_thread_ids must be an object")
        return {
            _required_text(lineage_id, "work lineage id"): _required_text(
                thread_id, "work lineage thread id"
            )
            for lineage_id, thread_id in value.items()
        }

    @model_validator(mode="before")
    @classmethod
    def _default_context_clearing_policy(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        raw_policy = payload.get("context_clearing_policy")
        if raw_policy in (None, ""):
            legacy_auto = payload.get("auto_context_clearing_enabled")
            legacy_enabled = (
                legacy_auto is True
                or (
                    isinstance(legacy_auto, int)
                    and not isinstance(legacy_auto, bool)
                    and legacy_auto != 0
                )
                or (
                    isinstance(legacy_auto, str)
                    and legacy_auto.strip().casefold() in {"1", "true", "yes", "on"}
                )
            )
            payload["context_clearing_policy"] = (
                AgentContextClearingPolicy.IDLE.value
                if legacy_enabled
                else AgentContextClearingPolicy.OFF.value
            )
        return payload

    @model_validator(mode="after")
    def _sync_context_clearing_compat_flag(self) -> "AgentRuntime":
        self.auto_context_clearing_enabled = (
            self.context_clearing_policy != AgentContextClearingPolicy.OFF
        )
        return self


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

    @field_validator("required_contribution_task_ids", mode="before")
    @classmethod
    def _required_contribution_task_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "required_contribution_task_ids")


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


class AgentTask(TeamModel):
    id: str
    owner_agent_id: str
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
        "owner_agent_id",
        "project_id",
        "title",
        "description",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
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
        if self.task_kind == TeamTaskKind.OUTCOME and self.outcome_contract is None:
            raise ValueError("outcome task requires outcome_contract")
        if self.task_kind == TeamTaskKind.SUPPORT and self.outcome_contract is not None:
            raise ValueError("support task cannot define outcome_contract")
        if self.task_kind == TeamTaskKind.SUPPORT and self.completion is not None:
            raise ValueError("support task cannot define structured completion")
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
    task_ids: list[str] = Field(default_factory=list)
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


class TeamConversation(TeamModel):
    id: str
    agent_id: str
    operator_user_id: str = "admin"
    operator_display_name: str | None = None
    created_by: str | None = None
    last_message_preview: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_interaction_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "agent_id", "operator_user_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("operator_display_name", "created_by", "last_message_preview", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class WorkLineageChatSourceRef(TeamModel):
    conversation_id: str
    message_ids: list[str] = Field(default_factory=list)

    @field_validator("conversation_id", mode="before")
    @classmethod
    def _conversation_id(cls, value: object) -> str:
        return _required_text(value, "conversation_id")

    @field_validator("message_ids", mode="before")
    @classmethod
    def _message_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "message_ids")

    @model_validator(mode="after")
    def _require_messages(self) -> "WorkLineageChatSourceRef":
        if not self.message_ids:
            raise ValueError("work lineage Chat source reference requires message_ids")
        return self


class TeamWorkLineage(TeamModel):
    id: str
    chat_source_refs: list[WorkLineageChatSourceRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return _required_text(value, "id")

    @model_validator(mode="after")
    def _unique_conversations(self) -> "TeamWorkLineage":
        conversation_ids = [ref.conversation_id for ref in self.chat_source_refs]
        if len(conversation_ids) != len(set(conversation_ids)):
            raise ValueError("work lineage Chat source conversations must be unique")
        return self


def filter_work_lineage_payloads(
    value: object,
    *,
    visible_conversation_ids: set[str],
    visible_lineage_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return lineage metadata without disclosing out-of-scope Chat anchors.

    When ``visible_lineage_ids`` is omitted, Chat-backed lineages are visible
    only when at least one of their source conversations is visible. Lineages
    without Chat anchors remain visible because they carry no Chat metadata.
    Scoped state projections retain only structurally referenced lineages while
    still removing any source refs outside their Chat scope.
    """

    if not isinstance(value, Mapping):
        return {}
    filtered: dict[str, dict[str, Any]] = {}
    for lineage_id, raw_lineage in value.items():
        if not isinstance(lineage_id, str) or not isinstance(raw_lineage, Mapping):
            continue
        raw_refs = raw_lineage.get("chat_source_refs")
        refs = raw_refs if isinstance(raw_refs, list) else []
        visible_refs = [
            dict(ref)
            for ref in refs
            if isinstance(ref, Mapping) and ref.get("conversation_id") in visible_conversation_ids
        ]
        if visible_lineage_ids is None:
            include_lineage = not refs or bool(visible_refs)
        else:
            include_lineage = lineage_id in visible_lineage_ids
        if include_lineage:
            filtered[lineage_id] = {
                **dict(raw_lineage),
                "chat_source_refs": visible_refs,
            }
    return filtered


def filter_work_lineage_references(
    payload: dict[str, Any],
    *,
    visible_lineage_ids: set[str],
) -> None:
    """Remove lineage references that are absent from a filtered state payload."""

    for collection_name in ("tasks", "release_packages", "dispatches"):
        collection = payload.get(collection_name)
        if not isinstance(collection, Mapping):
            continue
        for row in collection.values():
            if not isinstance(row, dict):
                continue
            lineage_id = row.get("work_lineage_id")
            if lineage_id is not None and lineage_id not in visible_lineage_ids:
                row["work_lineage_id"] = None
    runtimes = payload.get("agent_runtimes")
    if not isinstance(runtimes, Mapping):
        return
    for runtime in runtimes.values():
        if not isinstance(runtime, dict):
            continue
        thread_ids = runtime.get("work_lineage_thread_ids")
        runtime["work_lineage_thread_ids"] = {
            lineage_id: thread_id
            for lineage_id, thread_id in (
                thread_ids.items() if isinstance(thread_ids, Mapping) else ()
            )
            if lineage_id in visible_lineage_ids
        }


class TeamChatMutationAuthorization(TeamModel):
    """Server-issued mutation scope snapshot for one operator chat message."""

    version: Literal[1] = 1
    mode: Literal["reply_only", "project_scoped"] = "reply_only"
    source: Literal["admin_rbac", "external_bridge", "internal"]
    principal_id: str
    recipient_directory_path: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    managed_repo_ids: list[str] = Field(default_factory=list)
    authorized_at: datetime = Field(default_factory=utc_now)
    reason: str | None = None

    @field_validator("principal_id", mode="before")
    @classmethod
    def _principal_id(cls, value: object) -> str:
        return _required_text(value, "principal_id")

    @field_validator("recipient_directory_path", mode="before")
    @classmethod
    def _recipient_directory_path(cls, value: object) -> str | None:
        text = _optional_text(value)
        return DirectoryPath(segments=text).as_posix() if text is not None else None

    @field_validator("project_ids", "managed_repo_ids", mode="before")
    @classmethod
    def _scope_ids(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_scope(self) -> "TeamChatMutationAuthorization":
        if self.mode == "reply_only":
            if self.project_ids or self.managed_repo_ids:
                raise ValueError("reply-only Team chat authorization cannot contain scope ids")
            return self
        if self.recipient_directory_path is None:
            raise ValueError(
                "project-scoped Team chat authorization requires recipient_directory_path"
            )
        if not self.project_ids:
            raise ValueError("project-scoped Team chat authorization requires project_ids")
        return self


class TeamConversationMessage(TeamModel):
    id: str
    conversation_id: str
    sender: Literal["operator", "agent", "system"]
    message_type: Literal["text", "voice_call", "system"] = "text"
    body: str | None = None
    status: Literal[
        "created",
        "dispatching",
        "queued",
        "running",
        "seen",
        "delivered_in_dispatch",
        "acknowledged",
        "replied",
        "failed",
        "sent",
        "ended",
    ] = "sent"
    dispatch_id: str | None = None
    voice_session_id: str | None = None
    source_message_id: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)
    seen_at: datetime | None = None
    seen_dispatch_id: str | None = None
    error: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "conversation_id", "sender", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "body",
        "dispatch_id",
        "voice_session_id",
        "source_message_id",
        "seen_dispatch_id",
        "error",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("source_message_ids", mode="before")
    @classmethod
    def _source_message_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "source_message_ids")

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        return dict(value)

    @model_validator(mode="after")
    def _validate_body(self) -> "TeamConversationMessage":
        if self.message_type == "text" and not self.body and not self.attachment_ids:
            raise ValueError("text conversation messages require body or attachments")
        return self


class ExternalChatIngressRecord(TeamModel):
    """Durable external-message provenance and ingress idempotency record."""

    id: str
    provider: str
    communication_target_id: str
    source_message_id: str
    source_thread_id: str | None = None
    source_author_id: str
    source_author_name: str | None = None
    operator_user_id: str
    conversation_id: str
    message_id: str
    dispatch_id: str
    allow_team_mutations: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "provider",
        "communication_target_id",
        "source_message_id",
        "source_author_id",
        "operator_user_id",
        "conversation_id",
        "message_id",
        "dispatch_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("source_thread_id", "source_author_name", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class ExternalChatDeliveryRecord(TeamModel):
    """A persisted request to return one Team Chat reply through its source target."""

    id: str
    agent_reply_message_id: str
    operator_message_id: str
    provider: str
    communication_target_id: str
    source_thread_id: str | None = None
    status: Literal["pending", "sending", "sent", "failed", "unknown"] = "pending"
    provider_message_id: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    attempted_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "agent_reply_message_id",
        "operator_message_id",
        "provider",
        "communication_target_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("source_thread_id", "provider_message_id", "last_error", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class TeamChatAttachmentExternalSource(TeamModel):
    provider: Literal["teams", "discord", "email"]
    communication_target_id: str
    source_message_id: str
    source_attachment_id: str

    @field_validator(
        "provider",
        "communication_target_id",
        "source_message_id",
        "source_attachment_id",
        mode="before",
    )
    @classmethod
    def _required_source_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class TeamChatAttachment(TeamModel):
    id: str
    message_id: str
    conversation_id: str
    filename: str
    mime_type: str | None = None
    byte_count: int = Field(ge=0)
    sha256: str
    sender: Literal["operator", "agent"]
    created_by_session_id: str | None = None
    external_source: TeamChatAttachmentExternalSource | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "message_id",
        "conversation_id",
        "filename",
        "sha256",
        "sender",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("mime_type", "created_by_session_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class TeamChatAttachmentPolicy(TeamModel):
    id: str
    directory_path: str
    operator_to_agent_enabled: bool | None = None
    agent_to_operator_enabled: bool | None = None
    agent_send_requires_approval: bool | None = None
    max_attachment_bytes: int | None = Field(default=None, ge=0)
    max_attachments_per_message: int | None = Field(default=None, ge=1, le=20)
    allowed_mime_types: list[str] | None = None
    allowed_extensions: list[str] | None = None
    blocked_extensions: list[str] | None = None
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "directory_path", mode="before")
    @classmethod
    def _directory_text(cls, value: object, info) -> str:
        normalized = DirectoryPath(segments=str(value or "").strip()).as_posix()
        return _required_text(normalized, info.field_name)

    @field_validator("updated_by", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator(
        "allowed_mime_types",
        "allowed_extensions",
        "blocked_extensions",
        mode="before",
    )
    @classmethod
    def _optional_text_list(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("attachment policy lists must be arrays")
        return sorted(
            {str(item or "").strip().lower() for item in value if str(item or "").strip()}
        )

    @model_validator(mode="after")
    def _matching_directory_id(self) -> "TeamChatAttachmentPolicy":
        if self.id != self.directory_path:
            raise ValueError("attachment policy id must match directory_path")
        return self

    @classmethod
    def root_defaults(cls) -> "TeamChatAttachmentPolicy":
        return cls(
            id="root",
            directory_path="root",
            operator_to_agent_enabled=True,
            agent_to_operator_enabled=True,
            agent_send_requires_approval=True,
            max_attachment_bytes=TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_BYTES,
            max_attachments_per_message=TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_FILES,
            allowed_mime_types=list(TEAM_CHAT_ATTACHMENT_DEFAULT_MIME_TYPES),
            allowed_extensions=list(TEAM_CHAT_ATTACHMENT_DEFAULT_EXTENSIONS),
            blocked_extensions=list(TEAM_CHAT_ATTACHMENT_DEFAULT_BLOCKED_EXTENSIONS),
        )


class AgentStandDown(TeamModel):
    id: str
    agent_id: str
    scope: Literal["agent", "subtree"] = "subtree"
    reason: str
    source: AgentStandDownSource = AgentStandDownSource.OPERATOR
    set_by: str
    set_at: datetime = Field(default_factory=utc_now)
    cleared_by: str | None = None
    cleared_at: datetime | None = None

    @field_validator("id", "agent_id", "reason", "set_by", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("cleared_by", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @property
    def active(self) -> bool:
        return self.cleared_at is None


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


class ReleaseVerificationRun(TeamModel):
    id: str
    release_package_id: str
    candidate_commit_sha: str
    session_id: str
    state: Literal["pending", "running", "passed", "failed", "timed_out"] = "pending"
    provenance: Literal["server_executed", "server_acceptance_reuse"] = "server_executed"
    evidence_refs: list[str] = Field(default_factory=list)
    container_image: str | None = None
    resource_limits: WorkerResourceLimits | None = None
    verifier_settings_snapshotted: bool = False
    attempt_number: int = Field(default=1, ge=1)
    failure_kind: Literal["candidate", "infrastructure", "timeout"] | None = None
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
    stage: Literal["verification", "review", "human_review", "configuration", "lineage"]
    kind: Literal[
        "candidate",
        "infrastructure",
        "timeout",
        "blocked",
        "rejected",
        "changes_requested",
        "configuration",
        "convergence",
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
        terminal = self.state in {"approved", "changes_requested"}
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


class ReleaseCandidateManifest(TeamModel):
    schema_version: Literal[1, 2] = 2
    evidence_status: Literal["complete", "legacy_partial"] = "complete"
    project_id: str
    release_package_id: str
    base_commit_sha: str | None = None
    candidate_commit_sha: str
    candidate_tree_hash: str | None = None
    release_branch: str
    deliverables: list[ReleaseCandidateDeliverableEvidence] = Field(default_factory=list)
    scope_validation: ReleaseScopeValidation | None = None
    verification_commands: list[ReleaseVerificationCommand] = Field(default_factory=list)
    review_requirements: list[ReleaseReviewRequirement] = Field(default_factory=list)
    human_review_requirement: HumanReviewRequirementSnapshot | None = None
    predecessor_release_package_id: str | None = None
    correction_deliverable_ids: list[str] = Field(default_factory=list)
    carried_human_findings: list[HumanReviewFindingSnapshot] = Field(default_factory=list)
    contribution_graph: dict[str, list[str]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

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
    scope_validation: ReleaseScopeValidation | None = None
    state: TeamRecordState = TeamRecordState.PENDING
    published_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime | None = None

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


class TeamState(TeamModel):
    schema_version: int = SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    teams: dict[str, Team] = Field(default_factory=dict)
    governance_groups: dict[str, GovernanceGroup] = Field(default_factory=dict)
    governance_agenda: dict[str, GovernanceAgendaItem] = Field(default_factory=dict)
    governance_runtimes: dict[str, GovernanceRuntime] = Field(default_factory=dict)
    projects: dict[str, Project] = Field(default_factory=dict)
    roles: dict[str, TeamRole] = Field(default_factory=dict)
    agents: dict[str, PersistentAgent] = Field(default_factory=dict)
    project_assignments: dict[str, AgentProjectAssignment] = Field(default_factory=dict)
    agent_runtimes: dict[str, AgentRuntime] = Field(default_factory=dict)
    work_lineages: dict[str, "TeamWorkLineage"] = Field(default_factory=dict)
    cases: dict[str, TeamCase] = Field(default_factory=dict)
    tasks: dict[str, AgentTask] = Field(default_factory=dict)
    external_source_evidence_snapshots: dict[str, ExternalSourceEvidenceSnapshot] = Field(
        default_factory=dict
    )
    deliverables: dict[str, Deliverable] = Field(default_factory=dict)
    review_requests: dict[str, ReviewRequest] = Field(default_factory=dict)
    outcome_kind_definitions: dict[str, OutcomeKindDefinition] = Field(default_factory=dict)
    review_profiles: dict[str, ReviewProfile] = Field(default_factory=dict)
    inbox: dict[str, AgentInboxItem] = Field(default_factory=dict)
    blockers: dict[str, TeamBlocker] = Field(default_factory=dict)
    dependency_composition_recoveries: dict[str, DependencyCompositionRecovery] = Field(
        default_factory=dict
    )
    blocker_events: dict[str, TeamBlockerEvent] = Field(default_factory=dict)
    action_records: dict[str, AgentActionRecord] = Field(default_factory=dict)
    memories: dict[str, TeamMemoryRecord] = Field(default_factory=dict)
    conversations: dict[str, TeamConversation] = Field(default_factory=dict)
    conversation_messages: dict[str, TeamConversationMessage] = Field(default_factory=dict)
    external_chat_ingress: dict[str, ExternalChatIngressRecord] = Field(default_factory=dict)
    external_chat_deliveries: dict[str, ExternalChatDeliveryRecord] = Field(default_factory=dict)
    chat_attachments: dict[str, TeamChatAttachment] = Field(default_factory=dict)
    chat_attachment_policies: dict[str, TeamChatAttachmentPolicy] = Field(default_factory=dict)
    release_packages: dict[str, ReleasePackage] = Field(default_factory=dict)
    release_verification_runs: dict[str, ReleaseVerificationRun] = Field(default_factory=dict)
    human_review_requests: dict[str, HumanReviewRequest] = Field(default_factory=dict)
    human_review_decisions: dict[str, HumanReviewDecision] = Field(default_factory=dict)
    human_review_findings: dict[str, HumanReviewFinding] = Field(default_factory=dict)
    dispatches: dict[str, TeamDispatch] = Field(default_factory=dict)
    git_preparation_attempts: dict[str, TeamGitPreparationAttempt] = Field(default_factory=dict)
    worktree_cleanup_candidates: dict[str, WorktreeCleanupCandidate] = Field(default_factory=dict)
    ephemeral_assignments: dict[str, EphemeralAssignment] = Field(default_factory=dict)
    git_promotions: dict[str, GitPromotionRecord] = Field(default_factory=dict)
    git_work_contexts: dict[str, GitWorkContext] = Field(default_factory=dict)
    stand_downs: dict[str, AgentStandDown] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def empty(cls) -> "TeamState":
        return cls()

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_project_assignments(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        projects = dict(migrated.get("projects") or {})
        agents = dict(migrated.get("agents") or {})
        roles = dict(migrated.get("roles") or {})
        assignments = dict(migrated.get("project_assignments") or {})

        def role_project_id(role_id: object) -> str | None:
            if role_id is None:
                return None
            role = roles.get(str(role_id))
            if isinstance(role, TeamRole):
                return role.project_id
            if isinstance(role, dict):
                return _optional_text(role.get("project_id"))
            return None

        def assignment_id(project_id: str, agent_id: str, kind: str) -> str:
            return f"{project_id}__{agent_id}__{kind}"

        def assignment_kind_value(value: object) -> str | None:
            if isinstance(value, AgentProjectAssignmentKind):
                return value.value
            text = _optional_text(value)
            if text is None:
                return None
            if text.startswith("AgentProjectAssignmentKind."):
                key = text.rsplit(".", 1)[-1]
                try:
                    return AgentProjectAssignmentKind[key].value
                except KeyError:
                    return text
            return text

        def has_assignment(project_id: str, agent_id: str, kind: str | None = None) -> bool:
            for assignment in assignments.values():
                if isinstance(assignment, AgentProjectAssignment):
                    current_project_id = assignment.project_id
                    current_agent_id = assignment.agent_id
                    current_kind = assignment.kind.value
                elif isinstance(assignment, dict):
                    current_project_id = _optional_text(assignment.get("project_id"))
                    current_agent_id = _optional_text(assignment.get("agent_id"))
                    current_kind = assignment_kind_value(assignment.get("kind"))
                else:
                    continue
                if (
                    current_project_id == project_id
                    and current_agent_id == agent_id
                    and (kind is None or current_kind == kind)
                ):
                    return True
            return False

        def put_assignment(
            *, project_id: str, agent_id: str, team_id: str, role_id: str | None, kind: str
        ) -> None:
            if has_assignment(project_id, agent_id, kind):
                return
            key = assignment_id(project_id, agent_id, kind)
            suffix = 2
            while key in assignments:
                key = f"{assignment_id(project_id, agent_id, kind)}-{suffix}"
                suffix += 1
            assignments[key] = {
                "id": key,
                "team_id": team_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "role_id": role_id,
                "kind": kind,
            }

        for project_id, raw_project in projects.items():
            project = (
                raw_project.model_dump(mode="python")
                if isinstance(raw_project, TeamModel)
                else raw_project
            )
            if not isinstance(project, dict):
                continue
            lead_agent_id = _optional_text(project.get("lead_agent_id"))
            team_id = _optional_text(project.get("team_id"))
            if lead_agent_id is None or team_id is None:
                continue
            raw_agent = agents.get(lead_agent_id)
            agent = (
                raw_agent.model_dump(mode="python")
                if isinstance(raw_agent, TeamModel)
                else raw_agent
            )
            role_id = _optional_text(agent.get("role_id")) if isinstance(agent, dict) else None
            if role_project_id(role_id) != str(project_id):
                role_id = next(
                    (
                        role_key
                        for role_key, raw_role in roles.items()
                        if (
                            raw_role.project_id
                            if isinstance(raw_role, TeamRole)
                            else _optional_text(raw_role.get("project_id"))
                            if isinstance(raw_role, dict)
                            else None
                        )
                        == str(project_id)
                    ),
                    role_id,
                )
            put_assignment(
                project_id=str(project_id),
                agent_id=lead_agent_id,
                team_id=team_id,
                role_id=role_id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD.value,
            )

        for agent_id, raw_agent in list(agents.items()):
            agent = (
                raw_agent.model_dump(mode="python")
                if isinstance(raw_agent, TeamModel)
                else dict(raw_agent)
                if isinstance(raw_agent, dict)
                else raw_agent
            )
            if not isinstance(agent, dict):
                continue
            team_id = _optional_text(agent.get("team_id"))
            legacy_project_id = _optional_text(agent.pop("project_id", None))
            role_id = _optional_text(agent.get("role_id"))
            role_project = role_project_id(role_id)
            project_id = legacy_project_id or role_project
            if (
                team_id is not None
                and project_id is not None
                and not has_assignment(project_id, str(agent_id))
            ):
                put_assignment(
                    project_id=project_id,
                    agent_id=str(agent_id),
                    team_id=team_id,
                    role_id=role_id if role_project == project_id else None,
                    kind=AgentProjectAssignmentKind.PROJECT_MEMBER.value,
                )
            agents[agent_id] = agent

        migrated["agents"] = agents
        migrated["project_assignments"] = assignments
        return migrated

    @model_validator(mode="after")
    def _validate_state(self) -> "TeamState":
        errors: list[str] = []
        self._ensure_project_assignment_defaults()
        self._ensure_core_role_permissions()
        self._validate_key_matches(errors)
        self._validate_teams(errors)
        self._validate_governance_groups(errors)
        self._validate_governance_agenda(errors)
        self._validate_governance_runtimes(errors)
        self._validate_projects(errors)
        self._validate_roles(errors)
        self._validate_agents(errors)
        self._validate_project_assignments(errors)
        self._validate_agent_runtimes(errors)
        self._validate_work_lineages(errors)
        self._validate_external_source_evidence_snapshots(errors)
        self._validate_cases(errors)
        self._validate_tasks(errors)
        self._validate_task_graph(errors)
        self._validate_deliverables(errors)
        self._validate_outcome_kind_definitions(errors)
        self._validate_review_profiles(errors)
        self._validate_reviews(errors)
        self._validate_inbox(errors)
        self._validate_blockers(errors)
        self._validate_action_records(errors)
        self._validate_memories(errors)
        self._validate_conversations(errors)
        self._validate_external_chat_bridge(errors)
        self._validate_releases(errors)
        self._validate_human_reviews(errors)
        self._validate_dispatches(errors)
        self._validate_git_preparation_attempts(errors)
        self._validate_worktree_cleanup_candidates(errors)
        self._validate_ephemeral_assignments(errors)
        self._validate_git_promotions(errors)
        self._validate_stand_downs(errors)
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def ordered_tasks_for_agent(self, agent_id: str) -> list[AgentTask]:
        return sorted(
            [task for task in self.tasks.values() if task.owner_agent_id == agent_id],
            key=lambda task: (task.priority, task.created_at, task.id),
        )

    def _validate_key_matches(self, errors: list[str]) -> None:
        for collection_name in (
            "teams",
            "governance_groups",
            "governance_agenda",
            "governance_runtimes",
            "projects",
            "roles",
            "agents",
            "project_assignments",
            "agent_runtimes",
            "work_lineages",
            "cases",
            "tasks",
            "external_source_evidence_snapshots",
            "deliverables",
            "review_requests",
            "outcome_kind_definitions",
            "review_profiles",
            "inbox",
            "blockers",
            "dependency_composition_recoveries",
            "blocker_events",
            "action_records",
            "memories",
            "conversations",
            "conversation_messages",
            "external_chat_ingress",
            "external_chat_deliveries",
            "chat_attachments",
            "chat_attachment_policies",
            "release_packages",
            "release_verification_runs",
            "human_review_requests",
            "human_review_decisions",
            "human_review_findings",
            "dispatches",
            "git_preparation_attempts",
            "worktree_cleanup_candidates",
            "ephemeral_assignments",
            "git_promotions",
            "git_work_contexts",
            "stand_downs",
        ):
            collection = getattr(self, collection_name)
            for key, record in collection.items():
                record_id = getattr(
                    record,
                    "id",
                    getattr(record, "agent_id", getattr(record, "governance_group_id", None)),
                )
                if key != record_id:
                    errors.append(
                        f"{collection_name}.{key} key does not match record id {record_id}"
                    )

    def _validate_work_lineages(self, errors: list[str]) -> None:
        for lineage in self.work_lineages.values():
            for ref in lineage.chat_source_refs:
                conversation = self.conversations.get(ref.conversation_id)
                if conversation is None:
                    errors.append(
                        f"work lineage {lineage.id} references missing conversation "
                        f"{ref.conversation_id}"
                    )
                for message_id in ref.message_ids:
                    message = self.conversation_messages.get(message_id)
                    if message is None:
                        errors.append(
                            f"work lineage {lineage.id} references missing Chat message "
                            f"{message_id}"
                        )
                    elif message.conversation_id != ref.conversation_id:
                        errors.append(
                            f"work lineage {lineage.id} Chat message {message_id} belongs to "
                            f"conversation {message.conversation_id}, not {ref.conversation_id}"
                        )
        for runtime in self.agent_runtimes.values():
            for lineage_id in runtime.work_lineage_thread_ids:
                if lineage_id not in self.work_lineages:
                    errors.append(
                        f"agent runtime {runtime.agent_id} references missing work lineage "
                        f"{lineage_id}"
                    )
        for record_kind, records in (
            ("task", self.tasks.values()),
            ("release package", self.release_packages.values()),
            ("dispatch", self.dispatches.values()),
        ):
            for record in records:
                lineage_id = record.work_lineage_id
                if lineage_id is not None and lineage_id not in self.work_lineages:
                    errors.append(
                        f"{record_kind} {record.id} references missing work lineage {lineage_id}"
                    )

    def _ensure_project_assignment_defaults(self) -> None:
        now = utc_now()
        for project in self.projects.values():
            if project.lead_agent_id in self.agents and not _agent_has_project_assignment(
                self,
                project.lead_agent_id,
                project.id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD,
            ):
                lead = self.agents[project.lead_agent_id]
                role = self.roles.get(lead.role_id) if lead.role_id is not None else None
                role_id = (
                    lead.role_id if role is not None and role.project_id == project.id else None
                )
                assignment_id = _project_assignment_id(
                    project.id, lead.id, AgentProjectAssignmentKind.PROJECT_LEAD
                )
                self.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=project.team_id,
                    project_id=project.id,
                    agent_id=lead.id,
                    role_id=role_id,
                    kind=AgentProjectAssignmentKind.PROJECT_LEAD,
                    created_at=now,
                    updated_at=now,
                )
        for agent in self.agents.values():
            role = self.roles.get(agent.role_id) if agent.role_id is not None else None
            if role is None or role.project_id is None or agent.team_id is None:
                continue
            if _agent_has_project_assignment(self, agent.id, role.project_id):
                continue
            assignment_id = _project_assignment_id(
                role.project_id, agent.id, AgentProjectAssignmentKind.PROJECT_MEMBER
            )
            self.project_assignments[assignment_id] = AgentProjectAssignment(
                id=assignment_id,
                team_id=agent.team_id,
                project_id=role.project_id,
                agent_id=agent.id,
                role_id=role.id,
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                created_at=now,
                updated_at=now,
            )

    def _ensure_core_role_permissions(self) -> None:
        for team in self.teams.values():
            manager = self.agents.get(team.manager_agent_id)
            role = self.roles.get(manager.role_id) if manager is not None else None
            if role is not None and RolePermission.CREATE_ROOT_TASK not in role.permissions:
                role.permissions.append(RolePermission.CREATE_ROOT_TASK)
        for assignment in self.project_assignments.values():
            if (
                assignment.kind != AgentProjectAssignmentKind.PROJECT_LEAD
                or assignment.role_id is None
            ):
                continue
            role = self.roles.get(assignment.role_id)
            if role is not None and RolePermission.CREATE_ROOT_TASK not in role.permissions:
                role.permissions.append(RolePermission.CREATE_ROOT_TASK)

    def _validate_teams(self, errors: list[str]) -> None:
        for team in self.teams.values():
            manager = self.agents.get(team.manager_agent_id)
            if manager is None:
                errors.append(f"team {team.id} references missing manager {team.manager_agent_id}")
            elif manager.team_id != team.id:
                errors.append(
                    f"team {team.id} manager {manager.id} belongs to team {manager.team_id}"
                )
            for project_id in team.project_ids:
                project = self.projects.get(project_id)
                if project is None:
                    errors.append(f"team {team.id} references missing project {project_id}")
                elif project.team_id != team.id:
                    errors.append(
                        f"team {team.id} project {project_id} belongs to team {project.team_id}"
                    )

    def _validate_governance_groups(self, errors: list[str]) -> None:
        for group in self.governance_groups.values():
            if group.manager_agent_id not in self.agents:
                errors.append(
                    f"governance group {group.id} references missing manager "
                    f"{group.manager_agent_id}"
                )
            for team_id in group.member_team_ids:
                if team_id not in self.teams:
                    errors.append(
                        f"governance group {group.id} references missing member team {team_id}"
                    )

    def _validate_governance_agenda(self, errors: list[str]) -> None:
        for item in self.governance_agenda.values():
            group = self.governance_groups.get(item.governance_group_id)
            if group is None:
                errors.append(
                    f"governance agenda item {item.id} references missing group "
                    f"{item.governance_group_id}"
                )
            if item.team_id is not None:
                team = self.teams.get(item.team_id)
                if team is None:
                    errors.append(
                        f"governance agenda item {item.id} references missing team {item.team_id}"
                    )
                elif (
                    group is not None
                    and item.state != GovernanceAgendaItemState.HANDLED
                    and item.team_id not in group.member_team_ids
                ):
                    errors.append(
                        f"governance agenda item {item.id} team {item.team_id} is not in "
                        f"group {group.id}"
                    )
            if item.project_id is not None:
                project = self.projects.get(item.project_id)
                if project is None:
                    errors.append(
                        f"governance agenda item {item.id} references missing project "
                        f"{item.project_id}"
                    )
                elif item.team_id is not None and project.team_id != item.team_id:
                    errors.append(
                        f"governance agenda item {item.id} project {project.id} belongs "
                        f"to team {project.team_id}"
                    )
                elif (
                    group is not None
                    and item.state != GovernanceAgendaItemState.HANDLED
                    and project.team_id not in group.member_team_ids
                ):
                    errors.append(
                        f"governance agenda item {item.id} project {project.id} team "
                        f"{project.team_id} is not in group {group.id}"
                    )
            if item.agent_id is not None:
                agent = self.agents.get(item.agent_id)
                if agent is None:
                    errors.append(
                        f"governance agenda item {item.id} references missing agent {item.agent_id}"
                    )
                elif item.team_id is not None and agent.team_id != item.team_id:
                    errors.append(
                        f"governance agenda item {item.id} agent {agent.id} belongs "
                        f"to team {agent.team_id}"
                    )
                elif (
                    group is not None
                    and item.state != GovernanceAgendaItemState.HANDLED
                    and agent.team_id not in group.member_team_ids
                ):
                    errors.append(
                        f"governance agenda item {item.id} agent {agent.id} team "
                        f"{agent.team_id} is not in group {group.id}"
                    )

    def _validate_governance_runtimes(self, errors: list[str]) -> None:
        for group_id, runtime in self.governance_runtimes.items():
            if runtime.governance_group_id != group_id:
                errors.append(f"governance runtime {group_id} does not point back")
            if group_id not in self.governance_groups:
                errors.append(f"governance runtime {group_id} references missing group")

    def _validate_projects(self, errors: list[str]) -> None:
        for project in self.projects.values():
            if project.team_id not in self.teams:
                errors.append(f"project {project.id} references missing team {project.team_id}")
            lead_id = project_lead_agent_id(self, project.id) or project.lead_agent_id
            lead = self.agents.get(lead_id) if lead_id is not None else None
            if lead is None:
                errors.append(
                    f"project {project.id} references missing lead {lead_id or project.lead_agent_id}"
                )
            elif lead.team_id != project.team_id:
                errors.append(f"project {project.id} lead {lead.id} belongs to team {lead.team_id}")

    def _validate_roles(self, errors: list[str]) -> None:
        for role in self.roles.values():
            if role.team_id not in self.teams:
                errors.append(f"role {role.id} references missing team {role.team_id}")
            if role.project_id is not None:
                project = self.projects.get(role.project_id)
                if project is None:
                    errors.append(f"role {role.id} references missing project {role.project_id}")
                elif project.team_id != role.team_id:
                    errors.append(
                        f"role {role.id} project {project.id} belongs to team {project.team_id}"
                    )

    def _validate_agents(self, errors: list[str]) -> None:
        for agent in self.agents.values():
            if agent.team_id is not None and agent.team_id not in self.teams:
                errors.append(f"agent {agent.id} references missing team {agent.team_id}")
            role = self.roles.get(agent.role_id) if agent.role_id is not None else None
            if agent.role_id is not None and role is None:
                errors.append(f"agent {agent.id} references missing role {agent.role_id}")
            elif role is not None and role.team_id != agent.team_id:
                errors.append(f"agent {agent.id} role {role.id} belongs to team {role.team_id}")
            elif (
                role is not None
                and role.project_id is not None
                and not _agent_has_project_assignment(self, agent.id, role.project_id)
            ):
                errors.append(
                    f"agent {agent.id} role {role.id} belongs to project {role.project_id}"
                )
            if agent.reports_to_agent_id is not None:
                superior = self.agents.get(agent.reports_to_agent_id)
                if superior is None:
                    errors.append(
                        f"agent {agent.id} references missing superior {agent.reports_to_agent_id}"
                    )
                elif superior.team_id != agent.team_id:
                    errors.append(f"agent {agent.id} superior {superior.id} is in another team")

    def _validate_project_assignments(self, errors: list[str]) -> None:
        lead_assignments_by_project: dict[str, list[str]] = {}
        seen_agent_project_kind: set[tuple[str, str, AgentProjectAssignmentKind]] = set()
        for assignment in self.project_assignments.values():
            project = self.projects.get(assignment.project_id)
            if project is None:
                errors.append(
                    f"project assignment {assignment.id} references missing project {assignment.project_id}"
                )
            elif project.team_id != assignment.team_id:
                errors.append(
                    f"project assignment {assignment.id} project {project.id} belongs to team {project.team_id}"
                )
            agent = self.agents.get(assignment.agent_id)
            if agent is None:
                errors.append(
                    f"project assignment {assignment.id} references missing agent {assignment.agent_id}"
                )
            elif agent.team_id != assignment.team_id:
                errors.append(
                    f"project assignment {assignment.id} agent {agent.id} belongs to team {agent.team_id}"
                )
            role = self.roles.get(assignment.role_id) if assignment.role_id is not None else None
            if assignment.role_id is not None and role is None:
                errors.append(
                    f"project assignment {assignment.id} references missing role {assignment.role_id}"
                )
            elif role is not None:
                if role.team_id != assignment.team_id:
                    errors.append(
                        f"project assignment {assignment.id} role {role.id} belongs to team {role.team_id}"
                    )
                if role.project_id is not None and role.project_id != assignment.project_id:
                    errors.append(
                        f"project assignment {assignment.id} role {role.id} belongs to project {role.project_id}"
                    )
            key = (assignment.agent_id, assignment.project_id, assignment.kind)
            if key in seen_agent_project_kind:
                errors.append(
                    f"duplicate {assignment.kind.value} assignment for agent {assignment.agent_id} project {assignment.project_id}"
                )
            seen_agent_project_kind.add(key)
            if assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD:
                lead_assignments_by_project.setdefault(assignment.project_id, []).append(
                    assignment.agent_id
                )
        for project in self.projects.values():
            lead_agent_ids = lead_assignments_by_project.get(project.id, [])
            if len(lead_agent_ids) != 1:
                errors.append(f"project {project.id} must have exactly one project lead assignment")
            elif project.lead_agent_id != lead_agent_ids[0]:
                errors.append(
                    f"project {project.id} lead_agent_id {project.lead_agent_id} does not match lead assignment {lead_agent_ids[0]}"
                )
            for subordinate_id in agent.subordinate_agent_ids:
                subordinate = self.agents.get(subordinate_id)
                if subordinate is None:
                    errors.append(
                        f"agent {agent.id} references missing subordinate {subordinate_id}"
                    )
                elif subordinate.reports_to_agent_id != agent.id:
                    errors.append(
                        f"agent {agent.id} subordinate {subordinate_id} does not report back"
                    )
        for agent_id in self.agents:
            self._validate_no_reporting_cycle(agent_id, errors)

    def _validate_agent_runtimes(self, errors: list[str]) -> None:
        for agent_id, runtime in self.agent_runtimes.items():
            if runtime.agent_id != agent_id:
                errors.append(f"agent runtime {agent_id} does not point back")
            if agent_id not in self.agents:
                errors.append(f"agent runtime {agent_id} references missing agent")

    def _validate_no_reporting_cycle(self, agent_id: str, errors: list[str]) -> None:
        seen: set[str] = set()
        current_id: str | None = agent_id
        while current_id is not None:
            if current_id in seen:
                errors.append(f"agent reporting cycle includes {current_id}")
                return
            seen.add(current_id)
            current = self.agents.get(current_id)
            current_id = current.reports_to_agent_id if current is not None else None

    def _validate_external_source_evidence_snapshots(self, errors: list[str]) -> None:
        for snapshot in self.external_source_evidence_snapshots.values():
            if snapshot.team_id not in self.teams:
                errors.append(
                    f"external source evidence snapshot {snapshot.id} references "
                    f"missing team {snapshot.team_id}"
                )
            if snapshot.project_id is not None:
                project = self.projects.get(snapshot.project_id)
                if project is None:
                    errors.append(
                        f"external source evidence snapshot {snapshot.id} references "
                        f"missing project {snapshot.project_id}"
                    )
                elif project.team_id != snapshot.team_id:
                    errors.append(
                        f"external source evidence snapshot {snapshot.id} project "
                        f"{project.id} belongs to team {project.team_id}"
                    )

    def _validate_cases(self, errors: list[str]) -> None:
        for case in self.cases.values():
            team = self.teams.get(case.team_id)
            if team is None:
                errors.append(f"case {case.id} references missing team {case.team_id}")
            if case.project_id is not None:
                project = self.projects.get(case.project_id)
                if project is None:
                    errors.append(f"case {case.id} references missing project {case.project_id}")
                elif project.team_id != case.team_id:
                    errors.append(
                        f"case {case.id} project {project.id} belongs to team {project.team_id}"
                    )
            if case.owner_agent_id is not None:
                owner = self.agents.get(case.owner_agent_id)
                if owner is None:
                    errors.append(f"case {case.id} references missing owner {case.owner_agent_id}")
                elif owner.team_id != case.team_id and not (
                    case.status == TeamCaseStatus.CLOSED
                    and owner.team_id is None
                    and owner.retired_team_id == case.team_id
                ):
                    errors.append(
                        f"case {case.id} owner {owner.id} belongs to team {owner.team_id}"
                    )
            for task_id in case.task_ids:
                task = self.tasks.get(task_id)
                if task is None:
                    errors.append(f"case {case.id} references missing task {task_id}")
                    continue
                if task.case_id != case.id:
                    errors.append(f"case {case.id} task {task_id} does not point back")
                project = self.projects.get(task.project_id)
                if project is not None and project.team_id != case.team_id:
                    errors.append(
                        f"case {case.id} task {task.id} belongs to team {project.team_id}"
                    )
                if case.project_id is not None and task.project_id != case.project_id:
                    errors.append(
                        f"case {case.id} task {task.id} belongs to project {task.project_id}"
                    )
            for snapshot_id in case.source_evidence_snapshot_ids:
                snapshot = self.external_source_evidence_snapshots.get(snapshot_id)
                if snapshot is None:
                    errors.append(
                        f"case {case.id} references missing external source evidence "
                        f"snapshot {snapshot_id}"
                    )
                    continue
                if snapshot.team_id != case.team_id:
                    errors.append(
                        f"case {case.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to team {snapshot.team_id}"
                    )
                if snapshot.project_id is not None and snapshot.project_id != case.project_id:
                    errors.append(
                        f"case {case.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to project {snapshot.project_id}"
                    )

    def _validate_tasks(self, errors: list[str]) -> None:
        terminal_task_states = {
            TeamTaskState.FINISHED,
            TeamTaskState.ACCEPTED,
            TeamTaskState.REJECTED,
            TeamTaskState.CANCELLED,
        }
        for task in self.tasks.values():
            task_is_terminal = task.state in terminal_task_states
            owner = self.agents.get(task.owner_agent_id)
            if owner is None:
                errors.append(f"task {task.id} references missing owner {task.owner_agent_id}")
            project = self.projects.get(task.project_id)
            if project is None:
                errors.append(f"task {task.id} references missing project {task.project_id}")
            elif not task_is_terminal and owner is not None and owner.team_id != project.team_id:
                errors.append(f"task {task.id} owner {owner.id} is outside project team")
            if project is not None and task.outcome_contract is not None:
                contract = task.outcome_contract
                referenced_kinds = (
                    contract.required_outcome_kinds + contract.forbidden_outcome_kinds
                )
                for kind in referenced_kinds:
                    if (
                        resolve_outcome_kind_definition(
                            self,
                            team_id=project.team_id,
                            project_id=project.id,
                            kind=kind,
                            include_archived=True,
                        )
                        is None
                    ):
                        errors.append(f"task {task.id} references unknown outcome kind {kind}")
                snapshot_kinds = {
                    definition.kind for definition in contract.outcome_kind_definitions
                }
                missing_snapshots = sorted(set(referenced_kinds) - snapshot_kinds)
                if missing_snapshots:
                    errors.append(
                        f"task {task.id} is missing outcome kind definition snapshots: "
                        + ", ".join(missing_snapshots)
                    )
            if task.case_id is not None:
                case = self.cases.get(task.case_id)
                if case is None:
                    errors.append(f"task {task.id} references missing case {task.case_id}")
                elif project is not None:
                    if case.team_id != project.team_id:
                        errors.append(
                            f"task {task.id} case {case.id} belongs to team {case.team_id}"
                        )
                    if case.project_id is not None and case.project_id != task.project_id:
                        errors.append(
                            f"task {task.id} case {case.id} belongs to project {case.project_id}"
                        )
                    if task.id not in case.task_ids:
                        errors.append(f"task {task.id} case {case.id} does not link back")
            if task.created_by_agent_id is not None and task.created_by_agent_id not in self.agents:
                errors.append(
                    f"task {task.id} references missing creator {task.created_by_agent_id}"
                )
            for deliverable_id in task.deliverable_ids:
                deliverable = self.deliverables.get(deliverable_id)
                if deliverable is None:
                    errors.append(f"task {task.id} references missing deliverable {deliverable_id}")
                elif deliverable.task_id != task.id:
                    errors.append(
                        f"task {task.id} deliverable {deliverable_id} does not point back"
                    )
            for snapshot_id in task.source_evidence_snapshot_ids:
                snapshot = self.external_source_evidence_snapshots.get(snapshot_id)
                if snapshot is None:
                    errors.append(
                        f"task {task.id} references missing external source evidence "
                        f"snapshot {snapshot_id}"
                    )
                    continue
                if project is not None and snapshot.team_id != project.team_id:
                    errors.append(
                        f"task {task.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to team {snapshot.team_id}"
                    )
                if snapshot.project_id is not None and snapshot.project_id != task.project_id:
                    errors.append(
                        f"task {task.id} external source evidence snapshot {snapshot.id} "
                        f"belongs to project {snapshot.project_id}"
                    )

    def _validate_task_graph(self, errors: list[str]) -> None:
        # Historical graph contradictions are deliberately readable. Command
        # boundaries prevent new invalid edges, while TaskAssessment reports
        # missing/cross-project/cyclic parent and dependency relationships and
        # blocks new completion or publication until an audited repair occurs.
        return

    def _validate_no_parent_cycle(self, task_id: str, errors: list[str]) -> None:
        seen: set[str] = set()
        current_id: str | None = task_id
        while current_id is not None:
            if current_id in seen:
                errors.append(f"task parent cycle includes {current_id}")
                return
            seen.add(current_id)
            current = self.tasks.get(current_id)
            current_id = current.parent_task_id if current is not None else None

    def _validate_no_dependency_cycle(self, errors: list[str]) -> None:
        visited: set[str] = set()
        visiting: set[str] = set()
        reported = False
        for start in self.tasks:
            if start in visited:
                continue
            stack: list[str] = [start]
            while stack:
                node = stack[-1]
                visiting.add(node)
                task = self.tasks.get(node)
                dependencies = task.dependency_task_ids if task is not None else []
                advanced = False
                for dependency_id in dependencies:
                    if dependency_id not in self.tasks:
                        continue
                    if dependency_id in visited:
                        continue
                    if dependency_id in visiting:
                        if not reported:
                            errors.append(f"task dependency cycle includes {dependency_id}")
                            reported = True
                        continue
                    stack.append(dependency_id)
                    advanced = True
                    break
                if not advanced:
                    visiting.discard(node)
                    visited.add(node)
                    stack.pop()

    def child_task_ids(self, task_id: str) -> list[str]:
        return sorted(task.id for task in self.tasks.values() if task.parent_task_id == task_id)

    def dependency_status(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None or not task.dependency_task_ids:
            return "satisfied"
        dependency_states = [
            self.tasks[dep_id].state for dep_id in task.dependency_task_ids if dep_id in self.tasks
        ]
        if any(
            state in {TeamTaskState.BLOCKED, TeamTaskState.REJECTED} for state in dependency_states
        ):
            return "blocked"
        if any(
            state
            not in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.FINISHED,
                TeamTaskState.CANCELLED,
            }
            for state in dependency_states
        ):
            return "waiting"
        return "satisfied"

    def blocked_by_task_ids(self, task_id: str) -> list[str]:
        task = self.tasks.get(task_id)
        if task is None:
            return []
        blocked_by: list[str] = []
        for dependency_id in task.dependency_task_ids:
            dependency = self.tasks.get(dependency_id)
            if dependency is None:
                continue
            if dependency.state not in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.FINISHED,
                TeamTaskState.CANCELLED,
            }:
                blocked_by.append(dependency_id)
        return blocked_by

    def subtask_counts(self, task_id: str) -> dict[str, int]:
        counts = {"total": 0, "accepted": 0, "finished": 0, "blocked": 0, "open": 0}
        for child_id in self.child_task_ids(task_id):
            child = self.tasks[child_id]
            counts["total"] += 1
            if child.state == TeamTaskState.ACCEPTED:
                counts["accepted"] += 1
            elif child.state in {TeamTaskState.FINISHED, TeamTaskState.CANCELLED}:
                counts["finished"] += 1
            elif child.state in {TeamTaskState.BLOCKED, TeamTaskState.REJECTED}:
                counts["blocked"] += 1
            elif child.state in {
                TeamTaskState.PENDING,
                TeamTaskState.IN_PROGRESS,
                TeamTaskState.WAITING_FOR_INPUT,
                TeamTaskState.WAITING_FOR_REVIEW,
            }:
                counts["open"] += 1
        return counts

    def _validate_deliverables(self, errors: list[str]) -> None:
        for deliverable in self.deliverables.values():
            task = self.tasks.get(deliverable.task_id)
            if task is None:
                errors.append(
                    f"deliverable {deliverable.id} references missing task {deliverable.task_id}"
                )
            else:
                project = self.projects.get(task.project_id)
                if project is not None:
                    for outcome in deliverable.outcomes:
                        definition = resolve_outcome_kind_definition(
                            self,
                            team_id=project.team_id,
                            project_id=project.id,
                            kind=outcome.kind,
                            include_archived=True,
                        )
                        if definition is None:
                            errors.append(
                                f"deliverable {deliverable.id} references unknown "
                                f"outcome kind {outcome.kind}"
                            )
                        if outcome.definition_snapshot is None:
                            errors.append(
                                f"deliverable {deliverable.id} outcome {outcome.kind} "
                                "is missing its definition snapshot"
                            )
            if deliverable.producer_agent_id not in self.agents:
                errors.append(
                    f"deliverable {deliverable.id} references missing producer "
                    f"{deliverable.producer_agent_id}"
                )
            disposition = deliverable.initial_release_disposition
            if (
                disposition is not None
                and disposition.state == InitialReleaseDispositionState.RELEASE_REQUESTED
            ):
                release = self.release_packages.get(disposition.release_package_id or "")
                if release is None:
                    errors.append(
                        f"deliverable {deliverable.id} initial release disposition "
                        f"references missing release {disposition.release_package_id}"
                    )
                elif deliverable.id not in release.deliverable_ids:
                    errors.append(
                        f"deliverable {deliverable.id} initial release disposition "
                        f"references release {release.id} that does not include it"
                    )
            if (
                deliverable.rework_requested_by_agent_id is not None
                and deliverable.rework_requested_by_agent_id not in self.agents
            ):
                errors.append(
                    f"deliverable {deliverable.id} references missing rework requester "
                    f"{deliverable.rework_requested_by_agent_id}"
                )
            for review_request_id in deliverable.review_request_ids:
                review = self.review_requests.get(review_request_id)
                if review is None:
                    errors.append(
                        f"deliverable {deliverable.id} references missing review {review_request_id}"
                    )
                elif review.deliverable_id != deliverable.id:
                    errors.append(
                        f"deliverable {deliverable.id} review {review_request_id} does not point back"
                    )
            finding_ids: set[str] = set()
            for finding in deliverable.carried_findings:
                if finding.id in finding_ids:
                    errors.append(
                        f"deliverable {deliverable.id} has duplicate carried finding {finding.id}"
                    )
                finding_ids.add(finding.id)
                if finding.source_review_request_id not in self.review_requests:
                    errors.append(
                        f"deliverable {deliverable.id} finding {finding.id} references "
                        f"missing source review {finding.source_review_request_id}"
                    )
                if finding.source_deliverable_id not in self.deliverables:
                    errors.append(
                        f"deliverable {deliverable.id} finding {finding.id} references "
                        f"missing source deliverable {finding.source_deliverable_id}"
                    )
                if (
                    finding.resolved_by_review_request_id is not None
                    and finding.resolved_by_review_request_id not in self.review_requests
                ):
                    errors.append(
                        f"deliverable {deliverable.id} finding {finding.id} references "
                        f"missing resolving review {finding.resolved_by_review_request_id}"
                    )
            if deliverable.supersedes_deliverable_id is not None:
                previous = self.deliverables.get(deliverable.supersedes_deliverable_id)
                if previous is None:
                    errors.append(
                        f"deliverable {deliverable.id} supersedes missing deliverable "
                        f"{deliverable.supersedes_deliverable_id}"
                    )
                elif previous.id == deliverable.id:
                    errors.append(f"deliverable {deliverable.id} cannot supersede itself")
                else:
                    previous_task = self.tasks.get(previous.task_id)
                    replacement_task = self.tasks.get(deliverable.task_id)
                    if (
                        previous_task is not None
                        and replacement_task is not None
                        and previous_task.project_id != replacement_task.project_id
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} supersedes deliverable "
                            f"{previous.id} from another project"
                        )
                    if previous.superseded_by_deliverable_id != deliverable.id:
                        errors.append(
                            f"deliverable {deliverable.id} supersedes deliverable "
                            f"{previous.id} without a reciprocal superseded_by link"
                        )
                    if previous.state not in {
                        TeamRecordState.REJECTED,
                        TeamRecordState.BLOCKED,
                    } and not (
                        previous.state == TeamRecordState.ACCEPTED
                        and previous.rework_requested_at is not None
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} supersedes a deliverable "
                            f"{previous.id} that was neither rejected, blocked, nor "
                            "explicitly reopened for rework"
                        )
            if deliverable.superseded_by_deliverable_id is not None:
                replacement = self.deliverables.get(deliverable.superseded_by_deliverable_id)
                if replacement is None:
                    errors.append(
                        f"deliverable {deliverable.id} is superseded by missing deliverable "
                        f"{deliverable.superseded_by_deliverable_id}"
                    )
                elif replacement.id == deliverable.id:
                    errors.append(f"deliverable {deliverable.id} cannot supersede itself")
                else:
                    predecessor_task = self.tasks.get(deliverable.task_id)
                    replacement_task = self.tasks.get(replacement.task_id)
                    if (
                        predecessor_task is not None
                        and replacement_task is not None
                        and predecessor_task.project_id != replacement_task.project_id
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} is superseded by deliverable "
                            f"{replacement.id} from another project"
                        )
                    if replacement.supersedes_deliverable_id != deliverable.id:
                        errors.append(
                            f"deliverable {deliverable.id} is superseded by deliverable "
                            f"{replacement.id} without a reciprocal supersedes link"
                        )
                    if deliverable.state not in {
                        TeamRecordState.REJECTED,
                        TeamRecordState.BLOCKED,
                    } and not (
                        deliverable.state == TeamRecordState.ACCEPTED
                        and deliverable.rework_requested_at is not None
                    ):
                        errors.append(
                            f"deliverable {deliverable.id} cannot be superseded without "
                            "rejection, a block, or an explicit rework request"
                        )

    def _validate_outcome_kind_definitions(self, errors: list[str]) -> None:
        seen_scopes: set[tuple[str, str | None, str]] = set()
        for definition in self.outcome_kind_definitions.values():
            if definition.team_id not in self.teams:
                errors.append(
                    f"outcome kind {definition.id} references missing team {definition.team_id}"
                )
            if definition.project_id is not None:
                project = self.projects.get(definition.project_id)
                if project is None:
                    errors.append(
                        f"outcome kind {definition.id} references missing project "
                        f"{definition.project_id}"
                    )
                elif project.team_id != definition.team_id:
                    errors.append(
                        f"outcome kind {definition.id} project {project.id} belongs "
                        f"to team {project.team_id}"
                    )
            scope = (
                definition.team_id,
                definition.project_id,
                definition.kind,
            )
            if scope in seen_scopes:
                errors.append(
                    "duplicate outcome kind definition for "
                    f"{definition.kind} in "
                    f"{definition.project_id or definition.team_id}"
                )
            seen_scopes.add(scope)

    def _validate_review_profiles(self, errors: list[str]) -> None:
        for profile in self.review_profiles.values():
            if profile.team_id not in self.teams:
                errors.append(
                    f"review profile {profile.id} references missing team {profile.team_id}"
                )
            if profile.project_id is not None:
                project = self.projects.get(profile.project_id)
                if project is None:
                    errors.append(
                        f"review profile {profile.id} references missing project "
                        f"{profile.project_id}"
                    )
                elif project.team_id != profile.team_id:
                    errors.append(
                        f"review profile {profile.id} project {project.id} belongs to team "
                        f"{project.team_id}"
                    )
            for role_id in profile.applicable_role_ids:
                role = self.roles.get(role_id)
                if role is None:
                    errors.append(f"review profile {profile.id} references missing role {role_id}")
                elif role.team_id != profile.team_id:
                    errors.append(
                        f"review profile {profile.id} role {role.id} belongs to team {role.team_id}"
                    )
                elif role.project_id is not None and role.project_id != profile.project_id:
                    errors.append(
                        f"review profile {profile.id} role {role.id} belongs to project "
                        f"{role.project_id}"
                    )
            for kind in profile.required_outcome_kinds:
                if (
                    resolve_outcome_kind_definition(
                        self,
                        team_id=profile.team_id,
                        project_id=profile.project_id,
                        kind=kind,
                        include_archived=profile.archived,
                    )
                    is None
                ):
                    errors.append(
                        f"review profile {profile.id} references unknown outcome kind {kind}"
                    )

    def _validate_reviews(self, errors: list[str]) -> None:
        current_cohort_reviewers: set[tuple[str, str, str]] = set()
        current_release_cohort_members: set[tuple[str, str, str, str | None]] = set()
        for review in self.review_requests.values():
            if review.deliverable_id is not None and review.deliverable_id not in self.deliverables:
                errors.append(
                    f"review request {review.id} references missing deliverable "
                    f"{review.deliverable_id}"
                )
            deliverable = (
                self.deliverables.get(review.deliverable_id)
                if review.deliverable_id is not None
                else None
            )
            release = (
                self.release_packages.get(review.release_package_id)
                if review.release_package_id is not None
                else None
            )
            if (
                review.review_cohort_id is not None
                and deliverable is not None
                and review.review_cohort_id != deliverable.review_cohort_id
            ):
                errors.append(
                    f"review request {review.id} cohort "
                    f"{review.review_cohort_id} does not match deliverable "
                    f"{deliverable.id} cohort {deliverable.review_cohort_id}"
                )
            if (
                deliverable is not None
                and deliverable.review_cohort_id is not None
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
                and review.review_cohort_id != deliverable.review_cohort_id
            ):
                errors.append(
                    f"current review request {review.id} must carry deliverable "
                    f"{deliverable.id} cohort {deliverable.review_cohort_id}"
                )
            if (
                review.deliverable_id is not None
                and review.review_cohort_id is not None
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
            ):
                cohort_reviewer_key = (
                    review.deliverable_id,
                    review.review_cohort_id,
                    review.reviewer_agent_id,
                )
                if cohort_reviewer_key in current_cohort_reviewers:
                    errors.append(
                        f"deliverable {review.deliverable_id} cohort "
                        f"{review.review_cohort_id} has duplicate current reviewer "
                        f"{review.reviewer_agent_id}"
                    )
                current_cohort_reviewers.add(cohort_reviewer_key)
            if (
                review.review_cohort_id is not None
                and release is not None
                and review.review_cohort_id != release.review_cohort_id
            ):
                errors.append(
                    f"release review {review.id} cohort {review.review_cohort_id} does not "
                    f"match release {release.id} cohort {release.review_cohort_id}"
                )
            if (
                release is not None
                and release.review_cohort_id is not None
                and review.review_cohort_id != release.review_cohort_id
            ):
                errors.append(
                    f"release review {review.id} must carry release {release.id} cohort "
                    f"{release.review_cohort_id}"
                )
            if (
                review.release_package_id is not None
                and review.review_cohort_id is not None
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
            ):
                release_member_key = (
                    review.release_package_id,
                    review.review_cohort_id,
                    review.reviewer_agent_id,
                    review.review_profile_id,
                )
                if release_member_key in current_release_cohort_members:
                    errors.append(
                        f"release {review.release_package_id} cohort "
                        f"{review.review_cohort_id} has duplicate current reviewer/profile "
                        f"slot {review.reviewer_agent_id}/{review.review_profile_id}"
                    )
                current_release_cohort_members.add(release_member_key)
            if (
                review.release_package_id is not None
                and review.release_package_id not in self.release_packages
            ):
                errors.append(
                    f"review request {review.id} references missing release package "
                    f"{review.release_package_id}"
                )
            if review.reviewer_agent_id not in self.agents:
                errors.append(
                    f"review request {review.id} references missing reviewer {review.reviewer_agent_id}"
                )
            if (
                review.requested_by_agent_id is not None
                and review.requested_by_agent_id not in self.agents
            ):
                errors.append(
                    f"review request {review.id} references missing requester "
                    f"{review.requested_by_agent_id}"
                )
            profile = None
            if review.review_profile_id is not None:
                profile = self.review_profiles.get(review.review_profile_id)
                if profile is None:
                    errors.append(
                        f"review request {review.id} references missing review profile "
                        f"{review.review_profile_id}"
                    )
            if review.criteria_snapshot is not None and review.review_profile_id is None:
                errors.append(
                    f"review request {review.id} has a criteria snapshot but no review profile"
                )
            if review.supersedes_review_request_id is not None:
                previous = self.review_requests.get(review.supersedes_review_request_id)
                if previous is None or (
                    previous.deliverable_id != review.deliverable_id
                    or previous.release_package_id != review.release_package_id
                ):
                    errors.append(f"review request {review.id} supersedes an invalid review")
                elif previous.superseded_by_review_request_id != review.id:
                    errors.append(
                        f"review request {review.id} has non-reciprocal supersession lineage"
                    )
            if review.superseded_by_review_request_id is not None:
                successor = self.review_requests.get(review.superseded_by_review_request_id)
                if successor is None or (
                    successor.deliverable_id != review.deliverable_id
                    or successor.release_package_id != review.release_package_id
                ):
                    errors.append(f"review request {review.id} has an invalid successor")
                elif successor.supersedes_review_request_id != review.id:
                    errors.append(
                        f"review request {review.id} has non-reciprocal successor lineage"
                    )
            if review.criteria and review.review_profile_id is None:
                errors.append(
                    f"review request {review.id} has criterion results but no review profile"
                )
            criterion_definitions = (
                review.criteria_snapshot
                if review.criteria_snapshot is not None
                else profile.criteria
                if profile is not None
                else []
            )
            valid_criterion_ids = {criterion.id for criterion in criterion_definitions}
            seen: set[str] = set()
            for result in review.criteria:
                if profile is not None and result.criterion_id not in valid_criterion_ids:
                    errors.append(
                        f"review request {review.id} criterion result references unknown "
                        f"criterion {result.criterion_id}"
                    )
                if result.criterion_id in seen:
                    errors.append(
                        f"review request {review.id} has duplicate criterion result "
                        f"{result.criterion_id}"
                    )
                seen.add(result.criterion_id)

    def _validate_inbox(self, errors: list[str]) -> None:
        for item in self.inbox.values():
            if item.recipient_agent_id not in self.agents:
                errors.append(
                    f"inbox item {item.id} references missing recipient {item.recipient_agent_id}"
                )
            if item.sender_agent_id is not None and item.sender_agent_id not in self.agents:
                errors.append(
                    f"inbox item {item.id} references missing sender {item.sender_agent_id}"
                )
            if item.task_id is not None and item.task_id not in self.tasks:
                errors.append(f"inbox item {item.id} references missing task {item.task_id}")

    def _validate_blockers(self, errors: list[str]) -> None:
        open_sources: dict[tuple[str, TeamBlockerSourceKind, str], str] = {}
        for blocker in self.blockers.values():
            team = self.teams.get(blocker.team_id)
            if team is None:
                errors.append(f"blocker {blocker.id} references missing team {blocker.team_id}")
            project = self.projects.get(blocker.project_id) if blocker.project_id else None
            if blocker.project_id is not None and project is None:
                errors.append(
                    f"blocker {blocker.id} references missing project {blocker.project_id}"
                )
            elif project is not None and project.team_id != blocker.team_id:
                errors.append(
                    f"blocker {blocker.id} project {project.id} belongs to team {project.team_id}"
                )
            task = self.tasks.get(blocker.task_id) if blocker.task_id else None
            if blocker.task_id is not None and task is None:
                errors.append(f"blocker {blocker.id} references missing task {blocker.task_id}")
            elif task is not None and blocker.project_id is not None:
                if task.project_id != blocker.project_id:
                    errors.append(
                        f"blocker {blocker.id} task {task.id} belongs to project {task.project_id}"
                    )
            for field_name, agent_id in (
                ("reporter", blocker.reporter_agent_id),
                ("owner", blocker.owner_agent_id),
                ("delegate", blocker.delegate_agent_id),
            ):
                if agent_id is None:
                    continue
                agent = self.agents.get(agent_id)
                if agent is None:
                    errors.append(
                        f"blocker {blocker.id} references missing {field_name} {agent_id}"
                    )
                elif (
                    blocker.state == TeamBlockerState.OPEN
                    and field_name in {"owner", "delegate"}
                    and agent.team_id != blocker.team_id
                ):
                    errors.append(
                        f"blocker {blocker.id} {field_name} {agent.id} belongs to team "
                        f"{agent.team_id}"
                    )
            source_exists = {
                TeamBlockerSourceKind.REVIEW_REQUEST: blocker.source_id in self.review_requests,
                TeamBlockerSourceKind.TASK: blocker.source_id in self.tasks,
                TeamBlockerSourceKind.DELIVERABLE: blocker.source_id in self.deliverables,
                TeamBlockerSourceKind.RELEASE_PACKAGE: blocker.source_id in self.release_packages,
                TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST: blocker.source_id
                in self.human_review_requests,
                TeamBlockerSourceKind.AGENT_EXECUTION_STALL: blocker.source_id in self.agents,
                TeamBlockerSourceKind.MCP_CAPABILITY: (
                    (
                        blocker.mcp_capability_identity is not None
                        and blocker.mcp_capability_identity.agent_id in self.agents
                        and all(
                            review_id in self.review_requests
                            for review_id in blocker.mcp_capability_identity.review_request_ids
                        )
                    )
                    or (
                        # Compatibility for blockers persisted before capability
                        # identity received dedicated structured storage.
                        blocker.mcp_capability_identity is None
                        and ":" in blocker.source_id
                        and blocker.source_id.split(":", 1)[0] in self.agents
                        and any(
                            review_id for review_id in blocker.source_id.split(":", 1)[1].split("|")
                        )
                        and all(
                            review_id in self.review_requests
                            for review_id in blocker.source_id.split(":", 1)[1].split("|")
                            if review_id
                        )
                    )
                ),
                TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR: (
                    blocker.source_id.startswith("candidate-correction-successor:")
                    and blocker.source_id.removeprefix("candidate-correction-successor:")
                    in self.release_packages
                ),
                TeamBlockerSourceKind.EXPLICIT: True,
            }[blocker.source_kind]
            if not source_exists:
                errors.append(
                    f"blocker {blocker.id} references missing {blocker.source_kind.value} "
                    f"{blocker.source_id}"
                )
            if blocker.state == TeamBlockerState.OPEN:
                key = (blocker.team_id, blocker.source_kind, blocker.source_id)
                if key in open_sources:
                    errors.append(
                        f"blockers {open_sources[key]} and {blocker.id} are both open for "
                        f"{blocker.source_kind.value} {blocker.source_id}"
                    )
                open_sources[key] = blocker.id
        for event in self.blocker_events.values():
            if event.blocker_id not in self.blockers:
                errors.append(
                    f"blocker event {event.id} references missing blocker {event.blocker_id}"
                )
            for field_name, agent_id in (
                ("actor", event.actor_agent_id),
                ("from agent", event.from_agent_id),
                ("to agent", event.to_agent_id),
            ):
                if agent_id is not None and agent_id not in self.agents:
                    errors.append(
                        f"blocker event {event.id} references missing {field_name} {agent_id}"
                    )

    def _validate_action_records(self, errors: list[str]) -> None:
        for record in self.action_records.values():
            if record.agent_id not in self.agents:
                errors.append(
                    f"action record {record.id} references missing agent {record.agent_id}"
                )

    def _validate_memories(self, errors: list[str]) -> None:
        for memory in self.memories.values():
            if memory.team_id not in self.teams:
                errors.append(f"memory {memory.id} references missing team {memory.team_id}")
            if memory.project_id is not None:
                project = self.projects.get(memory.project_id)
                if project is None:
                    errors.append(
                        f"memory {memory.id} references missing project {memory.project_id}"
                    )
                elif project.team_id != memory.team_id:
                    errors.append(
                        f"memory {memory.id} project {project.id} belongs to team {project.team_id}"
                    )
            if memory.agent_id is not None:
                agent = self.agents.get(memory.agent_id)
                if agent is None:
                    errors.append(f"memory {memory.id} references missing agent {memory.agent_id}")
                elif (agent.team_id or agent.retired_team_id) != memory.team_id:
                    errors.append(
                        f"memory {memory.id} agent {agent.id} belongs to team {agent.team_id}"
                    )
            if memory.created_by_agent_id is not None:
                creator = self.agents.get(memory.created_by_agent_id)
                if creator is None:
                    errors.append(
                        f"memory {memory.id} references missing creator "
                        f"{memory.created_by_agent_id}"
                    )
                elif (creator.team_id or creator.retired_team_id) != memory.team_id:
                    errors.append(
                        f"memory {memory.id} creator {creator.id} belongs to team {creator.team_id}"
                    )
            if (
                memory.supersedes_memory_id is not None
                and memory.supersedes_memory_id not in self.memories
            ):
                errors.append(
                    f"memory {memory.id} supersedes missing memory {memory.supersedes_memory_id}"
                )
            if (
                memory.superseded_by_memory_id is not None
                and memory.superseded_by_memory_id not in self.memories
            ):
                errors.append(
                    f"memory {memory.id} is superseded by missing memory "
                    f"{memory.superseded_by_memory_id}"
                )

    def _validate_conversations(self, errors: list[str]) -> None:
        conversation_pairs: dict[tuple[str, str], str] = {}
        for conversation in self.conversations.values():
            if conversation.agent_id not in self.agents:
                errors.append(
                    f"conversation {conversation.id} references missing agent "
                    f"{conversation.agent_id}"
                )
            pair = (conversation.agent_id, conversation.operator_user_id)
            existing = conversation_pairs.get(pair)
            if existing is not None:
                errors.append(
                    f"agent {conversation.agent_id} and operator "
                    f"{conversation.operator_user_id} have multiple conversations: "
                    f"{existing}, {conversation.id}"
                )
            conversation_pairs[pair] = conversation.id
        for message in self.conversation_messages.values():
            if message.conversation_id not in self.conversations:
                errors.append(
                    f"conversation message {message.id} references missing conversation "
                    f"{message.conversation_id}"
                )
            if (
                message.source_message_id is not None
                and message.source_message_id not in self.conversation_messages
            ):
                errors.append(
                    f"conversation message {message.id} references missing source message "
                    f"{message.source_message_id}"
                )
            for source_message_id in message.source_message_ids:
                if source_message_id not in self.conversation_messages:
                    errors.append(
                        f"conversation message {message.id} references missing source message "
                        f"{source_message_id}"
                    )
            for attachment_id in message.attachment_ids:
                attachment = self.chat_attachments.get(attachment_id)
                if attachment is None:
                    errors.append(
                        f"conversation message {message.id} references missing attachment "
                        f"{attachment_id}"
                    )
                elif attachment.message_id != message.id:
                    errors.append(
                        f"attachment {attachment.id} belongs to message {attachment.message_id}, "
                        f"not {message.id}"
                    )
        for attachment in self.chat_attachments.values():
            message = self.conversation_messages.get(attachment.message_id)
            if message is None:
                errors.append(
                    f"chat attachment {attachment.id} references missing message "
                    f"{attachment.message_id}"
                )
            if attachment.conversation_id not in self.conversations:
                errors.append(
                    f"chat attachment {attachment.id} references missing conversation "
                    f"{attachment.conversation_id}"
                )
            if message is not None and attachment.conversation_id != message.conversation_id:
                errors.append(
                    f"chat attachment {attachment.id} conversation {attachment.conversation_id} "
                    f"does not match message conversation {message.conversation_id}"
                )
            if message is not None and attachment.id not in message.attachment_ids:
                errors.append(
                    f"chat attachment {attachment.id} is not linked from message {message.id}"
                )
        for dispatch in self.dispatches.values():
            chat_conversation_ids: set[str] = set()
            for message_id in dispatch.chat_message_ids:
                message = self.conversation_messages.get(message_id)
                if message is None:
                    errors.append(
                        f"dispatch {dispatch.id} references missing chat message {message_id}"
                    )
                    continue
                if message.sender != "operator":
                    errors.append(
                        f"dispatch {dispatch.id} chat message {message_id} is not an operator message"
                    )
                if message.dispatch_id != dispatch.id:
                    errors.append(
                        f"dispatch {dispatch.id} chat message {message_id} has a different dispatch"
                    )
                chat_conversation_ids.add(message.conversation_id)
            if len(chat_conversation_ids) > 1:
                errors.append(f"dispatch {dispatch.id} chat messages span multiple conversations")

    def _validate_external_chat_bridge(self, errors: list[str]) -> None:
        ingress_keys: set[tuple[str, str, str]] = set()
        for record in self.external_chat_ingress.values():
            key = (
                record.provider,
                record.communication_target_id,
                record.source_message_id,
            )
            if key in ingress_keys:
                errors.append(
                    "external chat ingress has duplicate provider/target/source message "
                    f"identity {key!r}"
                )
            ingress_keys.add(key)
            message = self.conversation_messages.get(record.message_id)
            if message is None:
                errors.append(
                    f"external chat ingress {record.id} references missing message {record.message_id}"
                )
                continue
            if message.conversation_id != record.conversation_id:
                errors.append(
                    f"external chat ingress {record.id} conversation does not match message"
                )
            if message.dispatch_id != record.dispatch_id:
                errors.append(f"external chat ingress {record.id} dispatch does not match message")
        delivery_keys: set[tuple[str, str]] = set()
        for record in self.external_chat_deliveries.values():
            key = (record.agent_reply_message_id, record.communication_target_id)
            if key in delivery_keys:
                errors.append(f"external chat delivery has duplicate reply/target identity {key!r}")
            delivery_keys.add(key)
            reply = self.conversation_messages.get(record.agent_reply_message_id)
            if reply is None or reply.sender != "agent":
                errors.append(
                    f"external chat delivery {record.id} references missing agent reply "
                    f"{record.agent_reply_message_id}"
                )
            operator_message = self.conversation_messages.get(record.operator_message_id)
            if operator_message is None or operator_message.sender != "operator":
                errors.append(
                    f"external chat delivery {record.id} references missing operator message "
                    f"{record.operator_message_id}"
                )

    def _validate_releases(self, errors: list[str]) -> None:
        for release in self.release_packages.values():
            project = self.projects.get(release.project_id)
            if project is None:
                errors.append(
                    f"release {release.id} references missing project {release.project_id}"
                )
                continue
            if release.owner_agent_id is not None:
                owner = self.agents.get(release.owner_agent_id)
                terminal_historical_owner = (
                    release.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
                    and owner is not None
                    and owner.team_id is None
                    and owner.retired_team_id == project.team_id
                )
                if owner is None or (
                    owner.team_id != project.team_id and not terminal_historical_owner
                ):
                    errors.append(
                        f"release {release.id} references invalid owner {release.owner_agent_id}"
                    )
            if release.review_cohort_id is not None:
                if release.manifest is None or not release.manifest.review_requirements:
                    errors.append(
                        f"release {release.id} review cohort requires frozen review requirements"
                    )
                else:
                    current_reviews = [
                        self.review_requests[review_id]
                        for review_id in release.review_request_ids
                        if review_id in self.review_requests
                        and self.review_requests[review_id].superseded_by_review_request_id is None
                        and self.review_requests[review_id].retired_at is None
                    ]
                    current_members = [
                        (review.reviewer_agent_id, review.review_profile_id)
                        for review in current_reviews
                    ]
                    required_members = [
                        (requirement.reviewer_agent_id, requirement.review_profile_id)
                        for requirement in release.manifest.review_requirements
                    ]
                    if sorted(current_members) != sorted(required_members):
                        errors.append(
                            f"release {release.id} cohort membership does not match its "
                            "frozen review requirements"
                        )
            if release.supersedes_release_package_id is not None:
                predecessor = self.release_packages.get(release.supersedes_release_package_id)
                if predecessor is None:
                    errors.append(
                        f"release {release.id} supersedes missing release "
                        f"{release.supersedes_release_package_id}"
                    )
                elif predecessor.id == release.id:
                    errors.append(f"release {release.id} cannot supersede itself")
                elif predecessor.project_id != release.project_id:
                    errors.append(
                        f"release {release.id} supersedes release {predecessor.id} "
                        "from another project"
                    )
            for deliverable_id in release.deliverable_ids:
                deliverable = self.deliverables.get(deliverable_id)
                if deliverable is None:
                    errors.append(
                        f"release {release.id} references missing deliverable {deliverable_id}"
                    )
                    continue
                task = self.tasks.get(deliverable.task_id)
                if task is not None and task.project_id != project.id:
                    errors.append(
                        f"release {release.id} includes deliverable {deliverable_id} outside project"
                    )
            waiver_deliverable_ids: set[str] = set()
            for waiver in release.verification_waivers:
                if waiver.deliverable_id not in release.deliverable_ids:
                    errors.append(
                        f"release {release.id} waiver references deliverable "
                        f"{waiver.deliverable_id} outside the package"
                    )
                if waiver.deliverable_id in waiver_deliverable_ids:
                    errors.append(
                        f"release {release.id} has duplicate waiver for {waiver.deliverable_id}"
                    )
                waiver_deliverable_ids.add(waiver.deliverable_id)
            for run_id in release.verification_run_ids:
                run = self.release_verification_runs.get(run_id)
                if run is None:
                    errors.append(
                        f"release {release.id} references missing verification run {run_id}"
                    )
                elif run.release_package_id != release.id:
                    errors.append(
                        f"release {release.id} references verification run {run_id} "
                        f"owned by {run.release_package_id}"
                    )
                    continue
                if (
                    release.candidate_commit_sha is not None
                    and run.candidate_commit_sha != release.candidate_commit_sha
                ):
                    errors.append(
                        f"release {release.id} verification run {run_id} targets "
                        f"{run.candidate_commit_sha}, not candidate "
                        f"{release.candidate_commit_sha}"
                    )
                if run.correction_task_id is not None:
                    correction_task = self.tasks.get(run.correction_task_id)
                    if correction_task is None or correction_task.project_id != release.project_id:
                        errors.append(
                            f"release verification run {run.id} references invalid "
                            f"correction task {run.correction_task_id}"
                        )
                if run.supersedes_verification_run_id is not None:
                    previous = self.release_verification_runs.get(
                        run.supersedes_verification_run_id
                    )
                    if previous is None or previous.release_package_id != release.id:
                        errors.append(
                            f"release verification run {run.id} supersedes an invalid run"
                        )
                    elif previous.superseded_by_verification_run_id != run.id:
                        errors.append(
                            f"release verification run {run.id} has non-reciprocal lineage"
                        )
                if run.superseded_by_verification_run_id is not None:
                    successor = self.release_verification_runs.get(
                        run.superseded_by_verification_run_id
                    )
                    if successor is None or successor.release_package_id != release.id:
                        errors.append(f"release verification run {run.id} has an invalid successor")
                    elif successor.supersedes_verification_run_id != run.id:
                        errors.append(
                            f"release verification run {run.id} has non-reciprocal lineage"
                        )
            for review_id in release.review_request_ids:
                review = self.review_requests.get(review_id)
                if review is None:
                    errors.append(
                        f"release {release.id} references missing review request {review_id}"
                    )
                elif review.release_package_id != release.id:
                    errors.append(
                        f"release {release.id} references review {review_id} owned by "
                        f"{review.release_package_id}"
                    )
                    continue
                if review.supersedes_review_request_id is not None:
                    previous_review = self.review_requests.get(review.supersedes_review_request_id)
                    if previous_review is None or previous_review.release_package_id != release.id:
                        errors.append(f"release review {review.id} supersedes an invalid review")
                    elif previous_review.superseded_by_review_request_id != review.id:
                        errors.append(f"release review {review.id} has non-reciprocal lineage")
                if review.superseded_by_review_request_id is not None:
                    successor_review = self.review_requests.get(
                        review.superseded_by_review_request_id
                    )
                    if (
                        successor_review is None
                        or successor_review.release_package_id != release.id
                    ):
                        errors.append(f"release review {review.id} has an invalid successor")
                    elif successor_review.supersedes_review_request_id != review.id:
                        errors.append(f"release review {review.id} has non-reciprocal lineage")
                if (
                    release.candidate_commit_sha is not None
                    and review.candidate_commit_sha != release.candidate_commit_sha
                ):
                    errors.append(
                        f"release {release.id} review {review_id} targets "
                        f"{review.candidate_commit_sha}, not candidate "
                        f"{release.candidate_commit_sha}"
                    )
            current_runs = [
                self.release_verification_runs[run_id]
                for run_id in release.verification_run_ids
                if run_id in self.release_verification_runs
                and self.release_verification_runs[run_id].superseded_by_verification_run_id is None
            ]
            if len(current_runs) > 1:
                errors.append(f"release {release.id} has multiple current verification runs")
            if release.manifest is not None:
                if release.manifest.project_id != release.project_id:
                    errors.append(
                        f"release {release.id} manifest belongs to project "
                        f"{release.manifest.project_id}"
                    )
                if release.manifest.release_package_id != release.id:
                    errors.append(
                        f"release {release.id} manifest belongs to "
                        f"{release.manifest.release_package_id}"
                    )
                if release.manifest.candidate_commit_sha != release.candidate_commit_sha:
                    errors.append(
                        f"release {release.id} manifest candidate does not match the release"
                    )
                manifest_deliverable_ids = [
                    evidence.deliverable_id for evidence in release.manifest.deliverables
                ]
                if len(manifest_deliverable_ids) != len(set(manifest_deliverable_ids)):
                    errors.append(f"release {release.id} manifest has duplicate deliverables")
                if set(manifest_deliverable_ids) != set(release.deliverable_ids):
                    errors.append(
                        f"release {release.id} manifest deliverables do not match the package"
                    )
                for evidence in release.manifest.deliverables:
                    if evidence.review_manifest is None:
                        continue
                    accepted_shas = {
                        review.reviewed_commit_sha for review in evidence.accepted_reviews
                    }
                    if (
                        accepted_shas
                        and evidence.review_manifest.reviewed_commit_sha not in accepted_shas
                    ):
                        errors.append(
                            f"release {release.id} deliverable {evidence.deliverable_id} "
                            "manifest does not match accepted review evidence"
                        )

    def _validate_human_reviews(self, errors: list[str]) -> None:
        request_ids_by_release: dict[str, list[str]] = {}
        for request in self.human_review_requests.values():
            release = self.release_packages.get(request.release_package_id)
            if release is None:
                errors.append(
                    f"human review {request.id} references missing release "
                    f"{request.release_package_id}"
                )
                continue
            request_ids_by_release.setdefault(release.id, []).append(request.id)
            if release.candidate_commit_sha != request.candidate_commit_sha:
                errors.append(
                    f"human review {request.id} candidate does not match release {release.id}"
                )
            if release.candidate_tree_hash != request.candidate_tree_hash:
                errors.append(f"human review {request.id} tree does not match release {release.id}")
        for release_id, request_ids in request_ids_by_release.items():
            current = [
                request_id
                for request_id in request_ids
                if self.human_review_requests[request_id].state
                in {"preparing", "pending", "blocked"}
            ]
            if len(current) > 1:
                errors.append(f"release {release_id} has multiple current human review requests")

        decision_keys: set[tuple[str, str]] = set()
        for decision in self.human_review_decisions.values():
            if decision.human_review_request_id not in self.human_review_requests:
                errors.append(
                    f"human decision {decision.id} references missing request "
                    f"{decision.human_review_request_id}"
                )
            for finding_id in decision.confirmed_finding_ids:
                if finding_id not in self.human_review_findings:
                    errors.append(
                        f"human decision {decision.id} confirms missing finding {finding_id}"
                    )
            key = (decision.human_review_request_id, decision.request_id)
            if key in decision_keys:
                errors.append(
                    f"human review {decision.human_review_request_id} has duplicate "
                    f"decision request id {decision.request_id}"
                )
            decision_keys.add(key)

        for finding in self.human_review_findings.values():
            source_request = self.human_review_requests.get(finding.source_human_review_request_id)
            if source_request is None:
                errors.append(
                    f"human finding {finding.id} references missing source request "
                    f"{finding.source_human_review_request_id}"
                )
            if finding.correction_task_id not in self.tasks:
                errors.append(
                    f"human finding {finding.id} references missing correction task "
                    f"{finding.correction_task_id}"
                )
            for deliverable_id in finding.target_deliverable_ids:
                if deliverable_id not in self.deliverables:
                    errors.append(
                        f"human finding {finding.id} references missing deliverable "
                        f"{deliverable_id}"
                    )
            for deliverable_id in finding.proposed_by_deliverable_ids:
                if deliverable_id not in self.deliverables:
                    errors.append(
                        f"human finding {finding.id} references missing proposed "
                        f"deliverable {deliverable_id}"
                    )
            if (
                finding.resolved_by_human_review_request_id is not None
                and finding.resolved_by_human_review_request_id not in self.human_review_requests
            ):
                errors.append(
                    f"human finding {finding.id} references missing resolving request "
                    f"{finding.resolved_by_human_review_request_id}"
                )
            elif finding.resolved_by_human_review_request_id is not None:
                resolving_request = self.human_review_requests[
                    finding.resolved_by_human_review_request_id
                ]
                if resolving_request.id == finding.source_human_review_request_id:
                    errors.append(
                        f"human finding {finding.id} cannot be resolved by its source request"
                    )
                elif source_request is not None and not self._release_descends_from(
                    resolving_request.release_package_id,
                    source_request.release_package_id,
                ):
                    errors.append(
                        f"human finding {finding.id} resolving request "
                        f"{resolving_request.id} is not on a successor release"
                    )

    def _release_descends_from(
        self,
        release_package_id: str,
        ancestor_release_package_id: str,
    ) -> bool:
        current_id = release_package_id
        seen: set[str] = set()
        while current_id not in seen:
            seen.add(current_id)
            current = self.release_packages.get(current_id)
            if current is None or current.supersedes_release_package_id is None:
                return False
            current_id = current.supersedes_release_package_id
            if current_id == ancestor_release_package_id:
                return True
        return False

    def _validate_dispatches(self, errors: list[str]) -> None:
        for dispatch in self.dispatches.values():
            dispatch_is_live = dispatch.status in {
                TeamDispatchStatus.PREPARING,
                TeamDispatchStatus.LEASED,
            }
            agent = self.agents.get(dispatch.agent_id)
            if agent is None:
                errors.append(
                    f"dispatch {dispatch.id} references missing agent {dispatch.agent_id}"
                )
            elif dispatch_is_live and agent.session_id != dispatch.session_id:
                errors.append(f"dispatch {dispatch.id} session does not match agent {agent.id}")
            for task_id in dispatch.task_ids:
                task = self.tasks.get(task_id)
                if task is None:
                    errors.append(f"dispatch {dispatch.id} references missing task {task_id}")
            for blocker_id in dispatch.blocker_ids:
                if blocker_id not in self.blockers:
                    errors.append(f"dispatch {dispatch.id} references missing blocker {blocker_id}")
            for review_id in dispatch.review_request_ids:
                review = self.review_requests.get(review_id)
                if review is None:
                    errors.append(f"dispatch {dispatch.id} references missing review {review_id}")
            for inbox_id in dispatch.inbox_item_ids:
                item = self.inbox.get(inbox_id)
                if item is None:
                    errors.append(
                        f"dispatch {dispatch.id} references missing inbox item {inbox_id}"
                    )
            for project_id in dispatch.project_ids:
                if project_id not in self.projects:
                    errors.append(f"dispatch {dispatch.id} references missing project {project_id}")
            for snapshot_id in dispatch.source_evidence_snapshot_ids:
                snapshot = self.external_source_evidence_snapshots.get(snapshot_id)
                if snapshot is None:
                    errors.append(
                        f"dispatch {dispatch.id} references missing external source evidence "
                        f"snapshot {snapshot_id}"
                    )
                    continue
                effective_agent_team_id = (
                    agent.team_id or agent.retired_team_id if agent is not None else None
                )
                if (
                    effective_agent_team_id is not None
                    and snapshot.team_id != effective_agent_team_id
                ):
                    errors.append(
                        f"dispatch {dispatch.id} external source evidence snapshot "
                        f"{snapshot.id} belongs to team {snapshot.team_id}"
                    )
                if (
                    snapshot.project_id is not None
                    and snapshot.project_id not in dispatch.project_ids
                ):
                    errors.append(
                        f"dispatch {dispatch.id} external source evidence snapshot "
                        f"{snapshot.id} is outside dispatch project scope"
                    )
            for deliverable_id in dispatch.initial_release_decision_deliverable_ids:
                if deliverable_id not in self.deliverables:
                    errors.append(
                        f"dispatch {dispatch.id} initial release authority references "
                        f"missing deliverable {deliverable_id}"
                    )
            for authority in dispatch.review_routing_authorities:
                reviewer = self.agents.get(authority.reviewer_agent_id)
                project = self.projects.get(authority.project_id)
                task = self.tasks.get(authority.task_id)
                deliverable = self.deliverables.get(authority.deliverable_id)
                if reviewer is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing agent {authority.reviewer_agent_id}"
                    )
                if project is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing project {authority.project_id}"
                    )
                if task is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing task {authority.task_id}"
                    )
                elif task.project_id != authority.project_id:
                    errors.append(
                        f"dispatch {dispatch.id} review routing task {task.id} "
                        f"does not belong to project {authority.project_id}"
                    )
                if deliverable is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        f"missing deliverable {authority.deliverable_id}"
                    )
                elif deliverable.task_id != authority.task_id:
                    errors.append(
                        f"dispatch {dispatch.id} review routing deliverable "
                        f"{deliverable.id} does not belong to task {authority.task_id}"
                    )
                consumed_review = (
                    self.review_requests.get(authority.consumed_by_review_request_id)
                    if authority.consumed_by_review_request_id is not None
                    else None
                )
                if authority.consumed_by_review_request_id is not None and consumed_review is None:
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority references "
                        "missing consuming review "
                        f"{authority.consumed_by_review_request_id}"
                    )
                elif consumed_review is not None and (
                    consumed_review.deliverable_id != authority.deliverable_id
                    or consumed_review.reviewer_agent_id != authority.reviewer_agent_id
                    or consumed_review.requested_by_agent_id != dispatch.agent_id
                ):
                    errors.append(
                        f"dispatch {dispatch.id} review routing authority has "
                        f"invalid consuming review {consumed_review.id}"
                    )
                if (
                    dispatch_is_live
                    and reviewer is not None
                    and project is not None
                    and reviewer.team_id != project.team_id
                ):
                    errors.append(
                        f"dispatch {dispatch.id} review routing agent {reviewer.id} "
                        f"does not belong to project team {project.team_id}"
                    )

    def _validate_worktree_cleanup_candidates(self, errors: list[str]) -> None:
        for candidate in self.worktree_cleanup_candidates.values():
            if candidate.agent_id not in self.agents:
                errors.append(
                    f"worktree cleanup {candidate.id} references missing agent {candidate.agent_id}"
                )
            task = self.tasks.get(candidate.task_id)
            if task is None:
                errors.append(
                    f"worktree cleanup {candidate.id} references missing task {candidate.task_id}"
                )
            elif task.project_id != candidate.project_id:
                errors.append(
                    f"worktree cleanup {candidate.id} task belongs to project {task.project_id}"
                )
            project = self.projects.get(candidate.project_id)
            if project is None:
                errors.append(
                    f"worktree cleanup {candidate.id} references missing project "
                    f"{candidate.project_id}"
                )

    def _validate_git_preparation_attempts(self, errors: list[str]) -> None:
        for attempt in self.git_preparation_attempts.values():
            if attempt.agent_id not in self.agents:
                errors.append(
                    f"Git preparation attempt {attempt.id} references missing agent "
                    f"{attempt.agent_id}"
                )
            task = self.tasks.get(attempt.task_id)
            if task is None:
                errors.append(
                    f"Git preparation attempt {attempt.id} references missing task "
                    f"{attempt.task_id}"
                )
            elif task.project_id != attempt.project_id:
                errors.append(
                    f"Git preparation attempt {attempt.id} task belongs to project "
                    f"{task.project_id}"
                )
            project = self.projects.get(attempt.project_id)
            if project is None:
                errors.append(
                    f"Git preparation attempt {attempt.id} references missing project "
                    f"{attempt.project_id}"
                )
            if attempt.state == TeamGitPreparationAttemptState.COMMITTED:
                context = self.git_work_contexts.get(attempt.git_work_context_id or "")
                if context is None:
                    errors.append(
                        f"Git preparation attempt {attempt.id} references missing committed "
                        f"work context {attempt.git_work_context_id}"
                    )
                elif (
                    context.agent_id != attempt.agent_id
                    or context.task_id != attempt.task_id
                    or context.agent_branch != attempt.branch
                    or context.worktree_path != attempt.worktree_path
                ):
                    errors.append(
                        f"Git preparation attempt {attempt.id} committed context provenance "
                        "does not match"
                    )

    def _validate_ephemeral_assignments(self, errors: list[str]) -> None:
        for assignment in self.ephemeral_assignments.values():
            parent = self.agents.get(assignment.parent_agent_id)
            if parent is None:
                errors.append(
                    f"ephemeral assignment {assignment.id} references missing parent "
                    f"{assignment.parent_agent_id}"
                )
            project = self.projects.get(assignment.project_id)
            if project is None:
                errors.append(
                    f"ephemeral assignment {assignment.id} references missing project "
                    f"{assignment.project_id}"
                )
            task = self.tasks.get(assignment.task_id)
            if task is None:
                errors.append(
                    f"ephemeral assignment {assignment.id} references missing task "
                    f"{assignment.task_id}"
                )
            if parent is not None and not _agent_has_project_assignment(
                self, parent.id, assignment.project_id
            ):
                errors.append(
                    f"ephemeral assignment {assignment.id} parent {parent.id} "
                    f"is not assigned to project {assignment.project_id}"
                )
            if task is not None and task.project_id != assignment.project_id:
                errors.append(
                    f"ephemeral assignment {assignment.id} task {task.id} "
                    f"belongs to project {task.project_id}"
                )
            if task is not None and task.owner_agent_id != assignment.parent_agent_id:
                errors.append(
                    f"ephemeral assignment {assignment.id} task {task.id} "
                    f"is owned by {task.owner_agent_id}"
                )

    def _validate_git_promotions(self, errors: list[str]) -> None:
        for promotion in self.git_promotions.values():
            if promotion.project_id not in self.projects:
                errors.append(
                    f"git promotion {promotion.id} references missing project "
                    f"{promotion.project_id}"
                )
            if promotion.task_id is not None and promotion.task_id not in self.tasks:
                errors.append(
                    f"git promotion {promotion.id} references missing task {promotion.task_id}"
                )
            if (
                promotion.deliverable_id is not None
                and promotion.deliverable_id not in self.deliverables
            ):
                errors.append(
                    f"git promotion {promotion.id} references missing deliverable "
                    f"{promotion.deliverable_id}"
                )
            if (
                promotion.release_package_id is not None
                and promotion.release_package_id not in self.release_packages
            ):
                errors.append(
                    f"git promotion {promotion.id} references missing release "
                    f"{promotion.release_package_id}"
                )

    def _validate_stand_downs(self, errors: list[str]) -> None:
        for stand_down in self.stand_downs.values():
            if stand_down.agent_id not in self.agents:
                errors.append(
                    f"stand-down {stand_down.id} references missing agent {stand_down.agent_id}"
                )
            if stand_down.cleared_at is not None and stand_down.cleared_by is None:
                errors.append(f"stand-down {stand_down.id} has cleared_at without cleared_by")


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of strings")
    return [_required_text(item, field_name) for item in value]


def _unique_text_list(value: object, field_name: str) -> list[str]:
    items = _text_list(value, field_name)
    _ensure_unique(items, field_name)
    return items


def _ensure_unique(values: list[str], field_name: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
            continue
        seen.add(value)
    if duplicates:
        raise ValueError(
            f"{field_name} contains duplicate ids: {', '.join(sorted(set(duplicates)))}"
        )


def task_payload_with_derived(state: TeamState, task: AgentTask) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    payload["child_task_ids"] = state.child_task_ids(task.id)
    payload["dependency_status"] = state.dependency_status(task.id)
    payload["blocked_by_task_ids"] = state.blocked_by_task_ids(task.id)
    payload["subtask_counts"] = state.subtask_counts(task.id)
    payload["waiting_reason"] = _task_waiting_reason(state, task)
    return payload


def effective_outcome_kind_definitions(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    include_archived: bool = False,
) -> dict[str, OutcomeKindDefinition]:
    effective: dict[str, OutcomeKindDefinition] = {}
    for definition in sorted(
        state.outcome_kind_definitions.values(),
        key=lambda item: (item.project_id is not None, item.id),
    ):
        if definition.team_id != team_id:
            continue
        if definition.project_id not in {None, project_id}:
            continue
        if definition.project_id is not None and project_id is None:
            continue
        if definition.archived and not include_archived:
            effective.pop(definition.kind, None)
            continue
        effective[definition.kind] = definition
    return effective


def resolve_outcome_kind_definition(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    kind: str,
    include_archived: bool = False,
) -> OutcomeKindDefinition | None:
    return effective_outcome_kind_definitions(
        state,
        team_id=team_id,
        project_id=project_id,
        include_archived=include_archived,
    ).get(kind)


def outcome_kind_snapshots(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    kinds: Sequence[str],
    include_archived: bool = False,
) -> list[OutcomeKindSnapshot]:
    snapshots: list[OutcomeKindSnapshot] = []
    missing: list[str] = []
    for kind in _unique_text_list(list(kinds), "outcome kinds"):
        definition = resolve_outcome_kind_definition(
            state,
            team_id=team_id,
            project_id=project_id,
            kind=kind,
            include_archived=include_archived,
        )
        if definition is None:
            missing.append(kind)
        else:
            snapshots.append(definition.snapshot())
    if missing:
        raise ValueError("unknown or archived outcome kinds: " + ", ".join(missing))
    return snapshots


def deliverable_requires_formal_review(
    deliverable: Deliverable,
    *,
    state: TeamState | None = None,
) -> bool:
    has_current_review = bool(deliverable.review_request_ids)
    if state is not None:
        has_current_review = any(
            review_id in state.review_requests
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
            for review_id in deliverable.review_request_ids
        )
    return has_current_review or any(
        finding.state == "open" for finding in deliverable.carried_findings
    )


def _task_waiting_reason(state: TeamState, task: AgentTask) -> str | None:
    if task.state == TeamTaskState.WAITING_FOR_INPUT:
        return "input"
    if task.state != TeamTaskState.WAITING_FOR_REVIEW:
        return None
    active_deliverable_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
    deliverables = [
        state.deliverables[deliverable_id]
        for deliverable_id in task.deliverable_ids
        if deliverable_id in state.deliverables
        and state.deliverables[deliverable_id].superseded_by_deliverable_id is None
        and state.deliverables[deliverable_id].state in active_deliverable_states
    ]
    if any(
        deliverable_requires_formal_review(deliverable, state=state) for deliverable in deliverables
    ):
        return "review"
    if deliverables:
        return "superior_acceptance"
    return None


def _project_assignment_id(
    project_id: str,
    agent_id: str,
    kind: AgentProjectAssignmentKind,
) -> str:
    return f"{project_id}__{agent_id}__{kind.value}"


def project_assignments_for_agent(
    state: TeamState,
    agent_id: str,
    *,
    project_id: str | None = None,
    kind: AgentProjectAssignmentKind | None = None,
) -> list[AgentProjectAssignment]:
    return sorted(
        [
            assignment
            for assignment in state.project_assignments.values()
            if assignment.agent_id == agent_id
            and (project_id is None or assignment.project_id == project_id)
            and (kind is None or assignment.kind == kind)
        ],
        key=lambda item: (item.project_id, item.kind.value, item.id),
    )


def project_assignments_for_project(
    state: TeamState,
    project_id: str,
    *,
    kind: AgentProjectAssignmentKind | None = None,
) -> list[AgentProjectAssignment]:
    return sorted(
        [
            assignment
            for assignment in state.project_assignments.values()
            if assignment.project_id == project_id and (kind is None or assignment.kind == kind)
        ],
        key=lambda item: (item.kind.value, item.agent_id, item.id),
    )


def _agent_has_project_assignment(
    state: TeamState,
    agent_id: str,
    project_id: str,
    *,
    kind: AgentProjectAssignmentKind | None = None,
) -> bool:
    return bool(project_assignments_for_agent(state, agent_id, project_id=project_id, kind=kind))


def agent_has_project_assignment(
    state: TeamState,
    agent_id: str,
    project_id: str,
    *,
    kind: AgentProjectAssignmentKind | None = None,
) -> bool:
    return _agent_has_project_assignment(state, agent_id, project_id, kind=kind)


def project_lead_agent_id(state: TeamState, project_id: str) -> str | None:
    leads = project_assignments_for_project(
        state,
        project_id,
        kind=AgentProjectAssignmentKind.PROJECT_LEAD,
    )
    return leads[0].agent_id if leads else None


def agent_project_ids(state: TeamState, agent_id: str) -> list[str]:
    return sorted(
        {assignment.project_id for assignment in project_assignments_for_agent(state, agent_id)}
    )


def agent_lead_project_ids(state: TeamState, agent_id: str) -> list[str]:
    return sorted(
        {
            assignment.project_id
            for assignment in project_assignments_for_agent(
                state,
                agent_id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD,
            )
        }
    )


def agent_project_assignment_payloads(state: TeamState, agent_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assignment in project_assignments_for_agent(state, agent_id):
        payload = assignment.model_dump(mode="json")
        role = state.roles.get(assignment.role_id) if assignment.role_id is not None else None
        payload["role_name"] = role.name if role is not None else None
        payload["role_permissions"] = (
            [permission.value for permission in role.permissions] if role is not None else []
        )
        if (
            assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD
            and RolePermission.CREATE_ROOT_TASK.value not in payload["role_permissions"]
        ):
            payload["role_permissions"].append(RolePermission.CREATE_ROOT_TASK.value)
        rows.append(payload)
    return rows


def effective_role_permissions(
    state: TeamState,
    agent: PersistentAgent,
    *,
    project_id: str | None = None,
) -> list[RolePermission]:
    permissions: list[RolePermission] = []

    def add(items: list[RolePermission]) -> None:
        for item in items:
            if item not in permissions:
                permissions.append(item)

    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None and (
        role.project_id is None or project_id is None or role.project_id == project_id
    ):
        add(role.permissions)
    for assignment in project_assignments_for_agent(state, agent.id, project_id=project_id):
        assignment_role = (
            state.roles.get(assignment.role_id) if assignment.role_id is not None else None
        )
        if assignment_role is not None:
            add(assignment_role.permissions)
        if assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD:
            add([RolePermission.CREATE_ROOT_TASK])
    return permissions


def agent_activity_summaries_payload(
    state: TeamState,
    *,
    runtime_operations: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    agent_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    generated_at = (now or utc_now()).isoformat()
    if runtime_operations is None:
        runtime_operations = runtime_operations_payload(state, now=now, agent_ids=agent_ids)
    runtime_agent_rows = _activity_rows_by_agent(runtime_operations.get("agents"))
    session_health_rows = _activity_rows_by_agent(runtime_operations.get("session_health"))
    attention_rows = _activity_attention_rows_by_agent(runtime_operations.get("attention"))
    included_agent_ids = (
        sorted(agent_id for agent_id in agent_ids if agent_id in state.agents)
        if agent_ids is not None
        else sorted(state.agents)
    )
    return {
        agent_id: _agent_activity_summary_payload(
            state,
            agent_id=agent_id,
            generated_at=generated_at,
            runtime_row=runtime_agent_rows.get(agent_id),
            session_health_row=session_health_rows.get(agent_id),
            attention_rows=attention_rows.get(agent_id, []),
        )
        for agent_id in included_agent_ids
    }


def _agent_activity_summary_payload(
    state: TeamState,
    *,
    agent_id: str,
    generated_at: str,
    runtime_row: Mapping[str, Any] | None,
    session_health_row: Mapping[str, Any] | None,
    attention_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    agent = state.agents[agent_id]
    runtime = state.agent_runtimes.get(agent_id)
    open_tasks = _activity_open_tasks(state, agent_id)
    inbox_items = _activity_pending_inbox_items(state, agent_id)
    current_task = _activity_current_task(state, open_tasks)
    blocked_task = next((task for task in open_tasks if _activity_task_blocked(state, task)), None)
    waiting_task = next(
        (
            task
            for task in open_tasks
            if not _activity_task_blocked(state, task) and _activity_task_waiting(state, task)
        ),
        None,
    )

    session_issue = _activity_session_issue(agent, runtime_row, session_health_row)
    if session_issue is not None:
        return _activity_summary(
            headline=session_issue,
            working_on="Unavailable",
            waiting_for=session_issue,
            tone="offline",
            refs=[_activity_ref("session", agent.session_id)],
            generated_at=generated_at,
        )

    stand_down = _activity_active_stand_down_for_agent(state, agent_id)
    if stand_down is not None:
        reason = _activity_text(stand_down.reason)
        headline = f"Stood down{f': {reason}' if reason else ''}"
        return _activity_summary(
            headline=headline,
            working_on="Stood down",
            waiting_for=reason or "Stand-down active",
            tone="attention",
            refs=[_activity_ref("stand_down", stand_down.id)],
            generated_at=generated_at,
        )

    if agent.status != AgentStatus.ACTIVE:
        status = _activity_humanize_token(agent.status.value)
        return _activity_summary(
            headline=f"{status} agent",
            working_on=status,
            waiting_for="Clear",
            tone="offline" if agent.status == AgentStatus.ARCHIVED else "idle",
            refs=[_activity_ref("agent", agent.id)],
            generated_at=generated_at,
        )

    active_dispatch_id = _activity_first_text(
        runtime_row.get("active_dispatch_id") if runtime_row is not None else None,
        runtime.active_dispatch_id if runtime is not None else None,
    )
    lease_active = bool(runtime_row.get("lease_active")) if runtime_row is not None else False
    if active_dispatch_id or lease_active:
        if current_task is not None:
            refs = [_activity_ref("task", current_task.id)]
            if active_dispatch_id:
                refs.append(_activity_ref("dispatch", active_dispatch_id))
            return _activity_summary(
                headline=f"Working on {_activity_task_label(current_task)}",
                working_on=_activity_task_label(current_task),
                waiting_for=_activity_waiting_for_task(state, current_task)
                if _activity_task_waiting_or_blocked(state, current_task)
                else "Clear",
                tone="working",
                refs=refs,
                generated_at=generated_at,
            )
        if inbox_items:
            item = inbox_items[0]
            refs = [_activity_ref("inbox", item.id)]
            if active_dispatch_id:
                refs.append(_activity_ref("dispatch", active_dispatch_id))
            return _activity_summary(
                headline=f"Handling {_activity_inbox_label(item)}",
                working_on=_activity_inbox_label(item),
                waiting_for="Clear",
                tone="working",
                refs=refs,
                generated_at=generated_at,
            )
        return _activity_summary(
            headline="Running team dispatch",
            working_on="Team dispatch",
            waiting_for="Clear",
            tone="working",
            refs=[_activity_ref("dispatch", active_dispatch_id)] if active_dispatch_id else [],
            generated_at=generated_at,
        )

    if blocked_task is not None:
        return _activity_summary(
            headline=f"Blocked on {_activity_task_label(blocked_task)}",
            working_on=_activity_task_label(blocked_task),
            waiting_for=_activity_waiting_for_task(state, blocked_task),
            tone="blocked",
            refs=_activity_task_refs(state, blocked_task),
            generated_at=generated_at,
        )

    if waiting_task is not None:
        return _activity_summary(
            headline=f"Waiting on {_activity_task_label(waiting_task)}",
            working_on=_activity_task_label(waiting_task),
            waiting_for=_activity_waiting_for_task(state, waiting_task),
            tone="waiting",
            refs=_activity_task_refs(state, waiting_task),
            generated_at=generated_at,
        )

    actionable_inbox = [item for item in inbox_items if item.priority != TeamInboxPriority.LOW]
    if actionable_inbox:
        item = actionable_inbox[0]
        return _activity_summary(
            headline=f"Handling {_activity_inbox_label(item)}",
            working_on=_activity_inbox_label(item),
            waiting_for="Response requested" if item.response_requested else "Clear",
            tone="attention",
            refs=[_activity_ref("inbox", item.id)],
            generated_at=generated_at,
        )

    if current_task is not None:
        return _activity_summary(
            headline=f"Working on {_activity_task_label(current_task)}",
            working_on=_activity_task_label(current_task),
            waiting_for="Clear",
            tone="working",
            refs=[_activity_ref("task", current_task.id)],
            generated_at=generated_at,
        )

    if inbox_items:
        item = inbox_items[0]
        return _activity_summary(
            headline=f"Has low-priority {_activity_inbox_label(item)}",
            working_on=_activity_inbox_label(item),
            waiting_for="Low priority",
            tone="idle",
            refs=[_activity_ref("inbox", item.id)],
            generated_at=generated_at,
        )

    if attention_rows:
        row = attention_rows[0]
        title = _activity_first_text(
            row.get("title"), row.get("detail"), row.get("kind"), "runtime attention"
        )
        kind = _activity_text(row.get("kind"))
        return _activity_summary(
            headline=f"Needs attention: {title}",
            working_on=title,
            waiting_for=_activity_text(row.get("detail")) or "Runtime attention",
            tone="blocked" if "blocked" in kind else "attention",
            refs=_activity_row_refs(row),
            generated_at=generated_at,
        )

    return _activity_summary(
        headline="Idle, no open work",
        working_on="No open work",
        waiting_for="Clear",
        tone="idle",
        refs=[_activity_ref("agent", agent.id)],
        generated_at=generated_at,
    )


def _activity_summary(
    *,
    headline: str,
    working_on: str,
    waiting_for: str,
    tone: str,
    refs: Sequence[Mapping[str, str | None]] | None,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "headline": _activity_clamp(headline, _ACTIVITY_SUMMARY_HEADLINE_MAX),
        "working_on": _activity_clamp(working_on, _ACTIVITY_SUMMARY_LINE_MAX),
        "waiting_for": _activity_clamp(waiting_for, _ACTIVITY_SUMMARY_LINE_MAX),
        "tone": tone,
        "source": "derived",
        "reason_refs": _activity_refs(refs or []),
        "generated_at": generated_at,
    }


def _activity_open_tasks(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    return [
        task
        for task in state.ordered_tasks_for_agent(agent_id)
        if task.state not in terminal_states
    ]


def _activity_current_task(state: TeamState, tasks: Sequence[AgentTask]) -> AgentTask | None:
    return next(
        (task for task in tasks if not _activity_task_waiting_or_blocked(state, task)), None
    ) or (tasks[0] if tasks else None)


def _activity_pending_inbox_items(state: TeamState, agent_id: str) -> list[AgentInboxItem]:
    priority_order = {
        TeamInboxPriority.HIGH: 0,
        TeamInboxPriority.NORMAL: 1,
        TeamInboxPriority.LOW: 2,
    }
    return sorted(
        [
            item
            for item in state.inbox.values()
            if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
        ],
        key=lambda item: (priority_order.get(item.priority, 9), item.created_at, item.id),
    )


def _activity_task_waiting_or_blocked(state: TeamState, task: AgentTask) -> bool:
    return _activity_task_blocked(state, task) or _activity_task_waiting(state, task)


def _activity_task_blocked(state: TeamState, task: AgentTask) -> bool:
    return task.state == TeamTaskState.BLOCKED or state.dependency_status(task.id) == "blocked"


def _activity_task_waiting(state: TeamState, task: AgentTask) -> bool:
    return (
        task.state
        in {
            TeamTaskState.WAITING_FOR_INPUT,
            TeamTaskState.WAITING_FOR_REVIEW,
        }
        or state.dependency_status(task.id) == "waiting"
        or bool(state.blocked_by_task_ids(task.id))
    )


def _activity_waiting_for_task(state: TeamState, task: AgentTask) -> str:
    blocked_by = state.blocked_by_task_ids(task.id)
    dependency_status = state.dependency_status(task.id)
    if task.state == TeamTaskState.WAITING_FOR_INPUT:
        return "Input"
    if task.state == TeamTaskState.WAITING_FOR_REVIEW:
        return "Review"
    if blocked_by:
        return f"Task {blocked_by[0]}"
    if dependency_status == "blocked":
        return "Blocked dependencies"
    if dependency_status == "waiting":
        return "Dependencies"
    if task.state == TeamTaskState.BLOCKED:
        return "Blocked task state"
    return "Clear"


def _activity_task_refs(state: TeamState, task: AgentTask) -> list[dict[str, str]]:
    refs = [_activity_ref("task", task.id)]
    refs.extend(_activity_ref("task", task_id) for task_id in state.blocked_by_task_ids(task.id))
    return _activity_refs(refs)


def _activity_task_label(task: AgentTask) -> str:
    return _activity_text(task.title) or task.id


def _activity_inbox_label(item: AgentInboxItem) -> str:
    item_type = _activity_humanize_token(item.item_type.value).lower()
    detail = _activity_inbox_detail(item)
    return f"{item_type}: {detail}" if detail else item_type


def _activity_inbox_detail(item: AgentInboxItem) -> str:
    for key in ("title", "question", "message", "summary", "description"):
        value = _activity_text(item.payload.get(key))
        if value:
            return value
    return item.task_id or item.id


def _activity_session_issue(
    agent: PersistentAgent,
    runtime_row: Mapping[str, Any] | None,
    session_health_row: Mapping[str, Any] | None,
) -> str | None:
    rows = [row for row in (runtime_row, session_health_row) if row is not None]
    if any(bool(row.get("session_is_expired") or row.get("is_expired")) for row in rows):
        return "Session expired"
    if any(
        _activity_text(row.get("status") or row.get("session_status")) == "expired" for row in rows
    ):
        return "Session expired"
    if any(row.get("session_present") is False for row in rows):
        return f"Session {agent.session_id} not found"
    return None


def _activity_active_stand_down_for_agent(state: TeamState, agent_id: str) -> AgentStandDown | None:
    candidates = [
        stand_down
        for stand_down in state.stand_downs.values()
        if stand_down.active
        and (
            stand_down.agent_id == agent_id
            or (
                stand_down.scope == "subtree"
                and _activity_agent_reports_under(state, agent_id, stand_down.agent_id)
            )
        )
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda stand_down: stand_down.set_at)[-1]


def _activity_agent_reports_under(state: TeamState, agent_id: str, ancestor_agent_id: str) -> bool:
    seen: set[str] = set()
    current = state.agents.get(agent_id)
    while current is not None and current.reports_to_agent_id:
        parent_id = current.reports_to_agent_id
        if parent_id == ancestor_agent_id:
            return True
        if parent_id in seen:
            return False
        seen.add(parent_id)
        current = state.agents.get(parent_id)
    return False


def _activity_rows_by_agent(rows: object) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return result
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        agent_id = _activity_first_text(row.get("agent_id"), row.get("id"))
        if agent_id:
            result[agent_id] = row
    return result


def _activity_attention_rows_by_agent(rows: object) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return result
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        agent_id = _activity_text(row.get("agent_id"))
        if agent_id:
            result.setdefault(agent_id, []).append(row)
    return result


def _activity_row_refs(row: Mapping[str, Any]) -> list[dict[str, str]]:
    refs = []
    for ref_type, key in (
        ("task", "task_id"),
        ("deliverable", "deliverable_id"),
        ("inbox", "inbox_item_id"),
        ("action", "action_record_id"),
        ("assignment", "assignment_id"),
        ("promotion", "promotion_id"),
        ("release", "release_package_id"),
        ("dispatch", "dispatch_id"),
        ("session", "session_id"),
    ):
        refs.append(_activity_ref(ref_type, row.get(key)))
    return _activity_refs(refs)


def _activity_ref(ref_type: str, ref_id: object) -> dict[str, str]:
    return {"type": ref_type, "id": _activity_text(ref_id)}


def _activity_refs(refs: Sequence[Mapping[str, str | None]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        ref_type = _activity_text(ref.get("type"))
        ref_id = _activity_text(ref.get("id"))
        if not ref_type or not ref_id:
            continue
        key = (ref_type, ref_id)
        if key in seen:
            continue
        seen.add(key)
        result.append({"type": ref_type, "id": ref_id})
        if len(result) >= _ACTIVITY_SUMMARY_REF_MAX:
            break
    return result


def _activity_first_text(*values: object) -> str:
    for value in values:
        text = _activity_text(value)
        if text:
            return text
    return ""


def _activity_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _activity_clamp(value: object, max_length: int) -> str:
    text = _activity_text(value)
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3].rstrip()}..."


def _activity_humanize_token(value: object) -> str:
    return _activity_text(value).replace("_", " ").title()


def team_state_payload(
    state: TeamState,
    *,
    include_agent_avatars: bool = True,
    include_agent_voice_profiles: bool | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    # Keep the model layer independent from domain services at import time while
    # still routing every task read through the canonical recursive evaluator.
    from .services.domain.task_assessment import (
        assess_tasks,
        task_payload_with_assessment,
    )

    if include_agent_voice_profiles is None:
        include_agent_voice_profiles = include_agent_avatars
    payload = state.model_dump(mode="json")
    payload["available_role_permissions"] = [permission.value for permission in RolePermission]
    payload["agents"] = {
        agent_id: _agent_payload_with_role(
            state,
            agent,
            include_avatar=include_agent_avatars,
            include_voice_profile=include_agent_voice_profiles,
        )
        for agent_id, agent in state.agents.items()
    }
    assessments = assess_tasks(state)
    payload["tasks"] = {
        task_id: task_payload_with_assessment(
            state,
            task,
            assessment=assessments[task_id],
            assessments=assessments,
        )
        for task_id, task in state.tasks.items()
    }
    payload["external_source_evidence_snapshots"] = {
        snapshot_id: external_source_evidence_snapshot_payload(snapshot, now=now)
        for snapshot_id, snapshot in state.external_source_evidence_snapshots.items()
    }
    payload["release_packages"] = {
        release_id: release_package_payload_with_derived(state, release)
        for release_id, release in state.release_packages.items()
    }
    payload["memories"] = {
        memory_id: _compact_memory_payload(memory) for memory_id, memory in state.memories.items()
    }
    for blocker in state.blockers.values():
        if blocker.state != TeamBlockerState.OPEN:
            continue
        collection_name = {
            TeamBlockerSourceKind.TASK: "tasks",
            TeamBlockerSourceKind.DELIVERABLE: "deliverables",
            TeamBlockerSourceKind.REVIEW_REQUEST: "review_requests",
            TeamBlockerSourceKind.RELEASE_PACKAGE: "release_packages",
            TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST: "human_review_requests",
            TeamBlockerSourceKind.AGENT_EXECUTION_STALL: "agents",
            TeamBlockerSourceKind.MCP_CAPABILITY: None,
            TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR: None,
            TeamBlockerSourceKind.EXPLICIT: None,
        }[blocker.source_kind]
        if collection_name is not None:
            source_payload = payload[collection_name].get(blocker.source_id)
            if isinstance(source_payload, dict):
                source_payload.setdefault("active_blocker_ids", [])
                if blocker.id not in source_payload["active_blocker_ids"]:
                    source_payload["active_blocker_ids"].append(blocker.id)
        if (
            blocker.task_id is not None
            and blocker.resolution_scope == TeamBlockerResolutionScope.TASK
        ):
            task_payload = payload["tasks"].get(blocker.task_id)
            if isinstance(task_payload, dict):
                task_payload.setdefault("active_blocker_ids", [])
                if blocker.id not in task_payload["active_blocker_ids"]:
                    task_payload["active_blocker_ids"].append(blocker.id)
    payload["runtime_operations"] = runtime_operations_payload(
        state,
        now=now,
        session_records=session_records,
        agent_execution_records=agent_execution_records,
    )
    payload["agent_activity_summaries"] = agent_activity_summaries_payload(
        state,
        runtime_operations=payload["runtime_operations"],
        now=now,
    )
    for agent_id, summary in payload["agent_activity_summaries"].items():
        agent_payload = payload["agents"].get(agent_id)
        if isinstance(agent_payload, dict):
            agent_payload["activity_summary"] = summary
    return payload


def _compact_memory_payload(memory: TeamMemoryRecord) -> dict[str, Any]:
    payload = memory.model_dump(mode="json")
    payload.pop("body", None)
    return payload


def release_package_payload_with_derived(
    state: TeamState,
    release: ReleasePackage,
) -> dict[str, Any]:
    payload = release.model_dump(mode="json")
    payload["verification_runs"] = [
        state.release_verification_runs[run_id].model_dump(mode="json")
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
    ]
    payload["release_reviews"] = [
        state.review_requests[review_id].model_dump(mode="json")
        for review_id in release.review_request_ids
        if review_id in state.review_requests
    ]
    if release.review_cohort_id is not None:
        cohort_reviews = [
            state.review_requests[review_id]
            for review_id in release.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
            and state.review_requests[review_id].review_cohort_id == release.review_cohort_id
        ]
        blocked_ids = [
            review.id for review in cohort_reviews if review.state == TeamRecordState.BLOCKED
        ]
        pending_ids = [
            review.id
            for review in cohort_reviews
            if review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
        ]
        rejected_ids = [
            review.id for review in cohort_reviews if review.state == TeamRecordState.REJECTED
        ]
        accepted_ids = [
            review.id for review in cohort_reviews if review.state == TeamRecordState.ACCEPTED
        ]
        cohort_status = (
            "blocked"
            if blocked_ids
            else "collecting"
            if pending_ids
            else "changes_requested"
            if rejected_ids
            else "accepted"
        )
        payload["release_review_cohort"] = {
            "id": release.review_cohort_id,
            "sealed_at": release.review_cohort_sealed_at,
            "status": cohort_status,
            "total": len(cohort_reviews),
            "resolved": len(accepted_ids) + len(rejected_ids),
            "pending_review_request_ids": pending_ids,
            "blocked_review_request_ids": blocked_ids,
            "rejected_review_request_ids": rejected_ids,
            "accepted_review_request_ids": accepted_ids,
        }
    related_promotions = _release_related_promotions(state, release)
    pending_promotions = [
        promotion.id for promotion in related_promotions if promotion.state == "pending"
    ]
    failed_promotions = [
        promotion.id for promotion in related_promotions if promotion.state == "failed"
    ]
    if pending_promotions:
        payload["pending_git_promotion_ids"] = pending_promotions
    if failed_promotions:
        payload["failed_git_promotion_ids"] = failed_promotions
    if related_promotions:
        payload["promotion_attempt_count"] = len(
            [promotion for promotion in related_promotions if promotion.state != "pending"]
        )
    next_action_hint = _release_next_action_hint(state, release)
    if next_action_hint is not None:
        payload["next_action"] = _release_next_action(state, release)
        payload["next_action_hint"] = next_action_hint
    blocked_reason_code = _release_blocked_reason_code(
        release,
        _release_blocking_promotion(state, release),
    )
    if blocked_reason_code is not None:
        payload["blocked_reason_code"] = blocked_reason_code
    return payload


def _release_next_action_hint(state: TeamState, release: ReleasePackage) -> str | None:
    if release.review_cohort_id is not None and release.state == TeamRecordState.PENDING:
        current_reviews = [
            state.review_requests[review_id]
            for review_id in release.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
            and state.review_requests[review_id].review_cohort_id == release.review_cohort_id
        ]
        pending_count = sum(
            review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
            for review in current_reviews
        )
        rejected_count = sum(review.state == TeamRecordState.REJECTED for review in current_reviews)
        if pending_count:
            return (
                f"Wait for the remaining {pending_count} release review cohort slot(s). "
                f"{rejected_count} rejection(s) are recorded, but correction work begins "
                "only after the sealed cohort closes."
            )
    if release.state != TeamRecordState.BLOCKED and not release.error:
        return None
    gate_failure = release.gate_failure
    if gate_failure is not None:
        if (
            release.state == TeamRecordState.BLOCKED
            and gate_failure.stage == "verification"
            and gate_failure.kind in {"infrastructure", "timeout"}
        ):
            current_run = _current_release_verification_run(state, release)
            if current_run is not None and (current_run.retry_requested_by or "").startswith(
                "agent:"
            ):
                return (
                    f"Do not retry verification gate {current_run.id} as an agent: the "
                    "agent-requested retry already failed against the unchanged candidate "
                    f"for release {release.id}. Escalate the run evidence and the concrete "
                    "infrastructure recovery requirement to an operator. "
                    f"Current blocker: {gate_failure.message}"
                )
            return (
                "Inspect the failed run and confirm concrete evidence that its "
                "infrastructure failure recovered. Only then use team_retry_release_gate "
                "with gate_kind verification and gate_id "
                f"{gate_failure.source_id or 'from the current verification gate'} against "
                f"the unchanged candidate for release {release.id}, citing exactly what "
                "changed. If no recovery evidence exists, update or escalate the blocker "
                "instead of retrying. "
                f"Current blocker: {gate_failure.message}"
            )
        if (
            release.state == TeamRecordState.BLOCKED
            and gate_failure.stage == "review"
            and gate_failure.kind == "blocked"
        ):
            return (
                "Use team_retry_release_gate with gate_kind review and gate_id "
                f"{gate_failure.source_id or 'from the current release review'} to replace the "
                f"blocked review for release {release.id} against the unchanged candidate after "
                "resolving its process or evidence blocker. "
                f"Current blocker: {gate_failure.message}"
            )
        if release.state == TeamRecordState.REJECTED:
            if (
                release.review_cohort_id is not None
                and gate_failure.stage == "review"
                and gate_failure.kind == "rejected"
                and release.review_correction_task_id is not None
            ):
                return (
                    f"Release review cohort {release.review_cohort_id} is complete and requested "
                    f"changes. Resolve correction task {release.review_correction_task_id} once "
                    "against the consolidated findings; Team will create the successor release."
                )
            return (
                f"Release {release.id} is terminal because its candidate failed or was "
                "rejected. Submit changed reviewed content as a new release; unchanged "
                "candidate content will be refused. "
                f"Current outcome: {gate_failure.message}"
            )
    failed_promotion = _release_blocking_promotion(state, release)
    parts = [
        f"Resolve existing release package {release.id}; do not create another "
        "git_release_requests item with this id."
    ]
    if failed_promotion is not None:
        parts.append(
            f"Failed {failed_promotion.promotion_kind} promotion "
            f"{failed_promotion.id}: {failed_promotion.source_branch} -> "
            f"{failed_promotion.target_branch}."
        )
        if failed_promotion.promotion_kind in {
            "feature",
            "correction_aggregate",
            "release",
        }:
            parts.append(
                "Assign semantic conflicts to the owning coder, have the resolved "
                "commit pushed to an agents/... ref, then submit "
                "git_release_recoveries for this package with the resolved commit_sha."
            )
            parts.append(
                f"Resolve and commit on promotion target branch {failed_promotion.target_branch}."
            )
        else:
            parts.append(
                f"This blocker is a failed {failed_promotion.promotion_kind} "
                "promotion, not a recoverable correction, feature, or release promotion; "
                "do not submit "
                "git_release_recoveries. Resolve and commit on failed promotion "
                f"target branch {failed_promotion.target_branch}, or escalate "
                "for operator/orchestrator retry."
            )
    if release.error:
        parts.append(f"Current blocker: {release.error}")
    if release.error and "merge conflict" in release.error.casefold():
        if failed_promotion is not None and failed_promotion.promotion_kind in {
            "feature",
            "correction_aggregate",
            "release",
        }:
            parts.append(
                "Resolve the merge conflict on the failed promotion target branch, "
                "push the resolved commit to an agents/... ref, then recover the "
                "existing release package with git_release_recoveries."
            )
        else:
            parts.append(
                "Resolve the merge conflict on the failed promotion target branch; "
                "git_release_recoveries only applies to failed correction-aggregate, "
                "feature, or release promotions."
            )
    if failed_promotion is None:
        parts.append(
            "Inspect the package's git_promotion_record_ids and current Git state "
            "to find the blocked promotion target before retrying or finalizing."
        )
    return " ".join(parts)


def _current_release_verification_run(
    state: TeamState,
    release: ReleasePackage,
) -> ReleaseVerificationRun | None:
    current = [
        state.release_verification_runs[run_id]
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
        and state.release_verification_runs[run_id].superseded_by_verification_run_id is None
    ]
    current.sort(key=lambda run: (run.attempt_number, run.created_at, run.id), reverse=True)
    return current[0] if current else None


def _release_next_action(state: TeamState, release: ReleasePackage) -> str:
    failure = release.gate_failure
    if failure is not None:
        if (
            release.state == TeamRecordState.BLOCKED
            and failure.stage == "verification"
            and failure.kind in {"infrastructure", "timeout"}
        ):
            current_run = _current_release_verification_run(state, release)
            if current_run is not None and (current_run.retry_requested_by or "").startswith(
                "agent:"
            ):
                return "escalate_release_verification_recovery"
            return "retry_release_verification"
        if (
            release.state == TeamRecordState.BLOCKED
            and failure.stage == "review"
            and failure.kind == "blocked"
        ):
            return "retry_release_review"
        if release.state == TeamRecordState.REJECTED:
            return "create_replacement_release"
    return "resolve_blocked_release"


def _release_related_promotions(
    state: TeamState,
    release: ReleasePackage,
) -> list[GitPromotionRecord]:
    release_promotion_ids = set(release.git_promotion_record_ids)
    promotions = [
        promotion
        for promotion in state.git_promotions.values()
        if promotion.id in release_promotion_ids or promotion.release_package_id == release.id
    ]
    promotions.sort(key=lambda promotion: (promotion.created_at, promotion.id))
    return promotions


def _release_blocked_reason_code(
    release: ReleasePackage,
    failed_promotion: GitPromotionRecord | None,
) -> str | None:
    if release.state != TeamRecordState.BLOCKED and not release.error:
        return None
    if release.gate_failure is not None:
        return f"release_{release.gate_failure.stage}_{release.gate_failure.kind}"
    text_parts = [release.error or ""]
    if failed_promotion is not None:
        text_parts.append(failed_promotion.error or "")
        text_parts.append(json.dumps(failed_promotion.outputs, sort_keys=True))
    text = "\n".join(text_parts).casefold()
    if "index.lock" in text:
        return "stale_index_lock"
    if "dirty worktree" in text:
        return "checkout_dirty"
    if "merge conflict" in text or "automatic merge failed" in text:
        return "merge_conflict"
    if "not something we can merge" in text or "source_ref_missing" in text:
        return "source_ref_missing"
    if "bare repo not found" in text:
        return "source_ref_missing"
    return "unexpected_git_failure"


def _release_blocking_promotion(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    release_promotion_ids = set(release.git_promotion_record_ids)
    failed_promotions = [
        promotion
        for promotion in state.git_promotions.values()
        if promotion.state == "failed"
        and (promotion.id in release_promotion_ids or promotion.release_package_id == release.id)
    ]
    if not failed_promotions:
        return None
    failed_promotions.sort(
        key=lambda promotion: (
            promotion.updated_at,
            promotion.created_at,
            promotion.id,
        ),
        reverse=True,
    )
    return failed_promotions[0]


def _agent_payload_with_role(
    state: TeamState,
    agent: PersistentAgent,
    *,
    include_avatar: bool = True,
    include_voice_profile: bool = True,
) -> dict[str, Any]:
    exclude: set[str] = set()
    if not include_avatar:
        exclude.add("avatar")
    if not include_voice_profile:
        exclude.add("voice_profile")
    payload = agent.model_dump(mode="json", exclude=exclude)
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    effective_permissions = effective_role_permissions(state, agent)
    payload["role_name"] = role.name if role is not None else None
    payload["role_permissions"] = [permission.value for permission in effective_permissions]
    payload["effective_role_permissions"] = [
        permission.value for permission in effective_permissions
    ]
    payload["project_assignments"] = agent_project_assignment_payloads(state, agent.id)
    payload["project_ids"] = agent_project_ids(state, agent.id)
    payload["lead_project_ids"] = agent_lead_project_ids(state, agent.id)
    effective_stand_downs = _effective_stand_down_payloads(state, agent.id)
    payload["effective_stand_downs"] = effective_stand_downs
    payload["stand_down_active"] = bool(effective_stand_downs)
    return payload


def _effective_stand_down_payloads(
    state: TeamState,
    agent_id: str,
) -> list[dict[str, Any]]:
    root_ids: list[str] = []
    seen: set[str] = set()
    current_id: str | None = agent_id
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        root_ids.append(current_id)
        current = state.agents.get(current_id)
        current_id = current.reports_to_agent_id if current is not None else None
    root_set = set(root_ids)
    rows: list[dict[str, Any]] = []
    for stand_down in sorted(
        state.stand_downs.values(),
        key=lambda item: (item.set_at, item.id),
    ):
        if (
            not stand_down.active
            or stand_down.agent_id not in root_set
            or (stand_down.scope == "agent" and stand_down.agent_id != agent_id)
        ):
            continue
        row = stand_down.model_dump(mode="json")
        row["inherited"] = stand_down.agent_id != agent_id
        row["root_agent_id"] = stand_down.agent_id
        rows.append(row)
    return rows


def runtime_operations_payload(
    state: TeamState,
    *,
    now: datetime | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    agent_ids: set[str] | None = None,
) -> dict[str, Any]:
    effective_now = _ensure_utc(now or utc_now())
    included_agent_ids = set(agent_ids) if agent_ids is not None else None
    session_health = team_session_health_payload(
        state,
        session_records=session_records,
        agent_execution_records=agent_execution_records,
        now=effective_now,
        agent_ids=included_agent_ids,
    )
    session_health_by_agent = {
        row["agent_id"]: row for row in session_health if row.get("agent_id")
    }
    agents = [
        _runtime_agent_payload(
            state,
            agent,
            effective_now,
            session_health=session_health_by_agent.get(agent.id),
        )
        for agent in sorted(state.agents.values(), key=lambda item: item.id)
        if included_agent_ids is None or agent.id in included_agent_ids
    ]
    attention = _runtime_attention_payload(
        state,
        session_health=session_health,
        agent_ids=included_agent_ids,
        now=effective_now,
    )
    return {
        "generated_at": effective_now.isoformat(),
        "agents": agents,
        "attention": attention,
        "session_health": session_health,
    }


def _runtime_agent_payload(
    state: TeamState,
    agent: PersistentAgent,
    now: datetime,
    *,
    session_health: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = state.agent_runtimes.get(agent.id)
    wake_leased_until = runtime.wake_leased_until if runtime is not None else None
    next_wake_at = runtime.next_wake_at if runtime is not None else None
    lease_active = runtime is not None and (
        runtime.wake_leased_until is not None and _ensure_utc(runtime.wake_leased_until) > now
    )
    effective_stand_downs = _effective_stand_down_payloads(state, agent.id)
    wake_due = _runtime_agent_wake_due(
        state,
        agent,
        runtime=runtime,
        now=now,
        effective_stand_downs=effective_stand_downs,
    )
    detail_parts = [f"session {agent.session_id}", f"status {agent.status.value}"]
    project_ids = agent_project_ids(state, agent.id)
    if project_ids:
        detail_parts.append("projects " + ", ".join(project_ids))
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None:
        detail_parts.append(f"role {role.name}")
    session_is_expired = bool(session_health and session_health.get("is_expired"))
    if session_is_expired:
        expires_at = _optional_text(session_health.get("expires_at")) or "unknown time"
        detail_parts.append(f"session expired at {expires_at}")
    if runtime is None:
        detail_parts.append("no runtime snapshot")
    elif runtime.wake_reason is not None:
        detail_parts.append(f"wake reason {runtime.wake_reason}")
    if effective_stand_downs:
        detail_parts.append("stood down")
    return {
        "id": agent.id,
        "kind": "agent_runtime",
        "severity": (
            "critical"
            if session_is_expired
            else "warning"
            if wake_due or lease_active or effective_stand_downs
            else "info"
        ),
        "title": agent.id,
        "detail": "; ".join(detail_parts),
        "team_id": agent.team_id,
        "project_ids": project_ids,
        "lead_project_ids": agent_lead_project_ids(state, agent.id),
        "project_assignments": agent_project_assignment_payloads(state, agent.id),
        "role_id": agent.role_id,
        "role_name": role.name if role is not None else None,
        "role_permissions": [
            permission.value for permission in effective_role_permissions(state, agent)
        ],
        "effective_role_permissions": [
            permission.value for permission in effective_role_permissions(state, agent)
        ],
        "session_id": agent.session_id,
        "session_present": (
            session_health.get("session_present") if session_health is not None else None
        ),
        "session_is_expired": (
            session_health.get("is_expired") if session_health is not None else None
        ),
        "session_expires_at": (
            session_health.get("expires_at") if session_health is not None else None
        ),
        "session_status": (session_health.get("status") if session_health is not None else None),
        "status": agent.status.value,
        "stand_down_active": bool(effective_stand_downs),
        "effective_stand_downs": effective_stand_downs,
        "last_wake_at": _json_datetime(runtime.last_wake_at if runtime is not None else None),
        "next_wake_at": _json_datetime(next_wake_at),
        "wake_leased_until": _json_datetime(wake_leased_until),
        "wake_reason": runtime.wake_reason if runtime is not None else None,
        "active_dispatch_id": runtime.active_dispatch_id if runtime is not None else None,
        "no_progress_count": runtime.no_progress_count if runtime is not None else 0,
        "low_priority_batch_started_at": _json_datetime(
            runtime.low_priority_batch_started_at if runtime is not None else None
        ),
        "low_priority_batch_cycle_count": (
            runtime.low_priority_batch_cycle_count if runtime is not None else 0
        ),
        "low_priority_batch_due_after_cycles": (
            runtime.low_priority_batch_due_after_cycles if runtime is not None else None
        ),
        "context_clearing_policy": (
            runtime.context_clearing_policy.value
            if runtime is not None
            else AgentContextClearingPolicy.OFF.value
        ),
        "context_clearing_idle_grace_seconds": (
            runtime.context_clearing_idle_grace_seconds if runtime is not None else 0
        ),
        "context_idle_since_at": _json_datetime(
            runtime.context_idle_since_at if runtime is not None else None
        ),
        "context_idle_rotation_due_at": _json_datetime(
            runtime.context_idle_rotation_due_at if runtime is not None else None
        ),
        "auto_context_clearing_enabled": (
            runtime.auto_context_clearing_enabled if runtime is not None else False
        ),
        "context_rotation_pending": (
            runtime.context_rotation_pending if runtime is not None else False
        ),
        "context_rotation_reason": (
            runtime.context_rotation_reason if runtime is not None else None
        ),
        "context_rotation_requested_by": (
            runtime.context_rotation_requested_by if runtime is not None else None
        ),
        "context_rotation_requested_at": _json_datetime(
            runtime.context_rotation_requested_at if runtime is not None else None
        ),
        "last_handoff_summary": (runtime.last_handoff_summary if runtime is not None else None),
        "context_handoff_preparation_pending": (
            runtime.context_handoff_preparation_pending if runtime is not None else False
        ),
        "context_handoff_preparation_status": (
            runtime.context_handoff_preparation_status if runtime is not None else None
        ),
        "last_agent_handoff_summary": (
            runtime.last_agent_handoff_summary if runtime is not None else None
        ),
        "last_agent_handoff_at": _json_datetime(
            runtime.last_agent_handoff_at if runtime is not None else None
        ),
        "last_context_rotated_at": _json_datetime(
            runtime.last_context_rotated_at if runtime is not None else None
        ),
        "last_thread_id": runtime.last_thread_id if runtime is not None else None,
        "wake_due": wake_due,
        "lease_active": lease_active,
    }


def _runtime_agent_wake_due(
    state: TeamState,
    agent: PersistentAgent,
    *,
    runtime: AgentRuntime | None,
    now: datetime,
    effective_stand_downs: list[dict[str, Any]],
) -> bool:
    if runtime is None or agent.status != AgentStatus.ACTIVE or effective_stand_downs:
        return False
    pending_inbox = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent.id and item.state == TeamInboxItemState.PENDING
    ]
    wake_inbox = [item for item in pending_inbox if _runtime_inbox_item_can_wake(item)]
    immediate_inbox = [item for item in wake_inbox if item.priority != TeamInboxPriority.LOW]
    low_priority_inbox = [item for item in wake_inbox if item.priority == TeamInboxPriority.LOW]
    pending_reviews = [
        review
        for review in state.review_requests.values()
        if review.reviewer_agent_id == agent.id
        and review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
    ]
    pending_reviews.sort(
        key=lambda item: (
            item.created_at or datetime.min.replace(tzinfo=UTC),
            item.id,
        )
    )
    blocked_review_ids = _runtime_mcp_capability_blocked_review_ids(
        state,
        agent_id=agent.id,
    )
    if blocked_review_ids:
        pending_inbox = [
            item
            for item in pending_inbox
            if not _runtime_inbox_references_review_ids(item, blocked_review_ids)
        ]
        wake_inbox = [item for item in pending_inbox if _runtime_inbox_item_can_wake(item)]
        immediate_inbox = [item for item in wake_inbox if item.priority != TeamInboxPriority.LOW]
        low_priority_inbox = [item for item in wake_inbox if item.priority == TeamInboxPriority.LOW]
        pending_reviews = [
            review for review in pending_reviews if review.id not in blocked_review_ids
        ]
    action_feedback = _latest_undelivered_action_feedback_for_agent(state, agent.id)
    due_blockers = _runtime_blockers_due_for_agent(
        state,
        agent_id=agent.id,
        now=now,
    )
    rotation_pending = runtime.context_rotation_pending
    active_tasks = [
        task
        for task in _active_tasks_for_runtime_agent(state, agent.id)
        if _runtime_task_can_wake_owner_for_work(state, task)
    ]
    task_work_ready = any(
        state.dependency_status(task.id) == "satisfied" for task in active_tasks
    ) or any(state.subtask_counts(task.id)["open"] > 0 for task in active_tasks)
    wake_eligible = (
        rotation_pending
        or bool(wake_inbox)
        or bool(pending_reviews)
        or bool(action_feedback)
        or bool(due_blockers)
        or task_work_ready
    )
    if not wake_eligible:
        return False
    no_progress_exhausted = (
        _DEFAULT_MAX_NO_PROGRESS_COUNT >= 0
        and runtime.no_progress_count >= _DEFAULT_MAX_NO_PROGRESS_COUNT
    )
    if (
        no_progress_exhausted
        and not rotation_pending
        and not action_feedback
        and not due_blockers
        and not wake_work_changed_since_latest_attempt(
            state,
            agent_id=agent.id,
            inbox_items=wake_inbox,
            reviews=pending_reviews,
        )
    ):
        return False
    due_by_time = runtime.next_wake_at is None or _ensure_utc(runtime.next_wake_at) <= now
    if rotation_pending or immediate_inbox or pending_reviews or action_feedback or due_blockers:
        return True
    if task_work_ready and due_by_time:
        if no_progress_exhausted:
            return False
        return True
    if low_priority_inbox:
        return _runtime_low_priority_batch_due(runtime)
    return False


def _runtime_mcp_capability_blocked_review_ids(
    state: TeamState,
    *,
    agent_id: str,
) -> set[str]:
    blocked_review_ids: set[str] = set()
    for blocker in state.blockers.values():
        if (
            blocker.state != TeamBlockerState.OPEN
            or blocker.source_kind != TeamBlockerSourceKind.MCP_CAPABILITY
        ):
            continue
        identity = blocker.mcp_capability_identity
        if identity is not None:
            if identity.agent_id == agent_id:
                blocked_review_ids.update(identity.review_request_ids)
            continue
        prefix = f"{agent_id}:"
        if blocker.source_id.startswith(prefix):
            blocked_review_ids.update(
                review_id for review_id in blocker.source_id[len(prefix) :].split("|") if review_id
            )
    return blocked_review_ids


def _runtime_inbox_references_review_ids(
    item: AgentInboxItem,
    review_request_ids: set[str],
) -> bool:
    if not review_request_ids:
        return False
    review_id = item.payload.get("review_request_id")
    return isinstance(review_id, str) and review_id.strip() in review_request_ids


def wake_work_fingerprint(
    *,
    inbox_items: Sequence[AgentInboxItem],
    reviews: Sequence[ReviewRequest],
) -> str | None:
    """Return a stable snapshot of the inbox and review work offered on a wake."""

    if not inbox_items and not reviews:
        return None
    payload = wake_work_record_fingerprints(
        inbox_items=inbox_items,
        reviews=reviews,
    )
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def wake_work_record_fingerprints(
    *,
    inbox_items: Sequence[AgentInboxItem],
    reviews: Sequence[ReviewRequest],
) -> dict[str, str]:
    records: dict[str, str] = {}
    for kind, rows in (("inbox", inbox_items), ("review", reviews)):
        for row in rows:
            serialized = json.dumps(
                row.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            records[f"{kind}:{row.id}"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return records


def wake_work_changed_since_latest_attempt(
    state: TeamState,
    *,
    agent_id: str,
    inbox_items: Sequence[AgentInboxItem],
    reviews: Sequence[ReviewRequest],
) -> bool:
    """Whether pending inbox/review work changed since its latest terminal attempt.

    With no prior delivered dispatch, pending work receives one bounded attempt.
    Legacy dispatches without a stored fingerprint still recognize newly added
    or updated work from their durable scope and completion timestamp.
    """

    current_fingerprint = wake_work_fingerprint(
        inbox_items=inbox_items,
        reviews=reviews,
    )
    if current_fingerprint is None:
        return False
    terminal_statuses = {
        TeamDispatchStatus.COMPLETED,
        TeamDispatchStatus.EXPIRED,
        TeamDispatchStatus.FAILED,
    }
    terminal_dispatches = [
        dispatch
        for dispatch in state.dispatches.values()
        if dispatch.agent_id == agent_id
        and dispatch.cause_kind != "manager_fallback"
        and dispatch.status in terminal_statuses
        and dispatch.completed_at is not None
    ]
    if not terminal_dispatches:
        return True
    wake_attempts = [
        dispatch
        for dispatch in terminal_dispatches
        if (dispatch.leased_at is not None or dispatch.activation_completed_at is not None)
        and (
            dispatch.wake_work_record_fingerprints
            or dispatch.wake_work_fingerprint is not None
            or (
                dispatch.cause_kind not in {"blocker", "context_handoff"}
                and (dispatch.inbox_item_ids or dispatch.review_request_ids)
            )
        )
    ]
    if not wake_attempts:
        return True
    previous = max(
        wake_attempts,
        key=lambda dispatch: (
            _ensure_utc(dispatch.leased_at or dispatch.activation_completed_at),
            dispatch.id,
        ),
    )
    if previous.wake_work_record_fingerprints:
        current_records = wake_work_record_fingerprints(
            inbox_items=inbox_items,
            reviews=reviews,
        )
        if set(current_records) - set(previous.wake_work_record_fingerprints):
            return True
        return any(
            previous.wake_work_record_fingerprints.get(record_id) != fingerprint
            for record_id, fingerprint in current_records.items()
        )
    if previous.wake_work_fingerprint == current_fingerprint:
        return False

    current_inbox_ids = {item.id for item in inbox_items}
    current_review_ids = {review.id for review in reviews}
    if current_inbox_ids - set(previous.inbox_item_ids):
        return True
    if current_review_ids - set(previous.review_request_ids):
        return True
    completed_at = _ensure_utc(previous.completed_at)
    if any(_ensure_utc(item.updated_at) > completed_at for item in inbox_items):
        return True
    return any(
        review.created_at is not None and _ensure_utc(review.created_at) > completed_at
        for review in reviews
    )


def _runtime_blockers_due_for_agent(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> list[TeamBlocker]:
    rows: list[TeamBlocker] = []
    for blocker in state.blockers.values():
        if blocker.state != TeamBlockerState.OPEN:
            continue
        assigned = (
            blocker.route == TeamBlockerRoute.DELEGATED and blocker.delegate_agent_id == agent_id
        ) or (
            blocker.route in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
            and blocker.owner_agent_id == agent_id
        )
        if not assigned:
            continue
        has_pending_inbox = any(
            item.recipient_agent_id == agent_id
            and item.state == TeamInboxItemState.PENDING
            and item.payload.get("blocker_id") == blocker.id
            for item in state.inbox.values()
        )
        follow_up_due = blocker.follow_up_at is None or _ensure_utc(blocker.follow_up_at) <= now
        if has_pending_inbox or follow_up_due:
            rows.append(blocker)
    return rows


def _active_tasks_for_runtime_agent(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    return [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id and task.state not in terminal
    ]


def _runtime_task_can_wake_owner_for_work(state: TeamState, task: AgentTask) -> bool:
    return task.state not in {
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_INPUT,
    } and not _runtime_task_waits_on_active_deliverable_review(state, task)


def _runtime_task_waits_on_active_deliverable_review(
    state: TeamState,
    task: AgentTask,
) -> bool:
    if task.state != TeamTaskState.WAITING_FOR_REVIEW:
        return False
    review_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
        TeamRecordState.ACCEPTED,
    }
    return any(
        (deliverable := state.deliverables.get(deliverable_id)) is not None
        and deliverable.superseded_by_deliverable_id is None
        and deliverable.state in review_states
        for deliverable_id in task.deliverable_ids
    )


def _latest_undelivered_action_feedback_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[AgentActionRecord]:
    records = [record for record in state.action_records.values() if record.agent_id == agent_id]
    if not records:
        return []
    latest_record = max(records, key=lambda record: (record.created_at, record.id))
    if latest_record.success:
        return []
    if latest_record.dismissed_at is not None:
        return []
    if latest_record.feedback_delivered_at is not None:
        return []
    return [
        record
        for record in records
        if record.dispatch_id == latest_record.dispatch_id
        and not record.success
        and record.dismissed_at is None
    ]


def current_failed_action_records(state: TeamState) -> list[AgentActionRecord]:
    latest_records_by_agent: dict[str, AgentActionRecord] = {}
    for record in state.action_records.values():
        current = latest_records_by_agent.get(record.agent_id)
        if current is None or (record.created_at, record.id) > (
            current.created_at,
            current.id,
        ):
            latest_records_by_agent[record.agent_id] = record

    rows: list[AgentActionRecord] = []
    for latest_record in latest_records_by_agent.values():
        if latest_record.success:
            continue
        rows.extend(
            record
            for record in state.action_records.values()
            if record.agent_id == latest_record.agent_id
            and record.dispatch_id == latest_record.dispatch_id
            and not record.success
            and record.dismissed_at is None
        )
    return sorted(rows, key=lambda entry: (entry.created_at, entry.id))


def _runtime_low_priority_batch_due(runtime: AgentRuntime) -> bool:
    if runtime.low_priority_batch_started_at is None:
        return False
    due_after_cycles = (
        runtime.low_priority_batch_due_after_cycles
        if runtime.low_priority_batch_due_after_cycles is not None
        else _DEFAULT_LOW_PRIORITY_WAKE_AFTER_CYCLES
    )
    return runtime.low_priority_batch_cycle_count >= due_after_cycles


def _runtime_inbox_item_can_wake(item: AgentInboxItem) -> bool:
    return item.response_requested or item.priority != TeamInboxPriority.LOW


def team_session_health_payload(
    state: TeamState,
    *,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    agent_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if session_records is None:
        return []
    effective_now = _ensure_utc(now or utc_now())
    rows: list[dict[str, Any]] = []
    for agent in sorted(state.agents.values(), key=lambda item: item.id):
        if agent_ids is not None and agent.id not in agent_ids:
            continue
        session_id = agent.session_id
        session = session_records.get(session_id) if session_id else None
        execution_record = (
            agent_execution_records.get(session_id)
            if agent_execution_records is not None and session_id
            else None
        )
        execution_host = (
            getattr(execution_record, "host", {}) if execution_record is not None else {}
        )
        if not isinstance(execution_host, Mapping):
            execution_host = {}
        active_execution_count = execution_host.get("active_execution_count")
        if not isinstance(active_execution_count, int):
            active_execution_count = None
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        vm_provider = _optional_text(getattr(session, "vm_provider", None))
        execution_backend = _optional_text(metadata.get("execution_backend")) or vm_provider
        container_lifecycle = metadata.get("container_lifecycle")
        if not isinstance(container_lifecycle, Mapping):
            container_lifecycle = {}
        container_lifecycle_state = _optional_text(container_lifecycle.get("state"))
        expires_at_value = getattr(session, "expires_at", None) if session is not None else None
        expires_at = (
            _ensure_utc(expires_at_value) if isinstance(expires_at_value, datetime) else None
        )
        is_expired = expires_at is not None and expires_at <= effective_now
        status = (
            "expired"
            if is_expired
            else "active"
            if expires_at is not None
            else "missing"
            if session is None
            else "unknown"
        )
        row = {
            "agent_id": agent.id,
            "team_id": agent.team_id,
            "project_ids": agent_project_ids(state, agent.id),
            "lead_project_ids": agent_lead_project_ids(state, agent.id),
            "role_id": agent.role_id,
            "session_id": session_id,
            "session_present": session is not None,
            "expires_at": _json_datetime(expires_at),
            "is_expired": is_expired,
            "status": status,
        }
        has_container_metadata = bool(
            session is not None
            and (
                vm_provider == "container_worker_vm"
                or execution_backend == "container_worker_vm"
                or container_lifecycle_state is not None
            )
        )
        if has_container_metadata or execution_record is not None:
            if vm_provider is not None:
                row["vm_provider"] = vm_provider
            if execution_backend is not None:
                row["execution_backend"] = execution_backend
            if container_lifecycle_state is not None:
                row["container_lifecycle_state"] = container_lifecycle_state
        if execution_record is not None:
            agent_execution_backend = _optional_text(getattr(execution_record, "backend", None))
            if agent_execution_backend is not None:
                row["agent_execution_backend"] = agent_execution_backend
            agent_execution_status = _optional_text(getattr(execution_record, "status", None))
            if agent_execution_status is not None:
                row["agent_execution_status"] = agent_execution_status
            if active_execution_count is not None:
                row["agent_active_execution_count"] = active_execution_count
            agent_latest_execution_id = _optional_text(execution_host.get("latest_execution_id"))
            if agent_latest_execution_id is not None:
                row["agent_latest_execution_id"] = agent_latest_execution_id
        rows.append(row)
    return rows


def _runtime_attention_payload(
    state: TeamState,
    *,
    session_health: list[dict[str, Any]] | None = None,
    agent_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    effective_now = _ensure_utc(now or utc_now())
    rows: list[dict[str, Any]] = []
    rows.extend(_expired_session_rows(state, session_health or []))
    rows.extend(_blocker_rows(state))
    rows.extend(_blocked_task_rows(state))
    rows.extend(_waiting_dependency_rows(state))
    rows.extend(_pending_inbox_rows(state))
    rows.extend(_failed_action_rows(state))
    rows.extend(_failed_promotion_rows(state))
    rows.extend(_blocked_release_rows(state))
    rows.extend(_ephemeral_assignment_rows(state))
    rows = [
        _runtime_attention_metadata(
            state,
            row,
            now=effective_now,
            session_health=session_health or [],
        )
        for row in rows
    ]
    if agent_ids is not None:
        rows = [row for row in rows if _runtime_attention_row_in_agent_scope(row, agent_ids)]
    rows.sort(key=_runtime_attention_sort_key)
    return rows


def _runtime_attention_row_in_agent_scope(row: Mapping[str, Any], agent_ids: set[str]) -> bool:
    agent_id = _optional_text(row.get("agent_id"))
    return agent_id is not None and agent_id in agent_ids


def _expired_session_rows(
    state: TeamState,
    session_health: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for health in session_health:
        if not bool(health.get("is_expired")):
            continue
        agent_id = _optional_text(health.get("agent_id"))
        if agent_id is None:
            continue
        agent = state.agents.get(agent_id)
        project_ids = _text_list(health.get("project_ids") or [], "project_ids")
        session_id = _optional_text(health.get("session_id")) or (
            agent.session_id if agent is not None else None
        )
        expires_at = _optional_text(health.get("expires_at")) or "unknown time"
        rows.append(
            _runtime_row(
                id=f"{agent_id}:expired-session",
                kind="expired_agent_session",
                severity="critical",
                title=f"Expired session for {agent_id}",
                detail=f"agent {agent_id}; session {session_id or 'unknown'} expired at {expires_at}",
                state="expired",
                team_id=health.get("team_id") or (agent.team_id if agent is not None else None),
                agent_id=agent_id,
                project_id=project_ids[0] if len(project_ids) == 1 else None,
                project_ids=project_ids,
                session_id=session_id,
                session_expires_at=health.get("expires_at"),
                occurred_at=health.get("expires_at"),
            )
        )
    return rows


def _blocker_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for blocker in sorted(state.blockers.values(), key=lambda item: (item.created_at, item.id)):
        if blocker.state != TeamBlockerState.OPEN:
            continue
        deliverable_id: str | None = None
        review_request_id: str | None = None
        release_package_id: str | None = None
        if blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
            deliverable_id = blocker.source_id
        elif blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
            review_request_id = blocker.source_id
            review = state.review_requests.get(blocker.source_id)
            if review is not None:
                deliverable_id = review.deliverable_id
                release_package_id = review.release_package_id
        elif blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE:
            release_package_id = blocker.source_id
        elif (
            blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
            and blocker.source_id.startswith("candidate-correction-successor:")
        ):
            release_package_id = blocker.source_id.removeprefix("candidate-correction-successor:")
        detail_parts = [
            f"source {blocker.source_kind.value} {blocker.source_id}",
            f"route {blocker.route.value}",
        ]
        if blocker.owner_agent_id is not None:
            detail_parts.append(f"owner {blocker.owner_agent_id}")
        if blocker.delegate_agent_id is not None:
            detail_parts.append(f"delegate {blocker.delegate_agent_id}")
        detail_parts.append(f"level {blocker.escalation_depth}")
        if blocker.details:
            detail_parts.append(blocker.details)
        rows.append(
            _runtime_row(
                id=blocker.id,
                kind="team_blocker",
                severity=(
                    "critical"
                    if blocker.route
                    in {
                        TeamBlockerRoute.GOVERNANCE,
                        TeamBlockerRoute.OPERATOR,
                        TeamBlockerRoute.ROUTING_FAILED,
                    }
                    or blocker.priority == TeamInboxPriority.HIGH
                    else "warning"
                ),
                title=blocker.summary,
                detail="; ".join(detail_parts),
                state=blocker.state.value,
                team_id=blocker.team_id,
                agent_id=blocker.owner_agent_id,
                project_id=blocker.project_id,
                task_id=blocker.task_id,
                deliverable_id=deliverable_id,
                review_request_id=review_request_id,
                release_package_id=release_package_id,
                blocker_id=blocker.id,
                source_kind=blocker.source_kind.value,
                source_id=blocker.source_id,
                owner_agent_id=blocker.owner_agent_id,
                delegate_agent_id=blocker.delegate_agent_id,
                route=blocker.route.value,
                escalation_depth=blocker.escalation_depth,
                active_dispatch_id=blocker.active_dispatch_id,
                follow_up_at=_json_datetime(blocker.follow_up_at),
                occurred_at=_json_datetime(blocker.created_at),
            )
        )
    return rows


def _blocked_task_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in sorted(state.tasks.values(), key=lambda item: item.id):
        dependency_status = state.dependency_status(task.id)
        if task.state != TeamTaskState.BLOCKED and dependency_status != "blocked":
            continue
        rows.append(
            _runtime_row(
                id=task.id,
                kind="blocked_task",
                severity="warning",
                title=task.title,
                detail=_task_attention_detail(task, dependency_status=dependency_status),
                state=task.state.value,
                agent_id=task.owner_agent_id,
                project_id=task.project_id,
                task_id=task.id,
                deliverable_id=_first_deliverable_id(state, task.id),
                dependency_status=dependency_status,
                blocked_by_task_ids=state.blocked_by_task_ids(task.id),
                occurred_at=_json_datetime(task.state_entered_at),
            )
        )
    return rows


def _waiting_dependency_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in sorted(state.tasks.values(), key=lambda item: item.id):
        dependency_status = state.dependency_status(task.id)
        if dependency_status != "waiting" or task.state == TeamTaskState.BLOCKED:
            continue
        rows.append(
            _runtime_row(
                id=task.id,
                kind="waiting_dependency_task",
                severity="warning",
                title=task.title,
                detail=_task_attention_detail(task, dependency_status=dependency_status),
                state=task.state.value,
                agent_id=task.owner_agent_id,
                project_id=task.project_id,
                task_id=task.id,
                deliverable_id=_first_deliverable_id(state, task.id),
                dependency_status=dependency_status,
                blocked_by_task_ids=state.blocked_by_task_ids(task.id),
                occurred_at=_json_datetime(task.state_entered_at),
            )
        )
    return rows


def _pending_inbox_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(state.inbox.values(), key=lambda entry: (entry.created_at, entry.id)):
        if item.state != TeamInboxItemState.PENDING:
            continue
        title = _inbox_title(item)
        detail = _inbox_detail(state, item)
        kind = (
            "pending_escalation"
            if item.item_type == TeamInboxItemType.ESCALATION
            else "pending_inbox_item"
        )
        rows.append(
            _runtime_row(
                id=item.id,
                kind=kind,
                severity="warning",
                title=title,
                detail=detail,
                state=item.state.value,
                agent_id=item.recipient_agent_id,
                project_id=_project_id_for_task(state, item.task_id),
                task_id=item.task_id,
                inbox_item_id=item.id,
                sender_agent_id=item.sender_agent_id,
                item_type=item.item_type.value,
                response_requested=item.response_requested,
                priority=item.priority.value,
                occurred_at=_json_datetime(item.created_at),
                links=_runtime_attention_payload_links(item.payload),
            )
        )
    return rows


def _failed_action_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in current_failed_action_records(state):
        rows.append(
            _runtime_row(
                id=record.id,
                kind="failed_action_record",
                severity="critical",
                title=f"Failed action {record.id}",
                detail=_action_record_detail(record),
                state="failed",
                agent_id=record.agent_id,
                dispatch_id=record.dispatch_id,
                action_record_id=record.id,
                error=record.error,
                occurred_at=_json_datetime(record.created_at),
                links=_runtime_attention_payload_links(record.action),
            )
        )
    return rows


def _failed_promotion_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for promotion in sorted(
        state.git_promotions.values(),
        key=lambda entry: (entry.created_at, entry.id),
    ):
        if promotion.state != "failed":
            continue
        if promotion.release_package_id is not None:
            release = state.release_packages.get(promotion.release_package_id)
            if release is not None and release.state in {
                TeamRecordState.ACCEPTED,
                TeamRecordState.REJECTED,
            }:
                continue
        if promotion.deliverable_id is not None:
            deliverable = state.deliverables.get(promotion.deliverable_id)
            if deliverable is not None and deliverable.state in {
                TeamRecordState.ACCEPTED,
                TeamRecordState.REJECTED,
            }:
                continue
        if promotion.task_id is not None:
            task = state.tasks.get(promotion.task_id)
            if task is not None and task.state in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.REJECTED,
                TeamTaskState.CANCELLED,
            }:
                continue
        rows.append(
            _runtime_row(
                id=promotion.id,
                kind="failed_git_promotion",
                severity="critical",
                title=f"Failed {promotion.promotion_kind} promotion {promotion.id}",
                detail=_promotion_detail(promotion),
                state=promotion.state,
                agent_id=promotion.actor_agent_id,
                project_id=promotion.project_id,
                task_id=promotion.task_id,
                deliverable_id=promotion.deliverable_id,
                promotion_id=promotion.id,
                release_package_id=promotion.release_package_id,
                promotion_kind=promotion.promotion_kind,
                source_branch=promotion.source_branch,
                target_branch=promotion.target_branch,
                commit_sha=promotion.commit_sha,
                error=promotion.error,
                occurred_at=_json_datetime(promotion.updated_at),
            )
        )
    return rows


def _blocked_release_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for release in sorted(
        state.release_packages.values(), key=lambda entry: (entry.created_at, entry.id)
    ):
        if release.state in {
            TeamRecordState.ACCEPTED,
            TeamRecordState.REJECTED,
        }:
            continue
        if release.state != TeamRecordState.BLOCKED and not release.error:
            continue
        next_action_hint = _release_next_action_hint(state, release)
        rows.append(
            _runtime_row(
                id=release.id,
                kind="blocked_release_package",
                severity="critical" if release.error else "warning",
                title=f"Release {release.id} blocked",
                detail=_release_detail(release),
                state=release.state.value,
                project_id=release.project_id,
                release_package_id=release.id,
                deliverable_ids=list(release.deliverable_ids),
                artifact_refs=list(release.artifact_refs),
                error=release.error,
                next_action="resolve_blocked_release" if next_action_hint else None,
                next_action_hint=next_action_hint,
                occurred_at=_json_datetime(release.updated_at or release.created_at),
            )
        )
    return rows


def _ephemeral_assignment_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assignment in sorted(
        state.ephemeral_assignments.values(),
        key=lambda entry: (entry.created_at, entry.id),
    ):
        if assignment.state not in {
            EphemeralAssignmentState.FAILED,
            EphemeralAssignmentState.CLEANUP_PENDING,
        }:
            continue
        kind = (
            "failed_ephemeral_assignment"
            if assignment.state == EphemeralAssignmentState.FAILED
            else "cleanup_pending_ephemeral_assignment"
        )
        rows.append(
            _runtime_row(
                id=assignment.id,
                kind=kind,
                severity="critical"
                if assignment.state == EphemeralAssignmentState.FAILED
                else "warning",
                title=f"Ephemeral assignment {assignment.id} {assignment.state.value.replace('_', ' ')}",
                detail=_assignment_detail(assignment),
                state=assignment.state.value,
                agent_id=assignment.parent_agent_id,
                project_id=assignment.project_id,
                task_id=assignment.task_id,
                assignment_id=assignment.id,
                session_id=assignment.session_id,
                error=assignment.error,
                occurred_at=_json_datetime(assignment.updated_at),
            )
        )
    return rows


def _runtime_row(**row: Any) -> dict[str, Any]:
    base = {
        "id": None,
        "kind": None,
        "severity": None,
        "title": None,
        "detail": None,
        "state": None,
        "error": None,
        "team_id": None,
        "agent_id": None,
        "project_id": None,
        "task_id": None,
        "deliverable_id": None,
        "inbox_item_id": None,
        "assignment_id": None,
        "promotion_id": None,
        "release_package_id": None,
        "review_request_id": None,
        "blocker_id": None,
        "source_kind": None,
        "source_id": None,
        "owner_agent_id": None,
        "delegate_agent_id": None,
        "route": None,
        "escalation_depth": None,
        "active_dispatch_id": None,
        "follow_up_at": None,
        "dispatch_id": None,
        "session_id": None,
        "project_ids": [],
        "session_expires_at": None,
        "occurred_at": None,
        "age_seconds": 0,
        "age_bucket": "lt_1h",
        "attention_group": "active_obligation",
        "attention_reason": None,
        "terminal_context": [],
        "reconciliation_required": False,
        "links": {},
    }
    base.update(row)
    return base


_RUNTIME_ATTENTION_LINK_KEYS = {
    "team_id": "team_ids",
    "team_ids": "team_ids",
    "project_id": "project_ids",
    "project_ids": "project_ids",
    "agent_id": "agent_ids",
    "agent_ids": "agent_ids",
    "recipient_agent_id": "agent_ids",
    "sender_agent_id": "agent_ids",
    "owner_agent_id": "agent_ids",
    "delegate_agent_id": "agent_ids",
    "actor_agent_id": "agent_ids",
    "producer_agent_id": "agent_ids",
    "reviewer_agent_id": "agent_ids",
    "parent_agent_id": "agent_ids",
    "task_id": "task_ids",
    "task_ids": "task_ids",
    "parent_task_id": "task_ids",
    "child_task_id": "task_ids",
    "child_task_ids": "task_ids",
    "affected_task_ids": "task_ids",
    "deliverable_id": "deliverable_ids",
    "deliverable_ids": "deliverable_ids",
    "release_package_id": "release_package_ids",
    "release_package_ids": "release_package_ids",
    "review_request_id": "review_request_ids",
    "review_request_ids": "review_request_ids",
    "blocker_id": "blocker_ids",
    "blocker_ids": "blocker_ids",
    "dispatch_id": "dispatch_ids",
    "dispatch_ids": "dispatch_ids",
    "active_dispatch_id": "dispatch_ids",
    "session_id": "session_ids",
    "session_ids": "session_ids",
    "requesting_session_id": "session_ids",
    "assignment_id": "assignment_ids",
    "assignment_ids": "assignment_ids",
    "promotion_id": "promotion_ids",
    "promotion_ids": "promotion_ids",
    "inbox_item_id": "inbox_item_ids",
    "inbox_item_ids": "inbox_item_ids",
}
_RUNTIME_ATTENTION_FAILED_KINDS = {
    "failed_action_record",
    "failed_git_promotion",
    "failed_ephemeral_assignment",
}
_RUNTIME_ATTENTION_GROUP_RANK = {
    "active_obligation": 0,
    "failed_attempt": 1,
    "retained_history": 2,
}


def _runtime_attention_payload_links(payload: object) -> dict[str, list[str]]:
    """Extract only well-known durable IDs from nested action/inbox payloads."""

    links: dict[str, set[str]] = {}
    pending: list[tuple[object, int]] = [(payload, 0)]
    visited = 0
    while pending and visited < 1000:
        value, depth = pending.pop()
        visited += 1
        if depth > 8:
            continue
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key)
                target = _RUNTIME_ATTENTION_LINK_KEYS.get(key)
                if target is not None:
                    values = (
                        nested
                        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes))
                        else [nested]
                    )
                    for candidate in values:
                        if isinstance(candidate, Mapping) or (
                            isinstance(candidate, Sequence)
                            and not isinstance(candidate, (str, bytes))
                        ):
                            continue
                        text = _optional_text(candidate)
                        if text is not None:
                            links.setdefault(target, set()).add(text)
                if isinstance(nested, Mapping) or (
                    isinstance(nested, Sequence) and not isinstance(nested, (str, bytes))
                ):
                    pending.append((nested, depth + 1))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            pending.extend((nested, depth + 1) for nested in value)
    return {key: sorted(values) for key, values in sorted(links.items()) if values}


def _runtime_attention_metadata(
    state: TeamState,
    row: Mapping[str, Any],
    *,
    now: datetime,
    session_health: list[dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(row)
    occurred_at = _runtime_attention_datetime(enriched.get("occurred_at")) or now
    age_seconds = max(0, int((now - occurred_at).total_seconds()))
    links = _runtime_attention_links(state, enriched)
    terminal_context = _runtime_attention_terminal_context(
        state,
        links,
        session_health=session_health,
    )
    kind = _optional_text(enriched.get("kind")) or "attention"
    response_requested = bool(enriched.get("response_requested"))
    primary_target = _runtime_attention_primary_target(state, enriched)
    if primary_target is None:
        reconciliation_context = [
            context
            for context in terminal_context
            if context["kind"] in {"task", "deliverable", "release_package", "dispatch", "session"}
        ]
    else:
        reconciliation_context = [
            context
            for context in terminal_context
            if (context["kind"], context["id"]) == primary_target
        ]
    reconciliation_required = bool(
        kind in {"pending_inbox_item", "pending_escalation"} and reconciliation_context
    )
    if kind in _RUNTIME_ATTENTION_FAILED_KINDS:
        attention_group = "failed_attempt"
    elif kind in {"pending_inbox_item", "pending_escalation"} and (
        reconciliation_required or not response_requested
    ):
        attention_group = "retained_history"
    else:
        attention_group = "active_obligation"

    enriched.update(
        {
            "occurred_at": occurred_at.isoformat(),
            "age_seconds": age_seconds,
            "age_bucket": _runtime_attention_age_bucket(age_seconds),
            "attention_group": attention_group,
            "attention_reason": _runtime_attention_reason(
                kind,
                response_requested=response_requested,
                reconciliation_required=reconciliation_required,
            ),
            "terminal_context": terminal_context,
            "reconciliation_required": reconciliation_required,
            "links": links,
        }
    )

    project_ids = links.get("project_ids", [])
    team_ids = links.get("team_ids", [])
    enriched["project_ids"] = project_ids
    if _optional_text(enriched.get("project_id")) is None and len(project_ids) == 1:
        enriched["project_id"] = project_ids[0]
    if _optional_text(enriched.get("team_id")) is None and len(team_ids) == 1:
        enriched["team_id"] = team_ids[0]
    return enriched


def _runtime_attention_primary_target(
    state: TeamState,
    row: Mapping[str, Any],
) -> tuple[str, str] | None:
    """Return the response target rather than a transitively related record."""

    inbox_id = _optional_text(row.get("inbox_item_id"))
    item = state.inbox.get(inbox_id or "")
    if item is None:
        return None
    payload = item.payload if isinstance(item.payload, Mapping) else {}
    for field, kind in (
        ("blocker_id", "blocker"),
        ("review_request_id", "review_request"),
        ("release_package_id", "release_package"),
        ("deliverable_id", "deliverable"),
        ("task_id", "task"),
        ("dispatch_id", "dispatch"),
        ("session_id", "session"),
    ):
        target_id = _optional_text(payload.get(field))
        if target_id is not None:
            return kind, target_id
    if item.task_id is not None:
        return "task", item.task_id
    return None


def _runtime_attention_links(
    state: TeamState,
    row: Mapping[str, Any],
) -> dict[str, list[str]]:
    scope_team_ids = _runtime_attention_source_team_ids(state, row)
    extracted = _runtime_attention_payload_links(row)
    links: dict[str, set[str]] = {key: set(values) for key, values in extracted.items()}

    def add(key: str, *values: object) -> None:
        for value in values:
            text = _optional_text(value)
            if text is not None:
                links.setdefault(key, set()).add(text)

    # Resolve durable relationships without copying arbitrary payload data into
    # the operator/read-tool projection.
    for _ in range(5):
        previous_size = sum(len(values) for values in links.values())
        for task_id in list(links.get("task_ids", ())):
            task = state.tasks.get(task_id)
            if task is None:
                continue
            add("project_ids", task.project_id)
            add("agent_ids", task.owner_agent_id, task.created_by_agent_id)
            for deliverable_id in task.deliverable_ids:
                add("deliverable_ids", deliverable_id)
        for deliverable_id in list(links.get("deliverable_ids", ())):
            deliverable = state.deliverables.get(deliverable_id)
            if deliverable is None:
                continue
            add("task_ids", deliverable.task_id)
            add("agent_ids", deliverable.producer_agent_id)
            for review_id in deliverable.review_request_ids:
                add("review_request_ids", review_id)
        for review_id in list(links.get("review_request_ids", ())):
            review = state.review_requests.get(review_id)
            if review is None:
                continue
            add("deliverable_ids", review.deliverable_id)
            add("release_package_ids", review.release_package_id)
            add("agent_ids", review.reviewer_agent_id)
        for release_id in list(links.get("release_package_ids", ())):
            release = state.release_packages.get(release_id)
            if release is None:
                continue
            add("project_ids", release.project_id)
            for deliverable_id in release.deliverable_ids:
                add("deliverable_ids", deliverable_id)
        for blocker_id in list(links.get("blocker_ids", ())):
            blocker = state.blockers.get(blocker_id)
            if blocker is None:
                continue
            add("team_ids", blocker.team_id)
            add("project_ids", blocker.project_id)
            add("task_ids", blocker.task_id)
            add(
                "agent_ids",
                blocker.reporter_agent_id,
                blocker.owner_agent_id,
                blocker.delegate_agent_id,
            )
            if blocker.source_kind == TeamBlockerSourceKind.TASK:
                add("task_ids", blocker.source_id)
            elif blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
                add("deliverable_ids", blocker.source_id)
            elif blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
                add("review_request_ids", blocker.source_id)
            elif blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE:
                add("release_package_ids", blocker.source_id)
            elif (
                blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
                and blocker.source_id.startswith("candidate-correction-successor:")
            ):
                add(
                    "release_package_ids",
                    blocker.source_id.removeprefix("candidate-correction-successor:"),
                )
        for promotion_id in list(links.get("promotion_ids", ())):
            promotion = state.git_promotions.get(promotion_id)
            if promotion is None:
                continue
            add("project_ids", promotion.project_id)
            add("agent_ids", promotion.actor_agent_id)
            add("task_ids", promotion.task_id)
            add("deliverable_ids", promotion.deliverable_id)
            add("release_package_ids", promotion.release_package_id)
        for assignment_id in list(links.get("assignment_ids", ())):
            assignment = state.ephemeral_assignments.get(assignment_id)
            if assignment is None:
                continue
            add("project_ids", assignment.project_id)
            add("agent_ids", assignment.parent_agent_id)
            add("task_ids", assignment.task_id)
            add("session_ids", assignment.session_id)
        for inbox_id in list(links.get("inbox_item_ids", ())):
            item = state.inbox.get(inbox_id)
            if item is None:
                continue
            add("agent_ids", item.recipient_agent_id, item.sender_agent_id)
            add("task_ids", item.task_id)
            for key, values in _runtime_attention_payload_links(item.payload).items():
                for value in values:
                    add(key, value)
        for dispatch_id in list(links.get("dispatch_ids", ())):
            dispatch = state.dispatches.get(dispatch_id)
            if dispatch is None:
                continue
            add("agent_ids", dispatch.agent_id)
            add("session_ids", dispatch.session_id)
            for project_id in dispatch.project_ids:
                add("project_ids", project_id)
            for task_id in dispatch.task_ids:
                add("task_ids", task_id)
            for blocker_id in dispatch.blocker_ids:
                add("blocker_ids", blocker_id)
            for review_id in dispatch.review_request_ids:
                add("review_request_ids", review_id)
            for inbox_id in dispatch.inbox_item_ids:
                add("inbox_item_ids", inbox_id)
        if sum(len(values) for values in links.values()) == previous_size:
            break

    if scope_team_ids:
        for key, values in links.items():
            links[key] = {
                value
                for value in values
                if not (
                    linked_team_ids := _runtime_attention_link_team_ids(
                        state,
                        key=key,
                        id=value,
                    )
                )
                or bool(linked_team_ids.intersection(scope_team_ids))
            }
    for agent_id in list(links.get("agent_ids", ())):
        agent = state.agents.get(agent_id)
        if agent is not None:
            add("team_ids", agent.team_id)
    if not links.get("project_ids"):
        for agent_id in list(links.get("agent_ids", ())):
            for project_id in agent_project_ids(state, agent_id):
                add("project_ids", project_id)
    for project_id in list(links.get("project_ids", ())):
        project = state.projects.get(project_id)
        if project is not None:
            add("team_ids", project.team_id)

    return {key: sorted(values) for key, values in sorted(links.items()) if values}


def _runtime_attention_source_team_ids(
    state: TeamState,
    row: Mapping[str, Any],
) -> set[str]:
    team_ids: set[str] = set()
    direct_team_id = _optional_text(row.get("team_id"))
    if direct_team_id is not None:
        team_ids.add(direct_team_id)
    agent_id = _optional_text(row.get("agent_id"))
    if agent_id is not None and (agent := state.agents.get(agent_id)) is not None:
        if agent.team_id is not None:
            team_ids.add(agent.team_id)
    direct_project_ids = [
        project_id
        for value in [row.get("project_id"), *_sequence_values(row.get("project_ids"))]
        if (project_id := _optional_text(value)) is not None
    ]
    for project_id in direct_project_ids:
        project = state.projects.get(project_id)
        if project is not None:
            team_ids.add(project.team_id)
    return team_ids


def _runtime_attention_link_team_ids(
    state: TeamState,
    *,
    key: str,
    id: str,
) -> set[str]:
    if key == "team_ids":
        return {id} if id in state.teams else set()
    if key == "project_ids":
        project = state.projects.get(id)
        return {project.team_id} if project is not None else set()
    if key == "agent_ids":
        agent = state.agents.get(id)
        return {agent.team_id} if agent is not None and agent.team_id is not None else set()
    if key == "task_ids":
        task = state.tasks.get(id)
        if task is not None and (project := state.projects.get(task.project_id)) is not None:
            return {project.team_id}
    elif key == "deliverable_ids":
        deliverable = state.deliverables.get(id)
        if (
            deliverable is not None
            and (task := state.tasks.get(deliverable.task_id)) is not None
            and (project := state.projects.get(task.project_id)) is not None
        ):
            return {project.team_id}
    elif key == "release_package_ids":
        release = state.release_packages.get(id)
        if release is not None and (project := state.projects.get(release.project_id)) is not None:
            return {project.team_id}
    elif key == "review_request_ids":
        review = state.review_requests.get(id)
        if review is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="deliverable_ids",
                id=review.deliverable_id,
            )
    elif key == "blocker_ids":
        blocker = state.blockers.get(id)
        if blocker is not None:
            return {blocker.team_id}
    elif key == "dispatch_ids":
        dispatch = state.dispatches.get(id)
        if dispatch is not None:
            return {
                state.projects[project_id].team_id
                for project_id in dispatch.project_ids
                if project_id in state.projects
            } or _runtime_attention_link_team_ids(
                state,
                key="agent_ids",
                id=dispatch.agent_id,
            )
    elif key == "assignment_ids":
        assignment = state.ephemeral_assignments.get(id)
        if assignment is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="project_ids",
                id=assignment.project_id,
            )
    elif key == "promotion_ids":
        promotion = state.git_promotions.get(id)
        if promotion is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="project_ids",
                id=promotion.project_id,
            )
    elif key == "inbox_item_ids":
        item = state.inbox.get(id)
        if item is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="agent_ids",
                id=item.recipient_agent_id,
            )
    elif key == "session_ids":
        return {
            agent.team_id
            for agent in state.agents.values()
            if agent.session_id == id and agent.team_id is not None
        }
    return set()


def _sequence_values(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _runtime_attention_terminal_context(
    state: TeamState,
    links: Mapping[str, list[str]],
    *,
    session_health: list[dict[str, Any]],
) -> list[dict[str, str]]:
    contexts: dict[tuple[str, str], dict[str, str]] = {}

    def add(kind: str, id: str, state_value: str) -> None:
        contexts[(kind, id)] = {"kind": kind, "id": id, "state": state_value}

    terminal_task_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    for task_id in links.get("task_ids", []):
        task = state.tasks.get(task_id)
        if task is not None and task.state in terminal_task_states:
            add("task", task.id, task.state.value)
    for deliverable_id in links.get("deliverable_ids", []):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        if deliverable.superseded_by_deliverable_id is not None:
            add("deliverable", deliverable.id, "superseded")
        elif deliverable.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}:
            add("deliverable", deliverable.id, deliverable.state.value)
    for release_id in links.get("release_package_ids", []):
        release = state.release_packages.get(release_id)
        if release is not None and release.state in {
            TeamRecordState.ACCEPTED,
            TeamRecordState.REJECTED,
        }:
            add("release_package", release.id, release.state.value)
    for review_id in links.get("review_request_ids", []):
        review = state.review_requests.get(review_id)
        if review is None:
            continue
        if review.retired_at is not None:
            add("review_request", review.id, "retired")
        elif review.superseded_by_review_request_id is not None:
            add("review_request", review.id, "superseded")
        elif review.state in {
            TeamRecordState.ACCEPTED,
            TeamRecordState.REJECTED,
        }:
            add("review_request", review.id, review.state.value)
    for blocker_id in links.get("blocker_ids", []):
        blocker = state.blockers.get(blocker_id)
        if blocker is not None and blocker.state == TeamBlockerState.RESOLVED:
            add("blocker", blocker.id, blocker.state.value)
    for dispatch_id in links.get("dispatch_ids", []):
        dispatch = state.dispatches.get(dispatch_id)
        if dispatch is not None and dispatch.status in {
            TeamDispatchStatus.COMPLETED,
            TeamDispatchStatus.EXPIRED,
            TeamDispatchStatus.FAILED,
        }:
            add("dispatch", dispatch.id, dispatch.status.value)
    health_by_session = {
        session_id: health
        for health in session_health
        if (session_id := _optional_text(health.get("session_id"))) is not None
    }
    for session_id in links.get("session_ids", []):
        health = health_by_session.get(session_id)
        if health is not None and bool(health.get("is_expired")):
            add("session", session_id, "expired")
    terminal_assignment_states = {
        EphemeralAssignmentState.COMPLETED,
        EphemeralAssignmentState.ACCEPTED,
        EphemeralAssignmentState.REJECTED,
        EphemeralAssignmentState.CLEANED_UP,
        EphemeralAssignmentState.FAILED,
    }
    for assignment_id in links.get("assignment_ids", []):
        assignment = state.ephemeral_assignments.get(assignment_id)
        if assignment is not None and assignment.state in terminal_assignment_states:
            add("ephemeral_assignment", assignment.id, assignment.state.value)
    for promotion_id in links.get("promotion_ids", []):
        promotion = state.git_promotions.get(promotion_id)
        if promotion is not None and promotion.state in {"succeeded", "failed"}:
            add("git_promotion", promotion.id, promotion.state)
    for project_id in links.get("project_ids", []):
        project = state.projects.get(project_id)
        if project is not None and project.status in {
            ProjectStatus.COMPLETED,
            ProjectStatus.ARCHIVED,
        }:
            add("project", project.id, project.status.value)
    for agent_id in links.get("agent_ids", []):
        agent = state.agents.get(agent_id)
        if agent is not None and agent.status == AgentStatus.ARCHIVED:
            add("agent", agent.id, agent.status.value)
    return [contexts[key] for key in sorted(contexts)]


def _runtime_attention_reason(
    kind: str,
    *,
    response_requested: bool,
    reconciliation_required: bool,
) -> str:
    if kind in {"pending_inbox_item", "pending_escalation"}:
        if reconciliation_required:
            return "terminal_work_pending_reconciliation"
        if not response_requested:
            return "pending_without_response_obligation"
        return "awaiting_response"
    return {
        "expired_agent_session": "agent_session_expired",
        "team_blocker": "open_blocker",
        "blocked_task": "task_blocked",
        "waiting_dependency_task": "waiting_on_dependency",
        "failed_action_record": "undismissed_failed_attempt",
        "failed_git_promotion": "failed_git_promotion",
        "blocked_release_package": "release_blocked",
        "failed_ephemeral_assignment": "failed_ephemeral_assignment",
        "cleanup_pending_ephemeral_assignment": "cleanup_pending",
    }.get(kind, "attention_required")


def _runtime_attention_age_bucket(age_seconds: int) -> str:
    if age_seconds < 60 * 60:
        return "lt_1h"
    if age_seconds < 24 * 60 * 60:
        return "lt_1d"
    if age_seconds < 7 * 24 * 60 * 60:
        return "lt_7d"
    return "gte_7d"


def _runtime_attention_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _runtime_attention_sort_key(row: Mapping[str, Any]) -> tuple[int, float, int, str, str]:
    occurred_at = _runtime_attention_datetime(row.get("occurred_at"))
    timestamp = occurred_at.timestamp() if occurred_at is not None else 0.0
    return (
        _RUNTIME_ATTENTION_GROUP_RANK.get(str(row.get("attention_group")), 9),
        -timestamp,
        _severity_rank(row.get("severity")),
        str(row.get("kind") or ""),
        str(row.get("id") or ""),
    )


def _severity_rank(severity: object) -> int:
    order = {"critical": 0, "warning": 1, "info": 2}
    return order.get(str(severity), 9)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _ensure_utc(value).isoformat()


def _first_deliverable_id(state: TeamState, task_id: str) -> str | None:
    for deliverable in sorted(state.deliverables.values(), key=lambda item: item.id):
        if deliverable.task_id == task_id:
            return deliverable.id
    return None


def _project_id_for_task(state: TeamState, task_id: str | None) -> str | None:
    if task_id is None:
        return None
    task = state.tasks.get(task_id)
    return task.project_id if task is not None else None


def _task_attention_detail(task: AgentTask, *, dependency_status: str) -> str:
    detail_parts = [f"owner {task.owner_agent_id}", f"state {task.state.value}"]
    if dependency_status != "satisfied":
        detail_parts.append(f"dependencies {dependency_status}")
    blocked_by = ", ".join(task.dependency_task_ids) if task.dependency_task_ids else "none"
    if blocked_by != "none":
        detail_parts.append(f"depends on {blocked_by}")
    return "; ".join(detail_parts)


def _inbox_title(item: AgentInboxItem) -> str:
    label = item.item_type.value.replace("_", " ")
    return label.title()


def _inbox_detail(state: TeamState, item: AgentInboxItem) -> str:
    detail_parts = [f"recipient {item.recipient_agent_id}", f"state {item.state.value}"]
    if item.sender_agent_id is not None:
        detail_parts.append(f"sender {item.sender_agent_id}")
    if item.task_id is not None:
        task = state.tasks.get(item.task_id)
        if task is not None:
            detail_parts.append(f"task {task.id}")
            detail_parts.append(f"project {task.project_id}")
    return "; ".join(detail_parts)


def _action_record_detail(record: AgentActionRecord) -> str:
    action_type = _optional_text(record.action.get("type") or record.action.get("kind"))
    detail_parts = [f"agent {record.agent_id}", f"dispatch {record.dispatch_id}"]
    if action_type is not None:
        detail_parts.append(f"action {action_type}")
    if record.error is not None:
        detail_parts.append(f"error {record.error}")
    return "; ".join(detail_parts)


def _promotion_detail(promotion: GitPromotionRecord) -> str:
    detail_parts = [
        f"actor {promotion.actor_agent_id}",
        f"{promotion.source_branch} -> {promotion.target_branch}",
    ]
    if promotion.error is not None:
        detail_parts.append(f"error {promotion.error}")
    return "; ".join(detail_parts)


def _release_detail(release: ReleasePackage) -> str:
    detail_parts = [f"project {release.project_id}"]
    if release.deliverable_ids:
        detail_parts.append(f"deliverables {', '.join(release.deliverable_ids)}")
    if release.error is not None:
        detail_parts.append(f"error {release.error}")
    return "; ".join(detail_parts)


def _assignment_detail(assignment: EphemeralAssignment) -> str:
    detail_parts = [
        f"parent {assignment.parent_agent_id}",
        f"session {assignment.session_id}",
        f"state {assignment.state.value}",
    ]
    if assignment.error is not None:
        detail_parts.append(f"error {assignment.error}")
    return "; ".join(detail_parts)
