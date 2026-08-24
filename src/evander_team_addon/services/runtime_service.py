from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Callable, Literal
from uuid import uuid4

from evander.events.internal import (
    InternalEventBus,
    InternalEventSource,
    internal_event_store_path,
)

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
    AgentInboxItem,
    AgentRuntime,
    AgentStandDown,
    AgentStandDownSource,
    AgentStatus,
    GovernanceRuntime,
    PersistentAgent,
    TaskAssessmentReadiness,
    TeamBlockerRoute,
    TeamBlocker,
    TeamBlockerState,
    TeamDispatch,
    TeamDispatchActivityStatus,
    TeamDispatchActivity,
    TeamDispatchMcpCapabilityReceipt,
    TeamDispatchMcpCapabilityStatus,
    TeamDispatchStatus,
    TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    agent_has_project_assignment,
    agent_lead_project_ids,
    agent_project_assignment_payloads,
    agent_project_ids,
    effective_role_permissions,
    external_source_evidence_snapshot_payload,
    wake_work_changed_since_latest_attempt,
    wake_work_fingerprint,
    wake_work_record_fingerprints,
)
from .domain.action_feedback_payloads import _latest_action_feedback_for_agent
from .domain.task_assessment import task_payload_with_assessment
from .domain.task_assessment import TaskAssessmentService
from .domain.agent_access import (
    _agent_for_session,
    _agent_payload,
    _subordinate_agent_payloads,
    _team_ids_with_open_work,
    agent_role_description_fingerprint,
)
from .domain.chat import (
    _append_pending_chat_message_queued_event_for_agent,
    _chat_dispatch_finalization_error,
    _clear_completed_chat_dispatch_lease,
    _mark_chat_dispatch_message_failed,
    _mark_chat_dispatch_message_replied_from_activity,
    _operator_chat_participant_payloads,
)
from .domain.blockers import (
    assign_blockers_to_dispatch,
    blockers_assigned_to_agent,
    blockers_due_for_agent,
    complete_blocker_dispatches,
    open_agent_mcp_capability_blocker,
    open_or_update_agent_execution_stall,
    reconcile_unavailable_blocker_assignments,
)
from .domain.common import (
    _ensure_utc,
    _optional_text,
    _require_present,
    _required_text,
)
from .domain.context_rotation import (
    _mark_context_rotation_pending,
    _maybe_mark_idle_context_rotation_due,
)
from .domain.dispatch_scope import (
    _assigned_blocker_recovery_tasks,
    actionable_dispatch_reviews,
    actionable_dispatch_tasks,
    build_agent_dispatch_scope,
    build_preparation_recovery_dispatch_scope,
    external_source_evidence_snapshot_ids_for_dispatch,
    inbox_references_review_ids,
    mcp_capability_blocked_review_ids,
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
from .domain.git_work_contexts import (
    finalize_active_git_work_contexts_for_dispatch,
    reconcile_git_work_context_lifecycle,
)
from .domain.manager_fallback import (
    _manager_fallback_review_routing_authority_error,
    _manager_fallback_review_routing_authorities,
    _manager_fallback_initial_release_decision_ids,
    _manager_fallback_summary_payload,
    _manager_fallback_summary_has_actionable_work,
    manager_fallback_work_fingerprint,
    manager_fallback_work_record_fingerprints,
)
from .domain.initial_release import (
    manager_fallback_initial_release_authority_error,
)
from .domain.project_status import project_blocking_sources
from .domain.outcome_catalog import effective_outcome_kind_payloads
from .domain.preparation_failures import (
    EXECUTION_PREPARATION_BLOCKER_REASON_CODES,
    reconcile_stale_task_preparation_blockers,
)
from .domain.stand_down import (
    _active_stand_downs_for_agent,
    _normalize_stand_down_scope,
    _stand_down_affected_agent_ids,
)
from .domain.wake import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
    _active_tasks_for_agent,
    _agent_immediate_wake_request,
    _append_agent_wake_requested_event,
    _append_follow_up_agent_wake_requested_event,
    _case_payloads_for_agent,
    _case_payloads_for_team,
    _ordinary_wake_work_for_agent,
    _pending_inbox_for_agent,
    _pending_reviews_for_agent,
    _release_package_payloads_for_agent,
    _runtime_attention_payloads_for_agent,
    _wakeable_inbox_items,
)
from .domain.work_due import (
    _advance_low_priority_batch,
    _agent_has_open_execution_stall,
    _clear_low_priority_batch,
    _mark_agent_work_due,
)
from .commands.outbox import _team_event_outbox_records


GIT_WORK_CONTEXT_RETENTION_DAYS = 30
MAX_AGENT_DISPATCH_LEASE_RENEWAL_SECONDS = 900
REVIEW_DECISION_MCP_TOOL_NAME = "team_decide_review"
MAX_REVIEW_MCP_CAPABILITY_ATTEMPTS = 2
logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return _team_common.utc_now()


class TeamRuntimeService:
    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        event_bus: InternalEventBus | None = None,
        event_bus_factory: Callable[[], InternalEventBus] | None = None,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._event_bus_factory = event_bus_factory

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
        scope: Literal["agent", "subtree"] | str = "subtree",
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
        normalized_scope = _normalize_stand_down_scope(scope)

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            now = utc_now()
            stand_down = AgentStandDown(
                id=f"stand-down-{uuid4().hex}",
                agent_id=agent.id,
                scope=normalized_scope,
                reason=normalized_reason,
                source=normalized_source,
                set_by=normalized_set_by,
                set_at=now,
            )
            state.stand_downs[stand_down.id] = stand_down
            for affected_id in _stand_down_affected_agent_ids(
                state,
                agent_id=agent.id,
                scope=normalized_scope,
            ):
                runtime = state.agent_runtimes.get(affected_id)
                if runtime is None:
                    continue
                runtime.next_wake_at = None
                runtime.updated_at = now
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def revoke_stand_downs(
        self,
        *,
        agent_id: str,
        revoked_by: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_revoked_by = _optional_text(revoked_by) or "operator"
        wake_result: dict[str, Any] = {"events": []}

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.agents, normalized_agent_id, "agent")
            now = utc_now()
            revoked_ids: list[str] = []
            affected_ids: set[str] = set()
            for stand_down in state.stand_downs.values():
                if (
                    stand_down.agent_id == normalized_agent_id
                    and stand_down.source
                    in {
                        AgentStandDownSource.OPERATOR,
                        AgentStandDownSource.SUPERIOR,
                    }
                    and stand_down.active
                ):
                    affected_ids.update(
                        _stand_down_affected_agent_ids(
                            state,
                            agent_id=normalized_agent_id,
                            scope=stand_down.scope,
                        )
                    )
                    stand_down.cleared_by = normalized_revoked_by
                    stand_down.cleared_at = now
                    revoked_ids.append(stand_down.id)
            if not revoked_ids:
                raise ValueError(f"agent {normalized_agent_id} has no active direct stand-down")
            idempotency_suffix = "stand-down-revocation:" + ",".join(sorted(revoked_ids))
            for affected_id in sorted(affected_ids):
                if _active_stand_downs_for_agent(state, affected_id):
                    continue
                _mark_agent_work_due(state, agent_id=affected_id, now=now)
                wake = _agent_immediate_wake_request(
                    state,
                    agent_id=affected_id,
                    now=now,
                )
                if wake is None:
                    continue
                reason, _delay_seconds = wake
                _append_agent_wake_requested_event(
                    state,
                    agent_id=affected_id,
                    result=wake_result,
                    reason=reason,
                    now=now,
                    delay_seconds=0,
                    idempotency_suffix=idempotency_suffix,
                )
            return state

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(wake_result.get("events") or []),
            expected_revision=expected_revision,
        )
        self._publish_stand_down_revocation_wakes(wake_result.get("events"))
        return state

    def revoke_operator_stand_downs(
        self,
        *,
        agent_id: str,
        revoked_by: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        """Compatibility alias for the privileged operator stand-down route."""
        return self.revoke_stand_downs(
            agent_id=agent_id,
            revoked_by=revoked_by,
            expected_revision=expected_revision,
        )

    def _publish_stand_down_revocation_wakes(self, events: object) -> None:
        if not isinstance(events, list) or not events:
            return
        records_by_idempotency_key = {
            record.idempotency_key: record
            for record in self._repository.list_outbox_records(
                intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE
            )
            if record.idempotency_key is not None
        }
        bus = self._event_bus
        if bus is None and self._event_bus_factory is not None:
            bus = self._event_bus_factory()
        if bus is None:
            bus = InternalEventBus(internal_event_store_path(self._repository.runtime_dir))
        for event in events:
            if not isinstance(event, Mapping):
                continue
            payload = event.get("payload")
            payload_mapping = dict(payload) if isinstance(payload, Mapping) else {}
            idempotency_key = _optional_text(event.get("idempotency_key"))
            try:
                bus.publish(
                    event_type=_required_text(event.get("event_type"), "event_type"),
                    payload=payload_mapping,
                    correlation_key=_optional_text(event.get("correlation_key")),
                    idempotency_key=idempotency_key,
                    source=InternalEventSource(
                        source_type="team-addon",
                        source_name="team-addon",
                    ),
                )
            except Exception:
                logger.exception(
                    "failed to publish immediate wake after privileged stand-down revocation"
                )
                continue
            outbox_record = records_by_idempotency_key.get(idempotency_key)
            if outbox_record is None or outbox_record.status.value == "completed":
                continue
            try:
                self._repository.complete_outbox_record(record_id=outbox_record.id)
            except Exception:
                logger.exception(
                    "published privileged stand-up wake but failed to acknowledge outbox record %s",
                    outbox_record.id,
                )

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

    def record_agent_context_handoff(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        summary: str,
    ) -> TeamState:
        """Persist one advisory handoff and release the fresh-context rotation."""
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_summary = " ".join(_required_text(summary, "summary").split())
        if len(normalized_summary) > 1200:
            raise ValueError("summary must be at most 1200 characters")

        def _mutate(state: TeamState) -> TeamState:
            runtime = _require_present(state.agent_runtimes, normalized_agent_id, "agent runtime")
            dispatch = _require_present(state.dispatches, normalized_dispatch_id, "dispatch")
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError("context handoff dispatch belongs to another agent")
            if dispatch.cause_kind != "context_handoff":
                raise ValueError("agent context handoff requires a context_handoff dispatch")
            if dispatch.status != TeamDispatchStatus.LEASED:
                raise ValueError("agent context handoff dispatch is not active")
            if runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError("context handoff dispatch no longer owns the runtime lease")
            if not runtime.context_handoff_preparation_pending:
                raise ValueError("agent context handoff is no longer pending")
            now = utc_now()
            runtime.last_agent_handoff_summary = normalized_summary
            runtime.last_agent_handoff_at = now
            runtime.context_handoff_preparation_pending = False
            runtime.context_handoff_preparation_status = "recorded"
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.next_wake_at = now
            runtime.updated_at = now
            _mark_dispatch_terminal(
                state,
                dispatch_id=dispatch.id,
                status=TeamDispatchStatus.COMPLETED,
                completed_at=now,
            )
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
        session_is_busy: Callable[[str], bool] | None = None,
        defer_activation: bool = False,
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_limit = max(0, int(limit))
        low_priority_threshold = max(0, int(low_priority_wake_after_cycles))
        blocked_teams = set(blocked_team_ids or set())
        leases: list[dict[str, Any]] = []
        blocker_routing_events: list[dict[str, Any]] = []
        idle_rotation_result: dict[str, Any] = {"events": []}
        if normalized_limit == 0:
            return self._repository.read(), leases

        def _mutate(state_model: TeamState) -> TeamState:
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
                                "dispatch preparation reservation expired before durable "
                                "prompt delivery"
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
                if pending_reviews:
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
                    max_no_progress_count >= 0
                    and runtime.no_progress_count >= max_no_progress_count
                )
                wake_work_changed = wake_work_changed_since_latest_attempt(
                    state_model,
                    agent_id=agent.id,
                    inbox_items=wake_inbox,
                    reviews=pending_reviews,
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
                _clear_low_priority_batch(runtime)
                runtime.active_dispatch_id = dispatch_id
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
                agent_payload = _agent_payload(state_model, agent)
                role_description_fingerprint = agent_role_description_fingerprint(agent_payload)
                durable_dispatch.role_description_fingerprint = role_description_fingerprint
                leases.append(
                    {
                        "agent": agent_payload,
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
                        "cases": _case_payloads_for_agent(state_model, agent.id),
                        "external_source_evidence_snapshots": [
                            external_source_evidence_snapshot_payload(
                                state_model.external_source_evidence_snapshots[snapshot_id],
                                now=effective_now,
                            )
                            for snapshot_id in durable_dispatch.source_evidence_snapshot_ids
                            if snapshot_id in state_model.external_source_evidence_snapshots
                        ],
                        "inbox": [item.model_dump(mode="json") for item in pending_inbox],
                        "blockers": scoped_blockers,
                        "reviews": [review.model_dump(mode="json") for review in pending_reviews],
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
                manager_blockers = blockers_due_for_agent(
                    state_model,
                    agent_id=manager.id,
                    now=effective_now,
                )
                manager_fallback_summary = _manager_fallback_summary_payload(
                    state_model,
                    team=team,
                    manager=manager,
                    now=effective_now,
                    excluded_review_request_ids=manager_blocked_review_ids,
                )
                if not _manager_fallback_summary_has_actionable_work(manager_fallback_summary):
                    continue
                manager_fallback_trigger_summary = _manager_fallback_summary_payload(
                    state_model,
                    team=team,
                    manager=manager,
                    now=effective_now,
                    limit_rows=False,
                    excluded_review_request_ids=(
                        set(manager_blocked_review_ids) | {review.id for review in manager_reviews}
                    ),
                    excluded_inbox_item_ids={item.id for item in manager_inbox},
                    excluded_task_ids=manager_active_task_ids,
                    excluded_action_record_ids={
                        record.id
                        for record in state_model.action_records.values()
                        if record.agent_id == manager.id
                    },
                )
                if not _manager_fallback_summary_has_actionable_work(
                    manager_fallback_trigger_summary
                ):
                    continue
                manager_work_fingerprint = manager_fallback_work_fingerprint(
                    manager_fallback_trigger_summary,
                    state=state_model,
                )
                manager_work_record_fingerprints = manager_fallback_work_record_fingerprints(
                    manager_fallback_trigger_summary,
                    state=state_model,
                )
                latest_manager_attempt = _latest_delivered_wake_work_attempt_for_agent(
                    state_model,
                    agent_id=manager.id,
                    cause_kind="manager_fallback",
                )
                if (
                    max_no_progress_count >= 0
                    and manager_runtime.no_progress_count >= max_no_progress_count
                    and latest_manager_attempt is not None
                    and not _wake_record_snapshot_has_additions_or_updates(
                        current=manager_work_record_fingerprints,
                        previous=latest_manager_attempt.wake_work_record_fingerprints,
                        current_aggregate=manager_work_fingerprint,
                        previous_aggregate=latest_manager_attempt.wake_work_fingerprint,
                    )
                ):
                    continue
                due_by_time = (
                    manager_runtime.next_wake_at is None
                    or _ensure_utc(manager_runtime.next_wake_at) <= effective_now
                )
                if not due_by_time and latest_manager_attempt is not None:
                    if not _wake_record_snapshot_has_additions_or_updates(
                        current=manager_work_record_fingerprints,
                        previous=latest_manager_attempt.wake_work_record_fingerprints,
                        current_aggregate=manager_work_fingerprint,
                        previous_aggregate=latest_manager_attempt.wake_work_fingerprint,
                    ):
                        continue
                manager_fallback_routing_authorities = _manager_fallback_review_routing_authorities(
                    state_model,
                    summary=manager_fallback_summary,
                )
                initial_release_decision_ids = _manager_fallback_initial_release_decision_ids(
                    manager_fallback_summary
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
                    blockers=blockers_assigned_to_agent(
                        state_model,
                        agent_id=manager.id,
                    ),
                    reviews=manager_reviews,
                    inbox_items=manager_inbox,
                )
                initial_release_project_ids = {
                    task.project_id
                    for deliverable_id in initial_release_decision_ids
                    if (deliverable := state_model.deliverables.get(deliverable_id)) is not None
                    if (task := state_model.tasks.get(deliverable.task_id)) is not None
                }
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
                            *manager_scope.task_ids,
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
                    task_ids=list(manager_scope.task_ids),
                    blocker_ids=list(manager_scope.blocker_ids),
                    review_request_ids=list(manager_scope.review_request_ids),
                    inbox_item_ids=list(manager_scope.inbox_item_ids),
                    source_evidence_snapshot_ids=list(
                        manager_source_evidence_snapshot_ids
                    ),
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
                manager_runtime.wake_reason = "manager_fallback"
                manager_runtime.last_wake_at = effective_now
                manager_runtime.wake_leased_until = (
                    None if defer_activation else effective_now + timedelta(seconds=lease_seconds)
                )
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
                manager_payload = _agent_payload(state_model, manager)
                role_description_fingerprint = agent_role_description_fingerprint(manager_payload)
                durable_dispatch.role_description_fingerprint = role_description_fingerprint
                teams_with_lease.add(team.id)
                leases.append(
                    {
                        "agent": manager_payload,
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
                            task_payload_with_assessment(state_model, task)
                            for task in manager_tasks
                        ],
                        "cases": _case_payloads_for_team(state_model, team.id),
                        "external_source_evidence_snapshots": [
                            external_source_evidence_snapshot_payload(
                                state_model.external_source_evidence_snapshots[snapshot_id],
                                now=effective_now,
                            )
                            for snapshot_id in durable_dispatch.source_evidence_snapshot_ids
                            if snapshot_id in state_model.external_source_evidence_snapshots
                        ],
                        "inbox": [item.model_dump(mode="json") for item in manager_inbox],
                        "blockers": scoped_blockers,
                        "reviews": [review.model_dump(mode="json") for review in manager_reviews],
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
            return state_model

        if normalized_limit == 0:
            return self._repository.read(), []

        def _transaction(
            state_model: TeamState,
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            next_state = _mutate(state_model)
            for record in _team_event_outbox_records(blocker_routing_events):
                tx.enqueue_outbox_record(record)
            for lease in leases:
                lease_agent = lease["agent"]
                tx.append_dispatch_activity(
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
            return next_state

        state = self._repository.update_with_command_transaction(_transaction)
        if not defer_activation:
            for lease in leases:
                state = self.mark_agent_dispatch_leased(
                    agent_id=lease["agent"]["id"],
                    dispatch_id=lease["dispatch_id"],
                    lease_seconds=lease_seconds,
                    now=effective_now,
                )
                lease["runtime"] = state.agent_runtimes[lease["agent"]["id"]].model_dump(
                    mode="json"
                )
                lease["dispatch"] = state.dispatches[lease["dispatch_id"]].model_dump(mode="json")
        return state, leases

    def mark_agent_dispatch_queued(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        now: datetime | None = None,
    ) -> TeamState:
        """Record that a PREPARING reservation is backed by a durable prompt."""

        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(state.dispatches, normalized_dispatch_id, "dispatch")
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError(
                    f"dispatch {dispatch.id} belongs to agent {dispatch.agent_id}, "
                    f"not {normalized_agent_id}"
                )
            if dispatch.status != TeamDispatchStatus.PREPARING:
                if dispatch.status == TeamDispatchStatus.LEASED:
                    return state
                raise ValueError(
                    f"dispatch {dispatch.id} is {dispatch.status.value}, not preparing"
                )
            runtime = _require_present(
                state.agent_runtimes,
                normalized_agent_id,
                "agent runtime",
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError(
                    f"dispatch {dispatch.id} no longer owns the runtime reservation "
                    f"for agent {normalized_agent_id}"
                )
            dispatch.queued_at = dispatch.queued_at or effective_now
            dispatch.preparation_expires_at = effective_now + timedelta(
                seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS
            )
            runtime.updated_at = effective_now
            return state

        return self._repository.update(_mutate)

    def activate_delivered_agent_dispatch(
        self,
        *,
        dispatch_id: str,
        thread_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        """Atomically lease one delivered prompt and apply its durable bookkeeping.

        Reconciliation may call this again after a process restart. A dispatch
        that was leased by an older worker but lacks ``activation_completed_at``
        is repaired in the same transaction; a fully activated dispatch is a
        no-op.
        """

        # Keep the Chat authorization dependency local to delivered Chat
        # activation; ordinary Team runtime paths do not otherwise need it.
        from .chat_service import team_chat_mutation_allowed

        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_thread_id = _optional_text(thread_id)
        effective_now = _ensure_utc(now or utc_now())
        preparation_error: list[str] = []
        activation: dict[str, Any] = {
            "delivered_action_feedback_dispatch_ids": [],
            "context_rotation": None,
            "context_handoff_preparation": None,
        }

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(state.dispatches, normalized_dispatch_id, "dispatch")
            agent = _require_present(state.agents, dispatch.agent_id, "agent")
            runtime = _require_present(state.agent_runtimes, dispatch.agent_id, "agent runtime")
            context_handoff_preparation = dispatch.cause_kind == "context_handoff"
            activation["delivered_action_feedback_dispatch_ids"] = list(
                dispatch.action_feedback_dispatch_ids
            )
            if context_handoff_preparation:
                activation["context_handoff_preparation"] = {
                    "status": "started",
                    "timeout_seconds": dispatch.requested_lease_seconds,
                }

            if dispatch.status == TeamDispatchStatus.PREPARING:
                if runtime.active_dispatch_id != dispatch.id:
                    error = (
                        f"dispatch {dispatch.id} no longer owns the runtime reservation "
                        f"for agent {dispatch.agent_id}"
                    )
                    preparation_error.append(error)
                    _fail_dispatch_preparation_in_state(
                        state,
                        dispatch_id=dispatch.id,
                        error=error,
                        completed_at=effective_now,
                    )
                    return state
                if dispatch.cause_kind != "team_chat":
                    stale_errors = _dispatch_staleness_errors(state, dispatch=dispatch)
                    if stale_errors:
                        error = "dispatch scope became stale during preparation: " + "; ".join(
                            stale_errors
                        )
                        preparation_error.append(error)
                        _fail_dispatch_preparation_in_state(
                            state,
                            dispatch_id=dispatch.id,
                            error=error,
                            completed_at=effective_now,
                        )
                        return state
                lease_expires_at = effective_now + timedelta(
                    seconds=max(1, int(dispatch.requested_lease_seconds))
                )
                dispatch.status = TeamDispatchStatus.LEASED
                dispatch.preparation_expires_at = None
                dispatch.prepared_at = effective_now
                dispatch.leased_at = effective_now
                dispatch.lease_expires_at = lease_expires_at
                dispatch.completed_at = None
                dispatch.error = None
                runtime.wake_leased_until = lease_expires_at
            elif dispatch.status != TeamDispatchStatus.LEASED:
                raise ValueError(f"dispatch {dispatch.id} is {dispatch.status.value}, not active")
            elif runtime.active_dispatch_id != dispatch.id:
                raise ValueError(
                    f"dispatch {dispatch.id} no longer owns the runtime lease "
                    f"for agent {dispatch.agent_id}"
                )

            if dispatch.activation_completed_at is not None:
                return state

            rotation_allowed = not context_handoff_preparation
            if dispatch.cause_kind == "team_chat":
                source_message = state.conversation_messages.get(dispatch.cause_id or "")
                if source_message is None:
                    source_message = next(
                        (
                            state.conversation_messages[message_id]
                            for message_id in dispatch.chat_message_ids
                            if message_id in state.conversation_messages
                        ),
                        None,
                    )
                mutation_allowed = bool(
                    source_message is not None
                    and team_chat_mutation_allowed(source_message, dispatch)
                )
                rotation_allowed = mutation_allowed
                for message_id in dispatch.chat_message_ids:
                    message = state.conversation_messages.get(message_id)
                    if message is None:
                        continue
                    if message.status == "dispatching":
                        message.status = "running"
                    if mutation_allowed:
                        message.metadata["ordinary_context_seeded"] = True
                    message.updated_at = effective_now
                if mutation_allowed:
                    role_fingerprint = dispatch.role_description_fingerprint or (
                        agent_role_description_fingerprint(_agent_payload(state, agent))
                    )
                    runtime.last_delivered_role_description_fingerprint = role_fingerprint
            else:
                if not context_handoff_preparation:
                    role_fingerprint = dispatch.role_description_fingerprint or (
                        agent_role_description_fingerprint(_agent_payload(state, agent))
                    )
                    runtime.last_delivered_role_description_fingerprint = role_fingerprint
                for source_dispatch_id in dispatch.action_feedback_dispatch_ids:
                    for record in state.action_records.values():
                        if (
                            record.agent_id == dispatch.agent_id
                            and record.dispatch_id == source_dispatch_id
                            and not record.success
                            and record.feedback_delivered_at is None
                        ):
                            record.feedback_delivered_at = effective_now
                            record.feedback_delivered_by_dispatch_id = dispatch.id

            if runtime.context_rotation_pending and rotation_allowed:
                context_rotation_reason = runtime.context_rotation_reason
                runtime.context_rotation_pending = False
                runtime.last_context_rotated_at = effective_now
                if normalized_thread_id is not None:
                    runtime.last_thread_id = normalized_thread_id
                activation["context_rotation"] = {
                    "rotated": True,
                    "reason": context_rotation_reason,
                }

            dispatch.activation_completed_at = effective_now
            runtime.updated_at = effective_now
            return state

        state = self._repository.update(_mutate)
        if preparation_error:
            raise RuntimeError(preparation_error[0])
        return state, activation

    def mark_agent_dispatch_session_metadata_mirrored(
        self,
        *,
        dispatch_id: str,
        now: datetime | None = None,
    ) -> TeamState:
        """Acknowledge an idempotent external session-metadata mirror."""

        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(state.dispatches, normalized_dispatch_id, "dispatch")
            if dispatch.activation_completed_at is None:
                raise ValueError(f"dispatch {dispatch.id} activation bookkeeping is not complete")
            dispatch.session_metadata_mirrored_at = effective_now
            return state

        return self._repository.update(_mutate)

    def mark_agent_dispatch_leased(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())
        normalized_lease_seconds = max(1, int(lease_seconds))
        preparation_error: list[str] = []

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(
                state.dispatches,
                normalized_dispatch_id,
                "dispatch",
            )
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError(
                    f"dispatch {dispatch.id} belongs to agent {dispatch.agent_id}, "
                    f"not {normalized_agent_id}"
                )
            if dispatch.status == TeamDispatchStatus.LEASED:
                return state
            if dispatch.status != TeamDispatchStatus.PREPARING:
                raise ValueError(
                    f"dispatch {dispatch.id} is {dispatch.status.value}, not preparing"
                )
            runtime = _require_present(
                state.agent_runtimes,
                normalized_agent_id,
                "agent runtime",
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                error = (
                    f"dispatch {dispatch.id} no longer owns the runtime reservation "
                    f"for agent {normalized_agent_id}"
                )
                preparation_error.append(error)
                _fail_dispatch_preparation_in_state(
                    state,
                    dispatch_id=dispatch.id,
                    error=error,
                    completed_at=effective_now,
                )
                return state
            stale_errors = _dispatch_staleness_errors(state, dispatch=dispatch)
            if stale_errors:
                error = "dispatch scope became stale during preparation: " + "; ".join(stale_errors)
                preparation_error.append(error)
                _fail_dispatch_preparation_in_state(
                    state,
                    dispatch_id=dispatch.id,
                    error=error,
                    completed_at=effective_now,
                )
                return state
            dispatch.status = TeamDispatchStatus.LEASED
            dispatch.preparation_expires_at = None
            dispatch.prepared_at = effective_now
            dispatch.leased_at = effective_now
            dispatch.lease_expires_at = effective_now + timedelta(seconds=normalized_lease_seconds)
            dispatch.completed_at = None
            dispatch.error = None
            runtime.wake_leased_until = dispatch.lease_expires_at
            runtime.updated_at = effective_now
            return state

        state = self._repository.update(_mutate)
        if preparation_error:
            raise RuntimeError(preparation_error[0])
        return state

    def confirm_agent_dispatch_mcp_capability_grant(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        granted_tool_names: list[str] | tuple[str, ...],
        now: datetime | None = None,
    ) -> TeamState:
        """Open the evidence window only after the policy grant is confirmed."""

        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_granted_tools = {
            _required_text(tool_name, "granted_tool_names") for tool_name in granted_tool_names
        }
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(
                state.dispatches,
                normalized_dispatch_id,
                "dispatch",
            )
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError(
                    f"dispatch {dispatch.id} belongs to agent {dispatch.agent_id}, "
                    f"not {normalized_agent_id}"
                )
            if dispatch.status not in {
                TeamDispatchStatus.PREPARING,
                TeamDispatchStatus.LEASED,
            }:
                raise ValueError(f"dispatch {dispatch.id} is {dispatch.status.value}, not active")
            runtime = _require_present(
                state.agent_runtimes,
                normalized_agent_id,
                "agent runtime",
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError(
                    f"dispatch {dispatch.id} no longer owns the runtime reservation "
                    f"for agent {normalized_agent_id}"
                )
            receipt = dispatch.mcp_capability_receipt
            if receipt is None:
                return state
            missing_tools = sorted(set(receipt.required_tool_names) - normalized_granted_tools)
            if missing_tools:
                raise ValueError(
                    "confirmed MCP policy grant omitted required tool(s): "
                    + ", ".join(missing_tools)
                )
            receipt.grant_confirmed_at = effective_now
            return state

        return self._repository.update(_mutate)

    def record_agent_role_description_delivered(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        role_description_fingerprint: str,
        now: datetime | None = None,
    ) -> TeamState:
        """Record the description that was successfully sent in a Team prompt."""

        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_fingerprint = _required_text(
            role_description_fingerprint,
            "role_description_fingerprint",
        )
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(state.dispatches, normalized_dispatch_id, "dispatch")
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError(
                    f"dispatch {dispatch.id} belongs to agent {dispatch.agent_id}, "
                    f"not {normalized_agent_id}"
                )
            runtime = _require_present(state.agent_runtimes, normalized_agent_id, "agent runtime")
            runtime.last_delivered_role_description_fingerprint = normalized_fingerprint
            runtime.updated_at = effective_now
            return state

        return self._repository.update(_mutate)

    def renew_agent_dispatch_lease(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        now: datetime | None = None,
        lease_seconds: int = MAX_AGENT_DISPATCH_LEASE_RENEWAL_SECONDS,
        renew_within_seconds: int = 300,
    ) -> tuple[TeamState, bool]:
        """Boundedly renew one still-active durable dispatch lease.

        The caller is responsible for proving that the exact execution backing
        the dispatch is alive. This method only renews the matching durable
        dispatch/runtime pair and never revives a terminal or replaced
        dispatch.
        """

        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())
        normalized_lease_seconds = min(
            MAX_AGENT_DISPATCH_LEASE_RENEWAL_SECONDS,
            max(1, int(lease_seconds)),
        )
        normalized_renew_within_seconds = max(0, int(renew_within_seconds))
        renewal_due_at = effective_now + timedelta(seconds=normalized_renew_within_seconds)

        def _renewal_due(state: TeamState) -> bool:
            dispatch = state.dispatches.get(normalized_dispatch_id)
            runtime = state.agent_runtimes.get(normalized_agent_id)
            if (
                dispatch is None
                or runtime is None
                or dispatch.agent_id != normalized_agent_id
                or dispatch.status != TeamDispatchStatus.LEASED
                or runtime.active_dispatch_id != normalized_dispatch_id
                or dispatch.lease_expires_at is None
                or runtime.wake_leased_until is None
            ):
                return False
            return (
                _ensure_utc(dispatch.lease_expires_at) <= renewal_due_at
                or _ensure_utc(runtime.wake_leased_until) <= renewal_due_at
            )

        current = self._repository.read()
        if not _renewal_due(current):
            return current, False

        renewed: list[bool] = []

        def _mutate(state: TeamState) -> TeamState:
            if not _renewal_due(state):
                return state
            dispatch = state.dispatches[normalized_dispatch_id]
            runtime = state.agent_runtimes[normalized_agent_id]
            renewed_until = effective_now + timedelta(seconds=normalized_lease_seconds)
            dispatch.lease_expires_at = renewed_until
            runtime.wake_leased_until = renewed_until
            runtime.updated_at = effective_now
            renewed.append(True)
            return state

        state = self._repository.update(
            _mutate,
            expected_revision=current.revision,
        )
        return state, bool(renewed)

    def fail_agent_dispatch(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        error: str,
        now: datetime | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_error = _required_text(error, "error")
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(
                state.dispatches,
                normalized_dispatch_id,
                "dispatch",
            )
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError(
                    f"dispatch {dispatch.id} belongs to agent {dispatch.agent_id}, "
                    f"not {normalized_agent_id}"
                )
            if dispatch.status in {
                TeamDispatchStatus.COMPLETED,
                TeamDispatchStatus.EXPIRED,
                TeamDispatchStatus.FAILED,
            }:
                return state
            _fail_dispatch_preparation_in_state(
                state,
                dispatch_id=dispatch.id,
                error=normalized_error,
                completed_at=effective_now,
            )
            return state

        return self._repository.update(_mutate)

    def record_agent_mcp_capability_preparation_failure(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        agent_id: str | None = None,
        diagnostic: str,
        expected_revision: int | None = None,
        now: datetime | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        """Fail a pre-execution capability check without completing dispatch scope."""

        normalized_session_id = _required_text(session_id, "session_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_agent_id = _optional_text(agent_id)
        normalized_diagnostic = _required_text(diagnostic, "diagnostic")
        effective_now = _ensure_utc(now or utc_now())
        result: dict[str, Any] = {}

        def _mutate(
            state: TeamState,
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            agent = _agent_for_session(state, session_id=normalized_session_id)
            if normalized_agent_id is not None and agent.id != normalized_agent_id:
                raise ValueError(
                    f"session {normalized_session_id} belongs to agent {agent.id}, "
                    f"not {normalized_agent_id}"
                )
            dispatch = _require_present(
                state.dispatches,
                normalized_dispatch_id,
                "dispatch",
            )
            if dispatch.agent_id != agent.id:
                raise ValueError(
                    f"dispatch {dispatch.id} belongs to agent {dispatch.agent_id}, not {agent.id}"
                )
            if dispatch.status not in {
                TeamDispatchStatus.PREPARING,
                TeamDispatchStatus.LEASED,
            }:
                raise ValueError(f"dispatch {dispatch.id} is {dispatch.status.value}, not active")
            runtime = _require_present(
                state.agent_runtimes,
                agent.id,
                "agent runtime",
            )
            if runtime.active_dispatch_id != dispatch.id:
                raise ValueError(
                    f"dispatch {dispatch.id} no longer owns the runtime reservation "
                    f"for agent {agent.id}"
                )
            receipt = dispatch.mcp_capability_receipt
            if receipt is None:
                raise ValueError(f"dispatch {dispatch.id} has no MCP capability receipt")

            receipt.verification_status = TeamDispatchMcpCapabilityStatus.MISSING
            receipt.missing_tool_names = list(receipt.required_tool_names)
            receipt.diagnostic = normalized_diagnostic
            dispatch.mcp_capability_receipt = receipt
            error = "Required review MCP tool unavailable: " + ", ".join(
                receipt.required_tool_names
            )

            # This dispatch never executed. Release only its reservations; do
            # not complete blocker scope or submit/abandon Git work contexts.
            _release_blocker_dispatch_reservations(
                state,
                dispatch_id=dispatch.id,
                now=effective_now,
            )
            _mark_dispatch_terminal(
                state,
                dispatch_id=dispatch.id,
                status=TeamDispatchStatus.FAILED,
                completed_at=effective_now,
                error=error,
            )
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.next_wake_at = None
            runtime.updated_at = effective_now

            result.update(
                {
                    "success": True,
                    "finalized": True,
                    "applied": True,
                    "progress_made": False,
                    "pre_execution_failure": True,
                    "session_id": normalized_session_id,
                    "agent_id": agent.id,
                    "dispatch_id": dispatch.id,
                    "activity_count": 0,
                    "successful_activity_count": 0,
                    "failed_activity_count": 0,
                    "context_activity": False,
                    "mcp_capability_receipt": receipt.model_dump(mode="json"),
                    "events": [],
                    "errors": [],
                }
            )
            _route_or_retry_review_mcp_capability_failure(
                state,
                agent_id=agent.id,
                dispatch=dispatch,
                receipt=receipt,
                diagnostic=normalized_diagnostic,
                result=result,
                now=effective_now,
            )
            stored_outbox_records = [
                tx.enqueue_outbox_record(record)
                for record in _team_event_outbox_records(result.get("events") or [])
            ]
            if stored_outbox_records:
                result["outbox_record_ids"] = [record.id for record in stored_outbox_records]
            state.revision += 1
            return state

        state = self._repository.update_with_command_transaction(
            _mutate,
            expected_revision=expected_revision,
        )
        result["revision"] = state.revision
        result["no_progress_count"] = state.agent_runtimes[result["agent_id"]].no_progress_count
        return state, result

    def lease_due_governance_managers(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 900,
        wake_interval_seconds: int = 3600,
        governance_group_id: str | None = None,
        cost_payload: Mapping[str, Any] | None = None,
        defer_activation: bool = False,
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_group_id = _optional_text(governance_group_id)
        normalized_limit = max(0, int(limit))
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
                if manager_runtime is not None and (
                    _runtime_has_active_dispatch_reservation(
                        state_model,
                        manager_runtime,
                    )
                    or (
                        manager_runtime.wake_leased_until is not None
                        and _ensure_utc(manager_runtime.wake_leased_until) > effective_now
                    )
                ):
                    continue
                runtime = state_model.governance_runtimes.setdefault(
                    group.id,
                    GovernanceRuntime(governance_group_id=group.id),
                )
                if (
                    runtime.active_dispatch_id is not None
                    and runtime.preparation_expires_at is not None
                    and _ensure_utc(runtime.preparation_expires_at) <= effective_now
                ):
                    runtime.active_dispatch_id = None
                    runtime.preparation_expires_at = None
                    runtime.wake_leased_until = None
                    runtime.wake_reason = None
                    runtime.next_wake_at = None
                    runtime.updated_at = effective_now
                if runtime.active_dispatch_id is not None or (
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
                runtime.preparation_expires_at = (
                    effective_now + timedelta(seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS)
                    if defer_activation
                    else None
                )
                runtime.queued_at = None
                runtime.requested_lease_seconds = max(1, int(lease_seconds))
                runtime.wake_reason = reason
                runtime.last_wake_at = effective_now
                runtime.wake_leased_until = (
                    None if defer_activation else effective_now + timedelta(seconds=lease_seconds)
                )
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

    def mark_governance_dispatch_queued(
        self,
        *,
        governance_group_id: str,
        dispatch_id: str,
        now: datetime | None = None,
    ) -> TeamState:
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            runtime = _require_present(
                state.governance_runtimes,
                normalized_group_id,
                "governance runtime",
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError(
                    f"governance dispatch {normalized_dispatch_id} no longer owns its reservation"
                )
            runtime.queued_at = runtime.queued_at or effective_now
            runtime.preparation_expires_at = effective_now + timedelta(
                seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS
            )
            runtime.updated_at = effective_now
            return state

        return self._repository.update(_mutate)

    def activate_governance_dispatch(
        self,
        *,
        governance_group_id: str,
        dispatch_id: str,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> TeamState:
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            runtime = _require_present(
                state.governance_runtimes,
                normalized_group_id,
                "governance runtime",
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError(
                    f"governance dispatch {normalized_dispatch_id} no longer owns its reservation"
                )
            effective_lease_seconds = max(
                1,
                int(runtime.requested_lease_seconds if lease_seconds is None else lease_seconds),
            )
            runtime.preparation_expires_at = None
            runtime.wake_leased_until = effective_now + timedelta(seconds=effective_lease_seconds)
            runtime.updated_at = effective_now
            return state

        return self._repository.update(_mutate)

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
            runtime.preparation_expires_at = None
            runtime.queued_at = None
            runtime.wake_leased_until = None
            runtime.updated_at = utc_now()
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def release_agent_wake_lease_for_retry(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        error: str = "dispatch preparation was interrupted and will be retried",
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_error = _required_text(error, "error")

        def _mutate(state: TeamState) -> TeamState:
            runtime = state.agent_runtimes.get(normalized_agent_id)
            dispatch = state.dispatches.get(normalized_dispatch_id)
            if dispatch is None:
                raise ValueError(f"dispatch {normalized_dispatch_id} does not exist")
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError(
                    f"dispatch {normalized_dispatch_id} belongs to agent "
                    f"{dispatch.agent_id}, not {normalized_agent_id}"
                )
            if dispatch.status in {
                TeamDispatchStatus.COMPLETED,
                TeamDispatchStatus.EXPIRED,
                TeamDispatchStatus.FAILED,
            }:
                return state
            if runtime is None or runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError(
                    f"dispatch {normalized_dispatch_id} no longer owns the wake lease "
                    f"for agent {normalized_agent_id}"
                )
            completed_at = utc_now()
            _fail_dispatch_preparation_in_state(
                state,
                dispatch_id=normalized_dispatch_id,
                error=normalized_error,
                completed_at=completed_at,
            )
            runtime.next_wake_at = None
            return state

        return self._repository.update(_mutate)

    def reconcile_git_work_contexts(
        self,
        *,
        now: datetime | None = None,
        prune_before: datetime | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, int]]:
        effective_now = _ensure_utc(now or utc_now())
        effective_prune_before = _ensure_utc(prune_before) if prune_before is not None else None
        result: dict[str, int] = {}

        def _mutate(state: TeamState) -> TeamState:
            result.update(
                reconcile_git_work_context_lifecycle(
                    state,
                    now=effective_now,
                    prune_before=effective_prune_before,
                )
            )
            return state

        state = self._repository.update(
            _mutate,
            expected_revision=expected_revision,
        )
        return state, result

    def finalize_agent_dispatch_from_activity(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        agent_id: str | None = None,
        expected_revision: int | None = None,
        mcp_capability_observation: Mapping[str, Any] | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_agent_id = _optional_text(agent_id)
        normalized_mcp_capability_observation = (
            dict(mcp_capability_observation)
            if isinstance(mcp_capability_observation, Mapping)
            else None
        )

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
            state: TeamState,
            tx: TeamStateSqliteCommandTransaction,
        ) -> tuple[TeamState, dict[str, Any]]:
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
            durable_dispatch = state.dispatches.get(normalized_dispatch_id)
            if (
                durable_dispatch is not None
                and durable_dispatch.status != TeamDispatchStatus.LEASED
            ):
                return _failure(
                    state,
                    code="dispatch_not_leased",
                    message=(
                        f"dispatch {normalized_dispatch_id} is "
                        f"{durable_dispatch.status.value}, not leased"
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

            capability_missing = False
            capability_error: str | None = None
            receipt = (
                durable_dispatch.mcp_capability_receipt if durable_dispatch is not None else None
            )
            if receipt is not None and normalized_mcp_capability_observation is not None:
                receipt_payload = receipt.model_dump(mode="json")
                receipt_payload.update(
                    {
                        key: normalized_mcp_capability_observation[key]
                        for key in (
                            "verification_status",
                            "observed_transport_session_id",
                            "observed_at",
                            "observed_tool_fingerprint",
                            "missing_tool_names",
                            "diagnostic",
                        )
                        if key in normalized_mcp_capability_observation
                    }
                )
                receipt = TeamDispatchMcpCapabilityReceipt.model_validate(receipt_payload)
                durable_dispatch.mcp_capability_receipt = receipt
                result["mcp_capability_receipt"] = receipt.model_dump(mode="json")
                capability_missing = (
                    receipt.verification_status == TeamDispatchMcpCapabilityStatus.MISSING
                )
                if capability_missing:
                    capability_error = "Required review MCP tool unavailable: " + ", ".join(
                        receipt.missing_tool_names or receipt.required_tool_names
                    )

            chat_dispatch = runtime.wake_reason == "team_chat" or normalized_dispatch_id.startswith(
                "team-chat:"
            )
            chat_terminal_error: str | None = None
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
                    replied_ids = _mark_chat_dispatch_message_replied_from_activity(
                        state,
                        dispatch_id=normalized_dispatch_id,
                    )
                    result["replied_chat_message_ids"].extend(replied_ids)
                else:
                    chat_terminal_error = _chat_dispatch_finalization_error(failed_activities)
                    failed_ids = _mark_chat_dispatch_message_failed(
                        state,
                        dispatch_id=normalized_dispatch_id,
                        error=chat_terminal_error,
                    )
                    result["failed_chat_message_ids"].extend(failed_ids)

            complete_blocker_dispatches(
                state,
                agent_id=agent.id,
                dispatch_id=normalized_dispatch_id,
                result=result,
                now=completed_at,
            )
            finalize_active_git_work_contexts_for_dispatch(
                state,
                agent_id=agent.id,
                dispatch_id=normalized_dispatch_id,
                now=completed_at,
            )
            _mark_dispatch_terminal(
                state,
                dispatch_id=normalized_dispatch_id,
                status=(
                    TeamDispatchStatus.FAILED
                    if chat_terminal_error is not None or capability_error is not None
                    else TeamDispatchStatus.COMPLETED
                ),
                completed_at=completed_at,
                error=chat_terminal_error or capability_error,
            )
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            if (
                durable_dispatch is not None
                and durable_dispatch.cause_kind == "context_handoff"
                and runtime.context_handoff_preparation_pending
            ):
                runtime.context_handoff_preparation_pending = False
                runtime.context_handoff_preparation_status = "failed"
                runtime.next_wake_at = completed_at
            if not capability_missing:
                if progress_made:
                    if not _agent_has_open_execution_stall(
                        state,
                        agent_id=agent.id,
                    ):
                        runtime.no_progress_count = 0
                else:
                    runtime.no_progress_count += 1
            if context_activity:
                runtime.last_progress_at = completed_at
            runtime.updated_at = completed_at
            _maybe_mark_idle_context_rotation_due(
                state,
                agent_id=agent.id,
                now=runtime.updated_at,
                result=result,
            )
            _append_pending_chat_message_queued_event_for_agent(
                state,
                agent=agent,
                completed_dispatch_id=normalized_dispatch_id,
                result=result,
            )
            if capability_missing and receipt is not None:
                _route_or_retry_review_mcp_capability_failure(
                    state,
                    agent_id=agent.id,
                    dispatch=durable_dispatch,
                    receipt=receipt,
                    diagnostic=receipt.diagnostic or capability_error or "",
                    result=result,
                    now=completed_at,
                )
            else:
                _append_follow_up_agent_wake_requested_event(
                    state,
                    agent_id=agent.id,
                    result=result,
                    now=completed_at,
                    completed_dispatch_id=normalized_dispatch_id,
                    progress_made=progress_made,
                )
            stored_outbox_records = [
                tx.enqueue_outbox_record(record)
                for record in _team_event_outbox_records(result.get("events") or [])
            ]
            if stored_outbox_records:
                result["outbox_record_ids"] = [record.id for record in stored_outbox_records]

            state.revision += 1
            state.updated_at = completed_at
            finalized_runtime = state.agent_runtimes[agent.id]
            result["revision"] = state.revision
            result["no_progress_count"] = finalized_runtime.no_progress_count
            result["last_progress_at"] = (
                finalized_runtime.last_progress_at.isoformat()
                if finalized_runtime.last_progress_at is not None
                else None
            )
            result["context_rotation_pending"] = finalized_runtime.context_rotation_pending
            tx.append_dispatch_activity(
                TeamDispatchActivity(
                    id=f"activity-{uuid4().hex}",
                    tool_name="team_dispatch_finished",
                    caller_session_id=normalized_session_id,
                    caller_identity=f"agent:{agent.id}",
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id,
                    status=TeamDispatchActivityStatus.SUCCESS,
                    progress_classification=("progress" if progress_made else "none"),
                    created_at=completed_at,
                    updated_at=completed_at,
                )
            )
            return state, result

        return self._repository.store.update_state_with_command_transaction(_transaction)

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
                "context_clearing_idle_grace_seconds": (
                    runtime.context_clearing_idle_grace_seconds if runtime is not None else 0
                ),
                "context_idle_rotation_due_at": (
                    runtime.context_idle_rotation_due_at.isoformat()
                    if runtime is not None and runtime.context_idle_rotation_due_at is not None
                    else None
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


def _action_feedback_cause_id(
    action_feedback: list[dict[str, Any]],
) -> str | None:
    if not action_feedback:
        return None
    return _optional_text(action_feedback[0].get("action_record_id"))


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
) -> TeamDispatch | None:
    attempts = [
        dispatch
        for dispatch in state.dispatches.values()
        if dispatch.agent_id == agent_id
        and (cause_kind is None or dispatch.cause_kind == cause_kind)
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


def _review_dispatch_task_ids(state: TeamState, dispatch: TeamDispatch) -> list[str]:
    task_ids: set[str] = set()
    for review_id in dispatch.review_request_ids:
        review = state.review_requests.get(review_id)
        if review is None:
            continue
        deliverable = state.deliverables.get(review.deliverable_id)
        if deliverable is not None:
            task_ids.add(deliverable.task_id)
    return sorted(task_ids)


def _route_or_retry_review_mcp_capability_failure(
    state: TeamState,
    *,
    agent_id: str,
    dispatch: TeamDispatch,
    receipt: TeamDispatchMcpCapabilityReceipt,
    diagnostic: str,
    result: dict[str, Any],
    now: datetime,
) -> None:
    if receipt.attempt < MAX_REVIEW_MCP_CAPABILITY_ATTEMPTS:
        _append_agent_wake_requested_event(
            state,
            agent_id=agent_id,
            result=result,
            reason="review_capability_retry",
            now=now,
            delay_seconds=0,
            idempotency_suffix=f"review-capability-retry:{dispatch.id}",
        )
        result["mcp_capability_retry_scheduled"] = True
        return
    open_agent_mcp_capability_blocker(
        state,
        agent_id=agent_id,
        review_request_ids=dispatch.review_request_ids,
        affected_task_ids=_review_dispatch_task_ids(state, dispatch),
        dispatch_id=dispatch.id,
        required_tool_names=receipt.required_tool_names,
        diagnostic=diagnostic,
        result=result,
        now=now,
    )
    result["mcp_capability_retry_scheduled"] = False


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


def _dispatch_staleness_errors(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
) -> list[str]:
    errors: list[str] = []
    assessment_service = TaskAssessmentService(state)
    dispatch_agent = state.agents.get(dispatch.agent_id)
    recovery_task_ids = (
        {
            task.id
            for task in _assigned_blocker_recovery_tasks(
                state,
                agent=dispatch_agent,
                blockers=(
                    state.blockers[blocker_id]
                    for blocker_id in dispatch.blocker_ids
                    if blocker_id in state.blockers
                ),
            )
        }
        if dispatch_agent is not None
        else set()
    )
    operator_directive_task_ids = {
        item.task_id
        for inbox_id in dispatch.inbox_item_ids
        if (item := state.inbox.get(inbox_id)) is not None
        and item.item_type == TeamInboxItemType.OPERATOR_DIRECTIVE
        and item.recipient_agent_id == dispatch.agent_id
        and item.task_id is not None
    }
    for task_id in dispatch.task_ids:
        task = state.tasks.get(task_id)
        if task is None:
            errors.append(f"task {task_id} no longer exists")
            continue
        if task.owner_agent_id != dispatch.agent_id:
            errors.append(f"task {task.id} is now assigned to {task.owner_agent_id}")
            continue
        assessment = assessment_service.assess(task.id)
        if (
            task_id not in recovery_task_ids
            and task_id not in operator_directive_task_ids
            and assessment.readiness != TaskAssessmentReadiness.ACTIONABLE
        ):
            errors.append(f"task {task.id} is {assessment.readiness.value}, not actionable")
    for blocker_id in dispatch.blocker_ids:
        blocker = state.blockers.get(blocker_id)
        if blocker is None:
            errors.append(f"blocker {blocker_id} no longer exists")
            continue
        assigned = (
            blocker.route == TeamBlockerRoute.DELEGATED
            and blocker.delegate_agent_id == dispatch.agent_id
        ) or (
            blocker.route in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
            and blocker.owner_agent_id == dispatch.agent_id
        )
        if blocker.state != TeamBlockerState.OPEN or not assigned:
            errors.append(f"blocker {blocker.id} is no longer open and assigned")
        # A scoped future-follow-up blocker remains intentionally unreserved;
        # another dispatch's reservation still makes this snapshot stale.
        elif blocker.active_dispatch_id not in {None, dispatch.id}:
            errors.append(f"blocker {blocker.id} is reserved by another dispatch")
    for review_id in dispatch.review_request_ids:
        review = state.review_requests.get(review_id)
        if review is None:
            errors.append(f"review {review_id} no longer exists")
            continue
        if (
            review.reviewer_agent_id != dispatch.agent_id
            or review.state not in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
            or review.superseded_by_review_request_id is not None
            or review.retired_at is not None
        ):
            errors.append(f"review {review.id} is no longer actionable and assigned")
    for inbox_id in dispatch.inbox_item_ids:
        item = state.inbox.get(inbox_id)
        if item is None:
            errors.append(f"inbox item {inbox_id} no longer exists")
            continue
        if item.recipient_agent_id != dispatch.agent_id or item.state != TeamInboxItemState.PENDING:
            errors.append(f"inbox item {item.id} is no longer pending and assigned")
    for project_id in dispatch.project_ids:
        if project_id not in state.projects:
            errors.append(f"project {project_id} no longer exists")
    for authority in dispatch.review_routing_authorities:
        authority_error = _manager_fallback_review_routing_authority_error(
            state,
            dispatch_agent_id=dispatch.agent_id,
            authority=authority,
        )
        if authority_error is not None:
            errors.append(authority_error)
    for deliverable_id in dispatch.initial_release_decision_deliverable_ids:
        authority_error = manager_fallback_initial_release_authority_error(
            state,
            manager_agent_id=dispatch.agent_id,
            deliverable_id=deliverable_id,
        )
        if authority_error is not None:
            errors.append(authority_error)
    current_managed_repo_ids = {
        state.projects[project_id].git.managed_repo_id
        for project_id in dispatch.project_ids
        if project_id in state.projects and state.projects[project_id].git is not None
    }
    if current_managed_repo_ids != set(dispatch.managed_repo_ids):
        errors.append("managed repository scope changed during dispatch preparation")
    return errors
