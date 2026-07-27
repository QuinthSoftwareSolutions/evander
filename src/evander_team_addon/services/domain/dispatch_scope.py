from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from ...models import (
    AgentInboxItem,
    AgentTask,
    PersistentAgent,
    ReviewRequest,
    TaskAssessmentReadiness,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamRecordState,
    TeamState,
)
from .agent_access import _visible_project_ids_for_agent
from .task_assessment import TaskAssessmentService


@dataclass(frozen=True)
class AgentDispatchScope:
    task_ids: tuple[str, ...]
    blocker_ids: tuple[str, ...]
    review_request_ids: tuple[str, ...]
    inbox_item_ids: tuple[str, ...]
    project_ids: tuple[str, ...]
    managed_repo_ids: tuple[str, ...]

    def model_fields(self) -> dict[str, list[str]]:
        return {
            "task_ids": list(self.task_ids),
            "blocker_ids": list(self.blocker_ids),
            "review_request_ids": list(self.review_request_ids),
            "inbox_item_ids": list(self.inbox_item_ids),
            "project_ids": list(self.project_ids),
            "managed_repo_ids": list(self.managed_repo_ids),
        }


def actionable_dispatch_tasks(
    state: TeamState,
    *,
    agent: PersistentAgent,
) -> list[AgentTask]:
    if agent.team_id is None:
        return []
    visible_project_ids = _visible_project_ids_for_agent(state, agent)
    assessments = TaskAssessmentService(state)
    stalled_task_ids = _stalled_task_ids(state, agent_id=agent.id)
    rows = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent.id
        and task.project_id in visible_project_ids
        and task.project_id in state.projects
        and state.projects[task.project_id].team_id == agent.team_id
        and task.id not in stalled_task_ids
        and assessments.assess(task.id).readiness == TaskAssessmentReadiness.ACTIONABLE
    ]
    rows.sort(key=lambda item: (item.priority, item.created_at, item.id))
    return rows


def actionable_dispatch_reviews(
    reviews: Iterable[ReviewRequest],
) -> list[ReviewRequest]:
    rows = [
        review
        for review in reviews
        if review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
    ]
    rows.sort(
        key=lambda item: (
            item.created_at or datetime.min.replace(tzinfo=UTC),
            item.id,
        )
    )
    return rows


def build_agent_dispatch_scope(
    state: TeamState,
    *,
    agent: PersistentAgent,
    tasks: Iterable[AgentTask],
    blockers: Iterable[TeamBlocker],
    reviews: Iterable[ReviewRequest],
    inbox_items: Iterable[AgentInboxItem],
) -> AgentDispatchScope:
    task_rows = _unique_by_id(tasks)
    blocker_rows = _unique_by_id(blockers)
    review_rows = _unique_by_id(reviews)
    inbox_rows = _unique_by_id(inbox_items)

    project_ids: set[str] = set()
    for task in task_rows:
        project_ids.add(task.project_id)
    for blocker in blocker_rows:
        project_ids.update(_blocker_project_ids(state, blocker))
    for review in review_rows:
        project_ids.update(_review_project_ids(state, review))
    for item in inbox_rows:
        project_ids.update(_inbox_project_ids(state, item))

    scoped_project_ids = tuple(
        sorted(
            project_id
            for project_id in project_ids
            if project_id in state.projects
            and state.projects[project_id].team_id == agent.team_id
        )
    )
    managed_repo_ids = tuple(
        sorted(
            {
                project.git.managed_repo_id
                for project_id in scoped_project_ids
                if (project := state.projects[project_id]).git is not None
            }
        )
    )
    return AgentDispatchScope(
        task_ids=tuple(row.id for row in task_rows),
        blocker_ids=tuple(row.id for row in blocker_rows),
        review_request_ids=tuple(row.id for row in review_rows),
        inbox_item_ids=tuple(row.id for row in inbox_rows),
        project_ids=scoped_project_ids,
        managed_repo_ids=managed_repo_ids,
    )


def _unique_by_id(rows: Iterable[object]) -> list:
    by_id = {str(getattr(row, "id")): row for row in rows}
    return [by_id[row_id] for row_id in sorted(by_id)]


def _stalled_task_ids(state: TeamState, *, agent_id: str) -> set[str]:
    active_stalls = [
        blocker
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL
        and blocker.source_id == agent_id
    ]
    if not active_stalls:
        return set()
    affected = {
        task_id
        for blocker in active_stalls
        for task_id in blocker.affected_task_ids
    }
    if affected:
        return affected
    return {
        task.id
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id
    }


def _task_project_id(state: TeamState, task_id: object) -> str | None:
    task = state.tasks.get(str(task_id or ""))
    return task.project_id if task is not None else None


def _deliverable_project_id(state: TeamState, deliverable_id: object) -> str | None:
    deliverable = state.deliverables.get(str(deliverable_id or ""))
    return (
        _task_project_id(state, deliverable.task_id)
        if deliverable is not None
        else None
    )


def _release_project_id(state: TeamState, release_id: object) -> str | None:
    release = state.release_packages.get(str(release_id or ""))
    return release.project_id if release is not None else None


def _review_project_ids(state: TeamState, review: ReviewRequest) -> set[str]:
    project_ids = {
        project_id
        for project_id in (
            _deliverable_project_id(state, review.deliverable_id),
            _release_project_id(state, review.release_package_id),
        )
        if project_id is not None
    }
    return project_ids


def _blocker_project_ids(state: TeamState, blocker: TeamBlocker) -> set[str]:
    project_ids = {
        project_id
        for project_id in (
            blocker.project_id,
            _task_project_id(state, blocker.task_id),
        )
        if project_id is not None
    }
    project_ids.update(
        project_id
        for task_id in blocker.affected_task_ids
        if (project_id := _task_project_id(state, task_id)) is not None
    )
    if blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
        review = state.review_requests.get(blocker.source_id)
        if review is not None:
            project_ids.update(_review_project_ids(state, review))
    elif blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
        project_id = _deliverable_project_id(state, blocker.source_id)
        if project_id is not None:
            project_ids.add(project_id)
    elif blocker.source_kind in {
        TeamBlockerSourceKind.RELEASE_PACKAGE,
        TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
    }:
        project_id = _release_project_id(state, blocker.source_id)
        if project_id is not None:
            project_ids.add(project_id)
    return project_ids


def _inbox_project_ids(state: TeamState, item: AgentInboxItem) -> set[str]:
    project_ids = {
        project_id
        for project_id in (
            _task_project_id(state, item.task_id),
            _task_project_id(state, item.payload.get("task_id")),
            str(item.payload.get("project_id") or "").strip() or None,
            _deliverable_project_id(state, item.payload.get("deliverable_id")),
            _release_project_id(state, item.payload.get("release_package_id")),
        )
        if project_id is not None
    }
    blocker = state.blockers.get(str(item.payload.get("blocker_id") or ""))
    if blocker is not None:
        project_ids.update(_blocker_project_ids(state, blocker))
    review = state.review_requests.get(
        str(item.payload.get("review_request_id") or "")
    )
    if review is not None:
        project_ids.update(_review_project_ids(state, review))
    return project_ids


__all__ = [
    "AgentDispatchScope",
    "actionable_dispatch_reviews",
    "actionable_dispatch_tasks",
    "build_agent_dispatch_scope",
]
