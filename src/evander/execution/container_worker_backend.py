from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from evander.config import GatewayConfig
from evander.worker.models import (
    SAFE_ID_PATTERN,
    WorkerFileWriteRequest,
    WorkerFileWriteRecord,
    WorkerGitWriteKeyRecord,
    WorkerGitWriteKeyRequest,
    WorkerJobRequest,
    WorkerMssqlDatabaseDeleteRecord,
    WorkerMssqlDatabaseListRecord,
    WorkerMssqlDatabaseRecord,
    WorkerMssqlDatabaseRequest,
    WorkerRepoCloneRecord,
    WorkerRepoCloneRequest,
    WorkerResourceLimits,
    WorkerSessionPrepareRequest,
    WorkerSessionRecord,
    WorkerTaskWorktreeRecord,
    WorkerTaskWorktreeRequest,
    now_utc,
)
from evander.worker.security import sanitize_metadata, sanitize_text

from .backend import ExecutionTarget
from .container_worker_client import ContainerWorkerClient, ContainerWorkerClientError
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
            codex_turn=spec.codex_turn,
            image=spec.image or self.config.container_worker_default_image,
            repo_source=spec.repo_source,
            clone_branch=spec.clone_branch,
            working_directory=spec.working_directory,
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
            metadata=self._metadata_from_worker_record(
                {**prepared.metadata, "image": record.image},
                record,
            ),
        )
        self.repository.put(mapping)
        return ExecutionHandle(
            backend=self.kind,
            execution_id=prepared.execution_id,
            target_name=prepared.target_name,
            session_id=request.session_id,
            directory_path=prepared.directory_path,
            command=prepared.command,
            codex_turn=prepared.codex_turn,
            image=request.image,
            repo_source=prepared.repo_source,
            clone_branch=prepared.clone_branch,
            working_directory=prepared.working_directory,
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
        mapping.metadata = self._metadata_from_worker_record(mapping.metadata, record)
        self.repository.put(mapping)
        return self._status_from_record(mapping, record.status, message=record.error)

    def stop(self, target: ExecutionTarget, *, timeout_seconds: int = 60) -> ExecutionStatus:
        del timeout_seconds
        mapping = self._mapping_for_target(target)
        record = self.client.stop(mapping.worker_job_id)
        mapping.last_status = record.status
        mapping.finished_at = record.finished_at
        mapping.metadata = self._metadata_from_worker_record(mapping.metadata, record)
        self.repository.put(mapping)
        return self._status_from_record(mapping, record.status, message=record.error)

    def destroy(self, target: ExecutionTarget) -> ExecutionStatus:
        mapping = self._mapping_for_target(target)
        attempted_at = now_utc()
        try:
            self.client.delete(mapping.worker_job_id)
        except ContainerWorkerClientError as exc:
            self._mark_cleanup_failed(
                mapping,
                error=sanitize_text(str(exc)),
                attempted_at=attempted_at,
            )
            raise
        completed_at = now_utc()
        self._mark_cleanup_succeeded(
            mapping,
            attempted_at=attempted_at,
            completed_at=completed_at,
        )
        return self._status_from_record(mapping, "removed", message="container worker job deleted")

    def get_log_snapshot(
        self,
        target: ExecutionTarget,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
        mode: str | None = None,
    ) -> str:
        del repo_name, vm_user
        mapping = self._mapping_for_target(target)
        text = self.client.logs(mapping.worker_job_id, mode=mode)
        if lines <= 0:
            return text
        return "\n".join(text.splitlines()[-lines:]) + (
            "\n" if text.endswith("\n") and text.splitlines() else ""
        )

    def get_log_page(
        self,
        target: ExecutionTarget,
        *,
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
        mode: str | None = None,
    ) -> dict[str, object]:
        mapping = self._mapping_for_target(target)
        text, metadata = self.client.logs_page(
            mapping.worker_job_id,
            mode=mode,
            limit=limit,
            offset=offset,
            query=query,
        )
        if metadata.get("paged"):
            return {
                "text": text,
                "limit": int(metadata.get("limit") or limit),
                "offset": int(metadata.get("offset") or offset),
                "returned_lines": int(
                    metadata.get("returned_lines")
                    if metadata.get("returned_lines") is not None
                    else len(text.splitlines())
                ),
                "total_lines": metadata.get("total_lines"),
                "start_line": metadata.get("start_line"),
                "end_line": metadata.get("end_line"),
                "has_more_before": bool(metadata.get("has_more_before")),
                "query": str(query or "").strip() or None,
            }
        return _log_page_from_end(text, limit=limit, offset=offset, query=query)

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
        reconcile_existing_remote: bool = False,
    ) -> WorkerRepoCloneRecord:
        return self.client.clone_repo(
            session_id,
            WorkerRepoCloneRequest(
                repo_source=repo_source,
                clone_branch=clone_branch,
                repo_name=repo_name,
                reconcile_existing_remote=reconcile_existing_remote,
            ),
        )

    def prepare_task_worktree(
        self,
        *,
        session_id: str,
        repo_name: str,
        worktree_name: str,
        branch: str,
        base_ref: str,
    ) -> WorkerTaskWorktreeRecord:
        return self.client.prepare_task_worktree(
            session_id,
            WorkerTaskWorktreeRequest(
                repo_name=repo_name,
                worktree_name=worktree_name,
                branch=branch,
                base_ref=base_ref,
            ),
        )

    def provision_git_write_key(
        self,
        *,
        session_id: str,
        git_host: str,
        git_user: str,
        git_ssh_alias: str,
    ) -> WorkerGitWriteKeyRecord:
        return self.client.provision_git_write_key(
            session_id,
            WorkerGitWriteKeyRequest(
                git_host=git_host,
                git_user=git_user,
                git_ssh_alias=git_ssh_alias,
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

    def read_session_artifact_text(
        self,
        *,
        session_id: str,
        relative_path: str,
    ) -> str:
        return self.client.read_session_artifact_text(session_id, relative_path)

    def provision_mssql_database(
        self,
        *,
        session_id: str,
        logical_name: str,
    ) -> WorkerMssqlDatabaseRecord:
        record = self.client.provision_mssql_database(
            session_id,
            WorkerMssqlDatabaseRequest(logical_name=logical_name),
        )
        return self._apply_mssql_endpoint_config(record)

    def list_mssql_databases(
        self,
        *,
        session_id: str,
    ) -> WorkerMssqlDatabaseListRecord:
        return self.client.list_mssql_databases(session_id)

    def drop_mssql_database(
        self,
        *,
        session_id: str,
        logical_name: str,
    ) -> WorkerMssqlDatabaseDeleteRecord:
        return self.client.drop_mssql_database(session_id, logical_name)

    def delete_session(self, session_id: str) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        list_by_session = getattr(self.repository, "list_by_session", None)
        if callable(list_by_session):
            mappings = list(list_by_session(normalized_session_id))
        else:
            read_all = getattr(self.repository, "read_all", None)
            if not callable(read_all):
                mappings = []
            else:
                mappings = [
                    record
                    for record in read_all().values()
                    if record.session_id == normalized_session_id
                ]
        attempted_at = now_utc()
        try:
            response = self.client.delete_session(normalized_session_id)
        except ContainerWorkerClientError as exc:
            error = sanitize_text(str(exc))
            self._mark_session_cleanup_failed(
                mappings,
                error=error,
                attempted_at=attempted_at,
            )
            raise
        completed_at = now_utc()
        self._mark_session_cleanup_succeeded(
            mappings,
            attempted_at=attempted_at,
            completed_at=completed_at,
        )
        return response

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
        if prepared.codex_turn is not None:
            payload["codex_turn"] = prepared.codex_turn.model_dump(mode="json", exclude_none=True)
        if prepared.repo_source:
            payload["repo_source"] = prepared.repo_source
        if prepared.clone_branch:
            payload["clone_branch"] = prepared.clone_branch
        if prepared.working_directory:
            payload["working_directory"] = prepared.working_directory
        if prepared.resource_limits:
            payload["resource_limits"] = WorkerResourceLimits.model_validate(
                prepared.resource_limits
            )
        return WorkerJobRequest.model_validate(payload)

    def _apply_mssql_endpoint_config(
        self,
        record: WorkerMssqlDatabaseRecord,
    ) -> WorkerMssqlDatabaseRecord:
        server = str(self.config.container_worker_mssql_host or record.server).strip()
        server = server or record.server
        port = int(self.config.container_worker_mssql_port)
        if server == record.server and port == record.port:
            return record
        connection_string = (
            f"Server=tcp:{server},{port};"
            f"Database={record.database_name};"
            f"User ID={record.username};"
            f"Password={record.password};"
            "Encrypt=True;TrustServerCertificate=True"
        )
        return record.model_copy(
            update={
                "server": server,
                "port": port,
                "connection_string": connection_string,
            }
        )

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
                codex_turn=target.codex_turn,
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
        outcome = _failed_codex_outcome(mapping.metadata)
        status = self._map_status(worker_status)
        if outcome is not None:
            status = ExecutionStatusKind.FAILED
            message = message or str(outcome.get("message") or "Codex execution failed")
        return ExecutionStatus(
            backend=self.kind,
            execution_id=mapping.execution_id,
            target_name=mapping.target_name,
            status=status,
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
        if normalized == "cleanup_failed":
            return ExecutionStatusKind.FAILED
        if normalized == "removed":
            return ExecutionStatusKind.DESTROYED
        return ExecutionStatusKind.UNKNOWN

    @staticmethod
    def _metadata_from_worker_record(
        existing_metadata: dict[str, Any],
        record: Any,
    ) -> dict[str, Any]:
        metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        worker_metadata = (
            dict(record.metadata) if isinstance(getattr(record, "metadata", None), dict) else {}
        )
        for key in (
            "codex_outcome",
            "agent_provider",
            "model_provider_mode",
            "model",
            "codex_auth_mode",
            "codex_subscription_thread_id",
            "codex_subscription_turn_id",
            "codex_subscription_exit_code",
            "exit_code",
            "worker_terminal_kind",
        ):
            if key in worker_metadata:
                metadata[key] = worker_metadata[key]
        if getattr(record, "error", None):
            metadata["worker_error"] = str(record.error)
        metadata["worker_status"] = record.status
        return sanitize_metadata(metadata)

    def _mark_cleanup_succeeded(
        self,
        mapping: ContainerWorkerExecutionRecord,
        *,
        attempted_at: datetime,
        completed_at: datetime,
    ) -> None:
        mapping.last_status = "removed"
        mapping.finished_at = mapping.finished_at or completed_at
        mapping.cleanup_status = "cleanup_succeeded"
        mapping.cleanup_attempted_at = attempted_at
        mapping.cleanup_completed_at = completed_at
        mapping.cleanup_error = None
        mapping.metadata = {
            **mapping.metadata,
            "worker_status": "removed",
            "cleanup_status": "cleanup_succeeded",
            "cleanup_attempted_at": attempted_at.isoformat(),
            "cleanup_completed_at": completed_at.isoformat(),
        }
        self.repository.put(mapping)

    def _mark_cleanup_failed(
        self,
        mapping: ContainerWorkerExecutionRecord,
        *,
        error: str,
        attempted_at: datetime,
    ) -> None:
        completed_at = now_utc()
        sanitized_error = sanitize_text(error)
        mapping.last_status = "cleanup_failed"
        mapping.finished_at = completed_at
        mapping.cleanup_status = "cleanup_failed"
        mapping.cleanup_attempted_at = attempted_at
        mapping.cleanup_completed_at = completed_at
        mapping.cleanup_error = sanitized_error
        mapping.metadata = {
            **mapping.metadata,
            "worker_status": "cleanup_failed",
            "cleanup_status": "cleanup_failed",
            "cleanup_attempted_at": attempted_at.isoformat(),
            "cleanup_completed_at": completed_at.isoformat(),
            "cleanup_error": sanitized_error,
        }
        self.repository.put(mapping)

    def _mark_session_cleanup_succeeded(
        self,
        mappings: list[ContainerWorkerExecutionRecord],
        *,
        attempted_at: datetime,
        completed_at: datetime,
    ) -> None:
        for mapping in mappings:
            self._mark_cleanup_succeeded(
                mapping,
                attempted_at=attempted_at,
                completed_at=completed_at,
            )

    def _mark_session_cleanup_failed(
        self,
        mappings: list[ContainerWorkerExecutionRecord],
        *,
        error: str,
        attempted_at: datetime,
    ) -> None:
        for mapping in mappings:
            self._mark_cleanup_failed(
                mapping,
                error=error,
                attempted_at=attempted_at,
            )


def _failed_codex_outcome(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    outcome = metadata.get("codex_outcome")
    if not isinstance(outcome, dict):
        return None
    status = str(outcome.get("status") or "").strip().casefold()
    if status != "failed":
        return None
    return outcome


def _log_page_from_end(
    text: str,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
) -> dict[str, object]:
    lines = text.splitlines()
    normalized_query = str(query or "").strip()
    if normalized_query:
        needle = normalized_query.casefold()
        lines = [line for line in lines if needle in line.casefold()]
    total_lines = len(lines)
    normalized_limit = max(1, int(limit))
    normalized_offset = max(0, int(offset))
    end_index = max(0, total_lines - normalized_offset)
    start_index = max(0, end_index - normalized_limit)
    page_lines = lines[start_index:end_index]
    return {
        "text": "\n".join(page_lines) + ("\n" if page_lines else ""),
        "limit": normalized_limit,
        "offset": normalized_offset,
        "returned_lines": len(page_lines),
        "total_lines": total_lines,
        "start_line": start_index + 1 if page_lines else 0,
        "end_line": end_index if page_lines else 0,
        "has_more_before": start_index > 0,
        "query": normalized_query or None,
    }
