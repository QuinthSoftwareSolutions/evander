"""Directory-bound state, encrypted OAuth custody and cross-process coordination."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet

from evander.addons import addon_runtime_dir

from .models import ADDON_ID, Connection, MiroError, in_directory
from .permissions import BoardGrantStore


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class MiroStore(BoardGrantStore):
    def __init__(self, runtime_dir: str | Path):
        self.runtime_dir = Path(runtime_dir)
        self.root = addon_runtime_dir(self.runtime_dir, ADDON_ID)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.root / "state.db"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(kind, key));
                CREATE TABLE IF NOT EXISTS usage (
                    connection_key TEXT NOT NULL, day TEXT NOT NULL, calls INTEGER NOT NULL,
                    PRIMARY KEY(connection_key, day));
            """)
            self.migrate_board_grants(db)

    @contextmanager
    def db(self):
        fd = os.open(self.database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind: str, key: str) -> dict | None:
        with self.db() as db:
            row = db.execute(
                "SELECT value FROM records WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind: str, key: str, value: dict) -> None:
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO records VALUES (?, ?, ?)", (kind, key, encoded(value))
            )

    def list(self, kind: str) -> list[dict]:
        with self.db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT value FROM records WHERE kind=? ORDER BY key", (kind,)
                )
            ]

    def delete(self, kind: str, key: str) -> None:
        with self.db() as db:
            db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, key))

    @staticmethod
    def connection_key(directory: str, connection_id: str) -> str:
        return digest([directory, connection_id])

    def save_connection(self, connection: Connection, expected_revision: int) -> dict:
        # Legacy connection-wide board lists never authorize session access.
        connection = connection.model_copy(update={"boards": []})
        key = self.connection_key(connection.directory_path, connection.id)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM records WHERE kind='connection' AND key=?", (key,)
            ).fetchone()
            previous = json.loads(row[0]) if row else None
            if (previous or {}).get("revision", 0) != expected_revision:
                raise MiroError("conflict", "Connection changed; reload before saving")
            changed_auth = (
                previous and previous["config"]["redirect_uri"] != connection.redirect_uri
            )
            value = {
                "key": key,
                "config": connection.model_dump(),
                "revision": expected_revision + 1,
                "generation": uuid4().hex
                if not previous or changed_auth
                else previous["generation"],
                "updated_at": time.time(),
            }
            db.execute(
                "INSERT OR REPLACE INTO records VALUES ('connection', ?, ?)", (key, encoded(value))
            )
            if changed_auth:
                db.execute("DELETE FROM records WHERE kind='credentials' AND key=?", (key,))
        return value

    def effective_connections(self, directory: str) -> list[dict]:
        effective: dict[str, dict] = {}
        for record in self.list("connection"):
            config = record["config"]
            if not in_directory(config["directory_path"], directory):
                continue
            previous = effective.get(config["id"])
            if previous is None or len(config["directory_path"]) > len(
                previous["config"]["directory_path"]
            ):
                effective[config["id"]] = record
        # Select overrides before checking enabled/access; a disabled child still
        # shadows its parent and must not accidentally reveal the parent connection.
        return [effective[key] for key in sorted(effective)]

    def resolve_connection(self, directory: str, connection_id: str) -> dict:
        for record in self.effective_connections(directory):
            if record["config"]["id"] == connection_id:
                return record
        raise MiroError("forbidden", "Miro connection is unavailable in this directory")

    def assert_generation(self, record: dict) -> None:
        current = self.get("connection", record["key"])
        if (
            not current
            or current["generation"] != record["generation"]
            or not current["config"]["enabled"]
        ):
            raise MiroError("connection_changed", "Miro connection changed; reconnect or retry")

    def assert_current(self, record: dict) -> None:
        self.assert_generation(record)
        if self.get("connection", record["key"])["revision"] != record["revision"]:
            raise MiroError(
                "connection_changed", "Miro grants changed; reload before making another request"
            )

    def disconnect(self, record: dict) -> None:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM records WHERE kind='connection' AND key=?", (record["key"],)
            ).fetchone()
            current = json.loads(row[0])
            if current["revision"] != record["revision"]:
                raise MiroError("conflict", "Connection changed; reload before disconnecting")
            current.update(generation=uuid4().hex, revision=current["revision"] + 1)
            db.execute(
                "UPDATE records SET value=? WHERE kind='connection' AND key=?",
                (encoded(current), record["key"]),
            )
            db.execute("DELETE FROM records WHERE kind='credentials' AND key=?", (record["key"],))

    def _cipher(self) -> Fernet:
        path = self.root / "oauth.key"
        if not path.exists():
            fd, temporary = tempfile.mkstemp(dir=self.root)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            os.fchmod(fd, 0o600)
            return Fernet(stream.read())

    def seal(self, value: dict) -> str:
        return self._cipher().encrypt(encoded(value).encode()).decode()

    def unseal(self, value: str) -> dict:
        return json.loads(self._cipher().decrypt(value.encode()))

    def credentials(self, record: dict) -> dict | None:
        self.assert_generation(record)
        saved = self.get("credentials", record["key"])
        if not saved or saved["generation"] != record["generation"]:
            return None
        return self.unseal(saved["encrypted"])

    def save_credentials(self, record: dict, credentials: dict) -> None:
        encrypted = self.seal(credentials)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM records WHERE kind='connection' AND key=?", (record["key"],)
            ).fetchone()
            current = json.loads(row[0]) if row else {}
            if (
                current.get("generation") != record["generation"]
                or not current["config"]["enabled"]
            ):
                raise MiroError("connection_changed", "Connection changed during authorization")
            db.execute(
                "INSERT OR REPLACE INTO records VALUES ('credentials', ?, ?)",
                (
                    record["key"],
                    encoded({"generation": record["generation"], "encrypted": encrypted}),
                ),
            )

    def consume_call(self, record: dict) -> None:
        day = datetime.now(UTC).date().isoformat()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT calls FROM usage WHERE connection_key=? AND day=?", (record["key"], day)
            ).fetchone()
            count = row[0] if row else 0
            if count >= record["config"]["daily_call_budget"]:
                raise MiroError(
                    "quota_exhausted", "Miro daily call budget exhausted; resets at 00:00 UTC"
                )
            db.execute(
                "INSERT OR REPLACE INTO usage VALUES (?, ?, ?)", (record["key"], day, count + 1)
            )

    def usage(self, record: dict) -> int:
        with self.db() as db:
            row = db.execute(
                "SELECT calls FROM usage WHERE connection_key=? AND day=?",
                (
                    record["key"],
                    datetime.now(UTC).date().isoformat(),
                ),
            ).fetchone()
        return row[0] if row else 0

    @asynccontextmanager
    async def lock(self, name: str, timeout: float = 45):
        """flock releases on process death; no stale network-write lease can be stolen."""
        path = self.root / ("lock-" + digest(name))
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise MiroError("busy", "Miro operation is already running; retry later")
                    await asyncio.sleep(0.05)
            yield
        finally:
            os.close(fd)

    def snapshot(self, record: dict, board_id: str, scope: dict, kind: str, data: Any) -> dict:
        value = {
            "connection_key": record["key"],
            "board_id": board_id,
            "scope": scope,
            "kind": kind,
            "data": data,
        }
        snapshot_id = digest(value)
        value.update(snapshot_id=snapshot_id, observed_at=time.time())
        self.put("snapshot", snapshot_id, value)
        return value
