from __future__ import annotations

import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.git_submissions import GitSubmissionStore
from evander.worker.models import (
    WorkerRepoCloneRequest,
    WorkerSessionPaths,
    WorkerTaskWorktreeRequest,
)
from evander.worker.service import WorkerDaemon
from evander_team_addon.models import AgentRuntime, ProjectGitConfig, TeamGitPreparationAttemptState
from evander_team_addon.provider_git_contexts import _create_project_repo_git_work_contexts
from evander_team_addon.provider_git_errors import DependencyGitPreparationError
from evander_team_addon.repository import TeamStateConflictError
from evander_team_addon.services.admin_service import TeamAdminService
from team_runtime_support import _seed_team, _local_agent_python_transport


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, timeout=15
    ).stdout.strip()


@pytest.fixture
def continuity(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.name", "Original Author")
    git(source, "config", "user.email", "original@example.invalid")
    (source / "logic.txt").write_text("original\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "Candidate")
    base = git(source, "rev-parse", "HEAD")
    repo = _seed_team(tmp_path / "team")

    def seed(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd", directory_path="root", base_branch="main"
        )
        state.tasks["task"].git_base_branch = "main"
        state.tasks["task"].git_base_commit_sha = base

    repo.update(seed)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "worker-runtime",
        worker_root=tmp_path / "worker-root",
        allowed_git_source_prefixes=[str(tmp_path)],
        container_runtime=SimpleNamespace(),
    )
    daemon.clone_repo(
        "session-backend", WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo")
    )
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-backend")
    requests = []
    dependencies = []

    def prepare(*, session_id, **kwargs):
        request = WorkerTaskWorktreeRequest(**kwargs)
        requests.append(request)
        return daemon.prepare_task_worktree(session_id, request)

    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.container_backend",
        lambda *_args: SimpleNamespace(prepare_task_worktree=prepare),
    )
    monkeypatch.setattr(
        "evander_team_addon.provider_git_contexts._project_repo_task_dependency_git_sources",
        lambda *_args, **_kwargs: list(dependencies),
    )

    _local_agent_python_transport(monkeypatch, paths.agent_home)
    store = GitSubmissionStore(repo.runtime_dir)
    managed = SimpleNamespace(
        reduce_commit_frontier=lambda *_args, **kwargs: {
            "base_tree_hash": git(source, "rev-parse", kwargs["base_commit_sha"] + "^{tree}"),
            "frontier_commit_shas": [row["commit_sha"] for row in kwargs["sources"]],
            "reduced_source_deliverable_ids": [row["deliverable_id"] for row in kwargs["sources"]],
        }
    )
    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(repo.runtime_dir)},
        admin_service=SimpleNamespace(
            managed_git_admin=managed, register_git_submission_checkout=store.register
        ),
    )
    counter = 0

    def run():
        nonlocal counter
        counter += 1
        dispatch = f"dispatch-{counter}"
        repo.update(
            lambda state: setattr(state.agent_runtimes["backend"], "active_dispatch_id", dispatch)
        )
        state = repo.read()
        result, rows, _ = _create_project_repo_git_work_contexts(
            context,
            repo,
            state=state,
            agent_id="backend",
            session_id="session-backend",
            project_id="project",
            dispatch_id=dispatch,
            project_repo={
                "managed_repo_id": state.projects["project"].git.managed_repo_id,
                "guest_repo_name": "repo",
                "target_dir": "/home/agent/repos/repo",
                "execution_backend": "container_worker_vm",
            },
            tasks=[state.tasks["task"].model_dump(mode="json")],
            base_branch="main",
        )
        return result, rows[0]

    def physical(row):
        return paths.agent_home / row["worktree_path"].removeprefix("/home/agent/")

    def edit():
        state = repo.read()
        return TeamAdminService(repo).update_task(
            task_id="task",
            description=state.tasks["task"].description
            + "\nRevise the remaining implementation instructions.",
            expected_revision=state.revision,
        )

    def commit(path, text="retained repair\n"):
        (path / "logic.txt").write_text(text)
        git(path, "add", "logic.txt")
        git(path, "commit", "-qm", "Retained correction")
        return git(path, "rev-parse", "HEAD")

    return SimpleNamespace(
        repo=repo,
        run=run,
        physical=physical,
        edit=edit,
        commit=commit,
        base=base,
        requests=requests,
        dependencies=dependencies,
        paths=paths,
        store=store,
        source=source,
    )


def test_changed_instructions_carry_real_commits_into_fresh_receipted_checkout(continuity):
    h = continuity
    _, old = h.run()
    old_path = h.physical(old)
    retained = h.commit(old_path)
    metadata = git(old_path, "show", "-s", "--format=%H %T %P %an %ae %cn %ce %aI %cI", retained)
    h.edit()
    state, new = h.run()
    new_path = h.physical(new)
    assert new_path != old_path
    assert new["id"] != old["id"] and new["checkout_id"] != old["checkout_id"]
    assert git(new_path, "rev-parse", "HEAD") == retained
    assert (new_path / "logic.txt").read_text() == "retained repair\n"
    assert (
        git(new_path, "show", "-s", "--format=%H %T %P %an %ae %cn %ce %aI %cI", "HEAD") == metadata
    )
    assert git(new_path, "status", "--porcelain") == ""
    assert git(new_path, "config", "--worktree", "--get", "evander.baseCommit") == h.base
    assert new["retained_work"]["commit_sha"] == retained
    assert new["retained_work"]["source_context_id"] == old["id"]
    projected = h.repo.read_projection(collections=["git_work_contexts"])
    assert projected.git_work_contexts[new["id"]].retained_work.commit_sha == retained
    assert state.git_work_contexts[old["id"]].state.value == "abandoned"
    attempt = next(
        a for a in state.git_preparation_attempts.values() if a.git_work_context_id == new["id"]
    )
    assert attempt.head_commit_sha == retained and attempt.base_commit_sha == h.base
    binding = next(
        b for b in h.store.list("session-backend") if b["checkout_id"] == new["checkout_id"]
    )
    assert binding["generation"] == new["preparation_generation"]
    assert binding["guest_path"] == new["worktree_path"]
    assert state.deliverables == {} and state.release_packages == {}
    # Later coding on this generation must never reset to the carried tip.
    later = h.commit(new_path, "later implementation\n")
    _, following = h.run()
    assert h.physical(following) == new_path
    assert git(new_path, "rev-parse", "HEAD") == later
    # Another specification edit chooses the descendant containing all earlier
    # generations, rather than mistaking their different tips for divergence.
    h.edit()
    _, newest = h.run()
    assert h.physical(newest) != new_path
    assert git(h.physical(newest), "rev-parse", "HEAD") == later
    assert newest["retained_work"]["source_context_id"] == following["id"]


@pytest.mark.parametrize(
    "problem",
    [
        "dirty",
        "dependency_changed",
        "baseline_changed",
        "pinned_baseline_changed",
        "forged_base",
        "missing",
        "divergent",
        "directory_changed",
    ],
)
def test_incompatible_or_uncommitted_work_is_preserved_and_blocks_preparation(continuity, problem):
    h = continuity
    _, old = h.run()
    path = h.physical(old)
    retained = h.commit(path)
    if problem == "dirty":
        (path / "unfinished.txt").write_text("keep these uncommitted bytes\n")
    if problem == "divergent":
        h.edit()
        _, second = h.run()
        h.commit(h.physical(second), "other generation\n")
        h.commit(path, "divergent older branch\n")
    h.edit()
    if problem == "dependency_changed":
        h.dependencies.append(
            {
                "dependency_task_id": "other",
                "deliverable_id": "accepted-other",
                "branch": "agents/other/task",
                "commit_sha": "b" * 40,
            }
        )
    if problem == "baseline_changed":
        h.repo.update(
            lambda state: setattr(state.tasks["task"], "git_base_branch", "different-workstream")
        )
    if problem == "pinned_baseline_changed":
        (h.source / "new-base.txt").write_text("different original base\n")
        git(h.source, "add", "new-base.txt")
        git(h.source, "commit", "-qm", "Advance original base")
        h.repo.update(
            lambda state: setattr(
                state.tasks["task"], "git_base_commit_sha", git(h.source, "rev-parse", "HEAD")
            )
        )
    if problem == "forged_base":
        git(path, "config", "--worktree", "evander.baseCommit", retained)
    if problem == "directory_changed":

        def change_directory(state):
            state.projects["project"].directory_path = "root/other"
            state.projects["project"].git.directory_path = "root/other"

        h.repo.update(change_directory)
    if problem == "missing":
        path.rename(path.with_name(path.name + "-preserved"))
    with pytest.raises(DependencyGitPreparationError) as failure:
        h.run()
    expected = {
        "dirty": "workspace_dirty",
        "dependency_changed": "incompatible_source_base",
        "baseline_changed": "incompatible_source_base",
        "pinned_baseline_changed": "incompatible_source_base",
        "forged_base": "workspace_receipt_mismatch",
        "missing": "workspace_missing",
        "divergent": "incompatible_source_base",
        "directory_changed": "policy_conflict",
    }
    assert failure.value.cause == expected[problem]
    state = h.repo.read()
    assert state.deliverables == {} and state.release_packages == {}
    if problem == "dirty":
        assert (path / "unfinished.txt").read_text() == "keep these uncommitted bytes\n"
    if problem not in {"missing", "divergent"}:
        assert git(path, "rev-parse", "HEAD") == retained
    assert not any(c.state.value == "active" for c in state.git_work_contexts.values())


@pytest.mark.parametrize("failure_phase", ["apply", "context_commit"])
def test_retry_uses_persisted_source_pin_even_if_old_branch_advances(
    continuity, monkeypatch, failure_phase
):
    h = continuity
    _, old = h.run()
    path = h.physical(old)
    retained = h.commit(path)
    h.edit()
    if failure_phase == "apply":
        original = __import__(
            "evander_team_addon.provider_git_contexts", fromlist=["apply_retained_task_work"]
        ).apply_retained_task_work
        monkeypatch.setattr(
            "evander_team_addon.provider_git_contexts.apply_retained_task_work",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("interrupted before transfer")
            ),
        )
        expected = RuntimeError
    else:
        original = TeamAdminService.create_git_work_context_with_id

        def conflict(*_args, **_kwargs):
            raise TeamStateConflictError(expected_revision=1, current_revision=2)

        monkeypatch.setattr(TeamAdminService, "create_git_work_context_with_id", conflict)
        expected = TeamStateConflictError
    with pytest.raises(expected):
        h.run()
    current = next(
        a
        for a in h.repo.read().git_preparation_attempts.values()
        if a.preparation_generation != old["preparation_generation"]
    )
    assert current.retained_work.commit_sha == retained
    if failure_phase == "context_commit":
        assert current.state == TeamGitPreparationAttemptState.PREPARED
    advanced = h.commit(path, "later old-branch work\n")
    if failure_phase == "apply":
        monkeypatch.setattr(
            "evander_team_addon.provider_git_contexts.apply_retained_task_work", original
        )
    else:
        monkeypatch.setattr(TeamAdminService, "create_git_work_context_with_id", original)
    _, new = h.run()
    assert git(h.physical(new), "rev-parse", "HEAD") == retained
    assert git(path, "rev-parse", "HEAD") == advanced
    assert new["retained_work"]["commit_sha"] == retained


def test_specification_race_rejects_pin_before_target_is_created(continuity, monkeypatch):
    h = continuity
    _, old = h.run()
    h.commit(h.physical(old))
    h.edit()
    execute = AgentExecEffectProvider.execute

    def racing(self, **kwargs):
        result = execute(self, **kwargs)
        if self._effect().command[3] == "probe":
            h.edit()
        return result

    monkeypatch.setattr(AgentExecEffectProvider, "execute", racing)
    count = len(h.requests)
    with pytest.raises(DependencyGitPreparationError, match="generation changed"):
        h.run()
    assert len(h.requests) == count
    assert all(a.retained_work is None for a in h.repo.read().git_preparation_attempts.values())


def test_new_owner_cannot_automatically_import_previous_owners_work(continuity):
    h = continuity
    _, old = h.run()
    retained = h.commit(h.physical(old))
    h.edit()

    def reassign(state):
        state.tasks["task"].owner_agent_id = "lead"
        state.agent_runtimes.setdefault("lead", AgentRuntime(agent_id="lead"))
        state.agent_runtimes["lead"].active_dispatch_id = "dispatch-new-owner"

    h.repo.update(reassign)
    state = h.repo.read()
    with pytest.raises(DependencyGitPreparationError) as failure:
        _create_project_repo_git_work_contexts(
            EffectRuntimeContext(resolved_config={"runtime_dir": str(h.repo.runtime_dir)}),
            h.repo,
            state=state,
            agent_id="lead",
            session_id="session-lead",
            project_id="project",
            dispatch_id="dispatch-new-owner",
            project_repo={
                "managed_repo_id": "mr_0123456789abcdefabcd",
                "guest_repo_name": "repo",
                "target_dir": "/home/agent/repos/repo",
                "execution_backend": "container_worker_vm",
            },
            tasks=[state.tasks["task"].model_dump(mode="json")],
            base_branch="main",
        )
    assert failure.value.cause == "policy_conflict"
    assert git(h.physical(old), "rev-parse", "HEAD") == retained


@pytest.mark.parametrize("pinned", [False, True])
def test_continuity_keeps_dependency_composition_distinct_from_owned_work(continuity, pinned):
    h = continuity
    if not pinned:
        h.repo.update(lambda state: setattr(state.tasks["task"], "git_base_commit_sha", None))
    branch = "agents/dependency/tasks/input"
    git(h.source, "checkout", "-qb", branch)
    (h.source / "dependency.txt").write_text("accepted input\n")
    git(h.source, "add", "dependency.txt")
    git(h.source, "commit", "-qm", "Dependency")
    dependency = git(h.source, "rev-parse", "HEAD")
    git(h.source, "update-ref", "refs/evander/" + branch, dependency)
    git(h.source, "checkout", "-q", "main")
    h.dependencies.append(
        {
            "dependency_task_id": "input",
            "deliverable_id": "accepted-input",
            "branch": branch,
            "commit_sha": dependency,
        }
    )
    _, old = h.run()
    retained = h.commit(h.physical(old))
    h.edit()
    state, new = h.run()
    assert git(h.physical(new), "rev-parse", "HEAD") == retained
    assert (h.physical(new) / "dependency.txt").read_text() == "accepted input\n"
    manifest = state.git_work_contexts[new["id"]].dependency_composition_manifest
    assert manifest.base_commit_sha == h.base
    assert manifest.composed_commit_sha == dependency
    assert new["retained_work"]["commit_sha"] == retained
    assert new["retained_work"]["root_commit_sha"] == h.base
    assert new["retained_work"]["base_commit_sha"] == dependency
    # Receipt replay must use the composed prepared base, without losing the
    # separately pinned original root or pretending owned work was accepted.
    _, again = h.run()
    assert git(h.physical(again), "rev-parse", "HEAD") == retained


def test_existing_sqlite_store_adds_nullable_provenance_without_rewriting_state(continuity):
    from evander_team_addon.repository import TeamStateRepository

    h = continuity
    before, old = h.run()
    with sqlite3.connect(h.repo.store.path) as db:
        db.execute("ALTER TABLE team_git_work_contexts DROP COLUMN retained_work_json")
        db.execute("UPDATE team_state_metadata SET value='85' WHERE key='sqlite_schema_version'")
    migrated = TeamStateRepository(h.repo.runtime_dir).read()
    assert migrated.revision == before.revision
    assert migrated.tasks["task"] == before.tasks["task"]
    assert migrated.git_work_contexts[old["id"]] == before.git_work_contexts[old["id"]]
    assert migrated.git_work_contexts[old["id"]].retained_work is None


def test_admin_cannot_attach_unattested_retained_commit_to_checkout(continuity):
    h = continuity
    _, old = h.run()
    h.commit(h.physical(old))
    h.edit()
    _, new = h.run()
    forged = {**new["retained_work"], "commit_sha": "e" * 40}
    with pytest.raises(ValueError, match="server-owned prepared attempt"):
        TeamAdminService(h.repo).create_git_work_context_with_id(
            context_id="forged",
            agent_id="backend",
            task_id="task",
            agent_branch=new["agent_branch"],
            base_branch="main",
            base_commit=h.base,
            dispatch_id=new["dispatch_id"],
            worktree_path=new["worktree_path"],
            retained_work=forged,
        )
    assert "forged" not in h.repo.read().git_work_contexts
