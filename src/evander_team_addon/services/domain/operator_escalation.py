from __future__ import annotations

from typing import Any
from uuid import uuid4
from ...models import (
    AgentInboxItem,
    GovernanceAgendaItem,
    GovernanceAgendaItemType,
    GovernanceRuntime,
    PersistentAgent,
    TeamInboxPriority,
    TeamInboxItemType,
    TeamState,
)

from .agent_access import _is_team_manager
from .common import (
    _optional_text,
    _require_present,
    _required_text,
    utc_now,
)
from .governance import _governance_groups_for_team


OPERATOR_ESCALATION_EVENT_TYPE = "team.operator.escalation_requested"


def _apply_operator_escalation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    escalation: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    if not _is_team_manager(state, actor):
        result["errors"].append(f"agent {actor.id} is not permitted to send operator escalations")
        return False
    title = _required_text(escalation.get("title"), "title")
    message = _required_text(escalation.get("message"), "message")
    urgency = _optional_text(escalation.get("urgency")) or "normal"
    project_id = _optional_text(escalation.get("project_id"))
    task_id = _optional_text(escalation.get("task_id"))
    team = state.teams.get(actor.team_id)
    if team is None:
        result["errors"].append(f"team {actor.team_id} does not exist")
        return False
    if project_id is not None:
        project = _require_present(state.projects, project_id, "project")
        if project.team_id != team.id:
            raise ValueError(f"project {project.id} is not in team {team.id}")
    if task_id is not None:
        task = _require_present(state.tasks, task_id, "task")
        task_project = _require_present(state.projects, task.project_id, "project")
        if task_project.team_id != team.id:
            raise ValueError(
                f"task {task.id} belongs to project {task_project.id} outside team {team.id}"
            )
        if project_id is not None and task.project_id != project_id:
            raise ValueError(
                f"task {task.id} belongs to project {task.project_id}, not project {project_id}"
            )
    escalation_id = f"escalation-{uuid4().hex}"
    payload: dict[str, Any] = {
        "escalation_id": escalation_id,
        "team_id": actor.team_id,
        "manager_agent_id": actor.id,
        "sender_session_id": actor.session_id,
        "title": title,
        "message": message,
        "urgency": urgency,
    }
    if project_id is not None:
        payload["project_id"] = project_id
    if task_id is not None:
        payload["task_id"] = task_id
    if not team.escalate_to_governance:
        target = _optional_text(team.operator_communication_target_id)
        if not target:
            result["errors"].append(
                f"team {actor.team_id} has no operator_communication_target_id for escalations"
            )
            return False
        payload["communication_target_id"] = target
        inbox_id = f"inbox-{uuid4().hex}"
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=actor.id,
            sender_agent_id=actor.id,
            task_id=task_id,
            item_type=TeamInboxItemType.ESCALATION,
            response_requested=False,
            priority=TeamInboxPriority.NORMAL,
            payload={"escalation_id": escalation_id, **payload},
        )
        result["created_inbox_item_ids"].append(inbox_id)
        result["events"].append(
            {
                "event_type": OPERATOR_ESCALATION_EVENT_TYPE,
                "correlation_key": escalation_id,
                "idempotency_key": (f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:{escalation_id}"),
                "payload": payload,
            }
        )
        result["operator_escalation_event_ids"].append(escalation_id)
        return True

    governance_groups = _governance_groups_for_team(state, team.id)
    if not governance_groups:
        result["errors"].append(
            f"team {team.id} is configured for governance escalation but is not in a "
            "governance group"
        )
        return False

    now = utc_now()
    for group in governance_groups:
        agenda_id = f"governance-agenda-{uuid4().hex}"
        group_runtime = state.governance_runtimes.setdefault(
            group.id,
            GovernanceRuntime(governance_group_id=group.id),
        )
        state.governance_agenda[agenda_id] = GovernanceAgendaItem(
            id=agenda_id,
            governance_group_id=group.id,
            item_type=GovernanceAgendaItemType.TEAM_ESCALATION,
            title=title,
            summary=message,
            team_id=team.id,
            project_id=project_id,
            payload=payload | {"escalation_id": escalation_id, "governance_group_id": group.id},
            created_at=now,
            updated_at=now,
        )
        group_runtime.next_wake_at = None
        group_runtime.updated_at = now
        result.setdefault("created_governance_agenda_item_ids", []).append(agenda_id)
    return True


def record_operator_escalation_delivery(
    state: TeamState,
    *,
    escalation_id: str,
    success: bool,
    provider_message_id: str | None = None,
    error: str | None = None,
    fallback_used: bool = False,
) -> dict[str, Any]:
    """Persist the terminal communication outcome for an operator escalation.

    Escalations predate a separate delivery-record model, so their manager inbox
    item is the durable intent record.  Storing the outcome there keeps the
    operator-facing action and its delivery evidence together without conflating
    successful workflow routing with an actually sent message.
    """

    normalized_escalation_id = _required_text(escalation_id, "escalation_id")
    normalized_provider_message_id = _optional_text(provider_message_id)
    normalized_error = _optional_text(error)
    matching_items = [
        item
        for item in state.inbox.values()
        if _optional_text(item.payload.get("escalation_id")) == normalized_escalation_id
    ]
    matching_blockers = [
        blocker
        for blocker in state.blockers.values()
        if normalized_escalation_id in blocker.terminal_reference_ids
    ]
    if not matching_items and not matching_blockers:
        raise ValueError(f"operator escalation {normalized_escalation_id} does not exist")

    now = utc_now()
    item = (
        min(matching_items, key=lambda candidate: (candidate.created_at, candidate.id))
        if matching_items
        else None
    )
    matching_blocker = (
        min(matching_blockers, key=lambda candidate: (candidate.created_at, candidate.id))
        if matching_blockers
        else None
    )
    manager_agent_id = (
        item.recipient_agent_id
        if item is not None
        else state.teams[matching_blocker.team_id].manager_agent_id
    )
    prior_payload = dict(item.payload) if item is not None else {}
    previous_delivery = prior_payload.get("delivery")
    previous_attempt_count = (
        int(previous_delivery.get("attempt_count") or 0)
        if isinstance(previous_delivery, dict)
        else 0
    )
    status = "delivered" if success else "failed"
    delivery = {
        "status": status,
        "provider_message_id": normalized_provider_message_id,
        "error": None if success else (normalized_error or "operator escalation send failed"),
        "fallback_used": bool(fallback_used),
        "attempt_count": previous_attempt_count + 1,
        "updated_at": now.isoformat(),
    }
    if item is not None:
        prior_payload["delivery"] = delivery
        item.payload = prior_payload
        item.updated_at = now
    if matching_blocker is not None:
        for event in state.blocker_events.values():
            if (
                event.blocker_id == matching_blocker.id
                and normalized_escalation_id in event.reference_ids
            ):
                metadata = dict(event.metadata)
                metadata["operator_escalation_delivery"] = delivery
                event.metadata = metadata

    runtime = state.agent_runtimes.get(manager_agent_id)
    if success:
        if runtime is not None:
            runtime.no_progress_count = 0
            runtime.last_progress_at = now
            runtime.updated_at = now
    else:
        # Make the failed delivery visible in the next manager wake.  The
        # no-progress counter is deliberately left untouched: a failed send is
        # not progress and must not extend a fallback loop.
        if item is not None:
            item.priority = TeamInboxPriority.HIGH
            item.response_requested = True
        else:
            failure_inbox_id = f"inbox-operator-escalation-delivery-{normalized_escalation_id}"
            if failure_inbox_id not in state.inbox:
                state.inbox[failure_inbox_id] = AgentInboxItem(
                    id=failure_inbox_id,
                    recipient_agent_id=manager_agent_id,
                    sender_agent_id=manager_agent_id,
                    task_id=matching_blocker.task_id if matching_blocker is not None else None,
                    item_type=TeamInboxItemType.ESCALATION,
                    response_requested=True,
                    priority=TeamInboxPriority.HIGH,
                    payload={
                        "escalation_id": normalized_escalation_id,
                        "delivery": delivery,
                        "blocker_id": matching_blocker.id if matching_blocker is not None else None,
                    },
                    created_at=now,
                    updated_at=now,
                )
        if runtime is not None:
            runtime.next_wake_at = now
            runtime.updated_at = now

    return {
        "escalation_id": normalized_escalation_id,
        "manager_agent_id": manager_agent_id,
        "status": status,
        "attempt_count": previous_attempt_count + 1,
        "provider_message_id": normalized_provider_message_id,
        "error": None if success else (normalized_error or "operator escalation send failed"),
        "fallback_used": bool(fallback_used),
    }


__all__ = [
    "OPERATOR_ESCALATION_EVENT_TYPE",
    "_apply_operator_escalation",
    "record_operator_escalation_delivery",
]
