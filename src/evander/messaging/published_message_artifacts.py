"""Prepare directory-owned published evidence for host workflow file delivery."""

from __future__ import annotations

import hashlib
import io
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
import tarfile
from typing import Any
import zlib

from pydantic import BaseModel, ConfigDict, field_validator

from evander.communication_artifact_links import (
    communication_artifact_link_secret_env,
    communication_artifact_public_base_url,
    issue_communication_artifact_download_link,
)
from evander.communication_artifacts import CommunicationArtifactStore, normalize_safe_filename
from evander.integration_services.teams import _artifact_is_inline_teams_image
from evander.models import DirectoryPath
from evander.published_artifacts import PublishedArtifactStore, validate_artifact_key

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


class PublishedMessageArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    path: str | None = None
    filename: str | None = None
    optional: bool = False

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        return validate_artifact_key(value)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if (
            not value
            or not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or path.as_posix() != value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("published artifact path must be a canonical relative file path")
        return value

    @field_validator("filename")
    @classmethod
    def valid_filename(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("attachment filename must be a plain filename")
        return value


def _require_owner(runtime: Any, directory_path: str, artifact: Any) -> None:
    owner = artifact.metadata.metadata.get("directory_path")
    if not owner:
        session = runtime.session_repository.get(artifact.metadata.session_id)
        owner = getattr(session, "directory_path", None)
    selected = DirectoryPath(segments=directory_path).as_posix()
    if not owner:
        if selected != "root":
            raise PermissionError("Published artifact has no verifiable directory owner")
        return
    actual = DirectoryPath(segments=owner).as_posix()
    if actual != selected and not actual.startswith(selected + "/"):
        raise PermissionError("Published artifact is outside the workflow directory")


def _require_content(config: Any, filename: str, mime: str, size: int) -> None:
    limit = config.communication_file_max_bytes
    if limit is not None and size > limit:
        raise ValueError("Published attachment exceeds communication_file_max_bytes")
    extension = PurePosixPath(filename).suffix.lower()

    def normalize(item: str) -> str:
        return "." + item.strip().lower().lstrip(".")

    blocked = {
        normalize(item)
        for item in config.communication_file_blocked_extensions
        if str(item).strip()
    }
    allowed = {
        normalize(item)
        for item in config.communication_file_allowed_extensions
        if str(item).strip()
    }
    if extension in blocked or (allowed and extension not in allowed):
        raise ValueError("Published attachment extension is not allowed")
    mimes = {
        str(item).strip().lower()
        for item in config.communication_file_allowed_mime_types
        if str(item).strip()
    }
    if mimes and mime.lower() not in mimes:
        raise ValueError("Published attachment MIME type is not allowed")


def prepare_published_teams_files(
    *,
    runtime: Any,
    directory_path: str,
    config: Any,
    specifications: list[Any],
    sender_session_id: str,
) -> list[dict[str, Any]]:
    """Stage explicit immutable publications, preserving the existing Teams transport.

    Operator-owned workflow effects use declared target capabilities. MCP agent
    file-send grants and approvals remain enforced by the agent tool boundary.
    """
    if not isinstance(specifications, list) or len(specifications) > 16:
        raise ValueError("published_artifacts must resolve to an array of at most 16 files")
    specs = [PublishedMessageArtifact.model_validate(item) for item in specifications]
    file_policy = runtime.policy_config.resolve_for_directory(directory_path)
    store = PublishedArtifactStore(runtime.config.runtime_dir)
    prepared: list[tuple[str, str, bytes, str]] = []
    archives: dict[str, Any] = {}
    compressed_bytes = 0
    expanded_bytes = 0
    attachment_bytes = 0
    try:
        for spec in specs:
            if spec.key not in archives:
                try:
                    artifact = store.get(spec.key)
                except FileNotFoundError:
                    if spec.optional:
                        continue
                    raise
                _require_owner(runtime, directory_path, artifact)
                if artifact.blob_path.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise ValueError("Published evidence archive exceeds 64 MiB")
                with artifact.blob_path.open("rb") as source:
                    raw = source.read(MAX_ARCHIVE_BYTES + 1)
                compressed_bytes += len(raw)
                if compressed_bytes > MAX_ARCHIVE_BYTES:
                    raise ValueError("Published evidence archives exceed 64 MiB in total")
                if (
                    len(raw) != artifact.metadata.byte_count
                    or hashlib.sha256(raw).hexdigest() != artifact.metadata.sha256
                ):
                    raise ValueError("Published evidence archive checksum mismatch")
                archives[spec.key] = (artifact, raw, None)
            artifact, raw, archive = archives[spec.key]
            if spec.path is None:
                filename = spec.filename or artifact.metadata.filename
                mime = artifact.metadata.mime_type
                content = raw
            else:
                if archive is None:
                    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    unpacked = inflater.decompress(raw, MAX_ARCHIVE_BYTES + 1)
                    expanded_bytes += len(unpacked)
                    if (
                        expanded_bytes > MAX_ARCHIVE_BYTES
                        or not inflater.eof
                        or inflater.unused_data
                    ):
                        raise ValueError(
                            "Expanded evidence archive exceeds 64 MiB or is incomplete"
                        )
                    archive = tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:")
                    archives[spec.key] = (artifact, raw, archive)
                matches = [member for member in archive if member.name == spec.path]
                if not matches and spec.optional:
                    continue
                if len(matches) != 1 or not matches[0].isfile():
                    raise ValueError("Published attachment path must identify one regular file")
                filename = spec.filename or PurePosixPath(spec.path).name
                safe_filename = normalize_safe_filename(filename)
                mime = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
                remaining_bytes = MAX_ARCHIVE_BYTES - attachment_bytes
                if matches[0].size > remaining_bytes:
                    raise ValueError("Published attachments exceed 64 MiB in total")
                limit = file_policy.communication_file_max_bytes
                if limit is not None and matches[0].size > limit:
                    raise ValueError("Published attachment exceeds communication_file_max_bytes")
                with archive.extractfile(matches[0]) as source:
                    content = source.read(remaining_bytes + 1)
            PublishedMessageArtifact.valid_filename(filename)
            _require_content(file_policy, normalize_safe_filename(filename), mime, len(content))
            attachment_bytes += len(content)
            if attachment_bytes > MAX_ARCHIVE_BYTES:
                raise ValueError("Published attachments exceed 64 MiB in total")
            prepared.append((filename, mime, content, spec.key))
    finally:
        for _artifact, _raw, archive in archives.values():
            if archive is not None:
                archive.close()

    # Validate every requested file before making any communication artifacts.
    if any(not _artifact_is_inline_teams_image(mime) for _, mime, _, _ in prepared):
        issue_communication_artifact_download_link(
            artifact_id="comm_" + "0" * 32,
            artifact_expires_at=datetime.now(UTC)
            + timedelta(seconds=config.communication_artifact_link_ttl_seconds),
            public_base_url=communication_artifact_public_base_url(config),
            secret_env=communication_artifact_link_secret_env(config),
            ttl_seconds=config.communication_artifact_link_ttl_seconds,
        )
    staged = CommunicationArtifactStore(runtime.config.runtime_dir)
    payloads: list[dict[str, Any]] = []
    for filename, mime, content, key in prepared:
        record = staged.stage(
            provider="workflow",
            session_id=sender_session_id,
            content=content,
            original_filename=filename,
            mime_type=mime,
            metadata={"published_artifact_key": key, "directory_path": directory_path},
        )
        item = staged.payload(record.artifact_id)
        if not _artifact_is_inline_teams_image(mime):
            link = issue_communication_artifact_download_link(
                artifact_id=record.artifact_id,
                artifact_expires_at=record.expires_at,
                public_base_url=communication_artifact_public_base_url(config),
                secret_env=communication_artifact_link_secret_env(config),
                ttl_seconds=config.communication_artifact_link_ttl_seconds,
            )
            item["download_url"] = link.url
        payloads.append(item)
    return payloads
