from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agent_sandbox.addons import addon_json_object_store
from agent_sandbox.models import utc_now
from agent_sandbox.storage import JsonObjectStore

from .git_naming import (
    canonical_release_branch,
    ephemeral_agent_branch,
    feature_branch,
    release_branch,
    review_branch,
    validate_branch_name,
)
from .models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    EphemeralAssignment,
    EphemeralAssignmentState,
    EphemeralAgentPolicy,
    AgentStatus,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGoal,
    ProjectStatus,
    ReleasePackage,
    ReviewCriterionResult,
    ReviewProfile,
    ReviewProfileCriterion,
    ReviewRequest,
    Team,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    task_payload_with_derived,
    _ensure_unique,
)
from .templates import TeamTemplate, get_template

ADDON_ID = "team-addon"
STATE_FILE = "state.json"
EPHEMERAL_REQUESTED_EVENT_TYPE = "team.ephemeral.requested"
EPHEMERAL_COMPLETED_EVENT_TYPE = "team.ephemeral.completed"
OPERATOR_ESCALATION_EVENT_TYPE = "team.operator.escalation_requested"
GIT_PROMOTION_EVENT_TYPE = "team.git.promotion_requested"

_NON_TERMINAL_TASK_STATES = frozenset(
    {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
    }
)


@dataclass(frozen=True)
class _TaskCreationPlan:
    task_id: str
    owner_id: str
    project_id: str
    task_state: TeamTaskState
    parent_task_id: str | None
    dependency_task_ids: list[str]
    priority: int
    title: str
    description: str


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
        manager_display_name: str | None = None,
        manager_role: str = "manager",
        operator_communication_target_id: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.teams, team_id, "team")
            _require_absent(state.agents, manager_agent_id, "agent")
            now = utc_now()
            manager = PersistentAgent(
                id=manager_agent_id,
                team_id=team_id,
                display_name=manager_display_name,
                role=manager_role,
                session_id=manager_session_id,
                reports_to_agent_id=None,
            )
            state.agents[manager.id] = manager
            state.teams[team_id] = Team(
                id=team_id,
                name=name,
                manager_agent_id=manager.id,
                operator_communication_target_id=operator_communication_target_id,
                created_at=now,
                updated_at=now,
            )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_team(
        self,
        *,
        team_id: str,
        name: str | None = None,
        operator_communication_target_id: str | None = None,
        set_operator_communication_target_id: bool = False,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, team_id, "team")
            if name is not None:
                team.name = name
            if set_operator_communication_target_id:
                team.operator_communication_target_id = operator_communication_target_id
            if archived is not None:
                team.archived = archived
                if archived:
                    for agent in state.agents.values():
                        if agent.team_id == team_id:
                            agent.status = AgentStatus.ARCHIVED
            team.updated_at = utc_now()
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
        lead_display_name: str | None = None,
        goals: list[ProjectGoal] | None = None,
        git: ProjectGitConfig | None = None,
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
                display_name=lead_display_name,
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
                git=git,
                created_at=now,
                updated_at=now,
            )
            team.project_ids = _append_unique(team.project_ids, project_id)
            team.updated_at = now
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_project(
        self,
        *,
        project_id: str,
        name: str | None = None,
        git: ProjectGitConfig | None = None,
        set_git: bool = False,
        goals: list[ProjectGoal] | None = None,
        status: ProjectStatus | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            project = _require_present(state.projects, project_id, "project")
            if name is not None:
                project.name = name
            if set_git:
                project.git = git
            if goals is not None:
                project.goals = goals
            if status is not None:
                project.status = status
            project.updated_at = utc_now()
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
        display_name: str | None = None,
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
                display_name=display_name,
                role=role,
                session_id=session_id,
                reports_to_agent_id=reports_to_agent_id,
                ephemeral_policy=ephemeral_policy or EphemeralAgentPolicy(),
            )
            state.agents[agent.id] = agent
            superior.subordinate_agent_ids = _append_unique(superior.subordinate_agent_ids, agent.id)
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_agent(
        self,
        *,
        agent_id: str,
        project_id: str | None = None,
        set_project_id: bool = False,
        role: str | None = None,
        session_id: str | None = None,
        reports_to_agent_id: str | None = None,
        set_reports_to_agent_id: bool = False,
        display_name: str | None = None,
        set_display_name: bool = False,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        status: AgentStatus | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, agent_id, "agent")
            if set_project_id:
                if project_id is not None:
                    project = _require_present(state.projects, project_id, "project")
                    if project.team_id != agent.team_id:
                        raise ValueError(
                            f"project {project_id} does not belong to team {agent.team_id}"
                        )
                agent.project_id = project_id
            if role is not None:
                agent.role = role
            if session_id is not None:
                agent.session_id = session_id
            if set_display_name:
                agent.display_name = display_name
            if ephemeral_policy is not None:
                agent.ephemeral_policy = ephemeral_policy
            if status is not None:
                agent.status = status
            if set_reports_to_agent_id:
                old_superior_id = agent.reports_to_agent_id
                if reports_to_agent_id is not None:
                    superior = _require_present(
                        state.agents, reports_to_agent_id, "superior agent"
                    )
                    if superior.team_id != agent.team_id:
                        raise ValueError(
                            f"superior agent {reports_to_agent_id} is not in team {agent.team_id}"
                        )
                if old_superior_id and old_superior_id in state.agents:
                    old_superior = state.agents[old_superior_id]
                    old_superior.subordinate_agent_ids = _remove_value(
                        old_superior.subordinate_agent_ids, agent.id
                    )
                agent.reports_to_agent_id = reports_to_agent_id
                if reports_to_agent_id and reports_to_agent_id in state.agents:
                    superior = state.agents[reports_to_agent_id]
                    superior.subordinate_agent_ids = _append_unique(
                        superior.subordinate_agent_ids, agent.id
                    )
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
        created_by_agent_id: str | None = None,
        created_by_actor: str | None = None,
        state: TeamTaskState = TeamTaskState.PENDING,
        parent_task_id: str | None = None,
        set_parent_task_id: bool = False,
        dependency_task_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            now = utc_now()
            if task_id in state_model.tasks:
                task = state_model.tasks[task_id]
                task.priority = priority
                task.title = title
                task.description = description
                if set_parent_task_id or parent_task_id is not None:
                    task.parent_task_id = parent_task_id
                if dependency_task_ids is not None:
                    task.dependency_task_ids = dependency_task_ids
                task.updated_at = now
                if task.state in _NON_TERMINAL_TASK_STATES:
                    _mark_agent_work_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
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
                created_by_actor=created_by_actor,
                parent_task_id=parent_task_id,
                dependency_task_ids=dependency_task_ids or [],
                created_at=now,
                updated_at=now,
            )
            if state in _NON_TERMINAL_TASK_STATES:
                _mark_agent_work_due(state_model, agent_id=owner_agent_id, now=now)
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
        parent_task_id: str | None = None,
        set_parent_task_id: bool = False,
        dependency_task_ids: list[str] | None = None,
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
            if set_parent_task_id:
                task.parent_task_id = parent_task_id
            if dependency_task_ids is not None:
                task.dependency_task_ids = dependency_task_ids
            task.updated_at = utc_now()
            if task.state in _NON_TERMINAL_TASK_STATES:
                _mark_agent_work_due(
                    state_model,
                    agent_id=task.owner_agent_id,
                    now=task.updated_at,
                )
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

    def create_review_profile(
        self,
        *,
        profile_id: str,
        team_id: str,
        project_id: str | None = None,
        name: str,
        description: str = "",
        applicable_roles: list[str] | None = None,
        criteria: list[ReviewProfileCriterion] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.review_profiles, profile_id, "review profile")
            now = utc_now()
            profile = ReviewProfile(
                id=profile_id,
                team_id=team_id,
                project_id=project_id,
                name=name,
                description=description,
                applicable_roles=applicable_roles or [],
                criteria=criteria or [],
                created_at=now,
                updated_at=now,
            )
            state.review_profiles[profile.id] = profile
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_review_profile(
        self,
        *,
        profile_id: str,
        name: str | None = None,
        description: str | None = None,
        applicable_roles: list[str] | None = None,
        criteria: list[ReviewProfileCriterion] | None = None,
        archived: bool | None = None,
        project_id: str | None = None,
        set_project_id: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            profile = _require_present(state.review_profiles, profile_id, "review profile")
            if name is not None:
                profile.name = name
            if description is not None:
                profile.description = description
            if applicable_roles is not None:
                profile.applicable_roles = applicable_roles
            if criteria is not None:
                profile.criteria = [
                    ReviewProfileCriterion.model_validate(criterion) for criterion in criteria
                ]
            if archived is not None:
                profile.archived = archived
            if set_project_id:
                profile.project_id = project_id
            profile.updated_at = utc_now()
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def materialize_template(
        self,
        template_id: str,
        *,
        inputs: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_template_id = _required_text(template_id, "template_id")
        template = get_template(normalized_template_id)
        if template is None:
            raise ValueError(f"template {normalized_template_id} does not exist")

        normalized_inputs = _mapping(inputs, "inputs")
        team_input = _required_mapping(normalized_inputs, "team")
        project_input = _required_mapping(normalized_inputs, "project")
        team_id = _required_text(team_input.get("id"), "team.id")
        team_name = _required_text(team_input.get("name"), "team.name")
        operator_communication_target_id = _optional_text(
            team_input.get("operator_communication_target_id")
        )
        project_id = _required_text(project_input.get("id"), "project.id")
        project_name = _required_text(project_input.get("name"), "project.name")
        git = _project_git_config(project_input.get("git"))

        role_inputs = _materialize_role_inputs(template, normalized_inputs, team_input, project_input)
        goal_ids = {
            goal.id: _materialized_template_record_id(project_id, goal.id)
            for goal in template.project_goals
        }
        review_profile_ids = {
            profile.id: _materialized_template_record_id(project_id, profile.id)
            for profile in template.review_profiles
        }
        task_ids = {
            task.id: _materialized_template_record_id(project_id, task.id)
            for task in template.tasks
        }
        concrete_ids = [
            team_id,
            project_id,
            *[role_input["agent_id"] for role_input in role_inputs.values()],
            *goal_ids.values(),
            *review_profile_ids.values(),
            *task_ids.values(),
        ]
        _ensure_unique(concrete_ids, "materialization ids")
        _ensure_unique(
            [role_input["session_id"] for role_input in role_inputs.values()],
            "materialization session ids",
        )

        result: dict[str, Any] = {
            "success": False,
            "template_id": template.id,
            "template_name": template.name,
            "team_id": team_id,
            "project_id": project_id,
            "agent_ids": {key: value["agent_id"] for key, value in role_inputs.items()},
            "goal_ids": [goal_ids[goal.id] for goal in template.project_goals],
            "review_profile_ids": [
                review_profile_ids[profile.id] for profile in template.review_profiles
            ],
            "task_ids": [task_ids[task.id] for task in template.tasks],
            "errors": [],
        }

        role_agent_ids = {role.key: role_inputs[role.key]["agent_id"] for role in template.roles}
        root_role_key = template.root_role_key
        root_role = next(role for role in template.roles if role.key == root_role_key)
        if root_role.reports_to_role_key is not None:
            raise ValueError(f"template {template.id} root role must not report upward")

        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.teams, team_id, "team")
            _require_absent(state.projects, project_id, "project")
            for role_key, role_input in role_inputs.items():
                _require_absent(state.agents, role_input["agent_id"], "agent")
            for profile in template.review_profiles:
                _require_absent(
                    state.review_profiles,
                    review_profile_ids[profile.id],
                    "review profile",
                )
            for task in template.tasks:
                _require_absent(state.tasks, task_ids[task.id], "task")

            now = utc_now()
            manager_role_input = role_inputs[root_role_key]
            manager = PersistentAgent(
                id=manager_role_input["agent_id"],
                team_id=team_id,
                role=root_role_key,
                session_id=manager_role_input["session_id"],
                reports_to_agent_id=None,
                ephemeral_policy=root_role.ephemeral_policy.model_copy(deep=True),
            )
            state.agents[manager.id] = manager
            state.teams[team_id] = Team(
                id=team_id,
                name=team_name,
                manager_agent_id=manager.id,
                operator_communication_target_id=operator_communication_target_id,
                created_at=now,
                updated_at=now,
            )

            project_goals = [
                goal.model_copy(update={"id": goal_ids[goal.id]}, deep=True)
                for goal in template.project_goals
            ]
            state.projects[project_id] = Project(
                id=project_id,
                team_id=team_id,
                name=project_name,
                lead_agent_id=role_agent_ids["project_lead"],
                goals=project_goals,
                git=git,
                created_at=now,
                updated_at=now,
            )
            state.teams[team_id].project_ids = _append_unique(
                state.teams[team_id].project_ids,
                project_id,
            )

            role_objects: dict[str, PersistentAgent] = {
                root_role_key: manager,
            }
            ordered_roles = _ordered_template_roles(template)
            for role in ordered_roles:
                if role.key == root_role_key:
                    continue
                superior_role_key = role.reports_to_role_key
                if superior_role_key is None:
                    raise ValueError(
                        f"template {template.id} role {role.key} is missing reports_to_role_key"
                    )
                superior = role_objects.get(superior_role_key)
                if superior is None:
                    raise ValueError(
                        f"template {template.id} role {role.key} reports to missing role "
                        f"{superior_role_key}"
                    )
                role_agent = PersistentAgent(
                    id=role_agent_ids[role.key],
                    team_id=team_id,
                    project_id=project_id,
                    role=role.key,
                    session_id=role_inputs[role.key]["session_id"],
                    reports_to_agent_id=superior.id,
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                )
                state.agents[role_agent.id] = role_agent
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids,
                    role_agent.id,
                )
                role_objects[role.key] = role_agent
            state.projects[project_id].lead_agent_id = role_agent_ids["project_lead"]
            state.projects[project_id].updated_at = now
            state.teams[team_id].updated_at = now

            for profile_template in template.review_profiles:
                profile = ReviewProfile(
                    id=review_profile_ids[profile_template.id],
                    team_id=team_id,
                    project_id=project_id if profile_template.project_scoped else None,
                    name=profile_template.name,
                    description=profile_template.description,
                    applicable_roles=list(profile_template.applicable_role_keys),
                    criteria=[
                        criterion.model_copy(deep=True)
                        for criterion in profile_template.criteria
                    ],
                    created_at=now,
                    updated_at=now,
                )
                state.review_profiles[profile.id] = profile

            for task_template in template.tasks:
                owner_agent_id = role_agent_ids[task_template.owner_role_key]
                created_by_agent_id = role_agent_ids[task_template.created_by_role_key]
                dependency_task_ids = [
                    task_ids[dependency_id]
                    for dependency_id in task_template.dependency_task_ids
                ]
                state.tasks[task_ids[task_template.id]] = AgentTask(
                    id=task_ids[task_template.id],
                    owner_agent_id=owner_agent_id,
                    project_id=project_id,
                    priority=task_template.priority,
                    state=task_template.state,
                    title=task_template.title,
                    description=task_template.description,
                    created_by_agent_id=created_by_agent_id,
                    parent_task_id=(
                        task_ids[task_template.parent_task_id]
                        if task_template.parent_task_id is not None
                        else None
                    ),
                    dependency_task_ids=dependency_task_ids,
                    created_at=now,
                    updated_at=now,
                )

            result["success"] = True
            result["created_agent_ids"] = dict(result["agent_ids"])
            return state

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["created_team_id"] = team_id
        result["created_project_id"] = project_id
        return state, result

    def record_review_request(
        self,
        *,
        review_request_id: str,
        deliverable_id: str,
        reviewer_agent_id: str,
        state: TeamRecordState = TeamRecordState.PENDING,
        findings: list[str] | None = None,
        review_profile_id: str | None = None,
        instructions: str | None = None,
        decision_at: datetime | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            _require_absent(state_model.review_requests, review_request_id, "review request")
            deliverable = _require_present(state_model.deliverables, deliverable_id, "deliverable")
            reviewer = _require_present(state_model.agents, reviewer_agent_id, "reviewer")
            task = _require_present(state_model.tasks, deliverable.task_id, "task")
            project = _require_present(state_model.projects, task.project_id, "project")
            if reviewer.team_id != project.team_id:
                raise ValueError(
                    f"reviewer {reviewer_agent_id} is not in the same team as deliverable "
                    f"{deliverable_id}"
                )
            _profile, profile_error = _review_profile_for_request(
                state_model,
                profile_id=review_profile_id,
                expected_team_id=reviewer.team_id,
                project_id=task.project_id,
                reviewer=reviewer,
            )
            if profile_error is not None:
                raise ValueError(profile_error)
            review = ReviewRequest(
                id=review_request_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id=reviewer_agent_id,
                state=state,
                findings=findings or [],
                review_profile_id=review_profile_id,
                instructions=instructions,
                created_at=utc_now(),
                decision_at=decision_at,
            )
            state_model.review_requests[review.id] = review
            deliverable.review_request_ids = _append_unique(
                deliverable.review_request_ids,
                review.id,
            )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def record_review_decision(
        self,
        *,
        review_request_id: str,
        state: TeamRecordState,
        findings: list[str] | None = None,
        criteria: list[ReviewCriterionResult] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_id = _required_text(review_request_id, "review_request_id")
        if not _is_review_decision_state(state):
            raise ValueError(
                f"review decision state {state.value} must be accepted, rejected, or blocked"
            )
        findings_list = list(findings or [])
        criterion_results = (
            [ReviewCriterionResult.model_validate(item) for item in criteria]
            if criteria is not None
            else None
        )

        def _mutate(state_model: TeamState) -> TeamState:
            review = _require_present(
                state_model.review_requests, normalized_id, "review request"
            )
            if criterion_results is not None:
                review.criteria = criterion_results
            projects_to_recalc: set[str] = set()
            _cascade_review_decision(
                state_model,
                review=review,
                decision_state=state,
                findings=findings_list,
                projects_to_recalc=projects_to_recalc,
            )
            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, project_id)
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def create_release_package(
        self,
        *,
        project_id: str,
        deliverable_ids: list[str],
        release_id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        state, _result = self.create_release_package_with_result(
            project_id=project_id,
            deliverable_ids=deliverable_ids,
            release_id=release_id,
            release_branch=release_branch,
            canonical_branch=canonical_branch,
            expected_revision=expected_revision,
        )
        return state

    def create_release_package_with_result(
        self,
        *,
        project_id: str,
        deliverable_ids: list[str],
        release_id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_project_id = _required_text(project_id, "project_id")
        request = {
            "id": _optional_text(release_id),
            "project_id": normalized_project_id,
            "deliverable_ids": list(deliverable_ids),
            "release_branch": _optional_text(release_branch),
            "canonical_branch": _optional_text(canonical_branch),
        }
        result: dict[str, Any] = {
            "success": False,
            "release_package_ids": [],
            "errors": [],
            "events": [],
            "git_promotion_event_ids": [],
            "created_deliverable_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            project = _require_present(
                state_model.projects, normalized_project_id, "project"
            )
            projects_to_recalc: set[str] = set()
            created_id = _build_release_package(
                state_model,
                project=project,
                request=request,
                result=result,
                projects_to_recalc=projects_to_recalc,
                actor_agent_id=project.lead_agent_id,
                team_id=project.team_id,
            )
            if created_id is not None:
                result["release_package_ids"].append(created_id)
            if result["errors"]:
                raise ValueError("; ".join(result["errors"]))
            for recalc_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, recalc_id)
            result["success"] = True
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def request_ephemeral_assignments(
        self,
        *,
        parent_agent_id: str,
        requests: list[dict[str, Any]],
        dispatch_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_parent_id = _required_text(parent_agent_id, "parent_agent_id")
        normalized_dispatch_id = _optional_text(dispatch_id)
        normalized_requests = _list_of_mappings(requests, "requests")
        result: dict[str, Any] = _ephemeral_request_result()

        def _mutate(state_model: TeamState) -> TeamState:
            parent = _require_present(state_model.agents, normalized_parent_id, "parent agent")
            _create_ephemeral_assignments(
                state_model,
                parent=parent,
                requests=normalized_requests,
                dispatch_id=normalized_dispatch_id,
                result=result,
            )
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        return state, result

    def mark_ephemeral_spawned(
        self,
        *,
        assignment_id: str,
        session_id: str | None = None,
        create_result: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_session_id = _optional_text(session_id)
        normalized_create_result = dict(create_result or {})
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _require_present(
                state_model.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            result["session_id"] = assignment.session_id
            if normalized_session_id is not None and normalized_session_id != assignment.session_id:
                result["error"] = "session_id does not match ephemeral assignment"
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = str(result["error"])
            elif normalized_create_result and not _bool_from_value(
                normalized_create_result.get("success"),
                default=True,
            ):
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = (
                    _optional_text(normalized_create_result.get("message"))
                    or _optional_text(normalized_create_result.get("error"))
                    or "spawn failed"
                )
                result["error"] = assignment.error
            else:
                assignment.state = EphemeralAssignmentState.RUNNING
                assignment.error = None
                result["success"] = True
            assignment.updated_at = utc_now()
            result["state"] = assignment.state.value
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def apply_ephemeral_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": None,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
            "event": None,
            "created_inbox_item_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _resolve_ephemeral_assignment(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["assignment_id"] = assignment.id
            result["session_id"] = assignment.session_id
            if not valid:
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = error or "invalid ephemeral action"
                result["error"] = assignment.error
                result["state"] = assignment.state.value
                assignment.updated_at = utc_now()
                return state_model

            assignment.summary = _required_text(normalized_action.get("summary"), "summary")
            assignment.artifact_refs = _string_list(
                normalized_action.get("artifact_refs"),
                "artifact_refs",
            )
            agent_branch = _optional_text(normalized_action.get("agent_branch"))
            commit_sha = _optional_text(normalized_action.get("commit_sha"))
            if agent_branch is not None:
                expected_branch = ephemeral_agent_branch(
                    assignment.session_id, assignment.id
                )
                if agent_branch != expected_branch:
                    assignment.state = EphemeralAssignmentState.FAILED
                    assignment.error = (
                        f"agent_branch {agent_branch!r} does not match expected "
                        f"ephemeral branch {expected_branch!r}"
                    )
                    assignment.updated_at = utc_now()
                    result["error"] = assignment.error
                    result["state"] = assignment.state.value
                    result["success"] = False
                    return state_model
                assignment.agent_branch = agent_branch
            if commit_sha is not None:
                assignment.commit_sha = commit_sha
            assignment.error = _optional_text(normalized_action.get("error"))
            assignment.state = (
                EphemeralAssignmentState.FAILED
                if assignment.error
                else EphemeralAssignmentState.COMPLETED
            )
            if assignment.state == EphemeralAssignmentState.COMPLETED:
                inbox_id = f"inbox-{uuid4().hex}"
                state_model.inbox[inbox_id] = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=assignment.parent_agent_id,
                    sender_agent_id=None,
                    task_id=assignment.task_id,
                    item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                    payload={
                        "ephemeral_assignment_id": assignment.id,
                        "summary": assignment.summary,
                        "artifact_refs": list(assignment.artifact_refs),
                    },
                )
                result["created_inbox_item_ids"].append(inbox_id)
            assignment.updated_at = utc_now()
            result["success"] = assignment.state == EphemeralAssignmentState.COMPLETED
            result["state"] = assignment.state.value
            result["error"] = assignment.error
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        assignment_id = result.get("assignment_id")
        if isinstance(assignment_id, str) and result.get("success"):
            result["event"] = _ephemeral_assignment_event_payload(
                state,
                assignment_id,
                event_type=EPHEMERAL_COMPLETED_EVENT_TYPE,
            )
        return state, result

    def accept_ephemeral_deliverable(
        self,
        *,
        assignment_id: str,
        accepted: bool = True,
        deliverable_id: str | None = None,
        rejection_reason: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_rejection_reason = _optional_text(rejection_reason)
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "accepted": bool(accepted),
            "deliverable_id": None,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _require_present(
                state_model.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            result["deliverable_id"] = _apply_ephemeral_acceptance(
                state_model,
                assignment=assignment,
                accepted=accepted,
                deliverable_id=normalized_deliverable_id,
                rejection_reason=normalized_rejection_reason,
            )
            result["success"] = True
            result["state"] = assignment.state.value
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def mark_ephemeral_cleaned_up(
        self,
        *,
        assignment_id: str | None = None,
        session_id: str | None = None,
        destroy_result: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _optional_text(assignment_id)
        normalized_session_id = _optional_text(session_id)
        normalized_destroy_result = dict(destroy_result or {})
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _resolve_ephemeral_assignment(
                state_model,
                action={"ephemeral_assignment_id": normalized_assignment_id}
                if normalized_assignment_id
                else {},
                session_id=normalized_session_id,
            )
            result["assignment_id"] = assignment.id
            result["session_id"] = assignment.session_id
            if normalized_destroy_result and not bool(normalized_destroy_result.get("success", True)):
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = (
                    _optional_text(normalized_destroy_result.get("message"))
                    or "cleanup failed"
                )
                result["error"] = assignment.error
            else:
                assignment.state = EphemeralAssignmentState.CLEANED_UP
                assignment.error = None
                result["success"] = True
            assignment.updated_at = utc_now()
            result["state"] = assignment.state.value
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def lease_due_agents(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 900,
        wake_interval_seconds: int = 900,
        max_no_progress_count: int = 3,
        team_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_limit = max(0, int(limit))
        leases: list[dict[str, Any]] = []

        def _mutate(state_model: TeamState) -> TeamState:
            for agent in sorted(state_model.agents.values(), key=lambda item: item.id):
                if normalized_limit and len(leases) >= normalized_limit:
                    break
                if agent.status != AgentStatus.ACTIVE:
                    continue
                if normalized_team_id is not None and agent.team_id != normalized_team_id:
                    continue
                if normalized_project_id is not None and agent.project_id != normalized_project_id:
                    continue
                if normalized_agent_id is not None and agent.id != normalized_agent_id:
                    continue
                runtime = state_model.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue
                active_tasks = _active_tasks_for_agent(state_model, agent.id)
                pending_inbox = _pending_inbox_for_agent(state_model, agent.id)
                wake_eligible = (
                    bool(pending_inbox)
                    or any(
                        state_model.dependency_status(task.id) == "satisfied"
                        for task in active_tasks
                    )
                    or any(
                        state_model.subtask_counts(task.id)["open"] > 0
                        for task in active_tasks
                    )
                )
                if not wake_eligible:
                    continue
                due_by_time = (
                    runtime.next_wake_at is None
                    or _ensure_utc(runtime.next_wake_at) <= effective_now
                )
                if not pending_inbox and not due_by_time:
                    continue
                if (
                    max_no_progress_count >= 0
                    and runtime.no_progress_count >= max_no_progress_count
                    and not pending_inbox
                ):
                    continue

                dispatch_id = f"team-dispatch-{uuid4().hex}"
                reason = "inbox" if pending_inbox else "scheduled"
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_reason = reason
                runtime.last_wake_at = effective_now
                runtime.wake_leased_until = effective_now + timedelta(seconds=lease_seconds)
                runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
                runtime.updated_at = effective_now
                leases.append(
                    {
                        "agent": agent.model_dump(mode="json"),
                        "runtime": runtime.model_dump(mode="json"),
                        "dispatch_id": dispatch_id,
                        "reason": reason,
                        "tasks": [
                            task_payload_with_derived(state_model, task)
                            for task in active_tasks
                        ],
                        "inbox": [item.model_dump(mode="json") for item in pending_inbox],
                        "superior_agent_id": agent.reports_to_agent_id,
                        "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                    }
                )
            return state_model

        if normalized_limit == 0:
            return self.read(), []
        state = self.update(_mutate)
        return state, leases

    def mirror_session_metadata_payload(
        self,
        *,
        state: TeamState,
        agent_id: str,
    ) -> dict[str, Any]:
        agent = _require_present(state.agents, agent_id, "agent")
        active_task_ids = [task.id for task in _active_tasks_for_agent(state, agent_id)]
        pending_inbox_ids = [item.id for item in _pending_inbox_for_agent(state, agent_id)]
        return {
            "team_addon": {
                "agent_id": agent.id,
                "team_id": agent.team_id,
                "project_id": agent.project_id,
                "role": agent.role,
                "reports_to_agent_id": agent.reports_to_agent_id,
                "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                "active_task_ids": active_task_ids,
                "pending_inbox_item_ids": pending_inbox_ids,
            }
        }

    def apply_agent_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        normalized_dispatch_id = _optional_text(dispatch_id) or _optional_text(
            normalized_action.get("dispatch_id")
        )
        result: dict[str, Any] = {
            "success": False,
            "applied": False,
            "agent_id": None,
            "errors": [],
            "created_inbox_item_ids": [],
            "created_deliverable_ids": [],
            "created_task_ids": [],
            "created_status_request_ids": [],
            "created_review_request_ids": [],
            "decided_review_request_ids": [],
            "updated_goal_ids": [],
            "operator_escalation_event_ids": [],
            "git_promotion_event_ids": [],
            "release_package_ids": [],
            "updated_task_ids": [],
            "handled_inbox_item_ids": [],
            "created_ephemeral_assignment_ids": [],
            "ephemeral_events": [],
            "events": [],
            "accepted_ephemeral_assignment_ids": [],
            "rejected_ephemeral_assignment_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            agent = _resolve_action_agent(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["agent_id"] = agent.id
            runtime = state_model.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            action_id = f"action-{uuid4().hex}"
            if not valid:
                _mark_action_failure(
                    state_model,
                    runtime=runtime,
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                    action=normalized_action,
                    error=error or "invalid action",
                    action_id=action_id,
                    result=result,
                )
                return state_model
            if normalized_dispatch_id and runtime.active_dispatch_id:
                if normalized_dispatch_id != runtime.active_dispatch_id:
                    _mark_action_failure(
                        state_model,
                        runtime=runtime,
                        agent_id=agent.id,
                        dispatch_id=normalized_dispatch_id,
                        action=normalized_action,
                        error="dispatch_id does not match active team runtime lease",
                        action_id=action_id,
                        result=result,
                    )
                    return state_model

            progress = False
            projects_to_recalc: set[str] = set()
            for index, update in enumerate(
                _list_of_mappings(normalized_action.get("task_updates"), "task_updates")
            ):
                try:
                    task_id = _required_text(update.get("task_id"), "task_id")
                    task = _require_present(state_model.tasks, task_id, "task")
                    is_owner = task.owner_agent_id == agent.id
                    raw_state = _optional_text(update.get("state"))
                    priority = update.get("priority")
                    title = _optional_text(update.get("title"))
                    description = _optional_text(update.get("description"))
                    has_owner_only_edit = (
                        raw_state is not None
                        or priority is not None
                        or title is not None
                        or description is not None
                    )
                    has_dependency_edit = "dependency_task_ids" in update
                    # State, priority, title, and description remain owner-only.
                    if has_owner_only_edit and not is_owner:
                        result["errors"].append(f"agent {agent.id} cannot update task {task_id}")
                        continue
                    new_dependency_task_ids: list[str] | None = None
                    if has_dependency_edit:
                        graph_permitted = (
                            is_owner
                            or _supervises(state_model, agent.id, task.owner_agent_id)
                            or _is_project_lead_for(state_model, agent.id, task.project_id)
                            or _is_team_manager(state_model, agent)
                        )
                        if not graph_permitted:
                            result["errors"].append(
                                f"agent {agent.id} is not permitted to edit the task graph "
                                f"for task {task_id}"
                            )
                            continue
                        new_dependency_task_ids = _string_list(
                            update.get("dependency_task_ids"), "dependency_task_ids"
                        )
                        graph_error = _task_graph_edit_error(
                            state_model,
                            task_id=task.id,
                            project_id=task.project_id,
                            parent_task_id=task.parent_task_id,
                            dependency_task_ids=new_dependency_task_ids,
                        )
                        if graph_error is not None:
                            result["errors"].append(graph_error)
                            continue
                    changed = False
                    if raw_state is not None:
                        task.state = TeamTaskState(raw_state)
                        projects_to_recalc.add(task.project_id)
                        changed = True
                    if priority is not None:
                        task.priority = int(priority)
                        changed = True
                    if title is not None:
                        task.title = title
                        changed = True
                    if description is not None:
                        task.description = description
                        changed = True
                    if new_dependency_task_ids is not None:
                        task.dependency_task_ids = new_dependency_task_ids
                        changed = True
                    if changed:
                        task.updated_at = utc_now()
                        progress = True
                        result["updated_task_ids"].append(task.id)
                except ValueError as exc:
                    _append_action_item_error(result, "task_updates", index, exc)

            task_creation_plans: list[_TaskCreationPlan] = []
            reserved_task_ids: set[str] = set()
            for index, creation in enumerate(
                _list_of_mappings(normalized_action.get("task_creations"), "task_creations")
            ):
                try:
                    plan = _plan_task_creation(
                        state_model,
                        actor=agent,
                        creation=creation,
                        result=result,
                        reserved_task_ids=reserved_task_ids,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "task_creations", index, exc)
                    continue
                if plan is None:
                    continue
                reserved_task_ids.add(plan.task_id)
                task_creation_plans.append(plan)

            graph_errors = _task_creation_batch_graph_errors(
                state_model,
                task_creation_plans,
            )
            for plan in task_creation_plans:
                graph_error = graph_errors.get(plan.task_id)
                if graph_error is not None:
                    result["errors"].append(graph_error)
                    continue
                created_id = _apply_task_creation_plan(
                    state_model,
                    plan=plan,
                    actor=agent,
                )
                result["created_task_ids"].append(created_id)
                projects_to_recalc.add(state_model.tasks[created_id].project_id)
                progress = True

            for question in _list_of_mappings(normalized_action.get("questions"), "questions"):
                recipient_id = (
                    _optional_text(question.get("recipient_agent_id"))
                    or agent.reports_to_agent_id
                )
                upward_error = _upward_recipient_error(agent, recipient_id, "questions")
                if upward_error is not None:
                    result["errors"].append(upward_error)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
                    task_id=_optional_text(question.get("task_id")),
                    item_type=TeamInboxItemType.QUESTION,
                    payload={
                        key: value
                        for key, value in question.items()
                        if key not in {"recipient_agent_id", "task_id"}
                    },
                )
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                progress = True

            for report in _list_of_mappings(
                normalized_action.get("status_reports"), "status_reports"
            ):
                recipient_id = (
                    _optional_text(report.get("recipient_agent_id"))
                    or agent.reports_to_agent_id
                )
                upward_error = _upward_recipient_error(agent, recipient_id, "status_reports")
                if upward_error is not None:
                    result["errors"].append(upward_error)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
                    task_id=_optional_text(report.get("task_id")),
                    item_type=TeamInboxItemType.PROGRESS_REPORT,
                    payload={
                        key: value
                        for key, value in report.items()
                        if key not in {"recipient_agent_id", "task_id"}
                    },
                )
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                progress = True

            for index, status_request in enumerate(
                _list_of_mappings(normalized_action.get("status_requests"), "status_requests")
            ):
                try:
                    recipient_id = _required_text(
                        status_request.get("recipient_agent_id"), "recipient_agent_id"
                    )
                    if not _is_direct_subordinate(agent, recipient_id):
                        result["errors"].append(
                            f"agent {agent.id} can only request status from a direct subordinate"
                        )
                        continue
                    inbox_id = f"inbox-{uuid4().hex}"
                    item = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=recipient_id,
                        sender_agent_id=agent.id,
                        task_id=_optional_text(status_request.get("task_id")),
                        item_type=TeamInboxItemType.STATUS_REQUEST,
                        payload={
                            key: value
                            for key, value in status_request.items()
                            if key not in {"recipient_agent_id", "task_id"}
                        },
                    )
                    state_model.inbox[item.id] = item
                    result["created_status_request_ids"].append(item.id)
                    progress = True
                except ValueError as exc:
                    _append_action_item_error(result, "status_requests", index, exc)

            for escalation in _list_of_mappings(
                normalized_action.get("operator_escalations"), "operator_escalations"
            ):
                if _apply_operator_escalation(
                    state_model,
                    actor=agent,
                    escalation=escalation,
                    result=result,
                ):
                    progress = True

            for index, deliverable_payload in enumerate(
                _list_of_mappings(normalized_action.get("deliverables"), "deliverables")
            ):
                try:
                    task_id = _required_text(deliverable_payload.get("task_id"), "task_id")
                    task = _require_present(state_model.tasks, task_id, "task")
                    if task.owner_agent_id != agent.id:
                        result["errors"].append(f"agent {agent.id} cannot deliver task {task_id}")
                        continue
                    deliverable_id = _optional_text(deliverable_payload.get("id")) or f"deliverable-{uuid4().hex}"
                    artifact_refs = _string_list(deliverable_payload.get("artifact_refs"), "artifact_refs")
                    artifact_space_ref = _optional_text(deliverable_payload.get("artifact_space_ref"))
                    if artifact_space_ref:
                        artifact_refs = _append_unique(artifact_refs, artifact_space_ref)
                    deliverable = Deliverable(
                        id=deliverable_id,
                        task_id=task_id,
                        producer_agent_id=agent.id,
                        summary=_required_text(deliverable_payload.get("summary"), "summary"),
                        artifact_refs=artifact_refs,
                        state=TeamRecordState.PENDING,
                    )
                    state_model.deliverables[deliverable.id] = deliverable
                    task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
                    task.state = TeamTaskState.WAITING_FOR_REVIEW
                    task.updated_at = utc_now()
                    projects_to_recalc.add(task.project_id)
                    result["created_deliverable_ids"].append(deliverable.id)
                    if agent.reports_to_agent_id is not None:
                        inbox_id = f"inbox-{uuid4().hex}"
                        state_model.inbox[inbox_id] = AgentInboxItem(
                            id=inbox_id,
                            recipient_agent_id=agent.reports_to_agent_id,
                            sender_agent_id=agent.id,
                            task_id=task_id,
                            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                            payload={"deliverable_id": deliverable.id, "summary": deliverable.summary},
                        )
                        result["created_inbox_item_ids"].append(inbox_id)
                    progress = True
                except ValueError as exc:
                    _append_action_item_error(result, "deliverables", index, exc)

            for index, review_request in enumerate(
                _list_of_mappings(normalized_action.get("review_requests"), "review_requests")
            ):
                try:
                    created_id = _apply_review_request(
                        state_model,
                        actor=agent,
                        request=review_request,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "review_requests", index, exc)
                    continue
                if created_id is not None:
                    result["created_review_request_ids"].append(created_id)
                    progress = True

            for index, decision in enumerate(
                _list_of_mappings(normalized_action.get("review_decisions"), "review_decisions")
            ):
                try:
                    decided_id = _apply_review_decision(
                        state_model,
                        actor=agent,
                        decision=decision,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "review_decisions", index, exc)
                    continue
                if decided_id is not None:
                    result["decided_review_request_ids"].append(decided_id)
                    progress = True

            for index, goal_update in enumerate(
                _list_of_mappings(normalized_action.get("goal_updates"), "goal_updates")
            ):
                try:
                    updated_id = _apply_goal_update(
                        state_model,
                        actor=agent,
                        update=goal_update,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "goal_updates", index, exc)
                    continue
                if updated_id is not None:
                    result["updated_goal_ids"].append(updated_id)
                    progress = True

            for index, promotion in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_review_promotions"), "git_review_promotions"
                )
            ):
                try:
                    promotion_id = _apply_git_review_promotion(
                        state_model,
                        actor=agent,
                        request=promotion,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_review_promotions", index, exc)
                    continue
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    progress = True

            for index, feature_update in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_feature_updates"), "git_feature_updates"
                )
            ):
                try:
                    promotion_id = _apply_git_feature_update(
                        state_model,
                        actor=agent,
                        update=feature_update,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_feature_updates", index, exc)
                    continue
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    progress = True

            for index, release_request in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_release_requests"), "git_release_requests"
                )
            ):
                try:
                    release_id = _apply_git_release_request(
                        state_model,
                        actor=agent,
                        request=release_request,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_release_requests", index, exc)
                    continue
                if release_id is not None:
                    result["release_package_ids"].append(release_id)
                    progress = True

            for inbox_item_id in _string_list(
                normalized_action.get("handled_inbox_item_ids"),
                "handled_inbox_item_ids",
            ):
                item = state_model.inbox.get(inbox_item_id)
                if item is None or item.recipient_agent_id != agent.id:
                    result["errors"].append(f"agent {agent.id} cannot handle inbox item {inbox_item_id}")
                    continue
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = utc_now()
                result["handled_inbox_item_ids"].append(inbox_item_id)
                progress = True

            ephemeral_requests = _list_of_mappings(
                normalized_action.get("ephemeral_requests"),
                "ephemeral_requests",
            )
            if ephemeral_requests:
                try:
                    _create_ephemeral_assignments(
                        state_model,
                        parent=agent,
                        requests=ephemeral_requests,
                        dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id,
                        result=result,
                    )
                    progress = True
                except ValueError as exc:
                    result["errors"].append(f"ephemeral_requests: {exc}")

            for index, acceptance in enumerate(
                _list_of_mappings(
                    normalized_action.get("ephemeral_acceptances"),
                    "ephemeral_acceptances",
                )
            ):
                try:
                    assignment_id = _required_text(
                        acceptance.get("assignment_id"), "assignment_id"
                    )
                    assignment = _require_present(
                        state_model.ephemeral_assignments,
                        assignment_id,
                        "ephemeral assignment",
                    )
                    if assignment.parent_agent_id != agent.id:
                        result["errors"].append(
                            f"agent {agent.id} cannot accept ephemeral assignment {assignment_id}"
                        )
                        continue
                    accepted = _bool_from_value(acceptance.get("accepted"), default=True)
                    _apply_ephemeral_acceptance(
                        state_model,
                        assignment=assignment,
                        accepted=accepted,
                        deliverable_id=_optional_text(acceptance.get("deliverable_id")),
                        rejection_reason=_optional_text(acceptance.get("rejection_reason")),
                    )
                    if accepted:
                        result["accepted_ephemeral_assignment_ids"].append(assignment.id)
                    else:
                        result["rejected_ephemeral_assignment_ids"].append(assignment.id)
                    progress = True
                except ValueError as exc:
                    _append_action_item_error(result, "ephemeral_acceptances", index, exc)

            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, project_id)

            record = AgentActionRecord(
                id=action_id,
                agent_id=agent.id,
                dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                action=normalized_action,
                success=not result["errors"],
                error="; ".join(result["errors"]) if result["errors"] else None,
            )
            state_model.action_records[record.id] = record
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.no_progress_count = 0 if progress else runtime.no_progress_count + 1
            if progress:
                runtime.last_progress_at = utc_now()
            runtime.updated_at = utc_now()
            result["success"] = progress or not result["errors"]
            result["applied"] = progress
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["ephemeral_events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        return state, result

    def record_git_promotion_result(
        self,
        *,
        promotion_id: str,
        success: bool = True,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        commit_sha: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_promotion_id = _required_text(promotion_id, "promotion_id")
        normalized_outputs = dict(outputs or {})
        normalized_error = _optional_text(error)
        normalized_commit_sha = _optional_text(commit_sha)
        result: dict[str, Any] = {
            "success": False,
            "promotion_id": normalized_promotion_id,
            "promotion_state": None,
            "deliverable_id": None,
            "release_package_id": None,
            "created_deliverable_ids": [],
            "created_inbox_item_ids": [],
            "events": [],
            "git_promotion_event_ids": [],
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            promotion = _require_present(
                state_model.git_promotions,
                normalized_promotion_id,
                "git promotion",
            )
            promotion.outputs = normalized_outputs
            if normalized_commit_sha is not None:
                promotion.commit_sha = normalized_commit_sha
            if success:
                promotion.state = "succeeded"
                promotion.error = None
                _attach_promotion_success(
                    state_model,
                    promotion=promotion,
                    commit_sha=normalized_commit_sha,
                    result=result,
                )
            else:
                promotion.state = "failed"
                promotion.error = normalized_error or "git promotion failed"
                result["error"] = promotion.error
                _attach_promotion_failure(
                    state_model,
                    promotion=promotion,
                    result=result,
                )
            promotion.updated_at = utc_now()
            _recalculate_project_status(state_model, promotion.project_id)
            result["promotion_state"] = promotion.state
            result["deliverable_id"] = promotion.deliverable_id
            result["release_package_id"] = promotion.release_package_id
            result["success"] = True
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def mark_release_published(
        self,
        *,
        release_package_id: str,
        success: bool = True,
        canonical_branch: str | None = None,
        published_commit_sha: str | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_id = _required_text(release_package_id, "release_package_id")
        normalized_canonical = _optional_text(canonical_branch)
        normalized_commit = _optional_text(published_commit_sha)
        normalized_error = _optional_text(error)
        # outputs are accepted for parity with the git effect contract; they are
        # not persisted on the release package today.
        _ = dict(outputs or {})
        result: dict[str, Any] = {
            "success": False,
            "release_package_id": normalized_id,
            "state": None,
            "created_inbox_item_ids": [],
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            release = _require_present(
                state_model.release_packages,
                normalized_id,
                "release package",
            )
            now = utc_now()
            if success:
                release.state = TeamRecordState.ACCEPTED
                if normalized_canonical is not None:
                    release.canonical_branch = normalized_canonical
                if normalized_commit is not None:
                    release.published_commit_sha = normalized_commit
                release.published_at = now
                release.error = None
            else:
                release.state = TeamRecordState.BLOCKED
                release.error = normalized_error or "release publication failed"
                result["error"] = release.error
                recipient_id = _release_failure_recipient(state_model, release)
                if recipient_id is not None and recipient_id in state_model.agents:
                    inbox_id = f"inbox-{uuid4().hex}"
                    state_model.inbox[inbox_id] = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=recipient_id,
                        sender_agent_id=None,
                        item_type=TeamInboxItemType.PROGRESS_REPORT,
                        payload={
                            "kind": "release_publish_failed",
                            "release_package_id": release.id,
                            "project_id": release.project_id,
                            "error": release.error,
                        },
                    )
                    result["created_inbox_item_ids"].append(inbox_id)
            release.updated_at = now
            _recalculate_project_status(state_model, release.project_id)
            result["state"] = release.state.value
            result["success"] = True
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result


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


def _remove_value(values: list[str], value: str) -> list[str]:
    return [item for item in values if item != value]


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _required_mapping(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if field_name not in payload:
        raise ValueError(f"{field_name} is required")
    return _mapping(payload[field_name], field_name)


def _materialize_role_inputs(
    template: TeamTemplate,
    inputs: Mapping[str, Any],
    team_input: Mapping[str, Any],
    project_input: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    nested_agents: Mapping[str, Any] | None = None
    if "roles" in inputs:
        nested_agents = _mapping(inputs["roles"], "roles")
    elif "agents" in inputs:
        nested_agents = _mapping(inputs["agents"], "agents")

    role_inputs: dict[str, dict[str, Any]] = {}
    for role in template.roles:
        raw_role_input: object | None = None
        if role.key in inputs and role.key not in {"team", "project", "roles", "agents"}:
            raw_role_input = inputs[role.key]
        elif nested_agents is not None and role.key in nested_agents:
            raw_role_input = nested_agents[role.key]
        elif role.key == "manager" and (
            "manager_agent_id" in team_input or "manager_session_id" in team_input
        ):
            raw_role_input = {
                "agent_id": team_input.get("manager_agent_id"),
                "session_id": team_input.get("manager_session_id"),
            }
        elif role.key == "project_lead" and (
            "lead_agent_id" in project_input or "lead_session_id" in project_input
        ):
            raw_role_input = {
                "agent_id": project_input.get("lead_agent_id"),
                "session_id": project_input.get("lead_session_id"),
            }
        if raw_role_input is None:
            raise ValueError(f"{role.key} is required")
        role_inputs[role.key] = _mapping(raw_role_input, role.key)
        role_input = role_inputs[role.key]
        role_input["agent_id"] = _required_text(
            role_input.get("agent_id", role_input.get("id")),
            f"{role.key}.agent_id",
        )
        role_input["session_id"] = _required_text(
            role_input.get("session_id"),
            f"{role.key}.session_id",
        )
    return role_inputs


def _project_git_config(value: object) -> ProjectGitConfig | None:
    if value is None:
        return None
    git_payload = _mapping(value, "project.git")
    host_repo_path = _optional_text(git_payload.get("host_repo_path"))
    if host_repo_path is None:
        return None
    return ProjectGitConfig.model_validate({**git_payload, "host_repo_path": host_repo_path})


def _materialized_template_record_id(project_id: str, template_record_id: str) -> str:
    return f"{project_id}-{template_record_id}"


def _ordered_template_roles(template: TeamTemplate) -> list[Any]:
    ordered: list[Any] = []
    pending = list(template.roles)
    while pending:
        progressed = False
        for role in list(pending):
            if role.reports_to_role_key is None or role.reports_to_role_key in {
                item.key for item in ordered
            }:
                ordered.append(role)
                pending.remove(role)
                progressed = True
        if not progressed:
            unresolved = ", ".join(sorted(role.key for role in pending))
            raise ValueError(
                f"template {template.id} has unresolved role ordering: {unresolved}"
            )
    if ordered[0].key != template.root_role_key:
        raise ValueError(
            f"template {template.id} must start role ordering with {template.root_role_key}"
        )
    return ordered


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


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _active_tasks_for_agent(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal = {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}
    tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id and task.state not in terminal
    ]
    tasks.sort(key=lambda item: (item.priority, item.created_at, item.id))
    return tasks


def _mark_agent_work_due(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> None:
    runtime = state.agent_runtimes.setdefault(agent_id, AgentRuntime(agent_id=agent_id))
    runtime.no_progress_count = 0
    runtime.next_wake_at = None
    runtime.updated_at = now


def _append_action_item_error(
    result: dict[str, Any],
    field_name: str,
    index: int,
    error: ValueError,
) -> None:
    result["errors"].append(f"{field_name}[{index}]: {error}")


def _pending_inbox_for_agent(state: TeamState, agent_id: str) -> list[AgentInboxItem]:
    items = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
    ]
    items.sort(key=lambda item: (item.created_at, item.id))
    return items


def _resolve_action_agent(
    state: TeamState,
    *,
    action: dict[str, Any],
    session_id: str | None,
) -> PersistentAgent:
    action_agent_id = _optional_text(action.get("agent_id"))
    if action_agent_id:
        return _require_present(state.agents, action_agent_id, "agent")
    if session_id:
        for agent in state.agents.values():
            if agent.session_id == session_id:
                return agent
    raise ValueError("agent action requires agent_id or a known session_id")


def _mark_action_failure(
    state: TeamState,
    *,
    runtime: AgentRuntime,
    agent_id: str,
    dispatch_id: str,
    action: dict[str, Any],
    error: str,
    action_id: str,
    result: dict[str, Any],
) -> None:
    runtime.active_dispatch_id = None
    runtime.wake_leased_until = None
    runtime.wake_reason = None
    runtime.no_progress_count += 1
    runtime.updated_at = utc_now()
    state.action_records[action_id] = AgentActionRecord(
        id=action_id,
        agent_id=agent_id,
        dispatch_id=dispatch_id,
        action=action,
        success=False,
        error=error,
    )
    result["errors"].append(error)


def _is_direct_subordinate(actor: PersistentAgent, agent_id: str) -> bool:
    return agent_id in actor.subordinate_agent_ids


def _is_team_manager(state: TeamState, actor: PersistentAgent) -> bool:
    if actor.reports_to_agent_id is not None:
        return False
    team = state.teams.get(actor.team_id)
    return team is not None and team.manager_agent_id == actor.id


def _is_project_lead_for(state: TeamState, agent_id: str, project_id: str) -> bool:
    project = state.projects.get(project_id)
    return project is not None and project.lead_agent_id == agent_id


def _agent_in_project_hierarchy(state: TeamState, agent_id: str, project_id: str) -> bool:
    agent = state.agents.get(agent_id)
    return agent is not None and agent.project_id == project_id


def _upward_recipient_error(
    agent: PersistentAgent,
    recipient_id: str | None,
    kind: str,
) -> str | None:
    if agent.reports_to_agent_id is None:
        return (
            f"agent {agent.id} is a top-level manager and must use operator_escalations "
            f"instead of {kind}"
        )
    if recipient_id != agent.reports_to_agent_id:
        return (
            f"agent {agent.id} can only send {kind} to its direct superior "
            f"{agent.reports_to_agent_id}"
        )
    return None


def _plan_task_creation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    creation: dict[str, Any],
    result: dict[str, Any],
    reserved_task_ids: set[str],
) -> _TaskCreationPlan | None:
    owner_id = _required_text(creation.get("owner_agent_id"), "owner_agent_id")
    project_id = _required_text(creation.get("project_id"), "project_id")
    owner = state.agents.get(owner_id)
    if owner is None:
        result["errors"].append(f"task creation references missing owner {owner_id}")
        return None
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"task creation references missing project {project_id}")
        return None
    if owner.team_id != actor.team_id or project.team_id != actor.team_id:
        result["errors"].append(
            f"agent {actor.id} cannot create tasks outside its team"
        )
        return None
    actor_can_target_project = _is_project_lead_for(
        state, actor.id, project_id
    ) or _is_team_manager(state, actor)
    if owner.project_id != project_id and not actor_can_target_project:
        result["errors"].append(
            f"agent {actor.id} cannot create a task for {owner_id} outside project {project_id}"
        )
        return None
    permitted = (
        owner.id == actor.id
        or _is_direct_subordinate(actor, owner.id)
        or (
            _is_project_lead_for(state, actor.id, project_id)
            and _agent_in_project_hierarchy(state, owner.id, project_id)
        )
        or (
            _is_team_manager(state, actor)
            and _is_project_lead_for(state, owner.id, project_id)
        )
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to create tasks for {owner_id}"
        )
        return None
    raw_state = _optional_text(creation.get("state"))
    if raw_state is not None:
        try:
            task_state = TeamTaskState(raw_state)
        except ValueError:
            result["errors"].append(f"task creation has invalid state {raw_state}")
            return None
        if task_state not in _NON_TERMINAL_TASK_STATES:
            result["errors"].append(
                f"task creation cannot start in terminal state {raw_state}"
            )
            return None
    else:
        task_state = TeamTaskState.PENDING
    explicit_task_id = _optional_text(creation.get("task_id"))
    legacy_task_id = _optional_text(creation.get("id"))
    if explicit_task_id is not None and legacy_task_id is not None and explicit_task_id != legacy_task_id:
        result["errors"].append("task creation has conflicting task_id and id")
        return None
    task_id = explicit_task_id or legacy_task_id or f"task-{uuid4().hex}"
    if task_id in state.tasks or task_id in reserved_task_ids:
        result["errors"].append(f"task {task_id} already exists")
        return None
    parent_task_id = _optional_text(creation.get("parent_task_id"))
    dependency_task_ids = _string_list(
        creation.get("dependency_task_ids"), "dependency_task_ids"
    )
    priority = creation.get("priority")
    return _TaskCreationPlan(
        task_id=task_id,
        owner_id=owner_id,
        project_id=project_id,
        task_state=task_state,
        parent_task_id=parent_task_id,
        dependency_task_ids=dependency_task_ids,
        priority=int(priority) if priority is not None else 0,
        title=_required_text(creation.get("title"), "title"),
        description=_required_text(creation.get("description"), "description"),
    )


def _apply_task_creation_plan(
    state: TeamState,
    *,
    plan: _TaskCreationPlan,
    actor: PersistentAgent,
) -> str:
    now = utc_now()
    state.tasks[plan.task_id] = AgentTask(
        id=plan.task_id,
        owner_agent_id=plan.owner_id,
        project_id=plan.project_id,
        priority=plan.priority,
        state=plan.task_state,
        title=plan.title,
        description=plan.description,
        created_by_agent_id=actor.id,
        parent_task_id=plan.parent_task_id,
        dependency_task_ids=plan.dependency_task_ids,
        created_at=now,
        updated_at=now,
    )
    if plan.task_state in _NON_TERMINAL_TASK_STATES:
        _mark_agent_work_due(state, agent_id=plan.owner_id, now=now)
    return plan.task_id


def _task_creation_batch_graph_errors(
    state: TeamState,
    plans: list[_TaskCreationPlan],
) -> dict[str, str]:
    errors: dict[str, str] = {}
    while True:
        valid_plans = [plan for plan in plans if plan.task_id not in errors]
        if not valid_plans:
            return errors
        task_projects = {task_id: task.project_id for task_id, task in state.tasks.items()}
        parent_map = {
            task_id: task.parent_task_id for task_id, task in state.tasks.items()
        }
        dependency_map = {
            task_id: list(task.dependency_task_ids)
            for task_id, task in state.tasks.items()
        }
        for plan in valid_plans:
            task_projects[plan.task_id] = plan.project_id
            parent_map[plan.task_id] = plan.parent_task_id
            dependency_map[plan.task_id] = list(plan.dependency_task_ids)

        next_errors: dict[str, str] = {}
        for plan in valid_plans:
            graph_error = _task_creation_plan_graph_error(
                plan,
                task_projects=task_projects,
                parent_map=parent_map,
                dependency_map=dependency_map,
            )
            if graph_error is not None:
                next_errors[plan.task_id] = graph_error
        if not next_errors:
            return errors
        for task_id, graph_error in next_errors.items():
            errors.setdefault(task_id, graph_error)


def _task_creation_plan_graph_error(
    plan: _TaskCreationPlan,
    *,
    task_projects: dict[str, str],
    parent_map: dict[str, str | None],
    dependency_map: dict[str, list[str]],
) -> str | None:
    if plan.parent_task_id == plan.task_id:
        return "task cannot be its own parent"
    if plan.task_id in plan.dependency_task_ids:
        return "task cannot depend on itself"
    if plan.parent_task_id is not None:
        parent_project_id = task_projects.get(plan.parent_task_id)
        if parent_project_id is None:
            return f"task references missing parent {plan.parent_task_id}"
        if parent_project_id != plan.project_id:
            return f"task parent {plan.parent_task_id} belongs to project {parent_project_id}"
    for dependency_id in plan.dependency_task_ids:
        dependency_project_id = task_projects.get(dependency_id)
        if dependency_project_id is None:
            return f"task references missing dependency {dependency_id}"
        if dependency_project_id != plan.project_id:
            return (
                f"task dependency {dependency_id} belongs to project "
                f"{dependency_project_id}"
            )
    current = plan.parent_task_id
    seen: set[str] = set()
    while current is not None:
        if current == plan.task_id:
            return f"task parent cycle includes {plan.task_id}"
        if current in seen:
            break
        seen.add(current)
        current = parent_map.get(current)
    for dependency_id in plan.dependency_task_ids:
        if _dependency_path_exists(
            dependency_map,
            start_task_id=dependency_id,
            target_task_id=plan.task_id,
        ):
            return f"task dependency cycle includes {plan.task_id}"
    return None


def _dependency_path_exists(
    dependency_map: dict[str, list[str]],
    *,
    start_task_id: str,
    target_task_id: str,
) -> bool:
    stack = [start_task_id]
    seen: set[str] = set()
    while stack:
        task_id = stack.pop()
        if task_id == target_task_id:
            return True
        if task_id in seen:
            continue
        seen.add(task_id)
        stack.extend(dependency_map.get(task_id, []))
    return False


def _apply_operator_escalation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    escalation: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    if not _is_team_manager(state, actor):
        result["errors"].append(
            f"agent {actor.id} is not permitted to send operator escalations"
        )
        return False
    title = _required_text(escalation.get("title"), "title")
    message = _required_text(escalation.get("message"), "message")
    urgency = _optional_text(escalation.get("urgency")) or "normal"
    project_id = _optional_text(escalation.get("project_id"))
    task_id = _optional_text(escalation.get("task_id"))
    team = state.teams.get(actor.team_id)
    target = (
        _optional_text(team.operator_communication_target_id) if team is not None else None
    )
    if not target:
        result["errors"].append(
            f"team {actor.team_id} has no operator_communication_target_id for escalations"
        )
        return False
    escalation_id = f"escalation-{uuid4().hex}"
    payload: dict[str, Any] = {
        "team_id": actor.team_id,
        "manager_agent_id": actor.id,
        "communication_target_id": target,
        "title": title,
        "message": message,
        "urgency": urgency,
    }
    if project_id is not None:
        payload["project_id"] = project_id
    if task_id is not None:
        payload["task_id"] = task_id
    inbox_id = f"inbox-{uuid4().hex}"
    state.inbox[inbox_id] = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=actor.id,
        sender_agent_id=actor.id,
        task_id=task_id,
        item_type=TeamInboxItemType.ESCALATION,
        payload={"escalation_id": escalation_id, **payload},
    )
    result["created_inbox_item_ids"].append(inbox_id)
    result["events"].append(
        {
            "event_type": OPERATOR_ESCALATION_EVENT_TYPE,
            "correlation_key": escalation_id,
            "idempotency_key": (
                f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:{escalation_id}"
            ),
            "payload": payload,
        }
    )
    result["operator_escalation_event_ids"].append(escalation_id)
    return True


def _supervises(state: TeamState, supervisor_id: str, agent_id: str) -> bool:
    """Return True if supervisor_id is an ancestor of agent_id in the reporting chain."""

    seen: set[str] = set()
    current = state.agents.get(agent_id)
    while current is not None and current.reports_to_agent_id is not None:
        superior_id = current.reports_to_agent_id
        if superior_id in seen:
            break
        seen.add(superior_id)
        if superior_id == supervisor_id:
            return True
        current = state.agents.get(superior_id)
    return False


def _has_dependency_cycle(dependency_map: dict[str, list[str]]) -> bool:
    """Iterative DFS mirroring TeamState._validate_no_dependency_cycle."""

    visited: set[str] = set()
    visiting: set[str] = set()
    for start in dependency_map:
        if start in visited:
            continue
        stack: list[str] = [start]
        while stack:
            node = stack[-1]
            visiting.add(node)
            advanced = False
            for dependency_id in dependency_map.get(node, []):
                if dependency_id not in dependency_map:
                    continue
                if dependency_id in visited:
                    continue
                if dependency_id in visiting:
                    return True
                stack.append(dependency_id)
                advanced = True
                break
            if not advanced:
                visiting.discard(node)
                visited.add(node)
                stack.pop()
    return False


def _task_graph_edit_error(
    state: TeamState,
    *,
    task_id: str | None,
    project_id: str,
    parent_task_id: str | None,
    dependency_task_ids: list[str],
) -> str | None:
    """Pre-check the Step 1 task-graph invariants for a proposed create/edit.

    Mirrors TeamState._validate_task_graph but simulates the proposed edge(s) so
    apply_agent_action can reject the item instead of letting the post-mutation
    re-validation abort the whole action. ``task_id`` is None for a new task.
    """

    if task_id is not None:
        if parent_task_id == task_id:
            return "task cannot be its own parent"
        if task_id in dependency_task_ids:
            return "task cannot depend on itself"
    if parent_task_id is not None:
        parent = state.tasks.get(parent_task_id)
        if parent is None:
            return f"task references missing parent {parent_task_id}"
        if parent.project_id != project_id:
            return (
                f"task parent {parent_task_id} belongs to project {parent.project_id}"
            )
    for dependency_id in dependency_task_ids:
        dependency = state.tasks.get(dependency_id)
        if dependency is None:
            return f"task references missing dependency {dependency_id}"
        if dependency.project_id != project_id:
            return (
                f"task dependency {dependency_id} belongs to project "
                f"{dependency.project_id}"
            )
    # Walk up the parent chain from the proposed parent; reaching task_id is a cycle.
    if task_id is not None and parent_task_id is not None:
        seen: set[str] = set()
        current: str | None = parent_task_id
        while current is not None:
            if current == task_id:
                return f"task parent cycle includes {task_id}"
            if current in seen:
                break
            seen.add(current)
            current_task = state.tasks.get(current)
            current = current_task.parent_task_id if current_task is not None else None
    # Simulate the dependency graph with the proposed edges and look for a cycle.
    if task_id is not None:
        dependency_map = {
            existing_id: list(task.dependency_task_ids)
            for existing_id, task in state.tasks.items()
        }
        dependency_map[task_id] = list(dependency_task_ids)
        if _has_dependency_cycle(dependency_map):
            return f"task dependency cycle includes {task_id}"
    return None


def _review_profile_for_request(
    state: TeamState,
    *,
    profile_id: str | None,
    expected_team_id: str,
    project_id: str | None,
    reviewer: PersistentAgent,
) -> tuple[ReviewProfile | None, str | None]:
    if profile_id is None:
        return None, None
    profile = state.review_profiles.get(profile_id)
    if profile is None:
        return None, f"review request references missing review profile {profile_id}"
    if profile.archived:
        return None, f"review profile {profile_id} is archived"
    if profile.team_id != expected_team_id:
        return None, f"review profile {profile_id} is not in the same team as reviewer {reviewer.id}"
    if profile.project_id is not None and profile.project_id != project_id:
        return None, f"review profile {profile_id} does not apply to project {project_id}"
    if profile.applicable_roles and reviewer.role not in profile.applicable_roles:
        return None, f"review profile {profile_id} does not apply to reviewer role {reviewer.role}"
    return profile, None


def _apply_review_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        result["errors"].append(f"review request references missing deliverable {deliverable_id}")
        return None
    reviewer_id = _required_text(request.get("reviewer_agent_id"), "reviewer_agent_id")
    reviewer = state.agents.get(reviewer_id)
    if reviewer is None:
        result["errors"].append(f"review request references missing reviewer {reviewer_id}")
        return None
    if reviewer.team_id != actor.team_id:
        result["errors"].append(
            f"reviewer {reviewer_id} is not in the same team as {actor.id}"
        )
        return None
    task = state.tasks.get(deliverable.task_id)
    project_id = task.project_id if task is not None else None
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or (project_id is not None and _is_project_lead_for(state, actor.id, project_id))
        or _is_team_manager(state, actor)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request review of deliverable {deliverable_id}"
        )
        return None
    instructions = _optional_text(request.get("instructions"))
    profile_id = _optional_text(request.get("review_profile_id"))
    profile, profile_error = _review_profile_for_request(
        state,
        profile_id=profile_id,
        expected_team_id=actor.team_id,
        project_id=project_id,
        reviewer=reviewer,
    )
    if profile_error is not None:
        result["errors"].append(profile_error)
        return None
    review_id = _optional_text(request.get("id")) or f"review-{uuid4().hex}"
    if review_id in state.review_requests:
        result["errors"].append(f"review request {review_id} already exists")
        return None
    now = utc_now()
    review = ReviewRequest(
        id=review_id,
        deliverable_id=deliverable_id,
        reviewer_agent_id=reviewer_id,
        state=TeamRecordState.PENDING,
        review_profile_id=profile_id,
        instructions=instructions,
        created_at=now,
    )
    state.review_requests[review.id] = review
    deliverable.review_request_ids = _append_unique(
        deliverable.review_request_ids,
        review.id,
    )
    payload: dict[str, Any] = {
        "kind": "review_request",
        "review_request_id": review.id,
        "deliverable_id": deliverable_id,
        "instructions": instructions or "",
    }
    if profile is not None:
        payload["review_profile"] = {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "criteria": [
                {
                    "id": criterion.id,
                    "label": criterion.label,
                    "description": criterion.description,
                    "required": criterion.required,
                }
                for criterion in profile.criteria
            ],
        }
    inbox_id = f"inbox-{uuid4().hex}"
    state.inbox[inbox_id] = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=reviewer_id,
        sender_agent_id=actor.id,
        task_id=deliverable.task_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        payload=payload,
    )
    result["created_inbox_item_ids"].append(inbox_id)
    return review.id


def _apply_review_decision(
    state: TeamState,
    *,
    actor: PersistentAgent,
    decision: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    review_request_id = _required_text(
        decision.get("review_request_id"), "review_request_id"
    )
    review = state.review_requests.get(review_request_id)
    if review is None:
        result["errors"].append(
            f"review decision references missing review request {review_request_id}"
        )
        return None
    if review.reviewer_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} is not the assigned reviewer for {review_request_id}"
        )
        return None
    raw_state = _required_text(decision.get("state"), "state")
    try:
        decision_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"review decision has invalid state {raw_state}")
        return None
    if not _is_review_decision_state(decision_state):
        result["errors"].append(
            f"review decision state {raw_state} must be accepted, rejected, or blocked"
        )
        return None
    criterion_results, criteria_error = _build_criterion_results(
        state,
        review,
        _list_of_mappings(decision.get("criteria"), "criteria"),
    )
    if criteria_error is not None:
        result["errors"].append(criteria_error)
        return None
    if criterion_results is not None:
        review.criteria = criterion_results
    _cascade_review_decision(
        state,
        review=review,
        decision_state=decision_state,
        findings=_string_list(decision.get("findings"), "findings"),
        projects_to_recalc=projects_to_recalc,
    )
    return review.id


def _build_criterion_results(
    state: TeamState,
    review: ReviewRequest,
    raw_criteria: list[dict[str, Any]],
) -> tuple[list[ReviewCriterionResult] | None, str | None]:
    """Validate criterion-result mappings against the review's profile.

    Returns ``(results, None)`` on success or ``(None, error_message)`` so callers
    can append the error to ``result["errors"]`` without mutating state. ``results``
    is an empty list when no criteria were supplied.
    """

    if not raw_criteria:
        return [], None
    profile = (
        state.review_profiles.get(review.review_profile_id)
        if review.review_profile_id is not None
        else None
    )
    if profile is None:
        return None, (
            f"review decision for {review.id} has criterion results but no review profile"
        )
    valid_ids = {criterion.id for criterion in profile.criteria}
    seen: set[str] = set()
    results: list[ReviewCriterionResult] = []
    for entry in raw_criteria:
        criterion_id = _optional_text(entry.get("criterion_id"))
        if criterion_id is None:
            return None, "review decision criterion result is missing criterion_id"
        if criterion_id not in valid_ids:
            return None, (
                f"review decision for {review.id} references unknown criterion {criterion_id}"
            )
        if criterion_id in seen:
            return None, (
                f"review decision for {review.id} has duplicate criterion result {criterion_id}"
            )
        seen.add(criterion_id)
        raw_state = _optional_text(entry.get("state"))
        if raw_state not in {"passed", "failed", "skipped"}:
            return None, (
                f"review decision criterion {criterion_id} has invalid state {raw_state}"
            )
        results.append(
            ReviewCriterionResult(
                criterion_id=criterion_id,
                state=raw_state,
                finding=_optional_text(entry.get("finding")) or "",
            )
        )
    return results, None


def _is_review_decision_state(decision_state: TeamRecordState) -> bool:
    return decision_state in {
        TeamRecordState.ACCEPTED,
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }


def _cascade_review_decision(
    state: TeamState,
    *,
    review: ReviewRequest,
    decision_state: TeamRecordState,
    findings: list[str],
    projects_to_recalc: set[str],
) -> None:
    """Apply a review decision and cascade it to the deliverable, task, and project.

    This is the single source of truth for review decision cascade rules, shared by
    the ``review_decisions`` agent action and the Admin API decision endpoint.
    """

    review.state = decision_state
    review.findings = findings
    review.decision_at = utc_now()
    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    task = _require_present(state.tasks, deliverable.task_id, "task")
    now = utc_now()
    if decision_state == TeamRecordState.ACCEPTED:
        all_accepted = all(
            state.review_requests[review_id].state == TeamRecordState.ACCEPTED
            for review_id in deliverable.review_request_ids
        )
        if all_accepted:
            deliverable.state = TeamRecordState.ACCEPTED
            task_deliverables = [
                state.deliverables[deliverable_id]
                for deliverable_id in task.deliverable_ids
                if deliverable_id in state.deliverables
            ]
            if task_deliverables and all(
                item.state == TeamRecordState.ACCEPTED for item in task_deliverables
            ):
                task.state = TeamTaskState.ACCEPTED
                task.updated_at = now
    elif decision_state == TeamRecordState.REJECTED:
        deliverable.state = TeamRecordState.REJECTED
        task.state = TeamTaskState.BLOCKED
        task.updated_at = now
    else:  # blocked
        deliverable.state = TeamRecordState.BLOCKED
        task.state = TeamTaskState.BLOCKED
        task.updated_at = now
    projects_to_recalc.add(task.project_id)


def _apply_goal_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(update.get("project_id"), "project_id")
    goal_id = _required_text(update.get("goal_id"), "goal_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"goal update references missing project {project_id}")
        return None
    permitted = _is_project_lead_for(state, actor.id, project_id) or (
        _is_team_manager(state, actor) and project.team_id == actor.team_id
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to update goals on project {project_id}"
        )
        return None
    raw_state = _required_text(update.get("state"), "state")
    try:
        goal_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"goal update has invalid state {raw_state}")
        return None
    goal = next((item for item in project.goals if item.id == goal_id), None)
    if goal is None:
        result["errors"].append(
            f"goal {goal_id} does not exist on project {project_id}"
        )
        return None
    goal.state = goal_state
    project.updated_at = utc_now()
    projects_to_recalc.add(project_id)
    return goal_id


def _project_git_or_error(
    project: Project,
    result: dict[str, Any],
) -> Any:
    if project.git is None:
        result["errors"].append(
            f"project {project.id} has no git configuration for promotions"
        )
        return None
    return project.git


def _bare_repo_fields(git: Any) -> dict[str, str]:
    if git.bare_repo_name:
        return {"bare_repo_name": git.bare_repo_name}
    return {"bare_repo_path": git.bare_repo_path}


def _emit_git_promotion_event(
    result: dict[str, Any],
    *,
    promotion_id: str,
    payload: dict[str, Any],
) -> None:
    result["events"].append(
        {
            "event_type": GIT_PROMOTION_EVENT_TYPE,
            "correlation_key": promotion_id,
            "idempotency_key": f"team-addon:{GIT_PROMOTION_EVENT_TYPE}:{promotion_id}",
            "payload": payload,
        }
    )


def _apply_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    assignment_id = _required_text(request.get("assignment_id"), "assignment_id")
    assignment = state.ephemeral_assignments.get(assignment_id)
    if assignment is None:
        result["errors"].append(
            f"git review promotion references missing ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.parent_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} cannot promote ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.state not in {
        EphemeralAssignmentState.COMPLETED,
        EphemeralAssignmentState.ACCEPTED,
    }:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} is not completed or accepted"
        )
        return None
    if not assignment.agent_branch:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} has no agent_branch to promote"
        )
        return None
    deliverable_id = _optional_text(request.get("deliverable_id"))
    if deliverable_id is not None:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(
                f"git review promotion references missing deliverable {deliverable_id}"
            )
            return None
        deliverable_task = state.tasks.get(deliverable.task_id)
        if (
            deliverable_task is None
            or deliverable_task.id != assignment.task_id
            or deliverable_task.project_id != assignment.project_id
        ):
            result["errors"].append(
                f"deliverable {deliverable_id} does not belong to ephemeral assignment {assignment_id}"
            )
            return None
    project = state.projects.get(assignment.project_id)
    if project is None:
        result["errors"].append(
            f"git review promotion references missing project {assignment.project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    source_branch = assignment.agent_branch
    target_branch = review_branch(actor.team_id, assignment_id)
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=assignment.task_id,
        deliverable_id=deliverable_id,
        actor_agent_id=actor.id,
        source_branch=source_branch,
        target_branch=target_branch,
        promotion_kind="review",
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "review",
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "assignment_id": assignment_id,
    }
    payload.update(_bare_repo_fields(git))
    if deliverable_id is not None:
        payload["deliverable_id"] = deliverable_id
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _apply_git_feature_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    task_id = _required_text(update.get("task_id"), "task_id")
    task = state.tasks.get(task_id)
    if task is None:
        result["errors"].append(
            f"git feature update references missing task {task_id}"
        )
        return None
    if task.owner_agent_id != actor.id and not _supervises(
        state, actor.id, task.owner_agent_id
    ):
        result["errors"].append(
            f"agent {actor.id} is not permitted to consolidate task {task_id}"
        )
        return None
    project = state.projects.get(task.project_id)
    if project is None:
        result["errors"].append(
            f"git feature update references missing project {task.project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    raw_sources = _string_list(
        update.get("source_review_branches"), "source_review_branches"
    )
    if not raw_sources:
        result["errors"].append(
            f"git feature update for task {task_id} requires source_review_branches"
        )
        return None
    try:
        source_branches = [validate_branch_name(branch) for branch in raw_sources]
        target_branch = validate_branch_name(
            _optional_text(update.get("feature_branch"))
            or feature_branch(actor.team_id, task_id, actor.id)
        )
    except ValueError as exc:
        result["errors"].append(f"git feature update has invalid branch name: {exc}")
        return None
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=task_id,
        actor_agent_id=actor.id,
        source_branch=source_branches[0],
        target_branch=target_branch,
        promotion_kind="feature",
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "feature",
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "base_branch": git.base_branch,
        "source_branches": source_branches,
        "target_branch": target_branch,
        "task_id": task_id,
    }
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _apply_git_release_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(request.get("project_id"), "project_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(
            f"git release request references missing project {project_id}"
        )
        return None
    permitted = _is_project_lead_for(state, actor.id, project_id) or (
        _is_team_manager(state, actor) and project.team_id == actor.team_id
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request releases on project {project_id}"
        )
        return None
    return _build_release_package(
        state,
        project=project,
        request=request,
        result=result,
        projects_to_recalc=projects_to_recalc,
        actor_agent_id=actor.id,
        team_id=actor.team_id,
    )


def _build_release_package(
    state: TeamState,
    *,
    project: Project,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
    actor_agent_id: str,
    team_id: str,
) -> str | None:
    """Validate deliverables and build a pending ReleasePackage with git promotions.

    Shared by the ``git_release_requests`` agent action and the Admin API manual
    release-package endpoint so both enforce identical validation and branch naming.
    """

    git = _project_git_or_error(project, result)
    if git is None:
        return None
    project_id = project.id
    deliverable_ids = _string_list(request.get("deliverable_ids"), "deliverable_ids")
    if not deliverable_ids:
        result["errors"].append(
            f"git release request for project {project_id} requires deliverable_ids"
        )
        return None
    artifact_refs: list[str] = []
    feature_branches: list[str] = []
    valid = True
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(
                f"git release request references missing deliverable {deliverable_id}"
            )
            valid = False
            continue
        task = state.tasks.get(deliverable.task_id)
        if task is None or task.project_id != project_id:
            result["errors"].append(
                f"deliverable {deliverable_id} does not belong to project {project_id}"
            )
            valid = False
            continue
        if deliverable.state != TeamRecordState.ACCEPTED:
            result["errors"].append(
                f"deliverable {deliverable_id} is not accepted and cannot be released"
            )
            valid = False
            continue
        feature = deliverable.git_refs.feature_branch
        if feature:
            feature_branches = _append_unique(feature_branches, feature)
        elif not deliverable.artifact_refs:
            result["errors"].append(
                f"deliverable {deliverable_id} has neither a feature branch nor artifact refs"
            )
            valid = False
        for artifact_ref in deliverable.artifact_refs:
            artifact_refs = _append_unique(artifact_refs, artifact_ref)
    if not valid:
        return None
    release_id = _optional_text(request.get("id")) or f"release-{uuid4().hex}"
    if release_id in state.release_packages:
        result["errors"].append(f"release package {release_id} already exists")
        return None
    target_release_branch = validate_branch_name(
        _optional_text(request.get("release_branch"))
        or release_branch(team_id, release_id)
    )
    target_canonical_branch = validate_branch_name(
        _optional_text(request.get("canonical_branch"))
        or canonical_release_branch(project, release_id)
    )
    promotion_ids: list[str] = []
    now = utc_now()
    # Internal feature branches are consolidated into a release branch, which is then
    # pushed to the canonical remote after consolidation succeeds. Only create git
    # promotions when there is at least one feature branch to consolidate; pure
    # artifact deliverables need no push.
    if feature_branches:
        release_promotion_id = f"promotion-{uuid4().hex}"
        state.git_promotions[release_promotion_id] = GitPromotionRecord(
            id=release_promotion_id,
            project_id=project.id,
            release_package_id=release_id,
            actor_agent_id=actor_agent_id,
            source_branch=feature_branches[0],
            target_branch=target_release_branch,
            promotion_kind="release",
        )
        promotion_ids.append(release_promotion_id)
    state.release_packages[release_id] = ReleasePackage(
        id=release_id,
        project_id=project.id,
        deliverable_ids=deliverable_ids,
        artifact_refs=artifact_refs,
        feature_branches=feature_branches,
        release_branch=target_release_branch,
        canonical_branch=target_canonical_branch,
        git_promotion_record_ids=promotion_ids,
        state=TeamRecordState.PENDING,
        created_at=now,
        updated_at=now,
    )
    if feature_branches:
        _emit_git_promotion_event(
            result,
            promotion_id=promotion_ids[0],
            payload={
                "promotion_id": promotion_ids[0],
                "promotion_kind": "release",
                "project_id": project.id,
                "release_package_id": release_id,
                "host_repo_path": git.host_repo_path,
                "base_branch": git.base_branch,
                "source_branches": list(feature_branches),
                "target_branch": target_release_branch,
            },
        )
        result["git_promotion_event_ids"].extend(promotion_ids)
    projects_to_recalc.add(project_id)
    return release_id


def _attach_promotion_success(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    commit_sha: str | None,
    result: dict[str, Any],
) -> None:
    """Attach a succeeded promotion's branch + record id to its linked records."""

    kind = promotion.promotion_kind
    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
    )
    if deliverable is not None:
        refs = deliverable.git_refs
        if kind == "review":
            refs.review_branch = promotion.target_branch
            refs.agent_branch = refs.agent_branch or promotion.source_branch
        elif kind == "feature":
            refs.feature_branch = promotion.target_branch
        elif kind == "release":
            refs.release_branch = promotion.target_branch
        if commit_sha is not None:
            refs.commit_sha = commit_sha
        refs.promotion_record_ids = _append_unique(refs.promotion_record_ids, promotion.id)
    elif kind == "feature" and promotion.task_id is not None and promotion.task_id in state.tasks:
        # A feature consolidation has no deliverable yet; capture the consolidated
        # branch on a new deliverable so later release requests can find it.
        task = state.tasks[promotion.task_id]
        deliverable_id = f"deliverable-{uuid4().hex}"
        new_deliverable = Deliverable(
            id=deliverable_id,
            task_id=task.id,
            producer_agent_id=promotion.actor_agent_id,
            summary=f"Consolidated feature branch {promotion.target_branch}",
            state=TeamRecordState.PENDING,
            git_refs=DeliverableGitRefs(
                feature_branch=promotion.target_branch,
                commit_sha=commit_sha,
                promotion_record_ids=[promotion.id],
            ),
        )
        state.deliverables[deliverable_id] = new_deliverable
        task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable_id)
        task.updated_at = utc_now()
        promotion.deliverable_id = deliverable_id
        result["created_deliverable_ids"].append(deliverable_id)

    release = (
        state.release_packages.get(promotion.release_package_id)
        if promotion.release_package_id is not None
        else None
    )
    if release is not None:
        release.git_promotion_record_ids = _append_unique(
            release.git_promotion_record_ids,
            promotion.id,
        )
        if kind == "release":
            release.release_branch = release.release_branch or promotion.target_branch
            _queue_canonical_push_after_release_success(
                state,
                release=release,
                release_promotion=promotion,
                result=result,
            )
        elif kind == "canonical_push":
            release.canonical_branch = release.canonical_branch or promotion.target_branch


def _queue_canonical_push_after_release_success(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    project = state.projects.get(release_promotion.project_id)
    git = project.git if project is not None else None
    if project is None or git is None:
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"project {release_promotion.project_id} has no git configuration "
            "for canonical release push"
        )
        result["error"] = release.error
        return

    source_branch = release.release_branch or release_promotion.target_branch
    target_branch = release.canonical_branch or canonical_release_branch(project, release.id)
    existing = next(
        (
            state.git_promotions[promotion_id]
            for promotion_id in release.git_promotion_record_ids
            if promotion_id in state.git_promotions
            and state.git_promotions[promotion_id].promotion_kind == "canonical_push"
        ),
        None,
    )
    if existing is None:
        existing = GitPromotionRecord(
            id=f"promotion-{uuid4().hex}",
            project_id=project.id,
            release_package_id=release.id,
            actor_agent_id=release_promotion.actor_agent_id,
            source_branch=source_branch,
            target_branch=target_branch,
            promotion_kind="canonical_push",
        )
        state.git_promotions[existing.id] = existing
        release.git_promotion_record_ids = _append_unique(
            release.git_promotion_record_ids,
            existing.id,
        )
    release.canonical_branch = target_branch

    if existing.state != "pending":
        return

    payload: dict[str, Any] = {
        "promotion_id": existing.id,
        "promotion_kind": "canonical_push",
        "project_id": project.id,
        "release_package_id": release.id,
        "host_repo_path": git.host_repo_path,
        "base_branch": git.base_branch,
        "source_branch": existing.source_branch,
        "target_branch": existing.target_branch,
    }
    if git.canonical_origin_url is not None:
        payload["canonical_origin_url"] = git.canonical_origin_url
    if git.provider is not None:
        payload["provider"] = git.provider
    _emit_git_promotion_event(result, promotion_id=existing.id, payload=payload)
    result["git_promotion_event_ids"].append(existing.id)


def _attach_promotion_failure(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    """Block linked records and notify the actor's superior or project lead."""

    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
    )
    if deliverable is not None:
        deliverable.state = TeamRecordState.BLOCKED
    release = (
        state.release_packages.get(promotion.release_package_id)
        if promotion.release_package_id is not None
        else None
    )
    if release is not None:
        release.state = TeamRecordState.BLOCKED
        release.error = promotion.error
    recipient_id = _promotion_failure_recipient(state, promotion)
    if recipient_id is not None and recipient_id in state.agents:
        inbox_id = f"inbox-{uuid4().hex}"
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=recipient_id,
            sender_agent_id=None,
            task_id=(
                promotion.task_id
                if promotion.task_id is not None and promotion.task_id in state.tasks
                else None
            ),
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            payload={
                "kind": "git_promotion_failed",
                "promotion_id": promotion.id,
                "promotion_kind": promotion.promotion_kind,
                "project_id": promotion.project_id,
                "source_branch": promotion.source_branch,
                "target_branch": promotion.target_branch,
                "error": promotion.error,
            },
        )
        result["created_inbox_item_ids"].append(inbox_id)


def _promotion_failure_recipient(
    state: TeamState,
    promotion: GitPromotionRecord,
) -> str | None:
    actor = state.agents.get(promotion.actor_agent_id)
    if actor is not None and actor.reports_to_agent_id is not None:
        return actor.reports_to_agent_id
    project = state.projects.get(promotion.project_id)
    if project is not None:
        return project.lead_agent_id
    return None


def _release_failure_recipient(state: TeamState, release: ReleasePackage) -> str | None:
    project = state.projects.get(release.project_id)
    if project is None:
        return None
    if project.lead_agent_id in state.agents:
        return project.lead_agent_id
    team = state.teams.get(project.team_id)
    return team.manager_agent_id if team is not None else None


def _recalculate_project_status(state: TeamState, project_id: str) -> None:
    project = state.projects.get(project_id)
    if project is None or project.status == ProjectStatus.ARCHIVED:
        return
    project_tasks = [
        task for task in state.tasks.values() if task.project_id == project_id
    ]
    project_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if (task := state.tasks.get(deliverable.task_id)) is not None
        and task.project_id == project_id
    ]
    deliverable_ids = {deliverable.id for deliverable in project_deliverables}
    project_reviews = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id in deliverable_ids
    ]
    project_releases = [
        release
        for release in state.release_packages.values()
        if release.project_id == project_id
    ]

    pending_or_blocked = {TeamRecordState.PENDING, TeamRecordState.BLOCKED}
    blocked = (
        any(task.state == TeamTaskState.BLOCKED for task in project_tasks)
        or any(goal.state == TeamRecordState.BLOCKED for goal in project.goals)
        or any(
            deliverable.state == TeamRecordState.BLOCKED
            for deliverable in project_deliverables
        )
        or any(review.state == TeamRecordState.BLOCKED for review in project_reviews)
        or any(release.state == TeamRecordState.BLOCKED for release in project_releases)
    )
    completed = (
        all(goal.state == TeamRecordState.ACCEPTED for goal in project.goals)
        and not any(task.state in _NON_TERMINAL_TASK_STATES for task in project_tasks)
        and not any(
            deliverable.state in pending_or_blocked
            for deliverable in project_deliverables
        )
        and not any(review.state in pending_or_blocked for review in project_reviews)
        and not any(release.state in pending_or_blocked for release in project_releases)
    )
    if completed:
        new_status = ProjectStatus.COMPLETED
    elif blocked:
        new_status = ProjectStatus.BLOCKED
    else:
        new_status = ProjectStatus.ACTIVE
    if new_status != project.status:
        project.status = new_status
        project.updated_at = utc_now()


def _list_of_mappings(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain JSON objects")
        records.append(dict(item))
    return records


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _ephemeral_request_result() -> dict[str, Any]:
    return {
        "success": True,
        "applied": True,
        "errors": [],
        "created_ephemeral_assignment_ids": [],
        "events": [],
    }


def _create_ephemeral_assignments(
    state: TeamState,
    *,
    parent: PersistentAgent,
    requests: list[dict[str, Any]],
    dispatch_id: str | None,
    result: dict[str, Any],
) -> None:
    if not requests:
        return
    active_count = len(_active_ephemeral_assignments_for_parent(state, parent.id))
    max_concurrent = parent.ephemeral_policy.max_concurrent
    if max_concurrent <= 0:
        raise ValueError(f"agent {parent.id} is not allowed to spawn ephemeral agents")
    if active_count + len(requests) > max_concurrent:
        raise ValueError(
            f"agent {parent.id} ephemeral request would exceed max_concurrent="
            f"{max_concurrent}"
        )

    normalized: list[tuple[dict[str, Any], AgentTask]] = []
    seen_ids: set[str] = set()
    for request in requests:
        task_id = _required_text(request.get("task_id"), "task_id")
        task = _require_present(state.tasks, task_id, "task")
        if task.owner_agent_id != parent.id:
            raise ValueError(f"agent {parent.id} cannot spawn ephemeral work for task {task_id}")
        if parent.project_id != task.project_id:
            raise ValueError(f"agent {parent.id} is not assigned to project {task.project_id}")
        assignment_id = _optional_text(request.get("id")) or f"ephemeral-{uuid4().hex}"
        if assignment_id in seen_ids:
            raise ValueError(f"duplicate ephemeral assignment id {assignment_id}")
        _require_absent(state.ephemeral_assignments, assignment_id, "ephemeral assignment")
        request["id"] = assignment_id
        seen_ids.add(assignment_id)
        normalized.append((request, task))

    now = utc_now()
    for request, task in normalized:
        assignment_id = _required_text(request.get("id"), "id")
        session_id = _optional_text(request.get("session_id")) or f"team-eph-{uuid4().hex}"
        assignment = EphemeralAssignment(
            id=assignment_id,
            parent_agent_id=parent.id,
            project_id=task.project_id,
            task_id=task.id,
            session_id=session_id,
            title=_required_text(request.get("title"), "title"),
            prompt=_required_text(request.get("prompt"), "prompt"),
            state=EphemeralAssignmentState.REQUESTED,
            model=_optional_text(request.get("model")),
            agent_provider=_optional_text(request.get("agent_provider")),
            execution_backend=(
                _optional_text(request.get("execution_backend")) or "container_worker_vm"
            ),
            container_image=_optional_text(request.get("container_image")),
            allowed_tools=_string_list(request.get("allowed_tools"), "allowed_tools"),
            ttl_hours=int(request["ttl_hours"]) if request.get("ttl_hours") is not None else None,
            directory_path=_optional_text(request.get("directory_path")),
            metadata=_optional_mapping(request.get("metadata"), "metadata") or {},
            created_at=now,
            updated_at=now,
        )
        state.ephemeral_assignments[assignment.id] = assignment
        result["created_ephemeral_assignment_ids"].append(assignment.id)


def _active_ephemeral_assignments_for_parent(
    state: TeamState,
    parent_agent_id: str,
) -> list[EphemeralAssignment]:
    active_states = {
        EphemeralAssignmentState.REQUESTED,
        EphemeralAssignmentState.SPAWN_PENDING,
        EphemeralAssignmentState.RUNNING,
    }
    return [
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.parent_agent_id == parent_agent_id and assignment.state in active_states
    ]


def _resolve_ephemeral_assignment(
    state: TeamState,
    *,
    action: dict[str, Any],
    session_id: str | None,
) -> EphemeralAssignment:
    assignment_id = (
        _optional_text(action.get("ephemeral_assignment_id"))
        or _optional_text(action.get("assignment_id"))
    )
    if assignment_id:
        return _require_present(
            state.ephemeral_assignments,
            assignment_id,
            "ephemeral assignment",
        )
    if session_id:
        for assignment in state.ephemeral_assignments.values():
            if assignment.session_id == session_id:
                return assignment
    raise ValueError("ephemeral action requires assignment_id or a known session_id")


def _apply_ephemeral_acceptance(
    state: TeamState,
    *,
    assignment: EphemeralAssignment,
    accepted: bool,
    deliverable_id: str | None,
    rejection_reason: str | None,
) -> str | None:
    if accepted:
        if assignment.state not in {
            EphemeralAssignmentState.COMPLETED,
            EphemeralAssignmentState.ACCEPTED,
            EphemeralAssignmentState.CLEANUP_PENDING,
            EphemeralAssignmentState.CLEANED_UP,
        }:
            raise ValueError(f"ephemeral assignment {assignment.id} is not completed")
        deliverable_id_value = deliverable_id or f"deliverable-{uuid4().hex}"
        _require_absent(state.deliverables, deliverable_id_value, "deliverable")
        task = _require_present(state.tasks, assignment.task_id, "task")
        deliverable = Deliverable(
            id=deliverable_id_value,
            task_id=assignment.task_id,
            producer_agent_id=assignment.parent_agent_id,
            summary=_required_text(assignment.summary, "summary"),
            artifact_refs=list(assignment.artifact_refs),
            state=TeamRecordState.PENDING,
        )
        state.deliverables[deliverable.id] = deliverable
        task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
        task.state = TeamTaskState.WAITING_FOR_REVIEW
        task.updated_at = utc_now()
        assignment.state = EphemeralAssignmentState.ACCEPTED
        assignment.error = None
        assignment.updated_at = utc_now()
        return deliverable.id

    assignment.state = EphemeralAssignmentState.REJECTED
    assignment.error = rejection_reason
    assignment.updated_at = utc_now()
    return None


def _ephemeral_assignment_event_payload(
    state: TeamState,
    assignment_id: str,
    *,
    event_type: str = EPHEMERAL_REQUESTED_EVENT_TYPE,
) -> dict[str, Any]:
    assignment = _require_present(
        state.ephemeral_assignments,
        assignment_id,
        "ephemeral assignment",
    )
    parent = _require_present(state.agents, assignment.parent_agent_id, "parent agent")
    task = _require_present(state.tasks, assignment.task_id, "task")
    metadata = dict(assignment.metadata)
    team_metadata = dict(metadata.get("team_addon")) if isinstance(metadata.get("team_addon"), dict) else {}
    team_metadata.update(
        {
            "ephemeral_assignment_id": assignment.id,
            "parent_agent_id": assignment.parent_agent_id,
            "team_id": parent.team_id,
            "project_id": assignment.project_id,
            "task_id": assignment.task_id,
            "ephemeral": True,
        }
    )
    metadata["team_addon"] = team_metadata
    assignment_payload = assignment.model_dump(mode="json")
    return {
        "event_type": event_type,
        "correlation_key": assignment.id,
        "idempotency_key": f"team-addon:{event_type}:{assignment.id}",
        "payload": {
            "assignment": assignment_payload,
            "assignment_id": assignment.id,
            "parent_agent_id": assignment.parent_agent_id,
            "project_id": assignment.project_id,
            "task_id": assignment.task_id,
            "session_id": assignment.session_id,
            "title": assignment.title,
            "prompt": _build_ephemeral_prompt(
                assignment=assignment,
                parent=parent,
                task=task,
            ),
            "summary": assignment.summary,
            "artifact_refs": list(assignment.artifact_refs),
            "model": assignment.model,
            "agent_provider": assignment.agent_provider,
            "execution_backend": assignment.execution_backend or "container_worker_vm",
            "container_image": assignment.container_image,
            "allowed_tools": list(assignment.allowed_tools),
            "ttl_hours": assignment.ttl_hours,
            "directory_path": assignment.directory_path,
            "metadata": metadata,
        },
    }


def _build_ephemeral_prompt(
    *,
    assignment: EphemeralAssignment,
    parent: PersistentAgent,
    task: AgentTask,
) -> str:
    context = {
        "assignment": assignment.model_dump(mode="json"),
        "parent_agent": parent.model_dump(mode="json"),
        "task": task.model_dump(mode="json"),
        "action_artifact_path": "/artifacts/team-action.json",
    }
    schema = {
        "ephemeral_assignment_id": assignment.id,
        "summary": "",
        "artifact_refs": [],
        "task_notes": "",
        "error": None,
    }
    return (
        "Complete this scoped ephemeral team-agent assignment. "
        "You are a disposable worker for one parent agent; do not spawn other agents, "
        "ask hierarchy questions, or update unrelated team state.\n\n"
        f"Assignment prompt:\n{assignment.prompt}\n\n"
        f"Assignment context JSON:\n{_json_dumps(context)}\n\n"
        "Before finishing, write a UTF-8 JSON action object to "
        "/artifacts/team-action.json using this shape:\n"
        f"{_json_dumps(schema)}\n"
    )


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _bool_from_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, indent=2)


__all__ = [
    "ADDON_ID",
    "EPHEMERAL_COMPLETED_EVENT_TYPE",
    "EPHEMERAL_REQUESTED_EVENT_TYPE",
    "GIT_PROMOTION_EVENT_TYPE",
    "OPERATOR_ESCALATION_EVENT_TYPE",
    "STATE_FILE",
    "TeamStateConflictError",
    "TeamStateRepository",
    "ValidationError",
]
