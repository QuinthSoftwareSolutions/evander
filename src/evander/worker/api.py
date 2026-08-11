from __future__ import annotations

import subprocess
from collections.abc import Sequence
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .models import (
    DEFAULT_WORKER_ROOT,
    WorkerFileWriteRequest,
    WorkerGitWriteKeyRequest,
    WorkerJobRequest,
    WorkerMssqlDatabaseRequest,
    WorkerRepoCloneRequest,
    WorkerSessionPrepareRequest,
    WorkerTaskWorktreeCleanupRequest,
    WorkerTaskWorktreeRequest,
)
from .codex_auth_broker import CodexAuthBroker, CodexAuthBrokerError
from .codex_egress_proxy import RestrictedCodexEgressProxy
from .runtime import ContainerRuntime
from .sandbox_profile import WorkerSandboxProfile
from .security import list_relative_files, safe_subprocess_error
from .service import WorkerDaemon, WorkerDependencyCompositionConflict


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

    async def telemetry(request: Request) -> JSONResponse:
        refresh = str(request.query_params.get("refresh") or "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        record = await run_in_threadpool(daemon.read_telemetry, refresh=refresh)
        return JSONResponse(record.model_dump(mode="json"))

    async def codex_auth_status(_: Request) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(daemon.codex_auth_status))

    async def start_codex_device_login(_: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(daemon.start_codex_device_login)
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(record, status_code=201)

    async def codex_device_login_status(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.codex_device_login_status,
                request.path_params["login_id"],
            )
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(record)

    async def cancel_codex_device_login(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.cancel_codex_device_login,
                request.path_params["login_id"],
            )
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(record)

    async def logout_codex_auth(_: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(daemon.logout_codex_auth)
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(record)

    async def create_job(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            job_request = WorkerJobRequest.model_validate(payload)
            record = await run_in_threadpool(daemon.start_job, job_request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"), status_code=201)

    async def prepare_session(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            session_request = WorkerSessionPrepareRequest.model_validate(payload)
            record = await run_in_threadpool(daemon.prepare_session, session_request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(record.model_dump(mode="json"), status_code=201)

    async def clone_session_repo(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            clone_request = WorkerRepoCloneRequest.model_validate(payload)
            record = await run_in_threadpool(
                daemon.clone_repo,
                request.path_params["session_id"],
                clone_request,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def prepare_task_worktree(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            worktree_request = WorkerTaskWorktreeRequest.model_validate(payload)
            record = await run_in_threadpool(
                daemon.prepare_task_worktree,
                request.path_params["session_id"],
                worktree_request,
            )
        except WorkerDependencyCompositionConflict as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "dependency_composition_conflict": exc.details,
                },
                status_code=409,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def cleanup_task_worktree(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            cleanup_request = WorkerTaskWorktreeCleanupRequest.model_validate(payload)
            record = await run_in_threadpool(
                daemon.cleanup_task_worktree, request.path_params["session_id"], cleanup_request
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def provision_session_git_write_key(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            key_request = WorkerGitWriteKeyRequest.model_validate(payload)
            record = await run_in_threadpool(
                daemon.provision_git_write_key,
                request.path_params["session_id"],
                key_request,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def revoke_session_git_key(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.revoke_git_key,
                request.path_params["session_id"],
                request.path_params["key_name"],
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(record.model_dump(mode="json"))

    async def write_session_files(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            write_request = WorkerFileWriteRequest.model_validate(payload)
            record = await run_in_threadpool(
                daemon.write_files,
                request.path_params["session_id"],
                write_request,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(record.model_dump(mode="json"))

    async def read_session_artifact(request: Request) -> Response:
        try:
            text = await run_in_threadpool(
                daemon.read_session_artifact_text,
                request.path_params["session_id"],
                request.path_params["artifact_path"],
            )
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return PlainTextResponse(text)

    async def delete_session(request: Request) -> JSONResponse:
        try:
            deleted = await run_in_threadpool(
                daemon.delete_session,
                request.path_params["session_id"],
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse({"deleted": deleted, "session_id": request.path_params["session_id"]})

    async def provision_mssql_database(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            database_request = WorkerMssqlDatabaseRequest.model_validate(payload)
            record = await run_in_threadpool(
                daemon.provision_mssql_database,
                request.path_params["session_id"],
                database_request,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"), status_code=201)

    async def list_mssql_databases(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.list_mssql_databases,
                request.path_params["session_id"],
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(record.model_dump(mode="json"))

    async def drop_mssql_database(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.drop_mssql_database,
                request.path_params["session_id"],
                request.path_params["logical_name"],
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def get_job(request: Request) -> JSONResponse:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        record = await run_in_threadpool(daemon.inspect_job, record)
        return JSONResponse(record.model_dump(mode="json"))

    async def stop_job(request: Request) -> JSONResponse:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        try:
            record = await run_in_threadpool(daemon.stop_job, record)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def interrupt_active_tool(request: Request) -> JSONResponse:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("interrupt request must be an object")
            max_elapsed_raw = payload.get("max_elapsed_seconds")
            max_elapsed_seconds = (
                float(max_elapsed_raw) if max_elapsed_raw is not None else None
            )
            if max_elapsed_seconds is not None and max_elapsed_seconds <= 0:
                raise ValueError("max_elapsed_seconds must be greater than zero")
            reason = str(payload.get("reason") or "operator_requested").strip()
            if reason not in {"operator_requested", "active_tool_timeout"}:
                raise ValueError("unsupported active-tool interruption reason")
            record = await run_in_threadpool(
                daemon.interrupt_active_tool,
                record,
                reason=reason,
                max_elapsed_seconds=max_elapsed_seconds,
            )
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def delete_job(request: Request) -> JSONResponse:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        try:
            await run_in_threadpool(daemon.remove_job, record)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse({"deleted": True, "job_id": record.job_id})

    async def get_logs(request: Request) -> Response:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        try:
            limit_text = request.query_params.get("limit")
            offset_text = request.query_params.get("offset")
            query_text = request.query_params.get("query")
            if limit_text is not None or offset_text is not None or query_text is not None:
                page = await run_in_threadpool(
                    daemon.get_log_page,
                    record,
                    mode=str(request.query_params.get("mode") or "augmented"),
                    limit=int(limit_text or "200"),
                    offset=int(offset_text or "0"),
                    query=query_text,
                )
                headers = {
                    "x-evander-log-paged": "true",
                    "x-evander-log-limit": str(page["limit"]),
                    "x-evander-log-offset": str(page["offset"]),
                    "x-evander-log-returned-lines": str(page["returned_lines"]),
                    "x-evander-log-has-more-before": (
                        "true" if page["has_more_before"] else "false"
                    ),
                }
                if page["total_lines"] is not None:
                    headers["x-evander-log-total-lines"] = str(page["total_lines"])
                if page["start_line"] is not None:
                    headers["x-evander-log-start-line"] = str(page["start_line"])
                if page["end_line"] is not None:
                    headers["x-evander-log-end-line"] = str(page["end_line"])
                return PlainTextResponse(str(page["text"]), headers=headers)
            text = await run_in_threadpool(
                daemon.get_logs,
                record,
                mode=str(request.query_params.get("mode") or "augmented"),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return PlainTextResponse(text)

    async def get_artifacts(request: Request) -> JSONResponse:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        artifacts = await run_in_threadpool(
            list_relative_files,
            Path(record.artifacts_path),
        )
        return JSONResponse({"artifacts": artifacts})

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            daemon.close()

    app = Starlette(
        debug=False,
        lifespan=lifespan,
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/v1/telemetry", telemetry, methods=["GET"]),
            Route("/v1/codex-auth", codex_auth_status, methods=["GET"]),
            Route("/v1/codex-auth/device-login", start_codex_device_login, methods=["POST"]),
            Route(
                "/v1/codex-auth/device-login/{login_id}",
                codex_device_login_status,
                methods=["GET"],
            ),
            Route(
                "/v1/codex-auth/device-login/{login_id}",
                cancel_codex_device_login,
                methods=["DELETE"],
            ),
            Route("/v1/codex-auth", logout_codex_auth, methods=["DELETE"]),
            Route("/v1/sessions", prepare_session, methods=["POST"]),
            Route("/v1/sessions/{session_id}/repo", clone_session_repo, methods=["POST"]),
            Route(
                "/v1/sessions/{session_id}/worktrees",
                prepare_task_worktree,
                methods=["POST"],
            ),
            Route(
                "/v1/sessions/{session_id}/worktrees/cleanup",
                cleanup_task_worktree,
                methods=["POST"],
            ),
            Route(
                "/v1/sessions/{session_id}/git/write-key",
                provision_session_git_write_key,
                methods=["POST"],
            ),
            Route(
                "/v1/sessions/{session_id}/git/keys/{key_name}",
                revoke_session_git_key,
                methods=["DELETE"],
            ),
            Route("/v1/sessions/{session_id}/files", write_session_files, methods=["POST"]),
            Route(
                "/v1/sessions/{session_id}/artifacts/{artifact_path:path}",
                read_session_artifact,
                methods=["GET"],
            ),
            Route(
                "/v1/sessions/{session_id}/mssql/databases",
                provision_mssql_database,
                methods=["POST"],
            ),
            Route(
                "/v1/sessions/{session_id}/mssql/databases",
                list_mssql_databases,
                methods=["GET"],
            ),
            Route(
                "/v1/sessions/{session_id}/mssql/databases/{logical_name}",
                drop_mssql_database,
                methods=["DELETE"],
            ),
            Route("/v1/sessions/{session_id}", delete_session, methods=["DELETE"]),
            Route("/v1/jobs", create_job, methods=["POST"]),
            Route("/v1/jobs/{job_id}", get_job, methods=["GET"]),
            Route("/v1/jobs/{job_id}/stop", stop_job, methods=["POST"]),
            Route(
                "/v1/jobs/{job_id}/interrupt-active-tool",
                interrupt_active_tool,
                methods=["POST"],
            ),
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
    mssql_enabled: bool = False,
    mssql_admin_user: str = "sa",
    mssql_admin_password: str | None = None,
    mssql_admin_password_env: str = "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD",
    mssql_admin_host: str = "127.0.0.1",
    mssql_port: int = 1433,
    mssql_container_host: str = "172.17.0.1",
    mssql_sqlcmd_path: str = "sqlcmd",
    codex_auth_broker: CodexAuthBroker | None = None,
    codex_auth_command: Sequence[str] | None = None,
    codex_egress_proxy_url: str | None = None,
    codex_egress_proxy: RestrictedCodexEgressProxy | None = None,
    codex_egress_proxy_bind_host: str | None = None,
    codex_egress_proxy_port: int = 11889,
    codex_egress_allowed_hosts: Sequence[str] | None = None,
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
        mssql_enabled=mssql_enabled,
        mssql_admin_user=mssql_admin_user,
        mssql_admin_password=mssql_admin_password,
        mssql_admin_password_env=mssql_admin_password_env,
        mssql_admin_host=mssql_admin_host,
        mssql_port=mssql_port,
        mssql_container_host=mssql_container_host,
        mssql_sqlcmd_path=mssql_sqlcmd_path,
        codex_auth_broker=codex_auth_broker,
        codex_auth_command=codex_auth_command,
        codex_egress_proxy_url=codex_egress_proxy_url,
        codex_egress_proxy=codex_egress_proxy,
        codex_egress_proxy_bind_host=codex_egress_proxy_bind_host,
        codex_egress_proxy_port=codex_egress_proxy_port,
        codex_egress_allowed_hosts=codex_egress_allowed_hosts,
    ).create_app()
