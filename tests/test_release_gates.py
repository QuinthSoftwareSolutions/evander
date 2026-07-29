from __future__ import annotations

import pytest

from evander_team_addon.models import (
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ReleaseCandidateManifest,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseReviewRequirement,
    ReleaseVerificationCommand,
    ReleaseVerificationCommandResult,
    ReleaseVerificationRun,
    ReviewProfile,
    ReviewRequest,
    TeamRecordState,
    TeamState,
)
from evander_team_addon.services.domain.release_gates import (
    create_release_reviews_or_approve,
    record_release_verification_result,
    retry_release_gate,
)


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"


def _project(*, release_policy: ProjectReleasePolicy) -> Project:
    return Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        git=ProjectGitConfig(
            managed_repo_id=MANAGED_REPO_ID,
            base_branch="main",
        ),
        release_policy=release_policy,
    )


def _release_and_promotion(
    *,
    verification_commands: list[ReleaseVerificationCommand] | None = None,
    review_requirements: list[ReleaseReviewRequirement] | None = None,
) -> tuple[ReleasePackage, GitPromotionRecord]:
    manifest = ReleaseCandidateManifest(
        project_id="project",
        release_package_id="release",
        candidate_commit_sha="candidate-sha",
        release_branch="team/team/releases/release",
        verification_commands=verification_commands or [],
        review_requirements=review_requirements or [],
    )
    promotion = GitPromotionRecord(
        id="release-promotion",
        project_id="project",
        release_package_id="release",
        actor_agent_id="lead",
        source_branch="team/team/features/task",
        target_branch="team/team/releases/release",
        promotion_kind="release",
        state="succeeded",
        commit_sha="candidate-sha",
    )
    release = ReleasePackage(
        id="release",
        project_id="project",
        release_branch="team/team/releases/release",
        candidate_commit_sha="candidate-sha",
        manifest=manifest,
        git_promotion_record_ids=[promotion.id],
    )
    return release, promotion


def _result() -> dict[str, list[object]]:
    return {"events": [], "git_promotion_event_ids": []}


def test_release_reviews_use_requirements_frozen_in_candidate_manifest() -> None:
    frozen_requirement = ReleaseReviewRequirement(
        reviewer_agent_id="reviewer",
        review_profile_id="profile",
    )
    release, promotion = _release_and_promotion(review_requirements=[frozen_requirement])
    state = TeamState.empty()
    state.projects["project"] = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    state.agents["reviewer"] = PersistentAgent(
        id="reviewer",
        team_id="team",
        role_id="reviewer-role",
        session_id="session-reviewer",
    )
    state.review_profiles["profile"] = ReviewProfile(
        id="profile",
        team_id="team",
        project_id="project",
        name="Frozen release review",
    )

    result = _result()
    create_release_reviews_or_approve(
        state,
        release=release,
        release_promotion=promotion,
        result=result,
    )

    assert release.approved_commit_sha is None
    assert len(release.review_request_ids) == 1
    review = state.review_requests[release.review_request_ids[0]]
    assert review.reviewer_agent_id == frozen_requirement.reviewer_agent_id
    assert review.review_profile_id == frozen_requirement.review_profile_id


def test_successful_verification_only_policy_approves_release() -> None:
    command = ReleaseVerificationCommand(
        name="tests",
        argv=["pytest", "-q"],
    )
    release, promotion = _release_and_promotion(verification_commands=[command])
    run = ReleaseVerificationRun(
        id="verification-run",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        session_id="verification-session",
        command_results=[ReleaseVerificationCommandResult(name=command.name)],
    )
    release.verification_run_ids = [run.id]
    state = TeamState.empty()
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(verification_commands=[command])
    )
    state.release_packages[release.id] = release
    state.release_verification_runs[run.id] = run
    state.git_promotions[promotion.id] = promotion

    result = _result()
    record_release_verification_result(
        state,
        release_package_id=release.id,
        verification_run_id=run.id,
        candidate_commit_sha="candidate-sha",
        verification_state="passed",
        failure_kind=None,
        command_results=[
            ReleaseVerificationCommandResult(
                name=command.name,
                state="passed",
                exit_code=0,
            )
        ],
        error=None,
        result=result,
    )

    assert release.state == TeamRecordState.PENDING
    assert release.approved_commit_sha == "candidate-sha"
    canonical_push = next(
        row for row in state.git_promotions.values() if row.promotion_kind == "canonical_push"
    )
    assert canonical_push.sources[0].expected_commit_sha == "candidate-sha"


def test_candidate_verification_failure_wakes_project_lead_for_delegated_rework() -> None:
    command = ReleaseVerificationCommand(name="tests", argv=["pytest", "-q"])
    release, _promotion = _release_and_promotion(verification_commands=[command])
    run = ReleaseVerificationRun(
        id="verification-run",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        session_id="verification-session",
        command_results=[ReleaseVerificationCommandResult(name=command.name)],
    )
    release.verification_run_ids = [run.id]
    state = TeamState.empty()
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(verification_commands=[command])
    )
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
    )
    state.agents["coder"] = PersistentAgent(
        id="coder",
        team_id="team",
        role_id="coder-role",
        session_id="session-coder",
        reports_to_agent_id="lead",
    )
    state.release_packages[release.id] = release
    state.release_verification_runs[run.id] = run

    result = _result()
    record_release_verification_result(
        state,
        release_package_id=release.id,
        verification_run_id=run.id,
        candidate_commit_sha="candidate-sha",
        verification_state="failed",
        failure_kind="candidate",
        command_results=[
            ReleaseVerificationCommandResult(
                name=command.name,
                state="failed",
                exit_code=1,
                log_excerpt="assertion failed",
            )
        ],
        error="release tests failed",
        result=result,
    )

    assert release.state == TeamRecordState.REJECTED
    assert len(result["created_inbox_item_ids"]) == 1
    item = state.inbox[result["created_inbox_item_ids"][0]]
    assert item.recipient_agent_id == "lead"
    assert item.priority.value == "high"
    assert item.payload["kind"] == "release_verification_candidate_failure"
    assert item.payload["verification_run_id"] == run.id
    assert item.payload["candidate_commit_sha"] == "candidate-sha"
    assert item.payload["failed_command_results"] == [
        {
            "name": "tests",
            "state": "failed",
            "exit_code": 1,
            "log_excerpt": "assertion failed",
        }
    ]
    assert "delegate" in item.payload["next_action"]
    assert "Pin the failed candidate_commit_sha" in item.payload["next_action"]
    assert "dependency_task_ids for every accepted Git-backed predecessor" in (
        item.payload["next_action"]
    )
    assert "originating_task_id alone does not seed" in item.payload["next_action"]
    assert "base-branch-only implementation" in item.payload["next_action"]
    assert not any(row.recipient_agent_id == "coder" for row in state.inbox.values())
    wake = next(
        event
        for event in result["events"]
        if event["event_type"] == "team.agent.wake_requested"
    )
    assert wake["payload"]["agent_id"] == "lead"
    assert wake["payload"]["reason"] == "inbox"


def test_verification_retry_uses_commands_frozen_in_release_manifest() -> None:
    frozen_command = ReleaseVerificationCommand(name="frozen-tests", argv=["pytest", "-q"])
    release, _promotion = _release_and_promotion(verification_commands=[frozen_command])
    release.state = TeamRecordState.BLOCKED
    failed_run = ReleaseVerificationRun(
        id="verification-run",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        session_id="verification-session",
        state="failed",
        failure_kind="infrastructure",
        command_results=[ReleaseVerificationCommandResult(name=frozen_command.name)],
    )
    release.verification_run_ids = [failed_run.id]
    state = TeamState.empty()
    state.projects["project"] = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    state.release_verification_runs[failed_run.id] = failed_run

    result: dict[str, object] = {}
    retry_release_gate(
        state,
        release=release,
        gate_kind="verification",
        gate_id=failed_run.id,
        reason="Verifier infrastructure recovered.",
        requested_by="operator",
        result=result,
    )

    replacement = state.release_verification_runs[str(result["replacement_gate_id"])]
    assert [item.name for item in replacement.command_results] == [frozen_command.name]
    assert release.state == TeamRecordState.PENDING


def test_agent_verification_retry_escalates_after_agent_requested_retry_fails() -> None:
    frozen_command = ReleaseVerificationCommand(name="frozen-tests", argv=["pytest", "-q"])
    release, _promotion = _release_and_promotion(verification_commands=[frozen_command])
    release.state = TeamRecordState.BLOCKED
    first_run = ReleaseVerificationRun(
        id="verification-run-1",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        session_id="verification-session-1",
        state="failed",
        failure_kind="infrastructure",
        command_results=[ReleaseVerificationCommandResult(name=frozen_command.name)],
    )
    release.verification_run_ids = [first_run.id]
    state = TeamState.empty()
    state.projects["project"] = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    state.release_verification_runs[first_run.id] = first_run

    first_retry: dict[str, object] = {}
    retry_release_gate(
        state,
        release=release,
        gate_kind="verification",
        gate_id=first_run.id,
        reason="The disconnected verification worker was replaced.",
        requested_by="agent:lead",
        result=first_retry,
    )

    second_run = state.release_verification_runs[str(first_retry["replacement_gate_id"])]
    second_run.state = "failed"
    second_run.failure_kind = "infrastructure"
    release.state = TeamRecordState.BLOCKED

    with pytest.raises(
        ValueError,
        match="agent-requested verification retry already failed",
    ):
        retry_release_gate(
            state,
            release=release,
            gate_kind="verification",
            gate_id=second_run.id,
            reason="Try the unchanged candidate again.",
            requested_by="agent:lead",
            result={},
        )

    assert second_run.superseded_by_verification_run_id is None
    assert release.state == TeamRecordState.BLOCKED

    operator_retry: dict[str, object] = {}
    retry_release_gate(
        state,
        release=release,
        gate_kind="verification",
        gate_id=second_run.id,
        reason="The operator applied and verified a new worker resource policy.",
        requested_by="operator:release-admin",
        result=operator_retry,
    )

    third_run = state.release_verification_runs[str(operator_retry["replacement_gate_id"])]
    assert third_run.attempt_number == 3
    assert third_run.retry_requested_by == "operator:release-admin"
    assert release.state == TeamRecordState.PENDING


def test_retry_keeps_release_blocked_until_every_blocked_review_is_replaced() -> None:
    requirements = [
        ReleaseReviewRequirement(
            reviewer_agent_id="reviewer-a",
            review_profile_id="profile-a",
        ),
        ReleaseReviewRequirement(
            reviewer_agent_id="reviewer-b",
            review_profile_id="profile-b",
        ),
    ]
    release, _promotion = _release_and_promotion(review_requirements=requirements)
    release.state = TeamRecordState.BLOCKED
    release.review_request_ids = ["review-a", "review-b"]
    release.gate_failure = ReleaseGateFailure(
        stage="review",
        kind="blocked",
        source_id="review-a",
        message="review A infrastructure failed",
    )
    release.error = release.gate_failure.message
    state = TeamState.empty()
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(review_requirements=requirements)
    )
    for suffix in ("a", "b"):
        agent_id = f"reviewer-{suffix}"
        profile_id = f"profile-{suffix}"
        state.agents[agent_id] = PersistentAgent(
            id=agent_id,
            team_id="team",
            role_id="reviewer-role",
            session_id=f"session-{agent_id}",
        )
        state.review_profiles[profile_id] = ReviewProfile(
            id=profile_id,
            team_id="team",
            project_id="project",
            name=f"Review {suffix.upper()}",
        )
        state.review_requests[f"review-{suffix}"] = ReviewRequest(
            id=f"review-{suffix}",
            release_package_id=release.id,
            candidate_commit_sha="candidate-sha",
            reviewer_agent_id=agent_id,
            review_profile_id=profile_id,
            reviewed_commit_sha="candidate-sha",
            state=TeamRecordState.BLOCKED,
            findings=[f"review {suffix.upper()} infrastructure failed"],
        )

    first_result: dict[str, object] = {}
    retry_release_gate(
        state,
        release=release,
        gate_kind="review",
        gate_id="review-a",
        reason="Reviewer A is available again.",
        requested_by="operator",
        result=first_result,
    )

    assert release.state == TeamRecordState.BLOCKED
    assert release.gate_failure is not None
    assert release.gate_failure.source_id == "review-b"

    second_result: dict[str, object] = {}
    retry_release_gate(
        state,
        release=release,
        gate_kind="review",
        gate_id="review-b",
        reason="Reviewer B is available again.",
        requested_by="operator",
        result=second_result,
    )

    assert release.state == TeamRecordState.PENDING
    assert release.gate_failure is None
    assert second_result["retried_gate_id"] == "review-b"


@pytest.mark.parametrize(
    ("scope_change", "error"),
    [
        ("reviewer_team", "release reviewer reviewer is missing or outside team team"),
        ("profile_project", "release review profile profile does not apply to project project"),
    ],
)
def test_review_retry_revalidates_reviewer_and_profile_scope(
    scope_change: str,
    error: str,
) -> None:
    requirement = ReleaseReviewRequirement(
        reviewer_agent_id="reviewer",
        review_profile_id="profile",
    )
    release, _promotion = _release_and_promotion(review_requirements=[requirement])
    release.state = TeamRecordState.BLOCKED
    release.review_request_ids = ["review"]
    state = TeamState.empty()
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(review_requirements=[requirement])
    )
    state.agents["reviewer"] = PersistentAgent(
        id="reviewer",
        team_id="team",
        role_id="reviewer-role",
        session_id="session-reviewer",
    )
    state.review_profiles["profile"] = ReviewProfile(
        id="profile",
        team_id="team",
        project_id="project",
        name="Release review",
    )
    state.review_requests["review"] = ReviewRequest(
        id="review",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        reviewer_agent_id="reviewer",
        review_profile_id="profile",
        reviewed_commit_sha="candidate-sha",
        state=TeamRecordState.BLOCKED,
    )
    if scope_change == "reviewer_team":
        state.agents["reviewer"].team_id = "other-team"
    else:
        state.review_profiles["profile"].project_id = "other-project"

    with pytest.raises(ValueError, match=error):
        retry_release_gate(
            state,
            release=release,
            gate_kind="review",
            gate_id="review",
            reason="Retry after infrastructure recovery.",
            requested_by="operator",
            result={},
        )

    assert release.review_request_ids == ["review"]
    assert state.review_requests["review"].superseded_by_review_request_id is None
