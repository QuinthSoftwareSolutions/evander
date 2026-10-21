"""Small SQLite transactions; credentials are hashed and frames are never persisted."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time

from .models import BrowserError, Session, Settings, TERMINAL
from .run_state import RunStore


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store(RunStore):
    def __init__(self, runtime_dir, *, now=time.time):
        self.root = Path(runtime_dir) / "addons" / "browser-sessions-addon"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "state.db"
        self.now = now
        with self.connect() as db:
            # Switching a legacy database to WAL can return SQLITE_BUSY even
            # with busy_timeout set when another process initializes it too.
            deadline = time.monotonic() + 10
            while True:
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(directory TEXT PRIMARY KEY, revision INTEGER, payload TEXT);
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, directory TEXT, owner TEXT,
                    request_id TEXT, payload TEXT, UNIQUE(owner,request_id));
                CREATE INDEX IF NOT EXISTS sessions_directory ON sessions(directory);
                CREATE TABLE IF NOT EXISTS capabilities(hash TEXT PRIMARY KEY, session_id TEXT,
                    role TEXT, principal TEXT, expires_at REAL, used INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                    at REAL, actor TEXT, kind TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
                    session_id TEXT, channel TEXT, actor TEXT, body TEXT, at REAL,
                    reply_to TEXT, status TEXT, annotation TEXT);
                CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, session_id TEXT, actor TEXT,
                    state TEXT, at REAL, fingerprint TEXT);
                CREATE TABLE IF NOT EXISTS dispatches(message_id TEXT PRIMARY KEY, state TEXT, lease_until REAL);
                CREATE TABLE IF NOT EXISTS delivery_poll_state(id INTEGER PRIMARY KEY, after_seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS private_sessions(session_id TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS voice_requests(session_id TEXT, actor TEXT, request_id TEXT,
                    kind TEXT, fingerprint TEXT, state TEXT, at REAL, lease_until REAL, result TEXT,
                    PRIMARY KEY(session_id,actor,request_id));
                CREATE INDEX IF NOT EXISTS voice_requests_active ON voice_requests(state,lease_until);

            """)
            self.init_runs(db)
            if "delivery_status" not in {
                row[1] for row in db.execute("PRAGMA table_info(messages)")
            }:
                db.execute("ALTER TABLE messages ADD COLUMN delivery_status TEXT DEFAULT 'pending'")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self, directory):
        # A child override is a complete replacement, matching add-on profile catalogs.
        candidates = [
            "/".join(directory.split("/")[:n]) for n in range(1, len(directory.split("/")) + 1)
        ]
        with self.connect() as db:
            for candidate in reversed(candidates):
                row = db.execute(
                    "SELECT revision,payload FROM settings WHERE directory=?", (candidate,)
                ).fetchone()
                if row:
                    return row["revision"], Settings.model_validate_json(row["payload"])
        return 0, Settings()

    def save_settings(self, directory, revision, settings):
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT revision FROM settings WHERE directory=?", (directory,)
            ).fetchone()
            current = row[0] if row else 0
            if row is None:
                parts = directory.split("/")
                for length in range(len(parts) - 1, 0, -1):
                    inherited = db.execute(
                        "SELECT revision FROM settings WHERE directory=?",
                        ("/".join(parts[:length]),),
                    ).fetchone()
                    if inherited:
                        current = inherited[0]
                        break
            if revision != current:
                raise BrowserError(
                    "Settings changed; reload before saving", "revision_conflict", 409
                )
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?,?)",
                (directory, current + 1, settings.model_dump_json()),
            )
        return current + 1

    def create(self, session: Session, request_id, quota):
        with self.connect(write=True) as db:
            previous = db.execute(
                "SELECT payload FROM sessions WHERE owner=? AND request_id=?",
                (session.agent_session_id, request_id),
            ).fetchone()
            if previous:
                found = Session.model_validate_json(previous[0])
                if any(
                    getattr(found, key) != getattr(session, key)
                    for key in (
                        "mode",
                        "directory",
                        "workspace_ref",
                        "profile_id",
                        "launch_manifest_path",
                    )
                ):
                    raise BrowserError(
                        "Request ID was reused with different inputs", "request_conflict", 409
                    )
                return found, False
            active = [
                Session.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM sessions").fetchall()
            ]
            active = [item for item in active if item.state not in TERMINAL]
            if session.mode != "user_hosted" and (
                any(
                    item.agent_session_id == session.agent_session_id and item.mode != "user_hosted"
                    for item in active
                )
                or sum(
                    item.worker_id == session.worker_id and item.mode != "user_hosted"
                    for item in active
                )
                >= quota
            ):
                raise BrowserError("Hosted browser quota reached", "quota_exceeded", 409)
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    session.id,
                    session.directory,
                    session.agent_session_id,
                    request_id,
                    session.model_dump_json(),
                ),
            )
            self._audit(db, session.id, session.agent_session_id, "created", {"mode": session.mode})
        return session, True

    def get(self, session_id, directory=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise BrowserError("Browser session not found", "not_found", 404)
        result = Session.model_validate_json(row[0])
        if directory is not None and result.directory != directory:
            raise BrowserError("Browser session not found", "not_found", 404)
        return result

    def list(self, directory=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM sessions" + (" WHERE directory=?" if directory else ""),
                (directory,) if directory else (),
            ).fetchall()
        return [Session.model_validate_json(row[0]) for row in rows]

    def mutate(self, session_id, actor, operation, *, revision=None, kind="updated"):
        with self.connect(write=True) as db:
            row = db.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise BrowserError("Browser session not found", "not_found", 404)
            session = Session.model_validate_json(row[0])
            if revision is not None and revision != session.revision:
                raise BrowserError("Session changed; refresh and retry", "revision_conflict", 409)
            old_generation = session.control_generation
            operation(session)
            if session.control_generation != old_generation or session.state not in {
                "starting",
                "ready",
                "paused",
            }:
                self.interrupt_run(db, session_id, kind)
            session.revision += 1
            db.execute(
                "UPDATE sessions SET payload=? WHERE id=?", (session.model_dump_json(), session_id)
            )
            self._audit(db, session_id, actor, kind, {"generation": session.control_generation})
        return session

    def active(self, session: Session):
        if session.state in TERMINAL or session.state in {"closing", "cleanup_failed", "failed"}:
            raise BrowserError("Browser session is unavailable", "session_closed", 410)
        if self.now() >= session.expires_at:
            raise BrowserError("Browser session expired", "session_expired", 410)
        if self.now() >= session.last_activity_at + session.idle_seconds:
            raise BrowserError("Browser session is inactive", "session_idle", 410)

    def capability(self, session_id, role, principal, seconds):
        token = secrets.token_urlsafe(32)
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO capabilities VALUES(?,?,?,?,?,0)",
                (digest(token), session_id, role, principal, self.now() + seconds),
            )
        return token

    def authenticate(self, token, role=None, *, consume=False):
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM capabilities WHERE hash=?", (digest(token),)).fetchone()
            if (
                not row
                or row["used"]
                or self.now() >= row["expires_at"]
                or (role and row["role"] != role)
            ):
                raise BrowserError("Invitation or connection expired", "unauthorized", 401)
            self.active(self._get_in(db, row["session_id"]))
            if consume:
                db.execute("UPDATE capabilities SET used=1 WHERE hash=?", (digest(token),))
            return dict(row)

    @staticmethod
    def _get_in(db, session_id):
        row = db.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise BrowserError("Browser session not found", "not_found", 404)
        return Session.model_validate_json(row[0])

    def revoke_capabilities(self, session_id):
        with self.connect(write=True) as db:
            db.execute("DELETE FROM capabilities WHERE session_id=?", (session_id,))

    def set_private(self, session_id, payload):
        with self.connect(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO private_sessions VALUES(?,?)",
                (session_id, json.dumps(payload)),
            )

    def private(self, session_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM private_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def audit(self, session_id, actor, kind, payload):
        with self.connect(write=True) as db:
            self._audit(db, session_id, actor, kind, payload)

    def _audit(self, db, session_id, actor, kind, payload):
        db.execute(
            "INSERT INTO audit(session_id,at,actor,kind,payload) VALUES(?,?,?,?,?)",
            (session_id, self.now(), actor, kind, json.dumps(payload)),
        )

    def message(
        self, session_id, channel, actor, message_id, body, *, reply_to=None, annotation=None
    ):
        with self.connect(write=True) as db:
            existing = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if existing:
                if (
                    existing["session_id"] != session_id
                    or existing["actor"] != actor
                    or existing["body"] != body
                    or existing["channel"] != channel
                    or existing["reply_to"] != reply_to
                ):
                    raise BrowserError("Message identity conflict", "message_conflict", 409)
                return dict(existing)
            if reply_to is None:
                pending = db.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=? AND reply_to IS NULL AND status IN ('queued','dispatched')",
                    (session_id,),
                ).fetchone()[0]
                recent = db.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=? AND actor=? AND at>?",
                    (session_id, actor, self.now() - 60),
                ).fetchone()[0]
                if pending >= 50 or recent >= 20:
                    raise BrowserError(
                        "Message queue is full; wait for the agent to reply", "message_limit", 429
                    )
            if reply_to:
                previous = db.execute(
                    "SELECT * FROM messages WHERE id=? AND session_id=?", (reply_to, session_id)
                ).fetchone()
                if not previous or previous["channel"] != channel:
                    raise BrowserError("Reply must use the request's channel")
            db.execute(
                "INSERT INTO messages(id,session_id,channel,actor,body,at,reply_to,status,annotation) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    message_id,
                    session_id,
                    channel,
                    actor,
                    body,
                    self.now(),
                    reply_to,
                    "queued",
                    json.dumps(annotation) if annotation else None,
                ),
            )
            return dict(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def messages(self, session_id, channel="viewer", after=0):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM messages WHERE session_id=? AND channel=? AND seq>? ORDER BY seq LIMIT 200",
                    (session_id, channel, after),
                )
            ]

    def message_updates(self, session_id, channel="viewer"):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,status,delivery_status FROM messages WHERE session_id=? AND channel=? AND reply_to IS NULL ORDER BY seq DESC LIMIT 50",
                    (session_id, channel),
                )
            ]

    def message_delivery_batch(self, limit=50):
        """Claim a bounded rotating page, including across poller restarts."""
        with self.connect(write=True) as db:
            cursor = db.execute("SELECT after_seq FROM delivery_poll_state WHERE id=1").fetchone()
            after = cursor[0] if cursor else 0
            query = """SELECT * FROM messages WHERE status='dispatched' AND reply_to IS NULL
                       AND delivery_status IN ('queued','pending','delivery_unknown')"""
            rows = db.execute(query + " AND seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
            if after and len(rows) < limit:
                rows.extend(db.execute(
                    query + " AND seq<=? ORDER BY seq LIMIT ?", (after, limit - len(rows))
                ).fetchall())
            if rows:
                db.execute(
                    "INSERT INTO delivery_poll_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET after_seq=excluded.after_seq",
                    (rows[-1]["seq"],),
                )
            return rows

    def record_command(self, session_id, actor, command, *, run_id=None):
        fingerprint = digest(command.model_dump_json())
        with self.connect(write=True) as db:
            self._command_access(db, session_id, actor, run_id)
            previous = db.execute("SELECT * FROM commands WHERE id=?", (command.id,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint or previous["session_id"] != session_id:
                    raise BrowserError("Command identity conflict", "command_conflict", 409)
                raise BrowserError(
                    "Command was already submitted; observe before continuing",
                    "command_duplicate",
                    409,
                )
            db.execute(
                "INSERT INTO commands(id,session_id,actor,state,at,fingerprint,run_id,observing,action_metadata) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    command.id,
                    session_id,
                    actor,
                    "submitted",
                    self.now(),
                    fingerprint,
                    run_id,
                    int(command.observing),
                    json.dumps(command.audit()),
                ),
            )
            self._audit(db, session_id, actor, "browser_action", command.audit())

    def finish_command(self, command_id, state):
        with self.connect(write=True) as db:
            db.execute("UPDATE commands SET state=? WHERE id=?", (state, command_id))
            command = db.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if command and command["run_id"] and not command["observing"]:
                row = db.execute(
                    "SELECT payload FROM browser_runs WHERE id=?", (command["run_id"],)
                ).fetchone()
                if row:
                    value = json.loads(row[0])
                    step = {**json.loads(command["action_metadata"]), "outcome": state}
                    value["steps"] = [
                        s for s in value["steps"] if s.get("command_id") != command_id
                    ] + [step]
                    value["step_count"] = len(value["steps"])
                    self._write_run(db, value)

    def prune(self):
        with self.connect(write=True) as db:
            db.execute("DELETE FROM capabilities WHERE expires_at <= ?", (self.now(),))
            for session in self.list():
                if session.state not in TERMINAL:
                    continue
                days = self.settings(session.directory)[1].retention_days
                if self.now() < session.expires_at + days * 86400:
                    continue
                db.execute(
                    "DELETE FROM dispatches WHERE message_id IN (SELECT id FROM messages WHERE session_id=?)",
                    (session.id,),
                )
                for table in (
                    "audit",
                    "messages",
                    "commands",
                    "private_sessions",
                    "voice_requests",
                    "browser_runs",
                    "endpoint_features",
                ):
                    db.execute(f"DELETE FROM {table} WHERE session_id=?", (session.id,))
                db.execute("DELETE FROM sessions WHERE id=?", (session.id,))
