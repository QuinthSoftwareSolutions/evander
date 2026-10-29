from __future__ import annotations

from evander.models import utc_now
from typing import Any
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from ...models import (
    AgentStatus,
    GovernanceAgendaItem,
    GovernanceAgendaItemState,
    TeamState,
)
from ..domain.agent_access import _agent_for_session
from ..domain.common import (
    _optional_text,
    _require_present,
    _required_text,
)
from ..domain.operator_directives import append_operator_directive

from .executor import TeamCommandExecutor


class TeamGovernanceCommandService(TeamCommandExecutor):
    def mark_governance_agenda_handled_by_session(
        self,
        *,
        agenda_item_id: str,
        requesting_session_id: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, GovernanceAgendaItem]:
        normalized_item_id = _required_text(agenda_item_id, "agenda_item_id")
        normalized_session_id = _required_text(
            requesting_session_id,
            "requesting_session_id",
        )

        def _mutate(state: TeamState) -> TeamState:
            item = _require_present(
                state.governance_agenda,
                normalized_item_id,
                "governance agenda item",
            )
            group = _require_present(
                state.governance_groups,
                item.governance_group_id,
                "governance group",
            )
            requester = _agent_for_session(state, session_id=normalized_session_id)
            if (
                requester.status != AgentStatus.ACTIVE
                or group.archived
                or group.manager_agent_id != requester.id
            ):
                raise ValueError(
                    "governance agenda handling requires the active governance "
                    "manager session for the agenda item's governance group"
                )
            item.state = GovernanceAgendaItemState.HANDLED
            item.updated_at = utc_now()
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        return state, state.governance_agenda[normalized_item_id]

    def create_operator_directive_command(
        self,
        *,
        session_id: str,
        team_id: str,
        message: str,
        request_id: str,
        governance_group_id: str | None = None,
        recipient_agent_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        title: str | None = None,
        source: str | None = None,
        source_message_id: str | None = None,
        created_by: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_message = _required_text(message, "message")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_group_id = _optional_text(governance_group_id)
        normalized_recipient_id = _optional_text(recipient_agent_id)
        normalized_project_id = _optional_text(project_id)
        normalized_task_id = _optional_text(task_id)
        normalized_title = _optional_text(title)
        normalized_source = _optional_text(source)
        normalized_source_message_id = _optional_text(source_message_id)
        normalized_created_by = _optional_text(created_by)
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_operator_directive",
            request_id=normalized_request_id,
            governance_group_id=normalized_group_id,
            wake_session_class="governance",
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "team_id": normalized_team_id,
            "message": normalized_message,
            "recipient_agent_id": normalized_recipient_id,
            "project_id": normalized_project_id,
            "task_id": normalized_task_id,
            "title": normalized_title,
            "source": normalized_source,
            "source_message_id": normalized_source_message_id,
            "created_by": normalized_created_by,
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            if resolved.agent is None or resolved.governance_group_id is None:
                raise TeamCommandValidationError(
                    code="invalid_governance_scope",
                    message="team_create_operator_directive requires a governance manager scope",
                )
            group = _require_present(
                state_model.governance_groups,
                resolved.governance_group_id,
                "governance group",
            )
            team = _require_present(state_model.teams, normalized_team_id, "team")
            if team.archived:
                raise TeamCommandValidationError(
                    code="team_archived",
                    message=f"team {team.id} is archived",
                    correction_context={"team_id": team.id},
                )
            if team.id not in group.member_team_ids:
                raise TeamCommandValidationError(
                    code="team_not_in_governance_group",
                    message=f"team {team.id} is not managed by governance group {group.id}",
                    correction_context={
                        "team_id": team.id,
                        "governance_group_id": group.id,
                    },
                )
            recipient_id = normalized_recipient_id or team.manager_agent_id
            if recipient_id != team.manager_agent_id:
                raise TeamCommandValidationError(
                    code="invalid_recipient",
                    message=(
                        "governance manager sessions can only enqueue operator "
                        "directives for the target team manager inbox"
                    ),
                    correction_context={
                        "team_id": team.id,
                        "recipient_agent_id": recipient_id,
                        "manager_agent_id": team.manager_agent_id,
                    },
                )
            recipient = _require_present(state_model.agents, recipient_id, "recipient agent")
            if recipient.team_id != team.id:
                raise TeamCommandValidationError(
                    code="recipient_team_mismatch",
                    message=f"recipient agent {recipient.id} is not in team {team.id}",
                    correction_context={
                        "team_id": team.id,
                        "recipient_agent_id": recipient.id,
                    },
                )
            if recipient.status != AgentStatus.ACTIVE:
                raise TeamCommandValidationError(
                    code="recipient_not_active",
                    message=f"recipient agent {recipient.id} is not active",
                    correction_context={
                        "recipient_agent_id": recipient.id,
                        "status": recipient.status.value,
                    },
                )

            effective_project_id = normalized_project_id
            if normalized_task_id is not None:
                task = _require_present(state_model.tasks, normalized_task_id, "task")
                project = _require_present(state_model.projects, task.project_id, "project")
                if project.team_id != team.id:
                    raise TeamCommandValidationError(
                        code="task_team_mismatch",
                        message=f"task {task.id} is not in team {team.id}",
                        correction_context={"task_id": task.id, "team_id": team.id},
                    )
                if effective_project_id is not None and effective_project_id != task.project_id:
                    raise TeamCommandValidationError(
                        code="task_project_mismatch",
                        message=(
                            f"task {task.id} belongs to project {task.project_id}, "
                            f"not project {effective_project_id}"
                        ),
                        correction_context={
                            "task_id": task.id,
                            "project_id": effective_project_id,
                            "task_project_id": task.project_id,
                        },
                    )
                effective_project_id = task.project_id
            if effective_project_id is not None:
                project = _require_present(
                    state_model.projects,
                    effective_project_id,
                    "project",
                )
                if project.team_id != team.id:
                    raise TeamCommandValidationError(
                        code="project_team_mismatch",
                        message=f"project {project.id} is not in team {team.id}",
                        correction_context={
                            "project_id": project.id,
                            "team_id": team.id,
                        },
                    )

            now = utc_now()
            wake_result: dict[str, Any] = {"events": []}
            item = append_operator_directive(
                state_model,
                team_id=team.id,
                recipient_agent_id=recipient.id,
                message=normalized_message,
                result=wake_result,
                now=now,
                project_id=effective_project_id,
                task_id=normalized_task_id,
                title=normalized_title,
                source=normalized_source,
                source_message_id=normalized_source_message_id,
                created_by=normalized_created_by,
                requesting_session_id=normalized_session_id,
            )
            return TeamCommandApplyResult(
                response_payload={
                    "inbox_item_id": item.id,
                    "recipient_agent_id": item.recipient_agent_id,
                    "team_id": team.id,
                    "project_id": effective_project_id,
                    "task_id": item.task_id,
                    "operator_directive": item.model_dump(mode="json"),
                    "governance_group_id": group.id,
                    "progress_made": True,
                    "events": wake_result["events"],
                },
                created_domain_ids=[item.id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=[],
            apply_command=_apply,
            require_active_dispatch=False,
        )


__all__ = ["TeamGovernanceCommandService"]
