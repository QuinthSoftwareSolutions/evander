from __future__ import annotations

import fcntl
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

PUBLISHED_ARTIFACT_ARCHIVE_FORMAT = "tar.gz"
PUBLISHED_ARTIFACT_MIME_TYPE = "application/gzip"
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> datetime:
    return datetime.now(UTC)


class PublishedArtifactMetadata(BaseModel):
    key: str = Field(min_length=1)
    storage_id: str = Field(min_length=64, max_length=64)
    archive_format: Literal["tar.gz"] = PUBLISHED_ARTIFACT_ARCHIVE_FORMAT
    filename: str = Field(min_length=1)
    mime_type: str = PUBLISHED_ARTIFACT_MIME_TYPE
    byte_count: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    published_at: datetime = Field(default_factory=utc_now)
    session_id: str = Field(min_length=1)
    vm_name: str | None = None
    execution_backend: str | None = None
    source_artifacts_root: str | None = None
    source_file_count: int = Field(default=0, ge=0)
    source_byte_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class PublishedArtifact:
    metadata: PublishedArtifactMetadata
    blob_path: Path
    metadata_path: Path


class PublishedArtifactStore:
    """Durable host-side archive store for published agent artifacts."""

    def __init__(self, runtime_dir: Path) -> None:
        self.root = Path(runtime_dir) / "published-artifacts"
        self.blob_root = self.root / "blobs"
        self.metadata_root = self.root / "metadata"
        self.root.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)

    def exists(self, key: str) -> bool:
        storage_id = storage_id_for_key(validate_artifact_key(key))
        return self.metadata_path(storage_id).is_file()

    def publish_archive(
        self,
        *,
        key: str,
        session_id: str,
        archive_path: Path,
        source_artifacts_root: str | None = None,
        source_file_count: int = 0,
        source_byte_count: int = 0,
        vm_name: str | None = None,
        execution_backend: str | None = None,
        metadata: dict[str, Any] | None = None,
        published_at: datetime | None = None,
    ) -> PublishedArtifactMetadata:
        normalized_key = validate_artifact_key(key)
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        source_path = Path(archive_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"Archive path does not exist: {source_path}")

        storage_id = storage_id_for_key(normalized_key)
        metadata_path = self.metadata_path(storage_id)
        blob_path = self.blob_path(storage_id)
        with self._lock():
            if metadata_path.exists() or blob_path.exists():
                raise ValueError(f"Published artifact key already exists: {normalized_key}")
            temp_blob = blob_path.with_name(f".{blob_path.name}.{uuid4().hex}.tmp")
            temp_metadata = metadata_path.with_name(f".{metadata_path.name}.{uuid4().hex}.tmp")
            try:
                content_digest = sha256()
                byte_count = 0
                with source_path.open("rb") as source, temp_blob.open("wb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        content_digest.update(chunk)
                        byte_count += len(chunk)
                record = PublishedArtifactMetadata(
                    key=normalized_key,
                    storage_id=storage_id,
                    filename=filename_for_key(normalized_key),
                    mime_type=PUBLISHED_ARTIFACT_MIME_TYPE,
                    byte_count=byte_count,
                    sha256=content_digest.hexdigest(),
                    published_at=published_at or utc_now(),
                    session_id=normalized_session_id,
                    vm_name=_optional_text(vm_name),
                    execution_backend=_optional_text(execution_backend),
                    source_artifacts_root=_optional_text(source_artifacts_root),
                    source_file_count=int(source_file_count or 0),
                    source_byte_count=int(source_byte_count or 0),
                    metadata=dict(metadata or {}),
                )
                temp_metadata.write_text(
                    json.dumps(record.model_dump(mode="json"), sort_keys=True),
                    encoding="utf-8",
                )
                temp_blob.replace(blob_path)
                temp_metadata.replace(metadata_path)
                return record
            except Exception:
                for temp_path in (temp_blob, temp_metadata):
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass
                raise

    def publish_bytes(
        self,
        *,
        key: str,
        session_id: str,
        content: bytes,
        source_artifacts_root: str | None = None,
        source_file_count: int = 0,
        source_byte_count: int = 0,
        vm_name: str | None = None,
        execution_backend: str | None = None,
        metadata: dict[str, Any] | None = None,
        published_at: datetime | None = None,
    ) -> PublishedArtifactMetadata:
        temp_path = self.root / f".publish-{uuid4().hex}.tar.gz"
        try:
            temp_path.write_bytes(content)
            return self.publish_archive(
                key=key,
                session_id=session_id,
                archive_path=temp_path,
                source_artifacts_root=source_artifacts_root,
                source_file_count=source_file_count,
                source_byte_count=source_byte_count,
                vm_name=vm_name,
                execution_backend=execution_backend,
                metadata=metadata,
                published_at=published_at,
            )
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def get(self, key: str) -> PublishedArtifact:
        normalized_key = validate_artifact_key(key)
        storage_id = storage_id_for_key(normalized_key)
        metadata_path = self.metadata_path(storage_id)
        blob_path = self.blob_path(storage_id)
        if not metadata_path.is_file() or not blob_path.is_file():
            raise FileNotFoundError(f"Unknown published artifact key: {normalized_key}")
        record = PublishedArtifactMetadata.model_validate(
            json.loads(metadata_path.read_text(encoding="utf-8"))
        )
        return PublishedArtifact(record, blob_path, metadata_path)

    def list_metadata(
        self,
        *,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        key: str | None = None,
    ) -> list[PublishedArtifactMetadata]:
        normalized_key = validate_artifact_key(key) if key else None
        records: list[PublishedArtifactMetadata] = []
        for metadata_path in self.metadata_root.glob("*.json"):
            try:
                record = PublishedArtifactMetadata.model_validate(
                    json.loads(metadata_path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
            if normalized_key is not None and record.key != normalized_key:
                continue
            published_at = _ensure_utc(record.published_at)
            if published_after is not None and published_at < _ensure_utc(published_after):
                continue
            if published_before is not None and published_at > _ensure_utc(published_before):
                continue
            records.append(record)
        records.sort(key=lambda item: (_ensure_utc(item.published_at), item.key), reverse=True)
        return records

    def metadata_path(self, storage_id: str) -> Path:
        return self.metadata_root / f"{validate_storage_id(storage_id)}.json"

    def blob_path(self, storage_id: str) -> Path:
        return self.blob_root / f"{validate_storage_id(storage_id)}.tar.gz"

    @contextmanager
    def _lock(self):
        lock_path = self.root / ".lock"
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validate_artifact_key(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("artifact key is required")
    if len(text) > 512:
        raise ValueError("artifact key must be 512 characters or fewer")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("artifact key must not contain control characters")
    return text


def validate_storage_id(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ValueError("invalid artifact storage id")
    return text


def storage_id_for_key(key: str) -> str:
    return sha256(validate_artifact_key(key).encode("utf-8")).hexdigest()


def filename_for_key(key: str) -> str:
    text = validate_artifact_key(key).replace("\\", "/").rsplit("/", 1)[-1]
    safe = _SAFE_FILENAME_RE.sub("-", text).strip(".-") or "artifact"
    suffix = ".tar.gz"
    if safe.endswith(suffix):
        safe = safe[: -len(suffix)] or "artifact"
    return f"{safe[: 255 - len(suffix)]}{suffix}"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None
