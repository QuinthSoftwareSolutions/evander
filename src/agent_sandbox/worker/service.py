from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

from starlette.applications import Starlette

from .docker_runtime import DockerContainerRuntime
from .models import (
    DEFAULT_WORKER_ROOT,
    WorkerFileWriteRequest,
    WorkerFileWriteRecord,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerRepoCloneRecord,
    WorkerRepoCloneRequest,
    SAFE_ID_PATTERN,
    WorkerSessionPrepareRequest,
    WorkerSessionPaths,
    WorkerSessionRecord,
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
        read_only_mounts: Sequence[str] | None = None,
        allowed_git_source_prefixes: Sequence[str] | None = None,
        git_ssh_source_dir: Path | None = None,
        token: str | None = None,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.worker_root = worker_root
        self.store = WorkerJobStore(runtime_dir / "worker-jobs.json")
        self.allowed_git_source_prefixes = tuple(
            prefix.strip()
            for prefix in (allowed_git_source_prefixes or ())
            if str(prefix).strip()
        )
        self.git_ssh_source_dir = Path(git_ssh_source_dir) if git_ssh_source_dir else None
        self.container_runtime = container_runtime or DockerContainerRuntime(
            docker_network=docker_network,
            sandbox_profile=sandbox_profile,
            read_only_mounts=read_only_mounts,
        )
        self.token = token if token is not None else os.environ.get("AGENT_SANDBOX_WORKER_TOKEN")

    def create_app(self) -> Starlette:

        from .api import build_worker_app

        return build_worker_app(self)

    def start_job(self, request: WorkerJobRequest) -> WorkerJobRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, request.session_id)
        if request.repo_source:
            self._ensure_allowed_git_source(request.repo_source)
        self._sync_git_ssh_into_agent_home(
            paths, required=_repo_source_requires_git_ssh(request.repo_source)
        )
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

    def prepare_session(self, request: WorkerSessionPrepareRequest) -> WorkerSessionRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, request.session_id)
        self._sync_git_ssh_into_agent_home(paths, required=False)
        metadata = sanitize_metadata(request.metadata)
        if metadata:
            current = self._read_session_metadata(paths)
            current["metadata"] = {**dict(current.get("metadata") or {}), **metadata}
            self._write_session_metadata(paths, current)
        return self._session_record(paths, metadata=metadata)

    def clone_repo(
        self,
        session_id: str,
        request: WorkerRepoCloneRequest,
    ) -> WorkerRepoCloneRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, session_id)
        self._ensure_allowed_git_source(request.repo_source)
        self._sync_git_ssh_into_agent_home(
            paths, required=_repo_source_requires_git_ssh(request.repo_source)
        )
        cloned = self._clone_repo_into_workspace(
            paths=paths,
            repo_source=request.repo_source,
            clone_branch=request.clone_branch,
        )
        return WorkerRepoCloneRecord(
            session_id=session_id,
            target_dir="/workspace",
            guest_repo_name=self._repo_name(request.repo_source),
            remote_url=request.repo_source,
            cloned=cloned,
        )

    def write_files(
        self,
        session_id: str,
        request: WorkerFileWriteRequest,
    ) -> WorkerFileWriteRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, session_id)
        copied_paths: list[str] = []
        for entry in request.files:
            root, relative_path, container_path = self._resolve_container_destination(
                paths=paths,
                destination_path=entry.destination_path,
            )
            destination = root / relative_path
            self._ensure_resolved_destination_stays_under_root(
                root=root,
                destination=destination,
                container_path=container_path,
            )
            if destination.exists() and not entry.overwrite:
                raise ValueError(f"destination already exists: {container_path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_resolved_destination_stays_under_root(
                root=root,
                destination=destination,
                container_path=container_path,
            )
            destination.write_text(entry.content, encoding="utf-8")
            if entry.mode is not None:
                destination.chmod(int(self._normalize_mode(entry.mode), 8))
            copied_paths.append(container_path)
        return WorkerFileWriteRecord(
            session_id=session_id,
            copied_count=len(copied_paths),
            copied_paths=copied_paths,
            roots={"workspace": "/workspace", "agent_home": "/home/agent"},
        )

    def delete_session(self, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not SAFE_ID_PATTERN.fullmatch(normalized_session_id):
            raise ValueError(
                "session_id must contain only letters, numbers, dots, underscores, or dashes"
            )

        removed_any = False
        for record in list(self.store.read_all().values()):
            if record.session_id != normalized_session_id:
                continue
            if record.status != "removed":
                self.container_runtime.remove_job(record)
            self.store.delete(record.job_id)
            removed_any = True

        session_root = self.worker_root / "sessions" / normalized_session_id
        if session_root.is_symlink() or session_root.is_file():
            session_root.unlink()
            removed_any = True
        elif session_root.exists():
            shutil.rmtree(session_root)
            removed_any = True
        logger.info(
            "worker_session_deleted session_id=%s removed=%s",
            normalized_session_id,
            removed_any,
        )
        return removed_any

    def _log_path(self, record: WorkerJobRecord) -> Path:
        return Path(record.logs_path) / f"{record.job_id}.log"

    def _clone_repo_if_workspace_empty(
        self,
        request: WorkerJobRequest,
        paths: WorkerSessionPaths,
    ) -> None:
        if not request.repo_source:
            return
        self._clone_repo_into_workspace(
            paths=paths,
            repo_source=request.repo_source,
            clone_branch=request.clone_branch,
        )

    def _clone_repo_into_workspace(
        self,
        *,
        paths: WorkerSessionPaths,
        repo_source: str,
        clone_branch: str | None,
    ) -> bool:
        self._ensure_allowed_git_source(repo_source)
        if any(paths.workspace.iterdir()):
            return False
        args = ["git", "clone"]
        if clone_branch:
            args.extend(["--branch", clone_branch])
        args.extend([repo_source, "."])
        subprocess.run(args, cwd=paths.workspace, check=True, capture_output=True, text=True)
        return True

    def _ensure_allowed_git_source(self, repo_source: str) -> None:
        if not self.allowed_git_source_prefixes:
            raise ValueError(
                "worker Git source prefixes are not configured; refusing repo_source"
            )
        if not any(repo_source.startswith(prefix) for prefix in self.allowed_git_source_prefixes):
            allowed = ", ".join(self.allowed_git_source_prefixes)
            raise ValueError(
                f"repo_source is not allowed for this worker: {repo_source!r}; "
                f"allowed prefixes: {allowed}"
            )

    def _sync_git_ssh_into_agent_home(
        self,
        paths: WorkerSessionPaths,
        *,
        required: bool,
    ) -> None:
        if self.git_ssh_source_dir is None:
            if required:
                raise ValueError("worker Git SSH source directory is not configured")
            return
        source_dir = self.git_ssh_source_dir
        required_files = ("agent-sandbox-git", "config")
        missing = [name for name in required_files if not (source_dir / name).is_file()]
        if missing:
            if required:
                raise ValueError(
                    "worker Git SSH source directory is missing required files: "
                    + ", ".join(missing)
                )
            return
        target_dir = paths.agent_home / ".ssh"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir.chmod(0o700)
        for name in ("agent-sandbox-git", "agent-sandbox-git.pub", "config", "known_hosts"):
            source = source_dir / name
            if not source.is_file():
                continue
            destination = target_dir / name
            shutil.copy2(source, destination)
            if name.endswith(".pub"):
                destination.chmod(0o644)
            else:
                destination.chmod(0o600)

    def _session_record(
        self,
        paths: WorkerSessionPaths,
        *,
        metadata: dict[str, object] | None = None,
    ) -> WorkerSessionRecord:
        return WorkerSessionRecord(
            session_id=paths.session_id,
            root_path=str(paths.root),
            workspace_path=str(paths.workspace),
            agent_home_path=str(paths.agent_home),
            artifacts_path=str(paths.artifacts),
            logs_path=str(paths.logs),
            metadata_path=str(paths.metadata_path),
            metadata=dict(metadata or {}),
        )

    def _read_session_metadata(self, paths: WorkerSessionPaths) -> dict[str, object]:
        try:
            import json

            payload = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_session_metadata(
        self,
        paths: WorkerSessionPaths,
        payload: dict[str, object],
    ) -> None:
        import json

        paths.metadata_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _resolve_container_destination(
        self,
        *,
        paths: WorkerSessionPaths,
        destination_path: str,
    ) -> tuple[Path, Path, str]:
        normalized = destination_path.strip()
        for container_root, host_root in (
            ("/workspace", paths.workspace),
            ("/home/agent", paths.agent_home),
        ):
            if normalized == container_root:
                raise ValueError(f"destination_path must name a file below {container_root}")
            prefix = f"{container_root}/"
            if not normalized.startswith(prefix):
                continue
            relative_text = normalized[len(prefix) :]
            relative = Path(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"destination_path must stay below {container_root}")
            return host_root, relative, f"{container_root}/{relative.as_posix()}"
        raise ValueError("destination_path must be under /workspace or /home/agent")

    @staticmethod
    def _ensure_resolved_destination_stays_under_root(
        *,
        root: Path,
        destination: Path,
        container_path: str,
    ) -> None:
        resolved_root = root.resolve()
        resolved_destination = destination.resolve(strict=False)
        try:
            resolved_destination.relative_to(resolved_root)
        except ValueError as exc:
            container_root = (
                "/home/agent" if container_path.startswith("/home/agent/") else "/workspace"
            )
            raise ValueError(f"destination_path must stay below {container_root}") from exc

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = mode.strip()
        if not normalized or any(char not in "01234567" for char in normalized):
            raise ValueError(f"mode must be octal digits: {mode!r}")
        return normalized

    @staticmethod
    def _repo_name(repo_source: str) -> str:
        parsed = urlparse(repo_source)
        raw_path = parsed.path if parsed.scheme else repo_source
        candidate = raw_path.rstrip("/").rsplit("/", 1)[-1].strip() or "workspace"
        if candidate.endswith(".git"):
            candidate = candidate[:-4]
        return candidate or "workspace"


def _repo_source_requires_git_ssh(repo_source: str | None) -> bool:
    if not repo_source:
        return False
    normalized = repo_source.strip()
    return normalized.startswith("ssh://") or bool(
        re.match(r"^[^/@:\s]+@[^:\s]+:.+", normalized)
    )
