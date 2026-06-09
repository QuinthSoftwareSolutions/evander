from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from truman_team_addon.models import ProjectGoal
from truman_team_addon.repository import TeamStateConflictError, TeamStateRepository


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
