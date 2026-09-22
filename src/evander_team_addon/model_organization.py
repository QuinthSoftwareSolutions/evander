"""Team organization models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_serializer, field_validator, model_validator

from evander.models import DirectoryPath, utc_now
from evander.worker.models import WorkerResourceLimits
from evander_team_addon.git_naming import (
    DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX,
    normalize_canonical_release_branch_prefix,
)

from .model_common import (
    DEFAULT_TEAM_COST_WARNING_RATIO,
    REALTIME_BUILT_IN_VOICE_IDS,
    AgentContextClearingPolicy,
    AgentProjectAssignmentKind,
    AgentStandDownSource,
    AgentStatus,
    GovernanceAgendaItemState,
    GovernanceAgendaItemType,
    ProjectStatus,
    ReleaseGateKind,
    RolePermission,
    TeamCostBudgetRenewalMode,
    TeamCostBudgetWindowKind,
    TeamInboxPriority,
    TeamModel,
    TeamRecordState,
    _ensure_unique,
    _optional_text,
    _required_text,
    _text_list,
    _unique_text_list,
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


class ReleaseGateStage(TeamModel):
    kind: ReleaseGateKind
    enabled: bool = True


def _legacy_release_gate_sequence(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": ReleaseGateKind.VERIFICATION.value,
            "enabled": bool(value.get("verification_commands")),
        },
        {
            "kind": ReleaseGateKind.AGENT_REVIEW.value,
            "enabled": bool(value.get("review_requirements")),
        },
        {
            "kind": ReleaseGateKind.HUMAN_REVIEW.value,
            "enabled": value.get("human_review_requirement") is not None,
        },
    ]


class ProjectReleasePolicy(TeamModel):
    schema_version: Literal[1, 2, 3] = 3
    gate_sequence: list[ReleaseGateStage] = Field(default_factory=list)
    automatic_release_branch_publication: bool = True
    verification_backend: Literal["container_worker_vm"] = "container_worker_vm"
    container_image: str | None = None
    resource_limits: WorkerResourceLimits | None = None
    verification_commands: list[ReleaseVerificationCommand] = Field(default_factory=list)
    review_requirements: list[ReleaseReviewRequirement] = Field(default_factory=list)
    human_review_requirement: HumanReviewRequirement | None = None
    # Retained as a compatibility input. An explicit policy with every stage
    # disabled is now sufficient to authorize an ungated release.
    allow_ungated: bool = False

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_gate_sequence(cls, value: object) -> object:
        if not isinstance(value, Mapping) or "gate_sequence" in value:
            return value
        normalized = dict(value)
        normalized["schema_version"] = 3
        normalized["gate_sequence"] = _legacy_release_gate_sequence(value)
        return normalized

    @field_validator("container_image", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _require_gate(self) -> "ProjectReleasePolicy":
        kinds = [stage.kind for stage in self.gate_sequence]
        expected = set(ReleaseGateKind)
        if len(kinds) != len(expected) or set(kinds) != expected:
            raise ValueError(
                "release policy gate_sequence must contain each gate kind exactly once"
            )
        enabled = {stage.kind for stage in self.gate_sequence if stage.enabled}
        if ReleaseGateKind.VERIFICATION in enabled and not self.verification_commands:
            raise ValueError("enabled verification gate requires verification_commands")
        if ReleaseGateKind.AGENT_REVIEW in enabled and not self.review_requirements:
            raise ValueError("enabled agent_review gate requires review_requirements")
        if ReleaseGateKind.HUMAN_REVIEW in enabled and self.human_review_requirement is None:
            raise ValueError("enabled human_review gate requires human_review_requirement")
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
    # The latest delivered dispatch owns each binding, even while its native
    # thread is unknown. Late completion receipts cannot replace this owner.
    work_lineage_thread_dispatch_ids: dict[str, str] = Field(default_factory=dict)
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

    @field_validator("work_lineage_thread_ids", "work_lineage_thread_dispatch_ids", mode="before")
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
