"""Private, durable recovery manifests and process-safe site mutation leases."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from uuid import uuid4

from evander.credentials.store import protected_read, secure_directory, write_private
from evander.models import DirectoryPath

from .models import ADDON_ID, Conflict, Settings, UNRESOLVED, digest, owns_browser


class Store:
    def __init__(self, runtime_dir):
        self.runtime_dir = Path(runtime_dir)
        self.root = self.runtime_dir / "addons" / ADDON_ID
        secure_directory(self.root, create=True)
        self.path = self.root / "changes.db"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise Conflict("Change database must be a private regular file")
        finally:
            os.close(fd)
        with self.db() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS settings(directory TEXT PRIMARY KEY,
                revision INTEGER NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY, directory TEXT NOT NULL,
                site TEXT NOT NULL, origin TEXT NOT NULL, revision INTEGER NOT NULL,
                state TEXT NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS change_scope ON changes(directory,site,updated);
              CREATE INDEX IF NOT EXISTS change_origin ON changes(origin,state);
              CREATE TABLE IF NOT EXISTS requests(directory TEXT NOT NULL, site TEXT NOT NULL,
                operation TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                change_id TEXT NOT NULL, PRIMARY KEY(directory,site,operation,key));
              CREATE TABLE IF NOT EXISTS fences(origin TEXT PRIMARY KEY, counter INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                change_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self, directory):
        directory = DirectoryPath(segments=directory).as_posix()
        with self.db() as db:
            row = db.execute("SELECT * FROM settings WHERE directory=?", (directory,)).fetchone()
        return (
            (row["revision"], Settings.model_validate_json(row["payload"]))
            if row
            else (0, Settings())
        )

    def save_settings(self, directory, expected_revision, settings):
        directory = DirectoryPath(segments=directory).as_posix()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision,payload FROM settings WHERE directory=?", (directory,)
            ).fetchone()
            revision = row[0] if row else 0
            if revision != expected_revision:
                raise Conflict("Site configuration changed; reload before saving")
            previous = Settings.model_validate_json(row["payload"]) if row else Settings()
            pending = db.execute(
                "SELECT site,payload FROM changes WHERE directory=? AND state IN ('prepared','queued','applying','verifying','restoring','uncertain','conflict','recovery_required')",
                (directory,),
            ).fetchall()
            for record in pending:
                change = json.loads(record["payload"])
                if (
                    change["state"] == "prepared"
                    and change.get("ready")
                    and not owns_browser(change)
                ):
                    continue
                old, new = previous.sites.get(record["site"]), settings.sites.get(record["site"])
                if old and (new is None or new.binding() != old.binding()):
                    raise Conflict(
                        "Resolve or restore the active change before rebinding or removing its site"
                    )
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?,?)",
                (directory, revision + 1, settings.model_dump_json()),
            )
        return revision + 1

    @contextmanager
    def lease(self, origin):
        # A live process owns the OS lock. A crash releases it; the next owner gets a
        # strictly larger token which remote adapters must check before writing.
        fd = os.open(
            self.root / (digest(origin) + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Conflict("Another website mutation is running") from None
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO fences VALUES (?,1) ON CONFLICT(origin) DO UPDATE SET counter=counter+1",
                    (origin,),
                )
                token = db.execute(
                    "SELECT counter FROM fences WHERE origin=?", (origin,)
                ).fetchone()[0]
            yield token
        finally:
            os.close(fd)

    def request(self, directory, site, operation, key, payload):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE directory=? AND site=? AND operation=? AND key=?",
                (directory, site, operation, key),
            ).fetchone()
        if not row:
            return None
        if row["fingerprint"] != digest(payload):
            raise Conflict("Idempotency key was already used for a different request")
        return self.get(row["change_id"], directory, site)

    def remember(self, change, operation, key, payload):
        with self.db() as db:
            db.execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?)",
                (
                    change["directory"],
                    change["site_id"],
                    operation,
                    key,
                    digest(payload),
                    change["change_id"],
                ),
            )

    def create(self, directory, site, origin, payload, *, request=None):
        change = {
            "change_id": "wp_" + uuid4().hex,
            "directory": directory,
            "site_id": site,
            "origin": origin,
            "revision": 1,
            "state": "prepared",
            "created_at": time.time(),
            **payload,
        }
        with self.db() as db:
            db.execute(
                "INSERT INTO changes VALUES (?,?,?,?,?,?,?,?)",
                (
                    change["change_id"],
                    directory,
                    site,
                    origin,
                    1,
                    "prepared",
                    time.time(),
                    json.dumps(change),
                ),
            )
            if request:
                db.execute(
                    "INSERT INTO requests VALUES (?,?,?,?,?,?)",
                    (
                        directory,
                        site,
                        "prepare_change",
                        request["idempotency_key"],
                        digest(request),
                        change["change_id"],
                    ),
                )
        return change

    def get(self, id, directory, site=None):
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM changes WHERE id=? AND directory=?", (id, directory)
            ).fetchone()
        if not row:
            raise Conflict("Unknown change in this directory")
        value = json.loads(row[0])
        if site is not None and value["site_id"] != site:
            raise Conflict("Change belongs to a different site")
        return value

    def update(self, change, *, request=None, **fields):
        previous = change["revision"]
        value = {**change, **fields, "revision": previous + 1, "updated_at": time.time()}
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "UPDATE changes SET revision=?,state=?,updated=?,payload=? WHERE id=? AND revision=?",
                (
                    value["revision"],
                    value["state"],
                    time.time(),
                    json.dumps(value),
                    value["change_id"],
                    previous,
                ),
            )
            if row.rowcount != 1:
                raise Conflict("Change revision changed")
            db.execute(
                "INSERT INTO events(change_id,revision,payload) VALUES (?,?,?)",
                (
                    value["change_id"],
                    value["revision"],
                    json.dumps(
                        {"state": value["state"], "time": time.time(), "actor": value.get("actor")}
                    ),
                ),
            )
            if request:
                operation, key, payload = request
                db.execute(
                    "INSERT INTO requests VALUES (?,?,?,?,?,?)",
                    (
                        value["directory"],
                        value["site_id"],
                        operation,
                        key,
                        digest(payload),
                        value["change_id"],
                    ),
                )
        return value

    def list(self, directory, site=None, *, active=False, pending=False):
        with self.db() as db:
            states = (
                " AND state IN ('queued','applying','verifying','restoring','uncertain','prepared')"
                if pending
                else ""
            )
            rows = db.execute(
                "SELECT payload FROM changes WHERE directory=?"
                + states
                + " ORDER BY updated DESC LIMIT 500",
                (directory,),
            ).fetchall()
        return [
            value
            for row in rows
            if (value := json.loads(row[0]))
            and (site is None or value["site_id"] == site)
            and (not active or value["state"] in UNRESOLVED)
        ]

    def require_clear(self, origin, change_id=None):
        with self.db() as db:
            rows = db.execute("SELECT id,state FROM changes WHERE origin=?", (origin,)).fetchall()
        if any(row["id"] != change_id and row["state"] in UNRESOLVED for row in rows):
            raise Conflict("Website has an unresolved change; reconcile or restore it first")
        with self.db() as db:
            prepared = db.execute(
                "SELECT id,payload FROM changes WHERE origin=? AND state='prepared'", (origin,)
            ).fetchall()
        if any(
            row["id"] != change_id and owns_browser(json.loads(row["payload"])) for row in prepared
        ):
            raise Conflict("A prepared browser change owns this website")

    def artifact(self, data: bytes, suffix="json"):
        name = sha256(data).hexdigest() + "." + suffix
        path = self.root / "artifacts" / name
        if not path.exists():
            write_private(path, data)
        elif protected_read(path, limit=200 * 1024**2) != data:
            raise Conflict("Artifact digest collision")
        return name

    def read_artifact(self, name):
        if not isinstance(name, str) or not __import__("re").fullmatch(
            r"[a-f0-9]{64}\.(json|png|zip)", name
        ):
            raise Conflict("Invalid artifact ID")
        return protected_read(self.root / "artifacts" / name, limit=200 * 1024**2)

    def prune(self, directory, retention_days, release_pin=None):
        # Retain unresolved records and the latest published recovery baseline for each site.
        with self.db() as db:
            rows = db.execute(
                "SELECT payload FROM changes WHERE directory=? ORDER BY updated DESC", (directory,)
            ).fetchall()
        changes = [json.loads(row[0]) for row in rows]
        latest = {}
        for change in changes:
            if change["state"] == "published":
                latest.setdefault(change["site_id"], change["change_id"])
        cutoff = time.time() - retention_days * 86400
        for change in changes:
            if (
                change["state"] in UNRESOLVED
                or (change["state"] == "prepared" and owns_browser(change))
                or latest.get(change["site_id"]) == change["change_id"]
                or change.get("updated_at", change["created_at"]) >= cutoff
            ):
                continue
            try:
                with self.lease(change["origin"]):
                    current = self.get(change["change_id"], directory)
                    if current["revision"] != change["revision"]:
                        continue
                    source = change.get("deployment") or change.get("build_pending")
                    if source and source.get("pin"):
                        if release_pin is None:
                            continue
                        release_pin(change, source["pin"])
                    with self.db() as db:
                        db.execute("DELETE FROM events WHERE change_id=?", (change["change_id"],))
                        db.execute("DELETE FROM requests WHERE change_id=?", (change["change_id"],))
                        db.execute("DELETE FROM changes WHERE id=?", (change["change_id"],))
            except Conflict:
                continue
        with self.db() as db:
            retained = [row[0] for row in db.execute("SELECT payload FROM changes").fetchall()]
        # Artifacts are content-addressed and can be shared across directories. Keep
        # all referenced data and a full retention window for in-flight captures.
        import re

        referenced = set(re.findall(r"[a-f0-9]{64}\.(?:json|png|zip)", "\n".join(retained)))
        artifacts = self.root / "artifacts"
        if artifacts.exists():
            for path in artifacts.iterdir():
                if (
                    not path.is_symlink()
                    and path.name not in referenced
                    and path.stat().st_mtime < cutoff
                ):
                    path.unlink()
