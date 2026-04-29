from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.execution import ExecutionSpec, ExecutionStatusKind
from agent_sandbox.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from agent_sandbox.execution.container_worker_repository import ContainerWorkerExecutionRepository
from agent_sandbox.worker.models import (
    WorkerFileWriteRecord,
    WorkerFileWriteRequest,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerRepoCloneRecord,
    WorkerRepoCloneRequest,
    WorkerSessionPrepareRequest,
    WorkerSessionRecord,
)


class FakeContainerWorkerClient:
    base_url = "http://worker.test"

    def __init__(self) -> None:
        self.created: list[WorkerJobRequest] = []
        self.got: list[str] = []
        self.stopped: list[str] = []
        self.deleted: list[str] = []
        self.logged: list[str] = []
        self.prepared_sessions: list[WorkerSessionPrepareRequest] = []
        self.cloned_repos: list[tuple[str, WorkerRepoCloneRequest]] = []
        self.written_files: list[tuple[str, WorkerFileWriteRequest]] = []
        self.status_by_job: dict[str, str] = {}

    def create(self, request: WorkerJobRequest) -> WorkerJobRecord:
        self.created.append(request)
        return _record(request, self.status_by_job.get(request.job_id or "", "running"))

    def get(self, job_id: str) -> WorkerJobRecord:
        self.got.append(job_id)
        return _record(
            WorkerJobRequest(
                session_id="session-1", job_id=job_id, image="runner:latest", command=["true"]
            ),
            self.status_by_job.get(job_id, "running"),
        )

    def stop(self, job_id: str) -> WorkerJobRecord:
        self.stopped.append(job_id)
        return _record(
            WorkerJobRequest(
                session_id="session-1", job_id=job_id, image="runner:latest", command=["true"]
            ),
            "stopped",
        )

    def delete(self, job_id: str) -> dict[str, object]:
        self.deleted.append(job_id)
        return {"deleted": True, "job_id": job_id}

    def logs(self, job_id: str) -> str:
        self.logged.append(job_id)
        return "one\ntwo\nthree\n"

    def prepare_session(self, request: WorkerSessionPrepareRequest) -> WorkerSessionRecord:
        self.prepared_sessions.append(request)
        return WorkerSessionRecord(
            session_id=request.session_id,
            root_path=f"/worker/{request.session_id}",
            workspace_path=f"/worker/{request.session_id}/workspace",
            agent_home_path=f"/worker/{request.session_id}/agent_home",
            artifacts_path=f"/worker/{request.session_id}/artifacts",
            logs_path=f"/worker/{request.session_id}/logs",
            metadata_path=f"/worker/{request.session_id}/metadata.json",
            metadata=request.metadata,
        )

    def clone_repo(
        self, session_id: str, request: WorkerRepoCloneRequest
    ) -> WorkerRepoCloneRecord:
        self.cloned_repos.append((session_id, request))
        return WorkerRepoCloneRecord(
            session_id=session_id,
            target_dir="/workspace",
            guest_repo_name="repo",
            remote_url=request.repo_source,
            cloned=True,
        )

    def write_files(
        self, session_id: str, request: WorkerFileWriteRequest
    ) -> WorkerFileWriteRecord:
        self.written_files.append((session_id, request))
        return WorkerFileWriteRecord(
            session_id=session_id,
            copied_count=len(request.files),
            copied_paths=[entry.destination_path for entry in request.files],
        )


def _record(request: WorkerJobRequest, status: str) -> WorkerJobRecord:
    return WorkerJobRecord(
        job_id=request.job_id or "job-1",
        session_id=request.session_id,
        image=request.image,
        command=request.command,
        container_name=f"agent-worker-{request.job_id}",
        status=status,
        workspace_path="/worker/session-1/workspace",
        agent_home_path="/worker/session-1/agent_home",
        artifacts_path="/worker/session-1/artifacts",
        logs_path="/worker/session-1/logs",
        metadata=request.metadata,
    )


def _backend(
    tmp_path: Path, client: FakeContainerWorkerClient | None = None
) -> ContainerWorkerVmExecutionBackend:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_default_image="runner:latest",
    )
    return ContainerWorkerVmExecutionBackend(
        config=config,
        client=client or FakeContainerWorkerClient(),
        repository=ContainerWorkerExecutionRepository(
            tmp_path / "container-worker-executions.json"
        ),
    )


def test_prepare_builds_metadata_without_http_call(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)

    prepared = backend.prepare(
        ExecutionSpec(
            backend="container_worker_vm",
            target_name="exec-1",
            session_id="session-1",
            directory_path="root/team",
            command=["true"],
            image="custom-runner:latest",
            repo_source="git@example.test/repo.git",
            clone_branch="feature/test",
            resource_limits={"cpus": 1.5},
            persistence_mode="session",
            workspace_mode="session",
            metadata={"purpose": "test"},
        )
    )

    assert prepared.execution_id == "exec-1"
    assert prepared.session_id == "session-1"
    assert prepared.command == ["true"]
    assert prepared.image == "custom-runner:latest"
    assert prepared.repo_source == "git@example.test/repo.git"
    assert prepared.clone_branch == "feature/test"
    assert prepared.resource_limits == {"cpus": 1.5}
    assert prepared.persistence_mode == "session"
    assert prepared.metadata["directory_path"] == "root/team"
    assert prepared.metadata["workspace_mode"] == "session"
    assert client.created == []


def test_start_sends_worker_job_request_and_records_mapping(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)
    prepared = backend.prepare(
        ExecutionSpec(
            backend="container_worker_vm",
            target_name="exec-1",
            session_id="session-1",
            command="echo hello",
            image="custom-runner:latest",
            repo_source="git@example.test/repo.git",
            clone_branch="feature/test",
            resource_limits={"memory": "512m"},
            metadata={"purpose": "test"},
        )
    )

    handle = backend.start(prepared)

    request = client.created[0]
    assert request.session_id == "session-1"
    assert request.job_id == "exec-1"
    assert request.image == "custom-runner:latest"
    assert request.command == ["bash", "-lc", "echo hello"]
    assert request.repo_source == "git@example.test/repo.git"
    assert request.clone_branch == "feature/test"
    assert request.resource_limits.memory == "512m"
    assert request.metadata["purpose"] == "test"
    assert handle.execution_id == "exec-1"
    assert handle.image == "custom-runner:latest"
    assert handle.repo_source == "git@example.test/repo.git"
    store = json.loads((tmp_path / "container-worker-executions.json").read_text(encoding="utf-8"))
    assert store["exec-1"]["worker_job_id"] == "exec-1"
    assert store["exec-1"]["session_id"] == "session-1"


def test_start_requires_first_class_command(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    prepared = backend.prepare(
        ExecutionSpec(
            backend="container_worker_vm",
            target_name="exec-1",
            session_id="session-1",
            metadata={"command": ["ignored"]},
        )
    )

    with pytest.raises(ValueError, match="ExecutionSpec.command is required"):
        backend.start(prepared)


def test_inspect_maps_worker_status(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)
    handle = backend.start(
        backend.prepare(
            ExecutionSpec(
                backend="container_worker_vm",
                target_name="exec-1",
                session_id="session-1",
                command=["true"],
            )
        )
    )
    client.status_by_job["exec-1"] = "failed"

    status = backend.inspect(handle)

    assert status.status == ExecutionStatusKind.FAILED
    assert client.got == ["exec-1"]


def test_stop_destroy_and_logs_call_expected_endpoints(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)
    backend.start(
        backend.prepare(
            ExecutionSpec(
                backend="container_worker_vm",
                target_name="exec-1",
                session_id="session-1",
                command=["true"],
            )
        )
    )

    assert backend.stop("exec-1").status == ExecutionStatusKind.STOPPED
    assert backend.get_log_snapshot("exec-1", lines=2) == "two\nthree\n"
    assert backend.destroy("exec-1").status == ExecutionStatusKind.DESTROYED
    assert client.stopped == ["exec-1"]
    assert client.logged == ["exec-1"]
    assert client.deleted == ["exec-1"]


def test_session_preparation_clone_and_write_delegate_to_worker_client(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)

    prepared = backend.prepare_session(session_id="session-1", metadata={"purpose": "test"})
    clone = backend.clone_repo(session_id="session-1", repo_source="/tmp/repo")
    written = backend.write_files(
        session_id="session-1",
        files=[{"destination_path": "/workspace/AGENT_TASK.yaml", "content": "task"}],
    )

    assert prepared.workspace_container_path == "/workspace"
    assert client.prepared_sessions[0].metadata == {"purpose": "test"}
    assert clone.target_dir == "/workspace"
    assert client.cloned_repos[0][1].repo_source == "/tmp/repo"
    assert written.copied_paths == ["/workspace/AGENT_TASK.yaml"]
    assert client.written_files[0][1].files[0].content == "task"


def test_ambiguous_session_id_resolution_raises_clear_error(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    for target_name in ("exec-1", "exec-2"):
        backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend="container_worker_vm",
                    target_name=target_name,
                    session_id="session-1",
                    command=["true"],
                )
            )
        )

    with pytest.raises(ValueError, match="Ambiguous container worker session target session-1"):
        backend.inspect("session-1")


def test_secret_metadata_is_redacted_before_request_and_store(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)

    backend.start(
        backend.prepare(
            ExecutionSpec(
                backend="container_worker_vm",
                target_name="exec-1",
                session_id="session-1",
                command=["true"],
                metadata={
                    "purpose": "test",
                    "authorization": "Bearer should-not-leak",
                    "nested": {"api_key": "hidden"},
                },
            )
        )
    )

    request_payload = client.created[0].model_dump(mode="json")
    store_text = (tmp_path / "container-worker-executions.json").read_text(encoding="utf-8")
    assert "should-not-leak" not in json.dumps(request_payload)
    assert "hidden" not in json.dumps(request_payload)
    assert "should-not-leak" not in store_text
    assert "hidden" not in store_text
    assert client.created[0].metadata["authorization"] == "[redacted]"
