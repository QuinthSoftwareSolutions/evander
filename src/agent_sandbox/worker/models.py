from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    workspace: Path
    agent_home: Path
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
        paths = cls(
            session_id=session_id,
            root=root,
            workspace=root / "workspace",
            agent_home=root / "agent_home",
            artifacts=root / "artifacts",
            logs=root / "logs",
            metadata_path=root / "metadata.json",
        )
        paths.ensure()
        return paths

    def ensure(self) -> None:
        for path in (self.workspace, self.agent_home, self.artifacts, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self.metadata_path.write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "created_at": now_utc().isoformat(),
                        "workspace": str(self.workspace),
                        "agent_home": str(self.agent_home),
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
    workspace_path: str
    agent_home_path: str
    artifacts_path: str
    logs_path: str
    metadata_path: str
    workspace_container_path: str = "/workspace"
    agent_home_container_path: str = "/home/agent"
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerRepoCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_source: str
    clone_branch: str | None = None

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


class WorkerRepoCloneRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    target_dir: str = "/workspace"
    guest_repo_name: str
    remote_url: str
    cloned: bool


class WorkerFileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_path: str
    content: str
    mode: str | None = None
    overwrite: bool = True

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
    workspace_path: str
    agent_home_path: str
    artifacts_path: str
    logs_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
