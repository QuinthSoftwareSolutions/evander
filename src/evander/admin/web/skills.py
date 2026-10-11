from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.web.json_support import (
    json_body as _json_body,
    jsonable as _jsonable,
)
from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services.skill_admin_service import (
    SkillDeletionBlockedByWorkflowReferences,
    SkillPropagationError,
)
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations


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
        except SkillPropagationError as exc:
            return json_error(str(exc), status_code=502, details=exc.details)
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
