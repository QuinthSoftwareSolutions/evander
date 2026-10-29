"""Durable intents, quota admission and process-wide serialization.

The file lock deliberately spans external effects: a second worker must not race
a reset/release. Durable intent is committed before every external operation.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .models import Conflict, FixtureError, TERMINAL


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "leases.db"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(session TEXT, request TEXT, lease TEXT,
                    PRIMARY KEY(session, request));
                CREATE TABLE IF NOT EXISTS settings(directory TEXT PRIMARY KEY, revision INTEGER, payload TEXT);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL, actor TEXT, operation TEXT, lease TEXT);
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def locked(self):
        fd = os.open(self.root / "leases.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def get(self, lease_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM leases WHERE id=?", (lease_id,)).fetchone()
        if not row:
            raise FixtureError("Unknown fixture lease")
        return json.loads(row[0])

    def list(self):
        with self.connect() as db:
            return [
                json.loads(row[0]) for row in db.execute("SELECT payload FROM leases ORDER BY id")
            ]

    def put(self, lease):
        lease["revision"] = lease.get("revision", 0) + 1
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO leases VALUES(?,?)", (lease["lease_id"], json.dumps(lease))
            )
        return lease

    def audit(self, actor, operation, lease_id=None):
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(at,actor,operation,lease) VALUES(?,?,?,?)",
                (time.time(), actor, operation, lease_id),
            )

    def settings(self, directory):
        from .models import Settings

        with self.connect() as db:
            row = db.execute(
                "SELECT revision,payload FROM settings WHERE directory=?", (directory,)
            ).fetchone()
        return (row[0], Settings.model_validate_json(row[1])) if row else (0, Settings())

    def save_settings(self, directory, expected_revision, settings, actor):
        with self.locked():
            revision, _ = self.settings(directory)
            if revision != expected_revision:
                raise Conflict("Settings revision changed; reread before saving")
            with self.connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES(?,?,?)",
                    (directory, revision + 1, settings.model_dump_json()),
                )
            self.audit(actor, "settings")
        return revision + 1

    def acquire(self, scope, profile, request_id, settings, *, now=None):
        now = time.time() if now is None else now
        if not request_id or len(request_id) > 128:
            raise FixtureError("request_id must have 1 to 128 characters")
        # Caller holds the process lock. Admission and the deduplication record
        # are one transaction, including after process termination.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT lease FROM requests WHERE session=? AND request=?",
                (scope.session_id, request_id),
            ).fetchone()
            if row:
                lease = json.loads(
                    db.execute("SELECT payload FROM leases WHERE id=?", row).fetchone()[0]
                )
                if (
                    any(
                        lease["scope"][key] != getattr(scope, key)
                        for key in (
                            "session_id",
                            "agent_id",
                            "team_id",
                            "project_id",
                            "task_id",
                            "worker_id",
                            "directory",
                        )
                    )
                    or lease["profile"] != profile
                ):
                    raise Conflict("request_id is already bound to another fixture request")
                return lease
            active = [json.loads(row[0]) for row in db.execute("SELECT payload FROM leases")]
            active = [item for item in active if item["state"] not in TERMINAL]
            for lease in active:
                if lease["scope"]["task_id"] == scope.task_id:
                    prior = lease["scope"]
                    if (
                        all(
                            prior[k] == getattr(scope, k)
                            for k in (
                                "session_id",
                                "agent_id",
                                "team_id",
                                "project_id",
                                "worker_id",
                                "directory",
                            )
                        )
                        and lease["profile"] == profile
                    ):
                        db.execute(
                            "INSERT INTO requests VALUES(?,?,?)",
                            (scope.session_id, request_id, lease["lease_id"]),
                        )
                        return lease
                    raise Conflict(
                        "Task already has a fixture bundle; release it before acquiring another"
                    )
            if settings.drain_only:
                raise FixtureError(
                    "Test Fixtures is draining; use the SQL Server and RabbitMQ add-ons for new resources"
                )
            if (
                sum(item["scope"]["worker_id"] == scope.worker_id for item in active)
                >= settings.max_per_worker
            ):
                raise FixtureError("Worker fixture quota reached")
            lease = dict(
                lease_id="fx-" + uuid.uuid4().hex,
                scope=scope.model_dump(),
                profile=profile,
                state="provisioning",
                revision=1,
                created_at=now,
                expires_at=now + settings.idle_seconds,
                config_path=None,
                endpoints={},
                resource_counts={},
                error=None,
                fault_until=None,
                bound_job_id=None,
                notified=None,
            )
            if scope.worker_id in settings.workers:
                lease["worker"] = settings.workers[scope.worker_id].model_dump()
            db.execute("INSERT INTO leases VALUES(?,?)", (lease["lease_id"], json.dumps(lease)))
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)",
                (scope.session_id, request_id, lease["lease_id"]),
            )
            return lease
