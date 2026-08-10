from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from evander.capacity_health import (
    DirectorySizeTelemetry,
    DiskUsageTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryStatus,
)
from evander.codex_auth import CodexAuthMode, CodexTurnSpec
from evander.managed_git_grants import managed_git_ssh_alias
from evander.worker import api as worker_api
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.service import (
    WorkerDaemon,
    WorkerDependencyCompositionConflict,
    _git_subprocess_env,
)
from evander.worker.models import (
    WorkerGitWriteKeyRequest,
    WorkerRepoCloneRequest,
    WorkerSessionPrepareRequest,
    WorkerTaskWorktreeCleanupRecord,
    WorkerTaskWorktreeCleanupRequest,
    WorkerTaskWorktreeRequest,
)
from evander.worker_daemon import (
    WorkerDiskTelemetry,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerJobTelemetry,
    WorkerJobStore,
    WorkerSessionPaths,
    WorkerSessionStorageTelemetry,
    WorkerTelemetryPayload,
    create_app,
)


_TEST_MANAGED_REPO_ID = "mr_0123456789abcdefabcd"
_TEST_MANAGED_GIT_ALIAS = managed_git_ssh_alias("evander-git-host", _TEST_MANAGED_REPO_ID)


class FakeContainerRuntime:
    def __init__(self) -> None:
        self.started: list[tuple[WorkerJobRequest, WorkerJobRecord, WorkerSessionPaths]] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []
        self.logs: dict[str, str] = {}
        self.inspect_results: dict[str, dict[str, Any]] = {}

    def start_job(
        self,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> str:
        self.started.append((request, record, paths))
        return f"container-{record.job_id}"

    def inspect_job(self, record: WorkerJobRecord) -> dict[str, Any]:
        return self.inspect_results.get(record.job_id, {"status": record.status})

    def stop_job(self, record: WorkerJobRecord) -> None:
        self.stopped.append(record.job_id)

    def remove_job(self, record: WorkerJobRecord) -> None:
        self.removed.append(record.job_id)

    def get_logs(self, record: WorkerJobRecord) -> str:
        return self.logs.get(record.job_id, f"log for {record.job_id}\n")


def _client(
    tmp_path: Path,
    runtime: FakeContainerRuntime | None = None,
    *,
    allowed_git_source_prefixes: list[str] | None = None,
    git_ssh_source_dir: Path | None = None,
    mssql_enabled: bool = False,
    mssql_admin_password: str | None = None,
    mssql_sqlcmd_path: str = "sqlcmd",
) -> TestClient:
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime or FakeContainerRuntime(),
        allowed_git_source_prefixes=allowed_git_source_prefixes,
        git_ssh_source_dir=git_ssh_source_dir,
        token="test-token",
        mssql_enabled=mssql_enabled,
        mssql_admin_password=mssql_admin_password,
        mssql_sqlcmd_path=mssql_sqlcmd_path,
    )
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _set_session_created_at(paths: WorkerSessionPaths, created_at: datetime) -> None:
    payload = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
    payload["created_at"] = created_at.isoformat()
    paths.metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    timestamp = created_at.timestamp()
    os.utime(paths.root, (timestamp, timestamp))
    os.utime(paths.metadata_path, (timestamp, timestamp))


def _available_session_storage(
    *,
    size_bytes: int = 1024,
    session_id: str = "session-1",
) -> WorkerSessionStorageTelemetry:
    sample = SessionStorageSample(
        session_id=session_id,
        directory_path=f"sessions/{session_id}",
        backend="container_worker_vm",
        size_bytes=size_bytes,
        created_at=datetime(2026, 5, 21, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
    )
    return WorkerSessionStorageTelemetry(
        total=DirectorySizeTelemetry(
            scope="worker_sessions",
            status=TelemetryStatus.AVAILABLE,
            health=HealthState.OK,
            size_bytes=size_bytes,
            file_count=1,
        ),
        session_count=1,
        largest_sessions=[sample],
        oldest_sessions=[sample],
    )


def _worker_telemetry_payload(
    session_storage: WorkerSessionStorageTelemetry | None = None,
) -> WorkerTelemetryPayload:
    return WorkerTelemetryPayload(
        disk=WorkerDiskTelemetry(
            worker_root=DiskUsageTelemetry(
                scope="worker_root",
                status=TelemetryStatus.AVAILABLE,
                health=HealthState.OK,
            )
        ),
        session_storage=session_storage or _available_session_storage(),
        jobs=WorkerJobTelemetry(),
    )


def test_auth_rejects_missing_or_wrong_bearer_token_and_accepts_configured_token(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/telemetry").status_code == 401
    assert client.post("/v1/jobs", json={}).status_code == 401
    assert (
        client.post("/v1/jobs", headers={"Authorization": "Bearer wrong"}, json={}).status_code
        == 401
    )

    response = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )

    assert response.status_code == 201


@pytest.mark.parametrize("session_id", [".", ".."])
def test_worker_session_paths_reject_dot_segments_before_creating_directories(
    tmp_path: Path,
    session_id: str,
) -> None:
    worker_root = tmp_path / "worker-root"
    raw_root = worker_root / "sessions" / session_id
    resolved_raw_root = raw_root.resolve(strict=False)

    with pytest.raises(ValueError, match="session_id"):
        WorkerSessionPaths.resolve(worker_root, session_id)

    assert resolved_raw_root in {
        worker_root.resolve(strict=False),
        (worker_root / "sessions").resolve(strict=False),
    }
    assert not (worker_root / "agent_home").exists()
    assert not (worker_root / "metadata.json").exists()
    assert not (worker_root / "sessions").exists()


def test_worker_api_rejects_dot_segment_session_and_job_ids_before_path_use(
    tmp_path: Path,
) -> None:
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)

    responses = [
        client.post("/v1/sessions", headers=_auth(), json={"session_id": ".."}),
        client.post(
            "/v1/jobs",
            headers=_auth(),
            json={
                "session_id": "..",
                "job_id": "job-1",
                "image": "codex:latest",
                "command": ["true"],
            },
        ),
        client.post(
            "/v1/jobs",
            headers=_auth(),
            json={
                "session_id": "session-1",
                "job_id": "..",
                "image": "codex:latest",
                "command": ["true"],
            },
        ),
        client.post(
            "/v1/sessions/%2E%2E/files",
            headers=_auth(),
            json={"files": [{"destination_path": "/home/agent/AGENTS.md", "content": "bad"}]},
        ),
        client.delete("/v1/sessions/%2E%2E", headers=_auth()),
    ]

    assert [response.status_code for response in responses] == [400, 400, 400, 400, 400]
    assert runtime.started == []
    worker_root = tmp_path / "worker-root"
    assert not (worker_root / "agent_home").exists()
    assert not (worker_root / "metadata.json").exists()


def test_worker_api_allows_safe_dotted_session_ids(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session.1"},
    )

    assert response.status_code == 201
    assert (tmp_path / "worker-root" / "sessions" / "session.1" / "metadata.json").is_file()


def test_worker_api_telemetry_refresh_query_requests_blocking_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    refresh_values: list[bool] = []

    def fake_read_telemetry(*, refresh: bool = False) -> WorkerTelemetryPayload:
        refresh_values.append(refresh)
        return _worker_telemetry_payload()

    monkeypatch.setattr(daemon, "read_telemetry", fake_read_telemetry)
    client = TestClient(daemon.create_app())

    assert client.get("/v1/telemetry?refresh=1", headers=_auth()).status_code == 200
    assert client.get("/v1/telemetry", headers=_auth()).status_code == 200
    assert refresh_values == [True, False]


def test_worker_daemon_read_telemetry_cold_returns_warming_and_starts_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    started = 0

    def fake_start_refresh() -> None:
        nonlocal started
        if daemon._session_storage_telemetry_refreshing:
            return
        started += 1
        daemon._session_storage_telemetry_refreshing = True

    monkeypatch.setattr(
        daemon,
        "_start_session_storage_telemetry_refresh_locked",
        fake_start_refresh,
    )

    telemetry = daemon.read_telemetry()
    repeated = daemon.read_telemetry()

    assert telemetry.session_storage.total.status is TelemetryStatus.UNSUPPORTED
    assert "warming" in telemetry.session_storage.total.errors[0].message
    assert repeated.session_storage.total.status is TelemetryStatus.UNSUPPORTED
    assert started == 1


def test_worker_daemon_read_telemetry_reuses_fresh_storage_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    calls = 0

    def fake_collect() -> WorkerSessionStorageTelemetry:
        nonlocal calls
        calls += 1
        return _available_session_storage(size_bytes=calls, session_id=f"session-{calls}")

    monkeypatch.setattr(daemon, "_collect_session_storage_telemetry", fake_collect)

    first = daemon.read_telemetry(refresh=True)
    second = daemon.read_telemetry()

    assert first.session_storage.total.size_bytes == 1
    assert second.session_storage.total.size_bytes == 1
    assert calls == 1


def test_worker_daemon_read_telemetry_returns_stale_cache_and_schedules_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        storage_telemetry_ttl_seconds=0,
    )
    monkeypatch.setattr(
        daemon,
        "_collect_session_storage_telemetry",
        lambda: _available_session_storage(size_bytes=99, session_id="session-stale"),
    )
    daemon.read_telemetry(refresh=True)
    started = 0

    def fake_start_refresh() -> None:
        nonlocal started
        if daemon._session_storage_telemetry_refreshing:
            return
        started += 1
        daemon._session_storage_telemetry_refreshing = True

    monkeypatch.setattr(
        daemon,
        "_start_session_storage_telemetry_refresh_locked",
        fake_start_refresh,
    )

    first = daemon.read_telemetry()
    second = daemon.read_telemetry()

    assert first.session_storage.total.size_bytes == 99
    assert second.session_storage.total.size_bytes == 99
    assert started == 1


def test_worker_daemon_read_telemetry_explicit_refresh_recomputes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    calls = 0

    def fake_collect() -> WorkerSessionStorageTelemetry:
        nonlocal calls
        calls += 1
        return _available_session_storage(size_bytes=calls, session_id=f"session-{calls}")

    monkeypatch.setattr(daemon, "_collect_session_storage_telemetry", fake_collect)

    first = daemon.read_telemetry(refresh=True)
    second = daemon.read_telemetry(refresh=True)

    assert first.session_storage.total.size_bytes == 1
    assert second.session_storage.total.size_bytes == 2
    assert calls == 2


def test_worker_daemon_ignores_older_background_refresh_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    calls = 0
    calls_lock = threading.Lock()
    first_started = threading.Event()
    finish_first = threading.Event()

    def fake_collect() -> WorkerSessionStorageTelemetry:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_started.set()
            finish_first.wait(timeout=2)
            return _available_session_storage(size_bytes=1, session_id="session-old")
        return _available_session_storage(size_bytes=2, session_id="session-new")

    monkeypatch.setattr(daemon, "_collect_session_storage_telemetry", fake_collect)

    cold = daemon.read_telemetry()
    assert cold.session_storage.total.status is TelemetryStatus.UNSUPPORTED
    assert first_started.wait(timeout=1)

    fresh = daemon.read_telemetry(refresh=True)
    assert fresh.session_storage.total.size_bytes == 2

    finish_first.set()
    deadline = time.monotonic() + 2
    while daemon._session_storage_telemetry_refreshing and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not daemon._session_storage_telemetry_refreshing
    assert daemon.read_telemetry().session_storage.total.size_bytes == 2
    assert calls == 2


def test_worker_daemon_read_telemetry_reports_disk_sessions_and_jobs_without_leaking_paths(
    tmp_path: Path,
) -> None:
    runtime = FakeContainerRuntime()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )

    old_paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-old")
    new_paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-new")
    _set_session_created_at(old_paths, datetime(2026, 5, 20, 9, 0, tzinfo=UTC))
    _set_session_created_at(new_paths, datetime(2026, 5, 21, 9, 0, tzinfo=UTC))
    (old_paths.artifacts / "small.txt").write_text("tiny\n", encoding="utf-8")
    (new_paths.artifacts / "nested").mkdir()
    (new_paths.artifacts / "nested" / "large.bin").write_bytes(b"x" * 1024)
    invalid_root = daemon.worker_root / "sessions" / "invalid session"
    (invalid_root / "session-shadow").mkdir(parents=True)
    (invalid_root / "session-shadow" / "larger.bin").write_bytes(b"x" * 4096)

    daemon.store.put(
        WorkerJobRecord(
            job_id="job-running",
            session_id="session-new",
            image="runner:latest",
            command=["true"],
            container_name="agent-worker-job-running",
            status="running",
            started_at=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
            agent_home_path=str(new_paths.agent_home),
            repo_root_path=str(new_paths.repo_root),
            artifacts_path=str(new_paths.artifacts),
            logs_path=str(new_paths.logs),
        )
    )
    daemon.store.put(
        WorkerJobRecord(
            job_id="job-failed",
            session_id="session-old",
            image="runner:latest",
            command=["true"],
            container_name="agent-worker-job-failed",
            status="failed",
            started_at=datetime(2026, 5, 21, 10, 5, tzinfo=UTC),
            finished_at=datetime(2026, 5, 21, 10, 6, tzinfo=UTC),
            agent_home_path=str(old_paths.agent_home),
            repo_root_path=str(old_paths.repo_root),
            artifacts_path=str(old_paths.artifacts),
            logs_path=str(old_paths.logs),
        )
    )
    daemon.store.put(
        WorkerJobRecord(
            job_id="job-stopped",
            session_id="session-new",
            image="runner:latest",
            command=["true"],
            container_name="agent-worker-job-stopped",
            status="stopped",
            started_at=datetime(2026, 5, 21, 10, 10, tzinfo=UTC),
            finished_at=datetime(2026, 5, 21, 10, 15, tzinfo=UTC),
            agent_home_path=str(new_paths.agent_home),
            repo_root_path=str(new_paths.repo_root),
            artifacts_path=str(new_paths.artifacts),
            logs_path=str(new_paths.logs),
        )
    )

    telemetry = daemon.read_telemetry(refresh=True)
    payload = json.dumps(telemetry.model_dump(mode="json"))

    assert telemetry.disk.worker_root.status is TelemetryStatus.AVAILABLE
    assert telemetry.session_storage.total.status is TelemetryStatus.AVAILABLE
    assert telemetry.session_storage.session_count == 2
    assert telemetry.session_storage.largest_sessions[0].session_id == "session-new"
    assert telemetry.session_storage.largest_sessions[0].directory_path == "sessions/session-new"
    assert telemetry.session_storage.largest_sessions[0].backend == "container_worker_vm"
    assert telemetry.session_storage.oldest_sessions[0].session_id == "session-old"
    assert telemetry.jobs.total_jobs == 3
    assert telemetry.jobs.counts_by_status == {"running": 1, "failed": 1, "stopped": 1}
    assert telemetry.jobs.running_job_count == 1
    assert telemetry.jobs.queued_job_count == 0
    assert telemetry.jobs.queued_job_status is TelemetryStatus.UNSUPPORTED
    assert telemetry.jobs.failed_start_count == 1
    assert telemetry.jobs.cleanup_failure_status is TelemetryStatus.UNSUPPORTED
    assert telemetry.jobs.cleanup_failure_count == 0
    assert str(tmp_path) not in payload


def test_worker_daemon_read_telemetry_reports_cleanup_failures_as_available(
    tmp_path: Path,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-cleanup-failed")
    daemon.store.put(
        WorkerJobRecord(
            job_id="job-cleanup-failed",
            session_id="session-cleanup-failed",
            image="runner:latest",
            command=["true"],
            container_name="agent-worker-job-cleanup-failed",
            status="cleanup_failed",
            started_at=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
            finished_at=datetime(2026, 5, 21, 10, 5, tzinfo=UTC),
            agent_home_path=str(paths.agent_home),
            repo_root_path=str(paths.repo_root),
            artifacts_path=str(paths.artifacts),
            logs_path=str(paths.logs),
            cleanup_status="cleanup_failed",
            cleanup_attempted_at=datetime(2026, 5, 21, 10, 4, tzinfo=UTC),
            cleanup_completed_at=datetime(2026, 5, 21, 10, 5, tzinfo=UTC),
            cleanup_error="session cleanup failed",
        )
    )

    telemetry = daemon.read_telemetry()

    assert telemetry.jobs.cleanup_failure_status is TelemetryStatus.AVAILABLE
    assert telemetry.jobs.cleanup_failure_count == 1
    assert telemetry.jobs.cleanup_failure_message == "1 cleanup failure recorded"
    assert telemetry.jobs.health is HealthState.WARNING
    assert telemetry.jobs.error_count == 1


@pytest.mark.parametrize(
    "repo_source",
    [
        f"ssh://evander-git-host/runtime/managed-git/{_TEST_MANAGED_REPO_ID}.git",
        f"ssh://{_TEST_MANAGED_GIT_ALIAS}/runtime/managed-git/{_TEST_MANAGED_REPO_ID}.git",
    ],
)
def test_worker_clone_repo_uses_host_only_git_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo_source: str,
) -> None:
    ssh_source = tmp_path / "git-ssh"
    ssh_source.mkdir()
    for name in ("evander-git", "config"):
        (ssh_source / name).write_text("ssh\n", encoding="utf-8")
    (ssh_source / "evander-git.pub").write_text("ssh pub\n", encoding="utf-8")
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=["ssh://evander-git-host/runtime/managed-git/"],
        git_ssh_source_dir=ssh_source,
    )
    calls: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(
            repo_source=repo_source,
            clone_branch="main",
            repo_name="repo",
        ),
    )

    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    assert not (paths.agent_home / ".ssh" / "evander-git").exists()
    host_ssh = paths.root / "host-ssh"
    assert (host_ssh / "evander-git").is_file()
    clone_call = next(call for call in calls if call["args"][:2] == ["git", "clone"])
    assert clone_call["env"]["HOME"] == str(paths.agent_home)  # type: ignore[index]
    assert clone_call["env"]["GIT_SSH_COMMAND"] == f"ssh -F {host_ssh / 'config'}"  # type: ignore[index]


def test_worker_reconciles_existing_checkout_remote_without_recloning(
    tmp_path: Path,
) -> None:
    first_source = tmp_path / "legacy-source"
    second_source = tmp_path / "managed-source"
    for source, content in ((first_source, "legacy\n"), (second_source, "managed\n")):
        source.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=source,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
        (source / "README.md").write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=source, check=True)

    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(first_source), repo_name="repo"),
    )
    checkout = WorkerSessionPaths.resolve(daemon.worker_root, "session-1").repo_root / "repo"
    (checkout / "unpublished.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a checkout for requested source"):
        daemon.clone_repo(
            "session-1",
            WorkerRepoCloneRequest(repo_source=str(second_source), repo_name="repo"),
        )

    reconciled = daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(
            repo_source=str(second_source),
            repo_name="repo",
            reconcile_existing_remote=True,
        ),
    )

    assert reconciled.cloned is False
    assert (checkout / "unpublished.txt").read_text(encoding="utf-8") == "keep me\n"
    assert subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == str(second_source)


def test_worker_prepares_and_reuses_task_scoped_worktree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    clone = daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    assert clone.commit_sha

    request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-a-1234",
        branch="agents/session-1/tasks/task-a-1234",
        base_ref="main",
    )
    created = daemon.prepare_task_worktree("session-1", request)
    reused = daemon.prepare_task_worktree("session-1", request)

    assert created.created is True
    assert reused.created is False
    assert created.commit_sha == clone.commit_sha
    assert reused.target_dir == "/home/agent/worktrees/repo/task-a-1234"


def test_worker_composes_exact_dependency_git_source_before_task_work(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "agents/predecessor/tasks/domain"],
        cwd=source,
        check=True,
    )
    (source / "domain.txt").write_text("accepted dependency\n", encoding="utf-8")
    subprocess.run(["git", "add", "domain.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "dependency"], cwd=source, check=True)
    dependency_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "main"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-ui-1234",
        branch="agents/session-1/tasks/task-ui-1234",
        base_ref="main",
        dependency_git_sources=[
            {
                "dependency_task_id": "task-domain",
                "deliverable_id": "deliverable-domain",
                "branch": "agents/predecessor/tasks/domain",
                "commit_sha": dependency_commit,
            }
        ],
        dependency_merge_author_name="Jonathan Quinth",
        dependency_merge_author_email="operator@example.invalid",
    )

    prepared = daemon.prepare_task_worktree("session-1", request)
    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / "task-ui-1234"
    )

    assert prepared.created is True
    assert prepared.base_commit_sha == prepared.commit_sha
    assert (target / "domain.txt").read_text(encoding="utf-8") == "accepted dependency\n"
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", dependency_commit, "HEAD"],
            cwd=target,
            check=False,
        ).returncode
        == 0
    )
    provenance = subprocess.run(
        [
            "git",
            "config",
            "--worktree",
            "--get",
            "evander.dependencyGitProvenance",
        ],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert json.loads(provenance) == [
        {
            "branch": "agents/predecessor/tasks/domain",
            "commit_sha": dependency_commit,
            "deliverable_id": "deliverable-domain",
            "dependency_task_id": "task-domain",
        }
    ]
    identity = subprocess.run(
        ["git", "show", "-s", "--format=%an%n%ae%n%cn%n%ce", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert prepared.commit_sha == dependency_commit
    assert identity == [
        "Worker Test",
        "test@example.invalid",
        "Worker Test",
        "test@example.invalid",
    ]

    (target / "ui.txt").write_text("successor work\n", encoding="utf-8")
    subprocess.run(["git", "add", "ui.txt"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "successor"], cwd=target, check=True)
    successor_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    reused = daemon.prepare_task_worktree("session-1", request)

    assert reused.created is False
    assert reused.base_commit_sha == prepared.base_commit_sha
    assert reused.commit_sha == successor_commit

    without_provenance = WorkerTaskWorktreeRequest(
        repo_name=request.repo_name,
        worktree_name=request.worktree_name,
        branch=request.branch,
        base_ref=request.base_ref,
    )
    with pytest.raises(
        ValueError,
        match="dependency Git provenance does not match",
    ):
        daemon.prepare_task_worktree("session-1", without_provenance)


def test_worker_composes_divergent_dependencies_into_one_meaningful_commit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dependency_commits: list[str] = []
    dependency_rows: list[dict[str, str]] = []
    for suffix in ("domain", "client"):
        branch = f"agents/predecessor/tasks/{suffix}"
        subprocess.run(["git", "checkout", "-q", "main"], cwd=source, check=True)
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=source, check=True)
        (source / f"{suffix}.txt").write_text(f"accepted {suffix}\n", encoding="utf-8")
        subprocess.run(["git", "add", f"{suffix}.txt"], cwd=source, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"{suffix} dependency"], cwd=source, check=True
        )
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dependency_commits.append(commit)
        dependency_rows.append(
            {
                "dependency_task_id": f"task-{suffix}",
                "deliverable_id": f"deliverable-{suffix}",
                "branch": branch,
                "commit_sha": commit,
            }
        )
    subprocess.run(["git", "checkout", "-q", "main"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-ui-compact",
        branch="agents/session-1/tasks/task-ui-compact",
        base_ref="main",
        dependency_git_sources=dependency_rows,
        dependency_merge_author_name="Jonathan Quinth",
        dependency_merge_author_email="operator@example.invalid",
        dependency_composition_commit_message=("Compose accepted dependencies for task task-ui"),
    )
    prepared = daemon.prepare_task_worktree("session-1", request)
    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / "task-ui-compact"
    )

    details = subprocess.run(
        ["git", "show", "-s", "--format=%s%n%P%n%an%n%ae%n%cn%n%ce", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert prepared.base_commit_sha == prepared.commit_sha
    assert details == [
        "Compose accepted dependencies for task task-ui",
        " ".join([base_commit, *dependency_commits]),
        "Jonathan Quinth",
        "operator@example.invalid",
        "Evander Team Dependency Composer",
        "team-dependencies@evander.invalid",
    ]
    assert (target / "domain.txt").read_text(encoding="utf-8") == "accepted domain\n"
    assert (target / "client.txt").read_text(encoding="utf-8") == "accepted client\n"
    parent_timestamps = [
        int(
            subprocess.run(
                ["git", "show", "-s", "--format=%ct", commit],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        for commit in [base_commit, *dependency_commits]
    ]
    composition_timestamps = subprocess.run(
        ["git", "show", "-s", "--format=%at %ct", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    expected_timestamp = max(parent_timestamps) + 1
    assert composition_timestamps == f"{expected_timestamp} {expected_timestamp}"

    daemon.clone_repo(
        "session-2",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    reproduced = daemon.prepare_task_worktree(
        "session-2",
        request.model_copy(
            update={
                "worktree_name": "same-composition",
                "branch": "agents/session-2/tasks/same-composition",
            }
        ),
    )
    assert reproduced.commit_sha == prepared.commit_sha


def test_worker_adopts_only_clean_retained_dependency_history_with_prior_base(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "agents/predecessor/tasks/domain"],
        cwd=source,
        check=True,
    )
    (source / "domain.txt").write_text("accepted dependency\n", encoding="utf-8")
    subprocess.run(["git", "add", "domain.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "dependency"], cwd=source, check=True)
    dependency_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "main"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    legacy_request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-rework-1234",
        branch="agents/session-1/tasks/task-rework-1234",
        base_ref="main",
    )
    prepared = daemon.prepare_task_worktree("session-1", legacy_request)
    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / "task-rework-1234"
    )
    subprocess.run(
        ["git", "merge", "--no-ff", "--no-edit", dependency_commit],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    (target / "task.txt").write_text("retained task work\n", encoding="utf-8")
    subprocess.run(["git", "add", "task.txt"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "task work"], cwd=target, check=True)
    retained_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "config", "--worktree", "--unset", "evander.baseCommit"],
        cwd=target,
        check=True,
    )
    dependency_sources = [
        {
            "dependency_task_id": "task-domain",
            "deliverable_id": "deliverable-domain",
            "branch": "agents/predecessor/tasks/domain",
            "commit_sha": dependency_commit,
        }
    ]
    recovery_request = legacy_request.model_copy(
        update={
            "expected_base_commit_sha": prepared.base_commit_sha,
            "dependency_git_sources": dependency_sources,
        }
    )

    recovered = daemon.prepare_task_worktree("session-1", recovery_request)

    assert recovered.created is False
    assert recovered.commit_sha == retained_head
    assert recovered.base_commit_sha == prepared.base_commit_sha
    assert subprocess.run(
        [
            "git",
            "config",
            "--worktree",
            "--get",
            "evander.dependencyGitProvenance",
        ],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == json.dumps(dependency_sources, sort_keys=True, separators=(",", ":"))
    assert (
        subprocess.run(
            ["git", "config", "--worktree", "--get", "evander.baseCommit"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == prepared.base_commit_sha
    )
    with pytest.raises(ValueError, match="does not match authoritative prior"):
        daemon.prepare_task_worktree(
            "session-1",
            recovery_request.model_copy(update={"expected_base_commit_sha": "f" * 40}),
        )
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == retained_head
    )

    subprocess.run(
        [
            "git",
            "config",
            "--worktree",
            "--unset",
            "evander.dependencyGitProvenance",
        ],
        cwd=target,
        check=True,
    )
    (target / "task.txt").write_text("uncommitted retained work\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        daemon.prepare_task_worktree("session-1", recovery_request)
    assert (target / "task.txt").read_text(encoding="utf-8") == ("uncommitted retained work\n")
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == retained_head
    )
    assert (
        subprocess.run(
            [
                "git",
                "config",
                "--worktree",
                "--get",
                "evander.dependencyGitProvenance",
            ],
            cwd=target,
            check=False,
        ).returncode
        != 0
    )


def test_worker_does_not_merge_missing_dependency_into_retained_task_work(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    legacy_request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-rework-1234",
        branch="agents/session-1/tasks/task-rework-1234",
        base_ref="main",
    )
    prepared = daemon.prepare_task_worktree("session-1", legacy_request)
    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / "task-rework-1234"
    )
    (target / "task.txt").write_text("retained task work\n", encoding="utf-8")
    subprocess.run(["git", "add", "task.txt"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "task work"], cwd=target, check=True)
    retained_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", "-q", "-b", "agents/predecessor/tasks/domain"],
        cwd=source,
        check=True,
    )
    (source / "domain.txt").write_text("late dependency\n", encoding="utf-8")
    subprocess.run(["git", "add", "domain.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "dependency"], cwd=source, check=True)
    dependency_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    recovery_request = legacy_request.model_copy(
        update={
            "expected_base_commit_sha": prepared.base_commit_sha,
            "dependency_git_sources": [
                {
                    "dependency_task_id": "task-domain",
                    "deliverable_id": "deliverable-domain",
                    "branch": "agents/predecessor/tasks/domain",
                    "commit_sha": dependency_commit,
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="does not already contain"):
        daemon.prepare_task_worktree("session-1", recovery_request)

    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == retained_head
    )
    assert not (target / "domain.txt").exists()
    assert (
        subprocess.run(
            [
                "git",
                "config",
                "--worktree",
                "--get",
                "evander.dependencyGitProvenance",
            ],
            cwd=target,
            check=False,
        ).returncode
        != 0
    )


def test_worker_rolls_back_conflicting_dependency_git_composition(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "shared.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "shared.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    base_tree_hash = subprocess.run(
        ["git", "rev-parse", f"{base_commit}^{{tree}}"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dependency_commits: list[str] = []
    for branch, content in (
        ("agents/predecessor/tasks/first", "first\n"),
        ("agents/predecessor/tasks/second", "second\n"),
    ):
        subprocess.run(["git", "checkout", "-q", "-B", branch, "main"], cwd=source, check=True)
        (source / "shared.txt").write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "shared.txt"], cwd=source, check=True)
        subprocess.run(["git", "commit", "-q", "-m", branch], cwd=source, check=True)
        dependency_commits.append(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    subprocess.run(["git", "checkout", "-q", "main"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-conflict-1234",
        branch="agents/session-1/tasks/task-conflict-1234",
        base_ref="main",
        dependency_git_sources=[
            {
                "dependency_task_id": "task-first",
                "deliverable_id": "deliverable-first",
                "branch": "agents/predecessor/tasks/first",
                "commit_sha": dependency_commits[0],
            },
            {
                "dependency_task_id": "task-second",
                "deliverable_id": "deliverable-second",
                "branch": "agents/predecessor/tasks/second",
                "commit_sha": dependency_commits[1],
            },
        ],
    )

    with pytest.raises(
        WorkerDependencyCompositionConflict,
        match="task-second deliverable deliverable-second",
    ) as raised:
        daemon.prepare_task_worktree("session-1", request)

    assert raised.value.details == {
        "base_branch": "main",
        "base_commit_sha": base_commit,
        "base_tree_hash": base_tree_hash,
        "dependency_git_sources": [
            source.model_dump(mode="json") for source in request.dependency_git_sources
        ],
        "failed_dependency_task_id": "task-second",
        "failed_deliverable_id": "deliverable-second",
        "conflict_paths": ["shared.txt"],
    }

    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / "task-conflict-1234"
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == base_commit
    )
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    assert (
        subprocess.run(
            [
                "git",
                "config",
                "--worktree",
                "--get",
                "evander.dependencyGitProvenance",
            ],
            cwd=target,
            check=False,
        ).returncode
        != 0
    )

    manual = daemon.prepare_task_worktree(
        "session-1",
        request.model_copy(
            update={
                "expected_base_commit_sha": base_commit,
                "expected_base_tree_hash": base_tree_hash,
                "dependency_composition_mode": "manual_recovery",
                "dependency_composition_recovery_id": "depcomp-test",
            }
        ),
    )
    assert manual.commit_sha == base_commit
    manual_payload = json.loads(
        subprocess.run(
            [
                "git",
                "config",
                "--worktree",
                "--get",
                "evander.manualDependencyGitSources",
            ],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert manual_payload["recovery_id"] == "depcomp-test"
    assert manual_payload["dependency_git_sources"] == [
        source.model_dump(mode="json") for source in request.dependency_git_sources
    ]

    resolved_branch = "agents/resolver/tasks/dependency-recovery"
    subprocess.run(
        ["git", "checkout", "-q", "-B", resolved_branch, dependency_commits[0]],
        cwd=source,
        check=True,
    )
    merge = subprocess.run(
        ["git", "merge", "--no-ff", "--no-edit", dependency_commits[1]],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0
    (source / "shared.txt").write_text("resolved\n", encoding="utf-8")
    subprocess.run(["git", "add", "shared.txt"], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "Resolve dependency composition"],
        cwd=source,
        check=True,
    )
    resolved_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    adopted = daemon.prepare_task_worktree(
        "session-1",
        request.model_copy(
            update={
                "worktree_name": "task-adopt-recovery",
                "branch": "agents/session-1/tasks/task-adopt-recovery",
                "expected_base_commit_sha": base_commit,
                "expected_base_tree_hash": base_tree_hash,
                "dependency_composition_mode": "adopt_resolved",
                "dependency_composition_recovery_id": "depcomp-test",
                "resolved_dependency_branch": resolved_branch,
                "resolved_dependency_commit_sha": resolved_commit,
            }
        ),
    )
    assert adopted.commit_sha == resolved_commit
    assert adopted.base_commit_sha == resolved_commit
    adopted_target = target.parent / "task-adopt-recovery"
    assert (adopted_target / "shared.txt").read_text(encoding="utf-8") == "resolved\n"
    assert (
        subprocess.run(
            [
                "git",
                "config",
                "--worktree",
                "--get",
                "evander.dependencyCompositionRecovery",
            ],
            cwd=adopted_target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "depcomp-test"
    )


def test_worker_task_worktree_metadata_survives_host_container_host_reuse(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    tracked = source / "README.md"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-portable-1234",
        branch="agents/session-1/tasks/task-portable-1234",
        base_ref="main",
    )

    created = daemon.prepare_task_worktree("session-1", request)
    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    target = paths.agent_home / "worktrees" / "repo" / request.worktree_name
    admin_dir = paths.repo_root / "repo" / ".git" / "worktrees" / request.worktree_name
    assert not Path(
        (target / ".git").read_text(encoding="utf-8").split(": ", 1)[1].strip()
    ).is_absolute()
    assert Path((admin_dir / "gitdir").read_text(encoding="utf-8").strip()) == target / ".git"

    container_home = tmp_path / "container-mount" / "home" / "agent"
    shutil.copytree(paths.agent_home, container_home)
    container_target = container_home / "worktrees" / "repo" / request.worktree_name
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=container_target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == request.branch
    )

    (target / "README.md").write_text("uncommitted work\n", encoding="utf-8")
    (target / "untracked.txt").write_text("retain me\n", encoding="utf-8")
    (target / ".git").write_text(
        f"gitdir: /home/agent/repos/repo/.git/worktrees/{request.worktree_name}\n",
        encoding="utf-8",
    )
    (admin_dir / "gitdir").write_text(
        f"/home/agent/worktrees/repo/{request.worktree_name}/.git\n",
        encoding="utf-8",
    )

    reused = daemon.prepare_task_worktree("session-1", request)

    assert reused.created is False
    assert reused.commit_sha == created.commit_sha
    assert (target / "README.md").read_text(encoding="utf-8") == "uncommitted work\n"
    assert (target / "untracked.txt").read_text(encoding="utf-8") == "retain me\n"
    assert subprocess.run(
        ["git", "status", "--short"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines() == [" M README.md", "?? untracked.txt"]
    assert not Path(
        (target / ".git").read_text(encoding="utf-8").split(": ", 1)[1].strip()
    ).is_absolute()
    assert Path((admin_dir / "gitdir").read_text(encoding="utf-8").strip()) == target / ".git"


def test_worker_refuses_orphaned_task_worktree_without_overwriting_retained_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-orphaned-1234",
        branch="agents/session-1/tasks/task-orphaned-1234",
        base_ref="main",
    )
    daemon.prepare_task_worktree("session-1", request)
    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    target = paths.agent_home / "worktrees" / "repo" / request.worktree_name
    admin_dir = paths.repo_root / "repo" / ".git" / "worktrees" / request.worktree_name
    (target / "retained.txt").write_text("do not overwrite\n", encoding="utf-8")
    admin_dir.rename(admin_dir.with_name(f"{admin_dir.name}-missing"))

    with pytest.raises(
        ValueError,
        match="metadata is missing or invalid; retained checkout content",
    ):
        daemon.prepare_task_worktree("session-1", request)

    assert (target / "retained.txt").read_text(encoding="utf-8") == "do not overwrite\n"
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{request.branch}"],
            cwd=paths.repo_root / "repo",
            check=False,
        ).returncode
        == 0
    )


def test_worker_pins_new_task_worktree_to_fresh_remote_base_without_moving_existing_work(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=source, check=True)
    initial_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    checkout = WorkerSessionPaths.resolve(daemon.worker_root, "session-1").repo_root / "repo"

    (source / "README.md").write_text("fresh remote\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fresh remote"], cwd=source, check=True)
    fresh_remote_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert (
        subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == initial_commit
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == initial_commit
    )

    request = WorkerTaskWorktreeRequest(
        repo_name="repo",
        worktree_name="task-fresh-1234",
        branch="agents/session-1/tasks/task-fresh-1234",
        base_ref="main",
    )
    prepared = daemon.prepare_task_worktree("session-1", request)
    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / "task-fresh-1234"
    )

    assert prepared.created is True
    assert prepared.commit_sha == fresh_remote_commit
    assert prepared.base_commit_sha == fresh_remote_commit
    assert (
        subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == initial_commit
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == fresh_remote_commit
    )
    assert (
        subprocess.run(
            ["git", "config", "--worktree", "--get", "evander.baseBranch"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "main"
    )
    assert (
        subprocess.run(
            ["git", "config", "--worktree", "--get", "evander.baseCommit"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == fresh_remote_commit
    )

    (target / "task.txt").write_text("task work\n", encoding="utf-8")
    subprocess.run(["git", "add", "task.txt"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "task work"], cwd=target, check=True)
    task_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (source / "later.txt").write_text("later remote work\n", encoding="utf-8")
    subprocess.run(["git", "add", "later.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "later remote"], cwd=source, check=True)

    reused = daemon.prepare_task_worktree("session-1", request)

    assert reused.created is False
    assert reused.commit_sha == task_commit
    assert reused.base_commit_sha == fresh_remote_commit
    assert (
        subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == fresh_remote_commit
    )

    pinned = daemon.prepare_task_worktree(
        "session-1",
        WorkerTaskWorktreeRequest(
            repo_name="repo",
            worktree_name="task-pinned-1234",
            branch="agents/session-1/tasks/task-pinned-1234",
            base_ref=initial_commit,
        ),
    )

    assert pinned.commit_sha == initial_commit
    assert pinned.base_commit_sha == initial_commit
    assert (
        subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == fresh_remote_commit
    )

    subprocess.run(
        ["git", "checkout", "--detach", initial_commit],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    detached = daemon.prepare_task_worktree(
        "session-1",
        WorkerTaskWorktreeRequest(
            repo_name="repo",
            worktree_name="task-detached-1234",
            branch="agents/session-1/tasks/task-detached-1234",
            base_ref="HEAD",
        ),
    )

    assert detached.commit_sha == initial_commit
    assert detached.base_commit_sha == initial_commit


def test_worker_fetches_new_release_branch_missing_from_persistent_clone(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    checkout = WorkerSessionPaths.resolve(daemon.worker_root, "session-1").repo_root / "repo"
    release_branch = "team/team/releases/release-new"
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", f"{release_branch}^{{commit}}"],
            cwd=checkout,
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        != 0
    )

    subprocess.run(["git", "checkout", "-q", "-b", release_branch], cwd=source, check=True)
    (source / "release.txt").write_text("rejected candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "release.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "release candidate"], cwd=source, check=True)
    release_commit = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    release_tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    prepared = daemon.prepare_task_worktree(
        "session-1",
        WorkerTaskWorktreeRequest(
            repo_name="repo",
            worktree_name="correction-1234",
            branch="agents/session-1/tasks/correction-1234",
            base_ref=release_branch,
            expected_base_commit_sha=release_commit,
            expected_base_tree_hash=release_tree,
        ),
    )

    assert prepared.created is True
    assert prepared.commit_sha == release_commit
    assert prepared.base_commit_sha == release_commit
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=(
                tmp_path
                / "worker-root"
                / "sessions"
                / "session-1"
                / "agent_home"
                / "worktrees"
                / "repo"
                / "correction-1234"
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == release_tree
    )


def test_worker_rejects_release_branch_that_does_not_match_pinned_candidate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    expected_tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    release_branch = "team/team/releases/release-moved"
    subprocess.run(["git", "checkout", "-q", "-b", release_branch], cwd=source, check=True)
    (source / "moved.txt").write_text("unexpected candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "moved.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "moved candidate"], cwd=source, check=True)
    moved_commit = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(ValueError, match="expected authoritative Team base commit"):
        daemon.prepare_task_worktree(
            "session-1",
            WorkerTaskWorktreeRequest(
                repo_name="repo",
                worktree_name="correction-moved-1234",
                branch="agents/session-1/tasks/correction-moved-1234",
                base_ref=release_branch,
                expected_base_commit_sha=expected_commit,
                expected_base_tree_hash=expected_tree,
            ),
        )

    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    assert not (paths.agent_home / "worktrees" / "repo" / "correction-moved-1234").exists()
    assert (
        subprocess.run(
            [
                "git",
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/agents/session-1/tasks/correction-moved-1234",
            ],
            cwd=paths.repo_root / "repo",
            check=False,
        ).returncode
        != 0
    )

    with pytest.raises(ValueError, match="expected authoritative Team base tree"):
        daemon.prepare_task_worktree(
            "session-1",
            WorkerTaskWorktreeRequest(
                repo_name="repo",
                worktree_name="correction-wrong-tree-1234",
                branch="agents/session-1/tasks/correction-wrong-tree-1234",
                base_ref=release_branch,
                expected_base_commit_sha=moved_commit,
                expected_base_tree_hash=expected_tree,
            ),
        )
    assert not (paths.agent_home / "worktrees" / "repo" / "correction-wrong-tree-1234").exists()


def test_worker_cleanup_removes_safe_task_worktree_and_inventory_ignored_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / ".gitignore").write_text("cache/\n", encoding="utf-8")
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    clone = daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    worktree_name = "task-a-1234"
    branch = "agents/session-1/tasks/task-a-1234"
    prepared = daemon.prepare_task_worktree(
        "session-1",
        WorkerTaskWorktreeRequest(
            repo_name="repo",
            worktree_name=worktree_name,
            branch=branch,
            base_ref="main",
        ),
    )
    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / worktree_name
    )
    cache = target / "cache"
    cache.mkdir()
    generated = cache / "generated.bin"
    generated.write_bytes(b"generated")
    request = WorkerTaskWorktreeCleanupRequest(
        repo_name="repo",
        worktree_name=worktree_name,
        branch=branch,
        expected_commit_sha=prepared.commit_sha,
        expected_target_dir=prepared.target_dir,
    )

    inventory = daemon.cleanup_task_worktree(
        "session-1",
        request.model_copy(update={"dry_run": True}),
    )
    assert inventory.state == "dry_run"
    assert inventory.safe_to_remove is True
    assert inventory.removed is False
    assert inventory.pruned is False
    assert inventory.validation_count == 1
    assert target.exists()

    removed = daemon.cleanup_task_worktree("session-1", request)
    repeated = daemon.cleanup_task_worktree("session-1", request)

    assert removed.state == "removed"
    assert removed.safe_to_remove is True
    assert removed.removed is True
    assert removed.pruned is True
    assert removed.validation_count == 2
    assert removed.ignored_paths == ["cache/generated.bin"]
    assert removed.ignored_bytes == len(b"generated")
    assert removed.worktree_bytes >= removed.ignored_bytes
    assert removed.reclaimed_bytes == removed.worktree_bytes
    assert not target.exists()
    assert repeated.state == "already_absent"
    assert repeated.removed is False
    assert repeated.pruned is True
    assert repeated.validation_count == 2
    assert repeated.reclaimed_bytes == 0
    assert clone.commit_sha == prepared.commit_sha


def test_worker_cleanup_record_allows_final_non_force_git_validation() -> None:
    record = WorkerTaskWorktreeCleanupRecord(
        session_id="session-1",
        repo_name="repo",
        worktree_name="task-a-1234",
        target_dir="/home/agent/worktrees/repo/task-a-1234",
        branch="agents/session-1/tasks/task-a-1234",
        expected_commit_sha="a" * 40,
        actual_branch="agents/session-1/tasks/task-a-1234",
        actual_commit_sha="a" * 40,
        state="refused",
        safe_to_remove=False,
        removed=False,
        pruned=False,
        dry_run=False,
        validation_count=3,
        failed_check="tracked_changes",
        message="late tracked work was retained",
        tracked_paths=["README.md"],
    )

    assert record.validation_count == 3
    assert record.tracked_paths == ["README.md"]


def test_worker_cleanup_endpoint_offloads_blocking_git_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    branch = "agents/session-1/tasks/task-a-1234"
    expected_sha = "a" * 40
    expected_record = WorkerTaskWorktreeCleanupRecord(
        session_id="session-1",
        repo_name="repo",
        worktree_name="task-a-1234",
        target_dir="/home/agent/worktrees/repo/task-a-1234",
        branch=branch,
        expected_commit_sha=expected_sha,
        state="already_absent",
        safe_to_remove=True,
        removed=False,
        pruned=True,
        dry_run=False,
        validation_count=2,
        message="task worktree was already absent; registrations were pruned",
    )
    offloaded_calls: list[tuple[object, tuple[object, ...]]] = []

    async def _record_threadpool_call(function, *args):
        offloaded_calls.append((function, args))
        return function(*args)

    def _cleanup(
        session_id: str,
        request: WorkerTaskWorktreeCleanupRequest,
    ) -> WorkerTaskWorktreeCleanupRecord:
        assert session_id == "session-1"
        assert request.worktree_name == "task-a-1234"
        return expected_record

    monkeypatch.setattr(worker_api, "run_in_threadpool", _record_threadpool_call)
    monkeypatch.setattr(daemon, "cleanup_task_worktree", _cleanup)

    with TestClient(daemon.create_app()) as client:
        response = client.post(
            "/v1/sessions/session-1/worktrees/cleanup",
            headers=_auth(),
            json={
                "repo_name": "repo",
                "worktree_name": "task-a-1234",
                "branch": branch,
                "expected_commit_sha": expected_sha,
                "expected_target_dir": expected_record.target_dir,
            },
        )

    assert response.status_code == 200
    assert response.json()["state"] == "already_absent"
    assert len(offloaded_calls) == 1
    assert offloaded_calls[0][1][0] == "session-1"
    assert isinstance(offloaded_calls[0][1][1], WorkerTaskWorktreeCleanupRequest)


def test_worker_worktree_endpoint_returns_structured_dependency_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    details = {
        "base_branch": "main",
        "base_commit_sha": "a" * 40,
        "base_tree_hash": "b" * 40,
        "dependency_git_sources": [
            {
                "dependency_task_id": "source-task",
                "deliverable_id": "source-deliverable",
                "branch": "agents/source/tasks/source-task",
                "commit_sha": "c" * 40,
            }
        ],
        "failed_dependency_task_id": "source-task",
        "failed_deliverable_id": "source-deliverable",
        "conflict_paths": ["src/shared.py"],
    }

    def _prepare(_session_id: str, _request: WorkerTaskWorktreeRequest) -> None:
        raise WorkerDependencyCompositionConflict(
            "dependency Git composition conflict",
            details=details,
        )

    monkeypatch.setattr(daemon, "prepare_task_worktree", _prepare)

    with TestClient(daemon.create_app()) as client:
        response = client.post(
            "/v1/sessions/session-1/worktrees",
            headers=_auth(),
            json={
                "repo_name": "repo",
                "worktree_name": "task-a-1234",
                "branch": "agents/session-1/tasks/task-a-1234",
                "base_ref": "main",
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": "dependency Git composition conflict",
        "dependency_composition_conflict": details,
    }


def test_worker_prepare_session_endpoint_offloads_blocking_lifecycle_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    offloaded_calls: list[tuple[object, tuple[object, ...]]] = []

    async def _record_threadpool_call(function, *args, **kwargs):
        offloaded_calls.append((function, args))
        return function(*args, **kwargs)

    monkeypatch.setattr(worker_api, "run_in_threadpool", _record_threadpool_call)

    app = daemon.create_app()
    endpoint = next(
        route.endpoint for route in app.routes if getattr(route, "path", None) == "/v1/sessions"
    )
    body = json.dumps({"session_id": "session-1"}).encode()

    async def _receive():
        return {"type": "http.request", "body": body, "more_body": False}

    response = asyncio.run(
        endpoint(
            worker_api.Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/v1/sessions",
                    "query_string": b"",
                    "headers": [],
                },
                _receive,
            )
        )
    )
    assert response.status_code == 201
    assert len(offloaded_calls) == 1
    assert offloaded_calls[0][0] == daemon.prepare_session
    assert isinstance(offloaded_calls[0][1][0], WorkerSessionPrepareRequest)


@pytest.mark.parametrize(
    ("change_kind", "failed_check", "expected_paths"),
    [
        ("tracked", "tracked_changes", ["README.md"]),
        ("untracked", "untracked_files", ["notes.txt"]),
    ],
)
def test_worker_cleanup_refuses_unsafe_task_worktree(
    tmp_path: Path,
    change_kind: str,
    failed_check: str,
    expected_paths: list[str],
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Worker Test"], cwd=source, check=True)
    (source / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=source, check=True)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    daemon.clone_repo(
        "session-1",
        WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    worktree_name = "task-a-1234"
    branch = "agents/session-1/tasks/task-a-1234"
    prepared = daemon.prepare_task_worktree(
        "session-1",
        WorkerTaskWorktreeRequest(
            repo_name="repo",
            worktree_name=worktree_name,
            branch=branch,
            base_ref="main",
        ),
    )
    target = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "worktrees"
        / "repo"
        / worktree_name
    )
    if change_kind == "tracked":
        (target / "README.md").write_text("changed\n", encoding="utf-8")
    else:
        (target / "notes.txt").write_text("untracked\n", encoding="utf-8")

    result = daemon.cleanup_task_worktree(
        "session-1",
        WorkerTaskWorktreeCleanupRequest(
            repo_name="repo",
            worktree_name=worktree_name,
            branch=branch,
            expected_commit_sha=prepared.commit_sha,
            expected_target_dir=prepared.target_dir,
        ),
    )

    assert result.state == "refused"
    assert result.safe_to_remove is False
    assert result.removed is False
    assert result.failed_check == failed_check
    assert result.validation_count == 1
    assert (
        result.tracked_paths if change_kind == "tracked" else result.untracked_paths
    ) == expected_paths
    assert target.exists()


def test_worker_provisions_session_git_write_key_and_preserves_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ssh_source = tmp_path / "git-ssh"
    ssh_source.mkdir()
    (ssh_source / "evander-git").write_text("read key\n", encoding="utf-8")
    (ssh_source / "evander-git.pub").write_text("read key pub\n", encoding="utf-8")
    (ssh_source / "config").write_text(
        "\n".join(
            [
                "# BEGIN evander git host",
                "Host evander-git-host",
                "  HostName 10.2.0.4",
                "  User agent",
                "  IdentityFile ~/.ssh/evander-git",
                "# END evander git host",
                "",
            ]
        ),
        encoding="utf-8",
    )
    runtime = FakeContainerRuntime()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
        git_ssh_source_dir=ssh_source,
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        key_path = Path(args[-1])
        key_path.write_text("private key\n", encoding="utf-8")
        key_path.with_suffix(".pub").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestWorkerKey worker\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    daemon.prepare_session(WorkerSessionPrepareRequest(session_id="session-1", metadata={}))

    record = daemon.provision_git_write_key(
        "session-1",
        WorkerGitWriteKeyRequest(
            git_host="10.2.0.4",
            git_user="agent",
            git_ssh_alias="evander-git-host",
        ),
    )

    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    config_text = (paths.agent_home / ".ssh" / "config").read_text(encoding="utf-8")
    assert record.public_key_body == "AAAAC3NzaC1lZDI1NTE5AAAAITestWorkerKey"
    assert record.git_ssh_alias == "evander-git-host-write"
    assert config_text.count("Host evander-git-host\n") == 1
    assert config_text.count("Host evander-git-host-write\n") == 1
    assert config_text.index("Host evander-git-host\n") < config_text.index(
        "Host evander-git-host-write\n"
    )
    assert "# BEGIN evander git host" in config_text
    assert "# BEGIN evander git write host" in config_text
    assert "IdentityFile ~/.ssh/evander-git" in config_text
    assert "IdentityFile ~/.ssh/evander-git-write" in config_text
    host_config_text = (paths.root / "host-ssh" / "config").read_text(encoding="utf-8")
    assert f"IdentityFile {paths.root / 'host-ssh' / 'evander-git'}" in host_config_text
    assert f"IdentityFile {paths.agent_home / '.ssh' / 'evander-git-write'}" in host_config_text
    assert _git_subprocess_env(paths.agent_home)["GIT_SSH_COMMAND"] == (
        f"ssh -F {paths.root / 'host-ssh' / 'config'}"
    )

    daemon.start_job(
        WorkerJobRequest(
            session_id="session-1",
            job_id="job-1",
            image="runner:latest",
            command=["true"],
        )
    )

    assert (paths.agent_home / ".ssh" / "config").read_text(encoding="utf-8") == config_text
    refreshed_host_config = (paths.root / "host-ssh" / "config").read_text(encoding="utf-8")
    assert "Host evander-git-host-write" in refreshed_host_config
    assert f"IdentityFile {paths.agent_home / '.ssh' / 'evander-git-write'}" in (
        refreshed_host_config
    )


def test_worker_rejects_symlinked_git_write_ssh_directory(tmp_path: Path) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    victim_dir = tmp_path / "victim-ssh"
    victim_dir.mkdir()
    os.chmod(victim_dir, 0o755)
    (paths.agent_home / ".ssh").symlink_to(victim_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="Managed SSH"):
        daemon.provision_git_write_key(
            "session-1",
            WorkerGitWriteKeyRequest(
                git_host="10.2.0.4",
                git_user="agent",
                git_ssh_alias="evander-git-host",
            ),
        )

    assert stat.S_IMODE(victim_dir.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    "unsafe_name",
    ["evander-git-write", "evander-git-write.pub", "config"],
)
def test_worker_rejects_symlinked_git_write_managed_files(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    ssh_dir = paths.agent_home / ".ssh"
    ssh_dir.mkdir()
    private_key = ssh_dir / "evander-git-write"
    public_key = ssh_dir / "evander-git-write.pub"
    if unsafe_name != private_key.name:
        private_key.write_text("private key\n", encoding="utf-8")
    if unsafe_name != public_key.name:
        public_key.write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestWorkerKey worker\n",
            encoding="utf-8",
        )
    victim = tmp_path / f"victim-{unsafe_name.replace('.', '-')}"
    victim.write_text("host-owned\n", encoding="utf-8")
    os.chmod(victim, 0o644)
    (ssh_dir / unsafe_name).symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        daemon.provision_git_write_key(
            "session-1",
            WorkerGitWriteKeyRequest(
                git_host="10.2.0.4",
                git_user="agent",
                git_ssh_alias="evander-git-host",
            ),
        )

    assert victim.read_text(encoding="utf-8") == "host-owned\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_worker_api_provisions_session_git_write_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        key_path = Path(args[-1])
        key_path.write_text("private key\n", encoding="utf-8")
        key_path.with_suffix(".pub").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestWorkerApiKey worker\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = _client(tmp_path)

    response = client.post(
        "/v1/sessions/session-1/git/write-key",
        headers=_auth(),
        json={
            "git_host": "10.2.0.4",
            "git_user": "agent",
            "git_ssh_alias": "evander-git-host",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["public_key_body"] == "AAAAC3NzaC1lZDI1NTE5AAAAITestWorkerApiKey"
    assert payload["git_ssh_alias"] == "evander-git-host-write"
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    config_text = (paths.agent_home / ".ssh" / "config").read_text(encoding="utf-8")
    assert "Host evander-git-host" in config_text
    assert "Host evander-git-host-write" in config_text
    assert "IdentityFile ~/.ssh/evander-git-write" in config_text
    host_config_text = (paths.root / "host-ssh" / "config").read_text(encoding="utf-8")
    assert f"IdentityFile {paths.agent_home / '.ssh' / 'evander-git-write'}" in host_config_text


def test_worker_api_rejects_legacy_worker_git_key_name(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/sessions/session-1/git/write-key",
        headers=_auth(),
        json={
            "git_host": "10.2.0.4",
            "git_user": "agent",
            "git_ssh_alias": "evander-git-host",
            "key_name": "evander-git",
        },
    )

    assert response.status_code == 400
    assert "reserved for the worker Git credential" in response.json()["error"]
    assert not (
        tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / ".ssh" / "evander-git"
    ).exists()


def test_worker_service_rejects_legacy_worker_git_key_name(tmp_path: Path) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    request = WorkerGitWriteKeyRequest.model_construct(
        git_host="10.2.0.4",
        git_user="agent",
        git_ssh_alias="evander-git-host",
        key_name="evander-git",
    )

    with pytest.raises(ValueError, match="reserved for the worker Git credential"):
        daemon.provision_git_write_key("session-1", request)

    assert not (tmp_path / "worker-root" / "sessions" / "session-1").exists()


def test_worker_preserves_multiple_exact_repo_keys_and_revokes_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ssh_source = tmp_path / "git-ssh"
    ssh_source.mkdir()
    (ssh_source / "evander-git").write_text("read key\n", encoding="utf-8")
    (ssh_source / "evander-git.pub").write_text("read key pub\n", encoding="utf-8")
    (ssh_source / "config").write_text(
        "Host evander-git-host\n  IdentityFile ~/.ssh/evander-git\n",
        encoding="utf-8",
    )
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
        git_ssh_source_dir=ssh_source,
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        key_path = Path(args[-1])
        key_path.write_text("private key\n", encoding="utf-8")
        key_path.with_name(f"{key_path.name}.pub").write_text(
            f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{key_path.name} worker\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    daemon.prepare_session(WorkerSessionPrepareRequest(session_id="session-1", metadata={}))
    for key_name, alias in (("evander-git-one", "git-one"), ("evander-git-two", "git-two")):
        daemon.prepare_session(WorkerSessionPrepareRequest(session_id="session-1", metadata={}))
        daemon.provision_git_write_key(
            "session-1",
            WorkerGitWriteKeyRequest(
                git_host="10.2.0.4",
                git_user="agent",
                git_ssh_alias=alias,
                key_name=key_name,
                alias_is_exact=True,
            ),
        )

    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    config_path = paths.agent_home / ".ssh" / "config"
    host_config_path = paths.root / "host-ssh" / "config"
    assert "Host git-one" in config_path.read_text(encoding="utf-8")
    assert "Host git-two" in config_path.read_text(encoding="utf-8")
    assert "Host git-one" in host_config_path.read_text(encoding="utf-8")
    assert "Host git-two" in host_config_path.read_text(encoding="utf-8")

    daemon.prepare_session(WorkerSessionPrepareRequest(session_id="session-1", metadata={}))
    assert "Host git-one" in config_path.read_text(encoding="utf-8")
    assert "Host git-two" in config_path.read_text(encoding="utf-8")
    assert "Host git-one" in host_config_path.read_text(encoding="utf-8")
    assert "Host git-two" in host_config_path.read_text(encoding="utf-8")

    daemon.start_job(
        WorkerJobRequest(
            session_id="session-1",
            job_id="job-1",
            image="runner:latest",
            command=["true"],
        )
    )
    assert "Host git-one" in host_config_path.read_text(encoding="utf-8")
    assert "Host git-two" in host_config_path.read_text(encoding="utf-8")

    result = daemon.revoke_git_key("session-1", "evander-git-one")

    config_text = config_path.read_text(encoding="utf-8")
    assert result.removed is True
    assert "Host git-one" not in config_text
    assert "Host git-two" in config_text
    assert not (paths.agent_home / ".ssh" / "evander-git-one").exists()
    assert (paths.agent_home / ".ssh" / "evander-git-two").is_file()


def test_worker_provisions_dotted_git_write_key_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeContainerRuntime()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        key_path = Path(args[-1])
        key_path.write_text("private key\n", encoding="utf-8")
        key_path.with_name(f"{key_path.name}.pub").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestWorkerDottedKey worker\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    daemon.prepare_session(WorkerSessionPrepareRequest(session_id="session-1", metadata={}))

    record = daemon.provision_git_write_key(
        "session-1",
        WorkerGitWriteKeyRequest(
            git_host="10.2.0.4",
            git_user="agent",
            git_ssh_alias="evander-git-host",
            key_name="foo.bar",
        ),
    )

    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session-1")
    ssh_dir = paths.agent_home / ".ssh"
    assert record.public_key_body == "AAAAC3NzaC1lZDI1NTE5AAAAITestWorkerDottedKey"
    assert (ssh_dir / "foo.bar").read_text(encoding="utf-8") == "private key\n"
    assert (ssh_dir / "foo.bar.pub").is_file()
    assert record.git_ssh_alias == "evander-git-host-write"
    assert "IdentityFile ~/.ssh/foo.bar" in (ssh_dir / "config").read_text(encoding="utf-8")
    assert f"IdentityFile {ssh_dir / 'foo.bar'}" in (paths.root / "host-ssh" / "config").read_text(
        encoding="utf-8"
    )
    assert not list(ssh_dir.glob(".foo.bar-*"))


def test_worker_daemon_read_telemetry_degrades_when_sqlite_job_store_is_unavailable(
    tmp_path: Path,
) -> None:
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )

    class LockedJobStore:
        def list_all_ordered(self) -> list[WorkerJobRecord]:
            raise sqlite3.OperationalError("database is locked")

    daemon.store = LockedJobStore()  # type: ignore[assignment]

    telemetry = daemon.read_telemetry()

    assert telemetry.jobs.status is TelemetryStatus.UNSUPPORTED
    assert telemetry.jobs.health is HealthState.UNKNOWN
    assert telemetry.jobs.error_count == 1
    assert telemetry.jobs.errors[0].message == "job store is unavailable"


def test_worker_job_request_rejects_egress_policy_fields() -> None:
    with pytest.raises(ValidationError, match="egress_policy_enabled"):
        WorkerJobRequest.model_validate(
            {
                "session_id": "session-1",
                "job_id": "job-1",
                "image": "codex:latest",
                "command": ["true"],
                "egress_policy_enabled": True,
            }
        )

    assert not any("egress" in field_name for field_name in WorkerJobRequest.model_fields)


def test_session_directories_are_created_and_reused_without_replacing_codex(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)

    first = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    assert first.status_code == 201
    paths = runtime.started[0][2]
    codex_dir = paths.agent_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("model = 'gpt-5.2'\n", encoding="utf-8")

    second = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-2",
            "image": "codex:latest",
            "command": ["true"],
        },
    )

    assert second.status_code == 201
    assert runtime.started[1][2].root == paths.root
    assert (codex_dir / "config.toml").read_text(encoding="utf-8") == "model = 'gpt-5.2'\n"
    assert paths.agent_home.is_dir()
    assert paths.repo_root.is_dir()
    assert paths.artifacts.is_dir()
    assert paths.logs.is_dir()
    assert not (paths.root / "workspace").exists()
    assert json.loads(paths.metadata_path.read_text(encoding="utf-8"))["session_id"] == "session-1"


def test_session_prepare_and_file_write_surface_targets_durable_paths(tmp_path: Path) -> None:
    client = _client(tmp_path)

    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1", "metadata": {"purpose": "test"}},
    )
    assert prepared.status_code == 201
    assert prepared.json()["agent_home_container_path"] == "/home/agent"
    assert prepared.json()["repo_root_container_path"] == "/home/agent/repos"

    written = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={
            "files": [
                {"destination_path": "/home/agent/AGENTS.md", "content": "# Agent\n"},
                {
                    "destination_path": "/home/agent/repos/Repo/AGENT_TASK.yaml",
                    "content": "task: test\n",
                },
            ]
        },
    )

    assert written.status_code == 200
    assert written.json()["copied_paths"] == [
        "/home/agent/AGENTS.md",
        "/home/agent/repos/Repo/AGENT_TASK.yaml",
    ]
    root = tmp_path / "worker-root" / "sessions" / "session-1"
    assert (root / "agent_home" / "AGENTS.md").read_text(encoding="utf-8") == "# Agent\n"
    assert (root / "agent_home" / "repos" / "Repo" / "AGENT_TASK.yaml").read_text(
        encoding="utf-8"
    ) == "task: test\n"


def test_session_file_write_skips_identical_existing_content_and_mode(tmp_path: Path) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201

    payload = {
        "files": [
            {
                "destination_path": "/home/agent/AGENTS.md",
                "content": "# Agent\n",
                "mode": "0644",
                "overwrite": True,
            }
        ]
    }
    first = client.post("/v1/sessions/session-1/files", headers=_auth(), json=payload)
    second = client.post("/v1/sessions/session-1/files", headers=_auth(), json=payload)
    mode_change = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={
            "files": [
                {
                    "destination_path": "/home/agent/AGENTS.md",
                    "content": "# Agent\n",
                    "mode": "0600",
                    "overwrite": True,
                }
            ]
        },
    )

    assert first.status_code == 200
    assert first.json()["copied_count"] == 1
    assert first.json()["copied_paths"] == ["/home/agent/AGENTS.md"]
    assert second.status_code == 200
    assert second.json()["copied_count"] == 0
    assert second.json()["copied_paths"] == []
    assert mode_change.status_code == 200
    assert mode_change.json()["copied_count"] == 1
    target = tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / "AGENTS.md"
    assert target.read_text(encoding="utf-8") == "# Agent\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_session_file_write_merges_managed_section(tmp_path: Path) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201

    first = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={
            "files": [
                {
                    "destination_path": "/home/agent/AGENTS.md",
                    "content": "# Base\n",
                    "overwrite": True,
                }
            ]
        },
    )
    merged = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={
            "files": [
                {
                    "destination_path": "/home/agent/AGENTS.md",
                    "content": "Team guidance v1\n",
                    "mode": "0644",
                    "overwrite": True,
                    "merge_strategy": "managed_section",
                    "section_id": "evander-team-addon",
                }
            ]
        },
    )
    replaced = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={
            "files": [
                {
                    "destination_path": "/home/agent/AGENTS.md",
                    "content": "Team guidance v2\n",
                    "mode": "0644",
                    "overwrite": True,
                    "merge_strategy": "managed_section",
                    "section_id": "evander-team-addon",
                }
            ]
        },
    )

    assert first.status_code == 200
    assert merged.status_code == 200
    assert merged.json()["copied_paths"] == ["/home/agent/AGENTS.md"]
    assert replaced.status_code == 200
    assert replaced.json()["copied_paths"] == ["/home/agent/AGENTS.md"]
    target = tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / "AGENTS.md"
    assert target.read_text(encoding="utf-8") == (
        "# Base\n\n"
        "<!-- evander-managed-section:evander-team-addon:begin -->\n"
        "Team guidance v2\n"
        "<!-- evander-managed-section:evander-team-addon:end -->\n"
    )


def test_session_file_write_removes_stale_managed_sections(tmp_path: Path) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201
    target = tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / "AGENTS.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Base\n\n"
        "<!-- evander-managed-section:truman-team-addon:begin -->\n"
        "Old guidance\n"
        "<!-- evander-managed-section:truman-team-addon:end -->\n\n"
        "<!-- evander-managed-section:evander-team-addon:begin -->\n"
        "Team guidance v1\n"
        "<!-- evander-managed-section:evander-team-addon:end -->\n",
        encoding="utf-8",
    )

    replaced = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={
            "files": [
                {
                    "destination_path": "/home/agent/AGENTS.md",
                    "content": "Team guidance v2\n",
                    "mode": "0644",
                    "overwrite": True,
                    "merge_strategy": "managed_section",
                    "section_id": "evander-team-addon",
                    "remove_section_ids": ["truman-team-addon"],
                }
            ]
        },
    )

    assert replaced.status_code == 200
    assert "truman-team-addon" not in target.read_text(encoding="utf-8")
    assert "Team guidance v2" in target.read_text(encoding="utf-8")


def test_session_artifact_text_endpoint_reads_utf8_artifact_and_rejects_escape(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201
    artifact_dir = tmp_path / "worker-root" / "sessions" / "session-1" / "artifacts" / "reports"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "summary.txt").write_text("daily summary\n", encoding="utf-8")

    response = client.get(
        "/v1/sessions/session-1/artifacts/reports/summary.txt",
        headers=_auth(),
    )
    escaped = client.get(
        "/v1/sessions/session-1/artifacts/%2E%2E/metadata.json",
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.text == "daily summary\n"
    assert escaped.status_code == 400


def test_session_artifact_text_endpoint_rejects_symlink_leaf_inside_artifacts(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201
    artifact_dir = tmp_path / "worker-root" / "sessions" / "session-1" / "artifacts" / "reports"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "summary.txt").write_text("daily summary\n", encoding="utf-8")
    (artifact_dir / "link.txt").symlink_to("summary.txt")

    normal = client.get(
        "/v1/sessions/session-1/artifacts/reports/summary.txt",
        headers=_auth(),
    )
    linked = client.get(
        "/v1/sessions/session-1/artifacts/reports/link.txt",
        headers=_auth(),
    )

    assert normal.status_code == 200
    assert normal.text == "daily summary\n"
    assert linked.status_code == 400
    assert "symlinks" in linked.json()["error"]


def test_session_artifact_text_endpoint_does_not_follow_leaf_symlink_swapped_at_read_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201
    artifact_dir = tmp_path / "worker-root" / "sessions" / "session-1" / "artifacts" / "reports"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "summary.txt"
    artifact.write_text("daily summary\n", encoding="utf-8")
    outside = tmp_path / "host-secret.txt"
    outside.write_text("host secret\n", encoding="utf-8")
    original_open = os.open

    def swap_leaf_to_symlink_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == "summary.txt" and not artifact.is_symlink():
            artifact.unlink()
            artifact.symlink_to(outside)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_leaf_to_symlink_before_open)

    response = client.get(
        "/v1/sessions/session-1/artifacts/reports/summary.txt",
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "symlinks" in response.json()["error"]
    assert outside.read_text(encoding="utf-8") == "host secret\n"


def test_session_prepare_keeps_git_ssh_outside_agent_home(tmp_path: Path) -> None:
    ssh_source = tmp_path / "worker-ssh"
    ssh_source.mkdir()
    (ssh_source / "evander-git").write_text("private-key\n", encoding="utf-8")
    (ssh_source / "evander-git.pub").write_text("public-key\n", encoding="utf-8")
    (ssh_source / "config").write_text("Host evander-git-host\n", encoding="utf-8")
    (ssh_source / "known_hosts").write_text("known-host\n", encoding="utf-8")
    client = _client(tmp_path, git_ssh_source_dir=ssh_source)
    exposed_dir = tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / ".ssh"
    exposed_dir.mkdir(parents=True)
    (exposed_dir / "evander-git").write_text("legacy-private-key\n", encoding="utf-8")

    response = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )

    assert response.status_code == 201
    session_root = tmp_path / "worker-root" / "sessions" / "session-1"
    assert not (session_root / "agent_home" / ".ssh" / "evander-git").exists()
    target = session_root / "host-ssh"
    assert (target / "evander-git").read_text(encoding="utf-8") == "private-key\n"
    assert (target / "config").read_text(encoding="utf-8") == "Host evander-git-host\n"
    assert oct((target / "evander-git").stat().st_mode & 0o777) == "0o600"
    assert oct((target / "evander-git.pub").stat().st_mode & 0o777) == "0o644"


def test_session_delete_removes_durable_paths_and_worker_jobs(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)
    created = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    assert created.status_code == 201
    root = tmp_path / "worker-root" / "sessions" / "session-1"
    assert root.is_dir()

    deleted = client.delete("/v1/sessions/session-1", headers=_auth())

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "session_id": "session-1"}
    assert runtime.removed == ["job-1"]
    assert not root.exists()
    job_response = client.get("/v1/jobs/job-1", headers=_auth())
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "removed"
    assert job_response.json()["cleanup_status"] == "cleanup_succeeded"
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.json")
    record = store.get("job-1")
    assert record is not None
    assert record.status == "removed"
    assert record.cleanup_status == "cleanup_succeeded"


def test_session_delete_removes_stopped_worker_jobs(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)
    created = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    assert created.status_code == 201
    stopped = client.post("/v1/jobs/job-1/stop", headers=_auth())
    assert stopped.status_code == 200

    deleted = client.delete("/v1/sessions/session-1", headers=_auth())

    assert deleted.status_code == 200
    assert runtime.removed == ["job-1"]
    job_response = client.get("/v1/jobs/job-1", headers=_auth())
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "removed"
    assert job_response.json()["cleanup_status"] == "cleanup_succeeded"


def test_session_delete_uses_session_scoped_job_lookup(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )

    session_a = WorkerSessionPaths.resolve(daemon.worker_root, "session-a")
    session_b = WorkerSessionPaths.resolve(daemon.worker_root, "session-b")
    session_jobs: dict[str, WorkerJobRecord] = {
        "job-a": WorkerJobRecord(
            job_id="job-a",
            session_id="session-a",
            image="codex:latest",
            command=["true"],
            container_name="agent-worker-job-a",
            status="running",
            started_at=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
            agent_home_path=str(session_a.agent_home),
            repo_root_path=str(session_a.repo_root),
            artifacts_path=str(session_a.artifacts),
            logs_path=str(session_a.logs),
        ),
        "job-b": WorkerJobRecord(
            job_id="job-b",
            session_id="session-b",
            image="codex:latest",
            command=["true"],
            container_name="agent-worker-job-b",
            status="running",
            started_at=datetime(2026, 5, 21, 10, 5, tzinfo=UTC),
            agent_home_path=str(session_b.agent_home),
            repo_root_path=str(session_b.repo_root),
            artifacts_path=str(session_b.artifacts),
            logs_path=str(session_b.logs),
        ),
    }

    class FakeWorkerJobStore:
        def __init__(self) -> None:
            self.put_calls: list[str] = []
            self.list_by_session_calls: list[str] = []

        def list_by_session(self, session_id: str) -> list[WorkerJobRecord]:
            self.list_by_session_calls.append(session_id)
            return [record for record in session_jobs.values() if record.session_id == session_id]

        def read_all(self) -> dict[str, WorkerJobRecord]:
            raise AssertionError("read_all should not be used")

        def put(self, record: WorkerJobRecord) -> None:
            self.put_calls.append(record.job_id)
            session_jobs[record.job_id] = record

        def get(self, job_id: str) -> WorkerJobRecord | None:
            return session_jobs.get(job_id)

    daemon.store = FakeWorkerJobStore()

    deleted = daemon.delete_session("session-a")

    assert deleted is True
    assert runtime.removed == ["job-a"]
    assert daemon.store.list_by_session_calls == ["session-a"]
    assert daemon.store.get("job-a").status == "removed"
    assert daemon.store.get("job-a").cleanup_status == "cleanup_succeeded"
    assert daemon.store.get("job-b").status == "running"


def test_session_delete_marks_cleanup_failed_when_session_root_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)
    created = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    assert created.status_code == 201
    root = tmp_path / "worker-root" / "sessions" / "session-1"

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr("evander.worker.service.shutil.rmtree", _raise)

    deleted = client.delete("/v1/sessions/session-1", headers=_auth())

    assert deleted.status_code == 502
    assert runtime.removed == ["job-1"]
    assert root.exists()
    job_response = client.get("/v1/jobs/job-1", headers=_auth())
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "removed"
    assert job_response.json()["cleanup_status"] == "cleanup_failed"
    assert "permission denied" in job_response.json()["cleanup_error"]
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.json")
    record = store.get("job-1")
    assert record is not None
    assert record.status == "removed"
    assert record.cleanup_status == "cleanup_failed"


def test_mssql_database_endpoints_are_session_scoped_and_cleanup_on_delete(
    tmp_path: Path,
) -> None:
    client = _client(
        tmp_path,
        mssql_enabled=True,
        mssql_admin_password="AdminPassw0rd!",
        mssql_sqlcmd_path="/bin/true",
    )

    created = client.post(
        "/v1/sessions/session-1/mssql/databases",
        headers=_auth(),
        json={"logical_name": "app"},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["logical_name"] == "app"
    assert payload["database_name"].startswith("asb_")
    assert payload["server"] == "172.17.0.1"
    assert payload["username"].startswith("asb_login_")
    assert "Password=" in payload["connection_string"]
    state_path = tmp_path / "runtime" / "mssql-state.json"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600

    second = client.post(
        "/v1/sessions/session-1/mssql/databases",
        headers=_auth(),
        json={"logical_name": "shadow"},
    )
    assert second.status_code == 201
    other_session = client.post(
        "/v1/sessions/session-2/mssql/databases",
        headers=_auth(),
        json={"logical_name": "app"},
    )
    assert other_session.status_code == 201

    listed = client.get("/v1/sessions/session-1/mssql/databases", headers=_auth())

    assert listed.status_code == 200
    assert [item["logical_name"] for item in listed.json()["databases"]] == ["app", "shadow"]

    dropped = client.delete(
        "/v1/sessions/session-1/mssql/databases/app",
        headers=_auth(),
    )
    assert dropped.status_code == 200
    assert dropped.json()["deleted"] is True

    deleted = client.delete("/v1/sessions/session-1", headers=_auth())

    assert deleted.status_code == 200
    assert not (tmp_path / "worker-root" / "sessions" / "session-1").exists()
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    remaining = client.get("/v1/sessions/session-2/mssql/databases", headers=_auth())
    assert [item["logical_name"] for item in remaining.json()["databases"]] == ["app"]


def test_mssql_database_endpoints_reject_dot_segment_session_ids(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        mssql_enabled=True,
        mssql_admin_password="AdminPassw0rd!",
        mssql_sqlcmd_path="/bin/true",
    )

    created = client.post(
        "/v1/sessions/%2E%2E/mssql/databases",
        headers=_auth(),
        json={"logical_name": "app"},
    )
    listed = client.get("/v1/sessions/%2E%2E/mssql/databases", headers=_auth())
    dropped = client.delete(
        "/v1/sessions/%2E%2E/mssql/databases/app",
        headers=_auth(),
    )

    assert [created.status_code, listed.status_code, dropped.status_code] == [400, 400, 400]
    assert all("session_id" in response.json()["error"] for response in (created, listed, dropped))
    state_path = tmp_path / "runtime" / "mssql-state.json"
    assert json.loads(state_path.read_text(encoding="utf-8")) == {}
    assert not (tmp_path / "worker-root" / "agent_home").exists()


def test_mssql_cleanup_failure_blocks_session_file_deletion(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        mssql_enabled=True,
        mssql_admin_password="AdminPassw0rd!",
        mssql_sqlcmd_path="/bin/true",
    )
    created = client.post(
        "/v1/sessions/session-1/mssql/databases",
        headers=_auth(),
        json={"logical_name": "app"},
    )
    assert created.status_code == 201
    session_root = tmp_path / "worker-root" / "sessions" / "session-1"
    session_root.mkdir(parents=True, exist_ok=True)
    failing_client = _client(
        tmp_path,
        mssql_enabled=True,
        mssql_admin_password="AdminPassw0rd!",
        mssql_sqlcmd_path="/bin/false",
    )

    deleted = failing_client.delete("/v1/sessions/session-1", headers=_auth())

    assert deleted.status_code == 502
    assert session_root.exists()


def test_mssql_database_provisioning_requires_enabled_worker_support(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post(
        "/v1/sessions/session-1/mssql/databases",
        headers=_auth(),
        json={"logical_name": "app"},
    )
    listed = client.get("/v1/sessions/session-1/mssql/databases", headers=_auth())
    dropped = client.delete(
        "/v1/sessions/session-1/mssql/databases/app",
        headers=_auth(),
    )

    assert created.status_code == 400
    assert "MSSQL support is disabled" in created.json()["error"]
    assert listed.status_code == 400
    assert "MSSQL support is disabled" in listed.json()["error"]
    assert dropped.status_code == 400
    assert "MSSQL support is disabled" in dropped.json()["error"]


def test_session_file_write_rejects_paths_outside_worker_session_roots(tmp_path: Path) -> None:
    client = _client(tmp_path)

    for destination_path in ("/tmp/escape.txt", "/workspace/AGENT_TASK.yaml"):
        response = client.post(
            "/v1/sessions/session-1/files",
            headers=_auth(),
            json={"files": [{"destination_path": destination_path, "content": "bad"}]},
        )

        assert response.status_code == 400
        assert "/home/agent" in response.json()["error"]


@pytest.mark.parametrize(
    "destination_path",
    [
        "/home/agent/./AGENTS.md",
        "/home/agent/repos/../escape.txt",
    ],
)
def test_session_file_write_rejects_dot_segment_destinations(
    tmp_path: Path,
    destination_path: str,
) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={"files": [{"destination_path": destination_path, "content": "bad"}]},
    )

    assert response.status_code == 400
    assert "dot, or parent segments" in response.json()["error"]
    session_root = tmp_path / "worker-root" / "sessions" / "session-1"
    assert not (session_root / "agent_home" / "AGENTS.md").exists()
    assert not (session_root / "agent_home" / "escape.txt").exists()


def test_session_file_write_rejects_symlink_escape_from_worker_roots(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201
    outside = tmp_path / "outside"
    outside.mkdir()
    repo_root = tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / "repos"
    (repo_root / "link").symlink_to(outside, target_is_directory=True)

    response = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={
            "files": [
                {
                    "destination_path": "/home/agent/repos/link/escape.txt",
                    "content": "bad",
                }
            ]
        },
    )

    assert response.status_code == 400
    assert "must stay below /home/agent" in response.json()["error"]
    assert not (outside / "escape.txt").exists()


def test_session_file_write_rejects_leaf_symlink_swapped_at_write_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path)
    prepared = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )
    assert prepared.status_code == 201
    target = tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / "AGENTS.md"
    outside = tmp_path / "outside.txt"
    outside.write_text("host secret\n", encoding="utf-8")
    original_open = os.open
    leaf_open_count = 0

    def swap_leaf_to_symlink_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal leaf_open_count
        if path == "AGENTS.md":
            leaf_open_count += 1
        if path == "AGENTS.md" and leaf_open_count == 2 and not target.exists():
            target.symlink_to(outside)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_leaf_to_symlink_before_open)

    response = client.post(
        "/v1/sessions/session-1/files",
        headers=_auth(),
        json={"files": [{"destination_path": "/home/agent/AGENTS.md", "content": "# Agent\n"}]},
    )

    assert response.status_code == 400
    assert "must stay below /home/agent" in response.json()["error"]
    assert outside.read_text(encoding="utf-8") == "host secret\n"
    assert target.is_symlink()


def test_session_repo_clone_clones_into_named_repo_under_agent_home(tmp_path: Path) -> None:
    source = tmp_path / "source-repo"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True, text=True)
    (source / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )
    client = _client(tmp_path, allowed_git_source_prefixes=[str(source)])

    first = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": str(source), "repo_name": "ExampleIndustryAPP"},
    )
    second = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": str(source), "repo_name": "ExampleIndustryAPP"},
    )

    assert first.status_code == 200
    assert first.json()["cloned"] is True
    assert first.json()["target_dir"] == "/home/agent/repos/ExampleIndustryAPP"
    assert first.json()["guest_repo_name"] == "ExampleIndustryAPP"
    assert second.status_code == 200
    assert second.json()["cloned"] is False
    checkout = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "repos"
        / "ExampleIndustryAPP"
    )
    assert (checkout / "README.md").read_text(encoding="utf-8") == "hello\n"


@pytest.mark.parametrize("repo_name", ["../escape", ".", ".."])
def test_session_repo_clone_rejects_unsafe_repo_name(
    tmp_path: Path,
    repo_name: str,
) -> None:
    source = tmp_path / "source-repo"
    source.mkdir()
    client = _client(tmp_path, allowed_git_source_prefixes=[str(source)])

    response = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": str(source), "repo_name": repo_name},
    )

    assert response.status_code == 400
    assert "repo_name" in response.json()["error"]


def test_session_repo_clone_rejects_conflicting_non_empty_target(tmp_path: Path) -> None:
    source = tmp_path / "source-repo"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True, text=True)
    (source / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )
    client = _client(tmp_path, allowed_git_source_prefixes=[str(source)])
    conflict = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / "repos"
        / "ExampleIndustryAPP"
    )
    conflict.mkdir(parents=True)
    (conflict / "README.md").write_text("not this repo\n", encoding="utf-8")

    response = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": str(source), "repo_name": "ExampleIndustryAPP"},
    )

    assert response.status_code == 400
    assert "not a checkout for requested source" in response.json()["error"]


def test_session_repo_clone_rejects_sources_without_allowed_prefix(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": "https://github.com/example/repo.git"},
    )

    assert response.status_code == 400
    assert "prefixes are not configured" in response.json()["error"]


@pytest.mark.parametrize(
    "repo_source",
    [
        "https://github.com/example/repo.git",
        (f"ssh://evander-git-host-00000000000000000000/opt/git/{_TEST_MANAGED_REPO_ID}.git"),
        (f"ssh://{_TEST_MANAGED_GIT_ALIAS}.example.com/opt/git/{_TEST_MANAGED_REPO_ID}.git"),
        (f"ssh://{_TEST_MANAGED_GIT_ALIAS}/opt/other/{_TEST_MANAGED_REPO_ID}.git"),
        (f"ssh://{_TEST_MANAGED_GIT_ALIAS}/opt/git/%2e%2e/{_TEST_MANAGED_REPO_ID}.git"),
        (f"ssh://agent:secret@{_TEST_MANAGED_GIT_ALIAS}/opt/git/{_TEST_MANAGED_REPO_ID}.git"),
        (f"ssh://{_TEST_MANAGED_GIT_ALIAS}/opt/git/{_TEST_MANAGED_REPO_ID}.git?ref=main"),
        (f"ssh://{_TEST_MANAGED_GIT_ALIAS}/opt/git/{_TEST_MANAGED_REPO_ID}.git#main"),
    ],
)
def test_session_repo_clone_rejects_sources_outside_allowed_prefix(
    tmp_path: Path,
    repo_source: str,
) -> None:
    client = _client(
        tmp_path,
        allowed_git_source_prefixes=["ssh://evander-git-host/opt/git/"],
    )

    response = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": repo_source},
    )

    assert response.status_code == 400
    assert "repo_source is not allowed" in response.json()["error"]


def test_job_repo_clone_enforces_allowed_prefix(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    client = _client(
        tmp_path,
        runtime,
        allowed_git_source_prefixes=["ssh://evander-git-host/opt/git/"],
    )

    response = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
            "repo_source": "git@ssh.dev.azure.com:v3/org/project/repo",
        },
    )

    assert response.status_code == 400
    assert "repo_source is not allowed" in response.json()["error"]
    assert runtime.started == []


def test_container_mount_paths_are_persisted_for_repo_root_and_agent_home(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)

    response = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )

    payload = response.json()
    paths = runtime.started[0][2]
    assert payload["agent_home_path"] == str(paths.agent_home)
    assert payload["repo_root_path"] == str(paths.repo_root)
    assert paths.agent_home == tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home"
    assert paths.repo_root == paths.agent_home / "repos"


def test_worker_job_store_migrates_legacy_workspace_path_records(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    legacy_workspace = tmp_path / "worker-root" / "sessions" / "legacy-session" / "workspace"
    store_path = runtime_dir / "worker-jobs.json"
    legacy_text = json.dumps(
        {
            "legacy-job": {
                "job_id": "legacy-job",
                "session_id": "legacy-session",
                "image": "codex:latest",
                "command": ["true"],
                "container_name": "agent-worker-legacy-job",
                "status": "exited",
                "created_at": "2026-05-03T20:00:00+00:00",
                "workspace_path": str(legacy_workspace),
                "agent_home_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "agent_home"
                ),
                "artifacts_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "artifacts"
                ),
                "logs_path": str(tmp_path / "worker-root" / "sessions" / "legacy-session" / "logs"),
                "metadata": {},
            }
        },
        indent=2,
        sort_keys=True,
    )
    store_path.write_text(legacy_text, encoding="utf-8")
    legacy_bytes = store_path.read_bytes()
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)

    response = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )

    assert response.status_code == 201
    store = WorkerJobStore(store_path)
    assert store.path == runtime_dir / "worker-jobs.db"
    record = store.get("legacy-job")
    assert record is not None
    assert record.repo_root_path == str(legacy_workspace)
    assert not hasattr(record, "workspace_path")
    new_record = store.get("job-1")
    assert new_record is not None
    assert new_record.session_id == "session-1"
    assert store_path.read_bytes() == legacy_bytes


def test_job_lifecycle_create_inspect_stop_delete(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    client = _client(tmp_path, runtime)

    created = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["sleep", "1"],
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "running"

    inspected = client.get("/v1/jobs/job-1", headers=_auth())
    assert inspected.status_code == 200
    assert inspected.json()["status"] == "running"

    runtime.inspect_results["job-1"] = {"status": "failed", "exit_code": 7}
    failed = client.get("/v1/jobs/job-1", headers=_auth())
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["metadata"]["exit_code"] == 7
    assert failed.json()["metadata"]["worker_terminal_kind"] == "command_exit"

    stopped = client.post("/v1/jobs/job-1/stop", headers=_auth())
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert runtime.stopped == ["job-1"]

    deleted = client.delete("/v1/jobs/job-1", headers=_auth())
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "job_id": "job-1"}
    assert runtime.removed == ["job-1"]
    deleted_again = client.delete("/v1/jobs/job-1", headers=_auth())
    assert deleted_again.status_code == 200
    assert deleted_again.json() == {"deleted": True, "job_id": "job-1"}
    assert runtime.removed == ["job-1"]


def test_subscription_job_uses_worker_owned_read_only_completion_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runners: list[Any] = []

    class SubscriptionRuntime(FakeContainerRuntime):
        def codex_app_server_command(self, record, turn):
            del record, turn
            return ["codex-app-server"]

    class SubscriptionBroker:
        def external_tokens(self, **kwargs):
            del kwargs
            return CodexExternalAuthTokens("access-secret", "account-1", "plus")

        def close(self) -> None:
            return None

    class Runner:
        def __init__(self, **kwargs):
            self.finished_marker = kwargs["finished_marker"]
            runners.append(self)

        def start(self) -> None:
            return None

        def cancel(self) -> None:
            return None

        def join(self, *, timeout_seconds: float) -> bool:
            del timeout_seconds
            return True

    monkeypatch.setattr("evander.worker.service.CodexSubscriptionTurnRunner", Runner)
    runtime = SubscriptionRuntime()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
        codex_auth_broker=SubscriptionBroker(),  # type: ignore[arg-type]
    )
    request = WorkerJobRequest(
        session_id="session-1",
        job_id="job-1",
        image="runner:latest",
        command=["codex", "exec", "continue"],
        codex_turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            prompt="Continue",
            model="gpt-5.4",
        ),
    )

    try:
        daemon.start_job(request)

        started_request, _record, paths = runtime.started[0]
        shell_command = started_request.command[-1]
        assert "/run/evander/codex-turns/job-1.exit" in shell_command
        assert "/home/agent/.evander/codex-turns" not in shell_command
        assert runners
        assert runners[0].finished_marker == paths.logs / "codex-turns" / "job-1.exit"
        assert paths.agent_home not in runners[0].finished_marker.parents
    finally:
        daemon.close()


def test_subscription_restart_ignores_agent_completion_marker_ancestor_symlink(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_root = tmp_path / "worker-root"
    paths = WorkerSessionPaths.resolve(worker_root, "session-1")
    attacker_marker_dir = paths.agent_home / ".evander" / "codex-turns"
    attacker_marker_dir.parent.mkdir(parents=True, exist_ok=True)
    attacker_marker_dir.symlink_to("/proc", target_is_directory=True)
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="runner:latest",
        command=["codex", "exec", "continue"],
        container_name="agent-worker-job-1",
        status="running",
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
        metadata={"codex_auth_mode": "chatgpt_subscription"},
    )
    WorkerJobStore(runtime_dir / "worker-jobs.json").put(record)

    daemon = WorkerDaemon(
        runtime_dir=runtime_dir,
        worker_root=worker_root,
        container_runtime=FakeContainerRuntime(),
        token="test-token",
    )
    try:
        reconciled = daemon.store.get("job-1")
        assert reconciled is not None
        assert reconciled.status == "failed"
        assert (paths.logs / "codex-turns" / "job-1.exit").read_text(encoding="utf-8") == "1"
        assert attacker_marker_dir.is_symlink()
    finally:
        daemon.close()


def test_worker_waits_for_subscription_runner_before_removing_job_data(tmp_path: Path) -> None:
    events: list[str] = []

    class OrderedRuntime(FakeContainerRuntime):
        def remove_job(self, record: WorkerJobRecord) -> None:
            events.append("container-remove")
            super().remove_job(record)

    class Runner:
        def cancel_and_wait(self, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 40.0
            events.append("runner-stopped")

    runtime = OrderedRuntime()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="runner:latest",
        command=["true"],
        container_name="agent-worker-job-1",
        status="running",
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    daemon.store.put(record)
    daemon._codex_subscription_runners[record.job_id] = Runner()  # type: ignore[assignment]  # noqa: SLF001

    daemon.remove_job(record)

    assert events == ["runner-stopped", "container-remove"]


def test_worker_keeps_subscription_runner_handle_when_shutdown_fails(tmp_path: Path) -> None:
    class StuckRunner:
        def cancel_and_wait(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            raise RuntimeError("runner still active")

    runtime = FakeContainerRuntime()
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="runner:latest",
        command=["true"],
        container_name="agent-worker-job-1",
        status="running",
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    runner = StuckRunner()
    daemon._codex_subscription_runners[record.job_id] = runner  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(RuntimeError, match="runner still active"):
        daemon.remove_job(record)

    assert runtime.removed == []
    assert daemon._codex_subscription_runners[record.job_id] is runner  # noqa: SLF001


def test_worker_inspection_records_objective_command_exit_metadata(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    runtime.inspect_results["job-1"] = {"status": "failed", "exit_code": 9}
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="runner:latest",
        command=["dotnet", "test"],
        container_name="agent-worker-job-1",
        status="running",
        agent_home_path="/worker/session-1/agent_home",
        repo_root_path="/worker/session-1/agent_home/repos",
        artifacts_path="/worker/session-1/artifacts",
        logs_path="/worker/session-1/logs",
    )

    inspected = daemon.inspect_job(record)

    assert inspected.status == "failed"
    assert inspected.metadata["exit_code"] == 9
    assert inspected.metadata["worker_terminal_kind"] == "command_exit"


def test_worker_inspection_classifies_oom_exit_as_infrastructure(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    runtime.inspect_results["job-1"] = {
        "status": "failed",
        "exit_code": 137,
        "oom_killed": True,
    }
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="runner:latest",
        command=["dotnet", "test"],
        container_name="agent-worker-job-1",
        status="running",
        agent_home_path="/worker/session-1/agent_home",
        repo_root_path="/worker/session-1/agent_home/repos",
        artifacts_path="/worker/session-1/artifacts",
        logs_path="/worker/session-1/logs",
    )

    inspected = daemon.inspect_job(record)

    assert inspected.metadata["worker_terminal_kind"] == "infrastructure"
    assert "memory limit" in (inspected.error or "")
    assert inspected.failure_cause == "memory_limit_exhausted"


def test_worker_inspection_classifies_pids_limit_event_as_infrastructure(
    tmp_path: Path,
) -> None:
    runtime = FakeContainerRuntime()
    runtime.inspect_results["job-1"] = {
        "status": "failed",
        "exit_code": 1,
        "oom_killed": False,
        "failure_cause": "pids_limit_exhausted",
    }
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="runner:latest",
        command=["dotnet", "test"],
        container_name="agent-worker-job-1",
        status="running",
        agent_home_path="/worker/session-1/agent_home",
        repo_root_path="/worker/session-1/agent_home/repos",
        artifacts_path="/worker/session-1/artifacts",
        logs_path="/worker/session-1/logs",
    )

    inspected = daemon.inspect_job(record)

    assert inspected.metadata["worker_terminal_kind"] == "infrastructure"
    assert inspected.failure_cause == "pids_limit_exhausted"
    assert "process/thread limit" in (inspected.error or "")


def test_logs_are_written_and_read_from_session_logs(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = "hello from container\n"
    client = _client(tmp_path, runtime)

    client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    response = client.get("/v1/jobs/job-1/logs", headers=_auth())

    assert response.status_code == 200
    assert response.text == "hello from container\n"
    log_path = tmp_path / "worker-root" / "sessions" / "session-1" / "logs" / "job-1.log"
    assert log_path.read_text(encoding="utf-8") == "hello from container\n"


def test_logs_endpoint_returns_bounded_pages_from_newest_end(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = "one\ntwo\nthree\nfour\n"
    client = _client(tmp_path, runtime)

    client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    response = client.get("/v1/jobs/job-1/logs?limit=2&offset=1", headers=_auth())

    assert response.status_code == 200
    assert response.text == "two\nthree\n"
    assert response.headers["x-evander-log-paged"] == "true"
    assert response.headers["x-evander-log-returned-lines"] == "2"
    assert response.headers["x-evander-log-has-more-before"] == "true"


def test_logs_recover_codex_messages_from_session_jsonl(tmp_path: Path) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    client = _client(tmp_path, runtime)

    created = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    assert created.status_code == 201
    session_dir = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / ".config"
        / "evander"
        / "codex"
        / "sessions"
        / "2026"
        / "05"
        / "04"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-04T19-30-23-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": "I am checking the repo now.",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "I am checking the repo now.",
                            }
                        ],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done."}],
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/v1/jobs/job-1/logs", headers=_auth())
    raw_response = client.get("/v1/jobs/job-1/logs?mode=raw", headers=_auth())
    session_response = client.get("/v1/jobs/job-1/logs?mode=session", headers=_auth())

    assert response.status_code == 200
    assert response.text == (f"session id: {thread_id}\nI am checking the repo now.\nDone.\n")
    assert raw_response.status_code == 200
    assert raw_response.text == f"session id: {thread_id}\n"
    assert session_response.status_code == 200
    assert session_response.text == "I am checking the repo now.\nDone.\n"
    assert response.text.count("I am checking the repo now.") == 1
    log_path = tmp_path / "worker-root" / "sessions" / "session-1" / "logs" / "job-1.log"
    assert log_path.read_text(encoding="utf-8") == f"session id: {thread_id}\n"


def test_execution_logs_skip_codex_no_response_requested_artifact(tmp_path: Path) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\nlatest raw output\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["true"],
        container_name="agent-worker-job-1",
        status="running",
        started_at=started_at,
        finished_at=None,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": (started_at + timedelta(seconds=10)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "No response requested."},
                },
                {
                    "timestamp": (started_at + timedelta(seconds=20)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "real session status"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = daemon.get_logs(record, mode="execution")

    assert output == (f"session id: {thread_id}\nlatest raw output\nreal session status\n")


def test_execution_logs_read_legacy_agent_sandbox_codex_home(tmp_path: Path) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\nlatest raw output\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["true"],
        container_name="agent-worker-job-1",
        status="running",
        started_at=started_at,
        finished_at=None,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    session_dir = (
        paths.agent_home / ".config" / "agent-sandbox" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": (started_at + timedelta(seconds=20)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "legacy home status"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = daemon.get_logs(record, mode="execution")

    assert output == (f"session id: {thread_id}\nlatest raw output\nlegacy home status\n")


def test_completed_codex_job_allows_successful_chat_reply_with_empty_final_message(
    tmp_path: Path,
) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    finished_at = started_at + timedelta(minutes=1)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["bash", "-lc", "codex exec --json prompt | evander_codex_json_to_log"],
        container_name="agent-worker-job-1",
        status="exited",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
        metadata={"agent_provider": "codex", "model_provider_mode": "openai_public"},
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": (started_at + timedelta(seconds=40)).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "mcp_tool_call_end",
                        "invocation": {
                            "server": "evander",
                            "tool": "team_reply_to_chat",
                        },
                        "result": {
                            "Ok": {
                                "structuredContent": {
                                    "success": True,
                                    "applied": True,
                                    "chat_message_id": "chatmsg-reply",
                                },
                                "isError": False,
                            }
                        },
                    },
                },
                {
                    "timestamp": (started_at + timedelta(seconds=50)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": ""},
                },
                {
                    "timestamp": (started_at + timedelta(seconds=55)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": None},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    inspected = daemon.inspect_job(record)

    assert "codex_outcome" not in inspected.metadata
    assert inspected.error is None
    assert daemon.get_logs(inspected, mode="execution") == f"session id: {thread_id}\n"


def test_completed_codex_job_records_missing_final_message_as_failure(tmp_path: Path) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    finished_at = started_at + timedelta(minutes=1)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["bash", "-lc", "codex exec --json prompt | evander_codex_json_to_log"],
        container_name="agent-worker-job-1",
        status="exited",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
        metadata={"agent_provider": "codex", "model_provider_mode": "local_openai"},
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": (started_at + timedelta(seconds=50)).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "last_token_usage": {
                            "input_tokens": 63657,
                            "output_tokens": 343,
                            "total_tokens": 64000,
                        },
                        "model_context_window": 258400,
                    },
                },
                {
                    "timestamp": (started_at + timedelta(seconds=55)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": None},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    inspected = daemon.inspect_job(record)

    outcome = inspected.metadata["codex_outcome"]
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "codex_context_limit_suspected"
    assert outcome["provider_kind"] == "local_model"
    assert outcome["last_token_usage"]["total_tokens"] == 64000
    assert inspected.error == (
        "Codex ended without a final agent message after local model token usage "
        "reached 64000 tokens."
    )
    assert daemon.get_logs(inspected, mode="execution") == (
        f"session id: {thread_id}\n"
        "codex failed: task_complete did not include a final agent message; "
        "context limit suspected (last_total_tokens=64000)\n"
    )


def test_completed_codex_job_records_session_meta_only_as_failure(tmp_path: Path) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    finished_at = started_at + timedelta(minutes=1)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["bash", "-lc", "codex exec --json prompt | evander_codex_json_to_log"],
        container_name="agent-worker-job-1",
        status="exited",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
        metadata={"agent_provider": "codex"},
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "timestamp": (started_at + timedelta(seconds=5)).isoformat(),
                "type": "session_meta",
                "payload": {"id": thread_id},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    inspected = daemon.inspect_job(record)

    outcome = inspected.metadata["codex_outcome"]
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "codex_no_final_message"
    assert inspected.error == "Codex ended without a final agent message."
    assert daemon.get_logs(inspected, mode="execution") == (
        f"session id: {thread_id}\n"
        "codex failed: task_complete did not include a final agent message\n"
    )


def test_completed_codex_job_prefers_unsupported_tool_failure_over_context_retry(
    tmp_path: Path,
) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    finished_at = started_at + timedelta(minutes=1)
    raw_log = (
        f"session id: {thread_id}\n"
        "2026-05-05T08:19:10Z ERROR codex_core::tools::router: "
        "error=unsupported call: evander:team_get_task\n"
        "2026-05-05T08:19:11Z ERROR codex_core::tools::router: "
        "error=unsupported call: evander:team_get_task\n"
    )
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = raw_log
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["bash", "-lc", "codex exec --json prompt | evander_codex_json_to_log"],
        container_name="agent-worker-job-1",
        status="exited",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
        metadata={"agent_provider": "codex", "model_provider_mode": "local_openai"},
    )
    log_path = paths.logs / "job-1.log"
    assert not log_path.exists()
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": (started_at + timedelta(seconds=50)).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "last_token_usage": {
                            "input_tokens": 63657,
                            "output_tokens": 343,
                            "total_tokens": 64000,
                        },
                        "model_context_window": 258400,
                    },
                },
                {
                    "timestamp": (started_at + timedelta(seconds=55)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": None},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    inspected = daemon.inspect_job(record)

    assert log_path.read_text(encoding="utf-8") == raw_log
    outcome = inspected.metadata["codex_outcome"]
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "codex_unsupported_tool_call"
    assert outcome["unsupported_tool_calls"] == ["evander:team_get_task"]
    assert outcome["last_token_usage"]["total_tokens"] == 64000
    assert inspected.error == (
        "Codex ended without a final agent message after emitting unsupported tool calls: "
        "evander:team_get_task."
    )
    execution_log = daemon.get_logs(inspected, mode="execution")
    assert "codex_unsupported_tool_call" not in execution_log
    assert "Codex ended without a final agent message after emitting unsupported tool calls" in (
        execution_log
    )
    assert "context limit suspected" not in execution_log


def test_completed_codex_job_allows_null_task_complete_after_assistant_text(
    tmp_path: Path,
) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    finished_at = started_at + timedelta(minutes=1)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["bash", "-lc", "codex exec --json prompt | evander_codex_json_to_log"],
        container_name="agent-worker-job-1",
        status="exited",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
        metadata={"agent_provider": "codex", "model_provider_mode": "local_openai"},
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": (started_at + timedelta(seconds=40)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "Done."},
                },
                {
                    "timestamp": (started_at + timedelta(seconds=55)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": None},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    inspected = daemon.inspect_job(record)

    assert "codex_outcome" not in inspected.metadata
    assert inspected.error is None
    assert daemon.get_logs(inspected, mode="execution") == (f"session id: {thread_id}\nDone.\n")


def test_execution_logs_limit_resumed_thread_to_job_window(tmp_path: Path) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    finished_at = started_at + timedelta(minutes=1)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["true"],
        container_name="agent-worker-job-1",
        status="removed",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "timestamp": (started_at - timedelta(minutes=1)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "old planner message"},
                },
                {
                    "timestamp": (started_at + timedelta(seconds=10)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "message during this job"},
                },
                {
                    "timestamp": (finished_at + timedelta(minutes=1)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "message after this job"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert daemon.get_logs(record, mode="execution") == (
        f"session id: {thread_id}\nmessage during this job\n"
    )


def test_logs_recover_claude_messages_from_project_jsonl(tmp_path: Path) -> None:
    thread_id = "9c50cf46-1c43-4119-befd-23582d367152"
    started_at = datetime(2026, 6, 8, 4, 26, 3, tzinfo=UTC)
    finished_at = started_at + timedelta(seconds=6)
    runtime = FakeContainerRuntime()
    runtime.logs["job-claude"] = f"session id: {thread_id}\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "claude-test-agent")
    record = WorkerJobRecord(
        job_id="job-claude",
        session_id="claude-test-agent",
        image="evander-runner-claude:latest",
        command=["true"],
        container_name="agent-worker-job-claude",
        status="removed",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    session_dir = paths.agent_home / ".claude" / "projects" / "-home-agent"
    session_dir.mkdir(parents=True)
    (session_dir / f"{thread_id}.jsonl").write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "assistant",
                    "sessionId": thread_id,
                    "timestamp": (started_at - timedelta(seconds=1)).isoformat(),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "old Claude message"}],
                    },
                },
                {
                    "type": "assistant",
                    "sessionId": thread_id,
                    "timestamp": (started_at + timedelta(seconds=2)).isoformat(),
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "private chain of thought"},
                            {"type": "text", "text": "Claude response during this job."},
                        ],
                    },
                },
                {
                    "type": "assistant",
                    "sessionId": thread_id,
                    "timestamp": (started_at + timedelta(seconds=3)).isoformat(),
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "mcp__evander__teams_get_messages",
                                "input": {"limit": 5},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "sessionId": thread_id,
                    "timestamp": (started_at + timedelta(seconds=4)).isoformat(),
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "content": "tool output"}],
                    },
                },
                {
                    "type": "assistant",
                    "sessionId": thread_id,
                    "timestamp": (finished_at + timedelta(seconds=1)).isoformat(),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "later Claude message"}],
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    codex_session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "06" / "08"
    )
    codex_session_dir.mkdir(parents=True)
    (codex_session_dir / f"rollout-2026-06-08T04-26-03-{thread_id}.jsonl").write_text(
        json.dumps(
            {
                "type": "event_msg",
                "timestamp": (started_at + timedelta(seconds=2)).isoformat(),
                "payload": {
                    "type": "agent_message",
                    "message": "Codex transcript should not be used first.",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert daemon.get_logs(record, mode="session") == (
        "old Claude message\nClaude response during this job.\nlater Claude message\n"
    )
    assert daemon.get_logs(record, mode="execution") == (
        f"session id: {thread_id}\nClaude response during this job.\n"
    )


def test_execution_logs_skip_claude_no_response_requested_artifact(tmp_path: Path) -> None:
    thread_id = "9c50cf46-1c43-4119-befd-23582d367152"
    started_at = datetime(2026, 6, 8, 4, 26, 3, tzinfo=UTC)
    runtime = FakeContainerRuntime()
    runtime.logs["job-claude"] = f"session id: {thread_id}\nlatest raw output\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "claude-test-agent")
    record = WorkerJobRecord(
        job_id="job-claude",
        session_id="claude-test-agent",
        image="evander-runner-claude:latest",
        command=["true"],
        container_name="agent-worker-job-claude",
        status="running",
        started_at=started_at,
        finished_at=None,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    session_dir = paths.agent_home / ".claude" / "projects" / "-home-agent"
    session_dir.mkdir(parents=True)
    (session_dir / f"{thread_id}.jsonl").write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "assistant",
                    "sessionId": thread_id,
                    "timestamp": (started_at + timedelta(seconds=1)).isoformat(),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "No response requested."}],
                    },
                },
                {
                    "type": "assistant",
                    "sessionId": thread_id,
                    "timestamp": (started_at + timedelta(seconds=2)).isoformat(),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "real Claude status"}],
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = daemon.get_logs(record, mode="execution")

    assert output == (f"session id: {thread_id}\nlatest raw output\nreal Claude status\n")


def test_logs_recover_live_codex_messages_without_thread_id_in_container_output(
    tmp_path: Path,
) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = ""
    client = _client(tmp_path, runtime)

    created = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    assert created.status_code == 201
    session_dir = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / ".config"
        / "evander"
        / "codex"
        / "sessions"
        / "2026"
        / "05"
        / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": "2026-05-05T08:19:10Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": "I am reading the repo.",
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/v1/jobs/job-1/logs?limit=10", headers=_auth())

    assert response.status_code == 200
    assert response.text == "I am reading the repo.\n"


def test_logs_show_active_codex_exec_command_from_session_jsonl(tmp_path: Path) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    client = _client(tmp_path, runtime)

    created = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
        },
    )
    assert created.status_code == 201
    session_dir = (
        tmp_path
        / "worker-root"
        / "sessions"
        / "session-1"
        / "agent_home"
        / ".config"
        / "evander"
        / "codex"
        / "sessions"
        / "2026"
        / "05"
        / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": "2026-05-05T08:19:10Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": "I am running the bounded test command.",
                    },
                },
                {
                    "timestamp": "2026-05-05T08:19:20Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "call-active",
                        "arguments": json.dumps(
                            {
                                "cmd": "timeout --preserve-status 120 dotnet test --no-restore",
                                "workdir": "/home/agent/repos/repo",
                            }
                        ),
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/v1/jobs/job-1/logs", headers=_auth())

    assert response.status_code == 200
    assert f"session id: {thread_id}\n" in response.text
    assert "I am running the bounded test command.\n" in response.text
    assert "latest codex event: 2026-05-05T08:19:20Z\n" in response.text
    assert (
        "active tool call: exec_command timeout --preserve-status 120 dotnet test --no-restore "
        "cwd=/home/agent/repos/repo\n"
    ) in response.text


def test_execution_logs_show_bounded_codex_reasoning_and_completed_tools(
    tmp_path: Path,
) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    runtime = FakeContainerRuntime()
    runtime.logs["job-1"] = f"session id: {thread_id}\n"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime,
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["true"],
        container_name="agent-worker-job-1",
        status="running",
        started_at=started_at,
        finished_at=None,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": thread_id}},
                {
                    "timestamp": (started_at - timedelta(seconds=1)).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": "agent_reasoning", "text": "old reasoning"},
                },
                {
                    "timestamp": (started_at + timedelta(seconds=1)).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_reasoning",
                        "text": "Inspecting the existing patterns before editing.",
                    },
                },
                {
                    "timestamp": (started_at + timedelta(seconds=2)).isoformat(),
                    "type": "response_item",
                    "payload": {
                        "type": "reasoning",
                        "summary": [
                            {
                                "type": "summary_text",
                                "text": "Inspecting the existing patterns before editing.",
                            }
                        ],
                    },
                },
                {
                    "timestamp": (started_at + timedelta(seconds=3)).isoformat(),
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "team_get_task",
                        "call_id": "call-team",
                        "arguments": json.dumps({"task_id": "sensitive-structured-value"}),
                    },
                },
                {
                    "timestamp": (started_at + timedelta(seconds=4)).isoformat(),
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "call-team",
                        "output": "large sensitive tool output",
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = daemon.get_logs(record, mode="execution")

    assert output == (
        f"session id: {thread_id}\n"
        "reasoning: Inspecting the existing patterns before editing.\n"
        "tool started: team_get_task\n"
        "tool completed: team_get_task\n"
    )
    assert "old reasoning" not in output
    assert "sensitive-structured-value" not in output
    assert "large sensitive tool output" not in output


def test_raw_logs_reconstruct_removed_codex_job_from_session_timestamps(
    tmp_path: Path,
) -> None:
    thread_id = "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    started_at = datetime(2026, 5, 5, 8, 19, tzinfo=UTC)
    finished_at = started_at + timedelta(minutes=1)

    class MissingContainerRuntime(FakeContainerRuntime):
        def get_logs(self, record: WorkerJobRecord) -> str:
            raise subprocess.CalledProcessError(1, ["docker", "logs", record.job_id])

    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=MissingContainerRuntime(),
        token="test-token",
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["true"],
        container_name="agent-worker-job-1",
        status="removed",
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )
    log_path = paths.logs / "job-1.log"
    log_path.write_text(
        f"session id: {thread_id}\nmessage during this job\nmessage after this job\n",
        encoding="utf-8",
    )
    session_dir = (
        paths.agent_home / ".config" / "evander" / "codex" / "sessions" / "2026" / "05" / "05"
    )
    session_dir.mkdir(parents=True)
    rollout = session_dir / f"rollout-2026-05-05T08-19-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "timestamp": (started_at + timedelta(seconds=10)).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": "message during this job",
                    },
                },
                {
                    "timestamp": (finished_at + timedelta(minutes=1)).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": "message after this job",
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert daemon.get_logs(record, mode="raw") == (
        f"session id: {thread_id}\nmessage during this job\n"
    )
    assert log_path.read_text(encoding="utf-8") == (
        f"session id: {thread_id}\nmessage during this job\n"
    )


def test_job_store_omits_secret_fields_and_rejects_raw_environment(tmp_path: Path) -> None:
    client = _client(tmp_path)

    rejected = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-env",
            "image": "codex:latest",
            "command": ["true"],
            "env": {"OPENAI_API_KEY": "secret"},
        },
    )
    assert rejected.status_code == 400

    response = client.post(
        "/v1/jobs",
        headers=_auth(),
        json={
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["true"],
            "metadata": {
                "purpose": "test",
                "authorization": "Bearer secret",
                "nested": {"api_key": "secret"},
            },
        },
    )

    assert response.status_code == 201
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.json")
    record = store.get("job-1")
    assert record is not None
    assert record.metadata == {
        "authorization": "[redacted]",
        "nested": {"api_key": "[redacted]"},
        "purpose": "test",
    }

    db_path = tmp_path / "runtime" / "worker-jobs.db"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM worker_jobs WHERE job_id = ?",
            ("job-1",),
        ).fetchone()

    assert row is not None
    assert "Bearer secret" not in row[0]
    assert "OPENAI_API_KEY" not in row[0]
