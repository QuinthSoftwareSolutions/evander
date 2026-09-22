"""Agent activity summary projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from evander.models import utc_now

from .model_common import (
    _ACTIVITY_SUMMARY_HEADLINE_MAX,
    _ACTIVITY_SUMMARY_LINE_MAX,
    _ACTIVITY_SUMMARY_REF_MAX,
    AgentStatus,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamTaskState,
)
from .model_inbox_memory import (
    AgentInboxItem,
)
from .model_organization import (
    AgentStandDown,
    PersistentAgent,
)
from .model_state import (
    TeamState,
)
from .model_tasks import (
    AgentTask,
)
from .runtime_projections import (
    runtime_operations_payload,
)


def agent_activity_summaries_payload(
    state: TeamState,
    *,
    runtime_operations: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    agent_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    generated_at = (now or utc_now()).isoformat()
    if runtime_operations is None:
        runtime_operations = runtime_operations_payload(state, now=now, agent_ids=agent_ids)
    runtime_agent_rows = _activity_rows_by_agent(runtime_operations.get("agents"))
    session_health_rows = _activity_rows_by_agent(runtime_operations.get("session_health"))
    attention_rows = _activity_attention_rows_by_agent(runtime_operations.get("attention"))
    included_agent_ids = (
        sorted(agent_id for agent_id in agent_ids if agent_id in state.agents)
        if agent_ids is not None
        else sorted(state.agents)
    )
    return {
        agent_id: _agent_activity_summary_payload(
            state,
            agent_id=agent_id,
            generated_at=generated_at,
            runtime_row=runtime_agent_rows.get(agent_id),
            session_health_row=session_health_rows.get(agent_id),
            attention_rows=attention_rows.get(agent_id, []),
        )
        for agent_id in included_agent_ids
    }


def _agent_activity_summary_payload(
    state: TeamState,
    *,
    agent_id: str,
    generated_at: str,
    runtime_row: Mapping[str, Any] | None,
    session_health_row: Mapping[str, Any] | None,
    attention_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    agent = state.agents[agent_id]
    runtime = state.agent_runtimes.get(agent_id)
    open_tasks = _activity_open_tasks(state, agent_id)
    inbox_items = _activity_pending_inbox_items(state, agent_id)
    current_task = _activity_current_task(state, open_tasks)
    blocked_task = next((task for task in open_tasks if _activity_task_blocked(state, task)), None)
    waiting_task = next(
        (
            task
            for task in open_tasks
            if not _activity_task_blocked(state, task) and _activity_task_waiting(state, task)
        ),
        None,
    )

    session_issue = _activity_session_issue(agent, runtime_row, session_health_row)
    if session_issue is not None:
        return _activity_summary(
            headline=session_issue,
            working_on="Unavailable",
            waiting_for=session_issue,
            tone="offline",
            refs=[_activity_ref("session", agent.session_id)],
            generated_at=generated_at,
        )

    stand_down = _activity_active_stand_down_for_agent(state, agent_id)
    if stand_down is not None:
        reason = _activity_text(stand_down.reason)
        headline = f"Stood down{f': {reason}' if reason else ''}"
        return _activity_summary(
            headline=headline,
            working_on="Stood down",
            waiting_for=reason or "Stand-down active",
            tone="attention",
            refs=[_activity_ref("stand_down", stand_down.id)],
            generated_at=generated_at,
        )

    if agent.status != AgentStatus.ACTIVE:
        status = _activity_humanize_token(agent.status.value)
        return _activity_summary(
            headline=f"{status} agent",
            working_on=status,
            waiting_for="Clear",
            tone="offline" if agent.status == AgentStatus.ARCHIVED else "idle",
            refs=[_activity_ref("agent", agent.id)],
            generated_at=generated_at,
        )

    active_dispatch_id = _activity_first_text(
        runtime_row.get("active_dispatch_id") if runtime_row is not None else None,
        runtime.active_dispatch_id if runtime is not None else None,
    )
    lease_active = bool(runtime_row.get("lease_active")) if runtime_row is not None else False
    if active_dispatch_id or lease_active:
        if current_task is not None:
            refs = [_activity_ref("task", current_task.id)]
            if active_dispatch_id:
                refs.append(_activity_ref("dispatch", active_dispatch_id))
            return _activity_summary(
                headline=f"Working on {_activity_task_label(current_task)}",
                working_on=_activity_task_label(current_task),
                waiting_for=_activity_waiting_for_task(state, current_task)
                if _activity_task_waiting_or_blocked(state, current_task)
                else "Clear",
                tone="working",
                refs=refs,
                generated_at=generated_at,
            )
        if inbox_items:
            item = inbox_items[0]
            refs = [_activity_ref("inbox", item.id)]
            if active_dispatch_id:
                refs.append(_activity_ref("dispatch", active_dispatch_id))
            return _activity_summary(
                headline=f"Handling {_activity_inbox_label(item)}",
                working_on=_activity_inbox_label(item),
                waiting_for="Clear",
                tone="working",
                refs=refs,
                generated_at=generated_at,
            )
        return _activity_summary(
            headline="Running team dispatch",
            working_on="Team dispatch",
            waiting_for="Clear",
            tone="working",
            refs=[_activity_ref("dispatch", active_dispatch_id)] if active_dispatch_id else [],
            generated_at=generated_at,
        )

    if blocked_task is not None:
        return _activity_summary(
            headline=f"Blocked on {_activity_task_label(blocked_task)}",
            working_on=_activity_task_label(blocked_task),
            waiting_for=_activity_waiting_for_task(state, blocked_task),
            tone="blocked",
            refs=_activity_task_refs(state, blocked_task),
            generated_at=generated_at,
        )

    if waiting_task is not None:
        return _activity_summary(
            headline=f"Waiting on {_activity_task_label(waiting_task)}",
            working_on=_activity_task_label(waiting_task),
            waiting_for=_activity_waiting_for_task(state, waiting_task),
            tone="waiting",
            refs=_activity_task_refs(state, waiting_task),
            generated_at=generated_at,
        )

    actionable_inbox = [item for item in inbox_items if item.priority != TeamInboxPriority.LOW]
    if actionable_inbox:
        item = actionable_inbox[0]
        return _activity_summary(
            headline=f"Handling {_activity_inbox_label(item)}",
            working_on=_activity_inbox_label(item),
            waiting_for="Response requested" if item.response_requested else "Clear",
            tone="attention",
            refs=[_activity_ref("inbox", item.id)],
            generated_at=generated_at,
        )

    if current_task is not None:
        return _activity_summary(
            headline=f"Working on {_activity_task_label(current_task)}",
            working_on=_activity_task_label(current_task),
            waiting_for="Clear",
            tone="working",
            refs=[_activity_ref("task", current_task.id)],
            generated_at=generated_at,
        )

    if inbox_items:
        item = inbox_items[0]
        return _activity_summary(
            headline=f"Has low-priority {_activity_inbox_label(item)}",
            working_on=_activity_inbox_label(item),
            waiting_for="Low priority",
            tone="idle",
            refs=[_activity_ref("inbox", item.id)],
            generated_at=generated_at,
        )

    if attention_rows:
        row = attention_rows[0]
        title = _activity_first_text(
            row.get("title"), row.get("detail"), row.get("kind"), "runtime attention"
        )
        kind = _activity_text(row.get("kind"))
        return _activity_summary(
            headline=f"Needs attention: {title}",
            working_on=title,
            waiting_for=_activity_text(row.get("detail")) or "Runtime attention",
            tone="blocked" if "blocked" in kind else "attention",
            refs=_activity_row_refs(row),
            generated_at=generated_at,
        )

    return _activity_summary(
        headline="Idle, no open work",
        working_on="No open work",
        waiting_for="Clear",
        tone="idle",
        refs=[_activity_ref("agent", agent.id)],
        generated_at=generated_at,
    )


def _activity_summary(
    *,
    headline: str,
    working_on: str,
    waiting_for: str,
    tone: str,
    refs: Sequence[Mapping[str, str | None]] | None,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "headline": _activity_clamp(headline, _ACTIVITY_SUMMARY_HEADLINE_MAX),
        "working_on": _activity_clamp(working_on, _ACTIVITY_SUMMARY_LINE_MAX),
        "waiting_for": _activity_clamp(waiting_for, _ACTIVITY_SUMMARY_LINE_MAX),
        "tone": tone,
        "source": "derived",
        "reason_refs": _activity_refs(refs or []),
        "generated_at": generated_at,
    }


def _activity_open_tasks(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    return [
        task
        for task in state.ordered_tasks_for_agent(agent_id)
        if task.state not in terminal_states
    ]


def _activity_current_task(state: TeamState, tasks: Sequence[AgentTask]) -> AgentTask | None:
    return next(
        (task for task in tasks if not _activity_task_waiting_or_blocked(state, task)), None
    ) or (tasks[0] if tasks else None)


def _activity_pending_inbox_items(state: TeamState, agent_id: str) -> list[AgentInboxItem]:
    priority_order = {
        TeamInboxPriority.HIGH: 0,
        TeamInboxPriority.NORMAL: 1,
        TeamInboxPriority.LOW: 2,
    }
    return sorted(
        [
            item
            for item in state.inbox.values()
            if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
        ],
        key=lambda item: (priority_order.get(item.priority, 9), item.created_at, item.id),
    )


def _activity_task_waiting_or_blocked(state: TeamState, task: AgentTask) -> bool:
    return _activity_task_blocked(state, task) or _activity_task_waiting(state, task)


def _activity_task_blocked(state: TeamState, task: AgentTask) -> bool:
    return task.state == TeamTaskState.BLOCKED or state.dependency_status(task.id) == "blocked"


def _activity_task_waiting(state: TeamState, task: AgentTask) -> bool:
    return (
        task.state
        in {
            TeamTaskState.WAITING_FOR_INPUT,
            TeamTaskState.WAITING_FOR_REVIEW,
        }
        or state.dependency_status(task.id) == "waiting"
        or bool(state.blocked_by_task_ids(task.id))
    )


def _activity_waiting_for_task(state: TeamState, task: AgentTask) -> str:
    blocked_by = state.blocked_by_task_ids(task.id)
    dependency_status = state.dependency_status(task.id)
    if task.state == TeamTaskState.WAITING_FOR_INPUT:
        return "Input"
    if task.state == TeamTaskState.WAITING_FOR_REVIEW:
        return "Review"
    if blocked_by:
        return f"Task {blocked_by[0]}"
    if dependency_status == "blocked":
        return "Blocked dependencies"
    if dependency_status == "waiting":
        return "Dependencies"
    if task.state == TeamTaskState.BLOCKED:
        return "Blocked task state"
    return "Clear"


def _activity_task_refs(state: TeamState, task: AgentTask) -> list[dict[str, str]]:
    refs = [_activity_ref("task", task.id)]
    refs.extend(_activity_ref("task", task_id) for task_id in state.blocked_by_task_ids(task.id))
    return _activity_refs(refs)


def _activity_task_label(task: AgentTask) -> str:
    return _activity_text(task.title) or task.id


def _activity_inbox_label(item: AgentInboxItem) -> str:
    item_type = _activity_humanize_token(item.item_type.value).lower()
    detail = _activity_inbox_detail(item)
    return f"{item_type}: {detail}" if detail else item_type


def _activity_inbox_detail(item: AgentInboxItem) -> str:
    for key in ("title", "question", "message", "summary", "description"):
        value = _activity_text(item.payload.get(key))
        if value:
            return value
    return item.task_id or item.id


def _activity_session_issue(
    agent: PersistentAgent,
    runtime_row: Mapping[str, Any] | None,
    session_health_row: Mapping[str, Any] | None,
) -> str | None:
    rows = [row for row in (runtime_row, session_health_row) if row is not None]
    if any(bool(row.get("session_is_expired") or row.get("is_expired")) for row in rows):
        return "Session expired"
    if any(
        _activity_text(row.get("status") or row.get("session_status")) == "expired" for row in rows
    ):
        return "Session expired"
    if any(row.get("session_present") is False for row in rows):
        return f"Session {agent.session_id} not found"
    return None


def _activity_active_stand_down_for_agent(state: TeamState, agent_id: str) -> AgentStandDown | None:
    candidates = [
        stand_down
        for stand_down in state.stand_downs.values()
        if stand_down.active
        and (
            stand_down.agent_id == agent_id
            or (
                stand_down.scope == "subtree"
                and _activity_agent_reports_under(state, agent_id, stand_down.agent_id)
            )
        )
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda stand_down: stand_down.set_at)[-1]


def _activity_agent_reports_under(state: TeamState, agent_id: str, ancestor_agent_id: str) -> bool:
    seen: set[str] = set()
    current = state.agents.get(agent_id)
    while current is not None and current.reports_to_agent_id:
        parent_id = current.reports_to_agent_id
        if parent_id == ancestor_agent_id:
            return True
        if parent_id in seen:
            return False
        seen.add(parent_id)
        current = state.agents.get(parent_id)
    return False


def _activity_rows_by_agent(rows: object) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return result
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        agent_id = _activity_first_text(row.get("agent_id"), row.get("id"))
        if agent_id:
            result[agent_id] = row
    return result


def _activity_attention_rows_by_agent(rows: object) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return result
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        agent_id = _activity_text(row.get("agent_id"))
        if agent_id:
            result.setdefault(agent_id, []).append(row)
    return result


def _activity_row_refs(row: Mapping[str, Any]) -> list[dict[str, str]]:
    refs = []
    for ref_type, key in (
        ("task", "task_id"),
        ("deliverable", "deliverable_id"),
        ("inbox", "inbox_item_id"),
        ("action", "action_record_id"),
        ("assignment", "assignment_id"),
        ("promotion", "promotion_id"),
        ("release", "release_package_id"),
        ("dispatch", "dispatch_id"),
        ("session", "session_id"),
    ):
        refs.append(_activity_ref(ref_type, row.get(key)))
    return _activity_refs(refs)


def _activity_ref(ref_type: str, ref_id: object) -> dict[str, str]:
    return {"type": ref_type, "id": _activity_text(ref_id)}


def _activity_refs(refs: Sequence[Mapping[str, str | None]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        ref_type = _activity_text(ref.get("type"))
        ref_id = _activity_text(ref.get("id"))
        if not ref_type or not ref_id:
            continue
        key = (ref_type, ref_id)
        if key in seen:
            continue
        seen.add(key)
        result.append({"type": ref_type, "id": ref_id})
        if len(result) >= _ACTIVITY_SUMMARY_REF_MAX:
            break
    return result


def _activity_first_text(*values: object) -> str:
    for value in values:
        text = _activity_text(value)
        if text:
            return text
    return ""


def _activity_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _activity_clamp(value: object, max_length: int) -> str:
    text = _activity_text(value)
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3].rstrip()}..."


def _activity_humanize_token(value: object) -> str:
    return _activity_text(value).replace("_", " ").title()
