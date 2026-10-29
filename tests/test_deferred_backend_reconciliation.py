import threading
import time
from types import SimpleNamespace

import pytest

from evander.orchestration.deferred_backend import DeferredBackendReconciliation


def record(owner="effect-a", target="job-a"):
    return SimpleNamespace(execution_id=owner, status="waiting", outputs={"execution_id": target})


def drain(pool, rec, backend, reconcile, wake):
    for _ in range(20):
        result = pool.run(record=rec, backend=backend, reconcile=reconcile)
        if result:
            return result
        assert wake.wait(2), "background RPC did not finish"
        wake.clear()
    raise AssertionError("reconciliation did not finish")


def test_slow_rpc_does_not_block_foreground_or_repeat_completed_steps():
    wake, entered, release = threading.Event(), threading.Event(), threading.Event()
    calls, published = [], []
    main_thread = threading.get_ident()

    def inspect(target):
        calls.append(("inspect", target))
        entered.set()
        assert release.wait(5)
        return "stopped"

    def logs(target):
        calls.append(("logs", target))
        return "finished"

    def destroy(target):
        calls.append(("destroy", target))

    backend = SimpleNamespace(inspect=inspect, get_log_snapshot=logs, destroy=destroy)
    pool = DeferredBackendReconciliation(notify=wake.set)
    rec = record()

    def reconcile(port):
        assert port.inspect("job-a") == "stopped"
        assert port.get_log_snapshot("job-a") == "finished"
        port.destroy("job-a")
        published.append(threading.get_ident())
        return 1

    try:
        assert pool.run(record=rec, backend=backend, reconcile=reconcile) == 0
        assert entered.wait(1) and not release.is_set()
        # Other work can run before the blocked inspect is released.
        assert (
            pool.run(
                record=record("effect-b", "job-b"), backend=SimpleNamespace(), reconcile=lambda p: 7
            )
            == 7
        )
        assert published == []
        release.set()
        assert drain(pool, rec, backend, reconcile, wake) == 1
        assert calls == [("inspect", "job-a"), ("logs", "job-a"), ("destroy", "job-a")]
        assert published == [main_thread]
        assert pool.ready_owners() == []
    finally:
        release.set()
        pool.close()


def test_pending_call_crosses_best_effort_error_handlers_without_false_completion():
    wake, release = threading.Event(), threading.Event()
    pool = DeferredBackendReconciliation(notify=wake.set)
    backend = SimpleNamespace(get_log_snapshot=lambda target: release.wait(5) and "finished")
    rec, observed = record(), []

    def reconcile(port):
        try:
            observed.append(port.get_log_snapshot("job-a"))
        except Exception:
            observed.append("false empty result")
        return 1

    try:
        assert pool.run(record=rec, backend=backend, reconcile=reconcile) == 0
        assert observed == []
        release.set()
        assert drain(pool, rec, backend, reconcile, wake) == 1
        assert observed == ["finished"]
    finally:
        release.set()
        pool.close()


def test_capacity_is_bounded_and_abandoned_result_can_be_discarded():
    wake, release = threading.Event(), threading.Event()
    pool = DeferredBackendReconciliation(notify=wake.set, max_workers=1, capacity=1)
    calls = []

    def inspect(target):
        calls.append(target)
        release.wait(5)
        return target

    backend = SimpleNamespace(inspect=inspect)
    try:
        pool.run(record=record(), backend=backend, reconcile=lambda p: p.inspect("job-a"))
        assert (
            pool.run(
                record=record("other", "job-b"),
                backend=backend,
                reconcile=lambda p: p.inspect("job-b"),
            )
            == 0
        )
        release.set()
        assert wake.wait(2)
        pool.discard(("effect-a", "job-a"))
        assert pool.ready_owners() == []
        assert calls == ["job-a"]
    finally:
        release.set()
        pool.close()


def test_rpc_errors_follow_existing_retry_semantics():
    wake = threading.Event()
    pool = DeferredBackendReconciliation(notify=wake.set)
    rec = record()
    calls = []

    def inspect(target):
        calls.append(target)
        raise OSError("worker unavailable")

    backend = SimpleNamespace(inspect=inspect)

    def reconcile(port):
        try:
            return port.inspect("job-a")
        except OSError:
            return 0

    try:
        assert pool.run(record=rec, backend=backend, reconcile=reconcile) == 0
        assert wake.wait(2)
        assert pool.run(record=rec, backend=backend, reconcile=reconcile) == 0
        assert pool.ready_owners() == []
        assert len(calls) == 1
    finally:
        pool.close()


def test_single_cycle_consumes_worker_result_before_draining(tmp_path):
    from test_orchestration_handoff_latency import _worker

    worker = _worker(tmp_path, chain_length=2)
    rec, published = record(), []
    backend = SimpleNamespace(inspect=lambda target: "stopped")

    def reconcile(port):
        assert port.inspect("job-a") == "stopped"
        published.append(True)
        return 1

    def recover(**kwargs):
        return worker.reconcile_container_backend(record=rec, backend=backend, reconcile=reconcile)

    worker._lifecycle_reconciliation_hooks = (SimpleNamespace(reconcile=recover),)
    worker._completion_dispatch_hooks = ()
    worker._recovery_schedule.next_due = 0
    worker.run_once_with_lock()
    assert published == [True]
    assert worker._deferred_backends is None
    assert worker._inline_container_reconciliation is False


@pytest.mark.parametrize("reason", ["model_capacity", "rate_limited"])
def test_suspended_cleanup_does_not_advance_capacity_retry_twice(tmp_path, monkeypatch, reason):
    from test_model_capacity_retry import Backend, waiting_record, worker_at
    from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider

    wake, release, entered = threading.Event(), threading.Event(), threading.Event()
    backend = Backend()
    backend.status.metadata["codex_subscription_deferral"]["reason"] = reason
    original_destroy = backend.destroy

    def destroy(target):
        entered.set()
        assert release.wait(5)
        original_destroy(target)

    backend.destroy = destroy
    monkeypatch.setattr(
        "evander.effects.providers.agent_prompt.build_execution_backend",
        lambda *args, **kwargs: backend,
    )
    worker = worker_at(tmp_path, [])
    worker.execution_store.put(waiting_record())
    pool = DeferredBackendReconciliation(notify=wake.set)
    worker.reconcile_container_backend = lambda **kwargs: pool.run(**kwargs)
    try:
        deadline = time.monotonic() + 3
        while not entered.is_set() and time.monotonic() < deadline:
            AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker)
            wake.wait(0.01)
            wake.clear()
        assert entered.is_set()
        assert worker.queued_agent_prompt_store.read_all() == {}
        release.set()
        while worker.execution_store.get_execution("effect-original").status == "waiting":
            assert time.monotonic() < deadline
            AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker)
            wake.wait(0.01)
            wake.clear()
        [queued] = worker.queued_agent_prompt_store.read_all().values()
        assert queued.capacity_deferral_count == 1
        assert backend.destroyed == ["worker-original"]
        if reason == "model_capacity":
            assert queued.metadata["model_capacity_retry_count"] == "1"
    finally:
        release.set()
        pool.close()


@pytest.mark.parametrize(
    "outcome", ["completed", "cancelled", "replaced", "deleted", "interrupted"]
)
def test_ready_result_rechecks_owner_and_wakes_without_another_poll_interval(tmp_path, outcome):
    from test_orchestration_handoff_latency import _publish, _worker

    worker = _worker(tmp_path, chain_length=2)
    rec = record()
    entered, release, wake = threading.Event(), threading.Event(), threading.Event()
    original_get = worker.execution_store.get_execution
    current = [rec]
    worker.execution_store.get_execution = lambda key: (
        current[0] if key == rec.execution_id else original_get(key)
    )
    worker._deferred_backends = DeferredBackendReconciliation(notify=wake.set)
    published = []

    def inspect(target):
        entered.set()
        assert release.wait(5)
        return "stopped"

    backend = SimpleNamespace(inspect=inspect)

    def reconcile(port):
        assert port.inspect("job-a") == "stopped"
        published.append(True)
        rec.status = "succeeded"
        return 1

    def recover(**kwargs):
        return worker.reconcile_container_backend(record=rec, backend=backend, reconcile=reconcile)

    worker._lifecycle_reconciliation_hooks = (SimpleNamespace(reconcile=recover),)
    worker._completion_dispatch_hooks = ()
    try:
        worker._recovery_schedule.next_due = 0
        _publish(worker)
        worker.run_once()
        assert entered.wait(1)
        # The ordinary workflow progresses while the worker RPC is blocked.
        for _ in range(10):
            worker.run_once()
            if len(worker.execution_store.list_executions()) == 2:
                break
        assert len(worker.execution_store.list_executions()) == 2
        assert published == []
        release.set()
        assert wake.wait(2)
        if outcome == "cancelled":
            rec.status = "failed"
        elif outcome == "replaced":
            rec.outputs["execution_id"] = "replacement-job"
        elif outcome == "deleted":
            current[0] = None
        elif outcome == "interrupted":
            rec.outputs["interrupt_in_progress"] = True
        worker._recovery_schedule.next_due = time.monotonic() + 1000
        worker.run_once()
        assert published == ([True] if outcome == "completed" else [])
        assert worker._deferred_backends.ready_owners() == []
    finally:
        release.set()
        worker.close()


@pytest.mark.parametrize("change", ["cancelled", "replaced", "deleted"])
def test_inspection_cannot_restore_cancelled_replaced_or_deleted_mapping(tmp_path, change):
    from evander.execution.container_worker_repository import (
        ContainerWorkerExecutionRecord,
        ContainerWorkerExecutionRepository,
    )

    repo = ContainerWorkerExecutionRepository(tmp_path / "executions.db")
    original = ContainerWorkerExecutionRecord(
        execution_id="exec-a",
        session_id="session-a",
        worker_job_id="job-a",
        worker_url="http://worker.test",
        target_name="target-a",
        last_status="running",
    )
    repo.put(original)
    expected = repo.get("exec-a")
    observed = expected.model_copy(deep=True)
    if change == "deleted":
        repo.delete("exec-a")
    else:
        updated = expected.model_copy(deep=True)
        if change == "cancelled":
            updated.last_status = "removed"
            updated.cleanup_status = "cleanup_succeeded"
        else:
            updated.worker_job_id = "replacement-job"
        repo.put(updated)
    result = repo.save_observation(observed, expected=expected)
    current = repo.get("exec-a")
    if change == "deleted":
        assert result is None and current is None
    elif change == "cancelled":
        assert result.cleanup_status == current.cleanup_status == "cleanup_succeeded"
        assert current.last_status == "removed"
    else:
        assert result.worker_job_id == current.worker_job_id == "replacement-job"


def test_observation_preserves_concurrent_metadata_and_usage_progress(tmp_path):
    from evander.execution.container_worker_repository import (
        ContainerWorkerExecutionRecord,
        ContainerWorkerExecutionRepository,
    )

    repo = ContainerWorkerExecutionRepository(tmp_path / "executions.db")
    repo.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a",
            session_id="session-a",
            worker_job_id="job-a",
            worker_url="http://worker.test",
            target_name="target-a",
            last_status="running",
            metadata={"model_usage_cursor": 1},
        )
    )
    expected = repo.get("exec-a")
    updated = expected.model_copy(deep=True)
    updated.metadata.update(operator_note="keep", model_usage_cursor=20, model_usage_complete=True)
    repo.put(updated)
    observed = expected.model_copy(deep=True)
    observed.last_status = "stopped"
    observed.metadata["native_thread_id"] = "thread-a"
    repo.save_observation(observed, expected=expected)
    current = repo.get("exec-a")
    assert current.last_status == "stopped"
    assert current.metadata["operator_note"] == "keep"
    assert current.metadata["native_thread_id"] == "thread-a"
    assert current.metadata["model_usage_cursor"] == 20
    assert current.metadata["model_usage_complete"] is True


@pytest.mark.parametrize("method", ["inspect", "stop", "interrupt_active_tool", "destroy"])
@pytest.mark.parametrize("change", ["cancelled", "replaced", "deleted"])
def test_rpc_completion_does_not_restore_concurrently_changed_job(tmp_path, method, change):
    from test_container_worker_execution_backend import _backend, FakeContainerWorkerClient
    from evander.execution.container_worker_repository import ContainerWorkerExecutionRecord

    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)
    repo = backend.repository
    repo.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a",
            session_id="session-1",
            worker_job_id="job-a",
            worker_url="http://worker.test",
            target_name="target-a",
            last_status="running",
        )
    )
    port = {
        "inspect": "get",
        "stop": "stop",
        "interrupt_active_tool": "interrupt_active_tool",
        "destroy": "delete",
    }[method]
    original = getattr(client, port)

    def changed_during_rpc(*args, **kwargs):
        if change == "deleted":
            repo.delete("exec-a")
        else:
            current = repo.get("exec-a")
            if change == "cancelled":
                current.last_status = "removed"
                current.cleanup_status = "cleanup_succeeded"
            else:
                current.worker_job_id = "replacement-job"
            repo.put(current)
        return original(*args, **kwargs)

    setattr(client, port, changed_during_rpc)
    bound = backend.for_reconciliation("exec-a")
    if change == "replaced":
        with pytest.raises(RuntimeError, match="replaced"):
            getattr(bound, method)("exec-a")
        # A later RPC must not target the replacement, even using the same ID.
        with pytest.raises(RuntimeError, match="replaced"):
            bound.destroy("exec-a")
    else:
        getattr(bound, method)("exec-a")
    current = repo.get("exec-a")
    if change == "deleted":
        assert current is None
    elif change == "replaced":
        assert current.worker_job_id == "replacement-job"
        assert "replacement-job" not in client.deleted
    else:
        assert current.last_status == "removed"
        assert current.cleanup_status == "cleanup_succeeded"
