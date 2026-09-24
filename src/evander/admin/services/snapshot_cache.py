from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from typing import Generic, TypeVar


T = TypeVar("T")


class SnapshotCache(Generic[T]):
    """Bounded process-local snapshots with one producer per key.

    Expiration starts after collection completes. Failed collections are never
    cached, and concurrent readers share the same result (or exception).
    Callers must copy mutable snapshots before handing them to consumers.
    """

    def __init__(self, *, ttl_seconds: float, max_entries: int = 16) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()
        self._inflight: dict[Hashable, Future[T]] = {}
        self._lock = threading.Lock()

    def read(self, key: Hashable, collect: Callable[[], T], *, refresh: bool = False) -> T:
        with self._lock:
            cached = self._entries.get(key)
            if not refresh and cached is not None and cached[0] > time.monotonic():
                self._entries.move_to_end(key)
                return cached[1]
            pending = self._inflight.get(key)
            owner = pending is None
            if pending is None:
                pending = Future()
                self._inflight[key] = pending
        if not owner:
            return pending.result()
        try:
            result = collect()
            with self._lock:
                self._entries[key] = (time.monotonic() + self._ttl_seconds, result)
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            pending.set_result(result)
            return result
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
