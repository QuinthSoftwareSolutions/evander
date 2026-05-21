from __future__ import annotations

import httpx
import pytest

from agent_sandbox.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from agent_sandbox.worker.models import WorkerJobRequest
from agent_sandbox.worker.models import (
    WorkerFileWriteRequest,
    WorkerMssqlDatabaseRequest,
    WorkerRepoCloneRequest,
    WorkerSessionPrepareRequest,
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


def test_container_worker_client_prepares_session_clones_repo_and_writes_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TOKEN", "secret-token")
    seen: list[tuple[str, str]] = []

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
        if request.url.path.endswith("/repo"):
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
    assert seen == [
        ("POST", "/v1/sessions"),
        ("POST", "/v1/sessions/session-1/repo"),
        ("POST", "/v1/sessions/session-1/files"),
        ("GET", "/v1/sessions/session-1/artifacts/reports/summary.txt"),
        ("DELETE", "/v1/sessions/session-1"),
    ]


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
    monkeypatch.setattr("agent_sandbox.execution.container_worker_client.random.random", lambda: 0.5)
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
    monkeypatch.setattr("agent_sandbox.execution.container_worker_client.random.random", lambda: 0.5)
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
