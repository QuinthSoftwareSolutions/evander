from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.storage import JsonObjectStore

SCHEMA_VERSION = 1
STATE_FILE = "artifact-spaces.json"
_SAFE_SPACE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def utc_now() -> datetime:
    return datetime.now(UTC)


class ArtifactSpaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ArtifactSpaceGrant(ArtifactSpaceModel):
    read_subjects: list[str] = Field(default_factory=list)
    write_subjects: list[str] = Field(default_factory=list)
    manage_subjects: list[str] = Field(default_factory=list)

    @field_validator("read_subjects", "write_subjects", "manage_subjects", mode="before")
    @classmethod
    def _normalize_subjects(cls, value: object) -> list[str]:
        return _unique_text_list(value, "subjects")

    def allows(self, subject: str | None, capability: str) -> bool:
        normalized = str(subject or "").strip()
        if not self.read_subjects and not self.write_subjects and not self.manage_subjects:
            return True
        if not normalized:
            return False
        if normalized in self.manage_subjects:
            return True
        if capability == "read":
            return normalized in self.read_subjects or normalized in self.write_subjects
        if capability == "write":
            return normalized in self.write_subjects
        if capability == "manage":
            return normalized in self.manage_subjects
        return False


class ArtifactSpace(ArtifactSpaceModel):
    path: str
    parent_path: str | None = None
    display_name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    grants: ArtifactSpaceGrant = Field(default_factory=ArtifactSpaceGrant)

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, value: object) -> str:
        return normalize_space_path(value)

    @field_validator("parent_path", mode="before")
    @classmethod
    def _normalize_parent_path(cls, value: object) -> str | None:
        if value is None:
            return None
        return normalize_space_path(value)

    @field_validator("display_name", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _default_parent(self) -> "ArtifactSpace":
        expected_parent = parent_space_path(self.path)
        if self.parent_path is None:
            self.parent_path = expected_parent
        elif self.parent_path != expected_parent:
            raise ValueError(
                f"artifact space {self.path} parent must be {expected_parent or '<root>'}"
            )
        return self


class ArtifactSpaceVersion(ArtifactSpaceModel):
    id: str
    space_path: str
    artifact_id: str
    version: int = Field(ge=1)
    artifact_key: str
    created_at: datetime = Field(default_factory=utc_now)
    created_by_subject: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "artifact_id", "artifact_key", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("space_path", mode="before")
    @classmethod
    def _space_path(cls, value: object) -> str:
        return normalize_space_path(value)

    @field_validator("created_by_subject", mode="before")
    @classmethod
    def _created_by_subject(cls, value: object) -> str | None:
        return _optional_text(value)


class ArtifactSpaceState(ArtifactSpaceModel):
    schema_version: int = SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    spaces: dict[str, ArtifactSpace] = Field(default_factory=dict)
    versions: dict[str, ArtifactSpaceVersion] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def empty(cls) -> "ArtifactSpaceState":
        return cls()

    @model_validator(mode="after")
    def _validate_links(self) -> "ArtifactSpaceState":
        errors: list[str] = []
        for path, space in self.spaces.items():
            if path != space.path:
                errors.append(f"space key {path} does not match record path {space.path}")
            if space.parent_path is not None and space.parent_path not in self.spaces:
                errors.append(f"space {path} references missing parent {space.parent_path}")
        for version_id, version in self.versions.items():
            if version_id != version.id:
                errors.append(f"version key {version_id} does not match record id {version.id}")
            if version.space_path not in self.spaces:
                errors.append(f"version {version.id} references missing space {version.space_path}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def versions_for_space(
        self, space_path: str, *, artifact_id: str | None = None
    ) -> list[ArtifactSpaceVersion]:
        normalized_space_path = normalize_space_path(space_path)
        normalized_artifact_id = _optional_text(artifact_id)
        records = [
            version
            for version in self.versions.values()
            if version.space_path == normalized_space_path
            and (normalized_artifact_id is None or version.artifact_id == normalized_artifact_id)
        ]
        records.sort(key=lambda item: (item.artifact_id, item.version, item.created_at))
        return records

    def latest_versions_for_space(
        self, space_path: str, *, artifact_id: str | None = None
    ) -> list[ArtifactSpaceVersion]:
        latest: dict[str, ArtifactSpaceVersion] = {}
        for version in self.versions_for_space(space_path, artifact_id=artifact_id):
            current = latest.get(version.artifact_id)
            if current is None or version.version > current.version:
                latest[version.artifact_id] = version
        return sorted(latest.values(), key=lambda item: item.artifact_id)


class ArtifactSpaceConflictError(RuntimeError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"stale artifact-space state revision: expected {expected_revision}, "
            f"current {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class ArtifactSpacePermissionError(PermissionError):
    pass


class ArtifactSpaceStore:
    def __init__(self, runtime_dir: str | Any) -> None:
        from pathlib import Path

        self.runtime_dir = Path(runtime_dir)
        self.path = self.runtime_dir / STATE_FILE
        self.store: JsonObjectStore[Any] = JsonObjectStore(
            self.path,
            ArtifactSpaceState.empty().model_dump(mode="json"),
        )

    def read(self) -> ArtifactSpaceState:
        return ArtifactSpaceState.model_validate(self.store.read_object())

    def update(
        self,
        mutator: Callable[[ArtifactSpaceState], ArtifactSpaceState | None],
        *,
        expected_revision: int | None = None,
    ) -> ArtifactSpaceState:
        def _update(payload: dict[str, Any]) -> ArtifactSpaceState:
            state = ArtifactSpaceState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise ArtifactSpaceConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            validated = ArtifactSpaceState.model_validate(
                next_state.model_copy(
                    update={"revision": state.revision + 1, "updated_at": utc_now()},
                    deep=True,
                ).model_dump(mode="json")
            )
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            return validated

        return self.store.update_object(_update)  # type: ignore[return-value]

    def ensure_space(
        self,
        *,
        path: str,
        display_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        grants: Mapping[str, Any] | ArtifactSpaceGrant | None = None,
        subject: str | None = None,
        expected_revision: int | None = None,
    ) -> ArtifactSpaceState:
        normalized_path = normalize_space_path(path)

        def _mutate(state: ArtifactSpaceState) -> ArtifactSpaceState:
            _ensure_parent_spaces(state, normalized_path, subject=subject)
            if normalized_path in state.spaces:
                space = state.spaces[normalized_path]
                _require_permission(space, subject=subject, capability="manage")
                if display_name is not None:
                    space.display_name = _optional_text(display_name)
                if metadata is not None:
                    space.metadata = dict(metadata)
                if grants is not None:
                    space.grants = (
                        grants
                        if isinstance(grants, ArtifactSpaceGrant)
                        else ArtifactSpaceGrant.model_validate(grants)
                    )
                space.updated_at = utc_now()
                return state

            parent_path = parent_space_path(normalized_path)
            if parent_path is not None:
                _require_permission(state.spaces[parent_path], subject=subject, capability="manage")
            state.spaces[normalized_path] = ArtifactSpace(
                path=normalized_path,
                parent_path=parent_path,
                display_name=_optional_text(display_name),
                metadata=dict(metadata or {}),
                grants=(
                    grants
                    if isinstance(grants, ArtifactSpaceGrant)
                    else ArtifactSpaceGrant.model_validate(grants or {})
                ),
            )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def publish_version(
        self,
        *,
        space_path: str,
        artifact_id: str,
        artifact_key: str,
        created_by_subject: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> ArtifactSpaceState:
        normalized_space_path = normalize_space_path(space_path)
        normalized_artifact_id = _required_text(artifact_id, "artifact_id")
        normalized_artifact_key = _required_text(artifact_key, "artifact_key")

        def _mutate(state: ArtifactSpaceState) -> ArtifactSpaceState:
            space = _require_space(state, normalized_space_path)
            _require_permission(space, subject=created_by_subject, capability="write")
            current_versions = state.versions_for_space(
                normalized_space_path,
                artifact_id=normalized_artifact_id,
            )
            next_version_number = (
                max((version.version for version in current_versions), default=0) + 1
            )
            version = ArtifactSpaceVersion(
                id=f"asv-{uuid4().hex}",
                space_path=normalized_space_path,
                artifact_id=normalized_artifact_id,
                version=next_version_number,
                artifact_key=normalized_artifact_key,
                created_by_subject=_optional_text(created_by_subject),
                metadata=dict(metadata or {}),
            )
            state.versions[version.id] = version
            return state

        return self.update(_mutate, expected_revision=expected_revision)


def normalize_space_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("artifact space path is required")
    if not text.startswith("/"):
        text = "/" + text
    if text.startswith("//"):
        raise ValueError("artifact space path must be a normalized absolute path")
    path = PurePosixPath(text)
    if str(path) != text or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError("artifact space path must be a normalized absolute path")
    if path.as_posix() == "/":
        return "/"
    for segment in path.parts[1:]:
        if not _SAFE_SPACE_SEGMENT.fullmatch(segment):
            raise ValueError(
                "artifact space path segments may only contain letters, numbers, dot, "
                "underscore, or hyphen"
            )
    return path.as_posix()


def parent_space_path(path: str) -> str | None:
    normalized = normalize_space_path(path)
    if normalized == "/":
        return None
    parent = PurePosixPath(normalized).parent.as_posix()
    return parent if parent != "." else "/"


def artifact_space_state_payload(state: ArtifactSpaceState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def _ensure_parent_spaces(
    state: ArtifactSpaceState,
    path: str,
    *,
    subject: str | None,
) -> None:
    normalized = normalize_space_path(path)
    missing: list[str] = []
    existing_ancestor: ArtifactSpace | None = None
    current = parent_space_path(normalized)
    while current is not None:
        existing_ancestor = state.spaces.get(current)
        if existing_ancestor is not None:
            break
        missing.append(current)
        current = parent_space_path(current)
    if not missing:
        return
    inherited_grants = ArtifactSpaceGrant()
    if existing_ancestor is not None:
        _require_permission(existing_ancestor, subject=subject, capability="manage")
        inherited_grants = existing_ancestor.grants.model_copy(deep=True)
    for space_path in reversed(missing):
        state.spaces[space_path] = ArtifactSpace(
            path=space_path,
            parent_path=parent_space_path(space_path),
            grants=inherited_grants.model_copy(deep=True),
        )


def _require_space(state: ArtifactSpaceState, path: str) -> ArtifactSpace:
    normalized = normalize_space_path(path)
    space = state.spaces.get(normalized)
    if space is None:
        raise ValueError(f"artifact space does not exist: {normalized}")
    return space


def _require_permission(space: ArtifactSpace, *, subject: str | None, capability: str) -> None:
    if not space.grants.allows(subject, capability):
        raise ArtifactSpacePermissionError(
            f"subject {subject or '<anonymous>'} cannot {capability} artifact space {space.path}"
        )


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique_text_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of strings")
    normalized = [_required_text(item, field_name) for item in value]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in normalized:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    if duplicates:
        raise ValueError(f"{field_name} contains duplicate values: {', '.join(sorted(duplicates))}")
    return normalized
