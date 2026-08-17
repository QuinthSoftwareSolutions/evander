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


class TeamsInboxPollHealth(BaseModel):
    target_key: str
    consecutive_failures: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    last_error_class: str | None = None
    last_error_message: str | None = None
    last_failed_at: datetime | None = None
    last_success_at: datetime | None = None


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

    def __init__(self, path: Path) -> None:
        self.path = teams_inbox_db_path(Path(path))
        self._lock = RLock()
        self.ensure_schema()

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

    def get_poll_health(self, target_key: str) -> TeamsInboxPollHealth | None:
        normalized_key = str(target_key or "").strip()
        if not normalized_key:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM teams_inbox_poll_health WHERE target_key = ?",
                (normalized_key,),
            ).fetchone()
        if row is None:
            return None
        return TeamsInboxPollHealth.model_validate_json(row["payload_json"])

    def record_poll_failure(
        self,
        target_key: str,
        *,
        next_attempt_at: datetime,
        error_class: str,
        error_message: str,
        failed_at: datetime,
    ) -> TeamsInboxPollHealth:
        normalized_key = str(target_key or "").strip()
        if not normalized_key:
            raise ValueError("target_key is required")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM teams_inbox_poll_health WHERE target_key = ?",
                (normalized_key,),
            ).fetchone()
            previous = (
                TeamsInboxPollHealth.model_validate_json(row["payload_json"])
                if row is not None
                else None
            )
            state = TeamsInboxPollHealth(
                target_key=normalized_key,
                consecutive_failures=(previous.consecutive_failures if previous else 0) + 1,
                next_attempt_at=next_attempt_at,
                last_error_class=str(error_class or "").strip() or None,
                last_error_message=str(error_message or "").strip()[:1000] or None,
                last_failed_at=failed_at,
                last_success_at=previous.last_success_at if previous else None,
            )
            connection.execute(
                """
                INSERT INTO teams_inbox_poll_health(target_key, next_attempt_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    next_attempt_at = excluded.next_attempt_at,
                    payload_json = excluded.payload_json
                """,
                (
                    normalized_key,
                    self._datetime_to_storage(next_attempt_at),
                    _json_dumps(state.model_dump(mode="json")),
                ),
            )
        return state

    def record_poll_success(
        self,
        target_key: str,
        *,
        succeeded_at: datetime,
    ) -> TeamsInboxPollHealth:
        normalized_key = str(target_key or "").strip()
        if not normalized_key:
            raise ValueError("target_key is required")
        state = TeamsInboxPollHealth(
            target_key=normalized_key,
            consecutive_failures=0,
            last_success_at=succeeded_at,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO teams_inbox_poll_health(target_key, next_attempt_at, payload_json)
                VALUES (?, NULL, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    next_attempt_at = NULL,
                    payload_json = excluded.payload_json
                """,
                (normalized_key, _json_dumps(state.model_dump(mode="json"))),
            )
        return state

    def get_discovered_chat_targets(self, scope_key: str) -> set[str]:
        normalized_scope = str(scope_key or "").strip()
        if not normalized_scope:
            return set()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT channel_target
                FROM teams_inbox_discovered_chats
                WHERE scope_key = ?
                ORDER BY channel_target ASC
                """,
                (normalized_scope,),
            ).fetchall()
        return {str(row["channel_target"]) for row in rows}

    def replace_discovered_chat_targets(
        self,
        scope_key: str,
        channel_targets: set[str],
        *,
        discovered_at: datetime,
    ) -> None:
        normalized_scope = str(scope_key or "").strip()
        if not normalized_scope:
            raise ValueError("scope_key is required")
        normalized_targets = {
            str(target or "").strip().strip("/")
            for target in channel_targets
            if str(target or "").strip()
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM teams_inbox_discovered_chats WHERE scope_key = ?",
                (normalized_scope,),
            )
            connection.executemany(
                """
                INSERT INTO teams_inbox_discovered_chats(
                    scope_key, channel_target, discovered_at
                ) VALUES (?, ?, ?)
                """,
                [
                    (
                        normalized_scope,
                        target,
                        self._datetime_to_storage(discovered_at),
                    )
                    for target in sorted(normalized_targets)
                ],
            )

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
            health_rows = connection.execute(
                """
                SELECT target_key, payload_json
                FROM teams_inbox_poll_health
                ORDER BY target_key ASC
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
            "poll_health": {
                str(row["target_key"]): TeamsInboxPollHealth.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in health_rows
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
                CREATE TABLE IF NOT EXISTS teams_inbox_poll_health (
                    target_key TEXT PRIMARY KEY,
                    next_attempt_at TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_poll_health_next_attempt
                    ON teams_inbox_poll_health(next_attempt_at, target_key);
                CREATE TABLE IF NOT EXISTS teams_inbox_discovered_chats (
                    scope_key TEXT NOT NULL,
                    channel_target TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    PRIMARY KEY(scope_key, channel_target)
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
