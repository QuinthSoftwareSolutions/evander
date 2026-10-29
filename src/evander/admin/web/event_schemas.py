from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.services.event_schema_admin_service import EventSchemaRevisionConflict
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import json_error, request_has_privileged_operations


def event_schema_routes(service, auth):
    async def handle(request):
        try:
            directory = service.teams_admin.normalize_directory_reference(
                request.query_params.get("directory_path", "root")
            )
            auth.require(request)
            auth.require_permission(request, ADMIN_OWNER_PERMISSION, directory_path=directory)
            if request.method == "GET":
                result = await run_in_threadpool(service.event_schema_admin.snapshot, directory)
            else:
                if not request_has_privileged_operations(
                    auth=auth, service=service, request=request
                ):
                    return json_error("Privileged operations are locked.", status_code=403)
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("Request body must be an object")
                if (
                    not isinstance(body.get("expected_revision"), str)
                    or not body["expected_revision"]
                ):
                    return json_error("expected_revision is required", status_code=428)
                result = await run_in_threadpool(
                    service.event_schema_admin.save,
                    directory,
                    expected_revision=body["expected_revision"],
                    config=body.get("config"),
                )
            return JSONResponse(result, headers={"cache-control": "no-store"})
        except EventSchemaRevisionConflict as exc:
            return json_error(str(exc), status_code=409)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))

    return [Route("/api/admin/event-schemas", handle, methods=["GET", "PUT"])]
