"""Bounded RPC continuations; durable reconciliation stays on the calling thread."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from typing import Any, Callable


class _PendingBackendCall(BaseException):
    """Suspend reconciliation without being treated as a failed provider call.

    Like cancellation, this must cross the provider's best-effort Exception
    handlers: a pending log read must not look like empty logs or failed cleanup.
    """


@dataclass
class _Continuation:
    owner: str
    target: str
    backend: Any
    bound: bool = False
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    calls: dict[str, Future] = field(default_factory=dict)
    consumed: set[str] = field(default_factory=set)

    @property
    def ready(self) -> bool:
        return any(key not in self.consumed and future.done() for key, future in self.calls.items())


class DeferredBackendReconciliation:
    """One RPC at a time per record, shared across bounded reconciliation passes.

    Replaying the local provider code reuses completed RPC results until that
    pass finishes. It never repeats a side effect merely because a later RPC is
    pending. The provider's existing durable/idempotent transitions remain on
    the orchestration thread; only backend operations run in the pool.
    """

    def __init__(self, *, notify: Callable[[], None], max_workers: int = 4, capacity: int = 32):
        self._notify = notify
        self._capacity = capacity
        self._states: dict[tuple[str, str], _Continuation] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent-rpc")

    def ready_owners(self) -> list[tuple[str, str]]:
        return [key for key, state in self._states.items() if state.ready]

    def discard(self, key: tuple[str, str]) -> None:
        state = self._states.pop(key, None)
        if state is not None:
            for future in state.calls.values():
                future.cancel()

    def run(self, *, record: Any, backend: Any, reconcile: Callable[[Any], int]) -> int:
        key = (record.execution_id, str(record.outputs["execution_id"]))
        state = self._states.get(key)
        if state is None:
            if len(self._states) >= self._capacity:
                return 0
            state = self._states[key] = _Continuation(*key, backend)
        try:
            result = reconcile(_DeferredBackend(self, state))
        except _PendingBackendCall:
            return 0
        except BaseException:
            self.discard(key)
            raise
        self.discard(key)
        return result

    def call(self, state: _Continuation, method: str, args: tuple, kwargs: dict):
        key = json.dumps([method, args, kwargs], sort_keys=True)
        future = state.calls.get(key)
        if future is None:
            future = self._executor.submit(self._invoke, state, method, args, kwargs)
            state.calls[key] = future
            future.add_done_callback(self._completed)
            raise _PendingBackendCall()
        if not future.done():
            raise _PendingBackendCall()
        state.consumed.add(key)
        return future.result()

    @staticmethod
    def _invoke(state: _Continuation, method: str, args: tuple, kwargs: dict):
        # Bind in the first RPC's future, so missing mappings follow the
        # provider's existing inspect-error/retry path instead of escaping the
        # record and aborting the recovery page. Only one RPC runs per state.
        if not state.bound:
            bind = getattr(state.backend, "for_reconciliation", None)
            if callable(bind):
                state.backend = bind(state.target)
            state.bound = True
        return getattr(state.backend, method)(*args, **kwargs)

    def _completed(self, future: Future) -> None:
        try:
            self._notify()
        except Exception:
            pass  # Periodic durable recovery remains available if waking fails.

    def close(self) -> None:
        # Drain before switching runtime/store bindings; RPCs retain their normal
        # client timeouts. Never publish a completion from executor callbacks.
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._states.clear()


class _DeferredBackend:
    _RPC_METHODS = {
        "inspect",
        "get_log_snapshot",
        "get_log_page",
        "destroy",
        "stop",
        "interrupt_active_tool",
    }

    def __init__(self, pool: DeferredBackendReconciliation, state: _Continuation):
        self._pool, self._state = pool, state
        self.reconciliation_observed_at = state.observed_at

    def __getattr__(self, name: str):
        # Preserve optional backend capabilities (for example get_log_page).
        attribute = getattr(self._state.backend, name)
        if name not in self._RPC_METHODS:
            return attribute
        return lambda *args, **kwargs: self._pool.call(self._state, name, args, kwargs)
