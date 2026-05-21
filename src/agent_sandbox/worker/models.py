from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_sandbox.capacity_health import (
    DiskUsageTelemetry,
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
)

DEFAULT_WORKER_ROOT = Path("/var/lib/agent-worker")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def now_utc() -> datetime:
    return datetime.now(UTC)


class WorkerResourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory: str | None = None
    cpus: float | None = Field(default=None, gt=0)
    pids_limit: int | None = Field(default=None, gt=0)

    @field_validator("memory", mode="before")
    @classmethod
    def normalize_memory(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class WorkerJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    job_id: str | None = None
    image: str
    command: list[str]
    repo_source: str | None = None
    clone_branch: str | None = None
    resource_limits: WorkerResourceLimits = Field(default_factory=WorkerResourceLimits)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "job_id", mode="before")
    @classmethod
    def normalize_safe_id(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            raise ValueError("must not be empty")
        if not SAFE_ID_PATTERN.fullmatch(text):
            raise ValueError("must contain only letters, numbers, dots, underscores, or dashes")
        return text

    @field_validator("image", mode="before")
    @classmethod
    def normalize_image(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("image is required")
        return text

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("command must contain at least one argument")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError("command entries must be non-empty strings")
        return value

    @field_validator("repo_source", "clone_branch", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="before")
    @classmethod
    def reject_raw_environment_injection(cls, data: object) -> object:
        if isinstance(data, dict):
            forbidden = {"env", "environment", "environment_variables"}
            present = sorted(forbidden.intersection(data))
            if present:
                raise ValueError(
                    f"raw environment injection is not supported: {', '.join(present)}"
                )
        return data


class WorkerSessionPaths(BaseModel):
    session_id: str
    root: Path
    agent_home: Path
    repo_root: Path
    artifacts: Path
    logs: Path
    metadata_path: Path

    @classmethod
    def resolve(cls, worker_root: Path, session_id: str) -> "WorkerSessionPaths":
        if not SAFE_ID_PATTERN.fullmatch(session_id):
            raise ValueError(
                "session_id must contain only letters, numbers, dots, underscores, or dashes"
            )
        root = worker_root / "sessions" / session_id
        agent_home = root / "agent_home"
        paths = cls(
            session_id=session_id,
            root=root,
            agent_home=agent_home,
            repo_root=agent_home / "repos",
            artifacts=root / "artifacts",
            logs=root / "logs",
            metadata_path=root / "metadata.json",
        )
        paths.ensure()
        return paths

    def ensure(self) -> None:
        for path in (self.agent_home, self.repo_root, self.artifacts, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self.metadata_path.write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "created_at": now_utc().isoformat(),
                        "agent_home": str(self.agent_home),
                        "repo_root": str(self.repo_root),
                        "artifacts": str(self.artifacts),
                        "logs": str(self.logs),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )


class WorkerSessionPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_safe_id(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("session_id is required")
        if not SAFE_ID_PATTERN.fullmatch(text):
            raise ValueError("must contain only letters, numbers, dots, underscores, or dashes")
        return text


class WorkerSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    root_path: str
    agent_home_path: str
    repo_root_path: str
    artifacts_path: str
    logs_path: str
    metadata_path: str
    agent_home_container_path: str = "/home/agent"
    repo_root_container_path: str = "/home/agent/repos"
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerRepoCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_source: str
    clone_branch: str | None = None
    repo_name: str | None = None

    @field_validator("repo_source", mode="before")
    @classmethod
    def normalize_repo_source(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("repo_source is required")
        return text

    @field_validator("clone_branch", mode="before")
    @classmethod
    def normalize_clone_branch(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("repo_name", mode="before")
    @classmethod
    def normalize_repo_name(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text in {".", ".."} or not SAFE_ID_PATTERN.fullmatch(text):
            raise ValueError(
                "repo_name must contain only letters, numbers, dots, underscores, or dashes"
            )
        return text


class WorkerRepoCloneRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    target_dir: str
    guest_repo_name: str
    remote_url: str
    cloned: bool


class WorkerFileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_path: str
    content: str | None = None
    content_base64: str | None = None
    mode: str | None = None
    overwrite: bool = True

    @model_validator(mode="after")
    def validate_content_source(self) -> "WorkerFileWrite":
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("exactly one of content or content_base64 is required")
        return self

    @field_validator("destination_path", mode="before")
    @classmethod
    def normalize_destination_path(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("destination_path is required")
        return text

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class WorkerFileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[WorkerFileWrite]

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: list[WorkerFileWrite]) -> list[WorkerFileWrite]:
        if not value:
            raise ValueError("files must contain at least one entry")
        return value


class WorkerFileWriteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    copied_count: int
    copied_paths: list[str]
    roots: dict[str, str] = Field(default_factory=dict)


class WorkerMssqlDatabaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_name: str

    @field_validator("logical_name", mode="before")
    @classmethod
    def normalize_logical_name(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("logical_name is required")
        if text in {".", ".."} or not SAFE_ID_PATTERN.fullmatch(text):
            raise ValueError(
                "logical_name must contain only letters, numbers, dots, underscores, or dashes"
            )
        if len(text) > 64:
            raise ValueError("logical_name must be 64 characters or fewer")
        return text


class WorkerMssqlDatabaseState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_name: str
    database_name: str
    created_at: datetime = Field(default_factory=now_utc)


class WorkerMssqlSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    login_name: str
    password: str
    created_at: datetime = Field(default_factory=now_utc)
    databases: dict[str, WorkerMssqlDatabaseState] = Field(default_factory=dict)


class WorkerMssqlDatabaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    logical_name: str
    database_name: str
    server: str
    port: int
    username: str
    password: str
    connection_string: str


class WorkerMssqlDatabaseListRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    databases: list[WorkerMssqlDatabaseState]


class WorkerMssqlDatabaseDeleteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    logical_name: str
    database_name: str | None = None
    deleted: bool


class WorkerMssqlSessionDeleteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    deleted_databases: list[str] = Field(default_factory=list)
    deleted_login: bool = False


class WorkerJobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    session_id: str
    image: str
    command: list[str]
    container_name: str
    container_id: str | None = None
    status: str = "created"
    created_at: datetime = Field(default_factory=now_utc)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    agent_home_path: str
    repo_root_path: str
    artifacts_path: str
    logs_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_workspace_path(cls, data: object) -> object:
        if not isinstance(data, dict) or "workspace_path" not in data:
            return data
        migrated = dict(data)
        workspace_path = migrated.pop("workspace_path")
        if "repo_root_path" not in migrated:
            migrated["repo_root_path"] = workspace_path
        return migrated


class WorkerDiskTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_root: DiskUsageTelemetry


class WorkerSessionStorageTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: DirectorySizeTelemetry
    session_count: int = 0
    largest_sessions: list[SessionStorageSample] = Field(default_factory=list)
    oldest_sessions: list[SessionStorageSample] = Field(default_factory=list)


class WorkerJobTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TelemetryStatus = TelemetryStatus.AVAILABLE
    health: HealthState = HealthState.OK
    total_jobs: int = 0
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    running_job_count: int = 0
    queued_job_count: int = 0
    queued_job_status: TelemetryStatus = TelemetryStatus.UNSUPPORTED
    queued_job_message: str = "worker daemon starts jobs synchronously; no queue is persisted"
    failed_start_count: int = 0
    cleanup_failure_status: TelemetryStatus = TelemetryStatus.UNSUPPORTED
    cleanup_failure_message: str = "cleanup failures are not currently persisted"
    error_count: int = 0
    errors: list[TelemetryError] = Field(default_factory=list)


class WorkerTelemetryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=now_utc)
    disk: WorkerDiskTelemetry
    session_storage: WorkerSessionStorageTelemetry
    jobs: WorkerJobTelemetry
