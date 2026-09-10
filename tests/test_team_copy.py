from __future__ import annotations

from pathlib import Path

import pytest

from evander_team_addon.models import (
    AgentStatus,
    ProjectGoal,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ProjectStatus,
    ReleaseGateKind,
    ReleaseGateStage,
    ReleaseReviewRequirement,
    ReviewProfileCriterion,
    RolePermission,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateConflictError, TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService


def _seed_copyable_team(tmp_path: Path) -> tuple[TeamAdminService, TeamStateRepository]:
    repo = TeamStateRepository(tmp_path)
    admin = TeamAdminService(repo)
    state = admin.create_team(
        team_id="source",
        name="Source Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        manager_display_name="Manager",
        operator_communication_target_id="discord:ops",
        max_cost_usd="25.00",
        cost_warning_ratio="0.75",
        cost_budget_renewal_mode="monthly_utc",
        escalate_to_governance=True,
        expected_revision=0,
    )
    state = admin.create_project(
        project_id="project",
        team_id="source",
        name="Product",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Lead",
        goals=[ProjectGoal(id="ship", description="Ship it", state=TeamRecordState.ACCEPTED)],
        expected_revision=state.revision,
    )
    state = admin.create_role(
        role_id="project__reviewer",
        team_id="source",
        project_id="project",
        name="Reviewer",
        description="Review product changes.",
        permissions=[RolePermission.REQUEST_RELEASE],
        expected_revision=state.revision,
    )
    state = admin.register_agent(
        agent_id="reviewer",
        team_id="source",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-reviewer",
        reports_to_agent_id="lead",
        display_name="Reviewer",
        expected_revision=state.revision,
    )
    state = admin.create_outcome_kind_definition(
        definition_id="product-change",
        team_id="source",
        project_id="project",
        kind="product_change",
        name="Product change",
        expected_revision=state.revision,
    )
    state = admin.create_review_profile(
        profile_id="product-review",
        team_id="source",
        project_id="project",
        name="Product review",
        applicable_role_ids=["project__reviewer"],
        criteria=[ReviewProfileCriterion(id="quality", label="Quality")],
        required_outcome_kinds=["product_change"],
        expected_revision=state.revision,
    )
    state = admin.update_project(
        project_id="project",
        release_policy=ProjectReleasePolicy(
            gate_sequence=[
                ReleaseGateStage(kind=ReleaseGateKind.VERIFICATION, enabled=False),
                ReleaseGateStage(kind=ReleaseGateKind.AGENT_REVIEW, enabled=True),
                ReleaseGateStage(kind=ReleaseGateKind.HUMAN_REVIEW, enabled=False),
            ],
            review_requirements=[
                ReleaseReviewRequirement(
                    reviewer_agent_id="reviewer",
                    review_profile_id="product-review",
                )
            ],
        ),
        set_release_policy=True,
        status=ProjectStatus.BLOCKED,
        expected_revision=state.revision,
    )
    state = admin.update_agent(
        agent_id="reviewer",
        status=AgentStatus.PAUSED,
        expected_revision=state.revision,
    )
    admin.create_or_reprioritize_task(
        task_id="source-task",
        owner_agent_id="reviewer",
        project_id="project",
        priority=1,
        state=TeamTaskState.PENDING,
        title="Existing work",
        description="Must not be copied.",
        created_by_agent_id="lead",
        expected_revision=state.revision,
    )
    return admin, repo


def test_copy_team_creates_clean_configuration_graph(tmp_path: Path) -> None:
    admin, repo = _seed_copyable_team(tmp_path)
    source_state = repo.read()

    state, copied = admin.copy_team(
        source_team_id="source",
        team_id=None,
        name="Destination Team",
        agent_session_ids={
            "manager": "session-copy-manager",
            "lead": "session-copy-lead",
            "reviewer": "session-copy-reviewer",
        },
        expected_revision=source_state.revision,
    )

    copied_team = state.teams[copied["team_id"]]
    copied_project = state.projects[copied["project_ids"]["project"]]
    copied_manager = state.agents[copied["agent_ids"]["manager"]]
    copied_lead = state.agents[copied["agent_ids"]["lead"]]
    copied_reviewer = state.agents[copied["agent_ids"]["reviewer"]]
    copied_profile_id = copied["review_profile_ids"]["product-review"]

    assert copied_team.name == "Destination Team"
    assert copied_team.manager_agent_id == copied_manager.id
    assert copied_team.operator_communication_target_id == "discord:ops"
    assert copied_team.escalate_to_governance is True
    assert float(copied_team.max_cost_usd or 0) == 25.0
    assert len(copied_team.cost_budget_windows) == 1
    assert copied_team.cost_budget_windows[0].actor == "operator:team-copy"
    assert copied_team.project_ids == [copied_project.id]

    assert copied_manager.session_id == "session-copy-manager"
    assert copied_lead.reports_to_agent_id == copied_manager.id
    assert copied_reviewer.reports_to_agent_id == copied_lead.id
    assert copied_reviewer.status == AgentStatus.ACTIVE
    assert copied_project.status == ProjectStatus.ACTIVE
    assert copied_project.goals[0].state == TeamRecordState.PENDING
    assert copied_project.goals[0].id != "ship"
    assert copied_project.release_policy is not None
    requirement = copied_project.release_policy.review_requirements[0]
    assert requirement.reviewer_agent_id == copied_reviewer.id
    assert requirement.review_profile_id == copied_profile_id
    assert state.review_profiles[copied_profile_id].applicable_role_ids == [
        copied["role_ids"]["project__reviewer"]
    ]
    assert state.outcome_kind_definitions[
        copied["outcome_kind_definition_ids"]["product-change"]
    ].project_id == copied_project.id
    source_assignments = [
        assignment
        for assignment in state.project_assignments.values()
        if assignment.project_id == "project"
    ]
    copied_assignments = [
        assignment
        for assignment in state.project_assignments.values()
        if assignment.project_id == copied_project.id
    ]
    assert len(copied_assignments) == len(source_assignments)
    assert {assignment.agent_id for assignment in copied_assignments} == {
        copied["agent_ids"][assignment.agent_id] for assignment in source_assignments
    }
    assert {assignment.role_id for assignment in copied_assignments} == {
        copied["role_ids"][assignment.role_id]
        for assignment in source_assignments
        if assignment.role_id is not None
    }
    assert set(state.tasks) == {"source-task"}
    assert not any(runtime.agent_id in copied["agent_ids"].values() for runtime in state.agent_runtimes.values())


def test_copy_team_requires_exact_distinct_unbound_sessions(tmp_path: Path) -> None:
    admin, repo = _seed_copyable_team(tmp_path)
    revision = repo.read().revision

    with pytest.raises(ValueError, match="exactly match copied agents.*missing: reviewer"):
        admin.copy_team(
            source_team_id="source",
            team_id=None,
            name="Missing mapping",
            agent_session_ids={
                "manager": "session-copy-manager",
                "lead": "session-copy-lead",
            },
            expected_revision=revision,
        )

    with pytest.raises(ValueError, match="distinct session"):
        admin.copy_team(
            source_team_id="source",
            team_id=None,
            name="Duplicate mapping",
            agent_session_ids={
                "manager": "session-copy",
                "lead": "session-copy",
                "reviewer": "session-copy-reviewer",
            },
            expected_revision=revision,
        )

    with pytest.raises(ValueError, match="already bound.*session-manager"):
        admin.copy_team(
            source_team_id="source",
            team_id=None,
            name="Reused mapping",
            agent_session_ids={
                "manager": "session-manager",
                "lead": "session-copy-lead",
                "reviewer": "session-copy-reviewer",
            },
            expected_revision=revision,
        )

    assert repo.read().revision == revision


def test_copy_team_keeps_source_snapshot_revision_strict(tmp_path: Path) -> None:
    admin, repo = _seed_copyable_team(tmp_path)
    stale_revision = repo.read().revision
    admin.update_team(
        team_id="source",
        name="Changed Source",
        expected_revision=stale_revision,
    )

    with pytest.raises(TeamStateConflictError):
        admin.copy_team(
            source_team_id="source",
            team_id=None,
            name="Stale copy",
            agent_session_ids={
                "manager": "session-copy-manager",
                "lead": "session-copy-lead",
                "reviewer": "session-copy-reviewer",
            },
            expected_revision=stale_revision,
        )


def test_copy_team_excludes_completed_and_archived_configuration(tmp_path: Path) -> None:
    admin, repo = _seed_copyable_team(tmp_path)
    state = admin.create_project(
        project_id="historical",
        team_id="source",
        name="Historical",
        lead_agent_id="historical-lead",
        lead_session_id="session-historical-lead",
        expected_revision=repo.read().revision,
    )
    historical_role_id = state.agents["historical-lead"].role_id
    assert historical_role_id is not None

    def _archive_historical_configuration(current):
        current.projects["historical"].status = ProjectStatus.COMPLETED
        current.agents["historical-lead"].status = AgentStatus.ARCHIVED
        current.roles[historical_role_id].archived = True
        return current

    source_state = repo.update(_archive_historical_configuration)
    copied_state, result = admin.copy_team(
        source_team_id="source",
        team_id=None,
        name="Current Configuration",
        agent_session_ids={
            "manager": "session-copy-manager",
            "lead": "session-copy-lead",
            "reviewer": "session-copy-reviewer",
        },
        expected_revision=source_state.revision,
    )

    assert "historical" not in result["project_ids"]
    assert "historical-lead" not in result["agent_ids"]
    assert historical_role_id not in result["role_ids"]
    copied_team = copied_state.teams[result["team_id"]]
    assert copied_team.project_ids == [result["project_ids"]["project"]]
    copied_manager = copied_state.agents[result["agent_ids"]["manager"]]
    assert result["agent_ids"]["lead"] in copied_manager.subordinate_agent_ids
    assert len(copied_manager.subordinate_agent_ids) == 1


def test_copy_team_copies_managed_repository_bindings_and_rolls_back_conflicts(
    tmp_path: Path,
) -> None:
    first_repo_id = "mr_11111111111111111111"
    second_repo_id = "mr_22222222222222222222"

    class FakeManagedGit:
        def __init__(self) -> None:
            self.bindings: dict[str, set[tuple[str, str]]] = {
                first_repo_id: set(),
                second_repo_id: set(),
            }

        def get_repository(self, managed_repo_id: str) -> dict[str, object]:
            return {
                "bindings": [
                    {"kind": kind, "binding_id": binding_id}
                    for kind, binding_id in sorted(self.bindings[managed_repo_id])
                ]
            }

        def bind(self, managed_repo_id: str, *, kind: str, binding_id: str) -> None:
            self.bindings[managed_repo_id].add((kind, binding_id))

        def unbind(self, managed_repo_id: str, *, kind: str, binding_id: str) -> None:
            self.bindings[managed_repo_id].discard((kind, binding_id))

    repo = TeamStateRepository(tmp_path)
    TeamAdminService(repo).create_team(
        team_id="source",
        name="Source",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    managed_git = FakeManagedGit()
    admin = TeamAdminService(repo, managed_git_admin=managed_git)
    state = admin.create_project(
        project_id="first",
        team_id="source",
        name="First",
        lead_agent_id="first-lead",
        lead_session_id="session-first-lead",
        git=ProjectGitConfig(managed_repo_id=first_repo_id),
        expected_revision=1,
    )
    state = admin.create_project(
        project_id="second",
        team_id="source",
        name="Second",
        lead_agent_id="second-lead",
        lead_session_id="session-second-lead",
        git=ProjectGitConfig(managed_repo_id=second_repo_id),
        expected_revision=state.revision,
    )
    source_bindings = {
        first_repo_id: set(managed_git.bindings[first_repo_id]),
        second_repo_id: set(managed_git.bindings[second_repo_id]),
    }
    stale_revision = state.revision
    admin.update_team(
        team_id="source",
        name="Changed Source",
        expected_revision=stale_revision,
    )
    session_mapping = {
        "manager": "session-copy-manager",
        "first-lead": "session-copy-first-lead",
        "second-lead": "session-copy-second-lead",
    }

    with pytest.raises(TeamStateConflictError):
        admin.copy_team(
            source_team_id="source",
            team_id="destination",
            name="Destination",
            agent_session_ids=session_mapping,
            expected_revision=stale_revision,
        )

    assert managed_git.bindings == source_bindings
    current_revision = repo.read().revision
    copied_state, result = admin.copy_team(
        source_team_id="source",
        team_id="destination",
        name="Destination",
        agent_session_ids=session_mapping,
        expected_revision=current_revision,
    )

    for source_project_id, managed_repo_id in (
        ("first", first_repo_id),
        ("second", second_repo_id),
    ):
        copied_project_id = result["project_ids"][source_project_id]
        assert ("team_projects", copied_project_id) in managed_git.bindings[managed_repo_id]
        assert copied_state.projects[copied_project_id].git == repo.read().projects[
            source_project_id
        ].git
