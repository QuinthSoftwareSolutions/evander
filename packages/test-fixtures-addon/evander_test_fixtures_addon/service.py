import time
import uuid

from .authority import Authority
from .client import WorkerClient
from .models import Conflict, Control, Ensure, FixtureError, Scope, TERMINAL, Worker, receipt
from .state import Store


class Service:
    def __init__(
        self, runtime_dir, runtime, *, authority=None, client_factory=WorkerClient, now=time.time
    ):
        self.store = Store(runtime_dir / "addons" / "test-fixtures-addon")
        self.authority = authority or Authority(runtime, runtime_dir)
        self.clients = client_factory
        self.now = now

    def scope(self, session_id, task_id, tool):
        # Directory comes from the authenticated session, never a tool argument.
        directory, _ = self.authority.assignment(session_id)
        _, settings = self.store.settings(directory)
        return self.authority.resolve(session_id, task_id, tool, settings), settings

    def acquire(self, session_id, task_id, profile, request_id):
        with self.store.locked():
            # Settings saves use this same lock. Revalidate the current policy
            # and scope before admitting a lease, never a pre-lock snapshot.
            scope, settings = self.scope(session_id, task_id, "test_fixtures_acquire")
            if not settings.allocations_enabled and not settings.drain_only:
                raise FixtureError("New fixture allocations are disabled")
            # Validate profiles before creating a durable request or reserving quota.
            Ensure(
                lease_id="fx-" + "0" * 32,
                scope=scope,
                profile=profile,
                idle_seconds=settings.idle_seconds,
            )
            lease = self.store.acquire(scope, profile, request_id, settings, now=self.now())
            if "worker" not in lease:
                lease["worker"] = settings.workers[scope.worker_id].model_dump()
                self.store.put(lease)
            self.store.audit(session_id, "acquire", lease["lease_id"])
            return self.view(lease, scope)

    def authorize(self, session_id, lease_id, tool):
        lease = self.store.get(lease_id)
        scope, settings = self.scope(session_id, lease["scope"]["task_id"], tool)
        prior = lease["scope"]
        if any(
            prior[key] != getattr(scope, key)
            for key in (
                "session_id",
                "agent_id",
                "team_id",
                "project_id",
                "task_id",
                "worker_id",
                "directory",
            )
        ):
            raise FixtureError("Fixture lease is outside the current task/session/worker scope")
        return lease, scope, settings

    def acquire_ready(self, session_id, task_id, profile, request_id):
        lease = self.acquire(session_id, task_id, profile, request_id)
        return self.reconcile(lease["lease_id"]) if lease["state"] not in TERMINAL else lease

    def get(self, session_id, lease_id):
        lease, scope, _ = self.authorize(session_id, lease_id, "test_fixtures_get")
        return self.view(lease, scope)  # A status read never renews expiry.

    def view(self, lease, scope):
        value = receipt(lease)
        if lease["state"] == "ready" and (
            lease["scope"]["dispatch_id"] != scope.dispatch_id
            or self.now() >= min(lease["scope"]["dispatch_expires_at"], lease["expires_at"])
        ):
            value.update(state="idle", config_path=None, bound_job_id=None)
        return value

    def queue(self, session_id, lease_id, operation, control=None):
        tool = "test_fixtures_" + operation
        with self.store.locked():
            lease, scope, _ = self.authorize(session_id, lease_id, tool)
            if lease["state"] in TERMINAL:
                if operation == "release":
                    return receipt(lease)
                raise FixtureError("Fixture lease has ended")
            if lease.get("operation"):
                raise Conflict("A fixture operation is already pending")
            if operation in {"reset", "control"} and self.view(lease, scope)["state"] != "ready":
                raise FixtureError("Fixture must be ready before reset or broker control")
            if operation == "control" and "rabbitmq" not in lease["profile"]:
                raise FixtureError("This fixture profile has no broker")
            lease["scope"] = scope.model_dump()
            lease["operation"] = dict(
                kind=operation,
                id=uuid.uuid4().hex,
                control=Control.model_validate(control).model_dump()
                if operation == "control"
                else None,
            )
            if operation == "release":
                lease["state"] = "releasing"
            elif operation == "reset":
                lease["state"] = "resetting"
                # A new reset needs its own readiness continuation, including
                # when it returns to ready in the same dispatch.
                lease.pop("notification_key", None)
            else:
                lease["state"] = "controlling"
            self.store.put(lease)
            self.store.audit(session_id, operation, lease_id)
            return receipt(lease)

    def control(self, session_id, lease_id, action, duration_seconds):
        self.queue(
            session_id, lease_id, "control", dict(action=action, duration_seconds=duration_seconds)
        )
        # Fault tests need an acknowledged worker action before testing their
        # application. This bounded call returns applied/failed/pending evidence.
        self.reconcile(lease_id)
        return self.get(session_id, lease_id)

    def operator_release(self, lease_id, expected_revision, actor, directory):
        with self.store.locked():
            lease = self.store.get(lease_id)
            if lease["scope"]["directory"] != directory:
                raise FixtureError("Lease belongs to another directory")
            if lease["revision"] != expected_revision:
                raise Conflict("Lease revision changed; reread before releasing")
            if lease["state"] not in TERMINAL:
                lease["state"] = "releasing"
                lease["operation"] = dict(kind="release", id=uuid.uuid4().hex)
                self.store.put(lease)
            self.store.audit(actor, "operator_release", lease_id)
            return receipt(lease)

    def reconcile(self, lease_id):
        with self.store.locked():
            lease = self.store.get(lease_id)
            if lease["state"] in TERMINAL:
                return receipt(lease)
            scope = Scope.model_validate(lease["scope"])
            _, settings = self.store.settings(scope.directory)
            operation = lease.get("operation")
            try:
                client = self.clients(Worker.model_validate(lease["worker"]))
                owns = self.authority.owns(scope) and scope.team_id in settings.allowed_team_ids
                if owns:
                    _, assigned_url = self.authority.assignment(scope.session_id)
                    owns = assigned_url == lease["worker"]["worker_base_url"]
                if not owns or lease["state"] in {"releasing", "cleanup_failed"}:
                    result = client.call("release", lease_id)
                else:
                    # A new live dispatch may renew/rebind the same task lease.
                    try:
                        current = self.authority.resolve(
                            scope.session_id, scope.task_id, "test_fixtures_acquire", settings
                        )
                    except Exception:
                        current = None
                    if current is not None and current.worker_id != scope.worker_id:
                        result = client.call("release", lease_id)
                    elif current is None:
                        # Worker-local expiry is authoritative during CP outages.
                        if (
                            lease["state"] in {"provisioning", "failed"}
                            and self.now() < scope.dispatch_expires_at
                            and not operation
                        ):
                            # Continue the accepted creation intent after checking
                            # ownership above. No job means no renewal or binding.
                            result = client.call(
                                "ensure",
                                lease_id,
                                Ensure(
                                    lease_id=lease_id,
                                    scope=scope,
                                    profile=lease["profile"],
                                    idle_seconds=settings.idle_seconds,
                                ).model_dump(),
                            )
                            lease["worker_seen"] = True
                        else:
                            result = (
                                client.call("idle", lease_id) if lease.get("worker_seen") else None
                            )
                            if (
                                operation
                                and operation["kind"] == "reset"
                                and result
                                and result.get("state") not in TERMINAL
                            ):
                                # Reset still requires a live authorized job.
                                # Wake its owner instead of silently retaining
                                # an operation that cannot progress while idle.
                                result.update(
                                    state="awaiting_job", config_path=None, bound_job_id=None
                                )
                        if self.now() >= lease["expires_at"]:
                            result = client.call("release", lease_id)
                    else:
                        lease["scope"] = current.model_dump()
                        if operation and operation["kind"] in {"reset", "control"}:
                            try:
                                self.authority.resolve(
                                    scope.session_id,
                                    scope.task_id,
                                    "test_fixtures_" + operation["kind"],
                                    settings,
                                )
                            except Exception:
                                lease["state"] = "releasing"
                                lease["operation"] = dict(kind="release", id=uuid.uuid4().hex)
                                self.store.put(lease)
                                return receipt(lease)
                        request = Ensure(
                            lease_id=lease_id,
                            scope=current,
                            profile=lease["profile"],
                            idle_seconds=settings.idle_seconds,
                        ).model_dump()
                        result = client.call("ensure", lease_id, request)
                        lease["worker_seen"] = True
                        if operation:
                            kind = operation["kind"]
                            payload = {"operation_id": operation["id"]}
                            if kind == "control":
                                payload["control"] = operation["control"]
                            if result.get("state") == "ready" or (
                                kind == "reset" and result.get("state") == "resetting"
                            ):
                                result = client.call(kind, lease_id, payload)
                                lease["operation"] = None
                                if kind == "reset":
                                    result = client.call("ensure", lease_id, request)
                if result:
                    for key in (
                        "state",
                        "expires_at",
                        "config_path",
                        "endpoints",
                        "resource_counts",
                        "error",
                        "fault_until",
                        "bound_job_id",
                        "last_control",
                    ):
                        if key in result:
                            lease[key] = result[key]
                    if lease["state"] in TERMINAL:
                        lease["operation"] = None
                elif self.now() >= lease["expires_at"]:
                    lease["state"] = "expired"
                lease["reconcile_error"] = None
            except Exception:
                lease["error"] = "Fixture reconciliation failed; automatic retry is pending"
                lease["reconcile_error"] = True
                if lease["state"] == "releasing":
                    lease["state"] = "cleanup_failed"
                elif lease["state"] not in {"cleanup_failed", "released", "expired"}:
                    lease["state"] = "failed"
            self.store.put(lease)
            notification_key = dict(state=lease["state"])
            if lease["state"] == "awaiting_job":
                # A dispatch that exits during provisioning needs a new handoff.
                # Ordinary rebinds/failures must not wake another dispatch just
                # because the previous readiness directive caused this one.
                notification_key["dispatch_id"] = lease["scope"]["dispatch_id"]
            if (
                lease["state"] in {"ready", "awaiting_job", "failed", "cleanup_failed"}
                and lease.get("notification_key") != notification_key
            ):
                try:
                    # Never wake a reassigned/cancelled task's former agent.
                    if self.authority.owns(Scope.model_validate(lease["scope"])):
                        self.authority.notify(receipt(lease))
                    lease["notified"] = lease["state"]
                    lease["notification_key"] = notification_key
                    self.store.put(lease)
                except Exception:
                    pass  # Durable notification is retried by the next poll.
            return receipt(lease)
