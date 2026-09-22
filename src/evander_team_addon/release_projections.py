"""Release progress and next-action projections."""

from __future__ import annotations

import json
from typing import Any

from .model_common import (
    TeamRecordState,
)
from .model_dispatch import (
    GitPromotionRecord,
)
from .model_releases import (
    ReleasePackage,
    ReleaseVerificationRun,
)
from .model_state import (
    TeamState,
)


def release_package_payload_with_derived(
    state: TeamState,
    release: ReleasePackage,
) -> dict[str, Any]:
    from .services.domain.release_cancellation import release_cancellation_error

    payload = release.model_dump(mode="json")
    cancellation_error = release_cancellation_error(state, release)
    payload["cancellation_available"] = (
        release.state != TeamRecordState.CANCELLED and cancellation_error is None
    )
    payload["cancellation_unavailable_reason"] = cancellation_error
    payload["verification_runs"] = [
        state.release_verification_runs[run_id].model_dump(mode="json")
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
    ]
    payload["release_reviews"] = [
        state.review_requests[review_id].model_dump(mode="json")
        for review_id in release.review_request_ids
        if review_id in state.review_requests
    ]
    if release.review_cohort_id is not None:
        cohort_reviews = [
            state.review_requests[review_id]
            for review_id in release.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
            and state.review_requests[review_id].review_cohort_id == release.review_cohort_id
        ]
        blocked_ids = [
            review.id for review in cohort_reviews if review.state == TeamRecordState.BLOCKED
        ]
        pending_ids = [
            review.id
            for review in cohort_reviews
            if review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
        ]
        rejected_ids = [
            review.id for review in cohort_reviews if review.state == TeamRecordState.REJECTED
        ]
        accepted_ids = [
            review.id for review in cohort_reviews if review.state == TeamRecordState.ACCEPTED
        ]
        cohort_status = (
            "blocked"
            if blocked_ids
            else "collecting"
            if pending_ids
            else "changes_requested"
            if rejected_ids
            else "accepted"
        )
        payload["release_review_cohort"] = {
            "id": release.review_cohort_id,
            "sealed_at": release.review_cohort_sealed_at,
            "status": cohort_status,
            "total": len(cohort_reviews),
            "resolved": len(accepted_ids) + len(rejected_ids),
            "pending_review_request_ids": pending_ids,
            "blocked_review_request_ids": blocked_ids,
            "rejected_review_request_ids": rejected_ids,
            "accepted_review_request_ids": accepted_ids,
        }
    related_promotions = _release_related_promotions(state, release)
    pending_promotions = [
        promotion.id for promotion in related_promotions if promotion.state == "pending"
    ]
    failed_promotions = [
        promotion.id for promotion in related_promotions if promotion.state == "failed"
    ]
    if pending_promotions:
        payload["pending_git_promotion_ids"] = pending_promotions
    if failed_promotions:
        payload["failed_git_promotion_ids"] = failed_promotions
    if related_promotions:
        payload["promotion_attempt_count"] = len(
            [promotion for promotion in related_promotions if promotion.state != "pending"]
        )
    next_action_hint = _release_next_action_hint(state, release)
    if next_action_hint is not None:
        payload["next_action"] = _release_next_action(state, release)
        payload["next_action_hint"] = next_action_hint
    blocked_reason_code = _release_blocked_reason_code(
        release,
        _release_blocking_promotion(state, release),
    )
    if blocked_reason_code is not None:
        payload["blocked_reason_code"] = blocked_reason_code
    return payload


def _release_next_action_hint(state: TeamState, release: ReleasePackage) -> str | None:
    if release.review_cohort_id is not None and release.state == TeamRecordState.PENDING:
        current_reviews = [
            state.review_requests[review_id]
            for review_id in release.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
            and state.review_requests[review_id].review_cohort_id == release.review_cohort_id
        ]
        pending_count = sum(
            review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
            for review in current_reviews
        )
        rejected_count = sum(review.state == TeamRecordState.REJECTED for review in current_reviews)
        if pending_count:
            return (
                f"Wait for the remaining {pending_count} release review cohort slot(s). "
                f"{rejected_count} rejection(s) are recorded, but correction work begins "
                "only after the sealed cohort closes."
            )
    if release.state != TeamRecordState.BLOCKED and not release.error:
        return None
    gate_failure = release.gate_failure
    if gate_failure is not None:
        if (
            release.state == TeamRecordState.BLOCKED
            and gate_failure.stage == "gate_obligation"
            and gate_failure.kind == "waiver_required"
        ):
            return (
                "A directory owner must record a reasoned gate-obligation waiver for "
                f"release {release.id} and its pinned candidate before publication can resume. "
                f"Current blocker: {gate_failure.message}"
            )
        if (
            release.state == TeamRecordState.BLOCKED
            and gate_failure.stage == "verification"
            and gate_failure.kind in {"infrastructure", "timeout"}
        ):
            current_run = _current_release_verification_run(state, release)
            if current_run is not None and (current_run.retry_requested_by or "").startswith(
                "agent:"
            ):
                return (
                    f"Do not retry verification gate {current_run.id} as an agent: the "
                    "agent-requested retry already failed against the unchanged candidate "
                    f"for release {release.id}. Escalate the run evidence and the concrete "
                    "infrastructure recovery requirement to an operator. "
                    f"Current blocker: {gate_failure.message}"
                )
            return (
                "Inspect the failed run and confirm concrete evidence that its "
                "infrastructure failure recovered. Only then use team_retry_release_gate "
                "with gate_kind verification and gate_id "
                f"{gate_failure.source_id or 'from the current verification gate'} against "
                f"the unchanged candidate for release {release.id}, citing exactly what "
                "changed. If no recovery evidence exists, update or escalate the blocker "
                "instead of retrying. "
                f"Current blocker: {gate_failure.message}"
            )
        if (
            release.state == TeamRecordState.BLOCKED
            and gate_failure.stage == "review"
            and gate_failure.kind == "blocked"
        ):
            return (
                "Use team_retry_release_gate with gate_kind review and gate_id "
                f"{gate_failure.source_id or 'from the current release review'} to replace the "
                f"blocked review for release {release.id} against the unchanged candidate after "
                "resolving its process or evidence blocker. "
                f"Current blocker: {gate_failure.message}"
            )
        if release.state == TeamRecordState.REJECTED:
            if (
                release.review_cohort_id is not None
                and gate_failure.stage == "review"
                and gate_failure.kind == "rejected"
                and release.review_correction_task_id is not None
            ):
                return (
                    f"Release review cohort {release.review_cohort_id} is complete and requested "
                    f"changes. Resolve correction task {release.review_correction_task_id} once "
                    "against the consolidated findings; Team will create the successor release."
                )
            return (
                f"Release {release.id} is terminal because its candidate failed or was "
                "rejected. Submit changed reviewed content as a new release; unchanged "
                "candidate content will be refused. "
                f"Current outcome: {gate_failure.message}"
            )
    failed_promotion = _release_blocking_promotion(state, release)
    parts = [
        f"Resolve existing release package {release.id}; do not create another "
        "git_release_requests item with this id."
    ]
    if failed_promotion is not None:
        parts.append(
            f"Failed {failed_promotion.promotion_kind} promotion "
            f"{failed_promotion.id}: {failed_promotion.source_branch} -> "
            f"{failed_promotion.target_branch}."
        )
        if failed_promotion.promotion_kind in {
            "feature",
            "correction_aggregate",
            "release",
        }:
            parts.append(
                "Assign semantic conflicts to the owning coder, have the resolved "
                "commit pushed to an agents/... ref, then submit "
                "git_release_recoveries for this package with the resolved commit_sha."
            )
            parts.append(
                f"Resolve and commit on promotion target branch {failed_promotion.target_branch}."
            )
        else:
            parts.append(
                f"This blocker is a failed {failed_promotion.promotion_kind} "
                "promotion, not a recoverable correction, feature, or release promotion; "
                "do not submit "
                "git_release_recoveries. Resolve and commit on failed promotion "
                f"target branch {failed_promotion.target_branch}, or escalate "
                "for operator/orchestrator retry."
            )
    if release.error:
        parts.append(f"Current blocker: {release.error}")
    if release.error and "merge conflict" in release.error.casefold():
        if failed_promotion is not None and failed_promotion.promotion_kind in {
            "feature",
            "correction_aggregate",
            "release",
        }:
            parts.append(
                "Resolve the merge conflict on the failed promotion target branch, "
                "push the resolved commit to an agents/... ref, then recover the "
                "existing release package with git_release_recoveries."
            )
        else:
            parts.append(
                "Resolve the merge conflict on the failed promotion target branch; "
                "git_release_recoveries only applies to failed correction-aggregate, "
                "feature, or release promotions."
            )
    if failed_promotion is None:
        parts.append(
            "Inspect the package's git_promotion_record_ids and current Git state "
            "to find the blocked promotion target before retrying or finalizing."
        )
    return " ".join(parts)


def _current_release_verification_run(
    state: TeamState,
    release: ReleasePackage,
) -> ReleaseVerificationRun | None:
    current = [
        state.release_verification_runs[run_id]
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
        and state.release_verification_runs[run_id].superseded_by_verification_run_id is None
    ]
    current.sort(key=lambda run: (run.attempt_number, run.created_at, run.id), reverse=True)
    return current[0] if current else None


def _release_next_action(state: TeamState, release: ReleasePackage) -> str:
    failure = release.gate_failure
    if failure is not None:
        if (
            release.state == TeamRecordState.BLOCKED
            and failure.stage == "gate_obligation"
            and failure.kind == "waiver_required"
        ):
            return "authorize_release_gate_obligation_waiver"
        if (
            release.state == TeamRecordState.BLOCKED
            and failure.stage == "verification"
            and failure.kind in {"infrastructure", "timeout"}
        ):
            current_run = _current_release_verification_run(state, release)
            if current_run is not None and (current_run.retry_requested_by or "").startswith(
                "agent:"
            ):
                return "escalate_release_verification_recovery"
            return "retry_release_verification"
        if (
            release.state == TeamRecordState.BLOCKED
            and failure.stage == "review"
            and failure.kind == "blocked"
        ):
            return "retry_release_review"
        if release.state == TeamRecordState.REJECTED:
            return "create_replacement_release"
    return "resolve_blocked_release"


def _release_related_promotions(
    state: TeamState,
    release: ReleasePackage,
) -> list[GitPromotionRecord]:
    release_promotion_ids = set(release.git_promotion_record_ids)
    promotions = [
        promotion
        for promotion in state.git_promotions.values()
        if promotion.id in release_promotion_ids or promotion.release_package_id == release.id
    ]
    promotions.sort(key=lambda promotion: (promotion.created_at, promotion.id))
    return promotions


def _release_blocked_reason_code(
    release: ReleasePackage,
    failed_promotion: GitPromotionRecord | None,
) -> str | None:
    if release.state != TeamRecordState.BLOCKED and not release.error:
        return None
    if release.gate_failure is not None:
        return f"release_{release.gate_failure.stage}_{release.gate_failure.kind}"
    text_parts = [release.error or ""]
    if failed_promotion is not None:
        text_parts.append(failed_promotion.error or "")
        text_parts.append(json.dumps(failed_promotion.outputs, sort_keys=True))
    text = "\n".join(text_parts).casefold()
    if "index.lock" in text:
        return "stale_index_lock"
    if "dirty worktree" in text:
        return "checkout_dirty"
    if "merge conflict" in text or "automatic merge failed" in text:
        return "merge_conflict"
    if "not something we can merge" in text or "source_ref_missing" in text:
        return "source_ref_missing"
    if "bare repo not found" in text:
        return "source_ref_missing"
    return "unexpected_git_failure"


def _release_blocking_promotion(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    release_promotion_ids = set(release.git_promotion_record_ids)
    failed_promotions = [
        promotion
        for promotion in state.git_promotions.values()
        if promotion.state == "failed"
        and (promotion.id in release_promotion_ids or promotion.release_package_id == release.id)
    ]
    if not failed_promotions:
        return None
    failed_promotions.sort(
        key=lambda promotion: (
            promotion.updated_at,
            promotion.created_at,
            promotion.id,
        ),
        reverse=True,
    )
    return failed_promotions[0]
