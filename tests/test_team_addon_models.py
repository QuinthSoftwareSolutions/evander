from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evander.models import SessionManifest
from evander_team_addon.git_naming import (
    canonical_release_branch,
    ephemeral_agent_branch,
    feature_branch,
    release_branch,
    review_branch,
    validate_branch_name,
)
from evander_team_addon.models import (
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
    HumanReviewRequirementSnapshot,
    HumanReviewRequest,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGitIdentity,
    ProjectGoal,
    ProjectReleasePolicy,
    ReleasePackage,
    ReleaseReviewRequirement,
    ReviewCriterionResult,
    ReviewFindingRecord,
    ReviewProfile,
    ReviewProfileCriterion,
    ReviewRequest,
    Team,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamInboxItemType,
    TeamMemoryRecord,
    TeamMemoryScope,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    runtime_operations_payload,
    agent_project_ids,
    project_lead_agent_id,
    team_state_payload,
)
from evander_team_addon.services.domain.task_assessment import (
    TaskAssessmentService,
    assess_task,
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


def test_release_package_upgrades_legacy_candidate_manifest_shape() -> None:
    release = ReleasePackage(
        id="release",
        project_id="project",
        deliverable_ids=["deliverable"],
        candidate_commit_sha="candidate-sha",
        release_branch="team/team/releases/release",
        manifest={
            "project_id": "project",
            "release_package_id": "release",
            "candidate_commit_sha": "candidate-sha",
            "candidate_tree_hash": "candidate-tree",
            "deliverable_ids": ["deliverable"],
            "feature_branches": ["team/team/features/task-worker"],
            "release_branch": "team/team/releases/release",
            "verification_commands": [],
            "review_requirements": [],
        },
    )

    assert release.manifest is not None
    assert release.manifest.evidence_status == "legacy_partial"
    assert release.manifest.candidate_commit_sha == "candidate-sha"
    assert release.manifest.deliverables[0].deliverable_id == "deliverable"
    assert release.manifest.deliverables[0].feature_branch == "team/team/features/task-worker"


def test_release_policy_rejects_duplicate_reviewer_profile_pairs() -> None:
    requirement = ReleaseReviewRequirement(
        reviewer_agent_id="reviewer",
        review_profile_id="release-profile",
    )

    with pytest.raises(ValidationError, match="unique reviewer/profile pairs"):
        ProjectReleasePolicy(review_requirements=[requirement, requirement])


def test_release_policy_resource_limits_are_typed_and_optional() -> None:
    default_policy = ProjectReleasePolicy(allow_ungated=True)
    configured_policy = ProjectReleasePolicy.model_validate(
        {
            "allow_ungated": True,
            "resource_limits": {
                "memory": " 6g ",
                "cpus": 4,
                "pids_limit": 1536,
            },
        }
    )

    assert default_policy.resource_limits is None
    assert configured_policy.model_dump(mode="json")["resource_limits"] == {
        "memory": "6g",
        "cpus": 4.0,
        "pids_limit": 1536,
    }


@pytest.mark.parametrize(
    "resource_limits",
    [
        {"cpus": 0},
        {"cpus": -1},
        {"pids_limit": 0},
        {"pids_limit": -1},
        {"unexpected": 1},
    ],
)
def test_release_policy_rejects_invalid_resource_limits(
    resource_limits: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProjectReleasePolicy(
            allow_ungated=True,
            resource_limits=resource_limits,
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
        release_branch="team/team/releases/release",
        state=TeamRecordState.BLOCKED,
        error="merge conflict while merging feature into team/team/releases/release",
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
        commit_sha="failed-commit-sha",
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


def test_team_memory_record_validates_scope_requirements() -> None:
    with pytest.raises(ValidationError, match="project-scoped"):
        TeamMemoryRecord(
            id="memory-project",
            team_id="team",
            scope=TeamMemoryScope.PROJECT,
            title="Project decision",
            summary="Project-scoped memory must identify the project.",
        )

    with pytest.raises(ValidationError, match="agent-private"):
        TeamMemoryRecord(
            id="memory-agent",
            team_id="team",
            scope=TeamMemoryScope.AGENT_PRIVATE,
            title="Private note",
            summary="Agent-private memory must identify the owning agent.",
        )


def test_team_state_validates_and_compacts_memory_payloads() -> None:
    state = _valid_state()
    state.memories["memory-1"] = TeamMemoryRecord(
        id="memory-1",
        team_id="team",
        project_id="project",
        scope=TeamMemoryScope.PROJECT,
        kind="decision",
        title="Use artifact spaces for evidence",
        summary="Keep the memory row compact and store bulky evidence elsewhere.",
        body="Long rationale and detailed evidence stay out of broad state payloads.",
        tags=["Architecture", "memory"],
        source_refs=["task:task"],
        artifact_refs=["artifact-space:/teams/team/projects/project/memory/memory-1"],
        created_by_agent_id="lead",
    )

    validated = TeamState.model_validate(state.model_dump(mode="python"))
    payload = team_state_payload(validated)

    assert validated.memories["memory-1"].tags == ["architecture", "memory"]
    assert payload["memories"]["memory-1"]["summary"] == (
        "Keep the memory row compact and store bulky evidence elsewhere."
    )
    assert "body" not in payload["memories"]["memory-1"]


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

    assert "session-ephemeral" not in {agent.session_id for agent in validated.agents.values()}
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


def test_project_git_config_requires_managed_repo_id() -> None:
    config = ProjectGitConfig(
        managed_repo_id="mr_0123456789abcdefabcd",
        directory_path=" root/team-a ",
    )
    assert config.managed_repo_id == "mr_0123456789abcdefabcd"
    assert config.directory_path == "root/team-a"
    assert config.base_branch is None
    assert config.canonical_release_branch_prefix == "releases/<project-id>/"
    assert config.agent_author_identity is None
    assert config.agent_committer_identity is None

    with pytest.raises(ValidationError, match="managed_repo_id"):
        ProjectGitConfig.model_validate({})

    with pytest.raises(ValidationError, match="must match mr_<20-hex>"):
        ProjectGitConfig(managed_repo_id="legacy-repo")

    with pytest.raises(ValidationError, match="must begin with 'root'"):
        ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="team-a",
        )

    with pytest.raises(ValidationError, match="repo_source"):
        ProjectGitConfig.model_validate(
            {
                "managed_repo_id": "mr_0123456789abcdefabcd",
                "repo_source": "https://example.invalid/operator-checkout.git",
            }
        )


def test_project_git_config_accepts_distinct_agent_author_and_committer() -> None:
    config = ProjectGitConfig(
        managed_repo_id="mr_0123456789abcdefabcd",
        agent_author_identity=ProjectGitIdentity(
            name=" Jonathan Quinth ",
            email=" operator@example.invalid ",
        ),
        agent_committer_identity={
            "name": "Evander Bot",
            "email": "evander@quinth.se",
        },
    )

    assert config.agent_author_identity == ProjectGitIdentity(
        name="Jonathan Quinth",
        email="operator@example.invalid",
    )
    assert config.agent_committer_identity == ProjectGitIdentity(
        name="Evander Bot",
        email="evander@quinth.se",
    )


@pytest.mark.parametrize(
    "identity",
    [
        {"name": "", "email": "person@example.com"},
        {"name": "Person", "email": "not-an-email"},
        {"name": "Person\nInjected", "email": "person@example.com"},
    ],
)
def test_project_git_config_rejects_invalid_agent_identity(
    identity: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            agent_author_identity=identity,
        )


def test_project_accepts_git_config() -> None:
    state = _valid_state().model_dump(mode="python")
    state["projects"]["project"]["git"] = {
        "managed_repo_id": "mr_0123456789abcdefabcd",
    }

    validated = TeamState.model_validate(state)

    assert validated.projects["project"].git is not None
    assert validated.projects["project"].git.managed_repo_id == "mr_0123456789abcdefabcd"


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
    assert ephemeral_agent_branch("session-1", "assignment-1") == ("agents/session-1/assignment-1")
    assert review_branch("team-1", "assignment-1") == ("team/team-1/reviews/assignment-1")
    assert feature_branch("team-1", "task-1", "ui") == ("team/team-1/features/task-1-ui")
    assert release_branch("team-1", "release-1") == "team/team-1/releases/release-1"


def test_canonical_release_branch_honors_project_prefix() -> None:
    default_project = Project(id="project", team_id="team", name="Project", lead_agent_id="lead")
    assert canonical_release_branch(default_project, "release-1") == ("releases/project/release-1")

    custom_project = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        git=ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
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
            managed_repo_id="mr_0123456789abcdefabcd",
            canonical_release_branch_prefix="release",
        ),
    )

    assert project.git is not None
    assert project.git.canonical_release_branch_prefix == "release/"
    assert canonical_release_branch(project, "release-1") == "release/release-1"


def test_git_promotion_and_release_round_trip_through_team_state() -> None:
    state = _valid_state()
    state.deliverables["deliverable"].git_refs.feature_branch = "team/team/features/task-ui"
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
    assert validated.release_packages["release"].canonical_branch == ("releases/project/release")


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


def _add_other_project(state: dict) -> None:
    """Add a second valid project without triggering unrelated assignment errors."""
    state["roles"]["other__project-lead"] = {
        "id": "other__project-lead",
        "team_id": "team",
        "project_id": "other",
        "name": "Other Project Lead",
    }
    state["projects"]["other"] = {
        "id": "other",
        "team_id": "team",
        "name": "Other",
        "lead_agent_id": "lead",
    }
    state["teams"]["team"]["project_ids"] = ["project", "other"]


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

    with pytest.raises(ValidationError, match="case case task task does not point back"):
        TeamState.model_validate(state)


def test_task_graph_reports_missing_parent_without_rejecting_legacy_state() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"]["parent_task_id"] = "ghost"

    validated = TeamState.model_validate(state)
    assessment = assess_task(validated, "task")

    assert validated.tasks["task"].parent_task_id == "ghost"
    assert assessment.resolution_status.value == "invalid"
    assert assessment.readiness.value == "invalid"
    assert assessment.invalid_reason_codes == ["parent_task_missing"]
    assert assessment.direct_child_ids == []
    assert assessment.unsatisfied_dependency_ids == []


def test_task_graph_reports_missing_dependency_without_rejecting_legacy_state() -> None:
    state = _valid_state().model_dump(mode="python")
    state["tasks"]["task"]["dependency_task_ids"] = ["ghost"]

    validated = TeamState.model_validate(state)
    assessment = assess_task(validated, "task")

    assert validated.tasks["task"].dependency_task_ids == ["ghost"]
    assert assessment.resolution_status.value == "invalid"
    assert assessment.readiness.value == "invalid"
    assert assessment.invalid_reason_codes == ["dependency_task_missing"]
    assert assessment.unsatisfied_dependency_ids == ["ghost"]


def test_task_graph_reports_cross_project_parent_without_rejecting_legacy_state() -> None:
    state = _state_with_task_graph()
    state["tasks"]["parent"]["project_id"] = "other"
    # Decouple "task" from the (now cross-project) dependency tasks to isolate parent.
    state["tasks"]["task"]["dependency_task_ids"] = []
    _add_other_project(state)

    validated = TeamState.model_validate(state)
    assessment = assess_task(validated, "task")

    assert validated.tasks["task"].parent_task_id == "parent"
    assert validated.child_task_ids("parent") == ["task"]
    assert assessment.resolution_status.value == "invalid"
    assert assessment.invalid_reason_codes == ["parent_task_outside_project"]
    assert assessment.unsatisfied_dependency_ids == []


def test_task_graph_reports_cross_project_dependency_without_rejecting_legacy_state() -> None:
    state = _state_with_task_graph()
    state["tasks"]["task"]["parent_task_id"] = None
    state["tasks"]["dep-a"]["project_id"] = "other"
    _add_other_project(state)

    validated = TeamState.model_validate(state)
    assessment = assess_task(validated, "task")

    assert validated.tasks["task"].dependency_task_ids == ["dep-a", "dep-b"]
    assert assessment.resolution_status.value == "invalid"
    assert assessment.invalid_reason_codes == ["dependency_task_outside_project"]
    assert assessment.unsatisfied_dependency_ids == ["dep-a", "dep-b"]


def test_task_graph_reports_parent_cycle_without_rejecting_legacy_state() -> None:
    state = _state_with_task_graph()
    # parent <- task already; close the loop so parent points back to task.
    state["tasks"]["parent"]["parent_task_id"] = "task"

    validated = TeamState.model_validate(state)
    assessments = TaskAssessmentService(validated).assess_all()

    assert validated.child_task_ids("parent") == ["task"]
    assert validated.child_task_ids("task") == ["parent"]
    assert assessments["parent"].invalid_reason_codes == ["parent_cycle"]
    assert assessments["parent"].direct_child_ids == ["task"]
    assert assessments["parent"].unresolved_descendant_ids == ["task"]
    assert assessments["task"].invalid_reason_codes == ["parent_cycle"]
    assert assessments["task"].direct_child_ids == ["parent"]
    assert assessments["task"].unresolved_descendant_ids == ["parent"]


def test_task_graph_reports_dependency_cycle_without_rejecting_legacy_state() -> None:
    state = _state_with_task_graph()
    # task -> dep-a, dep-b; add dep-a -> task to create a cycle.
    state["tasks"]["dep-a"]["dependency_task_ids"] = ["task"]

    validated = TeamState.model_validate(state)
    assessments = TaskAssessmentService(validated).assess_all()

    assert validated.tasks["task"].dependency_task_ids == ["dep-a", "dep-b"]
    assert validated.tasks["dep-a"].dependency_task_ids == ["task"]
    assert assessments["task"].invalid_reason_codes == ["dependency_cycle"]
    assert assessments["task"].unsatisfied_dependency_ids == ["dep-a", "dep-b"]
    assert assessments["dep-a"].invalid_reason_codes == ["dependency_cycle"]
    assert assessments["dep-a"].unsatisfied_dependency_ids == ["task"]
    assert assessments["dep-b"].invalid_reason_codes == []


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
    assert task["waiting_reason"] == "review"

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
    assert parent["waiting_reason"] is None
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


def test_team_state_payload_serializes_human_review_and_stall_blockers() -> None:
    state = _valid_state()
    state.human_review_requests["human-review"] = HumanReviewRequest(
        id="human-review",
        release_package_id="release",
        candidate_commit_sha="a" * 40,
        candidate_tree_hash="b" * 40,
        requirement_snapshot=HumanReviewRequirementSnapshot(
            directory_path="root",
            configured_group_ids=["reviewers"],
            initially_eligible_user_ids=[],
        ),
        state="blocked",
    )
    state.blockers["human-review-blocker"] = TeamBlocker(
        id="human-review-blocker",
        team_id="team",
        source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
        source_id="human-review",
        owner_agent_id="manager",
        summary="A reviewer must be reassigned.",
    )
    state.blockers["stall-blocker"] = TeamBlocker(
        id="stall-blocker",
        team_id="team",
        source_kind=TeamBlockerSourceKind.AGENT_EXECUTION_STALL,
        source_id="ui",
        owner_agent_id="manager",
        summary="The agent made no scoped progress.",
    )

    payload = team_state_payload(state)

    assert payload["human_review_requests"]["human-review"]["active_blocker_ids"] == [
        "human-review-blocker"
    ]
    assert payload["agents"]["ui"]["active_blocker_ids"] == ["stall-blocker"]


def test_team_state_payload_distinguishes_superior_acceptance_from_review() -> None:
    state = _valid_state()

    payload = team_state_payload(state)
    assert payload["tasks"]["task"]["waiting_reason"] == "review"

    state.review_requests["review"].state = TeamRecordState.ACCEPTED
    payload = team_state_payload(state)
    assert payload["tasks"]["task"]["waiting_reason"] == "review"

    state.deliverables["deliverable"].review_request_ids = []
    state.review_requests = {}
    state.deliverables["deliverable"].carried_findings = [
        ReviewFindingRecord(
            id="finding",
            source_review_request_id="review",
            source_deliverable_id="deliverable-v1",
            owner_review_profile_id="profile",
            text="A matching formal reviewer must confirm the rework.",
        )
    ]
    payload = team_state_payload(state)
    assert payload["tasks"]["task"]["waiting_reason"] == "review"

    state.deliverables["deliverable"].carried_findings = []
    payload = team_state_payload(state)
    assert payload["tasks"]["task"]["waiting_reason"] == "superior_acceptance"

    state.tasks["task"].state = TeamTaskState.FINISHED
    payload = team_state_payload(state)
    assert payload["tasks"]["task"]["waiting_reason"] is None


def test_team_state_payload_includes_agent_session_backend_health() -> None:
    state = TeamState.model_validate(_state_with_task_graph())
    expires_at = datetime(2026, 1, 2, tzinfo=UTC)

    payload = team_state_payload(
        state,
        session_records={
            "session-ui": SessionManifest(
                session_id="session-ui",
                vm_provider="container_worker_vm",
                vm_provider_id="session-ui",
                model="gpt-5",
                allowed_tools=[],
                expires_at=expires_at,
                metadata={
                    "execution_backend": "container_worker_vm",
                    "container_lifecycle": {"state": "running"},
                },
            ),
            "session-lead": SessionManifest(
                session_id="session-lead",
                vm_id="vm-lead",
                vm_provider="libvirt",
                vm_provider_id="vm-lead",
                model="gpt-5",
                allowed_tools=[],
                expires_at=expires_at,
                metadata={"execution_backend": "libvirt_vm"},
            ),
        },
        agent_execution_records={
            "session-ui": SimpleNamespace(
                backend="container_worker_vm",
                status="running",
                host={
                    "active_execution_count": 1,
                    "latest_execution_id": "exec-ui",
                },
            )
        },
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    health = {row["agent_id"]: row for row in payload["runtime_operations"]["session_health"]}
    assert health["ui"]["session_present"] is True
    assert health["ui"]["vm_provider"] == "container_worker_vm"
    assert health["ui"]["execution_backend"] == "container_worker_vm"
    assert health["ui"]["container_lifecycle_state"] == "running"
    assert health["ui"]["agent_execution_backend"] == "container_worker_vm"
    assert health["ui"]["agent_execution_status"] == "running"
    assert health["ui"]["agent_active_execution_count"] == 1
    assert health["ui"]["agent_latest_execution_id"] == "exec-ui"
    assert "vm_provider" not in health["lead"]
    assert "execution_backend" not in health["lead"]
    assert "container_lifecycle_state" not in health["lead"]
    assert "agent_execution_backend" not in health["lead"]


def test_team_state_payload_includes_blocked_release_next_action_hint() -> None:
    state = _state_with_runtime_operations()

    payload = team_state_payload(state)

    release = payload["release_packages"]["release"]
    assert release["next_action"] == "resolve_blocked_release"
    assert "do not create another git_release_requests" in release["next_action_hint"]
    assert "git_release_recoveries" in release["next_action_hint"]
    assert "team/team/releases/release" in release["next_action_hint"]
    assert "Resolve the merge conflict" in release["next_action_hint"]
    assert release["blocked_reason_code"] == "merge_conflict"
    assert release["failed_git_promotion_ids"] == ["promotion-1"]
    assert release["promotion_attempt_count"] == 1


def test_team_state_payload_classifies_stale_index_lock_release_blocker() -> None:
    state = _valid_state()
    state.release_packages["release"] = ReleasePackage(
        id="release",
        project_id="project",
        deliverable_ids=["deliverable"],
        release_branch="team/team/releases/release",
        git_promotion_record_ids=["promotion-1"],
        state=TeamRecordState.BLOCKED,
        error=("fatal: Unable to create '/home/jonathan/true-app/.git/index.lock': File exists."),
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
        error="stale git index lock blocks release promotion",
        outputs={"error_code": "stale_index_lock"},
    )

    payload = team_state_payload(state)

    release = payload["release_packages"]["release"]
    assert release["blocked_reason_code"] == "stale_index_lock"
    assert release["failed_git_promotion_ids"] == ["promotion-1"]
    assert release["promotion_attempt_count"] == 1


def test_blocked_release_hint_uses_failed_feature_promotion_target() -> None:
    state = _valid_state()
    state.release_packages["release"] = ReleasePackage(
        id="release",
        project_id="project",
        deliverable_ids=["deliverable"],
        release_branch="team/team/releases/release",
        git_promotion_record_ids=["feature-promotion"],
        state=TeamRecordState.BLOCKED,
        error=(
            "merge conflict while merging team/team/reviews/deliverable into "
            "team/team/features/task-ui"
        ),
    )
    state.git_promotions["feature-promotion"] = GitPromotionRecord(
        id="feature-promotion",
        project_id="project",
        release_package_id="release",
        task_id="task",
        actor_agent_id="lead",
        source_branch="team/team/reviews/deliverable",
        target_branch="team/team/features/task-ui",
        promotion_kind="feature",
        state="failed",
        error="merge conflict",
    )

    payload = team_state_payload(state)

    hint = payload["release_packages"]["release"]["next_action_hint"]
    assert "Failed feature promotion feature-promotion" in hint
    assert ("Resolve and commit on promotion target branch team/team/features/task-ui") in hint
    assert "submit git_release_recoveries" in hint
    assert "do not submit git_release_recoveries" not in hint
    assert "Continue from release branch" not in hint


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
    assert rows["failed_git_promotion"]["commit_sha"] == "failed-commit-sha"
    assert rows["blocked_release_package"]["release_package_id"] == "release"
    assert rows["blocked_release_package"]["state"] == "blocked"
    assert rows["blocked_release_package"]["next_action"] == "resolve_blocked_release"
    assert (
        "do not create another git_release_requests"
        in rows["blocked_release_package"]["next_action_hint"]
    )
    assert "git_release_recoveries" in rows["blocked_release_package"]["next_action_hint"]
    assert "team/team/releases/release" in rows["blocked_release_package"]["next_action_hint"]
    assert rows["failed_ephemeral_assignment"]["assignment_id"] == "ephemeral-failed"
    assert rows["failed_ephemeral_assignment"]["state"] == "failed"
    assert rows["cleanup_pending_ephemeral_assignment"]["assignment_id"] == "ephemeral-cleanup"
    assert rows["cleanup_pending_ephemeral_assignment"]["state"] == "cleanup_pending"


def test_runtime_attention_derives_age_links_and_terminal_reconciliation() -> None:
    state = _state_with_runtime_operations()
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    occurred_at = now - timedelta(days=10)
    state.tasks["waiting-task"].state = TeamTaskState.ACCEPTED
    state.tasks["waiting-task"].state_entered_at = occurred_at
    state.deliverables["deliverable"].state = TeamRecordState.ACCEPTED
    state.release_packages["release"].state = TeamRecordState.ACCEPTED
    state.release_packages["release"].error = None
    state.inbox["inbox-1"].task_id = None
    state.inbox["inbox-1"].created_at = occurred_at
    state.inbox["inbox-1"].payload = {
        "legacy_context": [
            {"task_id": "waiting-task"},
            {
                "nested": {
                    "deliverable_id": "deliverable",
                    "release_package_id": "release",
                }
            },
        ]
    }
    state.inbox["passive-note"] = AgentInboxItem(
        id="passive-note",
        recipient_agent_id="ui",
        item_type=TeamInboxItemType.PROGRESS_REPORT,
        response_requested=False,
        state=TeamInboxItemState.PENDING,
        created_at=now - timedelta(hours=2),
    )

    payload = runtime_operations_payload(state, now=now)
    inbox_row = next(
        row for row in payload["attention"] if row.get("inbox_item_id") == "inbox-1"
    )
    passive_row = next(
        row for row in payload["attention"] if row.get("inbox_item_id") == "passive-note"
    )

    assert inbox_row["occurred_at"] == occurred_at.isoformat()
    assert inbox_row["age_seconds"] == 10 * 24 * 60 * 60
    assert inbox_row["age_bucket"] == "gte_7d"
    assert inbox_row["attention_group"] == "retained_history"
    assert inbox_row["attention_reason"] == "terminal_work_pending_reconciliation"
    assert inbox_row["reconciliation_required"] is True
    assert inbox_row["project_id"] == "project"
    assert inbox_row["project_ids"] == ["project"]
    assert inbox_row["team_id"] == "team"
    assert {"waiting-task", "task"}.issubset(inbox_row["links"]["task_ids"])
    assert inbox_row["links"]["deliverable_ids"] == ["deliverable"]
    assert inbox_row["links"]["release_package_ids"] == ["release"]
    terminal_context = {
        (context["kind"], context["id"], context["state"])
        for context in inbox_row["terminal_context"]
    }
    assert ("task", "waiting-task", "accepted") in terminal_context
    assert ("deliverable", "deliverable", "accepted") in terminal_context
    assert ("release_package", "release", "accepted") in terminal_context

    assert passive_row["attention_group"] == "retained_history"
    assert passive_row["attention_reason"] == "pending_without_response_obligation"
    assert passive_row["reconciliation_required"] is False

    group_ranks = {
        "active_obligation": 0,
        "failed_attempt": 1,
        "retained_history": 2,
    }
    ranked_groups = [group_ranks[row["attention_group"]] for row in payload["attention"]]
    assert ranked_groups == sorted(ranked_groups)
    assert all(row["occurred_at"] for row in payload["attention"])


def test_runtime_attention_reconciles_pending_inbox_from_its_primary_target() -> None:
    state = _state_with_runtime_operations()
    state.deliverables["deliverable"].state = TeamRecordState.ACCEPTED
    state.release_packages["release"].state = TeamRecordState.PENDING
    state.release_packages["release"].error = None
    state.review_requests["release-review"] = ReviewRequest(
        id="release-review",
        release_package_id="release",
        candidate_commit_sha="candidate-sha",
        reviewer_agent_id="ui",
        state=TeamRecordState.PENDING,
    )
    state.inbox["release-review-inbox"] = AgentInboxItem(
        id="release-review-inbox",
        recipient_agent_id="ui",
        sender_agent_id="lead",
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=True,
        state=TeamInboxItemState.PENDING,
        payload={
            "kind": "release_review_request",
            "review_request_id": "release-review",
            "release_package_id": "release",
        },
    )

    payload = runtime_operations_payload(
        state,
        now=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    row = next(
        item
        for item in payload["attention"]
        if item.get("inbox_item_id") == "release-review-inbox"
    )

    assert row["links"]["deliverable_ids"] == ["deliverable"]
    assert {
        (context["kind"], context["id"], context["state"])
        for context in row["terminal_context"]
    } == {("deliverable", "deliverable", "accepted")}
    assert row["attention_group"] == "active_obligation"
    assert row["attention_reason"] == "awaiting_response"
    assert row["reconciliation_required"] is False

    state.review_requests["release-review"].state = TeamRecordState.ACCEPTED
    reconciled_payload = runtime_operations_payload(
        state,
        now=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    reconciled = next(
        item
        for item in reconciled_payload["attention"]
        if item.get("inbox_item_id") == "release-review-inbox"
    )
    assert reconciled["attention_group"] == "retained_history"
    assert reconciled["attention_reason"] == "terminal_work_pending_reconciliation"
    assert reconciled["reconciliation_required"] is True


@pytest.mark.parametrize(
    "terminal_state",
    [TeamRecordState.ACCEPTED, TeamRecordState.REJECTED],
)
def test_runtime_operations_ignores_failed_attention_for_terminal_release(
    terminal_state: TeamRecordState,
) -> None:
    state = _state_with_runtime_operations()
    state.release_packages["release"].state = terminal_state

    payload = runtime_operations_payload(
        state,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    attention_kinds = {row["kind"] for row in payload["attention"]}
    assert "failed_git_promotion" not in attention_kinds
    assert "blocked_release_package" not in attention_kinds


def test_runtime_operations_ignores_failed_promotion_for_terminal_task() -> None:
    state = _state_with_runtime_operations()
    promotion = state.git_promotions["promotion-1"]
    promotion.release_package_id = None
    promotion.task_id = "blocked-task"
    state.tasks["blocked-task"].state = TeamTaskState.ACCEPTED

    payload = runtime_operations_payload(
        state,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    promotion_ids = {
        row.get("promotion_id")
        for row in payload["attention"]
        if row["kind"] == "failed_git_promotion"
    }
    assert "promotion-1" not in promotion_ids


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


def test_runtime_operations_wake_due_includes_normal_no_reply_inbox() -> None:
    state = _valid_state()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state.agent_runtimes["ui"] = AgentRuntime(
        agent_id="ui",
        last_wake_at=now - timedelta(minutes=20),
        next_wake_at=now + timedelta(minutes=15),
        updated_at=now,
    )
    state.inbox["normal-note"] = AgentInboxItem(
        id="normal-note",
        recipient_agent_id="ui",
        sender_agent_id="lead",
        task_id="task",
        item_type=TeamInboxItemType.PROGRESS_REPORT,
        response_requested=False,
        priority=TeamInboxPriority.NORMAL,
        state=TeamInboxItemState.PENDING,
    )

    payload = runtime_operations_payload(state, now=now)

    ui = next(row for row in payload["agents"] if row["id"] == "ui")
    assert ui["wake_due"] is True
    assert ui["severity"] == "warning"


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


def test_runtime_attention_hides_failed_action_from_older_dispatch() -> None:
    state = _state_with_runtime_operations()
    state.action_records["action-2"] = AgentActionRecord(
        id="action-2",
        agent_id="ui",
        dispatch_id="dispatch-ui-follow-up",
        action={"kind": "build"},
        success=False,
        error="newer build failed",
        created_at=state.action_records["action-1"].created_at + timedelta(seconds=1),
    )

    payload = runtime_operations_payload(state, now=datetime(2026, 1, 1, tzinfo=UTC))

    rows = [row for row in payload["attention"] if row["kind"] == "failed_action_record"]
    assert len(rows) == 1
    assert rows[0]["action_record_id"] == "action-2"
    assert rows[0]["dispatch_id"] == "dispatch-ui-follow-up"
    assert rows[0]["error"] == "newer build failed"


def test_runtime_attention_keeps_undismissed_failure_from_current_dispatch() -> None:
    state = _state_with_runtime_operations()
    state.action_records["action-2"] = AgentActionRecord(
        id="action-2",
        agent_id="ui",
        dispatch_id="dispatch-ui",
        action={"kind": "test"},
        success=False,
        error="test failed",
        created_at=state.action_records["action-1"].created_at + timedelta(seconds=1),
        dismissed_at=datetime(2026, 1, 1, tzinfo=UTC),
        dismissed_by="operator",
    )

    payload = runtime_operations_payload(state, now=datetime(2026, 1, 1, tzinfo=UTC))

    rows = [row for row in payload["attention"] if row["kind"] == "failed_action_record"]
    assert len(rows) == 1
    assert rows[0]["action_record_id"] == "action-1"
    assert rows[0]["dispatch_id"] == "dispatch-ui"
    assert rows[0]["error"] == "build failed"


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

    with pytest.raises(
        ValidationError, match="review profile profile references missing team ghost"
    ):
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

    with pytest.raises(
        ValidationError, match="review profile profile project project belongs to team team"
    ):
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

    with pytest.raises(ValidationError, match="has duplicate criterion result tests"):
        TeamState.model_validate(state.model_dump(mode="python"))


def test_review_rejects_criterion_results_without_profile() -> None:
    state = _valid_state()
    state.review_requests["review"].criteria = [
        ReviewCriterionResult(criterion_id="tests", state="passed"),
    ]

    with pytest.raises(ValidationError, match="has criterion results but no review profile"):
        TeamState.model_validate(state.model_dump(mode="python"))


def test_review_rejects_missing_profile_reference() -> None:
    state = _valid_state().model_dump(mode="python")
    state["review_requests"]["review"]["review_profile_id"] = "ghost"

    with pytest.raises(ValidationError, match="references missing review profile ghost"):
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
