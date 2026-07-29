from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import (
    UTC,
    datetime,
)
from typing import Any
from uuid import uuid4
from ...models import (
    AgentInboxItem,
    AgentTask,
    Deliverable,
    PersistentAgent,
    ReleaseGateFailure,
    ReviewCriterionResult,
    ReviewFindingRecord,
    ReviewBlockKind,
    ReviewProfile,
    ReviewProfileCriterion,
    ReviewRequest,
    TeamBlockerSourceKind,
    TeamInboxItemState,
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
from .blockers import open_or_get_blocker, resolve_active_blockers_for_source
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
    _accept_deliverable_and_maybe_task,
    _active_deliverable_acceptance_blocker_ids,
    _deliverable_verification_payload,
)
from .git_work_contexts import (
    GitCandidateCommitResolver,
    require_authoritative_git_review_retry_candidate,
)
from .manager_fallback import _manager_fallback_review_decision_guard_error
from .outcome_catalog import snapshot_review_profile_outcomes
from .review_handoff import queue_actionable_inbox_item
from .task_assessment import (
    TaskAssessmentBoundaryError,
    require_deliverable_acceptance,
)
from .task_supervision import (
    parent_task_for_child,
    queue_parent_task_transition,
    task_acceptance_recipient_agent_id,
)
from .work_due import _mark_agent_work_due


_TERMINAL_REVIEW_STATES = frozenset(
    {
        TeamRecordState.ACCEPTED,
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }
)
_REVIEW_ASSIGNMENT_INBOX_KINDS = frozenset(
    {
        "review_request",
        "review_retry_request",
    }
)


def _is_team_manager_for_task_project(
    state: TeamState,
    *,
    actor: PersistentAgent,
    task: AgentTask,
) -> bool:
    project = state.projects.get(task.project_id)
    return bool(
        project is not None
        and actor.team_id == project.team_id
        and _is_team_manager(state, actor)
    )


def _review_profile_for_request(
    state: TeamState,
    *,
    profile_id: str | None,
    expected_team_id: str,
    project_id: str | None,
    reviewer: PersistentAgent,
    deliverable: Deliverable | None = None,
) -> tuple[ReviewProfile | None, str | None]:
    if profile_id is None:
        return _implicit_review_profile_for_request(
            state,
            expected_team_id=expected_team_id,
            project_id=project_id,
            reviewer=reviewer,
            deliverable=deliverable,
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
    deliverable: Deliverable | None = None,
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
    if deliverable is not None and candidates:
        outcome_constrained_candidates = [
            profile for profile in candidates if profile.required_outcome_kinds
        ]
        delivered_outcome_kinds = {outcome.kind for outcome in deliverable.outcomes}
        candidates = [
            profile
            for profile in candidates
            if set(profile.required_outcome_kinds) <= delivered_outcome_kinds
        ]
        if not candidates and outcome_constrained_candidates:
            required_sets = ", ".join(
                f"{profile.id}=[{', '.join(profile.required_outcome_kinds)}]"
                for profile in sorted(
                    outcome_constrained_candidates,
                    key=lambda item: item.id,
                )
            )
            delivered = ", ".join(sorted(delivered_outcome_kinds)) or "none"
            return (
                None,
                "no review profile outcome contract matches this deliverable; "
                f"delivered outcome kinds: {delivered}; required profiles: {required_sets}",
            )

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
    if (
        deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.rework_requested_at is not None
        and deliverable.superseded_by_deliverable_id is None
    ):
        result["errors"].append(
            f"accepted deliverable {deliverable.id} was explicitly reopened for "
            "same-task replacement; submit a new deliverable with "
            f"supersedes_deliverable_id={deliverable.id} and fresh reviews"
        )
        return None
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
        deliverable=deliverable,
    )
    if profile_error is not None:
        result["errors"].append(profile_error)
        return None
    if profile is not None and profile_id is None:
        profile_id = profile.id
    if profile is not None and profile.required_outcome_kinds:
        delivered_outcome_kinds = {outcome.kind for outcome in deliverable.outcomes}
        missing_outcome_kinds = sorted(
            set(profile.required_outcome_kinds) - delivered_outcome_kinds
        )
        if missing_outcome_kinds:
            result["errors"].append(
                f"review profile {profile.id} requires deliverable outcome kinds: "
                + ", ".join(missing_outcome_kinds)
            )
            return None
    submitted_commit_sha = (
        deliverable.git_refs.submitted_commit_sha or deliverable.git_refs.commit_sha
    )
    reviewed_commit_sha = deliverable.git_refs.reviewed_commit_sha or submitted_commit_sha
    if reviewed_commit_sha is None and any(
        (
            deliverable.git_refs.branch_context_id,
            deliverable.git_refs.agent_branch,
            deliverable.git_refs.review_branch,
            deliverable.git_refs.feature_branch,
            deliverable.git_refs.release_branch,
        )
    ):
        result["errors"].append(
            f"Git-backed deliverable {deliverable_id} does not have a pinned "
            "submitted commit; record git_refs.commit_sha before requesting review"
        )
        return None
    existing_review = _active_review_request_for_reviewer(
        state,
        deliverable_id=deliverable_id,
        reviewer_id=reviewer_id,
    )
    now = utc_now()
    if existing_review is not None:
        if (
            deliverable.review_cohort_id is not None
            and existing_review.review_cohort_id != deliverable.review_cohort_id
        ):
            result["errors"].append(
                f"deliverable {deliverable.id} has sealed review cohort "
                f"{deliverable.review_cohort_id}, but reviewer {reviewer.id} has "
                f"an active review outside that cohort"
            )
            return None
        _reopen_accepted_deliverable_for_pending_review(
            deliverable=deliverable,
            task=task,
            now=now,
            projects_to_recalc=projects_to_recalc,
        )
        result.setdefault("existing_review_request_ids", []).append(existing_review.id)
        return existing_review.id
    requested_cohort_id = _optional_text(request.get("review_cohort_id"))
    if deliverable.review_cohort_id is not None:
        if requested_cohort_id != deliverable.review_cohort_id:
            qualifier = (
                "sealed "
                if deliverable.review_cohort_sealed_at is not None
                else ""
            )
            result["errors"].append(
                f"deliverable {deliverable.id} belongs to {qualifier}review cohort "
                f"{deliverable.review_cohort_id}; new review requests must be "
                "created atomically with that cohort or replace a blocked cohort member"
            )
            return None
    elif requested_cohort_id is not None:
        result["errors"].append(
            f"review request cannot assign cohort {requested_cohort_id} because "
            f"deliverable {deliverable.id} has no review cohort"
        )
        return None
    if deliverable.review_cohort_sealed_at is not None:
        result["errors"].append(
            f"deliverable {deliverable.id} review cohort "
            f"{deliverable.review_cohort_id} is sealed; add every required reviewer "
            "in the original atomic submission or explicitly retry/replace an "
            "existing cohort member"
        )
        return None
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
        requested_by_agent_id=actor.id,
        state=TeamRecordState.PENDING,
        review_profile_id=profile_id,
        required_outcome_kind_definitions=snapshot_review_profile_outcomes(
            state,
            profile=profile,
            project_id=project_id,
        ),
        instructions=instructions,
        reviewed_commit_sha=reviewed_commit_sha,
        review_cohort_id=requested_cohort_id,
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
            "required_outcome_kinds": list(profile.required_outcome_kinds),
            "outcome_kind_definitions": [
                definition.model_dump(mode="json")
                for definition in review.required_outcome_kind_definitions
            ],
        }
    queue_actionable_inbox_item(
        state,
        recipient_agent_id=reviewer_id,
        sender_agent_id=actor.id,
        task_id=deliverable.task_id,
        priority=TeamInboxPriority.NORMAL,
        payload=payload,
        result=result,
        now=now,
    )
    return review.id


def create_sealed_review_cohort(
    state: TeamState,
    *,
    actor: PersistentAgent,
    deliverable_id: str,
    reviews: Sequence[Mapping[str, Any]],
    result: dict[str, Any],
    projects_to_recalc: set[str] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Attach every requested reviewer to one immutable deliverable atomically.

    The caller must abort its state transaction when this function raises. A
    repeated request with the same reviewer/profile membership is idempotent.
    """

    normalized_deliverable_id = _required_text(deliverable_id, "deliverable_id")
    deliverable = _require_present(
        state.deliverables,
        normalized_deliverable_id,
        "deliverable",
    )
    task = state.tasks.get(deliverable.task_id)
    project_id = task.project_id if task is not None else None
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or (project_id is not None and _is_project_lead_for(state, actor.id, project_id))
        or _is_team_manager(state, actor)
    )
    if not permitted:
        raise ValueError(
            f"agent {actor.id} is not permitted to request review of "
            f"deliverable {deliverable.id}"
        )
    normalized_reviews = [dict(review) for review in reviews]
    if not normalized_reviews:
        raise ValueError("reviews must contain at least one review request")

    requested_members: dict[str, str | None] = {}
    for index, review in enumerate(normalized_reviews):
        reviewer_id = _required_text(
            review.get("reviewer_agent_id"),
            f"reviews[{index}].reviewer_agent_id",
        )
        if reviewer_id in requested_members:
            raise ValueError(
                "a sealed review cohort requires one review slot per reviewer; "
                f"duplicate reviewer: {reviewer_id}"
            )
        requested_members[reviewer_id] = _optional_text(
            review.get("review_profile_id")
        )

    current_reviews = _current_deliverable_reviews(state, deliverable)
    if _has_sealed_review_cohort(deliverable):
        current_members = {
            review.reviewer_agent_id: review.review_profile_id
            for review in current_reviews
        }
        profiles_match = all(
            requested_profile is None
            or current_members.get(reviewer_id) == requested_profile
            for reviewer_id, requested_profile in requested_members.items()
        )
        if set(current_members) != set(requested_members) or not profiles_match:
            raise ValueError(
                f"deliverable {deliverable.id} already has sealed review cohort "
                f"{deliverable.review_cohort_id} with different membership"
            )
        existing_ids = sorted(review.id for review in current_reviews)
        result.setdefault("existing_review_request_ids", []).extend(existing_ids)
        if any(
            review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
            for review in current_reviews
        ):
            _align_task_with_sealed_review_cohort(
                deliverable=deliverable,
                task=task,
                now=utc_now(),
                projects_to_recalc=projects_to_recalc,
            )
        return deliverable.review_cohort_id or "", [], existing_ids

    if deliverable.review_cohort_id is not None:
        raise ValueError(
            f"deliverable {deliverable.id} has an unsealed review cohort "
            f"{deliverable.review_cohort_id}; recover that cohort before retrying"
        )
    if current_reviews:
        raise ValueError(
            f"deliverable {deliverable.id} already has independent review requests; "
            "a sealed cohort must be created before any individual review request"
        )

    cohort_id = f"review-cohort-{deliverable.id}"
    deliverable.review_cohort_id = cohort_id
    created_ids: list[str] = []
    for index, review in enumerate(normalized_reviews):
        request_payload = {
            **review,
            "deliverable_id": deliverable.id,
            "review_cohort_id": cohort_id,
        }
        if "review_request_id" in request_payload:
            request_payload["id"] = request_payload.pop("review_request_id")
        created_id = _apply_review_request(
            state,
            actor=actor,
            request=request_payload,
            result=result,
            projects_to_recalc=projects_to_recalc,
        )
        if created_id is None:
            raise ValueError(
                "; ".join(str(error) for error in result.get("errors") or [])
                or f"reviews[{index}] could not be created"
            )
        if _review_request_was_created(result, created_id):
            created_ids.append(created_id)

    now = utc_now()
    deliverable.review_cohort_sealed_at = now
    _align_task_with_sealed_review_cohort(
        deliverable=deliverable,
        task=task,
        now=now,
        projects_to_recalc=projects_to_recalc,
    )
    return cohort_id, created_ids, []


def retry_blocked_review(
    state: TeamState,
    *,
    actor: PersistentAgent,
    review_request_id: str,
    reason: str,
    reviewer_agent_id: str | None,
    result: dict[str, Any],
    candidate_commit_resolver: GitCandidateCommitResolver | None = None,
) -> ReviewRequest:
    """Replace the current blocked review attempt without rewriting its decision."""

    current_id = _required_text(review_request_id, "review_request_id")
    normalized_reason = _required_text(reason, "reason")
    current = _require_present(state.review_requests, current_id, "review request")
    if current.release_package_id is not None:
        raise ValueError(
            "release reviews must be retried with team_retry_release_gate"
        )
    if current.state != TeamRecordState.BLOCKED:
        raise ValueError("only a blocked review may be retried")
    if current.superseded_by_review_request_id is not None or current.retired_at is not None:
        raise ValueError("review retry must target the current active review attempt")
    deliverable = _require_present(state.deliverables, current.deliverable_id, "deliverable")
    task = _require_present(state.tasks, deliverable.task_id, "task")
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or _is_project_lead_for(state, actor.id, task.project_id)
        or _is_team_manager(state, actor)
    )
    if not permitted:
        raise ValueError(
            f"agent {actor.id} is not permitted to retry review {current.id}"
        )
    requested_reviewer_id = _optional_text(reviewer_agent_id) or current.reviewer_agent_id
    reviewer = _require_present(state.agents, requested_reviewer_id, "reviewer")
    if reviewer.team_id != actor.team_id:
        raise ValueError(f"reviewer {reviewer.id} is not in team {actor.team_id}")
    conflicting_review = next(
        (
            review
            for review in _current_deliverable_reviews(state, deliverable)
            if review.id != current.id
            and review.reviewer_agent_id == reviewer.id
        ),
        None,
    )
    if conflicting_review is not None:
        raise ValueError(
            f"reviewer {reviewer.id} already owns current review "
            f"{conflicting_review.id} for deliverable {deliverable.id}; a retry "
            "cannot collapse two review cohort slots onto one reviewer"
        )
    profile, profile_error = _review_profile_for_request(
        state,
        profile_id=current.review_profile_id,
        expected_team_id=actor.team_id,
        project_id=task.project_id,
        reviewer=reviewer,
        deliverable=deliverable,
    )
    if profile_error is not None:
        raise ValueError(profile_error)
    require_authoritative_git_review_retry_candidate(
        state,
        deliverable=deliverable,
        reviewed_commit_sha=current.reviewed_commit_sha,
        candidate_commit_resolver=candidate_commit_resolver,
    )
    retry_reviewed_commit_sha = current.reviewed_commit_sha
    if retry_reviewed_commit_sha is None and any(
        (
            deliverable.git_refs.branch_context_id,
            deliverable.git_refs.agent_branch,
            deliverable.git_refs.review_branch,
            deliverable.git_refs.feature_branch,
            deliverable.git_refs.release_branch,
        )
    ):
        retry_reviewed_commit_sha = (
            deliverable.git_refs.reviewed_commit_sha
            or deliverable.git_refs.submitted_commit_sha
            or deliverable.git_refs.commit_sha
        )
        if retry_reviewed_commit_sha is None:
            raise ValueError(
                f"Git-backed deliverable {deliverable.id} does not have a pinned "
                "submitted commit; record git_refs.commit_sha before retrying review"
            )
    now = utc_now()
    retry = ReviewRequest(
        id=_generated_id(
            prefix="review-retry",
            labels=[current.id, reviewer.id],
            existing_ids=state.review_requests,
        ),
        deliverable_id=deliverable.id,
        reviewer_agent_id=reviewer.id,
        requested_by_agent_id=actor.id,
        state=TeamRecordState.PENDING,
        review_profile_id=profile.id if profile is not None else current.review_profile_id,
        criteria_snapshot=(
            [criterion.model_copy(deep=True) for criterion in profile.criteria]
            if profile is not None
            else (
                [criterion.model_copy(deep=True) for criterion in current.criteria_snapshot]
                if current.criteria_snapshot is not None
                else None
            )
        ),
        required_outcome_kind_definitions=(
            snapshot_review_profile_outcomes(
                state,
                profile=profile,
                project_id=task.project_id,
            )
            if profile is not None
            else [
                definition.model_copy(deep=True)
                for definition in current.required_outcome_kind_definitions
            ]
        ),
        instructions=(
            f"Retry blocked review {current.id}. Reason: {normalized_reason}"
        ),
        reviewed_commit_sha=retry_reviewed_commit_sha,
        review_cohort_id=current.review_cohort_id,
        supersedes_review_request_id=current.id,
        retry_reason=normalized_reason,
        retry_requested_by=f"agent:{actor.id}",
        created_at=now,
    )
    current.superseded_by_review_request_id = retry.id
    state.review_requests[retry.id] = retry
    deliverable.review_request_ids = _append_unique(deliverable.review_request_ids, retry.id)
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
        source_id=current.id,
        actor_agent_id=actor.id,
        dispatch_id=None,
        note=f"Review retry {retry.id} was scheduled.",
        reference_ids=[retry.id],
        result=result,
        now=now,
    )
    if _has_sealed_review_cohort(deliverable):
        _recalculate_sealed_review_cohort(
            state,
            closed_review=retry,
            deliverable=deliverable,
            task=task,
            sender_agent_id=actor.id,
            result=result,
            now=now,
        )
    else:
        deliverable.state = TeamRecordState.PENDING
        _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        task.updated_at = now
    payload: dict[str, Any] = {
        "kind": "review_retry_request",
        "review_request_id": retry.id,
        "supersedes_review_request_id": current.id,
        "deliverable_id": deliverable.id,
        "retry_reason": normalized_reason,
        "artifact_refs": list(deliverable.artifact_refs),
        "instructions": retry.instructions,
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
                criterion.model_dump(mode="json") for criterion in profile.criteria
            ],
            "required_outcome_kinds": list(profile.required_outcome_kinds),
            "outcome_kind_definitions": [
                definition.model_dump(mode="json")
                for definition in retry.required_outcome_kind_definitions
            ],
        }
    queue_actionable_inbox_item(
        state,
        recipient_agent_id=reviewer.id,
        sender_agent_id=actor.id,
        task_id=task.id,
        priority=TeamInboxPriority.HIGH,
        payload=payload,
        result=result,
        now=now,
    )
    result.setdefault("created_review_request_ids", []).append(retry.id)
    return retry


def withdraw_pending_review(
    state: TeamState,
    *,
    review_request_id: str,
    reason: str,
    retired_by: str,
    actor: PersistentAgent | None,
    result: dict[str, Any] | None = None,
) -> ReviewRequest:
    """Withdraw one current pending non-release review without inventing a decision."""

    review, deliverable, task = _review_closure_target(
        state,
        review_request_id=review_request_id,
    )
    if review.state != TeamRecordState.PENDING or review.decision_at is not None:
        raise ValueError("only a pending undecided review may be withdrawn")
    if actor is not None:
        permitted = (
            actor.id == review.requested_by_agent_id
            or actor.id == deliverable.producer_agent_id
            or _supervises(state, actor.id, deliverable.producer_agent_id)
            or _is_project_lead_for(state, actor.id, task.project_id)
            or _is_team_manager_for_task_project(state, actor=actor, task=task)
        )
        if not permitted:
            raise ValueError(
                f"agent {actor.id} is not permitted to withdraw review {review.id}"
            )
    _close_review_attempt(
        state,
        review=review,
        deliverable=deliverable,
        task=task,
        reason=reason,
        retired_by=retired_by,
        action="withdrawn",
        actor_agent_id=actor.id if actor is not None else None,
        result=result,
    )
    return review


def retire_terminal_review(
    state: TeamState,
    *,
    review_request_id: str,
    reason: str,
    retired_by: str,
    actor: PersistentAgent | None,
    result: dict[str, Any] | None = None,
) -> ReviewRequest:
    """Retire one current terminal non-release review without changing its decision."""

    review, deliverable, task = _review_closure_target(
        state,
        review_request_id=review_request_id,
    )
    if review.state not in _TERMINAL_REVIEW_STATES:
        raise ValueError("only a terminal review may be retired")
    if actor is not None and not (
        _is_project_lead_for(state, actor.id, task.project_id)
        or _is_team_manager_for_task_project(state, actor=actor, task=task)
    ):
        raise ValueError(
            f"agent {actor.id} must be the project lead or team manager "
            f"to retire review {review.id}"
        )
    _close_review_attempt(
        state,
        review=review,
        deliverable=deliverable,
        task=task,
        reason=reason,
        retired_by=retired_by,
        action="retired",
        actor_agent_id=actor.id if actor is not None else None,
        result=result,
    )
    return review


def _review_closure_target(
    state: TeamState,
    *,
    review_request_id: str,
) -> tuple[ReviewRequest, Deliverable, AgentTask]:
    review = _require_present(
        state.review_requests,
        _required_text(review_request_id, "review_request_id"),
        "review request",
    )
    if review.release_package_id is not None:
        raise ValueError("release reviews must be recovered through release gates")
    if review.superseded_by_review_request_id is not None:
        raise ValueError(
            f"review {review.id} is already superseded by "
            f"{review.superseded_by_review_request_id}"
        )
    if review.retired_at is not None:
        raise ValueError(f"review {review.id} is already retired")
    deliverable = _require_present(
        state.deliverables,
        review.deliverable_id,
        "deliverable",
    )
    task = _require_present(state.tasks, deliverable.task_id, "task")
    return review, deliverable, task


def _close_review_attempt(
    state: TeamState,
    *,
    review: ReviewRequest,
    deliverable: Deliverable,
    task: AgentTask,
    reason: str,
    retired_by: str,
    action: str,
    actor_agent_id: str | None,
    result: dict[str, Any] | None,
) -> None:
    normalized_reason = _required_text(reason, "reason")
    normalized_actor = _required_text(retired_by, "retired_by")
    if _has_sealed_review_cohort(deliverable):
        current_cohort_reviews = _current_deliverable_reviews(state, deliverable)
        if (
            len(current_cohort_reviews) == 1
            and current_cohort_reviews[0].id == review.id
        ):
            raise ValueError(
                f"review {review.id} is the final active member of sealed cohort "
                f"{deliverable.review_cohort_id}; replace/retry that cohort slot "
                "or supersede the deliverable with a new reviewed candidate"
            )
    now = utc_now()
    review.retired_at = now
    review.retired_by = normalized_actor
    review.retirement_reason = normalized_reason
    closed_inbox_ids = _close_review_inbox_items(
        state,
        review_request_id=review.id,
        reviewer_agent_id=review.reviewer_agent_id,
        now=now,
    )
    if result is not None:
        result.setdefault("closed_inbox_item_ids", []).extend(closed_inbox_ids)
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
        source_id=review.id,
        actor_agent_id=actor_agent_id,
        dispatch_id=None,
        note=f"Review attempt {action} by {normalized_actor}: {normalized_reason}",
        result=result,
        now=now,
    )
    if deliverable.superseded_by_deliverable_id is None:
        _recalculate_deliverable_after_review_closure(
            state,
            closed_review=review,
            deliverable=deliverable,
            task=task,
            sender_agent_id=actor_agent_id or deliverable.producer_agent_id,
            result=result,
            now=now,
        )


def _close_review_inbox_items(
    state: TeamState,
    *,
    review_request_id: str,
    reviewer_agent_id: str,
    now: datetime,
) -> list[str]:
    closed_ids: list[str] = []
    for item in state.inbox.values():
        if (
            item.state == TeamInboxItemState.PENDING
            and item.recipient_agent_id == reviewer_agent_id
            and item.payload.get("kind") in _REVIEW_ASSIGNMENT_INBOX_KINDS
            and item.payload.get("review_request_id") == review_request_id
        ):
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = now
            closed_ids.append(item.id)
    return sorted(closed_ids)


def _current_deliverable_reviews(
    state: TeamState,
    deliverable: Deliverable,
) -> list[ReviewRequest]:
    return [
        state.review_requests[review_id]
        for review_id in deliverable.review_request_ids
        if review_id in state.review_requests
        and state.review_requests[review_id].superseded_by_review_request_id is None
        and state.review_requests[review_id].retired_at is None
        and (
            deliverable.review_cohort_id is None
            or state.review_requests[review_id].review_cohort_id
            == deliverable.review_cohort_id
        )
    ]


def _has_sealed_review_cohort(deliverable: Deliverable) -> bool:
    return (
        deliverable.review_cohort_id is not None
        and deliverable.review_cohort_sealed_at is not None
    )


def _recalculate_sealed_review_cohort(
    state: TeamState,
    *,
    closed_review: ReviewRequest,
    deliverable: Deliverable,
    task: AgentTask,
    sender_agent_id: str,
    result: dict[str, Any] | None,
    now: datetime,
) -> None:
    """Advance one immutable deliverable only after its sealed cohort resolves."""

    current_reviews = _current_deliverable_reviews(state, deliverable)
    if any(review.state == TeamRecordState.BLOCKED for review in current_reviews):
        if closed_review.state == TeamRecordState.BLOCKED:
            reviewer = _require_present(
                state.agents,
                closed_review.reviewer_agent_id,
                "reviewer",
            )
            open_or_get_blocker(
                state,
                source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                source_id=closed_review.id,
                summary=f"Review {closed_review.id} is blocked",
                details=(
                    ("; ".join(closed_review.findings) or "Reviewer cannot complete the review.")
                    + " Retry this valid cohort member after recovery; a project "
                    "lead or team manager may retire it only if the attempt is erroneous."
                ),
                reporter_agent_id=reviewer.id,
                team_id=reviewer.team_id,
                project_id=task.project_id,
                task_id=task.id,
                result=result,
                now=now,
            )
        deliverable.state = TeamRecordState.BLOCKED
        _set_task_state(task, TeamTaskState.BLOCKED, now=now)
        task.updated_at = now
        return
    if any(review.state == TeamRecordState.PENDING for review in current_reviews):
        deliverable.state = TeamRecordState.PENDING
        _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        task.updated_at = now
        return

    rejected_reviews = [
        review
        for review in current_reviews
        if review.state == TeamRecordState.REJECTED
    ]
    cohort_review_ids = {
        review_id
        for review_id in deliverable.review_request_ids
        if (review := state.review_requests.get(review_id)) is not None
        and review.review_cohort_id == deliverable.review_cohort_id
    }
    open_findings = [
        finding.model_dump(mode="json")
        for finding in deliverable.carried_findings
        if finding.state == "open"
        and finding.source_review_request_id in cohort_review_ids
    ]
    if rejected_reviews or open_findings:
        deliverable.state = TeamRecordState.REJECTED
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        task.updated_at = now
        rejected_review_ids = sorted(
            {review.id for review in rejected_reviews}
            | {
                finding["source_review_request_id"]
                for finding in open_findings
            }
        )
        rejection_source_review_id = rejected_review_ids[0]
        rejection_source_review = state.review_requests.get(
            rejection_source_review_id
        )
        rejection_sender_agent_id = (
            rejection_source_review.reviewer_agent_id
            if rejection_source_review is not None
            else sender_agent_id
        )
        findings = [finding["text"] for finding in open_findings]
        existing_rework = next(
            (
                item
                for item in state.inbox.values()
                if item.state == TeamInboxItemState.PENDING
                and item.recipient_agent_id == task.owner_agent_id
                and item.task_id == task.id
                and item.payload.get("kind") == "review_rejected"
                and item.payload.get("review_cohort_id")
                == deliverable.review_cohort_id
            ),
            None,
        )
        if existing_rework is None:
            queue_actionable_inbox_item(
                state,
                recipient_agent_id=task.owner_agent_id,
                sender_agent_id=rejection_sender_agent_id,
                task_id=task.id,
                priority=TeamInboxPriority.NORMAL,
                payload={
                    "kind": "review_rejected",
                    "review_request_id": rejection_source_review_id,
                    "review_request_ids": rejected_review_ids,
                    "review_cohort_id": deliverable.review_cohort_id,
                    "deliverable_id": deliverable.id,
                    "supersedes_deliverable_id": deliverable.id,
                    "task_id": task.id,
                    "summary": (
                        "The sealed review cohort requested changes; revise once "
                        "against the complete cohort findings and resubmit."
                    ),
                    "findings": findings,
                    "open_findings": open_findings,
                    "next_action": (
                        "Submit one replacement deliverable with "
                        f"supersedes_deliverable_id set to {deliverable.id}; "
                        "its open cohort findings carry forward."
                    ),
                    "artifact_refs": list(deliverable.artifact_refs),
                },
                result=result,
                now=now,
            )
            queue_parent_task_transition(
                state,
                child_task_id=task.id,
                transition="rejection",
                sender_agent_id=rejection_sender_agent_id,
                result=result,
                now=now,
                references={
                    "deliverable_id": deliverable.id,
                    "review_request_id": rejection_source_review_id,
                    "review_cohort_id": deliverable.review_cohort_id,
                },
            )
        return

    if current_reviews and all(
        review.state == TeamRecordState.ACCEPTED for review in current_reviews
    ):
        if any(finding.state == "open" for finding in deliverable.carried_findings):
            deliverable.state = TeamRecordState.PENDING
            _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        else:
            try:
                require_deliverable_acceptance(state, deliverable.id)
            except TaskAssessmentBoundaryError as exc:
                if result is not None:
                    result["deliverable_acceptance_deferred"] = {
                        "code": exc.code,
                        **exc.correction_context,
                    }
            else:
                _accept_deliverable_and_maybe_task(
                    state,
                    deliverable=deliverable,
                    task=task,
                    now=now,
                    result=result,
                    accepted_by_agent_id=sender_agent_id,
                )
        task.updated_at = now
        return

    # Every cohort member was explicitly retired without a replacement. Preserve
    # that audit trail, but do not invent an approval.
    deliverable.state = TeamRecordState.PENDING
    deliverable.acceptance_recipient_agent_id = None
    _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
    task.updated_at = now


def _recalculate_deliverable_after_review_closure(
    state: TeamState,
    *,
    closed_review: ReviewRequest,
    deliverable: Deliverable,
    task: AgentTask,
    sender_agent_id: str,
    result: dict[str, Any] | None,
    now: datetime,
) -> None:
    if _has_sealed_review_cohort(deliverable):
        _recalculate_sealed_review_cohort(
            state,
            closed_review=closed_review,
            deliverable=deliverable,
            task=task,
            sender_agent_id=sender_agent_id,
            result=result,
            now=now,
        )
        return
    current_reviews = _current_deliverable_reviews(state, deliverable)
    active_acceptance_blocker_ids = _active_deliverable_acceptance_blocker_ids(
        state,
        deliverable=deliverable,
        task=task,
    )
    if any(review.state == TeamRecordState.BLOCKED for review in current_reviews):
        deliverable.state = TeamRecordState.BLOCKED
        _set_task_state(task, TeamTaskState.BLOCKED, now=now)
    elif any(review.state == TeamRecordState.REJECTED for review in current_reviews):
        deliverable.state = TeamRecordState.REJECTED
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
    elif current_reviews and all(
        review.state == TeamRecordState.ACCEPTED for review in current_reviews
    ):
        if any(finding.state == "open" for finding in deliverable.carried_findings):
            deliverable.state = TeamRecordState.PENDING
            _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        elif active_acceptance_blocker_ids:
            deliverable.state = TeamRecordState.PENDING
            _set_task_state(task, TeamTaskState.BLOCKED, now=now)
        else:
            require_deliverable_acceptance(state, deliverable.id)
            _accept_deliverable_and_maybe_task(
                state,
                deliverable=deliverable,
                task=task,
                now=now,
                result=result,
                accepted_by_agent_id=sender_agent_id,
            )
    elif current_reviews:
        deliverable.state = TeamRecordState.PENDING
        _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
    elif any(finding.state == "open" for finding in deliverable.carried_findings):
        deliverable.state = TeamRecordState.PENDING
        deliverable.acceptance_recipient_agent_id = None
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        _restore_owner_rework_after_review_closure(
            state,
            closed_review=closed_review,
            deliverable=deliverable,
            task=task,
            sender_agent_id=sender_agent_id,
            result=result,
            now=now,
        )
    elif (
        deliverable.acceptance_decision is not None
        and deliverable.acceptance_decision.decision == "accepted"
    ):
        if active_acceptance_blocker_ids:
            deliverable.state = TeamRecordState.PENDING
            _set_task_state(task, TeamTaskState.BLOCKED, now=now)
        else:
            require_deliverable_acceptance(state, deliverable.id)
            _accept_deliverable_and_maybe_task(
                state,
                deliverable=deliverable,
                task=task,
                now=now,
                result=result,
                accepted_by_agent_id=sender_agent_id,
            )
    elif active_acceptance_blocker_ids:
        deliverable.state = TeamRecordState.PENDING
        _set_task_state(task, TeamTaskState.BLOCKED, now=now)
    else:
        _route_to_superior_acceptance(
            state,
            deliverable=deliverable,
            task=task,
            sender_agent_id=sender_agent_id,
            result=result,
            now=now,
        )
    task.updated_at = now


def _restore_owner_rework_after_review_closure(
    state: TeamState,
    *,
    closed_review: ReviewRequest,
    deliverable: Deliverable,
    task: AgentTask,
    sender_agent_id: str,
    result: dict[str, Any] | None,
    now: datetime,
) -> None:
    owner_agent_id = task.owner_agent_id
    existing = next(
        (
            item
            for item in state.inbox.values()
            if item.state == TeamInboxItemState.PENDING
            and item.item_type == TeamInboxItemType.STATUS_REQUEST
            and item.recipient_agent_id == owner_agent_id
            and item.task_id == task.id
            and item.payload.get("deliverable_id") == deliverable.id
            and item.payload.get("kind")
            in {
                "review_rejected",
                "review_rework_restored",
            }
        ),
        None,
    )
    if existing is not None:
        _mark_agent_work_due(state, agent_id=owner_agent_id, now=now)
        return
    queue_actionable_inbox_item(
        state,
        recipient_agent_id=owner_agent_id,
        sender_agent_id=sender_agent_id,
        task_id=task.id,
        priority=TeamInboxPriority.NORMAL,
        payload={
            "kind": "review_rework_restored",
            "review_request_id": closed_review.id,
            "review_state": closed_review.state.value,
            "retirement_reason": closed_review.retirement_reason,
            "deliverable_id": deliverable.id,
            "task_id": task.id,
            "summary": "Review closed with open findings; obtain a resolving review.",
            "open_findings": [
                finding.model_dump(mode="json")
                for finding in deliverable.carried_findings
                if finding.state == "open"
            ],
            "next_action": (
                f"Request a new review for deliverable {deliverable.id} and resolve "
                "every carried finding with evidence; use an explicit replacement "
                "path if the deliverable itself must change."
            ),
            "artifact_refs": list(deliverable.artifact_refs),
        },
        result=result,
        now=now,
    )


def _route_to_superior_acceptance(
    state: TeamState,
    *,
    deliverable: Deliverable,
    task: AgentTask,
    sender_agent_id: str,
    result: dict[str, Any] | None,
    now: datetime,
) -> None:
    producer = _require_present(
        state.agents,
        deliverable.producer_agent_id,
        "deliverable producer",
    )
    parent_task = parent_task_for_child(state, task)
    recipient_id = task_acceptance_recipient_agent_id(
        state,
        task=task,
        producer_agent_id=producer.id,
    )
    deliverable.state = TeamRecordState.PENDING
    deliverable.acceptance_recipient_agent_id = recipient_id
    if recipient_id is None:
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        return
    _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
    existing = next(
        (
            item
            for item in state.inbox.values()
            if item.state == TeamInboxItemState.PENDING
            and item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
            and item.payload.get("deliverable_id") == deliverable.id
            and item.recipient_agent_id == recipient_id
        ),
        None,
    )
    if existing is not None:
        _mark_agent_work_due(state, agent_id=recipient_id, now=now)
        return
    payload: dict[str, Any] = {
        "kind": "superior_acceptance_requested",
        "deliverable_id": deliverable.id,
        "task_id": task.id,
        "producer_agent_id": producer.id,
        "acceptance_recipient_agent_id": recipient_id,
        "summary": deliverable.summary,
        "artifact_refs": list(deliverable.artifact_refs),
        "available_decisions": ["accepted", "rejected"],
        "available_alternatives": ["request_review", "report_blocker"],
    }
    if parent_task is not None:
        payload.update(
            {
                "transition": "handoff",
                "parent_task_id": parent_task.id,
                "child_task_id": task.id,
            }
        )
    verification_payload = _deliverable_verification_payload(deliverable)
    if verification_payload is not None:
        payload["verification"] = verification_payload
    item = AgentInboxItem(
        id=f"inbox-{uuid4().hex}",
        recipient_agent_id=recipient_id,
        sender_agent_id=sender_agent_id,
        task_id=task.id,
        item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
        response_requested=True,
        priority=TeamInboxPriority.LOW,
        payload=payload,
        created_at=now,
        updated_at=now,
    )
    state.inbox[item.id] = item
    if result is not None:
        result.setdefault("created_inbox_item_ids", []).append(item.id)
    _mark_agent_work_due(state, agent_id=recipient_id, now=now)
    if parent_task is not None and recipient_id != parent_task.owner_agent_id:
        queue_parent_task_transition(
            state,
            child_task_id=task.id,
            transition="handoff",
            sender_agent_id=sender_agent_id,
            result=result,
            now=now,
            references={"deliverable_id": deliverable.id},
        )


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
    if task is not None and task.state in {
        TeamTaskState.ACCEPTED,
        TeamTaskState.FINISHED,
    }:
        _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        task.updated_at = now
        reopened = True
    if reopened and task is not None and projects_to_recalc is not None:
        projects_to_recalc.add(task.project_id)
    return reopened


def _align_task_with_sealed_review_cohort(
    *,
    deliverable: Deliverable,
    task: AgentTask | None,
    now: datetime,
    projects_to_recalc: set[str] | None = None,
) -> None:
    changed = _reopen_accepted_deliverable_for_pending_review(
        deliverable=deliverable,
        task=task,
        now=now,
        projects_to_recalc=projects_to_recalc,
    )
    if task is not None and task.state in {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
    }:
        changed = (
            _set_task_state(
                task,
                TeamTaskState.WAITING_FOR_REVIEW,
                now=now,
            )
            or changed
        )
        task.updated_at = now
    if changed and task is not None and projects_to_recalc is not None:
        projects_to_recalc.add(task.project_id)


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
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
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
    if review.retired_at is not None:
        action = (
            "withdrawn"
            if review.state == TeamRecordState.PENDING
            else "retired"
        )
        result["errors"].append(
            f"review {review_request_id} was {action} and cannot receive a decision"
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
            if review.release_package_id is not None and review.state == TeamRecordState.BLOCKED
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
    raw_block_kind = _optional_text(decision.get("block_kind"))
    if decision_state == TeamRecordState.BLOCKED:
        try:
            block_kind = ReviewBlockKind(
                raw_block_kind or ReviewBlockKind.UNSPECIFIED.value
            )
        except ValueError:
            result["errors"].append(
                "review decision block_kind must be dependency, input_unavailable, "
                "policy, system, or unspecified"
            )
            return None
    elif raw_block_kind is not None:
        result["errors"].append(
            "review decision block_kind is only allowed when state is blocked"
        )
        return None
    else:
        block_kind = None
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
    review.block_kind = block_kind
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
    if (
        decision_state == TeamRecordState.ACCEPTED
        and review.deliverable_id is not None
        and review.review_profile_id is not None
    ):
        deliverable = state.deliverables.get(review.deliverable_id)
        if deliverable is not None:
            delivered_outcome_kinds = {
                outcome.kind for outcome in deliverable.outcomes
            }
            missing_outcome_kinds = sorted(
                {
                    definition.kind
                    for definition in review.required_outcome_kind_definitions
                }
                - delivered_outcome_kinds
            )
            if missing_outcome_kinds:
                return (
                    f"accepted review {review.id} is missing required deliverable "
                    "outcome kinds: "
                    + ", ".join(missing_outcome_kinds)
                )
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
            if criterion.required and by_id[criterion.id].state != "passed"
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
            return f"finding_resolutions[{index}] requires finding_id and a non-empty resolution"
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
            return f"accepted review {review.id} must resolve its carried findings: " + ", ".join(
                missing_resolutions
            )
    return None


def _review_owns_finding(
    state: TeamState,
    *,
    review: ReviewRequest,
    finding: ReviewFindingRecord,
) -> bool:
    current: ReviewRequest | None = review
    seen: set[str] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        if current.id == finding.source_review_request_id:
            return True
        predecessor_id = current.supersedes_review_request_id
        current = (
            state.review_requests.get(predecessor_id)
            if predecessor_id is not None
            else None
        )
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
    if _has_sealed_review_cohort(deliverable):
        _recalculate_sealed_review_cohort(
            state,
            closed_review=review,
            deliverable=deliverable,
            task=task,
            sender_agent_id=review.reviewer_agent_id,
            result=result,
            now=now,
        )
        projects_to_recalc.add(task.project_id)
        return
    if decision_state == TeamRecordState.ACCEPTED:
        current_reviews = [
            state.review_requests[review_id]
            for review_id in deliverable.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
        ]
        all_accepted = bool(current_reviews) and all(
            current.state == TeamRecordState.ACCEPTED for current in current_reviews
        )
        no_open_findings = all(
            finding.state == "resolved" for finding in deliverable.carried_findings
        )
        if all_accepted and no_open_findings:
            try:
                require_deliverable_acceptance(state, deliverable.id)
            except TaskAssessmentBoundaryError as exc:
                # A reviewer's decision is independent evidence. Persist it even
                # when another boundary keeps the aggregate deliverable and task
                # from advancing.
                if result is not None:
                    result["deliverable_acceptance_deferred"] = {
                        "code": exc.code,
                        **exc.correction_context,
                    }
            else:
                _accept_deliverable_and_maybe_task(
                    state,
                    deliverable=deliverable,
                    task=task,
                    now=now,
                    result=result,
                    accepted_by_agent_id=review.reviewer_agent_id,
                )
    elif decision_state == TeamRecordState.REJECTED:
        deliverable.state = TeamRecordState.REJECTED
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        task.updated_at = now
        rework_agent_id = task.owner_agent_id
        queue_actionable_inbox_item(
            state,
            recipient_agent_id=rework_agent_id,
            sender_agent_id=review.reviewer_agent_id,
            task_id=task.id,
            priority=TeamInboxPriority.NORMAL,
            payload={
                "kind": "review_rejected",
                "review_request_id": review.id,
                "deliverable_id": deliverable.id,
                "supersedes_deliverable_id": deliverable.id,
                "task_id": task.id,
                "summary": "Review rejected; revise and resubmit.",
                "findings": list(findings),
                "open_findings": [
                    finding.model_dump(mode="json")
                    for finding in deliverable.carried_findings
                    if finding.state == "open"
                ],
                "next_action": (
                    "Submit a new deliverable with supersedes_deliverable_id set "
                    f"to {deliverable.id}; its open findings carry forward."
                ),
                "artifact_refs": list(deliverable.artifact_refs),
            },
            result=result,
            now=now,
        )
        queue_parent_task_transition(
            state,
            child_task_id=task.id,
            transition="rejection",
            sender_agent_id=review.reviewer_agent_id,
            result=result,
            now=now,
            references={
                "deliverable_id": deliverable.id,
                "review_request_id": review.id,
            },
        )
    else:  # blocked
        deliverable.state = TeamRecordState.BLOCKED
        _set_task_state(task, TeamTaskState.BLOCKED, now=now)
        task.updated_at = now
        reviewer = _require_present(state.agents, review.reviewer_agent_id, "reviewer")
        open_or_get_blocker(
            state,
            source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
            source_id=review.id,
            summary=f"Review {review.id} is blocked",
            details=(
                ("; ".join(findings) or "Reviewer cannot complete the review.")
                + " Retry this valid review after recovery; a project lead or "
                "team manager may retire it only if the attempt is erroneous."
            ),
            reporter_agent_id=reviewer.id,
            team_id=reviewer.team_id,
            project_id=task.project_id,
            task_id=task.id,
            result=result,
            now=now,
        )
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
        reviewer = _require_present(state.agents, review.reviewer_agent_id, "reviewer")
        open_or_get_blocker(
            state,
            source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
            source_id=review.id,
            summary=f"Release review {review.id} is blocked",
            details=release.error or "Reviewer cannot complete the release review.",
            reporter_agent_id=reviewer.id,
            team_id=reviewer.team_id,
            project_id=release.project_id,
            result=result,
            now=release.updated_at,
        )
        projects_to_recalc.add(release.project_id)
        return

    from .release_gates import advance_release_gates

    advance_release_gates(
        state,
        release=release,
        result=result if result is not None else {"events": []},
    )
    release.updated_at = utc_now()
    projects_to_recalc.add(release.project_id)


__all__ = [
    "_review_profile_for_request",
    "_apply_review_request",
    "_reopen_accepted_deliverable_for_pending_review",
    "withdraw_pending_review",
    "retire_terminal_review",
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
