from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import timedelta
from types import SimpleNamespace

import pytest

from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.git_submissions import GitSubmissionStore
from evander.models import utc_now
from evander.worker.models import (
    WorkerRepoCloneRequest,
    WorkerSessionPaths,
    WorkerTaskWorktreeRequest,
)
from evander.worker.service import WorkerDaemon
from evander_team_addon.models import (
    AgentRuntime,
    ProjectGitConfig,
    TeamDispatch,
    TeamDispatchStatus,
    TeamGitPreparationAttemptState,
)
from evander_team_addon.provider_git_contexts import _create_project_repo_git_work_contexts
from evander_team_addon.provider_git_errors import DependencyGitPreparationError
from evander_team_addon.provider_prompts import _build_prompt
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
    homes = {"session-backend": paths.agent_home}
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

    _local_agent_python_transport(monkeypatch, homes)
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

    def run(agent_id="backend"):
        nonlocal counter
        counter += 1
        dispatch = f"dispatch-{counter}"
        session_id = repo.read().agents[agent_id].session_id
        if session_id not in homes:
            daemon.clone_repo(
                session_id, WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo")
            )
            homes[session_id] = WorkerSessionPaths.resolve(
                tmp_path / "worker-root", session_id
            ).agent_home
        repo.update(
            lambda state: setattr(
                state.agent_runtimes.setdefault(agent_id, AgentRuntime(agent_id=agent_id)),
                "active_dispatch_id",
                dispatch,
            )
        )
        state = repo.read()
        result, rows, _ = _create_project_repo_git_work_contexts(
            context,
            repo,
            state=state,
            agent_id=agent_id,
            session_id=session_id,
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
        session_id = repo.read().agents[row["agent_id"]].session_id
        return homes[session_id] / row["worktree_path"].removeprefix("/home/agent/")

    def handoff(agent_id):
        def mutate(state):
            state.agent_runtimes[state.tasks["task"].owner_agent_id].active_dispatch_id = None
            state.tasks["task"].owner_agent_id = agent_id

        repo.update(mutate)

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
        handoff=handoff,
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


@pytest.mark.parametrize("interruption", [None, "apply", "context_commit"])
def test_returning_to_previous_specification_keeps_latest_work_and_receipts(
    continuity, monkeypatch, interruption
):
    h = continuity
    description = h.repo.read().tasks["task"].description
    _, original = h.run()
    h.commit(h.physical(original), "work under specification A\n")
    h.edit()
    state, changed = h.run()
    latest = h.commit(h.physical(changed), "newer work under specification B\n")
    receipts = {
        key: row.model_dump(mode="json") for key, row in state.git_preparation_attempts.items()
    }
    TeamAdminService(h.repo).update_task(
        task_id="task", description=description, expected_revision=h.repo.read().revision
    )
    if interruption is not None:
        with monkeypatch.context() as patch:
            if interruption == "apply":
                patch.setattr(
                    "evander_team_addon.provider_git_contexts.apply_retained_task_work",
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupted")),
                )
            else:

                def conflict(*_args, **_kwargs):
                    raise TeamStateConflictError(expected_revision=1, current_revision=2)

                patch.setattr(TeamAdminService, "create_git_work_context_with_id", conflict)
            with pytest.raises(RuntimeError):
                h.run()
        advanced = h.commit(h.physical(changed), "old source advanced after interruption\n")
        assert advanced != latest
    state, resumed = h.run()
    resumed_path = h.physical(resumed)
    assert resumed["preparation_generation"] == original["preparation_generation"]
    assert resumed_path not in {h.physical(original), h.physical(changed)}
    assert git(resumed_path, "rev-parse", "HEAD") == latest
    assert resumed["retained_work"]["source_context_id"] == changed["id"]
    assert resumed["retained_work"]["commit_sha"] == latest
    attempts = state.git_preparation_attempts
    new_attempts = [row for key, row in attempts.items() if key not in receipts]
    assert len(new_attempts) == 1
    assert new_attempts[0].head_commit_sha == latest
    assert new_attempts[0].activation_sequence > max(
        row["activation_sequence"] for row in receipts.values()
    )
    assert {key: attempts[key].model_dump(mode="json") for key in receipts} == receipts
    assert state.deliverables == {} and state.release_packages == {}
    # Repeated wakes of the reactivated specification preserve later coding and
    # reuse this preparation instead of allocating another checkout or receipt.
    later = h.commit(resumed_path, "later active implementation\n")
    after, again = h.run()
    assert h.physical(again) == resumed_path
    assert git(resumed_path, "rev-parse", "HEAD") == later
    assert set(after.git_preparation_attempts) == set(attempts)


@pytest.mark.parametrize("fresh", [True, False])
def test_final_wake_prompt_keeps_restored_provenance_once_with_bounded_size(continuity, fresh):
    h = continuity
    _, original = h.run()
    commit = h.commit(h.physical(original))
    h.edit()
    _, restored = h.run()
    # Diagnostics and full source paths belong on the retrievable durable
    # context, not in every wake. Extra fields must not bypass the projection.
    restored["retained_work"]["diagnostics"] = "verbose history " * 1000
    repo = {"prepared": True, "project_id": "project", "git_work_contexts": [restored]}
    lease = {
        "agent": {"id": "backend", "role_name": "Developer", "effective_role_permissions": []},
        "runtime": {"context_clearing_policy": "off", "context_rotation_pending": False},
        "has_prior_successful_wake": not fresh,
        "reason": "scheduled",
        "dispatch": {"id": restored["dispatch_id"], "task_ids": ["task"]},
        "project_repo": repo,
        "project_repos": [repo],
    }
    prompt = _build_prompt(lease, effective_resume_mode="new_context" if fresh else "same_context")
    payload = json.loads(
        prompt.split("Team context JSON:\n", 1)[1].split("\n\nMCP action surface:", 1)[0]
    )
    [binding] = payload["git_work_contexts"].values()
    assert binding["retained_work"] == {
        "source_context_id": original["id"],
        "commit_sha": commit,
        "tree_hash": git(h.physical(restored), "rev-parse", "HEAD^{tree}"),
        "root_commit_sha": h.base,
    }
    assert payload["context_mode"] == ("fresh_full" if fresh else "retained_current")
    assert prompt.count('"retained_work":') == 1
    assert prompt.count(restored["worktree_path"]) == 1
    assert "verbose history" not in prompt
    assert original["worktree_path"] not in prompt
    assert original["agent_branch"] not in prompt
    restored.pop("retained_work")
    ordinary = _build_prompt(
        lease, effective_resume_mode="new_context" if fresh else "same_context"
    )
    assert '"retained_work":' not in ordinary
    # Exact object IDs are essential provenance. Bound the additional serialized
    # working set to a small record, rather than asserting incidental prose.
    assert len(prompt.encode()) - len(ordinary.encode()) <= 360


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


def test_clean_unchanged_checkout_allows_handoff_through_original_session(continuity, monkeypatch):
    h = continuity
    state, old = h.run()
    original_context = state.git_work_contexts[old["id"]].model_dump(mode="json")
    original_receipts = {
        k: a.model_dump(mode="json") for k, a in state.git_preparation_attempts.items()
    }
    calls = []
    execute = AgentExecEffectProvider.execute

    def record(self, **kwargs):
        effect = self._effect()
        calls.append((effect.session_id, effect.command[3]))
        return execute(self, **kwargs)

    monkeypatch.setattr(AgentExecEffectProvider, "execute", record)
    h.handoff("lead")
    state, new = h.run("lead")
    assert ("session-backend", "probe") in calls
    assert new["agent_id"] == "lead" and state.git_work_contexts[new["id"]].retained_work is None
    assert git(h.physical(new), "rev-parse", "HEAD") == h.base
    assert git(h.physical(old), "rev-parse", "HEAD") == h.base
    assert git(h.physical(old), "status", "--porcelain") == ""
    assert state.git_work_contexts[old["id"]].model_dump(mode="json") == original_context
    assert {
        k: state.git_preparation_attempts[k].model_dump(mode="json") for k in original_receipts
    } == original_receipts
    assert state.deliverables == {} and state.release_packages == {}


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


@pytest.mark.parametrize(
    "change", ["unstaged", "staged", "untracked", "committed", "missing", "advanced_base"]
)
def test_handoff_preserves_changed_or_unverifiable_previous_checkout(continuity, change):
    h = continuity
    _, old = h.run()
    path = h.physical(old)
    if change in {"unstaged", "staged", "untracked"}:
        name = "new.txt" if change == "untracked" else "logic.txt"
        (path / name).write_text("unpublished work\n")
        if change == "staged":
            git(path, "add", name)
    elif change in {"committed", "advanced_base"}:
        head = h.commit(path)
        if change == "advanced_base":
            # Same-owner legacy base advancement is not proof of an empty handoff.
            git(path, "config", "--worktree", "evander.baseCommit", head)
            h.repo.update(
                lambda state: setattr(state.git_work_contexts[old["id"]], "base_commit", head)
            )
    else:
        path.rename(path.with_name(path.name + "-preserved"))
    before = (
        None
        if change == "missing"
        else (
            git(path, "rev-parse", "HEAD"),
            git(path, "status", "--porcelain"),
            {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()},
        )
    )
    count = len(h.requests)
    h.handoff("lead")
    with pytest.raises(DependencyGitPreparationError) as failure:
        h.run("lead")
    assert (
        failure.value.cause
        == {
            "unstaged": "workspace_dirty",
            "staged": "workspace_dirty",
            "untracked": "workspace_dirty",
            "committed": "policy_conflict",
            "missing": "workspace_missing",
            "advanced_base": "workspace_receipt_mismatch",
        }[change]
    )
    assert len(h.requests) == count
    if before is not None:
        assert (
            git(path, "rev-parse", "HEAD"),
            git(path, "status", "--porcelain"),
            {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()},
        ) == before


@pytest.mark.parametrize("status", [TeamDispatchStatus.PREPARING, TeamDispatchStatus.LEASED])
@pytest.mark.parametrize("reference", ["task", "receipt"])
def test_handoff_waits_for_previous_task_writer(continuity, status, reference):
    h = continuity
    _, old = h.run()

    def writer(state):
        dispatch_id = (
            state.git_work_contexts[old["id"]].dispatch_id
            if reference == "receipt"
            else "old-task-writer"
        )
        state.dispatches[dispatch_id] = TeamDispatch(
            id=dispatch_id,
            agent_id="backend",
            session_id="session-backend",
            cause_kind="task",
            task_ids=["task"] if reference == "task" else [],
            status=status,
            queued_at=utc_now(),
            leased_at=utc_now() if status == TeamDispatchStatus.LEASED else None,
            lease_expires_at=utc_now() + timedelta(minutes=15)
            if status == TeamDispatchStatus.LEASED
            else None,
        )

    h.repo.update(writer)
    h.handoff("lead")
    count = len(h.requests)
    with pytest.raises(DependencyGitPreparationError) as failure:
        h.run("lead")
    assert failure.value.cause == "policy_conflict"
    assert len(h.requests) == count
    assert git(h.physical(old), "rev-parse", "HEAD") == h.base


@pytest.mark.parametrize("mismatch", ["session", "namespace"])
def test_handoff_rejects_foreign_provenance_before_any_session_probe(
    continuity, monkeypatch, mismatch
):
    h = continuity
    _, old = h.run()

    def corrupt(state):
        receipt = next(
            a for a in state.git_preparation_attempts.values() if a.git_work_context_id == old["id"]
        )
        if mismatch == "namespace":
            receipt.branch = state.git_work_contexts[old["id"]].agent_branch = (
                "agents/session-lead/tasks/wrong-owner"
            )
        else:
            setattr(receipt, mismatch + "_id", "different-" + mismatch)

    h.repo.update(corrupt)
    h.handoff("lead")
    monkeypatch.setattr(
        AgentExecEffectProvider,
        "execute",
        lambda *_a, **_kw: pytest.fail("invalid provenance reached a session"),
    )
    count = len(h.requests)
    with pytest.raises(DependencyGitPreparationError) as failure:
        h.run("lead")
    assert failure.value.cause == "policy_conflict"
    assert len(h.requests) == count


def test_repeated_handoffs_check_every_historical_owner(continuity):
    h = continuity
    _, original = h.run()
    h.handoff("lead")
    _, middle = h.run("lead")
    h.handoff("manager")
    _, newest = h.run("manager")
    assert all(
        git(h.physical(row), "rev-parse", "HEAD") == h.base for row in (original, middle, newest)
    )
    retained = h.commit(h.physical(original))
    h.handoff("lead")
    count = len(h.requests)
    with pytest.raises(DependencyGitPreparationError) as failure:
        h.run("lead")
    assert failure.value.cause == "policy_conflict"
    assert len(h.requests) == count
    assert git(h.physical(original), "rev-parse", "HEAD") == retained


def test_handoff_rejects_receipted_work_even_if_checkout_was_reset(continuity):
    h = continuity
    _, original = h.run()
    retained = h.commit(h.physical(original))
    h.edit()
    state, carried = h.run()
    assert (
        next(
            a
            for a in state.git_preparation_attempts.values()
            if a.git_work_context_id == carried["id"]
        ).head_commit_sha
        == retained
    )
    # Simulate externally reset work in the disposable test repository.
    for row in (original, carried):
        git(h.physical(row), "reset", "--hard", h.base)
    h.handoff("lead")
    count = len(h.requests)
    with pytest.raises(DependencyGitPreparationError) as failure:
        h.run("lead")
    assert failure.value.cause == "policy_conflict"
    assert len(h.requests) == count


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
        db.execute(
            "UPDATE team_git_preparation_attempts SET payload_json=json_remove(payload_json, '$.activation_sequence')"
        )
    migrated = TeamStateRepository(h.repo.runtime_dir).read()
    assert migrated.revision == before.revision
    assert migrated.tasks["task"] == before.tasks["task"]
    assert migrated.git_work_contexts[old["id"]] == before.git_work_contexts[old["id"]]
    assert migrated.git_work_contexts[old["id"]].retained_work is None
    assert migrated.git_preparation_attempts == before.git_preparation_attempts


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
