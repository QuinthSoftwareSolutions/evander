from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import json_error


async def json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


async def workflow_run_events(service: AdminService, request: Request) -> AsyncIterator[str]:
    last_state_key: str | None = None
    journal = service.workflow_event_journal()
    cursor = journal.cursor()

    async def snapshot_event() -> tuple[str, str] | None:
        if await request.is_disconnected():
            return None
        try:
            snapshot = service.read_workflow_run_snapshot()
        except ValueError as exc:
            payload = json.dumps({"error": str(exc)})
            return f"error:{payload}", f"event: error\ndata: {payload}\n\n"

        payload = json.dumps(snapshot.model_dump(mode="json"))
        state_key = json.dumps(
            {
                "runs": [run.model_dump(mode="json") for run in snapshot.runs],
                "node_activity": snapshot.node_activity,
            },
            sort_keys=True,
        )
        return state_key, f"event: snapshot\ndata: {payload}\n\n"

    first = await snapshot_event()
    if first is None:
        return
    last_state_key, first_payload = first
    yield first_payload

    while True:
        if await request.is_disconnected():
            break
        events, cursor = await journal.wait_after(cursor, timeout_seconds=15.0)
        if not events:
            yield ": keepalive\n\n"
            continue
        event = await snapshot_event()
        if event is None:
            break
        state_key, payload = event
        if state_key != last_state_key:
            last_state_key = state_key
            yield payload
        else:
            yield ": keepalive\n\n"


def workflow_routes(service: AdminService) -> list[Route]:
    async def get_document(_: Request) -> JSONResponse:
        try:
            document = service.read_workflow_editor_document()
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(document.model_dump(mode="json"))

    async def get_catalog(_: Request) -> JSONResponse:
        try:
            catalog = service.read_workflow_editor_catalog()
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(catalog.model_dump(mode="json"))

    async def get_runs(_: Request) -> JSONResponse:
        try:
            snapshot = service.read_workflow_run_snapshot()
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(snapshot.model_dump(mode="json"))

    async def clear_run(request: Request) -> JSONResponse:
        event_id = request.path_params.get("event_id", "")
        try:
            payload = service.clear_workflow_run(event_id)
        except ValueError as exc:
            status_code = 404 if "not found or already inactive" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse(payload.model_dump(mode="json"))

    async def resume_run(request: Request) -> JSONResponse:
        event_id = request.path_params.get("event_id", "")
        try:
            payload = service.resume_workflow_run(event_id)
        except ValueError as exc:
            status_code = 404 if "not resumable" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse(payload.model_dump(mode="json"))

    async def stream_runs(request: Request) -> StreamingResponse:
        return StreamingResponse(
            workflow_run_events(service, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    async def validate_document(request: Request) -> JSONResponse:
        try:
            payload = await json_body(request)
            validation = service.validate_workflow_editor_document(
                config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
                layout=payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(validation.model_dump(mode="json"))

    async def save_document(request: Request) -> JSONResponse:
        try:
            payload = await json_body(request)
            save_result = service.save_workflow_editor_document(
                config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
                layout=payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(save_result.model_dump(mode="json"))

    return [
        Route("/api/workflow-editor/document", get_document, methods=["GET"]),
        Route("/api/workflow-editor/catalog", get_catalog, methods=["GET"]),
        Route("/api/workflow-editor/runs", get_runs, methods=["GET"]),
        Route("/api/workflow-editor/runs/{event_id:path}", clear_run, methods=["DELETE"]),
        Route("/api/workflow-editor/runs/{event_id:path}/resume", resume_run, methods=["POST"]),
        Route("/api/workflow-editor/runs/stream", stream_runs, methods=["GET"]),
        Route("/api/workflow-editor/validate", validate_document, methods=["POST"]),
        Route("/api/workflow-editor/document", save_document, methods=["PUT"]),
    ]
