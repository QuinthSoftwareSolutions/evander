from __future__ import annotations

from datetime import UTC, datetime, timedelta

from truman_team_addon.models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentTask,
    Deliverable,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)
from truman_team_addon.timeline import derive_team_timeline


def _timeline_state() -> TeamState:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    later = base + timedelta(hours=1)
    later1_5 = base + timedelta(minutes=90)
    later2 = base + timedelta(hours=2)
    later3 = base + timedelta(hours=3)
    later4 = base + timedelta(hours=4)
    later5 = base + timedelta(hours=5)
    later6 = base + timedelta(hours=6)
    later7 = base + timedelta(hours=7)
    later8 = base + timedelta(hours=8)
    later9 = base + timedelta(hours=9)
    later10 = base + timedelta(hours=10)

    manager_a = PersistentAgent(
        id="manager-a",
        team_id="team-a",
        role="manager",
        session_id="session-manager-a",
        subordinate_agent_ids=["lead-a"],
    )
    lead_a = PersistentAgent(
        id="lead-a",
        team_id="team-a",
        project_id="project-a",
        role="lead",
        session_id="session-lead-a",
        reports_to_agent_id="manager-a",
        subordinate_agent_ids=["producer-a", "reviewer-a"],
    )
    producer_a = PersistentAgent(
        id="producer-a",
        team_id="team-a",
        project_id="project-a",
        role="producer",
        session_id="session-producer-a",
        reports_to_agent_id="lead-a",
    )
    reviewer_a = PersistentAgent(
        id="reviewer-a",
        team_id="team-a",
        project_id="project-a",
        role="reviewer",
        session_id="session-reviewer-a",
        reports_to_agent_id="lead-a",
    )
    manager_b = PersistentAgent(
        id="manager-b",
        team_id="team-b",
        role="manager",
        session_id="session-manager-b",
        subordinate_agent_ids=["lead-b"],
    )
    lead_b = PersistentAgent(
        id="lead-b",
        team_id="team-b",
        project_id="project-b",
        role="lead",
        session_id="session-lead-b",
        reports_to_agent_id="manager-b",
        subordinate_agent_ids=["agent-b"],
    )
    agent_b = PersistentAgent(
        id="agent-b",
        team_id="team-b",
        project_id="project-b",
        role="producer",
        session_id="session-agent-b",
        reports_to_agent_id="lead-b",
    )

    return TeamState(
        revision=7,
        teams={
            "team-a": Team(
                id="team-a",
                name="Team A",
                manager_agent_id="manager-a",
                project_ids=["project-a"],
            ),
            "team-b": Team(
                id="team-b",
                name="Team B",
                manager_agent_id="manager-b",
                project_ids=["project-b"],
            ),
        },
        projects={
            "project-a": Project(
                id="project-a",
                team_id="team-a",
                name="Project A",
                lead_agent_id="lead-a",
            ),
            "project-b": Project(
                id="project-b",
                team_id="team-b",
                name="Project B",
                lead_agent_id="lead-b",
            ),
        },
        agents={
            "manager-a": manager_a,
            "lead-a": lead_a,
            "producer-a": producer_a,
            "reviewer-a": reviewer_a,
            "manager-b": manager_b,
            "lead-b": lead_b,
            "agent-b": agent_b,
        },
        tasks={
            "task-a": AgentTask(
                id="task-a",
                owner_agent_id="producer-a",
                project_id="project-a",
                priority=1,
                state=TeamTaskState.IN_PROGRESS,
                title="Task A",
                description="Primary task",
                created_by_agent_id="lead-a",
                created_at=base,
                updated_at=later,
            ),
            "task-b": AgentTask(
                id="task-b",
                owner_agent_id="agent-b",
                project_id="project-b",
                priority=2,
                state=TeamTaskState.PENDING,
                title="Task B",
                description="Scope for team B",
                created_by_agent_id="lead-b",
                created_at=later10,
                updated_at=later10,
            ),
        },
        deliverables={
            "deliverable-a": Deliverable(
                id="deliverable-a",
                task_id="task-a",
                producer_agent_id="producer-a",
                summary="Deliverable A",
                state=TeamRecordState.PENDING,
                created_at=base,
            ),
        },
        review_requests={
            "review-a": ReviewRequest(
                id="review-a",
                deliverable_id="deliverable-a",
                reviewer_agent_id="reviewer-a",
                state=TeamRecordState.BLOCKED,
                findings=["needs changes"],
                created_at=None,
                decision_at=later3,
            ),
        },
        inbox={
            "inbox-a": AgentInboxItem(
                id="inbox-a",
                recipient_agent_id="lead-a",
                sender_agent_id="producer-a",
                task_id="task-a",
                item_type=TeamInboxItemType.STATUS_REQUEST,
                state=TeamInboxItemState.HANDLED,
                created_at=base,
                updated_at=later2,
            ),
        },
        action_records={
            "action-a": AgentActionRecord(
                id="action-a",
                agent_id="producer-a",
                dispatch_id="dispatch-a",
                action={"type": "deploy", "summary": "Failed deploy"},
                success=False,
                error="boom",
                created_at=base,
            ),
        },
        release_packages={
            "release-c": ReleasePackage(
                id="release-c",
                project_id="project-a",
                deliverable_ids=["deliverable-a"],
                artifact_refs=[],
                feature_branches=[],
                release_branch="team-a/releases/release-c",
                canonical_branch="releases/project-a/release-c",
                git_promotion_record_ids=[],
                state=TeamRecordState.PENDING,
                created_at=later1_5,
                updated_at=later8,
            ),
            "release-a": ReleasePackage(
                id="release-a",
                project_id="project-a",
                deliverable_ids=["deliverable-a"],
                artifact_refs=[],
                feature_branches=[],
                release_branch="team-a/releases/release-a",
                canonical_branch="releases/project-a/release-a",
                git_promotion_record_ids=[],
                state=TeamRecordState.BLOCKED,
                error="blocked by review",
                created_at=later7,
                updated_at=later8,
            ),
            "release-b": ReleasePackage(
                id="release-b",
                project_id="project-a",
                deliverable_ids=["deliverable-a"],
                artifact_refs=[],
                feature_branches=[],
                release_branch="team-a/releases/release-b",
                canonical_branch="releases/project-a/release-b",
                git_promotion_record_ids=[],
                state=TeamRecordState.ACCEPTED,
                published_at=later10,
                created_at=later9,
                updated_at=later10,
            ),
        },
        ephemeral_assignments={
            "ephemeral-a": EphemeralAssignment(
                id="ephemeral-a",
                parent_agent_id="producer-a",
                project_id="project-a",
                task_id="task-a",
                session_id="session-ephemeral-a",
                title="Ephemeral A",
                prompt="Work on a draft implementation.",
                state=EphemeralAssignmentState.RUNNING,
                created_at=later4,
                updated_at=later6,
            ),
        },
        git_promotions={
            "promotion-a": GitPromotionRecord(
                id="promotion-a",
                project_id="project-a",
                actor_agent_id="producer-a",
                source_branch="feature/task-a",
                target_branch="review/task-a",
                promotion_kind="review",
                task_id="task-a",
                deliverable_id="deliverable-a",
                state="failed",
                error="push failed",
                created_at=later5,
                updated_at=later6,
            ),
        },
    )


def test_derive_team_timeline_covers_families_and_orders_entries() -> None:
    state = _timeline_state()

    timeline = derive_team_timeline(state)
    entries = timeline["entries"]

    assert timeline["revision"] == 7
    assert timeline["derived"] is True
    assert timeline["audit_log"] is False
    assert timeline["timestampless_count"] == 1

    assert [entry["id"] for entry in entries[:4]] == [
        "action:action-a:created",
        "deliverable:deliverable-a:created",
        "inbox:inbox-a:created",
        "task:task-a:created",
    ]
    assert entries[-1]["id"] == "review:review-a:created"
    assert entries[-1]["timestamp"] is None
    assert entries[-1]["timestamp_missing"] is True

    deliverable = next(
        entry for entry in entries if entry["id"] == "deliverable:deliverable-a:created"
    )
    assert deliverable["type"] == "deliverable"
    assert deliverable["category"] == "created"
    assert deliverable["actor_agent_id"] == "producer-a"
    assert "lead-a" in deliverable["related_agent_ids"]
    assert deliverable["task_id"] == "task-a"
    assert deliverable["deliverable_id"] == "deliverable-a"


def test_derive_team_timeline_filters_by_scope_type_status_and_attention() -> None:
    state = _timeline_state()

    team_entries = derive_team_timeline(state, team_id="team-a")["entries"]
    project_entries = derive_team_timeline(state, project_id="project-a")["entries"]
    agent_entries = derive_team_timeline(state, agent_id="lead-a")["entries"]
    review_entries = derive_team_timeline(state, types=["review"])["entries"]
    blocked_entries = derive_team_timeline(state, statuses=["blocked"])["entries"]
    attention_entries = derive_team_timeline(state, attention_needed=True)["entries"]
    calm_entries = derive_team_timeline(state, attention_needed=False)["entries"]
    all_entries = derive_team_timeline(state)["entries"]

    assert "task:task-b:created" not in {entry["id"] for entry in team_entries}
    assert "task:task-b:created" not in {entry["id"] for entry in project_entries}
    assert "deliverable:deliverable-a:created" in {entry["id"] for entry in agent_entries}
    assert "review:review-a:created" in {entry["id"] for entry in agent_entries}

    assert {entry["type"] for entry in review_entries} == {"review"}
    assert {entry["status"] for entry in review_entries} == {"blocked"}
    assert {entry["type"] for entry in blocked_entries} == {"review", "release_package"}
    assert all(entry["status"] == "blocked" for entry in blocked_entries)
    assert "release:release-c:created" in {entry["id"] for entry in all_entries}
    assert "release:release-c:blocked" not in {entry["id"] for entry in all_entries}

    assert attention_entries
    assert all(entry["attention_needed"] is True for entry in attention_entries)
    assert all(
        entry["status"] in {"blocked", "failed", "pending"} or entry["type"] == "release_package"
        for entry in attention_entries
    )

    assert calm_entries
    assert all(entry["attention_needed"] is False for entry in calm_entries)
    assert "task:task-a:created" in {entry["id"] for entry in calm_entries}
    assert "release:release-b:published" in {entry["id"] for entry in calm_entries}
