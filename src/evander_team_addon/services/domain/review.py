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
    ReviewCriterionResult,
    ReviewProfile,
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
        return None, None
    profile = state.review_profiles.get(profile_id)
    if profile is None:
        return None, f"review request references missing review profile {profile_id}"
    if profile.archived:
        return None, f"review profile {profile_id} is archived"
    if profile.team_id != expected_team_id:
        return (
            None,
            f"review profile {profile_id} is not in the same team as reviewer {reviewer.id}",
        )
    if profile.project_id is not None and profile.project_id != project_id:
        return None, f"review profile {profile_id} does not apply to project {project_id}"
    if profile.applicable_role_ids and reviewer.role_id not in profile.applicable_role_ids:
        return (
            None,
            f"review profile {profile_id} does not apply to reviewer role {reviewer.role_id}",
        )
    return profile, None

def _apply_review_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
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
    existing_review = _active_review_request_for_reviewer(
        state,
        deliverable_id=deliverable_id,
        reviewer_id=reviewer_id,
    )
    if existing_review is not None:
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
    now = utc_now()
    review = ReviewRequest(
        id=review_id,
        deliverable_id=deliverable_id,
        reviewer_agent_id=reviewer_id,
        state=TeamRecordState.PENDING,
        review_profile_id=profile_id,
        instructions=instructions,
        created_at=now,
    )
    state.review_requests[review.id] = review
    deliverable.review_request_ids = _append_unique(
        deliverable.review_request_ids,
        review.id,
    )
    payload: dict[str, Any] = {
        "kind": "review_request",
        "review_request_id": review.id,
        "deliverable_id": deliverable_id,
        "instructions": instructions or "",
        "artifact_refs": list(deliverable.artifact_refs),
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
    if (
        enforce_manager_fallback_routing_guard
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
    if criterion_results is not None:
        review.criteria = criterion_results
    _cascade_review_decision(
        state,
        review=review,
        decision_state=decision_state,
        findings=_string_list(decision.get("findings"), "findings"),
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
    profile = (
        state.review_profiles.get(review.review_profile_id)
        if review.review_profile_id is not None
        else None
    )
    if profile is None:
        return None, (
            f"review decision for {review.id} has criterion results but no review profile"
        )
    valid_ids = {criterion.id for criterion in profile.criteria}
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
    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    task = _require_present(state.tasks, deliverable.task_id, "task")
    now = utc_now()
    if decision_state == TeamRecordState.ACCEPTED:
        all_accepted = all(
            state.review_requests[review_id].state == TeamRecordState.ACCEPTED
            for review_id in deliverable.review_request_ids
        )
        if all_accepted:
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

__all__ = [
    "_review_profile_for_request",
    "_apply_review_request",
    "_active_review_request_for_reviewer",
    "_review_request_was_created",
    "_apply_review_decision",
    "_build_criterion_results",
    "_is_review_decision_state",
    "_cascade_review_decision",
]
