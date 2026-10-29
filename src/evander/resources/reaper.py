"""Persistent, provider-neutral scheduling for independent resource cleanup."""

from collections.abc import Callable
import logging
import signal
from threading import Event, current_thread, main_thread


LOGGER = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 5.0


def run_cleanup_loop(
    tick: Callable[[], None],
    *,
    stop: Event | None = None,
    interval: float = CLEANUP_INTERVAL_SECONDS,
) -> None:
    """Run serial passes, with an interruptible delay after each completed pass.

    The caller constructs its service once. Durable journals remain authoritative;
    a failed pass is retried without rebuilding the service or logging its inputs.
    """
    if interval <= 0:
        raise ValueError("Cleanup interval must be positive")
    stop = stop if stop is not None else Event()
    previous_handlers = {}

    def request_stop(signum, frame):
        stop.set()

    if current_thread() is main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, request_stop)
    failed = False
    first_pass = True
    try:
        while not stop.is_set():
            try:
                tick()
            except Exception:
                # Provider exceptions can contain credentials or customer data.
                LOGGER.error("Resource cleanup pass failed; retrying after the cleanup interval")
                failed = True
            else:
                if first_pass or failed:
                    LOGGER.info("Resource cleanup pass completed; independent recovery is running")
                failed = False
            first_pass = False
            stop.wait(interval)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        LOGGER.info("Resource cleanup stopped")
