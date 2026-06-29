from __future__ import annotations

# ruff: noqa: F401

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from agent_sandbox.admin.user_notifications import AdminUserNotificationStore
from agent_sandbox.models import utc_now

from ..git_naming import (
    canonical_release_branch,
    ephemeral_agent_branch,
    feature_branch,
    release_branch,
    review_branch,
    validate_branch_name,
)
from ..models import (
    AGENT_OPERATOR_ONLY_FIELDS,
    AgentActionRecord,
    AgentContextClearingPolicy,
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentAvatar,
    AgentVoiceProfile,
    AgentInboxItem,
    AgentRuntime,
    AgentStandDown,
    AgentStandDownSource,
    AgentTask,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    Deliverable,
    DeliverableGitRefs,
    DeliverableVerification,
    EphemeralAssignment,
    EphemeralAssignmentState,
    EphemeralAgentPolicy,
    AgentStatus,
    GitPromotionRecord,
    GovernanceAgendaItem,
    GovernanceAgendaItemState,
    GovernanceAgendaItemType,
    GovernanceGoal,
    GovernanceGroup,
    GovernanceRuntime,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGoal,
    ProjectStatus,
    ReleasePackage,
    ReviewCriterionResult,
    ReviewProfile,
    ReviewProfileCriterion,
    ReviewRequest,
    RolePermission,
    Team,
    TeamCase,
    TeamCaseStatus,
    TeamConversation,
    TeamConversationMessage,
    TeamIdempotencyRecord,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamInboxItemType,
    TeamOutboxRecord,
    TeamOutboxStatus,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    release_package_payload_with_derived,
    runtime_operations_payload,
    task_payload_with_derived,
    agent_has_project_assignment,
    agent_lead_project_ids,
    agent_project_assignment_payloads,
    agent_project_ids,
    effective_role_permissions,
    project_lead_agent_id,
    _ensure_unique,
    _unique_text_list,
)
from ..templates import TeamTemplate, get_template

ADDON_ID = "team-addon"
EPHEMERAL_REQUESTED_EVENT_TYPE = "team.ephemeral.requested"
EPHEMERAL_COMPLETED_EVENT_TYPE = "team.ephemeral.completed"
EPHEMERAL_WORKER_REPORT_TOOL = "team_report_ephemeral_result"
CHAT_MESSAGE_QUEUED_EVENT_TYPE = "team.chat.message_queued"
AGENT_WAKE_REQUESTED_EVENT_TYPE = "team.agent.wake_requested"
OPERATOR_ESCALATION_EVENT_TYPE = "team.operator.escalation_requested"
GIT_PROMOTION_EVENT_TYPE = "team.git.promotion_requested"
GIT_RELEASE_RECOVERY_EVENT_TYPE = "team.git.release_recovery_requested"
DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS = 10.0
DEFAULT_MAX_NO_PROGRESS_COUNT = 3

_NON_TERMINAL_TASK_STATES = frozenset(
    {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
    }
)
_MANAGER_FALLBACK_OPEN_WORK_LIMIT = 20
_MANAGER_FALLBACK_PENDING_INBOX_LIMIT = 10
_MANAGER_FALLBACK_FAILED_ACTION_LIMIT = 3
_MANAGER_FALLBACK_ROUTING_GAP_LIMIT = 20
_MANAGER_FALLBACK_TEXT_LIMIT = 240
_MANAGER_FALLBACK_PENDING_REVIEW_STATES = frozenset(
    {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
)
_ACTIVE_DELIVERABLE_WORK_STATES = _MANAGER_FALLBACK_PENDING_REVIEW_STATES
_DELIVERABLE_VERIFICATION_STATUSES = frozenset(
    {"passed", "failed", "partial", "not_run", "unknown"}
)
_DISPATCH_NON_PROGRESS_CLASSES = frozenset({"", "none", "context", "context_only"})
_DISPATCH_CONTEXT_SUPPRESSED_CLASSES = frozenset(
    {"none", "progress_no_context", "progress_report", "status_report"}
)
_UNSET: Any = object()
_MCP_TASK_UPDATE_STATE_VALUES = (
    TeamTaskState.PENDING.value,
    TeamTaskState.IN_PROGRESS.value,
    TeamTaskState.BLOCKED.value,
    TeamTaskState.WAITING_FOR_REVIEW.value,
    TeamTaskState.FINISHED.value,
)


def _set_task_state(task: AgentTask, state: TeamTaskState, *, now: datetime | None = None) -> bool:
    if task.state == state:
        return False
    effective_now = now or utc_now()
    task.state = state
    task.state_entered_at = effective_now
    return True


@dataclass(frozen=True)
class _TaskCreationPlan:
    task_id: str
    owner_id: str
    project_id: str
    case_id: str | None
    task_state: TeamTaskState
    parent_task_id: str | None
    dependency_task_ids: list[str]
    priority: int
    title: str
    description: str


def _require_present(collection: dict[str, Any], key: str, label: str) -> Any:
    if key not in collection:
        raise ValueError(f"{label} {key} does not exist")
    return collection[key]


def _require_absent(collection: dict[str, Any], key: str, label: str) -> None:
    if key in collection:
        raise ValueError(f"{label} {key} already exists")


def _validate_case_scope(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    owner_agent_id: str | None,
) -> None:
    _require_present(state.teams, team_id, "team")
    if project_id is not None:
        project = _require_present(state.projects, project_id, "project")
        if project.team_id != team_id:
            raise ValueError(f"project {project_id} does not belong to team {team_id}")
    if owner_agent_id is not None:
        owner = _require_present(state.agents, owner_agent_id, "case owner")
        if owner.team_id != team_id:
            raise ValueError(f"case owner {owner_agent_id} does not belong to team {team_id}")


def _case_can_link_task(state: TeamState, *, case: TeamCase, task: AgentTask) -> bool:
    project = state.projects.get(task.project_id)
    if project is None:
        return False
    if project.team_id != case.team_id:
        return False
    if case.project_id is not None and task.project_id != case.project_id:
        return False
    return True


def _link_task_to_case(
    state: TeamState,
    *,
    task_id: str,
    case_id: str,
    now: datetime,
) -> None:
    case = _require_present(state.cases, case_id, "case")
    task = _require_present(state.tasks, task_id, "task")
    if not _case_can_link_task(state, case=case, task=task):
        raise ValueError(f"task {task_id} cannot be linked to case {case_id}")
    if task.case_id is not None and task.case_id != case_id:
        _unlink_task_from_case(state, task_id=task_id, case_id=task.case_id, now=now)
    task.case_id = case_id
    task.updated_at = now
    case.task_ids = _append_unique(case.task_ids, task_id)
    case.updated_at = now


def _unlink_task_from_case(
    state: TeamState,
    *,
    task_id: str,
    case_id: str,
    now: datetime,
) -> None:
    case = state.cases.get(case_id)
    if case is not None and task_id in case.task_ids:
        case.task_ids = [item for item in case.task_ids if item != task_id]
        case.updated_at = now
    task = state.tasks.get(task_id)
    if task is not None and task.case_id == case_id:
        task.case_id = None
        task.updated_at = now


def _sync_case_task_link(
    state: TeamState,
    *,
    task: AgentTask,
    old_case_id: str | None,
    new_case_id: str | None,
    now: datetime,
) -> None:
    if old_case_id == new_case_id:
        if new_case_id is not None:
            _link_task_to_case(state, task_id=task.id, case_id=new_case_id, now=now)
        return
    if old_case_id is not None:
        _unlink_task_from_case(state, task_id=task.id, case_id=old_case_id, now=now)
    if new_case_id is not None:
        _link_task_to_case(state, task_id=task.id, case_id=new_case_id, now=now)


def _append_unique(values: list[str], value: str) -> list[str]:
    if value in values:
        return values
    return [*values, value]


_GENERATED_ID_PART_RE = re.compile(r"[^a-z0-9]+")


def _slug_id_part(value: object, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    slug = _GENERATED_ID_PART_RE.sub("-", text).strip("-")
    return slug or fallback


def _generated_id(
    *,
    prefix: str,
    labels: list[object],
    existing_ids: Mapping[str, Any] | set[str],
) -> str:
    prefix_part = _slug_id_part(prefix, fallback="record")
    label_parts = [
        _slug_id_part(label, fallback="") for label in labels if str(label or "").strip()
    ]
    base = "-".join([prefix_part, *[part for part in label_parts if part]])
    used = set(existing_ids.keys() if isinstance(existing_ids, Mapping) else existing_ids)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _unique_id_from_base(
    base: str,
    *,
    existing_ids: Mapping[str, Any] | set[str],
) -> str:
    candidate = _required_text(base, "id")
    used = set(existing_ids.keys() if isinstance(existing_ids, Mapping) else existing_ids)
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _project_goals_with_generated_ids(
    goals: list[ProjectGoal | Mapping[str, Any]],
    *,
    project_id: str,
) -> list[ProjectGoal]:
    normalized: list[ProjectGoal] = []
    used_ids: set[str] = set()
    for index, raw_goal in enumerate(goals):
        if isinstance(raw_goal, ProjectGoal):
            payload = raw_goal.model_dump(mode="json")
        elif isinstance(raw_goal, Mapping):
            payload = dict(raw_goal)
        else:
            raise ValueError(f"goals[{index}] must be a JSON object")
        goal_id = _optional_text(payload.get("id"))
        if goal_id is None:
            goal_id = _generated_id(
                prefix="goal",
                labels=[project_id, payload.get("description")],
                existing_ids=used_ids,
            )
        if goal_id in used_ids:
            raise ValueError(f"duplicate project goal id: {goal_id}")
        used_ids.add(goal_id)
        payload["id"] = goal_id
        normalized.append(ProjectGoal.model_validate(payload))
    return normalized


def _governance_goals_with_generated_ids(
    goals: list[GovernanceGoal | Mapping[str, Any]],
    *,
    governance_group_id: str,
) -> list[GovernanceGoal]:
    normalized: list[GovernanceGoal] = []
    used_ids: set[str] = set()
    for index, raw_goal in enumerate(goals):
        if isinstance(raw_goal, GovernanceGoal):
            payload = raw_goal.model_dump(mode="json")
        elif isinstance(raw_goal, Mapping):
            payload = dict(raw_goal)
        else:
            raise ValueError(f"goals[{index}] must be a JSON object")
        goal_id = _optional_text(payload.get("id"))
        if goal_id is None:
            goal_id = _generated_id(
                prefix="governance-goal",
                labels=[governance_group_id, payload.get("description")],
                existing_ids=used_ids,
            )
        if goal_id in used_ids:
            raise ValueError(f"duplicate governance goal id: {goal_id}")
        used_ids.add(goal_id)
        payload["id"] = goal_id
        normalized.append(GovernanceGoal.model_validate(payload))
    return normalized


def _remove_value(values: list[str], value: str) -> list[str]:
    return [item for item in values if item != value]


def _default_role_id(
    *,
    team_id: str,
    project_id: str | None,
    role_key: str,
) -> str:
    scope = project_id or team_id
    return f"{scope}__{_role_id_part(role_key)}"


def _role_id_part(value: str) -> str:
    return _required_text(value, "role_key").replace("_", "-")


def _project_assignment_id(
    project_id: str,
    agent_id: str,
    kind: AgentProjectAssignmentKind,
    *,
    existing_ids: Mapping[str, Any],
) -> str:
    return _unique_id_from_base(
        f"{project_id}__{agent_id}__{kind.value}",
        existing_ids=existing_ids,
    )


def _normalize_role_permissions(
    permissions: list[RolePermission | str] | None,
) -> list[RolePermission]:
    if permissions is None:
        return []
    normalized: list[RolePermission] = []
    for permission in permissions:
        item = (
            permission
            if isinstance(permission, RolePermission)
            else RolePermission(str(permission))
        )
        if item not in normalized:
            normalized.append(item)
    return normalized


def _agent_team_removal_blockers(state: TeamState, agent: PersistentAgent) -> list[str]:
    if agent.team_id is None:
        return []
    blockers: list[str] = []
    for team in state.teams.values():
        if team.manager_agent_id == agent.id:
            blockers.append(f"manager of team {team.id}")
    for assignment in state.project_assignments.values():
        if (
            assignment.agent_id == agent.id
            and assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD
        ):
            blockers.append(f"lead of project {assignment.project_id}")
    if agent.subordinate_agent_ids:
        blockers.append("has subordinate agents " + ", ".join(sorted(agent.subordinate_agent_ids)))
    for other in state.agents.values():
        if other.reports_to_agent_id == agent.id:
            blockers.append(f"superior of agent {other.id}")
    terminal_task_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
    }
    for task in state.tasks.values():
        if task.state in terminal_task_states:
            continue
        if task.owner_agent_id == agent.id:
            blockers.append(f"owns task {task.id}")
        if task.created_by_agent_id == agent.id:
            blockers.append(f"created task {task.id}")
    terminal_record_states = {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
    for deliverable in state.deliverables.values():
        if deliverable.state in terminal_record_states:
            continue
        if deliverable.producer_agent_id == agent.id:
            blockers.append(f"produced deliverable {deliverable.id}")
    for review in state.review_requests.values():
        if review.state in terminal_record_states:
            continue
        if review.reviewer_agent_id == agent.id:
            blockers.append(f"reviews request {review.id}")
    for item in state.inbox.values():
        if item.state != TeamInboxItemState.PENDING:
            continue
        if item.recipient_agent_id == agent.id:
            blockers.append(f"recipient of inbox item {item.id}")
    runtime = state.agent_runtimes.get(agent.id)
    if runtime is not None and runtime.active_dispatch_id is not None:
        blockers.append(f"active dispatch {runtime.active_dispatch_id}")
    return list(dict.fromkeys(blockers))


def _validate_role_assignment(
    role: TeamRole,
    *,
    team_id: str | None,
    project_ids: list[str],
) -> None:
    if team_id is None:
        raise ValueError(f"role {role.id} cannot be assigned to a teamless agent")
    if role.team_id != team_id:
        raise ValueError(f"role {role.id} does not belong to team {team_id}")
    if role.project_id is not None and role.project_id not in project_ids:
        raise ValueError(f"role {role.id} does not belong to any assigned project")


def _validate_role_filter_assignment(role: TeamRole, *, profile: ReviewProfile) -> None:
    if role.team_id != profile.team_id:
        raise ValueError(f"role {role.id} does not belong to team {profile.team_id}")
    if role.project_id is not None and role.project_id != profile.project_id:
        raise ValueError(f"role {role.id} does not apply to project {profile.project_id}")


def _agent_payload(state: TeamState, agent: PersistentAgent) -> dict[str, Any]:
    payload = agent.model_dump(mode="json", exclude=AGENT_OPERATOR_ONLY_FIELDS)
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    effective_permissions = effective_role_permissions(state, agent)
    payload["role_name"] = role.name if role is not None else None
    payload["role_description"] = role.description if role is not None else ""
    payload["role_permissions"] = [permission.value for permission in effective_permissions]
    payload["effective_role_permissions"] = [
        permission.value for permission in effective_permissions
    ]
    payload["project_assignments"] = agent_project_assignment_payloads(state, agent.id)
    payload["project_ids"] = agent_project_ids(state, agent.id)
    payload["lead_project_ids"] = agent_lead_project_ids(state, agent.id)
    return payload


def _subordinate_agent_payloads(state: TeamState, agent: PersistentAgent) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for subordinate_id in agent.subordinate_agent_ids:
        subordinate = state.agents.get(subordinate_id)
        if subordinate is None:
            continue
        payloads.append(_agent_payload(state, subordinate))
    return payloads


def _is_active_deliverable_work(deliverable: Deliverable) -> bool:
    return (
        deliverable.state in _ACTIVE_DELIVERABLE_WORK_STATES
        and deliverable.superseded_by_deliverable_id is None
    )


def _task_has_active_deliverable_work(state: TeamState, task_id: str) -> bool:
    return any(
        deliverable.task_id == task_id and _is_active_deliverable_work(deliverable)
        for deliverable in state.deliverables.values()
    )


def _team_ids_with_open_work(state: TeamState) -> set[str]:
    team_ids: set[str] = set()
    for task in state.tasks.values():
        if task.state not in _NON_TERMINAL_TASK_STATES:
            continue
        owner = state.agents.get(task.owner_agent_id)
        if owner is not None and owner.team_id is not None:
            team_ids.add(owner.team_id)
    for deliverable in state.deliverables.values():
        if not _is_active_deliverable_work(deliverable):
            continue
        task = state.tasks.get(deliverable.task_id)
        if task is None:
            continue
        project = state.projects.get(task.project_id)
        if project is not None:
            team_ids.add(project.team_id)
            continue
        owner = state.agents.get(task.owner_agent_id)
        if owner is not None and owner.team_id is not None:
            team_ids.add(owner.team_id)
    return team_ids


def _manager_fallback_task_visible_for_work(
    state: TeamState,
    task: AgentTask,
    project_ids: set[str],
) -> bool:
    return task.project_id in project_ids and (
        task.state in _NON_TERMINAL_TASK_STATES or _task_has_active_deliverable_work(state, task.id)
    )


def _manager_fallback_summary_payload(
    state: TeamState,
    *,
    team: Team,
    manager: PersistentAgent,
    now: datetime,
) -> dict[str, Any]:
    project_ids = {project.id for project in state.projects.values() if project.team_id == team.id}
    team_agents = sorted(
        (
            agent
            for agent in state.agents.values()
            if agent.team_id == team.id and agent.status != AgentStatus.ARCHIVED
        ),
        key=lambda item: item.id,
    )
    team_agent_ids = {agent.id for agent in team_agents}
    visible_tasks = sorted(
        (
            task
            for task in state.tasks.values()
            if _manager_fallback_task_visible_for_work(state, task, project_ids)
        ),
        key=lambda item: (item.priority, item.created_at, item.id),
    )
    visible_task_ids = {task.id for task in visible_tasks}
    visible_deliverables = sorted(
        (
            deliverable
            for deliverable in state.deliverables.values()
            if deliverable.task_id in visible_task_ids
            and deliverable.superseded_by_deliverable_id is None
        ),
        key=lambda item: (item.created_at, item.id),
    )
    visible_deliverable_ids = {deliverable.id for deliverable in visible_deliverables}
    visible_pending_inbox = sorted(
        (
            item
            for item in state.inbox.values()
            if item.recipient_agent_id in team_agent_ids
            and (item.sender_agent_id is None or item.sender_agent_id in team_agent_ids)
            and (item.task_id is None or item.task_id in visible_task_ids)
            and item.state == TeamInboxItemState.PENDING
        ),
        key=lambda item: (item.created_at, item.id),
    )
    visible_action_records = sorted(
        (
            record
            for record in state.action_records.values()
            if record.agent_id in team_agent_ids
            and not record.success
            and record.dismissed_at is None
        ),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    review_profiles = sorted(
        (
            profile
            for profile in state.review_profiles.values()
            if profile.team_id == team.id
            and not profile.archived
            and (profile.project_id is None or profile.project_id in project_ids)
        ),
        key=lambda item: item.id,
    )
    visible_reviews = sorted(
        (
            review
            for review in state.review_requests.values()
            if review.deliverable_id in visible_deliverable_ids
        ),
        key=lambda item: (item.created_at or now, item.id),
    )
    visible_pending_reviews = [
        review
        for review in visible_reviews
        if review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
    ]
    reviewer_candidates = _manager_fallback_reviewer_candidates(
        state,
        team_agents=team_agents,
        project_ids=project_ids,
        review_profiles=review_profiles,
        now=now,
    )
    open_work_rows = [
        _manager_fallback_task_row(
            state,
            task=task,
            visible_reviews=visible_reviews,
        )
        for task in visible_tasks
    ]
    deliverable_rows = [
        _manager_fallback_deliverable_row(
            deliverable=deliverable,
            visible_reviews=visible_reviews,
        )
        for deliverable in visible_deliverables
    ]
    review_rows = [_manager_fallback_review_row(review) for review in visible_reviews]
    inbox_rows = [_manager_fallback_inbox_row(item) for item in visible_pending_inbox]
    failed_action_rows = [
        _manager_fallback_failed_action_row(record) for record in visible_action_records
    ]
    routing_gaps = _manager_fallback_routing_gaps(
        state,
        reviewer_candidates=reviewer_candidates,
        review_profiles=review_profiles,
        visible_tasks=visible_tasks,
        visible_deliverables=visible_deliverables,
        visible_reviews=visible_reviews,
        visible_pending_inbox=visible_pending_inbox,
    )
    limited_open_work, omitted_open_work = _manager_fallback_limited_rows(
        open_work_rows,
        _MANAGER_FALLBACK_OPEN_WORK_LIMIT,
    )
    limited_deliverables, omitted_deliverables = _manager_fallback_limited_rows(
        deliverable_rows,
        _MANAGER_FALLBACK_OPEN_WORK_LIMIT,
    )
    limited_reviews, omitted_reviews = _manager_fallback_limited_rows(
        review_rows,
        _MANAGER_FALLBACK_OPEN_WORK_LIMIT,
    )
    limited_inbox, omitted_inbox = _manager_fallback_limited_rows(
        inbox_rows,
        _MANAGER_FALLBACK_PENDING_INBOX_LIMIT,
    )
    limited_failed_actions, omitted_failed_actions = _manager_fallback_limited_rows(
        failed_action_rows,
        _MANAGER_FALLBACK_FAILED_ACTION_LIMIT,
    )
    limited_routing_gaps, omitted_routing_gaps = _manager_fallback_limited_rows(
        routing_gaps,
        _MANAGER_FALLBACK_ROUTING_GAP_LIMIT,
    )
    omitted_counts = _manager_fallback_omitted_counts(
        {
            "open_work": omitted_open_work,
            "deliverables": omitted_deliverables,
            "review_requests": omitted_reviews,
            "pending_inbox": omitted_inbox,
            "latest_failed_actions": omitted_failed_actions,
            "routing_gaps": omitted_routing_gaps,
        }
    )
    return {
        "reason": "manager_fallback",
        "generated_at": _manager_fallback_time(now),
        "team": {
            "id": team.id,
            "name": team.name,
            "manager_agent_id": manager.id,
        },
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "lead_agent_id": project.lead_agent_id,
                "status": project.status.value,
            }
            for project in sorted(state.projects.values(), key=lambda item: item.id)
            if project.id in project_ids
        ],
        "summary_counts": {
            "active_agent_count": len(team_agents),
            "open_task_count": len(visible_tasks),
            "active_deliverable_count": len(visible_deliverables),
            "review_request_count": len(visible_reviews),
            "pending_review_request_count": len(visible_pending_reviews),
            "pending_inbox_count": len(visible_pending_inbox),
            "failed_action_count": len(visible_action_records),
            "routing_gap_count": len(routing_gaps),
        },
        "agents": [
            _manager_fallback_agent_row(
                state,
                agent=agent,
                now=now,
                visible_tasks=visible_tasks,
                visible_pending_reviews=visible_pending_reviews,
                visible_pending_inbox=visible_pending_inbox,
                visible_failed_actions=visible_action_records,
            )
            for agent in team_agents
        ],
        "reviewer_candidate_agent_ids": [agent.id for agent in reviewer_candidates],
        "open_work": limited_open_work,
        "deliverables": limited_deliverables,
        "review_requests": limited_reviews,
        "pending_inbox": limited_inbox,
        "latest_failed_actions": limited_failed_actions,
        "routing_gaps": limited_routing_gaps,
        "omitted_counts": omitted_counts,
    }


def _manager_fallback_time(value: datetime | None) -> str | None:
    return _ensure_utc(value).isoformat() if value is not None else None


def _manager_fallback_short_text(
    value: object,
    *,
    limit: int = _MANAGER_FALLBACK_TEXT_LIMIT,
) -> str:
    text = _optional_text(value) or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _manager_fallback_limited_rows(
    rows: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    return rows[:limit], max(0, len(rows) - limit)


def _manager_fallback_omitted_counts(
    counts: Mapping[str, int],
) -> dict[str, int]:
    return {key: value for key, value in counts.items() if value > 0}


def _manager_fallback_review_state_counts(
    reviews: list[ReviewRequest],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for review in reviews:
        counts[review.state.value] = counts.get(review.state.value, 0) + 1
    return dict(sorted(counts.items()))


def _manager_fallback_task_row(
    state: TeamState,
    *,
    task: AgentTask,
    visible_reviews: list[ReviewRequest],
) -> dict[str, Any]:
    task_payload = task_payload_with_derived(state, task)
    deliverables = _active_task_deliverables(state, task)
    deliverable_ids = [deliverable.id for deliverable in deliverables]
    reviews = [review for review in visible_reviews if review.deliverable_id in deliverable_ids]
    return {
        "id": task.id,
        "title": _manager_fallback_short_text(task.title, limit=160),
        "state": task.state.value,
        "owner_agent_id": task.owner_agent_id,
        "project_id": task.project_id,
        "priority": task.priority,
        "dependency_status": task_payload.get("dependency_status"),
        "blocked_by_task_ids": task_payload.get("blocked_by_task_ids") or [],
        "deliverable_ids": deliverable_ids,
        "review_request_ids": [review.id for review in reviews],
        "reviewer_agent_ids": sorted({review.reviewer_agent_id for review in reviews}),
        "review_state_counts": _manager_fallback_review_state_counts(reviews),
    }


def _manager_fallback_deliverable_row(
    *,
    deliverable: Deliverable,
    visible_reviews: list[ReviewRequest],
) -> dict[str, Any]:
    reviews = [review for review in visible_reviews if review.deliverable_id == deliverable.id]
    pending_reviews = [
        review for review in reviews if review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
    ]
    row: dict[str, Any] = {
        "id": deliverable.id,
        "task_id": deliverable.task_id,
        "producer_agent_id": deliverable.producer_agent_id,
        "state": deliverable.state.value,
        "summary": _manager_fallback_short_text(deliverable.summary),
        "review_request_ids": [review.id for review in reviews],
        "reviewer_agent_ids": sorted({review.reviewer_agent_id for review in reviews}),
        "review_state_counts": _manager_fallback_review_state_counts(reviews),
    }
    if pending_reviews or _is_active_deliverable_work(deliverable):
        row["artifact_refs"] = list(deliverable.artifact_refs)
        row["git_refs"] = _manager_fallback_git_refs_row(deliverable)
    return row


def _manager_fallback_git_refs_row(deliverable: Deliverable) -> dict[str, Any]:
    refs = deliverable.git_refs
    row: dict[str, Any] = {}
    for field_name in (
        "agent_branch",
        "review_branch",
        "feature_branch",
        "release_branch",
        "commit_sha",
    ):
        value = getattr(refs, field_name)
        if value:
            row[field_name] = value
    if refs.promotion_record_ids:
        row["promotion_record_ids"] = list(refs.promotion_record_ids)
    return row


def _manager_fallback_review_row(review: ReviewRequest) -> dict[str, Any]:
    criteria_counts: dict[str, int] = {}
    for criterion in review.criteria:
        criteria_counts[criterion.state] = criteria_counts.get(criterion.state, 0) + 1
    return {
        "id": review.id,
        "deliverable_id": review.deliverable_id,
        "reviewer_agent_id": review.reviewer_agent_id,
        "state": review.state.value,
        "review_profile_id": review.review_profile_id,
        "instructions": _manager_fallback_short_text(review.instructions),
        "finding_count": len(review.findings),
        "latest_finding": _manager_fallback_short_text(
            review.findings[-1] if review.findings else None
        ),
        "criteria_state_counts": dict(sorted(criteria_counts.items())),
    }


def _manager_fallback_inbox_row(item: AgentInboxItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "recipient_agent_id": item.recipient_agent_id,
        "sender_agent_id": item.sender_agent_id,
        "task_id": item.task_id,
        "item_type": item.item_type.value,
        "response_requested": item.response_requested,
        "priority": item.priority.value,
        "created_at": _manager_fallback_time(item.created_at),
        "summary": _manager_fallback_inbox_summary(item),
    }


def _manager_fallback_inbox_summary(item: AgentInboxItem) -> str:
    for key in (
        "summary",
        "message",
        "body",
        "question",
        "instructions",
        "note",
        "reason",
    ):
        summary = _manager_fallback_short_text(item.payload.get(key))
        if summary:
            return summary
    return ""


def _manager_fallback_failed_action_row(record: AgentActionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "agent_id": record.agent_id,
        "dispatch_id": record.dispatch_id,
        "created_at": _manager_fallback_time(record.created_at),
        "error": _manager_fallback_short_text(record.error),
    }


def _manager_fallback_agent_row(
    state: TeamState,
    *,
    agent: PersistentAgent,
    now: datetime,
    visible_tasks: list[AgentTask],
    visible_pending_reviews: list[ReviewRequest],
    visible_pending_inbox: list[AgentInboxItem],
    visible_failed_actions: list[AgentActionRecord],
) -> dict[str, Any]:
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    runtime = state.agent_runtimes.get(agent.id)
    active_tasks = [task for task in visible_tasks if task.owner_agent_id == agent.id]
    pending_reviews = [
        review for review in visible_pending_reviews if review.reviewer_agent_id == agent.id
    ]
    pending_inbox = [item for item in visible_pending_inbox if item.recipient_agent_id == agent.id]
    failed_actions = [record for record in visible_failed_actions if record.agent_id == agent.id]
    stand_downs = _active_stand_downs_for_agent(state, agent.id)
    leased_until = runtime.wake_leased_until if runtime is not None else None
    return {
        "id": agent.id,
        "display_name": agent.display_name,
        "status": agent.status.value,
        "role_id": agent.role_id,
        "role_name": role.name if role is not None else None,
        "project_ids": agent_project_ids(state, agent.id),
        "active_task_count": len(active_tasks),
        "active_task_ids": [task.id for task in active_tasks],
        "pending_review_request_count": len(pending_reviews),
        "pending_review_request_ids": [review.id for review in pending_reviews],
        "pending_inbox_count": len(pending_inbox),
        "response_requested_inbox_count": sum(
            1 for item in pending_inbox if item.response_requested
        ),
        "failed_action_count": len(failed_actions),
        "latest_failed_action_id": failed_actions[0].id if failed_actions else None,
        "active_dispatch_id": (runtime.active_dispatch_id if runtime is not None else None),
        "wake_reason": runtime.wake_reason if runtime is not None else None,
        "wake_leased": (leased_until is not None and _ensure_utc(leased_until) > _ensure_utc(now)),
        "wake_leased_until": _manager_fallback_time(leased_until),
        "next_wake_at": (
            _manager_fallback_time(runtime.next_wake_at) if runtime is not None else None
        ),
        "active_stand_down_count": len(stand_downs),
    }


def _manager_fallback_reviewer_candidates(
    state: TeamState,
    *,
    team_agents: list[PersistentAgent],
    project_ids: set[str],
    review_profiles: list[ReviewProfile],
    now: datetime,
) -> list[PersistentAgent]:
    candidates: list[PersistentAgent] = []
    for agent in team_agents:
        if not _manager_fallback_reviewer_is_wakeable_for_routing(
            state,
            agent=agent,
            now=now,
        ):
            continue
        eligible_project_ids = _manager_fallback_reviewer_eligible_project_ids(
            state,
            agent=agent,
            project_ids=project_ids,
            review_profiles=review_profiles,
        )
        if eligible_project_ids:
            candidates.append(agent)
    return candidates


def _manager_fallback_reviewer_is_wakeable_for_routing(
    state: TeamState,
    *,
    agent: PersistentAgent,
    now: datetime,
) -> bool:
    if agent.status != AgentStatus.ACTIVE:
        return False
    if _active_stand_downs_for_agent(state, agent.id):
        return False
    runtime = state.agent_runtimes.get(agent.id)
    return not (
        runtime is not None
        and runtime.wake_leased_until is not None
        and _ensure_utc(runtime.wake_leased_until) > _ensure_utc(now)
    )


def _manager_fallback_reviewer_eligible_project_ids(
    state: TeamState,
    *,
    agent: PersistentAgent,
    project_ids: set[str],
    review_profiles: list[ReviewProfile],
) -> set[str]:
    if agent.role_id is None:
        return set()
    profile_project_ids: set[str] = set()
    for profile in review_profiles:
        if profile.applicable_role_ids and agent.role_id not in profile.applicable_role_ids:
            continue
        if profile.project_id is None:
            profile_project_ids.update(project_ids)
        elif profile.project_id in project_ids:
            profile_project_ids.add(profile.project_id)
    if not profile_project_ids:
        return set()
    return (
        _manager_fallback_candidate_project_ids(
            state,
            agent=agent,
            project_ids=project_ids,
        )
        & profile_project_ids
    )


def _manager_fallback_candidate_project_ids(
    state: TeamState,
    *,
    agent: PersistentAgent,
    project_ids: set[str],
) -> set[str]:
    candidate_project_ids = set(agent_project_ids(state, agent.id)) & project_ids
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None and role.project_id in project_ids:
        candidate_project_ids.add(role.project_id)
    return candidate_project_ids


def _manager_fallback_routing_gaps(
    state: TeamState,
    *,
    reviewer_candidates: list[PersistentAgent],
    review_profiles: list[ReviewProfile],
    visible_tasks: list[AgentTask],
    visible_deliverables: list[Deliverable],
    visible_reviews: list[ReviewRequest],
    visible_pending_inbox: list[AgentInboxItem],
) -> list[dict[str, Any]]:
    if not reviewer_candidates:
        return []
    tasks_by_id = {task.id: task for task in visible_tasks}
    reviews_by_deliverable: dict[str, list[ReviewRequest]] = {}
    for review in visible_reviews:
        reviews_by_deliverable.setdefault(review.deliverable_id, []).append(review)
    review_work = [
        deliverable
        for deliverable in visible_deliverables
        if _manager_fallback_deliverable_needs_review(
            deliverable,
            task=tasks_by_id.get(deliverable.task_id),
        )
    ]
    if not review_work:
        return []
    gaps: list[dict[str, Any]] = []
    visible_project_ids = {task.project_id for task in visible_tasks}
    for reviewer in reviewer_candidates:
        reviewer_project_ids = _manager_fallback_reviewer_eligible_project_ids(
            state,
            agent=reviewer,
            project_ids=visible_project_ids,
            review_profiles=review_profiles,
        )
        if not reviewer_project_ids:
            continue
        candidate_work = [
            deliverable
            for deliverable in review_work
            if (task := tasks_by_id.get(deliverable.task_id)) is not None
            and task.project_id in reviewer_project_ids
            and not any(
                review.reviewer_agent_id == reviewer.id
                for review in reviews_by_deliverable.get(deliverable.id, [])
            )
        ]
        if not candidate_work:
            continue
        reviewer_active_tasks = [
            task for task in visible_tasks if task.owner_agent_id == reviewer.id
        ]
        reviewer_pending_reviews = [
            review
            for review in visible_reviews
            if review.reviewer_agent_id == reviewer.id
            and review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
        ]
        reviewer_pending_inbox = [
            item for item in visible_pending_inbox if item.recipient_agent_id == reviewer.id
        ]
        if reviewer_active_tasks or reviewer_pending_reviews or reviewer_pending_inbox:
            continue
        display_name = reviewer.display_name or reviewer.id
        deliverable_ids = [deliverable.id for deliverable in candidate_work[:5]]
        task_ids = sorted({deliverable.task_id for deliverable in candidate_work[:5]})
        existing_reviewers = sorted(
            {
                review.reviewer_agent_id
                for deliverable in candidate_work
                for review in reviews_by_deliverable.get(deliverable.id, [])
                if review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
            }
        )
        gaps.append(
            {
                "kind": "reviewer_without_assigned_review_work",
                "severity": "warning",
                "agent_id": reviewer.id,
                "agent_display_name": reviewer.display_name,
                "summary": (
                    f"{display_name} has no pending review requests, active "
                    "tasks, or pending inbox items, but review work exists in "
                    "their project."
                ),
                "suggested_action": (
                    "Create review_requests for this reviewer for the listed "
                    "deliverables, or assign a review-focused task."
                ),
                "project_ids": sorted(reviewer_project_ids),
                "task_ids": task_ids,
                "deliverable_ids": deliverable_ids,
                "existing_reviewer_agent_ids": existing_reviewers,
            }
        )
    return gaps


def _manager_fallback_review_decision_guard_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    review: ReviewRequest,
) -> str | None:
    deliverable = state.deliverables.get(review.deliverable_id)
    if deliverable is None:
        return None
    task = state.tasks.get(deliverable.task_id)
    if task is None:
        return None
    team_id = actor.team_id
    if team_id is None:
        return None
    now = utc_now()
    project_ids = {project.id for project in state.projects.values() if project.team_id == team_id}
    team_agents = sorted(
        (
            agent
            for agent in state.agents.values()
            if agent.team_id == team_id and agent.status != AgentStatus.ARCHIVED
        ),
        key=lambda item: item.id,
    )
    team_agent_ids = {agent.id for agent in team_agents}
    visible_tasks = sorted(
        (
            item
            for item in state.tasks.values()
            if _manager_fallback_task_visible_for_work(state, item, project_ids)
        ),
        key=lambda item: (item.priority, item.created_at, item.id),
    )
    visible_task_ids = {item.id for item in visible_tasks}
    visible_deliverables = sorted(
        (
            item
            for item in state.deliverables.values()
            if item.task_id in visible_task_ids and item.superseded_by_deliverable_id is None
        ),
        key=lambda item: (item.created_at, item.id),
    )
    visible_deliverable_ids = {item.id for item in visible_deliverables}
    review_profiles = sorted(
        (
            profile
            for profile in state.review_profiles.values()
            if profile.team_id == team_id
            and not profile.archived
            and (profile.project_id is None or profile.project_id in project_ids)
        ),
        key=lambda item: item.id,
    )
    reviewer_candidates = _manager_fallback_reviewer_candidates(
        state,
        team_agents=team_agents,
        project_ids=project_ids,
        review_profiles=review_profiles,
        now=now,
    )
    visible_reviews = [
        item
        for item in state.review_requests.values()
        if item.deliverable_id in visible_deliverable_ids
    ]
    visible_pending_inbox = sorted(
        (
            item
            for item in state.inbox.values()
            if item.recipient_agent_id in team_agent_ids
            and (item.sender_agent_id is None or item.sender_agent_id in team_agent_ids)
            and (item.task_id is None or item.task_id in visible_task_ids)
            and item.state == TeamInboxItemState.PENDING
        ),
        key=lambda item: (item.created_at, item.id),
    )
    routing_gaps = _manager_fallback_routing_gaps(
        state,
        reviewer_candidates=reviewer_candidates,
        review_profiles=review_profiles,
        visible_tasks=visible_tasks,
        visible_deliverables=visible_deliverables,
        visible_reviews=visible_reviews,
        visible_pending_inbox=visible_pending_inbox,
    )
    relevant_gaps = [
        gap
        for gap in routing_gaps
        if gap.get("kind") == "reviewer_without_assigned_review_work"
        and gap.get("agent_id") != actor.id
        and deliverable.id in (gap.get("deliverable_ids") or [])
    ]
    if not relevant_gaps:
        return None
    gap = relevant_gaps[0]
    reviewer_id = str(gap.get("agent_id") or "")
    reviewer_name = _optional_text(gap.get("agent_display_name")) or reviewer_id
    return (
        f"manager fallback review decision for {review.id} should route deliverable "
        f"{deliverable.id} to idle profile-eligible reviewer {reviewer_name} "
        f"({reviewer_id}) instead of deciding it; create a fresh review_requests "
        "item for that reviewer, or include override_routing_gap_reason to accept "
        "as manager"
    )


def _manager_fallback_deliverable_needs_review(
    deliverable: Deliverable,
    *,
    task: AgentTask | None,
) -> bool:
    if task is not None and task.state == TeamTaskState.WAITING_FOR_REVIEW:
        return True
    return deliverable.state in {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }


def _governance_group_payload(state: TeamState, group: GovernanceGroup) -> dict[str, Any]:
    payload = group.model_dump(mode="json")
    manager = state.agents.get(group.manager_agent_id)
    payload["manager"] = _agent_payload(state, manager) if manager is not None else None
    return payload


def _record_governance_membership_change_agenda(
    state: TeamState,
    *,
    group: GovernanceGroup,
    previous_member_team_ids: list[str],
    added_team_ids: list[str],
    removed_team_ids: list[str],
    now: datetime,
) -> None:
    if not added_team_ids and not removed_team_ids:
        return
    if group.archived:
        return

    payload = {
        "change_type": "member_teams_changed",
        "added_team_ids": added_team_ids,
        "removed_team_ids": removed_team_ids,
        "previous_member_team_ids": previous_member_team_ids,
        "current_member_team_ids": list(group.member_team_ids),
    }
    summary_parts: list[str] = []
    if added_team_ids:
        summary_parts.append(
            "Added member teams: "
            + ", ".join(_team_reference_label(state, team_id) for team_id in added_team_ids)
            + "."
        )
    if removed_team_ids:
        summary_parts.append(
            "Removed member teams: "
            + ", ".join(_team_reference_label(state, team_id) for team_id in removed_team_ids)
            + "."
        )
    summary_parts.append(
        "Current member teams: "
        + (
            ", ".join(_team_reference_label(state, team_id) for team_id in group.member_team_ids)
            if group.member_team_ids
            else "none"
        )
        + "."
    )
    agenda_id = _generated_id(
        prefix="governance-agenda",
        labels=[group.id, "member-teams-changed"],
        existing_ids=state.governance_agenda,
    )
    state.governance_agenda[agenda_id] = GovernanceAgendaItem(
        id=agenda_id,
        governance_group_id=group.id,
        item_type=GovernanceAgendaItemType.POLICY_DECISION,
        title="Governance member teams changed",
        summary=" ".join(summary_parts),
        payload=payload,
        priority=TeamInboxPriority.NORMAL,
        created_at=now,
        updated_at=now,
    )
    runtime = state.governance_runtimes.get(group.id)
    if runtime is not None:
        runtime.next_wake_at = None
        runtime.updated_at = now


def _team_reference_label(state: TeamState, team_id: str) -> str:
    team = state.teams.get(team_id)
    if team is None:
        return team_id
    return f"{team.name} ({team.id})"


def _governance_member_team_payload(
    state: TeamState,
    team_id: str,
    *,
    cost_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    team = state.teams[team_id]
    projects = [
        project.model_dump(mode="json")
        for project in sorted(state.projects.values(), key=lambda item: item.id)
        if project.team_id == team_id
    ]
    manager = state.agents.get(team.manager_agent_id)
    team_costs = _mapping_or_empty((cost_payload or {}).get("team_costs"))
    return {
        "team": team.model_dump(mode="json"),
        "manager": _agent_payload(state, manager) if manager is not None else None,
        "projects": projects,
        "cost": dict(team_costs.get(team_id) or {}),
    }


def _pending_governance_agenda_for_group(
    state: TeamState,
    governance_group_id: str,
) -> list[GovernanceAgendaItem]:
    items = [
        item
        for item in state.governance_agenda.values()
        if item.governance_group_id == governance_group_id
        and item.state == GovernanceAgendaItemState.PENDING
    ]
    items.sort(
        key=lambda item: (
            _priority_sort_value(item.priority),
            item.created_at,
            item.id,
        )
    )
    return items


def _governance_groups_for_team(
    state: TeamState,
    team_id: str,
) -> list[GovernanceGroup]:
    return sorted(
        [
            group
            for group in state.governance_groups.values()
            if not group.archived and team_id in group.member_team_ids
        ],
        key=lambda group: group.id,
    )


def _governance_agenda_effective_team_id(
    state: TeamState,
    item: GovernanceAgendaItem,
) -> str | None:
    if item.team_id is not None:
        return item.team_id
    if item.project_id is not None:
        project = state.projects.get(item.project_id)
        if project is not None:
            return project.team_id
    if item.agent_id is not None:
        agent = state.agents.get(item.agent_id)
        if agent is not None:
            return agent.team_id
    return None


def _governance_cost_signals(
    state: TeamState,
    *,
    group: GovernanceGroup,
    cost_payload: Mapping[str, Any] | None,
    warning_ratio: Decimal,
) -> list[dict[str, Any]]:
    team_costs = _mapping_or_empty((cost_payload or {}).get("team_costs"))
    signals: list[dict[str, Any]] = []
    for team_id in group.member_team_ids:
        team = state.teams.get(team_id)
        cost = team_costs.get(team_id)
        if team is None or not isinstance(cost, Mapping):
            continue
        max_cost = _decimal_or_none(cost.get("max_cost_usd"))
        if max_cost is None:
            max_cost = _decimal_or_none(team.max_cost_usd)
        if max_cost is None or max_cost <= 0:
            continue
        estimated = _decimal_or_zero(cost.get("estimated_cost"))
        remaining = max(max_cost - estimated, Decimal("0"))
        usage_ratio = estimated / max_cost
        if bool(cost.get("cost_limit_reached")) or estimated >= max_cost:
            signal_type = GovernanceAgendaItemType.COST_CAP_REACHED.value
        elif usage_ratio >= warning_ratio:
            signal_type = GovernanceAgendaItemType.COST_CAP_WARNING.value
        else:
            continue
        signals.append(
            {
                "type": signal_type,
                "team_id": team_id,
                "team_name": team.name,
                "estimated_cost": float(estimated),
                "max_cost_usd": float(max_cost),
                "remaining_cost_usd": float(remaining),
                "usage_ratio": float(usage_ratio),
                "coverage_status": cost.get("coverage_status"),
                "coverage_reasons": list(cost.get("coverage_reasons") or []),
            }
        )
    signals.sort(
        key=lambda item: (
            item["type"] != GovernanceAgendaItemType.COST_CAP_REACHED.value,
            item["team_id"],
        )
    )
    return signals


def _governance_cost_signal_fingerprints(signals: list[dict[str, Any]]) -> list[str]:
    return sorted(
        f"{_optional_text(signal.get('team_id')) or ''}:{_optional_text(signal.get('type')) or ''}"
        for signal in signals
    )


def _governance_blocked_team_signals(
    state: TeamState,
    *,
    group: GovernanceGroup,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for team_id in group.member_team_ids:
        team = state.teams.get(team_id)
        if team is None:
            continue
        blocked_projects = [
            project.id
            for project in state.projects.values()
            if project.team_id == team_id and project.status == ProjectStatus.BLOCKED
        ]
        blocked_tasks = [
            task.id
            for task in state.tasks.values()
            if task.state == TeamTaskState.BLOCKED
            and (project := state.projects.get(task.project_id)) is not None
            and project.team_id == team_id
        ]
        if not blocked_projects and not blocked_tasks:
            continue
        signals.append(
            {
                "type": GovernanceAgendaItemType.BLOCKED_TEAM.value,
                "team_id": team_id,
                "team_name": team.name,
                "blocked_project_ids": sorted(blocked_projects),
                "blocked_task_ids": sorted(blocked_tasks),
            }
        )
    signals.sort(key=lambda item: item["team_id"])
    return signals


def _governance_blocked_signal_fingerprints(signals: list[dict[str, Any]]) -> list[str]:
    fingerprints: list[str] = []
    for signal in signals:
        blocked_projects = ",".join(
            sorted(str(project_id) for project_id in signal.get("blocked_project_ids") or [])
        )
        blocked_tasks = ",".join(
            sorted(str(task_id) for task_id in signal.get("blocked_task_ids") or [])
        )
        fingerprints.append(
            f"{_optional_text(signal.get('team_id')) or ''}:"
            f"projects={blocked_projects}:tasks={blocked_tasks}"
        )
    return sorted(fingerprints)


def _mapping_or_empty(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _decimal_or_none(value: object) -> Decimal | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _decimal_or_zero(value: object) -> Decimal:
    return _decimal_or_none(value) or Decimal("0")


def _priority_sort_value(priority: TeamInboxPriority) -> int:
    order = {
        TeamInboxPriority.HIGH: 0,
        TeamInboxPriority.NORMAL: 1,
        TeamInboxPriority.LOW: 2,
    }
    return order.get(priority, 1)


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _required_mapping(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if field_name not in payload:
        raise ValueError(f"{field_name} is required")
    return _mapping(payload[field_name], field_name)


def _materialize_role_inputs(
    template: TeamTemplate,
    inputs: Mapping[str, Any],
    team_input: Mapping[str, Any],
    project_input: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    nested_agents: Mapping[str, Any] | None = None
    if "roles" in inputs:
        nested_agents = _mapping(inputs["roles"], "roles")
    elif "agents" in inputs:
        nested_agents = _mapping(inputs["agents"], "agents")

    role_inputs: dict[str, dict[str, Any]] = {}
    for role in template.roles:
        raw_role_input: object | None = None
        if role.key in inputs and role.key not in {"team", "project", "roles", "agents"}:
            raw_role_input = inputs[role.key]
        elif nested_agents is not None and role.key in nested_agents:
            raw_role_input = nested_agents[role.key]
        elif role.key == "manager" and (
            "manager_agent_id" in team_input or "manager_session_id" in team_input
        ):
            raw_role_input = {
                "agent_id": team_input.get("manager_agent_id"),
                "session_id": team_input.get("manager_session_id"),
            }
        elif role.key == "project_lead" and (
            "lead_agent_id" in project_input or "lead_session_id" in project_input
        ):
            raw_role_input = {
                "agent_id": project_input.get("lead_agent_id"),
                "session_id": project_input.get("lead_session_id"),
            }
        if raw_role_input is None:
            raise ValueError(f"{role.key} is required")
        role_inputs[role.key] = _mapping(raw_role_input, role.key)
        role_input = role_inputs[role.key]
        role_input["agent_id"] = _optional_text(
            role_input.get("agent_id", role_input.get("id")),
        )
        role_input["session_id"] = _required_text(
            role_input.get("session_id"),
            f"{role.key}.session_id",
        )
    return role_inputs


def _project_git_config(value: object) -> ProjectGitConfig | None:
    if value is None:
        return None
    git_payload = _mapping(value, "project.git")
    host_repo_path = _optional_text(git_payload.get("host_repo_path"))
    if host_repo_path is None:
        return None
    return ProjectGitConfig.model_validate({**git_payload, "host_repo_path": host_repo_path})


def _materialized_template_record_id(project_id: str, template_record_id: str) -> str:
    return f"{project_id}-{template_record_id}"


def _ordered_template_roles(template: TeamTemplate) -> list[Any]:
    ordered: list[Any] = []
    pending = list(template.roles)
    while pending:
        progressed = False
        for role in list(pending):
            if role.reports_to_role_key is None or role.reports_to_role_key in {
                item.key for item in ordered
            }:
                ordered.append(role)
                pending.remove(role)
                progressed = True
        if not progressed:
            unresolved = ", ".join(sorted(role.key for role in pending))
            raise ValueError(f"template {template.id} has unresolved role ordering: {unresolved}")
    if ordered[0].key != template.root_role_key:
        raise ValueError(
            f"template {template.id} must start role ordering with {template.root_role_key}"
        )
    return ordered


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _normalize_operator_user_id(
    value: object | None,
    *,
    created_by: object | None = None,
) -> str:
    text = _optional_text(value)
    if text is None:
        created_by_text = _optional_text(created_by)
        if created_by_text and created_by_text.startswith("operator:"):
            text = created_by_text.removeprefix("operator:")
        elif created_by_text:
            text = created_by_text
    if text is None:
        text = "admin"
    return _required_text(text, "operator_user_id").casefold()


def _operator_display_name_for_user(
    state: TeamState,
    operator_user_id: str,
) -> str | None:
    normalized_operator_user_id = _normalize_operator_user_id(operator_user_id)
    for conversation in sorted(
        state.conversations.values(),
        key=lambda item: (item.last_interaction_at, item.id),
        reverse=True,
    ):
        if conversation.operator_user_id == normalized_operator_user_id:
            return conversation.operator_display_name or conversation.operator_user_id
    return None


def _operator_chat_participant_payloads(
    state: TeamState,
    *,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    participants: dict[str, dict[str, Any]] = {}
    for conversation in state.conversations.values():
        if agent_id is not None and conversation.agent_id != agent_id:
            continue
        participant = participants.setdefault(
            conversation.operator_user_id,
            {
                "operator_user_id": conversation.operator_user_id,
                "operator_display_name": conversation.operator_display_name,
                "conversation_ids": [],
            },
        )
        participant["conversation_ids"].append(conversation.id)
        if not participant.get("operator_display_name") and conversation.operator_display_name:
            participant["operator_display_name"] = conversation.operator_display_name
    return sorted(
        participants.values(),
        key=lambda item: str(
            item.get("operator_display_name") or item.get("operator_user_id") or ""
        ),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _chat_preview(value: object, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}..."


def _publish_admin_chat_notification(
    *,
    runtime_dir: Path,
    state: TeamState,
    message_id: str,
) -> None:
    message = state.conversation_messages.get(message_id)
    if message is None or message.sender != "agent":
        return
    conversation = state.conversations.get(message.conversation_id)
    if conversation is None:
        return
    agent = state.agents.get(conversation.agent_id)
    agent_label = (
        _optional_text(agent.display_name if agent is not None else None) or conversation.agent_id
    )
    try:
        AdminUserNotificationStore(runtime_dir).push(
            user_id=conversation.operator_user_id,
            title="New Team chat message",
            body=f"{agent_label}: {_chat_preview(message.body, limit=140)}",
            source="team-chat",
            source_id=message.id,
            target_url="#addon:team-addon",
            metadata={
                "conversation_id": conversation.id,
                "message_id": message.id,
                "agent_id": conversation.agent_id,
            },
        )
    except Exception:
        return


def _publish_admin_chat_notifications(
    *,
    runtime_dir: Path,
    state: TeamState,
    message_ids: Sequence[str],
) -> None:
    for message_id in message_ids:
        _publish_admin_chat_notification(
            runtime_dir=runtime_dir,
            state=state,
            message_id=message_id,
        )


def _touch_conversation(
    conversation: TeamConversation,
    *,
    now: datetime,
    preview: str,
) -> None:
    conversation.last_message_preview = _chat_preview(preview)
    conversation.last_interaction_at = now
    conversation.updated_at = now


def _optional_cost_limit(value: object) -> Decimal | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("max_cost_usd must be a non-negative decimal") from None
    if amount < 0:
        raise ValueError("max_cost_usd must be a non-negative decimal")
    return amount


def _optional_bool(value: object, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalized_progress_classification(activity: TeamDispatchActivity) -> str:
    return (_optional_text(activity.progress_classification) or "none").casefold()


def _dispatch_activity_counts_as_progress(activity: TeamDispatchActivity) -> bool:
    if activity.status != TeamDispatchActivityStatus.SUCCESS:
        return False
    return _normalized_progress_classification(activity) not in _DISPATCH_NON_PROGRESS_CLASSES


def _dispatch_activity_counts_for_context_rotation(
    activity: TeamDispatchActivity,
) -> bool:
    if activity.status != TeamDispatchActivityStatus.SUCCESS:
        return False
    progress_classification = _normalized_progress_classification(activity)
    return progress_classification not in _DISPATCH_CONTEXT_SUPPRESSED_CLASSES


def _active_tasks_for_agent(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal = {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}
    tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id and task.state not in terminal
    ]
    tasks.sort(key=lambda item: (item.priority, item.created_at, item.id))
    return tasks


def _case_payloads_for_agent(state: TeamState, agent_id: str) -> list[dict[str, Any]]:
    active_task_ids = {task.id for task in _active_tasks_for_agent(state, agent_id)}
    cases = [
        case
        for case in state.cases.values()
        if case.status != TeamCaseStatus.CLOSED
        and (
            case.owner_agent_id == agent_id
            or any(task_id in active_task_ids for task_id in case.task_ids)
        )
    ]
    cases.sort(key=lambda item: (_priority_sort_value(item.priority), item.updated_at, item.id))
    return [case.model_dump(mode="json") for case in cases]


def _case_payloads_for_team(state: TeamState, team_id: str) -> list[dict[str, Any]]:
    cases = [
        case
        for case in state.cases.values()
        if case.team_id == team_id and case.status != TeamCaseStatus.CLOSED
    ]
    cases.sort(key=lambda item: (_priority_sort_value(item.priority), item.updated_at, item.id))
    return [case.model_dump(mode="json") for case in cases]


def _visible_project_ids_for_agent(state: TeamState, agent: PersistentAgent) -> set[str]:
    project_ids = set(agent_project_ids(state, agent.id))
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    if role is not None and role.project_id is not None:
        project_ids.add(role.project_id)
    if agent.team_id is not None:
        team = state.teams.get(agent.team_id)
        for project in state.projects.values():
            if project.team_id != agent.team_id:
                continue
            if project.lead_agent_id == agent.id or (
                team is not None and team.manager_agent_id == agent.id
            ):
                project_ids.add(project.id)
    return project_ids


def _release_package_payloads_for_agent(
    state: TeamState,
    agent: PersistentAgent,
) -> dict[str, dict[str, Any]]:
    visible_project_ids = _visible_project_ids_for_agent(state, agent)
    releases = [
        release
        for release in state.release_packages.values()
        if release.project_id in visible_project_ids
    ]
    releases.sort(key=lambda release: (release.created_at, release.id))
    return {
        release.id: release_package_payload_with_derived(state, release) for release in releases
    }


def _runtime_attention_payloads_for_agent(
    state: TeamState,
    agent: PersistentAgent,
) -> list[dict[str, Any]]:
    visible_project_ids = _visible_project_ids_for_agent(state, agent)
    visible_release_ids = {
        release.id
        for release in state.release_packages.values()
        if release.project_id in visible_project_ids
    }
    runtime_attention = runtime_operations_payload(state).get("attention")
    if not isinstance(runtime_attention, list):
        return []
    rows = [
        row
        for row in runtime_attention
        if isinstance(row, dict)
        and (
            row.get("agent_id") == agent.id
            or row.get("project_id") in visible_project_ids
            or row.get("release_package_id") in visible_release_ids
        )
    ]
    return rows


def _wakeable_inbox_items(items: list[AgentInboxItem]) -> list[AgentInboxItem]:
    return [item for item in items if _inbox_item_can_wake(item)]


def _inbox_item_can_wake(item: AgentInboxItem) -> bool:
    return item.response_requested or item.priority != TeamInboxPriority.LOW


def _task_can_wake_owner_for_work(state: TeamState, task: AgentTask) -> bool:
    return not _task_waits_on_active_deliverable_review(state, task)


def _task_waits_on_active_deliverable_review(state: TeamState, task: AgentTask) -> bool:
    if task.state != TeamTaskState.WAITING_FOR_REVIEW:
        return False
    review_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
        TeamRecordState.ACCEPTED,
    }
    return any(
        deliverable.state in review_states for deliverable in _active_task_deliverables(state, task)
    )


def _active_stand_downs_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[AgentStandDown]:
    root_ids: list[str] = []
    seen: set[str] = set()
    current_id: str | None = agent_id
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        root_ids.append(current_id)
        current = state.agents.get(current_id)
        current_id = current.reports_to_agent_id if current is not None else None
    root_set = set(root_ids)
    return sorted(
        [
            stand_down
            for stand_down in state.stand_downs.values()
            if stand_down.active and stand_down.agent_id in root_set
        ],
        key=lambda item: (item.set_at, item.id),
    )


def _stand_down_error_detail(stand_downs: list[AgentStandDown]) -> str:
    if not stand_downs:
        return "active stand-down"
    primary = stand_downs[-1]
    return f"{primary.source.value} stand-down on {primary.agent_id}: {primary.reason}"


def _agent_subtree_ids(state: TeamState, agent_id: str) -> list[str]:
    if agent_id not in state.agents:
        return []
    ordered: list[str] = []
    stack = [agent_id]
    seen: set[str] = set()
    while stack:
        current_id = stack.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        ordered.append(current_id)
        agent = state.agents.get(current_id)
        if agent is None:
            continue
        stack.extend(reversed(agent.subordinate_agent_ids))
    return ordered


def _maybe_mark_idle_context_rotation_due(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> None:
    agent = state.agents.get(agent_id)
    if agent is None:
        return
    if _active_stand_downs_for_agent(state, agent.id):
        return
    runtime = state.agent_runtimes.setdefault(agent.id, AgentRuntime(agent_id=agent.id))
    if (
        runtime.context_clearing_policy != AgentContextClearingPolicy.IDLE
        or runtime.context_rotation_pending
    ):
        return
    if not _has_activity_since_last_context_rotation(state, agent=agent, runtime=runtime):
        return
    if not _is_agent_truly_idle(state, agent=agent, runtime=runtime, now=now):
        return
    _mark_context_rotation_pending(
        state,
        agent=agent,
        runtime=runtime,
        reason="idle",
        requested_by=None,
        requested_at=now,
        request_note=None,
    )


def _has_activity_since_last_context_rotation(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
) -> bool:
    rotated_at = runtime.last_context_rotated_at
    if rotated_at is None:
        return True
    rotated_at = _ensure_utc(rotated_at)
    if runtime.last_progress_at is not None and _ensure_utc(runtime.last_progress_at) > rotated_at:
        return True

    def _newer_than_rotation(value: datetime | None) -> bool:
        return value is not None and _ensure_utc(value) > rotated_at

    for task in state.tasks.values():
        if task.owner_agent_id != agent.id:
            continue
        if _newer_than_rotation(task.created_at) or _newer_than_rotation(task.updated_at):
            return True
    for item in state.inbox.values():
        if item.recipient_agent_id != agent.id:
            continue
        if _newer_than_rotation(item.created_at) or _newer_than_rotation(item.updated_at):
            return True
    for review in state.review_requests.values():
        if review.reviewer_agent_id != agent.id:
            continue
        if _newer_than_rotation(review.created_at) or _newer_than_rotation(review.decision_at):
            return True
    for assignment in state.ephemeral_assignments.values():
        if assignment.parent_agent_id != agent.id:
            continue
        if _newer_than_rotation(assignment.created_at) or _newer_than_rotation(
            assignment.updated_at
        ):
            return True
    return False


def _is_agent_truly_idle(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    now: datetime,
) -> bool:
    if agent.status != AgentStatus.ACTIVE:
        return False
    if _active_stand_downs_for_agent(state, agent.id):
        return False
    if runtime.active_dispatch_id:
        return False
    if runtime.wake_leased_until is not None and _ensure_utc(runtime.wake_leased_until) > now:
        return False
    if _pending_inbox_for_agent(state, agent.id):
        return False
    if _active_tasks_for_agent(state, agent.id):
        return False
    if _pending_reviews_for_agent(state, agent.id):
        return False
    if _pending_ephemeral_acceptances_for_agent(state, agent.id):
        return False
    return True


def _mark_context_rotation_pending(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    reason: Literal["idle", "manual", "every_wake"],
    requested_by: str | None,
    requested_at: datetime,
    request_note: str | None,
) -> None:
    if reason not in {"idle", "manual", "every_wake"}:
        raise ValueError("context rotation reason must be idle, manual, or every_wake")
    runtime.context_rotation_pending = True
    runtime.context_rotation_reason = reason
    runtime.context_rotation_requested_by = requested_by
    runtime.context_rotation_requested_at = requested_at
    runtime.context_rotation_request_note = request_note
    runtime.next_wake_at = requested_at
    runtime.last_handoff_summary = _build_context_handoff_summary(
        state,
        agent=agent,
        now=requested_at,
        reason=reason,
        request_note=request_note,
    )
    runtime.updated_at = requested_at


def _resolved_context_clearing_policy(
    *,
    context_clearing_policy: AgentContextClearingPolicy | str | None,
    set_context_clearing_policy: bool,
    auto_context_clearing_enabled: bool | None,
    set_auto_context_clearing_enabled: bool,
) -> AgentContextClearingPolicy | None:
    if set_context_clearing_policy:
        return _context_clearing_policy_from_value(context_clearing_policy)
    if set_auto_context_clearing_enabled:
        return (
            AgentContextClearingPolicy.IDLE
            if bool(auto_context_clearing_enabled)
            else AgentContextClearingPolicy.OFF
        )
    return None


def _context_clearing_policy_from_value(
    value: AgentContextClearingPolicy | str | None,
) -> AgentContextClearingPolicy:
    if value is None:
        return AgentContextClearingPolicy.OFF
    if isinstance(value, AgentContextClearingPolicy):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return AgentContextClearingPolicy.OFF
    try:
        return AgentContextClearingPolicy(normalized)
    except ValueError:
        allowed = ", ".join(policy.value for policy in AgentContextClearingPolicy)
        raise ValueError(f"context_clearing_policy must be one of: {allowed}") from None


def _set_context_clearing_policy(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    policy: AgentContextClearingPolicy,
    now: datetime,
) -> None:
    previous_policy = runtime.context_clearing_policy
    if previous_policy != policy:
        _clear_pending_auto_context_rotation(runtime)
    runtime.context_clearing_policy = policy
    runtime.auto_context_clearing_enabled = policy != AgentContextClearingPolicy.OFF
    runtime.updated_at = now
    if policy == AgentContextClearingPolicy.IDLE:
        _maybe_mark_idle_context_rotation_due(state, agent_id=agent.id, now=now)


def _clear_pending_auto_context_rotation(runtime: AgentRuntime) -> None:
    if not runtime.context_rotation_pending or runtime.context_rotation_reason not in {
        "idle",
        "every_wake",
    }:
        return
    runtime.context_rotation_pending = False
    runtime.context_rotation_reason = None
    runtime.context_rotation_requested_by = None
    runtime.context_rotation_requested_at = None
    runtime.context_rotation_request_note = None
    runtime.next_wake_at = None
    runtime.last_handoff_summary = None


def _build_context_handoff_summary(
    state: TeamState,
    *,
    agent: PersistentAgent,
    now: datetime,
    reason: Literal["idle", "manual"],
    request_note: str | None,
) -> str:
    display = agent.display_name or agent.id
    assigned_project_ids = agent_project_ids(state, agent.id)
    project_label = ", ".join(assigned_project_ids) or "none"
    superior_label = agent.reports_to_agent_id or "none"
    subordinate_label = ", ".join(agent.subordinate_agent_ids) or "none"
    role = state.roles.get(agent.role_id)
    role_label = role.name if role is not None else agent.role_id
    lines = [
        f"Context rotation handoff for {display} ({agent.id}).",
        f"Generated at {now.isoformat()} for {reason} context clearing.",
        (
            f"Role: {role_label} ({agent.role_id}); team: {agent.team_id}; project: {project_label}; "
            f"session: {agent.session_id}; reports_to: {superior_label}; "
            f"subordinates: {subordinate_label}."
        ),
    ]
    if request_note:
        lines.append(f"Operator note: {request_note}.")

    active_tasks = _active_tasks_for_agent(state, agent.id)
    if active_tasks:
        lines.append("Active tasks:")
        for task in active_tasks[:8]:
            lines.append(f"- {task.id}: {task.state.value}, priority {task.priority}, {task.title}")
    else:
        lines.append("Active tasks: none.")

    pending_inbox = _pending_inbox_for_agent(state, agent.id)
    if pending_inbox:
        lines.append("Pending inbox:")
        for item in pending_inbox[:8]:
            task_suffix = f", task {item.task_id}" if item.task_id else ""
            sender_suffix = f", from {item.sender_agent_id}" if item.sender_agent_id else ""
            lines.append(f"- {item.id}: {item.item_type.value}{task_suffix}{sender_suffix}")
    else:
        lines.append("Pending inbox: none.")

    pending_reviews = _pending_reviews_for_agent(state, agent.id)
    if pending_reviews:
        lines.append("Pending reviews:")
        for review in pending_reviews[:8]:
            lines.append(
                f"- {review.id}: {review.state.value}, deliverable {review.deliverable_id}"
            )
    else:
        lines.append("Pending reviews: none.")

    pending_ephemeral = _pending_ephemeral_acceptances_for_agent(state, agent.id)
    if pending_ephemeral:
        lines.append("Pending ephemeral acceptances:")
        for assignment in pending_ephemeral[:8]:
            lines.append(
                f"- {assignment.id}: task {assignment.task_id}, session {assignment.session_id}"
            )
    else:
        lines.append("Pending ephemeral acceptances: none.")

    recent_deliverables = sorted(
        [
            deliverable
            for deliverable in state.deliverables.values()
            if deliverable.producer_agent_id == agent.id
        ],
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    if recent_deliverables:
        lines.append("Recent deliverables:")
        for deliverable in recent_deliverables[:5]:
            lines.append(
                f"- {deliverable.id}: {deliverable.state.value}, task {deliverable.task_id}, "
                f"{deliverable.summary}"
            )
    else:
        lines.append("Recent deliverables: none.")

    recent_actions = sorted(
        [record for record in state.action_records.values() if record.agent_id == agent.id],
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    if recent_actions:
        lines.append("Recent action records:")
        for record in recent_actions[:5]:
            status = "success" if record.success else f"failed: {record.error or 'unknown'}"
            lines.append(f"- {record.id}: {status}, dispatch {record.dispatch_id}")
    else:
        lines.append("Recent action records: none.")

    return "\n".join(lines)


def _pending_reviews_for_agent(state: TeamState, agent_id: str) -> list[ReviewRequest]:
    pending_states = {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
    reviews = [
        review
        for review in state.review_requests.values()
        if review.reviewer_agent_id == agent_id and review.state in pending_states
    ]
    reviews.sort(key=lambda item: (item.created_at or datetime.min.replace(tzinfo=UTC), item.id))
    return reviews


def _pending_ephemeral_acceptances_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[EphemeralAssignment]:
    assignments = [
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.parent_agent_id == agent_id
        and assignment.state == EphemeralAssignmentState.COMPLETED
    ]
    assignments.sort(key=lambda item: (item.updated_at, item.id))
    return assignments


def _mark_agent_work_due(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> None:
    runtime = state.agent_runtimes.setdefault(agent_id, AgentRuntime(agent_id=agent_id))
    _clear_completed_chat_dispatch_lease(state, runtime, now=now)
    runtime.no_progress_count = 0
    runtime.next_wake_at = None
    _clear_low_priority_batch(runtime)
    runtime.updated_at = now


def _append_action_item_error(
    result: dict[str, Any],
    field_name: str,
    index: int,
    error: ValueError,
) -> None:
    result["errors"].append(f"{field_name}[{index}]: {error}")


def _response_requested_from_item(
    item: Mapping[str, Any],
    *,
    default: bool,
) -> bool:
    if "response_requested" not in item:
        return default
    value = item.get("response_requested")
    if isinstance(value, bool):
        return value
    raise ValueError("response_requested must be a boolean")


def _priority_from_item(
    item: Mapping[str, Any],
    *,
    default: TeamInboxPriority,
) -> TeamInboxPriority:
    if "priority" not in item:
        return default
    try:
        return TeamInboxPriority(str(item.get("priority") or "").strip())
    except ValueError:
        allowed = ", ".join(priority.value for priority in TeamInboxPriority)
        raise ValueError(f"priority must be one of: {allowed}") from None


def _clear_low_priority_batch(runtime: AgentRuntime) -> None:
    runtime.low_priority_batch_started_at = None
    runtime.low_priority_batch_cycle_count = 0
    runtime.low_priority_batch_due_after_cycles = None


def _advance_low_priority_batch(
    runtime: AgentRuntime,
    *,
    now: datetime,
    due_after_cycles: int,
) -> bool:
    if runtime.low_priority_batch_started_at is None:
        runtime.low_priority_batch_started_at = now
        runtime.low_priority_batch_cycle_count = 0
    runtime.low_priority_batch_cycle_count += 1
    runtime.low_priority_batch_due_after_cycles = due_after_cycles
    return runtime.low_priority_batch_cycle_count >= due_after_cycles


def _pending_inbox_for_agent(state: TeamState, agent_id: str) -> list[AgentInboxItem]:
    items = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
    ]
    items.sort(key=lambda item: (item.created_at, item.id))
    return items


def _append_agent_wake_requested_event_for_inbox_item(
    state: TeamState,
    *,
    item: AgentInboxItem,
    result: dict[str, Any],
    now: datetime,
) -> None:
    if not _inbox_item_can_wake(item) or item.priority == TeamInboxPriority.LOW:
        return
    _append_agent_wake_requested_event(
        state,
        agent_id=item.recipient_agent_id,
        result=result,
        reason="inbox",
        now=now,
        delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    )


def _append_follow_up_agent_wake_requested_event(
    state: TeamState,
    *,
    agent_id: str,
    result: dict[str, Any],
    now: datetime,
    completed_dispatch_id: str,
    progress_made: bool,
    max_no_progress_count: int = DEFAULT_MAX_NO_PROGRESS_COUNT,
) -> None:
    wake = _agent_immediate_wake_request(state, agent_id=agent_id, now=now)
    if wake is None:
        return
    reason, delay_seconds = wake
    if not progress_made and reason != "inbox":
        return
    if (
        reason == "task_ready"
        and max_no_progress_count >= 0
        and state.agent_runtimes.get(agent_id, AgentRuntime(agent_id=agent_id)).no_progress_count
        >= max_no_progress_count
    ):
        return
    _append_agent_wake_requested_event(
        state,
        agent_id=agent_id,
        result=result,
        reason=reason,
        now=now,
        delay_seconds=delay_seconds,
        idempotency_suffix=(
            f"after-dispatch:{completed_dispatch_id}" if delay_seconds <= 0 else None
        ),
    )


def _agent_immediate_wake_request(
    state: TeamState,
    *,
    agent_id: str,
    now: datetime,
) -> tuple[str, float] | None:
    agent = state.agents.get(agent_id)
    if agent is None or agent.status != AgentStatus.ACTIVE or agent.team_id is None:
        return None
    if _active_stand_downs_for_agent(state, agent.id):
        return None
    runtime = state.agent_runtimes.get(agent.id)
    if runtime is None:
        runtime = AgentRuntime(agent_id=agent.id)
    _clear_completed_chat_dispatch_lease(state, runtime, now=now)
    if runtime.wake_leased_until is not None and _ensure_utc(
        runtime.wake_leased_until
    ) > _ensure_utc(now):
        return None

    pending_inbox = _pending_inbox_for_agent(state, agent.id)
    wake_inbox = _wakeable_inbox_items(pending_inbox)
    immediate_inbox = [item for item in wake_inbox if item.priority != TeamInboxPriority.LOW]
    if immediate_inbox:
        oldest = min(_ensure_utc(item.created_at) for item in immediate_inbox)
        age_seconds = max(0.0, (_ensure_utc(now) - oldest).total_seconds())
        return (
            "inbox",
            max(0.0, DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS - age_seconds),
        )
    if runtime.context_rotation_pending:
        return "context_rotation", DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    if _latest_action_feedback_for_agent(state, agent.id):
        return "action_feedback", DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    if _agent_task_work_ready(state, agent.id):
        return "task_ready", DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    return None


def _agent_task_work_ready(state: TeamState, agent_id: str) -> bool:
    wakeable_tasks = [
        task
        for task in _active_tasks_for_agent(state, agent_id)
        if _task_can_wake_owner_for_work(state, task)
    ]
    return any(state.dependency_status(task.id) == "satisfied" for task in wakeable_tasks) or any(
        state.subtask_counts(task.id)["open"] > 0 for task in wakeable_tasks
    )


def _append_agent_wake_requested_event(
    state: TeamState,
    *,
    agent_id: str,
    result: dict[str, Any],
    reason: str,
    now: datetime,
    delay_seconds: float,
    idempotency_suffix: str | None = None,
) -> None:
    agent = state.agents.get(agent_id)
    if agent is None or agent.status != AgentStatus.ACTIVE or agent.team_id is None:
        return
    normalized_now = _ensure_utc(now)
    normalized_delay = max(0.0, float(delay_seconds))
    bucket_seconds = max(1.0, DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS)
    bucket = int((normalized_now.timestamp() + normalized_delay) // bucket_seconds)
    suffix = f":{idempotency_suffix}" if idempotency_suffix else ""
    result.setdefault("events", []).append(
        {
            "event_type": AGENT_WAKE_REQUESTED_EVENT_TYPE,
            "correlation_key": f"{AGENT_WAKE_REQUESTED_EVENT_TYPE}:{agent.id}",
            "idempotency_key": (
                f"team-addon:{AGENT_WAKE_REQUESTED_EVENT_TYPE}:{agent.id}:{bucket}{suffix}"
            ),
            "delay_seconds": normalized_delay,
            "payload": {
                "team_id": agent.team_id,
                "agent_id": agent.id,
                "session_id": agent.session_id,
                "reason": reason,
            },
        }
    )


def _resolve_action_agent(
    state: TeamState,
    *,
    action: dict[str, Any],
    session_id: str | None,
) -> PersistentAgent:
    action_agent_id = _optional_text(action.get("agent_id"))
    if action_agent_id:
        return _require_present(state.agents, action_agent_id, "agent")
    if session_id:
        return _agent_for_session(state, session_id=session_id)
    raise ValueError("agent action requires agent_id or a known session_id")


def _agent_for_session(state: TeamState, *, session_id: str) -> PersistentAgent:
    for agent in state.agents.values():
        if agent.session_id == session_id:
            return agent
    raise ValueError(f"no team agent found for session_id {session_id}")


def _latest_action_feedback_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[dict[str, Any]]:
    records = [record for record in state.action_records.values() if record.agent_id == agent_id]
    if not records:
        return []
    latest_record = max(records, key=lambda record: (record.created_at, record.id))
    if latest_record.success:
        return []
    if latest_record.dismissed_at is not None:
        return []
    if latest_record.feedback_delivered_at is not None:
        return []
    latest_dispatch_id = latest_record.dispatch_id
    failed_records = [
        record
        for record in records
        if record.dispatch_id == latest_dispatch_id
        and not record.success
        and record.dismissed_at is None
    ]
    return [
        _action_feedback_payload(record)
        for record in sorted(
            failed_records,
            key=lambda record: (record.created_at, record.id),
        )
    ]


def _action_feedback_payload(record: AgentActionRecord) -> dict[str, Any]:
    error = record.error or "action failed validation"
    return {
        "action_record_id": record.id,
        "dispatch_id": record.dispatch_id,
        "created_at": record.created_at.isoformat(),
        "error": error,
        "action_fields": _non_empty_action_fields(record.action),
        "correction_hints": _correction_hints(error),
    }


def _non_empty_action_fields(action: Mapping[str, Any]) -> list[str]:
    ignored_fields = {"agent_id", "dispatch_id", "schema_version", "notes"}
    return sorted(
        key
        for key, value in action.items()
        if key not in ignored_fields and value not in (None, "", [], {})
    )


def _git_release_sequence_error(action: Mapping[str, Any]) -> str | None:
    feature_updates = action.get("git_feature_updates")
    release_requests = action.get("git_release_requests")
    if (
        isinstance(feature_updates, list)
        and feature_updates
        and isinstance(release_requests, list)
        and release_requests
    ):
        return (
            "cannot combine git_feature_updates and git_release_requests in one "
            "Team action; submit git_release_requests alone when the intent is to "
            "release accepted deliverables. The release request will enqueue "
            "prerequisite feature promotions from accepted deliverable review or "
            "agent branches before continuing."
        )
    return None


def _correction_hints(error: str) -> list[str]:
    normalized = error.casefold()
    hints: list[str] = []
    if "cannot combine git_feature_updates and git_release_requests" in normalized:
        hints.append(
            "Submit git_release_requests alone when the intent is a release; it can "
            "auto-enqueue prerequisite feature promotions for accepted deliverables "
            "with review or agent branches."
        )
    if "must not start with 'refs/'" in normalized:
        hints.append("Use bare branch names like agents/... or team/..., not refs/heads/....")
    if "cannot deliver task" in normalized:
        hints.append("Only the task owner can create deliverables for that task.")
    if "artifact_paths must contain at least one path" in normalized:
        hints.append(
            "For Git-only deliverables, omit artifact_paths; otherwise list staged "
            "paths under /artifacts."
        )
    if "supersedes_deliverable_id" in normalized:
        hints.append(
            "When revising a rejected deliverable, set supersedes_deliverable_id to "
            "the rejected deliverable id from the review_rejected inbox payload."
        )
    if (
        "has neither a feature branch nor artifact refs" in normalized
        or "has neither a feature branch, review branch, agent branch" in normalized
    ):
        hints.append(
            "Release requests can auto-promote accepted Git deliverables only when "
            "they have git_refs.review_branch or git_refs.agent_branch; otherwise "
            "publish a review branch or provide artifact refs first."
        )
    if "assignment_id must be a non-empty string" in normalized:
        hints.append(
            "Use the assignment_id shape for ephemeral promotions, or the "
            "deliverable_id shape for persistent deliverables."
        )
    if "already exists" in normalized:
        if "release package" in normalized:
            hints.append(
                "git_release_requests creates new release packages only. For an "
                "existing or blocked package, inspect release_packages[id], resolve "
                "its release_branch/error, and follow its next_action_hint. Only "
                "submit git_release_recoveries when the hint identifies a failed "
                "release promotion and the resolved commit has been pushed to an "
                "agents/... ref; do not reuse the same id in another "
                "git_release_requests item."
            )
        if "review request" in normalized:
            hints.append(
                "For review handoff, do not reuse the existing review request id; "
                "omit id or choose a new id for the intended reviewer."
            )
        hints.append("Do not recreate existing ids; reference existing records or choose a new id.")
    if not hints:
        hints.append(
            "Review the current Team context and retry only the missing corrected action items."
        )
    return hints


def _mark_action_failure(
    state: TeamState,
    *,
    runtime: AgentRuntime,
    agent_id: str,
    dispatch_id: str,
    action: dict[str, Any],
    error: str,
    action_id: str,
    result: dict[str, Any],
) -> None:
    runtime.active_dispatch_id = None
    runtime.wake_leased_until = None
    runtime.wake_reason = None
    runtime.no_progress_count += 1
    runtime.updated_at = utc_now()
    _maybe_mark_idle_context_rotation_due(state, agent_id=agent_id, now=runtime.updated_at)
    state.action_records[action_id] = AgentActionRecord(
        id=action_id,
        agent_id=agent_id,
        dispatch_id=dispatch_id,
        action=action,
        success=False,
        error=error,
    )
    result["errors"].append(error)
    agent = state.agents.get(agent_id)
    if agent is not None:
        _append_pending_chat_message_queued_event_for_agent(
            state,
            agent=agent,
            completed_dispatch_id=dispatch_id,
            result=result,
        )


def _append_pending_chat_message_queued_event_for_agent(
    state: TeamState,
    *,
    agent: PersistentAgent,
    completed_dispatch_id: str,
    result: dict[str, Any],
) -> None:
    pending_message: TeamConversationMessage | None = None
    pending_conversation: TeamConversation | None = None
    for message in sorted(
        state.conversation_messages.values(),
        key=lambda item: (item.created_at, item.id),
    ):
        if (
            message.sender != "operator"
            or message.message_type != "text"
            or message.status != "queued"
        ):
            continue
        conversation = state.conversations.get(message.conversation_id)
        if conversation is None or conversation.agent_id != agent.id:
            continue
        pending_message = message
        pending_conversation = conversation
        break
    if pending_message is None or pending_conversation is None:
        return
    dispatch_id = pending_message.dispatch_id or f"team-chat:{pending_message.id}"
    pending_message.dispatch_id = dispatch_id
    result["events"].append(
        {
            "event_type": CHAT_MESSAGE_QUEUED_EVENT_TYPE,
            "correlation_key": dispatch_id,
            "idempotency_key": (
                "team-addon:"
                f"{CHAT_MESSAGE_QUEUED_EVENT_TYPE}:agent-ready:"
                f"{completed_dispatch_id}:{pending_message.id}"
            ),
            "payload": {
                "team_id": agent.team_id,
                "agent_id": agent.id,
                "session_id": agent.session_id,
                "conversation_id": pending_conversation.id,
                "message_id": pending_message.id,
                "dispatch_id": dispatch_id,
            },
        }
    )


def _is_direct_subordinate(actor: PersistentAgent, agent_id: str) -> bool:
    return agent_id in actor.subordinate_agent_ids


def _is_team_manager(state: TeamState, actor: PersistentAgent) -> bool:
    if actor.reports_to_agent_id is not None:
        return False
    team = state.teams.get(actor.team_id)
    return team is not None and team.manager_agent_id == actor.id


def _agent_governs_team(state: TeamState, *, agent_id: str, team_id: str) -> bool:
    agent = state.agents.get(agent_id)
    if agent is None or agent.status != AgentStatus.ACTIVE:
        return False
    return any(
        not group.archived
        and group.manager_agent_id == agent_id
        and team_id in group.member_team_ids
        for group in state.governance_groups.values()
    )


def _is_project_lead_for(state: TeamState, agent_id: str, project_id: str) -> bool:
    return project_lead_agent_id(state, project_id) == agent_id


def _apply_chat_reply(
    state: TeamState,
    *,
    actor: PersistentAgent,
    reply: Mapping[str, Any],
    result: dict[str, Any],
) -> str:
    conversation_id = _required_text(reply.get("conversation_id"), "conversation_id")
    source_message_id = _required_text(reply.get("message_id"), "message_id")
    body = _required_text(reply.get("body"), "body")
    conversation = _require_present(state.conversations, conversation_id, "conversation")
    if conversation.agent_id != actor.id:
        raise ValueError(f"agent {actor.id} cannot reply to conversation {conversation_id}")
    source_message = _require_present(
        state.conversation_messages,
        source_message_id,
        "conversation message",
    )
    if source_message.conversation_id != conversation.id:
        raise ValueError(f"message {source_message_id} is not in conversation {conversation_id}")
    if source_message.sender != "operator":
        raise ValueError(f"message {source_message_id} is not an operator message")
    now = utc_now()
    source_message.status = "replied"
    source_message.error = None
    source_message.updated_at = now
    reply_id = f"chatmsg-{uuid4().hex}"
    metadata: dict[str, Any] = {}
    summary = _optional_text(reply.get("summary"))
    if summary is not None:
        metadata["summary"] = summary
    state.conversation_messages[reply_id] = TeamConversationMessage(
        id=reply_id,
        conversation_id=conversation.id,
        sender="agent",
        message_type="text",
        body=body,
        status="sent",
        source_message_id=source_message.id,
        metadata=metadata,
        created_at=now,
        updated_at=now,
    )
    _touch_conversation(conversation, now=now, preview=_chat_preview(body))
    result["replied_chat_message_ids"].append(source_message.id)
    return reply_id


def _apply_operator_chat_message(
    state: TeamState,
    *,
    actor: PersistentAgent,
    message: Mapping[str, Any],
) -> str:
    recipient_operator_user_id = _normalize_operator_user_id(
        message.get("recipient_operator_user_id")
    )
    known_display_name = _operator_display_name_for_user(
        state,
        recipient_operator_user_id,
    )
    known_operator_ids = {
        conversation.operator_user_id for conversation in state.conversations.values()
    }
    if recipient_operator_user_id not in known_operator_ids:
        raise ValueError(f"unknown operator {recipient_operator_user_id}")
    conversation = next(
        (
            item
            for item in state.conversations.values()
            if item.agent_id == actor.id and item.operator_user_id == recipient_operator_user_id
        ),
        None,
    )
    now = utc_now()
    if conversation is None:
        conversation_id = _generated_id(
            prefix="conversation",
            labels=[actor.id, recipient_operator_user_id],
            existing_ids=state.conversations,
        )
        conversation = TeamConversation(
            id=conversation_id,
            agent_id=actor.id,
            operator_user_id=recipient_operator_user_id,
            operator_display_name=known_display_name,
            created_by=f"agent:{actor.id}",
            last_message_preview="",
            created_at=now,
            updated_at=now,
            last_interaction_at=now,
        )
        state.conversations[conversation.id] = conversation
    body = _required_text(message.get("body"), "body")
    summary = _optional_text(message.get("summary"))
    message_id = f"chatmsg-{uuid4().hex}"
    state.conversation_messages[message_id] = TeamConversationMessage(
        id=message_id,
        conversation_id=conversation.id,
        sender="agent",
        message_type="text",
        body=body,
        status="sent",
        metadata={
            key: value
            for key, value in {
                "sender_kind": "team_agent",
                "recipient_operator_user_id": recipient_operator_user_id,
                "recipient_operator_display_name": known_display_name,
                "summary": summary,
            }.items()
            if value
        },
        created_at=now,
        updated_at=now,
    )
    _touch_conversation(conversation, now=now, preview=_chat_preview(body))
    return message_id


def _mark_chat_dispatch_message_failed(
    state: TeamState,
    *,
    dispatch_id: str | None,
    error: str,
) -> str | None:
    normalized_dispatch_id = _optional_text(dispatch_id)
    if normalized_dispatch_id is None or not normalized_dispatch_id.startswith("team-chat:"):
        return None
    source_message = next(
        (
            message
            for message in state.conversation_messages.values()
            if message.dispatch_id == normalized_dispatch_id
            and message.sender == "operator"
            and message.status != "replied"
        ),
        None,
    )
    if source_message is None:
        return None
    conversation = state.conversations.get(source_message.conversation_id)
    now = utc_now()
    source_message.status = "failed"
    source_message.error = error
    source_message.updated_at = now
    if conversation is not None:
        _touch_conversation(
            conversation,
            now=now,
            preview=f"Reply failed: {_chat_preview(error)}",
        )
    return source_message.id


def _mark_chat_dispatch_message_replied_from_activity(
    state: TeamState,
    *,
    dispatch_id: str,
) -> str | None:
    source_message = next(
        (
            message
            for message in state.conversation_messages.values()
            if message.dispatch_id == dispatch_id
            and message.sender == "operator"
            and message.status != "failed"
        ),
        None,
    )
    if source_message is None:
        return None
    if source_message.status != "replied":
        now = utc_now()
        source_message.status = "replied"
        source_message.error = None
        source_message.updated_at = now
        conversation = state.conversations.get(source_message.conversation_id)
        if conversation is not None:
            _touch_conversation(
                conversation,
                now=now,
                preview=f"Replied: {_chat_preview(source_message.body)}",
            )
    return source_message.id


def _clear_completed_chat_dispatch_lease(
    state: TeamState,
    runtime: AgentRuntime,
    *,
    now: datetime,
) -> bool:
    dispatch_id = _optional_text(runtime.active_dispatch_id)
    if dispatch_id is None or not dispatch_id.startswith("team-chat:"):
        return False
    if runtime.wake_leased_until is not None and _ensure_utc(
        runtime.wake_leased_until
    ) > _ensure_utc(now):
        return False
    message = next(
        (
            item
            for item in state.conversation_messages.values()
            if item.dispatch_id == dispatch_id
            and item.sender == "operator"
            and item.status in {"replied", "failed"}
        ),
        None,
    )
    if message is None:
        return False
    runtime.active_dispatch_id = None
    runtime.wake_leased_until = None
    runtime.wake_reason = None
    runtime.updated_at = now
    return True


def _chat_dispatch_finalization_error(
    failed_activities: Sequence[TeamDispatchActivity],
) -> str:
    reply_failure = next(
        (activity for activity in failed_activities if activity.tool_name == "team_reply_to_chat"),
        None,
    )
    if reply_failure is not None:
        return (
            reply_failure.error_message or reply_failure.error_code or "team_reply_to_chat failed"
        )
    return "Team chat dispatch completed without a successful team_reply_to_chat call"


def _agent_has_role_permission_for_project(
    state: TeamState,
    actor: PersistentAgent,
    permission: RolePermission,
    project_id: str,
) -> bool:
    project = state.projects.get(project_id)
    if project is None:
        return False
    if actor.team_id != project.team_id:
        return False
    if _is_team_manager(state, actor) and permission in effective_role_permissions(state, actor):
        return True
    return permission in effective_role_permissions(state, actor, project_id=project_id)


def _snapshot_case_task_state(
    state: TeamState,
) -> tuple[dict[str, TeamCase], dict[str, AgentTask]]:
    return (
        {case_id: case.model_copy(deep=True) for case_id, case in state.cases.items()},
        {task_id: task.model_copy(deep=True) for task_id, task in state.tasks.items()},
    )


def _restore_case_task_state(
    state: TeamState,
    snapshot: tuple[dict[str, TeamCase], dict[str, AgentTask]],
) -> None:
    state.cases, state.tasks = snapshot


def _apply_case_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: Mapping[str, Any],
) -> tuple[str, bool]:
    raw_case_id = _optional_text(update.get("case_id")) or _optional_text(update.get("id"))
    now = utc_now()
    if raw_case_id is not None and raw_case_id in state.cases:
        case = state.cases[raw_case_id]
        error = _case_edit_error(state, actor=actor, case=case)
        if error is not None:
            raise ValueError(error)
        next_project_id = case.project_id
        next_owner_agent_id = case.owner_agent_id
        if "project_id" in update:
            next_project_id = _optional_text(update.get("project_id"))
        if "owner_agent_id" in update:
            next_owner_agent_id = _optional_text(update.get("owner_agent_id"))
        _validate_case_scope(
            state,
            team_id=case.team_id,
            project_id=next_project_id,
            owner_agent_id=next_owner_agent_id,
        )
        if "title" in update:
            case.title = _required_text(update.get("title"), "title")
        if "description" in update:
            case.description = _optional_text(update.get("description")) or ""
        if "project_id" in update:
            case.project_id = next_project_id
        if "status" in update:
            previous_status = case.status
            case.status = TeamCaseStatus(_required_text(update.get("status"), "status"))
            if case.status == TeamCaseStatus.CLOSED and previous_status != TeamCaseStatus.CLOSED:
                case.closed_at = now
            elif case.status != TeamCaseStatus.CLOSED:
                case.closed_at = None
        if "priority" in update:
            case.priority = TeamInboxPriority(_required_text(update.get("priority"), "priority"))
        if "owner_agent_id" in update:
            case.owner_agent_id = next_owner_agent_id
        if "source" in update:
            case.source = _optional_text(update.get("source"))
        if "source_ref" in update:
            case.source_ref = _optional_text(update.get("source_ref"))
        if "tags" in update:
            case.tags = _unique_text_list(update.get("tags"), "tags")
        if "metadata" in update:
            case.metadata = _mapping(update.get("metadata") or {}, "metadata")
        if "task_ids" in update:
            task_ids = _unique_text_list(update.get("task_ids"), "task_ids")
            for task_id in list(case.task_ids):
                if task_id not in task_ids:
                    _unlink_task_from_case(state, task_id=task_id, case_id=case.id, now=now)
            for task_id in task_ids:
                _link_task_to_case(state, task_id=task_id, case_id=case.id, now=now)
        else:
            for task_id in list(case.task_ids):
                task = state.tasks.get(task_id)
                if task is not None and not _case_can_link_task(state, case=case, task=task):
                    _unlink_task_from_case(state, task_id=task_id, case_id=case.id, now=now)
        case.updated_at = now
        return case.id, False

    team_id = _optional_text(update.get("team_id")) or actor.team_id
    if team_id is None:
        raise ValueError("case team_id is required for teamless agents")
    if actor.team_id != team_id:
        raise ValueError(f"agent {actor.id} cannot create a case for team {team_id}")
    project_id = _optional_text(update.get("project_id"))
    if project_id is not None and not (
        _is_team_manager(state, actor) or agent_has_project_assignment(state, actor.id, project_id)
    ):
        raise ValueError(f"agent {actor.id} cannot create a case for project {project_id}")
    owner_agent_id = _optional_text(update.get("owner_agent_id"))
    _validate_case_scope(
        state,
        team_id=team_id,
        project_id=project_id,
        owner_agent_id=owner_agent_id,
    )
    title = _required_text(update.get("title"), "title")
    case_id = raw_case_id or _generated_id(
        prefix="case",
        labels=[project_id or team_id, title],
        existing_ids=state.cases,
    )
    _require_absent(state.cases, case_id, "case")
    status = TeamCaseStatus(_optional_text(update.get("status")) or TeamCaseStatus.OPEN.value)
    case = TeamCase(
        id=case_id,
        team_id=team_id,
        project_id=project_id,
        title=title,
        description=_optional_text(update.get("description")) or "",
        status=status,
        priority=TeamInboxPriority(
            _optional_text(update.get("priority")) or TeamInboxPriority.NORMAL.value
        ),
        owner_agent_id=owner_agent_id,
        source=_optional_text(update.get("source")),
        source_ref=_optional_text(update.get("source_ref")),
        tags=_unique_text_list(update.get("tags") or [], "tags"),
        metadata=_mapping(update.get("metadata") or {}, "metadata"),
        task_ids=[],
        created_at=now,
        updated_at=now,
        closed_at=now if status == TeamCaseStatus.CLOSED else None,
    )
    state.cases[case.id] = case
    for task_id in _unique_text_list(update.get("task_ids") or [], "task_ids"):
        _link_task_to_case(state, task_id=task_id, case_id=case.id, now=now)
    return case.id, True


def _case_edit_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    case: TeamCase,
) -> str | None:
    if actor.team_id != case.team_id:
        return f"agent {actor.id} cannot update case {case.id}"
    if case.owner_agent_id == actor.id:
        return None
    if _is_team_manager(state, actor):
        return None
    if case.project_id is not None and _is_project_lead_for(state, actor.id, case.project_id):
        return None
    if case.owner_agent_id is not None and _supervises(state, actor.id, case.owner_agent_id):
        return None
    return f"agent {actor.id} cannot update case {case.id}"


def _task_creation_mandate_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    project_id: str,
    parent_task_id: str | None,
) -> str | None:
    can_create_root = _agent_has_role_permission_for_project(
        state,
        actor,
        RolePermission.CREATE_ROOT_TASK,
        project_id,
    )
    if parent_task_id is None:
        if can_create_root:
            return None
        return (
            f"agent {actor.id} cannot create a top-level task without "
            f"{RolePermission.CREATE_ROOT_TASK.value} permission"
        )
    parent = state.tasks.get(parent_task_id)
    if parent is None:
        return None
    if parent.state not in _NON_TERMINAL_TASK_STATES:
        return f"task parent {parent_task_id} is not active"
    if parent.project_id != project_id:
        return None
    if can_create_root:
        return None
    if parent.owner_agent_id == actor.id:
        return None
    return (
        f"agent {actor.id} can only create child tasks under its own active tasks "
        f"without {RolePermission.CREATE_ROOT_TASK.value} permission"
    )


def _agent_in_project_hierarchy(state: TeamState, agent_id: str, project_id: str) -> bool:
    agent = state.agents.get(agent_id)
    return agent is not None and agent_has_project_assignment(state, agent_id, project_id)


def _upward_recipient_error(
    agent: PersistentAgent,
    recipient_id: str | None,
    kind: str,
) -> str | None:
    if agent.reports_to_agent_id is None:
        return (
            f"agent {agent.id} is a top-level manager and must use operator_escalations "
            f"instead of {kind}"
        )
    if recipient_id != agent.reports_to_agent_id:
        return (
            f"agent {agent.id} can only send {kind} to its direct superior "
            f"{agent.reports_to_agent_id}"
        )
    return None


def _plan_task_creation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    creation: dict[str, Any],
    result: dict[str, Any],
    reserved_task_ids: set[str],
) -> _TaskCreationPlan | None:
    owner_id = _required_text(creation.get("owner_agent_id"), "owner_agent_id")
    project_id = _required_text(creation.get("project_id"), "project_id")
    owner = state.agents.get(owner_id)
    if owner is None:
        result["errors"].append(f"task creation references missing owner {owner_id}")
        return None
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"task creation references missing project {project_id}")
        return None
    if owner.team_id != actor.team_id or project.team_id != actor.team_id:
        result["errors"].append(f"agent {actor.id} cannot create tasks outside its team")
        return None
    actor_can_target_project = _is_project_lead_for(
        state, actor.id, project_id
    ) or _is_team_manager(state, actor)
    if (
        not agent_has_project_assignment(state, owner.id, project_id)
        and not actor_can_target_project
    ):
        result["errors"].append(
            f"agent {actor.id} cannot create a task for {owner_id} outside project {project_id}"
        )
        return None
    permitted = (
        owner.id == actor.id
        or _is_direct_subordinate(actor, owner.id)
        or (
            _is_project_lead_for(state, actor.id, project_id)
            and _agent_in_project_hierarchy(state, owner.id, project_id)
        )
        or (_is_team_manager(state, actor) and _is_project_lead_for(state, owner.id, project_id))
    )
    if not permitted:
        result["errors"].append(f"agent {actor.id} is not permitted to create tasks for {owner_id}")
        return None
    raw_state = _optional_text(creation.get("state"))
    if raw_state is not None:
        try:
            task_state = TeamTaskState(raw_state)
        except ValueError:
            result["errors"].append(f"task creation has invalid state {raw_state}")
            return None
        if task_state not in _NON_TERMINAL_TASK_STATES:
            result["errors"].append(f"task creation cannot start in terminal state {raw_state}")
            return None
    else:
        task_state = TeamTaskState.PENDING
    explicit_task_id = _optional_text(creation.get("task_id"))
    legacy_task_id = _optional_text(creation.get("id"))
    if (
        explicit_task_id is not None
        and legacy_task_id is not None
        and explicit_task_id != legacy_task_id
    ):
        result["errors"].append("task creation has conflicting task_id and id")
        return None
    task_id = explicit_task_id or legacy_task_id
    if task_id is None:
        task_id = _generated_id(
            prefix="task",
            labels=[project_id, creation.get("title")],
            existing_ids=set(state.tasks) | set(reserved_task_ids),
        )
    if task_id in state.tasks or task_id in reserved_task_ids:
        result["errors"].append(f"task {task_id} already exists")
        return None
    parent_task_id = _optional_text(creation.get("parent_task_id"))
    case_id = _optional_text(creation.get("case_id"))
    if case_id is not None:
        case = state.cases.get(case_id)
        if case is None:
            result["errors"].append(f"task creation references missing case {case_id}")
            return None
        if case.team_id != project.team_id:
            result["errors"].append(f"case {case_id} belongs to another team")
            return None
        if case.project_id is not None and case.project_id != project_id:
            result["errors"].append(f"case {case_id} belongs to project {case.project_id}")
            return None
    dependency_task_ids = _string_list(creation.get("dependency_task_ids"), "dependency_task_ids")
    mandate_error = _task_creation_mandate_error(
        state,
        actor=actor,
        project_id=project_id,
        parent_task_id=parent_task_id,
    )
    if mandate_error is not None:
        result["errors"].append(mandate_error)
        return None
    priority = creation.get("priority")
    return _TaskCreationPlan(
        task_id=task_id,
        owner_id=owner_id,
        project_id=project_id,
        case_id=case_id,
        task_state=task_state,
        parent_task_id=parent_task_id,
        dependency_task_ids=dependency_task_ids,
        priority=int(priority) if priority is not None else 0,
        title=_required_text(creation.get("title"), "title"),
        description=_required_text(creation.get("description"), "description"),
    )


def _apply_task_creation_plan(
    state: TeamState,
    *,
    plan: _TaskCreationPlan,
    actor: PersistentAgent,
) -> str:
    now = utc_now()
    state.tasks[plan.task_id] = AgentTask(
        id=plan.task_id,
        owner_agent_id=plan.owner_id,
        project_id=plan.project_id,
        case_id=plan.case_id,
        priority=plan.priority,
        state=plan.task_state,
        title=plan.title,
        description=plan.description,
        created_by_agent_id=actor.id,
        parent_task_id=plan.parent_task_id,
        dependency_task_ids=plan.dependency_task_ids,
        created_at=now,
        updated_at=now,
    )
    if plan.case_id is not None:
        _link_task_to_case(state, task_id=plan.task_id, case_id=plan.case_id, now=now)
    if plan.task_state in _NON_TERMINAL_TASK_STATES:
        _mark_agent_work_due(state, agent_id=plan.owner_id, now=now)
    return plan.task_id


def _task_owner_reassignment_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    task: AgentTask,
    new_owner_id: str,
) -> str | None:
    if task.state not in _NON_TERMINAL_TASK_STATES:
        return f"task {task.id} is terminal and cannot be reassigned"
    if not _supervises(state, actor.id, task.owner_agent_id):
        return f"agent {actor.id} is not permitted to reassign task {task.id}"
    new_owner = state.agents.get(new_owner_id)
    if new_owner is None:
        return f"task reassignment references missing owner {new_owner_id}"
    project = state.projects.get(task.project_id)
    if project is None:
        return f"task {task.id} references missing project {task.project_id}"
    if new_owner.team_id != actor.team_id or project.team_id != actor.team_id:
        return f"agent {actor.id} cannot reassign task {task.id} outside its team"
    if new_owner.id != actor.id and not _supervises(state, actor.id, new_owner.id):
        return f"agent {actor.id} cannot reassign task {task.id} to {new_owner.id}"
    if not agent_has_project_assignment(state, new_owner.id, task.project_id):
        return (
            f"agent {actor.id} cannot reassign task {task.id} to {new_owner.id} "
            f"outside project {task.project_id}"
        )
    return None


def _subordinate_task_finish_allowed(
    state: TeamState,
    *,
    actor: PersistentAgent,
    task: AgentTask,
    raw_state: object,
    new_owner_id: str | None,
) -> bool:
    try:
        next_state = TeamTaskState(str(raw_state))
    except ValueError:
        return False
    if next_state != TeamTaskState.FINISHED:
        return False
    if new_owner_id is not None and new_owner_id != actor.id:
        return False
    return (
        _task_owner_reassignment_error(
            state,
            actor=actor,
            task=task,
            new_owner_id=actor.id,
        )
        is None
    )


def _task_creation_batch_graph_errors(
    state: TeamState,
    plans: list[_TaskCreationPlan],
    *,
    actor: PersistentAgent,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    while True:
        valid_plans = [plan for plan in plans if plan.task_id not in errors]
        if not valid_plans:
            return errors
        task_projects = {task_id: task.project_id for task_id, task in state.tasks.items()}
        task_owners = {task_id: task.owner_agent_id for task_id, task in state.tasks.items()}
        task_states = {task_id: task.state for task_id, task in state.tasks.items()}
        parent_map = {task_id: task.parent_task_id for task_id, task in state.tasks.items()}
        dependency_map = {
            task_id: list(task.dependency_task_ids) for task_id, task in state.tasks.items()
        }
        for plan in valid_plans:
            task_projects[plan.task_id] = plan.project_id
            task_owners[plan.task_id] = plan.owner_id
            task_states[plan.task_id] = plan.task_state
            parent_map[plan.task_id] = plan.parent_task_id
            dependency_map[plan.task_id] = list(plan.dependency_task_ids)

        next_errors: dict[str, str] = {}
        for plan in valid_plans:
            graph_error = _task_creation_plan_graph_error(
                plan,
                task_projects=task_projects,
                parent_map=parent_map,
                dependency_map=dependency_map,
            )
            if graph_error is not None:
                next_errors[plan.task_id] = graph_error
                continue
            mandate_error = _task_creation_plan_batch_mandate_error(
                state,
                actor=actor,
                plan=plan,
                task_projects=task_projects,
                task_owners=task_owners,
                task_states=task_states,
            )
            if mandate_error is not None:
                next_errors[plan.task_id] = mandate_error
        if not next_errors:
            return errors
        for task_id, graph_error in next_errors.items():
            errors.setdefault(task_id, graph_error)


def _task_creation_plan_batch_mandate_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    plan: _TaskCreationPlan,
    task_projects: dict[str, str],
    task_owners: dict[str, str],
    task_states: dict[str, TeamTaskState],
) -> str | None:
    parent_task_id = plan.parent_task_id
    can_create_root = _agent_has_role_permission_for_project(
        state,
        actor,
        RolePermission.CREATE_ROOT_TASK,
        plan.project_id,
    )
    if parent_task_id is None:
        if can_create_root:
            return None
        return (
            f"agent {actor.id} cannot create a top-level task without "
            f"{RolePermission.CREATE_ROOT_TASK.value} permission"
        )
    parent_project_id = task_projects.get(parent_task_id)
    parent_owner_id = task_owners.get(parent_task_id)
    parent_state = task_states.get(parent_task_id)
    if parent_project_id is None or parent_owner_id is None or parent_state is None:
        return None
    if parent_state not in _NON_TERMINAL_TASK_STATES:
        return f"task parent {parent_task_id} is not active"
    if parent_project_id != plan.project_id:
        return None
    if can_create_root:
        return None
    if parent_owner_id == actor.id:
        return None
    return (
        f"agent {actor.id} can only create child tasks under its own active tasks "
        f"without {RolePermission.CREATE_ROOT_TASK.value} permission"
    )


def _task_creation_plan_graph_error(
    plan: _TaskCreationPlan,
    *,
    task_projects: dict[str, str],
    parent_map: dict[str, str | None],
    dependency_map: dict[str, list[str]],
) -> str | None:
    if plan.parent_task_id == plan.task_id:
        return "task cannot be its own parent"
    if plan.task_id in plan.dependency_task_ids:
        return "task cannot depend on itself"
    if plan.parent_task_id is not None:
        parent_project_id = task_projects.get(plan.parent_task_id)
        if parent_project_id is None:
            return f"task references missing parent {plan.parent_task_id}"
        if parent_project_id != plan.project_id:
            return f"task parent {plan.parent_task_id} belongs to project {parent_project_id}"
    for dependency_id in plan.dependency_task_ids:
        dependency_project_id = task_projects.get(dependency_id)
        if dependency_project_id is None:
            return f"task references missing dependency {dependency_id}"
        if dependency_project_id != plan.project_id:
            return f"task dependency {dependency_id} belongs to project {dependency_project_id}"
    current = plan.parent_task_id
    seen: set[str] = set()
    while current is not None:
        if current == plan.task_id:
            return f"task parent cycle includes {plan.task_id}"
        if current in seen:
            break
        seen.add(current)
        current = parent_map.get(current)
    for dependency_id in plan.dependency_task_ids:
        if _dependency_path_exists(
            dependency_map,
            start_task_id=dependency_id,
            target_task_id=plan.task_id,
        ):
            return f"task dependency cycle includes {plan.task_id}"
    return None


def _dependency_path_exists(
    dependency_map: dict[str, list[str]],
    *,
    start_task_id: str,
    target_task_id: str,
) -> bool:
    stack = [start_task_id]
    seen: set[str] = set()
    while stack:
        task_id = stack.pop()
        if task_id == target_task_id:
            return True
        if task_id in seen:
            continue
        seen.add(task_id)
        stack.extend(dependency_map.get(task_id, []))
    return False


def _apply_operator_escalation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    escalation: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    if not _is_team_manager(state, actor):
        result["errors"].append(f"agent {actor.id} is not permitted to send operator escalations")
        return False
    title = _required_text(escalation.get("title"), "title")
    message = _required_text(escalation.get("message"), "message")
    urgency = _optional_text(escalation.get("urgency")) or "normal"
    project_id = _optional_text(escalation.get("project_id"))
    task_id = _optional_text(escalation.get("task_id"))
    team = state.teams.get(actor.team_id)
    if team is None:
        result["errors"].append(f"team {actor.team_id} does not exist")
        return False
    if project_id is not None:
        project = _require_present(state.projects, project_id, "project")
        if project.team_id != team.id:
            raise ValueError(f"project {project.id} is not in team {team.id}")
    escalation_id = f"escalation-{uuid4().hex}"
    payload: dict[str, Any] = {
        "team_id": actor.team_id,
        "manager_agent_id": actor.id,
        "title": title,
        "message": message,
        "urgency": urgency,
    }
    if project_id is not None:
        payload["project_id"] = project_id
    if task_id is not None:
        payload["task_id"] = task_id
    if not team.escalate_to_governance:
        target = _optional_text(team.operator_communication_target_id)
        if not target:
            result["errors"].append(
                f"team {actor.team_id} has no operator_communication_target_id for escalations"
            )
            return False
        payload["communication_target_id"] = target
        inbox_id = f"inbox-{uuid4().hex}"
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=actor.id,
            sender_agent_id=actor.id,
            task_id=task_id,
            item_type=TeamInboxItemType.ESCALATION,
            response_requested=False,
            priority=TeamInboxPriority.NORMAL,
            payload={"escalation_id": escalation_id, **payload},
        )
        result["created_inbox_item_ids"].append(inbox_id)
        result["events"].append(
            {
                "event_type": OPERATOR_ESCALATION_EVENT_TYPE,
                "correlation_key": escalation_id,
                "idempotency_key": (f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:{escalation_id}"),
                "payload": payload,
            }
        )
        result["operator_escalation_event_ids"].append(escalation_id)
        return True

    governance_groups = _governance_groups_for_team(state, team.id)
    if not governance_groups:
        result["errors"].append(
            f"team {team.id} is configured for governance escalation but is not in a "
            "governance group"
        )
        return False

    now = utc_now()
    for group in governance_groups:
        agenda_id = f"governance-agenda-{uuid4().hex}"
        group_runtime = state.governance_runtimes.setdefault(
            group.id,
            GovernanceRuntime(governance_group_id=group.id),
        )
        state.governance_agenda[agenda_id] = GovernanceAgendaItem(
            id=agenda_id,
            governance_group_id=group.id,
            item_type=GovernanceAgendaItemType.TEAM_ESCALATION,
            title=title,
            summary=message,
            team_id=team.id,
            project_id=project_id,
            payload=payload | {"escalation_id": escalation_id, "governance_group_id": group.id},
            created_at=now,
            updated_at=now,
        )
        group_runtime.next_wake_at = None
        group_runtime.updated_at = now
        result.setdefault("created_governance_agenda_item_ids", []).append(agenda_id)
    return True


def _apply_superior_stand_down(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
) -> str:
    target_id = _required_text(request.get("agent_id"), "agent_id")
    reason = _required_text(request.get("reason"), "reason")
    target = _require_present(state.agents, target_id, "agent")
    if target.team_id != actor.team_id:
        raise ValueError(f"agent {target.id} is not in team {actor.team_id}")
    if not _is_direct_subordinate(actor, target.id):
        raise ValueError(f"agent {actor.id} can only stand down a direct subordinate")
    if target.status == AgentStatus.ARCHIVED:
        raise ValueError(f"agent {target.id} is archived")
    now = utc_now()
    stand_down = AgentStandDown(
        id=f"stand-down-{uuid4().hex}",
        agent_id=target.id,
        reason=reason,
        source=AgentStandDownSource.SUPERIOR,
        set_by=actor.id,
        set_at=now,
    )
    state.stand_downs[stand_down.id] = stand_down
    for affected_id in _agent_subtree_ids(state, target.id):
        runtime = state.agent_runtimes.get(affected_id)
        if runtime is None:
            continue
        runtime.next_wake_at = None
        runtime.updated_at = now
    return stand_down.id


def _apply_superior_stand_down_revocation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
) -> list[str]:
    target_id = _required_text(request.get("agent_id"), "agent_id")
    target = _require_present(state.agents, target_id, "agent")
    if target.team_id != actor.team_id:
        raise ValueError(f"agent {target.id} is not in team {actor.team_id}")
    if not _is_direct_subordinate(actor, target.id):
        raise ValueError(f"agent {actor.id} can only revoke stand-downs for a direct subordinate")
    now = utc_now()
    revoked_ids: list[str] = []
    for stand_down in state.stand_downs.values():
        if (
            stand_down.agent_id == target.id
            and stand_down.source == AgentStandDownSource.SUPERIOR
            and stand_down.set_by == actor.id
            and stand_down.active
        ):
            stand_down.cleared_by = actor.id
            stand_down.cleared_at = now
            revoked_ids.append(stand_down.id)
    if not revoked_ids:
        raise ValueError(f"agent {target.id} has no active superior stand-down by {actor.id}")
    for affected_id in _agent_subtree_ids(state, target.id):
        if _active_stand_downs_for_agent(state, affected_id):
            continue
        _mark_agent_work_due(state, agent_id=affected_id, now=now)
    return revoked_ids


def _supervises(state: TeamState, supervisor_id: str, agent_id: str) -> bool:
    """Return True if supervisor_id is an ancestor of agent_id in the reporting chain."""

    seen: set[str] = set()
    current = state.agents.get(agent_id)
    while current is not None and current.reports_to_agent_id is not None:
        superior_id = current.reports_to_agent_id
        if superior_id in seen:
            break
        seen.add(superior_id)
        if superior_id == supervisor_id:
            return True
        current = state.agents.get(superior_id)
    return False


def _has_dependency_cycle(dependency_map: dict[str, list[str]]) -> bool:
    """Iterative DFS mirroring TeamState._validate_no_dependency_cycle."""

    visited: set[str] = set()
    visiting: set[str] = set()
    for start in dependency_map:
        if start in visited:
            continue
        stack: list[str] = [start]
        while stack:
            node = stack[-1]
            visiting.add(node)
            advanced = False
            for dependency_id in dependency_map.get(node, []):
                if dependency_id not in dependency_map:
                    continue
                if dependency_id in visited:
                    continue
                if dependency_id in visiting:
                    return True
                stack.append(dependency_id)
                advanced = True
                break
            if not advanced:
                visiting.discard(node)
                visited.add(node)
                stack.pop()
    return False


def _task_graph_edit_error(
    state: TeamState,
    *,
    task_id: str | None,
    project_id: str,
    parent_task_id: str | None,
    dependency_task_ids: list[str],
) -> str | None:
    """Pre-check the Step 1 task-graph invariants for a proposed create/edit.

    Mirrors TeamState._validate_task_graph but simulates the proposed edge(s) so
    apply_agent_action can reject the item instead of letting the post-mutation
    re-validation abort the whole action. ``task_id`` is None for a new task.
    """

    if task_id is not None:
        if parent_task_id == task_id:
            return "task cannot be its own parent"
        if task_id in dependency_task_ids:
            return "task cannot depend on itself"
    if parent_task_id is not None:
        parent = state.tasks.get(parent_task_id)
        if parent is None:
            return f"task references missing parent {parent_task_id}"
        if parent.project_id != project_id:
            return f"task parent {parent_task_id} belongs to project {parent.project_id}"
    for dependency_id in dependency_task_ids:
        dependency = state.tasks.get(dependency_id)
        if dependency is None:
            return f"task references missing dependency {dependency_id}"
        if dependency.project_id != project_id:
            return f"task dependency {dependency_id} belongs to project {dependency.project_id}"
    # Walk up the parent chain from the proposed parent; reaching task_id is a cycle.
    if task_id is not None and parent_task_id is not None:
        seen: set[str] = set()
        current: str | None = parent_task_id
        while current is not None:
            if current == task_id:
                return f"task parent cycle includes {task_id}"
            if current in seen:
                break
            seen.add(current)
            current_task = state.tasks.get(current)
            current = current_task.parent_task_id if current_task is not None else None
    # Simulate the dependency graph with the proposed edges and look for a cycle.
    if task_id is not None:
        dependency_map = {
            existing_id: list(task.dependency_task_ids) for existing_id, task in state.tasks.items()
        }
        dependency_map[task_id] = list(dependency_task_ids)
        if _has_dependency_cycle(dependency_map):
            return f"task dependency cycle includes {task_id}"
    return None


def _review_profile_for_request(
    state: TeamState,
    *,
    profile_id: str | None,
    expected_team_id: str,
    project_id: str | None,
    reviewer: PersistentAgent,
) -> tuple[ReviewProfile | None, str | None]:
    if profile_id is None:
        return None, None
    profile = state.review_profiles.get(profile_id)
    if profile is None:
        return None, f"review request references missing review profile {profile_id}"
    if profile.archived:
        return None, f"review profile {profile_id} is archived"
    if profile.team_id != expected_team_id:
        return (
            None,
            f"review profile {profile_id} is not in the same team as reviewer {reviewer.id}",
        )
    if profile.project_id is not None and profile.project_id != project_id:
        return None, f"review profile {profile_id} does not apply to project {project_id}"
    if profile.applicable_role_ids and reviewer.role_id not in profile.applicable_role_ids:
        return (
            None,
            f"review profile {profile_id} does not apply to reviewer role {reviewer.role_id}",
        )
    return profile, None


def _apply_review_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        result["errors"].append(f"review request references missing deliverable {deliverable_id}")
        return None
    reviewer_id = _required_text(request.get("reviewer_agent_id"), "reviewer_agent_id")
    reviewer = state.agents.get(reviewer_id)
    if reviewer is None:
        result["errors"].append(f"review request references missing reviewer {reviewer_id}")
        return None
    if reviewer.team_id != actor.team_id:
        result["errors"].append(f"reviewer {reviewer_id} is not in the same team as {actor.id}")
        return None
    task = state.tasks.get(deliverable.task_id)
    project_id = task.project_id if task is not None else None
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or (project_id is not None and _is_project_lead_for(state, actor.id, project_id))
        or _is_team_manager(state, actor)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request review of deliverable {deliverable_id}"
        )
        return None
    instructions = _optional_text(request.get("instructions"))
    profile_id = _optional_text(request.get("review_profile_id"))
    profile, profile_error = _review_profile_for_request(
        state,
        profile_id=profile_id,
        expected_team_id=actor.team_id,
        project_id=project_id,
        reviewer=reviewer,
    )
    if profile_error is not None:
        result["errors"].append(profile_error)
        return None
    existing_review = _active_review_request_for_reviewer(
        state,
        deliverable_id=deliverable_id,
        reviewer_id=reviewer_id,
    )
    if existing_review is not None:
        result.setdefault("existing_review_request_ids", []).append(existing_review.id)
        return existing_review.id
    review_id = _optional_text(request.get("id"))
    if review_id is None:
        review_id = _generated_id(
            prefix="review",
            labels=[deliverable_id, profile_id or reviewer_id],
            existing_ids=state.review_requests,
        )
    if review_id in state.review_requests:
        result["errors"].append(f"review request {review_id} already exists")
        return None
    now = utc_now()
    review = ReviewRequest(
        id=review_id,
        deliverable_id=deliverable_id,
        reviewer_agent_id=reviewer_id,
        state=TeamRecordState.PENDING,
        review_profile_id=profile_id,
        instructions=instructions,
        created_at=now,
    )
    state.review_requests[review.id] = review
    deliverable.review_request_ids = _append_unique(
        deliverable.review_request_ids,
        review.id,
    )
    payload: dict[str, Any] = {
        "kind": "review_request",
        "review_request_id": review.id,
        "deliverable_id": deliverable_id,
        "instructions": instructions or "",
        "artifact_refs": list(deliverable.artifact_refs),
    }
    verification_payload = _deliverable_verification_payload(deliverable)
    if verification_payload is not None:
        payload["verification"] = verification_payload
    if profile is not None:
        payload["review_profile"] = {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "criteria": [
                {
                    "id": criterion.id,
                    "label": criterion.label,
                    "description": criterion.description,
                    "required": criterion.required,
                }
                for criterion in profile.criteria
            ],
        }
    inbox_id = f"inbox-{uuid4().hex}"
    item = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=reviewer_id,
        sender_agent_id=actor.id,
        task_id=deliverable.task_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=True,
        priority=TeamInboxPriority.NORMAL,
        payload=payload,
        created_at=now,
        updated_at=now,
    )
    state.inbox[inbox_id] = item
    result["created_inbox_item_ids"].append(inbox_id)
    _mark_agent_work_due(state, agent_id=reviewer_id, now=now)
    _append_agent_wake_requested_event_for_inbox_item(
        state,
        item=item,
        result=result,
        now=now,
    )
    return review.id


def _active_review_request_for_reviewer(
    state: TeamState,
    *,
    deliverable_id: str,
    reviewer_id: str,
) -> ReviewRequest | None:
    matches = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id == deliverable_id
        and review.reviewer_agent_id == reviewer_id
        and review.state in _MANAGER_FALLBACK_PENDING_REVIEW_STATES
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda review: (
            review.created_at or datetime.min.replace(tzinfo=UTC),
            review.id,
        )
    )
    return matches[0]


def _review_request_was_created(result: Mapping[str, Any], review_request_id: str) -> bool:
    return review_request_id not in set(result.get("existing_review_request_ids") or [])


def _apply_review_decision(
    state: TeamState,
    *,
    actor: PersistentAgent,
    decision: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
    enforce_manager_fallback_routing_guard: bool = False,
) -> str | None:
    review_request_id = _required_text(decision.get("review_request_id"), "review_request_id")
    review = state.review_requests.get(review_request_id)
    if review is None:
        result["errors"].append(
            f"review decision references missing review request {review_request_id}"
        )
        return None
    if review.reviewer_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} is not the assigned reviewer for {review_request_id}"
        )
        return None
    if (
        enforce_manager_fallback_routing_guard
        and _optional_text(decision.get("override_routing_gap_reason")) is None
    ):
        guard_error = _manager_fallback_review_decision_guard_error(
            state,
            actor=actor,
            review=review,
        )
        if guard_error is not None:
            result["errors"].append(guard_error)
            return None
    raw_state = _required_text(decision.get("state"), "state")
    try:
        decision_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"review decision has invalid state {raw_state}")
        return None
    if not _is_review_decision_state(decision_state):
        result["errors"].append(
            f"review decision state {raw_state} must be accepted, rejected, or blocked"
        )
        return None
    criterion_results, criteria_error = _build_criterion_results(
        state,
        review,
        _list_of_mappings(decision.get("criteria"), "criteria"),
    )
    if criteria_error is not None:
        result["errors"].append(criteria_error)
        return None
    if criterion_results is not None:
        review.criteria = criterion_results
    _cascade_review_decision(
        state,
        review=review,
        decision_state=decision_state,
        findings=_string_list(decision.get("findings"), "findings"),
        projects_to_recalc=projects_to_recalc,
        result=result,
    )
    return review.id


def _build_criterion_results(
    state: TeamState,
    review: ReviewRequest,
    raw_criteria: list[dict[str, Any]],
) -> tuple[list[ReviewCriterionResult] | None, str | None]:
    """Validate criterion-result mappings against the review's profile.

    Returns ``(results, None)`` on success or ``(None, error_message)`` so callers
    can append the error to ``result["errors"]`` without mutating state. ``results``
    is an empty list when no criteria were supplied.
    """

    if not raw_criteria:
        return [], None
    profile = (
        state.review_profiles.get(review.review_profile_id)
        if review.review_profile_id is not None
        else None
    )
    if profile is None:
        return None, (
            f"review decision for {review.id} has criterion results but no review profile"
        )
    valid_ids = {criterion.id for criterion in profile.criteria}
    seen: set[str] = set()
    results: list[ReviewCriterionResult] = []
    for entry in raw_criteria:
        criterion_id = _optional_text(entry.get("criterion_id"))
        if criterion_id is None:
            return None, "review decision criterion result is missing criterion_id"
        if criterion_id not in valid_ids:
            return None, (
                f"review decision for {review.id} references unknown criterion {criterion_id}"
            )
        if criterion_id in seen:
            return None, (
                f"review decision for {review.id} has duplicate criterion result {criterion_id}"
            )
        seen.add(criterion_id)
        raw_state = _optional_text(entry.get("state"))
        if raw_state not in {"passed", "failed", "skipped"}:
            return None, (f"review decision criterion {criterion_id} has invalid state {raw_state}")
        results.append(
            ReviewCriterionResult(
                criterion_id=criterion_id,
                state=raw_state,
                finding=_optional_text(entry.get("finding")) or "",
            )
        )
    return results, None


def _is_review_decision_state(decision_state: TeamRecordState) -> bool:
    return decision_state in {
        TeamRecordState.ACCEPTED,
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }


def _cascade_review_decision(
    state: TeamState,
    *,
    review: ReviewRequest,
    decision_state: TeamRecordState,
    findings: list[str],
    projects_to_recalc: set[str],
    result: dict[str, Any] | None = None,
) -> None:
    """Apply a review decision and cascade it to the deliverable, task, and project.

    This is the single source of truth for review decision cascade rules, shared by
    the ``review_decisions`` agent action and the Admin API decision endpoint.
    """

    review.state = decision_state
    review.findings = findings
    review.decision_at = utc_now()
    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    task = _require_present(state.tasks, deliverable.task_id, "task")
    now = utc_now()
    if decision_state == TeamRecordState.ACCEPTED:
        all_accepted = all(
            state.review_requests[review_id].state == TeamRecordState.ACCEPTED
            for review_id in deliverable.review_request_ids
        )
        if all_accepted:
            deliverable.state = TeamRecordState.ACCEPTED
            task_deliverables = _active_task_deliverables(state, task)
            if task_deliverables and all(
                item.state == TeamRecordState.ACCEPTED for item in task_deliverables
            ):
                _set_task_state(task, TeamTaskState.ACCEPTED, now=now)
                task.updated_at = now
    elif decision_state == TeamRecordState.REJECTED:
        deliverable.state = TeamRecordState.REJECTED
        _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
        task.updated_at = now
        rework_agent_id = task.owner_agent_id
        inbox_id = f"inbox-{uuid4().hex}"
        item = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=rework_agent_id,
            sender_agent_id=review.reviewer_agent_id,
            task_id=task.id,
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=True,
            priority=TeamInboxPriority.NORMAL,
            payload={
                "kind": "review_rejected",
                "review_request_id": review.id,
                "deliverable_id": deliverable.id,
                "supersedes_deliverable_id": deliverable.id,
                "task_id": task.id,
                "summary": "Review rejected; revise and resubmit.",
                "findings": list(findings),
                "artifact_refs": list(deliverable.artifact_refs),
            },
        )
        state.inbox[inbox_id] = item
        if result is not None:
            result["created_inbox_item_ids"].append(inbox_id)
            _append_agent_wake_requested_event_for_inbox_item(
                state,
                item=item,
                result=result,
                now=now,
            )
        _mark_agent_work_due(
            state,
            agent_id=rework_agent_id,
            now=now,
        )
    else:  # blocked
        deliverable.state = TeamRecordState.BLOCKED
        _set_task_state(task, TeamTaskState.BLOCKED, now=now)
        task.updated_at = now
    projects_to_recalc.add(task.project_id)


def _apply_goal_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(update.get("project_id"), "project_id")
    goal_id = _required_text(update.get("goal_id"), "goal_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"goal update references missing project {project_id}")
        return None
    permitted = _is_project_lead_for(state, actor.id, project_id) or (
        _is_team_manager(state, actor) and project.team_id == actor.team_id
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to update goals on project {project_id}"
        )
        return None
    raw_state = _required_text(update.get("state"), "state")
    try:
        goal_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"goal update has invalid state {raw_state}")
        return None
    goal = next((item for item in project.goals if item.id == goal_id), None)
    if goal is None:
        result["errors"].append(f"goal {goal_id} does not exist on project {project_id}")
        return None
    goal.state = goal_state
    project.updated_at = utc_now()
    projects_to_recalc.add(project_id)
    return goal_id


def _project_git_or_error(
    project: Project,
    result: dict[str, Any],
) -> Any:
    if project.git is None:
        result["errors"].append(f"project {project.id} has no git configuration for promotions")
        return None
    return project.git


def _bare_repo_fields(git: Any) -> dict[str, str]:
    if git.bare_repo_name:
        return {"bare_repo_name": git.bare_repo_name}
    return {"bare_repo_path": git.bare_repo_path}


def _deliverable_git_refs_from_payload(value: object) -> DeliverableGitRefs:
    payload = _optional_mapping(value, "git_refs") or {}
    agent_branch = _optional_text(payload.get("agent_branch"))
    if agent_branch is not None:
        agent_branch = validate_branch_name(agent_branch)
    return DeliverableGitRefs(
        agent_branch=agent_branch,
        commit_sha=_optional_text(payload.get("commit_sha")),
    )


def _normalize_deliverable_verification_mapping(
    value: object,
    *,
    field_name: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = _optional_mapping(value, field_name) or {}
    status = _required_text(payload.get("status"), f"{field_name}.status").lower()
    if status not in _DELIVERABLE_VERIFICATION_STATUSES:
        allowed = ", ".join(sorted(_DELIVERABLE_VERIFICATION_STATUSES))
        raise ValueError(f"{field_name}.status must be one of: {allowed}")
    return {
        "status": status,
        "summary": _optional_text(payload.get("summary")),
        "evidence_refs": _string_list(
            payload.get("evidence_refs"),
            f"{field_name}.evidence_refs",
        ),
    }


def _deliverable_verification_from_payload(
    value: object,
) -> DeliverableVerification | None:
    payload = _normalize_deliverable_verification_mapping(
        value,
        field_name="verification",
    )
    if payload is None:
        return None
    return DeliverableVerification.model_validate(payload)


def _deliverable_verification_payload(deliverable: Deliverable) -> dict[str, Any] | None:
    if deliverable.verification is None:
        return None
    return deliverable.verification.model_dump(mode="json")


def _deliverable_submission_next_action_hints(
    *,
    deliverable_id: str,
    deliverable: Mapping[str, Any],
    created_inbox_ids: Sequence[str],
    created_review_request_ids: Sequence[str],
) -> list[str]:
    hints: list[str] = []
    git_refs = _optional_mapping(deliverable.get("git_refs"), "git_refs") or {}
    if git_refs.get("agent_branch") or git_refs.get("commit_sha"):
        hints.append(
            "Deliverable git_refs are provenance only; they do not publish a "
            "review branch by themselves."
        )
        hints.append(
            f"Call team_publish_review_branch with deliverable_id {deliverable_id!r} "
            "only when a durable review branch is needed for reviewer access, "
            "feature promotion, release sequencing, or an explicit superior request."
        )
    if created_review_request_ids:
        hints.append(
            "Review requests were created; wait for assigned reviewers to decide "
            "them before treating the task as accepted."
        )
    elif created_inbox_ids:
        hints.append(
            "A deliverable notice was sent to your superior; wait for review, "
            "acceptance, or branch-promotion instructions before taking more "
            "completion actions."
        )
    verification = _optional_mapping(deliverable.get("verification"), "verification")
    if verification is not None and verification.get("status") == "not_run":
        hints.append(
            "Verification was recorded as not_run; keep the reason in "
            "verification.summary so reviewers can decide whether more validation "
            "is required."
        )
    return hints


def _superseded_deliverable_for_rework(
    state: TeamState,
    *,
    task: AgentTask,
    producer_agent_id: str,
    supersedes_deliverable_id: str | None,
) -> Deliverable | None:
    if supersedes_deliverable_id is None:
        return None
    deliverable = state.deliverables.get(supersedes_deliverable_id)
    if deliverable is None:
        raise ValueError(
            f"supersedes_deliverable_id references missing deliverable {supersedes_deliverable_id}"
        )
    if deliverable.task_id != task.id:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} belongs to task "
            f"{deliverable.task_id}, not {task.id}"
        )
    if (
        deliverable.producer_agent_id != producer_agent_id
        and task.owner_agent_id != producer_agent_id
    ):
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} was produced by "
            f"{deliverable.producer_agent_id}; only that producer or current "
            f"task owner {task.owner_agent_id} may supersede it"
        )
    if deliverable.state != TeamRecordState.REJECTED:
        raise ValueError(f"supersedes_deliverable_id {deliverable.id} is not rejected")
    if deliverable.superseded_by_deliverable_id is not None:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} is already superseded by "
            f"{deliverable.superseded_by_deliverable_id}"
        )
    return deliverable


def _active_task_deliverables(
    state: TeamState,
    task: AgentTask,
) -> list[Deliverable]:
    return [
        deliverable
        for deliverable_id in task.deliverable_ids
        if (deliverable := state.deliverables.get(deliverable_id)) is not None
        and deliverable.superseded_by_deliverable_id is None
    ]


def _emit_git_promotion_event(
    result: dict[str, Any],
    *,
    promotion_id: str,
    payload: dict[str, Any],
) -> None:
    result["events"].append(
        {
            "event_type": GIT_PROMOTION_EVENT_TYPE,
            "correlation_key": promotion_id,
            "idempotency_key": f"team-addon:{GIT_PROMOTION_EVENT_TYPE}:{promotion_id}",
            "payload": payload,
        }
    )


def _emit_git_release_recovery_event(
    result: dict[str, Any],
    *,
    release_package_id: str,
    agent_branch: str,
    payload: dict[str, Any],
) -> None:
    recovery_key = f"{release_package_id}:{agent_branch}"
    commit_sha = _optional_text(payload.get("expected_commit_sha"))
    if commit_sha is not None:
        recovery_key = f"{recovery_key}:{commit_sha}"
    result["events"].append(
        {
            "event_type": GIT_RELEASE_RECOVERY_EVENT_TYPE,
            "correlation_key": release_package_id,
            "idempotency_key": (f"team-addon:{GIT_RELEASE_RECOVERY_EVENT_TYPE}:{recovery_key}"),
            "payload": payload,
        }
    )


def _normalize_agent_branch_ref(value: object) -> str:
    text = _required_text(value, "agent_branch")
    prefix = "refs/heads/"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    branch = validate_branch_name(text)
    if not branch.startswith("agents/"):
        raise ValueError("agent_branch must be under agents/")
    return branch


def _apply_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    assignment_id = _optional_text(request.get("assignment_id"))
    if assignment_id is None:
        return _apply_persistent_git_review_promotion(
            state,
            actor=actor,
            request=request,
            result=result,
        )
    return _apply_ephemeral_git_review_promotion(
        state,
        actor=actor,
        request=request,
        result=result,
        assignment_id=assignment_id,
    )


def _apply_ephemeral_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
    assignment_id: str,
) -> str | None:
    assignment = state.ephemeral_assignments.get(assignment_id)
    if assignment is None:
        result["errors"].append(
            f"git review promotion references missing ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.parent_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} cannot promote ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.state not in {
        EphemeralAssignmentState.COMPLETED,
        EphemeralAssignmentState.ACCEPTED,
    }:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} is not completed or accepted"
        )
        return None
    if not assignment.agent_branch:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} has no agent_branch to promote"
        )
        return None
    deliverable_id = _optional_text(request.get("deliverable_id"))
    if deliverable_id is not None:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(
                f"git review promotion references missing deliverable {deliverable_id}"
            )
            return None
        deliverable_task = state.tasks.get(deliverable.task_id)
        if (
            deliverable_task is None
            or deliverable_task.id != assignment.task_id
            or deliverable_task.project_id != assignment.project_id
        ):
            result["errors"].append(
                f"deliverable {deliverable_id} does not belong to ephemeral assignment {assignment_id}"
            )
            return None
    project = state.projects.get(assignment.project_id)
    if project is None:
        result["errors"].append(
            f"git review promotion references missing project {assignment.project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    try:
        source_branch = validate_branch_name(assignment.agent_branch)
    except ValueError as exc:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} has invalid agent_branch: {exc}"
        )
        return None
    target_branch = review_branch(actor.team_id, assignment_id)
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=assignment.task_id,
        deliverable_id=deliverable_id,
        actor_agent_id=actor.id,
        source_branch=source_branch,
        target_branch=target_branch,
        promotion_kind="review",
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "review",
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "assignment_id": assignment_id,
    }
    payload.update(_bare_repo_fields(git))
    if deliverable_id is not None:
        payload["deliverable_id"] = deliverable_id
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _apply_persistent_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        result["errors"].append(
            f"git review promotion references missing deliverable {deliverable_id}"
        )
        return None
    task = state.tasks.get(deliverable.task_id)
    if task is None:
        result["errors"].append(
            f"git review promotion references missing task {deliverable.task_id}"
        )
        return None
    project = state.projects.get(task.project_id)
    if project is None:
        result["errors"].append(
            f"git review promotion references missing project {task.project_id}"
        )
        return None
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or _is_project_lead_for(state, actor.id, project.id)
        or (_is_team_manager(state, actor) and project.team_id == actor.team_id)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to promote deliverable {deliverable_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None

    commit_sha = _optional_text(request.get("commit_sha")) or deliverable.git_refs.commit_sha
    source_branch_value = (
        _optional_text(request.get("source_branch")) or deliverable.git_refs.agent_branch
    )
    if source_branch_value is not None:
        try:
            source_branch = validate_branch_name(source_branch_value)
        except ValueError as exc:
            result["errors"].append(f"git review promotion has invalid source_branch: {exc}")
            return None
    elif commit_sha is not None:
        source_branch = _commit_source_branch(commit_sha)
    else:
        result["errors"].append(
            f"git review promotion for deliverable {deliverable_id} requires source_branch or commit_sha"
        )
        return None

    target_branch = review_branch(actor.team_id, deliverable_id)
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=task.id,
        deliverable_id=deliverable_id,
        actor_agent_id=actor.id,
        source_branch=source_branch,
        target_branch=target_branch,
        promotion_kind="review",
        commit_sha=commit_sha,
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "review",
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "deliverable_id": deliverable_id,
    }
    payload.update(_bare_repo_fields(git))
    if source_branch_value is not None:
        payload["source_branch"] = source_branch_value
    if commit_sha is not None:
        payload["commit_sha"] = commit_sha
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _commit_source_branch(commit_sha: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in commit_sha).strip("-")
    if not slug:
        slug = "unknown"
    return validate_branch_name(f"commit/{slug[:64]}")


def _apply_git_feature_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    task_id = _required_text(update.get("task_id"), "task_id")
    task = state.tasks.get(task_id)
    if task is None:
        result["errors"].append(f"git feature update references missing task {task_id}")
        return None
    if task.owner_agent_id != actor.id and not _supervises(state, actor.id, task.owner_agent_id):
        result["errors"].append(f"agent {actor.id} is not permitted to consolidate task {task_id}")
        return None
    project = state.projects.get(task.project_id)
    if project is None:
        result["errors"].append(f"git feature update references missing project {task.project_id}")
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    raw_sources = _string_list(update.get("source_review_branches"), "source_review_branches")
    if not raw_sources:
        result["errors"].append(
            f"git feature update for task {task_id} requires source_review_branches"
        )
        return None
    try:
        source_branches = [validate_branch_name(branch) for branch in raw_sources]
        target_branch = validate_branch_name(
            _optional_text(update.get("feature_branch"))
            or feature_branch(actor.team_id, task_id, actor.id)
        )
    except ValueError as exc:
        result["errors"].append(f"git feature update has invalid branch name: {exc}")
        return None
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=task_id,
        actor_agent_id=actor.id,
        source_branch=source_branches[0],
        target_branch=target_branch,
        promotion_kind="feature",
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "feature",
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "base_branch": git.base_branch,
        "source_branches": source_branches,
        "target_branch": target_branch,
        "task_id": task_id,
    }
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _apply_git_release_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(request.get("project_id"), "project_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"git release request references missing project {project_id}")
        return None
    permitted = (
        _is_project_lead_for(state, actor.id, project_id)
        or _agent_has_role_permission_for_project(
            state,
            actor,
            RolePermission.REQUEST_RELEASE,
            project_id,
        )
        or (_is_team_manager(state, actor) and project.team_id == actor.team_id)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request releases on project {project_id}"
        )
        return None
    return _build_release_package(
        state,
        project=project,
        request=request,
        result=result,
        projects_to_recalc=projects_to_recalc,
        actor_agent_id=actor.id,
        team_id=actor.team_id,
    )


def _release_request_permitted(
    state: TeamState,
    *,
    actor: PersistentAgent,
    project: Project,
) -> bool:
    return (
        _is_project_lead_for(state, actor.id, project.id)
        or _agent_has_role_permission_for_project(
            state,
            actor,
            RolePermission.REQUEST_RELEASE,
            project.id,
        )
        or (_is_team_manager(state, actor) and project.team_id == actor.team_id)
    )


def _failed_release_promotion_for_recovery(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    promotion_ids = set(release.git_promotion_record_ids)
    promotions = [
        promotion
        for promotion in state.git_promotions.values()
        if promotion.promotion_kind == "release"
        and promotion.state == "failed"
        and (promotion.id in promotion_ids or promotion.release_package_id == release.id)
    ]
    if not promotions:
        return None
    promotions.sort(
        key=lambda promotion: (
            promotion.updated_at,
            promotion.created_at,
            promotion.id,
        ),
        reverse=True,
    )
    return promotions[0]


def _apply_git_release_recovery(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    release_id = _required_text(request.get("release_package_id"), "release_package_id")
    release = state.release_packages.get(release_id)
    if release is None:
        result["errors"].append(
            f"git release recovery references missing release package {release_id}"
        )
        return None
    project = state.projects.get(release.project_id)
    if project is None:
        result["errors"].append(
            f"git release recovery references missing project {release.project_id}"
        )
        return None
    if not _release_request_permitted(state, actor=actor, project=project):
        result["errors"].append(
            f"agent {actor.id} is not permitted to recover releases on project {project.id}"
        )
        return None
    if release.state != TeamRecordState.BLOCKED:
        result["errors"].append(
            f"release package {release.id} is {release.state.value}; only blocked "
            "release packages can be recovered"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    try:
        agent_branch = _normalize_agent_branch_ref(request.get("agent_branch"))
    except ValueError as exc:
        result["errors"].append(f"git release recovery has invalid agent_branch: {exc}")
        return None
    failed_promotion = _failed_release_promotion_for_recovery(state, release)
    if failed_promotion is None:
        result["errors"].append(
            f"release package {release.id} has no failed release promotion to recover"
        )
        return None
    target_branch = release.release_branch or failed_promotion.target_branch
    if release.release_branch and release.release_branch != failed_promotion.target_branch:
        result["errors"].append(
            f"release package {release.id} release_branch {release.release_branch} "
            f"does not match failed promotion target {failed_promotion.target_branch}"
        )
        return None
    target_branch = validate_branch_name(target_branch)
    expected_commit_sha = _required_text(request.get("commit_sha"), "commit_sha")
    payload: dict[str, Any] = {
        "release_package_id": release.id,
        "promotion_id": failed_promotion.id,
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "agent_branch": agent_branch,
        "target_branch": target_branch,
        "release_branch": target_branch,
        "canonical_branch": release.canonical_branch
        or canonical_release_branch(project, release.id),
        "base_branch": git.base_branch,
        "expected_commit_sha": expected_commit_sha,
    }
    payload.update(_bare_repo_fields(git))
    summary = _optional_text(request.get("summary"))
    if summary is not None:
        payload["summary"] = summary
    _emit_git_release_recovery_event(
        result,
        release_package_id=release.id,
        agent_branch=agent_branch,
        payload=payload,
    )
    return release.id


def _build_release_package(
    state: TeamState,
    *,
    project: Project,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
    actor_agent_id: str,
    team_id: str,
) -> str | None:
    """Validate deliverables and build a pending ReleasePackage with git promotions.

    Shared by the ``git_release_requests`` agent action and the Admin API manual
    release-package endpoint so both enforce identical validation and branch naming.
    """

    project_id = project.id
    deliverable_ids = _string_list(request.get("deliverable_ids"), "deliverable_ids")
    if not deliverable_ids:
        result["errors"].append(
            f"git release request for project {project_id} requires deliverable_ids"
        )
        return None
    artifact_refs: list[str] = []
    feature_branches: list[str] = []
    feature_sources_by_task: dict[str, list[str]] = {}
    valid = True
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(
                f"git release request references missing deliverable {deliverable_id}"
            )
            valid = False
            continue
        task = state.tasks.get(deliverable.task_id)
        if task is None or task.project_id != project_id:
            result["errors"].append(
                f"deliverable {deliverable_id} does not belong to project {project_id}"
            )
            valid = False
            continue
        if deliverable.state != TeamRecordState.ACCEPTED:
            result["errors"].append(
                f"deliverable {deliverable_id} is not accepted and cannot be released"
            )
            valid = False
            continue
        feature = deliverable.git_refs.feature_branch
        if feature:
            try:
                feature = validate_branch_name(feature)
            except ValueError as exc:
                result["errors"].append(
                    f"deliverable {deliverable_id} has invalid feature branch for release: {exc}"
                )
                valid = False
                continue
            feature_branches = _append_unique(feature_branches, feature)
        else:
            try:
                source_branch = _feature_promotion_source_branch_for_release(deliverable)
            except ValueError as exc:
                result["errors"].append(
                    f"deliverable {deliverable_id} has invalid Git refs for release: {exc}"
                )
                valid = False
                continue
            if source_branch is not None:
                feature_sources_by_task[task.id] = _append_unique(
                    feature_sources_by_task.get(task.id, []),
                    source_branch,
                )
            elif not deliverable.artifact_refs:
                result["errors"].append(
                    f"deliverable {deliverable_id} has neither a feature branch, "
                    "review branch, agent branch, nor artifact refs; Git release "
                    "requests can auto-promote accepted deliverables from "
                    "git_refs.review_branch or git_refs.agent_branch, or release "
                    "artifact-only deliverables with artifact refs"
                )
                valid = False
        for artifact_ref in deliverable.artifact_refs:
            artifact_refs = _append_unique(artifact_refs, artifact_ref)
    if not valid:
        return None
    release_id = _optional_text(request.get("id")) or f"release-{uuid4().hex}"
    if release_id in state.release_packages:
        result["errors"].append(
            _duplicate_release_package_error(state.release_packages[release_id])
        )
        return None
    target_release_branch: str | None = None
    target_canonical_branch: str | None = None
    git = None
    needs_git_release = bool(feature_branches or feature_sources_by_task)
    effective_team_id = team_id or project.team_id
    if needs_git_release:
        git = _project_git_or_error(project, result)
        if git is None:
            return None
        target_release_branch = validate_branch_name(
            _optional_text(request.get("release_branch"))
            or release_branch(effective_team_id, release_id)
        )
        target_canonical_branch = validate_branch_name(
            _optional_text(request.get("canonical_branch"))
            or canonical_release_branch(project, release_id)
        )
    promotion_ids: list[str] = []
    now = utc_now()
    if feature_sources_by_task:
        assert git is not None
        for task_id, source_branches in feature_sources_by_task.items():
            feature_promotion_id = f"promotion-{uuid4().hex}"
            state.git_promotions[feature_promotion_id] = GitPromotionRecord(
                id=feature_promotion_id,
                project_id=project.id,
                release_package_id=release_id,
                task_id=task_id,
                actor_agent_id=actor_agent_id,
                source_branch=source_branches[0],
                target_branch=feature_branch(effective_team_id, task_id, actor_agent_id),
                promotion_kind="feature",
            )
            promotion_ids.append(feature_promotion_id)
    state.release_packages[release_id] = ReleasePackage(
        id=release_id,
        project_id=project.id,
        deliverable_ids=deliverable_ids,
        artifact_refs=artifact_refs,
        feature_branches=feature_branches,
        release_branch=target_release_branch,
        canonical_branch=target_canonical_branch,
        git_promotion_record_ids=promotion_ids,
        state=TeamRecordState.PENDING,
        created_at=now,
        updated_at=now,
    )
    if feature_sources_by_task:
        assert git is not None
        for promotion_id in promotion_ids:
            promotion = state.git_promotions[promotion_id]
            assert promotion.task_id is not None
            payload = {
                "promotion_id": promotion_id,
                "promotion_kind": "feature",
                "project_id": project.id,
                "release_package_id": release_id,
                "host_repo_path": git.host_repo_path,
                "base_branch": git.base_branch,
                "source_branches": list(feature_sources_by_task[promotion.task_id]),
                "target_branch": promotion.target_branch,
                "task_id": promotion.task_id,
            }
            payload.update(_bare_repo_fields(git))
            _emit_git_promotion_event(
                result,
                promotion_id=promotion_id,
                payload=payload,
            )
        result["git_promotion_event_ids"].extend(promotion_ids)
    elif feature_branches:
        _queue_release_promotion_if_ready(
            state,
            release=state.release_packages[release_id],
            actor_agent_id=actor_agent_id,
            result=result,
        )
    projects_to_recalc.add(project_id)
    return release_id


def _duplicate_release_package_error(release: ReleasePackage) -> str:
    parts = [
        f"release package {release.id} already exists",
        f"state {release.state.value}",
    ]
    if release.release_branch:
        parts.append(f"release_branch {release.release_branch}")
    if release.canonical_branch:
        parts.append(f"canonical_branch {release.canonical_branch}")
    if release.error:
        parts.append(f"error {release.error}")
    parts.append(
        "do not submit git_release_requests for this id again; resolve the existing "
        "release package and follow its next_action_hint. Submit git_release_recoveries "
        "only when the hint identifies a failed release promotion and the resolved "
        "commit is pushed to an agents/... ref, or choose a new id only for a "
        "separate release"
    )
    return "; ".join(parts)


def _feature_promotion_source_branch_for_release(deliverable: Deliverable) -> str | None:
    refs = deliverable.git_refs
    source_branch = refs.review_branch or refs.agent_branch
    if source_branch is None:
        return None
    return validate_branch_name(source_branch)


def _release_feature_branches_from_deliverables(
    state: TeamState,
    release: ReleasePackage,
) -> list[str]:
    feature_branches = list(release.feature_branches)
    for deliverable_id in release.deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        feature_branch_value = deliverable.git_refs.feature_branch
        if feature_branch_value:
            feature_branches = _append_unique(feature_branches, feature_branch_value)
    return feature_branches


def _release_feature_prerequisites_ready(
    state: TeamState,
    release: ReleasePackage,
) -> bool:
    for promotion_id in release.git_promotion_record_ids:
        promotion = state.git_promotions.get(promotion_id)
        if promotion is None or promotion.promotion_kind != "feature":
            continue
        if promotion.state != "succeeded":
            return False
    return True


def _queue_release_promotion_if_ready(
    state: TeamState,
    *,
    release: ReleasePackage,
    actor_agent_id: str,
    result: dict[str, Any],
) -> None:
    if release.state in {
        TeamRecordState.ACCEPTED,
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }:
        return
    if not _release_feature_prerequisites_ready(state, release):
        return
    existing = next(
        (
            state.git_promotions[promotion_id]
            for promotion_id in release.git_promotion_record_ids
            if promotion_id in state.git_promotions
            and state.git_promotions[promotion_id].promotion_kind == "release"
        ),
        None,
    )
    if existing is not None:
        return
    project = state.projects.get(release.project_id)
    git = project.git if project is not None else None
    feature_branches = _release_feature_branches_from_deliverables(state, release)
    release.feature_branches = feature_branches
    release.updated_at = utc_now()
    if not feature_branches:
        return
    if project is None or git is None:
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"project {release.project_id} has no git configuration for release promotion"
        )
        result["error"] = release.error
        return
    target_release_branch = release.release_branch or release_branch(
        project.team_id,
        release.id,
    )
    release_promotion = GitPromotionRecord(
        id=f"promotion-{uuid4().hex}",
        project_id=project.id,
        release_package_id=release.id,
        actor_agent_id=actor_agent_id,
        source_branch=feature_branches[0],
        target_branch=target_release_branch,
        promotion_kind="release",
    )
    state.git_promotions[release_promotion.id] = release_promotion
    release.release_branch = target_release_branch
    release.canonical_branch = release.canonical_branch or canonical_release_branch(
        project,
        release.id,
    )
    release.git_promotion_record_ids = _append_unique(
        release.git_promotion_record_ids,
        release_promotion.id,
    )
    payload = {
        "promotion_id": release_promotion.id,
        "promotion_kind": "release",
        "project_id": project.id,
        "release_package_id": release.id,
        "host_repo_path": git.host_repo_path,
        "base_branch": git.base_branch,
        "source_branches": list(feature_branches),
        "target_branch": target_release_branch,
    }
    payload.update(_bare_repo_fields(git))
    _emit_git_promotion_event(
        result,
        promotion_id=release_promotion.id,
        payload=payload,
    )
    result["git_promotion_event_ids"].append(release_promotion.id)


def _branch_aliases(branch: str | None) -> set[str]:
    value = _optional_text(branch)
    if value is None:
        return set()
    aliases = {value}
    prefixes = ("refs/heads/", "refs/remotes/", "origin/", "local-evander/")
    changed = True
    while changed:
        changed = False
        for alias in list(aliases):
            for prefix in prefixes:
                if alias.startswith(prefix):
                    stripped = alias[len(prefix) :]
                    if stripped and stripped not in aliases:
                        aliases.add(stripped)
                        changed = True
    return aliases


def _promotion_source_branch_aliases(promotion: GitPromotionRecord) -> set[str]:
    aliases = _branch_aliases(promotion.source_branch)
    for key in ("source_branches", "merged_branches"):
        value = promotion.outputs.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                aliases.update(_branch_aliases(item))
    return aliases


def _deliverable_git_branch_aliases(deliverable: Deliverable) -> set[str]:
    refs = deliverable.git_refs
    aliases: set[str] = set()
    aliases.update(_branch_aliases(refs.agent_branch))
    aliases.update(_branch_aliases(refs.review_branch))
    aliases.update(_branch_aliases(refs.feature_branch))
    aliases.update(_branch_aliases(refs.release_branch))
    return aliases


def _deliverable_has_git_source_refs(deliverable: Deliverable) -> bool:
    refs = deliverable.git_refs
    return any(
        (
            refs.agent_branch,
            refs.review_branch,
            refs.feature_branch,
            refs.release_branch,
            refs.commit_sha,
            refs.promotion_record_ids,
        )
    )


def _accepted_source_deliverables_for_feature_promotion(
    state: TeamState,
    promotion: GitPromotionRecord,
    *,
    exclude_deliverable_ids: set[str] | None = None,
) -> list[Deliverable]:
    if promotion.task_id is None:
        return []
    task = state.tasks.get(promotion.task_id)
    if task is None:
        return []
    excluded = set(exclude_deliverable_ids or set())
    candidates = [
        deliverable
        for deliverable in (
            state.deliverables[item_id]
            for item_id in task.deliverable_ids
            if item_id in state.deliverables
        )
        if deliverable.id not in excluded
        and deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.superseded_by_deliverable_id is None
        and _deliverable_has_git_source_refs(deliverable)
    ]
    source_aliases = _promotion_source_branch_aliases(promotion)
    matching = [
        deliverable
        for deliverable in candidates
        if source_aliases and source_aliases & _deliverable_git_branch_aliases(deliverable)
    ]
    if matching:
        return matching
    return []


def _attach_feature_promotion_to_accepted_deliverables(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    commit_sha: str | None,
    result: dict[str, Any],
    deliverables: list[Deliverable] | None = None,
) -> bool:
    matching_deliverables = deliverables or _accepted_source_deliverables_for_feature_promotion(
        state,
        promotion,
    )
    if not matching_deliverables:
        return False
    now = utc_now()
    for deliverable in matching_deliverables:
        refs = deliverable.git_refs
        refs.feature_branch = promotion.target_branch
        if commit_sha is not None:
            refs.commit_sha = commit_sha
        refs.promotion_record_ids = _append_unique(refs.promotion_record_ids, promotion.id)
        task = state.tasks.get(deliverable.task_id)
        if task is not None:
            task.updated_at = now
        result["updated_deliverable_ids"] = _append_unique(
            result.setdefault("updated_deliverable_ids", []),
            deliverable.id,
        )
    promotion.deliverable_id = matching_deliverables[0].id
    return True


def _is_safe_synthetic_feature_deliverable(
    state: TeamState,
    *,
    deliverable: Deliverable,
    promotion: GitPromotionRecord,
) -> bool:
    refs = deliverable.git_refs
    extra_promotion_ids = [
        promotion_id for promotion_id in refs.promotion_record_ids if promotion_id != promotion.id
    ]
    return (
        deliverable.state == TeamRecordState.PENDING
        and deliverable.summary.startswith("Consolidated feature branch ")
        and not deliverable.artifact_refs
        and not deliverable.review_request_ids
        and deliverable.supersedes_deliverable_id is None
        and deliverable.superseded_by_deliverable_id is None
        and refs.agent_branch is None
        and refs.review_branch is None
        and refs.feature_branch == promotion.target_branch
        and refs.release_branch is None
        and not extra_promotion_ids
        and all(
            deliverable.id not in release.deliverable_ids
            for release in state.release_packages.values()
        )
    )


def _attach_promotion_success(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    commit_sha: str | None,
    result: dict[str, Any],
) -> None:
    """Attach a succeeded promotion's branch + record id to its linked records."""

    kind = promotion.promotion_kind
    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
    )
    if deliverable is not None:
        refs = deliverable.git_refs
        if kind == "review":
            refs.review_branch = promotion.target_branch
            if not promotion.source_branch.startswith("commit/"):
                refs.agent_branch = refs.agent_branch or promotion.source_branch
        elif kind == "feature":
            refs.feature_branch = promotion.target_branch
        elif kind == "release":
            refs.release_branch = promotion.target_branch
        if commit_sha is not None:
            refs.commit_sha = commit_sha
        refs.promotion_record_ids = _append_unique(refs.promotion_record_ids, promotion.id)
    elif (
        kind == "feature"
        and promotion.task_id is not None
        and promotion.task_id in state.tasks
        and not _attach_feature_promotion_to_accepted_deliverables(
            state,
            promotion=promotion,
            commit_sha=commit_sha,
            result=result,
        )
    ):
        # A feature consolidation has no deliverable yet; capture the consolidated
        # branch on a new deliverable so later release requests can find it.
        task = state.tasks[promotion.task_id]
        deliverable_id = f"deliverable-{uuid4().hex}"
        new_deliverable = Deliverable(
            id=deliverable_id,
            task_id=task.id,
            producer_agent_id=promotion.actor_agent_id,
            summary=f"Consolidated feature branch {promotion.target_branch}",
            state=TeamRecordState.PENDING,
            git_refs=DeliverableGitRefs(
                feature_branch=promotion.target_branch,
                commit_sha=commit_sha,
                promotion_record_ids=[promotion.id],
            ),
        )
        state.deliverables[deliverable_id] = new_deliverable
        task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable_id)
        task.updated_at = utc_now()
        promotion.deliverable_id = deliverable_id
        result["created_deliverable_ids"].append(deliverable_id)

    release = (
        state.release_packages.get(promotion.release_package_id)
        if promotion.release_package_id is not None
        else None
    )
    if release is not None:
        release.git_promotion_record_ids = _append_unique(
            release.git_promotion_record_ids,
            promotion.id,
        )
        if kind == "release":
            release.release_branch = release.release_branch or promotion.target_branch
            _queue_canonical_push_after_release_success(
                state,
                release=release,
                release_promotion=promotion,
                result=result,
            )
        elif kind == "feature":
            _queue_release_promotion_if_ready(
                state,
                release=release,
                actor_agent_id=promotion.actor_agent_id,
                result=result,
            )
        elif kind == "canonical_push":
            release.canonical_branch = release.canonical_branch or promotion.target_branch


def _queue_canonical_push_after_release_success(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    project = state.projects.get(release_promotion.project_id)
    git = project.git if project is not None else None
    if project is None or git is None:
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"project {release_promotion.project_id} has no git configuration "
            "for canonical release push"
        )
        result["error"] = release.error
        return

    source_branch = release.release_branch or release_promotion.target_branch
    target_branch = release.canonical_branch or canonical_release_branch(project, release.id)
    existing = next(
        (
            state.git_promotions[promotion_id]
            for promotion_id in release.git_promotion_record_ids
            if promotion_id in state.git_promotions
            and state.git_promotions[promotion_id].promotion_kind == "canonical_push"
        ),
        None,
    )
    if existing is None:
        existing = GitPromotionRecord(
            id=f"promotion-{uuid4().hex}",
            project_id=project.id,
            release_package_id=release.id,
            actor_agent_id=release_promotion.actor_agent_id,
            source_branch=source_branch,
            target_branch=target_branch,
            promotion_kind="canonical_push",
        )
        state.git_promotions[existing.id] = existing
        release.git_promotion_record_ids = _append_unique(
            release.git_promotion_record_ids,
            existing.id,
        )
    release.canonical_branch = target_branch

    if existing.state != "pending":
        return

    payload: dict[str, Any] = {
        "promotion_id": existing.id,
        "promotion_kind": "canonical_push",
        "project_id": project.id,
        "release_package_id": release.id,
        "host_repo_path": git.host_repo_path,
        "base_branch": git.base_branch,
        "source_branch": existing.source_branch,
        "target_branch": existing.target_branch,
    }
    if git.canonical_origin_url is not None:
        payload["canonical_origin_url"] = git.canonical_origin_url
    if git.provider is not None:
        payload["provider"] = git.provider
    _emit_git_promotion_event(result, promotion_id=existing.id, payload=payload)
    result["git_promotion_event_ids"].append(existing.id)


def _attach_promotion_failure(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    """Block linked records and notify the actor's superior or project lead."""

    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
    )
    if deliverable is not None:
        deliverable.state = TeamRecordState.BLOCKED
    release = (
        state.release_packages.get(promotion.release_package_id)
        if promotion.release_package_id is not None
        else None
    )
    if release is not None:
        release.state = TeamRecordState.BLOCKED
        release.error = promotion.error
    recipient_id = _promotion_failure_recipient(state, promotion)
    if recipient_id is not None and recipient_id in state.agents:
        inbox_id = f"inbox-{uuid4().hex}"
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=recipient_id,
            sender_agent_id=None,
            task_id=(
                promotion.task_id
                if promotion.task_id is not None and promotion.task_id in state.tasks
                else None
            ),
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=True,
            priority=TeamInboxPriority.NORMAL,
            payload={
                "kind": "git_promotion_failed",
                "promotion_id": promotion.id,
                "promotion_kind": promotion.promotion_kind,
                "project_id": promotion.project_id,
                "source_branch": promotion.source_branch,
                "target_branch": promotion.target_branch,
                "error": promotion.error,
            },
        )
        result["created_inbox_item_ids"].append(inbox_id)


def _promotion_failure_recipient(
    state: TeamState,
    promotion: GitPromotionRecord,
) -> str | None:
    actor = state.agents.get(promotion.actor_agent_id)
    if actor is not None and actor.reports_to_agent_id is not None:
        return actor.reports_to_agent_id
    lead_agent_id = project_lead_agent_id(state, promotion.project_id)
    if lead_agent_id is not None:
        return lead_agent_id
    return None


def _release_failure_recipient(state: TeamState, release: ReleasePackage) -> str | None:
    project = state.projects.get(release.project_id)
    if project is None:
        return None
    lead_agent_id = project_lead_agent_id(state, release.project_id)
    if lead_agent_id in state.agents:
        return lead_agent_id
    team = state.teams.get(project.team_id)
    return team.manager_agent_id if team is not None else None


def _recalculate_project_status(state: TeamState, project_id: str) -> None:
    project = state.projects.get(project_id)
    if project is None or project.status == ProjectStatus.ARCHIVED:
        return
    project_tasks = [task for task in state.tasks.values() if task.project_id == project_id]
    project_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if (task := state.tasks.get(deliverable.task_id)) is not None
        and task.project_id == project_id
    ]
    active_project_deliverables = [
        deliverable
        for deliverable in project_deliverables
        if deliverable.superseded_by_deliverable_id is None
    ]
    active_deliverable_ids = {deliverable.id for deliverable in active_project_deliverables}
    project_reviews = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id in active_deliverable_ids
    ]
    project_releases = [
        release for release in state.release_packages.values() if release.project_id == project_id
    ]

    pending_or_blocked = {TeamRecordState.PENDING, TeamRecordState.BLOCKED}
    blocked = (
        any(task.state == TeamTaskState.BLOCKED for task in project_tasks)
        or any(goal.state == TeamRecordState.BLOCKED for goal in project.goals)
        or any(
            deliverable.state == TeamRecordState.BLOCKED
            for deliverable in active_project_deliverables
        )
        or any(review.state == TeamRecordState.BLOCKED for review in project_reviews)
        or any(release.state == TeamRecordState.BLOCKED for release in project_releases)
    )
    completed = (
        all(goal.state == TeamRecordState.ACCEPTED for goal in project.goals)
        and not any(task.state in _NON_TERMINAL_TASK_STATES for task in project_tasks)
        and not any(
            deliverable.state in pending_or_blocked for deliverable in active_project_deliverables
        )
        and not any(review.state in pending_or_blocked for review in project_reviews)
        and not any(release.state in pending_or_blocked for release in project_releases)
    )
    if completed:
        new_status = ProjectStatus.COMPLETED
    elif blocked:
        new_status = ProjectStatus.BLOCKED
    else:
        new_status = ProjectStatus.ACTIVE
    if new_status != project.status:
        project.status = new_status
        project.updated_at = utc_now()


def _list_of_mappings(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain JSON objects")
        records.append(dict(item))
    return records


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _ephemeral_request_result() -> dict[str, Any]:
    return {
        "success": True,
        "applied": True,
        "errors": [],
        "created_ephemeral_assignment_ids": [],
        "events": [],
    }


def _create_ephemeral_assignments(
    state: TeamState,
    *,
    parent: PersistentAgent,
    requests: list[dict[str, Any]],
    dispatch_id: str | None,
    result: dict[str, Any],
) -> None:
    if not requests:
        return
    active_count = len(_active_ephemeral_assignments_for_parent(state, parent.id))
    max_concurrent = parent.ephemeral_policy.max_concurrent
    if max_concurrent <= 0:
        raise ValueError(f"agent {parent.id} is not allowed to spawn ephemeral agents")
    if active_count + len(requests) > max_concurrent:
        raise ValueError(
            f"agent {parent.id} ephemeral request would exceed max_concurrent={max_concurrent}"
        )

    normalized: list[tuple[dict[str, Any], AgentTask]] = []
    seen_ids: set[str] = set()
    for request in requests:
        task_id = _required_text(request.get("task_id"), "task_id")
        task = _require_present(state.tasks, task_id, "task")
        if task.owner_agent_id != parent.id:
            raise ValueError(f"agent {parent.id} cannot spawn ephemeral work for task {task_id}")
        if not agent_has_project_assignment(state, parent.id, task.project_id):
            raise ValueError(f"agent {parent.id} is not assigned to project {task.project_id}")
        assignment_id = _optional_text(request.get("id")) or f"ephemeral-{uuid4().hex}"
        if assignment_id in seen_ids:
            raise ValueError(f"duplicate ephemeral assignment id {assignment_id}")
        _require_absent(state.ephemeral_assignments, assignment_id, "ephemeral assignment")
        request["id"] = assignment_id
        seen_ids.add(assignment_id)
        normalized.append((request, task))

    now = utc_now()
    for request, task in normalized:
        assignment_id = _required_text(request.get("id"), "id")
        session_id = _optional_text(request.get("session_id")) or f"team-eph-{uuid4().hex}"
        assignment = EphemeralAssignment(
            id=assignment_id,
            parent_agent_id=parent.id,
            project_id=task.project_id,
            task_id=task.id,
            session_id=session_id,
            title=_required_text(request.get("title"), "title"),
            prompt=_required_text(request.get("prompt"), "prompt"),
            state=EphemeralAssignmentState.REQUESTED,
            model=_optional_text(request.get("model")),
            agent_provider=_optional_text(request.get("agent_provider")),
            execution_backend=(
                _optional_text(request.get("execution_backend")) or "container_worker_vm"
            ),
            container_image=_optional_text(request.get("container_image")),
            allowed_tools=_string_list(request.get("allowed_tools"), "allowed_tools"),
            ttl_hours=int(request["ttl_hours"]) if request.get("ttl_hours") is not None else None,
            directory_path=_optional_text(request.get("directory_path")),
            metadata=_optional_mapping(request.get("metadata"), "metadata") or {},
            created_at=now,
            updated_at=now,
        )
        state.ephemeral_assignments[assignment.id] = assignment
        result["created_ephemeral_assignment_ids"].append(assignment.id)


def _active_ephemeral_assignments_for_parent(
    state: TeamState,
    parent_agent_id: str,
) -> list[EphemeralAssignment]:
    active_states = {
        EphemeralAssignmentState.REQUESTED,
        EphemeralAssignmentState.SPAWN_PENDING,
        EphemeralAssignmentState.RUNNING,
    }
    return [
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.parent_agent_id == parent_agent_id and assignment.state in active_states
    ]


def _resolve_ephemeral_assignment(
    state: TeamState,
    *,
    action: dict[str, Any],
    session_id: str | None,
) -> EphemeralAssignment:
    assignment_id = _optional_text(action.get("ephemeral_assignment_id")) or _optional_text(
        action.get("assignment_id")
    )
    if assignment_id:
        return _require_present(
            state.ephemeral_assignments,
            assignment_id,
            "ephemeral assignment",
        )
    if session_id:
        for assignment in state.ephemeral_assignments.values():
            if assignment.session_id == session_id:
                return assignment
    raise ValueError("ephemeral action requires assignment_id or a known session_id")


def _apply_ephemeral_acceptance(
    state: TeamState,
    *,
    assignment: EphemeralAssignment,
    accepted: bool,
    deliverable_id: str | None,
    rejection_reason: str | None,
) -> str | None:
    if accepted:
        if assignment.state not in {
            EphemeralAssignmentState.COMPLETED,
            EphemeralAssignmentState.ACCEPTED,
            EphemeralAssignmentState.CLEANUP_PENDING,
            EphemeralAssignmentState.CLEANED_UP,
        }:
            raise ValueError(f"ephemeral assignment {assignment.id} is not completed")
        metadata = dict(assignment.metadata)
        existing_deliverable_id = _optional_text(metadata.get("accepted_deliverable_id"))
        if existing_deliverable_id is not None:
            if deliverable_id is not None and deliverable_id != existing_deliverable_id:
                raise ValueError(
                    f"ephemeral assignment {assignment.id} was already accepted "
                    f"as deliverable {existing_deliverable_id}"
                )
            _require_present(state.deliverables, existing_deliverable_id, "deliverable")
            assignment.state = EphemeralAssignmentState.ACCEPTED
            assignment.error = None
            assignment.updated_at = utc_now()
            return existing_deliverable_id
        deliverable_id_value = deliverable_id or f"deliverable-{uuid4().hex}"
        _require_absent(state.deliverables, deliverable_id_value, "deliverable")
        task = _require_present(state.tasks, assignment.task_id, "task")
        deliverable = Deliverable(
            id=deliverable_id_value,
            task_id=assignment.task_id,
            producer_agent_id=assignment.parent_agent_id,
            summary=_required_text(assignment.summary, "summary"),
            artifact_refs=list(assignment.artifact_refs),
            state=TeamRecordState.PENDING,
        )
        state.deliverables[deliverable.id] = deliverable
        task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
        now = utc_now()
        _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        task.updated_at = now
        assignment.state = EphemeralAssignmentState.ACCEPTED
        assignment.error = None
        metadata["accepted_deliverable_id"] = deliverable.id
        assignment.metadata = metadata
        assignment.updated_at = utc_now()
        return deliverable.id

    assignment.state = EphemeralAssignmentState.REJECTED
    assignment.error = rejection_reason
    assignment.updated_at = utc_now()
    return None


def _create_ephemeral_parent_notice(
    state: TeamState,
    assignment: EphemeralAssignment,
) -> str:
    inbox_id = f"inbox-{uuid4().hex}"
    state.inbox[inbox_id] = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=assignment.parent_agent_id,
        sender_agent_id=None,
        task_id=assignment.task_id,
        item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
        response_requested=True,
        priority=TeamInboxPriority.LOW,
        payload={
            "ephemeral_assignment_id": assignment.id,
            "summary": assignment.summary,
            "artifact_refs": list(assignment.artifact_refs),
        },
    )
    return inbox_id


def _ephemeral_assignment_event_payload(
    state: TeamState,
    assignment_id: str,
    *,
    event_type: str = EPHEMERAL_REQUESTED_EVENT_TYPE,
) -> dict[str, Any]:
    assignment = _require_present(
        state.ephemeral_assignments,
        assignment_id,
        "ephemeral assignment",
    )
    parent = _require_present(state.agents, assignment.parent_agent_id, "parent agent")
    task = _require_present(state.tasks, assignment.task_id, "task")
    metadata = dict(assignment.metadata)
    team_metadata = (
        dict(metadata.get("team_addon")) if isinstance(metadata.get("team_addon"), dict) else {}
    )
    team_metadata.update(
        {
            "ephemeral_assignment_id": assignment.id,
            "parent_agent_id": assignment.parent_agent_id,
            "team_id": parent.team_id,
            "project_id": assignment.project_id,
            "task_id": assignment.task_id,
            "ephemeral": True,
        }
    )
    metadata["team_addon"] = team_metadata
    assignment_payload = assignment.model_dump(mode="json")
    return {
        "event_type": event_type,
        "correlation_key": assignment.id,
        "idempotency_key": f"team-addon:{event_type}:{assignment.id}",
        "payload": {
            "assignment": assignment_payload,
            "assignment_id": assignment.id,
            "parent_agent_id": assignment.parent_agent_id,
            "project_id": assignment.project_id,
            "task_id": assignment.task_id,
            "session_id": assignment.session_id,
            "title": assignment.title,
            "prompt": _build_ephemeral_prompt(
                assignment=assignment,
                parent=parent,
                task=task,
            ),
            "summary": assignment.summary,
            "artifact_refs": list(assignment.artifact_refs),
            "model": assignment.model,
            "agent_provider": assignment.agent_provider,
            "execution_backend": assignment.execution_backend or "container_worker_vm",
            "container_image": assignment.container_image,
            "allowed_tools": [EPHEMERAL_WORKER_REPORT_TOOL],
            "agent_branch": (
                assignment.agent_branch
                or ephemeral_agent_branch(assignment.session_id, assignment.id)
            ),
            "ttl_hours": assignment.ttl_hours,
            "directory_path": assignment.directory_path,
            "metadata": metadata,
        },
    }


def _build_ephemeral_prompt(
    *,
    assignment: EphemeralAssignment,
    parent: PersistentAgent,
    task: AgentTask,
) -> str:
    context = {
        "assignment": assignment.model_dump(mode="json"),
        "parent_agent": parent.model_dump(
            mode="json",
            exclude=AGENT_OPERATOR_ONLY_FIELDS,
        ),
        "task": task.model_dump(mode="json"),
        "report_tool_name": EPHEMERAL_WORKER_REPORT_TOOL,
        "expected_agent_branch": ephemeral_agent_branch(
            assignment.session_id,
            assignment.id,
        ),
    }
    schema = {
        "assignment_id": assignment.id,
        "summary": "",
        "artifact_refs": [],
        "agent_branch": ephemeral_agent_branch(assignment.session_id, assignment.id),
        "commit_sha": None,
        "error": None,
        "request_id": "stable-result-id",
    }
    return (
        "Complete this scoped ephemeral team-agent assignment. "
        "You are a disposable worker for one parent agent; do not spawn other agents, "
        "ask hierarchy questions, or update unrelated team state. The only Team MCP "
        f"tool granted to this worker is {EPHEMERAL_WORKER_REPORT_TOOL}.\n\n"
        f"Assignment prompt:\n{assignment.prompt}\n\n"
        f"Assignment context JSON:\n{_json_dumps(context)}\n\n"
        f"Before finishing, call {EPHEMERAL_WORKER_REPORT_TOOL}. "
        "For successful work include summary, artifact_refs, request_id, and "
        "agent_branch when you created a branch. For failed work include error "
        "and request_id. Use this argument shape:\n"
        f"{_json_dumps(schema)}\n"
    )


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _bool_from_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, indent=2)


__all__ = [name for name in globals() if name != "annotations" and not name.startswith("__")]
