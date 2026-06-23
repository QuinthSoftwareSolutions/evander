from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from agent_sandbox.models import utc_now

from .git_naming import (
    canonical_release_branch,
    ephemeral_agent_branch,
    feature_branch,
    release_branch,
    review_branch,
    validate_branch_name,
)
from .models import (
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
    Deliverable,
    DeliverableGitRefs,
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
    TeamConversation,
    TeamConversationMessage,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamInboxItemType,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
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
from .sqlite_store import LEGACY_STATE_FILE, STATE_DB_FILE, TeamStateSqliteStore
from .templates import TeamTemplate, get_template

ADDON_ID = "team-addon"
STATE_FILE = STATE_DB_FILE
EPHEMERAL_REQUESTED_EVENT_TYPE = "team.ephemeral.requested"
EPHEMERAL_COMPLETED_EVENT_TYPE = "team.ephemeral.completed"
OPERATOR_ESCALATION_EVENT_TYPE = "team.operator.escalation_requested"
GIT_PROMOTION_EVENT_TYPE = "team.git.promotion_requested"
MISSING_ACTION_RECOVERY_MARKER = "missing action artifact recovery prompt dispatched"

_NON_TERMINAL_TASK_STATES = frozenset(
    {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
    }
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
    task_state: TeamTaskState
    parent_task_id: str | None
    dependency_task_ids: list[str]
    priority: int
    title: str
    description: str


class TeamStateConflictError(RuntimeError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"stale team add-on state revision: expected {expected_revision}, "
            f"current {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class TeamStateRepository:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.store = TeamStateSqliteStore(self.runtime_dir)

    def read(self) -> TeamState:
        return TeamState.model_validate(self.store.read_object())

    def update(
        self,
        mutator: Callable[[TeamState], TeamState | None],
        *,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(payload: dict[str, Any]) -> TeamState:
            state = TeamState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            now = utc_now()
            next_state = next_state.model_copy(
                update={"revision": state.revision + 1, "updated_at": now},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            return validated

        return self.store.update_object(_update)  # type: ignore[return-value]

    def dismiss_action_record_attention(
        self,
        *,
        action_record_id: str,
        dismissed_by: str | None = None,
        dismissed_reason: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            record = _require_present(
                state.action_records,
                _required_text(action_record_id, "action_record_id"),
                "action record",
            )
            if record.success:
                raise ValueError(
                    f"action record {record.id} is successful and does not need dismissal"
                )
            record.dismissed_at = utc_now()
            record.dismissed_by = _optional_text(dismissed_by) or "operator"
            record.dismissed_reason = _optional_text(dismissed_reason)
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def mark_action_feedback_delivered(
        self,
        *,
        agent_id: str,
        source_dispatch_id: str,
        delivery_dispatch_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_source_dispatch_id = _required_text(
            source_dispatch_id,
            "source_dispatch_id",
        )
        normalized_delivery_dispatch_id = _required_text(
            delivery_dispatch_id,
            "delivery_dispatch_id",
        )

        def _mutate(state: TeamState) -> TeamState:
            delivered_at = utc_now()
            for record in state.action_records.values():
                if record.agent_id != normalized_agent_id:
                    continue
                if record.dispatch_id != normalized_source_dispatch_id:
                    continue
                if record.success or record.feedback_delivered_at is not None:
                    continue
                record.feedback_delivered_at = delivered_at
                record.feedback_delivered_by_dispatch_id = normalized_delivery_dispatch_id
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_team(
        self,
        *,
        team_id: str | None,
        name: str,
        manager_agent_id: str | None,
        manager_session_id: str,
        manager_display_name: str | None = None,
        manager_role_id: str | None = None,
        manager_role_name: str | None = None,
        operator_communication_target_id: str | None = None,
        max_cost_usd: object | None = None,
        escalate_to_governance: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_team_id = _optional_text(team_id)
        normalized_name = _required_text(name, "name")
        normalized_manager_agent_id = _optional_text(manager_agent_id)
        normalized_manager_session_id = _required_text(manager_session_id, "manager_session_id")
        normalized_manager_display_name = _optional_text(manager_display_name)
        normalized_manager_role_id = _optional_text(manager_role_id)
        normalized_manager_role_name = _optional_text(manager_role_name) or "Manager"
        normalized_operator_target = _optional_text(operator_communication_target_id)

        def _mutate(state: TeamState) -> TeamState:
            effective_team_id = normalized_team_id or _generated_id(
                prefix="team",
                labels=[normalized_name],
                existing_ids=state.teams,
            )
            _require_absent(state.teams, effective_team_id, "team")
            role_base_id = _default_role_id(
                team_id=effective_team_id,
                project_id=None,
                role_key="manager",
            )
            role_id = normalized_manager_role_id or _unique_id_from_base(
                role_base_id,
                existing_ids=state.roles,
            )
            _require_absent(state.roles, role_id, "role")
            effective_manager_agent_id = normalized_manager_agent_id or _generated_id(
                prefix="agent",
                labels=[
                    effective_team_id,
                    normalized_manager_display_name or normalized_manager_role_name,
                ],
                existing_ids=state.agents,
            )
            existing_manager = state.agents.get(effective_manager_agent_id)
            if existing_manager is not None and existing_manager.team_id is not None:
                raise ValueError(
                    f"agent {effective_manager_agent_id} already belongs to team "
                    f"{existing_manager.team_id}"
                )
            now = utc_now()
            role = TeamRole(
                id=role_id,
                team_id=effective_team_id,
                name=normalized_manager_role_name,
                permissions=[RolePermission.CREATE_ROOT_TASK],
                created_at=now,
                updated_at=now,
            )
            state.roles[role.id] = role
            if existing_manager is not None:
                manager = existing_manager
                manager.team_id = effective_team_id
                manager.display_name = normalized_manager_display_name or manager.display_name
                manager.role_id = role.id
                manager.session_id = normalized_manager_session_id
                manager.reports_to_agent_id = None
                manager.subordinate_agent_ids = []
                manager.ephemeral_policy = role.ephemeral_policy.model_copy(deep=True)
            else:
                manager = PersistentAgent(
                    id=effective_manager_agent_id,
                    team_id=effective_team_id,
                    display_name=normalized_manager_display_name,
                    role_id=role.id,
                    session_id=normalized_manager_session_id,
                    reports_to_agent_id=None,
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                )
                state.agents[manager.id] = manager
            state.teams[effective_team_id] = Team(
                id=effective_team_id,
                name=normalized_name,
                manager_agent_id=manager.id,
                operator_communication_target_id=normalized_operator_target,
                max_cost_usd=max_cost_usd,
                escalate_to_governance=escalate_to_governance,
                created_at=now,
                updated_at=now,
            )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_team(
        self,
        *,
        team_id: str,
        name: str | None = None,
        manager_agent_id: str | None = None,
        operator_communication_target_id: str | None = None,
        set_operator_communication_target_id: bool = False,
        max_cost_usd: object | None = None,
        set_max_cost_usd: bool = False,
        escalate_to_governance: bool | None = None,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_manager_agent_id = _optional_text(manager_agent_id)

        def _superior_depth(
            state: TeamState,
            *,
            agent_id: str,
            superior_id: str,
        ) -> int | None:
            depth = 0
            current_id = state.agents.get(agent_id).reports_to_agent_id
            seen: set[str] = set()
            while current_id is not None:
                if current_id in seen:
                    return None
                seen.add(current_id)
                depth += 1
                if current_id == superior_id:
                    return depth
                current = state.agents.get(current_id)
                if current is None:
                    return None
                current_id = current.reports_to_agent_id
            return None

        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, team_id, "team")
            if (
                normalized_manager_agent_id is not None
                and normalized_manager_agent_id != team.manager_agent_id
            ):
                old_manager = _require_present(
                    state.agents,
                    team.manager_agent_id,
                    "current manager",
                )
                new_manager = _require_present(
                    state.agents,
                    normalized_manager_agent_id,
                    "manager agent",
                )
                if new_manager.team_id != team.id:
                    raise ValueError(
                        f"manager agent {new_manager.id} is not in team {team.id}"
                    )
                if new_manager.status != AgentStatus.ACTIVE:
                    raise ValueError(f"manager agent {new_manager.id} is not active")
                new_under_old_depth = _superior_depth(
                    state,
                    agent_id=new_manager.id,
                    superior_id=old_manager.id,
                )
                if new_under_old_depth is not None and new_under_old_depth > 1:
                    raise ValueError(
                        f"manager agent {new_manager.id} is nested under current "
                        f"manager {old_manager.id}; reassign its current superior first"
                    )
                if (
                    _superior_depth(
                        state,
                        agent_id=old_manager.id,
                        superior_id=new_manager.id,
                    )
                    is not None
                ):
                    raise ValueError(
                        f"manager agent {new_manager.id} would create a reporting cycle"
                    )

                previous_superior_id = new_manager.reports_to_agent_id
                if previous_superior_id is not None and previous_superior_id in state.agents:
                    previous_superior = state.agents[previous_superior_id]
                    previous_superior.subordinate_agent_ids = _remove_value(
                        previous_superior.subordinate_agent_ids,
                        new_manager.id,
                    )

                moved_report_ids = [
                    report_id
                    for report_id in old_manager.subordinate_agent_ids
                    if report_id != new_manager.id
                ]
                for report_id in moved_report_ids:
                    report = state.agents.get(report_id)
                    if report is None or report.team_id != team.id:
                        continue
                    report.reports_to_agent_id = new_manager.id

                old_manager.subordinate_agent_ids = []
                new_manager.reports_to_agent_id = None
                for report_id in moved_report_ids:
                    new_manager.subordinate_agent_ids = _append_unique(
                        new_manager.subordinate_agent_ids,
                        report_id,
                    )
                role = (
                    state.roles.get(new_manager.role_id)
                    if new_manager.role_id is not None
                    else None
                )
                if role is not None and RolePermission.CREATE_ROOT_TASK not in role.permissions:
                    role.permissions.append(RolePermission.CREATE_ROOT_TASK)
                    role.updated_at = utc_now()
                team.manager_agent_id = new_manager.id
            if name is not None:
                team.name = name
            if set_operator_communication_target_id:
                team.operator_communication_target_id = operator_communication_target_id
            if set_max_cost_usd:
                team.max_cost_usd = _optional_cost_limit(max_cost_usd)
            if escalate_to_governance is not None:
                team.escalate_to_governance = bool(escalate_to_governance)
            if archived is not None:
                team.archived = archived
                if archived:
                    for agent in state.agents.values():
                        if agent.team_id == team_id:
                            agent.status = AgentStatus.ARCHIVED
            team.updated_at = utc_now()
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_governance_group(
        self,
        *,
        governance_group_id: str | None,
        name: str,
        manager_agent_id: str,
        member_team_ids: list[str] | None = None,
        goals: list[GovernanceGoal | Mapping[str, Any]] | None = None,
        max_cost_usd: object | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_group_id = _optional_text(governance_group_id)
        normalized_name = _required_text(name, "name")
        normalized_manager_agent_id = _required_text(manager_agent_id, "manager_agent_id")
        normalized_member_team_ids = _unique_text_list(member_team_ids or [], "member_team_ids")
        goal_inputs = list(goals or [])

        def _mutate(state: TeamState) -> TeamState:
            effective_group_id = normalized_group_id or _generated_id(
                prefix="governance",
                labels=[normalized_name],
                existing_ids=state.governance_groups,
            )
            _require_absent(state.governance_groups, effective_group_id, "governance group")
            _require_present(state.agents, normalized_manager_agent_id, "governance manager")
            for team_id in normalized_member_team_ids:
                _require_present(state.teams, team_id, "member team")
            now = utc_now()
            state.governance_groups[effective_group_id] = GovernanceGroup(
                id=effective_group_id,
                name=normalized_name,
                manager_agent_id=normalized_manager_agent_id,
                member_team_ids=normalized_member_team_ids,
                goals=_governance_goals_with_generated_ids(
                    goal_inputs,
                    governance_group_id=effective_group_id,
                ),
                max_cost_usd=max_cost_usd,
                created_at=now,
                updated_at=now,
            )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_governance_group(
        self,
        *,
        governance_group_id: str,
        name: str | None = None,
        manager_agent_id: str | None = None,
        member_team_ids: list[str] | None = None,
        set_member_team_ids: bool = False,
        goals: list[GovernanceGoal] | None = None,
        max_cost_usd: object | None = None,
        set_max_cost_usd: bool = False,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")

        def _mutate(state: TeamState) -> TeamState:
            group = _require_present(
                state.governance_groups,
                normalized_group_id,
                "governance group",
            )
            if name is not None:
                group.name = name
            if manager_agent_id is not None:
                _require_present(state.agents, manager_agent_id, "governance manager")
                group.manager_agent_id = manager_agent_id
            if archived is not None:
                group.archived = archived
            if set_member_team_ids:
                previous_member_team_ids = set(group.member_team_ids)
                normalized_member_team_ids = _unique_text_list(
                    member_team_ids or [],
                    "member_team_ids",
                )
                for team_id in normalized_member_team_ids:
                    _require_present(state.teams, team_id, "member team")
                group.member_team_ids = normalized_member_team_ids
                added_team_ids = set(normalized_member_team_ids) - previous_member_team_ids
                removed_team_ids = previous_member_team_ids - set(normalized_member_team_ids)
                membership_changed_at = utc_now()
                if removed_team_ids:
                    for item in state.governance_agenda.values():
                        if (
                            item.governance_group_id != group.id
                            or item.state == GovernanceAgendaItemState.HANDLED
                        ):
                            continue
                        item_team_id = _governance_agenda_effective_team_id(state, item)
                        if item_team_id in removed_team_ids:
                            item.state = GovernanceAgendaItemState.HANDLED
                            item.updated_at = membership_changed_at
                _record_governance_membership_change_agenda(
                    state,
                    group=group,
                    previous_member_team_ids=sorted(previous_member_team_ids),
                    added_team_ids=sorted(added_team_ids),
                    removed_team_ids=sorted(removed_team_ids),
                    now=membership_changed_at,
                )
            if goals is not None:
                group.goals = goals
            if set_max_cost_usd:
                group.max_cost_usd = _optional_cost_limit(max_cost_usd)
            group.updated_at = utc_now()
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_governance_agenda_item(
        self,
        *,
        agenda_item_id: str | None,
        governance_group_id: str,
        item_type: GovernanceAgendaItemType | str,
        title: str,
        summary: str,
        team_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_item_id = _optional_text(agenda_item_id)
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")
        normalized_title = _required_text(title, "title")
        normalized_summary = _required_text(summary, "summary")
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_item_type = (
            item_type
            if isinstance(item_type, GovernanceAgendaItemType)
            else GovernanceAgendaItemType(_required_text(item_type, "item_type"))
        )
        normalized_priority = (
            priority
            if isinstance(priority, TeamInboxPriority)
            else TeamInboxPriority(_required_text(priority, "priority"))
        )
        normalized_payload = dict(payload or {})

        def _mutate(state: TeamState) -> TeamState:
            group = _require_present(
                state.governance_groups,
                normalized_group_id,
                "governance group",
            )
            effective_team_id = normalized_team_id
            if normalized_project_id is not None:
                project = _require_present(state.projects, normalized_project_id, "project")
                if effective_team_id is not None and project.team_id != effective_team_id:
                    raise ValueError(
                        f"project {normalized_project_id} does not belong to team "
                        f"{effective_team_id}"
                    )
                effective_team_id = effective_team_id or project.team_id
            if normalized_agent_id is not None:
                agent = _require_present(state.agents, normalized_agent_id, "agent")
                if effective_team_id is not None and agent.team_id != effective_team_id:
                    raise ValueError(
                        f"agent {normalized_agent_id} does not belong to team "
                        f"{effective_team_id}"
                    )
                effective_team_id = effective_team_id or agent.team_id
            if effective_team_id is not None:
                _require_present(state.teams, effective_team_id, "team")
                if effective_team_id not in group.member_team_ids:
                    raise ValueError(
                        f"team {effective_team_id} is not in governance group {group.id}"
                    )
            effective_item_id = normalized_item_id or _generated_id(
                prefix="governance-agenda",
                labels=[normalized_group_id, normalized_item_type.value, normalized_title],
                existing_ids=state.governance_agenda,
            )
            _require_absent(state.governance_agenda, effective_item_id, "governance agenda item")
            now = utc_now()
            state.governance_agenda[effective_item_id] = GovernanceAgendaItem(
                id=effective_item_id,
                governance_group_id=normalized_group_id,
                item_type=normalized_item_type,
                title=normalized_title,
                summary=normalized_summary,
                team_id=effective_team_id,
                project_id=normalized_project_id,
                agent_id=normalized_agent_id,
                payload=normalized_payload,
                priority=normalized_priority,
                created_at=now,
                updated_at=now,
            )
            runtime = state.governance_runtimes.get(normalized_group_id)
            if runtime is not None:
                runtime.next_wake_at = None
                runtime.updated_at = now
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def mark_governance_agenda_handled(
        self,
        *,
        agenda_item_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_item_id = _required_text(agenda_item_id, "agenda_item_id")

        def _mutate(state: TeamState) -> TeamState:
            item = _require_present(
                state.governance_agenda,
                normalized_item_id,
                "governance agenda item",
            )
            item.state = GovernanceAgendaItemState.HANDLED
            item.updated_at = utc_now()
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def mark_governance_agenda_handled_by_session(
        self,
        *,
        agenda_item_id: str,
        requesting_session_id: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, GovernanceAgendaItem]:
        normalized_item_id = _required_text(agenda_item_id, "agenda_item_id")
        normalized_session_id = _required_text(
            requesting_session_id,
            "requesting_session_id",
        )

        def _mutate(state: TeamState) -> TeamState:
            item = _require_present(
                state.governance_agenda,
                normalized_item_id,
                "governance agenda item",
            )
            group = _require_present(
                state.governance_groups,
                item.governance_group_id,
                "governance group",
            )
            requester = _agent_for_session(state, session_id=normalized_session_id)
            if (
                requester.status != AgentStatus.ACTIVE
                or group.archived
                or group.manager_agent_id != requester.id
            ):
                raise ValueError(
                    "governance agenda handling requires the active governance "
                    "manager session for the agenda item's governance group"
                )
            item.state = GovernanceAgendaItemState.HANDLED
            item.updated_at = utc_now()
            return state

        state = self.update(_mutate, expected_revision=expected_revision)
        return state, state.governance_agenda[normalized_item_id]

    def create_project(
        self,
        *,
        project_id: str | None,
        team_id: str,
        name: str,
        lead_agent_id: str | None,
        lead_session_id: str | None,
        lead_display_name: str | None = None,
        goals: list[ProjectGoal | Mapping[str, Any]] | None = None,
        git: ProjectGitConfig | None = None,
        lead_role_id: str | None = None,
        lead_role_name: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_project_id = _optional_text(project_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_name = _required_text(name, "name")
        normalized_lead_agent_id = _optional_text(lead_agent_id)
        normalized_lead_session_id = _optional_text(lead_session_id)
        normalized_lead_display_name = _optional_text(lead_display_name)
        normalized_lead_role_id = _optional_text(lead_role_id)
        normalized_lead_role_name = _optional_text(lead_role_name) or "Project Lead"
        goal_inputs = list(goals or [])

        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, normalized_team_id, "team")
            effective_project_id = normalized_project_id or _generated_id(
                prefix="project",
                labels=[normalized_team_id, normalized_name],
                existing_ids=state.projects,
            )
            _require_absent(state.projects, effective_project_id, "project")
            role_base_id = _default_role_id(
                team_id=normalized_team_id,
                project_id=effective_project_id,
                role_key="project_lead",
            )
            role_id = normalized_lead_role_id or _unique_id_from_base(
                role_base_id,
                existing_ids=state.roles,
            )
            _require_absent(state.roles, role_id, "role")
            effective_lead_agent_id = normalized_lead_agent_id or _generated_id(
                prefix="agent",
                labels=[
                    effective_project_id,
                    normalized_lead_display_name or normalized_lead_role_name,
                ],
                existing_ids=state.agents,
            )
            existing_lead = state.agents.get(effective_lead_agent_id)
            if existing_lead is not None:
                if existing_lead.team_id != normalized_team_id:
                    raise ValueError(
                        f"agent {effective_lead_agent_id} belongs to team "
                        f"{existing_lead.team_id}"
                    )
            elif normalized_lead_agent_id and normalized_lead_session_id is None:
                raise ValueError(f"agent {effective_lead_agent_id} does not exist")
            elif normalized_lead_session_id is None:
                raise ValueError("lead_session_id is required when creating a new project lead agent")
            project_goals = _project_goals_with_generated_ids(
                goal_inputs,
                project_id=effective_project_id,
            )
            now = utc_now()
            role = TeamRole(
                id=role_id,
                team_id=normalized_team_id,
                project_id=effective_project_id,
                name=normalized_lead_role_name,
                permissions=[RolePermission.CREATE_ROOT_TASK],
                created_at=now,
                updated_at=now,
            )
            state.roles[role.id] = role
            if existing_lead is not None:
                lead = existing_lead
            else:
                lead = PersistentAgent(
                    id=effective_lead_agent_id,
                    team_id=normalized_team_id,
                    display_name=normalized_lead_display_name,
                    role_id=role.id,
                    session_id=normalized_lead_session_id,
                    reports_to_agent_id=team.manager_agent_id,
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                )
                state.agents[lead.id] = lead
                manager = state.agents[team.manager_agent_id]
                manager.subordinate_agent_ids = _append_unique(
                    manager.subordinate_agent_ids,
                    lead.id,
                )
            assignment_id = _project_assignment_id(
                effective_project_id,
                lead.id,
                AgentProjectAssignmentKind.PROJECT_LEAD,
                existing_ids=state.project_assignments,
            )
            state.project_assignments[assignment_id] = AgentProjectAssignment(
                id=assignment_id,
                team_id=normalized_team_id,
                project_id=effective_project_id,
                agent_id=lead.id,
                role_id=role.id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD,
                created_at=now,
                updated_at=now,
            )
            state.projects[effective_project_id] = Project(
                id=effective_project_id,
                team_id=normalized_team_id,
                name=normalized_name,
                lead_agent_id=lead.id,
                goals=project_goals,
                git=git,
                created_at=now,
                updated_at=now,
            )
            team.project_ids = _append_unique(team.project_ids, effective_project_id)
            team.updated_at = now
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_project(
        self,
        *,
        project_id: str,
        name: str | None = None,
        git: ProjectGitConfig | None = None,
        set_git: bool = False,
        goals: list[ProjectGoal] | None = None,
        status: ProjectStatus | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            project = _require_present(state.projects, project_id, "project")
            if name is not None:
                project.name = name
            if set_git:
                project.git = git
            if goals is not None:
                project.goals = goals
            if status is not None:
                project.status = status
            project.updated_at = utc_now()
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_role(
        self,
        *,
        role_id: str | None,
        team_id: str,
        name: str,
        project_id: str | None = None,
        description: str | None = None,
        permissions: list[RolePermission | str] | None = None,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_role_id = _optional_text(role_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_name = _required_text(name, "name")
        normalized_description = _optional_text(description) or ""
        normalized_project_id = _optional_text(project_id)

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.teams, normalized_team_id, "team")
            if normalized_project_id is not None:
                project = _require_present(state.projects, normalized_project_id, "project")
                if project.team_id != normalized_team_id:
                    raise ValueError(
                        f"project {normalized_project_id} does not belong to team {normalized_team_id}"
                    )
            effective_role_id = normalized_role_id or _generated_id(
                prefix="role",
                labels=[normalized_project_id or normalized_team_id, normalized_name],
                existing_ids=state.roles,
            )
            _require_absent(state.roles, effective_role_id, "role")
            now = utc_now()
            role = TeamRole(
                id=effective_role_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                name=normalized_name,
                description=normalized_description,
                permissions=_normalize_role_permissions(permissions),
                ephemeral_policy=ephemeral_policy or EphemeralAgentPolicy(),
                created_at=now,
                updated_at=now,
            )
            state.roles[role.id] = role
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_role(
        self,
        *,
        role_id: str,
        name: str | None = None,
        project_id: str | None = None,
        set_project_id: bool = False,
        description: str | None = None,
        set_description: bool = False,
        permissions: list[RolePermission | str] | None = None,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            role = _require_present(state.roles, role_id, "role")
            if name is not None:
                role.name = name
            if set_project_id:
                normalized_project_id = _optional_text(project_id)
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != role.team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team {role.team_id}"
                        )
                role.project_id = normalized_project_id
            if set_description:
                role.description = description or ""
            if permissions is not None:
                role.permissions = _normalize_role_permissions(permissions)
            if ephemeral_policy is not None:
                role.ephemeral_policy = ephemeral_policy
            if archived is not None:
                role.archived = archived
            role.updated_at = utc_now()
            for agent in state.agents.values():
                if agent.role_id == role.id:
                    _validate_role_assignment(
                        role,
                        team_id=agent.team_id,
                        project_ids=agent_project_ids(state, agent.id),
                    )
            for profile in state.review_profiles.values():
                if role.id in profile.applicable_role_ids:
                    _validate_role_filter_assignment(role, profile=profile)
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def register_agent(
        self,
        *,
        agent_id: str | None,
        session_id: str,
        team_id: str | None = None,
        project_id: str | None = None,
        role_id: str | None = None,
        reports_to_agent_id: str | None = None,
        display_name: str | None = None,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _optional_text(agent_id)
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_role_id = _optional_text(role_id)
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_reports_to_agent_id = _optional_text(reports_to_agent_id)
        normalized_display_name = _optional_text(display_name)

        def _mutate(state: TeamState) -> TeamState:
            generated_agent_labels = (
                [
                    normalized_project_id or normalized_team_id,
                    normalized_display_name or normalized_role_id or normalized_session_id,
                ]
                if normalized_team_id is not None
                else [normalized_display_name or normalized_session_id]
            )
            effective_agent_id = normalized_agent_id or _generated_id(
                prefix="agent",
                labels=generated_agent_labels,
                existing_ids=state.agents,
            )
            _require_absent(state.agents, effective_agent_id, "agent")
            role: TeamRole | None = None
            if normalized_team_id is None:
                if normalized_project_id is not None:
                    raise ValueError("project_id requires team_id")
                if normalized_role_id is not None:
                    raise ValueError("role_id requires team_id")
                if normalized_reports_to_agent_id is not None:
                    raise ValueError("reports_to_agent_id requires team_id")
            else:
                _require_present(state.teams, normalized_team_id, "team")
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != normalized_team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team "
                            f"{normalized_team_id}"
                        )
                if normalized_role_id is None:
                    raise ValueError("role_id is required when registering an agent to a team")
                role = _require_present(state.roles, normalized_role_id, "role")
                _validate_role_assignment(
                    role,
                    team_id=normalized_team_id,
                    project_ids=[normalized_project_id] if normalized_project_id is not None else [],
                )
                if normalized_reports_to_agent_id is not None:
                    superior = _require_present(
                        state.agents,
                        normalized_reports_to_agent_id,
                        "superior agent",
                    )
                    if superior.team_id != normalized_team_id:
                        raise ValueError(
                            f"superior agent {normalized_reports_to_agent_id} is not in team "
                            f"{normalized_team_id}"
                        )
            agent = PersistentAgent(
                id=effective_agent_id,
                team_id=normalized_team_id,
                display_name=normalized_display_name,
                role_id=normalized_role_id,
                session_id=normalized_session_id,
                reports_to_agent_id=normalized_reports_to_agent_id,
                ephemeral_policy=(
                    ephemeral_policy.model_copy(deep=True)
                    if ephemeral_policy is not None
                    else (
                        role.ephemeral_policy.model_copy(deep=True)
                        if role is not None
                        else EphemeralAgentPolicy()
                    )
                ),
            )
            state.agents[agent.id] = agent
            if normalized_project_id is not None and normalized_team_id is not None:
                assignment_id = _project_assignment_id(
                    normalized_project_id,
                    agent.id,
                    AgentProjectAssignmentKind.PROJECT_MEMBER,
                    existing_ids=state.project_assignments,
                )
                state.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=normalized_team_id,
                    project_id=normalized_project_id,
                    agent_id=agent.id,
                    role_id=normalized_role_id,
                    kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                )
            if normalized_reports_to_agent_id and normalized_reports_to_agent_id in state.agents:
                superior = state.agents[normalized_reports_to_agent_id]
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids,
                    agent.id,
                )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_agent(
        self,
        *,
        agent_id: str,
        team_id: str | None = None,
        set_team_id: bool = False,
        project_id: str | None = None,
        set_project_id: bool = False,
        role_id: str | None = None,
        set_role_id: bool = False,
        session_id: str | None = None,
        reports_to_agent_id: str | None = None,
        set_reports_to_agent_id: bool = False,
        display_name: str | None = None,
        set_display_name: bool = False,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        status: AgentStatus | None = None,
        voice_profile: AgentVoiceProfile | None = None,
        set_voice_profile: bool = False,
        context_clearing_policy: AgentContextClearingPolicy | str | None = None,
        set_context_clearing_policy: bool = False,
        auto_context_clearing_enabled: bool | None = None,
        set_auto_context_clearing_enabled: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_role_id = _optional_text(role_id)
        normalized_reports_to_agent_id = _optional_text(reports_to_agent_id)
        normalized_context_clearing_policy = _resolved_context_clearing_policy(
            context_clearing_policy=context_clearing_policy,
            set_context_clearing_policy=set_context_clearing_policy,
            auto_context_clearing_enabled=auto_context_clearing_enabled,
            set_auto_context_clearing_enabled=set_auto_context_clearing_enabled,
        )

        def _mutate(state: TeamState) -> TeamState:
            now = utc_now()
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            detached_from_team = False
            if set_team_id:
                if normalized_team_id is None:
                    blockers = _agent_team_removal_blockers(state, agent)
                    if blockers:
                        raise ValueError(
                            f"agent {agent.id} cannot be removed from team: "
                            + "; ".join(blockers)
                        )
                    old_superior_id = agent.reports_to_agent_id
                    if old_superior_id and old_superior_id in state.agents:
                        old_superior = state.agents[old_superior_id]
                        old_superior.subordinate_agent_ids = _remove_value(
                            old_superior.subordinate_agent_ids,
                            agent.id,
                        )
                    for assignment_id in [
                        assignment.id
                        for assignment in state.project_assignments.values()
                        if assignment.agent_id == agent.id
                    ]:
                        state.project_assignments.pop(assignment_id, None)
                    agent.team_id = None
                    agent.role_id = None
                    agent.reports_to_agent_id = None
                    agent.subordinate_agent_ids = []
                    detached_from_team = True
                else:
                    _require_present(state.teams, normalized_team_id, "team")
                    agent.team_id = normalized_team_id
            if set_project_id and not detached_from_team:
                if agent.team_id is None:
                    raise ValueError("project_id cannot be set for a teamless agent")
                for assignment_id in [
                    assignment.id
                    for assignment in state.project_assignments.values()
                    if assignment.agent_id == agent.id
                    and assignment.kind == AgentProjectAssignmentKind.PROJECT_MEMBER
                ]:
                    state.project_assignments.pop(assignment_id, None)
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != agent.team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team {agent.team_id}"
                        )
                    assignment_id = _project_assignment_id(
                        normalized_project_id,
                        agent.id,
                        AgentProjectAssignmentKind.PROJECT_MEMBER,
                        existing_ids=state.project_assignments,
                    )
                    state.project_assignments[assignment_id] = AgentProjectAssignment(
                        id=assignment_id,
                        team_id=agent.team_id,
                        project_id=normalized_project_id,
                        agent_id=agent.id,
                        role_id=agent.role_id,
                        kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                    )
            if set_role_id and not detached_from_team:
                if normalized_role_id is not None:
                    if agent.team_id is None:
                        raise ValueError("role_id cannot be set for a teamless agent")
                    role = _require_present(state.roles, normalized_role_id, "role")
                    _validate_role_assignment(
                        role,
                        team_id=agent.team_id,
                        project_ids=agent_project_ids(state, agent.id),
                    )
                agent.role_id = normalized_role_id
                if role is not None and role.project_id is not None:
                    for assignment in state.project_assignments.values():
                        if assignment.agent_id == agent.id and assignment.project_id == role.project_id:
                            assignment.role_id = role.id
                            assignment.updated_at = now
            elif role_id is not None and not detached_from_team:
                role = _require_present(state.roles, normalized_role_id or role_id, "role")
                _validate_role_assignment(
                    role,
                    team_id=agent.team_id,
                    project_ids=agent_project_ids(state, agent.id),
                )
                agent.role_id = role.id
                if role.project_id is not None:
                    for assignment in state.project_assignments.values():
                        if assignment.agent_id == agent.id and assignment.project_id == role.project_id:
                            assignment.role_id = role.id
                            assignment.updated_at = now
            if session_id is not None:
                agent.session_id = session_id
            if set_display_name:
                agent.display_name = display_name
            if ephemeral_policy is not None:
                agent.ephemeral_policy = ephemeral_policy
            if status is not None:
                agent.status = status
            if set_voice_profile:
                agent.voice_profile = (
                    voice_profile
                    if voice_profile is not None and voice_profile.configured
                    else None
                )
            if normalized_context_clearing_policy is not None:
                runtime = state.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                _set_context_clearing_policy(
                    state,
                    agent=agent,
                    runtime=runtime,
                    policy=normalized_context_clearing_policy,
                    now=now,
                )
            if set_reports_to_agent_id and not detached_from_team:
                old_superior_id = agent.reports_to_agent_id
                if normalized_reports_to_agent_id is not None:
                    if agent.team_id is None:
                        raise ValueError("reports_to_agent_id cannot be set for a teamless agent")
                    superior = _require_present(
                        state.agents, normalized_reports_to_agent_id, "superior agent"
                    )
                    if superior.team_id != agent.team_id:
                        raise ValueError(
                            f"superior agent {normalized_reports_to_agent_id} is not in team {agent.team_id}"
                        )
                if old_superior_id and old_superior_id in state.agents:
                    old_superior = state.agents[old_superior_id]
                    old_superior.subordinate_agent_ids = _remove_value(
                        old_superior.subordinate_agent_ids, agent.id
                    )
                agent.reports_to_agent_id = normalized_reports_to_agent_id
                if normalized_reports_to_agent_id and normalized_reports_to_agent_id in state.agents:
                    superior = state.agents[normalized_reports_to_agent_id]
                    superior.subordinate_agent_ids = _append_unique(
                        superior.subordinate_agent_ids, agent.id
                    )
            if agent.team_id is None:
                if agent.role_id is not None:
                    raise ValueError("teamless agent cannot have a team-scoped role")
                if agent.reports_to_agent_id is not None:
                    raise ValueError("teamless agent cannot report to another agent")
                if agent.subordinate_agent_ids:
                    raise ValueError("teamless agent cannot have subordinate agents")
            else:
                if agent.role_id is None:
                    raise ValueError("role_id is required when assigning an agent to a team")
                role = _require_present(state.roles, agent.role_id, "role")
                _validate_role_assignment(
                    role,
                    team_id=agent.team_id,
                    project_ids=agent_project_ids(state, agent.id),
                )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_or_get_conversation(
        self,
        *,
        agent_id: str,
        operator_user_id: str | None = None,
        operator_display_name: str | None = None,
        created_by: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamConversation, bool]:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_operator_user_id = _normalize_operator_user_id(
            operator_user_id,
            created_by=created_by,
        )
        normalized_operator_display_name = _optional_text(operator_display_name)
        normalized_created_by = _optional_text(created_by)

        current_state = self.read()
        if expected_revision is not None and current_state.revision != expected_revision:
            raise TeamStateConflictError(
                expected_revision=expected_revision,
                current_revision=current_state.revision,
            )
        current_agent = _require_present(current_state.agents, normalized_agent_id, "agent")
        if current_agent.status == AgentStatus.ARCHIVED:
            raise ValueError(f"agent {current_agent.id} is archived")
        for conversation in current_state.conversations.values():
            if (
                conversation.agent_id == current_agent.id
                and conversation.operator_user_id == normalized_operator_user_id
            ):
                return current_state, conversation, False

        conversation_id: str | None = None
        created = False

        def _mutate(state: TeamState) -> TeamState:
            nonlocal conversation_id, created
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            for conversation in state.conversations.values():
                if (
                    conversation.agent_id == agent.id
                    and conversation.operator_user_id == normalized_operator_user_id
                ):
                    conversation_id = conversation.id
                    return state
            now = utc_now()
            conversation_id = _generated_id(
                prefix="conversation",
                labels=[agent.id, normalized_operator_user_id],
                existing_ids=state.conversations,
            )
            state.conversations[conversation_id] = TeamConversation(
                id=conversation_id,
                agent_id=agent.id,
                operator_user_id=normalized_operator_user_id,
                operator_display_name=normalized_operator_display_name,
                created_by=normalized_created_by,
                last_message_preview="",
                created_at=now,
                updated_at=now,
                last_interaction_at=now,
            )
            created = True
            return state

        state = self.update(_mutate, expected_revision=expected_revision)
        if conversation_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation was not resolved")
        return state, state.conversations[conversation_id], created

    def record_operator_chat_message(
        self,
        *,
        conversation_id: str,
        body: str,
        created_by: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_body = _required_text(body, "body")
        normalized_created_by = _optional_text(created_by)
        message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal message_id
            conversation = _require_present(
                state.conversations,
                normalized_conversation_id,
                "conversation",
            )
            agent = _require_present(state.agents, conversation.agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            now = utc_now()
            message_id = f"chatmsg-{uuid4().hex}"
            state.conversation_messages[message_id] = TeamConversationMessage(
                id=message_id,
                conversation_id=conversation.id,
                sender="operator",
                message_type="text",
                body=normalized_body,
                status="created",
                metadata={
                    key: value
                    for key, value in {
                        "sender_kind": "human_operator",
                        "operator_user_id": conversation.operator_user_id,
                        "operator_display_name": conversation.operator_display_name,
                        "created_by": normalized_created_by,
                    }.items()
                    if value
                },
                created_at=now,
                updated_at=now,
            )
            _touch_conversation(
                conversation,
                now=now,
                preview=_chat_preview(normalized_body),
            )
            return state

        state = self.update(_mutate, expected_revision=expected_revision)
        if message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message was not created")
        return state, state.conversation_messages[message_id]

    def begin_chat_message_dispatch(
        self,
        *,
        message_id: str,
        dispatch_id: str,
        lease_seconds: int = 172800,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_message_id = _required_text(message_id, "message_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        resolved_message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal resolved_message_id
            message = _require_present(
                state.conversation_messages,
                normalized_message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            agent = _require_present(state.agents, conversation.agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            runtime = state.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            if (
                runtime.active_dispatch_id is not None
                and runtime.active_dispatch_id != normalized_dispatch_id
            ):
                raise ValueError(
                    f"agent {agent.id} already has active Team dispatch "
                    f"{runtime.active_dispatch_id}"
                )
            now = utc_now()
            runtime.active_dispatch_id = normalized_dispatch_id
            runtime.wake_leased_until = now + timedelta(seconds=max(1, int(lease_seconds)))
            runtime.wake_reason = "team_chat"
            runtime.last_wake_at = now
            runtime.next_wake_at = None
            runtime.updated_at = now
            message.status = "running"
            message.dispatch_id = normalized_dispatch_id
            message.error = None
            message.updated_at = now
            conversation.updated_at = now
            resolved_message_id = message.id
            return state

        state = self.update(_mutate)
        if resolved_message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch was not started")
        return state, state.conversation_messages[resolved_message_id]

    def queue_chat_message_dispatch(
        self,
        *,
        message_id: str,
        dispatch_id: str,
        reason: str | None = None,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_message_id = _required_text(message_id, "message_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_reason = _optional_text(reason)
        resolved_message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal resolved_message_id
            message = _require_present(
                state.conversation_messages,
                normalized_message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            agent = _require_present(state.agents, conversation.agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            if message.sender != "operator":
                raise ValueError(f"message {message.id} is not an operator message")
            now = utc_now()
            metadata = dict(message.metadata)
            metadata["queued_at"] = now.isoformat()
            if normalized_reason is not None:
                metadata["queued_reason"] = normalized_reason
            message.status = "queued"
            message.dispatch_id = normalized_dispatch_id
            message.error = None
            message.metadata = metadata
            message.updated_at = now
            _touch_conversation(
                conversation,
                now=now,
                preview=f"Queued: {_chat_preview(message.body)}",
            )
            resolved_message_id = message.id
            return state

        state = self.update(_mutate)
        if resolved_message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch was not queued")
        return state, state.conversation_messages[resolved_message_id]

    def claim_pending_chat_message_dispatches(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 172800,
        team_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        blocked_team_ids: set[str] | None = None,
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_limit = max(0, int(limit))
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        blocked_teams = set(blocked_team_ids or set())
        claims: list[dict[str, Any]] = []
        if normalized_limit == 0:
            return self.read(), claims

        def _mutate(state: TeamState) -> TeamState:
            messages = sorted(
                state.conversation_messages.values(),
                key=lambda item: (item.created_at, item.id),
            )
            for message in messages:
                if normalized_limit and len(claims) >= normalized_limit:
                    break
                if (
                    message.sender != "operator"
                    or message.message_type != "text"
                    or message.status != "queued"
                ):
                    continue
                conversation = state.conversations.get(message.conversation_id)
                if conversation is None:
                    continue
                agent = state.agents.get(conversation.agent_id)
                if agent is None or agent.status == AgentStatus.ARCHIVED:
                    continue
                if normalized_team_id is not None and agent.team_id != normalized_team_id:
                    continue
                if agent.team_id in blocked_teams:
                    continue
                if normalized_project_id is not None and not agent_has_project_assignment(
                    state,
                    agent.id,
                    normalized_project_id,
                ):
                    continue
                if normalized_agent_id is not None and agent.id != normalized_agent_id:
                    continue
                runtime = state.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue
                dispatch_id = message.dispatch_id or f"team-chat:{message.id}"
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_leased_until = effective_now + timedelta(
                    seconds=max(1, int(lease_seconds))
                )
                runtime.wake_reason = "team_chat"
                runtime.last_wake_at = effective_now
                runtime.next_wake_at = None
                runtime.updated_at = effective_now
                message.status = "running"
                message.dispatch_id = dispatch_id
                message.error = None
                message.updated_at = effective_now
                conversation.updated_at = effective_now
                claims.append(
                    {
                        "agent_id": agent.id,
                        "session_id": agent.session_id,
                        "conversation_id": conversation.id,
                        "message_id": message.id,
                        "dispatch_id": dispatch_id,
                    }
                )
            return state

        state = self.update(_mutate)
        return state, claims

    def mark_chat_message_dispatch_failed(
        self,
        *,
        message_id: str,
        error: str,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_message_id = _required_text(message_id, "message_id")
        normalized_error = _required_text(error, "error")
        resolved_message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal resolved_message_id
            message = _require_present(
                state.conversation_messages,
                normalized_message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            runtime = state.agent_runtimes.get(conversation.agent_id)
            if (
                runtime is not None
                and message.dispatch_id is not None
                and runtime.active_dispatch_id == message.dispatch_id
            ):
                runtime.active_dispatch_id = None
                runtime.wake_leased_until = None
                runtime.wake_reason = None
                runtime.updated_at = utc_now()
            now = utc_now()
            message.status = "failed"
            message.error = normalized_error
            message.updated_at = now
            _touch_conversation(
                conversation,
                now=now,
                preview=f"Send failed: {_chat_preview(normalized_error)}",
            )
            resolved_message_id = message.id
            return state

        state = self.update(_mutate)
        if resolved_message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch failure was not recorded")
        return state, state.conversation_messages[resolved_message_id]

    def record_conversation_voice_call(
        self,
        *,
        conversation_id: str,
        voice_session_id: str,
        status: str = "running",
        summary: str | None = None,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_voice_session_id = _required_text(voice_session_id, "voice_session_id")
        normalized_summary = _optional_text(summary)
        normalized_status = _optional_text(status) or "running"
        message_id: str | None = None

        current_state = self.read()
        for message in current_state.conversation_messages.values():
            if message.voice_session_id == normalized_voice_session_id:
                return current_state, message

        def _mutate(state: TeamState) -> TeamState:
            nonlocal message_id
            for message in state.conversation_messages.values():
                if message.voice_session_id == normalized_voice_session_id:
                    message_id = message.id
                    return state
            conversation = _require_present(
                state.conversations,
                normalized_conversation_id,
                "conversation",
            )
            now = utc_now()
            message_id = f"chatmsg-{uuid4().hex}"
            body = normalized_summary or "Voice call"
            state.conversation_messages[message_id] = TeamConversationMessage(
                id=message_id,
                conversation_id=conversation.id,
                sender="system",
                message_type="voice_call",
                body=body,
                status="ended" if normalized_status == "ended" else "running",
                voice_session_id=normalized_voice_session_id,
                created_at=now,
                updated_at=now,
            )
            _touch_conversation(conversation, now=now, preview=body)
            return state

        state = self.update(_mutate)
        if message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation voice call was not recorded")
        return state, state.conversation_messages[message_id]

    def update_conversation_voice_call_status(
        self,
        *,
        voice_session_id: str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> tuple[TeamState, TeamConversationMessage] | None:
        normalized_voice_session_id = _required_text(voice_session_id, "voice_session_id")
        normalized_status = _optional_text(status) or "ended"
        normalized_summary = _optional_text(summary)
        normalized_error = _optional_text(error)
        current = self.read()
        message_id = next(
            (
                message.id
                for message in current.conversation_messages.values()
                if message.voice_session_id == normalized_voice_session_id
            ),
            None,
        )
        if message_id is None:
            return None

        def _mutate(state: TeamState) -> TeamState:
            message = _require_present(
                state.conversation_messages,
                message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            now = utc_now()
            message.status = "failed" if normalized_status == "failed" else "ended"
            if normalized_summary is not None:
                message.metadata = {**message.metadata, "summary": normalized_summary}
            if normalized_error is not None:
                message.error = normalized_error
            message.updated_at = now
            preview = "Voice call failed" if message.status == "failed" else "Voice call ended"
            _touch_conversation(conversation, now=now, preview=preview)
            return state

        state = self.update(_mutate)
        return state, state.conversation_messages[message_id]

    def set_agent_avatar(
        self,
        *,
        agent_id: str,
        avatar: AgentAvatar,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            agent.avatar = avatar
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def clear_agent_avatar(
        self,
        *,
        agent_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            agent.avatar = None
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def request_agent_context_rotation(
        self,
        *,
        agent_id: str,
        requested_by: str | None = None,
        request_note: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_requested_by = _optional_text(requested_by)
        normalized_request_note = _optional_text(request_note)

        def _mutate(state: TeamState) -> TeamState:
            now = utc_now()
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            runtime = state.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            _mark_context_rotation_pending(
                state,
                agent=agent,
                runtime=runtime,
                reason="manual",
                requested_by=normalized_requested_by,
                requested_at=now,
                request_note=normalized_request_note,
            )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def stand_down_agent(
        self,
        *,
        agent_id: str,
        reason: str,
        set_by: str | None = None,
        source: AgentStandDownSource | str = AgentStandDownSource.OPERATOR,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_reason = _required_text(reason, "reason")
        normalized_set_by = _optional_text(set_by) or "operator"
        normalized_source = (
            source
            if isinstance(source, AgentStandDownSource)
            else AgentStandDownSource(str(source))
        )

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            now = utc_now()
            stand_down = AgentStandDown(
                id=f"stand-down-{uuid4().hex}",
                agent_id=agent.id,
                reason=normalized_reason,
                source=normalized_source,
                set_by=normalized_set_by,
                set_at=now,
            )
            state.stand_downs[stand_down.id] = stand_down
            for affected_id in _agent_subtree_ids(state, agent.id):
                runtime = state.agent_runtimes.get(affected_id)
                if runtime is None:
                    continue
                runtime.next_wake_at = None
                runtime.updated_at = now
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def revoke_operator_stand_downs(
        self,
        *,
        agent_id: str,
        revoked_by: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_revoked_by = _optional_text(revoked_by) or "operator"

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.agents, normalized_agent_id, "agent")
            now = utc_now()
            revoked = False
            for stand_down in state.stand_downs.values():
                if (
                    stand_down.agent_id == normalized_agent_id
                    and stand_down.source == AgentStandDownSource.OPERATOR
                    and stand_down.active
                ):
                    stand_down.cleared_by = normalized_revoked_by
                    stand_down.cleared_at = now
                    revoked = True
            if not revoked:
                raise ValueError(
                    f"agent {normalized_agent_id} has no active operator stand-down"
                )
            for affected_id in _agent_subtree_ids(state, normalized_agent_id):
                if _active_stand_downs_for_agent(state, affected_id):
                    continue
                _mark_agent_work_due(state, agent_id=affected_id, now=now)
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def record_context_rotation_dispatch_started(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        thread_id: str | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_thread_id = _optional_text(thread_id)

        def _mutate(state: TeamState) -> TeamState:
            now = utc_now()
            _require_present(state.agents, normalized_agent_id, "agent")
            runtime = _require_present(
                state.agent_runtimes,
                normalized_agent_id,
                "agent runtime",
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                raise ValueError("dispatch_id does not match active team runtime lease")
            if runtime.context_rotation_pending:
                runtime.context_rotation_pending = False
                runtime.last_context_rotated_at = now
                if normalized_thread_id is not None:
                    runtime.last_thread_id = normalized_thread_id
                runtime.updated_at = now
            return state

        return self.update(_mutate)

    def create_or_reprioritize_task(
        self,
        *,
        task_id: str | None,
        owner_agent_id: str,
        project_id: str,
        priority: int,
        title: str,
        description: str,
        created_by_agent_id: str | None = None,
        created_by_actor: str | None = None,
        state: TeamTaskState = TeamTaskState.PENDING,
        parent_task_id: str | None = None,
        set_parent_task_id: bool = False,
        dependency_task_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_task_id = _optional_text(task_id)
        normalized_title = _required_text(title, "title")
        normalized_description = _required_text(description, "description")
        normalized_owner_agent_id = _required_text(owner_agent_id, "owner_agent_id")
        normalized_project_id = _required_text(project_id, "project_id")

        def _mutate(state_model: TeamState) -> TeamState:
            now = utc_now()
            if normalized_task_id is not None and normalized_task_id in state_model.tasks:
                task = state_model.tasks[normalized_task_id]
                task.priority = priority
                task.title = normalized_title
                task.description = normalized_description
                if set_parent_task_id or parent_task_id is not None:
                    task.parent_task_id = parent_task_id
                if dependency_task_ids is not None:
                    task.dependency_task_ids = dependency_task_ids
                task.updated_at = now
                if task.state in _NON_TERMINAL_TASK_STATES:
                    _mark_agent_work_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
                else:
                    _maybe_mark_idle_context_rotation_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
                return state_model
            effective_task_id = normalized_task_id or _generated_id(
                prefix="task",
                labels=[normalized_project_id, normalized_title],
                existing_ids=state_model.tasks,
            )
            _require_absent(state_model.tasks, effective_task_id, "task")
            state_model.tasks[effective_task_id] = AgentTask(
                id=effective_task_id,
                owner_agent_id=normalized_owner_agent_id,
                project_id=normalized_project_id,
                priority=priority,
                state=state,
                state_entered_at=now,
                title=normalized_title,
                description=normalized_description,
                created_by_agent_id=created_by_agent_id,
                created_by_actor=created_by_actor,
                parent_task_id=parent_task_id,
                dependency_task_ids=dependency_task_ids or [],
                created_at=now,
                updated_at=now,
            )
            if state in _NON_TERMINAL_TASK_STATES:
                _mark_agent_work_due(state_model, agent_id=normalized_owner_agent_id, now=now)
            else:
                _maybe_mark_idle_context_rotation_due(
                    state_model,
                    agent_id=normalized_owner_agent_id,
                    now=now,
                )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def update_task(
        self,
        *,
        task_id: str,
        priority: int | None = None,
        state: TeamTaskState | None = None,
        title: str | None = None,
        description: str | None = None,
        parent_task_id: str | None = None,
        set_parent_task_id: bool = False,
        dependency_task_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            task = _require_present(state_model.tasks, task_id, "task")
            now = utc_now()
            if priority is not None:
                task.priority = priority
            if state is not None:
                _set_task_state(task, state, now=now)
            if title is not None:
                task.title = title
            if description is not None:
                task.description = description
            if set_parent_task_id:
                task.parent_task_id = parent_task_id
            if dependency_task_ids is not None:
                task.dependency_task_ids = dependency_task_ids
            task.updated_at = now
            if task.state in _NON_TERMINAL_TASK_STATES:
                _mark_agent_work_due(
                    state_model,
                    agent_id=task.owner_agent_id,
                    now=task.updated_at,
                )
            else:
                _maybe_mark_idle_context_rotation_due(
                    state_model,
                    agent_id=task.owner_agent_id,
                    now=task.updated_at,
                )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def create_operator_directive(
        self,
        *,
        team_id: str,
        message: str,
        recipient_agent_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        title: str | None = None,
        source: str | None = None,
        source_message_id: str | None = None,
        created_by: str | None = None,
        requesting_session_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, AgentInboxItem]:
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_message = _required_text(message, "message")
        normalized_recipient_id = _optional_text(recipient_agent_id)
        normalized_project_id = _optional_text(project_id)
        normalized_task_id = _optional_text(task_id)
        normalized_title = _optional_text(title)
        normalized_source = _optional_text(source)
        normalized_source_message_id = _optional_text(source_message_id)
        normalized_created_by = _optional_text(created_by)
        normalized_requesting_session_id = _optional_text(requesting_session_id)
        created_inbox_id: str | None = None

        def _mutate(state_model: TeamState) -> TeamState:
            nonlocal created_inbox_id
            now = utc_now()
            team = _require_present(state_model.teams, normalized_team_id, "team")
            if team.archived:
                raise ValueError(f"team {team.id} is archived")
            recipient_id = normalized_recipient_id or team.manager_agent_id
            recipient = _require_present(state_model.agents, recipient_id, "recipient agent")
            if recipient.team_id != team.id:
                raise ValueError(f"recipient agent {recipient.id} is not in team {team.id}")
            if recipient.status != AgentStatus.ACTIVE:
                raise ValueError(f"recipient agent {recipient.id} is not active")
            if normalized_requesting_session_id is not None:
                requester = _agent_for_session(
                    state_model,
                    session_id=normalized_requesting_session_id,
                )
                requester_is_team_manager = requester.id == team.manager_agent_id
                requester_is_governance_manager = _agent_governs_team(
                    state_model,
                    agent_id=requester.id,
                    team_id=team.id,
                )
                if not requester_is_team_manager and not requester_is_governance_manager:
                    raise ValueError(
                        "team operator directives from agent sessions require the "
                        "team manager session or governance manager session"
                    )
                if recipient.id != team.manager_agent_id:
                    raise ValueError(
                        "team manager and governance manager sessions can only enqueue "
                        "operator directives for the target team manager inbox"
                    )

            effective_project_id = normalized_project_id
            if normalized_task_id is not None:
                task = _require_present(state_model.tasks, normalized_task_id, "task")
                project = _require_present(state_model.projects, task.project_id, "project")
                if project.team_id != team.id:
                    raise ValueError(f"task {task.id} is not in team {team.id}")
                if effective_project_id is not None and effective_project_id != task.project_id:
                    raise ValueError(
                        f"task {task.id} belongs to project {task.project_id}, "
                        f"not project {effective_project_id}"
                    )
                effective_project_id = task.project_id
            if effective_project_id is not None:
                project = _require_present(
                    state_model.projects,
                    effective_project_id,
                    "project",
                )
                if project.team_id != team.id:
                    raise ValueError(f"project {project.id} is not in team {team.id}")

            payload: dict[str, Any] = {
                "title": normalized_title or "Operator directive",
                "message": normalized_message,
                "team_id": team.id,
            }
            if effective_project_id is not None:
                payload["project_id"] = effective_project_id
            if normalized_source is not None:
                payload["source"] = normalized_source
            if normalized_source_message_id is not None:
                payload["source_message_id"] = normalized_source_message_id
            if normalized_created_by is not None:
                payload["created_by"] = normalized_created_by
            if normalized_requesting_session_id is not None:
                payload["requesting_session_id"] = normalized_requesting_session_id

            inbox_id = f"inbox-{uuid4().hex}"
            state_model.inbox[inbox_id] = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient.id,
                sender_agent_id=None,
                task_id=normalized_task_id,
                item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
                response_requested=True,
                priority=TeamInboxPriority.NORMAL,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            created_inbox_id = inbox_id
            _mark_agent_work_due(state_model, agent_id=recipient.id, now=now)
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        if created_inbox_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("operator directive was not created")
        return state, state.inbox[created_inbox_id]

    def record_deliverable(
        self,
        *,
        deliverable_id: str | None,
        task_id: str,
        producer_agent_id: str,
        summary: str,
        artifact_refs: list[str] | None = None,
        state: TeamRecordState = TeamRecordState.PENDING,
        supersedes_deliverable_id: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_producer_agent_id = _required_text(producer_agent_id, "producer_agent_id")
        normalized_summary = _required_text(summary, "summary")
        normalized_supersedes_deliverable_id = _optional_text(
            supersedes_deliverable_id
        )

        def _mutate(state_model: TeamState) -> TeamState:
            task = _require_present(state_model.tasks, normalized_task_id, "task")
            superseded_deliverable = _superseded_deliverable_for_rework(
                state_model,
                task=task,
                producer_agent_id=normalized_producer_agent_id,
                supersedes_deliverable_id=normalized_supersedes_deliverable_id,
            )
            effective_deliverable_id = normalized_deliverable_id or _generated_id(
                prefix="deliverable",
                labels=[normalized_task_id, normalized_summary],
                existing_ids=state_model.deliverables,
            )
            _require_absent(state_model.deliverables, effective_deliverable_id, "deliverable")
            deliverable = Deliverable(
                id=effective_deliverable_id,
                task_id=normalized_task_id,
                producer_agent_id=normalized_producer_agent_id,
                summary=normalized_summary,
                artifact_refs=artifact_refs or [],
                state=state,
                supersedes_deliverable_id=normalized_supersedes_deliverable_id,
            )
            state_model.deliverables[deliverable.id] = deliverable
            if superseded_deliverable is not None:
                superseded_deliverable.superseded_by_deliverable_id = deliverable.id
            task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
            task.updated_at = utc_now()
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def create_review_profile(
        self,
        *,
        profile_id: str | None,
        team_id: str,
        project_id: str | None = None,
        name: str,
        description: str = "",
        applicable_role_ids: list[str] | None = None,
        criteria: list[ReviewProfileCriterion] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_profile_id = _optional_text(profile_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_name = _required_text(name, "name")
        normalized_project_id = _optional_text(project_id)

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.teams, normalized_team_id, "team")
            if normalized_project_id is not None:
                project = _require_present(state.projects, normalized_project_id, "project")
                if project.team_id != normalized_team_id:
                    raise ValueError(
                        f"project {normalized_project_id} does not belong to team {normalized_team_id}"
                    )
            effective_profile_id = normalized_profile_id or _generated_id(
                prefix="profile",
                labels=[normalized_project_id or normalized_team_id, normalized_name],
                existing_ids=state.review_profiles,
            )
            _require_absent(state.review_profiles, effective_profile_id, "review profile")
            now = utc_now()
            profile = ReviewProfile(
                id=effective_profile_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                name=normalized_name,
                description=description,
                applicable_role_ids=applicable_role_ids or [],
                criteria=criteria or [],
                created_at=now,
                updated_at=now,
            )
            for role_id in profile.applicable_role_ids:
                role = _require_present(state.roles, role_id, "role")
                _validate_role_filter_assignment(role, profile=profile)
            state.review_profiles[profile.id] = profile
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def update_review_profile(
        self,
        *,
        profile_id: str,
        name: str | None = None,
        description: str | None = None,
        applicable_role_ids: list[str] | None = None,
        criteria: list[ReviewProfileCriterion] | None = None,
        archived: bool | None = None,
        project_id: str | None = None,
        set_project_id: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            profile = _require_present(state.review_profiles, profile_id, "review profile")
            if name is not None:
                profile.name = name
            if description is not None:
                profile.description = description
            if applicable_role_ids is not None:
                profile.applicable_role_ids = applicable_role_ids
            if criteria is not None:
                profile.criteria = [
                    ReviewProfileCriterion.model_validate(criterion) for criterion in criteria
                ]
            if archived is not None:
                profile.archived = archived
            if set_project_id:
                normalized_project_id = _optional_text(project_id)
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != profile.team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team {profile.team_id}"
                        )
                profile.project_id = normalized_project_id
            for role_id in profile.applicable_role_ids:
                role = _require_present(state.roles, role_id, "role")
                _validate_role_filter_assignment(role, profile=profile)
            profile.updated_at = utc_now()
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def materialize_template(
        self,
        template_id: str,
        *,
        inputs: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_template_id = _required_text(template_id, "template_id")
        template = get_template(normalized_template_id)
        if template is None:
            raise ValueError(f"template {normalized_template_id} does not exist")

        normalized_inputs = _mapping(inputs, "inputs")
        team_input = _required_mapping(normalized_inputs, "team")
        project_input = _required_mapping(normalized_inputs, "project")
        team_name = _required_text(team_input.get("name"), "team.name")
        operator_communication_target_id = _optional_text(
            team_input.get("operator_communication_target_id")
        )
        escalate_to_governance = _optional_bool(
            team_input.get("escalate_to_governance"),
            "team.escalate_to_governance",
        )
        max_cost_usd = (
            team_input.get("max_cost_usd") if "max_cost_usd" in team_input else None
        )
        project_name = _required_text(project_input.get("name"), "project.name")
        git = _project_git_config(project_input.get("git"))

        role_inputs = _materialize_role_inputs(template, normalized_inputs, team_input, project_input)
        current_state = self.read()
        team_id = _optional_text(team_input.get("id")) or _generated_id(
            prefix="team",
            labels=[team_name],
            existing_ids=current_state.teams,
        )
        project_id = _optional_text(project_input.get("id")) or _generated_id(
            prefix="project",
            labels=[team_id, project_name],
            existing_ids=current_state.projects,
        )
        goal_ids = {
            goal.id: _unique_id_from_base(
                _materialized_template_record_id(project_id, goal.id),
                existing_ids=set(),
            )
            for goal in template.project_goals
        }
        used_review_profile_ids = set(current_state.review_profiles)
        review_profile_ids = {
            profile.id: _unique_id_from_base(
                _materialized_template_record_id(project_id, profile.id),
                existing_ids=used_review_profile_ids,
            )
            for profile in template.review_profiles
        }
        used_review_profile_ids.update(review_profile_ids.values())
        used_task_ids = set(current_state.tasks)
        task_ids = {
            task.id: _unique_id_from_base(
                _materialized_template_record_id(project_id, task.id),
                existing_ids=used_task_ids,
            )
            for task in template.tasks
        }
        used_task_ids.update(task_ids.values())
        used_role_ids = set(current_state.roles)
        role_ids: dict[str, str] = {}
        for role in template.roles:
            role_id = _unique_id_from_base(
                _default_role_id(
                    team_id=team_id,
                    project_id=None if role.key == template.root_role_key else project_id,
                    role_key=role.key,
                ),
                existing_ids=used_role_ids,
            )
            used_role_ids.add(role_id)
            role_ids[role.key] = role_id
        used_agent_ids = set(current_state.agents)
        for role in template.roles:
            role_input = role_inputs[role.key]
            agent_id = _optional_text(role_input.get("agent_id", role_input.get("id")))
            if agent_id is None:
                agent_id = _generated_id(
                    prefix="agent",
                    labels=[
                        team_id if role.key == template.root_role_key else project_id,
                        role.label,
                    ],
                    existing_ids=used_agent_ids,
                )
            role_input["agent_id"] = agent_id
            used_agent_ids.add(agent_id)
        concrete_ids = [
            team_id,
            project_id,
            *role_ids.values(),
            *[role_input["agent_id"] for role_input in role_inputs.values()],
            *goal_ids.values(),
            *review_profile_ids.values(),
            *task_ids.values(),
        ]
        _ensure_unique(concrete_ids, "materialization ids")
        _ensure_unique(
            [role_input["session_id"] for role_input in role_inputs.values()],
            "materialization session ids",
        )

        result: dict[str, Any] = {
            "success": False,
            "template_id": template.id,
            "template_name": template.name,
            "team_id": team_id,
            "project_id": project_id,
            "role_ids": role_ids,
            "agent_ids": {key: value["agent_id"] for key, value in role_inputs.items()},
            "goal_ids": [goal_ids[goal.id] for goal in template.project_goals],
            "review_profile_ids": [
                review_profile_ids[profile.id] for profile in template.review_profiles
            ],
            "task_ids": [task_ids[task.id] for task in template.tasks],
            "errors": [],
        }

        role_agent_ids = {role.key: role_inputs[role.key]["agent_id"] for role in template.roles}
        root_role_key = template.root_role_key
        root_role = next(role for role in template.roles if role.key == root_role_key)
        if root_role.reports_to_role_key is not None:
            raise ValueError(f"template {template.id} root role must not report upward")

        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.teams, team_id, "team")
            _require_absent(state.projects, project_id, "project")
            for role_key, role_id in role_ids.items():
                _require_absent(state.roles, role_id, "role")
            for role_key, role_input in role_inputs.items():
                _require_absent(state.agents, role_input["agent_id"], "agent")
            for profile in template.review_profiles:
                _require_absent(
                    state.review_profiles,
                    review_profile_ids[profile.id],
                    "review profile",
                )
            for task in template.tasks:
                _require_absent(state.tasks, task_ids[task.id], "task")

            now = utc_now()
            for role in template.roles:
                role_project_id = None if role.key == root_role_key else project_id
                state.roles[role_ids[role.key]] = TeamRole(
                    id=role_ids[role.key],
                    team_id=team_id,
                    project_id=role_project_id,
                    name=role.label,
                    description=role.description,
                    permissions=_normalize_role_permissions(role.permissions),
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                    created_at=now,
                    updated_at=now,
                )
            manager_role_input = role_inputs[root_role_key]
            manager = PersistentAgent(
                id=manager_role_input["agent_id"],
                team_id=team_id,
                role_id=role_ids[root_role_key],
                session_id=manager_role_input["session_id"],
                reports_to_agent_id=None,
                ephemeral_policy=root_role.ephemeral_policy.model_copy(deep=True),
            )
            state.agents[manager.id] = manager
            state.teams[team_id] = Team(
                id=team_id,
                name=team_name,
                manager_agent_id=manager.id,
                operator_communication_target_id=operator_communication_target_id,
                escalate_to_governance=escalate_to_governance or False,
                max_cost_usd=max_cost_usd,
                created_at=now,
                updated_at=now,
            )

            project_goals = [
                goal.model_copy(update={"id": goal_ids[goal.id]}, deep=True)
                for goal in template.project_goals
            ]
            state.projects[project_id] = Project(
                id=project_id,
                team_id=team_id,
                name=project_name,
                lead_agent_id=role_agent_ids["project_lead"],
                goals=project_goals,
                git=git,
                created_at=now,
                updated_at=now,
            )
            state.teams[team_id].project_ids = _append_unique(
                state.teams[team_id].project_ids,
                project_id,
            )

            role_objects: dict[str, PersistentAgent] = {
                root_role_key: manager,
            }
            ordered_roles = _ordered_template_roles(template)
            for role in ordered_roles:
                if role.key == root_role_key:
                    continue
                superior_role_key = role.reports_to_role_key
                if superior_role_key is None:
                    raise ValueError(
                        f"template {template.id} role {role.key} is missing reports_to_role_key"
                    )
                superior = role_objects.get(superior_role_key)
                if superior is None:
                    raise ValueError(
                        f"template {template.id} role {role.key} reports to missing role "
                        f"{superior_role_key}"
                    )
                role_agent = PersistentAgent(
                    id=role_agent_ids[role.key],
                    team_id=team_id,
                    role_id=role_ids[role.key],
                    session_id=role_inputs[role.key]["session_id"],
                    reports_to_agent_id=superior.id,
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                )
                state.agents[role_agent.id] = role_agent
                assignment_kind = (
                    AgentProjectAssignmentKind.PROJECT_LEAD
                    if role.key == "project_lead"
                    else AgentProjectAssignmentKind.PROJECT_MEMBER
                )
                assignment_id = _project_assignment_id(
                    project_id,
                    role_agent.id,
                    assignment_kind,
                    existing_ids=state.project_assignments,
                )
                state.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=team_id,
                    project_id=project_id,
                    agent_id=role_agent.id,
                    role_id=role_ids[role.key],
                    kind=assignment_kind,
                    created_at=now,
                    updated_at=now,
                )
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids,
                    role_agent.id,
                )
                role_objects[role.key] = role_agent
            state.projects[project_id].lead_agent_id = role_agent_ids["project_lead"]
            state.projects[project_id].updated_at = now
            state.teams[team_id].updated_at = now

            for profile_template in template.review_profiles:
                profile = ReviewProfile(
                    id=review_profile_ids[profile_template.id],
                    team_id=team_id,
                    project_id=project_id if profile_template.project_scoped else None,
                    name=profile_template.name,
                    description=profile_template.description,
                    applicable_role_ids=[
                        role_ids[role_key]
                        for role_key in profile_template.applicable_role_keys
                    ],
                    criteria=[
                        criterion.model_copy(deep=True)
                        for criterion in profile_template.criteria
                    ],
                    created_at=now,
                    updated_at=now,
                )
                state.review_profiles[profile.id] = profile

            for task_template in template.tasks:
                owner_agent_id = role_agent_ids[task_template.owner_role_key]
                created_by_agent_id = role_agent_ids[task_template.created_by_role_key]
                dependency_task_ids = [
                    task_ids[dependency_id]
                    for dependency_id in task_template.dependency_task_ids
                ]
                state.tasks[task_ids[task_template.id]] = AgentTask(
                    id=task_ids[task_template.id],
                    owner_agent_id=owner_agent_id,
                    project_id=project_id,
                    priority=task_template.priority,
                    state=task_template.state,
                    title=task_template.title,
                    description=task_template.description,
                    created_by_agent_id=created_by_agent_id,
                    parent_task_id=(
                        task_ids[task_template.parent_task_id]
                        if task_template.parent_task_id is not None
                        else None
                    ),
                    dependency_task_ids=dependency_task_ids,
                    created_at=now,
                    updated_at=now,
                )

            result["success"] = True
            result["created_agent_ids"] = dict(result["agent_ids"])
            return state

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["created_team_id"] = team_id
        result["created_project_id"] = project_id
        return state, result

    def record_review_request(
        self,
        *,
        review_request_id: str | None,
        deliverable_id: str,
        reviewer_agent_id: str,
        state: TeamRecordState = TeamRecordState.PENDING,
        findings: list[str] | None = None,
        review_profile_id: str | None = None,
        instructions: str | None = None,
        decision_at: datetime | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_review_request_id = _optional_text(review_request_id)
        normalized_deliverable_id = _required_text(deliverable_id, "deliverable_id")
        normalized_reviewer_agent_id = _required_text(reviewer_agent_id, "reviewer_agent_id")

        def _mutate(state_model: TeamState) -> TeamState:
            deliverable = _require_present(
                state_model.deliverables,
                normalized_deliverable_id,
                "deliverable",
            )
            reviewer = _require_present(
                state_model.agents,
                normalized_reviewer_agent_id,
                "reviewer",
            )
            task = _require_present(state_model.tasks, deliverable.task_id, "task")
            project = _require_present(state_model.projects, task.project_id, "project")
            if reviewer.team_id != project.team_id:
                raise ValueError(
                    f"reviewer {normalized_reviewer_agent_id} is not in the same team as deliverable "
                    f"{normalized_deliverable_id}"
                )
            _profile, profile_error = _review_profile_for_request(
                state_model,
                profile_id=review_profile_id,
                expected_team_id=reviewer.team_id,
                project_id=task.project_id,
                reviewer=reviewer,
            )
            if profile_error is not None:
                raise ValueError(profile_error)
            effective_review_request_id = normalized_review_request_id or _generated_id(
                prefix="review",
                labels=[
                    normalized_deliverable_id,
                    review_profile_id or normalized_reviewer_agent_id,
                ],
                existing_ids=state_model.review_requests,
            )
            _require_absent(
                state_model.review_requests,
                effective_review_request_id,
                "review request",
            )
            review = ReviewRequest(
                id=effective_review_request_id,
                deliverable_id=normalized_deliverable_id,
                reviewer_agent_id=normalized_reviewer_agent_id,
                state=state,
                findings=findings or [],
                review_profile_id=review_profile_id,
                instructions=instructions,
                created_at=utc_now(),
                decision_at=decision_at,
            )
            state_model.review_requests[review.id] = review
            deliverable.review_request_ids = _append_unique(
                deliverable.review_request_ids,
                review.id,
            )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def record_review_decision(
        self,
        *,
        review_request_id: str,
        state: TeamRecordState,
        findings: list[str] | None = None,
        criteria: list[ReviewCriterionResult] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_id = _required_text(review_request_id, "review_request_id")
        if not _is_review_decision_state(state):
            raise ValueError(
                f"review decision state {state.value} must be accepted, rejected, or blocked"
            )
        findings_list = list(findings or [])
        criterion_results = (
            [ReviewCriterionResult.model_validate(item) for item in criteria]
            if criteria is not None
            else None
        )

        def _mutate(state_model: TeamState) -> TeamState:
            review = _require_present(
                state_model.review_requests, normalized_id, "review request"
            )
            if criterion_results is not None:
                review.criteria = criterion_results
            projects_to_recalc: set[str] = set()
            _cascade_review_decision(
                state_model,
                review=review,
                decision_state=state,
                findings=findings_list,
                projects_to_recalc=projects_to_recalc,
            )
            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, project_id)
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=review.reviewer_agent_id,
                now=utc_now(),
            )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def create_release_package(
        self,
        *,
        project_id: str,
        deliverable_ids: list[str],
        release_id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        state, _result = self.create_release_package_with_result(
            project_id=project_id,
            deliverable_ids=deliverable_ids,
            release_id=release_id,
            release_branch=release_branch,
            canonical_branch=canonical_branch,
            expected_revision=expected_revision,
        )
        return state

    def create_release_package_with_result(
        self,
        *,
        project_id: str,
        deliverable_ids: list[str],
        release_id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_project_id = _required_text(project_id, "project_id")
        request = {
            "id": _optional_text(release_id),
            "project_id": normalized_project_id,
            "deliverable_ids": list(deliverable_ids),
            "release_branch": _optional_text(release_branch),
            "canonical_branch": _optional_text(canonical_branch),
        }
        result: dict[str, Any] = {
            "success": False,
            "release_package_ids": [],
            "errors": [],
            "events": [],
            "git_promotion_event_ids": [],
            "created_deliverable_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            project = _require_present(
                state_model.projects, normalized_project_id, "project"
            )
            projects_to_recalc: set[str] = set()
            created_id = _build_release_package(
                state_model,
                project=project,
                request=request,
                result=result,
                projects_to_recalc=projects_to_recalc,
                actor_agent_id=project.lead_agent_id,
                team_id=project.team_id,
            )
            if created_id is not None:
                result["release_package_ids"].append(created_id)
            if result["errors"]:
                raise ValueError("; ".join(result["errors"]))
            for recalc_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, recalc_id)
            result["success"] = True
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def request_ephemeral_assignments(
        self,
        *,
        parent_agent_id: str,
        requests: list[dict[str, Any]],
        dispatch_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_parent_id = _required_text(parent_agent_id, "parent_agent_id")
        normalized_dispatch_id = _optional_text(dispatch_id)
        normalized_requests = _list_of_mappings(requests, "requests")
        result: dict[str, Any] = _ephemeral_request_result()

        def _mutate(state_model: TeamState) -> TeamState:
            parent = _require_present(state_model.agents, normalized_parent_id, "parent agent")
            _create_ephemeral_assignments(
                state_model,
                parent=parent,
                requests=normalized_requests,
                dispatch_id=normalized_dispatch_id,
                result=result,
            )
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        return state, result

    def mark_ephemeral_spawned(
        self,
        *,
        assignment_id: str,
        session_id: str | None = None,
        create_result: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_session_id = _optional_text(session_id)
        normalized_create_result = dict(create_result or {})
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _require_present(
                state_model.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            result["session_id"] = assignment.session_id
            if normalized_session_id is not None and normalized_session_id != assignment.session_id:
                result["error"] = "session_id does not match ephemeral assignment"
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = str(result["error"])
            elif normalized_create_result and not _bool_from_value(
                normalized_create_result.get("success"),
                default=True,
            ):
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = (
                    _optional_text(normalized_create_result.get("message"))
                    or _optional_text(normalized_create_result.get("error"))
                    or "spawn failed"
                )
                result["error"] = assignment.error
            else:
                assignment.state = EphemeralAssignmentState.RUNNING
                assignment.error = None
                result["success"] = True
            assignment.updated_at = utc_now()
            result["state"] = assignment.state.value
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def apply_ephemeral_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": None,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
            "event": None,
            "created_inbox_item_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _resolve_ephemeral_assignment(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["assignment_id"] = assignment.id
            result["session_id"] = assignment.session_id
            if not valid:
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = error or "invalid ephemeral action"
                result["error"] = assignment.error
                result["state"] = assignment.state.value
                assignment.updated_at = utc_now()
                return state_model

            assignment.summary = _required_text(normalized_action.get("summary"), "summary")
            assignment.artifact_refs = _string_list(
                normalized_action.get("artifact_refs"),
                "artifact_refs",
            )
            agent_branch = _optional_text(normalized_action.get("agent_branch"))
            commit_sha = _optional_text(normalized_action.get("commit_sha"))
            if agent_branch is not None:
                expected_branch = ephemeral_agent_branch(
                    assignment.session_id, assignment.id
                )
                if agent_branch != expected_branch:
                    assignment.state = EphemeralAssignmentState.FAILED
                    assignment.error = (
                        f"agent_branch {agent_branch!r} does not match expected "
                        f"ephemeral branch {expected_branch!r}"
                    )
                    assignment.updated_at = utc_now()
                    result["error"] = assignment.error
                    result["state"] = assignment.state.value
                    result["success"] = False
                    return state_model
                assignment.agent_branch = agent_branch
            if commit_sha is not None:
                assignment.commit_sha = commit_sha
            assignment.error = _optional_text(normalized_action.get("error"))
            assignment.state = (
                EphemeralAssignmentState.FAILED
                if assignment.error
                else EphemeralAssignmentState.COMPLETED
            )
            if assignment.state == EphemeralAssignmentState.COMPLETED:
                inbox_id = f"inbox-{uuid4().hex}"
                state_model.inbox[inbox_id] = AgentInboxItem(
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
                result["created_inbox_item_ids"].append(inbox_id)
            assignment.updated_at = utc_now()
            result["success"] = assignment.state == EphemeralAssignmentState.COMPLETED
            result["state"] = assignment.state.value
            result["error"] = assignment.error
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        assignment_id = result.get("assignment_id")
        if isinstance(assignment_id, str) and result.get("success"):
            result["event"] = _ephemeral_assignment_event_payload(
                state,
                assignment_id,
                event_type=EPHEMERAL_COMPLETED_EVENT_TYPE,
            )
        return state, result

    def accept_ephemeral_deliverable(
        self,
        *,
        assignment_id: str,
        accepted: bool = True,
        deliverable_id: str | None = None,
        rejection_reason: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_rejection_reason = _optional_text(rejection_reason)
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "accepted": bool(accepted),
            "deliverable_id": None,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _require_present(
                state_model.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            result["deliverable_id"] = _apply_ephemeral_acceptance(
                state_model,
                assignment=assignment,
                accepted=accepted,
                deliverable_id=normalized_deliverable_id,
                rejection_reason=normalized_rejection_reason,
            )
            result["success"] = True
            result["state"] = assignment.state.value
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def mark_ephemeral_cleaned_up(
        self,
        *,
        assignment_id: str | None = None,
        session_id: str | None = None,
        destroy_result: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _optional_text(assignment_id)
        normalized_session_id = _optional_text(session_id)
        normalized_destroy_result = dict(destroy_result or {})
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _resolve_ephemeral_assignment(
                state_model,
                action={"ephemeral_assignment_id": normalized_assignment_id}
                if normalized_assignment_id
                else {},
                session_id=normalized_session_id,
            )
            result["assignment_id"] = assignment.id
            result["session_id"] = assignment.session_id
            if normalized_destroy_result and not bool(normalized_destroy_result.get("success", True)):
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = (
                    _optional_text(normalized_destroy_result.get("message"))
                    or "cleanup failed"
                )
                result["error"] = assignment.error
            else:
                assignment.state = EphemeralAssignmentState.CLEANED_UP
                assignment.error = None
                result["success"] = True
            assignment.updated_at = utc_now()
            result["state"] = assignment.state.value
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def lease_due_agents(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 900,
        wake_interval_seconds: int = 900,
        max_no_progress_count: int = 3,
        low_priority_wake_after_cycles: int = 4,
        team_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        blocked_team_ids: set[str] | None = None,
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_limit = max(0, int(limit))
        low_priority_threshold = max(0, int(low_priority_wake_after_cycles))
        blocked_teams = set(blocked_team_ids or set())
        leases: list[dict[str, Any]] = []
        if normalized_limit == 0:
            return self.read(), leases

        def _mutate(state_model: TeamState) -> TeamState:
            for agent in sorted(state_model.agents.values(), key=lambda item: item.id):
                if normalized_limit and len(leases) >= normalized_limit:
                    break
                if agent.status != AgentStatus.ACTIVE:
                    continue
                if _active_stand_downs_for_agent(state_model, agent.id):
                    continue
                if normalized_team_id is not None and agent.team_id != normalized_team_id:
                    continue
                if agent.team_id in blocked_teams:
                    continue
                if normalized_project_id is not None and not agent_has_project_assignment(state_model, agent.id, normalized_project_id):
                    continue
                if normalized_agent_id is not None and agent.id != normalized_agent_id:
                    continue
                runtime = state_model.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue
                active_tasks = _active_tasks_for_agent(state_model, agent.id)
                pending_inbox = _pending_inbox_for_agent(state_model, agent.id)
                immediate_inbox = [
                    item
                    for item in pending_inbox
                    if item.priority != TeamInboxPriority.LOW
                ]
                low_priority_inbox = [
                    item for item in pending_inbox if item.priority == TeamInboxPriority.LOW
                ]
                action_feedback = _latest_action_feedback_for_agent(state_model, agent.id)
                rotation_pending = runtime.context_rotation_pending
                task_work_ready = any(
                    state_model.dependency_status(task.id) == "satisfied"
                    for task in active_tasks
                ) or any(
                    state_model.subtask_counts(task.id)["open"] > 0
                    for task in active_tasks
                )
                wake_eligible = (
                    rotation_pending
                    or bool(pending_inbox)
                    or bool(action_feedback)
                    or task_work_ready
                )
                if not wake_eligible:
                    _clear_low_priority_batch(runtime)
                    continue
                due_by_time = (
                    runtime.next_wake_at is None
                    or _ensure_utc(runtime.next_wake_at) <= effective_now
                )
                immediate_wake_ready = (
                    rotation_pending
                    or bool(immediate_inbox)
                    or bool(action_feedback)
                    or (task_work_ready and due_by_time)
                )
                low_priority_wake_ready = False
                if low_priority_inbox and not immediate_wake_ready:
                    low_priority_wake_ready = _advance_low_priority_batch(
                        runtime,
                        now=effective_now,
                        due_after_cycles=low_priority_threshold,
                    )
                    if not low_priority_wake_ready:
                        runtime.updated_at = effective_now
                        continue
                elif not low_priority_inbox:
                    _clear_low_priority_batch(runtime)
                if (
                    not pending_inbox
                    and not rotation_pending
                    and not action_feedback
                    and not due_by_time
                ):
                    continue
                if pending_inbox and not immediate_wake_ready and not low_priority_wake_ready:
                    continue
                if (
                    max_no_progress_count >= 0
                    and runtime.no_progress_count >= max_no_progress_count
                    and not pending_inbox
                    and not rotation_pending
                    and not action_feedback
                ):
                    continue

                dispatch_id = f"team-dispatch-{uuid4().hex}"
                reason = (
                    "inbox"
                    if immediate_inbox
                    else "low_priority_inbox"
                    if low_priority_wake_ready
                    else "action_feedback"
                    if action_feedback
                    else "scheduled"
                )
                if (
                    runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
                    and not runtime.context_rotation_pending
                ):
                    _mark_context_rotation_pending(
                        state_model,
                        agent=agent,
                        runtime=runtime,
                        reason="every_wake",
                        requested_by=None,
                        requested_at=effective_now,
                        request_note=None,
                    )
                _clear_low_priority_batch(runtime)
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_reason = reason
                runtime.last_wake_at = effective_now
                runtime.wake_leased_until = effective_now + timedelta(seconds=lease_seconds)
                runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
                runtime.updated_at = effective_now
                leases.append(
                    {
                        "agent": _agent_payload(state_model, agent),
                        "runtime": runtime.model_dump(mode="json"),
                        "dispatch_id": dispatch_id,
                        "reason": reason,
                        "tasks": [
                            task_payload_with_derived(state_model, task)
                            for task in active_tasks
                        ],
                        "inbox": [item.model_dump(mode="json") for item in pending_inbox],
                        "recent_action_feedback": action_feedback,
                        "operator_chat_participants": _operator_chat_participant_payloads(state_model),
                        "superior_agent_id": agent.reports_to_agent_id,
                        "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                        "subordinates": _subordinate_agent_payloads(state_model, agent),
                    }
                )
            return state_model

        if normalized_limit == 0:
            return self.read(), []
        state = self.update(_mutate)
        return state, leases

    def lease_due_governance_managers(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 900,
        wake_interval_seconds: int = 3600,
        governance_group_id: str | None = None,
        cost_payload: Mapping[str, Any] | None = None,
        cost_warning_ratio: Decimal | str | float = Decimal("0.8"),
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_group_id = _optional_text(governance_group_id)
        normalized_limit = max(0, int(limit))
        warning_ratio = _decimal_or_zero(cost_warning_ratio)
        leases: list[dict[str, Any]] = []

        def _mutate(state_model: TeamState) -> TeamState:
            for group in sorted(state_model.governance_groups.values(), key=lambda item: item.id):
                if normalized_limit and len(leases) >= normalized_limit:
                    break
                if group.archived:
                    continue
                if normalized_group_id is not None and group.id != normalized_group_id:
                    continue
                manager = state_model.agents.get(group.manager_agent_id)
                if manager is None or manager.status != AgentStatus.ACTIVE:
                    continue
                manager_runtime = state_model.agent_runtimes.get(manager.id)
                if (
                    manager_runtime is not None
                    and manager_runtime.wake_leased_until is not None
                    and _ensure_utc(manager_runtime.wake_leased_until) > effective_now
                ):
                    continue
                runtime = state_model.governance_runtimes.setdefault(
                    group.id,
                    GovernanceRuntime(governance_group_id=group.id),
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue

                pending_agenda = _pending_governance_agenda_for_group(
                    state_model,
                    group.id,
                )
                cost_signals = _governance_cost_signals(
                    state_model,
                    group=group,
                    cost_payload=cost_payload,
                    warning_ratio=warning_ratio,
                )
                blocked_team_signals = _governance_blocked_team_signals(
                    state_model,
                    group=group,
                )
                cost_signal_fingerprints = _governance_cost_signal_fingerprints(
                    cost_signals
                )
                blocked_signal_fingerprints = _governance_blocked_signal_fingerprints(
                    blocked_team_signals
                )
                cost_signals_changed = (
                    cost_signal_fingerprints != runtime.last_cost_signal_fingerprints
                )
                blocked_signals_changed = (
                    blocked_signal_fingerprints != runtime.last_blocked_signal_fingerprints
                )
                changed_cost_signals = cost_signals if cost_signals and cost_signals_changed else []
                changed_blocked_team_signals = (
                    blocked_team_signals
                    if blocked_team_signals and blocked_signals_changed
                    else []
                )
                due_by_interval = (
                    runtime.next_wake_at is None
                    or _ensure_utc(runtime.next_wake_at) <= effective_now
                )
                pending_agenda_ready = bool(pending_agenda) and due_by_interval
                if (
                    not pending_agenda_ready
                    and not changed_cost_signals
                    and not changed_blocked_team_signals
                ):
                    if cost_signals_changed or blocked_signals_changed:
                        runtime.last_cost_signal_fingerprints = cost_signal_fingerprints
                        runtime.last_blocked_signal_fingerprints = blocked_signal_fingerprints
                        runtime.updated_at = effective_now
                    continue

                if pending_agenda_ready:
                    reason = pending_agenda[0].item_type.value
                elif changed_cost_signals:
                    reason = changed_cost_signals[0]["type"]
                else:
                    reason = changed_blocked_team_signals[0]["type"]

                dispatch_id = f"team-governance-dispatch-{uuid4().hex}"
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_reason = reason
                runtime.last_wake_at = effective_now
                runtime.wake_leased_until = effective_now + timedelta(seconds=lease_seconds)
                runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
                runtime.last_cost_signal_fingerprints = cost_signal_fingerprints
                runtime.last_blocked_signal_fingerprints = blocked_signal_fingerprints
                runtime.updated_at = effective_now
                leases.append(
                    {
                        "agent": _agent_payload(state_model, manager),
                        "governance_group": _governance_group_payload(state_model, group),
                        "runtime": runtime.model_dump(mode="json"),
                        "dispatch_id": dispatch_id,
                        "reason": reason,
                        "agenda": [
                            item.model_dump(mode="json")
                            for item in pending_agenda
                        ],
                        "member_teams": [
                            _governance_member_team_payload(
                                state_model,
                                team_id,
                                cost_payload=cost_payload,
                            )
                            for team_id in group.member_team_ids
                            if team_id in state_model.teams
                        ],
                        "cost_signals": changed_cost_signals,
                        "blocked_team_signals": changed_blocked_team_signals,
                    }
                )
            return state_model

        if normalized_limit == 0:
            return self.read(), []
        state = self.update(_mutate)
        return state, leases

    def complete_governance_dispatch(
        self,
        *,
        governance_group_id: str,
        dispatch_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")

        def _mutate(state: TeamState) -> TeamState:
            runtime = state.governance_runtimes.get(normalized_group_id)
            if runtime is None or runtime.active_dispatch_id != normalized_dispatch_id:
                return state
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.updated_at = utc_now()
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def mirror_session_metadata_payload(
        self,
        *,
        state: TeamState,
        agent_id: str,
    ) -> dict[str, Any]:
        agent = _require_present(state.agents, agent_id, "agent")
        runtime = state.agent_runtimes.get(agent_id)
        active_task_ids = [task.id for task in _active_tasks_for_agent(state, agent_id)]
        pending_inbox_ids = [item.id for item in _pending_inbox_for_agent(state, agent_id)]
        role = state.roles.get(agent.role_id)
        active_stand_downs = _active_stand_downs_for_agent(state, agent_id)
        return {
            "team_addon": {
                "agent_id": agent.id,
                "team_id": agent.team_id,
                "project_ids": agent_project_ids(state, agent.id),
                "lead_project_ids": agent_lead_project_ids(state, agent.id),
                "project_assignments": agent_project_assignment_payloads(state, agent.id),
                "role_id": agent.role_id,
                "role_name": role.name if role is not None else None,
                "role_permissions": [permission.value for permission in effective_role_permissions(state, agent)],
                "effective_role_permissions": [permission.value for permission in effective_role_permissions(state, agent)],
                "reports_to_agent_id": agent.reports_to_agent_id,
                "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                "active_task_ids": active_task_ids,
                "pending_inbox_item_ids": pending_inbox_ids,
                "stand_down_active": bool(active_stand_downs),
                "effective_stand_downs": [
                    stand_down.model_dump(mode="json")
                    for stand_down in active_stand_downs
                ],
                "context_clearing_policy": (
                    runtime.context_clearing_policy.value
                    if runtime is not None
                    else AgentContextClearingPolicy.OFF.value
                ),
                "auto_context_clearing_enabled": (
                    runtime.auto_context_clearing_enabled if runtime is not None else False
                ),
                "context_rotation_pending": (
                    runtime.context_rotation_pending if runtime is not None else False
                ),
                "last_context_rotated_at": (
                    runtime.last_context_rotated_at.isoformat()
                    if runtime is not None and runtime.last_context_rotated_at is not None
                    else None
                ),
            }
        }

    def apply_agent_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        normalized_dispatch_id = _optional_text(dispatch_id) or _optional_text(
            normalized_action.get("dispatch_id")
        )
        result: dict[str, Any] = {
            "success": False,
            "applied": False,
            "agent_id": None,
            "errors": [],
            "created_inbox_item_ids": [],
            "created_deliverable_ids": [],
            "created_task_ids": [],
            "created_status_request_ids": [],
            "created_review_request_ids": [],
            "decided_review_request_ids": [],
            "updated_goal_ids": [],
            "operator_escalation_event_ids": [],
            "git_promotion_event_ids": [],
            "release_package_ids": [],
            "updated_task_ids": [],
            "handled_inbox_item_ids": [],
            "created_ephemeral_assignment_ids": [],
            "ephemeral_events": [],
            "events": [],
            "accepted_ephemeral_assignment_ids": [],
            "rejected_ephemeral_assignment_ids": [],
            "created_stand_down_ids": [],
            "revoked_stand_down_ids": [],
            "created_chat_reply_message_ids": [],
            "replied_chat_message_ids": [],
            "created_operator_chat_message_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            agent = _resolve_action_agent(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["agent_id"] = agent.id
            runtime = state_model.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            action_id = f"action-{uuid4().hex}"
            if not valid:
                _mark_action_failure(
                    state_model,
                    runtime=runtime,
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                    action=normalized_action,
                    error=error or "invalid action",
                    action_id=action_id,
                    result=result,
                )
                return state_model
            if normalized_dispatch_id and runtime.active_dispatch_id:
                if normalized_dispatch_id != runtime.active_dispatch_id:
                    _mark_action_failure(
                        state_model,
                        runtime=runtime,
                        agent_id=agent.id,
                        dispatch_id=normalized_dispatch_id,
                        action=normalized_action,
                        error="dispatch_id does not match active team runtime lease",
                        action_id=action_id,
                        result=result,
                    )
                    return state_model
            active_stand_downs = _active_stand_downs_for_agent(state_model, agent.id)
            if active_stand_downs and _non_empty_action_fields(normalized_action):
                reason = _stand_down_error_detail(active_stand_downs)
                _mark_action_failure(
                    state_model,
                    runtime=runtime,
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                    action=normalized_action,
                    error=f"agent {agent.id} is stood down: {reason}",
                    action_id=action_id,
                    result=result,
                )
                return state_model

            progress = False
            context_rotation_activity = False
            projects_to_recalc: set[str] = set()

            def _mark_progress(*, context_activity: bool = True) -> None:
                nonlocal progress, context_rotation_activity
                progress = True
                if context_activity:
                    context_rotation_activity = True

            for index, stand_down_request in enumerate(
                _list_of_mappings(normalized_action.get("stand_downs"), "stand_downs")
            ):
                try:
                    stand_down_id = _apply_superior_stand_down(
                        state_model,
                        actor=agent,
                        request=stand_down_request,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "stand_downs", index, exc)
                    continue
                result["created_stand_down_ids"].append(stand_down_id)
                _mark_progress()

            for index, revoke_request in enumerate(
                _list_of_mappings(
                    normalized_action.get("stand_down_revocations"),
                    "stand_down_revocations",
                )
            ):
                try:
                    revoked_ids = _apply_superior_stand_down_revocation(
                        state_model,
                        actor=agent,
                        request=revoke_request,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "stand_down_revocations", index, exc)
                    continue
                result["revoked_stand_down_ids"].extend(revoked_ids)
                _mark_progress()

            for index, update in enumerate(
                _list_of_mappings(normalized_action.get("task_updates"), "task_updates")
            ):
                try:
                    task_id = _required_text(update.get("task_id"), "task_id")
                    task = _require_present(state_model.tasks, task_id, "task")
                    is_owner = task.owner_agent_id == agent.id
                    raw_state = _optional_text(update.get("state"))
                    priority = update.get("priority")
                    title = _optional_text(update.get("title"))
                    description = _optional_text(update.get("description"))
                    has_owner_only_edit = (
                        raw_state is not None
                        or priority is not None
                        or title is not None
                        or description is not None
                    )
                    has_dependency_edit = "dependency_task_ids" in update
                    # State, priority, title, and description remain owner-only.
                    if has_owner_only_edit and not is_owner:
                        result["errors"].append(f"agent {agent.id} cannot update task {task_id}")
                        continue
                    new_dependency_task_ids: list[str] | None = None
                    if has_dependency_edit:
                        graph_permitted = (
                            is_owner
                            or _supervises(state_model, agent.id, task.owner_agent_id)
                            or _is_project_lead_for(state_model, agent.id, task.project_id)
                            or _is_team_manager(state_model, agent)
                        )
                        if not graph_permitted:
                            result["errors"].append(
                                f"agent {agent.id} is not permitted to edit the task graph "
                                f"for task {task_id}"
                            )
                            continue
                        new_dependency_task_ids = _string_list(
                            update.get("dependency_task_ids"), "dependency_task_ids"
                        )
                        graph_error = _task_graph_edit_error(
                            state_model,
                            task_id=task.id,
                            project_id=task.project_id,
                            parent_task_id=task.parent_task_id,
                            dependency_task_ids=new_dependency_task_ids,
                        )
                        if graph_error is not None:
                            result["errors"].append(graph_error)
                            continue
                    changed = False
                    now = utc_now()
                    if raw_state is not None:
                        _set_task_state(task, TeamTaskState(raw_state), now=now)
                        projects_to_recalc.add(task.project_id)
                        changed = True
                    if priority is not None:
                        task.priority = int(priority)
                        changed = True
                    if title is not None:
                        task.title = title
                        changed = True
                    if description is not None:
                        task.description = description
                        changed = True
                    if new_dependency_task_ids is not None:
                        task.dependency_task_ids = new_dependency_task_ids
                        changed = True
                    if changed:
                        task.updated_at = now
                        _mark_progress()
                        result["updated_task_ids"].append(task.id)
                except ValueError as exc:
                    _append_action_item_error(result, "task_updates", index, exc)

            task_creation_plans: list[_TaskCreationPlan] = []
            reserved_task_ids: set[str] = set()
            for index, creation in enumerate(
                _list_of_mappings(normalized_action.get("task_creations"), "task_creations")
            ):
                try:
                    plan = _plan_task_creation(
                        state_model,
                        actor=agent,
                        creation=creation,
                        result=result,
                        reserved_task_ids=reserved_task_ids,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "task_creations", index, exc)
                    continue
                if plan is None:
                    continue
                reserved_task_ids.add(plan.task_id)
                task_creation_plans.append(plan)

            graph_errors = _task_creation_batch_graph_errors(
                state_model,
                task_creation_plans,
                actor=agent,
            )
            for plan in task_creation_plans:
                graph_error = graph_errors.get(plan.task_id)
                if graph_error is not None:
                    result["errors"].append(graph_error)
                    continue
                created_id = _apply_task_creation_plan(
                    state_model,
                    plan=plan,
                    actor=agent,
                )
                result["created_task_ids"].append(created_id)
                projects_to_recalc.add(state_model.tasks[created_id].project_id)
                _mark_progress()

            for index, question in enumerate(
                _list_of_mappings(normalized_action.get("questions"), "questions")
            ):
                try:
                    recipient_id = (
                        _optional_text(question.get("recipient_agent_id"))
                        or agent.reports_to_agent_id
                    )
                    upward_error = _upward_recipient_error(agent, recipient_id, "questions")
                    if upward_error is not None:
                        result["errors"].append(upward_error)
                        continue
                    response_requested = _response_requested_from_item(
                        question,
                        default=True,
                    )
                    priority = _priority_from_item(
                        question,
                        default=TeamInboxPriority.NORMAL,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "questions", index, exc)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
                    task_id=_optional_text(question.get("task_id")),
                    item_type=TeamInboxItemType.QUESTION,
                    response_requested=response_requested,
                    priority=priority,
                    payload={
                        key: value
                        for key, value in question.items()
                        if key
                        not in {
                            "recipient_agent_id",
                            "task_id",
                            "response_requested",
                            "priority",
                        }
                    },
                )
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                _mark_progress()

            for index, report in enumerate(
                _list_of_mappings(
                    normalized_action.get("status_reports"),
                    "status_reports",
                )
            ):
                try:
                    recipient_id = (
                        _optional_text(report.get("recipient_agent_id"))
                        or agent.reports_to_agent_id
                    )
                    upward_error = _upward_recipient_error(agent, recipient_id, "status_reports")
                    if upward_error is not None:
                        result["errors"].append(upward_error)
                        continue
                    response_requested = _response_requested_from_item(
                        report,
                        default=False,
                    )
                    priority = _priority_from_item(
                        report,
                        default=TeamInboxPriority.LOW,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "status_reports", index, exc)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
                    task_id=_optional_text(report.get("task_id")),
                    item_type=TeamInboxItemType.PROGRESS_REPORT,
                    response_requested=response_requested,
                    priority=priority,
                    payload={
                        key: value
                        for key, value in report.items()
                        if key
                        not in {
                            "recipient_agent_id",
                            "task_id",
                            "response_requested",
                            "priority",
                        }
                    },
                )
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                _mark_progress(context_activity=False)

            for index, status_request in enumerate(
                _list_of_mappings(normalized_action.get("status_requests"), "status_requests")
            ):
                try:
                    recipient_id = _required_text(
                        status_request.get("recipient_agent_id"), "recipient_agent_id"
                    )
                    if not _is_direct_subordinate(agent, recipient_id):
                        result["errors"].append(
                            f"agent {agent.id} can only request status from a direct subordinate"
                        )
                        continue
                    inbox_id = f"inbox-{uuid4().hex}"
                    item = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=recipient_id,
                        sender_agent_id=agent.id,
                        task_id=_optional_text(status_request.get("task_id")),
                        item_type=TeamInboxItemType.STATUS_REQUEST,
                        response_requested=_response_requested_from_item(
                            status_request,
                            default=True,
                        ),
                        priority=_priority_from_item(
                            status_request,
                            default=TeamInboxPriority.NORMAL,
                        ),
                        payload={
                            key: value
                            for key, value in status_request.items()
                            if key
                            not in {
                                "recipient_agent_id",
                                "task_id",
                                "response_requested",
                                "priority",
                            }
                        },
                    )
                    state_model.inbox[item.id] = item
                    result["created_status_request_ids"].append(item.id)
                    _mark_progress()
                except ValueError as exc:
                    _append_action_item_error(result, "status_requests", index, exc)

            for index, reply in enumerate(
                _list_of_mappings(normalized_action.get("chat_replies"), "chat_replies")
            ):
                try:
                    created_id = _apply_chat_reply(
                        state_model,
                        actor=agent,
                        reply=reply,
                        result=result,
                    )
                except ValueError as exc:
                    error_detail = f"chat_replies[{index}]: {exc}"
                    _append_action_item_error(result, "chat_replies", index, exc)
                    _mark_chat_dispatch_message_failed(
                        state_model,
                        dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id,
                        error=error_detail,
                    )
                    continue
                result["created_chat_reply_message_ids"].append(created_id)
                _mark_progress()

            for index, chat_message in enumerate(
                _list_of_mappings(
                    normalized_action.get("operator_chat_messages"),
                    "operator_chat_messages",
                )
            ):
                try:
                    created_id = _apply_operator_chat_message(
                        state_model,
                        actor=agent,
                        message=chat_message,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "operator_chat_messages", index, exc)
                    continue
                result["created_operator_chat_message_ids"].append(created_id)
                _mark_progress()

            for escalation in _list_of_mappings(
                normalized_action.get("operator_escalations"), "operator_escalations"
            ):
                if _apply_operator_escalation(
                    state_model,
                    actor=agent,
                    escalation=escalation,
                    result=result,
                ):
                    _mark_progress()

            for index, deliverable_payload in enumerate(
                _list_of_mappings(normalized_action.get("deliverables"), "deliverables")
            ):
                try:
                    task_id = _required_text(deliverable_payload.get("task_id"), "task_id")
                    task = _require_present(state_model.tasks, task_id, "task")
                    if task.owner_agent_id != agent.id:
                        result["errors"].append(f"agent {agent.id} cannot deliver task {task_id}")
                        continue
                    deliverable_id = _optional_text(deliverable_payload.get("id"))
                    if deliverable_id is None:
                        deliverable_id = _generated_id(
                            prefix="deliverable",
                            labels=[task_id, deliverable_payload.get("summary")],
                            existing_ids=state_model.deliverables,
                        )
                    artifact_refs = _string_list(deliverable_payload.get("artifact_refs"), "artifact_refs")
                    artifact_space_ref = _optional_text(deliverable_payload.get("artifact_space_ref"))
                    if artifact_space_ref:
                        artifact_refs = _append_unique(artifact_refs, artifact_space_ref)
                    git_refs = _deliverable_git_refs_from_payload(
                        deliverable_payload.get("git_refs")
                    )
                    supersedes_deliverable_id = _optional_text(
                        deliverable_payload.get("supersedes_deliverable_id")
                    )
                    if (
                        supersedes_deliverable_id is not None
                        and deliverable_id == supersedes_deliverable_id
                    ):
                        raise ValueError(
                            "deliverable id cannot match supersedes_deliverable_id"
                        )
                    superseded_deliverable = _superseded_deliverable_for_rework(
                        state_model,
                        task=task,
                        producer_agent_id=agent.id,
                        supersedes_deliverable_id=supersedes_deliverable_id,
                    )
                    deliverable = Deliverable(
                        id=deliverable_id,
                        task_id=task_id,
                        producer_agent_id=agent.id,
                        summary=_required_text(deliverable_payload.get("summary"), "summary"),
                        artifact_refs=artifact_refs,
                        git_refs=git_refs,
                        state=TeamRecordState.PENDING,
                        supersedes_deliverable_id=supersedes_deliverable_id,
                    )
                    state_model.deliverables[deliverable.id] = deliverable
                    if superseded_deliverable is not None:
                        superseded_deliverable.superseded_by_deliverable_id = (
                            deliverable.id
                        )
                    task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
                    now = utc_now()
                    _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
                    task.updated_at = now
                    projects_to_recalc.add(task.project_id)
                    result["created_deliverable_ids"].append(deliverable.id)
                    if agent.reports_to_agent_id is not None:
                        inbox_id = f"inbox-{uuid4().hex}"
                        state_model.inbox[inbox_id] = AgentInboxItem(
                            id=inbox_id,
                            recipient_agent_id=agent.reports_to_agent_id,
                            sender_agent_id=agent.id,
                            task_id=task_id,
                            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                            response_requested=True,
                            priority=TeamInboxPriority.LOW,
                            payload={
                                "deliverable_id": deliverable.id,
                                "summary": deliverable.summary,
                                "artifact_refs": list(deliverable.artifact_refs),
                            },
                        )
                        result["created_inbox_item_ids"].append(inbox_id)
                    _mark_progress()
                except ValueError as exc:
                    _append_action_item_error(result, "deliverables", index, exc)

            for index, review_request in enumerate(
                _list_of_mappings(normalized_action.get("review_requests"), "review_requests")
            ):
                try:
                    created_id = _apply_review_request(
                        state_model,
                        actor=agent,
                        request=review_request,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "review_requests", index, exc)
                    continue
                if created_id is not None:
                    result["created_review_request_ids"].append(created_id)
                    _mark_progress()

            for index, decision in enumerate(
                _list_of_mappings(normalized_action.get("review_decisions"), "review_decisions")
            ):
                try:
                    decided_id = _apply_review_decision(
                        state_model,
                        actor=agent,
                        decision=decision,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "review_decisions", index, exc)
                    continue
                if decided_id is not None:
                    result["decided_review_request_ids"].append(decided_id)
                    _mark_progress()

            for index, goal_update in enumerate(
                _list_of_mappings(normalized_action.get("goal_updates"), "goal_updates")
            ):
                try:
                    updated_id = _apply_goal_update(
                        state_model,
                        actor=agent,
                        update=goal_update,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "goal_updates", index, exc)
                    continue
                if updated_id is not None:
                    result["updated_goal_ids"].append(updated_id)
                    _mark_progress()

            for index, promotion in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_review_promotions"), "git_review_promotions"
                )
            ):
                try:
                    promotion_id = _apply_git_review_promotion(
                        state_model,
                        actor=agent,
                        request=promotion,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_review_promotions", index, exc)
                    continue
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    _mark_progress()

            for index, feature_update in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_feature_updates"), "git_feature_updates"
                )
            ):
                try:
                    promotion_id = _apply_git_feature_update(
                        state_model,
                        actor=agent,
                        update=feature_update,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_feature_updates", index, exc)
                    continue
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    _mark_progress()

            for index, release_request in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_release_requests"), "git_release_requests"
                )
            ):
                try:
                    release_id = _apply_git_release_request(
                        state_model,
                        actor=agent,
                        request=release_request,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_release_requests", index, exc)
                    continue
                if release_id is not None:
                    result["release_package_ids"].append(release_id)
                    _mark_progress()

            for inbox_item_id in _string_list(
                normalized_action.get("handled_inbox_item_ids"),
                "handled_inbox_item_ids",
            ):
                item = state_model.inbox.get(inbox_item_id)
                if item is None or item.recipient_agent_id != agent.id:
                    result["errors"].append(f"agent {agent.id} cannot handle inbox item {inbox_item_id}")
                    continue
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = utc_now()
                result["handled_inbox_item_ids"].append(inbox_item_id)
                _mark_progress()

            ephemeral_requests = _list_of_mappings(
                normalized_action.get("ephemeral_requests"),
                "ephemeral_requests",
            )
            if ephemeral_requests:
                try:
                    _create_ephemeral_assignments(
                        state_model,
                        parent=agent,
                        requests=ephemeral_requests,
                        dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id,
                        result=result,
                    )
                    _mark_progress()
                except ValueError as exc:
                    result["errors"].append(f"ephemeral_requests: {exc}")

            for index, acceptance in enumerate(
                _list_of_mappings(
                    normalized_action.get("ephemeral_acceptances"),
                    "ephemeral_acceptances",
                )
            ):
                try:
                    assignment_id = _required_text(
                        acceptance.get("assignment_id"), "assignment_id"
                    )
                    assignment = _require_present(
                        state_model.ephemeral_assignments,
                        assignment_id,
                        "ephemeral assignment",
                    )
                    if assignment.parent_agent_id != agent.id:
                        result["errors"].append(
                            f"agent {agent.id} cannot accept ephemeral assignment {assignment_id}"
                        )
                        continue
                    accepted = _bool_from_value(acceptance.get("accepted"), default=True)
                    _apply_ephemeral_acceptance(
                        state_model,
                        assignment=assignment,
                        accepted=accepted,
                        deliverable_id=_optional_text(acceptance.get("deliverable_id")),
                        rejection_reason=_optional_text(acceptance.get("rejection_reason")),
                    )
                    if accepted:
                        result["accepted_ephemeral_assignment_ids"].append(assignment.id)
                    else:
                        result["rejected_ephemeral_assignment_ids"].append(assignment.id)
                    _mark_progress()
                except ValueError as exc:
                    _append_action_item_error(result, "ephemeral_acceptances", index, exc)

            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, project_id)

            record = AgentActionRecord(
                id=action_id,
                agent_id=agent.id,
                dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                action=normalized_action,
                success=not result["errors"],
                error="; ".join(result["errors"]) if result["errors"] else None,
            )
            state_model.action_records[record.id] = record
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.no_progress_count = 0 if progress else runtime.no_progress_count + 1
            completed_at = utc_now()
            if context_rotation_activity:
                runtime.last_progress_at = completed_at
            runtime.updated_at = completed_at
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=agent.id,
                now=runtime.updated_at,
            )
            result["success"] = progress or not result["errors"]
            result["applied"] = progress
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["ephemeral_events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        return state, result

    def record_missing_action_recovery_prompted(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        action_artifact_path: str,
        error: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_path = _required_text(action_artifact_path, "action_artifact_path")
        result: dict[str, Any] = {
            "recorded": False,
            "already_prompted": False,
            "agent_id": None,
            "action_record_id": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            agent = _agent_for_session(state_model, session_id=normalized_session_id)
            result["agent_id"] = agent.id
            runtime = state_model.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            if runtime.active_dispatch_id != normalized_dispatch_id:
                expected = runtime.active_dispatch_id or "none"
                result["error"] = (
                    "cannot recover missing Team action artifact because dispatch_id "
                    f"{normalized_dispatch_id} does not match active lease {expected}"
                )
                return state_model
            for record in state_model.action_records.values():
                if (
                    record.agent_id == agent.id
                    and record.dispatch_id == normalized_dispatch_id
                    and record.error is not None
                    and MISSING_ACTION_RECOVERY_MARKER in record.error
                ):
                    result["already_prompted"] = True
                    result["action_record_id"] = record.id
                    return state_model
            action_id = f"action-{uuid4().hex}"
            recovery_error = (
                f"{MISSING_ACTION_RECOVERY_MARKER}: {normalized_path}; "
                f"original error: {error}"
            )
            state_model.action_records[action_id] = AgentActionRecord(
                id=action_id,
                agent_id=agent.id,
                dispatch_id=normalized_dispatch_id,
                action={
                    "agent_id": agent.id,
                    "dispatch_id": normalized_dispatch_id,
                },
                success=False,
                error=recovery_error,
            )
            runtime.updated_at = utc_now()
            result["recorded"] = True
            result["action_record_id"] = action_id
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def record_git_promotion_result(
        self,
        *,
        promotion_id: str,
        success: bool = True,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        commit_sha: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_promotion_id = _required_text(promotion_id, "promotion_id")
        normalized_outputs = dict(outputs or {})
        normalized_error = _optional_text(error)
        normalized_commit_sha = _optional_text(commit_sha)
        result: dict[str, Any] = {
            "success": False,
            "promotion_id": normalized_promotion_id,
            "promotion_state": None,
            "deliverable_id": None,
            "release_package_id": None,
            "created_deliverable_ids": [],
            "created_inbox_item_ids": [],
            "events": [],
            "git_promotion_event_ids": [],
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            promotion = _require_present(
                state_model.git_promotions,
                normalized_promotion_id,
                "git promotion",
            )
            promotion.outputs = normalized_outputs
            if normalized_commit_sha is not None:
                promotion.commit_sha = normalized_commit_sha
            if success:
                promotion.state = "succeeded"
                promotion.error = None
                _attach_promotion_success(
                    state_model,
                    promotion=promotion,
                    commit_sha=normalized_commit_sha,
                    result=result,
                )
            else:
                promotion.state = "failed"
                promotion.error = normalized_error or "git promotion failed"
                result["error"] = promotion.error
                _attach_promotion_failure(
                    state_model,
                    promotion=promotion,
                    result=result,
                )
            promotion.updated_at = utc_now()
            _recalculate_project_status(state_model, promotion.project_id)
            result["promotion_state"] = promotion.state
            result["deliverable_id"] = promotion.deliverable_id
            result["release_package_id"] = promotion.release_package_id
            result["success"] = True
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def mark_release_published(
        self,
        *,
        release_package_id: str,
        success: bool = True,
        canonical_branch: str | None = None,
        published_commit_sha: str | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_id = _required_text(release_package_id, "release_package_id")
        normalized_canonical = _optional_text(canonical_branch)
        normalized_commit = _optional_text(published_commit_sha)
        normalized_error = _optional_text(error)
        # outputs are accepted for parity with the git effect contract; they are
        # not persisted on the release package today.
        _ = dict(outputs or {})
        result: dict[str, Any] = {
            "success": False,
            "release_package_id": normalized_id,
            "state": None,
            "created_inbox_item_ids": [],
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            release = _require_present(
                state_model.release_packages,
                normalized_id,
                "release package",
            )
            now = utc_now()
            if success:
                release.state = TeamRecordState.ACCEPTED
                if normalized_canonical is not None:
                    release.canonical_branch = normalized_canonical
                if normalized_commit is not None:
                    release.published_commit_sha = normalized_commit
                release.published_at = now
                release.error = None
            else:
                release.state = TeamRecordState.BLOCKED
                release.error = normalized_error or "release publication failed"
                result["error"] = release.error
                recipient_id = _release_failure_recipient(state_model, release)
                if recipient_id is not None and recipient_id in state_model.agents:
                    inbox_id = f"inbox-{uuid4().hex}"
                    state_model.inbox[inbox_id] = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=recipient_id,
                        sender_agent_id=None,
                        item_type=TeamInboxItemType.PROGRESS_REPORT,
                        response_requested=True,
                        priority=TeamInboxPriority.NORMAL,
                        payload={
                            "kind": "release_publish_failed",
                            "release_package_id": release.id,
                            "project_id": release.project_id,
                            "error": release.error,
                        },
                    )
                    result["created_inbox_item_ids"].append(inbox_id)
            release.updated_at = now
            _recalculate_project_status(state_model, release.project_id)
            result["state"] = release.state.value
            result["success"] = True
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result


def _require_present(collection: dict[str, Any], key: str, label: str) -> Any:
    if key not in collection:
        raise ValueError(f"{label} {key} does not exist")
    return collection[key]


def _require_absent(collection: dict[str, Any], key: str, label: str) -> None:
    if key in collection:
        raise ValueError(f"{label} {key} already exists")


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
        _slug_id_part(label, fallback="")
        for label in labels
        if str(label or "").strip()
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
        item = permission if isinstance(permission, RolePermission) else RolePermission(str(permission))
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
        blockers.append(
            "has subordinate agents " + ", ".join(sorted(agent.subordinate_agent_ids))
        )
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
        if item.sender_agent_id == agent.id:
            blockers.append(f"sender of inbox item {item.id}")
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
    payload["effective_role_permissions"] = [permission.value for permission in effective_permissions]
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
            ", ".join(
                _team_reference_label(state, team_id)
                for team_id in group.member_team_ids
            )
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
    signals.sort(key=lambda item: (item["type"] != GovernanceAgendaItemType.COST_CAP_REACHED.value, item["team_id"]))
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
            raise ValueError(
                f"template {template.id} has unresolved role ordering: {unresolved}"
            )
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


def _active_tasks_for_agent(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal = {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}
    tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id and task.state not in terminal
    ]
    tasks.sort(key=lambda item: (item.priority, item.created_at, item.id))
    return tasks


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
        if _newer_than_rotation(assignment.created_at) or _newer_than_rotation(assignment.updated_at):
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
    if (
        not runtime.context_rotation_pending
        or runtime.context_rotation_reason not in {"idle", "every_wake"}
    ):
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
            lines.append(
                f"- {task.id}: {task.state.value}, priority {task.priority}, {task.title}"
            )
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
        [
            record
            for record in state.action_records.values()
            if record.agent_id == agent.id
        ],
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
    records = [
        record
        for record in state.action_records.values()
        if record.agent_id == agent_id
    ]
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


def _correction_hints(error: str) -> list[str]:
    normalized = error.casefold()
    hints: list[str] = []
    if "must not start with 'refs/'" in normalized:
        hints.append(
            "Use bare branch names like agents/... or team/..., not refs/heads/...."
        )
    if "cannot deliver task" in normalized:
        hints.append("Only the task owner can create deliverables for that task.")
    if "artifact_paths must contain at least one path" in normalized:
        hints.append(
            "For Git-only deliverables, omit artifact_paths; otherwise list staged "
            "paths under /artifacts."
        )
    if "artifact file not found" in normalized or MISSING_ACTION_RECOVERY_MARKER in normalized:
        hints.append(
            "Before finishing any Team wake, write /artifacts/team-action.json. "
            "Use the seeded team-finish helper to fill agent_id and dispatch_id."
        )
    if "supersedes_deliverable_id" in normalized:
        hints.append(
            "When revising a rejected deliverable, set supersedes_deliverable_id to "
            "the rejected deliverable id from the review_rejected inbox payload."
        )
    if "has neither a feature branch nor artifact refs" in normalized:
        hints.append(
            "Feature-promote Git deliverables before release: publish a review branch if "
            "needed, then use git_feature_updates with source_review_branches and retry "
            "git_release_requests only after git_refs.feature_branch is present."
        )
    if "assignment_id must be a non-empty string" in normalized:
        hints.append(
            "Use the assignment_id shape for ephemeral promotions, or the "
            "deliverable_id shape for persistent deliverables."
        )
    if "already exists" in normalized:
        hints.append(
            "Do not recreate existing ids; reference existing records or choose a new id."
        )
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
        raise ValueError(
            f"agent {actor.id} cannot reply to conversation {conversation_id}"
        )
    source_message = _require_present(
        state.conversation_messages,
        source_message_id,
        "conversation message",
    )
    if source_message.conversation_id != conversation.id:
        raise ValueError(
            f"message {source_message_id} is not in conversation {conversation_id}"
        )
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
            if item.agent_id == actor.id
            and item.operator_user_id == recipient_operator_user_id
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
        result["errors"].append(
            f"agent {actor.id} cannot create tasks outside its team"
        )
        return None
    actor_can_target_project = _is_project_lead_for(
        state, actor.id, project_id
    ) or _is_team_manager(state, actor)
    if not agent_has_project_assignment(state, owner.id, project_id) and not actor_can_target_project:
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
        or (
            _is_team_manager(state, actor)
            and _is_project_lead_for(state, owner.id, project_id)
        )
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to create tasks for {owner_id}"
        )
        return None
    raw_state = _optional_text(creation.get("state"))
    if raw_state is not None:
        try:
            task_state = TeamTaskState(raw_state)
        except ValueError:
            result["errors"].append(f"task creation has invalid state {raw_state}")
            return None
        if task_state not in _NON_TERMINAL_TASK_STATES:
            result["errors"].append(
                f"task creation cannot start in terminal state {raw_state}"
            )
            return None
    else:
        task_state = TeamTaskState.PENDING
    explicit_task_id = _optional_text(creation.get("task_id"))
    legacy_task_id = _optional_text(creation.get("id"))
    if explicit_task_id is not None and legacy_task_id is not None and explicit_task_id != legacy_task_id:
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
    dependency_task_ids = _string_list(
        creation.get("dependency_task_ids"), "dependency_task_ids"
    )
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
    if plan.task_state in _NON_TERMINAL_TASK_STATES:
        _mark_agent_work_due(state, agent_id=plan.owner_id, now=now)
    return plan.task_id


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
        task_owners = {
            task_id: task.owner_agent_id for task_id, task in state.tasks.items()
        }
        task_states = {task_id: task.state for task_id, task in state.tasks.items()}
        parent_map = {
            task_id: task.parent_task_id for task_id, task in state.tasks.items()
        }
        dependency_map = {
            task_id: list(task.dependency_task_ids)
            for task_id, task in state.tasks.items()
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
            return (
                f"task dependency {dependency_id} belongs to project "
                f"{dependency_project_id}"
            )
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
        result["errors"].append(
            f"agent {actor.id} is not permitted to send operator escalations"
        )
        return False
    title = _required_text(escalation.get("title"), "title")
    message = _required_text(escalation.get("message"), "message")
    urgency = _optional_text(escalation.get("urgency")) or "normal"
    project_id = _optional_text(escalation.get("project_id"))
    task_id = _optional_text(escalation.get("task_id"))
    team = state.teams.get(actor.team_id)
    if team is None:
        result["errors"].append(
            f"team {actor.team_id} does not exist"
        )
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
        target = (
            _optional_text(team.operator_communication_target_id)
        )
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
                "idempotency_key": (
                    f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:{escalation_id}"
                ),
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
        raise ValueError(
            f"agent {actor.id} can only stand down a direct subordinate"
        )
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
        raise ValueError(
            f"agent {actor.id} can only revoke stand-downs for a direct subordinate"
        )
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
            return (
                f"task parent {parent_task_id} belongs to project {parent.project_id}"
            )
    for dependency_id in dependency_task_ids:
        dependency = state.tasks.get(dependency_id)
        if dependency is None:
            return f"task references missing dependency {dependency_id}"
        if dependency.project_id != project_id:
            return (
                f"task dependency {dependency_id} belongs to project "
                f"{dependency.project_id}"
            )
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
            existing_id: list(task.dependency_task_ids)
            for existing_id, task in state.tasks.items()
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
        return None, f"review profile {profile_id} is not in the same team as reviewer {reviewer.id}"
    if profile.project_id is not None and profile.project_id != project_id:
        return None, f"review profile {profile_id} does not apply to project {project_id}"
    if profile.applicable_role_ids and reviewer.role_id not in profile.applicable_role_ids:
        return None, f"review profile {profile_id} does not apply to reviewer role {reviewer.role_id}"
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
        result["errors"].append(
            f"reviewer {reviewer_id} is not in the same team as {actor.id}"
        )
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
    state.inbox[inbox_id] = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=reviewer_id,
        sender_agent_id=actor.id,
        task_id=deliverable.task_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=True,
        priority=TeamInboxPriority.NORMAL,
        payload=payload,
    )
    result["created_inbox_item_ids"].append(inbox_id)
    return review.id


def _apply_review_decision(
    state: TeamState,
    *,
    actor: PersistentAgent,
    decision: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    review_request_id = _required_text(
        decision.get("review_request_id"), "review_request_id"
    )
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
            return None, (
                f"review decision criterion {criterion_id} has invalid state {raw_state}"
            )
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
        inbox_id = f"inbox-{uuid4().hex}"
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=deliverable.producer_agent_id,
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
        if result is not None:
            result["created_inbox_item_ids"].append(inbox_id)
        _mark_agent_work_due(
            state,
            agent_id=deliverable.producer_agent_id,
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
        result["errors"].append(
            f"goal {goal_id} does not exist on project {project_id}"
        )
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
        result["errors"].append(
            f"project {project.id} has no git configuration for promotions"
        )
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
            f"supersedes_deliverable_id references missing deliverable "
            f"{supersedes_deliverable_id}"
        )
    if deliverable.task_id != task.id:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} belongs to task "
            f"{deliverable.task_id}, not {task.id}"
        )
    if deliverable.producer_agent_id != producer_agent_id:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} was produced by "
            f"{deliverable.producer_agent_id}, not {producer_agent_id}"
        )
    if deliverable.state != TeamRecordState.REJECTED:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} is not rejected"
        )
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
    source_branch = assignment.agent_branch
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
        _optional_text(request.get("source_branch"))
        or deliverable.git_refs.agent_branch
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
        result["errors"].append(
            f"git feature update references missing task {task_id}"
        )
        return None
    if task.owner_agent_id != actor.id and not _supervises(
        state, actor.id, task.owner_agent_id
    ):
        result["errors"].append(
            f"agent {actor.id} is not permitted to consolidate task {task_id}"
        )
        return None
    project = state.projects.get(task.project_id)
    if project is None:
        result["errors"].append(
            f"git feature update references missing project {task.project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    raw_sources = _string_list(
        update.get("source_review_branches"), "source_review_branches"
    )
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
        result["errors"].append(
            f"git release request references missing project {project_id}"
        )
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
            feature_branches = _append_unique(feature_branches, feature)
        elif not deliverable.artifact_refs:
            result["errors"].append(
                f"deliverable {deliverable_id} has neither a feature branch nor artifact refs; "
                "Git release requests require feature-promoted deliverables. Publish a review "
                "branch first if needed, then use git_feature_updates with source_review_branches "
                "so the deliverable has git_refs.feature_branch before retrying git_release_requests"
            )
            valid = False
        for artifact_ref in deliverable.artifact_refs:
            artifact_refs = _append_unique(artifact_refs, artifact_ref)
    if not valid:
        return None
    release_id = _optional_text(request.get("id")) or f"release-{uuid4().hex}"
    if release_id in state.release_packages:
        result["errors"].append(f"release package {release_id} already exists")
        return None
    target_release_branch: str | None = None
    target_canonical_branch: str | None = None
    git = None
    if feature_branches:
        git = _project_git_or_error(project, result)
        if git is None:
            return None
        target_release_branch = validate_branch_name(
            _optional_text(request.get("release_branch"))
            or release_branch(team_id, release_id)
        )
        target_canonical_branch = validate_branch_name(
            _optional_text(request.get("canonical_branch"))
            or canonical_release_branch(project, release_id)
        )
    promotion_ids: list[str] = []
    now = utc_now()
    # Internal feature branches are consolidated into a release branch, which is then
    # pushed to the canonical remote after consolidation succeeds. Only create git
    # promotions when there is at least one feature branch to consolidate; pure
    # artifact deliverables need no push.
    if feature_branches:
        assert git is not None
        assert target_release_branch is not None
        release_promotion_id = f"promotion-{uuid4().hex}"
        state.git_promotions[release_promotion_id] = GitPromotionRecord(
            id=release_promotion_id,
            project_id=project.id,
            release_package_id=release_id,
            actor_agent_id=actor_agent_id,
            source_branch=feature_branches[0],
            target_branch=target_release_branch,
            promotion_kind="release",
        )
        promotion_ids.append(release_promotion_id)
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
    if feature_branches:
        _emit_git_promotion_event(
            result,
            promotion_id=promotion_ids[0],
            payload={
                "promotion_id": promotion_ids[0],
                "promotion_kind": "release",
                "project_id": project.id,
                "release_package_id": release_id,
                "host_repo_path": git.host_repo_path,
                "base_branch": git.base_branch,
                "source_branches": list(feature_branches),
                "target_branch": target_release_branch,
            },
        )
        result["git_promotion_event_ids"].extend(promotion_ids)
    projects_to_recalc.add(project_id)
    return release_id


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
    elif kind == "feature" and promotion.task_id is not None and promotion.task_id in state.tasks:
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
    project_tasks = [
        task for task in state.tasks.values() if task.project_id == project_id
    ]
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
    active_deliverable_ids = {
        deliverable.id for deliverable in active_project_deliverables
    }
    project_reviews = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id in active_deliverable_ids
    ]
    project_releases = [
        release
        for release in state.release_packages.values()
        if release.project_id == project_id
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
            deliverable.state in pending_or_blocked
            for deliverable in active_project_deliverables
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
            f"agent {parent.id} ephemeral request would exceed max_concurrent="
            f"{max_concurrent}"
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
    assignment_id = (
        _optional_text(action.get("ephemeral_assignment_id"))
        or _optional_text(action.get("assignment_id"))
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
        assignment.updated_at = utc_now()
        return deliverable.id

    assignment.state = EphemeralAssignmentState.REJECTED
    assignment.error = rejection_reason
    assignment.updated_at = utc_now()
    return None


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
    team_metadata = dict(metadata.get("team_addon")) if isinstance(metadata.get("team_addon"), dict) else {}
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
            "allowed_tools": list(assignment.allowed_tools),
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
        "action_artifact_path": "/artifacts/team-action.json",
    }
    schema = {
        "ephemeral_assignment_id": assignment.id,
        "summary": "",
        "artifact_refs": [],
        "task_notes": "",
        "error": None,
    }
    return (
        "Complete this scoped ephemeral team-agent assignment. "
        "You are a disposable worker for one parent agent; do not spawn other agents, "
        "ask hierarchy questions, or update unrelated team state.\n\n"
        f"Assignment prompt:\n{assignment.prompt}\n\n"
        f"Assignment context JSON:\n{_json_dumps(context)}\n\n"
        "Before finishing, write a UTF-8 JSON action object to "
        "/artifacts/team-action.json using this shape:\n"
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


__all__ = [
    "ADDON_ID",
    "EPHEMERAL_COMPLETED_EVENT_TYPE",
    "EPHEMERAL_REQUESTED_EVENT_TYPE",
    "GIT_PROMOTION_EVENT_TYPE",
    "LEGACY_STATE_FILE",
    "OPERATOR_ESCALATION_EVENT_TYPE",
    "STATE_DB_FILE",
    "STATE_FILE",
    "TeamStateConflictError",
    "TeamStateRepository",
    "ValidationError",
]
