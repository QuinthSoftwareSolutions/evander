from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import evander_team_addon.services.admin_service as admin_service_module
from evander_team_addon.models import TeamCostBudgetRenewalMode, TeamCostBudgetWindowKind
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.case_task import TaskGitBaseValidationError


def test_template_materialization_opens_cost_budget_at_facade_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created_at = datetime(2026, 9, 30, 12, tzinfo=UTC)
    monkeypatch.setattr(admin_service_module, "utc_now", lambda: created_at)
    repo = TeamStateRepository(tmp_path)
    state, result = TeamAdminService(repo).materialize_template(
        "auto-dev-squad",
        inputs={
            "team": {
                "name": "Development",
                "max_cost_usd": "10.00",
                "cost_warning_ratio": "0.75",
                "cost_budget_renewal_mode": "monthly_utc",
            },
            "project": {"name": "Product"},
            "roles": {
                key: {"session_id": f"session-{key}"}
                for key in (
                    "manager",
                    "project_lead",
                    "implementer",
                    "reviewer",
                    "release_operator",
                )
            },
        },
        expected_revision=0,
    )

    team = state.teams[result["team_id"]]
    assert result["success"] is True
    assert result["revision"] == 1
    assert team.created_at == created_at
    assert state.projects[result["project_id"]].created_at == created_at
    assert state.tasks[result["task_ids"][0]].created_at == created_at
    assert team.cost_warning_ratio == Decimal("0.75")
    assert team.cost_budget_renewal_mode == TeamCostBudgetRenewalMode.MONTHLY_UTC
    assert len(team.cost_budget_windows) == 1
    budget = team.cost_budget_windows[0]
    assert budget.started_at == created_at
    assert budget.ended_at is None
    assert budget.max_cost_usd == Decimal("10.00")
    assert budget.kind == TeamCostBudgetWindowKind.CONFIGURED
    assert budget.actor == "operator:team-template"


def test_template_materialization_rolls_back_graph_and_binding_after_git_lookup_failure(
    tmp_path: Path,
) -> None:
    class UnavailableManagedGit:
        def __init__(self) -> None:
            self.bindings = {("team_projects", "existing-project")}
            self.changes: list[tuple[str, str]] = []

        def get_repository(self, managed_repo_id, *, directory_path=None):
            if directory_path is not None:
                raise RuntimeError("repository default branch unavailable")
            return {
                "bindings": [
                    {"kind": kind, "binding_id": binding_id}
                    for kind, binding_id in sorted(self.bindings)
                ]
            }

        def bind(self, managed_repo_id, *, kind, binding_id):
            self.bindings.add((kind, binding_id))
            self.changes.append(("bind", binding_id))

        def unbind(self, managed_repo_id, *, kind, binding_id):
            self.bindings.discard((kind, binding_id))
            self.changes.append(("unbind", binding_id))

    repo = TeamStateRepository(tmp_path)
    before = repo.read()
    managed_git = UnavailableManagedGit()
    admin = TeamAdminService(repo, managed_git_admin=managed_git)
    with pytest.raises(TaskGitBaseValidationError, match="default branch unavailable"):
        admin.materialize_template(
            "auto-dev-squad",
            inputs={
                "team": {"name": "Development"},
                "project": {
                    "id": "new-project",
                    "name": "Product",
                    "git": {"managed_repo_id": "mr_11111111111111111111"},
                },
                "roles": {
                    key: {"session_id": f"session-{key}"}
                    for key in (
                        "manager",
                        "project_lead",
                        "implementer",
                        "reviewer",
                        "release_operator",
                    )
                },
            },
            expected_revision=0,
        )

    assert repo.read().model_dump() == before.model_dump()
    assert managed_git.bindings == {("team_projects", "existing-project")}
    assert managed_git.changes == [("bind", "new-project"), ("unbind", "new-project")]
