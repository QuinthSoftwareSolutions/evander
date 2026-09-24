from pathlib import Path
from types import SimpleNamespace

import pytest

from evander_team_addon.mcp import _team_mcp_git_candidate_commit_resolver
from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DependencyCompositionManifest,
    GitWorkContext,
    GitWorkContextState,
    ProjectGitConfig,
    TaskDeliverableContract,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.provider_git_sources import _project_repo_task_dependency_git_sources
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.commands import TeamTaskCreationCommandService
from evander_team_addon.services.domain.git_work_contexts import (
    GitWorkContextValidationError,
    bind_deliverable_git_refs_to_work_context,
    dependency_source_base_requirements,
)
from evander_team_addon.services.domain.preparation_failures import task_git_work_context_generation
from test_managed_git_service import _bare_path, _commit, _run, _service, _source_repository
from test_team_addon_repository import _lease_agent_dispatch, _seed_hierarchy


def _history(tmp_path: Path, *, clean_source: bool = False):
    source, _upstream, pinned = _source_repository(tmp_path)
    newer = _commit(source, "unrelated.txt", "unrelated work\n", "Advance main")
    _run("git", "push", "origin", "main", cwd=source)
    managed = _service(tmp_path)
    record = managed.register_repository("root", str(source))
    branch = "agents/session-backend/contribution"
    _run("git", "switch", "-c", branch, pinned if clean_source else newer, cwd=source)
    candidate = _commit(source, "contribution.txt", "requested work\n", "Contribution")
    _run("git", "push", str(_bare_path(managed, record["managed_repo_id"])), branch, cwd=source)
    return managed, record["managed_repo_id"], pinned, newer, branch, candidate


def _state(tmp_path: Path, *, clean_source: bool = False):
    managed, repo_id, pinned, newer, branch, candidate = _history(
        tmp_path, clean_source=clean_source
    )
    repo = _seed_hierarchy(tmp_path / "team")
    state = repo.read()
    state.projects["project"].git = ProjectGitConfig(managed_repo_id=repo_id, base_branch="main")
    state.tasks["integration"] = AgentTask(
        id="integration",
        project_id="project",
        owner_agent_id="backend",
        priority=1,
        created_by_agent_id="lead",
        title="Integrate requested work",
        description="Preserve the frozen baseline.",
        git_base_branch="main",
        git_base_commit_sha=pinned,
        integration_boundary=True,
        deliverable_contract=TaskDeliverableContract(
            require_git_provenance=True,
            required_contribution_task_ids=["contribution"],
        ),
    )
    state.tasks["contribution"] = AgentTask(
        id="contribution",
        project_id="project",
        owner_agent_id="backend",
        priority=1,
        created_by_agent_id="lead",
        parent_task_id="integration",
        git_base_branch="main",
        title="Requested change",
        description="Legacy child without a commit pin.",
        state=TeamTaskState.FINISHED,
        deliverable_ids=["accepted"],
    )
    state.git_work_contexts["source-context"] = GitWorkContext(
        id="source-context",
        agent_id="backend",
        task_id="contribution",
        base_branch="main",
        base_commit=pinned if clean_source else newer,
        agent_branch=branch,
        commit_sha=candidate,
        state=GitWorkContextState.SUBMITTED,
    )
    state.deliverables["accepted"] = Deliverable(
        id="accepted",
        task_id="contribution",
        producer_agent_id="backend",
        summary="Reviewed requested change",
        state=TeamRecordState.ACCEPTED,
        git_refs=DeliverableGitRefs(
            branch_context_id="source-context",
            agent_branch=branch,
            commit_sha=candidate,
            submitted_commit_sha=candidate,
        ),
    )
    return state, managed, repo_id, pinned, newer, branch, candidate


@pytest.mark.parametrize("surface", ["admin", "command"])
@pytest.mark.parametrize("relationship", ["child", "other_branch", "origin_only"])
def test_new_child_inherits_parent_pin_after_main_advances(
    tmp_path: Path, surface: str, relationship: str
):
    managed, repo_id, pinned, newer, _branch, _candidate = _history(tmp_path)
    repo = _seed_hierarchy(tmp_path / "team")

    def seed(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id=repo_id, base_branch="main"
        )
        state.tasks["parent"] = AgentTask(
            id="parent",
            project_id="project",
            owner_agent_id="backend",
            priority=1,
            created_by_agent_id="lead",
            title="Frozen parent",
            description="Main was pinned before its advance.",
            git_base_branch="main",
            git_base_commit_sha=pinned,
            integration_boundary=True,
            deliverable_contract=TaskDeliverableContract(require_git_provenance=True),
        )
        return state

    repo.update(seed)
    assert managed.resolve_branch_commit(repo_id, directory_path="root", branch="main") == newer
    payload = dict(
        task_id="child",
        project_id="project",
        owner_agent_id="backend",
        parent_task_id="parent",
        title="Child",
        description="Use the parent pin even though main advanced.",
    )
    if relationship == "other_branch":
        payload["git_base_branch"] = "feature/other-workstream"
    elif relationship == "origin_only":
        payload.pop("parent_task_id")
        payload["originating_task_id"] = "parent"
    if surface == "admin":
        state = TeamAdminService(repo, managed_git_admin=managed).create_or_reprioritize_task(
            **payload,
            priority=1,
            created_by_agent_id="lead",
        )
    else:
        _lease_agent_dispatch(repo, agent_id="lead")
        state, result = TeamTaskCreationCommandService(repo).create_task_command(
            **payload,
            session_id="session-lead",
            request_id="create-child",
        )
        assert result["success"], result["errors"]
    assert state.tasks["child"].git_base_commit_sha == (pinned if relationship == "child" else None)


def test_unordered_batch_descendants_inherit_one_automatic_pin(tmp_path: Path):
    repo = _seed_hierarchy(tmp_path)

    def seed(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        return state

    repo.update(seed)
    _lease_agent_dispatch(repo, agent_id="lead")
    calls = []

    def resolve(**kwargs):
        calls.append(kwargs)
        return ("a" if len(calls) == 1 else "b") * 40

    state, result = TeamTaskCreationCommandService(
        repo,
        git_base_commit_resolver=resolve,
    ).create_tasks_command(
        session_id="session-lead",
        request_id="batch",
        tasks=[
            dict(
                task_id=id,
                owner_agent_id="backend",
                project_id="project",
                title=id,
                description="Pinned workstream",
                **extra,
            )
            for id, extra in [
                ("grandchild", {"parent_task_id": "child"}),
                (
                    "child",
                    {
                        "parent_task_id": "parent",
                        "integration_boundary": True,
                        "deliverable_contract": {"require_git_provenance": True},
                    },
                ),
                (
                    "parent",
                    {
                        "integration_boundary": True,
                        "deliverable_contract": {"require_git_provenance": True},
                    },
                ),
            ]
        ],
    )
    assert result["success"], result["errors"]
    assert len(calls) == 1
    assert {state.tasks[id].git_base_commit_sha for id in ["parent", "child", "grandchild"]} == {
        "a" * 40
    }


def test_preparation_rejects_accepted_source_based_on_newer_main(tmp_path: Path):
    state, managed, repo_id, pinned, newer, _branch, _candidate = _state(tmp_path)
    with pytest.raises(RuntimeError, match="outside pinned integration base") as error:
        _project_repo_task_dependency_git_sources(
            SimpleNamespace(admin_service=SimpleNamespace(managed_git_admin=managed)),
            state=state,
            task_id="integration",
            managed_repo_id=repo_id,
        )
    assert pinned in str(error.value)
    assert newer in str(error.value)


@pytest.mark.parametrize("advance_target", [False, True])
def test_source_on_same_or_older_base_can_integrate(tmp_path: Path, advance_target: bool):
    state, managed, repo_id, pinned, newer, _branch, candidate = _state(tmp_path, clean_source=True)
    state.tasks["integration"].git_base_commit_sha = newer if advance_target else pinned
    sources = _project_repo_task_dependency_git_sources(
        SimpleNamespace(admin_service=SimpleNamespace(managed_git_admin=managed)),
        state=state,
        task_id="integration",
        managed_repo_id=repo_id,
    )
    assert sources[0]["commit_sha"] == candidate
    assert (
        dependency_source_base_requirements(state, sources=sources)[0]["base_commit_sha"] == pinned
    )


def test_submission_rechecks_already_composed_worktree(tmp_path: Path):
    state, managed, repo_id, pinned, _newer, branch, candidate = _state(tmp_path)
    source = dict(
        dependency_task_id="contribution",
        deliverable_id="accepted",
        branch=branch,
        commit_sha=candidate,
        relationship_kind="required_contribution",
    )
    context = GitWorkContext(
        id="integration-context",
        agent_id="backend",
        task_id="integration",
        base_branch="main",
        base_commit=pinned,
        agent_branch=branch,
        dependency_git_sources=[source],
        dependency_composition_manifest=DependencyCompositionManifest(
            target_task_id="integration",
            managed_repo_id=repo_id,
            base_branch="main",
            base_commit_sha=pinned,
            base_tree_hash="a" * 40,
            dependency_git_sources=[source],
            source_fingerprint="x",
            composed_commit_sha=candidate,
            composed_tree_hash="b" * 40,
        ),
        preparation_generation=task_git_work_context_generation(state, "integration"),
    )
    state.git_work_contexts[context.id] = context
    with pytest.raises(GitWorkContextValidationError, match="outside pinned integration base"):
        bind_deliverable_git_refs_to_work_context(
            state,
            agent_id="backend",
            task_id="integration",
            deliverable={"git_refs": {"branch_context_id": context.id, "commit_sha": candidate}},
            candidate_commit_resolver=_team_mcp_git_candidate_commit_resolver(
                None, managed_git=managed
            ),
        )
    assert context.commit_sha is None


def test_unknown_source_base_is_not_guessed_from_current_main(tmp_path: Path):
    state, managed, repo_id, _pinned, _newer, _branch, _candidate = _state(tmp_path)
    state.git_work_contexts["source-context"].base_commit = None
    with pytest.raises(RuntimeError, match="no authoritative full Git base"):
        _project_repo_task_dependency_git_sources(
            SimpleNamespace(admin_service=SimpleNamespace(managed_git_admin=managed)),
            state=state,
            task_id="integration",
            managed_repo_id=repo_id,
        )


@pytest.mark.parametrize("clean_source", [False, True])
def test_nested_composition_checks_original_source_base(tmp_path: Path, clean_source: bool):
    state, managed, repo_id, pinned, _newer, branch, candidate = _state(
        tmp_path, clean_source=clean_source
    )
    nested_source = dict(
        dependency_task_id="contribution",
        deliverable_id="accepted",
        branch=branch,
        commit_sha=candidate,
    )
    manifest = DependencyCompositionManifest(
        target_task_id="aggregate",
        managed_repo_id=repo_id,
        base_branch="main",
        base_commit_sha=pinned,
        base_tree_hash="a" * 40,
        dependency_git_sources=[nested_source],
        source_fingerprint="legacy-composition",
        composed_commit_sha=candidate,
        composed_tree_hash="b" * 40,
        verified_candidate_commit_sha=candidate,
    )
    state.tasks["aggregate"] = state.tasks["contribution"].model_copy(
        update={
            "id": "aggregate",
            "deliverable_ids": ["aggregate-accepted"],
        }
    )
    state.git_work_contexts["aggregate-context"] = GitWorkContext(
        id="aggregate-context",
        agent_id="backend",
        task_id="aggregate",
        base_branch="main",
        base_commit=pinned,
        agent_branch=branch,
        dependency_git_sources=[nested_source],
        dependency_composition_manifest=manifest,
    )
    state.deliverables["aggregate-accepted"] = Deliverable(
        id="aggregate-accepted",
        task_id="aggregate",
        producer_agent_id="backend",
        summary="Previously composed work",
        state=TeamRecordState.ACCEPTED,
        dependency_composition_manifest=manifest,
        git_refs=DeliverableGitRefs(
            branch_context_id="aggregate-context",
            agent_branch=branch,
            commit_sha=candidate,
            submitted_commit_sha=candidate,
        ),
    )
    state.tasks["integration"].deliverable_contract.required_contribution_task_ids = ["aggregate"]

    def resolve():
        return _project_repo_task_dependency_git_sources(
            SimpleNamespace(admin_service=SimpleNamespace(managed_git_admin=managed)),
            state=state,
            task_id="integration",
            managed_repo_id=repo_id,
        )

    if clean_source:
        assert resolve()[0]["deliverable_id"] == "aggregate-accepted"
    else:
        with pytest.raises(RuntimeError, match="contribution accepted.*outside pinned"):
            resolve()


def test_managed_verifier_rejects_false_recorded_source_ancestry(tmp_path: Path):
    managed, repo_id, pinned, newer, _branch, _candidate = _history(tmp_path)
    with pytest.raises(ValueError, match="does not contain its recorded base"):
        managed.verify_contribution_bases(
            repo_id,
            directory_path="root",
            base_commit_sha=newer,
            sources=[dict(deliverable_id="source", commit_sha=pinned, base_commit_sha=newer)],
        )
