"""Prospective, credential-free subscription counters and durable worker receipts.

Only live App Server notifications enter this journal. It never reads session
histories, imports old jobs, or performs recovery at worker startup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Callable

from pydantic import BaseModel, Field

from evander.model_usage.models import ModelInferenceUsageEvent

logger = logging.getLogger(__name__)


class WorkerUsagePage(BaseModel):
    events: list[ModelInferenceUsageEvent]
    next_cursor: int = Field(ge=0)
    has_more: bool
    complete: bool = False


class WorkerUsageJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS usage_receipts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_receipts_job ON usage_receipts(job_id, sequence);
            """)

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
            yield connection

    def append(self, job_id: str, event: ModelInferenceUsageEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO usage_receipts(event_id,job_id,payload_json) VALUES(?,?,?)",
                (event.event_id, job_id, event.model_dump_json()),
            )

    def page(self, job_id: str, *, cursor: int = 0, limit: int = 200) -> WorkerUsagePage:
        if cursor < 0 or not 1 <= limit <= 500:
            raise ValueError("cursor must be nonnegative and limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence,payload_json FROM usage_receipts WHERE job_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?",
                (job_id, cursor, limit + 1),
            ).fetchall()
        page = rows[:limit]
        return WorkerUsagePage(
            events=[ModelInferenceUsageEvent.model_validate_json(row[1]) for row in page],
            next_cursor=page[-1][0] if page else cursor,
            has_more=len(rows) > limit,
        )


def _counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for native, field in (
        ("inputTokens", "input_tokens"),
        ("outputTokens", "output_tokens"),
        ("totalTokens", "total_tokens"),
    ):
        number = value.get(native)
        if type(number) is int and number >= 0:
            result[field] = number
    if "total_tokens" not in result and {"input_tokens", "output_tokens"} <= result.keys():
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


class SubscriptionUsageRecorder:
    """Count each native inference increment, including newly started child turns.

    The first update in a turn uses `last`, since `total` includes earlier turns
    of resumed threads. Further updates use cumulative deltas; repeated totals
    are notifications, not another inference. Cache/reasoning subsets aren't added.
    """

    def __init__(
        self,
        *,
        job_id: str,
        session_id: str,
        context: dict,
        sink: Callable[[str, ModelInferenceUsageEvent], None],
    ) -> None:
        self.job_id = job_id
        self.session_id = session_id
        self.context = context
        self.sink = sink
        self._lock = RLock()
        self._expected: set[str] = set()
        self._active: dict[tuple[str, str], dict] = {}
        self._closed: set[tuple[str, str]] = set()
        self._pending: dict[str, ModelInferenceUsageEvent] = {}

    def expect_turn(self, thread_id: str) -> None:
        with self._lock:
            self._expected.add(thread_id)

    def notification(self, method: str, params: dict) -> None:
        with self._lock:
            self._notification(method, params)
            self._flush()

    def _notification(self, method: str, params: dict) -> None:
        thread_id = params.get("threadId")
        turn = params.get("turn")
        turn_id = params.get("turnId") or (turn.get("id") if isinstance(turn, dict) else None)
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            return
        key = (thread_id, turn_id)
        if key in self._closed:
            return
        if method == "turn/started" or (
            method in {"thread/tokenUsage/updated", "turn/completed"}
            and thread_id in self._expected
        ):
            self._active.setdefault(key, {"previous": None, "counted": False})
        state = self._active.get(key)
        if state is None:
            return
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            if not isinstance(usage, dict):
                return
            total = _counts(usage.get("total"))
            last = _counts(usage.get("last"))
            # Without cumulative identity repeated `last` values cannot be distinguished.
            if "total_tokens" not in total:
                return
            previous = state["previous"]
            if previous is not None and total == previous:
                return
            counts = (
                last
                if previous is None
                else {
                    field: value - previous[field]
                    for field, value in total.items()
                    if field in previous
                }
            )
            state["previous"] = total
            if "total_tokens" not in counts or any(value < 0 for value in counts.values()):
                counts = {}
            self._emit(key, json.dumps(total, sort_keys=True), counts)
            state["counted"] = True
        elif method == "turn/completed":
            status = turn.get("status") if isinstance(turn, dict) else None
            self._complete(key, status="success" if status == "completed" else "failed")

    def _emit(self, key: tuple[str, str], identity: str, counts: dict, *, status="success") -> None:
        digest = hashlib.sha256(json.dumps([self.job_id, *key, identity]).encode()).hexdigest()
        event = ModelInferenceUsageEvent(
            event_id=f"subscription-usage-{digest}",
            occurred_at=datetime.now(UTC),
            provider="openai_chatgpt_subscription",
            provider_endpoint_identifier="codex_app_server",
            billing_kind="subscription",
            api_key_name=None,
            cost_attribution_recorded=True,
            cost_category=self.context.get("cost_category"),
            model=self.context.get("model"),
            **counts,
            request_status=status,
            normalized_usage_status="available"
            if "total_tokens" in counts
            else "usage_unavailable",
            session_id=self.session_id,
            agent_harness="codex",
            execution_backend="container_worker_vm",
            **{
                field: self.context.get(field)
                for field in (
                    "directory_path",
                    "dispatch_id",
                    "workflow_run_id",
                    "effect_id",
                    "node_id",
                    "trace_id",
                )
            },
            request_id=f"{self.job_id}:{digest}",
            raw_usage_metadata={
                "request_metadata": {
                    "source": "subscription_app_server",
                    "worker_job_id": self.job_id,
                    "thread_id": key[0],
                    "turn_id": key[1],
                }
            },
        )
        self._pending[event.event_id] = event

    def _complete(self, key: tuple[str, str], *, status: str) -> None:
        state = self._active.pop(key)
        if not state["counted"]:
            self._emit(key, "unavailable", {}, status=status)
        self._closed.add(key)
        self._expected.discard(key[0])

    def finish(self, *, status: str = "cancelled") -> None:
        with self._lock:
            for key in list(self._active):
                if self._active[key]["counted"]:
                    # No terminal native notification means a final inference
                    # may have been interrupted before reporting its counters.
                    self._emit(key, "incomplete", {}, status="cancelled")
                self._complete(key, status=status)
            self._flush()

    def _flush(self) -> None:
        for identity, event in list(self._pending.items()):
            try:
                self.sink(self.job_id, event)
            except Exception as exc:  # Usage must not interrupt the agent turn.
                logger.warning(
                    "subscription_usage_write_failed exception_class=%s", type(exc).__name__
                )
                break
            del self._pending[identity]
