from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from ...models import TeamCase, TeamCaseStatus


TEAM_CASE_CLOSED_EVENT_TYPE = "team.case.closed"


def case_closed_event(
    case: TeamCase,
    *,
    previous_status: TeamCaseStatus,
) -> dict[str, Any] | None:
    """Build the one durable workflow event for an actual Case closure."""

    if (
        previous_status == TeamCaseStatus.CLOSED
        or case.status != TeamCaseStatus.CLOSED
        or case.closed_at is None
    ):
        return None
    closed_at = case.closed_at.isoformat()
    idempotency_key = "team.case.closed:" + sha256(
        f"{case.id}:{closed_at}".encode("utf-8")
    ).hexdigest()
    return {
        "id": idempotency_key,
        "event_type": TEAM_CASE_CLOSED_EVENT_TYPE,
        "correlation_key": case.id,
        "idempotency_key": idempotency_key,
        "payload": {
            "case_id": case.id,
            "team_id": case.team_id,
            "project_id": case.project_id,
            "owner_agent_id": case.owner_agent_id,
            "previous_status": previous_status.value,
            "source": case.source,
            "source_ref": case.source_ref,
            "closed_at": closed_at,
            # Preserve the closed Case as it stood at the transition.  Downstream
            # workflows often need a customer-facing outcome and the original
            # source-thread routing metadata; re-reading mutable Team state after
            # publication could instead observe a later operator edit.
            "case": case.model_dump(mode="json"),
        },
    }


def append_case_closed_event(
    events: list[Mapping[str, Any] | dict[str, Any]],
    case: TeamCase,
    *,
    previous_status: TeamCaseStatus,
) -> dict[str, Any] | None:
    event = case_closed_event(case, previous_status=previous_status)
    if event is not None:
        events.append(event)
    return event


__all__ = [
    "TEAM_CASE_CLOSED_EVENT_TYPE",
    "append_case_closed_event",
    "case_closed_event",
]
