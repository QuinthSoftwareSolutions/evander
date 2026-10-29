"""One application lifecycle shared by MCP, workflow nodes and Admin Web."""

import secrets
import time
from evander.resources.session_owner import session_owner, Peer
from evander.resources.endpoints import Endpoints, Endpoint
from evander.resources.models import ResourceError
from evander.session_dependencies import SessionDependencies
from .models import Environment
from .state import Store


class Service:
    def __init__(self, runtime_dir, service, *, parent=None, peers=Peer, now=time.time):
        self.runtime_dir, self.service, self.now = runtime_dir, service, now
        self.parent = parent or (
            lambda id, tool=None: session_owner(runtime_dir, service, id, tool)
        )
        self.peers = peers
        self.store = Store(runtime_dir)
        self.endpoints = Endpoints(runtime_dir, now=now)
        self.dependencies = SessionDependencies(runtime_dir)

    def placement(self, parent):
        settings = self.store.settings(parent.directory)[1]
        matches = [
            (key, value)
            for key, value in settings.workers.items()
            if value.parent_worker_url == parent.worker_url
            and (parent.worker_id is None or key == parent.worker_id)
        ]
        if len(matches) != 1:
            raise ResourceError("The assigned worker has no application supervisor")
        return settings, *matches[0]

    def workspaces(self, session_id):
        parent = self.parent(session_id, "application_environment_workspaces")
        _, _, worker = self.placement(parent)
        return self.peers(worker.base_url, worker.token_env).call(
            "GET", "/v1/workspaces/" + session_id
        )

    def create(self, session_id, request, *, tool="application_environment_create"):
        parent = self.parent(session_id, tool)
        settings, worker_id, worker = self.placement(parent)
        if not settings.allocations_enabled:
            raise ResourceError("Application allocations are disabled")
        profile = settings.profiles.get(request.profile_id)
        if profile is None:
            raise ResourceError("Select an approved application profile")
        self.reconcile_expired(worker_id)
        now = self.now()
        environment = Environment(
            id="environment-" + secrets.token_hex(12),
            agent_session_id=session_id,
            directory=parent.directory,
            worker_id=worker_id,
            profile_id=request.profile_id,
            workspace_ref=request.workspace_ref,
            launch_manifest_path=request.launch_manifest_path,
            title=request.title,
            created_at=now,
            expires_at=min(parent.expires_at, now + settings.lifetime_seconds),
        )
        record, created = self.store.create(
            {
                "session": environment.model_dump(),
                "connection": worker.model_dump(),
                "profile": profile.model_dump(),
                "state": "starting",
                "endpoints": [],
                "error": None,
            },
            request.model_dump(),
            settings.max_per_worker,
        )
        if created:
            id = record["session"]["id"]
            with self.store.locked(id):
                record = self.store.get(id)
                if record["state"] != "starting":
                    return self.receipt(record)
                self.dependencies.register(
                    session_id,
                    id,
                    worker.base_url + "/v1/environments/" + id + "/close",
                    worker.token_env,
                )
                try:
                    health = self.peers(worker.base_url, worker.token_env).call("GET", "/healthz")
                    if (
                        health.get("capabilities", {}).get("application_environments") != 1
                        or health.get("worker_id") != worker_id
                    ):
                        raise ResourceError("Application worker is incompatible")
                    result = self.peers(worker.base_url, worker.token_env).call(
                        "POST",
                        "/v1/environments/" + id,
                        {"session": record["session"], "profile": record["profile"]},
                    )
                    self.update(record, result)
                    if result.get("workspace_path"):
                        self.dependencies.register(
                            "workspace:" + session_id + ":" + result["workspace_path"],
                            id + ":workspace",
                            worker.base_url + "/v1/environments/" + id + "/close",
                            worker.token_env,
                        )
                except ResourceError:
                    record["state"], record["error"] = (
                        "failed",
                        "Startup result unavailable; inspect before retrying",
                    )
                    self.store.put(record)
        return self.receipt(record)

    def reconcile_expired(self, worker_id):
        # Worker expiry and parent cleanup call the pinned worker directly. Confirm
        # release here before reusing its quota, even after the old owner expires.
        for candidate in self.store.active(worker_id):
            id = candidate["session"]["id"]
            with self.store.locked(id):
                record = self.store.get(id)
                if (
                    record["state"] not in {"closed", "expired"}
                    and self.now() >= record["session"]["expires_at"]
                ):
                    try:
                        self._close(id)
                    except ResourceError:
                        # Unconfirmed cleanup still reserves capacity and guards.
                        pass

    def update(self, record, result):
        if result.get("id") != record["session"]["id"]:
            raise ResourceError("Application worker returned another resource")
        record.update(
            state="ready" if result["state"] == "running" else result["state"],
            error=result.get("error"),
            endpoints=result.get("endpoints", []),
        )
        session = record["session"]
        for item in record["endpoints"]:
            self.endpoints.put(
                Endpoint(
                    **item,
                    resource_id=session["id"],
                    session_id=session["agent_session_id"],
                    directory=session["directory"],
                    worker_id=session["worker_id"],
                    expires_at=session["expires_at"],
                    ready=record["state"] == "ready",
                )
            )
        if record["state"] != "ready":
            self.endpoints.revoke(session["id"])
        self.store.put(record)

    def owned(self, session_id, id, tool):
        parent = self.parent(session_id, tool)
        record = self.store.get(id, parent.directory)
        if record["session"]["agent_session_id"] != session_id:
            raise ResourceError("Application belongs to another agent")
        return record

    def status(self, session_id, id, *, tool="application_environment_status"):
        self.owned(session_id, id, tool)
        with self.store.locked(id):
            return self._status(self.store.get(id))

    def _status(self, record):
        id = record["session"]["id"]
        if record["state"] not in {"closed", "expired"}:
            try:
                c = record["connection"]
                result = self.peers(c["base_url"], c["token_env"]).call(
                    "GET", "/v1/environments/" + id
                )
                self.update(record, result)
            except ResourceError:
                return {
                    **self.receipt(record),
                    "state": "unknown",
                    "error": "Worker state is unavailable",
                }
        return self.receipt(record)

    def close(self, id, *, directory=None):
        with self.store.locked(id):
            return self._close(id, directory=directory)

    def _close(self, id, *, directory=None):
        record = self.store.get(id, directory)
        self.endpoints.revoke(id)
        c = record["connection"]
        try:
            result = self.peers(c["base_url"], c["token_env"]).call(
                "POST", "/v1/environments/" + id + "/close", {}
            )
            if result.get("released") is not True:
                raise ResourceError("Application cleanup is unconfirmed")
        except ResourceError:
            record["state"], record["error"] = (
                "cleanup_failed",
                "Cleanup is unconfirmed; dependencies retained",
            )
            self.store.put(record)
            raise
        record["state"], record["error"] = "closed", None
        self.store.put(record)
        self.dependencies.remove(id)
        self.dependencies.remove(id + ":workspace")
        return self.receipt(record)

    @staticmethod
    def receipt(record):
        return {
            **record["session"],
            "state": record["state"],
            "error": record["error"],
            "endpoints": record["endpoints"] if record["state"] == "ready" else [],
        }
