"""Single-flight lifecycle maintenance outside the foreground event dispatcher.

Only hooks explicitly opting in use this lane. The dispatcher owns scheduling;
the hook's mutable recovery cursors are touched only by the one executor thread.
Notifications are bounded hints: durable receipts remain the recovery authority.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

from evander.orchestration.maintenance import (
    MaintenanceBudget,
    MaintenanceResult,
    MAINTENANCE_SLICE_ITEMS,
    RECOVERY_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)


def sqlite_busy(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and (
        getattr(error, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
        or str(error).lower() in {"database is locked", "database table is locked"}
    )


class IsolatedMaintenanceHook:
    """Wrap a lifecycle hook, sharing one lane across all three entry points."""

    def __init__(self, hook: Any) -> None:
        self._hook = hook
        self._executor: ThreadPoolExecutor | None = None
        self._future: Future[MaintenanceResult] | None = None
        self._operation: tuple[str, str | None] | None = None
        self._pending: set[str] = set()
        self._targets: OrderedDict[str, None] = OrderedDict()
        self._retry_at: dict[str, float] = {}
        self._targets_since_completion = 0
        self._closed = False

    @property
    def maintenance_name(self) -> str:
        return type(self._hook).__name__

    @property
    def maintenance_priority(self) -> int:
        return getattr(self._hook, "maintenance_priority", 0)

    def initialize(self, *, worker: Any) -> None:
        self._closed = False
        self._hook.initialize(worker=worker)

    def close(self) -> None:
        # Called before changing any worker bindings. Never cancel a transaction.
        self._closed = True
        if self._executor is not None:
            self._executor.shutdown(wait=True)
        self._observe()
        self._executor = None
        self._pending.clear()
        self._targets.clear()
        self._retry_at.clear()
        self._targets_since_completion = 0

    def poll(self, *, worker: Any) -> int:
        """Observe completions and continue queued slices after the wake signal."""
        return self._pump(worker=worker).changes

    def _observe(self) -> int:
        if self._future is None or not self._future.done():
            return 0
        future, operation = self._future, self._operation
        self._future = self._operation = None
        assert operation is not None
        phase, dispatch_id = operation
        try:
            result = future.result()
        except Exception as exc:
            # Retain the failed candidate and retry at the recovery cadence.
            # Non-busy failures also remain retryable, with their traceback.
            self._retry_at[phase] = time.monotonic() + RECOVERY_INTERVAL_SECONDS
            if dispatch_id is not None:
                self._targets[dispatch_id] = None
            else:
                self._pending.add(phase)
            logger.warning(
                "Isolated maintenance deferred hook=%s phase=%s busy=%s",
                self.maintenance_name,
                phase,
                sqlite_busy(exc),
                exc_info=not sqlite_busy(exc),
            )
            return 0
        self._retry_at.pop(phase, None)
        if result.remaining:
            self._pending.add(phase)
        return result.changes

    def _pump(self, *, worker: Any) -> MaintenanceResult:
        changes = self._observe()
        if self._closed or self._future is not None:
            return MaintenanceResult(changes=changes)
        now = time.monotonic()
        phases = ("leases", "target", "completion")
        if self._targets_since_completion >= MAINTENANCE_SLICE_ITEMS:
            # A steady stream of hints must not starve durable receipt recovery.
            phases = ("leases", "completion", "target")
        phase = next(
            (
                p
                for p in phases
                if (self._targets if p == "target" else p in self._pending)
                and now >= self._retry_at.get(p, 0)
            ),
            None,
        )
        if phase is None:
            return MaintenanceResult(changes=changes)
        dispatch_id = self._targets.popitem(last=False)[0] if phase == "target" else None
        if phase == "target":
            self._targets_since_completion += 1
        elif phase == "completion":
            self._targets_since_completion = 0
        self._pending.discard(phase)
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="team-maintenance"
            )
        self._operation = (phase, dispatch_id)
        self._future = self._executor.submit(self._execute, worker, phase, dispatch_id)
        # Wake only. Callbacks must never run workflow successors or touch cursors.
        self._future.add_done_callback(lambda _future: self._notify(worker))
        return MaintenanceResult(changes=changes)

    @staticmethod
    def _notify(worker: Any) -> None:
        try:
            worker._wake_notifier.notify()
        except Exception:
            logger.debug("Maintenance wake notification unavailable", exc_info=True)

    def _execute(self, worker: Any, phase: str, dispatch_id: str | None) -> MaintenanceResult:
        started = time.perf_counter()
        outcome, result = "succeeded", MaintenanceResult()
        try:
            if phase == "target":
                self._hook.on_prompt_execution_changed(
                    worker=worker,
                    record=SimpleNamespace(outputs={"dispatch_id": dispatch_id}),
                )
            else:
                method = (
                    self._hook.on_waiting_reconciliation_slice
                    if phase == "leases"
                    else self._hook.on_post_completion_dispatch_slice
                )
                result = method(worker=worker, budget=MaintenanceBudget())
            return result
        except Exception as exc:
            outcome = "busy" if sqlite_busy(exc) else "failed"
            raise
        finally:
            logger.info(
                "Isolated maintenance completed hook=%s phase=%s duration_ms=%.1f "
                "records_examined=%s changes=%s outcome=%s",
                self.maintenance_name,
                phase,
                (time.perf_counter() - started) * 1000,
                result.examined,
                result.changes if phase != "target" else None,
                outcome,
            )

    def on_waiting_reconciliation_slice(self, *, worker: Any, budget: Any) -> MaintenanceResult:
        self._pending.add("leases")
        return self._pump(worker=worker)

    def on_post_completion_dispatch_slice(self, *, worker: Any, budget: Any) -> MaintenanceResult:
        self._pending.add("completion")
        return self._pump(worker=worker)

    def on_waiting_reconciliation(self, *, worker: Any) -> int:
        return self.on_waiting_reconciliation_slice(worker=worker, budget=None).changes

    def on_post_completion_dispatch(self, *, worker: Any) -> int:
        return self.on_post_completion_dispatch_slice(worker=worker, budget=None).changes

    def on_prompt_execution_changed(self, *, worker: Any, record: Any) -> None:
        dispatch_id = str((getattr(record, "outputs", None) or {}).get("dispatch_id") or "").strip()
        in_flight_target = int(self._operation is not None and self._operation[0] == "target")
        if dispatch_id and (
            dispatch_id in self._targets
            or len(self._targets) + in_flight_target < MAINTENANCE_SLICE_ITEMS
        ):
            self._targets[dispatch_id] = None
        self._pump(worker=worker)

    def on_completion_fan_out(self, *, worker: Any, queue_record: Any, graph: Any) -> int:
        self.on_prompt_execution_changed(
            worker=worker, record=SimpleNamespace(outputs=getattr(queue_record, "payload", {}))
        )
        return 0

    def validate_queued_prompt(self, *, worker: Any, record: Any) -> bool:
        # This is an authorization check, not an asynchronous hint. The Team
        # implementation uses a bounded read-only lookup and fails closed.
        return self._hook.validate_queued_prompt(worker=worker, record=record)
