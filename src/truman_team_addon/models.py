from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_sandbox.models import utc_now

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


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


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
    project_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    archived: bool = False

    @field_validator("id", "name", "manager_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("project_ids", mode="before")
    @classmethod
    def _project_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "project_ids")


class Project(TeamModel):
    id: str
    team_id: str
    name: str
    lead_agent_id: str
    goals: list[ProjectGoal] = Field(default_factory=list)
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

    @field_validator("project_id", "reports_to_agent_id", mode="before")
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


class AgentTask(TeamModel):
    id: str
    owner_agent_id: str
    project_id: str
    priority: int = Field(ge=0)
    state: TeamTaskState = TeamTaskState.PENDING
    title: str
    description: str
    created_by_agent_id: str
    deliverable_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "owner_agent_id",
        "project_id",
        "title",
        "description",
        "created_by_agent_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("deliverable_ids", mode="before")
    @classmethod
    def _deliverable_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value, "deliverable_ids")


class Deliverable(TeamModel):
    id: str
    task_id: str
    producer_agent_id: str
    artifact_refs: list[str] = Field(default_factory=list)
    summary: str
    state: TeamRecordState = TeamRecordState.PENDING
    review_request_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "task_id", "producer_agent_id", "summary", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("artifact_refs", "review_request_ids", mode="before")
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


class ReviewRequest(TeamModel):
    id: str
    deliverable_id: str
    reviewer_agent_id: str
    state: TeamRecordState = TeamRecordState.PENDING
    findings: list[str] = Field(default_factory=list)
    decision_at: datetime | None = None

    @field_validator("id", "deliverable_id", "reviewer_agent_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value: object) -> list[str]:
        return _text_list(value, "findings")


class ReleasePackage(TeamModel):
    id: str
    project_id: str
    deliverable_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    state: TeamRecordState = TeamRecordState.PENDING
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "project_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("deliverable_ids", "artifact_refs", mode="before")
    @classmethod
    def _text_lists(cls, value: object, info) -> list[str]:
        return _unique_text_list(value, info.field_name)


class TeamState(TeamModel):
    schema_version: int = SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    teams: dict[str, Team] = Field(default_factory=dict)
    projects: dict[str, Project] = Field(default_factory=dict)
    agents: dict[str, PersistentAgent] = Field(default_factory=dict)
    tasks: dict[str, AgentTask] = Field(default_factory=dict)
    deliverables: dict[str, Deliverable] = Field(default_factory=dict)
    review_requests: dict[str, ReviewRequest] = Field(default_factory=dict)
    release_packages: dict[str, ReleasePackage] = Field(default_factory=dict)
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
        self._validate_tasks(errors)
        self._validate_deliverables(errors)
        self._validate_reviews(errors)
        self._validate_releases(errors)
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
            "tasks",
            "deliverables",
            "review_requests",
            "release_packages",
        ):
            collection = getattr(self, collection_name)
            for key, record in collection.items():
                if key != record.id:
                    errors.append(f"{collection_name}.{key} key does not match record id {record.id}")

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
            if task.created_by_agent_id not in self.agents:
                errors.append(f"task {task.id} references missing creator {task.created_by_agent_id}")
            for deliverable_id in task.deliverable_ids:
                deliverable = self.deliverables.get(deliverable_id)
                if deliverable is None:
                    errors.append(f"task {task.id} references missing deliverable {deliverable_id}")
                elif deliverable.task_id != task.id:
                    errors.append(f"task {task.id} deliverable {deliverable_id} does not point back")

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


def team_state_payload(state: TeamState) -> dict[str, Any]:
    return state.model_dump(mode="json")
