from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from ...models import (
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ReleaseCandidateDeliverableEvidence,
    ReleaseCandidateManifest,
    ReleaseCandidateReviewEvidence,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseVerificationCommandResult,
    ReleaseVerificationRun,
    ReviewRequest,
    ReviewProfile,
    TeamBlockerSourceKind,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
)
from .common import _append_unique, _required_text, utc_now
from .blockers import open_release_blocker, resolve_active_blockers_for_source
from .review_handoff import queue_actionable_inbox_item
from .outcome_catalog import snapshot_review_profile_outcomes


RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE = "team.release.verification_requested"


def _release_review_scope_error(
    *,
    project: Any,
    reviewer: Any,
    profile: Any,
    reviewer_agent_id: str,
    review_profile_id: str,
) -> str | None:
    if reviewer is None or reviewer.team_id != project.team_id:
        return f"release reviewer {reviewer_agent_id} is missing or outside team {project.team_id}"
    if profile is None or profile.team_id != project.team_id or profile.archived:
        return f"release review profile {review_profile_id} is unavailable"
    if profile.project_id not in {None, project.id}:
        return f"release review profile {profile.id} does not apply to project {project.id}"
    return None


def _create_release_review(
    state: TeamState,
    *,
    release: ReleasePackage,
    reviewer: PersistentAgent,
    profile: ReviewProfile,
    candidate_commit_sha: str,
    instructions: str,
    result: dict[str, Any],
    now: datetime,
    supersedes: ReviewRequest | None = None,
    retry_reason: str | None = None,
    retry_requested_by: str | None = None,
) -> ReviewRequest:
    """Create the durable review record shared by initial and retry handoffs."""

    review = ReviewRequest(
        id=f"release-review-{release.id}-{uuid4().hex}",
        release_package_id=release.id,
        candidate_commit_sha=candidate_commit_sha,
        reviewer_agent_id=reviewer.id,
        review_profile_id=profile.id,
        criteria_snapshot=[criterion.model_copy(deep=True) for criterion in profile.criteria],
        required_outcome_kind_definitions=snapshot_review_profile_outcomes(
            state,
            profile=profile,
        ),
        reviewed_commit_sha=candidate_commit_sha,
        state=TeamRecordState.PENDING,
        supersedes_review_request_id=supersedes.id if supersedes is not None else None,
        retry_reason=retry_reason,
        retry_requested_by=retry_requested_by,
        instructions=instructions,
        created_at=now,
    )
    if supersedes is not None:
        supersedes.superseded_by_review_request_id = review.id
    state.review_requests[review.id] = review
    release.review_request_ids = _append_unique(release.review_request_ids, review.id)
    result.setdefault("created_review_request_ids", []).append(review.id)
    return review


def _queue_release_review_handoff(
    state: TeamState,
    *,
    release: ReleasePackage,
    project: Project,
    reviewer: PersistentAgent,
    profile: ReviewProfile,
    review: ReviewRequest,
    result: dict[str, Any],
    now: datetime,
) -> None:
    """Deliver a release review through the common review inbox lifecycle."""

    payload: dict[str, Any] = {
        "kind": "release_review_request",
        "review_request_id": review.id,
        "release_package_id": release.id,
        "candidate_commit_sha": review.candidate_commit_sha,
        "release_manifest": (
            release.manifest.model_dump(mode="json") if release.manifest is not None else None
        ),
        "review_profile": {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "criteria": [criterion.model_dump(mode="json") for criterion in profile.criteria],
            "required_outcome_kinds": list(profile.required_outcome_kinds),
            "outcome_kind_definitions": [
                definition.model_dump(mode="json")
                for definition in review.required_outcome_kind_definitions
            ],
        },
    }
    if review.supersedes_review_request_id is not None:
        payload["supersedes_review_request_id"] = review.supersedes_review_request_id
    if review.retry_reason is not None:
        payload["retry_reason"] = review.retry_reason
    if review.retry_requested_by is not None:
        payload["retry_requested_by"] = review.retry_requested_by
    queue_actionable_inbox_item(
        state,
        recipient_agent_id=reviewer.id,
        sender_agent_id=project.lead_agent_id,
        priority=TeamInboxPriority.HIGH,
        payload=payload,
        result=result,
        now=now,
    )


def begin_release_gates(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    candidate_commit_sha: str,
    candidate_tree_hash: str | None,
    result: dict[str, Any],
) -> None:
    project = state.projects.get(release.project_id)
    if project is None:
        _reject_release(
            release,
            stage="configuration",
            kind="configuration",
            error=f"release project {release.project_id} does not exist",
            result=result,
        )
        return
    policy = project.release_policy
    if policy is None:
        _reject_release(
            release,
            stage="configuration",
            kind="configuration",
            error=(
                "git release policy is not configured; recreate the release after configuring "
                "verification and review gates"
            ),
            result=result,
        )
        return
    if release.candidate_commit_sha not in {None, candidate_commit_sha}:
        _reject_release(
            release,
            stage="configuration",
            kind="configuration",
            error=(
                f"release candidate is already pinned to {release.candidate_commit_sha}, not "
                f"{candidate_commit_sha}"
            ),
            result=result,
        )
        return

    release.candidate_commit_sha = candidate_commit_sha
    release.candidate_tree_hash = candidate_tree_hash
    unchanged_release = _unchanged_failed_release(
        state,
        release=release,
        candidate_commit_sha=candidate_commit_sha,
        candidate_tree_hash=candidate_tree_hash,
    )
    if unchanged_release is not None:
        _reject_release(
            release,
            stage="verification",
            kind="candidate",
            error=(
                f"release candidate content is unchanged from failed release "
                f"{unchanged_release.id}; submit changed reviewed content"
            ),
            result=result,
        )
        return
    try:
        release.manifest = _candidate_manifest(
            state,
            release=release,
            release_promotion=release_promotion,
        )
    except ValueError as exc:
        _reject_release(
            release,
            stage="configuration",
            kind="configuration",
            error=str(exc),
            result=result,
        )
        return
    release.gate_failure = None
    release.updated_at = utc_now()

    if policy.verification_commands:
        run_id = _verification_run_id(release.id, candidate_commit_sha, 1)
        run = state.release_verification_runs.get(run_id)
        if run is None:
            run = ReleaseVerificationRun(
                id=run_id,
                release_package_id=release.id,
                candidate_commit_sha=candidate_commit_sha,
                session_id=f"team-release-verification-{uuid4().hex}",
                attempt_number=1,
                command_results=[
                    ReleaseVerificationCommandResult(name=command.name)
                    for command in policy.verification_commands
                ],
            )
            state.release_verification_runs[run.id] = run
        release.verification_run_ids = _append_unique(
            release.verification_run_ids,
            run.id,
        )
        _append_verification_requested_event(
            result,
            release=release,
            run=run,
        )
        result.setdefault("release_verification_run_ids", []).append(run.id)
        return

    create_release_reviews_or_approve(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def record_release_verification_result(
    state: TeamState,
    *,
    release_package_id: str,
    verification_run_id: str,
    candidate_commit_sha: str,
    verification_state: str,
    failure_kind: str | None,
    command_results: list[ReleaseVerificationCommandResult],
    error: str | None,
    result: dict[str, Any],
) -> None:
    release = state.release_packages.get(release_package_id)
    run = state.release_verification_runs.get(verification_run_id)
    if release is None or run is None:
        raise ValueError("release package or verification run does not exist")
    if release.candidate_commit_sha != candidate_commit_sha:
        raise ValueError(
            f"release {release.id} is pinned to {release.candidate_commit_sha}, not "
            f"{candidate_commit_sha}"
        )
    if run.candidate_commit_sha != candidate_commit_sha:
        raise ValueError(
            f"verification run {run.id} is pinned to {run.candidate_commit_sha}, not "
            f"{candidate_commit_sha}"
        )
    if run.state in {"passed", "failed", "timed_out"}:
        result["verification_state"] = run.state
        return
    if verification_state not in {"passed", "failed", "timed_out"}:
        raise ValueError(f"invalid terminal release verification state {verification_state}")
    normalized_failure_kind = failure_kind
    if verification_state == "timed_out":
        normalized_failure_kind = "timeout"
    elif verification_state == "failed" and normalized_failure_kind is None:
        normalized_failure_kind = "candidate"
    if verification_state == "passed" and normalized_failure_kind is not None:
        raise ValueError("passed release verification cannot have failure_kind")
    if verification_state != "passed" and normalized_failure_kind not in {
        "candidate",
        "infrastructure",
        "timeout",
    }:
        raise ValueError("failed release verification requires a valid failure_kind")

    now = utc_now()
    run.state = verification_state
    run.failure_kind = normalized_failure_kind
    run.command_results = command_results
    run.current_command_index = len(command_results)
    run.error = error
    run.updated_at = now
    run.finished_at = now
    result["verification_state"] = verification_state
    if verification_state != "passed":
        message = error or f"release verification {verification_state}"
        if normalized_failure_kind == "candidate":
            _reject_release(
                release,
                stage="verification",
                kind="candidate",
                source_id=run.id,
                error=message,
                result=result,
            )
        else:
            _block_release_gate(
                state,
                release,
                stage="verification",
                kind=("timeout" if normalized_failure_kind == "timeout" else "infrastructure"),
                source_id=run.id,
                error=message,
                result=result,
            )
        return

    release_promotion = _release_promotion(state, release)
    if release_promotion is None:
        _reject_release(
            release,
            stage="configuration",
            kind="configuration",
            error="release promotion record is missing",
            result=result,
        )
        return
    create_release_reviews_or_approve(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def create_release_reviews_or_approve(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    project = state.projects.get(release.project_id)
    manifest = release.manifest
    candidate = release.candidate_commit_sha
    if project is None or manifest is None or candidate is None:
        _reject_release(
            release,
            stage="configuration",
            kind="configuration",
            error="release gate configuration is incomplete",
            result=result,
        )
        return

    for requirement in manifest.review_requirements:
        existing = next(
            (
                state.review_requests[review_id]
                for review_id in reversed(release.review_request_ids)
                if review_id in state.review_requests
                and state.review_requests[review_id].superseded_by_review_request_id is None
                and state.review_requests[review_id].reviewer_agent_id
                == requirement.reviewer_agent_id
                and state.review_requests[review_id].review_profile_id
                == requirement.review_profile_id
            ),
            None,
        )
        if existing is not None:
            continue
        reviewer = state.agents.get(requirement.reviewer_agent_id)
        profile = state.review_profiles.get(requirement.review_profile_id)
        scope_error = _release_review_scope_error(
            project=project,
            reviewer=reviewer,
            profile=profile,
            reviewer_agent_id=requirement.reviewer_agent_id,
            review_profile_id=requirement.review_profile_id,
        )
        if scope_error is not None:
            _reject_release(
                release,
                stage="configuration",
                kind="configuration",
                error=scope_error,
                result=result,
            )
            return
        now = utc_now()
        review = _create_release_review(
            state,
            release=release,
            reviewer=reviewer,
            profile=profile,
            candidate_commit_sha=candidate,
            instructions=(
                f"Review release candidate {candidate} for release package {release.id}. "
                "Decide only the pinned candidate commit."
            ),
            result=result,
            now=now,
        )
        _queue_release_review_handoff(
            state,
            release=release,
            project=project,
            reviewer=reviewer,
            profile=profile,
            review=review,
            result=result,
            now=now,
        )

    if _current_release_reviews(state, release):
        release.updated_at = utc_now()
        return
    approve_release_candidate(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def approve_release_candidate(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    candidate = release.candidate_commit_sha
    if candidate is None:
        _reject_release(
            release,
            stage="configuration",
            kind="configuration",
            error="release candidate commit is missing",
            result=result,
        )
        return
    release.gate_failure = None
    release.approved_commit_sha = candidate
    release.updated_at = utc_now()
    from .git_promotion import _queue_canonical_push_after_release_success

    _queue_canonical_push_after_release_success(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def _release_promotion(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    matches = [
        state.git_promotions[promotion_id]
        for promotion_id in release.git_promotion_record_ids
        if promotion_id in state.git_promotions
        and state.git_promotions[promotion_id].promotion_kind == "release"
        and state.git_promotions[promotion_id].state == "succeeded"
    ]
    matches.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
    return matches[0] if matches else None


def retry_release_gate(
    state: TeamState,
    *,
    release: ReleasePackage,
    gate_kind: str,
    gate_id: str,
    reason: str,
    requested_by: str,
    result: dict[str, Any],
) -> None:
    if release.state != TeamRecordState.BLOCKED:
        raise ValueError(f"release {release.id} is not blocked")
    candidate = release.candidate_commit_sha
    if candidate is None:
        raise ValueError(f"release {release.id} has no pinned candidate")
    normalized_reason = _required_text(reason, "reason")
    normalized_requested_by = _required_text(requested_by, "requested_by")
    if gate_kind == "verification":
        current = _current_verification_run(state, release)
        if current is None or current.id != gate_id:
            raise ValueError("verification retry must target the current verification run")
        if current.state not in {"failed", "timed_out"} or current.failure_kind not in {
            "infrastructure",
            "timeout",
        }:
            raise ValueError("only infrastructure-failed or timed-out verification may be retried")
        verification_commands = release.manifest.verification_commands
        if not verification_commands:
            raise ValueError("release manifest has no verification commands")
        attempt_number = (
            max(
                (
                    state.release_verification_runs[run_id].attempt_number
                    for run_id in release.verification_run_ids
                    if run_id in state.release_verification_runs
                ),
                default=0,
            )
            + 1
        )
        run = ReleaseVerificationRun(
            id=_verification_run_id(release.id, candidate, attempt_number),
            release_package_id=release.id,
            candidate_commit_sha=candidate,
            session_id=f"team-release-verification-{uuid4().hex}",
            attempt_number=attempt_number,
            supersedes_verification_run_id=current.id,
            retry_reason=normalized_reason,
            retry_requested_by=normalized_requested_by,
            command_results=[
                ReleaseVerificationCommandResult(name=command.name)
                for command in verification_commands
            ],
        )
        current.superseded_by_verification_run_id = run.id
        state.release_verification_runs[run.id] = run
        release.verification_run_ids = _append_unique(release.verification_run_ids, run.id)
        _recompute_release_state_after_gate_retry(state, release)
        resolve_active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
            source_id=release.id,
            actor_agent_id=None,
            dispatch_id=None,
            note=f"Verification retry {run.id} was scheduled.",
            reference_ids=[run.id],
            result=result,
        )
        _append_verification_requested_event(result, release=release, run=run)
        result.setdefault("release_verification_run_ids", []).append(run.id)
        result["retried_gate_id"] = current.id
        result["replacement_gate_id"] = run.id
        result["retry_reason"] = normalized_reason
        result["retry_requested_by"] = normalized_requested_by
        return
    if gate_kind != "review":
        raise ValueError("gate_kind must be verification or review")
    current_reviews = _current_release_reviews(state, release)
    current = next((review for review in current_reviews if review.id == gate_id), None)
    if current is None:
        raise ValueError("review retry must target a current release review")
    if current.state != TeamRecordState.BLOCKED:
        raise ValueError("only a blocked release review may be retried")
    project = state.projects.get(release.project_id)
    profile = (
        state.review_profiles.get(current.review_profile_id)
        if current.review_profile_id is not None
        else None
    )
    reviewer = state.agents.get(current.reviewer_agent_id)
    if project is None:
        raise ValueError("release reviewer or review profile is unavailable")
    scope_error = _release_review_scope_error(
        project=project,
        reviewer=reviewer,
        profile=profile,
        reviewer_agent_id=current.reviewer_agent_id,
        review_profile_id=current.review_profile_id or "",
    )
    if scope_error is not None:
        raise ValueError(scope_error)
    now = utc_now()
    review = _create_release_review(
        state,
        release=release,
        reviewer=reviewer,
        profile=profile,
        candidate_commit_sha=candidate,
        supersedes=current,
        retry_reason=normalized_reason,
        retry_requested_by=normalized_requested_by,
        instructions=(
            f"Retry blocked review {current.id} for unchanged release candidate {candidate}. "
            f"Retry reason: {normalized_reason}"
        ),
        result=result,
        now=now,
    )
    _recompute_release_state_after_gate_retry(state, release)
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
        source_id=current.id,
        actor_agent_id=None,
        dispatch_id=None,
        note=f"Review retry {review.id} was scheduled.",
        reference_ids=[review.id],
        result=result,
    )
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=release.id,
        actor_agent_id=None,
        dispatch_id=None,
        note=f"Review retry {review.id} was scheduled.",
        reference_ids=[review.id],
        result=result,
    )
    _queue_release_review_handoff(
        state,
        release=release,
        project=project,
        reviewer=reviewer,
        profile=profile,
        review=review,
        result=result,
        now=now,
    )
    result["retried_gate_id"] = current.id
    result["replacement_gate_id"] = review.id
    result["retry_reason"] = normalized_reason
    result["retry_requested_by"] = normalized_requested_by


def _candidate_manifest(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
) -> ReleaseCandidateManifest:
    project = state.projects[release.project_id]
    policy = project.release_policy
    if policy is None or release.candidate_commit_sha is None:
        raise ValueError("release candidate policy or commit is missing")
    waivers = {waiver.deliverable_id: waiver for waiver in release.verification_waivers}
    deliverable_rows: list[ReleaseCandidateDeliverableEvidence] = []
    for deliverable_id in release.deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            raise ValueError(f"release deliverable {deliverable_id} is missing")
        accepted_reviews = [
            state.review_requests[review_id]
            for review_id in deliverable.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].state == TeamRecordState.ACCEPTED
        ]
        review_evidence = [
            ReleaseCandidateReviewEvidence(
                review_request_id=review.id,
                reviewer_agent_id=review.reviewer_agent_id,
                review_profile_id=review.review_profile_id,
                reviewed_commit_sha=_required_text(
                    review.reviewed_commit_sha,
                    f"review {review.id} reviewed_commit_sha",
                ),
                criteria=list(review.criteria),
            )
            for review in accepted_reviews
        ]
        reviewed_shas = {review.reviewed_commit_sha for review in review_evidence}
        if (
            deliverable.review_manifest is not None
            and reviewed_shas
            and deliverable.review_manifest.reviewed_commit_sha not in reviewed_shas
        ):
            raise ValueError(
                f"deliverable {deliverable.id} reviewed manifest commit "
                f"{deliverable.review_manifest.reviewed_commit_sha} does not match its "
                "accepted immutable review"
            )
        deliverable_rows.append(
            ReleaseCandidateDeliverableEvidence(
                deliverable_id=deliverable.id,
                artifact_refs=list(deliverable.artifact_refs),
                verification=deliverable.verification,
                verification_waiver=waivers.get(deliverable.id),
                accepted_reviews=review_evidence,
                review_manifest=deliverable.review_manifest,
                feature_branch=deliverable.git_refs.feature_branch,
                feature_commit_sha=deliverable.git_refs.feature_commit_sha,
            )
        )
    return ReleaseCandidateManifest(
        project_id=project.id,
        release_package_id=release.id,
        base_commit_sha=release.base_commit_sha,
        candidate_commit_sha=release.candidate_commit_sha,
        candidate_tree_hash=release.candidate_tree_hash,
        release_branch=release.release_branch or release_promotion.target_branch,
        deliverables=deliverable_rows,
        scope_validation=(
            release.scope_validation.model_copy(deep=True)
            if release.scope_validation is not None
            else None
        ),
        verification_commands=list(policy.verification_commands),
        review_requirements=list(policy.review_requirements),
    )


def _unchanged_failed_release(
    state: TeamState,
    *,
    release: ReleasePackage,
    candidate_commit_sha: str,
    candidate_tree_hash: str | None,
) -> ReleasePackage | None:
    deliverables = tuple(sorted(set(release.deliverable_ids)))
    candidates = [
        candidate
        for candidate in state.release_packages.values()
        if candidate.id != release.id
        and candidate.project_id == release.project_id
        and candidate.state == TeamRecordState.REJECTED
        and candidate.gate_failure is not None
        and candidate.gate_failure.kind in {"candidate", "rejected"}
        and tuple(sorted(set(candidate.deliverable_ids))) == deliverables
        and (
            (
                candidate_tree_hash is not None
                and candidate.candidate_tree_hash == candidate_tree_hash
            )
            or (
                candidate_tree_hash is None
                and candidate.candidate_commit_sha == candidate_commit_sha
            )
        )
    ]
    candidates.sort(key=lambda item: (item.updated_at or item.created_at, item.id), reverse=True)
    return candidates[0] if candidates else None


def _current_verification_run(
    state: TeamState,
    release: ReleasePackage,
) -> ReleaseVerificationRun | None:
    runs = [
        state.release_verification_runs[run_id]
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
        and state.release_verification_runs[run_id].superseded_by_verification_run_id is None
    ]
    runs.sort(key=lambda run: (run.attempt_number, run.created_at, run.id), reverse=True)
    return runs[0] if runs else None


def _current_release_reviews(state: TeamState, release: ReleasePackage) -> list[ReviewRequest]:
    return [
        state.review_requests[review_id]
        for review_id in release.review_request_ids
        if review_id in state.review_requests
        and state.review_requests[review_id].superseded_by_review_request_id is None
    ]


def current_release_gates_passed(state: TeamState, release: ReleasePackage) -> bool:
    current_run = _current_verification_run(state, release)
    verification_passed = current_run is None or current_run.state == "passed"
    reviews = _current_release_reviews(state, release)
    reviews_accepted = all(review.state == TeamRecordState.ACCEPTED for review in reviews)
    return verification_passed and reviews_accepted


def _append_verification_requested_event(
    result: dict[str, Any],
    *,
    release: ReleasePackage,
    run: ReleaseVerificationRun,
) -> None:
    result.setdefault("events", []).append(
        {
            "event_type": RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE,
            "correlation_key": run.id,
            "idempotency_key": (f"team-addon:{RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE}:{run.id}"),
            "payload": {
                "release_package_id": release.id,
                "verification_run_id": run.id,
                "candidate_commit_sha": run.candidate_commit_sha,
            },
        }
    )


def _recompute_release_state_after_gate_retry(
    state: TeamState,
    release: ReleasePackage,
) -> None:
    blocked_reviews = [
        review
        for review in _current_release_reviews(state, release)
        if review.state == TeamRecordState.BLOCKED
    ]
    if blocked_reviews:
        existing_source_id = (
            release.gate_failure.source_id if release.gate_failure is not None else None
        )
        blocker = next(
            (review for review in blocked_reviews if review.id == existing_source_id),
            blocked_reviews[0],
        )
        message = "; ".join(blocker.findings) or f"release review {blocker.id} blocked"
        release.state = TeamRecordState.BLOCKED
        release.gate_failure = ReleaseGateFailure(
            stage="review",
            kind="blocked",
            source_id=blocker.id,
            message=message,
        )
        release.error = message
        release.updated_at = utc_now()
        return

    release.state = TeamRecordState.PENDING
    release.gate_failure = None
    release.error = None
    release.updated_at = utc_now()


def _verification_run_id(
    release_id: str,
    candidate_commit_sha: str,
    attempt_number: int,
) -> str:
    return f"release-verification-{release_id}-{candidate_commit_sha[:16]}-attempt-{attempt_number}"


def _block_release_gate(
    state: TeamState,
    release: ReleasePackage,
    *,
    stage: str,
    kind: str,
    error: str,
    result: dict[str, Any],
    source_id: str | None = None,
) -> None:
    release.state = TeamRecordState.BLOCKED
    release.gate_failure = ReleaseGateFailure(
        stage=stage,
        kind=kind,
        source_id=source_id,
        message=error,
    )
    release.error = error
    release.updated_at = utc_now()
    result["error"] = error
    open_release_blocker(
        state,
        release_package_id=release.id,
        summary=f"Release {release.id} gate is blocked",
        details=error,
        result=result,
        now=release.updated_at,
    )


def _reject_release(
    release: ReleasePackage,
    *,
    stage: str,
    kind: str,
    error: str,
    result: dict[str, Any],
    source_id: str | None = None,
) -> None:
    release.state = TeamRecordState.REJECTED
    release.gate_failure = ReleaseGateFailure(
        stage=stage,
        kind=kind,
        source_id=source_id,
        message=error,
    )
    release.error = error
    release.updated_at = utc_now()
    result["error"] = error


__all__ = [
    "RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE",
    "approve_release_candidate",
    "begin_release_gates",
    "create_release_reviews_or_approve",
    "current_release_gates_passed",
    "record_release_verification_result",
    "retry_release_gate",
]
