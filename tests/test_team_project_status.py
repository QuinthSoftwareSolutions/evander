from __future__ import annotations

from datetime import UTC, datetime

from evander_team_addon.models import (
    AgentTask,
    HumanReviewAssignment,
    HumanReviewFinding,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    Project,
    ProjectStatus,
    ReleasePackage,
    ReviewBlockKind,
    ReviewRequest,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)
from evander_team_addon.services.domain.project_status import (
    _recalculate_project_status,
    project_blocking_sources,
)


def test_project_blocking_sources_includes_release_level_agent_review() -> None:
    state = TeamState.empty()
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
    )
    state.release_packages["release"] = ReleasePackage(
        id="release",
        project_id="project",
    )
    state.review_requests["release-review"] = ReviewRequest(
        id="release-review",
        release_package_id="release",
        candidate_commit_sha="candidate",
        reviewer_agent_id="reviewer",
        state=TeamRecordState.BLOCKED,
        block_kind=ReviewBlockKind.SYSTEM,
        findings=["The release test environment is unavailable."],
    )

    rows = project_blocking_sources(state, "project")

    assert [
        row
        for row in rows
        if row["kind"] == "review_request"
    ] == [
        {
            "kind": "review_request",
            "id": "release-review",
            "state": "blocked",
            "summary": "The release test environment is unavailable.",
        }
    ]


def test_project_completion_waits_for_human_request_finding_and_successor() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    state = TeamState.empty()
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
    )
    state.release_packages["release"] = ReleasePackage(
        id="release",
        project_id="project",
        state=TeamRecordState.ACCEPTED,
    )
    request = HumanReviewRequest(
        id="human-request",
        release_package_id="release",
        candidate_commit_sha="a" * 40,
        candidate_tree_hash="b" * 40,
        requirement_snapshot=HumanReviewRequirementSnapshot(
            directory_path="root",
            configured_group_ids=["release-reviewers"],
            initially_eligible_user_ids=["reviewer"],
        ),
        current_assignment=HumanReviewAssignment(
            eligible_user_ids=["reviewer"],
        ),
        state="pending",
        ready_at=now,
    )
    state.human_review_requests[request.id] = request

    _recalculate_project_status(state, "project")
    assert state.projects["project"].status == ProjectStatus.ACTIVE

    request.state = "approved"
    request.ready_at = None
    request.completed_at = now
    state.tasks["correction-task"] = AgentTask(
        id="correction-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        state=TeamTaskState.FINISHED,
        title="Completed correction",
        description="The task resolved, but its finding still needs human confirmation.",
        created_by_agent_id="lead",
    )
    finding = HumanReviewFinding(
        id="finding",
        source_human_review_request_id=request.id,
        text="Confirm the corrected behavior.",
        correction_task_id="correction-task",
    )
    state.human_review_findings[finding.id] = finding

    _recalculate_project_status(state, "project")
    assert state.projects["project"].status == ProjectStatus.ACTIVE

    finding.state = "resolved"
    finding.resolved_by_human_review_request_id = request.id
    finding.resolved_at = now
    _recalculate_project_status(state, "project")
    assert state.projects["project"].status == ProjectStatus.COMPLETED

    state.release_packages["successor"] = ReleasePackage(
        id="successor",
        project_id="project",
        supersedes_release_package_id="release",
        state=TeamRecordState.PENDING,
    )
    _recalculate_project_status(state, "project")
    assert state.projects["project"].status == ProjectStatus.ACTIVE


def test_terminal_release_resolves_obsolete_sibling_review_blocker() -> None:
    state = TeamState.empty()
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
    )
    state.release_packages["release"] = ReleasePackage(
        id="release",
        project_id="project",
        state=TeamRecordState.REJECTED,
        review_request_ids=["blocked-review", "rejecting-review"],
    )
    state.review_requests["blocked-review"] = ReviewRequest(
        id="blocked-review",
        release_package_id="release",
        candidate_commit_sha="candidate",
        reviewer_agent_id="reviewer-a",
        state=TeamRecordState.BLOCKED,
        block_kind=ReviewBlockKind.SYSTEM,
        findings=["The review environment is unavailable."],
    )
    state.review_requests["rejecting-review"] = ReviewRequest(
        id="rejecting-review",
        release_package_id="release",
        candidate_commit_sha="candidate",
        reviewer_agent_id="reviewer-b",
        state=TeamRecordState.REJECTED,
        findings=["The candidate cannot be released."],
    )
    state.blockers["blocked-review-blocker"] = TeamBlocker(
        id="blocked-review-blocker",
        team_id="team",
        project_id="project",
        source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
        source_id="blocked-review",
        owner_agent_id="lead",
        summary="Release review blocked",
    )

    assert project_blocking_sources(state, "project") == []

    _recalculate_project_status(state, "project")

    blocker = state.blockers["blocked-review-blocker"]
    assert blocker.state == TeamBlockerState.RESOLVED
    assert blocker.terminal_reference_ids == ["release"]
    assert state.projects["project"].status == ProjectStatus.COMPLETED


def test_rejected_task_remains_terminal_for_project_completion() -> None:
    state = TeamState.empty()
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
    )
    state.tasks["rejected-task"] = AgentTask(
        id="rejected-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        state=TeamTaskState.REJECTED,
        title="Rejected historical task",
        description="This persisted terminal disposition must remain resolved.",
        created_by_agent_id="lead",
    )

    _recalculate_project_status(state, "project")

    assert state.projects["project"].status == ProjectStatus.COMPLETED
