from __future__ import annotations

import json
from contextlib import nullcontext

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .domain.role_task_queue import (
    plan_role_task_allocations,
    set_unstarted_role_task_owner,
)
from .case_context_dispatch import prepare_case_context, dispatch_case_contexts
from ..repository import (
    TeamStateConflictError,
)
from ..lifecycle_sql import DISPATCH_ATTEMPT_ORDER, DISPATCH_COMPLETED_ORDER
from ..sqlite_store import (
    TeamStateSqliteCommandTransaction,
)
from ..models import (
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentRuntime,
    AgentTask,
    ReviewRequest,
    AgentStatus,
    PersistentAgent,
    TaskAssessmentReadiness,
    TeamBlocker,
    TeamDispatch,
    TeamDispatchActivityStatus,
    TeamDispatchActivity,
    TeamDispatchMcpCapabilityReceipt,
    TeamDispatchMcpCapabilityStatus,
    TeamDispatchStatus,
    TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS,
    TeamInboxPriority,
    TeamState,
    Team,
    TeamDispatchReviewRoutingAuthority,
    agent_has_project_assignment,
    external_source_evidence_snapshot_payload,
    wake_work_changed_since_latest_attempt,
    wake_work_fingerprint,
    wake_work_record_fingerprints,
)
from .domain.action_feedback_payloads import _latest_action_feedback_for_agent
from .domain.task_assessment import task_payload_with_assessment
from .domain.task_assessment import TaskAssessmentService
from .domain.agent_access import (
    _agent_payload,
    _subordinate_agent_payloads,
    _team_ids_with_open_work,
    agent_role_description_fingerprint,
)
from .domain.chat import (
    _clear_completed_chat_dispatch_lease,
    _operator_chat_participant_payloads,
    _requeue_unacknowledged_in_dispatch_messages,
)
from .domain.blockers import (
    assign_blockers_to_dispatch,
    blockers_assigned_to_agent,
    blockers_due_for_agent,
    complete_blocker_dispatches,
    open_or_update_agent_execution_stall,
    reconcile_unavailable_blocker_assignments,
)
from .domain.common import (
    _ensure_utc,
    _optional_text,
)
from .domain.context_rotation import (
    _mark_context_rotation_pending,
    _maybe_mark_idle_context_rotation_due,
)
from .domain.dispatch_scope import (
    AgentDispatchScope,
    actionable_dispatch_reviews,
    actionable_dispatch_tasks,
    build_agent_dispatch_scope,
    build_preparation_recovery_dispatch_scope,
    external_source_evidence_snapshot_ids_for_dispatch,
    inbox_references_review_ids,
    mcp_capability_blocked_review_ids,
    reconcile_stale_inbox_preparation_blockers,
)
from .domain.git_work_contexts import (
    finalize_active_git_work_contexts_for_dispatch,
    reconcile_git_work_context_lifecycle,
)
from .read_service import TeamReadService
from .domain.manager_fallback import (
    _manager_fallback_review_routing_authorities,
    _manager_fallback_initial_release_decision_ids,
    _manager_fallback_summary_payload,
    _manager_fallback_summary_has_actionable_work,
    manager_fallback_work_fingerprint,
    manager_fallback_work_record_fingerprints,
)
from .domain.project_status import project_blocking_sources
from .domain.outcome_catalog import effective_outcome_kind_payloads
from .domain.preparation_failures import (
    EXECUTION_PREPARATION_BLOCKER_REASON_CODES,
    reconcile_stale_task_preparation_blockers,
)
from .domain.stand_down import (
    _active_stand_downs_for_agent,
)
from .domain.wake import (
    _active_tasks_for_agent,
    _dispatch_case_payloads,
    _ordinary_wake_work_for_agent,
    _pending_inbox_for_agent,
    _pending_reviews_for_agent,
    _release_package_payloads_for_agent,
    _runtime_attention_payloads_for_agent,
    _wakeable_inbox_items,
)
from .domain.work_due import (
    _advance_low_priority_batch,
    _clear_low_priority_batch,
)
from .domain.work_lineage import work_lineage_id_for_record
from .commands.outbox import _team_event_outbox_records
from .dispatch_planning import (
    TEAM_DISPATCH_PREPARED_COMMIT_MAX_ATTEMPTS,
    team_dispatch_planning_lock,
)


GIT_WORK_CONTEXT_RETENTION_DAYS = 30
REVIEW_DECISION_MCP_TOOL_NAME = "team_decide_review"
MAX_REVIEW_MCP_CAPABILITY_ATTEMPTS = 2
TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS = TEAM_DISPATCH_PREPARED_COMMIT_MAX_ATTEMPTS


def _reconcile_leasing_state(
    state_model: TeamState,
    *,
    effective_now: datetime,
    normalized_team_id: str | None,
    normalized_agent_id: str | None,
    blocked_teams: set[str],
    blocker_routing_events: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    """Reconcile prior dispatches and context rotation before selecting new work."""
    reconcile_git_work_context_lifecycle(
        state_model,
        now=effective_now,
        prune_before=effective_now - timedelta(days=GIT_WORK_CONTEXT_RETENTION_DAYS),
    )
    from .domain.git_recovery_continuation import reconcile_git_recovery_successors

    recovery_result: dict[str, Any] = {"events": []}
    reconcile_git_recovery_successors(state_model, result=recovery_result, now=effective_now)
    blocker_routing_events.extend(recovery_result["events"])
    stale_preparation_result: dict[str, Any] = {
        "events": [],
        "errors": [],
    }
    reconcile_stale_task_preparation_blockers(
        state_model,
        result=stale_preparation_result,
        now=effective_now,
    )
    reconcile_stale_inbox_preparation_blockers(
        state_model,
        result=stale_preparation_result,
        now=effective_now,
    )
    blocker_routing_events.extend(stale_preparation_result.get("events") or [])
    availability_result: dict[str, Any] = {"events": [], "errors": []}
    reconcile_unavailable_blocker_assignments(
        state_model,
        result=availability_result,
        now=effective_now,
    )
    blocker_routing_events.extend(availability_result.get("events") or [])
    teams_with_open_work = _team_ids_with_open_work(state_model)
    teams_with_lease = set()
    idle_rotation_result: dict[str, Any] = {"events": []}
    for agent in sorted(state_model.agents.values(), key=lambda item: item.id):
        runtime = state_model.agent_runtimes.get(agent.id)
        if runtime is not None:
            preparing_dispatch = state_model.dispatches.get(
                _optional_text(runtime.active_dispatch_id) or ""
            )
            if (
                preparing_dispatch is not None
                and preparing_dispatch.cause_kind != "team_chat"
                and preparing_dispatch.status == TeamDispatchStatus.PREPARING
                and preparing_dispatch.preparation_expires_at is not None
                and _ensure_utc(preparing_dispatch.preparation_expires_at) <= effective_now
            ):
                _fail_dispatch_preparation_in_state(
                    state_model,
                    dispatch_id=preparing_dispatch.id,
                    error=(
                        "dispatch preparation reservation expired before durable prompt delivery"
                    ),
                    completed_at=effective_now,
                )
            _clear_completed_chat_dispatch_lease(
                state_model,
                runtime,
                now=effective_now,
            )
            if (
                runtime.active_dispatch_id is not None
                and runtime.wake_leased_until is not None
                and _ensure_utc(runtime.wake_leased_until) <= effective_now
            ):
                expired_dispatch_id = runtime.active_dispatch_id
                durable_dispatch = state_model.dispatches.get(expired_dispatch_id)
                if (
                    durable_dispatch is not None
                    and durable_dispatch.status == TeamDispatchStatus.PREPARING
                ):
                    _release_blocker_dispatch_reservations(
                        state_model,
                        dispatch_id=expired_dispatch_id,
                        now=effective_now,
                    )
                else:
                    expiry_result: dict[str, Any] = {
                        "events": [],
                        "errors": [],
                    }
                    processed_blockers = complete_blocker_dispatches(
                        state_model,
                        agent_id=agent.id,
                        dispatch_id=expired_dispatch_id,
                        result=expiry_result,
                        now=effective_now,
                    )
                    blocker_routing_events.extend(expiry_result.get("events") or [])
                    if processed_blockers:
                        runtime.no_progress_count += 1
                finalize_active_git_work_contexts_for_dispatch(
                    state_model,
                    agent_id=agent.id,
                    dispatch_id=expired_dispatch_id,
                    now=effective_now,
                )
                _mark_dispatch_terminal(
                    state_model,
                    dispatch_id=expired_dispatch_id,
                    status=TeamDispatchStatus.EXPIRED,
                    completed_at=effective_now,
                )
                runtime.active_dispatch_id = None
                runtime.wake_leased_until = None
                runtime.wake_reason = None
                if (
                    durable_dispatch is not None
                    and durable_dispatch.cause_kind == "context_handoff"
                ):
                    runtime.context_handoff_preparation_pending = False
                    runtime.context_handoff_preparation_status = "timed_out"
                    runtime.next_wake_at = effective_now
                runtime.updated_at = effective_now
        if normalized_agent_id is None or agent.id == normalized_agent_id:
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=agent.id,
                now=effective_now,
                result=idle_rotation_result,
            )
        runtime = state_model.agent_runtimes.get(agent.id)
        if agent.status != AgentStatus.ACTIVE or agent.team_id is None:
            continue
        if normalized_team_id is not None and agent.team_id != normalized_team_id:
            continue
        if agent.team_id in blocked_teams:
            continue
        if _active_stand_downs_for_agent(state_model, agent.id):
            continue
        if runtime is not None and (
            _runtime_has_active_dispatch_reservation(state_model, runtime)
            or (
                runtime.wake_leased_until is not None
                and _ensure_utc(runtime.wake_leased_until) > effective_now
            )
        ):
            teams_with_lease.add(agent.team_id)
    blocker_routing_events.extend(idle_rotation_result.get("events") or [])
    return teams_with_open_work, teams_with_lease


def lease_due_agents(
    service: Any,
    *,
    effective_now: datetime,
    limit: int = 10,
    lease_seconds: int = 900,
    wake_interval_seconds: int = 900,
    max_no_progress_count: int = 3,
    low_priority_wake_after_cycles: int = 4,
    respect_wake_interval: bool = True,
    team_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    blocked_team_ids: set[str] | None = None,
    session_is_busy: Callable[[str], bool] | None = None,
    defer_activation: bool = False,
) -> tuple[TeamState, list[dict[str, Any]]]:
    normalized_team_id = _optional_text(team_id)
    normalized_project_id = _optional_text(project_id)
    normalized_agent_id = _optional_text(agent_id)
    normalized_limit = max(0, int(limit))
    low_priority_threshold = max(0, int(low_priority_wake_after_cycles))
    blocked_teams = set(blocked_team_ids or set())
    leases: list[dict[str, Any]] = []
    blocker_routing_events: list[dict[str, Any]] = []
    if normalized_limit == 0:
        return service._repository.read(), leases

    planner = _LeasePlanning(
        service=service,
        effective_now=effective_now,
        team_id=normalized_team_id,
        project_id=normalized_project_id,
        agent_id=normalized_agent_id,
        limit=normalized_limit,
        low_priority_threshold=low_priority_threshold,
        blocked_teams=blocked_teams,
        leases=leases,
        blocker_routing_events=blocker_routing_events,
        session_is_busy=session_is_busy,
        max_no_progress_count=max_no_progress_count,
        respect_wake_interval=respect_wake_interval,
        lease_seconds=lease_seconds,
        wake_interval_seconds=wake_interval_seconds,
        defer_activation=defer_activation,
    )

    def _commit_plan(planning_state):
        previous_payload = planning_state.model_dump(mode="json")
        candidate = planner.plan(planning_state)
        outbox_records = _team_event_outbox_records(blocker_routing_events)
        activity_records = []
        for lease in leases:
            lease_agent = lease["agent"]
            activity_records.append(
                TeamDispatchActivity(
                    id=f"activity-{uuid4().hex}",
                    tool_name="team_dispatch_started",
                    caller_session_id=lease_agent["session_id"],
                    caller_identity=f"agent:{lease_agent['id']}",
                    agent_id=lease_agent["id"],
                    dispatch_id=lease["dispatch_id"],
                    status=TeamDispatchActivityStatus.SUCCESS,
                    progress_classification="none",
                    created_at=effective_now,
                    updated_at=effective_now,
                )
            )

        def _write_commands(tx: TeamStateSqliteCommandTransaction) -> None:
            for record in outbox_records:
                tx.enqueue_outbox_record(record)
            for activity in activity_records:
                tx.append_dispatch_activity(activity)

        return service._repository.commit_prepared_state_with_command_transaction(
            previous_payload=previous_payload, candidate=candidate, command_writer=_write_commands,
        )

    def _plan_and_commit() -> TeamState:
        last_conflict: TeamStateConflictError | None = None
        for _attempt in range(TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS):
            leases.clear()
            blocker_routing_events.clear()
            try:
                snapshot = getattr(service._repository, "command_snapshot", None)
                context = snapshot() if callable(snapshot) else nullcontext(service._repository.read())
                with context as planning_state:
                    return _commit_plan(planning_state)
            except TeamStateConflictError as exc:
                last_conflict = exc
        assert last_conflict is not None
        raise last_conflict

    with team_dispatch_planning_lock(service._repository.store.path):
        state = _plan_and_commit()
    if not defer_activation:
        for lease in leases:
            state = service.mark_agent_dispatch_leased(
                agent_id=lease["agent"]["id"],
                dispatch_id=lease["dispatch_id"],
                lease_seconds=lease_seconds,
                now=effective_now,
            )
            lease["runtime"] = state.agent_runtimes[lease["agent"]["id"]].model_dump(mode="json")
            lease["dispatch"] = state.dispatches[lease["dispatch_id"]].model_dump(mode="json")
    return state, leases


@dataclass
class _LeasePlanning:
    """Plan ordinary leases, reconcile role allocations, then plan manager fallback."""

    service: Any
    effective_now: datetime
    team_id: str | None
    project_id: str | None
    agent_id: str | None
    limit: int
    low_priority_threshold: int
    blocked_teams: set[str]
    leases: list[dict[str, Any]]
    blocker_routing_events: list[dict[str, Any]]
    session_is_busy: Callable[[str], bool] | None
    max_no_progress_count: int
    respect_wake_interval: bool
    lease_seconds: int
    wake_interval_seconds: int
    defer_activation: bool

    def plan(self, state_model: TeamState) -> TeamState:
        teams_with_open_work, teams_with_lease = _reconcile_leasing_state(
            state_model,
            effective_now=self.effective_now,
            normalized_team_id=self.team_id,
            normalized_agent_id=self.agent_id,
            blocked_teams=self.blocked_teams,
            blocker_routing_events=self.blocker_routing_events,
        )
        role_allocations = plan_role_task_allocations(
            state_model,
            now=self.effective_now,
            limit=self.limit,
            team_id=self.team_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            blocked_team_ids=self.blocked_teams,
            max_no_progress_count=self.max_no_progress_count,
            session_is_busy=self.session_is_busy,
            respect_wake_interval=self.respect_wake_interval,
        )
        allocation_order = {
            row["selected_owner_agent_id"]: index
            for index, row in enumerate(role_allocations.values())
        }
        for agent in sorted(
            state_model.agents.values(),
            key=lambda item: (allocation_order.get(item.id, len(allocation_order)), item.id),
        ):
            if self.limit and len(self.leases) >= self.limit:
                break
            candidate = _OrdinaryLeaseCandidate(
                planning=self,
                state=state_model,
                agent=agent,
                role_allocations=role_allocations,
            )
            if candidate.plan() and agent.team_id is not None:
                teams_with_lease.add(agent.team_id)

        self._finalize_role_allocations(state_model, role_allocations)
        self._plan_manager_fallback(state_model, teams_with_open_work, teams_with_lease)
        return state_model

    def _finalize_role_allocations(
        self,
        state_model: TeamState,
        role_allocations: dict[str, dict[str, Any]],
    ) -> None:
        for task_id, allocation in role_allocations.items():
            assignment_lease = next(
                (
                    lease
                    for lease in self.leases
                    if lease["agent"]["id"] == allocation["selected_owner_agent_id"]
                    and task_id in lease["dispatch"]["task_ids"]
                ),
                None,
            )
            if assignment_lease is None:
                set_unstarted_role_task_owner(
                    state_model,
                    task=state_model.tasks[task_id],
                    owner_agent_id=None,
                )
                continue
            allocation["dispatch_id"] = assignment_lease["dispatch_id"]
            assignment_lease.setdefault("role_task_allocations", []).append(allocation)
            self.blocker_routing_events.append(
                {
                    "event_type": "team.task.role_assigned",
                    "correlation_key": task_id,
                    "idempotency_key": f"role-assignment:{assignment_lease['dispatch_id']}:{task_id}",
                    "payload": allocation,
                }
            )

    def _plan_manager_fallback(
        self,
        state_model: TeamState,
        teams_with_open_work: set[str],
        teams_with_lease: set[str],
    ) -> None:
        """Lease managers after ordinary dispatches and role allocations."""
        for team in sorted(state_model.teams.values(), key=lambda item: item.id):
            if self.limit and len(self.leases) >= self.limit:
                break
            _ManagerFallbackCandidate(
                planner=self,
                state=state_model,
                team=team,
                teams_with_open_work=teams_with_open_work,
                teams_with_lease=teams_with_lease,
            ).lease()


@dataclass(frozen=True)
class _ManagerFallbackWork:
    lineage_id: str | None
    tasks: list[AgentTask]
    inbox: list[AgentInboxItem]
    reviews: list[ReviewRequest]
    blockers: list[TeamBlocker]
    scope_blockers: list[TeamBlocker]
    summary: dict[str, Any]
    fingerprint: str
    record_fingerprints: dict[str, str]


class _ManagerFallbackCandidate:
    """Select and reserve one manager's fallback work in the current planning attempt."""

    manager: PersistentAgent
    runtime: AgentRuntime
    dispatch_id: str
    tasks: list[AgentTask]
    inbox: list[AgentInboxItem]
    reviews: list[ReviewRequest]
    blockers: list[TeamBlocker]
    scope_blockers: list[TeamBlocker]
    active_task_ids: set[str]
    blocked_review_ids: set[str]
    low_priority_inbox_ids: set[str]
    owned_review_ids: set[str]
    owned_inbox_ids: set[str]
    owned_action_ids: set[str]
    selection_summary: dict[str, Any]
    filter_work_lineage: bool
    work: _ManagerFallbackWork
    routing_authorities: list[TeamDispatchReviewRoutingAuthority]
    initial_release_decision_ids: list[str]
    supervision_task_ids: list[str]
    reservation_scope: AgentDispatchScope
    scope: AgentDispatchScope
    scoped_task_ids: list[str]
    scoped_project_ids: list[str]
    scoped_managed_repo_ids: list[str]
    source_evidence_snapshot_ids: list[str]
    has_prior_successful_wake: bool
    dispatch: TeamDispatch

    def __init__(
        self,
        *,
        planner: _LeasePlanning,
        state: TeamState,
        team: Team,
        teams_with_open_work: set[str],
        teams_with_lease: set[str],
    ) -> None:
        self.planner = planner
        self.state = state
        self.team = team
        self.teams_with_open_work = teams_with_open_work
        self.teams_with_lease = teams_with_lease

    def lease(self) -> None:
        if not self._eligible():
            return
        self._load_owned_work()
        if not self._select_work():
            return
        self._resolve_authorities()
        self._build_scope()
        self._reserve_dispatch()
        self._update_runtime()
        self._append_lease()

    def _eligible(self) -> bool:
        if (
            self.team.archived
            or self.team.id in self.planner.blocked_teams
            or self.team.id not in self.teams_with_open_work
        ):
            return False
        if self.planner.team_id is not None and self.team.id != self.planner.team_id:
            return False
        if self.team.id in self.teams_with_lease:
            return False
        self.manager = self.state.agents.get(self.team.manager_agent_id)
        if (
            self.manager is None
            or self.manager.status != AgentStatus.ACTIVE
            or (self.manager.id is None)
        ):
            return False
        if self.planner.project_id is not None and not agent_has_project_assignment(
            self.state,
            self.manager.id,
            self.planner.project_id,
        ):
            return False
        if self.planner.agent_id is not None and self.manager.id != self.planner.agent_id:
            return False
        if self.planner.session_is_busy is not None and self.planner.session_is_busy(
            self.manager.session_id
        ):
            return False
        if (
            self.manager.id in self.state.agent_runtimes
            and self.state.agent_runtimes[self.manager.id].wake_leased_until
            and _ensure_utc(self.state.agent_runtimes[self.manager.id].wake_leased_until)
            > self.planner.effective_now
        ):
            return False
        if _active_stand_downs_for_agent(self.state, self.manager.id):
            return False

        return True

    def _load_owned_work(self) -> None:
        self.runtime = self.state.agent_runtimes.setdefault(
            self.manager.id,
            AgentRuntime(agent_id=self.manager.id),
        )
        self.dispatch_id = f"team-dispatch-{uuid4().hex}"
        self.tasks = actionable_dispatch_tasks(
            self.state,
            agent=self.manager,
        )
        self.active_task_ids = {
            task.id for task in _active_tasks_for_agent(self.state, self.manager.id)
        }
        self.inbox = _pending_inbox_for_agent(
            self.state,
            self.manager.id,
        )
        self.reviews = actionable_dispatch_reviews(
            _pending_reviews_for_agent(self.state, self.manager.id)
        )
        self.blocked_review_ids = mcp_capability_blocked_review_ids(
            self.state,
            agent_id=self.manager.id,
        )
        if self.blocked_review_ids:
            self.inbox = [
                item
                for item in self.inbox
                if not inbox_references_review_ids(
                    item,
                    self.blocked_review_ids,
                )
            ]
            self.reviews = [
                review for review in self.reviews if review.id not in self.blocked_review_ids
            ]
        self.low_priority_inbox_ids = {
            item.id for item in self.inbox if item.priority == TeamInboxPriority.LOW
        }
        self.blockers = blockers_due_for_agent(
            self.state,
            agent_id=self.manager.id,
            now=self.planner.effective_now,
        )
        self.scope_blockers = blockers_assigned_to_agent(
            self.state,
            agent_id=self.manager.id,
        )
        self.owned_review_ids = {review.id for review in self.reviews}
        self.owned_inbox_ids = {item.id for item in self.inbox}
        self.owned_action_ids = {
            record.id
            for record in self.state.action_records.values()
            if record.agent_id == self.manager.id
        }
        self.selection_summary = _manager_fallback_summary_payload(
            self.state,
            team=self.team,
            manager=self.manager,
            now=self.planner.effective_now,
            limit_rows=False,
            excluded_review_request_ids=(set(self.blocked_review_ids) | self.owned_review_ids),
            excluded_inbox_item_ids=self.owned_inbox_ids,
            excluded_task_ids=self.active_task_ids,
            excluded_action_record_ids=self.owned_action_ids,
        )

    def _select_work(self) -> bool:
        if not _manager_fallback_summary_has_actionable_work(self.selection_summary):
            return False
        self.filter_work_lineage = (
            self.runtime.context_clearing_policy == AgentContextClearingPolicy.WORK_LINEAGE
        )
        candidate_lineage_ids = (
            _manager_fallback_lineage_ids(
                self.state,
                self.selection_summary,
            )
            if self.filter_work_lineage
            else [None]
        )
        for lineage_id in candidate_lineage_ids:
            selected_work = _ManagerFallbackLineageCandidate(
                candidate=self,
                lineage_id=lineage_id,
            ).select()
            if selected_work is not None:
                self.work = selected_work
                return True
        return False

    def _resolve_authorities(self) -> None:
        self.routing_authorities = _manager_fallback_review_routing_authorities(
            self.state,
            summary=self.work.summary,
        )
        self.initial_release_decision_ids = _manager_fallback_initial_release_decision_ids(
            self.work.summary
        )
        self.supervision_task_ids = _manager_fallback_supervision_task_ids(
            self.state,
            manager=self.manager,
            summary=self.work.summary,
        )

    def _build_scope(self) -> None:
        self.reservation_scope = build_agent_dispatch_scope(
            self.state,
            agent=self.manager,
            tasks=self.work.tasks,
            blockers=self.work.blockers,
            reviews=self.work.reviews,
            inbox_items=self.work.inbox,
        )
        self.scope = build_agent_dispatch_scope(
            self.state,
            agent=self.manager,
            tasks=self.work.tasks,
            blockers=self.work.scope_blockers,
            reviews=self.work.reviews,
            inbox_items=self.work.inbox,
        )
        initial_release_project_ids = {
            task.project_id
            for deliverable_id in self.initial_release_decision_ids
            if (deliverable := self.state.deliverables.get(deliverable_id)) is not None
            if (task := self.state.tasks.get(deliverable.task_id)) is not None
        }
        self.scoped_task_ids = sorted(set(self.scope.task_ids) | set(self.supervision_task_ids))
        self.scoped_project_ids = sorted(set(self.scope.project_ids) | initial_release_project_ids)
        self.scoped_managed_repo_ids = sorted(
            {
                project.git.managed_repo_id
                for project_id in self.scoped_project_ids
                if (project := self.state.projects.get(project_id)) is not None
                and project.git is not None
            }
        )
        self.source_evidence_snapshot_ids = external_source_evidence_snapshot_ids_for_dispatch(
            self.state,
            agent=self.manager,
            task_ids={
                *self.scoped_task_ids,
                *(
                    self.state.deliverables[deliverable_id].task_id
                    for deliverable_id in self.initial_release_decision_ids
                    if deliverable_id in self.state.deliverables
                ),
            },
            project_ids=self.scoped_project_ids,
            include_team_cases=True,
        )

    def _reserve_dispatch(self) -> None:
        self.has_prior_successful_wake = _has_prior_successful_ordinary_wake(
            self.state,
            self.manager,
        )
        self.dispatch = TeamDispatch(
            id=self.dispatch_id,
            agent_id=self.manager.id,
            session_id=self.manager.session_id,
            cause_kind="manager_fallback",
            work_lineage_id=self.work.lineage_id,
            status=TeamDispatchStatus.PREPARING,
            review_routing_authorities=self.routing_authorities,
            initial_release_decision_deliverable_ids=(self.initial_release_decision_ids),
            project_ids=self.scoped_project_ids,
            managed_repo_ids=self.scoped_managed_repo_ids,
            created_at=self.planner.effective_now,
            preparation_expires_at=(
                self.planner.effective_now
                + timedelta(seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS)
                if self.planner.defer_activation
                else None
            ),
            requested_lease_seconds=max(1, int(self.planner.lease_seconds)),
            task_ids=self.scoped_task_ids,
            blocker_ids=list(self.scope.blocker_ids),
            review_request_ids=list(self.scope.review_request_ids),
            inbox_item_ids=list(self.scope.inbox_item_ids),
            source_evidence_snapshot_ids=list(self.source_evidence_snapshot_ids),
            mcp_capability_receipt=_new_review_mcp_capability_receipt(
                self.state,
                agent_id=self.manager.id,
                review_request_ids=self.scope.review_request_ids,
            ),
            wake_work_fingerprint=self.work.fingerprint,
            wake_work_record_fingerprints=self.work.record_fingerprints,
        )
        self.state.dispatches[self.dispatch_id] = self.dispatch
        self.runtime.active_dispatch_id = self.dispatch_id
        prepare_case_context(self.planner.service._repository, self.state, self.dispatch)

    def _update_runtime(self) -> None:
        self.runtime.wake_reason = "manager_fallback"
        self.runtime.last_wake_at = self.planner.effective_now
        self.runtime.wake_leased_until = (
            None
            if self.planner.defer_activation
            else self.planner.effective_now + timedelta(seconds=self.planner.lease_seconds)
        )
        self.runtime.next_wake_at = self.planner.effective_now + timedelta(
            seconds=self.planner.wake_interval_seconds
        )
        self.runtime.updated_at = self.planner.effective_now
        if (
            self.runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
            and not self.runtime.context_rotation_pending
        ):
            _mark_context_rotation_pending(
                self.state,
                agent=self.manager,
                runtime=self.runtime,
                reason="every_wake",
                requested_by=None,
                requested_at=self.planner.effective_now,
                request_note=None,
            )
        if (
            not self.low_priority_inbox_ids
            or {item.id for item in self.work.inbox if item.priority == TeamInboxPriority.LOW}
            == self.low_priority_inbox_ids
        ):
            _clear_low_priority_batch(self.runtime)
        assign_blockers_to_dispatch(
            self.state,
            agent_id=self.manager.id,
            dispatch_id=self.dispatch_id,
            blocker_ids=self.reservation_scope.blocker_ids,
            now=self.planner.effective_now,
        )

    def _append_lease(self) -> None:
        scoped_blockers = [
            self.state.blockers[blocker_id].model_dump(mode="json")
            for blocker_id in self.dispatch.blocker_ids
        ]
        dispatch_assessments = TaskAssessmentService(self.state)
        manager_payload = _agent_payload(self.state, self.manager)
        role_description_fingerprint = agent_role_description_fingerprint(manager_payload)
        self.dispatch.role_description_fingerprint = role_description_fingerprint
        self.teams_with_lease.add(self.team.id)
        context_snapshots = self.planner.service._repository.snapshots.for_dispatch(
            self.dispatch_id, state=self.state
        )
        self.planner.leases.append(
            {
                "agent": manager_payload,
                "supervisory_overview": TeamReadService(
                    state=self.state,
                    actor=self.manager,
                ).supervisory_overview(),
                "runtime": self.runtime.model_dump(mode="json"),
                "role_description_changed": (
                    self.runtime.last_delivered_role_description_fingerprint
                    != role_description_fingerprint
                ),
                "role_description_fingerprint": role_description_fingerprint,
                "dispatch_id": self.dispatch_id,
                "has_prior_successful_wake": self.has_prior_successful_wake,
                "reason": "manager_fallback",
                "dispatch": self.dispatch.model_dump(mode="json"),
                "tasks": [
                    task_payload_with_assessment(
                        self.state,
                        task,
                        assessment_service=dispatch_assessments,
                    )
                    for task in self.work.tasks
                ],
                **_dispatch_case_payloads(
                    self.state,
                    agent=self.manager,
                    dispatch=self.dispatch,
                ),
                "external_source_evidence_snapshots": [
                    external_source_evidence_snapshot_payload(
                        self.state.external_source_evidence_snapshots[snapshot_id],
                        now=self.planner.effective_now,
                    )
                    for snapshot_id in self.dispatch.source_evidence_snapshot_ids
                    if snapshot_id in self.state.external_source_evidence_snapshots
                ],
                "case_contexts": dispatch_case_contexts(
                    self.state, self.dispatch_id, snapshots=context_snapshots
                ),
                "case_contexts_delta": dispatch_case_contexts(
                    self.state, self.dispatch_id, snapshots=context_snapshots, delta=True
                ),
                "inbox": [item.model_dump(mode="json") for item in self.work.inbox],
                "blockers": scoped_blockers,
                "reviews": [review.model_dump(mode="json") for review in self.work.reviews],
                "release_packages": _release_package_payloads_for_dispatch(
                    self.state,
                    self.manager,
                    work_lineage_id=self.dispatch.work_lineage_id,
                    filter_work_lineage=self.filter_work_lineage,
                ),
                "runtime_attention": _runtime_attention_payloads_for_dispatch(
                    self.state,
                    self.manager,
                    work_lineage_id=self.dispatch.work_lineage_id,
                    filter_work_lineage=self.filter_work_lineage,
                ),
                "recent_action_feedback": _action_feedback_for_dispatch(
                    self.state,
                    agent_id=self.manager.id,
                    work_lineage_id=self.dispatch.work_lineage_id,
                    filter_work_lineage=self.filter_work_lineage,
                ),
                "work_lineage_chat_sources": (
                    [
                        ref.model_dump(mode="json")
                        for ref in lineage.chat_source_refs
                        if (
                            (conversation := self.state.conversations.get(ref.conversation_id))
                            is not None
                            and conversation.agent_id == self.manager.id
                        )
                    ]
                    if self.dispatch.work_lineage_id is not None
                    and (lineage := self.state.work_lineages.get(self.dispatch.work_lineage_id))
                    is not None
                    else []
                ),
                "manager_fallback_summary": self.work.summary,
                "operator_chat_participants": _operator_chat_participant_payloads(
                    self.state,
                    agent_id=self.manager.id,
                ),
                "superior_agent_id": self.manager.reports_to_agent_id,
                "subordinate_agent_ids": list(self.manager.subordinate_agent_ids),
                "subordinates": _subordinate_agent_payloads(self.state, self.manager),
            }
        )


class _ManagerFallbackLineageCandidate:
    """Keep each lineage's exclusions and fingerprints local to its evaluation."""

    excluded_task_ids: set[str]
    excluded_review_ids: set[str]
    excluded_inbox_ids: set[str]
    excluded_action_ids: set[str]
    tasks: list[AgentTask]
    inbox: list[AgentInboxItem]
    reviews: list[ReviewRequest]
    blockers: list[TeamBlocker]
    scope_blockers: list[TeamBlocker]
    summary: dict[str, Any]
    fingerprint: str
    record_fingerprints: dict[str, str]
    work_changed: bool

    def __init__(
        self,
        *,
        candidate: _ManagerFallbackCandidate,
        lineage_id: str | None,
    ) -> None:
        self.candidate = candidate
        self.lineage_id = lineage_id

    def select(self) -> _ManagerFallbackWork | None:
        self._exclude_other_lineages()
        self._collect_work()
        if not self._prepare_trigger():
            return None
        if not self._can_wake():
            return None
        return _ManagerFallbackWork(
            lineage_id=self.lineage_id,
            tasks=self.tasks,
            inbox=self.inbox,
            reviews=self.reviews,
            blockers=self.blockers,
            scope_blockers=self.scope_blockers,
            summary=self.summary,
            fingerprint=self.fingerprint,
            record_fingerprints=self.record_fingerprints,
        )

    def _exclude_other_lineages(self) -> None:
        self.excluded_task_ids = (
            {
                task.id
                for task in self.candidate.state.tasks.values()
                if work_lineage_id_for_record(self.candidate.state, task) != self.lineage_id
            }
            if self.candidate.filter_work_lineage
            else set()
        )
        self.excluded_review_ids = (
            {
                review.id
                for review in self.candidate.state.review_requests.values()
                if work_lineage_id_for_record(self.candidate.state, review) != self.lineage_id
            }
            if self.candidate.filter_work_lineage
            else set()
        )
        self.excluded_inbox_ids = (
            {
                item.id
                for item in self.candidate.state.inbox.values()
                if work_lineage_id_for_record(self.candidate.state, item) != self.lineage_id
            }
            if self.candidate.filter_work_lineage
            else set()
        )
        self.excluded_action_ids = (
            {
                record.id
                for record in self.candidate.state.action_records.values()
                if work_lineage_id_for_record(self.candidate.state, record) != self.lineage_id
            }
            if self.candidate.filter_work_lineage
            else set()
        )

    def _collect_work(self) -> None:
        self.tasks = [row for row in self.candidate.tasks if self._same_lineage(row)]
        self.inbox = [row for row in self.candidate.inbox if self._same_lineage(row)]
        self.reviews = [row for row in self.candidate.reviews if self._same_lineage(row)]
        self.blockers = [row for row in self.candidate.blockers if self._same_lineage(row)]
        self.scope_blockers = [
            row for row in self.candidate.scope_blockers if self._same_lineage(row)
        ]
        self.summary = _manager_fallback_summary_payload(
            self.candidate.state,
            team=self.candidate.team,
            manager=self.candidate.manager,
            now=self.candidate.planner.effective_now,
            excluded_review_request_ids=(
                set(self.candidate.blocked_review_ids) | self.excluded_review_ids
            ),
            excluded_inbox_item_ids=self.excluded_inbox_ids,
            excluded_task_ids=self.excluded_task_ids,
            excluded_action_record_ids=self.excluded_action_ids,
        )

    def _prepare_trigger(self) -> bool:
        # Fingerprint all fallback work, including rows outside the displayed summary.
        candidate_trigger_summary = _manager_fallback_summary_payload(
            self.candidate.state,
            team=self.candidate.team,
            manager=self.candidate.manager,
            now=self.candidate.planner.effective_now,
            limit_rows=False,
            excluded_review_request_ids=(
                set(self.candidate.blocked_review_ids)
                | self.excluded_review_ids
                | self.candidate.owned_review_ids
            ),
            excluded_inbox_item_ids=(self.excluded_inbox_ids | self.candidate.owned_inbox_ids),
            excluded_task_ids=(self.excluded_task_ids | self.candidate.active_task_ids),
            excluded_action_record_ids=(self.excluded_action_ids | self.candidate.owned_action_ids),
        )
        if not _manager_fallback_summary_has_actionable_work(candidate_trigger_summary):
            return False
        self.fingerprint = manager_fallback_work_fingerprint(
            candidate_trigger_summary,
            state=self.candidate.state,
        )
        self.record_fingerprints = manager_fallback_work_record_fingerprints(
            candidate_trigger_summary,
            state=self.candidate.state,
        )
        latest_manager_attempt = _latest_delivered_wake_work_attempt_for_agent(
            self.candidate.state,
            agent_id=self.candidate.manager.id,
            cause_kind="manager_fallback",
            work_lineage_id=self.lineage_id,
            filter_work_lineage=self.candidate.filter_work_lineage,
        )
        self.work_changed = (
            latest_manager_attempt is None
            or _wake_record_snapshot_has_additions_or_updates(
                current=self.record_fingerprints,
                previous=(latest_manager_attempt.wake_work_record_fingerprints),
                current_aggregate=self.fingerprint,
                previous_aggregate=(latest_manager_attempt.wake_work_fingerprint),
            )
        )

        return True

    def _can_wake(self) -> bool:
        if (
            self.candidate.planner.max_no_progress_count >= 0
            and self.candidate.runtime.no_progress_count
            >= self.candidate.planner.max_no_progress_count
            and not self.work_changed
        ):
            return False
        due_by_time = (
            self.candidate.runtime.next_wake_at is None
            or _ensure_utc(self.candidate.runtime.next_wake_at)
            <= self.candidate.planner.effective_now
        )
        if not due_by_time and not self.work_changed:
            return False

        return True

    def _same_lineage(self, record: object) -> bool:
        return (
            not self.candidate.filter_work_lineage
            or work_lineage_id_for_record(self.candidate.state, record) == self.lineage_id
        )


class _OrdinaryLeaseCandidate:
    """Keep one agent's selection and reservation state local to a planning attempt."""

    runtime: AgentRuntime
    active_tasks: list[AgentTask]
    pending_inbox: list[AgentInboxItem]
    wake_inbox: list[AgentInboxItem]
    pending_reviews: list[ReviewRequest]
    lineage_context_active: bool
    due_blockers: list[TeamBlocker]
    assigned_blockers: list[TeamBlocker]
    preparation_recovery_blockers: list[TeamBlocker]
    preparation_recovery_dispatch: bool
    immediate_inbox: list[AgentInboxItem]
    low_priority_inbox: list[AgentInboxItem]
    wakeable_low_priority_inbox_ids: set[str]
    action_feedback: list[dict[str, Any]]
    no_progress_exhausted: bool
    rotation_pending: bool
    task_work_ready: bool
    low_priority_wake_ready: bool
    dispatch_work_lineage_id: str | None
    dispatch_id: str
    reason: str
    has_prior_successful_wake: bool
    durable_dispatch: TeamDispatch

    def __init__(
        self,
        *,
        planning: _LeasePlanning,
        state: TeamState,
        agent: PersistentAgent,
        role_allocations: dict[str, dict[str, Any]],
    ) -> None:
        self.planning = planning
        self.state = state
        self.agent = agent
        self.role_allocations = role_allocations

    def plan(self) -> bool:
        if not self._eligible():
            return False
        self._collect_work()
        if not self._wake_due():
            return False
        if not self._select_work_lineage():
            return False
        self._reserve_dispatch()
        self._append_lease_payload()
        return True

    def _eligible(self) -> bool:
        if self.agent.status != AgentStatus.ACTIVE:
            return False
        if _active_stand_downs_for_agent(self.state, self.agent.id):
            return False
        if self.planning.team_id is not None and self.agent.team_id != self.planning.team_id:
            return False
        if self.agent.team_id in self.planning.blocked_teams:
            return False
        if self.planning.project_id is not None and not agent_has_project_assignment(
            self.state, self.agent.id, self.planning.project_id
        ):
            return False
        if self.planning.agent_id is not None and self.agent.id != self.planning.agent_id:
            return False
        if self.planning.session_is_busy is not None and self.planning.session_is_busy(
            self.agent.session_id
        ):
            return False
        self.runtime = self.state.agent_runtimes.setdefault(
            self.agent.id,
            AgentRuntime(agent_id=self.agent.id),
        )
        _clear_completed_chat_dispatch_lease(
            self.state,
            self.runtime,
            now=self.planning.effective_now,
        )
        if _runtime_has_active_dispatch_reservation(self.state, self.runtime) or (
            self.runtime.wake_leased_until is not None
            and _ensure_utc(self.runtime.wake_leased_until) > self.planning.effective_now
        ):
            return False
        return True

    def _collect_work(self) -> None:
        self.active_tasks = actionable_dispatch_tasks(
            self.state,
            agent=self.agent,
        )
        self.pending_inbox, self.wake_inbox, self.pending_reviews = _ordinary_wake_work_for_agent(
            self.state,
            self.agent.id,
        )
        self.lineage_context_active = (
            self.runtime.context_clearing_policy == AgentContextClearingPolicy.WORK_LINEAGE
        )
        if self.pending_reviews and not self.lineage_context_active:
            selected_review = self.pending_reviews[0]
            unselected_review_ids = {review.id for review in self.pending_reviews[1:]}
            self.pending_reviews = [selected_review]
            if unselected_review_ids:
                self.pending_inbox = [
                    item
                    for item in self.pending_inbox
                    if not inbox_references_review_ids(
                        item,
                        unselected_review_ids,
                    )
                ]
                self.wake_inbox = _wakeable_inbox_items(self.pending_inbox)
        self.due_blockers = blockers_due_for_agent(
            self.state,
            agent_id=self.agent.id,
            now=self.planning.effective_now,
        )
        self.assigned_blockers = blockers_assigned_to_agent(
            self.state,
            agent_id=self.agent.id,
        )
        self.preparation_recovery_blockers = _preparation_recovery_blockers(
            due_blockers=self.due_blockers,
            assigned_blockers=self.assigned_blockers,
            inbox_items=self.wake_inbox,
        )
        self.preparation_recovery_dispatch = bool(self.preparation_recovery_blockers)
        if self.preparation_recovery_dispatch:
            recovery_blocker_ids = {blocker.id for blocker in self.preparation_recovery_blockers}
            self.active_tasks = []
            self.pending_reviews = []
            self.due_blockers = [
                blocker for blocker in self.due_blockers if blocker.id in recovery_blocker_ids
            ]
            self.assigned_blockers = [
                blocker
                for blocker in self.assigned_blockers
                if blocker.reason_code in EXECUTION_PREPARATION_BLOCKER_REASON_CODES
            ]
            self.pending_inbox = [
                item
                for item in self.pending_inbox
                if _inbox_references_blocker_ids(
                    item,
                    recovery_blocker_ids,
                )
            ]
            self.wake_inbox = _wakeable_inbox_items(self.pending_inbox)
        self.immediate_inbox = [
            item for item in self.wake_inbox if item.priority != TeamInboxPriority.LOW
        ]
        self.low_priority_inbox = [
            item for item in self.wake_inbox if item.priority == TeamInboxPriority.LOW
        ]
        self.wakeable_low_priority_inbox_ids = {item.id for item in self.low_priority_inbox}
        self.action_feedback = (
            []
            if self.preparation_recovery_dispatch
            else _latest_action_feedback_for_agent(self.state, self.agent.id)
        )
        active_owned_tasks = _active_tasks_for_agent(
            self.state,
            self.agent.id,
        )
        self.no_progress_exhausted = (
            self.planning.max_no_progress_count >= 0
            and self.runtime.no_progress_count >= self.planning.max_no_progress_count
        )
        if self.no_progress_exhausted and (
            active_owned_tasks
            or self.wake_inbox
            or self.pending_reviews
            or self.action_feedback
            or self.due_blockers
        ):
            exhausted_dispatch = _latest_terminal_dispatch_for_agent(
                self.state,
                agent_id=self.agent.id,
            )
            stall_result: dict[str, Any] = {
                "events": [],
                "errors": [],
            }
            open_or_update_agent_execution_stall(
                self.state,
                agent_id=self.agent.id,
                affected_task_ids=[task.id for task in active_owned_tasks],
                exhausted_dispatch_id=(
                    exhausted_dispatch.id if exhausted_dispatch is not None else None
                ),
                exhausted_dispatch_task_ids=(
                    exhausted_dispatch.task_ids if exhausted_dispatch is not None else []
                ),
                no_progress_count=self.runtime.no_progress_count,
                max_no_progress_count=self.planning.max_no_progress_count,
                result=stall_result,
                now=self.planning.effective_now,
            )
            self.planning.blocker_routing_events.extend(stall_result.get("events") or [])
            self.active_tasks = actionable_dispatch_tasks(
                self.state,
                agent=self.agent,
            )
        self.rotation_pending = self.runtime.context_rotation_pending
        self.task_work_ready = bool(self.active_tasks)

    def _wake_due(self) -> bool:
        wake_eligible = (
            self.rotation_pending
            or bool(self.wake_inbox)
            or bool(self.pending_reviews)
            or bool(self.action_feedback)
            or bool(self.due_blockers)
            or self.task_work_ready
        )
        if not wake_eligible:
            _clear_low_priority_batch(self.runtime)
            return False
        due_by_time = (
            not self.planning.respect_wake_interval
            or self.runtime.next_wake_at is None
            or _ensure_utc(self.runtime.next_wake_at) <= self.planning.effective_now
        )
        immediate_wake_ready = (
            self.rotation_pending
            or bool(self.immediate_inbox)
            or bool(self.pending_reviews)
            or bool(self.action_feedback)
            or bool(self.due_blockers)
            or (self.task_work_ready and due_by_time)
        )
        self.low_priority_wake_ready = False
        if self.low_priority_inbox and not immediate_wake_ready:
            self.low_priority_wake_ready = _advance_low_priority_batch(
                self.runtime,
                now=self.planning.effective_now,
                due_after_cycles=self.planning.low_priority_threshold,
            )
            if not self.low_priority_wake_ready:
                self.runtime.updated_at = self.planning.effective_now
                return False
        elif not self.low_priority_inbox:
            _clear_low_priority_batch(self.runtime)
        if (
            not self.wake_inbox
            and not self.pending_reviews
            and not self.rotation_pending
            and not self.action_feedback
            and not self.due_blockers
            and not due_by_time
        ):
            return False
        if self.wake_inbox and not immediate_wake_ready and not self.low_priority_wake_ready:
            return False
        return True

    def _select_work_lineage(self) -> bool:
        self.dispatch_work_lineage_id: str | None = None
        if self.lineage_context_active:
            candidate_lineage_ids = _ordered_work_lineage_ids(
                self.state,
                (
                    *self.preparation_recovery_blockers,
                    *self.immediate_inbox,
                    *self.due_blockers,
                    *self.pending_reviews,
                    *(self.low_priority_inbox if self.low_priority_wake_ready else ()),
                    *self.action_feedback,
                    *self.active_tasks,
                ),
            )
            if not candidate_lineage_ids and self.rotation_pending:
                candidate_lineage_ids = [None]
            selected_lineage_work: (
                tuple[
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                ]
                | None
            ) = None
            for candidate_lineage_id in candidate_lineage_ids:

                def _same_lineage(record: object) -> bool:
                    return work_lineage_id_for_record(self.state, record) == candidate_lineage_id

                candidate_active_tasks = [row for row in self.active_tasks if _same_lineage(row)]
                candidate_lineage_inbox = [row for row in self.pending_inbox if _same_lineage(row)]
                candidate_lineage_reviews = [
                    row for row in self.pending_reviews if _same_lineage(row)
                ]
                candidate_due_blockers = [row for row in self.due_blockers if _same_lineage(row)]
                candidate_assigned_blockers = [
                    row for row in self.assigned_blockers if _same_lineage(row)
                ]
                candidate_action_feedback = [
                    row for row in self.action_feedback if _same_lineage(row)
                ]
                review_options = (
                    [[review] for review in candidate_lineage_reviews]
                    if candidate_lineage_reviews
                    else [[]]
                )
                for candidate_pending_reviews in review_options:
                    selected_review_ids = {review.id for review in candidate_pending_reviews}
                    unselected_review_ids = {
                        review.id
                        for review in candidate_lineage_reviews
                        if review.id not in selected_review_ids
                    }
                    candidate_pending_inbox = [
                        item
                        for item in candidate_lineage_inbox
                        if not inbox_references_review_ids(
                            item,
                            unselected_review_ids,
                        )
                    ]
                    candidate_wake_inbox = _wakeable_inbox_items(candidate_pending_inbox)
                    candidate_immediate_inbox = [
                        item
                        for item in candidate_wake_inbox
                        if item.priority != TeamInboxPriority.LOW
                    ]
                    candidate_low_priority_inbox = [
                        item
                        for item in candidate_wake_inbox
                        if item.priority == TeamInboxPriority.LOW
                    ]
                    wake_work_changed = _wake_work_changed_since_latest_lineage_attempt(
                        self.state,
                        agent_id=self.agent.id,
                        work_lineage_id=candidate_lineage_id,
                        inbox_items=candidate_wake_inbox,
                        reviews=candidate_pending_reviews,
                    )
                    if (
                        self.no_progress_exhausted
                        and not wake_work_changed
                        and not self.rotation_pending
                        and not candidate_action_feedback
                        and not candidate_due_blockers
                    ):
                        continue
                    self.dispatch_work_lineage_id = candidate_lineage_id
                    selected_lineage_work = (
                        candidate_active_tasks,
                        candidate_pending_inbox,
                        candidate_wake_inbox,
                        candidate_immediate_inbox,
                        candidate_low_priority_inbox,
                        candidate_pending_reviews,
                        candidate_due_blockers,
                        candidate_assigned_blockers,
                        candidate_action_feedback,
                    )
                    break
                if selected_lineage_work is not None:
                    break
            if selected_lineage_work is None:
                return False
            (
                self.active_tasks,
                self.pending_inbox,
                self.wake_inbox,
                self.immediate_inbox,
                self.low_priority_inbox,
                self.pending_reviews,
                self.due_blockers,
                self.assigned_blockers,
                self.action_feedback,
            ) = selected_lineage_work
            self.preparation_recovery_blockers = [
                row
                for row in self.preparation_recovery_blockers
                if (work_lineage_id_for_record(self.state, row) == self.dispatch_work_lineage_id)
            ]
        else:
            wake_work_changed = wake_work_changed_since_latest_attempt(
                self.state,
                agent_id=self.agent.id,
                inbox_items=self.wake_inbox,
                reviews=self.pending_reviews,
            )
            if (
                self.no_progress_exhausted
                and not wake_work_changed
                and not self.rotation_pending
                and not self.action_feedback
                and not self.due_blockers
            ):
                return False
        return True

    def _reserve_dispatch(self) -> None:
        context_handoff_preparation = (
            self.runtime.context_rotation_pending
            and self.runtime.context_handoff_preparation_pending
        )
        self.dispatch_id = f"team-dispatch-{uuid4().hex}"
        self.reason = (
            "context_handoff"
            if context_handoff_preparation
            else "blocker"
            if self.preparation_recovery_dispatch
            else "inbox"
            if self.immediate_inbox
            else "blocker"
            if self.due_blockers
            else "review"
            if self.pending_reviews
            else "low_priority_inbox"
            if self.low_priority_wake_ready
            else "action_feedback"
            if self.action_feedback
            else "scheduled"
        )
        cause_id = (
            self.preparation_recovery_blockers[0].id
            if self.preparation_recovery_dispatch
            else self.immediate_inbox[0].id
            if self.immediate_inbox
            else self.due_blockers[0].id
            if self.due_blockers
            else self.pending_reviews[0].id
            if self.pending_reviews
            else self.low_priority_inbox[0].id
            if self.low_priority_wake_ready and self.low_priority_inbox
            else _action_feedback_cause_id(self.action_feedback)
        )
        if context_handoff_preparation:
            reservation_scope = None
            dispatch_scope = None
        elif self.preparation_recovery_dispatch:
            reservation_scope = build_preparation_recovery_dispatch_scope(
                blockers=self.due_blockers,
                inbox_items=self.pending_inbox,
            )
            dispatch_scope = build_preparation_recovery_dispatch_scope(
                blockers=self.assigned_blockers,
                inbox_items=self.pending_inbox,
            )
        else:
            reservation_scope = build_agent_dispatch_scope(
                self.state,
                agent=self.agent,
                tasks=self.active_tasks,
                blockers=self.due_blockers,
                reviews=self.pending_reviews,
                inbox_items=self.pending_inbox,
            )
            dispatch_scope = build_agent_dispatch_scope(
                self.state,
                agent=self.agent,
                tasks=self.active_tasks,
                blockers=self.assigned_blockers,
                reviews=self.pending_reviews,
                inbox_items=self.pending_inbox,
            )
        self.has_prior_successful_wake = _has_prior_successful_ordinary_wake(
            self.state,
            self.agent,
        )
        self.durable_dispatch = TeamDispatch(
            id=self.dispatch_id,
            agent_id=self.agent.id,
            session_id=self.agent.session_id,
            cause_kind=self.reason,
            cause_id=cause_id,
            work_lineage_id=self.dispatch_work_lineage_id,
            status=TeamDispatchStatus.PREPARING,
            created_at=self.planning.effective_now,
            preparation_expires_at=(
                self.planning.effective_now
                + timedelta(seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS)
                if self.planning.defer_activation
                else None
            ),
            requested_lease_seconds=(
                120
                if self.reason == "context_handoff"
                else max(1, int(self.planning.lease_seconds))
            ),
            action_feedback_dispatch_ids=(
                [
                    feedback_dispatch_id
                    for feedback in self.action_feedback
                    if (
                        feedback_dispatch_id := _optional_text(
                            feedback.get("dispatch_id") if isinstance(feedback, Mapping) else None
                        )
                    )
                    is not None
                ]
                if self.reason == "action_feedback"
                else []
            ),
            mcp_capability_receipt=_new_review_mcp_capability_receipt(
                self.state,
                agent_id=self.agent.id,
                review_request_ids=(
                    dispatch_scope.review_request_ids if dispatch_scope is not None else ()
                ),
            ),
            wake_work_fingerprint=(
                wake_work_fingerprint(
                    inbox_items=self.wake_inbox,
                    reviews=self.pending_reviews,
                )
                if not context_handoff_preparation and not self.preparation_recovery_dispatch
                else None
            ),
            wake_work_record_fingerprints=(
                wake_work_record_fingerprints(
                    inbox_items=self.wake_inbox,
                    reviews=self.pending_reviews,
                )
                if not context_handoff_preparation and not self.preparation_recovery_dispatch
                else {}
            ),
            **(dispatch_scope.model_fields() if dispatch_scope is not None else {}),
        )
        self.durable_dispatch.claimed_role_task_ids = [
            task_id
            for task_id in self.durable_dispatch.task_ids
            if task_id in self.role_allocations
            and self.role_allocations[task_id]["selected_owner_agent_id"] == self.agent.id
        ]
        self.state.dispatches[self.dispatch_id] = self.durable_dispatch
        if (
            self.runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
            and not self.runtime.context_rotation_pending
        ):
            _mark_context_rotation_pending(
                self.state,
                agent=self.agent,
                runtime=self.runtime,
                reason="every_wake",
                requested_by=None,
                requested_at=self.planning.effective_now,
                request_note=None,
            )
        if (
            self.wakeable_low_priority_inbox_ids
            and {item.id for item in self.low_priority_inbox}
            == self.wakeable_low_priority_inbox_ids
        ):
            _clear_low_priority_batch(self.runtime)
        self.runtime.active_dispatch_id = self.dispatch_id
        prepare_case_context(self.planning.service._repository, self.state, self.durable_dispatch)
        self.runtime.wake_reason = self.reason
        self.runtime.last_wake_at = self.planning.effective_now
        self.runtime.wake_leased_until = (
            None
            if self.planning.defer_activation
            else self.planning.effective_now + timedelta(seconds=self.planning.lease_seconds)
        )
        self.runtime.next_wake_at = self.planning.effective_now + timedelta(
            seconds=self.planning.wake_interval_seconds
        )
        self.runtime.updated_at = self.planning.effective_now
        if reservation_scope is not None:
            assign_blockers_to_dispatch(
                self.state,
                agent_id=self.agent.id,
                dispatch_id=self.dispatch_id,
                blocker_ids=reservation_scope.blocker_ids,
                now=self.planning.effective_now,
            )

    def _append_lease_payload(self) -> None:
        scoped_blockers = [
            self.state.blockers[blocker_id].model_dump(mode="json")
            for blocker_id in self.durable_dispatch.blocker_ids
        ]
        dispatch_assessments = TaskAssessmentService(self.state)
        agent_payload = _agent_payload(self.state, self.agent)
        role_description_fingerprint = agent_role_description_fingerprint(agent_payload)
        self.durable_dispatch.role_description_fingerprint = role_description_fingerprint
        context_snapshots = self.planning.service._repository.snapshots.for_dispatch(
            self.dispatch_id, state=self.state
        )
        self.planning.leases.append(
            {
                "agent": agent_payload,
                "supervisory_overview": TeamReadService(
                    state=self.state,
                    actor=self.agent,
                ).supervisory_overview(),
                "runtime": self.runtime.model_dump(mode="json"),
                "role_description_changed": (
                    self.runtime.last_delivered_role_description_fingerprint
                    != role_description_fingerprint
                ),
                "role_description_fingerprint": role_description_fingerprint,
                "dispatch_id": self.dispatch_id,
                "has_prior_successful_wake": self.has_prior_successful_wake,
                "reason": self.reason,
                "dispatch": self.durable_dispatch.model_dump(mode="json"),
                "tasks": [
                    task_payload_with_assessment(
                        self.state,
                        self.state.tasks[task_id],
                        assessment_service=dispatch_assessments,
                    )
                    for task_id in self.durable_dispatch.task_ids
                    if task_id in self.state.tasks
                ],
                "projects": [
                    {
                        **project.model_dump(mode="json"),
                        "blocking_sources": project_blocking_sources(
                            self.state,
                            project.id,
                            assessment_service=dispatch_assessments,
                        ),
                    }
                    for project in sorted(
                        (
                            self.state.projects[visible_project_id]
                            for visible_project_id in self.durable_dispatch.project_ids
                            if visible_project_id in self.state.projects
                        ),
                        key=lambda item: item.id,
                    )
                ],
                "outcome_kinds": [
                    {
                        **definition,
                        "effective_project_id": visible_project_id,
                    }
                    for visible_project_id in self.durable_dispatch.project_ids
                    if visible_project_id in self.state.projects
                    for definition in effective_outcome_kind_payloads(
                        self.state,
                        team_id=self.state.projects[visible_project_id].team_id,
                        project_id=visible_project_id,
                    )
                ],
                **_dispatch_case_payloads(
                    self.state,
                    agent=self.agent,
                    dispatch=self.durable_dispatch,
                ),
                "external_source_evidence_snapshots": [
                    external_source_evidence_snapshot_payload(
                        self.state.external_source_evidence_snapshots[snapshot_id],
                        now=self.planning.effective_now,
                    )
                    for snapshot_id in self.durable_dispatch.source_evidence_snapshot_ids
                    if snapshot_id in self.state.external_source_evidence_snapshots
                ],
                "case_contexts": dispatch_case_contexts(
                    self.state, self.dispatch_id, snapshots=context_snapshots
                ),
                "case_contexts_delta": dispatch_case_contexts(
                    self.state, self.dispatch_id, snapshots=context_snapshots, delta=True
                ),
                "inbox": [item.model_dump(mode="json") for item in self.pending_inbox],
                "blockers": scoped_blockers,
                "reviews": [review.model_dump(mode="json") for review in self.pending_reviews],
                "release_packages": _release_package_payloads_for_dispatch(
                    self.state,
                    self.agent,
                    work_lineage_id=self.durable_dispatch.work_lineage_id,
                    filter_work_lineage=self.lineage_context_active,
                ),
                "runtime_attention": _runtime_attention_payloads_for_dispatch(
                    self.state,
                    self.agent,
                    work_lineage_id=self.durable_dispatch.work_lineage_id,
                    filter_work_lineage=self.lineage_context_active,
                ),
                "recent_action_feedback": self.action_feedback,
                "work_lineage_chat_sources": (
                    [
                        ref.model_dump(mode="json")
                        for ref in lineage.chat_source_refs
                        if (
                            (conversation := self.state.conversations.get(ref.conversation_id))
                            is not None
                            and conversation.agent_id == self.agent.id
                        )
                    ]
                    if self.durable_dispatch.work_lineage_id is not None
                    and (
                        lineage := self.state.work_lineages.get(
                            self.durable_dispatch.work_lineage_id
                        )
                    )
                    is not None
                    else []
                ),
                "operator_chat_participants": _operator_chat_participant_payloads(
                    self.state,
                    agent_id=self.agent.id,
                ),
                "superior_agent_id": self.agent.reports_to_agent_id,
                "subordinate_agent_ids": list(self.agent.subordinate_agent_ids),
                "subordinates": _subordinate_agent_payloads(self.state, self.agent),
            }
        )


def _action_feedback_cause_id(
    action_feedback: list[dict[str, Any]],
) -> str | None:
    if not action_feedback:
        return None
    return _optional_text(action_feedback[0].get("action_record_id"))


def _action_feedback_for_dispatch(
    state: TeamState,
    *,
    agent_id: str,
    work_lineage_id: str | None,
    filter_work_lineage: bool,
) -> list[dict[str, Any]]:
    rows = _latest_action_feedback_for_agent(state, agent_id)
    if not filter_work_lineage:
        return rows
    return [row for row in rows if work_lineage_id_for_record(state, row) == work_lineage_id]


def _ordered_work_lineage_ids(
    state: TeamState,
    records: Sequence[object],
) -> list[str | None]:
    lineage_ids: list[str | None] = []
    seen: set[str | None] = set()
    for record in records:
        lineage_id = work_lineage_id_for_record(state, record)
        if lineage_id in seen:
            continue
        seen.add(lineage_id)
        lineage_ids.append(lineage_id)
    return lineage_ids


def _release_package_payloads_for_dispatch(
    state: TeamState,
    agent: PersistentAgent,
    *,
    work_lineage_id: str | None,
    filter_work_lineage: bool,
) -> dict[str, dict[str, Any]]:
    payloads = _release_package_payloads_for_agent(state, agent)
    if not filter_work_lineage:
        return payloads
    return {
        release_id: payload
        for release_id, payload in payloads.items()
        if (release := state.release_packages.get(release_id)) is not None
        and work_lineage_id_for_record(state, release) == work_lineage_id
    }


def _runtime_attention_payloads_for_dispatch(
    state: TeamState,
    agent: PersistentAgent,
    *,
    work_lineage_id: str | None,
    filter_work_lineage: bool,
) -> list[dict[str, Any]]:
    rows = _runtime_attention_payloads_for_agent(state, agent)
    if not filter_work_lineage:
        return rows
    return [
        row
        for row in rows
        if not (lineage_ids := _runtime_attention_work_lineage_ids(state, row))
        or lineage_ids == {work_lineage_id}
    ]


def _runtime_attention_work_lineage_ids(
    state: TeamState,
    row: Mapping[str, Any],
) -> set[str | None]:
    """Resolve durable work references while leaving agent-global attention visible."""

    lineage_ids: set[str | None] = set()
    collections: tuple[tuple[str, str, Mapping[str, Any]], ...] = (
        ("task_id", "task_ids", state.tasks),
        ("deliverable_id", "deliverable_ids", state.deliverables),
        ("review_request_id", "review_request_ids", state.review_requests),
        ("release_package_id", "release_package_ids", state.release_packages),
        ("blocker_id", "blocker_ids", state.blockers),
        ("inbox_item_id", "inbox_item_ids", state.inbox),
        ("action_record_id", "action_record_ids", state.action_records),
        ("promotion_id", "promotion_ids", state.git_promotions),
        ("dispatch_id", "dispatch_ids", state.dispatches),
    )
    links = row.get("links")
    for singular_key, plural_key, collection in collections:
        record_ids = [_optional_text(row.get(singular_key))]
        if isinstance(links, Mapping):
            linked_ids = links.get(plural_key)
            if isinstance(linked_ids, Sequence) and not isinstance(
                linked_ids,
                (str, bytes, bytearray),
            ):
                record_ids.extend(_optional_text(value) for value in linked_ids)
        for record_id in record_ids:
            record = collection.get(record_id or "")
            if record is not None:
                lineage_ids.add(work_lineage_id_for_record(state, record))

    source_id = _optional_text(row.get("source_id"))
    source_kind = (_optional_text(row.get("source_kind")) or "").lower()
    source_collections: dict[str, Mapping[str, Any]] = {
        "task": state.tasks,
        "deliverable": state.deliverables,
        "review_request": state.review_requests,
        "release_package": state.release_packages,
        "human_review_request": state.human_review_requests,
    }
    source = source_collections.get(source_kind, {}).get(source_id or "")
    if source is not None:
        lineage_ids.add(work_lineage_id_for_record(state, source))
    return lineage_ids


def _manager_fallback_lineage_ids(
    state: TeamState,
    summary: Mapping[str, Any],
) -> list[str | None]:
    lineage_ids: list[str | None] = []
    seen: set[str | None] = set()

    def _append(record: object) -> None:
        lineage_id = work_lineage_id_for_record(state, record)
        if lineage_id in seen:
            return
        seen.add(lineage_id)
        lineage_ids.append(lineage_id)

    for key, collection in (
        ("open_work", state.tasks),
        ("pending_inbox", state.inbox),
        ("review_requests", state.review_requests),
        ("latest_failed_actions", state.action_records),
    ):
        rows = summary.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            record = collection.get(_optional_text(row.get("id")) or "")
            if record is not None:
                _append(record)
    release_rows = summary.get("pending_initial_release_decisions")
    if isinstance(release_rows, list):
        for row in release_rows:
            if not isinstance(row, Mapping):
                continue
            deliverable = state.deliverables.get(_optional_text(row.get("deliverable_id")) or "")
            if deliverable is not None:
                _append(deliverable)
    routing_gaps = summary.get("routing_gaps")
    if isinstance(routing_gaps, list):
        for row in routing_gaps:
            if not isinstance(row, Mapping):
                continue
            for key, collection in (
                ("task_ids", state.tasks),
                ("deliverable_ids", state.deliverables),
            ):
                record_ids = row.get(key)
                if not isinstance(record_ids, Sequence) or isinstance(
                    record_ids,
                    (str, bytes, bytearray),
                ):
                    continue
                for record_id in record_ids:
                    record = collection.get(_optional_text(record_id) or "")
                    if record is not None:
                        _append(record)
    return lineage_ids or [None]


def _manager_fallback_supervision_task_ids(
    state: TeamState,
    *,
    manager: PersistentAgent,
    summary: Mapping[str, Any],
) -> list[str]:
    """Freeze displayed actionable subordinate work into manager scope.

    The fallback summary is bounded diagnostic context. Only its displayed
    ``open_work`` rows can become supervision authority, and only while the
    source task is still actionable in the manager's Team. The task remains
    owned by its assigned agent; domain command policy still controls which
    supervisory actions the manager may perform.
    """

    rows = summary.get("open_work")
    if not isinstance(rows, list) or manager.team_id is None:
        return []
    assessments = TaskAssessmentService(state)
    task_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        task_id = _optional_text(row.get("id"))
        task = state.tasks.get(task_id or "")
        project = state.projects.get(task.project_id) if task is not None else None
        if (
            task is None
            or project is None
            or task.owner_agent_id == manager.id
            or project.team_id != manager.team_id
            or assessments.assess(task.id).readiness != TaskAssessmentReadiness.ACTIONABLE
        ):
            continue
        task_ids.add(task.id)
    return sorted(task_ids)


def _has_prior_successful_ordinary_wake(
    state: TeamState,
    agent: PersistentAgent,
) -> bool:
    if hasattr(state, "has_prior_successful_ordinary_wake"):
        return state.has_prior_successful_ordinary_wake(agent)
    return any(
        dispatch.agent_id == agent.id
        and dispatch.session_id == agent.session_id
        and dispatch.status == TeamDispatchStatus.COMPLETED
        and (
            dispatch.cause_kind != "team_chat"
            or _chat_dispatch_seeded_ordinary_context(state, dispatch)
        )
        and dispatch.cause_kind != "governance"
        for dispatch in state.dispatches.values()
    )


def _chat_dispatch_seeded_ordinary_context(
    state: TeamState,
    dispatch: TeamDispatch,
) -> bool:
    message = state.conversation_messages.get(dispatch.cause_id or "")
    return bool(message is not None and message.metadata.get("ordinary_context_seeded") is True)


def _inbox_references_blocker_ids(
    item: AgentInboxItem,
    blocker_ids: set[str],
) -> bool:
    payload = getattr(item, "payload", None)
    if not isinstance(payload, Mapping):
        return False
    raw_ids: list[object] = [payload.get("blocker_id")]
    payload_blocker_ids = payload.get("blocker_ids")
    if isinstance(payload_blocker_ids, (list, tuple, set)):
        raw_ids.extend(payload_blocker_ids)
    referenced_ids = {text for raw in raw_ids if (text := _optional_text(raw)) is not None}
    return bool(referenced_ids & blocker_ids)


def _preparation_recovery_blockers(
    *,
    due_blockers: list[TeamBlocker],
    assigned_blockers: list[TeamBlocker],
    inbox_items: list[AgentInboxItem],
) -> list[TeamBlocker]:
    rows = {
        blocker.id: blocker
        for blocker in due_blockers
        if blocker.reason_code in EXECUTION_PREPARATION_BLOCKER_REASON_CODES
    }
    for blocker in assigned_blockers:
        if blocker.reason_code in EXECUTION_PREPARATION_BLOCKER_REASON_CODES and any(
            _inbox_references_blocker_ids(item, {blocker.id}) for item in inbox_items
        ):
            rows[blocker.id] = blocker
    return [rows[blocker_id] for blocker_id in sorted(rows)]


def _latest_terminal_dispatch_for_agent(
    state: TeamState,
    *,
    agent_id: str,
) -> TeamDispatch | None:
    terminal = {
        TeamDispatchStatus.COMPLETED,
        TeamDispatchStatus.EXPIRED,
        TeamDispatchStatus.FAILED,
    }
    records = (state.query_records(
        "dispatches", "agent_id=? AND completed_at IS NOT NULL AND status IN ('completed','failed','expired')",
        (agent_id,), order=DISPATCH_COMPLETED_ORDER, limit=1,
    ) if hasattr(state, "query_records") else state.dispatches.values())
    candidates = [
        dispatch
        for dispatch in records
        if dispatch.agent_id == agent_id
        and dispatch.status in terminal
        and dispatch.completed_at is not None
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda dispatch: (
            _ensure_utc(dispatch.completed_at),
            dispatch.id,
        ),
    )


def _latest_delivered_wake_work_attempt_for_agent(
    state: TeamState,
    *,
    agent_id: str,
    cause_kind: str | None = None,
    work_lineage_id: str | None = None,
    filter_work_lineage: bool = False,
) -> TeamDispatch | None:
    if hasattr(state, "query_records"):
        where = ("agent_id=? AND completed_at IS NOT NULL AND status IN ('completed','failed','expired') "
                 "AND wake_work_fingerprint IS NOT NULL AND (leased_at IS NOT NULL OR activation_completed_at IS NOT NULL)")
        parameters = [agent_id]
        if cause_kind is not None:
            where += " AND cause_kind=?"
            parameters.append(cause_kind)
        if filter_work_lineage:
            where += " AND work_lineage_id IS ?"
            parameters.append(work_lineage_id)
        records = state.query_records("dispatches", where, parameters, order=DISPATCH_ATTEMPT_ORDER, limit=1)
    else:
        records = state.dispatches.values()
    attempts = [
        dispatch
        for dispatch in records
        if dispatch.agent_id == agent_id
        and (cause_kind is None or dispatch.cause_kind == cause_kind)
        and (not filter_work_lineage or dispatch.work_lineage_id == work_lineage_id)
        and dispatch.completed_at is not None
        and dispatch.status
        in {
            TeamDispatchStatus.COMPLETED,
            TeamDispatchStatus.EXPIRED,
            TeamDispatchStatus.FAILED,
        }
        and dispatch.wake_work_fingerprint is not None
        and (dispatch.leased_at is not None or dispatch.activation_completed_at is not None)
    ]
    if not attempts:
        return None
    return max(
        attempts,
        key=lambda dispatch: (
            _ensure_utc(dispatch.leased_at or dispatch.activation_completed_at),
            dispatch.id,
        ),
    )


def _wake_work_changed_since_latest_lineage_attempt(
    state: TeamState,
    *,
    agent_id: str,
    work_lineage_id: str | None,
    inbox_items: Sequence[AgentInboxItem],
    reviews: Sequence[Any],
) -> bool:
    """Compare pending wake work only with attempts from its selected lineage."""

    current_aggregate = wake_work_fingerprint(
        inbox_items=inbox_items,
        reviews=reviews,
    )
    if current_aggregate is None:
        return False
    current_records = wake_work_record_fingerprints(
        inbox_items=inbox_items,
        reviews=reviews,
    )
    current_inbox_ids = {item.id for item in inbox_items}
    current_review_ids = {review.id for review in reviews}
    terminal_statuses = {
        TeamDispatchStatus.COMPLETED,
        TeamDispatchStatus.EXPIRED,
        TeamDispatchStatus.FAILED,
    }
    records = state.dispatches.values() if not hasattr(state, "query_records") else state.query_records(
        "dispatches", """agent_id=? AND work_lineage_id IS ? AND cause_kind!='manager_fallback'
        AND completed_at IS NOT NULL AND status IN ('completed','expired','failed')
        AND (leased_at IS NOT NULL OR activation_completed_at IS NOT NULL) AND (
          (wake_work_record_fingerprints_json!='{}' AND NOT EXISTS(
            SELECT 1 FROM json_each(?) required WHERE NOT EXISTS(
              SELECT 1 FROM json_each(team_dispatches.wake_work_record_fingerprints_json) stored
              WHERE stored.key=required.value))) OR
          (wake_work_record_fingerprints_json='{}' AND ? AND NOT EXISTS(
            SELECT 1 FROM json_each(?) required WHERE NOT EXISTS(
              SELECT 1 FROM team_dispatch_inbox i WHERE i.dispatch_id=team_dispatches.id
              AND i.inbox_item_id=required.value)) AND NOT EXISTS(
            SELECT 1 FROM json_each(?) required WHERE NOT EXISTS(
              SELECT 1 FROM team_dispatch_reviews r WHERE r.dispatch_id=team_dispatches.id
              AND r.review_request_id=required.value))) OR
          (wake_work_record_fingerprints_json='{}' AND wake_work_fingerprint=?
           AND NOT EXISTS(SELECT 1 FROM team_dispatch_inbox i WHERE i.dispatch_id=team_dispatches.id)
           AND NOT EXISTS(SELECT 1 FROM team_dispatch_reviews r WHERE r.dispatch_id=team_dispatches.id)))""",
        (agent_id, work_lineage_id, json.dumps(sorted(current_records)),
         int(bool(current_inbox_ids or current_review_ids)), json.dumps(sorted(current_inbox_ids)),
         json.dumps(sorted(current_review_ids)), current_aggregate),
        order=DISPATCH_ATTEMPT_ORDER, limit=1,
    )
    attempts = [
        dispatch
        for dispatch in records
        if dispatch.agent_id == agent_id
        and dispatch.cause_kind != "manager_fallback"
        and dispatch.work_lineage_id == work_lineage_id
        and dispatch.status in terminal_statuses
        and dispatch.completed_at is not None
        and (dispatch.leased_at is not None or dispatch.activation_completed_at is not None)
        and (
            dispatch.wake_work_record_fingerprints
            or dispatch.wake_work_fingerprint is not None
            or (
                dispatch.cause_kind not in {"blocker", "context_handoff"}
                and (dispatch.inbox_item_ids or dispatch.review_request_ids)
            )
        )
    ]
    matching_attempts = [
        dispatch
        for dispatch in attempts
        if (
            (
                dispatch.wake_work_record_fingerprints
                and set(current_records) <= set(dispatch.wake_work_record_fingerprints)
            )
            or (
                not dispatch.wake_work_record_fingerprints
                and current_inbox_ids <= set(dispatch.inbox_item_ids)
                and current_review_ids <= set(dispatch.review_request_ids)
                and (current_inbox_ids or current_review_ids)
            )
            or (
                not dispatch.wake_work_record_fingerprints
                and not dispatch.inbox_item_ids
                and not dispatch.review_request_ids
                and dispatch.wake_work_fingerprint == current_aggregate
            )
        )
    ]
    if not matching_attempts:
        return True
    previous = max(
        matching_attempts,
        key=lambda dispatch: (
            _ensure_utc(dispatch.leased_at or dispatch.activation_completed_at),
            dispatch.id,
        ),
    )
    if previous.wake_work_record_fingerprints:
        return _wake_record_snapshot_has_additions_or_updates(
            current=current_records,
            previous=previous.wake_work_record_fingerprints,
            current_aggregate=current_aggregate,
            previous_aggregate=previous.wake_work_fingerprint,
        )
    if previous.wake_work_fingerprint == current_aggregate:
        return False

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


def _wake_record_snapshot_has_additions_or_updates(
    *,
    current: Mapping[str, str],
    previous: Mapping[str, str],
    current_aggregate: str | None,
    previous_aggregate: str | None,
) -> bool:
    if previous:
        if set(current) - set(previous):
            return True
        return any(
            previous.get(record_id) != fingerprint for record_id, fingerprint in current.items()
        )
    return current_aggregate != previous_aggregate


def _release_blocker_dispatch_reservations(
    state: TeamState,
    *,
    dispatch_id: str,
    now: datetime,
) -> None:
    for blocker in state.blockers.values():
        if blocker.active_dispatch_id != dispatch_id:
            continue
        blocker.active_dispatch_id = None
        blocker.updated_at = now


def _mark_dispatch_terminal(
    state: TeamState,
    *,
    dispatch_id: str,
    status: TeamDispatchStatus,
    completed_at: datetime,
    error: str | None = None,
) -> None:
    dispatch = state.dispatches.get(dispatch_id)
    if dispatch is None:
        return
    if dispatch.status in {
        TeamDispatchStatus.COMPLETED,
        TeamDispatchStatus.EXPIRED,
        TeamDispatchStatus.FAILED,
    }:
        return
    agent = state.agents.get(dispatch.agent_id)
    if agent is not None:
        _requeue_unacknowledged_in_dispatch_messages(
            state,
            agent=agent,
            completed_dispatch_id=dispatch.id,
        )
    dispatch.status = status
    dispatch.completed_at = completed_at
    dispatch.error = error if status == TeamDispatchStatus.FAILED else None


def _review_mcp_capability_attempt(
    state: TeamState,
    *,
    agent_id: str,
    review_request_ids: tuple[str, ...],
) -> int:
    """Continue exactly one retry only for the same unresolved review scope."""

    normalized_review_ids = sorted(set(review_request_ids))
    if not normalized_review_ids:
        return 1
    previous_attempt = 0
    records = state.dispatches.values() if not hasattr(state, "query_records") else state.query_records(
        "dispatches", "agent_id=? AND json_extract(mcp_capability_receipt_json,'$.verification_status')='missing' "
        "AND id IN (SELECT dispatch_id FROM team_dispatch_reviews WHERE review_request_id=?) "
        "AND (SELECT COUNT(DISTINCT r.review_request_id) FROM team_dispatch_reviews r WHERE r.dispatch_id=team_dispatches.id)=? "
        "AND NOT EXISTS(SELECT 1 FROM team_dispatch_reviews r WHERE r.dispatch_id=team_dispatches.id "
        "AND r.review_request_id NOT IN (" + ",".join("?" for _ in normalized_review_ids) + "))",
        (agent_id, normalized_review_ids[0], len(normalized_review_ids), *normalized_review_ids),
    )
    for dispatch in records:
        receipt = dispatch.mcp_capability_receipt
        if (
            dispatch.agent_id != agent_id
            or sorted(dispatch.review_request_ids) != normalized_review_ids
            or receipt is None
            or receipt.verification_status != TeamDispatchMcpCapabilityStatus.MISSING
        ):
            continue
        previous_attempt = max(previous_attempt, receipt.attempt)
    return min(previous_attempt + 1, MAX_REVIEW_MCP_CAPABILITY_ATTEMPTS)


def _new_review_mcp_capability_receipt(
    state: TeamState,
    *,
    agent_id: str,
    review_request_ids: tuple[str, ...] | list[str],
) -> TeamDispatchMcpCapabilityReceipt | None:
    if not review_request_ids:
        return None
    return TeamDispatchMcpCapabilityReceipt(
        required_tool_names=[REVIEW_DECISION_MCP_TOOL_NAME],
        attempt=_review_mcp_capability_attempt(
            state,
            agent_id=agent_id,
            review_request_ids=tuple(review_request_ids),
        ),
    )


def _fail_dispatch_preparation_in_state(
    state: TeamState,
    *,
    dispatch_id: str,
    error: str,
    completed_at: datetime,
) -> None:
    dispatch = state.dispatches[dispatch_id]
    failed_while_preparing = dispatch.status == TeamDispatchStatus.PREPARING
    finalize_active_git_work_contexts_for_dispatch(
        state,
        agent_id=dispatch.agent_id,
        dispatch_id=dispatch.id,
        now=completed_at,
    )
    _release_blocker_dispatch_reservations(
        state,
        dispatch_id=dispatch.id,
        now=completed_at,
    )
    runtime = state.agent_runtimes.get(dispatch.agent_id)
    if runtime is not None and runtime.active_dispatch_id == dispatch.id:
        runtime.active_dispatch_id = None
        runtime.wake_leased_until = None
        runtime.wake_reason = None
        if dispatch.cause_kind == "context_handoff":
            runtime.context_handoff_preparation_pending = False
            runtime.context_handoff_preparation_status = "failed"
            runtime.next_wake_at = completed_at
        elif failed_while_preparing:
            # Reserving a dispatch advances the ordinary wake interval before
            # repository/worktree preparation runs. If preparation fails
            # before inference, release that interval as well as the lease so
            # transient infrastructure recovery can be picked up immediately.
            # Durable preparation blockers still suppress unchanged causes.
            runtime.next_wake_at = None
        runtime.updated_at = completed_at
    _mark_dispatch_terminal(
        state,
        dispatch_id=dispatch.id,
        status=TeamDispatchStatus.FAILED,
        completed_at=completed_at,
        error=error,
    )


def _runtime_has_active_dispatch_reservation(
    state: TeamState,
    runtime: AgentRuntime,
) -> bool:
    dispatch_id = _optional_text(runtime.active_dispatch_id)
    if dispatch_id is None:
        return False
    dispatch = state.dispatches.get(dispatch_id)
    return dispatch is not None and dispatch.status in {
        TeamDispatchStatus.PREPARING,
        TeamDispatchStatus.LEASED,
    }


__all__ = ["lease_due_agents"]
