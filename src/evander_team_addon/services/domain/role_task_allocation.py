from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from evander.models import utc_now

from ...models import (
    AgentStatus,
    PersistentAgent,
    TeamInboxItemState,
    TeamState,
    _ensure_utc,
    _runtime_blockers_due_for_agent,
    agent_has_project_assignment,
)
from .stand_down import _active_stand_downs_for_agent
from .wake import (
    _active_tasks_for_agent,
    _pending_reviews_for_agent,
    _task_can_wake_owner_for_work,
)


ROLE_TASK_ALLOCATION_POLICY = "work_aware_least_loaded_v1"


class RoleTaskAllocationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        correction_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.correction_context = correction_context or {}


@dataclass(frozen=True)
class RoleTaskOwnerSelection:
    role_id: str
    project_id: str
    selected_owner_agent_id: str
    candidate_scores: tuple[dict[str, Any], ...]
    exclusion_reason_counts: dict[str, int]

    @property
    def eligible_candidate_count(self) -> int:
        return len(self.candidate_scores)

    def response_payload(
        self,
        *,
        outcome: str,
        previous_owner_agent_id: str | None,
    ) -> dict[str, Any]:
        return {
            "assignment_outcome": outcome,
            "assignment_policy": ROLE_TASK_ALLOCATION_POLICY,
            "assignment_role_id": self.role_id,
            "selected_owner_agent_id": self.selected_owner_agent_id,
            "previous_owner_agent_id": previous_owner_agent_id,
            "eligible_candidate_count": self.eligible_candidate_count,
            "candidate_scores": [dict(row) for row in self.candidate_scores],
        }


def select_role_task_owner(
    state: TeamState,
    *,
    actor: PersistentAgent,
    project_id: str,
    role_id: str,
    candidate_authorization_error: Callable[[PersistentAgent], str | None],
    now: datetime | None = None,
    allow_unavailable: bool = False,
) -> RoleTaskOwnerSelection:
    project = state.projects.get(project_id)
    if project is None:
        raise RoleTaskAllocationError(
            "project_not_found",
            f"project {project_id} does not exist",
            correction_context={"project_id": project_id},
        )
    role = state.roles.get(role_id)
    if role is None:
        raise RoleTaskAllocationError(
            "role_not_found",
            f"role {role_id} does not exist",
            correction_context={"role_id": role_id, "project_id": project_id},
        )
    if role.archived:
        raise RoleTaskAllocationError(
            "role_archived",
            f"role {role_id} is archived",
            correction_context={"role_id": role_id, "project_id": project_id},
        )
    if role.team_id != project.team_id or role.team_id != actor.team_id:
        raise RoleTaskAllocationError(
            "role_team_mismatch",
            f"role {role_id} does not belong to project team {project.team_id}",
            correction_context={
                "role_id": role_id,
                "role_team_id": role.team_id,
                "project_id": project_id,
                "project_team_id": project.team_id,
            },
        )
    if role.project_id is not None and role.project_id != project_id:
        raise RoleTaskAllocationError(
            "role_project_mismatch",
            f"role {role_id} does not apply to project {project_id}",
            correction_context={
                "role_id": role_id,
                "role_project_id": role.project_id,
                "project_id": project_id,
            },
        )

    effective_now = _ensure_utc(now or utc_now())
    exclusion_counts: Counter[str] = Counter()
    scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for candidate in sorted(state.agents.values(), key=lambda item: item.id):
        if candidate.team_id != project.team_id:
            continue
        if not agent_has_project_assignment(state, candidate.id, project_id):
            exclusion_counts["agent_not_project_assigned"] += 1
            continue
        if not _agent_has_effective_project_role(
            state,
            agent=candidate,
            project_id=project_id,
            role_id=role_id,
        ):
            exclusion_counts["role_not_effective_for_project"] += 1
            continue
        if candidate.status == AgentStatus.ARCHIVED or (
            not allow_unavailable and candidate.status != AgentStatus.ACTIVE
        ):
            exclusion_counts["agent_not_active"] += 1
            continue
        if not allow_unavailable and _active_stand_downs_for_agent(state, candidate.id):
            exclusion_counts["agent_stood_down"] += 1
            continue
        authorization_error = candidate_authorization_error(candidate)
        if authorization_error is not None:
            exclusion_counts["assignment_not_permitted"] += 1
            continue
        sort_key, payload = _candidate_load_score(
            state,
            agent=candidate,
            now=effective_now,
        )
        scored.append((sort_key, payload))

    if not scored:
        raise RoleTaskAllocationError(
            "role_assignment_no_eligible_agent",
            f"role {role_id} has no eligible agent for project {project_id}",
            correction_context={
                "role_id": role_id,
                "project_id": project_id,
                "assignment_policy": ROLE_TASK_ALLOCATION_POLICY,
                "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
            },
        )

    scored.sort(key=lambda item: item[0])
    candidate_scores = tuple(
        {**payload, "rank": index}
        for index, (_sort_key, payload) in enumerate(scored, start=1)
    )
    return RoleTaskOwnerSelection(
        role_id=role_id,
        project_id=project_id,
        selected_owner_agent_id=str(candidate_scores[0]["agent_id"]),
        candidate_scores=candidate_scores,
        exclusion_reason_counts=dict(sorted(exclusion_counts.items())),
    )


def _agent_has_effective_project_role(
    state: TeamState,
    *,
    agent: PersistentAgent,
    project_id: str,
    role_id: str,
) -> bool:
    if agent.role_id == role_id:
        return True
    return any(
        assignment.agent_id == agent.id
        and assignment.project_id == project_id
        and assignment.role_id == role_id
        for assignment in state.project_assignments.values()
    )


def _candidate_load_score(
    state: TeamState,
    *,
    agent: PersistentAgent,
    now: datetime,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    active_tasks = _active_tasks_for_agent(state, agent.id)
    ready_tasks = [
        task
        for task in active_tasks
        if _task_can_wake_owner_for_work(state, task)
        and (
            state.dependency_status(task.id) == "satisfied"
            or state.subtask_counts(task.id)["open"] > 0
        )
    ]
    pending_reviews = _pending_reviews_for_agent(state, agent.id)
    due_blockers = _runtime_blockers_due_for_agent(
        state,
        agent_id=agent.id,
        now=now,
    )
    response_required_inbox = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent.id
        and item.state == TeamInboxItemState.PENDING
        and item.response_requested
    ]
    ready_work_count = (
        len(ready_tasks)
        + len(pending_reviews)
        + len(due_blockers)
        + len(response_required_inbox)
    )
    runtime = state.agent_runtimes.get(agent.id)
    lease_active = bool(
        runtime is not None
        and runtime.wake_leased_until is not None
        and _ensure_utc(runtime.wake_leased_until) > now
    )
    active_dispatch = bool(runtime is not None and runtime.active_dispatch_id)
    busy = active_dispatch or lease_active
    last_wake_at = runtime.last_wake_at if runtime is not None else None
    last_wake_sort = (
        _ensure_utc(last_wake_at)
        if last_wake_at is not None
        else datetime.min.replace(tzinfo=UTC)
    )
    payload = {
        "agent_id": agent.id,
        "ready_work_count": ready_work_count,
        "ready_task_count": len(ready_tasks),
        "pending_review_count": len(pending_reviews),
        "due_blocker_count": len(due_blockers),
        "response_required_inbox_count": len(response_required_inbox),
        "busy": busy,
        "active_dispatch": active_dispatch,
        "lease_active": lease_active,
        "open_task_count": len(active_tasks),
        "last_wake_at": last_wake_at.isoformat() if last_wake_at is not None else None,
    }
    return (
        (
            ready_work_count,
            int(busy),
            len(active_tasks),
            last_wake_sort,
            agent.id,
        ),
        payload,
    )


__all__ = [
    "ROLE_TASK_ALLOCATION_POLICY",
    "RoleTaskAllocationError",
    "RoleTaskOwnerSelection",
    "select_role_task_owner",
]
