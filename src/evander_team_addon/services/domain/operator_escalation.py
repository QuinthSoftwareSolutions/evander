from __future__ import annotations

from typing import Any
from uuid import uuid4
from ...models import (
    AgentInboxItem,
    GovernanceAgendaItem,
    GovernanceAgendaItemType,
    GovernanceRuntime,
    PersistentAgent,
    TeamInboxItemType,
    TeamInboxPriority,
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
    escalation_id = f"escalation-{uuid4().hex}"
    payload: dict[str, Any] = {
        "team_id": actor.team_id,
        "manager_agent_id": actor.id,
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


__all__ = [
    "OPERATOR_ESCALATION_EVENT_TYPE",
    "_apply_operator_escalation",
]
