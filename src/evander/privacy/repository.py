from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, local
from typing import Iterator

_locks_guard = RLock()
_thread_locks: dict[Path, RLock] = {}
_held = local()


def _reset_after_fork():
    global _locks_guard, _thread_locks, _held
    _locks_guard = RLock()
    _thread_locks = {}
    _held = local()


os.register_at_fork(after_in_child=_reset_after_fork)


@contextmanager
def privacy_guard(runtime_dir: Path) -> Iterator[None]:
    """Acquire before any source-store lock, including opportunistic cleanup.

    Hold publication and a local destructive batch have one serial order across
    processes. Reentrant calls reuse the current thread's acquisition; forked
    children must acquire their own lock rather than inherit that bookkeeping.
    """
    runtime_dir = Path(runtime_dir).resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    with _locks_guard:
        thread_lock = _thread_locks.setdefault(runtime_dir, RLock())
    with thread_lock:
        if not hasattr(_held, "paths"):
            _held.paths = set()
        if runtime_dir in _held.paths:
            yield
            return
        fd = os.open(runtime_dir / ".privacy.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _held.paths.add(runtime_dir)
            yield
        finally:
            _held.paths.discard(runtime_dir)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class PrivacyRepository:
    """Minimal case/evidence store, independent of retained operational payloads."""

    def __init__(self, runtime_dir: Path):
        self.runtime_dir = Path(runtime_dir)
        self.path = self.runtime_dir / "privacy.db"
        with privacy_guard(self.runtime_dir):
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
            with self.connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS privacy_meta (
                        id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL
                    );
                    INSERT OR IGNORE INTO privacy_meta VALUES (1,0);
                    CREATE TABLE IF NOT EXISTS privacy_records (
                        kind TEXT NOT NULL, id TEXT NOT NULL, scope TEXT NOT NULL,
                        body TEXT NOT NULL, PRIMARY KEY(kind,id)
                    );
                    CREATE INDEX IF NOT EXISTS privacy_scope ON privacy_records(scope,kind);
                    CREATE TABLE IF NOT EXISTS privacy_cursors (
                        name TEXT PRIMARY KEY, position INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS privacy_access_events (
                        id INTEGER PRIMARY KEY, scope TEXT NOT NULL, operation TEXT NOT NULL,
                        record_id TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL
                    );
                """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def generation(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT generation FROM privacy_meta WHERE id=1").fetchone()[0])

    def get(self, kind: str, record_id: str, scope: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT body FROM privacy_records WHERE kind=? AND id=? AND scope=?",
                (kind, record_id, scope),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def list(self, kind: str, scope: str | None = None, *, limit: int = 1000) -> list[dict]:
        with self.connect() as db:
            if scope is None:
                rows = db.execute(
                    "SELECT body FROM privacy_records WHERE kind=? ORDER BY rowid DESC LIMIT ?",
                    (kind, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT body FROM privacy_records WHERE kind=? AND scope=? "
                    "ORDER BY rowid DESC LIMIT ?",
                    (kind, scope, limit),
                ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def put(self, kind: str, record_id: str, scope: str, body: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO privacy_records VALUES (?,?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body "
                "WHERE privacy_records.scope=excluded.scope",
                (kind, record_id, scope, json.dumps(body, sort_keys=True, allow_nan=False)),
            )
            db.execute("UPDATE privacy_meta SET generation=generation+1 WHERE id=1")

    def held(self, dataset: str, record_id: str) -> bool:
        # Do not use a paginated list: older holds still protect data.
        with self.connect() as db:
            for row in db.execute("SELECT body FROM privacy_records WHERE kind='hold'"):
                hold = json.loads(row[0])
                if (
                    hold["status"] == "active"
                    and hold["dataset"] == dataset
                    and record_id in hold["record_ids"]
                ):
                    return True
        return False

    def case_has_unresolved(self, kind: str, scope: str, case_id: str, completed: set[str]) -> bool:
        with self.connect() as db:
            placeholders = ",".join("?" for _ in completed)
            return (
                db.execute(
                    "SELECT 1 FROM privacy_records WHERE kind=? AND scope=? "
                    "AND json_extract(body,'$.case_id')=? "
                    f"AND json_extract(body,'$.status') NOT IN ({placeholders}) LIMIT 1",
                    (kind, scope, case_id, *sorted(completed)),
                ).fetchone()
                is not None
            )

    def maintenance_page(self, limit: int) -> list[tuple[int, dict]]:
        with self.connect() as db:
            saved = db.execute("SELECT position FROM privacy_cursors WHERE name='jobs'").fetchone()
            position = int(saved[0]) if saved else 0
            rows = db.execute(
                "SELECT rowid, body FROM privacy_records WHERE kind='job' AND rowid>? "
                "ORDER BY rowid LIMIT ?",
                (position, limit),
            ).fetchall()
            if not rows and position:
                rows = db.execute(
                    "SELECT rowid, body FROM privacy_records WHERE kind='job' ORDER BY rowid LIMIT ?",
                    (limit,),
                ).fetchall()
            return [(int(row[0]), json.loads(row[1])) for row in rows]

    def advance_maintenance(self, position: int):
        with self.connect() as db:
            db.execute(
                "INSERT INTO privacy_cursors VALUES ('jobs',?) "
                "ON CONFLICT(name) DO UPDATE SET position=excluded.position",
                (position,),
            )

    def record_access(self, scope: str, operation: str, record_id: str, actor: str):
        from datetime import UTC, datetime

        if not actor.strip():
            raise ValueError("An accountable reader is required")
        with self.connect() as db:
            db.execute(
                "INSERT INTO privacy_access_events(scope,operation,record_id,actor,occurred_at) "
                "VALUES (?,?,?,?,?)",
                (scope, operation, record_id, actor, datetime.now(UTC).isoformat()),
            )


def held_without_initializing(runtime_dir: Path, dataset: str, record_id: str) -> bool:
    """Used under privacy_guard by existing cleanup; never bootstraps privacy state."""
    path = Path(runtime_dir) / "privacy.db"
    if not path.exists():
        return False
    db = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        for (raw,) in db.execute("SELECT body FROM privacy_records WHERE kind='hold'"):
            hold = json.loads(raw)
            if (
                hold["status"] == "active"
                and hold["dataset"] == dataset
                and record_id in hold["record_ids"]
            ):
                return True
        return False
    finally:
        db.close()
