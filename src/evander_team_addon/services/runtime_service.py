from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Literal
from uuid import uuid4

from evander.agent_threads import usable_agent_thread_id
from evander.events.internal import (
    InternalEventBus,
    InternalEventSource,
)
from evander.orchestration.internal_events import build_internal_event_bus_for_runtime

from .agent_leasing import (
    MAX_REVIEW_MCP_CAPABILITY_ATTEMPTS,
    TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS as TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS,
    lease_due_agents as _lease_due_agents,
    _fail_dispatch_preparation_in_state,
    # Compatibility import used by callers of the former runtime helper location.
    _has_prior_successful_ordinary_wake as _has_prior_successful_ordinary_wake,
    _mark_dispatch_terminal,
    _release_blocker_dispatch_reservations,
    _runtime_has_active_dispatch_reservation,
)
from .domain.role_task_queue import (
    append_role_queue_wake_events,
    set_unstarted_role_task_owner,
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
    SCHEMA_VERSION,
    AgentContextClearingPolicy,
    AgentRuntime,
    AgentStandDown,
    AgentStandDownSource,
    AgentStatus,
    GovernanceRuntime,
    PersistentAgent,
    TaskAssessmentReadiness,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamDispatch,
    TeamDispatchActivityStatus,
    TeamDispatchActivity,
    TeamDispatchMcpCapabilityReceipt,
    TeamDispatchMcpCapabilityStatus,
    TeamDispatchStatus,
    TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS,
    TeamInboxItemType,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
)
from .domain.task_assessment import TaskAssessmentService
from .domain.agent_access import (
    _agent_for_session,
    _agent_payload,
    agent_role_description_fingerprint,
)
from .domain.chat import (
    _append_pending_chat_message_queued_event_for_agent,
    _chat_dispatch_finalization_error,
    _mark_chat_dispatch_message_failed,
    _mark_chat_dispatch_message_replied_from_activity,
)
from .domain.blockers import (
    complete_blocker_dispatches,
    open_agent_mcp_capability_blocker,
)
from .domain.common import (
    _NON_TERMINAL_TASK_STATES,
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
    _blocker_project_ids,
    _inbox_project_ids,
    _review_project_ids,
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
    manager_fallback_dispatch_supervision_task_ids,
)
from .domain.initial_release import (
    manager_fallback_initial_release_authority_error,
)
from .domain.stand_down import (
    _active_stand_downs_for_agent,
    _normalize_stand_down_scope,
    _stand_down_affected_agent_ids,
)
from .domain.wake import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
    _agent_immediate_wake_request,
    _append_agent_wake_requested_event,
    _append_follow_up_agent_wake_requested_event,
)
from .domain.work_due import (
    _agent_has_open_execution_stall,
    _mark_agent_work_due,
)
from .commands.outbox import _team_event_outbox_records


TEAM_NATIVE_THREAD_RECONCILIATION_BATCH_SIZE = 32
MAX_AGENT_DISPATCH_LEASE_RENEWAL_SECONDS = 900
logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return _team_common.utc_now()




@dataclass(frozen=True)
class AgentDispatchNativeThreadReceipt:
    session_id: str
    dispatch_id: str
    thread_id: str
    observed_at: datetime | None = None
    missing_thread: bool = False


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
            if runtime.context_clearing_policy in {
                AgentContextClearingPolicy.EVERY_WAKE,
                AgentContextClearingPolicy.WORK_LINEAGE,
            }:
                raise ValueError(
                    "manual context clearing is unavailable for every_wake and "
                    "work_lineage policies"
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
            append_role_queue_wake_events(state, result=wake_result, now=now)
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
            for intent_type in (AGENT_WAKE_REQUESTED_EVENT_TYPE, "team.role_queue.wake_requested")
            for record in self._repository.list_outbox_records(intent_type=intent_type)
            if record.idempotency_key is not None
        }
        bus = self._event_bus
        if bus is None and self._event_bus_factory is not None:
            bus = self._event_bus_factory()
        if bus is None:
            bus = build_internal_event_bus_for_runtime(self._repository.runtime_dir)
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
        normalized_thread_id = usable_agent_thread_id(thread_id, dispatch_id)

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
        return _lease_due_agents(
            self,
            effective_now=_ensure_utc(now or utc_now()),
            limit=limit,
            lease_seconds=lease_seconds,
            wake_interval_seconds=wake_interval_seconds,
            max_no_progress_count=max_no_progress_count,
            low_priority_wake_after_cycles=low_priority_wake_after_cycles,
            respect_wake_interval=respect_wake_interval,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            blocked_team_ids=blocked_team_ids,
            session_is_busy=session_is_busy,
            defer_activation=defer_activation,
        )

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
        normalized_thread_id = usable_agent_thread_id(thread_id, dispatch_id)
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
                self._record_dispatch_native_thread_in_state(
                    state, dispatch=dispatch, thread_id=normalized_thread_id, now=effective_now
                )
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

            if (
                runtime.context_clearing_policy == AgentContextClearingPolicy.WORK_LINEAGE
                and dispatch.work_lineage_id is not None
            ):
                lineage_id = dispatch.work_lineage_id
                runtime.work_lineage_thread_dispatch_ids[lineage_id] = dispatch.id
                if usable_agent_thread_id(runtime.work_lineage_thread_ids.get(lineage_id)) is None:
                    runtime.work_lineage_thread_ids.pop(lineage_id, None)

            for task_id in dispatch.task_ids:
                task = state.tasks.get(task_id)
                if (
                    task is not None
                    and task.assignment_mode == "role"
                    and task.owner_agent_id == dispatch.agent_id
                    and task.assignment_started_at is None
                ):
                    task.assignment_started_at = effective_now
                    task.updated_at = effective_now
            dispatch.activation_completed_at = effective_now
            self._record_dispatch_native_thread_in_state(
                state, dispatch=dispatch, thread_id=normalized_thread_id, now=effective_now
            )
            runtime.updated_at = effective_now
            return state

        state = self._repository.update(_mutate)
        if preparation_error:
            raise RuntimeError(preparation_error[0])
        return state, activation

    @staticmethod
    def _dispatch_native_thread_update_is_due(
        state: TeamState,
        *,
        dispatch: TeamDispatch,
        thread_id: str | None,
        now: datetime,
        observed_at: datetime | None = None,
    ) -> bool:
        thread_id = usable_agent_thread_id(thread_id, dispatch.id)
        runtime = state.agent_runtimes.get(dispatch.agent_id)
        agent = state.agents.get(dispatch.agent_id)
        if (
            thread_id is None
            or (dispatch.native_thread_id == thread_id and observed_at is None)
            or runtime is None
            or agent is None
            or agent.session_id != dispatch.session_id
            or dispatch.activation_completed_at is None
            or (
                dispatch.native_thread_observed_at is not None
                and (observed_at or now) <= dispatch.native_thread_observed_at
            )
        ):
            return False
        return True

    @staticmethod
    def _record_dispatch_native_thread_in_state(
        state: TeamState,
        *,
        dispatch: TeamDispatch,
        thread_id: str | None,
        now: datetime,
        observed_at: datetime | None = None,
    ) -> bool:
        """Bind a receipt to its immutable dispatch scope, never the current task."""
        thread_id = usable_agent_thread_id(thread_id, dispatch.id)
        if not TeamRuntimeService._dispatch_native_thread_update_is_due(
            state, dispatch=dispatch, thread_id=thread_id, now=now, observed_at=observed_at
        ):
            return False
        runtime = state.agent_runtimes[dispatch.agent_id]
        if dispatch.native_thread_id == thread_id:
            # Advance the receipt for a newer execution that used the same ID,
            # without resurrecting a binding invalidated since that execution.
            dispatch.native_thread_observed_at = observed_at
            return True
        dispatch.native_thread_id = thread_id
        dispatch.native_thread_observed_at = observed_at or now
        lineage_id = dispatch.work_lineage_id
        if (
            runtime.context_clearing_policy == AgentContextClearingPolicy.WORK_LINEAGE
            and lineage_id is not None
        ):
            owner = runtime.work_lineage_thread_dispatch_ids.get(lineage_id)
            # Recover an already-delivered pre-fix dispatch using its exact
            # lease or poisoned binding. An absent binding after a policy reset
            # is not permission to resurrect historical conversation state.
            legacy_binding = runtime.work_lineage_thread_ids.get(lineage_id)
            if owner is None and legacy_binding is not None and (
                runtime.active_dispatch_id == dispatch.id or legacy_binding == dispatch.id
            ):
                runtime.work_lineage_thread_dispatch_ids[lineage_id] = dispatch.id
                owner = dispatch.id
            if owner == dispatch.id:
                runtime.work_lineage_thread_ids[lineage_id] = thread_id
        if runtime.active_dispatch_id == dispatch.id:
            runtime.last_thread_id = thread_id
        runtime.updated_at = now
        return True

    def record_agent_dispatch_native_thread(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        thread_id: str,
        observed_at: datetime | None = None,
        now: datetime | None = None,
    ) -> tuple[TeamState, bool]:
        """Reconcile an observed provider ID for an active or completed dispatch."""
        session_id = _required_text(session_id, "session_id")
        dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())
        recorded = False

        def _mutate(state: TeamState) -> TeamState:
            nonlocal recorded
            dispatch = state.dispatches.get(dispatch_id)
            if dispatch is None or dispatch.session_id != session_id:
                return state
            recorded = self._record_dispatch_native_thread_in_state(
                state,
                dispatch=dispatch,
                thread_id=thread_id,
                now=effective_now,
                observed_at=_ensure_utc(observed_at) if observed_at is not None else None,
            )
            return state

        return self._repository.update(_mutate), recorded

    def reconcile_agent_dispatch_native_threads(
        self,
        *,
        state: TeamState,
        receipts: Sequence[AgentDispatchNativeThreadReceipt],
        now: datetime | None = None,
    ) -> tuple[TeamState, set[str]]:
        """Apply one bounded receipt batch; return dispatches deferred to a later pass.

        The supplied snapshot avoids transactions for already handled history. All
        identity, timestamp and binding-ownership checks run again under the write
        transaction. Persisted receipts, not a process-local cursor, track progress.
        """
        effective_now = _ensure_utc(now or utc_now())

        def is_due(current: TeamState, receipt: AgentDispatchNativeThreadReceipt) -> bool:
            dispatch = current.dispatches.get(receipt.dispatch_id)
            if dispatch is None or dispatch.session_id != receipt.session_id:
                return False
            if receipt.missing_thread:
                runtime = current.agent_runtimes.get(dispatch.agent_id)
                lineage = dispatch.work_lineage_id
                if runtime is None or lineage is None:
                    return False
                owner = runtime.work_lineage_thread_dispatch_ids.get(lineage)
                return runtime.work_lineage_thread_ids.get(lineage) == receipt.thread_id and (
                    owner == dispatch.id
                    or (owner is None and runtime.active_dispatch_id == dispatch.id)
                )
            return self._dispatch_native_thread_update_is_due(
                current,
                dispatch=dispatch,
                thread_id=receipt.thread_id,
                now=effective_now,
                observed_at=receipt.observed_at,
            )

        # Only the newest receipt per immutable dispatch matters. In particular,
        # an older successful observation must not undo a later missing-thread
        # failure when a batch boundary or restart splits the history.
        latest: dict[str, AgentDispatchNativeThreadReceipt] = {}
        for receipt in receipts:
            dispatch = state.dispatches.get(receipt.dispatch_id)
            if dispatch is None or dispatch.session_id != receipt.session_id:
                continue
            if receipt.observed_at is not None:
                receipt = replace(receipt, observed_at=_ensure_utc(receipt.observed_at))
            previous = latest.get(receipt.dispatch_id)
            if previous is None or (receipt.observed_at or effective_now) >= (
                previous.observed_at or effective_now
            ):
                latest[receipt.dispatch_id] = receipt
        candidates = [receipt for receipt in latest.values() if is_due(state, receipt)]

        def priority(receipt: AgentDispatchNativeThreadReceipt) -> tuple[bool, bool, datetime, str]:
            dispatch = state.dispatches[receipt.dispatch_id]
            runtime = state.agent_runtimes[dispatch.agent_id]
            owner = runtime.work_lineage_thread_dispatch_ids.get(dispatch.work_lineage_id or "")
            if owner is None and runtime.work_lineage_thread_ids.get(
                dispatch.work_lineage_id or ""
            ) == dispatch.id:
                owner = dispatch.id
            return (
                runtime.active_dispatch_id != dispatch.id,
                owner != dispatch.id,
                receipt.observed_at or effective_now,
                dispatch.id,
            )

        candidates.sort(key=priority)
        selected = candidates[:TEAM_NATIVE_THREAD_RECONCILIATION_BATCH_SIZE]
        deferred = {receipt.dispatch_id for receipt in candidates[len(selected):]}
        if not selected:
            return state, deferred

        def mutate(current: TeamState) -> TeamState:
            for receipt in selected:
                if not is_due(current, receipt):
                    continue
                dispatch = current.dispatches[receipt.dispatch_id]
                self._apply_dispatch_native_thread_receipt(
                    current, dispatch=dispatch, receipt=receipt, now=effective_now
                )
            return current

        return self._repository.update(mutate), deferred

    @staticmethod
    def _apply_dispatch_native_thread_receipt(
        state: TeamState, *, dispatch: TeamDispatch,
        receipt: AgentDispatchNativeThreadReceipt, now: datetime,
    ) -> None:
        """Save the target receipt before its lease/lineage owner can be released."""
        if receipt.missing_thread:
            runtime = state.agent_runtimes.get(dispatch.agent_id)
            lineage = dispatch.work_lineage_id
            if runtime is None or lineage is None:
                return
            owner = runtime.work_lineage_thread_dispatch_ids.get(lineage)
            if runtime.work_lineage_thread_ids.get(lineage) != receipt.thread_id or not (
                owner == dispatch.id or (owner is None and runtime.active_dispatch_id == dispatch.id)
            ):
                return
            runtime.work_lineage_thread_ids.pop(lineage, None)
            runtime.work_lineage_thread_dispatch_ids.pop(lineage, None)
            if runtime.last_thread_id == receipt.thread_id:
                runtime.last_thread_id = None
            runtime.updated_at = now
        else:
            TeamRuntimeService._record_dispatch_native_thread_in_state(
                state, dispatch=dispatch, thread_id=receipt.thread_id, now=now,
                observed_at=_ensure_utc(receipt.observed_at) if receipt.observed_at else None,
            )

    def invalidate_agent_work_lineage_thread_binding(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        resume_thread_id: str,
        require_dispatch_owner: bool = False,
        now: datetime | None = None,
    ) -> tuple[TeamState, bool]:
        """Remove the exact lineage binding rejected by the agent provider."""

        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_thread_id = _required_text(resume_thread_id, "resume_thread_id")
        effective_now = _ensure_utc(now or utc_now())
        invalidated = False

        def _mutate(state: TeamState) -> TeamState:
            nonlocal invalidated
            dispatch = _require_present(state.dispatches, normalized_dispatch_id, "dispatch")
            if dispatch.agent_id != normalized_agent_id:
                raise ValueError(
                    f"dispatch {dispatch.id} belongs to agent {dispatch.agent_id}, "
                    f"not {normalized_agent_id}"
                )
            lineage_id = dispatch.work_lineage_id
            if lineage_id is None:
                return state
            runtime = state.agent_runtimes.setdefault(
                normalized_agent_id,
                AgentRuntime(agent_id=normalized_agent_id),
            )
            if runtime.work_lineage_thread_ids.get(lineage_id) != normalized_thread_id:
                return state
            owner = runtime.work_lineage_thread_dispatch_ids.get(lineage_id)
            if require_dispatch_owner and not (
                owner == dispatch.id
                or (owner is None and runtime.active_dispatch_id == dispatch.id)
            ):
                return state
            runtime.work_lineage_thread_ids.pop(lineage_id, None)
            runtime.work_lineage_thread_dispatch_ids.pop(lineage_id, None)
            if runtime.last_thread_id == normalized_thread_id:
                runtime.last_thread_id = None
            runtime.updated_at = effective_now
            invalidated = True
            return state

        state = self._repository.update(_mutate)
        return state, invalidated

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
        include_state: bool = True,
    ) -> tuple[TeamState | None, bool]:
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

        # Maintenance only needs the renewal result. Preserve the full-state
        # return for other callers while avoiding historical state reads in the
        # per-dispatch maintenance loop.
        current = self._repository.read() if include_state else None
        if current is not None:
            dispatch = current.dispatches.get(normalized_dispatch_id)
            runtime = current.agent_runtimes.get(normalized_agent_id)
            if (
                dispatch is None or runtime is None
                or dispatch.agent_id != normalized_agent_id
                or dispatch.status != TeamDispatchStatus.LEASED
                or runtime.active_dispatch_id != normalized_dispatch_id
                or dispatch.lease_expires_at is None or runtime.wake_leased_until is None
                or (
                    _ensure_utc(dispatch.lease_expires_at) > renewal_due_at
                    and _ensure_utc(runtime.wake_leased_until) > renewal_due_at
                )
            ):
                return current, False
        renewed_until = effective_now + timedelta(seconds=normalized_lease_seconds)
        revision, updated_at, renewed = self._repository.renew_dispatch_lease(
            agent_id=normalized_agent_id,
            dispatch_id=normalized_dispatch_id,
            renewal_due_at=renewal_due_at,
            renewed_until=renewed_until,
            now=effective_now,
            expected_revision=current.revision if current is not None else None,
        )
        if current is not None and renewed:
            current.dispatches[normalized_dispatch_id].lease_expires_at = renewed_until
            runtime = current.agent_runtimes[normalized_agent_id]
            runtime.wake_leased_until = renewed_until
            runtime.updated_at = effective_now
            if current.revision != revision:
                current.schema_version = SCHEMA_VERSION
            current.revision = revision
            current.updated_at = updated_at
        return current, renewed

    def fail_agent_dispatch(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        error: str,
        now: datetime | None = None,
        delivery_not_started: bool = False,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_error = _required_text(error, "error")
        effective_now = _ensure_utc(now or utc_now())
        result: dict[str, Any] = {"events": []}

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
            if delivery_not_started:
                # A retry cannot release an earlier claim: an earlier delivery
                # may have executed even when activation was never confirmed.
                for task_id in dispatch.claimed_role_task_ids:
                    task = state.tasks.get(task_id)
                    if (
                        task is not None
                        and task.assignment_mode == "role"
                        and task.assignment_started_at is None
                        and task.state.value == "pending"
                        and task.owner_agent_id == dispatch.agent_id
                        and not task.deliverable_ids
                    ):
                        set_unstarted_role_task_owner(state, task=task, owner_agent_id=None)
                        task.updated_at = effective_now
                append_role_queue_wake_events(
                    state,
                    result=result,
                    now=effective_now,
                    team_id=agent.team_id
                    if (agent := state.agents.get(dispatch.agent_id))
                    else None,
                )
            return state

        return self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result["events"]),
        )

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
            append_role_queue_wake_events(
                state,
                result=result,
                now=effective_now,
                team_id=agent.team_id,
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
    ) -> tuple[TeamState, dict[str, Any]]:
        effective_now = _ensure_utc(now or utc_now())
        effective_prune_before = _ensure_utc(prune_before) if prune_before is not None else None
        result: dict[str, Any] = {}

        def _mutate(state: TeamState) -> TeamState:
            result.update(
                reconcile_git_work_context_lifecycle(
                    state,
                    now=effective_now,
                    prune_before=effective_prune_before,
                )
            )
            from .domain.git_recovery_continuation import reconcile_git_recovery_successors
            reconcile_git_recovery_successors(state, result=result, now=effective_now)
            return state

        from .commands.outbox import _team_event_outbox_records
        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
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
        native_thread_receipt: AgentDispatchNativeThreadReceipt | None = None,
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

            if native_thread_receipt is not None:
                if (
                    native_thread_receipt.session_id != normalized_session_id
                    or native_thread_receipt.dispatch_id != normalized_dispatch_id
                    or durable_dispatch is None
                    or durable_dispatch.session_id != normalized_session_id
                ):
                    return _failure(
                        state, code="native_thread_receipt_mismatch",
                        message="Native thread receipt does not belong to the finalizing dispatch",
                        resolved_agent_id=agent.id,
                    )
                self._apply_dispatch_native_thread_receipt(
                    state, dispatch=durable_dispatch, receipt=native_thread_receipt, now=utc_now()
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
            append_role_queue_wake_events(
                state,
                result=result,
                now=completed_at,
                team_id=agent.team_id,
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
        and (task := state.tasks.get(item.task_id)) is not None
        and task.state in _NON_TERMINAL_TASK_STATES
    }
    manager_fallback_supervision_task_ids = manager_fallback_dispatch_supervision_task_ids(
        state,
        dispatch=dispatch,
    )
    for task_id in dispatch.task_ids:
        task = state.tasks.get(task_id)
        if task is None:
            errors.append(f"task {task_id} no longer exists")
            continue
        manager_fallback_supervision_task = task.id in manager_fallback_supervision_task_ids
        if task.owner_agent_id != dispatch.agent_id and not manager_fallback_supervision_task:
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
    if dispatch_agent is not None:
        current_project_ids = _current_dispatch_project_ids(
            state,
            dispatch=dispatch,
            dispatch_agent=dispatch_agent,
        )
        if current_project_ids != set(dispatch.project_ids):
            errors.append("project scope changed during dispatch preparation")
    current_managed_repo_ids = {
        state.projects[project_id].git.managed_repo_id
        for project_id in dispatch.project_ids
        if project_id in state.projects and state.projects[project_id].git is not None
    }
    if current_managed_repo_ids != set(dispatch.managed_repo_ids):
        errors.append("managed repository scope changed during dispatch preparation")
    return errors


def _current_dispatch_project_ids(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    dispatch_agent: PersistentAgent,
) -> set[str]:
    """Recompute only project authority actually represented by a dispatch."""

    fallback_supervision_task_ids = manager_fallback_dispatch_supervision_task_ids(
        state,
        dispatch=dispatch,
    )
    project_ids = {
        task.project_id
        for task_id in dispatch.task_ids
        if task_id not in fallback_supervision_task_ids
        if (task := state.tasks.get(task_id)) is not None
    }
    for blocker_id in dispatch.blocker_ids:
        blocker = state.blockers.get(blocker_id)
        if blocker is not None:
            project_ids.update(_blocker_project_ids(state, blocker))
    for review_id in dispatch.review_request_ids:
        review = state.review_requests.get(review_id)
        if review is not None:
            project_ids.update(_review_project_ids(state, review))
    for inbox_id in dispatch.inbox_item_ids:
        item = state.inbox.get(inbox_id)
        if item is not None:
            project_ids.update(_inbox_project_ids(state, item))
    project_ids.update(
        task.project_id
        for deliverable_id in dispatch.initial_release_decision_deliverable_ids
        if (deliverable := state.deliverables.get(deliverable_id)) is not None
        if (task := state.tasks.get(deliverable.task_id)) is not None
    )
    return {
        project_id
        for project_id in project_ids
        if (project := state.projects.get(project_id)) is not None
        and project.team_id == dispatch_agent.team_id
    }
