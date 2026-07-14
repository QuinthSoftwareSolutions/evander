from __future__ import annotations

from collections.abc import Mapping
from datetime import (
    UTC,
    datetime,
)
from typing import Any
from uuid import uuid4
from ...models import (
    AgentInboxItem,
    PersistentAgent,
    ReleaseGateFailure,
    ReviewCriterionResult,
    ReviewFindingRecord,
    ReviewProfile,
    ReviewProfileCriterion,
    ReviewRequest,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)

from .agent_access import (
    _is_project_lead_for,
    _is_team_manager,
    _supervises,
)
from .common import (
    _MANAGER_FALLBACK_PENDING_REVIEW_STATES,
    _append_unique,
    _generated_id,
    _list_of_mappings,
    _optional_text,
    _require_present,
    _required_text,
    _set_task_state,
    _string_list,
    utc_now,
)
from .deliverables import (
    _active_task_deliverables,
    _deliverable_verification_payload,
)
from .manager_fallback import _manager_fallback_review_decision_guard_error
from .wake import _append_agent_wake_requested_event_for_inbox_item
from .work_due import _mark_agent_work_due


def _review_profile_for_request(
    state: TeamState,
    *,
    profile_id: str | None,
    expected_team_id: str,
    project_id: str | None,
    reviewer: PersistentAgent,
) -> tuple[ReviewProfile | None, str | None]:
    if profile_id is None:
        return _implicit_review_profile_for_request(
            state,
            expected_team_id=expected_team_id,
            project_id=project_id,
            reviewer=reviewer,
        )
    profile = state.review_profiles.get(profile_id)
    if profile is None:
        return None, f"review request references missing review profile {profile_id}"
    error = _review_profile_request_error(
        profile,
        expected_team_id=expected_team_id,
        project_id=project_id,
        reviewer=reviewer,
    )
    if error is not None:
        return None, error
    return profile, None


def _implicit_review_profile_for_request(
    state: TeamState,
    *,
    expected_team_id: str,
    project_id: str | None,
    reviewer: PersistentAgent,
) -> tuple[ReviewProfile | None, str | None]:
    candidates = [
        profile
        for profile in state.review_profiles.values()
        if _review_profile_request_error(
            profile,
            expected_team_id=expected_team_id,
            project_id=project_id,
            reviewer=reviewer,
        )
        is None
    ]
    if project_id is not None:
        scoped = [profile for profile in candidates if profile.project_id == project_id]
        if scoped:
            candidates = scoped
        else:
            candidates = [profile for profile in candidates if profile.project_id is None]
    else:
        candidates = [profile for profile in candidates if profile.project_id is None]

    candidates.sort(key=lambda profile: profile.id)
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0], None
    role_label = reviewer.role_id or "none"
    project_label = project_id or "none"
    profile_ids = ", ".join(profile.id for profile in candidates)
    return (
        None,
        "multiple review profiles apply to reviewer role "
        f"{role_label} for project {project_label}; pass review_profile_id explicitly: "
        f"{profile_ids}",
    )


def _review_profile_request_error(
    profile: ReviewProfile,
    *,
    expected_team_id: str,
    project_id: str | None,
    reviewer: PersistentAgent,
) -> str | None:
    if profile.archived:
        return f"review profile {profile.id} is archived"
    if profile.team_id != expected_team_id:
        return f"review profile {profile.id} is not in the same team as reviewer {reviewer.id}"
    if profile.project_id is not None and profile.project_id != project_id:
        return f"review profile {profile.id} does not apply to project {project_id}"
    if profile.applicable_role_ids and reviewer.role_id not in profile.applicable_role_ids:
        return f"review profile {profile.id} does not apply to reviewer role {reviewer.role_id}"
    return None


def _apply_review_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str] | None = None,
) -> str | None:
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        result["errors"].append(f"review request references missing deliverable {deliverable_id}")
        return None
    reviewer_id = _required_text(request.get("reviewer_agent_id"), "reviewer_agent_id")
    reviewer = state.agents.get(reviewer_id)
    if reviewer is None:
        result["errors"].append(f"review request references missing reviewer {reviewer_id}")
        return None
    if reviewer.team_id != actor.team_id:
        result["errors"].append(f"reviewer {reviewer_id} is not in the same team as {actor.id}")
        return None
    task = state.tasks.get(deliverable.task_id)
    project_id = task.project_id if task is not None else None
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or (project_id is not None and _is_project_lead_for(state, actor.id, project_id))
        or _is_team_manager(state, actor)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request review of deliverable {deliverable_id}"
        )
        return None
    instructions = _optional_text(request.get("instructions"))
    profile_id = _optional_text(request.get("review_profile_id"))
    profile, profile_error = _review_profile_for_request(
        state,
        profile_id=profile_id,
        expected_team_id=actor.team_id,
        project_id=project_id,
        reviewer=reviewer,
    )
    if profile_error is not None:
        result["errors"].append(profile_error)
        return None
    if profile is not None and profile_id is None:
        profile_id = profile.id
    submitted_commit_sha = (
        deliverable.git_refs.submitted_commit_sha or deliverable.git_refs.commit_sha
    )
    reviewed_commit_sha = deliverable.git_refs.reviewed_commit_sha or submitted_commit_sha
    existing_review = _active_review_request_for_reviewer(
        state,
        deliverable_id=deliverable_id,
        reviewer_id=reviewer_id,
    )
    now = utc_now()
    if existing_review is not None:
        _reopen_accepted_deliverable_for_pending_review(
            deliverable=deliverable,
            task=task,
            now=now,
            projects_to_recalc=projects_to_recalc,
        )
        result.setdefault("existing_review_request_ids", []).append(existing_review.id)
        return existing_review.id
    review_id = _optional_text(request.get("id"))
    if review_id is None:
        review_id = _generated_id(
            prefix="review",
            labels=[deliverable_id, profile_id or reviewer_id],
            existing_ids=state.review_requests,
        )
    if review_id in state.review_requests:
        result["errors"].append(f"review request {review_id} already exists")
        return None
    review = ReviewRequest(
        id=review_id,
        deliverable_id=deliverable_id,
        reviewer_agent_id=reviewer_id,
        state=TeamRecordState.PENDING,
        review_profile_id=profile_id,
        instructions=instructions,
        reviewed_commit_sha=reviewed_commit_sha,
        created_at=now,
    )
    state.review_requests[review.id] = review
    deliverable.review_request_ids = _append_unique(
        deliverable.review_request_ids,
        review.id,
    )
    _reopen_accepted_deliverable_for_pending_review(
        deliverable=deliverable,
        task=task,
        now=now,
        projects_to_recalc=projects_to_recalc,
    )
    payload: dict[str, Any] = {
        "kind": "review_request",
        "review_request_id": review.id,
        "deliverable_id": deliverable_id,
        "instructions": instructions or "",
        "artifact_refs": list(deliverable.artifact_refs),
        "carried_findings": [
            finding.model_dump(mode="json")
            for finding in deliverable.carried_findings
            if finding.state == "open"
        ],
    }
    verification_payload = _deliverable_verification_payload(deliverable)
    if verification_payload is not None:
        payload["verification"] = verification_payload
    if profile is not None:
        payload["review_profile"] = {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "criteria": [
                {
                    "id": criterion.id,
                    "label": criterion.label,
                    "description": criterion.description,
                    "required": criterion.required,
                }
                for criterion in profile.criteria
            ],
        }
    inbox_id = f"inbox-{uuid4().hex}"
    item = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=reviewer_id,
        sender_agent_id=actor.id,
        task_id=deliverable.task_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=True,
        priority=TeamInboxPriority.NORMAL,
        payload=payload,
        created_at=now,
        updated_at=now,
    )
    state.inbox[inbox_id] = item
    result["created_inbox_item_ids"].append(inbox_id)
    _mark_agent_work_due(state, agent_id=reviewer_id, now=now)
    _append_agent_wake_requested_event_for_inbox_item(
        state,
        item=item,
        result=result,
        now=now,
    )
    return review.id


def _reopen_accepted_deliverable_for_pending_review(
    *,
    deliverable: Any,
    task: Any | None,
    now: datetime,
    projects_to_recalc: set[str] | None = None,
) -> bool:
    reopened = False
    if deliverable.state == TeamRecordState.ACCEPTED:
        deliverable.state = TeamRecordState.PENDING
        reopened = True
    if task is not None and task.state == TeamTaskState.ACCEPTED:
        _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        task.updated_at = now
        reopened = True
    if reopened and task is not None and projects_to_recalc is not None:
        projects_to_recalc.add(task.project_id)
    return reopened

def _active_review_request_for_reviewer(
    state: TeamState,
    *,
    deliverable_id: str,
    reviewer_id: str,
) -> ReviewRequest | None:
    matches = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id == deliverable_id
        and review.reviewer_agent_id == reviewer_id
        and review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda review: (
            review.created_at or datetime.min.replace(tzinfo=UTC),
            review.id,
        )
    )
    return matches[0]

def _review_request_was_created(result: Mapping[str, Any], review_request_id: str) -> bool:
    return review_request_id not in set(result.get("existing_review_request_ids") or [])

def _apply_review_decision(
    state: TeamState,
    *,
    actor: PersistentAgent,
    decision: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
    enforce_manager_fallback_routing_guard: bool = False,
) -> str | None:
    review_request_id = _required_text(decision.get("review_request_id"), "review_request_id")
    review = state.review_requests.get(review_request_id)
    if review is None:
        result["errors"].append(
            f"review decision references missing review request {review_request_id}"
        )
        return None
    if review.reviewer_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} is not the assigned reviewer for {review_request_id}"
        )
        return None
    if review.decision_at is not None:
        retry_guidance = (
            " Use team_retry_release_gate for an eligible process-blocked release review."
            if review.release_package_id is not None
            and review.state == TeamRecordState.BLOCKED
            else ""
        )
        result["errors"].append(
            f"review {review_request_id} already has an immutable "
            f"{review.state.value} decision.{retry_guidance}"
        )
        return None
    if (
        enforce_manager_fallback_routing_guard
        and review.release_package_id is None
        and _optional_text(decision.get("override_routing_gap_reason")) is None
    ):
        guard_error = _manager_fallback_review_decision_guard_error(
            state,
            actor=actor,
            review=review,
        )
        if guard_error is not None:
            result["errors"].append(guard_error)
            return None
    raw_state = _required_text(decision.get("state"), "state")
    try:
        decision_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"review decision has invalid state {raw_state}")
        return None
    if not _is_review_decision_state(decision_state):
        result["errors"].append(
            f"review decision state {raw_state} must be accepted, rejected, or blocked"
        )
        return None
    criterion_results, criteria_error = _build_criterion_results(
        state,
        review,
        _list_of_mappings(decision.get("criteria"), "criteria"),
    )
    if criteria_error is not None:
        result["errors"].append(criteria_error)
        return None
    finding_resolutions = _list_of_mappings(
        decision.get("finding_resolutions"),
        "finding_resolutions",
    )
    findings = _string_list(decision.get("findings"), "findings")
    decision_error = _review_decision_policy_error(
        state,
        review=review,
        decision_state=decision_state,
        criterion_results=criterion_results or [],
        finding_resolutions=finding_resolutions,
        findings=findings,
    )
    if decision_error is not None:
        result["errors"].append(decision_error)
        return None
    if criterion_results is not None:
        review.criteria = criterion_results
    _apply_finding_resolutions(
        state,
        review=review,
        decision_state=decision_state,
        finding_resolutions=finding_resolutions,
    )
    _record_open_review_findings(
        state,
        review=review,
        decision_state=decision_state,
        findings=findings,
        criterion_results=criterion_results or [],
    )
    _cascade_review_decision(
        state,
        review=review,
        decision_state=decision_state,
        findings=findings,
        projects_to_recalc=projects_to_recalc,
        result=result,
    )
    return review.id

def _build_criterion_results(
    state: TeamState,
    review: ReviewRequest,
    raw_criteria: list[dict[str, Any]],
) -> tuple[list[ReviewCriterionResult] | None, str | None]:
    """Validate criterion-result mappings against the review's profile.

    Returns ``(results, None)`` on success or ``(None, error_message)`` so callers
    can append the error to ``result["errors"]`` without mutating state. ``results``
    is an empty list when no criteria were supplied.
    """

    if not raw_criteria:
        return [], None
    criterion_definitions = _criterion_definitions_for_review(state, review)
    if criterion_definitions is None:
        return None, (
            f"review decision for {review.id} has criterion results but no review profile"
        )
    valid_ids = {criterion.id for criterion in criterion_definitions}
    seen: set[str] = set()
    results: list[ReviewCriterionResult] = []
    for entry in raw_criteria:
        criterion_id = _optional_text(entry.get("criterion_id"))
        if criterion_id is None:
            return None, "review decision criterion result is missing criterion_id"
        if criterion_id not in valid_ids:
            return None, (
                f"review decision for {review.id} references unknown criterion {criterion_id}"
            )
        if criterion_id in seen:
            return None, (
                f"review decision for {review.id} has duplicate criterion result {criterion_id}"
            )
        seen.add(criterion_id)
        raw_state = _optional_text(entry.get("state"))
        if raw_state not in {"passed", "failed", "skipped"}:
            return None, (f"review decision criterion {criterion_id} has invalid state {raw_state}")
        results.append(
            ReviewCriterionResult(
                criterion_id=criterion_id,
                state=raw_state,
                finding=_optional_text(entry.get("finding")) or "",
            )
        )
    return results, None


def _criterion_definitions_for_review(
    state: TeamState,
    review: ReviewRequest,
) -> list[ReviewProfileCriterion] | None:
    if review.criteria_snapshot is not None:
        return review.criteria_snapshot
    profile = (
        state.review_profiles.get(review.review_profile_id)
        if review.review_profile_id is not None
        else None
    )
    return profile.criteria if profile is not None else None


def _review_decision_policy_error(
    state: TeamState,
    *,
    review: ReviewRequest,
    decision_state: TeamRecordState,
    criterion_results: list[ReviewCriterionResult],
    finding_resolutions: list[dict[str, Any]],
    findings: list[str] | None = None,
) -> str | None:
    criterion_definitions = _criterion_definitions_for_review(state, review)
    if decision_state == TeamRecordState.ACCEPTED and criterion_definitions is not None:
        by_id = {result.criterion_id: result for result in criterion_results}
        missing = [
            criterion.id
            for criterion in criterion_definitions
            if criterion.required and criterion.id not in by_id
        ]
        if missing:
            return (
                f"accepted review {review.id} is missing required criterion results: "
                + ", ".join(missing)
            )
        not_passed = [
            criterion.id
            for criterion in criterion_definitions
            if criterion.required
            and by_id[criterion.id].state != "passed"
        ]
        if not_passed:
            return (
                f"accepted review {review.id} has required criteria that did not pass: "
                + ", ".join(not_passed)
            )

    if review.release_package_id is not None:
        release = _require_present(
            state.release_packages,
            review.release_package_id,
            "release package",
        )
        if review.candidate_commit_sha != release.candidate_commit_sha:
            return (
                f"release review {review.id} targets candidate "
                f"{review.candidate_commit_sha}, not the pinned candidate "
                f"{release.candidate_commit_sha}"
            )
        if finding_resolutions:
            return "release review decisions do not accept carried finding resolutions"
        if decision_state == TeamRecordState.BLOCKED and not findings:
            return "blocked release review requires a non-empty blocker finding"
        return None

    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    if finding_resolutions and decision_state != TeamRecordState.ACCEPTED:
        return "carried findings may only be resolved by an accepted review decision"
    resolution_ids: list[str] = []
    for index, resolution in enumerate(finding_resolutions):
        finding_id = _optional_text(resolution.get("finding_id"))
        explanation = _optional_text(resolution.get("resolution"))
        if finding_id is None or explanation is None:
            return (
                f"finding_resolutions[{index}] requires finding_id and a non-empty resolution"
            )
        if finding_id in resolution_ids:
            return f"finding_resolutions contains duplicate finding_id {finding_id}"
        resolution_ids.append(finding_id)
        finding = next(
            (item for item in deliverable.carried_findings if item.id == finding_id),
            None,
        )
        if finding is None:
            return f"finding resolution references unknown carried finding {finding_id}"
        if finding.state != "open":
            return f"carried finding {finding_id} is already resolved"
        if not _review_owns_finding(state, review=review, finding=finding):
            return (
                f"review {review.id} cannot resolve carried finding {finding_id}; "
                "a matching review profile or legacy reviewer role is required"
            )

    if decision_state == TeamRecordState.ACCEPTED:
        owned_open_ids = {
            finding.id
            for finding in deliverable.carried_findings
            if finding.state == "open"
            and _review_owns_finding(state, review=review, finding=finding)
        }
        missing_resolutions = sorted(owned_open_ids - set(resolution_ids))
        if missing_resolutions:
            return (
                f"accepted review {review.id} must resolve its carried findings: "
                + ", ".join(missing_resolutions)
            )
    return None


def _review_owns_finding(
    state: TeamState,
    *,
    review: ReviewRequest,
    finding: ReviewFindingRecord,
) -> bool:
    if finding.owner_review_profile_id is not None:
        return review.review_profile_id == finding.owner_review_profile_id
    reviewer = state.agents.get(review.reviewer_agent_id)
    return reviewer is not None and reviewer.role_id == finding.owner_role_id


def _apply_finding_resolutions(
    state: TeamState,
    *,
    review: ReviewRequest,
    decision_state: TeamRecordState,
    finding_resolutions: list[dict[str, Any]],
) -> None:
    if decision_state != TeamRecordState.ACCEPTED or review.deliverable_id is None:
        return
    if review.release_package_id is not None:
        return
    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    now = utc_now()
    by_id = {finding.id: finding for finding in deliverable.carried_findings}
    for resolution in finding_resolutions:
        finding = by_id[_required_text(resolution.get("finding_id"), "finding_id")]
        finding.state = "resolved"
        finding.resolution = _required_text(resolution.get("resolution"), "resolution")
        finding.evidence_refs = _string_list(
            resolution.get("evidence_refs"),
            "evidence_refs",
        )
        finding.resolved_by_review_request_id = review.id
        finding.resolved_at = now


def _record_open_review_findings(
    state: TeamState,
    *,
    review: ReviewRequest,
    decision_state: TeamRecordState,
    findings: list[str],
    criterion_results: list[ReviewCriterionResult],
) -> None:
    if (
        decision_state not in {TeamRecordState.REJECTED, TeamRecordState.BLOCKED}
        or review.deliverable_id is None
    ):
        return
    if review.release_package_id is not None:
        return
    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    reviewer = state.agents.get(review.reviewer_agent_id)
    rows: list[tuple[str | None, str]] = [(None, finding) for finding in findings]
    rows.extend(
        (
            result.criterion_id,
            result.finding or f"Criterion {result.criterion_id} was {result.state}.",
        )
        for result in criterion_results
        if result.state in {"failed", "skipped"}
    )
    existing = {
        (finding.source_review_request_id, finding.criterion_id, finding.text)
        for finding in deliverable.carried_findings
    }
    for criterion_id, text in rows:
        key = (review.id, criterion_id, text)
        if key in existing:
            continue
        deliverable.carried_findings.append(
            ReviewFindingRecord(
                id=f"finding-{uuid4().hex}",
                source_review_request_id=review.id,
                source_deliverable_id=deliverable.id,
                owner_review_profile_id=review.review_profile_id,
                owner_role_id=reviewer.role_id if reviewer is not None else None,
                criterion_id=criterion_id,
                text=text,
            )
        )
        existing.add(key)

def _is_review_decision_state(decision_state: TeamRecordState) -> bool:
    return decision_state in {
        TeamRecordState.ACCEPTED,
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }

def _cascade_review_decision(
    state: TeamState,
    *,
    review: ReviewRequest,
    decision_state: TeamRecordState,
    findings: list[str],
    projects_to_recalc: set[str],
    result: dict[str, Any] | None = None,
) -> None:
    """Apply a review decision and cascade it to the deliverable, task, and project.

    This is the single source of truth for review decision cascade rules, shared by
    the ``review_decisions`` agent action and the Admin API decision endpoint.
    """

    review.state = decision_state
    review.findings = findings
    review.decision_at = utc_now()
    if review.release_package_id is not None:
        _cascade_release_review_decision(
            state,
            review=review,
            decision_state=decision_state,
            findings=findings,
            projects_to_recalc=projects_to_recalc,
            result=result,
        )
        return
    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    task = _require_present(state.tasks, deliverable.task_id, "task")
    now = utc_now()
    if decision_state == TeamRecordState.ACCEPTED:
        all_accepted = all(
            state.review_requests[review_id].state == TeamRecordState.ACCEPTED
            for review_id in deliverable.review_request_ids
        )
        no_open_findings = all(
            finding.state == "resolved" for finding in deliverable.carried_findings
        )
        if all_accepted and no_open_findings:
            deliverable.state = TeamRecordState.ACCEPTED
            task_deliverables = _active_task_deliverables(state, task)
            if task_deliverables and all(
                item.state == TeamRecordState.ACCEPTED for item in task_deliverables
            ):
                _set_task_state(task, TeamTaskState.ACCEPTED, now=now)
                task.updated_at = now
    elif decision_state == TeamRecordState.REJECTED:
        deliverable.state = TeamRecordState.REJECTED
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        task.updated_at = now
        rework_agent_id = task.owner_agent_id
        inbox_id = f"inbox-{uuid4().hex}"
        item = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=rework_agent_id,
            sender_agent_id=review.reviewer_agent_id,
            task_id=task.id,
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=True,
            priority=TeamInboxPriority.NORMAL,
            payload={
                "kind": "review_rejected",
                "review_request_id": review.id,
                "deliverable_id": deliverable.id,
                "supersedes_deliverable_id": deliverable.id,
                "task_id": task.id,
                "summary": "Review rejected; revise and resubmit.",
                "findings": list(findings),
                "artifact_refs": list(deliverable.artifact_refs),
            },
        )
        state.inbox[inbox_id] = item
        if result is not None:
            result["created_inbox_item_ids"].append(inbox_id)
            _append_agent_wake_requested_event_for_inbox_item(
                state,
                item=item,
                result=result,
                now=now,
            )
        _mark_agent_work_due(
            state,
            agent_id=rework_agent_id,
            now=now,
        )
    else:  # blocked
        deliverable.state = TeamRecordState.BLOCKED
        _set_task_state(task, TeamTaskState.BLOCKED, now=now)
        task.updated_at = now
    projects_to_recalc.add(task.project_id)


def _cascade_release_review_decision(
    state: TeamState,
    *,
    review: ReviewRequest,
    decision_state: TeamRecordState,
    findings: list[str],
    projects_to_recalc: set[str],
    result: dict[str, Any] | None,
) -> None:
    release = _require_present(
        state.release_packages,
        review.release_package_id,
        "release package",
    )
    candidate = release.candidate_commit_sha
    if review.candidate_commit_sha != candidate or review.reviewed_commit_sha != candidate:
        release.state = TeamRecordState.REJECTED
        release.error = (
            f"release review {review.id} is pinned to {review.candidate_commit_sha}, "
            f"but release {release.id} is pinned to {candidate}"
        )
        release.gate_failure = ReleaseGateFailure(
            stage="configuration",
            kind="configuration",
            source_id=review.id,
            message=release.error,
        )
        release.updated_at = utc_now()
        projects_to_recalc.add(release.project_id)
        if result is not None:
            result["errors"].append(release.error)
        return
    if decision_state == TeamRecordState.REJECTED:
        release.state = TeamRecordState.REJECTED
        release.error = "; ".join(findings) or f"release review {review.id} rejected"
        release.gate_failure = ReleaseGateFailure(
            stage="review",
            kind="rejected",
            source_id=review.id,
            message=release.error,
        )
        release.updated_at = utc_now()
        projects_to_recalc.add(release.project_id)
        return
    if decision_state == TeamRecordState.BLOCKED:
        release.state = TeamRecordState.BLOCKED
        release.error = "; ".join(findings) or f"release review {review.id} blocked"
        release.gate_failure = ReleaseGateFailure(
            stage="review",
            kind="blocked",
            source_id=review.id,
            message=release.error,
        )
        release.updated_at = utc_now()
        projects_to_recalc.add(release.project_id)
        return

    from .release_gates import (
        _release_promotion,
        approve_release_candidate,
        current_release_gates_passed,
    )

    if current_release_gates_passed(state, release):
        promotion = _release_promotion(state, release)
        if promotion is None:
            release.state = TeamRecordState.REJECTED
            release.error = "release promotion record is missing"
            release.gate_failure = ReleaseGateFailure(
                stage="configuration",
                kind="configuration",
                source_id=review.id,
                message=release.error,
            )
        else:
            approve_release_candidate(
                state,
                release=release,
                release_promotion=promotion,
                result=result if result is not None else {"events": []},
            )
    release.updated_at = utc_now()
    projects_to_recalc.add(release.project_id)

__all__ = [
    "_review_profile_for_request",
    "_apply_review_request",
    "_reopen_accepted_deliverable_for_pending_review",
    "_active_review_request_for_reviewer",
    "_review_request_was_created",
    "_apply_review_decision",
    "_build_criterion_results",
    "_review_decision_policy_error",
    "_apply_finding_resolutions",
    "_record_open_review_findings",
    "_is_review_decision_state",
    "_cascade_review_decision",
    "_cascade_release_review_decision",
]
