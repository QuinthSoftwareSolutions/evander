from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
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
from ..sqlite_store import (
    TeamStateSqliteCommandTransaction,
)
from ..models import (
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentRuntime,
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


GIT_WORK_CONTEXT_RETENTION_DAYS = 30
REVIEW_DECISION_MCP_TOOL_NAME = "team_decide_review"
MAX_REVIEW_MCP_CAPABILITY_ATTEMPTS = 2
TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS = 3
_TEAM_LEASE_PLANNING_LOCKS_GUARD = Lock()
_TEAM_LEASE_PLANNING_LOCKS: dict[str, Lock] = {}


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

    def _plan_manager_fallback(
        state_model: TeamState,
        teams_with_open_work: set[str],
        teams_with_lease: set[str],
    ) -> None:
        """Lease managers after ordinary dispatches and role allocations."""
        for team in sorted(state_model.teams.values(), key=lambda item: item.id):
            if normalized_limit and len(leases) >= normalized_limit:
                break
            if team.archived or team.id in blocked_teams or team.id not in teams_with_open_work:
                continue
            if normalized_team_id is not None and team.id != normalized_team_id:
                continue
            if team.id in teams_with_lease:
                continue
            manager = state_model.agents.get(team.manager_agent_id)
            if manager is None or manager.status != AgentStatus.ACTIVE or (manager.id is None):
                continue
            if normalized_project_id is not None and not agent_has_project_assignment(
                state_model,
                manager.id,
                normalized_project_id,
            ):
                continue
            if normalized_agent_id is not None and manager.id != normalized_agent_id:
                continue
            if session_is_busy is not None and session_is_busy(manager.session_id):
                continue
            if (
                manager.id in state_model.agent_runtimes
                and state_model.agent_runtimes[manager.id].wake_leased_until
                and _ensure_utc(state_model.agent_runtimes[manager.id].wake_leased_until)
                > effective_now
            ):
                continue
            if _active_stand_downs_for_agent(state_model, manager.id):
                continue
            manager_runtime = state_model.agent_runtimes.setdefault(
                manager.id,
                AgentRuntime(agent_id=manager.id),
            )
            dispatch_id = f"team-dispatch-{uuid4().hex}"
            manager_tasks = actionable_dispatch_tasks(
                state_model,
                agent=manager,
            )
            manager_active_task_ids = {
                task.id for task in _active_tasks_for_agent(state_model, manager.id)
            }
            manager_inbox = _pending_inbox_for_agent(
                state_model,
                manager.id,
            )
            manager_reviews = actionable_dispatch_reviews(
                _pending_reviews_for_agent(state_model, manager.id)
            )
            manager_blocked_review_ids = mcp_capability_blocked_review_ids(
                state_model,
                agent_id=manager.id,
            )
            if manager_blocked_review_ids:
                manager_inbox = [
                    item
                    for item in manager_inbox
                    if not inbox_references_review_ids(
                        item,
                        manager_blocked_review_ids,
                    )
                ]
                manager_reviews = [
                    review
                    for review in manager_reviews
                    if review.id not in manager_blocked_review_ids
                ]
            manager_low_priority_inbox_ids = {
                item.id for item in manager_inbox if item.priority == TeamInboxPriority.LOW
            }
            manager_blockers = blockers_due_for_agent(
                state_model,
                agent_id=manager.id,
                now=effective_now,
            )
            manager_scope_blockers = blockers_assigned_to_agent(
                state_model,
                agent_id=manager.id,
            )
            manager_owned_review_ids = {review.id for review in manager_reviews}
            manager_owned_inbox_ids = {item.id for item in manager_inbox}
            manager_owned_action_ids = {
                record.id
                for record in state_model.action_records.values()
                if record.agent_id == manager.id
            }
            manager_fallback_selection_summary = _manager_fallback_summary_payload(
                state_model,
                team=team,
                manager=manager,
                now=effective_now,
                limit_rows=False,
                excluded_review_request_ids=(
                    set(manager_blocked_review_ids) | manager_owned_review_ids
                ),
                excluded_inbox_item_ids=manager_owned_inbox_ids,
                excluded_task_ids=manager_active_task_ids,
                excluded_action_record_ids=manager_owned_action_ids,
            )
            if not _manager_fallback_summary_has_actionable_work(
                manager_fallback_selection_summary
            ):
                continue
            filter_manager_work_lineage = (
                manager_runtime.context_clearing_policy == AgentContextClearingPolicy.WORK_LINEAGE
            )
            candidate_lineage_ids = (
                _manager_fallback_lineage_ids(
                    state_model,
                    manager_fallback_selection_summary,
                )
                if filter_manager_work_lineage
                else [None]
            )
            selected_manager_work: (
                tuple[
                    str | None,
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                    list[Any],
                    dict[str, Any],
                    str,
                    dict[str, str],
                ]
                | None
            ) = None
            for candidate_lineage_id in candidate_lineage_ids:
                manager_lineage_excluded_task_ids = (
                    {
                        task.id
                        for task in state_model.tasks.values()
                        if work_lineage_id_for_record(state_model, task) != candidate_lineage_id
                    }
                    if filter_manager_work_lineage
                    else set()
                )
                manager_lineage_excluded_review_ids = (
                    {
                        review.id
                        for review in state_model.review_requests.values()
                        if work_lineage_id_for_record(state_model, review) != candidate_lineage_id
                    }
                    if filter_manager_work_lineage
                    else set()
                )
                manager_lineage_excluded_inbox_ids = (
                    {
                        item.id
                        for item in state_model.inbox.values()
                        if work_lineage_id_for_record(state_model, item) != candidate_lineage_id
                    }
                    if filter_manager_work_lineage
                    else set()
                )
                manager_lineage_excluded_action_ids = (
                    {
                        record.id
                        for record in state_model.action_records.values()
                        if work_lineage_id_for_record(state_model, record) != candidate_lineage_id
                    }
                    if filter_manager_work_lineage
                    else set()
                )

                def _same_manager_lineage(record: object) -> bool:
                    return (
                        not filter_manager_work_lineage
                        or work_lineage_id_for_record(state_model, record) == candidate_lineage_id
                    )

                candidate_manager_tasks = [
                    row for row in manager_tasks if _same_manager_lineage(row)
                ]
                candidate_manager_inbox = [
                    row for row in manager_inbox if _same_manager_lineage(row)
                ]
                candidate_manager_reviews = [
                    row for row in manager_reviews if _same_manager_lineage(row)
                ]
                candidate_manager_blockers = [
                    row for row in manager_blockers if _same_manager_lineage(row)
                ]
                candidate_manager_scope_blockers = [
                    row for row in manager_scope_blockers if _same_manager_lineage(row)
                ]
                candidate_manager_fallback_summary = _manager_fallback_summary_payload(
                    state_model,
                    team=team,
                    manager=manager,
                    now=effective_now,
                    excluded_review_request_ids=(
                        set(manager_blocked_review_ids) | manager_lineage_excluded_review_ids
                    ),
                    excluded_inbox_item_ids=manager_lineage_excluded_inbox_ids,
                    excluded_task_ids=manager_lineage_excluded_task_ids,
                    excluded_action_record_ids=manager_lineage_excluded_action_ids,
                )
                candidate_trigger_summary = _manager_fallback_summary_payload(
                    state_model,
                    team=team,
                    manager=manager,
                    now=effective_now,
                    limit_rows=False,
                    excluded_review_request_ids=(
                        set(manager_blocked_review_ids)
                        | manager_lineage_excluded_review_ids
                        | manager_owned_review_ids
                    ),
                    excluded_inbox_item_ids=(
                        manager_lineage_excluded_inbox_ids | manager_owned_inbox_ids
                    ),
                    excluded_task_ids=(manager_lineage_excluded_task_ids | manager_active_task_ids),
                    excluded_action_record_ids=(
                        manager_lineage_excluded_action_ids | manager_owned_action_ids
                    ),
                )
                if not _manager_fallback_summary_has_actionable_work(candidate_trigger_summary):
                    continue
                candidate_work_fingerprint = manager_fallback_work_fingerprint(
                    candidate_trigger_summary,
                    state=state_model,
                )
                candidate_work_record_fingerprints = manager_fallback_work_record_fingerprints(
                    candidate_trigger_summary,
                    state=state_model,
                )
                latest_manager_attempt = _latest_delivered_wake_work_attempt_for_agent(
                    state_model,
                    agent_id=manager.id,
                    cause_kind="manager_fallback",
                    work_lineage_id=candidate_lineage_id,
                    filter_work_lineage=filter_manager_work_lineage,
                )
                candidate_work_changed = (
                    latest_manager_attempt is None
                    or _wake_record_snapshot_has_additions_or_updates(
                        current=candidate_work_record_fingerprints,
                        previous=(latest_manager_attempt.wake_work_record_fingerprints),
                        current_aggregate=candidate_work_fingerprint,
                        previous_aggregate=(latest_manager_attempt.wake_work_fingerprint),
                    )
                )
                if (
                    max_no_progress_count >= 0
                    and manager_runtime.no_progress_count >= max_no_progress_count
                    and not candidate_work_changed
                ):
                    continue
                due_by_time = (
                    manager_runtime.next_wake_at is None
                    or _ensure_utc(manager_runtime.next_wake_at) <= effective_now
                )
                if not due_by_time and not candidate_work_changed:
                    continue
                selected_manager_work = (
                    candidate_lineage_id,
                    candidate_manager_tasks,
                    candidate_manager_inbox,
                    candidate_manager_reviews,
                    candidate_manager_blockers,
                    candidate_manager_scope_blockers,
                    candidate_manager_fallback_summary,
                    candidate_work_fingerprint,
                    candidate_work_record_fingerprints,
                )
                break
            if selected_manager_work is None:
                continue
            (
                manager_dispatch_work_lineage_id,
                manager_tasks,
                manager_inbox,
                manager_reviews,
                manager_blockers,
                manager_scope_blockers,
                manager_fallback_summary,
                manager_work_fingerprint,
                manager_work_record_fingerprints,
            ) = selected_manager_work
            manager_fallback_routing_authorities = _manager_fallback_review_routing_authorities(
                state_model,
                summary=manager_fallback_summary,
            )
            initial_release_decision_ids = _manager_fallback_initial_release_decision_ids(
                manager_fallback_summary
            )
            manager_fallback_supervision_task_ids = _manager_fallback_supervision_task_ids(
                state_model,
                manager=manager,
                summary=manager_fallback_summary,
            )
            manager_reservation_scope = build_agent_dispatch_scope(
                state_model,
                agent=manager,
                tasks=manager_tasks,
                blockers=manager_blockers,
                reviews=manager_reviews,
                inbox_items=manager_inbox,
            )
            manager_scope = build_agent_dispatch_scope(
                state_model,
                agent=manager,
                tasks=manager_tasks,
                blockers=manager_scope_blockers,
                reviews=manager_reviews,
                inbox_items=manager_inbox,
            )
            initial_release_project_ids = {
                task.project_id
                for deliverable_id in initial_release_decision_ids
                if (deliverable := state_model.deliverables.get(deliverable_id)) is not None
                if (task := state_model.tasks.get(deliverable.task_id)) is not None
            }
            scoped_task_ids = sorted(
                set(manager_scope.task_ids) | set(manager_fallback_supervision_task_ids)
            )
            scoped_project_ids = sorted(
                set(manager_scope.project_ids) | initial_release_project_ids
            )
            scoped_managed_repo_ids = sorted(
                {
                    project.git.managed_repo_id
                    for project_id in scoped_project_ids
                    if (project := state_model.projects.get(project_id)) is not None
                    and project.git is not None
                }
            )
            manager_source_evidence_snapshot_ids = (
                external_source_evidence_snapshot_ids_for_dispatch(
                    state_model,
                    agent=manager,
                    task_ids={
                        *scoped_task_ids,
                        *(
                            state_model.deliverables[deliverable_id].task_id
                            for deliverable_id in initial_release_decision_ids
                            if deliverable_id in state_model.deliverables
                        ),
                    },
                    project_ids=scoped_project_ids,
                    include_team_cases=True,
                )
            )
            has_prior_successful_wake = _has_prior_successful_ordinary_wake(
                state_model,
                manager,
            )
            durable_dispatch = TeamDispatch(
                id=dispatch_id,
                agent_id=manager.id,
                session_id=manager.session_id,
                cause_kind="manager_fallback",
                work_lineage_id=manager_dispatch_work_lineage_id,
                status=TeamDispatchStatus.PREPARING,
                review_routing_authorities=manager_fallback_routing_authorities,
                initial_release_decision_deliverable_ids=(initial_release_decision_ids),
                project_ids=scoped_project_ids,
                managed_repo_ids=scoped_managed_repo_ids,
                created_at=effective_now,
                preparation_expires_at=(
                    effective_now + timedelta(seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS)
                    if defer_activation
                    else None
                ),
                requested_lease_seconds=max(1, int(lease_seconds)),
                task_ids=scoped_task_ids,
                blocker_ids=list(manager_scope.blocker_ids),
                review_request_ids=list(manager_scope.review_request_ids),
                inbox_item_ids=list(manager_scope.inbox_item_ids),
                source_evidence_snapshot_ids=list(manager_source_evidence_snapshot_ids),
                mcp_capability_receipt=_new_review_mcp_capability_receipt(
                    state_model,
                    agent_id=manager.id,
                    review_request_ids=manager_scope.review_request_ids,
                ),
                wake_work_fingerprint=manager_work_fingerprint,
                wake_work_record_fingerprints=manager_work_record_fingerprints,
            )
            state_model.dispatches[dispatch_id] = durable_dispatch
            manager_runtime.active_dispatch_id = dispatch_id
            prepare_case_context(service._repository, state_model, durable_dispatch)
            manager_runtime.wake_reason = "manager_fallback"
            manager_runtime.last_wake_at = effective_now
            manager_runtime.wake_leased_until = (
                None if defer_activation else effective_now + timedelta(seconds=lease_seconds)
            )
            manager_runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
            manager_runtime.updated_at = effective_now
            if (
                manager_runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
                and not manager_runtime.context_rotation_pending
            ):
                _mark_context_rotation_pending(
                    state_model,
                    agent=manager,
                    runtime=manager_runtime,
                    reason="every_wake",
                    requested_by=None,
                    requested_at=effective_now,
                    request_note=None,
                )
            if (
                not manager_low_priority_inbox_ids
                or {item.id for item in manager_inbox if item.priority == TeamInboxPriority.LOW}
                == manager_low_priority_inbox_ids
            ):
                _clear_low_priority_batch(manager_runtime)
            assign_blockers_to_dispatch(
                state_model,
                agent_id=manager.id,
                dispatch_id=dispatch_id,
                blocker_ids=manager_reservation_scope.blocker_ids,
                now=effective_now,
            )
            scoped_blockers = [
                state_model.blockers[blocker_id].model_dump(mode="json")
                for blocker_id in durable_dispatch.blocker_ids
            ]
            dispatch_assessments = TaskAssessmentService(state_model)
            manager_payload = _agent_payload(state_model, manager)
            role_description_fingerprint = agent_role_description_fingerprint(manager_payload)
            durable_dispatch.role_description_fingerprint = role_description_fingerprint
            teams_with_lease.add(team.id)
            leases.append(
                {
                    "agent": manager_payload,
                    "supervisory_overview": TeamReadService(
                        state=state_model,
                        actor=manager,
                    ).supervisory_overview(),
                    "runtime": manager_runtime.model_dump(mode="json"),
                    "role_description_changed": (
                        manager_runtime.last_delivered_role_description_fingerprint
                        != role_description_fingerprint
                    ),
                    "role_description_fingerprint": role_description_fingerprint,
                    "dispatch_id": dispatch_id,
                    "has_prior_successful_wake": has_prior_successful_wake,
                    "reason": "manager_fallback",
                    "dispatch": durable_dispatch.model_dump(mode="json"),
                    "tasks": [
                        task_payload_with_assessment(
                            state_model,
                            task,
                            assessment_service=dispatch_assessments,
                        )
                        for task in manager_tasks
                    ],
                    **_dispatch_case_payloads(
                        state_model,
                        agent=manager,
                        dispatch=durable_dispatch,
                    ),
                    "external_source_evidence_snapshots": [
                        external_source_evidence_snapshot_payload(
                            state_model.external_source_evidence_snapshots[snapshot_id],
                            now=effective_now,
                        )
                        for snapshot_id in durable_dispatch.source_evidence_snapshot_ids
                        if snapshot_id in state_model.external_source_evidence_snapshots
                    ],
                    "case_contexts": dispatch_case_contexts(state_model, dispatch_id),
                    "case_contexts_delta": dispatch_case_contexts(
                        state_model, dispatch_id, delta=True
                    ),
                    "inbox": [item.model_dump(mode="json") for item in manager_inbox],
                    "blockers": scoped_blockers,
                    "reviews": [review.model_dump(mode="json") for review in manager_reviews],
                    "release_packages": _release_package_payloads_for_dispatch(
                        state_model,
                        manager,
                        work_lineage_id=durable_dispatch.work_lineage_id,
                        filter_work_lineage=filter_manager_work_lineage,
                    ),
                    "runtime_attention": _runtime_attention_payloads_for_dispatch(
                        state_model,
                        manager,
                        work_lineage_id=durable_dispatch.work_lineage_id,
                        filter_work_lineage=filter_manager_work_lineage,
                    ),
                    "recent_action_feedback": _action_feedback_for_dispatch(
                        state_model,
                        agent_id=manager.id,
                        work_lineage_id=durable_dispatch.work_lineage_id,
                        filter_work_lineage=filter_manager_work_lineage,
                    ),
                    "work_lineage_chat_sources": (
                        [
                            ref.model_dump(mode="json")
                            for ref in lineage.chat_source_refs
                            if (
                                (conversation := state_model.conversations.get(ref.conversation_id))
                                is not None
                                and conversation.agent_id == manager.id
                            )
                        ]
                        if durable_dispatch.work_lineage_id is not None
                        and (
                            lineage := state_model.work_lineages.get(
                                durable_dispatch.work_lineage_id
                            )
                        )
                        is not None
                        else []
                    ),
                    "manager_fallback_summary": manager_fallback_summary,
                    "operator_chat_participants": _operator_chat_participant_payloads(
                        state_model,
                        agent_id=manager.id,
                    ),
                    "superior_agent_id": manager.reports_to_agent_id,
                    "subordinate_agent_ids": list(manager.subordinate_agent_ids),
                    "subordinates": _subordinate_agent_payloads(state_model, manager),
                }
            )

    def _mutate(state_model: TeamState) -> TeamState:
        teams_with_open_work, teams_with_lease = _reconcile_leasing_state(
            state_model,
            effective_now=effective_now,
            normalized_team_id=normalized_team_id,
            normalized_agent_id=normalized_agent_id,
            blocked_teams=blocked_teams,
            blocker_routing_events=blocker_routing_events,
        )
        role_allocations = plan_role_task_allocations(
            state_model,
            now=effective_now,
            limit=normalized_limit,
            team_id=normalized_team_id,
            project_id=normalized_project_id,
            agent_id=normalized_agent_id,
            blocked_team_ids=blocked_teams,
            max_no_progress_count=max_no_progress_count,
            session_is_busy=session_is_busy,
            respect_wake_interval=respect_wake_interval,
        )
        allocation_order = {
            row["selected_owner_agent_id"]: index
            for index, row in enumerate(role_allocations.values())
        }
        for agent in sorted(
            state_model.agents.values(),
            key=lambda item: (allocation_order.get(item.id, len(allocation_order)), item.id),
        ):
            if normalized_limit and len(leases) >= normalized_limit:
                break
            if agent.status != AgentStatus.ACTIVE:
                continue
            if _active_stand_downs_for_agent(state_model, agent.id):
                continue
            if normalized_team_id is not None and agent.team_id != normalized_team_id:
                continue
            if agent.team_id in blocked_teams:
                continue
            if normalized_project_id is not None and not agent_has_project_assignment(
                state_model, agent.id, normalized_project_id
            ):
                continue
            if normalized_agent_id is not None and agent.id != normalized_agent_id:
                continue
            if session_is_busy is not None and session_is_busy(agent.session_id):
                continue
            runtime = state_model.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            _clear_completed_chat_dispatch_lease(
                state_model,
                runtime,
                now=effective_now,
            )
            if _runtime_has_active_dispatch_reservation(state_model, runtime) or (
                runtime.wake_leased_until is not None
                and _ensure_utc(runtime.wake_leased_until) > effective_now
            ):
                continue
            active_tasks = actionable_dispatch_tasks(
                state_model,
                agent=agent,
            )
            pending_inbox, wake_inbox, pending_reviews = _ordinary_wake_work_for_agent(
                state_model,
                agent.id,
            )
            lineage_context_active = (
                runtime.context_clearing_policy == AgentContextClearingPolicy.WORK_LINEAGE
            )
            if pending_reviews and not lineage_context_active:
                selected_review = pending_reviews[0]
                unselected_review_ids = {review.id for review in pending_reviews[1:]}
                pending_reviews = [selected_review]
                if unselected_review_ids:
                    pending_inbox = [
                        item
                        for item in pending_inbox
                        if not inbox_references_review_ids(
                            item,
                            unselected_review_ids,
                        )
                    ]
                    wake_inbox = _wakeable_inbox_items(pending_inbox)
            due_blockers = blockers_due_for_agent(
                state_model,
                agent_id=agent.id,
                now=effective_now,
            )
            assigned_blockers = blockers_assigned_to_agent(
                state_model,
                agent_id=agent.id,
            )
            preparation_recovery_blockers = _preparation_recovery_blockers(
                due_blockers=due_blockers,
                assigned_blockers=assigned_blockers,
                inbox_items=wake_inbox,
            )
            preparation_recovery_dispatch = bool(preparation_recovery_blockers)
            if preparation_recovery_dispatch:
                recovery_blocker_ids = {blocker.id for blocker in preparation_recovery_blockers}
                active_tasks = []
                pending_reviews = []
                due_blockers = [
                    blocker for blocker in due_blockers if blocker.id in recovery_blocker_ids
                ]
                assigned_blockers = [
                    blocker
                    for blocker in assigned_blockers
                    if blocker.reason_code in EXECUTION_PREPARATION_BLOCKER_REASON_CODES
                ]
                pending_inbox = [
                    item
                    for item in pending_inbox
                    if _inbox_references_blocker_ids(
                        item,
                        recovery_blocker_ids,
                    )
                ]
                wake_inbox = _wakeable_inbox_items(pending_inbox)
            immediate_inbox = [
                item for item in wake_inbox if item.priority != TeamInboxPriority.LOW
            ]
            low_priority_inbox = [
                item for item in wake_inbox if item.priority == TeamInboxPriority.LOW
            ]
            wakeable_low_priority_inbox_ids = {item.id for item in low_priority_inbox}
            action_feedback = (
                []
                if preparation_recovery_dispatch
                else _latest_action_feedback_for_agent(state_model, agent.id)
            )
            active_owned_tasks = _active_tasks_for_agent(
                state_model,
                agent.id,
            )
            no_progress_exhausted = (
                max_no_progress_count >= 0 and runtime.no_progress_count >= max_no_progress_count
            )
            if no_progress_exhausted and (
                active_owned_tasks
                or wake_inbox
                or pending_reviews
                or action_feedback
                or due_blockers
            ):
                exhausted_dispatch = _latest_terminal_dispatch_for_agent(
                    state_model,
                    agent_id=agent.id,
                )
                stall_result: dict[str, Any] = {
                    "events": [],
                    "errors": [],
                }
                open_or_update_agent_execution_stall(
                    state_model,
                    agent_id=agent.id,
                    affected_task_ids=[task.id for task in active_owned_tasks],
                    exhausted_dispatch_id=(
                        exhausted_dispatch.id if exhausted_dispatch is not None else None
                    ),
                    exhausted_dispatch_task_ids=(
                        exhausted_dispatch.task_ids if exhausted_dispatch is not None else []
                    ),
                    no_progress_count=runtime.no_progress_count,
                    max_no_progress_count=max_no_progress_count,
                    result=stall_result,
                    now=effective_now,
                )
                blocker_routing_events.extend(stall_result.get("events") or [])
                active_tasks = actionable_dispatch_tasks(
                    state_model,
                    agent=agent,
                )
            rotation_pending = runtime.context_rotation_pending
            task_work_ready = bool(active_tasks)
            wake_eligible = (
                rotation_pending
                or bool(wake_inbox)
                or bool(pending_reviews)
                or bool(action_feedback)
                or bool(due_blockers)
                or task_work_ready
            )
            if not wake_eligible:
                _clear_low_priority_batch(runtime)
                continue
            due_by_time = (
                not respect_wake_interval
                or runtime.next_wake_at is None
                or _ensure_utc(runtime.next_wake_at) <= effective_now
            )
            immediate_wake_ready = (
                rotation_pending
                or bool(immediate_inbox)
                or bool(pending_reviews)
                or bool(action_feedback)
                or bool(due_blockers)
                or (task_work_ready and due_by_time)
            )
            low_priority_wake_ready = False
            if low_priority_inbox and not immediate_wake_ready:
                low_priority_wake_ready = _advance_low_priority_batch(
                    runtime,
                    now=effective_now,
                    due_after_cycles=low_priority_threshold,
                )
                if not low_priority_wake_ready:
                    runtime.updated_at = effective_now
                    continue
            elif not low_priority_inbox:
                _clear_low_priority_batch(runtime)
            if (
                not wake_inbox
                and not pending_reviews
                and not rotation_pending
                and not action_feedback
                and not due_blockers
                and not due_by_time
            ):
                continue
            if wake_inbox and not immediate_wake_ready and not low_priority_wake_ready:
                continue

            dispatch_work_lineage_id: str | None = None
            if lineage_context_active:
                candidate_lineage_ids = _ordered_work_lineage_ids(
                    state_model,
                    (
                        *preparation_recovery_blockers,
                        *immediate_inbox,
                        *due_blockers,
                        *pending_reviews,
                        *(low_priority_inbox if low_priority_wake_ready else ()),
                        *action_feedback,
                        *active_tasks,
                    ),
                )
                if not candidate_lineage_ids and rotation_pending:
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
                        return (
                            work_lineage_id_for_record(state_model, record) == candidate_lineage_id
                        )

                    candidate_active_tasks = [row for row in active_tasks if _same_lineage(row)]
                    candidate_lineage_inbox = [row for row in pending_inbox if _same_lineage(row)]
                    candidate_lineage_reviews = [
                        row for row in pending_reviews if _same_lineage(row)
                    ]
                    candidate_due_blockers = [row for row in due_blockers if _same_lineage(row)]
                    candidate_assigned_blockers = [
                        row for row in assigned_blockers if _same_lineage(row)
                    ]
                    candidate_action_feedback = [
                        row for row in action_feedback if _same_lineage(row)
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
                            state_model,
                            agent_id=agent.id,
                            work_lineage_id=candidate_lineage_id,
                            inbox_items=candidate_wake_inbox,
                            reviews=candidate_pending_reviews,
                        )
                        if (
                            no_progress_exhausted
                            and not wake_work_changed
                            and not rotation_pending
                            and not candidate_action_feedback
                            and not candidate_due_blockers
                        ):
                            continue
                        dispatch_work_lineage_id = candidate_lineage_id
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
                    continue
                (
                    active_tasks,
                    pending_inbox,
                    wake_inbox,
                    immediate_inbox,
                    low_priority_inbox,
                    pending_reviews,
                    due_blockers,
                    assigned_blockers,
                    action_feedback,
                ) = selected_lineage_work
                preparation_recovery_blockers = [
                    row
                    for row in preparation_recovery_blockers
                    if (work_lineage_id_for_record(state_model, row) == dispatch_work_lineage_id)
                ]
            else:
                wake_work_changed = wake_work_changed_since_latest_attempt(
                    state_model,
                    agent_id=agent.id,
                    inbox_items=wake_inbox,
                    reviews=pending_reviews,
                )
                if (
                    no_progress_exhausted
                    and not wake_work_changed
                    and not rotation_pending
                    and not action_feedback
                    and not due_blockers
                ):
                    continue

            context_handoff_preparation = (
                runtime.context_rotation_pending and runtime.context_handoff_preparation_pending
            )
            dispatch_id = f"team-dispatch-{uuid4().hex}"
            reason = (
                "context_handoff"
                if context_handoff_preparation
                else "blocker"
                if preparation_recovery_dispatch
                else "inbox"
                if immediate_inbox
                else "blocker"
                if due_blockers
                else "review"
                if pending_reviews
                else "low_priority_inbox"
                if low_priority_wake_ready
                else "action_feedback"
                if action_feedback
                else "scheduled"
            )
            cause_id = (
                preparation_recovery_blockers[0].id
                if preparation_recovery_dispatch
                else immediate_inbox[0].id
                if immediate_inbox
                else due_blockers[0].id
                if due_blockers
                else pending_reviews[0].id
                if pending_reviews
                else low_priority_inbox[0].id
                if low_priority_wake_ready and low_priority_inbox
                else _action_feedback_cause_id(action_feedback)
            )
            if context_handoff_preparation:
                reservation_scope = None
                dispatch_scope = None
            elif preparation_recovery_dispatch:
                reservation_scope = build_preparation_recovery_dispatch_scope(
                    blockers=due_blockers,
                    inbox_items=pending_inbox,
                )
                dispatch_scope = build_preparation_recovery_dispatch_scope(
                    blockers=assigned_blockers,
                    inbox_items=pending_inbox,
                )
            else:
                reservation_scope = build_agent_dispatch_scope(
                    state_model,
                    agent=agent,
                    tasks=active_tasks,
                    blockers=due_blockers,
                    reviews=pending_reviews,
                    inbox_items=pending_inbox,
                )
                dispatch_scope = build_agent_dispatch_scope(
                    state_model,
                    agent=agent,
                    tasks=active_tasks,
                    blockers=assigned_blockers,
                    reviews=pending_reviews,
                    inbox_items=pending_inbox,
                )
            has_prior_successful_wake = _has_prior_successful_ordinary_wake(
                state_model,
                agent,
            )
            durable_dispatch = TeamDispatch(
                id=dispatch_id,
                agent_id=agent.id,
                session_id=agent.session_id,
                cause_kind=reason,
                cause_id=cause_id,
                work_lineage_id=dispatch_work_lineage_id,
                status=TeamDispatchStatus.PREPARING,
                created_at=effective_now,
                preparation_expires_at=(
                    effective_now + timedelta(seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS)
                    if defer_activation
                    else None
                ),
                requested_lease_seconds=(
                    120 if reason == "context_handoff" else max(1, int(lease_seconds))
                ),
                action_feedback_dispatch_ids=(
                    [
                        feedback_dispatch_id
                        for feedback in action_feedback
                        if (
                            feedback_dispatch_id := _optional_text(
                                feedback.get("dispatch_id")
                                if isinstance(feedback, Mapping)
                                else None
                            )
                        )
                        is not None
                    ]
                    if reason == "action_feedback"
                    else []
                ),
                mcp_capability_receipt=_new_review_mcp_capability_receipt(
                    state_model,
                    agent_id=agent.id,
                    review_request_ids=(
                        dispatch_scope.review_request_ids if dispatch_scope is not None else ()
                    ),
                ),
                wake_work_fingerprint=(
                    wake_work_fingerprint(
                        inbox_items=wake_inbox,
                        reviews=pending_reviews,
                    )
                    if not context_handoff_preparation and not preparation_recovery_dispatch
                    else None
                ),
                wake_work_record_fingerprints=(
                    wake_work_record_fingerprints(
                        inbox_items=wake_inbox,
                        reviews=pending_reviews,
                    )
                    if not context_handoff_preparation and not preparation_recovery_dispatch
                    else {}
                ),
                **(dispatch_scope.model_fields() if dispatch_scope is not None else {}),
            )
            durable_dispatch.claimed_role_task_ids = [
                task_id
                for task_id in durable_dispatch.task_ids
                if task_id in role_allocations
                and role_allocations[task_id]["selected_owner_agent_id"] == agent.id
            ]
            state_model.dispatches[dispatch_id] = durable_dispatch
            if (
                runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
                and not runtime.context_rotation_pending
            ):
                _mark_context_rotation_pending(
                    state_model,
                    agent=agent,
                    runtime=runtime,
                    reason="every_wake",
                    requested_by=None,
                    requested_at=effective_now,
                    request_note=None,
                )
            if (
                wakeable_low_priority_inbox_ids
                and {item.id for item in low_priority_inbox} == wakeable_low_priority_inbox_ids
            ):
                _clear_low_priority_batch(runtime)
            runtime.active_dispatch_id = dispatch_id
            prepare_case_context(service._repository, state_model, durable_dispatch)
            runtime.wake_reason = reason
            runtime.last_wake_at = effective_now
            runtime.wake_leased_until = (
                None if defer_activation else effective_now + timedelta(seconds=lease_seconds)
            )
            runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
            runtime.updated_at = effective_now
            if reservation_scope is not None:
                assign_blockers_to_dispatch(
                    state_model,
                    agent_id=agent.id,
                    dispatch_id=dispatch_id,
                    blocker_ids=reservation_scope.blocker_ids,
                    now=effective_now,
                )
            scoped_blockers = [
                state_model.blockers[blocker_id].model_dump(mode="json")
                for blocker_id in durable_dispatch.blocker_ids
            ]
            dispatch_assessments = TaskAssessmentService(state_model)
            agent_payload = _agent_payload(state_model, agent)
            role_description_fingerprint = agent_role_description_fingerprint(agent_payload)
            durable_dispatch.role_description_fingerprint = role_description_fingerprint
            leases.append(
                {
                    "agent": agent_payload,
                    "supervisory_overview": TeamReadService(
                        state=state_model,
                        actor=agent,
                    ).supervisory_overview(),
                    "runtime": runtime.model_dump(mode="json"),
                    "role_description_changed": (
                        runtime.last_delivered_role_description_fingerprint
                        != role_description_fingerprint
                    ),
                    "role_description_fingerprint": role_description_fingerprint,
                    "dispatch_id": dispatch_id,
                    "has_prior_successful_wake": has_prior_successful_wake,
                    "reason": reason,
                    "dispatch": durable_dispatch.model_dump(mode="json"),
                    "tasks": [
                        task_payload_with_assessment(
                            state_model,
                            state_model.tasks[task_id],
                            assessment_service=dispatch_assessments,
                        )
                        for task_id in durable_dispatch.task_ids
                        if task_id in state_model.tasks
                    ],
                    "projects": [
                        {
                            **project.model_dump(mode="json"),
                            "blocking_sources": project_blocking_sources(
                                state_model,
                                project.id,
                                assessment_service=dispatch_assessments,
                            ),
                        }
                        for project in sorted(
                            (
                                state_model.projects[visible_project_id]
                                for visible_project_id in durable_dispatch.project_ids
                                if visible_project_id in state_model.projects
                            ),
                            key=lambda item: item.id,
                        )
                    ],
                    "outcome_kinds": [
                        {
                            **definition,
                            "effective_project_id": visible_project_id,
                        }
                        for visible_project_id in durable_dispatch.project_ids
                        if visible_project_id in state_model.projects
                        for definition in effective_outcome_kind_payloads(
                            state_model,
                            team_id=state_model.projects[visible_project_id].team_id,
                            project_id=visible_project_id,
                        )
                    ],
                    **_dispatch_case_payloads(
                        state_model,
                        agent=agent,
                        dispatch=durable_dispatch,
                    ),
                    "external_source_evidence_snapshots": [
                        external_source_evidence_snapshot_payload(
                            state_model.external_source_evidence_snapshots[snapshot_id],
                            now=effective_now,
                        )
                        for snapshot_id in durable_dispatch.source_evidence_snapshot_ids
                        if snapshot_id in state_model.external_source_evidence_snapshots
                    ],
                    "case_contexts": dispatch_case_contexts(state_model, dispatch_id),
                    "case_contexts_delta": dispatch_case_contexts(
                        state_model, dispatch_id, delta=True
                    ),
                    "inbox": [item.model_dump(mode="json") for item in pending_inbox],
                    "blockers": scoped_blockers,
                    "reviews": [review.model_dump(mode="json") for review in pending_reviews],
                    "release_packages": _release_package_payloads_for_dispatch(
                        state_model,
                        agent,
                        work_lineage_id=durable_dispatch.work_lineage_id,
                        filter_work_lineage=lineage_context_active,
                    ),
                    "runtime_attention": _runtime_attention_payloads_for_dispatch(
                        state_model,
                        agent,
                        work_lineage_id=durable_dispatch.work_lineage_id,
                        filter_work_lineage=lineage_context_active,
                    ),
                    "recent_action_feedback": action_feedback,
                    "work_lineage_chat_sources": (
                        [
                            ref.model_dump(mode="json")
                            for ref in lineage.chat_source_refs
                            if (
                                (conversation := state_model.conversations.get(ref.conversation_id))
                                is not None
                                and conversation.agent_id == agent.id
                            )
                        ]
                        if durable_dispatch.work_lineage_id is not None
                        and (
                            lineage := state_model.work_lineages.get(
                                durable_dispatch.work_lineage_id
                            )
                        )
                        is not None
                        else []
                    ),
                    "operator_chat_participants": _operator_chat_participant_payloads(
                        state_model,
                        agent_id=agent.id,
                    ),
                    "superior_agent_id": agent.reports_to_agent_id,
                    "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                    "subordinates": _subordinate_agent_payloads(state_model, agent),
                }
            )
            if agent.team_id is not None:
                teams_with_lease.add(agent.team_id)

        for task_id, allocation in role_allocations.items():
            assignment_lease = next(
                (
                    lease
                    for lease in leases
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
            blocker_routing_events.append(
                {
                    "event_type": "team.task.role_assigned",
                    "correlation_key": task_id,
                    "idempotency_key": f"role-assignment:{assignment_lease['dispatch_id']}:{task_id}",
                    "payload": allocation,
                }
            )

        _plan_manager_fallback(
            state_model,
            teams_with_open_work,
            teams_with_lease,
        )
        return state_model

    def _plan_and_commit() -> TeamState:
        last_conflict: TeamStateConflictError | None = None
        for _attempt in range(TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS):
            leases.clear()
            blocker_routing_events.clear()

            planning_state = service._repository.read()
            previous_payload = planning_state.model_dump(mode="json")
            candidate = _mutate(planning_state)
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

            try:
                return service._repository.commit_prepared_state_with_command_transaction(
                    previous_payload=previous_payload,
                    candidate=candidate,
                    command_writer=_write_commands,
                )
            except TeamStateConflictError as exc:
                last_conflict = exc
        assert last_conflict is not None
        raise last_conflict

    with _team_lease_planning_lock(service._repository.store.path):
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


def _team_lease_planning_lock(state_db_path: object) -> Lock:
    key = str(Path(str(state_db_path)).resolve())
    with _TEAM_LEASE_PLANNING_LOCKS_GUARD:
        lock = _TEAM_LEASE_PLANNING_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _TEAM_LEASE_PLANNING_LOCKS[key] = lock
        return lock


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
    candidates = [
        dispatch
        for dispatch in state.dispatches.values()
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
    attempts = [
        dispatch
        for dispatch in state.dispatches.values()
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
    attempts = [
        dispatch
        for dispatch in state.dispatches.values()
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
    for dispatch in state.dispatches.values():
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
