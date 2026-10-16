from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import (
    AgentInboxItem,
    AgentStatus,
    AgentTask,
    GitWorkContextState,
    GitPromotionRecord,
    ReleasePackage,
    ReleaseScopeValidation,
    TeamBlockerEventType,
    TeamBlockerResolutionScope,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    TaskCancellation,
    TaskDeliverableContract,
    TeamTaskKind,
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
from .domain.release_gates import (
    _candidate_correction_git_base,
    _candidate_correction_mutation_policy,
)
from .domain.review import retry_deliverable_acceptance_if_reviewed
from .domain.task_assessment import (
    TaskAssessmentBoundaryError,
    require_deliverable_acceptance,
    require_task_resolved,
)
from .domain.work_due import _mark_agent_work_due
from .domain.work_lineage import ensure_task_work_lineage


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
_NONTERMINAL_CORRECTION_TASK_STATES = {
    TeamTaskState.PENDING,
    TeamTaskState.IN_PROGRESS,
    TeamTaskState.BLOCKED,
    TeamTaskState.WAITING_FOR_INPUT,
    TeamTaskState.WAITING_FOR_REVIEW,
}
_MISSING_COMPOSITION_REASON_CODE = "dependency_composition_manifest_missing"
_MISSING_COMPOSITION_BLOCKER_REASON = (
    "deliverable_acceptance_deferred:dependency_composition_manifest_missing"
)


@dataclass(frozen=True, slots=True)
class _LegacyCorrectionNormalization:
    correction_task_id: str
    correction_deliverable_id: str
    source_task_id: str
    source_deliverable_id: str
    git_work_context_id: str
    accepted_by_agent_id: str
    reviewed_commit_sha: str


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


def _full_git_object_id(value: object) -> str | None:
    normalized = str(value or "").strip().casefold()
    if len(normalized) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        return None
    return normalized


def _acceptance_failure_code(
    state: TeamState,
    *,
    deliverable_id: str,
) -> str | None:
    """Purely identify the exact deferred boundary represented by legacy state."""

    preview = state.model_copy(deep=True)
    deliverable = preview.deliverables[deliverable_id]
    task = preview.tasks[deliverable.task_id]
    missing_manifest_blockers = [
        blocker
        for blocker in preview.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE
        and blocker.source_id == deliverable.id
        and blocker.reason_code == _MISSING_COMPOSITION_BLOCKER_REASON
    ]
    for blocker in missing_manifest_blockers:
        blocker.state = TeamBlockerState.RESOLVED
    if missing_manifest_blockers and task.state == TeamTaskState.BLOCKED:
        task.state = TeamTaskState.WAITING_FOR_REVIEW
    try:
        require_deliverable_acceptance(preview, deliverable.id)
    except TaskAssessmentBoundaryError as exc:
        return exc.code
    return None


def _legacy_correction_normalization(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_task: AgentTask,
) -> _LegacyCorrectionNormalization | None:
    """Match only the stranded, reviewed legacy wrapper emitted by old workers."""

    if (
        not is_automatic_candidate_correction_task(correction_task)
        or correction_task.state not in _NONTERMINAL_CORRECTION_TASK_STATES
        or correction_task.project_id != predecessor.project_id
        or correction_task.parent_task_id is not None
        or correction_task.task_kind != TeamTaskKind.SUPPORT
        or not correction_task.integration_boundary
        or correction_task.deliverable_contract is None
        or not correction_task.deliverable_contract.require_git_provenance
        or len(correction_task.dependency_task_ids) != 1
        or correction_task.deliverable_contract.required_contribution_task_ids
        != correction_task.dependency_task_ids
        or correction_task.git_base_branch != _candidate_correction_git_base(predecessor)
    ):
        return None
    expected_policy = _candidate_correction_mutation_policy()
    if (
        correction_task.mutation_policy is not None
        and correction_task.mutation_policy != expected_policy
    ):
        return None

    source_task_id = correction_task.dependency_task_ids[0]
    source_task = state.tasks.get(source_task_id)
    if (
        source_task is None
        or source_task.originating_task_id != correction_task.id
        or source_task.state not in {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}
    ):
        return None

    current_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if deliverable.task_id == correction_task.id
        and deliverable.superseded_by_deliverable_id is None
    ]
    if len(current_deliverables) != 1:
        return None
    correction_deliverable = current_deliverables[0]
    if (
        correction_deliverable.state != TeamRecordState.PENDING
        or correction_deliverable.acceptance_policy_version != 2
        or correction_deliverable.dependency_composition_manifest is not None
        or len(correction_deliverable.source_deliverable_ids) != 1
        or any(finding.state == "open" for finding in correction_deliverable.carried_findings)
    ):
        return None

    source_deliverable_id = correction_deliverable.source_deliverable_ids[0]
    source_deliverable = state.deliverables.get(source_deliverable_id)
    current_source_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if deliverable.task_id == source_task.id
        and deliverable.superseded_by_deliverable_id is None
    ]
    if (
        source_deliverable is None
        or source_deliverable.task_id != source_task.id
        or source_deliverable.state != TeamRecordState.ACCEPTED
        or source_deliverable.superseded_by_deliverable_id is not None
        or [deliverable.id for deliverable in current_source_deliverables]
        != [source_deliverable.id]
    ):
        return None

    correction_commit = _full_git_object_id(correction_deliverable.git_refs.submitted_commit_sha)
    commit_evidence = {
        correction_commit,
        _full_git_object_id(correction_deliverable.git_refs.commit_sha),
        _full_git_object_id(correction_deliverable.git_refs.reviewed_commit_sha),
        _full_git_object_id(source_deliverable.git_refs.commit_sha),
        _full_git_object_id(source_deliverable.git_refs.submitted_commit_sha),
        _full_git_object_id(source_deliverable.git_refs.reviewed_commit_sha),
    }
    if correction_commit is None or commit_evidence != {correction_commit}:
        return None
    if (
        correction_deliverable.review_manifest is None
        or _full_git_object_id(correction_deliverable.review_manifest.reviewed_commit_sha)
        != correction_commit
    ):
        return None

    context_id = correction_deliverable.git_refs.branch_context_id
    context = state.git_work_contexts.get(context_id or "")
    if (
        context is None
        or context.state != GitWorkContextState.SUBMITTED
        or context.task_id != correction_task.id
        or context.agent_id != correction_deliverable.producer_agent_id
        or context.agent_branch != correction_deliverable.git_refs.agent_branch
        or _full_git_object_id(context.commit_sha) != correction_commit
        or context.dependency_composition_manifest is not None
    ):
        return None
    context_sources = context.dependency_git_sources
    if len(context_sources) != 1:
        return None
    context_source = context_sources[0]
    if (
        context_source.dependency_task_id != source_task.id
        or context_source.deliverable_id != source_deliverable.id
        or context_source.relationship_kind != "required_contribution"
        or context_source.branch != source_deliverable.git_refs.agent_branch
        or source_deliverable.git_refs.accepted_pinned_ref is None
        or context_source.pinned_ref != source_deliverable.git_refs.accepted_pinned_ref
        or _full_git_object_id(context_source.commit_sha) != correction_commit
    ):
        return None

    cohort_id = correction_deliverable.review_cohort_id
    if (
        cohort_id is None
        or correction_deliverable.review_cohort_sealed_at is None
        or not correction_deliverable.review_request_ids
        or len(set(correction_deliverable.review_request_ids))
        != len(correction_deliverable.review_request_ids)
    ):
        return None
    current_reviews = [
        state.review_requests[review_id]
        for review_id in correction_deliverable.review_request_ids
        if review_id in state.review_requests
        and state.review_requests[review_id].superseded_by_review_request_id is None
        and state.review_requests[review_id].retired_at is None
    ]
    if len(current_reviews) != len(correction_deliverable.review_request_ids) or any(
        review.deliverable_id != correction_deliverable.id
        or review.review_cohort_id != cohort_id
        or review.state != TeamRecordState.ACCEPTED
        or _full_git_object_id(review.reviewed_commit_sha) != correction_commit
        or review.repository_snapshot is None
        or review.repository_snapshot.source_kind != "deliverable_candidate"
        or _full_git_object_id(review.repository_snapshot.commit_sha) != correction_commit
        for review in current_reviews
    ):
        return None
    if (
        _acceptance_failure_code(
            state,
            deliverable_id=correction_deliverable.id,
        )
        != _MISSING_COMPOSITION_REASON_CODE
    ):
        return None

    accepted_by_agent_id = sorted(
        current_reviews,
        key=lambda review: (
            (review.decision_at or review.created_at).isoformat()
            if review.decision_at is not None or review.created_at is not None
            else "",
            review.id,
        ),
    )[-1].reviewer_agent_id
    assert context_id is not None
    return _LegacyCorrectionNormalization(
        correction_task_id=correction_task.id,
        correction_deliverable_id=correction_deliverable.id,
        source_task_id=source_task.id,
        source_deliverable_id=source_deliverable.id,
        git_work_context_id=context_id,
        accepted_by_agent_id=accepted_by_agent_id,
        reviewed_commit_sha=correction_commit,
    )


def _normalize_legacy_correction_and_replay_acceptance(
    state: TeamState,
    *,
    normalization: _LegacyCorrectionNormalization,
    result: dict[str, Any],
    now: datetime,
) -> None:
    """Normalize the exact legacy wrapper and reuse its durable accepted review."""

    task = state.tasks[normalization.correction_task_id]
    deliverable = state.deliverables[normalization.correction_deliverable_id]
    task.dependency_task_ids = []
    task.integration_boundary = False
    task.deliverable_contract = TaskDeliverableContract(require_git_provenance=True)
    deliverable.source_deliverable_ids = []
    task.mutation_policy = _candidate_correction_mutation_policy()
    task.updated_at = now
    accepted = retry_deliverable_acceptance_if_reviewed(
        state,
        deliverable_id=deliverable.id,
        actor_agent_id=normalization.accepted_by_agent_id,
        result=result,
        now=now,
    )
    if not accepted:
        raise RuntimeError(
            f"legacy correction {task.id} normalization did not replay accepted review"
        )
    result.setdefault("normalized_legacy_candidate_correction_task_ids", []).append(task.id)
    result.setdefault("legacy_candidate_correction_normalizations", {})[task.id] = {
        "correction_deliverable_id": deliverable.id,
        "source_task_id": normalization.source_task_id,
        "source_deliverable_id": normalization.source_deliverable_id,
        "git_work_context_id": normalization.git_work_context_id,
        "reviewed_commit_sha": normalization.reviewed_commit_sha,
    }


def _direct_successors(
    state: TeamState,
    predecessor_id: str,
) -> list[ReleasePackage]:
    records = (
        state.query_records(
            "release_packages", "supersedes_release_package_id=?", (predecessor_id,)
        )
        if hasattr(state, "query_records")
        else state.release_packages.values()
    )
    return sorted(
        (release for release in records if release.supersedes_release_package_id == predecessor_id),
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
    return (
        bool(current_ids)
        and current_ids != previous_ids
        and all(
            deliverable_id in current_ids
            or _deliverable_supersession_reaches(
                state,
                deliverable_id=deliverable_id,
                current_ids=current_ids,
            )
            for deliverable_id in previous_ids
        )
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
    preview_state = state.model_copy(deep=True)
    preview_state.release_packages.pop(successor.id, None)
    preview_result = _package_result()
    try:
        preview_id = _build_release_package(
            preview_state,
            project=preview_state.projects[predecessor.project_id],
            request={
                "id": successor.id,
                "project_id": predecessor.project_id,
                "deliverable_ids": correction_deliverable_ids,
                "supersedes_release_package_id": predecessor.id,
                "summary": successor.summary,
                "release_branch": successor.release_branch,
                "canonical_branch": successor.canonical_branch,
            },
            result=preview_result,
            projects_to_recalc=set(),
            actor_agent_id=(predecessor.owner_agent_id or correction_task.owner_agent_id),
            team_id=preview_state.projects[predecessor.project_id].team_id,
        )
    except Exception as exc:  # pragma: no cover - defensive durable recovery
        preview_id = None
        preview_result["errors"].append(
            f"{exc.__class__.__name__}: {str(exc) or 'unexpected packaging failure'}"
        )
    if preview_id is None or preview_result["errors"]:
        validation_error = (
            "; ".join(str(error) for error in preview_result["errors"])
            or f"failed to validate replacement intake for successor {successor.id}"
        )
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
    preview_release = preview_state.release_packages[preview_id]

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
    successor.deliverable_ids = list(preview_release.deliverable_ids)
    successor.artifact_refs = list(preview_release.artifact_refs)
    successor.feature_branches = list(preview_release.feature_branches)
    successor.release_branch = preview_release.release_branch
    successor.canonical_branch = preview_release.canonical_branch
    successor.git_base_branch = preview_release.git_base_branch
    successor.base_commit_sha = preview_release.base_commit_sha
    successor.canonical_base_commit_sha = preview_release.canonical_base_commit_sha
    successor.policy_version = preview_release.policy_version
    successor.verification_waivers = [
        waiver.model_copy(deep=True) for waiver in preview_release.verification_waivers
    ]
    successor.state = TeamRecordState.PENDING
    successor.error = None
    successor.gate_failure = None
    successor.scope_validation = (
        preview_release.scope_validation.model_copy(deep=True)
        if preview_release.scope_validation is not None
        else ReleaseScopeValidation(state="pending")
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
    records = (
        state.query_records(
            "deliverables",
            "task_id IN (SELECT id FROM team_tasks WHERE originating_task_id=? OR id=?)",
            (correction_task.id, correction_task.id),
        )
        if hasattr(state, "query_records")
        else state.deliverables.values()
    )
    recovery_ids = sorted(
        deliverable.id
        for deliverable in records
        if deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.superseded_by_deliverable_id is None
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
    evidence = (
        state.query_records("inbox", "task_id=?", (correction_task.id,))
        if hasattr(state, "query_records")
        else state.inbox.values()
    )
    for item in evidence:
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
    records = (
        state.release_packages.values()
        if not hasattr(state, "query_records")
        else state.query_records(
            "release_packages",
            "state='rejected' AND ((json_extract(gate_failure_json,'$.stage')='verification' "
            "AND json_extract(gate_failure_json,'$.kind')='candidate') OR "
            "(json_extract(gate_failure_json,'$.stage')='review' AND json_extract(gate_failure_json,'$.kind')='rejected'))"
            + (
                " AND project_id IN (" + ",".join("?" for _ in state.project_ids) + ")"
                if state.project_ids
                else ""
            ),
            state.project_ids,
        )
    )
    for predecessor in sorted(
        records,
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
        if task is None:
            continue
        legacy_normalization = _legacy_correction_normalization(
            state,
            predecessor=predecessor,
            correction_task=task,
        )
        if legacy_normalization is None and (
            not _task_is_resolved(state, task)
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
    records = (
        state.query_records(
            "inbox", "task_id=? AND recipient_agent_id=?", (correction_task.id, recipient_agent_id)
        )
        if hasattr(state, "query_records")
        else state.inbox.values()
    )
    notices = [
        item
        for item in records
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
    accepted_sources = sorted(
        (
            deliverable
            for deliverable in state.deliverables.values()
            if deliverable.task_id == correction_task.id
            and deliverable.state == TeamRecordState.ACCEPTED
            and deliverable.superseded_by_deliverable_id is None
            and deliverable.git_refs.agent_branch is not None
            and (deliverable.git_refs.submitted_commit_sha or deliverable.git_refs.commit_sha)
            is not None
            and any(
                (review := state.review_requests.get(review_id)) is not None
                and review.state == TeamRecordState.ACCEPTED
                and review.superseded_by_review_request_id is None
                for review_id in deliverable.review_request_ids
            )
        ),
        key=lambda deliverable: (deliverable.created_at, deliverable.id),
        reverse=True,
    )
    lead_id = project_lead_agent_id(state, project.id)
    lead = state.agents.get(lead_id) if lead_id is not None else None
    if accepted_sources and lead is not None and lead.status == AgentStatus.ACTIVE:
        source = accepted_sources[0]
        source_branch = source.git_refs.agent_branch
        source_commit = source.git_refs.submitted_commit_sha or source.git_refs.commit_sha
        assert source_branch is not None and source_commit is not None
        recovery_task_id = "task-candidate-recovery-" + _stable_token(
            predecessor.id,
            correction_task.id,
            source_commit,
            length=24,
        )
        recovery_task = state.tasks.get(recovery_task_id)
        if recovery_task is None:
            recovery_task = AgentTask(
                id=recovery_task_id,
                owner_agent_id=lead.id,
                project_id=project.id,
                priority=correction_task.priority,
                state=TeamTaskState.PENDING,
                title=f"Converge release lineage for {predecessor.id}",
                description=(
                    f"Recover the single blocked successor for {predecessor.id}. Start "
                    f"from accepted correction branch {source_branch} at exact commit "
                    f"{source_commit}. Resolve only the packaging or composition failure, "
                    "submit reviewed Git work, and do not request another release package."
                ),
                created_by_actor="system:release-lineage-recovery",
                originating_task_id=correction_task.id,
                authored_by=correction_task.authored_by.model_copy(deep=True)
                if correction_task.authored_by
                else None,
                work_item_attributions=[
                    item.model_copy(deep=True) for item in correction_task.work_item_attributions
                ]
                if correction_task.work_item_attributions
                else None,
                release_base_package_id=predecessor.id,
                git_base_branch=source_branch,
                git_base_commit_sha=source_commit,
                dependency_task_ids=[],
                deliverable_contract=TaskDeliverableContract(require_git_provenance=True),
                created_at=now,
                updated_at=now,
            )
            state.tasks[recovery_task.id] = recovery_task
            ensure_task_work_lineage(state, recovery_task)
            blocker.affected_task_ids = sorted(set(blocker.affected_task_ids) | {recovery_task.id})
            blocker.updated_at = now
            _mark_agent_work_due(state, agent_id=lead.id, now=now)
            result.setdefault("created_release_lineage_recovery_task_ids", []).append(
                recovery_task.id
            )
            changed = True
    elif accepted_sources:
        result.setdefault("release_lineage_recovery_assignment_required", []).append(predecessor.id)
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
    records = (
        [state.release_packages[key] for key in selected_ids if key in state.release_packages]
        if selected_ids
        else state.release_packages.values()
    )
    predecessors = sorted(
        (
            release
            for release in records
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
        if correction_task is not None:
            legacy_normalization = _legacy_correction_normalization(
                state,
                predecessor=predecessor,
                correction_task=correction_task,
            )
            if legacy_normalization is not None:
                _normalize_legacy_correction_and_replay_acceptance(
                    state,
                    normalization=legacy_normalization,
                    result=result,
                    now=effective_now,
                )
                correction_task = state.tasks[correction_task.id]
                changed = True
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
                    "focused checks as appropriate, and submit an accepted reviewed Git "
                    "deliverable. The frozen release candidate verification remains "
                    "authoritative. Do "
                    "not merge or rebase another base branch and do not request a release."
                    f"{operator_instruction}"
                ),
                created_by_actor="system:candidate-correction-recovery",
                originating_task_id=correction_task.id,
                authored_by=correction_task.authored_by.model_copy(deep=True)
                if correction_task.authored_by
                else None,
                work_item_attributions=[
                    item.model_copy(deep=True) for item in correction_task.work_item_attributions
                ]
                if correction_task.work_item_attributions
                else None,
                release_base_package_id=predecessor.id,
                git_base_branch=source_branch,
                git_base_commit_sha=source_commit,
                dependency_task_ids=[],
                deliverable_contract=TaskDeliverableContract(require_git_provenance=True),
                created_at=effective_now,
                updated_at=effective_now,
            )
            state.tasks[recovery_task.id] = recovery_task
            ensure_task_work_lineage(state, recovery_task)
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

    raise ValueError(f"unsupported candidate correction recovery action {action!r}")


class CandidateCorrectionReconciliationService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    def reconcile(self) -> dict[str, Any]:
        from ..lifecycle_scope import TeamLifecycleRepository

        self._repository = TeamLifecycleRepository(self._repository, purpose="candidate_correction")
        last_conflict: TeamStateConflictError | None = None
        for _attempt in range(_MAX_RECONCILIATION_ATTEMPTS):
            try:
                current = self._repository.read()
                release_ids = _candidate_release_ids_for_reconciliation(current)
            except TeamStateConflictError as exc:
                last_conflict = exc
                continue
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
