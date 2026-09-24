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
        on_start: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_cycle_success: Callable[[TResult], None] | None = None,
        on_cycle_exception: Callable[[Exception], None] | None = None,
        get_exception_sleep_seconds: Callable[[Exception], float] | None = None,
    ) -> None:
        self.config = config
        self._lock_manager = lock_manager
        self._sleeper = sleeper
        self._run_cycle = run_cycle
        self._get_sleep_seconds = get_sleep_seconds
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_cycle_success = on_cycle_success
        self._on_cycle_exception = on_cycle_exception
        self._get_exception_sleep_seconds = get_exception_sleep_seconds

    def run_once_with_lock(self, *, on_stop: Callable[[], None] | None = None) -> TResult:
        with self._lock_manager.lock(self.config.lock_path):
            try:
                return self._run_cycle()
            finally:
                if on_stop is not None:
                    on_stop()

    def run_forever(self) -> None:
        with self._lock_manager.lock(self.config.lock_path):
            try:
                self._run_forever_locked()
            finally:
                if self._on_stop is not None:
                    self._on_stop()

    def _run_forever_locked(self) -> None:
        if self._on_start is not None:
            self._on_start()
        while True:
            try:
                result = self._run_cycle()
            except Exception as exc:
                if self._on_cycle_exception is None or self._get_exception_sleep_seconds is None:
                    raise
                self._on_cycle_exception(exc)
                self._sleeper.sleep(self._get_exception_sleep_seconds(exc))
                continue
            if self._on_cycle_success is not None:
                self._on_cycle_success(result)
            self._sleeper.sleep(self._get_sleep_seconds(result))


def compute_next_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(0.0, delay)
