from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from evander.effects.providers.base import EffectRuntimeContext
from evander_team_addon.models import (
    EphemeralAgentPolicy,
    GitWorkContext,
    GitWorkContextState,
    ProjectGitConfig,
    ProjectGoal,
    TeamDispatch,
    TeamDispatchStatus,
    TeamState,
    TeamTaskState,
    WorktreeCleanupCandidate,
    WorktreeCleanupState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.providers import _prepare_project_repo_task_worktree
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.git_work_contexts import (
    reconcile_git_work_context_lifecycle,
)
from evander_team_addon.services.worktree_cleanup_service import (
    EffectTaskWorktreeCleanupBackend,
    TeamWorktreeCleanupService,
)


_MANAGED_REPO_ID = "mr_11111111111111111111"
_BASE_COMMIT = "1" * 40
_WORKTREE_PATH = "/home/agent/worktrees/project/task-12345678"


class FakeCleanupBackend:
    def __init__(self, outcomes: list[dict[str, Any]]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[WorktreeCleanupCandidate, bool]] = []

    def execution_backend_for_session(
        self,
        *,
        session_id: str,
        directory_path: str,
    ) -> str:
        assert session_id == "session-backend"
        assert directory_path == "root"
        return "container_worker_vm"

    def cleanup(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        self.calls.append((candidate.model_copy(deep=True), dry_run))
        return self.outcomes.pop(0)


def _seed_cleanup_state(tmp_path) -> TeamStateRepository:
    repository = TeamStateRepository(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        git=ProjectGitConfig(managed_repo_id=_MANAGED_REPO_ID),
        expected_revision=1,
    )
    admin.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    admin.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=0),
        expected_revision=3,
    )
    admin.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=4,
    )

    def _finish_with_checkout(state: TeamState) -> TeamState:
        task = state.tasks["task"]
        task.state = TeamTaskState.FINISHED
        task.state_entered_at = datetime(2026, 7, 25, tzinfo=UTC)
        task.updated_at = task.state_entered_at
        state.git_work_contexts["context"] = GitWorkContext(
            id="context",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=task.state_entered_at,
            updated_at=task.state_entered_at,
        )
        return state

    repository.update(_finish_with_checkout, expected_revision=5)
    return repository


def _removed_outcome(*, reclaimed_bytes: int = 512) -> dict[str, Any]:
    return {
        "state": "removed",
        "safe_to_remove": True,
        "removed": True,
        "pruned": True,
        "validation_count": 2,
        "failed_check": None,
        "message": "task worktree removed and registration pruned",
        "ignored_paths": [".cache/generated.bin"],
        "ignored_bytes": 64,
        "worktree_bytes": reclaimed_bytes,
        "reclaimed_bytes": reclaimed_bytes,
    }


def test_reconciler_enqueues_removes_audits_and_only_then_prunes_context(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)

    state, counts = service.enqueue_candidates(now=now, grace_seconds=0)
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.expected_head == _BASE_COMMIT
    assert candidate.worktree_path == _WORKTREE_PATH

    # Generic metadata retention cannot run ahead of physical cleanup.
    def _try_metadata_prune(current: TeamState) -> TeamState:
        reconcile_git_work_context_lifecycle(
            current,
            now=now,
            prune_before=now + timedelta(days=1),
        )
        return current

    state = repository.update(_try_metadata_prune)
    assert "context" in state.git_work_contexts

    result = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    assert result.reclaimed_bytes == 512
    assert len(backend.calls) == 1
    final_state = repository.read()
    candidate = next(iter(final_state.worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.SUCCEEDED
    assert candidate.ignored_file_inventory == [".cache/generated.bin"]
    assert candidate.ignored_bytes == 64
    assert candidate.worktree_bytes == 512
    assert candidate.reclaimed_bytes == 512
    assert candidate.completed_at == now
    assert "context" not in final_state.git_work_contexts
    audit = repository.list_dispatch_activity(tool_name="team_worktree_cleanup")
    assert len(audit) == 1
    assert audit[0].audit_record_id == candidate.id
    assert audit[0].status.value == "success"
    assert _WORKTREE_PATH in (audit[0].correction_context_summary or "")


def test_backend_refusal_is_durable_recovery_and_retry_is_idempotent(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend(
        [
            {
                "state": "refused",
                "safe_to_remove": False,
                "removed": False,
                "pruned": False,
                "validation_count": 2,
                "failed_check": "tracked_changes",
                "message": "tracked changes prevent task worktree cleanup",
                "ignored_paths": [".cache/generated.bin"],
                "ignored_bytes": 64,
                "worktree_bytes": 256,
                "reclaimed_bytes": 0,
            },
            _removed_outcome(reclaimed_bytes=128),
        ]
    )
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)

    first = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
    )

    assert first.recovery_required_count == 1
    failed = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert failed.state == WorktreeCleanupState.RECOVERY_REQUIRED
    assert failed.last_safety_check == "tracked_changes"
    assert failed.last_error == "tracked changes prevent task worktree cleanup"
    assert "context" in repository.read().git_work_contexts

    second = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
    )

    assert second.succeeded_count == 1
    succeeded = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert succeeded.state == WorktreeCleanupState.SUCCEEDED
    assert succeeded.attempt_count == 2
    assert len(repository.list_dispatch_activity(tool_name="team_worktree_cleanup")) == 2


def test_active_dispatch_defers_cleanup_without_calling_backend(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    service.enqueue_candidates(now=now, grace_seconds=0)

    def _add_dispatch(state: TeamState) -> TeamState:
        state.dispatches["dispatch"] = TeamDispatch(
            id="dispatch",
            agent_id="backend",
            session_id="session-backend",
            cause_kind="task",
            cause_id="task",
            task_ids=["task"],
            project_ids=["project"],
            managed_repo_ids=[_MANAGED_REPO_ID],
            status=TeamDispatchStatus.PREPARING,
            created_at=now,
        )
        return state

    repository.update(_add_dispatch)
    result = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=60,
    )

    assert result.deferred_count == 1
    assert backend.calls == []
    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.last_safety_check == "active_dispatch_reference"
    assert candidate.eligible_at == now + timedelta(seconds=60)


def test_reassignment_enqueues_the_previous_owner_checkout(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)

    def _reopen_for_new_owner(state: TeamState) -> TeamState:
        task = state.tasks["task"]
        task.state = TeamTaskState.IN_PROGRESS
        task.owner_agent_id = "lead"
        task.updated_at = datetime(2026, 7, 25, 11, tzinfo=UTC)
        return state

    repository.update(_reopen_for_new_owner)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    state, counts = TeamWorktreeCleanupService(
        repository,
        backend,
    ).enqueue_candidates(now=now, grace_seconds=0)

    assert counts["enqueued"] == 1
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.reason == "task_reassigned"
    assert candidate.agent_id == "backend"
    assert candidate.session_id == "session-backend"


def test_newer_checkout_generation_reopens_succeeded_candidate(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend(
        [
            _removed_outcome(reclaimed_bytes=128),
            _removed_outcome(reclaimed_bytes=256),
        ]
    )
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    assert service.reconcile(now=now, grace_seconds=0).succeeded_count == 1

    def _add_new_generation(state: TeamState) -> TeamState:
        state.git_work_contexts["context-next"] = GitWorkContext(
            id="context-next",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=now + timedelta(minutes=1),
            updated_at=now + timedelta(minutes=1),
        )
        return state

    repository.update(_add_new_generation)
    next_now = now + timedelta(minutes=2)
    state, counts = service.enqueue_candidates(now=next_now, grace_seconds=0)

    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert counts["refreshed"] == 1
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.completed_at is None
    assert candidate.reclaimed_bytes == 0
    assert service.reconcile(now=next_now, grace_seconds=0).succeeded_count == 1
    assert len(backend.calls) == 2


def test_effect_backend_delegates_container_cleanup_with_exact_expectations(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeContainerBackend:
        def cleanup_task_worktree(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "state": "already_absent",
                    "safe_to_remove": True,
                    "removed": False,
                    "pruned": True,
                    "validation_count": 2,
                    "failed_check": None,
                    "message": "already absent",
                    "ignored_paths": [],
                    "reclaimed_bytes": 0,
                }
            )

    monkeypatch.setattr(
        "evander_team_addon.services.worktree_cleanup_service."
        "selected_execution_backend_for_target",
        lambda *_args, **_kwargs: ("container_worker_vm", SimpleNamespace()),
    )
    monkeypatch.setattr(
        "evander_team_addon.services.worktree_cleanup_service.container_backend",
        lambda *_args, **_kwargs: FakeContainerBackend(),
    )
    candidate = WorktreeCleanupCandidate(
        id="candidate",
        agent_id="backend",
        session_id="session-backend",
        task_id="task",
        project_id="project",
        managed_repo_id=_MANAGED_REPO_ID,
        execution_backend="container_worker_vm",
        worktree_path=_WORKTREE_PATH,
        branch="agents/session-backend/tasks/task-12345678",
        expected_head=_BASE_COMMIT,
        reason="task_resolved",
    )

    result = EffectTaskWorktreeCleanupBackend(
        SimpleNamespace(metadata={"directory_path": "root"})
    ).cleanup(candidate, dry_run=True)

    assert result["state"] == "already_absent"
    assert calls == [
        {
            "session_id": "session-backend",
            "repo_name": "project",
            "worktree_name": "task-12345678",
            "branch": "agents/session-backend/tasks/task-12345678",
            "expected_commit_sha": _BASE_COMMIT,
            "expected_target_dir": _WORKTREE_PATH,
            "dry_run": True,
        }
    ]


def test_libvirt_preparation_uses_cleanup_lock_and_records_base_commit(tmp_path) -> None:
    calls: list[dict[str, Any]] = []

    class FakeVmExecutor:
        def vm_exec(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "success": True,
                "stdout": f"{_BASE_COMMIT}\n",
                "stderr": "",
                "exit_code": 0,
                "message": "prepared",
            }

    class FakeSessionLookup:
        def read_all_sessions(self) -> dict[str, Any]:
            return {
                "session-backend": SimpleNamespace(
                    session_id="session-backend",
                    vm_id="vm-backend",
                    vm_provider="libvirt",
                    directory_path="root",
                    metadata={},
                )
            }

        def get_session(self, session_id: str) -> Any:
            return self.read_all_sessions().get(session_id)

    path, base_commit = _prepare_project_repo_task_worktree(
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            vm_executor=FakeVmExecutor(),
            session_lookup=FakeSessionLookup(),
        ),
        project_repo={
            "guest_repo_name": "project",
            "target_dir": "/home/agent/repos/project",
            "execution_backend": "libvirt_vm",
        },
        session_id="session-backend",
        task_id="task",
        agent_branch="agents/session-backend/tasks/task-12345678",
        base_ref="main",
    )

    task_hash = hashlib.sha256(b"task").hexdigest()[:8]
    assert path == f"/home/agent/worktrees/project/task-{task_hash}"
    assert base_commit == _BASE_COMMIT
    command = str(calls[0]["command"])
    assert "/home/agent/.evander/locks/task-worktree-project.lock" in command
    assert 'exec 9>"$lock"' in command
    assert "flock 9" in command
    assert 'git -C "$target" rev-parse HEAD' in command
