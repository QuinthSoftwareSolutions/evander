"""Durable outbound Teams ownership and edit state, independent of the inbox cache."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import html
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TeamsImageVersion(BaseModel):
    """Host-issued immutable source; URLs are sensitive and never enter results/audits."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    artifact_id: str
    sha256: str
    mime_type: str = "image/png"
    expires_at: datetime
    renew_at: datetime
    source_url: str = Field(repr=False)
    alt_text: str = "Progress graphic"


class TeamsImageSlot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    # Exact acknowledged wrapper split around the img element. Only this slot
    # is mutable; the rest of the suffix remains protected.
    before: str
    after: str
    paragraph_prefix: str = "<p>"
    paragraph_suffix: str = "</p>"


class TeamsOutboundReceipt(BaseModel):
    """Write-once evidence from a successful host send; never inferred from ingestion."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    channel_target: str
    message_id: str
    session_id: str
    thread_id: str | None = None
    author: dict[str, Any]
    body_prefix: str = ""
    body_suffix: str = ""
    protected: dict[str, Any] = Field(default_factory=dict)
    editable: bool = True
    uneditable_reason: str | None = None
    web_url: str | None = None
    image_slot: TeamsImageSlot | None = None
    image_uneditable_reason: str | None = None


class TeamsPendingEdit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text_html: str
    body_html: str
    image: TeamsImageVersion | None = None


class TeamsOutboundEditState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: int = Field(default=1, ge=1)
    text_html: str
    body_html: str
    pending: TeamsPendingEdit | None = None
    image: TeamsImageVersion | None = None


def outbound_key(channel_target: str, message_id: str) -> str:
    # Length-safe and unambiguous even when either identifier contains a slash.
    return hashlib.sha256(json.dumps([channel_target, message_id]).encode()).hexdigest()


@contextmanager
def outbound_message_lock(directory: Path, channel_target: str, message_id: str) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (outbound_key(channel_target, message_id) + ".lock")
    with path.open("a+b") as stream:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Teams message is being edited; retry later") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class JsonTeamsOutboundStore:
    """A sibling, indexed receipt store for the legacy single-file inbox repository."""

    def __init__(self, inbox_path: Path) -> None:
        self.directory = inbox_path.with_name(inbox_path.name + ".outbound")

    def lock(self, channel_target: str, message_id: str):
        return outbound_message_lock(self.directory / "locks", channel_target, message_id)

    def _path(self, channel_target: str, message_id: str) -> Path:
        return self.directory / (outbound_key(channel_target, message_id) + ".json")

    def _read(self, channel_target: str, message_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(channel_target, message_id).read_text())
        except FileNotFoundError:
            return None

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x") as stream:
                os.chmod(temporary, 0o600)
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, receipt: TeamsOutboundReceipt, state: TeamsOutboundEditState) -> None:
        with self.lock(receipt.channel_target, receipt.message_id):
            if self._read(receipt.channel_target, receipt.message_id) is not None:
                raise ValueError("Teams outbound ownership receipt already exists")
            self._write(
                self._path(receipt.channel_target, receipt.message_id),
                {
                    "receipt": receipt.model_dump(mode="json"),
                    "state": state.model_dump(mode="json"),
                },
            )

    def receipt(self, channel_target: str, message_id: str) -> TeamsOutboundReceipt | None:
        payload = self._read(channel_target, message_id)
        return TeamsOutboundReceipt.model_validate(payload["receipt"]) if payload else None

    def state(self, channel_target: str, message_id: str) -> TeamsOutboundEditState | None:
        payload = self._read(channel_target, message_id)
        return TeamsOutboundEditState.model_validate(payload["state"]) if payload else None

    def save_state(
        self, channel_target: str, message_id: str, state: TeamsOutboundEditState
    ) -> None:
        # The command holds the per-message lock across reconciliation and provider I/O.
        payload = self._read(channel_target, message_id)
        if payload is None:
            raise ValueError("No Teams outbound ownership receipt")
        payload["state"] = state.model_dump(mode="json")
        self._write(self._path(channel_target, message_id), payload)


def compose_outbound_body(receipt, text_html, image):
    """Compose only receipt-authorized regions; old receipts retain their exact suffix."""
    slot = receipt.image_slot
    suffix = receipt.body_suffix
    if slot is not None:
        paragraph = ""
        if image is not None:
            paragraph = (
                slot.paragraph_prefix
                + '<img src="'
                + html.escape(image.source_url, quote=True)
                + '" alt="'
                + html.escape(image.alt_text, quote=True)
                + '">'
                + slot.paragraph_suffix
            )
        suffix = slot.before + paragraph + slot.after
    return receipt.body_prefix + text_html + suffix


def outbound_edit_result(receipt, state, status):
    image = state.image
    return {
        "success": True,
        "status": status,
        "session_id": receipt.session_id,
        "channel_target": receipt.channel_target,
        "message_id": receipt.message_id,
        "thread_id": receipt.thread_id,
        "message_revision": state.revision,
        "web_url": receipt.web_url,
        "image_sha256": image.sha256 if image else None,
        "image_expires_at": image.expires_at.isoformat() if image else None,
        "image_renew_at": image.renew_at.isoformat() if image else None,
    }
