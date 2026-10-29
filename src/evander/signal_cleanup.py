from __future__ import annotations

from collections.abc import Callable, Mapping
import signal
from threading import current_thread, main_thread
from typing import Any


def install_signal_cleanup_handlers(cleanup: Callable[[], Any]) -> dict[int, Any]:
    """Install main-thread SIGINT/SIGTERM handlers that run cleanup first."""

    if current_thread() is not main_thread():
        return {}
    old_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(signum)

        def handler(received: int, frame: Any, *, previous_handler: Any = previous) -> None:
            cleanup()
            if callable(previous_handler):
                previous_handler(received, frame)
                return
            if received == signal.SIGINT:
                raise KeyboardInterrupt
            raise SystemExit(128 + received)

        signal.signal(signum, handler)
        old_handlers[signum] = previous
    return old_handlers


def restore_signal_handlers(old_handlers: Mapping[int, Any]) -> None:
    if current_thread() is not main_thread():
        return
    for signum, previous in old_handlers.items():
        signal.signal(signum, previous)
