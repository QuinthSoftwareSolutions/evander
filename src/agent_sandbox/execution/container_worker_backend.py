from __future__ import annotations

import uuid
from typing import Any

from agent_sandbox.config import GatewayConfig
from agent_sandbox.worker.models import (
    SAFE_ID_PATTERN,
    WorkerFileWriteRequest,
    WorkerFileWriteRecord,
    WorkerJobRequest,
    WorkerRepoCloneRecord,
    WorkerRepoCloneRequest,
    WorkerResourceLimits,
    WorkerSessionPrepareRequest,
    WorkerSessionRecord,
    now_utc,
)
from agent_sandbox.worker.security import sanitize_metadata

from .backend import ExecutionTarget
from .container_worker_client import ContainerWorkerClient
from .container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from .models import (
    ExecutionBackendCapabilities,
    ExecutionBackendKind,
    ExecutionHandle,
    ExecutionSpec,
    ExecutionStatus,
    ExecutionStatusKind,
    PreparedExecution,
)


class ContainerWorkerVmExecutionBackend:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        client: ContainerWorkerClient | None = None,
        repository: ContainerWorkerExecutionRepository | None = None,
    ) -> None:
        if not config.container_worker_base_url:
            raise ValueError("container_worker_vm requires container_worker_base_url.")
        self.config = config
        self.client = client or ContainerWorkerClient(
            base_url=config.container_worker_base_url,
            token_env=config.container_worker_token_env,
            timeout_seconds=config.container_worker_request_timeout_seconds,
        )
        self.repository = repository or ContainerWorkerExecutionRepository(
            config.runtime_dir / "container-worker-executions.json"
        )

    @property
    def kind(self) -> ExecutionBackendKind:
        return ExecutionBackendKind.CONTAINER_WORKER_VM

    @property
    def capabilities(self) -> ExecutionBackendCapabilities:
        return ExecutionBackendCapabilities()

    def prepare(self, spec: ExecutionSpec) -> PreparedExecution:
        execution_id = self._safe_execution_id(spec.target_name)
        session_id = spec.session_id or execution_id
        target_name = spec.target_name or execution_id
        persistence_mode = (
            spec.persistence_mode or self.config.container_worker_workspace_persistence
        )
        workspace_mode = spec.workspace_mode or persistence_mode
        metadata = sanitize_metadata(
            {
                **spec.metadata,
                "session_id": session_id,
                "directory_path": spec.directory_path,
                "persistence_mode": persistence_mode,
                "workspace_mode": workspace_mode,
                "gateway_url": self.config.gateway_url,
                "openai_relay_port": self.config.openai_relay_port,
                "http_target_relay_port": self.config.http_target_relay_port,
                "nuget_relay_port": self.config.nuget_relay_port,
            }
        )
        return PreparedExecution(
            backend=self.kind,
            execution_id=execution_id,
            target_name=target_name,
            session_id=session_id,
            directory_path=spec.directory_path,
            command=spec.command,
            image=spec.image or self.config.container_worker_default_image,
            repo_source=spec.repo_source,
            clone_branch=spec.clone_branch,
            resource_limits=spec.resource_limits,
            persistence_mode=persistence_mode,
            workspace_mode=workspace_mode,
            metadata=metadata,
        )

    def start(self, target: ExecutionTarget) -> ExecutionHandle:
        prepared = (
            target
            if isinstance(target, PreparedExecution)
            else self.prepare(self._spec_from_target(target))
        )
        request = self._job_request(prepared)
        record = self.client.create(request)
        mapping = ContainerWorkerExecutionRecord(
            execution_id=prepared.execution_id,
            session_id=request.session_id,
            worker_job_id=record.job_id,
            worker_url=self.client.base_url,
            target_name=prepared.target_name,
            started_at=record.started_at,
            finished_at=record.finished_at,
            last_status=record.status,
            directory_path=prepared.directory_path,
            metadata={
                **prepared.metadata,
                "worker_status": record.status,
                "image": record.image,
            },
        )
        self.repository.put(mapping)
        return ExecutionHandle(
            backend=self.kind,
            execution_id=prepared.execution_id,
            target_name=prepared.target_name,
            session_id=request.session_id,
            directory_path=prepared.directory_path,
            command=prepared.command,
            image=request.image,
            repo_source=prepared.repo_source,
            clone_branch=prepared.clone_branch,
            resource_limits=prepared.resource_limits,
            persistence_mode=prepared.persistence_mode,
            workspace_mode=prepared.workspace_mode,
            metadata=mapping.metadata,
        )

    def inspect(self, target: ExecutionTarget) -> ExecutionStatus:
        mapping = self._mapping_for_target(target)
        record = self.client.get(mapping.worker_job_id)
        mapping.last_status = record.status
        mapping.started_at = record.started_at
        mapping.finished_at = record.finished_at
        mapping.metadata = {**mapping.metadata, "worker_status": record.status}
        self.repository.put(mapping)
        return self._status_from_record(mapping, record.status, message=record.error)

    def stop(self, target: ExecutionTarget, *, timeout_seconds: int = 60) -> ExecutionStatus:
        del timeout_seconds
        mapping = self._mapping_for_target(target)
        record = self.client.stop(mapping.worker_job_id)
        mapping.last_status = record.status
        mapping.finished_at = record.finished_at
        mapping.metadata = {**mapping.metadata, "worker_status": record.status}
        self.repository.put(mapping)
        return self._status_from_record(mapping, record.status, message=record.error)

    def destroy(self, target: ExecutionTarget) -> ExecutionStatus:
        mapping = self._mapping_for_target(target)
        self.client.delete(mapping.worker_job_id)
        mapping.last_status = "removed"
        mapping.finished_at = mapping.finished_at or now_utc()
        mapping.metadata = {**mapping.metadata, "worker_status": "removed"}
        self.repository.put(mapping)
        return self._status_from_record(mapping, "removed", message="container worker job deleted")

    def get_log_snapshot(
        self,
        target: ExecutionTarget,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        del repo_name, vm_user
        mapping = self._mapping_for_target(target)
        text = self.client.logs(mapping.worker_job_id)
        if lines <= 0:
            return text
        return "\n".join(text.splitlines()[-lines:]) + (
            "\n" if text.endswith("\n") and text.splitlines() else ""
        )

    def prepare_session(
        self,
        *,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> WorkerSessionRecord:
        return self.client.prepare_session(
            WorkerSessionPrepareRequest(
                session_id=session_id,
                metadata=sanitize_metadata(metadata or {}),
            )
        )

    def clone_repo(
        self,
        *,
        session_id: str,
        repo_source: str,
        clone_branch: str | None = None,
        repo_name: str | None = None,
    ) -> WorkerRepoCloneRecord:
        return self.client.clone_repo(
            session_id,
            WorkerRepoCloneRequest(
                repo_source=repo_source,
                clone_branch=clone_branch,
                repo_name=repo_name,
            ),
        )

    def write_files(
        self,
        *,
        session_id: str,
        files: list[dict[str, Any]],
    ) -> WorkerFileWriteRecord:
        return self.client.write_files(
            session_id,
            WorkerFileWriteRequest.model_validate({"files": files}),
        )

    def delete_session(self, session_id: str) -> dict[str, Any]:
        return self.client.delete_session(session_id)

    def _job_request(self, prepared: PreparedExecution) -> WorkerJobRequest:
        command = self._command_from_spec(prepared.command)
        image = str(prepared.image or self.config.container_worker_default_image).strip()
        payload: dict[str, Any] = {
            "session_id": prepared.session_id or prepared.execution_id,
            "job_id": prepared.execution_id,
            "image": image,
            "command": command,
            "metadata": sanitize_metadata(prepared.metadata),
        }
        if prepared.repo_source:
            payload["repo_source"] = prepared.repo_source
        if prepared.clone_branch:
            payload["clone_branch"] = prepared.clone_branch
        if prepared.resource_limits:
            payload["resource_limits"] = WorkerResourceLimits.model_validate(
                prepared.resource_limits
            )
        return WorkerJobRequest.model_validate(payload)

    def _command_from_spec(self, command: Any) -> list[str]:
        if command is None:
            raise ValueError("ExecutionSpec.command is required for container_worker_vm.")
        if isinstance(command, str):
            text = command.strip()
            if not text:
                raise ValueError("ExecutionSpec.command is required for container_worker_vm.")
            return ["bash", "-lc", text]
        if (
            isinstance(command, list)
            and command
            and all(isinstance(item, str) and item for item in command)
        ):
            return command
        raise ValueError("ExecutionSpec.command must be a non-empty string or list of strings.")

    def _mapping_for_target(self, target: ExecutionTarget) -> ContainerWorkerExecutionRecord:
        if isinstance(target, PreparedExecution | ExecutionHandle):
            return self.repository.resolve(target.execution_id)
        return self.repository.resolve(str(target or ""))

    def _spec_from_target(self, target: ExecutionTarget) -> ExecutionSpec:
        if isinstance(target, ExecutionHandle):
            return ExecutionSpec(
                backend=self.kind,
                target_name=target.target_name,
                session_id=target.session_id,
                directory_path=target.directory_path,
                command=target.command,
                image=target.image,
                repo_source=target.repo_source,
                clone_branch=target.clone_branch,
                resource_limits=target.resource_limits,
                persistence_mode=target.persistence_mode,
                workspace_mode=target.workspace_mode,
                metadata=target.metadata,
            )
        raw_target = str(target or "").strip()
        return ExecutionSpec(backend=self.kind, target_name=raw_target, metadata={})

    def _safe_execution_id(self, target_name: str | None) -> str:
        if target_name:
            candidate = target_name.strip()
            if SAFE_ID_PATTERN.fullmatch(candidate):
                return candidate
        return f"exec-{uuid.uuid4().hex}"

    def _status_from_record(
        self,
        mapping: ContainerWorkerExecutionRecord,
        worker_status: str,
        *,
        message: str | None,
    ) -> ExecutionStatus:
        return ExecutionStatus(
            backend=self.kind,
            execution_id=mapping.execution_id,
            target_name=mapping.target_name,
            status=self._map_status(worker_status),
            session_id=mapping.session_id,
            message=message,
            started_at=mapping.started_at,
            finished_at=mapping.finished_at,
            metadata=mapping.metadata,
        )

    def _map_status(self, worker_status: str) -> ExecutionStatusKind:
        normalized = str(worker_status or "").strip().casefold()
        if normalized in {"created", "starting"}:
            return ExecutionStatusKind.STARTING
        if normalized == "running":
            return ExecutionStatusKind.RUNNING
        if normalized in {"stopped", "exited"}:
            return ExecutionStatusKind.STOPPED
        if normalized == "failed":
            return ExecutionStatusKind.FAILED
        if normalized == "removed":
            return ExecutionStatusKind.DESTROYED
        return ExecutionStatusKind.UNKNOWN
