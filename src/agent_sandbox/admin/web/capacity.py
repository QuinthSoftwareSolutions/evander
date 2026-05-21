from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.cache import AdminWebJsonResponseCache
from agent_sandbox.admin.web.shared import AdminWebAuth, json_error


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def capacity_routes(
    service: AdminService,
    _auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[BaseRoute]:
    async def get_capacity_health(_: Request) -> JSONResponse:
        try:
            payload = await run_in_threadpool(service.read_capacity_health)
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
