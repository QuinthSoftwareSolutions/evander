from __future__ import annotations

from ...models import (
    ProjectStatus,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)

from .common import (
    _NON_TERMINAL_TASK_STATES,
    utc_now,
)


def _recalculate_project_status(state: TeamState, project_id: str) -> None:
    project = state.projects.get(project_id)
    if project is None or project.status == ProjectStatus.ARCHIVED:
        return
    project_tasks = [task for task in state.tasks.values() if task.project_id == project_id]
    project_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if (task := state.tasks.get(deliverable.task_id)) is not None
        and task.project_id == project_id
    ]
    active_project_deliverables = [
        deliverable
        for deliverable in project_deliverables
        if deliverable.superseded_by_deliverable_id is None
    ]
    active_deliverable_ids = {deliverable.id for deliverable in active_project_deliverables}
    project_reviews = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id in active_deliverable_ids
    ]
    project_releases = [
        release for release in state.release_packages.values() if release.project_id == project_id
    ]

    pending_or_blocked = {TeamRecordState.PENDING, TeamRecordState.BLOCKED}
    blocked = (
        any(task.state == TeamTaskState.BLOCKED for task in project_tasks)
        or any(goal.state == TeamRecordState.BLOCKED for goal in project.goals)
        or any(
            deliverable.state == TeamRecordState.BLOCKED
            for deliverable in active_project_deliverables
        )
        or any(review.state == TeamRecordState.BLOCKED for review in project_reviews)
        or any(release.state == TeamRecordState.BLOCKED for release in project_releases)
    )
    completed = (
        all(goal.state == TeamRecordState.ACCEPTED for goal in project.goals)
        and not any(task.state in _NON_TERMINAL_TASK_STATES for task in project_tasks)
        and not any(
            deliverable.state in pending_or_blocked for deliverable in active_project_deliverables
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


__all__ = [
    "_recalculate_project_status",
]
