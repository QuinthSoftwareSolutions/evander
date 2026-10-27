from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from evander.admin import operations_helpers
from evander.admin.services import worker_control_service
from evander.admin.services.vm_agent.container_session_helpers import (
    VmAgentContainerSessionHelperMixin,
)
from evander.admin.services.vm_agent.container_session_lifecycle import (
    VmAgentContainerSessionLifecycleMixin,
)
from evander.admin.web import cache as cache_module
from evander.admin.web.cache import AdminWebJsonResponseCache
from evander.admin.web.chat import ChatStore
from evander.admin.web.workers_config import worker_config_routes
from evander.admin.services.session_service import SessionAdminService, SessionAdminDependencies
from evander.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from evander.models import SessionManifest


def cache_request(key: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("test", 80),
            "path": "/api/workers",
            "query_string": b"_dashboard=1",
            "headers": [(b"cookie", f"evander_admin_session={key}".encode())],
        }
    )


def test_dashboard_cache_evicts_expired_lru_and_oversized_bodies(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def run():
        cache = AdminWebJsonResponseCache(default_ttl_seconds=5, max_entries=2, max_bytes=100)

        async def factory():
            return JSONResponse({"value": "small"})

        for key in ("a", "b", "a", "c"):
            await cache.get_or_set_json(cache_request(key), "workers", factory)
        assert {key[3] for key in cache._entries} == {"a", "c"}
        now[0] = 6.0
        await cache.get_or_set_json(cache_request("d"), "workers", factory)
        assert {key[3] for key in cache._entries} == {"d"}

        async def large():
            return JSONResponse({"value": "x" * 100})

        response = await cache.get_or_set_json(cache_request("large"), "workers", large)
        assert json.loads(response.body)["value"] == "x" * 100
        assert cache._cached_bytes <= 100
        assert {key[3] for key in cache._entries} == {"d"}

    asyncio.run(run())


def test_dashboard_cache_reuses_encoded_response_and_etag():
    async def run():
        cache = AdminWebJsonResponseCache()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            return JSONResponse({"value": "å", "items": [1, 2]})

        first = await cache.get_or_set_json(cache_request("a"), "workers", factory)
        second = await cache.get_or_set_json(cache_request("a"), "workers", factory)
        assert first.body is second.body
        assert first.headers["etag"] == second.headers["etag"]
        assert second.headers["content-type"] == "application/json"
        assert calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("invalidation", ["clear", "refresh"])
def test_dashboard_cache_older_hash_cannot_overwrite_newer_refresh(monkeypatch, invalidation):
    async def run():
        cache = AdminWebJsonResponseCache()
        hashing = asyncio.Event()
        release = asyncio.Event()

        async def delayed_hash(function, body):
            if json.loads(body)["value"] == "old":
                hashing.set()
                await release.wait()
            return function(body)

        monkeypatch.setattr(cache_module, "run_in_threadpool", delayed_hash)

        async def old_factory():
            return JSONResponse({"value": "old"})

        async def new_factory():
            return JSONResponse({"value": "new"})

        old_request = asyncio.create_task(
            cache.get_or_set_json(cache_request("a"), "workers", old_factory)
        )
        await asyncio.wait_for(hashing.wait(), timeout=1)
        request = cache_request("a")
        if invalidation == "clear":
            cache.clear()
        else:
            request.scope["query_string"] += b"&_dashboard_refresh=1"
        await cache.get_or_set_json(request, "workers", new_factory)
        release.set()
        await old_request
        latest = await cache.get_or_set_json(cache_request("a"), "workers", new_factory)
        assert latest.headers["x-admin-web-cache"] == "hit"
        assert json.loads(latest.body) == {"value": "new"}
        assert not cache._refresh_tokens

    asyncio.run(run())


def test_chat_transcript_cache_observes_external_writes_and_does_not_leak_mutations(
    tmp_path, monkeypatch
):
    store = ChatStore(tmp_path / "chat.json")
    thread = store.get_or_create_thread("alice")
    store.append_message(user_id="alice", thread_id=thread, role="user", content="first")
    reads = 0
    original = store._read

    def read():
        nonlocal reads
        reads += 1
        return original()

    monkeypatch.setattr(store, "_read", read)
    first = store.get_transcript(user_id="alice", thread_id=thread)
    first["messages"][0]["content"] = "client mutation"
    assert (
        store.get_transcript(user_id="alice", thread_id=thread)["messages"][0]["content"] == "first"
    )
    assert reads == 1
    other = ChatStore(store.path)
    other.append_message(user_id="alice", thread_id=thread, role="assistant", content="second")
    assert len(store.get_transcript(user_id="alice", thread_id=thread)["messages"]) == 2
    assert reads == 2
    with pytest.raises(ValueError, match="Unknown chat thread"):
        store.get_transcript(user_id="bob", thread_id=thread)
    store.append_message(user_id="alice", thread_id=thread, role="user", content="third")
    assert len(store.get_transcript(user_id="alice", thread_id=thread)["messages"]) == 3


def test_chat_transcript_cache_is_bounded(tmp_path):
    store = ChatStore(tmp_path / "chat.json")
    thread = store.get_or_create_thread("alice")
    store.append_message(user_id="alice", thread_id=thread, role="user", content="hello")
    for limit in range(1, 80):
        store.get_transcript(user_id="alice", thread_id=thread, limit=limit)
    assert len(store._transcripts) == 64
    assert store._transcript_bytes <= 8 * 1024 * 1024


def test_worker_inventory_uses_one_batch_and_invalidates_after_actions(monkeypatch):
    calls = []

    def batch(units):
        calls.append(units)
        return {unit: {"ActiveState": "active", "MainPID": "42"} for unit in units}

    monkeypatch.setattr(worker_control_service, "_systemctl_show_many", batch)
    monkeypatch.setattr(
        worker_control_service,
        "_run_systemd_control",
        lambda *args: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    service = worker_control_service.WorkerControlAdminService()
    first = service.list_background_workers()
    first[0].active_state = "modified"
    assert service.list_background_workers()[0].active_state == "active"
    assert len(calls) == 1
    assert len(calls[0]) == len(first)
    assert set(calls[0]) == {worker.unit for worker in first}
    service.stop_background_worker("ado")
    service.list_background_workers()
    assert len(calls) == 2


def test_systemctl_batch_preserves_partial_missing_unit_results(monkeypatch):
    calls = []

    def run(args):
        calls.append(args)
        return SimpleNamespace(
            returncode=1,
            stderr="missing unit",
            stdout="Id=one.service\nActiveState=active\n\nId=two.service\nLoadState=not-found\nActiveState=inactive\n",
        )

    monkeypatch.setattr(operations_helpers, "_run_subprocess", run)
    result = operations_helpers._systemctl_show_many(["one.service", "two.service"])
    assert result["one.service"]["ActiveState"] == "active"
    assert result["two.service"]["LoadState"] == "not-found"
    assert len(calls) == 2
    assert calls[1] == ["sudo", "-n", *calls[0]]


@pytest.mark.parametrize("sudo_outcome", ["denied", "missing", "partial", "success"])
def test_worker_inventory_preserves_partial_results_across_sudo(monkeypatch, sudo_outcome):
    calls = []
    initial_stdout = (
        "Id=evander-ado-worker.service\nLoadState=loaded\n"
        "ActiveState=active\nSubState=running\nMainPID=42\n"
    )

    def run(args):
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, initial_stdout, "Another unit is missing")
        if sudo_outcome == "missing":
            raise FileNotFoundError("sudo is not installed")
        if sudo_outcome == "denied":
            return subprocess.CompletedProcess(args, 1, "", "sudo: a password is required")
        stdout = "Id=evander-proxy-issue-sync.service\nActiveState=active\nMainPID=24\n"
        if sudo_outcome == "success":
            stdout += "\n" + initial_stdout.replace("MainPID=42", "MainPID=84")
        return subprocess.CompletedProcess(args, int(sudo_outcome == "partial"), stdout, "")

    monkeypatch.setattr(operations_helpers, "_run_subprocess", run)
    service = worker_control_service.WorkerControlAdminService()
    workers = {worker.worker_id: worker for worker in service.list_background_workers()}
    assert workers["ado"].active_state == "active"
    assert workers["ado"].running is True
    assert workers["ado"].main_pid == (84 if sudo_outcome == "success" else 42)
    if sudo_outcome in {"partial", "success"}:
        assert workers["proxy-issue-sync"].running is True
        assert workers["proxy-issue-sync"].main_pid == 24
    else:
        assert workers["proxy-issue-sync"].active_state == "unavailable"
    assert workers["discord-ingest"].active_state == "unavailable"
    assert len(calls) == 2
    assert calls[1] == ["sudo", "-n", *calls[0]]


def test_systemctl_batch_does_not_retry_success(monkeypatch):
    calls = []

    def run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "Id=one.service\nActiveState=active\n", "")

    monkeypatch.setattr(operations_helpers, "_run_subprocess", run)
    assert operations_helpers._systemctl_show_many(["one.service"])["one.service"][
        "ActiveState"
    ] == "active"
    assert len(calls) == 1


def test_systemctl_batch_without_unit_data_reports_sudo_failure(monkeypatch):
    monkeypatch.setattr(
        operations_helpers,
        "_run_subprocess",
        lambda args: subprocess.CompletedProcess(
            args, 1, "", "sudo: a password is required" if args[0] == "sudo" else "Access denied"
        ),
    )
    with pytest.raises(RuntimeError, match="sudo: a password is required"):
        operations_helpers._systemctl_show_many(["one.service"])


def test_system_service_command_keeps_default_sudo_result(monkeypatch):
    attempts = [
        subprocess.CompletedProcess([], 1, "partial output", "Initial error"),
        subprocess.CompletedProcess([], 1, "", "sudo: a password is required"),
    ]
    final_result = attempts[-1]
    monkeypatch.setattr(operations_helpers, "_run_subprocess", lambda args: attempts.pop(0))
    assert operations_helpers._run_system_service_command(["systemctl", "show"]) is final_result
    assert not attempts


def test_worker_route_runs_inventory_off_the_event_loop():
    main_thread = threading.get_ident()

    def list_workers():
        assert threading.get_ident() != main_thread
        return []

    routes = worker_config_routes(
        SimpleNamespace(operations_admin=SimpleNamespace(list_background_workers=list_workers)),
        SimpleNamespace(),
    )
    endpoint = next(route.endpoint for route in routes if route.path == "/api/workers")
    response = asyncio.run(endpoint(cache_request("a")))
    assert json.loads(response.body) == {"workers": []}


def test_single_session_summary_uses_direct_repository_lookup():
    session = SessionManifest(
        session_id="s",
        vm_id="vm-123",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    repository = SimpleNamespace(
        get_session=lambda value: session if value == "s" else None,
        list_sessions=lambda: pytest.fail("single detail must not scan all sessions"),
    )
    service = SessionAdminService(
        SessionAdminDependencies(
            runtime=SimpleNamespace(session_repository=repository),
            config=SimpleNamespace(),
            directory_ownership=SimpleNamespace(),
        )
    )
    assert service.get_session_summary("s").session_id == "s"
    assert service.get_session_summary("s").is_expired is False
    with pytest.raises(ValueError, match="Unknown session"):
        service.get_session_summary("missing")


class LifecycleReader(VmAgentContainerSessionLifecycleMixin, VmAgentContainerSessionHelperMixin):
    def __init__(self, repository, now):
        self.repository = repository
        self.now = now

    def _container_worker_execution_repository(self):
        return self.repository

    def _is_container_session(self, session):
        return True

    def _current_utc_now(self):
        return self.now


def execution(index: int, now: datetime, **overrides):
    fields = dict(
        execution_id=f"e-{index:03}",
        session_id="s",
        worker_job_id=f"j-{index}",
        worker_url="http://worker",
        target_name="t",
        created_at=now + timedelta(minutes=index),
        last_status="exited",
        finished_at=now + timedelta(minutes=index + 1),
        metadata={"large_unused_payload": "x" * 10000},
    )
    fields.update(overrides)
    return ContainerWorkerExecutionRecord(**fields)


def test_batched_lifecycle_projection_preserves_history_without_hydrating_payloads(
    tmp_path, monkeypatch
):
    now = datetime(2026, 5, 21, tzinfo=UTC)
    repo = ContainerWorkerExecutionRepository(tmp_path / "executions.db")
    for index in range(30):
        metadata = {"large_unused_payload": "x" * 10000}
        if index == 2:
            metadata["container_lifecycle"] = {
                "cleanup_error": "old error",
                "cleanup_attempted_at": (now + timedelta(days=2)).isoformat(),
            }
        if index == 4:
            metadata["cleanup_status"] = "failed"
        if index == 6:
            metadata["container_lifecycle"] = {
                "cleanup_completed_at": (now + timedelta(days=1)).isoformat(),
                "cleanup_error": "new error",
            }
        repo.put(execution(index, now, metadata=metadata))
    reader = LifecycleReader(repo, now + timedelta(days=3))
    session = SessionManifest(session_id="s", allowed_tools=[], expires_at=now + timedelta(days=4))
    expected = reader._container_session_lifecycle(session, executions=repo.list_by_session("s"))
    monkeypatch.setattr(
        repo,
        "_record_from_row",
        lambda row: pytest.fail("must not hydrate historical execution payloads"),
    )
    rows = reader._container_lifecycle_records_by_session(["s", "empty"])
    assert len(rows["s"]) < 10
    assert rows["empty"] == []
    assert all("large_unused_payload" not in row.metadata for row in rows["s"])
    assert reader._container_session_lifecycle(session, executions=rows["s"]) == expected
    # A second repository instance simulates another process appending to the WAL.
    other = ContainerWorkerExecutionRepository(repo.path)
    other.put(execution(31, now, last_status="running"))
    updated = reader._container_lifecycle_records_by_session(["s"])
    assert (
        reader._container_session_lifecycle(session, executions=updated["s"]).state.value
        == "running"
    )
    other.delete("e-031")
    assert (
        reader._container_session_lifecycle(
            session, executions=reader._container_lifecycle_records_by_session(["s"])["s"]
        )
        == expected
    )


@pytest.mark.parametrize("status", ["running", "created", "failed", "unknown", "removed"])
@pytest.mark.parametrize("expired", [True, False])
@pytest.mark.parametrize("cleanup", ["none", "record_failure", "session_cleaned", "session_error"])
def test_reduced_lifecycle_history_retains_all_status_and_timestamp_semantics(
    tmp_path, status, expired, cleanup
):
    now = datetime(2026, 5, 21, tzinfo=UTC)
    reader = LifecycleReader(None, now + timedelta(days=3))
    records = [execution(index, now, last_status="exited") for index in range(40)]
    # Ordering uses started_at in preference to created_at, and the latest
    # heartbeat/completion can belong to a different execution than the latest.
    records[2] = execution(
        2,
        now,
        last_status=status,
        started_at=now + timedelta(days=1),
        updated_at=now + timedelta(days=2),
        finished_at=now + timedelta(days=2),
    )
    if cleanup != "none":
        records[3].metadata = {"cleanup_error": "older", "cleanup_attempted_at": "not-a-date"}
        records[8].metadata = {
            "container_lifecycle": {
                "cleanup_status": None,
                "state": " ",
                "cleanup_error": "latest",
                "cleanup_attempted_at": (now + timedelta(days=1)).isoformat(),
            },
            "cleanup_status": "failed",
        }
        records[11].metadata = {
            "state": "cleanup_failed",
            "cleanup_completed_at": (now + timedelta(days=2)).isoformat(),
        }
    session = SessionManifest(
        session_id="s",
        allowed_tools=[],
        expires_at=now + timedelta(days=2 if expired else 4),
        metadata=(
            {"container_lifecycle": {"cleanup_status": "succeeded"}}
            if cleanup == "session_cleaned"
            else {"cleanup_error": "session failure"}
            if cleanup == "session_error"
            else {}
        ),
    )
    full = reader._container_session_lifecycle(session, executions=records)
    compact = reader._lifecycle_relevant_records(records)
    assert len(compact) <= 9
    assert reader._container_session_lifecycle(session, executions=compact) == full


@pytest.mark.parametrize("value", [True, False, 1, 1.5, [True, False], {"flag": True}])
def test_lifecycle_sql_projection_preserves_metadata_json_types(tmp_path, value):
    now = datetime(2026, 5, 21, tzinfo=UTC)
    repo = ContainerWorkerExecutionRepository(tmp_path / "executions.db")
    repo.put(execution(1, now, metadata={"cleanup_error": value}))
    reader = LifecycleReader(repo, now)
    session = SessionManifest(session_id="s", allowed_tools=[], expires_at=now + timedelta(days=1))
    original = repo.list_by_session("s")
    projected = repo.list_lifecycle_records(["s"])["s"]
    assert type(projected[0].metadata["cleanup_error"]) is type(
        original[0].metadata["cleanup_error"]
    )
    assert reader._container_session_lifecycle(
        session, executions=projected
    ) == reader._container_session_lifecycle(session, executions=original)
