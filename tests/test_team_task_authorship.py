from __future__ import annotations

from pathlib import Path

import pytest

from evander_team_addon.models import AgentTask, ProjectGitIdentity
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.domain.task_authorship import common_task_author
from team_repository_support import (
    _command_service,
    _lease_agent_dispatch,
    _seed_hierarchy,
    _team_admin_service,
)


ALICE = {"name": "Alice Example", "email": "alice@example.test"}
BOB = {"name": "Bob Example", "email": "bob@example.test"}


def _create(admin, task_id, **kwargs):
    return admin.create_or_reprioritize_task(
        task_id=task_id,
        project_id="project",
        owner_agent_id="backend",
        priority=1,
        title=task_id,
        description="Delegated work",
        created_by_agent_id="lead",
        **kwargs,
    )


def test_task_authors_persist_inherit_and_can_be_cleared(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _create(admin, "alice", authored_by=ALICE)
    _create(admin, "bob", authored_by=BOB)
    _create(admin, "child", parent_task_id="alice")
    _create(admin, "followup", originating_task_id="alice", parent_task_id="bob")
    _create(admin, "override", originating_task_id="alice", authored_by=BOB)
    _create(admin, "default")
    _create(admin, "default-followup", originating_task_id="default", parent_task_id="bob")

    state = TeamStateRepository(tmp_path).read()
    for task_id in ("alice", "child", "followup"):
        assert state.tasks[task_id].authored_by == ProjectGitIdentity(**ALICE)
    assert state.tasks["override"].authored_by == ProjectGitIdentity(**BOB)
    assert state.tasks["default"].authored_by is None
    assert state.tasks["default-followup"].authored_by is None

    admin.update_task(task_id="alice", authored_by=BOB, set_authored_by=True)
    admin.update_task(task_id="bob", authored_by=None, set_authored_by=True)
    state = repo.read()
    assert state.tasks["alice"].authored_by == ProjectGitIdentity(**BOB)
    assert state.tasks["bob"].authored_by is None
    assert state.tasks["child"].authored_by == ProjectGitIdentity(**ALICE)
    # Omission on an upsert preserves the task's identity.
    _create(admin, "alice")
    assert repo.read().tasks["alice"].authored_by == ProjectGitIdentity(**BOB)


def test_task_author_batch_inherits_parent_created_later_in_input(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="lead")
    commands = _command_service(repo)
    tasks = [
        {"task_id": "grandchild", "parent_task_id": "child"},
        {"task_id": "child", "parent_task_id": "parent"},
        {"task_id": "parent", "authored_by": ALICE},
        {"task_id": "override", "parent_task_id": "parent", "authored_by": BOB},
    ]
    for task in tasks:
        task.update(
            owner_agent_id="backend",
            project_id="project",
            title=task["task_id"],
            description="Work",
        )
    state, result = commands.create_tasks_command(
        session_id="session-lead",
        request_id="author-batch",
        tasks=tasks,
    )
    assert result.get("success", True), result
    for task_id in ("grandchild", "child", "parent"):
        assert state.tasks[task_id].authored_by == ProjectGitIdentity(**ALICE)
    assert state.tasks["override"].authored_by == ProjectGitIdentity(**BOB)
    replay, _ = commands.create_tasks_command(
        session_id="session-lead",
        request_id="author-batch",
        tasks=tasks,
    )
    assert replay.tasks["parent"].authored_by == ProjectGitIdentity(**ALICE)


def test_task_author_command_create_update_and_owner_authorization(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="lead")
    commands = _command_service(repo)
    state, result = commands.create_task_command(
        session_id="session-lead",
        request_id="alice-task",
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        title="Work",
        description="Work",
        authored_by=ALICE,
    )
    assert state.tasks["task"].authored_by == ProjectGitIdentity(**ALICE), result

    def _scope(state):
        state.dispatches["dispatch-command"].task_ids = ["task"]
        return state

    repo.update(_scope)
    state, result = commands.update_task_command(
        session_id="session-lead",
        request_id="not-owner",
        task_id="task",
        authored_by=BOB,
    )
    assert state.tasks["task"].authored_by == ProjectGitIdentity(**ALICE)
    assert result["applied"] is False
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="backend-author")
    state, result = commands.update_task_command(
        session_id="session-backend",
        request_id="owner-update",
        task_id="task",
        authored_by=BOB,
    )
    assert state.tasks["task"].authored_by == ProjectGitIdentity(**BOB), result
    state, result = commands.update_task_command(
        session_id="session-backend",
        request_id="owner-clear",
        task_id="task",
        authored_by=None,
    )
    assert state.tasks["task"].authored_by is None, result


@pytest.mark.parametrize(
    "authored_by",
    [
        {"name": "Alice"},
        {"name": "Alice", "email": "invalid"},
        {"name": "Alice\nBob", "email": "alice@example.test"},
        "Alice",
    ],
)
def test_invalid_task_author_cannot_be_persisted(tmp_path: Path, authored_by: object) -> None:
    repo = _seed_hierarchy(tmp_path)
    revision = repo.read().revision
    with pytest.raises(ValueError):
        _create(_team_admin_service(repo), "invalid", authored_by=authored_by)
    assert repo.read().revision == revision
    assert "invalid" not in repo.read().tasks


def test_legacy_task_uses_project_fallback() -> None:
    task = AgentTask(
        id="legacy",
        owner_agent_id="owner",
        project_id="project",
        priority=0,
        title="Legacy",
        description="Legacy",
        created_by_actor="operator",
    )
    assert task.authored_by is None


def test_aggregate_author_does_not_choose_one_of_multiple_users(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _create(admin, "alice", authored_by=ALICE)
    _create(admin, "alice-followup", originating_task_id="alice")
    _create(admin, "bob", authored_by=BOB)
    _create(admin, "default")
    state = repo.read()
    assert common_task_author(state, ["alice", "alice-followup"]) == ProjectGitIdentity(**ALICE)
    for ids in (["alice", "bob"], ["alice", "default"], ["alice", "missing"], []):
        assert common_task_author(state, ids) is None


def test_existing_database_adds_nullable_author_without_changing_tasks(tmp_path: Path) -> None:
    import sqlite3
    from evander_team_addon.sqlite_store import TeamStateSqliteStore

    repo = _seed_hierarchy(tmp_path)
    _create(_team_admin_service(repo), "legacy-task")
    previous = repo.read()
    store = TeamStateSqliteStore(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("ALTER TABLE team_tasks DROP COLUMN authored_by_json")
        connection.execute(
            "UPDATE team_state_metadata SET value = '72' WHERE key = 'sqlite_schema_version'"
        )
    migrated = TeamStateRepository(tmp_path).read()
    assert migrated.tasks == previous.tasks
    assert migrated.revision == previous.revision
    _team_admin_service(TeamStateRepository(tmp_path)).update_task(
        task_id="legacy-task",
        authored_by=ALICE,
        set_authored_by=True,
    )
    assert TeamStateRepository(tmp_path).read().tasks[
        "legacy-task"
    ].authored_by == ProjectGitIdentity(**ALICE)
