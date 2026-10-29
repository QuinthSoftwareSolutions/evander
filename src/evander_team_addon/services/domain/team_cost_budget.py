from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ...models import (
    Team,
    TeamCostBudgetRenewalMode,
    TeamCostBudgetWindow,
    TeamCostBudgetWindowKind,
)
from .common import _optional_text


def _cost_budget_renewal_mode(value: object) -> TeamCostBudgetRenewalMode:
    text = _optional_text(value)
    if text is None:
        return TeamCostBudgetRenewalMode.MANUAL
    try:
        return TeamCostBudgetRenewalMode(text)
    except ValueError:
        allowed = ", ".join(item.value for item in TeamCostBudgetRenewalMode)
        raise ValueError(f"cost_budget_renewal_mode must be one of: {allowed}") from None


def _active_team_cost_budget_window(team: Team) -> TeamCostBudgetWindow | None:
    return next((window for window in team.cost_budget_windows if window.ended_at is None), None)


def _close_active_team_cost_budget_window(team: Team, *, ended_at: datetime) -> None:
    active_window = _active_team_cost_budget_window(team)
    if active_window is not None:
        active_window.ended_at = ended_at


def _open_team_cost_budget_window(
    team: Team,
    *,
    now: datetime,
    kind: TeamCostBudgetWindowKind,
    actor: str,
    reason: str,
) -> TeamCostBudgetWindow:
    if team.max_cost_usd is None:
        raise ValueError("a cost budget window requires max_cost_usd")
    _close_active_team_cost_budget_window(team, ended_at=now)
    window = TeamCostBudgetWindow(
        id=f"team-cost-budget-{uuid4().hex}",
        started_at=now,
        kind=kind,
        actor=actor,
        reason=reason,
        max_cost_usd=team.max_cost_usd,
    )
    team.cost_budget_windows.append(window)
    return window


def _next_utc_month_start(value: datetime) -> datetime:
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if normalized.month == 12:
        return datetime(normalized.year + 1, 1, 1, tzinfo=UTC)
    return datetime(normalized.year, normalized.month + 1, 1, tzinfo=UTC)


def _advance_team_cost_budget_window(team: Team, *, now: datetime) -> bool:
    if (
        team.max_cost_usd is None
        or team.cost_budget_renewal_mode != TeamCostBudgetRenewalMode.MONTHLY_UTC
    ):
        return False
    active_window = _active_team_cost_budget_window(team)
    if active_window is None:
        # A pre-window Team stays lifetime-based until an operator resets it.
        return False
    effective_now = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    next_boundary = _next_utc_month_start(active_window.started_at)
    if active_window.monthly_renewal_not_before is not None:
        next_boundary = max(next_boundary, active_window.monthly_renewal_not_before)
    advanced = False
    while next_boundary <= effective_now:
        _open_team_cost_budget_window(
            team,
            now=next_boundary,
            kind=TeamCostBudgetWindowKind.MONTHLY_RENEWAL,
            actor="system:team-cost-budget-renewal",
            reason="UTC monthly cost budget renewal.",
        )
        next_boundary = _next_utc_month_start(next_boundary)
        advanced = True
    return advanced


def _team_cost_budget_window_advance_due(team: Team, *, now: datetime) -> bool:
    """Return whether advancing this Team would open a new monthly window."""

    if (
        team.max_cost_usd is None
        or team.cost_budget_renewal_mode != TeamCostBudgetRenewalMode.MONTHLY_UTC
    ):
        return False
    active_window = _active_team_cost_budget_window(team)
    if active_window is None:
        return False
    effective_now = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    next_boundary = _next_utc_month_start(active_window.started_at)
    if active_window.monthly_renewal_not_before is not None:
        next_boundary = max(next_boundary, active_window.monthly_renewal_not_before)
    return next_boundary <= effective_now
