from __future__ import annotations

from typing import Any
from uuid import uuid4
from ...models import (
    AgentStandDown,
    AgentStandDownSource,
    AgentStatus,
    PersistentAgent,
    TeamState,
)

from .agent_access import _is_direct_subordinate
from .common import (
    _require_present,
    _required_text,
    utc_now,
)
from .work_due import _mark_agent_work_due


def _active_stand_downs_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[AgentStandDown]:
    root_ids: list[str] = []
    seen: set[str] = set()
    current_id: str | None = agent_id
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        root_ids.append(current_id)
        current = state.agents.get(current_id)
        current_id = current.reports_to_agent_id if current is not None else None
    root_set = set(root_ids)
    return sorted(
        [
            stand_down
            for stand_down in state.stand_downs.values()
            if stand_down.active and stand_down.agent_id in root_set
        ],
        key=lambda item: (item.set_at, item.id),
    )


def _stand_down_error_detail(stand_downs: list[AgentStandDown]) -> str:
    if not stand_downs:
        return "active stand-down"
    primary = stand_downs[-1]
    return f"{primary.source.value} stand-down on {primary.agent_id}: {primary.reason}"


def _agent_subtree_ids(state: TeamState, agent_id: str) -> list[str]:
    if agent_id not in state.agents:
        return []
    ordered: list[str] = []
    stack = [agent_id]
    seen: set[str] = set()
    while stack:
        current_id = stack.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        ordered.append(current_id)
        agent = state.agents.get(current_id)
        if agent is None:
            continue
        stack.extend(reversed(agent.subordinate_agent_ids))
    return ordered


def _apply_superior_stand_down(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
) -> str:
    target_id = _required_text(request.get("agent_id"), "agent_id")
    reason = _required_text(request.get("reason"), "reason")
    target = _require_present(state.agents, target_id, "agent")
    if target.team_id != actor.team_id:
        raise ValueError(f"agent {target.id} is not in team {actor.team_id}")
    if not _is_direct_subordinate(actor, target.id):
        raise ValueError(f"agent {actor.id} can only stand down a direct subordinate")
    if target.status == AgentStatus.ARCHIVED:
        raise ValueError(f"agent {target.id} is archived")
    now = utc_now()
    stand_down = AgentStandDown(
        id=f"stand-down-{uuid4().hex}",
        agent_id=target.id,
        reason=reason,
        source=AgentStandDownSource.SUPERIOR,
        set_by=actor.id,
        set_at=now,
    )
    state.stand_downs[stand_down.id] = stand_down
    for affected_id in _agent_subtree_ids(state, target.id):
        runtime = state.agent_runtimes.get(affected_id)
        if runtime is None:
            continue
        runtime.next_wake_at = None
        runtime.updated_at = now
    return stand_down.id


def _apply_superior_stand_down_revocation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
) -> list[str]:
    target_id = _required_text(request.get("agent_id"), "agent_id")
    target = _require_present(state.agents, target_id, "agent")
    if target.team_id != actor.team_id:
        raise ValueError(f"agent {target.id} is not in team {actor.team_id}")
    if not _is_direct_subordinate(actor, target.id):
        raise ValueError(f"agent {actor.id} can only revoke stand-downs for a direct subordinate")
    now = utc_now()
    revoked_ids: list[str] = []
    for stand_down in state.stand_downs.values():
        if (
            stand_down.agent_id == target.id
            and stand_down.source == AgentStandDownSource.SUPERIOR
            and stand_down.set_by == actor.id
            and stand_down.active
        ):
            stand_down.cleared_by = actor.id
            stand_down.cleared_at = now
            revoked_ids.append(stand_down.id)
    if not revoked_ids:
        raise ValueError(f"agent {target.id} has no active superior stand-down by {actor.id}")
    for affected_id in _agent_subtree_ids(state, target.id):
        if _active_stand_downs_for_agent(state, affected_id):
            continue
        _mark_agent_work_due(state, agent_id=affected_id, now=now)
    return revoked_ids


__all__ = [
    "_active_stand_downs_for_agent",
    "_stand_down_error_detail",
    "_agent_subtree_ids",
    "_apply_superior_stand_down",
    "_apply_superior_stand_down_revocation",
]
