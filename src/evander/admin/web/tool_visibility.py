from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.shared import json_error


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def tool_visibility_routes(service: AdminRuntimeComponents) -> list[Route]:
    async def get_tool_visibility(request: Request) -> JSONResponse:
        params = request.query_params
        session_id = _optional_text(params.get("session_id"))
        if not session_id:
            return json_error("session_id is required.")
        try:
            payload = await run_in_threadpool(
                service.approval_audit_admin.tool_visibility_payload,
                target=session_id,
                workflow_run_id=_optional_text(params.get("workflow_run_id")),
                mcp_tool_approval_lease_id=_optional_text(params.get("mcp_tool_approval_lease_id")),
                mcp_transport_session_id=_optional_text(params.get("mcp_transport_session_id")),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown session" in str(exc) else 400)
        return JSONResponse(payload.model_dump(mode="json"))

    async def get_session_tool_visibility(request: Request) -> JSONResponse:
        session_id = _optional_text(request.path_params.get("session_id"))
        if not session_id:
            return json_error("session_id is required.")
        try:
            payload = await run_in_threadpool(
                service.approval_audit_admin.tool_visibility_payload,
                target=session_id,
                workflow_run_id=_optional_text(request.query_params.get("workflow_run_id")),
                mcp_tool_approval_lease_id=_optional_text(
                    request.query_params.get("mcp_tool_approval_lease_id")
                ),
                mcp_transport_session_id=_optional_text(
                    request.query_params.get("mcp_transport_session_id")
                ),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown session" in str(exc) else 400)
        return JSONResponse(payload.model_dump(mode="json"))

    return [
        Route("/api/admin/tool-visibility", get_tool_visibility, methods=["GET"]),
        Route(
            "/api/admin/sessions/{session_id:str}/tool-visibility",
            get_session_tool_visibility,
            methods=["GET"],
        ),
    ]
