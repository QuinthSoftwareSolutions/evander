from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..models import (
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentTask,
    OutcomeKindDefinition,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ReviewProfile,
    Team,
    TeamCostBudgetRenewalMode,
    TeamCostBudgetWindowKind,
    TeamRole,
    TeamState,
    _ensure_unique,
)
from ..templates import TeamTemplate, get_template
from .domain.agent_access import (
    _default_role_id,
    _normalize_role_permissions,
    _project_assignment_id,
)
from .domain.case_task import TaskGitDefaultBranchResolver, _resolve_new_task_git_base_branch
from .domain.common import (
    _append_unique,
    _cost_warning_ratio,
    _generated_id,
    _mapping,
    _materialize_role_inputs,
    _materialized_template_record_id,
    _optional_bool,
    _optional_text,
    _ordered_template_roles,
    _project_git_config,
    _require_absent,
    _required_mapping,
    _required_text,
    _unique_id_from_base,
)
from .domain.team_cost_budget import _cost_budget_renewal_mode, _open_team_cost_budget_window
from .domain.work_lineage import ensure_task_work_lineages


@dataclass(frozen=True)
class TemplateMaterializationRequest:
    template: TeamTemplate
    team_input: Mapping[str, Any]
    project_input: Mapping[str, Any]
    team_name: str
    operator_communication_target_id: str | None
    escalate_to_governance: bool | None
    max_cost_usd: object
    cost_warning_ratio: Decimal
    cost_budget_renewal_mode: TeamCostBudgetRenewalMode
    project_name: str
    git: ProjectGitConfig | None
    role_inputs: dict[str, dict[str, Any]]

    @classmethod
    def from_inputs(
        cls, template_id: str, *, inputs: Mapping[str, Any]
    ) -> TemplateMaterializationRequest:
        normalized_template_id = _required_text(template_id, "template_id")
        template = get_template(normalized_template_id)
        if template is None:
            raise ValueError(f"template {normalized_template_id} does not exist")
        normalized_inputs = _mapping(inputs, "inputs")
        team_input = _required_mapping(normalized_inputs, "team")
        project_input = _required_mapping(normalized_inputs, "project")
        team_name = _required_text(team_input.get("name"), "team.name")
        operator_communication_target_id = _optional_text(
            team_input.get("operator_communication_target_id")
        )
        escalate_to_governance = _optional_bool(
            team_input.get("escalate_to_governance"), "team.escalate_to_governance"
        )
        max_cost_usd = team_input.get("max_cost_usd") if "max_cost_usd" in team_input else None
        cost_warning_ratio = _cost_warning_ratio(team_input.get("cost_warning_ratio"))
        cost_budget_renewal_mode = _cost_budget_renewal_mode(
            team_input.get("cost_budget_renewal_mode")
        )
        project_name = _required_text(project_input.get("name"), "project.name")
        git = _project_git_config(project_input.get("git"))
        role_inputs = _materialize_role_inputs(
            template, normalized_inputs, team_input, project_input
        )
        return cls(
            template=template,
            team_input=team_input,
            project_input=project_input,
            team_name=team_name,
            operator_communication_target_id=operator_communication_target_id,
            escalate_to_governance=escalate_to_governance,
            max_cost_usd=max_cost_usd,
            cost_warning_ratio=cost_warning_ratio,
            cost_budget_renewal_mode=cost_budget_renewal_mode,
            project_name=project_name,
            git=git,
            role_inputs=role_inputs,
        )


class TemplateMaterializationApplication:
    """Allocate template IDs before applying ordered creation stages in one transaction."""

    def __init__(
        self,
        *,
        snapshot: TeamState,
        request: TemplateMaterializationRequest,
        clock: Callable[[], datetime],
        git_default_branch_resolver: TaskGitDefaultBranchResolver,
    ) -> None:
        self.snapshot = snapshot
        self.request = request
        self.clock = clock
        self.git_default_branch_resolver = git_default_branch_resolver
        self.role_inputs = {
            role_key: dict(role_input) for role_key, role_input in request.role_inputs.items()
        }

    def prepare(self) -> None:
        self._allocate_team_and_project_ids()
        self._allocate_goal_ids()
        self._allocate_review_profile_ids()
        self._allocate_outcome_kind_ids()
        self._allocate_task_ids()
        self._allocate_role_ids()
        self._allocate_agent_ids()
        self._validate_unique_ids()
        self._build_result()
        self._validate_root_role()

    def apply(self, state: TeamState) -> TeamState:
        self.state = state
        self._validate_destination_ids()
        self.now = self.clock()
        self._create_roles()
        self._create_manager()
        self._create_team()
        self._create_project()
        self._create_reporting_hierarchy()
        self._create_outcome_kinds()
        self._create_review_profiles()
        self._create_tasks()
        self.result["success"] = True
        self.result["created_agent_ids"] = dict(self.result["agent_ids"])
        return self.state

    def _allocate_team_and_project_ids(self) -> None:
        self.team_id = _optional_text(self.request.team_input.get("id")) or _generated_id(
            prefix="team", labels=[self.request.team_name], existing_ids=self.snapshot.teams
        )
        self.project_id = _optional_text(self.request.project_input.get("id")) or _generated_id(
            prefix="project",
            labels=[self.team_id, self.request.project_name],
            existing_ids=self.snapshot.projects,
        )

    def _allocate_goal_ids(self) -> None:
        self.goal_ids = {
            goal.id: _unique_id_from_base(
                _materialized_template_record_id(self.project_id, goal.id), existing_ids=set()
            )
            for goal in self.request.template.project_goals
        }

    def _allocate_review_profile_ids(self) -> None:
        used_review_profile_ids = set(self.snapshot.review_profiles)
        self.review_profile_ids = {
            profile.id: _unique_id_from_base(
                _materialized_template_record_id(self.project_id, profile.id),
                existing_ids=used_review_profile_ids,
            )
            for profile in self.request.template.review_profiles
        }

    def _allocate_outcome_kind_ids(self) -> None:
        used_outcome_kind_ids = set(self.snapshot.outcome_kind_definitions)
        self.outcome_kind_ids = {
            definition.kind: _unique_id_from_base(
                _materialized_template_record_id(
                    self.project_id if definition.project_scoped else self.team_id,
                    f"outcome-kind-{definition.kind}",
                ),
                existing_ids=used_outcome_kind_ids,
            )
            for definition in self.request.template.outcome_kinds
        }

    def _allocate_task_ids(self) -> None:
        used_task_ids = set(self.snapshot.tasks)
        self.task_ids = {
            task.id: _unique_id_from_base(
                _materialized_template_record_id(self.project_id, task.id),
                existing_ids=used_task_ids,
            )
            for task in self.request.template.tasks
        }

    def _allocate_role_ids(self) -> None:
        used_role_ids = set(self.snapshot.roles)
        self.role_ids: dict[str, str] = {}
        for role in self.request.template.roles:
            role_id = _unique_id_from_base(
                _default_role_id(
                    team_id=self.team_id,
                    project_id=None
                    if role.key == self.request.template.root_role_key
                    else self.project_id,
                    role_key=role.key,
                ),
                existing_ids=used_role_ids,
            )
            used_role_ids.add(role_id)
            self.role_ids[role.key] = role_id

    def _allocate_agent_ids(self) -> None:
        used_agent_ids = set(self.snapshot.agents)
        for role in self.request.template.roles:
            role_input = self.role_inputs[role.key]
            agent_id = _optional_text(role_input.get("agent_id", role_input.get("id")))
            if agent_id is None:
                agent_id = _generated_id(
                    prefix="agent",
                    labels=[
                        self.team_id
                        if role.key == self.request.template.root_role_key
                        else self.project_id,
                        role.label,
                    ],
                    existing_ids=used_agent_ids,
                )
            role_input["agent_id"] = agent_id
            used_agent_ids.add(agent_id)

    def _validate_unique_ids(self) -> None:
        concrete_ids = [
            self.team_id,
            self.project_id,
            *self.role_ids.values(),
            *[role_input["agent_id"] for role_input in self.role_inputs.values()],
            *self.goal_ids.values(),
            *self.outcome_kind_ids.values(),
            *self.review_profile_ids.values(),
            *self.task_ids.values(),
        ]
        _ensure_unique(concrete_ids, "materialization ids")
        _ensure_unique(
            [role_input["session_id"] for role_input in self.role_inputs.values()],
            "materialization session ids",
        )

    def _build_result(self) -> None:
        self.result: dict[str, Any] = {
            "success": False,
            "template_id": self.request.template.id,
            "template_name": self.request.template.name,
            "team_id": self.team_id,
            "project_id": self.project_id,
            "role_ids": self.role_ids,
            "agent_ids": {key: value["agent_id"] for key, value in self.role_inputs.items()},
            "goal_ids": [self.goal_ids[goal.id] for goal in self.request.template.project_goals],
            "outcome_kind_definition_ids": [
                self.outcome_kind_ids[definition.kind]
                for definition in self.request.template.outcome_kinds
            ],
            "review_profile_ids": [
                self.review_profile_ids[profile.id]
                for profile in self.request.template.review_profiles
            ],
            "task_ids": [self.task_ids[task.id] for task in self.request.template.tasks],
            "errors": [],
        }

    def _validate_root_role(self) -> None:
        self.role_agent_ids = {
            role.key: self.role_inputs[role.key]["agent_id"] for role in self.request.template.roles
        }
        self.root_role_key = self.request.template.root_role_key
        self.root_role = next(
            (role for role in self.request.template.roles if role.key == self.root_role_key)
        )
        if self.root_role.reports_to_role_key is not None:
            raise ValueError(
                f"template {self.request.template.id} root role must not report upward"
            )

    def _validate_destination_ids(self) -> None:
        _require_absent(self.state.teams, self.team_id, "team")
        _require_absent(self.state.projects, self.project_id, "project")
        for role_key, role_id in self.role_ids.items():
            _require_absent(self.state.roles, role_id, "role")
        for role_key, role_input in self.role_inputs.items():
            _require_absent(self.state.agents, role_input["agent_id"], "agent")
        for profile in self.request.template.review_profiles:
            _require_absent(
                self.state.review_profiles, self.review_profile_ids[profile.id], "review profile"
            )
        for definition in self.request.template.outcome_kinds:
            _require_absent(
                self.state.outcome_kind_definitions,
                self.outcome_kind_ids[definition.kind],
                "outcome kind definition",
            )
        for task in self.request.template.tasks:
            _require_absent(self.state.tasks, self.task_ids[task.id], "task")

    def _create_roles(self) -> None:
        for role in self.request.template.roles:
            role_project_id = None if role.key == self.root_role_key else self.project_id
            self.state.roles[self.role_ids[role.key]] = TeamRole(
                id=self.role_ids[role.key],
                team_id=self.team_id,
                project_id=role_project_id,
                name=role.label,
                description=role.description,
                permissions=_normalize_role_permissions(role.permissions),
                ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                created_at=self.now,
                updated_at=self.now,
            )

    def _create_manager(self) -> None:
        manager_role_input = self.role_inputs[self.root_role_key]
        self.manager = PersistentAgent(
            id=manager_role_input["agent_id"],
            team_id=self.team_id,
            role_id=self.role_ids[self.root_role_key],
            session_id=manager_role_input["session_id"],
            reports_to_agent_id=None,
            ephemeral_policy=self.root_role.ephemeral_policy.model_copy(deep=True),
        )
        self.state.agents[self.manager.id] = self.manager

    def _create_team(self) -> None:
        team = Team(
            id=self.team_id,
            name=self.request.team_name,
            manager_agent_id=self.manager.id,
            operator_communication_target_id=self.request.operator_communication_target_id,
            escalate_to_governance=self.request.escalate_to_governance or False,
            max_cost_usd=self.request.max_cost_usd,
            cost_warning_ratio=self.request.cost_warning_ratio,
            cost_budget_renewal_mode=self.request.cost_budget_renewal_mode,
            created_at=self.now,
            updated_at=self.now,
        )
        if team.max_cost_usd is not None:
            _open_team_cost_budget_window(
                team,
                now=self.now,
                kind=TeamCostBudgetWindowKind.CONFIGURED,
                actor="operator:team-template",
                reason="Initial cost budget configured from Team template.",
            )
        self.state.teams[self.team_id] = team

    def _create_project(self) -> None:
        project_goals = [
            goal.model_copy(update={"id": self.goal_ids[goal.id]}, deep=True)
            for goal in self.request.template.project_goals
        ]
        self.state.projects[self.project_id] = Project(
            id=self.project_id,
            team_id=self.team_id,
            name=self.request.project_name,
            lead_agent_id=self.role_agent_ids["project_lead"],
            goals=project_goals,
            git=self.request.git,
            created_at=self.now,
            updated_at=self.now,
        )
        self.state.teams[self.team_id].project_ids = _append_unique(
            self.state.teams[self.team_id].project_ids, self.project_id
        )

    def _create_reporting_hierarchy(self) -> None:
        role_objects: dict[str, PersistentAgent] = {self.root_role_key: self.manager}
        ordered_roles = _ordered_template_roles(self.request.template)
        for role in ordered_roles:
            if role.key == self.root_role_key:
                continue
            superior_role_key = role.reports_to_role_key
            if superior_role_key is None:
                raise ValueError(
                    f"template {self.request.template.id} role {role.key} is missing reports_to_role_key"
                )
            superior = role_objects.get(superior_role_key)
            if superior is None:
                raise ValueError(
                    f"template {self.request.template.id} role {role.key} reports to missing role {superior_role_key}"
                )
            role_agent = PersistentAgent(
                id=self.role_agent_ids[role.key],
                team_id=self.team_id,
                role_id=self.role_ids[role.key],
                session_id=self.role_inputs[role.key]["session_id"],
                reports_to_agent_id=superior.id,
                ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
            )
            self.state.agents[role_agent.id] = role_agent
            assignment_kind = (
                AgentProjectAssignmentKind.PROJECT_LEAD
                if role.key == "project_lead"
                else AgentProjectAssignmentKind.PROJECT_MEMBER
            )
            assignment_id = _project_assignment_id(
                self.project_id,
                role_agent.id,
                assignment_kind,
                existing_ids=self.state.project_assignments,
            )
            self.state.project_assignments[assignment_id] = AgentProjectAssignment(
                id=assignment_id,
                team_id=self.team_id,
                project_id=self.project_id,
                agent_id=role_agent.id,
                role_id=self.role_ids[role.key],
                kind=assignment_kind,
                created_at=self.now,
                updated_at=self.now,
            )
            superior.subordinate_agent_ids = _append_unique(
                superior.subordinate_agent_ids, role_agent.id
            )
            role_objects[role.key] = role_agent
        self.state.projects[self.project_id].lead_agent_id = self.role_agent_ids["project_lead"]
        self.state.projects[self.project_id].updated_at = self.now
        self.state.teams[self.team_id].updated_at = self.now

    def _create_outcome_kinds(self) -> None:
        for definition_template in self.request.template.outcome_kinds:
            definition = OutcomeKindDefinition(
                id=self.outcome_kind_ids[definition_template.kind],
                team_id=self.team_id,
                project_id=self.project_id if definition_template.project_scoped else None,
                kind=definition_template.kind,
                name=definition_template.name,
                description=definition_template.description,
                reference_requirements=definition_template.reference_requirements,
                evidence_requirements=definition_template.evidence_requirements,
                created_at=self.now,
                updated_at=self.now,
            )
            self.state.outcome_kind_definitions[definition.id] = definition

    def _create_review_profiles(self) -> None:
        for profile_template in self.request.template.review_profiles:
            profile = ReviewProfile(
                id=self.review_profile_ids[profile_template.id],
                team_id=self.team_id,
                project_id=self.project_id if profile_template.project_scoped else None,
                name=profile_template.name,
                description=profile_template.description,
                applicable_role_ids=[
                    self.role_ids[role_key] for role_key in profile_template.applicable_role_keys
                ],
                criteria=[
                    criterion.model_copy(deep=True) for criterion in profile_template.criteria
                ],
                required_outcome_kinds=list(profile_template.required_outcome_kinds),
                created_at=self.now,
                updated_at=self.now,
            )
            self.state.review_profiles[profile.id] = profile

    def _create_tasks(self) -> None:
        template_git_base_branch = (
            _resolve_new_task_git_base_branch(
                self.state,
                project_id=self.project_id,
                git_default_branch_resolver=self.git_default_branch_resolver,
            )
            if self.request.template.tasks
            else None
        )
        materialized_tasks: list[AgentTask] = []
        for task_template in self.request.template.tasks:
            owner_agent_id = self.role_agent_ids[task_template.owner_role_key]
            created_by_agent_id = self.role_agent_ids[task_template.created_by_role_key]
            dependency_task_ids = [
                self.task_ids[dependency_id] for dependency_id in task_template.dependency_task_ids
            ]
            self.state.tasks[self.task_ids[task_template.id]] = AgentTask(
                id=self.task_ids[task_template.id],
                owner_agent_id=owner_agent_id,
                project_id=self.project_id,
                git_base_branch=template_git_base_branch,
                priority=task_template.priority,
                state=task_template.state,
                title=task_template.title,
                description=task_template.description,
                created_by_agent_id=created_by_agent_id,
                parent_task_id=self.task_ids[task_template.parent_task_id]
                if task_template.parent_task_id is not None
                else None,
                dependency_task_ids=dependency_task_ids,
                created_at=self.now,
                updated_at=self.now,
            )
            materialized_tasks.append(self.state.tasks[self.task_ids[task_template.id]])
        ensure_task_work_lineages(self.state, materialized_tasks)
