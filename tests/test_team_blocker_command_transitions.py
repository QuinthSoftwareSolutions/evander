from __future__ import annotations

from pathlib import Path

from team_repository_support import _command_service, _lease_agent_dispatch, _seed_hierarchy

from evander_team_addon.models import (
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamInboxItemState,
)
from evander_team_addon.services.domain.blockers import open_or_get_blocker


def _open_dependency_blocker(repo) -> str:
    def seed(state):
        open_or_get_blocker(
            state,
            source_kind="explicit",
            source_id="dependency",
            summary="Dependency needs investigation",
            initial_owner_agent_id="backend",
            team_id="team",
            project_id="project",
        )

    state = repo.update(seed)
    return next(iter(state.blockers))


def _pending_blocker_recipients(state, blocker_id: str) -> list[str]:
    return sorted(
        item.recipient_agent_id
        for item in state.inbox.values()
        if item.state == TeamInboxItemState.PENDING
        and item.payload.get("blocker_id") == blocker_id
    )


def test_delegate_result_returns_ownership_before_owner_escalates(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _open_dependency_blocker(repo)
    commands = _command_service(repo)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-delegate")
    _state, delegated = commands.update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="delegate",
        note="Investigate the local dependency.",
        request_id="delegate",
        delegate_agent_id="worker",
    )
    assert delegated["success"] is True

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-result")
    _state, returned = commands.update_blocker_command(
        session_id="session-worker",
        blocker_id=blocker_id,
        action="delegate_result",
        note="The dependency needs a project decision.",
        request_id="delegate-result",
        resolution_refs=["evidence-local-check"],
    )

    persisted = repo.read()
    blocker = persisted.blockers[blocker_id]
    assert returned["success"] is True
    assert returned["progress_made"] is True
    assert blocker.state == TeamBlockerState.OPEN
    assert blocker.route == TeamBlockerRoute.OWNED
    assert blocker.owner_agent_id == "backend"
    assert blocker.delegate_agent_id is None
    assert blocker.follow_up_at is None
    assert blocker.active_dispatch_id is None
    assert _pending_blocker_recipients(persisted, blocker_id) == ["backend"]
    event = persisted.blocker_events[returned["blocker_event_ids"][0]]
    assert event.event_type == TeamBlockerEventType.DELEGATE_RESULT
    assert event.from_agent_id == "worker"
    assert event.to_agent_id == "backend"
    assert event.reference_ids == ["evidence-local-check"]

    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-escalate")
    _state, escalated = commands.update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="escalate",
        note="The project lead must decide.",
        request_id="escalate",
    )

    persisted = repo.read()
    blocker = persisted.blockers[blocker_id]
    assert escalated["success"] is True
    assert blocker.owner_agent_id == "lead"
    assert blocker.delegate_agent_id is None
    assert blocker.route == TeamBlockerRoute.OWNED
    assert _pending_blocker_recipients(persisted, blocker_id) == ["lead"]
    events = [persisted.blocker_events[event_id] for event_id in escalated["blocker_event_ids"]]
    assert len(events) == 1
    assert events[0].event_type == TeamBlockerEventType.OWNER_TRANSFERRED
    assert events[0].from_agent_id == "backend"
    assert events[0].to_agent_id == "lead"


def test_failed_delegation_still_handles_owner_inbox_without_changing_route(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _open_dependency_blocker(repo)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-invalid-delegate")
    before = repo.read()
    before_event_ids = set(before.blocker_events)
    assert _pending_blocker_recipients(before, blocker_id) == ["backend"]

    _state, result = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="delegate",
        note="Delegate to someone who is not a subordinate.",
        request_id="invalid-delegate",
        delegate_agent_id="lead",
    )

    persisted = repo.read()
    assert result["success"] is False
    assert result["errors"][0]["code"] == "blocker_delegate_not_direct_subordinate"
    assert persisted.blockers[blocker_id] == before.blockers[blocker_id]
    assert set(persisted.blocker_events) == before_event_ids
    # The command handles the dispatched blocker inbox before validating the
    # requested delegate, even when that validation rejects the action.
    assert _pending_blocker_recipients(persisted, blocker_id) == []
