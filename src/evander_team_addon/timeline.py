from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    agent_project_ids,
    project_lead_agent_id,
)


class TimelineChange(TeamModel):
    field: str
    before: str | None = None
    after: str | None = None
    before_known: bool = False


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
    subject: str | None = None
    change: TimelineChange | None = None
    reason: str | None = None
    process: str | None = None
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
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    normalized_types = _normalize_filter_values(types)
    normalized_statuses = _normalize_filter_values(statuses)
    normalized_offset = max(0, offset)

    entries = _collect_timeline_entries(state)
    type_facet_entries = [
        entry
        for entry in entries
        if _entry_matches_filters(
            entry,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            types=None,
            statuses=normalized_statuses,
            attention_needed=attention_needed,
        )
    ]
    status_facet_entries = [
        entry
        for entry in entries
        if _entry_matches_filters(
            entry,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            types=normalized_types,
            statuses=None,
            attention_needed=attention_needed,
        )
    ]
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
    filtered_entries.sort(key=_timeline_tiebreaker_key)
    filtered_entries.sort(key=_timeline_timestamp_sort_key, reverse=True)
    total_count = len(filtered_entries)
    if limit is None:
        windowed_entries = filtered_entries
        normalized_limit = total_count
    else:
        normalized_limit = max(1, limit)
        windowed_entries = filtered_entries[
            normalized_offset : normalized_offset + normalized_limit
        ]

    return {
        "revision": state.revision,
        "derived": True,
        "audit_log": False,
        "timestampless_count": sum(1 for entry in filtered_entries if entry.timestamp is None),
        "total_count": total_count,
        "limit": normalized_limit,
        "offset": normalized_offset,
        "has_more": normalized_offset + len(windowed_entries) < total_count,
        "facets": {
            "types": sorted({entry.type for entry in type_facet_entries}),
            "statuses": sorted(
                {entry.status for entry in status_facet_entries if entry.status is not None}
            ),
        },
        "entries": [entry.model_dump(mode="json") for entry in windowed_entries],
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
        creator_subject = (
            _agent_subject(state, creator_agent_id)
            if creator_agent_id is not None
            else task.created_by_actor
        )
        entries.append(
            _entry(
                id=f"task:{task.id}:created",
                type="task",
                category="created",
                timestamp=task.created_at,
                timestamp_source="created_at",
                actor_agent_id=creator_agent_id,
                actor_subject=creator_subject,
                related_agent_ids=related,
                team_id=team_id,
                project_id=task.project_id,
                task_id=task.id,
                status=task.state.value,
                attention_needed=_task_attention_needed(task.state),
                subject=task.title,
                process=(
                    _process_subject(task.created_by_actor)
                    if creator_agent_id is None
                    else None
                ),
                summary=f"Task created: {task.title}",
            )
        )
        if task.updated_at != task.created_at:
            category = "updated"
            actor_agent_id = None
            actor_subject = None
            process = None
            reason = None
            change = None
            summary = f"Task snapshot updated: {task.title}"
            cancellation = task.cancellation
            if (
                task.state == TeamTaskState.CANCELLED
                and cancellation is not None
                and _timestamps_equal(cancellation.cancelled_at, task.updated_at)
            ):
                category = "cancelled"
                if cancellation.actor_kind == "agent":
                    actor_agent_id = cancellation.actor_id
                    actor_subject = _agent_subject(state, cancellation.actor_id)
                else:
                    actor_subject = cancellation.actor_id
                    process = _humanize_token(cancellation.actor_kind)
                reason = cancellation.reason
                change = TimelineChange(
                    field="State",
                    after=_humanize_token(task.state.value),
                    before_known=False,
                )
                summary = f"Task cancelled: {task.title}"
            elif _timestamps_equal(task.state_entered_at, task.updated_at):
                change = TimelineChange(
                    field="State",
                    after=_humanize_token(task.state.value),
                    before_known=False,
                )
                summary = (
                    f"Task entered {_humanize_token(task.state.value).lower()}: "
                    f"{task.title}"
                )
            entries.append(
                _entry(
                    id=f"task:{task.id}:{category}",
                    type="task",
                    category=category,
                    timestamp=task.updated_at,
                    timestamp_source="updated_at",
                    actor_agent_id=actor_agent_id,
                    actor_subject=actor_subject,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    status=task.state.value,
                    attention_needed=_task_attention_needed(task.state),
                    subject=task.title,
                    change=change,
                    reason=reason,
                    process=process,
                    summary=summary,
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
        subject = _inbox_subject(item, task_title=task.title if task is not None else None)
        item_type = _humanize_token(item.item_type.value)
        sender_subject = _agent_subject(state, item.sender_agent_id)
        entries.append(
            _entry(
                id=f"inbox:{item.id}:created",
                type="inbox_item",
                category="created",
                timestamp=item.created_at,
                timestamp_source="created_at",
                actor_agent_id=item.sender_agent_id,
                actor_subject=sender_subject,
                related_agent_ids=related,
                team_id=team_id,
                project_id=task.project_id if task is not None else None,
                task_id=item.task_id,
                inbox_item_id=item.id,
                status=item.state.value,
                attention_needed=item.state == TeamInboxItemState.PENDING,
                subject=subject,
                process=(
                    _inbox_process(item.payload)
                    if item.sender_agent_id is None
                    else None
                ),
                summary=f"{item_type} sent: {subject}",
            )
        )
        if item.updated_at != item.created_at:
            category = "handled" if item.state == TeamInboxItemState.HANDLED else "updated"
            change = (
                TimelineChange(
                    field="State",
                    before=_humanize_token(TeamInboxItemState.PENDING.value),
                    after=_humanize_token(TeamInboxItemState.HANDLED.value),
                    before_known=True,
                )
                if category == "handled"
                else None
            )
            entries.append(
                _entry(
                    id=f"inbox:{item.id}:{category}",
                    type="inbox_item",
                    category=category,
                    timestamp=item.updated_at,
                    timestamp_source="updated_at",
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=task.project_id if task is not None else None,
                    task_id=item.task_id,
                    inbox_item_id=item.id,
                    status=item.state.value,
                    attention_needed=item.state == TeamInboxItemState.PENDING,
                    subject=subject,
                    change=change,
                    summary=(
                        f"{item_type} handled: {subject}"
                        if category == "handled"
                        else f"{item_type} snapshot updated: {subject}"
                    ),
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
        subject = _deliverable_subject(deliverable.summary, task.title if task else None)
        entries.append(
            _entry(
                id=f"deliverable:{deliverable.id}:created",
                type="deliverable",
                category="created",
                timestamp=deliverable.created_at,
                timestamp_source="created_at",
                actor_agent_id=deliverable.producer_agent_id,
                actor_subject=_agent_subject(state, deliverable.producer_agent_id),
                related_agent_ids=related,
                team_id=team_id,
                project_id=task.project_id if task is not None else None,
                task_id=deliverable.task_id,
                deliverable_id=deliverable.id,
                status=deliverable.state.value,
                attention_needed=_record_attention_needed(deliverable.state),
                subject=subject,
                summary=f"Deliverable created: {subject}",
            )
        )
    return entries


def _review_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for review in state.review_requests.values():
        deliverable = (
            state.deliverables.get(review.deliverable_id)
            if review.deliverable_id is not None
            else None
        )
        release = (
            state.release_packages.get(review.release_package_id)
            if review.release_package_id is not None
            else None
        )
        task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
        project_id = (
            task.project_id
            if task is not None
            else release.project_id
            if release is not None
            else None
        )
        project = state.projects.get(project_id) if project_id is not None else None
        team_id = project.team_id if project is not None else None
        release_related_agent_ids = (
            _release_related_agent_ids(state, release)
            if release is not None
            else []
        )
        related = _unique_agent_ids(
            [
                review.reviewer_agent_id,
                review.requested_by_agent_id,
                deliverable.producer_agent_id if deliverable is not None else None,
                task.owner_agent_id if task is not None else None,
                task.created_by_agent_id if task is not None else None,
                *release_related_agent_ids,
            ]
        )
        subject = (
            _deliverable_subject(deliverable.summary, task.title if task else None)
            if deliverable is not None
            else _release_subject(state, release)
            if release is not None
            else "Retained review target unavailable"
        )
        requester_subject = (
            _agent_subject(state, review.requested_by_agent_id)
            if review.requested_by_agent_id is not None
            else review.requested_by_actor
        )
        entries.append(
            _entry(
                id=f"review:{review.id}:created",
                type="review",
                category="requested",
                timestamp=review.created_at,
                timestamp_source="created_at",
                timestamp_missing=review.created_at is None,
                actor_agent_id=review.requested_by_agent_id,
                actor_subject=requester_subject,
                related_agent_ids=related,
                team_id=team_id,
                project_id=project_id,
                task_id=task.id if task is not None else None,
                deliverable_id=review.deliverable_id,
                release_package_id=review.release_package_id,
                review_request_id=review.id,
                status=review.state.value,
                attention_needed=_record_attention_needed(review.state),
                subject=subject,
                reason=review.retry_reason,
                process=(
                    _process_subject(review.requested_by_actor)
                    if review.requested_by_agent_id is None
                    else None
                ),
                summary=f"Review requested: {subject}",
            )
        )
        if review.decision_at is not None or review.state != TeamRecordState.PENDING:
            decision = _humanize_token(review.state.value)
            entries.append(
                _entry(
                    id=f"review:{review.id}:decision",
                    type="review",
                    category="decision",
                    timestamp=review.decision_at,
                    timestamp_source="decision_at",
                    timestamp_missing=review.decision_at is None,
                    actor_agent_id=review.reviewer_agent_id,
                    actor_subject=_agent_subject(state, review.reviewer_agent_id),
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=project_id,
                    task_id=task.id if task is not None else None,
                    deliverable_id=review.deliverable_id,
                    release_package_id=review.release_package_id,
                    review_request_id=review.id,
                    status=review.state.value,
                    attention_needed=_review_decision_attention_needed(review.state),
                    subject=subject,
                    change=TimelineChange(
                        field="Decision",
                        after=decision,
                        before_known=False,
                    ),
                    reason=_review_reason(review),
                    summary=f"Review {decision.lower()}: {subject}",
                )
            )
    return entries


def _action_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for record in state.action_records.values():
        agent = state.agents.get(record.agent_id)
        team_id = agent.team_id if agent is not None else None
        assigned_project_ids = (
            agent_project_ids(state, record.agent_id) if agent is not None else []
        )
        project_id = assigned_project_ids[0] if assigned_project_ids else None
        subject = _action_subject(record)
        status = "succeeded" if record.success else "failed"
        entries.append(
            _entry(
                id=f"action:{record.id}:created",
                type="action",
                category="created",
                timestamp=record.created_at,
                timestamp_source="created_at",
                actor_agent_id=record.agent_id,
                actor_subject=_agent_subject(state, record.agent_id),
                related_agent_ids=_unique_agent_ids([record.agent_id]),
                team_id=team_id,
                project_id=project_id,
                action_record_id=record.id,
                status=status,
                attention_needed=not record.success and record.dismissed_at is None,
                subject=subject,
                reason=record.error if not record.success else None,
                process=_action_process(record),
                summary=f"Action {status}: {subject}",
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
                actor_subject=_agent_subject(state, assignment.parent_agent_id),
                related_agent_ids=related,
                team_id=team_id,
                project_id=assignment.project_id,
                task_id=assignment.task_id,
                ephemeral_assignment_id=assignment.id,
                status=assignment.state.value,
                attention_needed=_ephemeral_attention_needed(assignment.state),
                subject=assignment.title,
                summary=f"Temporary assignment requested: {assignment.title}",
            )
        )
        if assignment.updated_at != assignment.created_at:
            assignment_state = _humanize_token(assignment.state.value)
            entries.append(
                _entry(
                    id=f"ephemeral:{assignment.id}:state",
                    type="ephemeral_assignment",
                    category="state",
                    timestamp=assignment.updated_at,
                    timestamp_source="updated_at",
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=assignment.project_id,
                    task_id=assignment.task_id,
                    ephemeral_assignment_id=assignment.id,
                    status=assignment.state.value,
                    attention_needed=_ephemeral_attention_needed(assignment.state),
                    subject=assignment.title,
                    change=TimelineChange(
                        field="State",
                        after=assignment_state,
                        before_known=False,
                    ),
                    reason=assignment.error,
                    summary=(
                        f"Temporary assignment {assignment_state.lower()}: "
                        f"{assignment.title}"
                    ),
                )
            )
    return entries


def _git_promotion_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for promotion in state.git_promotions.values():
        project = state.projects.get(promotion.project_id)
        team_id = project.team_id if project is not None else None
        related = _unique_agent_ids([promotion.actor_agent_id])
        subject = _git_promotion_subject(state, promotion)
        promotion_process = f"{_humanize_token(promotion.promotion_kind)} promotion"
        entries.append(
            _entry(
                id=f"git-promotion:{promotion.id}:requested",
                type="git_promotion",
                category="requested",
                timestamp=promotion.created_at,
                timestamp_source="created_at",
                actor_agent_id=promotion.actor_agent_id,
                actor_subject=_agent_subject(state, promotion.actor_agent_id),
                related_agent_ids=related,
                team_id=team_id,
                project_id=promotion.project_id,
                task_id=promotion.task_id,
                deliverable_id=promotion.deliverable_id,
                release_package_id=promotion.release_package_id,
                git_promotion_id=promotion.id,
                status=promotion.state,
                attention_needed=_git_promotion_attention_needed(promotion.state),
                subject=subject,
                process=promotion_process,
                summary=f"{promotion_process} requested: {subject}",
            )
        )
        if promotion.state != "pending":
            promotion_state = _humanize_token(promotion.state)
            entries.append(
                _entry(
                    id=f"git-promotion:{promotion.id}:result",
                    type="git_promotion",
                    category="result",
                    timestamp=promotion.updated_at,
                    timestamp_source="updated_at",
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=promotion.project_id,
                    task_id=promotion.task_id,
                    deliverable_id=promotion.deliverable_id,
                    release_package_id=promotion.release_package_id,
                    git_promotion_id=promotion.id,
                    status=promotion.state,
                    attention_needed=promotion.state == "failed",
                    subject=subject,
                    change=TimelineChange(
                        field="State",
                        before=_humanize_token("pending"),
                        after=promotion_state,
                        before_known=True,
                    ),
                    reason=promotion.error,
                    process="Git promotion worker",
                    summary=(
                        f"{promotion_process} {promotion_state.lower()}: {subject}"
                    ),
                )
            )
    return entries


def _release_entries(state: TeamState) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for release in state.release_packages.values():
        project = state.projects.get(release.project_id)
        team_id = project.team_id if project is not None else None
        related = _release_related_agent_ids(state, release)
        subject = _release_subject(state, release)
        entries.append(
            _entry(
                id=f"release:{release.id}:created",
                type="release_package",
                category="created",
                timestamp=release.created_at,
                timestamp_source="created_at",
                related_agent_ids=related,
                team_id=team_id,
                project_id=release.project_id,
                release_package_id=release.id,
                status=release.state.value,
                attention_needed=_release_attention_needed(release.state),
                subject=subject,
                summary=f"Release prepared: {subject}",
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
                    timestamp_source="published_at"
                    if release.published_at is not None
                    else "updated_at",
                    timestamp_missing=timestamp is None,
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=release.project_id,
                    release_package_id=release.id,
                    status=release.state.value,
                    attention_needed=_release_attention_needed(release.state),
                    subject=subject,
                    change=TimelineChange(
                        field="Publication",
                        after="Published",
                        before_known=False,
                    ),
                    summary=f"Release published: {subject}",
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
                    related_agent_ids=related,
                    team_id=team_id,
                    project_id=release.project_id,
                    release_package_id=release.id,
                    status=release.state.value,
                    attention_needed=_release_attention_needed(release.state),
                    subject=subject,
                    change=TimelineChange(
                        field="State",
                        after=_humanize_token(release.state.value),
                        before_known=False,
                    ),
                    reason=_release_reason(release),
                    summary=f"Release blocked: {subject}",
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
    subject: str | None = None,
    change: TimelineChange | Mapping[str, Any] | None = None,
    reason: str | None = None,
    process: str | None = None,
    summary: str,
    timestamp_missing: bool | None = None,
) -> TimelineEntry:
    effective_timestamp_missing = (
        timestamp is None if timestamp_missing is None else timestamp_missing
    )
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
        subject=subject,
        change=change,
        reason=reason,
        process=process,
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


def _timeline_timestamp_sort_key(entry: TimelineEntry) -> datetime:
    return _normalized_sort_timestamp(entry.timestamp)


def _timeline_tiebreaker_key(entry: TimelineEntry) -> tuple[str, str, str]:
    return (entry.category, entry.type, entry.id)


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


def _agent_subject(state: TeamState, agent_id: str | None) -> str | None:
    if agent_id is None:
        return None
    agent = state.agents.get(agent_id)
    if agent is not None and agent.display_name:
        return agent.display_name
    return agent_id


def _process_subject(actor_subject: str | None) -> str | None:
    normalized = _optional_summary_text(actor_subject)
    if normalized is None:
        return None
    prefix = normalized.split(":", 1)[0].strip()
    if prefix.casefold() in {"operator", "system", "workflow", "admin"}:
        return _humanize_token(prefix)
    return None


def _inbox_subject(item: AgentInboxItem, *, task_title: str | None) -> str:
    payload = item.payload if isinstance(item.payload, Mapping) else {}
    for key in (
        "title",
        "subject",
        "summary",
        "question",
        "message",
        "instructions",
    ):
        candidate = _optional_summary_text(payload.get(key))
        if candidate is not None:
            return _preview(candidate)
    if task_title:
        return _preview(task_title)
    return _humanize_token(item.item_type.value)


def _inbox_process(payload: Mapping[str, Any]) -> str | None:
    for key in ("source", "origin", "created_by"):
        candidate = _optional_summary_text(payload.get(key))
        if candidate is not None:
            return _preview(candidate, limit=80)
    return None


def _deliverable_subject(summary: str, task_title: str | None) -> str:
    if task_title:
        return _preview(task_title)
    first_line = next(
        (line.strip() for line in str(summary).splitlines() if line.strip()),
        "Deliverable outcome",
    )
    return _preview(first_line)


def _review_reason(review: ReviewRequest) -> str | None:
    parts: list[str] = []
    if review.block_kind is not None:
        parts.append(f"Blocked by {_humanize_token(review.block_kind.value).lower()}")
    parts.extend(_preview(finding, limit=240) for finding in review.findings if finding.strip())
    return " · ".join(parts) or None


def _action_subject(record: AgentActionRecord) -> str:
    action_type = record.action.get("type") if isinstance(record.action, dict) else None
    action_summary = record.action.get("summary") if isinstance(record.action, dict) else None
    if isinstance(action_summary, str) and action_summary.strip():
        return _preview(action_summary)
    elif isinstance(action_type, str) and action_type.strip():
        return _humanize_token(action_type)
    return "Recorded action"


def _action_process(record: AgentActionRecord) -> str | None:
    action_type = record.action.get("type") if isinstance(record.action, dict) else None
    if isinstance(action_type, str) and action_type.strip():
        return _humanize_token(action_type)
    return None


def _git_promotion_subject(state: TeamState, promotion: GitPromotionRecord) -> str:
    if promotion.task_id is not None:
        task = state.tasks.get(promotion.task_id)
        if task is not None:
            return _preview(task.title)
    if promotion.deliverable_id is not None:
        deliverable = state.deliverables.get(promotion.deliverable_id)
        if deliverable is not None:
            task = state.tasks.get(deliverable.task_id)
            return _deliverable_subject(
                deliverable.summary,
                task.title if task is not None else None,
            )
    if promotion.release_package_id is not None:
        release = state.release_packages.get(promotion.release_package_id)
        if release is not None:
            return _release_subject(state, release)
    project = state.projects.get(promotion.project_id)
    project_name = project.name if project is not None else "Project"
    return f"{project_name} {_humanize_token(promotion.promotion_kind).lower()} promotion"


def _release_related_agent_ids(
    state: TeamState,
    release: ReleasePackage,
) -> list[str]:
    project = state.projects.get(release.project_id)
    return _unique_agent_ids(
        [
            project_lead_agent_id(state, release.project_id)
            if project is not None
            else None,
            *(
                state.deliverables[deliverable_id].producer_agent_id
                for deliverable_id in release.deliverable_ids
                if deliverable_id in state.deliverables
            ),
        ]
    )


def _release_subject(state: TeamState, release: ReleasePackage | None) -> str:
    if release is None:
        return "Retained release target unavailable"
    project = state.projects.get(release.project_id)
    project_name = project.name if project is not None else "Project"
    work_titles: list[str] = []
    seen_titles: set[str] = set()
    for deliverable_id in release.deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        task = state.tasks.get(deliverable.task_id)
        title = _deliverable_subject(
            deliverable.summary,
            task.title if task is not None else None,
        )
        if title in seen_titles:
            continue
        seen_titles.add(title)
        work_titles.append(title)
    if not work_titles:
        return f"{project_name} release"
    first = work_titles[0]
    remainder = len(work_titles) - 1
    work_label = f"{first} + {remainder} more" if remainder else first
    return f"{project_name}: {work_label}"


def _release_reason(release: ReleasePackage) -> str | None:
    if release.error:
        return _preview(release.error, limit=400)
    if release.gate_failure is not None:
        return _preview(release.gate_failure.message, limit=400)
    return None


def _timestamps_equal(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    return _normalized_sort_timestamp(left) == _normalized_sort_timestamp(right)


def _optional_summary_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _preview(value: object, *, limit: int = 120) -> str:
    normalized = _optional_summary_text(value) or ""
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(1, limit - 1)].rstrip()}…"


def _humanize_token(value: object) -> str:
    normalized = str(value or "").strip().replace("_", " ").replace("-", " ")
    if not normalized:
        return "Unknown"
    return normalized[0].upper() + normalized[1:]
