from __future__ import annotations


from collections.abc import Mapping
from typing import Any
from .types import (
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from ..domain_helpers import (
    AgentStatus,
    PersistentAgent,
    TeamConversation,
    TeamConversationMessage,
    TeamInboxPriority,
    TeamState,
    _optional_text,
)

def _team_command_priority(
    value: TeamInboxPriority | str | None,
    *,
    field_name: str,
    default: TeamInboxPriority,
) -> TeamInboxPriority:
    if value is None:
        return default
    if isinstance(value, TeamInboxPriority):
        return value
    try:
        return TeamInboxPriority(str(value or "").strip())
    except ValueError:
        allowed = ", ".join(priority.value for priority in TeamInboxPriority)
        raise ValueError(f"{field_name} must be one of: {allowed}") from None

def _require_command_agent(
    resolved: _ResolvedTeamCommandContext,
    tool_name: str,
) -> PersistentAgent:
    if resolved.agent is None or resolved.dispatch_id is None:
        raise TeamCommandValidationError(
            code="invalid_command_scope",
            message=f"{tool_name} requires an active agent dispatch",
        )
    return resolved.agent

def _required_command_text(
    value: object,
    *,
    field_name: str,
    code: str,
) -> str:
    text = _optional_text(value)
    if text is None:
        raise TeamCommandValidationError(
            code=code,
            message=f"{field_name} must be a non-empty meaningful string",
            correction_context={"field": field_name},
        )
    return text

def _team_command_validation_error_from_exception(
    exc: Exception,
    *,
    code: str,
    correction_context: Mapping[str, Any] | None = None,
) -> TeamCommandValidationError:
    return TeamCommandValidationError(
        code=code,
        message=str(exc),
        correction_context=correction_context,
    )

def _require_command_agent_by_id(
    state: TeamState,
    agent_id: str,
    *,
    field_name: str,
) -> PersistentAgent:
    agent = state.agents.get(agent_id)
    if agent is None:
        raise TeamCommandValidationError(
            code="agent_not_found",
            message=f"agent {agent_id} does not exist",
            correction_context={field_name: agent_id},
        )
    return agent

def _validate_command_recipient_active(agent: PersistentAgent) -> None:
    if agent.status != AgentStatus.ACTIVE:
        raise TeamCommandValidationError(
            code="recipient_not_active",
            message=f"recipient agent {agent.id} is {agent.status.value}",
            correction_context={"recipient_agent_id": agent.id, "status": agent.status.value},
        )

def _validate_command_same_team(actor: PersistentAgent, recipient: PersistentAgent) -> None:
    if recipient.team_id != actor.team_id:
        raise TeamCommandValidationError(
            code="recipient_outside_team",
            message=f"recipient agent {recipient.id} is not in team {actor.team_id}",
            correction_context={
                "caller_agent_id": actor.id,
                "caller_team_id": actor.team_id,
                "recipient_agent_id": recipient.id,
                "recipient_team_id": recipient.team_id,
            },
        )

def _resolve_upward_command_recipient(
    state: TeamState,
    *,
    agent: PersistentAgent,
    recipient_agent_id: str | None,
    tool_name: str,
    communication_kind: str,
) -> PersistentAgent:
    if agent.reports_to_agent_id is None:
        raise TeamCommandValidationError(
            code="top_level_manager_requires_operator_escalation",
            message=(
                f"agent {agent.id} is a top-level manager and must use "
                "team_escalate_operator for operator escalation"
            ),
            correction_context={
                "caller_agent_id": agent.id,
                "recommended_tool": "team_escalate_operator",
                "tool_name": tool_name,
            },
        )
    effective_recipient_id = recipient_agent_id or agent.reports_to_agent_id
    if effective_recipient_id != agent.reports_to_agent_id:
        raise TeamCommandValidationError(
            code="recipient_not_direct_superior",
            message=(
                f"agent {agent.id} can only send {communication_kind} to its "
                f"direct superior {agent.reports_to_agent_id}"
            ),
            correction_context={
                "caller_agent_id": agent.id,
                "recipient_agent_id": effective_recipient_id,
                "direct_superior_agent_id": agent.reports_to_agent_id,
            },
        )
    recipient = _require_command_agent_by_id(
        state,
        effective_recipient_id,
        field_name="recipient_agent_id",
    )
    _validate_command_recipient_active(recipient)
    _validate_command_same_team(agent, recipient)
    return recipient

def _validate_command_project_scope(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
) -> None:
    if project_id is None:
        return
    project = state.projects.get(project_id)
    if project is None:
        raise TeamCommandValidationError(
            code="project_not_found",
            message=f"project {project_id} does not exist",
            correction_context={"project_id": project_id},
        )
    if project.team_id != team_id:
        raise TeamCommandValidationError(
            code="project_outside_team",
            message=f"project {project.id} is not in team {team_id}",
            correction_context={
                "project_id": project.id,
                "project_team_id": project.team_id,
                "caller_team_id": team_id,
            },
        )

def _validate_command_task_scope(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task_id: str | None,
) -> None:
    if task_id is None:
        return
    task = state.tasks.get(task_id)
    if task is None:
        raise TeamCommandValidationError(
            code="task_not_found",
            message=f"task {task_id} does not exist",
            correction_context={"task_id": task_id},
        )
    project = state.projects.get(task.project_id)
    if project is None:
        raise TeamCommandValidationError(
            code="task_project_not_found",
            message=f"task {task.id} references missing project {task.project_id}",
            correction_context={"task_id": task.id, "project_id": task.project_id},
        )
    if project.team_id != agent.team_id:
        raise TeamCommandValidationError(
            code="task_outside_team",
            message=f"task {task.id} is not in team {agent.team_id}",
            correction_context={
                "task_id": task.id,
                "project_id": project.id,
                "project_team_id": project.team_id,
                "caller_team_id": agent.team_id,
            },
        )

def _require_command_conversation(
    state: TeamState,
    *,
    conversation_id: str,
    agent: PersistentAgent,
) -> TeamConversation:
    conversation = state.conversations.get(conversation_id)
    if conversation is None:
        raise TeamCommandValidationError(
            code="conversation_not_found",
            message=f"conversation {conversation_id} does not exist",
            correction_context={"conversation_id": conversation_id},
        )
    if conversation.agent_id != agent.id:
        raise TeamCommandValidationError(
            code="conversation_not_owned",
            message=f"agent {agent.id} cannot reply to conversation {conversation.id}",
            correction_context={
                "conversation_id": conversation.id,
                "conversation_agent_id": conversation.agent_id,
                "caller_agent_id": agent.id,
            },
        )
    return conversation

def _require_command_chat_message(
    state: TeamState,
    *,
    message_id: str,
    conversation_id: str,
) -> TeamConversationMessage:
    message = state.conversation_messages.get(message_id)
    if message is None:
        raise TeamCommandValidationError(
            code="chat_message_not_found",
            message=f"conversation message {message_id} does not exist",
            correction_context={"message_id": message_id},
        )
    if message.conversation_id != conversation_id:
        raise TeamCommandValidationError(
            code="chat_message_wrong_conversation",
            message=f"message {message.id} is not in conversation {conversation_id}",
            correction_context={
                "message_id": message.id,
                "message_conversation_id": message.conversation_id,
                "conversation_id": conversation_id,
            },
        )
    return message

__all__ = [
    '_team_command_priority',
    '_require_command_agent',
    '_required_command_text',
    '_team_command_validation_error_from_exception',
    '_require_command_agent_by_id',
    '_validate_command_recipient_active',
    '_validate_command_same_team',
    '_resolve_upward_command_recipient',
    '_validate_command_project_scope',
    '_validate_command_task_scope',
    '_require_command_conversation',
    '_require_command_chat_message',
]
