from __future__ import annotations

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
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(allow_ungated=True)
    )
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
