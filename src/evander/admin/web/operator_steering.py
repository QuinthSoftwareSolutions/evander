from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import (
    WORKFLOW_RUNS_CONTROL_PERMISSION,
    WORKFLOW_RUNS_READ_PERMISSION,
)
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _session_login(auth: AdminWebAuth, request: Request) -> str | None:
    session = auth.read_session(request)
    if session is None:
        return None
    for key in ("login", "preferred_username", "username", "name", "sub"):
        value = str(session.get(key) or "").strip()
        if value:
            return value
    return None


def _limit_from_query(value: str | None) -> int:
    if value is None or not value.strip():
        return 50
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError("limit must be an integer") from exc
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    return limit


def operator_steering_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[Route]:
    async def active_context(request: Request) -> JSONResponse:
        try:
            auth.require(request)
            session_id = str(request.query_params.get("session_id") or "").strip()
            context = service.model_usage_admin.active_operator_steering_context(session_id=session_id)
            directory_path = service.model_usage_admin.operator_steering_directory_path(
                session_id=session_id,
                dispatch_id=context.dispatch_id if context is not None else None,
            )
            auth.require_permission(
                request, WORKFLOW_RUNS_READ_PERMISSION, directory_path=directory_path
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(
            {
                "session_id": session_id,
                "directory_path": directory_path,
                "context": context.model_dump(mode="json") if context is not None else None,
            }
        )

    async def list_notes(request: Request) -> JSONResponse:
        try:
            auth.require(request)
            session_id = str(request.query_params.get("session_id") or "").strip()
            dispatch_id = str(request.query_params.get("dispatch_id") or "").strip() or None
            directory_path = service.model_usage_admin.operator_steering_directory_path(
                session_id=session_id,
                dispatch_id=dispatch_id,
            )
            auth.require_permission(
                request, WORKFLOW_RUNS_READ_PERMISSION, directory_path=directory_path
            )
            notes = service.operator_steering_admin.list_operator_steering_notes(
                session_id=session_id,
                dispatch_id=dispatch_id,
                limit=_limit_from_query(request.query_params.get("limit")),
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(
            {
                "session_id": session_id,
                "dispatch_id": dispatch_id,
                "directory_path": directory_path,
                "notes": [note.model_dump(mode="json") for note in notes],
            }
        )

    async def create_note(request: Request) -> JSONResponse:
        try:
            auth.require(request)
            payload = await _json_body(request)
            session_id = str(payload.get("session_id") or "").strip()
            dispatch_id = str(payload.get("dispatch_id") or "").strip()
            directory_path = service.model_usage_admin.operator_steering_directory_path(
                session_id=session_id,
                dispatch_id=dispatch_id,
            )
            auth.require_permission(
                request, WORKFLOW_RUNS_CONTROL_PERMISSION, directory_path=directory_path
            )
            if not request_has_privileged_operations(auth=auth, service=service, request=request):
                return json_error("Privileged operations are locked.", status_code=403)
            result = service.model_usage_admin.submit_operator_steering_note(
                session_id=session_id,
                dispatch_id=dispatch_id,
                worker_execution_id=str(payload.get("worker_execution_id") or "").strip() or None,
                message=str(payload.get("message") or ""),
                created_by=_session_login(auth, request),
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        response_payload = {"directory_path": directory_path, **dict(result)}
        note = response_payload.get("note")
        if hasattr(note, "model_dump"):
            response_payload["note"] = note.model_dump(mode="json")
        return JSONResponse(response_payload)

    async def cancel_note(request: Request) -> JSONResponse:
        try:
            auth.require(request)
            note_id = str(request.path_params.get("note_id") or "").strip()
            existing = service.operator_steering_admin.get_operator_steering_note(note_id)
            directory_path = service.model_usage_admin.operator_steering_directory_path(
                session_id=existing.session_id,
                dispatch_id=existing.dispatch_id,
            )
            auth.require_permission(
                request, WORKFLOW_RUNS_CONTROL_PERMISSION, directory_path=directory_path
            )
            if not request_has_privileged_operations(auth=auth, service=service, request=request):
                return json_error("Privileged operations are locked.", status_code=403)
            note = service.operator_steering_admin.cancel_operator_steering_note(note_id)
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(
            {
                "directory_path": directory_path,
                "note": note.model_dump(mode="json"),
            }
        )

    return [
        Route("/api/operator-steering/context", active_context, methods=["GET"]),
        Route("/api/operator-steering/notes", list_notes, methods=["GET"]),
        Route("/api/operator-steering/notes", create_note, methods=["POST"]),
        Route(
            "/api/operator-steering/notes/{note_id}/cancel",
            cancel_note,
            methods=["POST"],
        ),
    ]
