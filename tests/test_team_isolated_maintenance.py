from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, current_thread
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.orchestration.host_polling import single_instance_lock
from evander.orchestration.isolated_maintenance import IsolatedMaintenanceHook
from evander.orchestration.maintenance import MaintenanceBudget, MaintenanceResult
from evander.orchestration.providers.agent_defaults import (
    _ProviderCompletionDispatchHook,
    _ProviderLifecycleReconciliationHook,
    build_agent_default_effect_handlers,
)
from evander_team_addon.providers import TeamAgentPromptCompletionFinalizeHook
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.sqlite_store import TeamStateSqliteStore
from test_orchestration_handoff_latency import _publish, _worker, _seed_latency_history
from test_effect_orchestrator import _ImmediateExecutor, _StaticReloader
from test_team_addon_runtime import (
    FakeSessionStore,
    _seed_team,
    _team_admin_service,
    _team_runtime_service,
)


def _context(tmp_path):
    return SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=SimpleNamespace(list_prompt_records_for_dispatches=lambda ids: []),
        _wake_notifier=SimpleNamespace(notify=lambda: None),
    )


class BlockingHook:
    maintenance_priority = -100

    def __init__(self):
        self.entered, self.release = Event(), Event()
        self.calls = []

    def initialize(self, *, worker):
        self.calls.append(("initialize", str(worker.config.runtime_dir)))

    def on_waiting_reconciliation_slice(self, *, worker, budget):
        self.calls.append("leases")
        self.entered.set()
        assert self.release.wait(10), "test did not release maintenance barrier"
        return MaintenanceResult(examined=0)

    def on_post_completion_dispatch_slice(self, *, worker, budget):
        self.calls.append("completion")
        return MaintenanceResult(examined=0)

    def on_prompt_execution_changed(self, *, worker, record):
        self.calls.append(record.outputs["dispatch_id"])


def _drain(lane, worker):
    # Wait only for executor work in the test; production poll never waits.
    for _ in range(120):
        lane.poll(worker=worker)
        future = lane._future
        if future is None:
            return
        future.result(timeout=5)
    raise AssertionError("maintenance failed to drain bounded hints")


def test_factory_shares_one_lane_across_all_team_lifecycle_entry_points(monkeypatch):
    team_hook = TeamAgentPromptCompletionFinalizeHook()
    legacy_hook = BlockingHook()
    monkeypatch.setattr(
        "evander.orchestration.providers.agent_defaults.build_effect_lifecycle_hooks",
        lambda **kwargs: (team_hook, legacy_hook),
    )
    events, reconciliation, completion = build_agent_default_effect_handlers(provider_factory=None)
    lane = events[0]._lifecycle_hooks[0]
    assert isinstance(lane, IsolatedMaintenanceHook)
    assert lane._hook is team_hook
    assert reconciliation[0]._hook is lane
    assert completion[0]._hook is lane
    assert completion[0].maintenance_name == "TeamAgentPromptCompletionFinalizeHook"
    assert events[0]._lifecycle_hooks[1] is legacy_hook
    assert reconciliation[1]._hook is legacy_hook
    assert completion[1]._hook is legacy_hook


def test_ownership_read_failure_cannot_authorize_queue_replay(tmp_path, monkeypatch):
    worker = _context(tmp_path)
    lane = IsolatedMaintenanceHook(TeamAgentPromptCompletionFinalizeHook())
    lane.initialize(worker=worker)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(lane._hook._repository, "lifecycle_candidates", unavailable)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        lane.validate_queued_prompt(
            worker=worker, record=SimpleNamespace(dispatch_id="team-dispatch-active")
        )


def test_blocked_maintenance_does_not_hold_foreground_fifo(tmp_path):
    worker = _worker(tmp_path, chain_length=6)
    worker._executor = _ImmediateExecutor()
    hook = BlockingHook()
    lane = IsolatedMaintenanceHook(hook)
    worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(lane),)
    worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(lane),)
    worker._initialize_lifecycle_hooks()
    try:
        lane.on_waiting_reconciliation(worker=worker)
        assert hook.entered.wait(2)
        _publish(worker)
        started = time.monotonic()
        for _ in range(20):
            worker.run_once()
            if len(worker.execution_store.list_executions()) == 6:
                break
        records = worker.execution_store.list_executions()
        assert len(records) == 6
        assert all(r.status == "succeeded" for r in records)
        assert time.monotonic() - started < 5
        assert not hook.release.is_set()  # causality, not just a fast idle run
    finally:
        hook.release.set()
        worker.close()


def test_slow_receipt_query_does_not_hold_foreground_execution_store(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=6)
    worker._executor = _ImmediateExecutor()
    repo = _seed_team(tmp_path)
    _, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend", defer_activation=True
    )
    lane = IsolatedMaintenanceHook(TeamAgentPromptCompletionFinalizeHook())
    worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(lane),)
    worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(lane),)
    worker._initialize_lifecycle_hooks()
    entered, release = Event(), Event()
    connect = sqlite3.connect

    def slow_receipt_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        if current_thread().name.startswith("team-maintenance"):
            def trace(statement):
                if "SELECT payload_json FROM effect_executions" in statement:
                    entered.set()
                    release.wait(10)

            connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(sqlite3, "connect", slow_receipt_connection)
    try:
        lane.on_prompt_execution_changed(
            worker=worker,
            record=SimpleNamespace(outputs={"dispatch_id": leases[0]["dispatch_id"]}),
        )
        assert entered.wait(2)
        _publish(worker)

        def dispatch_chain():
            for _ in range(20):
                worker.run_once()
                records = worker.execution_store.list_executions()
                if len(records) == 6 and all(r.status == "succeeded" for r in records):
                    return
            raise AssertionError("foreground chain did not finish")

        with ThreadPoolExecutor(max_workers=1) as executor:
            cycle = executor.submit(dispatch_chain)
            try:
                cycle.result(timeout=3)
                assert not release.is_set()
            finally:
                release.set()
    finally:
        release.set()
        worker.close()


@pytest.mark.parametrize("failed", [False, True])
def test_single_cycle_drains_maintenance_before_releasing_worker_lock(
    tmp_path, monkeypatch, failed
):
    worker = _worker(tmp_path)
    hook = BlockingHook()
    lane = IsolatedMaintenanceHook(hook)
    worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(lane),)
    worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(lane),)
    worker._initialize_lifecycle_hooks()
    worker._recovery_schedule.next_due = 0
    close_entered = Event()
    close = lane.close

    def observed_close():
        close_entered.set()
        close()

    def run_cycle():
        result = worker.run_once()
        if failed:
            raise RuntimeError("cycle failed")
        return result

    monkeypatch.setattr(lane, "close", observed_close)
    monkeypatch.setattr(worker._kernel, "_run_cycle", run_cycle)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            cycle = executor.submit(worker.run_once_with_lock)
            try:
                assert hook.entered.wait(2)
                assert close_entered.wait(2)
                assert not cycle.done()
                with pytest.raises(RuntimeError, match="worker already running"):
                    with single_instance_lock(worker.lock_path):
                        pass
            finally:
                hook.release.set()
            if failed:
                with pytest.raises(RuntimeError, match="cycle failed"):
                    cycle.result(timeout=5)
            else:
                cycle.result(timeout=5)
        with single_instance_lock(worker.lock_path):
            assert lane._future is None
        # The same worker can acquire the lock for another independent cycle.
        worker._recovery_schedule.next_due = 0
        if failed:
            with pytest.raises(RuntimeError, match="cycle failed"):
                worker.run_once_with_lock()
        else:
            worker.run_once_with_lock()
        assert hook.calls.count("leases") == 2
    finally:
        hook.release.set()
        worker.close()


def test_graph_reload_keeps_maintenance_running_without_resetting_cursors(tmp_path):
    worker = _worker(tmp_path, chain_length=6)
    worker._executor = _ImmediateExecutor()
    hook = BlockingHook()
    lane = IsolatedMaintenanceHook(hook)
    worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(lane),)
    worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(lane),)
    worker._initialize_lifecycle_hooks()
    previous_config = worker.worker_config
    updated = previous_config.model_copy(
        update={
            "triggers": [
                previous_config.triggers[0].model_copy(update={"display_name": "Renamed trigger"})
            ]
        }
    )
    worker._config_reloader = _StaticReloader(updated, changed=True)
    try:
        lane.on_waiting_reconciliation(worker=worker)
        assert hook.entered.wait(2)
        _publish(worker)
        with ThreadPoolExecutor(max_workers=1) as executor:
            cycle = executor.submit(worker.run_once)
            try:
                cycle.result(timeout=3)
                assert not hook.release.is_set()
                assert worker.worker_config is updated
                assert worker.execution_store.list_executions()
                assert hook.calls == [("initialize", str(tmp_path)), "leases"]
            finally:
                hook.release.set()
    finally:
        hook.release.set()
        worker.close()


def test_requests_coalesce_and_leases_precede_completion_and_targets(tmp_path):
    worker, hook = _context(tmp_path), BlockingHook()
    lane = IsolatedMaintenanceHook(hook)
    lane.initialize(worker=worker)
    try:
        lane.on_waiting_reconciliation(worker=worker)
        assert hook.entered.wait(2)
        for _ in range(100):
            lane.on_waiting_reconciliation(worker=worker)
            lane.on_post_completion_dispatch(worker=worker)
            lane.on_prompt_execution_changed(
                worker=worker, record=SimpleNamespace(outputs={"dispatch_id": "same"})
            )
        assert hook.calls[1:] == ["leases"]
        assert lane._targets == {"same": None}
        assert lane._pending == {"leases", "completion"}
        hook.release.set()
        _drain(lane, worker)
        assert hook.calls[1:] == ["leases", "leases", "same", "completion"]
    finally:
        hook.release.set()
        lane.close()


def test_target_hints_are_bounded_and_recovery_remains_scheduled(tmp_path):
    worker, hook = _context(tmp_path), BlockingHook()
    lane = IsolatedMaintenanceHook(hook)
    try:
        lane.on_waiting_reconciliation(worker=worker)
        assert hook.entered.wait(2)
        for i in range(500):
            lane.on_prompt_execution_changed(
                worker=worker, record=SimpleNamespace(outputs={"dispatch_id": str(i)})
            )
        lane.on_post_completion_dispatch(worker=worker)
        assert len(lane._targets) == 50
        assert "completion" in lane._pending
        hook.release.set()
        _drain(lane, worker)
        assert hook.calls[-1] == "completion"
    finally:
        hook.release.set()
        lane.close()


def test_busy_operation_retries_at_recovery_cadence(tmp_path, monkeypatch):
    worker, hook = _context(tmp_path), BlockingHook()
    attempts = []
    clock = [0.0]
    monkeypatch.setattr(
        "evander.orchestration.isolated_maintenance.time.monotonic", lambda: clock[0]
    )

    def renew(**kwargs):
        attempts.append(clock[0])
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return MaintenanceResult(changes=1, examined=1)

    hook.on_waiting_reconciliation_slice = renew
    lane = IsolatedMaintenanceHook(hook)
    try:
        lane.on_waiting_reconciliation(worker=worker)
        with pytest.raises(sqlite3.OperationalError):
            lane._future.result(timeout=2)
        lane.poll(worker=worker)
        for _ in range(20):
            lane.on_waiting_reconciliation(worker=worker)
        assert attempts == [0]
        clock[0] = 5
        lane.poll(worker=worker)
        lane._future.result(timeout=2)
        assert lane.poll(worker=worker) == 1
        assert attempts == [0, 5]
    finally:
        lane.close()


def test_continuous_target_hints_do_not_starve_receipt_recovery(tmp_path):
    worker, hook = _context(tmp_path), BlockingHook()
    lane = IsolatedMaintenanceHook(hook)
    try:
        lane.on_waiting_reconciliation(worker=worker)
        assert hook.entered.wait(2)
        lane.on_post_completion_dispatch(worker=worker)
        for i in range(50):
            lane.on_prompt_execution_changed(
                worker=worker, record=SimpleNamespace(outputs={"dispatch_id": str(i)})
            )
        hook.release.set()
        for i in range(60):
            lane._future.result(timeout=2)
            lane.on_prompt_execution_changed(
                worker=worker, record=SimpleNamespace(outputs={"dispatch_id": f"new-{i}"})
            )
            if "completion" in hook.calls:
                break
        assert "completion" in hook.calls
        assert hook.calls.index("completion") <= 51  # one lease, at most 50 targets
        assert lane._targets  # the test never drained the competing hint stream
    finally:
        hook.release.set()
        lane.close()


def test_close_drains_before_runtime_rebinding(tmp_path):
    worker, hook = _context(tmp_path), BlockingHook()
    lane = IsolatedMaintenanceHook(hook)
    lane.initialize(worker=worker)
    lane.on_waiting_reconciliation(worker=worker)
    assert hook.entered.wait(2)
    with ThreadPoolExecutor(max_workers=1) as executor:
        closed = executor.submit(lane.close)
        try:
            assert not closed.done()
        finally:
            hook.release.set()
        closed.result(timeout=5)
    worker.config = GatewayConfig(runtime_dir=tmp_path / "new")
    lane.initialize(worker=worker)
    try:
        lane.on_post_completion_dispatch(worker=worker)
        _drain(lane, worker)
        assert hook.calls[-2:] == [("initialize", str(tmp_path / "new")), "completion"]
    finally:
        lane.close()


def test_worker_rechecks_old_bus_after_draining_maintenance(tmp_path, monkeypatch):
    worker = _worker(tmp_path)
    worker._executor = _ImmediateExecutor()
    previous_config = worker.worker_config
    old_store = worker.execution_store
    updated = previous_config.model_copy(
        update={"orchestration_store_path": tmp_path / "new-orchestration.db"}
    )
    worker._config_reloader = _StaticReloader(updated, changed=True)
    hook = BlockingHook()
    close_entered = Event()

    def publish_during_drain(**kwargs):
        hook.entered.set()
        assert hook.release.wait(10)
        assert worker.worker_config is previous_config
        assert worker.execution_store is old_store
        _publish(worker, event_id="published-during-drain")
        return MaintenanceResult(changes=1)

    hook.on_waiting_reconciliation_slice = publish_during_drain
    lane = IsolatedMaintenanceHook(hook)
    worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(lane),)
    worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(lane),)
    worker._initialize_lifecycle_hooks()
    close = lane.close

    def observed_close():
        close_entered.set()
        close()

    monkeypatch.setattr(lane, "close", observed_close)
    try:
        lane.on_waiting_reconciliation(worker=worker)
        assert hook.entered.wait(2)
        with ThreadPoolExecutor(max_workers=1) as executor:
            cycle = executor.submit(worker.run_once)
            try:
                assert close_entered.wait(5)
                assert not cycle.done()
            finally:
                hook.release.set()
            cycle.result(timeout=10)
        assert worker.worker_config is previous_config
        assert worker.execution_store is old_store
        # Let the existing queue drain without requesting the store switch yet.
        worker._config_reloader = _StaticReloader(previous_config, changed=False)
        for _ in range(10):
            worker.run_once()
        assert len(old_store.list_executions()) == 6
        assert all(r.status == "succeeded" for r in old_store.list_executions())
        assert not worker.event_queue.list_pending()
        worker._config_reloader = _StaticReloader(updated, changed=True)
        worker.run_once()
        assert worker.worker_config is updated
        assert worker.execution_store is not old_store
    finally:
        hook.release.set()
        worker.close()


def test_repeated_team_slices_do_not_initialize_schema_or_negotiate_wal(tmp_path, monkeypatch):
    worker = _context(tmp_path)
    hook = TeamAgentPromptCompletionFinalizeHook()
    hook.initialize(worker=worker)

    def forbidden(*args, **kwargs):
        raise AssertionError("schema/WAL initialization in steady-state maintenance")

    monkeypatch.setattr(TeamStateSqliteStore, "ensure_schema", forbidden)
    monkeypatch.setattr(TeamStateSqliteStore, "_enable_wal", forbidden)
    for _ in range(3):
        hook.on_waiting_reconciliation(worker=worker)
        hook.on_post_completion_dispatch(worker=worker)
        hook.on_prompt_execution_changed(
            worker=worker, record=SimpleNamespace(outputs={"dispatch_id": "absent"})
        )
        assert not hook.validate_queued_prompt(
            worker=worker, record=SimpleNamespace(dispatch_id="team-dispatch-absent")
        )


def test_lifecycle_reader_does_not_wait_for_team_write_lock(tmp_path):
    repo = _seed_team(tmp_path)
    _, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend", defer_activation=True
    )
    worker = _context(tmp_path)
    hook = TeamAgentPromptCompletionFinalizeHook()
    hook.initialize(worker=worker)
    with sqlite3.connect(repo.store.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE team_state_metadata SET value='123456' WHERE key='revision'")
        # SQLite readers must see the previous committed revision and ownership.
        with ThreadPoolExecutor(max_workers=1) as executor:
            page = executor.submit(hook._repository.lifecycle_candidates).result(timeout=2)
        assert page.revision != 123456
        assert page.candidates[0].dispatch_id == leases[0]["dispatch_id"]
        assert hook.validate_queued_prompt(
            worker=worker,
            record=SimpleNamespace(
                dispatch_id=leases[0]["dispatch_id"], session_id="session-backend"
            ),
        )
        writer.rollback()


def test_contended_renewal_preserves_cursor_then_succeeds(tmp_path, monkeypatch):
    repo = _seed_team(tmp_path)
    now = datetime.now(UTC)
    _, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=now,
        lease_seconds=60,
    )
    dispatch_id = leases[0]["dispatch_id"]
    record = SimpleNamespace(
        status="waiting", outputs={"dispatch_id": dispatch_id, "session_id": "session-backend"}
    )
    worker = _context(tmp_path)
    worker.execution_store.list_prompt_records_for_dispatches = lambda ids: [record]
    monkeypatch.setattr(
        "evander_team_addon.provider_completion._team_dispatch_execution_is_live", lambda *a, **k: True
    )
    hook = TeamAgentPromptCompletionFinalizeHook()
    hook.initialize(worker=worker)
    before = repo.read().dispatches[dispatch_id].lease_expires_at
    with sqlite3.connect(repo.store.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            hook.on_waiting_reconciliation_slice(worker=worker, budget=MaintenanceBudget())
        assert time.monotonic() - started < 1
        assert hook._lease_cursor == ""
        assert repo.read().dispatches[dispatch_id].lease_expires_at == before
        writer.rollback()
    result = hook.on_waiting_reconciliation_slice(worker=worker, budget=MaintenanceBudget())
    assert result.changes == 1
    assert repo.read().dispatches[dispatch_id].lease_expires_at > before


def test_repository_rebind_resets_cursors_and_initializes_new_database(tmp_path):
    worker = _context(tmp_path / "first")
    hook = TeamAgentPromptCompletionFinalizeHook()
    hook.initialize(worker=worker)
    original = hook._repository
    hook._completion_cursor = "stale-receipt-store"
    hook.initialize(worker=worker)
    assert hook._repository is original
    assert hook._completion_cursor == ""
    hook._lease_cursor = "stale"
    worker.worker_config = SimpleNamespace(runtime_dir=tmp_path / "second")
    hook.initialize(worker=worker)
    assert hook._repository is not original
    assert hook._repository.runtime_dir == tmp_path / "second"
    assert hook._lease_cursor == ""
    assert hook._repository.lifecycle_candidates().candidates == ()


def test_replaced_database_requires_reinitialization(tmp_path):
    worker = _context(tmp_path)
    hook = TeamAgentPromptCompletionFinalizeHook()
    hook.initialize(worker=worker)
    original = hook._repository.store.path
    original.rename(original.with_suffix(".previous"))
    TeamStateRepository(tmp_path)
    with pytest.raises(RuntimeError, match="database replaced"):
        hook.on_waiting_reconciliation(worker=worker)
    restarted = TeamAgentPromptCompletionFinalizeHook()
    restarted.initialize(worker=worker)
    assert restarted.on_waiting_reconciliation(worker=worker) == 0


def test_read_only_connection_cannot_write(tmp_path):
    repo = TeamStateRepository(tmp_path, maintenance=True)
    with repo.store._read_connection() as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM team_dispatches")


def test_two_extra_teams_with_history_and_lock_contention_keep_message_p95_under_five_seconds(
    tmp_path,
    monkeypatch,
):
    worker = _worker(tmp_path, chain_length=6)
    worker._executor = _ImmediateExecutor()
    worker.provider_factory.admin_service = FakeSessionStore()
    _seed_latency_history(worker, tmp_path)
    repo = TeamStateRepository(tmp_path)
    admin = _team_admin_service(repo)
    records = []
    dispatch_ids = []
    for i in range(2):
        state = admin.create_team(
            team_id=f"extra-{i}",
            name=f"Extra {i}",
            manager_agent_id=f"manager-{i}",
            manager_session_id=f"session-manager-{i}",
            expected_revision=repo.read_revision(),
        )
        state = admin.create_project(
            project_id=f"project-{i}",
            team_id=f"extra-{i}",
            name=f"Project {i}",
            lead_agent_id=f"lead-{i}",
            lead_session_id=f"session-lead-{i}",
            goals=[{"id": "ship", "description": "Ship work"}],
            expected_revision=state.revision,
        )
        admin.create_or_reprioritize_task(
            task_id=f"task-{i}",
            owner_agent_id=f"lead-{i}",
            project_id=f"project-{i}",
            priority=1,
            title="Work",
            description="Work",
            created_by_agent_id=f"manager-{i}",
            expected_revision=state.revision,
        )
        _, leases = _team_runtime_service(repo).lease_due_agents(
            agent_id=f"lead-{i}",
            lease_seconds=60,
            respect_wake_interval=False,
        )
        assert len(leases) == 1
        dispatch_id = leases[0]["dispatch_id"]
        dispatch_ids.append(dispatch_id)
        records.append(
            SimpleNamespace(
                execution_id=f"active-{i}",
                effect_type="effect:agent:enqueue-prompt",
                status="waiting",
                finished_at=datetime.now(UTC),
                outputs={
                    "dispatch_id": dispatch_id,
                    "session_id": f"session-lead-{i}",
                    "success": True,
                    "queued": False,
                },
            )
        )
    actual_query = worker.execution_store.list_prompt_records_for_dispatches
    monkeypatch.setattr(
        worker.execution_store,
        "list_prompt_records_for_dispatches",
        lambda ids: [r for r in records if r.outputs["dispatch_id"] in ids] + actual_query(ids),
    )
    monkeypatch.setattr(
        "evander_team_addon.provider_completion._team_dispatch_execution_is_live", lambda *a, **k: True
    )
    team = TeamAgentPromptCompletionFinalizeHook()
    lane = IsolatedMaintenanceHook(team)
    worker._lifecycle_reconciliation_hooks = (_ProviderLifecycleReconciliationHook(lane),)
    worker._completion_dispatch_hooks = (_ProviderCompletionDispatchHook(lane),)
    worker._initialize_lifecycle_hooks()
    samples = []
    original_expiries = [repo.read().dispatches[d].lease_expires_at for d in dispatch_ids]
    try:
        with sqlite3.connect(repo.store.path) as writer:
            writer.execute("BEGIN IMMEDIATE")
            lane.on_waiting_reconciliation(worker=worker)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                lane._future.result(timeout=5)
            lane.poll(worker=worker)
            for i in range(30):
                trace = f"message-{i}"
                start = time.monotonic()
                _publish(worker, trace)
                worker._recovery_schedule.next_due = 0
                for _ in range(30):
                    worker.run_once()
                    with sqlite3.connect(worker.execution_store.path) as connection:
                        completed = connection.execute(
                            "SELECT count(*) FROM effect_executions WHERE trace_id=? AND status='succeeded'",
                            (trace,),
                        ).fetchone()[0]
                    if completed == 6:
                        break
                assert completed == 6
                samples.append(time.monotonic() - start)
            writer.rollback()
        p95 = sorted(samples)[28]
        print(
            f"30 messages / 2 additional active teams / 5k dispatch history: p95={p95:.3f}s max={max(samples):.3f}s"
        )
        assert p95 < 5
        # Make the deferred lease eligible without sleeping in the test.
        lane._retry_at.clear()
        lane.on_waiting_reconciliation(worker=worker)
        _drain(lane, worker)
        state = repo.read()
        for dispatch_id, before in zip(dispatch_ids, original_expiries):
            assert state.dispatches[dispatch_id].lease_expires_at > before
            assert state.dispatches[dispatch_id].lease_expires_at > datetime.now(UTC) + timedelta(
                minutes=5
            )
        # Drop in-memory hints, as at restart. Durable receipt recovery must finish each once.
        lane.close()
        for record in records:
            record.status = "succeeded"
        restarted = IsolatedMaintenanceHook(TeamAgentPromptCompletionFinalizeHook())
        restarted.initialize(worker=worker)
        try:
            restarted.on_post_completion_dispatch(worker=worker)
            _drain(restarted, worker)
            state = repo.read()
            assert all(state.dispatches[d].status.value == "completed" for d in dispatch_ids)
            revision = state.revision
            for record in records:
                restarted.on_prompt_execution_changed(worker=worker, record=record)
            _drain(restarted, worker)
            assert repo.read_revision() == revision
        finally:
            restarted.close()
    finally:
        worker.close()
