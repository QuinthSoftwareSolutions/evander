from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any


@dataclass
class _WakeGeneration:
    ready: Event = field(default_factory=Event)
    future: Future[dict[str, Any]] = field(default_factory=Future)


@dataclass
class _WakeFlight:
    active_generation: _WakeGeneration
    pending_generation: _WakeGeneration | None = None
    coalesced_waiter_count: int = 0


@dataclass
class _WakeDatabaseState:
    lock: Lock = field(default_factory=Lock)
    flights: dict[str, _WakeFlight] = field(default_factory=dict)


class TeamWakeSingleFlight:
    """Coalesce overlapping Team wakes without dropping a later eligibility check.

    The invocation key includes the state revision observed by the caller, so
    a later state change always gets its own execution. Differently scoped or
    revisioned wakes remain concurrent; SQLite serializes only their atomic
    write sections instead of prompt preparation for unrelated agents.

    Identical calls that arrive during one active execution share one trailing
    generation. The trailing execution rechecks wall-clock and external state
    that is intentionally absent from the invocation key. Calls arriving while
    that trailing generation runs form the next generation, so no overlapping
    wake is represented only by an eligibility check that began before it.
    """

    def __init__(self) -> None:
        self._registry_lock = Lock()
        self._database_states: dict[str, _WakeDatabaseState] = {}

    def run(
        self,
        *,
        state_db_path: str | Path,
        invocation_key: str,
        execute: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        database_state = self._database_state(state_db_path)

        generation_leader = False
        coalesced = False
        with database_state.lock:
            flight = database_state.flights.get(invocation_key)
            if flight is None:
                generation = _WakeGeneration()
                generation.ready.set()
                flight = _WakeFlight(active_generation=generation)
                database_state.flights[invocation_key] = flight
                generation_leader = True
            else:
                generation = flight.pending_generation
                if generation is None:
                    generation = _WakeGeneration()
                    flight.pending_generation = generation
                    generation_leader = True
                flight.coalesced_waiter_count += 1
                coalesced = True

        if not generation_leader:
            shared_result = generation.future.result()
            return {**shared_result, "coalesced": True}

        generation.ready.wait()
        try:
            result = execute()
        except BaseException as exc:
            result = None
            error: BaseException | None = exc
        else:
            error = None

        with database_state.lock:
            next_generation = flight.pending_generation
            flight.pending_generation = None
            if next_generation is None:
                database_state.flights.pop(invocation_key, None)
            else:
                flight.active_generation = next_generation

        if error is None:
            assert result is not None
            generation.future.set_result(result)
        else:
            generation.future.set_exception(error)
        if next_generation is not None:
            next_generation.ready.set()

        completed_result = generation.future.result()
        if coalesced:
            return {**completed_result, "coalesced": True}
        return completed_result

    def _database_state(self, state_db_path: str | Path) -> _WakeDatabaseState:
        key = str(Path(state_db_path).resolve())
        with self._registry_lock:
            return self._database_states.setdefault(key, _WakeDatabaseState())


TEAM_WAKE_SINGLE_FLIGHT = TeamWakeSingleFlight()


__all__ = ["TEAM_WAKE_SINGLE_FLIGHT", "TeamWakeSingleFlight"]
