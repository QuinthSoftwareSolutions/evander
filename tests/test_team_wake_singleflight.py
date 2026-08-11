from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Event, Thread
from types import SimpleNamespace

from evander_team_addon.models import (
    PersistentAgent,
    TeamConversation,
    TeamConversationMessage,
    TeamState,
)
from evander_team_addon.providers import (
    TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS,
    TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD,
    _TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS,
    _team_wake_has_chat_claim_candidate,
    _team_wake_invocation_key,
    _team_wake_lease_summary,
)
from evander_team_addon.services.wake_singleflight import TeamWakeSingleFlight


def test_team_wake_singleflight_coalesces_identical_overlapping_invocations(
    tmp_path: Path,
) -> None:
    coordinator = TeamWakeSingleFlight()
    state_db_path = tmp_path / "state.db"
    leader_started = Event()
    release_leader = Event()
    executions: list[int] = []
    results: dict[str, dict[str, object]] = {}

    def execute() -> dict[str, object]:
        execution_number = len(executions) + 1
        executions.append(execution_number)
        if execution_number == 1:
            leader_started.set()
            assert release_leader.wait(timeout=2)
        return {"success": True, "revision": execution_number, "leases": []}

    def run(name: str) -> None:
        results[name] = coordinator.run(
            state_db_path=state_db_path,
            invocation_key="same-agent-and-options",
            execute=execute,
        )

    leader = Thread(target=run, args=("leader",))
    followers = [Thread(target=run, args=(f"follower-{index}",)) for index in range(3)]
    leader.start()
    assert leader_started.wait(timeout=2)
    for follower in followers:
        follower.start()
    _wait_for_flight_waiters(
        coordinator,
        state_db_path,
        coalesced=3,
    )

    release_leader.set()
    leader.join(timeout=2)
    for follower in followers:
        follower.join(timeout=2)

    assert not leader.is_alive()
    assert all(not follower.is_alive() for follower in followers)
    assert executions == [1, 2]
    assert results["leader"] == {
        "success": True,
        "revision": 1,
        "leases": [],
    }
    for index in range(3):
        assert results[f"follower-{index}"] == {
            "success": True,
            "revision": 2,
            "leases": [],
            "coalesced": True,
        }


def test_team_wake_singleflight_rechecks_external_eligibility_for_overlapping_call(
    tmp_path: Path,
) -> None:
    coordinator = TeamWakeSingleFlight()
    state_db_path = tmp_path / "state.db"
    leader_checked_eligibility = Event()
    release_leader = Event()
    externally_eligible = Event()
    results: dict[str, dict[str, object]] = {}

    def execute() -> dict[str, object]:
        eligible = externally_eligible.is_set()
        if not leader_checked_eligibility.is_set():
            leader_checked_eligibility.set()
            assert release_leader.wait(timeout=2)
        return {"success": True, "leased_count": int(eligible), "coalesced": False}

    def run(name: str) -> None:
        results[name] = coordinator.run(
            state_db_path=state_db_path,
            invocation_key="unchanged-revision-and-config",
            execute=execute,
        )

    leader = Thread(target=run, args=("leader",))
    follower = Thread(target=run, args=("follower",))
    leader.start()
    assert leader_checked_eligibility.wait(timeout=2)
    follower.start()
    _wait_for_flight_waiters(
        coordinator,
        state_db_path,
        coalesced=1,
        invocation_key="unchanged-revision-and-config",
    )

    externally_eligible.set()
    release_leader.set()
    leader.join(timeout=2)
    follower.join(timeout=2)

    assert not leader.is_alive()
    assert not follower.is_alive()
    assert results["leader"] == {
        "success": True,
        "leased_count": 0,
        "coalesced": False,
    }
    assert results["follower"] == {
        "success": True,
        "leased_count": 1,
        "coalesced": True,
    }


def test_team_wake_singleflight_queues_another_generation_during_trailing_run(
    tmp_path: Path,
) -> None:
    coordinator = TeamWakeSingleFlight()
    state_db_path = tmp_path / "state.db"
    execution_started = [Event(), Event()]
    release_execution = [Event(), Event()]
    executions: list[int] = []
    results: dict[str, dict[str, object]] = {}

    def execute() -> dict[str, object]:
        execution_number = len(executions) + 1
        executions.append(execution_number)
        if execution_number <= 2:
            execution_started[execution_number - 1].set()
            assert release_execution[execution_number - 1].wait(timeout=2)
        return {"success": True, "revision": execution_number}

    def run(name: str) -> None:
        results[name] = coordinator.run(
            state_db_path=state_db_path,
            invocation_key="same-agent-and-options",
            execute=execute,
        )

    leader = Thread(target=run, args=("leader",))
    first_follower = Thread(target=run, args=("first-follower",))
    second_follower = Thread(target=run, args=("second-follower",))
    leader.start()
    assert execution_started[0].wait(timeout=2)
    first_follower.start()
    _wait_for_flight_waiters(coordinator, state_db_path, coalesced=1)

    release_execution[0].set()
    assert execution_started[1].wait(timeout=2)
    leader.join(timeout=2)
    assert not leader.is_alive()
    second_follower.start()
    _wait_for_flight_waiters(coordinator, state_db_path, coalesced=2)

    release_execution[1].set()
    first_follower.join(timeout=2)
    second_follower.join(timeout=2)

    assert not first_follower.is_alive()
    assert not second_follower.is_alive()
    assert executions == [1, 2, 3]
    assert results["leader"]["revision"] == 1
    assert results["leader"].get("coalesced") is None
    assert results["first-follower"]["revision"] == 2
    assert results["first-follower"]["coalesced"] is True
    assert results["second-follower"]["revision"] == 3
    assert results["second-follower"]["coalesced"] is True


def test_team_wake_singleflight_allows_different_agent_scopes_to_overlap(
    tmp_path: Path,
) -> None:
    coordinator = TeamWakeSingleFlight()
    state_db_path = tmp_path / "state.db"
    both_started = Barrier(2)
    results: list[str] = []

    def run(invocation_key: str) -> None:
        result = coordinator.run(
            state_db_path=state_db_path,
            invocation_key=invocation_key,
            execute=lambda: _meet_at_barrier(both_started, invocation_key),
        )
        results.append(result["database"])

    first = Thread(target=run, args=("agent-a",))
    second = Thread(target=run, args=("agent-b",))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == ["agent-a", "agent-b"]


def test_team_wake_singleflight_allows_independent_state_databases(
    tmp_path: Path,
) -> None:
    coordinator = TeamWakeSingleFlight()
    both_started = Barrier(2)
    results: list[str] = []

    def run(database_name: str) -> None:
        result = coordinator.run(
            state_db_path=tmp_path / database_name / "state.db",
            invocation_key="global-wake",
            execute=lambda: _meet_at_barrier(both_started, database_name),
        )
        results.append(result["database"])

    first = Thread(target=run, args=("first",))
    second = Thread(target=run, args=("second",))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == ["first", "second"]


def test_team_wake_singleflight_key_changes_with_state_revision() -> None:
    definition = SimpleNamespace(
        **{field_name: None for field_name in _TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS}
    )

    before = _team_wake_invocation_key(
        definition,
        resolved_config={"agent_id": "agent"},
        state_revision=10,
    )
    after = _team_wake_invocation_key(
        definition,
        resolved_config={"agent_id": "agent"},
        state_revision=11,
    )

    assert before != after


def test_team_wake_lease_summary_is_bounded_and_omits_release_history() -> None:
    release_packages = {
        f"release-{index:03d}": {
            "id": f"release-{index:03d}",
            "history": "sensitive release history " * 500,
            "deliverables": [{"body": "large" * 500}],
        }
        for index in range(42)
    }
    lease = {
        "agent": {
            "id": "agent",
            "session_id": "session",
            "team_id": "team",
            "role_id": "role",
            "description": "private role prompt",
        },
        "dispatch_id": "dispatch",
        "reason": "task_ready",
        "dispatch": {
            "id": "dispatch",
            "agent_id": "agent",
            "session_id": "session",
            "status": "leased",
            "project_ids": [f"project-{index:03d}" for index in range(25)],
            "task_ids": ["task"],
            "error": "x" * 5_000,
        },
        "tasks": [{"id": "task", "description": "private task body"}],
        "release_packages": release_packages,
    }

    summary = _team_wake_lease_summary(lease)
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["agent_id"] == "agent"
    assert summary["dispatch_id"] == "dispatch"
    assert summary["status"] == "leased"
    assert summary["context_counts"]["release_packages"] == 42
    assert len(summary["release_package_ids"]) == TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD
    assert summary["release_package_ids_truncated"] is True
    assert summary["dispatch"]["project_count"] == 25
    assert len(summary["dispatch"]["project_ids"]) == TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD
    assert len(summary["error"] or "") == TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS
    assert "sensitive release history" not in serialized
    assert "private role prompt" not in serialized
    assert "private task body" not in serialized
    assert len(serialized) < 10_000
    assert len(lease["release_packages"]) == 42


def test_targeted_wake_chat_preflight_skips_only_without_scoped_messages() -> None:
    state = TeamState.empty()
    state.agents["agent"] = PersistentAgent.model_construct(
        id="agent",
        team_id="team",
        role_id="role",
        session_id="session",
        subordinate_agent_ids=[],
    )
    state.conversations["conversation"] = TeamConversation(
        id="conversation",
        agent_id="agent",
    )
    now = datetime(2030, 1, 1, tzinfo=UTC)

    assert not _team_wake_has_chat_claim_candidate(
        state,
        team_id="team",
        project_id=None,
        agent_id="agent",
        blocked_team_ids=set(),
        now=now,
    )

    state.conversation_messages["message"] = TeamConversationMessage(
        id="message",
        conversation_id="conversation",
        sender="operator",
        body="Please take a look.",
        status="queued",
    )

    assert _team_wake_has_chat_claim_candidate(
        state,
        team_id="team",
        project_id=None,
        agent_id="agent",
        blocked_team_ids=set(),
        now=now,
    )
    assert not _team_wake_has_chat_claim_candidate(
        state,
        team_id="team",
        project_id=None,
        agent_id="agent",
        blocked_team_ids={"team"},
        now=now,
    )


def _meet_at_barrier(barrier: Barrier, database_name: str) -> dict[str, object]:
    barrier.wait(timeout=2)
    return {"success": True, "database": database_name}


def _wait_for_flight_waiters(
    coordinator: TeamWakeSingleFlight,
    state_db_path: Path,
    *,
    coalesced: int = 0,
    invocation_key: str = "same-agent-and-options",
) -> None:
    deadline = time.monotonic() + 2
    database_state = coordinator._database_state(state_db_path)
    while time.monotonic() < deadline:
        with database_state.lock:
            flight = database_state.flights.get(invocation_key)
            if flight is not None and flight.coalesced_waiter_count >= coalesced:
                return
        time.sleep(0.001)
    raise AssertionError("wake invocation did not reach the expected single-flight wait state")
