from __future__ import annotations

from datetime import (
    UTC,
    datetime,
)
from typing import Any
from ...models import (
    ACTIVE_TEAM_CASE_STATUS_VALUES,
    AgentInboxItem,
    AgentRuntime,
    AgentStatus,
    AgentTask,
    EphemeralAssignment,
    EphemeralAssignmentState,
    PersistentAgent,
    ReviewRequest,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    release_package_payload_with_derived,
    runtime_operations_payload,
)

from .action_feedback_payloads import _latest_action_feedback_for_agent
from .agent_access import _visible_project_ids_for_agent
from .chat import _clear_completed_chat_dispatch_lease
from .common import (
    DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    DEFAULT_MAX_NO_PROGRESS_COUNT,
    _ensure_utc,
    _priority_sort_value,
)
from .deliverables import _active_task_deliverables
from .stand_down import _active_stand_downs_for_agent


AGENT_WAKE_REQUESTED_EVENT_TYPE = "team.agent.wake_requested"


def _active_tasks_for_agent(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id and task.state not in terminal
    ]
    tasks.sort(key=lambda item: (item.priority, item.created_at, item.id))
    return tasks


def _case_payloads_for_agent(state: TeamState, agent_id: str) -> list[dict[str, Any]]:
    active_task_ids = {task.id for task in _active_tasks_for_agent(state, agent_id)}
    cases = [
        case
        for case in state.cases.values()
        if case.status.value in ACTIVE_TEAM_CASE_STATUS_VALUES
        and (
            case.owner_agent_id == agent_id
            or any(task_id in active_task_ids for task_id in case.task_ids)
        )
    ]
    cases.sort(key=lambda item: (_priority_sort_value(item.priority), item.updated_at, item.id))
    return [case.model_dump(mode="json") for case in cases]


def _case_payloads_for_team(state: TeamState, team_id: str) -> list[dict[str, Any]]:
    cases = [
        case
        for case in state.cases.values()
        if case.team_id == team_id and case.status.value in ACTIVE_TEAM_CASE_STATUS_VALUES
    ]
    cases.sort(key=lambda item: (_priority_sort_value(item.priority), item.updated_at, item.id))
    return [case.model_dump(mode="json") for case in cases]


def _release_package_payloads_for_agent(
    state: TeamState,
    agent: PersistentAgent,
) -> dict[str, dict[str, Any]]:
    visible_project_ids = _visible_project_ids_for_agent(state, agent)
    releases = [
        release
        for release in state.release_packages.values()
        if release.project_id in visible_project_ids
    ]
    releases.sort(key=lambda release: (release.created_at, release.id))
    return {
        release.id: release_package_payload_with_derived(state, release) for release in releases
    }


def _runtime_attention_payloads_for_agent(
    state: TeamState,
    agent: PersistentAgent,
) -> list[dict[str, Any]]:
    visible_project_ids = _visible_project_ids_for_agent(state, agent)
    visible_release_ids = {
        release.id
        for release in state.release_packages.values()
        if release.project_id in visible_project_ids
    }
    runtime_attention = runtime_operations_payload(state).get("attention")
    if not isinstance(runtime_attention, list):
        return []
    rows = [
        row
        for row in runtime_attention
        if isinstance(row, dict)
        and (
            row.get("agent_id") == agent.id
            or row.get("project_id") in visible_project_ids
            or row.get("release_package_id") in visible_release_ids
        )
    ]
    return rows


def _wakeable_inbox_items(items: list[AgentInboxItem]) -> list[AgentInboxItem]:
    return [item for item in items if _inbox_item_can_wake(item)]


def _inbox_item_can_wake(item: AgentInboxItem) -> bool:
    return item.response_requested or item.priority != TeamInboxPriority.LOW


def _task_can_wake_owner_for_work(state: TeamState, task: AgentTask) -> bool:
    return task.state != TeamTaskState.BLOCKED and not _task_waits_on_active_deliverable_review(
        state, task
    )


def _task_waits_on_active_deliverable_review(state: TeamState, task: AgentTask) -> bool:
    if task.state != TeamTaskState.WAITING_FOR_REVIEW:
        return False
    review_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
        TeamRecordState.ACCEPTED,
    }
    return any(
        deliverable.state in review_states for deliverable in _active_task_deliverables(state, task)
    )


def _pending_reviews_for_agent(state: TeamState, agent_id: str) -> list[ReviewRequest]:
    pending_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
    reviews = [
        review
        for review in state.review_requests.values()
        if review.reviewer_agent_id == agent_id
        and review.state in pending_states
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
    ]
    reviews.sort(key=lambda item: (item.created_at or datetime.min.replace(tzinfo=UTC), item.id))
    return reviews


def _pending_ephemeral_acceptances_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[EphemeralAssignment]:
    assignments = [
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.parent_agent_id == agent_id
        and assignment.state == EphemeralAssignmentState.COMPLETED
    ]
    assignments.sort(key=lambda item: (item.updated_at, item.id))
    return assignments


def _pending_inbox_for_agent(state: TeamState, agent_id: str) -> list[AgentInboxItem]:
    items = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
    ]
    items.sort(key=lambda item: (item.created_at, item.id))
    return items


def _append_agent_wake_requested_event_for_inbox_item(
    state: TeamState,
    *,
    item: AgentInboxItem,
    result: dict[str, Any],
    now: datetime,
) -> None:
    if not _inbox_item_can_wake(item) or item.priority == TeamInboxPriority.LOW:
        return
    _append_agent_wake_requested_event(
        state,
        agent_id=item.recipient_agent_id,
        result=result,
        reason="inbox",
        now=now,
        delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    )


def _append_follow_up_agent_wake_requested_event(
    state: TeamState,
    *,
    agent_id: str,
    result: dict[str, Any],
    now: datetime,
    completed_dispatch_id: str,
    progress_made: bool,
    max_no_progress_count: int = DEFAULT_MAX_NO_PROGRESS_COUNT,
) -> None:
    wake = _agent_immediate_wake_request(state, agent_id=agent_id, now=now)
    if wake is None:
        return
    reason, delay_seconds = wake
    if not progress_made and reason != "inbox":
        return
    if (
        reason == "task_ready"
        and max_no_progress_count >= 0
        and state.agent_runtimes.get(agent_id, AgentRuntime(agent_id=agent_id)).no_progress_count
        >= max_no_progress_count
    ):
        return
    _append_agent_wake_requested_event(
        state,
        agent_id=agent_id,
        result=result,
        reason=reason,
        now=now,
        delay_seconds=delay_seconds,
        idempotency_suffix=(
            f"after-dispatch:{completed_dispatch_id}" if delay_seconds <= 0 else None
        ),
    )


def _agent_immediate_wake_request(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> tuple[str, float] | None:
    agent = state.agents.get(agent_id)
    if agent is None or agent.status != AgentStatus.ACTIVE or agent.team_id is None:
        return None
    if _active_stand_downs_for_agent(state, agent.id):
        return None
    runtime = state.agent_runtimes.get(agent.id)
    if runtime is None:
        runtime = AgentRuntime(agent_id=agent.id)
    _clear_completed_chat_dispatch_lease(state, runtime, now=now)
    if runtime.wake_leased_until is not None and _ensure_utc(
        runtime.wake_leased_until
    ) > _ensure_utc(now):
        return None

    pending_inbox = _pending_inbox_for_agent(state, agent.id)
    wake_inbox = _wakeable_inbox_items(pending_inbox)
    immediate_inbox = [item for item in wake_inbox if item.priority != TeamInboxPriority.LOW]
    if immediate_inbox:
        oldest = min(_ensure_utc(item.created_at) for item in immediate_inbox)
        age_seconds = max(0.0, (_ensure_utc(now) - oldest).total_seconds())
        return (
            "inbox",
            max(0.0, DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS - age_seconds),
        )
    if runtime.context_rotation_pending:
        return "context_rotation", DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    if _latest_action_feedback_for_agent(state, agent.id):
        return "action_feedback", DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    if _agent_task_work_ready(state, agent.id):
        return "task_ready", DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    return None


def _agent_task_work_ready(state: TeamState, agent_id: str) -> bool:
    wakeable_tasks = [
        task
        for task in _active_tasks_for_agent(state, agent_id)
        if _task_can_wake_owner_for_work(state, task)
    ]
    return any(state.dependency_status(task.id) == "satisfied" for task in wakeable_tasks) or any(
        state.subtask_counts(task.id)["open"] > 0 for task in wakeable_tasks
    )


def _append_agent_wake_requested_event(
    state: TeamState,
    *,
    agent_id: str,
    result: dict[str, Any],
    reason: str,
    now: datetime,
    delay_seconds: float,
    idempotency_suffix: str | None = None,
) -> None:
    agent = state.agents.get(agent_id)
    if agent is None or agent.status != AgentStatus.ACTIVE or agent.team_id is None:
        return
    normalized_now = _ensure_utc(now)
    normalized_delay = max(0.0, float(delay_seconds))
    bucket_seconds = max(1.0, DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS)
    bucket = int((normalized_now.timestamp() + normalized_delay) // bucket_seconds)
    suffix = f":{idempotency_suffix}" if idempotency_suffix else ""
    result.setdefault("events", []).append(
        {
            "event_type": AGENT_WAKE_REQUESTED_EVENT_TYPE,
            "correlation_key": f"{AGENT_WAKE_REQUESTED_EVENT_TYPE}:{agent.id}",
            "idempotency_key": (
                f"team-addon:{AGENT_WAKE_REQUESTED_EVENT_TYPE}:{agent.id}:{bucket}{suffix}"
            ),
            "delay_seconds": normalized_delay,
            "payload": {
                "team_id": agent.team_id,
                "agent_id": agent.id,
                "session_id": agent.session_id,
                "reason": reason,
            },
        }
    )


__all__ = [
    "AGENT_WAKE_REQUESTED_EVENT_TYPE",
    "_active_tasks_for_agent",
    "_case_payloads_for_agent",
    "_case_payloads_for_team",
    "_release_package_payloads_for_agent",
    "_runtime_attention_payloads_for_agent",
    "_wakeable_inbox_items",
    "_inbox_item_can_wake",
    "_task_can_wake_owner_for_work",
    "_task_waits_on_active_deliverable_review",
    "_pending_reviews_for_agent",
    "_pending_ephemeral_acceptances_for_agent",
    "_pending_inbox_for_agent",
    "_append_agent_wake_requested_event_for_inbox_item",
    "_append_follow_up_agent_wake_requested_event",
    "_agent_immediate_wake_request",
    "_agent_task_work_ready",
    "_append_agent_wake_requested_event",
]
