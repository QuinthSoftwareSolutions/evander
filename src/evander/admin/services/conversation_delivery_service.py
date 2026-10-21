"""Native message delivery shared by workflows and authenticated chat adapters.

Receipts contain identities and hashes, never user message bodies. A sending receipt
pins retries to the original worker job; uncertainty must never become a new turn.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from evander.agent_threads import usable_agent_thread_id
from evander.conversation_context import ConversationContextStore, execution_context_generation

ACTIVE = {"created", "starting", "running"}
DELIVERED = {"steered", "started"}
TERMINAL_DELIVERY = DELIVERED | {"cancelled"}


class ConversationDeliveryStore:
    def __init__(self, runtime_dir):
        self.path = Path(runtime_dir) / "agent-message-deliveries.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries(
                session_id TEXT, delivery_id TEXT, fingerprint TEXT, payload TEXT,
                updated REAL, PRIMARY KEY(session_id,delivery_id))""")
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @contextmanager
    def delivery_lock(self, session_id, delivery_id):
        # Different admin/orchestrator processes must agree on the first-send
        # decision. Never hold a database transaction while waiting for the worker.
        directory = self.path.parent / "agent-message-delivery-locks"
        directory.mkdir(mode=0o700, exist_ok=True)
        key = hashlib.sha256(json.dumps([session_id, delivery_id]).encode()).hexdigest()
        with (directory / (key + ".lock")).open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def get(self, session_id, delivery_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM deliveries WHERE session_id=? AND delivery_id=?",
                (session_id, delivery_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, session_id, delivery_id, fingerprint, payload, *, claim=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT fingerprint,payload FROM deliveries WHERE session_id=? AND delivery_id=?",
                (session_id, delivery_id),
            ).fetchone()
            if row:
                current = json.loads(row[1])
                if current.get("delivery_status") == "cancelled":
                    return current
                if row[0] != fingerprint:
                    raise ValueError("Message delivery identity conflict")
                if claim or current.get("delivery_status") in DELIVERED:
                    return current
            db.execute(
                "INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?)",
                (session_id, delivery_id, fingerprint, json.dumps(payload), time.time()),
            )
        return payload

    def cancel(self, session_id, delivery_id):
        with self.delivery_lock(session_id, delivery_id), self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM deliveries WHERE session_id=? AND delivery_id=?",
                (session_id, delivery_id),
            ).fetchone()
            receipt = json.loads(row[0]) if row else {}
            if receipt.get("delivery_status") not in DELIVERED:
                receipt["delivery_status"] = "cancelled"
                db.execute(
                    "INSERT INTO deliveries VALUES(?,?,?,?,?) ON CONFLICT(session_id,delivery_id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
                    (session_id, delivery_id, "cancelled", json.dumps(receipt), time.time()),
                )
            return receipt


def active_conversation(backend, session_id):
    records = backend.repository.list_active_by_session(session_id, ACTIVE)
    active = []
    for record in records:
        backend.inspect(record.execution_id)
        current = backend.repository.get(record.execution_id)
        if current and current.last_status in ACTIVE:
            active.append(current)
    return active[0] if len(active) == 1 else None


class ConversationDeliveryService:
    def __init__(self, runtime_dir, backend):
        self.store = ConversationDeliveryStore(runtime_dir)
        self.backend = backend

    def try_steer(
        self,
        *,
        session_id,
        delivery_id,
        prompt,
        conversation_key=None,
        thread_id=None,
        model=None,
        approval_lease_id=None,
        turn_settings=None,
        allow_native=True,
    ):
        fingerprint = hashlib.sha256(
            json.dumps([session_id, conversation_key, thread_id, prompt]).encode()
        ).hexdigest()
        saved = self.store.get(session_id, delivery_id)
        if saved:
            if saved["delivery_status"] == "cancelled":
                return saved
            # Validate even when returning an earlier successful delivery.
            saved = self.store.save(session_id, delivery_id, fingerprint, saved, claim=True)
            if saved["delivery_status"] in {"steered", "started", "cancelled"}:
                return saved
            if saved.get("worker_job_id"):
                saved.setdefault("conversation_key", conversation_key)
                saved.setdefault("context_generation", 0)
                return self._deliver(session_id, delivery_id, fingerprint, saved, prompt)
        from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

        queued = AgentPromptQueueStore(
            self.store.path.parent / "queued-agent-prompts.json"
        ).read_all()
        current = next(
            (
                r
                for r in queued.values()
                if r.session_id == session_id and r.dispatch_id == delivery_id
            ),
            None,
        )
        if any(
            r.session_id == session_id
            and r.dispatch_id != delivery_id
            and (
                (thread_id and r.resume_thread_id == thread_id)
                or (conversation_key and r.metadata.get("conversation_key") == conversation_key)
            )
            and (
                current is None
                or (r.created_at, r.dispatch_id or "") < (current.created_at, delivery_id)
            )
            for r in queued.values()
        ):
            return {"delivery_status": "queued", "dispatch_id": delivery_id}
        state = ConversationContextStore(self.store.path.parent).get(session_id, conversation_key)
        if not allow_native and state is None:
            return None
        record = active_conversation(self.backend, session_id)
        if record is None:
            return None
        meta = record.metadata
        if meta.get("dispatch_id") == delivery_id:
            # Recover a launch whose worker acceptance outlived the host reply.
            # This is already-running work, even if another reset has arrived.
            native = usable_agent_thread_id(
                meta.get("codex_subscription_thread_id"), record.execution_id, meta.get("dispatch_id")
            )
            if state and native:
                contexts = ConversationContextStore(self.store.path.parent)
                with contexts.lock(session_id, conversation_key):
                    contexts.accept_thread(
                        session_id, conversation_key, execution_context_generation(record), native
                    )
            return self.mark_started(
                session_id=session_id,
                delivery_id=delivery_id,
                prompt=prompt,
                conversation_key=conversation_key,
                thread_id=thread_id,
                execution_id=record.execution_id,
            )
        if not allow_native or (state and (
            state["pending"] or execution_context_generation(record) != state["generation"]
        )):
            return None
        native_thread = meta.get("codex_subscription_thread_id")
        native_turn = meta.get("codex_subscription_turn_id")
        same = (
            (thread_id == native_thread)
            if thread_id
            else (bool(conversation_key) and conversation_key == meta.get("conversation_key"))
        )
        if not same or not native_thread or not native_turn:
            return None
        if model and meta.get("model") != model:
            return None
        if any(meta.get(key) != value for key, value in (turn_settings or {}).items()):
            return None
        if approval_lease_id and meta.get("mcp_tool_approval_lease_id") != approval_lease_id:
            return None
        client = self.backend._client_for_mapping(record)
        if (client.health().get("capabilities") or {}).get("codex_native_steering", 0) < 1:
            return None
        saved = self.store.save(
            session_id,
            delivery_id,
            fingerprint,
            {
                "delivery_status": "pending",
                "dispatch_id": delivery_id,
                "execution_id": record.execution_id,
                "worker_job_id": record.worker_job_id,
                "worker_url": record.worker_url.rstrip("/"),
                "steered_into_dispatch_id": meta.get("dispatch_id"),
                "thread_id": native_thread,
                "conversation_key": conversation_key,
                "context_generation": execution_context_generation(record),
                "turn_id": native_turn,
            },
            claim=True,
        )
        return self._deliver(session_id, delivery_id, fingerprint, saved, prompt)

    def _deliver(self, session_id, delivery_id, fingerprint, saved, prompt):
        if saved.get("delivery_status") in TERMINAL_DELIVERY:
            return saved
        try:
            record = self.backend.repository.get(saved["execution_id"])
            if (
                record is None
                or record.worker_job_id != saved["worker_job_id"]
                or record.worker_url.rstrip("/") != saved["worker_url"]
                or record.session_id != session_id
            ):
                raise RuntimeError("Original steering execution unavailable")
            client = self.backend._client_for_mapping(record)
            wire_id = (
                "message-" + hashlib.sha256(f"{session_id}:{delivery_id}".encode()).hexdigest()
            )
            receipt = client.steering_receipt(record.worker_job_id, wire_id)
        except Exception:
            # HTTP loss and worker loss are not evidence that input was rejected.
            receipt = {"status": "delivery_unknown"}

        # The lookup is read-only and may stall. Cancellation can win during it;
        # recheck under the shared lock before writing any new native input.
        with self.store.delivery_lock(session_id, delivery_id):
            current = self.store.get(session_id, delivery_id)
            if current and current.get("delivery_status") in TERMINAL_DELIVERY:
                return current
            result = dict(saved)
            try:
                if receipt is None:
                    contexts = ConversationContextStore(self.store.path.parent)
                    key = saved.get("conversation_key") or ""
                    with contexts.lock(session_id, key):
                        state = contexts.get(session_id, key)
                        if state and (state["pending"] or int(saved.get("context_generation", 0)) != state["generation"]):
                            receipt = {"status": "rejected"}
                        else:
                            receipt = client.steer(
                                record.worker_job_id,
                                {
                                    "session_id": session_id,
                                    "delivery_id": wire_id,
                                    "prompt": prompt,
                                    "expected_thread_id": saved["thread_id"],
                                    "expected_turn_id": saved["turn_id"],
                                },
                            )
                result["delivery_status"] = {
                    "accepted": "steered",
                    "rejected": "queued",
                    "sending": "pending",
                }.get(receipt["status"], "delivery_unknown")
            except Exception:
                result["delivery_status"] = "delivery_unknown"
            result = self.store.save(session_id, delivery_id, fingerprint, result)
        if result["delivery_status"] == "queued":
            return None
        return result

    def mark_started(
        self, *, session_id, delivery_id, prompt, conversation_key, thread_id, execution_id
    ):
        fingerprint = hashlib.sha256(
            json.dumps([session_id, conversation_key, thread_id, prompt]).encode()
        ).hexdigest()
        return self.store.save(
            session_id,
            delivery_id,
            fingerprint,
            {
                "delivery_status": "started",
                "dispatch_id": delivery_id,
                "execution_id": execution_id,
            },
        )
