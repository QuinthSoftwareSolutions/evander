from __future__ import annotations

from datetime import datetime
from ...models import (
    AgentRuntime,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamState,
)

from .chat import _clear_completed_chat_dispatch_lease


def _mark_agent_work_due(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> None:
    runtime = state.agent_runtimes.setdefault(agent_id, AgentRuntime(agent_id=agent_id))
    _clear_completed_chat_dispatch_lease(state, runtime, now=now)
    if not _agent_has_open_execution_stall(state, agent_id=agent_id):
        runtime.no_progress_count = 0
    runtime.next_wake_at = None
    _clear_low_priority_batch(runtime)
    runtime.updated_at = now


def _agent_has_open_execution_stall(
    state: TeamState,
    *,
    agent_id: str,
) -> bool:
    return any(
        blocker.state == TeamBlockerState.OPEN
        and blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL
        and blocker.source_id == agent_id
        for blocker in state.blockers.values()
    )


def _clear_low_priority_batch(runtime: AgentRuntime) -> None:
    runtime.low_priority_batch_started_at = None
    runtime.low_priority_batch_cycle_count = 0
    runtime.low_priority_batch_due_after_cycles = None


def _advance_low_priority_batch(
    runtime: AgentRuntime,
    *,
    now: datetime,
    due_after_cycles: int,
) -> bool:
    if runtime.low_priority_batch_started_at is None:
        runtime.low_priority_batch_started_at = now
        runtime.low_priority_batch_cycle_count = 0
    runtime.low_priority_batch_cycle_count += 1
    runtime.low_priority_batch_due_after_cycles = due_after_cycles
    return runtime.low_priority_batch_cycle_count >= due_after_cycles


__all__ = [
    "_mark_agent_work_due",
    "_clear_low_priority_batch",
    "_advance_low_priority_batch",
    "_agent_has_open_execution_stall",
]
