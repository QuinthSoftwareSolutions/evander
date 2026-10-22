"""Transactional automation leases and metadata-only receipts. Never resume a run."""

from __future__ import annotations

import json
import uuid

from .models import BrowserError, Session


class RunStore:
    def init_runs(self, db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS browser_runs(
                id TEXT PRIMARY KEY, session_id TEXT, actor TEXT, request_id TEXT,
                fingerprint TEXT, status TEXT, deadline REAL, payload TEXT,
                UNIQUE(session_id,actor,request_id));
            CREATE UNIQUE INDEX IF NOT EXISTS browser_run_active
                ON browser_runs(session_id) WHERE status='running';
            CREATE TABLE IF NOT EXISTS endpoint_features(session_id TEXT PRIMARY KEY, payload TEXT);
        """)
        db.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in db.execute("PRAGMA table_info(commands)")}
        for column, declaration in (
            ("run_id", "TEXT"),
            ("observing", "INTEGER DEFAULT 0"),
            ("action_metadata", "TEXT"),
        ):
            if column not in columns:
                db.execute(f"ALTER TABLE commands ADD COLUMN {column} {declaration}")

    def endpoint_features(self, session_id, features=None):
        with self.connect(write=features is not None) as db:
            if features is not None:
                allowed = {"fast_execution": 1} if features.get("fast_execution") == 1 else {}
                db.execute(
                    "INSERT OR REPLACE INTO endpoint_features VALUES(?,?)",
                    (session_id, json.dumps(allowed)),
                )
                return allowed
            row = db.execute(
                "SELECT payload FROM endpoint_features WHERE session_id=?", (session_id,)
            ).fetchone()
            return json.loads(row[0]) if row else {}

    def _write_run(self, db, value):
        db.execute(
            "UPDATE browser_runs SET status=?,payload=? WHERE id=?",
            (value["status"], json.dumps(value), value["id"]),
        )

    def _end_run(self, db, row, reason, status="cancelled"):
        value = json.loads(row["payload"])
        pending = db.execute(
            "SELECT id,action_metadata FROM commands WHERE run_id=? AND observing=0 AND state IN ('submitted','unknown')",
            (value["id"],),
        ).fetchall()
        for command in pending:
            if not any(step.get("command_id") == command["id"] for step in value["steps"]):
                value["steps"].append(
                    {**json.loads(command["action_metadata"] or "{}"), "outcome": "unknown"}
                )
        # Retire pending receipts with the run, preserving their unknown outcome
        # and identity. Already-issued input may still finish, but must never be
        # replayed or block a later authorized objective.
        db.execute(
            "UPDATE commands SET state='unknown' WHERE run_id=? AND state='submitted'",
            (value["id"],),
        )
        value["step_count"] = len(value["steps"])
        value.update(
            status="outcome_unknown" if pending else status, reason=reason, finished_at=self.now()
        )
        self._write_run(db, value)
        return value

    def interrupt_run(self, db, session_id, reason):
        row = db.execute(
            "SELECT * FROM browser_runs WHERE session_id=? AND status='running'", (session_id,)
        ).fetchone()
        if row:
            self._end_run(db, row, reason)

    def _recover_runs(self, db, session_id):
        row = db.execute(
            "SELECT * FROM browser_runs WHERE session_id=? AND status='running' AND deadline<=?",
            (session_id, self.now()),
        ).fetchone()
        if row:
            self._end_run(db, row, "Run deadline elapsed; inspect before continuing", "needs_pa")

    def run_status(self, session_id):
        with self.connect(write=True) as db:
            self._recover_runs(db, session_id)
            rows = db.execute(
                "SELECT payload FROM browser_runs WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                (session_id,),
            ).fetchall()
            recent = json.loads(rows[0][0]) if rows else None
            return {
                "active_run": recent if recent and recent["status"] == "running" else None,
                "last_run": recent if recent and recent["status"] != "running" else None,
            }

    def run_get(self, session_id, run_id):
        with self.connect(write=True) as db:
            self._recover_runs(db, session_id)
            row = db.execute(
                "SELECT payload FROM browser_runs WHERE session_id=? AND id=?", (session_id, run_id)
            ).fetchone()
            if not row:
                raise BrowserError("Browser run not found", "not_found", 404)
            return json.loads(row[0])

    def previous_run(self, session_id, actor, request_id, fingerprint):
        with self.connect(write=True) as db:
            self._recover_runs(db, session_id)
            row = db.execute(
                "SELECT * FROM browser_runs WHERE session_id=? AND actor=? AND request_id=?",
                (session_id, actor, request_id),
            ).fetchone()
            if row and row["fingerprint"] != fingerprint:
                raise BrowserError("Run request identity conflict", "request_conflict", 409)
            return json.loads(row["payload"]) if row else None

    def claim_run(self, session_id, actor, request, fingerprint, *, seconds, max_steps):
        with self.connect(write=True) as db:
            self._recover_runs(db, session_id)
            previous = db.execute(
                "SELECT * FROM browser_runs WHERE session_id=? AND actor=? AND request_id=?",
                (session_id, actor, request.request_id),
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise BrowserError("Run request identity conflict", "request_conflict", 409)
                return json.loads(previous["payload"]), False
            session = Session.model_validate_json(
                db.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
            )
            self.active(session)
            if session.control_generation != request.generation or session.controller != actor:
                raise BrowserError("Browser control changed; observe again", "stale_control", 409)
            self._command_access(db, session_id, actor, None)
            # A submitted direct command may already be on the wire. Do not overlap it.
            if db.execute(
                "SELECT 1 FROM commands WHERE session_id=? AND state='submitted'",
                (session_id,),
            ).fetchone():
                raise BrowserError("Another browser command is pending", "automation_busy", 409)
            value = dict(
                id=uuid.uuid4().hex,
                session_id=session_id,
                status="running",
                reason="",
                generation=request.generation,
                started_at=self.now(),
                deadline_at=min(self.now() + seconds, session.expires_at),
                max_steps=max_steps,
                steps=[],
                step_count=0,
            )
            db.execute(
                "INSERT INTO browser_runs VALUES(?,?,?,?,?,?,?,?)",
                (
                    value["id"],
                    session_id,
                    actor,
                    request.request_id,
                    fingerprint,
                    "running",
                    value["deadline_at"],
                    json.dumps(value),
                ),
            )
            self._audit(db, session_id, actor, "run_started", {"run_id": value["id"]})
            return value, True

    def _command_access(self, db, session_id, actor, run_id):
        self._recover_runs(db, session_id)
        row = db.execute(
            "SELECT * FROM browser_runs WHERE session_id=? AND status='running'", (session_id,)
        ).fetchone()
        if run_id:
            if not row or row["id"] != run_id or row["actor"] != actor:
                raise BrowserError("Browser run has ended", "run_ended", 409)
        elif row:
            raise BrowserError("A browser run is active; wait or cancel it", "automation_busy", 409)

    def command_access(self, session_id, actor, run_id=None):
        with self.connect(write=True) as db:
            self._command_access(db, session_id, actor, run_id)

    def update_run(self, session_id, run_id, *, step=None, status=None, reason="", decision=None):
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT * FROM browser_runs WHERE session_id=? AND id=?", (session_id, run_id)
            ).fetchone()
            value = json.loads(row["payload"])
            if step:
                value["steps"].append(step)
                value["step_count"] = len(value["steps"])
            # A late return must not erase a cancellation or uncertainty receipt.
            if value["status"] == "running" and status:
                value.update(status=status, reason=reason, finished_at=self.now())
                if decision is not None:
                    value["decision"] = decision
            self._write_run(db, value)
            return value

    def cancel_run(self, session_id, actor, run_id=None):
        with self.connect(write=True) as db:
            if run_id is None:
                row = db.execute(
                    "SELECT * FROM browser_runs WHERE session_id=? AND status='running'",
                    (session_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM browser_runs WHERE session_id=? AND id=?", (session_id, run_id)
                ).fetchone()
                if not row:
                    raise BrowserError("Browser run not found", "not_found", 404)
                if row["status"] != "running":
                    return json.loads(row["payload"])
            session = Session.model_validate_json(
                db.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
            )
            if session.state in {"starting", "ready", "paused", "disconnected"}:
                session.controller, session.state = None, "paused"
                session.control_generation += 1
                session.revision += 1
                db.execute(
                    "UPDATE sessions SET payload=? WHERE id=?",
                    (session.model_dump_json(), session_id),
                )
            # An unnamed UI Stop fences input even if the previous run just
            # finished and a new run is about to acquire its lease.
            value = (
                self._end_run(db, row, "Stopped; do not automatically resume")
                if row
                else {
                    "id": None,
                    "session_id": session_id,
                    "status": "cancelled",
                    "reason": "Agent input paused; no active run",
                    "steps": [],
                    "step_count": 0,
                }
            )
            self._audit(db, session_id, actor, "run_cancelled", {"run_id": value["id"]})
            return value
