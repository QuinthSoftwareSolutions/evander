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
    Callers must copy mutable snapshots before handing them to consumers. A
    monotonically increasing minimum revision invalidates older collections
    without allowing concurrent producers for different revisions of one key.
    The optional revision reader runs after producer ownership is acquired and
    before collection, so a waiting producer includes revisions committed while
    it waited. All waiters receive that collection's actual revision.
    """

    def __init__(self, *, ttl_seconds: float, max_entries: int = 16) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[Hashable, tuple[float, int, T]] = OrderedDict()
        self._inflight: dict[Hashable, Future[tuple[int, T]]] = {}
        self._lock = threading.Lock()

    def read(
        self,
        key: Hashable,
        collect: Callable[[], T],
        *,
        refresh: bool = False,
        minimum_revision: int = 0,
        read_revision: Callable[[], int] | None = None,
    ) -> T:
        while True:
            with self._lock:
                cached = self._entries.get(key)
                if (
                    not refresh
                    and cached is not None
                    and cached[0] > time.monotonic()
                    and cached[1] >= minimum_revision
                ):
                    self._entries.move_to_end(key)
                    return cached[2]
                pending = self._inflight.get(key)
                if pending is None:
                    pending = Future()
                    self._inflight[key] = pending
                    break
            revision, result = pending.result()
            if revision >= minimum_revision:
                return result
            # A request following a newer event must not consume a collection
            # that began before that event. Join or produce the next revision.
        try:
            revision = minimum_revision if read_revision is None else read_revision()
            result = collect()
            with self._lock:
                self._entries[key] = (
                    time.monotonic() + self._ttl_seconds,
                    revision,
                    result,
                )
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
                self._inflight.pop(key)
            pending.set_result((revision, result))
            return result
        except BaseException as exc:
            with self._lock:
                self._inflight.pop(key, None)
            pending.set_exception(exc)
            raise
