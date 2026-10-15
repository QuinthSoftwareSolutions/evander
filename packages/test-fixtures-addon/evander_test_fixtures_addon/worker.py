import os
from pathlib import Path
import secrets
import time

from evander.worker.models import WorkerMssqlDatabaseRequest
from evander.worker.mssql import WorkerMssqlProvisioner, WorkerMssqlStore
from evander.worker.store import WorkerJobStore

from .docker import Docker
from .models import Ensure, FixtureError, NotReady, TERMINAL, receipt
from .state import Store


class WorkerService:
    def __init__(self, settings, *, docker=None, sql=None, jobs=None, now=time.time):
        self.settings = settings
        self.store = Store(settings.runtime_dir)
        self.docker = docker or Docker(self.store.root, settings.rabbitmq_image)
        self.sql = sql or WorkerMssqlProvisioner(
            enabled=settings.sql_enabled,
            store=WorkerMssqlStore(self.store.root / "sql.json"),
            admin_user=settings.sql_admin_user,
            admin_password=os.environ.get(settings.sql_password_env),
            admin_host=settings.sql_host,
            port=settings.sql_port,
            container_host=settings.sql_container_host,
            sqlcmd_path=settings.sqlcmd_path,
        )
        self.jobs = jobs or WorkerJobStore(Path(settings.worker_jobs_db))
        self.now = now

    def job(self, scope):
        if self.now() >= scope["dispatch_expires_at"]:
            return None
        jobs = [
            job
            for job in self.jobs.list_by_session(scope["session_id"])
            if job.status == "running"
            and job.metadata.get("dispatch_id") == scope["dispatch_id"]
            and self.docker.current_job(job)
        ]
        if len(jobs) > 1:
            raise FixtureError("Ambiguous owning worker job")
        return jobs[0] if jobs else None

    def ensure(self, request: Ensure):
        with self.store.locked():
            if request.scope.worker_id != self.settings.worker_id:
                raise FixtureError("Worker assignment mismatch")
            try:
                lease = self.store.get(request.lease_id)
            except FixtureError:
                if not self.settings.allocations_enabled:
                    raise FixtureError("Worker fixture allocations are disabled")
                active = [item for item in self.store.list() if item["state"] not in TERMINAL]
                if any(item["scope"]["task_id"] == request.scope.task_id for item in active):
                    raise FixtureError("Task already has a worker fixture bundle")
                if len(active) >= self.settings.max_per_worker:
                    raise FixtureError("Worker fixture quota reached")
                lease = dict(
                    lease_id=request.lease_id,
                    scope=request.scope.model_dump(),
                    profile=request.profile,
                    state="provisioning",
                    created_at=self.now(),
                    revision=0,
                    expires_at=self.now() + request.idle_seconds,
                    config_path=None,
                    endpoints={},
                    resource_counts={},
                    bound_job_id=None,
                    bound_container_id=None,
                    fault_until=None,
                    error=None,
                    secrets={"rabbitmq_password": secrets.token_urlsafe(32)},
                )
                self.store.put(lease)
            if lease["profile"] != request.profile or any(
                lease["scope"][key] != getattr(request.scope, key)
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
                raise FixtureError("Fixture ownership mismatch")
            if lease["state"] in TERMINAL or lease["state"] in {"releasing", "cleanup_failed"}:
                return receipt(lease)
            if self.now() >= lease["expires_at"]:
                self._release(lease, "expired")
                return receipt(lease)
            # Dispatch may advance while the task and persistent agent stay the same.
            lease["scope"] = request.scope.model_dump()
            if lease["state"] == "resetting":
                # Only the durable reset operation may finish a partial reset;
                # cached connection records can refer to databases already gone.
                self.store.put(lease)
                return receipt(lease)
            job = self.job(lease["scope"])
            if not job:
                self._unbind(lease)
                if (
                    lease["state"] not in {"provisioning", "failed"}
                    or self.now() >= request.scope.dispatch_expires_at
                ):
                    self.store.put(lease)
                    return receipt(lease)
            if job and lease.get("bound_job_id") != job.job_id:
                self._unbind(lease)
            # A durable intent precedes every create, including SQL and Docker.
            if job:
                lease["expires_at"] = self.now() + request.idle_seconds
            self.store.put(lease)
            if lease.get("fault_until"):
                if self.now() < lease["fault_until"]:
                    # An intentional stop/pause is not a provisioning failure.
                    # Keep the existing binding; a replacement job waits for
                    # recovery instead of probing a deliberately stopped port.
                    return receipt(lease)
                self.docker.recover(lease)
                lease["fault_until"] = None
                if lease.get("last_control"):
                    lease["last_control"].update(status="recovered", observed_at=self.now())
                self.store.put(lease)
            phase = "SQL databases"
            try:
                connections = {}
                if "sqlserver" in request.profile:
                    for alias in ("source", "target"):
                        if alias not in lease["secrets"]:
                            record = self.sql.provision_database(
                                lease["lease_id"], WorkerMssqlDatabaseRequest(logical_name=alias)
                            )
                            lease["secrets"][alias] = record.model_dump(mode="json")
                            self.store.put(lease)
                        connections[alias] = lease["secrets"][alias]
                        lease["endpoints"][alias] = dict(
                            host=connections[alias]["server"],
                            port=connections[alias]["port"],
                            database=connections[alias]["database_name"],
                        )
                    lease["resource_counts"]["databases"] = 2
                if "rabbitmq" in request.profile:
                    phase = "RabbitMQ broker"
                    if not lease.get("fault_until"):
                        if not lease.get("broker_starting_at"):
                            lease["broker_starting_at"] = self.now()
                            self.store.put(lease)
                        lease["broker_ip"] = self.docker.ensure_broker(lease)
                        lease.pop("broker_starting_at", None)
                    if not lease.get("broker_ip"):
                        raise FixtureError("Broker endpoint is unavailable")
                    connections["rabbitmq"] = dict(
                        host=lease["broker_ip"],
                        port=5672,
                        username="fixture",
                        password=lease["secrets"]["rabbitmq_password"],
                        vhost=lease["lease_id"],
                    )
                    lease["endpoints"]["rabbitmq"] = dict(
                        host=lease["broker_ip"], port=5672, vhost=lease["lease_id"]
                    )
                    lease["resource_counts"]["brokers"] = 1
                    lease["resource_counts"]["volumes"] = 1
                    lease["resource_counts"]["networks"] = 1
                # Persist target identity before projecting secrets or attaching a
                # network, so a crash between the effects remains cleanable.
                if not job:
                    lease.update(
                        state="awaiting_job",
                        error=None,
                        config_path=None,
                        bound_job_id=None,
                        bound_container_id=None,
                    )
                    self.store.put(lease)
                    return receipt(lease)
                lease["bound_job_id"] = job.job_id
                lease["bound_container_id"] = job.container_id
                self.store.put(lease)
                phase = "private job binding"
                self.docker.bind(lease, job, connections)
                lease["config_path"] = (
                    f"/tmp/evander-test-fixtures/{lease['lease_id']}/connection.json"
                )
                lease["state"] = "ready"
                lease["error"] = None
            except NotReady:
                overdue = self.now() - lease["broker_starting_at"] >= 120
                lease["state"] = "failed" if overdue else "provisioning"
                lease["error"] = (
                    "RabbitMQ startup readiness deadline exceeded; automatic retry is pending"
                    if overdue
                    else None
                )
                self.store.put(lease)
                return receipt(lease)
            except Exception as exc:
                lease["state"] = "failed"
                lease["error"] = (
                    f"Fixture provisioning failed during {phase}; automatic retry is pending"
                )
                lease["diagnostic"] = {"phase": phase, "exception_type": type(exc).__name__}
                self.store.put(lease)
                return receipt(lease)
            self.store.put(lease)
            return receipt(lease)

    def _unbind(self, lease):
        self.docker.unbind(lease)
        lease.update(bound_job_id=None, bound_container_id=None, config_path=None)
        if lease["state"] == "ready":
            lease["state"] = "idle"

    def idle(self, lease_id):
        with self.store.locked():
            lease = self.store.get(lease_id)
            if lease["state"] not in TERMINAL:
                self._unbind(lease)
                self.store.put(lease)
            return receipt(lease)

    def _release(self, lease, final="released"):
        if lease["state"] in TERMINAL:
            return
        lease["state"] = "releasing"
        self.store.put(lease)
        failures = []
        try:
            self._unbind(lease)
        except Exception:
            failures.append("private job binding")
        if "rabbitmq" in lease["profile"]:
            try:
                self.docker.remove_broker(lease)
                for resource in ("brokers", "volumes", "networks"):
                    lease["resource_counts"].pop(resource, None)
                lease["endpoints"].pop("rabbitmq", None)
                lease.pop("broker_ip", None)
            except Exception:
                failures.append("RabbitMQ resources")
        if "sqlserver" in lease["profile"]:
            try:
                self.sql.drop_session(lease["lease_id"])
                for alias in ("source", "target"):
                    lease["secrets"].pop(alias, None)
                    lease["endpoints"].pop(alias, None)
                lease["resource_counts"].pop("databases", None)
            except Exception:
                failures.append("SQL databases")
        if not failures:
            lease.update(
                state=final,
                secrets={},
                error=None,
                resource_counts={},
                endpoints={},
                fault_until=None,
            )
        else:
            lease.update(
                state="cleanup_failed",
                error="Fixture cleanup failed for "
                + ", ".join(failures)
                + "; automatic retry is pending",
            )
        self.store.put(lease)

    def release(self, lease_id):
        with self.store.locked():
            try:
                lease = self.store.get(lease_id)
            except FixtureError:
                return dict(lease_id=lease_id, state="released")
            self._release(lease)
            return receipt(lease)

    def reset(self, lease_id, operation_id):
        with self.store.locked():
            lease = self.store.get(lease_id)
            if lease.get("last_reset_id") == operation_id:
                return receipt(lease)
            if lease["state"] not in {"ready", "resetting"} or not self.job(lease["scope"]):
                raise FixtureError("Reset requires an active owning job and a ready lease")
            lease.update(state="resetting", fault_until=None)
            self.store.put(lease)
            self._unbind(lease)
            if "rabbitmq" in lease["profile"]:
                self.docker.remove_broker(lease)
                lease.pop("broker_ip", None)
                lease.pop("broker_starting_at", None)
                lease["secrets"]["rabbitmq_password"] = secrets.token_urlsafe(32)
            if "sqlserver" in lease["profile"]:
                self.sql.drop_session(lease["lease_id"])
            lease.update(
                state="provisioning", endpoints={}, resource_counts={}, last_reset_id=operation_id
            )
            for alias in ("source", "target"):
                lease["secrets"].pop(alias, None)
            self.store.put(lease)
            return receipt(lease)

    def control(self, lease_id, control, operation_id):
        with self.store.locked():
            lease = self.store.get(lease_id)
            if lease.get("last_control_id") == operation_id:
                return receipt(lease)
            if (
                lease["state"] != "ready"
                or "rabbitmq" not in lease["profile"]
                or not self.job(lease["scope"])
            ):
                raise FixtureError("Broker control requires an active owning job and ready broker")
            # Save the recovery deadline before introducing a fault. Tick resumes
            # even if the HTTP process dies after docker stop/pause succeeds.
            pending = lease.get("pending_control")
            if pending and pending["id"] == operation_id:
                deadline = pending["deadline"]
            else:
                deadline = (
                    self.now() + control.duration_seconds
                    if control.action in {"stop", "pause"}
                    else None
                )
            lease["fault_until"] = deadline
            lease["pending_control"] = dict(id=operation_id, deadline=deadline)
            self.store.put(lease)
            if control.action in {"start", "resume"} or (
                deadline is not None and self.now() >= deadline
            ):
                self.docker.recover(lease)
            else:
                self.docker.recover(lease)
                self.docker.control(lease, control.action)
            lease["last_control_id"] = operation_id
            lease["pending_control"] = None
            lease["last_control"] = dict(
                operation_id=operation_id,
                action=control.action,
                status="recovered_after_unknown_result"
                if deadline is not None and self.now() >= deadline
                else "applied",
                observed_at=self.now(),
            )
            self.store.put(lease)
            self.store.audit(lease["scope"]["session_id"], control.action, lease_id)
            return receipt(lease)

    def get(self, lease_id):
        return receipt(self.store.get(lease_id))

    def tick(self):
        """Independent worker timer: outage expiry, revoked bindings, fault recovery."""
        with self.store.locked():
            for lease in self.store.list():
                if lease["state"] in TERMINAL:
                    continue
                try:
                    if (
                        lease["state"] in {"releasing", "cleanup_failed"}
                        or self.now() >= lease["expires_at"]
                    ):
                        self._release(lease, "expired")
                        continue
                    if lease.get("fault_until") and self.now() >= lease["fault_until"]:
                        self.docker.recover(lease)
                        lease["fault_until"] = None
                        if lease.get("last_control"):
                            lease["last_control"].update(status="recovered", observed_at=self.now())
                    job = self.job(lease["scope"])
                    if not job or job.job_id != lease.get("bound_job_id"):
                        self._unbind(lease)
                    elif lease.get("broker_ip"):
                        # Restore routes after Docker/firewall restarts.
                        inspected = self.docker.current_job(job)
                        network = inspected["NetworkSettings"]["Networks"].get(lease["lease_id"])
                        self.docker.firewall(lease, network["IPAddress"] if network else None)
                    self.store.put(lease)
                except Exception:
                    lease["error"] = "Worker reconciliation failed; automatic retry is pending"
                    self.store.put(lease)
