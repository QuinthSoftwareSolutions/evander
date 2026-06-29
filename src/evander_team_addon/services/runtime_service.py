from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from .domain import common as _team_common
from ..repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from ..sqlite_store import (
    TeamStateSqliteCommandTransaction,
)
from ..models import (
    AgentContextClearingPolicy,
    AgentRuntime,
    AgentStandDown,
    AgentStandDownSource,
    AgentStatus,
    GovernanceRuntime,
    TeamDispatchActivityStatus,
    TeamInboxPriority,
    TeamState,
    agent_has_project_assignment,
    agent_lead_project_ids,
    agent_project_assignment_payloads,
    agent_project_ids,
    effective_role_permissions,
    task_payload_with_derived,
)
from .domain.action_feedback_payloads import _latest_action_feedback_for_agent
from .domain.agent_access import (
    _agent_for_session,
    _agent_payload,
    _subordinate_agent_payloads,
    _team_ids_with_open_work,
)
from .domain.chat import (
    _append_pending_chat_message_queued_event_for_agent,
    _chat_dispatch_finalization_error,
    _clear_completed_chat_dispatch_lease,
    _mark_chat_dispatch_message_failed,
    _mark_chat_dispatch_message_replied_from_activity,
    _operator_chat_participant_payloads,
)
from .domain.common import (
    _decimal_or_zero,
    _ensure_utc,
    _optional_text,
    _require_present,
    _required_text,
)
from .domain.context_rotation import (
    _mark_context_rotation_pending,
    _maybe_mark_idle_context_rotation_due,
)
from .domain.dispatch_progress import (
    _dispatch_activity_counts_as_progress,
    _dispatch_activity_counts_for_context_rotation,
)
from .domain.governance import (
    _governance_blocked_signal_fingerprints,
    _governance_blocked_team_signals,
    _governance_cost_signal_fingerprints,
    _governance_cost_signals,
    _governance_group_payload,
    _governance_member_team_payload,
    _pending_governance_agenda_for_group,
)
from .domain.manager_fallback import _manager_fallback_summary_payload
from .domain.stand_down import (
    _active_stand_downs_for_agent,
    _agent_subtree_ids,
)
from .domain.wake import (
    _active_tasks_for_agent,
    _append_follow_up_agent_wake_requested_event,
    _case_payloads_for_agent,
    _case_payloads_for_team,
    _pending_inbox_for_agent,
    _release_package_payloads_for_agent,
    _runtime_attention_payloads_for_agent,
    _task_can_wake_owner_for_work,
    _wakeable_inbox_items,
)
from .domain.work_due import (
    _advance_low_priority_batch,
    _clear_low_priority_batch,
    _mark_agent_work_due,
)


def utc_now() -> datetime:
    return _team_common.utc_now()


class TeamRuntimeService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def request_agent_context_rotation(
        self,
        *,
        agent_id: str,
        requested_by: str | None = None,
        request_note: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_requested_by = _optional_text(requested_by)
        normalized_request_note = _optional_text(request_note)

        def _mutate(state: TeamState) -> TeamState:
            now = utc_now()
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            runtime = state.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            _mark_context_rotation_pending(
                state,
                agent=agent,
                runtime=runtime,
                reason="manual",
                requested_by=normalized_requested_by,
                requested_at=now,
                request_note=normalized_request_note,
            )
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def stand_down_agent(
        self,
        *,
        agent_id: str,
        reason: str,
        set_by: str | None = None,
        source: AgentStandDownSource | str = AgentStandDownSource.OPERATOR,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_reason = _required_text(reason, "reason")
        normalized_set_by = _optional_text(set_by) or "operator"
        normalized_source = (
            source
            if isinstance(source, AgentStandDownSource)
            else AgentStandDownSource(str(source))
        )

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            now = utc_now()
            stand_down = AgentStandDown(
                id=f"stand-down-{uuid4().hex}",
                agent_id=agent.id,
                reason=normalized_reason,
                source=normalized_source,
                set_by=normalized_set_by,
                set_at=now,
            )
            state.stand_downs[stand_down.id] = stand_down
            for affected_id in _agent_subtree_ids(state, agent.id):
                runtime = state.agent_runtimes.get(affected_id)
                if runtime is None:
                    continue
                runtime.next_wake_at = None
                runtime.updated_at = now
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def revoke_operator_stand_downs(
        self,
        *,
        agent_id: str,
        revoked_by: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_revoked_by = _optional_text(revoked_by) or "operator"

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.agents, normalized_agent_id, "agent")
            now = utc_now()
            revoked = False
            for stand_down in state.stand_downs.values():
                if (
                    stand_down.agent_id == normalized_agent_id
                    and stand_down.source == AgentStandDownSource.OPERATOR
                    and stand_down.active
                ):
                    stand_down.cleared_by = normalized_revoked_by
                    stand_down.cleared_at = now
                    revoked = True
            if not revoked:
                raise ValueError(f"agent {normalized_agent_id} has no active operator stand-down")
            for affected_id in _agent_subtree_ids(state, normalized_agent_id):
                if _active_stand_downs_for_agent(state, affected_id):
                    continue
                _mark_agent_work_due(state, agent_id=affected_id, now=now)
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def record_context_rotation_dispatch_started(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        thread_id: str | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_thread_id = _optional_text(thread_id)

        def _mutate(state: TeamState) -> TeamState:
            now = utc_now()
            _require_present(state.agents, normalized_agent_id, "agent")
            runtime = _require_present(
                state.agent_runtimes,
                normalized_agent_id,
                "agent runtime",
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError("dispatch_id does not match active team runtime lease")
            if runtime.context_rotation_pending:
                runtime.context_rotation_pending = False
                runtime.last_context_rotated_at = now
                if normalized_thread_id is not None:
                    runtime.last_thread_id = normalized_thread_id
                runtime.updated_at = now
            return state

        return self._repository.update(_mutate)

    def lease_due_agents(
        self,
        *,
        now: datetime | None = None,
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
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_limit = max(0, int(limit))
        low_priority_threshold = max(0, int(low_priority_wake_after_cycles))
        blocked_teams = set(blocked_team_ids or set())
        leases: list[dict[str, Any]] = []
        if normalized_limit == 0:
            return self._repository.read(), leases

        def _mutate(state_model: TeamState) -> TeamState:
            teams_with_open_work = _team_ids_with_open_work(state_model)
            teams_with_lease = set()
            for agent in sorted(state_model.agents.values(), key=lambda item: item.id):
                if agent.status != AgentStatus.ACTIVE or agent.team_id is None:
                    continue
                if normalized_team_id is not None and agent.team_id != normalized_team_id:
                    continue
                if agent.team_id in blocked_teams:
                    continue
                if _active_stand_downs_for_agent(state_model, agent.id):
                    continue
                runtime = state_model.agent_runtimes.get(agent.id)
                if runtime is not None:
                    _clear_completed_chat_dispatch_lease(
                        state_model,
                        runtime,
                        now=effective_now,
                    )
                if (
                    runtime is not None
                    and runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    teams_with_lease.add(agent.team_id)
            for agent in sorted(state_model.agents.values(), key=lambda item: item.id):
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
                runtime = state_model.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                _clear_completed_chat_dispatch_lease(
                    state_model,
                    runtime,
                    now=effective_now,
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue
                active_tasks = _active_tasks_for_agent(state_model, agent.id)
                pending_inbox = _pending_inbox_for_agent(state_model, agent.id)
                wake_inbox = _wakeable_inbox_items(pending_inbox)
                immediate_inbox = [
                    item for item in wake_inbox if item.priority != TeamInboxPriority.LOW
                ]
                low_priority_inbox = [
                    item for item in wake_inbox if item.priority == TeamInboxPriority.LOW
                ]
                action_feedback = _latest_action_feedback_for_agent(state_model, agent.id)
                rotation_pending = runtime.context_rotation_pending
                wakeable_tasks = [
                    task
                    for task in active_tasks
                    if _task_can_wake_owner_for_work(state_model, task)
                ]
                task_work_ready = any(
                    state_model.dependency_status(task.id) == "satisfied" for task in wakeable_tasks
                ) or any(state_model.subtask_counts(task.id)["open"] > 0 for task in wakeable_tasks)
                wake_eligible = (
                    rotation_pending or bool(wake_inbox) or bool(action_feedback) or task_work_ready
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
                    or bool(action_feedback)
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
                    and not rotation_pending
                    and not action_feedback
                    and not due_by_time
                ):
                    continue
                if wake_inbox and not immediate_wake_ready and not low_priority_wake_ready:
                    continue
                if (
                    max_no_progress_count >= 0
                    and runtime.no_progress_count >= max_no_progress_count
                    and not wake_inbox
                    and not rotation_pending
                    and not action_feedback
                ):
                    continue

                dispatch_id = f"team-dispatch-{uuid4().hex}"
                reason = (
                    "inbox"
                    if immediate_inbox
                    else "low_priority_inbox"
                    if low_priority_wake_ready
                    else "action_feedback"
                    if action_feedback
                    else "scheduled"
                )
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
                _clear_low_priority_batch(runtime)
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_reason = reason
                runtime.last_wake_at = effective_now
                runtime.wake_leased_until = effective_now + timedelta(seconds=lease_seconds)
                runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
                runtime.updated_at = effective_now
                leases.append(
                    {
                        "agent": _agent_payload(state_model, agent),
                        "runtime": runtime.model_dump(mode="json"),
                        "dispatch_id": dispatch_id,
                        "reason": reason,
                        "tasks": [
                            task_payload_with_derived(state_model, task) for task in active_tasks
                        ],
                        "cases": _case_payloads_for_agent(state_model, agent.id),
                        "inbox": [item.model_dump(mode="json") for item in pending_inbox],
                        "release_packages": _release_package_payloads_for_agent(
                            state_model,
                            agent,
                        ),
                        "runtime_attention": _runtime_attention_payloads_for_agent(
                            state_model,
                            agent,
                        ),
                        "recent_action_feedback": action_feedback,
                        "operator_chat_participants": _operator_chat_participant_payloads(
                            state_model
                        ),
                        "superior_agent_id": agent.reports_to_agent_id,
                        "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                        "subordinates": _subordinate_agent_payloads(state_model, agent),
                    }
                )
                if agent.team_id is not None:
                    teams_with_lease.add(agent.team_id)

            if normalized_limit and len(leases) >= normalized_limit:
                return state_model

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
                due_by_time = (
                    manager_runtime.next_wake_at is None
                    or _ensure_utc(manager_runtime.next_wake_at) <= effective_now
                )
                if not due_by_time:
                    continue
                dispatch_id = f"team-dispatch-{uuid4().hex}"
                manager_runtime.active_dispatch_id = dispatch_id
                manager_runtime.wake_reason = "manager_fallback"
                manager_runtime.last_wake_at = effective_now
                manager_runtime.wake_leased_until = effective_now + timedelta(seconds=lease_seconds)
                manager_runtime.next_wake_at = effective_now + timedelta(
                    seconds=wake_interval_seconds
                )
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
                _clear_low_priority_batch(manager_runtime)
                teams_with_lease.add(team.id)
                leases.append(
                    {
                        "agent": _agent_payload(state_model, manager),
                        "runtime": manager_runtime.model_dump(mode="json"),
                        "dispatch_id": dispatch_id,
                        "reason": "manager_fallback",
                        "tasks": [
                            task_payload_with_derived(state_model, task)
                            for task in _active_tasks_for_agent(
                                state_model,
                                manager.id,
                            )
                        ],
                        "cases": _case_payloads_for_team(state_model, team.id),
                        "inbox": [
                            item.model_dump(mode="json")
                            for item in _pending_inbox_for_agent(
                                state_model,
                                manager.id,
                            )
                        ],
                        "release_packages": _release_package_payloads_for_agent(
                            state_model,
                            manager,
                        ),
                        "runtime_attention": _runtime_attention_payloads_for_agent(
                            state_model,
                            manager,
                        ),
                        "recent_action_feedback": _latest_action_feedback_for_agent(
                            state_model,
                            manager.id,
                        ),
                        "manager_fallback_summary": _manager_fallback_summary_payload(
                            state_model,
                            team=team,
                            manager=manager,
                            now=effective_now,
                        ),
                        "operator_chat_participants": _operator_chat_participant_payloads(
                            state_model
                        ),
                        "superior_agent_id": manager.reports_to_agent_id,
                        "subordinate_agent_ids": list(manager.subordinate_agent_ids),
                        "subordinates": _subordinate_agent_payloads(state_model, manager),
                    }
                )
            return state_model

        if normalized_limit == 0:
            return self._repository.read(), []
        state = self._repository.update(_mutate)
        return state, leases

    def lease_due_governance_managers(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 900,
        wake_interval_seconds: int = 3600,
        governance_group_id: str | None = None,
        cost_payload: Mapping[str, Any] | None = None,
        cost_warning_ratio: Decimal | str | float = Decimal("0.8"),
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_group_id = _optional_text(governance_group_id)
        normalized_limit = max(0, int(limit))
        warning_ratio = _decimal_or_zero(cost_warning_ratio)
        leases: list[dict[str, Any]] = []

        def _mutate(state_model: TeamState) -> TeamState:
            for group in sorted(state_model.governance_groups.values(), key=lambda item: item.id):
                if normalized_limit and len(leases) >= normalized_limit:
                    break
                if group.archived:
                    continue
                if normalized_group_id is not None and group.id != normalized_group_id:
                    continue
                manager = state_model.agents.get(group.manager_agent_id)
                if manager is None or manager.status != AgentStatus.ACTIVE:
                    continue
                manager_runtime = state_model.agent_runtimes.get(manager.id)
                if (
                    manager_runtime is not None
                    and manager_runtime.wake_leased_until is not None
                    and _ensure_utc(manager_runtime.wake_leased_until) > effective_now
                ):
                    continue
                runtime = state_model.governance_runtimes.setdefault(
                    group.id,
                    GovernanceRuntime(governance_group_id=group.id),
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue

                pending_agenda = _pending_governance_agenda_for_group(
                    state_model,
                    group.id,
                )
                cost_signals = _governance_cost_signals(
                    state_model,
                    group=group,
                    cost_payload=cost_payload,
                    warning_ratio=warning_ratio,
                )
                blocked_team_signals = _governance_blocked_team_signals(
                    state_model,
                    group=group,
                )
                cost_signal_fingerprints = _governance_cost_signal_fingerprints(cost_signals)
                blocked_signal_fingerprints = _governance_blocked_signal_fingerprints(
                    blocked_team_signals
                )
                cost_signals_changed = (
                    cost_signal_fingerprints != runtime.last_cost_signal_fingerprints
                )
                blocked_signals_changed = (
                    blocked_signal_fingerprints != runtime.last_blocked_signal_fingerprints
                )
                changed_cost_signals = cost_signals if cost_signals and cost_signals_changed else []
                changed_blocked_team_signals = (
                    blocked_team_signals if blocked_team_signals and blocked_signals_changed else []
                )
                due_by_interval = (
                    runtime.next_wake_at is None
                    or _ensure_utc(runtime.next_wake_at) <= effective_now
                )
                pending_agenda_ready = bool(pending_agenda) and due_by_interval
                if (
                    not pending_agenda_ready
                    and not changed_cost_signals
                    and not changed_blocked_team_signals
                ):
                    if cost_signals_changed or blocked_signals_changed:
                        runtime.last_cost_signal_fingerprints = cost_signal_fingerprints
                        runtime.last_blocked_signal_fingerprints = blocked_signal_fingerprints
                        runtime.updated_at = effective_now
                    continue

                if pending_agenda_ready:
                    reason = pending_agenda[0].item_type.value
                elif changed_cost_signals:
                    reason = changed_cost_signals[0]["type"]
                else:
                    reason = changed_blocked_team_signals[0]["type"]

                dispatch_id = f"team-governance-dispatch-{uuid4().hex}"
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_reason = reason
                runtime.last_wake_at = effective_now
                runtime.wake_leased_until = effective_now + timedelta(seconds=lease_seconds)
                runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
                runtime.last_cost_signal_fingerprints = cost_signal_fingerprints
                runtime.last_blocked_signal_fingerprints = blocked_signal_fingerprints
                runtime.updated_at = effective_now
                leases.append(
                    {
                        "agent": _agent_payload(state_model, manager),
                        "governance_group": _governance_group_payload(state_model, group),
                        "runtime": runtime.model_dump(mode="json"),
                        "dispatch_id": dispatch_id,
                        "reason": reason,
                        "agenda": [item.model_dump(mode="json") for item in pending_agenda],
                        "member_teams": [
                            _governance_member_team_payload(
                                state_model,
                                team_id,
                                cost_payload=cost_payload,
                            )
                            for team_id in group.member_team_ids
                            if team_id in state_model.teams
                        ],
                        "cost_signals": changed_cost_signals,
                        "blocked_team_signals": changed_blocked_team_signals,
                    }
                )
            return state_model

        if normalized_limit == 0:
            return self._repository.read(), []
        state = self._repository.update(_mutate)
        return state, leases

    def complete_governance_dispatch(
        self,
        *,
        governance_group_id: str,
        dispatch_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")

        def _mutate(state: TeamState) -> TeamState:
            runtime = state.governance_runtimes.get(normalized_group_id)
            if runtime is None or runtime.active_dispatch_id != normalized_dispatch_id:
                return state
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.updated_at = utc_now()
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def finalize_agent_dispatch_from_activity(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        agent_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_agent_id = _optional_text(agent_id)

        def _failure(
            state: TeamState,
            *,
            code: str,
            message: str,
            resolved_agent_id: str | None = None,
        ) -> tuple[TeamState, dict[str, Any]]:
            return state, {
                "success": False,
                "finalized": False,
                "applied": False,
                "progress_made": False,
                "session_id": normalized_session_id,
                "agent_id": resolved_agent_id or normalized_agent_id,
                "dispatch_id": normalized_dispatch_id,
                "revision": state.revision,
                "activity_count": 0,
                "successful_activity_count": 0,
                "failed_activity_count": 0,
                "context_activity": False,
                "events": [],
                "errors": [{"code": code, "message": message}],
            }

        def _transaction(
            payload: dict[str, Any],
            tx: TeamStateSqliteCommandTransaction,
        ) -> tuple[TeamState, dict[str, Any]]:
            state = TeamState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            try:
                agent = _agent_for_session(state, session_id=normalized_session_id)
            except ValueError as exc:
                return _failure(state, code="unknown_session", message=str(exc))
            if normalized_agent_id is not None and agent.id != normalized_agent_id:
                return _failure(
                    state,
                    code="agent_session_mismatch",
                    message=(
                        f"session {normalized_session_id} belongs to agent "
                        f"{agent.id}, not {normalized_agent_id}"
                    ),
                    resolved_agent_id=agent.id,
                )
            runtime = state.agent_runtimes.get(agent.id)
            if runtime is None or runtime.active_dispatch_id != normalized_dispatch_id:
                active_dispatch_id = runtime.active_dispatch_id if runtime is not None else None
                return _failure(
                    state,
                    code="dispatch_mismatch",
                    message=(
                        f"dispatch_id {normalized_dispatch_id} does not match active "
                        f"Team dispatch {active_dispatch_id or 'none'}"
                    ),
                    resolved_agent_id=agent.id,
                )

            all_activities = tx.list_dispatch_activity(dispatch_id=normalized_dispatch_id)
            activities = [
                activity
                for activity in all_activities
                if activity.agent_id == agent.id
                and activity.caller_session_id == normalized_session_id
                and activity.governance_group_id is None
                and activity.assignment_id is None
            ]
            successful_activities = [
                activity
                for activity in activities
                if activity.status == TeamDispatchActivityStatus.SUCCESS
            ]
            failed_activities = [
                activity
                for activity in activities
                if activity.status == TeamDispatchActivityStatus.FAILURE
            ]
            progress_made = any(
                _dispatch_activity_counts_as_progress(activity)
                for activity in successful_activities
            )
            context_activity = any(
                _dispatch_activity_counts_for_context_rotation(activity)
                for activity in successful_activities
            )
            completed_at = utc_now()
            result: dict[str, Any] = {
                "success": True,
                "finalized": True,
                "applied": True,
                "progress_made": progress_made,
                "session_id": normalized_session_id,
                "agent_id": agent.id,
                "dispatch_id": normalized_dispatch_id,
                "activity_count": len(activities),
                "successful_activity_count": len(successful_activities),
                "failed_activity_count": len(failed_activities),
                "ignored_activity_count": len(all_activities) - len(activities),
                "context_activity": context_activity,
                "activity_ids": [activity.id for activity in activities],
                "successful_activity_ids": [activity.id for activity in successful_activities],
                "failed_activity_ids": [activity.id for activity in failed_activities],
                "replied_chat_message_ids": [],
                "failed_chat_message_ids": [],
                "events": [],
                "errors": [],
            }

            chat_dispatch = runtime.wake_reason == "team_chat" or normalized_dispatch_id.startswith(
                "team-chat:"
            )
            if chat_dispatch:
                successful_reply = next(
                    (
                        activity
                        for activity in successful_activities
                        if activity.tool_name == "team_reply_to_chat"
                    ),
                    None,
                )
                if successful_reply is not None:
                    replied_id = _mark_chat_dispatch_message_replied_from_activity(
                        state,
                        dispatch_id=normalized_dispatch_id,
                    )
                    if replied_id is not None:
                        result["replied_chat_message_ids"].append(replied_id)
                else:
                    failed_id = _mark_chat_dispatch_message_failed(
                        state,
                        dispatch_id=normalized_dispatch_id,
                        error=_chat_dispatch_finalization_error(failed_activities),
                    )
                    if failed_id is not None:
                        result["failed_chat_message_ids"].append(failed_id)

            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.no_progress_count = 0 if progress_made else runtime.no_progress_count + 1
            if context_activity:
                runtime.last_progress_at = completed_at
            runtime.updated_at = completed_at
            _maybe_mark_idle_context_rotation_due(
                state,
                agent_id=agent.id,
                now=runtime.updated_at,
            )
            _append_pending_chat_message_queued_event_for_agent(
                state,
                agent=agent,
                completed_dispatch_id=normalized_dispatch_id,
                result=result,
            )
            _append_follow_up_agent_wake_requested_event(
                state,
                agent_id=agent.id,
                result=result,
                now=completed_at,
                completed_dispatch_id=normalized_dispatch_id,
                progress_made=progress_made,
            )

            next_state = state.model_copy(
                update={"revision": state.revision + 1, "updated_at": completed_at},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            finalized_runtime = validated.agent_runtimes[agent.id]
            result["revision"] = validated.revision
            result["no_progress_count"] = finalized_runtime.no_progress_count
            result["last_progress_at"] = (
                finalized_runtime.last_progress_at.isoformat()
                if finalized_runtime.last_progress_at is not None
                else None
            )
            result["context_rotation_pending"] = finalized_runtime.context_rotation_pending
            return validated, result

        return self._repository.store.update_object_with_command_transaction(_transaction)

    def mirror_session_metadata_payload(
        self,
        *,
        state: TeamState,
        agent_id: str,
    ) -> dict[str, Any]:
        agent = _require_present(state.agents, agent_id, "agent")
        runtime = state.agent_runtimes.get(agent_id)
        active_task_ids = [task.id for task in _active_tasks_for_agent(state, agent_id)]
        pending_inbox_ids = [item.id for item in _pending_inbox_for_agent(state, agent_id)]
        role = state.roles.get(agent.role_id)
        active_stand_downs = _active_stand_downs_for_agent(state, agent_id)
        return {
            "team_addon": {
                "agent_id": agent.id,
                "team_id": agent.team_id,
                "project_ids": agent_project_ids(state, agent.id),
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
                "reports_to_agent_id": agent.reports_to_agent_id,
                "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                "active_task_ids": active_task_ids,
                "pending_inbox_item_ids": pending_inbox_ids,
                "stand_down_active": bool(active_stand_downs),
                "effective_stand_downs": [
                    stand_down.model_dump(mode="json") for stand_down in active_stand_downs
                ],
                "context_clearing_policy": (
                    runtime.context_clearing_policy.value
                    if runtime is not None
                    else AgentContextClearingPolicy.OFF.value
                ),
                "auto_context_clearing_enabled": (
                    runtime.auto_context_clearing_enabled if runtime is not None else False
                ),
                "context_rotation_pending": (
                    runtime.context_rotation_pending if runtime is not None else False
                ),
                "last_context_rotated_at": (
                    runtime.last_context_rotated_at.isoformat()
                    if runtime is not None and runtime.last_context_rotated_at is not None
                    else None
                ),
            }
        }
