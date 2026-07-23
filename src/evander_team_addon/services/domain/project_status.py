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


def project_blocking_sources(
    state: TeamState,
    project_id: str,
) -> list[dict[str, str]]:
    """Return the exact current records that make a project blocked."""

    tasks = [task for task in state.tasks.values() if task.project_id == project_id]
    task_ids = {task.id for task in tasks}
    deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if deliverable.task_id in task_ids
        and deliverable.superseded_by_deliverable_id is None
    ]
    deliverable_ids = {deliverable.id for deliverable in deliverables}
    project = state.projects.get(project_id)
    rows: list[dict[str, str]] = []
    rows.extend(
        {
            "kind": "task",
            "id": task.id,
            "state": task.state.value,
            "summary": task.title,
        }
        for task in tasks
        if task.state == TeamTaskState.BLOCKED
    )
    if project is not None:
        rows.extend(
            {
                "kind": "goal",
                "id": goal.id,
                "state": goal.state.value,
                "summary": goal.description,
            }
            for goal in project.goals
            if goal.state == TeamRecordState.BLOCKED
        )
    rows.extend(
        {
            "kind": "deliverable",
            "id": deliverable.id,
            "state": deliverable.state.value,
            "summary": deliverable.summary,
        }
        for deliverable in deliverables
        if deliverable.state == TeamRecordState.BLOCKED
    )
    rows.extend(
        {
            "kind": "review_request",
            "id": review.id,
            "state": review.state.value,
            "summary": (
                "; ".join(review.findings)
                or f"Review of {review.deliverable_id or review.release_package_id}"
            ),
        }
        for review in state.review_requests.values()
        if review.deliverable_id in deliverable_ids
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
        and review.state == TeamRecordState.BLOCKED
    )
    rows.extend(
        {
            "kind": "release_package",
            "id": release.id,
            "state": release.state.value,
            "summary": release.error or f"Release {release.id} is blocked",
        }
        for release in state.release_packages.values()
        if release.project_id == project_id
        and release.state == TeamRecordState.BLOCKED
    )
    return sorted(rows, key=lambda row: (row["kind"], row["id"]))


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
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
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
    "project_blocking_sources",
]
