"""Bounded temporary snapshots for delegated native Teams voice queries."""

from __future__ import annotations

import hashlib
import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from evander.storage import JsonObjectStore

MAX_IMAGE_BYTES = 512 * 1024
MAX_REQUEST_BYTES = 2 * MAX_IMAGE_BYTES + 64 * 1024
CALL_QUOTA_BYTES = 16 * 1024 * 1024
GLOBAL_QUOTA_BYTES = 128 * 1024 * 1024
SNAPSHOT_TTL = timedelta(hours=24)


class VisualSnapshotMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["camera", "screen"]
    captured_at: datetime
    width: int = Field(ge=1, le=1920)
    height: int = Field(ge=1, le=1920)

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Snapshot capture time requires a timezone")
        return value.astimezone(UTC)


class VoiceVisualReference(VisualSnapshotMetadata):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sha256: str
    byte_count: int = Field(ge=1, le=MAX_IMAGE_BYTES)


def validate_snapshots(
    images: list[tuple[dict[str, Any], bytes]],
) -> list[tuple[VisualSnapshotMetadata, bytes]]:
    if not 1 <= len(images) <= 2:
        raise ValueError("A visual query requires one or two snapshots")
    result = []
    sources = set()
    for raw, content in images:
        metadata = VisualSnapshotMetadata.model_validate(raw)
        if metadata.source in sources:
            raise ValueError("Only one snapshot per visual source is allowed")
        sources.add(metadata.source)
        if not 0 < len(content) <= MAX_IMAGE_BYTES:
            raise ValueError("Snapshot exceeds the 512 KiB image limit")
        now = datetime.now(UTC)
        # Frozen turn images can wait for model tool invocation and HTTP retries.
        if not now - timedelta(minutes=5) <= metadata.captured_at <= now + timedelta(seconds=30):
            raise ValueError("Snapshot capture time is outside the accepted turn window")
        long_edge, short_edge = (1280, 720) if metadata.source == "camera" else (1920, 1080)
        if (
            max(metadata.width, metadata.height) > long_edge
            or min(metadata.width, metadata.height) > short_edge
        ):
            raise ValueError("Snapshot dimensions exceed the source limit")
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "JPEG" or image.size != (metadata.width, metadata.height):
                    raise ValueError("Snapshot must be a JPEG with matching dimensions")
                image.load()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Invalid JPEG snapshot") from exc
        result.append((metadata, content))
    return result


def snapshot_fingerprint(images: list[tuple[VisualSnapshotMetadata, bytes]]) -> str:
    parts = [
        f"{meta.model_dump_json()}:{hashlib.sha256(content).hexdigest()}"
        for meta, content in images
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


class VoiceVisualStore(JsonObjectStore):
    """Private blobs, serialized across gateway and supervisor processes.

    Snapshots have no public download links and are never workspace artifacts.
    The index is committed after all blobs; interrupted writes are removed by cleanup.
    """

    def __init__(
        self,
        runtime_dir: Path,
        *,
        call_quota: int = CALL_QUOTA_BYTES,
        global_quota: int = GLOBAL_QUOTA_BYTES,
    ) -> None:
        self.root = Path(runtime_dir) / "voice-visuals"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.call_quota = call_quota
        self.global_quota = global_quota
        super().__init__(self.root / "index.json", {"queries": {}}, file_mode=0o600)

    def stage(
        self, query: Any, images: list[tuple[VisualSnapshotMetadata, bytes]]
    ) -> list[VoiceVisualReference]:
        with self._lock, self._file_lock():
            payload = self._read_object_unlocked()
            self._cleanup(payload, datetime.now(UTC))
            entries = payload["queries"]
            existing = entries.get(query.query_id)
            fingerprint = snapshot_fingerprint(images)
            if existing:
                if (
                    existing["fingerprint"] != fingerprint
                    or existing["origin_session_id"] != query.origin_session_id
                ):
                    raise ValueError(
                        "Voice query request_id already used with different visual context"
                    )
                self._write_object_unlocked(payload)
                return [VoiceVisualReference.model_validate(ref) for ref in existing["images"]]
            size = sum(len(content) for _, content in images)
            total = sum(ref["byte_count"] for entry in entries.values() for ref in entry["images"])
            per_call = sum(
                ref["byte_count"]
                for entry in entries.values()
                if entry["voice_session_id"] == query.voice_session_id
                for ref in entry["images"]
            )
            per_call_count = sum(
                entry["voice_session_id"] == query.voice_session_id for entry in entries.values()
            )
            if (
                total + size > self.global_quota
                or per_call + size > self.call_quota
                or len(entries) >= 1024
                or per_call_count >= 128
            ):
                self._write_object_unlocked(payload)
                raise VisualQuotaExceeded(
                    "Temporary visual storage is full; no snapshots were retained"
                )
            references = []
            try:
                for metadata, content in images:
                    snapshot_id = uuid4().hex
                    path = self.root / f"{snapshot_id}.jpg"
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    references.append(
                        VoiceVisualReference(
                            **metadata.model_dump(),
                            snapshot_id=snapshot_id,
                            byte_count=len(content),
                            sha256=hashlib.sha256(content).hexdigest(),
                        )
                    )
                entries[query.query_id] = {
                    "voice_session_id": query.voice_session_id,
                    "origin_session_id": query.origin_session_id,
                    "directory_path": query.directory_path,
                    "created_at": query.created_at.isoformat(),
                    "expires_at": (query.created_at + SNAPSHOT_TTL).isoformat(),
                    "fingerprint": fingerprint,
                    "images": [ref.model_dump(mode="json") for ref in references],
                }
                self._write_object_unlocked(payload)
            except Exception:
                for reference in references:
                    (self.root / f"{reference.snapshot_id}.jpg").unlink(missing_ok=True)
                raise
            return references

    def read(self, query: Any) -> list[tuple[VoiceVisualReference, bytes]]:
        with self._lock, self._file_lock():
            payload = self._read_object_unlocked()
            self._cleanup(payload, datetime.now(UTC))
            self._write_object_unlocked(payload)
            entry = payload["queries"].get(query.query_id)
            if not entry:
                raise FileNotFoundError("Visual snapshots are unavailable or have expired")
            if (
                entry["origin_session_id"] != query.origin_session_id
                or entry["voice_session_id"] != query.voice_session_id
                or entry["directory_path"] != query.directory_path
            ):
                raise ValueError("Visual context is outside this query scope")
            result = []
            for raw in entry["images"]:
                reference = VoiceVisualReference.model_validate(raw)
                # IDs are generated by this store, never caller-provided paths.
                if len(reference.snapshot_id) != 32 or any(
                    c not in "0123456789abcdef" for c in reference.snapshot_id
                ):
                    raise ValueError("Invalid stored snapshot ID")
                content = (self.root / f"{reference.snapshot_id}.jpg").read_bytes()
                if (
                    len(content) != reference.byte_count
                    or hashlib.sha256(content).hexdigest() != reference.sha256
                ):
                    raise ValueError("Stored snapshot failed integrity validation")
                result.append((reference, content))
            return result

    def cleanup(
        self,
        *,
        get_query: Callable[[str], Any] | None = None,
        get_queries: Callable[[list[str]], dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> None:
        with self._lock, self._file_lock():
            payload = self._read_object_unlocked()
            if get_queries is not None:
                queries = get_queries(list(payload["queries"]))
                get_query = queries.get
            self._cleanup(payload, now or datetime.now(UTC), get_query=get_query)
            self._write_object_unlocked(payload)

    def _cleanup(
        self,
        payload: dict[str, Any],
        now: datetime,
        *,
        get_query: Callable[[str], Any] | None = None,
    ) -> None:
        entries = payload["queries"]
        for query_id, entry in list(entries.items()):
            query = get_query(query_id) if get_query else None
            orphaned = bool(
                get_query
                and query is not None
                and not query.visual_context
                and datetime.fromisoformat(entry["created_at"]) + timedelta(minutes=5) <= now
            )
            if (
                datetime.fromisoformat(entry["expires_at"]) <= now
                or orphaned
                or (get_query and (query is None or query.status in {"answered", "failed"}))
            ):
                del entries[query_id]
        referenced = {
            f"{ref['snapshot_id']}.jpg" for entry in entries.values() for ref in entry["images"]
        }
        for path in self.root.glob("*.jpg"):
            if path.name not in referenced:
                path.unlink(missing_ok=True)


class VisualQuotaExceeded(ValueError):
    pass
