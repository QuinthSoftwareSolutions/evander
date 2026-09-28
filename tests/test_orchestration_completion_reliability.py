from __future__ import annotations

import sqlite3
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.daemons.effect_orchestrator import EffectExecutionRecord
from evander.events.models import TriggerEvent
from evander.orchestration.completion_scheduling import CompletionBudget, CompletionRetry
from test_orchestration_handoff_latency import _worker


def _completed(worker, number=0):
    key = f"completed-{number}"
    now = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=number)
    worker.event_queue.publish(TriggerEvent(
        event_id=key, trigger_id="start", trigger_type="trigger:cron",
        trace_id=key, occurred_at=now, payload={},
    ))
    record = EffectExecutionRecord(
        execution_id=key, idempotency_key=key, event_id=key, trace_id=key,
        source_node_id="start", source_trigger_type="trigger:cron",
        effect_id="step-0", effect_type="effect:vm:create", status="running",
        started_at=now, finished_at=now, resolved_config_hash="hash",
    )
    worker.execution_store.record_execution(record)
    future = Future()
    future.set_result({"success": True, "value": number})
    worker._register_inflight_future(
        idempotency_key=key, future=future, config_revision="revision",
    )
    return record


def _allow_retry(worker):
    worker._completion_storage_retry_at = 0
    for retry in worker._completion_retries.values():
        retry.retry_at = 0


@pytest.mark.parametrize("scope", ["work_items", 42, ["work_items"], {"session_id": "session"}])
def test_completion_preserves_provider_scope_and_retries_saved_result(tmp_path, monkeypatch, scope):
    worker = _worker(tmp_path, chain_length=1)
    record = _completed(worker)
    result = {"scope": scope, "inserted": 3}
    future = Future()
    future.set_result(result)
    worker._inflight[record.idempotency_key].future = future
    publish = worker.execution_store.record_completion_and_publish

    def unavailable(**kwargs):
        raise RuntimeError("publication temporarily unavailable")

    monkeypatch.setattr(worker.execution_store, "record_completion_and_publish", unavailable)
    assert worker._reap_completed_futures() == (1, 0)
    pending = worker.execution_store.get_execution(record.execution_id)
    assert pending.status == "completion_pending"
    assert pending.outputs == result
    monkeypatch.setattr(worker.execution_store, "record_completion_and_publish", publish)
    _allow_retry(worker)
    assert worker._retry_pending_effect_completion_publications() == 1
    assert worker._retry_pending_effect_completion_publications() == 0
    saved = worker.execution_store.get_execution(record.execution_id)
    assert saved.status == "succeeded"
    assert saved.outputs == result
    successors = [event for event in worker.event_queue.read_all().values()
                  if event.trigger_id == "step-0"]
    assert len(successors) == 1
    assert successors[0].payload["scope"] == scope


@pytest.mark.parametrize("operation", ["lookup", "atomic_commit"])
def test_busy_completion_preserves_every_completed_future(tmp_path, monkeypatch, operation):
    worker = _worker(tmp_path, chain_length=1)
    records = [_completed(worker, number) for number in range(2)]
    name = (
        "find_execution_by_idempotency_key"
        if operation == "lookup" else "record_completion_and_publish"
    )
    original = getattr(worker.execution_store, name)
    calls = []

    def busy_once(*args, **kwargs):
        calls.append(None)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(*args, **kwargs)

    monkeypatch.setattr(worker.execution_store, name, busy_once)
    assert worker._reap_completed_futures() == (0, 0)
    assert set(worker._inflight) == {record.idempotency_key for record in records}
    # Repeated calls during the cooldown do not hammer the locked storage.
    worker._reap_completed_futures()
    assert len(calls) == 1
    _allow_retry(worker)
    assert worker._reap_completed_futures() == (2, 2)
    assert not worker._inflight
    queue = worker.event_queue.read_all()
    for record in records:
        saved = worker.execution_store.get_execution(record.execution_id)
        assert saved.status == "succeeded"
        assert saved.outputs["success"] is True
        assert sum(event.trigger_id == "step-0" and event.trace_id == record.trace_id
                   for event in queue.values()) == 1
        assert queue[record.event_id].delivery_state == "delivered"


def test_committed_completion_retries_bookkeeping_without_publication(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=1)
    record = _completed(worker)
    original = worker._update_event_delivery_state
    attempts = []

    def busy_once(event_id):
        attempts.append(event_id)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(event_id)

    monkeypatch.setattr(worker, "_update_event_delivery_state", busy_once)
    assert worker._reap_completed_futures() == (0, 0)
    saved = worker.execution_store.get_execution(record.execution_id)
    assert saved.status == "succeeded"
    assert saved.published_event_id in worker.event_queue.read_all()
    monkeypatch.setattr(worker.execution_store, "record_completion_and_publish", lambda **kw: (
        pytest.fail("committed completion was published again")
    ))
    _allow_retry(worker)
    worker._reap_completed_futures()
    assert not worker._inflight
    assert not worker._pending_completion_bookkeeping
    assert worker.event_queue.read_all()[record.event_id].delivery_state == "delivered"


@pytest.mark.parametrize("outcome, status, branch", [
    ("waiting", "waiting", "started"), ("error", "error_routed", "error"),
])
def test_started_and_error_branches_retain_results_until_atomic_commit(
    tmp_path, monkeypatch, outcome, status, branch,
):
    worker = _worker(tmp_path, chain_length=1)
    record = _completed(worker)
    future = Future()
    if outcome == "waiting":
        record.effect_type = "effect:agent:prompt"
        future.set_result({"dispatch_id": "synthetic-dispatch", "success": True})
        worker.execution_store.record_execution(record)
    else:
        future.set_exception(ValueError("synthetic provider failure"))
        monkeypatch.setattr(worker, "_build_execution_graph", lambda: SimpleNamespace(
            outbound_index={"step-0": (SimpleNamespace(source_port="error"),)},
        ))
    worker._inflight[record.idempotency_key].future = future
    original = worker.execution_store.record_completion_and_publish
    attempts = []

    def busy_once(**kwargs):
        attempts.append(kwargs["record"].status)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(**kwargs)

    monkeypatch.setattr(worker.execution_store, "record_completion_and_publish", busy_once)
    assert worker._reap_completed_futures() == (0, 0)
    assert record.idempotency_key in worker._inflight
    assert worker.execution_store.get_execution(record.execution_id).status == "running"
    _allow_retry(worker)
    assert worker._reap_completed_futures() == (1, 1)
    saved = worker.execution_store.get_execution(record.execution_id)
    assert saved.status == status
    assert saved.completion_pending_status is None
    successors = [event for event in worker.event_queue.read_all().values()
                  if event.trigger_id == "step-0"]
    assert len(successors) == 1
    assert successors[0].payload["__branch_port"] == branch


def test_one_completion_budget_is_shared_across_reaping_calls(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=1)
    for number in range(35):
        future = Future()
        future.set_result({})
        worker._register_inflight_future(
            idempotency_key=f"future-{number}", future=future, config_revision="revision",
        )
    observed = []
    monkeypatch.setattr(worker, "_finalize_completed_effect", lambda key, _entry: observed.append(key) or 1)
    worker._completion_budget = CompletionBudget(clock=lambda: 0.0)
    assert worker._reap_completed_futures() == (32, 32)
    assert worker._reap_completed_futures() == (0, 0)
    assert len(worker._inflight) == 3
    worker._completion_budget = CompletionBudget(clock=lambda: 0.0)
    assert worker._reap_completed_futures() == (3, 3)
    assert len(set(observed)) == 35


def test_one_blocked_completion_does_not_delay_other_ready_results(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=1)
    records = [_completed(worker, number) for number in range(2)]
    now = [100.0]
    monkeypatch.setattr("evander.orchestration.completion_scheduling.time.monotonic", lambda: now[0])
    monkeypatch.setattr("evander.orchestration.completion_scheduling.random.uniform", lambda *_: 1.0)
    blocked = records[0].idempotency_key
    worker._completion_retries[f"future:{blocked}"] = CompletionRetry(attempts=4)
    original = worker._finalize_completed_effect

    def finalize(key, entry):
        if key == blocked:
            raise sqlite3.OperationalError("database is locked")
        return original(key, entry)

    monkeypatch.setattr(worker, "_finalize_completed_effect", finalize)
    assert worker._reap_completed_futures() == (0, 0)
    assert worker._completion_retries[f"future:{blocked}"].retry_at == pytest.approx(100.25)
    # Another writer can release a short slot while the blocked item's own
    # exponential retry remains outstanding. The other result can use it.
    now[0] += 0.026
    assert worker._reap_completed_futures() == (1, 1)
    assert set(worker._inflight) == {blocked}
    saved = worker.execution_store.get_execution(records[1].execution_id)
    assert saved.status == "succeeded"


def test_pending_retry_pages_advance_oldest_first_without_loading_entire_backlog(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=1)
    records = [_completed(worker, number) for number in range(35)]
    worker._inflight.clear()
    for record in records:
        record.status = "completion_pending"
        record.completion_pending_status = "succeeded"
        record.outputs = {"success": True}
        worker.execution_store.record_execution(record)
    observed, pages = [], []
    original = worker.execution_store.list_completion_pending_records

    def page(**kwargs):
        pages.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(worker.execution_store, "list_completion_pending_records", page)
    monkeypatch.setattr(worker, "_publish_effect_completion_event_or_mark_pending", lambda **kw: (
        observed.append(kw["record"].execution_id) or "published"
    ))
    monkeypatch.setattr(worker, "_finalize_effect_completion_publication", lambda **kw: None)
    worker._completion_budget = CompletionBudget(clock=lambda: 0.0)
    assert worker._retry_pending_effect_completion_publications() == 32
    worker._completion_budget = CompletionBudget(clock=lambda: 0.0)
    assert worker._retry_pending_effect_completion_publications() == 3
    assert observed == [f"completed-{number}" for number in range(35)]
    assert [page["limit"] for page in pages] == [32, 32]
    assert pages[1]["after"] is not None


def test_kernel_retries_only_transient_storage_failures(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=1)
    record = _completed(worker)
    cycles, sleeps = [], []

    def cycle():
        cycles.append(None)
        if len(cycles) == 1:
            raise sqlite3.OperationalError("database is locked")
        worker._reap_completed_futures()
        raise KeyboardInterrupt

    monkeypatch.setattr(worker._kernel, "_run_cycle", cycle)
    monkeypatch.setattr(worker._kernel, "_on_start", None)
    monkeypatch.setattr(worker._kernel, "_sleeper", SimpleNamespace(sleep=sleeps.append))
    with pytest.raises(KeyboardInterrupt):
        worker._kernel._run_forever_locked()
    assert len(cycles) == 2
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 0.3
    assert worker.execution_store.get_execution(record.execution_id).status == "succeeded"
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        worker._on_cycle_storage_exception(sqlite3.OperationalError("no such table: missing"))


def test_run_once_defers_transient_storage_failure_without_dropping_results(tmp_path, monkeypatch):
    worker = _worker(tmp_path, chain_length=1)
    record = _completed(worker)
    calls = []

    def busy_cycle():
        calls.append(None)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(worker, "_run_once", busy_cycle)
    first = worker.run_once()
    assert 0 < first.next_sleep_seconds <= 0.3
    assert record.idempotency_key in worker._inflight
    second = worker.run_once()
    assert second.next_sleep_seconds > 0
    assert len(calls) == 1
    worker._cycle_storage_retry.retry_at = 0
    monkeypatch.setattr(worker, "_run_once", lambda: (_ for _ in ()).throw(
        sqlite3.OperationalError("no such table: missing")
    ))
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        worker.run_once()
