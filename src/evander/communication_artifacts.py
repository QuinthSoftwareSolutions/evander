from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


DEFAULT_COMMUNICATION_ARTIFACT_TTL_SECONDS = 7 * 24 * 60 * 60
_ARTIFACT_ID_RE = re.compile(r"^comm_[0-9a-f]{32}$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> datetime:
    return datetime.now(UTC)


class CommunicationArtifactMetadata(BaseModel):
    artifact_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    source_message_id: str | None = None
    source_attachment_id: str | None = None
    source_channel: str | None = None
    source_mailbox: str | None = None
    original_filename: str = Field(min_length=1)
    normalized_safe_filename: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    mime_type: str | None = None
    byte_count: int = Field(ge=0)
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    staged_at: datetime = Field(default_factory=utc_now)
    session_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class StagedCommunicationArtifact:
    metadata: CommunicationArtifactMetadata
    blob_path: Path
    metadata_path: Path


class CommunicationArtifactStore:
    """Host-side store for provider-sourced communication attachments.

    The store chooses artifact IDs and blob locations itself. Provider metadata is
    recorded only as metadata; it is never used as a destination path.
    """

    def __init__(
        self,
        runtime_dir: Path,
        *,
        ttl_seconds: int = DEFAULT_COMMUNICATION_ARTIFACT_TTL_SECONDS,
    ) -> None:
        self.root = Path(runtime_dir) / "communication-artifacts"
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.root.mkdir(parents=True, exist_ok=True)

    def stage(
        self,
        *,
        provider: str,
        session_id: str,
        content: bytes,
        original_filename: str,
        mime_type: str | None = None,
        source_message_id: str | None = None,
        source_attachment_id: str | None = None,
        source_channel: str | None = None,
        source_mailbox: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> CommunicationArtifactMetadata:
        self.cleanup_expired()
        artifact_id = f"comm_{uuid4().hex}"
        safe_filename = normalize_safe_filename(original_filename)
        now = created_at or utc_now()
        expiry = expires_at or now + timedelta(seconds=self.ttl_seconds)
        blob_path = self.blob_path(artifact_id)
        metadata_path = self.metadata_path(artifact_id)
        blob_path.write_bytes(content)
        digest = sha256(content).hexdigest()
        resolved_mime = mime_type or mimetypes.guess_type(safe_filename)[0]
        record = CommunicationArtifactMetadata(
            artifact_id=artifact_id,
            provider=str(provider or "").strip().lower(),
            source_message_id=source_message_id,
            source_attachment_id=source_attachment_id,
            source_channel=source_channel,
            source_mailbox=source_mailbox,
            original_filename=str(original_filename or "attachment"),
            normalized_safe_filename=safe_filename,
            filename=safe_filename,
            mime_type=resolved_mime,
            byte_count=len(content),
            sha256=digest,
            created_at=now,
            expires_at=expiry,
            staged_at=now,
            session_id=session_id,
            metadata={"storage": "host_artifact_store", **dict(metadata or {})},
        )
        self._atomic_write_json(metadata_path, record.model_dump(mode="json"))
        return record

    def get(self, artifact_id: str) -> StagedCommunicationArtifact:
        self.cleanup_expired()
        safe_id = validate_artifact_id(artifact_id)
        metadata_path = self.metadata_path(safe_id)
        blob_path = self.blob_path(safe_id)
        if not metadata_path.is_file() or not blob_path.is_file():
            raise FileNotFoundError(f"Unknown staged artifact_id: {safe_id}")
        record = CommunicationArtifactMetadata.model_validate(
            json.loads(metadata_path.read_text(encoding="utf-8"))
        )
        if record.expires_at <= utc_now():
            self.delete(safe_id)
            raise FileNotFoundError(f"Expired staged artifact_id: {safe_id}")
        return StagedCommunicationArtifact(record, blob_path, metadata_path)

    def payload(self, artifact_id: str) -> dict[str, Any]:
        staged = self.get(artifact_id)
        return {**staged.metadata.model_dump(mode="json"), "path": str(staged.blob_path)}

    def delete(self, artifact_id: str) -> None:
        safe_id = validate_artifact_id(artifact_id)
        for path in (self.metadata_path(safe_id), self.blob_path(safe_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = now or utc_now()
        removed = 0
        for metadata_path in self.root.glob("comm_*.json"):
            artifact_id = metadata_path.stem
            try:
                record = CommunicationArtifactMetadata.model_validate(
                    json.loads(metadata_path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
            if record.expires_at <= current:
                self.delete(artifact_id)
                removed += 1
        return removed

    def metadata_path(self, artifact_id: str) -> Path:
        return self.root / f"{validate_artifact_id(artifact_id)}.json"

    def blob_path(self, artifact_id: str) -> Path:
        return self.root / f"{validate_artifact_id(artifact_id)}.bin"

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)


def validate_artifact_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not _ARTIFACT_ID_RE.fullmatch(normalized):
        raise ValueError("artifact_id must be an opaque staged artifact ID")
    return normalized


def normalize_safe_filename(value: str) -> str:
    original = str(value or "attachment").replace("\x00", "")
    name = Path(original).name
    # pathlib on POSIX does not split Windows separators, so handle them explicitly.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE_FILENAME_RE.sub("-", name).strip(".-")
    if not name or name in {".", ".."}:
        name = "attachment"
    return name[:255]
