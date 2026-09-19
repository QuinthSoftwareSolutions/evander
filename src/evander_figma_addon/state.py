"""Durable operations, directory settings, and cross-process file serialization."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from evander.addons import addon_runtime_dir
from evander.models import DirectoryPath

from .models import ADDON_ID, FigmaConflict, Settings, digest


class Store:
    def __init__(self, runtime_dir: Path):
        self.runtime_dir = Path(runtime_dir)
        self.root = addon_runtime_dir(runtime_dir, ADDON_ID)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.db"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                  directory TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (
                  kind TEXT NOT NULL, id TEXT NOT NULL, directory TEXT NOT NULL,
                  target TEXT NOT NULL, file_key TEXT NOT NULL, payload TEXT NOT NULL,
                  PRIMARY KEY (kind, id));
                CREATE INDEX IF NOT EXISTS records_scope ON records(kind, directory, target);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                  directory TEXT NOT NULL, target TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS acknowledgements (
                  cursor_id TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(cursor_id, seq));
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self, directory: str) -> tuple[int, Settings]:
        directory = DirectoryPath(segments=directory).as_posix()
        with self.db() as db:
            row = db.execute(
                "SELECT revision,payload FROM settings WHERE directory=?", (directory,)
            ).fetchone()
        return (
            (int(row["revision"]), Settings.model_validate_json(row["payload"]))
            if row
            else (0, Settings())
        )

    def save_settings(self, directory: str, expected_revision: int, settings: Settings):
        directory = DirectoryPath(segments=directory).as_posix()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM settings WHERE directory=?", (directory,)
            ).fetchone()
            revision = int(row[0]) if row else 0
            if revision != expected_revision:
                raise FigmaConflict("Figma settings changed; reload before saving")
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?,?)",
                (directory, revision + 1, settings.model_dump_json()),
            )
        return revision + 1

    def put(
        self,
        kind: str,
        id: str,
        directory: str,
        target: str,
        file_key: str,
        payload: dict[str, Any],
        *,
        immutable: bool = False,
    ):
        with self.db() as db:
            sql = "INSERT INTO" if immutable else "INSERT OR REPLACE INTO"
            try:
                db.execute(
                    f"{sql} records VALUES (?,?,?,?,?,?)",
                    (kind, id, directory, target, file_key, json.dumps(payload)),
                )
            except sqlite3.IntegrityError:
                raise FigmaConflict(
                    "Record or request_id already exists; use its original target and arguments"
                ) from None

    def get(self, kind: str, id: str, directory: str, target: str, file_key: str):
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM records WHERE kind=? AND id=? AND directory=? "
                "AND target=? AND file_key=?",
                (kind, id, directory, target, file_key),
            ).fetchone()
        if row is None:
            raise FigmaConflict("Unknown record in this Figma target")
        return json.loads(row[0])

    def records(self, kind: str, directory: str, target: str, file_key: str, limit=100):
        with self.db() as db:
            rows = db.execute(
                "SELECT payload FROM records WHERE kind=? AND directory=? "
                "AND target=? AND file_key=? ORDER BY rowid DESC LIMIT ?",
                (kind, directory, target, file_key, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def operation_for_file(self, operation_id: str, file_key: str):
        """Internal provenance lookup across registrations of the same Figma file."""
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM records WHERE kind='operation' AND id=? AND file_key=?",
                (operation_id, file_key),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def unresolved(self, file_key: str):
        with self.db() as db:
            rows = db.execute(
                "SELECT payload FROM records WHERE kind='operation' AND file_key=?", (file_key,)
            ).fetchall()
        return [
            record
            for record in (json.loads(row[0]) for row in rows)
            if record.get("status") in {"running", "uncertain"}
            # Process termination can leave a snapshot marked running, but it
            # cannot have changed the canvas and must not block file authors.
            and record.get("request", {}).get("operation") != "snapshot"
        ]

    def finish(self, record, directory, target, file_key, *, event=None):
        with self.db() as db:
            db.execute(
                "UPDATE records SET payload=? WHERE kind='operation' AND id=?",
                (json.dumps(record), record["operation_id"]),
            )
            if event:
                db.execute(
                    "INSERT OR IGNORE INTO events(id,directory,target,payload) VALUES (?,?,?,?)",
                    (record["operation_id"], directory, target, json.dumps(event)),
                )

    def event(self, id, directory, target, payload):
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO events(id,directory,target,payload) VALUES (?,?,?,?)",
                (id, directory, target, json.dumps(payload)),
            )

    def events(self, cursor_id, directory, target, limit=100):
        with self.db() as db:
            rows = db.execute(
                "SELECT e.* FROM events e WHERE directory=? AND target=? AND NOT EXISTS "
                "(SELECT 1 FROM acknowledgements a WHERE a.cursor_id=? AND a.seq=e.seq) "
                "ORDER BY seq LIMIT ?",
                (directory, target, cursor_id, limit),
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]

    def acknowledge(self, cursor_id, seq):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO acknowledgements VALUES (?,?)", (cursor_id, seq))

    @contextmanager
    def file_lock(self, file_key: str, timeout=30):
        root = self.root / "locks"
        root.mkdir(exist_ok=True)
        # Lock by Figma file, including when accessed through different directory connections.
        with (root / (digest(file_key) + ".lock")).open("a") as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise FigmaConflict("Figma file is busy; retry later") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
