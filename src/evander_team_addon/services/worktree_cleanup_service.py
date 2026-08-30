from __future__ import annotations

import hashlib
import json
import fcntl
import os
import re
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Protocol
from uuid import uuid4

from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.agent_session import (
    container_backend,
    selected_execution_backend_for_target,
)
from evander.events.provider_models import AgentExecEffectDefinition
from evander.execution import build_libvirt_task_worktree_cleanup_command

from ..models import (
    GitWorkContext,
    GitWorkContextState,
    TaskResolutionStatus,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamDispatchStatus,
    TeamState,
    WORKTREE_CLEANUP_TRANSIENT_FAILURE_CHECKS,
    WorktreeCleanupCandidate,
    WorktreeCleanupState,
    summarize_worktree_cleanup_inventory,
)
from ..repository import TeamStateRepository
from .domain.preparation_failures import task_git_work_context_generation
from .domain.task_assessment import TaskAssessmentService


DEFAULT_CLEANUP_GRACE_SECONDS = 300
DEFAULT_CLEANUP_RETRY_SECONDS = 300
DEFAULT_CLEANUP_RECOVERY_SECONDS = 900
DEFAULT_CLEANUP_MAX_RETRY_SECONDS = 21_600
DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS = 8
_FULL_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_ACTIVE_DISPATCH_STATES = frozenset(
    {
        TeamDispatchStatus.PREPARING,
        TeamDispatchStatus.LEASED,
    }
)
_STALE_GENERATION_FAILURE_CHECKS = frozenset(
    {
        "head_mismatch",
        "retained_commit_mismatch",
    }
)
_TERMINAL_CLEANUP_STATES = frozenset(
    {
        WorktreeCleanupState.SUCCEEDED,
        WorktreeCleanupState.SUPERSEDED,
    }
)


def _set_ignored_file_evidence(
    candidate: WorktreeCleanupCandidate,
    inventory: object,
) -> None:
    sample, count, digest, truncated = summarize_worktree_cleanup_inventory(inventory)
    candidate.ignored_file_inventory = sample
    candidate.ignored_file_count = count
    candidate.ignored_file_inventory_sha256 = digest
    candidate.ignored_file_inventory_truncated = truncated


class TaskWorktreeCleanupBackend(Protocol):
    def execution_backend_for_session(
        self,
        *,
        session_id: str,
        directory_path: str,
    ) -> str: ...

    def cleanup(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        dry_run: bool,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class WorktreeCleanupSafetyFailure:
    check: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class WorktreeCleanupReconcileResult:
    enqueued_count: int
    recovered_count: int
    attempted_count: int
    succeeded_count: int
    recovery_required_count: int
    operator_action_required_count: int
    deferred_count: int
    dry_run_count: int
    reclaimed_bytes: int
    candidate_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        return {
            "enqueued_count": self.enqueued_count,
            "recovered_count": self.recovered_count,
            "attempted_count": self.attempted_count,
            "succeeded_count": self.succeeded_count,
            "recovery_required_count": self.recovery_required_count,
            "operator_action_required_count": self.operator_action_required_count,
            "deferred_count": self.deferred_count,
            "dry_run_count": self.dry_run_count,
            "reclaimed_bytes": self.reclaimed_bytes,
            "candidate_ids": list(self.candidate_ids),
            "errors": list(self.errors),
        }


class EffectTaskWorktreeCleanupBackend:
    """Execute cleanup through the session's selected execution backend."""

    def __init__(
        self,
        context: Any,
        *,
        repository: TeamStateRepository | None = None,
    ) -> None:
        self._context = context
        # Keep the optional argument for callers compiled against the earlier
        # constructor. Physical cleanup must use the scope frozen on the
        # candidate rather than re-reading mutable project configuration.
        _ = repository

    def execution_backend_for_session(
        self,
        *,
        session_id: str,
        directory_path: str,
    ) -> str:
        execution_backend, _config = selected_execution_backend_for_target(
            self._context,
            {"session_id": session_id},
            directory_path=directory_path,
        )
        if execution_backend not in {"libvirt_vm", "container_worker_vm"}:
            raise ValueError(
                f"Team task worktree cleanup does not support execution backend "
                f"{execution_backend!r}"
            )
        return execution_backend

    def cleanup(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        dry_run: bool,
    ) -> Mapping[str, Any]:
        repo_name, worktree_name = _task_worktree_components(candidate.worktree_path)
        expected_commit_sha = _required_expected_commit_sha(candidate)
        if candidate.execution_backend == "container_worker_vm":
            _selected, config = selected_execution_backend_for_target(
                self._context,
                {
                    "session_id": candidate.session_id,
                    "execution_backend": "container_worker_vm",
                },
                directory_path=candidate.directory_path,
            )
            record = container_backend(self._context, config).cleanup_task_worktree(
                session_id=candidate.session_id,
                repo_name=repo_name,
                worktree_name=worktree_name,
                branch=candidate.branch,
                expected_commit_sha=expected_commit_sha,
                expected_target_dir=candidate.worktree_path,
                dry_run=dry_run,
            )
            payload = (
                record.model_dump(mode="python") if hasattr(record, "model_dump") else dict(record)
            )
            return payload

        if candidate.execution_backend != "libvirt_vm":
            raise ValueError(
                f"unsupported task worktree cleanup backend {candidate.execution_backend!r}"
            )
        repo_dir = f"/home/agent/repos/{repo_name}"
        worktrees_root = f"/home/agent/worktrees/{repo_name}"
        lock_path = _libvirt_task_worktree_lock_path(repo_name)
        command = build_libvirt_task_worktree_cleanup_command(
            trusted_root="/home/agent",
            repo_dir=repo_dir,
            worktrees_root=worktrees_root,
            target_dir=candidate.worktree_path,
            branch=candidate.branch,
            expected_commit_sha=expected_commit_sha,
            lock_path=lock_path,
            dry_run=dry_run,
        )
        provider = AgentExecEffectProvider()
        provider.initialize(
            AgentExecEffectDefinition(
                id=f"team-cleanup-task-worktree-{candidate.id[-24:]}",
                display_name="Clean up Team task worktree",
                type="effect:agent:exec",
                session_id=candidate.session_id,
                command=command,
                working_directory="/home/agent",
                timeout_seconds=120,
                check=True,
            ),
            self._context,
        )
        result = provider.execute(resolved_config={"execution_backend": "libvirt_vm"})
        stdout = str(result.get("stdout") or "")
        payload = _last_json_object(stdout)
        payload.update(
            {
                "session_id": candidate.session_id,
                "repo_name": repo_name,
                "worktree_name": worktree_name,
                "target_dir": candidate.worktree_path,
                "branch": candidate.branch,
                "expected_commit_sha": expected_commit_sha,
                "dry_run": dry_run,
            }
        )
        return payload


class TeamWorktreeCleanupService:
    """Durably enqueue and reconcile task-scoped physical worktree cleanup."""

    def __init__(
        self,
        repository: TeamStateRepository,
        backend: TaskWorktreeCleanupBackend,
    ) -> None:
        self._repository = repository
        self._backend = backend

    def enqueue_candidates(
        self,
        *,
        now: datetime | None = None,
        grace_seconds: int = DEFAULT_CLEANUP_GRACE_SECONDS,
        recovery_seconds: int = DEFAULT_CLEANUP_RECOVERY_SECONDS,
    ) -> tuple[TeamState, dict[str, int]]:
        effective_now = _ensure_utc(now or datetime.now(UTC))
        grace = timedelta(seconds=max(0, int(grace_seconds)))
        recovery_after = timedelta(seconds=max(1, int(recovery_seconds)))
        snapshot = self._repository.read()
        selected_context_ids = _cleanup_context_ids_to_enqueue(snapshot)
        execution_targets: dict[str, tuple[str, str]] = {}
        for context in snapshot.git_work_contexts.values():
            if context.id not in selected_context_ids:
                continue
            task = snapshot.tasks.get(context.task_id)
            agent = snapshot.agents.get(context.agent_id)
            if task is None or agent is None:
                continue
            frozen_candidate = _cleanup_candidate_for_context(
                snapshot,
                context=context,
            )
            if frozen_candidate is not None:
                execution_targets[context.id] = (
                    frozen_candidate.session_id,
                    frozen_candidate.execution_backend,
                )
                continue
            session_id = _git_work_context_session_id(snapshot, context)
            if session_id is None:
                continue
            project = snapshot.projects.get(task.project_id)
            if project is None or project.git is None:
                continue
            prior_candidate = _cleanup_candidate_with_context_identity(
                snapshot,
                context=context,
                session_id=session_id,
                managed_repo_id=project.git.managed_repo_id,
            )
            if prior_candidate is not None:
                execution_targets[context.id] = (
                    prior_candidate.session_id,
                    prior_candidate.execution_backend,
                )
                continue
            try:
                execution_backend = self._backend.execution_backend_for_session(
                    session_id=session_id,
                    directory_path=project.directory_path,
                )
                execution_targets[context.id] = (session_id, execution_backend)
            except Exception:
                # Candidate creation remains fail closed. A later pass can
                # resolve the session backend once its runtime metadata exists.
                continue

        counts = {"enqueued": 0, "recovered": 0, "refreshed": 0}

        def _mutate(
            state: TeamState,
            *,
            append_activity: Callable[[TeamDispatchActivity], object] | None = None,
        ) -> TeamState:
            for candidate in state.worktree_cleanup_candidates.values():
                if (
                    candidate.state == WorktreeCleanupState.PROCESSING
                    and candidate.updated_at <= effective_now - recovery_after
                ):
                    candidate.automatic_retry_count += 1
                    if candidate.automatic_retry_count >= DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS:
                        candidate.state = WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
                        candidate.operator_action_required_at = effective_now
                        candidate.operator_action_reason = "automatic_retry_exhausted"
                    else:
                        candidate.state = WorktreeCleanupState.PENDING
                    candidate.last_safety_check = "processing_lease_expired"
                    candidate.last_error = (
                        "cleanup processing did not complete before the recovery grace"
                    )
                    candidate.eligible_at = effective_now
                    candidate.updated_at = effective_now
                    counts["recovered"] += 1
                    if append_activity is not None:
                        append_activity(
                            self._audit_activity(
                                candidate,
                                status=TeamDispatchActivityStatus.FAILURE,
                                failed_check="processing_lease_expired",
                                message=candidate.last_error,
                                now=effective_now,
                            )
                        )

            assessments = TaskAssessmentService(state)
            _supersede_stale_cleanup_generations(state, now=effective_now)
            selected_context_ids = _cleanup_context_ids_to_enqueue(state)
            for context in sorted(
                state.git_work_contexts.values(),
                key=lambda item: (
                    _ensure_utc(item.created_at),
                    _ensure_utc(item.updated_at),
                    item.id,
                ),
            ):
                if (
                    context.id not in selected_context_ids
                    or context.state == GitWorkContextState.ACTIVE
                    or context.worktree_path is None
                ):
                    continue
                task = state.tasks.get(context.task_id)
                project = state.projects.get(task.project_id) if task is not None else None
                agent = state.agents.get(context.agent_id)
                if task is None or project is None or agent is None:
                    continue
                try:
                    resolved = (
                        assessments.assess(task.id).resolution_status
                        == TaskResolutionStatus.RESOLVED
                    )
                except Exception:
                    resolved = False
                reassigned = task.owner_agent_id != context.agent_id
                preparation_invalidated = _context_preparation_was_invalidated(
                    state,
                    context,
                )
                if not resolved and not reassigned and not preparation_invalidated:
                    continue
                execution_target = execution_targets.get(context.id)
                if execution_target is None:
                    continue
                session_id, execution_backend = execution_target
                if execution_backend not in {"libvirt_vm", "container_worker_vm"}:
                    continue
                frozen_candidate = _cleanup_candidate_for_context(
                    state,
                    context=context,
                )
                if frozen_candidate is None and project.git is None:
                    continue
                try:
                    _task_worktree_components(context.worktree_path)
                except ValueError:
                    # Persisting a candidate with the unexpected path makes the
                    # recovery evidence visible instead of ever deleting it.
                    pass
                expected_head, retained_commit = _expected_commits_for_context(
                    state,
                    context,
                )
                if frozen_candidate is not None:
                    identity = frozen_candidate.id
                    existing = frozen_candidate
                else:
                    assert project.git is not None
                    identity = _cleanup_candidate_identity(
                        task_id=task.id,
                        git_work_context_id=context.id,
                        managed_repo_id=project.git.managed_repo_id,
                        session_id=session_id,
                        worktree_path=context.worktree_path,
                    )
                    existing = state.worktree_cleanup_candidates.get(identity)
                reason = (
                    "task_resolved"
                    if resolved
                    else "task_reassigned"
                    if reassigned
                    else "preparation_invalidated"
                )
                if existing is not None:
                    if (
                        existing.state == WorktreeCleanupState.SUCCEEDED
                        and existing.completed_at is not None
                        and _ensure_utc(context.updated_at) > _ensure_utc(existing.completed_at)
                    ):
                        existing.state = WorktreeCleanupState.PENDING
                        existing.reason = reason
                        existing.base_ref = context.base_branch
                        existing.expected_head = expected_head
                        existing.retained_commit_sha = retained_commit
                        existing.eligible_at = max(
                            effective_now,
                            _ensure_utc(context.updated_at) + grace,
                        )
                        existing.last_safety_check = None
                        existing.last_error = None
                        existing.automatic_retry_count = 0
                        existing.operator_action_required_at = None
                        existing.operator_action_reason = None
                        _set_ignored_file_evidence(existing, [])
                        existing.ignored_bytes = 0
                        existing.worktree_bytes = 0
                        existing.reclaimed_bytes = 0
                        existing.completed_at = None
                        existing.updated_at = effective_now
                        counts["refreshed"] += 1
                        continue
                    if (
                        existing.state != WorktreeCleanupState.SUCCEEDED
                        and existing.expected_head != expected_head
                        and expected_head is not None
                    ):
                        # Refreshing Git safety metadata must not erase a
                        # prior attempt's durable backoff or failure evidence.
                        # One context generation may advance its recorded head
                        # before physical cleanup runs.
                        existing.expected_head = expected_head
                        existing.retained_commit_sha = retained_commit
                        existing.reason = reason
                        existing.updated_at = effective_now
                        counts["refreshed"] += 1
                    continue
                assert project.git is not None
                state.worktree_cleanup_candidates[identity] = WorktreeCleanupCandidate(
                    id=identity,
                    agent_id=context.agent_id,
                    session_id=session_id,
                    task_id=task.id,
                    project_id=project.id,
                    git_work_context_id=context.id,
                    managed_repo_id=project.git.managed_repo_id,
                    directory_path=project.directory_path,
                    execution_backend=execution_backend,
                    worktree_path=context.worktree_path,
                    branch=context.agent_branch,
                    base_ref=context.base_branch,
                    expected_head=expected_head,
                    retained_commit_sha=retained_commit,
                    reason=reason,
                    eligible_at=max(effective_now, _ensure_utc(context.updated_at) + grace),
                    created_at=effective_now,
                    updated_at=effective_now,
                )
                counts["enqueued"] += 1
            return state

        probe = snapshot.model_copy(deep=True)
        _mutate(probe)
        if not any(counts.values()):
            return snapshot, counts
        counts = {"enqueued": 0, "recovered": 0, "refreshed": 0}
        state = self._repository.update_with_command_transaction(
            lambda current, tx: _mutate(
                current,
                append_activity=tx.append_dispatch_activity,
            )
        )
        return state, counts

    def reconcile(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        grace_seconds: int = DEFAULT_CLEANUP_GRACE_SECONDS,
        retry_seconds: int = DEFAULT_CLEANUP_RETRY_SECONDS,
        recovery_seconds: int = DEFAULT_CLEANUP_RECOVERY_SECONDS,
        dry_run: bool | None = False,
    ) -> WorktreeCleanupReconcileResult:
        effective_now = _ensure_utc(now or datetime.now(UTC))
        operator_action_ids_before = {
            candidate.id
            for candidate in self._repository.read().worktree_cleanup_candidates.values()
            if candidate.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
        }
        enqueue_state, enqueue_counts = self.enqueue_candidates(
            now=effective_now,
            grace_seconds=grace_seconds,
            recovery_seconds=recovery_seconds,
        )
        attempted = 0
        succeeded = 0
        recovery_required = 0
        operator_action_required = sum(
            candidate.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
            and candidate.id not in operator_action_ids_before
            for candidate in enqueue_state.worktree_cleanup_candidates.values()
        )
        deferred = 0
        dry_runs = 0
        reclaimed_bytes = 0
        candidate_ids: list[str] = []
        errors: list[str] = []
        for _index in range(max(0, int(limit))):
            candidate = self._claim_next(
                effective_now,
                excluded_ids=set(candidate_ids),
            )
            if candidate is None:
                break
            attempted += 1
            candidate_ids.append(candidate.id)
            candidate_dry_run = (
                candidate.last_safety_check != "dry_run_safe" if dry_run is None else dry_run
            )
            status, reclaimed, error = self._reconcile_claimed_candidate(
                candidate,
                now=effective_now,
                retry_seconds=retry_seconds,
                dry_run=candidate_dry_run,
            )
            if status == "succeeded":
                succeeded += 1
                reclaimed_bytes += reclaimed
            elif status == "dry_run":
                dry_runs += 1
            elif status == "deferred":
                deferred += 1
            elif status == "operator_action_required":
                operator_action_required += 1
            else:
                recovery_required += 1
            if error is not None:
                errors.append(f"{candidate.id}: {error}")

        return WorktreeCleanupReconcileResult(
            enqueued_count=enqueue_counts["enqueued"],
            recovered_count=enqueue_counts["recovered"],
            attempted_count=attempted,
            succeeded_count=succeeded,
            recovery_required_count=recovery_required,
            operator_action_required_count=operator_action_required,
            deferred_count=deferred,
            dry_run_count=dry_runs,
            reclaimed_bytes=reclaimed_bytes,
            candidate_ids=tuple(candidate_ids),
            errors=tuple(errors),
        )

    def requeue_operator_action(
        self,
        candidate_id: str,
        *,
        requested_by: str,
        reason: str,
        now: datetime | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, WorktreeCleanupCandidate]:
        """Requeue one halted candidate without bypassing any cleanup safety check."""

        normalized_candidate_id = str(candidate_id or "").strip()
        normalized_actor = str(requested_by or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_candidate_id:
            raise ValueError("cleanup candidate id is required")
        if not normalized_actor:
            raise ValueError("cleanup retry actor is required")
        if not normalized_reason:
            raise ValueError("cleanup retry reason is required")
        if len(normalized_reason) > 1_000:
            raise ValueError("cleanup retry reason must be at most 1000 characters")

        effective_now = _ensure_utc(now or datetime.now(UTC))
        snapshot = self._repository.read()
        candidate = snapshot.worktree_cleanup_candidates.get(normalized_candidate_id)
        if candidate is None:
            raise ValueError(f"cleanup candidate {normalized_candidate_id} does not exist")
        requeued: WorktreeCleanupCandidate | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal requeued
            current = state.worktree_cleanup_candidates.get(normalized_candidate_id)
            if current is None:
                raise ValueError(f"cleanup candidate {normalized_candidate_id} does not exist")
            if current.state != WorktreeCleanupState.OPERATOR_ACTION_REQUIRED:
                raise ValueError(
                    f"cleanup candidate {normalized_candidate_id} is {current.state.value}; "
                    "only operator-action-required cleanup can be retried"
                )
            current.state = WorktreeCleanupState.PENDING
            current.automatic_retry_count = 0
            current.eligible_at = effective_now
            current.operator_action_required_at = None
            current.operator_action_reason = None
            current.last_operator_retry_at = effective_now
            current.last_operator_retry_by = normalized_actor
            current.last_operator_retry_reason = normalized_reason
            current.updated_at = effective_now
            requeued = current.model_copy(deep=True)
            return state

        activity = TeamDispatchActivity(
            id=f"activity-worktree-cleanup-retry-{uuid4().hex}",
            tool_name="team_worktree_cleanup_retry",
            caller_session_id="admin-web",
            caller_identity=normalized_actor,
            agent_id=candidate.agent_id,
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="none",
            audit_record_id=candidate.id,
            correction_context_summary=(
                f"task={candidate.task_id}; repository={candidate.managed_repo_id}; "
                f"path={candidate.worktree_path}; branch={candidate.branch}; "
                f"reason={normalized_reason}"
            ),
            created_at=effective_now,
            updated_at=effective_now,
        )
        updated = self._repository.update_with_dispatch_activity(
            _mutate,
            activity=activity,
            expected_revision=expected_revision,
        )
        if requeued is None:  # pragma: no cover - guarded by the transaction above
            raise RuntimeError(f"cleanup candidate {normalized_candidate_id} was not requeued")
        return updated, requeued

    def _reconcile_claimed_candidate(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        now: datetime,
        retry_seconds: int,
        dry_run: bool,
    ) -> tuple[str, int, str | None]:
        try:
            repo_name, _worktree_name = _task_worktree_components(candidate.worktree_path)
        except ValueError:
            # The safety check below records the exact invalid path. Use the
            # managed repository ID only to serialize that refusal.
            repo_name = candidate.managed_repo_id
        with team_task_worktree_cleanup_lock(
            runtime_dir=self._repository.runtime_dir,
            session_id=candidate.session_id,
            repo_name=repo_name,
        ):
            # Both durable reads occur after acquiring the same coordinator
            # lock used by task-worktree preparation. The selected backend then
            # acquires its repository-local lock and repeats physical
            # validation twice before mutation.
            first_failure = _team_state_safety_failure(
                self._repository.read(),
                candidate,
            )
            if first_failure is not None:
                next_state = self._record_safety_failure(
                    candidate,
                    failure=first_failure,
                    now=now,
                    retry_seconds=retry_seconds,
                )
                return (
                    _failure_status(next_state),
                    0,
                    first_failure.message,
                )
            second_failure = _team_state_safety_failure(
                self._repository.read(),
                candidate,
            )
            if second_failure is not None:
                next_state = self._record_safety_failure(
                    candidate,
                    failure=second_failure,
                    now=now,
                    retry_seconds=retry_seconds,
                )
                return (
                    _failure_status(next_state),
                    0,
                    second_failure.message,
                )

            try:
                outcome = dict(self._backend.cleanup(candidate, dry_run=dry_run))
            except Exception as exc:
                failure = WorktreeCleanupSafetyFailure(
                    check="backend_cleanup_failed",
                    message=str(exc) or exc.__class__.__name__,
                    retryable=True,
                )
                next_state = self._record_safety_failure(
                    candidate,
                    failure=failure,
                    now=now,
                    retry_seconds=retry_seconds,
                )
                return _failure_status(next_state), 0, failure.message

            outcome_state = str(outcome.get("state") or "").strip()
            if outcome_state in {"removed", "already_absent"}:
                reclaimed = max(0, int(outcome.get("reclaimed_bytes") or 0))
                self._record_success(
                    candidate,
                    outcome=outcome,
                    now=now,
                )
                return "succeeded", reclaimed, None
            if outcome_state == "dry_run":
                self._record_dry_run(
                    candidate,
                    outcome=outcome,
                    now=now,
                    retry_seconds=retry_seconds,
                )
                return "dry_run", 0, None
            failed_check = str(outcome.get("failed_check") or "backend_cleanup_refused").strip()
            message = _backend_refusal_message(outcome)
            next_state = self._record_backend_refusal(
                candidate,
                outcome=outcome,
                failed_check=failed_check,
                message=message,
                now=now,
                retry_seconds=retry_seconds,
            )
            return _failure_status(next_state), 0, message

    def _claim_next(
        self,
        now: datetime,
        *,
        excluded_ids: set[str],
    ) -> WorktreeCleanupCandidate | None:
        claimed: WorktreeCleanupCandidate | None = None
        snapshot = self._repository.read()
        if not any(
            candidate.state == WorktreeCleanupState.PENDING
            and candidate.eligible_at <= now
            and candidate.id not in excluded_ids
            for candidate in snapshot.worktree_cleanup_candidates.values()
        ):
            return None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal claimed
            eligible = sorted(
                (
                    candidate
                    for candidate in state.worktree_cleanup_candidates.values()
                    if candidate.state == WorktreeCleanupState.PENDING
                    and candidate.eligible_at <= now
                    and candidate.id not in excluded_ids
                ),
                key=lambda item: (item.eligible_at, item.id),
            )
            if not eligible:
                return state
            candidate = eligible[0]
            candidate.state = WorktreeCleanupState.PROCESSING
            candidate.attempt_count += 1
            candidate.updated_at = now
            claimed = candidate.model_copy(deep=True)
            return state

        self._repository.update(_mutate)
        return claimed

    def _record_safety_failure(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        failure: WorktreeCleanupSafetyFailure,
        now: datetime,
        retry_seconds: int,
    ) -> WorktreeCleanupState:
        next_state, retry_delay, operator_reason = self._failure_disposition(
            candidate,
            retryable=failure.retryable,
            failed_check=failure.check,
            retry_seconds=retry_seconds,
        )
        self._update_failed_candidate(
            candidate,
            state=next_state,
            failed_check=failure.check,
            message=failure.message,
            inventory=[],
            ignored_bytes=0,
            worktree_bytes=0,
            now=now,
            retry_delay_seconds=retry_delay,
            operator_reason=operator_reason,
        )
        return next_state

    def _record_backend_refusal(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        outcome: Mapping[str, Any],
        failed_check: str,
        message: str,
        now: datetime,
        retry_seconds: int,
    ) -> WorktreeCleanupState:
        inventory = _string_list(outcome.get("ignored_paths"))
        next_state, retry_delay, operator_reason = self._failure_disposition(
            candidate,
            retryable=failed_check in WORKTREE_CLEANUP_TRANSIENT_FAILURE_CHECKS,
            failed_check=failed_check,
            retry_seconds=retry_seconds,
        )
        self._update_failed_candidate(
            candidate,
            state=next_state,
            failed_check=failed_check,
            message=message,
            inventory=inventory,
            ignored_bytes=max(0, int(outcome.get("ignored_bytes") or 0)),
            worktree_bytes=max(0, int(outcome.get("worktree_bytes") or 0)),
            now=now,
            retry_delay_seconds=retry_delay,
            operator_reason=operator_reason,
        )
        return next_state

    @staticmethod
    def _failure_disposition(
        candidate: WorktreeCleanupCandidate,
        *,
        retryable: bool,
        failed_check: str,
        retry_seconds: int,
    ) -> tuple[WorktreeCleanupState, int, str | None]:
        next_retry_count = candidate.automatic_retry_count + 1
        if not retryable:
            return (
                WorktreeCleanupState.OPERATOR_ACTION_REQUIRED,
                0,
                failed_check,
            )
        if next_retry_count >= DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS:
            return (
                WorktreeCleanupState.OPERATOR_ACTION_REQUIRED,
                0,
                "automatic_retry_exhausted",
            )
        return (
            WorktreeCleanupState.PENDING,
            _automatic_retry_delay_seconds(
                retry_seconds,
                automatic_retry_count=next_retry_count,
            ),
            None,
        )

    def _update_failed_candidate(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        state: WorktreeCleanupState,
        failed_check: str,
        message: str,
        inventory: list[str],
        ignored_bytes: int,
        worktree_bytes: int,
        now: datetime,
        retry_delay_seconds: int,
        operator_reason: str | None,
    ) -> None:
        def _mutate(team_state: TeamState) -> TeamState:
            current = team_state.worktree_cleanup_candidates[candidate.id]
            if current.state != WorktreeCleanupState.PROCESSING:
                raise RuntimeError(f"cleanup candidate {candidate.id} is no longer processing")
            current.state = state
            current.last_safety_check = failed_check
            current.last_error = message
            current.automatic_retry_count += 1
            if state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED:
                current.operator_action_required_at = now
                current.operator_action_reason = operator_reason or failed_check
            else:
                current.operator_action_required_at = None
                current.operator_action_reason = None
            _set_ignored_file_evidence(current, inventory)
            current.ignored_bytes = ignored_bytes
            current.worktree_bytes = worktree_bytes
            current.eligible_at = now + timedelta(seconds=max(0, int(retry_delay_seconds)))
            current.updated_at = now
            return team_state

        self._repository.update_with_dispatch_activity(
            _mutate,
            activity=self._audit_activity(
                candidate,
                status=TeamDispatchActivityStatus.FAILURE,
                failed_check=failed_check,
                message=message,
                now=now,
            ),
        )

    def _record_success(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        outcome: Mapping[str, Any],
        now: datetime,
    ) -> None:
        inventory = _string_list(outcome.get("ignored_paths"))
        ignored_bytes = max(0, int(outcome.get("ignored_bytes") or 0))
        worktree_bytes = max(0, int(outcome.get("worktree_bytes") or 0))
        reclaimed_bytes = max(0, int(outcome.get("reclaimed_bytes") or 0))

        def _mutate(state: TeamState) -> TeamState:
            current = state.worktree_cleanup_candidates[candidate.id]
            if current.state != WorktreeCleanupState.PROCESSING:
                raise RuntimeError(f"cleanup candidate {candidate.id} is no longer processing")
            current.state = WorktreeCleanupState.SUCCEEDED
            current.last_safety_check = "passed"
            current.last_error = None
            current.automatic_retry_count = 0
            current.operator_action_required_at = None
            current.operator_action_reason = None
            _set_ignored_file_evidence(current, inventory)
            current.ignored_bytes = ignored_bytes
            current.worktree_bytes = worktree_bytes
            current.reclaimed_bytes = reclaimed_bytes
            current.completed_at = now
            current.updated_at = now
            _prune_cleaned_git_context_metadata(state, current)
            return state

        self._repository.update_with_dispatch_activity(
            _mutate,
            activity=self._audit_activity(
                candidate,
                status=TeamDispatchActivityStatus.SUCCESS,
                failed_check=None,
                message=str(outcome.get("message") or "task worktree cleanup succeeded"),
                now=now,
            ),
        )

    def _record_dry_run(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        outcome: Mapping[str, Any],
        now: datetime,
        retry_seconds: int,
    ) -> None:
        inventory = _string_list(outcome.get("ignored_paths"))
        ignored_bytes = max(0, int(outcome.get("ignored_bytes") or 0))
        worktree_bytes = max(0, int(outcome.get("worktree_bytes") or 0))

        def _mutate(state: TeamState) -> TeamState:
            current = state.worktree_cleanup_candidates[candidate.id]
            if current.state != WorktreeCleanupState.PROCESSING:
                raise RuntimeError(f"cleanup candidate {candidate.id} is no longer processing")
            current.state = WorktreeCleanupState.PENDING
            current.last_safety_check = "dry_run_safe"
            current.last_error = None
            current.automatic_retry_count = 0
            current.operator_action_required_at = None
            current.operator_action_reason = None
            _set_ignored_file_evidence(current, inventory)
            current.ignored_bytes = ignored_bytes
            current.worktree_bytes = worktree_bytes
            current.eligible_at = now + timedelta(seconds=max(0, int(retry_seconds)))
            current.updated_at = now
            return state

        self._repository.update_with_dispatch_activity(
            _mutate,
            activity=self._audit_activity(
                candidate,
                status=TeamDispatchActivityStatus.SUCCESS,
                failed_check=None,
                message=str(outcome.get("message") or "task worktree dry-run passed"),
                now=now,
            ),
        )

    def _audit_activity(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        status: TeamDispatchActivityStatus,
        failed_check: str | None,
        message: str,
        now: datetime,
    ) -> TeamDispatchActivity:
        return TeamDispatchActivity(
            id=f"activity-worktree-cleanup-{uuid4().hex}",
            tool_name="team_worktree_cleanup",
            caller_session_id=candidate.session_id,
            caller_identity="system:team-worktree-cleanup",
            agent_id=candidate.agent_id,
            status=status,
            progress_classification="none",
            audit_record_id=candidate.id,
            error_code=failed_check,
            error_message=(message if status == TeamDispatchActivityStatus.FAILURE else None),
            correction_context_summary=(
                f"task={candidate.task_id}; repository={candidate.managed_repo_id}; "
                f"path={candidate.worktree_path}; branch={candidate.branch}; "
                f"check={failed_check or 'passed'}"
            ),
            created_at=now,
            updated_at=now,
        )

def _team_state_safety_failure(
    state: TeamState,
    candidate: WorktreeCleanupCandidate,
) -> WorktreeCleanupSafetyFailure | None:
    current = state.worktree_cleanup_candidates.get(candidate.id)
    if current is None:
        return WorktreeCleanupSafetyFailure(
            check="candidate_missing",
            message=f"cleanup candidate {candidate.id} no longer exists",
            retryable=False,
        )
    if current.state != WorktreeCleanupState.PROCESSING:
        return WorktreeCleanupSafetyFailure(
            check="candidate_not_processing",
            message=f"cleanup candidate {candidate.id} is {current.state.value}",
            retryable=False,
        )
    task = state.tasks.get(candidate.task_id)
    if task is None:
        return WorktreeCleanupSafetyFailure(
            check="task_missing",
            message=f"cleanup task {candidate.task_id} no longer exists",
            retryable=False,
        )
    project = state.projects.get(candidate.project_id)
    if project is None:
        return WorktreeCleanupSafetyFailure(
            check="project_missing",
            message=f"cleanup project {candidate.project_id} no longer exists",
            retryable=False,
        )
    try:
        resolved = (
            TaskAssessmentService(state).assess(task.id).resolution_status
            == TaskResolutionStatus.RESOLVED
        )
    except Exception:
        resolved = False
    reassigned = task.owner_agent_id != candidate.agent_id
    context = state.git_work_contexts.get(candidate.git_work_context_id)
    preparation_invalidated = bool(
        context is not None
        and _context_preparation_was_invalidated(state, context)
    )
    if not resolved and not reassigned and not preparation_invalidated:
        return WorktreeCleanupSafetyFailure(
            check="task_not_cleanup_eligible",
            message=(
                f"task {task.id} is neither resolved, reassigned away from "
                f"agent {candidate.agent_id}, nor prepared under an invalidated generation"
            ),
            retryable=True,
        )
    active_dispatch = next(
        (
            dispatch
            for dispatch in state.dispatches.values()
            if dispatch.status in _ACTIVE_DISPATCH_STATES
            and dispatch.agent_id == candidate.agent_id
            and dispatch.session_id == candidate.session_id
            and candidate.task_id in dispatch.task_ids
        ),
        None,
    )
    if active_dispatch is not None:
        return WorktreeCleanupSafetyFailure(
            check="active_dispatch_reference",
            message=(
                f"dispatch {active_dispatch.id} is {active_dispatch.status.value} "
                f"and still references task {candidate.task_id}"
            ),
            retryable=True,
        )
    active_context = next(
        (
            context
            for context in state.git_work_contexts.values()
            if context.state == GitWorkContextState.ACTIVE
            and context.agent_id == candidate.agent_id
            and context.worktree_path is not None
            and _cleanup_worktree_path_identity(context.worktree_path)
            == _cleanup_worktree_path_identity(candidate.worktree_path)
            and context.agent_branch == candidate.branch
            and _git_work_context_session_id(state, context) == candidate.session_id
        ),
        None,
    )
    if active_context is not None:
        return WorktreeCleanupSafetyFailure(
            check="active_git_context_reference",
            message=(
                f"active Git context {active_context.id} still references {candidate.worktree_path}"
            ),
            retryable=True,
        )
    try:
        _task_worktree_components(candidate.worktree_path)
    except ValueError as exc:
        return WorktreeCleanupSafetyFailure(
            check="worktree_path_mismatch",
            message=str(exc),
            retryable=False,
        )
    try:
        expected = _required_expected_commit_sha(candidate)
    except ValueError as exc:
        return WorktreeCleanupSafetyFailure(
            check="expected_head_missing",
            message=str(exc),
            retryable=False,
        )
    retained = candidate.retained_commit_sha
    if retained is not None:
        retained = retained.casefold()
        matching_commits = {
            value.casefold()
            for deliverable in state.deliverables.values()
            if deliverable.task_id == candidate.task_id
            and deliverable.producer_agent_id == candidate.agent_id
            and deliverable.git_refs.agent_branch == candidate.branch
            for value in (
                deliverable.git_refs.submitted_commit_sha,
                deliverable.git_refs.commit_sha,
                deliverable.git_refs.reviewed_commit_sha,
                deliverable.git_refs.feature_commit_sha,
                deliverable.git_refs.release_commit_sha,
            )
            if value is not None
        }
        if retained not in matching_commits or retained != expected:
            return WorktreeCleanupSafetyFailure(
                check="retained_commit_not_referenced",
                message=(
                    f"terminal deliverable no longer retains expected commit "
                    f"{candidate.retained_commit_sha}"
                ),
                retryable=False,
            )
    return None


def _expected_commits_for_context(
    state: TeamState,
    context: GitWorkContext,
) -> tuple[str | None, str | None]:
    submitted = {
        value.casefold()
        for deliverable in state.deliverables.values()
        if deliverable.task_id == context.task_id
        and deliverable.producer_agent_id == context.agent_id
        and deliverable.git_refs.branch_context_id == context.id
        for value in (
            deliverable.git_refs.submitted_commit_sha,
            deliverable.git_refs.commit_sha,
        )
        if value is not None and _FULL_GIT_OBJECT_ID.fullmatch(value.casefold())
    }
    if len(submitted) == 1:
        retained = next(iter(submitted))
        return retained, retained
    if len(submitted) > 1:
        return None, None
    fallback = context.commit_sha or context.base_commit
    if fallback is not None:
        fallback = fallback.casefold()
        if _FULL_GIT_OBJECT_ID.fullmatch(fallback):
            return fallback, None
    return None, None


def _context_preparation_was_invalidated(
    state: TeamState,
    context: GitWorkContext,
) -> bool:
    if context.state != GitWorkContextState.ABANDONED:
        return False
    if context.preparation_generation is not None:
        try:
            if context.preparation_generation != task_git_work_context_generation(
                state,
                context.task_id,
            ):
                return True
        except Exception:
            pass
    return any(
        item.task_id == context.task_id
        and item.payload.get("kind") == "task_preparation_invalidated"
        and context.id
        in (item.payload.get("invalidated_git_work_context_ids") or [])
        for item in state.inbox.values()
    )


def _prune_cleaned_git_context_metadata(
    state: TeamState,
    candidate: WorktreeCleanupCandidate,
) -> None:
    referenced = {
        context_id
        for deliverable in state.deliverables.values()
        if (context_id := deliverable.git_refs.branch_context_id) is not None
    }
    prunable = [
        context.id
        for context in state.git_work_contexts.values()
        if context.id not in referenced
        and context.state != GitWorkContextState.ACTIVE
        and context.id == candidate.git_work_context_id
    ]
    for context_id in prunable:
        del state.git_work_contexts[context_id]


def _cleanup_candidate_identity(
    *,
    task_id: str,
    git_work_context_id: str,
    managed_repo_id: str,
    session_id: str,
    worktree_path: str,
) -> str:
    material = "\0".join(
        (
            task_id,
            git_work_context_id,
            managed_repo_id,
            session_id,
            worktree_path,
        )
    ).encode("utf-8")
    return f"worktree-cleanup-{hashlib.sha256(material).hexdigest()[:32]}"


def _cleanup_candidate_for_context(
    state: TeamState,
    *,
    context: GitWorkContext,
) -> WorktreeCleanupCandidate | None:
    matching = sorted(
        (
            candidate
            for candidate in state.worktree_cleanup_candidates.values()
            if candidate.task_id == context.task_id
            and candidate.agent_id == context.agent_id
            and candidate.git_work_context_id == context.id
            and context.worktree_path is not None
            and _cleanup_worktree_path_identity(candidate.worktree_path)
            == _cleanup_worktree_path_identity(context.worktree_path)
            and candidate.branch == context.agent_branch
        ),
        key=lambda candidate: (_ensure_utc(candidate.created_at), candidate.id),
    )
    return matching[0] if matching else None


def _cleanup_context_ids_to_enqueue(state: TeamState) -> set[str]:
    newest_by_lineage = _newest_cleanup_contexts_by_lineage(state)

    nonterminal_lineages: set[tuple[str, str, str, str]] = set()
    for candidate in state.worktree_cleanup_candidates.values():
        if candidate.state in _TERMINAL_CLEANUP_STATES:
            continue
        lineage = _cleanup_candidate_lineage(candidate)
        newest_context = newest_by_lineage.get(lineage)
        stale_mismatch_generation = (
            newest_context is not None
            and newest_context.id != candidate.git_work_context_id
            and candidate.state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
            and candidate.last_safety_check in _STALE_GENERATION_FAILURE_CHECKS
        )
        if not stale_mismatch_generation:
            nonterminal_lineages.add(lineage)

    candidate_context_ids = {
        candidate.git_work_context_id
        for candidate in state.worktree_cleanup_candidates.values()
    }
    return {
        context.id
        for lineage, context in newest_by_lineage.items()
        if context.id in candidate_context_ids or lineage not in nonterminal_lineages
    }


def _newest_cleanup_contexts_by_lineage(
    state: TeamState,
) -> dict[tuple[str, str, str, str], GitWorkContext]:
    newest_by_lineage: dict[tuple[str, str, str, str], GitWorkContext] = {}
    for context in state.git_work_contexts.values():
        if context.worktree_path is None:
            continue
        session_id = _git_work_context_session_id(state, context)
        if session_id is None:
            continue
        lineage = (
            context.agent_id,
            session_id,
            _cleanup_worktree_path_identity(context.worktree_path),
            context.agent_branch,
        )
        current = newest_by_lineage.get(lineage)
        if current is None or (
            _ensure_utc(context.created_at),
            _ensure_utc(context.updated_at),
            context.id,
        ) > (
            _ensure_utc(current.created_at),
            _ensure_utc(current.updated_at),
            current.id,
        ):
            newest_by_lineage[lineage] = context
    return newest_by_lineage


def _cleanup_candidate_lineage(
    candidate: WorktreeCleanupCandidate,
) -> tuple[str, str, str, str]:
    return (
        candidate.agent_id,
        candidate.session_id,
        _cleanup_worktree_path_identity(candidate.worktree_path),
        candidate.branch,
    )


def _supersede_stale_cleanup_generations(
    state: TeamState,
    *,
    now: datetime,
) -> None:
    newest_by_lineage = _newest_cleanup_contexts_by_lineage(state)
    for candidate in state.worktree_cleanup_candidates.values():
        if (
            candidate.state != WorktreeCleanupState.OPERATOR_ACTION_REQUIRED
            or candidate.last_safety_check not in _STALE_GENERATION_FAILURE_CHECKS
        ):
            continue
        newest_context = newest_by_lineage.get(_cleanup_candidate_lineage(candidate))
        if newest_context is None or newest_context.id == candidate.git_work_context_id:
            continue
        candidate.state = WorktreeCleanupState.SUPERSEDED
        candidate.superseded_by_git_work_context_id = newest_context.id
        candidate.completed_at = now
        candidate.updated_at = now


def _cleanup_candidate_with_context_identity(
    state: TeamState,
    *,
    context: GitWorkContext,
    session_id: str,
    managed_repo_id: str,
) -> WorktreeCleanupCandidate | None:
    matching = sorted(
        (
            candidate
            for candidate in state.worktree_cleanup_candidates.values()
            if candidate.task_id == context.task_id
            and candidate.agent_id == context.agent_id
            and candidate.session_id == session_id
            and candidate.git_work_context_id == context.id
            and candidate.managed_repo_id == managed_repo_id
            and context.worktree_path is not None
            and _cleanup_worktree_path_identity(candidate.worktree_path)
            == _cleanup_worktree_path_identity(context.worktree_path)
            and candidate.branch == context.agent_branch
        ),
        key=lambda candidate: (_ensure_utc(candidate.created_at), candidate.id),
    )
    return matching[0] if matching else None


def _git_work_context_session_id(
    state: TeamState,
    context: GitWorkContext,
) -> str | None:
    if context.dispatch_id is not None:
        dispatch = state.dispatches.get(context.dispatch_id)
        if (
            dispatch is None
            or dispatch.agent_id != context.agent_id
            or context.task_id not in dispatch.task_ids
        ):
            return None
        return dispatch.session_id
    agent = state.agents.get(context.agent_id)
    return agent.session_id if agent is not None else None


def _cleanup_worktree_path_identity(worktree_path: str) -> str:
    return str(PurePosixPath(worktree_path))


@contextmanager
def team_task_worktree_cleanup_lock(
    *,
    runtime_dir: str | Path,
    session_id: str,
    repo_name: str,
) -> Iterator[None]:
    """Serialize Team preparation and reconciliation for one session repository."""

    material = "\0".join((session_id, repo_name)).encode("utf-8")
    lock_name = f"{hashlib.sha256(material).hexdigest()}.lock"
    lock_root = Path(runtime_dir) / "team-worktree-locks"
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_root.chmod(0o700)
    lock_path = lock_root / lock_name
    with lock_path.open("a+", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _task_worktree_components(worktree_path: str) -> tuple[str, str]:
    path = PurePosixPath(str(worktree_path or "").strip())
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("task worktree path must be an absolute contained path")
    parts = path.parts
    expected_prefix = ("/", "home", "agent", "worktrees")
    if len(parts) != 6 or parts[:4] != expected_prefix:
        raise ValueError(
            "task worktree path must match /home/agent/worktrees/<repository>/<worktree>"
        )
    repo_name, worktree_name = parts[4], parts[5]
    if not repo_name or not worktree_name:
        raise ValueError("task worktree path is missing repository or worktree name")
    return repo_name, worktree_name


def _required_expected_commit_sha(candidate: WorktreeCleanupCandidate) -> str:
    expected = str(candidate.expected_head or "").strip().casefold()
    if not _FULL_GIT_OBJECT_ID.fullmatch(expected):
        raise ValueError(f"cleanup candidate {candidate.id} requires a full expected Git object ID")
    return expected


def _libvirt_task_worktree_lock_path(repo_name: str) -> str:
    return f"/home/agent/.evander/locks/task-worktree-{repo_name}.lock"


def _last_json_object(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return dict(payload)
    raise RuntimeError("task worktree cleanup did not return a JSON result")


def _backend_refusal_message(outcome: Mapping[str, Any]) -> str:
    message = str(outcome.get("message") or "task worktree cleanup was refused").strip()
    details: list[str] = []
    for key in ("actual_branch", "actual_commit_sha"):
        value = str(outcome.get(key) or "").strip()
        if value:
            details.append(f"{key}={value}")
    for key in ("tracked_paths", "untracked_paths"):
        paths = _string_list(outcome.get(key))
        if not paths:
            continue
        shown = paths[:20]
        suffix = f" (+{len(paths) - len(shown)} more)" if len(paths) > len(shown) else ""
        details.append(f"{key}={json.dumps(shown, separators=(',', ':'))}{suffix}")
    return f"{message}; {'; '.join(details)}" if details else message


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _automatic_retry_delay_seconds(
    retry_seconds: int,
    *,
    automatic_retry_count: int,
) -> int:
    base_delay = max(0, int(retry_seconds))
    if base_delay == 0:
        return 0
    exponent = max(0, min(int(automatic_retry_count) - 1, 30))
    return min(DEFAULT_CLEANUP_MAX_RETRY_SECONDS, base_delay * (1 << exponent))


def _failure_status(state: WorktreeCleanupState) -> str:
    if state == WorktreeCleanupState.PENDING:
        return "deferred"
    if state == WorktreeCleanupState.OPERATOR_ACTION_REQUIRED:
        return "operator_action_required"
    return "recovery_required"


__all__ = [
    "DEFAULT_CLEANUP_GRACE_SECONDS",
    "DEFAULT_CLEANUP_MAX_AUTOMATIC_ATTEMPTS",
    "DEFAULT_CLEANUP_MAX_RETRY_SECONDS",
    "DEFAULT_CLEANUP_RECOVERY_SECONDS",
    "DEFAULT_CLEANUP_RETRY_SECONDS",
    "EffectTaskWorktreeCleanupBackend",
    "TaskWorktreeCleanupBackend",
    "TeamWorktreeCleanupService",
    "WorktreeCleanupReconcileResult",
    "WorktreeCleanupSafetyFailure",
    "team_task_worktree_cleanup_lock",
]
