from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ..models import (
    AgentInboxItem,
    AgentTask,
    ReleasePackage,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    project_lead_agent_id,
    utc_now,
)
from ..repository import TeamStateConflictError, TeamStateRepository
from .commands.outbox import _team_event_outbox_records
from .domain.blockers import (
    active_blockers_for_source,
    open_or_get_blocker,
    resolve_active_blockers_for_source,
)
from .domain.project_status import _recalculate_project_status
from .domain.release_packages import (
    _build_release_package,
    _reviewed_commit_for_release,
    _successor_release_request_error,
)
from .domain.task_assessment import TaskAssessmentBoundaryError, require_task_resolved


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


def _blocker_source_id(predecessor_id: str) -> str:
    return _BLOCKER_SOURCE_PREFIX + predecessor_id


def _has_reconciliation_blocker(
    state: TeamState,
    predecessor_id: str,
) -> bool:
    return bool(
        active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.EXPLICIT,
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
    ]
    if not current_reviews or any(
        review.state not in _COMPLETED_RELEASE_REVIEW_STATES
        for review in current_reviews
    ):
        return False

    rejected_review_ids = {
        review.id
        for review in current_reviews
        if review.state == TeamRecordState.REJECTED
    }
    latest_rejection_at = max(
        (
            review.decision_at
            for review in current_reviews
            if review.state == TeamRecordState.REJECTED
            and review.decision_at is not None
        ),
        default=None,
    )
    if latest_rejection_at is not None and any(
        deliverable.created_at < latest_rejection_at
        for deliverable in state.deliverables.values()
        if deliverable.task_id == correction_task.id
        and deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.superseded_by_deliverable_id is None
    ):
        return False
    incorporated_review_ids = {
        review_id
        for review_id in rejected_review_ids
        if review_id in correction_task.description
    }
    for item in state.inbox.values():
        if (
            item.task_id != correction_task.id
            or item.payload.get("kind")
            != "release_review_rejection_correction_work"
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
    return bool(rejected_review_ids) and rejected_review_ids.issubset(
        incorporated_review_ids
    )


def _candidate_release_ids_for_reconciliation(state: TeamState) -> list[str]:
    release_ids: list[str] = []
    for predecessor in sorted(
        state.release_packages.values(),
        key=lambda release: (release.created_at, release.id),
    ):
        if not _is_candidate_correction_predecessor(predecessor):
            continue
        task = _correction_task(state, predecessor)
        if (
            task is None
            or not _task_is_resolved(state, task)
            or not _release_review_correction_is_ready(state, predecessor, task)
        ):
            continue
        successors = _direct_successors(state, predecessor.id)
        has_blocker = _has_reconciliation_blocker(state, predecessor.id)
        if successors:
            if has_blocker:
                release_ids.append(predecessor.id)
            continue
        if has_blocker:
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
        and item.payload.get("kind")
        in {"candidate_correction_resolution", "root_task_resolution"}
    ]
    created = False
    if not notices:
        notice_id = (
            "inbox-candidate-successor-"
            + _stable_token(predecessor.id, correction_task.id, length=24)
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
                "The reviewed rejected-gate correction automatically created "
                "its successor release."
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
        source_kind=TeamBlockerSourceKind.EXPLICIT,
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
        result=result,
        now=now,
    )
    result.setdefault("candidate_correction_blocker_ids", []).append(blocker.id)
    if created:
        result.setdefault("created_candidate_correction_blocker_ids", []).append(
            blocker.id
        )
    return created


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
        existing_successors = _direct_successors(state, predecessor.id)
        if existing_successors:
            successor = existing_successors[0]
            resolved_ids = resolve_active_blockers_for_source(
                state,
                source_kind=TeamBlockerSourceKind.EXPLICIT,
                source_id=_blocker_source_id(predecessor.id),
                actor_agent_id=None,
                dispatch_id=None,
                note=f"Successor release {successor.id} exists.",
                reference_ids=[successor.id],
                result=result,
                now=effective_now,
            )
            changed = bool(resolved_ids) or changed
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
            result.setdefault("existing_successor_release_package_ids", []).append(
                successor.id
            )
            continue

        correction_deliverable_ids = _current_accepted_deliverable_ids(
            state,
            correction_task,
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
            result.setdefault("candidate_correction_errors", {})[
                predecessor.id
            ] = successor_error
            continue

        package_result = _package_result()
        successor_id = (
            "release-"
            + _stable_token(
                "candidate-correction-successor",
                predecessor.id,
                correction_task.id,
                *correction_deliverable_ids,
            )
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
                actor_agent_id=(
                    predecessor.owner_agent_id or correction_task.owner_agent_id
                ),
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
            result.setdefault("candidate_correction_errors", {})[
                predecessor.id
            ] = details
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
            result.setdefault("candidate_correction_errors", {})[
                predecessor.id
            ] = details
            continue

        if package_result.get("created"):
            result.setdefault("created_successor_release_package_ids", []).append(
                created_id
            )
            result.setdefault("release_package_ids", []).append(created_id)
            result.setdefault("events", []).extend(package_result.get("events") or [])
            result.setdefault("git_promotion_event_ids", []).extend(
                package_result.get("git_promotion_event_ids") or []
            )
        else:
            result.setdefault("existing_successor_release_package_ids", []).append(
                created_id
            )
        resolve_active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.EXPLICIT,
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
                    outbox_records=lambda: _team_event_outbox_records(
                        result.get("events") or []
                    ),
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
