from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services.connector_admin_service import ConnectorConfigRevisionConflict
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def connector_routes(
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
    ) -> dict[str, Any] | JSONResponse:
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

    async def get_connectors(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=False)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(service.connector_admin.snapshot(directory_path))
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    async def put_connectors(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        expected_revision = str(payload.get("expected_revision") or "").strip()
        if not expected_revision:
            return json_error("expected_revision is required", status_code=428)
        try:
            return JSONResponse(
                service.connector_admin.save(
                    directory_path,
                    expected_revision=expected_revision,
                    config_payload=payload.get("config"),
                )
            )
        except ConnectorConfigRevisionConflict as exc:
            return json_error(str(exc), status_code=409)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    async def test_connector(request: Request) -> JSONResponse:
        try:
            directory_path = _directory_path(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        owner = _require_owner(request, directory_path=directory_path, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(
                await service.connector_admin.test_connector(
                    directory_path,
                    request.path_params.get("connector_id"),
                    actor_user_id=str(owner.get("login") or "").strip() or None,
                )
            )
        except Exception as exc:
            return json_error(str(exc), status_code=400)

    return [
        Route("/api/admin/connectors", get_connectors, methods=["GET"]),
        Route("/api/admin/connectors", put_connectors, methods=["PUT"]),
        Route(
            "/api/admin/connectors/{connector_id:str}/test",
            test_connector,
            methods=["POST"],
        ),
    ]
