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
        "workspace_path": "/worker/session-1/workspace",
        "agent_home_path": "/worker/session-1/agent_home",
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
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
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
    assert seen == [
        ("GET", "/healthz"),
        ("POST", "/v1/jobs"),
        ("GET", "/v1/jobs/job-1"),
        ("POST", "/v1/jobs/job-1/stop"),
        ("DELETE", "/v1/jobs/job-1"),
        ("GET", "/v1/jobs/job-1/logs"),
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
                    "workspace_path": "/worker/session-1/workspace",
                    "agent_home_path": "/worker/session-1/agent_home",
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
                    "target_dir": "/workspace",
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
        return httpx.Response(
            200,
            json={
                "session_id": "session-1",
                "copied_count": 1,
                "copied_paths": ["/workspace/AGENT_TASK.yaml"],
                "roots": {"workspace": "/workspace"},
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
        ).workspace_container_path
        == "/workspace"
    )
    assert (
        worker.clone_repo("session-1", WorkerRepoCloneRequest(repo_source="/tmp/repo")).target_dir
        == "/workspace"
    )
    assert (
        worker.write_files(
            "session-1",
            WorkerFileWriteRequest(
                files=[{"destination_path": "/workspace/AGENT_TASK.yaml", "content": "x"}]
            ),
        ).copied_count
        == 1
    )
    assert worker.delete_session("session-1") == {"deleted": True, "session_id": "session-1"}
    assert seen == [
        ("POST", "/v1/sessions"),
        ("POST", "/v1/sessions/session-1/repo"),
        ("POST", "/v1/sessions/session-1/files"),
        ("DELETE", "/v1/sessions/session-1"),
    ]


def test_container_worker_client_requires_token_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKER_TOKEN", raising=False)
    worker = ContainerWorkerClient(base_url="http://worker.test", token_env="WORKER_TOKEN")

    with pytest.raises(ContainerWorkerClientError, match="WORKER_TOKEN") as exc:
        worker.get("job-1")

    assert "secret" not in str(exc.value).lower()


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
