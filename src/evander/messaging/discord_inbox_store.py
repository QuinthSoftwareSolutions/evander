from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Sequence

from evander.models import DiscordMessageEnvelope

_DISCORD_MESSAGE_RETENTION = timedelta(days=90)
_SQLITE_DISCORD_INBOX_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0


def discord_inbox_db_path(path: Path) -> Path:
    if path.suffix.lower() in _SQLITE_DISCORD_INBOX_SUFFIXES:
        return path
    return path.with_suffix(".db")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class DiscordInboxListResult:
    messages: list[DiscordMessageEnvelope]
    next_cursor: str | None


@dataclass(frozen=True)
class DiscordThreadBinding:
    thread_id: str
    parent_channel_id: str
    guild_id: str
    thread_type: int
    archived: bool
    locked: bool
    discovered_at: datetime
    updated_at: datetime


class DiscordInboxStore:
    """SQLite-backed store for Discord messages, provider cursors, and session reads."""

    def __init__(self, path: Path) -> None:
        self.path = discord_inbox_db_path(Path(path))
        self._lock = RLock()
        self.ensure_schema()

    def get_cursor(self, channel_id: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT cursor
                FROM discord_inbox_cursors
                WHERE channel_id = ?
                """,
                (channel_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["cursor"])

    def set_cursor(self, channel_id: str, cursor: str | None) -> None:
        with self._lock, self._connect() as connection:
            if cursor is None:
                connection.execute(
                    "DELETE FROM discord_inbox_cursors WHERE channel_id = ?",
                    (channel_id,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO discord_inbox_cursors(channel_id, cursor)
                    VALUES (?, ?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        cursor = excluded.cursor
                    """,
                    (channel_id, cursor),
                )
            self._compact_messages(connection)

    def add_messages_if_new(self, envelopes: Sequence[DiscordMessageEnvelope]) -> int:
        validated = [DiscordMessageEnvelope.model_validate(envelope) for envelope in envelopes]
        if not validated:
            return 0

        with self._lock, self._connect() as connection:
            before_changes = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO discord_inbox_messages(
                    message_id,
                    channel_id,
                    author_id,
                    author_name,
                    created_at,
                    in_reply_to,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self._message_row(message) for message in validated),
            )
            inserted_count = connection.total_changes - before_changes
            if inserted_count > 0:
                self._compact_messages(connection)
            return inserted_count

    def add_message_if_new(self, envelope: DiscordMessageEnvelope) -> bool:
        return self.add_messages_if_new([envelope]) > 0

    def get_message(self, message_id: str) -> DiscordMessageEnvelope | None:
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM discord_inbox_messages
                WHERE message_id = ?
                """,
                (normalized_message_id,),
            ).fetchone()
        if row is None:
            return None
        return DiscordMessageEnvelope.model_validate_json(row["payload_json"])

    def register_thread_parent(self, parent_channel_id: str, *, observed_at: datetime) -> bool:
        normalized_parent = str(parent_channel_id or "").strip()
        if not normalized_parent:
            raise ValueError("parent_channel_id is required")
        timestamp = self._datetime_to_storage(observed_at)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM discord_thread_parents WHERE parent_channel_id = ?",
                (normalized_parent,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO discord_thread_parents(parent_channel_id, initialized_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(parent_channel_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (normalized_parent, timestamp, timestamp),
            )
        return existing is None

    def upsert_thread_binding(
        self,
        *,
        thread_id: str,
        parent_channel_id: str,
        guild_id: str,
        thread_type: int,
        archived: bool,
        locked: bool,
        observed_at: datetime,
    ) -> bool:
        normalized_thread = str(thread_id or "").strip()
        normalized_parent = str(parent_channel_id or "").strip()
        normalized_guild = str(guild_id or "").strip()
        if not normalized_thread or not normalized_parent or not normalized_guild:
            raise ValueError("thread_id, parent_channel_id, and guild_id are required")
        timestamp = self._datetime_to_storage(observed_at)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM discord_thread_bindings WHERE thread_id = ?",
                (normalized_thread,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO discord_thread_bindings(
                    thread_id, parent_channel_id, guild_id, thread_type,
                    archived, locked, discovered_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    parent_channel_id = excluded.parent_channel_id,
                    guild_id = excluded.guild_id,
                    thread_type = excluded.thread_type,
                    archived = excluded.archived,
                    locked = excluded.locked,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_thread,
                    normalized_parent,
                    normalized_guild,
                    int(thread_type),
                    int(bool(archived)),
                    int(bool(locked)),
                    timestamp,
                    timestamp,
                ),
            )
        return existing is None

    def get_thread_binding(self, thread_id: str) -> DiscordThreadBinding | None:
        normalized_thread = str(thread_id or "").strip()
        if not normalized_thread:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT thread_id, parent_channel_id, guild_id, thread_type,
                       archived, locked, discovered_at, updated_at
                FROM discord_thread_bindings
                WHERE thread_id = ?
                """,
                (normalized_thread,),
            ).fetchone()
        return self._thread_binding_from_row(row) if row is not None else None

    def list_thread_bindings(
        self,
        *,
        parent_channel_ids: Sequence[str],
        include_archived: bool = True,
    ) -> list[DiscordThreadBinding]:
        normalized_parents = sorted(
            {str(value or "").strip() for value in parent_channel_ids if str(value or "").strip()}
        )
        if not normalized_parents:
            return []
        placeholders = ", ".join("?" for _ in normalized_parents)
        archived_clause = "" if include_archived else " AND archived = 0"
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT thread_id, parent_channel_id, guild_id, thread_type,
                       archived, locked, discovered_at, updated_at
                FROM discord_thread_bindings
                WHERE parent_channel_id IN ({placeholders}){archived_clause}
                ORDER BY parent_channel_id, thread_id
                """,
                normalized_parents,
            ).fetchall()
        return [self._thread_binding_from_row(row) for row in rows]

    def mark_missing_parent_threads_archived(
        self,
        *,
        parent_channel_id: str,
        active_thread_ids: Sequence[str],
        observed_at: datetime,
    ) -> None:
        normalized_parent = str(parent_channel_id or "").strip()
        active_ids = {
            str(value or "").strip() for value in active_thread_ids if str(value or "").strip()
        }
        timestamp = self._datetime_to_storage(observed_at)
        with self._lock, self._connect() as connection:
            if active_ids:
                placeholders = ", ".join("?" for _ in active_ids)
                connection.execute(
                    f"""
                    UPDATE discord_thread_bindings
                    SET archived = 1, updated_at = ?
                    WHERE parent_channel_id = ? AND thread_id NOT IN ({placeholders})
                    """,
                    (timestamp, normalized_parent, *sorted(active_ids)),
                )
            else:
                connection.execute(
                    """
                    UPDATE discord_thread_bindings
                    SET archived = 1, updated_at = ?
                    WHERE parent_channel_id = ?
                    """,
                    (timestamp, normalized_parent),
                )

    def get_session_cursor(self, session_id: str, channel_id: str) -> str | None:
        with self._lock, self._connect() as connection:
            return self._get_session_cursor_from_connection(connection, session_id, channel_id)

    def advance_session_cursor(
        self, session_id: str, channel_id: str, cursor: str | None
    ) -> str | None:
        with self._lock, self._connect() as connection:
            next_cursor = self._advance_session_cursor_in_connection(
                connection, session_id, channel_id, cursor
            )
            self._compact_messages(connection)
            return next_cursor

    def fast_forward_session_cursors_to_latest(
        self, session_id: str, channel_ids: Sequence[str]
    ) -> dict[str, str]:
        normalized_channel_ids = {
            str(channel_id or "").strip()
            for channel_id in channel_ids
            if str(channel_id or "").strip()
        }
        if not normalized_channel_ids:
            return {}

        seed_cursor = self._datetime_to_storage(datetime.now(timezone.utc))
        advanced: dict[str, str] = {}
        with self._lock, self._connect() as connection:
            for channel_id in sorted(normalized_channel_ids):
                message_row = connection.execute(
                    """
                    SELECT MAX(created_at) AS cursor
                    FROM discord_inbox_messages
                    WHERE channel_id = ?
                    """,
                    (channel_id,),
                ).fetchone()
                message_cursor = (
                    str(message_row["cursor"])
                    if message_row is not None and message_row["cursor"]
                    else None
                )
                channel_row = connection.execute(
                    """
                    SELECT cursor
                    FROM discord_inbox_cursors
                    WHERE channel_id = ?
                    """,
                    (channel_id,),
                ).fetchone()
                channel_cursor = (
                    str(channel_row["cursor"])
                    if channel_row is not None and channel_row["cursor"]
                    else None
                )
                latest_cursor = self._max_cursors((message_cursor, channel_cursor, seed_cursor))
                next_cursor = self._advance_session_cursor_in_connection(
                    connection,
                    session_id,
                    channel_id,
                    latest_cursor,
                )
                if next_cursor is not None:
                    advanced[channel_id] = next_cursor
            self._compact_messages(connection)
        return advanced

    def has_unread_messages(self, *, session_id: str, channel_ids: list[str]) -> bool:
        with self._lock, self._connect() as connection:
            for channel_id in channel_ids:
                session_cursor = self._get_session_cursor_from_connection(
                    connection, session_id, channel_id
                )
                if self._channel_has_unread_messages(
                    connection=connection,
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
        allowed_channel_ids = {
            channel_id.strip() for channel_id in channel_ids if channel_id.strip()
        }
        if not allowed_channel_ids:
            return DiscordInboxListResult(messages=[], next_cursor=None)

        with self._lock, self._connect() as connection:
            effective_cursors = {
                channel_id: self._effective_cursor(
                    connection,
                    session_id=session_id,
                    channel_id=channel_id,
                    explicit_cursor=cursor,
                )
                for channel_id in allowed_channel_ids
            }
            normalized_cursors = {
                channel_id: (
                    self._storage_cursor(effective_cursor) if effective_cursor is not None else None
                )
                for channel_id, effective_cursor in effective_cursors.items()
            }
            messages = self._select_messages(
                connection,
                allowed_channel_ids=allowed_channel_ids,
            )

            filtered: list[DiscordMessageEnvelope] = []
            for candidate in messages:
                effective_cursor = normalized_cursors.get(candidate.channel_id)
                if effective_cursor and candidate.created_at <= self._parse_dt(effective_cursor):
                    continue
                filtered.append(candidate)

            if limit is not None:
                filtered = filtered[:limit]

            next_cursor = (
                filtered[-1].created_at.isoformat().replace("+00:00", "Z")
                if filtered
                else self._max_cursors(effective_cursors.values())
            )

            for channel_id in allowed_channel_ids:
                self._advance_session_cursor_in_connection(
                    connection,
                    session_id,
                    channel_id,
                    next_cursor if next_cursor is not None else cursor,
                )
            self._compact_messages(connection)
            return DiscordInboxListResult(messages=filtered, next_cursor=next_cursor)

    def list_all_messages(
        self,
        *,
        channel_ids: list[str],
        limit: int | None = None,
    ) -> DiscordInboxListResult:
        allowed_channel_ids = {
            channel_id.strip() for channel_id in channel_ids if channel_id.strip()
        }
        if not allowed_channel_ids:
            return DiscordInboxListResult(messages=[], next_cursor=None)

        with self._lock, self._connect() as connection:
            messages = self._select_messages(
                connection,
                allowed_channel_ids=allowed_channel_ids,
            )
        if limit is not None:
            messages = messages[:limit]
        next_cursor = (
            messages[-1].created_at.isoformat().replace("+00:00", "Z") if messages else None
        )
        return DiscordInboxListResult(messages=messages, next_cursor=next_cursor)

    def list_trigger_messages(
        self,
        *,
        channel_ids: list[str],
        author_ids: list[str],
        created_at: datetime | None,
        acknowledged_ids: list[str],
        limit: int = 200,
    ) -> list[DiscordMessageEnvelope]:
        """Read a bounded trigger page, including unacknowledged timestamp ties."""
        clauses, params = [], []
        for column, values in (("channel_id", channel_ids), ("author_id", author_ids)):
            if values:
                clauses.append(f"{column} IN (SELECT value FROM json_each(?))")
                params.append(_json_dumps(values))
        if created_at is not None:
            cursor = self._datetime_to_storage(created_at)
            clauses.append("created_at >= ?")
            params.append(cursor)
            if acknowledged_ids:
                clauses.append(
                    "(created_at > ? OR message_id NOT IN (SELECT value FROM json_each(?)))"
                )
                params.extend((cursor, _json_dumps(acknowledged_ids)))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        messages: list[DiscordMessageEnvelope] = []
        page_limit = max(1, min(limit, 512))
        after: tuple[str, str] | None = None
        with self._lock, self._connect() as connection:
            while len(messages) < page_limit:
                page_where, page_params = where, list(params)
                if after is not None:
                    page_where += (
                        " AND " if page_where else " WHERE "
                    ) + "(created_at,message_id) > (?,?)"
                    page_params.extend(after)
                rows = connection.execute(
                    "SELECT created_at,message_id,payload_json FROM discord_inbox_messages"
                    + page_where
                    + " ORDER BY created_at ASC, message_id ASC LIMIT ?",
                    (*page_params, page_limit - len(messages)),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    if (message := self._validate_message(row["payload_json"])) is not None:
                        messages.append(message)
                after = (rows[-1]["created_at"], rows[-1]["message_id"])
        return messages

    def read_raw(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            cursor_rows = connection.execute(
                """
                SELECT channel_id, cursor
                FROM discord_inbox_cursors
                ORDER BY channel_id ASC
                """
            ).fetchall()
            message_rows = connection.execute(
                """
                SELECT message_id, payload_json
                FROM discord_inbox_messages
                ORDER BY created_at ASC, message_id ASC
                """
            ).fetchall()
            session_rows = connection.execute(
                """
                SELECT session_id, channel_id, cursor
                FROM discord_inbox_session_reads
                ORDER BY session_id ASC, channel_id ASC
                """
            ).fetchall()

        return {
            "cursors": {str(row["channel_id"]): str(row["cursor"]) for row in cursor_rows},
            "messages": {
                str(row["message_id"]): DiscordMessageEnvelope.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in message_rows
            },
            "session_reads": self._session_reads_payload(session_rows),
        }

    def ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS discord_inbox_messages (
                    message_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    author_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    in_reply_to TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_discord_inbox_messages_channel_created_message
                    ON discord_inbox_messages(channel_id, created_at, message_id);
                CREATE INDEX IF NOT EXISTS idx_discord_inbox_messages_created_message
                    ON discord_inbox_messages(created_at, message_id);
                CREATE TABLE IF NOT EXISTS discord_inbox_cursors (
                    channel_id TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_inbox_session_reads (
                    session_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    cursor TEXT NOT NULL,
                    PRIMARY KEY(session_id, channel_id)
                );
                CREATE INDEX IF NOT EXISTS idx_discord_inbox_session_reads_session_channel
                    ON discord_inbox_session_reads(session_id, channel_id);
                CREATE TABLE IF NOT EXISTS discord_thread_parents (
                    parent_channel_id TEXT PRIMARY KEY,
                    initialized_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_thread_bindings (
                    thread_id TEXT PRIMARY KEY,
                    parent_channel_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    thread_type INTEGER NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    locked INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_discord_thread_bindings_parent
                    ON discord_thread_bindings(parent_channel_id, archived, thread_id);
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
        cutoff = self._datetime_to_storage(datetime.now(timezone.utc) - _DISCORD_MESSAGE_RETENTION)
        cursor_rows = connection.execute(
            """
            SELECT channel_id, cursor
            FROM discord_inbox_cursors
            """
        ).fetchall()
        cursor_by_channel = {str(row["channel_id"]): str(row["cursor"]) for row in cursor_rows}
        message_rows = connection.execute(
            """
            SELECT message_id, channel_id, created_at
            FROM discord_inbox_messages
            WHERE created_at < ?
            """,
            (cutoff,),
        ).fetchall()

        expired_message_ids: list[str] = []
        for row in message_rows:
            channel_id = str(row["channel_id"])
            channel_cursor = cursor_by_channel.get(channel_id)
            if channel_cursor is None:
                continue
            if self._parse_dt(row["created_at"]) <= self._parse_dt(channel_cursor):
                expired_message_ids.append(str(row["message_id"]))

        if expired_message_ids:
            connection.executemany(
                "DELETE FROM discord_inbox_messages WHERE message_id = ?",
                ((message_id,) for message_id in expired_message_ids),
            )

    def _select_messages(
        self,
        connection: sqlite3.Connection,
        *,
        allowed_channel_ids: set[str],
    ) -> list[DiscordMessageEnvelope]:
        placeholders = ", ".join("?" for _ in allowed_channel_ids)
        rows = connection.execute(
            f"""
            SELECT payload_json
            FROM discord_inbox_messages
            WHERE channel_id IN ({placeholders})
            ORDER BY created_at ASC, message_id ASC
            """,
            sorted(allowed_channel_ids),
        ).fetchall()
        messages: list[DiscordMessageEnvelope] = []
        for row in rows:
            candidate = self._validate_message(row["payload_json"])
            if candidate is None:
                continue
            messages.append(candidate)
        return messages

    def _channel_has_unread_messages(
        self,
        *,
        connection: sqlite3.Connection,
        channel_id: str,
        cursor: str | None,
    ) -> bool:
        if cursor is None:
            row = connection.execute(
                """
                SELECT 1
                FROM discord_inbox_messages
                WHERE channel_id = ?
                LIMIT 1
                """,
                (channel_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT 1
                FROM discord_inbox_messages
                WHERE channel_id = ?
                  AND created_at > ?
                LIMIT 1
                """,
                (channel_id, self._storage_cursor(cursor)),
            ).fetchone()
        return row is not None

    def _advance_session_cursor_in_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        channel_id: str,
        cursor: str | None,
    ) -> str | None:
        current_cursor = self._get_session_cursor_from_connection(
            connection, session_id, channel_id
        )
        next_cursor = self._max_cursor(current_cursor, cursor)
        if next_cursor is None:
            connection.execute(
                """
                DELETE FROM discord_inbox_session_reads
                WHERE session_id = ? AND channel_id = ?
                """,
                (session_id, channel_id),
            )
            return None

        connection.execute(
            """
            INSERT INTO discord_inbox_session_reads(session_id, channel_id, cursor)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id, channel_id) DO UPDATE SET
                cursor = excluded.cursor
            """,
            (session_id, channel_id, next_cursor),
        )
        return next_cursor

    def _effective_cursor(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str | None,
        channel_id: str,
        explicit_cursor: str | None,
    ) -> str | None:
        if session_id is None:
            return explicit_cursor
        return self._max_cursor(
            self._get_session_cursor_from_connection(connection, session_id, channel_id),
            explicit_cursor,
        )

    def _get_session_cursor_from_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        channel_id: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT cursor
            FROM discord_inbox_session_reads
            WHERE session_id = ? AND channel_id = ?
            """,
            (session_id, channel_id),
        ).fetchone()
        if row is None:
            return None
        return str(row["cursor"])

    @staticmethod
    def _session_reads_payload(rows: list[sqlite3.Row]) -> dict[str, dict[str, str]]:
        payload: dict[str, dict[str, str]] = {}
        for row in rows:
            session_id = str(row["session_id"])
            channel_id = str(row["channel_id"])
            cursor = str(row["cursor"])
            payload.setdefault(session_id, {})[channel_id] = cursor
        return payload

    @staticmethod
    def _validate_message(raw_value: object) -> DiscordMessageEnvelope | None:
        try:
            if isinstance(raw_value, dict):
                return DiscordMessageEnvelope.model_validate(raw_value)
            if isinstance(raw_value, str):
                return DiscordMessageEnvelope.model_validate_json(raw_value)
        except Exception:
            return None
        return None

    @staticmethod
    def _message_row(
        message: DiscordMessageEnvelope,
    ) -> tuple[str, str, str, str, str, str | None, str]:
        return (
            message.message_id,
            message.channel_id,
            message.author_id,
            message.author_name,
            DiscordInboxStore._datetime_to_storage(message.created_at),
            message.in_reply_to,
            _json_dumps(message.model_dump(mode="json")),
        )

    @staticmethod
    def _thread_binding_from_row(row: sqlite3.Row) -> DiscordThreadBinding:
        return DiscordThreadBinding(
            thread_id=str(row["thread_id"]),
            parent_channel_id=str(row["parent_channel_id"]),
            guild_id=str(row["guild_id"]),
            thread_type=int(row["thread_type"]),
            archived=bool(row["archived"]),
            locked=bool(row["locked"]),
            discovered_at=DiscordInboxStore._parse_dt(row["discovered_at"]),
            updated_at=DiscordInboxStore._parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _storage_cursor(cursor: str | None) -> str | None:
        if cursor is None:
            return None
        return DiscordInboxStore._datetime_to_storage(DiscordInboxStore._parse_dt(cursor))

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

    @staticmethod
    def _datetime_to_storage(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (
            value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        )
