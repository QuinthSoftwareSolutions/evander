"""Small durable journal shared by optional resource providers, without provider dependencies."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .models import Conflict, Lease, ResourceError, Settings


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "resources.db"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(directory TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL, actor TEXT, operation TEXT, resource TEXT);
                CREATE INDEX IF NOT EXISTS resource_active_leases ON leases(id)
                    WHERE json_extract(payload,'$.state') NOT IN ('released','expired');
                CREATE INDEX IF NOT EXISTS resource_directory_history ON leases(
                    json_extract(payload,'$.owner.directory'),json_extract(payload,'$.created_at') DESC);
                CREATE INDEX IF NOT EXISTS resource_operation_directory ON operations(json_extract(payload,'$.directory'));
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def locked(self, lease_id=None, *, blocking=True):
        name = (
            "resources.lock"
            if lease_id is None
            else "lease-" + hashlib.sha256(lease_id.encode()).hexdigest() + ".lock"
        )
        fd = os.open(self.root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        acquired = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            acquired = True
            yield
        finally:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def settings(self, directory):
        with self.connect() as db:
            row = db.execute(
                "SELECT revision,payload FROM settings WHERE directory=?", (directory,)
            ).fetchone()
        return (row[0], Settings.model_validate_json(row[1])) if row else (0, Settings())

    def save_settings(self, directory, revision, settings, actor):
        with self.locked(), self.connect() as db:
            if self.settings(directory)[0] != revision:
                raise Conflict("Settings changed; refresh before saving")
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?,?)",
                (directory, revision + 1, settings.model_dump_json()),
            )
        self.audit(actor, "settings", directory)
        return revision + 1

    def get(self, lease_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM leases WHERE id=?", (lease_id,)).fetchone()
        if row is None:
            raise ResourceError("Unknown resource lease")
        return Lease.model_validate_json(row[0])

    def leases(self, *, active_only=False, directory=None, limit=None):
        query = "SELECT payload FROM leases"
        clauses, parameters = [], []
        if active_only:
            clauses.append("json_extract(payload,'$.state') NOT IN ('released','expired')")
        if directory is not None:
            clauses.append("json_extract(payload,'$.owner.directory')=?")
            parameters.append(directory)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY json_extract(payload,'$.created_at') DESC" if limit else " ORDER BY id"
        if limit:
            query += " LIMIT ?"
            parameters.append(limit)
        with self.connect() as db:
            return [Lease.model_validate_json(row[0]) for row in db.execute(query, parameters)]

    def put(self, lease):
        lease.revision += 1
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO leases VALUES(?,?)",
                (lease.lease_id, lease.model_dump_json()),
            )
        return lease

    def put_with_operation(self, lease, key, fingerprint, record):
        lease.revision += 1
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR REPLACE INTO leases VALUES(?,?)",
                (lease.lease_id, lease.model_dump_json()),
            )
            db.execute(
                "INSERT INTO operations VALUES(?,?,?)", (key, fingerprint, json.dumps(record))
            )
        return lease

    def operation(self, key):
        with self.connect() as db:
            row = db.execute(
                "SELECT fingerprint,payload FROM operations WHERE id=?", (key,)
            ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def put_operation(self, key, fingerprint, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO operations VALUES(?,?,?)",
                (key, fingerprint, json.dumps(value)),
            )

    def operations(self, directory):
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM operations")
                if json.loads(row[0]).get("directory") == directory
            ]

    def operation_summaries(self, directory):
        allowed = (
            "id",
            "directory",
            "operation",
            "connection_id",
            "resource_id",
            "database",
            "state",
            "revision",
        )
        columns = ",".join(f"json_extract(payload,'$.{key}')" for key in allowed)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT {columns} FROM operations WHERE json_extract(payload,'$.directory')=? ORDER BY rowid DESC LIMIT 200",
                (directory,),
            )
            return [
                {key: value for key, value in zip(allowed, row) if value is not None}
                for row in rows
            ]

    def audit(self, actor, operation, resource):
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(at,actor,operation,resource) VALUES(?,?,?,?)",
                (time.time(), actor, operation, resource),
            )
