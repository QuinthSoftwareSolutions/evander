"""Provider completion."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from pathlib import Path
from types import (
    SimpleNamespace,
)
from typing import (
    Any,
)

from evander.effects.providers.base import (
    EffectLifecycleHookBase,
)
from evander.orchestration.isolated_maintenance import sqlite_busy
from evander.orchestration.maintenance import (
    MaintenanceBudget,
    MaintenanceResult,
)

from .lifecycle_candidates import (
    LifecycleCandidate,
)
from .models import (
    TeamDispatchStatus,
)
from .provider_constants import (
    logger,
)
from .provider_events import (
    _publish_team_events,
    _publish_team_outbox_events,
)
from .provider_prompt_queue import (
    _agent_prompt_records_for_dispatches,
    _completed_agent_prompt_records_from_records,
    _completed_prompt_session_id,
    _effect_context_from_worker,
    _reconcile_team_queued_prompt_reservations,
    _record_outputs,
    _runtime_dir_from_worker,
    _team_dispatch_execution_is_live,
    _team_native_thread_receipt,
)
from .provider_runtime import (
    _review_mcp_capability_observation,
)
from .provider_values import (
    _optional_text,
)
from .provider_wake_chat import (
    _activate_delivered_team_prompt,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.candidate_correction_reconciliation_service import (
    CandidateCorrectionReconciliationService,
)
from .services.human_correction_reconciliation_service import (
    HumanCorrectionReconciliationService,
)
from .services.runtime_service import (
    MAX_AGENT_DISPATCH_LEASE_RENEWAL_SECONDS,
    AgentDispatchNativeThreadReceipt,
    TeamRuntimeService,
)


class TeamAgentPromptCompletionFinalizeHook(EffectLifecycleHookBase):
    """Renew and finalize Team leases for standalone prompt executions.

    Team wakes are dispatched through standalone admin prompt effects. This hook
    keeps the lease lifecycle owned by the add-on, extends it only while the
    exact execution is active, and reuses the same repository finalizer.
    """

    _LEASE_RENEWAL_SECONDS = MAX_AGENT_DISPATCH_LEASE_RENEWAL_SECONDS
    _LEASE_RENEWAL_WINDOW_SECONDS = 300
    maintenance_priority = -100  # Lease work precedes other broad recovery.
    isolated_maintenance = True

    def __init__(self) -> None:
        self._repository: TeamStateRepository | None = None
        self._database_identity: tuple[int, int] | None = None
        self._reset_cursors()

    def _reset_cursors(self) -> None:
        self._lease_cursor = ""
        self._completion_cursor = ""
        self._history_cursor = ""
        self._completion_lane = "completion"
        self._queue_cursor = ""
        self._queue_validated = False

    @contextmanager
    def _timed(self, phase: str):
        started = time.perf_counter()
        outcome = "succeeded"
        details: dict[str, Any] = {}
        try:
            yield details
        except Exception as exc:
            outcome = "busy" if sqlite_busy(exc) else "failed"
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            log = logger.info if elapsed >= 100 or outcome != "succeeded" else logger.debug
            log(
                "Team lifecycle phase=%s duration_ms=%.1f outcome=%s details=%s",
                phase,
                elapsed,
                outcome,
                details,
            )

    def initialize(self, *, worker: Any) -> None:
        runtime_dir = _runtime_dir_from_worker(worker)
        if runtime_dir is not None:
            self._get_repository(runtime_dir)
        # The receipt store or providers can change without changing runtime_dir.
        # Initialization is only called before work or after draining the lane.
        self._reset_cursors()

    def _get_repository(self, runtime_dir: Path) -> TeamStateRepository:
        if self._repository is None or self._repository.runtime_dir != runtime_dir:
            with self._timed("repository_initialization"):
                self._repository = TeamStateRepository(runtime_dir, maintenance=True)
                stat = self._repository.store.path.stat()
                self._database_identity = (stat.st_dev, stat.st_ino)
                self._reset_cursors()
        else:
            stat = self._repository.store.path.stat()
            if (stat.st_dev, stat.st_ino) != self._database_identity:
                raise RuntimeError(
                    "Team database replaced; restart worker to initialize its schema"
                )
        return self._repository

    def _candidate_page(self, repo: TeamStateRepository, **kwargs: Any):
        with self._timed("lifecycle_candidates") as details:
            details["kind"] = kwargs.get("kind", "completion")
            page = repo.lifecycle_candidates(**kwargs)
            details["candidates"] = len(page.candidates)
            return page

    def _prompt_records(self, worker: Any, dispatch_ids: set[str]) -> list[Any]:
        with self._timed("execution_receipt_lookup") as details:
            records = _agent_prompt_records_for_dispatches(worker, dispatch_ids)
            details["dispatches"] = len(dispatch_ids)
            details["records"] = len(records)
            return records

    def on_waiting_reconciliation_slice(
        self, *, worker: Any, budget: MaintenanceBudget
    ) -> MaintenanceResult:
        return self._renew_lease_candidates(worker=worker, budget=budget)

    def on_post_completion_dispatch_slice(
        self, *, worker: Any, budget: MaintenanceBudget
    ) -> MaintenanceResult:
        runtime_dir = _runtime_dir_from_worker(worker)
        if runtime_dir is None:
            return MaintenanceResult(examined=0)
        repo = self._get_repository(runtime_dir)
        queued = MaintenanceResult(examined=0)
        if not self._queue_validated:
            queued = self._reconcile_queue_slice(worker=worker, repo=repo, budget=budget)
            if queued.remaining:
                return queued
            self._queue_validated = True
        kind = self._completion_lane
        cursor = self._completion_cursor if kind == "completion" else self._history_cursor
        page = self._candidate_page(repo, kind=kind, after=cursor)
        records = self._prompt_records(worker, {c.dispatch_id for c in page.candidates})
        changes = queued.changes
        for candidate in page.candidates:
            if not budget.take():
                return MaintenanceResult(changes, budget.examined, True)
            changes += self._reconcile_candidate(worker, repo, candidate, records)
            if kind == "completion":
                self._completion_cursor = candidate.cursor
            else:
                self._history_cursor = candidate.dispatch_id
        if kind == "completion":
            if page.next_cursor is None:
                self._completion_cursor = ""
                self._completion_lane = "history"
            return MaintenanceResult(changes, budget.examined, True)
        # One historical page per recovery pass; current owners/active work
        # always get their own lane first, regardless of retained history size.
        self._history_cursor = page.next_cursor or ""
        self._completion_lane = "completion"
        self._queue_validated = False
        return MaintenanceResult(changes, budget.examined)

    def on_prompt_execution_changed(self, *, worker: Any, record: Any) -> None:
        dispatch_id = _optional_text(_record_outputs(record).get("dispatch_id"))
        runtime_dir = _runtime_dir_from_worker(worker)
        if not dispatch_id or runtime_dir is None:
            return
        repo = self._get_repository(runtime_dir)
        page = self._candidate_page(repo, dispatch_ids=[dispatch_id])
        # Query every receipt for this immutable dispatch, not only the event
        # that woke us: a delayed old success must not undo a newer failure.
        records = self._prompt_records(worker, {dispatch_id})
        for candidate in page.candidates:
            self._reconcile_candidate(worker, repo, candidate, records)

    def on_completion_fan_out(self, *, worker: Any, queue_record: Any, graph: Any) -> int:
        del graph
        payload = getattr(queue_record, "payload", {}) or {}
        try:
            self.on_prompt_execution_changed(worker=worker, record=SimpleNamespace(outputs=payload))
        except Exception:
            # A transient add-on read must not block the core completion fan-out.
            logger.exception("Targeted Team completion reconciliation failed; recovery will retry")
        return 0

    def _reconcile_candidate(
        self,
        worker: Any,
        repo: TeamStateRepository,
        candidate: LifecycleCandidate,
        records: list[Any],
    ) -> int:
        matching = [
            r
            for r in records
            if _optional_text(_record_outputs(r).get("dispatch_id")) == candidate.dispatch_id
            and _completed_prompt_session_id(r) == candidate.session_id
        ]
        actionable = bool(
            matching
            and (
                candidate.governance_group_id is not None
                or candidate.status == "preparing"
                or (candidate.status == "leased" and candidate.activation_completed_at is None)
                or (
                    candidate.active
                    and any(getattr(r, "status", None) in {"succeeded", "failed"} for r in matching)
                )
            )
        )
        receipts = [
            receipt
            for r in [
                *(r for r in matching if getattr(r, "status", None) == "waiting"),
                *_completed_agent_prompt_records_from_records(matching),
            ]
            if (receipt := _team_native_thread_receipt(r)) is not None
        ]
        if receipts:
            now = datetime.now(UTC)
            # Match the transactional reconciler: waiting before completed,
            # and last receipt wins for equal observation timestamps.
            receipt = max(
                enumerate(receipts), key=lambda pair: (pair[1].observed_at or now, pair[0])
            )[1]
            if receipt.missing_thread:
                actionable |= candidate.lineage_thread_id == receipt.thread_id and (
                    candidate.lineage_owner_id == candidate.dispatch_id
                    or (candidate.lineage_owner_id is None and candidate.active)
                )
            else:
                actionable |= bool(
                    candidate.activation_completed_at is not None
                    and candidate.current_session_id == candidate.session_id
                    and (
                        candidate.native_thread_observed_at is None
                        or (receipt.observed_at or datetime.now(UTC))
                        > candidate.native_thread_observed_at
                    )
                )
        if not actionable:
            return 0
        with self._timed("completion_finalization") as details:
            changes = self._reconcile_selected_completion(
                worker=worker,
                repo=repo,
                dispatch_ids={candidate.dispatch_id},
                records=matching,
            )
            details["changes"] = changes
            return changes

    def validate_queued_prompt(self, *, worker: Any, record: Any) -> bool:
        dispatch_id = _optional_text(getattr(record, "dispatch_id", None))
        runtime_dir = _runtime_dir_from_worker(worker)
        if not dispatch_id or runtime_dir is None:
            return True
        page = self._candidate_page(self._get_repository(runtime_dir), dispatch_ids=[dispatch_id])
        if not page.candidates:
            return not dispatch_id.startswith(("team-dispatch-", "team-chat:", "team-governance"))
        candidate = page.candidates[0]
        return bool(
            candidate.active
            and (candidate.status == "preparing" or candidate.governance_group_id)
            and getattr(record, "session_id", None) in {None, "", candidate.session_id}
        )

    def _reconcile_queue_slice(
        self, *, worker: Any, repo: TeamStateRepository, budget: MaintenanceBudget
    ) -> MaintenanceResult:
        queue_store = getattr(worker, "queued_agent_prompt_store", None)
        read_all = getattr(queue_store, "read_all", None)
        records = read_all() if callable(read_all) else {}
        changes = 0
        for key, record in sorted(records.items()):
            if key <= self._queue_cursor:
                continue
            if not budget.take():
                return MaintenanceResult(changes, budget.examined, True)
            dispatch_id = _optional_text(getattr(record, "dispatch_id", None))
            if dispatch_id:
                page = self._candidate_page(repo, dispatch_ids=[dispatch_id])
                candidate = page.candidates[0] if page.candidates else None
                current = (
                    candidate is not None
                    and candidate.active
                    and (
                        candidate.status == "preparing" or candidate.governance_group_id is not None
                    )
                    and getattr(record, "session_id", None) in {None, "", candidate.session_id}
                )
                known_team = candidate is not None or dispatch_id.startswith(
                    ("team-dispatch-", "team-chat:", "team-governance")
                )
                if known_team and (not current or candidate.queued_at is None):
                    # Only actionable candidates load authoritative TeamState;
                    # reuse the snapshot but preserve compare-delete and races.
                    _state, count = _reconcile_team_queued_prompt_reservations(
                        worker,
                        repo=repo,
                        state=repo.read(),
                        now=datetime.now(UTC),
                        queued_records={key: record},
                    )
                    changes += count
            self._queue_cursor = key
        self._queue_cursor = ""
        return MaintenanceResult(changes, budget.examined)

    def on_waiting_reconciliation(self, *, worker: Any) -> int:
        result = self._renew_lease_candidates(worker=worker, budget=MaintenanceBudget())
        runtime_dir = _runtime_dir_from_worker(worker)
        if runtime_dir is not None:
            result_queue = self._reconcile_queue_slice(
                worker=worker, repo=self._get_repository(runtime_dir), budget=MaintenanceBudget()
            )
            return result.changes + result_queue.changes
        return result.changes

    def _renew_lease_candidates(
        self, *, worker: Any, budget: MaintenanceBudget
    ) -> MaintenanceResult:
        runtime_dir = _runtime_dir_from_worker(worker)
        if runtime_dir is None:
            return MaintenanceResult(examined=0)
        repo = self._get_repository(runtime_dir)
        now = datetime.now(UTC)
        renewal_due_at = now + timedelta(seconds=self._LEASE_RENEWAL_WINDOW_SECONDS)
        page = self._candidate_page(
            repo, kind="leases", after=self._lease_cursor, due_at=renewal_due_at
        )
        due_dispatches = {
            c.dispatch_id: (c.agent_id, c.current_session_id)
            for c in page.candidates
            if c.current_session_id is not None
        }
        if not due_dispatches:
            self._lease_cursor = ""
            return MaintenanceResult(examined=0)

        records_by_dispatch: dict[str, Any] = {}
        for record in self._prompt_records(worker, set(due_dispatches)):
            if getattr(record, "status", None) != "waiting":
                continue
            outputs = _record_outputs(record)
            dispatch_id = _optional_text(outputs.get("dispatch_id"))
            if dispatch_id is None or dispatch_id not in due_dispatches:
                continue
            agent_id, session_id = due_dispatches[dispatch_id]
            del agent_id
            if _optional_text(outputs.get("session_id")) != session_id:
                continue
            records_by_dispatch[dispatch_id] = record

        renewed_count = 0
        for dispatch_id, (agent_id, session_id) in due_dispatches.items():
            if not budget.take():
                return MaintenanceResult(renewed_count, budget.examined, True)
            record = records_by_dispatch.get(dispatch_id)
            if record is None or not _team_dispatch_execution_is_live(
                worker,
                record=record,
                dispatch_id=dispatch_id,
                session_id=session_id,
            ):
                self._lease_cursor = dispatch_id
                continue
            try:
                with self._timed("lease_renewal"):
                    _state, renewed = TeamRuntimeService(repo).renew_agent_dispatch_lease(
                        agent_id=agent_id,
                        dispatch_id=dispatch_id,
                        now=now,
                        lease_seconds=self._LEASE_RENEWAL_SECONDS,
                        renew_within_seconds=self._LEASE_RENEWAL_WINDOW_SECONDS,
                        include_state=False,
                    )
            except TeamStateConflictError:
                # Completion, expiry, or replacement won the atomic state race.
                # The next reconciliation cycle will use the authoritative row.
                continue
            renewed_count += int(renewed)
            self._lease_cursor = dispatch_id
        self._lease_cursor = page.next_cursor or ""
        return MaintenanceResult(renewed_count, budget.examined, page.next_cursor is not None)

    def on_post_completion_dispatch(self, *, worker: Any) -> int:
        budget = MaintenanceBudget()
        changes = 0
        while True:
            result = self.on_post_completion_dispatch_slice(worker=worker, budget=budget)
            changes += result.changes
            if not result.remaining or budget.examined >= 50 or budget.clock() >= budget.deadline:
                return changes

    def _reconcile_selected_completion(
        self, *, worker: Any, repo: TeamStateRepository, dispatch_ids: set[str], records: list[Any]
    ) -> int:
        runtime_dir = _runtime_dir_from_worker(worker)
        if runtime_dir is None:
            return 0
        state = repo.read()
        context = _effect_context_from_worker(worker, runtime_dir=runtime_dir)
        activated_count = 0
        completed_records = _completed_agent_prompt_records_from_records(records)
        delivered_records = [
            r for r in records if getattr(r, "status", None) == "waiting"
        ] + completed_records
        pending_governance = {
            runtime.active_dispatch_id: group_id
            for group_id, runtime in state.governance_runtimes.items()
            if runtime.active_dispatch_id in dispatch_ids and runtime.wake_leased_until is None
        }
        if pending_governance:
            for record in delivered_records:
                dispatch_id = _optional_text(_record_outputs(record).get("dispatch_id"))
                if dispatch_id is None or dispatch_id not in pending_governance:
                    continue
                group_id = pending_governance[dispatch_id]
                try:
                    state = TeamRuntimeService(repo).activate_governance_dispatch(
                        governance_group_id=group_id,
                        dispatch_id=dispatch_id,
                        lease_seconds=state.governance_runtimes[group_id].requested_lease_seconds,
                    )
                    state = TeamRuntimeService(repo).complete_governance_dispatch(
                        governance_group_id=group_id,
                        dispatch_id=dispatch_id,
                    )
                except (RuntimeError, ValueError, TeamStateConflictError):
                    continue
                activated_count += 1
                pending_governance.pop(dispatch_id, None)
                if not pending_governance:
                    break
        activation_candidates = {
            dispatch_id: dispatch
            for dispatch_id, dispatch in state.dispatches.items()
            if dispatch_id in dispatch_ids
            and (
                dispatch.status == TeamDispatchStatus.PREPARING
                or (
                    dispatch.status == TeamDispatchStatus.LEASED
                    and dispatch.activation_completed_at is None
                )
            )
        }
        if activation_candidates:
            for record in delivered_records:
                outputs = _record_outputs(record)
                dispatch_id = _optional_text(outputs.get("dispatch_id"))
                if dispatch_id is None or dispatch_id not in activation_candidates:
                    continue
                dispatch = activation_candidates[dispatch_id]
                if _optional_text(outputs.get("session_id")) != dispatch.session_id:
                    continue
                was_preparing = dispatch.status == TeamDispatchStatus.PREPARING
                try:
                    state, _activation = _activate_delivered_team_prompt(
                        repo=repo,
                        dispatch_id=dispatch_id,
                        result_payload=outputs,
                    )
                except (RuntimeError, ValueError, TeamStateConflictError):
                    continue
                activated_count += int(was_preparing)
                activation_candidates.pop(dispatch_id, None)
                if not activation_candidates:
                    break
        # Native IDs arrive after asynchronous dispatch activation. Reconcile
        # both running and completed execution receipts before closing leases;
        # a completion workflow may already have finalized the Team dispatch.
        native_receipts: list[AgentDispatchNativeThreadReceipt] = []
        for record in delivered_records:
            receipt = _team_native_thread_receipt(record)
            if receipt is not None:
                native_receipts.append(receipt)
        state, deferred_native_dispatches = TeamRuntimeService(
            repo
        ).reconcile_agent_dispatch_native_threads(state=state, receipts=native_receipts)
        active_dispatches = {
            dispatch_id: agent_id
            for agent_id, runtime in state.agent_runtimes.items()
            if (dispatch_id := _optional_text(runtime.active_dispatch_id)) is not None
            and (dispatch_id.startswith("team-dispatch-") or dispatch_id.startswith("team-chat:"))
        }
        finalized_count = 0
        for record in completed_records:
            dispatch_id = _optional_text(_record_outputs(record).get("dispatch_id"))
            if (
                dispatch_id is None
                or dispatch_id not in active_dispatches
                or dispatch_id in deferred_native_dispatches
            ):
                continue
            session_id = _completed_prompt_session_id(record)
            if session_id is None:
                continue
            agent_id = active_dispatches[dispatch_id]
            dispatch = state.dispatches.get(dispatch_id)
            state, result = TeamRuntimeService(repo).finalize_agent_dispatch_from_activity(
                session_id=session_id,
                dispatch_id=dispatch_id,
                agent_id=agent_id,
                mcp_capability_observation=_review_mcp_capability_observation(
                    context,
                    dispatch=dispatch,
                ),
            )
            if not result.get("finalized"):
                continue
            finalized_count += 1
            active_dispatches.pop(dispatch_id, None)
            _publish_team_events(context, result.get("events"))
            if not active_dispatches:
                break
        if finalized_count:
            try:
                candidate_correction_result = CandidateCorrectionReconciliationService(
                    repo
                ).reconcile()
                _publish_team_events(
                    context,
                    candidate_correction_result.get("events"),
                )
            except Exception:
                # The normal Team reconciliation cycle retries durable
                # candidate-correction release creation.
                pass
            try:
                correction_result = HumanCorrectionReconciliationService(repo).reconcile()
                _publish_team_events(
                    context,
                    correction_result.get("events"),
                )
            except Exception:
                # The normal Team reconciliation cycle retries durable
                # correction-case aggregation without reopening this dispatch.
                pass
            _publish_team_outbox_events(
                context=context,
                repo=repo,
                worker_id="team-outbox-after-dispatch",
                limit=25,
                claim_ttl_seconds=300,
                retry_after_seconds=60,
            )
        return activated_count + finalized_count
