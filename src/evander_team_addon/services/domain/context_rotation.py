from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from ...models import (
    AgentContextClearingPolicy,
    AgentRuntime,
    AgentStatus,
    PersistentAgent,
    TeamState,
)

from .common import _ensure_utc
from .stand_down import _active_stand_downs_for_agent
from .work_due import _clear_idle_context_tracking
from .wake import (
    _active_tasks_for_agent,
    _append_agent_wake_requested_event,
    _pending_ephemeral_acceptances_for_agent,
    _pending_inbox_for_agent,
    _pending_reviews_for_agent,
)

MAX_CONTEXT_CLEARING_IDLE_GRACE_SECONDS = 7 * 24 * 60 * 60


def _maybe_mark_idle_context_rotation_due(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
    result: dict[str, Any],
) -> None:
    agent = state.agents.get(agent_id)
    if agent is None:
        return
    runtime = state.agent_runtimes.setdefault(agent.id, AgentRuntime(agent_id=agent.id))
    normalized_now = _ensure_utc(now)
    if runtime.context_clearing_policy != AgentContextClearingPolicy.IDLE:
        _clear_idle_context_tracking(runtime)
        return
    if runtime.context_rotation_pending:
        _clear_idle_context_tracking(runtime)
        return
    if not _has_activity_since_last_context_rotation(state, agent=agent, runtime=runtime):
        _clear_idle_context_tracking(runtime)
        return

    if not _is_agent_truly_idle(state, agent=agent, runtime=runtime, now=normalized_now):
        _clear_idle_context_tracking(runtime)
        return

    idle_due_at = runtime.context_idle_rotation_due_at
    if idle_due_at is not None and _ensure_utc(idle_due_at) <= normalized_now:
        _mark_context_rotation_pending(
            state,
            agent=agent,
            runtime=runtime,
            reason="idle",
            requested_by=None,
            requested_at=normalized_now,
            request_note=None,
        )
        return

    grace_seconds = runtime.context_clearing_idle_grace_seconds
    if grace_seconds <= 0:
        _mark_context_rotation_pending(
            state,
            agent=agent,
            runtime=runtime,
            reason="idle",
            requested_by=None,
            requested_at=normalized_now,
            request_note=None,
        )
        return

    idle_since_at = runtime.context_idle_since_at
    if idle_since_at is None:
        idle_since_at = normalized_now
    else:
        idle_since_at = _ensure_utc(idle_since_at)
    desired_due_at = idle_since_at + timedelta(seconds=grace_seconds)
    runtime.context_idle_since_at = idle_since_at
    runtime.context_idle_rotation_due_at = desired_due_at
    runtime.next_wake_at = desired_due_at
    runtime.updated_at = normalized_now
    _append_idle_context_rotation_wake_requested_event(
        state,
        agent_id=agent.id,
        result=result,
        now=normalized_now,
    )


def _append_idle_context_rotation_wake_requested_event(
    state: TeamState,
    *,
    agent_id: str,
    result: dict[str, Any],
    now: datetime,
) -> None:
    runtime = state.agent_runtimes.get(agent_id)
    if runtime is None or runtime.context_idle_rotation_due_at is None:
        return
    normalized_now = _ensure_utc(now)
    due_at = _ensure_utc(runtime.context_idle_rotation_due_at)
    due_key = int(due_at.timestamp() * 1_000_000)
    _append_agent_wake_requested_event(
        state,
        agent_id=agent_id,
        result=result,
        reason="idle_grace_expired",
        now=normalized_now,
        run_at=due_at,
        idempotency_suffix=f"idle-grace:{due_key}",
        payload_extra={"context_idle_rotation_due_at": due_at.isoformat().replace("+00:00", "Z")},
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

    if hasattr(state, "has_recent_lifecycle_activity"):
        return state.has_recent_lifecycle_activity(agent.id, rotated_at)
    tasks, inbox = state.tasks.values(), state.inbox.values()
    for task in tasks:
        if task.owner_agent_id != agent.id:
            continue
        if _newer_than_rotation(task.created_at) or _newer_than_rotation(task.updated_at):
            return True
    for item in inbox:
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
    _clear_idle_context_tracking(runtime)
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
    result: dict[str, Any],
) -> None:
    previous_policy = runtime.context_clearing_policy
    if previous_policy != policy:
        _clear_pending_auto_context_rotation(runtime)
        if AgentContextClearingPolicy.WORK_LINEAGE in {previous_policy, policy}:
            runtime.work_lineage_thread_ids = {}
            runtime.work_lineage_thread_dispatch_ids = {}
    runtime.context_clearing_policy = policy
    runtime.auto_context_clearing_enabled = policy != AgentContextClearingPolicy.OFF
    runtime.updated_at = now
    if policy == AgentContextClearingPolicy.IDLE:
        _maybe_mark_idle_context_rotation_due(
            state,
            agent_id=agent.id,
            now=now,
            result=result,
        )


def _context_clearing_idle_grace_seconds_from_value(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("context_clearing_idle_grace_seconds must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise ValueError("context_clearing_idle_grace_seconds must be an integer") from None
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("context_clearing_idle_grace_seconds must be an integer")
    if isinstance(value, str) and str(normalized) != value.strip():
        raise ValueError("context_clearing_idle_grace_seconds must be an integer")
    if normalized < 0 or normalized > MAX_CONTEXT_CLEARING_IDLE_GRACE_SECONDS:
        raise ValueError("context_clearing_idle_grace_seconds must be between 0 and 604800")
    return normalized


def _set_context_clearing_idle_grace_seconds(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    grace_seconds: int,
    now: datetime,
    result: dict[str, Any],
) -> None:
    runtime.context_clearing_idle_grace_seconds = grace_seconds
    runtime.updated_at = now
    if runtime.context_clearing_policy == AgentContextClearingPolicy.IDLE:
        if runtime.context_idle_since_at is not None:
            idle_since_at = _ensure_utc(runtime.context_idle_since_at)
            runtime.context_idle_since_at = idle_since_at
            runtime.context_idle_rotation_due_at = idle_since_at + timedelta(seconds=grace_seconds)
            runtime.next_wake_at = runtime.context_idle_rotation_due_at
        _maybe_mark_idle_context_rotation_due(
            state,
            agent_id=agent.id,
            now=now,
            result=result,
        )


def _clear_pending_auto_context_rotation(runtime: AgentRuntime) -> None:
    _clear_idle_context_tracking(runtime)
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
    "_append_idle_context_rotation_wake_requested_event",
    "_has_activity_since_last_context_rotation",
    "_is_agent_truly_idle",
    "_mark_context_rotation_pending",
    "_resolved_context_clearing_policy",
    "_context_clearing_policy_from_value",
    "_set_context_clearing_policy",
    "_context_clearing_idle_grace_seconds_from_value",
    "_set_context_clearing_idle_grace_seconds",
    "_clear_pending_auto_context_rotation",
    "_clear_idle_context_tracking",
    "_build_context_handoff_summary",
]
