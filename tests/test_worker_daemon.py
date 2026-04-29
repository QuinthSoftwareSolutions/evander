from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

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
        return {"status": "running"}

    def stop_job(self, record: WorkerJobRecord) -> None:
        self.stopped.append(record.job_id)

    def remove_job(self, record: WorkerJobRecord) -> None:
        self.removed.append(record.job_id)

    def get_logs(self, record: WorkerJobRecord) -> str:
        return self.logs.get(record.job_id, f"log for {record.job_id}\n")


def _client(tmp_path: Path, runtime: FakeContainerRuntime | None = None) -> TestClient:
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime or FakeContainerRuntime(),
        token="test-token",
    )
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_auth_rejects_missing_or_wrong_bearer_token_and_accepts_configured_token(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    assert client.get("/healthz").status_code == 200
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
    assert paths.workspace.is_dir()
    assert paths.agent_home.is_dir()
    assert paths.artifacts.is_dir()
    assert paths.logs.is_dir()
    assert json.loads(paths.metadata_path.read_text(encoding="utf-8"))["session_id"] == "session-1"


def test_container_mount_paths_are_persisted_for_workspace_and_agent_home(tmp_path: Path) -> None:
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
    assert payload["workspace_path"] == str(paths.workspace)
    assert payload["agent_home_path"] == str(paths.agent_home)
    assert paths.workspace == tmp_path / "worker-root" / "sessions" / "session-1" / "workspace"
    assert paths.agent_home == tmp_path / "worker-root" / "sessions" / "session-1" / "agent_home"


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
