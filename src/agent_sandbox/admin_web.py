from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .admin_service import AdminService


def _dist_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "workflow-editor" / "dist"


def _fallback_html(dist_dir: Path) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Workflow Editor</title>
    <style>
      body {{
        font-family: ui-sans-serif, system-ui, sans-serif;
        margin: 0;
        padding: 3rem;
        background: #f4f1e8;
        color: #1e1b16;
      }}
      main {{
        max-width: 42rem;
        background: #fffaf1;
        border: 1px solid #d7cdbb;
        border-radius: 18px;
        padding: 2rem;
        box-shadow: 0 18px 60px rgba(58, 45, 20, 0.08);
      }}
      code {{
        background: #efe7d7;
        border-radius: 6px;
        padding: 0.12rem 0.33rem;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Workflow editor frontend not built</h1>
      <p>The API is available, but the static frontend bundle was not found at <code>{dist_dir}</code>.</p>
      <p>Build the React app in <code>frontend/workflow-editor</code> and reload this page.</p>
    </main>
  </body>
</html>
"""


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


async def _workflow_run_events(service: AdminService, request: Request):
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


def create_app(service: AdminService) -> Starlette:
    dist_dir = _dist_dir()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def get_document(_: Request) -> JSONResponse:
        try:
            document = service.read_workflow_editor_document()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(document.model_dump(mode="json"))

    async def get_catalog(_: Request) -> JSONResponse:
        try:
            catalog = service.read_workflow_editor_catalog()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(catalog.model_dump(mode="json"))

    async def get_runs(_: Request) -> JSONResponse:
        try:
            snapshot = service.read_workflow_run_snapshot()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(snapshot.model_dump(mode="json"))

    async def stream_runs(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _workflow_run_events(service, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    async def validate_document(request: Request) -> JSONResponse:
        try:
            payload = await _json_body(request)
            validation = service.validate_workflow_editor_document(
                config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
                layout=payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(validation.model_dump(mode="json"))

    async def save_document(request: Request) -> JSONResponse:
        try:
            payload = await _json_body(request)
            save_result = service.save_workflow_editor_document(
                config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
                layout=payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(save_result.model_dump(mode="json"))

    async def index(_: Request) -> Response:
        return HTMLResponse(_fallback_html(dist_dir))

    routes = [
        Route("/healthz", health, methods=["GET"]),
        Route("/api/workflow-editor/document", get_document, methods=["GET"]),
        Route("/api/workflow-editor/catalog", get_catalog, methods=["GET"]),
        Route("/api/workflow-editor/runs", get_runs, methods=["GET"]),
        Route("/api/workflow-editor/runs/stream", stream_runs, methods=["GET"]),
        Route("/api/workflow-editor/validate", validate_document, methods=["POST"]),
        Route("/api/workflow-editor/document", save_document, methods=["PUT"]),
    ]
    if dist_dir.exists():
        routes.append(Mount("/", app=StaticFiles(directory=dist_dir, html=True)))
    else:
        routes.append(Route("/", index, methods=["GET"]))

    return Starlette(debug=False, routes=routes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local workflow editor admin web app.")
    parser.add_argument(
        "--runtime-dir",
        "--config",
        default=str(Path(__file__).resolve().parents[2] / "runtime"),
        help="Runtime directory root. A legacy gateway YAML path is accepted for bootstrap compatibility.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for the local admin web app.")
    parser.add_argument("--port", type=int, default=8100, help="Bind port for the local admin web app.")
    args = parser.parse_args()

    service = AdminService(Path(args.runtime_dir))
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="info")


__all__ = ["create_app", "main"]
