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

    def read_context(request: Request) -> tuple[str, str | None] | JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        try:
            auth.require(request)
            auth.require_permission(
                request,
                ADMIN_OWNER_PERMISSION,
                directory_path="root",
            )
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        session = auth.read_session(request) or {}
        actor = str(session.get("login") or "").strip() or None
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

    async def list_profiles(request: Request) -> JSONResponse:
        context = read_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, _ = context
        try:
            payload = service.codex_auth_admin.list_profiles(directory_path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def create_profile(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object")
            payload = service.codex_auth_admin.create_profile(
                directory_path, body, actor_user_id=actor
            )
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload, status_code=201)

    async def profile_status(request: Request) -> JSONResponse:
        context = read_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, _ = context
        try:
            payload = service.codex_auth_admin.profile_status(
                directory_path, request.path_params["profile_id"]
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def patch_profile(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object")
            payload = service.codex_auth_admin.patch_profile(
                directory_path,
                request.path_params["profile_id"],
                body,
                actor_user_id=actor,
            )
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def start_profile_login(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            payload = service.codex_auth_admin.start_profile_device_login(
                directory_path,
                request.path_params["profile_id"],
                actor_user_id=actor,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload, status_code=201)

    async def consume_profile_reset_credit(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object")
            payload = service.codex_auth_admin.consume_profile_reset_credit(
                directory_path,
                request.path_params["profile_id"],
                body,
                actor_user_id=actor,
            )
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def profile_login_status(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, _ = context
        try:
            payload = service.codex_auth_admin.profile_device_login_status(
                directory_path,
                request.path_params["profile_id"],
                request.path_params["login_id"],
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def cancel_profile_login(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            payload = service.codex_auth_admin.cancel_profile_device_login(
                directory_path,
                request.path_params["profile_id"],
                request.path_params["login_id"],
                actor_user_id=actor,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    async def logout_profile(request: Request) -> JSONResponse:
        context = request_context(request)
        if isinstance(context, JSONResponse):
            return context
        directory_path, actor = context
        try:
            payload = service.codex_auth_admin.logout_profile(
                directory_path,
                request.path_params["profile_id"],
                actor_user_id=actor,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except ContainerWorkerClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(payload)

    return [
        Route("/api/admin/codex-subscriptions", list_profiles, methods=["GET"]),
        Route("/api/admin/codex-subscriptions", create_profile, methods=["POST"]),
        Route(
            "/api/admin/codex-subscriptions/{profile_id:str}",
            profile_status,
            methods=["GET"],
        ),
        Route(
            "/api/admin/codex-subscriptions/{profile_id:str}",
            patch_profile,
            methods=["PATCH"],
        ),
        Route(
            "/api/admin/codex-subscriptions/{profile_id:str}/device-login",
            start_profile_login,
            methods=["POST"],
        ),
        Route(
            "/api/admin/codex-subscriptions/{profile_id:str}/reset-credits/consume",
            consume_profile_reset_credit,
            methods=["POST"],
        ),
        Route(
            "/api/admin/codex-subscriptions/{profile_id:str}/device-login/{login_id:str}",
            profile_login_status,
            methods=["GET"],
        ),
        Route(
            "/api/admin/codex-subscriptions/{profile_id:str}/device-login/{login_id:str}",
            cancel_profile_login,
            methods=["DELETE"],
        ),
        Route(
            "/api/admin/codex-subscriptions/{profile_id:str}/session",
            logout_profile,
            methods=["DELETE"],
        ),
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
