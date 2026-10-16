from __future__ import annotations

from datetime import datetime

from ...models import (
    AgentTask,
    Deliverable,
    DeliverableInitialReleaseDisposition,
    InitialReleaseDispositionState,
    PersistentAgent,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)
from .common import _required_text
from .agent_access import _is_project_lead_for, _is_team_manager


_SYSTEM_OWNED_RELEASE_FOLLOW_UP_ACTORS = frozenset(
    {
        "system:candidate-correction-recovery",
        "system:git-merge-recovery",
        "system:human-review",
        "system:release-review-correction",
        "system:release-verification-correction",
    }
)


def initialize_pending_initial_release_disposition(
    state: TeamState,
    *,
    task: AgentTask,
    deliverable: Deliverable,
    now: datetime,
) -> bool:
    """Track only newly accepted ordinary roots; legacy rows remain untracked."""

    project = state.projects.get(task.project_id)
    if (
        deliverable.initial_release_disposition is not None
        or deliverable.state != TeamRecordState.ACCEPTED
        or deliverable.superseded_by_deliverable_id is not None
        or deliverable.task_id != task.id
        or task.parent_task_id is not None
        or task.state not in {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}
        or task.created_by_actor in _SYSTEM_OWNED_RELEASE_FOLLOW_UP_ACTORS
        or project is None
        or project.release_policy is None
    ):
        return False
    deliverable.initial_release_disposition = DeliverableInitialReleaseDisposition(
        state=InitialReleaseDispositionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    return True


def deliverable_has_release_package(state: TeamState, deliverable_id: str) -> bool:
    records = (
        state.query_records(
            "release_packages",
            "id IN (SELECT package_id FROM team_release_deliverables WHERE deliverable_id=?)",
            (deliverable_id,),
        )
        if hasattr(state, "query_records")
        else state.release_packages.values()
    )
    return any(deliverable_id in release.deliverable_ids for release in records)


def initial_release_decision_project_id(
    state: TeamState,
    deliverable_id: str,
) -> str | None:
    deliverable = state.deliverables.get(deliverable_id)
    task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
    return task.project_id if task is not None else None


def initial_release_decision_permitted(
    state: TeamState,
    *,
    actor: PersistentAgent,
    deliverable_id: str,
) -> bool:
    project_id = initial_release_decision_project_id(state, deliverable_id)
    project = state.projects.get(project_id or "")
    return bool(
        project is not None
        and (
            _is_project_lead_for(state, actor.id, project.id)
            or (_is_team_manager(state, actor) and actor.team_id == project.team_id)
        )
    )


def manager_fallback_initial_release_authority_error(
    state: TeamState,
    *,
    manager_agent_id: str,
    deliverable_id: str,
) -> str | None:
    manager = state.agents.get(manager_agent_id)
    deliverable = state.deliverables.get(deliverable_id)
    task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
    project = state.projects.get(task.project_id) if task is not None else None
    disposition = deliverable.initial_release_disposition if deliverable is not None else None
    if manager is None or not _is_team_manager(state, manager):
        return f"agent {manager_agent_id} is no longer a top-level team manager"
    if deliverable is None or task is None or project is None:
        return f"initial release deliverable {deliverable_id} no longer has project lineage"
    if project.team_id != manager.team_id:
        return (
            f"initial release deliverable {deliverable_id} no longer belongs to "
            f"manager team {manager.team_id}"
        )
    if (
        disposition is None
        or disposition.state != InitialReleaseDispositionState.PENDING
        or deliverable.id
        not in {
            candidate.id
            for candidate in pending_initial_release_deliverables(
                state,
                project_id=project.id,
            )
        }
    ):
        return f"initial release deliverable {deliverable_id} is no longer pending"
    return None


def pending_initial_release_deliverables(
    state: TeamState,
    *,
    team_id: str | None = None,
    project_id: str | None = None,
) -> list[Deliverable]:
    rows: list[Deliverable] = []
    for deliverable in state.deliverables.values():
        disposition = deliverable.initial_release_disposition
        task = state.tasks.get(deliverable.task_id)
        project = state.projects.get(task.project_id) if task is not None else None
        if (
            disposition is None
            or disposition.state != InitialReleaseDispositionState.PENDING
            or deliverable.state != TeamRecordState.ACCEPTED
            or deliverable.superseded_by_deliverable_id is not None
            or task is None
            or task.parent_task_id is not None
            or task.state not in {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}
            or project is None
            or (team_id is not None and project.team_id != team_id)
            or (project_id is not None and project.id != project_id)
            or deliverable_has_release_package(state, deliverable.id)
        ):
            continue
        rows.append(deliverable)
    return sorted(rows, key=lambda item: (item.created_at, item.id))


def mark_initial_release_requested(
    state: TeamState,
    *,
    deliverable_ids: list[str],
    release_package_id: str,
    decided_by_actor: str,
    now: datetime,
) -> list[str]:
    normalized_release_id = _required_text(release_package_id, "release_package_id")
    normalized_actor = _required_text(decided_by_actor, "decided_by_actor")
    updated: list[str] = []
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        disposition = deliverable.initial_release_disposition if deliverable is not None else None
        if disposition is None or disposition.state != InitialReleaseDispositionState.PENDING:
            continue
        deliverable.initial_release_disposition = DeliverableInitialReleaseDisposition(
            state=InitialReleaseDispositionState.RELEASE_REQUESTED,
            created_at=disposition.created_at,
            updated_at=now,
            decided_at=now,
            decided_by_actor=normalized_actor,
            release_package_id=normalized_release_id,
            reopened_at=disposition.reopened_at,
            reopened_by_actor=disposition.reopened_by_actor,
            reopen_reason=disposition.reopen_reason,
        )
        updated.append(deliverable.id)
    handle_initial_release_resolution_notices(
        state,
        deliverable_ids=updated,
        now=now,
    )
    return updated


def mark_initial_release_not_planned(
    state: TeamState,
    *,
    deliverable_ids: list[str],
    decided_by_actor: str,
    reason: str,
    now: datetime,
) -> list[str]:
    normalized_actor = _required_text(decided_by_actor, "decided_by_actor")
    normalized_reason = _required_text(reason, "reason")
    updated: list[str] = []
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            raise ValueError(f"deliverable {deliverable_id} does not exist")
        disposition = deliverable.initial_release_disposition
        if disposition is None or disposition.state != InitialReleaseDispositionState.PENDING:
            state_name = disposition.state.value if disposition is not None else "untracked"
            raise ValueError(
                f"deliverable {deliverable.id} initial release disposition is "
                f"{state_name}, not pending"
            )
        if deliverable_has_release_package(state, deliverable.id):
            raise ValueError(
                f"deliverable {deliverable.id} is already included in a release package"
            )
        deliverable.initial_release_disposition = DeliverableInitialReleaseDisposition(
            state=InitialReleaseDispositionState.NOT_PLANNED,
            created_at=disposition.created_at,
            updated_at=now,
            decided_at=now,
            decided_by_actor=normalized_actor,
            reason=normalized_reason,
            reopened_at=disposition.reopened_at,
            reopened_by_actor=disposition.reopened_by_actor,
            reopen_reason=disposition.reopen_reason,
        )
        updated.append(deliverable.id)
    handle_initial_release_resolution_notices(
        state,
        deliverable_ids=updated,
        now=now,
    )
    return updated


def reopen_initial_release_decisions(
    state: TeamState,
    *,
    deliverable_ids: list[str],
    reopened_by_actor: str,
    reason: str,
    now: datetime,
) -> list[str]:
    normalized_actor = _required_text(reopened_by_actor, "reopened_by_actor")
    normalized_reason = _required_text(reason, "reason")
    updated: list[str] = []
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            raise ValueError(f"deliverable {deliverable_id} does not exist")
        disposition = deliverable.initial_release_disposition
        if disposition is None or disposition.state != InitialReleaseDispositionState.NOT_PLANNED:
            state_name = disposition.state.value if disposition is not None else "untracked"
            raise ValueError(
                f"deliverable {deliverable.id} initial release disposition is "
                f"{state_name}, not not_planned"
            )
        if deliverable_has_release_package(state, deliverable.id):
            raise ValueError(
                f"deliverable {deliverable.id} is already included in a release package"
            )
        deliverable.initial_release_disposition = DeliverableInitialReleaseDisposition(
            state=InitialReleaseDispositionState.PENDING,
            created_at=disposition.created_at,
            updated_at=now,
            reopened_at=now,
            reopened_by_actor=normalized_actor,
            reopen_reason=normalized_reason,
        )
        updated.append(deliverable.id)
    return updated


def handle_initial_release_resolution_notices(
    state: TeamState,
    *,
    deliverable_ids: list[str],
    now: datetime,
) -> list[str]:
    selected = set(deliverable_ids)
    if not selected:
        return []
    handled: list[str] = []
    for item in state.inbox.values():
        if (
            item.state == TeamInboxItemState.PENDING
            and item.payload.get("kind") == "root_task_resolution"
            and item.payload.get("deliverable_id") in selected
        ):
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = now
            handled.append(item.id)
    return handled


__all__ = [
    "deliverable_has_release_package",
    "handle_initial_release_resolution_notices",
    "initial_release_decision_permitted",
    "initial_release_decision_project_id",
    "initialize_pending_initial_release_disposition",
    "mark_initial_release_not_planned",
    "mark_initial_release_requested",
    "pending_initial_release_deliverables",
    "manager_fallback_initial_release_authority_error",
    "reopen_initial_release_decisions",
]
