from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from truman_team_addon.models import (
    EphemeralAgentPolicy,
    EphemeralAssignmentState,
    ProjectGitConfig,
    ProjectGoal,
    ProjectStatus,
    TeamInboxItemType,
    TeamRecordState,
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


def _seed_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    owner_agent_id: str = "backend",
) -> None:
    repo.create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id=owner_agent_id,
        project_id="project",
        priority=1,
        title=f"Task {task_id}",
        description="Work to deliver.",
        created_by_agent_id="lead",
    )


def _seed_deliverable(
    repo: TeamStateRepository,
    *,
    deliverable_id: str,
    task_id: str,
    producer_agent_id: str = "backend",
) -> None:
    repo.record_deliverable(
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id=producer_agent_id,
        summary="Deliverable summary.",
    )


def test_apply_agent_action_review_request_creates_pending_review(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    # lead leads the project, so it may request review of backend's deliverable.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Check the slice carefully.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["created_review_request_ids"] == ["review-1"]
    review = state.review_requests["review-1"]
    assert review.reviewer_agent_id == "worker"
    assert review.state == TeamRecordState.PENDING
    assert "review-1" in state.deliverables["deliverable"].review_request_ids
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    inbox_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.STATUS_REQUEST
        and item.recipient_agent_id == "worker"
    ]
    assert len(inbox_items) == 1
    assert inbox_items[0].payload["review_request_id"] == "review-1"
    assert inbox_items[0].payload["instructions"] == "Check the slice carefully."


def test_apply_agent_action_review_request_rejected_for_unrelated_agent(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    # worker neither produced nor supervises the producer (backend) and is not a lead.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "lead",
                    "instructions": "Please review.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_review_request_ids"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_accept_cascades_to_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "findings": ["Looks good."],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["decided_review_request_ids"] == ["review-1"]
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.decision_at is not None
    assert review.findings == ["Looks good."]
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_review_decision_reviewer_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    # lead is not the assigned reviewer (worker is).
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )

    assert result["success"] is False
    assert any("assigned reviewer" in error for error in result["errors"])
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING


def test_apply_agent_action_multi_review_holds_back_deliverable(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "First.",
                },
                {
                    "id": "review-2",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "backend",
                    "instructions": "Second.",
                },
            ],
        },
    )

    # Accepting only the first review must not accept the deliverable yet.
    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state != TeamTaskState.ACCEPTED

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "review_decisions": [
                {"review_request_id": "review-2", "state": "accepted"}
            ],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_multi_deliverable_holds_back_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable-a", task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable-b", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-a",
                    "deliverable_id": "deliverable-a",
                    "reviewer_agent_id": "worker",
                    "instructions": "A.",
                },
                {
                    "id": "review-b",
                    "deliverable_id": "deliverable-b",
                    "reviewer_agent_id": "worker",
                    "instructions": "B.",
                },
            ],
        },
    )

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-a", "state": "accepted"}
            ],
        },
    )
    assert state.deliverables["deliverable-a"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state != TeamTaskState.ACCEPTED

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-b", "state": "accepted"}
            ],
        },
    )
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_review_rejected_blocks_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "rejected",
                    "findings": ["Needs rework."],
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.review_requests["review-1"].state == TeamRecordState.REJECTED
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert state.projects["project"].status == ProjectStatus.BLOCKED


def test_apply_agent_action_review_blocked_blocks_all(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "blocked"}
            ],
        },
    )

    assert result["success"] is True
    assert state.review_requests["review-1"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert state.projects["project"].status == ProjectStatus.BLOCKED


def test_apply_agent_action_goal_update_permission(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend is neither the project lead nor the team manager.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "accepted"}
            ],
        },
    )
    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.projects["project"].goals[0].state == TeamRecordState.PENDING

    # the project lead may update the goal.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "in_progress"}
            ],
        },
    )
    assert result["success"] is True
    assert result["updated_goal_ids"] == ["goal"]
    assert state.projects["project"].goals[0].state == TeamRecordState.IN_PROGRESS

    # the team manager may also update the goal.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "accepted"}
            ],
        },
    )
    assert result["success"] is True
    assert state.projects["project"].goals[0].state == TeamRecordState.ACCEPTED


def test_apply_agent_action_project_status_completes(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )
    repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )
    # Task is accepted; project not yet complete until the goal is accepted.
    assert repo.read().projects["project"].status == ProjectStatus.ACTIVE

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "accepted"}
            ],
        },
    )

    assert state.projects["project"].status == ProjectStatus.COMPLETED


def test_apply_agent_action_goal_update_resets_no_progress_count(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    repo.apply_agent_action(action={"agent_id": "lead"}, valid=False, error="boom")
    assert repo.read().agent_runtimes["lead"].no_progress_count == 1

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "in_progress"}
            ],
        },
    )

    assert result["success"] is True
    assert state.agent_runtimes["lead"].no_progress_count == 0


def _set_project_git(
    repo: TeamStateRepository,
    *,
    bare_repo_name: str | None = "Truman.git",
    canonical_origin_url: str | None = "https://example.invalid/Truman.git",
    provider: str | None = "azure_devops",
) -> None:
    def _mutate(state):
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name=bare_repo_name,
            canonical_origin_url=canonical_origin_url,
            provider=provider,
        )
        return state

    repo.update(_mutate)


def _seed_completed_ephemeral(
    repo: TeamStateRepository,
    *,
    assignment_id: str = "ephemeral-1",
    session_id: str = "eph-1",
    agent_branch: str | None = None,
) -> None:
    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=2)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    repo.request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": assignment_id,
                "task_id": "task",
                "session_id": session_id,
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )
    repo.apply_ephemeral_action(
        session_id=session_id,
        action={
            "ephemeral_assignment_id": assignment_id,
            "summary": "Done.",
            "agent_branch": agent_branch
            if agent_branch is not None
            else f"agents/{session_id}/{assignment_id}",
            "commit_sha": "abc1234",
        },
    )


def test_apply_ephemeral_action_accepts_matching_agent_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    repo.request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "eph-1",
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )

    state, result = repo.apply_ephemeral_action(
        session_id="eph-1",
        action={
            "ephemeral_assignment_id": "ephemeral-1",
            "summary": "Done.",
            "agent_branch": "agents/eph-1/ephemeral-1",
            "commit_sha": "deadbeef",
        },
    )

    assert result["success"] is True
    assignment = state.ephemeral_assignments["ephemeral-1"]
    assert assignment.state == EphemeralAssignmentState.COMPLETED
    assert assignment.agent_branch == "agents/eph-1/ephemeral-1"
    assert assignment.commit_sha == "deadbeef"


def test_apply_ephemeral_action_rejects_mismatched_agent_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    repo.request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "eph-1",
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )

    state, result = repo.apply_ephemeral_action(
        session_id="eph-1",
        action={
            "ephemeral_assignment_id": "ephemeral-1",
            "summary": "Done.",
            "agent_branch": "agents/other/wrong",
        },
    )

    assert result["success"] is False
    assignment = state.ephemeral_assignments["ephemeral-1"]
    assert assignment.state == EphemeralAssignmentState.FAILED
    assert assignment.agent_branch is None
    assert "does not match" in (result["error"] or "")


def test_apply_agent_action_git_review_promotion_parent_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_completed_ephemeral(repo)

    # lead is not the parent of the ephemeral assignment and cannot promote it.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )
    assert result["success"] is False
    assert any("cannot promote" in error for error in result["errors"])
    assert state.git_promotions == {}

    # backend (the parent) may promote its completed assignment's branch.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )
    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    promotion_id = result["git_promotion_event_ids"][0]
    promotion = state.git_promotions[promotion_id]
    assert promotion.promotion_kind == "review"
    assert promotion.state == "pending"
    assert promotion.source_branch == "agents/eph-1/ephemeral-1"
    assert promotion.target_branch == "team/team/reviews/ephemeral-1"
    assert promotion.project_id == "project"
    event = result["events"][0]
    assert event["event_type"] == "team.git.promotion_requested"
    assert event["payload"]["promotion_kind"] == "review"
    assert event["payload"]["host_repo_path"] == "/opt/repos/Truman"
    assert event["payload"]["bare_repo_name"] == "Truman.git"
    assert event["payload"]["source_branch"] == "agents/eph-1/ephemeral-1"
    assert event["payload"]["target_branch"] == "team/team/reviews/ephemeral-1"


def test_apply_agent_action_git_review_promotion_requires_git_config(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_completed_ephemeral(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )

    assert result["success"] is False
    assert any("no git configuration" in error for error in result["errors"])
    assert state.git_promotions == {}


def test_apply_agent_action_git_feature_update_default_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": [
                        "team/team/reviews/ephemeral-1",
                        "team/team/reviews/ephemeral-2",
                    ],
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    promotion = state.git_promotions[result["git_promotion_event_ids"][0]]
    assert promotion.promotion_kind == "feature"
    assert promotion.target_branch == "team/team/features/task-backend"
    event = result["events"][0]
    assert event["payload"]["promotion_kind"] == "feature"
    assert event["payload"]["source_branches"] == [
        "team/team/reviews/ephemeral-1",
        "team/team/reviews/ephemeral-2",
    ]
    assert event["payload"]["target_branch"] == "team/team/features/task-backend"


def test_apply_agent_action_git_feature_update_supervisor_allowed(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    # lead supervises backend (the task owner) and may consolidate the task branches.
    _state, lead_result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_feature_updates": [
                {"task_id": "task", "source_review_branches": ["team/team/reviews/x"]}
            ],
        },
    )
    assert lead_result["success"] is True

    # worker neither owns nor supervises the task owner.
    _state, worker_result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "git_feature_updates": [
                {"task_id": "task", "source_review_branches": ["team/team/reviews/x"]}
            ],
        },
    )
    assert worker_result["success"] is False
    assert any("not permitted" in error for error in worker_result["errors"])


def test_apply_agent_action_git_feature_update_rejects_bad_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": ["bad:branch"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("invalid branch name" in error for error in result["errors"])
    assert state.git_promotions == {}


def _seed_accepted_feature_deliverable(
    repo: TeamStateRepository,
    *,
    deliverable_id: str,
    task_id: str,
    feature: str | None,
    artifact_refs: list[str] | None = None,
) -> None:
    _seed_task(repo, task_id=task_id)
    repo.record_deliverable(
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id="backend",
        summary="Deliverable summary.",
        artifact_refs=artifact_refs or [],
    )

    def _mutate(state):
        deliverable = state.deliverables[deliverable_id]
        deliverable.state = TeamRecordState.ACCEPTED
        if feature is not None:
            deliverable.git_refs.feature_branch = feature
        return state

    repo.update(_mutate)


def test_apply_agent_action_git_release_request_creates_package(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["release_package_ids"] == ["rel-1"]
    release = state.release_packages["rel-1"]
    assert release.state == TeamRecordState.PENDING
    assert release.feature_branches == ["team/team/features/task-backend"]
    assert release.release_branch == "team/team/releases/rel-1"
    assert release.canonical_branch == "releases/project/rel-1"
    assert len(release.git_promotion_record_ids) == 1
    kinds = sorted(
        state.git_promotions[pid].promotion_kind
        for pid in release.git_promotion_record_ids
    )
    assert kinds == ["release"]
    assert len(result["git_promotion_event_ids"]) == 1
    payloads = {event["payload"]["promotion_kind"]: event["payload"] for event in result["events"]}
    assert payloads["release"]["source_branches"] == ["team/team/features/task-backend"]
    assert payloads["release"]["target_branch"] == "team/team/releases/rel-1"
    assert "canonical_push" not in payloads

    release_promotion_id = release.git_promotion_record_ids[0]
    state, promotion_result = repo.record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        commit_sha="feedface",
    )
    release = state.release_packages["rel-1"]
    canonical_promotions = [
        state.git_promotions[promotion_id]
        for promotion_id in release.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "canonical_push"
    ]
    assert len(canonical_promotions) == 1
    canonical = canonical_promotions[0]
    assert canonical.source_branch == "team/team/releases/rel-1"
    assert canonical.target_branch == "releases/project/rel-1"
    assert promotion_result["git_promotion_event_ids"] == [canonical.id]
    payloads = {
        event["payload"]["promotion_kind"]: event["payload"]
        for event in promotion_result["events"]
    }
    assert payloads["canonical_push"]["source_branch"] == "team/team/releases/rel-1"
    assert payloads["canonical_push"]["target_branch"] == "releases/project/rel-1"
    assert payloads["canonical_push"]["canonical_origin_url"] == (
        "https://example.invalid/Truman.git"
    )
    assert payloads["canonical_push"]["provider"] == "azure_devops"
    assert state.projects["project"].status == ProjectStatus.ACTIVE


def test_apply_agent_action_git_release_request_artifact_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
        artifact_refs=["artifact-space:/teams/team/result"],
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"id": "rel-1", "project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is True
    release = state.release_packages["rel-1"]
    assert release.feature_branches == []
    assert release.git_promotion_record_ids == []
    assert result["git_promotion_event_ids"] == []
    assert state.git_promotions == {}


def test_apply_agent_action_git_release_request_rejects_unaccepted(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert any("not accepted" in error for error in result["errors"])
    assert state.release_packages == {}


def test_apply_agent_action_git_release_request_requires_lead_or_manager(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_release_requests": [
                {"project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.release_packages == {}
