from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from collections.abc import Mapping
from typing import Iterable

from ...models import (
    ACTIVE_TEAM_CASE_STATUS_VALUES,
    AgentInboxItem,
    AgentTask,
    PersistentAgent,
    ReviewRequest,
    TaskAssessmentReadiness,
    TeamBlockerRoute,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)
from .agent_access import _visible_project_ids_for_agent
from .preparation_failures import (
    EXECUTION_PREPARATION_BLOCKER_REASON_CODES,
    TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES,
    task_has_current_preparation_blocker,
)
from .task_assessment import TaskAssessmentService


@dataclass(frozen=True)
class AgentDispatchScope:
    task_ids: tuple[str, ...]
    blocker_ids: tuple[str, ...]
    review_request_ids: tuple[str, ...]
    inbox_item_ids: tuple[str, ...]
    project_ids: tuple[str, ...]
    managed_repo_ids: tuple[str, ...]
    source_evidence_snapshot_ids: tuple[str, ...]

    def model_fields(self) -> dict[str, list[str]]:
        return {
            "task_ids": list(self.task_ids),
            "blocker_ids": list(self.blocker_ids),
            "review_request_ids": list(self.review_request_ids),
            "inbox_item_ids": list(self.inbox_item_ids),
            "project_ids": list(self.project_ids),
            "managed_repo_ids": list(self.managed_repo_ids),
            "source_evidence_snapshot_ids": list(self.source_evidence_snapshot_ids),
        }


def actionable_dispatch_tasks(
    state: TeamState,
    *,
    agent: PersistentAgent,
) -> list[AgentTask]:
    if agent.team_id is None:
        return []
    visible_project_ids = _visible_project_ids_for_agent(state, agent)
    assessments = TaskAssessmentService(state)
    stalled_task_ids = _stalled_task_ids(state, agent_id=agent.id)
    rows = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent.id
        and task.project_id in visible_project_ids
        and task.project_id in state.projects
        and state.projects[task.project_id].team_id == agent.team_id
        and task.id not in stalled_task_ids
        and not _task_has_open_execution_preparation_blocker(state, task.id)
        and assessments.assess(task.id).readiness == TaskAssessmentReadiness.ACTIONABLE
    ]
    rows.sort(key=lambda item: (item.priority, item.created_at, item.id))
    return rows


def _task_has_open_execution_preparation_blocker(
    state: TeamState,
    task_id: str,
) -> bool:
    return task_has_current_preparation_blocker(state, task_id=task_id)


def actionable_dispatch_reviews(
    reviews: Iterable[ReviewRequest],
) -> list[ReviewRequest]:
    rows = [
        review
        for review in reviews
        if review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
    ]
    rows.sort(
        key=lambda item: (
            item.created_at or datetime.min.replace(tzinfo=UTC),
            item.id,
        )
    )
    return rows


def mcp_capability_blocked_review_ids(
    state: TeamState,
    *,
    agent_id: str,
) -> set[str]:
    """Return review work already quarantined by an open MCP capability blocker."""

    blocked_review_ids: set[str] = set()
    for blocker in state.blockers.values():
        if (
            blocker.state != TeamBlockerState.OPEN
            or blocker.source_kind != TeamBlockerSourceKind.MCP_CAPABILITY
        ):
            continue
        identity = blocker.mcp_capability_identity
        if identity is not None:
            if identity.agent_id == agent_id:
                blocked_review_ids.update(identity.review_request_ids)
            continue
        # Compatibility for blockers created before capability identity was
        # stored structurally.
        prefix = f"{agent_id}:"
        if blocker.source_id.startswith(prefix):
            blocked_review_ids.update(
                review_id for review_id in blocker.source_id[len(prefix) :].split("|") if review_id
            )
    return blocked_review_ids


def inbox_references_review_ids(
    item: AgentInboxItem,
    review_request_ids: set[str],
) -> bool:
    """Return whether an inbox item is only an already-blocked review notice."""

    if not review_request_ids:
        return False
    payload = item.payload if isinstance(item.payload, Mapping) else {}
    review_id = payload.get("review_request_id")
    return isinstance(review_id, str) and review_id.strip() in review_request_ids


def build_agent_dispatch_scope(
    state: TeamState,
    *,
    agent: PersistentAgent,
    tasks: Iterable[AgentTask],
    blockers: Iterable[TeamBlocker],
    reviews: Iterable[ReviewRequest],
    inbox_items: Iterable[AgentInboxItem],
) -> AgentDispatchScope:
    inbox_rows = _unique_by_id(inbox_items)
    blocker_rows = _unique_by_id(
        [
            *blockers,
            *_actionable_blockers_referenced_by_inbox(
                state,
                agent=agent,
                inbox_items=inbox_rows,
            ),
        ]
    )
    task_rows = _unique_by_id(
        [
            *tasks,
            *_assigned_blocker_recovery_tasks(
                state,
                agent=agent,
                blockers=blocker_rows,
            ),
            *_owned_task_scoped_operator_directive_tasks(
                state,
                agent=agent,
                inbox_items=inbox_rows,
            ),
        ]
    )
    review_rows = _unique_by_id(reviews)

    project_ids: set[str] = set()
    for task in task_rows:
        project_ids.add(task.project_id)
    for blocker in blocker_rows:
        project_ids.update(_blocker_project_ids(state, blocker))
    for review in review_rows:
        project_ids.update(_review_project_ids(state, review))
    for item in inbox_rows:
        project_ids.update(_inbox_project_ids(state, item))

    scoped_project_ids = tuple(
        sorted(
            project_id
            for project_id in project_ids
            if project_id in state.projects and state.projects[project_id].team_id == agent.team_id
        )
    )
    managed_repo_ids = tuple(
        sorted(
            {
                project.git.managed_repo_id
                for project_id in scoped_project_ids
                if (project := state.projects[project_id]).git is not None
            }
        )
    )
    evidence_task_ids = {row.id for row in task_rows}
    for review in review_rows:
        deliverable = (
            state.deliverables.get(review.deliverable_id)
            if review.deliverable_id is not None
            else None
        )
        if deliverable is not None:
            evidence_task_ids.add(deliverable.task_id)
    for item in inbox_rows:
        if item.item_type != TeamInboxItemType.DELIVERABLE_NOTICE:
            continue
        deliverable_id = item.payload.get("deliverable_id")
        deliverable = (
            state.deliverables.get(deliverable_id)
            if isinstance(deliverable_id, str)
            else None
        )
        if deliverable is not None:
            evidence_task_ids.add(deliverable.task_id)
        elif item.task_id is not None:
            evidence_task_ids.add(item.task_id)
    source_evidence_snapshot_ids = external_source_evidence_snapshot_ids_for_dispatch(
        state,
        agent=agent,
        task_ids=evidence_task_ids,
        project_ids=scoped_project_ids,
    )
    return AgentDispatchScope(
        task_ids=tuple(row.id for row in task_rows),
        blocker_ids=tuple(row.id for row in blocker_rows),
        review_request_ids=tuple(row.id for row in review_rows),
        inbox_item_ids=tuple(row.id for row in inbox_rows),
        project_ids=scoped_project_ids,
        managed_repo_ids=managed_repo_ids,
        source_evidence_snapshot_ids=source_evidence_snapshot_ids,
    )


def _owned_task_scoped_operator_directive_tasks(
    state: TeamState,
    *,
    agent: PersistentAgent,
    inbox_items: Iterable[AgentInboxItem],
) -> list[AgentTask]:
    """Authorize the owner task explicitly selected by an operator directive.

    Most task-bearing inbox items are observations or handoffs, and must not
    grant a recipient authority over the referenced task. An operator directive
    is the narrow exception: when it targets a task owned by its recipient, the
    task must be included even if normal readiness is temporarily invalid (for
    example, while repairing a failed review-to-acceptance transition).
    """

    tasks: list[AgentTask] = []
    for item in inbox_items:
        if item.item_type != TeamInboxItemType.OPERATOR_DIRECTIVE or item.task_id is None:
            continue
        task = state.tasks.get(item.task_id)
        if task is not None and task.owner_agent_id == agent.id:
            tasks.append(task)
    return tasks


def build_preparation_recovery_dispatch_scope(
    *,
    blockers: Iterable[TeamBlocker],
    inbox_items: Iterable[AgentInboxItem],
) -> AgentDispatchScope:
    """Freeze a preparation incident without retrying the failed preparation.

    Generic blocker scope intentionally infers an owned source task and its
    project so an assignee can repair the underlying product. Preparation
    blockers are different: repository or artifact setup already failed before
    inference, so re-adding that task/project would repeat the same setup before
    the recovery owner can inspect, resolve, or reroute the incident.
    """

    blocker_rows = [
        blocker
        for blocker in _unique_by_id(blockers)
        if blocker.reason_code in EXECUTION_PREPARATION_BLOCKER_REASON_CODES
    ]
    inbox_rows = _unique_by_id(inbox_items)
    return AgentDispatchScope(
        task_ids=(),
        blocker_ids=tuple(row.id for row in blocker_rows),
        review_request_ids=(),
        inbox_item_ids=tuple(row.id for row in inbox_rows),
        project_ids=(),
        managed_repo_ids=(),
        source_evidence_snapshot_ids=(),
    )


def external_source_evidence_snapshot_ids_for_dispatch(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task_ids: Iterable[str],
    project_ids: Iterable[str],
    include_team_cases: bool = False,
) -> tuple[str, ...]:
    """Return immutable source snapshots already authorized by dispatch scope."""

    scoped_project_ids = set(project_ids)
    scoped_task_ids = {
        task_id
        for task_id in task_ids
        if (task := state.tasks.get(task_id)) is not None
        and task.project_id in scoped_project_ids
    }
    snapshot_ids: set[str] = {
        snapshot_id
        for task_id in scoped_task_ids
        for snapshot_id in state.tasks[task_id].source_evidence_snapshot_ids
    }
    active_owned_task_ids = {
        task.id
        for task in state.tasks.values()
        if task.owner_agent_id == agent.id
        and task.state
        not in {
            TeamTaskState.FINISHED,
            TeamTaskState.ACCEPTED,
            TeamTaskState.REJECTED,
            TeamTaskState.CANCELLED,
        }
    }
    for case in state.cases.values():
        case_matches_scoped_task = bool(
            set(case.task_ids).intersection(scoped_task_ids)
        )
        if (
            not case_matches_scoped_task
            and case.status.value not in ACTIVE_TEAM_CASE_STATUS_VALUES
        ):
            continue
        if include_team_cases:
            if agent.team_id is None or case.team_id != agent.team_id:
                continue
        elif (
            case.owner_agent_id != agent.id
            and not case_matches_scoped_task
            and not any(task_id in active_owned_task_ids for task_id in case.task_ids)
        ):
            continue
        if case.project_id is not None and case.project_id not in scoped_project_ids:
            continue
        snapshot_ids.update(case.source_evidence_snapshot_ids)
    effective_team_id = agent.team_id or agent.retired_team_id
    return tuple(
        sorted(
            snapshot_id
            for snapshot_id in snapshot_ids
            if (snapshot := state.external_source_evidence_snapshots.get(snapshot_id))
            is not None
            and snapshot.team_id == effective_team_id
            and (
                snapshot.project_id is None
                or snapshot.project_id in scoped_project_ids
            )
        )
    )


def _actionable_blockers_referenced_by_inbox(
    state: TeamState,
    *,
    agent: PersistentAgent,
    inbox_items: Iterable[AgentInboxItem],
) -> list[TeamBlocker]:
    """Return only blockers an actionable inbox item asks this agent to handle.

    Generated blocker notices already carry a structured ``blocker_id``. Agent
    questions may instead quote the durable identifier in prose, so match only
    exact, known IDs and retain the blocker's current owner/delegate boundary.
    The command layer still applies the action-specific blocker policy.
    """

    rows: dict[str, TeamBlocker] = {}
    for item in inbox_items:
        if (
            item.recipient_agent_id != agent.id
            or item.state != TeamInboxItemState.PENDING
            or not item.response_requested
        ):
            continue
        referenced_ids = _structured_inbox_blocker_ids(item)
        if item.item_type == TeamInboxItemType.QUESTION:
            referenced_ids.update(_question_text_blocker_ids(state, item))
        for blocker_id in referenced_ids:
            blocker = state.blockers.get(blocker_id)
            if (
                blocker is not None
                and blocker.state == TeamBlockerState.OPEN
                and _blocker_is_assigned_to_agent(blocker, agent_id=agent.id)
            ):
                rows[blocker.id] = blocker
    return [rows[blocker_id] for blocker_id in sorted(rows)]


def _structured_inbox_blocker_ids(item: AgentInboxItem) -> set[str]:
    values: list[object] = [item.payload.get("blocker_id")]
    blocker_ids = item.payload.get("blocker_ids")
    if isinstance(blocker_ids, (list, tuple, set)):
        values.extend(blocker_ids)
    return {text for value in values if (text := str(value or "").strip())}


def _question_text_blocker_ids(
    state: TeamState,
    item: AgentInboxItem,
) -> set[str]:
    text = "\n".join(str(item.payload.get(key) or "") for key in ("question", "message", "summary"))
    return {
        blocker_id
        for blocker_id in state.blockers
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(blocker_id)}(?![A-Za-z0-9_-])",
            text,
        )
    }


def _blocker_is_assigned_to_agent(
    blocker: TeamBlocker,
    *,
    agent_id: str,
) -> bool:
    return (
        blocker.route == TeamBlockerRoute.DELEGATED and blocker.delegate_agent_id == agent_id
    ) or (
        blocker.route in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
        and blocker.owner_agent_id == agent_id
    )


def _assigned_blocker_recovery_tasks(
    state: TeamState,
    *,
    agent: PersistentAgent,
    blockers: Iterable[TeamBlocker],
) -> list[AgentTask]:
    """Freeze only owned, visible source tasks needed for assigned recovery.

    A blocked task is intentionally absent from the ordinary actionable-task
    list. When a dispatch assigns its blocker back to the task owner, retaining
    that omission prevents the owner from submitting the corrective product.
    The blocker relationship supplies the missing task ID, but never expands
    scope to a foreign task merely because that task is related to the blocker.
    """

    if agent.team_id is None:
        return []
    visible_project_ids = _visible_project_ids_for_agent(state, agent)
    active_task_states = {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
        TeamTaskState.REJECTED,
    }
    rows: dict[str, AgentTask] = {}
    for blocker in blockers:
        if (
            blocker.state != TeamBlockerState.OPEN
            or blocker.reason_code in EXECUTION_PREPARATION_BLOCKER_REASON_CODES
            or not _blocker_is_assigned_to_agent(
                blocker,
                agent_id=agent.id,
            )
        ):
            continue
        for task_id in _blocker_recovery_task_ids(state, blocker):
            task = state.tasks.get(task_id)
            if (
                task is None
                or task.owner_agent_id != agent.id
                or task.state not in active_task_states
                or task.project_id not in visible_project_ids
                or task.project_id not in state.projects
                or state.projects[task.project_id].team_id != agent.team_id
            ):
                continue
            rows[task.id] = task
    return [rows[task_id] for task_id in sorted(rows)]


def _blocker_recovery_task_ids(
    state: TeamState,
    blocker: TeamBlocker,
) -> set[str]:
    task_ids = set(blocker.affected_task_ids)
    if blocker.task_id is not None:
        task_ids.add(blocker.task_id)
    if blocker.source_kind == TeamBlockerSourceKind.TASK:
        task_ids.add(blocker.source_id)
    elif blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
        deliverable = state.deliverables.get(blocker.source_id)
        if deliverable is not None:
            task_ids.add(deliverable.task_id)
    elif blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
        review = state.review_requests.get(blocker.source_id)
        deliverable = (
            state.deliverables.get(review.deliverable_id or "") if review is not None else None
        )
        if deliverable is not None:
            task_ids.add(deliverable.task_id)
    return task_ids


def _unique_by_id(rows: Iterable[object]) -> list:
    by_id = {str(getattr(row, "id")): row for row in rows}
    return [by_id[row_id] for row_id in sorted(by_id)]


def _stalled_task_ids(state: TeamState, *, agent_id: str) -> set[str]:
    active_stalls = [
        blocker
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL
        and blocker.source_id == agent_id
    ]
    if not active_stalls:
        return set()
    affected = {task_id for blocker in active_stalls for task_id in blocker.affected_task_ids}
    if affected:
        return affected
    return {task.id for task in state.tasks.values() if task.owner_agent_id == agent_id}


def _task_project_id(state: TeamState, task_id: object) -> str | None:
    task = state.tasks.get(str(task_id or ""))
    return task.project_id if task is not None else None


def _deliverable_project_id(state: TeamState, deliverable_id: object) -> str | None:
    deliverable = state.deliverables.get(str(deliverable_id or ""))
    return _task_project_id(state, deliverable.task_id) if deliverable is not None else None


def _release_project_id(state: TeamState, release_id: object) -> str | None:
    release = state.release_packages.get(str(release_id or ""))
    return release.project_id if release is not None else None


def _review_project_ids(state: TeamState, review: ReviewRequest) -> set[str]:
    project_ids = {
        project_id
        for project_id in (
            _deliverable_project_id(state, review.deliverable_id),
            _release_project_id(state, review.release_package_id),
        )
        if project_id is not None
    }
    return project_ids


def _blocker_project_ids(state: TeamState, blocker: TeamBlocker) -> set[str]:
    if blocker.reason_code in {
        *TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES,
        "inbox_execution_preparation_failed",
        "review_preparation_failed",
    }:
        # Recovery for a setup incident must remain possible even when the
        # repository itself is the unavailable resource. The durable blocker
        # still carries project/task identity in Team context, but it does not
        # ask the wake provider to repeat the failed setup merely to discuss or
        # resolve that blocker.
        return set()
    project_ids = {
        project_id
        for project_id in (
            blocker.project_id,
            _task_project_id(state, blocker.task_id),
        )
        if project_id is not None
    }
    project_ids.update(
        project_id
        for task_id in blocker.affected_task_ids
        if (project_id := _task_project_id(state, task_id)) is not None
    )
    if blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
        review = state.review_requests.get(blocker.source_id)
        if review is not None:
            project_ids.update(_review_project_ids(state, review))
    elif blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
        project_id = _deliverable_project_id(state, blocker.source_id)
        if project_id is not None:
            project_ids.add(project_id)
    elif blocker.source_kind in {
        TeamBlockerSourceKind.RELEASE_PACKAGE,
        TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
    }:
        project_id = _release_project_id(state, blocker.source_id)
        if project_id is not None:
            project_ids.add(project_id)
    elif (
        blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
        and blocker.source_id.startswith("candidate-correction-successor:")
    ):
        project_id = _release_project_id(
            state,
            blocker.source_id.removeprefix("candidate-correction-successor:"),
        )
        if project_id is not None:
            project_ids.add(project_id)
    return project_ids


def _inbox_project_ids(state: TeamState, item: AgentInboxItem) -> set[str]:
    blocker = state.blockers.get(str(item.payload.get("blocker_id") or ""))
    if blocker is not None and blocker.reason_code in {
        *TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES,
        "inbox_execution_preparation_failed",
        "review_preparation_failed",
    }:
        return set()
    project_ids = {
        project_id
        for project_id in (
            _task_project_id(state, item.task_id),
            _task_project_id(state, item.payload.get("task_id")),
            str(item.payload.get("project_id") or "").strip() or None,
            _deliverable_project_id(state, item.payload.get("deliverable_id")),
            _release_project_id(state, item.payload.get("release_package_id")),
        )
        if project_id is not None
    }
    if blocker is not None:
        project_ids.update(_blocker_project_ids(state, blocker))
    review = state.review_requests.get(str(item.payload.get("review_request_id") or ""))
    if review is not None:
        project_ids.update(_review_project_ids(state, review))
    return project_ids


__all__ = [
    "AgentDispatchScope",
    "actionable_dispatch_reviews",
    "actionable_dispatch_tasks",
    "build_agent_dispatch_scope",
    "build_preparation_recovery_dispatch_scope",
    "inbox_references_review_ids",
    "mcp_capability_blocked_review_ids",
]
