"""Authenticated worker lifecycle with an independent reaper and no provider dependencies."""

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import os
from pathlib import Path
import time

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.applications import Starlette

from .credentials import Credentials
from .jobs import Jobs
from .models import Lease, ResourceError, NotReady, TERMINAL, WorkerSettings, receipt
from .state import Store


class WorkerService:
    def __init__(self, addon_id, settings, adapter, *, jobs=None, credentials=None, now=time.time):
        self.addon_id, self.settings, self.adapter, self.now = addon_id, settings, adapter, now
        self.store = Store(settings.runtime_dir)
        self.jobs = jobs or Jobs(addon_id, settings.worker_jobs_db)
        self.credentials = credentials or Credentials(Path(settings.runtime_dir), addon_id)

    def ensure(self, incoming):
        with self.store.locked():
            if incoming.profile.worker_id != self.settings.worker_id:
                raise ResourceError("Worker assignment does not match this service")
            try:
                lease = self.store.get(incoming.lease_id)
            except ResourceError:
                if incoming.operation and incoming.operation["kind"] == "release":
                    self.adapter.verify_absent(
                        incoming, self.settings.connections, self.credentials
                    )
                    incoming.state = "released"
                    incoming.operation = None
                    self.store.put(incoming)
                    return receipt(incoming)
                if not self.settings.allocations_enabled:
                    raise ResourceError("New worker allocations are disabled")
                if (
                    sum(item.state not in TERMINAL for item in self.store.leases(active_only=True))
                    >= self.settings.max_per_worker
                ):
                    raise ResourceError("Worker resource quota is full")
                lease = incoming.model_copy(deep=True)
                lease.resources, lease.job_binding = {}, {}
                if lease.profile.connection_id:
                    lease.connection = self.settings.connections.get(lease.profile.connection_id)
                    if lease.connection is None:
                        raise ResourceError("Worker connection is not registered")
                self.adapter.validate_profile(lease.profile)
                self.store.put(lease)
            if (
                lease.owner.identity() != incoming.owner.identity()
                or lease.profile != incoming.profile
            ):
                raise ResourceError("Worker lease provenance changed")
            if lease.state in TERMINAL:
                return receipt(lease)
            lease.owner = incoming.owner
            lease.expires_at = max(lease.expires_at, incoming.expires_at)
            if incoming.operation and incoming.operation["id"] != lease.last_operation_id:
                if (
                    lease.operation
                    and lease.operation["id"] != incoming.operation["id"]
                    and incoming.operation["kind"] != "release"
                ):
                    raise ResourceError("A different operation is already pending")
                if lease.operation is None or incoming.operation["kind"] == "release":
                    lease.operation = incoming.operation
            self.store.put(lease)
            return self._reconcile(lease)

    def _reconcile(self, lease):
        def save():
            return self.store.put(lease)

        cleanup_failed = False
        try:
            if self.now() >= lease.expires_at:
                lease.operation = {
                    "kind": "release",
                    "id": "expiry:" + lease.lease_id,
                    "arguments": {},
                }
                save()
            operation = lease.operation
            job, job_unknown = None, False
            try:
                job = self.jobs.current(lease.owner)
            except Exception:
                job_unknown = True
            if (
                job is None
                or lease.bound_job_id != job[0].job_id
                or lease.state != "ready"
                or operation
                and operation["kind"] in {"reset", "release"}
            ):
                try:
                    self.jobs.unbind(lease, save)
                except Exception:
                    cleanup_failed = True
            # An explicit release may still remove provider resources when a local
            # withdrawal step needs retrying. Never declare release complete early.
            if operation and operation["kind"] == "release":
                self.adapter.release(lease, save, self.credentials)
                if cleanup_failed:
                    raise ResourceError("Worker binding cleanup is incomplete")
                lease.fault_until = None
                lease.last_operation_id = operation["id"]
                lease.operation = None
                lease.state, lease.error = "released", None
                save()
                return receipt(lease)
            # Fault recovery is independent of the job lookup and local cleanup.
            if lease.fault_until is not None and self.now() >= lease.fault_until:
                self.adapter.recover(lease)
                lease.fault_until = None
                lease.state = "provisioning"
                save()
            if cleanup_failed or job_unknown:
                raise ResourceError("Current worker binding cannot be verified")
            if operation:
                action = operation["kind"]
                if action in {"reset", "control"} and job is None:
                    lease.state = "awaiting_job"
                    save()
                    return receipt(lease)
                if action == "reset":
                    self.adapter.reset(lease, save, self.credentials)
                    lease.fault_until = None
                    lease.state = "provisioning"
                elif action == "control":
                    self.adapter.validate_control(lease.profile, operation["arguments"])
                    args = operation["arguments"]
                    if "deadline" not in operation:
                        operation["deadline"] = (
                            self.now() + args.get("duration_seconds", 60)
                            if args["action"] in {"stop", "pause"}
                            else None
                        )
                    lease.fault_until = operation["deadline"]
                    save()
                    if lease.fault_until is not None and self.now() >= lease.fault_until:
                        self.adapter.recover(lease)
                        lease.fault_until = None
                        status = "recovered_after_unknown_result"
                    else:
                        self.adapter.control(lease, args["action"])
                        status = "applied"
                    lease.last_control = {
                        "operation_id": operation["id"],
                        "action": args["action"],
                        "status": status,
                    }
                else:
                    raise ResourceError("Unsupported worker lifecycle operation")
                lease.last_operation_id = operation["id"]
                lease.operation = None
                save()
            if job is None:
                lease.state = "awaiting_job"
            elif not lease.fault_until:
                # A ready, unchanged job needs no provider I/O on a safety tick.
                # Any existing obsolete binding has already been withdrawn above.
                if lease.state != "ready" or lease.bound_job_id != job[0].job_id:
                    self.adapter.provision(lease, save, self.credentials)
                current = self.jobs.current(lease.owner)
                if current is None:
                    self.jobs.unbind(lease, save)
                    lease.state = "awaiting_job"
                else:
                    if lease.bound_job_id != current[0].job_id:
                        self.jobs.bind(
                            lease, current, self.adapter.private_connections(lease), save
                        )
                    lease.state = "ready"
                    lease.expires_at = self.now() + lease.profile.idle_seconds
            elif lease.bound_job_id == job[0].job_id:
                lease.state = "ready"
                lease.expires_at = self.now() + lease.profile.idle_seconds
            else:
                lease.state = "awaiting_job"
            lease.error = None
        except NotReady:
            lease.state = "provisioning"
            lease.error = None
        except Exception:
            lease.error = "Worker resource operation failed; durable intent retained"
            lease.state = (
                "cleanup_failed"
                if cleanup_failed or lease.operation and lease.operation["kind"] == "release"
                else "failed"
            )
        save()
        return receipt(lease)

    def tick(self):
        for lease in self.store.leases(active_only=True):
            if lease.state not in TERMINAL:
                with self.store.locked():
                    self._reconcile(self.store.get(lease.lease_id))

    def lease_call(self, lease_id, owner, operation, arguments, request_id):
        from .operations import perform

        if operation not in self.adapter.lease_operations:
            raise ResourceError("Operation is not permitted on disposable resources")

        def authorized():
            lease = self.store.get(lease_id)
            job = self.jobs.current(owner)
            if (
                lease.owner.identity() != owner.identity()
                or lease.owner.job_tags != owner.job_tags
                or lease.state != "ready"
                or job is None
                or lease.bound_job_id != job[0].job_id
            ):
                raise ResourceError("Lease data access requires its current authorized job binding")
            return lease

        # A cached mutation receipt still requires the current job and owner.
        authorized()
        result = perform(
            self.store,
            owner,
            lease_id,
            operation,
            arguments,
            request_id,
            lambda: self.adapter.lease_call(authorized(), operation, arguments),
            read=operation in self.adapter.read_operations,
        )
        with self.store.locked():
            lease = authorized()
            lease.owner = owner
            lease.expires_at = self.now() + lease.profile.idle_seconds
            self.store.put(lease)
        return result


async def read_body(request, limit=131072):
    import json

    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ResourceError("Request body exceeds limit")
    value = json.loads(data or b"{}")
    if not isinstance(value, dict):
        raise ResourceError("Expected a JSON object")
    return value


def create_app(service, *, periodic=True):
    @asynccontextmanager
    async def lifespan(app):
        async def tick():
            while True:
                await run_in_threadpool(service.tick)
                await asyncio.sleep(2)

        task = asyncio.create_task(tick()) if periodic else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def endpoint(request):
        token = os.environ.get(service.settings.token_env, "")
        if not token or not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            return JSONResponse({"error": "Worker authentication required"}, status_code=401)
        try:
            if request.url.path == "/healthz":
                return JSONResponse(
                    {
                        "protocol": 1,
                        "addon_id": service.addon_id,
                        "version": "0.1.0",
                        "worker_id": service.settings.worker_id,
                        "allocations_enabled": service.settings.allocations_enabled,
                    }
                )
            if "/connections/" in request.url.path:
                connection_id = request.path_params["connection_id"]
                raw = service.settings.connections[connection_id]
                from evander.models import DirectoryPath

                directory = DirectoryPath(
                    segments=request.query_params.get("directory", "root")
                ).as_posix()
                await run_in_threadpool(
                    service.adapter.readiness, connection_id, raw, directory, service.credentials
                )
                return JSONResponse({"status": "ready", "code": "connected"})
            if request.method == "GET":
                return JSONResponse(receipt(service.store.get(request.path_params["lease_id"])))
            payload = await read_body(request)
            if payload.get("addon_id") != service.addon_id:
                raise ResourceError("Resource worker add-on identity does not match")
            if request.url.path.endswith("/call"):
                from .models import Owner

                value = await run_in_threadpool(
                    service.lease_call,
                    request.path_params["lease_id"],
                    Owner.model_validate(payload["owner"]),
                    payload["operation"],
                    payload["arguments"],
                    payload.get("request_id", ""),
                )
                return JSONResponse(value, headers={"Cache-Control": "no-store"})
            lease = Lease.model_validate(payload["lease"])
            if lease.lease_id != request.path_params["lease_id"]:
                raise ResourceError("Lease ID mismatch")
            result = await run_in_threadpool(service.ensure, lease)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (ValueError, KeyError):
            return JSONResponse({"error": "Invalid resource worker request"}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Resource worker unavailable"}, status_code=503)

    return Starlette(
        routes=[
            Route("/healthz", endpoint),
            Route("/v1/connections/{connection_id}/readiness", endpoint),
            Route("/v1/leases/{lease_id}/get", endpoint),
            Route("/v1/leases/{lease_id}/ensure", endpoint, methods=["POST"]),
            Route("/v1/leases/{lease_id}/call", endpoint, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def main(addon_id, adapter_factory, default_port):
    parser = argparse.ArgumentParser(description="Optional resource worker")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--reap", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    settings = WorkerSettings.model_validate_json(args.config.read_bytes())
    service = WorkerService(addon_id, settings, adapter_factory(settings))
    if args.reap:
        service.tick()
    else:
        import uvicorn

        uvicorn.run(create_app(service), host=args.host, port=args.port, access_log=False)
