from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .task_authorship import combined_deliverable_attributions, common_deliverable_author
from ...models import (
    AgentStatus,
    AgentInboxItem,
    AgentTask,
    HumanReviewAssignment,
    HumanReviewDecision,
    HumanReviewFinding,
    HumanReviewFindingTargetScope,
    HumanReviewRequest,
    ReleaseGateFailure,
    ReleasePackage,
    TeamBlockerSourceKind,
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamCase,
    TeamCaseStatus,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
    agent_has_project_assignment,
    project_lead_agent_id,
    utc_now,
)
from .blockers import (
    active_blockers_for_source,
    append_blocker_event,
    create_blocker_inbox,
    handle_blocker_inbox_for_agent,
    open_or_get_blocker,
    resolve_blocker,
)
from .project_status import _recalculate_project_status
from .common import _release_candidate_branch
from .task_assessment import require_task_resolved
from .wake import _append_agent_wake_requested_event
from .work_due import _mark_agent_work_due
from .case_events import append_case_closed_event
from .work_lineage import ensure_task_work_lineage


HUMAN_REVIEW_REQUESTED_EVENT_TYPE = "team.release.human_review_requested"
HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE = "team.release.human_review_preparation_requested"
HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE = "human_review_assignment_unavailable"
HUMAN_REVIEW_TESTING_BLOCKED = "human_review_testing_blocked"
HUMAN_REVIEW_PREPARATION_FAILED = "human_review_preparation_failed"
HUMAN_REVIEW_TARGET_EXPIRED = "human_review_target_expired"
HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY = "_human_review_eligibility_resolver"
HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY = "human_correction_aggregation_task_id"
# Compatibility-only recovery state for findings written before target_scope.
# New validated findings never add an ID to this collection.
HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY = "human_correction_ambiguous_finding_ids"


def human_review_request_for_release(
    state: TeamState,
    release_package_id: str,
) -> HumanReviewRequest | None:
    matches = [
        request
        for request in state.human_review_requests.values()
        if request.release_package_id == release_package_id
    ]
    matches.sort(key=lambda item: (item.created_at, item.id), reverse=True)
    return matches[0] if matches else None


def ensure_human_review_request(
    state: TeamState,
    *,
    release: ReleasePackage,
    result: dict[str, Any],
    now: datetime | None = None,
) -> HumanReviewRequest | None:
    """Create the single candidate-pinned request after all earlier gates pass."""

    manifest = release.manifest
    if manifest is None or manifest.human_review_requirement is None:
        return None
    candidate_sha = release.candidate_commit_sha
    candidate_tree_hash = release.candidate_tree_hash
    if candidate_sha is None or candidate_tree_hash is None:
        raise ValueError("human review requires a pinned candidate SHA and tree hash")
    existing = human_review_request_for_release(state, release.id)
    if existing is not None:
        _require_request_candidate(existing, release)
        return existing

    effective_now = now or utc_now()
    requirement = manifest.human_review_requirement.model_copy(deep=True)
    eligibility_resolver = result.get(HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY)
    eligible_user_ids = (
        sorted(
            {
                str(user_id).strip()
                for user_id in eligibility_resolver(requirement)
                if str(user_id).strip()
            }
        )
        if callable(eligibility_resolver)
        else list(requirement.initially_eligible_user_ids)
    )
    preparation_mode = requirement.preparation_mode
    request_state = (
        "blocked"
        if not eligible_user_ids
        else ("preparing" if preparation_mode == "workflow" else "pending")
    )
    request = HumanReviewRequest(
        id=f"human-review-{_stable_token(release.id, candidate_sha, length=24)}",
        release_package_id=release.id,
        candidate_commit_sha=candidate_sha,
        candidate_tree_hash=candidate_tree_hash,
        requirement_snapshot=requirement,
        current_assignment=HumanReviewAssignment(
            eligible_user_ids=eligible_user_ids,
        ),
        state=request_state,
        created_at=effective_now,
        ready_at=effective_now if request_state == "pending" else None,
    )
    state.human_review_requests[request.id] = request
    result["human_review_request_id"] = request.id
    result.setdefault("created_human_review_request_ids", []).append(request.id)
    if request_state == "blocked":
        _block_human_review(
            state,
            request=request,
            release=release,
            reason_code=HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE,
            summary="Human review has no decision-eligible assignee",
            details=(
                "The configured reviewer groups currently resolve to no eligible "
                "Admin users. Reassign this same request before resuming it."
            ),
            result=result,
            now=effective_now,
        )
    elif request_state == "preparing":
        _append_preparation_event(result, request)
    else:
        _append_review_notifications(result, request)
    return request


def reconcile_human_review_assignment(
    state: TeamState,
    *,
    human_review_request_id: str,
    decision_eligible_user_ids: Sequence[str],
    result: dict[str, Any],
    now: datetime | None = None,
) -> HumanReviewRequest:
    """Reconcile current directory/RBAC eligibility without fabricating a decision."""

    request = _require_request(state, human_review_request_id)
    if request.state in {"approved", "changes_requested", "cancelled"}:
        return request
    effective_now = now or utc_now()
    eligible = sorted(
        {str(user_id).strip() for user_id in decision_eligible_user_ids if str(user_id).strip()}
    )
    if request.current_assignment.eligible_user_ids == eligible:
        return request
    previously_eligible = set(request.current_assignment.eligible_user_ids)
    request.current_assignment.eligible_user_ids = eligible
    request.revision += 1
    release = _require_release_for_request(state, request)
    if not request.current_assignment.eligible_user_ids:
        _block_human_review(
            state,
            request=request,
            release=release,
            reason_code=HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE,
            summary="Human review assignment became unavailable",
            details=(
                "No current assignee remains decision-eligible in the snapshotted "
                "directory. Use audited reassignment on this request."
            ),
            result=result,
            now=effective_now,
        )
    elif request.state == "pending":
        newly_eligible = [user_id for user_id in eligible if user_id not in previously_eligible]
        if newly_eligible:
            _append_review_notifications(
                result,
                request,
                eligible_user_ids=newly_eligible,
            )
    return request


def reassign_human_review(
    state: TeamState,
    *,
    human_review_request_id: str,
    user_ids: Sequence[str],
    assigned_by_user_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> HumanReviewRequest:
    request = _require_request(state, human_review_request_id)
    if request.state in {"approved", "changes_requested", "cancelled"}:
        raise ValueError("terminal human review requests cannot be reassigned")
    normalized_user_ids = sorted(
        {str(user_id).strip() for user_id in user_ids if str(user_id).strip()}
    )
    if not normalized_user_ids:
        raise ValueError("human review reassignment requires at least one eligible user")
    effective_now = now or utc_now()
    request.current_assignment = HumanReviewAssignment(
        eligible_user_ids=normalized_user_ids,
        explicitly_assigned_user_ids=normalized_user_ids,
        assigned_by_user_id=str(assigned_by_user_id).strip(),
        assigned_at=effective_now,
    )
    request.assignment_generation += 1
    request.revision += 1
    release = _require_release_for_request(state, request)
    _resolve_human_review_blockers(
        state,
        request=request,
        reason_codes={HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE},
        note="Human review assignment was restored by an authorized Admin user.",
        reference_ids=normalized_user_ids,
        result=result,
        now=effective_now,
    )
    if active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
        source_id=request.id,
    ):
        request.state = "blocked"
        request.ready_at = None
        release.state = TeamRecordState.BLOCKED
        release.updated_at = effective_now
        _recalculate_project_status(state, release.project_id)
        return request
    if request.requirement_snapshot.preparation_mode == "workflow" and request.test_target is None:
        request.state = "preparing"
        request.preparation_generation += 1
        request.ready_at = None
        _append_preparation_event(result, request)
    else:
        request.state = "pending"
        request.ready_at = effective_now
        _append_review_notifications(result, request)
    release.state = TeamRecordState.PENDING
    release.gate_failure = None
    release.error = None
    release.updated_at = effective_now
    _recalculate_project_status(state, release.project_id)
    return request


def record_human_review_decision(
    state: TeamState,
    *,
    human_review_request_id: str,
    reviewer_user_id: str,
    reviewer_display_name: str,
    decision: str,
    request_id: str,
    expected_revision: int,
    checklist_results: Mapping[str, Any] | None,
    confirmed_finding_ids: Sequence[str] = (),
    summary: str = "",
    evidence_refs: Sequence[str] = (),
    findings: Sequence[Mapping[str, Any]] = (),
    result: dict[str, Any],
    now: datetime | None = None,
) -> HumanReviewDecision:
    """Apply one authenticated decision; callers own session/RBAC confirmation."""

    request = _require_request(state, human_review_request_id)
    normalized_request_id = str(request_id or "").strip()
    existing = next(
        (
            item
            for item in state.human_review_decisions.values()
            if item.human_review_request_id == request.id
            and item.request_id == normalized_request_id
        ),
        None,
    )
    if existing is not None:
        if (
            existing.reviewer_user_id != str(reviewer_user_id).strip()
            or existing.decision != decision
        ):
            raise ValueError("human review request_id was reused with different inputs")
        result["human_review_decision_id"] = existing.id
        result["idempotent_replay"] = True
        return existing
    if request.revision != expected_revision:
        raise ValueError(
            f"stale human review revision: expected {expected_revision}, current {request.revision}"
        )
    if request.state != "pending":
        raise ValueError(f"human review request {request.id} is {request.state}, not pending")
    reviewer_id = str(reviewer_user_id or "").strip()
    if reviewer_id not in request.current_assignment.eligible_user_ids:
        raise PermissionError("reviewer is not a current eligible assignee")
    normalized_decision = str(decision or "").strip()
    if normalized_decision not in {"approved", "changes_requested", "blocked"}:
        raise ValueError("decision must be approved, changes_requested, or blocked")
    if normalized_decision == "approved":
        _validate_checklist(request, checklist_results or {})
    effective_now = now or utc_now()
    release = _require_release_for_request(state, request)
    _require_request_candidate(request, release)
    carried_ids = {
        finding.finding_id
        for finding in (release.manifest.carried_human_findings if release.manifest else [])
    }
    confirmed_ids = sorted(
        {str(finding_id).strip() for finding_id in confirmed_finding_ids if str(finding_id).strip()}
    )
    if normalized_decision == "approved" and set(confirmed_ids) != carried_ids:
        raise ValueError("human approval must explicitly confirm every carried finding")
    if normalized_decision == "changes_requested" and not findings:
        raise ValueError("changes_requested requires at least one actionable finding")
    if normalized_decision == "changes_requested":
        _validate_human_finding_targets(release, findings)
        _validate_human_review_system_record_ids(
            state,
            request=request,
            release=release,
            finding_inputs=findings,
        )

    decision_record = HumanReviewDecision(
        id=f"human-decision-{_stable_token(request.id, normalized_request_id, length=24)}",
        human_review_request_id=request.id,
        reviewer_user_id=reviewer_id,
        reviewer_display_name_snapshot=str(reviewer_display_name or reviewer_id).strip(),
        decision=normalized_decision,
        checklist_results=dict(checklist_results or {}),
        confirmed_finding_ids=confirmed_ids,
        summary=str(summary or "").strip(),
        evidence_refs=list(evidence_refs),
        request_id=normalized_request_id,
        decided_at=effective_now,
    )
    state.human_review_decisions[decision_record.id] = decision_record
    request.revision += 1
    result["human_review_decision_id"] = decision_record.id
    result.setdefault("created_human_review_decision_ids", []).append(decision_record.id)

    if normalized_decision == "blocked":
        _block_human_review(
            state,
            request=request,
            release=release,
            reason_code=HUMAN_REVIEW_TESTING_BLOCKED,
            summary=f"Human testing is blocked for release {release.id}",
            details=decision_record.summary or "The assigned reviewer cannot complete testing.",
            result=result,
            now=effective_now,
        )
    elif normalized_decision == "changes_requested":
        _apply_changes_requested(
            state,
            request=request,
            release=release,
            decision=decision_record,
            finding_inputs=findings,
            result=result,
            now=effective_now,
        )
    else:
        _apply_approval(
            state,
            request=request,
            release=release,
            decision=decision_record,
            result=result,
            now=effective_now,
        )
        from .release_gates import advance_release_gates

        advance_release_gates(
            state,
            release=release,
            result=result,
        )
    _recalculate_project_status(state, release.project_id)
    return decision_record


def resume_human_review(
    state: TeamState,
    *,
    human_review_request_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> HumanReviewRequest:
    request = _require_request(state, human_review_request_id)
    if request.state != "blocked":
        raise ValueError("only a blocked human review request can be resumed")
    if not request.current_assignment.eligible_user_ids:
        raise ValueError("assignment blocker requires audited reassignment before resume")
    effective_now = now or utc_now()
    release = _require_release_for_request(state, request)
    active_blockers = active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
        source_id=request.id,
    )
    if any(
        blocker.reason_code == HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE for blocker in active_blockers
    ):
        raise ValueError("assignment blocker requires audited reassignment before resume")
    _resolve_human_review_blockers(
        state,
        request=request,
        reason_codes=None,
        note="Human review blocker was recovered against the unchanged candidate.",
        reference_ids=[request.id],
        result=result,
        now=effective_now,
    )
    if request.requirement_snapshot.preparation_mode == "workflow" and request.test_target is None:
        request.state = "preparing"
        request.preparation_generation += 1
        request.ready_at = None
        _append_preparation_event(result, request)
    else:
        request.state = "pending"
        request.ready_at = effective_now
        _append_review_notifications(result, request)
    request.revision += 1
    release.state = TeamRecordState.PENDING
    release.gate_failure = None
    release.error = None
    release.updated_at = effective_now
    _recalculate_project_status(state, release.project_id)
    return request


def record_human_review_preparation_result(
    state: TeamState,
    *,
    human_review_request_id: str,
    candidate_commit_sha: str,
    candidate_tree_hash: str,
    preparation_generation: int,
    target: Mapping[str, Any] | None,
    error: str | None,
    result: dict[str, Any],
    now: datetime | None = None,
) -> HumanReviewRequest:
    from ...models import HumanReviewTestTarget

    request = _require_request(state, human_review_request_id)
    if (
        request.candidate_commit_sha != candidate_commit_sha
        or request.candidate_tree_hash != candidate_tree_hash
        or request.preparation_generation != preparation_generation
    ):
        raise ValueError("preparation result does not match the pinned request generation")
    if request.state == "pending" and request.test_target is not None:
        return request
    if request.state != "preparing":
        raise ValueError("human review request is not preparing")
    effective_now = now or utc_now()
    release = _require_release_for_request(state, request)
    if error is not None:
        _block_human_review(
            state,
            request=request,
            release=release,
            reason_code=HUMAN_REVIEW_PREPARATION_FAILED,
            summary=f"Human review preparation failed for release {release.id}",
            details=str(error),
            result=result,
            now=effective_now,
        )
        request.revision += 1
        return request
    safe_target = _safe_test_target_payload(target or {})
    request.test_target = HumanReviewTestTarget.model_validate(safe_target)
    request.state = "pending"
    request.ready_at = effective_now
    request.revision += 1
    release.state = TeamRecordState.PENDING
    release.gate_failure = None
    release.error = None
    release.updated_at = effective_now
    _append_review_notifications(result, request)
    _recalculate_project_status(state, release.project_id)
    return request


def expire_human_review_targets(
    state: TeamState,
    *,
    result: dict[str, Any],
    now: datetime | None = None,
    human_review_request_ids: Sequence[str] | set[str] | None = None,
) -> list[str]:
    effective_now = now or utc_now()
    selected_ids = (
        {
            str(request_id).strip()
            for request_id in human_review_request_ids
            if str(request_id).strip()
        }
        if human_review_request_ids is not None
        else None
    )
    expired: list[str] = []
    for request in state.human_review_requests.values():
        if (
            (selected_ids is None or request.id in selected_ids)
            and request.state == "pending"
            and request.test_target is not None
            and request.test_target.expires_at is not None
            and request.test_target.expires_at <= effective_now
        ):
            release = _require_release_for_request(state, request)
            _block_human_review(
                state,
                request=request,
                release=release,
                reason_code=HUMAN_REVIEW_TARGET_EXPIRED,
                summary=f"Human review target expired for release {release.id}",
                details="Prepare a fresh target for the same pinned candidate.",
                result=result,
                now=effective_now,
            )
            request.test_target = None
            request.revision += 1
            expired.append(request.id)
            _recalculate_project_status(state, release.project_id)
    return expired


def record_human_finding_resolution_proposal(
    state: TeamState,
    *,
    finding_id: str,
    proposed_resolution: str,
    evidence_refs: Sequence[str],
    proposed_by_deliverable_ids: Sequence[str],
) -> HumanReviewFinding:
    finding = state.human_review_findings.get(finding_id)
    if finding is None:
        raise ValueError(f"human review finding {finding_id} does not exist")
    if finding.state != "open":
        raise ValueError("resolved findings cannot receive new proposals")
    deliverable_ids = sorted(
        {
            str(deliverable_id).strip()
            for deliverable_id in proposed_by_deliverable_ids
            if str(deliverable_id).strip()
        }
    )
    if not deliverable_ids:
        raise ValueError("finding resolution proposal requires accepted deliverables")
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if (
            deliverable is None
            or deliverable.task_id != finding.correction_task_id
            or deliverable.state != TeamRecordState.ACCEPTED
            or deliverable.superseded_by_deliverable_id is not None
        ):
            raise ValueError(f"deliverable {deliverable_id} is not a current accepted correction")
    normalized_resolution = str(proposed_resolution or "").strip()
    if not normalized_resolution:
        raise ValueError("proposed_resolution is required")
    normalized_evidence_refs = sorted(
        {str(evidence_ref).strip() for evidence_ref in evidence_refs if str(evidence_ref).strip()}
    )
    if not normalized_evidence_refs:
        raise ValueError("finding resolution proposal requires evidence")
    finding.proposed_resolution = normalized_resolution
    finding.resolution_evidence_refs = normalized_evidence_refs
    finding.proposed_by_deliverable_ids = deliverable_ids
    return finding


def route_human_finding_correction(
    state: TeamState,
    *,
    finding_id: str,
    owner_agent_id: str,
    result: dict[str, Any],
    target_deliverable_ids: Sequence[str] | None = None,
    now: datetime | None = None,
) -> AgentTask:
    """Route a legacy ambiguous finding child without recreating its task."""

    normalized_finding_id = str(finding_id or "").strip()
    finding = state.human_review_findings.get(normalized_finding_id)
    if finding is None:
        raise ValueError(f"human review finding {normalized_finding_id} does not exist")
    if finding.state != "open":
        raise ValueError("resolved findings cannot be routed")
    if finding.proposed_resolution or finding.proposed_by_deliverable_ids:
        raise ValueError("a finding with a resolution proposal cannot be routed")

    source_request = state.human_review_requests.get(finding.source_human_review_request_id)
    release = (
        state.release_packages.get(source_request.release_package_id)
        if source_request is not None
        else None
    )
    if source_request is None or release is None:
        raise ValueError("human review finding has no valid source release")
    if release.state == TeamRecordState.CANCELLED:
        raise ValueError(f"release {release.id} is cancelled")
    current_task = state.tasks.get(finding.correction_task_id)
    case = (
        state.cases.get(current_task.case_id)
        if current_task is not None and current_task.case_id is not None
        else None
    )
    if (
        current_task is None
        or case is None
        or case.source != "human_release_review"
        or case.source_ref != source_request.id
        or case.project_id != release.project_id
    ):
        raise ValueError("human review finding has no valid correction case")
    legacy_conversion = not str(
        case.metadata.get(HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY) or ""
    ).strip()
    ensure_human_correction_aggregation(
        state,
        case=case,
        result=result,
        now=now,
    )
    aggregation_task_id = str(
        case.metadata.get(HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY) or ""
    ).strip()
    aggregation_task = state.tasks.get(aggregation_task_id)
    if (
        aggregation_task is None
        or aggregation_task.case_id != case.id
        or current_task.id == aggregation_task.id
        or current_task.parent_task_id != aggregation_task.id
    ):
        raise ValueError(
            "human review finding is not attached to a valid correction aggregation task"
        )
    ambiguous_finding_ids = {
        str(value).strip()
        for value in (case.metadata.get(HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY) or [])
        if str(value).strip()
    }
    if finding.id not in ambiguous_finding_ids and not legacy_conversion:
        raise ValueError("only a legacy ambiguous human-review finding can be routed")

    project = state.projects.get(release.project_id)
    owner = state.agents.get(str(owner_agent_id or "").strip())
    if project is None or owner is None:
        raise ValueError("correction routing requires a valid project owner")
    if (
        owner.status != AgentStatus.ACTIVE
        or owner.team_id != project.team_id
        or not agent_has_project_assignment(state, owner.id, project.id)
    ):
        raise ValueError(
            f"correction owner {owner.id} is not an active assignee for project {project.id}"
        )
    if target_deliverable_ids is not None:
        normalized_target_ids = sorted(
            {str(value).strip() for value in target_deliverable_ids if str(value).strip()}
        )
        _validate_human_finding_targets(
            release,
            [
                {
                    "text": finding.text,
                    "target_scope": (
                        HumanReviewFindingTargetScope.DELIVERABLES.value
                        if normalized_target_ids
                        else HumanReviewFindingTargetScope.RELEASE_CANDIDATE.value
                    ),
                    "target_deliverable_ids": normalized_target_ids,
                }
            ],
        )
        finding.target_deliverable_ids = normalized_target_ids
    finding.target_scope = (
        HumanReviewFindingTargetScope.DELIVERABLES
        if finding.target_deliverable_ids
        else HumanReviewFindingTargetScope.RELEASE_CANDIDATE
    )

    effective_now = now or utc_now()
    previous_owner_id = current_task.owner_agent_id
    current_task.owner_agent_id = owner.id
    current_task.updated_at = effective_now
    case.status = TeamCaseStatus.IN_PROGRESS
    case.updated_at = effective_now
    ambiguous_finding_ids.discard(finding.id)
    case.metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] = sorted(ambiguous_finding_ids)
    for item in state.inbox.values():
        if (
            item.task_id == current_task.id
            and item.recipient_agent_id == previous_owner_id
            and item.state == TeamInboxItemState.PENDING
        ):
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = effective_now
            result.setdefault("handled_inbox_item_ids", []).append(item.id)
    _ensure_correction_inbox(
        state,
        request_key=(f"{source_request.id}:route:{finding.id}:{current_task.id}"),
        owner_agent_id=owner.id,
        task=current_task,
        case=case,
        result=result,
        now=effective_now,
        finding_id=finding.id,
        aggregation_task_id=aggregation_task.id,
    )
    result["human_review_finding_id"] = finding.id
    result["correction_task_id"] = current_task.id
    result["previous_owner_agent_id"] = previous_owner_id
    result["owner_agent_id"] = owner.id
    _recalculate_project_status(state, project.id)
    return current_task


def ensure_human_correction_aggregation(
    state: TeamState,
    *,
    case: TeamCase,
    result: dict[str, Any],
    now: datetime | None = None,
) -> AgentTask:
    """Ensure the deterministic coordination parent for a correction case."""

    if case.source != "human_release_review" or not case.source_ref:
        raise ValueError("correction aggregation requires a human-review case")
    request = state.human_review_requests.get(case.source_ref)
    release = (
        state.release_packages.get(request.release_package_id) if request is not None else None
    )
    if request is None or release is None or release.project_id != case.project_id:
        raise ValueError("human-review correction case has invalid release lineage")
    project = state.projects.get(release.project_id)
    if project is None:
        raise ValueError("human-review correction case has no valid project")
    lead_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    deterministic_task_id = f"human-correction-aggregation-{_stable_token(request.id, length=24)}"
    configured_task_id = str(
        case.metadata.get(HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY) or ""
    ).strip()
    case_findings = sorted(
        (
            finding
            for finding in state.human_review_findings.values()
            if finding.source_human_review_request_id == request.id and finding.state == "open"
        ),
        key=lambda finding: (finding.created_at, finding.id),
    )
    if not configured_task_id:
        correction_task_ids = [finding.correction_task_id for finding in case_findings]
        if len(set(correction_task_ids)) != len(correction_task_ids):
            raise ValueError(
                "legacy human-review findings share one correction task and "
                "cannot be converted automatically"
            )
        if len(case_findings) > 1:
            legacy_ambiguous_ids = {
                str(value).strip()
                for value in (case.metadata.get(HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY) or [])
                if str(value).strip()
            }
            legacy_ambiguous_ids.update(finding.id for finding in case_findings)
            case.metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] = sorted(legacy_ambiguous_ids)
    aggregation_task_id = configured_task_id or deterministic_task_id
    aggregation_task = state.tasks.get(aggregation_task_id)
    effective_now = now or utc_now()
    changed = False
    if aggregation_task is None:
        if configured_task_id:
            raise ValueError("human-review case references a missing correction aggregation task")
        aggregation_task = _ensure_system_task(
            state,
            task_id=deterministic_task_id,
            project_id=project.id,
            case=case,
            owner_agent_id=lead_id,
            title=f"Coordinate human review corrections for {release.id}",
            description=(
                "Track the correction findings for this rejected release "
                "candidate. Child tasks contain the actionable work."
            ),
            parent_task_id=None,
            originating_task_id=None,
            authored_by=common_deliverable_author(state, release.deliverable_ids),
            work_item_attributions=combined_deliverable_attributions(state, release.deliverable_ids),
            release_base_package_id=None,
            git_base_branch=(_release_candidate_branch(release) or release.git_base_branch),
            now=effective_now,
            task_kind=TeamTaskKind.COORDINATION,
        )
        result.setdefault("created_task_ids", []).append(aggregation_task.id)
        changed = True
    elif (
        aggregation_task.case_id != case.id
        or aggregation_task.project_id != project.id
        or aggregation_task.parent_task_id is not None
    ):
        raise ValueError("correction aggregation task has invalid case lineage")
    if _upgrade_system_human_review_coordination_task(
        aggregation_task,
        now=effective_now,
    ):
        result.setdefault("migrated_coordination_task_ids", []).append(aggregation_task.id)
        changed = True
    if case.metadata.get(HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY) != aggregation_task.id:
        case.metadata[HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY] = aggregation_task.id
        changed = True
    if case.metadata.get("human_correction_graph_version") != 2:
        case.metadata["human_correction_graph_version"] = 2
        changed = True
    for finding in case_findings:
        correction_task = state.tasks.get(finding.correction_task_id)
        if correction_task is None or correction_task.id == aggregation_task.id:
            continue
        if correction_task.case_id != case.id:
            raise ValueError(f"finding {finding.id} correction task belongs to another case")
        if correction_task.parent_task_id != aggregation_task.id:
            correction_task.parent_task_id = aggregation_task.id
            correction_task.updated_at = effective_now
            result.setdefault("reparented_correction_task_ids", []).append(correction_task.id)
            changed = True
    if changed:
        case.updated_at = effective_now
    result["correction_aggregation_task_id"] = aggregation_task.id
    return aggregation_task


def _upgrade_system_human_review_coordination_task(
    task: AgentTask,
    *,
    now: datetime,
) -> bool:
    """Upgrade a deterministic pre-coordination correction parent in place."""

    if task.task_kind == TeamTaskKind.COORDINATION:
        return False
    if task.created_by_actor != "system:human-review" or task.task_kind != TeamTaskKind.SUPPORT:
        raise ValueError("correction aggregation task is not a system-created support task")
    incompatible_reasons: list[str] = []
    if task.integration_boundary:
        incompatible_reasons.append("integration boundary")
    if task.deliverable_contract is not None:
        incompatible_reasons.append("deliverable contract")
    if task.outcome_contract is not None or task.completion is not None:
        incompatible_reasons.append("outcome contract")
    if task.git_base_commit_sha is not None:
        incompatible_reasons.append("Git base commit pin")
    if task.deliverable_ids:
        incompatible_reasons.append("deliverables")
    if task.state == TeamTaskState.ACCEPTED:
        incompatible_reasons.append("accepted state")
    if incompatible_reasons:
        raise ValueError(
            f"legacy correction aggregation task {task.id} cannot be upgraded to "
            "coordination because it has " + ", ".join(incompatible_reasons)
        )
    task.task_kind = TeamTaskKind.COORDINATION
    task.updated_at = now
    return True


def close_post_release_feedback_case(
    state: TeamState,
    *,
    case_id: str,
    reason: str,
    evidence_refs: Sequence[str],
    actor_kind: str,
    actor_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> TeamCase:
    """Close a post-release report without mutating its published release."""

    normalized_case_id = str(case_id or "").strip()
    case = state.cases.get(normalized_case_id)
    if case is None or case.source != "post_release_feedback":
        raise ValueError(f"case {normalized_case_id} is not a post-release feedback case")
    if case.status in {TeamCaseStatus.RESOLVED, TeamCaseStatus.CLOSED}:
        raise ValueError(f"post-release feedback case {case.id} is already {case.status.value}")
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("post-release case closure reason is required")
    normalized_evidence_refs = sorted(
        {str(evidence_ref).strip() for evidence_ref in evidence_refs if str(evidence_ref).strip()}
    )
    if not normalized_evidence_refs:
        raise ValueError("post-release case closure requires evidence")
    normalized_actor_kind = str(actor_kind or "").strip()
    normalized_actor_id = str(actor_id or "").strip()
    if not normalized_actor_kind or not normalized_actor_id:
        raise ValueError("post-release case closure requires an actor")
    for task_id in case.task_ids:
        require_task_resolved(
            state,
            task_id,
            boundary=f"closing post-release feedback case {case.id}",
        )

    effective_now = now or utc_now()
    previous_status = case.status
    case.metadata = {
        **case.metadata,
        "post_release_feedback_resolution": {
            "reason": normalized_reason,
            "evidence_refs": normalized_evidence_refs,
            "actor_kind": normalized_actor_kind,
            "actor_id": normalized_actor_id,
            "closed_at": effective_now.isoformat(),
        },
    }
    case.status = TeamCaseStatus.CLOSED
    case.closed_at = effective_now
    case.updated_at = effective_now
    result["case_id"] = case.id
    result["closed_case_ids"] = [case.id]
    result["resolution_evidence_refs"] = normalized_evidence_refs
    append_case_closed_event(result.setdefault("events", []), case, previous_status=previous_status)
    if case.project_id is not None:
        _recalculate_project_status(state, case.project_id)
    return case


def report_post_release_feedback(
    state: TeamState,
    *,
    release_package_id: str,
    reporter_user_id: str,
    reporter_display_name: str,
    report: str,
    evidence_refs: Sequence[str],
    request_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> TeamCase:
    release = state.release_packages.get(release_package_id)
    if (
        release is None
        or release.state != TeamRecordState.ACCEPTED
        or release.published_at is None
        or release.published_commit_sha is None
        or release.candidate_commit_sha is None
        or release.approved_commit_sha != release.candidate_commit_sha
        or release.published_commit_sha != release.candidate_commit_sha
    ):
        raise ValueError("post-release feedback requires a published release")
    normalized_report = str(report or "").strip()
    if not normalized_report:
        raise ValueError("post-release report is required")
    normalized_evidence_refs = sorted(
        {str(evidence_ref).strip() for evidence_ref in evidence_refs if str(evidence_ref).strip()}
    )
    if not normalized_evidence_refs:
        raise ValueError("post-release feedback requires evidence")
    source_ref = f"{release.id}:{str(request_id).strip()}"
    existing = next(
        (
            case
            for case in state.cases.values()
            if case.source == "post_release_feedback" and case.source_ref == source_ref
        ),
        None,
    )
    if existing is not None:
        return existing
    project = state.projects[release.project_id]
    lead_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    effective_now = now or utc_now()
    case_id = f"post-release-case-{_stable_token(source_ref, length=24)}"
    task_id = f"post-release-triage-{_stable_token(source_ref, length=24)}"
    _require_system_record_id_available(
        state.cases,
        record_id=case_id,
        record_kind="case",
    )
    _require_system_record_id_available(
        state.tasks,
        record_id=task_id,
        record_kind="task",
    )
    case = TeamCase(
        id=case_id,
        team_id=project.team_id,
        project_id=project.id,
        title=f"Post-release feedback for {release.id}",
        description=normalized_report,
        status=TeamCaseStatus.TRIAGE,
        priority=TeamInboxPriority.HIGH,
        owner_agent_id=lead_id,
        source="post_release_feedback",
        source_ref=source_ref,
        metadata={
            "reporter_user_id": str(reporter_user_id).strip(),
            "reporter_display_name": str(reporter_display_name).strip(),
            "release_package_id": release.id,
            "evidence_refs": normalized_evidence_refs,
        },
        created_at=effective_now,
        updated_at=effective_now,
    )
    state.cases[case.id] = case
    task = _ensure_system_task(
        state,
        task_id=task_id,
        project_id=project.id,
        case=case,
        owner_agent_id=lead_id,
        title=f"Triage post-release feedback for {release.id}",
        description=case.description,
        parent_task_id=None,
        originating_task_id=None,
        authored_by=common_deliverable_author(state, release.deliverable_ids),
        work_item_attributions=combined_deliverable_attributions(state, release.deliverable_ids),
        release_base_package_id=None,
        git_base_branch=(_release_candidate_branch(release) or release.git_base_branch),
        now=effective_now,
    )
    _ensure_correction_inbox(
        state,
        request_key=source_ref,
        owner_agent_id=lead_id,
        task=task,
        case=case,
        result=result,
        now=effective_now,
    )
    result["case_id"] = case.id
    result["triage_task_id"] = task.id
    _recalculate_project_status(state, project.id)
    return case


def _apply_approval(
    state: TeamState,
    *,
    request: HumanReviewRequest,
    release: ReleasePackage,
    decision: HumanReviewDecision,
    result: dict[str, Any],
    now: datetime,
) -> None:
    carried_ids = {
        finding.finding_id
        for finding in (release.manifest.carried_human_findings if release.manifest else [])
    }
    for finding_id in carried_ids:
        finding = state.human_review_findings.get(finding_id)
        if finding is None or finding.state != "open":
            raise ValueError(f"carried finding {finding_id} is unavailable")
        finding.state = "resolved"
        finding.resolved_by_human_review_request_id = request.id
        finding.resolved_at = now
        result.setdefault("resolved_human_finding_ids", []).append(finding.id)
    request.state = "approved"
    request.completed_at = now
    release.gate_failure = None
    release.error = None
    release.updated_at = now
    result["human_review_approved"] = True


def _apply_changes_requested(
    state: TeamState,
    *,
    request: HumanReviewRequest,
    release: ReleasePackage,
    decision: HumanReviewDecision,
    finding_inputs: Sequence[Mapping[str, Any]],
    result: dict[str, Any],
    now: datetime,
) -> None:
    if release.approved_commit_sha is not None or release.published_commit_sha is not None:
        raise ValueError("published or already approved releases cannot request changes")
    project = state.projects[release.project_id]
    lead_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    case_id = f"human-review-case-{_stable_token(request.id, length=24)}"
    case = state.cases.get(case_id)
    if case is not None:
        raise ValueError(f"reserved human-review case id {case_id} already exists")
    case = TeamCase(
        id=case_id,
        team_id=project.team_id,
        project_id=project.id,
        title=f"Human review corrections for {release.id}",
        description=decision.summary,
        status=TeamCaseStatus.IN_PROGRESS,
        priority=TeamInboxPriority.HIGH,
        owner_agent_id=lead_id,
        source="human_release_review",
        source_ref=request.id,
        created_at=now,
        updated_at=now,
    )
    state.cases[case.id] = case
    result.setdefault("created_case_ids", []).append(case.id)

    aggregation_task = ensure_human_correction_aggregation(
        state,
        case=case,
        result=result,
        now=now,
    )
    wake_owner_ids: set[str] = set()
    ambiguous_finding_ids: set[str] = set()
    for position, raw in enumerate(finding_inputs):
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError(f"human finding {position + 1} requires actionable text")
        target_ids = sorted(
            {
                str(value).strip()
                for value in (raw.get("target_deliverable_ids") or [])
                if str(value).strip()
            }
        )
        evidence_refs = sorted(
            {str(value).strip() for value in (raw.get("evidence_refs") or []) if str(value).strip()}
        )
        target_scope = _human_finding_target_scope(
            raw,
            target_deliverable_ids=target_ids,
        )
        (
            target_ids,
            origin_task_id,
            owner_id,
            is_ambiguous,
        ) = _human_finding_route(
            state,
            release=release,
            target_scope=target_scope,
            target_deliverable_ids=target_ids,
        )
        finding_id = f"human-finding-{_stable_token(request.id, str(position), text, length=24)}"
        correction_task = _ensure_system_task(
            state,
            task_id=f"human-correction-{_stable_token(finding_id, length=24)}",
            project_id=project.id,
            case=case,
            owner_agent_id=owner_id,
            title=text,
            description=text,
            parent_task_id=aggregation_task.id,
            originating_task_id=origin_task_id,
            authored_by=common_deliverable_author(state, target_ids or release.deliverable_ids),
            work_item_attributions=combined_deliverable_attributions(state, target_ids or release.deliverable_ids),
            release_base_package_id=release.id,
            git_base_branch=(_release_candidate_branch(release) or release.git_base_branch),
            now=now,
        )
        result.setdefault("created_task_ids", []).append(correction_task.id)
        if is_ambiguous:
            ambiguous_finding_ids.add(finding_id)
        finding = state.human_review_findings.get(finding_id)
        if finding is None:
            finding = HumanReviewFinding(
                id=finding_id,
                source_human_review_request_id=request.id,
                text=text,
                target_scope=target_scope,
                target_deliverable_ids=target_ids,
                evidence_refs=evidence_refs,
                correction_task_id=correction_task.id,
                created_at=now,
            )
            state.human_review_findings[finding.id] = finding
            result.setdefault("created_human_review_finding_ids", []).append(finding.id)
        _ensure_correction_inbox(
            state,
            request_key=request.id,
            owner_agent_id=correction_task.owner_agent_id,
            task=correction_task,
            case=case,
            result=result,
            now=now,
            finding_id=finding.id,
            aggregation_task_id=aggregation_task.id,
            request_wake=False,
        )
        wake_owner_ids.add(correction_task.owner_agent_id)

    case.metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] = sorted(ambiguous_finding_ids)
    for owner_id in sorted(wake_owner_ids):
        _append_agent_wake_requested_event(
            state,
            agent_id=owner_id,
            result=result,
            reason="inbox",
            now=now,
            delay_seconds=0,
            idempotency_suffix=f"human-review:{request.id}:corrections",
        )

    request.state = "changes_requested"
    request.completed_at = now
    release.state = TeamRecordState.REJECTED
    release.gate_failure = ReleaseGateFailure(
        stage="human_review",
        kind="changes_requested",
        source_id=request.id,
        message=decision.summary or "Human review requested changes.",
    )
    release.error = release.gate_failure.message
    release.updated_at = now
    result["human_review_changes_requested"] = True
    result["case_id"] = case.id


def _validate_human_finding_targets(
    release: ReleasePackage,
    finding_inputs: Sequence[Mapping[str, Any]],
) -> None:
    candidate_deliverable_ids = _reviewed_candidate_deliverable_ids(release)
    for position, raw in enumerate(finding_inputs):
        if not isinstance(raw, Mapping):
            raise ValueError(f"human finding {position + 1} must be an object")
        raw_target_ids = raw.get("target_deliverable_ids", [])
        if raw_target_ids is None:
            raw_target_ids = []
        if isinstance(raw_target_ids, (str, bytes)) or not isinstance(raw_target_ids, Sequence):
            raise ValueError(
                f"human finding {position + 1} target_deliverable_ids must be an array"
            )
        target_ids = {str(value).strip() for value in raw_target_ids if str(value).strip()}
        target_scope = _human_finding_target_scope(
            raw,
            target_deliverable_ids=sorted(target_ids),
        )
        if target_scope == HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED:
            raise ValueError(
                f"human finding {position + 1} must select the entire release "
                "candidate or at least one affected deliverable"
            )
        if target_scope == HumanReviewFindingTargetScope.RELEASE_CANDIDATE and target_ids:
            raise ValueError(
                f"human finding {position + 1} cannot select both the entire "
                "release candidate and specific deliverables"
            )
        if target_scope == HumanReviewFindingTargetScope.DELIVERABLES and not target_ids:
            raise ValueError(
                f"human finding {position + 1} deliverable scope requires at "
                "least one affected deliverable"
            )
        invalid_target_ids = sorted(target_ids - candidate_deliverable_ids)
        if invalid_target_ids:
            raise ValueError(
                f"human finding {position + 1} targets deliverables outside the "
                "reviewed release candidate: " + ", ".join(invalid_target_ids)
            )


def _require_system_record_id_available(
    records: Mapping[str, Any],
    *,
    record_id: str,
    record_kind: str,
) -> None:
    if record_id in records:
        raise ValueError(f"reserved human-review {record_kind} id {record_id} already exists")


def _validate_human_review_system_record_ids(
    state: TeamState,
    *,
    request: HumanReviewRequest,
    release: ReleasePackage,
    finding_inputs: Sequence[Mapping[str, Any]],
) -> None:
    case_id = f"human-review-case-{_stable_token(request.id, length=24)}"
    _require_system_record_id_available(
        state.cases,
        record_id=case_id,
        record_kind="case",
    )
    aggregation_task_id = f"human-correction-aggregation-{_stable_token(request.id, length=24)}"
    _require_system_record_id_available(
        state.tasks,
        record_id=aggregation_task_id,
        record_kind="task",
    )
    for position, raw in enumerate(finding_inputs):
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError(f"human finding {position + 1} requires actionable text")
        finding_id = f"human-finding-{_stable_token(request.id, str(position), text, length=24)}"
        task_id = f"human-correction-{_stable_token(finding_id, length=24)}"
        _require_system_record_id_available(
            state.tasks,
            record_id=task_id,
            record_kind="task",
        )


def _reviewed_candidate_deliverable_ids(
    release: ReleasePackage,
) -> set[str]:
    manifest = release.manifest
    if manifest is None:
        return set()
    candidate_ids = {evidence.deliverable_id for evidence in manifest.deliverables}
    pending_ids = list(candidate_ids)
    while pending_ids:
        deliverable_id = pending_ids.pop()
        for source_id in manifest.contribution_graph.get(deliverable_id, []):
            if source_id not in candidate_ids:
                candidate_ids.add(source_id)
                pending_ids.append(source_id)
    return candidate_ids


def _human_finding_target_scope(
    raw: Mapping[str, Any],
    *,
    target_deliverable_ids: Sequence[str],
) -> HumanReviewFindingTargetScope:
    raw_scope = str(raw.get("target_scope") or "").strip()
    if not raw_scope:
        return (
            HumanReviewFindingTargetScope.DELIVERABLES
            if target_deliverable_ids
            else HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED
        )
    try:
        return HumanReviewFindingTargetScope(raw_scope)
    except ValueError as exc:
        raise ValueError(
            "human finding target_scope must be release_candidate or deliverables"
        ) from exc


def _human_finding_route(
    state: TeamState,
    *,
    release: ReleasePackage,
    target_scope: HumanReviewFindingTargetScope,
    target_deliverable_ids: Sequence[str],
) -> tuple[list[str], str | None, str, bool]:
    project = state.projects[release.project_id]
    lead_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    normalized_target_ids = sorted(
        {str(value).strip() for value in target_deliverable_ids if str(value).strip()}
    )
    if target_scope == HumanReviewFindingTargetScope.RELEASE_CANDIDATE:
        return [], None, lead_id, False
    manifest = release.manifest
    if not normalized_target_ids and manifest is not None and len(manifest.deliverables) == 1:
        normalized_target_ids = [manifest.deliverables[0].deliverable_id]

    route_deliverable_ids = (
        normalized_target_ids
        if normalized_target_ids
        else sorted(_reviewed_candidate_deliverable_ids(release))
    )
    if not route_deliverable_ids:
        return (
            normalized_target_ids,
            None,
            lead_id,
            target_scope == HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED,
        )
    tasks: list[AgentTask] = []
    for deliverable_id in route_deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
        if deliverable is None or task is None or task.project_id != release.project_id:
            return (
                normalized_target_ids,
                None,
                lead_id,
                target_scope == HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED,
            )
        tasks.append(task)
    owner_ids = {task.owner_agent_id for task in tasks}
    origin_task_ids = {task.id for task in tasks}
    origin_task_id = next(iter(origin_task_ids)) if len(origin_task_ids) == 1 else None
    if len(owner_ids) != 1:
        return (
            normalized_target_ids,
            origin_task_id,
            lead_id,
            target_scope == HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED,
        )
    owner_id = next(iter(owner_ids))
    owner = state.agents.get(owner_id)
    if (
        owner is None
        or owner.status != AgentStatus.ACTIVE
        or owner.team_id != project.team_id
        or not agent_has_project_assignment(state, owner.id, project.id)
    ):
        return (
            normalized_target_ids,
            origin_task_id,
            lead_id,
            target_scope == HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED,
        )
    return normalized_target_ids, origin_task_id, owner.id, False


def _ensure_system_task(
    state: TeamState,
    *,
    task_id: str,
    project_id: str,
    case: TeamCase,
    owner_agent_id: str,
    title: str,
    description: str,
    parent_task_id: str | None,
    originating_task_id: str | None,
    release_base_package_id: str | None,
    git_base_branch: str | None,
    now: datetime,
    authored_by: object = None,
    work_item_attributions: object = None,
    task_kind: TeamTaskKind = TeamTaskKind.SUPPORT,
) -> AgentTask:
    existing = state.tasks.get(task_id)
    if existing is not None:
        raise ValueError(f"reserved human-review task id {task_id} already exists")
    task = AgentTask(
        id=task_id,
        owner_agent_id=owner_agent_id,
        project_id=project_id,
        case_id=case.id,
        priority=0,
        state=TeamTaskState.PENDING,
        task_kind=task_kind,
        title=title,
        description=description,
        created_by_actor="system:human-review",
        parent_task_id=parent_task_id,
        originating_task_id=originating_task_id,
        authored_by=authored_by,
        work_item_attributions=work_item_attributions,
        release_base_package_id=release_base_package_id,
        git_base_branch=git_base_branch,
        created_at=now,
        updated_at=now,
        state_entered_at=now,
    )
    state.tasks[task.id] = task
    ensure_task_work_lineage(state, task)
    if task.id not in case.task_ids:
        case.task_ids.append(task.id)
        case.updated_at = now
    return task


def _ensure_correction_inbox(
    state: TeamState,
    *,
    request_key: str,
    owner_agent_id: str,
    task: AgentTask,
    case: TeamCase,
    result: dict[str, Any],
    now: datetime,
    finding_id: str | None = None,
    aggregation_task_id: str | None = None,
    request_wake: bool = True,
) -> AgentInboxItem:
    inbox_id = (
        f"inbox-human-review-{_stable_token(request_key, owner_agent_id, task.id, length=24)}"
    )
    existing = state.inbox.get(inbox_id)
    if existing is not None:
        return existing
    sender_id = case.owner_agent_id or owner_agent_id
    item = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=owner_agent_id,
        sender_agent_id=sender_id,
        task_id=task.id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=True,
        priority=TeamInboxPriority.HIGH,
        payload={
            "kind": "human_review_correction_work",
            "human_review_request_id": request_key,
            "case_id": case.id,
            "task_id": task.id,
            **({"human_review_finding_id": finding_id} if finding_id is not None else {}),
            **(
                {"correction_aggregation_task_id": aggregation_task_id}
                if aggregation_task_id is not None
                else {}
            ),
        },
        state=TeamInboxItemState.PENDING,
        created_at=now,
        updated_at=now,
    )
    state.inbox[item.id] = item
    _mark_agent_work_due(state, agent_id=owner_agent_id, now=now)
    if request_wake:
        _append_agent_wake_requested_event(
            state,
            agent_id=owner_agent_id,
            result=result,
            reason="inbox",
            now=now,
            delay_seconds=0,
            idempotency_suffix=f"human-review:{request_key}:{task.id}",
        )
    result.setdefault("created_inbox_item_ids", []).append(item.id)
    return item


def _resolve_human_review_blockers(
    state: TeamState,
    *,
    request: HumanReviewRequest,
    reason_codes: set[str] | None,
    note: str,
    reference_ids: Sequence[str],
    result: dict[str, Any],
    now: datetime,
) -> None:
    for blocker in active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
        source_id=request.id,
    ):
        if reason_codes is not None and blocker.reason_code not in reason_codes:
            continue
        resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=None,
            dispatch_id=None,
            note=note,
            reference_ids=reference_ids,
            metadata={
                "recovery_permission": "admin.owner",
                "human_review_request_id": request.id,
            },
            now=now,
        )
        result.setdefault("resolved_blocker_ids", []).append(blocker.id)


def _block_human_review(
    state: TeamState,
    *,
    request: HumanReviewRequest,
    release: ReleasePackage,
    reason_code: str,
    summary: str,
    details: str,
    result: dict[str, Any],
    now: datetime,
) -> None:
    project = state.projects[release.project_id]
    lead_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    blocker, _ = open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
        source_id=request.id,
        summary=summary,
        details=details,
        reason_code=reason_code,
        priority=TeamInboxPriority.HIGH,
        reporter_actor="system:human-review",
        initial_owner_agent_id=lead_id,
        team_id=project.team_id,
        project_id=project.id,
        result=result,
        now=now,
    )
    if (
        reason_code == HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE
        and blocker.route != TeamBlockerRoute.OPERATOR
    ):
        previous_owner_id = blocker.owner_agent_id
        blocker.route = TeamBlockerRoute.OPERATOR
        blocker.delegate_agent_id = None
        blocker.active_dispatch_id = None
        blocker.follow_up_at = None
        blocker.updated_at = now
        if previous_owner_id is not None:
            handle_blocker_inbox_for_agent(
                state,
                blocker_id=blocker.id,
                agent_id=previous_owner_id,
                now=now,
            )
            create_blocker_inbox(
                state,
                blocker=blocker,
                recipient_agent_id=previous_owner_id,
                sender_agent_id=None,
                kind="human_review_operator_recovery_visibility",
                result=result,
                now=now,
            )
        append_blocker_event(
            state,
            blocker=blocker,
            event_type=TeamBlockerEventType.TERMINAL_ROUTED,
            from_agent_id=previous_owner_id,
            note=(
                "Human-review assignment recovery is accountable to an "
                "authorized Admin owner; the project lead retains visibility."
            ),
            metadata={
                "recovery_permission": "admin.owner",
                "recovery_directory_path": (request.requirement_snapshot.directory_path),
                "visibility_agent_id": previous_owner_id,
            },
            now=now,
        )
        result["human_review_recovery_permission"] = "admin.owner"
        result["human_review_recovery_directory_path"] = request.requirement_snapshot.directory_path
    request.state = "blocked"
    request.ready_at = None
    release.state = TeamRecordState.BLOCKED
    release.gate_failure = ReleaseGateFailure(
        stage="human_review",
        kind="blocked",
        source_id=request.id,
        message=details,
    )
    release.error = details
    release.updated_at = now
    result["error"] = details
    result["human_review_blocker_id"] = blocker.id


def _validate_checklist(
    request: HumanReviewRequest,
    checklist_results: Mapping[str, Any],
) -> None:
    required_ids = {item.id for item in request.requirement_snapshot.checklist if item.required}
    missing = sorted(
        item_id for item_id in required_ids if checklist_results.get(item_id) is not True
    )
    if missing:
        raise ValueError(
            "required human review checklist items are incomplete: " + ", ".join(missing)
        )


def _safe_test_target_payload(target: Mapping[str, Any]) -> dict[str, Any]:
    forbidden_tokens = ("secret", "password", "token", "credential", "cookie", "authorization")
    forbidden = sorted(
        key for key in target if any(token in str(key).casefold() for token in forbidden_tokens)
    )
    if forbidden:
        raise ValueError(
            "human review target contains secret-bearing fields: " + ", ".join(forbidden)
        )
    raw_url = target.get("url")
    if raw_url is not None:
        parsed_url = urlsplit(str(raw_url).strip())
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError("human review target URL must not contain userinfo credentials")
        secret_query_names = sorted(
            {
                name
                for name, _value in (
                    parse_qsl(parsed_url.query, keep_blank_values=True)
                    + parse_qsl(parsed_url.fragment, keep_blank_values=True)
                )
                if _secret_bearing_url_parameter(name)
            }
        )
        if secret_query_names:
            raise ValueError(
                "human review target URL contains secret-bearing parameters: "
                + ", ".join(secret_query_names)
            )
    allowed = {
        "url",
        "alias",
        "expires_at",
        "instructions",
        "evidence_refs",
    }
    extra = sorted(set(target) - allowed)
    if extra:
        raise ValueError("unsupported human review target fields: " + ", ".join(extra))
    return dict(target)


def _secret_bearing_url_parameter(name: str) -> bool:
    normalized = str(name).strip().casefold().replace("-", "_").replace(".", "_")
    if normalized in {
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "cookie",
        "credential",
        "jwt",
        "key",
        "password",
        "passwd",
        "secret",
        "session",
        "session_id",
        "sig",
        "signature",
        "signed",
        "token",
    }:
        return True
    return normalized.endswith(
        (
            "_authorization",
            "_credential",
            "_key",
            "_password",
            "_secret",
            "_signature",
            "_token",
        )
    )


def _append_review_notifications(
    result: dict[str, Any],
    request: HumanReviewRequest,
    *,
    eligible_user_ids: Sequence[str] | None = None,
) -> None:
    for user_id in (
        request.current_assignment.eligible_user_ids
        if eligible_user_ids is None
        else eligible_user_ids
    ):
        result.setdefault("events", []).append(
            {
                "event_type": HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
                "correlation_key": request.id,
                "idempotency_key": (
                    f"team-addon:{HUMAN_REVIEW_REQUESTED_EVENT_TYPE}:"
                    f"{request.id}:{user_id}:{request.assignment_generation}"
                ),
                "payload": {
                    "human_review_request_id": request.id,
                    "release_package_id": request.release_package_id,
                    "candidate_commit_sha": request.candidate_commit_sha,
                    "candidate_tree_hash": request.candidate_tree_hash,
                    "recipient_user_id": user_id,
                    "assignment_generation": request.assignment_generation,
                },
            }
        )


def _append_preparation_event(
    result: dict[str, Any],
    request: HumanReviewRequest,
) -> None:
    result.setdefault("events", []).append(
        {
            "event_type": HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE,
            "correlation_key": request.id,
            "idempotency_key": (
                f"team-addon:{HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE}:"
                f"{request.id}:{request.preparation_generation}"
            ),
            "payload": {
                "human_review_request_id": request.id,
                "release_package_id": request.release_package_id,
                "candidate_commit_sha": request.candidate_commit_sha,
                "candidate_tree_hash": request.candidate_tree_hash,
                "preparation_generation": request.preparation_generation,
            },
        }
    )


def _require_request(state: TeamState, request_id: str) -> HumanReviewRequest:
    request = state.human_review_requests.get(str(request_id or "").strip())
    if request is None:
        raise ValueError(f"human review request {request_id} does not exist")
    return request


def _require_release_for_request(
    state: TeamState,
    request: HumanReviewRequest,
) -> ReleasePackage:
    release = state.release_packages.get(request.release_package_id)
    if release is None:
        raise ValueError(f"human review request {request.id} references a missing release")
    if release.state == TeamRecordState.CANCELLED:
        raise ValueError(f"release {release.id} is cancelled")
    return release


def _require_request_candidate(
    request: HumanReviewRequest,
    release: ReleasePackage,
) -> None:
    if (
        release.candidate_commit_sha != request.candidate_commit_sha
        or release.candidate_tree_hash != request.candidate_tree_hash
    ):
        raise ValueError("human review request no longer matches its pinned candidate")


def _stable_token(*values: str, length: int = 32) -> str:
    raw = "\x00".join(values).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


__all__ = [
    "HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY",
    "HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY",
    "HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE",
    "HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY",
    "HUMAN_REVIEW_PREPARATION_FAILED",
    "HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE",
    "HUMAN_REVIEW_REQUESTED_EVENT_TYPE",
    "HUMAN_REVIEW_TARGET_EXPIRED",
    "HUMAN_REVIEW_TESTING_BLOCKED",
    "close_post_release_feedback_case",
    "ensure_human_correction_aggregation",
    "ensure_human_review_request",
    "expire_human_review_targets",
    "human_review_request_for_release",
    "reassign_human_review",
    "reconcile_human_review_assignment",
    "record_human_finding_resolution_proposal",
    "record_human_review_decision",
    "record_human_review_preparation_result",
    "report_post_release_feedback",
    "route_human_finding_correction",
    "resume_human_review",
]
