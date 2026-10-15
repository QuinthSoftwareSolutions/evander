"""Core, session-owned checkout bindings and durable Git submission receipts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


class GitSubmissionStore:
    def __init__(self, runtime_dir: str | Path):
        self.path = Path(runtime_dir) / "git-submissions.db"

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create with protected permissions, including before SQLite opens it.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS checkouts (
                    checkout_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    managed_repo_id TEXT NOT NULL, active INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS checkouts_session ON checkouts(session_id);
                CREATE TABLE IF NOT EXISTS receipts (
                    request_id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
            """)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def register(
        self,
        *,
        session_id: str,
        managed_repo_id: str,
        guest_path: str,
        owner_kind: str = "session",
        owner_id: str = "",
        generation: str = "",
        fixed_branch: str | None = None,
        prepared_base: str | None = None,
        base_branch: str | None = None,
        destination_branch: str | None = None,
        **metadata,
    ) -> str:
        path = PurePosixPath(guest_path)
        # VM clones historically live directly below /home/agent; container
        # clones and task worktrees use its repos/ and worktrees/ subdirectories.
        if (
            ".." in path.parts
            or not path.is_relative_to("/home/agent")
            or path == PurePosixPath("/home/agent")
        ):
            raise ValueError("Git checkout must be a managed guest repository or worktree")
        identity = json.dumps(
            [session_id, managed_repo_id, str(path), owner_kind, owner_id, generation],
            separators=(",", ":"),
        )
        checkout_id = "gc_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        payload = dict(
            metadata,
            session_id=session_id,
            managed_repo_id=managed_repo_id,
            guest_path=str(path),
            owner_kind=owner_kind,
            owner_id=owner_id,
            generation=generation,
            fixed_branch=fixed_branch,
            prepared_base=prepared_base,
            base_branch=base_branch,
            destination_branch=destination_branch,
            checkout_id=checkout_id,
        )
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkouts VALUES (?,?,?,?,?)",
                (checkout_id, session_id, managed_repo_id, 1, json.dumps(payload)),
            )
        return checkout_id

    def list(self, session_id: str) -> list[dict]:
        with self.connection() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT payload FROM checkouts WHERE session_id=? AND active=1 ORDER BY checkout_id",
                    (session_id,),
                )
            ]

    def retire(self, checkout_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE checkouts SET active=0 WHERE checkout_id=?", (checkout_id,))

    def retire_repository(self, session_id: str, managed_repo_id: str) -> None:
        if not self.path.exists():
            return
        with self.connection() as conn:
            conn.execute(
                "UPDATE checkouts SET active=0 WHERE session_id=? AND managed_repo_id=?",
                (session_id, managed_repo_id),
            )

    def save_receipt(self, request_id: str, checkout_id: str, receipt: dict) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO receipts VALUES (?,?,?)",
                (request_id, checkout_id, json.dumps(receipt)),
            )
