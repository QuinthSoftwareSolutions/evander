from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from ...models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentStatus,
    AgentTask,
    Deliverable,
    PersistentAgent,
    ReviewProfile,
    ReviewRequest,
    Team,
    TeamDispatchReviewRoutingAuthority,
    TeamInboxItemState,
    TeamState,
    agent_project_ids,
)

from .agent_access import (
    _is_active_deliverable_work,
    _task_has_active_deliverable_work,
)
from .common import (
    _MANAGER_FALLBACK_PENDING_REVIEW_STATES,
    _NON_TERMINAL_TASK_STATES,
    _ensure_utc,
    _optional_text,
    utc_now,
)
from .deliverables import _active_task_deliverables
from .initial_release import pending_initial_release_deliverables
from .stand_down import _active_stand_downs_for_agent
from .task_assessment import task_payload_with_assessment
from ..wake_prompt_context import MAX_MANAGER_FALLBACK_ROWS


_MANAGER_FALLBACK_OPEN_WORK_LIMIT = 20

_MANAGER_FALLBACK_PENDING_INBOX_LIMIT = 10

_MANAGER_FALLBACK_FAILED_ACTION_LIMIT = 3

_MANAGER_FALLBACK_ROUTING_GAP_LIMIT = MAX_MANAGER_FALLBACK_ROWS

_MANAGER_FALLBACK_TEXT_LIMIT = 240


def _manager_fallback_task_visible_for_work(
    state: TeamState,
    task: AgentTask,
    project_ids: set[str],
) -> bool:
    return task.project_id in project_ids and (
        task.state in _NON_TERMINAL_TASK_STATES or _task_has_active_deliverable_work(state, task.id)
    )


def _manager_fallback_summary_payload(
    state: TeamState,
    *,
    team: Team,
    manager: PersistentAgent,
    now: datetime,
    routing_gap_limit: int | None = _MANAGER_FALLBACK_ROUTING_GAP_LIMIT,
) -> dict[str, Any]:
    project_ids = {project.id for project in state.projects.values() if project.team_id == team.id}
    team_agents = sorted(
        (
            agent
            for agent in state.agents.values()
            if agent.team_id == team.id and agent.status != AgentStatus.ARCHIVED
        ),
        key=lambda item: item.id,
    )
    team_agent_ids = {agent.id for agent in team_agents}
    visible_tasks = sorted(
        (
            task
            for task in state.tasks.values()
            if _manager_fallback_task_visible_for_work(state, task, project_ids)
        ),
        key=lambda item: (item.priority, item.created_at, item.id),
    )
    visible_task_ids = {task.id for task in visible_tasks}
    visible_deliverables = sorted(
        (
            deliverable
            for deliverable in state.deliverables.values()
            if deliverable.task_id in visible_task_ids
            and deliverable.superseded_by_deliverable_id is None
        ),
        key=lambda item: (item.created_at, item.id),
    )
    visible_deliverable_ids = {deliverable.id for deliverable in visible_deliverables}
    visible_pending_inbox = sorted(
        (
            item
            for item in state.inbox.values()
            if item.recipient_agent_id in team_agent_ids
            and (item.sender_agent_id is None or item.sender_agent_id in team_agent_ids)
            and (item.task_id is None or item.task_id in visible_task_ids)
            and item.state == TeamInboxItemState.PENDING
        ),
        key=lambda item: (item.created_at, item.id),
    )
    visible_action_records = sorted(
        (
            record
            for record in state.action_records.values()
            if record.agent_id in team_agent_ids
            and not record.success
            and record.dismissed_at is None
        ),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    review_profiles = sorted(
        (
            profile
            for profile in state.review_profiles.values()
            if profile.team_id == team.id
            and not profile.archived
            and (profile.project_id is None or profile.project_id in project_ids)
        ),
        key=lambda item: item.id,
    )
    visible_reviews = sorted(
        (
            review
            for review in state.review_requests.values()
            if review.deliverable_id in visible_deliverable_ids
            and review.superseded_by_review_request_id is None
            and review.retired_at is None
        ),
        key=lambda item: (item.created_at or now, item.id),
    )
    visible_pending_reviews = [
        review
        for review in visible_reviews
        if review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
    ]
    pending_initial_release_rows = [
        _manager_fallback_initial_release_row(
            state,
            deliverable=deliverable,
        )
        for deliverable in pending_initial_release_deliverables(
            state,
            team_id=team.id,
        )
    ]
    reviewer_candidates = _manager_fallback_reviewer_candidates(
        state,
        team_agents=[
            agent for agent in team_agents if agent.id != manager.id
        ],
        project_ids=project_ids,
        review_profiles=review_profiles,
        now=now,
    )
    open_work_rows = [
        _manager_fallback_task_row(
            state,
            task=task,
            visible_reviews=visible_reviews,
        )
        for task in visible_tasks
    ]
    deliverable_rows = [
        _manager_fallback_deliverable_row(
            deliverable=deliverable,
            visible_reviews=visible_reviews,
        )
        for deliverable in visible_deliverables
    ]
    review_rows = [_manager_fallback_review_row(review) for review in visible_reviews]
    inbox_rows = [_manager_fallback_inbox_row(item) for item in visible_pending_inbox]
    failed_action_rows = [
        _manager_fallback_failed_action_row(record) for record in visible_action_records
    ]
    routing_gaps = _manager_fallback_routing_gaps(
        state,
        reviewer_candidates=reviewer_candidates,
        review_profiles=review_profiles,
        visible_tasks=visible_tasks,
        visible_deliverables=visible_deliverables,
        visible_reviews=visible_reviews,
        visible_pending_inbox=visible_pending_inbox,
    )
    limited_open_work, omitted_open_work = _manager_fallback_limited_rows(
        open_work_rows,
        _MANAGER_FALLBACK_OPEN_WORK_LIMIT,
    )
    limited_deliverables, omitted_deliverables = _manager_fallback_limited_rows(
        deliverable_rows,
        _MANAGER_FALLBACK_OPEN_WORK_LIMIT,
    )
    limited_reviews, omitted_reviews = _manager_fallback_limited_rows(
        review_rows,
        _MANAGER_FALLBACK_OPEN_WORK_LIMIT,
    )
    limited_inbox, omitted_inbox = _manager_fallback_limited_rows(
        inbox_rows,
        _MANAGER_FALLBACK_PENDING_INBOX_LIMIT,
    )
    limited_failed_actions, omitted_failed_actions = _manager_fallback_limited_rows(
        failed_action_rows,
        _MANAGER_FALLBACK_FAILED_ACTION_LIMIT,
    )
    (
        limited_initial_release_rows,
        omitted_initial_release_rows,
    ) = _manager_fallback_limited_rows(
        pending_initial_release_rows,
        MAX_MANAGER_FALLBACK_ROWS,
    )
    if routing_gap_limit is None:
        limited_routing_gaps = list(routing_gaps)
        omitted_routing_gaps = 0
    else:
        limited_routing_gaps, omitted_routing_gaps = (
            _manager_fallback_limited_rows(
                routing_gaps,
                max(0, routing_gap_limit),
            )
        )
    omitted_counts = _manager_fallback_omitted_counts(
        {
            "open_work": omitted_open_work,
            "deliverables": omitted_deliverables,
            "review_requests": omitted_reviews,
            "pending_inbox": omitted_inbox,
            "latest_failed_actions": omitted_failed_actions,
            "routing_gaps": omitted_routing_gaps,
            "pending_initial_release_decisions": omitted_initial_release_rows,
        }
    )
    return {
        "reason": "manager_fallback",
        "generated_at": _manager_fallback_time(now),
        "team": {
            "id": team.id,
            "name": team.name,
            "manager_agent_id": manager.id,
        },
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "lead_agent_id": project.lead_agent_id,
                "status": project.status.value,
            }
            for project in sorted(state.projects.values(), key=lambda item: item.id)
            if project.id in project_ids
        ],
        "summary_counts": {
            "active_agent_count": len(team_agents),
            "open_task_count": len(visible_tasks),
            "actionable_open_task_count": sum(
                task.state in _NON_TERMINAL_TASK_STATES for task in visible_tasks
            ),
            "active_deliverable_count": len(visible_deliverables),
            "review_request_count": len(visible_reviews),
            "pending_review_request_count": len(visible_pending_reviews),
            "pending_inbox_count": len(visible_pending_inbox),
            "failed_action_count": len(visible_action_records),
            "routing_gap_count": len(routing_gaps),
            "pending_initial_release_decision_count": len(
                pending_initial_release_rows
            ),
        },
        "agents": [
            _manager_fallback_agent_row(
                state,
                agent=agent,
                now=now,
                visible_tasks=visible_tasks,
                visible_pending_reviews=visible_pending_reviews,
                visible_pending_inbox=visible_pending_inbox,
                visible_failed_actions=visible_action_records,
            )
            for agent in team_agents
        ],
        "reviewer_candidate_agent_ids": [agent.id for agent in reviewer_candidates],
        "open_work": limited_open_work,
        "deliverables": limited_deliverables,
        "review_requests": limited_reviews,
        "pending_inbox": limited_inbox,
        "latest_failed_actions": limited_failed_actions,
        "routing_gaps": limited_routing_gaps,
        "pending_initial_release_decisions": limited_initial_release_rows,
        "omitted_counts": omitted_counts,
    }


def _manager_fallback_summary_has_actionable_work(
    summary: Mapping[str, Any],
) -> bool:
    """Return whether a fallback summary contains work a manager can advance."""

    counts = summary.get("summary_counts")
    if not isinstance(counts, Mapping):
        return False
    return any(
        int(counts.get(key) or 0) > 0
        for key in (
            "actionable_open_task_count",
            "pending_review_request_count",
            "pending_inbox_count",
            "failed_action_count",
            "routing_gap_count",
            "pending_initial_release_decision_count",
        )
    )


def _manager_fallback_initial_release_decision_ids(
    summary: Mapping[str, Any],
) -> list[str]:
    rows = summary.get("pending_initial_release_decisions")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    return sorted(
        {
            deliverable_id
            for row in rows
            if isinstance(row, Mapping)
            if (deliverable_id := _optional_text(row.get("deliverable_id")))
            is not None
        }
    )


def _manager_fallback_review_routing_authorities(
    state: TeamState,
    *,
    summary: Mapping[str, Any],
) -> list[TeamDispatchReviewRoutingAuthority]:
    """Freeze the exact reviewer and deliverable pairs exposed as routing gaps."""

    raw_gaps = summary.get("routing_gaps")
    if not isinstance(raw_gaps, Sequence) or isinstance(
        raw_gaps,
        (str, bytes, bytearray),
    ):
        return []
    authorities: dict[
        tuple[str, str],
        TeamDispatchReviewRoutingAuthority,
    ] = {}
    for raw_gap in raw_gaps:
        if not isinstance(raw_gap, Mapping):
            continue
        if raw_gap.get("kind") != "reviewer_without_assigned_review_work":
            continue
        reviewer_agent_id = _optional_text(raw_gap.get("agent_id"))
        deliverable_ids = raw_gap.get("deliverable_ids")
        if (
            reviewer_agent_id is None
            or reviewer_agent_id not in state.agents
            or not isinstance(deliverable_ids, Sequence)
            or isinstance(deliverable_ids, (str, bytes, bytearray))
        ):
            continue
        for raw_deliverable_id in deliverable_ids:
            deliverable_id = _optional_text(raw_deliverable_id)
            deliverable = state.deliverables.get(deliverable_id or "")
            task = (
                state.tasks.get(deliverable.task_id)
                if deliverable is not None
                else None
            )
            if deliverable is None or task is None:
                continue
            authorities[(reviewer_agent_id, deliverable.id)] = (
                TeamDispatchReviewRoutingAuthority(
                    kind="reviewer_without_assigned_review_work",
                    reviewer_agent_id=reviewer_agent_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    deliverable_id=deliverable.id,
                )
            )
    return [authorities[key] for key in sorted(authorities)]


def _manager_fallback_review_routing_authority_error(
    state: TeamState,
    *,
    dispatch_agent_id: str,
    authority: TeamDispatchReviewRoutingAuthority,
) -> str | None:
    """Return why a frozen routing pair is no longer intrinsically actionable.

    Idle workload, wake leases, and shortlist order are minting-time concerns.
    Rechecking them here would make one frozen pair invalidate its siblings or
    let unrelated work reorder immutable dispatch authority.
    """

    if authority.consumed_by_review_request_id is not None:
        return (
            "reviewer/deliverable pair "
            f"{authority.reviewer_agent_id}:{authority.deliverable_id} "
            "was already consumed by review "
            f"{authority.consumed_by_review_request_id}"
        )
    manager = state.agents.get(dispatch_agent_id)
    reviewer = state.agents.get(authority.reviewer_agent_id)
    project = state.projects.get(authority.project_id)
    task = state.tasks.get(authority.task_id)
    deliverable = state.deliverables.get(authority.deliverable_id)
    if manager is None:
        return f"manager {dispatch_agent_id} no longer exists"
    if project is None:
        return f"project {authority.project_id} no longer exists"
    if task is None or task.project_id != project.id:
        return f"task {authority.task_id} no longer belongs to project {project.id}"
    if task.state not in _NON_TERMINAL_TASK_STATES:
        return f"task {task.id} is now {task.state.value}, not open review work"
    if (
        deliverable is None
        or deliverable.task_id != task.id
        or not _is_active_deliverable_work(deliverable)
    ):
        return (
            f"deliverable {authority.deliverable_id} is no longer active "
            f"review work for task {task.id}"
        )
    team = state.teams.get(project.team_id)
    if team is None:
        return f"project team {project.team_id} no longer exists"
    if manager.team_id != team.id or team.manager_agent_id != manager.id:
        return (
            f"agent {manager.id} is no longer manager of project team "
            f"{team.id}"
        )
    pair = f"{authority.reviewer_agent_id}:{authority.deliverable_id}"
    if reviewer is None:
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: reviewer no longer exists"
        )
    if reviewer.id == manager.id:
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: manager cannot route review to self"
        )
    if reviewer.status != AgentStatus.ACTIVE:
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: reviewer is not active"
        )
    if reviewer.team_id != team.id:
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: reviewer is not in the project team"
        )
    if _active_stand_downs_for_agent(state, reviewer.id):
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: reviewer is stood down"
        )
    review_profiles = [
        profile
        for profile in state.review_profiles.values()
        if profile.team_id == team.id
        and not profile.archived
        and (profile.project_id is None or profile.project_id == project.id)
    ]
    eligible_project_ids = _manager_fallback_reviewer_eligible_project_ids(
        state,
        agent=reviewer,
        project_ids={project.id},
        review_profiles=review_profiles,
    )
    if project.id not in eligible_project_ids:
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: reviewer is no longer assignment- "
            "and profile-eligible for the project"
        )
    current_reviews = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id == deliverable.id
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
    ]
    if not _manager_fallback_deliverable_needs_review(
        deliverable,
        reviews=current_reviews,
    ):
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: deliverable no longer needs review"
        )
    if deliverable.review_cohort_id is not None:
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: deliverable belongs to review cohort "
            f"{deliverable.review_cohort_id}; retry or replace its blocked cohort "
            "member instead of creating a new review request"
        )
    if any(
        review.reviewer_agent_id == reviewer.id
        for review in current_reviews
    ):
        return (
            f"reviewer/deliverable pair {pair} is no longer an actionable "
            "manager-fallback routing gap: reviewer already has a current "
            "review for the deliverable"
        )
    return None


def _manager_fallback_time(value: datetime | None) -> str | None:
    return _ensure_utc(value).isoformat() if value is not None else None


def _manager_fallback_short_text(
    value: object,
    *,
    limit: int = _MANAGER_FALLBACK_TEXT_LIMIT,
) -> str:
    text = _optional_text(value) or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _manager_fallback_limited_rows(
    rows: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    return rows[:limit], max(0, len(rows) - limit)


def _manager_fallback_omitted_counts(
    counts: Mapping[str, int],
) -> dict[str, int]:
    return {key: value for key, value in counts.items() if value > 0}


def _manager_fallback_review_state_counts(
    reviews: list[ReviewRequest],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for review in reviews:
        counts[review.state.value] = counts.get(review.state.value, 0) + 1
    return dict(sorted(counts.items()))


def _manager_fallback_task_row(
    state: TeamState,
    *,
    task: AgentTask,
    visible_reviews: list[ReviewRequest],
) -> dict[str, Any]:
    task_payload = task_payload_with_assessment(state, task)
    deliverables = _active_task_deliverables(state, task)
    deliverable_ids = [deliverable.id for deliverable in deliverables]
    reviews = [review for review in visible_reviews if review.deliverable_id in deliverable_ids]
    return {
        "id": task.id,
        "title": _manager_fallback_short_text(task.title, limit=160),
        "state": task.state.value,
        "owner_agent_id": task.owner_agent_id,
        "project_id": task.project_id,
        "priority": task.priority,
        "dependency_status": task_payload.get("dependency_status"),
        "blocked_by_task_ids": task_payload.get("blocked_by_task_ids") or [],
        "waiting_reason": task_payload.get("waiting_reason"),
        "deliverable_ids": deliverable_ids,
        "review_request_ids": [review.id for review in reviews],
        "reviewer_agent_ids": sorted({review.reviewer_agent_id for review in reviews}),
        "review_state_counts": _manager_fallback_review_state_counts(reviews),
    }


def _manager_fallback_deliverable_row(
    *,
    deliverable: Deliverable,
    visible_reviews: list[ReviewRequest],
) -> dict[str, Any]:
    reviews = [review for review in visible_reviews if review.deliverable_id == deliverable.id]
    pending_reviews = [
        review for review in reviews if review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
    ]
    row: dict[str, Any] = {
        "id": deliverable.id,
        "task_id": deliverable.task_id,
        "producer_agent_id": deliverable.producer_agent_id,
        "state": deliverable.state.value,
        "summary": _manager_fallback_short_text(deliverable.summary),
        "review_request_ids": [review.id for review in reviews],
        "reviewer_agent_ids": sorted({review.reviewer_agent_id for review in reviews}),
        "review_state_counts": _manager_fallback_review_state_counts(reviews),
    }
    if pending_reviews or _is_active_deliverable_work(deliverable):
        row["artifact_refs"] = list(deliverable.artifact_refs)
        row["git_refs"] = _manager_fallback_git_refs_row(deliverable)
    return row


def _manager_fallback_git_refs_row(deliverable: Deliverable) -> dict[str, Any]:
    refs = deliverable.git_refs
    row: dict[str, Any] = {}
    for field_name in (
        "branch_context_id",
        "agent_branch",
        "review_branch",
        "feature_branch",
        "release_branch",
        "commit_sha",
    ):
        value = getattr(refs, field_name)
        if value:
            row[field_name] = value
    if refs.promotion_record_ids:
        row["promotion_record_ids"] = list(refs.promotion_record_ids)
    return row


def _manager_fallback_initial_release_row(
    state: TeamState,
    *,
    deliverable: Deliverable,
) -> dict[str, Any]:
    task = state.tasks[deliverable.task_id]
    project = state.projects[task.project_id]
    reviewed_commit_sha = (
        deliverable.review_manifest.reviewed_commit_sha
        if deliverable.review_manifest is not None
        else deliverable.git_refs.reviewed_commit_sha
        or deliverable.git_refs.commit_sha
    )
    row: dict[str, Any] = {
        "project_id": project.id,
        "task_id": task.id,
        "task_title": _manager_fallback_short_text(task.title, limit=160),
        "deliverable_id": deliverable.id,
        "summary": _manager_fallback_short_text(deliverable.summary),
        "producer_agent_id": deliverable.producer_agent_id,
        "disposition": "pending",
        "release_policy_configured": project.release_policy is not None,
        "artifact_ref_count": len(deliverable.artifact_refs),
    }
    if reviewed_commit_sha is not None:
        row["reviewed_commit_sha"] = reviewed_commit_sha
    if task.git_base_branch is not None:
        row["git_base_branch"] = task.git_base_branch
    return row


def _manager_fallback_review_row(review: ReviewRequest) -> dict[str, Any]:
    criteria_counts: dict[str, int] = {}
    for criterion in review.criteria:
        criteria_counts[criterion.state] = criteria_counts.get(criterion.state, 0) + 1
    return {
        "id": review.id,
        "deliverable_id": review.deliverable_id,
        "reviewer_agent_id": review.reviewer_agent_id,
        "state": review.state.value,
        "review_profile_id": review.review_profile_id,
        "instructions": _manager_fallback_short_text(review.instructions),
        "finding_count": len(review.findings),
        "latest_finding": _manager_fallback_short_text(
            review.findings[-1] if review.findings else None
        ),
        "criteria_state_counts": dict(sorted(criteria_counts.items())),
    }


def _manager_fallback_inbox_row(item: AgentInboxItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "recipient_agent_id": item.recipient_agent_id,
        "sender_agent_id": item.sender_agent_id,
        "task_id": item.task_id,
        "item_type": item.item_type.value,
        "response_requested": item.response_requested,
        "priority": item.priority.value,
        "created_at": _manager_fallback_time(item.created_at),
        "summary": _manager_fallback_inbox_summary(item),
    }


def _manager_fallback_inbox_summary(item: AgentInboxItem) -> str:
    for key in (
        "summary",
        "message",
        "body",
        "question",
        "instructions",
        "note",
        "reason",
    ):
        summary = _manager_fallback_short_text(item.payload.get(key))
        if summary:
            return summary
    return ""


def _manager_fallback_failed_action_row(record: AgentActionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "agent_id": record.agent_id,
        "dispatch_id": record.dispatch_id,
        "created_at": _manager_fallback_time(record.created_at),
        "error": _manager_fallback_short_text(record.error),
    }


def _manager_fallback_agent_row(
    state: TeamState,
    *,
    agent: PersistentAgent,
    now: datetime,
    visible_tasks: list[AgentTask],
    visible_pending_reviews: list[ReviewRequest],
    visible_pending_inbox: list[AgentInboxItem],
    visible_failed_actions: list[AgentActionRecord],
) -> dict[str, Any]:
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    runtime = state.agent_runtimes.get(agent.id)
    active_tasks = [task for task in visible_tasks if task.owner_agent_id == agent.id]
    pending_reviews = [
        review for review in visible_pending_reviews if review.reviewer_agent_id == agent.id
    ]
    pending_inbox = [item for item in visible_pending_inbox if item.recipient_agent_id == agent.id]
    failed_actions = [record for record in visible_failed_actions if record.agent_id == agent.id]
    stand_downs = _active_stand_downs_for_agent(state, agent.id)
    leased_until = runtime.wake_leased_until if runtime is not None else None
    return {
        "id": agent.id,
        "display_name": agent.display_name,
        "status": agent.status.value,
        "role_id": agent.role_id,
        "role_name": role.name if role is not None else None,
        "project_ids": agent_project_ids(state, agent.id),
        "active_task_count": len(active_tasks),
        "active_task_ids": [task.id for task in active_tasks],
        "pending_review_request_count": len(pending_reviews),
        "pending_review_request_ids": [review.id for review in pending_reviews],
        "pending_inbox_count": len(pending_inbox),
        "response_requested_inbox_count": sum(
            1 for item in pending_inbox if item.response_requested
        ),
        "failed_action_count": len(failed_actions),
        "latest_failed_action_id": failed_actions[0].id if failed_actions else None,
        "active_dispatch_id": (runtime.active_dispatch_id if runtime is not None else None),
        "wake_reason": runtime.wake_reason if runtime is not None else None,
        "wake_leased": (leased_until is not None and _ensure_utc(leased_until) > _ensure_utc(now)),
        "wake_leased_until": _manager_fallback_time(leased_until),
        "next_wake_at": (
            _manager_fallback_time(runtime.next_wake_at) if runtime is not None else None
        ),
        "active_stand_down_count": len(stand_downs),
    }


def _manager_fallback_reviewer_candidates(
    state: TeamState,
    *,
    team_agents: list[PersistentAgent],
    project_ids: set[str],
    review_profiles: list[ReviewProfile],
    now: datetime,
) -> list[PersistentAgent]:
    candidates: list[PersistentAgent] = []
    for agent in team_agents:
        if not _manager_fallback_reviewer_is_wakeable_for_routing(
            state,
            agent=agent,
            now=now,
        ):
            continue
        eligible_project_ids = _manager_fallback_reviewer_eligible_project_ids(
            state,
            agent=agent,
            project_ids=project_ids,
            review_profiles=review_profiles,
        )
        if eligible_project_ids:
            candidates.append(agent)
    return candidates


def _manager_fallback_reviewer_is_wakeable_for_routing(
    state: TeamState,
    *,
    agent: PersistentAgent,
    now: datetime,
) -> bool:
    if agent.status != AgentStatus.ACTIVE:
        return False
    if _active_stand_downs_for_agent(state, agent.id):
        return False
    runtime = state.agent_runtimes.get(agent.id)
    return not (
        runtime is not None
        and runtime.wake_leased_until is not None
        and _ensure_utc(runtime.wake_leased_until) > _ensure_utc(now)
    )


def _manager_fallback_reviewer_eligible_project_ids(
    state: TeamState,
    *,
    agent: PersistentAgent,
    project_ids: set[str],
    review_profiles: list[ReviewProfile],
) -> set[str]:
    if agent.role_id is None:
        return set()
    profile_project_ids: set[str] = set()
    for profile in review_profiles:
        if profile.applicable_role_ids and agent.role_id not in profile.applicable_role_ids:
            continue
        if profile.project_id is None:
            profile_project_ids.update(project_ids)
        elif profile.project_id in project_ids:
            profile_project_ids.add(profile.project_id)
    if not profile_project_ids:
        return set()
    return (
        _manager_fallback_candidate_project_ids(
            state,
            agent=agent,
            project_ids=project_ids,
        )
        & profile_project_ids
    )


def _manager_fallback_candidate_project_ids(
    state: TeamState,
    *,
    agent: PersistentAgent,
    project_ids: set[str],
) -> set[str]:
    candidate_project_ids = set(agent_project_ids(state, agent.id)) & project_ids
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None and role.project_id in project_ids:
        candidate_project_ids.add(role.project_id)
    return candidate_project_ids


def _manager_fallback_routing_gaps(
    state: TeamState,
    *,
    reviewer_candidates: list[PersistentAgent],
    review_profiles: list[ReviewProfile],
    visible_tasks: list[AgentTask],
    visible_deliverables: list[Deliverable],
    visible_reviews: list[ReviewRequest],
    visible_pending_inbox: list[AgentInboxItem],
) -> list[dict[str, Any]]:
    if not reviewer_candidates:
        return []
    tasks_by_id = {task.id: task for task in visible_tasks}
    reviews_by_deliverable: dict[str, list[ReviewRequest]] = {}
    for review in visible_reviews:
        reviews_by_deliverable.setdefault(review.deliverable_id, []).append(review)
    review_work = [
        deliverable
        for deliverable in visible_deliverables
        if (task := tasks_by_id.get(deliverable.task_id)) is not None
        and task.state in _NON_TERMINAL_TASK_STATES
        # Routing gaps authorize a fresh team_request_review call. Cohort
        # membership is immutable (sealed or pending recovery), so its review
        # work must instead proceed through the assigned member or the explicit
        # blocked-member retry/replacement flow.
        and deliverable.review_cohort_id is None
        and _manager_fallback_deliverable_needs_review(
            deliverable,
            reviews=reviews_by_deliverable.get(deliverable.id, []),
        )
    ]
    if not review_work:
        return []
    gaps: list[dict[str, Any]] = []
    visible_project_ids = {task.project_id for task in visible_tasks}
    for reviewer in reviewer_candidates:
        reviewer_project_ids = _manager_fallback_reviewer_eligible_project_ids(
            state,
            agent=reviewer,
            project_ids=visible_project_ids,
            review_profiles=review_profiles,
        )
        if not reviewer_project_ids:
            continue
        candidate_work = [
            deliverable
            for deliverable in review_work
            if (task := tasks_by_id.get(deliverable.task_id)) is not None
            and task.project_id in reviewer_project_ids
            and not any(
                review.reviewer_agent_id == reviewer.id
                for review in reviews_by_deliverable.get(deliverable.id, [])
            )
        ]
        if not candidate_work:
            continue
        reviewer_active_tasks = [
            task for task in visible_tasks if task.owner_agent_id == reviewer.id
        ]
        reviewer_pending_reviews = [
            review
            for review in visible_reviews
            if review.reviewer_agent_id == reviewer.id
            and review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
        ]
        reviewer_pending_inbox = [
            item for item in visible_pending_inbox if item.recipient_agent_id == reviewer.id
        ]
        if reviewer_active_tasks or reviewer_pending_reviews or reviewer_pending_inbox:
            continue
        display_name = reviewer.display_name or reviewer.id
        deliverable_ids = [deliverable.id for deliverable in candidate_work[:5]]
        task_ids = sorted({deliverable.task_id for deliverable in candidate_work[:5]})
        existing_reviewers = sorted(
            {
                review.reviewer_agent_id
                for deliverable in candidate_work
                for review in reviews_by_deliverable.get(deliverable.id, [])
                if review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
            }
        )
        gaps.append(
            {
                "kind": "reviewer_without_assigned_review_work",
                "severity": "warning",
                "agent_id": reviewer.id,
                "agent_display_name": reviewer.display_name,
                "summary": (
                    f"{display_name} has no pending review requests, active "
                    "tasks, or pending inbox items, but review work exists in "
                    "their project."
                ),
                "suggested_action": (
                    "Create review_requests for this reviewer for the listed "
                    "deliverables, or assign a review-focused task."
                ),
                "project_ids": sorted(reviewer_project_ids),
                "task_ids": task_ids,
                "deliverable_ids": deliverable_ids,
                "existing_reviewer_agent_ids": existing_reviewers,
            }
        )
    return gaps


def _manager_fallback_review_decision_guard_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    review: ReviewRequest,
) -> str | None:
    deliverable = state.deliverables.get(review.deliverable_id)
    if deliverable is None:
        return None
    task = state.tasks.get(deliverable.task_id)
    if task is None:
        return None
    team_id = actor.team_id
    if team_id is None:
        return None
    now = utc_now()
    project_ids = {project.id for project in state.projects.values() if project.team_id == team_id}
    team_agents = sorted(
        (
            agent
            for agent in state.agents.values()
            if agent.team_id == team_id and agent.status != AgentStatus.ARCHIVED
        ),
        key=lambda item: item.id,
    )
    team_agent_ids = {agent.id for agent in team_agents}
    visible_tasks = sorted(
        (
            item
            for item in state.tasks.values()
            if _manager_fallback_task_visible_for_work(state, item, project_ids)
        ),
        key=lambda item: (item.priority, item.created_at, item.id),
    )
    visible_task_ids = {item.id for item in visible_tasks}
    visible_deliverables = sorted(
        (
            item
            for item in state.deliverables.values()
            if item.task_id in visible_task_ids and item.superseded_by_deliverable_id is None
        ),
        key=lambda item: (item.created_at, item.id),
    )
    visible_deliverable_ids = {item.id for item in visible_deliverables}
    review_profiles = sorted(
        (
            profile
            for profile in state.review_profiles.values()
            if profile.team_id == team_id
            and not profile.archived
            and (profile.project_id is None or profile.project_id in project_ids)
        ),
        key=lambda item: item.id,
    )
    reviewer_candidates = _manager_fallback_reviewer_candidates(
        state,
        team_agents=team_agents,
        project_ids=project_ids,
        review_profiles=review_profiles,
        now=now,
    )
    visible_reviews = [
        item
        for item in state.review_requests.values()
        if item.deliverable_id in visible_deliverable_ids
        and item.superseded_by_review_request_id is None
        and item.retired_at is None
    ]
    visible_pending_inbox = sorted(
        (
            item
            for item in state.inbox.values()
            if item.recipient_agent_id in team_agent_ids
            and (item.sender_agent_id is None or item.sender_agent_id in team_agent_ids)
            and (item.task_id is None or item.task_id in visible_task_ids)
            and item.state == TeamInboxItemState.PENDING
        ),
        key=lambda item: (item.created_at, item.id),
    )
    routing_gaps = _manager_fallback_routing_gaps(
        state,
        reviewer_candidates=reviewer_candidates,
        review_profiles=review_profiles,
        visible_tasks=visible_tasks,
        visible_deliverables=visible_deliverables,
        visible_reviews=visible_reviews,
        visible_pending_inbox=visible_pending_inbox,
    )
    relevant_gaps = [
        gap
        for gap in routing_gaps
        if gap.get("kind") == "reviewer_without_assigned_review_work"
        and gap.get("agent_id") != actor.id
        and deliverable.id in (gap.get("deliverable_ids") or [])
    ]
    if not relevant_gaps:
        return None
    gap = relevant_gaps[0]
    reviewer_id = str(gap.get("agent_id") or "")
    reviewer_name = _optional_text(gap.get("agent_display_name")) or reviewer_id
    return (
        f"manager fallback review decision for {review.id} should route deliverable "
        f"{deliverable.id} to idle profile-eligible reviewer {reviewer_name} "
        f"({reviewer_id}) instead of deciding it; create a fresh review_requests "
        "item for that reviewer, or include override_routing_gap_reason to accept "
        "as manager"
    )


def _manager_fallback_deliverable_needs_review(
    deliverable: Deliverable,
    *,
    reviews: list[ReviewRequest],
) -> bool:
    if not _is_active_deliverable_work(deliverable):
        return False
    if any(finding.state == "open" for finding in deliverable.carried_findings):
        return True
    return any(review.release_package_id is None for review in reviews)


__all__ = [
    "_MANAGER_FALLBACK_OPEN_WORK_LIMIT",
    "_MANAGER_FALLBACK_PENDING_INBOX_LIMIT",
    "_MANAGER_FALLBACK_FAILED_ACTION_LIMIT",
    "_MANAGER_FALLBACK_ROUTING_GAP_LIMIT",
    "_MANAGER_FALLBACK_TEXT_LIMIT",
    "_MANAGER_FALLBACK_PENDING_REVIEW_STATES",
    "_manager_fallback_task_visible_for_work",
    "_manager_fallback_summary_payload",
    "_manager_fallback_summary_has_actionable_work",
    "_manager_fallback_initial_release_decision_ids",
    "_manager_fallback_review_routing_authorities",
    "_manager_fallback_review_routing_authority_error",
    "_manager_fallback_time",
    "_manager_fallback_short_text",
    "_manager_fallback_limited_rows",
    "_manager_fallback_omitted_counts",
    "_manager_fallback_review_state_counts",
    "_manager_fallback_task_row",
    "_manager_fallback_deliverable_row",
    "_manager_fallback_git_refs_row",
    "_manager_fallback_initial_release_row",
    "_manager_fallback_review_row",
    "_manager_fallback_inbox_row",
    "_manager_fallback_inbox_summary",
    "_manager_fallback_failed_action_row",
    "_manager_fallback_agent_row",
    "_manager_fallback_reviewer_candidates",
    "_manager_fallback_reviewer_is_wakeable_for_routing",
    "_manager_fallback_reviewer_eligible_project_ids",
    "_manager_fallback_candidate_project_ids",
    "_manager_fallback_routing_gaps",
    "_manager_fallback_review_decision_guard_error",
    "_manager_fallback_deliverable_needs_review",
]
