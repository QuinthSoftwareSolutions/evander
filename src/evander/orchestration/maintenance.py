"""Cooperative, single-threaded recovery scheduling (not a task executor)."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

EVENT_SLICE_SECONDS = 0.1
MAINTENANCE_SLICE_SECONDS = 0.1
MAINTENANCE_SLICE_ITEMS = 50
RECOVERY_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class MaintenanceResult:
    changes: int = 0
    examined: int | None = None  # Unknown for legacy hooks, never reported as zero.
    remaining: bool = False


class MaintenanceBudget:
    """Check between candidates; a database call/transaction is never preempted."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.deadline = clock() + MAINTENANCE_SLICE_SECONDS
        self.examined = 0

    def take(self) -> bool:
        if self.examined and (
            self.examined >= MAINTENANCE_SLICE_ITEMS or self.clock() >= self.deadline
        ):
            return False
        self.examined += 1
        return True


class RecoverySchedule:
    """A pass starts at least every five seconds, even under event backlog.

    Continuations retain their position: ownership validation must finish before
    generic queue replay. Cursors live on hooks; all durable state remains in the
    repositories. Restarting starts a fresh recovery pass.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.next_due = clock() + RECOVERY_INTERVAL_SECONDS
        self.pending: deque[tuple[str, Any]] = deque()

    def begin_if_due(
        self,
        tasks: list[tuple[str, Any]],
        *,
        foreground_pending: bool = False,
    ) -> bool:
        now = self.clock()
        due = now >= self.next_due
        if due:
            if not self.pending:
                self.pending.extend(tasks)
            else:
                # A large recovery pass must not hold up a newly due lease.
                urgent = [
                    task
                    for task in tasks
                    if task[0] == "waiting_reconciliation"
                    and getattr(task[1], "maintenance_priority", 0) < 0
                ]
                for task in reversed(urgent):
                    if task in self.pending:
                        self.pending.remove(task)
                    self.pending.appendleft(task)
            self.next_due = now + RECOVERY_INTERVAL_SECONDS
        if not self.pending:
            return False
        # Pending continuations drain immediately while idle. Under a durable
        # event backlog one reserved recovery opportunity runs per interval;
        # the executor gives urgent lease work and ordinary recovery separate
        # bounded progress within that opportunity.
        return due or not foreground_pending

    def deferred_seconds(self, *, foreground_pending: bool) -> float:
        if not foreground_pending or not self.pending:
            return 0.0
        return max(0.0, self.next_due - self.clock())

    def sleep_limit(self) -> float:
        return 0.0 if self.pending else max(0.0, self.next_due - self.clock())
