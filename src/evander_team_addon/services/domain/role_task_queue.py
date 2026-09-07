"""Durable exact-role work, claimed only alongside an execution reservation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from ...models import (
    AgentStatus,
    AgentTask,
    DependencyCompositionRecoveryState,
    TeamState,
    TeamTaskState,
    TaskAssessmentReadiness,
    _ensure_utc,
)
from .case_task import _task_creation_owner_assignment_error
from .dispatch_scope import actionable_dispatch_tasks
from .preparation_failures import task_preparation_generation
from .role_task_allocation import RoleTaskAllocationError, select_role_task_owner
from .task_assessment import TaskAssessmentService

ROLE_QUEUE_WAKE_EVENT = "team.role_queue.wake_requested"
ROLE_QUEUE_POLICY = "role_queue_v1"


def set_unstarted_role_task_owner(
    state: TeamState, *, task: AgentTask, owner_agent_id: str | None,
) -> None:
    """Carry current accepted recovery provenance across a reversible role claim.

    A claim changes the owner in the preparation fingerprint without changing the
    reviewed source. Preserve only recoveries valid before that change; stale
    recoveries and explicit task reassignments must still fail provenance checks.
    Use this for both reservation and release before execution starts.
    """
    if task.assignment_mode != "role" or task.assignment_started_at is not None:
        raise ValueError("Only an unstarted role task can change its reserved owner")
    if task.owner_agent_id == owner_agent_id:
        return
    accepted = [
        recovery for recovery in state.dependency_composition_recoveries.values()
        if recovery.target_task_id == task.id
        and recovery.state == DependencyCompositionRecoveryState.ACCEPTED
    ]
    if accepted:
        generation = task_preparation_generation(state, task.id)
        accepted = [
            recovery for recovery in accepted
            if recovery.preparation_generation == generation
        ]
    task.owner_agent_id = owner_agent_id
    if accepted:
        generation = task_preparation_generation(state, task.id)
        for recovery in accepted:
            recovery.preparation_generation = generation


def queued_role_tasks(state: TeamState) -> list[AgentTask]:
    return sorted(
        (
            task
            for task in state.tasks.values()
            if task.assignment_mode == "role"
            and task.owner_agent_id is None
            and task.state == TeamTaskState.PENDING
        ),
        key=lambda task: (task.priority, task.created_at, task.id),
    )


def append_role_queue_wake_events(
    state: TeamState,
    *,
    result: dict[str, Any],
    now: datetime,
    project_id: str | None = None,
    team_id: str | None = None,
) -> None:
    projects = sorted(
        {
            task.project_id
            for task in queued_role_tasks(state)
            if (project_id is None or task.project_id == project_id)
            and (team_id is None or state.projects[task.project_id].team_id == team_id)
        }
    )
    for project in projects:
        event = {
            "event_type": ROLE_QUEUE_WAKE_EVENT,
            "correlation_key": f"{ROLE_QUEUE_WAKE_EVENT}:{project}",
            "idempotency_key": f"{ROLE_QUEUE_WAKE_EVENT}:{project}:{int(now.timestamp())}",
            "delay_seconds": 1.0,
            "payload": {"project_id": project, "team_id": state.projects[project].team_id},
        }
        if event not in result.setdefault("events", []):
            result["events"].append(event)


def plan_role_task_allocations(
    state: TeamState,
    *,
    now: datetime,
    limit: int,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    blocked_team_ids: set[str],
    max_no_progress_count: int,
    session_is_busy: Callable[[str], bool] | None,
    respect_wake_interval: bool,
) -> dict[str, dict[str, Any]]:
    """Tentatively assign free agents; caller commits only tasks in new dispatches.

    Runs inside the same revision-checked planning/commit as dispatch reservation.
    External execution is never started here. Caller discards assignments which
    lose precedence to chat, context handoff, or another execution gate.
    """
    from .wake import _ordinary_wake_work_for_agent
    from .role_task_allocation import _candidate_load_score

    selections: dict[str, dict[str, Any]] = {}
    reserved_agents: set[str] = set()
    assessments = TaskAssessmentService(state)
    for task in queued_role_tasks(state):
        project = state.projects[task.project_id]
        if (
            (team_id is not None and project.team_id != team_id)
            or (project_id is not None and project.id != project_id)
            or state.teams[project.team_id].archived
            or project.team_id in blocked_team_ids
            or len(selections) >= limit
        ):
            continue
        if (
            state.dependency_status(task.id) != "satisfied"
            or assessments.assess(task.id).readiness != TaskAssessmentReadiness.ACTIONABLE
        ):
            continue
        creator = state.agents.get(task.created_by_agent_id or "")
        if creator is None or creator.status != AgentStatus.ACTIVE:
            continue

        def authorization_error(candidate):
            if candidate.id in reserved_agents or (
                agent_id is not None and candidate.id != agent_id
            ):
                return "outside current capacity"
            runtime = state.agent_runtimes.get(candidate.id)
            if (
                respect_wake_interval
                and runtime is not None
                and runtime.next_wake_at is not None
                and _ensure_utc(runtime.next_wake_at) > now
            ):
                return "wake interval not due"
            if runtime is not None and (
                runtime.active_dispatch_id
                or (runtime.wake_leased_until is not None and runtime.wake_leased_until > now)
                or (
                    max_no_progress_count >= 0
                    and runtime.no_progress_count >= max_no_progress_count
                )
            ):
                return "execution unavailable"
            if session_is_busy is not None and session_is_busy(candidate.session_id):
                return "session busy"
            if actionable_dispatch_tasks(state, agent=candidate):
                return "existing ready task work"
            _pending, wake_inbox, reviews = _ordinary_wake_work_for_agent(state, candidate.id)
            _score, load = _candidate_load_score(state, agent=candidate, now=now)
            if wake_inbox or reviews or load["due_blocker_count"]:
                return "existing response or recovery work"
            return _task_creation_owner_assignment_error(
                state,
                actor=creator,
                owner_id=candidate.id,
                project_id=task.project_id,
            )

        try:
            selection = select_role_task_owner(
                state,
                actor=creator,
                project_id=task.project_id,
                role_id=task.assignment_role_id or "",
                now=now,
                candidate_authorization_error=authorization_error,
            )
        except RoleTaskAllocationError:
            continue
        set_unstarted_role_task_owner(
            state, task=task, owner_agent_id=selection.selected_owner_agent_id,
        )
        reserved_agents.add(task.owner_agent_id)
        selections[task.id] = {
            **selection.response_payload(outcome="reserved", previous_owner_agent_id=None),
            "assignment_policy": ROLE_QUEUE_POLICY,
            "task_id": task.id,
        }
    return selections


def role_queue_wait_reason(state: TeamState, task: AgentTask) -> str | None:
    if task.assignment_mode != "role" or task.owner_agent_id is not None:
        return None
    if task.state != TeamTaskState.PENDING:
        return task.state.value
    if state.dependency_status(task.id) != "satisfied":
        return "waiting_for_dependencies"
    if TaskAssessmentService(state).assess(task.id).readiness != TaskAssessmentReadiness.ACTIONABLE:
        return "waiting_for_execution_gates"
    creator = state.agents.get(task.created_by_agent_id or "")
    if creator is None or creator.status != AgentStatus.ACTIVE:
        return "waiting_for_assignment_authority"
    try:
        select_role_task_owner(
            state,
            actor=creator,
            project_id=task.project_id,
            role_id=task.assignment_role_id or "",
            candidate_authorization_error=lambda agent: _task_creation_owner_assignment_error(
                state, actor=creator, owner_id=agent.id, project_id=task.project_id
            ),
        )
    except RoleTaskAllocationError:
        return "waiting_for_eligible_role_holder"
    return "waiting_for_execution_capacity"


def can_manage_unclaimed_role_task(state: TeamState, task: AgentTask, actor_id: str) -> bool:
    from .agent_access import _is_project_lead_for, _is_team_manager, _supervises

    actor = state.agents.get(actor_id)
    project = state.projects.get(task.project_id)
    return bool(
        task.assignment_mode == "role"
        and task.owner_agent_id is None
        and actor is not None
        and project is not None
        and actor.team_id == project.team_id
        and (
            task.created_by_agent_id == actor_id
            or _supervises(state, actor_id, task.created_by_agent_id)
            or _is_project_lead_for(state, actor_id, task.project_id)
            or _is_team_manager(state, actor)
        )
    )
