from __future__ import annotations

import json
import subprocess
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


def _client(
    tmp_path: Path,
    runtime: FakeContainerRuntime | None = None,
    *,
    allowed_git_source_prefixes: list[str] | None = None,
    git_ssh_source_dir: Path | None = None,
) -> TestClient:
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=runtime or FakeContainerRuntime(),
        allowed_git_source_prefixes=allowed_git_source_prefixes,
        git_ssh_source_dir=git_ssh_source_dir,
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
    assert client.get("/v1/jobs/job-1", headers=_auth()).status_code == 404


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
    assert client.get("/v1/jobs/job-1", headers=_auth()).status_code == 404


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
