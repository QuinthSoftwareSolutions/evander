from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from evander.admin.web.rbac import (
    TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
    scope_matches,
)

from ..models import TeamState
from ..repository import TeamStateRepository
from .commands.outbox import _team_event_outbox_records
from .domain.human_reviews import (
    expire_human_review_targets,
    reconcile_human_review_assignment,
)


def current_human_review_eligible_user_ids(
    user_store: Any,
    request: Any,
) -> list[str]:
    """Resolve live group, explicit-assignment, user, and permission eligibility."""

    return current_human_review_eligible_user_ids_for_requirement(
        user_store,
        request.requirement_snapshot,
        explicitly_assigned_user_ids=(request.current_assignment.explicitly_assigned_user_ids),
    )


def current_human_review_eligible_user_ids_for_requirement(
    user_store: Any,
    requirement: Any,
    *,
    explicitly_assigned_user_ids: Sequence[str] = (),
) -> list[str]:
    """Resolve a gate snapshot against the current Admin directory and RBAC."""

    directory_path = requirement.directory_path
    configured_group_ids = {
        str(group_id).strip().casefold()
        for group_id in requirement.configured_group_ids
        if str(group_id).strip()
    }
    explicit_user_ids = {
        str(user_id).strip() for user_id in explicitly_assigned_user_ids if str(user_id).strip()
    }
    candidate_user_ids = set(explicit_user_ids)
    for assignment in user_store.list_user_group_assignments():
        if assignment.group_id.casefold() in configured_group_ids and scope_matches(
            assignment.directory_path, directory_path
        ):
            candidate_user_ids.add(assignment.login)
    return sorted(
        user_id
        for user_id in candidate_user_ids
        if user_store.get_user(user_id) is not None
        and user_store.can(
            user_id,
            TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
            directory_path=directory_path,
        )
    )


def human_review_eligibility_resolver(
    user_store: Any,
) -> Callable[[Any], list[str]] | None:
    """Build a gate-time resolver only when a real Admin user store is available."""

    required_methods = (
        "list_user_group_assignments",
        "get_user",
        "can",
    )
    if user_store is None or any(
        not callable(getattr(user_store, method_name, None)) for method_name in required_methods
    ):
        return None
    return lambda requirement: current_human_review_eligible_user_ids_for_requirement(
        user_store,
        requirement,
    )


def reconcile_current_human_review_assignments(
    *,
    repository: TeamStateRepository,
    user_store: Any,
    request_ids: Sequence[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist live assignment/target drift and its blockers in one Team transaction."""

    initial_state = repository.read()
    selected_request_ids = (
        {str(request_id).strip() for request_id in request_ids if str(request_id).strip()}
        if request_ids is not None
        else None
    )
    requests = [
        request
        for request in initial_state.human_review_requests.values()
        if request.state not in {"approved", "changes_requested"}
        and (selected_request_ids is None or request.id in selected_request_ids)
    ]
    if not requests:
        return _reconciliation_result(
            revision=initial_state.revision,
            checked_count=0,
        )

    planned_eligible_user_ids = {
        request.id: current_human_review_eligible_user_ids(user_store, request)
        for request in requests
    }
    changed_request_ids = sorted(
        request.id
        for request in requests
        if request.current_assignment.eligible_user_ids != planned_eligible_user_ids[request.id]
    )
    effective_now = now or datetime.now(UTC)
    has_expired_targets = any(
        request.state == "pending"
        and request.test_target is not None
        and request.test_target.expires_at is not None
        and request.test_target.expires_at <= effective_now
        for request in requests
    )
    if not changed_request_ids and not has_expired_targets:
        return _reconciliation_result(
            revision=initial_state.revision,
            checked_count=len(requests),
        )

    emitted_events: list[Mapping[str, Any]] = []
    reconciled_request_ids: list[str] = []
    expired_request_ids: list[str] = []

    def _apply(state: TeamState) -> TeamState:
        expired_request_ids.extend(
            expire_human_review_targets(
                state,
                result={"events": emitted_events},
                now=effective_now,
                human_review_request_ids=selected_request_ids,
            )
        )
        for request_id in changed_request_ids:
            request = state.human_review_requests.get(request_id)
            if request is None or request.state in {"approved", "changes_requested"}:
                continue
            eligible_user_ids = current_human_review_eligible_user_ids(
                user_store,
                request,
            )
            if request.current_assignment.eligible_user_ids == eligible_user_ids:
                continue
            result: dict[str, Any] = {"events": []}
            reconcile_human_review_assignment(
                state,
                human_review_request_id=request.id,
                decision_eligible_user_ids=eligible_user_ids,
                result=result,
                now=effective_now,
            )
            reconciled_request_ids.append(request.id)
            emitted_events.extend(event for event in result["events"] if isinstance(event, Mapping))
        return state

    next_state = repository.update_with_outbox(
        _apply,
        expected_revision=initial_state.revision,
        outbox_records=lambda: _team_event_outbox_records(emitted_events),
    )
    return _reconciliation_result(
        revision=next_state.revision,
        checked_count=len(requests),
        reconciled_request_ids=reconciled_request_ids,
        expired_request_ids=expired_request_ids,
        event_count=len(emitted_events),
    )


def _reconciliation_result(
    *,
    revision: int,
    checked_count: int,
    reconciled_request_ids: Sequence[str] = (),
    expired_request_ids: Sequence[str] = (),
    event_count: int = 0,
) -> dict[str, Any]:
    reconciled = sorted(set(reconciled_request_ids))
    return {
        "revision": revision,
        "checked_count": checked_count,
        "reconciled_count": len(reconciled),
        "reconciled_human_review_request_ids": reconciled,
        "expired_human_review_request_ids": sorted(set(expired_request_ids)),
        "event_count": event_count,
    }


__all__ = [
    "current_human_review_eligible_user_ids",
    "current_human_review_eligible_user_ids_for_requirement",
    "human_review_eligibility_resolver",
    "reconcile_current_human_review_assignments",
]
