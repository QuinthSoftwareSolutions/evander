from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from evander.outbound_identity import OutboundAvatar
from evander.storage import JsonStore


class DiscordWebhookCredential(BaseModel):
    key: str
    channel_id: str
    api_base_url: str
    bot_token_env: str
    webhook_id: str
    token: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_verified_at: datetime | None = None
    last_error: str | None = None

    @field_validator(
        "key",
        "channel_id",
        "api_base_url",
        "bot_token_env",
        "webhook_id",
        "token",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Discord webhook credential fields must not be empty")
        return text

    def safe_status(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "channel_id": self.channel_id,
            "api_base_url": self.api_base_url,
            "bot_token_env": self.bot_token_env,
            "webhook_id": self.webhook_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_verified_at": (
                self.last_verified_at.isoformat() if self.last_verified_at else None
            ),
            "last_error": self.last_error,
            "configured": True,
        }


def discord_webhook_credential_key(
    *, api_base_url: str, bot_token_env: str, channel_id: str
) -> str:
    material = "\n".join(
        (
            str(api_base_url).strip().rstrip("/"),
            str(bot_token_env).strip(),
            str(channel_id).strip(),
        )
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class DiscordWebhookCredentialStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._store = JsonStore(self.path, DiscordWebhookCredential, file_mode=0o600)
        self._provision_lock_path = self.path.with_name(f".{self.path.name}.provision.lock")

    def get(
        self, *, api_base_url: str, bot_token_env: str, channel_id: str
    ) -> DiscordWebhookCredential | None:
        key = discord_webhook_credential_key(
            api_base_url=api_base_url,
            bot_token_env=bot_token_env,
            channel_id=channel_id,
        )
        return self._store.get(key)

    def put(self, credential: DiscordWebhookCredential) -> None:
        self._store.put(credential.key, credential)

    def delete(self, credential: DiscordWebhookCredential) -> None:
        self._store.delete(credential.key)

    def list_safe(self) -> list[dict[str, Any]]:
        return [item.safe_status() for item in self._store.read_all().values()]

    @contextmanager
    def provision_lock(self):
        self._provision_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._provision_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


_AVATAR_SUFFIX_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class CommunicationPersonaAvatarStore:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = Path(runtime_dir) / "communication-persona-avatars"

    def put(self, avatar: OutboundAvatar) -> Path:
        suffix = _AVATAR_SUFFIX_BY_MIME[avatar.mime_type]
        path = self.root / f"{avatar.sha256}{suffix}"
        if path.is_file():
            return path
        self.root.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            return path
        with os.fdopen(fd, "wb") as handle:
            handle.write(avatar.content)
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def get(self, sha256: str) -> tuple[Path, str]:
        digest = validate_avatar_sha256(sha256)
        for mime_type, suffix in _AVATAR_SUFFIX_BY_MIME.items():
            path = self.root / f"{digest}{suffix}"
            if path.is_file():
                return path, mime_type
        raise FileNotFoundError("communication persona avatar not found")


def validate_avatar_sha256(value: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("invalid communication persona avatar digest")
    return digest
