from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander_team_addon.models import (
    EphemeralAgentPolicy,
    GitWorkContext,
    GitWorkContextState,
    ProjectGitConfig,
    ProjectGitIdentity,
    ProjectGoal,
    TaskCancellation,
    TeamDispatch,
    TeamDispatchActivityStatus,
    TeamDispatchStatus,
    TeamGitPreparationAttempt,
    TeamGitPreparationAttemptState,
    TeamState,
    TeamTaskState,
    WorktreeCleanupCandidate,
    WorktreeCleanupState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.providers import (
    _prepare_project_repo_task_worktree,
    _project_repo_agent_branch,
    _prior_project_repo_task_base_commit,
)
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.git_work_contexts import (
    reconcile_git_work_context_lifecycle,
)
from evander_team_addon.services.domain.preparation_failures import (
    task_git_work_context_generation,
)
from evander_team_addon.services.worktree_cleanup_service import (
    DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS,
    DEFAULT_CLEANUP_MAX_RETRY_SECONDS,
    EffectTaskWorktreeCleanupBackend,
    TeamWorktreeCleanupService,
)


_MANAGED_REPO_ID = "mr_11111111111111111111"
_BASE_COMMIT = "1" * 40
_WORKTREE_PATH = "/home/agent/worktrees/project/task-12345678"


class FakeCleanupBackend:
    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
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
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _seed_cleanup_state(
    tmp_path,
    *,
    task_state: TeamTaskState = TeamTaskState.FINISHED,
) -> TeamStateRepository:
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
        git=ProjectGitConfig(
            managed_repo_id=_MANAGED_REPO_ID,
            base_branch="main",
        ),
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
        task.state = task_state
        task.state_entered_at = datetime(2026, 7, 25, tzinfo=UTC)
        task.updated_at = task.state_entered_at
        if task_state == TeamTaskState.CANCELLED:
            task.cancellation = TaskCancellation(
                reason_code="operator_cancelled",
                reason="Task no longer required.",
                actor_kind="operator",
                actor_id="operator",
                cancelled_at=task.state_entered_at,
            )
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


def _dry_run_outcome(*, worktree_bytes: int = 512) -> dict[str, Any]:
    return {
        "state": "dry_run",
        "safe_to_remove": True,
        "removed": False,
        "pruned": False,
        "validation_count": 2,
        "failed_check": None,
        "message": "task worktree dry-run passed",
        "ignored_paths": [".cache/generated.bin"],
        "ignored_bytes": 64,
        "worktree_bytes": worktree_bytes,
        "reclaimed_bytes": 0,
    }


def _add_second_session_context_with_same_path(state: TeamState) -> TeamState:
    timestamp = datetime(2026, 8, 24, 11, tzinfo=UTC)
    state.dispatches["dispatch-second-session"] = TeamDispatch(
        id="dispatch-second-session",
        agent_id="backend",
        session_id="session-backend-second",
        cause_kind="scheduled",
        task_ids=["task"],
        project_ids=["project"],
        managed_repo_ids=[_MANAGED_REPO_ID],
        status=TeamDispatchStatus.COMPLETED,
        created_at=timestamp,
        completed_at=timestamp,
    )
    state.git_work_contexts["context-second-session"] = GitWorkContext(
        id="context-second-session",
        agent_id="backend",
        task_id="task",
        agent_branch="agents/session-backend-second/tasks/task-12345678",
        base_branch="main",
        base_commit=_BASE_COMMIT,
        dispatch_id="dispatch-second-session",
        worktree_path=_WORKTREE_PATH,
        state=GitWorkContextState.ACTIVE,
        created_at=timestamp,
        updated_at=timestamp,
    )
    return state


def test_cleanup_accepts_invalidated_uncommitted_preparation_receipt(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path, task_state=TeamTaskState.IN_PROGRESS)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = "gitprep-invalidated-receipt"
    worktree_path = "/home/agent/worktrees/project/task-invalidated"

    def _seed_attempt(state: TeamState) -> TeamState:
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-failed",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch="agents/session-backend/tasks/task-invalidated",
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.CLEANUP_PENDING,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            prepared_at=now,
            created_at=now,
            updated_at=now,
        )
        state.worktree_cleanup_candidates[f"cleanup-{attempt_id}"] = WorktreeCleanupCandidate(
            id=f"cleanup-{attempt_id}",
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            git_work_context_id=f"preparation:{attempt_id}",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch="agents/session-backend/tasks/task-invalidated",
            base_ref="main",
            expected_head=_BASE_COMMIT,
            retained_commit_sha=None,
            reason="preparation_invalidated",
            eligible_at=now,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_attempt)
    backend = FakeCleanupBackend([_removed_outcome()])

    result = TeamWorktreeCleanupService(repository, backend).reconcile(
        now=now,
        grace_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    assert len(backend.calls) == 1
    state = repository.read()
    assert attempt_id not in state.git_preparation_attempts
    assert state.worktree_cleanup_candidates[f"cleanup-{attempt_id}"].state == (
        WorktreeCleanupState.SUCCEEDED
    )


@pytest.mark.parametrize(
    ("task_state", "reassign", "expected_reason"),
    [
        (TeamTaskState.FINISHED, False, "task_resolved"),
        (TeamTaskState.CANCELLED, False, "task_resolved"),
        (TeamTaskState.IN_PROGRESS, True, "task_reassigned"),
    ],
)
def test_reconciler_discovers_uncommitted_preparation_without_same_owner_wake(
    tmp_path,
    task_state: TeamTaskState,
    reassign: bool,
    expected_reason: str,
) -> None:
    repository = _seed_cleanup_state(tmp_path, task_state=task_state)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = f"gitprep-{expected_reason}"
    worktree_path = f"/home/agent/worktrees/project/{expected_reason}"

    def _seed_uncommitted_attempt(state: TeamState) -> TeamState:
        state.git_work_contexts.clear()
        if reassign:
            state.tasks["task"].owner_agent_id = "lead"
        preparation_generation = task_git_work_context_generation(state, "task")
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-state-conflict",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch=f"agents/session-backend/tasks/{expected_reason}",
            base_ref="main",
            preparation_generation=preparation_generation,
            state=TeamGitPreparationAttemptState.PREPARED,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            prepared_at=now,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_uncommitted_attempt)
    backend = FakeCleanupBackend([_removed_outcome()])
    service = TeamWorktreeCleanupService(repository, backend)

    state, counts = service.enqueue_candidates(now=now, grace_seconds=0)

    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    attempt = state.git_preparation_attempts[attempt_id]
    assert attempt.state == TeamGitPreparationAttemptState.CLEANUP_PENDING
    candidate = state.worktree_cleanup_candidates[f"cleanup-{attempt_id}"]
    assert candidate.git_work_context_id == f"preparation:{attempt_id}"
    assert candidate.reason == expected_reason
    assert candidate.expected_head == _BASE_COMMIT

    result = service.reconcile(now=now, grace_seconds=0, dry_run=False)

    assert result.succeeded_count == 1
    assert len(backend.calls) == 1
    final_state = repository.read()
    assert attempt_id not in final_state.git_preparation_attempts
    assert final_state.worktree_cleanup_candidates[f"cleanup-{attempt_id}"].state == (
        WorktreeCleanupState.SUCCEEDED
    )


def test_reconciler_keeps_current_uncommitted_preparation_for_active_owner(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path, task_state=TeamTaskState.IN_PROGRESS)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = "gitprep-current-active-owner"

    def _seed_current_attempt(state: TeamState) -> TeamState:
        state.git_work_contexts.clear()
        task = state.tasks["task"]
        generation_state = state.model_copy(
            update={
                "tasks": {
                    **state.tasks,
                    "task": task.model_copy(update={"git_base_branch": "main"}, deep=True),
                }
            },
            deep=False,
        )
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-state-conflict",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path="/home/agent/worktrees/project/current-active-owner",
            branch="agents/session-backend/tasks/current-active-owner",
            base_ref="main",
            preparation_generation=task_git_work_context_generation(
                generation_state,
                "task",
            ),
            state=TeamGitPreparationAttemptState.PREPARED,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            prepared_at=now,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_current_attempt)
    backend = FakeCleanupBackend([_removed_outcome()])

    state, counts = TeamWorktreeCleanupService(repository, backend).enqueue_candidates(
        now=now,
        grace_seconds=0,
    )

    assert counts == {"enqueued": 0, "recovered": 0, "refreshed": 0}
    assert state.git_preparation_attempts[attempt_id].state == (
        TeamGitPreparationAttemptState.PREPARED
    )
    assert state.worktree_cleanup_candidates == {}


@pytest.mark.parametrize(
    ("task_state", "reassign", "expected_reason"),
    [
        (TeamTaskState.FINISHED, False, "task_resolved"),
        (TeamTaskState.IN_PROGRESS, True, "task_reassigned"),
    ],
)
def test_reconciler_cleans_interrupted_pending_preparation_from_worker_receipt(
    tmp_path,
    task_state: TeamTaskState,
    reassign: bool,
    expected_reason: str,
) -> None:
    repository = _seed_cleanup_state(tmp_path, task_state=task_state)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = f"gitprep-pending-{expected_reason}"

    def _seed_pending_attempt(state: TeamState) -> TeamState:
        state.git_work_contexts.clear()
        if reassign:
            state.tasks["task"].owner_agent_id = "lead"
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-interrupted",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=f"/home/agent/worktrees/project/pending-{expected_reason}",
            branch=f"agents/session-backend/tasks/pending-{expected_reason}",
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.PENDING,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_pending_attempt)
    backend = FakeCleanupBackend([_removed_outcome()])

    result = TeamWorktreeCleanupService(repository, backend).reconcile(
        now=now,
        grace_seconds=0,
        dry_run=False,
    )

    assert result.enqueued_count == 1
    assert result.attempted_count == 1
    assert result.succeeded_count == 1
    assert len(backend.calls) == 1
    assert backend.calls[0][0].expected_head is None
    state = repository.read()
    candidate = state.worktree_cleanup_candidates[f"cleanup-{attempt_id}"]
    assert candidate.reason == expected_reason
    assert candidate.last_safety_check == "passed"
    assert candidate.state == WorktreeCleanupState.SUCCEEDED
    assert attempt_id not in state.git_preparation_attempts


def test_reconciler_reactivates_terminal_candidate_for_reused_attempt_id(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path, task_state=TeamTaskState.IN_PROGRESS)
    completed_at = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = "gitprep-reused-after-cleanup"
    candidate_id = f"cleanup-{attempt_id}"
    worktree_path = "/home/agent/worktrees/project/reused-after-cleanup"
    branch = "agents/session-backend/tasks/reused-after-cleanup"
    new_head = "2" * 40

    def _seed_reused_attempt(state: TeamState) -> TeamState:
        state.git_work_contexts.clear()
        state.worktree_cleanup_candidates[candidate_id] = WorktreeCleanupCandidate(
            id=candidate_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            git_work_context_id=f"preparation:{attempt_id}",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch=branch,
            base_ref="main",
            expected_head=_BASE_COMMIT,
            state=WorktreeCleanupState.SUCCEEDED,
            reason="task_reassigned",
            attempt_count=3,
            created_at=completed_at - timedelta(hours=1),
            updated_at=completed_at,
            completed_at=completed_at,
        )
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-reused",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch=branch,
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.CLEANUP_PENDING,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=new_head,
            prepared_at=completed_at,
            created_at=completed_at,
            updated_at=completed_at,
        )
        return state

    repository.update(_seed_reused_attempt)
    backend = FakeCleanupBackend([_removed_outcome()])
    service = TeamWorktreeCleanupService(repository, backend)
    retry_at = completed_at + timedelta(seconds=2)

    state, counts = service.enqueue_candidates(now=retry_at, grace_seconds=0)

    assert counts == {"enqueued": 0, "recovered": 0, "refreshed": 1}
    candidate = state.worktree_cleanup_candidates[candidate_id]
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.expected_head == new_head
    assert candidate.completed_at is None
    assert candidate.attempt_count == 0

    result = service.reconcile(now=retry_at, grace_seconds=0, dry_run=False)

    assert result.succeeded_count == 1
    assert len(backend.calls) == 1
    final_state = repository.read()
    assert final_state.worktree_cleanup_candidates[candidate_id].state == (
        WorktreeCleanupState.SUCCEEDED
    )
    assert attempt_id not in final_state.git_preparation_attempts


@pytest.mark.parametrize("scoped", [False, True])
def test_reconciler_enqueues_removes_audits_and_only_then_prunes_context(
    tmp_path,
    scoped,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    if scoped:
        from evander_team_addon.lifecycle_scope import TeamLifecycleRepository

        repository = TeamLifecycleRepository(repository)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)

    state, counts = service.enqueue_candidates(now=now, grace_seconds=0)
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.expected_head == _BASE_COMMIT
    assert candidate.worktree_path == _WORKTREE_PATH
    assert candidate.directory_path == "root"

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
    candidate = final_state.worktree_cleanup_candidates[candidate.id]
    assert candidate.state == WorktreeCleanupState.SUCCEEDED
    assert candidate.ignored_file_inventory == [".cache/generated.bin"]
    assert candidate.ignored_file_count == 1
    assert candidate.ignored_file_inventory_sha256 is not None
    assert candidate.ignored_file_inventory_truncated is False
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


@pytest.mark.parametrize("scoped", [False, True])
def test_cleanup_retires_committed_attempt_before_pruning_its_context(tmp_path, scoped) -> None:
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = "gitprep-committed-context"

    def _seed_committed_attempt(state: TeamState) -> TeamState:
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-prepared",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=_WORKTREE_PATH,
            branch="agents/session-backend/tasks/task-12345678",
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.COMMITTED,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            git_work_context_id="context",
            prepared_at=now,
            committed_at=now,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_committed_attempt)
    base_repository = repository
    if scoped:
        from evander_team_addon.lifecycle_scope import TeamLifecycleRepository

        repository = TeamLifecycleRepository(repository)
    backend = FakeCleanupBackend([_removed_outcome()])

    result = TeamWorktreeCleanupService(repository, backend).reconcile(
        now=now,
        grace_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    assert len(backend.calls) == 1
    state = base_repository.read()
    assert "context" not in state.git_work_contexts
    assert attempt_id not in state.git_preparation_attempts
    [candidate] = state.worktree_cleanup_candidates.values()
    assert candidate.state == WorktreeCleanupState.SUCCEEDED


@pytest.mark.parametrize("scoped", [False, True])
def test_cleanup_retires_attempt_bound_to_older_context_in_cleaned_lineage(
    tmp_path, scoped
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = "gitprep-older-lineage-context"

    def _seed_multiple_dispatch_contexts(state: TeamState) -> TeamState:
        old_context = state.git_work_contexts.pop("context")
        old_context.id = "context-first-dispatch"
        old_context.created_at = now - timedelta(hours=2)
        old_context.updated_at = now - timedelta(hours=2)
        state.git_work_contexts[old_context.id] = old_context
        state.git_work_contexts["context-latest-dispatch"] = GitWorkContext(
            id="context-latest-dispatch",
            agent_id="backend",
            task_id="task",
            agent_branch=old_context.agent_branch,
            base_branch="main",
            base_commit=_BASE_COMMIT,
            commit_sha=_BASE_COMMIT,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-first",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=_WORKTREE_PATH,
            branch=old_context.agent_branch,
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.COMMITTED,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            git_work_context_id=old_context.id,
            prepared_at=now - timedelta(hours=2),
            committed_at=now - timedelta(hours=2),
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=2),
        )
        return state

    repository.update(_seed_multiple_dispatch_contexts)
    base_repository = repository
    if scoped:
        from evander_team_addon.lifecycle_scope import TeamLifecycleRepository

        repository = TeamLifecycleRepository(repository)
    service = TeamWorktreeCleanupService(
        repository,
        FakeCleanupBackend([_removed_outcome()]),
    )

    result = service.reconcile(now=now, grace_seconds=0, dry_run=False)

    assert result.succeeded_count == 1
    cleaned = base_repository.read()
    [candidate] = cleaned.worktree_cleanup_candidates.values()
    assert candidate.git_work_context_id == "context-latest-dispatch"
    assert attempt_id not in cleaned.git_preparation_attempts
    assert "context-first-dispatch" in cleaned.git_work_contexts

    def _apply_retention(state: TeamState) -> TeamState:
        reconcile_git_work_context_lifecycle(
            state,
            now=now + timedelta(days=1),
            prune_before=now + timedelta(days=1),
        )
        return state

    retained = repository.update(_apply_retention)
    assert "context-first-dispatch" not in retained.git_work_contexts


def test_scoped_cleanup_keeps_a_cleaned_newer_generation_as_lineage_authority(tmp_path):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository

    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, FakeCleanupBackend([]))
    state, _ = service.enqueue_candidates(now=now, grace_seconds=0)
    candidate = next(iter(state.worktree_cleanup_candidates.values()))

    def history(current):
        context = current.git_work_contexts["context"]
        current.git_work_contexts["older-generation"] = context.model_copy(
            update={
                "id": "older-generation",
                "created_at": context.created_at - timedelta(days=1),
                "updated_at": context.updated_at - timedelta(days=1),
            }
        )
        current.worktree_cleanup_candidates[candidate.id].state = WorktreeCleanupState.SUCCEEDED
        current.worktree_cleanup_candidates[candidate.id].completed_at = now

    repository.update(history)
    scoped = TeamLifecycleRepository(repository)
    revision = repository.read_revision()
    _, counts = TeamWorktreeCleanupService(scoped, FakeCleanupBackend([])).enqueue_candidates(
        now=now, grace_seconds=0
    )
    assert counts == {"enqueued": 0, "recovered": 0, "refreshed": 0}
    assert repository.read_revision() == revision
    assert {
        row.git_work_context_id for row in repository.read().worktree_cleanup_candidates.values()
    } == {"context"}


def test_scoped_retention_honors_resolved_recovery_incidents(tmp_path):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import GitRecoveryIncident, TeamBlocker
    from evander_team_addon.services.domain.git_recovery import preservation_hold

    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    state, _ = TeamWorktreeCleanupService(repository, FakeCleanupBackend([])).enqueue_candidates(
        now=now, grace_seconds=0
    )
    candidate = next(iter(state.worktree_cleanup_candidates.values()))

    def retain(current):
        current.worktree_cleanup_candidates[candidate.id].state = WorktreeCleanupState.SUCCEEDED
        current.blockers["recovered"] = TeamBlocker(
            id="recovered",
            team_id="team",
            project_id="project",
            task_id="task",
            source_kind="explicit",
            source_id="retained-work",
            summary="Recovery resolved",
            state="resolved",
            route="resolved",
            resolved_at=now,
        )
        current.git_recovery_incidents["incident"] = GitRecoveryIncident(
            id="incident",
            blocker_id="recovered",
            project_id="project",
            task_id="task",
            lineage_id="task",
            input_fingerprint="input",
            preparation_generation="generation",
            state="resumed",
            preserved_context_ids=["context"],
        )

    repository.update(retain)
    scoped = TeamLifecycleRepository(repository)
    with scoped.command_snapshot() as snapshot:
        assert not snapshot.git_recovery_incidents.values()
        assert preservation_hold(snapshot, context_id="context") == "incident"

    def prune(current):
        reconcile_git_work_context_lifecycle(current, now=now, prune_before=now + timedelta(days=1))

    scoped.update(prune)
    assert "context" in repository.read().git_work_contexts


def test_invalidated_preparation_is_cleanup_eligible_before_task_resolution(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(
        tmp_path,
        task_state=TeamTaskState.IN_PROGRESS,
    )

    def _mark_invalidated_generation(state: TeamState) -> TeamState:
        state.git_work_contexts["context"].preparation_generation = "0" * 20
        return state

    repository.update(_mark_invalidated_generation)
    backend = FakeCleanupBackend([_removed_outcome()])
    service = TeamWorktreeCleanupService(repository, backend)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)

    state, counts = service.enqueue_candidates(now=now, grace_seconds=0)

    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.reason == "preparation_invalidated"
    result = service.reconcile(now=now, grace_seconds=0, dry_run=False)
    assert result.succeeded_count == 1


def test_operator_requeue_restores_invalidated_preparation_cleanup_eligibility(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path, task_state=TeamTaskState.IN_PROGRESS)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = "gitprep-invalidated-requeue"
    candidate_id = f"cleanup-{attempt_id}"
    worktree_path = "/home/agent/worktrees/project/task-invalidated-requeue"

    def _seed_invalidated_attempt(state: TeamState) -> TeamState:
        state.git_work_contexts.clear()
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-invalidated",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch="agents/session-backend/tasks/task-invalidated-requeue",
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.CLEANUP_PENDING,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            prepared_at=now,
            created_at=now,
            updated_at=now,
        )
        state.worktree_cleanup_candidates[candidate_id] = WorktreeCleanupCandidate(
            id=candidate_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            git_work_context_id=f"preparation:{attempt_id}",
            managed_repo_id=_MANAGED_REPO_ID,
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch="agents/session-backend/tasks/task-invalidated-requeue",
            base_ref="main",
            expected_head=_BASE_COMMIT,
            reason="preparation_invalidated",
            eligible_at=now,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_invalidated_attempt)
    backend = FakeCleanupBackend(
        [
            {
                "state": "refused",
                "safe_to_remove": False,
                "failed_check": "tracked_changes",
                "message": "tracked changes prevent cleanup",
            },
            _removed_outcome(),
        ]
    )
    service = TeamWorktreeCleanupService(repository, backend)

    first = service.reconcile(now=now, grace_seconds=0, retry_seconds=0)

    assert first.operator_action_required_count == 1
    halted = repository.read()
    assert halted.worktree_cleanup_candidates[candidate_id].state == (
        WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
    )
    assert halted.git_preparation_attempts[attempt_id].state == (
        TeamGitPreparationAttemptState.OPERATOR_ACTION_REQUIRED
    )

    service.requeue_operator_action(
        candidate_id,
        requested_by="operator:admin",
        reason="The tracked edits were reviewed and committed.",
        now=now + timedelta(seconds=1),
        expected_revision=repository.read_revision(),
    )

    requeued = repository.read()
    assert requeued.git_preparation_attempts[attempt_id].state == (
        TeamGitPreparationAttemptState.CLEANUP_PENDING
    )
    second = service.reconcile(
        now=now + timedelta(seconds=1),
        grace_seconds=0,
        retry_seconds=0,
    )
    assert second.succeeded_count == 1
    assert len(backend.calls) == 2
    assert attempt_id not in repository.read().git_preparation_attempts


def test_cleanup_uses_enqueued_directory_after_project_scope_moves(
    tmp_path,
    monkeypatch,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    resolution_scopes: list[tuple[dict[str, Any], str]] = []

    class FakeContainerBackend:
        def cleanup_task_worktree(self, **_kwargs: Any) -> SimpleNamespace:
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
                    "ignored_bytes": 0,
                    "worktree_bytes": 0,
                    "reclaimed_bytes": 0,
                }
            )

    def _select_backend(_context, target, *, directory_path):
        resolution_scopes.append((dict(target), directory_path))
        return "container_worker_vm", SimpleNamespace()

    monkeypatch.setattr(
        "evander_team_addon.services.worktree_cleanup_service."
        "selected_execution_backend_for_target",
        _select_backend,
    )
    monkeypatch.setattr(
        "evander_team_addon.services.worktree_cleanup_service.container_backend",
        lambda *_args, **_kwargs: FakeContainerBackend(),
    )
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(
        repository,
        EffectTaskWorktreeCleanupBackend(
            SimpleNamespace(metadata={"directory_path": "root"}),
            repository=repository,
        ),
    )

    enqueued, counts = service.enqueue_candidates(now=now, grace_seconds=0)
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    candidate = next(iter(enqueued.worktree_cleanup_candidates.values()))
    assert candidate.directory_path == "root"

    moved_directory_path = "root/departments/accounting"
    TeamAdminService(repository).update_project(
        project_id="project",
        directory_path=moved_directory_path,
        set_directory_path=True,
        git=ProjectGitConfig(
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path=moved_directory_path,
            base_branch="main",
        ),
        set_git=True,
    )

    result = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    succeeded = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert succeeded.directory_path == "root"
    assert resolution_scopes == [
        ({"session_id": "session-backend"}, "root"),
        (
            {
                "session_id": "session-backend",
                "execution_backend": "container_worker_vm",
            },
            "root",
        ),
    ]


def test_project_scope_move_fails_before_cleanup_scope_is_frozen(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    moved_directory_path = "root/departments/accounting"

    with pytest.raises(
        ValueError,
        match=(
            "repository scope cannot change while Git work contexts lack frozen "
            "cleanup scope: context"
        ),
    ):
        TeamAdminService(repository).update_project(
            project_id="project",
            directory_path=moved_directory_path,
            set_directory_path=True,
            git=ProjectGitConfig(
                managed_repo_id=_MANAGED_REPO_ID,
                directory_path=moved_directory_path,
                base_branch="main",
            ),
            set_git=True,
        )

    state = repository.read()
    assert state.projects["project"].directory_path == "root"
    assert state.worktree_cleanup_candidates == {}


def test_historical_same_checkout_context_does_not_block_move_after_newest_is_frozen(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)

    def _add_historical_context(state: TeamState) -> TeamState:
        state.git_work_contexts["context-historical"] = GitWorkContext(
            id="context-historical",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=datetime(2026, 7, 24, tzinfo=UTC),
            updated_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
        return state

    repository.update(_add_historical_context)
    state, counts = TeamWorktreeCleanupService(
        repository,
        FakeCleanupBackend([]),
    ).enqueue_candidates(
        now=datetime(2026, 8, 24, 12, tzinfo=UTC),
        grace_seconds=0,
    )
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    assert candidate.git_work_context_id == "context"

    moved_directory_path = "root/departments/accounting"
    TeamAdminService(repository).update_project(
        project_id="project",
        directory_path=moved_directory_path,
        set_directory_path=True,
        git=ProjectGitConfig(
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path=moved_directory_path,
            base_branch="main",
        ),
        set_git=True,
    )

    assert repository.read().projects["project"].directory_path == moved_directory_path


def test_older_cross_project_context_does_not_block_move_after_global_newest_is_frozen(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_project(
        project_id="project-other",
        team_id="team",
        name="Other Project",
        lead_agent_id="lead",
        lead_session_id=None,
        goals=[ProjectGoal(id="goal-other", description="Ship other work")],
        git=ProjectGitConfig(
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            base_branch="main",
        ),
    )
    admin.create_or_reprioritize_task(
        task_id="other-task",
        owner_agent_id="backend",
        project_id="project-other",
        priority=1,
        title="Other task",
        description="Reuse the physical checkout in another project.",
        created_by_agent_id="lead",
    )

    now = datetime(2026, 8, 24, 12, tzinfo=UTC)

    def _finish_other_with_newer_colliding_context(state: TeamState) -> TeamState:
        other_task = state.tasks["other-task"]
        other_task.state = TeamTaskState.FINISHED
        other_task.state_entered_at = now
        other_task.updated_at = now
        state.git_work_contexts["context-other-project"] = GitWorkContext(
            id="context-other-project",
            agent_id="backend",
            task_id="other-task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_finish_other_with_newer_colliding_context)
    state, counts = TeamWorktreeCleanupService(
        repository,
        FakeCleanupBackend([]),
    ).enqueue_candidates(now=now + timedelta(minutes=1), grace_seconds=0)

    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    assert len(state.worktree_cleanup_candidates) == 1
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.project_id == "project-other"
    assert candidate.git_work_context_id == "context-other-project"

    moved_directory_path = "root/archived/original-project"
    admin.update_project(
        project_id="project",
        directory_path=moved_directory_path,
        set_directory_path=True,
        git=ProjectGitConfig(
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path=moved_directory_path,
            base_branch="main",
        ),
        set_git=True,
    )

    assert repository.read().projects["project"].directory_path == moved_directory_path


def test_project_repository_removal_fails_before_cleanup_scope_is_frozen(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)

    with pytest.raises(
        ValueError,
        match=(
            "repository scope cannot change while Git work contexts lack frozen "
            "cleanup scope: context"
        ),
    ):
        TeamAdminService(repository).update_project(
            project_id="project",
            git=None,
            set_git=True,
        )

    project = repository.read().projects["project"]
    assert project.git is not None
    assert project.git.managed_repo_id == _MANAGED_REPO_ID


def test_project_repository_swap_fails_before_cleanup_scope_is_frozen(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)

    with pytest.raises(
        ValueError,
        match=(
            "repository scope cannot change while Git work contexts lack frozen "
            "cleanup scope: context"
        ),
    ):
        TeamAdminService(repository).update_project(
            project_id="project",
            git=ProjectGitConfig(
                managed_repo_id="mr_22222222222222222222",
                directory_path="root",
                base_branch="main",
            ),
            set_git=True,
        )

    project = repository.read().projects["project"]
    assert project.git is not None
    assert project.git.managed_repo_id == _MANAGED_REPO_ID


def test_project_repository_swap_is_allowed_after_cleanup_scope_is_frozen(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, FakeCleanupBackend([]))
    enqueued, counts = service.enqueue_candidates(now=now, grace_seconds=0)
    candidate = next(iter(enqueued.worktree_cleanup_candidates.values()))
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}

    replacement_repo_id = "mr_22222222222222222222"
    TeamAdminService(repository).update_project(
        project_id="project",
        git=ProjectGitConfig(
            managed_repo_id=replacement_repo_id,
            directory_path="root",
            base_branch="main",
        ),
        set_git=True,
    )

    state = repository.read()
    assert state.projects["project"].git is not None
    assert state.projects["project"].git.managed_repo_id == replacement_repo_id
    assert state.worktree_cleanup_candidates[candidate.id].managed_repo_id == _MANAGED_REPO_ID
    assert state.worktree_cleanup_candidates[candidate.id].directory_path == "root"

    archived_directory_path = "root/archived/accounting"
    TeamAdminService(repository).update_project(
        project_id="project",
        directory_path=archived_directory_path,
        set_directory_path=True,
        git=None,
        set_git=True,
    )

    state = repository.read()
    assert state.projects["project"].directory_path == archived_directory_path
    assert state.projects["project"].git is None
    assert state.worktree_cleanup_candidates[candidate.id].managed_repo_id == _MANAGED_REPO_ID
    assert state.worktree_cleanup_candidates[candidate.id].directory_path == "root"


@pytest.mark.parametrize("binding_change", ["swap", "remove"])
def test_cleanup_reconciles_frozen_candidate_after_project_repository_change(
    tmp_path,
    binding_change: str,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    backend = FakeCleanupBackend([_removed_outcome()])
    service = TeamWorktreeCleanupService(repository, backend)
    enqueued, counts = service.enqueue_candidates(now=now, grace_seconds=0)
    candidate = next(iter(enqueued.worktree_cleanup_candidates.values()))
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}

    replacement_git = (
        ProjectGitConfig(
            managed_repo_id="mr_22222222222222222222",
            directory_path="root",
            base_branch="main",
        )
        if binding_change == "swap"
        else None
    )
    TeamAdminService(repository).update_project(
        project_id="project",
        git=replacement_git,
        set_git=True,
    )

    result = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    assert len(repository.read().worktree_cleanup_candidates) == 1
    assert len(backend.calls) == 1
    cleaned_candidate, dry_run = backend.calls[0]
    assert dry_run is False
    assert cleaned_candidate.id == candidate.id
    assert cleaned_candidate.managed_repo_id == _MANAGED_REPO_ID
    assert cleaned_candidate.directory_path == "root"


def test_cleanup_scope_and_pruning_distinguish_sessions_reusing_a_path(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    backend = FakeCleanupBackend([_removed_outcome()])
    service = TeamWorktreeCleanupService(repository, backend)
    service.enqueue_candidates(now=now, grace_seconds=0)
    repository.update(_add_second_session_context_with_same_path)

    with pytest.raises(
        ValueError,
        match=("work contexts lack frozen cleanup scope: context-second-session"),
    ):
        TeamAdminService(repository).update_project(
            project_id="project",
            directory_path="root/departments/accounting",
            set_directory_path=True,
            git=ProjectGitConfig(
                managed_repo_id=_MANAGED_REPO_ID,
                directory_path="root/departments/accounting",
                base_branch="main",
            ),
            set_git=True,
        )

    result = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    state = repository.read()
    assert "context" not in state.git_work_contexts
    assert "context-second-session" in state.git_work_contexts


def test_cross_task_active_context_blocks_same_physical_checkout_cleanup(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    backend = FakeCleanupBackend([_removed_outcome()])
    service = TeamWorktreeCleanupService(repository, backend)
    enqueued, counts = service.enqueue_candidates(now=now, grace_seconds=0)
    candidate = next(iter(enqueued.worktree_cleanup_candidates.values()))
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}

    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="other-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Other work",
        description="Exercise physical checkout collision safety.",
        created_by_agent_id="lead",
    )

    def _add_colliding_context(state: TeamState) -> TeamState:
        state.git_work_contexts["context-other-task"] = GitWorkContext(
            id="context-other-task",
            agent_id="backend",
            task_id="other-task",
            agent_branch=candidate.branch,
            base_branch="main",
            base_commit=_BASE_COMMIT,
            worktree_path=candidate.worktree_path.replace("/worktrees/", "/worktrees//"),
            state=GitWorkContextState.ACTIVE,
            created_at=now + timedelta(minutes=1),
            updated_at=now + timedelta(minutes=1),
        )
        return state

    repository.update(_add_colliding_context)
    after_collision, collision_counts = service.enqueue_candidates(
        now=now + timedelta(minutes=2),
        grace_seconds=0,
    )

    assert collision_counts == {"enqueued": 0, "recovered": 0, "refreshed": 0}
    assert len(after_collision.worktree_cleanup_candidates) == 1

    result = service.reconcile(
        now=now + timedelta(minutes=2),
        grace_seconds=0,
        retry_seconds=60,
        dry_run=False,
    )

    assert result.deferred_count == 1
    assert backend.calls == []
    deferred = repository.read().worktree_cleanup_candidates[candidate.id]
    assert deferred.last_safety_check == "active_git_context_reference"
    assert "context-other-task" in (deferred.last_error or "")


def test_cleanup_retains_bounded_deterministic_inventory_evidence(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    outcome = _removed_outcome()
    outcome["ignored_paths"] = [
        f"node_modules/cache/{index:04d}.bin" for index in range(250, -1, -1)
    ]
    outcome["ignored_paths"].append("node_modules/cache/0042.bin")
    backend = FakeCleanupBackend([outcome])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    result = TeamWorktreeCleanupService(repository, backend).reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.ignored_file_count == 251
    assert candidate.ignored_file_inventory == sorted(set(outcome["ignored_paths"]))[:200]
    assert candidate.ignored_file_inventory_sha256 is not None
    assert candidate.ignored_file_inventory_truncated is True


def test_automatic_cleanup_audits_then_removes_on_later_eligible_pass(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend(
        [
            _dry_run_outcome(worktree_bytes=1024),
            _removed_outcome(reclaimed_bytes=1024),
        ]
    )
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    # Keep journal retention on the same clock as the simulated cleanup passes.
    monkeypatch.setattr("evander_team_addon.sqlite_journal.utc_now", lambda: now)
    service = TeamWorktreeCleanupService(repository, backend)

    first = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=300,
        dry_run=None,
    )

    assert first.dry_run_count == 1
    assert first.succeeded_count == 0
    after_dry_run = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert after_dry_run.state == WorktreeCleanupState.PENDING
    assert after_dry_run.last_safety_check == "dry_run_safe"
    assert after_dry_run.eligible_at == now + timedelta(seconds=300)
    assert after_dry_run.worktree_bytes == 1024
    assert [dry_run for _candidate, dry_run in backend.calls] == [True]

    too_early = service.reconcile(
        now=now + timedelta(seconds=299),
        grace_seconds=0,
        retry_seconds=300,
        dry_run=None,
    )
    assert too_early.attempted_count == 0

    second = service.reconcile(
        now=now + timedelta(seconds=300),
        grace_seconds=0,
        retry_seconds=300,
        dry_run=None,
    )

    assert second.dry_run_count == 0
    assert second.succeeded_count == 1
    assert second.reclaimed_bytes == 1024
    succeeded = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert succeeded.state == WorktreeCleanupState.SUCCEEDED
    assert succeeded.attempt_count == 2
    assert [dry_run for _candidate, dry_run in backend.calls] == [True, False]
    audits = repository.list_dispatch_activity(tool_name="team_worktree_cleanup")
    assert len(audits) == 2
    assert all(audit.status == TeamDispatchActivityStatus.SUCCESS for audit in audits)


def test_explicit_dry_run_only_never_progresses_to_removal(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_dry_run_outcome(), _dry_run_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)

    first = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=300,
        dry_run=True,
    )
    second = service.reconcile(
        now=now + timedelta(seconds=300),
        grace_seconds=0,
        retry_seconds=300,
        dry_run=True,
    )

    assert first.dry_run_count == 1
    assert second.dry_run_count == 1
    assert [dry_run for _candidate, dry_run in backend.calls] == [True, True]
    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.last_safety_check == "dry_run_safe"


def test_dry_run_cannot_authorize_later_removal_without_its_audit(
    tmp_path,
    monkeypatch,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_dry_run_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    def _reject_audit(_connection, _activity):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        repository.store,
        "_insert_dispatch_activity",
        _reject_audit,
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        TeamWorktreeCleanupService(repository, backend).reconcile(
            now=now,
            grace_seconds=0,
            retry_seconds=300,
            dry_run=None,
        )

    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PROCESSING
    assert candidate.last_safety_check is None
    assert repository.list_dispatch_activity(tool_name="team_worktree_cleanup") == []


def test_cleanup_audit_failure_rolls_back_success_and_context_prune(
    tmp_path,
    monkeypatch,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)

    def _reject_audit(_connection, _activity):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        repository.store,
        "_insert_dispatch_activity",
        _reject_audit,
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.reconcile(now=now, grace_seconds=0)

    state = repository.read()
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PROCESSING
    assert candidate.completed_at is None
    assert "context" in state.git_work_contexts
    assert repository.list_dispatch_activity(tool_name="team_worktree_cleanup") == []


def test_cleanup_audit_failure_rolls_back_failure_disposition(
    tmp_path,
    monkeypatch,
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
                "failed_check": "retained_commit_mismatch",
                "message": "the retained commit is absent from the task branch",
            }
        ]
    )
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)

    def _reject_audit(_connection, _activity):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        repository.store,
        "_insert_dispatch_activity",
        _reject_audit,
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        TeamWorktreeCleanupService(repository, backend).reconcile(
            now=now,
            grace_seconds=0,
        )

    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PROCESSING
    assert candidate.last_safety_check is None
    assert candidate.last_error is None
    assert candidate.automatic_retry_count == 0
    assert candidate.operator_action_required_at is None
    assert repository.list_dispatch_activity(tool_name="team_worktree_cleanup") == []


def test_rejected_task_remains_unresolved_and_is_not_enqueued_for_worktree_cleanup(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(
        tmp_path,
        task_state=TeamTaskState.REJECTED,
    )
    service = TeamWorktreeCleanupService(
        repository,
        FakeCleanupBackend([_removed_outcome()]),
    )

    state, counts = service.enqueue_candidates(
        now=datetime(2026, 7, 25, 12, tzinfo=UTC),
        grace_seconds=0,
    )

    assert counts == {"enqueued": 0, "recovered": 0, "refreshed": 0}
    assert state.worktree_cleanup_candidates == {}


def test_deterministic_backend_refusal_halts_until_operator_requeues(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
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
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    # Keep journal retention on the same clock as the simulated cleanup passes.
    monkeypatch.setattr("evander_team_addon.sqlite_journal.utc_now", lambda: now)
    service = TeamWorktreeCleanupService(repository, backend)

    first = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
    )

    assert first.operator_action_required_count == 1
    assert first.recovery_required_count == 0
    failed = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert failed.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
    assert failed.last_safety_check == "tracked_changes"
    assert failed.last_error == "tracked changes prevent task worktree cleanup"
    assert failed.operator_action_reason == "tracked_changes"
    assert failed.operator_action_required_at == now
    assert "context" in repository.read().git_work_contexts

    not_retried = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
    )
    assert not_retried.attempted_count == 0
    assert len(backend.calls) == 1

    state, requeued = service.requeue_operator_action(
        failed.id,
        requested_by="operator:admin",
        reason="The tracked edits were reviewed and committed.",
        now=now + timedelta(seconds=1),
        expected_revision=repository.read_revision(),
    )
    assert state.revision == repository.read_revision()
    assert requeued.state == WorktreeCleanupState.PENDING
    assert requeued.automatic_retry_count == 0
    assert requeued.last_safety_check == "tracked_changes"
    assert requeued.last_operator_retry_by == "operator:admin"
    assert requeued.last_operator_retry_reason == "The tracked edits were reviewed and committed."

    second = service.reconcile(
        now=now + timedelta(seconds=1),
        grace_seconds=0,
        retry_seconds=0,
    )

    assert second.succeeded_count == 1
    succeeded = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert succeeded.state == WorktreeCleanupState.SUCCEEDED
    assert succeeded.attempt_count == 2
    assert len(repository.list_dispatch_activity(tool_name="team_worktree_cleanup")) == 2
    retry_audit = repository.list_dispatch_activity(tool_name="team_worktree_cleanup_retry")
    assert len(retry_audit) == 1
    assert retry_audit[0].caller_identity == "operator:admin"


@pytest.mark.parametrize(
    "failed_check",
    ["retained_commit_mismatch", "tracked_changes", "untracked_files"],
)
def test_deterministic_cleanup_safety_refusals_are_not_automatically_claimable(
    tmp_path,
    failed_check: str,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend(
        [
            {
                "state": "refused",
                "safe_to_remove": False,
                "failed_check": failed_check,
                "message": f"{failed_check} prevents cleanup",
            }
        ]
    )
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)

    first = service.reconcile(now=now, grace_seconds=0, retry_seconds=0)
    second = service.reconcile(now=now + timedelta(days=1), grace_seconds=0, retry_seconds=0)

    assert first.operator_action_required_count == 1
    assert second.attempted_count == 0
    assert len(backend.calls) == 1
    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
    assert candidate.operator_action_reason == failed_check


def test_cleanup_metadata_refresh_preserves_recovery_backoff_across_maintenance(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    old_commit = "2" * 40
    current_commit = "3" * 40
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    def _add_historical_context(state: TeamState) -> TeamState:
        state.git_work_contexts["historical-context"] = GitWorkContext(
            id="historical-context",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            commit_sha=old_commit,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
        )
        current = state.git_work_contexts["context"]
        current.commit_sha = current_commit
        current.created_at = now - timedelta(minutes=30)
        current.updated_at = now - timedelta(minutes=15)
        return state

    repository.update(_add_historical_context)
    backend = FakeCleanupBackend(
        [
            RuntimeError(
                "container worker POST /v1/sessions/session-backend/"
                "worktrees/cleanup returned HTTP 404: Not Found"
            ),
            _removed_outcome(),
        ]
    )
    service = TeamWorktreeCleanupService(repository, backend)

    first = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=300,
    )

    assert first.deferred_count == 1
    assert first.recovery_required_count == 0
    failed = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert failed.state == WorktreeCleanupState.PENDING
    assert failed.expected_head == current_commit
    assert failed.attempt_count == 1
    assert failed.automatic_retry_count == 1
    assert failed.eligible_at == now + timedelta(seconds=300)
    assert failed.last_safety_check == "backend_cleanup_failed"

    # Agent-completion maintenance can run between cron ticks. Re-reading the
    # historical and current contexts must not erase the failed attempt's
    # durable retry schedule or evidence.
    early = service.reconcile(
        now=now + timedelta(minutes=1),
        grace_seconds=0,
        retry_seconds=300,
    )

    assert early.attempted_count == 0
    still_deferred = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert still_deferred.state == WorktreeCleanupState.PENDING
    assert still_deferred.expected_head == current_commit
    assert still_deferred.attempt_count == 1
    assert still_deferred.eligible_at == now + timedelta(seconds=300)
    assert still_deferred.last_safety_check == "backend_cleanup_failed"
    assert len(backend.calls) == 1

    retried = service.reconcile(
        now=now + timedelta(seconds=300),
        grace_seconds=0,
        retry_seconds=300,
    )

    assert retried.succeeded_count == 1
    succeeded = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert succeeded.state == WorktreeCleanupState.SUCCEEDED
    assert succeeded.attempt_count == 2
    assert len(backend.calls) == 2


def test_transient_cleanup_failures_use_bounded_exponential_retry_then_halt(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend(
        [RuntimeError("worker temporarily unavailable")] * DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS
    )
    service = TeamWorktreeCleanupService(repository, backend)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_at = now
    base_retry_seconds = 10_000

    for attempt in range(1, DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS + 1):
        result = service.reconcile(
            now=attempt_at,
            grace_seconds=0,
            retry_seconds=base_retry_seconds,
        )
        candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
        assert candidate.automatic_retry_count == attempt
        if attempt == DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS:
            assert result.operator_action_required_count == 1
            assert candidate.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
            assert candidate.operator_action_reason == "automatic_retry_exhausted"
            break
        expected_delay = min(
            DEFAULT_CLEANUP_MAX_RETRY_SECONDS,
            base_retry_seconds * (2 ** (attempt - 1)),
        )
        assert result.deferred_count == 1
        assert candidate.state == WorktreeCleanupState.PENDING
        assert candidate.eligible_at == attempt_at + timedelta(seconds=expected_delay)
        attempt_at = candidate.eligible_at

    halted = service.reconcile(
        now=attempt_at + timedelta(days=30),
        grace_seconds=0,
        retry_seconds=base_retry_seconds,
    )
    assert halted.attempted_count == 0
    assert len(backend.calls) == DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS


def test_expired_processing_lease_reports_when_recovery_exhausts_retries(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([])
    service = TeamWorktreeCleanupService(repository, backend)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    service.enqueue_candidates(now=now, grace_seconds=0)

    def _expire_processing_lease(state: TeamState) -> TeamState:
        candidate = next(iter(state.worktree_cleanup_candidates.values()))
        candidate.state = WorktreeCleanupState.PROCESSING
        candidate.automatic_retry_count = DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS - 1
        candidate.updated_at = now - timedelta(hours=1)
        return state

    repository.update(_expire_processing_lease)

    result = service.reconcile(
        now=now,
        grace_seconds=0,
        recovery_seconds=1,
    )

    assert result.recovered_count == 1
    assert result.operator_action_required_count == 1
    assert result.attempted_count == 0
    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
    assert candidate.last_safety_check == "processing_lease_expired"
    assert candidate.operator_action_reason == "automatic_retry_exhausted"
    assert candidate.operator_action_required_at == now
    assert backend.calls == []
    activity = repository.list_dispatch_activity(tool_name="team_worktree_cleanup")
    assert len(activity) == 1
    assert activity[0].audit_record_id == candidate.id
    assert activity[0].status == TeamDispatchActivityStatus.FAILURE
    assert activity[0].error_code == "processing_lease_expired"


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


def test_new_owner_dispatch_does_not_block_previous_session_cleanup(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    service.enqueue_candidates(now=now, grace_seconds=0)

    def _reassign_with_active_dispatch(state: TeamState) -> TeamState:
        task = state.tasks["task"]
        task.owner_agent_id = "lead"
        task.state = TeamTaskState.IN_PROGRESS
        task.updated_at = now
        state.dispatches["dispatch-new-owner"] = TeamDispatch(
            id="dispatch-new-owner",
            agent_id="lead",
            session_id="session-lead",
            cause_kind="task",
            cause_id="task",
            task_ids=["task"],
            project_ids=["project"],
            managed_repo_ids=[_MANAGED_REPO_ID],
            status=TeamDispatchStatus.PREPARING,
            created_at=now,
        )
        return state

    repository.update(_reassign_with_active_dispatch)
    result = service.reconcile(
        now=now,
        grace_seconds=0,
        retry_seconds=0,
        dry_run=False,
    )

    assert result.succeeded_count == 1
    assert len(backend.calls) == 1
    candidate = next(iter(repository.read().worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.SUCCEEDED


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


def test_enqueue_uses_originating_dispatch_session_after_agent_rebind(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    def _rebind_agent(state: TeamState) -> TeamState:
        context = state.git_work_contexts["context"]
        context.dispatch_id = "origin-dispatch"
        state.dispatches["origin-dispatch"] = TeamDispatch(
            id="origin-dispatch",
            agent_id="backend",
            session_id="session-backend",
            cause_kind="task",
            cause_id="task",
            task_ids=["task"],
            project_ids=["project"],
            managed_repo_ids=[_MANAGED_REPO_ID],
            status=TeamDispatchStatus.COMPLETED,
            created_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=30),
        )
        state.agents["backend"].session_id = "session-rebound"
        return state

    repository.update(_rebind_agent)
    state, counts = TeamWorktreeCleanupService(
        repository,
        FakeCleanupBackend([_removed_outcome()]),
    ).enqueue_candidates(now=now, grace_seconds=0)

    assert counts["enqueued"] == 1
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.session_id == "session-backend"


def test_agent_session_rotation_does_not_rebind_frozen_context_candidate(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    enqueued, counts = service.enqueue_candidates(now=now, grace_seconds=0)
    original = next(iter(enqueued.worktree_cleanup_candidates.values()))
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}

    def _rotate_session(state: TeamState) -> TeamState:
        state.agents["backend"].session_id = "session-backend-rotated"
        return state

    repository.update(_rotate_session)
    after_rotation, rotated_counts = service.enqueue_candidates(
        now=now + timedelta(minutes=1),
        grace_seconds=0,
    )

    assert rotated_counts == {"enqueued": 0, "recovered": 0, "refreshed": 0}
    assert len(after_rotation.worktree_cleanup_candidates) == 1
    frozen = next(iter(after_rotation.worktree_cleanup_candidates.values()))
    assert frozen.id == original.id
    assert frozen.git_work_context_id == "context"
    assert frozen.session_id == "session-backend"
    assert frozen.execution_backend == "container_worker_vm"

    result = service.reconcile(
        now=now + timedelta(minutes=1),
        grace_seconds=0,
        dry_run=False,
    )
    assert result.succeeded_count == 1
    assert len(backend.calls) == 1
    assert backend.calls[0][0].session_id == "session-backend"


def test_same_context_refresh_keeps_frozen_scope_after_project_move(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    assert service.reconcile(now=now, grace_seconds=0, dry_run=False).succeeded_count == 1

    moved_directory_path = "root/departments/accounting"
    TeamAdminService(repository).update_project(
        project_id="project",
        directory_path=moved_directory_path,
        set_directory_path=True,
        git=ProjectGitConfig(
            managed_repo_id="mr_22222222222222222222",
            directory_path=moved_directory_path,
            base_branch="main",
        ),
        set_git=True,
    )

    def _restore_same_generation(state: TeamState) -> TeamState:
        state.git_work_contexts["context"] = GitWorkContext(
            id="context",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=now - timedelta(minutes=1),
            updated_at=now + timedelta(minutes=1),
        )
        return state

    repository.update(_restore_same_generation)
    state, counts = service.enqueue_candidates(
        now=now + timedelta(minutes=2),
        grace_seconds=0,
    )

    assert counts == {"enqueued": 0, "recovered": 0, "refreshed": 1}
    assert len(state.worktree_cleanup_candidates) == 1
    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.git_work_context_id == "context"
    assert candidate.managed_repo_id == _MANAGED_REPO_ID
    assert candidate.directory_path == "root"
    assert candidate.execution_backend == "container_worker_vm"


def test_new_context_generation_gets_current_directory_and_backend(tmp_path) -> None:
    repository = _seed_cleanup_state(tmp_path)
    outcomes = [
        _removed_outcome(reclaimed_bytes=128),
        _removed_outcome(reclaimed_bytes=256),
    ]

    class GenerationAwareBackend:
        def __init__(self) -> None:
            self.resolved_scopes: list[tuple[str, str]] = []
            self.calls: list[tuple[WorktreeCleanupCandidate, bool]] = []

        def execution_backend_for_session(
            self,
            *,
            session_id: str,
            directory_path: str,
        ) -> str:
            execution_backend = "container_worker_vm" if directory_path == "root" else "libvirt_vm"
            self.resolved_scopes.append((directory_path, execution_backend))
            return execution_backend

        def cleanup(
            self,
            candidate: WorktreeCleanupCandidate,
            *,
            dry_run: bool,
        ) -> dict[str, Any]:
            self.calls.append((candidate.model_copy(deep=True), dry_run))
            return outcomes.pop(0)

    backend = GenerationAwareBackend()
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    assert service.reconcile(now=now, grace_seconds=0).succeeded_count == 1

    moved_directory_path = "root/departments/accounting"
    TeamAdminService(repository).update_project(
        project_id="project",
        directory_path=moved_directory_path,
        set_directory_path=True,
        git=ProjectGitConfig(
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path=moved_directory_path,
            base_branch="main",
        ),
        set_git=True,
    )

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

    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    assert len(state.worktree_cleanup_candidates) == 2
    candidates_by_context = {
        candidate.git_work_context_id: candidate
        for candidate in state.worktree_cleanup_candidates.values()
    }
    original = candidates_by_context["context"]
    candidate = candidates_by_context["context-next"]
    assert original.state == WorktreeCleanupState.SUCCEEDED
    assert original.directory_path == "root"
    assert original.execution_backend == "container_worker_vm"
    assert candidate.state == WorktreeCleanupState.PENDING
    assert candidate.directory_path == moved_directory_path
    assert candidate.execution_backend == "libvirt_vm"
    assert candidate.completed_at is None
    assert candidate.reclaimed_bytes == 0
    assert backend.resolved_scopes == [
        ("root", "container_worker_vm"),
        (moved_directory_path, "libvirt_vm"),
    ]
    assert service.reconcile(now=next_now, grace_seconds=0).succeeded_count == 1
    assert len(backend.calls) == 2
    assert backend.calls[0][0].git_work_context_id == "context"
    assert backend.calls[1][0].git_work_context_id == "context-next"


def test_new_context_generation_waits_while_same_checkout_candidate_is_pending(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend([_removed_outcome()])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    first_state, first_counts = service.enqueue_candidates(now=now, grace_seconds=0)
    original = next(iter(first_state.worktree_cleanup_candidates.values()))

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
    state, counts = service.enqueue_candidates(
        now=now + timedelta(minutes=2),
        grace_seconds=0,
    )

    assert first_counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    assert counts == {"enqueued": 0, "recovered": 0, "refreshed": 0}
    assert len(state.worktree_cleanup_candidates) == 1
    assert next(iter(state.worktree_cleanup_candidates.values())).id == original.id


@pytest.mark.parametrize(
    "failed_check",
    ["head_mismatch", "retained_commit_mismatch"],
)
def test_new_context_generation_supersedes_stale_mismatch_candidate(
    tmp_path,
    failed_check: str,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    backend = FakeCleanupBackend(
        [
            {
                "state": "refused",
                "safe_to_remove": False,
                "failed_check": failed_check,
                "message": f"{failed_check} prevents cleanup",
            }
        ]
    )
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    service = TeamWorktreeCleanupService(repository, backend)
    first_state, first_counts = service.enqueue_candidates(now=now, grace_seconds=0)
    original = next(iter(first_state.worktree_cleanup_candidates.values()))
    next_commit = "4" * 40

    def _add_new_generation(state: TeamState) -> TeamState:
        state.git_work_contexts["context-next"] = GitWorkContext(
            id="context-next",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            commit_sha=next_commit,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=now + timedelta(minutes=1),
            updated_at=now + timedelta(minutes=1),
        )
        return state

    repository.update(_add_new_generation)
    mismatch_now = now + timedelta(minutes=2)
    halted = service.reconcile(
        now=mismatch_now,
        grace_seconds=0,
        retry_seconds=0,
    )
    halted_candidate = repository.read().worktree_cleanup_candidates[original.id]

    assert halted.operator_action_required_count == 1
    assert halted_candidate.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
    assert halted_candidate.last_safety_check == failed_check
    assert halted_candidate.operator_action_required_at == mismatch_now
    assert len(repository.read().worktree_cleanup_candidates) == 1

    next_now = now + timedelta(minutes=3)
    state, counts = service.enqueue_candidates(now=next_now, grace_seconds=0)

    assert first_counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    candidates_by_context = {
        candidate.git_work_context_id: candidate
        for candidate in state.worktree_cleanup_candidates.values()
    }
    superseded = candidates_by_context["context"]
    replacement = candidates_by_context["context-next"]
    assert superseded.state == WorktreeCleanupState.SUPERSEDED
    assert superseded.superseded_by_git_work_context_id == "context-next"
    assert superseded.completed_at == next_now
    assert superseded.last_safety_check == failed_check
    assert replacement.state == WorktreeCleanupState.PENDING
    assert replacement.expected_head == next_commit
    assert replacement.completed_at is None


def test_cleanup_candidate_uses_newest_context_not_opaque_id_order(
    tmp_path,
) -> None:
    repository = _seed_cleanup_state(tmp_path)
    old_commit = "1" * 40
    new_commit = "2" * 40

    def _add_reverse_sorted_contexts(state: TeamState) -> TeamState:
        old_context = state.git_work_contexts.pop("context")
        old_context.id = "z-old-context"
        old_context.commit_sha = old_commit
        old_context.created_at = datetime(2026, 7, 25, 9, tzinfo=UTC)
        old_context.updated_at = datetime(2026, 7, 25, 11, tzinfo=UTC)
        state.git_work_contexts[old_context.id] = old_context
        state.git_work_contexts["a-new-context"] = GitWorkContext(
            id="a-new-context",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
            base_commit=_BASE_COMMIT,
            commit_sha=new_commit,
            worktree_path=_WORKTREE_PATH,
            state=GitWorkContextState.ABANDONED,
            created_at=datetime(2026, 7, 25, 10, tzinfo=UTC),
            updated_at=datetime(2026, 7, 25, 11, tzinfo=UTC),
        )
        return state

    repository.update(_add_reverse_sorted_contexts)

    state, counts = TeamWorktreeCleanupService(
        repository,
        FakeCleanupBackend([_removed_outcome()]),
    ).enqueue_candidates(
        now=datetime(2026, 7, 25, 12, tzinfo=UTC),
        grace_seconds=0,
    )

    candidate = next(iter(state.worktree_cleanup_candidates.values()))
    assert counts == {"enqueued": 1, "recovered": 0, "refreshed": 0}
    assert candidate.git_work_context_id == "a-new-context"
    assert candidate.expected_head == new_commit


def test_effect_backend_delegates_container_cleanup_with_exact_expectations(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    repository = _seed_cleanup_state(tmp_path)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)

    def _seed_committed_attempt(state: TeamState) -> TeamState:
        first_context = state.git_work_contexts["context"]
        state.git_work_contexts["context-latest"] = first_context.model_copy(
            update={
                "id": "context-latest",
                "created_at": now + timedelta(seconds=1),
                "updated_at": now + timedelta(seconds=1),
            },
            deep=True,
        )
        state.git_preparation_attempts["gitprep-committed-receipt"] = TeamGitPreparationAttempt(
            id="gitprep-committed-receipt",
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-prepared",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=_WORKTREE_PATH,
            branch="agents/session-backend/tasks/task-12345678",
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.COMMITTED,
            worker_request_fingerprint="f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            git_work_context_id="context",
            prepared_at=now,
            committed_at=now,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_committed_attempt)

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
        git_work_context_id="context-latest",
        managed_repo_id=_MANAGED_REPO_ID,
        execution_backend="container_worker_vm",
        worktree_path=_WORKTREE_PATH,
        branch="agents/session-backend/tasks/task-12345678",
        expected_head=_BASE_COMMIT,
        reason="task_resolved",
    )

    result = EffectTaskWorktreeCleanupBackend(
        SimpleNamespace(metadata={"directory_path": "root"}),
        repository=repository,
    ).cleanup(candidate, dry_run=True)

    assert result["state"] == "already_absent"
    assert calls == [
        {
            "session_id": "session-backend",
            "repo_name": "project",
            "worktree_name": "task-12345678",
            "preparation_attempt_id": "gitprep-committed-receipt",
            "branch": "agents/session-backend/tasks/task-12345678",
            "expected_commit_sha": _BASE_COMMIT,
            "expected_target_dir": _WORKTREE_PATH,
            "dry_run": True,
        }
    ]

    preparation_candidate = candidate.model_copy(
        update={
            "id": "preparation-candidate",
            "git_work_context_id": "preparation:gitprep-task-12345678",
        }
    )
    EffectTaskWorktreeCleanupBackend(SimpleNamespace(metadata={"directory_path": "root"})).cleanup(
        preparation_candidate, dry_run=False
    )

    assert calls[1] == {
        "session_id": "session-backend",
        "repo_name": "project",
        "worktree_name": "task-12345678",
        "preparation_attempt_id": "gitprep-task-12345678",
        "branch": "agents/session-backend/tasks/task-12345678",
        "expected_commit_sha": _BASE_COMMIT,
        "expected_target_dir": _WORKTREE_PATH,
        "dry_run": False,
    }


def test_effect_backend_handles_legacy_and_pending_receipt_cleanup(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    repository = _seed_cleanup_state(tmp_path, task_state=TeamTaskState.IN_PROGRESS)
    now = datetime(2026, 8, 24, 12, tzinfo=UTC)
    attempt_id = "gitprep-legacy-worker-cleanup"
    worktree_path = "/home/agent/worktrees/project/task-legacy-worker"

    def _seed_legacy_attempt(state: TeamState) -> TeamState:
        state.git_work_contexts.clear()
        state.git_preparation_attempts[attempt_id] = TeamGitPreparationAttempt(
            id=attempt_id,
            agent_id="backend",
            session_id="session-backend",
            task_id="task",
            project_id="project",
            latest_dispatch_id="dispatch-legacy-worker",
            managed_repo_id=_MANAGED_REPO_ID,
            directory_path="root",
            execution_backend="container_worker_vm",
            worktree_path=worktree_path,
            branch="agents/session-backend/tasks/task-legacy-worker",
            base_ref="main",
            preparation_generation="a" * 20,
            state=TeamGitPreparationAttemptState.CLEANUP_PENDING,
            worker_request_fingerprint="legacy-container-worker:" + "f" * 64,
            base_commit_sha=_BASE_COMMIT,
            head_commit_sha=_BASE_COMMIT,
            prepared_at=now,
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_legacy_attempt)

    class FakeContainerBackend:
        def cleanup_task_worktree(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "state": "removed",
                    "safe_to_remove": True,
                    "removed": True,
                    "pruned": True,
                    "validation_count": 2,
                    "message": "removed",
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
        id=f"cleanup-{attempt_id}",
        agent_id="backend",
        session_id="session-backend",
        task_id="task",
        project_id="project",
        git_work_context_id=f"preparation:{attempt_id}",
        managed_repo_id=_MANAGED_REPO_ID,
        execution_backend="container_worker_vm",
        worktree_path=worktree_path,
        branch="agents/session-backend/tasks/task-legacy-worker",
        expected_head=_BASE_COMMIT,
        reason="preparation_invalidated",
    )

    EffectTaskWorktreeCleanupBackend(
        SimpleNamespace(metadata={"directory_path": "root"}),
        repository=repository,
    ).cleanup(candidate, dry_run=False)

    assert calls == [
        {
            "session_id": "session-backend",
            "repo_name": "project",
            "worktree_name": "task-legacy-worker",
            "branch": "agents/session-backend/tasks/task-legacy-worker",
            "expected_commit_sha": _BASE_COMMIT,
            "expected_target_dir": worktree_path,
            "dry_run": False,
        }
    ]

    def _make_attempt_pending(state: TeamState) -> TeamState:
        attempt = state.git_preparation_attempts[attempt_id]
        attempt.state = TeamGitPreparationAttemptState.PENDING
        attempt.worker_request_fingerprint = None
        attempt.base_commit_sha = None
        attempt.head_commit_sha = None
        attempt.prepared_at = None
        return state

    repository.update(_make_attempt_pending)
    pending_candidate = candidate.model_copy(update={"expected_head": None}, deep=True)

    EffectTaskWorktreeCleanupBackend(
        SimpleNamespace(metadata={"directory_path": "root"}),
        repository=repository,
    ).cleanup(pending_candidate, dry_run=False)

    assert calls[1] == {
        "session_id": "session-backend",
        "repo_name": "project",
        "worktree_name": "task-legacy-worker",
        "preparation_attempt_id": attempt_id,
        "branch": "agents/session-backend/tasks/task-legacy-worker",
        "expected_commit_sha": None,
        "expected_target_dir": worktree_path,
        "dry_run": False,
    }


def test_libvirt_preparation_uses_cleanup_lock_and_records_base_commit(tmp_path) -> None:
    calls: list[dict[str, Any]] = []

    class FakeVmExecutor:
        def vm_exec(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "success": True,
                "stdout": f"EVANDER_BASE_COMMIT={_BASE_COMMIT}\n",
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
    assert "base_fetch_ref=refs/heads/main" in command
    fresh_base_fetch = "\n".join(
        (
            '  if [ -z "$pinned_base_commit" ]; then',
            '    if [ "$base" = "HEAD" ]; then',
            "      git fetch --no-tags origin HEAD",
            "    else",
            '      git fetch --no-tags origin "$base_fetch_ref"',
            "    fi",
            '    base_commitish="$(git rev-parse --verify FETCH_HEAD^{commit})"',
            "  fi",
            '  git worktree add -b "$branch" "$target" "$base_commitish"',
        )
    )
    assert fresh_base_fetch in command
    assert 'git -C "$target" rev-parse HEAD' in command


def test_container_preparation_records_pinned_base_separately_from_task_head(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    task_head = "2" * 40

    class FakeContainerBackend:
        def prepare_task_worktree(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                target_dir=_WORKTREE_PATH,
                branch=kwargs["branch"],
                commit_sha=task_head,
                base_commit_sha=_BASE_COMMIT,
            )

    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.selected_execution_backend_for_target",
        lambda *_args, **_kwargs: ("container_worker_vm", SimpleNamespace()),
    )
    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.container_backend",
        lambda *_args, **_kwargs: FakeContainerBackend(),
    )

    path, base_commit = _prepare_project_repo_task_worktree(
        SimpleNamespace(metadata={"directory_path": "root"}),
        project_repo={
            "guest_repo_name": "project",
            "target_dir": "/home/agent/repos/project",
            "execution_backend": "container_worker_vm",
        },
        session_id="session-backend",
        task_id="task",
        agent_branch="agents/session-backend/tasks/task-12345678",
        base_ref="team/team/releases/release-1234",
        pinned_base_commit=_BASE_COMMIT,
        pinned_base_tree="3" * 40,
        task_author_identity=ProjectGitIdentity(name="Task Author", email="task@example.test"),
    )

    assert path == _WORKTREE_PATH
    assert base_commit == _BASE_COMMIT
    assert calls == [
        {
            "session_id": "session-backend",
            "repo_name": "project",
            "worktree_name": (
                f"task-{hashlib.sha256(b'task').hexdigest()[:8]}-{_BASE_COMMIT[:12]}"
            ),
            "branch": "agents/session-backend/tasks/task-12345678",
            "base_ref": "team/team/releases/release-1234",
            "expected_base_commit_sha": _BASE_COMMIT,
            "expected_base_tree_hash": "3" * 40,
            "dependency_git_sources": [],
            "dependency_composition_mode": "automatic",
            "dependency_composition_recovery_id": None,
            "resolved_dependency_branch": None,
            "resolved_dependency_commit_sha": None,
            "git_author_name": "Task Author",
            "git_author_email": "task@example.test",
            "dependency_merge_author_name": "Evander Bot",
            "dependency_merge_author_email": "evander@quinth.se",
        }
    ]


def test_candidate_correction_branch_is_scoped_to_the_pinned_candidate() -> None:
    ordinary = _project_repo_agent_branch("session", task_id="correction-task")
    candidate = _project_repo_agent_branch(
        "session",
        task_id="correction-task",
        candidate_commit_sha="a" * 40,
    )

    assert candidate == f"{ordinary}-{'a' * 12}"


def test_task_branch_is_scoped_to_preparation_generation() -> None:
    first = _project_repo_agent_branch(
        "session",
        task_id="integration-task",
        preparation_generation="a" * 20,
    )
    second = _project_repo_agent_branch(
        "session",
        task_id="integration-task",
        preparation_generation="b" * 20,
    )

    assert first.endswith(f"-prep-{'a' * 12}")
    assert second.endswith(f"-prep-{'b' * 12}")
    assert first != second


def test_container_preparation_recovers_legacy_base_from_prior_team_context(
    monkeypatch,
) -> None:
    task_head = "2" * 40

    class FakeContainerBackend:
        def prepare_task_worktree(self, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                target_dir=_WORKTREE_PATH,
                branch=kwargs["branch"],
                commit_sha=task_head,
                base_commit_sha=None,
                created=False,
            )

    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.selected_execution_backend_for_target",
        lambda *_args, **_kwargs: ("container_worker_vm", SimpleNamespace()),
    )
    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.container_backend",
        lambda *_args, **_kwargs: FakeContainerBackend(),
    )

    path, base_commit = _prepare_project_repo_task_worktree(
        SimpleNamespace(metadata={"directory_path": "root"}),
        project_repo={
            "guest_repo_name": "project",
            "target_dir": "/home/agent/repos/project",
            "execution_backend": "container_worker_vm",
        },
        session_id="session-backend",
        task_id="task",
        agent_branch="agents/session-backend/tasks/task-12345678",
        base_ref="main",
        prior_base_commit=_BASE_COMMIT,
    )

    assert path == _WORKTREE_PATH
    assert base_commit == _BASE_COMMIT


def test_container_preparation_records_legacy_worker_receipt(monkeypatch) -> None:
    task_head = "2" * 40

    class FakeContainerBackend:
        def prepare_task_worktree(self, **kwargs: Any) -> SimpleNamespace:
            assert kwargs["preparation_attempt_id"] == "gitprep-task-12345678"
            return SimpleNamespace(
                target_dir=_WORKTREE_PATH,
                branch=kwargs["branch"],
                commit_sha=task_head,
                base_commit_sha=_BASE_COMMIT,
                created=True,
            )

    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.selected_execution_backend_for_target",
        lambda *_args, **_kwargs: ("container_worker_vm", SimpleNamespace()),
    )
    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.container_backend",
        lambda *_args, **_kwargs: FakeContainerBackend(),
    )
    receipt: dict[str, Any] = {}

    path, base_commit = _prepare_project_repo_task_worktree(
        SimpleNamespace(metadata={"directory_path": "root"}),
        project_repo={
            "guest_repo_name": "project",
            "target_dir": "/home/agent/repos/project",
            "execution_backend": "container_worker_vm",
        },
        session_id="session-backend",
        task_id="task",
        agent_branch="agents/session-backend/tasks/task-12345678",
        base_ref="main",
        preparation_attempt_id="gitprep-task-12345678",
        preparation_receipt_result=receipt,
    )

    assert path == _WORKTREE_PATH
    assert base_commit == _BASE_COMMIT
    assert str(receipt["request_fingerprint"]).startswith("legacy-container-worker:")
    assert receipt["head_commit_sha"] == task_head
    assert receipt["receipt_reused"] is False


@pytest.mark.parametrize(
    "reported_base",
    [pytest.param(None, id="missing-base"), pytest.param("2" * 40, id="guest-reported-base")],
)
def test_container_preparation_requires_server_authoritative_base(
    monkeypatch,
    reported_base: str | None,
) -> None:
    class FakeContainerBackend:
        def prepare_task_worktree(self, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                target_dir=_WORKTREE_PATH,
                branch=kwargs["branch"],
                commit_sha="2" * 40,
                base_commit_sha=reported_base,
                created=False,
            )

    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.selected_execution_backend_for_target",
        lambda *_args, **_kwargs: ("container_worker_vm", SimpleNamespace()),
    )
    monkeypatch.setattr(
        "evander_team_addon.provider_git_worktrees.container_backend",
        lambda *_args, **_kwargs: FakeContainerBackend(),
    )

    with pytest.raises(
        RuntimeError,
        match="no authoritative server-recorded base.*audited provenance recovery",
    ):
        _prepare_project_repo_task_worktree(
            SimpleNamespace(metadata={"directory_path": "root"}),
            project_repo={
                "guest_repo_name": "project",
                "target_dir": "/home/agent/repos/project",
                "execution_backend": "container_worker_vm",
            },
            session_id="session-backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_ref="main",
        )


def test_prior_task_base_commit_rejects_conflicting_team_provenance() -> None:
    state = SimpleNamespace(
        git_work_contexts={
            "ctx-first": GitWorkContext(
                id="ctx-first",
                agent_id="backend",
                task_id="task",
                agent_branch="agents/session-backend/tasks/task-12345678",
                base_commit="1" * 40,
            ),
            "ctx-second": GitWorkContext(
                id="ctx-second",
                agent_id="backend",
                task_id="task",
                agent_branch="agents/session-backend/tasks/task-12345678",
                base_commit="2" * 40,
            ),
        }
    )

    with pytest.raises(RuntimeError, match="conflicting base commits"):
        _prior_project_repo_task_base_commit(
            state,
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
        )


def test_prior_task_base_commit_is_scoped_to_task_base_snapshot() -> None:
    state = SimpleNamespace(
        git_work_contexts={
            "ctx-main": GitWorkContext(
                id="ctx-main",
                agent_id="backend",
                task_id="task",
                agent_branch="agents/session-backend/tasks/task-12345678",
                base_branch="main",
                base_commit="1" * 40,
            ),
            "ctx-feature": GitWorkContext(
                id="ctx-feature",
                agent_id="backend",
                task_id="task",
                agent_branch="agents/session-backend/tasks/task-12345678",
                base_branch="feature/foo",
                base_commit="2" * 40,
            ),
        }
    )

    assert (
        _prior_project_repo_task_base_commit(
            state,
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/tasks/task-12345678",
            base_branch="main",
        )
        == "1" * 40
    )


def test_prior_task_base_commit_survives_task_reassignment() -> None:
    state = SimpleNamespace(
        git_work_contexts={
            "ctx-original-owner": GitWorkContext(
                id="ctx-original-owner",
                agent_id="backend-original",
                task_id="task",
                agent_branch="agents/session-original/tasks/task-12345678",
                base_branch="feature/foo",
                base_commit="2" * 40,
            ),
        }
    )

    assert (
        _prior_project_repo_task_base_commit(
            state,
            agent_id="backend-reassigned",
            task_id="task",
            agent_branch="agents/session-reassigned/tasks/task-12345678",
            base_branch="feature/foo",
        )
        == "2" * 40
    )
