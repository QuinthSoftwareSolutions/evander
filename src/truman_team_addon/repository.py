from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_sandbox.addons import addon_json_object_store
from agent_sandbox.models import utc_now
from agent_sandbox.storage import JsonObjectStore

from .models import (
    AgentTask,
    Deliverable,
    EphemeralAgentPolicy,
    PersistentAgent,
    Project,
    ProjectGoal,
    ReviewRequest,
    Team,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)

ADDON_ID = "team-addon"
STATE_FILE = "state.json"


class TeamStateConflictError(RuntimeError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"stale team add-on state revision: expected {expected_revision}, "
            f"current {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class TeamStateRepository:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.store: JsonObjectStore[Any] = addon_json_object_store(
            self.runtime_dir,
            ADDON_ID,
            STATE_FILE,
            initial_object=TeamState.empty().model_dump(mode="json"),
        )

    def read(self) -> TeamState:
        return TeamState.model_validate(self.store.read_object())

    def update(
        self,
        mutator: Callable[[TeamState], TeamState | None],
        *,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(payload: dict[str, Any]) -> TeamState:
            state = TeamState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            now = utc_now()
            next_state = next_state.model_copy(
                update={"revision": state.revision + 1, "updated_at": now},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            return validated

        return self.store.update_object(_update)  # type: ignore[return-value]

    def create_team(
        self,
        *,
        team_id: str,
        name: str,
        manager_agent_id: str,
        manager_session_id: str,
        manager_role: str = "manager",
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.teams, team_id, "team")
            _require_absent(state.agents, manager_agent_id, "agent")
            now = utc_now()
            manager = PersistentAgent(
                id=manager_agent_id,
                team_id=team_id,
                role=manager_role,
                session_id=manager_session_id,
                reports_to_agent_id=None,
            )
            state.agents[manager.id] = manager
            state.teams[team_id] = Team(
                id=team_id,
                name=name,
                manager_agent_id=manager.id,
                created_at=now,
                updated_at=now,
            )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_project(
        self,
        *,
        project_id: str,
        team_id: str,
        name: str,
        lead_agent_id: str,
        lead_session_id: str,
        goals: list[ProjectGoal] | None = None,
        lead_role: str = "project_lead",
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, team_id, "team")
            _require_absent(state.projects, project_id, "project")
            _require_absent(state.agents, lead_agent_id, "agent")
            now = utc_now()
            lead = PersistentAgent(
                id=lead_agent_id,
                team_id=team_id,
                project_id=project_id,
                role=lead_role,
                session_id=lead_session_id,
                reports_to_agent_id=team.manager_agent_id,
            )
            state.agents[lead.id] = lead
            manager = state.agents[team.manager_agent_id]
            manager.subordinate_agent_ids = _append_unique(manager.subordinate_agent_ids, lead.id)
            state.projects[project_id] = Project(
                id=project_id,
                team_id=team_id,
                name=name,
                lead_agent_id=lead.id,
                goals=goals or [],
                created_at=now,
                updated_at=now,
            )
            team.project_ids = _append_unique(team.project_ids, project_id)
            team.updated_at = now
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def register_agent(
        self,
        *,
        agent_id: str,
        team_id: str,
        project_id: str,
        role: str,
        session_id: str,
        reports_to_agent_id: str,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.agents, agent_id, "agent")
            _require_present(state.teams, team_id, "team")
            project = _require_present(state.projects, project_id, "project")
            if project.team_id != team_id:
                raise ValueError(f"project {project_id} does not belong to team {team_id}")
            superior = _require_present(state.agents, reports_to_agent_id, "superior agent")
            if superior.team_id != team_id:
                raise ValueError(f"superior agent {reports_to_agent_id} is not in team {team_id}")
            agent = PersistentAgent(
                id=agent_id,
                team_id=team_id,
                project_id=project_id,
                role=role,
                session_id=session_id,
                reports_to_agent_id=reports_to_agent_id,
                ephemeral_policy=ephemeral_policy or EphemeralAgentPolicy(),
            )
            state.agents[agent.id] = agent
            superior.subordinate_agent_ids = _append_unique(superior.subordinate_agent_ids, agent.id)
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_or_reprioritize_task(
        self,
        *,
        task_id: str,
        owner_agent_id: str,
        project_id: str,
        priority: int,
        title: str,
        description: str,
        created_by_agent_id: str,
        state: TeamTaskState = TeamTaskState.PENDING,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            now = utc_now()
            if task_id in state_model.tasks:
                task = state_model.tasks[task_id]
                task.priority = priority
                task.title = title
                task.description = description
                task.updated_at = now
                return state_model
            state_model.tasks[task_id] = AgentTask(
                id=task_id,
                owner_agent_id=owner_agent_id,
                project_id=project_id,
                priority=priority,
                state=state,
                title=title,
                description=description,
                created_by_agent_id=created_by_agent_id,
                created_at=now,
                updated_at=now,
            )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def update_task(
        self,
        *,
        task_id: str,
        priority: int | None = None,
        state: TeamTaskState | None = None,
        title: str | None = None,
        description: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            task = _require_present(state_model.tasks, task_id, "task")
            if priority is not None:
                task.priority = priority
            if state is not None:
                task.state = state
            if title is not None:
                task.title = title
            if description is not None:
                task.description = description
            task.updated_at = utc_now()
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def record_deliverable(
        self,
        *,
        deliverable_id: str,
        task_id: str,
        producer_agent_id: str,
        summary: str,
        artifact_refs: list[str] | None = None,
        state: TeamRecordState = TeamRecordState.PENDING,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            _require_absent(state_model.deliverables, deliverable_id, "deliverable")
            task = _require_present(state_model.tasks, task_id, "task")
            deliverable = Deliverable(
                id=deliverable_id,
                task_id=task_id,
                producer_agent_id=producer_agent_id,
                summary=summary,
                artifact_refs=artifact_refs or [],
                state=state,
            )
            state_model.deliverables[deliverable.id] = deliverable
            task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
            task.updated_at = utc_now()
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def record_review_request(
        self,
        *,
        review_request_id: str,
        deliverable_id: str,
        reviewer_agent_id: str,
        state: TeamRecordState = TeamRecordState.PENDING,
        findings: list[str] | None = None,
        decision_at: datetime | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            _require_absent(state_model.review_requests, review_request_id, "review request")
            deliverable = _require_present(state_model.deliverables, deliverable_id, "deliverable")
            review = ReviewRequest(
                id=review_request_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id=reviewer_agent_id,
                state=state,
                findings=findings or [],
                decision_at=decision_at,
            )
            state_model.review_requests[review.id] = review
            deliverable.review_request_ids = _append_unique(
                deliverable.review_request_ids,
                review.id,
            )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)


def _require_present(collection: dict[str, Any], key: str, label: str) -> Any:
    if key not in collection:
        raise ValueError(f"{label} {key} does not exist")
    return collection[key]


def _require_absent(collection: dict[str, Any], key: str, label: str) -> None:
    if key in collection:
        raise ValueError(f"{label} {key} already exists")


def _append_unique(values: list[str], value: str) -> list[str]:
    if value in values:
        return values
    return [*values, value]


__all__ = [
    "ADDON_ID",
    "STATE_FILE",
    "TeamStateConflictError",
    "TeamStateRepository",
    "ValidationError",
]
