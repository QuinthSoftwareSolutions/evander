from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from agent_sandbox.models import DiscordMessageEnvelope

_DISCORD_MESSAGE_RETENTION = timedelta(days=90)


@dataclass(frozen=True)
class DiscordInboxListResult:
    messages: list[DiscordMessageEnvelope]
    next_cursor: str | None


class DiscordInboxStore:
    """Atomic JSON-backed store for Discord messages, provider cursors, and session reads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"cursors": {}, "messages": {}, "session_reads": {}})

    def get_cursor(self, channel_id: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            cursor = payload.get("cursors", {}).get(channel_id)
            return str(cursor) if cursor is not None else None

    def set_cursor(self, channel_id: str, cursor: str | None) -> None:
        with self._lock:
            payload = self._read_raw()
            cursors = payload.setdefault("cursors", {})
            if cursor is None:
                cursors.pop(channel_id, None)
            else:
                cursors[channel_id] = cursor
            self._compact_messages(payload)
            self._atomic_write(payload)

    def add_message_if_new(self, envelope: DiscordMessageEnvelope) -> bool:
        with self._lock:
            payload = self._read_raw()
            stored_messages = payload.setdefault("messages", {})
            if envelope.message_id in stored_messages:
                return False
            stored_messages[envelope.message_id] = envelope.model_dump(mode="json")
            self._compact_messages(payload)
            self._atomic_write(payload)
            return True

    def get_session_cursor(self, session_id: str, channel_id: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            return self._get_session_cursor_from_payload(payload, session_id, channel_id)

    def advance_session_cursor(
        self, session_id: str, channel_id: str, cursor: str | None
    ) -> str | None:
        with self._lock:
            payload = self._read_raw()
            next_cursor = self._advance_session_cursor_in_payload(
                payload, session_id, channel_id, cursor
            )
            self._compact_messages(payload)
            self._atomic_write(payload)
            return next_cursor

    def has_unread_messages(self, *, session_id: str, channel_ids: list[str]) -> bool:
        with self._lock:
            payload = self._read_raw()
            message_items = payload.get("messages", {})
            if not isinstance(message_items, dict):
                return False
            for channel_id in channel_ids:
                session_cursor = self._get_session_cursor_from_payload(
                    payload, session_id, channel_id
                )
                if self._channel_has_unread_messages(
                    message_items=message_items,
                    channel_id=channel_id,
                    cursor=session_cursor,
                ):
                    return True
            return False

    def list_unread_messages(
        self,
        *,
        session_id: str,
        channel_ids: list[str],
        cursor: str | None = None,
        limit: int | None = None,
    ) -> DiscordInboxListResult:
        with self._lock:
            payload = self._read_raw()
            result = self._list_messages(
                payload,
                channel_ids=channel_ids,
                session_id=session_id,
                cursor=cursor,
                limit=limit,
            )
            for channel_id in {
                channel_id.strip() for channel_id in channel_ids if channel_id.strip()
            }:
                self._advance_session_cursor_in_payload(
                    payload,
                    session_id,
                    channel_id,
                    result.next_cursor if result.next_cursor is not None else cursor,
                )
            self._compact_messages(payload)
            self._atomic_write(payload)
            return result

    def list_all_messages(
        self,
        *,
        channel_ids: list[str],
        limit: int | None = None,
    ) -> DiscordInboxListResult:
        with self._lock:
            payload = self._read_raw()
            return self._list_messages(
                payload,
                channel_ids=channel_ids,
                session_id=None,
                cursor=None,
                limit=limit,
            )

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def _list_messages(
        self,
        payload: dict[str, Any],
        *,
        channel_ids: list[str],
        session_id: str | None,
        cursor: str | None,
        limit: int | None,
    ) -> DiscordInboxListResult:
        allowed_channel_ids = {
            channel_id.strip() for channel_id in channel_ids if channel_id.strip()
        }
        effective_cursors = {
            channel_id: self._effective_cursor(
                payload,
                session_id=session_id,
                channel_id=channel_id,
                explicit_cursor=cursor,
            )
            for channel_id in allowed_channel_ids
        }

        messages: list[DiscordMessageEnvelope] = []
        for raw_value in payload.get("messages", {}).values():
            if not isinstance(raw_value, dict):
                continue
            candidate = self._validate_message(raw_value)
            if candidate is None or candidate.channel_id not in allowed_channel_ids:
                continue
            effective_cursor = effective_cursors.get(candidate.channel_id)
            if effective_cursor and candidate.created_at <= self._parse_dt(effective_cursor):
                continue
            messages.append(candidate)

        messages.sort(key=lambda item: (item.created_at, item.message_id))
        if limit is not None:
            messages = messages[:limit]

        next_cursor = (
            messages[-1].created_at.isoformat().replace("+00:00", "Z")
            if messages
            else self._max_cursors(effective_cursors.values())
        )
        return DiscordInboxListResult(messages=messages, next_cursor=next_cursor)

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

        cutoff = datetime.now(timezone.utc) - _DISCORD_MESSAGE_RETENTION
        compacted: dict[str, Any] = {}
        changed = False
        for message_id, raw_value in messages.items():
            if not isinstance(raw_value, dict):
                compacted[message_id] = raw_value
                continue
            channel_id = str(raw_value.get("channel_id", "")).strip()
            if not channel_id:
                compacted[message_id] = raw_value
                continue
            created_at = self._parse_dt(raw_value.get("created_at"))
            channel_cursor = cursors.get(channel_id)
            if (
                created_at < cutoff
                and channel_cursor is not None
                and created_at <= self._parse_dt(channel_cursor)
            ):
                changed = True
                continue
            compacted[message_id] = raw_value
        if changed:
            payload["messages"] = compacted

    def _advance_session_cursor_in_payload(
        self,
        payload: dict[str, Any],
        session_id: str,
        channel_id: str,
        cursor: str | None,
    ) -> str | None:
        session_reads = payload.setdefault("session_reads", {})
        if not isinstance(session_reads, dict):
            session_reads = {}
            payload["session_reads"] = session_reads
        current_reads = session_reads.get(session_id)
        if not isinstance(current_reads, dict):
            current_reads = {}
            session_reads[session_id] = current_reads

        current_cursor = current_reads.get(channel_id)
        next_cursor = self._max_cursor(
            str(current_cursor) if current_cursor is not None else None,
            cursor,
        )
        if next_cursor is None:
            current_reads.pop(channel_id, None)
        else:
            current_reads[channel_id] = next_cursor
        if not current_reads:
            session_reads.pop(session_id, None)
        return next_cursor

    def _effective_cursor(
        self,
        payload: dict[str, Any],
        *,
        session_id: str | None,
        channel_id: str,
        explicit_cursor: str | None,
    ) -> str | None:
        if session_id is None:
            return explicit_cursor
        return self._max_cursor(
            self._get_session_cursor_from_payload(payload, session_id, channel_id),
            explicit_cursor,
        )

    @staticmethod
    def _get_session_cursor_from_payload(
        payload: dict[str, Any],
        session_id: str,
        channel_id: str,
    ) -> str | None:
        raw_session_reads = payload.get("session_reads", {})
        if not isinstance(raw_session_reads, dict):
            return None
        session_reads = raw_session_reads.get(session_id, {})
        if not isinstance(session_reads, dict):
            return None
        cursor = session_reads.get(channel_id)
        return str(cursor) if cursor is not None else None

    @staticmethod
    def _channel_has_unread_messages(
        *,
        message_items: dict[str, Any],
        channel_id: str,
        cursor: str | None,
    ) -> bool:
        cursor_dt = DiscordInboxStore._parse_dt(cursor) if cursor else None
        for raw_value in message_items.values():
            if not isinstance(raw_value, dict):
                continue
            if str(raw_value.get("channel_id")) != channel_id:
                continue
            created_at = DiscordInboxStore._parse_dt(raw_value.get("created_at"))
            if cursor_dt and created_at <= cursor_dt:
                continue
            return True
        return False

    @staticmethod
    def _validate_message(raw_value: dict[str, Any]) -> DiscordMessageEnvelope | None:
        try:
            return DiscordMessageEnvelope.model_validate(raw_value)
        except Exception:
            return None

    @staticmethod
    def _max_cursor(first: str | None, second: str | None) -> str | None:
        if first is None:
            return second
        if second is None:
            return first
        if DiscordInboxStore._parse_dt(first) >= DiscordInboxStore._parse_dt(second):
            return first
        return second

    @staticmethod
    def _max_cursors(cursors: Any) -> str | None:
        maximum: str | None = None
        for cursor in cursors:
            maximum = DiscordInboxStore._max_cursor(maximum, cursor)
        return maximum

    @staticmethod
    def _parse_dt(value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            return datetime.min.replace(tzinfo=timezone.utc)
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
