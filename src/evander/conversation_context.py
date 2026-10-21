"""Durable conversation reset boundaries shared by dispatch and message history.

Only explicitly reset conversations have a canonical context row. Admitted
launches retain receipts so accepted or uncertain retries keep their original
context.
Context generations, rather than manifest snapshots, own the resume pointer
after the first reset.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


class ConversationContextStore:
    def __init__(self, runtime_dir: Path | str):
        self.runtime_dir = Path(runtime_dir)
        self.path = self.runtime_dir / "agent-message-deliveries.db"

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            # Keep the legacy origin columns for existing databases and receipts.
            # Reset identity and authorization use only the session/conversation.
            db.execute("""CREATE TABLE IF NOT EXISTS conversation_contexts(
                session_id TEXT NOT NULL, conversation_key TEXT NOT NULL,
                generation INTEGER NOT NULL, pending INTEGER NOT NULL,
                thread_id TEXT, communication_target TEXT NOT NULL DEFAULT '',
                history_after TEXT, launch_dispatch_id TEXT,
                requested_at TEXT NOT NULL,
                PRIMARY KEY(session_id, conversation_key))""")
            db.execute("""CREATE TABLE IF NOT EXISTS context_reset_receipts(
                session_id TEXT NOT NULL, conversation_key TEXT NOT NULL,
                request_id TEXT NOT NULL, communication_target TEXT NOT NULL DEFAULT '',
                generation INTEGER NOT NULL,
                PRIMARY KEY(session_id, conversation_key, request_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS context_launch_receipts(
                session_id TEXT NOT NULL, conversation_key TEXT NOT NULL,
                dispatch_id TEXT NOT NULL, generation INTEGER NOT NULL,
                thread_id TEXT, resume_mode TEXT NOT NULL, history_after TEXT,
                PRIMARY KEY(session_id, conversation_key, dispatch_id))""")
        self.path.chmod(0o600)

    @contextmanager
    def lock(self, session_id: str, conversation_key: str):
        directory = self.runtime_dir / "agent-context-locks"
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        key = hashlib.sha256(json.dumps([session_id, conversation_key]).encode()).hexdigest()
        with (directory / (key + ".lock")).open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def get(self, session_id: str, conversation_key: str) -> dict[str, Any] | None:
        if not self.path.exists() or not session_id or not conversation_key:
            return None
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_contexts'"
            ).fetchone():
                return None
            row = db.execute(
                "SELECT * FROM conversation_contexts WHERE session_id=? AND conversation_key=?",
                (session_id, conversation_key),
            ).fetchone()
            return dict(row) if row is not None else None

    def request_reset(self, *, session_id, conversation_key, request_id):
        self._initialize()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute(
                "SELECT * FROM context_reset_receipts WHERE session_id=? AND conversation_key=? AND request_id=?",
                (session_id, conversation_key, request_id),
            ).fetchone()
            if receipt:
                return {
                    "success": True,
                    "session_id": session_id,
                    "conversation_key": conversation_key,
                    "generation": receipt["generation"],
                    "pending": True,
                    "duplicate": True,
                }
            previous = db.execute(
                "SELECT generation FROM conversation_contexts WHERE session_id=? AND conversation_key=?",
                (session_id, conversation_key),
            ).fetchone()
            generation = int(previous["generation"]) + 1 if previous else 1
            now = datetime.now(UTC).isoformat()
            db.execute(
                """INSERT INTO conversation_contexts
                (session_id,conversation_key,generation,pending,thread_id,communication_target,requested_at)
                VALUES(?,?,?,1,NULL,?,?)
                ON CONFLICT(session_id,conversation_key) DO UPDATE SET
                generation=excluded.generation,pending=1,thread_id=NULL,
                launch_dispatch_id=NULL,requested_at=excluded.requested_at""",
                (session_id, conversation_key, generation, "", now),
            )
            db.execute(
                "INSERT INTO context_reset_receipts VALUES(?,?,?,?,?)",
                (session_id, conversation_key, request_id, "", generation),
            )
        return {
            "success": True,
            "session_id": session_id,
            "conversation_key": conversation_key,
            "generation": generation,
            "pending": True,
            "duplicate": False,
        }

    def begin_launch(self, session_id: str, conversation_key: str, dispatch_id: str):
        """Called only after admission confirms earlier work has finished."""
        state = self.get(session_id, conversation_key)
        if state is None:
            return None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM conversation_contexts WHERE session_id=? AND conversation_key=?",
                (session_id, conversation_key),
            ).fetchone()
            state = dict(row)
            if state["pending"] and state["launch_dispatch_id"] != dispatch_id:
                state["history_after"] = datetime.now(UTC).isoformat()
                state["launch_dispatch_id"] = dispatch_id
                db.execute(
                    """UPDATE conversation_contexts SET history_after=?,launch_dispatch_id=?
                    WHERE session_id=? AND conversation_key=?""",
                    (state["history_after"], dispatch_id, session_id, conversation_key),
                )
        return state

    def accept_thread(self, session_id, conversation_key, generation, thread_id):
        if not isinstance(thread_id, str) or not thread_id.strip():
            return False
        if self.get(session_id, conversation_key) is None:
            return True
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE conversation_contexts SET thread_id=?,pending=0
                WHERE session_id=? AND conversation_key=? AND generation=?
                AND (pending=0 OR launch_dispatch_id IS NOT NULL)""",
                (thread_id, session_id, conversation_key, int(generation or 0)),
            )
            return cursor.rowcount == 1

    def filter_history(
        self,
        messages: Iterable[Any],
        *,
        session_id: str,
        conversation_key: str,
        classification: bool = False,
    ):
        state = self.get(session_id, conversation_key)
        values = list(messages)
        if state is None:
            return values
        if classification and state["pending"]:
            return []
        boundary = state["history_after"]
        if not boundary:
            return values
        cutoff = datetime.fromisoformat(boundary)
        result = []
        for message in values:
            created_at = (
                message.get("created_at") if isinstance(message, dict) else message.created_at
            )
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if isinstance(created_at, datetime) and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at is not None and created_at > cutoff:
                result.append(message)
        return result


def execution_context_generation(record: Any) -> int:
    for name in ("outputs", "resolved_config", "input_payload", "metadata"):
        source = getattr(record, name, None)
        if isinstance(source, dict) and source.get("context_generation") is not None:
            return int(source["context_generation"])
    return 0


def launch_context(
    runtime_dir,
    session_id,
    conversation_key,
    dispatch_id,
    *,
    resume_thread_id=None,
    resume_mode="new_context",
):
    """Pin an admitted launch before sending it; retries retain its original context.

    A later reset must not rebind an uncertain worker request to another
    generation, even if the host lost the acceptance response before saving its
    worker mapping. Only a confirmed no-job rejection can release this binding.
    """
    if runtime_dir is None or not session_id or not conversation_key:
        return None
    store = ConversationContextStore(runtime_dir)
    # Resumer-only adapters may have no persisted runtime. With no directory,
    # neither an existing session nor a reset boundary can be stored here.
    if not store.runtime_dir.is_dir():
        return None
    with store.lock(session_id, conversation_key):
        store._initialize()
        with store._connect() as db:
            receipt = db.execute(
                "SELECT * FROM context_launch_receipts WHERE session_id=? AND conversation_key=? AND dispatch_id=?",
                (session_id, conversation_key, dispatch_id),
            ).fetchone()
        if receipt:
            return {**dict(receipt), "pending": receipt["resume_mode"] == "new_context"}
        state = store.begin_launch(session_id, conversation_key, dispatch_id)
        generation = int(state["generation"]) if state else 0
        if state:
            resume_thread_id = state["thread_id"]
            resume_mode = (
                "new_context" if state["pending"] or not resume_thread_id else "same_context"
            )
        with store._connect() as db:
            db.execute(
                "INSERT INTO context_launch_receipts VALUES(?,?,?,?,?,?,?)",
                (
                    session_id,
                    conversation_key,
                    dispatch_id,
                    generation,
                    resume_thread_id,
                    resume_mode,
                    state["history_after"] if state else None,
                ),
            )
        return {
            **(state or {}),
            "generation": generation,
            "thread_id": resume_thread_id,
            "resume_mode": resume_mode,
            "pending": resume_mode == "new_context",
        }


def reject_launch_context(runtime_dir, session_id, conversation_key, dispatch_id, *, generation):
    """Release a binding only after the worker confirms it created no job.

    Call before releasing launch admission claims. Accepted or uncertain launches
    must keep their receipt. The generation check prevents an older rejection
    from releasing a later reset's launch; the delivery identity is unchanged.
    """
    if runtime_dir is None or not session_id or not conversation_key or generation is None:
        return False
    store = ConversationContextStore(runtime_dir)
    if not store.path.exists():
        return False
    with store.lock(session_id, conversation_key):
        with store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            deleted = db.execute(
                """DELETE FROM context_launch_receipts
                WHERE session_id=? AND conversation_key=? AND dispatch_id=? AND generation=?""",
                (session_id, conversation_key, dispatch_id, generation),
            ).rowcount
            if deleted:
                # No turn started. Capture the effective cutoff again at retry,
                # without clearing a newer reset or an already observed thread.
                db.execute(
                    """UPDATE conversation_contexts SET launch_dispatch_id=NULL
                    WHERE session_id=? AND conversation_key=? AND generation=?
                    AND pending=1 AND launch_dispatch_id=?""",
                    (session_id, conversation_key, generation, dispatch_id),
                )
            return bool(deleted)


@contextmanager
def thread_write_guard(runtime_dir, session_id, conversation_key, generation, thread_id):
    if not isinstance(thread_id, str) or not thread_id.strip():
        yield False
        return
    if runtime_dir is None or not session_id or not conversation_key:
        yield True
        return
    store = ConversationContextStore(runtime_dir)
    if not store.runtime_dir.is_dir():
        yield True
        return
    # Lock before checking for a canonical row: a conversation's first reset
    # can race with a generation-zero callback from the existing turn.
    with store.lock(session_id, conversation_key):
        yield store.accept_thread(session_id, conversation_key, generation, thread_id)
