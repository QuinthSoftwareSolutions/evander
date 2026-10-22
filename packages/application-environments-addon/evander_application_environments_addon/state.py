"""Revision-checked configuration and idempotent durable application intents."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
from .models import Settings
from evander.resources.models import ResourceError


class Store:
    def __init__(self, runtime_dir):
        self.root = Path(runtime_dir) / "addons/application-environments-addon"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "state.db"
        with self.db() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS settings(directory TEXT PRIMARY KEY,revision INTEGER,payload TEXT); CREATE TABLE IF NOT EXISTS environments(id TEXT PRIMARY KEY,owner TEXT,directory TEXT,request_id TEXT,request TEXT,payload TEXT,UNIQUE(owner,request_id));"
            )
        self.path.chmod(0o600)

    @contextmanager
    def locked(self, id):
        # Gateway, workflow and Admin Web processes share the same lifecycle lock.
        path = self.root / (hashlib.sha256(id.encode()).hexdigest() + ".lock")
        with path.open("a") as handle:
            path.chmod(0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self, directory):
        with self.db() as db:
            parts = directory.split("/")
            for count in range(len(parts), 0, -1):
                row = db.execute(
                    "SELECT revision,payload FROM settings WHERE directory=?",
                    ("/".join(parts[:count]),),
                ).fetchone()
                if row:
                    return row[0], Settings.model_validate_json(row[1])
        return 0, Settings()

    def save_settings(self, directory, revision, settings):
        with self.db() as db:
            current = db.execute(
                "SELECT revision FROM settings WHERE directory=?", (directory,)
            ).fetchone()
            # The API supplies the effective inherited revision for a new override.
            inherited = current[0] if current else 0
            if current is None:
                parts = directory.split("/")
                for count in range(len(parts) - 1, 0, -1):
                    row = db.execute(
                        "SELECT revision FROM settings WHERE directory=?",
                        ("/".join(parts[:count]),),
                    ).fetchone()
                    if row:
                        inherited = row[0]
                        break
            if inherited != revision:
                raise ResourceError("Configuration revision changed")
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?,?)",
                (directory, revision + 1, settings.model_dump_json()),
            )
        return revision + 1

    def create(self, record, request, quota):
        owner = record["session"]["agent_session_id"]
        fingerprint = json.dumps(request, sort_keys=True)
        with self.db() as db:
            row = db.execute(
                "SELECT request,payload FROM environments WHERE owner=? AND request_id=?",
                (owner, request["request_id"]),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ResourceError("Request ID was reused with different inputs")
                return json.loads(row[1]), False
            active = [json.loads(row[0]) for row in db.execute("SELECT payload FROM environments")]
            active = [row for row in active if row["state"] not in {"closed", "expired"}]
            if sum(
                row["session"]["worker_id"] == record["session"]["worker_id"] for row in active
            ) >= quota or any(row["session"]["agent_session_id"] == owner for row in active):
                raise ResourceError("Application environment quota reached")
            db.execute(
                "INSERT INTO environments VALUES(?,?,?,?,?,?)",
                (
                    record["session"]["id"],
                    owner,
                    record["session"]["directory"],
                    request["request_id"],
                    fingerprint,
                    json.dumps(record),
                ),
            )
        return record, True

    def get(self, id, directory=None):
        with self.db() as db:
            row = db.execute(
                "SELECT payload,directory FROM environments WHERE id=?", (id,)
            ).fetchone()
        if not row or directory is not None and row[1] != directory:
            raise ResourceError("Unknown application environment")
        return json.loads(row[0])

    def put(self, record):
        with self.db() as db:
            db.execute(
                "UPDATE environments SET payload=? WHERE id=?",
                (json.dumps(record), record["session"]["id"]),
            )

    def list(self, directory):
        with self.db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM environments WHERE directory=?", (directory,)
                )
            ]
