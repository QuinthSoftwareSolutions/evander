import base64
import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from evander.addons import AddonContext
from evander.admin.services.git_submission_service import GitSubmissionService
from evander.effects.providers.base import EffectRuntimeContext
from evander_team_addon.git_submission import validate_submission_checkout
from evander_team_addon.models import ProjectGitConfig, GitWorkContextState
from evander_team_addon.providers import (
    TeamWakeDueAgentsEffectDefinition,
    TeamWakeDueAgentsEffectProvider,
    _create_project_repo_git_work_contexts,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.read_service import TeamReadService
from evander_team_addon.services.wake_prompt_context import build_compact_wake_context
from team_runtime_support import (
    FakeSessionStore,
    FakeResumer,
    FakeFileCopier,
    FakeRepoCloner,
    FakeVmExecutor,
    _seed_team,
)
from test_git_submission import REPO_ID, SUMMARY, setup_submission as setup_submission
from test_guest_push_agent_script import _git, _git_dir


@pytest.fixture
def prepared_team_checkout(tmp_path):
    repo_id = "mr_0123456789abcdefabcd"
    repo = _seed_team(tmp_path)

    def configure(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id=repo_id, base_branch="main"
        )
        return state

    repo.update(configure)
    sessions = FakeSessionStore()
    session = sessions.get_session("session-backend")
    session.metadata["managed_git_access_grants"] = {
        repo_id: {
            "managed_repo_id": repo_id,
            "access_mode": "write",
            "lifetime": "session",
        }
    }
    addon_context = AddonContext(
        addon_id="team-addon", runtime_dir=tmp_path, addon_runtime_dir=tmp_path
    )
    service = GitSubmissionService(
        SimpleNamespace(session_repository=sessions),
        SimpleNamespace(runtime_dir=tmp_path, clone_info=lambda *a, **kw: {}),
        tmp_path,
        execute=lambda *a: pytest.fail("A retired binding must not execute"),
        validators={
            "team-addon": lambda binding: validate_submission_checkout(addon_context, binding)
        },
    )
    admin = SimpleNamespace(
        register_git_submission_checkout=service.register_checkout, git_submission_admin=service
    )
    provider = TeamWakeDueAgentsEffectProvider()
    runtime_context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(tmp_path)},
        agent_resumer=FakeResumer(),
        vm_file_copier=FakeFileCopier(),
        vm_repo_cloner=FakeRepoCloner(),
        vm_executor=FakeVmExecutor(),
        session_lookup=sessions,
        session_metadata_updater=sessions,
        admin_service=admin,
    )
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake", display_name="Wake", type="effect:team:wake-due-agents"
        ),
        runtime_context,
    )
    result = provider.execute()
    assert result["dispatches"], result.get("errors", result)
    context = result["dispatches"][0]["project_repo"]["git_work_contexts"][0]
    return SimpleNamespace(
        repo=repo, service=service, session=session, context=context,
        runtime_context=runtime_context,
        project_repo=result["dispatches"][0]["project_repo"],
    )


@pytest.mark.parametrize("retirement", ["closed", "generation", "owner"])
def test_team_preparation_registers_and_revalidates_submission_binding(
    prepared_team_checkout, retirement,
):
    prepared = prepared_team_checkout
    repo, service, session, context = (
        prepared.repo, prepared.service, prepared.session, prepared.context,
    )
    cid = context["checkout_id"]
    assert service.resolve(session, checkout_id=cid)["success"]
    assert "submit_git_changes" in session.allowed_tools
    reopened = TeamStateRepository(repo.runtime_dir)
    assert reopened.read().git_work_contexts[context["id"]].checkout_id == cid

    def unrelated_update(state):
        state.agents["lead"].display_name = "Updated team lead"

    reopened.update(unrelated_update)
    state = TeamStateRepository(repo.runtime_dir).read()
    assert state.git_work_contexts[context["id"]].checkout_id == cid
    readback = TeamReadService(state=state, actor=state.agents["backend"])
    assert readback.get_task("task")["git_work_contexts"][0]["checkout_id"] == cid
    compact = build_compact_wake_context({"project_repos": [prepared.project_repo]})
    assert [row["checkout_id"] for row in compact["git_work_contexts"].values()] == [cid]

    def retire(state):
        work = state.git_work_contexts[context["id"]]
        if retirement == "closed":
            work.state = GitWorkContextState.ABANDONED
        elif retirement == "generation":
            state.tasks[work.task_id].description += " changed implementation requirements"
        else:
            state.tasks[work.task_id].owner_agent_id = "lead"
        return state

    repo.update(retire)
    assert service.resolve(session, checkout_id=cid)["code"] == "checkout_unavailable"
    assert cid not in {binding["checkout_id"] for binding in service.store.list(session.session_id)}


def test_legacy_git_context_column_upgrade_is_nullable_and_idempotent(prepared_team_checkout):
    prepared = prepared_team_checkout
    repo = prepared.repo
    before = repo.read()
    context_id = prepared.context["id"]
    # Recreate the physical v81 boundary, rather than merely lowering metadata.
    with sqlite3.connect(repo.store.path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(team_git_work_contexts)")}
        if "checkout_id" in columns:
            connection.execute("ALTER TABLE team_git_work_contexts DROP COLUMN checkout_id")
        connection.execute(
            "UPDATE team_state_metadata SET value = '81' WHERE key = 'sqlite_schema_version'"
        )

    for _ in range(2):
        upgraded = TeamStateRepository(repo.runtime_dir)
        state = upgraded.read()
        with sqlite3.connect(upgraded.store.path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(team_git_work_contexts)")}
        assert "checkout_id" in columns
        assert state.git_work_contexts[context_id].checkout_id is None
        assert state.git_work_contexts[context_id] == before.git_work_contexts[context_id].model_copy(
            update={"checkout_id": None}
        )
        assert state.tasks == before.tasks
        assert state.schema_version == before.schema_version


@pytest.mark.parametrize("changed", [None, "worktree_path", "agent_branch", "base_commit", "closed"])
def test_registered_context_replay_preserves_binding_and_rejects_changed_provenance(
    prepared_team_checkout, changed,
):
    prepared = prepared_team_checkout
    repo = prepared.repo
    context_id = prepared.context["id"]
    work = repo.read().git_work_contexts[context_id]
    args = {name: getattr(work, name) for name in (
        "agent_id", "task_id", "agent_branch", "base_branch", "base_commit", "commit_sha",
        "dispatch_id", "worktree_path", "dependency_git_sources",
        "dependency_composition_recovery_id", "manual_dependency_git_sources",
        "dependency_composition_manifest",
    )}
    args.update(context_id=context_id, expected_preparation_generation=work.preparation_generation)
    if changed == "closed":
        repo.update(lambda state: setattr(
            state.git_work_contexts[context_id], "state", GitWorkContextState.ABANDONED
        ))
    elif changed:
        args[changed] = "f" * 40 if changed == "base_commit" else args[changed] + "-other"
    admin = TeamAdminService(repo)
    if changed:
        with pytest.raises(ValueError, match="different resource provenance|does not match task"):
            admin.create_git_work_context_with_id(**args)
    else:
        _state, replay_id = admin.create_git_work_context_with_id(**args)
        assert replay_id == context_id
        persisted = TeamStateRepository(repo.runtime_dir).read().git_work_contexts[context_id]
        assert persisted.checkout_id == prepared.context["checkout_id"]
        assert prepared.service.resolve(prepared.session, checkout_id=persisted.checkout_id)["success"]


def test_normal_preparation_repairs_missing_id_after_interrupted_registration(
    prepared_team_checkout, monkeypatch,
):
    prepared = prepared_team_checkout
    repo = prepared.repo
    context_id = prepared.context["id"]
    original = repo.read().git_work_contexts[context_id]
    original_binding_ids = {
        binding["checkout_id"]
        for binding in prepared.service.store.list(prepared.session.session_id)
    }
    repo.update(lambda state: setattr(state.git_work_contexts[context_id], "checkout_id", None))

    def prepare():
        state = TeamStateRepository(repo.runtime_dir).read()
        return _create_project_repo_git_work_contexts(
            prepared.runtime_context, repo, state=state,
            agent_id=original.agent_id, session_id=prepared.session.session_id,
            project_id="project", project_repo=prepared.project_repo,
            dispatch_id=original.dispatch_id,
            tasks=[state.tasks[original.task_id].model_dump(mode="json")], base_branch="main",
        )

    def interrupted_registration(**kwargs):
        prepared.service.register_checkout(**kwargs)
        raise RuntimeError("Interrupted after canonical registration")

    admin = prepared.runtime_context.admin_service
    monkeypatch.setattr(admin, "register_git_submission_checkout", interrupted_registration)
    with pytest.raises(RuntimeError, match="Interrupted after canonical registration"):
        prepare()
    assert repo.read().git_work_contexts[context_id].checkout_id is None

    monkeypatch.setattr(admin, "register_git_submission_checkout", prepared.service.register_checkout)
    _state, rows, _metrics = prepare()
    persisted = TeamStateRepository(repo.runtime_dir).read().git_work_contexts[context_id]
    assert persisted == original
    assert persisted.checkout_id == rows[0]["checkout_id"] == prepared.context["checkout_id"]
    assert {
        binding["checkout_id"]
        for binding in prepared.service.store.list(prepared.session.session_id)
    } == original_binding_ids
    assert prepared.service.resolve(prepared.session, checkout_id=persisted.checkout_id)["success"]


def test_reloaded_team_selector_submits_exact_retained_worktree_with_multiple_checkouts(
    setup_submission, tmp_path,
):
    submission = setup_submission
    repo = _seed_team(submission.config.runtime_dir)
    branch = f"agents/{submission.session.session_id}/tasks/task"
    checkout = tmp_path / "task-worktree"
    _git(submission.agent, "worktree", "add", "-b", branch, str(checkout), submission.sha)
    base = _git(checkout, "rev-parse", "origin/main").stdout.strip()
    guest_path = "/home/agent/worktrees/Example/task"

    def configure(state):
        state.agents["backend"].session_id = submission.session.session_id
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id=REPO_ID, base_branch="main"
        )
        state.tasks["task"].git_base_branch = "main"
        state.tasks["task"].git_base_commit_sha = base

    repo.update(configure)
    admin = TeamAdminService(repo)
    state, context_id = admin.create_git_work_context_with_id(
        context_id="ctx-submission", agent_id="backend", task_id="task",
        agent_branch=branch, base_branch="main", base_commit=base, worktree_path=guest_path,
    )
    checkout_id = submission.service.register_checkout(
        session_id=submission.session.session_id, managed_repo_id=REPO_ID,
        guest_path=guest_path, owner_kind="team-addon", owner_id=context_id,
        generation=state.git_work_contexts[context_id].preparation_generation,
        fixed_branch=branch, prepared_base=base, provenance_base=base, base_branch="main",
    )
    repo.update(lambda current: setattr(
        current.git_work_contexts[context_id], "checkout_id", checkout_id
    ))
    addon_context = AddonContext(
        addon_id="team-addon", runtime_dir=repo.runtime_dir, addon_runtime_dir=repo.runtime_dir,
    )
    submission.service.validators["team-addon"] = lambda binding: validate_submission_checkout(
        addon_context, binding
    )

    def execute(session, command, working_directory):
        request = json.loads(base64.b64decode(command[-1]))
        assert request["guest_path"] == guest_path
        assert request["checkout_id"] == checkout_id
        submission.calls.append(working_directory)
        request.update(guest_path=str(checkout), remote_url=str(submission.bare))
        command = [*command[:-1], base64.b64encode(json.dumps(request).encode()).decode()]
        result = subprocess.run(
            command, cwd=submission.source, capture_output=True, text=True, timeout=20,
        )
        return dict(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)

    submission.service.execute = execute
    # A later ordinary state save must retain the selector, not just the first response.
    repo.update(lambda current: setattr(current.agents["lead"], "display_name", "Updated lead"))
    state = TeamStateRepository(repo.runtime_dir).read()
    row = TeamReadService(state=state, actor=state.agents["backend"]).get_task("task")
    exposed_id = row["git_work_contexts"][0]["checkout_id"]
    assert exposed_id == checkout_id
    (checkout / "file.txt").write_text("Pending tracked edit\n")
    (checkout / "pending.txt").write_text("Pending untracked edit\n")
    pending = _git(checkout, "status", "--porcelain").stdout

    ambiguous = submission.service.submit(
        submission.session, expected_commit_sha=submission.sha, summary_markdown=SUMMARY,
    )
    assert ambiguous["code"] == "checkout_ambiguous"
    assert not submission.calls
    receipt = submission.service.submit(
        submission.session, expected_commit_sha=submission.sha, summary_markdown=SUMMARY,
        managed_repo_id=REPO_ID, checkout_id=exposed_id,
    )
    assert receipt["success"], receipt
    assert receipt["checkout_id"] == checkout_id
    assert receipt["agent_branch"] == branch
    assert receipt["commit_sha"] == submission.sha
    assert _git_dir(submission.bare, "rev-parse", receipt["agent_ref"]).stdout.strip() == submission.sha
    blob = _git_dir(
        submission.bare, "notes", f"--ref={receipt['notes_ref']}", "list", submission.sha,
    ).stdout.strip()
    assert _git_dir(submission.bare, "cat-file", "blob", blob).stdout == SUMMARY
    assert _git(checkout, "rev-parse", "HEAD").stdout.strip() == submission.sha
    assert _git(checkout, "status", "--porcelain").stdout == pending
    assert (checkout / "file.txt").read_text() == "Pending tracked edit\n"
    assert (checkout / "pending.txt").read_text() == "Pending untracked edit\n"
