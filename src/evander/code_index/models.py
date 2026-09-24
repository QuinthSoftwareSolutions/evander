from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TOOLCHAIN = {
    "format": 1,
    "scip-dotnet": "0.2.15",
    "scip-dotnet-revision": "47884461a79839fb74c99e6a0a7978cd7eb62476",
    "scip-typescript": "0.4.0",
    "typescript": "5.9.3",
    "dotnet-sdk": "10.0.111",
    "node": "24.20.0",
}
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
DEFAULT_INCLUDES = [
    "**/*.cs",
    "**/*.ts",
    "**/*.tsx",
    "**/*.html",
    "**/*.scss",
    "**/*.css",
    "**/*.md",
    "**/*.sql",
]
DEFAULT_EXCLUDES = [
    "**/packages/**",
    "**/*.Designer.cs",
    "**/*ModelSnapshot.cs",
    "**/*.g.cs",
    "**/*.generated.cs",
    "**/*.d.ts",
    "**/*.min.*",
]
BLOCKED_PARTS = {
    ".git",
    ".codex",
    ".agents",
    "node_modules",
    "bin",
    "obj",
    "dist",
    "vendor",
    ".angular",
}
BLOCKED_NAMES = re.compile(
    r"(^\.env($|\.)|^secrets?([.-]|$)|^appsettings([.-]|$)|\.(pem|key|pfx|p12|kdbx)$|^id_(rsa|ed25519)|credentials)",
    re.I,
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or any(p in {"", ".", ".."} for p in value.split("/"))
    ):
        raise ValueError("Source paths must be canonical repository-relative paths")
    return value


def commit_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        raise ValueError("An exact Git commit SHA is required")
    return value.lower()


def matches(path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, p) or (p.startswith("**/") and fnmatch.fnmatchcase(path, p[3:]))
        for p in patterns
    )


class IndexSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    worker_id: str | None = None
    image: str = "code-index"
    tracked_ref: str = "main"
    refresh_on_git_change: bool = True
    refresh_interval_seconds: int = Field(default=3600, ge=300, le=604800)
    worker_cpus: float = Field(default=1, ge=0.1, le=4)
    # Accepted for older profiles; Team preparation never submits compiler builds.
    index_task_commits: bool = Field(default=False, deprecated=True)
    includes: list[str] = Field(default_factory=lambda: list(DEFAULT_INCLUDES))
    excludes: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    dotnet_projects: list[str] = Field(default_factory=list)
    typescript_projects: list[str] = Field(default_factory=list)
    scip: bool = False
    semantic: bool = True
    npm_restore: Literal["locked", "install"] = "locked"
    timeout_seconds: int = Field(default=1800, ge=60, le=14400)
    retention_days: int = Field(default=30, ge=1, le=365)
    max_file_bytes: int = Field(default=512 * 1024, ge=1024, le=2 * 1024 * 1024)

    @field_validator("dotnet_projects", "typescript_projects")
    @classmethod
    def validate_projects(cls, values: list[str]) -> list[str]:
        return [source_path(p) for p in values]

    @field_validator("includes", "excludes")
    @classmethod
    def validate_patterns(cls, values: list[str]) -> list[str]:
        if len(values) > 100:
            raise ValueError("At most 100 file patterns are allowed")
        return [source_path(p) for p in values]

    def includes_file(self, path: str) -> bool:
        source_path(path)
        return (
            not (
                set(path.lower().split("/")) & BLOCKED_PARTS
                or BLOCKED_NAMES.search(PurePosixPath(path).name)
            )
            and matches(path, self.includes)
            and not matches(path, self.excludes)
        )

    def index_policy(self) -> dict:
        """Only source selection and analysis settings invalidate a published snapshot."""
        return self.model_dump(
            include={
                "includes",
                "excludes",
                "dotnet_projects",
                "typescript_projects",
                "npm_restore",
                "scip",
                "semantic",
                "max_file_bytes",
                "image",
            }
        )


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    text: str
    language: str = "text"
    generated: bool = False
    _path = field_validator("path")(source_path)


class Occurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    symbol: str = Field(min_length=1, max_length=4096)
    # One-based lines; zero-based columns in the document's reported encoding.
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=0)
    encoding: Literal["utf-8", "utf-16", "utf-32"] = "utf-16"
    definition: bool = False
    generated: bool = False
    _path = field_validator("path")(source_path)


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    target: str
    kind: Literal["reference", "implementation", "definition"]


class IndexArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal[1] = 1
    commit_sha: str
    toolchain: dict[str, str | int] = Field(default_factory=lambda: dict(TOOLCHAIN))
    documents: list[SourceDocument]
    occurrences: list[Occurrence] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    projects: list[dict[str, str | int]] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    scip_files: dict[str, str] = Field(default_factory=dict)
    _sha = field_validator("commit_sha")(commit_id)
