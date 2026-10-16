from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ResourceError(ValueError):
    """Safe public diagnostic; never include provider exception text."""


class Conflict(ResourceError):
    pass


class Uncertain(ResourceError):
    def __init__(self, message, *, progress=None):
        super().__init__(message)
        self.progress = progress


class NotReady(ResourceError):
    """Accepted work is still starting and may be reconciled without an escalation."""


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Identifier = str


class Owner(Contract):
    provider: str = "session"
    key: str
    directory: str
    session_id: str = ""
    reference: dict = Field(default_factory=dict)
    job_tags: dict[str, str] = Field(default_factory=dict)
    valid_until: float | None = None

    @field_validator("directory")
    @classmethod
    def directory_path(cls, value):
        from evander.models import DirectoryPath

        return DirectoryPath(segments=value).as_posix()

    def identity(self):
        return self.provider, self.key, self.directory, self.session_id


class Credential(Contract):
    username: str | None = Field(default=None, min_length=1, max_length=256)
    credential_ref: str | None = Field(default=None, pattern=r"^cred_[a-f0-9]{32}$")
    username_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]+$")
    password_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]+$")

    @model_validator(mode="after")
    def exclusive(self):
        if self.username and self.username_env:
            raise ValueError("Select a literal username or username environment variable")
        if bool(self.credential_ref) == bool(
            (self.username or self.username_env) and self.password_env
        ):
            raise ValueError("Select a credential reference or an environment pair")
        if self.credential_ref and (self.username or self.username_env or self.password_env):
            raise ValueError("Credential sources cannot be combined")
        return self


class Worker(Contract):
    worker_base_url: str
    service_url: str
    token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")

    @field_validator("worker_base_url", "service_url")
    @classmethod
    def origin(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Expected an origin without credentials or a path")
        return value.rstrip("/")


class Profile(Contract):
    connection_id: str | None = None
    worker_id: str | None = None
    idle_seconds: int = Field(default=7200, ge=300, le=86400)
    options: dict = Field(default_factory=dict)
    allowed_owner_providers: list[str] = Field(default_factory=lambda: ["session", "workflow"])
    allowed_owner_keys: list[str] = Field(default_factory=list)
    allowed_owner_key_prefixes: list[str] = Field(default_factory=list)


class Settings(Contract):
    allocations_enabled: bool = False
    connections: dict[str, dict] = Field(default_factory=dict)
    profiles: dict[str, Profile] = Field(default_factory=dict)
    workers: dict[str, Worker] = Field(default_factory=dict)
    max_per_worker: int = Field(default=2, ge=1, le=100)
    workflow_operations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        for profile in self.profiles.values():
            if profile.connection_id and profile.connection_id not in self.connections:
                raise ValueError("Profile connection is not registered")
            if profile.worker_id and profile.worker_id not in self.workers:
                raise ValueError("Profile worker is not registered")
        return self


TERMINAL = {"released", "expired"}


class Lease(Contract):
    lease_id: str = Field(pattern=r"^rs-[a-f0-9]{32}$")
    owner: Owner
    profile_id: str
    profile: Profile
    connection: dict | None = None
    worker: Worker | None = None
    state: str = "provisioning"
    revision: int = 0
    created_at: float
    expires_at: float
    resources: dict = Field(default_factory=dict)
    operation: dict | None = None
    bound_job_id: str | None = None
    config_path: str | None = None
    error: str | None = None
    fault_until: float | None = None
    notice_key: str | None = None
    pending_notice: dict | None = None
    job_binding: dict = Field(default_factory=dict)
    last_operation_id: str | None = None
    last_control: dict | None = None


def receipt(lease: Lease) -> dict:
    return lease.model_dump(
        include={
            "lease_id",
            "owner",
            "profile_id",
            "state",
            "revision",
            "created_at",
            "expires_at",
            "operation",
            "bound_job_id",
            "config_path",
            "error",
            "fault_until",
            "last_control",
        }
    )


class WorkerSettings(Contract):
    runtime_dir: str
    worker_id: str
    token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    worker_jobs_db: str = "/var/lib/agent-worker/runtime/worker-jobs.db"
    allocations_enabled: bool = False
    max_per_worker: int = Field(default=2, ge=1, le=100)
    connections: dict[str, dict] = Field(default_factory=dict)
    allowed_images: list[str] = Field(default_factory=list)


class Request(Contract):
    operation: str
    arguments: dict = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=128)
    owner_provider: str = "session"
    owner_reference: dict = Field(default_factory=dict)
