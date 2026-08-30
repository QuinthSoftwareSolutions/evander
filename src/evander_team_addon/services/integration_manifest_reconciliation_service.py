from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import (
    Deliverable,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    utc_now,
)
from ..repository import TeamStateConflictError, TeamStateRepository
from .commands.outbox import _team_event_outbox_records
from .domain.blockers import resolve_active_blockers_for_source, resolve_blocker
from .domain.common import _set_task_state
from .domain.git_work_contexts import finalize_active_git_work_contexts_for_task
from .domain.project_status import _recalculate_project_status
from .domain.review_handoff import queue_actionable_inbox_item


_MAX_RECONCILIATION_ATTEMPTS = 3
_RECOVERABLE_DELIVERABLE_STATES = {
    TeamRecordState.PENDING,
    TeamRecordState.IN_PROGRESS,
    TeamRecordState.BLOCKED,
}
_RECOVERABLE_TASK_STATES = {
    TeamTaskState.PENDING,
    TeamTaskState.IN_PROGRESS,
    TeamTaskState.BLOCKED,
    TeamTaskState.WAITING_FOR_INPUT,
    TeamTaskState.WAITING_FOR_REVIEW,
}


def _deliverable_has_git_provenance(deliverable: Deliverable) -> bool:
    refs = deliverable.git_refs
    return any(
        (
            refs.branch_context_id,
            refs.agent_branch,
            refs.commit_sha,
            refs.submitted_commit_sha,
            refs.reviewed_commit_sha,
            refs.accepted_pinned_ref,
        )
    )


def _missing_manifest_deliverable_ids(state: TeamState) -> list[str]:
    candidates: list[str] = []
    for deliverable in sorted(
        state.deliverables.values(),
        key=lambda item: (item.created_at, item.id),
    ):
        task = state.tasks.get(deliverable.task_id)
        if (
            deliverable.acceptance_policy_version != 2
            or deliverable.state not in _RECOVERABLE_DELIVERABLE_STATES
            or deliverable.superseded_by_deliverable_id is not None
            or deliverable.dependency_composition_manifest is not None
            or task is None
            or task.state not in _RECOVERABLE_TASK_STATES
            or not task.integration_boundary
            or deliverable.acceptance_recipient_agent_id is not None
        ):
            continue
        submitted_git_source = any(
            (source := state.deliverables.get(source_id)) is not None
            and _deliverable_has_git_provenance(source)
            for source_id in deliverable.source_deliverable_ids
        )
        accepted_dependency_git_source = any(
            candidate.task_id in task.dependency_task_ids
            and candidate.state == TeamRecordState.ACCEPTED
            and candidate.superseded_by_deliverable_id is None
            and _deliverable_has_git_provenance(candidate)
            for candidate in state.deliverables.values()
        )
        if submitted_git_source or accepted_dependency_git_source:
            candidates.append(deliverable.id)
    return candidates


def reconcile_missing_integration_manifests(
    state: TeamState,
    *,
    deliverable_ids: list[str],
    result: dict[str, Any],
    now: datetime,
) -> bool:
    changed = False
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None or deliverable.id not in _missing_manifest_deliverable_ids(state):
            continue
        task = state.tasks[deliverable.task_id]
        retired_review_ids: list[str] = []
        for review_id in deliverable.review_request_ids:
            review = state.review_requests.get(review_id)
            if (
                review is None
                or review.retired_at is not None
                or review.superseded_by_review_request_id is not None
            ):
                continue
            review.retired_at = now
            review.retired_by = "system:dependency-composition-reconciliation"
            review.retirement_reason = (
                "The retained policy-v2 integration candidate lacks its immutable "
                "dependency composition manifest and must be replaced."
            )
            retired_review_ids.append(review.id)
        handled_inbox_ids: list[str] = []
        for item in state.inbox.values():
            if item.state != TeamInboxItemState.PENDING:
                continue
            if (
                item.payload.get("deliverable_id") == deliverable.id
                or item.payload.get("review_request_id") in retired_review_ids
            ):
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = now
                handled_inbox_ids.append(item.id)

        deliverable.state = TeamRecordState.REJECTED
        resolved_blocker_ids = resolve_active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.DELIVERABLE,
            source_id=deliverable.id,
            actor_agent_id=None,
            dispatch_id=None,
            note=(
                "The malformed integration candidate was retained as rejected "
                "evidence and replacement preparation was queued."
            ),
            reference_ids=[deliverable.id, task.id],
            result=result,
            now=now,
        )
        for review_id in deliverable.review_request_ids:
            resolved_blocker_ids.extend(
                resolve_active_blockers_for_source(
                    state,
                    source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                    source_id=review_id,
                    actor_agent_id=None,
                    dispatch_id=None,
                    note=(
                        "The review was retired because its integration candidate "
                        "lacked immutable composition evidence."
                    ),
                    reference_ids=[deliverable.id, task.id],
                    result=result,
                    now=now,
                )
            )
        for blocker in sorted(state.blockers.values(), key=lambda item: item.id):
            if (
                blocker.state == TeamBlockerState.OPEN
                and blocker.task_id == task.id
                and "dependency_composition_manifest" in (blocker.reason_code or "")
            ):
                resolve_blocker(
                    state,
                    blocker=blocker,
                    actor_agent_id=None,
                    dispatch_id=None,
                    note=(
                        "Replacement preparation superseded the malformed retained "
                        "integration candidate."
                    ),
                    reference_ids=[deliverable.id, task.id],
                    now=now,
                )
                resolved_blocker_ids.append(blocker.id)
        if resolved_blocker_ids:
            result.setdefault("resolved_manifest_blocker_ids", []).extend(
                sorted(set(resolved_blocker_ids))
            )
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        task.updated_at = now
        finalized_contexts = finalize_active_git_work_contexts_for_task(
            state,
            task_id=task.id,
            now=now,
        )
        recovery_item = queue_actionable_inbox_item(
            state,
            recipient_agent_id=task.owner_agent_id,
            sender_agent_id=None,
            task_id=task.id,
            priority=TeamInboxPriority.HIGH,
            payload={
                "kind": "dependency_composition_manifest_recovery_required",
                "task_id": task.id,
                "rejected_deliverable_id": deliverable.id,
                "retired_review_request_ids": retired_review_ids,
                "preserved_git_refs": deliverable.git_refs.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                "required_action": "reprepare_and_submit_replacement",
                "summary": (
                    "A retained integration candidate lacked its immutable dependency "
                    "composition manifest. Its evidence was preserved, but the candidate "
                    "was rejected; prepare and submit a replacement."
                ),
            },
            result=result,
            now=now,
        )
        _recalculate_project_status(state, task.project_id)
        result.setdefault("reconciled_deliverable_ids", []).append(deliverable.id)
        result.setdefault("reopened_task_ids", []).append(task.id)
        result.setdefault("retired_review_request_ids", []).extend(retired_review_ids)
        result.setdefault("handled_inbox_item_ids", []).extend(handled_inbox_ids)
        result["submitted_git_work_context_count"] = (
            result.get("submitted_git_work_context_count", 0)
            + finalized_contexts["submitted"]
        )
        result["abandoned_git_work_context_count"] = (
            result.get("abandoned_git_work_context_count", 0)
            + finalized_contexts["abandoned"]
        )
        result.setdefault("recovery_inbox_item_ids", []).append(recovery_item.id)
        changed = True
    return changed


class IntegrationManifestReconciliationService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    def reconcile(self) -> dict[str, Any]:
        last_conflict: TeamStateConflictError | None = None
        for _attempt in range(_MAX_RECONCILIATION_ATTEMPTS):
            current = self._repository.read()
            deliverable_ids = _missing_manifest_deliverable_ids(current)
            if not deliverable_ids:
                return {"changed": False, "revision": current.revision, "events": []}
            result: dict[str, Any] = {"changed": False, "events": []}
            now = utc_now()

            def _mutate(state: TeamState) -> TeamState:
                result["changed"] = reconcile_missing_integration_manifests(
                    state,
                    deliverable_ids=deliverable_ids,
                    result=result,
                    now=now,
                )
                return state

            try:
                updated = self._repository.update_with_outbox(
                    _mutate,
                    outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
                    expected_revision=current.revision,
                )
            except TeamStateConflictError as exc:
                last_conflict = exc
                continue
            result["revision"] = updated.revision
            return result
        assert last_conflict is not None
        raise last_conflict


__all__ = [
    "IntegrationManifestReconciliationService",
    "reconcile_missing_integration_manifests",
]
