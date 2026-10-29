from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.web.json_support import jsonable as _jsonable
from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.cache import AdminWebJsonResponseCache, _request_bypasses_cache
from evander.admin.web.shared import AdminWebAuth, json_error


def capacity_routes(
    service: AdminRuntimeComponents,
    _auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[BaseRoute]:
    async def get_capacity_health(request: Request) -> JSONResponse:
        try:
            kwargs = {"refresh": True} if _request_bypasses_cache(request) else {}
            payload = await run_in_threadpool(
                service.capacity_health_admin.read_capacity_health, **kwargs
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=500)
        return JSONResponse(_jsonable(payload))

    return [
        Route(
            "/api/admin/capacity-health",
            cache.wrap("admin.capacity_health", get_capacity_health)
            if cache
            else get_capacity_health,
            methods=["GET"],
        )
    ]
