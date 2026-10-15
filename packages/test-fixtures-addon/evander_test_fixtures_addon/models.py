from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

Profile = Literal["sqlserver", "rabbitmq", "sqlserver-rabbitmq"]
Action = Literal["start", "stop", "pause", "resume"]
TERMINAL = {"released", "expired"}


class FixtureError(ValueError):
    pass


class Conflict(FixtureError):
    pass


class NotReady(FixtureError):
    pass


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Contract):
    session_id: str
    agent_id: str
    team_id: str
    project_id: str
    task_id: str
    dispatch_id: str
    dispatch_expires_at: float = Field(ge=0)
    directory: str
    worker_id: str


class Worker(Contract):
    # The base URL must match the actual session assignment, never an agent argument.
    worker_base_url: str
    fixture_base_url: str
    token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")

    @field_validator("worker_base_url", "fixture_base_url")
    @classmethod
    def url(cls, value):
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
            raise ValueError("Expected a worker origin without credentials or path")
        return value.rstrip("/")


class Settings(Contract):
    allocations_enabled: bool = False
    allowed_team_ids: list[str] = Field(default_factory=list)
    workers: dict[str, Worker] = Field(default_factory=dict)
    idle_seconds: int = Field(default=7200, ge=300, le=7200)
    max_per_worker: int = Field(default=2, ge=1, le=2)


class Ensure(Contract):
    lease_id: str = Field(pattern=r"^fx-[a-f0-9]{32}$")
    scope: Scope
    profile: Profile
    idle_seconds: int = Field(default=7200, ge=300, le=7200)


class Control(Contract):
    action: Action
    duration_seconds: int = Field(default=60, ge=1, le=120)


class WorkerSettings(Contract):
    runtime_dir: str = "/var/lib/evander-test-fixtures"
    worker_jobs_db: str = "/var/lib/agent-worker/runtime/worker-jobs.db"
    worker_id: str
    token_env: str = "EVANDER_TEST_FIXTURES_TOKEN"
    allocations_enabled: bool = False
    max_per_worker: int = Field(default=2, ge=1, le=2)
    rabbitmq_image: str | None = Field(
        default=None, pattern=r"^[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}$"
    )
    sql_enabled: bool = False
    sql_admin_user: str = "sa"
    sql_password_env: str = "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD"
    sql_host: str = "127.0.0.1"
    sql_container_host: str = "172.17.0.1"
    sql_port: int = Field(default=1433, ge=1, le=65535)
    sqlcmd_path: str = "/opt/mssql-tools18/bin/sqlcmd"


def receipt(lease: dict) -> dict:
    """Single allowlist for every ordinary MCP/API/workflow response."""
    result = {
        key: lease.get(key)
        for key in (
            "lease_id",
            "scope",
            "profile",
            "state",
            "revision",
            "created_at",
            "expires_at",
            "config_path",
            "endpoints",
            "resource_counts",
            "error",
            "fault_until",
            "bound_job_id",
            "operation",
            "last_control",
        )
    }
    result["endpoints"] = {
        alias: {
            key: value
            for key, value in endpoint.items()
            if key in {"host", "port", "database", "vhost"}
        }
        for alias, endpoint in (lease.get("endpoints") or {}).items()
        if alias in {"source", "target", "rabbitmq"}
    }
    return result
