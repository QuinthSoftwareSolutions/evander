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
from evander.resources.container_runtime import RuntimeError as WorkerError
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
            if request.path_params.get("action") in {"connect", "fence"}:
                payload = await read_json(request)
                result = await asyncio.to_thread(
                    getattr(supervisor, request.path_params["action"]),
                    request.path_params["session_id"],
                    payload,
                )
            elif request.path_params.get("action", "").startswith("detach-"):
                result = await asyncio.to_thread(
                    supervisor.detach_resource,
                    request.path_params["session_id"],
                    request.path_params["action"][7:],
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
        except (BrowserError, WorkerError, ValueError, KeyError) as exc:
            return error(exc)

    return Starlette(
        routes=[
            Route("/healthz", handle),
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
