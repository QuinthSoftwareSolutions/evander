"""Bounded foreground completion work and contention retry timing."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

COMPLETION_SLICE_SECONDS = 0.1
COMPLETION_SLICE_ITEMS = 32
COMPLETION_RETRY_INITIAL_SECONDS = 0.025
COMPLETION_RETRY_MAX_SECONDS = 0.25


class CompletionBudget:
    """One shared allowance per cycle, checked between storage operations."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or time.monotonic
        self.deadline: float | None = None
        self.examined = 0

    @property
    def remaining(self) -> int:
        if self.deadline is not None and self.clock() >= self.deadline:
            return 0
        return max(0, COMPLETION_SLICE_ITEMS - self.examined)

    def take(self) -> bool:
        if not self.remaining:
            return False
        if self.deadline is None:
            self.deadline = self.clock() + COMPLETION_SLICE_SECONDS
        self.examined += 1
        return True


@dataclass
class CompletionRetry:
    attempts: int = 0
    retry_at: float = 0.0
    initial_delay_seconds: float = COMPLETION_RETRY_INITIAL_SECONDS
    maximum_delay_seconds: float = COMPLETION_RETRY_MAX_SECONDS

    def defer(self) -> float:
        delay = min(
            self.maximum_delay_seconds,
            self.initial_delay_seconds * 2 ** min(self.attempts, 5),
        )
        self.attempts += 1
        delay = min(self.maximum_delay_seconds, delay * random.uniform(0.8, 1.2))
        self.retry_at = time.monotonic() + delay
        return delay
