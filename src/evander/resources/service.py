"""Durable lifecycle shared by optional adapters; technology stays in the owning project."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import uuid

from .authority import Authorities, runtime_of
from .client import WorkerClient
from .credentials import Credentials
from .models import Conflict, Lease, Owner, ResourceError, Settings, TERMINAL, Uncertain, receipt
from .state import Store


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ResourceService:
    def __init__(
        self,
        context,
        adapter,
        *,
        authority=None,
        credentials=None,
        clients=WorkerClient,
        now=time.time,
    ):
        self.context = context
        self.runtime = runtime_of(context.service)
        self.adapter = adapter
        self.store = Store(Path(context.runtime_dir) / "addons" / context.addon_id)
        self.authority = authority or Authorities(
            self.runtime, adapter.prefix + "_acquire", context.runtime_dir
        )
        self.credentials = credentials or Credentials(context.runtime_dir, context.addon_id)
        self.clients = clients
        self.now = now

    def validate_settings(self, raw):
        settings = Settings.model_validate(raw)
        for key, value in settings.connections.items():
            self.adapter.validate_connection(value)
        for profile in settings.profiles.values():
            self.adapter.validate_profile(profile)
        return settings

    def readiness(self, directory, connection_id=None, profile_id=None):
        _, settings = self.store.settings(directory)
        profile = settings.profiles.get(profile_id) if profile_id else None
        if profile_id and profile is None:
            raise ResourceError("Unknown resource profile")
        if profile:
            connection_id = profile.connection_id
        worker_ids = {
            p.worker_id
            for p in settings.profiles.values()
            if connection_id and p.connection_id == connection_id and p.worker_id
        }
        worker_id = (
            profile.worker_id
            if profile
            else next(iter(worker_ids))
            if len(worker_ids) == 1
            else None
        )
        if len(worker_ids) > 1 and not profile:
            raise ResourceError("Select a profile to check this worker-local connection")
        if worker_id:
            client = self.clients(settings.workers[worker_id])
            health = client.health()
            if (
                health.get("protocol") != 1
                or health.get("addon_id") != self.context.addon_id
                or health.get("worker_id") != worker_id
            ):
                raise ResourceError("Worker service identity or protocol does not match")
            result = (
                client.readiness(connection_id, directory)
                if connection_id
                else {"status": "ready", "code": "worker_connected"}
            )
            return {
                **result,
                "allocations_enabled": settings.allocations_enabled
                and health.get("allocations_enabled", False),
            }
        connection = settings.connections.get(connection_id)
        if connection is None:
            raise ResourceError("Unknown registered connection")
        self.adapter.readiness(connection_id, connection, directory, self.credentials)
        return {"status": "ready", "code": "connected"}

    def owner(self, session_id, operation, provider="session", reference=None):
        return self.authority.resolve(
            session_id, provider, reference or {}, f"{self.adapter.prefix}_{operation}"
        )

    def discover(self, owner):
        _, settings = self.store.settings(owner.directory)
        return {
            "connections": [
                {"id": key, "operations": value.get("operations", [])}
                for key, value in settings.connections.items()
            ],
            "profiles": [
                {"id": key, "options": value.options, "idle_seconds": value.idle_seconds}
                for key, value in settings.profiles.items()
                if self.allowed(owner, value)
            ],
        }

    @staticmethod
    def allowed(owner, profile):
        return owner.provider in profile.allowed_owner_providers and (
            not (profile.allowed_owner_keys or profile.allowed_owner_key_prefixes)
            or owner.key in profile.allowed_owner_keys
            or any(
                owner.key.startswith(prefix + ":") for prefix in profile.allowed_owner_key_prefixes
            )
        )

    def connection(self, owner, connection_id, operation):
        _, settings = self.store.settings(owner.directory)
        connection = settings.connections.get(connection_id)
        if connection is None or operation not in connection.get("operations", []):
            raise ResourceError("Connection or operation is not permitted")
        if owner.provider == "workflow" and operation not in settings.workflow_operations:
            raise ResourceError("Workflow operation is not enabled")
        return connection

    def call(self, owner, connection_id, operation, arguments, request_id):
        connection = self.connection(owner, connection_id, operation)
        self.adapter.validate_request(connection, operation, arguments)
        if operation in self.adapter.read_operations:
            try:
                return self.adapter.call(
                    connection_id,
                    connection,
                    operation,
                    arguments,
                    owner.directory,
                    self.credentials,
                    self.store,
                    None,
                )
            except ResourceError:
                raise
            except Exception:
                raise ResourceError("Provider read failed") from None
        if not request_id or len(request_id) > 128:
            raise ResourceError("A request_id of at most 128 characters is required")
        key = fingerprint([owner.identity(), request_id])
        digest = fingerprint([connection_id, operation, arguments])
        with self.store.locked():
            # Recheck permissions inside the same lock used by configuration writes.
            connection = self.connection(owner, connection_id, operation)
            prior = self.store.operation(key)
            if prior:
                if prior[0] != digest:
                    raise Conflict("request_id is bound to another operation")
                return prior[1]
            record = {
                "id": key,
                "directory": owner.directory,
                "operation": operation,
                "connection_id": connection_id,
                "state": "uncertain",
                "revision": 1,
            }
            self.store.put_operation(key, digest, record)
            try:
                result = self.adapter.call(
                    connection_id,
                    connection,
                    operation,
                    arguments,
                    owner.directory,
                    self.credentials,
                    self.store,
                    key,
                )
            except Exception as exc:
                # Intent remains uncertain even if the process dies while committing the provider write.
                if isinstance(exc, Uncertain) and exc.progress is not None:
                    record["progress"] = exc.progress
                    self.store.put_operation(key, digest, record)
                self.store.audit(owner.key, operation + ":uncertain", key)
                return record
            record.update(state="completed", result=result, revision=2)
            self.store.put_operation(key, digest, record)
            self.store.audit(owner.key, operation, key)
            return record

    def worker(self, owner, profile, settings):
        if not profile.worker_id:
            return None
        worker = settings.workers[profile.worker_id]
        if not owner.session_id:
            raise ResourceError("Worker resources require a session with an assigned worker")
        from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend

        session = self.runtime.policy.get_session(owner.session_id)
        config = self.runtime.provider_config.resolve_for_session_manifest(session)
        if config.execution_backend != "container_worker_vm":
            raise ResourceError("Profile requires a container worker session")
        config = ContainerWorkerVmExecutionBackend(config=config).config_for_session(
            owner.session_id, directory_path=owner.directory
        )
        if str(config.container_worker_base_url).rstrip("/") != worker.worker_base_url:
            raise ResourceError("Profile worker does not match the actual session assignment")
        return worker

    def acquire(self, owner, profile_id, request_id):
        if not request_id or len(request_id) > 128:
            raise ResourceError("request_id must contain 1 to 128 characters")
        with self.store.locked():
            _, settings = self.store.settings(owner.directory)
            profile = settings.profiles.get(profile_id)
            if profile is None or not self.allowed(owner, profile):
                raise ResourceError("Profile is outside the owner's scope")
            worker = self.worker(owner, profile, settings)
            key = "lease:" + fingerprint([owner.identity(), request_id])
            digest = fingerprint(profile_id)
            prior = self.store.operation(key)
            if prior:
                if prior[0] != digest:
                    raise Conflict("request_id is bound to another profile")
                lease = self.store.get(prior[1]["lease_id"])
                if lease.state in TERMINAL:
                    return receipt(lease)
            else:
                active = [
                    item
                    for item in self.store.leases(active_only=True)
                    if item.state not in TERMINAL
                ]
                lease = next(
                    (
                        item
                        for item in active
                        if item.owner.identity() == owner.identity()
                        and item.profile_id == profile_id
                    ),
                    None,
                )
                if lease is None:
                    if not settings.allocations_enabled:
                        raise ResourceError("New resource allocations are disabled")
                    if (
                        sum(item.profile.worker_id == profile.worker_id for item in active)
                        >= settings.max_per_worker
                    ):
                        raise ResourceError("Resource lease quota is full")
                    connection = (
                        settings.connections.get(profile.connection_id)
                        if profile.connection_id
                        else None
                    )
                    lease = Lease(
                        lease_id="rs-" + uuid.uuid4().hex,
                        owner=owner,
                        profile_id=profile_id,
                        profile=profile,
                        connection=connection,
                        worker=worker,
                        created_at=self.now(),
                        expires_at=self.now() + profile.idle_seconds,
                    )
                    self.store.put(lease)
                self.store.put_operation(key, digest, {"lease_id": lease.lease_id})
            if (
                lease.profile != profile
                or lease.worker != worker
                or lease.connection != settings.connections.get(profile.connection_id)
            ):
                raise Conflict("Profile changed; release the previous lease before acquiring again")
            lease.owner = owner
            lease.expires_at = self.now() + lease.profile.idle_seconds
            self.store.put(lease)
            self.store.audit(owner.key, "acquire", lease.lease_id)
        return self.reconcile(lease.lease_id)

    def authorized(self, owner, lease_id):
        lease = self.store.get(lease_id)
        if owner.identity() != lease.owner.identity():
            raise ResourceError("Lease is outside the current resource owner scope")
        _, settings = self.store.settings(owner.directory)
        profile = settings.profiles.get(lease.profile_id)
        if profile is None or not self.allowed(owner, profile) or not self.authority.owns(owner):
            raise ResourceError("Lease authority has ended")
        if lease.worker:
            if self.worker(owner, lease.profile, settings) != lease.worker:
                raise ResourceError(
                    "Lease worker binding changed; await cleanup before reacquiring"
                )
        return lease

    def get(self, owner, lease_id):
        lease = self.authorized(owner, lease_id)
        value = receipt(lease)
        published_owner = lease.owner
        if lease.worker and lease.state not in TERMINAL:
            try:
                value = self.clients(lease.worker).call(lease_id, "get")
                value = receipt(Lease.model_validate({**lease.model_dump(), **value}))
                published_owner = Owner.model_validate(value["owner"])
                if value["lease_id"] != lease_id or published_owner.identity() != owner.identity():
                    raise ResourceError("Worker returned a different resource identity")
            except Exception:
                return {
                    **receipt(lease),
                    "state": "unknown",
                    "config_path": None,
                    "bound_job_id": None,
                    "error": "Worker status is unavailable; no readiness claim is made",
                }
        if self.now() >= value["expires_at"] and value["state"] not in TERMINAL:
            value.update(state="releasing", config_path=None, bound_job_id=None)
        if published_owner.job_tags != owner.job_tags or (
            published_owner.valid_until is not None and self.now() >= published_owner.valid_until
        ):
            value.update(
                config_path=None,
                bound_job_id=None,
                state="idle" if value["state"] == "ready" else value["state"],
            )
        return value

    def lease_call(self, owner, lease_id, operation, arguments, request_id):
        lease = self.authorized(owner, lease_id)
        if lease.state != "ready":
            raise ResourceError("Data access requires a ready lease")
        if operation not in self.adapter.lease_operations:
            raise ResourceError("Operation is not permitted on disposable resources")
        if lease.worker:
            return self.clients(lease.worker).call(
                lease_id,
                "call",
                {
                    "addon_id": self.context.addon_id,
                    "owner": owner.model_dump(),
                    "operation": operation,
                    "arguments": arguments,
                    "request_id": request_id,
                },
            )
        from .operations import perform

        def invoke():
            current = self.authorized(owner, lease_id)
            if current.state != "ready":
                raise ResourceError("Data access requires a ready lease")
            return self.adapter.lease_call(current, operation, arguments)

        result = perform(
            self.store,
            owner,
            lease_id,
            operation,
            arguments,
            request_id,
            invoke,
            read=operation in self.adapter.read_operations,
        )
        with self.store.locked():
            current = self.authorized(owner, lease_id)
            if current.state not in TERMINAL:
                current.owner = owner
                current.expires_at = self.now() + current.profile.idle_seconds
                self.store.put(current)
        return result

    def queue(self, owner, lease_id, action, arguments=None, request_id=None):
        with self.store.locked():
            lease = self.authorized(owner, lease_id)
            if request_id is not None and not request_id:
                raise ResourceError("Lifecycle request_id cannot be empty")
            request_id = request_id or (
                "release:" + lease_id if action == "release" else uuid.uuid4().hex
            )
            if len(request_id) > 128:
                raise ResourceError("Lifecycle request_id must contain at most 128 characters")
            key = "lifecycle:" + fingerprint([owner.identity(), request_id])
            digest = fingerprint([lease_id, action, arguments])
            prior = self.store.operation(key)
            if prior:
                if prior[0] != digest:
                    raise Conflict("request_id is bound to another lifecycle operation")
                return receipt(lease)
            if lease.state in TERMINAL:
                return receipt(lease)
            if lease.operation and action != "release":
                raise Conflict("A lifecycle operation is already pending")
            if action not in {"reset", "release", "control"}:
                raise ResourceError("Unsupported resource action")
            if action == "control":
                self.adapter.validate_control(lease.profile, arguments or {})
            if action in {"reset", "control"} and lease.state != "ready":
                raise ResourceError("Reset and broker control require a ready resource lease")
            lease.owner = owner
            lease.operation = {"kind": action, "id": uuid.uuid4().hex, "arguments": arguments or {}}
            lease.state = {"release": "releasing", "reset": "resetting", "control": "controlling"}[
                action
            ]
            self.store.put_with_operation(
                lease,
                key,
                digest,
                {
                    "lease_id": lease_id,
                    "directory": owner.directory,
                    "operation": action,
                    "state": "accepted",
                },
            )
        return self.reconcile(lease_id)

    def operator_release(self, directory, lease_id, revision, actor="operator"):
        with self.store.locked():
            lease = self.store.get(lease_id)
            if lease.owner.directory != directory:
                raise ResourceError("Lease is outside the directory")
            if lease.revision != revision:
                raise Conflict("Lease changed; refresh before releasing")
            if lease.state not in TERMINAL:
                lease.operation = {"kind": "release", "id": uuid.uuid4().hex, "arguments": {}}
                lease.state = "releasing"
                self.store.put(lease)
                self.store.audit(actor, "release", lease_id)
        return self.reconcile(lease_id)

    def reconcile(self, lease_id):
        with self.store.locked():
            lease = self.store.get(lease_id)
            if lease.state in TERMINAL:
                return receipt(lease)
            _, settings = self.store.settings(lease.owner.directory)
            profile = settings.profiles.get(lease.profile_id)
            owns = (
                self.authority.owns(lease.owner)
                and profile is not None
                and self.allowed(lease.owner, profile)
            )
            if owns and lease.worker:
                try:
                    owns = self.worker(lease.owner, lease.profile, settings) == lease.worker
                except Exception:
                    owns = False
            if owns and lease.owner.session_id:
                try:
                    current = self.owner(
                        lease.owner.session_id,
                        "acquire",
                        lease.owner.provider,
                        lease.owner.reference,
                    )
                    if current.identity() == lease.owner.identity():
                        lease.owner = current
                except Exception:
                    pass  # Idle scopes retain resources, without bindings or renewal.
            if not owns or (lease.worker is None and self.now() >= lease.expires_at):
                lease.operation = {
                    "kind": "release",
                    "id": "expiry:" + lease.lease_id,
                    "arguments": {},
                }
                lease.state = "releasing"
                self.store.put(lease)
            try:
                lease.error = None
                if lease.worker:
                    result = self.clients(lease.worker).call(
                        lease_id,
                        "ensure",
                        {"addon_id": self.context.addon_id, "lease": lease.model_dump()},
                    )
                    for field in (
                        "state",
                        "bound_job_id",
                        "config_path",
                        "error",
                        "fault_until",
                        "expires_at",
                        "last_control",
                    ):
                        if field in result:
                            setattr(lease, field, result[field])
                    if result.get("operation") is None:
                        lease.operation = None
                else:
                    self._local(lease)
            except Exception:
                lease.error = "Resource reconciliation failed; inspect provider readiness and retry"
                lease.state = (
                    "cleanup_failed"
                    if lease.operation and lease.operation["kind"] == "release"
                    else "failed"
                )
            self.store.put(lease)
            value = receipt(lease)
            notice = fingerprint([lease.state, lease.operation])
            if lease.notice_key != notice and lease.state in {
                "ready",
                "awaiting_job",
                "cleanup_failed",
            }:
                if not lease.pending_notice or lease.pending_notice["key"] != notice:
                    lease.pending_notice = {"id": uuid.uuid4().hex, "key": notice, "receipt": value}
                    self.store.put(lease)
                self.authority.notify(
                    lease.owner,
                    {**lease.pending_notice["receipt"], "notice_id": lease.pending_notice["id"]},
                )
                lease.notice_key = notice
                lease.pending_notice = None
                self.store.put(lease)
            return receipt(lease)

    def _local(self, lease):
        def save():
            return self.store.put(lease)

        if lease.operation:
            action = lease.operation["kind"]
            if action == "release":
                self.adapter.release(lease, save, self.credentials)
                lease.state = "released"
                lease.operation = None
                return
            if action == "reset":
                self.adapter.reset(lease, save, self.credentials)
            else:
                raise ResourceError("Broker faults require an isolated worker broker")
            lease.operation = None
            save()
        self.adapter.provision(lease, save, self.credentials)
        lease.state = "ready"
