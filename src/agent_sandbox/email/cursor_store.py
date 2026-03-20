from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Callable

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EmailSyncCursor(BaseModel):
    mailbox_id: str
    folder: str
    provider: str
    primitive_cursor: str | None = None
    cursor_version: int = 0
    updated_at: datetime = Field(default_factory=_utc_now)
    recent_message_ids: list[str] = Field(default_factory=list)


class DurableCursorStore:
    """Atomic JSON-backed cursor state keyed by mailbox_id + folder."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    @staticmethod
    def _key(mailbox_id: str, folder: str) -> str:
        return f"{mailbox_id}:{folder}"

    def get(self, mailbox_id: str, folder: str) -> EmailSyncCursor | None:
        with self._lock:
            payload = self._read_raw()
            raw_cursor = payload.get(self._key(mailbox_id, folder))
            if raw_cursor is None:
                return None
            return EmailSyncCursor.model_validate(raw_cursor)

    def update(
        self,
        mailbox_id: str,
        folder: str,
        updater: Callable[[EmailSyncCursor | None], EmailSyncCursor],
    ) -> EmailSyncCursor:
        with self._lock:
            payload = self._read_raw()
            key = self._key(mailbox_id, folder)
            current = payload.get(key)
            current_cursor = EmailSyncCursor.model_validate(current) if current is not None else None
            updated = updater(current_cursor)
            payload[key] = updated.model_dump(mode="json")
            self._atomic_write(payload)
            return updated

    def _read_raw(self) -> dict[str, object]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, object]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)
