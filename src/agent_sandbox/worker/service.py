from __future__ import annotations

import logging
import os
import subprocess
import uuid
from pathlib import Path

from starlette.applications import Starlette

from .docker_runtime import DockerContainerRuntime
from .models import (
    DEFAULT_WORKER_ROOT,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerSessionPaths,
    now_utc,
)
from .runtime import ContainerRuntime
from .sandbox_profile import WorkerSandboxProfile
from .security import safe_subprocess_error, sanitize_metadata, sanitize_text
from .store import WorkerJobStore

logger = logging.getLogger(__name__)


class WorkerDaemon:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        worker_root: Path = DEFAULT_WORKER_ROOT,
        container_runtime: ContainerRuntime | None = None,
        docker_network: str | None = "none",
        sandbox_profile: WorkerSandboxProfile | None = None,
        token: str | None = None,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.worker_root = worker_root
        self.store = WorkerJobStore(runtime_dir / "worker-jobs.json")
        self.container_runtime = container_runtime or DockerContainerRuntime(
            docker_network=docker_network,
            sandbox_profile=sandbox_profile,
        )
        self.token = token if token is not None else os.environ.get("AGENT_SANDBOX_WORKER_TOKEN")

    def create_app(self) -> Starlette:

        from .api import build_worker_app

        return build_worker_app(self)

    def start_job(self, request: WorkerJobRequest) -> WorkerJobRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, request.session_id)
        self._clone_repo_if_workspace_empty(request, paths)
        job_id = request.job_id or uuid.uuid4().hex
        container_name = f"agent-worker-{job_id}"
        record = WorkerJobRecord(
            job_id=job_id,
            session_id=request.session_id,
            image=request.image,
            command=request.command,
            container_name=container_name,
            status="starting",
            started_at=now_utc(),
            workspace_path=str(paths.workspace),
            agent_home_path=str(paths.agent_home),
            artifacts_path=str(paths.artifacts),
            logs_path=str(paths.logs),
            metadata=sanitize_metadata(request.metadata),
        )
        self._log_path(record).touch(exist_ok=True)
        self.store.put(record)
        try:
            container_id = self.container_runtime.start_job(request, record, paths)
        except Exception as exc:
            record.status = "failed"
            record.finished_at = now_utc()
            record.error = sanitize_text(str(exc))
            self.store.put(record)
            logger.exception(
                "worker_job_start_failed job_id=%s session_id=%s", job_id, request.session_id
            )
            raise
        record.container_id = container_id
        record.status = "running"
        self.store.put(record)
        logger.info(
            "worker_job_started job_id=%s session_id=%s container_id=%s",
            record.job_id,
            record.session_id,
            record.container_id,
        )
        return record

    def inspect_job(self, record: WorkerJobRecord) -> WorkerJobRecord:
        try:
            info = self.container_runtime.inspect_job(record)
        except subprocess.CalledProcessError as exc:
            record.error = safe_subprocess_error(exc)
            self.store.put(record)
            return record
        status = str(info.get("status") or record.status)
        record.status = status
        if status in {"exited", "stopped", "failed", "removed"} and record.finished_at is None:
            record.finished_at = now_utc()
        self.store.put(record)
        return record

    def stop_job(self, record: WorkerJobRecord) -> WorkerJobRecord:
        self.container_runtime.stop_job(record)
        record.status = "stopped"
        record.finished_at = now_utc()
        self.store.put(record)
        logger.info("worker_job_stopped job_id=%s session_id=%s", record.job_id, record.session_id)
        return record

    def remove_job(self, record: WorkerJobRecord) -> None:
        self.container_runtime.remove_job(record)
        record.status = "removed"
        record.finished_at = record.finished_at or now_utc()
        self.store.put(record)
        logger.info("worker_job_removed job_id=%s session_id=%s", record.job_id, record.session_id)

    def get_logs(self, record: WorkerJobRecord) -> str:
        log_path = self._log_path(record)
        try:
            text = self.container_runtime.get_logs(record)
        except subprocess.CalledProcessError:
            return log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        log_path.write_text(text, encoding="utf-8")
        return text

    def _log_path(self, record: WorkerJobRecord) -> Path:
        return Path(record.logs_path) / f"{record.job_id}.log"

    def _clone_repo_if_workspace_empty(
        self,
        request: WorkerJobRequest,
        paths: WorkerSessionPaths,
    ) -> None:
        if not request.repo_source:
            return
        if any(paths.workspace.iterdir()):
            return
        args = ["git", "clone"]
        if request.clone_branch:
            args.extend(["--branch", request.clone_branch])
        args.extend([request.repo_source, "."])
        subprocess.run(args, cwd=paths.workspace, check=True, capture_output=True, text=True)
