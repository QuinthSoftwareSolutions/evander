from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from evander.models import utc_now

from ...models import (
    AgentInboxItem,
    AgentStatus,
    GovernanceAgendaItem,
    GovernanceAgendaItemState,
    GovernanceAgendaItemType,
    GovernanceRuntime,
    PersistentAgent,
    TeamBlocker,
    TeamBlockerEvent,
    TeamBlockerEventType,
    TeamBlockerResolutionScope,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
)
from .common import _ensure_utc, _optional_text, _required_text
from .governance import _governance_groups_for_team
from .operator_escalation import OPERATOR_ESCALATION_EVENT_TYPE
from .stand_down import _active_stand_downs_for_agent
from .task_supervision import parent_task_for_child
from .wake import _append_agent_wake_requested_event_for_inbox_item
from .work_due import _mark_agent_work_due


BLOCKER_PROGRESS_EVENT_TYPES = {
    TeamBlockerEventType.PROGRESS,
    TeamBlockerEventType.DELEGATED,
    TeamBlockerEventType.DELEGATE_RESULT,
    TeamBlockerEventType.OWNER_TRANSFERRED,
    TeamBlockerEventType.RESOLVED,
    TeamBlockerEventType.TERMINAL_ROUTED,
    TeamBlockerEventType.ROUTING_FAILED,
}
GOVERNANCE_WAKE_REQUESTED_EVENT_TYPE = "team.governance.wake_requested"


def open_or_get_blocker(
    state: TeamState,
    *,
    source_kind: TeamBlockerSourceKind | str,
    source_id: str,
    summary: str,
    details: str = "",
    reason_code: str = "unspecified",
    priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
    reporter_agent_id: str | None = None,
    reporter_actor: str | None = None,
    initial_owner_agent_id: str | None = None,
    route_initial_owner_even_if_unavailable: bool = False,
    wake_owner: bool = True,
    team_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    affected_task_ids: Sequence[str] | None = None,
    resolution_scope: TeamBlockerResolutionScope | str = TeamBlockerResolutionScope.TASK,
    result: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[TeamBlocker, bool]:
    normalized_kind = (
        source_kind
        if isinstance(source_kind, TeamBlockerSourceKind)
        else TeamBlockerSourceKind(str(source_kind))
    )
    normalized_source_id = _required_text(source_id, "source_id")
    normalized_resolution_scope = (
        resolution_scope
        if isinstance(resolution_scope, TeamBlockerResolutionScope)
        else TeamBlockerResolutionScope(str(resolution_scope))
    )
    effective_now = _ensure_utc(now or utc_now())
    reporter = (
        state.agents.get(reporter_agent_id) if _optional_text(reporter_agent_id) else None
    )
    normalized_team_id = _optional_text(team_id) or (reporter.team_id if reporter else None)
    if normalized_team_id is None:
        raise ValueError("blocker requires team_id or a team-bound reporter")
    team = state.teams.get(normalized_team_id)
    if team is None:
        raise ValueError(f"team {normalized_team_id} does not exist")
    existing = next(
        (
            blocker
            for blocker in state.blockers.values()
            if blocker.state == TeamBlockerState.OPEN
            and blocker.team_id == normalized_team_id
            and blocker.source_kind == normalized_kind
            and blocker.source_id == normalized_source_id
        ),
        None,
    )
    if existing is not None:
        return existing, False

    owner: PersistentAgent | None
    skipped_agent_ids: list[str]
    task = state.tasks.get(_optional_text(task_id) or "")
    parent_task = parent_task_for_child(state, task) if task is not None else None
    parent_owner_id = parent_task.owner_agent_id if parent_task is not None else None
    if initial_owner_agent_id is not None and route_initial_owner_even_if_unavailable:
        candidate = state.agents.get(initial_owner_agent_id)
        owner = (
            candidate
            if candidate is not None
            and candidate.team_id == team.id
            and candidate.status == AgentStatus.ACTIVE
            else None
        )
        skipped_agent_ids = [] if owner is not None else [initial_owner_agent_id]
    elif initial_owner_agent_id is not None:
        owner, skipped_agent_ids = nearest_available_agent(
            state,
            start_agent_id=initial_owner_agent_id,
            team_id=team.id,
        )
    elif parent_owner_id is not None and (
        reporter is None or parent_owner_id != reporter.id
    ):
        owner, skipped_agent_ids = nearest_available_agent(
            state,
            start_agent_id=parent_owner_id,
            team_id=team.id,
        )
    elif reporter is not None:
        owner, skipped_agent_ids = nearest_available_superior(state, reporter)
    else:
        owner, skipped_agent_ids = nearest_available_agent(
            state,
            start_agent_id=team.manager_agent_id,
            team_id=team.id,
        )

    anchor_owner = owner or reporter or state.agents.get(team.manager_agent_id)
    route = TeamBlockerRoute.OWNED if anchor_owner is not None else TeamBlockerRoute.ROUTING_FAILED
    blocker = TeamBlocker(
        id=f"blocker-{uuid4().hex}",
        team_id=team.id,
        project_id=_optional_text(project_id),
        task_id=_optional_text(task_id),
        source_kind=normalized_kind,
        source_id=normalized_source_id,
        reason_code=_required_text(reason_code, "reason_code"),
        reporter_agent_id=reporter.id if reporter is not None else None,
        reporter_actor=_optional_text(reporter_actor),
        owner_agent_id=anchor_owner.id if anchor_owner is not None else None,
        affected_task_ids=list(affected_task_ids or []),
        resolution_scope=normalized_resolution_scope,
        state=TeamBlockerState.OPEN,
        route=route,
        summary=_required_text(summary, "summary"),
        details=_optional_text(details) or "",
        priority=(
            priority
            if isinstance(priority, TeamInboxPriority)
            else TeamInboxPriority(str(priority))
        ),
        created_at=effective_now,
        updated_at=effective_now,
    )
    state.blockers[blocker.id] = blocker
    append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.REPORTED,
        actor_agent_id=reporter.id if reporter is not None else None,
        to_agent_id=owner.id if owner is not None else None,
        note=blocker.details or blocker.summary,
        metadata={
            "skipped_agent_ids": skipped_agent_ids,
            **(
                {"reporter_actor": blocker.reporter_actor}
                if blocker.reporter_actor is not None
                else {}
            ),
        },
        now=effective_now,
    )
    if result is not None:
        result.setdefault("created_blocker_ids", []).append(blocker.id)

    if owner is not None:
        blocker.owner_agent_id = owner.id
        item = create_blocker_inbox(
            state,
            blocker=blocker,
            recipient_agent_id=owner.id,
            sender_agent_id=reporter.id if reporter is not None else None,
            kind="blocker_reported",
            result=result if wake_owner else None,
            now=effective_now,
        )
        if result is not None and not wake_owner:
            result.setdefault("created_inbox_item_ids", []).append(item.id)
    else:
        route_blocker_terminal(
            state,
            blocker=blocker,
            actor_agent_id=anchor_owner.id if anchor_owner is not None else None,
            dispatch_id=None,
            reason="No available local owner exists for the blocker.",
            result=result,
            now=effective_now,
        )
    return blocker, True


def open_or_update_agent_execution_stall(
    state: TeamState,
    *,
    agent_id: str,
    affected_task_ids: Sequence[str],
    exhausted_dispatch_id: str | None,
    exhausted_dispatch_task_ids: Sequence[str],
    no_progress_count: int,
    max_no_progress_count: int,
    result: dict[str, Any],
    now: datetime | None = None,
) -> TeamBlocker:
    """Materialize one durable, routed incident for an agent-wide execution stall."""

    effective_now = _ensure_utc(now or utc_now())
    agent = state.agents.get(_required_text(agent_id, "agent_id"))
    if agent is None or agent.team_id is None:
        raise ValueError(f"agent {agent_id} is not assigned to a Team")
    current_task_ids = sorted(
        {
            task_id
            for raw_task_id in affected_task_ids
            if (task_id := _optional_text(raw_task_id)) is not None
            and (task := state.tasks.get(task_id)) is not None
            and task.owner_agent_id == agent.id
        }
    )
    blocker, _created = open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.AGENT_EXECUTION_STALL,
        source_id=agent.id,
        reason_code="agent_no_progress_exhausted",
        summary=f"Agent {agent.id} exhausted its no-progress allowance",
        details=(
            f"The agent reached {no_progress_count} consecutive no-progress "
            f"dispatches (configured limit {max_no_progress_count})."
        ),
        priority=TeamInboxPriority.HIGH,
        reporter_agent_id=agent.id,
        team_id=agent.team_id,
        affected_task_ids=current_task_ids,
        result=result,
        now=effective_now,
    )
    if (
        blocker.state == TeamBlockerState.OPEN
        and blocker.owner_agent_id == agent.id
        and blocker.route
        in {TeamBlockerRoute.OWNED, TeamBlockerRoute.DELEGATED}
    ):
        route_blocker_terminal(
            state,
            blocker=blocker,
            actor_agent_id=agent.id,
            dispatch_id=_optional_text(exhausted_dispatch_id),
            reason=(
                "The stalled agent has no available local superior; route the "
                "execution stall to the configured terminal authority."
            ),
            result=result,
            now=effective_now,
        )
    merged_task_ids = sorted(set(blocker.affected_task_ids).union(current_task_ids))
    if merged_task_ids != blocker.affected_task_ids:
        blocker.affected_task_ids = merged_task_ids
        blocker.updated_at = effective_now

    normalized_dispatch_id = _optional_text(exhausted_dispatch_id)
    scoped_task_ids = sorted(
        {
            task_id
            for raw_task_id in exhausted_dispatch_task_ids
            if (task_id := _optional_text(raw_task_id)) is not None
        }
    )
    evidence_exists = any(
        event.blocker_id == blocker.id
        and event.event_type == TeamBlockerEventType.PROGRESS
        and event.dispatch_id == normalized_dispatch_id
        and event.metadata.get("kind") == "execution_stall_exhausted"
        and event.metadata.get("no_progress_count") == no_progress_count
        for event in state.blocker_events.values()
    )
    if not evidence_exists:
        evidence_event = append_blocker_event(
            state,
            blocker=blocker,
            event_type=TeamBlockerEventType.PROGRESS,
            actor_agent_id=agent.id,
            dispatch_id=normalized_dispatch_id,
            note=(
                f"Dispatch {normalized_dispatch_id or 'unknown'} exhausted the "
                "agent-wide no-progress allowance."
            ),
            reference_ids=scoped_task_ids,
            metadata={
                "kind": "execution_stall_exhausted",
                "exhausted_at": effective_now.isoformat(),
                "no_progress_count": no_progress_count,
                "max_no_progress_count": max_no_progress_count,
                "scoped_task_ids": scoped_task_ids,
                "affected_task_ids": merged_task_ids,
            },
            now=effective_now,
        )
        result.setdefault("blocker_event_ids", []).append(evidence_event.id)

    accountable_parents_by_owner: dict[str, set[str]] = {}
    affected_children_by_owner: dict[str, set[str]] = {}
    for task_id in merged_task_ids:
        task = state.tasks.get(task_id)
        parent = parent_task_for_child(state, task) if task is not None else None
        if (
            parent is None
            or parent.owner_agent_id == agent.id
            or parent.owner_agent_id == blocker.owner_agent_id
        ):
            continue
        accountable_parents_by_owner.setdefault(
            parent.owner_agent_id,
            set(),
        ).add(parent.id)
        affected_children_by_owner.setdefault(
            parent.owner_agent_id,
            set(),
        ).add(task_id)
    for owner_id, parent_task_ids in sorted(accountable_parents_by_owner.items()):
        notice = create_blocker_inbox(
            state,
            blocker=blocker,
            recipient_agent_id=owner_id,
            sender_agent_id=agent.id,
            kind="agent_execution_stall_parent_notice",
            result=result,
            now=effective_now,
        )
        notice.payload["accountable_parent_task_ids"] = sorted(parent_task_ids)
        notice.payload["affected_child_task_ids"] = sorted(
            affected_children_by_owner[owner_id]
        )
        exhausted_ids = {
            str(value)
            for value in notice.payload.get("exhausted_dispatch_ids") or []
            if str(value).strip()
        }
        if normalized_dispatch_id is not None:
            exhausted_ids.add(normalized_dispatch_id)
        notice.payload["exhausted_dispatch_ids"] = sorted(exhausted_ids)
        notice.updated_at = effective_now
    return blocker


def nearest_available_superior(
    state: TeamState,
    agent: PersistentAgent,
) -> tuple[PersistentAgent | None, list[str]]:
    start_id = agent.reports_to_agent_id
    team = state.teams.get(agent.team_id or "")
    if start_id is None and team is not None and team.manager_agent_id != agent.id:
        start_id = team.manager_agent_id
    return nearest_available_agent(
        state,
        start_agent_id=start_id,
        team_id=agent.team_id,
    )


def nearest_available_agent(
    state: TeamState,
    *,
    start_agent_id: str | None,
    team_id: str | None,
) -> tuple[PersistentAgent | None, list[str]]:
    current_id = _optional_text(start_agent_id)
    seen: set[str] = set()
    skipped: list[str] = []
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        agent = state.agents.get(current_id)
        if agent is None:
            skipped.append(current_id)
            break
        if (
            agent.team_id == team_id
            and agent.status == AgentStatus.ACTIVE
            and not _active_stand_downs_for_agent(state, agent.id)
        ):
            return agent, skipped
        skipped.append(agent.id)
        current_id = agent.reports_to_agent_id
    return None, skipped


def reconcile_unavailable_blocker_assignments(
    state: TeamState,
    *,
    result: dict[str, Any],
    now: datetime | None = None,
) -> list[str]:
    effective_now = _ensure_utc(now or utc_now())
    rerouted_ids: list[str] = []
    for blocker in sorted(state.blockers.values(), key=lambda item: item.id):
        if blocker.state != TeamBlockerState.OPEN or blocker.route not in {
            TeamBlockerRoute.OWNED,
            TeamBlockerRoute.DELEGATED,
        }:
            continue
        if blocker.route == TeamBlockerRoute.DELEGATED and not _blocker_agent_available(
            state,
            agent_id=blocker.delegate_agent_id,
            team_id=blocker.team_id,
        ):
            delegate_id = blocker.delegate_agent_id
            if delegate_id is not None:
                handle_blocker_inbox_for_agent(
                    state,
                    blocker_id=blocker.id,
                    agent_id=delegate_id,
                    now=effective_now,
                )
            blocker.delegate_agent_id = None
            blocker.route = TeamBlockerRoute.OWNED
            blocker.active_dispatch_id = None
            blocker.follow_up_at = None
            blocker.updated_at = effective_now
            append_blocker_event(
                state,
                blocker=blocker,
                event_type=TeamBlockerEventType.DELEGATION_STALLED,
                from_agent_id=delegate_id,
                to_agent_id=blocker.owner_agent_id,
                note="Delegated blocker assignee became unavailable.",
                now=effective_now,
            )
            if blocker.owner_agent_id is not None:
                create_blocker_inbox(
                    state,
                    blocker=blocker,
                    recipient_agent_id=blocker.owner_agent_id,
                    sender_agent_id=delegate_id,
                    kind="blocker_delegation_stalled",
                    result=result,
                    now=effective_now,
                )
            rerouted_ids.append(blocker.id)
        if not _blocker_agent_available(
            state,
            agent_id=blocker.owner_agent_id,
            team_id=blocker.team_id,
        ):
            transfer_blocker_up(
                state,
                blocker=blocker,
                actor_agent_id=None,
                dispatch_id=None,
                reason="Blocker owner became unavailable.",
                result=result,
                now=effective_now,
            )
            if blocker.id not in rerouted_ids:
                rerouted_ids.append(blocker.id)
    return rerouted_ids


def _blocker_agent_available(
    state: TeamState,
    *,
    agent_id: str | None,
    team_id: str,
) -> bool:
    agent = state.agents.get(agent_id or "")
    return bool(
        agent is not None
        and agent.team_id == team_id
        and agent.status == AgentStatus.ACTIVE
        and not _active_stand_downs_for_agent(state, agent.id)
    )


def blocker_resolution_line_agent_ids(
    state: TeamState,
    *,
    blocker: TeamBlocker,
) -> list[str]:
    """Return the Team reporting line allowed to resolve ``blocker`` manually.

    Human-reported blockers remain anchored to their reporter even after ownership
    transfers or the blocker reaches a terminal route. Automated blockers have no
    reporter, so their first recorded owner is the anchor. Delegates and peers are
    deliberately absent: delegation does not alter the escalation line.
    """

    anchor_id = _blocker_resolution_anchor_agent_id(state, blocker=blocker)
    team = state.teams.get(blocker.team_id)
    current_id = anchor_id
    seen: set[str] = set()
    agent_ids: list[str] = []
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        agent = state.agents.get(current_id)
        if agent is None or agent.team_id != blocker.team_id:
            break
        agent_ids.append(agent.id)
        next_id = _optional_text(agent.reports_to_agent_id)
        if (
            next_id is None
            and team is not None
            and team.manager_agent_id != agent.id
        ):
            next_id = team.manager_agent_id
        current_id = next_id
    return agent_ids


def agent_can_resolve_blocker(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    agent_id: str,
) -> bool:
    """Whether an active Team agent is on the blocker's escalation line."""

    return _blocker_agent_available(
        state,
        agent_id=agent_id,
        team_id=blocker.team_id,
    ) and agent_id in blocker_resolution_line_agent_ids(state, blocker=blocker)


def _blocker_resolution_anchor_agent_id(
    state: TeamState,
    *,
    blocker: TeamBlocker,
) -> str | None:
    reporter_id = _optional_text(blocker.reporter_agent_id)
    reporter = state.agents.get(reporter_id or "")
    if reporter is not None and reporter.team_id == blocker.team_id:
        return reporter.id

    reported_events = sorted(
        (
            event
            for event in state.blocker_events.values()
            if event.blocker_id == blocker.id
            and event.event_type == TeamBlockerEventType.REPORTED
            and _optional_text(event.to_agent_id) is not None
        ),
        key=lambda event: (event.created_at, event.id),
    )
    if reported_events:
        return reported_events[0].to_agent_id
    return blocker.owner_agent_id


def append_blocker_event(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    event_type: TeamBlockerEventType,
    actor_agent_id: str | None = None,
    from_agent_id: str | None = None,
    to_agent_id: str | None = None,
    dispatch_id: str | None = None,
    note: str = "",
    reference_ids: Sequence[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> TeamBlockerEvent:
    event = TeamBlockerEvent(
        id=f"blocker-event-{uuid4().hex}",
        blocker_id=blocker.id,
        event_type=event_type,
        actor_agent_id=_optional_text(actor_agent_id),
        from_agent_id=_optional_text(from_agent_id),
        to_agent_id=_optional_text(to_agent_id),
        dispatch_id=_optional_text(dispatch_id),
        note=_optional_text(note) or "",
        reference_ids=list(reference_ids or []),
        metadata=dict(metadata or {}),
        created_at=_ensure_utc(now or utc_now()),
    )
    state.blocker_events[event.id] = event
    return event


def create_blocker_inbox(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    recipient_agent_id: str,
    sender_agent_id: str | None,
    kind: str,
    result: dict[str, Any] | None,
    now: datetime | None = None,
) -> AgentInboxItem:
    effective_now = _ensure_utc(now or utc_now())
    for existing in state.inbox.values():
        if (
            existing.recipient_agent_id == recipient_agent_id
            and existing.state == TeamInboxItemState.PENDING
            and existing.payload.get("blocker_id") == blocker.id
            and existing.payload.get("kind") == kind
        ):
            return existing
    item = AgentInboxItem(
        id=f"inbox-{uuid4().hex}",
        recipient_agent_id=recipient_agent_id,
        sender_agent_id=(
            sender_agent_id if sender_agent_id in state.agents else None
        ),
        task_id=blocker.task_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=True,
        priority=blocker.priority,
        payload={
            "kind": kind,
            "blocker_id": blocker.id,
            "source_kind": blocker.source_kind.value,
            "source_id": blocker.source_id,
            "reason_code": blocker.reason_code,
            "affected_task_ids": list(blocker.affected_task_ids),
            "summary": blocker.summary,
            "details": blocker.details,
            "owner_agent_id": blocker.owner_agent_id,
            "delegate_agent_id": blocker.delegate_agent_id,
            "route": blocker.route.value,
            "escalation_depth": blocker.escalation_depth,
            **(
                {
                    "transition": "blocker",
                    "parent_task_id": parent_task_id,
                    "child_task_id": blocker.task_id,
                }
                if (
                    blocker.task_id is not None
                    and (child_task := state.tasks.get(blocker.task_id)) is not None
                    and (parent_task_id := child_task.parent_task_id) is not None
                )
                else {}
            ),
        },
        created_at=effective_now,
        updated_at=effective_now,
    )
    state.inbox[item.id] = item
    _mark_agent_work_due(state, agent_id=recipient_agent_id, now=effective_now)
    if result is not None:
        result.setdefault("created_inbox_item_ids", []).append(item.id)
        _append_agent_wake_requested_event_for_inbox_item(
            state,
            item=item,
            result=result,
            now=effective_now,
        )
    return item


def handle_blocker_inbox_for_agent(
    state: TeamState,
    *,
    blocker_id: str,
    agent_id: str,
    now: datetime | None = None,
) -> list[str]:
    effective_now = _ensure_utc(now or utc_now())
    handled: list[str] = []
    for item in state.inbox.values():
        if (
            item.recipient_agent_id == agent_id
            and item.state == TeamInboxItemState.PENDING
            and item.payload.get("blocker_id") == blocker_id
        ):
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = effective_now
            handled.append(item.id)
    return handled


def assign_blocker_owner(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    owner_agent_id: str,
    actor_agent_id: str | None,
    reason: str,
    result: dict[str, Any] | None,
    wake_owner: bool = True,
    now: datetime | None = None,
) -> None:
    """Route an open blocker to an explicitly selected local recovery owner."""

    effective_now = _ensure_utc(now or utc_now())
    owner = state.agents.get(_required_text(owner_agent_id, "owner_agent_id"))
    if owner is None or owner.team_id != blocker.team_id or owner.status != AgentStatus.ACTIVE:
        raise ValueError(
            f"blocker {blocker.id} owner {owner_agent_id} is not an active team agent"
        )
    previous_owner_id = blocker.owner_agent_id
    route_changed = (
        blocker.owner_agent_id != owner.id
        or blocker.delegate_agent_id is not None
        or blocker.route != TeamBlockerRoute.OWNED
        or bool(blocker.terminal_reference_ids)
    )
    if route_changed:
        pending_recipient_ids = {
            item.recipient_agent_id
            for item in state.inbox.values()
            if item.state == TeamInboxItemState.PENDING
            and item.payload.get("blocker_id") == blocker.id
            and item.recipient_agent_id != owner.id
        }
        for recipient_agent_id in pending_recipient_ids:
            handle_blocker_inbox_for_agent(
                state,
                blocker_id=blocker.id,
                agent_id=recipient_agent_id,
                now=effective_now,
            )
        for reference_id in blocker.terminal_reference_ids:
            agenda = state.governance_agenda.get(reference_id)
            if agenda is not None and agenda.state == GovernanceAgendaItemState.PENDING:
                agenda.state = GovernanceAgendaItemState.HANDLED
                agenda.updated_at = effective_now
        blocker.owner_agent_id = owner.id
        blocker.delegate_agent_id = None
        blocker.route = TeamBlockerRoute.OWNED
        blocker.active_dispatch_id = None
        blocker.follow_up_at = None
        blocker.terminal_reference_ids = []
        blocker.updated_at = effective_now
        event = append_blocker_event(
            state,
            blocker=blocker,
            event_type=TeamBlockerEventType.OWNER_TRANSFERRED,
            actor_agent_id=actor_agent_id,
            from_agent_id=previous_owner_id,
            to_agent_id=owner.id,
            note=reason,
            now=effective_now,
        )
        if result is not None:
            result.setdefault("blocker_event_ids", []).append(event.id)

    if any(
        item.state == TeamInboxItemState.PENDING
        and item.recipient_agent_id == owner.id
        and item.payload.get("blocker_id") == blocker.id
        for item in state.inbox.values()
    ):
        return
    existing_inbox_ids = set(state.inbox)
    item = create_blocker_inbox(
        state,
        blocker=blocker,
        recipient_agent_id=owner.id,
        sender_agent_id=actor_agent_id,
        kind="blocker_recovery_assigned",
        result=result if wake_owner else None,
        now=effective_now,
    )
    if result is not None and not wake_owner and item.id not in existing_inbox_ids:
        result.setdefault("created_inbox_item_ids", []).append(item.id)


def blocker_events_for_dispatch(
    state: TeamState,
    *,
    blocker_id: str,
    dispatch_id: str,
) -> list[TeamBlockerEvent]:
    return sorted(
        [
            event
            for event in state.blocker_events.values()
            if event.blocker_id == blocker_id and event.dispatch_id == dispatch_id
        ],
        key=lambda event: (event.created_at, event.id),
    )


def blockers_due_for_agent(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime | None = None,
) -> list[TeamBlocker]:
    effective_now = _ensure_utc(now or utc_now())
    rows: list[TeamBlocker] = []
    for blocker in blockers_assigned_to_agent(state, agent_id=agent_id):
        has_pending_inbox = any(
            item.recipient_agent_id == agent_id
            and item.state == TeamInboxItemState.PENDING
            and item.payload.get("blocker_id") == blocker.id
            for item in state.inbox.values()
        )
        follow_up_due = blocker.follow_up_at is None or _ensure_utc(blocker.follow_up_at) <= effective_now
        if has_pending_inbox or follow_up_due:
            rows.append(blocker)
    return sorted(rows, key=lambda item: (item.created_at, item.id))


def blockers_assigned_to_agent(
    state: TeamState,
    *,
    agent_id: str,
) -> list[TeamBlocker]:
    """Return the exact open blockers currently routed to one agent.

    This is deliberately independent of follow-up scheduling. A dispatch may
    expose an assigned blocker in runtime attention before its next follow-up
    is due, and its immutable authorization scope must still cover that exact
    blocker without admitting team-manager or foreign-owner access.
    """

    rows = [
        blocker
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and (
            (
                blocker.route == TeamBlockerRoute.DELEGATED
                and blocker.delegate_agent_id == agent_id
            )
            or (
                blocker.route
                in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
                and blocker.owner_agent_id == agent_id
            )
        )
    ]
    return sorted(rows, key=lambda item: (item.created_at, item.id))


def assign_blockers_to_dispatch(
    state: TeamState,
    *,
    agent_id: str,
    dispatch_id: str,
    blocker_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    effective_now = _ensure_utc(now or utc_now())
    if blocker_ids is None:
        blockers = blockers_due_for_agent(state, agent_id=agent_id, now=effective_now)
    else:
        blockers = []
        for blocker_id in sorted(set(blocker_ids)):
            blocker = state.blockers.get(blocker_id)
            assigned = blocker is not None and (
                (
                    blocker.route == TeamBlockerRoute.DELEGATED
                    and blocker.delegate_agent_id == agent_id
                )
                or (
                    blocker.route
                    in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
                    and blocker.owner_agent_id == agent_id
                )
            )
            if (
                blocker is None
                or blocker.state != TeamBlockerState.OPEN
                or not assigned
            ):
                raise ValueError(
                    f"blocker {blocker_id} is not open and assigned to agent {agent_id}"
                )
            blockers.append(blocker)
    for blocker in blockers:
        blocker.active_dispatch_id = dispatch_id
        blocker.updated_at = effective_now
    return [blocker.model_dump(mode="json") for blocker in blockers]


def complete_blocker_dispatches(
    state: TeamState,
    *,
    agent_id: str,
    dispatch_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> list[str]:
    effective_now = _ensure_utc(now or utc_now())
    processed: list[str] = []
    blockers = sorted(
        [
            blocker
            for blocker in state.blockers.values()
            if blocker.state == TeamBlockerState.OPEN
            and blocker.active_dispatch_id == dispatch_id
        ],
        key=lambda blocker: blocker.id,
    )
    for blocker in blockers:
        events = blocker_events_for_dispatch(
            state,
            blocker_id=blocker.id,
            dispatch_id=dispatch_id,
        )
        progressed = any(event.event_type in BLOCKER_PROGRESS_EVENT_TYPES for event in events)
        blocker.active_dispatch_id = None
        blocker.updated_at = effective_now
        if progressed:
            processed.append(blocker.id)
            continue
        if (
            blocker.route == TeamBlockerRoute.DELEGATED
            and blocker.delegate_agent_id == agent_id
        ):
            delegate_id = blocker.delegate_agent_id
            handle_blocker_inbox_for_agent(
                state,
                blocker_id=blocker.id,
                agent_id=delegate_id or "",
                now=effective_now,
            )
            blocker.delegate_agent_id = None
            blocker.route = TeamBlockerRoute.OWNED
            blocker.follow_up_at = None
            append_blocker_event(
                state,
                blocker=blocker,
                event_type=TeamBlockerEventType.DELEGATION_STALLED,
                actor_agent_id=delegate_id,
                from_agent_id=delegate_id,
                to_agent_id=blocker.owner_agent_id,
                dispatch_id=dispatch_id,
                note="Delegated blocker dispatch completed without blocker-specific progress.",
                now=effective_now,
            )
            if blocker.owner_agent_id is not None:
                create_blocker_inbox(
                    state,
                    blocker=blocker,
                    recipient_agent_id=blocker.owner_agent_id,
                    sender_agent_id=delegate_id,
                    kind="blocker_delegation_stalled",
                    result=result,
                    now=effective_now,
                )
        elif blocker.owner_agent_id == agent_id:
            transfer_blocker_up(
                state,
                blocker=blocker,
                actor_agent_id=agent_id,
                dispatch_id=dispatch_id,
                reason="Owner dispatch completed without blocker-specific progress.",
                result=result,
                now=effective_now,
            )
        processed.append(blocker.id)
    if processed:
        result["processed_blocker_ids"] = processed
    return processed


def transfer_blocker_up(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    actor_agent_id: str | None,
    dispatch_id: str | None,
    reason: str,
    result: dict[str, Any] | None,
    now: datetime | None = None,
) -> None:
    effective_now = _ensure_utc(now or utc_now())
    current_owner_id = blocker.owner_agent_id
    current_owner = state.agents.get(current_owner_id or "")
    superior, skipped_agent_ids = (
        nearest_available_superior(state, current_owner)
        if current_owner is not None
        else (None, [])
    )
    if superior is None:
        route_blocker_terminal(
            state,
            blocker=blocker,
            actor_agent_id=actor_agent_id,
            dispatch_id=dispatch_id,
            reason=reason,
            result=result,
            now=effective_now,
            skipped_agent_ids=skipped_agent_ids,
        )
        return
    handle_blocker_inbox_for_agent(
        state,
        blocker_id=blocker.id,
        agent_id=current_owner_id or "",
        now=effective_now,
    )
    if blocker.delegate_agent_id is not None:
        handle_blocker_inbox_for_agent(
            state,
            blocker_id=blocker.id,
            agent_id=blocker.delegate_agent_id,
            now=effective_now,
        )
    blocker.owner_agent_id = superior.id
    blocker.delegate_agent_id = None
    blocker.route = TeamBlockerRoute.OWNED
    blocker.escalation_depth += 1
    blocker.active_dispatch_id = None
    blocker.follow_up_at = None
    blocker.updated_at = effective_now
    append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.OWNER_TRANSFERRED,
        actor_agent_id=actor_agent_id,
        from_agent_id=current_owner_id,
        to_agent_id=superior.id,
        dispatch_id=dispatch_id,
        note=reason,
        metadata={"skipped_agent_ids": skipped_agent_ids},
        now=effective_now,
    )
    create_blocker_inbox(
        state,
        blocker=blocker,
        recipient_agent_id=superior.id,
        sender_agent_id=current_owner_id,
        kind="blocker_escalated",
        result=result,
        now=effective_now,
    )


def route_blocker_terminal(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    actor_agent_id: str | None,
    dispatch_id: str | None,
    reason: str,
    result: dict[str, Any] | None,
    now: datetime | None = None,
    skipped_agent_ids: Sequence[str] | None = None,
) -> None:
    effective_now = _ensure_utc(now or utc_now())
    team = state.teams.get(blocker.team_id)
    if team is None:
        _mark_terminal_routing_failed(
            state,
            blocker=blocker,
            actor_agent_id=actor_agent_id,
            dispatch_id=dispatch_id,
            reason=f"{reason} Team {blocker.team_id} does not exist.",
            result=result,
            now=effective_now,
        )
        return
    if blocker.owner_agent_id is not None:
        handle_blocker_inbox_for_agent(
            state,
            blocker_id=blocker.id,
            agent_id=blocker.owner_agent_id,
            now=effective_now,
        )
    if blocker.delegate_agent_id is not None:
        handle_blocker_inbox_for_agent(
            state,
            blocker_id=blocker.id,
            agent_id=blocker.delegate_agent_id,
            now=effective_now,
        )
    blocker.delegate_agent_id = None
    blocker.active_dispatch_id = None
    blocker.follow_up_at = None
    blocker.escalation_depth += 1
    if team.escalate_to_governance:
        groups = _governance_groups_for_team(state, team.id)
        if not groups:
            _mark_terminal_routing_failed(
                state,
                blocker=blocker,
                actor_agent_id=actor_agent_id,
                dispatch_id=dispatch_id,
                reason=(
                    f"{reason} Team {team.id} is configured for governance escalation "
                    "but belongs to no governance group."
                ),
                result=result,
                now=effective_now,
            )
            return
        agenda_ids: list[str] = []
        for group in groups:
            agenda = GovernanceAgendaItem(
                id=f"governance-agenda-{uuid4().hex}",
                governance_group_id=group.id,
                item_type=GovernanceAgendaItemType.TEAM_ESCALATION,
                title=blocker.summary,
                summary=blocker.details or reason,
                team_id=team.id,
                project_id=blocker.project_id,
                agent_id=blocker.owner_agent_id,
                priority=blocker.priority,
                payload={
                    "blocker_id": blocker.id,
                    "source_kind": blocker.source_kind.value,
                    "source_id": blocker.source_id,
                    "reason": reason,
                    "escalation_depth": blocker.escalation_depth,
                },
                created_at=effective_now,
                updated_at=effective_now,
            )
            state.governance_agenda[agenda.id] = agenda
            runtime = state.governance_runtimes.setdefault(
                group.id,
                GovernanceRuntime(governance_group_id=group.id),
            )
            runtime.next_wake_at = None
            runtime.updated_at = effective_now
            agenda_ids.append(agenda.id)
        blocker.route = TeamBlockerRoute.GOVERNANCE
        blocker.terminal_reference_ids = agenda_ids
        blocker.updated_at = effective_now
        append_blocker_event(
            state,
            blocker=blocker,
            event_type=TeamBlockerEventType.TERMINAL_ROUTED,
            actor_agent_id=actor_agent_id,
            from_agent_id=blocker.owner_agent_id,
            dispatch_id=dispatch_id,
            note=reason,
            reference_ids=agenda_ids,
            metadata={
                "route": TeamBlockerRoute.GOVERNANCE.value,
                "skipped_agent_ids": list(skipped_agent_ids or []),
            },
            now=effective_now,
        )
        if result is not None:
            result.setdefault("governance_agenda_item_ids", []).extend(agenda_ids)
            for group in groups:
                result.setdefault("events", []).append(
                    {
                        "event_type": GOVERNANCE_WAKE_REQUESTED_EVENT_TYPE,
                        "correlation_key": (
                            f"{GOVERNANCE_WAKE_REQUESTED_EVENT_TYPE}:{group.id}"
                        ),
                        "idempotency_key": (
                            "team-addon:team.governance.wake_requested:"
                            f"{group.id}:blocker:{blocker.id}:{blocker.escalation_depth}"
                        ),
                        "payload": {
                            "governance_group_id": group.id,
                            "blocker_id": blocker.id,
                            "team_id": team.id,
                            "reason": "team_blocker",
                        },
                    }
                )
        return

    target = _optional_text(team.operator_communication_target_id)
    if target is None:
        _mark_terminal_routing_failed(
            state,
            blocker=blocker,
            actor_agent_id=actor_agent_id,
            dispatch_id=dispatch_id,
            reason=f"{reason} Team {team.id} has no operator escalation target.",
            result=result,
            now=effective_now,
        )
        return
    escalation_id = f"escalation-{uuid4().hex}"
    blocker.route = TeamBlockerRoute.OPERATOR
    blocker.terminal_reference_ids = [escalation_id]
    blocker.updated_at = effective_now
    append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.TERMINAL_ROUTED,
        actor_agent_id=actor_agent_id,
        from_agent_id=blocker.owner_agent_id,
        dispatch_id=dispatch_id,
        note=reason,
        reference_ids=[escalation_id],
        metadata={
            "route": TeamBlockerRoute.OPERATOR.value,
            "communication_target_id": target,
            "skipped_agent_ids": list(skipped_agent_ids or []),
        },
        now=effective_now,
    )
    if result is not None:
        result.setdefault("events", []).append(
            {
                "event_type": OPERATOR_ESCALATION_EVENT_TYPE,
                "correlation_key": escalation_id,
                "idempotency_key": (
                    f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:blocker:{blocker.id}:"
                    f"{blocker.escalation_depth}"
                ),
                "payload": {
                    "escalation_id": escalation_id,
                    "communication_target_id": target,
                    "team_id": team.id,
                    "manager_agent_id": team.manager_agent_id,
                    "title": blocker.summary,
                    "message": blocker.details or reason,
                    "urgency": blocker.priority.value,
                    "project_id": blocker.project_id,
                    "task_id": blocker.task_id,
                    "blocker_id": blocker.id,
                    "source_kind": blocker.source_kind.value,
                    "source_id": blocker.source_id,
                },
            }
        )
        result.setdefault("operator_escalation_event_ids", []).append(escalation_id)


def _mark_terminal_routing_failed(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    actor_agent_id: str | None,
    dispatch_id: str | None,
    reason: str,
    result: dict[str, Any] | None,
    now: datetime,
) -> None:
    blocker.route = TeamBlockerRoute.ROUTING_FAILED
    blocker.delegate_agent_id = None
    blocker.active_dispatch_id = None
    blocker.follow_up_at = None
    blocker.updated_at = now
    append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.ROUTING_FAILED,
        actor_agent_id=actor_agent_id,
        from_agent_id=blocker.owner_agent_id,
        dispatch_id=dispatch_id,
        note=reason,
        now=now,
    )
    if blocker.owner_agent_id is not None:
        create_blocker_inbox(
            state,
            blocker=blocker,
            recipient_agent_id=blocker.owner_agent_id,
            sender_agent_id=actor_agent_id,
            kind="blocker_routing_failed",
            result=result,
            now=now,
        )
    if result is not None:
        result.setdefault("errors", []).append(
            {"code": "blocker_terminal_routing_failed", "message": reason}
        )


def resolve_blocker(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    actor_agent_id: str | None,
    dispatch_id: str | None,
    note: str,
    reference_ids: Sequence[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> TeamBlockerEvent:
    effective_now = _ensure_utc(now or utc_now())
    assigned_agent_ids = {
        blocker.owner_agent_id,
        blocker.delegate_agent_id,
        actor_agent_id,
        *(
            item.recipient_agent_id
            for item in state.inbox.values()
            if item.payload.get("blocker_id") == blocker.id
        ),
    }
    terminal_reference_ids = list(blocker.terminal_reference_ids)
    for reference_id in reference_ids or []:
        if reference_id not in terminal_reference_ids:
            terminal_reference_ids.append(reference_id)
    blocker.terminal_reference_ids = terminal_reference_ids
    blocker.state = TeamBlockerState.RESOLVED
    blocker.route = TeamBlockerRoute.RESOLVED
    blocker.delegate_agent_id = None
    blocker.active_dispatch_id = None
    blocker.follow_up_at = None
    blocker.resolved_at = effective_now
    blocker.updated_at = effective_now
    if blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL:
        runtime = state.agent_runtimes.get(blocker.source_id)
        if runtime is not None:
            runtime.no_progress_count = 0
            runtime.updated_at = effective_now
    for reference_id in terminal_reference_ids:
        agenda = state.governance_agenda.get(reference_id)
        if agenda is not None and agenda.state == GovernanceAgendaItemState.PENDING:
            agenda.state = GovernanceAgendaItemState.HANDLED
            agenda.updated_at = effective_now
    for agent_id in assigned_agent_ids:
        if agent_id is not None:
            handle_blocker_inbox_for_agent(
                state,
                blocker_id=blocker.id,
                agent_id=agent_id,
                now=effective_now,
            )
    return append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.RESOLVED,
        actor_agent_id=actor_agent_id,
        dispatch_id=dispatch_id,
        note=note,
        reference_ids=reference_ids,
        metadata=metadata,
        now=effective_now,
    )


def resolve_active_blockers_for_source(
    state: TeamState,
    *,
    source_kind: TeamBlockerSourceKind | str,
    source_id: str,
    actor_agent_id: str | None,
    dispatch_id: str | None,
    note: str,
    reference_ids: Sequence[str] | None = None,
    result: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[str]:
    resolved_ids: list[str] = []
    for blocker in active_blockers_for_source(
        state,
        source_kind=source_kind,
        source_id=source_id,
    ):
        resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=actor_agent_id,
            dispatch_id=dispatch_id,
            note=note,
            reference_ids=reference_ids,
            now=now,
        )
        resolved_ids.append(blocker.id)
    if result is not None and resolved_ids:
        result.setdefault("resolved_blocker_ids", []).extend(resolved_ids)
    return resolved_ids


def task_cancellation_blocking_handoff_ids(
    state: TeamState,
    *,
    acceptance_ids: Sequence[str],
    review_request_ids: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Return handoffs that cancellation cannot close through blocker reconciliation."""

    # Direct acceptance has no terminal blocked decision of its own, so every
    # active acceptance handoff must close before cancellation.
    blocking_acceptance_ids = sorted(set(acceptance_ids))

    blocking_review_request_ids: list[str] = []
    for review_request_id in sorted(set(review_request_ids)):
        review = state.review_requests.get(review_request_id)
        reconcilable = (
            review is not None
            and review.deliverable_id is not None
            and review.state == TeamRecordState.BLOCKED
            and bool(
                active_blockers_for_source(
                    state,
                    source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                    source_id=review_request_id,
                )
            )
        )
        if not reconcilable:
            blocking_review_request_ids.append(review_request_id)

    return blocking_acceptance_ids, blocking_review_request_ids


def reconcile_task_blockers_after_cancellation(
    state: TeamState,
    *,
    task_id: str,
    actor_agent_id: str | None,
    dispatch_id: str | None,
    result: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Close local blockers and remove a cancelled task from shared incidents."""

    effective_now = _ensure_utc(now or utc_now())
    resolved_ids: list[str] = []
    for blocker in sorted(state.blockers.values(), key=lambda item: item.id):
        if blocker.state != TeamBlockerState.OPEN:
            continue
        local_link = task_id in _blocker_local_task_ids(state, blocker)
        shared_link = task_id in blocker.affected_task_ids
        if not local_link and not shared_link:
            continue

        if shared_link:
            blocker.affected_task_ids = [
                affected_id
                for affected_id in blocker.affected_task_ids
                if affected_id != task_id
            ]
            blocker.updated_at = effective_now
            _refresh_pending_blocker_inbox_payloads(
                state,
                blocker=blocker,
                now=effective_now,
            )

        # A local source with no remaining shared scope, or a shared incident
        # whose final affected task was cancelled, no longer has an active task
        # to block. A deliberately multi-task blocker remains open for the other
        # affected tasks.
        if blocker.affected_task_ids:
            continue
        if not local_link and blocker.source_kind not in {
            TeamBlockerSourceKind.AGENT_EXECUTION_STALL,
            TeamBlockerSourceKind.EXPLICIT,
        }:
            # Release and human-review blockers describe a gate that persists
            # independently of task membership. Removing their last shared task
            # must not bypass the source-specific recovery lifecycle.
            continue
        if local_link:
            _retire_cancelled_task_blocker_source(
                state,
                blocker=blocker,
                task_id=task_id,
                actor_agent_id=actor_agent_id,
                now=effective_now,
            )
        event = resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=actor_agent_id,
            dispatch_id=dispatch_id,
            note=(
                f"Task {task_id} was cancelled by an authorized disposition; "
                "the blocker no longer applies to any affected task."
            ),
            reference_ids=[task_id],
            metadata={
                "kind": "task_cancellation_reconciliation",
                "task_id": task_id,
                "source_linked": local_link,
                "shared_membership_removed": shared_link,
            },
            now=effective_now,
        )
        resolved_ids.append(blocker.id)
        _record_reconciled_blocker(
            result,
            blocker_id=blocker.id,
            event_id=event.id,
        )
    return resolved_ids


def reconcile_task_blockers_after_reassignment(
    state: TeamState,
    *,
    task_id: str,
    old_owner_agent_id: str,
    new_owner_agent_id: str,
    actor_agent_id: str | None,
    dispatch_id: str | None,
    result: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Retarget every local blocker and recompute shared stall membership."""

    effective_now = _ensure_utc(now or utc_now())
    task = state.tasks.get(task_id)
    new_owner = state.agents.get(new_owner_agent_id)
    if task is None or new_owner is None:
        return
    for blocker in sorted(state.blockers.values(), key=lambda item: item.id):
        if blocker.state != TeamBlockerState.OPEN:
            continue

        if blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL:
            membership_changed = False
            removed_from_old_stall = (
                blocker.source_id == old_owner_agent_id
                and task_id in blocker.affected_task_ids
            )
            added_to_new_stall = (
                blocker.source_id == new_owner_agent_id
                and task_id not in blocker.affected_task_ids
            )
            if removed_from_old_stall:
                blocker.affected_task_ids = [
                    affected_id
                    for affected_id in blocker.affected_task_ids
                    if affected_id != task_id
                ]
                membership_changed = True
            if added_to_new_stall:
                blocker.affected_task_ids = sorted(
                    {*blocker.affected_task_ids, task_id}
                )
                membership_changed = True
            if membership_changed:
                blocker.updated_at = effective_now
                _refresh_pending_blocker_inbox_payloads(
                    state,
                    blocker=blocker,
                    now=effective_now,
                )
            if removed_from_old_stall and not blocker.affected_task_ids:
                event = resolve_blocker(
                    state,
                    blocker=blocker,
                    actor_agent_id=actor_agent_id,
                    dispatch_id=dispatch_id,
                    note="The stalled agent no longer owns any affected task.",
                    reference_ids=[task_id],
                    metadata={
                        "kind": "task_reassignment_reconciliation",
                        "old_owner_agent_id": old_owner_agent_id,
                        "new_owner_agent_id": new_owner_agent_id,
                    },
                    now=effective_now,
                )
                _record_reconciled_blocker(
                    result,
                    blocker_id=blocker.id,
                    event_id=event.id,
                )
            elif membership_changed:
                event = append_blocker_event(
                    state,
                    blocker=blocker,
                    event_type=TeamBlockerEventType.PROGRESS,
                    actor_agent_id=actor_agent_id,
                    dispatch_id=dispatch_id,
                    note=(
                        f"Task {task_id} execution-stall membership was recomputed "
                        "after reassignment."
                    ),
                    reference_ids=[task_id],
                    metadata={
                        "kind": "affected_task_membership_changed",
                        "old_owner_agent_id": old_owner_agent_id,
                        "new_owner_agent_id": new_owner_agent_id,
                        "affected_task_ids": list(blocker.affected_task_ids),
                    },
                    now=effective_now,
                )
                _record_reconciled_event(result, event.id)
            continue

        if task_id not in _blocker_local_task_ids(state, blocker):
            continue

        previous_owner_id = blocker.owner_agent_id
        pending_recipient_ids = {
            item.recipient_agent_id
            for item in state.inbox.values()
            if item.state == TeamInboxItemState.PENDING
            and item.payload.get("blocker_id") == blocker.id
        }
        for assigned_id in pending_recipient_ids:
            handle_blocker_inbox_for_agent(
                state,
                blocker_id=blocker.id,
                agent_id=assigned_id,
                now=effective_now,
            )
        for reference_id in blocker.terminal_reference_ids:
            agenda = state.governance_agenda.get(reference_id)
            if agenda is not None and agenda.state == GovernanceAgendaItemState.PENDING:
                agenda.state = GovernanceAgendaItemState.HANDLED
                agenda.updated_at = effective_now

        blocker.delegate_agent_id = None
        blocker.active_dispatch_id = None
        blocker.follow_up_at = None
        blocker.terminal_reference_ids = []
        blocker.escalation_depth = 0
        parent_task = parent_task_for_child(state, task)
        if (
            parent_task is not None
            and parent_task.owner_agent_id != new_owner.id
            and parent_task.owner_agent_id != blocker.reporter_agent_id
        ):
            next_owner, skipped = nearest_available_agent(
                state,
                start_agent_id=parent_task.owner_agent_id,
                team_id=blocker.team_id,
            )
        else:
            next_owner, skipped = nearest_available_superior(state, new_owner)
        blocker.owner_agent_id = next_owner.id if next_owner is not None else None
        blocker.route = (
            TeamBlockerRoute.OWNED
            if next_owner is not None
            else TeamBlockerRoute.ROUTING_FAILED
        )
        blocker.updated_at = effective_now
        event = append_blocker_event(
            state,
            blocker=blocker,
            event_type=TeamBlockerEventType.OWNER_TRANSFERRED,
            actor_agent_id=actor_agent_id,
            from_agent_id=previous_owner_id,
            to_agent_id=next_owner.id if next_owner is not None else None,
            dispatch_id=dispatch_id,
            note=(
                f"Task {task_id} was reassigned from {old_owner_agent_id} "
                f"to {new_owner_agent_id}; its blocker route was recomputed."
            ),
            metadata={
                "skipped_agent_ids": skipped,
                "old_task_owner_agent_id": old_owner_agent_id,
                "new_task_owner_agent_id": new_owner_agent_id,
            },
            now=effective_now,
        )
        _record_reconciled_event(result, event.id)
        if next_owner is not None:
            create_blocker_inbox(
                state,
                blocker=blocker,
                recipient_agent_id=next_owner.id,
                sender_agent_id=actor_agent_id,
                kind="blocker_owner_transferred",
                result=result,
                now=effective_now,
            )


def _blocker_local_task_ids(
    state: TeamState,
    blocker: TeamBlocker,
) -> set[str]:
    task_ids: set[str] = set()
    if blocker.source_kind == TeamBlockerSourceKind.TASK:
        task_ids.add(blocker.source_id)
        if blocker.task_id is not None:
            task_ids.add(blocker.task_id)
    elif blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
        deliverable = state.deliverables.get(blocker.source_id)
        if deliverable is not None:
            task_ids.add(deliverable.task_id)
            if blocker.task_id is not None:
                task_ids.add(blocker.task_id)
    elif blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
        review = state.review_requests.get(blocker.source_id)
        deliverable = (
            state.deliverables.get(review.deliverable_id or "")
            if review is not None
            else None
        )
        if deliverable is not None:
            task_ids.add(deliverable.task_id)
            if blocker.task_id is not None:
                task_ids.add(blocker.task_id)
    elif (
        blocker.source_kind == TeamBlockerSourceKind.EXPLICIT
        and blocker.task_id is not None
    ):
        task_ids.add(blocker.task_id)
    return task_ids


def _retire_cancelled_task_blocker_source(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    task_id: str,
    actor_agent_id: str | None,
    now: datetime,
) -> None:
    if blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
        deliverable = state.deliverables.get(blocker.source_id)
        if deliverable is not None and deliverable.state in {
            TeamRecordState.PENDING,
            TeamRecordState.IN_PROGRESS,
            TeamRecordState.BLOCKED,
        }:
            deliverable.state = TeamRecordState.REJECTED
        return
    if blocker.source_kind != TeamBlockerSourceKind.REVIEW_REQUEST:
        return
    review = state.review_requests.get(blocker.source_id)
    if review is None or review.deliverable_id is None:
        return
    if review.retired_at is None and review.superseded_by_review_request_id is None:
        cancellation = state.tasks.get(task_id)
        cancellation_actor = (
            cancellation.cancellation
            if cancellation is not None
            else None
        )
        review.retired_at = now
        review.retired_by = (
            f"agent:{actor_agent_id}"
            if actor_agent_id is not None
            else (
                f"{cancellation_actor.actor_kind}:{cancellation_actor.actor_id}"
                if cancellation_actor is not None
                else "operator:task-cancellation"
            )
        )
        review.retirement_reason = (
            f"Task {task_id} was cancelled by an "
            "authorized disposition."
        )
        for item in state.inbox.values():
            if (
                item.state == TeamInboxItemState.PENDING
                and item.payload.get("review_request_id") == review.id
            ):
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = now
    deliverable = state.deliverables.get(review.deliverable_id)
    if deliverable is not None and deliverable.state in {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }:
        deliverable.state = TeamRecordState.REJECTED


def _refresh_pending_blocker_inbox_payloads(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    now: datetime,
) -> None:
    for item in state.inbox.values():
        if (
            item.state != TeamInboxItemState.PENDING
            or item.payload.get("blocker_id") != blocker.id
        ):
            continue
        item.payload["affected_task_ids"] = list(blocker.affected_task_ids)
        item.payload["owner_agent_id"] = blocker.owner_agent_id
        item.payload["delegate_agent_id"] = blocker.delegate_agent_id
        item.payload["route"] = blocker.route.value
        item.payload["escalation_depth"] = blocker.escalation_depth
        item.updated_at = now


def _record_reconciled_blocker(
    result: dict[str, Any] | None,
    *,
    blocker_id: str,
    event_id: str,
) -> None:
    if result is None:
        return
    resolved_ids = result.setdefault("resolved_blocker_ids", [])
    if blocker_id not in resolved_ids:
        resolved_ids.append(blocker_id)
    _record_reconciled_event(result, event_id)


def _record_reconciled_event(
    result: dict[str, Any] | None,
    event_id: str,
) -> None:
    if result is None:
        return
    event_ids = result.setdefault("blocker_event_ids", [])
    if event_id not in event_ids:
        event_ids.append(event_id)


def active_blockers_for_source(
    state: TeamState,
    *,
    source_kind: TeamBlockerSourceKind | str,
    source_id: str,
) -> list[TeamBlocker]:
    normalized_kind = (
        source_kind
        if isinstance(source_kind, TeamBlockerSourceKind)
        else TeamBlockerSourceKind(str(source_kind))
    )
    return [
        blocker
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and blocker.source_kind == normalized_kind
        and blocker.source_id == source_id
    ]


def open_release_blocker(
    state: TeamState,
    *,
    release_package_id: str,
    summary: str,
    details: str,
    result: dict[str, Any] | None,
    reporter_agent_id: str | None = None,
    initial_owner_agent_id: str | None = None,
    route_initial_owner_even_if_unavailable: bool = False,
    wake_owner: bool = True,
    now: datetime | None = None,
) -> tuple[TeamBlocker, bool]:
    release = state.release_packages.get(release_package_id)
    if release is None:
        raise ValueError(f"release package {release_package_id} does not exist")
    project = state.projects.get(release.project_id)
    if project is None:
        raise ValueError(f"project {release.project_id} does not exist")
    return open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=release.id,
        summary=summary,
        details=details,
        reporter_agent_id=reporter_agent_id,
        initial_owner_agent_id=(
            initial_owner_agent_id
            if initial_owner_agent_id is not None
            else (project.lead_agent_id if reporter_agent_id is None else None)
        ),
        route_initial_owner_even_if_unavailable=(
            route_initial_owner_even_if_unavailable
        ),
        wake_owner=wake_owner,
        team_id=project.team_id,
        project_id=project.id,
        result=result,
        now=now,
    )


def open_deliverable_blocker(
    state: TeamState,
    *,
    deliverable_id: str,
    summary: str,
    details: str,
    result: dict[str, Any] | None,
    reporter_agent_id: str | None = None,
    now: datetime | None = None,
) -> tuple[TeamBlocker, bool]:
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        raise ValueError(f"deliverable {deliverable_id} does not exist")
    task = state.tasks.get(deliverable.task_id)
    if task is None:
        raise ValueError(f"task {deliverable.task_id} does not exist")
    project = state.projects.get(task.project_id)
    if project is None:
        raise ValueError(f"project {task.project_id} does not exist")
    return open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.DELIVERABLE,
        source_id=deliverable.id,
        summary=summary,
        details=details,
        reporter_agent_id=reporter_agent_id or deliverable.producer_agent_id,
        team_id=project.team_id,
        project_id=project.id,
        task_id=task.id,
        result=result,
        now=now,
    )


__all__ = [
    "BLOCKER_PROGRESS_EVENT_TYPES",
    "GOVERNANCE_WAKE_REQUESTED_EVENT_TYPE",
    "active_blockers_for_source",
    "agent_can_resolve_blocker",
    "append_blocker_event",
    "assign_blocker_owner",
    "assign_blockers_to_dispatch",
    "blocker_resolution_line_agent_ids",
    "blocker_events_for_dispatch",
    "blockers_assigned_to_agent",
    "blockers_due_for_agent",
    "complete_blocker_dispatches",
    "create_blocker_inbox",
    "handle_blocker_inbox_for_agent",
    "nearest_available_agent",
    "nearest_available_superior",
    "open_or_update_agent_execution_stall",
    "open_or_get_blocker",
    "open_deliverable_blocker",
    "open_release_blocker",
    "reconcile_task_blockers_after_cancellation",
    "reconcile_task_blockers_after_reassignment",
    "resolve_blocker",
    "route_blocker_terminal",
    "task_cancellation_blocking_handoff_ids",
    "transfer_blocker_up",
]
