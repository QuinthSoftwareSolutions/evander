from __future__ import annotations

from evander.models import utc_now
from collections.abc import Sequence
from typing import Any
from uuid import uuid4
from .communication_helpers import _team_operator_escalation_normalized_payload
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import (
    _require_command_agent,
    _required_command_text,
    _validate_command_project_scope,
    _validate_command_task_scope,
)
from ...models import (
    AgentInboxItem,
    GovernanceAgendaItem,
    GovernanceAgendaItemType,
    GovernanceRuntime,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamOutboxRecord,
    TeamState,
    _unique_text_list,
)
from ..domain.agent_access import _is_team_manager
from ..domain.common import (
    _optional_text,
    _require_present,
    _required_text,
)
from ..domain.governance import _governance_groups_for_team
from ..domain.operator_escalation import OPERATOR_ESCALATION_EVENT_TYPE

from .executor import TeamCommandExecutor


_PENDING_ESCALATION_DELIVERY_MESSAGE = (
    "Escalation is queued, not delivered. Report it as queued or pending until "
    "downstream delivery evidence exists; intent_recorded alone is not delivery confirmation."
)


class TeamOperatorEscalationCommandService(TeamCommandExecutor):
    def escalate_operator_command(
        self,
        *,
        session_id: str,
        title: str,
        message: str,
        request_id: str,
        urgency: str = "normal",
        project_id: str | None = None,
        task_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_title = _optional_text(title) or ""
        normalized_message = _optional_text(message) or ""
        normalized_urgency = _optional_text(urgency) or "normal"
        normalized_project_id = _optional_text(project_id)
        normalized_task_id = _optional_text(task_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_escalate_operator",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            # Recording an escalation intent is not delivery evidence.  Its
            # completion effect resets no-progress only after a message is sent.
            progress_classification="none",
        )
        normalized_payload = _team_operator_escalation_normalized_payload(
            title=normalized_title,
            message=normalized_message,
            urgency=normalized_urgency,
            project_id=normalized_project_id,
            task_id=normalized_task_id,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_escalate_operator")
            title_text = _required_command_text(
                normalized_title,
                field_name="title",
                code="empty_title",
            )
            message_text = _required_command_text(
                normalized_message,
                field_name="message",
                code="empty_message",
            )
            if not _is_team_manager(state, agent):
                raise TeamCommandValidationError(
                    code="not_top_level_team_manager",
                    message="team_escalate_operator can only be called by the top-level team manager",
                    correction_context={"caller_agent_id": agent.id},
                )
            team = _require_present(state.teams, agent.team_id or "", "team")
            _validate_command_project_scope(
                state,
                team_id=team.id,
                project_id=normalized_project_id,
            )
            _validate_command_task_scope(state, agent=agent, task_id=normalized_task_id)
            escalation_id = f"escalation-{uuid4().hex}"
            payload: dict[str, Any] = {
                "escalation_id": escalation_id,
                "team_id": team.id,
                "manager_agent_id": agent.id,
                "sender_session_id": agent.session_id,
                "title": title_text,
                "message": message_text,
                "urgency": normalized_urgency,
            }
            if normalized_project_id is not None:
                payload["project_id"] = normalized_project_id
            if normalized_task_id is not None:
                payload["task_id"] = normalized_task_id
            if context.correlation_id is not None:
                payload["correlation_id"] = context.correlation_id
            if context.causation_id is not None:
                payload["causation_id"] = context.causation_id

            now = utc_now()
            if not team.escalate_to_governance:
                target = _optional_text(team.operator_communication_target_id)
                if target is None:
                    raise TeamCommandValidationError(
                        code="operator_escalation_target_missing",
                        message=(
                            f"team {team.id} has no operator_communication_target_id "
                            "for escalations"
                        ),
                        correction_context={"team_id": team.id},
                    )
                payload["communication_target_id"] = target
                inbox_id = f"inbox-{uuid4().hex}"
                state.inbox[inbox_id] = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=agent.id,
                    sender_agent_id=agent.id,
                    task_id=normalized_task_id,
                    item_type=TeamInboxItemType.ESCALATION,
                    response_requested=False,
                    priority=TeamInboxPriority.NORMAL,
                    payload={"escalation_id": escalation_id, **payload},
                    created_at=now,
                    updated_at=now,
                )
                outbox_id = f"outbox-{uuid4().hex}"
                outbox_record = TeamOutboxRecord(
                    id=outbox_id,
                    intent_type=OPERATOR_ESCALATION_EVENT_TYPE,
                    aggregate_type="team_operator_escalation",
                    aggregate_id=escalation_id,
                    idempotency_key=(
                        f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:{escalation_id}"
                    ),
                    payload={
                        "event_type": OPERATOR_ESCALATION_EVENT_TYPE,
                        "correlation_key": escalation_id,
                        "payload": payload,
                    },
                    created_at=now,
                    updated_at=now,
                )
                return TeamCommandApplyResult(
                    response_payload={
                        "escalation_id": escalation_id,
                        "operator_event_ids": [escalation_id],
                        "operator_escalation_event_ids": [escalation_id],
                        "outbox_record_ids": [outbox_id],
                        "created_inbox_item_ids": [inbox_id],
                        "intent_recorded": True,
                        "async_effect_pending": True,
                        "downstream_effect_completed": False,
                        "delivery_status": "pending",
                        "delivery_status_message": _PENDING_ESCALATION_DELIVERY_MESSAGE,
                        "progress_made": False,
                    },
                    created_domain_ids=[escalation_id, inbox_id, outbox_id],
                    created_event_ids=[escalation_id],
                    outbox_records=[outbox_record],
                    progress_classification="none",
                )

            governance_groups = _governance_groups_for_team(state, team.id)
            if not governance_groups:
                raise TeamCommandValidationError(
                    code="governance_group_not_found",
                    message=(
                        f"team {team.id} is configured for governance escalation "
                        "but is not in a governance group"
                    ),
                    correction_context={"team_id": team.id},
                )
            agenda_ids: list[str] = []
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
                    title=title_text,
                    summary=message_text,
                    team_id=team.id,
                    project_id=normalized_project_id,
                    payload=payload
                    | {
                        "escalation_id": escalation_id,
                        "governance_group_id": group.id,
                    },
                    created_at=now,
                    updated_at=now,
                )
                group_runtime.next_wake_at = None
                group_runtime.updated_at = now
                agenda_ids.append(agenda_id)
            return TeamCommandApplyResult(
                response_payload={
                    "escalation_id": escalation_id,
                    "governance_agenda_item_ids": agenda_ids,
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "delivery_status": "pending",
                    "delivery_status_message": _PENDING_ESCALATION_DELIVERY_MESSAGE,
                    "progress_made": True,
                },
                created_domain_ids=[escalation_id, *agenda_ids],
                created_event_ids=agenda_ids,
                progress_classification="progress",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamOperatorEscalationCommandService"]
