from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any, Iterable, Literal, Mapping
from uuid import uuid4

from evander.addons import addon_storage_path
from evander.models import DirectoryPath, utc_now

from .models import TeamChatAttachmentPolicy, TeamState
from .repository import ADDON_ID, TeamStateRepository

STAGING_TTL = timedelta(hours=24)
_SAFE_ATTACHMENT_ID = re.compile(r"^chatatt_[0-9a-f]{32}$")
_SAFE_STAGING_ID = re.compile(r"^chatstage_[0-9a-f]{32}$")
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(frozen=True)
class PreparedChatAttachment:
    id: str
    filename: str
    mime_type: str | None
    byte_count: int
    sha256: str

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class StagedChatAttachment(PreparedChatAttachment):
    session_id: str
    source_path: str
    expires_at: datetime

    def payload(self) -> dict[str, Any]:
        return {
            **super().payload(),
            "session_id": self.session_id,
            "source_path": self.source_path,
            "expires_at": self.expires_at.isoformat(),
        }


class TeamChatAttachmentPolicyService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self.repository = repository

    def effective_policy(
        self,
        directory_path: str | None,
        *,
        state: TeamState | None = None,
    ) -> TeamChatAttachmentPolicy:
        resolved_state = state or self.repository.read()
        normalized_path = normalize_directory_path(directory_path)
        values = TeamChatAttachmentPolicy.root_defaults().model_dump(mode="python")
        for candidate in directory_ancestors(normalized_path):
            override = resolved_state.chat_attachment_policies.get(candidate)
            if override is None:
                continue
            for field_name in (
                "operator_to_agent_enabled",
                "agent_to_operator_enabled",
                "agent_send_requires_approval",
                "max_attachment_bytes",
                "max_attachments_per_message",
                "allowed_mime_types",
                "allowed_extensions",
                "blocked_extensions",
            ):
                value = getattr(override, field_name)
                if value is not None:
                    values[field_name] = value
        values.update({"id": normalized_path, "directory_path": normalized_path})
        return TeamChatAttachmentPolicy.model_validate(values)

    def set_override(
        self,
        *,
        directory_path: str,
        values: Mapping[str, Any],
        updated_by: str | None,
        expected_revision: int,
    ) -> TeamState:
        normalized_path = normalize_directory_path(directory_path)
        allowed_fields = {
            "operator_to_agent_enabled",
            "agent_to_operator_enabled",
            "agent_send_requires_approval",
            "max_attachment_bytes",
            "max_attachments_per_message",
            "allowed_mime_types",
            "allowed_extensions",
            "blocked_extensions",
        }
        unknown = sorted(set(values) - allowed_fields)
        if unknown:
            raise ValueError(f"unknown Team Chat attachment policy fields: {', '.join(unknown)}")

        def mutate(state: TeamState) -> TeamState:
            payload = {
                "id": normalized_path,
                "directory_path": normalized_path,
                **{name: values.get(name) for name in allowed_fields},
                "updated_by": updated_by,
                "updated_at": utc_now(),
            }
            policy = TeamChatAttachmentPolicy.model_validate(payload)
            if normalized_path == "root":
                defaults = TeamChatAttachmentPolicy.root_defaults().model_dump(mode="python")
                for name in allowed_fields:
                    if payload[name] is None:
                        payload[name] = defaults[name]
                policy = TeamChatAttachmentPolicy.model_validate(payload)
            state.chat_attachment_policies[normalized_path] = policy
            return state

        return self.repository.update(mutate, expected_revision=expected_revision)


class TeamChatAttachmentStorage:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = addon_storage_path(runtime_dir, ADDON_ID, "attachments")
        self.assets_dir = self.root / "assets"
        self.staging_dir = self.root / "staging"

    def prepare_upload(
        self,
        *,
        content: bytes,
        filename: str,
        mime_type: str | None,
    ) -> PreparedChatAttachment:
        attachment_id = f"chatatt_{uuid4().hex}"
        safe_filename = normalize_attachment_filename(filename)
        resolved_mime = normalize_mime_type(mime_type) or mimetypes.guess_type(safe_filename)[0]
        digest = hashlib.sha256(content).hexdigest()
        self._atomic_write(self.asset_path(attachment_id), content)
        return PreparedChatAttachment(
            id=attachment_id,
            filename=safe_filename,
            mime_type=resolved_mime,
            byte_count=len(content),
            sha256=digest,
        )

    def stage_agent_file(
        self,
        *,
        session_id: str,
        source_path: str,
        content: bytes,
        filename: str,
        mime_type: str | None,
    ) -> StagedChatAttachment:
        self.cleanup_expired()
        staging_id = f"chatstage_{uuid4().hex}"
        safe_filename = normalize_attachment_filename(filename)
        resolved_mime = normalize_mime_type(mime_type) or mimetypes.guess_type(safe_filename)[0]
        created_at = utc_now()
        record = StagedChatAttachment(
            id=staging_id,
            filename=safe_filename,
            mime_type=resolved_mime,
            byte_count=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            session_id=str(session_id or "").strip(),
            source_path=str(source_path or "").strip(),
            expires_at=created_at + STAGING_TTL,
        )
        self._atomic_write(self.staging_blob_path(staging_id), content)
        self._atomic_write(
            self.staging_metadata_path(staging_id),
            json.dumps(record.payload(), sort_keys=True).encode("utf-8"),
        )
        return record

    def get_staged(self, staging_id: str, *, session_id: str) -> StagedChatAttachment:
        self.cleanup_expired()
        safe_id = validate_staging_id(staging_id)
        metadata_path = self.staging_metadata_path(safe_id)
        blob_path = self.staging_blob_path(safe_id)
        if not metadata_path.is_file() or not blob_path.is_file():
            raise FileNotFoundError(f"unknown staged Team Chat attachment: {safe_id}")
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        record = StagedChatAttachment(
            id=safe_id,
            filename=str(payload["filename"]),
            mime_type=payload.get("mime_type"),
            byte_count=int(payload["byte_count"]),
            sha256=str(payload["sha256"]),
            session_id=str(payload["session_id"]),
            source_path=str(payload["source_path"]),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
        )
        if record.session_id != str(session_id or "").strip():
            raise PermissionError("staged Team Chat attachment belongs to another session")
        content = blob_path.read_bytes()
        if (
            len(content) != record.byte_count
            or hashlib.sha256(content).hexdigest() != record.sha256
        ):
            raise ValueError("staged Team Chat attachment content no longer matches its metadata")
        return record

    def promote_staged(
        self,
        staging_ids: Iterable[str],
        *,
        session_id: str,
    ) -> list[PreparedChatAttachment]:
        prepared: list[PreparedChatAttachment] = []
        for staging_id in staging_ids:
            staged = self.get_staged(staging_id, session_id=session_id)
            attachment_id = f"chatatt_{staged.id.removeprefix('chatstage_')}"
            content = self.staging_blob_path(staged.id).read_bytes()
            self._atomic_write(self.asset_path(attachment_id), content)
            prepared.append(
                PreparedChatAttachment(
                    id=attachment_id,
                    filename=staged.filename,
                    mime_type=staged.mime_type,
                    byte_count=staged.byte_count,
                    sha256=staged.sha256,
                )
            )
        return prepared

    def delete_prepared(self, attachment_ids: Iterable[str]) -> None:
        for attachment_id in attachment_ids:
            try:
                self.asset_path(attachment_id).unlink()
            except FileNotFoundError:
                pass

    def delete_staged(self, staging_ids: Iterable[str]) -> None:
        for staging_id in staging_ids:
            safe_id = validate_staging_id(staging_id)
            self.staging_blob_path(safe_id).unlink(missing_ok=True)
            self.staging_metadata_path(safe_id).unlink(missing_ok=True)

    def asset_path(self, attachment_id: str) -> Path:
        safe_id = validate_attachment_id(attachment_id)
        return self.assets_dir / safe_id

    def staging_blob_path(self, staging_id: str) -> Path:
        safe_id = validate_staging_id(staging_id)
        return self.staging_dir / safe_id

    def staging_metadata_path(self, staging_id: str) -> Path:
        safe_id = validate_staging_id(staging_id)
        return self.staging_dir / f"{safe_id}.json"

    def cleanup_expired(self) -> int:
        if not self.staging_dir.is_dir():
            return 0
        removed = 0
        now = datetime.now(UTC)
        for metadata_path in self.staging_dir.glob("chatstage_*.json"):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(str(payload["expires_at"]))
            except Exception:
                continue
            if expires_at > now:
                continue
            staging_id = metadata_path.stem
            metadata_path.unlink(missing_ok=True)
            self.staging_blob_path(staging_id).unlink(missing_ok=True)
            removed += 1
        return removed

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp.write_bytes(content)
        os.replace(temp, path)


def validate_attachment_policy_content(
    policy: TeamChatAttachmentPolicy,
    *,
    direction: Literal["operator_to_agent", "agent_to_operator"],
    filename: str,
    mime_type: str | None,
    byte_count: int,
) -> str:
    enabled = (
        policy.operator_to_agent_enabled
        if direction == "operator_to_agent"
        else policy.agent_to_operator_enabled
    )
    if not enabled:
        raise PermissionError(f"Team Chat {direction.replace('_', '-')} attachments are disabled")
    safe_filename = normalize_attachment_filename(filename)
    if policy.max_attachment_bytes is not None and byte_count > policy.max_attachment_bytes:
        raise ValueError(
            f"attachment {safe_filename!r} exceeds Team Chat limit: "
            f"{byte_count} > {policy.max_attachment_bytes} bytes"
        )
    extension = PurePath(safe_filename).suffix.lower()
    blocked = set(policy.blocked_extensions or [])
    if extension and extension in blocked:
        raise ValueError(f"attachment extension {extension!r} is blocked by Team Chat policy")
    allowed_extensions = set(policy.allowed_extensions or [])
    if allowed_extensions and extension not in allowed_extensions:
        raise ValueError(f"attachment extension {extension or '<none>'!r} is not allowed")
    normalized_mime = normalize_mime_type(mime_type) or mimetypes.guess_type(safe_filename)[0]
    allowed_mime_types = set(policy.allowed_mime_types or [])
    if allowed_mime_types and normalized_mime not in allowed_mime_types:
        raise ValueError(f"attachment MIME type {normalized_mime or '<unknown>'!r} is not allowed")
    return safe_filename


def validate_attachment_count(policy: TeamChatAttachmentPolicy, count: int) -> None:
    limit = int(policy.max_attachments_per_message or 0)
    if limit and count > limit:
        raise ValueError(f"Team Chat allows at most {limit} attachments per message")


def normalize_directory_path(value: str | None) -> str:
    return DirectoryPath(segments=str(value or "root").strip() or "root").as_posix()


def directory_ancestors(value: str) -> list[str]:
    segments = DirectoryPath(segments=value).segments
    return ["/".join(segments[:index]) for index in range(1, len(segments) + 1)]


def normalize_attachment_filename(value: str) -> str:
    raw = PurePath(str(value or "").replace("\\", "/")).name.strip()
    normalized = _UNSAFE_FILENAME.sub("_", raw).strip(" .")
    if not normalized or normalized in {".", ".."}:
        normalized = "attachment"
    return normalized[:180]


def normalize_mime_type(value: str | None) -> str | None:
    normalized = str(value or "").split(";", 1)[0].strip().lower()
    return normalized or None


def validate_attachment_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ATTACHMENT_ID.fullmatch(normalized):
        raise ValueError("invalid Team Chat attachment id")
    return normalized


def validate_staging_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_STAGING_ID.fullmatch(normalized):
        raise ValueError("invalid staged Team Chat attachment id")
    return normalized


__all__ = [
    "PreparedChatAttachment",
    "StagedChatAttachment",
    "TeamChatAttachmentPolicyService",
    "TeamChatAttachmentStorage",
    "directory_ancestors",
    "normalize_attachment_filename",
    "normalize_directory_path",
    "normalize_mime_type",
    "validate_attachment_count",
    "validate_attachment_id",
    "validate_attachment_policy_content",
]
