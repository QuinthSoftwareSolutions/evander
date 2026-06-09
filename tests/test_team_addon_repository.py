from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from truman_team_addon.models import (
    EphemeralAgentPolicy,
    ProjectGoal,
    TeamInboxItemType,
    TeamTaskState,
)
from truman_team_addon.repository import TeamStateConflictError, TeamStateRepository


def _seed_hierarchy(tmp_path: Path) -> TeamStateRepository:
    """manager -> lead (project) -> backend -> worker, all in one team/project."""

    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=1,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=2,
    )
    repo.register_agent(
        agent_id="worker",
        team_id="team",
        project_id="project",
        role="worker",
        session_id="session-worker",
        reports_to_agent_id="backend",
        expected_revision=3,
    )
    return repo


def test_repository_initializes_private_addon_state(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.read()

    assert state.revision == 0
    assert state.teams == {}
    assert (tmp_path / "addons" / "team-addon" / "state.json").is_file()


def test_repository_writes_increment_revision_and_preserve_hierarchy(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    assert state.revision == 1

    state = repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Complete project")],
        expected_revision=1,
    )
    assert state.revision == 2

    state = repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=2,
    )
    assert state.revision == 3
    assert state.agents["manager"].subordinate_agent_ids == ["lead"]
    assert state.agents["lead"].subordinate_agent_ids == ["backend"]


def test_repository_enforces_parent_ephemeral_concurrency(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=1,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=1),
        expected_revision=2,
    )
    repo.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=3,
    )

    with pytest.raises(ValueError, match="max_concurrent=1"):
        repo.request_ephemeral_assignments(
            parent_agent_id="backend",
            requests=[
                {"task_id": "task", "title": "One", "prompt": "Do one thing."},
                {"task_id": "task", "title": "Two", "prompt": "Do another thing."},
            ],
        )

    assert repo.read().ephemeral_assignments == {}


def test_repository_stale_revision_rejects_without_writing(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )

    with pytest.raises(TeamStateConflictError):
        repo.create_project(
            project_id="project",
            team_id="team",
            name="Project",
            lead_agent_id="lead",
            lead_session_id="session-lead",
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 1
    assert state.projects == {}


def test_repository_rejects_malformed_existing_state(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.store.write_object(
        {
            "schema_version": 1,
            "revision": 0,
            "teams": {
                "team": {
                    "id": "team",
                    "name": "Team",
                    "manager_agent_id": "missing",
                }
            },
        }
    )

    with pytest.raises(ValidationError, match="missing manager"):
        repo.read()


def test_deleting_addon_state_does_not_affect_sessions_file(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    sessions_path = runtime_dir / "sessions.json"
    sessions_path.write_text('{"session": {"metadata": {}}}', encoding="utf-8")

    shutil.rmtree(runtime_dir / "addons" / "team-addon")

    assert sessions_path.read_text(encoding="utf-8") == '{"session": {"metadata": {}}}'
    assert not (runtime_dir / "addons" / "team-addon").exists()
    assert repo.runtime_dir == runtime_dir


def test_apply_agent_action_allows_self_and_subordinate_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "id": "task-self",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Lead self task",
                    "description": "Work the lead owns.",
                },
                {
                    "id": "task-sub",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Backend task",
                    "description": "Work for a direct subordinate.",
                },
            ],
        },
    )

    assert result["success"] is True
    assert set(result["created_task_ids"]) == {"task-self", "task-sub"}
    assert state.tasks["task-self"].created_by_agent_id == "lead"
    assert state.tasks["task-self"].state == TeamTaskState.PENDING
    assert state.tasks["task-sub"].owner_agent_id == "backend"


def test_apply_agent_action_project_lead_can_target_project_hierarchy(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # worker is in the project but reports to backend, not directly to the lead.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Deep task",
                    "description": "Task for an indirect report in the project.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_task_ids"]) == 1
    created_id = result["created_task_ids"][0]
    assert state.tasks[created_id].owner_agent_id == "worker"


def test_apply_agent_action_manager_can_assign_project_leads(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "task_creations": [
                {
                    "id": "task-lead",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 0,
                    "title": "Lead directive",
                    "description": "Manager assigns the project lead.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.tasks["task-lead"].owner_agent_id == "lead"
    assert state.tasks["task-lead"].created_by_agent_id == "manager"


def test_apply_agent_action_rejects_unrelated_agent_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend cannot create a task for its own superior.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Nope",
                    "description": "Backend cannot assign its superior.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert state.tasks == {}
    assert state.agent_runtimes["backend"].no_progress_count == 1


def test_apply_agent_action_rejects_cross_project_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=4,
    )

    # lead leads "project" but tries to place a task on backend inside "project-2".
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project-2",
                    "priority": 1,
                    "title": "Cross project",
                    "description": "Backend does not belong to project-2.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("outside project" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_rejects_terminal_task_creation_state(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Terminal",
                    "description": "Cannot be created already accepted.",
                    "state": "accepted",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("terminal" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_accepts_explicit_non_terminal_state(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "id": "task-progress",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 0,
                    "title": "In progress",
                    "description": "Created already in progress.",
                    "state": "in_progress",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.tasks["task-progress"].state == TeamTaskState.IN_PROGRESS


def test_apply_agent_action_creates_status_request_for_subordinate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "status_requests": [
                {"recipient_agent_id": "backend", "question": "Status on the backend slice?"}
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_status_request_ids"]) == 1
    item_id = result["created_status_request_ids"][0]
    item = state.inbox[item_id]
    assert item.recipient_agent_id == "backend"
    assert item.sender_agent_id == "lead"
    assert item.item_type == TeamInboxItemType.STATUS_REQUEST
    assert item.payload["question"] == "Status on the backend slice?"


def test_apply_agent_action_rejects_status_request_to_non_subordinate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "status_requests": [
                {"recipient_agent_id": "lead", "question": "Status?"}
            ],
        },
    )

    assert result["success"] is False
    assert result["created_status_request_ids"] == []
    assert any("direct subordinate" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_rejects_question_to_non_superior(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend's superior is lead; sending to worker (a subordinate) is rejected.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [{"recipient_agent_id": "worker", "question": "?"}],
        },
    )

    assert result["success"] is False
    assert any("direct superior" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_manager_question_redirected_to_escalations(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "questions": [{"recipient_agent_id": "lead", "question": "?"}],
        },
    )

    assert result["success"] is False
    assert any("operator_escalations" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_task_creation_resets_no_progress_count(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    repo.apply_agent_action(action={"agent_id": "backend"}, valid=False, error="boom")
    assert repo.read().agent_runtimes["backend"].no_progress_count == 1

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Self serve",
                    "description": "Backend assigns itself follow-up work.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.agent_runtimes["backend"].no_progress_count == 0


def _set_operator_target(repo: TeamStateRepository, target: str) -> None:
    def _mutate(state):
        state.teams["team"].operator_communication_target_id = target
        return state

    repo.update(_mutate)


def test_apply_agent_action_manager_operator_escalation_emits_event(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "operator_escalations": [
                {
                    "title": "Need a decision",
                    "message": "Blocked waiting on operator input.",
                    "urgency": "high",
                    "project_id": "project",
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["operator_escalation_event_ids"]) == 1
    assert state.agent_runtimes["manager"].no_progress_count == 0
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["event_type"] == "team.operator.escalation_requested"
    assert event["payload"] == {
        "team_id": "team",
        "manager_agent_id": "manager",
        "communication_target_id": "operator:ops-channel",
        "title": "Need a decision",
        "message": "Blocked waiting on operator input.",
        "urgency": "high",
        "project_id": "project",
    }
    escalation_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.ESCALATION
    ]
    assert len(escalation_items) == 1
    audit = escalation_items[0]
    assert audit.recipient_agent_id == "manager"
    assert audit.payload["communication_target_id"] == "operator:ops-channel"
    assert audit.payload["urgency"] == "high"


def test_apply_agent_action_rejects_non_manager_operator_escalation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "operator_escalations": [
                {"title": "Hi", "message": "Please look."}
            ],
        },
    )

    assert result["success"] is False
    assert result["operator_escalation_event_ids"] == []
    assert result["events"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert all(
        item.item_type != TeamInboxItemType.ESCALATION for item in state.inbox.values()
    )


def test_apply_agent_action_escalation_without_target_records_failure(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "operator_escalations": [
                {"title": "Hi", "message": "Please look."}
            ],
        },
    )

    assert result["success"] is False
    assert result["operator_escalation_event_ids"] == []
    assert result["events"] == []
    assert any(
        "operator_communication_target_id" in error for error in result["errors"]
    )
    assert state.inbox == {}
    record = next(iter(state.action_records.values()))
    assert record.success is False
