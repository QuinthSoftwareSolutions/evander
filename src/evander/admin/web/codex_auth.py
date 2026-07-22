from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth
from evander.execution.container_worker_client import ContainerWorkerClientError


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

    def request_context(
        request: Request,
    ) -> tuple[str, str | None] | JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        actor, error = require_worker_account_owner(request)
        if error is not None:
            return error
        return directory_path, actor

    async def status(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, _ = context
        try:
            payload = service.codex_auth_admin.status(directory_path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def start_device_login(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            payload = service.codex_auth_admin.start_device_login(
                directory_path, actor_user_id=actor
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload, status_code=201)

    async def device_login_status(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, _ = context
        try:
            payload = service.codex_auth_admin.device_login_status(
                directory_path, request.path_params["login_id"]
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def cancel_device_login(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            payload = service.codex_auth_admin.cancel_device_login(
                directory_path,
                request.path_params["login_id"],
                actor_user_id=actor,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def logout(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            payload = service.codex_auth_admin.logout(directory_path, actor_user_id=actor)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
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
