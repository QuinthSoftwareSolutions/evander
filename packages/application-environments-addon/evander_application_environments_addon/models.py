"""Application-only launch and lease contracts."""

import re
from typing import Literal
from pydantic import Field, field_validator
from evander.resources.models import Contract, Worker, Profile as ResourceProfile
from evander.resources.container_runtime import relative_path

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


class Profile(Contract):
    image: str = Field(min_length=1, max_length=256)
    cpus: float = Field(default=2, gt=0, le=16)
    memory_mib: int = Field(default=4096, ge=512, le=65536)
    public_environment: dict[str, str] = Field(default_factory=dict)
    readonly_mounts: dict[str, str] = Field(default_factory=dict)
    # Host-owned, named destinations; never supplied by an agent launch manifest.
    egress_allowlist: list[str] = Field(default_factory=list, max_length=64)
    database_profile: str | None = None

    @field_validator("image")
    @classmethod
    def image_ref(cls, value):
        if value.startswith("-") or any(char.isspace() for char in value):
            raise ValueError("Invalid approved image reference")
        return value

    @field_validator("public_environment")
    @classmethod
    def environment(cls, value):
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in value):
            raise ValueError("Invalid environment variable name")
        if set(value) & {"LD_PRELOAD", "LD_LIBRARY_PATH", "NODE_OPTIONS", "PYTHONPATH"}:
            raise ValueError("Loader overrides are unavailable")
        return value


class LaunchManifest(Contract):
    command: list[str] = Field(min_length=1, max_length=128)
    working_directory: str = "."
    entry_path: str = "/"
    port: int = Field(default=4200, ge=1024, le=65535)
    readiness_path: str = "/"
    additional_ports: list[int] = Field(default_factory=list, max_length=8)

    @field_validator("additional_ports")
    @classmethod
    def ports(cls, values):
        if any(type(port) is not int or not 1024 <= port <= 65535 for port in values):
            raise ValueError("Application ports must be between 1024 and 65535")
        return list(dict.fromkeys(values))

    startup_seconds: int = Field(default=600, ge=10, le=1800)
    database: str | None = Field(default=None, pattern=IDENTIFIER)

    @field_validator("working_directory")
    @classmethod
    def directory(cls, value):
        return relative_path(value)

    @field_validator("entry_path", "readiness_path")
    @classmethod
    def path(cls, value):
        if not value.startswith("/") or value.startswith("//") or "\\" in value or "\x00" in value:
            raise ValueError("Expected an application-relative URL path")
        return value

    @field_validator("command")
    @classmethod
    def argv(cls, value):
        if any(not item or "\x00" in item or len(item) > 8192 for item in value):
            raise ValueError("Invalid application command")
        return value


class WorkerConnection(Contract):
    base_url: str
    token_env: str
    parent_worker_url: str

    @field_validator("base_url", "parent_worker_url")
    @classmethod
    def validate_url(cls, value):
        from urllib.parse import urlsplit

        p = urlsplit(value)
        if (
            p.scheme not in {"http", "https"}
            or not p.hostname
            or p.username
            or p.password
            or p.path not in {"", "/"}
            or p.query
            or p.fragment
        ):
            raise ValueError("Expected a private worker origin")
        return value.rstrip("/")


class ResourceBinding(Contract):
    addon_id: Literal["sqlserver-addon"] = "sqlserver-addon"
    worker: Worker
    profile: ResourceProfile


class Settings(Contract):
    allocations_enabled: bool = False
    lifetime_seconds: int = Field(default=14400, ge=60, le=14400)
    max_per_worker: int = Field(default=1, ge=1, le=32)
    workers: dict[str, WorkerConnection] = Field(default_factory=dict)
    profiles: dict[str, Profile] = Field(default_factory=dict)


class CreateRequest(Contract):
    request_id: str = Field(pattern=IDENTIFIER)
    title: str = Field(default="Application environment", max_length=160)
    profile_id: str
    workspace_ref: str = Field(pattern=r"^[a-f0-9]{32}$")
    launch_manifest_path: str

    @field_validator("launch_manifest_path")
    @classmethod
    def manifest_path(cls, value):
        return relative_path(value)


class Environment(Contract):
    id: str = Field(pattern=IDENTIFIER)
    agent_session_id: str
    directory: str
    worker_id: str
    profile_id: str
    workspace_ref: str
    launch_manifest_path: str
    title: str = "Application environment"
    created_at: float
    expires_at: float
    state: str = "accepted"
    error: str | None = None
