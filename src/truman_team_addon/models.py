from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_sandbox.models import utc_now
from truman_team_addon.git_naming import DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX

SCHEMA_VERSION = 1


class TeamTaskState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    WAITING_FOR_REVIEW = "waiting_for_review"
    FINISHED = "finished"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TeamRecordState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


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


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TeamInboxItemType(str, Enum):
    QUESTION = "question"
    STATUS_REQUEST = "status_request"
    PROGRESS_REPORT = "progress_report"
    DELIVERABLE_NOTICE = "deliverable_notice"
    ESCALATION = "escalation"


class TeamInboxItemState(str, Enum):
    PENDING = "pending"
    HANDLED = "handled"


class TeamModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class EphemeralAgentPolicy(TeamModel):
    max_concurrent: int = Field(default=0, ge=0)


class ProjectGoal(TeamModel):
    id: str
    description: str
    state: TeamRecordState = TeamRecordState.PENDING

    @field_validator("id", "description", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class Team(TeamModel):
    id: str
    name: str
    manager_agent_id: str
    operator_communication_target_id: str | None = None
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

    @field_validator("project_ids", mode="before")
    @classmethod
    def _project_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "project_ids")


class ProjectGitConfig(TeamModel):
    host_repo_path: str
    bare_repo_name: str | None = None
    bare_repo_path: str | None = None
    base_branch: str = "main"
    canonical_origin_url: str | None = None
    provider: str | None = None
    canonical_release_branch_prefix: str = DEFAULT_CANONICAL_RELEASE_BRANCH_PREFIX

    @field_validator("host_repo_path", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("base_branch", "canonical_release_branch_prefix", mode="before")
    @classmethod
    def _required_with_default(cls, value: object, info) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return cls.model_fields[info.field_name].default
        return _required_text(value, info.field_name)

    @field_validator(
        "bare_repo_name",
        "bare_repo_path",
        "canonical_origin_url",
        "provider",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_bare_repo(self) -> "ProjectGitConfig":
        provided = [value for value in (self.bare_repo_name, self.bare_repo_path) if value]
        if len(provided) != 1:
            raise ValueError(
                "exactly one of bare_repo_name or bare_repo_path must be set"
            )
        return self


class Project(TeamModel):
    id: str
    team_id: str
    name: str
    lead_agent_id: str
    goals: list[ProjectGoal] = Field(default_factory=list)
    git: ProjectGitConfig | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "team_id", "name", "lead_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="after")
    def _validate_goal_ids(self) -> "Project":
        _ensure_unique([goal.id for goal in self.goals], "project goals")
        return self


class PersistentAgent(TeamModel):
    id: str
    team_id: str
    project_id: str | None = None
    display_name: str | None = None
    role: str
    session_id: str
    reports_to_agent_id: str | None = None
    subordinate_agent_ids: list[str] = Field(default_factory=list)
    ephemeral_policy: EphemeralAgentPolicy = Field(default_factory=EphemeralAgentPolicy)
    status: AgentStatus = AgentStatus.ACTIVE

    @field_validator("id", "team_id", "role", "session_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("project_id", "display_name", "reports_to_agent_id", mode="before")
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
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("active_dispatch_id", "wake_reason", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class AgentTask(TeamModel):
    id: str
    owner_agent_id: str
    project_id: str
    priority: int = Field(ge=0)
    state: TeamTaskState = TeamTaskState.PENDING
    title: str
    description: str
    created_by_agent_id: str | None = None
    created_by_actor: str | None = None
    parent_task_id: str | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)
    deliverable_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

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

    @field_validator("created_by_agent_id", "created_by_actor", "parent_task_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("dependency_task_ids", mode="before")
    @classmethod
    def _dependency_task_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "dependency_task_ids")

    @field_validator("deliverable_ids", mode="before")
    @classmethod
    def _deliverable_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "deliverable_ids")

    @model_validator(mode="after")
    def _validate_self_links(self) -> "AgentTask":
        if self.created_by_agent_id is None and self.created_by_actor is None:
            raise ValueError("task creator is required")
        if self.parent_task_id == self.id:
            raise ValueError("task cannot be its own parent")
        if self.id in self.dependency_task_ids:
            raise ValueError("task cannot depend on itself")
        return self


class DeliverableGitRefs(TeamModel):
    agent_branch: str | None = None
    review_branch: str | None = None
    feature_branch: str | None = None
    release_branch: str | None = None
    commit_sha: str | None = None
    promotion_record_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "agent_branch",
        "review_branch",
        "feature_branch",
        "release_branch",
        "commit_sha",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("promotion_record_ids", mode="before")
    @classmethod
    def _promotion_record_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "promotion_record_ids")


class Deliverable(TeamModel):
    id: str
    task_id: str
    producer_agent_id: str
    artifact_refs: list[str] = Field(default_factory=list)
    summary: str
    state: TeamRecordState = TeamRecordState.PENDING
    review_request_ids: list[str] = Field(default_factory=list)
    git_refs: DeliverableGitRefs = Field(default_factory=DeliverableGitRefs)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "task_id", "producer_agent_id", "summary", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("artifact_refs", "review_request_ids", mode="before")
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


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
    applicable_roles: list[str] = Field(default_factory=list)
    criteria: list[ReviewProfileCriterion] = Field(default_factory=list)
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

    @field_validator("applicable_roles", mode="before")
    @classmethod
    def _applicable_roles(cls, value: object) -> list[str]:
        return _unique_text_list(value, "applicable_roles")

    @model_validator(mode="after")
    def _validate_criterion_ids(self) -> "ReviewProfile":
        _ensure_unique([criterion.id for criterion in self.criteria], "review profile criteria")
        return self


class ReviewCriterionResult(TeamModel):
    criterion_id: str
    state: Literal["passed", "failed", "skipped"]
    finding: str = ""

    @field_validator("criterion_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class ReviewRequest(TeamModel):
    id: str
    deliverable_id: str
    reviewer_agent_id: str
    state: TeamRecordState = TeamRecordState.PENDING
    findings: list[str] = Field(default_factory=list)
    review_profile_id: str | None = None
    instructions: str | None = None
    criteria: list[ReviewCriterionResult] = Field(default_factory=list)
    created_at: datetime | None = None
    decision_at: datetime | None = None

    @field_validator("id", "deliverable_id", "reviewer_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value: object) -> list[str]:
        return _text_list(value, "findings")

    @field_validator("review_profile_id", "instructions", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class AgentInboxItem(TeamModel):
    id: str
    recipient_agent_id: str
    item_type: TeamInboxItemType
    sender_agent_id: str | None = None
    task_id: str | None = None
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

    @field_validator("id", "agent_id", "dispatch_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("error", "dismissed_by", "dismissed_reason", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class ReleasePackage(TeamModel):
    id: str
    project_id: str
    deliverable_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    feature_branches: list[str] = Field(default_factory=list)
    release_branch: str | None = None
    canonical_branch: str | None = None
    published_commit_sha: str | None = None
    git_promotion_record_ids: list[str] = Field(default_factory=list)
    state: TeamRecordState = TeamRecordState.PENDING
    published_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime | None = None

    @field_validator("id", "project_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "deliverable_ids",
        "artifact_refs",
        "feature_branches",
        "git_promotion_record_ids",
        mode="before",
    )
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @field_validator(
        "release_branch",
        "canonical_branch",
        "published_commit_sha",
        "error",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


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


class GitPromotionRecord(TeamModel):
    id: str
    project_id: str
    actor_agent_id: str
    source_branch: str
    target_branch: str
    promotion_kind: Literal["review", "feature", "release", "canonical_push"]
    task_id: str | None = None
    deliverable_id: str | None = None
    release_package_id: str | None = None
    state: Literal["pending", "succeeded", "failed"] = "pending"
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
        "commit_sha",
        "error",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

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
    projects: dict[str, Project] = Field(default_factory=dict)
    agents: dict[str, PersistentAgent] = Field(default_factory=dict)
    agent_runtimes: dict[str, AgentRuntime] = Field(default_factory=dict)
    tasks: dict[str, AgentTask] = Field(default_factory=dict)
    deliverables: dict[str, Deliverable] = Field(default_factory=dict)
    review_requests: dict[str, ReviewRequest] = Field(default_factory=dict)
    review_profiles: dict[str, ReviewProfile] = Field(default_factory=dict)
    inbox: dict[str, AgentInboxItem] = Field(default_factory=dict)
    action_records: dict[str, AgentActionRecord] = Field(default_factory=dict)
    release_packages: dict[str, ReleasePackage] = Field(default_factory=dict)
    ephemeral_assignments: dict[str, EphemeralAssignment] = Field(default_factory=dict)
    git_promotions: dict[str, GitPromotionRecord] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def empty(cls) -> "TeamState":
        return cls()

    @model_validator(mode="after")
    def _validate_state(self) -> "TeamState":
        errors: list[str] = []
        self._validate_key_matches(errors)
        self._validate_teams(errors)
        self._validate_projects(errors)
        self._validate_agents(errors)
        self._validate_agent_runtimes(errors)
        self._validate_tasks(errors)
        self._validate_task_graph(errors)
        self._validate_deliverables(errors)
        self._validate_review_profiles(errors)
        self._validate_reviews(errors)
        self._validate_inbox(errors)
        self._validate_action_records(errors)
        self._validate_releases(errors)
        self._validate_ephemeral_assignments(errors)
        self._validate_git_promotions(errors)
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
            "projects",
            "agents",
            "agent_runtimes",
            "tasks",
            "deliverables",
            "review_requests",
            "review_profiles",
            "inbox",
            "action_records",
            "release_packages",
            "ephemeral_assignments",
            "git_promotions",
        ):
            collection = getattr(self, collection_name)
            for key, record in collection.items():
                record_id = getattr(record, "id", getattr(record, "agent_id", None))
                if key != record_id:
                    errors.append(
                        f"{collection_name}.{key} key does not match record id {record_id}"
                    )

    def _validate_teams(self, errors: list[str]) -> None:
        for team in self.teams.values():
            manager = self.agents.get(team.manager_agent_id)
            if manager is None:
                errors.append(f"team {team.id} references missing manager {team.manager_agent_id}")
            elif manager.team_id != team.id:
                errors.append(f"team {team.id} manager {manager.id} belongs to team {manager.team_id}")
            for project_id in team.project_ids:
                project = self.projects.get(project_id)
                if project is None:
                    errors.append(f"team {team.id} references missing project {project_id}")
                elif project.team_id != team.id:
                    errors.append(f"team {team.id} project {project_id} belongs to team {project.team_id}")

    def _validate_projects(self, errors: list[str]) -> None:
        for project in self.projects.values():
            if project.team_id not in self.teams:
                errors.append(f"project {project.id} references missing team {project.team_id}")
            lead = self.agents.get(project.lead_agent_id)
            if lead is None:
                errors.append(f"project {project.id} references missing lead {project.lead_agent_id}")
            elif lead.project_id != project.id:
                errors.append(f"project {project.id} lead {lead.id} belongs to project {lead.project_id}")

    def _validate_agents(self, errors: list[str]) -> None:
        for agent in self.agents.values():
            if agent.team_id not in self.teams:
                errors.append(f"agent {agent.id} references missing team {agent.team_id}")
            if agent.project_id is not None:
                project = self.projects.get(agent.project_id)
                if project is None:
                    errors.append(f"agent {agent.id} references missing project {agent.project_id}")
                elif project.team_id != agent.team_id:
                    errors.append(
                        f"agent {agent.id} project {project.id} belongs to team {project.team_id}"
                    )
            if agent.reports_to_agent_id is not None:
                superior = self.agents.get(agent.reports_to_agent_id)
                if superior is None:
                    errors.append(
                        f"agent {agent.id} references missing superior {agent.reports_to_agent_id}"
                    )
                elif superior.team_id != agent.team_id:
                    errors.append(f"agent {agent.id} superior {superior.id} is in another team")
            for subordinate_id in agent.subordinate_agent_ids:
                subordinate = self.agents.get(subordinate_id)
                if subordinate is None:
                    errors.append(f"agent {agent.id} references missing subordinate {subordinate_id}")
                elif subordinate.reports_to_agent_id != agent.id:
                    errors.append(f"agent {agent.id} subordinate {subordinate_id} does not report back")
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

    def _validate_tasks(self, errors: list[str]) -> None:
        for task in self.tasks.values():
            owner = self.agents.get(task.owner_agent_id)
            if owner is None:
                errors.append(f"task {task.id} references missing owner {task.owner_agent_id}")
            project = self.projects.get(task.project_id)
            if project is None:
                errors.append(f"task {task.id} references missing project {task.project_id}")
            elif owner is not None and owner.team_id != project.team_id:
                errors.append(f"task {task.id} owner {owner.id} is outside project team")
            if (
                task.created_by_agent_id is not None
                and task.created_by_agent_id not in self.agents
            ):
                errors.append(f"task {task.id} references missing creator {task.created_by_agent_id}")
            for deliverable_id in task.deliverable_ids:
                deliverable = self.deliverables.get(deliverable_id)
                if deliverable is None:
                    errors.append(f"task {task.id} references missing deliverable {deliverable_id}")
                elif deliverable.task_id != task.id:
                    errors.append(f"task {task.id} deliverable {deliverable_id} does not point back")

    def _validate_task_graph(self, errors: list[str]) -> None:
        for task in self.tasks.values():
            if task.parent_task_id is not None:
                parent = self.tasks.get(task.parent_task_id)
                if parent is None:
                    errors.append(
                        f"task {task.id} references missing parent {task.parent_task_id}"
                    )
                elif parent.project_id != task.project_id:
                    errors.append(
                        f"task {task.id} parent {parent.id} belongs to project "
                        f"{parent.project_id}"
                    )
            for dependency_id in task.dependency_task_ids:
                dependency = self.tasks.get(dependency_id)
                if dependency is None:
                    errors.append(
                        f"task {task.id} references missing dependency {dependency_id}"
                    )
                elif dependency.project_id != task.project_id:
                    errors.append(
                        f"task {task.id} dependency {dependency_id} belongs to project "
                        f"{dependency.project_id}"
                    )
        for task_id in self.tasks:
            self._validate_no_parent_cycle(task_id, errors)
        self._validate_no_dependency_cycle(errors)

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
                            errors.append(
                                f"task dependency cycle includes {dependency_id}"
                            )
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
        return sorted(
            task.id
            for task in self.tasks.values()
            if task.parent_task_id == task_id
        )

    def dependency_status(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None or not task.dependency_task_ids:
            return "satisfied"
        dependency_states = [
            self.tasks[dep_id].state
            for dep_id in task.dependency_task_ids
            if dep_id in self.tasks
        ]
        if any(
            state in {TeamTaskState.BLOCKED, TeamTaskState.REJECTED}
            for state in dependency_states
        ):
            return "blocked"
        if any(
            state not in {TeamTaskState.ACCEPTED, TeamTaskState.FINISHED}
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
            if dependency.state not in {TeamTaskState.ACCEPTED, TeamTaskState.FINISHED}:
                blocked_by.append(dependency_id)
        return blocked_by

    def subtask_counts(self, task_id: str) -> dict[str, int]:
        counts = {"total": 0, "accepted": 0, "finished": 0, "blocked": 0, "open": 0}
        for child_id in self.child_task_ids(task_id):
            child = self.tasks[child_id]
            counts["total"] += 1
            if child.state == TeamTaskState.ACCEPTED:
                counts["accepted"] += 1
            elif child.state == TeamTaskState.FINISHED:
                counts["finished"] += 1
            elif child.state in {TeamTaskState.BLOCKED, TeamTaskState.REJECTED}:
                counts["blocked"] += 1
            elif child.state in {
                TeamTaskState.PENDING,
                TeamTaskState.IN_PROGRESS,
                TeamTaskState.WAITING_FOR_REVIEW,
            }:
                counts["open"] += 1
        return counts

    def _validate_deliverables(self, errors: list[str]) -> None:
        for deliverable in self.deliverables.values():
            if deliverable.task_id not in self.tasks:
                errors.append(f"deliverable {deliverable.id} references missing task {deliverable.task_id}")
            if deliverable.producer_agent_id not in self.agents:
                errors.append(
                    f"deliverable {deliverable.id} references missing producer "
                    f"{deliverable.producer_agent_id}"
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

    def _validate_reviews(self, errors: list[str]) -> None:
        for review in self.review_requests.values():
            if review.deliverable_id not in self.deliverables:
                errors.append(
                    f"review request {review.id} references missing deliverable "
                    f"{review.deliverable_id}"
                )
            if review.reviewer_agent_id not in self.agents:
                errors.append(
                    f"review request {review.id} references missing reviewer {review.reviewer_agent_id}"
                )
            profile = None
            if review.review_profile_id is not None:
                profile = self.review_profiles.get(review.review_profile_id)
                if profile is None:
                    errors.append(
                        f"review request {review.id} references missing review profile "
                        f"{review.review_profile_id}"
                    )
            if review.criteria and review.review_profile_id is None:
                errors.append(
                    f"review request {review.id} has criterion results but no review profile"
                )
            valid_criterion_ids = (
                {criterion.id for criterion in profile.criteria}
                if profile is not None
                else set()
            )
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

    def _validate_action_records(self, errors: list[str]) -> None:
        for record in self.action_records.values():
            if record.agent_id not in self.agents:
                errors.append(
                    f"action record {record.id} references missing agent {record.agent_id}"
                )

    def _validate_releases(self, errors: list[str]) -> None:
        for release in self.release_packages.values():
            project = self.projects.get(release.project_id)
            if project is None:
                errors.append(f"release {release.id} references missing project {release.project_id}")
                continue
            for deliverable_id in release.deliverable_ids:
                deliverable = self.deliverables.get(deliverable_id)
                if deliverable is None:
                    errors.append(f"release {release.id} references missing deliverable {deliverable_id}")
                    continue
                task = self.tasks.get(deliverable.task_id)
                if task is not None and task.project_id != project.id:
                    errors.append(f"release {release.id} includes deliverable {deliverable_id} outside project")

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
            if parent is not None and parent.project_id != assignment.project_id:
                errors.append(
                    f"ephemeral assignment {assignment.id} parent {parent.id} "
                    f"belongs to project {parent.project_id}"
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
        raise ValueError(f"{field_name} contains duplicate ids: {', '.join(sorted(set(duplicates)))}")


def task_payload_with_derived(state: TeamState, task: AgentTask) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    payload["child_task_ids"] = state.child_task_ids(task.id)
    payload["dependency_status"] = state.dependency_status(task.id)
    payload["blocked_by_task_ids"] = state.blocked_by_task_ids(task.id)
    payload["subtask_counts"] = state.subtask_counts(task.id)
    return payload


def team_state_payload(state: TeamState) -> dict[str, Any]:
    payload = state.model_dump(mode="json")
    payload["tasks"] = {
        task_id: task_payload_with_derived(state, task)
        for task_id, task in state.tasks.items()
    }
    payload["runtime_operations"] = runtime_operations_payload(state)
    return payload


def runtime_operations_payload(
    state: TeamState,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    effective_now = _ensure_utc(now or utc_now())
    agents = [
        _runtime_agent_payload(state, agent, effective_now)
        for agent in sorted(state.agents.values(), key=lambda item: item.id)
    ]
    attention = _runtime_attention_payload(state)
    return {
        "generated_at": effective_now.isoformat(),
        "agents": agents,
        "attention": attention,
    }


def _runtime_agent_payload(
    state: TeamState,
    agent: PersistentAgent,
    now: datetime,
) -> dict[str, Any]:
    runtime = state.agent_runtimes.get(agent.id)
    wake_leased_until = runtime.wake_leased_until if runtime is not None else None
    next_wake_at = runtime.next_wake_at if runtime is not None else None
    wake_due = runtime is not None and (
        runtime.next_wake_at is None or _ensure_utc(runtime.next_wake_at) <= now
    )
    lease_active = runtime is not None and (
        runtime.wake_leased_until is not None
        and _ensure_utc(runtime.wake_leased_until) > now
    )
    detail_parts = [f"session {agent.session_id}", f"status {agent.status.value}"]
    if agent.project_id is not None:
        detail_parts.append(f"project {agent.project_id}")
    if runtime is None:
        detail_parts.append("no runtime snapshot")
    elif runtime.wake_reason is not None:
        detail_parts.append(f"wake reason {runtime.wake_reason}")
    return {
        "id": agent.id,
        "kind": "agent_runtime",
        "severity": "warning" if wake_due or lease_active else "info",
        "title": agent.id,
        "detail": "; ".join(detail_parts),
        "team_id": agent.team_id,
        "project_id": agent.project_id,
        "role": agent.role,
        "session_id": agent.session_id,
        "status": agent.status.value,
        "last_wake_at": _json_datetime(runtime.last_wake_at if runtime is not None else None),
        "next_wake_at": _json_datetime(next_wake_at),
        "wake_leased_until": _json_datetime(wake_leased_until),
        "wake_reason": runtime.wake_reason if runtime is not None else None,
        "active_dispatch_id": runtime.active_dispatch_id if runtime is not None else None,
        "no_progress_count": runtime.no_progress_count if runtime is not None else 0,
        "wake_due": wake_due,
        "lease_active": lease_active,
    }


def _runtime_attention_payload(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(_blocked_task_rows(state))
    rows.extend(_waiting_dependency_rows(state))
    rows.extend(_pending_inbox_rows(state))
    rows.extend(_failed_action_rows(state))
    rows.extend(_failed_promotion_rows(state))
    rows.extend(_blocked_release_rows(state))
    rows.extend(_ephemeral_assignment_rows(state))
    rows.sort(key=lambda row: (_severity_rank(row["severity"]), row["kind"], row["id"]))
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
        kind = "pending_escalation" if item.item_type == TeamInboxItemType.ESCALATION else "pending_inbox_item"
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
            )
        )
    return rows


def _failed_action_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    successful_action_keys = [
        (record.agent_id, record.created_at)
        for record in state.action_records.values()
        if record.success
    ]
    for record in sorted(state.action_records.values(), key=lambda entry: (entry.created_at, entry.id)):
        if record.success:
            continue
        if record.dismissed_at is not None:
            continue
        if any(
            agent_id == record.agent_id
            and created_at > record.created_at
            for agent_id, created_at in successful_action_keys
        ):
            continue
        rows.append(
            _runtime_row(
                id=record.id,
                kind="failed_action_record",
                severity="critical",
                title=f"Failed action {record.id}",
                detail=_action_record_detail(record),
                state="failed",
                agent_id=record.agent_id,
                project_id=_agent_project_id(state, record.agent_id),
                dispatch_id=record.dispatch_id,
                action_record_id=record.id,
                error=record.error,
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
                error=promotion.error,
            )
        )
    return rows


def _blocked_release_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for release in sorted(state.release_packages.values(), key=lambda entry: (entry.created_at, entry.id)):
        if release.state != TeamRecordState.BLOCKED and not release.error:
            continue
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
        "dispatch_id": None,
        "session_id": None,
    }
    base.update(row)
    return base


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


def _agent_project_id(state: TeamState, agent_id: str) -> str | None:
    agent = state.agents.get(agent_id)
    return agent.project_id if agent is not None else None


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
