from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from evander.admin.payloads import WorkflowEditorDocumentPayload
from evander.admin.services import snapshot_cache, workflow_reports_service
from evander.admin.services.workflow_reports_service import (
    WorkflowReportsAdminDependencies,
    WorkflowReportsAdminService,
)
from evander.admin.web.workflow import workflow_routes
from evander.daemons.effect_orchestrator import SqliteExecutionStore, SqliteGateExecutionStore
from evander.events.models import TriggerEvent
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.events import SqliteWorkflowEventJournal


NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def _stores(runtime_dir: Path) -> dict:
    return {"runtime_dir": runtime_dir, "orchestration_store_path": runtime_dir / "runs.db"}


def _bundle(stores):
    path = stores["orchestration_store_path"]
    return (
        SqliteTriggerEventQueue(path),
        SqliteExecutionStore(path),
        SqliteGateExecutionStore(path),
    )


def _publish(queue, event_id="event-alpha", node_id="trigger-alpha"):
    queue.publish(
        TriggerEvent(
            event_id=event_id,
            trigger_id=node_id,
            trigger_type="trigger:test",
            occurred_at=NOW,
        )
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(snapshot_cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: NOW)
    stores = _stores(tmp_path / "runtime")
    queue, _, _ = _bundle(stores)
    admin = SimpleNamespace(
        stores=stores,
        _workflow_run_store_bundle=_bundle,
    )
    admin._workflow_run_store_paths = lambda: admin.stores
    service = WorkflowReportsAdminService(
        WorkflowReportsAdminDependencies(
            project_dir=tmp_path,
            config=SimpleNamespace(),
            runtime=SimpleNamespace(),
            workflow_config_admin=admin,
            list_agents=lambda **kwargs: [],
        )
    )
    return SimpleNamespace(service=service, queue=queue, admin=admin, clock=clock)


def test_rest_and_stream_share_collection_and_apply_current_reader_permissions(
    runtime, monkeypatch
):
    _publish(runtime.queue)
    _publish(runtime.queue, "event-beta", "trigger-beta")
    collections = []
    collect = runtime.service._collect_workflow_run_snapshot

    def counted(stores):
        collections.append(True)
        return collect(stores)

    monkeypatch.setattr(runtime.service, "_collect_workflow_run_snapshot", counted)
    document = WorkflowEditorDocumentPayload(
        config_path="config.json",
        layout_path="layout.json",
        config_exists=True,
        layout_exists=False,
        config={},
        layout={},
        graphs=[
            {
                "id": name,
                "name": name,
                "directory_path": f"root/{name}",
                "node_ids": [f"trigger-{name}"],
            }
            for name in ("alpha", "beta")
        ],
    )
    service = SimpleNamespace(
        workflow_config_admin=SimpleNamespace(
            read_composed_workflow_editor_document=lambda: document,
            read_workflow_run_snapshot=runtime.service._read_workflow_run_snapshot,
            workflow_event_journal=lambda: SqliteWorkflowEventJournal(
                runtime.admin.stores["orchestration_store_path"]
            ),
        )
    )
    permissions = {"alice": {"root/alpha"}, "bob": {"root/beta"}}

    def can(request, permission, *, directory_path):
        return directory_path in permissions[request.headers["reader"]]

    auth = SimpleNamespace(can=can)
    routes = {route.path: route for route in workflow_routes(service, auth)}

    async def receive():
        await asyncio.Event().wait()

    def request(reader):
        return Request(
            {
                "type": "http",
                "method": "GET",
                "query_string": b"",
                "headers": [(b"reader", reader.encode())],
            },
            receive,
        )

    async def exercise():
        rest = routes["/api/workflow-editor/runs"]
        alice, bob = await asyncio.gather(
            rest.endpoint(request("alice")), rest.endpoint(request("bob"))
        )
        stream = await routes["/api/workflow-editor/runs/stream"].endpoint(request("bob"))
        try:
            first = await asyncio.wait_for(anext(stream.body_iterator), timeout=3)
        finally:
            await stream.body_iterator.aclose()
        for payload, name in (
            (json.loads(alice.body), "alpha"),
            (json.loads(bob.body), "beta"),
            (json.loads(first.split("data: ", 1)[1]), "beta"),
        ):
            assert [run["event_id"] for run in payload["runs"]] == [f"event-{name}"]
            assert payload["node_activity"] == {f"trigger-{name}": {"pending": 1}}
            assert {item["node_id"] for item in payload["recent_node_activity"]} == {
                f"trigger-{name}"
            }
        permissions["bob"].clear()
        assert (await rest.endpoint(request("bob"))).status_code == 403

    asyncio.run(exercise())
    assert collections == [True]


def test_new_event_refreshes_shared_snapshot_and_callers_cannot_mutate_it(runtime):
    _publish(runtime.queue)
    first = runtime.service._read_workflow_run_snapshot()
    first.runs[0].active_nodes.clear()
    first.node_activity.clear()
    first.recent_node_activity.clear()
    cached = runtime.service._read_workflow_run_snapshot()
    assert cached.generated_at == NOW
    assert cached.runs[0].active_nodes[0].status == "pending"
    assert cached.node_activity == {"trigger-alpha": {"pending": 1}}
    assert cached.recent_node_activity

    runtime.queue.mark_delivered("event-alpha", delivered_at=NOW)
    refreshed = runtime.service._read_workflow_run_snapshot()
    assert refreshed.runs == []
    assert refreshed.node_activity == {}
    assert refreshed.recent_node_activity[0].status == "delivered"


def test_quiet_snapshot_expires_for_file_backed_waits_and_recent_activity(runtime, monkeypatch):
    _publish(runtime.queue)
    first = runtime.service._read_workflow_run_snapshot()
    pending_path = runtime.admin.stores["runtime_dir"] / "await-agent-pending.json"
    pending_path.write_text(
        json.dumps({"wait": {"gate_id": "await-agent", "prompt_event_id": "waiting-event"}})
    )
    runtime.clock[0] = 0.9
    assert runtime.service._read_workflow_run_snapshot() == first
    runtime.clock[0] = 1.01
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: NOW + timedelta(seconds=10))
    expired = runtime.service._read_workflow_run_snapshot()
    assert expired.generated_at == NOW + timedelta(seconds=10)
    assert {run.event_id for run in expired.runs} == {"event-alpha", "waiting-event"}
    assert expired.recent_node_activity == []


def test_switching_runtime_does_not_reuse_another_database_snapshot(runtime, tmp_path):
    _publish(runtime.queue)
    assert runtime.service._read_workflow_run_snapshot().runs[0].event_id == "event-alpha"
    other = _stores(tmp_path / "other-runtime")
    queue, _, _ = _bundle(other)
    _publish(queue, "event-beta", "trigger-beta")
    runtime.admin.stores = other
    assert runtime.service._read_workflow_run_snapshot().runs[0].event_id == "event-beta"


@pytest.mark.parametrize("stagger_revisions", [False, True])
def test_waiting_readers_share_one_fresh_collection_across_revisions(
    runtime, monkeypatch, stagger_revisions
):
    _publish(runtime.queue)
    entered = threading.Event()
    release = threading.Event()
    names = ("beta", "gamma", "delta")
    waiting = {name: threading.Event() for name in names}
    resume = {name: threading.Event() for name in names}
    reader = threading.local()
    collections = []
    collect = runtime.service._collect_workflow_run_snapshot

    class TrackedFuture(Future):
        def result(self, timeout=None):
            name = reader.name
            waiting[name].set()
            result = super().result(timeout=timeout)
            # Wake the oldest revision first to expose redundant rebuilds;
            # relying on the scheduler's waiter order would hide the race.
            assert resume[name].wait(5)
            return result

    monkeypatch.setattr(snapshot_cache, "Future", TrackedFuture)

    def blocked(stores):
        result = collect(stores)
        collections.append(sorted(run.event_id for run in result.runs))
        if len(collections) == 1:
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(runtime.service, "_collect_workflow_run_snapshot", blocked)

    def read(name):
        reader.name = name
        return runtime.service._read_workflow_run_snapshot()

    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(runtime.service._read_workflow_run_snapshot)
        try:
            assert entered.wait(3)
            readers = {}
            expected = ["event-alpha", "event-beta"]
            for name in names:
                if name == "beta" or stagger_revisions:
                    _publish(runtime.queue, f"event-{name}", f"trigger-{name}")
                readers[name] = executor.submit(read, name)
                assert waiting[name].wait(3)
            if stagger_revisions:
                expected.extend(["event-gamma", "event-delta"])
            expected.sort()
            assert collections == [["event-alpha"]]
            release.set()
            assert first.result(timeout=3).runs[0].event_id == "event-alpha"
            for name in names:
                resume[name].set()
                result = readers[name].result(timeout=3)
                assert sorted(run.event_id for run in result.runs) == expected
        finally:
            release.set()
            for event in resume.values():
                event.set()
    assert collections == [["event-alpha"], expected]


@pytest.mark.parametrize("mode", ["existing", "new", "timeout"])
def test_workflow_journal_wait_keeps_sqlite_reads_off_event_loop(tmp_path, monkeypatch, mode):
    journal = SqliteWorkflowEventJournal(tmp_path / "runs.db")
    loop_thread = threading.get_ident()
    read = journal.read_after
    calls = []

    def read_outside_loop(cursor):
        assert threading.get_ident() != loop_thread
        calls.append(cursor)
        if mode == "new" and len(calls) == 2:
            journal.append("workflow.run.changed", source="test")
        return read(cursor)

    monkeypatch.setattr(journal, "read_after", read_outside_loop)
    if mode == "existing":
        journal.append("workflow.run.changed", source="test")
    events, cursor = asyncio.run(journal.wait_after(0, timeout_seconds=0.3))
    assert bool(events) == (mode != "timeout")
    assert (cursor > 0) == (mode != "timeout")
    if mode != "existing":
        assert len(calls) >= 2
