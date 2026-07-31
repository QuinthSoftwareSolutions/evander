from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from ..models import (
    HumanReviewFinding,
    TaskCancellation,
    TeamCase,
    TeamBlockerSourceKind,
    TeamInboxPriority,
    TeamRecordState,
    TeamDispatchStatus,
    TeamTaskState,
    project_lead_agent_id,
    utc_now,
)
from ..repository import TeamStateConflictError, TeamStateRepository
from .commands.outbox import _team_event_outbox_records
from .domain.project_status import _recalculate_project_status
from .domain.blockers import (
    active_blockers_for_source,
    open_or_get_blocker,
    resolve_active_blockers_for_source,
)
from .domain.human_reviews import (
    HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY,
    HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY,
    ensure_human_correction_aggregation,
)
from .domain.release_packages import (
    _build_release_package,
    _correction_aggregate_promotion,
    _create_correction_aggregate_promotion,
    _reviewed_commit_for_release,
    _successor_release_request_error,
)


HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY = (
    HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY
)
_MAX_RECONCILIATION_ATTEMPTS = 3
_HUMAN_CORRECTION_AGGREGATION_BLOCKER_SOURCE_PREFIX = (
    "human-correction-aggregation:"
)
_TERMINAL_COORDINATION_PARENT_STATES = {
    TeamTaskState.FINISHED,
    TeamTaskState.ACCEPTED,
    TeamTaskState.REJECTED,
    TeamTaskState.CANCELLED,
}


def _stable_token(*parts: str, length: int = 24) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def _human_correction_cases(state: Any) -> list[TeamCase]:
    return sorted(
        (
            case
            for case in state.cases.values()
            if case.source == "human_release_review"
            and case.source_ref is not None
            and case.project_id is not None
        ),
        key=lambda case: (case.created_at, case.id),
    )


def _case_findings(state: Any, case: TeamCase) -> list[HumanReviewFinding]:
    return sorted(
        (
            finding
            for finding in state.human_review_findings.values()
            if finding.source_human_review_request_id == case.source_ref
            and finding.state == "open"
        ),
        key=lambda finding: (finding.created_at, finding.id),
    )


def _case_predecessor(state: Any, case: TeamCase) -> tuple[Any | None, Any | None]:
    request = state.human_review_requests.get(case.source_ref or "")
    release = (
        state.release_packages.get(request.release_package_id)
        if request is not None
        else None
    )
    return request, release


def _current_accepted_deliverables(state: Any, finding: HumanReviewFinding) -> list[Any]:
    return sorted(
        (
            deliverable
            for deliverable in state.deliverables.values()
            if deliverable.task_id == finding.correction_task_id
            and deliverable.state == TeamRecordState.ACCEPTED
            and deliverable.superseded_by_deliverable_id is None
        ),
        key=lambda deliverable: deliverable.id,
    )


def _deliverable_resolution_evidence(deliverable: Any) -> list[str]:
    refs = {
        deliverable.id,
        *deliverable.artifact_refs,
        *(
            deliverable.verification.evidence_refs
            if deliverable.verification is not None
            else []
        ),
    }
    git_refs = deliverable.git_refs
    for value in (
        git_refs.reviewed_commit_sha,
        git_refs.submitted_commit_sha,
        git_refs.commit_sha,
        git_refs.feature_commit_sha,
    ):
        if value is not None:
            refs.add(value)
    return sorted(refs)


def _successors(state: Any, predecessor_id: str) -> list[Any]:
    return sorted(
        (
            release
            for release in state.release_packages.values()
            if release.supersedes_release_package_id == predecessor_id
        ),
        key=lambda release: (release.created_at, release.id),
    )


def _coordination_parent_id(case: TeamCase) -> str | None:
    value = case.metadata.get(HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY)
    text = str(value or "").strip()
    return text or None


def _finish_coordination_parent(parent: Any, *, now: datetime) -> bool:
    if parent.state in _TERMINAL_COORDINATION_PARENT_STATES:
        return False
    parent.state = TeamTaskState.FINISHED
    parent.updated_at = now
    parent.state_entered_at = now
    return True


def _legacy_case_can_convert(state: Any, case: TeamCase, findings: list[Any]) -> bool:
    if _coordination_parent_id(case) is not None or not findings:
        return False
    task_ids = [finding.correction_task_id for finding in findings]
    if len(set(task_ids)) != len(task_ids):
        return False
    return all(
        task_id in state.tasks and state.tasks[task_id].case_id == case.id
        for task_id in task_ids
    )


def _case_needs_reconciliation(state: Any, case: TeamCase) -> bool:
    request, predecessor = _case_predecessor(state, case)
    if (
        request is None
        or predecessor is None
        or request.state != "changes_requested"
        or predecessor.state != TeamRecordState.REJECTED
    ):
        return False
    findings = _case_findings(state, case)
    successors = _successors(state, predecessor.id)
    if successors:
        if any(
            _legacy_successor_needs_migration(
                state,
                predecessor=predecessor,
                successor=successor,
            )
            for successor in successors
        ):
            return True
        parent = state.tasks.get(_coordination_parent_id(case) or "")
        return (
            parent is not None
            and parent.state not in _TERMINAL_COORDINATION_PARENT_STATES
        )
    if any(
        blocker.reason_code == "human_correction_aggregation_failed"
        for blocker in active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id=(
                _HUMAN_CORRECTION_AGGREGATION_BLOCKER_SOURCE_PREFIX
                + request.id
            ),
        )
    ):
        return False
    parent_id = _coordination_parent_id(case)
    if parent_id is not None and parent_id not in state.tasks:
        return True
    if parent_id is None and findings:
        return True
    current_finding_ids = {finding.id for finding in findings}
    configured_ambiguous_finding_ids = {
        str(value).strip()
        for value in (
            case.metadata.get(HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY) or []
        )
        if str(value).strip()
    }
    if configured_ambiguous_finding_ids - current_finding_ids:
        return True
    if configured_ambiguous_finding_ids:
        return False
    if _legacy_case_can_convert(state, case, findings):
        return True
    for finding in findings:
        accepted = _current_accepted_deliverables(state, finding)
        if len(accepted) > 1:
            return True
        if len(accepted) != 1:
            continue
        if finding.proposed_by_deliverable_ids != [accepted[0].id]:
            return True
        if not finding.proposed_resolution or not finding.resolution_evidence_refs:
            return True
    if findings and all(len(_current_accepted_deliverables(state, item)) == 1 for item in findings):
        deliverables = [
            _current_accepted_deliverables(state, item)[0] for item in findings
        ]
        if not all(_reviews_ready(state, deliverable)[0] for deliverable in deliverables):
            return False
        return (
            _successor_release_request_error(
                state,
                project_id=predecessor.project_id,
                predecessor=predecessor,
                correction_deliverable_ids=sorted(
                    deliverable.id for deliverable in deliverables
                ),
            )
            is None
        )
    return False


def _legacy_successor_promotions(state: Any, successor: Any) -> list[Any]:
    linked_ids = set(successor.git_promotion_record_ids)
    return sorted(
        (
            promotion
            for promotion in state.git_promotions.values()
            if promotion.promotion_kind == "feature"
            and promotion.state != "retired"
            and (
                promotion.release_package_id == successor.id
                or promotion.id in linked_ids
            )
        ),
        key=lambda item: (item.created_at, item.id),
    )


def _legacy_successor_needs_migration(
    state: Any,
    *,
    predecessor: Any,
    successor: Any,
) -> bool:
    if (
        predecessor.state != TeamRecordState.REJECTED
        or successor.state
        not in {
            TeamRecordState.PENDING,
            TeamRecordState.IN_PROGRESS,
            TeamRecordState.BLOCKED,
        }
        or successor.candidate_commit_sha is not None
        or _correction_aggregate_promotion(state, successor) is not None
    ):
        return False
    linked_ids = set(successor.git_promotion_record_ids)
    if any(
        promotion.promotion_kind in {"release", "canonical_push"}
        and promotion.state != "retired"
        and (
            promotion.release_package_id == successor.id
            or promotion.id in linked_ids
        )
        for promotion in state.git_promotions.values()
    ):
        return False
    legacy_promotions = _legacy_successor_promotions(state, successor)
    return bool(legacy_promotions) and all(
        promotion.state == "failed" for promotion in legacy_promotions
    )


def _cancel_obsolete_merge_recovery_tasks(
    state: Any,
    *,
    successor: Any,
    aggregate_promotion_id: str,
    result: dict[str, Any],
    now: datetime,
) -> None:
    active_dispatch_states = {
        TeamDispatchStatus.PREPARING,
        TeamDispatchStatus.LEASED,
    }
    for task in state.tasks.values():
        if (
            task.created_by_actor != "system:git-merge-recovery"
            or task.project_id != successor.project_id
            or task.state != TeamTaskState.PENDING
            or task.deliverable_ids
        ):
            continue
        linked = any(
            blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE
            and blocker.source_id == successor.id
            and task.id in blocker.affected_task_ids
            for blocker in state.blockers.values()
        )
        has_context = any(
            context.task_id == task.id
            for context in state.git_work_contexts.values()
        )
        has_active_dispatch = any(
            task.id in dispatch.task_ids
            and dispatch.status in active_dispatch_states
            for dispatch in state.dispatches.values()
        )
        if not linked or has_context or has_active_dispatch:
            continue
        task.cancellation = TaskCancellation(
            reason_code="promotion_boundary_superseded",
            reason=(
                "Legacy per-correction merge recovery was superseded by the "
                f"package-level aggregate promotion {aggregate_promotion_id}."
            ),
            actor_kind="system",
            actor_id="system:human-review",
            successor_task_id=None,
            cancelled_at=now,
        )
        task.state = TeamTaskState.CANCELLED
        task.state_entered_at = now
        task.updated_at = now
        result.setdefault("cancelled_merge_recovery_task_ids", []).append(task.id)


def _migrate_legacy_successor(
    state: Any,
    *,
    predecessor: Any,
    successor: Any,
    result: dict[str, Any],
    now: datetime,
) -> bool:
    legacy_promotions = _legacy_successor_promotions(state, successor)
    if not _legacy_successor_needs_migration(
        state,
        predecessor=predecessor,
        successor=successor,
    ):
        return False
    correction_ids = [
        deliverable_id
        for deliverable_id in successor.deliverable_ids
        if deliverable_id not in set(predecessor.deliverable_ids)
    ]
    migration_result = _package_result()
    aggregate = _create_correction_aggregate_promotion(
        state,
        release=successor,
        predecessor=predecessor,
        correction_deliverable_ids=correction_ids,
        actor_agent_id=(
            project_lead_agent_id(state, successor.project_id)
            or state.projects[successor.project_id].lead_agent_id
        ),
        result=migration_result,
    )
    if aggregate is None:
        result.setdefault("migration_errors", {})[successor.id] = list(
            migration_result["errors"]
        )
        return False
    for promotion in legacy_promotions:
        promotion.state = "retired"
        promotion.outputs["retired_at"] = now.isoformat()
        promotion.outputs["retirement_reason"] = (
            "Superseded by one rejected-candidate-rooted correction aggregate."
        )
        promotion.outputs["superseded_by_promotion_id"] = aggregate.id
        promotion.updated_at = now
    successor.feature_branches = []
    successor.state = TeamRecordState.PENDING
    successor.error = None
    if successor.scope_validation is not None:
        successor.scope_validation.state = "pending"
        successor.scope_validation.unexpected_paths = []
        successor.scope_validation.unexpected_commit_shas = []
        successor.scope_validation.release_commit_sha = None
        successor.scope_validation.validated_at = None
    successor.updated_at = now
    _cancel_obsolete_merge_recovery_tasks(
        state,
        successor=successor,
        aggregate_promotion_id=aggregate.id,
        result=result,
        now=now,
    )
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=successor.id,
        actor_agent_id=None,
        dispatch_id=None,
        note=(
            "Legacy per-correction promotion boundaries were retired in favor "
            f"of package-level aggregate {aggregate.id}."
        ),
        reference_ids=[aggregate.id],
        result=result,
        now=now,
    )
    result.setdefault("retired_legacy_promotion_ids", []).extend(
        promotion.id for promotion in legacy_promotions
    )
    result.setdefault("migrated_successor_release_package_ids", []).append(
        successor.id
    )
    result.setdefault("events", []).extend(migration_result["events"])
    result.setdefault("git_promotion_event_ids", []).append(aggregate.id)
    return True


def _open_aggregation_blocker(
    state: Any,
    *,
    case: TeamCase,
    request: Any,
    details: str,
    result: dict[str, Any],
    now: datetime,
) -> None:
    project = state.projects[case.project_id]
    owner_id = (
        case.owner_agent_id
        or project_lead_agent_id(state, project.id)
        or project.lead_agent_id
    )
    configured_parent_id = _coordination_parent_id(case)
    blocker, created = open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id=(
            _HUMAN_CORRECTION_AGGREGATION_BLOCKER_SOURCE_PREFIX + request.id
        ),
        summary=f"Human correction aggregation failed for {case.id}",
        details=details,
        reason_code="human_correction_aggregation_failed",
        priority=TeamInboxPriority.HIGH,
        reporter_actor="system:human-review",
        initial_owner_agent_id=owner_id,
        team_id=case.team_id,
        project_id=project.id,
        task_id=(
            configured_parent_id
            if configured_parent_id in state.tasks
            else None
        ),
        affected_task_ids=[
            task_id for task_id in case.task_ids if task_id in state.tasks
        ],
        result=result,
        now=now,
    )
    result.setdefault("aggregation_blocker_ids", []).append(blocker.id)
    if created:
        result.setdefault("created_aggregation_blocker_ids", []).append(blocker.id)


def _reviews_ready(state: Any, deliverable: Any) -> tuple[bool, str | None]:
    has_git = any(
        (
            deliverable.git_refs.feature_branch,
            deliverable.git_refs.review_branch,
            deliverable.git_refs.agent_branch,
            deliverable.git_refs.commit_sha,
            deliverable.git_refs.submitted_commit_sha,
            deliverable.git_refs.reviewed_commit_sha,
        )
    )
    if not has_git:
        return True, None
    _commit, error = _reviewed_commit_for_release(state, deliverable)
    return error is None, error


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


def _reconcile_case(
    state: Any,
    *,
    case: TeamCase,
    result: dict[str, Any],
    now: datetime,
) -> bool:
    request, predecessor = _case_predecessor(state, case)
    if (
        request is None
        or predecessor is None
        or request.state != "changes_requested"
        or predecessor.state != TeamRecordState.REJECTED
    ):
        return False
    findings = _case_findings(state, case)
    successors = _successors(state, predecessor.id)
    if successors:
        migrated = False
        for successor in successors:
            migrated = (
                _migrate_legacy_successor(
                    state,
                    predecessor=predecessor,
                    successor=successor,
                    result=result,
                    now=now,
                )
                or migrated
            )
        parent = state.tasks.get(_coordination_parent_id(case) or "")
        changed = (
            _finish_coordination_parent(parent, now=now)
            if parent is not None
            else False
        )
        result.setdefault("existing_successor_release_package_ids", []).extend(
            release.id for release in successors
        )
        return changed or migrated
    before_created = set(result.get("created_task_ids") or [])
    before_reparented = set(result.get("reparented_correction_task_ids") or [])
    try:
        parent = ensure_human_correction_aggregation(
            state,
            case=case,
            result=result,
            now=now,
        )
    except ValueError as exc:
        _open_aggregation_blocker(
            state,
            case=case,
            request=request,
            details=str(exc),
            result=result,
            now=now,
        )
        return True
    created_task_ids = set(result.get("created_task_ids") or [])
    reparented_task_ids = set(
        result.get("reparented_correction_task_ids") or []
    )
    newly_created = sorted(created_task_ids - before_created)
    if parent.id in newly_created:
        result.setdefault("created_coordination_task_ids", []).append(parent.id)
    changed = bool(
        newly_created or reparented_task_ids - before_reparented
    )
    if not findings:
        return changed
    configured_ambiguous_finding_ids = {
        str(value).strip()
        for value in (
            case.metadata.get(HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY) or []
        )
        if str(value).strip()
    }
    current_finding_ids = {finding.id for finding in findings}
    ambiguous_finding_ids = (
        configured_ambiguous_finding_ids & current_finding_ids
    )
    if ambiguous_finding_ids != configured_ambiguous_finding_ids:
        case.metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] = sorted(
            ambiguous_finding_ids
        )
        case.updated_at = now
        changed = True
    if ambiguous_finding_ids:
        result.setdefault("waiting_ambiguous_finding_ids", []).extend(
            sorted(ambiguous_finding_ids)
        )
        return changed

    correction_deliverable_ids: list[str] = []
    ambiguous_findings: list[str] = []
    for finding in findings:
        accepted = _current_accepted_deliverables(state, finding)
        if len(accepted) != 1:
            if len(accepted) > 1:
                ambiguous_findings.append(finding.id)
            continue
        deliverable = accepted[0]
        evidence = _deliverable_resolution_evidence(deliverable)
        finding_changed = False
        if finding.proposed_by_deliverable_ids != [deliverable.id]:
            finding.proposed_by_deliverable_ids = [deliverable.id]
            finding_changed = True
        if not finding.proposed_resolution:
            finding.proposed_resolution = deliverable.summary
            finding_changed = True
        merged_evidence = sorted(
            {*finding.resolution_evidence_refs, *evidence}
        )
        if finding.resolution_evidence_refs != merged_evidence:
            finding.resolution_evidence_refs = merged_evidence
            finding_changed = True
        if finding_changed:
            result.setdefault("derived_resolution_finding_ids", []).append(
                finding.id
            )
            changed = True
        correction_deliverable_ids.append(deliverable.id)

    if ambiguous_findings:
        result.setdefault("ambiguous_finding_ids", []).extend(ambiguous_findings)
        _open_aggregation_blocker(
            state,
            case=case,
            request=request,
            details=(
                "Each human finding must resolve through exactly one current "
                "accepted correction deliverable. Multiple current deliverables "
                "exist for: "
                + ", ".join(ambiguous_findings)
            ),
            result=result,
            now=now,
        )
        return True
    if len(correction_deliverable_ids) != len(findings):
        result.setdefault("waiting_case_ids", []).append(case.id)
        return changed

    for deliverable_id in correction_deliverable_ids:
        deliverable = state.deliverables[deliverable_id]
        ready, error = _reviews_ready(state, deliverable)
        if not ready:
            result.setdefault("waiting_review_errors", {})[deliverable_id] = error
            result.setdefault("waiting_case_ids", []).append(case.id)
            return changed

    successor_error = _successor_release_request_error(
        state,
        project_id=predecessor.project_id,
        predecessor=predecessor,
        correction_deliverable_ids=sorted(correction_deliverable_ids),
    )
    if successor_error is not None:
        result.setdefault("waiting_errors", {})[case.id] = successor_error
        result.setdefault("waiting_case_ids", []).append(case.id)
        return changed

    package_result = _package_result()
    projects_to_recalculate: set[str] = set()
    package_id = (
        "release-"
        + _stable_token(
            "human-correction-successor",
            predecessor.id,
            case.id,
            *sorted(correction_deliverable_ids),
            length=32,
        )
    )
    created_id = _build_release_package(
        state,
        project=state.projects[predecessor.project_id],
        request={
            "id": package_id,
            "project_id": predecessor.project_id,
            "deliverable_ids": sorted(correction_deliverable_ids),
            "supersedes_release_package_id": predecessor.id,
            "summary": (
                f"Automatic successor for {predecessor.id}; resolves "
                f"{len(findings)} human review finding(s)."
            ),
            "verification_waivers": [],
            "waiver_actor_kind": "operator",
            "waiver_actor_id": "system:human-review",
        },
        result=package_result,
        projects_to_recalc=projects_to_recalculate,
        actor_agent_id=(
            project_lead_agent_id(state, predecessor.project_id)
            or state.projects[predecessor.project_id].lead_agent_id
        ),
        team_id=state.projects[predecessor.project_id].team_id,
    )
    if created_id is None or package_result["errors"]:
        result.setdefault("package_errors", {})[case.id] = list(
            package_result["errors"]
        )
        _open_aggregation_blocker(
            state,
            case=case,
            request=request,
            details=(
                "; ".join(str(error) for error in package_result["errors"])
                or "The deterministic successor release package could not be created."
            ),
            result=result,
            now=now,
        )
        return True
    for project_id in sorted(projects_to_recalculate):
        _recalculate_project_status(state, project_id)
    _finish_coordination_parent(parent, now=now)
    result.setdefault("created_successor_release_package_ids", []).append(created_id)
    result.setdefault("events", []).extend(package_result.get("events") or [])
    result.setdefault("git_promotion_event_ids", []).extend(
        package_result.get("git_promotion_event_ids") or []
    )
    return True


class HumanCorrectionReconciliationService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    def reconcile(self) -> dict[str, Any]:
        last_conflict: TeamStateConflictError | None = None
        for _attempt in range(_MAX_RECONCILIATION_ATTEMPTS):
            current = self._repository.read()
            cases = [
                case
                for case in _human_correction_cases(current)
                if _case_needs_reconciliation(current, case)
            ]
            if not cases:
                return {
                    "changed": False,
                    "revision": current.revision,
                    "events": [],
                }
            result: dict[str, Any] = {
                "changed": False,
                "events": [],
                "reconciled_case_ids": [],
            }
            now = utc_now()

            def _mutate(state: Any) -> Any:
                changed = False
                for selected in cases:
                    case = state.cases.get(selected.id)
                    if case is None:
                        continue
                    case_changed = _reconcile_case(
                        state,
                        case=case,
                        result=result,
                        now=now,
                    )
                    if case_changed:
                        result["reconciled_case_ids"].append(case.id)
                    changed = changed or case_changed
                result["changed"] = changed
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
    "HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY",
    "HumanCorrectionReconciliationService",
]
