from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import fcntl
import hashlib
import os
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass
class _VerificationFlight:
    future: Future[dict[str, Any]] = field(default_factory=Future)
    coalesced_waiter_count: int = 0


@dataclass
class _VerificationDatabaseState:
    lock: Lock = field(default_factory=Lock)
    flights: dict[str, _VerificationFlight] = field(default_factory=dict)


class TeamReleaseVerificationSingleFlight:
    """Coalesce overlapping execution of one durable verification run.

    A verification session owns a mutable checkout, including untracked build
    outputs.  Two copies of the same run must therefore share the first
    execution rather than independently reset and clean that checkout.

    The process-local registry lets threads share the leader's result.  A
    per-database, per-run file lease additionally serializes leaders from
    different worker processes.  The callback must therefore reread durable
    state after acquiring the lease so a cross-process waiter can observe and
    reuse the completed run rather than touching its checkout again.

    Separate Team runtimes and different verification-run IDs remain fully
    concurrent.
    """

    def __init__(self) -> None:
        self._registry_lock = Lock()
        self._database_states: dict[str, _VerificationDatabaseState] = {}

    def run(
        self,
        *,
        state_db_path: str | Path,
        verification_run_id: str,
        execute: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        database_state = self._database_state(state_db_path)
        with database_state.lock:
            flight = database_state.flights.get(verification_run_id)
            if flight is None:
                flight = _VerificationFlight()
                database_state.flights[verification_run_id] = flight
                leader = True
            else:
                flight.coalesced_waiter_count += 1
                leader = False

        if not leader:
            return {**deepcopy(flight.future.result()), "coalesced": True}

        try:
            with _durable_verification_flight_lease(
                state_db_path=state_db_path,
                verification_run_id=verification_run_id,
            ):
                result = execute()
        except BaseException as exc:
            flight.future.set_exception(exc)
            raise
        else:
            flight.future.set_result(result)
            return result
        finally:
            with database_state.lock:
                if database_state.flights.get(verification_run_id) is flight:
                    database_state.flights.pop(verification_run_id, None)

    def _database_state(self, state_db_path: str | Path) -> _VerificationDatabaseState:
        key = str(Path(state_db_path).resolve())
        with self._registry_lock:
            return self._database_states.setdefault(key, _VerificationDatabaseState())


@contextmanager
def _durable_verification_flight_lease(
    *,
    state_db_path: str | Path,
    verification_run_id: str,
) -> Iterator[None]:
    """Hold the cross-process lease for one database verification run."""

    resolved_db_path = Path(state_db_path).resolve()
    lock_material = f"{resolved_db_path}\0{verification_run_id}".encode("utf-8")
    lock_root = resolved_db_path.parent / "release-verification-flight-locks"
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = lock_root / f"{hashlib.sha256(lock_material).hexdigest()}.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT = TeamReleaseVerificationSingleFlight()


__all__ = [
    "TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT",
    "TeamReleaseVerificationSingleFlight",
]
