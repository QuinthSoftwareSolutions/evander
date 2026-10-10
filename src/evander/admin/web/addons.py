from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.web.json_support import json_body as _json_body
from evander.admin.composition import AdminRuntimeComponents
from evander.admin.payloads import AdminRevisionConflict
from evander.admin.services.workflow_pack_history_service import WorkflowPackChangeActor
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations


def addon_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[BaseRoute]:
    def workflow_pack_actor_from_request(request: Request) -> WorkflowPackChangeActor | None:
        session = auth.require(request)
        login = str(session.get("login") or "").strip()
        if not login:
            return None
        display_name = session.get("display_name")
        return WorkflowPackChangeActor(
            login=login,
            display_name=str(display_name).strip() if display_name else None,
        )

    async def get_addons(request: Request) -> JSONResponse:
        auth.require(request)
        payload = await run_in_threadpool(service.addon_admin.read_addon_state)
        return JSONResponse(payload)

    async def put_enabled_addons(request: Request) -> JSONResponse:
        actor = workflow_pack_actor_from_request(request)
        try:
            auth.require_permission(request, ADMIN_OWNER_PERMISSION)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        if not request_has_privileged_operations(auth=auth, service=service, request=request):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        enabled_addon_ids = payload.get("enabled_addon_ids")
        if not isinstance(enabled_addon_ids, list):
            return json_error("enabled_addon_ids must be a JSON array", status_code=400)
        expected_revision = str(payload.get("expected_revision") or "").strip()
        if not expected_revision:
            return json_error("expected_revision is required", status_code=428)
        try:
            result = await run_in_threadpool(
                service.addon_admin.update_enabled_addons,
                enabled_addon_ids,
                expected_revision=expected_revision,
                actor=actor,
            )
        except AdminRevisionConflict as exc:
            return json_error(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(result)

    async def refresh_runtime_workflow_pack(request: Request) -> JSONResponse:
        actor = workflow_pack_actor_from_request(request)
        try:
            auth.require_permission(request, ADMIN_OWNER_PERMISSION)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        if not request_has_privileged_operations(auth=auth, service=service, request=request):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        expected_revision = str(payload.get("expected_revision") or "").strip()
        if not expected_revision:
            return json_error("expected_revision is required", status_code=428)
        try:
            result = await run_in_threadpool(
                service.addon_admin.refresh_runtime_workflow_pack,
                str(request.path_params.get("addon_id") or ""),
                expected_revision=expected_revision,
                actor=actor,
            )
        except AdminRevisionConflict as exc:
            return json_error(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(result)

    return [
        Route("/api/admin/addons", get_addons, methods=["GET"]),
        Route("/api/admin/addons/enabled", put_enabled_addons, methods=["PUT"]),
        Route(
            "/api/admin/addons/{addon_id}/runtime-workflow-pack/refresh",
            refresh_runtime_workflow_pack,
            methods=["POST"],
        ),
    ]


__all__ = ["addon_routes"]
