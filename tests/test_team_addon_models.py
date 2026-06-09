from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_sandbox.models import SessionManifest
from truman_team_addon.models import (
    AgentTask,
    Deliverable,
    EphemeralAssignment,
    PersistentAgent,
    Project,
    ProjectGoal,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)


def _valid_state() -> TeamState:
    manager = PersistentAgent(
        id="manager",
        team_id="team",
        role="manager",
        session_id="session-manager",
        subordinate_agent_ids=["lead"],
    )
    lead = PersistentAgent(
        id="lead",
        team_id="team",
        project_id="project",
        role="project_lead",
        session_id="session-lead",
        reports_to_agent_id="manager",
        subordinate_agent_ids=["ui", "reviewer"],
    )
    ui = PersistentAgent(
        id="ui",
        team_id="team",
        project_id="project",
        role="ui",
        session_id="session-ui",
        reports_to_agent_id="lead",
    )
    reviewer = PersistentAgent(
        id="reviewer",
        team_id="team",
        project_id="project",
        role="reviewer",
        session_id="session-reviewer",
        reports_to_agent_id="lead",
    )
    return TeamState(
        teams={
            "team": Team(
                id="team",
                name="Team",
                manager_agent_id="manager",
                project_ids=["project"],
            )
        },
        projects={
            "project": Project(
                id="project",
                team_id="team",
                name="Project",
                lead_agent_id="lead",
                goals=[
                    ProjectGoal(
                        id="goal-1",
                        description="Ship reviewed work",
                        state=TeamRecordState.IN_PROGRESS,
                    )
                ],
            )
        },
        agents={
            "manager": manager,
            "lead": lead,
            "ui": ui,
            "reviewer": reviewer,
        },
        tasks={
            "task": AgentTask(
                id="task",
                owner_agent_id="ui",
                project_id="project",
                priority=10,
                state=TeamTaskState.WAITING_FOR_REVIEW,
                title="Build UI",
                description="Implement the UI slice.",
                created_by_agent_id="lead",
                deliverable_ids=["deliverable"],
            )
        },
        deliverables={
            "deliverable": Deliverable(
                id="deliverable",
                task_id="task",
                producer_agent_id="ui",
                artifact_refs=["artifacts/ui.tar.gz"],
                summary="UI slice is ready.",
                state=TeamRecordState.IN_PROGRESS,
                review_request_ids=["review"],
            )
        },
        review_requests={
            "review": ReviewRequest(
                id="review",
                deliverable_id="deliverable",
                reviewer_agent_id="reviewer",
            )
        },
        release_packages={
            "release": ReleasePackage(
                id="release",
                project_id="project",
                deliverable_ids=["deliverable"],
                artifact_refs=["published/release.tar.gz"],
            )
        },
    )


def test_valid_team_state_loads() -> None:
    state = _valid_state()

    assert state.teams["team"].manager_agent_id == "manager"
    assert state.projects["project"].goals[0].id == "goal-1"


def test_team_state_rejects_missing_manager() -> None:
    with pytest.raises(ValidationError, match="missing manager"):
        TeamState(
            teams={
                "team": Team(id="team", name="Team", manager_agent_id="missing"),
            }
        )


def test_team_state_rejects_project_lead_outside_project() -> None:
    state = _valid_state().model_dump(mode="python")
    state["agents"]["lead"]["project_id"] = "other-project"

    with pytest.raises(ValidationError, match="lead lead belongs to project other-project"):
        TeamState.model_validate(state)


def test_team_state_rejects_reporting_cycle() -> None:
    state = _valid_state().model_dump(mode="python")
    state["agents"]["manager"]["reports_to_agent_id"] = "ui"

    with pytest.raises(ValidationError, match="reporting cycle"):
        TeamState.model_validate(state)


def test_ephemeral_assignments_do_not_join_reporting_hierarchy() -> None:
    state = _valid_state()
    state.ephemeral_assignments = {
        "ephemeral-1": EphemeralAssignment(
            id="ephemeral-1",
            parent_agent_id="ui",
            project_id="project",
            task_id="task",
            session_id="session-ephemeral",
            title="Draft UI implementation",
            prompt="Implement a scoped UI draft.",
        )
    }

    validated = TeamState.model_validate(state.model_dump(mode="python"))

    assert "session-ephemeral" not in {
        agent.session_id for agent in validated.agents.values()
    }
    assert validated.agents["ui"].subordinate_agent_ids == []


def test_tasks_are_ordered_by_priority_created_time_and_id() -> None:
    state = _valid_state()
    first = datetime(2026, 1, 1, tzinfo=UTC)
    second = datetime(2026, 1, 2, tzinfo=UTC)
    state.tasks = {
        "later": AgentTask(
            id="later",
            owner_agent_id="ui",
            project_id="project",
            priority=2,
            title="Later",
            description="Later task",
            created_by_agent_id="lead",
            created_at=second,
        ),
        "first-b": AgentTask(
            id="first-b",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="First B",
            description="First task B",
            created_by_agent_id="lead",
            created_at=first,
        ),
        "first-a": AgentTask(
            id="first-a",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="First A",
            description="First task A",
            created_by_agent_id="lead",
            created_at=first,
        ),
    }
    state.deliverables = {}
    state.review_requests = {}
    state.release_packages = {}
    state = TeamState.model_validate(state.model_dump(mode="python"))

    assert [task.id for task in state.ordered_tasks_for_agent("ui")] == [
        "first-a",
        "first-b",
        "later",
    ]


def test_session_manifest_has_no_team_addon_fields() -> None:
    assert "team_id" not in SessionManifest.model_fields
    assert "project_id" not in SessionManifest.model_fields
    assert "agent_id" not in SessionManifest.model_fields
