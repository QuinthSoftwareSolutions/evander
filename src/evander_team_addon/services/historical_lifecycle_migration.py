from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..models import (
    AgentTask,
    Deliverable,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
)
from .domain.blockers import append_blocker_event, resolve_blocker
from .domain.common import _set_task_state
from .domain.project_status import _recalculate_project_status
from .domain.task_assessment import assess_tasks


_REPAIRABLE_INVALID_REASON_CODES = frozenset(
    {
        "active_deliverable_without_acceptance_path",
        "support_task_accepted_instead_of_finished",
        "terminal_task_with_unresolved_descendants",
    }
)


class HistoricalLifecycleMigrationError(ValueError):
    """The requested migration is not safe for the observed lifecycle state."""


@dataclass(frozen=True)
class HistoricalLifecycleMigrationTask:
    task_id: str
    invalid_reason_codes: tuple[str, ...]
    action_kinds: tuple[str, ...]
    deliverable_ids: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalLifecycleMigrationPlan:
    project_id: str
    task_repairs: tuple[HistoricalLifecycleMigrationTask, ...]

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(repair.task_id for repair in self.task_repairs)

    def payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "task_count": len(self.task_repairs),
            "task_repairs": [
                {
                    "task_id": repair.task_id,
                    "invalid_reason_codes": list(repair.invalid_reason_codes),
                    "action_kinds": list(repair.action_kinds),
                    "deliverable_ids": list(repair.deliverable_ids),
                }
                for repair in self.task_repairs
            ],
        }


def plan_historical_lifecycle_migration(
    state: TeamState,
    *,
    project_id: str,
) -> HistoricalLifecycleMigrationPlan:
    """Plan only the narrow legacy states that have a lossless terminal repair."""

    project = state.projects.get(project_id)
    if project is None:
        raise HistoricalLifecycleMigrationError(f"project {project_id} does not exist")

    assessments = assess_tasks(state)
    repairs: list[HistoricalLifecycleMigrationTask] = []
    for task in sorted(
        (candidate for candidate in state.tasks.values() if candidate.project_id == project.id),
        key=lambda candidate: candidate.id,
    ):
        assessment = assessments[task.id]
        if assessment.resolution_status.value != "invalid":
            continue
        repairs.append(
            _plan_task_repair(
                state, task=task, invalid_reason_codes=assessment.invalid_reason_codes
            )
        )

    return HistoricalLifecycleMigrationPlan(
        project_id=project.id,
        task_repairs=tuple(repairs),
    )


def apply_historical_lifecycle_migration(
    state: TeamState,
    *,
    plan: HistoricalLifecycleMigrationPlan,
    actor: str,
    reason: str,
    reference_ids: Sequence[str],
    now: datetime,
) -> dict[str, Any]:
    """Apply a prevalidated migration and leave one resolved audit record per task."""

    current_plan = plan_historical_lifecycle_migration(state, project_id=plan.project_id)
    if current_plan != plan:
        raise HistoricalLifecycleMigrationError(
            "historical lifecycle state changed after planning; rerun the dry run before applying"
        )
    if not plan.task_repairs:
        return {
            "applied": False,
            "migration_id": None,
            "project_id": plan.project_id,
            "task_repairs": [],
            "audit_blocker_ids": [],
        }

    project = state.projects[plan.project_id]
    migration_id = f"historical-lifecycle-migration-{uuid4().hex}"
    audit_blocker_ids: list[str] = []
    task_results: list[dict[str, Any]] = []

    for repair in plan.task_repairs:
        task = state.tasks[repair.task_id]
        actions: list[dict[str, str]] = []
        if "finish_accepted_support_task" in repair.action_kinds:
            _set_task_state(task, TeamTaskState.FINISHED, now=now)
            task.updated_at = now
            actions.append({"kind": "finish_accepted_support_task", "task_id": task.id})
        for deliverable_id in repair.deliverable_ids:
            deliverable = state.deliverables[deliverable_id]
            deliverable.state = TeamRecordState.REJECTED
            actions.append(
                {
                    "kind": "retire_orphaned_pending_deliverable",
                    "deliverable_id": deliverable.id,
                }
            )
        if not actions:
            actions.append(
                {"kind": "reassess_terminal_parent_after_child_repair", "task_id": task.id}
            )
        task_results.append(
            {
                "task_id": task.id,
                "invalid_reason_codes": list(repair.invalid_reason_codes),
                "actions": actions,
            }
        )

    _recalculate_project_status(state, plan.project_id)
    post_assessments = assess_tasks(state)
    remaining_invalid_ids = [
        repair.task_id
        for repair in plan.task_repairs
        if post_assessments[repair.task_id].resolution_status.value == "invalid"
    ]
    if remaining_invalid_ids:
        raise HistoricalLifecycleMigrationError(
            "historical lifecycle migration did not resolve: " + ", ".join(remaining_invalid_ids)
        )

    for task_result in task_results:
        task = state.tasks[task_result["task_id"]]
        blocker = TeamBlocker(
            id=f"blocker-{uuid4().hex}",
            team_id=project.team_id,
            project_id=project.id,
            task_id=task.id,
            source_kind=TeamBlockerSourceKind.TASK,
            source_id=task.id,
            reason_code="historical_lifecycle_migration",
            reporter_actor=actor,
            owner_agent_id=task.owner_agent_id,
            affected_task_ids=[task.id],
            state=TeamBlockerState.OPEN,
            route=TeamBlockerRoute.OWNED,
            summary=f"Historical lifecycle migration: {task.title}",
            details=reason,
            priority=TeamInboxPriority.NORMAL,
            created_at=now,
            updated_at=now,
        )
        state.blockers[blocker.id] = blocker
        event_metadata: Mapping[str, Any] = {
            "operator_actor": actor,
            "migration_id": migration_id,
            "migration_kind": "historical_lifecycle",
            "invalid_reason_codes_before": task_result["invalid_reason_codes"],
            "actions": task_result["actions"],
        }
        audit_references = [migration_id, task.id, *reference_ids]
        append_blocker_event(
            state,
            blocker=blocker,
            event_type=TeamBlockerEventType.REPORTED,
            note=reason,
            reference_ids=audit_references,
            metadata=event_metadata,
            now=now,
        )
        resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=None,
            dispatch_id=None,
            note="Historical lifecycle migration verified the task as resolved.",
            reference_ids=audit_references,
            metadata={
                **event_metadata,
                "invalid_reason_codes_after": list(post_assessments[task.id].invalid_reason_codes),
            },
            now=now,
        )
        audit_blocker_ids.append(blocker.id)

    return {
        "applied": True,
        "migration_id": migration_id,
        "project_id": plan.project_id,
        "task_repairs": task_results,
        "audit_blocker_ids": audit_blocker_ids,
    }


def _plan_task_repair(
    state: TeamState,
    *,
    task: AgentTask,
    invalid_reason_codes: Sequence[str],
) -> HistoricalLifecycleMigrationTask:
    codes = tuple(sorted(set(invalid_reason_codes)))
    unsupported_codes = sorted(set(codes) - _REPAIRABLE_INVALID_REASON_CODES)
    if unsupported_codes:
        raise HistoricalLifecycleMigrationError(
            f"task {task.id} has unsupported invalid lifecycle reasons: "
            + ", ".join(unsupported_codes)
        )

    action_kinds: list[str] = []
    if "support_task_accepted_instead_of_finished" in codes:
        if task.task_kind != TeamTaskKind.SUPPORT or task.state != TeamTaskState.ACCEPTED:
            raise HistoricalLifecycleMigrationError(
                f"task {task.id} is not an accepted support task"
            )
        action_kinds.append("finish_accepted_support_task")

    pending_deliverable_ids = tuple(
        sorted(
            deliverable.id
            for deliverable in state.deliverables.values()
            if deliverable.task_id == task.id
            and deliverable.superseded_by_deliverable_id is None
            and deliverable.state == TeamRecordState.PENDING
        )
    )
    if "active_deliverable_without_acceptance_path" in codes:
        if task.state not in {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}:
            raise HistoricalLifecycleMigrationError(
                f"task {task.id} is not terminal while its deliverable lacks an acceptance path"
            )
        if not pending_deliverable_ids:
            raise HistoricalLifecycleMigrationError(
                f"task {task.id} has no pending deliverable to retire"
            )
        for deliverable_id in pending_deliverable_ids:
            _require_no_active_review(state, deliverable=state.deliverables[deliverable_id])
        action_kinds.append("retire_orphaned_pending_deliverable")
    elif pending_deliverable_ids:
        raise HistoricalLifecycleMigrationError(
            f"task {task.id} has an unexpected pending deliverable"
        )

    if "terminal_task_with_unresolved_descendants" in codes:
        if task.state not in {
            TeamTaskState.ACCEPTED,
            TeamTaskState.CANCELLED,
            TeamTaskState.FINISHED,
            TeamTaskState.REJECTED,
        }:
            raise HistoricalLifecycleMigrationError(
                f"task {task.id} is not terminal while it has unresolved descendants"
            )
        action_kinds.append("reassess_terminal_parent_after_child_repair")

    if not action_kinds:
        raise HistoricalLifecycleMigrationError(
            f"task {task.id} has no supported historical lifecycle repair"
        )
    return HistoricalLifecycleMigrationTask(
        task_id=task.id,
        invalid_reason_codes=codes,
        action_kinds=tuple(action_kinds),
        deliverable_ids=pending_deliverable_ids,
    )


def _require_no_active_review(state: TeamState, *, deliverable: Deliverable) -> None:
    active_reviews = [
        review.id
        for review in state.review_requests.values()
        if review.deliverable_id == deliverable.id
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
        and review.state
        in {
            TeamRecordState.PENDING,
            TeamRecordState.IN_PROGRESS,
            TeamRecordState.BLOCKED,
        }
    ]
    if active_reviews:
        raise HistoricalLifecycleMigrationError(
            f"deliverable {deliverable.id} has active reviews: " + ", ".join(sorted(active_reviews))
        )


__all__ = [
    "HistoricalLifecycleMigrationError",
    "HistoricalLifecycleMigrationPlan",
    "apply_historical_lifecycle_migration",
    "plan_historical_lifecycle_migration",
]
