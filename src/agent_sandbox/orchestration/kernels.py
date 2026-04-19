from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

from .core_ports import LockManagerPort, SleepPort


TResult = TypeVar("TResult")


class CycleRunner(Protocol[TResult]):
    def __call__(self) -> TResult: ...


class SleepSelector(Protocol[TResult]):
    def __call__(self, result: TResult) -> float: ...


@dataclass(frozen=True)
class PollingKernelConfig:
    poll_interval_seconds: float
    jitter_seconds: float
    lock_path: object


class PollingOrchestratorKernel:
    """Pure polling kernel used by host-specific worker adapters."""

    def __init__(
        self,
        *,
        config: PollingKernelConfig,
        lock_manager: LockManagerPort,
        sleeper: SleepPort,
        run_cycle: CycleRunner[TResult],
        get_sleep_seconds: SleepSelector[TResult],
    ) -> None:
        self.config = config
        self._lock_manager = lock_manager
        self._sleeper = sleeper
        self._run_cycle = run_cycle
        self._get_sleep_seconds = get_sleep_seconds

    def run_once_with_lock(self) -> TResult:
        with self._lock_manager.lock(self.config.lock_path):
            return self._run_cycle()

    def run_forever(self) -> None:
        with self._lock_manager.lock(self.config.lock_path):
            while True:
                result = self._run_cycle()
                self._sleeper.sleep(self._get_sleep_seconds(result))


def compute_next_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(0.0, delay)
