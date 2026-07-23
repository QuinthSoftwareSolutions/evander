from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services.host_directory_admin_service import (
    HostDirectoryConfigRevisionConflict,
)
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations
from evander.host_directories.service import HostDirectoryError


def host_directory_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
) -> list[BaseRoute]:
    def _directory_path(request: Request) -> str:
        requested = str(request.query_params.get("directory_path") or "root").strip() or "root"
        return service.teams_admin.normalize_directory_reference(requested)

    def _require_owner(
        request: Request,
        *,
        directory_path: str,
        privileged: bool,
    ) -> dict[str, object] | JSONResponse:
        try:
            session = auth.require(request)
            auth.require_permission(
                request,
                ADMIN_OWNER_PERMISSION,
                directory_path=directory_path,
            )
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        if privileged and not request_has_privileged_operations(
            auth=auth, service=service, request=request
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        return session

    async def get_host_directories(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=False)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(service.host_directory_admin.snapshot(directory_path))
        except (HostDirectoryError, ValueError) as exc:
            return json_error(str(exc), status_code=400)

    async def put_host_directories(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON", status_code=400)
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object", status_code=400)
        expected_revision = str(payload.get("expected_revision") or "").strip()
        if not expected_revision:
            return json_error("expected_revision is required", status_code=428)
        try:
            return JSONResponse(
                service.host_directory_admin.save(
                    directory_path,
                    expected_revision=expected_revision,
                    config_payload=payload.get("config"),
                )
            )
        except HostDirectoryConfigRevisionConflict as exc:
            return json_error(str(exc), status_code=409)
        except (HostDirectoryError, ValueError) as exc:
            return json_error(str(exc), status_code=400)

    async def check_health(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(
                service.host_directory_admin.health(
                    directory_path,
                    str(request.path_params["directory_id"]),
                )
            )
        except (HostDirectoryError, ValueError) as exc:
            return json_error(str(exc), status_code=400)

    async def get_quarantine(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=False)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(
                service.host_directory_admin.list_quarantine(
                    directory_path,
                    str(request.path_params["directory_id"]),
                )
            )
        except (HostDirectoryError, OSError, ValueError) as exc:
            return json_error(str(exc), status_code=400)

    async def restore_quarantine(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(
                service.host_directory_admin.restore_quarantine(
                    directory_path,
                    str(request.path_params["directory_id"]),
                    str(request.path_params["recovery_id"]),
                )
            )
        except (HostDirectoryError, OSError, ValueError) as exc:
            return json_error(str(exc), status_code=400)

    async def purge_quarantine(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(
                service.host_directory_admin.purge_quarantine(
                    directory_path,
                    str(request.path_params["directory_id"]),
                    str(request.path_params["recovery_id"]),
                )
            )
        except (HostDirectoryError, OSError, ValueError) as exc:
            return json_error(str(exc), status_code=400)

    return [
        Route("/api/admin/host-directories", get_host_directories, methods=["GET"]),
        Route("/api/admin/host-directories", put_host_directories, methods=["PUT"]),
        Route(
            "/api/admin/host-directories/{directory_id:str}/health",
            check_health,
            methods=["POST"],
        ),
        Route(
            "/api/admin/host-directories/{directory_id:str}/quarantine",
            get_quarantine,
            methods=["GET"],
        ),
        Route(
            "/api/admin/host-directories/{directory_id:str}/quarantine/{recovery_id:str}/restore",
            restore_quarantine,
            methods=["POST"],
        ),
        Route(
            "/api/admin/host-directories/{directory_id:str}/quarantine/{recovery_id:str}/purge",
            purge_quarantine,
            methods=["DELETE"],
        ),
    ]
