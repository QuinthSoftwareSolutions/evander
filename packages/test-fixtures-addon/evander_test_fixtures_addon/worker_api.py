import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import json
import os
from pathlib import Path

from pydantic import Field, ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .models import Contract, Control, Ensure, FixtureError, WorkerSettings
from .worker import WorkerService


class Operation(Contract):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    control: Control | None = None


class LeaseId(Contract):
    lease_id: str = Field(pattern=r"^fx-[a-f0-9]{32}$")


async def read_body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 16_384:
            raise FixtureError("Request exceeds fixture API limit")
    return json.loads(data or b"{}")


def create_app(settings, *, service=None, periodic=True):
    service = service or WorkerService(settings)

    @asynccontextmanager
    async def lifespan(app):
        async def tick():
            while True:
                try:
                    await run_in_threadpool(service.tick)
                except Exception:
                    # Timer retries and durable lease diagnostics hold details.
                    pass
                await asyncio.sleep(2)

        task = asyncio.create_task(tick()) if periodic else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def endpoint(request):
        token = os.environ.get(settings.token_env, "")
        supplied = request.headers.get("authorization", "")
        if not token or not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            return JSONResponse({"error": "Worker authentication required"}, status_code=401)
        try:
            if request.url.path == "/healthz":
                return JSONResponse(
                    dict(
                        protocol=1,
                        version="0.1.2",
                        worker_id=settings.worker_id,
                        deployment_revision=os.environ.get("EVANDER_WORKER_DEPLOYMENT_REVISION"),
                        allocations_enabled=settings.allocations_enabled,
                        capabilities=dict(
                            sqlserver=settings.sql_enabled, rabbitmq=bool(settings.rabbitmq_image)
                        ),
                        rabbitmq_image=settings.rabbitmq_image,
                    )
                )
            lease_id = LeaseId(lease_id=request.path_params["lease_id"]).lease_id
            op = request.path_params["operation"]
            if op == "get" and request.method == "GET":
                value = await run_in_threadpool(service.get, lease_id)
            elif op == "ensure" and request.method == "POST":
                payload = Ensure.model_validate(await read_body(request))
                if payload.lease_id != lease_id:
                    raise FixtureError("Lease ID mismatch")
                value = await run_in_threadpool(service.ensure, payload)
            elif op == "release" and request.method == "POST":
                value = await run_in_threadpool(service.release, lease_id)
            elif op == "idle" and request.method == "POST":
                value = await run_in_threadpool(service.idle, lease_id)
            elif op in {"reset", "control"} and request.method == "POST":
                payload = Operation.model_validate(await read_body(request))
                if op == "reset":
                    value = await run_in_threadpool(service.reset, lease_id, payload.operation_id)
                else:
                    if payload.control is None:
                        raise FixtureError("Broker control is required")
                    value = await run_in_threadpool(
                        service.control, lease_id, payload.control, payload.operation_id
                    )
            else:
                return JSONResponse({"error": "Unknown fixture operation"}, status_code=404)
            return JSONResponse(value, headers={"Cache-Control": "no-store"})
        except (FixtureError, ValidationError, ValueError):
            return JSONResponse(
                {"error": "Fixture request failed; inspect durable lease status"}, status_code=400
            )
        except Exception:
            return JSONResponse({"error": "Worker fixture operation failed"}, status_code=503)

    app = Starlette(
        routes=[
            Route("/healthz", endpoint),
            Route("/v1/leases/{lease_id}/{operation}", endpoint, methods=["GET", "POST"]),
        ],
        lifespan=lifespan,
    )
    app.state.fixture_service = service
    return app


def main():
    parser = argparse.ArgumentParser(description="Authenticated disposable test fixture worker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11991)
    parser.add_argument("--reap", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    settings = WorkerSettings.model_validate_json(args.config.read_bytes())
    if args.reap:
        WorkerService(settings).tick()
    else:
        import uvicorn

        uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
