from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass, field
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

    The registry is scoped to the Team state database, so separate Team
    runtimes and different verification-run IDs remain fully concurrent.
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


TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT = TeamReleaseVerificationSingleFlight()


__all__ = [
    "TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT",
    "TeamReleaseVerificationSingleFlight",
]
