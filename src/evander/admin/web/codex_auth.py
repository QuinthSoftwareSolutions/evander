from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth
from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.models import AuditEvent


def codex_auth_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
) -> list[BaseRoute]:
    def require_worker_account_owner(
        request: Request,
    ) -> tuple[str | None, JSONResponse | None]:
        try:
            auth.require(request)
            if not auth.privileged_for_request(request):
                raise PermissionError("Privileged operations are locked.")
            auth.require_permission(
                request,
                ADMIN_OWNER_PERMISSION,
                # Codex auth is one shared account per worker, not a
                # directory-scoped resource. Root ownership is the existing
                # worker-wide authorization boundary.
                directory_path="root",
            )
        except PermissionError as exc:
            return None, JSONResponse({"error": str(exc)}, status_code=403)
        session = auth.read_session(request) or {}
        return str(session.get("login") or "").strip() or None, None

    def worker_client(directory_path: str) -> ContainerWorkerClient:
        config = service.runtime.provider_config.resolve_for_directory(directory_path)
        if not config.container_worker_base_url:
            raise ValueError(
                "container_worker_base_url is required to manage worker Codex authentication"
            )
        return ContainerWorkerClient(
            base_url=config.container_worker_base_url,
            token_env=config.container_worker_token_env,
            timeout_seconds=config.container_worker_request_timeout_seconds,
        )

    def request_context(
        request: Request,
    ) -> tuple[str, str | None, ContainerWorkerClient] | JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        actor, error = require_worker_account_owner(request)
        if error is not None:
            return error
        try:
            client = worker_client(directory_path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return directory_path, actor, client

    async def status(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, _, client = context
        try:
            payload = client.codex_auth_status()
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse({"directory_path": directory_path, **payload})

    async def start_device_login(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor, client = context
        try:
            payload = client.start_codex_device_login()
        except ContainerWorkerClientError as exc:
            _audit(service, "codex_subscription_login_start", directory_path, actor, "error")
            return JSONResponse({"error": str(exc)}, status_code=502)
        _audit(service, "codex_subscription_login_start", directory_path, actor, "allowed")
        return JSONResponse(payload, status_code=201)

    async def device_login_status(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        _, _, client = context
        try:
            payload = client.codex_device_login_status(request.path_params["login_id"])
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def cancel_device_login(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor, client = context
        try:
            payload = client.cancel_codex_device_login(request.path_params["login_id"])
        except ContainerWorkerClientError as exc:
            _audit(service, "codex_subscription_login_cancel", directory_path, actor, "error")
            return JSONResponse({"error": str(exc)}, status_code=502)
        _audit(service, "codex_subscription_login_cancel", directory_path, actor, "allowed")
        return JSONResponse(payload)

    async def logout(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor, client = context
        try:
            payload = client.logout_codex_auth()
        except ContainerWorkerClientError as exc:
            _audit(service, "codex_subscription_logout", directory_path, actor, "error")
            return JSONResponse({"error": str(exc)}, status_code=502)
        _audit(service, "codex_subscription_logout", directory_path, actor, "allowed")
        return JSONResponse(payload)

    return [
        Route("/api/admin/codex-auth", status, methods=["GET"]),
        Route(
            "/api/admin/codex-auth/device-login",
            start_device_login,
            methods=["POST"],
        ),
        Route(
            "/api/admin/codex-auth/device-login/{login_id:str}",
            device_login_status,
            methods=["GET"],
        ),
        Route(
            "/api/admin/codex-auth/device-login/{login_id:str}",
            cancel_device_login,
            methods=["DELETE"],
        ),
        Route("/api/admin/codex-auth", logout, methods=["DELETE"]),
    ]


def _audit(
    service: AdminRuntimeComponents,
    event_type: str,
    directory_path: str,
    actor_user_id: str | None,
    decision: str,
) -> None:
    service.runtime.audit.log(
        AuditEvent(
            event_type=event_type,
            directory_path=directory_path,
            actor_user_id=actor_user_id,
            provider="openai_chatgpt_subscription",
            decision=decision,
        )
    )
