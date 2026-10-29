from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from evander.codex_auth import CodexRateLimitResetRequest, CodexSubscriptionDeferredError

from .models import (
    DEFAULT_WORKER_ROOT,
    WorkerFileWriteRequest,
    WorkerGitWriteKeyRequest,
    WorkerJobRequest,
    WorkerJobCancelRequest,
    WorkerSteerRequest,
    WorkerMssqlDatabaseRequest,
    WorkerRepoCloneRequest,
    WorkerReferenceRepoPrepareRequest,
    WorkerSessionPrepareRequest,
    WorkerTaskWorktreeCleanupRequest,
    WorkerTaskWorktreeRequest,
)
from .codex_auth_broker import CodexAuthBroker, CodexAuthBrokerError
from .codex_auth_profiles import CodexAuthBrokerRegistry, CodexAuthProfileInUseError
from .codex_egress_proxy import RestrictedCodexEgressProxy
from .runtime import ContainerRuntime
from .sandbox_profile import WorkerSandboxProfile
from evander.git_recovery import GitPreparationFailure, GIT_RECOVERY_PROTOCOL_VERSION

from .security import list_relative_files, safe_subprocess_error
from .service import (
    WorkerDaemon,
    WorkerDependencyCompositionConflict,
    WorkerReferenceRepoTrustConflict,
)


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
    deployment_revision = os.environ.get("EVANDER_WORKER_DEPLOY_REVISION")

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "deployment_revision": deployment_revision,
                "capabilities": {
                    "git_recovery": GIT_RECOVERY_PROTOCOL_VERSION,
                    "git_salvage": 1,
                    "codex_fast_mode": 1,
                    "codex_ultrafast_mode": 1,
                    "codex_runtime_bridge": 1,
                    "inference_usage": 1,
                    "job_cancellation": 1,
                    "codex_native_steering": 1,
                },
            }
        )

    async def steer_job(request: Request) -> JSONResponse:
        record = daemon.store.get(request.path_params["job_id"])
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        try:
            payload = WorkerSteerRequest.model_validate(await request.json())
            receipt = await run_in_threadpool(daemon.steer_job, record, payload)
            return JSONResponse(receipt)
        except ValueError:
            return JSONResponse(
                {"error": "Invalid or conflicting steering delivery"}, status_code=409
            )

    async def steering_receipt(request: Request) -> JSONResponse:
        receipt = daemon.store.steering(
            request.path_params["job_id"], request.path_params["delivery_id"]
        )
        return JSONResponse(
            receipt or {"error": "delivery not found"}, status_code=200 if receipt else 404
        )

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
        try:
            return JSONResponse(await run_in_threadpool(daemon.codex_auth_status))
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def list_codex_auth_profiles(_: Request) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(daemon.list_codex_auth_profiles))

    async def create_codex_auth_profile(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("profile request must be an object")
            record = await run_in_threadpool(daemon.create_codex_auth_profile, payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(record, status_code=201)

    async def codex_auth_profile_status(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.codex_auth_profile_status,
                request.path_params["profile_id"],
            )
        except (ValueError, CodexAuthBrokerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(record)

    async def patch_codex_auth_profile(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("profile request must be an object")
            record = await run_in_threadpool(
                daemon.patch_codex_auth_profile,
                request.path_params["profile_id"],
                payload,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(record)

    async def delete_codex_auth_profile(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.delete_codex_auth_profile, request.path_params["profile_id"]
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except CodexAuthProfileInUseError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except OSError:
            return JSONResponse({"error": "Unable to remove subscription credentials"}, status_code=502)
        return JSONResponse(record)

    async def start_codex_profile_device_login(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.start_codex_profile_device_login,
                request.path_params["profile_id"],
            )
        except CodexSubscriptionDeferredError as exc:
            return JSONResponse(
                {"error": str(exc), "deferral": exc.deferral.model_dump(mode="json")},
                status_code=409,
            )
        except (ValueError, CodexAuthBrokerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(record, status_code=201)

    async def consume_codex_profile_reset_credit(request: Request) -> JSONResponse:
        try:
            payload = CodexRateLimitResetRequest.model_validate(await request.json())
            record = await run_in_threadpool(
                daemon.consume_codex_profile_reset_credit,
                request.path_params["profile_id"],
                payload,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except CodexSubscriptionDeferredError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except CodexAuthBrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(record)

    async def codex_profile_device_login_status(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.codex_profile_device_login_status,
                request.path_params["profile_id"],
                request.path_params["login_id"],
            )
        except (ValueError, CodexAuthBrokerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(record)

    async def cancel_codex_profile_device_login(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.cancel_codex_profile_device_login,
                request.path_params["profile_id"],
                request.path_params["login_id"],
            )
        except (ValueError, CodexAuthBrokerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(record)

    async def logout_codex_profile(request: Request) -> JSONResponse:
        try:
            record = await run_in_threadpool(
                daemon.logout_codex_profile,
                request.path_params["profile_id"],
            )
        except (ValueError, CodexAuthBrokerError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(record)

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
        except CodexSubscriptionDeferredError as exc:
            return JSONResponse(
                {"error": str(exc), "deferral": exc.deferral.model_dump(mode="json")},
                status_code=429,
            )
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

    async def prepare_session_reference_repo(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            prepare_request = WorkerReferenceRepoPrepareRequest.model_validate(payload)
            record = await run_in_threadpool(
                daemon.prepare_reference_repo,
                request.path_params["session_id"],
                prepare_request,
            )
        except WorkerReferenceRepoTrustConflict as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "reference_repo_trust_conflict": exc.details,
                },
                status_code=409,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
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
        except GitPreparationFailure as exc:
            return JSONResponse({"error": str(exc), "git_preparation_failure": exc.diagnostic()}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(record.model_dump(mode="json"))

    async def capture_git_salvage(request: Request) -> JSONResponse:
        from .models import WorkerGitSalvageRequest

        try:
            payload = WorkerGitSalvageRequest.model_validate(await request.json())
            result = await run_in_threadpool(
                daemon.capture_git_salvage, request.path_params["session_id"], payload
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(result)

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
            byte_limit = request.query_params.get("max_bytes")
            text = await run_in_threadpool(
                daemon.read_session_artifact_text,
                request.path_params["session_id"],
                request.path_params["artifact_path"],
                max_bytes=int(byte_limit) if byte_limit is not None else None,
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

    async def get_model_usage(request: Request) -> JSONResponse:
        job_id = request.path_params["job_id"]
        record = daemon.store.get(job_id)
        if record is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        try:
            page = await run_in_threadpool(
                daemon.model_usage_page, record,
                cursor=int(request.query_params.get("cursor", "0")),
                limit=int(request.query_params.get("limit", "200")),
            )
            return JSONResponse(page.model_dump(mode="json"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def cancel_job(request: Request) -> JSONResponse:
        try:
            payload = WorkerJobCancelRequest.model_validate(await request.json())
            receipt = await run_in_threadpool(
                daemon.cancel_job, request.path_params["job_id"], payload.session_id
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except subprocess.CalledProcessError as exc:
            return JSONResponse({"error": safe_subprocess_error(exc)}, status_code=502)
        return JSONResponse(receipt)

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
            max_elapsed_seconds = float(max_elapsed_raw) if max_elapsed_raw is not None else None
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
            Route(
                "/v1/sessions/{session_id}/worktrees/salvage", capture_git_salvage, methods=["POST"]
            ),
            Route("/v1/telemetry", telemetry, methods=["GET"]),
            Route("/v1/codex-auth", codex_auth_status, methods=["GET"]),
            Route("/v1/codex-auth/profiles", list_codex_auth_profiles, methods=["GET"]),
            Route("/v1/codex-auth/profiles", create_codex_auth_profile, methods=["POST"]),
            Route(
                "/v1/codex-auth/profiles/{profile_id}",
                delete_codex_auth_profile,
                methods=["DELETE"],
            ),
            Route(
                "/v1/codex-auth/profiles/{profile_id}",
                codex_auth_profile_status,
                methods=["GET"],
            ),
            Route(
                "/v1/codex-auth/profiles/{profile_id}",
                patch_codex_auth_profile,
                methods=["PATCH"],
            ),
            Route(
                "/v1/codex-auth/profiles/{profile_id}/device-login",
                start_codex_profile_device_login,
                methods=["POST"],
            ),
            Route(
                "/v1/codex-auth/profiles/{profile_id}/reset-credits/consume",
                consume_codex_profile_reset_credit,
                methods=["POST"],
            ),
            Route(
                "/v1/codex-auth/profiles/{profile_id}/device-login/{login_id}",
                codex_profile_device_login_status,
                methods=["GET"],
            ),
            Route(
                "/v1/codex-auth/profiles/{profile_id}/device-login/{login_id}",
                cancel_codex_profile_device_login,
                methods=["DELETE"],
            ),
            Route(
                "/v1/codex-auth/profiles/{profile_id}/session",
                logout_codex_profile,
                methods=["DELETE"],
            ),
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
                "/v1/sessions/{session_id}/reference-repo",
                prepare_session_reference_repo,
                methods=["POST"],
            ),
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
            Route("/v1/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
            Route("/v1/jobs/{job_id}/steer", steer_job, methods=["POST"]),
            Route("/v1/jobs/{job_id}/steering/{delivery_id}", steering_receipt, methods=["GET"]),
            Route(
                "/v1/jobs/{job_id}/interrupt-active-tool",
                interrupt_active_tool,
                methods=["POST"],
            ),
            Route("/v1/jobs/{job_id}", delete_job, methods=["DELETE"]),
            Route("/v1/jobs/{job_id}/logs", get_logs, methods=["GET"]),
            Route("/v1/jobs/{job_id}/model-usage", get_model_usage, methods=["GET"]),
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
    codex_auth_registry: CodexAuthBrokerRegistry | None = None,
    codex_auth_command: Sequence[str] | None = None,
    codex_auth_command_factory: Callable[[str, Path], Sequence[str]] | None = None,
    codex_egress_proxy_url: str | None = None,
    codex_egress_proxy: RestrictedCodexEgressProxy | None = None,
    codex_egress_proxy_bind_host: str | None = None,
    codex_egress_proxy_port: int = 11889,
    codex_egress_allowed_hosts: Sequence[str] | None = None,
    codex_runtime_profiles: dict | None = None,
) -> Starlette:
    return WorkerDaemon(
        codex_runtime_profiles=codex_runtime_profiles,
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
        codex_auth_registry=codex_auth_registry,
        codex_auth_command=codex_auth_command,
        codex_auth_command_factory=codex_auth_command_factory,
        codex_egress_proxy_url=codex_egress_proxy_url,
        codex_egress_proxy=codex_egress_proxy,
        codex_egress_proxy_bind_host=codex_egress_proxy_bind_host,
        codex_egress_proxy_port=codex_egress_proxy_port,
        codex_egress_allowed_hosts=codex_egress_allowed_hosts,
    ).create_app()
