from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from evander.capacity_health import (
    DiskUsageTelemetry,
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
)
from evander.codex_auth import CodexTurnSpec
from evander.managed_sections import normalize_managed_section_id

DEFAULT_WORKER_ROOT = Path("/var/lib/agent-worker")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_ID_ALLOWED_DESCRIPTION = "letters, numbers, dots, underscores, or dashes"


def validate_worker_component_id(
    value: object,
    *,
    field_name: str,
    allow_none: bool = False,
) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if not SAFE_ID_PATTERN.fullmatch(text):
        raise ValueError(f"{field_name} must contain only {SAFE_ID_ALLOWED_DESCRIPTION}")
    if text in {".", ".."}:
        raise ValueError(f"{field_name} must not be a dot or parent segment")
    return text


def ensure_resolved_path_within(
    *,
    base: Path,
    candidate: Path,
    description: str,
    allow_base: bool = False,
) -> None:
    resolved_base = Path(base).resolve(strict=False)
    resolved_candidate = Path(candidate).resolve(strict=False)
    if not allow_base and resolved_candidate == resolved_base:
        raise ValueError(f"{description} must stay below {resolved_base}")
    try:
        resolved_candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError(f"{description} must stay below {resolved_base}") from exc


def now_utc() -> datetime:
    return datetime.now(UTC)


class WorkerFailureCause(str, Enum):
    PIDS_LIMIT_EXHAUSTED = "pids_limit_exhausted"
    MEMORY_LIMIT_EXHAUSTED = "memory_limit_exhausted"


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
    codex_turn: CodexTurnSpec | None = None
    repo_source: str | None = None
    clone_branch: str | None = None
    working_directory: str | None = None
    resource_limits: WorkerResourceLimits = Field(default_factory=WorkerResourceLimits)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: object) -> str:
        return str(validate_worker_component_id(value, field_name="session_id"))

    @field_validator("job_id", mode="before")
    @classmethod
    def normalize_job_id(cls, value: object) -> str | None:
        return validate_worker_component_id(value, field_name="job_id", allow_none=True)

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

    @field_validator("repo_source", "clone_branch", "working_directory", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("working_directory must be an absolute contained path")
        allowed = PurePosixPath("/home/agent")
        if path != allowed and allowed not in path.parents:
            raise ValueError("working_directory must be under /home/agent")
        return str(path)

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
    sessions_root: Path | None = None
    root: Path
    agent_home: Path
    repo_root: Path
    artifacts: Path
    logs: Path
    metadata_path: Path

    @classmethod
    def resolve(cls, worker_root: Path, session_id: str) -> "WorkerSessionPaths":
        normalized_session_id, sessions_root, root = cls.resolve_session_root(
            worker_root,
            session_id,
        )
        agent_home = root / "agent_home"
        paths = cls(
            session_id=normalized_session_id,
            sessions_root=sessions_root,
            root=root,
            agent_home=agent_home,
            repo_root=agent_home / "repos",
            artifacts=root / "artifacts",
            logs=root / "logs",
            metadata_path=root / "metadata.json",
        )
        paths.ensure()
        return paths

    @classmethod
    def resolve_session_root(cls, worker_root: Path, session_id: str) -> tuple[str, Path, Path]:
        normalized_session_id = str(
            validate_worker_component_id(session_id, field_name="session_id")
        )
        sessions_root = Path(worker_root) / "sessions"
        root = sessions_root / normalized_session_id
        ensure_resolved_path_within(
            base=sessions_root,
            candidate=root,
            description="session root",
        )
        return normalized_session_id, sessions_root, root

    def assert_contained(self) -> None:
        if self.sessions_root is None:
            raise ValueError("session paths are missing the sessions root")
        ensure_resolved_path_within(
            base=self.sessions_root,
            candidate=self.root,
            description="session root",
        )
        for description, candidate in (
            ("agent home path", self.agent_home),
            ("repo root path", self.repo_root),
            ("artifacts path", self.artifacts),
            ("logs path", self.logs),
            ("metadata path", self.metadata_path),
        ):
            ensure_resolved_path_within(
                base=self.root,
                candidate=candidate,
                description=description,
            )

    def ensure(self) -> None:
        self.assert_contained()
        for path in (self.agent_home, self.repo_root, self.artifacts, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        self.assert_contained()
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
        return str(validate_worker_component_id(value, field_name="session_id"))


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
    reconcile_existing_remote: bool = False

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
        return validate_worker_component_id(text, field_name="repo_name")


class WorkerRepoCloneRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    target_dir: str
    guest_repo_name: str
    remote_url: str
    cloned: bool
    commit_sha: str | None = None


class WorkerTaskDependencyGitSource(BaseModel):
    """One server-authorized Git contribution required by a task dependency."""

    model_config = ConfigDict(extra="forbid")

    dependency_task_id: str
    deliverable_id: str
    branch: str
    commit_sha: str

    @field_validator("dependency_task_id", "deliverable_id", mode="before")
    @classmethod
    def normalize_identity(cls, value: object, info: ValidationInfo) -> str:
        text = str(value or "").strip()
        if not text or "\n" in text or "\r" in text:
            raise ValueError(f"{info.field_name} must be non-empty single-line text")
        return text

    @field_validator("branch", mode="before")
    @classmethod
    def normalize_branch(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text or "\n" in text or "\r" in text:
            raise ValueError("branch must be a non-empty single-line Git ref")
        return text

    @field_validator("commit_sha", mode="before")
    @classmethod
    def normalize_commit_sha(cls, value: object) -> str:
        text = str(value or "").strip().casefold()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", text) is None:
            raise ValueError("commit_sha must be a full Git object ID")
        return text


class WorkerTaskWorktreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_name: str
    worktree_name: str
    branch: str
    base_ref: str
    expected_base_commit_sha: str | None = None
    dependency_git_sources: list[WorkerTaskDependencyGitSource] = Field(default_factory=list)

    @field_validator("repo_name", "worktree_name", mode="before")
    @classmethod
    def normalize_component(cls, value: object, info: ValidationInfo) -> str:
        return str(validate_worker_component_id(value, field_name=info.field_name))

    @field_validator("branch", "base_ref", mode="before")
    @classmethod
    def normalize_ref(cls, value: object, info: ValidationInfo) -> str:
        text = str(value or "").strip()
        if not text or "\n" in text or "\r" in text:
            raise ValueError(f"{info.field_name} must be a non-empty single-line Git ref")
        return text

    @field_validator("expected_base_commit_sha", mode="before")
    @classmethod
    def normalize_expected_base_commit_sha(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip().casefold()
        if not text:
            return None
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", text) is None:
            raise ValueError("expected_base_commit_sha must be a full Git object ID")
        return text


class WorkerTaskWorktreeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    repo_name: str
    target_dir: str
    branch: str
    commit_sha: str
    base_commit_sha: str | None = None
    created: bool


class WorkerTaskWorktreeCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_name: str
    worktree_name: str
    branch: str
    expected_commit_sha: str
    expected_target_dir: str
    dry_run: bool = False

    @field_validator("repo_name", "worktree_name", mode="before")
    @classmethod
    def normalize_component(cls, value: object, info: ValidationInfo) -> str:
        return str(validate_worker_component_id(value, field_name=info.field_name))

    @field_validator("branch", mode="before")
    @classmethod
    def normalize_branch(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text or "\n" in text or "\r" in text:
            raise ValueError("branch must be a non-empty single-line Git ref")
        return text

    @field_validator("expected_commit_sha", mode="before")
    @classmethod
    def normalize_expected_commit_sha(cls, value: object) -> str:
        text = str(value or "").strip().casefold()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", text):
            raise ValueError("expected_commit_sha must be a full Git object ID")
        return text

    @field_validator("expected_target_dir", mode="before")
    @classmethod
    def normalize_expected_target_dir(cls, value: object) -> str:
        text = str(value or "").strip()
        path = PurePosixPath(text)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("expected_target_dir must be an absolute contained path")
        return str(path)

    @model_validator(mode="after")
    def validate_expected_target_dir(self) -> "WorkerTaskWorktreeCleanupRequest":
        expected = f"/home/agent/worktrees/{self.repo_name}/{self.worktree_name}"
        if self.expected_target_dir != expected:
            raise ValueError(
                "expected_target_dir does not match the deterministic task worktree path"
            )
        return self


class WorkerTaskWorktreeCleanupRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    repo_name: str
    worktree_name: str
    target_dir: str
    branch: str
    expected_commit_sha: str
    actual_branch: str | None = None
    actual_commit_sha: str | None = None
    state: Literal["removed", "dry_run", "already_absent", "refused"]
    safe_to_remove: bool
    removed: bool
    pruned: bool
    dry_run: bool
    validation_count: int = Field(ge=0, le=3)
    failed_check: str | None = None
    message: str
    tracked_paths: list[str] = Field(default_factory=list)
    untracked_paths: list[str] = Field(default_factory=list)
    ignored_paths: list[str] = Field(default_factory=list)
    ignored_bytes: int = Field(default=0, ge=0)
    worktree_bytes: int = Field(default=0, ge=0)
    reclaimed_bytes: int = Field(default=0, ge=0)


class WorkerGitWriteKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git_host: str
    git_user: str
    git_ssh_alias: str
    key_name: str = "evander-git-write"
    alias_is_exact: bool = False

    @field_validator("git_host", "git_user", "git_ssh_alias", mode="before")
    @classmethod
    def normalize_required_string(cls, value: object, info: ValidationInfo) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} is required")
        if "\n" in text or "\r" in text:
            raise ValueError(f"{info.field_name} must not contain newlines")
        return text

    @field_validator("key_name", mode="before")
    @classmethod
    def normalize_key_name(cls, value: object) -> str:
        return str(validate_worker_component_id(value, field_name="key_name"))


class WorkerGitWriteKeyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    public_key: str
    public_key_body: str
    key_path: str
    config_path: str
    git_ssh_alias: str


class WorkerGitKeyDeleteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    key_name: str
    removed: bool


class WorkerFileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_path: str
    content: str | None = None
    content_base64: str | None = None
    mode: str | None = None
    overwrite: bool = True
    merge_strategy: str | None = None
    section_id: str | None = None
    remove_section_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_content_source(self) -> "WorkerFileWrite":
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("exactly one of content or content_base64 is required")
        if self.merge_strategy == "managed_section":
            if self.content is None:
                raise ValueError("managed_section merge requires UTF-8 text content")
            if self.section_id is None:
                raise ValueError("managed_section merge requires section_id")
        elif self.remove_section_ids:
            raise ValueError("remove_section_ids requires managed_section merge")
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

    @field_validator("merge_strategy", mode="before")
    @classmethod
    def normalize_merge_strategy(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if text != "managed_section":
            raise ValueError("merge_strategy must be one of: managed_section")
        return text

    @field_validator("section_id", mode="before")
    @classmethod
    def normalize_section_id(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return normalize_managed_section_id(text)

    @field_validator("remove_section_ids", mode="before")
    @classmethod
    def normalize_remove_section_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("remove_section_ids must be a list")
        return [normalize_managed_section_id(str(item or "")) for item in value]


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
        text = str(validate_worker_component_id(value, field_name="logical_name"))
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
    working_directory: str | None = None
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
    failure_cause: WorkerFailureCause | None = None
    cleanup_status: str = "unknown"
    cleanup_attempted_at: datetime | None = None
    cleanup_completed_at: datetime | None = None
    cleanup_error: str | None = None

    @field_validator("job_id", "session_id", mode="before")
    @classmethod
    def normalize_record_ids(cls, value: object, info: ValidationInfo) -> str:
        return str(validate_worker_component_id(value, field_name=str(info.field_name)))

    @field_validator("cleanup_status", mode="before")
    @classmethod
    def normalize_cleanup_status(cls, value: object) -> str:
        text = str(value or "").strip()
        return text or "unknown"

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
    cleanup_failure_count: int = 0
    error_count: int = 0
    errors: list[TelemetryError] = Field(default_factory=list)


class WorkerTelemetryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=now_utc)
    disk: WorkerDiskTelemetry
    session_storage: WorkerSessionStorageTelemetry
    jobs: WorkerJobTelemetry
