from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from ...models import (
    AgentTask,
    DependencyCompositionRecovery,
    DependencyCompositionRecoveryState,
    DependencyGitSource,
    Deliverable,
    PersistentAgent,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerState,
    TeamInboxPriority,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
    TaskCancellation,
    TaskDeliverableContract,
    agent_has_project_assignment,
)
from .agent_access import _supervises
from .blockers import append_blocker_event, resolve_blocker
from .common import _optional_text, _required_text
from .preparation_failures import task_preparation_generation
from .review_handoff import queue_actionable_inbox_item
from .work_due import _mark_agent_work_due


DEPENDENCY_COMPOSITION_CONFLICT_REASON_CODE = "dependency_git_composition_conflict"
DEPENDENCY_COMPOSITION_RECOVERY_ACTOR = "system:dependency-composition-recovery"

DependencyRecoveryCommitVerifier = Callable[
    [DependencyCompositionRecovery, str, str, str],
    None,
]


def dependency_composition_source_fingerprint(
    *,
    managed_repo_id: str,
    directory_path: str,
    base_branch: str,
    base_commit_sha: str,
    base_tree_hash: str,
    dependency_git_sources: Sequence[Mapping[str, object] | DependencyGitSource],
) -> str:
    sources = [
        source.model_dump(mode="json")
        if isinstance(source, DependencyGitSource)
        else DependencyGitSource.model_validate(source).model_dump(mode="json")
        for source in dependency_git_sources
    ]
    material = json.dumps(
        {
            "managed_repo_id": managed_repo_id,
            "directory_path": directory_path,
            "base_branch": base_branch,
            "base_commit_sha": base_commit_sha.casefold(),
            "base_tree_hash": base_tree_hash.casefold(),
            "dependency_git_sources": sources,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record_dependency_composition_conflict(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    conflict: Mapping[str, object],
    now: datetime,
) -> DependencyCompositionRecovery:
    task_id = _required_text(conflict.get("target_task_id"), "target_task_id")
    if blocker.task_id != task_id:
        raise ValueError(
            f"dependency conflict task {task_id} does not match blocker {blocker.id}"
        )
    sources = [
        DependencyGitSource.model_validate(source)
        for source in conflict.get("dependency_git_sources", [])
        if isinstance(source, Mapping)
    ]
    if not sources:
        raise ValueError("dependency composition conflict requires dependency sources")
    fingerprint = dependency_composition_source_fingerprint(
        managed_repo_id=_required_text(conflict.get("managed_repo_id"), "managed_repo_id"),
        directory_path=_required_text(conflict.get("directory_path"), "directory_path"),
        base_branch=_required_text(conflict.get("base_branch"), "base_branch"),
        base_commit_sha=_required_text(conflict.get("base_commit_sha"), "base_commit_sha"),
        base_tree_hash=_required_text(conflict.get("base_tree_hash"), "base_tree_hash"),
        dependency_git_sources=sources,
    )
    preparation_generation = task_preparation_generation(state, task_id)
    existing = next(
        (
            item
            for item in state.dependency_composition_recoveries.values()
            if item.target_task_id == task_id
            and item.source_fingerprint == fingerprint
            and item.preparation_generation == preparation_generation
            and item.state
            not in {
                DependencyCompositionRecoveryState.CANCELLED,
                DependencyCompositionRecoveryState.SUPERSEDED,
            }
        ),
        None,
    )
    if existing is not None:
        return existing
    recovery_id = (
        "depcomp-"
        + hashlib.sha256(
            f"{task_id}\0{fingerprint}\0{preparation_generation}".encode()
        ).hexdigest()[:24]
    )
    recovery = DependencyCompositionRecovery(
        id=recovery_id,
        blocker_id=blocker.id,
        target_task_id=task_id,
        project_id=_required_text(conflict.get("project_id"), "project_id"),
        managed_repo_id=_required_text(conflict.get("managed_repo_id"), "managed_repo_id"),
        directory_path=_required_text(conflict.get("directory_path"), "directory_path"),
        base_branch=_required_text(conflict.get("base_branch"), "base_branch"),
        base_commit_sha=_required_text(conflict.get("base_commit_sha"), "base_commit_sha"),
        base_tree_hash=_required_text(conflict.get("base_tree_hash"), "base_tree_hash"),
        dependency_git_sources=sources,
        source_fingerprint=fingerprint,
        preparation_generation=preparation_generation,
        failed_dependency_task_id=_required_text(
            conflict.get("failed_dependency_task_id"),
            "failed_dependency_task_id",
        ),
        failed_deliverable_id=_required_text(
            conflict.get("failed_deliverable_id"),
            "failed_deliverable_id",
        ),
        conflict_paths=[str(path) for path in conflict.get("conflict_paths", [])],
        created_at=now,
        updated_at=now,
    )
    state.dependency_composition_recoveries[recovery.id] = recovery
    append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.PROGRESS,
        note=(
            "Captured immutable dependency composition conflict evidence; "
            "a supervisor must start the generated recovery task."
        ),
        reference_ids=[recovery.id],
        metadata={
            "kind": "dependency_composition_conflict_captured",
            "recovery_id": recovery.id,
            "source_fingerprint": recovery.source_fingerprint,
            "failed_dependency_task_id": recovery.failed_dependency_task_id,
            "failed_deliverable_id": recovery.failed_deliverable_id,
            "conflict_paths": list(recovery.conflict_paths),
        },
        now=now,
    )
    return recovery


def recovery_for_task(
    state: TeamState,
    *,
    recovery_task_id: str,
) -> DependencyCompositionRecovery | None:
    matches = [
        recovery
        for recovery in state.dependency_composition_recoveries.values()
        if recovery.recovery_task_id == recovery_task_id
        and recovery.state
        in {
            DependencyCompositionRecoveryState.IN_PROGRESS,
            DependencyCompositionRecoveryState.ACCEPTED,
        }
    ]
    if len(matches) > 1:
        raise ValueError(
            f"task {recovery_task_id} references multiple dependency recoveries"
        )
    return matches[0] if matches else None


def accepted_recovery_for_target(
    state: TeamState,
    *,
    target_task_id: str,
    dependency_git_sources: Sequence[Mapping[str, object] | DependencyGitSource],
) -> DependencyCompositionRecovery | None:
    expected_sources = [
        source.model_dump(mode="json")
        if isinstance(source, DependencyGitSource)
        else DependencyGitSource.model_validate(source).model_dump(mode="json")
        for source in dependency_git_sources
    ]
    candidates = [
        recovery
        for recovery in state.dependency_composition_recoveries.values()
        if recovery.target_task_id == target_task_id
        and recovery.state == DependencyCompositionRecoveryState.ACCEPTED
        and [source.model_dump(mode="json") for source in recovery.dependency_git_sources]
        == expected_sources
        and recovery.preparation_generation
        == task_preparation_generation(state, target_task_id)
    ]
    if len(candidates) > 1:
        raise ValueError(
            f"task {target_task_id} has multiple accepted dependency recoveries"
        )
    return candidates[0] if candidates else None


def _resolver_assignment_error(
    state: TeamState,
    *,
    actor: PersistentAgent | None,
    resolver: PersistentAgent,
    recovery: DependencyCompositionRecovery,
) -> str | None:
    project = state.projects.get(recovery.project_id)
    if project is None:
        return f"recovery project {recovery.project_id} does not exist"
    if resolver.team_id != project.team_id:
        return "dependency recovery resolver must belong to the project Team"
    if resolver.status.value != "active":
        return f"dependency recovery resolver {resolver.id} is not active"
    if actor is not None and actor.team_id != project.team_id:
        return "dependency recovery supervisor must belong to the project Team"
    if (
        actor is not None
        and resolver.id != actor.id
        and not _supervises(state, actor.id, resolver.id)
    ):
        return f"agent {actor.id} cannot assign recovery work to {resolver.id}"
    if not agent_has_project_assignment(state, resolver.id, recovery.project_id):
        return f"resolver {resolver.id} is not assigned to project {recovery.project_id}"
    return None


def start_dependency_composition_recovery(
    state: TeamState,
    *,
    recovery: DependencyCompositionRecovery,
    blocker: TeamBlocker,
    actor: PersistentAgent | None,
    operator_actor_id: str | None = None,
    resolver: PersistentAgent,
    dispatch_id: str | None,
    result: dict[str, Any],
    now: datetime,
) -> AgentTask:
    if actor is None and _optional_text(operator_actor_id) is None:
        raise ValueError("dependency recovery requires a supervisor or operator actor")
    if blocker.state != TeamBlockerState.OPEN:
        raise ValueError(f"blocker {blocker.id} is not open")
    if recovery.blocker_id != blocker.id:
        raise ValueError(f"recovery {recovery.id} does not belong to blocker {blocker.id}")
    if recovery.state == DependencyCompositionRecoveryState.IN_PROGRESS:
        existing = state.tasks.get(recovery.recovery_task_id or "")
        if existing is None:
            raise ValueError(f"recovery {recovery.id} references a missing recovery task")
        return existing
    if recovery.state != DependencyCompositionRecoveryState.AWAITING_ASSIGNMENT:
        raise ValueError(f"recovery {recovery.id} is {recovery.state.value}")
    assignment_error = _resolver_assignment_error(
        state,
        actor=actor,
        resolver=resolver,
        recovery=recovery,
    )
    if assignment_error is not None:
        raise ValueError(assignment_error)
    target = state.tasks.get(recovery.target_task_id)
    if target is None:
        raise ValueError(f"recovery target task {recovery.target_task_id} does not exist")
    if recovery.preparation_generation != task_preparation_generation(state, target.id):
        raise ValueError(
            f"dependency recovery {recovery.id} is stale for task {target.id}"
        )
    contribution_task_ids = list(
        dict.fromkeys(source.dependency_task_id for source in recovery.dependency_git_sources)
    )
    recovery_task_id = f"{target.id}-merge-recovery-{recovery.source_fingerprint[:12]}"
    existing = state.tasks.get(recovery_task_id)
    if existing is not None:
        if existing.created_by_actor != DEPENDENCY_COMPOSITION_RECOVERY_ACTOR:
            raise ValueError(f"reserved recovery task id {recovery_task_id} is already in use")
        recovery.recovery_task_id = existing.id
        recovery.state = DependencyCompositionRecoveryState.IN_PROGRESS
        recovery.updated_at = now
        return existing
    source_lines = "\n".join(
        f"- {source.dependency_task_id} / {source.deliverable_id}: "
        f"{source.branch} at {source.commit_sha}"
        for source in recovery.dependency_git_sources
    )
    conflict_line = (
        ", ".join(recovery.conflict_paths)
        if recovery.conflict_paths
        else "Git did not report conflicted paths"
    )
    task = AgentTask(
        id=recovery_task_id,
        owner_agent_id=resolver.id,
        project_id=recovery.project_id,
        priority=target.priority,
        state=TeamTaskState.PENDING,
        task_kind=TeamTaskKind.SUPPORT,
        integration_boundary=True,
        deliverable_contract=TaskDeliverableContract(
            require_git_provenance=True,
            required_contribution_task_ids=contribution_task_ids,
        ),
        title=f"Resolve dependency merge for {target.id}",
        description=(
            f"Resolve the dependency composition conflict blocking task {target.id}.\n\n"
            f"Immutable base: {recovery.base_branch} at {recovery.base_commit_sha}\n"
            f"Base tree: {recovery.base_tree_hash}\n"
            f"Conflict paths: {conflict_line}\n"
            "Merge these exact accepted inputs in the listed order, resolve semantic "
            "conflicts, run focused verification, and submit the merged Git deliverable "
            "with every listed source_deliverable_id. Do not substitute or rebase a source.\n"
            f"{source_lines}"
        ),
        created_by_actor=DEPENDENCY_COMPOSITION_RECOVERY_ACTOR,
        parent_task_id=target.id,
        originating_task_id=target.id,
        git_base_branch=recovery.base_branch,
        git_base_commit_sha=recovery.base_commit_sha,
        dependency_task_ids=contribution_task_ids,
        created_at=now,
        updated_at=now,
        state_entered_at=now,
    )
    state.tasks[task.id] = task
    recovery.recovery_task_id = task.id
    recovery.state = DependencyCompositionRecoveryState.IN_PROGRESS
    recovery.updated_at = now
    blocker.last_progress_at = now
    blocker.updated_at = now
    event = append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.PROGRESS,
        actor_agent_id=actor.id if actor is not None else None,
        dispatch_id=dispatch_id,
        note=f"Created pinned dependency merge recovery task {task.id} for {resolver.id}.",
        reference_ids=[recovery.id, task.id],
        metadata={
            "kind": "dependency_composition_recovery_started",
            "recovery_id": recovery.id,
            "recovery_task_id": task.id,
            "resolver_agent_id": resolver.id,
            "operator_actor_id": _optional_text(operator_actor_id),
        },
        now=now,
    )
    queue_actionable_inbox_item(
        state,
        recipient_agent_id=resolver.id,
        sender_agent_id=actor.id if actor is not None else None,
        task_id=task.id,
        priority=TeamInboxPriority.HIGH,
        payload={
            "kind": "dependency_composition_recovery_work",
            "recovery_id": recovery.id,
            "blocker_id": blocker.id,
            "target_task_id": target.id,
            "operator_actor_id": _optional_text(operator_actor_id),
            "base_commit_sha": recovery.base_commit_sha,
            "base_tree_hash": recovery.base_tree_hash,
            "dependency_git_sources": [
                source.model_dump(mode="json")
                for source in recovery.dependency_git_sources
            ],
            "conflict_paths": list(recovery.conflict_paths),
            "next_action": (
                "Use the prepared manual-recovery worktree. Merge every exact pinned "
                "source, resolve conflicts, verify, and submit for normal review."
            ),
        },
        result=result,
        now=now,
    )
    result.setdefault("created_task_ids", []).append(task.id)
    result.setdefault("blocker_event_ids", []).append(event.id)
    _mark_agent_work_due(state, agent_id=resolver.id, now=now)
    return task


def verify_dependency_recovery_submission(
    state: TeamState,
    *,
    task_id: str,
    deliverable_id: str,
    branch: str,
    commit_sha: str,
    source_deliverable_ids: Sequence[str],
    verifier: DependencyRecoveryCommitVerifier | None,
    now: datetime,
) -> None:
    recovery = recovery_for_task(state, recovery_task_id=task_id)
    if recovery is None:
        return
    if recovery.state != DependencyCompositionRecoveryState.IN_PROGRESS:
        raise ValueError(f"dependency recovery {recovery.id} is not in progress")
    if recovery.preparation_generation != task_preparation_generation(
        state, recovery.target_task_id
    ):
        raise ValueError(f"dependency recovery {recovery.id} is stale")
    required_deliverable_ids = sorted(
        source.deliverable_id for source in recovery.dependency_git_sources
    )
    if sorted(set(source_deliverable_ids)) != required_deliverable_ids:
        raise ValueError(
            "dependency recovery deliverable must name the exact accepted source deliverables"
        )
    if verifier is None:
        raise ValueError("dependency recovery Git ancestry verification is unavailable")
    normalized_branch = _required_text(branch, "branch")
    normalized_commit = _required_text(commit_sha, "commit_sha").casefold()
    verifier(recovery, deliverable_id, normalized_branch, normalized_commit)
    recovery.verified_deliverable_id = deliverable_id
    recovery.verified_commit_sha = normalized_commit
    recovery.verified_at = now
    recovery.updated_at = now


def accept_dependency_composition_recovery(
    state: TeamState,
    *,
    task: AgentTask,
    deliverable: Deliverable,
    accepted_by_agent_id: str | None,
    dispatch_id: str | None,
    result: dict[str, Any] | None,
    now: datetime,
) -> DependencyCompositionRecovery | None:
    recovery = recovery_for_task(state, recovery_task_id=task.id)
    if recovery is None:
        return None
    commit_sha = _optional_text(deliverable.git_refs.commit_sha)
    if (
        recovery.verified_deliverable_id != deliverable.id
        or recovery.verified_commit_sha != commit_sha
    ):
        raise ValueError(
            f"dependency recovery deliverable {deliverable.id} lacks verified ancestry"
        )
    blocker = state.blockers.get(recovery.blocker_id)
    if blocker is None:
        raise ValueError(f"dependency recovery blocker {recovery.blocker_id} is missing")
    recovery.state = DependencyCompositionRecoveryState.ACCEPTED
    recovery.accepted_deliverable_id = deliverable.id
    recovery.resolved_commit_sha = commit_sha
    recovery.resolved_at = now
    recovery.updated_at = now
    if blocker.state == TeamBlockerState.OPEN:
        event = resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=accepted_by_agent_id,
            dispatch_id=dispatch_id,
            note=(
                f"Accepted reviewed dependency merge {deliverable.id} for target "
                f"task {recovery.target_task_id}."
            ),
            reference_ids=[recovery.id, task.id, deliverable.id, commit_sha],
            now=now,
        )
        if result is not None:
            result.setdefault("resolved_blocker_ids", []).append(blocker.id)
            result.setdefault("blocker_event_ids", []).append(event.id)
    target = state.tasks.get(recovery.target_task_id)
    if target is not None:
        # The reviewed recovery commit is now the target's authoritative
        # composed starting point. Preserve the original base separately on
        # the recovery record for ancestry verification and audit.
        target.git_base_commit_sha = commit_sha
        target.updated_at = now
        _mark_agent_work_due(state, agent_id=target.owner_agent_id, now=now)
    return recovery


def supersede_stale_dependency_recoveries(
    state: TeamState,
    *,
    now: datetime,
) -> list[str]:
    superseded: list[str] = []
    for recovery in state.dependency_composition_recoveries.values():
        if recovery.state in {
            DependencyCompositionRecoveryState.ACCEPTED,
            DependencyCompositionRecoveryState.CANCELLED,
            DependencyCompositionRecoveryState.SUPERSEDED,
        }:
            continue
        if recovery.preparation_generation == task_preparation_generation(
            state, recovery.target_task_id
        ):
            continue
        recovery.state = DependencyCompositionRecoveryState.SUPERSEDED
        recovery.updated_at = now
        task = state.tasks.get(recovery.recovery_task_id or "")
        if task is not None and task.state not in {
            TeamTaskState.FINISHED,
            TeamTaskState.ACCEPTED,
            TeamTaskState.CANCELLED,
        }:
            task.state = TeamTaskState.CANCELLED
            task.cancellation = TaskCancellation(
                reason_code="dependency_recovery_source_changed",
                reason=(
                    f"Dependency recovery {recovery.id} was superseded because "
                    "the target preparation generation changed."
                ),
                actor_kind="system",
                actor_id=DEPENDENCY_COMPOSITION_RECOVERY_ACTOR,
                cancelled_at=now,
            )
            task.state_entered_at = now
            task.updated_at = now
        superseded.append(recovery.id)
    return superseded


__all__ = [
    "DEPENDENCY_COMPOSITION_CONFLICT_REASON_CODE",
    "DependencyRecoveryCommitVerifier",
    "accept_dependency_composition_recovery",
    "accepted_recovery_for_target",
    "dependency_composition_source_fingerprint",
    "record_dependency_composition_conflict",
    "recovery_for_task",
    "start_dependency_composition_recovery",
    "supersede_stale_dependency_recoveries",
    "verify_dependency_recovery_submission",
]
