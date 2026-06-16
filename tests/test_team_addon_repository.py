from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from truman_team_addon.models import (
    AgentRuntime,
    AgentStatus,
    EphemeralAgentPolicy,
    EphemeralAssignmentState,
    ProjectGitConfig,
    ProjectGoal,
    ProjectStatus,
    ReviewProfileCriterion,
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
        manager_display_name="  Team Manager  ",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
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
        display_name="Backend Agent",
        expected_revision=2,
    )
    repo.register_agent(
        agent_id="worker",
        team_id="team",
        project_id="project",
        role="worker",
        session_id="session-worker",
        reports_to_agent_id="backend",
        display_name="  Worker Agent  ",
        expected_revision=3,
    )
    return repo


def _auto_dev_squad_inputs(
    *,
    suffix: str = "auto-dev-squad",
    duplicate_reviewer_id: str | None = None,
    bad_git: bool = False,
) -> dict[str, object]:
    reviewer_agent_id = duplicate_reviewer_id or f"reviewer-{suffix}"
    project_git: dict[str, object] = {
        "host_repo_path": f"/srv/repos/{suffix}",
        "bare_repo_name": f"{suffix}.git",
    }
    if bad_git:
        project_git["bare_repo_path"] = f"/srv/git/{suffix}.git"
    return {
        "team": {
            "id": f"team-{suffix}",
            "name": "Auto-Dev Squad",
            "operator_communication_target_id": "slack://ops-channel",
        },
        "project": {
            "id": f"project-{suffix}",
            "name": "Reviewed Auto-Dev Delivery",
            "git": project_git,
        },
        "manager": {
            "agent_id": f"manager-{suffix}",
            "session_id": f"session-manager-{suffix}",
        },
        "project_lead": {
            "agent_id": f"project-lead-{suffix}",
            "session_id": f"session-project-lead-{suffix}",
        },
        "implementer": {
            "agent_id": f"implementer-{suffix}",
            "session_id": f"session-implementer-{suffix}",
        },
        "reviewer": {
            "agent_id": reviewer_agent_id,
            "session_id": f"session-reviewer-{suffix}",
        },
        "release_operator": {
            "agent_id": f"release-operator-{suffix}",
            "session_id": f"session-release-operator-{suffix}",
        },
    }


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
        manager_display_name="  Team Manager  ",
        expected_revision=0,
    )
    assert state.revision == 1
    assert state.agents["manager"].display_name == "Team Manager"

    state = repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[ProjectGoal(id="goal", description="Complete project")],
        expected_revision=1,
    )
    assert state.revision == 2
    assert state.agents["lead"].display_name == "Project Lead"

    state = repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        display_name="  Backend Agent  ",
        expected_revision=2,
    )
    assert state.revision == 3
    assert state.agents["manager"].subordinate_agent_ids == ["lead"]
    assert state.agents["lead"].subordinate_agent_ids == ["backend"]
    assert state.agents["backend"].display_name == "Backend Agent"


def test_repository_updates_team_project_and_agent_records(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = repo.update_team(
        team_id="team",
        name="Renamed Team",
        operator_communication_target_id=None,
        set_operator_communication_target_id=True,
        archived=True,
        expected_revision=4,
    )

    team = state.teams["team"]
    assert state.revision == 5
    assert team.name == "Renamed Team"
    assert team.operator_communication_target_id is None
    assert team.archived is True
    assert {agent.id for agent in state.agents.values() if agent.status is AgentStatus.ARCHIVED} == {
        "backend",
        "lead",
        "manager",
        "worker",
    }

    state = repo.update_project(
        project_id="project",
        name="Renamed Project",
        status=ProjectStatus.BLOCKED,
        git=ProjectGitConfig(host_repo_path="/srv/repo", bare_repo_name="repo.git"),
        set_git=True,
        goals=[ProjectGoal(id="goal-2", description="Updated goal")],
        expected_revision=5,
    )

    project = state.projects["project"]
    assert state.revision == 6
    assert project.name == "Renamed Project"
    assert project.status is ProjectStatus.BLOCKED
    assert project.git is not None
    assert project.git.bare_repo_name == "repo.git"
    assert [goal.id for goal in project.goals] == ["goal-2"]

    state = repo.update_agent(
        agent_id="worker",
        role="qa",
        session_id="session-worker-2",
        reports_to_agent_id="lead",
        set_reports_to_agent_id=True,
        display_name=None,
        set_display_name=True,
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=3),
        status=AgentStatus.PAUSED,
        expected_revision=6,
    )

    worker = state.agents["worker"]
    assert state.revision == 7
    assert worker.role == "qa"
    assert worker.session_id == "session-worker-2"
    assert worker.display_name is None
    assert worker.reports_to_agent_id == "lead"
    assert worker.ephemeral_policy.max_concurrent == 3
    assert worker.status is AgentStatus.PAUSED
    assert state.agents["backend"].subordinate_agent_ids == []
    assert state.agents["lead"].subordinate_agent_ids == ["backend", "worker"]


def test_repository_rejects_agent_update_reporting_cycle_without_writing(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValidationError, match="reporting cycle"):
        repo.update_agent(
            agent_id="lead",
            reports_to_agent_id="worker",
            set_reports_to_agent_id=True,
            expected_revision=4,
        )

    state = repo.read()
    assert state.revision == 4
    assert state.agents["lead"].reports_to_agent_id == "manager"
    assert state.agents["manager"].subordinate_agent_ids == ["lead"]


def test_repository_materializes_auto_dev_squad_atomically(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state, result = repo.materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(),
        expected_revision=0,
    )

    assert result["success"] is True
    assert result["template_id"] == "auto-dev-squad"
    assert result["template_name"] == "Auto-Dev Squad"
    assert result["team_id"] == "team-auto-dev-squad"
    assert result["project_id"] == "project-auto-dev-squad"
    assert result["agent_ids"] == {
        "manager": "manager-auto-dev-squad",
        "project_lead": "project-lead-auto-dev-squad",
        "implementer": "implementer-auto-dev-squad",
        "reviewer": "reviewer-auto-dev-squad",
        "release_operator": "release-operator-auto-dev-squad",
    }
    assert result["goal_ids"] == ["project-auto-dev-squad-auto-dev-reviewed-delivery"]
    assert result["review_profile_ids"] == ["project-auto-dev-squad-auto-dev-review"]
    assert result["task_ids"] == [
        "project-auto-dev-squad-implement-scoped-slice",
        "project-auto-dev-squad-release-handoff",
    ]
    assert result["revision"] == 1

    assert state.revision == 1
    team = state.teams["team-auto-dev-squad"]
    assert team.manager_agent_id == "manager-auto-dev-squad"
    assert team.operator_communication_target_id == "slack://ops-channel"
    assert team.project_ids == ["project-auto-dev-squad"]

    project = state.projects["project-auto-dev-squad"]
    assert project.team_id == "team-auto-dev-squad"
    assert project.lead_agent_id == "project-lead-auto-dev-squad"
    assert project.git is not None
    assert project.git.host_repo_path == "/srv/repos/auto-dev-squad"
    assert project.git.bare_repo_name == "auto-dev-squad.git"
    assert [goal.id for goal in project.goals] == [
        "project-auto-dev-squad-auto-dev-reviewed-delivery"
    ]

    assert state.agents["manager-auto-dev-squad"].subordinate_agent_ids == [
        "project-lead-auto-dev-squad"
    ]
    assert state.agents["project-lead-auto-dev-squad"].subordinate_agent_ids == [
        "implementer-auto-dev-squad",
        "reviewer-auto-dev-squad",
        "release-operator-auto-dev-squad",
    ]
    assert state.agents["project-lead-auto-dev-squad"].reports_to_agent_id == "manager-auto-dev-squad"
    assert state.agents["implementer-auto-dev-squad"].reports_to_agent_id == "project-lead-auto-dev-squad"
    assert state.agents["reviewer-auto-dev-squad"].reports_to_agent_id == "project-lead-auto-dev-squad"
    assert state.agents["release-operator-auto-dev-squad"].reports_to_agent_id == "project-lead-auto-dev-squad"
    assert {
        role_key: state.agents[agent_id].ephemeral_policy.max_concurrent
        for role_key, agent_id in result["agent_ids"].items()
    } == {
        "manager": 0,
        "project_lead": 1,
        "implementer": 2,
        "reviewer": 1,
        "release_operator": 1,
    }

    profile = state.review_profiles["project-auto-dev-squad-auto-dev-review"]
    assert profile.team_id == "team-auto-dev-squad"
    assert profile.project_id == "project-auto-dev-squad"
    assert [criterion.id for criterion in profile.criteria] == [
        "scope",
        "tests",
        "risk",
        "handoff",
    ]

    implement_task = state.tasks["project-auto-dev-squad-implement-scoped-slice"]
    release_task = state.tasks["project-auto-dev-squad-release-handoff"]
    assert implement_task.owner_agent_id == "implementer-auto-dev-squad"
    assert implement_task.created_by_agent_id == "project-lead-auto-dev-squad"
    assert release_task.owner_agent_id == "release-operator-auto-dev-squad"
    assert release_task.dependency_task_ids == [
        "project-auto-dev-squad-implement-scoped-slice"
    ]


def test_repository_materializes_auto_dev_squad_with_team_project_and_agents_shape(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    state, result = repo.materialize_template(
        "auto-dev-squad",
        inputs={
            "team": {
                "id": "team-auto-dev-squad",
                "name": "Auto-Dev Squad",
                "operator_communication_target_id": "slack://ops-channel",
                "manager_agent_id": "manager-auto-dev-squad",
                "manager_session_id": "session-manager-auto-dev-squad",
            },
            "project": {
                "id": "project-auto-dev-squad",
                "name": "Reviewed Auto-Dev Delivery",
                "lead_agent_id": "project-lead-auto-dev-squad",
                "lead_session_id": "session-project-lead-auto-dev-squad",
                "git": {
                    "host_repo_path": "/srv/repos/auto-dev-squad",
                    "bare_repo_name": "auto-dev-squad.git",
                },
            },
            "agents": {
                "implementer": {
                    "agent_id": "implementer-auto-dev-squad",
                    "session_id": "session-implementer-auto-dev-squad",
                },
                "reviewer": {
                    "agent_id": "reviewer-auto-dev-squad",
                    "session_id": "session-reviewer-auto-dev-squad",
                },
                "release_operator": {
                    "agent_id": "release-operator-auto-dev-squad",
                    "session_id": "session-release-operator-auto-dev-squad",
                },
            },
        },
        expected_revision=0,
    )

    assert result["success"] is True
    assert state.projects["project-auto-dev-squad"].lead_agent_id == "project-lead-auto-dev-squad"
    assert state.agents["manager-auto-dev-squad"].subordinate_agent_ids == [
        "project-lead-auto-dev-squad"
    ]
    assert state.agents["project-lead-auto-dev-squad"].subordinate_agent_ids == [
        "implementer-auto-dev-squad",
        "reviewer-auto-dev-squad",
        "release-operator-auto-dev-squad",
    ]


def test_repository_materialize_template_allows_multiple_unique_squads(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    first_state, first_result = repo.materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(suffix="alpha"),
        expected_revision=0,
    )
    second_state, second_result = repo.materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(suffix="beta"),
        expected_revision=first_state.revision,
    )

    assert first_result["review_profile_ids"] == ["project-alpha-auto-dev-review"]
    assert second_result["review_profile_ids"] == ["project-beta-auto-dev-review"]
    assert second_state.revision == 2
    assert set(second_state.teams) == {"team-alpha", "team-beta"}
    assert set(second_state.projects) == {"project-alpha", "project-beta"}
    assert {
        "project-alpha-implement-scoped-slice",
        "project-alpha-release-handoff",
        "project-beta-implement-scoped-slice",
        "project-beta-release-handoff",
    }.issubset(second_state.tasks)
    assert (
        second_state.tasks["project-beta-release-handoff"].dependency_task_ids
        == ["project-beta-implement-scoped-slice"]
    )


def test_repository_materialize_template_rejects_duplicate_ids_without_writing(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    with pytest.raises(ValueError, match="materialization ids contains duplicate ids"):
        repo.materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(duplicate_reviewer_id="implementer-auto-dev-squad"),
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 0
    assert state.teams == {}
    assert state.projects == {}
    assert state.agents == {}
    assert state.review_profiles == {}
    assert state.tasks == {}


def test_repository_materialize_template_rejects_stale_revision_without_writing(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="existing-team",
        name="Existing Team",
        manager_agent_id="existing-manager",
        manager_session_id="existing-session",
        expected_revision=0,
    )

    with pytest.raises(TeamStateConflictError):
        repo.materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(),
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 1
    assert state.projects == {}


def test_repository_materialize_template_rejects_malformed_git_config(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    with pytest.raises(ValidationError, match="exactly one of bare_repo_name or bare_repo_path must be set"):
        repo.materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(bad_git=True),
            expected_revision=0,
        )

    assert repo.read().revision == 0


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


def test_apply_agent_action_task_creation_batch_allows_sibling_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "task_id": "task-scaffold",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Scaffold",
                    "description": "Create the project scaffold.",
                },
                {
                    "task_id": "task-netcode",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Netcode",
                    "description": "Build the authoritative loop.",
                    "dependency_task_ids": ["task-scaffold"],
                },
                {
                    "task_id": "task-review",
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 3,
                    "title": "Review",
                    "description": "Review the completed slice.",
                    "dependency_task_ids": ["task-netcode"],
                },
            ],
        },
    )

    assert result["success"] is True
    assert result["errors"] == []
    assert result["created_task_ids"] == [
        "task-scaffold",
        "task-netcode",
        "task-review",
    ]
    assert state.tasks["task-netcode"].dependency_task_ids == ["task-scaffold"]
    assert state.tasks["task-review"].dependency_task_ids == ["task-netcode"]


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


def test_apply_agent_action_item_errors_do_not_abort_valid_task_creation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "task_updates": [{"state": "finished"}],
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
    assert result["applied"] is True
    assert result["created_task_ids"] == ["task-lead"]
    assert result["updated_task_ids"] == []
    assert result["errors"] == [
        "task_updates[0]: task_id must be a non-empty string"
    ]
    assert state.tasks["task-lead"].owner_agent_id == "lead"
    assert state.agent_runtimes["lead"].no_progress_count == 0
    action_record = next(iter(state.action_records.values()))
    assert action_record.success is False
    assert action_record.error == "task_updates[0]: task_id must be a non-empty string"


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


def test_apply_agent_action_rejects_cross_project_self_task_creation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=4,
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project-2",
                    "priority": 1,
                    "title": "Cross project self",
                    "description": "Backend cannot assign itself into project-2.",
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


def test_operator_task_creation_resets_no_progress_and_wakes_owner(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _throttle_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            no_progress_count=10,
        )
        return state

    repo.update(_throttle_manager)

    state = repo.create_or_reprioritize_task(
        task_id="manager-delegate-prototype",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Delegate prototype",
        description="Create a project-lead task for the prototype.",
        created_by_agent_id="manager",
    )

    runtime = state.agent_runtimes["manager"]
    assert runtime.no_progress_count == 0
    assert runtime.next_wake_at is None

    _state, leases = repo.lease_due_agents(max_no_progress_count=3)

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]


def test_create_operator_directive_wakes_manager_inbox(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _throttle_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            no_progress_count=10,
        )
        return state

    repo.update(_throttle_manager)
    repo.create_or_reprioritize_task(
        task_id="status-sweep",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Status sweep",
        description="Ask implementers for status.",
        created_by_agent_id="manager",
    )

    state, item = repo.create_operator_directive(
        team_id="team",
        message="Ask the lead for a status report on next Team dispatch.",
        title="Status report request",
        project_id="project",
        task_id="status-sweep",
        source="discord",
        source_message_id="discord-msg-1",
        created_by="operator:jonathan",
        requesting_session_id="session-manager",
    )

    assert item.item_type == TeamInboxItemType.OPERATOR_DIRECTIVE
    assert item.recipient_agent_id == "manager"
    assert item.task_id == "status-sweep"
    assert item.payload == {
        "title": "Status report request",
        "message": "Ask the lead for a status report on next Team dispatch.",
        "team_id": "team",
        "project_id": "project",
        "source": "discord",
        "source_message_id": "discord-msg-1",
        "created_by": "operator:jonathan",
        "requesting_session_id": "session-manager",
    }
    runtime = state.agent_runtimes["manager"]
    assert runtime.no_progress_count == 0
    assert runtime.next_wake_at is None

    _state, leases = repo.lease_due_agents(max_no_progress_count=3)

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]
    assert leases[0]["reason"] == "inbox"
    assert leases[0]["inbox"][0]["id"] == item.id


def test_create_operator_directive_rejects_non_manager_session(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValueError, match="team manager session"):
        repo.create_operator_directive(
            team_id="team",
            message="Ask for status.",
            requesting_session_id="session-lead",
        )


def test_operator_task_creation_can_use_operator_actor(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = repo.create_or_reprioritize_task(
        task_id="manager-delegate-prototype",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Delegate prototype",
        description="Create a project-lead task for the prototype.",
        created_by_actor="operator:jonathan",
    )

    task = state.tasks["manager-delegate-prototype"]
    assert task.created_by_agent_id is None
    assert task.created_by_actor == "operator:jonathan"


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


def test_apply_agent_action_git_review_promotion_rejects_unrelated_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_completed_ephemeral(repo)
    _seed_task(repo, task_id="other-task")
    _seed_deliverable(
        repo,
        deliverable_id="other-deliverable",
        task_id="other-task",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [
                {
                    "assignment_id": "ephemeral-1",
                    "deliverable_id": "other-deliverable",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("does not belong" in error for error in result["errors"])
    assert result["git_promotion_event_ids"] == []
    assert state.git_promotions == {}


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


def test_apply_agent_action_records_deliverable_git_refs(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "git_refs": {
                        "agent_branch": "agents/session-backend/work",
                        "commit_sha": "deadbeef",
                    },
                }
            ],
        },
    )

    assert result["success"] is True
    refs = state.deliverables["deliverable"].git_refs
    assert refs.agent_branch == "agents/session-backend/work"
    assert refs.commit_sha == "deadbeef"


def test_apply_agent_action_persistent_git_review_promotion_from_commit(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "git_refs": {"commit_sha": "deadbeef"},
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is True
    promotion_id = result["git_promotion_event_ids"][0]
    promotion = state.git_promotions[promotion_id]
    assert promotion.promotion_kind == "review"
    assert promotion.deliverable_id == "deliverable"
    assert promotion.task_id == "task"
    assert promotion.source_branch == "commit/deadbeef"
    assert promotion.target_branch == "team/team/reviews/deliverable"
    assert promotion.commit_sha == "deadbeef"
    event = result["events"][0]
    assert event["payload"]["promotion_kind"] == "review"
    assert event["payload"]["deliverable_id"] == "deliverable"
    assert event["payload"]["target_branch"] == "team/team/reviews/deliverable"
    assert event["payload"]["commit_sha"] == "deadbeef"


def test_apply_agent_action_persistent_git_review_promotion_rejects_missing_source(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is False
    assert any("requires source_branch or commit_sha" in error for error in result["errors"])
    assert state.git_promotions == {}


def test_apply_agent_action_persistent_git_review_promotion_checks_authority(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "git_refs": {"commit_sha": "deadbeef"},
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is False
    assert any("not permitted to promote deliverable" in error for error in result["errors"])
    assert len(state.git_promotions) == 0


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
    assert release.artifact_refs == ["artifact-space:/teams/team/result"]
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


def test_apply_agent_action_creates_child_task_with_graph_fields(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")
    _seed_task(repo, task_id="dep", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child task",
                    "description": "Decomposed work.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["dep"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert "child" in result["created_task_ids"]
    child = state.tasks["child"]
    assert child.parent_task_id == "parent"
    assert child.dependency_task_ids == ["dep"]
    assert state.child_task_ids("parent") == ["child"]


def test_apply_agent_action_task_creation_rejects_missing_parent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child",
                    "description": "Refers to a missing parent.",
                    "parent_task_id": "ghost",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("missing parent" in error for error in result["errors"])
    assert "child" not in state.tasks


def test_apply_agent_action_task_creation_rejects_cross_project_dependency(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=4,
    )
    repo.create_or_reprioritize_task(
        task_id="other",
        owner_agent_id="lead-2",
        project_id="project-2",
        priority=1,
        title="Other project task",
        description="Lives in project-2.",
        created_by_agent_id="lead-2",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child",
                    "description": "Depends across projects.",
                    "dependency_task_ids": ["other"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("belongs to project" in error for error in result["errors"])
    assert "child" not in state.tasks


def test_apply_agent_action_task_creation_batch_rejects_sibling_cycle(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "a",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "A",
                    "description": "First task.",
                    "dependency_task_ids": ["b"],
                },
                {
                    "id": "b",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "B",
                    "description": "Second task.",
                    "dependency_task_ids": ["a"],
                },
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("dependency cycle" in error for error in result["errors"])
    assert "a" not in state.tasks
    assert "b" not in state.tasks


def test_apply_agent_action_task_creation_rejects_conflicting_task_id_alias(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "task_id": "canonical",
                    "id": "legacy",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "title": "Conflicting ids",
                    "description": "Both id forms disagree.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("conflicting task_id and id" in error for error in result["errors"])
    assert "canonical" not in state.tasks
    assert "legacy" not in state.tasks


def test_apply_agent_action_owner_edits_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="a", owner_agent_id="backend")
    _seed_task(repo, task_id="b", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "a", "dependency_task_ids": ["b"]}],
        },
    )

    assert result["success"] is True
    assert state.tasks["a"].dependency_task_ids == ["b"]
    assert "a" in result["updated_task_ids"]


def test_apply_agent_action_task_update_rejects_dependency_cycle(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="a", owner_agent_id="backend")
    _seed_task(repo, task_id="b", owner_agent_id="backend")
    repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "a", "dependency_task_ids": ["b"]}],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "b", "dependency_task_ids": ["a"]}],
        },
    )

    assert result["success"] is False
    assert any("cycle" in error for error in result["errors"])
    assert state.tasks["b"].dependency_task_ids == []
    assert state.tasks["a"].dependency_task_ids == ["b"]


def test_apply_agent_action_supervisor_edits_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="w1", owner_agent_id="worker")
    _seed_task(repo, task_id="w2", owner_agent_id="worker")

    # backend supervises worker but is neither project lead nor team manager.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "dependency_task_ids": ["w2"]}],
        },
    )

    assert result["success"] is True
    assert state.tasks["w1"].dependency_task_ids == ["w2"]


def test_apply_agent_action_lead_and_manager_edit_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="t1", owner_agent_id="backend")
    _seed_task(repo, task_id="t2", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "t1", "dependency_task_ids": ["t2"]}],
        },
    )
    assert result["success"] is True
    assert state.tasks["t1"].dependency_task_ids == ["t2"]

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "task_updates": [{"task_id": "t1", "dependency_task_ids": []}],
        },
    )
    assert result["success"] is True
    assert state.tasks["t1"].dependency_task_ids == []


def test_apply_agent_action_unrelated_agent_cannot_edit_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="b1", owner_agent_id="backend")
    _seed_task(repo, task_id="b2", owner_agent_id="backend")

    # worker reports to backend; it does not supervise backend and is not a lead/manager.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "task_updates": [{"task_id": "b1", "dependency_task_ids": ["b2"]}],
        },
    )

    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.tasks["b1"].dependency_task_ids == []


def test_apply_agent_action_non_owner_state_change_still_rejected(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="b1", owner_agent_id="backend")

    # lead supervises backend and leads the project, but state edits remain owner-only.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "b1", "state": "in_progress"}],
        },
    )

    assert result["success"] is False
    assert any("cannot update task" in error for error in result["errors"])
    assert state.tasks["b1"].state == TeamTaskState.PENDING


def _seed_review_profile(
    repo: TeamStateRepository,
    *,
    profile_id: str = "profile",
    project_id: str | None = None,
    applicable_roles: list[str] | None = None,
    criteria: list[ReviewProfileCriterion] | None = None,
) -> None:
    repo.create_review_profile(
        profile_id=profile_id,
        team_id="team",
        project_id=project_id,
        name="Backend review",
        description="Checklist for backend deliverables.",
        applicable_roles=applicable_roles or ["backend", "worker"],
        criteria=criteria
        or [
            ReviewProfileCriterion(id="tests", label="Tests pass", required=True),
            ReviewProfileCriterion(
                id="docs", label="Docs updated", description="README/CHANGELOG", required=False
            ),
        ],
    )


def test_create_update_archive_review_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_review_profile(repo)

    state = repo.read()
    profile = state.review_profiles["profile"]
    assert profile.team_id == "team"
    assert profile.name == "Backend review"
    assert [c.id for c in profile.criteria] == ["tests", "docs"]
    assert profile.applicable_roles == ["backend", "worker"]
    assert profile.archived is False

    state = repo.update_review_profile(
        profile_id="profile",
        name="Renamed review",
        criteria=[ReviewProfileCriterion(id="tests", label="Tests pass")],
        archived=True,
    )
    profile = state.review_profiles["profile"]
    assert profile.name == "Renamed review"
    assert [c.id for c in profile.criteria] == ["tests"]
    assert profile.archived is True


def test_create_review_profile_rejects_duplicate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_review_profile(repo)

    with pytest.raises(ValueError, match="already exists"):
        _seed_review_profile(repo)


def test_apply_agent_action_review_request_with_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                    "instructions": "Use the checklist.",
                }
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.review_profile_id == "profile"
    assert review.instructions == "Use the checklist."
    inbox_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.STATUS_REQUEST
        and item.recipient_agent_id == "worker"
    ]
    assert len(inbox_items) == 1
    payload = inbox_items[0].payload
    assert payload["instructions"] == "Use the checklist."
    assert payload["review_profile"]["id"] == "profile"
    assert payload["review_profile"]["name"] == "Backend review"
    criterion_ids = [c["id"] for c in payload["review_profile"]["criteria"]]
    assert criterion_ids == ["tests", "docs"]
    assert payload["review_profile"]["criteria"][0]["required"] is True


def test_apply_agent_action_review_request_archived_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    repo.update_review_profile(profile_id="profile", archived=True)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("archived" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_cross_project_profile_errors(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Other project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        goals=[ProjectGoal(id="goal-2", description="Other work")],
    )
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    # Profile is scoped to a different project than the deliverable's task ("project").
    _seed_review_profile(repo, project_id="project-2")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("does not apply to project" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_role_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo, applicable_roles=["backend"])

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("reviewer role worker" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_missing_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "missing",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("missing review profile" in error for error in result["errors"])
    assert state.review_requests == {}


def _request_review_with_profile(repo: TeamStateRepository) -> None:
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                    "instructions": "Use the checklist.",
                }
            ],
        },
    )


def test_apply_agent_action_review_decision_records_criteria(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "findings": ["Looks good."],
                    "criteria": [
                        {"criterion_id": "tests", "state": "passed", "finding": "All green."},
                        {"criterion_id": "docs", "state": "skipped"},
                    ],
                }
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.findings == ["Looks good."]
    assert {c.criterion_id: c.state for c in review.criteria} == {
        "tests": "passed",
        "docs": "skipped",
    }
    tests_result = next(c for c in review.criteria if c.criterion_id == "tests")
    assert tests_result.finding == "All green."
    # Criterion results never alter acceptance cascade.
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_review_decision_unknown_criterion_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "nope", "state": "passed"}],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("unknown criterion nope" in error for error in result["errors"])
    review = state.review_requests["review-1"]
    assert review.criteria == []
    assert review.state == TeamRecordState.PENDING


def test_apply_agent_action_review_decision_criteria_without_profile_errors(
    tmp_path: Path,
) -> None:
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
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("no review profile" in error for error in result["errors"])
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING


def test_apply_agent_action_review_decision_without_criteria_still_works(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.criteria == []


def test_record_review_decision_repository_stores_criteria(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    repo.record_review_request(
        review_request_id="review-1",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        review_profile_id="profile",
        instructions="Operator instructions.",
    )

    from truman_team_addon.models import ReviewCriterionResult

    state = repo.record_review_decision(
        review_request_id="review-1",
        state=TeamRecordState.ACCEPTED,
        findings=["ok"],
        criteria=[ReviewCriterionResult(criterion_id="tests", state="passed")],
    )

    review = state.review_requests["review-1"]
    assert review.review_profile_id == "profile"
    assert review.instructions == "Operator instructions."
    assert review.state == TeamRecordState.ACCEPTED
    assert [c.criterion_id for c in review.criteria] == ["tests"]
