from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from ...models import (
    AgentInboxItem,
    AgentTask,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamState,
)
from .task_assessment import TaskAssessmentService
from .work_due import _mark_agent_work_due


_PARENT_TRANSITION_SUMMARIES = {
    "blocker": "A constituent child reported a blocker and needs supervision.",
    "handoff": "A constituent child submitted a contribution for handoff.",
    "rejection": "A constituent child contribution was rejected for rework.",
    "resolution": "A constituent child reached a resolved disposition.",
    "integration_ready": "Every direct child is resolved; integration is ready.",
}
_AUTOMATIC_CANDIDATE_CORRECTION_ACTORS = {
    "system:release-review-correction",
    "system:release-verification-correction",
}


def parent_task_for_child(
    state: TeamState,
    child: AgentTask,
) -> AgentTask | None:
    if child.parent_task_id is None:
        return None
    return state.tasks.get(child.parent_task_id)


def task_acceptance_recipient_agent_id(
    state: TeamState,
    *,
    task: AgentTask,
    producer_agent_id: str,
) -> str | None:
    """Route a child contribution to its accountable parent when possible."""

    producer = state.agents.get(producer_agent_id)
    parent = parent_task_for_child(state, task)
    if parent is not None and parent.owner_agent_id != producer_agent_id:
        return parent.owner_agent_id
    return producer.reports_to_agent_id if producer is not None else None


def queue_parent_task_transition(
    state: TeamState,
    *,
    child_task_id: str,
    transition: str,
    sender_agent_id: str | None = None,
    result: dict[str, Any] | None = None,
    now: datetime,
    references: Mapping[str, object] | None = None,
) -> AgentInboxItem | None:
    """Queue one coalesced parent-owner notice for a meaningful child transition."""

    child = state.tasks.get(child_task_id)
    if child is None:
        return None
    parent = parent_task_for_child(state, child)
    if parent is None:
        return None
    summary = _PARENT_TRANSITION_SUMMARIES.get(transition)
    if summary is None:
        raise ValueError(f"unsupported parent task transition {transition}")
    recipient_agent_id = parent.owner_agent_id
    effective_sender_id = (
        sender_agent_id
        if sender_agent_id in state.agents
        else child.owner_agent_id
    )
    self_observed_resolution = (
        recipient_agent_id == effective_sender_id
        and transition == "resolution"
    )
    if (
        recipient_agent_id == effective_sender_id
        and transition in {"handoff", "rejection"}
    ):
        # An agent already knows about these child-state transitions caused by
        # its own command. Resolution is retained below as handled history;
        # integration-ready and blocker notices can make distinct parent-level
        # action newly available.
        return None
    existing = next(
        (
            item
            for item in state.inbox.values()
            if item.state == TeamInboxItemState.PENDING
            and item.recipient_agent_id == recipient_agent_id
            and item.payload.get("kind") == "parent_task_transition"
            and item.payload.get("parent_task_id") == parent.id
            and item.payload.get("transition") == transition
        ),
        None,
    )
    normalized_references = {
        str(key): value
        for key, value in (references or {}).items()
        if value is not None
    }
    if existing is not None:
        child_ids = {
            str(value)
            for value in existing.payload.get("child_task_ids") or []
            if str(value).strip()
        }
        child_ids.add(child.id)
        existing.payload["child_task_ids"] = sorted(child_ids)
        for key, value in normalized_references.items():
            plural_key = key if key.endswith("_ids") else f"{key}s"
            singular_key = key[:-1] if key.endswith("_ids") else key
            current_values: set[str] = set()
            for current_key in (singular_key, plural_key):
                current = existing.payload.get(current_key)
                if isinstance(current, list):
                    current_values.update(str(item) for item in current)
                elif current is not None:
                    current_values.add(str(current))
            if isinstance(value, list):
                current_values.update(str(item) for item in value)
            else:
                current_values.add(str(value))
            existing.payload[plural_key] = sorted(current_values)
        existing.updated_at = now
        if self_observed_resolution:
            existing.state = TeamInboxItemState.HANDLED
            existing.response_requested = False
        else:
            _mark_agent_work_due(state, agent_id=recipient_agent_id, now=now)
        if result is not None and not self_observed_resolution:
            _append_wake_event_for_notice(
                state,
                item=existing,
                result=result,
                now=now,
            )
        return existing

    payload: dict[str, object] = {
        "kind": "parent_task_transition",
        "transition": transition,
        "parent_task_id": parent.id,
        "child_task_ids": [child.id],
        "summary": summary,
        "next_action": _parent_transition_next_action(
            transition,
            parent_task_id=parent.id,
        ),
        **normalized_references,
    }
    item = AgentInboxItem(
        id=f"inbox-{uuid4().hex}",
        recipient_agent_id=recipient_agent_id,
        sender_agent_id=effective_sender_id,
        task_id=parent.id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=not self_observed_resolution,
        priority=TeamInboxPriority.NORMAL,
        payload=payload,
        state=(
            TeamInboxItemState.HANDLED
            if self_observed_resolution
            else TeamInboxItemState.PENDING
        ),
        created_at=now,
        updated_at=now,
    )
    state.inbox[item.id] = item
    if not self_observed_resolution:
        _mark_agent_work_due(state, agent_id=recipient_agent_id, now=now)
    if result is not None:
        result.setdefault("created_inbox_item_ids", []).append(item.id)
        if not self_observed_resolution:
            _append_wake_event_for_notice(
                state,
                item=item,
                result=result,
                now=now,
            )
    return item


def queue_parent_resolution_transitions(
    state: TeamState,
    *,
    child_task_id: str,
    sender_agent_id: str | None = None,
    result: dict[str, Any] | None = None,
    now: datetime,
    references: Mapping[str, object] | None = None,
) -> list[AgentInboxItem]:
    child = state.tasks.get(child_task_id)
    parent = parent_task_for_child(state, child) if child is not None else None
    if child is None or parent is None:
        return []
    notices: list[AgentInboxItem] = []
    resolution_notice = queue_parent_task_transition(
        state,
        child_task_id=child.id,
        transition="resolution",
        sender_agent_id=sender_agent_id,
        result=result,
        now=now,
        references=references,
    )
    if resolution_notice is not None:
        notices.append(resolution_notice)
    if TaskAssessmentService(state).assess(parent.id).integration_ready:
        ready_notice = queue_parent_task_transition(
            state,
            child_task_id=child.id,
            transition="integration_ready",
            sender_agent_id=sender_agent_id,
            result=result,
            now=now,
            references=references,
        )
        if ready_notice is not None:
            notices.append(ready_notice)
    return notices


def queue_root_task_resolution(
    state: TeamState,
    *,
    task_id: str,
    deliverable_id: str,
    sender_agent_id: str,
    result: dict[str, Any] | None,
    now: datetime,
) -> AgentInboxItem | None:
    """Return an accepted root boundary to its project lead for release work."""

    task = state.tasks.get(task_id)
    if task is None or task.parent_task_id is not None:
        return None
    project = state.projects.get(task.project_id)
    if project is None or project.lead_agent_id == sender_agent_id:
        return None
    automatic_candidate_correction = (
        task.created_by_actor in _AUTOMATIC_CANDIDATE_CORRECTION_ACTORS
    )
    notice_kind = (
        "candidate_correction_resolution"
        if automatic_candidate_correction
        else "root_task_resolution"
    )
    existing = next(
        (
            item
            for item in state.inbox.values()
            if item.recipient_agent_id == project.lead_agent_id
            and item.payload.get("kind") == notice_kind
            and item.payload.get("task_id") == task.id
            and item.payload.get("deliverable_id") == deliverable_id
        ),
        None,
    )
    if existing is not None:
        existing.updated_at = now
        if automatic_candidate_correction:
            existing.state = TeamInboxItemState.HANDLED
            existing.response_requested = False
            existing.priority = TeamInboxPriority.LOW
        else:
            _mark_agent_work_due(state, agent_id=project.lead_agent_id, now=now)
        if result is not None and not automatic_candidate_correction:
            _append_wake_event_for_notice(
                state,
                item=existing,
                result=result,
                now=now,
            )
        return existing
    release_ready = project.release_policy is not None
    if automatic_candidate_correction:
        item = AgentInboxItem(
            id=f"inbox-{uuid4().hex}",
            recipient_agent_id=project.lead_agent_id,
            sender_agent_id=sender_agent_id,
            task_id=task.id,
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=False,
            priority=TeamInboxPriority.LOW,
            payload={
                "kind": "candidate_correction_resolution",
                "project_id": project.id,
                "task_id": task.id,
                "deliverable_id": deliverable_id,
                "summary": (
                    "A reviewed rejected-gate correction reached its resolved "
                    "disposition."
                ),
                "next_action": (
                    "Team will automatically create the successor release; "
                    "no release request is required."
                ),
                "release_ready": release_ready,
                "response_required": False,
            },
            state=TeamInboxItemState.HANDLED,
            created_at=now,
            updated_at=now,
        )
        state.inbox[item.id] = item
        if result is not None:
            result.setdefault("created_inbox_item_ids", []).append(item.id)
        return item
    # Local import avoids the wake -> deliverables -> supervision import cycle.
    from .review_handoff import queue_actionable_inbox_item

    return queue_actionable_inbox_item(
        state,
        recipient_agent_id=project.lead_agent_id,
        sender_agent_id=sender_agent_id,
        task_id=task.id,
        priority=TeamInboxPriority.NORMAL,
        payload={
            "kind": "root_task_resolution",
            "project_id": project.id,
            "task_id": task.id,
            "deliverable_id": deliverable_id,
            "summary": "A reviewed root task reached its resolved disposition.",
            "next_action": (
                "Request a release package for the accepted root deliverable "
                "and its source lineage."
                if release_ready
                else "Review the root completion and choose the next project action."
            ),
            "release_ready": release_ready,
        },
        result=result,
        now=now,
    )


def _parent_transition_next_action(
    transition: str,
    *,
    parent_task_id: str,
) -> str:
    if transition == "blocker":
        return "Review the child blocker, resolve, delegate, or escalate it."
    if transition == "handoff":
        return "Track the child acceptance or review and prepare integration."
    if transition == "rejection":
        return "Supervise the child rework without treating it as resolved."
    if transition == "resolution":
        return "Review the resolved child contribution and remaining direct children."
    return (
        f"Integrate the accepted direct-child sources into parent task "
        f"{parent_task_id}."
    )


def _append_wake_event_for_notice(
    state: TeamState,
    *,
    item: AgentInboxItem,
    result: dict[str, Any],
    now: datetime,
) -> None:
    # Local import avoids the wake -> deliverables -> supervision import cycle.
    from .wake import _append_agent_wake_requested_event_for_inbox_item

    _append_agent_wake_requested_event_for_inbox_item(
        state,
        item=item,
        result=result,
        now=now,
    )


__all__ = [
    "parent_task_for_child",
    "queue_parent_resolution_transitions",
    "queue_parent_task_transition",
    "queue_root_task_resolution",
    "task_acceptance_recipient_agent_id",
]
