from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evander.effects.providers.base import EffectRuntimeContext
from evander.events.models import WorkflowNodeEnvelope
from evander_team_addon.models import AgentStatus, Deliverable, TeamTaskState
from evander_team_addon.provider_schemas import TeamCreateTaskForRoleEffectDefinition
from evander_team_addon.providers import register_providers
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.role_task_queue import ROLE_QUEUE_WAKE_EVENT
from evander_team_addon.services.runtime_service import TeamRuntimeService
from team_repository_support import _register_backend_peer, _seed_hierarchy, _seed_task


NODE_TYPE = "effect:team:create-task-for-role"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = _seed_hierarchy(tmp_path)
    _register_backend_peer(repo, reports_to_agent_id="lead")
    # Suppress external notification; durable outbox state is the assertion boundary.
    monkeypatch.setattr(
        "evander_team_addon.provider_role_tasks.request_team_outbox_publication_for_records",
        lambda *args, **kwargs: {"wake_notified": False, "error": "notification unavailable"},
    )
    return repo


def run_node(repo, *, scope="root", managed_git=None, **overrides):
    config = {
        "team_id": "team",
        "project_id": "project",
        "actor_agent_id": "lead",
        "role_id": "project__backend",
        "task_id": "workflow-task",
        "title": "Assess integrated code",
        "description": "Preserve behavior and verify changes.",
        **overrides,
    }
    registration = register_providers()
    provider = registration.effect_providers[NODE_TYPE]()
    provider.initialize(
        WorkflowNodeEnvelope(
            id="role-task", display_name="Queue role task", type=NODE_TYPE, config=config
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(repo.runtime_dir)},
            metadata={"directory_path": scope},
            admin_service=SimpleNamespace(managed_git_admin=managed_git),
        ),
    )
    return provider.execute()


def test_workflow_queues_without_dispatch_and_assigns_after_dependencies_resolve(repo):
    _seed_task(repo, task_id="source")
    assert not repo.read().dispatches
    result = run_node(repo, originating_task_id="source", dependency_task_ids=["source"])
    assert result["success"], result
    assert result["created"] and not result["reused"]
    assert result["assignment_mode"] == "role"
    assert result["assignment_role_id"] == "project__backend"
    assert result["owner_agent_id"] is None
    state = TeamStateRepository(repo.runtime_dir).read()
    assert state.tasks["workflow-task"].work_lineage_id == state.tasks["source"].work_lineage_id
    assert state.tasks["workflow-task"].created_by_agent_id == "lead"
    assert result["outbox_publication"]["error"] == "notification unavailable"
    wakes = repo.list_outbox_records(intent_type=ROLE_QUEUE_WAKE_EVENT)
    assert set(result["outbox_record_ids"]) <= {r.id for r in wakes}
    _, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend-peer",
        project_id="project",
        defer_activation=True,
    )
    assert not leases
    repo.update(lambda s: _finish(s, "source"))
    state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend-peer",
        project_id="project",
        defer_activation=True,
    )
    assert len(leases) == 1
    assert state.tasks["workflow-task"].owner_agent_id == "backend-peer"


def _finish(state, task_id):
    state.tasks[task_id].state = TeamTaskState.FINISHED
    return state


@pytest.mark.parametrize("terminal", ["finished", "cancelled"])
def test_replay_preserves_completed_or_reassigned_task_and_original_wake(repo, terminal):
    revision = repo.read().revision
    first = run_node(repo, expected_revision=revision)
    assert first["success"], first

    def changed(state):
        task = state.tasks["workflow-task"]
        task.owner_agent_id = "backend-peer"
        task.assignment_mode = "agent"
        task.assignment_role_id = None
        if terminal == "finished":
            task.state = TeamTaskState.FINISHED
            task.deliverable_ids = ["accepted-result"]
            state.deliverables["accepted-result"] = Deliverable(
                id="accepted-result",
                task_id=task.id,
                producer_agent_id="backend-peer",
                state="accepted",
                summary="Completed assessment",
            )
        task.description = "Reviewed result retained"
        return state

    repo.update(changed)
    if terminal == "cancelled":
        TeamAdminService(repo).cancel_task(
            task_id="workflow-task",
            reason="Work withdrawn",
            actor_id="operator",
            reason_code="scope_withdrawn",
        )
    before = repo.read().model_dump(mode="json")
    outbox_before = [r.model_dump(mode="json") for r in repo.list_outbox_records()]
    replay = run_node(
        repo,
        expected_revision=revision,
        title="Later event",
        description="Do not overwrite",
        dependency_task_ids=["different-input"],
        priority=99,
    )
    assert replay["success"] and replay["reused"] and not replay["created"], replay
    assert replay["owner_agent_id"] == "backend-peer"
    assert replay["task"]["state"] == terminal
    assert replay["outbox_record_ids"] == first["outbox_record_ids"]
    assert repo.read().model_dump(mode="json") == before
    assert [r.model_dump(mode="json") for r in repo.list_outbox_records()] == outbox_before


def test_concurrent_workflow_events_create_one_task_and_one_durable_wake(repo):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run_node(repo), range(4)))
    assert all(r["success"] for r in results), results
    assert sum(r["created"] for r in results) == 1
    assert len({tuple(r["outbox_record_ids"]) for r in results}) == 1
    assert list(repo.read().tasks) == ["workflow-task"]


@pytest.mark.parametrize(
    "override",
    [
        {"role_id": "project__worker"},
        {"actor_agent_id": "backend"},
        {"case_id": "different-case"},
        {"originating_task_id": "different-origin"},
        {"parent_task_id": "different-parent"},
    ],
)
def test_replay_rejects_changed_identity_without_mutation(repo, override):
    assert run_node(repo)["success"]
    before = repo.read().model_dump(mode="json")
    result = run_node(repo, **override)
    assert not result["success"]
    assert "different role-task identity" in result["error"]
    assert repo.read().model_dump(mode="json") == before


@pytest.mark.parametrize(
    "override",
    [
        {"team_id": "another-team"},
        {"project_id": "missing-project"},
        {"actor_agent_id": "missing-actor"},
        {"actor_agent_id": "worker"},
        {"role_id": "missing-role"},
        {"role_id": "project__worker", "actor_agent_id": "backend-peer"},
        {"dependency_task_ids": ["missing-task"]},
        {"expected_revision": -1},
        {"scope": "root/child"},
    ],
)
def test_invalid_scope_delegation_graph_or_revision_cannot_create_work(repo, override):
    before = repo.read().model_dump(mode="json")
    result = run_node(repo, **override)
    assert not result["success"], result
    assert repo.read().model_dump(mode="json") == before


def test_existing_unrelated_task_id_is_not_adopted(repo):
    _seed_task(repo, task_id="workflow-task")
    before = repo.read().model_dump(mode="json")
    result = run_node(repo)
    assert not result["success"]
    assert "without this workflow" in result["error"]
    assert repo.read().model_dump(mode="json") == before


def test_unavailable_members_leave_work_queued_then_dispatch_when_available(repo):
    def pause(state):
        for agent_id in ("backend", "backend-peer"):
            state.agents[agent_id].status = AgentStatus.PAUSED
        return state

    repo.update(pause)
    result = run_node(repo)
    assert result["success"], result
    state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        project_id="project",
        defer_activation=True,
    )
    assert not leases and state.tasks["workflow-task"].owner_agent_id is None
    TeamAdminService(repo).update_agent(agent_id="backend", status=AgentStatus.ACTIVE)
    state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        project_id="project",
        defer_activation=True,
    )
    assert leases and state.tasks["workflow-task"].owner_agent_id == "backend"


def test_archived_role_fails_without_task_or_receipt(repo):
    TeamAdminService(repo).update_role(role_id="project__backend", archived=True)
    before = repo.read().model_dump(mode="json")
    result = run_node(repo)
    assert not result["success"] and result["error_code"] == "role_archived"
    assert repo.read().model_dump(mode="json") == before


def test_role_without_authorized_members_fails_without_task(repo):
    TeamAdminService(repo).create_role(
        role_id="empty-role",
        team_id="team",
        project_id="project",
        name="Empty role",
    )
    result = run_node(repo, role_id="empty-role")
    assert not result["success"]
    assert result["error_code"] == "role_assignment_no_eligible_agent"
    assert not repo.read().tasks


def test_failed_receipt_commit_rolls_back_task_and_wake_then_retry_succeeds(repo, monkeypatch):
    from evander_team_addon.sqlite_store import TeamStateSqliteCommandTransaction

    before = repo.read().model_dump(mode="json")
    wakes_before = [r.id for r in repo.list_outbox_records()]

    def fail_receipt(*args, **kwargs):
        raise RuntimeError("receipt storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(TeamStateSqliteCommandTransaction, "store_idempotency_record", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt storage unavailable"):
            run_node(repo)
    assert repo.read().model_dump(mode="json") == before
    assert [r.id for r in repo.list_outbox_records()] == wakes_before
    result = run_node(repo)
    assert result["success"] and result["created"], result
    assert result["outbox_record_ids"]


def test_integration_task_inherits_context_and_snapshots_git_base_only_once(repo):
    from evander_team_addon.models import ProjectGitConfig

    admin = TeamAdminService(repo)
    admin.create_case(case_id="case", team_id="team", project_id="project", title="Code change")
    admin.create_or_reprioritize_task(
        task_id="source",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Source",
        description="Original contribution",
        case_id="case",
        created_by_agent_id="lead",
        authored_by={"name": "Example Author", "email": "author@example.test"},
    )

    def configure_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
            directory_path="root/engineering",
        )
        state.projects["project"].directory_path = "root/engineering"
        return state

    repo.update(configure_git)
    resolutions = []

    class ManagedGit:
        def resolve_branch_commit(self, *args, **kwargs):
            resolutions.append(kwargs)
            return "b" * 40

    fields = {
        "originating_task_id": "source",
        "dependency_task_ids": ["source"],
        "integration_boundary": True,
        "deliverable_contract": {
            "required_contribution_task_ids": ["source"],
            "require_git_provenance": True,
        },
    }
    result = run_node(repo, scope="root/engineering", managed_git=ManagedGit(), **fields)
    assert result["success"], result
    task = repo.read().tasks["workflow-task"]
    assert task.parent_task_id is None and task.integration_boundary
    assert task.case_id == "case"
    assert task.authored_by.email == "author@example.test"
    assert task.git_base_commit_sha == "b" * 40
    assert task.deliverable_contract.required_contribution_task_ids == ["source"]
    assert task.work_lineage_id == repo.read().tasks["source"].work_lineage_id
    assert len(resolutions) == 1
    # Replays can succeed with Git temporarily unavailable and must not repin the base.
    assert run_node(repo, scope="root/engineering", **fields)["reused"]
    assert repo.read().tasks["workflow-task"].git_base_commit_sha == "b" * 40
    assert not run_node(repo, scope="root/another", **fields)["success"]


def test_schema_requires_stable_identity_and_preserves_role_queue_defaults():
    fields = {
        "id": "queue-task",
        "type": NODE_TYPE,
        "team_id": "team",
        "project_id": "project",
        "display_name": "Queue role task",
        "actor_agent_id": "lead",
        "role_id": "developer",
        "task_id": "stable",
        "title": "Work",
        "description": "Do the work",
    }
    definition = TeamCreateTaskForRoleEffectDefinition(**fields)
    assert definition.priority == 0
    assert definition.task_kind == "support"
    assert definition.integration_boundary is False
    for required in ("actor_agent_id", "role_id", "task_id"):
        with pytest.raises(ValidationError):
            TeamCreateTaskForRoleEffectDefinition(
                **{k: v for k, v in fields.items() if k != required}
            )
    with pytest.raises(ValidationError):
        TeamCreateTaskForRoleEffectDefinition(**fields, dependency_task_ids={"task": "source"})
