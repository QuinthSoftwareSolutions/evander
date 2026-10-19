"""Strict, bounded contracts shared by hosted browsers and local Chrome hosts."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ADDON_ID = "browser-sessions-addon"
PROTOCOL_VERSION = 1
TERMINAL = frozenset({"closed", "expired"})
Mode = Literal["hosted_demo", "hosted_development", "user_hosted"]
State = Literal[
    "starting",
    "ready",
    "paused",
    "disconnected",
    "closing",
    "closed",
    "expired",
    "failed",
    "cleanup_failed",
]
IDENTIFIER = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"


class BrowserError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise BrowserError("Invalid application address") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise BrowserError("Application addresses must be HTTP(S) without credentials")
    host = parsed.hostname.encode("idna").decode().lower()
    host = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    return f"{parsed.scheme}://{host}"


def identifier(value: str) -> str:
    if not re.fullmatch(IDENTIFIER, value):
        raise BrowserError("Invalid identifier")
    return value


def relative_path(value: str) -> str:
    from pathlib import PurePosixPath

    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise BrowserError("Expected a repository-relative path")
    return str(path)


class WorkerConnection(Contract):
    base_url: str
    token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parent_worker_url: str
    relay_url: str | None = None

    @field_validator("relay_url")
    @classmethod
    def endpoint_url(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        origin(value.replace("wss://", "https://", 1).replace("ws://", "http://", 1))
        if parsed.path != "/_browser/endpoint" or parsed.query or parsed.fragment:
            raise ValueError("Expected the hosted browser endpoint URL")
        if parsed.scheme in {"http", "ws"} and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Public browser endpoints require TLS")
        return value

    @field_validator("base_url", "parent_worker_url")
    @classmethod
    def url(cls, value):
        origin(value)
        if urlsplit(value).query or urlsplit(value).fragment:
            raise ValueError("Worker URLs cannot contain queries or fragments")
        return value.rstrip("/")


class LaunchProfile(Contract):
    image: str = Field(min_length=1, max_length=256)
    browser_image: str = Field(default="evander-browser-controller:latest", min_length=1)
    cpus: float = Field(default=2, gt=0, le=16)
    memory_mib: int = Field(default=4096, ge=512, le=65536)
    browser_memory_mib: int = Field(default=1536, ge=512, le=8192)
    public_environment: dict[str, str] = Field(default_factory=dict)
    readonly_mounts: dict[str, str] = Field(default_factory=dict)
    # Host-owned, named destinations; never supplied by an agent launch manifest.
    egress_allowlist: list[str] = Field(default_factory=list, max_length=64)
    database_enabled: bool = False

    @field_validator("image", "browser_image")
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


class Settings(Contract):
    enabled: bool = False
    public_base_url: str = ""
    relay_url: str = "http://127.0.0.1:11890"
    relay_token_env: str = "EVANDER_BROWSER_RELAY_TOKEN"
    guest_enabled: bool = True
    lifetime_seconds: int = Field(default=14400, ge=300, le=86400)
    idle_seconds: int = Field(default=1800, ge=60, le=14400)
    invitation_seconds: int = Field(default=600, ge=60, le=1800)
    retention_days: int = Field(default=30, ge=1, le=365)
    max_hosted_per_worker: int = Field(default=1, ge=1, le=32)
    workers: dict[str, WorkerConnection] = Field(default_factory=dict)
    profiles: dict[str, LaunchProfile] = Field(default_factory=dict)

    @field_validator("public_base_url", "relay_url")
    @classmethod
    def base_url(cls, value):
        if value:
            origin(value)
            if (
                cls.__name__ == "Settings"
                and urlsplit(value).scheme == "http"
                and urlsplit(value).hostname not in {"127.0.0.1", "localhost", "::1"}
                and value != "http://127.0.0.1:11890"
            ):
                raise ValueError("Browser service URLs require HTTPS outside loopback")
            if urlsplit(value).query or urlsplit(value).fragment:
                raise ValueError("Base URLs cannot contain query parameters or fragments")
        return value.rstrip("/")

    @field_validator("relay_token_env")
    @classmethod
    def env(cls, value):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("Invalid credential reference")
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


class Session(Contract):
    id: str = Field(pattern=IDENTIFIER)
    directory: str
    agent_session_id: str
    native_thread_id: str | None = None
    support_agent_session_id: str | None = None
    mode: Mode
    title: str = Field(default="Shared browser", max_length=160)
    worker_id: str | None = None
    workspace_ref: str | None = None
    launch_manifest_path: str | None = None
    profile_id: str | None = None
    state: State = "starting"
    revision: int = 0
    control_generation: int = 0
    controller: str | None = None
    host_principal: str | None = None
    allowed_origins: list[str] = Field(default_factory=list, max_length=16)
    current_url: str | None = None
    created_at: float
    expires_at: float
    last_activity_at: float
    idle_seconds: int = 1800
    source: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("allowed_origins")
    @classmethod
    def origins(cls, values):
        return list(dict.fromkeys(origin(value) for value in values))

    @field_validator("launch_manifest_path")
    @classmethod
    def manifest_path(cls, value):
        return relative_path(value) if value else value


class CreateRequest(Contract):
    mode: Mode
    request_id: str = Field(pattern=IDENTIFIER)
    title: str = Field(default="Shared browser", max_length=160)
    profile_id: str | None = None
    workspace_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    launch_manifest_path: str | None = None

    @model_validator(mode="after")
    def hosted_workspace(self):
        if self.mode != "user_hosted" and not all(
            (self.profile_id, self.workspace_ref, self.launch_manifest_path)
        ):
            raise ValueError("Hosted sessions require a profile, workspace and launch manifest")
        if self.launch_manifest_path:
            relative_path(self.launch_manifest_path)
        return self


class BrowserCommand(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    id: str = Field(pattern=IDENTIFIER)
    generation: int = Field(ge=0)
    action: Literal[
        "observe",
        "navigate",
        "click",
        "hover",
        "fill",
        "type",
        "key",
        "scroll",
        "dialog",
        "highlight",
        "reload",
    ]
    observation_id: str | None = Field(default=None, max_length=128)
    ref: str | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=8192)
    text: str | None = Field(default=None, max_length=16384)
    x: float | None = Field(default=None, ge=0, le=4096)
    y: float | None = Field(default=None, ge=0, le=4096)
    delta_x: float = Field(default=0, ge=-20000, le=20000)
    delta_y: float = Field(default=0, ge=-20000, le=20000)
    accept: bool = False

    def audit(self):
        return {"command_id": self.id, "action": self.action, "ref": self.ref}

    @property
    def observing(self):
        return self.action in {"observe", "highlight"}


class MessageRequest(Contract):
    request_id: str = Field(pattern=IDENTIFIER)
    body: str = Field(min_length=1, max_length=16000)
    annotation: dict[str, str | float] | None = None


class Frame(Contract):
    kind: Literal["frame"] = "frame"
    image: str = Field(max_length=3000000)
    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)
    page_epoch: str = Field(max_length=128)
    url: str = Field(max_length=8192)
