from __future__ import annotations

from collections.abc import Mapping
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
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamDispatch,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    agent_lead_project_ids,
    wake_work_changed_since_latest_attempt,
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
from .dispatch_scope import (
    actionable_dispatch_reviews,
    inbox_references_review_ids,
    mcp_capability_blocked_review_ids,
)
from .preparation_failures import inbox_has_current_preparation_blocker
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


def _dispatch_case_payloads(
    state: TeamState,
    *,
    agent: PersistentAgent,
    dispatch: TeamDispatch,
) -> dict[str, Any]:
    """Partition active cases into dispatch detail and a bounded-prompt index source.

    The dispatch is the authority for full case detail. A broader case index is
    useful only for management and Chat wakes where an operator can refer to a
    case by name without first supplying a durable record id.
    """

    task_ids = set(dispatch.task_ids)
    related_case_ids: set[str] = set()

    def _add_deliverable(deliverable_id: object) -> None:
        if not isinstance(deliverable_id, str):
            return
        deliverable = state.deliverables.get(deliverable_id.strip())
        if deliverable is not None:
            task_ids.add(deliverable.task_id)

    def _add_review(review_id: object) -> None:
        if not isinstance(review_id, str):
            return
        review = state.review_requests.get(review_id.strip())
        if review is not None:
            if review.deliverable_id is not None:
                _add_deliverable(review.deliverable_id)
            elif review.release_package_id is not None:
                _add_release(review.release_package_id)

    def _add_release(release_id: object) -> None:
        if not isinstance(release_id, str):
            return
        release = state.release_packages.get(release_id.strip())
        if release is None:
            return
        for deliverable_id in release.deliverable_ids:
            _add_deliverable(deliverable_id)
        if release.review_correction_task_id is not None:
            task_ids.add(release.review_correction_task_id)

    def _add_blocker(blocker: TeamBlocker) -> None:
        if blocker.task_id is not None:
            task_ids.add(blocker.task_id)
        task_ids.update(blocker.affected_task_ids)
        if blocker.source_kind == TeamBlockerSourceKind.TASK:
            task_ids.add(blocker.source_id)
        elif blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
            _add_deliverable(blocker.source_id)
        elif blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
            _add_review(blocker.source_id)
        elif blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE:
            _add_release(blocker.source_id)
        elif blocker.source_kind == TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST:
            request = state.human_review_requests.get(blocker.source_id)
            if request is not None:
                _add_release(request.release_package_id)

    for blocker_id in dispatch.blocker_ids:
        blocker = state.blockers.get(blocker_id)
        if blocker is not None:
            _add_blocker(blocker)
    for review_id in dispatch.review_request_ids:
        _add_review(review_id)
    for deliverable_id in dispatch.initial_release_decision_deliverable_ids:
        _add_deliverable(deliverable_id)
    for authority in dispatch.review_routing_authorities:
        task_ids.add(authority.task_id)
        _add_deliverable(authority.deliverable_id)

    for inbox_id in dispatch.inbox_item_ids:
        item = state.inbox.get(inbox_id)
        if item is None:
            continue
        if item.task_id is not None:
            task_ids.add(item.task_id)
        payload = item.payload if isinstance(item.payload, Mapping) else {}
        case_id = payload.get("case_id")
        if isinstance(case_id, str) and case_id.strip():
            related_case_ids.add(case_id.strip())
        task_id = payload.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            task_ids.add(task_id.strip())
        _add_deliverable(payload.get("deliverable_id"))
        _add_review(payload.get("review_request_id"))
        blocker_id = payload.get("blocker_id")
        if isinstance(blocker_id, str):
            blocker = state.blockers.get(blocker_id.strip())
            if blocker is not None:
                _add_blocker(blocker)
        _add_release(payload.get("release_package_id"))

    for task_id in tuple(task_ids):
        task = state.tasks.get(task_id)
        if task is not None and task.case_id is not None:
            related_case_ids.add(task.case_id)
    related_case_ids.update(
        case.id for case in state.cases.values() if set(case.task_ids).intersection(task_ids)
    )

    active_cases = [
        case
        for case in state.cases.values()
        if case.team_id == agent.team_id
        and case.status.value in ACTIVE_TEAM_CASE_STATUS_VALUES
    ]
    active_cases.sort(
        key=lambda item: (_priority_sort_value(item.priority), item.updated_at, item.id)
    )
    linked_cases = [case for case in active_cases if case.id in related_case_ids]

    team = state.teams.get(agent.team_id) if agent.team_id is not None else None
    is_team_manager = team is not None and team.manager_agent_id == agent.id
    lead_project_ids = set(agent_lead_project_ids(state, agent.id))
    active_owned_task_ids = {task.id for task in _active_tasks_for_agent(state, agent.id)}
    index_project_ids = set(dispatch.project_ids) if dispatch.cause_kind == "team_chat" else set()
    unrelated_visible_cases = [
        case
        for case in active_cases
        if case.id not in related_case_ids
        and (
            is_team_manager
            or case.project_id in lead_project_ids
            or case.project_id in index_project_ids
            or case.owner_agent_id == agent.id
            or bool(set(case.task_ids).intersection(active_owned_task_ids))
        )
    ]
    include_case_index = bool(
        is_team_manager or lead_project_ids or dispatch.cause_kind == "team_chat"
    )
    index_cases = unrelated_visible_cases if include_case_index else []
    return {
        "cases": [case.model_dump(mode="json") for case in linked_cases],
        "case_index": [
            {
                "id": case.id,
                "title": case.title,
                "status": case.status.value,
                "priority": case.priority.value,
                "project_id": case.project_id,
            }
            for case in index_cases
        ],
        "unrelated_case_count": len(unrelated_visible_cases),
    }


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
    return (
        task.state
        not in {
            TeamTaskState.BLOCKED,
            TeamTaskState.WAITING_FOR_INPUT,
        }
        and not _task_waits_on_active_deliverable_review(state, task)
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
        if item.recipient_agent_id == agent_id
        and item.state == TeamInboxItemState.PENDING
        and not inbox_has_current_preparation_blocker(state, item=item)
    ]
    items.sort(key=lambda item: (item.created_at, item.id))
    return items


def _ordinary_wake_work_for_agent(
    state: TeamState,
    agent_id: str,
) -> tuple[list[AgentInboxItem], list[AgentInboxItem], list[ReviewRequest]]:
    """Return the inbox and review work an ordinary lease can actually offer."""

    pending_inbox = _pending_inbox_for_agent(state, agent_id)
    pending_reviews = actionable_dispatch_reviews(_pending_reviews_for_agent(state, agent_id))
    blocked_review_ids = mcp_capability_blocked_review_ids(state, agent_id=agent_id)
    if blocked_review_ids:
        pending_inbox = [
            item
            for item in pending_inbox
            if not inbox_references_review_ids(item, blocked_review_ids)
        ]
        pending_reviews = [
            review for review in pending_reviews if review.id not in blocked_review_ids
        ]
    return pending_inbox, _wakeable_inbox_items(pending_inbox), pending_reviews


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
    # Inbox events stay policy-neutral because lease_due_agents owns the operator's
    # configured no-progress limit. Keep the existing task-ready event bound here.
    if reason == "task_ready":
        runtime = state.agent_runtimes.get(agent_id, AgentRuntime(agent_id=agent_id))
        _pending_inbox, wake_inbox, pending_reviews = _ordinary_wake_work_for_agent(
            state,
            agent_id,
        )
        wake_work_changed = wake_work_changed_since_latest_attempt(
            state,
            agent_id=agent_id,
            inbox_items=wake_inbox,
            reviews=pending_reviews,
        )
        if (
            max_no_progress_count >= 0
            and runtime.no_progress_count >= max_no_progress_count
            and not wake_work_changed
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
    _pending_inbox, _wake_inbox, pending_reviews = _ordinary_wake_work_for_agent(
        state,
        agent.id,
    )
    if pending_reviews:
        return "review", DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
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


def _dependency_status_snapshot(state: TeamState) -> dict[str, str]:
    """Capture active dependency-gated tasks before a command transition."""

    terminal = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    return {
        task.id: state.dependency_status(task.id)
        for task in state.tasks.values()
        if task.dependency_task_ids and task.state not in terminal
    }


def _append_newly_ready_task_owner_wake_requested_events(
    state: TeamState,
    *,
    dependency_statuses_before: Mapping[str, str],
    result: dict[str, Any],
    now: datetime,
    max_no_progress_count: int = DEFAULT_MAX_NO_PROGRESS_COUNT,
) -> None:
    """Wake only owners whose existing dependency-gated task just became ready."""

    from .role_task_queue import append_role_queue_wake_events

    if any(
        previous != "satisfied"
        and task_id in state.tasks
        and state.tasks[task_id].assignment_mode == "role"
        and state.tasks[task_id].owner_agent_id is None
        and state.dependency_status(task_id) == "satisfied"
        for task_id, previous in dependency_statuses_before.items()
    ):
        append_role_queue_wake_events(state, result=result, now=now)
    newly_ready_owner_ids = {
        task.owner_agent_id
        for task_id, previous_status in dependency_statuses_before.items()
        if previous_status != "satisfied"
        and (task := state.tasks.get(task_id)) is not None
        and state.dependency_status(task.id) == "satisfied"
        and task.owner_agent_id is not None
        and _task_can_wake_owner_for_work(state, task)
    }
    for agent_id in sorted(newly_ready_owner_ids):
        wake = _agent_immediate_wake_request(state, agent_id=agent_id, now=now)
        if wake is None:
            continue
        reason, delay_seconds = wake
        runtime = state.agent_runtimes.get(agent_id, AgentRuntime(agent_id=agent_id))
        if (
            reason == "task_ready"
            and max_no_progress_count >= 0
            and runtime.no_progress_count >= max_no_progress_count
        ):
            continue
        _append_agent_wake_requested_event(
            state,
            agent_id=agent_id,
            result=result,
            reason=reason,
            now=now,
            delay_seconds=delay_seconds,
        )


def _append_agent_wake_requested_event(
    state: TeamState,
    *,
    agent_id: str,
    result: dict[str, Any],
    reason: str,
    now: datetime,
    delay_seconds: float | None = None,
    run_at: datetime | None = None,
    idempotency_suffix: str | None = None,
    payload_extra: Mapping[str, Any] | None = None,
) -> None:
    agent = state.agents.get(agent_id)
    if agent is None or agent.status != AgentStatus.ACTIVE or agent.team_id is None:
        return
    normalized_now = _ensure_utc(now)
    if (delay_seconds is None) == (run_at is None):
        raise ValueError("wake request must use exactly one of delay_seconds or run_at")
    normalized_run_at = _ensure_utc(run_at) if run_at is not None else None
    normalized_delay = max(0.0, float(delay_seconds or 0.0))
    bucket_seconds = max(1.0, DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS)
    scheduled_timestamp = (
        normalized_run_at.timestamp()
        if normalized_run_at is not None
        else normalized_now.timestamp() + normalized_delay
    )
    bucket = int(scheduled_timestamp // bucket_seconds)
    suffix = f":{idempotency_suffix}" if idempotency_suffix else ""
    event = {
        "event_type": AGENT_WAKE_REQUESTED_EVENT_TYPE,
        "correlation_key": f"{AGENT_WAKE_REQUESTED_EVENT_TYPE}:{agent.id}",
        "idempotency_key": (
            f"team-addon:{AGENT_WAKE_REQUESTED_EVENT_TYPE}:{agent.id}:{bucket}{suffix}"
        ),
        "payload": {
            "team_id": agent.team_id,
            "agent_id": agent.id,
            "session_id": agent.session_id,
            "reason": reason,
            **dict(payload_extra or {}),
        },
    }
    if normalized_run_at is not None:
        event["run_at"] = normalized_run_at.isoformat().replace("+00:00", "Z")
    else:
        event["delay_seconds"] = normalized_delay
    result.setdefault("events", []).append(event)


__all__ = [
    "AGENT_WAKE_REQUESTED_EVENT_TYPE",
    "_active_tasks_for_agent",
    "_case_payloads_for_agent",
    "_case_payloads_for_team",
    "_dispatch_case_payloads",
    "_release_package_payloads_for_agent",
    "_runtime_attention_payloads_for_agent",
    "_wakeable_inbox_items",
    "_inbox_item_can_wake",
    "_task_can_wake_owner_for_work",
    "_task_waits_on_active_deliverable_review",
    "_pending_reviews_for_agent",
    "_pending_ephemeral_acceptances_for_agent",
    "_pending_inbox_for_agent",
    "_ordinary_wake_work_for_agent",
    "_append_agent_wake_requested_event_for_inbox_item",
    "_append_follow_up_agent_wake_requested_event",
    "_agent_immediate_wake_request",
    "_agent_task_work_ready",
    "_dependency_status_snapshot",
    "_append_newly_ready_task_owner_wake_requested_events",
    "_append_agent_wake_requested_event",
]
