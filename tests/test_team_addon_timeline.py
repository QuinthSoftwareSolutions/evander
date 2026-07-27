from __future__ import annotations

from datetime import UTC, datetime

from evander_team_addon.models import (
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
    TaskCancellation,
    Team,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
)
from evander_team_addon.timeline import derive_team_timeline


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=UTC)


def _timeline_state() -> TeamState:
    return TeamState(
        revision=13,
        teams={
            "alpha-team": Team(
                id="alpha-team",
                name="Alpha",
                manager_agent_id="alpha-manager",
                project_ids=["alpha-project"],
            ),
            "beta-team": Team(
                id="beta-team",
                name="Beta",
                manager_agent_id="beta-manager",
                project_ids=["beta-project"],
            ),
        },
        projects={
            "alpha-project": Project(
                id="alpha-project",
                team_id="alpha-team",
                name="Alpha Project",
                lead_agent_id="alpha-lead",
            ),
            "beta-project": Project(
                id="beta-project",
                team_id="beta-team",
                name="Beta Project",
                lead_agent_id="beta-lead",
            ),
        },
        roles={
            "alpha-team__manager": TeamRole(
                id="alpha-team__manager",
                team_id="alpha-team",
                name="Manager",
            ),
            "alpha-project__project-lead": TeamRole(
                id="alpha-project__project-lead",
                team_id="alpha-team",
                project_id="alpha-project",
                name="Project Lead",
            ),
            "alpha-project__developer": TeamRole(
                id="alpha-project__developer",
                team_id="alpha-team",
                project_id="alpha-project",
                name="Developer",
            ),
            "alpha-project__reviewer": TeamRole(
                id="alpha-project__reviewer",
                team_id="alpha-team",
                project_id="alpha-project",
                name="Reviewer",
            ),
            "beta-team__manager": TeamRole(
                id="beta-team__manager",
                team_id="beta-team",
                name="Manager",
            ),
            "beta-project__project-lead": TeamRole(
                id="beta-project__project-lead",
                team_id="beta-team",
                project_id="beta-project",
                name="Project Lead",
            ),
            "beta-project__developer": TeamRole(
                id="beta-project__developer",
                team_id="beta-team",
                project_id="beta-project",
                name="Developer",
            ),
        },
        agents={
            "alpha-manager": PersistentAgent(
                id="alpha-manager",
                team_id="alpha-team",
                role_id="alpha-team__manager",
                session_id="session-alpha-manager",
                subordinate_agent_ids=["alpha-lead"],
            ),
            "alpha-lead": PersistentAgent(
                id="alpha-lead",
                team_id="alpha-team",
                project_id="alpha-project",
                role_id="alpha-project__project-lead",
                session_id="session-alpha-lead",
                reports_to_agent_id="alpha-manager",
                subordinate_agent_ids=["alpha-dev", "alpha-reviewer"],
            ),
            "alpha-dev": PersistentAgent(
                id="alpha-dev",
                team_id="alpha-team",
                project_id="alpha-project",
                role_id="alpha-project__developer",
                session_id="session-alpha-dev",
                reports_to_agent_id="alpha-lead",
            ),
            "alpha-reviewer": PersistentAgent(
                id="alpha-reviewer",
                team_id="alpha-team",
                project_id="alpha-project",
                role_id="alpha-project__reviewer",
                session_id="session-alpha-reviewer",
                reports_to_agent_id="alpha-lead",
            ),
            "beta-manager": PersistentAgent(
                id="beta-manager",
                team_id="beta-team",
                role_id="beta-team__manager",
                session_id="session-beta-manager",
                subordinate_agent_ids=["beta-lead"],
            ),
            "beta-lead": PersistentAgent(
                id="beta-lead",
                team_id="beta-team",
                project_id="beta-project",
                role_id="beta-project__project-lead",
                session_id="session-beta-lead",
                reports_to_agent_id="beta-manager",
                subordinate_agent_ids=["beta-dev"],
            ),
            "beta-dev": PersistentAgent(
                id="beta-dev",
                team_id="beta-team",
                project_id="beta-project",
                role_id="beta-project__developer",
                session_id="session-beta-dev",
                reports_to_agent_id="beta-lead",
            ),
        },
        tasks={
            "alpha-task": AgentTask(
                id="alpha-task",
                owner_agent_id="alpha-dev",
                project_id="alpha-project",
                priority=1,
                state=TeamTaskState.ACCEPTED,
                title="Alpha task",
                description="Build the alpha slice.",
                created_by_agent_id="alpha-lead",
                created_at=_dt(1, 9),
                updated_at=_dt(1, 10),
            ),
            "beta-task": AgentTask(
                id="beta-task",
                owner_agent_id="beta-dev",
                project_id="beta-project",
                priority=1,
                state=TeamTaskState.ACCEPTED,
                title="Beta task",
                description="Build the beta slice.",
                created_by_agent_id="beta-lead",
                created_at=_dt(1, 19),
                updated_at=_dt(1, 19),
            ),
        },
        deliverables={
            "alpha-deliverable": Deliverable(
                id="alpha-deliverable",
                task_id="alpha-task",
                producer_agent_id="alpha-dev",
                summary="Alpha deliverable",
                state=TeamRecordState.ACCEPTED,
                review_request_ids=["alpha-review"],
                created_at=_dt(1, 11),
            )
        },
        review_requests={
            "alpha-review": ReviewRequest(
                id="alpha-review",
                deliverable_id="alpha-deliverable",
                reviewer_agent_id="alpha-reviewer",
                state=TeamRecordState.ACCEPTED,
                findings=["looks good"],
                created_at=None,
                decision_at=None,
            )
        },
        inbox={
            "alpha-inbox": AgentInboxItem(
                id="alpha-inbox",
                recipient_agent_id="alpha-dev",
                sender_agent_id="alpha-lead",
                task_id="alpha-task",
                item_type=TeamInboxItemType.QUESTION,
                payload={"text": "Please review the draft."},
                state=TeamInboxItemState.HANDLED,
                created_at=_dt(1, 10, 30),
                updated_at=_dt(1, 11, 30),
            )
        },
        action_records={
            "alpha-action": AgentActionRecord(
                id="alpha-action",
                agent_id="alpha-dev",
                dispatch_id="alpha-dispatch",
                action={"type": "status_report", "summary": "Alpha action complete"},
                success=False,
                error="action failed",
                created_at=_dt(1, 13),
            )
        },
        release_packages={
            "alpha-release": ReleasePackage(
                id="alpha-release",
                project_id="alpha-project",
                deliverable_ids=["alpha-deliverable"],
                artifact_refs=["artifacts/alpha-release.tar.gz"],
                git_promotion_record_ids=["alpha-promotion"],
                state=TeamRecordState.BLOCKED,
                created_at=_dt(1, 18),
                updated_at=None,
                error="blocked waiting on release checks",
            )
        },
        ephemeral_assignments={
            "alpha-ephemeral": EphemeralAssignment(
                id="alpha-ephemeral",
                parent_agent_id="alpha-dev",
                project_id="alpha-project",
                task_id="alpha-task",
                session_id="session-alpha-ephemeral",
                title="Alpha ephemeral",
                prompt="Build a quick validation slice.",
                state=EphemeralAssignmentState.FAILED,
                created_at=_dt(1, 14),
                updated_at=_dt(1, 15),
                error="spawn failed",
            )
        },
        git_promotions={
            "alpha-promotion": GitPromotionRecord(
                id="alpha-promotion",
                project_id="alpha-project",
                actor_agent_id="alpha-lead",
                source_branch="team/alpha/features/alpha-task",
                target_branch="team/alpha/releases/alpha-release",
                promotion_kind="release",
                task_id="alpha-task",
                deliverable_id="alpha-deliverable",
                release_package_id="alpha-release",
                state="failed",
                created_at=_dt(1, 16),
                updated_at=_dt(1, 17),
                error="push rejected",
            )
        },
    )


def test_derive_team_timeline_covers_mixed_entries_and_missing_timestamps() -> None:
    state = _timeline_state()

    timeline = derive_team_timeline(state)
    entries = timeline["entries"]
    entry_ids = [entry["id"] for entry in entries]

    assert timeline["revision"] == 13
    assert timeline["derived"] is True
    assert timeline["audit_log"] is False
    assert timeline["timestampless_count"] == 3
    assert len(entries) == 15
    assert {entry["type"] for entry in entries} == {
        "task",
        "inbox_item",
        "deliverable",
        "review",
        "action",
        "ephemeral_assignment",
        "git_promotion",
        "release_package",
    }
    assert entry_ids[:3] == [
        "task:beta-task:created",
        "release:alpha-release:created",
        "git-promotion:alpha-promotion:result",
    ]
    assert entry_ids[-3:] == [
        "release:alpha-release:blocked",
        "review:alpha-review:decision",
        "review:alpha-review:created",
    ]

    entries_by_id = {entry["id"]: entry for entry in entries}
    task_created = entries_by_id["task:alpha-task:created"]
    assert task_created == {
        "id": "task:alpha-task:created",
        "type": "task",
        "category": "created",
        "timestamp": "2026-01-01T09:00:00Z",
        "timestamp_source": "created_at",
        "timestamp_missing": False,
        "actor_agent_id": "alpha-lead",
        "actor_subject": "alpha-lead",
        "related_agent_ids": ["alpha-dev", "alpha-lead"],
        "team_id": "alpha-team",
        "project_id": "alpha-project",
        "task_id": "alpha-task",
        "deliverable_id": None,
        "review_request_id": None,
        "inbox_item_id": None,
        "action_record_id": None,
        "ephemeral_assignment_id": None,
        "git_promotion_id": None,
        "release_package_id": None,
        "status": "accepted",
        "attention_needed": False,
        "subject": "Alpha task",
        "change": None,
        "reason": None,
        "process": None,
        "summary": "Task created: Alpha task",
    }

    review_created = entries_by_id["review:alpha-review:created"]
    assert review_created["timestamp"] is None
    assert review_created["timestamp_missing"] is True
    assert review_created["status"] == "accepted"
    assert review_created["attention_needed"] is False

    review_decision = entries_by_id["review:alpha-review:decision"]
    assert review_decision["timestamp"] is None
    assert review_decision["timestamp_missing"] is True
    assert review_decision["status"] == "accepted"
    assert review_decision["attention_needed"] is False

    release_blocked = entries_by_id["release:alpha-release:blocked"]
    assert release_blocked["timestamp"] is None
    assert release_blocked["timestamp_missing"] is True
    assert release_blocked["status"] == "blocked"
    assert release_blocked["attention_needed"] is True

    promotion_requested = entries_by_id["git-promotion:alpha-promotion:requested"]
    assert promotion_requested["status"] == "failed"
    assert promotion_requested["attention_needed"] is True


def test_derive_team_timeline_filters_by_team_project_agent_type_status_and_attention() -> None:
    state = _timeline_state()

    leader_view = derive_team_timeline(
        state,
        team_id="alpha-team",
        project_id="alpha-project",
        agent_id="alpha-lead",
        types="task,review",
        statuses=["accepted"],
        attention_needed=False,
    )

    assert [entry["id"] for entry in leader_view["entries"]] == [
        "task:alpha-task:updated",
        "task:alpha-task:created",
        "review:alpha-review:decision",
        "review:alpha-review:created",
    ]
    assert all(entry["team_id"] == "alpha-team" for entry in leader_view["entries"])
    assert all(entry["project_id"] == "alpha-project" for entry in leader_view["entries"])
    assert all("alpha-lead" in entry["related_agent_ids"] for entry in leader_view["entries"])
    assert all(entry["status"] == "accepted" for entry in leader_view["entries"])
    assert all(entry["attention_needed"] is False for entry in leader_view["entries"])

    attention_view = derive_team_timeline(state, attention_needed=True)
    assert [entry["id"] for entry in attention_view["entries"]] == [
        "release:alpha-release:created",
        "git-promotion:alpha-promotion:result",
        "git-promotion:alpha-promotion:requested",
        "ephemeral:alpha-ephemeral:state",
        "ephemeral:alpha-ephemeral:requested",
        "action:alpha-action:created",
        "release:alpha-release:blocked",
    ]
    assert all(entry["attention_needed"] is True for entry in attention_view["entries"])

    beta_view = derive_team_timeline(state, team_id="beta-team")
    assert [entry["id"] for entry in beta_view["entries"]] == ["task:beta-task:created"]
    assert all(entry["team_id"] == "beta-team" for entry in beta_view["entries"])


def test_derive_team_timeline_facets_cover_filtered_history_before_paging() -> None:
    state = _timeline_state()

    timeline = derive_team_timeline(state, limit=1)

    assert len(timeline["entries"]) == 1
    assert timeline["facets"] == {
        "types": [
            "action",
            "deliverable",
            "ephemeral_assignment",
            "git_promotion",
            "inbox_item",
            "release_package",
            "review",
            "task",
        ],
        "statuses": ["accepted", "blocked", "failed", "handled"],
    }

    accepted = derive_team_timeline(
        state,
        types="task",
        statuses="accepted",
        limit=1,
    )

    assert accepted["facets"]["types"] == ["deliverable", "review", "task"]
    assert accepted["facets"]["statuses"] == ["accepted"]


def test_derive_team_timeline_keeps_dismissed_failed_action_out_of_attention() -> None:
    state = _timeline_state()
    state.action_records["alpha-action"].dismissed_at = _dt(1, 14)
    state.action_records["alpha-action"].dismissed_by = "operator"

    attention_view = derive_team_timeline(state, attention_needed=True)
    all_entries = derive_team_timeline(state)
    action_entry = next(
        entry for entry in all_entries["entries"] if entry["id"] == "action:alpha-action:created"
    )

    assert action_entry["status"] == "failed"
    assert action_entry["attention_needed"] is False
    assert "action:alpha-action:created" not in [entry["id"] for entry in attention_view["entries"]]


def test_derive_team_timeline_normalizes_naive_timestamps_for_sorting() -> None:
    state = _timeline_state()
    review = state.review_requests["alpha-review"]
    review.created_at = datetime(2026, 1, 1, 12, 30)
    review.decision_at = datetime(2026, 1, 1, 12, 45)

    timeline = derive_team_timeline(state, types="review")

    assert [entry["id"] for entry in timeline["entries"]] == [
        "review:alpha-review:decision",
        "review:alpha-review:created",
    ]


def test_derive_team_timeline_uses_current_promotion_state_for_requested_entries() -> None:
    state = _timeline_state()
    promotion = state.git_promotions["alpha-promotion"]
    promotion.state = "succeeded"
    promotion.error = None

    timeline = derive_team_timeline(state)
    entries_by_id = {entry["id"]: entry for entry in timeline["entries"]}
    requested = entries_by_id["git-promotion:alpha-promotion:requested"]

    assert requested["status"] == "succeeded"
    assert requested["attention_needed"] is False

    attention_ids = [
        entry["id"] for entry in derive_team_timeline(state, attention_needed=True)["entries"]
    ]
    assert "git-promotion:alpha-promotion:requested" not in attention_ids
    assert "git-promotion:alpha-promotion:result" not in attention_ids

    pending_ids = [
        entry["id"] for entry in derive_team_timeline(state, statuses="pending")["entries"]
    ]
    assert "git-promotion:alpha-promotion:requested" not in pending_ids


def test_derived_activity_uses_recorded_initiators_without_inventing_actors() -> None:
    state = _timeline_state()
    state.agents["alpha-lead"].display_name = "Ada Lead"
    state.agents["alpha-reviewer"].display_name = "Rita Reviewer"
    state.review_requests["alpha-review"].requested_by_agent_id = "alpha-lead"

    entries = {
        entry["id"]: entry
        for entry in derive_team_timeline(state)["entries"]
    }

    assert entries["task:alpha-task:created"]["actor_subject"] == "Ada Lead"
    assert entries["task:alpha-task:updated"]["actor_agent_id"] is None
    assert entries["task:alpha-task:updated"]["actor_subject"] is None
    assert entries["task:alpha-task:updated"]["summary"] == (
        "Task entered accepted: Alpha task"
    )
    assert entries["task:alpha-task:updated"]["change"]["before_known"] is False

    assert entries["inbox:alpha-inbox:created"]["actor_subject"] == "Ada Lead"
    assert entries["inbox:alpha-inbox:handled"]["actor_agent_id"] is None
    assert entries["inbox:alpha-inbox:handled"]["actor_subject"] is None
    assert entries["inbox:alpha-inbox:handled"]["change"] == {
        "field": "State",
        "before": "Pending",
        "after": "Handled",
        "before_known": True,
    }

    assert entries["review:alpha-review:created"]["actor_agent_id"] == "alpha-lead"
    assert entries["review:alpha-review:created"]["actor_subject"] == "Ada Lead"
    assert entries["review:alpha-review:decision"]["actor_agent_id"] == "alpha-reviewer"
    assert entries["review:alpha-review:decision"]["actor_subject"] == "Rita Reviewer"

    assert entries["git-promotion:alpha-promotion:requested"]["actor_agent_id"] == (
        "alpha-lead"
    )
    assert entries["git-promotion:alpha-promotion:result"]["actor_agent_id"] is None
    assert entries["git-promotion:alpha-promotion:result"]["process"] == (
        "Git promotion worker"
    )

    assert entries["release:alpha-release:created"]["actor_agent_id"] is None
    assert entries["release:alpha-release:blocked"]["actor_agent_id"] is None
    assert "alpha-lead" in entries["release:alpha-release:created"][
        "related_agent_ids"
    ]


def test_derived_activity_exposes_only_honest_changes_reasons_and_human_subjects() -> None:
    state = _timeline_state()
    state.agents["alpha-lead"].display_name = "Ada Lead"
    state.review_requests["alpha-review"].requested_by_agent_id = "alpha-lead"
    cancellation_time = _dt(2, 8)
    task = state.tasks["alpha-task"]
    task.state = TeamTaskState.CANCELLED
    task.state_entered_at = cancellation_time
    task.updated_at = cancellation_time
    task.cancellation = TaskCancellation(
        reason_code="superseded",
        reason="A replacement task owns the work.",
        actor_kind="agent",
        actor_id="alpha-lead",
        cancelled_at=cancellation_time,
    )

    entries = {
        entry["id"]: entry
        for entry in derive_team_timeline(state)["entries"]
    }
    cancelled = entries["task:alpha-task:cancelled"]
    assert cancelled["subject"] == "Alpha task"
    assert cancelled["summary"] == "Task cancelled: Alpha task"
    assert "alpha-task" not in cancelled["summary"]
    assert cancelled["actor_subject"] == "Ada Lead"
    assert cancelled["change"] == {
        "field": "State",
        "before": None,
        "after": "Cancelled",
        "before_known": False,
    }
    assert cancelled["reason"] == "A replacement task owns the work."

    review_decision = entries["review:alpha-review:decision"]
    assert review_decision["subject"] == "Alpha task"
    assert review_decision["summary"] == "Review accepted: Alpha task"
    assert review_decision["change"]["before_known"] is False
    assert review_decision["change"]["after"] == "Accepted"
    assert review_decision["reason"] == "looks good"

    promotion_result = entries["git-promotion:alpha-promotion:result"]
    assert promotion_result["change"] == {
        "field": "State",
        "before": "Pending",
        "after": "Failed",
        "before_known": True,
    }
    assert promotion_result["reason"] == "push rejected"
    assert "alpha-promotion" not in promotion_result["summary"]

    assert entries["action:alpha-action:created"]["reason"] == "action failed"
    assert entries["ephemeral:alpha-ephemeral:state"]["reason"] == "spawn failed"
    assert entries["release:alpha-release:blocked"]["reason"] == (
        "blocked waiting on release checks"
    )
    assert entries["release:alpha-release:blocked"]["subject"] == (
        "Alpha Project: Alpha task"
    )
    assert "alpha-release" not in entries["release:alpha-release:blocked"]["summary"]


def test_derived_activity_resolves_release_review_context_and_request_process() -> None:
    state = _timeline_state()
    state.review_requests["release-review"] = ReviewRequest(
        id="release-review",
        release_package_id="alpha-release",
        candidate_commit_sha="a" * 40,
        reviewer_agent_id="alpha-reviewer",
        requested_by_actor="workflow:release-review",
        state=TeamRecordState.PENDING,
        created_at=_dt(2, 9),
    )

    timeline = derive_team_timeline(state, types="review")
    requested = next(
        entry
        for entry in timeline["entries"]
        if entry["id"] == "review:release-review:created"
    )

    assert requested["team_id"] == "alpha-team"
    assert requested["project_id"] == "alpha-project"
    assert requested["task_id"] is None
    assert requested["deliverable_id"] is None
    assert requested["release_package_id"] == "alpha-release"
    assert requested["subject"] == "Alpha Project: Alpha task"
    assert requested["summary"] == "Review requested: Alpha Project: Alpha task"
    assert requested["actor_agent_id"] is None
    assert requested["actor_subject"] == "workflow:release-review"
    assert requested["process"] == "Workflow"
    assert {"alpha-lead", "alpha-dev", "alpha-reviewer"}.issubset(
        requested["related_agent_ids"]
    )
