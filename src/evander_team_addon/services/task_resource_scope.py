"""Public, read-only authority boundary for task-owned external resources.

Blocked tasks may acquire the resources that unblock them. Project visibility or
supervision alone never grants this authority.
"""

from dataclasses import dataclass

from .commands.executor import (
    TeamCommandExecutor,
    _validate_dispatch_project_scope,
    _validate_dispatch_task_scope,
)
from .commands.types import TeamCommandContext, TeamCommandValidationError


@dataclass(frozen=True)
class TaskResourceScope:
    session_id: str
    agent_id: str
    team_id: str
    project_id: str
    task_id: str
    dispatch_id: str
    dispatch_expires_at: float


class TaskResourceAuthority:
    def __init__(self, repository):
        self.repository = repository
        self.executor = TeamCommandExecutor(repository)

    def resolve(self, *, session_id: str, task_id: str, tool_name: str) -> TaskResourceScope:
        state = self.repository.read()
        resolved, error = self.executor._resolve_team_command_context(
            state,
            TeamCommandContext(ambient_session_id=session_id, tool_name=tool_name),
            require_active_dispatch=True,
        )
        if error:
            raise error
        dispatch = state.dispatches[resolved.dispatch_id]
        task = state.tasks.get(task_id)
        if task_id not in dispatch.task_ids or task is None:
            raise TeamCommandValidationError(
                code="resource_scope", message="Task is outside the active dispatch"
            )
        _validate_dispatch_task_scope(
            state, dispatch=dispatch, task_ids={task_id}, require_actionable=False
        )
        _validate_dispatch_project_scope(state, dispatch=dispatch, project_ids={task.project_id})
        if task.owner_agent_id != resolved.agent.id or task.state.value not in {
            "pending",
            "in_progress",
            "blocked",
            "waiting_for_input",
        }:
            raise TeamCommandValidationError(
                code="resource_scope", message="Task no longer authorizes resources"
            )
        project = state.projects[task.project_id]
        team = state.teams.get(resolved.agent.team_id)
        if (
            project.team_id != resolved.agent.team_id
            or project.status.value not in {"active", "blocked"}
            or team is None
            or team.archived
        ):
            raise TeamCommandValidationError(
                code="resource_scope", message="Task team does not match the caller"
            )
        return TaskResourceScope(
            session_id,
            resolved.agent.id,
            resolved.agent.team_id,
            task.project_id,
            task.id,
            dispatch.id,
            min(
                dispatch.lease_expires_at.timestamp(),
                resolved.runtime.wake_leased_until.timestamp(),
            ),
        )

    def owns(self, scope: TaskResourceScope) -> bool:
        """Ownership survives an idle dispatch; cancellation/reassignment does not."""
        state = self.repository.read()
        agent = state.agents.get(scope.agent_id)
        task = state.tasks.get(scope.task_id)
        project = state.projects.get(scope.project_id)
        team = state.teams.get(scope.team_id)
        return bool(
            agent
            and agent.status.value == "active"
            and agent.session_id == scope.session_id
            and agent.team_id == scope.team_id
            and task
            and task.owner_agent_id == scope.agent_id
            and task.project_id == scope.project_id
            and project
            and project.team_id == scope.team_id
            and project.status.value in {"active", "blocked"}
            and team
            and not team.archived
            and task.state.value in {"pending", "in_progress", "blocked", "waiting_for_input"}
        )
