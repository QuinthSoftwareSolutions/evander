from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services.skill_admin_service import SkillDeletionBlockedByWorkflowReferences
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations


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


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def skill_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[BaseRoute]:
    async def list_skills(_: Request) -> JSONResponse:
        items = await run_in_threadpool(service.skill_admin.list_skills)
        return JSONResponse({"items": [_jsonable(item) for item in items]})

    async def create_skill(request: Request) -> JSONResponse:
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            payload = await _json_body(request)
            metadata = await run_in_threadpool(service.skill_admin.create_skill, payload)
        except FileExistsError as exc:
            return json_error(str(exc), status_code=409)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"skill": _jsonable(metadata)}, status_code=201)

    async def get_skill(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        try:
            entry = await run_in_threadpool(service.skill_admin.get_skill, name)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except LookupError as exc:
            return json_error(str(exc), status_code=404)
        except RuntimeError as exc:
            return json_error(str(exc), status_code=500)
        return JSONResponse({"skill": _jsonable(entry)})

    async def delete_skill(request: Request) -> JSONResponse:
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        name = request.path_params["name"]
        try:
            metadata = await run_in_threadpool(service.skill_admin.delete_skill, name)
        except SkillDeletionBlockedByWorkflowReferences as exc:
            return json_error(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except LookupError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse({"deleted": True, "skill": _jsonable(metadata)})

    return [
        Route("/api/admin/skills", list_skills, methods=["GET"]),
        Route("/api/admin/skills", create_skill, methods=["POST"]),
        Route("/api/admin/skills/{name:str}", get_skill, methods=["GET"]),
        Route("/api/admin/skills/{name:str}", delete_skill, methods=["DELETE"]),
    ]


__all__ = ["skill_routes"]
