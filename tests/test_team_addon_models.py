from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_sandbox.models import SessionManifest
from truman_team_addon.git_naming import (
    canonical_release_branch,
    ephemeral_agent_branch,
    feature_branch,
    release_branch,
    review_branch,
    validate_branch_name,
)
from truman_team_addon.models import (
    AgentTask,
    Deliverable,
    EphemeralAssignment,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    team_state_payload,
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


def test_project_git_config_requires_exactly_one_bare_repo() -> None:
    config = ProjectGitConfig(host_repo_path="/srv/repo", bare_repo_name="repo.git")
    assert config.bare_repo_name == "repo.git"
    assert config.base_branch == "main"
    assert config.canonical_release_branch_prefix == "releases/<project-id>/"

    path_config = ProjectGitConfig(
        host_repo_path="/srv/repo", bare_repo_path="/srv/bare/repo.git"
    )
    assert path_config.bare_repo_path == "/srv/bare/repo.git"

    with pytest.raises(ValidationError, match="exactly one of bare_repo_name"):
        ProjectGitConfig(host_repo_path="/srv/repo")

    with pytest.raises(ValidationError, match="exactly one of bare_repo_name"):
        ProjectGitConfig(
            host_repo_path="/srv/repo",
            bare_repo_name="repo.git",
            bare_repo_path="/srv/bare/repo.git",
        )


def test_project_accepts_git_config() -> None:
    state = _valid_state().model_dump(mode="python")
    state["projects"]["project"]["git"] = {
        "host_repo_path": "/srv/repo",
        "bare_repo_name": "repo.git",
    }

    validated = TeamState.model_validate(state)

    assert validated.projects["project"].git is not None
    assert validated.projects["project"].git.host_repo_path == "/srv/repo"


@pytest.mark.parametrize(
    "bad_name",
    [":", "/x", "a..b", "refs/heads/x", "feature branch", "ctrl\x01char", ""],
)
def test_validate_branch_name_rejects_bad_names(bad_name: str) -> None:
    with pytest.raises(ValueError):
        validate_branch_name(bad_name)


def test_validate_branch_name_accepts_plain_names() -> None:
    assert validate_branch_name("main") == "main"
    assert validate_branch_name("team/team-1/features/task-1-ui") == (
        "team/team-1/features/task-1-ui"
    )


def test_deterministic_branch_name_builders() -> None:
    assert ephemeral_agent_branch("session-1", "assignment-1") == (
        "agents/session-1/assignment-1"
    )
    assert review_branch("team-1", "assignment-1") == (
        "team/team-1/reviews/assignment-1"
    )
    assert feature_branch("team-1", "task-1", "ui") == (
        "team/team-1/features/task-1-ui"
    )
    assert release_branch("team-1", "release-1") == "team/team-1/releases/release-1"


def test_canonical_release_branch_honors_project_prefix() -> None:
    default_project = Project(
        id="project", team_id="team", name="Project", lead_agent_id="lead"
    )
    assert canonical_release_branch(default_project, "release-1") == (
        "releases/project/release-1"
    )

    custom_project = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        git=ProjectGitConfig(
            host_repo_path="/srv/repo",
            bare_repo_name="repo.git",
            canonical_release_branch_prefix="ship/",
        ),
    )
    assert canonical_release_branch(custom_project, "release-1") == "ship/release-1"


def test_git_promotion_and_release_round_trip_through_team_state() -> None:
    state = _valid_state()
    state.deliverables["deliverable"].git_refs.feature_branch = (
        "team/team/features/task-ui"
    )
    state.release_packages["release"].feature_branches = ["team/team/features/task-ui"]
    state.release_packages["release"].release_branch = "team/team/releases/release"
    state.release_packages["release"].canonical_branch = "releases/project/release"
    state.git_promotions = {
        "promotion-1": GitPromotionRecord(
            id="promotion-1",
            project_id="project",
            deliverable_id="deliverable",
            actor_agent_id="lead",
            source_branch="agents/session-ui/assignment",
            target_branch="team/team/reviews/assignment",
            promotion_kind="review",
        )
    }

    validated = TeamState.model_validate(state.model_dump(mode="python"))

    promotion = validated.git_promotions["promotion-1"]
    assert promotion.state == "pending"
    assert promotion.promotion_kind == "review"
    assert validated.deliverables["deliverable"].git_refs.feature_branch == (
        "team/team/features/task-ui"
    )
    assert validated.release_packages["release"].canonical_branch == (
        "releases/project/release"
    )


def _state_with_task_graph() -> dict:
    """A _valid_state payload with a parent task and two dependency tasks."""
    state = _valid_state().model_dump(mode="python")
    project = state["projects"]["project"]
    state["tasks"]["parent"] = {
        "id": "parent",
        "owner_agent_id": "ui",
        "project_id": "project",
        "priority": 5,
        "state": TeamTaskState.IN_PROGRESS.value,
        "title": "Parent",
        "description": "Parent task",
        "created_by_agent_id": "lead",
    }
    state["tasks"]["dep-a"] = {
        "id": "dep-a",
        "owner_agent_id": "ui",
        "project_id": "project",
        "priority": 6,
        "state": TeamTaskState.ACCEPTED.value,
        "title": "Dep A",
        "description": "Dependency A",
        "created_by_agent_id": "lead",
    }
    state["tasks"]["dep-b"] = {
        "id": "dep-b",
        "owner_agent_id": "ui",
        "project_id": "project",
        "priority": 7,
        "state": TeamTaskState.PENDING.value,
        "title": "Dep B",
        "description": "Dependency B",
        "created_by_agent_id": "lead",
    }
    # Make "task" a child of "parent" depending on both dep tasks.
    state["tasks"]["task"]["parent_task_id"] = "parent"
    state["tasks"]["task"]["dependency_task_ids"] = ["dep-a", "dep-b"]
    assert project  # keep reference for clarity
    return state


def test_task_graph_loads_with_parent_and_dependencies() -> None:
    validated = TeamState.model_validate(_state_with_task_graph())

    assert validated.tasks["task"].parent_task_id == "parent"
    assert validated.tasks["task"].dependency_task_ids == ["dep-a", "dep-b"]


def test_old_shape_task_without_graph_fields_validates() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"].pop("parent_task_id", None)
    state["tasks"]["task"].pop("dependency_task_ids", None)

    validated = TeamState.model_validate(state)

    assert validated.tasks["task"].parent_task_id is None
    assert validated.tasks["task"].dependency_task_ids == []


def test_task_graph_rejects_missing_parent() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"]["parent_task_id"] = "ghost"

    with pytest.raises(ValidationError, match="missing parent ghost"):
        TeamState.model_validate(state)


def test_task_graph_rejects_missing_dependency() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"]["dependency_task_ids"] = ["ghost"]

    with pytest.raises(ValidationError, match="missing dependency ghost"):
        TeamState.model_validate(state)


def test_task_graph_rejects_cross_project_parent() -> None:
    state = _state_with_task_graph()
    state["tasks"]["parent"]["project_id"] = "other"
    # Decouple "task" from the (now cross-project) dependency tasks to isolate parent.
    state["tasks"]["task"]["dependency_task_ids"] = []
    state["projects"]["other"] = {
        "id": "other",
        "team_id": "team",
        "name": "Other",
        "lead_agent_id": "lead",
    }
    state["teams"]["team"]["project_ids"] = ["project", "other"]

    with pytest.raises(ValidationError, match="parent parent belongs to project other"):
        TeamState.model_validate(state)


def test_task_graph_rejects_cross_project_dependency() -> None:
    state = _state_with_task_graph()
    state["tasks"]["task"]["parent_task_id"] = None
    state["tasks"]["dep-a"]["project_id"] = "other"
    state["projects"]["other"] = {
        "id": "other",
        "team_id": "team",
        "name": "Other",
        "lead_agent_id": "lead",
    }
    state["teams"]["team"]["project_ids"] = ["project", "other"]

    with pytest.raises(
        ValidationError, match="dependency dep-a belongs to project other"
    ):
        TeamState.model_validate(state)


def test_task_graph_rejects_parent_cycle() -> None:
    state = _state_with_task_graph()
    # parent <- task already; close the loop so parent points back to task.
    state["tasks"]["parent"]["parent_task_id"] = "task"

    with pytest.raises(ValidationError, match="parent cycle"):
        TeamState.model_validate(state)


def test_task_graph_rejects_dependency_cycle() -> None:
    state = _state_with_task_graph()
    # task -> dep-a, dep-b; add dep-a -> task to create a cycle.
    state["tasks"]["dep-a"]["dependency_task_ids"] = ["task"]

    with pytest.raises(ValidationError, match="dependency cycle"):
        TeamState.model_validate(state)


def test_task_rejects_self_parent() -> None:
    with pytest.raises(ValidationError, match="cannot be its own parent"):
        AgentTask(
            id="task",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="Self",
            description="Self parent",
            created_by_agent_id="lead",
            parent_task_id="task",
        )


def test_task_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        AgentTask(
            id="task",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="Self",
            description="Self dependency",
            created_by_agent_id="lead",
            dependency_task_ids=["task"],
        )


def test_task_graph_derived_helpers() -> None:
    validated = TeamState.model_validate(_state_with_task_graph())

    # "task" is the only child of "parent".
    assert validated.child_task_ids("parent") == ["task"]
    assert validated.child_task_ids("task") == []

    # dep-a accepted, dep-b pending -> waiting.
    assert validated.dependency_status("task") == "waiting"
    assert validated.blocked_by_task_ids("task") == ["dep-b"]

    # parent has no dependencies -> satisfied.
    assert validated.dependency_status("parent") == "satisfied"
    assert validated.blocked_by_task_ids("parent") == []

    counts = validated.subtask_counts("parent")
    assert counts == {
        "total": 1,
        "accepted": 0,
        "finished": 0,
        "blocked": 0,
        "open": 1,
    }


def test_team_state_payload_includes_derived_task_fields() -> None:
    state = TeamState.model_validate(_state_with_task_graph())

    payload = team_state_payload(state)

    task = payload["tasks"]["task"]
    assert task["parent_task_id"] == "parent"
    assert task["dependency_task_ids"] == ["dep-a", "dep-b"]
    assert task["child_task_ids"] == []
    assert task["dependency_status"] == "waiting"
    assert task["blocked_by_task_ids"] == ["dep-b"]
    assert task["subtask_counts"] == {
        "total": 0,
        "accepted": 0,
        "finished": 0,
        "blocked": 0,
        "open": 0,
    }

    parent = payload["tasks"]["parent"]
    assert parent["child_task_ids"] == ["task"]
    assert parent["dependency_status"] == "satisfied"
    assert parent["subtask_counts"] == {
        "total": 1,
        "accepted": 0,
        "finished": 0,
        "blocked": 0,
        "open": 1,
    }


def test_dependency_status_satisfied_and_blocked() -> None:
    state = _state_with_task_graph()
    # Both dependencies accepted/finished -> satisfied.
    state["tasks"]["dep-b"]["state"] = TeamTaskState.FINISHED.value
    satisfied = TeamState.model_validate(state)
    assert satisfied.dependency_status("task") == "satisfied"
    assert satisfied.blocked_by_task_ids("task") == []

    # A blocked dependency -> blocked.
    blocked_state = _state_with_task_graph()
    blocked_state["tasks"]["dep-b"]["state"] = TeamTaskState.BLOCKED.value
    blocked = TeamState.model_validate(blocked_state)
    assert blocked.dependency_status("task") == "blocked"
    assert sorted(blocked.blocked_by_task_ids("task")) == ["dep-b"]


def test_subtask_counts_classifies_child_states() -> None:
    state = _state_with_task_graph()
    # Add more children of "parent" in varied states.
    for child_id, child_state in (
        ("child-accepted", TeamTaskState.ACCEPTED),
        ("child-finished", TeamTaskState.FINISHED),
        ("child-blocked", TeamTaskState.BLOCKED),
        ("child-rejected", TeamTaskState.REJECTED),
    ):
        state["tasks"][child_id] = {
            "id": child_id,
            "owner_agent_id": "ui",
            "project_id": "project",
            "priority": 9,
            "state": child_state.value,
            "title": child_id,
            "description": child_id,
            "created_by_agent_id": "lead",
            "parent_task_id": "parent",
        }

    validated = TeamState.model_validate(state)

    counts = validated.subtask_counts("parent")
    # "task" is WAITING_FOR_REVIEW (open) plus the four added children.
    assert counts == {
        "total": 5,
        "accepted": 1,
        "finished": 1,
        "blocked": 2,
        "open": 1,
    }


def test_git_promotion_rejects_missing_project() -> None:
    state = _valid_state().model_dump(mode="python")
    state["git_promotions"] = {
        "promotion-1": {
            "id": "promotion-1",
            "project_id": "missing-project",
            "actor_agent_id": "lead",
            "source_branch": "agents/session-ui/assignment",
            "target_branch": "team/team/reviews/assignment",
            "promotion_kind": "review",
        }
    }

    with pytest.raises(ValidationError, match="missing project missing-project"):
        TeamState.model_validate(state)
