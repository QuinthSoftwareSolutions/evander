from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.worker.models import WorkerRepoCloneRequest, WorkerTaskWorktreeRequest
from evander.worker.service import WorkerDaemon, WorkerDependencyCompositionConflict
from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DependencyCompositionRecoveryState,
    GitWorkContextState,
    ProjectGitConfig,
    TeamGitPreparationAttemptState,
    TeamState,
    TeamTaskState,
    TaskDeliverableContract,
)
from evander_team_addon.providers import (
    DependencyGitPreparationError,
    _create_project_repo_git_work_contexts,
    _ensure_project_repo_git_preparation_attempt,
    _record_dependency_git_preparation_blocker,
)
from evander_team_addon.repository import TeamStateConflictError
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.dependency_composition_recovery import (
    accept_dependency_composition_recovery,
)
from evander_team_addon.services.runtime_service import TeamRuntimeService
from team_runtime_support import _seed_team


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()


@pytest.mark.parametrize("pinned_first_attempt", [False, True])
@pytest.mark.parametrize("assignment_mode", ["agent", "role"])
def test_conflicted_task_adopts_reviewed_recovery_in_distinct_retryable_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_first_attempt: bool,
    assignment_mode: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Recovery Test")
    (source / "shared.txt").write_text("base\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Base")
    base_commit = _git(source, "rev-parse", "HEAD")
    sources = []
    for name in ("first", "second"):
        branch = f"agents/source/tasks/{name}"
        _git(source, "checkout", "-q", "-b", branch, "main")
        (source / "shared.txt").write_text(f"{name}\n")
        _git(source, "commit", "-qam", name)
        sources.append({
            "dependency_task_id": f"task-{name}",
            "deliverable_id": f"deliverable-{name}",
            "branch": branch,
            "commit_sha": _git(source, "rev-parse", "HEAD"),
        })
    _git(source, "checkout", "-q", "main")

    repo = _seed_team(tmp_path / "team-runtime")

    def seed_git(state: TeamState) -> None:
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd", base_branch="main",
        )
        state.tasks["task"].git_base_branch = "main"
        if assignment_mode == "role":
            state.tasks["task"].assignment_mode = "role"
            state.tasks["task"].assignment_role_id = "project__backend"
            state.tasks["task"].owner_agent_id = None
        if pinned_first_attempt:
            state.tasks["task"].git_base_commit_sha = base_commit
            from evander_team_addon.models import GitBaselineConstraints
            state.tasks["task"].git_constraints = GitBaselineConstraints(
                intent="required", required_base_commit_sha=base_commit,
            )
        if assignment_mode == "agent":
            state.agent_runtimes["backend"].active_dispatch_id = "dispatch-first"

    repo.update(seed_git)
    runtime = TeamRuntimeService(repo)

    def claim_role_task() -> str:
        state, leases = runtime.lease_due_agents(
            agent_id="backend", defer_activation=True, respect_wake_interval=False,
        )
        [lease] = [item for item in leases if "task" in item["dispatch"]["task_ids"]]
        assert state.tasks["task"].owner_agent_id == "backend"
        return lease["dispatch_id"]

    first_dispatch = claim_role_task() if assignment_mode == "role" else "dispatch-first"
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "worker-runtime",
        worker_root=tmp_path / "worker-root",
        allowed_git_source_prefixes=[str(tmp_path)],
        container_runtime=SimpleNamespace(),
    )
    daemon.clone_repo(
        "session-backend", WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    requests: list[WorkerTaskWorktreeRequest] = []

    def prepare(*, session_id: str, **kwargs):
        request = WorkerTaskWorktreeRequest(**kwargs)
        requests.append(request)
        try:
            return daemon.prepare_task_worktree(session_id, request)
        except WorkerDependencyCompositionConflict as exc:
            raise ContainerWorkerClientError(
                str(exc), status_code=409,
                payload={"dependency_composition_conflict": exc.details},
            ) from exc

    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.container_backend",
        lambda *_args: SimpleNamespace(prepare_task_worktree=prepare),
    )
    monkeypatch.setattr(
        "evander_team_addon.provider_git_contexts._project_repo_task_dependency_git_sources",
        lambda *_args, **_kwargs: sources,
    )
    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(repo.runtime_dir)},
        admin_service=SimpleNamespace(managed_git_admin=None),
    )

    def prepare_task(dispatch_id: str):
        repo.update(lambda state: setattr(
            state.agent_runtimes["backend"], "active_dispatch_id", dispatch_id,
        ))
        state = repo.read()
        return _create_project_repo_git_work_contexts(
            context, repo, state=state, agent_id="backend", session_id="session-backend",
            project_id="project", dispatch_id=dispatch_id,
            project_repo={
                "managed_repo_id": "mr_0123456789abcdefabcd",
                "guest_repo_name": "repo", "target_dir": "/home/agent/repo",
                "execution_backend": "container_worker_vm",
            },
            tasks=[state.tasks["task"].model_dump(mode="json")], base_branch="main",
        )

    with pytest.raises(DependencyGitPreparationError) as failure:
        prepare_task(first_dispatch)
    assert failure.value.conflict is not None
    assert failure.value.conflict["conflict_paths"] == ["shared.txt"]
    [original_attempt] = repo.read().git_preparation_attempts.values()
    worktrees = (
        tmp_path / "worker-root/sessions/session-backend/agent_home/worktrees/repo"
    )
    original_checkout = worktrees / requests[0].worktree_name
    assert _git(original_checkout, "rev-parse", "HEAD") == base_commit
    assert _git(original_checkout, "status", "--porcelain") == ""
    assert original_attempt.state == TeamGitPreparationAttemptState.PENDING
    # Operator-authored material left after a failed attempt must survive recovery.
    (original_checkout / "operator-notes.txt").write_text("Preserve this checkout.\n")
    if assignment_mode == "role":
        state = runtime.fail_agent_dispatch(
            agent_id="backend", dispatch_id=first_dispatch, error=str(failure.value),
            delivery_not_started=True,
        )
        assert state.tasks["task"].owner_agent_id is None
    _record_dependency_git_preparation_blocker(
        repo, lease={"agent": {"id": "backend"}}, task_id="task",
        error=str(failure.value), conflict=failure.value.conflict,
    )

    recovery_branch = "agents/resolver/tasks/recovery"
    _git(source, "checkout", "-q", "-b", recovery_branch, sources[0]["commit_sha"])
    merge = subprocess.run(
        ["git", "merge", "--no-ff", "--no-edit", sources[1]["commit_sha"]],
        cwd=source, capture_output=True, text=True, timeout=15,
    )
    assert merge.returncode == 1
    (source / "shared.txt").write_text("resolved\n")
    _git(source, "add", "shared.txt")
    _git(source, "commit", "-qm", "Resolve dependencies")
    resolved_commit = _git(source, "rev-parse", "HEAD")

    def accept_recovery(state: TeamState) -> None:
        [recovery] = state.dependency_composition_recoveries.values()
        task = AgentTask(
            id="recovery-task", owner_agent_id="backend", project_id="project",
            priority=1, title="Resolve dependencies", description="Reviewed recovery",
            parent_task_id="task", created_by_actor="system:dependency-composition-recovery",
            state="finished", deliverable_ids=["recovery-deliverable"],
        )
        deliverable = Deliverable(
            id="recovery-deliverable", task_id=task.id, producer_agent_id="backend",
            summary="Reviewed dependency merge", state="accepted",
            git_refs=DeliverableGitRefs(
                agent_branch=recovery_branch, commit_sha=resolved_commit,
            ),
        )
        state.tasks[task.id] = task
        state.deliverables[deliverable.id] = deliverable
        recovery.recovery_task_id = task.id
        recovery.state = DependencyCompositionRecoveryState.IN_PROGRESS
        recovery.verified_deliverable_id = deliverable.id
        recovery.verified_commit_sha = resolved_commit
        recovery.verified_at = datetime.now(UTC)
        accept_dependency_composition_recovery(
            state, task=task, deliverable=deliverable, accepted_by_agent_id="lead",
            dispatch_id=None, result={}, now=datetime.now(UTC),
        )

    repo.update(accept_recovery)
    if pinned_first_attempt:
        assert repo.read().tasks["task"].git_base_commit_sha == base_commit
    recovery_dispatch = (
        claim_role_task() if assignment_mode == "role" else "dispatch-recovery"
    )
    original_create_context = TeamAdminService.create_git_work_context_with_id
    conflicts_remaining = 3

    def create_context(self, **kwargs):
        nonlocal conflicts_remaining
        if conflicts_remaining:
            conflicts_remaining -= 1
            raise TeamStateConflictError(expected_revision=1, current_revision=2)
        return original_create_context(self, **kwargs)

    monkeypatch.setattr(TeamAdminService, "create_git_work_context_with_id", create_context)
    with pytest.raises(TeamStateConflictError):
        prepare_task(recovery_dispatch)
    first, adopted = requests
    assert repo.read().git_preparation_attempts[adopted.preparation_attempt_id].state == (
        TeamGitPreparationAttemptState.PREPARED
    )
    # The worker succeeded, but committing the Team context failed. Replay its
    # exact receipt and request before permitting any agent to use the checkout.
    state, rows, _ = prepare_task(recovery_dispatch)
    assert len(rows) == 1
    assert requests[-1] == adopted
    assert adopted.branch != first.branch
    assert adopted.worktree_name != first.worktree_name
    assert adopted.preparation_attempt_id != first.preparation_attempt_id
    assert state.git_preparation_attempts[original_attempt.id].state == (
        TeamGitPreparationAttemptState.OPERATOR_ACTION_REQUIRED
    )
    checkout = worktrees / adopted.worktree_name
    assert _git(checkout, "rev-parse", "HEAD") == resolved_commit
    assert (checkout / "shared.txt").read_text() == "resolved\n"
    [work_context] = state.git_work_contexts.values()
    assert work_context.base_commit == resolved_commit
    assert work_context.agent_branch == adopted.branch
    assert work_context.dependency_composition_recovery_id is not None
    if assignment_mode == "role":
        state, _ = runtime.activate_delivered_agent_dispatch(dispatch_id=recovery_dispatch)
        assert state.tasks["task"].assignment_started_at is not None

    def start_task(state: TeamState) -> None:
        state.tasks["task"].state = TeamTaskState.IN_PROGRESS
        state.git_work_contexts[work_context.id].state = GitWorkContextState.ABANDONED

    repo.update(start_task)

    (checkout / "implementation.txt").write_text("Agent implementation.\n")
    _git(checkout, "add", "implementation.txt")
    _git(checkout, "commit", "-qm", "Continue recovered task")
    authored_commit = _git(checkout, "rev-parse", "HEAD")
    (checkout / "shared.txt").write_text("Uncommitted agent work.\n")
    state, rows, _ = prepare_task("dispatch-retry")
    replay = requests[-1]
    assert replay.branch == adopted.branch
    assert replay.worktree_name == adopted.worktree_name
    assert replay.dependency_composition_mode == "automatic"
    assert len(state.git_preparation_attempts) == 2
    assert all(
        item.base_commit == resolved_commit
        and item.dependency_composition_recovery_id == work_context.dependency_composition_recovery_id
        for item in state.git_work_contexts.values()
    )
    assert _git(checkout, "rev-parse", "HEAD") == authored_commit
    assert (checkout / "shared.txt").read_text() == "Uncommitted agent work.\n"
    assert _git(original_checkout, "rev-parse", "HEAD") == base_commit
    assert (original_checkout / "operator-notes.txt").read_text() == "Preserve this checkout.\n"


def test_explicit_contribution_contract_prepares_fresh_worktree_after_inputs_accumulate(
    tmp_path, monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Recovery Test")
    (source / "base.txt").write_text("base\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Base")
    base = _git(source, "rev-parse", "HEAD")
    contributions = []
    for name in ("first", "second"):
        branch = f"agents/source/{name}"
        _git(source, "checkout", "-q", "-b", branch, "main")
        (source / f"{name}.txt").write_text(f"{name}\n")
        _git(source, "add", ".")
        _git(source, "commit", "-qm", name)
        contributions.append({
            "dependency_task_id": f"task-{name}", "deliverable_id": f"deliverable-{name}",
            "branch": branch, "commit_sha": _git(source, "rev-parse", "HEAD"),
            "pinned_ref": None, "relationship_kind": "required_contribution",
        })
    _git(source, "checkout", "-q", "main")
    repo = _seed_team(tmp_path / "team-runtime")

    def seed_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd", base_branch="main",
        )
        task = state.tasks["task"]
        task.git_base_branch = "main"
        # This fixture models a legacy boundary whose contract is expanded
        # after accepted children arrive; new boundaries must be fresh tasks.
        task.integration_boundary = True
        task.deliverable_contract = TaskDeliverableContract(
            require_git_provenance=True,
        )

    repo.update(seed_git)
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "worker-runtime", worker_root=tmp_path / "worker-root",
        allowed_git_source_prefixes=[str(tmp_path)], container_runtime=SimpleNamespace(),
    )
    daemon.clone_repo(
        "session-backend", WorkerRepoCloneRequest(repo_source=str(source), repo_name="repo"),
    )
    requests = []

    def prepare(*, session_id, **kwargs):
        request = WorkerTaskWorktreeRequest(**kwargs)
        requests.append(request)
        return daemon.prepare_task_worktree(session_id, request)

    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.container_backend",
        lambda *_args: SimpleNamespace(prepare_task_worktree=prepare),
    )
    sources = []
    monkeypatch.setattr(
        "evander_team_addon.provider_git_contexts._project_repo_task_dependency_git_sources",
        lambda *_args, **_kwargs: list(sources),
    )
    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(repo.runtime_dir)},
        admin_service=SimpleNamespace(managed_git_admin=None),
    )

    def prepare_task(dispatch_id):
        repo.update(lambda state: setattr(
            state.agent_runtimes["backend"], "active_dispatch_id", dispatch_id,
        ))
        state = repo.read()
        return _create_project_repo_git_work_contexts(
            context, repo, state=state, agent_id="backend", session_id="session-backend",
            project_id="project", dispatch_id=dispatch_id,
            project_repo={
                "managed_repo_id": "mr_0123456789abcdefabcd", "guest_repo_name": "repo",
                "target_dir": "/home/agent/repo", "execution_backend": "container_worker_vm",
            },
            tasks=[state.tasks["task"].model_dump(mode="json")], base_branch="main",
        )

    prepare_task("dispatch-empty")
    sources.append(contributions[0])
    prepare_task("dispatch-first-child")
    checkout = (
        tmp_path / "worker-root/sessions/session-backend/agent_home/worktrees/repo"
        / requests[-1].worktree_name
    )
    assert _git(checkout, "rev-parse", "HEAD") == contributions[0]["commit_sha"]
    sources.append(contributions[1])
    with pytest.raises(DependencyGitPreparationError, match="provenance is ambiguous"):
        prepare_task("dispatch-expanded-implicit-inputs")
    original_checkout = checkout
    (original_checkout / "operator-notes.txt").write_text("Preserve prior preparation.\n")

    def record_accepted_children(state):
        for contribution in contributions:
            state.tasks[contribution["dependency_task_id"]] = AgentTask(
                id=contribution["dependency_task_id"], owner_agent_id="backend",
                project_id="project", priority=1, title="Accepted child",
                description="Completed contribution", state=TeamTaskState.FINISHED,
                deliverable_ids=[contribution["deliverable_id"]],
                created_by_agent_id="lead",
                parent_task_id="task",
            )
            state.deliverables[contribution["deliverable_id"]] = Deliverable(
                id=contribution["deliverable_id"], task_id=contribution["dependency_task_id"],
                producer_agent_id="backend", summary="Accepted contribution", state="accepted",
                git_refs=DeliverableGitRefs(
                    agent_branch=contribution["branch"], commit_sha=contribution["commit_sha"],
                ),
            )
    repo.update(record_accepted_children)
    TeamAdminService(repo).update_task(
        task_id="task",
        integration_boundary=True,
        deliverable_contract={
            "require_git_provenance": True,
            "required_contribution_task_ids": [c["dependency_task_id"] for c in contributions],
        },
        expected_revision=repo.read().revision,
    )
    state, rows, _ = prepare_task("dispatch-second-child")
    [row] = rows
    assert len({request.worktree_name for request in requests}) == 2
    assert len(state.git_preparation_attempts) == 2
    checkout = original_checkout.parent / requests[-1].worktree_name
    for contribution in contributions:
        _git(checkout, "merge-base", "--is-ancestor", contribution["commit_sha"], "HEAD")
    composed = _git(checkout, "rev-parse", "HEAD")
    assert state.git_work_contexts[row["branch_context_id"]].base_commit == composed
    assert {c.base_commit for c in state.git_work_contexts.values()} == {
        base, contributions[0]["commit_sha"], composed,
    }
    (checkout / "implementation.txt").write_text("Preserved agent work.\n")
    prepare_task("dispatch-repeat")
    assert _git(checkout, "rev-parse", "HEAD") == composed
    assert (checkout / "implementation.txt").read_text() == "Preserved agent work.\n"
    assert _git(original_checkout, "rev-parse", "HEAD") == contributions[0]["commit_sha"]
    assert (original_checkout / "operator-notes.txt").read_text() == "Preserve prior preparation.\n"


def test_preparation_attempt_rejects_changed_worktree_path(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    arguments = dict(
        attempt_id="gitprep-test", agent_id="backend", session_id="session-backend",
        task_id="task", project_id="project", dispatch_id="dispatch-first",
        managed_repo_id="mr_0123456789abcdefabcd", directory_path="root",
        execution_backend="container_worker_vm", branch="agents/session-backend/tasks/task",
        base_ref="main", preparation_generation="a" * 20,
    )
    original_path = "/home/agent/worktrees/repo/original"
    _ensure_project_repo_git_preparation_attempt(repo, worktree_path=original_path, **arguments)
    before = repo.read()
    with pytest.raises(RuntimeError, match="different identity"):
        _ensure_project_repo_git_preparation_attempt(
            repo, worktree_path="/home/agent/worktrees/repo/changed", **arguments,
        )
    after = repo.read()
    assert after.revision == before.revision
    assert after.git_preparation_attempts == before.git_preparation_attempts
