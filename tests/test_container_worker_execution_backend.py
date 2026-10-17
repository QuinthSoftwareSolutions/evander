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
    WorkerFailureCause,
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
    WorkerTaskWorktreeCleanupRecord,
    WorkerTaskWorktreeCleanupRequest,
)


def test_artifact_byte_limit_reaches_selected_worker(tmp_path, monkeypatch):
    import httpx

    from evander.execution.container_worker_client import ContainerWorkerClient

    monkeypatch.setenv("WORKER_TOKEN", "test-token")

    def handler(request):
        assert request.url.path == "/v1/sessions/session-1/artifacts/report.txt"
        assert request.url.params["max_bytes"] == "2"
        return httpx.Response(200, content="é".encode(), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = ContainerWorkerClient(
            base_url="http://worker.test", token_env="WORKER_TOKEN", client=transport,
        )
        backend = _backend(tmp_path, client)
        assert backend.read_session_artifact_text_bounded(
            session_id="session-1", relative_path="report.txt", max_bytes=2,
        ) == "é"


class FakeContainerWorkerClient:
    base_url = "http://worker.test"

    def __init__(self) -> None:
        self.created: list[WorkerJobRequest] = []
        self.got: list[str] = []
        self.stopped: list[str] = []
        self.interruptions: list[tuple[str, float | None, str]] = []
        self.deleted: list[str] = []
        self.deleted_sessions: list[str] = []
        self.logged: list[tuple[str, str | None]] = []
        self.prepared_sessions: list[WorkerSessionPrepareRequest] = []
        self.cloned_repos: list[tuple[str, WorkerRepoCloneRequest]] = []
        self.cleaned_worktrees: list[tuple[str, WorkerTaskWorktreeCleanupRequest]] = []
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

    def interrupt_active_tool(
        self,
        job_id: str,
        *,
        max_elapsed_seconds: float | None = None,
        reason: str = "operator_requested",
    ) -> WorkerJobRecord:
        self.interruptions.append((job_id, max_elapsed_seconds, reason))
        metadata = {
            **self.metadata_by_job.get(job_id, {}),
            "active_tool_interruption": {
                "interrupted": True,
                "reason": reason,
                "message": "sent SIGTERM to the active test process group",
            },
        }
        return _record(
            WorkerJobRequest(
                session_id="session-1",
                job_id=job_id,
                image="runner:latest",
                command=["true"],
                metadata=metadata,
            ),
            "running",
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

    def clone_repo(self, session_id: str, request: WorkerRepoCloneRequest) -> WorkerRepoCloneRecord:
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

    def cleanup_task_worktree(
        self,
        session_id: str,
        request: WorkerTaskWorktreeCleanupRequest,
    ) -> WorkerTaskWorktreeCleanupRecord:
        self.cleaned_worktrees.append((session_id, request))
        resolved_expected_commit = request.expected_commit_sha or "a" * 40
        return WorkerTaskWorktreeCleanupRecord(
            session_id=session_id,
            repo_name=request.repo_name,
            worktree_name=request.worktree_name,
            target_dir=request.expected_target_dir,
            branch=request.branch,
            expected_commit_sha=resolved_expected_commit,
            actual_branch=request.branch,
            actual_commit_sha=resolved_expected_commit,
            state="dry_run" if request.dry_run else "removed",
            safe_to_remove=True,
            removed=not request.dry_run,
            pruned=not request.dry_run,
            dry_run=request.dry_run,
            validation_count=1 if request.dry_run else 2,
            message="safe",
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


def test_new_subscription_receipts_retry_after_partial_import_and_keep_frozen_attribution(tmp_path, monkeypatch):
    from evander.codex_auth import CodexTurnSpec
    from evander.model_usage.runtime import model_usage_store, register_model_usage_correlation_context
    from evander.worker.model_usage import SubscriptionUsageRecorder, WorkerUsageJournal

    journal = WorkerUsageJournal(tmp_path / "worker" / "usage.db")

    class UsageClient(FakeContainerWorkerClient):
        def model_usage(self, job_id, *, cursor=0, limit=200):
            page = journal.page(job_id, cursor=cursor, limit=1)  # Exercise multiple pages.
            page.complete = True
            return page

    client = UsageClient()
    client.metadata_by_job["exec-usage"] = {"model_usage_capture": 1}
    backend = _backend(tmp_path, client)
    register_model_usage_correlation_context(
        runtime_dir=tmp_path, session_id="session-1", dispatch_id="dispatch-1",
        cost_category="Original category", cost_attribution_recorded=True,
        workflow_run_id="run-1", directory_path="root/a",
    )
    handle = backend.start(backend.prepare(ExecutionSpec(
        backend="container_worker_vm", target_name="exec-usage", session_id="session-1",
        directory_path="root/a", command=["true"], metadata={"dispatch_id": "dispatch-1"},
        codex_turn=CodexTurnSpec(auth_mode="chatgpt_subscription", prompt="test", model="test-model"),
    )))
    writer = SubscriptionUsageRecorder(
        job_id="exec-usage", session_id="session-1", sink=journal.append,
        context={"cost_category": "Changed worker label", "directory_path": "root/other"},
    )
    writer.expect_turn("thread")
    for total in (20, 40, 60):
        writer.notification("thread/tokenUsage/updated", {
            "threadId": "thread", "turnId": "turn-1", "tokenUsage": {
                "total": {"totalTokens": total}, "last": {"totalTokens": 20},
            },
        })
    writer.notification("turn/completed", {"threadId": "thread", "turn": {"id": "turn-1", "status": "completed"}})
    writer.finish()
    # A subsequent policy/context edit must not relabel this execution's receipts.
    register_model_usage_correlation_context(
        runtime_dir=tmp_path, session_id="session-1", dispatch_id="dispatch-1",
        cost_category="New category", cost_attribution_recorded=True,
    )
    store = model_usage_store(tmp_path)
    original = store.upsert_event
    calls = 0

    def interrupted(event):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated temporary write failure")
        original(event)

    monkeypatch.setattr(store, "upsert_event", interrupted)
    assert backend.inspect(handle).status == ExecutionStatusKind.RUNNING
    assert len(store.list_events()) == 1
    assert backend.repository.get(handle.execution_id).metadata["model_usage_sync_error"] == "OSError"
    # A fresh backend resumes the durable cursor. Earlier deliveries stay idempotent.
    backend = _backend(tmp_path, client)
    backend.inspect(handle)
    events = store.list_events()
    assert len(events) == 3
    from evander.model_usage.token_report import InferenceTokenReport
    from evander.model_usage.inference_report import InferenceCostReport
    assert InferenceTokenReport(store).read()["total_tokens"] == 60
    costs = InferenceCostReport(tmp_path, store).read()
    assert costs["total_cost"] == 0
    assert costs["coverage"]["excluded_subscription_events"] == 3
    assert {event.cost_category for event in events} == {"Original category"}
    assert {event.directory_path for event in events} == {"root/a"}
    assert {event.workflow_run_id for event in events} == {"run-1"}
    assert backend.repository.get(handle.execution_id).metadata["model_usage_complete"] is True
    backend.inspect(handle)
    assert len(store.list_events()) == 3


def test_subscription_usage_rejects_other_sessions_and_retries_after_cleanup(tmp_path):
    from evander.worker.model_usage import SubscriptionUsageRecorder, WorkerUsageJournal
    from evander.model_usage.runtime import model_usage_store

    journal = WorkerUsageJournal(tmp_path / "worker" / "usage.db")

    class UsageClient(FakeContainerWorkerClient):
        unavailable = True

        def model_usage(self, job_id, **kwargs):
            if self.unavailable:
                raise ContainerWorkerClientError("temporarily unavailable")
            page = journal.page(job_id, **kwargs)
            page.complete = True
            return page

    client = UsageClient()
    client.metadata_by_job["exec-usage"] = {"model_usage_capture": 1}
    backend = _backend(tmp_path, client)
    handle = backend.start(backend.prepare(ExecutionSpec(
        backend="container_worker_vm", target_name="exec-usage", session_id="session-1",
        command=["true"],
    )))
    writer = SubscriptionUsageRecorder(
        job_id="exec-usage", session_id="other-session", sink=journal.append, context={},
    )
    writer.expect_turn("thread")
    writer.notification("thread/tokenUsage/updated", {"threadId": "thread", "turnId": "turn", "tokenUsage": {
        "total": {"totalTokens": 10}, "last": {"totalTokens": 10},
    }})
    backend.destroy(handle)  # Receipt storage survives session/container cleanup.
    client.unavailable = False
    result = backend.inspect(handle)
    assert result.status == ExecutionStatusKind.DESTROYED
    assert model_usage_store(tmp_path).list_events() == []
    assert backend.repository.get(handle.execution_id).metadata["model_usage_sync_error"] == "ValueError"


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
            working_directory="/home/agent/repos/repo",
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
    assert prepared.working_directory == "/home/agent/repos/repo"
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
            working_directory="/home/agent/repos/repo",
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
    assert request.working_directory == "/home/agent/repos/repo"
    assert request.resource_limits.memory == "512m"
    assert request.metadata["purpose"] == "test"
    assert handle.execution_id == "exec-1"
    assert handle.image == "custom-runner:latest"
    assert handle.repo_source == "git@example.test/repo.git"
    stored = backend.repository.get("exec-1")
    assert stored is not None
    assert stored.worker_job_id == "exec-1"
    assert stored.session_id == "session-1"


def test_interrupt_active_tool_records_worker_outcome_without_stopping_job(tmp_path: Path) -> None:
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

    status = backend.interrupt_active_tool(
        handle,
        max_elapsed_seconds=300,
        reason="active_tool_timeout",
    )

    assert status.status == ExecutionStatusKind.RUNNING
    assert client.interruptions == [("exec-1", 300, "active_tool_timeout")]
    assert status.metadata["active_tool_interruption"]["interrupted"] is True
    assert status.metadata["active_tool_interruption"]["requested_reason"] == "active_tool_timeout"
    assert status.metadata["active_tool_interruption"]["watchdog_threshold_seconds"] == 300.0


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
    client.metadata_by_job["exec-1"] = {
        "exit_code": 2,
        "worker_terminal_kind": "command_exit",
    }

    status = backend.inspect(handle)

    assert status.status == ExecutionStatusKind.FAILED
    assert status.metadata["exit_code"] == 2
    assert status.metadata["worker_terminal_kind"] == "command_exit"
    assert client.got == ["exec-1"]


def test_worker_failure_cause_is_propagated_to_execution_metadata(tmp_path: Path) -> None:
    metadata = ContainerWorkerVmExecutionBackend._metadata_from_worker_record(  # noqa: SLF001
        {},
        WorkerJobRecord(
            job_id="job-1",
            session_id="session-1",
            image="runner:latest",
            command=["false"],
            container_name="agent-worker-job-1",
            status="failed",
            agent_home_path="/worker/session-1/agent_home",
            repo_root_path="/worker/session-1/agent_home/repos",
            artifacts_path="/worker/session-1/artifacts",
            logs_path="/worker/session-1/logs",
            failure_cause=WorkerFailureCause.PIDS_LIMIT_EXHAUSTED,
        ),
    )

    assert metadata["worker_failure_cause"] == "pids_limit_exhausted"


def test_subscription_deferral_is_propagated_to_execution_metadata() -> None:
    metadata = ContainerWorkerVmExecutionBackend._metadata_from_worker_record(  # noqa: SLF001
        {},
        WorkerJobRecord(
            job_id="job-1",
            session_id="session-1",
            image="runner:latest",
            command=["false"],
            container_name="agent-worker-job-1",
            status="exited",
            agent_home_path="/worker/session-1/agent_home",
            repo_root_path="/worker/session-1/agent_home/repos",
            artifacts_path="/worker/session-1/artifacts",
            logs_path="/worker/session-1/logs",
            metadata={
                "codex_subscription_deferral": {
                    "profile_id": "team-a",
                    "reason": "rate_limited",
                    "retry_at": "2026-08-01T12:30:00Z",
                },
                "worker_private_value": "not-forwarded",
            },
        ),
    )

    assert metadata["codex_subscription_deferral"] == {
        "profile_id": "team-a",
        "reason": "rate_limited",
        "retry_at": "2026-08-01T12:30:00Z",
    }
    assert "worker_private_value" not in metadata


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


def test_inspect_returns_destroyed_after_successful_cleanup_without_probing_worker(
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
    backend.destroy(handle)

    status = backend.inspect(handle)

    assert status.status == ExecutionStatusKind.DESTROYED
    assert client.got == []


def test_inspect_treats_worker_not_found_as_authoritative_destroyed_state(
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

    def missing_job(_: str) -> WorkerJobRecord:
        raise ContainerWorkerClientError("job not found", status_code=404)

    client.get = missing_job  # type: ignore[method-assign]

    status = backend.inspect(handle)

    assert status.status == ExecutionStatusKind.DESTROYED
    stored = backend.repository.get("exec-1")
    assert stored is not None
    assert stored.last_status == "removed"
    assert stored.cleanup_status == "cleanup_succeeded"


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
        files=[{"destination_path": "/home/agent/repos/Repo/AGENT_TASK.yaml", "content": "task"}],
    )

    assert prepared.repo_root_container_path == "/home/agent/repos"
    assert client.prepared_sessions[0].metadata == {"purpose": "test"}
    assert clone.target_dir == "/home/agent/repos/Repo"
    assert client.cloned_repos[0][1].repo_source == "/tmp/repo"
    assert client.cloned_repos[0][1].repo_name == "Repo"
    assert written.copied_paths == ["/home/agent/repos/Repo/AGENT_TASK.yaml"]
    assert client.written_files[0][1].files[0].content == "task"


def test_task_worktree_cleanup_delegates_structured_expectations(
    tmp_path: Path,
) -> None:
    client = FakeContainerWorkerClient()
    backend = _backend(tmp_path, client)

    result = backend.cleanup_task_worktree(
        session_id="session-1",
        repo_name="repo",
        worktree_name="task-a-1234",
        branch="agents/session-1/tasks/task-a-1234",
        expected_commit_sha="a" * 40,
        expected_target_dir="/home/agent/worktrees/repo/task-a-1234",
        dry_run=True,
    )

    assert result.state == "dry_run"
    assert client.cleaned_worktrees == [
        (
            "session-1",
            WorkerTaskWorktreeCleanupRequest(
                repo_name="repo",
                worktree_name="task-a-1234",
                branch="agents/session-1/tasks/task-a-1234",
                expected_commit_sha="a" * 40,
                expected_target_dir="/home/agent/worktrees/repo/task-a-1234",
                dry_run=True,
            ),
        )
    ]

    backend.cleanup_task_worktree(
        session_id="session-1",
        repo_name="repo",
        worktree_name="task-pending-1234",
        preparation_attempt_id="gitprep-task-pending-1234",
        branch="agents/session-1/tasks/task-pending-1234",
        expected_commit_sha=None,
        expected_target_dir="/home/agent/worktrees/repo/task-pending-1234",
    )

    assert client.cleaned_worktrees[1] == (
        "session-1",
        WorkerTaskWorktreeCleanupRequest(
            repo_name="repo",
            worktree_name="task-pending-1234",
            preparation_attempt_id="gitprep-task-pending-1234",
            branch="agents/session-1/tasks/task-pending-1234",
            expected_commit_sha=None,
            expected_target_dir="/home/agent/worktrees/repo/task-pending-1234",
        ),
    )


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
    record = ContainerWorkerExecutionRepository(tmp_path / "container-worker-executions.json")
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


def test_default_switch_routes_old_and_new_jobs_and_cleanup_to_assigned_workers(
    tmp_path, monkeypatch
):
    from evander.execution import container_worker_backend as module

    old = FakeContainerWorkerClient()
    new = FakeContainerWorkerClient()
    old.base_url, new.base_url = "http://old.test", "http://new.test"
    clients = {old.base_url: old, new.base_url: new}
    connections = []

    def client_factory(*, base_url, token_env, timeout_seconds):
        connections.append((base_url, token_env))
        return clients[base_url]

    monkeypatch.setattr(module, "ContainerWorkerClient", client_factory)
    config = GatewayConfig(
        runtime_dir=tmp_path,
        container_worker_base_url=old.base_url,
        container_worker_token_env="OLD_TOKEN",
        container_worker_mssql_host="old-sql",
    )
    backend = ContainerWorkerVmExecutionBackend(config=config)
    backend.prepare_session(session_id="existing")
    running = backend.start(backend.prepare(ExecutionSpec(session_id="existing", command=["true"])))
    replacement_config = config.model_copy(
        update={
            "container_worker_base_url": new.base_url,
            "container_worker_token_env": "NEW_TOKEN",
            "container_worker_mssql_host": "new-sql",
        }
    )
    restarted = ContainerWorkerVmExecutionBackend(config=replacement_config)
    restarted.inspect(running.execution_id)
    restarted.get_log_snapshot(running.execution_id)
    restarted.interrupt_active_tool(running.execution_id)
    restarted.stop(running.execution_id)
    restarted.destroy(running.execution_id)
    restarted.prepare_session(session_id="existing")
    restarted.clone_repo(session_id="existing", repo_source="ssh://git/repo")
    restarted.write_files(
        session_id="existing",
        files=[{"destination_path": "/home/agent/probe.txt", "content": "worker routing probe"}],
    )
    database = restarted.provision_mssql_database(session_id="existing", logical_name="app")
    assert database.server == "old-sql"
    resumed = restarted.start(
        restarted.prepare(ExecutionSpec(session_id="existing", command=["true"]))
    )
    assert old.created[-1].job_id == resumed.execution_id
    restarted.delete_session("existing")
    restarted.prepare_session(session_id="fresh")
    fresh = restarted.start(restarted.prepare(ExecutionSpec(session_id="fresh", command=["true"])))
    assert new.created[-1].job_id == fresh.execution_id
    assert old.got and old.logged and old.interruptions and old.stopped and old.deleted
    assert old.cloned_repos and old.written_files and old.deleted_sessions == ["existing"]
    assert not new.got and not new.deleted_sessions and not new.cloned_repos
    assert set(connections) == {(old.base_url, "OLD_TOKEN"), (new.base_url, "NEW_TOKEN")}


def test_worker_failure_keeps_assignment_and_never_falls_back_to_new_default(tmp_path, monkeypatch):
    from evander.execution import container_worker_backend as module
    from evander.execution.worker_assignments import ContainerWorkerAssignmentRepository

    old = FakeContainerWorkerClient()
    old.base_url = "http://old.test"
    config = GatewayConfig(runtime_dir=tmp_path, container_worker_base_url=old.base_url)
    backend = ContainerWorkerVmExecutionBackend(config=config, client=old)

    def offline(request):
        raise ContainerWorkerClientError("worker offline")

    old.prepare_session = offline
    with pytest.raises(ContainerWorkerClientError, match="offline"):
        backend.prepare_session(session_id="existing")
    assert ContainerWorkerAssignmentRepository(tmp_path).get("existing").worker_url == old.base_url

    new = FakeContainerWorkerClient()
    new.base_url = "http://new.test"
    requested = []

    def factory(**kwargs):
        requested.append(kwargs["base_url"])
        return old

    monkeypatch.setattr(module, "ContainerWorkerClient", factory)
    restarted = ContainerWorkerVmExecutionBackend(
        config=config.model_copy(update={"container_worker_base_url": new.base_url}), client=new
    )
    with pytest.raises(ContainerWorkerClientError, match="offline"):
        restarted.prepare_session(session_id="existing")
    assert requested == [old.base_url]
    assert not new.prepared_sessions
