from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from .models import (
    AgentActionRecord,
    AgentInboxItem,
    EphemeralAssignmentState,
    GitPromotionRecord,
    ReleasePackage,
    ReviewRequest,
    TeamInboxItemState,
    TeamModel,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)


class TimelineEntry(TeamModel):
    id: str
    type: str
    category: str
    timestamp: datetime | None = None
    timestamp_source: str
    timestamp_missing: bool = False
    actor_agent_id: str | None = None
    actor_subject: str | None = None
    related_agent_ids: list[str] = Field(default_factory=list)
    team_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    deliverable_id: str | None = None
    review_request_id: str | None = None
    inbox_item_id: str | None = None
    action_record_id: str | None = None
    ephemeral_assignment_id: str | None = None
    git_promotion_id: str | None = None
    release_package_id: str | None = None
    status: str | None = None
    attention_needed: bool = False
    summary: str


def derive_team_timeline(
    state: TeamState,
    *,
    team_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    types: Sequence[str] | str | None = None,
    statuses: Sequence[str] | str | None = None,
    attention_needed: bool | None = None,
) -> dict[str, Any]:
    normalized_types = _normalize_filter_values(types)
    normalized_statuses = _normalize_filter_values(statuses)

    entries = _collect_timeline_entries(state)
    filtered_entries = [
        entry
        for entry in entries
        if _entry_matches_filters(
            entry,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            types=normalized_types,
            statuses=normalized_statuses,
            attention_needed=attention_needed,
        )
    ]
    filtered_entries.sort(key=_timeline_sort_key)

    return {
        "revision": state.revision,
        "derived": True,
        "audit_log": False,
        "timestampless_count": sum(1 for entry in filtered_entries if entry.timestamp is None),
        "entries": [entry.model_dump(mode="json") for entry in filtered_entries],
    }


def _collect_timeline_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    entries.extend(_task_entries(state))
    entries.extend(_inbox_entries(state))
    entries.extend(_deliverable_entries(state))
    entries.extend(_review_entries(state))
    entries.extend(_action_entries(state))
    entries.extend(_ephemeral_entries(state))
    entries.extend(_git_promotion_entries(state))
    entries.extend(_release_entries(state))
    return entries


def _task_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for task in state.tasks.values():
        project = state.projects.get(task.project_id)
        team_id = project.team_id if project is not None else None
        parent = state.tasks.get(task.parent_task_id) if task.parent_task_id is not None else None
        creator_agent_id = task.created_by_agent_id
        related = _unique_agent_ids(
            [task.owner_agent_id, creator_agent_id]
            + ([parent.owner_agent_id] if parent is not None else [])
        )
        entries.append(
            _entry(
                id=f"task:{task.id}:created",
                type="task",
                category="created",
                timestamp=task.created_at,
                timestamp_source="created_at",
                actor_agent_id=creator_agent_id,
                actor_subject=creator_agent_id or task.created_by_actor,
                related_agent_ids=related,
                team_id=team_id,
                project_id=task.project_id,
                task_id=task.id,
                status=task.state.value,
                attention_needed=_task_attention_needed(task.state),
                summary=f"Task {task.id} created: {task.title}",
            )
        )
        if task.updated_at != task.created_at:
            entries.append(
                _entry(
                    id=f"task:{task.id}:updated",
                    type="task",
                    category="updated",
                    timestamp=task.updated_at,
                    timestamp_source="updated_at",
                    actor_agent_id=task.owner_agent_id,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    status=task.state.value,
                    attention_needed=_task_attention_needed(task.state),
                    summary=f"Task {task.id} updated: {task.title}",
                )
            )
    return entries


def _inbox_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for item in state.inbox.values():
        recipient = state.agents.get(item.recipient_agent_id)
        task = state.tasks.get(item.task_id) if item.task_id is not None else None
        project = state.projects.get(task.project_id) if task is not None else None
        team_id = recipient.team_id if recipient is not None else None
        if team_id is None and project is not None:
            team_id = project.team_id
        related = _unique_agent_ids([item.recipient_agent_id, item.sender_agent_id])
        entries.append(
            _entry(
                id=f"inbox:{item.id}:created",
                type="inbox_item",
                category="created",
                timestamp=item.created_at,
                timestamp_source="created_at",
                actor_agent_id=item.sender_agent_id,
                related_agent_ids=related,
                team_id=team_id,
                project_id=task.project_id if task is not None else None,
                task_id=item.task_id,
                inbox_item_id=item.id,
                status=item.state.value,
                attention_needed=item.state == TeamInboxItemState.PENDING,
                summary=f"Inbox item {item.id} created for {item.recipient_agent_id}",
            )
        )
        if item.updated_at != item.created_at:
            category = "handled" if item.state == TeamInboxItemState.HANDLED else "updated"
            entries.append(
                _entry(
                    id=f"inbox:{item.id}:{category}",
                    type="inbox_item",
                    category=category,
                    timestamp=item.updated_at,
                    timestamp_source="updated_at",
                    actor_agent_id=item.sender_agent_id,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=task.project_id if task is not None else None,
                    task_id=item.task_id,
                    inbox_item_id=item.id,
                    status=item.state.value,
                    attention_needed=item.state == TeamInboxItemState.PENDING,
                    summary=_inbox_summary(item),
                )
            )
    return entries


def _deliverable_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for deliverable in state.deliverables.values():
        task = state.tasks.get(deliverable.task_id)
        project = state.projects.get(task.project_id) if task is not None else None
        team_id = project.team_id if project is not None else None
        related = _unique_agent_ids(
            [
                deliverable.producer_agent_id,
                task.owner_agent_id if task is not None else None,
                task.created_by_agent_id if task is not None else None,
            ]
        )
        entries.append(
            _entry(
                id=f"deliverable:{deliverable.id}:created",
                type="deliverable",
                category="created",
                timestamp=deliverable.created_at,
                timestamp_source="created_at",
                actor_agent_id=deliverable.producer_agent_id,
                related_agent_ids=related,
                team_id=team_id,
                project_id=task.project_id if task is not None else None,
                task_id=deliverable.task_id,
                deliverable_id=deliverable.id,
                status=deliverable.state.value,
                attention_needed=_record_attention_needed(deliverable.state),
                summary=f"Deliverable {deliverable.id} created: {deliverable.summary}",
            )
        )
    return entries


def _review_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for review in state.review_requests.values():
        deliverable = state.deliverables.get(review.deliverable_id)
        task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
        project = state.projects.get(task.project_id) if task is not None else None
        team_id = project.team_id if project is not None else None
        related = _unique_agent_ids(
            [
                review.reviewer_agent_id,
                deliverable.producer_agent_id if deliverable is not None else None,
                task.owner_agent_id if task is not None else None,
                task.created_by_agent_id if task is not None else None,
            ]
        )
        entries.append(
            _entry(
                id=f"review:{review.id}:created",
                type="review",
                category="requested",
                timestamp=review.created_at,
                timestamp_source="created_at",
                timestamp_missing=review.created_at is None,
                actor_agent_id=review.reviewer_agent_id,
                related_agent_ids=related,
                team_id=team_id,
                project_id=task.project_id if task is not None else None,
                task_id=task.id if task is not None else None,
                deliverable_id=review.deliverable_id,
                review_request_id=review.id,
                status=review.state.value,
                attention_needed=_record_attention_needed(review.state),
                summary=f"Review requested for deliverable {review.deliverable_id}",
            )
        )
        if review.decision_at is not None or review.state != TeamRecordState.PENDING:
            entries.append(
                _entry(
                    id=f"review:{review.id}:decision",
                    type="review",
                    category="decision",
                    timestamp=review.decision_at,
                    timestamp_source="decision_at",
                    timestamp_missing=review.decision_at is None,
                    actor_agent_id=review.reviewer_agent_id,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=task.project_id if task is not None else None,
                    task_id=task.id if task is not None else None,
                    deliverable_id=review.deliverable_id,
                    review_request_id=review.id,
                    status=review.state.value,
                    attention_needed=_review_decision_attention_needed(review.state),
                    summary=_review_decision_summary(review),
                )
            )
    return entries


def _action_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for record in state.action_records.values():
        agent = state.agents.get(record.agent_id)
        team_id = agent.team_id if agent is not None else None
        project_id = agent.project_id if agent is not None else None
        entries.append(
            _entry(
                id=f"action:{record.id}:created",
                type="action",
                category="created",
                timestamp=record.created_at,
                timestamp_source="created_at",
                actor_agent_id=record.agent_id,
                related_agent_ids=_unique_agent_ids([record.agent_id]),
                team_id=team_id,
                project_id=project_id,
                action_record_id=record.id,
                status="succeeded" if record.success else "failed",
                attention_needed=not record.success,
                summary=_action_summary(record),
            )
        )
    return entries


def _ephemeral_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for assignment in state.ephemeral_assignments.values():
        project = state.projects.get(assignment.project_id)
        team_id = project.team_id if project is not None else None
        related = _unique_agent_ids([assignment.parent_agent_id])
        entries.append(
            _entry(
                id=f"ephemeral:{assignment.id}:requested",
                type="ephemeral_assignment",
                category="requested",
                timestamp=assignment.created_at,
                timestamp_source="created_at",
                actor_agent_id=assignment.parent_agent_id,
                related_agent_ids=related,
                team_id=team_id,
                project_id=assignment.project_id,
                task_id=assignment.task_id,
                ephemeral_assignment_id=assignment.id,
                status=assignment.state.value,
                attention_needed=_ephemeral_attention_needed(assignment.state),
                summary=f"Ephemeral assignment {assignment.id} requested for task {assignment.task_id}",
            )
        )
        if assignment.updated_at != assignment.created_at:
            entries.append(
                _entry(
                    id=f"ephemeral:{assignment.id}:state",
                    type="ephemeral_assignment",
                    category="state",
                    timestamp=assignment.updated_at,
                    timestamp_source="updated_at",
                    actor_agent_id=assignment.parent_agent_id,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=assignment.project_id,
                    task_id=assignment.task_id,
                    ephemeral_assignment_id=assignment.id,
                    status=assignment.state.value,
                    attention_needed=_ephemeral_attention_needed(assignment.state),
                    summary=f"Ephemeral assignment {assignment.id} {assignment.state.value}",
                )
            )
    return entries


def _git_promotion_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for promotion in state.git_promotions.values():
        project = state.projects.get(promotion.project_id)
        team_id = project.team_id if project is not None else None
        entries.append(
            _entry(
                id=f"git-promotion:{promotion.id}:requested",
                type="git_promotion",
                category="requested",
                timestamp=promotion.created_at,
                timestamp_source="created_at",
                actor_agent_id=promotion.actor_agent_id,
                related_agent_ids=_unique_agent_ids([promotion.actor_agent_id]),
                team_id=team_id,
                project_id=promotion.project_id,
                task_id=promotion.task_id,
                deliverable_id=promotion.deliverable_id,
                release_package_id=promotion.release_package_id,
                git_promotion_id=promotion.id,
                status=promotion.state,
                attention_needed=_git_promotion_attention_needed(promotion.state),
                summary=_git_promotion_requested_summary(promotion),
            )
        )
        if promotion.state != "pending":
            entries.append(
                _entry(
                    id=f"git-promotion:{promotion.id}:result",
                    type="git_promotion",
                    category="result",
                    timestamp=promotion.updated_at,
                    timestamp_source="updated_at",
                    actor_agent_id=promotion.actor_agent_id,
                    related_agent_ids=_unique_agent_ids([promotion.actor_agent_id]),
                    team_id=team_id,
                    project_id=promotion.project_id,
                    task_id=promotion.task_id,
                    deliverable_id=promotion.deliverable_id,
                    release_package_id=promotion.release_package_id,
                    git_promotion_id=promotion.id,
                    status=promotion.state,
                    attention_needed=promotion.state == "failed",
                    summary=_git_promotion_result_summary(promotion),
                )
            )
    return entries


def _release_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for release in state.release_packages.values():
        project = state.projects.get(release.project_id)
        team_id = project.team_id if project is not None else None
        related = _unique_agent_ids(
            [
                project.lead_agent_id if project is not None else None,
                *(
                    state.deliverables[deliverable_id].producer_agent_id
                    for deliverable_id in release.deliverable_ids
                    if deliverable_id in state.deliverables
                ),
            ]
        )
        entries.append(
            _entry(
                id=f"release:{release.id}:created",
                type="release_package",
                category="created",
                timestamp=release.created_at,
                timestamp_source="created_at",
                actor_agent_id=project.lead_agent_id if project is not None else None,
                related_agent_ids=related,
                team_id=team_id,
                project_id=release.project_id,
                release_package_id=release.id,
                status=release.state.value,
                attention_needed=_release_attention_needed(release.state),
                summary=f"Release package {release.id} created for project {release.project_id}",
            )
        )
        if release.state == TeamRecordState.ACCEPTED:
            timestamp = release.published_at or release.updated_at
            entries.append(
                _entry(
                    id=f"release:{release.id}:published",
                    type="release_package",
                    category="published",
                    timestamp=timestamp,
                    timestamp_source="published_at" if release.published_at is not None else "updated_at",
                    timestamp_missing=timestamp is None,
                    actor_agent_id=project.lead_agent_id if project is not None else None,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=release.project_id,
                    release_package_id=release.id,
                    status=release.state.value,
                    attention_needed=_release_attention_needed(release.state),
                    summary=_release_summary(release),
                )
            )
        elif release.state == TeamRecordState.BLOCKED:
            entries.append(
                _entry(
                    id=f"release:{release.id}:blocked",
                    type="release_package",
                    category="blocked",
                    timestamp=release.updated_at,
                    timestamp_source="updated_at",
                    timestamp_missing=release.updated_at is None,
                    actor_agent_id=project.lead_agent_id if project is not None else None,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=release.project_id,
                    release_package_id=release.id,
                    status=release.state.value,
                    attention_needed=_release_attention_needed(release.state),
                    summary=_release_summary(release),
                )
            )
    return entries


def _entry(
    *,
    id: str,
    type: str,
    category: str,
    timestamp: datetime | None,
    timestamp_source: str,
    actor_agent_id: str | None = None,
    actor_subject: str | None = None,
    related_agent_ids: Sequence[str] | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    deliverable_id: str | None = None,
    review_request_id: str | None = None,
    inbox_item_id: str | None = None,
    action_record_id: str | None = None,
    ephemeral_assignment_id: str | None = None,
    git_promotion_id: str | None = None,
    release_package_id: str | None = None,
    status: str | None = None,
    attention_needed: bool = False,
    summary: str,
    timestamp_missing: bool | None = None,
) -> TimelineEntry:
    effective_timestamp_missing = timestamp is None if timestamp_missing is None else timestamp_missing
    return TimelineEntry(
        id=id,
        type=type,
        category=category,
        timestamp=timestamp,
        timestamp_source=timestamp_source,
        timestamp_missing=effective_timestamp_missing,
        actor_agent_id=actor_agent_id,
        actor_subject=actor_subject or actor_agent_id,
        related_agent_ids=_unique_agent_ids(list(related_agent_ids or [])),
        team_id=team_id,
        project_id=project_id,
        task_id=task_id,
        deliverable_id=deliverable_id,
        review_request_id=review_request_id,
        inbox_item_id=inbox_item_id,
        action_record_id=action_record_id,
        ephemeral_assignment_id=ephemeral_assignment_id,
        git_promotion_id=git_promotion_id,
        release_package_id=release_package_id,
        status=status,
        attention_needed=attention_needed,
        summary=summary,
    )


def _entry_matches_filters(
    entry: TimelineEntry,
    *,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    types: Sequence[str] | None,
    statuses: Sequence[str] | None,
    attention_needed: bool | None,
) -> bool:
    if team_id is not None and entry.team_id != team_id:
        return False
    if project_id is not None and entry.project_id != project_id:
        return False
    if agent_id is not None and agent_id not in _entry_agent_ids(entry):
        return False
    if types is not None and entry.type not in types:
        return False
    if statuses is not None and (entry.status is None or entry.status not in statuses):
        return False
    if attention_needed is not None and entry.attention_needed != attention_needed:
        return False
    return True


def _entry_agent_ids(entry: TimelineEntry) -> list[str]:
    ids = list(entry.related_agent_ids)
    if entry.actor_agent_id is not None:
        ids.append(entry.actor_agent_id)
    return _unique_agent_ids(ids)


def _timeline_sort_key(entry: TimelineEntry) -> tuple[int, datetime, str, str, str]:
    missing = entry.timestamp is None
    timestamp = _normalized_sort_timestamp(entry.timestamp)
    return (1 if missing else 0, timestamp, entry.category, entry.type, entry.id)


def _normalized_sort_timestamp(timestamp: datetime | None) -> datetime:
    if timestamp is None:
        return datetime.min.replace(tzinfo=UTC)
    if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _normalize_filter_values(values: Sequence[str] | str | None) -> list[str] | None:
    if values is None:
        return None
    raw_values = [values] if isinstance(values, str) else list(values)
    normalized: list[str] = []
    for value in raw_values:
        for item in str(value).split(","):
            text = item.strip()
            if text:
                normalized.append(text)
    if not normalized:
        return None
    return list(dict.fromkeys(normalized))


def _unique_agent_ids(values: Sequence[str | None]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _task_attention_needed(state: TeamTaskState) -> bool:
    return state in {
        TeamTaskState.PENDING,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
        TeamTaskState.REJECTED,
    }


def _record_attention_needed(state: TeamRecordState) -> bool:
    return state in {
        TeamRecordState.PENDING,
        TeamRecordState.BLOCKED,
        TeamRecordState.REJECTED,
        TeamRecordState.IN_PROGRESS,
    }


def _review_decision_attention_needed(state: TeamRecordState) -> bool:
    return state in {TeamRecordState.BLOCKED, TeamRecordState.REJECTED}


def _ephemeral_attention_needed(state: EphemeralAssignmentState) -> bool:
    return state in {
        EphemeralAssignmentState.REQUESTED,
        EphemeralAssignmentState.SPAWN_PENDING,
        EphemeralAssignmentState.CLEANUP_PENDING,
        EphemeralAssignmentState.REJECTED,
        EphemeralAssignmentState.FAILED,
    }


def _git_promotion_attention_needed(state: str) -> bool:
    return state in {"pending", "failed"}


def _release_attention_needed(state: TeamRecordState) -> bool:
    return state in {TeamRecordState.PENDING, TeamRecordState.BLOCKED}


def _inbox_summary(item: AgentInboxItem) -> str:
    if item.state == TeamInboxItemState.HANDLED:
        return f"Inbox item {item.id} handled"
    return f"Inbox item {item.id} updated"


def _review_decision_summary(review: ReviewRequest) -> str:
    return f"Review {review.id} decided {review.state.value}"


def _action_summary(record: AgentActionRecord) -> str:
    action_type = record.action.get("type") if isinstance(record.action, dict) else None
    action_summary = record.action.get("summary") if isinstance(record.action, dict) else None
    if isinstance(action_summary, str) and action_summary.strip():
        detail = action_summary.strip()
    elif isinstance(action_type, str) and action_type.strip():
        detail = action_type.strip()
    else:
        detail = "created"
    return f"Action record {record.id} {detail}"


def _git_promotion_requested_summary(promotion: GitPromotionRecord) -> str:
    return (
        f"Git promotion {promotion.id} requested from {promotion.source_branch} "
        f"to {promotion.target_branch}"
    )


def _git_promotion_result_summary(promotion: GitPromotionRecord) -> str:
    return f"Git promotion {promotion.id} {promotion.state}"


def _release_summary(release: ReleasePackage) -> str:
    if release.state == TeamRecordState.ACCEPTED:
        return f"Release package {release.id} published"
    if release.state == TeamRecordState.BLOCKED:
        return f"Release package {release.id} blocked"
    return f"Release package {release.id} pending"
