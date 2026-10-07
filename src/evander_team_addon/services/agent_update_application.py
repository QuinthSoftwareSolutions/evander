from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import (
    AgentContextClearingPolicy,
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentRuntime,
    AgentStatus,
    AgentVoiceProfile,
    EphemeralAgentPolicy,
    PersistentAgent,
    TeamRole,
    TeamState,
    agent_project_ids,
)
from .domain.agent_access import (
    _agent_team_removal_blockers,
    _project_assignment_id,
    _validate_role_assignment,
)
from .domain.common import _append_unique, _remove_value, _require_present
from .domain.context_rotation import (
    _set_context_clearing_idle_grace_seconds,
    _set_context_clearing_policy,
)


@dataclass(frozen=True)
class AgentUpdateRequest:
    agent_id: str
    team_id: str | None
    project_id: str | None
    project_ids: list[str]
    role_id: str | None
    # Implicit role edits fall back to the supplied text when normalization is empty.
    raw_role_id: str | None
    superior_id: str | None
    session_id: str | None
    display_name: str | None
    ephemeral_policy: EphemeralAgentPolicy | None
    status: AgentStatus | None
    voice_profile: AgentVoiceProfile | None
    context_clearing_policy: AgentContextClearingPolicy | None
    idle_grace_seconds: int | None
    edit_team: bool
    edit_project: bool
    edit_projects: bool
    explicit_role: bool
    edit_role: bool
    edit_superior: bool
    edit_display_name: bool
    edit_voice_profile: bool


class AgentUpdateApplication:
    """Apply membership, profile, and runtime edits within one repository transaction."""

    agent: PersistentAgent
    now: datetime
    detached_from_team: bool

    def __init__(
        self,
        *,
        state: TeamState,
        request: AgentUpdateRequest,
        wake_result: dict[str, Any],
        clock: Callable[[], datetime],
    ) -> None:
        self.state = state
        self.request = request
        self.wake_result = wake_result
        self.clock = clock

    def apply(self) -> TeamState:
        self._load_agent()
        self._update_team()
        self._update_project_assignments()
        self._update_role()
        self._update_session()
        self._update_profile()
        self._update_context_policy()
        self._update_reporting_links()
        self._validate_membership()
        self._wake_role_queue()
        return self.state

    def _load_agent(self) -> None:
        self.now = self.clock()
        self.agent = _require_present(self.state.agents, self.request.agent_id, "agent")
        self.detached_from_team = False
        if self.request.edit_project and self.request.edit_projects:
            raise ValueError("project_id and project_ids cannot be set together")

    def _update_team(self) -> None:
        if self.request.edit_team:
            if self.request.team_id is None:
                blockers = _agent_team_removal_blockers(self.state, self.agent)
                if blockers:
                    raise ValueError(
                        f"agent {self.agent.id} cannot be removed from team: " + "; ".join(blockers)
                    )
                old_superior_id = self.agent.reports_to_agent_id
                if old_superior_id and old_superior_id in self.state.agents:
                    old_superior = self.state.agents[old_superior_id]
                    old_superior.subordinate_agent_ids = _remove_value(
                        old_superior.subordinate_agent_ids,
                        self.agent.id,
                    )
                for assignment_id in [
                    assignment.id
                    for assignment in self.state.project_assignments.values()
                    if assignment.agent_id == self.agent.id
                ]:
                    self.state.project_assignments.pop(assignment_id, None)
                self.agent.team_id = None
                self.agent.role_id = None
                self.agent.reports_to_agent_id = None
                self.agent.subordinate_agent_ids = []
                self.detached_from_team = True
            else:
                _require_present(self.state.teams, self.request.team_id, "team")
                self.agent.team_id = self.request.team_id

    def _update_project_assignments(self) -> None:
        if self.request.edit_project and not self.detached_from_team:
            self._replace_single_project_assignment()
        elif self.request.edit_projects and not self.detached_from_team:
            self._replace_project_assignments()

    def _update_role(self) -> None:
        if not self.detached_from_team and self.request.edit_role:
            requested_role_id = (
                self.request.role_id
                if self.request.explicit_role
                else self.request.role_id or self.request.raw_role_id
            )
            role: TeamRole | None = None
            if requested_role_id is not None:
                if self.request.explicit_role and self.agent.team_id is None:
                    raise ValueError("role_id cannot be set for a teamless agent")
                role = _require_present(self.state.roles, requested_role_id, "role")
                _validate_role_assignment(
                    role,
                    team_id=self.agent.team_id,
                    project_ids=agent_project_ids(self.state, self.agent.id),
                )
            self.agent.role_id = requested_role_id
            if role is not None and role.project_id is not None:
                for assignment in self.state.project_assignments.values():
                    if (
                        assignment.agent_id == self.agent.id
                        and assignment.project_id == role.project_id
                    ):
                        assignment.role_id = role.id
                        assignment.updated_at = self.now

    def _update_session(self) -> None:
        if self.request.session_id is not None:
            session_changed = self.request.session_id != self.agent.session_id
            self.agent.session_id = self.request.session_id
            if session_changed:
                runtime = self.state.agent_runtimes.setdefault(
                    self.agent.id,
                    AgentRuntime(agent_id=self.agent.id),
                )
                runtime.last_context_rotated_at = self.now
                runtime.work_lineage_thread_ids = {}
                runtime.work_lineage_thread_dispatch_ids = {}
                runtime.updated_at = self.now

    def _update_profile(self) -> None:
        if self.request.edit_display_name:
            self.agent.display_name = self.request.display_name
        if self.request.ephemeral_policy is not None:
            self.agent.ephemeral_policy = self.request.ephemeral_policy
        if self.request.status is not None:
            self.agent.status = self.request.status
        if self.request.edit_voice_profile:
            self.agent.voice_profile = (
                self.request.voice_profile
                if self.request.voice_profile is not None and self.request.voice_profile.configured
                else None
            )

    def _update_context_policy(self) -> None:
        if (
            self.request.context_clearing_policy is not None
            or self.request.idle_grace_seconds is not None
        ):
            runtime = self.state.agent_runtimes.setdefault(
                self.agent.id,
                AgentRuntime(agent_id=self.agent.id),
            )
            if self.request.idle_grace_seconds is not None:
                _set_context_clearing_idle_grace_seconds(
                    self.state,
                    agent=self.agent,
                    runtime=runtime,
                    grace_seconds=self.request.idle_grace_seconds,
                    now=self.now,
                    result=self.wake_result,
                )
        if self.request.context_clearing_policy is not None:
            _set_context_clearing_policy(
                self.state,
                agent=self.agent,
                runtime=runtime,
                policy=self.request.context_clearing_policy,
                now=self.now,
                result=self.wake_result,
            )

    def _update_reporting_links(self) -> None:
        if self.request.edit_superior and not self.detached_from_team:
            old_superior_id = self.agent.reports_to_agent_id
            if self.request.superior_id is not None:
                if self.agent.team_id is None:
                    raise ValueError("reports_to_agent_id cannot be set for a teamless agent")
                superior = _require_present(
                    self.state.agents, self.request.superior_id, "superior agent"
                )
                if superior.team_id != self.agent.team_id:
                    raise ValueError(
                        f"superior agent {self.request.superior_id} is not in team {self.agent.team_id}"
                    )
            if old_superior_id and old_superior_id in self.state.agents:
                old_superior = self.state.agents[old_superior_id]
                old_superior.subordinate_agent_ids = _remove_value(
                    old_superior.subordinate_agent_ids, self.agent.id
                )
            self.agent.reports_to_agent_id = self.request.superior_id
            if self.request.superior_id and self.request.superior_id in self.state.agents:
                superior = self.state.agents[self.request.superior_id]
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids, self.agent.id
                )

    def _validate_membership(self) -> None:
        if self.agent.team_id is None:
            if self.agent.role_id is not None:
                raise ValueError("teamless agent cannot have a team-scoped role")
            if self.agent.reports_to_agent_id is not None:
                raise ValueError("teamless agent cannot report to another agent")
            if self.agent.subordinate_agent_ids:
                raise ValueError("teamless agent cannot have subordinate agents")
        else:
            if self.agent.role_id is None:
                raise ValueError("role_id is required when assigning an agent to a team")
            role = _require_present(self.state.roles, self.agent.role_id, "role")
            _validate_role_assignment(
                role,
                team_id=self.agent.team_id,
                project_ids=agent_project_ids(self.state, self.agent.id),
            )

    def _wake_role_queue(self) -> None:
        if (
            self.request.status is not None
            or self.request.explicit_role
            or self.request.edit_project
            or self.request.edit_projects
            or self.request.edit_team
            or self.request.edit_superior
        ):
            from .domain.role_task_queue import append_role_queue_wake_events

            append_role_queue_wake_events(self.state, result=self.wake_result, now=self.now)

    def _replace_single_project_assignment(self) -> None:
        if self.agent.team_id is None:
            raise ValueError("project_id cannot be set for a teamless agent")
        for assignment_id in [
            assignment.id
            for assignment in self.state.project_assignments.values()
            if assignment.agent_id == self.agent.id
            and assignment.kind == AgentProjectAssignmentKind.PROJECT_MEMBER
        ]:
            self.state.project_assignments.pop(assignment_id, None)
        if self.request.project_id is not None:
            project = _require_present(self.state.projects, self.request.project_id, "project")
            if project.team_id != self.agent.team_id:
                raise ValueError(
                    f"project {self.request.project_id} does not belong to team {self.agent.team_id}"
                )
            assignment_id = _project_assignment_id(
                self.request.project_id,
                self.agent.id,
                AgentProjectAssignmentKind.PROJECT_MEMBER,
                existing_ids=self.state.project_assignments,
            )
            self.state.project_assignments[assignment_id] = AgentProjectAssignment(
                id=assignment_id,
                team_id=self.agent.team_id,
                project_id=self.request.project_id,
                agent_id=self.agent.id,
                role_id=self.agent.role_id,
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
            )

    def _replace_project_assignments(self) -> None:
        if self.agent.team_id is None:
            raise ValueError("project_ids cannot be set for a teamless agent")
        for requested_project_id in self.request.project_ids:
            project = _require_present(
                self.state.projects,
                requested_project_id,
                "project",
            )
            if project.team_id != self.agent.team_id:
                raise ValueError(
                    f"project {requested_project_id} does not belong to team {self.agent.team_id}"
                )

        lead_project_ids = {
            assignment.project_id
            for assignment in self.state.project_assignments.values()
            if assignment.agent_id == self.agent.id
            and assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD
        }
        missing_lead_project_ids = sorted(lead_project_ids.difference(self.request.project_ids))
        if missing_lead_project_ids:
            raise ValueError(
                "project_ids cannot remove project lead assignments; transfer the "
                "project lead first: " + ", ".join(missing_lead_project_ids)
            )

        for assignment_id in [
            assignment.id
            for assignment in self.state.project_assignments.values()
            if assignment.agent_id == self.agent.id
            and assignment.kind == AgentProjectAssignmentKind.PROJECT_MEMBER
            and assignment.project_id not in self.request.project_ids
        ]:
            self.state.project_assignments.pop(assignment_id, None)

        member_project_ids = {
            assignment.project_id
            for assignment in self.state.project_assignments.values()
            if assignment.agent_id == self.agent.id
            and assignment.kind == AgentProjectAssignmentKind.PROJECT_MEMBER
        }
        assignment_role_id = self.request.role_id if self.request.edit_role else self.agent.role_id
        assignment_role = (
            self.state.roles.get(assignment_role_id) if assignment_role_id is not None else None
        )
        for requested_project_id in self.request.project_ids:
            if (
                requested_project_id in lead_project_ids
                or requested_project_id in member_project_ids
            ):
                continue
            assignment_id = _project_assignment_id(
                requested_project_id,
                self.agent.id,
                AgentProjectAssignmentKind.PROJECT_MEMBER,
                existing_ids=self.state.project_assignments,
            )
            self.state.project_assignments[assignment_id] = AgentProjectAssignment(
                id=assignment_id,
                team_id=self.agent.team_id,
                project_id=requested_project_id,
                agent_id=self.agent.id,
                role_id=(
                    assignment_role_id
                    if assignment_role is not None
                    and assignment_role.project_id in {None, requested_project_id}
                    else None
                ),
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
            )
