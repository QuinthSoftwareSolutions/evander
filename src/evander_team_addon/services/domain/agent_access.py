from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from ...models import (
    AGENT_OPERATOR_ONLY_FIELDS,
    AgentProjectAssignmentKind,
    AgentStatus,
    Deliverable,
    PersistentAgent,
    ReviewProfile,
    RolePermission,
    TeamInboxItemState,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    agent_has_project_assignment,
    agent_lead_project_ids,
    agent_project_assignment_payloads,
    agent_project_ids,
    effective_role_permissions,
    project_lead_agent_id,
)

from .common import (
    _ACTIVE_DELIVERABLE_WORK_STATES,
    _NON_TERMINAL_TASK_STATES,
    _optional_text,
    _require_present,
    _required_text,
    _unique_id_from_base,
)


def _default_role_id(
    *,
    team_id: str,
    project_id: str | None,
    role_key: str,
) -> str:
    scope = project_id or team_id
    return f"{scope}__{_role_id_part(role_key)}"

def _role_id_part(value: str) -> str:
    return _required_text(value, "role_key").replace("_", "-")

def _project_assignment_id(
    project_id: str,
    agent_id: str,
    kind: AgentProjectAssignmentKind,
    *,
    existing_ids: Mapping[str, Any],
) -> str:
    return _unique_id_from_base(
        f"{project_id}__{agent_id}__{kind.value}",
        existing_ids=existing_ids,
    )

def _normalize_role_permissions(
    permissions: list[RolePermission | str] | None,
) -> list[RolePermission]:
    if permissions is None:
        return []
    normalized: list[RolePermission] = []
    for permission in permissions:
        item = (
            permission
            if isinstance(permission, RolePermission)
            else RolePermission(str(permission))
        )
        if item not in normalized:
            normalized.append(item)
    return normalized

def _agent_team_removal_blockers(state: TeamState, agent: PersistentAgent) -> list[str]:
    if agent.team_id is None:
        return []
    blockers: list[str] = []
    for team in state.teams.values():
        if team.manager_agent_id == agent.id:
            blockers.append(f"manager of team {team.id}")
    for assignment in state.project_assignments.values():
        if (
            assignment.agent_id == agent.id
            and assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD
        ):
            blockers.append(f"lead of project {assignment.project_id}")
    if agent.subordinate_agent_ids:
        blockers.append("has subordinate agents " + ", ".join(sorted(agent.subordinate_agent_ids)))
    for other in state.agents.values():
        if other.reports_to_agent_id == agent.id:
            blockers.append(f"superior of agent {other.id}")
    terminal_task_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
    }
    for task in state.tasks.values():
        if task.state in terminal_task_states:
            continue
        if task.owner_agent_id == agent.id:
            blockers.append(f"owns task {task.id}")
        if task.created_by_agent_id == agent.id:
            blockers.append(f"created task {task.id}")
    terminal_record_states = {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
    for deliverable in state.deliverables.values():
        if deliverable.state in terminal_record_states:
            continue
        if deliverable.producer_agent_id == agent.id:
            blockers.append(f"produced deliverable {deliverable.id}")
    for review in state.review_requests.values():
        if review.state in terminal_record_states:
            continue
        if review.reviewer_agent_id == agent.id:
            blockers.append(f"reviews request {review.id}")
    for item in state.inbox.values():
        if item.state != TeamInboxItemState.PENDING:
            continue
        if item.recipient_agent_id == agent.id:
            blockers.append(f"recipient of inbox item {item.id}")
    runtime = state.agent_runtimes.get(agent.id)
    if runtime is not None and runtime.active_dispatch_id is not None:
        blockers.append(f"active dispatch {runtime.active_dispatch_id}")
    return list(dict.fromkeys(blockers))

def _validate_role_assignment(
    role: TeamRole,
    *,
    team_id: str | None,
    project_ids: list[str],
) -> None:
    if team_id is None:
        raise ValueError(f"role {role.id} cannot be assigned to a teamless agent")
    if role.team_id != team_id:
        raise ValueError(f"role {role.id} does not belong to team {team_id}")
    if role.project_id is not None and role.project_id not in project_ids:
        raise ValueError(f"role {role.id} does not belong to any assigned project")

def _validate_role_filter_assignment(role: TeamRole, *, profile: ReviewProfile) -> None:
    if role.team_id != profile.team_id:
        raise ValueError(f"role {role.id} does not belong to team {profile.team_id}")
    if role.project_id is not None and role.project_id != profile.project_id:
        raise ValueError(f"role {role.id} does not apply to project {profile.project_id}")

def _agent_payload(state: TeamState, agent: PersistentAgent) -> dict[str, Any]:
    payload = agent.model_dump(mode="json", exclude=AGENT_OPERATOR_ONLY_FIELDS)
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    effective_permissions = effective_role_permissions(state, agent)
    payload["role_name"] = role.name if role is not None else None
    payload["role_description"] = role.description if role is not None else ""
    payload["role_permissions"] = [permission.value for permission in effective_permissions]
    payload["effective_role_permissions"] = [
        permission.value for permission in effective_permissions
    ]
    payload["project_assignments"] = agent_project_assignment_payloads(state, agent.id)
    payload["project_ids"] = agent_project_ids(state, agent.id)
    payload["lead_project_ids"] = agent_lead_project_ids(state, agent.id)
    return payload

def _subordinate_agent_payloads(state: TeamState, agent: PersistentAgent) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for subordinate_id in agent.subordinate_agent_ids:
        subordinate = state.agents.get(subordinate_id)
        if subordinate is None:
            continue
        payloads.append(_agent_payload(state, subordinate))
    return payloads

def _is_active_deliverable_work(deliverable: Deliverable) -> bool:
    return (
        deliverable.state in _ACTIVE_DELIVERABLE_WORK_STATES
        and deliverable.superseded_by_deliverable_id is None
    )

def _task_has_active_deliverable_work(state: TeamState, task_id: str) -> bool:
    return any(
        deliverable.task_id == task_id and _is_active_deliverable_work(deliverable)
        for deliverable in state.deliverables.values()
    )

def _team_ids_with_open_work(state: TeamState) -> set[str]:
    team_ids: set[str] = set()
    for task in state.tasks.values():
        if task.state not in _NON_TERMINAL_TASK_STATES:
            continue
        owner = state.agents.get(task.owner_agent_id)
        if owner is not None and owner.team_id is not None:
            team_ids.add(owner.team_id)
    for deliverable in state.deliverables.values():
        if not _is_active_deliverable_work(deliverable):
            continue
        task = state.tasks.get(deliverable.task_id)
        if task is None:
            continue
        project = state.projects.get(task.project_id)
        if project is not None:
            team_ids.add(project.team_id)
            continue
        owner = state.agents.get(task.owner_agent_id)
        if owner is not None and owner.team_id is not None:
            team_ids.add(owner.team_id)
    return team_ids

def _visible_project_ids_for_agent(state: TeamState, agent: PersistentAgent) -> set[str]:
    project_ids = set(agent_project_ids(state, agent.id))
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None and role.project_id is not None:
        project_ids.add(role.project_id)
    if agent.team_id is not None:
        team = state.teams.get(agent.team_id)
        for project in state.projects.values():
            if project.team_id != agent.team_id:
                continue
            if project.lead_agent_id == agent.id or (
                team is not None and team.manager_agent_id == agent.id
            ):
                project_ids.add(project.id)
    return project_ids

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
        return _agent_for_session(state, session_id=session_id)
    raise ValueError("agent action requires agent_id or a known session_id")

def _agent_for_session(state: TeamState, *, session_id: str) -> PersistentAgent:
    for agent in state.agents.values():
        if agent.session_id == session_id:
            return agent
    raise ValueError(f"no team agent found for session_id {session_id}")

def _is_direct_subordinate(actor: PersistentAgent, agent_id: str) -> bool:
    return agent_id in actor.subordinate_agent_ids

def _is_team_manager(state: TeamState, actor: PersistentAgent) -> bool:
    if actor.reports_to_agent_id is not None:
        return False
    team = state.teams.get(actor.team_id)
    return team is not None and team.manager_agent_id == actor.id

def _agent_governs_team(state: TeamState, *, agent_id: str, team_id: str) -> bool:
    agent = state.agents.get(agent_id)
    if agent is None or agent.status != AgentStatus.ACTIVE:
        return False
    return any(
        not group.archived
        and group.manager_agent_id == agent_id
        and team_id in group.member_team_ids
        for group in state.governance_groups.values()
    )

def _is_project_lead_for(state: TeamState, agent_id: str, project_id: str) -> bool:
    return project_lead_agent_id(state, project_id) == agent_id

def _agent_has_role_permission_for_project(
    state: TeamState,
    actor: PersistentAgent,
    permission: RolePermission,
    project_id: str,
) -> bool:
    project = state.projects.get(project_id)
    if project is None:
        return False
    if actor.team_id != project.team_id:
        return False
    if _is_team_manager(state, actor) and permission in effective_role_permissions(state, actor):
        return True
    return permission in effective_role_permissions(state, actor, project_id=project_id)

def _agent_in_project_hierarchy(state: TeamState, agent_id: str, project_id: str) -> bool:
    agent = state.agents.get(agent_id)
    return agent is not None and agent_has_project_assignment(state, agent_id, project_id)

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

__all__ = [
    "_default_role_id",
    "_role_id_part",
    "_project_assignment_id",
    "_normalize_role_permissions",
    "_agent_team_removal_blockers",
    "_validate_role_assignment",
    "_validate_role_filter_assignment",
    "_agent_payload",
    "_subordinate_agent_payloads",
    "_is_active_deliverable_work",
    "_task_has_active_deliverable_work",
    "_team_ids_with_open_work",
    "_visible_project_ids_for_agent",
    "_resolve_action_agent",
    "_agent_for_session",
    "_is_direct_subordinate",
    "_is_team_manager",
    "_agent_governs_team",
    "_is_project_lead_for",
    "_agent_has_role_permission_for_project",
    "_agent_in_project_hierarchy",
    "_supervises",
]
