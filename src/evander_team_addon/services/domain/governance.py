from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from ...models import (
    GovernanceAgendaItem,
    GovernanceAgendaItemState,
    GovernanceAgendaItemType,
    GovernanceGoal,
    GovernanceGroup,
    ProjectStatus,
    TeamInboxPriority,
    TeamState,
    TeamTaskState,
)

from .agent_access import _agent_payload
from .common import (
    _decimal_or_none,
    _decimal_or_zero,
    _generated_id,
    _mapping_or_empty,
    _optional_text,
    _priority_sort_value,
)


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
                labels=[
                    governance_group_id,
                    payload.get("name") or payload.get("description"),
                ],
                existing_ids=used_ids,
            )
        if goal_id in used_ids:
            raise ValueError(f"duplicate governance goal id: {goal_id}")
        used_ids.add(goal_id)
        payload["id"] = goal_id
        payload["name"] = (
            _optional_text(payload.get("name"))
            or _optional_text(payload.get("description"))
            or goal_id
        )
        normalized.append(GovernanceGoal.model_validate(payload))
    return normalized


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


__all__ = [
    "_governance_goals_with_generated_ids",
    "_governance_group_payload",
    "_record_governance_membership_change_agenda",
    "_team_reference_label",
    "_governance_member_team_payload",
    "_pending_governance_agenda_for_group",
    "_governance_groups_for_team",
    "_governance_agenda_effective_team_id",
    "_governance_cost_signals",
    "_governance_cost_signal_fingerprints",
    "_governance_blocked_team_signals",
    "_governance_blocked_signal_fingerprints",
]
