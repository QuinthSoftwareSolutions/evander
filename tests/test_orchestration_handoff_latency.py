from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from config_helpers import canonical_worker_config
from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectExecutionRecord, EffectOrchestratorWorker
from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.lifecycle_hooks import ProviderCompletionLifecycleHook
from evander.events.models import TriggerEvent
from evander.orchestration.maintenance import MaintenanceBudget, MaintenanceResult, RecoverySchedule
from evander.repositories.orchestration_db import connect_orchestration_db
from evander_team_addon.providers import TeamAgentPromptCompletionFinalizeHook
from evander_team_addon.sqlite_store import TeamStateSqliteStore
from test_effect_orchestrator import _Factory, _ImmediateExecutor, _edge
from test_team_addon_runtime import _seed_team, _team_runtime_service


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _worker(tmp_path, *, chain_length=6):
    nodes = [
        {
            "id": f"step-{i}",
            "display_name": f"Step {i}",
            "type": "effect:vm:create",
            "vm_name": f"vm-{i}",
        }
        for i in range(chain_length)
    ]
    config = canonical_worker_config(
        {
            "runtime_dir": tmp_path,
            "poll_interval_seconds": 13,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "start",
                    "display_name": "Start",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": nodes,
            "edges": [
                _edge("start" if i == 0 else f"step-{i - 1}", f"step-{i}")
                for i in range(chain_length)
            ],
        }
    )
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=tmp_path), worker_config=config, runtime=None
    )
    worker.provider_factory = _Factory({node["id"]: {"success": True} for node in nodes})
    return worker


def _publish(worker, event_id="sample"):
    worker.event_queue.publish(
        TriggerEvent(
            event_id=event_id,
            trigger_id="start",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(UTC),
            trace_id=event_id,
            payload={},
        )
    )


def test_ready_successors_drain_without_repeated_slow_recovery(tmp_path):
    worker = _worker(tmp_path)
    worker._executor = _ImmediateExecutor()
    calls = []
    worker._lifecycle_reconciliation_hooks = (
        SimpleNamespace(reconcile=lambda **kw: calls.append("scan") or 0),
    )
    worker._completion_dispatch_hooks = ()
    _publish(worker)
    for _ in range(10):
        result = worker.run_once()
        if result.next_sleep_seconds:
            break
    records = worker.execution_store.list_executions()
    assert len(records) == 6
    assert all(r.status == "succeeded" for r in records)
    assert calls == []
    assert 0 < result.next_sleep_seconds <= 5


def test_recovery_continuations_and_due_leases_preempt_long_pass():
    clock = Clock()
    schedule = RecoverySchedule(clock=clock)
    lease = SimpleNamespace(maintenance_priority=-100)
    slow = SimpleNamespace(maintenance_priority=0)
    tasks = [("waiting_reconciliation", lease), ("post_completion_dispatch", slow)]
    assert not schedule.begin_if_due(tasks)
    assert schedule.sleep_limit() == 5
    clock.now = 5
    assert schedule.begin_if_due(tasks)
    assert schedule.pending.popleft()[1] is lease
    assert schedule.sleep_limit() == 0
    clock.now = 10
    assert schedule.begin_if_due(tasks)
    assert list(schedule.pending) == tasks  # no duplicate continuation/lease


def test_busy_foreground_spaces_recovery_continuations_but_idle_drains_immediately(
    tmp_path, monkeypatch
):
    worker = _worker(tmp_path)
    clock = Clock()
    worker._recovery_schedule = RecoverySchedule(clock=clock)
    worker._recovery_schedule.next_due = 0
    calls = []

    def sliced(**kwargs):
        calls.append(clock.now)
        return MaintenanceResult(examined=1, remaining=len(calls) < 3)

    worker._lifecycle_reconciliation_hooks = (SimpleNamespace(run_maintenance_slice=sliced),)
    worker._completion_dispatch_hooks = ()
    monkeypatch.setattr("evander.daemons.effect_orchestrator.time.monotonic", clock)

    assert worker._run_due_recovery(graph=None, foreground_pending=True) == (True, 0)
    assert calls == [0]
    assert worker._run_due_recovery(graph=None, foreground_pending=True) == (False, 0)
    clock.now = 4.99
    assert worker._run_due_recovery(graph=None, foreground_pending=True) == (False, 0)
    clock.now = 5
    assert worker._run_due_recovery(graph=None, foreground_pending=True) == (True, 0)
    assert calls == [0, 5]
    assert worker._run_due_recovery(graph=None, foreground_pending=False) == (True, 0)
    assert calls == [0, 5, 5]


def test_busy_foreground_reserves_progress_beyond_urgent_lease_renewal(
    tmp_path, monkeypatch
):
    worker = _worker(tmp_path)
    clock = Clock()
    worker._recovery_schedule = RecoverySchedule(clock=clock)
    worker._recovery_schedule.next_due = 0
    calls = []

    def sliced(name):
        def run(**kwargs):
            calls.append((name, clock.now))
            return MaintenanceResult(examined=1, remaining=True)

        return run

    worker._lifecycle_reconciliation_hooks = (
        SimpleNamespace(maintenance_priority=-100, run_maintenance_slice=sliced("lease")),
        SimpleNamespace(maintenance_priority=0, run_maintenance_slice=sliced("reconcile")),
    )
    worker._completion_dispatch_hooks = (
        SimpleNamespace(maintenance_priority=0, run_maintenance_slice=sliced("queue")),
    )
    monkeypatch.setattr("evander.daemons.effect_orchestrator.time.monotonic", clock)

    for clock.now in range(0, 31, 5):
        assert worker._run_due_recovery(graph=None, foreground_pending=True)[0]

    assert [name for name, _when in calls].count("lease") == 7
    assert [name for name, _when in calls].count("reconcile") >= 3
    assert [name for name, _when in calls].count("queue") >= 3


def test_maintenance_budgets_are_cooperative_and_bounded():
    clock = Clock()
    budget = MaintenanceBudget(clock=clock)
    assert budget.take()
    clock.now = 1  # one slow DB operation can overrun, no second candidate
    assert not budget.take()


def test_event_backlog_yields_to_due_recovery_and_preserves_fifo(tmp_path, monkeypatch):
    worker = _worker(tmp_path)
    clock = Clock()
    worker._recovery_schedule = RecoverySchedule(clock=clock)
    worker._recovery_schedule.next_due = 0
    seen, recovered = [], []
    worker._lifecycle_reconciliation_hooks = (
        SimpleNamespace(reconcile=lambda **kw: recovered.append(clock.now) or 0),
    )
    worker._completion_dispatch_hooks = ()

    def process(record, **kwargs):
        seen.append(record.event_id)
        clock.now += 0.06
        return 0

    monkeypatch.setattr(worker, "_process_event_record", process)
    monkeypatch.setattr("evander.daemons.effect_orchestrator.time.monotonic", clock)
    for i in range(10):
        _publish(worker, f"event-{i}")
    result = worker.run_once()
    assert seen == ["event-0", "event-1"]
    assert recovered == [pytest.approx(0.12)]
    assert result.next_sleep_seconds == 0
    worker.run_once()
    assert seen == [f"event-{i}" for i in range(4)]
    budget = MaintenanceBudget(clock=clock)
    assert all(budget.take() for _ in range(50))
    assert not budget.take()


def test_recovery_slice_yields_between_legacy_hooks(tmp_path, monkeypatch):
    worker = _worker(tmp_path)
    clock = Clock()
    worker._recovery_schedule = RecoverySchedule(clock=clock)
    calls = []

    def slow(**kwargs):
        calls.append("slow")
        clock.now += 0.6
        return 0

    worker._lifecycle_reconciliation_hooks = (
        SimpleNamespace(reconcile=slow),
        SimpleNamespace(reconcile=slow),
    )
    worker._completion_dispatch_hooks = ()
    monkeypatch.setattr("evander.daemons.effect_orchestrator.time.monotonic", clock)
    clock.now = 5
    assert worker._run_due_recovery(graph=None) == (True, 0)
    assert calls == ["slow"]
    assert worker._recovery_schedule.sleep_limit() == 0
    worker._run_due_recovery(graph=None)
    assert calls == ["slow", "slow"]


def test_idle_cycle_does_not_sleep_on_event_published_by_zero_count_hook(tmp_path):
    worker = _worker(tmp_path)
    worker._recovery_schedule.next_due = 0
    worker._lifecycle_reconciliation_hooks = ()
    worker._completion_dispatch_hooks = (
        SimpleNamespace(dispatch=lambda **kw: _publish(worker) or 0),
    )
    result = worker.run_once()
    assert result.events_processed == 0
    assert result.next_sleep_seconds == 0
    assert worker.event_queue.list_pending(limit=1)


def test_history_backfill_does_not_block_inflight_effects(tmp_path, monkeypatch):
    worker = _worker(tmp_path)
    worker._recovery_schedule.next_due = 0
    worker._lifecycle_reconciliation_hooks = (SimpleNamespace(reconcile=lambda **kw: 0),)
    worker._completion_dispatch_hooks = ()
    worker._register_inflight_future(
        idempotency_key="running", future=Future(), config_revision="revision"
    )
    backfills = []
    monkeypatch.setattr(
        "evander.daemons.effect_orchestrator.run_execution_history_backfill",
        lambda path: backfills.append(path) or {},
    )
    worker.run_once()
    assert not backfills


def test_queue_recovery_is_bounded_and_reuses_noop_snapshot(monkeypatch):
    from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook

    records = {f"queue-{i:03d}": SimpleNamespace(created_at=datetime.now(UTC)) for i in range(120)}
    reads, inspected = [], []

    def read_all():
        reads.append(True)
        return dict(records)

    worker = SimpleNamespace(
        queued_agent_prompt_store=SimpleNamespace(read_all=read_all, get=records.get),
        agent_prompt_dispatch_port=object(),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    monkeypatch.setattr(hook, "_build_current_execution_graph", lambda **kw: None)
    monkeypatch.setattr(hook, "_container_queue_keys", lambda **kw: set(kw["records"]))
    monkeypatch.setattr(hook, "_retry_not_before_ready", lambda record: True)
    for name in (
        "_has_earlier_subscription_profile_record",
        "_has_earlier_local_capacity_record",
        "_discard_queue_record_for_exact_execution",
        "_has_earlier_claimed_replay_record",
    ):
        monkeypatch.setattr(hook, name, lambda **kw: False)
    monkeypatch.setattr(hook, "_scheduled_wake_queue_record_is_current", lambda **kw: True)
    monkeypatch.setattr(
        hook,
        "_has_waiting_enqueue_prompt_for_record",
        lambda **kw: inspected.append(kw["record"]) or True,
    )
    for expected, remaining in [(50, True), (50, True), (20, False)]:
        reads.clear()
        result = hook.on_post_completion_dispatch_slice(
            worker=worker, budget=MaintenanceBudget(clock=lambda: 0)
        )
        assert result.examined == expected and result.remaining == remaining
        assert len(reads) == 2  # no per-candidate read_all on a no-op pass
    assert inspected == list(records.values())


def test_replay_claim_rechecks_fifo_after_slice_snapshot(monkeypatch):
    from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
    from evander.local_inference_capacity import LOCAL_INFERENCE_QUEUE_REASON

    now = datetime.now(UTC)
    later = SimpleNamespace(
        queue_key="later",
        dispatch_id="later-dispatch",
        created_at=now,
        queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
        retry_not_before=None,
    )
    earlier = SimpleNamespace(
        queue_key="earlier",
        dispatch_id="earlier-dispatch",
        created_at=now - timedelta(seconds=1),
        queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
    )
    claims = []
    # The earlier reservation appeared after the slice selected `later`.
    store = SimpleNamespace(
        read_all=lambda: {"earlier": earlier, "later": later},
        claim_for_replay=lambda *a, **kw: claims.append(a),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    assert (
        hook._claim_queue_record_for_replay(queue_store=store, queue_key="later", record=later)
        is None
    )
    assert claims == []


@pytest.mark.parametrize("vm_count", [50, 100])
def test_queue_recovery_advances_past_failed_vm_inventory(monkeypatch, vm_count):
    from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook

    now = datetime.now(UTC)
    records = {
        f"vm-{i:03d}": SimpleNamespace(created_at=now, target=f"vm-{i:03d}")
        for i in range(vm_count)
    }
    records["container-last"] = SimpleNamespace(
        created_at=now + timedelta(seconds=1), target="container-last"
    )
    inventory_reads, dispatched = [], []

    def inventory():
        inventory_reads.append(True)
        raise RuntimeError("libvirt unavailable")

    worker = SimpleNamespace(
        queued_agent_prompt_store=SimpleNamespace(read_all=lambda: dict(records), get=records.get),
        agent_prompt_dispatch_port=object(),
        agent_run_status_port=SimpleNamespace(list_vm_summaries=inventory),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    monkeypatch.setattr(hook, "_build_current_execution_graph", lambda **kw: None)
    monkeypatch.setattr(
        hook,
        "_container_record_session_context",
        lambda **kw: object() if kw["record"].target == "container-last" else None,
    )
    for name in (
        "_has_earlier_subscription_profile_record",
        "_has_earlier_local_capacity_record",
        "_discard_queue_record_for_exact_execution",
        "_has_earlier_claimed_replay_record",
        "_has_waiting_enqueue_prompt_for_record",
    ):
        monkeypatch.setattr(hook, name, lambda **kw: False)
    monkeypatch.setattr(hook, "_scheduled_wake_queue_record_is_current", lambda **kw: True)

    def dispatch(**kwargs):
        key = kwargs["queue_key"]
        dispatched.append(key)
        records.pop(key)
        return True

    monkeypatch.setattr(hook, "_dispatch_container_record_if_idle", dispatch)
    for _ in range(vm_count // 50):
        result = hook.on_post_completion_dispatch_slice(
            worker=worker, budget=MaintenanceBudget(clock=lambda: 0)
        )
        assert result.remaining and result.changes == 0
    result = hook.on_post_completion_dispatch_slice(
        worker=worker, budget=MaintenanceBudget(clock=lambda: 0)
    )
    assert dispatched == ["container-last"]
    assert result.changes == 1 and not result.remaining
    assert len(records) == vm_count  # Failed VM entries are retained for retry.
    assert len(inventory_reads) == vm_count // 50

    # Ending the pass must make the failed VM pages eligible again.
    hook.on_post_completion_dispatch_slice(
        worker=worker, budget=MaintenanceBudget(clock=lambda: 0)
    )
    assert len(inventory_reads) == vm_count // 50 + 1


@pytest.mark.parametrize(
    "provider", [AgentPromptEffectProvider, AgentEnqueuePromptEffectProvider]
)
@pytest.mark.parametrize("inventory_fails", [False, True])
def test_waiting_recovery_reuses_inventory_and_refreshes_next_slice(
    monkeypatch, provider, inventory_fails
):
    records = [SimpleNamespace(execution_id=f"waiting-{i:02d}", outputs={}) for i in range(10)]
    reads, observations = [], []
    snapshot = []

    def inventory():
        reads.append(True)
        if inventory_fails:
            raise RuntimeError("libvirt unavailable")
        return snapshot

    worker = SimpleNamespace(
        execution_store=SimpleNamespace(list_waiting_records_page=lambda **kw: records),
        agent_run_status_port=SimpleNamespace(list_vm_summaries=inventory),
    )
    monkeypatch.setattr(
        AgentPromptEffectProvider,
        "_build_completion_queue_record_for_waiting_record",
        lambda **kw: observations.append(kw["summaries"]) or None,
    )
    hook = ProviderCompletionLifecycleHook(provider)
    result = hook.on_waiting_reconciliation_slice(
        worker=worker, budget=MaintenanceBudget(clock=lambda: 0)
    )
    assert len(reads) == 1  # Empty and failed inventories are also shared.
    assert result.examined == len(records) and not result.remaining

    inventory_fails = False
    snapshot = [SimpleNamespace(agent_state="idle")]
    observations.clear()
    hook.on_waiting_reconciliation_slice(worker=worker, budget=MaintenanceBudget(clock=lambda: 0))
    assert len(reads) == 2
    assert observations == [snapshot] * len(records)


@pytest.mark.parametrize(
    "provider", [AgentPromptEffectProvider, AgentEnqueuePromptEffectProvider]
)
def test_waiting_recovery_loads_inventory_lazily_and_still_yields(monkeypatch, provider):
    clock = Clock()
    records = [
        SimpleNamespace(
            execution_id="a-container", outputs={"execution_backend": "container_worker_vm"}
        ),
        SimpleNamespace(execution_id="b-vm", outputs={}),
    ]
    reads = []

    def page(**kwargs):
        return [record for record in records if record.execution_id > kwargs["after"]]

    def reconcile_container(**kwargs):
        clock.now += 1  # A slow record exhausts this slice's time budget.
        return 0

    worker = SimpleNamespace(
        execution_store=SimpleNamespace(list_waiting_records_page=page),
        agent_run_status_port=SimpleNamespace(list_vm_summaries=lambda: reads.append(True) or []),
    )
    monkeypatch.setattr(
        AgentPromptEffectProvider, "_reconcile_container_waiting_record", reconcile_container
    )
    hook = ProviderCompletionLifecycleHook(provider)
    first = hook.on_waiting_reconciliation_slice(worker=worker, budget=MaintenanceBudget(clock=clock))
    assert first.examined == 1 and first.remaining
    assert reads == []
    second = hook.on_waiting_reconciliation_slice(worker=worker, budget=MaintenanceBudget(clock=clock))
    assert second.examined == 1 and not second.remaining
    assert reads == [True]


@pytest.mark.parametrize("fails", [False, True])
def test_provider_timing_does_not_include_delayed_reaping(tmp_path, fails):
    worker = _worker(tmp_path, chain_length=1)
    record = EffectExecutionRecord(
        execution_id="timed",
        idempotency_key="timed",
        event_id="sample",
        trace_id="sample",
        source_node_id="start",
        source_trigger_type="trigger:cron",
        effect_id="step-0",
        effect_type="effect:vm:create",
        status="running",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        resolved_config_hash="hash",
    )
    worker.execution_store.record_execution(record)

    def provider():
        if fails:
            raise ValueError("synthetic failure")
        return {"success": True}

    with ThreadPoolExecutor(max_workers=1) as executor:
        worker._executor = executor
        future = worker._submit_provider(provider)
        worker._register_inflight_future(
            idempotency_key="timed", future=future, config_revision="revision"
        )
        try:
            future.result(timeout=1)
        except ValueError:
            pass
        time.sleep(0.05)
        # No executor/callback writes: until reap, durable timing is absent.
        assert worker.execution_store.get_execution("timed").provider_duration_ms is None
        worker._reap_completed_futures()
    saved = worker.execution_store.get_execution("timed")
    assert saved.return_to_reap_ms >= 40
    assert saved.provider_duration_ms < saved.return_to_reap_ms
    assert saved.provider_returned_at < saved.completion_observed_at
    assert saved.finished_at == saved.completion_observed_at
    assert saved.status == ("failed" if fails else "succeeded")


def test_noop_team_recovery_uses_projections_not_full_state(tmp_path, monkeypatch):
    repo = _seed_team(tmp_path)
    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend", defer_activation=True
    )
    dispatch_id = leases[0]["dispatch_id"]
    seen = []
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=SimpleNamespace(
            list_prompt_records_for_dispatches=lambda ids: seen.append(ids) or [],
        ),
    )

    def full_read_forbidden(*args, **kwargs):
        raise AssertionError("no-op reconciliation deserialized full Team state")

    monkeypatch.setattr(TeamStateSqliteStore, "_read_state", full_read_forbidden)
    hook = TeamAgentPromptCompletionFinalizeHook()
    result = hook.on_post_completion_dispatch_slice(worker=worker, budget=MaintenanceBudget())
    assert result.changes == 0
    assert seen == [{dispatch_id}]


def test_failed_before_activation_is_not_a_completion_or_history_candidate(tmp_path):
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend", defer_activation=True
    )
    dispatch_id = leases[0]["dispatch_id"]
    with repo.store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE team_dispatches
               SET status = 'failed', completed_at = ?, activation_completed_at = NULL,
                   session_metadata_mirrored_at = NULL
               WHERE id = ?""",
            (datetime.now(UTC).isoformat(), dispatch_id),
        )
        connection.execute(
            "UPDATE team_agent_runtimes SET active_dispatch_id = NULL WHERE agent_id = 'backend'"
        )

    assert repo.lifecycle_candidates(kind="completion").candidates == ()
    assert repo.lifecycle_candidates(kind="history").candidates == ()


def test_native_receipt_preflight_matches_last_wins_timestamp_ties(monkeypatch):
    from evander_team_addon.lifecycle_candidates import LifecycleCandidate
    from evander_team_addon.services.runtime_service import AgentDispatchNativeThreadReceipt

    now = datetime.now(UTC)
    candidate = LifecycleCandidate(
        dispatch_id="dispatch",
        session_id="session",
        agent_id="agent",
        status="completed",
        active=False,
        activation_completed_at=now,
        native_thread_id="native",
        native_thread_observed_at=now,
        lineage_thread_id="native",
        lineage_owner_id="dispatch",
        current_session_id="session",
        queued_at=None,
    )
    records = [
        SimpleNamespace(
            execution_id=str(i),
            effect_type="effect:agent:enqueue-prompt",
            status="succeeded",
            finished_at=now,
            outputs={"session_id": "session", "dispatch_id": "dispatch"},
        )
        for i in range(2)
    ]
    monkeypatch.setattr(
        "evander_team_addon.providers._team_native_thread_receipt",
        lambda r: AgentDispatchNativeThreadReceipt(
            "session", "dispatch", "native", now, missing_thread=r.execution_id == "1"
        ),
    )
    hook = TeamAgentPromptCompletionFinalizeHook()
    calls = []
    monkeypatch.setattr(hook, "_reconcile_selected_completion", lambda **kw: calls.append(kw) or 0)
    hook._reconcile_candidate(None, None, candidate, records)
    assert len(calls) == 1  # newer/tie-later missing receipt cannot be skipped


def test_lifecycle_projection_snapshot_pages_and_query_indexes(tmp_path, monkeypatch):
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend", defer_activation=True
    )
    dispatch_id = leases[0]["dispatch_id"]
    original_connect = repo.store._read_connection
    statements = []

    def connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repo.store, "_read_connection", connect)
    page = repo.lifecycle_candidates(limit=1)
    assert page.revision == repo.read_revision()
    assert page.candidates[0].dispatch_id == dispatch_id
    selects = [
        s
        for s in statements
        if s.startswith("BEGIN") or "SELECT value FROM team_state_metadata" in s or "WITH ids" in s
    ]
    assert selects[0] == "BEGIN"
    assert len(selects) == 3
    with sqlite3.connect(repo.store.path) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM team_dispatches WHERE status IN ('preparing','leased') AND activation_completed_at IS NULL AND id > '' ORDER BY id LIMIT 50"
        ).fetchall()
    assert any("idx_team_dispatches_pending_activation" in row[3] for row in plan)


def test_lifecycle_projection_cannot_mix_revisions(tmp_path, monkeypatch):
    repo = _seed_team(tmp_path)
    _, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend", defer_activation=True
    )
    dispatch_id = leases[0]["dispatch_id"]
    revision = repo.read_revision()
    connect = repo.store._read_connection
    changed = []

    def concurrent_connect():
        connection = connect()

        def on_statement(sql):
            if "WITH ids AS" in sql and not changed:
                changed.append(True)
                with sqlite3.connect(repo.store.path) as writer:
                    writer.execute(
                        "UPDATE team_dispatches SET session_id = 'replacement' WHERE id = ?",
                        (dispatch_id,),
                    )
                    writer.execute(
                        "UPDATE team_state_metadata SET value = ? WHERE key = 'revision'",
                        (str(revision + 1),),
                    )

        connection.set_trace_callback(on_statement)
        return connection

    monkeypatch.setattr(repo.store, "_read_connection", concurrent_connect)
    page = repo.lifecycle_candidates()
    assert changed and page.revision == revision
    assert page.candidates[0].session_id == "session-backend"
    assert repo.read_revision() == revision + 1


def test_lifecycle_indexes_drop_obsolete_mirror_index(tmp_path):
    store = TeamStateSqliteStore(tmp_path)
    revision = store.read_revision()
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """CREATE INDEX idx_team_dispatches_pending_mirror
               ON team_dispatches(id) WHERE session_metadata_mirrored_at IS NULL"""
        )
    reopened = TeamStateSqliteStore(tmp_path)
    assert reopened.read_revision() == revision
    assert reopened.lifecycle_candidates().candidates == ()
    with sqlite3.connect(store.path) as connection:
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='idx_team_dispatches_pending_mirror'"
        ).fetchone()
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='idx_team_dispatches_receipt_recovery'"
        ).fetchone()


def test_generic_replay_checks_current_team_reservation(tmp_path):
    from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
    from evander.orchestration.providers.agent_defaults import _ProviderCompletionDispatchHook

    repo = _seed_team(tmp_path)
    runtime = _team_runtime_service(repo)
    _, leases = runtime.lease_due_agents(agent_id="backend", defer_activation=True)
    dispatch_id = leases[0]["dispatch_id"]
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        _completion_dispatch_hooks=(
            _ProviderCompletionDispatchHook(TeamAgentPromptCompletionFinalizeHook()),
        ),
    )
    record = SimpleNamespace(dispatch_id=dispatch_id, session_id="session-backend")
    validate = AgentEnqueuePromptQueueDispatchHook._validate_queue_ownership
    assert validate(worker, record)

    def replace(state):
        state.agent_runtimes["backend"].active_dispatch_id = None

    repo.update(replace)
    assert not validate(worker, record)
    assert validate(
        worker, SimpleNamespace(dispatch_id="ordinary-admin", session_id="session-backend")
    )


def test_prompt_dispatch_query_ignores_large_unrelated_history(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=1)
    _publish(worker)
    worker._executor = _ImmediateExecutor()
    worker.run_once()
    record = worker.execution_store.list_executions()[0]
    record.effect_type = "effect:agent:enqueue-prompt"
    record.outputs = {"dispatch_id": "wanted", "session_id": "session"}
    worker.execution_store.record_execution(record)
    with connect_orchestration_db(worker.execution_store.path) as connection:
        # Invalid model payloads are intentionally safe only when not hydrated.
        connection.executemany(
            """INSERT INTO effect_executions
            (execution_id,idempotency_key,event_id,trace_id,source_node_id,source_trigger_type,
             status,effect_id,effect_type,started_at,finished_at,payload_json)
            VALUES (?,?,'history','history','start','trigger:cron','succeeded','step-0',
             'effect:agent:enqueue-prompt','2026-01-01','2026-01-01',?)""",
            [
                (
                    f"history-{i}",
                    f"history-{i}",
                    json.dumps({"outputs": {"dispatch_id": f"old-{i}"}}),
                )
                for i in range(5000)
            ],
        )
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT payload_json FROM effect_executions WHERE effect_type='effect:agent:enqueue-prompt' AND json_extract(payload_json, '$.outputs.dispatch_id') IN ('wanted') AND status IN ('waiting','succeeded','failed')"
        ).fetchall()
    assert any("idx_effect_executions_prompt_dispatch" in row[3] for row in plan)
    assert [
        r.execution_id
        for r in worker.execution_store.list_prompt_records_for_dispatches({"wanted"})
    ] == [record.execution_id]


def _seed_latency_history(worker, tmp_path):
    repo = _seed_team(tmp_path)
    with sqlite3.connect(repo.store.path) as connection:
        connection.executemany(
            """INSERT INTO team_dispatches
            (id,agent_id,session_id,cause_kind,status,created_at,activation_completed_at,
             completed_at,session_metadata_mirrored_at,native_thread_observed_at)
            VALUES (?,'backend','session-backend','scheduled_wake','completed',
              '2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',
              '2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',
              '2026-01-01T00:00:00+00:00')""",
            [(f"history-{i:05d}",) for i in range(5000)],
        )
    with connect_orchestration_db(worker.execution_store.path) as connection:
        connection.executemany(
            """INSERT INTO effect_executions
            (execution_id,idempotency_key,event_id,trace_id,source_node_id,source_trigger_type,
             status,effect_id,effect_type,started_at,finished_at,payload_json)
            VALUES (?,?,'history','history','start','trigger:cron','succeeded','step-0',
             'effect:agent:enqueue-prompt','2026-01-01','2026-01-01',?)""",
            [
                (
                    f"history-{i}",
                    f"history-{i}",
                    json.dumps({"outputs": {"dispatch_id": f"unrelated-{i}"}}),
                )
                for i in range(10000)
            ],
        )


def test_thirty_synthetic_chains_handoff_p95_under_two_seconds(tmp_path):
    """Local scheduler benchmark only: external work/model/clone are stubbed."""
    worker = _worker(tmp_path)
    _seed_latency_history(worker, tmp_path)
    from evander.orchestration.providers.agent_defaults import (
        _ProviderCompletionDispatchHook,
        _ProviderLifecycleReconciliationHook,
    )

    team = TeamAgentPromptCompletionFinalizeHook()
    worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(team),)
    worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(team),)
    samples = []
    for sample in range(30):
        trace_id = f"sample-{sample}"
        _publish(worker, trace_id)
        # Include due recovery during the measured chains, not only idle time.
        worker._recovery_schedule.next_due = 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            worker.run_once()
            with connect_orchestration_db(worker.execution_store.path) as connection:
                rows = connection.execute(
                    "SELECT payload_json FROM effect_executions WHERE trace_id = ?", (trace_id,)
                ).fetchall()
            records = [
                EffectExecutionRecord.model_validate_json(row["payload_json"]) for row in rows
            ]
            if len(records) == 6 and all(r.status == "succeeded" for r in records):
                break
        assert len(records) == 6 and all(r.status == "succeeded" for r in records)
        ordered = sorted(records, key=lambda r: r.effect_id)
        samples.append(
            sum(
                (b.provider_started_at - a.provider_returned_at).total_seconds()
                for a, b in zip(ordered, ordered[1:])
            )
        )
    p95 = sorted(samples)[28]
    print(
        f"30 six-stage synthetic chains with 10k executions / 5k Team dispatches: cumulative handoff p95={p95:.4f}s max={max(samples):.4f}s"
    )
    assert p95 < 2
