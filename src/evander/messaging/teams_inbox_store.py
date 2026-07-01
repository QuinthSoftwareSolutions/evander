from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field

_TEAMS_MESSAGE_RETENTION = timedelta(days=90)
_SQLITE_TEAMS_INBOX_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0


def teams_inbox_db_path(path: Path) -> Path:
    if path.suffix.lower() in _SQLITE_TEAMS_INBOX_SUFFIXES:
        return path
    return path.with_suffix(".db")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class TeamsInboxCursorState(BaseModel):
    cursor: str
    created_at: datetime | None = None


class TeamsInboxMessage(BaseModel):
    cursor: str
    message_id: str
    team_id: str = ""
    channel_id: str = ""
    chat_id: str | None = None
    thread_id: str | None = None
    author_id: str
    author_name: str = ""
    content: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamsInboxStore:
    """SQLite-backed store for Teams inbox messages and per-channel cursors."""

    def __init__(self, path: Path, *, legacy_json_path: Path | None = None) -> None:
        original_path = Path(path)
        self.path = teams_inbox_db_path(original_path)
        if legacy_json_path is None and self.path != original_path:
            legacy_json_path = original_path
        self.legacy_json_path = legacy_json_path
        self._lock = RLock()
        self.ensure_schema()
        self._import_legacy_json_if_empty()

    def get_cursor(self, channel_target: str) -> str | None:
        state = self.get_cursor_state(channel_target)
        return state.cursor if state is not None else None

    def get_cursor_state(self, channel_target: str) -> TeamsInboxCursorState | None:
        normalized_target = str(channel_target or "").strip().strip("/")
        if not normalized_target:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_cursors
                WHERE channel_target = ?
                """,
                (normalized_target,),
            ).fetchone()
        if row is None:
            return None
        return TeamsInboxCursorState.model_validate_json(row["payload_json"])

    def set_cursor(
        self,
        channel_target: str,
        cursor: str | None,
        *,
        created_at: datetime | None = None,
    ) -> None:
        normalized_target = str(channel_target or "").strip().strip("/")
        if not normalized_target:
            return
        with self._lock, self._connect() as connection:
            if cursor is None:
                connection.execute(
                    "DELETE FROM teams_inbox_cursors WHERE channel_target = ?",
                    (normalized_target,),
                )
            else:
                state = TeamsInboxCursorState(cursor=cursor, created_at=created_at)
                connection.execute(
                    """
                    INSERT INTO teams_inbox_cursors(
                        channel_target,
                        cursor,
                        created_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(channel_target) DO UPDATE SET
                        cursor = excluded.cursor,
                        created_at = excluded.created_at,
                        payload_json = excluded.payload_json
                    """,
                    self._cursor_row(normalized_target, state),
                )
            self._compact_messages(connection)

    def add_message_if_new(self, message: TeamsInboxMessage) -> bool:
        validated = TeamsInboxMessage.model_validate(message)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO teams_inbox_messages(
                    cursor,
                    message_id,
                    channel_target,
                    team_id,
                    channel_id,
                    chat_id,
                    thread_id,
                    author_id,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._message_row(validated),
            )
            if cursor.rowcount == 0:
                return False
            self._compact_messages(connection)
            return True

    def get_message(self, cursor: str) -> TeamsInboxMessage | None:
        normalized_cursor = str(cursor or "").strip()
        if not normalized_cursor:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_messages
                WHERE cursor = ?
                """,
                (normalized_cursor,),
            ).fetchone()
        if row is None:
            return None
        return TeamsInboxMessage.model_validate_json(row["payload_json"])

    def list_all_messages(
        self,
        *,
        channel_targets: list[str] | None = None,
        created_at_from: datetime | None = None,
        direct_messages_only: bool = False,
        limit: int | None = None,
    ) -> list[TeamsInboxMessage]:
        allowed_targets = None
        if channel_targets is not None:
            allowed_targets = {
                target.strip().strip("/") for target in channel_targets if target.strip()
            }
            if not allowed_targets:
                return []

        where: list[str] = []
        params: list[Any] = []
        if allowed_targets is not None:
            placeholders = ", ".join("?" for _ in allowed_targets)
            where.append(f"channel_target IN ({placeholders})")
            params.extend(sorted(allowed_targets))
        if created_at_from is not None:
            where.append("created_at >= ?")
            params.append(self._datetime_to_storage(created_at_from))
        if direct_messages_only:
            where.append("chat_id IS NOT NULL AND chat_id != ''")

        sql = "SELECT payload_json FROM teams_inbox_messages"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC, cursor ASC"
        if limit is not None:
            if limit <= 0:
                return []
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [TeamsInboxMessage.model_validate_json(row["payload_json"]) for row in rows]

    def read_raw(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            cursor_rows = connection.execute(
                """
                SELECT channel_target, payload_json
                FROM teams_inbox_cursors
                ORDER BY channel_target ASC
                """
            ).fetchall()
            message_rows = connection.execute(
                """
                SELECT cursor, payload_json
                FROM teams_inbox_messages
                ORDER BY created_at ASC, cursor ASC
                """
            ).fetchall()
        return {
            "cursors": {
                str(row["channel_target"]): TeamsInboxCursorState.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in cursor_rows
            },
            "messages": {
                str(row["cursor"]): TeamsInboxMessage.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in message_rows
            },
        }

    def ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS teams_inbox_messages (
                    cursor TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    channel_target TEXT NOT NULL,
                    team_id TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    chat_id TEXT,
                    thread_id TEXT,
                    author_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_messages_target_created
                    ON teams_inbox_messages(channel_target, created_at, cursor);
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_messages_created
                    ON teams_inbox_messages(created_at, cursor);
                CREATE TABLE IF NOT EXISTS teams_inbox_cursors (
                    channel_target TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    created_at TEXT,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        self._enable_wal(connection)
        return connection

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + _SQLITE_WAL_ENABLE_TIMEOUT_SECONDS
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _import_legacy_json_if_empty(self) -> None:
        legacy_json_path = self.legacy_json_path
        if (
            legacy_json_path is None
            or not legacy_json_path.exists()
            or legacy_json_path.suffix.lower() in _SQLITE_TEAMS_INBOX_SUFFIXES
        ):
            return
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM teams_inbox_messages) AS message_count,
                    (SELECT COUNT(*) FROM teams_inbox_cursors) AS cursor_count
                """
            ).fetchone()
            if row is not None and (
                int(row["message_count"] or 0) > 0 or int(row["cursor_count"] or 0) > 0
            ):
                return
            payload = self._read_legacy_payload(legacy_json_path)
            raw_cursors = payload.get("cursors", {})
            if isinstance(raw_cursors, dict):
                for channel_target, raw_state in raw_cursors.items():
                    state = self._validate_cursor_state(raw_state)
                    normalized_target = str(channel_target or "").strip().strip("/")
                    if state is None or not normalized_target:
                        continue
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO teams_inbox_cursors(
                            channel_target,
                            cursor,
                            created_at,
                            payload_json
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        self._cursor_row(normalized_target, state),
                    )
            raw_messages = payload.get("messages", {})
            if isinstance(raw_messages, dict):
                for raw_value in raw_messages.values():
                    message = self._validate_message(raw_value)
                    if message is None:
                        continue
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO teams_inbox_messages(
                            cursor,
                            message_id,
                            channel_target,
                            team_id,
                            channel_id,
                            chat_id,
                            thread_id,
                            author_id,
                            created_at,
                            payload_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self._message_row(message),
                    )
            self._compact_messages(connection)

    @staticmethod
    def _read_legacy_payload(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {"cursors": {}, "messages": {}}
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError(f"Teams inbox JSON store at {path} must contain an object")
        return raw

    def _compact_messages(self, connection: sqlite3.Connection) -> None:
        cutoff = self._datetime_to_storage(datetime.now(timezone.utc) - _TEAMS_MESSAGE_RETENTION)
        connection.execute(
            """
            DELETE FROM teams_inbox_messages
            WHERE created_at < ?
              AND EXISTS (
                  SELECT 1
                  FROM teams_inbox_cursors AS cursors
                  WHERE cursors.channel_target = teams_inbox_messages.channel_target
                    AND cursors.created_at IS NOT NULL
                    AND cursors.created_at >= ?
                    AND (
                        teams_inbox_messages.created_at < cursors.created_at
                        OR (
                            teams_inbox_messages.created_at = cursors.created_at
                            AND teams_inbox_messages.cursor <= cursors.cursor
                        )
                    )
              )
            """,
            (cutoff, cutoff),
        )

    def _message_row(
        self, message: TeamsInboxMessage
    ) -> tuple[str, str, str, str, str, str | None, str | None, str, str, str]:
        return (
            message.cursor,
            message.message_id,
            self._channel_target(message),
            message.team_id,
            message.channel_id,
            message.chat_id,
            message.thread_id,
            message.author_id,
            self._datetime_to_storage(message.created_at),
            _json_dumps(message.model_dump(mode="json")),
        )

    def _cursor_row(
        self, channel_target: str, state: TeamsInboxCursorState
    ) -> tuple[str, str, str | None, str]:
        return (
            channel_target,
            state.cursor,
            self._datetime_to_storage(state.created_at) if state.created_at is not None else None,
            _json_dumps(state.model_dump(mode="json")),
        )

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
        if message.chat_id:
            return f"chat:{message.chat_id}"
        return f"{message.team_id}/{message.channel_id}"

    @staticmethod
    def _message_sort_key(message: TeamsInboxMessage) -> tuple[datetime, str]:
        return (message.created_at, message.cursor)

    @staticmethod
    def _cursor_state_sort_key(state: TeamsInboxCursorState) -> tuple[datetime, str]:
        if state.created_at is None:
            return (datetime.min.replace(tzinfo=timezone.utc), state.cursor)
        return (state.created_at, state.cursor)

    @staticmethod
    def _datetime_to_storage(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (
            value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        )
