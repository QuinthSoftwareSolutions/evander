from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from agent_sandbox.capacity_health import HealthState, TelemetryStatus
from agent_sandbox.worker.service import WorkerDaemon
from agent_sandbox.worker_daemon import (
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerSessionPaths,
    create_app,
)


class FakeContainerRuntime:
    def __init__(self) -> None:
        self.started: list[tuple[WorkerJobRequest, WorkerJobRecord, WorkerSessionPaths]] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []
        self.logs: dict[str, str] = {}

    def start_job(
        self,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> str:
        self.started.append((request, record, paths))
        return f"container-{record.job_id}"

    def inspect_job(self, record: WorkerJobRecord) -> dict[str, Any]:
        return {"status": record.status}

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

    telemetry = daemon.read_telemetry()
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
    assert (
        root / "agent_home" / "repos" / "Repo" / "AGENT_TASK.yaml"
    ).read_text(encoding="utf-8") == "task: test\n"


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


def test_session_prepare_copies_restricted_git_ssh_into_agent_home(tmp_path: Path) -> None:
    ssh_source = tmp_path / "worker-ssh"
    ssh_source.mkdir()
    (ssh_source / "agent-sandbox-git").write_text("private-key\n", encoding="utf-8")
    (ssh_source / "agent-sandbox-git.pub").write_text("public-key\n", encoding="utf-8")
    (ssh_source / "config").write_text("Host agent-sandbox-git-host\n", encoding="utf-8")
    (ssh_source / "known_hosts").write_text("known-host\n", encoding="utf-8")
    client = _client(tmp_path, git_ssh_source_dir=ssh_source)

    response = client.post(
        "/v1/sessions",
        headers=_auth(),
        json={"session_id": "session-1"},
    )

    assert response.status_code == 201
    target = tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / ".ssh"
    assert (target / "agent-sandbox-git").read_text(encoding="utf-8") == "private-key\n"
    assert (target / "config").read_text(encoding="utf-8") == "Host agent-sandbox-git-host\n"
    assert oct((target / "agent-sandbox-git").stat().st_mode & 0o777) == "0o600"
    assert oct((target / "agent-sandbox-git.pub").stat().st_mode & 0o777) == "0o644"


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
    store = json.loads((tmp_path / "runtime" / "worker-jobs.json").read_text(encoding="utf-8"))
    assert store["job-1"]["status"] == "removed"
    assert store["job-1"]["cleanup_status"] == "cleanup_succeeded"


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

    monkeypatch.setattr("agent_sandbox.worker.service.shutil.rmtree", _raise)

    deleted = client.delete("/v1/sessions/session-1", headers=_auth())

    assert deleted.status_code == 502
    assert runtime.removed == ["job-1"]
    assert root.exists()
    job_response = client.get("/v1/jobs/job-1", headers=_auth())
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "removed"
    assert job_response.json()["cleanup_status"] == "cleanup_failed"
    assert "permission denied" in job_response.json()["cleanup_error"]
    store = json.loads((tmp_path / "runtime" / "worker-jobs.json").read_text(encoding="utf-8"))
    assert store["job-1"]["status"] == "removed"
    assert store["job-1"]["cleanup_status"] == "cleanup_failed"


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
    repo_root = (
        tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home" / "repos"
    )
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


def test_session_repo_clone_rejects_unsafe_repo_name(tmp_path: Path) -> None:
    source = tmp_path / "source-repo"
    source.mkdir()
    client = _client(tmp_path, allowed_git_source_prefixes=[str(source)])

    response = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": str(source), "repo_name": "../escape"},
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


def test_session_repo_clone_rejects_sources_outside_allowed_prefix(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        allowed_git_source_prefixes=["ssh://agent-sandbox-git-host/opt/git/"],
    )

    response = client.post(
        "/v1/sessions/session-1/repo",
        headers=_auth(),
        json={"repo_source": "https://github.com/example/repo.git"},
    )

    assert response.status_code == 400
    assert "repo_source is not allowed" in response.json()["error"]


def test_job_repo_clone_enforces_allowed_prefix(tmp_path: Path) -> None:
    runtime = FakeContainerRuntime()
    client = _client(
        tmp_path,
        runtime,
        allowed_git_source_prefixes=["ssh://agent-sandbox-git-host/opt/git/"],
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
    store_path.write_text(
        json.dumps(
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
                    "logs_path": str(
                        tmp_path / "worker-root" / "sessions" / "legacy-session" / "logs"
                    ),
                    "metadata": {},
                }
            }
        ),
        encoding="utf-8",
    )
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
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["legacy-job"]["repo_root_path"] == str(legacy_workspace)
    assert "workspace_path" not in payload["legacy-job"]


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

    stopped = client.post("/v1/jobs/job-1/stop", headers=_auth())
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert runtime.stopped == ["job-1"]

    deleted = client.delete("/v1/jobs/job-1", headers=_auth())
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "job_id": "job-1"}
    assert runtime.removed == ["job-1"]


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
    assert response.headers["x-agent-sandbox-log-paged"] == "true"
    assert response.headers["x-agent-sandbox-log-returned-lines"] == "2"
    assert response.headers["x-agent-sandbox-log-has-more-before"] == "true"


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
        / "agent-sandbox"
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
    assert response.text == (
        f"session id: {thread_id}\n"
        "I am checking the repo now.\n"
        "Done.\n"
    )
    assert raw_response.status_code == 200
    assert raw_response.text == f"session id: {thread_id}\n"
    assert session_response.status_code == 200
    assert session_response.text == "I am checking the repo now.\nDone.\n"
    assert response.text.count("I am checking the repo now.") == 1
    log_path = tmp_path / "worker-root" / "sessions" / "session-1" / "logs" / "job-1.log"
    assert log_path.read_text(encoding="utf-8") == f"session id: {thread_id}\n"


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
        / "agent-sandbox"
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
        / "agent-sandbox"
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
        f"session id: {thread_id}\n"
        "message during this job\n"
        "message after this job\n",
        encoding="utf-8",
    )
    session_dir = (
        paths.agent_home
        / ".config"
        / "agent-sandbox"
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
        f"session id: {thread_id}\n"
        "message during this job\n"
    )
    assert log_path.read_text(encoding="utf-8") == (
        f"session id: {thread_id}\n"
        "message during this job\n"
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
    store_text = (tmp_path / "runtime" / "worker-jobs.json").read_text(encoding="utf-8")
    assert "Bearer secret" not in store_text
    assert "OPENAI_API_KEY" not in store_text
    payload = json.loads(store_text)
    assert payload["job-1"]["metadata"] == {
        "authorization": "[redacted]",
        "nested": {"api_key": "[redacted]"},
        "purpose": "test",
    }
