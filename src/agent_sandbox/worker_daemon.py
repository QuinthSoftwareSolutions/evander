from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import uvicorn
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .storage import JsonStore

logger = logging.getLogger(__name__)

DEFAULT_WORKER_ROOT = Path("/var/lib/agent-worker")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
SENSITIVE_METADATA_KEYS = {
    "authorization",
    "token",
    "access_token",
    "api_key",
    "password",
    "secret",
    "pat",
    "env",
    "environment",
}


def _now() -> datetime:
    return datetime.now(UTC)


class WorkerResourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory: str | None = None
    cpus: float | None = Field(default=None, gt=0)
    pids_limit: int | None = Field(default=None, gt=0)

    @field_validator("memory", mode="before")
    @classmethod
    def normalize_memory(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class WorkerJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    job_id: str | None = None
    image: str
    command: list[str]
    repo_source: str | None = None
    clone_branch: str | None = None
    resource_limits: WorkerResourceLimits = Field(default_factory=WorkerResourceLimits)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "job_id", mode="before")
    @classmethod
    def normalize_safe_id(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            raise ValueError("must not be empty")
        if not SAFE_ID_PATTERN.fullmatch(text):
            raise ValueError("must contain only letters, numbers, dots, underscores, or dashes")
        return text

    @field_validator("image", mode="before")
    @classmethod
    def normalize_image(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("image is required")
        return text

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("command must contain at least one argument")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError("command entries must be non-empty strings")
        return value

    @field_validator("repo_source", "clone_branch", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="before")
    @classmethod
    def reject_raw_environment_injection(cls, data: object) -> object:
        if isinstance(data, dict):
            forbidden = {"env", "environment", "environment_variables"}
            present = sorted(forbidden.intersection(data))
            if present:
                raise ValueError(f"raw environment injection is not supported: {', '.join(present)}")
        return data


class WorkerSessionPaths(BaseModel):
    session_id: str
    root: Path
    workspace: Path
    agent_home: Path
    artifacts: Path
    logs: Path
    metadata_path: Path

    @classmethod
    def resolve(cls, worker_root: Path, session_id: str) -> "WorkerSessionPaths":
        if not SAFE_ID_PATTERN.fullmatch(session_id):
            raise ValueError("session_id must contain only letters, numbers, dots, underscores, or dashes")
        root = worker_root / "sessions" / session_id
        paths = cls(
            session_id=session_id,
            root=root,
            workspace=root / "workspace",
            agent_home=root / "agent_home",
            artifacts=root / "artifacts",
            logs=root / "logs",
            metadata_path=root / "metadata.json",
        )
        paths.ensure()
        return paths

    def ensure(self) -> None:
        for path in (self.workspace, self.agent_home, self.artifacts, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self.metadata_path.write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "created_at": _now().isoformat(),
                        "workspace": str(self.workspace),
                        "agent_home": str(self.agent_home),
                        "artifacts": str(self.artifacts),
                        "logs": str(self.logs),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )


class WorkerJobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    session_id: str
    image: str
    command: list[str]
    container_name: str
    container_id: str | None = None
    status: str = "created"
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    workspace_path: str
    agent_home_path: str
    artifacts_path: str
    logs_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WorkerJobStore:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, WorkerJobRecord)

    @property
    def path(self) -> Path:
        return self._store.path

    def get(self, job_id: str) -> WorkerJobRecord | None:
        return self._store.get(job_id)

    def put(self, record: WorkerJobRecord) -> None:
        self._store.put(record.job_id, record)

    def delete(self, job_id: str) -> None:
        self._store.delete(job_id)

    def read_all(self) -> dict[str, WorkerJobRecord]:
        return self._store.read_all()


class ContainerRuntime(Protocol):
    def start_job(
        self,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> str: ...

    def inspect_job(self, record: WorkerJobRecord) -> dict[str, Any]: ...

    def stop_job(self, record: WorkerJobRecord) -> None: ...

    def remove_job(self, record: WorkerJobRecord) -> None: ...

    def get_logs(self, record: WorkerJobRecord) -> str: ...


class DockerContainerRuntime:
    def __init__(self, *, docker_network: str = "none", docker_binary: str = "docker") -> None:
        if docker_network == "host":
            raise ValueError("docker_network='host' is not allowed")
        self.docker_network = docker_network or "none"
        self.docker_binary = docker_binary

    def start_job(
        self,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> str:
        args = self._build_run_args(request=request, record=record, paths=paths)
        completed = subprocess.run(args, check=True, capture_output=True, text=True)
        return completed.stdout.strip()

    def inspect_job(self, record: WorkerJobRecord) -> dict[str, Any]:
        target = record.container_id or record.container_name
        completed = subprocess.run(
            [self.docker_binary, "inspect", target],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout or "[]")
        if not payload:
            return {"status": "unknown"}
        state = payload[0].get("State", {})
        return {
            "status": _docker_state_to_status(state),
            "raw_state": {
                "status": state.get("Status"),
                "running": state.get("Running"),
                "exit_code": state.get("ExitCode"),
            },
        }

    def stop_job(self, record: WorkerJobRecord) -> None:
        subprocess.run(
            [self.docker_binary, "stop", record.container_id or record.container_name],
            check=True,
            capture_output=True,
            text=True,
        )

    def remove_job(self, record: WorkerJobRecord) -> None:
        subprocess.run(
            [self.docker_binary, "rm", "-f", record.container_id or record.container_name],
            check=True,
            capture_output=True,
            text=True,
        )

    def get_logs(self, record: WorkerJobRecord) -> str:
        completed = subprocess.run(
            [self.docker_binary, "logs", record.container_id or record.container_name],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    def _build_run_args(
        self,
        *,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> list[str]:
        args = [
            self.docker_binary,
            "run",
            "-d",
            "--name",
            record.container_name,
            "--rm=false",
            "--cap-drop",
            "ALL",
            "--network",
            self.docker_network,
            "-v",
            f"{paths.workspace}:/workspace",
            "-v",
            f"{paths.agent_home}:/home/agent",
            "-v",
            f"{paths.artifacts}:/artifacts",
            "-w",
            "/workspace",
        ]
        if request.resource_limits.memory:
            args.extend(["--memory", request.resource_limits.memory])
        if request.resource_limits.cpus is not None:
            args.extend(["--cpus", str(request.resource_limits.cpus)])
        if request.resource_limits.pids_limit is not None:
            args.extend(["--pids-limit", str(request.resource_limits.pids_limit)])
        args.append(request.image)
        args.extend(request.command)
        return args


class WorkerDaemon:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        worker_root: Path = DEFAULT_WORKER_ROOT,
        container_runtime: ContainerRuntime | None = None,
        docker_network: str = "none",
        token: str | None = None,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.worker_root = worker_root
        self.store = WorkerJobStore(runtime_dir / "worker-jobs.json")
        self.container_runtime = container_runtime or DockerContainerRuntime(
            docker_network=docker_network
        )
        self.token = token if token is not None else os.environ.get("AGENT_SANDBOX_WORKER_TOKEN")

    def create_app(self) -> Starlette:
        daemon = self

        async def health(_: Request) -> JSONResponse:
            return JSONResponse({"status": "ok"})

        async def create_job(request: Request) -> JSONResponse:
            try:
                payload = await request.json()
                job_request = WorkerJobRequest.model_validate(payload)
                record = daemon.start_job(job_request)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            except subprocess.CalledProcessError as exc:
                return JSONResponse({"error": _safe_subprocess_error(exc)}, status_code=502)
            return JSONResponse(record.model_dump(mode="json"), status_code=201)

        async def get_job(request: Request) -> JSONResponse:
            record = daemon.store.get(request.path_params["job_id"])
            if record is None:
                return JSONResponse({"error": "job not found"}, status_code=404)
            record = daemon.inspect_job(record)
            return JSONResponse(record.model_dump(mode="json"))

        async def stop_job(request: Request) -> JSONResponse:
            record = daemon.store.get(request.path_params["job_id"])
            if record is None:
                return JSONResponse({"error": "job not found"}, status_code=404)
            try:
                record = daemon.stop_job(record)
            except subprocess.CalledProcessError as exc:
                return JSONResponse({"error": _safe_subprocess_error(exc)}, status_code=502)
            return JSONResponse(record.model_dump(mode="json"))

        async def delete_job(request: Request) -> JSONResponse:
            record = daemon.store.get(request.path_params["job_id"])
            if record is None:
                return JSONResponse({"error": "job not found"}, status_code=404)
            try:
                daemon.remove_job(record)
            except subprocess.CalledProcessError as exc:
                return JSONResponse({"error": _safe_subprocess_error(exc)}, status_code=502)
            return JSONResponse({"deleted": True, "job_id": record.job_id})

        async def get_logs(request: Request) -> Response:
            record = daemon.store.get(request.path_params["job_id"])
            if record is None:
                return JSONResponse({"error": "job not found"}, status_code=404)
            text = daemon.get_logs(record)
            return PlainTextResponse(text)

        async def get_artifacts(request: Request) -> JSONResponse:
            record = daemon.store.get(request.path_params["job_id"])
            if record is None:
                return JSONResponse({"error": "job not found"}, status_code=404)
            return JSONResponse({"artifacts": _list_relative_files(Path(record.artifacts_path))})

        app = Starlette(
            debug=False,
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route("/v1/jobs", create_job, methods=["POST"]),
                Route("/v1/jobs/{job_id}", get_job, methods=["GET"]),
                Route("/v1/jobs/{job_id}/stop", stop_job, methods=["POST"]),
                Route("/v1/jobs/{job_id}", delete_job, methods=["DELETE"]),
                Route("/v1/jobs/{job_id}/logs", get_logs, methods=["GET"]),
                Route("/v1/jobs/{job_id}/artifacts", get_artifacts, methods=["GET"]),
            ],
        )
        app.add_middleware(WorkerAuthMiddleware, token=self.token)
        return app

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
            started_at=_now(),
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
            record.finished_at = _now()
            record.error = _sanitize_text(str(exc))
            self.store.put(record)
            logger.exception("worker_job_start_failed job_id=%s session_id=%s", job_id, request.session_id)
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
            record.error = _safe_subprocess_error(exc)
            self.store.put(record)
            return record
        status = str(info.get("status") or record.status)
        record.status = status
        if status in {"exited", "stopped", "failed", "removed"} and record.finished_at is None:
            record.finished_at = _now()
        self.store.put(record)
        return record

    def stop_job(self, record: WorkerJobRecord) -> WorkerJobRecord:
        self.container_runtime.stop_job(record)
        record.status = "stopped"
        record.finished_at = _now()
        self.store.put(record)
        logger.info("worker_job_stopped job_id=%s session_id=%s", record.job_id, record.session_id)
        return record

    def remove_job(self, record: WorkerJobRecord) -> None:
        self.container_runtime.remove_job(record)
        record.status = "removed"
        record.finished_at = record.finished_at or _now()
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


class WorkerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Starlette, *, token: str | None) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        if not self.token:
            return JSONResponse({"error": "worker token is not configured"}, status_code=503)
        expected = f"Bearer {self.token}"
        if request.headers.get("authorization") != expected:
            return JSONResponse({"error": "missing or invalid bearer token"}, status_code=401)
        return await call_next(request)


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized = key.lower()
        if normalized in SENSITIVE_METADATA_KEYS or any(
            marker in normalized for marker in ("token", "secret", "password", "authorization")
        ):
            sanitized[key] = "[redacted]"
            continue
        if isinstance(value, str):
            sanitized[key] = _sanitize_text(value)
        elif isinstance(value, int | float | bool) or value is None:
            sanitized[key] = value
        elif isinstance(value, list):
            sanitized[key] = [_sanitize_metadata_value(item) for item in value]
        elif isinstance(value, dict):
            sanitized[key] = sanitize_metadata(value)
        else:
            sanitized[key] = str(value)
    return sanitized


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, dict):
        return sanitize_metadata(value)
    return str(value)


def _sanitize_text(value: str) -> str:
    return value[:500]


def _safe_subprocess_error(exc: subprocess.CalledProcessError) -> str:
    parts = [f"command failed with exit code {exc.returncode}"]
    stderr = (exc.stderr or "").strip()
    if stderr:
        parts.append(_sanitize_text(stderr))
    return ": ".join(parts)


def _docker_state_to_status(state: dict[str, Any]) -> str:
    if state.get("Running"):
        return "running"
    status = str(state.get("Status") or "").lower()
    if status in {"exited", "dead"}:
        exit_code = state.get("ExitCode")
        return "exited" if exit_code in (0, None) else "failed"
    return status or "unknown"


def _list_relative_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


def create_app(
    *,
    runtime_dir: Path,
    worker_root: Path = DEFAULT_WORKER_ROOT,
    container_runtime: ContainerRuntime | None = None,
    docker_network: str = "none",
    token: str | None = None,
) -> Starlette:
    return WorkerDaemon(
        runtime_dir=runtime_dir,
        worker_root=worker_root,
        container_runtime=container_runtime,
        docker_network=docker_network,
        token=token,
    ).create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the worker-VM container job daemon.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11888)
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--worker-root", type=Path, default=DEFAULT_WORKER_ROOT)
    parser.add_argument("--docker-network", default=os.environ.get("AGENT_SANDBOX_WORKER_DOCKER_NETWORK", "none"))
    args = parser.parse_args()

    uvicorn.run(
        create_app(
            runtime_dir=args.runtime_dir,
            worker_root=args.worker_root,
            docker_network=args.docker_network,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
