from __future__ import annotations

import json

import httpx
import pytest

from evander.capacity_health import TelemetryStatus
from evander.execution.container_worker_client import (
    CodexSubscriptionDeferredClientError,
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.worker.models import WorkerJobRequest
from evander.worker.models import (
    WorkerFileWriteRequest,
    WorkerMssqlDatabaseRequest,
    WorkerRepoCloneRequest,
    WorkerReferenceRepoPrepareRequest,
    WorkerSessionPrepareRequest,
    WorkerTaskWorktreeCleanupRequest,
    WorkerTaskWorktreeRequest,
    WorkerTelemetryPayload,
)


def _job_payload(status: str = "running") -> dict[str, object]:
    return {
        "job_id": "job-1",
        "session_id": "session-1",
        "image": "runner:latest",
        "command": ["true"],
        "container_name": "agent-worker-job-1",
        "status": status,
        "agent_home_path": "/worker/session-1/agent_home",
        "repo_root_path": "/worker/session-1/agent_home/repos",
        "artifacts_path": "/worker/session-1/artifacts",
        "logs_path": "/worker/session-1/logs",
        "metadata": {},
    }


def test_bounded_artifact_reader_sends_limit_and_stops_oversized_stream(monkeypatch):
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    chunks = []

    class OversizedResponse(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(100):
                chunks.append(index)
                yield b"x" * 65536

    def handler(request):
        assert request.url.params["max_bytes"] == "5"
        return httpx.Response(200, stream=OversizedResponse(), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        worker = ContainerWorkerClient(
            base_url="http://worker.test", token_env="WORKER_TOKEN", client=client,
        )
        with pytest.raises(ValueError, match="exceeds import limit"):
            worker.read_session_artifact_text_bounded("session", "report.txt", max_bytes=5)
    assert chunks == [0]


def test_container_worker_client_uses_auth_header_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json=_job_payload(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=12.5)
    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        timeout_seconds=12.5,
        client=client,
    )

    record = worker.create(
        WorkerJobRequest(
            session_id="session-1", job_id="job-1", image="runner:latest", command=["true"]
        )
    )

    assert record.job_id == "job-1"
    assert requests[0].headers["authorization"] == "Bearer secret-token"
    assert client.timeout == httpx.Timeout(12.5)


def test_container_worker_client_health_create_get_stop_delete_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query, "ascii")))
        if request.url.path == "/healthz":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"status": "ok"}, request=request)
        assert request.headers["authorization"] == "Bearer secret-token"
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, text="hello\n", request=request)
        if request.method == "DELETE":
            return httpx.Response(200, json={"deleted": True, "job_id": "job-1"}, request=request)
        return httpx.Response(
            200 if request.method != "POST" or request.url.path.endswith("/stop") else 201,
            json=_job_payload(),
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test/",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert worker.health() == {"status": "ok"}
    assert (
        worker.create(
            WorkerJobRequest(
                session_id="session-1", job_id="job-1", image="runner:latest", command=["true"]
            )
        ).job_id
        == "job-1"
    )
    assert worker.get("job-1").status == "running"
    assert worker.stop("job-1").status == "running"
    assert worker.delete("job-1") == {"deleted": True, "job_id": "job-1"}
    assert worker.logs("job-1") == "hello\n"
    assert worker.logs("job-1", mode="raw") == "hello\n"
    assert worker.logs("job-1", mode="session", limit=25, offset=50, query="needle") == "hello\n"
    assert seen == [
        ("GET", "/healthz", ""),
        ("POST", "/v1/jobs", ""),
        ("GET", "/v1/jobs/job-1", ""),
        ("POST", "/v1/jobs/job-1/stop", ""),
        ("DELETE", "/v1/jobs/job-1", ""),
        ("GET", "/v1/jobs/job-1/logs", ""),
        ("GET", "/v1/jobs/job-1/logs", "mode=raw"),
        ("GET", "/v1/jobs/job-1/logs", "mode=session&limit=25&offset=50&query=needle"),
    ]


def test_container_worker_client_codex_auth_routes_use_worker_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer secret-token"
        if request.url.path == "/v1/codex-auth/profiles":
            if request.method == "GET":
                return httpx.Response(200, json={"profiles": []}, request=request)
            return httpx.Response(201, json={"profile_id": "team-a"}, request=request)
        if request.url.path.endswith("/device-login") and request.method == "POST":
            payload: dict[str, object] = {"login_id": "login/1", "status": "pending"}
            return httpx.Response(201, json=payload, request=request)
        if "/device-login/" in request.url.path:
            return httpx.Response(
                200,
                json={"login_id": "login/1", "status": "pending"},
                request=request,
            )
        if "/profiles/" in request.url.path:
            return httpx.Response(
                200,
                json={"profile_id": "team-a", "signed_in": False},
                request=request,
            )
        return httpx.Response(200, json={"signed_in": False}, request=request)

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert worker.codex_auth_status() == {"signed_in": False}
    assert worker.start_codex_device_login()["login_id"] == "login/1"
    assert worker.codex_device_login_status("login/1")["status"] == "pending"
    assert worker.cancel_codex_device_login("login/1")["status"] == "pending"
    assert worker.logout_codex_auth() == {"signed_in": False}
    assert worker.list_codex_auth_profiles() == {"profiles": []}
    assert (
        worker.create_codex_auth_profile({"profile_id": "team-a", "display_name": "Team A"})[
            "profile_id"
        ]
        == "team-a"
    )
    assert worker.codex_auth_profile_status("team-a")["profile_id"] == "team-a"
    assert worker.patch_codex_auth_profile("team-a", {"enabled": False})["profile_id"] == "team-a"
    assert worker.start_codex_profile_device_login("team-a")["login_id"] == "login/1"
    assert worker.codex_profile_device_login_status("team-a", "login/1")["status"] == "pending"
    assert worker.cancel_codex_profile_device_login("team-a", "login/1")["status"] == "pending"
    assert worker.logout_codex_auth_profile("team-a")["signed_in"] is False
    assert seen == [
        ("GET", "/v1/codex-auth"),
        ("POST", "/v1/codex-auth/device-login"),
        ("GET", "/v1/codex-auth/device-login/login/1"),
        ("DELETE", "/v1/codex-auth/device-login/login/1"),
        ("DELETE", "/v1/codex-auth"),
        ("GET", "/v1/codex-auth/profiles"),
        ("POST", "/v1/codex-auth/profiles"),
        ("GET", "/v1/codex-auth/profiles/team-a"),
        ("PATCH", "/v1/codex-auth/profiles/team-a"),
        ("POST", "/v1/codex-auth/profiles/team-a/device-login"),
        ("GET", "/v1/codex-auth/profiles/team-a/device-login/login/1"),
        ("DELETE", "/v1/codex-auth/profiles/team-a/device-login/login/1"),
        ("DELETE", "/v1/codex-auth/profiles/team-a/session"),
    ]


def test_container_worker_client_surfaces_structured_subscription_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": "subscription capacity unavailable",
                "deferral": {
                    "profile_id": "team-a",
                    "reason": "rate_limited",
                    "retry_at": "2026-08-01T12:30:00Z",
                },
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        max_connect_attempts=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(CodexSubscriptionDeferredClientError) as exc_info:
        worker.create(
            WorkerJobRequest(
                session_id="session-1",
                job_id="job-1",
                image="runner:latest",
                command=["true"],
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.deferral.profile_id == "team-a"
    assert exc_info.value.deferral.reason == "rate_limited"
    assert exc_info.value.deferral.retry_at.isoformat() == "2026-08-01T12:30:00+00:00"


def test_container_worker_client_telemetry_uses_auth_and_parses_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query, "ascii")))
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            json={
                "generated_at": "2026-05-21T12:00:00Z",
                "disk": {
                    "worker_root": {
                        "scope": "worker_root",
                        "status": "available",
                        "health": "ok",
                        "total_bytes": 100,
                        "used_bytes": 40,
                        "free_bytes": 60,
                        "used_percent": 40.0,
                        "free_percent": 60.0,
                        "error_count": 0,
                        "errors": [],
                    }
                },
                "session_storage": {
                    "total": {
                        "scope": "worker_sessions",
                        "status": "available",
                        "health": "ok",
                        "size_bytes": 2048,
                        "file_count": 2,
                        "directory_count": 1,
                        "symlink_count": 0,
                        "error_count": 0,
                        "errors": [],
                    },
                    "session_count": 1,
                    "largest_sessions": [
                        {
                            "session_id": "session-1",
                            "directory_path": "sessions/session-1",
                            "backend": "container_worker_vm",
                            "size_bytes": 2048,
                            "created_at": "2026-05-20T09:00:00Z",
                            "updated_at": "2026-05-21T09:00:00Z",
                        }
                    ],
                    "oldest_sessions": [
                        {
                            "session_id": "session-1",
                            "directory_path": "sessions/session-1",
                            "backend": "container_worker_vm",
                            "size_bytes": 2048,
                            "created_at": "2026-05-20T09:00:00Z",
                            "updated_at": "2026-05-21T09:00:00Z",
                        }
                    ],
                },
                "jobs": {
                    "status": "available",
                    "health": "ok",
                    "total_jobs": 3,
                    "counts_by_status": {"running": 1, "failed": 1, "stopped": 1},
                    "running_job_count": 1,
                    "failed_start_count": 1,
                    "cleanup_failure_status": "unsupported",
                    "cleanup_failure_message": "cleanup failures are not currently persisted",
                    "error_count": 0,
                    "errors": [],
                },
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    telemetry = worker.telemetry()
    refreshed = worker.telemetry(refresh=True)

    assert isinstance(telemetry, WorkerTelemetryPayload)
    assert isinstance(refreshed, WorkerTelemetryPayload)
    assert telemetry.jobs.failed_start_count == 1
    assert telemetry.jobs.queued_job_count == 0
    assert telemetry.jobs.queued_job_status is TelemetryStatus.UNSUPPORTED
    assert telemetry.jobs.cleanup_failure_status is TelemetryStatus.UNSUPPORTED
    assert telemetry.session_storage.session_count == 1
    assert telemetry.session_storage.largest_sessions[0].directory_path == "sessions/session-1"
    assert seen == [
        ("GET", "/v1/telemetry", ""),
        ("GET", "/v1/telemetry", "refresh=1"),
    ]


def test_container_worker_client_prepares_session_clones_repo_and_writes_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    seen: list[tuple[str, str]] = []
    reference_repo_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer secret-token"
        if request.url.path == "/v1/sessions":
            return httpx.Response(
                201,
                json={
                    "session_id": "session-1",
                    "root_path": "/worker/session-1",
                    "agent_home_path": "/worker/session-1/agent_home",
                    "repo_root_path": "/worker/session-1/agent_home/repos",
                    "artifacts_path": "/worker/session-1/artifacts",
                    "logs_path": "/worker/session-1/logs",
                    "metadata_path": "/worker/session-1/metadata.json",
                },
                request=request,
            )
        if request.url.path == "/v1/sessions/session-1/repo":
            return httpx.Response(
                200,
                json={
                    "session_id": "session-1",
                    "target_dir": "/home/agent/repos/repo",
                    "guest_repo_name": "repo",
                    "remote_url": "/tmp/repo",
                    "cloned": True,
                },
                request=request,
            )
        if request.url.path.endswith("/reference-repo"):
            reference_repo_payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "session_id": "session-1",
                    "target_dir": "/home/agent/repos/repo",
                    "guest_repo_name": "repo",
                    "remote_url": "/tmp/repo",
                    "source_ref": "refs/heads/main",
                    "commit_sha": "a" * 40,
                    "cloned": False,
                    "checkout_updated": True,
                    "configuration_updated": False,
                },
                request=request,
            )
        if request.method == "DELETE":
            return httpx.Response(
                200,
                json={"deleted": True, "session_id": "session-1"},
                request=request,
            )
        if request.method == "GET" and request.url.path.endswith("/artifacts/reports/summary.txt"):
            return httpx.Response(200, text="daily summary\n", request=request)
        return httpx.Response(
            200,
            json={
                "session_id": "session-1",
                "copied_count": 1,
                "copied_paths": ["/home/agent/repos/repo/AGENT_TASK.yaml"],
                "roots": {"repo_root": "/home/agent/repos"},
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert (
        worker.prepare_session(
            WorkerSessionPrepareRequest(session_id="session-1", metadata={"purpose": "test"})
        ).repo_root_container_path
        == "/home/agent/repos"
    )
    assert (
        worker.clone_repo(
            "session-1", WorkerRepoCloneRequest(repo_source="/tmp/repo", repo_name="repo")
        ).target_dir
        == "/home/agent/repos/repo"
    )
    assert worker.prepare_reference_repo(
        "session-1",
        WorkerReferenceRepoPrepareRequest(
            repo_source="/tmp/repo",
            repo_name="repo",
            source_ref="refs/heads/main",
            commit_sha="a" * 40,
            configuration_version="1",
            update_existing=False,
        ),
    ).checkout_updated
    assert (
        worker.write_files(
            "session-1",
            WorkerFileWriteRequest(
                files=[
                    {
                        "destination_path": "/home/agent/repos/repo/AGENT_TASK.yaml",
                        "content": "x",
                    }
                ]
            ),
        ).copied_count
        == 1
    )
    assert worker.read_session_artifact_text("session-1", "reports/summary.txt") == (
        "daily summary\n"
    )
    assert worker.delete_session("session-1") == {"deleted": True, "session_id": "session-1"}
    assert reference_repo_payloads == [
        {
            "repo_source": "/tmp/repo",
            "repo_name": "repo",
            "source_ref": "refs/heads/main",
            "commit_sha": "a" * 40,
            "configuration_version": "1",
            "update_existing": False,
        }
    ]
    assert seen == [
        ("POST", "/v1/sessions"),
        ("POST", "/v1/sessions/session-1/repo"),
        ("POST", "/v1/sessions/session-1/reference-repo"),
        ("POST", "/v1/sessions/session-1/files"),
        ("GET", "/v1/sessions/session-1/artifacts/reports/summary.txt"),
        ("DELETE", "/v1/sessions/session-1"),
    ]


def test_container_worker_client_requests_structured_task_worktree_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    seen_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/sessions/session-1/worktrees/cleanup"
        seen_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "session_id": "session-1",
                "repo_name": "repo",
                "worktree_name": "task-a-1234",
                "target_dir": "/home/agent/worktrees/repo/task-a-1234",
                "branch": "agents/session-1/tasks/task-a-1234",
                "expected_commit_sha": "a" * 40,
                "actual_branch": "agents/session-1/tasks/task-a-1234",
                "actual_commit_sha": "a" * 40,
                "state": "dry_run",
                "safe_to_remove": True,
                "removed": False,
                "pruned": False,
                "dry_run": True,
                "validation_count": 1,
                "failed_check": None,
                "message": "task worktree is safe to remove",
                "tracked_paths": [],
                "untracked_paths": [],
                "ignored_paths": ["cache/generated.bin"],
                "ignored_bytes": 9,
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = WorkerTaskWorktreeCleanupRequest(
        repo_name="repo",
        worktree_name="task-a-1234",
        preparation_attempt_id="gitprep-task-a-1234",
        branch="agents/session-1/tasks/task-a-1234",
        expected_commit_sha="a" * 40,
        expected_target_dir="/home/agent/worktrees/repo/task-a-1234",
        dry_run=True,
    )

    result = worker.cleanup_task_worktree("session-1", request)

    assert result.state == "dry_run"
    assert result.ignored_bytes == 9
    assert seen_payloads == [request.model_dump(mode="json")]


def test_container_worker_client_provisions_lists_and_drops_mssql_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer secret-token"
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "session_id": "session-1",
                    "databases": [
                        {
                            "logical_name": "app",
                            "database_name": "asb_hash_app_hash",
                            "created_at": "2026-05-10T12:00:00Z",
                        }
                    ],
                },
                request=request,
            )
        if request.method == "DELETE":
            return httpx.Response(
                200,
                json={
                    "session_id": "session-1",
                    "logical_name": "app",
                    "database_name": "asb_hash_app_hash",
                    "deleted": True,
                },
                request=request,
            )
        return httpx.Response(
            201,
            json={
                "session_id": "session-1",
                "logical_name": "app",
                "database_name": "asb_hash_app_hash",
                "server": "172.17.0.1",
                "port": 1433,
                "username": "asb_login_hash",
                "password": "session-secret",
                "connection_string": "Server=tcp:172.17.0.1,1433;Password=session-secret",
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    provisioned = worker.provision_mssql_database(
        "session-1",
        WorkerMssqlDatabaseRequest(logical_name="app"),
    )
    listed = worker.list_mssql_databases("session-1")
    dropped = worker.drop_mssql_database("session-1", "app")

    assert provisioned.connection_string.startswith("Server=tcp:172.17.0.1")
    assert [item.logical_name for item in listed.databases] == ["app"]
    assert dropped.deleted is True
    assert seen == [
        ("POST", "/v1/sessions/session-1/mssql/databases"),
        ("GET", "/v1/sessions/session-1/mssql/databases"),
        ("DELETE", "/v1/sessions/session-1/mssql/databases/app"),
    ]


@pytest.mark.parametrize("exception_class", [httpx.ConnectError, httpx.ConnectTimeout])
def test_container_worker_client_retries_connect_failure_before_create_success(
    monkeypatch: pytest.MonkeyPatch,
    exception_class: type[httpx.HTTPError],
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    monkeypatch.setattr("evander.execution.container_worker_client.random.random", lambda: 0.5)
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise exception_class("connection refused", request=request)
        return httpx.Response(201, json=_job_payload(), request=request)

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )

    record = worker.create(
        WorkerJobRequest(
            session_id="session-1", job_id="job-1", image="runner:latest", command=["true"]
        )
    )

    assert record.job_id == "job-1"
    assert [request.url.path for request in requests] == ["/v1/jobs", "/v1/jobs"]
    assert sleeps == [1.0]


def test_container_worker_client_exhausts_connect_retries_with_capped_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    monkeypatch.setattr("evander.execution.container_worker_client.random.random", lambda: 0.5)
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("connection refused", request=request)

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )

    with pytest.raises(ContainerWorkerClientError) as exc:
        worker.create(
            WorkerJobRequest(
                session_id="session-1",
                job_id="job-1",
                image="runner:latest",
                command=["true"],
            )
        )

    assert len(requests) == 5
    assert sleeps == [1.0, 2.0, 4.0, 8.0]
    message = str(exc.value)
    assert "attempts=5/5" in message
    assert "connection refused" in message
    assert exc.value.payload["git_preparation_failure"]["cause"] == "transport_unavailable"


def test_container_worker_client_does_not_retry_http_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, text="worker unavailable", request=request)

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )

    with pytest.raises(ContainerWorkerClientError, match="HTTP 503"):
        worker.create(
            WorkerJobRequest(
                session_id="session-1",
                job_id="job-1",
                image="runner:latest",
                command=["true"],
            )
        )

    assert len(requests) == 1
    assert sleeps == []


def test_container_worker_client_preserves_structured_dependency_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    requests: list[httpx.Request] = []
    conflict = {
        "base_branch": "main",
        "base_commit_sha": "a" * 40,
        "base_tree_hash": "b" * 40,
        "dependency_git_sources": [],
        "failed_dependency_task_id": "source-task",
        "failed_deliverable_id": "source-deliverable",
        "conflict_paths": ["src/shared.py"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            409,
            json={
                "error": "dependency Git composition conflict",
                "dependency_composition_conflict": conflict,
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ContainerWorkerClientError, match="HTTP 409") as raised:
        worker.prepare_task_worktree(
            "session-1",
            WorkerTaskWorktreeRequest(
                repo_name="repo",
                worktree_name="task-a-1234",
                preparation_attempt_id="gitprep-task-a-1234",
                branch="agents/session-1/tasks/task-a-1234",
                base_ref="main",
            ),
        )

    assert raised.value.payload == {
        "error": "dependency Git composition conflict",
        "dependency_composition_conflict": conflict,
    }
    assert len(requests) == 1


def test_container_worker_client_retries_without_receipt_id_for_older_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    requests: list[dict[str, object]] = []
    base_commit = "a" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                409,
                json={
                    "error": (
                        "1 validation error for WorkerTaskWorktreeRequest\n"
                        "preparation_attempt_id\n"
                        "  Extra inputs are not permitted [type=extra_forbidden]"
                    )
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "session_id": "session-1",
                "repo_name": "repo",
                "target_dir": "/home/agent/worktrees/repo/task-a-1234",
                "branch": "agents/session-1/tasks/task-a-1234",
                "commit_sha": base_commit,
                "base_commit_sha": base_commit,
                "created": True,
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    record = worker.prepare_task_worktree(
        "session-1",
        WorkerTaskWorktreeRequest(
            repo_name="repo",
            worktree_name="task-a-1234",
            preparation_attempt_id="gitprep-task-a-1234",
            branch="agents/session-1/tasks/task-a-1234",
            base_ref="main",
        ),
    )

    assert len(requests) == 2
    assert requests[0]["preparation_attempt_id"] == "gitprep-task-a-1234"
    assert "preparation_attempt_id" not in requests[1]
    assert record.created is True
    assert record.preparation_attempt_id is None


@pytest.mark.parametrize("available", [False, True])
def test_recovery_never_downgrades_preparation_identity_for_older_worker(monkeypatch, available):
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json={"status": "ok", "capabilities": {"git_recovery": 1 if available else 0}})
        return httpx.Response(409, json={"error": "preparation_attempt_id Extra inputs are not permitted [type=extra_forbidden]"})
    worker = ContainerWorkerClient(base_url="http://worker.test", token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ContainerWorkerClientError):
        worker.prepare_task_worktree("session-1", WorkerTaskWorktreeRequest(repo_name="repo", worktree_name="recovery",
            branch="agents/session-1/recovery", base_ref="main", preparation_attempt_id="new-physical-attempt",
            required_git_recovery_protocol=1))
    assert len(paths) == (2 if available else 1)


def test_container_worker_client_cleanup_retries_without_receipt_id_for_older_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                400,
                json={
                    "error": (
                        "1 validation error for WorkerTaskWorktreeCleanupRequest\n"
                        "preparation_attempt_id\n"
                        "  Extra inputs are not permitted [type=extra_forbidden]"
                    )
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "session_id": "session-1",
                "repo_name": "repo",
                "worktree_name": "task-a-1234",
                "target_dir": "/home/agent/worktrees/repo/task-a-1234",
                "branch": "agents/session-1/tasks/task-a-1234",
                "expected_commit_sha": "a" * 40,
                "state": "removed",
                "safe_to_remove": True,
                "removed": True,
                "pruned": True,
                "dry_run": False,
                "validation_count": 2,
                "message": "task worktree removed and registration pruned",
            },
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    record = worker.cleanup_task_worktree(
        "session-1",
        WorkerTaskWorktreeCleanupRequest(
            repo_name="repo",
            worktree_name="task-a-1234",
            preparation_attempt_id="gitprep-task-a-1234",
            branch="agents/session-1/tasks/task-a-1234",
            expected_commit_sha="a" * 40,
            expected_target_dir="/home/agent/worktrees/repo/task-a-1234",
        ),
    )

    assert record.state == "removed"
    assert len(requests) == 2
    assert requests[0]["preparation_attempt_id"] == "gitprep-task-a-1234"
    assert "preparation_attempt_id" not in requests[1]


def test_container_worker_client_does_not_retry_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("read timed out", request=request)

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )

    with pytest.raises(ContainerWorkerClientError, match="read timed out"):
        worker.create(
            WorkerJobRequest(
                session_id="session-1",
                job_id="job-1",
                image="runner:latest",
                command=["true"],
            )
        )

    assert len(requests) == 1
    assert sleeps == []


def test_container_worker_client_requires_token_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKER_TOKEN", raising=False)
    sleeps: list[float] = []
    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        sleep=sleeps.append,
    )

    with pytest.raises(ContainerWorkerClientError, match="WORKER_TOKEN") as exc:
        worker.get("job-1")

    assert "secret" not in str(exc.value).lower()
    assert sleeps == []


def test_container_worker_client_redacts_secret_error_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "bad", "token": "should-not-leak", "nested": {"api_key": "hidden"}},
            request=request,
        )

    worker = ContainerWorkerClient(
        base_url="http://worker.test",
        token_env="WORKER_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ContainerWorkerClientError) as exc:
        worker.get("job-1")

    message = str(exc.value)
    assert "should-not-leak" not in message
    assert "hidden" not in message
    assert "[redacted]" in message
