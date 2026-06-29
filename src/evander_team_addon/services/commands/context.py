from __future__ import annotations


import re
from .types import (
    TeamCommandContext,
    TeamCommandValidationError,
)
from ...models import (
    EphemeralAssignment,
    GovernanceGroup,
    PersistentAgent,
    TeamState,
)
from ..domain.agent_access import _agent_for_session
from ..domain.common import (
    _optional_text,
    _required_text,
)

def _normalize_team_command_context(context: TeamCommandContext) -> TeamCommandContext:
    return TeamCommandContext(
        ambient_session_id=_required_text(
            context.ambient_session_id,
            "ambient_session_id",
        ),
        tool_name=_required_text(context.tool_name, "tool_name"),
        request_id=_optional_text(context.request_id),
        caller_agent_id=_optional_text(context.caller_agent_id),
        governance_group_id=_optional_text(context.governance_group_id),
        assignment_id=_optional_text(context.assignment_id),
        active_dispatch_id=_optional_text(context.active_dispatch_id),
        wake_session_class=_optional_text(context.wake_session_class) or "team",
        correlation_id=_optional_text(context.correlation_id),
        causation_id=_optional_text(context.causation_id),
        progress_classification=_optional_text(context.progress_classification) or "none",
    )

def _team_command_context_scope(context: TeamCommandContext) -> str:
    wake_class = _normalized_wake_session_class(context.wake_session_class)
    governance_scope = context.governance_group_id is not None or wake_class in {
        "governance",
        "governance_manager",
        "team_governance",
    }
    assignment_scope = context.assignment_id is not None or wake_class in {
        "ephemeral",
        "ephemeral_agent",
        "ephemeral_worker",
        "worker",
        "worker_assignment",
    }
    if governance_scope and assignment_scope:
        raise TeamCommandValidationError(
            code="ambiguous_command_scope",
            message="command context cannot be both governance and assignment scoped",
            correction_context={"wake_session_class": context.wake_session_class},
        )
    if governance_scope:
        return "governance"
    if assignment_scope:
        return "assignment"
    return "agent"

def _normalized_wake_session_class(value: object) -> str:
    text = _optional_text(value) or "team"
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")

def _resolve_command_agent(
    state: TeamState,
    context: TeamCommandContext,
) -> PersistentAgent:
    if context.caller_agent_id is not None:
        agent = state.agents.get(context.caller_agent_id)
        if agent is None:
            raise TeamCommandValidationError(
                code="agent_not_found",
                message=f"agent {context.caller_agent_id} does not exist",
                correction_context={"agent_id": context.caller_agent_id},
            )
        if agent.session_id != context.ambient_session_id:
            raise TeamCommandValidationError(
                code="ambient_identity_conflict",
                message=(
                    f"ambient session {context.ambient_session_id} does not belong "
                    f"to agent {agent.id}"
                ),
                correction_context={
                    "agent_id": agent.id,
                    "ambient_session_id": context.ambient_session_id,
                    "agent_session_id": agent.session_id,
                },
            )
        return agent
    try:
        return _agent_for_session(state, session_id=context.ambient_session_id)
    except ValueError as exc:
        raise TeamCommandValidationError(
            code="unknown_session",
            message=str(exc),
            correction_context={"session_id": context.ambient_session_id},
        ) from None

def _resolve_command_governance_group(
    state: TeamState,
    context: TeamCommandContext,
    agent: PersistentAgent,
) -> tuple[GovernanceGroup | None, TeamCommandValidationError | None]:
    if context.governance_group_id is not None:
        group = state.governance_groups.get(context.governance_group_id)
        if group is None:
            return None, TeamCommandValidationError(
                code="governance_group_not_found",
                message=f"governance group {context.governance_group_id} does not exist",
                correction_context={"governance_group_id": context.governance_group_id},
            )
        if group.archived:
            return group, TeamCommandValidationError(
                code="governance_group_archived",
                message=f"governance group {group.id} is archived",
                correction_context={"governance_group_id": group.id},
            )
        if group.manager_agent_id != agent.id:
            return group, TeamCommandValidationError(
                code="governance_group_not_managed_by_caller",
                message=(f"agent {agent.id} is not the manager for governance group {group.id}"),
                correction_context={
                    "governance_group_id": group.id,
                    "manager_agent_id": group.manager_agent_id,
                    "caller_agent_id": agent.id,
                },
            )
        return group, None

    candidates = [
        group
        for group in state.governance_groups.values()
        if group.manager_agent_id == agent.id and not group.archived
    ]
    if context.active_dispatch_id is not None:
        candidates = [
            group
            for group in candidates
            if (runtime := state.governance_runtimes.get(group.id)) is not None
            and runtime.active_dispatch_id == context.active_dispatch_id
        ]
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, TeamCommandValidationError(
            code="governance_group_not_found",
            message="no active governance group is managed by the ambient session",
            correction_context={"caller_agent_id": agent.id},
        )
    return None, TeamCommandValidationError(
        code="governance_group_required",
        message="governance_group_id is required when the manager owns multiple groups",
        correction_context={
            "caller_agent_id": agent.id,
            "governance_group_ids": sorted(group.id for group in candidates),
        },
    )

def _resolve_command_assignment(
    state: TeamState,
    context: TeamCommandContext,
) -> tuple[EphemeralAssignment | None, TeamCommandValidationError | None]:
    explicit_assignment_id = context.assignment_id
    if explicit_assignment_id is not None:
        assignment = state.ephemeral_assignments.get(explicit_assignment_id)
        if assignment is None:
            return None, TeamCommandValidationError(
                code="assignment_not_found",
                message=f"ephemeral assignment {explicit_assignment_id} does not exist",
                correction_context={"assignment_id": explicit_assignment_id},
            )
        if assignment.session_id != context.ambient_session_id:
            return assignment, TeamCommandValidationError(
                code="assignment_session_mismatch",
                message=(
                    f"ambient session {context.ambient_session_id} does not match "
                    f"ephemeral assignment {assignment.id}"
                ),
                correction_context={
                    "assignment_id": assignment.id,
                    "assignment_session_id": assignment.session_id,
                    "ambient_session_id": context.ambient_session_id,
                },
            )
        return assignment, None

    matches = [
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.session_id == context.ambient_session_id
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, TeamCommandValidationError(
            code="unknown_worker_session",
            message="no ephemeral assignment matches the ambient worker session",
            correction_context={"session_id": context.ambient_session_id},
        )
    return None, TeamCommandValidationError(
        code="ambiguous_worker_session",
        message="multiple ephemeral assignments match the ambient worker session",
        correction_context={
            "session_id": context.ambient_session_id,
            "assignment_ids": sorted(assignment.id for assignment in matches),
        },
    )

__all__ = [
    '_normalize_team_command_context',
    '_team_command_context_scope',
    '_normalized_wake_session_class',
    '_resolve_command_agent',
    '_resolve_command_governance_group',
    '_resolve_command_assignment',
]
