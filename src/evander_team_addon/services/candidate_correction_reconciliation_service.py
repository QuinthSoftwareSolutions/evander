from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ..models import (
    AgentInboxItem,
    AgentStatus,
    AgentTask,
    GitPromotionRecord,
    ReleasePackage,
    ReleaseScopeValidation,
    TeamBlockerEventType,
    TeamBlockerResolutionScope,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    TaskCancellation,
    TaskDeliverableContract,
    project_lead_agent_id,
    utc_now,
)
from ..repository import TeamStateConflictError, TeamStateRepository
from .commands.outbox import _team_event_outbox_records
from .domain.blockers import (
    active_blockers_for_source,
    append_blocker_event,
    open_or_get_blocker,
    reconcile_task_blockers_after_cancellation,
    resolve_active_blockers_for_source,
)
from .domain.git_work_contexts import finalize_active_git_work_contexts_for_task
from .domain.project_status import _recalculate_project_status
from .domain.release_packages import (
    _build_release_package,
    _candidate_correction_downstream_recovery_base,
    _create_correction_aggregate_promotion,
    _reviewed_commit_for_release,
    _successor_release_request_error,
)
from .domain.task_assessment import TaskAssessmentBoundaryError, require_task_resolved
from .domain.work_due import _mark_agent_work_due


_MAX_RECONCILIATION_ATTEMPTS = 3
_CORRECTION_TASK_ACTORS = {
    "system:release-review-correction",
    "system:release-verification-correction",
}
_BLOCKER_SOURCE_PREFIX = "candidate-correction-successor:"
_COMPLETED_RELEASE_REVIEW_STATES = {
    TeamRecordState.ACCEPTED,
    TeamRecordState.REJECTED,
}


def is_automatic_candidate_correction_task(task: AgentTask) -> bool:
    return task.created_by_actor in _CORRECTION_TASK_ACTORS


def _stable_token(*parts: str, length: int = 32) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:length]


def _is_candidate_correction_predecessor(release: ReleasePackage) -> bool:
    failure = release.gate_failure
    return (
        release.state == TeamRecordState.REJECTED
        and failure is not None
        and (
            (failure.stage == "verification" and failure.kind == "candidate")
            or (failure.stage == "review" and failure.kind == "rejected")
        )
    )


def _correction_task(
    state: TeamState,
    predecessor: ReleasePackage,
) -> AgentTask | None:
    failure = predecessor.gate_failure
    if failure is None:
        return None
    task_id: str | None = None
    if failure.stage == "verification":
        run = state.release_verification_runs.get(failure.source_id or "")
        if run is not None and run.release_package_id == predecessor.id:
            task_id = run.correction_task_id
    elif failure.stage == "review":
        task_id = predecessor.review_correction_task_id
    task = state.tasks.get(task_id or "")
    if task is None or not is_automatic_candidate_correction_task(task):
        return None
    return task


def _direct_successors(
    state: TeamState,
    predecessor_id: str,
) -> list[ReleasePackage]:
    return sorted(
        (
            release
            for release in state.release_packages.values()
            if release.supersedes_release_package_id == predecessor_id
        ),
        key=lambda release: (release.created_at, release.id),
    )


def _failed_correction_aggregate(
    state: TeamState,
    successor: ReleasePackage,
) -> GitPromotionRecord | None:
    candidates = [
        promotion
        for promotion_id in successor.git_promotion_record_ids
        if (promotion := state.git_promotions.get(promotion_id)) is not None
        and promotion.promotion_kind == "correction_aggregate"
        and promotion.state == "failed"
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda promotion: (promotion.updated_at, promotion.created_at, promotion.id)
    )
    return candidates[-1]


def _cancel_obsolete_merge_recovery_tasks(
    state: TeamState,
    *,
    successor: ReleasePackage,
    failed_aggregate: GitPromotionRecord,
    replacement_aggregate: GitPromotionRecord,
    result: dict[str, Any],
    now: datetime,
) -> None:
    linked_task_ids = {
        task_id
        for blocker in state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE
        and blocker.source_id == successor.id
        for task_id in blocker.affected_task_ids
    }
    terminal_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    for task_id in sorted(linked_task_ids):
        task = state.tasks.get(task_id)
        if (
            task is None
            or task.created_by_actor != "system:git-merge-recovery"
            or task.state in terminal_states
        ):
            continue
        finalize_active_git_work_contexts_for_task(
            state,
            task_id=task.id,
            now=now,
        )
        task.cancellation = TaskCancellation(
            reason_code="promotion_boundary_superseded",
            reason=(
                f"Failed correction aggregate {failed_aggregate.id} was retired and "
                f"replaced by aggregate {replacement_aggregate.id}."
            ),
            actor_kind="system",
            actor_id="system:candidate-correction-reconciliation",
            cancelled_at=now,
        )
        task.state = TeamTaskState.CANCELLED
        task.state_entered_at = now
        task.updated_at = now
        result.setdefault("cancelled_merge_recovery_task_ids", []).append(task.id)
        reconcile_task_blockers_after_cancellation(
            state,
            task_id=task.id,
            actor_agent_id=None,
            dispatch_id=None,
            result=result,
            now=now,
        )


def _deliverable_supersession_reaches(
    state: TeamState,
    *,
    deliverable_id: str,
    current_ids: set[str],
) -> bool:
    seen: set[str] = set()
    cursor_id = deliverable_id
    while cursor_id not in seen:
        seen.add(cursor_id)
        deliverable = state.deliverables.get(cursor_id)
        if deliverable is None or deliverable.superseded_by_deliverable_id is None:
            return False
        cursor_id = deliverable.superseded_by_deliverable_id
        if cursor_id in current_ids:
            return True
    return False


def _blocked_successor_has_reviewed_rework(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_task: AgentTask | None,
    successor: ReleasePackage,
) -> bool:
    if (
        correction_task is None
        or successor.state != TeamRecordState.BLOCKED
        or successor.candidate_commit_sha is not None
        or successor.approved_commit_sha is not None
        or successor.verification_run_ids
        or successor.review_request_ids
        or _failed_correction_aggregate(state, successor) is None
        or not _task_is_resolved(state, correction_task)
        or not _release_review_correction_is_ready(state, predecessor, correction_task)
    ):
        return False
    current_ids = set(
        _current_successor_deliverable_ids(
            state,
            predecessor=predecessor,
            correction_task=correction_task,
        )
    )
    previous_ids = set(successor.deliverable_ids) - set(predecessor.deliverable_ids)
    return bool(current_ids) and current_ids != previous_ids and all(
        deliverable_id in current_ids
        or _deliverable_supersession_reaches(
            state,
            deliverable_id=deliverable_id,
            current_ids=current_ids,
        )
        for deliverable_id in previous_ids
    )


def _refresh_blocked_successor_from_reviewed_rework(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_task: AgentTask,
    successor: ReleasePackage,
    result: dict[str, Any],
    now: datetime,
) -> tuple[bool, bool]:
    if not _blocked_successor_has_reviewed_rework(
        state,
        predecessor=predecessor,
        correction_task=correction_task,
        successor=successor,
    ):
        return False, False
    correction_deliverable_ids = _current_successor_deliverable_ids(
        state,
        predecessor=predecessor,
        correction_task=correction_task,
    )
    reviewed_rows = [
        _reviewed_commit_for_release(state, state.deliverables[deliverable_id])
        for deliverable_id in correction_deliverable_ids
    ]
    reviewed_errors = [error for _commit, error in reviewed_rows if error is not None]
    validation_error = (
        "; ".join(reviewed_errors)
        if reviewed_errors
        else _successor_release_request_error(
            state,
            project_id=predecessor.project_id,
            predecessor=predecessor,
            correction_deliverable_ids=correction_deliverable_ids,
            ignore_successor_release_package_id=successor.id,
        )
    )
    if validation_error is not None:
        result.setdefault("candidate_correction_errors", {})[predecessor.id] = validation_error
        changed = _open_reconciliation_blocker(
            state,
            predecessor=predecessor,
            correction_task=correction_task,
            details=validation_error,
            result=result,
            now=now,
        )
        return True, changed

    failed_aggregate = _failed_correction_aggregate(state, successor)
    assert failed_aggregate is not None
    package_result = _package_result()
    aggregate = _create_correction_aggregate_promotion(
        state,
        release=successor,
        predecessor=predecessor,
        correction_deliverable_ids=correction_deliverable_ids,
        actor_agent_id=(predecessor.owner_agent_id or correction_task.owner_agent_id),
        result=package_result,
    )
    if aggregate is None:
        details = (
            "; ".join(str(error) for error in package_result["errors"])
            or f"failed to rebuild correction aggregate for successor {successor.id}"
        )
        changed = _open_reconciliation_blocker(
            state,
            predecessor=predecessor,
            correction_task=correction_task,
            details=details,
            result=result,
            now=now,
        )
        result.setdefault("candidate_correction_errors", {})[predecessor.id] = details
        return True, changed

    failed_aggregate.state = "retired"
    failed_aggregate.updated_at = now
    _cancel_obsolete_merge_recovery_tasks(
        state,
        successor=successor,
        failed_aggregate=failed_aggregate,
        replacement_aggregate=aggregate,
        result=result,
        now=now,
    )
    successor.deliverable_ids = list(
        dict.fromkeys([*predecessor.deliverable_ids, *correction_deliverable_ids])
    )
    successor.state = TeamRecordState.PENDING
    successor.error = None
    successor.gate_failure = None
    successor.feature_branches = []
    successor.scope_validation = ReleaseScopeValidation(
        state="pending",
        reviewed_commit_shas=sorted(
            commit for commit, error in reviewed_rows if error is None and commit is not None
        ),
    )
    successor.updated_at = now
    result.setdefault("events", []).extend(package_result["events"])
    result.setdefault("recovered_successor_release_package_ids", []).append(successor.id)
    result.setdefault("git_promotion_event_ids", []).append(aggregate.id)
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=successor.id,
        actor_agent_id=None,
        dispatch_id=None,
        note=f"Reviewed replacement correction rebuilt failed aggregate {aggregate.id}.",
        reference_ids=[aggregate.id, *correction_deliverable_ids],
        result=result,
        now=now,
    )
    _recalculate_project_status(state, predecessor.project_id)
    return True, True


def _current_accepted_deliverable_ids(
    state: TeamState,
    task: AgentTask,
) -> list[str]:
    return sorted(
        deliverable.id
        for deliverable in state.deliverables.values()
        if deliverable.task_id == task.id
        and deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.superseded_by_deliverable_id is None
    )


def _current_successor_deliverable_ids(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_task: AgentTask,
) -> list[str]:
    recovery_ids = sorted(
        deliverable.id
        for deliverable in state.deliverables.values()
        if deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.superseded_by_deliverable_id is None
        and deliverable.verification is not None
        and deliverable.verification.status == "passed"
        and _candidate_correction_downstream_recovery_base(
            state,
            predecessor=predecessor,
            deliverable=deliverable,
        )
        is not None
    )
    if recovery_ids:
        return recovery_ids
    return _current_accepted_deliverable_ids(state, correction_task)


def _blocker_source_id(predecessor_id: str) -> str:
    return _BLOCKER_SOURCE_PREFIX + predecessor_id


def _has_reconciliation_blocker(
    state: TeamState,
    predecessor_id: str,
) -> bool:
    return bool(
        active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR,
            source_id=_blocker_source_id(predecessor_id),
        )
    )


def _task_is_resolved(state: TeamState, task: AgentTask) -> bool:
    if task.state == TeamTaskState.CANCELLED:
        return False
    try:
        require_task_resolved(
            state,
            task.id,
            boundary=f"automatic successor release for correction task {task.id}",
        )
    except TaskAssessmentBoundaryError:
        return False
    return True


def _release_review_correction_is_ready(
    state: TeamState,
    predecessor: ReleasePackage,
    correction_task: AgentTask,
) -> bool:
    failure = predecessor.gate_failure
    if failure is None or failure.stage != "review":
        return True
    current_reviews = [
        state.review_requests[review_id]
        for review_id in predecessor.review_request_ids
        if review_id in state.review_requests
        and state.review_requests[review_id].superseded_by_review_request_id is None
        and state.review_requests[review_id].retired_at is None
        and (
            predecessor.review_cohort_id is None
            or state.review_requests[review_id].review_cohort_id == predecessor.review_cohort_id
        )
    ]
    if predecessor.review_cohort_id is not None and (
        predecessor.review_cohort_sealed_at is None
        or any(
            review.review_cohort_id != predecessor.review_cohort_id for review in current_reviews
        )
    ):
        return False
    if not current_reviews or any(
        review.state not in _COMPLETED_RELEASE_REVIEW_STATES for review in current_reviews
    ):
        return False

    rejected_review_ids = {
        review.id for review in current_reviews if review.state == TeamRecordState.REJECTED
    }
    latest_rejection_at = max(
        (
            review.decision_at
            for review in current_reviews
            if review.state == TeamRecordState.REJECTED and review.decision_at is not None
        ),
        default=None,
    )
    if (
        predecessor.review_cohort_id is None
        and latest_rejection_at is not None
        and any(
            deliverable.created_at < latest_rejection_at
            for deliverable in state.deliverables.values()
            if deliverable.task_id == correction_task.id
            and deliverable.state == TeamRecordState.ACCEPTED
            and deliverable.superseded_by_deliverable_id is None
        )
    ):
        return False
    incorporated_review_ids = {
        review_id for review_id in rejected_review_ids if review_id in correction_task.description
    }
    for item in state.inbox.values():
        if (
            item.task_id != correction_task.id
            or item.payload.get("kind") != "release_review_rejection_correction_work"
        ):
            continue
        review_id = item.payload.get("review_request_id")
        if isinstance(review_id, str):
            incorporated_review_ids.add(review_id)
        review_ids = item.payload.get("review_request_ids")
        if isinstance(review_ids, list):
            incorporated_review_ids.update(
                review_id for review_id in review_ids if isinstance(review_id, str)
            )
    return bool(rejected_review_ids) and rejected_review_ids.issubset(incorporated_review_ids)


def _candidate_release_ids_for_reconciliation(state: TeamState) -> list[str]:
    release_ids: list[str] = []
    for predecessor in sorted(
        state.release_packages.values(),
        key=lambda release: (release.created_at, release.id),
    ):
        if not _is_candidate_correction_predecessor(predecessor):
            continue
        successors = _direct_successors(state, predecessor.id)
        has_blocker = _has_reconciliation_blocker(state, predecessor.id)
        if successors:
            correction_task = _correction_task(state, predecessor)
            if has_blocker or _blocked_successor_has_reviewed_rework(
                state,
                predecessor=predecessor,
                correction_task=correction_task,
                successor=successors[0],
            ):
                release_ids.append(predecessor.id)
            continue
        task = _correction_task(state, predecessor)
        if (
            task is None
            or not _task_is_resolved(state, task)
            or not _release_review_correction_is_ready(state, predecessor, task)
        ):
            continue
        release_ids.append(predecessor.id)
    return release_ids


def _package_result() -> dict[str, Any]:
    return {
        "success": False,
        "created": False,
        "duplicate": False,
        "existing_release_package_id": None,
        "release_package_ids": [],
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
        "created_deliverable_ids": [],
        "next_action_hints": [],
    }


def _record_successor_notice(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_task: AgentTask,
    successor: ReleasePackage,
    result: dict[str, Any],
    now: datetime,
) -> bool:
    project = state.projects[predecessor.project_id]
    recipient_agent_id = (
        project_lead_agent_id(state, project.id)
        or project.lead_agent_id
        or correction_task.owner_agent_id
    )
    notices = [
        item
        for item in state.inbox.values()
        if item.task_id == correction_task.id
        and item.recipient_agent_id == recipient_agent_id
        and item.payload.get("kind") in {"candidate_correction_resolution", "root_task_resolution"}
    ]
    created = False
    if not notices:
        notice_id = "inbox-candidate-successor-" + _stable_token(
            predecessor.id, correction_task.id, length=24
        )
        notice = AgentInboxItem(
            id=notice_id,
            recipient_agent_id=recipient_agent_id,
            sender_agent_id=correction_task.owner_agent_id,
            task_id=correction_task.id,
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=False,
            priority=TeamInboxPriority.LOW,
            payload={},
            state=TeamInboxItemState.HANDLED,
            created_at=now,
            updated_at=now,
        )
        state.inbox[notice.id] = notice
        notices = [notice]
        result.setdefault("created_inbox_item_ids", []).append(notice.id)
        created = True
    changed = created
    for notice in notices:
        payload = {
            **notice.payload,
            "kind": "candidate_correction_resolution",
            "project_id": predecessor.project_id,
            "task_id": correction_task.id,
            "deliverable_ids": _current_accepted_deliverable_ids(
                state,
                correction_task,
            ),
            "predecessor_release_package_id": predecessor.id,
            "successor_release_package_id": successor.id,
            "summary": (
                "The reviewed rejected-gate correction automatically created its successor release."
            ),
            "next_action": (
                "Observe the successor's normal promotion and release gates; "
                "no release request is required."
            ),
            "release_ready": True,
            "response_required": False,
        }
        if (
            notice.payload != payload
            or notice.response_requested
            or notice.priority != TeamInboxPriority.LOW
            or notice.state != TeamInboxItemState.HANDLED
        ):
            notice.payload = payload
            notice.response_requested = False
            notice.priority = TeamInboxPriority.LOW
            notice.state = TeamInboxItemState.HANDLED
            notice.updated_at = now
            changed = True
    return changed


def _open_reconciliation_blocker(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_task: AgentTask,
    details: str,
    result: dict[str, Any],
    now: datetime,
) -> bool:
    project = state.projects[predecessor.project_id]
    blocker, created = open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR,
        source_id=_blocker_source_id(predecessor.id),
        summary=f"Automatic successor creation failed for {predecessor.id}",
        details=details,
        reason_code="candidate_correction_successor_failed",
        priority=TeamInboxPriority.HIGH,
        reporter_actor="system:release-correction",
        initial_owner_agent_id=correction_task.owner_agent_id,
        team_id=project.team_id,
        project_id=project.id,
        task_id=correction_task.id,
        affected_task_ids=[correction_task.id],
        resolution_scope=TeamBlockerResolutionScope.DOWNSTREAM,
        result=result,
        now=now,
    )
    result.setdefault("candidate_correction_blocker_ids", []).append(blocker.id)
    changed = created
    if (
        blocker.details != details
        or blocker.resolution_scope != TeamBlockerResolutionScope.DOWNSTREAM
    ):
        blocker.details = details
        blocker.resolution_scope = TeamBlockerResolutionScope.DOWNSTREAM
        blocker.updated_at = now
        changed = True
    if created:
        result.setdefault("created_candidate_correction_blocker_ids", []).append(blocker.id)
    return changed


def reconcile_candidate_correction_successors(
    state: TeamState,
    *,
    result: dict[str, Any],
    now: datetime | None = None,
    release_package_ids: Iterable[str] | None = None,
) -> bool:
    """Create missing successors for resolved rejected-gate corrections."""

    effective_now = now or utc_now()
    selected_ids = set(release_package_ids or ())
    predecessors = sorted(
        (
            release
            for release in state.release_packages.values()
            if _is_candidate_correction_predecessor(release)
            and (not selected_ids or release.id in selected_ids)
        ),
        key=lambda release: (release.created_at, release.id),
    )
    changed = False
    for predecessor in predecessors:
        correction_task = _correction_task(state, predecessor)
        existing_successors = _direct_successors(state, predecessor.id)
        if existing_successors:
            successor = existing_successors[0]
            refresh_attempted = False
            refresh_changed = False
            if correction_task is not None:
                refresh_attempted, refresh_changed = (
                    _refresh_blocked_successor_from_reviewed_rework(
                        state,
                        predecessor=predecessor,
                        correction_task=correction_task,
                        successor=successor,
                        result=result,
                        now=effective_now,
                    )
                )
            if refresh_attempted:
                result.setdefault("existing_successor_release_package_ids", []).append(successor.id)
                changed = refresh_changed or changed
                continue
            resolved_ids = resolve_active_blockers_for_source(
                state,
                source_kind=TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR,
                source_id=_blocker_source_id(predecessor.id),
                actor_agent_id=None,
                dispatch_id=None,
                note=f"Successor release {successor.id} exists.",
                reference_ids=[successor.id],
                result=result,
                now=effective_now,
            )
            changed = bool(resolved_ids) or changed
            if correction_task is not None:
                changed = (
                    _record_successor_notice(
                        state,
                        predecessor=predecessor,
                        correction_task=correction_task,
                        successor=successor,
                        result=result,
                        now=effective_now,
                    )
                    or changed
                )
            result.setdefault("existing_successor_release_package_ids", []).append(successor.id)
            continue
        if (
            correction_task is None
            or not _task_is_resolved(state, correction_task)
            or not _release_review_correction_is_ready(
                state,
                predecessor,
                correction_task,
            )
        ):
            continue

        correction_deliverable_ids = _current_successor_deliverable_ids(
            state,
            predecessor=predecessor,
            correction_task=correction_task,
        )
        reviewed_errors = [
            error
            for deliverable_id in correction_deliverable_ids
            if (
                error := _reviewed_commit_for_release(
                    state,
                    state.deliverables[deliverable_id],
                )[1]
            )
            is not None
        ]
        successor_error = (
            "; ".join(reviewed_errors)
            if reviewed_errors
            else _successor_release_request_error(
                state,
                project_id=predecessor.project_id,
                predecessor=predecessor,
                correction_deliverable_ids=correction_deliverable_ids,
            )
        )
        if successor_error is not None:
            changed = (
                _open_reconciliation_blocker(
                    state,
                    predecessor=predecessor,
                    correction_task=correction_task,
                    details=successor_error,
                    result=result,
                    now=effective_now,
                )
                or changed
            )
            result.setdefault("candidate_correction_errors", {})[predecessor.id] = successor_error
            continue

        package_result = _package_result()
        successor_id = "release-" + _stable_token(
            "candidate-correction-successor",
            predecessor.id,
            correction_task.id,
            *correction_deliverable_ids,
        )
        try:
            created_id = _build_release_package(
                state,
                project=state.projects[predecessor.project_id],
                request={
                    "id": successor_id,
                    "project_id": predecessor.project_id,
                    "deliverable_ids": correction_deliverable_ids,
                    "supersedes_release_package_id": predecessor.id,
                    "summary": (
                        f"Automatic successor for {predecessor.id} after accepted "
                        "rejected-gate correction."
                    ),
                    "verification_waivers": [],
                },
                result=package_result,
                projects_to_recalc=set(),
                actor_agent_id=(predecessor.owner_agent_id or correction_task.owner_agent_id),
                team_id=state.projects[predecessor.project_id].team_id,
            )
        except Exception as exc:  # pragma: no cover - defensive durable recovery
            state.release_packages.pop(successor_id, None)
            for promotion_id in [
                promotion.id
                for promotion in state.git_promotions.values()
                if promotion.release_package_id == successor_id
            ]:
                state.git_promotions.pop(promotion_id, None)
            created_id = None
            package_result["errors"].append(
                f"{exc.__class__.__name__}: {str(exc) or 'unexpected packaging failure'}"
            )
        if created_id is None or package_result["errors"]:
            details = (
                "; ".join(str(error) for error in package_result["errors"])
                or "The deterministic successor release package could not be created."
            )
            changed = (
                _open_reconciliation_blocker(
                    state,
                    predecessor=predecessor,
                    correction_task=correction_task,
                    details=details,
                    result=result,
                    now=effective_now,
                )
                or changed
            )
            result.setdefault("candidate_correction_errors", {})[predecessor.id] = details
            continue

        successor = state.release_packages[created_id]
        if successor.supersedes_release_package_id != predecessor.id:
            details = (
                f"release package {successor.id} has the same deliverables but does "
                f"not directly supersede rejected predecessor {predecessor.id}"
            )
            changed = (
                _open_reconciliation_blocker(
                    state,
                    predecessor=predecessor,
                    correction_task=correction_task,
                    details=details,
                    result=result,
                    now=effective_now,
                )
                or changed
            )
            result.setdefault("candidate_correction_errors", {})[predecessor.id] = details
            continue

        if package_result.get("created"):
            result.setdefault("created_successor_release_package_ids", []).append(created_id)
            result.setdefault("release_package_ids", []).append(created_id)
            result.setdefault("events", []).extend(package_result.get("events") or [])
            result.setdefault("git_promotion_event_ids", []).extend(
                package_result.get("git_promotion_event_ids") or []
            )
        else:
            result.setdefault("existing_successor_release_package_ids", []).append(created_id)
        resolve_active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR,
            source_id=_blocker_source_id(predecessor.id),
            actor_agent_id=None,
            dispatch_id=None,
            note=f"Automatic successor release {successor.id} was created.",
            reference_ids=[successor.id],
            result=result,
            now=effective_now,
        )
        _recalculate_project_status(state, predecessor.project_id)
        _record_successor_notice(
            state,
            predecessor=predecessor,
            correction_task=correction_task,
            successor=successor,
            result=result,
            now=effective_now,
        )
        changed = True
    return changed


def recover_candidate_correction_successor(
    state: TeamState,
    *,
    blocker_id: str,
    action: str,
    operator_actor_id: str,
    reason: str | None,
    instruction: str | None,
    owner_agent_id: str | None,
    result: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Apply one typed operator recovery without bypassing release validation."""

    blocker = state.blockers.get(blocker_id)
    if blocker is None:
        raise ValueError(f"blocker {blocker_id} does not exist")
    if blocker.state.value != "open":
        raise ValueError(f"blocker {blocker.id} is {blocker.state.value}")
    if (
        blocker.source_kind != TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
        or blocker.resolution_scope != TeamBlockerResolutionScope.DOWNSTREAM
    ):
        raise ValueError(
            f"blocker {blocker.id} is not a downstream candidate-correction successor blocker"
        )
    prefix = _BLOCKER_SOURCE_PREFIX
    if not blocker.source_id.startswith(prefix):
        raise ValueError(f"blocker {blocker.id} has invalid recovery source")
    predecessor_id = blocker.source_id[len(prefix) :]
    predecessor = state.release_packages.get(predecessor_id)
    if predecessor is None:
        raise ValueError(f"predecessor release package {predecessor_id} does not exist")
    if blocker.source_id != _blocker_source_id(predecessor.id):
        raise ValueError(f"blocker {blocker.id} recovery source is inconsistent")
    correction_task = _correction_task(state, predecessor)
    if correction_task is None:
        raise ValueError(f"release {predecessor.id} has no automatic correction task")
    if action == "create_verified_replacement":
        sources = sorted(
            (
                deliverable
                for deliverable in state.deliverables.values()
                if deliverable.task_id == correction_task.id
                and deliverable.state == TeamRecordState.ACCEPTED
                and deliverable.superseded_by_deliverable_id is None
                and deliverable.git_refs.agent_branch is not None
                and (deliverable.git_refs.submitted_commit_sha or deliverable.git_refs.commit_sha)
                is not None
            ),
            key=lambda deliverable: (deliverable.created_at, deliverable.id),
            reverse=True,
        )
        if not sources:
            raise ValueError(
                f"correction task {correction_task.id} has no accepted Git "
                "deliverable to pin a replacement task"
            )
        source = sources[0]
        source_branch = source.git_refs.agent_branch
        source_commit = source.git_refs.submitted_commit_sha or source.git_refs.commit_sha
        assert source_branch is not None and source_commit is not None
        task_id = "task-candidate-recovery-" + _stable_token(
            predecessor.id,
            correction_task.id,
            source_commit,
            length=24,
        )
        recovery_task = state.tasks.get(task_id)
        if recovery_task is None:
            owner_id = owner_agent_id or blocker.owner_agent_id or correction_task.owner_agent_id
            owner = state.agents.get(owner_id)
            project = state.projects[predecessor.project_id]
            if owner is None:
                raise ValueError(f"recovery task owner {owner_id} does not exist")
            if owner.status != AgentStatus.ACTIVE or owner.team_id != project.team_id:
                raise ValueError(
                    f"recovery task owner {owner.id} must be active in team {project.team_id}"
                )
            effective_now = now or utc_now()
            operator_instruction = (
                ""
                if instruction is None
                else (f"\n\nOperator instruction from {operator_actor_id}:\n{instruction}")
            )
            recovery_task = AgentTask(
                id=task_id,
                owner_agent_id=owner.id,
                project_id=project.id,
                priority=correction_task.priority,
                state=TeamTaskState.PENDING,
                title=f"Verify downstream recovery for {predecessor.id}",
                description=(
                    f"Recover the failed successor packaging for {predecessor.id}. "
                    f"Start from accepted correction branch {source_branch} at exact "
                    f"commit {source_commit}. Make only the downstream correction, run "
                    "the relevant release verification plus focused checks, and "
                    "submit an accepted Git deliverable with verification passed. Do "
                    "not merge or rebase another base branch and do not request a release."
                    f"{operator_instruction}"
                ),
                created_by_actor="system:candidate-correction-recovery",
                originating_task_id=correction_task.id,
                git_base_branch=source_branch,
                git_base_commit_sha=source_commit,
                dependency_task_ids=[],
                deliverable_contract=TaskDeliverableContract(require_git_provenance=True),
                created_at=effective_now,
                updated_at=effective_now,
            )
            state.tasks[recovery_task.id] = recovery_task
            _mark_agent_work_due(state, agent_id=owner.id, now=effective_now)
            blocker.last_progress_at = effective_now
            blocker.updated_at = effective_now
            blocker_event = append_blocker_event(
                state,
                blocker=blocker,
                event_type=TeamBlockerEventType.PROGRESS,
                note=(
                    f"Operator {operator_actor_id} created pinned verification "
                    f"replacement task {recovery_task.id}."
                ),
                reference_ids=[recovery_task.id],
                metadata={
                    "kind": "candidate_correction_recovery_task_created",
                    "operator_actor_id": operator_actor_id,
                    "recovery_task_id": recovery_task.id,
                    "owner_agent_id": recovery_task.owner_agent_id,
                    "git_base_branch": source_branch,
                    "git_base_commit_sha": source_commit,
                },
                now=effective_now,
            )
            result["created_recovery_task_id"] = recovery_task.id
            result["created_blocker_event_id"] = blocker_event.id
        else:
            if (
                recovery_task.originating_task_id != correction_task.id
                or recovery_task.parent_task_id is not None
                or recovery_task.dependency_task_ids
                or recovery_task.git_base_branch != source_branch
                or recovery_task.git_base_commit_sha != source_commit.casefold()
            ):
                raise ValueError(
                    f"deterministic recovery task {recovery_task.id} has incompatible provenance"
                )
            result["existing_recovery_task_id"] = recovery_task.id
        result["recovery_task_id"] = recovery_task.id
        result["success"] = True
        return

    if action == "retry":
        reconcile_candidate_correction_successors(
            state,
            result=result,
            release_package_ids=[predecessor.id],
            now=now,
        )
        result["success"] = True
        return

    deliverable_ids = _current_accepted_deliverable_ids(state, correction_task)
    if not deliverable_ids:
        raise ValueError(f"correction task {correction_task.id} has no accepted deliverables")
    waivers: list[dict[str, Any]] = []
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables[deliverable_id]
        status = (
            deliverable.verification.status if deliverable.verification is not None else "unknown"
        )
        if status == "failed":
            raise ValueError(
                f"deliverable {deliverable.id} has failed verification and cannot be waived"
            )
        if status != "passed":
            waivers.append({"deliverable_id": deliverable.id, "reason": reason})
    successor_id = "release-" + _stable_token(
        "candidate-correction-waiver",
        predecessor.id,
        *deliverable_ids,
    )
    package_result = _package_result()
    project = state.projects[predecessor.project_id]
    created_id = _build_release_package(
        state,
        project=project,
        request={
            "id": successor_id,
            "project_id": project.id,
            "deliverable_ids": deliverable_ids,
            "supersedes_release_package_id": predecessor.id,
            "summary": f"Operator-authorized verification-waiver successor for {predecessor.id}.",
            "verification_waivers": waivers,
            "waiver_actor_kind": "operator",
            "waiver_actor_id": operator_actor_id,
            "release_requested_by_actor": f"operator:{operator_actor_id}",
        },
        result=package_result,
        projects_to_recalc=set(),
        actor_agent_id=project.lead_agent_id,
        team_id=project.team_id,
    )
    if created_id is None or package_result["errors"]:
        raise ValueError(
            "; ".join(package_result["errors"])
            or "verification-waiver successor could not be created"
        )
    result["events"].extend(package_result.get("events") or [])
    result["release_package_id"] = created_id
    result["verification_waiver_count"] = len(waivers)
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR,
        source_id=blocker.source_id,
        actor_agent_id=None,
        dispatch_id=None,
        note=(f"Operator {operator_actor_id} authorized verified successor {created_id}: {reason}"),
        reference_ids=[created_id],
        result=result,
        now=now,
    )
    _recalculate_project_status(state, project.id)
    result["success"] = True


class CandidateCorrectionReconciliationService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    def reconcile(self) -> dict[str, Any]:
        last_conflict: TeamStateConflictError | None = None
        for _attempt in range(_MAX_RECONCILIATION_ATTEMPTS):
            current = self._repository.read()
            release_ids = _candidate_release_ids_for_reconciliation(current)
            if not release_ids:
                return {
                    "changed": False,
                    "revision": current.revision,
                    "events": [],
                }
            result: dict[str, Any] = {
                "changed": False,
                "events": [],
                "reconciled_release_package_ids": [],
            }
            now = utc_now()

            def _mutate(state: TeamState) -> TeamState:
                result["changed"] = reconcile_candidate_correction_successors(
                    state,
                    result=result,
                    now=now,
                    release_package_ids=release_ids,
                )
                if result["changed"]:
                    result["reconciled_release_package_ids"] = list(release_ids)
                return state

            try:
                state = self._repository.update_with_outbox(
                    _mutate,
                    outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
                    expected_revision=current.revision,
                )
            except TeamStateConflictError as exc:
                last_conflict = exc
                continue
            result["revision"] = state.revision
            return result
        assert last_conflict is not None
        raise last_conflict


__all__ = [
    "CandidateCorrectionReconciliationService",
    "is_automatic_candidate_correction_task",
    "reconcile_candidate_correction_successors",
]
