"""Team migrations acceptance."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime

from .models import TeamState
from .sqlite_codec import (
    _json_loads,
)
from .sqlite_schema_sql import (
    SQLITE_SCHEMA_VERSION,
)

_REJECTED_ACCEPTANCE_MIGRATION_ACTOR = f"system:sqlite-migration:v{SQLITE_SCHEMA_VERSION}"

_REJECTED_ACCEPTANCE_MIGRATION_REASON = (
    "Formal review was attached after the deliverable's authoritative direct rejection; "
    f"the invalid review path was retired during the v{SQLITE_SCHEMA_VERSION} "
    "state migration."
)


def _acceptance_decision_is_rejected(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("decision") == "rejected"


def _normalize_rejected_acceptance_override_rows(
    connection: sqlite3.Connection,
) -> list[str]:
    """Make legacy rejection overrides parseable before strict model validation."""

    current_review_deliverable_ids = {
        str(row["deliverable_id"])
        for row in connection.execute(
            """
            SELECT DISTINCT deliverable_id
            FROM team_review_requests
            WHERE deliverable_id IS NOT NULL
              AND retired_at IS NULL
              AND superseded_by_review_request_id IS NULL
            """
        )
    }
    affected_ids: list[str] = []
    for row in connection.execute(
        """
        SELECT id, state, acceptance_decision_json
        FROM team_deliverables
        WHERE acceptance_decision_json IS NOT NULL
        ORDER BY id
        """
    ):
        decision = _json_loads(row["acceptance_decision_json"])
        if not _acceptance_decision_is_rejected(decision):
            continue
        deliverable_id = str(row["id"])
        if row["state"] == "rejected" and deliverable_id not in current_review_deliverable_ids:
            continue
        connection.execute(
            "UPDATE team_deliverables SET state = 'rejected' WHERE id = ?",
            (deliverable_id,),
        )
        affected_ids.append(deliverable_id)
    return affected_ids


def _reconcile_rejected_acceptance_overrides(
    state: TeamState,
    *,
    deliverable_ids: Sequence[str],
    now: datetime,
) -> None:
    """Retire invalid review paths and restore actionable direct-rejection work."""

    from .models import (
        AgentInboxItem,
        TeamBlockerSourceKind,
        TeamInboxItemState,
        TeamInboxItemType,
        TeamInboxPriority,
        TeamRecordState,
        TeamTaskState,
    )
    from .services.domain.blockers import resolve_active_blockers_for_source
    from .services.domain.common import _set_task_state
    from .services.domain.project_status import _recalculate_project_status
    from .services.domain.task_assessment import assess_task
    from .services.domain.work_due import _mark_agent_work_due

    selected_ids = {
        deliverable_id for deliverable_id in deliverable_ids if deliverable_id in state.deliverables
    }
    if not selected_ids:
        return

    affected_task_ids: set[str] = set()
    affected_project_ids: set[str] = set()
    invalid_review_ids: set[str] = set()
    for deliverable_id in sorted(selected_ids):
        deliverable = state.deliverables[deliverable_id]
        deliverable.state = TeamRecordState.REJECTED
        task = state.tasks.get(deliverable.task_id)
        if task is not None:
            affected_project_ids.add(task.project_id)
        for review in state.review_requests.values():
            if review.deliverable_id != deliverable_id:
                continue
            invalid_review_ids.add(review.id)
            if review.retired_at is None:
                review.retired_at = now
                review.retired_by = _REJECTED_ACCEPTANCE_MIGRATION_ACTOR
                review.retirement_reason = _REJECTED_ACCEPTANCE_MIGRATION_REASON
            resolve_active_blockers_for_source(
                state,
                source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                source_id=review.id,
                actor_agent_id=None,
                dispatch_id=None,
                note=_REJECTED_ACCEPTANCE_MIGRATION_REASON,
                reference_ids=[deliverable_id],
                now=now,
            )
        for finding in deliverable.carried_findings:
            if finding.source_review_request_id in invalid_review_ids and finding.state == "open":
                finding.state = "resolved"
                finding.resolution = _REJECTED_ACCEPTANCE_MIGRATION_REASON
                finding.resolved_at = now
                finding.resolved_by_review_request_id = None
        if (
            task is not None
            and deliverable.superseded_by_deliverable_id is None
            and task.state not in {TeamTaskState.CANCELLED, TeamTaskState.REJECTED}
        ):
            affected_task_ids.add(task.id)

    _close_invalid_rejection_override_inbox_items(
        state,
        deliverable_ids=selected_ids,
        review_request_ids=invalid_review_ids,
        now=now,
    )

    for task_id in sorted(affected_task_ids):
        task = state.tasks[task_id]
        task.completion = None
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        task.updated_at = now
        assessment = assess_task(state, task.id)
        if assessment.active_blocker_ids:
            _set_task_state(task, TeamTaskState.BLOCKED, now=now)
        elif assessment.active_acceptance_ids or assessment.active_review_request_ids:
            _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)

    for deliverable_id in sorted(selected_ids):
        deliverable = state.deliverables[deliverable_id]
        task = state.tasks.get(deliverable.task_id)
        decision = deliverable.acceptance_decision
        if (
            task is None
            or task.id not in affected_task_ids
            or decision is None
            or deliverable.superseded_by_deliverable_id is not None
        ):
            continue
        existing_rework = next(
            (
                item
                for item in state.inbox.values()
                if item.state == TeamInboxItemState.PENDING
                and item.recipient_agent_id == task.owner_agent_id
                and item.payload.get("kind") == "superior_acceptance_rejected"
                and item.payload.get("deliverable_id") == deliverable.id
            ),
            None,
        )
        if existing_rework is None:
            token = hashlib.sha256(deliverable.id.encode("utf-8")).hexdigest()[:20]
            inbox_id = f"inbox-rejected-acceptance-migration-{token}"
            suffix = 2
            while inbox_id in state.inbox:
                inbox_id = f"inbox-rejected-acceptance-migration-{token}-{suffix}"
                suffix += 1
            state.inbox[inbox_id] = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=task.owner_agent_id,
                sender_agent_id=decision.decided_by_agent_id,
                task_id=task.id,
                item_type=TeamInboxItemType.STATUS_REQUEST,
                response_requested=True,
                priority=TeamInboxPriority.NORMAL,
                payload={
                    "kind": "superior_acceptance_rejected",
                    "deliverable_id": deliverable.id,
                    "supersedes_deliverable_id": deliverable.id,
                    "task_id": task.id,
                    "summary": "Superior rejected the deliverable; revise and resubmit.",
                    "reason": decision.reason,
                    "artifact_refs": list(deliverable.artifact_refs),
                    "migration": (f"rejected_acceptance_override_v{SQLITE_SCHEMA_VERSION}"),
                },
                created_at=now,
                updated_at=now,
            )
        _mark_agent_work_due(state, agent_id=task.owner_agent_id, now=now)

    terminal_ancestor_ids = _terminal_ancestor_ids_for_reopened_tasks(
        state,
        task_ids=affected_task_ids,
    )
    _close_stale_task_resolution_inbox_items(
        state,
        reopened_task_ids=affected_task_ids,
        terminal_ancestor_ids=terminal_ancestor_ids,
        deliverable_ids=selected_ids,
        now=now,
    )
    if terminal_ancestor_ids:
        for project_id in sorted(affected_project_ids):
            project_task_ids = sorted(
                task_id
                for task_id in terminal_ancestor_ids
                if state.tasks[task_id].project_id == project_id
            )
            if project_task_ids:
                _open_rejected_acceptance_migration_blocker(
                    state,
                    project_id=project_id,
                    source_id=(
                        "rejected-acceptance-terminal-ancestors:" + ",".join(project_task_ids)
                    ),
                    summary="Reconcile terminal ancestor tasks after a restored rejection",
                    details=(
                        "A rejected child deliverable had previously been accepted through an "
                        "invalid formal-review path. Preserve the terminal ancestor history and "
                        "explicitly reopen or replace it after reviewing downstream effects."
                    ),
                    reference_ids=sorted(selected_ids | set(project_task_ids)),
                    now=now,
                )

    affected_release_ids = _release_ids_depending_on_deliverables(
        state,
        deliverable_ids=selected_ids,
    )
    for release_id in affected_release_ids:
        release = state.release_packages[release_id]
        if release.state == TeamRecordState.REJECTED:
            continue
        _open_rejected_acceptance_migration_blocker(
            state,
            project_id=release.project_id,
            source_id=f"rejected-acceptance-release:{release.id}",
            summary=f"Reconcile release {release.id} after a restored rejection",
            details=(
                "This release includes deliverable lineage whose authoritative direct "
                "rejection was historically overridden by formal review. The release and "
                "promotion records were preserved; verify their external effects and create "
                "a successor from accepted replacement lineage before publication."
            ),
            reference_ids=sorted(
                set(selected_ids) | {release.id} | set(release.git_promotion_record_ids)
            ),
            now=now,
        )
        affected_project_ids.add(release.project_id)

    for project_id in sorted(affected_project_ids):
        _recalculate_project_status(state, project_id)


def _close_invalid_rejection_override_inbox_items(
    state: TeamState,
    *,
    deliverable_ids: set[str],
    review_request_ids: set[str],
    now: datetime,
) -> None:
    from .models import TeamInboxItemState

    invalid_review_kinds = {
        "review_request",
        "review_retry_request",
        "review_rejected",
        "review_rework_restored",
    }
    for item in state.inbox.values():
        if item.state != TeamInboxItemState.PENDING:
            continue
        payload = item.payload
        linked_review_ids = {
            str(value) for value in payload.get("review_request_ids") or [] if value is not None
        }
        if payload.get("review_request_id") is not None:
            linked_review_ids.add(str(payload["review_request_id"]))
        close = (
            payload.get("kind") in invalid_review_kinds
            and bool(linked_review_ids.intersection(review_request_ids))
        ) or (
            payload.get("kind") == "superior_acceptance_requested"
            and payload.get("deliverable_id") in deliverable_ids
        )
        if close:
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = now


def _terminal_ancestor_ids_for_reopened_tasks(
    state: TeamState,
    *,
    task_ids: set[str],
) -> set[str]:
    from .models import TeamTaskState

    terminal_ids: set[str] = set()
    for task_id in task_ids:
        current = state.tasks.get(task_id)
        seen: set[str] = set()
        while current is not None and current.parent_task_id is not None:
            parent_id = current.parent_task_id
            if parent_id in seen:
                break
            seen.add(parent_id)
            parent = state.tasks.get(parent_id)
            if parent is None:
                break
            if parent.state in {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}:
                terminal_ids.add(parent.id)
            current = parent
    return terminal_ids


def _close_stale_task_resolution_inbox_items(
    state: TeamState,
    *,
    reopened_task_ids: set[str],
    terminal_ancestor_ids: set[str],
    deliverable_ids: set[str],
    now: datetime,
) -> None:
    from .models import TeamInboxItemState

    invalid_resolution_task_ids = reopened_task_ids | terminal_ancestor_ids
    for item in state.inbox.values():
        if item.state != TeamInboxItemState.PENDING:
            continue
        payload = item.payload
        kind = payload.get("kind")
        if kind in {"root_task_resolution", "candidate_correction_resolution"} and (
            payload.get("task_id") in invalid_resolution_task_ids
            or payload.get("deliverable_id") in deliverable_ids
        ):
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = now
            continue
        if kind != "parent_task_transition":
            continue
        child_ids = {
            str(value) for value in payload.get("child_task_ids") or [] if value is not None
        }
        invalid_child_ids = child_ids.intersection(invalid_resolution_task_ids)
        if not invalid_child_ids:
            continue
        if payload.get("transition") == "integration_ready":
            item.state = TeamInboxItemState.HANDLED
        else:
            remaining_child_ids = sorted(child_ids - invalid_child_ids)
            payload["child_task_ids"] = remaining_child_ids
            if not remaining_child_ids:
                item.state = TeamInboxItemState.HANDLED
        item.updated_at = now


def _release_ids_depending_on_deliverables(
    state: TeamState,
    *,
    deliverable_ids: set[str],
) -> list[str]:
    memo: dict[str, bool] = {}

    def depends_on_rejected_lineage(deliverable_id: str, visiting: set[str]) -> bool:
        if deliverable_id in memo:
            return memo[deliverable_id]
        if deliverable_id in deliverable_ids:
            memo[deliverable_id] = True
            return True
        if deliverable_id in visiting:
            return False
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            memo[deliverable_id] = False
            return False
        next_visiting = set(visiting)
        next_visiting.add(deliverable_id)
        result = any(
            depends_on_rejected_lineage(source_id, next_visiting)
            for source_id in deliverable.source_deliverable_ids
        )
        memo[deliverable_id] = result
        return result

    return sorted(
        release.id
        for release in state.release_packages.values()
        if any(
            depends_on_rejected_lineage(deliverable_id, set())
            for deliverable_id in release.deliverable_ids
        )
    )


def _open_rejected_acceptance_migration_blocker(
    state: TeamState,
    *,
    project_id: str,
    source_id: str,
    summary: str,
    details: str,
    reference_ids: Sequence[str],
    now: datetime,
) -> None:
    from .models import (
        TeamBlocker,
        TeamBlockerEvent,
        TeamBlockerEventType,
        TeamBlockerRoute,
        TeamBlockerSourceKind,
        TeamBlockerState,
        TeamInboxPriority,
    )

    project = state.projects.get(project_id)
    if project is None:
        return
    existing = next(
        (
            blocker
            for blocker in state.blockers.values()
            if blocker.team_id == project.team_id
            and blocker.source_kind == TeamBlockerSourceKind.EXPLICIT
            and blocker.source_id == source_id
        ),
        None,
    )
    if existing is not None:
        return
    token = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:20]
    blocker_id = f"blocker-rejected-acceptance-migration-{token}"
    blocker = TeamBlocker(
        id=blocker_id,
        team_id=project.team_id,
        project_id=project.id,
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id=source_id,
        reason_code="rejected_acceptance_override_reconciliation",
        reporter_actor=_REJECTED_ACCEPTANCE_MIGRATION_ACTOR,
        state=TeamBlockerState.OPEN,
        route=TeamBlockerRoute.OPERATOR,
        summary=summary,
        details=details,
        priority=TeamInboxPriority.HIGH,
        terminal_reference_ids=sorted(set(reference_ids)),
        created_at=now,
        updated_at=now,
    )
    state.blockers[blocker.id] = blocker
    event_id = f"blockerevent-rejected-acceptance-migration-{token}"
    state.blocker_events[event_id] = TeamBlockerEvent(
        id=event_id,
        blocker_id=blocker.id,
        event_type=TeamBlockerEventType.REPORTED,
        note=details,
        reference_ids=list(blocker.terminal_reference_ids),
        metadata={"sqlite_migration": SQLITE_SCHEMA_VERSION},
        created_at=now,
    )
