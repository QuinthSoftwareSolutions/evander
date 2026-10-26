"""A missing worker mapping must not strand the rest of a recovery page."""

import threading
import time
from types import SimpleNamespace

import pytest

from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.lifecycle_hooks import ProviderCompletionLifecycleHook
from evander.execution.container_worker_repository import ContainerWorkerExecutionRecord
from evander.orchestration.deferred_backend import DeferredBackendReconciliation
from evander.orchestration.maintenance import MaintenanceBudget

from test_container_worker_execution_backend import FakeContainerWorkerClient, _backend
from test_model_capacity_retry import waiting_record, worker_at


@pytest.mark.parametrize("provider", [AgentPromptEffectProvider, AgentEnqueuePromptEffectProvider])
@pytest.mark.parametrize("stale_count", [1, 51])
def test_missing_mapping_allows_later_completion_and_can_recover(
    tmp_path, monkeypatch, provider, stale_count
):
    published = []
    worker = worker_at(tmp_path, published)
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)
    monkeypatch.setattr(
        "evander.effects.providers.agent_prompt.build_execution_backend", lambda *a, **k: backend
    )
    wake = threading.Event()
    pool = DeferredBackendReconciliation(notify=wake.set)
    worker.reconcile_container_backend = lambda **kwargs: pool.run(**kwargs)
    hook = ProviderCompletionLifecycleHook(provider)

    def add_waiting(owner, target):
        rec = waiting_record()
        rec.execution_id = owner
        rec.idempotency_key = owner
        rec.event_id = "event-" + owner
        rec.effect_type = provider.node_type()
        rec.outputs.update(execution_id=target, dispatch_id="dispatch-" + owner)
        worker.execution_store.put(rec)

    def add_mapping(target):
        backend.repository.put(
            ContainerWorkerExecutionRecord(
                execution_id=target,
                session_id="session-1",
                worker_job_id=target,
                worker_url="http://worker.test",
                target_name=target,
                last_status="stopped",
            )
        )
        client.status_by_job[target] = "stopped"
        client.metadata_by_job[target] = {"codex_subscription_thread_id": "native-" + target}

    for index in range(stale_count):
        add_waiting(f"a-stale-{index:03d}", f"missing-{index:03d}")
    add_waiting("z-healthy", "healthy-job")
    add_mapping("healthy-job")

    def recover_until_complete(owner):
        deadline = time.monotonic() + 8
        while worker.execution_store.get_execution(owner).status == "waiting":
            assert time.monotonic() < deadline, "recovery made no progress past missing mappings"
            hook.on_waiting_reconciliation_slice(
                worker=worker,
                budget=MaintenanceBudget(clock=lambda: 0),
            )
            wake.wait(0.005)
            wake.clear()

    try:
        recover_until_complete("z-healthy")
        assert client.deleted == ["healthy-job"]
        assert [(port, out["worker_execution_id"]) for port, out in published] == [
            ("completed", "healthy-job"),
        ]
        assert worker.execution_store.get_execution("a-stale-000").status == "waiting"
        assert backend.repository.get("missing-000") is None

        # An absent mapping is retriable, never silently finalized or discarded.
        add_mapping("missing-000")
        recover_until_complete("a-stale-000")
        assert client.deleted == ["healthy-job", "missing-000"]
        assert [(port, out["worker_execution_id"]) for port, out in published] == [
            ("completed", "healthy-job"),
            ("completed", "missing-000"),
        ]
    finally:
        pool.close()


def test_binding_runs_in_background_once_and_later_rpcs_use_the_bound_backend():
    entered, release, wake = threading.Event(), threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    binds, operations = [], []

    def operation(name, result):
        def execute(target):
            operations.append((name, target, threading.get_ident()))
            return result

        return execute

    bound = SimpleNamespace(
        inspect=operation("inspect", "stopped"),
        get_log_snapshot=operation("logs", "finished"),
        config="bound-config",
    )

    def bind(target):
        binds.append((target, threading.get_ident()))
        entered.set()
        assert release.wait(5)
        return bound

    def unbound(*args):
        raise AssertionError("RPC bypassed the pinned backend")

    backend = SimpleNamespace(
        for_reconciliation=bind,
        inspect=unbound,
        get_log_snapshot=unbound,
        config="unbound-config",
    )
    rec = SimpleNamespace(execution_id="owner", outputs={"execution_id": "job-a"})
    pool = DeferredBackendReconciliation(notify=wake.set)

    def reconcile(port):
        assert port.inspect("job-a") == "stopped"
        assert port.config == "bound-config"
        assert port.get_log_snapshot("job-a") == "finished"
        return 1

    try:
        assert pool.run(record=rec, backend=backend, reconcile=reconcile) == 0
        assert entered.wait(1)
        other = SimpleNamespace(execution_id="other", outputs={"execution_id": "job-b"})
        assert pool.run(record=other, backend=SimpleNamespace(), reconcile=lambda p: 7) == 7
        release.set()
        deadline = time.monotonic() + 5
        while not pool.run(record=rec, backend=backend, reconcile=reconcile):
            assert time.monotonic() < deadline
            wake.wait(0.005)
            wake.clear()
        assert len(binds) == 1 and binds[0][0] == "job-a"
        assert binds[0][1] != main_thread
        assert [(name, target) for name, target, thread in operations] == [
            ("inspect", "job-a"),
            ("logs", "job-a"),
        ]
        assert all(thread != main_thread for name, target, thread in operations)
    finally:
        release.set()
        pool.close()
