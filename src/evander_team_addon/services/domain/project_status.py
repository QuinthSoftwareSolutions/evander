from __future__ import annotations

from typing import Any

from ...models import (
    ProjectStatus,
    TaskAssessmentReadiness,
    TaskResolutionStatus,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamRecordState,
    TeamState,
)

from .blockers import resolve_active_blockers_for_source
from .common import utc_now
from .task_assessment import TaskAssessmentService


def project_blocking_sources(
    state: TeamState,
    project_id: str,
) -> list[dict[str, Any]]:
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
    project_releases = [
        release
        for release in state.release_packages.values()
        if release.project_id == project_id
    ]
    release_ids = {release.id for release in project_releases}
    terminal_release_review_ids = {
        review_id
        for release in project_releases
        if release.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
        for review_id in release.review_request_ids
    }
    project = state.projects.get(project_id)
    rows: list[dict[str, Any]] = []
    assessments = TaskAssessmentService(state).assess_all()
    rows.extend(
        {
            "kind": "task",
            "id": task.id,
            "state": assessments[task.id].readiness.value,
            "summary": task.title,
            "reason_code": (
                ",".join(assessments[task.id].invalid_reason_codes)
                or assessments[task.id].resolution_reason
            ),
            "source_kind": "task",
            "source_id": task.id,
            "affected_task_ids": [task.id],
            "next_action": (
                "repair invalid task state"
                if assessments[task.id].resolution_status
                == TaskResolutionStatus.INVALID
                else "resolve the task blocker"
            ),
        }
        for task in tasks
        if assessments[task.id].readiness
        in {TaskAssessmentReadiness.BLOCKED, TaskAssessmentReadiness.INVALID}
    )
    rows.extend(
        {
            "kind": "blocker",
            "id": blocker.id,
            "state": blocker.state.value,
            "summary": blocker.summary,
            "reason_code": blocker.reason_code,
            "source_kind": blocker.source_kind.value,
            "source_id": blocker.source_id,
            "affected_task_ids": list(blocker.affected_task_ids),
            "owner_agent_id": blocker.owner_agent_id,
            "delegate_agent_id": blocker.delegate_agent_id,
            "route": blocker.route.value,
            "escalation_depth": blocker.escalation_depth,
            "recovery_permission": (
                "admin.owner"
                if blocker.reason_code == "human_review_assignment_unavailable"
                else None
            ),
            "recovery_directory_path": (
                state.human_review_requests[
                    blocker.source_id
                ].requirement_snapshot.directory_path
                if blocker.reason_code == "human_review_assignment_unavailable"
                and blocker.source_id in state.human_review_requests
                else None
            ),
            "next_action": (
                "reassign the human reviewer in Admin"
                if blocker.reason_code == "human_review_assignment_unavailable"
                else "follow the blocker route and resolve its source condition"
            ),
        }
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and not (
            blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST
            and blocker.source_id in terminal_release_review_ids
        )
        and (
            blocker.project_id == project_id
            or blocker.task_id in task_ids
            or bool(set(blocker.affected_task_ids).intersection(task_ids))
        )
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
        if (
            review.deliverable_id in deliverable_ids
            or review.release_package_id in release_ids
        )
        and review.id not in terminal_release_review_ids
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
    rows.extend(
        {
            "kind": "human_review_request",
            "id": request.id,
            "state": request.state,
            "summary": f"Human review for release {request.release_package_id}",
            "reason_code": (
                "human_review_blocked"
                if request.state == "blocked"
                else "human_review_pending"
            ),
            "source_kind": "human_review_request",
            "source_id": request.id,
            "affected_task_ids": [],
            "next_action": (
                "recover or reassign the same human review request"
                if request.state == "blocked"
                else "assigned reviewer must decide the pinned candidate"
            ),
        }
        for request in state.human_review_requests.values()
        if request.release_package_id in release_ids
        and request.state in {"blocked"}
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
    terminal_release_reviews = {
        review_id: release
        for release in project_releases
        if release.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
        for review_id in release.review_request_ids
    }
    for review_id, release in terminal_release_reviews.items():
        resolve_active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
            source_id=review_id,
            actor_agent_id=None,
            dispatch_id=None,
            note=(
                f"Release package {release.id} reached terminal state "
                f"{release.state.value}; its review blocker is obsolete."
            ),
            reference_ids=[release.id],
            now=release.updated_at,
        )
    project_release_ids = {release.id for release in project_releases}
    project_human_requests = [
        request
        for request in state.human_review_requests.values()
        if request.release_package_id in project_release_ids
    ]
    project_human_request_ids = {
        request.id for request in project_human_requests
    }
    project_human_findings = [
        finding
        for finding in state.human_review_findings.values()
        if finding.source_human_review_request_id in project_human_request_ids
    ]
    assessment_service = TaskAssessmentService(state)
    assessments = {
        task.id: assessment_service.assess(task.id)
        for task in project_tasks
    }
    project_blockers = [
        blocker
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and (
            blocker.project_id == project_id
            or blocker.task_id in {task.id for task in project_tasks}
            or bool(
                set(blocker.affected_task_ids).intersection(
                    {task.id for task in project_tasks}
                )
            )
        )
    ]

    pending_or_blocked = {TeamRecordState.PENDING, TeamRecordState.BLOCKED}
    blocked = (
        any(
            assessment.readiness
            in {TaskAssessmentReadiness.BLOCKED, TaskAssessmentReadiness.INVALID}
            for assessment in assessments.values()
        )
        or bool(project_blockers)
        or any(goal.state == TeamRecordState.BLOCKED for goal in project.goals)
        or any(
            deliverable.state == TeamRecordState.BLOCKED
            for deliverable in active_project_deliverables
        )
        or any(review.state == TeamRecordState.BLOCKED for review in project_reviews)
        or any(release.state == TeamRecordState.BLOCKED for release in project_releases)
        or any(request.state == "blocked" for request in project_human_requests)
    )
    completed = (
        all(goal.state == TeamRecordState.ACCEPTED for goal in project.goals)
        and all(
            assessment.resolution_status == TaskResolutionStatus.RESOLVED
            for assessment in assessments.values()
        )
        and not any(
            deliverable.state in pending_or_blocked for deliverable in active_project_deliverables
        )
        and not any(review.state in pending_or_blocked for review in project_reviews)
        and not any(release.state in pending_or_blocked for release in project_releases)
        and not any(
            request.state in {"preparing", "pending", "blocked"}
            for request in project_human_requests
        )
        and not any(finding.state == "open" for finding in project_human_findings)
        and not project_blockers
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
