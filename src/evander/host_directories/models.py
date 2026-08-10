from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


HostDirectoryCapability = Literal["list", "import", "delete"]
HOST_DIRECTORY_CAPABILITIES: tuple[HostDirectoryCapability, ...] = (
    "list",
    "import",
    "delete",
)
HOST_DIRECTORY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
DEFAULT_MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_MAX_IMPORT_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_IMPORT_ENTRIES = 1_000
MAX_MAX_IMPORT_ENTRIES = 10_000
DEFAULT_MAX_WATCH_ENTRIES = 10_000
MAX_MAX_WATCH_ENTRIES = 100_000


def normalize_host_directory_id(value: object, *, field_name: str = "directory_id") -> str:
    normalized = str(value or "").strip().lower()
    if not HOST_DIRECTORY_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must start with a lowercase letter or digit and contain only "
            "lowercase letters, digits, '.', '_', or '-'"
        )
    return normalized


def normalize_host_directory_capability(value: object) -> HostDirectoryCapability:
    normalized = str(value or "").strip().lower()
    if normalized not in HOST_DIRECTORY_CAPABILITIES:
        allowed = ", ".join(HOST_DIRECTORY_CAPABILITIES)
        raise ValueError(f"host directory capability must be one of: {allowed}")
    return normalized  # type: ignore[return-value]


def normalize_host_directory_capabilities(value: object) -> list[HostDirectoryCapability]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("host directory capabilities must be a list")
    normalized: list[HostDirectoryCapability] = []
    for item in value:
        capability = normalize_host_directory_capability(item)
        if capability not in normalized:
            normalized.append(capability)
    return normalized


def normalize_host_directory_grant_capabilities(
    value: object,
) -> list[HostDirectoryCapability]:
    if value is None or isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("host directory grant capabilities must be a list")
    return normalize_host_directory_capabilities(value)


def normalize_host_directory_root(value: object) -> Path:
    text = str(value or "").strip()
    raw_root = Path(text)
    if not raw_root.is_absolute():
        raise ValueError("host directory root must be an absolute path")
    root = Path(os.path.abspath(text))
    if root == Path(root.anchor):
        raise ValueError("host directory root must not be a filesystem root")
    return root


class HostDirectoryDefinition(BaseModel):
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    root: Path
    capabilities: list[HostDirectoryCapability] = Field(default_factory=lambda: ["list", "import"])
    delete_requires_approval: bool = True
    max_import_bytes: int = Field(
        default=DEFAULT_MAX_IMPORT_BYTES,
        gt=0,
        le=MAX_MAX_IMPORT_BYTES,
    )
    max_import_entries: int = Field(
        default=DEFAULT_MAX_IMPORT_ENTRIES,
        gt=0,
        le=MAX_MAX_IMPORT_ENTRIES,
    )
    max_watch_entries: int = Field(
        default=DEFAULT_MAX_WATCH_ENTRIES,
        gt=0,
        le=MAX_MAX_WATCH_ENTRIES,
    )
    disabled: bool = False

    @field_validator("display_name", "description", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must be a non-empty string")
        return normalized

    @field_validator("root", mode="before")
    @classmethod
    def normalize_root(cls, value: object) -> Path:
        return normalize_host_directory_root(value)

    @field_validator("capabilities", mode="before")
    @classmethod
    def normalize_capabilities(cls, value: object) -> list[HostDirectoryCapability]:
        return normalize_host_directory_capabilities(value)

    @model_validator(mode="after")
    def validate_delete_configuration(self) -> "HostDirectoryDefinition":
        if "delete" not in self.capabilities and not self.delete_requires_approval:
            raise ValueError(
                "delete_requires_approval may be false only when the delete capability is enabled"
            )
        return self


class HostDirectoryDirectoryConfig(BaseModel):
    host_directories: dict[str, HostDirectoryDefinition] = Field(default_factory=dict)

    @field_validator("host_directories", mode="before")
    @classmethod
    def normalize_directory_map(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("host_directories must be an object")
        normalized: dict[str, object] = {}
        for raw_id, definition in value.items():
            directory_id = normalize_host_directory_id(raw_id)
            if directory_id in normalized:
                raise ValueError(f"duplicate host directory id: {directory_id!r}")
            normalized[directory_id] = definition
        return normalized


class HostDirectoryGrant(BaseModel):
    directory_id: str
    capabilities: list[HostDirectoryCapability]

    @field_validator("directory_id", mode="before")
    @classmethod
    def normalize_directory_id_field(cls, value: object) -> str:
        return normalize_host_directory_id(value)

    @field_validator("capabilities", mode="before")
    @classmethod
    def normalize_capabilities_field(cls, value: object) -> list[HostDirectoryCapability]:
        return normalize_host_directory_grant_capabilities(value)


def normalize_host_directory_grants(value: object) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            "host_directory_grants must be an object mapping directory ids to capabilities"
        )
    normalized: dict[str, list[str]] = {}
    for directory_id, capabilities in value.items():
        grant = HostDirectoryGrant(directory_id=directory_id, capabilities=capabilities)
        normalized[grant.directory_id] = list(grant.capabilities)
    return normalized


_DEFINITION_FIELDS = frozenset(HostDirectoryDefinition.model_fields)


def validate_host_directory_directory_overlay(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("host directory configuration must be a JSON object")
    unknown_top_level = sorted(set(payload) - {"host_directories"})
    if unknown_top_level:
        raise ValueError(
            "host directory configuration does not allow fields: " + ", ".join(unknown_top_level)
        )
    raw_directories = payload.get("host_directories", {})
    if not isinstance(raw_directories, dict):
        raise ValueError("host_directories must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_id, raw_definition in raw_directories.items():
        directory_id = normalize_host_directory_id(raw_id)
        if directory_id in normalized:
            raise ValueError(f"duplicate host directory id: {directory_id!r}")
        if not isinstance(raw_definition, dict):
            raise ValueError(f"host directory {directory_id!r} must be an object")
        unknown_fields = sorted(set(raw_definition) - _DEFINITION_FIELDS)
        if unknown_fields:
            raise ValueError(
                f"host directory {directory_id!r} does not allow fields: "
                + ", ".join(unknown_fields)
            )
        definition = dict(raw_definition)
        if "root" in definition:
            try:
                definition["root"] = str(normalize_host_directory_root(definition["root"]))
            except ValueError as exc:
                raise ValueError(f"host directory {directory_id!r} {exc}") from exc
        if "capabilities" in definition:
            definition["capabilities"] = normalize_host_directory_capabilities(
                definition["capabilities"]
            )
        for field_name, lower, upper in (
            ("max_import_bytes", 1, MAX_MAX_IMPORT_BYTES),
            ("max_import_entries", 1, MAX_MAX_IMPORT_ENTRIES),
            ("max_watch_entries", 1, MAX_MAX_WATCH_ENTRIES),
        ):
            if field_name not in definition:
                continue
            if isinstance(definition[field_name], bool):
                raise ValueError(f"host directory {directory_id!r} {field_name} must be an integer")
            field_value = int(definition[field_name])
            if field_value < lower or field_value > upper:
                raise ValueError(
                    f"host directory {directory_id!r} {field_name} must be between "
                    f"{lower} and {upper}"
                )
            definition[field_name] = field_value
        for field_name in ("display_name", "description"):
            if field_name in definition:
                text = str(definition[field_name] or "").strip()
                if not text:
                    raise ValueError(
                        f"host directory {directory_id!r} {field_name} must be non-empty"
                    )
                definition[field_name] = text
        for field_name in ("delete_requires_approval", "disabled"):
            if field_name in definition and not isinstance(definition[field_name], bool):
                raise ValueError(f"host directory {directory_id!r} {field_name} must be a boolean")
        normalized[directory_id] = definition
    return {"host_directories": normalized} if "host_directories" in payload else {}
