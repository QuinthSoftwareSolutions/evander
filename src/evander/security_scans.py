"""Provider-neutral contracts and durable identity for repository security scans."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.codex_auth import normalize_subscription_profile_id

SCAN_EFFECT_TYPE = "effect:security:scan-repo"
TERMINAL_SCAN_STATUSES = frozenset({"completed", "failed", "cancelled", "timed_out"})


class SecurityScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    repo_path: str
    provider: str = "codex-security"
    mode: Literal["standard", "deep"] = "standard"
    from_commit: str | None = None
    to_commit: str | None = None
    coverage_policy: Literal["provider", "all_tracked_files"] = "provider"
    managed_repo_id: str | None = None
    resume_from_scan_id: str | None = None
    resume_subscription_profile_id: str | None = None
    timeout_seconds: float = Field(default=3600, gt=0, le=345600, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def default_budget(cls, value):
        if isinstance(value, dict) and value.get("timeout_seconds") is None:
            value = {
                **value,
                "timeout_seconds": 21600
                if value.get("coverage_policy") == "all_tracked_files"
                else 3600,
            }
        return value

    @field_validator("session_id", "provider", mode="before")
    @classmethod
    def required_text(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()

    @field_validator("repo_path")
    @classmethod
    def contained_repo_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or "\x00" in value:
            raise ValueError("repo_path must be an absolute contained session path")
        if PurePosixPath("/home/agent") not in path.parents:
            raise ValueError("repo_path must be inside /home/agent")
        return str(path)

    @field_validator("from_commit", "to_commit", mode="before")
    @classmethod
    def commit_hash(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
            raise ValueError("commit must be a hexadecimal Git commit hash (7-64 characters)")
        return value.lower()

    @field_validator("resume_subscription_profile_id", mode="before")
    @classmethod
    def resume_subscription(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("resume_subscription_profile_id must be a subscription profile slug")
        return normalize_subscription_profile_id(value, allow_none=False)

    @model_validator(mode="after")
    def compatible_mode(self) -> SecurityScanRequest:
        if self.mode == "deep" and (self.from_commit or self.to_commit):
            raise ValueError("deep scans require the whole repository; omit commit bounds")
        if self.coverage_policy == "all_tracked_files":
            if self.mode != "standard" or self.from_commit or self.to_commit:
                raise ValueError("tracked coverage requires a Standard full snapshot")
            if not self.managed_repo_id or not self.managed_repo_id.strip():
                raise ValueError("tracked coverage requires managed_repo_id")
            if self.timeout_seconds > 21600:
                raise ValueError("tracked coverage has a maximum six-hour run budget")
        if self.resume_from_scan_id is not None:
            if self.coverage_policy != "all_tracked_files":
                raise ValueError("resume requires all_tracked_files coverage")
            if not re.fullmatch(r"[a-f0-9]{32}", self.resume_from_scan_id):
                raise ValueError("Invalid source scan identity")
        if self.resume_subscription_profile_id is not None and self.resume_from_scan_id is None:
            raise ValueError("resume_subscription_profile_id requires resume_from_scan_id")
        return self


class SecurityScanHandle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SecurityScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["running", "completed", "failed", "cancelled", "timed_out"]
    provider_scan_id: str | None = None
    from_commit: str | None = None
    to_commit: str | None = None
    coverage: Literal["complete", "partial", "unknown"] = "unknown"
    findings: list[dict[str, Any]] = Field(default_factory=list)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    coverage_progress: dict[str, Any] = Field(default_factory=dict)
    cleanup_confirmed: bool = True

    @property
    def success(self) -> bool:
        return self.status == "completed" and self.coverage == "complete"


class SecurityScanProvider(Protocol):
    """Start must be idempotent for scan_id, including after a lost response."""

    def start(
        self, *, scan_id: str, request: SecurityScanRequest, runtime: Any
    ) -> SecurityScanHandle: ...

    def poll(self, *, handle: SecurityScanHandle, runtime: Any) -> SecurityScanResult: ...

    def cancel(
        self,
        *,
        scan_id: str,
        request: SecurityScanRequest,
        handle: SecurityScanHandle | None,
        runtime: Any,
    ) -> None:
        """Stop by stable identity even when a launch acknowledgment was lost."""
        ...


class SecurityScanRetryableError(RuntimeError):
    """A transport failure with an uncertain launch outcome; retry the same identity."""


def security_scan_providers(addon_catalog: Any) -> dict[str, SecurityScanProvider]:
    providers: dict[str, SecurityScanProvider] = {}
    for addon in addon_catalog.registrations:
        for name, factory in addon.security_scan_providers.items():
            if name in providers:
                raise ValueError(f"Duplicate security scan provider: {name}")
            provider = factory() if isinstance(factory, type) else factory
            if not all(
                callable(getattr(provider, method, None)) for method in ("start", "poll", "cancel")
            ):
                raise TypeError(
                    f"Security scan provider {name!r} must implement start, poll and cancel"
                )
            providers[name] = provider
    return providers


class SecurityScanRecord(BaseModel):
    scan_id: str
    directory_path: str
    execution_id: str
    owner_idempotency_key: str = ""
    request: SecurityScanRequest
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    handle: SecurityScanHandle | None = None
    result: SecurityScanResult | None = None

    def outputs(self) -> dict[str, Any]:
        result = self.result
        return {
            "scan_id": self.scan_id,
            "directory_path": self.directory_path,
            "session_id": self.request.session_id,
            "provider": self.request.provider,
            "repo_path": self.request.repo_path,
            "mode": self.request.mode,
            "coverage_policy": self.request.coverage_policy,
            "managed_repo_id": self.request.managed_repo_id,
            "resume_from_scan_id": self.request.resume_from_scan_id,
            "success": result.success if result else False,
            "status": result.status if result else "running",
            **(result.model_dump(mode="json", exclude={"status"}) if result else {}),
        }


class SecurityScanStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.path = Path(runtime_dir) / "security-scans.sqlite3"

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS scans (scan_id TEXT PRIMARY KEY, "
            "directory_path TEXT NOT NULL, terminal INTEGER NOT NULL, payload TEXT NOT NULL)"
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def identity(directory_path: str, idempotency_key: str) -> str:
        return hashlib.sha256(f"{directory_path}\0{idempotency_key}".encode()).hexdigest()[:32]

    def get(self, scan_id: str, directory_path: str) -> SecurityScanRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM scans WHERE scan_id=? AND directory_path=?",
                (scan_id, directory_path),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown security scan in this directory")
        return SecurityScanRecord.model_validate_json(row[0])

    def create(self, record: SecurityScanRecord) -> SecurityScanRecord:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO scans VALUES (?, ?, 0, ?)",
                (record.scan_id, record.directory_path, record.model_dump_json()),
            )
        existing = self.get(record.scan_id, record.directory_path)
        if existing.request != record.request or existing.execution_id != record.execution_id:
            raise ValueError("Security scan idempotency key was reused with different inputs")
        return existing

    def save(self, record: SecurityScanRecord) -> None:
        terminal = bool(record.result and record.result.status in TERMINAL_SCAN_STATUSES)
        with self.connect() as connection:
            connection.execute(
                "UPDATE scans SET terminal=?, payload=? WHERE scan_id=? AND directory_path=?",
                (int(terminal), record.model_dump_json(), record.scan_id, record.directory_path),
            )

    def active(self) -> list[SecurityScanRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM scans WHERE terminal=0 OR json_extract(payload,'$.result.cleanup_confirmed')=0"
            ).fetchall()
        return [SecurityScanRecord.model_validate_json(row[0]) for row in rows]
