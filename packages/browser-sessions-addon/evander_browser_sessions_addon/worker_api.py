"""Bearer-authenticated worker-only API. Run on the private worker network."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from .models import BrowserError
from .relay import error, require_internal, read_json
from .worker import Supervisor, WorkerSettings


def create_app(settings, *, supervisor=None):
    supervisor = supervisor or Supervisor(settings)

    async def loop(stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(supervisor.reconcile)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), 2)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(supervisor.reconcile, startup=True)
        stop = asyncio.Event()
        task = asyncio.create_task(loop(stop))
        yield
        stop.set()
        await task

    async def handle(request):
        try:
            require_internal(request, os.environ.get(settings.token_env, ""))
            if request.url.path == "/healthz":
                return JSONResponse(
                    {
                        "ok": True,
                        "worker_id": settings.worker_id,
                        "capabilities": {"browser_sessions": 1},
                    }
                )
            if "parent_id" in request.path_params:
                result = await asyncio.to_thread(
                    supervisor.workspaces, request.path_params["parent_id"]
                )
            elif request.path_params.get("action") == "close":
                result = await asyncio.to_thread(
                    supervisor.close, request.path_params["session_id"]
                )
            elif request.path_params.get("action"):
                raise BrowserError("Unknown worker operation", "not_found", 404)
            elif request.method == "POST":
                payload = await read_json(request)
                if payload.get("session", {}).get("id") != request.path_params["session_id"]:
                    raise BrowserError("Session identity mismatch")
                result = await asyncio.to_thread(supervisor.create, payload)
            elif request.method == "DELETE":
                result = await asyncio.to_thread(
                    supervisor.close, request.path_params["session_id"]
                )
            else:
                result = supervisor.receipt(supervisor.get(request.path_params["session_id"]))
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (BrowserError, ValueError, KeyError) as exc:
            return error(exc)

    return Starlette(
        routes=[
            Route("/healthz", handle),
            Route("/v1/workspaces/{parent_id}", handle),
            Route("/v1/sessions/{session_id}", handle, methods=["GET", "POST", "DELETE"]),
            Route("/v1/sessions/{session_id}/{action}", handle, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11891)
    args = parser.parse_args()
    settings = WorkerSettings.model_validate_json(Path(args.config).read_text())
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
