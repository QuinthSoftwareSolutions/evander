from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander.issues.service import IssueService
from evander_team_addon.models import (
    AdoWorkItemAttribution,
    Deliverable,
    ProjectGitIdentity,
    TeamRecordState,
)
from evander_team_addon.provider_schemas import TeamCreateTaskEffectDefinition
from evander_team_addon.providers import TeamCreateTaskEffectProvider
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.ado_attribution import release_publication_context
from evander_team_addon.services.domain.task_authorship import combined_deliverable_attributions
from evander_team_addon.sqlite_store import TeamStateSqliteStore
from test_team_task_authorship import _create, ALICE, BOB
from test_team_addon_repository import (
    _seed_hierarchy,
    _team_admin_service,
    _lease_agent_dispatch,
    _command_service,
)


def attribution(owner="alice", work_item="123"):
    return [
        AdoWorkItemAttribution(
            organization="org",
            work_item_id=work_item,
            assignee_id=owner,
            display_name=owner.title(),
            email=f"{owner}@example.test",
        )
    ]


def test_host_intake_captures_assignee_once_and_preserves_project_fallback(tmp_path, monkeypatch):
    repo = _seed_hierarchy(tmp_path)
    current_owner = {
        "displayName": "Alice Example",
        "uniqueName": "alice@example.test",
        "id": "ALICE",
    }
    calls = []

    def get(self, **kwargs):
        calls.append(kwargs)
        return {"fields": {"System.AssignedTo": current_owner}}

    monkeypatch.setattr(IssueService, "get_remote_backlog_item_by_id", get)
    resolver = SimpleNamespace(
        resolve_for_directory=lambda _: SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org"
        )
    )
    provider = TeamCreateTaskEffectProvider()
    provider.initialize(
        TeamCreateTaskEffectDefinition(
            id="intake",
            display_name="ADO intake",
            type="effect:team:create-task",
            team_id="team",
            project_id="project",
            owner_agent_id="lead",
            priority=0,
            task_id="intake",
            title="Intake",
            description="Coordinate work",
            ado_work_item_id="123",
            ado_project="APP",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)}, provider_config_resolver=resolver
        ),
    )
    result = provider.execute()
    assert result["success"], result
    task = repo.read().tasks["intake"]
    assert task.authored_by == ProjectGitIdentity(**ALICE)
    assert task.work_item_attributions[0].assignee_id == "alice"
    current_owner = {"displayName": "Bob", "uniqueName": "bob@example.test", "id": "bob"}
    assert provider.execute()["success"]
    assert len(calls) == 1
    assert repo.read().tasks["intake"].work_item_attributions == task.work_item_attributions
    assert repo.read().tasks["intake"].authored_by == task.authored_by
    current_owner = {"displayName": "Owner without email", "id": "other"}
    result = provider.execute(resolved_config={"task_id": "no-email"})
    assert result["success"], result
    assert repo.read().tasks["no-email"].authored_by is None
    assert repo.read().tasks["no-email"].work_item_attributions[0].assignee_id == "other"


def test_attribution_persists_and_batch_inherits_without_using_editable_author(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _create(admin, "intake", authored_by=ALICE, work_item_attributions=attribution())
    _create(admin, "child", parent_task_id="intake")
    _create(admin, "correction", originating_task_id="child", authored_by=BOB)
    _create(admin, "legacy")
    _lease_agent_dispatch(repo, agent_id="lead")
    commands = _command_service(repo)
    tasks = [
        dict(task_id="grandchild", parent_task_id="followup"),
        dict(task_id="followup", originating_task_id="intake", authored_by=BOB),
    ]
    for task in tasks:
        task.update(
            project_id="project", owner_agent_id="backend", title="Work", description="Work"
        )
    state, result = commands.create_tasks_command(
        session_id="session-lead", request_id="batch", tasks=tasks
    )
    assert result.get("success", True), result
    state = TeamStateRepository(tmp_path).read()
    for task_id in ("intake", "child", "correction", "followup", "grandchild"):
        assert state.tasks[task_id].work_item_attributions == attribution()
    assert state.tasks["correction"].authored_by == ProjectGitIdentity(**BOB)
    assert state.tasks["legacy"].work_item_attributions is None
    _create(admin, "intake", work_item_attributions=attribution("bob"))
    assert repo.read().tasks["intake"].work_item_attributions == attribution()


def test_nullable_migration_does_not_backfill_existing_tasks(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    _create(_team_admin_service(repo), "legacy", authored_by=ALICE)
    before = repo.read()
    with sqlite3.connect(TeamStateSqliteStore(tmp_path).path) as db:
        db.execute("ALTER TABLE team_tasks DROP COLUMN work_item_attributions_json")
        db.execute("UPDATE team_state_metadata SET value='73' WHERE key='sqlite_schema_version'")
    migrated = TeamStateRepository(tmp_path).read()
    assert migrated.revision == before.revision
    assert migrated.tasks == before.tasks


@pytest.mark.parametrize(
    "second_owner,expected_reason",
    [
        ("alice", None),
        ("bob", "mixed_work_item_assignees"),
        (None, "missing_work_item_attribution"),
    ],
)
def test_release_creator_includes_nested_contributions(tmp_path, second_owner, expected_reason):
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _create(admin, "first", work_item_attributions=attribution())
    _create(
        admin,
        "second",
        work_item_attributions=attribution(second_owner, "456") if second_owner else None,
    )
    state = repo.read()
    state.deliverables["first"] = Deliverable(
        id="first",
        task_id="first",
        producer_agent_id="backend",
        summary="Wrapper",
        source_deliverable_ids=["second"],
    )
    state.deliverables["second"] = Deliverable(
        id="second", task_id="second", producer_agent_id="backend", summary="Contribution"
    )
    combined = combined_deliverable_attributions(state, ["first"])
    assert bool(combined) == (second_owner is not None)
    release = SimpleNamespace(
        project_id="project",
        id="release",
        state=TeamRecordState.ACCEPTED,
        published_commit_sha="1" * 40,
        manifest=None,
        deliverable_ids=["first"],
    )
    state.release_packages["release"] = release
    state.projects["project"].git = SimpleNamespace(managed_repo_id="repo", directory_path="root")
    captured = {}

    def issue(**kwargs):
        captured.update(kwargs)
        return "context"

    manager = SimpleNamespace(
        get_repository=lambda *a, **k: {
            "provider": "azure_devops",
            "canonical_repository_url": "url",
        },
        _azure_repository_parts=lambda _: ("org", "project", "repo"),
        ado_user_connections=SimpleNamespace(issue_publication_context=issue),
    )
    assert (
        release_publication_context(state, release_package_id="release", managed_git_admin=manager)
        == "context"
    )
    assert captured["fallback_reason"] == expected_reason
    assert captured["identity_id"] == ("alice" if expected_reason is None else None)
    assert captured["commit_sha"] == "1" * 40
