"""Team relationship and outcome queries."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from .model_common import (
    AgentProjectAssignmentKind,
    RolePermission,
    TeamRecordState,
    TeamTaskState,
    _unique_text_list,
)
from .model_deliverables_git import (
    Deliverable,
)
from .model_inbox_memory import (
    AgentActionRecord,
)
from .model_organization import (
    AgentProjectAssignment,
    PersistentAgent,
)
from .model_state import (
    TeamState,
)
from .model_tasks import (
    AgentTask,
    OutcomeKindDefinition,
    OutcomeKindSnapshot,
    TaskAssessment,
)


def task_payload_with_derived(
    state: TeamState, task: AgentTask, *, child_task_ids: Sequence[str] | None = None,
    assessment: TaskAssessment | None = None,
    include_fields: Collection[str] | None = None,
) -> dict[str, Any]:
    payload = task.model_dump(
        mode="json", include=set(include_fields) if include_fields is not None else None,
    )
    payload["child_task_ids"] = (
        state.child_task_ids(task.id) if child_task_ids is None else list(child_task_ids)
    )
    payload["dependency_status"] = state.dependency_status(task.id)
    payload["blocked_by_task_ids"] = state.blocked_by_task_ids(task.id)
    payload["subtask_counts"] = state.subtask_counts(
        task.id, child_task_ids=payload["child_task_ids"],
    )
    payload["waiting_reason"] = _task_waiting_reason(state, task)
    if task.assignment_mode == "role":
        from .services.domain.role_task_queue import role_queue_wait_reason

        payload["assignment_wait_reason"] = role_queue_wait_reason(
            state, task, assessment=assessment,
        )
        role = state.roles.get(task.assignment_role_id or "")
        payload["assignment_role_name"] = role.name if role is not None else task.assignment_role_id
    return payload


def effective_outcome_kind_definitions(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    include_archived: bool = False,
) -> dict[str, OutcomeKindDefinition]:
    effective: dict[str, OutcomeKindDefinition] = {}
    for definition in sorted(
        state.outcome_kind_definitions.values(),
        key=lambda item: (item.project_id is not None, item.id),
    ):
        if definition.team_id != team_id:
            continue
        if definition.project_id not in {None, project_id}:
            continue
        if definition.project_id is not None and project_id is None:
            continue
        if definition.archived and not include_archived:
            effective.pop(definition.kind, None)
            continue
        effective[definition.kind] = definition
    return effective


def resolve_outcome_kind_definition(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    kind: str,
    include_archived: bool = False,
) -> OutcomeKindDefinition | None:
    return effective_outcome_kind_definitions(
        state,
        team_id=team_id,
        project_id=project_id,
        include_archived=include_archived,
    ).get(kind)


def outcome_kind_snapshots(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    kinds: Sequence[str],
    include_archived: bool = False,
) -> list[OutcomeKindSnapshot]:
    snapshots: list[OutcomeKindSnapshot] = []
    missing: list[str] = []
    for kind in _unique_text_list(list(kinds), "outcome kinds"):
        definition = resolve_outcome_kind_definition(
            state,
            team_id=team_id,
            project_id=project_id,
            kind=kind,
            include_archived=include_archived,
        )
        if definition is None:
            missing.append(kind)
        else:
            snapshots.append(definition.snapshot())
    if missing:
        raise ValueError("unknown or archived outcome kinds: " + ", ".join(missing))
    return snapshots


def deliverable_requires_formal_review(
    deliverable: Deliverable,
    *,
    state: TeamState | None = None,
) -> bool:
    has_current_review = bool(deliverable.review_request_ids)
    if state is not None:
        has_current_review = any(
            review_id in state.review_requests
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
            for review_id in deliverable.review_request_ids
        )
    return has_current_review or any(
        finding.state == "open" for finding in deliverable.carried_findings
    )


def _task_waiting_reason(state: TeamState, task: AgentTask) -> str | None:
    if task.state == TeamTaskState.WAITING_FOR_INPUT:
        return "input"
    if task.state != TeamTaskState.WAITING_FOR_REVIEW:
        return None
    active_deliverable_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
    deliverables = [
        state.deliverables[deliverable_id]
        for deliverable_id in task.deliverable_ids
        if deliverable_id in state.deliverables
        and state.deliverables[deliverable_id].superseded_by_deliverable_id is None
        and state.deliverables[deliverable_id].state in active_deliverable_states
    ]
    if any(
        deliverable_requires_formal_review(deliverable, state=state) for deliverable in deliverables
    ):
        return "review"
    if deliverables:
        return "superior_acceptance"
    return None


def _project_assignment_id(
    project_id: str,
    agent_id: str,
    kind: AgentProjectAssignmentKind,
) -> str:
    return f"{project_id}__{agent_id}__{kind.value}"


def project_assignments_for_agent(
    state: TeamState,
    agent_id: str,
    *,
    project_id: str | None = None,
    kind: AgentProjectAssignmentKind | None = None,
) -> list[AgentProjectAssignment]:
    return sorted(
        [
            assignment
            for assignment in state.project_assignments.values()
            if assignment.agent_id == agent_id
            and (project_id is None or assignment.project_id == project_id)
            and (kind is None or assignment.kind == kind)
        ],
        key=lambda item: (item.project_id, item.kind.value, item.id),
    )


def project_assignments_for_project(
    state: TeamState,
    project_id: str,
    *,
    kind: AgentProjectAssignmentKind | None = None,
) -> list[AgentProjectAssignment]:
    return sorted(
        [
            assignment
            for assignment in state.project_assignments.values()
            if assignment.project_id == project_id and (kind is None or assignment.kind == kind)
        ],
        key=lambda item: (item.kind.value, item.agent_id, item.id),
    )


def _agent_has_project_assignment(
    state: TeamState,
    agent_id: str,
    project_id: str,
    *,
    kind: AgentProjectAssignmentKind | None = None,
) -> bool:
    return bool(project_assignments_for_agent(state, agent_id, project_id=project_id, kind=kind))


def agent_has_project_assignment(
    state: TeamState,
    agent_id: str,
    project_id: str,
    *,
    kind: AgentProjectAssignmentKind | None = None,
) -> bool:
    return _agent_has_project_assignment(state, agent_id, project_id, kind=kind)


def project_lead_agent_id(state: TeamState, project_id: str) -> str | None:
    leads = project_assignments_for_project(
        state,
        project_id,
        kind=AgentProjectAssignmentKind.PROJECT_LEAD,
    )
    return leads[0].agent_id if leads else None


def agent_project_ids(state: TeamState, agent_id: str) -> list[str]:
    return sorted(
        {assignment.project_id for assignment in project_assignments_for_agent(state, agent_id)}
    )


def agent_lead_project_ids(state: TeamState, agent_id: str) -> list[str]:
    return sorted(
        {
            assignment.project_id
            for assignment in project_assignments_for_agent(
                state,
                agent_id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD,
            )
        }
    )


def agent_project_assignment_payloads(state: TeamState, agent_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assignment in project_assignments_for_agent(state, agent_id):
        payload = assignment.model_dump(mode="json")
        role = state.roles.get(assignment.role_id) if assignment.role_id is not None else None
        payload["role_name"] = role.name if role is not None else None
        payload["role_permissions"] = (
            [permission.value for permission in role.permissions] if role is not None else []
        )
        if (
            assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD
            and RolePermission.CREATE_ROOT_TASK.value not in payload["role_permissions"]
        ):
            payload["role_permissions"].append(RolePermission.CREATE_ROOT_TASK.value)
        rows.append(payload)
    return rows


def effective_role_permissions(
    state: TeamState,
    agent: PersistentAgent,
    *,
    project_id: str | None = None,
) -> list[RolePermission]:
    permissions: list[RolePermission] = []

    def add(items: list[RolePermission]) -> None:
        for item in items:
            if item not in permissions:
                permissions.append(item)

    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None and (
        role.project_id is None or project_id is None or role.project_id == project_id
    ):
        add(role.permissions)
    for assignment in project_assignments_for_agent(state, agent.id, project_id=project_id):
        assignment_role = (
            state.roles.get(assignment.role_id) if assignment.role_id is not None else None
        )
        if assignment_role is not None:
            add(assignment_role.permissions)
        if assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD:
            add([RolePermission.CREATE_ROOT_TASK])
    return permissions


def _effective_stand_down_payloads(
    state: TeamState,
    agent_id: str,
) -> list[dict[str, Any]]:
    root_ids: list[str] = []
    seen: set[str] = set()
    current_id: str | None = agent_id
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        root_ids.append(current_id)
        current = state.agents.get(current_id)
        current_id = current.reports_to_agent_id if current is not None else None
    root_set = set(root_ids)
    rows: list[dict[str, Any]] = []
    for stand_down in sorted(
        state.stand_downs.values(),
        key=lambda item: (item.set_at, item.id),
    ):
        if (
            not stand_down.active
            or stand_down.agent_id not in root_set
            or (stand_down.scope == "agent" and stand_down.agent_id != agent_id)
        ):
            continue
        row = stand_down.model_dump(mode="json")
        row["inherited"] = stand_down.agent_id != agent_id
        row["root_agent_id"] = stand_down.agent_id
        rows.append(row)
    return rows


def current_failed_action_records(state: TeamState) -> list[AgentActionRecord]:
    latest_records_by_agent: dict[str, AgentActionRecord] = {}
    for record in state.action_records.values():
        current = latest_records_by_agent.get(record.agent_id)
        if current is None or (record.created_at, record.id) > (
            current.created_at,
            current.id,
        ):
            latest_records_by_agent[record.agent_id] = record

    rows: list[AgentActionRecord] = []
    for latest_record in latest_records_by_agent.values():
        if latest_record.success:
            continue
        rows.extend(
            record
            for record in state.action_records.values()
            if record.agent_id == latest_record.agent_id
            and record.dispatch_id == latest_record.dispatch_id
            and not record.success
            and record.dismissed_at is None
        )
    return sorted(rows, key=lambda entry: (entry.created_at, entry.id))
