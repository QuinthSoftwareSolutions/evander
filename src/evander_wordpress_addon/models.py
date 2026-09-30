"""Versioned, customer-neutral contracts for recoverable website changes."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.website_sessions import website_origin

ADDON_ID = "wordpress-addon"
PROTOCOL_VERSION = 1
ACTIVE = {"queued", "applying", "verifying", "restoring", "uncertain"}
UNRESOLVED = ACTIVE | {"conflict", "recovery_required"}
STATES = Literal[
    "prepared",
    "queued",
    "applying",
    "verifying",
    "published",
    "restoring",
    "restored",
    "conflict",
    "uncertain",
    "failed",
    "recovery_required",
    "resolved",
]


class WordPressError(RuntimeError):
    pass


class Conflict(WordPressError):
    pass


class UnknownOutcome(WordPressError):
    """A remote mutation may have happened; its status must be reconciled."""


def digest(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(Contract):
    path: str = "/"
    selector: str | None = None
    links: list[str] = Field(default_factory=list, max_length=40)
    # Form checks inspect controls/validation without submitting production data.
    form_selector: str | None = None

    @field_validator("path", "links")
    @classmethod
    def paths(cls, value):
        for path in value if isinstance(value, list) else [value]:
            if (
                not path.startswith("/")
                or path.startswith("//")
                or "\\" in path
                or any(ord(c) < 32 for c in path)
                or urlsplit(path).fragment
            ):
                raise ValueError("check URLs must be absolute paths on the configured site")
        return value


class Pipeline(Contract):
    kind: Literal["pipeline"] = "pipeline"
    connector_id: str
    # All URLs are fixed relative endpoints of a host-approved connector.
    prefix: str = "/evander-releases/v1"

    @field_validator("prefix")
    @classmethod
    def prefix_path(cls, value):
        if not re.fullmatch(r"/[a-zA-Z0-9/_-]+", value) or ".." in value:
            raise ValueError("invalid deployment endpoint prefix")
        return value.rstrip("/")


class SSHDeployment(Contract):
    kind: Literal["ssh"] = "ssh"
    # Operator-owned /etc profile, never an agent-supplied command or key path.
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


class Source(Contract):
    managed_repo_id: str
    component: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_path: str = "."
    # A pipeline builds inside its isolated runner; SSH consumes an already built Git tree.
    required_checks: list[str] = Field(default_factory=lambda: ["php-lint"], min_length=1)
    build_connector_id: str | None = None

    @field_validator("source_path")
    @classmethod
    def safe_path(cls, value):
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("source_path must stay inside the managed repository")
        return value


class Site(Contract):
    enabled: bool = False
    origin: str
    rest_path: str = "/wp-json/"
    connector_id: str
    capabilities: set[Literal["cms", "browser", "source"]] = Field(default_factory=lambda: {"cms"})
    browser_target: str | None = None
    browser_origins: list[str] = Field(default_factory=list, max_length=20)
    source: Source | None = None
    deployment: Pipeline | SSHDeployment | None = Field(default=None, discriminator="kind")
    checks: list[Check] = Field(default_factory=lambda: [Check()], min_length=1, max_length=40)
    retention_days: int = Field(default=90, ge=7, le=3650)
    change_ttl_seconds: int = Field(default=3600, ge=300, le=86400)
    # Set by the add-on's successful restore probe, bound to this exact configuration.
    recovery_verified: str | None = None

    @field_validator("origin")
    @classmethod
    def valid_origin(cls, value):
        origin = website_origin(value)
        if urlsplit(value).path not in {"", "/"} or urlsplit(value).query:
            raise ValueError("site origin must have no path or query")
        return origin

    @field_validator("browser_origins")
    @classmethod
    def valid_browser_origins(cls, values):
        return [cls.valid_origin(value) for value in values]

    @field_validator("rest_path")
    @classmethod
    def valid_rest_path(cls, value):
        if not re.fullmatch(
            r"/(?:[a-zA-Z0-9_-]+/)*(?:[a-zA-Z0-9_-]+/|index\.php\?rest_route=/)", value
        ):
            raise ValueError("use a WordPress REST path ending in / or index.php?rest_route=/")
        return value

    @model_validator(mode="after")
    def supported_paths(self):
        if "source" in self.capabilities and (self.source is None or self.deployment is None):
            raise ValueError("source editing requires a repository and external recovery adapter")
        if (
            "source" in self.capabilities
            and isinstance(self.deployment, SSHDeployment)
            and not self.source.build_connector_id
        ):
            raise ValueError("SSH deployments require an isolated build pipeline connector")
        if "browser" in self.capabilities and not self.browser_target:
            raise ValueError("browser editing requires a managed HTTP target alias")
        return self

    def binding(self):
        value = self.model_dump(mode="json", exclude={"enabled", "recovery_verified"})
        value["capabilities"] = sorted(self.capabilities)
        return digest(value)


class Settings(Contract):
    sites: dict[str, Site] = Field(default_factory=dict, max_length=128)

    @field_validator("sites")
    @classmethod
    def valid_ids(cls, value):
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", key) for key in value):
            raise ValueError("invalid site ID")
        return value


class Resource(Contract):
    key: str = Field(min_length=1, max_length=240)
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    # None deletes the explicitly addressed resource; adapters validate their own fields.
    value: dict[str, Any] | list[Any] | str | int | float | bool | None


class CodeChange(Contract):
    source_ref: str
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
    tree_hash: str = Field(pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")


class Prepare(Contract):
    site_id: str
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    summary: str = Field(min_length=1, max_length=2000)
    resources: list[Resource] = Field(default_factory=list, max_length=200)
    code: CodeChange | None = None
    browser: bool = False
    checks: list[Check] = Field(default_factory=list, max_length=40)
    change_id: str | None = Field(default=None, pattern=r"^wp_[a-f0-9]{32}$")
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def nonempty_unique(self):
        if (self.change_id is None) != (self.expected_revision is None):
            raise ValueError("extending a prepared change requires its ID and expected revision")
        if self.change_id and (self.code or self.checks):
            raise ValueError(
                "extensions add resource coverage; source and checks belong in the initial preparation"
            )
        if not self.resources and not self.code:
            raise ValueError("declare resources or a pinned source change")
        if len({r.key for r in self.resources}) != len(self.resources):
            raise ValueError("duplicate resource keys")
        if self.browser and not self.resources:
            raise ValueError("browser editing requires an explicit resource recovery manifest")
        return self


class Mutation(Contract):
    site_id: str
    change_id: str = Field(pattern=r"^wp_[a-f0-9]{32}$")
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")


OPERATIONS = {
    "discover",
    "inspect",
    "prepare_change",
    "apply_change",
    "change_status",
    "restore_change",
}


def operation_definitions():
    from evander.connectors.models import ConnectorOperationDefinition

    result = {}
    for name in sorted(OPERATIONS):
        schema = (
            Prepare
            if name == "prepare_change"
            else Mutation
            if name in {"apply_change", "restore_change"}
            else None
        )
        input_schema = (
            schema.model_json_schema()
            if schema
            else {
                "type": "object",
                "properties": {
                    "site_id": {"type": "string"},
                    **({"key": {"type": "string"}} if name == "inspect" else {}),
                    **({"change_id": {"type": "string"}} if name == "change_status" else {}),
                },
                "required": ["site_id"] + (["key"] if name == "inspect" else []),
                "additionalProperties": False,
            }
        )
        result[name] = ConnectorOperationDefinition(
            description={
                "discover": "Discover WordPress resources and recovery coverage",
                "inspect": "Read a resource and its current version",
                "prepare_change": "Capture durable recovery data before changing the website",
                "apply_change": "Queue publication; poll change_status until terminal before reporting completion",
                "change_status": "Read change history, checks, screenshots and undo receipts",
                "restore_change": "Restore only this change, checking for intervening edits",
            }[name],
            access="write" if schema else "read",
            input_schema=input_schema,
            requires_approval=False,
            result_mode="json",
            timeout_seconds=300 if schema else 30,
        )
    return result
