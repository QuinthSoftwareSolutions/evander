from __future__ import annotations

from datetime import datetime
from typing import Literal
from ...models import (
    AgentContextClearingPolicy,
    AgentRuntime,
    AgentStatus,
    PersistentAgent,
    TeamState,
)

from .common import _ensure_utc
from .stand_down import _active_stand_downs_for_agent
from .wake import (
    _active_tasks_for_agent,
    _pending_ephemeral_acceptances_for_agent,
    _pending_inbox_for_agent,
    _pending_reviews_for_agent,
)


def _maybe_mark_idle_context_rotation_due(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> None:
    agent = state.agents.get(agent_id)
    if agent is None:
        return
    if _active_stand_downs_for_agent(state, agent.id):
        return
    runtime = state.agent_runtimes.setdefault(agent.id, AgentRuntime(agent_id=agent.id))
    if (
        runtime.context_clearing_policy != AgentContextClearingPolicy.IDLE
        or runtime.context_rotation_pending
    ):
        return
    if not _has_activity_since_last_context_rotation(state, agent=agent, runtime=runtime):
        return
    if not _is_agent_truly_idle(state, agent=agent, runtime=runtime, now=now):
        return
    _mark_context_rotation_pending(
        state,
        agent=agent,
        runtime=runtime,
        reason="idle",
        requested_by=None,
        requested_at=now,
        request_note=None,
    )


def _has_activity_since_last_context_rotation(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
) -> bool:
    rotated_at = runtime.last_context_rotated_at
    if rotated_at is None:
        return True
    rotated_at = _ensure_utc(rotated_at)
    if runtime.last_progress_at is not None and _ensure_utc(runtime.last_progress_at) > rotated_at:
        return True

    def _newer_than_rotation(value: datetime | None) -> bool:
        return value is not None and _ensure_utc(value) > rotated_at

    for task in state.tasks.values():
        if task.owner_agent_id != agent.id:
            continue
        if _newer_than_rotation(task.created_at) or _newer_than_rotation(task.updated_at):
            return True
    for item in state.inbox.values():
        if item.recipient_agent_id != agent.id:
            continue
        if _newer_than_rotation(item.created_at) or _newer_than_rotation(item.updated_at):
            return True
    for review in state.review_requests.values():
        if review.reviewer_agent_id != agent.id:
            continue
        if _newer_than_rotation(review.created_at) or _newer_than_rotation(review.decision_at):
            return True
    for assignment in state.ephemeral_assignments.values():
        if assignment.parent_agent_id != agent.id:
            continue
        if _newer_than_rotation(assignment.created_at) or _newer_than_rotation(
            assignment.updated_at
        ):
            return True
    return False


def _is_agent_truly_idle(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    now: datetime,
) -> bool:
    if agent.status != AgentStatus.ACTIVE:
        return False
    if _active_stand_downs_for_agent(state, agent.id):
        return False
    if runtime.active_dispatch_id:
        return False
    if runtime.wake_leased_until is not None and _ensure_utc(runtime.wake_leased_until) > now:
        return False
    if _pending_inbox_for_agent(state, agent.id):
        return False
    if _active_tasks_for_agent(state, agent.id):
        return False
    if _pending_reviews_for_agent(state, agent.id):
        return False
    if _pending_ephemeral_acceptances_for_agent(state, agent.id):
        return False
    return True


def _mark_context_rotation_pending(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    reason: Literal["idle", "manual", "every_wake"],
    requested_by: str | None,
    requested_at: datetime,
    request_note: str | None,
) -> None:
    if reason not in {"idle", "manual", "every_wake"}:
        raise ValueError("context rotation reason must be idle, manual, or every_wake")
    runtime.context_rotation_pending = True
    runtime.context_rotation_reason = reason
    runtime.context_rotation_requested_by = requested_by
    runtime.context_rotation_requested_at = requested_at
    runtime.context_rotation_request_note = request_note
    runtime.next_wake_at = requested_at
    runtime.context_handoff_preparation_pending = reason in {"idle", "manual"}
    runtime.context_handoff_preparation_status = (
        "pending" if runtime.context_handoff_preparation_pending else None
    )
    runtime.last_agent_handoff_summary = None
    runtime.last_agent_handoff_at = None
    runtime.last_handoff_summary = _build_context_handoff_summary(
        state,
        agent=agent,
        now=requested_at,
        reason=reason,
        request_note=request_note,
    )
    runtime.updated_at = requested_at


def _resolved_context_clearing_policy(
    *,
    context_clearing_policy: AgentContextClearingPolicy | str | None,
    set_context_clearing_policy: bool,
    auto_context_clearing_enabled: bool | None,
    set_auto_context_clearing_enabled: bool,
) -> AgentContextClearingPolicy | None:
    if set_context_clearing_policy:
        return _context_clearing_policy_from_value(context_clearing_policy)
    if set_auto_context_clearing_enabled:
        return (
            AgentContextClearingPolicy.IDLE
            if bool(auto_context_clearing_enabled)
            else AgentContextClearingPolicy.OFF
        )
    return None


def _context_clearing_policy_from_value(
    value: AgentContextClearingPolicy | str | None,
) -> AgentContextClearingPolicy:
    if value is None:
        return AgentContextClearingPolicy.OFF
    if isinstance(value, AgentContextClearingPolicy):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return AgentContextClearingPolicy.OFF
    try:
        return AgentContextClearingPolicy(normalized)
    except ValueError:
        allowed = ", ".join(policy.value for policy in AgentContextClearingPolicy)
        raise ValueError(f"context_clearing_policy must be one of: {allowed}") from None


def _set_context_clearing_policy(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    policy: AgentContextClearingPolicy,
    now: datetime,
) -> None:
    previous_policy = runtime.context_clearing_policy
    if previous_policy != policy:
        _clear_pending_auto_context_rotation(runtime)
    runtime.context_clearing_policy = policy
    runtime.auto_context_clearing_enabled = policy != AgentContextClearingPolicy.OFF
    runtime.updated_at = now
    if policy == AgentContextClearingPolicy.IDLE:
        _maybe_mark_idle_context_rotation_due(state, agent_id=agent.id, now=now)


def _clear_pending_auto_context_rotation(runtime: AgentRuntime) -> None:
    if not runtime.context_rotation_pending or runtime.context_rotation_reason not in {
        "idle",
        "every_wake",
    }:
        return
    runtime.context_rotation_pending = False
    runtime.context_rotation_reason = None
    runtime.context_rotation_requested_by = None
    runtime.context_rotation_requested_at = None
    runtime.context_rotation_request_note = None
    runtime.next_wake_at = None
    runtime.last_handoff_summary = None
    runtime.context_handoff_preparation_pending = False
    runtime.context_handoff_preparation_status = None
    runtime.last_agent_handoff_summary = None
    runtime.last_agent_handoff_at = None


def _build_context_handoff_summary(
    state: TeamState,
    *,
    agent: PersistentAgent,
    now: datetime,
    reason: Literal["idle", "manual", "every_wake"],
    request_note: str | None,
) -> str:
    lines = [
        f"Context continuity checkpoint at {now.isoformat()} ({reason}).",
    ]
    if request_note:
        lines.append(f"Operator note: {_handoff_text(request_note, 1200)}")

    pending_ephemeral = _pending_ephemeral_acceptances_for_agent(state, agent.id)
    if pending_ephemeral:
        lines.append("Continuity not represented by ordinary dispatch records:")
        for assignment in pending_ephemeral[:12]:
            lines.append(
                f"- pending ephemeral acceptance {assignment.id}: "
                f"task {assignment.task_id}, session {assignment.session_id}"
            )
        if len(pending_ephemeral) > 12:
            lines.append(f"- {len(pending_ephemeral) - 12} more; use Team read tools.")

    return "\n".join(lines)


def _handoff_text(value: str, limit: int) -> str:
    compact = " ".join(str(value).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


__all__ = [
    "_maybe_mark_idle_context_rotation_due",
    "_has_activity_since_last_context_rotation",
    "_is_agent_truly_idle",
    "_mark_context_rotation_pending",
    "_resolved_context_clearing_policy",
    "_context_clearing_policy_from_value",
    "_set_context_clearing_policy",
    "_clear_pending_auto_context_rotation",
    "_build_context_handoff_summary",
]
