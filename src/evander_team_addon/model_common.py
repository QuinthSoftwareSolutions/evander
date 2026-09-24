"""Shared Team enums, normalization, and model configuration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

SCHEMA_VERSION = 23


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
    CANCELLED = "cancelled"


class InitialReleaseDispositionState(str, Enum):
    PENDING = "pending"
    RELEASE_REQUESTED = "release_requested"
    NOT_PLANNED = "not_planned"


class HumanReviewFindingTargetScope(str, Enum):
    LEGACY_UNSPECIFIED = "legacy_unspecified"
    RELEASE_CANDIDATE = "release_candidate"
    DELIVERABLES = "deliverables"


class ReleaseGateKind(str, Enum):
    AGENT_REVIEW = "agent_review"
    VERIFICATION = "verification"
    HUMAN_REVIEW = "human_review"


class ReviewBlockKind(str, Enum):
    DEPENDENCY = "dependency"
    INPUT_UNAVAILABLE = "input_unavailable"
    POLICY = "policy"
    SYSTEM = "system"
    UNSPECIFIED = "unspecified"


class TeamTaskKind(str, Enum):
    OUTCOME = "outcome"
    SUPPORT = "support"
    COORDINATION = "coordination"


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


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _ensure_utc(value).isoformat()
