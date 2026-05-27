from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from agent_sandbox.models import DiscordCommandEnvelope, EmailMessage, TeamsMessageEnvelope
from agent_sandbox.storage import JsonStore

SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_TEAMS_STORE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def teams_records_db_path(path: Path) -> Path:
    if path.suffix.lower() in _SQLITE_TEAMS_STORE_SUFFIXES:
        return path
    return path.with_suffix(".db")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _read_legacy_teams_json(path: Path) -> dict[str, TeamsMessageEnvelope]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"Teams JSON store at {path} must contain an object at the top level")
    return {key: TeamsMessageEnvelope.model_validate(value) for key, value in raw.items()}


class EmailRecordsRepository(Protocol):
    def get(self, message_id: str) -> EmailMessage | None: ...

    def put(self, message_id: str, message: EmailMessage) -> None: ...

    def read_all(self) -> dict[str, EmailMessage]: ...


class DiscordRecordsRepository(Protocol):
    def get(self, command_id: str) -> DiscordCommandEnvelope | None: ...

    def put(self, command_id: str, envelope: DiscordCommandEnvelope) -> None: ...

    def put_many(self, items: Mapping[str, DiscordCommandEnvelope]) -> None: ...

    def read_all(self) -> dict[str, DiscordCommandEnvelope]: ...


class TeamsRecordsRepository(Protocol):
    def get(self, cursor: str) -> TeamsMessageEnvelope | None: ...

    def put(self, cursor: str, envelope: TeamsMessageEnvelope) -> None: ...

    def read_all(self) -> dict[str, TeamsMessageEnvelope]: ...


class JsonEmailRecordsRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, EmailMessage)

    @property
    def model(self) -> type[EmailMessage]:
        return self._store.model

    def get(self, message_id: str) -> EmailMessage | None:
        return self._store.get(message_id)

    def put(self, message_id: str, message: EmailMessage) -> None:
        self._store.put(message_id, message)

    def read_all(self) -> dict[str, EmailMessage]:
        return self._store.read_all()


class JsonDiscordRecordsRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, DiscordCommandEnvelope)

    @property
    def model(self) -> type[DiscordCommandEnvelope]:
        return self._store.model

    def get(self, command_id: str) -> DiscordCommandEnvelope | None:
        return self._store.get(command_id)

    def put(self, command_id: str, envelope: DiscordCommandEnvelope) -> None:
        self._store.put(command_id, envelope)

    def put_many(self, items: Mapping[str, DiscordCommandEnvelope]) -> None:
        self._store.put_many(items)

    def read_all(self) -> dict[str, DiscordCommandEnvelope]:
        return self._store.read_all()


class JsonTeamsRecordsRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, TeamsMessageEnvelope)

    @property
    def model(self) -> type[TeamsMessageEnvelope]:
        return self._store.model

    def get(self, cursor: str) -> TeamsMessageEnvelope | None:
        return self._store.get(cursor)

    def put(self, cursor: str, envelope: TeamsMessageEnvelope) -> None:
        self._store.put(cursor, envelope)

    def put_many(self, records: Mapping[str, TeamsMessageEnvelope]) -> None:
        if not records:
            return
        validated = {
            cursor: TeamsMessageEnvelope.model_validate(envelope)
            for cursor, envelope in records.items()
        }

        def update(items: dict[str, TeamsMessageEnvelope]) -> None:
            items.update(validated)

        self._store.update_all(update)

    def read_all(self) -> dict[str, TeamsMessageEnvelope]:
        return self._store.read_all()


class SqliteTeamsRecordsRepository:
    def __init__(self, path: Path, *, legacy_json_path: Path | None = None) -> None:
        self.path = path
        self.legacy_json_path = legacy_json_path
        self._lock = RLock()
        self.ensure_schema()
        self._import_legacy_json_if_empty()

    @property
    def model(self) -> type[TeamsMessageEnvelope]:
        return TeamsMessageEnvelope

    def ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS teams_message_records (
                        cursor TEXT PRIMARY KEY,
                        message_id TEXT NOT NULL,
                        team_id TEXT NOT NULL DEFAULT '',
                        channel_id TEXT NOT NULL DEFAULT '',
                        chat_id TEXT,
                        thread_id TEXT,
                        created_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_teams_message_records_message_id
                        ON teams_message_records(message_id);
                    CREATE INDEX IF NOT EXISTS idx_teams_message_records_created_at
                        ON teams_message_records(created_at, cursor);
                    """
                )

    def get(self, cursor: str) -> TeamsMessageEnvelope | None:
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM teams_message_records
                    WHERE cursor = ?
                    """,
                    (cursor,),
                ).fetchone()
        if row is None:
            return None
        return TeamsMessageEnvelope.model_validate_json(row["payload_json"])

    def put(self, cursor: str, envelope: TeamsMessageEnvelope) -> None:
        self.put_many({cursor: envelope})

    def put_many(self, records: Mapping[str, TeamsMessageEnvelope]) -> None:
        if not records:
            return
        rows = [
            self._row_for_record(cursor, TeamsMessageEnvelope.model_validate(envelope))
            for cursor, envelope in records.items()
        ]
        with self._lock:
            with self._connect() as connection:
                self._put_rows(connection, rows)

    def read_all(self) -> dict[str, TeamsMessageEnvelope]:
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT cursor, payload_json
                    FROM teams_message_records
                    ORDER BY created_at, cursor
                    """
                ).fetchall()
        return {
            str(row["cursor"]): TeamsMessageEnvelope.model_validate_json(row["payload_json"])
            for row in rows
        }

    def _import_legacy_json_if_empty(self) -> None:
        legacy_json_path = self.legacy_json_path
        if (
            legacy_json_path is None
            or legacy_json_path.suffix.lower() in _SQLITE_TEAMS_STORE_SUFFIXES
        ):
            return
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS record_count FROM teams_message_records"
                ).fetchone()
                if row is not None and int(row["record_count"]) > 0:
                    return
                records = _read_legacy_teams_json(legacy_json_path)
                if not records:
                    return
                rows = [
                    self._row_for_record(cursor, envelope)
                    for cursor, envelope in records.items()
                ]
                self._put_rows(connection, rows)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def _row_for_record(
        cursor: str, envelope: TeamsMessageEnvelope
    ) -> tuple[str, str, str, str, str | None, str | None, str, str]:
        payload_json = _json_dumps(envelope.model_dump(mode="json"))
        return (
            cursor,
            envelope.message_id,
            envelope.team_id,
            envelope.channel_id,
            envelope.chat_id,
            envelope.thread_id,
            envelope.created_at.isoformat(),
            payload_json,
        )

    @staticmethod
    def _put_rows(
        connection: sqlite3.Connection,
        rows: list[tuple[str, str, str, str, str | None, str | None, str, str]],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO teams_message_records(
                cursor,
                message_id,
                team_id,
                channel_id,
                chat_id,
                thread_id,
                created_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cursor) DO UPDATE SET
                message_id = excluded.message_id,
                team_id = excluded.team_id,
                channel_id = excluded.channel_id,
                chat_id = excluded.chat_id,
                thread_id = excluded.thread_id,
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            rows,
        )
