from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import AdminWebAuth, json_error


def workflow_event_routes(service: AdminService, auth: AdminWebAuth) -> list[Route]:
    async def mint_token(request: Request) -> JSONResponse:
        try:
            payload = auth.issue_workflow_event_api_token(request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        return JSONResponse(payload)

    async def publish_event(request: Request) -> JSONResponse:
        authorization = request.headers.get("authorization", "")
        scheme, _separator, credentials = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credentials.strip():
            return json_error("Workflow event bearer token is required", status_code=401)
        claims = auth.verify_workflow_event_api_token(credentials)
        if claims is None:
            return json_error("Workflow event bearer token is invalid or expired", status_code=401)

        try:
            body = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(body, dict):
            return json_error("Request body must be a JSON object")

        payload = body.get("payload", {})
        if not isinstance(payload, dict):
            return json_error("payload must be a JSON object")

        try:
            event = service.publish_external_workflow_event(
                event_type=body.get("event_type"),
                payload=payload,
                correlation_key=body.get("correlation_key"),
                idempotency_key=_optional_string(body.get("idempotency_key")),
                source={
                    "source_type": "workflow_event_api",
                    "user_id": _optional_string(claims.get("sub")),
                    "user_login": _optional_string(claims.get("login")),
                },
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(event, status_code=202)

    return [
        Route("/api/workflow-events/token", mint_token, methods=["POST"]),
        Route("/api/workflow-events", publish_event, methods=["POST"]),
    ]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
