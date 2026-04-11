from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

_TEAMS_MESSAGE_RETENTION = timedelta(days=90)


class TeamsInboxCursorState(BaseModel):
    cursor: str
    created_at: datetime | None = None


class TeamsInboxMessage(BaseModel):
    cursor: str
    message_id: str
    team_id: str
    channel_id: str
    thread_id: str | None = None
    author_id: str
    author_name: str = ""
    content: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamsInboxStore:
    """Atomic JSON-backed store for Teams inbox messages and per-channel cursors."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"cursors": {}, "messages": {}})

    def get_cursor(self, channel_target: str) -> str | None:
        state = self.get_cursor_state(channel_target)
        return state.cursor if state is not None else None

    def get_cursor_state(self, channel_target: str) -> TeamsInboxCursorState | None:
        with self._lock:
            payload = self._read_raw()
            raw_state = payload.get("cursors", {}).get(channel_target)
            return self._validate_cursor_state(raw_state)

    def set_cursor(
        self,
        channel_target: str,
        cursor: str | None,
        *,
        created_at: datetime | None = None,
    ) -> None:
        with self._lock:
            payload = self._read_raw()
            cursors = payload.setdefault("cursors", {})
            if cursor is None:
                cursors.pop(channel_target, None)
            else:
                state = TeamsInboxCursorState(cursor=cursor, created_at=created_at)
                cursors[channel_target] = state.model_dump(mode="json")
            self._compact_messages(payload)
            self._atomic_write(payload)

    def add_message_if_new(self, message: TeamsInboxMessage) -> bool:
        with self._lock:
            payload = self._read_raw()
            stored_messages = payload.setdefault("messages", {})
            if message.cursor in stored_messages:
                return False
            stored_messages[message.cursor] = message.model_dump(mode="json")
            self._compact_messages(payload)
            self._atomic_write(payload)
            return True

    def get_message(self, cursor: str) -> TeamsInboxMessage | None:
        with self._lock:
            payload = self._read_raw()
            raw_message = payload.get("messages", {}).get(cursor)
            return self._validate_message(raw_message)

    def list_all_messages(
        self,
        *,
        channel_targets: list[str] | None = None,
        limit: int | None = None,
    ) -> list[TeamsInboxMessage]:
        with self._lock:
            payload = self._read_raw()
            allowed_targets = None
            if channel_targets is not None:
                allowed_targets = {
                    target.strip().strip("/")
                    for target in channel_targets
                    if target.strip()
                }
            messages: list[TeamsInboxMessage] = []
            for raw_value in payload.get("messages", {}).values():
                candidate = self._validate_message(raw_value)
                if candidate is None:
                    continue
                if allowed_targets is not None and self._channel_target(candidate) not in allowed_targets:
                    continue
                messages.append(candidate)
            messages.sort(key=self._message_sort_key)
            if limit is not None:
                return messages[:limit]
            return messages

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def _compact_messages(self, payload: dict[str, Any]) -> None:
        messages = payload.get("messages")
        cursors = payload.get("cursors")
        if not isinstance(messages, dict) or not isinstance(cursors, dict):
            return

        cutoff = datetime.now(timezone.utc) - _TEAMS_MESSAGE_RETENTION
        compacted: dict[str, Any] = {}
        changed = False
        for cursor, raw_value in messages.items():
            message = self._validate_message(raw_value)
            if message is None:
                compacted[cursor] = raw_value
                continue
            channel_target = self._channel_target(message)
            cursor_state = self._validate_cursor_state(cursors.get(channel_target))
            if (
                cursor_state is not None
                and cursor_state.created_at is not None
                and message.created_at < cutoff
                and self._message_sort_key(message)
                <= self._cursor_state_sort_key(cursor_state)
            ):
                changed = True
                continue
            compacted[cursor] = raw_value
        if changed:
            payload["messages"] = compacted

    @staticmethod
    def _validate_cursor_state(raw_value: object) -> TeamsInboxCursorState | None:
        if not isinstance(raw_value, dict):
            return None
        return TeamsInboxCursorState.model_validate(raw_value)

    @staticmethod
    def _validate_message(raw_value: object) -> TeamsInboxMessage | None:
        if not isinstance(raw_value, dict):
            return None
        return TeamsInboxMessage.model_validate(raw_value)

    @staticmethod
    def _channel_target(message: TeamsInboxMessage) -> str:
        return f"{message.team_id}/{message.channel_id}"

    @staticmethod
    def _message_sort_key(message: TeamsInboxMessage) -> tuple[datetime, str]:
        return (message.created_at, message.cursor)

    @staticmethod
    def _cursor_state_sort_key(state: TeamsInboxCursorState) -> tuple[datetime, str]:
        if state.created_at is None:
            return (datetime.min.replace(tzinfo=timezone.utc), state.cursor)
        return (state.created_at, state.cursor)
