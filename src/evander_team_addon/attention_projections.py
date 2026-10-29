"""Runtime attention and session health projections."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from evander.models import utc_now

from .model_common import (
    AgentStatus,
    EphemeralAssignmentState,
    ProjectStatus,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamTaskState,
    _ensure_utc,
    _json_datetime,
    _optional_text,
    _text_list,
)
from .model_dispatch import (
    EphemeralAssignment,
    GitPromotionRecord,
    TeamDispatchStatus,
)
from .model_inbox_memory import (
    AgentActionRecord,
    AgentInboxItem,
)
from .model_queries import (
    agent_lead_project_ids,
    agent_project_ids,
    current_failed_action_records,
)
from .model_releases import (
    ReleasePackage,
)
from .model_state import (
    TeamState,
)
from .model_tasks import (
    AgentTask,
)
from .release_projections import (
    _release_next_action_hint,
)


def team_session_health_payload(
    state: TeamState,
    *,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    agent_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if session_records is None:
        return []
    effective_now = _ensure_utc(now or utc_now())
    rows: list[dict[str, Any]] = []
    for agent in sorted(state.agents.values(), key=lambda item: item.id):
        if agent_ids is not None and agent.id not in agent_ids:
            continue
        session_id = agent.session_id
        session = session_records.get(session_id) if session_id else None
        execution_record = (
            agent_execution_records.get(session_id)
            if agent_execution_records is not None and session_id
            else None
        )
        execution_host = (
            getattr(execution_record, "host", {}) if execution_record is not None else {}
        )
        if not isinstance(execution_host, Mapping):
            execution_host = {}
        active_execution_count = execution_host.get("active_execution_count")
        if not isinstance(active_execution_count, int):
            active_execution_count = None
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        vm_provider = _optional_text(getattr(session, "vm_provider", None))
        execution_backend = _optional_text(metadata.get("execution_backend")) or vm_provider
        container_lifecycle = metadata.get("container_lifecycle")
        if not isinstance(container_lifecycle, Mapping):
            container_lifecycle = {}
        container_lifecycle_state = _optional_text(container_lifecycle.get("state"))
        expires_at_value = getattr(session, "expires_at", None) if session is not None else None
        expires_at = (
            _ensure_utc(expires_at_value) if isinstance(expires_at_value, datetime) else None
        )
        is_expired = expires_at is not None and expires_at <= effective_now
        status = (
            "expired"
            if is_expired
            else "active"
            if expires_at is not None
            else "missing"
            if session is None
            else "unknown"
        )
        row = {
            "agent_id": agent.id,
            "team_id": agent.team_id,
            "project_ids": agent_project_ids(state, agent.id),
            "lead_project_ids": agent_lead_project_ids(state, agent.id),
            "role_id": agent.role_id,
            "session_id": session_id,
            "session_present": session is not None,
            "expires_at": _json_datetime(expires_at),
            "is_expired": is_expired,
            "status": status,
        }
        has_container_metadata = bool(
            session is not None
            and (
                vm_provider == "container_worker_vm"
                or execution_backend == "container_worker_vm"
                or container_lifecycle_state is not None
            )
        )
        if has_container_metadata or execution_record is not None:
            if vm_provider is not None:
                row["vm_provider"] = vm_provider
            if execution_backend is not None:
                row["execution_backend"] = execution_backend
            if container_lifecycle_state is not None:
                row["container_lifecycle_state"] = container_lifecycle_state
        if execution_record is not None:
            agent_execution_backend = _optional_text(getattr(execution_record, "backend", None))
            if agent_execution_backend is not None:
                row["agent_execution_backend"] = agent_execution_backend
            agent_execution_status = _optional_text(getattr(execution_record, "status", None))
            if agent_execution_status is not None:
                row["agent_execution_status"] = agent_execution_status
            if active_execution_count is not None:
                row["agent_active_execution_count"] = active_execution_count
            agent_latest_execution_id = _optional_text(execution_host.get("latest_execution_id"))
            if agent_latest_execution_id is not None:
                row["agent_latest_execution_id"] = agent_latest_execution_id
        rows.append(row)
    return rows


def _runtime_attention_payload(
    state: TeamState,
    *,
    session_health: list[dict[str, Any]] | None = None,
    agent_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    effective_now = _ensure_utc(now or utc_now())
    rows: list[dict[str, Any]] = []
    deliverable_id_for_task = _first_deliverable_lookup(state)
    rows.extend(_expired_session_rows(state, session_health or []))
    rows.extend(_blocker_rows(state))
    rows.extend(_blocked_task_rows(state, deliverable_id_for_task=deliverable_id_for_task))
    rows.extend(_waiting_dependency_rows(state, deliverable_id_for_task=deliverable_id_for_task))
    rows.extend(_pending_inbox_rows(state))
    rows.extend(_failed_action_rows(state))
    rows.extend(_failed_promotion_rows(state))
    rows.extend(_blocked_release_rows(state))
    rows.extend(_ephemeral_assignment_rows(state))
    rows = [
        _runtime_attention_metadata(
            state,
            row,
            now=effective_now,
            session_health=session_health or [],
        )
        for row in rows
    ]
    if agent_ids is not None:
        rows = [row for row in rows if _runtime_attention_row_in_agent_scope(row, agent_ids)]
    rows.sort(key=_runtime_attention_sort_key)
    return rows


def _runtime_attention_row_in_agent_scope(row: Mapping[str, Any], agent_ids: set[str]) -> bool:
    agent_id = _optional_text(row.get("agent_id"))
    return agent_id is not None and agent_id in agent_ids


def _expired_session_rows(
    state: TeamState,
    session_health: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for health in session_health:
        if not bool(health.get("is_expired")):
            continue
        agent_id = _optional_text(health.get("agent_id"))
        if agent_id is None:
            continue
        agent = state.agents.get(agent_id)
        project_ids = _text_list(health.get("project_ids") or [], "project_ids")
        session_id = _optional_text(health.get("session_id")) or (
            agent.session_id if agent is not None else None
        )
        expires_at = _optional_text(health.get("expires_at")) or "unknown time"
        rows.append(
            _runtime_row(
                id=f"{agent_id}:expired-session",
                kind="expired_agent_session",
                severity="critical",
                title=f"Expired session for {agent_id}",
                detail=f"agent {agent_id}; session {session_id or 'unknown'} expired at {expires_at}",
                state="expired",
                team_id=health.get("team_id") or (agent.team_id if agent is not None else None),
                agent_id=agent_id,
                project_id=project_ids[0] if len(project_ids) == 1 else None,
                project_ids=project_ids,
                session_id=session_id,
                session_expires_at=health.get("expires_at"),
                occurred_at=health.get("expires_at"),
            )
        )
    return rows


def _blocker_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for blocker in sorted(state.blockers.values(), key=lambda item: (item.created_at, item.id)):
        if blocker.state != TeamBlockerState.OPEN:
            continue
        deliverable_id: str | None = None
        review_request_id: str | None = None
        release_package_id: str | None = None
        if blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
            deliverable_id = blocker.source_id
        elif blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
            review_request_id = blocker.source_id
            review = state.review_requests.get(blocker.source_id)
            if review is not None:
                deliverable_id = review.deliverable_id
                release_package_id = review.release_package_id
        elif blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE:
            release_package_id = blocker.source_id
        elif (
            blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
            and blocker.source_id.startswith("candidate-correction-successor:")
        ):
            release_package_id = blocker.source_id.removeprefix("candidate-correction-successor:")
        detail_parts = [
            f"source {blocker.source_kind.value} {blocker.source_id}",
            f"route {blocker.route.value}",
        ]
        if blocker.owner_agent_id is not None:
            detail_parts.append(f"owner {blocker.owner_agent_id}")
        if blocker.delegate_agent_id is not None:
            detail_parts.append(f"delegate {blocker.delegate_agent_id}")
        detail_parts.append(f"level {blocker.escalation_depth}")
        if blocker.details:
            detail_parts.append(blocker.details)
        rows.append(
            _runtime_row(
                id=blocker.id,
                kind="team_blocker",
                severity=(
                    "critical"
                    if blocker.route
                    in {
                        TeamBlockerRoute.GOVERNANCE,
                        TeamBlockerRoute.OPERATOR,
                        TeamBlockerRoute.ROUTING_FAILED,
                    }
                    or blocker.priority == TeamInboxPriority.HIGH
                    else "warning"
                ),
                title=blocker.summary,
                detail="; ".join(detail_parts),
                state=blocker.state.value,
                team_id=blocker.team_id,
                agent_id=blocker.owner_agent_id,
                project_id=blocker.project_id,
                task_id=blocker.task_id,
                deliverable_id=deliverable_id,
                review_request_id=review_request_id,
                release_package_id=release_package_id,
                blocker_id=blocker.id,
                source_kind=blocker.source_kind.value,
                source_id=blocker.source_id,
                owner_agent_id=blocker.owner_agent_id,
                delegate_agent_id=blocker.delegate_agent_id,
                route=blocker.route.value,
                escalation_depth=blocker.escalation_depth,
                active_dispatch_id=blocker.active_dispatch_id,
                follow_up_at=_json_datetime(blocker.follow_up_at),
                occurred_at=_json_datetime(blocker.created_at),
            )
        )
    return rows


def _blocked_task_rows(
    state: TeamState,
    *,
    deliverable_id_for_task: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    deliverable_id_for_task = deliverable_id_for_task or _first_deliverable_lookup(state)
    rows: list[dict[str, Any]] = []
    for task in sorted(state.tasks.values(), key=lambda item: item.id):
        dependency_status = state.dependency_status(task.id)
        if task.state != TeamTaskState.BLOCKED and dependency_status != "blocked":
            continue
        rows.append(
            _runtime_row(
                id=task.id,
                kind="blocked_task",
                severity="warning",
                title=task.title,
                detail=_task_attention_detail(task, dependency_status=dependency_status),
                state=task.state.value,
                agent_id=task.owner_agent_id,
                project_id=task.project_id,
                task_id=task.id,
                deliverable_id=deliverable_id_for_task(task.id),
                dependency_status=dependency_status,
                blocked_by_task_ids=state.blocked_by_task_ids(task.id),
                occurred_at=_json_datetime(task.state_entered_at),
            )
        )
    return rows


def _waiting_dependency_rows(
    state: TeamState,
    *,
    deliverable_id_for_task: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    deliverable_id_for_task = deliverable_id_for_task or _first_deliverable_lookup(state)
    rows: list[dict[str, Any]] = []
    for task in sorted(state.tasks.values(), key=lambda item: item.id):
        dependency_status = state.dependency_status(task.id)
        if dependency_status != "waiting" or task.state == TeamTaskState.BLOCKED:
            continue
        rows.append(
            _runtime_row(
                id=task.id,
                kind="waiting_dependency_task",
                severity="warning",
                title=task.title,
                detail=_task_attention_detail(task, dependency_status=dependency_status),
                state=task.state.value,
                agent_id=task.owner_agent_id,
                project_id=task.project_id,
                task_id=task.id,
                deliverable_id=deliverable_id_for_task(task.id),
                dependency_status=dependency_status,
                blocked_by_task_ids=state.blocked_by_task_ids(task.id),
                occurred_at=_json_datetime(task.state_entered_at),
            )
        )
    return rows


def _pending_inbox_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(state.inbox.values(), key=lambda entry: (entry.created_at, entry.id)):
        if item.state != TeamInboxItemState.PENDING:
            continue
        title = _inbox_title(item)
        detail = _inbox_detail(state, item)
        kind = (
            "pending_escalation"
            if item.item_type == TeamInboxItemType.ESCALATION
            else "pending_inbox_item"
        )
        rows.append(
            _runtime_row(
                id=item.id,
                kind=kind,
                severity="warning",
                title=title,
                detail=detail,
                state=item.state.value,
                agent_id=item.recipient_agent_id,
                project_id=_project_id_for_task(state, item.task_id),
                task_id=item.task_id,
                inbox_item_id=item.id,
                sender_agent_id=item.sender_agent_id,
                item_type=item.item_type.value,
                response_requested=item.response_requested,
                priority=item.priority.value,
                occurred_at=_json_datetime(item.created_at),
                links=_runtime_attention_payload_links(item.payload),
            )
        )
    return rows


def _failed_action_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in current_failed_action_records(state):
        rows.append(
            _runtime_row(
                id=record.id,
                kind="failed_action_record",
                severity="critical",
                title=f"Failed action {record.id}",
                detail=_action_record_detail(record),
                state="failed",
                agent_id=record.agent_id,
                dispatch_id=record.dispatch_id,
                action_record_id=record.id,
                error=record.error,
                occurred_at=_json_datetime(record.created_at),
                links=_runtime_attention_payload_links(record.action),
            )
        )
    return rows


def _failed_promotion_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for promotion in sorted(
        state.git_promotions.values(),
        key=lambda entry: (entry.created_at, entry.id),
    ):
        if promotion.state != "failed":
            continue
        if promotion.release_package_id is not None:
            release = state.release_packages.get(promotion.release_package_id)
            if release is not None and release.state in {
                TeamRecordState.ACCEPTED,
                TeamRecordState.REJECTED,
            }:
                continue
        if promotion.deliverable_id is not None:
            deliverable = state.deliverables.get(promotion.deliverable_id)
            if deliverable is not None and deliverable.state in {
                TeamRecordState.ACCEPTED,
                TeamRecordState.REJECTED,
            }:
                continue
        if promotion.task_id is not None:
            task = state.tasks.get(promotion.task_id)
            if task is not None and task.state in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.REJECTED,
                TeamTaskState.CANCELLED,
            }:
                continue
        rows.append(
            _runtime_row(
                id=promotion.id,
                kind="failed_git_promotion",
                severity="critical",
                title=f"Failed {promotion.promotion_kind} promotion {promotion.id}",
                detail=_promotion_detail(promotion),
                state=promotion.state,
                agent_id=promotion.actor_agent_id,
                project_id=promotion.project_id,
                task_id=promotion.task_id,
                deliverable_id=promotion.deliverable_id,
                promotion_id=promotion.id,
                release_package_id=promotion.release_package_id,
                promotion_kind=promotion.promotion_kind,
                source_branch=promotion.source_branch,
                target_branch=promotion.target_branch,
                commit_sha=promotion.commit_sha,
                error=promotion.error,
                occurred_at=_json_datetime(promotion.updated_at),
            )
        )
    return rows


def _blocked_release_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for release in sorted(
        state.release_packages.values(), key=lambda entry: (entry.created_at, entry.id)
    ):
        if release.state in {
            TeamRecordState.ACCEPTED,
            TeamRecordState.REJECTED,
        }:
            continue
        if release.state != TeamRecordState.BLOCKED and not release.error:
            continue
        next_action_hint = _release_next_action_hint(state, release)
        rows.append(
            _runtime_row(
                id=release.id,
                kind="blocked_release_package",
                severity="critical" if release.error else "warning",
                title=f"Release {release.id} blocked",
                detail=_release_detail(release),
                state=release.state.value,
                project_id=release.project_id,
                release_package_id=release.id,
                deliverable_ids=list(release.deliverable_ids),
                artifact_refs=list(release.artifact_refs),
                error=release.error,
                next_action="resolve_blocked_release" if next_action_hint else None,
                next_action_hint=next_action_hint,
                occurred_at=_json_datetime(release.updated_at or release.created_at),
            )
        )
    return rows


def _ephemeral_assignment_rows(state: TeamState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assignment in sorted(
        state.ephemeral_assignments.values(),
        key=lambda entry: (entry.created_at, entry.id),
    ):
        if assignment.state not in {
            EphemeralAssignmentState.FAILED,
            EphemeralAssignmentState.CLEANUP_PENDING,
        }:
            continue
        kind = (
            "failed_ephemeral_assignment"
            if assignment.state == EphemeralAssignmentState.FAILED
            else "cleanup_pending_ephemeral_assignment"
        )
        rows.append(
            _runtime_row(
                id=assignment.id,
                kind=kind,
                severity="critical"
                if assignment.state == EphemeralAssignmentState.FAILED
                else "warning",
                title=f"Ephemeral assignment {assignment.id} {assignment.state.value.replace('_', ' ')}",
                detail=_assignment_detail(assignment),
                state=assignment.state.value,
                agent_id=assignment.parent_agent_id,
                project_id=assignment.project_id,
                task_id=assignment.task_id,
                assignment_id=assignment.id,
                session_id=assignment.session_id,
                error=assignment.error,
                occurred_at=_json_datetime(assignment.updated_at),
            )
        )
    return rows


def _runtime_row(**row: Any) -> dict[str, Any]:
    base = {
        "id": None,
        "kind": None,
        "severity": None,
        "title": None,
        "detail": None,
        "state": None,
        "error": None,
        "team_id": None,
        "agent_id": None,
        "project_id": None,
        "task_id": None,
        "deliverable_id": None,
        "inbox_item_id": None,
        "assignment_id": None,
        "promotion_id": None,
        "release_package_id": None,
        "review_request_id": None,
        "blocker_id": None,
        "source_kind": None,
        "source_id": None,
        "owner_agent_id": None,
        "delegate_agent_id": None,
        "route": None,
        "escalation_depth": None,
        "active_dispatch_id": None,
        "follow_up_at": None,
        "dispatch_id": None,
        "session_id": None,
        "project_ids": [],
        "session_expires_at": None,
        "occurred_at": None,
        "age_seconds": 0,
        "age_bucket": "lt_1h",
        "attention_group": "active_obligation",
        "attention_reason": None,
        "terminal_context": [],
        "reconciliation_required": False,
        "links": {},
    }
    base.update(row)
    return base


_RUNTIME_ATTENTION_LINK_KEYS = {
    "team_id": "team_ids",
    "team_ids": "team_ids",
    "project_id": "project_ids",
    "project_ids": "project_ids",
    "agent_id": "agent_ids",
    "agent_ids": "agent_ids",
    "recipient_agent_id": "agent_ids",
    "sender_agent_id": "agent_ids",
    "owner_agent_id": "agent_ids",
    "delegate_agent_id": "agent_ids",
    "actor_agent_id": "agent_ids",
    "producer_agent_id": "agent_ids",
    "reviewer_agent_id": "agent_ids",
    "parent_agent_id": "agent_ids",
    "task_id": "task_ids",
    "task_ids": "task_ids",
    "parent_task_id": "task_ids",
    "child_task_id": "task_ids",
    "child_task_ids": "task_ids",
    "affected_task_ids": "task_ids",
    "deliverable_id": "deliverable_ids",
    "deliverable_ids": "deliverable_ids",
    "release_package_id": "release_package_ids",
    "release_package_ids": "release_package_ids",
    "review_request_id": "review_request_ids",
    "review_request_ids": "review_request_ids",
    "blocker_id": "blocker_ids",
    "blocker_ids": "blocker_ids",
    "dispatch_id": "dispatch_ids",
    "dispatch_ids": "dispatch_ids",
    "active_dispatch_id": "dispatch_ids",
    "session_id": "session_ids",
    "session_ids": "session_ids",
    "requesting_session_id": "session_ids",
    "assignment_id": "assignment_ids",
    "assignment_ids": "assignment_ids",
    "promotion_id": "promotion_ids",
    "promotion_ids": "promotion_ids",
    "inbox_item_id": "inbox_item_ids",
    "inbox_item_ids": "inbox_item_ids",
}


_RUNTIME_ATTENTION_FAILED_KINDS = {
    "failed_action_record",
    "failed_git_promotion",
    "failed_ephemeral_assignment",
}


_RUNTIME_ATTENTION_GROUP_RANK = {
    "active_obligation": 0,
    "failed_attempt": 1,
    "retained_history": 2,
}


def _runtime_attention_payload_links(payload: object) -> dict[str, list[str]]:
    """Extract only well-known durable IDs from nested action/inbox payloads."""

    links: dict[str, set[str]] = {}
    pending: list[tuple[object, int]] = [(payload, 0)]
    visited = 0
    while pending and visited < 1000:
        value, depth = pending.pop()
        visited += 1
        if depth > 8:
            continue
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key)
                target = _RUNTIME_ATTENTION_LINK_KEYS.get(key)
                if target is not None:
                    values = (
                        nested
                        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes))
                        else [nested]
                    )
                    for candidate in values:
                        if isinstance(candidate, Mapping) or (
                            isinstance(candidate, Sequence)
                            and not isinstance(candidate, (str, bytes))
                        ):
                            continue
                        text = _optional_text(candidate)
                        if text is not None:
                            links.setdefault(target, set()).add(text)
                if isinstance(nested, Mapping) or (
                    isinstance(nested, Sequence) and not isinstance(nested, (str, bytes))
                ):
                    pending.append((nested, depth + 1))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            pending.extend((nested, depth + 1) for nested in value)
    return {key: sorted(values) for key, values in sorted(links.items()) if values}


def _runtime_attention_metadata(
    state: TeamState,
    row: Mapping[str, Any],
    *,
    now: datetime,
    session_health: list[dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(row)
    occurred_at = _runtime_attention_datetime(enriched.get("occurred_at")) or now
    age_seconds = max(0, int((now - occurred_at).total_seconds()))
    links = _runtime_attention_links(state, enriched)
    terminal_context = _runtime_attention_terminal_context(
        state,
        links,
        session_health=session_health,
    )
    kind = _optional_text(enriched.get("kind")) or "attention"
    response_requested = bool(enriched.get("response_requested"))
    primary_target = _runtime_attention_primary_target(state, enriched)
    if primary_target is None:
        reconciliation_context = [
            context
            for context in terminal_context
            if context["kind"] in {"task", "deliverable", "release_package", "dispatch", "session"}
        ]
    else:
        reconciliation_context = [
            context
            for context in terminal_context
            if (context["kind"], context["id"]) == primary_target
        ]
    reconciliation_required = bool(
        kind in {"pending_inbox_item", "pending_escalation"} and reconciliation_context
    )
    if kind in _RUNTIME_ATTENTION_FAILED_KINDS:
        attention_group = "failed_attempt"
    elif kind in {"pending_inbox_item", "pending_escalation"} and (
        reconciliation_required or not response_requested
    ):
        attention_group = "retained_history"
    else:
        attention_group = "active_obligation"

    enriched.update(
        {
            "occurred_at": occurred_at.isoformat(),
            "age_seconds": age_seconds,
            "age_bucket": _runtime_attention_age_bucket(age_seconds),
            "attention_group": attention_group,
            "attention_reason": _runtime_attention_reason(
                kind,
                response_requested=response_requested,
                reconciliation_required=reconciliation_required,
            ),
            "terminal_context": terminal_context,
            "reconciliation_required": reconciliation_required,
            "links": links,
        }
    )

    project_ids = links.get("project_ids", [])
    team_ids = links.get("team_ids", [])
    enriched["project_ids"] = project_ids
    if _optional_text(enriched.get("project_id")) is None and len(project_ids) == 1:
        enriched["project_id"] = project_ids[0]
    if _optional_text(enriched.get("team_id")) is None and len(team_ids) == 1:
        enriched["team_id"] = team_ids[0]
    return enriched


def _runtime_attention_primary_target(
    state: TeamState,
    row: Mapping[str, Any],
) -> tuple[str, str] | None:
    """Return the response target rather than a transitively related record."""

    inbox_id = _optional_text(row.get("inbox_item_id"))
    item = state.inbox.get(inbox_id or "")
    if item is None:
        return None
    payload = item.payload if isinstance(item.payload, Mapping) else {}
    for field, kind in (
        ("blocker_id", "blocker"),
        ("review_request_id", "review_request"),
        ("release_package_id", "release_package"),
        ("deliverable_id", "deliverable"),
        ("task_id", "task"),
        ("dispatch_id", "dispatch"),
        ("session_id", "session"),
    ):
        target_id = _optional_text(payload.get(field))
        if target_id is not None:
            return kind, target_id
    if item.task_id is not None:
        return "task", item.task_id
    return None


def _runtime_attention_links(
    state: TeamState,
    row: Mapping[str, Any],
) -> dict[str, list[str]]:
    scope_team_ids = _runtime_attention_source_team_ids(state, row)
    extracted = _runtime_attention_payload_links(row)
    links: dict[str, set[str]] = {key: set(values) for key, values in extracted.items()}

    def add(key: str, *values: object) -> None:
        for value in values:
            text = _optional_text(value)
            if text is not None:
                links.setdefault(key, set()).add(text)

    # Resolve durable relationships without copying arbitrary payload data into
    # the operator/read-tool projection.
    for _ in range(5):
        previous_size = sum(len(values) for values in links.values())
        for task_id in list(links.get("task_ids", ())):
            task = state.tasks.get(task_id)
            if task is None:
                continue
            add("project_ids", task.project_id)
            add("agent_ids", task.owner_agent_id, task.created_by_agent_id)
            for deliverable_id in task.deliverable_ids:
                add("deliverable_ids", deliverable_id)
        for deliverable_id in list(links.get("deliverable_ids", ())):
            deliverable = state.deliverables.get(deliverable_id)
            if deliverable is None:
                continue
            add("task_ids", deliverable.task_id)
            add("agent_ids", deliverable.producer_agent_id)
            for review_id in deliverable.review_request_ids:
                add("review_request_ids", review_id)
        for review_id in list(links.get("review_request_ids", ())):
            review = state.review_requests.get(review_id)
            if review is None:
                continue
            add("deliverable_ids", review.deliverable_id)
            add("release_package_ids", review.release_package_id)
            add("agent_ids", review.reviewer_agent_id)
        for release_id in list(links.get("release_package_ids", ())):
            release = state.release_packages.get(release_id)
            if release is None:
                continue
            add("project_ids", release.project_id)
            for deliverable_id in release.deliverable_ids:
                add("deliverable_ids", deliverable_id)
        for blocker_id in list(links.get("blocker_ids", ())):
            blocker = state.blockers.get(blocker_id)
            if blocker is None:
                continue
            add("team_ids", blocker.team_id)
            add("project_ids", blocker.project_id)
            add("task_ids", blocker.task_id)
            add(
                "agent_ids",
                blocker.reporter_agent_id,
                blocker.owner_agent_id,
                blocker.delegate_agent_id,
            )
            if blocker.source_kind == TeamBlockerSourceKind.TASK:
                add("task_ids", blocker.source_id)
            elif blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
                add("deliverable_ids", blocker.source_id)
            elif blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
                add("review_request_ids", blocker.source_id)
            elif blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE:
                add("release_package_ids", blocker.source_id)
            elif (
                blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
                and blocker.source_id.startswith("candidate-correction-successor:")
            ):
                add(
                    "release_package_ids",
                    blocker.source_id.removeprefix("candidate-correction-successor:"),
                )
        for promotion_id in list(links.get("promotion_ids", ())):
            promotion = state.git_promotions.get(promotion_id)
            if promotion is None:
                continue
            add("project_ids", promotion.project_id)
            add("agent_ids", promotion.actor_agent_id)
            add("task_ids", promotion.task_id)
            add("deliverable_ids", promotion.deliverable_id)
            add("release_package_ids", promotion.release_package_id)
        for assignment_id in list(links.get("assignment_ids", ())):
            assignment = state.ephemeral_assignments.get(assignment_id)
            if assignment is None:
                continue
            add("project_ids", assignment.project_id)
            add("agent_ids", assignment.parent_agent_id)
            add("task_ids", assignment.task_id)
            add("session_ids", assignment.session_id)
        for inbox_id in list(links.get("inbox_item_ids", ())):
            item = state.inbox.get(inbox_id)
            if item is None:
                continue
            add("agent_ids", item.recipient_agent_id, item.sender_agent_id)
            add("task_ids", item.task_id)
            for key, values in _runtime_attention_payload_links(item.payload).items():
                for value in values:
                    add(key, value)
        for dispatch_id in list(links.get("dispatch_ids", ())):
            dispatch = state.dispatches.get(dispatch_id)
            if dispatch is None:
                continue
            add("agent_ids", dispatch.agent_id)
            add("session_ids", dispatch.session_id)
            for project_id in dispatch.project_ids:
                add("project_ids", project_id)
            for task_id in dispatch.task_ids:
                add("task_ids", task_id)
            for blocker_id in dispatch.blocker_ids:
                add("blocker_ids", blocker_id)
            for review_id in dispatch.review_request_ids:
                add("review_request_ids", review_id)
            for inbox_id in dispatch.inbox_item_ids:
                add("inbox_item_ids", inbox_id)
        if sum(len(values) for values in links.values()) == previous_size:
            break

    if scope_team_ids:
        for key, values in links.items():
            links[key] = {
                value
                for value in values
                if not (
                    linked_team_ids := _runtime_attention_link_team_ids(
                        state,
                        key=key,
                        id=value,
                    )
                )
                or bool(linked_team_ids.intersection(scope_team_ids))
            }
    for agent_id in list(links.get("agent_ids", ())):
        agent = state.agents.get(agent_id)
        if agent is not None:
            add("team_ids", agent.team_id)
    if not links.get("project_ids"):
        for agent_id in list(links.get("agent_ids", ())):
            for project_id in agent_project_ids(state, agent_id):
                add("project_ids", project_id)
    for project_id in list(links.get("project_ids", ())):
        project = state.projects.get(project_id)
        if project is not None:
            add("team_ids", project.team_id)

    return {key: sorted(values) for key, values in sorted(links.items()) if values}


def _runtime_attention_source_team_ids(
    state: TeamState,
    row: Mapping[str, Any],
) -> set[str]:
    team_ids: set[str] = set()
    direct_team_id = _optional_text(row.get("team_id"))
    if direct_team_id is not None:
        team_ids.add(direct_team_id)
    agent_id = _optional_text(row.get("agent_id"))
    if agent_id is not None and (agent := state.agents.get(agent_id)) is not None:
        if agent.team_id is not None:
            team_ids.add(agent.team_id)
    direct_project_ids = [
        project_id
        for value in [row.get("project_id"), *_sequence_values(row.get("project_ids"))]
        if (project_id := _optional_text(value)) is not None
    ]
    for project_id in direct_project_ids:
        project = state.projects.get(project_id)
        if project is not None:
            team_ids.add(project.team_id)
    return team_ids


def _runtime_attention_link_team_ids(
    state: TeamState,
    *,
    key: str,
    id: str,
) -> set[str]:
    if key == "team_ids":
        return {id} if id in state.teams else set()
    if key == "project_ids":
        project = state.projects.get(id)
        return {project.team_id} if project is not None else set()
    if key == "agent_ids":
        agent = state.agents.get(id)
        return {agent.team_id} if agent is not None and agent.team_id is not None else set()
    if key == "task_ids":
        task = state.tasks.get(id)
        if task is not None and (project := state.projects.get(task.project_id)) is not None:
            return {project.team_id}
    elif key == "deliverable_ids":
        deliverable = state.deliverables.get(id)
        if (
            deliverable is not None
            and (task := state.tasks.get(deliverable.task_id)) is not None
            and (project := state.projects.get(task.project_id)) is not None
        ):
            return {project.team_id}
    elif key == "release_package_ids":
        release = state.release_packages.get(id)
        if release is not None and (project := state.projects.get(release.project_id)) is not None:
            return {project.team_id}
    elif key == "review_request_ids":
        review = state.review_requests.get(id)
        if review is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="deliverable_ids",
                id=review.deliverable_id,
            )
    elif key == "blocker_ids":
        blocker = state.blockers.get(id)
        if blocker is not None:
            return {blocker.team_id}
    elif key == "dispatch_ids":
        dispatch = state.dispatches.get(id)
        if dispatch is not None:
            return {
                state.projects[project_id].team_id
                for project_id in dispatch.project_ids
                if project_id in state.projects
            } or _runtime_attention_link_team_ids(
                state,
                key="agent_ids",
                id=dispatch.agent_id,
            )
    elif key == "assignment_ids":
        assignment = state.ephemeral_assignments.get(id)
        if assignment is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="project_ids",
                id=assignment.project_id,
            )
    elif key == "promotion_ids":
        promotion = state.git_promotions.get(id)
        if promotion is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="project_ids",
                id=promotion.project_id,
            )
    elif key == "inbox_item_ids":
        item = state.inbox.get(id)
        if item is not None:
            return _runtime_attention_link_team_ids(
                state,
                key="agent_ids",
                id=item.recipient_agent_id,
            )
    elif key == "session_ids":
        return {
            agent.team_id
            for agent in state.agents.values()
            if agent.session_id == id and agent.team_id is not None
        }
    return set()


def _sequence_values(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _runtime_attention_terminal_context(
    state: TeamState,
    links: Mapping[str, list[str]],
    *,
    session_health: list[dict[str, Any]],
) -> list[dict[str, str]]:
    contexts: dict[tuple[str, str], dict[str, str]] = {}

    def add(kind: str, id: str, state_value: str) -> None:
        contexts[(kind, id)] = {"kind": kind, "id": id, "state": state_value}

    terminal_task_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    for task_id in links.get("task_ids", []):
        task = state.tasks.get(task_id)
        if task is not None and task.state in terminal_task_states:
            add("task", task.id, task.state.value)
    for deliverable_id in links.get("deliverable_ids", []):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        if deliverable.superseded_by_deliverable_id is not None:
            add("deliverable", deliverable.id, "superseded")
        elif deliverable.state in {
            TeamRecordState.ACCEPTED,
            TeamRecordState.REJECTED,
            TeamRecordState.CANCELLED,
        }:
            add("deliverable", deliverable.id, deliverable.state.value)
    for release_id in links.get("release_package_ids", []):
        release = state.release_packages.get(release_id)
        if release is not None and release.state in {
            TeamRecordState.ACCEPTED,
            TeamRecordState.REJECTED,
            TeamRecordState.CANCELLED,
        }:
            add("release_package", release.id, release.state.value)
    for review_id in links.get("review_request_ids", []):
        review = state.review_requests.get(review_id)
        if review is None:
            continue
        if review.retired_at is not None:
            add("review_request", review.id, "retired")
        elif review.superseded_by_review_request_id is not None:
            add("review_request", review.id, "superseded")
        elif review.state in {
            TeamRecordState.ACCEPTED,
            TeamRecordState.REJECTED,
        }:
            add("review_request", review.id, review.state.value)
    for blocker_id in links.get("blocker_ids", []):
        blocker = state.blockers.get(blocker_id)
        if blocker is not None and blocker.state == TeamBlockerState.RESOLVED:
            add("blocker", blocker.id, blocker.state.value)
    for dispatch_id in links.get("dispatch_ids", []):
        dispatch = state.dispatches.get(dispatch_id)
        if dispatch is not None and dispatch.status in {
            TeamDispatchStatus.COMPLETED,
            TeamDispatchStatus.EXPIRED,
            TeamDispatchStatus.FAILED,
        }:
            add("dispatch", dispatch.id, dispatch.status.value)
    health_by_session = {
        session_id: health
        for health in session_health
        if (session_id := _optional_text(health.get("session_id"))) is not None
    }
    for session_id in links.get("session_ids", []):
        health = health_by_session.get(session_id)
        if health is not None and bool(health.get("is_expired")):
            add("session", session_id, "expired")
    terminal_assignment_states = {
        EphemeralAssignmentState.COMPLETED,
        EphemeralAssignmentState.ACCEPTED,
        EphemeralAssignmentState.REJECTED,
        EphemeralAssignmentState.CLEANED_UP,
        EphemeralAssignmentState.FAILED,
    }
    for assignment_id in links.get("assignment_ids", []):
        assignment = state.ephemeral_assignments.get(assignment_id)
        if assignment is not None and assignment.state in terminal_assignment_states:
            add("ephemeral_assignment", assignment.id, assignment.state.value)
    for promotion_id in links.get("promotion_ids", []):
        promotion = state.git_promotions.get(promotion_id)
        if promotion is not None and promotion.state in {"succeeded", "failed"}:
            add("git_promotion", promotion.id, promotion.state)
    for project_id in links.get("project_ids", []):
        project = state.projects.get(project_id)
        if project is not None and project.status in {
            ProjectStatus.COMPLETED,
            ProjectStatus.ARCHIVED,
        }:
            add("project", project.id, project.status.value)
    for agent_id in links.get("agent_ids", []):
        agent = state.agents.get(agent_id)
        if agent is not None and agent.status == AgentStatus.ARCHIVED:
            add("agent", agent.id, agent.status.value)
    return [contexts[key] for key in sorted(contexts)]


def _runtime_attention_reason(
    kind: str,
    *,
    response_requested: bool,
    reconciliation_required: bool,
) -> str:
    if kind in {"pending_inbox_item", "pending_escalation"}:
        if reconciliation_required:
            return "terminal_work_pending_reconciliation"
        if not response_requested:
            return "pending_without_response_obligation"
        return "awaiting_response"
    return {
        "expired_agent_session": "agent_session_expired",
        "team_blocker": "open_blocker",
        "blocked_task": "task_blocked",
        "waiting_dependency_task": "waiting_on_dependency",
        "failed_action_record": "undismissed_failed_attempt",
        "failed_git_promotion": "failed_git_promotion",
        "blocked_release_package": "release_blocked",
        "failed_ephemeral_assignment": "failed_ephemeral_assignment",
        "cleanup_pending_ephemeral_assignment": "cleanup_pending",
    }.get(kind, "attention_required")


def _runtime_attention_age_bucket(age_seconds: int) -> str:
    if age_seconds < 60 * 60:
        return "lt_1h"
    if age_seconds < 24 * 60 * 60:
        return "lt_1d"
    if age_seconds < 7 * 24 * 60 * 60:
        return "lt_7d"
    return "gte_7d"


def _runtime_attention_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _runtime_attention_sort_key(row: Mapping[str, Any]) -> tuple[int, float, int, str, str]:
    occurred_at = _runtime_attention_datetime(row.get("occurred_at"))
    timestamp = occurred_at.timestamp() if occurred_at is not None else 0.0
    return (
        _RUNTIME_ATTENTION_GROUP_RANK.get(str(row.get("attention_group")), 9),
        -timestamp,
        _severity_rank(row.get("severity")),
        str(row.get("kind") or ""),
        str(row.get("id") or ""),
    )


def _severity_rank(severity: object) -> int:
    order = {"critical": 0, "warning": 1, "info": 2}
    return order.get(str(severity), 9)


def _first_deliverable_id(state: TeamState, task_id: str) -> str | None:
    return _first_deliverable_lookup(state)(task_id)


def _first_deliverable_lookup(state: TeamState) -> Callable[[str], str | None]:
    first_ids: dict[str, str] | None = None

    def lookup(task_id: str) -> str | None:
        nonlocal first_ids
        if first_ids is None:
            first_ids = {}
            for deliverable in state.deliverables.values():
                current = first_ids.get(deliverable.task_id)
                if current is None or deliverable.id < current:
                    first_ids[deliverable.task_id] = deliverable.id
        return first_ids.get(task_id)

    return lookup


def _project_id_for_task(state: TeamState, task_id: str | None) -> str | None:
    if task_id is None:
        return None
    task = state.tasks.get(task_id)
    return task.project_id if task is not None else None


def _task_attention_detail(task: AgentTask, *, dependency_status: str) -> str:
    detail_parts = [f"owner {task.owner_agent_id}", f"state {task.state.value}"]
    if dependency_status != "satisfied":
        detail_parts.append(f"dependencies {dependency_status}")
    blocked_by = ", ".join(task.dependency_task_ids) if task.dependency_task_ids else "none"
    if blocked_by != "none":
        detail_parts.append(f"depends on {blocked_by}")
    return "; ".join(detail_parts)


def _inbox_title(item: AgentInboxItem) -> str:
    label = item.item_type.value.replace("_", " ")
    return label.title()


def _inbox_detail(state: TeamState, item: AgentInboxItem) -> str:
    detail_parts = [f"recipient {item.recipient_agent_id}", f"state {item.state.value}"]
    if item.sender_agent_id is not None:
        detail_parts.append(f"sender {item.sender_agent_id}")
    if item.task_id is not None:
        task = state.tasks.get(item.task_id)
        if task is not None:
            detail_parts.append(f"task {task.id}")
            detail_parts.append(f"project {task.project_id}")
    return "; ".join(detail_parts)


def _action_record_detail(record: AgentActionRecord) -> str:
    action_type = _optional_text(record.action.get("type") or record.action.get("kind"))
    detail_parts = [f"agent {record.agent_id}", f"dispatch {record.dispatch_id}"]
    if action_type is not None:
        detail_parts.append(f"action {action_type}")
    if record.error is not None:
        detail_parts.append(f"error {record.error}")
    return "; ".join(detail_parts)


def _promotion_detail(promotion: GitPromotionRecord) -> str:
    detail_parts = [
        f"actor {promotion.actor_agent_id}",
        f"{promotion.source_branch} -> {promotion.target_branch}",
    ]
    if promotion.error is not None:
        detail_parts.append(f"error {promotion.error}")
    return "; ".join(detail_parts)


def _release_detail(release: ReleasePackage) -> str:
    detail_parts = [f"project {release.project_id}"]
    if release.deliverable_ids:
        detail_parts.append(f"deliverables {', '.join(release.deliverable_ids)}")
    if release.error is not None:
        detail_parts.append(f"error {release.error}")
    return "; ".join(detail_parts)


def _assignment_detail(assignment: EphemeralAssignment) -> str:
    detail_parts = [
        f"parent {assignment.parent_agent_id}",
        f"session {assignment.session_id}",
        f"state {assignment.state.value}",
    ]
    if assignment.error is not None:
        detail_parts.append(f"error {assignment.error}")
    return "; ".join(detail_parts)
