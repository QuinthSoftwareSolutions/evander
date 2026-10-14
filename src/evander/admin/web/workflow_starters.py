from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.payloads import AdminRevisionConflict
from evander.admin.services.workflow_pack_history_service import WorkflowPackChangeActor
from evander.admin.web.json_support import json_body
from evander.admin.web.rbac import (
    WORKFLOW_PACKS_MANAGE_PERMISSION,
    WORKFLOW_RUNS_CONTROL_PERMISSION,
    WORKFLOW_RUNS_READ_PERMISSION,
    WORKFLOWS_READ_PERMISSION,
    WORKFLOWS_WRITE_PERMISSION,
)
from evander.admin.web.shared import json_error, request_has_privileged_operations
from evander.events.internal import InternalEventSource


def workflow_starter_routes(service, auth):
    starters = service.workflow_starter_admin

    def directory(request, payload=None):
        value = (payload or request.query_params).get("directory_path", "root")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("directory_path is required")
        return service.integration_admin.context("workflow-starters", value).directory_path

    def mutation_access(request, path, *, run=False):
        auth.require_permission(request, WORKFLOWS_WRITE_PERMISSION, directory_path=path)
        auth.require_permission(
            request,
            WORKFLOW_RUNS_CONTROL_PERMISSION if run else WORKFLOW_PACKS_MANAGE_PERMISSION,
            directory_path=path if run else "root",
        )
        if not request_has_privileged_operations(auth=auth, service=service, request=request):
            raise PermissionError("Privileged operations are locked.")

    async def endpoint(request: Request):
        try:
            session = auth.require(request)
            payload = await json_body(request) if request.method == "POST" else None
            path = directory(request, payload)
            auth.require_permission(request, WORKFLOWS_READ_PERMISSION, directory_path=path)
            action = request.url.path.rsplit("/", 1)[-1]
            if action == "workflow-starters":
                action = "catalog"
            if action == "catalog":
                result = await run_in_threadpool(starters.catalog, path)
            elif action in {"preview", "activate"}:
                auth.require_permission(request, WORKFLOWS_WRITE_PERMISSION, directory_path=path)
                auth.require_permission(
                    request, WORKFLOW_PACKS_MANAGE_PERMISSION, directory_path="root"
                )
                values = payload.get("values")
                if not isinstance(values, dict) or not isinstance(payload.get("starter_id"), str):
                    raise ValueError("starter_id and form values are required")
                if action == "preview":
                    result = await run_in_threadpool(
                        starters.preview, payload["starter_id"], path, values
                    )
                else:
                    mutation_access(request, path)
                    revision = payload.get("preview_revision")
                    if not isinstance(revision, str) or not revision:
                        return json_error("preview_revision is required", status_code=428)
                    actor = WorkflowPackChangeActor(
                        login=str(session.get("login") or "operator"),
                        display_name=session.get("display_name"),
                    )
                    result = await run_in_threadpool(
                        starters.activate,
                        payload["starter_id"],
                        path,
                        values,
                        preview_revision=revision,
                        actor=actor,
                    )
            elif action == "verify":
                auth.require_permission(request, WORKFLOW_RUNS_READ_PERMISSION, directory_path=path)
                result = await run_in_threadpool(
                    starters.verification,
                    request.path_params["instance_id"],
                    path,
                    event_id=request.query_params.get("event_id"),
                )
            elif action == "test":
                mutation_access(request, path, run=True)
                request_id = payload.get("request_id")
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError("request_id is required")
                result = await run_in_threadpool(
                    starters.run_test,
                    request.path_params["instance_id"],
                    path,
                    request_id=request_id,
                    source=InternalEventSource(
                        source_type="workflow_starter",
                        user_id=str(session.get("sub") or ""),
                        user_login=str(session.get("login") or ""),
                    ),
                    authorize_directory=lambda value: auth.require_permission(
                        request, WORKFLOW_RUNS_CONTROL_PERMISSION, directory_path=value
                    ),
                )
            else:
                raise KeyError("Unknown starter action")
            return JSONResponse(result)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except AdminRevisionConflict as exc:
            return json_error(str(exc), status_code=409)
        except KeyError:
            return json_error("Starter is unavailable in this directory.", status_code=404)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    return [
        Route("/api/admin/workflow-starters", endpoint, methods=["GET"]),
        Route("/api/admin/workflow-starters/preview", endpoint, methods=["POST"]),
        Route("/api/admin/workflow-starters/activate", endpoint, methods=["POST"]),
        Route("/api/admin/workflow-starters/{instance_id}/verify", endpoint, methods=["GET"]),
        Route("/api/admin/workflow-starters/{instance_id}/test", endpoint, methods=["POST"]),
    ]
