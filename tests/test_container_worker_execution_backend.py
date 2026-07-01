from __future__ import annotations

import json
from pathlib import Path

import pytest

from evander.config import GatewayConfig
from evander.execution import ExecutionSpec, ExecutionStatusKind
from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from evander.worker.models import (
    WorkerFileWriteRecord,
    WorkerFileWriteRequest,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerMssqlDatabaseRecord,
    WorkerMssqlDatabaseRequest,
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
        self.deleted_sessions: list[str] = []
        self.logged: list[tuple[str, str | None]] = []
        self.prepared_sessions: list[WorkerSessionPrepareRequest] = []
        self.cloned_repos: list[tuple[str, WorkerRepoCloneRequest]] = []
        self.written_files: list[tuple[str, WorkerFileWriteRequest]] = []
        self.provisioned_mssql: list[tuple[str, WorkerMssqlDatabaseRequest]] = []
        self.status_by_job: dict[str, str] = {}
        self.metadata_by_job: dict[str, dict[str, object]] = {}

    def create(self, request: WorkerJobRequest) -> WorkerJobRecord:
        self.created.append(request)
        job_id = request.job_id or ""
        request = request.model_copy(
            update={"metadata": {**request.metadata, **self.metadata_by_job.get(job_id, {})}}
        )
        return _record(request, self.status_by_job.get(job_id, "running"))

    def get(self, job_id: str) -> WorkerJobRecord:
        self.got.append(job_id)
        return _record(
            WorkerJobRequest(
                session_id="session-1",
                job_id=job_id,
                image="runner:latest",
                command=["true"],
                metadata=self.metadata_by_job.get(job_id, {}),
            ),
            self.status_by_job.get(job_id, "running"),
        )

    def stop(self, job_id: str) -> WorkerJobRecord:
        self.stopped.append(job_id)
        return _record(
            WorkerJobRequest(
                session_id="session-1",
                job_id=job_id,
                image="runner:latest",
                command=["true"],
                metadata=self.metadata_by_job.get(job_id, {}),
            ),
            "stopped",
        )

    def delete(self, job_id: str) -> dict[str, object]:
        self.deleted.append(job_id)
        return {"deleted": True, "job_id": job_id}

    def delete_session(self, session_id: str) -> dict[str, object]:
        self.deleted_sessions.append(session_id)
        return {"deleted": True, "session_id": session_id}

    def logs(self, job_id: str, *, mode: str | None = None) -> str:
        self.logged.append((job_id, mode))
        return "one\ntwo\nthree\n"

    def prepare_session(self, request: WorkerSessionPrepareRequest) -> WorkerSessionRecord:
        self.prepared_sessions.append(request)
        return WorkerSessionRecord(
            session_id=request.session_id,
            root_path=f"/worker/{request.session_id}",
            agent_home_path=f"/worker/{request.session_id}/agent_home",
            repo_root_path=f"/worker/{request.session_id}/agent_home/repos",
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
            target_dir=f"/home/agent/repos/{request.repo_name or 'repo'}",
            guest_repo_name=request.repo_name or "repo",
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

    def provision_mssql_database(
        self, session_id: str, request: WorkerMssqlDatabaseRequest
    ) -> WorkerMssqlDatabaseRecord:
        self.provisioned_mssql.append((session_id, request))
        return WorkerMssqlDatabaseRecord(
            session_id=session_id,
            logical_name=request.logical_name,
            database_name="asb_hash_app_hash",
            server="worker-default",
            port=1433,
            username="asb_login_hash",
            password="session-secret",
            connection_string="Server=tcp:worker-default,1433;Password=session-secret",
        )


def _record(request: WorkerJobRequest, status: str) -> WorkerJobRecord:
    return WorkerJobRecord(
        job_id=request.job_id or "job-1",
        session_id=request.session_id,
        image=request.image,
        command=request.command,
        container_name=f"agent-worker-{request.job_id}",
        status=status,
        agent_home_path="/worker/session-1/agent_home",
        repo_root_path="/worker/session-1/agent_home/repos",
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
    stored = backend.repository.get("exec-1")
    assert stored is not None
    assert stored.worker_job_id == "exec-1"
    assert stored.session_id == "session-1"


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


def test_inspect_prefers_failed_codex_outcome_over_worker_lifecycle_status(
    tmp_path: Path,
) -> None:
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
    client.status_by_job["exec-1"] = "exited"
    client.metadata_by_job["exec-1"] = {
        "codex_outcome": {
            "status": "failed",
            "reason": "codex_no_final_message",
            "message": "Codex ended without a final agent message.",
            "provider_kind": "cloud_or_unknown",
        }
    }

    status = backend.inspect(handle)

    assert status.status == ExecutionStatusKind.FAILED
    assert status.message == "Codex ended without a final agent message."
    stored = backend.repository.get("exec-1")
    assert stored is not None
    assert stored.last_status == "exited"
    assert stored.metadata["codex_outcome"]["reason"] == "codex_no_final_message"


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
    assert backend.get_log_snapshot("exec-1", lines=2, mode="raw") == "two\nthree\n"
    assert backend.destroy("exec-1").status == ExecutionStatusKind.DESTROYED
    assert client.stopped == ["exec-1"]
    assert client.logged == [("exec-1", None), ("exec-1", "raw")]
    assert client.deleted == ["exec-1"]
    store = backend.repository.read_all()
    assert set(store) == {"exec-1"}
    assert store["exec-1"].last_status == "removed"
    assert store["exec-1"].cleanup_status == "cleanup_succeeded"
    assert store["exec-1"].cleanup_error is None


def test_destroy_records_cleanup_failed_tombstone_when_delete_fails(
    tmp_path: Path,
) -> None:
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

    def _raise(_: str) -> None:
        raise ContainerWorkerClientError("container worker DELETE /v1/jobs/exec-1 failed: boom")

    client.delete = _raise  # type: ignore[method-assign]

    with pytest.raises(ContainerWorkerClientError):
        backend.destroy("exec-1")

    store = backend.repository.read_all()
    assert store["exec-1"].last_status == "cleanup_failed"
    assert store["exec-1"].cleanup_status == "cleanup_failed"
    assert "boom" in store["exec-1"].cleanup_error
    assert store["exec-1"].finished_at is not None


def test_session_preparation_clone_and_write_delegate_to_worker_client(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)

    prepared = backend.prepare_session(session_id="session-1", metadata={"purpose": "test"})
    clone = backend.clone_repo(session_id="session-1", repo_source="/tmp/repo", repo_name="Repo")
    written = backend.write_files(
        session_id="session-1",
        files=[
            {"destination_path": "/home/agent/repos/Repo/AGENT_TASK.yaml", "content": "task"}
        ],
    )

    assert prepared.repo_root_container_path == "/home/agent/repos"
    assert client.prepared_sessions[0].metadata == {"purpose": "test"}
    assert clone.target_dir == "/home/agent/repos/Repo"
    assert client.cloned_repos[0][1].repo_source == "/tmp/repo"
    assert client.cloned_repos[0][1].repo_name == "Repo"
    assert written.copied_paths == ["/home/agent/repos/Repo/AGENT_TASK.yaml"]
    assert client.written_files[0][1].files[0].content == "task"


def test_delete_session_delegates_to_worker_client(tmp_path: Path) -> None:
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

    result = backend.delete_session("session-1")

    assert result == {"deleted": True, "session_id": "session-1"}
    assert client.deleted_sessions == ["session-1"]
    store = backend.repository.read_all()
    assert store["exec-1"].last_status == "removed"
    assert store["exec-1"].cleanup_status == "cleanup_succeeded"
    assert store["exec-1"].cleanup_error is None


def test_delete_session_uses_targeted_repository_lookup(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    record = ContainerWorkerExecutionRepository(
        tmp_path / "container-worker-executions.json"
    )
    record.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="session-1",
            worker_job_id="worker-1",
            worker_url="http://worker.test",
            target_name="target-1",
            last_status="running",
        )
    )

    class TargetedRepository:
        def __init__(self, source: ContainerWorkerExecutionRepository) -> None:
            self.source = source
            self.listed_sessions: list[str] = []
            self.updated: list[ContainerWorkerExecutionRecord] = []

        def list_by_session(self, session_id: str) -> list[ContainerWorkerExecutionRecord]:
            self.listed_sessions.append(session_id)
            return self.source.list_by_session(session_id)

        def put(self, value: ContainerWorkerExecutionRecord) -> None:
            self.updated.append(value)
            self.source.put(value)

        def read_all(self) -> dict[str, ContainerWorkerExecutionRecord]:
            raise AssertionError("delete_session should not scan all execution mappings")

    targeted_repository = TargetedRepository(record)
    backend = ContainerWorkerVmExecutionBackend(
        config=GatewayConfig(
            runtime_dir=tmp_path,
            execution_backend="container_worker_vm",
            container_worker_base_url="http://worker.test",
        ),
        client=client,
        repository=targeted_repository,  # type: ignore[arg-type]
    )

    result = backend.delete_session("session-1")

    assert result == {"deleted": True, "session_id": "session-1"}
    assert targeted_repository.listed_sessions == ["session-1"]
    assert [updated.execution_id for updated in targeted_repository.updated] == ["exec-1"]
    stored = record.get("exec-1")
    assert stored is not None
    assert stored.last_status == "removed"
    assert stored.cleanup_status == "cleanup_succeeded"


def test_mssql_provisioning_uses_configured_advertised_endpoint(tmp_path: Path) -> None:
    client = FakeContainerWorkerClient()
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_mssql_host="10.55.0.12",
        container_worker_mssql_port=15433,
    )
    backend = ContainerWorkerVmExecutionBackend(
        config=config,
        client=client,
        repository=ContainerWorkerExecutionRepository(
            tmp_path / "container-worker-executions.json"
        ),
    )

    record = backend.provision_mssql_database(session_id="session-1", logical_name="app")

    assert client.provisioned_mssql[0][0] == "session-1"
    assert client.provisioned_mssql[0][1].logical_name == "app"
    assert record.server == "10.55.0.12"
    assert record.port == 15433
    assert record.connection_string.startswith("Server=tcp:10.55.0.12,15433;")
    assert "Database=asb_hash_app_hash;" in record.connection_string
    assert "Password=session-secret;" in record.connection_string


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
    store_record = backend.repository.get("exec-1")
    assert store_record is not None
    store_text = json.dumps(store_record.model_dump(mode="json"))
    assert "should-not-leak" not in json.dumps(request_payload)
    assert "hidden" not in json.dumps(request_payload)
    assert "should-not-leak" not in store_text
    assert "hidden" not in store_text
    assert client.created[0].metadata["authorization"] == "[redacted]"
