from __future__ import annotations

import time
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier, Event, Thread

from evander_team_addon.services.release_verification_singleflight import (
    TeamReleaseVerificationSingleFlight,
)


def test_release_verification_singleflight_coalesces_overlapping_same_run(
    tmp_path: Path,
) -> None:
    coordinator = TeamReleaseVerificationSingleFlight()
    state_db_path = tmp_path / "state.db"
    leader_started = Event()
    release_leader = Event()
    executions: list[int] = []
    results: dict[str, dict[str, object]] = {}

    def execute() -> dict[str, object]:
        executions.append(len(executions) + 1)
        leader_started.set()
        assert release_leader.wait(timeout=2)
        return {"success": True, "verification_state": "passed"}

    def run(name: str) -> None:
        results[name] = coordinator.run(
            state_db_path=state_db_path,
            verification_run_id="verification-1",
            execute=execute,
        )

    leader = Thread(target=run, args=("leader",))
    follower = Thread(target=run, args=("follower",))
    leader.start()
    assert leader_started.wait(timeout=2)
    follower.start()
    _wait_for_coalesced_waiters(coordinator, state_db_path, "verification-1", count=1)

    release_leader.set()
    leader.join(timeout=2)
    follower.join(timeout=2)

    assert not leader.is_alive()
    assert not follower.is_alive()
    assert executions == [1]
    assert results["leader"] == {"success": True, "verification_state": "passed"}
    assert results["follower"] == {
        "success": True,
        "verification_state": "passed",
        "coalesced": True,
    }


def test_release_verification_singleflight_allows_different_runs_to_overlap(
    tmp_path: Path,
) -> None:
    coordinator = TeamReleaseVerificationSingleFlight()
    barrier = Barrier(2)
    results: list[str] = []

    def run(verification_run_id: str) -> None:
        result = coordinator.run(
            state_db_path=tmp_path / "state.db",
            verification_run_id=verification_run_id,
            execute=lambda: _meet_at_barrier(barrier, verification_run_id),
        )
        results.append(str(result["verification_run_id"]))

    first = Thread(target=run, args=("verification-1",))
    second = Thread(target=run, args=("verification-2",))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == ["verification-1", "verification-2"]


def test_release_verification_singleflight_serializes_separate_processes(
    tmp_path: Path,
) -> None:
    process_context = get_context("fork")
    state_db_path = tmp_path / "state.db"
    leader_entered = process_context.Event()
    follower_started = process_context.Event()
    follower_entered = process_context.Event()
    release_execution = process_context.Event()
    counter_lock = process_context.Lock()
    active_executions = process_context.Value("i", 0)
    maximum_active_executions = process_context.Value("i", 0)
    execution_count = process_context.Value("i", 0)

    def run_process(*, entered, started=None) -> None:
        if started is not None:
            started.set()

        def execute() -> dict[str, object]:
            with counter_lock:
                active_executions.value += 1
                execution_count.value += 1
                maximum_active_executions.value = max(
                    maximum_active_executions.value,
                    active_executions.value,
                )
            entered.set()
            try:
                assert release_execution.wait(timeout=3)
                return {"success": True}
            finally:
                with counter_lock:
                    active_executions.value -= 1

        TeamReleaseVerificationSingleFlight().run(
            state_db_path=state_db_path,
            verification_run_id="verification-1",
            execute=execute,
        )

    leader = process_context.Process(target=run_process, kwargs={"entered": leader_entered})
    follower = process_context.Process(
        target=run_process,
        kwargs={"entered": follower_entered, "started": follower_started},
    )
    leader.start()
    assert leader_entered.wait(timeout=2)
    follower.start()
    try:
        assert follower_started.wait(timeout=2)
        assert not follower_entered.wait(timeout=0.25)
    finally:
        release_execution.set()
        leader.join(timeout=3)
        follower.join(timeout=3)

    assert not leader.is_alive()
    assert not follower.is_alive()
    assert leader.exitcode == 0
    assert follower.exitcode == 0
    assert execution_count.value == 2
    assert maximum_active_executions.value == 1


def _meet_at_barrier(barrier: Barrier, verification_run_id: str) -> dict[str, object]:
    barrier.wait(timeout=2)
    return {"success": True, "verification_run_id": verification_run_id}


def _wait_for_coalesced_waiters(
    coordinator: TeamReleaseVerificationSingleFlight,
    state_db_path: Path,
    verification_run_id: str,
    *,
    count: int,
) -> None:
    deadline = time.monotonic() + 2
    database_state = coordinator._database_state(state_db_path)
    while time.monotonic() < deadline:
        with database_state.lock:
            flight = database_state.flights.get(verification_run_id)
            if flight is not None and flight.coalesced_waiter_count >= count:
                return
        time.sleep(0.001)
    raise AssertionError("release verification did not reach the expected single-flight wait state")
