"""Agent runtime and wake projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from evander.models import utc_now

from .attention_projections import (
    _runtime_attention_payload,
    team_session_health_payload,
)
from .model_common import (
    _DEFAULT_LOW_PRIORITY_WAKE_AFTER_CYCLES,
    _DEFAULT_MAX_NO_PROGRESS_COUNT,
    AgentContextClearingPolicy,
    AgentStatus,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamRecordState,
    TeamTaskState,
    _ensure_utc,
    _json_datetime,
    _optional_text,
)
from .model_dispatch import (
    TeamDispatchStatus,
)
from .model_inbox_memory import (
    AgentActionRecord,
    AgentInboxItem,
    TeamBlocker,
)
from .model_organization import (
    AgentRuntime,
    PersistentAgent,
)
from .model_queries import (
    _effective_stand_down_payloads,
    agent_lead_project_ids,
    agent_project_assignment_payloads,
    agent_project_ids,
    effective_role_permissions,
)
from .model_reviews import (
    ReviewRequest,
)
from .model_state import (
    TeamState,
)
from .model_tasks import (
    AgentTask,
)
from .model_work_fingerprints import (
    wake_work_fingerprint,
    wake_work_record_fingerprints,
)


def runtime_operations_payload(
    state: TeamState,
    *,
    now: datetime | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    agent_ids: set[str] | None = None,
) -> dict[str, Any]:
    effective_now = _ensure_utc(now or utc_now())
    included_agent_ids = set(agent_ids) if agent_ids is not None else None
    session_health = team_session_health_payload(
        state,
        session_records=session_records,
        agent_execution_records=agent_execution_records,
        now=effective_now,
        agent_ids=included_agent_ids,
    )
    session_health_by_agent = {
        row["agent_id"]: row for row in session_health if row.get("agent_id")
    }
    agents = [
        _runtime_agent_payload(
            state,
            agent,
            effective_now,
            session_health=session_health_by_agent.get(agent.id),
        )
        for agent in sorted(state.agents.values(), key=lambda item: item.id)
        if included_agent_ids is None or agent.id in included_agent_ids
    ]
    attention = _runtime_attention_payload(
        state,
        session_health=session_health,
        agent_ids=included_agent_ids,
        now=effective_now,
    )
    return {
        "generated_at": effective_now.isoformat(),
        "agents": agents,
        "attention": attention,
        "session_health": session_health,
    }


def _runtime_agent_payload(
    state: TeamState,
    agent: PersistentAgent,
    now: datetime,
    *,
    session_health: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = state.agent_runtimes.get(agent.id)
    wake_leased_until = runtime.wake_leased_until if runtime is not None else None
    next_wake_at = runtime.next_wake_at if runtime is not None else None
    lease_active = runtime is not None and (
        runtime.wake_leased_until is not None and _ensure_utc(runtime.wake_leased_until) > now
    )
    effective_stand_downs = _effective_stand_down_payloads(state, agent.id)
    wake_due = _runtime_agent_wake_due(
        state,
        agent,
        runtime=runtime,
        now=now,
        effective_stand_downs=effective_stand_downs,
    )
    detail_parts = [f"session {agent.session_id}", f"status {agent.status.value}"]
    project_ids = agent_project_ids(state, agent.id)
    if project_ids:
        detail_parts.append("projects " + ", ".join(project_ids))
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None:
        detail_parts.append(f"role {role.name}")
    session_is_expired = bool(session_health and session_health.get("is_expired"))
    if session_is_expired:
        expires_at = _optional_text(session_health.get("expires_at")) or "unknown time"
        detail_parts.append(f"session expired at {expires_at}")
    if runtime is None:
        detail_parts.append("no runtime snapshot")
    elif runtime.wake_reason is not None:
        detail_parts.append(f"wake reason {runtime.wake_reason}")
    if effective_stand_downs:
        detail_parts.append("stood down")
    return {
        "id": agent.id,
        "kind": "agent_runtime",
        "severity": (
            "critical"
            if session_is_expired
            else "warning"
            if wake_due or lease_active or effective_stand_downs
            else "info"
        ),
        "title": agent.id,
        "detail": "; ".join(detail_parts),
        "team_id": agent.team_id,
        "project_ids": project_ids,
        "lead_project_ids": agent_lead_project_ids(state, agent.id),
        "project_assignments": agent_project_assignment_payloads(state, agent.id),
        "role_id": agent.role_id,
        "role_name": role.name if role is not None else None,
        "role_permissions": [
            permission.value for permission in effective_role_permissions(state, agent)
        ],
        "effective_role_permissions": [
            permission.value for permission in effective_role_permissions(state, agent)
        ],
        "session_id": agent.session_id,
        "session_present": (
            session_health.get("session_present") if session_health is not None else None
        ),
        "session_is_expired": (
            session_health.get("is_expired") if session_health is not None else None
        ),
        "session_expires_at": (
            session_health.get("expires_at") if session_health is not None else None
        ),
        "session_status": (session_health.get("status") if session_health is not None else None),
        "status": agent.status.value,
        "stand_down_active": bool(effective_stand_downs),
        "effective_stand_downs": effective_stand_downs,
        "last_wake_at": _json_datetime(runtime.last_wake_at if runtime is not None else None),
        "next_wake_at": _json_datetime(next_wake_at),
        "wake_leased_until": _json_datetime(wake_leased_until),
        "wake_reason": runtime.wake_reason if runtime is not None else None,
        "active_dispatch_id": runtime.active_dispatch_id if runtime is not None else None,
        "no_progress_count": runtime.no_progress_count if runtime is not None else 0,
        "low_priority_batch_started_at": _json_datetime(
            runtime.low_priority_batch_started_at if runtime is not None else None
        ),
        "low_priority_batch_cycle_count": (
            runtime.low_priority_batch_cycle_count if runtime is not None else 0
        ),
        "low_priority_batch_due_after_cycles": (
            runtime.low_priority_batch_due_after_cycles if runtime is not None else None
        ),
        "context_clearing_policy": (
            runtime.context_clearing_policy.value
            if runtime is not None
            else AgentContextClearingPolicy.OFF.value
        ),
        "context_clearing_idle_grace_seconds": (
            runtime.context_clearing_idle_grace_seconds if runtime is not None else 0
        ),
        "context_idle_since_at": _json_datetime(
            runtime.context_idle_since_at if runtime is not None else None
        ),
        "context_idle_rotation_due_at": _json_datetime(
            runtime.context_idle_rotation_due_at if runtime is not None else None
        ),
        "auto_context_clearing_enabled": (
            runtime.auto_context_clearing_enabled if runtime is not None else False
        ),
        "context_rotation_pending": (
            runtime.context_rotation_pending if runtime is not None else False
        ),
        "context_rotation_reason": (
            runtime.context_rotation_reason if runtime is not None else None
        ),
        "context_rotation_requested_by": (
            runtime.context_rotation_requested_by if runtime is not None else None
        ),
        "context_rotation_requested_at": _json_datetime(
            runtime.context_rotation_requested_at if runtime is not None else None
        ),
        "last_handoff_summary": (runtime.last_handoff_summary if runtime is not None else None),
        "context_handoff_preparation_pending": (
            runtime.context_handoff_preparation_pending if runtime is not None else False
        ),
        "context_handoff_preparation_status": (
            runtime.context_handoff_preparation_status if runtime is not None else None
        ),
        "last_agent_handoff_summary": (
            runtime.last_agent_handoff_summary if runtime is not None else None
        ),
        "last_agent_handoff_at": _json_datetime(
            runtime.last_agent_handoff_at if runtime is not None else None
        ),
        "last_context_rotated_at": _json_datetime(
            runtime.last_context_rotated_at if runtime is not None else None
        ),
        "last_thread_id": runtime.last_thread_id if runtime is not None else None,
        "wake_due": wake_due,
        "lease_active": lease_active,
    }


def _runtime_agent_wake_due(
    state: TeamState,
    agent: PersistentAgent,
    *,
    runtime: AgentRuntime | None,
    now: datetime,
    effective_stand_downs: list[dict[str, Any]],
) -> bool:
    if runtime is None or agent.status != AgentStatus.ACTIVE or effective_stand_downs:
        return False
    pending_inbox = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent.id and item.state == TeamInboxItemState.PENDING
    ]
    wake_inbox = [item for item in pending_inbox if _runtime_inbox_item_can_wake(item)]
    immediate_inbox = [item for item in wake_inbox if item.priority != TeamInboxPriority.LOW]
    low_priority_inbox = [item for item in wake_inbox if item.priority == TeamInboxPriority.LOW]
    pending_reviews = [
        review
        for review in state.review_requests.values()
        if review.reviewer_agent_id == agent.id
        and review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
    ]
    pending_reviews.sort(
        key=lambda item: (
            item.created_at or datetime.min.replace(tzinfo=UTC),
            item.id,
        )
    )
    blocked_review_ids = _runtime_mcp_capability_blocked_review_ids(
        state,
        agent_id=agent.id,
    )
    if blocked_review_ids:
        pending_inbox = [
            item
            for item in pending_inbox
            if not _runtime_inbox_references_review_ids(item, blocked_review_ids)
        ]
        wake_inbox = [item for item in pending_inbox if _runtime_inbox_item_can_wake(item)]
        immediate_inbox = [item for item in wake_inbox if item.priority != TeamInboxPriority.LOW]
        low_priority_inbox = [item for item in wake_inbox if item.priority == TeamInboxPriority.LOW]
        pending_reviews = [
            review for review in pending_reviews if review.id not in blocked_review_ids
        ]
    action_feedback = _latest_undelivered_action_feedback_for_agent(state, agent.id)
    due_blockers = _runtime_blockers_due_for_agent(
        state,
        agent_id=agent.id,
        now=now,
    )
    rotation_pending = runtime.context_rotation_pending
    active_tasks = [
        task
        for task in _active_tasks_for_runtime_agent(state, agent.id)
        if _runtime_task_can_wake_owner_for_work(state, task)
    ]
    task_work_ready = any(
        state.dependency_status(task.id) == "satisfied" for task in active_tasks
    ) or any(state.subtask_counts(task.id)["open"] > 0 for task in active_tasks)
    wake_eligible = (
        rotation_pending
        or bool(wake_inbox)
        or bool(pending_reviews)
        or bool(action_feedback)
        or bool(due_blockers)
        or task_work_ready
    )
    if not wake_eligible:
        return False
    no_progress_exhausted = (
        _DEFAULT_MAX_NO_PROGRESS_COUNT >= 0
        and runtime.no_progress_count >= _DEFAULT_MAX_NO_PROGRESS_COUNT
    )
    if (
        no_progress_exhausted
        and not rotation_pending
        and not action_feedback
        and not due_blockers
        and not wake_work_changed_since_latest_attempt(
            state,
            agent_id=agent.id,
            inbox_items=wake_inbox,
            reviews=pending_reviews,
        )
    ):
        return False
    due_by_time = runtime.next_wake_at is None or _ensure_utc(runtime.next_wake_at) <= now
    if rotation_pending or immediate_inbox or pending_reviews or action_feedback or due_blockers:
        return True
    if task_work_ready and due_by_time:
        if no_progress_exhausted:
            return False
        return True
    if low_priority_inbox:
        return _runtime_low_priority_batch_due(runtime)
    return False


def _runtime_mcp_capability_blocked_review_ids(
    state: TeamState,
    *,
    agent_id: str,
) -> set[str]:
    blocked_review_ids: set[str] = set()
    for blocker in state.blockers.values():
        if (
            blocker.state != TeamBlockerState.OPEN
            or blocker.source_kind != TeamBlockerSourceKind.MCP_CAPABILITY
        ):
            continue
        identity = blocker.mcp_capability_identity
        if identity is not None:
            if identity.agent_id == agent_id:
                blocked_review_ids.update(identity.review_request_ids)
            continue
        prefix = f"{agent_id}:"
        if blocker.source_id.startswith(prefix):
            blocked_review_ids.update(
                review_id for review_id in blocker.source_id[len(prefix) :].split("|") if review_id
            )
    return blocked_review_ids


def _runtime_inbox_references_review_ids(
    item: AgentInboxItem,
    review_request_ids: set[str],
) -> bool:
    if not review_request_ids:
        return False
    review_id = item.payload.get("review_request_id")
    return isinstance(review_id, str) and review_id.strip() in review_request_ids


def wake_work_changed_since_latest_attempt(
    state: TeamState,
    *,
    agent_id: str,
    inbox_items: Sequence[AgentInboxItem],
    reviews: Sequence[ReviewRequest],
) -> bool:
    """Whether pending inbox/review work changed since its latest terminal attempt.

    With no prior delivered dispatch, pending work receives one bounded attempt.
    Legacy dispatches without a stored fingerprint still recognize newly added
    or updated work from their durable scope and completion timestamp.
    """

    current_fingerprint = wake_work_fingerprint(
        inbox_items=inbox_items,
        reviews=reviews,
    )
    if current_fingerprint is None:
        return False
    terminal_statuses = {
        TeamDispatchStatus.COMPLETED,
        TeamDispatchStatus.EXPIRED,
        TeamDispatchStatus.FAILED,
    }
    records = (state.latest_wake_work_records(agent_id) if hasattr(state, "latest_wake_work_records")
               else state.dispatches.values())
    terminal_dispatches = [
        dispatch
        for dispatch in records
        if dispatch.agent_id == agent_id
        and dispatch.cause_kind != "manager_fallback"
        and dispatch.status in terminal_statuses
        and dispatch.completed_at is not None
    ]
    if not terminal_dispatches:
        return True
    wake_attempts = [
        dispatch
        for dispatch in terminal_dispatches
        if (dispatch.leased_at is not None or dispatch.activation_completed_at is not None)
        and (
            dispatch.wake_work_record_fingerprints
            or dispatch.wake_work_fingerprint is not None
            or (
                dispatch.cause_kind not in {"blocker", "context_handoff"}
                and (dispatch.inbox_item_ids or dispatch.review_request_ids)
            )
        )
    ]
    if not wake_attempts:
        return True
    previous = max(
        wake_attempts,
        key=lambda dispatch: (
            _ensure_utc(dispatch.leased_at or dispatch.activation_completed_at),
            dispatch.id,
        ),
    )
    if previous.wake_work_record_fingerprints:
        current_records = wake_work_record_fingerprints(
            inbox_items=inbox_items,
            reviews=reviews,
        )
        if set(current_records) - set(previous.wake_work_record_fingerprints):
            return True
        return any(
            previous.wake_work_record_fingerprints.get(record_id) != fingerprint
            for record_id, fingerprint in current_records.items()
        )
    if previous.wake_work_fingerprint == current_fingerprint:
        return False

    current_inbox_ids = {item.id for item in inbox_items}
    current_review_ids = {review.id for review in reviews}
    if current_inbox_ids - set(previous.inbox_item_ids):
        return True
    if current_review_ids - set(previous.review_request_ids):
        return True
    completed_at = _ensure_utc(previous.completed_at)
    if any(_ensure_utc(item.updated_at) > completed_at for item in inbox_items):
        return True
    return any(
        review.created_at is not None and _ensure_utc(review.created_at) > completed_at
        for review in reviews
    )


def _runtime_blockers_due_for_agent(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> list[TeamBlocker]:
    rows: list[TeamBlocker] = []
    for blocker in state.blockers.values():
        if blocker.state != TeamBlockerState.OPEN:
            continue
        assigned = (
            blocker.route == TeamBlockerRoute.DELEGATED and blocker.delegate_agent_id == agent_id
        ) or (
            blocker.route in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
            and blocker.owner_agent_id == agent_id
        )
        if not assigned:
            continue
        has_pending_inbox = any(
            item.recipient_agent_id == agent_id
            and item.state == TeamInboxItemState.PENDING
            and item.payload.get("blocker_id") == blocker.id
            for item in state.inbox.values()
        )
        follow_up_due = blocker.follow_up_at is None or _ensure_utc(blocker.follow_up_at) <= now
        if has_pending_inbox or follow_up_due:
            rows.append(blocker)
    return rows


def _active_tasks_for_runtime_agent(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    return [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id and task.state not in terminal
    ]


def _runtime_task_can_wake_owner_for_work(state: TeamState, task: AgentTask) -> bool:
    return task.state not in {
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_INPUT,
    } and not _runtime_task_waits_on_active_deliverable_review(state, task)


def _runtime_task_waits_on_active_deliverable_review(
    state: TeamState,
    task: AgentTask,
) -> bool:
    if task.state != TeamTaskState.WAITING_FOR_REVIEW:
        return False
    review_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
        TeamRecordState.ACCEPTED,
    }
    return any(
        (deliverable := state.deliverables.get(deliverable_id)) is not None
        and deliverable.superseded_by_deliverable_id is None
        and deliverable.state in review_states
        for deliverable_id in task.deliverable_ids
    )


def _latest_undelivered_action_feedback_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[AgentActionRecord]:
    records = [record for record in state.action_records.values() if record.agent_id == agent_id]
    if not records:
        return []
    latest_record = max(records, key=lambda record: (record.created_at, record.id))
    if latest_record.success:
        return []
    if latest_record.dismissed_at is not None:
        return []
    if latest_record.feedback_delivered_at is not None:
        return []
    return [
        record
        for record in records
        if record.dispatch_id == latest_record.dispatch_id
        and not record.success
        and record.dismissed_at is None
    ]


def _runtime_low_priority_batch_due(runtime: AgentRuntime) -> bool:
    if runtime.low_priority_batch_started_at is None:
        return False
    due_after_cycles = (
        runtime.low_priority_batch_due_after_cycles
        if runtime.low_priority_batch_due_after_cycles is not None
        else _DEFAULT_LOW_PRIORITY_WAKE_AFTER_CYCLES
    )
    return runtime.low_priority_batch_cycle_count >= due_after_cycles


def _runtime_inbox_item_can_wake(item: AgentInboxItem) -> bool:
    return item.response_requested or item.priority != TeamInboxPriority.LOW
