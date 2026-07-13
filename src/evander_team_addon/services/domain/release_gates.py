from __future__ import annotations

from typing import Any
from uuid import uuid4

from ...models import (
    AgentInboxItem,
    GitPromotionRecord,
    ReleasePackage,
    ReleaseVerificationCommandResult,
    ReleaseVerificationRun,
    ReviewRequest,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
)
from .common import _append_unique, utc_now
from .wake import _append_agent_wake_requested_event_for_inbox_item
from .work_due import _mark_agent_work_due


RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE = "team.release.verification_requested"


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
        _block_release(release, f"release project {release.project_id} does not exist", result)
        return
    policy = project.release_policy
    if policy is None:
        _block_release(
            release,
            "git release policy is not configured; recreate the release after configuring "
            "verification and review gates",
            result,
        )
        return
    if release.candidate_commit_sha not in {None, candidate_commit_sha}:
        _block_release(
            release,
            f"release candidate is already pinned to {release.candidate_commit_sha}, not "
            f"{candidate_commit_sha}",
            result,
        )
        return

    release.candidate_commit_sha = candidate_commit_sha
    release.candidate_tree_hash = candidate_tree_hash
    release.manifest = {
        "project_id": project.id,
        "release_package_id": release.id,
        "candidate_commit_sha": candidate_commit_sha,
        "candidate_tree_hash": candidate_tree_hash,
        "deliverable_ids": list(release.deliverable_ids),
        "feature_branches": list(release.feature_branches),
        "release_branch": release.release_branch or release_promotion.target_branch,
        "verification_commands": [
            command.model_dump(mode="json") for command in policy.verification_commands
        ],
        "review_requirements": [
            requirement.model_dump(mode="json") for requirement in policy.review_requirements
        ],
    }
    release.updated_at = utc_now()

    if policy.verification_commands:
        run_id = _verification_run_id(release.id, candidate_commit_sha)
        run = state.release_verification_runs.get(run_id)
        if run is None:
            run = ReleaseVerificationRun(
                id=run_id,
                release_package_id=release.id,
                candidate_commit_sha=candidate_commit_sha,
                session_id=f"team-release-verification-{uuid4().hex}",
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
        result.setdefault("events", []).append(
            {
                "event_type": RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE,
                "correlation_key": run.id,
                "idempotency_key": (
                    f"team-addon:{RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE}:{run.id}"
                ),
                "payload": {
                    "release_package_id": release.id,
                    "verification_run_id": run.id,
                    "candidate_commit_sha": candidate_commit_sha,
                },
            }
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

    now = utc_now()
    run.state = verification_state
    run.command_results = command_results
    run.current_command_index = len(command_results)
    run.error = error
    run.updated_at = now
    run.finished_at = now
    result["verification_state"] = verification_state
    if verification_state != "passed":
        _block_release(
            release,
            error or f"release verification {verification_state}",
            result,
        )
        return

    release_promotion = _release_promotion(state, release)
    if release_promotion is None:
        _block_release(release, "release promotion record is missing", result)
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
    policy = project.release_policy if project is not None else None
    candidate = release.candidate_commit_sha
    if project is None or policy is None or candidate is None:
        _block_release(release, "release gate configuration is incomplete", result)
        return
    if release.review_request_ids:
        return

    for requirement in policy.review_requirements:
        reviewer = state.agents.get(requirement.reviewer_agent_id)
        profile = state.review_profiles.get(requirement.review_profile_id)
        if reviewer is None or reviewer.team_id != project.team_id:
            _block_release(
                release,
                f"release reviewer {requirement.reviewer_agent_id} is missing or outside "
                f"team {project.team_id}",
                result,
            )
            return
        if profile is None or profile.team_id != project.team_id or profile.archived:
            _block_release(
                release,
                f"release review profile {requirement.review_profile_id} is unavailable",
                result,
            )
            return
        if profile.project_id not in {None, project.id}:
            _block_release(
                release,
                f"release review profile {profile.id} does not apply to project {project.id}",
                result,
            )
            return
        review_id = f"release-review-{release.id}-{uuid4().hex}"
        review = ReviewRequest(
            id=review_id,
            release_package_id=release.id,
            candidate_commit_sha=candidate,
            reviewer_agent_id=reviewer.id,
            review_profile_id=profile.id,
            reviewed_commit_sha=candidate,
            state=TeamRecordState.PENDING,
            instructions=(
                f"Review release candidate {candidate} for release package {release.id}. "
                "Decide only the pinned candidate commit."
            ),
            created_at=utc_now(),
        )
        state.review_requests[review.id] = review
        release.review_request_ids = _append_unique(release.review_request_ids, review.id)
        inbox_id = f"inbox-{uuid4().hex}"
        item = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=reviewer.id,
            sender_agent_id=project.lead_agent_id,
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=True,
            priority=TeamInboxPriority.HIGH,
            payload={
                "kind": "release_review_request",
                "review_request_id": review.id,
                "release_package_id": release.id,
                "candidate_commit_sha": candidate,
                "release_manifest": dict(release.manifest),
                "review_profile": {
                    "id": profile.id,
                    "name": profile.name,
                    "description": profile.description,
                    "criteria": [
                        criterion.model_dump(mode="json") for criterion in profile.criteria
                    ],
                },
            },
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        state.inbox[item.id] = item
        result.setdefault("created_inbox_item_ids", []).append(item.id)
        result.setdefault("created_review_request_ids", []).append(review.id)
        _mark_agent_work_due(state, agent_id=reviewer.id, now=utc_now())
        _append_agent_wake_requested_event_for_inbox_item(
            state,
            item=item,
            result=result,
            now=utc_now(),
        )

    if release.review_request_ids:
        release.updated_at = utc_now()
        return
    if not policy.allow_ungated:
        _block_release(release, "release policy produced no approval gate", result)
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
        _block_release(release, "release candidate commit is missing", result)
        return
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


def _verification_run_id(release_id: str, candidate_commit_sha: str) -> str:
    return f"release-verification-{release_id}-{candidate_commit_sha[:16]}"


def _block_release(
    release: ReleasePackage,
    error: str,
    result: dict[str, Any],
) -> None:
    release.state = TeamRecordState.BLOCKED
    release.error = error
    release.updated_at = utc_now()
    result["error"] = error


__all__ = [
    "RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE",
    "approve_release_candidate",
    "begin_release_gates",
    "create_release_reviews_or_approve",
    "record_release_verification_result",
]
