from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ...models import (
    AgentInboxItem,
    AgentTask,
    HumanReviewAssignment,
    HumanReviewDecision,
    HumanReviewFinding,
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
    handle_blocker_inbox_for_agent,
    open_or_get_blocker,
    resolve_blocker,
)
from .project_status import _recalculate_project_status
from .task_assessment import require_task_resolved
from .wake import _append_agent_wake_requested_event
from .work_due import _mark_agent_work_due


HUMAN_REVIEW_REQUESTED_EVENT_TYPE = "team.release.human_review_requested"
HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE = (
    "team.release.human_review_preparation_requested"
)
HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE = "human_review_assignment_unavailable"
HUMAN_REVIEW_TESTING_BLOCKED = "human_review_testing_blocked"
HUMAN_REVIEW_PREPARATION_FAILED = "human_review_preparation_failed"
HUMAN_REVIEW_TARGET_EXPIRED = "human_review_target_expired"
HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY = (
    "_human_review_eligibility_resolver"
)


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
    eligibility_resolver = result.get(
        HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY
    )
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
    if request.state in {"approved", "changes_requested"}:
        return request
    effective_now = now or utc_now()
    eligible = sorted(
        {
            str(user_id).strip()
            for user_id in decision_eligible_user_ids
            if str(user_id).strip()
        }
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
        newly_eligible = [
            user_id for user_id in eligible if user_id not in previously_eligible
        ]
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
    if request.state in {"approved", "changes_requested"}:
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
    if (
        request.requirement_snapshot.preparation_mode == "workflow"
        and request.test_target is None
    ):
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
            f"stale human review revision: expected {expected_revision}, "
            f"current {request.revision}"
        )
    if request.state != "pending":
        raise ValueError(
            f"human review request {request.id} is {request.state}, not pending"
        )
    reviewer_id = str(reviewer_user_id or "").strip()
    if reviewer_id not in request.current_assignment.eligible_user_ids:
        raise PermissionError("reviewer is not a current eligible assignee")
    normalized_decision = str(decision or "").strip()
    if normalized_decision not in {"approved", "changes_requested", "blocked"}:
        raise ValueError("decision must be approved, changes_requested, or blocked")
    _validate_checklist(request, checklist_results or {})
    effective_now = now or utc_now()
    release = _require_release_for_request(state, request)
    _require_request_candidate(request, release)
    carried_ids = {
        finding.finding_id
        for finding in (
            release.manifest.carried_human_findings if release.manifest else []
        )
    }
    confirmed_ids = sorted(
        {
            str(finding_id).strip()
            for finding_id in confirmed_finding_ids
            if str(finding_id).strip()
        }
    )
    if normalized_decision == "approved" and set(confirmed_ids) != carried_ids:
        raise ValueError(
            "human approval must explicitly confirm every carried finding"
        )
    if normalized_decision == "changes_requested" and not findings:
        raise ValueError("changes_requested requires at least one actionable finding")

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
    result.setdefault("created_human_review_decision_ids", []).append(
        decision_record.id
    )

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
        blocker.reason_code == HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE
        for blocker in active_blockers
    ):
        raise ValueError(
            "assignment blocker requires audited reassignment before resume"
        )
    _resolve_human_review_blockers(
        state,
        request=request,
        reason_codes=None,
        note="Human review blocker was recovered against the unchanged candidate.",
        reference_ids=[request.id],
        result=result,
        now=effective_now,
    )
    if (
        request.requirement_snapshot.preparation_mode == "workflow"
        and request.test_target is None
    ):
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
            and
            request.state == "pending"
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
            raise ValueError(
                f"deliverable {deliverable_id} is not a current accepted correction"
            )
    normalized_resolution = str(proposed_resolution or "").strip()
    if not normalized_resolution:
        raise ValueError("proposed_resolution is required")
    normalized_evidence_refs = sorted(
        {
            str(evidence_ref).strip()
            for evidence_ref in evidence_refs
            if str(evidence_ref).strip()
        }
    )
    if not normalized_evidence_refs:
        raise ValueError("finding resolution proposal requires evidence")
    finding.proposed_resolution = normalized_resolution
    finding.resolution_evidence_refs = normalized_evidence_refs
    finding.proposed_by_deliverable_ids = deliverable_ids
    return finding


def create_human_finding_correction_root(
    state: TeamState,
    *,
    finding_id: str,
    owner_agent_id: str,
    title: str,
    description: str,
    request_id: str,
    result: dict[str, Any],
    task_id: str | None = None,
    originating_task_id: str | None = None,
    created_by_agent_id: str | None = None,
    created_by_actor: str | None = None,
    now: datetime | None = None,
) -> AgentTask:
    """Retarget one ambiguous finding to a new independent correction root."""

    normalized_finding_id = str(finding_id or "").strip()
    finding = state.human_review_findings.get(normalized_finding_id)
    if finding is None:
        raise ValueError(f"human review finding {normalized_finding_id} does not exist")
    if finding.state != "open":
        raise ValueError("resolved findings cannot be retargeted")
    if finding.proposed_resolution or finding.proposed_by_deliverable_ids:
        raise ValueError("a finding with a resolution proposal cannot be retargeted")

    source_request = state.human_review_requests.get(
        finding.source_human_review_request_id
    )
    release = (
        state.release_packages.get(source_request.release_package_id)
        if source_request is not None
        else None
    )
    if source_request is None or release is None:
        raise ValueError("human review finding has no valid source release")
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
    specific_origin_id, _specific_owner_id = _specific_finding_route(
        state,
        release=release,
        target_deliverable_ids=finding.target_deliverable_ids,
    )
    if specific_origin_id is not None:
        raise ValueError(
            "only an ambiguous or release-wide finding can be retargeted "
            "through the triage correction-root command"
        )

    project = state.projects.get(release.project_id)
    owner = state.agents.get(str(owner_agent_id or "").strip())
    if project is None or owner is None:
        raise ValueError("correction root requires a valid project owner")
    if (
        owner.team_id != project.team_id
        or not agent_has_project_assignment(state, owner.id, project.id)
    ):
        raise ValueError(
            f"correction root owner {owner.id} is not assigned to project {project.id}"
        )
    normalized_origin_id = str(originating_task_id or "").strip() or None
    if normalized_origin_id is not None:
        origin = state.tasks.get(normalized_origin_id)
        if origin is None or origin.project_id != project.id:
            raise ValueError(
                f"originating task {normalized_origin_id} must belong to "
                f"project {project.id}"
            )

    normalized_title = str(title or "").strip()
    normalized_description = str(description or "").strip()
    normalized_request_id = str(request_id or "").strip()
    if not normalized_title:
        raise ValueError("correction root title is required")
    if not normalized_description:
        raise ValueError("correction root description is required")
    if not normalized_request_id:
        raise ValueError("request_id is required")
    if (created_by_agent_id is None) == (created_by_actor is None):
        raise ValueError("correction root requires exactly one creator identity")

    normalized_task_id = str(task_id or "").strip() or (
        "human-correction-"
        + _stable_token(
            source_request.id,
            finding.id,
            normalized_request_id,
            length=24,
        )
    )
    if normalized_task_id in state.tasks:
        raise ValueError(f"task {normalized_task_id} already exists")
    effective_now = now or utc_now()
    correction_task = AgentTask(
        id=normalized_task_id,
        owner_agent_id=owner.id,
        project_id=project.id,
        case_id=case.id,
        priority=current_task.priority,
        state=TeamTaskState.PENDING,
        task_kind=TeamTaskKind.SUPPORT,
        title=normalized_title,
        description=normalized_description,
        created_by_agent_id=created_by_agent_id,
        created_by_actor=created_by_actor,
        originating_task_id=normalized_origin_id,
        created_at=effective_now,
        updated_at=effective_now,
        state_entered_at=effective_now,
    )
    state.tasks[correction_task.id] = correction_task
    if correction_task.id not in case.task_ids:
        case.task_ids.append(correction_task.id)
    case.status = TeamCaseStatus.IN_PROGRESS
    case.updated_at = effective_now
    finding.correction_task_id = correction_task.id
    _ensure_correction_inbox(
        state,
        request_key=(
            f"{source_request.id}:retarget:{finding.id}:{correction_task.id}"
        ),
        owner_agent_id=owner.id,
        task=correction_task,
        case=case,
        result=result,
        now=effective_now,
    )
    result["human_review_finding_id"] = finding.id
    result["correction_task_id"] = correction_task.id
    result.setdefault("created_task_ids", []).append(correction_task.id)
    _recalculate_project_status(state, project.id)
    return correction_task


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
        raise ValueError(
            f"case {normalized_case_id} is not a post-release feedback case"
        )
    if case.status in {TeamCaseStatus.RESOLVED, TeamCaseStatus.CLOSED}:
        raise ValueError(
            f"post-release feedback case {case.id} is already {case.status.value}"
        )
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("post-release case closure reason is required")
    normalized_evidence_refs = sorted(
        {
            str(evidence_ref).strip()
            for evidence_ref in evidence_refs
            if str(evidence_ref).strip()
        }
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
        {
            str(evidence_ref).strip()
            for evidence_ref in evidence_refs
            if str(evidence_ref).strip()
        }
    )
    if not normalized_evidence_refs:
        raise ValueError("post-release feedback requires evidence")
    source_ref = f"{release.id}:{str(request_id).strip()}"
    existing = next(
        (
            case
            for case in state.cases.values()
            if case.source == "post_release_feedback"
            and case.source_ref == source_ref
        ),
        None,
    )
    if existing is not None:
        return existing
    project = state.projects[release.project_id]
    lead_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    effective_now = now or utc_now()
    case = TeamCase(
        id=f"post-release-case-{_stable_token(source_ref, length=24)}",
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
        task_id=f"post-release-triage-{_stable_token(source_ref, length=24)}",
        project_id=project.id,
        case=case,
        owner_agent_id=lead_id,
        title=f"Triage post-release feedback for {release.id}",
        description=case.description,
        originating_task_id=None,
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
        for finding in (
            release.manifest.carried_human_findings if release.manifest else []
        )
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
    if case is None:
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

    ambiguous_task: AgentTask | None = None
    for position, raw in enumerate(finding_inputs):
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError(f"human finding {position + 1} requires actionable text")
        target_ids = sorted(
            {
                str(value).strip()
                for value in raw.get("target_deliverable_ids", [])
                if str(value).strip()
            }
        )
        evidence_refs = sorted(
            {
                str(value).strip()
                for value in raw.get("evidence_refs", [])
                if str(value).strip()
            }
        )
        origin_task_id, owner_id = _specific_finding_route(
            state,
            release=release,
            target_deliverable_ids=target_ids,
        )
        finding_id = f"human-finding-{_stable_token(request.id, str(position), text, length=24)}"
        if origin_task_id is None:
            if ambiguous_task is None:
                ambiguous_task = _ensure_system_task(
                    state,
                    task_id=f"human-triage-{_stable_token(request.id, length=24)}",
                    project_id=project.id,
                    case=case,
                    owner_agent_id=lead_id,
                    title=f"Triage human findings for {release.id}",
                    description=(
                        "Route release-wide or multi-owner findings into explicit "
                        "independent correction roots."
                    ),
                    originating_task_id=None,
                    now=now,
                )
            correction_task = ambiguous_task
        else:
            correction_task = _ensure_system_task(
                state,
                task_id=f"human-correction-{_stable_token(finding_id, length=24)}",
                project_id=project.id,
                case=case,
                owner_agent_id=owner_id,
                title=f"Correct human finding: {text[:96]}",
                description=text,
                originating_task_id=origin_task_id,
                now=now,
            )
        finding = state.human_review_findings.get(finding_id)
        if finding is None:
            finding = HumanReviewFinding(
                id=finding_id,
                source_human_review_request_id=request.id,
                text=text,
                target_deliverable_ids=target_ids,
                evidence_refs=evidence_refs,
                correction_task_id=correction_task.id,
                created_at=now,
            )
            state.human_review_findings[finding.id] = finding
            result.setdefault("created_human_review_finding_ids", []).append(
                finding.id
            )
        _ensure_correction_inbox(
            state,
            request_key=request.id,
            owner_agent_id=correction_task.owner_agent_id,
            task=correction_task,
            case=case,
            result=result,
            now=now,
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


def _specific_finding_route(
    state: TeamState,
    *,
    release: ReleasePackage,
    target_deliverable_ids: Sequence[str],
) -> tuple[str | None, str]:
    project = state.projects[release.project_id]
    lead_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    if len(target_deliverable_ids) != 1:
        return None, lead_id
    deliverable = state.deliverables.get(target_deliverable_ids[0])
    task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
    if (
        deliverable is None
        or task is None
        or task.project_id != release.project_id
        or deliverable.id not in {
            evidence.deliverable_id
            for evidence in (release.manifest.deliverables if release.manifest else [])
        }
    ):
        return None, lead_id
    return task.id, task.owner_agent_id


def _ensure_system_task(
    state: TeamState,
    *,
    task_id: str,
    project_id: str,
    case: TeamCase,
    owner_agent_id: str,
    title: str,
    description: str,
    originating_task_id: str | None,
    now: datetime,
) -> AgentTask:
    existing = state.tasks.get(task_id)
    if existing is not None:
        return existing
    task = AgentTask(
        id=task_id,
        owner_agent_id=owner_agent_id,
        project_id=project_id,
        case_id=case.id,
        priority=0,
        state=TeamTaskState.PENDING,
        task_kind=TeamTaskKind.SUPPORT,
        title=title,
        description=description,
        created_by_actor="system:human-review",
        originating_task_id=originating_task_id,
        created_at=now,
        updated_at=now,
        state_entered_at=now,
    )
    state.tasks[task.id] = task
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
) -> AgentInboxItem:
    inbox_id = f"inbox-human-review-{_stable_token(request_key, owner_agent_id, task.id, length=24)}"
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
        },
        state=TeamInboxItemState.PENDING,
        created_at=now,
        updated_at=now,
    )
    state.inbox[item.id] = item
    _mark_agent_work_due(state, agent_id=owner_agent_id, now=now)
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
                "recovery_directory_path": (
                    request.requirement_snapshot.directory_path
                ),
                "visibility_agent_id": previous_owner_id,
            },
            now=now,
        )
        result["human_review_recovery_permission"] = "admin.owner"
        result["human_review_recovery_directory_path"] = (
            request.requirement_snapshot.directory_path
        )
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
    required_ids = {
        item.id
        for item in request.requirement_snapshot.checklist
        if item.required
    }
    missing = sorted(
        item_id
        for item_id in required_ids
        if checklist_results.get(item_id) is not True
    )
    if missing:
        raise ValueError(
            "required human review checklist items are incomplete: "
            + ", ".join(missing)
        )


def _safe_test_target_payload(target: Mapping[str, Any]) -> dict[str, Any]:
    forbidden_tokens = ("secret", "password", "token", "credential", "cookie", "authorization")
    forbidden = sorted(
        key
        for key in target
        if any(token in str(key).casefold() for token in forbidden_tokens)
    )
    if forbidden:
        raise ValueError(
            "human review target contains secret-bearing fields: "
            + ", ".join(forbidden)
        )
    raw_url = target.get("url")
    if raw_url is not None:
        parsed_url = urlsplit(str(raw_url).strip())
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError(
                "human review target URL must not contain userinfo credentials"
            )
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
        raise ValueError(
            "unsupported human review target fields: " + ", ".join(extra)
        )
    return dict(target)


def _secret_bearing_url_parameter(name: str) -> bool:
    normalized = (
        str(name)
        .strip()
        .casefold()
        .replace("-", "_")
        .replace(".", "_")
    )
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
        "signature",
        "signed",
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
        raise ValueError(
            f"human review request {request.id} references a missing release"
        )
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
    "HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE",
    "HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY",
    "HUMAN_REVIEW_PREPARATION_FAILED",
    "HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE",
    "HUMAN_REVIEW_REQUESTED_EVENT_TYPE",
    "HUMAN_REVIEW_TARGET_EXPIRED",
    "HUMAN_REVIEW_TESTING_BLOCKED",
    "close_post_release_feedback_case",
    "create_human_finding_correction_root",
    "ensure_human_review_request",
    "expire_human_review_targets",
    "human_review_request_for_release",
    "reassign_human_review",
    "reconcile_human_review_assignment",
    "record_human_finding_resolution_proposal",
    "record_human_review_decision",
    "record_human_review_preparation_result",
    "report_post_release_feedback",
    "resume_human_review",
]
