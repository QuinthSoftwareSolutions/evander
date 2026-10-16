"""Bounded RabbitMQ Management HTTP API operations and vhost ownership."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import quote, urlsplit
from typing import Literal

import httpx
from pydantic import Field, field_validator

from evander.resources.models import Contract, Credential, ResourceError, NotReady, Uncertain


class Connection(Contract):
    management_url: str
    host: str
    port: int = Field(default=5671, ge=1, le=65535)
    tls: bool = True
    verify: bool | str = True
    credentials: dict[str, Credential] = Field(default_factory=dict)
    operations: list[str] = Field(default_factory=list)
    allowed_vhosts: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=15, ge=1, le=60)
    max_bytes: int = Field(default=1048576, ge=1024, le=10485760)

    @field_validator("management_url")
    @classmethod
    def origin(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Management URL must be an origin without credentials")
        return value.rstrip("/")


class Options(Contract):
    mode: Literal["isolated", "shared"] = "isolated"
    image: str | None = Field(default=None, pattern=r"^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$")
    max_queues: int = Field(default=100, ge=1, le=10000)
    max_connections: int = Field(default=16, ge=1, le=1000)
    max_queue_bytes: int = Field(default=16777216, ge=1024, le=1073741824)

    @field_validator("mode")
    @classmethod
    def mode_value(cls, value):
        if value not in {"isolated", "shared"}:
            raise ValueError("Select isolated or shared RabbitMQ")
        return value


class PublishMessage(Contract):
    exchange: str = Field(default="amq.default", max_length=255)
    routing_key: str = Field(default="", max_length=255)
    payload: str = Field(default="", max_length=65536)
    payload_encoding: Literal["string", "base64"] = "string"
    properties: dict = Field(default_factory=dict)


class Call(Contract):
    kind: Literal["health", "vhosts", "queues", "exchanges", "bindings", "users", "permissions"] = (
        "health"
    )
    action: Literal["put", "delete", "bind", "unbind"] = "put"
    vhost: str | None = None
    name: str | None = Field(default=None, max_length=255)
    definition: dict = Field(default_factory=dict)
    routing_key: str = ""
    payload: str = Field(default="", max_length=65536)
    payload_encoding: Literal["string", "base64"] = "string"
    count: int = Field(default=1, ge=1, le=100)
    requeue: bool = True
    properties: dict = Field(default_factory=dict)
    credential_purpose: str | None = None
    messages: list[PublishMessage] = Field(default_factory=list, max_length=100)


def segment(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ResourceError("Invalid RabbitMQ resource identifier")
    return quote(value, safe="")


def password_hash(password):
    salt = secrets.token_bytes(4)
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


class Adapter:
    argument_model = Call
    prefix = "rabbitmq"
    read_operations = {"inspect"}
    operations = {"inspect", "manage", "publish", "get_messages"}
    lease_operations = {"inspect", "publish", "get_messages"}

    def __init__(self, transport=None, docker=None):
        self.transport = transport
        self.docker = docker

    def validate_connection(self, value):
        config = Connection.model_validate(value)
        if not set(config.operations) <= self.operations:
            raise ResourceError("Unsupported RabbitMQ operation grant")
        return config

    def readiness(self, connection_id, raw, directory, credentials):
        purpose = "read" if "read" in raw.get("credentials", {}) else "admin"
        values = credentials.resolve(connection_id, raw, purpose, directory)
        self.http(
            {**raw, "timeout_seconds": min(raw.get("timeout_seconds", 15), 5)},
            values,
            "GET",
            "health/checks/alarms",
        )

    def validate_profile(self, profile):
        options = Options.model_validate(profile.options)
        if options.mode == "isolated" and (not profile.worker_id or not options.image):
            raise ResourceError("Isolated brokers require a worker and a pinned image")
        if options.mode == "shared" and not profile.connection_id:
            raise ResourceError("Shared broker profiles require a registered connection")
        return options

    def validate_control(self, profile, arguments):
        options = self.validate_profile(profile)
        if options.mode != "isolated":
            raise ResourceError("Fault controls require an isolated disposable broker")
        if set(arguments) - {"action", "duration_seconds"} or arguments.get("action") not in {
            "stop",
            "pause",
            "start",
            "resume",
        }:
            raise ResourceError("Unsupported broker control")
        duration = arguments.get("duration_seconds", 60)
        if type(duration) is not int or not 1 <= duration <= 120:
            raise ResourceError("Broker faults must last 1 to 120 seconds")

    def validate_call(self, operation, raw):
        if operation not in self.operations:
            raise ResourceError("Unsupported RabbitMQ operation")
        args = Call.model_validate(raw)
        if operation == "inspect" and args.kind not in {
            "health",
            "vhosts",
            "queues",
            "exchanges",
            "bindings",
        }:
            raise ResourceError("Unsupported RabbitMQ inspection")
        if operation == "manage" and (
            args.kind not in {"vhosts", "users", "permissions", "queues", "exchanges", "bindings"}
            or args.action not in {"put", "delete", "bind", "unbind"}
        ):
            raise ResourceError("Unsupported RabbitMQ management action")
        if operation == "publish" and args.payload_encoding not in {"string", "base64"}:
            raise ResourceError("Unsupported payload encoding")
        if args.messages:
            if (
                operation != "publish"
                or args.payload
                or args.name
                or args.routing_key
                or args.properties
            ):
                raise ResourceError("Select a single publish message or a messages batch")
            if len(json.dumps([item.model_dump() for item in args.messages]).encode()) > 65536:
                raise ResourceError("Publish batch exceeds the 64 KiB input limit")
            for item in args.messages:
                segment(item.exchange)
        return args

    def validate_request(self, raw, operation, arguments):
        args = self.validate_call(operation, arguments)
        config = self.validate_connection(raw)
        if args.vhost is not None and args.vhost not in config.allowed_vhosts:
            raise ResourceError("Virtual host is outside this connection's grants")
        if operation in {"publish", "get_messages"} and args.vhost is None:
            raise ResourceError("A permitted virtual host is required")
        if (
            operation == "manage"
            and args.kind in {"users", "permissions"}
            and args.name not in config.allowed_users
        ):
            raise ResourceError("Broker user is outside this connection's grants")
        return args

    def http(self, raw, values, method, path, body=None, *, missing=False):
        config = Connection.model_validate(raw)
        with httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds, connect=5),
            verify=config.verify,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            with client.stream(
                method,
                config.management_url + "/api/" + path,
                auth=(values["username"], values["password"]),
                json=body,
                headers={"Accept-Encoding": "identity"},
            ) as response:
                if missing and response.status_code == 404:
                    return None
                response.raise_for_status()
                data = bytearray()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ResourceError("Compressed provider responses are not accepted")
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(data) + len(chunk) > config.max_bytes:
                        raise ResourceError("RabbitMQ response exceeds the configured limit")
                    data.extend(chunk)
                return json.loads(data) if data else {}

    def call(
        self, connection_id, raw, operation, arguments, directory, credentials, store, operation_id
    ):
        args = self.validate_request(raw, operation, arguments)
        config = self.validate_connection(raw)
        purpose = (
            "read" if operation == "inspect" else "admin" if operation == "manage" else "write"
        )
        values = credentials.resolve(connection_id, raw, purpose, directory)
        if args.vhost is not None and args.vhost not in config.allowed_vhosts:
            raise ResourceError("Virtual host is outside this connection's grants")
        vhost = segment(args.vhost) if args.vhost is not None else None
        if operation == "inspect":
            if args.kind == "health":
                return self.http(raw, values, "GET", "health/checks/alarms")
            if args.kind == "vhosts":
                return {
                    "vhosts": [
                        self.http(raw, values, "GET", "vhosts/" + segment(v))
                        for v in config.allowed_vhosts
                    ]
                }
            if vhost is None:
                raise ResourceError("A permitted virtual host is required")
            return {
                "items": self.http(raw, values, "GET", f"{args.kind}/{vhost}?page=1&page_size=100")
            }
        if operation == "publish":
            if vhost is None:
                raise ResourceError("A permitted virtual host is required")
            if args.messages:
                acknowledged = []
                # The server's publish resource accepts POST, including RabbitMQ 4.3.
                # Do not probe or retry a mutation with a different HTTP method.
                for index, message in enumerate(args.messages):
                    try:
                        acknowledged.append(
                            self.http(
                                raw,
                                values,
                                "POST",
                                f"exchanges/{vhost}/{segment(message.exchange)}/publish",
                                message.model_dump(exclude={"exchange"}),
                            )
                        )
                    except Exception:
                        raise Uncertain(
                            "Batch publish stopped after an unknown outcome; do not replay it",
                            progress={
                                "acknowledged": index,
                                "uncertain": 1,
                                "unattempted": len(args.messages) - index - 1,
                            },
                        ) from None
                return {"results": acknowledged, "count": len(acknowledged)}
            exchange = quote(args.name or "amq.default", safe="")
            return self.http(
                raw,
                values,
                "POST",
                f"exchanges/{vhost}/{exchange}/publish",
                {
                    "routing_key": args.routing_key,
                    "payload": args.payload,
                    "payload_encoding": args.payload_encoding,
                    "properties": args.properties,
                },
            )
        if operation == "get_messages":
            if vhost is None or not args.name:
                raise ResourceError("A permitted virtual host and queue are required")
            return {
                "messages": self.http(
                    raw,
                    values,
                    "POST",
                    f"queues/{vhost}/{segment(args.name)}/get",
                    {
                        "count": args.count,
                        "ackmode": "ack_requeue_true" if args.requeue else "ack_requeue_false",
                        "encoding": "auto",
                        "truncate": min(config.max_bytes // args.count, 65536),
                    },
                )
            }
        if not args.name or args.kind != "users" and vhost is None:
            raise ResourceError("Management requires a resource name and permitted virtual host")
        if args.kind == "bindings":
            definition = args.definition
            if set(definition) - {
                "destination",
                "destination_type",
                "routing_key",
                "arguments",
                "properties_key",
            }:
                raise ResourceError("Unsupported binding definition")
            target_kind = {"queue": "q", "exchange": "e"}.get(
                definition.get("destination_type", "queue")
            )
            if target_kind is None:
                raise ResourceError("Invalid binding destination type")
            path = f"bindings/{vhost}/e/{segment(args.name)}/{target_kind}/{segment(definition['destination'])}"
            if args.action == "unbind":
                return self.http(
                    raw, values, "DELETE", path + "/" + segment(definition["properties_key"])
                )
            if args.action != "bind":
                raise ResourceError("Bindings require bind/unbind")
            return self.http(
                raw,
                values,
                "POST",
                path,
                {
                    "routing_key": definition.get("routing_key", ""),
                    "arguments": definition.get("arguments", {}),
                },
            )
        path = f"{args.kind}/" + (
            segment(args.name)
            if args.kind in {"vhosts", "users"}
            else vhost + "/" + segment(args.name)
        )
        if args.kind == "vhosts" and args.name != args.vhost:
            raise ResourceError("Vhost name must match its explicit connection grant")
        if args.kind in {"users", "permissions"} and args.name not in config.allowed_users:
            raise ResourceError("Broker user is outside this connection's grants")
        body = args.definition
        if args.kind == "users" and args.action == "put":
            if not args.credential_purpose or args.credential_purpose == "admin":
                raise ResourceError(
                    "User creation requires a configured application credential purpose"
                )
            application = credentials.resolve(
                connection_id, raw, args.credential_purpose, directory
            )
            if application["username"] != args.name:
                raise ResourceError(
                    "Configured application identity does not match the requested user"
                )
            if set(body) - {"tags"} or any(
                t not in {"management", "monitoring"} for t in body.get("tags", [])
            ):
                raise ResourceError(
                    "Application users cannot receive broker administrator privileges"
                )
            body = {
                "password_hash": password_hash(application["password"]),
                "hashing_algorithm": "rabbit_password_hashing_sha256",
                "tags": body.get("tags", []),
            }
        if args.action not in {"put", "delete"}:
            raise ResourceError("Resource requires put/delete")
        return self.http(
            raw,
            values,
            "PUT" if args.action == "put" else "DELETE",
            path,
            body if args.action == "put" else None,
        )

    def _target(self, lease, credentials):
        if self.validate_profile(lease.profile).mode == "isolated":
            return lease.resources["connection"], {
                "username": lease.resources["admin_user"],
                "password": lease.resources["admin_password"],
            }
        return lease.connection, credentials.resolve(
            lease.profile.connection_id, lease.connection, "admin", lease.owner.directory
        )

    def provision(self, lease, save, credentials):
        options = self.validate_profile(lease.profile)
        if not lease.resources:
            password = secrets.token_urlsafe(32)
            lease.resources = {
                "vhost": "evr_" + lease.lease_id[3:],
                "user": "evr_" + lease.lease_id[3:],
                "password": password,
                "password_hash": password_hash(password),
                "marker": secrets.token_hex(32),
            }
            save()
        if options.mode == "isolated":
            if self.docker is None:
                raise ResourceError("Isolated brokers require the RabbitMQ worker service")
            self.docker.ensure(lease, save)
        raw, values = self._target(lease, credentials)
        r = lease.resources
        if options.mode == "isolated":
            try:
                self.http(raw, values, "GET", "health/checks/alarms")
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                starting = r.get("recovering") or not r.get("initialized")
                retryable = not isinstance(
                    exc, httpx.HTTPStatusError
                ) or exc.response.status_code in {404, 503}
                if starting and retryable and time.time() - r.get("starting_at", 0) < 120:
                    raise NotReady("Broker management listener is starting") from None
                raise
        path = "vhosts/" + segment(r["vhost"])
        current = self.http(raw, values, "GET", path, missing=True)
        if current is not None and self.description(current) != r["marker"]:
            raise ResourceError("Virtual host ownership does not match the durable intent")
        if current is None:
            self.http(
                raw, values, "PUT", path, {"description": r["marker"], "tags": ["evander-managed"]}
            )
        user_path = "users/" + segment(r["user"])
        user = self.http(raw, values, "GET", user_path, missing=True)
        if user is not None and user.get("password_hash") != r["password_hash"]:
            raise ResourceError("Broker user identity does not match the durable intent")
        if user is None:
            self.http(
                raw,
                values,
                "PUT",
                user_path,
                {
                    "password_hash": r["password_hash"],
                    "hashing_algorithm": "rabbit_password_hashing_sha256",
                    "tags": ["management"],
                },
            )
        self.http(
            raw,
            values,
            "PUT",
            f"permissions/{segment(r['vhost'])}/{segment(r['user'])}",
            {"configure": ".*", "write": ".*", "read": ".*"},
        )
        for name, value in (
            ("max-queues", options.max_queues),
            ("max-connections", options.max_connections),
        ):
            self.http(
                raw, values, "PUT", f"vhost-limits/{segment(r['vhost'])}/{name}", {"value": value}
            )
        self.http(
            raw,
            values,
            "PUT",
            f"policies/{segment(r['vhost'])}/resource-bounds",
            {
                "pattern": ".*",
                "apply-to": "queues",
                "priority": 100,
                "definition": {
                    "max-length-bytes": options.max_queue_bytes,
                    "overflow": "reject-publish",
                },
            },
        )
        r["initialized"] = True
        r.pop("recovering", None)
        lease.state = "ready"
        save()

    @staticmethod
    def description(value):
        return value.get("description") or value.get("metadata", {}).get("description")

    def private_connections(self, lease):
        raw = lease.resources.get("connection") or lease.connection
        return {
            "broker": {
                "host": raw["host"],
                "port": raw.get("port", 5671),
                "tls": raw.get("tls", True),
                "username": lease.resources["user"],
                "password": lease.resources["password"],
                "vhost": lease.resources["vhost"],
            }
        }

    def release(self, lease, save, credentials):
        if not lease.resources:
            return
        options = self.validate_profile(lease.profile)
        if options.mode == "isolated":
            self.docker.remove(lease)
            return
        raw, values = self._target(lease, credentials)
        r = lease.resources
        vhost = "vhosts/" + segment(r["vhost"])
        current = self.http(raw, values, "GET", vhost, missing=True)
        if current is not None:
            if self.description(current) != r["marker"]:
                raise ResourceError("Foreign virtual host preserved")
            self.http(raw, values, "DELETE", vhost)
        user_path = "users/" + segment(r["user"])
        current = self.http(raw, values, "GET", user_path, missing=True)
        if current is not None:
            if current.get("password_hash") != r["password_hash"]:
                raise ResourceError("Foreign broker user preserved")
            self.http(raw, values, "DELETE", user_path)
        if (
            self.http(raw, values, "GET", vhost, missing=True) is not None
            or self.http(raw, values, "GET", user_path, missing=True) is not None
        ):
            raise ResourceError("RabbitMQ cleanup has not completed")
        save()

    def reset(self, lease, save, credentials):
        self.release(lease, save, credentials)
        lease.resources.pop("initialized", None)
        save()

    def verify_absent(self, lease, connections, credentials):
        if self.validate_profile(lease.profile).mode == "isolated":
            if any(
                self.docker.inspect(lease.lease_id, kind)
                for kind in ("container", "network", "volume")
            ):
                raise ResourceError(
                    "Unjournaled broker resources exist; explicit reconciliation is required"
                )
            return
        raw = connections[lease.profile.connection_id]
        values = credentials.resolve(
            lease.profile.connection_id, raw, "admin", lease.owner.directory
        )
        name = segment("evr_" + lease.lease_id[3:])
        if any(
            self.http(raw, values, "GET", kind + "/" + name, missing=True) is not None
            for kind in ("vhosts", "users")
        ):
            raise ResourceError(
                "Unjournaled broker resources exist; explicit reconciliation is required"
            )

    def lease_call(self, lease, operation, raw_arguments):
        if operation not in self.lease_operations:
            raise ResourceError("Unsupported disposable broker operation")
        arguments = dict(raw_arguments)
        name = lease.resources["vhost"]
        if arguments.get("vhost") not in {None, name}:
            raise ResourceError("Virtual host is outside the lease")
        arguments["vhost"] = name
        connection = dict(lease.resources.get("connection") or lease.connection)
        connection["allowed_vhosts"] = [name]
        resources = lease.resources

        class LeaseCredentials:
            def resolve(self, *args):
                return {"username": resources["user"], "password": resources["password"]}

        return self.call(
            lease.profile.connection_id,
            connection,
            operation,
            arguments,
            lease.owner.directory,
            LeaseCredentials(),
            None,
            None,
        )

    def control(self, lease, action):
        self.docker.control(lease, action)

    def recover(self, lease):
        self.docker.recover(lease)
