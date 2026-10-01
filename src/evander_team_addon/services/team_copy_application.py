from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import (
    AgentStatus,
    ProjectGoal,
    ProjectStatus,
    TeamCostBudgetWindowKind,
    TeamRecordState,
    TeamState,
)
from .domain.agent_access import _project_assignment_id
from .domain.common import (
    _generated_id,
    _optional_text,
    _require_absent,
    _require_present,
    _required_text,
)
from .domain.team_copy import _team_copy_source_configuration, _validated_team_copy_session_ids
from .domain.team_cost_budget import _open_team_cost_budget_window


@dataclass(frozen=True)
class TeamCopyRequest:
    source_team_id: str
    team_id: str | None
    name: str
    agent_session_ids: Mapping[str, object]

    @classmethod
    def from_inputs(
        cls,
        *,
        source_team_id: str,
        team_id: str | None,
        name: str,
        agent_session_ids: Mapping[str, object],
    ) -> TeamCopyRequest:
        normalized_source_team_id = _required_text(source_team_id, "source_team_id")
        normalized_team_id = _optional_text(team_id)
        normalized_name = _required_text(name, "name")
        if not isinstance(agent_session_ids, Mapping):
            raise ValueError("agent_session_ids must be a JSON object")
        return cls(
            normalized_source_team_id, normalized_team_id, normalized_name, agent_session_ids
        )


class TeamCopyApplication:
    """Plan a clean configuration graph, then apply it in one repository transaction."""

    def __init__(
        self, *, snapshot: TeamState, request: TeamCopyRequest, clock: Callable[[], datetime]
    ) -> None:
        self.snapshot = snapshot
        self.request = request
        self.clock = clock

    def prepare(self) -> None:
        self._select_source()
        self._validate_sessions()
        self._select_team_id()
        self._allocate_project_ids()
        self._allocate_role_ids()
        self._allocate_agent_ids()
        self._allocate_outcome_kind_ids()
        self._allocate_review_profile_ids()
        self._build_result()
        self._collect_project_bindings()

    def apply(self, state: TeamState) -> TeamState:
        self.state = state
        self._validate_destination_ids()
        self.now = self.clock()
        self._copy_roles()
        self._copy_agents()
        self._copy_team()
        self._copy_goals()
        self._copy_review_profiles()
        self._copy_outcome_kinds()
        self._copy_projects()
        self._copy_assignments()
        return self.state

    def _select_source(self) -> None:
        configuration = _team_copy_source_configuration(self.snapshot, self.request.source_team_id)
        self.source_team = configuration["team"]
        self.included_projects = configuration["projects"]
        self.ordered_included_project_ids = configuration["ordered_project_ids"]
        self.included_roles = configuration["roles"]
        self.included_agents = configuration["agents"]
        self.included_outcome_kinds = configuration["outcome_kinds"]
        self.included_review_profiles = configuration["review_profiles"]
        self.included_assignments = configuration["assignments"]

    def _validate_sessions(self) -> None:
        self.session_ids = _validated_team_copy_session_ids(
            self.request.agent_session_ids, self.included_agents, self.snapshot
        )

    def _select_team_id(self) -> None:
        self.team_id = self.request.team_id or _generated_id(
            prefix="team", labels=[self.request.name], existing_ids=self.snapshot.teams
        )
        _require_absent(self.snapshot.teams, self.team_id, "team")

    def _allocate_project_ids(self) -> None:
        used_project_ids = set(self.snapshot.projects)
        self.project_ids: dict[str, str] = {}
        for source_project in sorted(self.included_projects.values(), key=lambda item: item.id):
            copied_id = _generated_id(
                prefix="project",
                labels=[self.team_id, source_project.name],
                existing_ids=used_project_ids,
            )
            self.project_ids[source_project.id] = copied_id
            used_project_ids.add(copied_id)

    def _allocate_role_ids(self) -> None:
        used_role_ids = set(self.snapshot.roles)
        self.role_ids: dict[str, str] = {}
        for source_role in sorted(self.included_roles.values(), key=lambda item: item.id):
            copied_id = _generated_id(
                prefix="role",
                labels=[
                    self.project_ids.get(source_role.project_id or "", self.team_id),
                    source_role.name,
                ],
                existing_ids=used_role_ids,
            )
            self.role_ids[source_role.id] = copied_id
            used_role_ids.add(copied_id)

    def _allocate_agent_ids(self) -> None:
        used_agent_ids = set(self.snapshot.agents)
        self.agent_ids: dict[str, str] = {}
        for source_agent in sorted(self.included_agents.values(), key=lambda item: item.id):
            source_role = self.snapshot.roles.get(source_agent.role_id or "")
            copied_id = _generated_id(
                prefix="agent",
                labels=[
                    self.team_id,
                    source_agent.display_name
                    or (source_role.name if source_role else source_agent.id),
                ],
                existing_ids=used_agent_ids,
            )
            self.agent_ids[source_agent.id] = copied_id
            used_agent_ids.add(copied_id)

    def _allocate_outcome_kind_ids(self) -> None:
        used_outcome_kind_ids = set(self.snapshot.outcome_kind_definitions)
        self.outcome_kind_ids: dict[str, str] = {}
        for definition in sorted(self.included_outcome_kinds.values(), key=lambda item: item.id):
            copied_id = _generated_id(
                prefix="outcome-kind",
                labels=[
                    self.project_ids.get(definition.project_id or "", self.team_id),
                    definition.kind,
                ],
                existing_ids=used_outcome_kind_ids,
            )
            self.outcome_kind_ids[definition.id] = copied_id
            used_outcome_kind_ids.add(copied_id)

    def _allocate_review_profile_ids(self) -> None:
        used_profile_ids = set(self.snapshot.review_profiles)
        self.review_profile_ids: dict[str, str] = {}
        for profile in sorted(self.included_review_profiles.values(), key=lambda item: item.id):
            copied_id = _generated_id(
                prefix="profile",
                labels=[self.project_ids.get(profile.project_id or "", self.team_id), profile.name],
                existing_ids=used_profile_ids,
            )
            self.review_profile_ids[profile.id] = copied_id
            used_profile_ids.add(copied_id)

    def _build_result(self) -> None:
        self.result: dict[str, Any] = {
            "source_team_id": self.source_team.id,
            "team_id": self.team_id,
            "project_ids": self.project_ids,
            "role_ids": self.role_ids,
            "agent_ids": self.agent_ids,
            "outcome_kind_definition_ids": self.outcome_kind_ids,
            "review_profile_ids": self.review_profile_ids,
        }

    def _collect_project_bindings(self) -> None:
        self.project_bindings = {
            self.project_ids[source_project.id]: source_project.git.managed_repo_id
            for source_project in self.included_projects.values()
            if source_project.git is not None
        }

    def _validate_destination_ids(self) -> None:
        _require_present(self.state.teams, self.source_team.id, "source team")
        _require_absent(self.state.teams, self.team_id, "team")
        for copied_id in self.project_ids.values():
            _require_absent(self.state.projects, copied_id, "project")
        for copied_id in self.role_ids.values():
            _require_absent(self.state.roles, copied_id, "role")
        for copied_id in self.agent_ids.values():
            _require_absent(self.state.agents, copied_id, "agent")
        for copied_id in self.outcome_kind_ids.values():
            _require_absent(
                self.state.outcome_kind_definitions, copied_id, "outcome kind definition"
            )
        for copied_id in self.review_profile_ids.values():
            _require_absent(self.state.review_profiles, copied_id, "review profile")

    def _copy_roles(self) -> None:
        for source_role in self.included_roles.values():
            copied_role = source_role.model_copy(
                update={
                    "id": self.role_ids[source_role.id],
                    "team_id": self.team_id,
                    "project_id": self.project_ids.get(source_role.project_id or ""),
                    "archived": False,
                    "created_at": self.now,
                    "updated_at": self.now,
                },
                deep=True,
            )
            self.state.roles[copied_role.id] = copied_role

    def _copy_agents(self) -> None:
        for source_agent in self.included_agents.values():
            copied_agent = source_agent.model_copy(
                update={
                    "id": self.agent_ids[source_agent.id],
                    "team_id": self.team_id,
                    "role_id": self.role_ids[source_agent.role_id],
                    "session_id": self.session_ids[source_agent.id],
                    "reports_to_agent_id": self.agent_ids.get(
                        source_agent.reports_to_agent_id or ""
                    ),
                    "retired_team_id": None,
                    "subordinate_agent_ids": [
                        self.agent_ids[subordinate_id]
                        for subordinate_id in source_agent.subordinate_agent_ids
                        if subordinate_id in self.agent_ids
                    ],
                    "status": AgentStatus.ACTIVE,
                },
                deep=True,
            )
            self.state.agents[copied_agent.id] = copied_agent

    def _copy_team(self) -> None:
        copied_team = self.source_team.model_copy(
            update={
                "id": self.team_id,
                "name": self.request.name,
                "manager_agent_id": self.agent_ids[self.source_team.manager_agent_id],
                "project_ids": [
                    self.project_ids[project_id] for project_id in self.ordered_included_project_ids
                ],
                "cost_budget_windows": [],
                "escalate_to_governance": False,
                "archived": False,
                "created_at": self.now,
                "updated_at": self.now,
            },
            deep=True,
        )
        if copied_team.max_cost_usd is not None:
            _open_team_cost_budget_window(
                copied_team,
                now=self.now,
                kind=TeamCostBudgetWindowKind.CONFIGURED,
                actor="operator:team-copy",
                reason=f"Initial cost budget copied from Team {self.source_team.id}.",
            )
        self.state.teams[copied_team.id] = copied_team

    def _copy_goals(self) -> None:
        self.copied_goals_by_project: dict[str, list[ProjectGoal]] = {}
        for source_project in self.included_projects.values():
            copied_project_id = self.project_ids[source_project.id]
            used_goal_ids: set[str] = set()
            copied_goals: list[ProjectGoal] = []
            for source_goal in source_project.goals:
                goal_id = _generated_id(
                    prefix="goal",
                    labels=[copied_project_id, source_goal.description],
                    existing_ids=used_goal_ids,
                )
                used_goal_ids.add(goal_id)
                copied_goals.append(
                    source_goal.model_copy(
                        update={"id": goal_id, "state": TeamRecordState.PENDING}, deep=True
                    )
                )
            self.copied_goals_by_project[source_project.id] = copied_goals

    def _copy_review_profiles(self) -> None:
        for source_profile in self.included_review_profiles.values():
            copied_profile = source_profile.model_copy(
                update={
                    "id": self.review_profile_ids[source_profile.id],
                    "team_id": self.team_id,
                    "project_id": self.project_ids.get(source_profile.project_id or ""),
                    "applicable_role_ids": [
                        self.role_ids[role_id] for role_id in source_profile.applicable_role_ids
                    ],
                    "archived": False,
                    "created_at": self.now,
                    "updated_at": self.now,
                },
                deep=True,
            )
            self.state.review_profiles[copied_profile.id] = copied_profile

    def _copy_outcome_kinds(self) -> None:
        for source_definition in self.included_outcome_kinds.values():
            copied_definition = source_definition.model_copy(
                update={
                    "id": self.outcome_kind_ids[source_definition.id],
                    "team_id": self.team_id,
                    "project_id": self.project_ids.get(source_definition.project_id or ""),
                    "archived": False,
                    "created_at": self.now,
                    "updated_at": self.now,
                },
                deep=True,
            )
            self.state.outcome_kind_definitions[copied_definition.id] = copied_definition

    def _copy_projects(self) -> None:
        for source_project in self.included_projects.values():
            release_policy = source_project.release_policy
            if release_policy is not None:
                release_policy = release_policy.model_copy(
                    update={
                        "review_requirements": [
                            requirement.model_copy(
                                update={
                                    "reviewer_agent_id": self.agent_ids[
                                        requirement.reviewer_agent_id
                                    ],
                                    "review_profile_id": self.review_profile_ids[
                                        requirement.review_profile_id
                                    ],
                                },
                                deep=True,
                            )
                            for requirement in release_policy.review_requirements
                        ]
                    },
                    deep=True,
                )
            copied_project = source_project.model_copy(
                update={
                    "id": self.project_ids[source_project.id],
                    "team_id": self.team_id,
                    "lead_agent_id": self.agent_ids[source_project.lead_agent_id],
                    "goals": self.copied_goals_by_project[source_project.id],
                    "release_policy": release_policy,
                    "status": ProjectStatus.ACTIVE,
                    "created_at": self.now,
                    "updated_at": self.now,
                },
                deep=True,
            )
            self.state.projects[copied_project.id] = copied_project

    def _copy_assignments(self) -> None:
        for source_assignment in self.included_assignments.values():
            copied_project_id = self.project_ids[source_assignment.project_id]
            copied_agent_id = self.agent_ids[source_assignment.agent_id]
            copied_assignment_id = _project_assignment_id(
                copied_project_id,
                copied_agent_id,
                source_assignment.kind,
                existing_ids=self.state.project_assignments,
            )
            self.state.project_assignments[copied_assignment_id] = source_assignment.model_copy(
                update={
                    "id": copied_assignment_id,
                    "team_id": self.team_id,
                    "project_id": copied_project_id,
                    "agent_id": copied_agent_id,
                    "role_id": self.role_ids.get(source_assignment.role_id or ""),
                    "created_at": self.now,
                    "updated_at": self.now,
                },
                deep=True,
            )
