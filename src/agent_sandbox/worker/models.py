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
                raise ValueError(f"raw environment injection is not supported: {', '.join(present)}")
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
            raise ValueError("session_id must contain only letters, numbers, dots, underscores, or dashes")
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
