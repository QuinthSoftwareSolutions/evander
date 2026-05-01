from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .models import (
    DEFAULT_WORKER_ROOT,
    WorkerFileWriteRequest,
    WorkerJobRequest,
    WorkerRepoCloneRequest,
    WorkerSessionPrepareRequest,
)
from .runtime import ContainerRuntime
from .sandbox_profile import WorkerSandboxProfile
from .security import list_relative_files, safe_subprocess_error
from .service import WorkerDaemon


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


def build_worker_app(daemon: WorkerDaemon) -> Starlette:
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
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"), status_code=201)

    async def prepare_session(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            session_request = WorkerSessionPrepareRequest.model_validate(payload)
            record = daemon.prepare_session(session_request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(record.model_dump(mode="json"), status_code=201)

    async def clone_session_repo(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            clone_request = WorkerRepoCloneRequest.model_validate(payload)
            record = daemon.clone_repo(request.path_params["session_id"], clone_request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def write_session_files(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            write_request = WorkerFileWriteRequest.model_validate(payload)
            record = daemon.write_files(request.path_params["session_id"], write_request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(record.model_dump(mode="json"))

    async def delete_session(request: Request) -> JSONResponse:
        try:
            deleted = daemon.delete_session(request.path_params["session_id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(
            {"deleted": deleted, "session_id": request.path_params["session_id"]}
        )

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
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def delete_job(request: Request) -> JSONResponse:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        try:
            daemon.remove_job(record)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
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
        return JSONResponse({"artifacts": list_relative_files(Path(record.artifacts_path))})

    app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/v1/sessions", prepare_session, methods=["POST"]),
            Route("/v1/sessions/{session_id}/repo", clone_session_repo, methods=["POST"]),
            Route("/v1/sessions/{session_id}/files", write_session_files, methods=["POST"]),
            Route("/v1/sessions/{session_id}", delete_session, methods=["DELETE"]),
            Route("/v1/jobs", create_job, methods=["POST"]),
            Route("/v1/jobs/{job_id}", get_job, methods=["GET"]),
            Route("/v1/jobs/{job_id}/stop", stop_job, methods=["POST"]),
            Route("/v1/jobs/{job_id}", delete_job, methods=["DELETE"]),
            Route("/v1/jobs/{job_id}/logs", get_logs, methods=["GET"]),
            Route("/v1/jobs/{job_id}/artifacts", get_artifacts, methods=["GET"]),
        ],
    )
    app.add_middleware(WorkerAuthMiddleware, token=daemon.token)
    return app


def create_app(
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
) -> Starlette:
    return WorkerDaemon(
        runtime_dir=runtime_dir,
        worker_root=worker_root,
        container_runtime=container_runtime,
        docker_network=docker_network,
        sandbox_profile=sandbox_profile,
        read_only_mounts=read_only_mounts,
        allowed_git_source_prefixes=allowed_git_source_prefixes,
        git_ssh_source_dir=git_ssh_source_dir,
        token=token,
    ).create_app()
