from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any, ContextManager, Protocol

from evander.models import DiscordCommandEnvelope, EmailMessage, TeamsMessageEnvelope
from evander.storage import JsonStore
from evander.teams_outbound import (
    JsonTeamsOutboundStore,
    TeamsOutboundReceipt,
    TeamsOutboundEditState,
    outbound_message_lock,
)

SQLITE_BUSY_TIMEOUT_MS = 30_000


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class EmailRecordsRepository(Protocol):
    def get(self, message_id: str) -> EmailMessage | None: ...

    def put(self, message_id: str, message: EmailMessage) -> None: ...

    def read_all(self) -> dict[str, EmailMessage]: ...


class DiscordRecordsRepository(Protocol):
    def get(self, command_id: str) -> DiscordCommandEnvelope | None: ...

    def put(self, command_id: str, envelope: DiscordCommandEnvelope) -> None: ...

    def put_many(self, records: Mapping[str, DiscordCommandEnvelope]) -> None: ...

    def read_all(self) -> dict[str, DiscordCommandEnvelope]: ...


class TeamsRecordsRepository(Protocol):
    def get(self, cursor: str) -> TeamsMessageEnvelope | None: ...

    def put(self, cursor: str, envelope: TeamsMessageEnvelope) -> None: ...

    def read_all(self) -> dict[str, TeamsMessageEnvelope]: ...

    def create_outbound_receipt(
        self, receipt: TeamsOutboundReceipt, state: TeamsOutboundEditState
    ) -> None: ...

    def get_outbound_receipt(
        self, channel_target: str, message_id: str
    ) -> TeamsOutboundReceipt | None: ...

    def get_outbound_edit_state(
        self, channel_target: str, message_id: str
    ) -> TeamsOutboundEditState | None: ...

    def save_outbound_edit_state(
        self, channel_target: str, message_id: str, state: TeamsOutboundEditState
    ) -> None: ...

    def lock_outbound_message(
        self, channel_target: str, message_id: str
    ) -> ContextManager[None]: ...


def teams_records_with_outbound(store: TeamsRecordsRepository) -> TeamsRecordsRepository:
    """Retain compatibility with callers using the historical raw JSON store."""
    if isinstance(store, JsonStore):
        return JsonTeamsRecordsRepository(store.path)
    return store


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

    def put_many(self, records: Mapping[str, DiscordCommandEnvelope]) -> None:
        if not records:
            return
        validated = {
            command_id: DiscordCommandEnvelope.model_validate(envelope)
            for command_id, envelope in records.items()
        }

        def update(items: dict[str, DiscordCommandEnvelope]) -> None:
            items.update(validated)

        self._store.update_all(update)

    def read_all(self) -> dict[str, DiscordCommandEnvelope]:
        return self._store.read_all()


class JsonTeamsRecordsRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, TeamsMessageEnvelope, file_mode=0o600)
        self._outbound = JsonTeamsOutboundStore(path)

    def create_outbound_receipt(
        self, receipt: TeamsOutboundReceipt, state: TeamsOutboundEditState
    ) -> None:
        self._outbound.create(receipt, state)

    def get_outbound_receipt(
        self, channel_target: str, message_id: str
    ) -> TeamsOutboundReceipt | None:
        return self._outbound.receipt(channel_target, message_id)

    def get_outbound_edit_state(
        self, channel_target: str, message_id: str
    ) -> TeamsOutboundEditState | None:
        return self._outbound.state(channel_target, message_id)

    def save_outbound_edit_state(
        self, channel_target: str, message_id: str, state: TeamsOutboundEditState
    ) -> None:
        self._outbound.save_state(channel_target, message_id, state)

    def lock_outbound_message(self, channel_target: str, message_id: str):
        return self._outbound.lock(channel_target, message_id)

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
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        # Bodies and pending edits may contain image bearer URLs. Prepare mode
        # before opening SQLite so new WAL files inherit the private DB mode.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        for candidate in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if candidate.exists():
                candidate.chmod(candidate.stat().st_mode & 0o600)
        self.ensure_schema()

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
                    CREATE TABLE IF NOT EXISTS teams_outbound_receipts (
                        channel_target TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        receipt_json TEXT NOT NULL,
                        edit_json TEXT NOT NULL,
                        PRIMARY KEY (channel_target, message_id)
                    );
                    """
                )

    def create_outbound_receipt(
        self, receipt: TeamsOutboundReceipt, state: TeamsOutboundEditState
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO teams_outbound_receipts VALUES (?, ?, ?, ?)",
                (
                    receipt.channel_target,
                    receipt.message_id,
                    receipt.model_dump_json(),
                    state.model_dump_json(),
                ),
            )

    def get_outbound_receipt(
        self, channel_target: str, message_id: str
    ) -> TeamsOutboundReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_json FROM teams_outbound_receipts WHERE channel_target=? AND message_id=?",
                (channel_target, message_id),
            ).fetchone()
        return TeamsOutboundReceipt.model_validate_json(row[0]) if row else None

    def get_outbound_edit_state(
        self, channel_target: str, message_id: str
    ) -> TeamsOutboundEditState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT edit_json FROM teams_outbound_receipts WHERE channel_target=? AND message_id=?",
                (channel_target, message_id),
            ).fetchone()
        return TeamsOutboundEditState.model_validate_json(row[0]) if row else None

    def save_outbound_edit_state(
        self, channel_target: str, message_id: str, state: TeamsOutboundEditState
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE teams_outbound_receipts SET edit_json=? WHERE channel_target=? AND message_id=?",
                (state.model_dump_json(), channel_target, message_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("No Teams outbound ownership receipt")

    def lock_outbound_message(self, channel_target: str, message_id: str):
        return outbound_message_lock(
            self.path.with_name(self.path.name + ".edit-locks"), channel_target, message_id
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
