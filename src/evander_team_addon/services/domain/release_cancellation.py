from __future__ import annotations

from typing import Any

from ...models import (
    ReleaseCancellation,
    ReleasePackage,
    TaskCancellation,
    TeamBlockerState,
    TeamCaseStatus,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)
from .blockers import resolve_blocker
from .common import utc_now
from .git_work_contexts import finalize_active_git_work_contexts_for_task
from .project_status import _recalculate_project_status


_CORRECTION_ACTORS = {
    "system:human-review",
    "system:release-review-correction",
    "system:release-verification-correction",
    "system:candidate-correction-recovery",
    "system:git-merge-recovery",
}


def release_cancellation_error(state: TeamState, release: ReleasePackage) -> str | None:
    if release.state == TeamRecordState.CANCELLED:
        return None
    if (
        release.published_at
        or release.published_commit_sha
        or release.state == TeamRecordState.ACCEPTED
    ):
        return "Published releases cannot be cancelled."
    # Queueing the canonical effect is the publication boundary. It commits under
    # the same state lock as cancellation; external Git effects cannot be recalled.
    if any(
        promotion.release_package_id == release.id and promotion.promotion_kind == "canonical_push"
        for promotion in state.git_promotions.values()
    ):
        return "Publication has already been queued or attempted; this release cannot be cancelled."
    if any(
        candidate.supersedes_release_package_id == release.id
        and candidate.state != TeamRecordState.CANCELLED
        for candidate in state.release_packages.values()
    ):
        return "This release has a successor. Cancel the current release attempt instead."
    return None


def _exclusive_correction_task_ids(state: TeamState, release: ReleasePackage) -> set[str]:
    ids = {
        task.id
        for task in state.tasks.values()
        if task.release_base_package_id == release.id
        and task.created_by_actor in _CORRECTION_ACTORS
    }
    ids.update(
        task_id
        for blocker in state.blockers.values()
        if blocker.source_id == release.id
        for task_id in blocker.affected_task_ids
        if (task := state.tasks.get(task_id)) is not None
        and task.created_by_actor in _CORRECTION_ACTORS
    )
    if release.review_correction_task_id:
        ids.add(release.review_correction_task_id)
    for run in state.release_verification_runs.values():
        if run.release_package_id == release.id and run.correction_task_id:
            ids.add(run.correction_task_id)
    requests = {
        request.id
        for request in state.human_review_requests.values()
        if request.release_package_id == release.id
    }
    ids.update(
        task_id
        for case in state.cases.values()
        if case.source == "human_release_review" and case.source_ref in requests
        for task_id in case.task_ids
        if (task := state.tasks.get(task_id)) is not None
        and task.created_by_actor == "system:human-review"
    )
    ids.update(
        finding.correction_task_id
        for finding in state.human_review_findings.values()
        if finding.source_human_review_request_id in requests
    )
    while True:
        expanded = ids | {
            task.id
            for task in state.tasks.values()
            if task.parent_task_id in ids
            or (task.originating_task_id in ids and task.created_by_actor in _CORRECTION_ACTORS)
        }
        if expanded == ids:
            break
        ids = expanded
    # Keep shared work and its dependencies. Cancelling one attempt must not
    # invalidate another release or an independently requested task.
    shared = {
        deliverable.task_id
        for other in state.release_packages.values()
        if other.id != release.id and other.state != TeamRecordState.CANCELLED
        for deliverable_id in other.deliverable_ids
        if (deliverable := state.deliverables.get(deliverable_id)) is not None
    }
    ids -= shared
    while True:
        required = {
            dependency_id
            for task in state.tasks.values()
            if task.id not in ids
            for dependency_id in [task.parent_task_id, *task.dependency_task_ids]
            if dependency_id in ids
        }
        if not required:
            return ids
        ids -= required


def cancel_release(
    state: TeamState,
    *,
    release: ReleasePackage,
    actor_id: str,
    reason: str,
) -> dict[str, Any]:
    if release.state == TeamRecordState.CANCELLED:
        return {"already_cancelled": True, "cancelled_task_ids": [], "resolved_blocker_ids": []}
    error = release_cancellation_error(state, release)
    if error:
        raise ValueError(error)
    now = utc_now()
    note = f"Release {release.id} cancelled by {actor_id}." + (f" {reason}" if reason else "")
    release.state = TeamRecordState.CANCELLED
    release.cancellation = ReleaseCancellation(actor_id=actor_id, reason=reason, cancelled_at=now)
    release.updated_at = now
    release.error = None
    # Keep the gate failure and decisions as evidence; cancellation is not a
    # synthetic review decision and does not claim that findings were fixed.
    task_ids = _exclusive_correction_task_ids(state, release)
    cancelled_task_ids = set()
    for task_id in task_ids:
        task = state.tasks.get(task_id)
        if task is None or task.state in {
            TeamTaskState.FINISHED,
            TeamTaskState.ACCEPTED,
            TeamTaskState.CANCELLED,
        }:
            continue
        task.cancellation = TaskCancellation(
            reason_code="release_cancelled",
            reason=note,
            actor_kind="operator",
            actor_id=actor_id,
            cancelled_at=now,
        )
        task.state = TeamTaskState.CANCELLED
        task.state_entered_at = now
        task.updated_at = now
        finalize_active_git_work_contexts_for_task(state, task_id=task.id, now=now)
        cancelled_task_ids.add(task.id)
    deliverable_ids = {d.id for d in state.deliverables.values() if d.task_id in cancelled_task_ids}
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables[deliverable_id]
        if deliverable.state in {
            TeamRecordState.PENDING,
            TeamRecordState.IN_PROGRESS,
            TeamRecordState.BLOCKED,
        }:
            deliverable.state = TeamRecordState.CANCELLED
    review_ids = set()
    for review in state.review_requests.values():
        if review.release_package_id != release.id and review.deliverable_id not in deliverable_ids:
            continue
        review_ids.add(review.id)
        if review.retired_at is None:
            review.retired_at = now
            review.retired_by = actor_id
            review.retirement_reason = note
        if review.state in {
            TeamRecordState.PENDING,
            TeamRecordState.IN_PROGRESS,
            TeamRecordState.BLOCKED,
        }:
            review.state = TeamRecordState.CANCELLED
            review.block_kind = None
    human_ids = set()
    for request in state.human_review_requests.values():
        if request.release_package_id != release.id:
            continue
        human_ids.add(request.id)
        if request.state in {"pending", "preparing", "blocked"}:
            request.state = "cancelled"
            request.completed_at = now
            request.revision += 1
    for run in state.release_verification_runs.values():
        if run.release_package_id == release.id and run.state in {"pending", "running"}:
            run.state = "cancelled"
            run.finished_at = now
            run.updated_at = now
    for case in state.cases.values():
        if (
            case.source == "human_release_review"
            and case.source_ref in human_ids
            and set(case.task_ids).issubset(task_ids)
            and case.status not in {TeamCaseStatus.CLOSED, TeamCaseStatus.RESOLVED}
        ):
            case.status = TeamCaseStatus.CLOSED
            case.closed_at = now
            case.updated_at = now
            case.metadata["release_cancellation"] = release.cancellation.model_dump(mode="json")
    promotion_ids = set()
    for promotion in state.git_promotions.values():
        if promotion.release_package_id == release.id or promotion.task_id in cancelled_task_ids:
            promotion_ids.add(promotion.id)
            if promotion.state in {"pending", "failed"}:
                promotion.state = "retired"
                promotion.updated_at = now
    source_ids = {
        release.id,
        f"candidate-correction-successor:{release.id}",
        *review_ids,
        *human_ids,
        *task_ids,
        *deliverable_ids,
        *promotion_ids,
        *(f"human-correction-aggregation:{request_id}" for request_id in human_ids),
    }
    resolved_ids = []
    for blocker in state.blockers.values():
        if blocker.state == TeamBlockerState.OPEN and blocker.source_id in source_ids:
            resolve_blocker(
                state,
                blocker=blocker,
                actor_agent_id=None,
                dispatch_id=None,
                note=note,
                reference_ids=[release.id],
                metadata={"operator_actor_id": actor_id, "release_cancelled": True},
                now=now,
            )
            resolved_ids.append(blocker.id)
    inbox_refs = source_ids | set(resolved_ids)
    for item in state.inbox.values():
        if item.state == TeamInboxItemState.PENDING and any(
            item.payload.get(key) in inbox_refs
            for key in (
                "release_package_id",
                "predecessor_release_package_id",
                "task_id",
                "review_request_id",
                "human_review_request_id",
                "blocker_id",
                "deliverable_id",
            )
            if isinstance(item.payload.get(key), str)
        ):
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = now
    _recalculate_project_status(state, release.project_id)
    return {
        "already_cancelled": False,
        "cancelled_task_ids": sorted(cancelled_task_ids),
        "resolved_blocker_ids": sorted(resolved_ids),
    }
