"""Record wake failures without entering the legacy action-batch application."""

from __future__ import annotations

from uuid import uuid4

from ..models import AgentRuntime, TeamState
from ..repository import TeamStateRepository
from .domain.action_feedback import _mark_action_failure
from .domain.agent_access import _resolve_action_agent


def record_agent_wake_failure(
    repository: TeamStateRepository,
    *,
    session_id: str,
    dispatch_id: str,
    error: str,
) -> TeamState:
    """Preserve the legacy failure receipt and runtime transition in one write.

    The successful batch API remains available to existing callers. Failures use
    its same domain transition without assembling a batch of mutation handlers.
    """

    def _mutate(state: TeamState) -> TeamState:
        agent = _resolve_action_agent(state, action={}, session_id=session_id)
        runtime = state.agent_runtimes.setdefault(agent.id, AgentRuntime(agent_id=agent.id))
        _mark_action_failure(
            state,
            runtime=runtime,
            agent_id=agent.id,
            dispatch_id=dispatch_id,
            action={},
            error=error or "invalid action",
            action_id=f"action-{uuid4().hex}",
            result={"errors": [], "events": []},
        )
        return state

    return repository.update(_mutate)
