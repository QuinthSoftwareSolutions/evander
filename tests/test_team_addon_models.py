from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_sandbox.models import SessionManifest
from truman_team_addon.git_naming import (
    canonical_release_branch,
    ephemeral_agent_branch,
    feature_branch,
    release_branch,
    review_branch,
    validate_branch_name,
)
from truman_team_addon.models import (
    AgentActionRecord,
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentRuntime,
    AgentTask,
    AgentVoiceProfile,
    Deliverable,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    ReviewCriterionResult,
    ReviewProfile,
    ReviewProfileCriterion,
    ReviewRequest,
    Team,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamInboxItemType,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    runtime_operations_payload,
    agent_project_ids,
    project_lead_agent_id,
    team_state_payload,
)


def _valid_state() -> TeamState:
    manager = PersistentAgent(
        id="manager",
        team_id="team",
        role_id="team__manager",
        session_id="session-manager",
        subordinate_agent_ids=["lead"],
    )
    lead = PersistentAgent(
        id="lead",
        team_id="team",
        project_id="project",
        role_id="project__project-lead",
        session_id="session-lead",
        reports_to_agent_id="manager",
        subordinate_agent_ids=["ui", "reviewer"],
    )
    ui = PersistentAgent(
        id="ui",
        team_id="team",
        project_id="project",
        role_id="project__ui",
        session_id="session-ui",
        reports_to_agent_id="lead",
    )
    reviewer = PersistentAgent(
        id="reviewer",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-reviewer",
        reports_to_agent_id="lead",
    )
    return TeamState(
        teams={
            "team": Team(
                id="team",
                name="Team",
                manager_agent_id="manager",
                project_ids=["project"],
            )
        },
        projects={
            "project": Project(
                id="project",
                team_id="team",
                name="Project",
                lead_agent_id="lead",
                goals=[
                    ProjectGoal(
                        id="goal-1",
                        description="Ship reviewed work",
                        state=TeamRecordState.IN_PROGRESS,
                    )
                ],
            )
        },
        roles={
            "team__manager": TeamRole(
                id="team__manager",
                team_id="team",
                name="Manager",
            ),
            "project__project-lead": TeamRole(
                id="project__project-lead",
                team_id="team",
                project_id="project",
                name="Project Lead",
            ),
            "project__ui": TeamRole(
                id="project__ui",
                team_id="team",
                project_id="project",
                name="UI",
            ),
            "project__reviewer": TeamRole(
                id="project__reviewer",
                team_id="team",
                project_id="project",
                name="Reviewer",
            ),
        },
        agents={
            "manager": manager,
            "lead": lead,
            "ui": ui,
            "reviewer": reviewer,
        },
        project_assignments={
            "project__lead__project_lead": AgentProjectAssignment(
                id="project__lead__project_lead",
                team_id="team",
                project_id="project",
                agent_id="lead",
                role_id="project__project-lead",
                kind=AgentProjectAssignmentKind.PROJECT_LEAD,
            ),
            "project__ui__project_member": AgentProjectAssignment(
                id="project__ui__project_member",
                team_id="team",
                project_id="project",
                agent_id="ui",
                role_id="project__ui",
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
            ),
            "project__reviewer__project_member": AgentProjectAssignment(
                id="project__reviewer__project_member",
                team_id="team",
                project_id="project",
                agent_id="reviewer",
                role_id="project__reviewer",
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
            ),
        },
        tasks={
            "task": AgentTask(
                id="task",
                owner_agent_id="ui",
                project_id="project",
                priority=10,
                state=TeamTaskState.WAITING_FOR_REVIEW,
                title="Build UI",
                description="Implement the UI slice.",
                created_by_agent_id="lead",
                deliverable_ids=["deliverable"],
            )
        },
        deliverables={
            "deliverable": Deliverable(
                id="deliverable",
                task_id="task",
                producer_agent_id="ui",
                artifact_refs=["artifacts/ui.tar.gz"],
                summary="UI slice is ready.",
                state=TeamRecordState.IN_PROGRESS,
                review_request_ids=["review"],
            )
        },
        review_requests={
            "review": ReviewRequest(
                id="review",
                deliverable_id="deliverable",
                reviewer_agent_id="reviewer",
            )
        },
        release_packages={
            "release": ReleasePackage(
                id="release",
                project_id="project",
                deliverable_ids=["deliverable"],
                artifact_refs=["published/release.tar.gz"],
            )
        },
    )


def _state_with_runtime_operations() -> TeamState:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = _valid_state()
    state.agent_runtimes["ui"] = AgentRuntime(
        agent_id="ui",
        wake_leased_until=now + timedelta(minutes=5),
        active_dispatch_id="dispatch-ui",
        wake_reason="inbox",
        last_wake_at=now - timedelta(minutes=10),
        next_wake_at=now - timedelta(minutes=1),
        no_progress_count=2,
        last_progress_at=now - timedelta(minutes=20),
        updated_at=now,
    )
    state.tasks["blocked-task"] = AgentTask(
        id="blocked-task",
        owner_agent_id="ui",
        project_id="project",
        priority=2,
        state=TeamTaskState.BLOCKED,
        title="Blocked task",
        description="Blocked task description.",
        created_by_agent_id="lead",
    )
    state.tasks["dep-wait"] = AgentTask(
        id="dep-wait",
        owner_agent_id="ui",
        project_id="project",
        priority=3,
        state=TeamTaskState.PENDING,
        title="Dependency task",
        description="Dependency task description.",
        created_by_agent_id="lead",
    )
    state.tasks["waiting-task"] = AgentTask(
        id="waiting-task",
        owner_agent_id="ui",
        project_id="project",
        priority=4,
        state=TeamTaskState.IN_PROGRESS,
        title="Waiting task",
        description="Waiting task description.",
        created_by_agent_id="lead",
        dependency_task_ids=["dep-wait"],
    )
    state.inbox["inbox-1"] = AgentInboxItem(
        id="inbox-1",
        recipient_agent_id="ui",
        sender_agent_id="lead",
        task_id="waiting-task",
        item_type=TeamInboxItemType.STATUS_REQUEST,
        state=TeamInboxItemState.PENDING,
        payload={"message": "Please report progress."},
    )
    state.inbox["escalation-1"] = AgentInboxItem(
        id="escalation-1",
        recipient_agent_id="manager",
        sender_agent_id="lead",
        item_type=TeamInboxItemType.ESCALATION,
        state=TeamInboxItemState.PENDING,
        payload={"title": "Need operator help", "message": "Please review."},
    )
    state.action_records["action-1"] = AgentActionRecord(
        id="action-1",
        agent_id="ui",
        dispatch_id="dispatch-ui",
        action={"kind": "build"},
        success=False,
        error="build failed",
    )
    state.release_packages["release"] = ReleasePackage(
        id="release",
        project_id="project",
        deliverable_ids=["deliverable"],
        artifact_refs=["published/release.tar.gz"],
        state=TeamRecordState.BLOCKED,
        error="release blocked",
    )
    state.git_promotions["promotion-1"] = GitPromotionRecord(
        id="promotion-1",
        project_id="project",
        release_package_id="release",
        actor_agent_id="lead",
        source_branch="team/team/features/task-ui",
        target_branch="team/team/releases/release",
        promotion_kind="release",
        state="failed",
        error="merge conflict",
    )
    state.ephemeral_assignments["ephemeral-failed"] = EphemeralAssignment(
        id="ephemeral-failed",
        parent_agent_id="ui",
        project_id="project",
        task_id="waiting-task",
        session_id="session-ephemeral-failed",
        title="Failed ephemeral assignment",
        prompt="Do work.",
        state=EphemeralAssignmentState.FAILED,
        error="spawn failed",
    )
    state.ephemeral_assignments["ephemeral-cleanup"] = EphemeralAssignment(
        id="ephemeral-cleanup",
        parent_agent_id="ui",
        project_id="project",
        task_id="waiting-task",
        session_id="session-ephemeral-cleanup",
        title="Cleanup pending ephemeral assignment",
        prompt="Do more work.",
        state=EphemeralAssignmentState.CLEANUP_PENDING,
    )
    return TeamState.model_validate(state.model_dump(mode="python"))


def test_valid_team_state_loads() -> None:
    state = _valid_state()

    assert state.teams["team"].manager_agent_id == "manager"
    assert state.projects["project"].goals[0].id == "goal-1"


def test_persistent_agent_display_name_is_optional_and_trimmed() -> None:
    agent = PersistentAgent(
        id="agent",
        team_id="team",
        display_name="  Friendly Name  ",
        role_id="role-worker",
        session_id="session-agent",
    )

    assert agent.display_name == "Friendly Name"

    legacy_agent = PersistentAgent(
        id="legacy",
        team_id="team",
        role_id="role-worker",
        session_id="session-legacy",
    )

    assert legacy_agent.display_name is None


def test_agent_inbox_item_defaults_and_validates_priority() -> None:
    item = AgentInboxItem(
        id="inbox-1",
        recipient_agent_id="agent-1",
        item_type=TeamInboxItemType.PROGRESS_REPORT,
    )

    assert item.priority == TeamInboxPriority.NORMAL

    high = AgentInboxItem(
        id="inbox-2",
        recipient_agent_id="agent-1",
        item_type=TeamInboxItemType.STATUS_REQUEST,
        priority="high",
    )

    assert high.priority == TeamInboxPriority.HIGH

    with pytest.raises(ValidationError):
        AgentInboxItem(
            id="inbox-3",
            recipient_agent_id="agent-1",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            priority="urgent",
        )


def test_team_state_accepts_legacy_agents_without_display_name() -> None:
    payload = _valid_state().model_dump(mode="python", exclude_none=True)
    for agent in payload["agents"].values():
        agent.pop("display_name", None)

    validated = TeamState.model_validate(payload)

    assert validated.agents["manager"].display_name is None
    assert validated.agents["lead"].display_name is None


def test_team_state_rejects_missing_manager() -> None:
    with pytest.raises(ValidationError, match="missing manager"):
        TeamState(
            teams={
                "team": Team(id="team", name="Team", manager_agent_id="missing"),
            }
        )


def test_team_state_allows_one_agent_to_lead_multiple_projects() -> None:
    state = _valid_state().model_dump(mode="python")
    state["teams"]["team"]["project_ids"].append("other-project")
    state["projects"]["other-project"] = {
        "id": "other-project",
        "team_id": "team",
        "name": "Other Project",
        "lead_agent_id": "lead",
    }
    state["project_assignments"]["other-project__lead__project_lead"] = {
        "id": "other-project__lead__project_lead",
        "team_id": "team",
        "project_id": "other-project",
        "agent_id": "lead",
        "role_id": None,
        "kind": "project_lead",
    }

    validated = TeamState.model_validate(state)

    assert project_lead_agent_id(validated, "project") == "lead"
    assert project_lead_agent_id(validated, "other-project") == "lead"
    assert agent_project_ids(validated, "lead") == ["other-project", "project"]


def test_team_state_rejects_reporting_cycle() -> None:
    state = _valid_state().model_dump(mode="python")
    state["agents"]["manager"]["reports_to_agent_id"] = "ui"

    with pytest.raises(ValidationError, match="reporting cycle"):
        TeamState.model_validate(state)


def test_ephemeral_assignments_do_not_join_reporting_hierarchy() -> None:
    state = _valid_state()
    state.ephemeral_assignments = {
        "ephemeral-1": EphemeralAssignment(
            id="ephemeral-1",
            parent_agent_id="ui",
            project_id="project",
            task_id="task",
            session_id="session-ephemeral",
            title="Draft UI implementation",
            prompt="Implement a scoped UI draft.",
        )
    }

    validated = TeamState.model_validate(state.model_dump(mode="python"))

    assert "session-ephemeral" not in {
        agent.session_id for agent in validated.agents.values()
    }
    assert validated.agents["ui"].subordinate_agent_ids == []


def test_tasks_are_ordered_by_priority_created_time_and_id() -> None:
    state = _valid_state()
    first = datetime(2026, 1, 1, tzinfo=UTC)
    second = datetime(2026, 1, 2, tzinfo=UTC)
    state.tasks = {
        "later": AgentTask(
            id="later",
            owner_agent_id="ui",
            project_id="project",
            priority=2,
            title="Later",
            description="Later task",
            created_by_agent_id="lead",
            created_at=second,
        ),
        "first-b": AgentTask(
            id="first-b",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="First B",
            description="First task B",
            created_by_agent_id="lead",
            created_at=first,
        ),
        "first-a": AgentTask(
            id="first-a",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="First A",
            description="First task A",
            created_by_agent_id="lead",
            created_at=first,
        ),
    }
    state.deliverables = {}
    state.review_requests = {}
    state.release_packages = {}
    state = TeamState.model_validate(state.model_dump(mode="python"))

    assert [task.id for task in state.ordered_tasks_for_agent("ui")] == [
        "first-a",
        "first-b",
        "later",
    ]


def test_session_manifest_has_no_team_addon_fields() -> None:
    assert "team_id" not in SessionManifest.model_fields
    assert "project_id" not in SessionManifest.model_fields
    assert "agent_id" not in SessionManifest.model_fields


def test_project_git_config_requires_exactly_one_bare_repo() -> None:
    config = ProjectGitConfig(host_repo_path="/srv/repo", bare_repo_name="repo.git")
    assert config.bare_repo_name == "repo.git"
    assert config.base_branch == "main"
    assert config.canonical_release_branch_prefix == "releases/<project-id>/"

    path_config = ProjectGitConfig(
        host_repo_path="/srv/repo", bare_repo_path="/srv/bare/repo.git"
    )
    assert path_config.bare_repo_path == "/srv/bare/repo.git"

    with pytest.raises(ValidationError, match="exactly one of bare_repo_name"):
        ProjectGitConfig(host_repo_path="/srv/repo")

    with pytest.raises(ValidationError, match="exactly one of bare_repo_name"):
        ProjectGitConfig(
            host_repo_path="/srv/repo",
            bare_repo_name="repo.git",
            bare_repo_path="/srv/bare/repo.git",
        )


def test_project_accepts_git_config() -> None:
    state = _valid_state().model_dump(mode="python")
    state["projects"]["project"]["git"] = {
        "host_repo_path": "/srv/repo",
        "bare_repo_name": "repo.git",
    }

    validated = TeamState.model_validate(state)

    assert validated.projects["project"].git is not None
    assert validated.projects["project"].git.host_repo_path == "/srv/repo"


@pytest.mark.parametrize(
    "bad_name",
    [":", "/x", "a..b", "refs/heads/x", "feature branch", "ctrl\x01char", ""],
)
def test_validate_branch_name_rejects_bad_names(bad_name: str) -> None:
    with pytest.raises(ValueError):
        validate_branch_name(bad_name)


def test_validate_branch_name_accepts_plain_names() -> None:
    assert validate_branch_name("main") == "main"
    assert validate_branch_name("team/team-1/features/task-1-ui") == (
        "team/team-1/features/task-1-ui"
    )


def test_deterministic_branch_name_builders() -> None:
    assert ephemeral_agent_branch("session-1", "assignment-1") == (
        "agents/session-1/assignment-1"
    )
    assert review_branch("team-1", "assignment-1") == (
        "team/team-1/reviews/assignment-1"
    )
    assert feature_branch("team-1", "task-1", "ui") == (
        "team/team-1/features/task-1-ui"
    )
    assert release_branch("team-1", "release-1") == "team/team-1/releases/release-1"


def test_canonical_release_branch_honors_project_prefix() -> None:
    default_project = Project(
        id="project", team_id="team", name="Project", lead_agent_id="lead"
    )
    assert canonical_release_branch(default_project, "release-1") == (
        "releases/project/release-1"
    )

    custom_project = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        git=ProjectGitConfig(
            host_repo_path="/srv/repo",
            bare_repo_name="repo.git",
            canonical_release_branch_prefix="ship/",
        ),
    )
    assert canonical_release_branch(custom_project, "release-1") == "ship/release-1"


def test_canonical_release_branch_prefix_normalizes_trailing_slash() -> None:
    project = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        git=ProjectGitConfig(
            host_repo_path="/srv/repo",
            bare_repo_name="repo.git",
            canonical_release_branch_prefix="release",
        ),
    )

    assert project.git is not None
    assert project.git.canonical_release_branch_prefix == "release/"
    assert canonical_release_branch(project, "release-1") == "release/release-1"


def test_git_promotion_and_release_round_trip_through_team_state() -> None:
    state = _valid_state()
    state.deliverables["deliverable"].git_refs.feature_branch = (
        "team/team/features/task-ui"
    )
    state.release_packages["release"].feature_branches = ["team/team/features/task-ui"]
    state.release_packages["release"].release_branch = "team/team/releases/release"
    state.release_packages["release"].canonical_branch = "releases/project/release"
    state.git_promotions = {
        "promotion-1": GitPromotionRecord(
            id="promotion-1",
            project_id="project",
            deliverable_id="deliverable",
            actor_agent_id="lead",
            source_branch="agents/session-ui/assignment",
            target_branch="team/team/reviews/assignment",
            promotion_kind="review",
        )
    }

    validated = TeamState.model_validate(state.model_dump(mode="python"))

    promotion = validated.git_promotions["promotion-1"]
    assert promotion.state == "pending"
    assert promotion.promotion_kind == "review"
    assert validated.deliverables["deliverable"].git_refs.feature_branch == (
        "team/team/features/task-ui"
    )
    assert validated.release_packages["release"].canonical_branch == (
        "releases/project/release"
    )


def _state_with_task_graph() -> dict:
    """A _valid_state payload with a parent task and two dependency tasks."""
    state = _valid_state().model_dump(mode="python")
    project = state["projects"]["project"]
    state["tasks"]["parent"] = {
        "id": "parent",
        "owner_agent_id": "ui",
        "project_id": "project",
        "priority": 5,
        "state": TeamTaskState.IN_PROGRESS.value,
        "title": "Parent",
        "description": "Parent task",
        "created_by_agent_id": "lead",
    }
    state["tasks"]["dep-a"] = {
        "id": "dep-a",
        "owner_agent_id": "ui",
        "project_id": "project",
        "priority": 6,
        "state": TeamTaskState.ACCEPTED.value,
        "title": "Dep A",
        "description": "Dependency A",
        "created_by_agent_id": "lead",
    }
    state["tasks"]["dep-b"] = {
        "id": "dep-b",
        "owner_agent_id": "ui",
        "project_id": "project",
        "priority": 7,
        "state": TeamTaskState.PENDING.value,
        "title": "Dep B",
        "description": "Dependency B",
        "created_by_agent_id": "lead",
    }
    # Make "task" a child of "parent" depending on both dep tasks.
    state["tasks"]["task"]["parent_task_id"] = "parent"
    state["tasks"]["task"]["dependency_task_ids"] = ["dep-a", "dep-b"]
    assert project  # keep reference for clarity
    return state


def test_task_graph_loads_with_parent_and_dependencies() -> None:
    validated = TeamState.model_validate(_state_with_task_graph())

    assert validated.tasks["task"].parent_task_id == "parent"
    assert validated.tasks["task"].dependency_task_ids == ["dep-a", "dep-b"]


def test_old_shape_task_without_graph_fields_validates() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"].pop("parent_task_id", None)
    state["tasks"]["task"].pop("dependency_task_ids", None)

    validated = TeamState.model_validate(state)

    assert validated.tasks["task"].parent_task_id is None
    assert validated.tasks["task"].dependency_task_ids == []


def test_case_linked_task_validates_bidirectional_link() -> None:
    state = _valid_state().model_dump(mode="python")
    state["cases"] = {
        "case": {
            "id": "case",
            "team_id": "team",
            "project_id": "project",
            "title": "Customer ticket",
            "description": "Track the reported issue.",
            "status": "triage",
            "priority": "high",
            "owner_agent_id": "lead",
            "source": "github",
            "source_ref": "issue-42",
            "tags": ["customer", "bug"],
            "task_ids": ["task"],
        }
    }
    state["tasks"]["task"]["case_id"] = "case"

    validated = TeamState.model_validate(state)

    assert validated.cases["case"].task_ids == ["task"]
    assert validated.tasks["task"].case_id == "case"


def test_case_link_rejects_missing_task_backlink() -> None:
    state = _valid_state().model_dump(mode="python")
    state["cases"] = {
        "case": {
            "id": "case",
            "team_id": "team",
            "project_id": "project",
            "title": "Customer ticket",
            "task_ids": ["task"],
        }
    }

    with pytest.raises(
        ValidationError, match="case case task task does not point back"
    ):
        TeamState.model_validate(state)


def test_task_graph_rejects_missing_parent() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"]["parent_task_id"] = "ghost"

    with pytest.raises(ValidationError, match="missing parent ghost"):
        TeamState.model_validate(state)


def test_task_graph_rejects_missing_dependency() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"]["dependency_task_ids"] = ["ghost"]

    with pytest.raises(ValidationError, match="missing dependency ghost"):
        TeamState.model_validate(state)


def test_task_graph_rejects_cross_project_parent() -> None:
    state = _state_with_task_graph()
    state["tasks"]["parent"]["project_id"] = "other"
    # Decouple "task" from the (now cross-project) dependency tasks to isolate parent.
    state["tasks"]["task"]["dependency_task_ids"] = []
    state["projects"]["other"] = {
        "id": "other",
        "team_id": "team",
        "name": "Other",
        "lead_agent_id": "lead",
    }
    state["teams"]["team"]["project_ids"] = ["project", "other"]

    with pytest.raises(ValidationError, match="parent parent belongs to project other"):
        TeamState.model_validate(state)


def test_task_graph_rejects_cross_project_dependency() -> None:
    state = _state_with_task_graph()
    state["tasks"]["task"]["parent_task_id"] = None
    state["tasks"]["dep-a"]["project_id"] = "other"
    state["projects"]["other"] = {
        "id": "other",
        "team_id": "team",
        "name": "Other",
        "lead_agent_id": "lead",
    }
    state["teams"]["team"]["project_ids"] = ["project", "other"]

    with pytest.raises(
        ValidationError, match="dependency dep-a belongs to project other"
    ):
        TeamState.model_validate(state)


def test_task_graph_rejects_parent_cycle() -> None:
    state = _state_with_task_graph()
    # parent <- task already; close the loop so parent points back to task.
    state["tasks"]["parent"]["parent_task_id"] = "task"

    with pytest.raises(ValidationError, match="parent cycle"):
        TeamState.model_validate(state)


def test_task_graph_rejects_dependency_cycle() -> None:
    state = _state_with_task_graph()
    # task -> dep-a, dep-b; add dep-a -> task to create a cycle.
    state["tasks"]["dep-a"]["dependency_task_ids"] = ["task"]

    with pytest.raises(ValidationError, match="dependency cycle"):
        TeamState.model_validate(state)


def test_task_rejects_self_parent() -> None:
    with pytest.raises(ValidationError, match="cannot be its own parent"):
        AgentTask(
            id="task",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="Self",
            description="Self parent",
            created_by_agent_id="lead",
            parent_task_id="task",
        )


def test_task_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        AgentTask(
            id="task",
            owner_agent_id="ui",
            project_id="project",
            priority=1,
            title="Self",
            description="Self dependency",
            created_by_agent_id="lead",
            dependency_task_ids=["task"],
        )


def test_task_graph_derived_helpers() -> None:
    validated = TeamState.model_validate(_state_with_task_graph())

    # "task" is the only child of "parent".
    assert validated.child_task_ids("parent") == ["task"]
    assert validated.child_task_ids("task") == []

    # dep-a accepted, dep-b pending -> waiting.
    assert validated.dependency_status("task") == "waiting"
    assert validated.blocked_by_task_ids("task") == ["dep-b"]

    # parent has no dependencies -> satisfied.
    assert validated.dependency_status("parent") == "satisfied"
    assert validated.blocked_by_task_ids("parent") == []

    counts = validated.subtask_counts("parent")
    assert counts == {
        "total": 1,
        "accepted": 0,
        "finished": 0,
        "blocked": 0,
        "open": 1,
    }


def test_agent_runtime_maps_legacy_auto_context_clearing_to_idle_policy() -> None:
    runtime = AgentRuntime.model_validate(
        {
            "agent_id": "backend",
            "auto_context_clearing_enabled": True,
        }
    )

    assert runtime.context_clearing_policy == AgentContextClearingPolicy.IDLE
    assert runtime.auto_context_clearing_enabled is True
    assert runtime.model_dump(mode="json")["context_clearing_policy"] == "idle"


def test_team_state_payload_includes_derived_task_fields() -> None:
    state = TeamState.model_validate(_state_with_task_graph())

    payload = team_state_payload(state)
    runtime_operations = payload["runtime_operations"]

    task = payload["tasks"]["task"]
    assert task["parent_task_id"] == "parent"
    assert task["dependency_task_ids"] == ["dep-a", "dep-b"]
    assert task["child_task_ids"] == []
    assert task["dependency_status"] == "waiting"
    assert task["blocked_by_task_ids"] == ["dep-b"]
    assert task["subtask_counts"] == {
        "total": 0,
        "accepted": 0,
        "finished": 0,
        "blocked": 0,
        "open": 0,
    }

    parent = payload["tasks"]["parent"]
    assert parent["child_task_ids"] == ["task"]
    assert parent["dependency_status"] == "satisfied"
    assert parent["subtask_counts"] == {
        "total": 1,
        "accepted": 0,
        "finished": 0,
        "blocked": 0,
        "open": 1,
    }
    assert {row["id"] for row in runtime_operations["agents"]} == {
        "lead",
        "manager",
        "reviewer",
        "ui",
    }
    assert any(
        row["kind"] == "waiting_dependency_task" and row["id"] == "task"
        for row in runtime_operations["attention"]
    )


def test_team_state_payload_can_exclude_operator_only_agent_fields() -> None:
    state = _valid_state()
    state.agents["ui"].avatar = None
    state.agents["ui"].voice_profile = AgentVoiceProfile(
        voice="cedar",
        speaking_style="private voice direction",
    )

    admin_payload = team_state_payload(state)
    agent_visible_payload = team_state_payload(state, include_agent_avatars=False)

    assert admin_payload["agents"]["ui"]["voice_profile"] == {
        "voice": "cedar",
        "speaking_style": "private voice direction",
    }
    assert "voice_profile" not in agent_visible_payload["agents"]["ui"]
    assert "avatar" not in agent_visible_payload["agents"]["ui"]


def test_runtime_operations_payload_classifies_runtime_attention_rows() -> None:
    state = _state_with_runtime_operations()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    payload = runtime_operations_payload(state, now=now)

    assert payload["generated_at"] == now.isoformat()

    agents = {row["id"]: row for row in payload["agents"]}
    ui = agents["ui"]
    assert ui["active_dispatch_id"] == "dispatch-ui"
    assert ui["wake_due"] is True
    assert ui["lease_active"] is True
    assert ui["wake_reason"] == "inbox"
    assert ui["next_wake_at"] == "2025-12-31T23:59:00+00:00"
    assert ui["wake_leased_until"] == "2026-01-01T00:05:00+00:00"

    rows = {row["kind"]: row for row in payload["attention"]}
    assert set(rows) == {
        "blocked_task",
        "blocked_release_package",
        "cleanup_pending_ephemeral_assignment",
        "failed_action_record",
        "failed_ephemeral_assignment",
        "failed_git_promotion",
        "pending_escalation",
        "pending_inbox_item",
        "waiting_dependency_task",
    }
    assert rows["blocked_task"]["id"] == "blocked-task"
    assert rows["blocked_task"]["state"] == "blocked"
    assert rows["blocked_task"]["dependency_status"] == "satisfied"
    assert rows["waiting_dependency_task"]["id"] == "waiting-task"
    assert rows["waiting_dependency_task"]["dependency_status"] == "waiting"
    assert rows["waiting_dependency_task"]["blocked_by_task_ids"] == ["dep-wait"]
    assert rows["pending_inbox_item"]["inbox_item_id"] == "inbox-1"
    assert rows["pending_inbox_item"]["task_id"] == "waiting-task"
    assert rows["pending_escalation"]["inbox_item_id"] == "escalation-1"
    assert rows["failed_action_record"]["dispatch_id"] == "dispatch-ui"
    assert rows["failed_action_record"]["project_id"] == "project"
    assert rows["failed_action_record"]["error"] == "build failed"
    assert rows["failed_git_promotion"]["promotion_id"] == "promotion-1"
    assert rows["failed_git_promotion"]["release_package_id"] == "release"
    assert rows["blocked_release_package"]["release_package_id"] == "release"
    assert rows["blocked_release_package"]["state"] == "blocked"
    assert rows["failed_ephemeral_assignment"]["assignment_id"] == "ephemeral-failed"
    assert rows["failed_ephemeral_assignment"]["state"] == "failed"
    assert rows["cleanup_pending_ephemeral_assignment"]["assignment_id"] == "ephemeral-cleanup"
    assert rows["cleanup_pending_ephemeral_assignment"]["state"] == "cleanup_pending"


def test_runtime_operations_do_not_mark_idle_null_next_wake_as_due() -> None:
    state = _valid_state()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state.agent_runtimes["manager"] = AgentRuntime(
        agent_id="manager",
        last_wake_at=now - timedelta(minutes=5),
        next_wake_at=None,
        updated_at=now,
    )

    payload = runtime_operations_payload(state, now=now)

    manager = next(row for row in payload["agents"] if row["id"] == "manager")
    assert manager["wake_due"] is False
    assert manager["lease_active"] is False
    assert manager["severity"] == "info"


def test_runtime_operations_do_not_mark_waiting_review_producer_due() -> None:
    state = _valid_state()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state.agent_runtimes["ui"] = AgentRuntime(
        agent_id="ui",
        last_wake_at=now - timedelta(minutes=20),
        next_wake_at=now - timedelta(minutes=1),
        updated_at=now,
    )

    payload = runtime_operations_payload(state, now=now)

    ui = next(row for row in payload["agents"] if row["id"] == "ui")
    assert ui["wake_due"] is False
    assert ui["lease_active"] is False
    assert ui["severity"] == "info"


def test_runtime_operations_wake_due_ignores_no_reply_inbox() -> None:
    state = _valid_state()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state.agent_runtimes["ui"] = AgentRuntime(
        agent_id="ui",
        last_wake_at=now - timedelta(minutes=20),
        next_wake_at=now - timedelta(minutes=1),
        low_priority_batch_started_at=now - timedelta(minutes=15),
        low_priority_batch_cycle_count=10,
        low_priority_batch_due_after_cycles=4,
        updated_at=now,
    )
    state.inbox["no-reply"] = AgentInboxItem(
        id="no-reply",
        recipient_agent_id="ui",
        sender_agent_id="lead",
        task_id="task",
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=False,
        priority=TeamInboxPriority.LOW,
        state=TeamInboxItemState.PENDING,
    )

    payload = runtime_operations_payload(state, now=now)

    ui = next(row for row in payload["agents"] if row["id"] == "ui")
    assert ui["wake_due"] is False
    assert ui["severity"] == "info"


def test_runtime_attention_hides_failed_action_after_later_success() -> None:
    state = _state_with_runtime_operations()
    state.action_records["action-2"] = AgentActionRecord(
        id="action-2",
        agent_id="ui",
        dispatch_id="dispatch-ui-follow-up",
        action={"kind": "build"},
        success=True,
        created_at=state.action_records["action-1"].created_at + timedelta(seconds=1),
    )

    payload = runtime_operations_payload(state, now=datetime(2026, 1, 1, tzinfo=UTC))

    assert all(row["kind"] != "failed_action_record" for row in payload["attention"])


def test_runtime_attention_hides_dismissed_failed_action() -> None:
    state = _state_with_runtime_operations()
    state.action_records["action-1"].dismissed_at = datetime(2026, 1, 1, tzinfo=UTC)
    state.action_records["action-1"].dismissed_by = "operator"

    payload = runtime_operations_payload(state, now=datetime(2026, 1, 1, tzinfo=UTC))

    assert all(row["kind"] != "failed_action_record" for row in payload["attention"])


def test_dependency_status_satisfied_and_blocked() -> None:
    state = _state_with_task_graph()
    # Both dependencies accepted/finished -> satisfied.
    state["tasks"]["dep-b"]["state"] = TeamTaskState.FINISHED.value
    satisfied = TeamState.model_validate(state)
    assert satisfied.dependency_status("task") == "satisfied"
    assert satisfied.blocked_by_task_ids("task") == []

    # A blocked dependency -> blocked.
    blocked_state = _state_with_task_graph()
    blocked_state["tasks"]["dep-b"]["state"] = TeamTaskState.BLOCKED.value
    blocked = TeamState.model_validate(blocked_state)
    assert blocked.dependency_status("task") == "blocked"
    assert sorted(blocked.blocked_by_task_ids("task")) == ["dep-b"]


def test_subtask_counts_classifies_child_states() -> None:
    state = _state_with_task_graph()
    # Add more children of "parent" in varied states.
    for child_id, child_state in (
        ("child-accepted", TeamTaskState.ACCEPTED),
        ("child-finished", TeamTaskState.FINISHED),
        ("child-blocked", TeamTaskState.BLOCKED),
        ("child-rejected", TeamTaskState.REJECTED),
    ):
        state["tasks"][child_id] = {
            "id": child_id,
            "owner_agent_id": "ui",
            "project_id": "project",
            "priority": 9,
            "state": child_state.value,
            "title": child_id,
            "description": child_id,
            "created_by_agent_id": "lead",
            "parent_task_id": "parent",
        }

    validated = TeamState.model_validate(state)

    counts = validated.subtask_counts("parent")
    # "task" is WAITING_FOR_REVIEW (open) plus the four added children.
    assert counts == {
        "total": 5,
        "accepted": 1,
        "finished": 1,
        "blocked": 2,
        "open": 1,
    }


def _profile() -> ReviewProfile:
    return ReviewProfile(
        id="profile",
        team_id="team",
        project_id="project",
        name="Code review",
        description="Standard code review rubric.",
        applicable_role_ids=["project__reviewer"],
        criteria=[
            ReviewProfileCriterion(id="tests", label="Tests pass"),
            ReviewProfileCriterion(
                id="style", label="Style", description="Matches repo style", required=False
            ),
        ],
    )


def test_review_profile_round_trips_through_team_state() -> None:
    state = _valid_state()
    state.review_profiles = {"profile": _profile()}

    validated = TeamState.model_validate(state.model_dump(mode="python"))

    profile = validated.review_profiles["profile"]
    assert profile.name == "Code review"
    assert profile.applicable_role_ids == ["project__reviewer"]
    assert [criterion.id for criterion in profile.criteria] == ["tests", "style"]
    assert profile.criteria[1].required is False
    assert profile.archived is False


def test_review_profile_rejects_missing_team() -> None:
    state = _valid_state().model_dump(mode="python")
    state["review_profiles"] = {
        "profile": {
            "id": "profile",
            "team_id": "ghost",
            "name": "Code review",
        }
    }

    with pytest.raises(ValidationError, match="review profile profile references missing team ghost"):
        TeamState.model_validate(state)


def test_review_profile_rejects_cross_team_project() -> None:
    state = _valid_state().model_dump(mode="python")
    state["teams"]["other-team"] = {
        "id": "other-team",
        "name": "Other team",
        "manager_agent_id": "manager",
    }
    state["review_profiles"] = {
        "profile": {
            "id": "profile",
            "team_id": "other-team",
            "project_id": "project",
            "name": "Code review",
        }
    }

    with pytest.raises(ValidationError, match="review profile profile project project belongs to team team"):
        TeamState.model_validate(state)


def test_review_profile_rejects_duplicate_criterion_ids() -> None:
    with pytest.raises(ValidationError, match="review profile criteria contains duplicate ids"):
        ReviewProfile(
            id="profile",
            team_id="team",
            name="Code review",
            criteria=[
                ReviewProfileCriterion(id="tests", label="Tests pass"),
                ReviewProfileCriterion(id="tests", label="Tests again"),
            ],
        )


def test_review_with_profile_and_criterion_results_validates() -> None:
    state = _valid_state()
    state.review_profiles = {"profile": _profile()}
    state.review_requests["review"].review_profile_id = "profile"
    state.review_requests["review"].instructions = "Focus on the new tests."
    state.review_requests["review"].criteria = [
        ReviewCriterionResult(criterion_id="tests", state="passed", finding="All green."),
        ReviewCriterionResult(criterion_id="style", state="skipped"),
    ]

    validated = TeamState.model_validate(state.model_dump(mode="python"))

    review = validated.review_requests["review"]
    assert review.review_profile_id == "profile"
    assert review.instructions == "Focus on the new tests."
    assert [result.criterion_id for result in review.criteria] == ["tests", "style"]
    assert review.criteria[0].state == "passed"


def test_review_rejects_criterion_result_for_unknown_criterion() -> None:
    state = _valid_state()
    state.review_profiles = {"profile": _profile()}
    state.review_requests["review"].review_profile_id = "profile"
    state.review_requests["review"].criteria = [
        ReviewCriterionResult(criterion_id="ghost", state="passed"),
    ]

    with pytest.raises(
        ValidationError, match="criterion result references unknown criterion ghost"
    ):
        TeamState.model_validate(state.model_dump(mode="python"))


def test_review_rejects_duplicate_criterion_results() -> None:
    state = _valid_state()
    state.review_profiles = {"profile": _profile()}
    state.review_requests["review"].review_profile_id = "profile"
    state.review_requests["review"].criteria = [
        ReviewCriterionResult(criterion_id="tests", state="passed"),
        ReviewCriterionResult(criterion_id="tests", state="failed"),
    ]

    with pytest.raises(
        ValidationError, match="has duplicate criterion result tests"
    ):
        TeamState.model_validate(state.model_dump(mode="python"))


def test_review_rejects_criterion_results_without_profile() -> None:
    state = _valid_state()
    state.review_requests["review"].criteria = [
        ReviewCriterionResult(criterion_id="tests", state="passed"),
    ]

    with pytest.raises(
        ValidationError, match="has criterion results but no review profile"
    ):
        TeamState.model_validate(state.model_dump(mode="python"))


def test_review_rejects_missing_profile_reference() -> None:
    state = _valid_state().model_dump(mode="python")
    state["review_requests"]["review"]["review_profile_id"] = "ghost"

    with pytest.raises(
        ValidationError, match="references missing review profile ghost"
    ):
        TeamState.model_validate(state)


def test_legacy_state_without_review_profile_keys_validates() -> None:
    state = _valid_state().model_dump(mode="python")
    state.pop("review_profiles", None)
    review = state["review_requests"]["review"]
    review.pop("review_profile_id", None)
    review.pop("instructions", None)
    review.pop("criteria", None)

    validated = TeamState.model_validate(state)

    assert validated.review_profiles == {}
    assert validated.review_requests["review"].review_profile_id is None
    assert validated.review_requests["review"].instructions is None
    assert validated.review_requests["review"].criteria == []


def test_git_promotion_rejects_missing_project() -> None:
    state = _valid_state().model_dump(mode="python")
    state["git_promotions"] = {
        "promotion-1": {
            "id": "promotion-1",
            "project_id": "missing-project",
            "actor_agent_id": "lead",
            "source_branch": "agents/session-ui/assignment",
            "target_branch": "team/team/reviews/assignment",
            "promotion_kind": "review",
        }
    }

    with pytest.raises(ValidationError, match="missing project missing-project"):
        TeamState.model_validate(state)



def test_persistent_agent_can_be_teamless() -> None:
    agent = PersistentAgent(id="unassigned", session_id="session-unassigned")

    assert agent.team_id is None
    assert agent.role_id is None


def test_teamless_persistent_agent_rejects_team_scoped_links() -> None:
    with pytest.raises(ValidationError, match="teamless agent cannot have a team-scoped role"):
        PersistentAgent(id="unassigned", session_id="session-unassigned", role_id="role")

    agent = PersistentAgent(id="unassigned", session_id="session-unassigned", project_id="project")
    assert not hasattr(agent, "project_id")
