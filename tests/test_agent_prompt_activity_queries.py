from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest

from completion_recovery_support import (
    bounded_completion_queries,
    completion_record,
    seed_completed_executions,
)
from evander.daemons.effect_orchestrator import SqliteExecutionStore
from evander.effects.providers.lifecycle_hooks import has_active_enqueue_prompt_for_target
from evander.repositories.orchestration_db import connect_orchestration_db


def prompt(key, *, status="waiting", **changes):
    record = completion_record("effect", key, status=status)
    record.effect_type = "effect:agent:enqueue-prompt"
    record.outputs = {"session_id": "session"}
    return record.model_copy(update=changes)


def busy(store, **options):
    return has_active_enqueue_prompt_for_target(
        store, target="session", vm_name="vm", session_id="session", **options
    )


@pytest.mark.parametrize("history", [3000, 30000])
def test_session_busy_check_seeks_active_prompts_without_reading_history(tmp_path, monkeypatch, history):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    seed_completed_executions(store.path, kind="effect", count=history)
    store.put(prompt("wanted"))
    with connect_orchestration_db(store.path) as db:
        db.execute("ANALYZE")

    with bounded_completion_queries(monkeypatch) as statements:
        assert busy(store)
        assert not busy(store, owner_id="absent-owner")
    queries = [s for s in statements if "FROM effect_executions" in s]
    assert queries
    with sqlite3.connect(store.path) as db:
        for query in queries:
            plans = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + query)]
            assert any("SEARCH effect_executions USING INDEX idx_effect_executions_status_type" in p for p in plans)
            assert not any("SCAN " in p or "TEMP B-TREE" in p for p in plans)


@pytest.mark.parametrize("status,pending,dismissed,expected", [
    ("running", None, False, True),
    ("waiting", None, False, True),
    ("completion_pending", "running", False, True),
    ("completion_pending", "waiting", False, True),
    ("completion_pending", "succeeded", False, False),
    ("completion_pending", "failed", False, False),
    ("succeeded", None, False, False),
    ("failed", None, False, False),
    ("error_routed", None, False, False),
    ("waiting", None, True, False),
])
def test_busy_check_preserves_operational_and_pending_status_rules(tmp_path, status, pending, dismissed, expected):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    record = prompt("owner", status=status, completion_pending_status=pending)
    if dismissed:
        record.dismissed_at = record.started_at
    store.put(record)
    assert busy(store) is expected


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("field", ["outputs", "resolved_config", "input_payload"])
def test_busy_check_preserves_legacy_and_encoded_target_evidence(tmp_path, monkeypatch, inline, field):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    record = prompt("owner", outputs={}, resolved_config={}, input_payload={})
    getattr(record, field).update({"session_id": "session", "vm_name": "vm", "target": "session"})
    record.input_payload["prompt"] = "Unrelated prompt input. " * 10000
    record.resolved_config["options"] = {"unrelated": "setting " * 10000}
    store.put(record)
    if inline:
        with connect_orchestration_db(store.path) as db:
            db.execute("UPDATE effect_executions SET payload_json=? WHERE execution_id=?", (record.model_dump_json(), record.execution_id))

    def reject_full_record(*args, **kwargs):
        raise AssertionError("session activity hydrated a full execution payload")

    monkeypatch.setattr(store, "_record_from_row", reject_full_record)
    assert busy(store)
    assert not busy(store, current_idempotency_key="owner")


def test_busy_check_keeps_fifo_and_lock_ownership_rules(tmp_path):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    current = prompt("middle")
    older = prompt("older", started_at=current.started_at - timedelta(seconds=1))
    newer = prompt("newer", started_at=current.started_at + timedelta(seconds=1))
    for record in (newer, current, older):
        store.put(record)
    assert busy(store, current_idempotency_key="middle", owner_id="older")
    assert not busy(store, current_idempotency_key="middle", owner_id="newer")
    assert busy(store, owner_id="newer")
    assert not busy(store, owner_id="missing")


def test_activity_snapshot_does_not_wait_for_the_shared_execution_repository_lock(tmp_path):
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    store.put(prompt("owner"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        store._lock.acquire()
        try:
            result = pool.submit(busy, store)
            assert result.result(timeout=2)
        finally:
            store._lock.release()
