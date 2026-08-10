from __future__ import annotations

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import (
    AdminWebAuth,
    json_error,
    request_has_privileged_operations,
)


def discord_webhook_routes(
    service: AdminRuntimeComponents, auth: AdminWebAuth
) -> list[BaseRoute]:
    def request_context(request: Request) -> tuple[str, str]:
        directory_path = (
            str(request.query_params.get("directory_path") or "root").strip() or "root"
        )
        target_id = str(request.path_params.get("target_id") or "").strip()
        if not target_id:
            raise ValueError("target_id is required")
        return directory_path, target_id

    def require_mutation_access(request: Request) -> JSONResponse | None:
        auth.require(request)
        if request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        ):
            return None
        return json_error("Privileged operations are locked.", status_code=403)

    async def status(request: Request) -> JSONResponse:
        auth.require(request)
        try:
            directory_path, target_id = request_context(request)
            result = await run_in_threadpool(
                service.discord_webhook_admin.status,
                directory_path=directory_path,
                target_id=target_id,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(result)

    async def provision(request: Request) -> JSONResponse:
        denied = require_mutation_access(request)
        if denied is not None:
            return denied
        try:
            directory_path, target_id = request_context(request)
            result = await run_in_threadpool(
                service.discord_webhook_admin.provision,
                directory_path=directory_path,
                target_id=target_id,
                replace=False,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except httpx.HTTPError as exc:
            return json_error(f"Discord webhook provisioning failed: {exc}", status_code=502)
        return JSONResponse(result)

    async def recreate(request: Request) -> JSONResponse:
        denied = require_mutation_access(request)
        if denied is not None:
            return denied
        try:
            directory_path, target_id = request_context(request)
            result = await run_in_threadpool(
                service.discord_webhook_admin.provision,
                directory_path=directory_path,
                target_id=target_id,
                replace=True,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except httpx.HTTPError as exc:
            return json_error(f"Discord webhook recreation failed: {exc}", status_code=502)
        return JSONResponse(result)

    async def test(request: Request) -> JSONResponse:
        denied = require_mutation_access(request)
        if denied is not None:
            return denied
        try:
            directory_path, target_id = request_context(request)
            result = await run_in_threadpool(
                service.discord_webhook_admin.test,
                directory_path=directory_path,
                target_id=target_id,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except httpx.HTTPError as exc:
            return json_error(f"Discord webhook test failed: {exc}", status_code=502)
        return JSONResponse(result)

    base = "/api/discord-webhooks/targets/{target_id:str}"
    return [
        Route(base, status, methods=["GET"]),
        Route(f"{base}/provision", provision, methods=["POST"]),
        Route(f"{base}/recreate", recreate, methods=["POST"]),
        Route(f"{base}/test", test, methods=["POST"]),
    ]
