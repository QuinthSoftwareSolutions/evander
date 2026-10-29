from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from evander.operator_steering import operator_steering_note_store

from ..models import (
    TeamBlockerState,
    TeamDispatchStatus,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)


_ACTIVE_STEERING_NOTE_STATES = frozenset({"pending", "injecting", "delivered"})


def steer_closed_review_dispatch(
    *,
    runtime_dir: Path,
    state: TeamState,
    review_request_id: str,
    action: str,
    reason: str,
    created_by: str,
    admin_runtime_builder: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Interrupt or inform the exact live dispatch for an explicitly closed review.

    Review closure is already durable when this best-effort post-commit seam runs.
    Container workers use the supported interrupt-and-resume steering path; legacy
    executions receive a one-shot note on their next eligible model request.
    """

    review = state.review_requests.get(str(review_request_id or "").strip())
    if review is None or review.retired_at is None:
        return {"status": "not_required", "reason": "review_is_not_closed"}
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"withdrawn", "retired"}:
        return {"status": "not_required", "reason": "unsupported_closure_action"}
    runtime = state.agent_runtimes.get(review.reviewer_agent_id)
    dispatch = (
        state.dispatches.get(runtime.active_dispatch_id or "") if runtime is not None else None
    )
    if (
        dispatch is None
        or dispatch.status != TeamDispatchStatus.LEASED
        or dispatch.agent_id != review.reviewer_agent_id
        or review.id not in dispatch.review_request_ids
    ):
        return {"status": "not_required", "reason": "no_matching_leased_dispatch"}

    marker = _review_closure_steering_marker(
        review_request_id=review.id,
        action=normalized_action,
    )
    message = _review_closure_steering_message(
        marker=marker,
        review_request_id=review.id,
        action=normalized_action,
        has_other_scope=_dispatch_has_other_scope(
            state,
            dispatch,
            review_request_id=review.id,
        ),
    )
    request_key = _review_closure_steering_request_key(
        dispatch_id=dispatch.id,
        review_request_id=review.id,
        action=normalized_action,
    )
    try:
        if admin_runtime_builder is None:
            from evander.admin.composition import build_admin_runtime

            admin_runtime_builder = build_admin_runtime
        admin_runtime = admin_runtime_builder(Path(runtime_dir))
        existing = next(
            (
                note
                for note in admin_runtime.operator_steering_admin.list_operator_steering_notes(
                    session_id=dispatch.session_id,
                    dispatch_id=dispatch.id,
                    limit=200,
                )
                if marker in str(getattr(note, "message", ""))
                and str(getattr(note, "status", "")).strip().lower() in _ACTIVE_STEERING_NOTE_STATES
            ),
            None,
        )
        if existing is not None:
            return {
                "status": "already_requested",
                "dispatch_id": dispatch.id,
                "review_request_id": review.id,
                "note_id": str(getattr(existing, "note_id", "") or "") or None,
            }
        claim_store = operator_steering_note_store(runtime_dir)
        claim, acquired = claim_store.claim_request_once(
            request_key=request_key,
            session_id=dispatch.session_id,
            dispatch_id=dispatch.id,
        )
        if not acquired:
            return {
                "status": "already_requested",
                "dispatch_id": dispatch.id,
                "review_request_id": review.id,
                "note_id": claim.outcome_note_id,
                "claim_status": claim.status,
            }
        try:
            steering = admin_runtime.model_usage_admin.submit_operator_steering_note(
                session_id=dispatch.session_id,
                dispatch_id=dispatch.id,
                message=message,
                created_by=created_by,
            )
        except Exception as exc:
            try:
                claim_store.finish_request_claim(
                    request_key=claim.request_key,
                    owner_token=claim.owner_token,
                    succeeded=False,
                    error=str(exc).strip() or type(exc).__name__,
                )
            except Exception:
                pass
            raise
    except Exception as exc:
        return {
            "status": "failed",
            "dispatch_id": dispatch.id,
            "review_request_id": review.id,
            "error": str(exc).strip() or type(exc).__name__,
        }

    mode = str(steering.get("mode") or "").strip() if isinstance(steering, Mapping) else ""
    note = steering.get("note") if isinstance(steering, Mapping) else None
    note_id = str(getattr(note, "note_id", "") or "") or None
    try:
        claim_store.finish_request_claim(
            request_key=claim.request_key,
            owner_token=claim.owner_token,
            succeeded=True,
            outcome_note_id=note_id,
        )
    except Exception:
        pass
    return {
        "status": "requested",
        "mode": mode or None,
        "dispatch_id": dispatch.id,
        "review_request_id": review.id,
        "note_id": note_id,
    }


def _review_closure_steering_marker(
    *,
    review_request_id: str,
    action: str,
) -> str:
    return f"[team-review-closure:{review_request_id}:{action}]"


def _review_closure_steering_request_key(
    *,
    dispatch_id: str,
    review_request_id: str,
    action: str,
) -> str:
    return f"team-review-closure:{dispatch_id}:{review_request_id}:{action}"


def _review_closure_steering_message(
    *,
    marker: str,
    review_request_id: str,
    action: str,
    has_other_scope: bool,
) -> str:
    continuation = (
        "Stop all work and testing for that review now. Continue only the other "
        "still-actionable work already in this dispatch, then finish promptly."
        if has_other_scope
        else (
            "Stop all work and testing for that review now. Do not submit a review "
            "decision; finish this dispatch promptly."
        )
    )
    return (
        f"{marker}\nReview {review_request_id} was explicitly {action} and is no "
        "longer actionable.\n"
        f"{continuation}"
    )


def _dispatch_has_other_scope(
    state: TeamState,
    dispatch: Any,
    *,
    review_request_id: str,
) -> bool:
    return bool(
        any(
            (task := state.tasks.get(task_id)) is not None
            and task.owner_agent_id == dispatch.agent_id
            and task.state in {TeamTaskState.PENDING, TeamTaskState.IN_PROGRESS}
            for task_id in getattr(dispatch, "task_ids", ())
        )
        or any(
            (blocker := state.blockers.get(blocker_id)) is not None
            and blocker.state == TeamBlockerState.OPEN
            for blocker_id in getattr(dispatch, "blocker_ids", ())
        )
        or any(
            (item := state.inbox.get(inbox_item_id)) is not None
            and item.recipient_agent_id == dispatch.agent_id
            and item.state == TeamInboxItemState.PENDING
            for inbox_item_id in getattr(dispatch, "inbox_item_ids", ())
        )
        or any(
            review_id != review_request_id
            and (review := state.review_requests.get(review_id)) is not None
            and review.reviewer_agent_id == dispatch.agent_id
            and review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
            and review.superseded_by_review_request_id is None
            and review.retired_at is None
            for review_id in getattr(dispatch, "review_request_ids", ())
        )
    )


__all__ = ["steer_closed_review_dispatch"]
