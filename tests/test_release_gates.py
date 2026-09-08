from __future__ import annotations

from pathlib import Path

import pytest

from evander_team_addon.models import (
    AgentInboxItem,
    AgentProjectAssignment,
    AgentStatus,
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DeliverableReviewManifest,
    DeliverableVerification,
    GitPromotionRecord,
    GitWorkContext,
    GitWorkContextState,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ReleaseCandidateManifest,
    ReleaseCandidateDeliverableEvidence,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseReviewRequirement,
    ReleaseVerificationCommand,
    ReleaseVerificationCommandResult,
    ReleaseVerificationRun,
    ReviewProfile,
    ReviewRequest,
    TeamRecordState,
    TeamDispatch,
    TeamState,
    Team,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRole,
    TeamTaskState,
    release_package_payload_with_derived,
)
from evander_team_addon.services.domain.release_gates import (
    _materialize_candidate_verification_correction,
    advance_release_gates,
    begin_release_gates,
    create_release_reviews_or_approve,
    materialize_release_review_rejection_correction,
    record_release_verification_result,
    retry_release_gate,
)
from evander_team_addon.services.domain.review import _cascade_review_decision
from evander_team_addon.services.domain.release_packages import (
    _build_release_package,
    _successor_release_request_error,
)
from evander_team_addon.services.domain.task_assessment import (
    TaskAssessmentBoundaryError,
    require_task_can_finish,
)
from evander_team_addon.services.release_gate_service import TeamReleaseGateService
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.repository import TeamStateConflictError
from evander_team_addon.services.admin_service import TeamAdminService


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
        base_commit_sha="base-sha",
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
        base_commit_sha="base-sha",
        candidate_commit_sha="candidate-sha",
        manifest=manifest,
        git_promotion_record_ids=[promotion.id],
    )
    return release, promotion


def _result() -> dict[str, list[object]]:
    return {"events": [], "git_promotion_event_ids": []}


def test_terminal_release_replay_ignores_non_leaf_lineage_state() -> None:
    release, promotion = _release_and_promotion()
    release.state = TeamRecordState.ACCEPTED
    release.approved_commit_sha = "candidate-sha"
    release.published_commit_sha = "candidate-sha"
    release.lineage_root_release_package_id = release.id
    successor = ReleasePackage(
        id="successor",
        project_id="project",
        supersedes_release_package_id=release.id,
        lineage_root_release_package_id=release.id,
        lineage_sequence=1,
    )
    state = TeamState.empty()
    state.release_packages[release.id] = release
    state.release_packages[successor.id] = successor
    state.git_promotions[promotion.id] = promotion
    result = _result()

    begin_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        candidate_commit_sha="candidate-sha",
        candidate_tree_hash="candidate-tree",
        result=result,
    )
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result=result,
    )

    assert release.state == TeamRecordState.ACCEPTED
    assert release.approved_commit_sha == "candidate-sha"
    assert release.published_commit_sha == "candidate-sha"
    assert release.gate_failure is None
    assert release.error is None
    assert state.blockers == {}


def test_lineage_wait_pins_candidate_and_enters_successor_recovery_path() -> None:
    state, release, promotion, _reviews = _release_review_cohort_fixture()
    release.lineage_root_release_package_id = release.id
    release.lineage_sequence = 0
    state.tasks["parallel-remediation"] = AgentTask(
        id="parallel-remediation",
        owner_agent_id="lead",
        project_id="project",
        priority=0,
        state=TeamTaskState.IN_PROGRESS,
        title="Parallel remediation",
        description="Complete the candidate-bound remediation.",
        created_by_agent_id="lead",
        release_base_package_id=release.id,
    )
    result = _result()

    begin_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        candidate_commit_sha="candidate-sha",
        candidate_tree_hash="candidate-tree",
        result=result,
    )

    assert release.state == TeamRecordState.REJECTED
    assert release.candidate_commit_sha == "candidate-sha"
    assert release.candidate_tree_hash == "candidate-tree"
    assert release.gate_failure is not None
    assert release.gate_failure.stage == "lineage"
    assert release.gate_failure.kind == "convergence"
    assert "single successor" in (release.error or "")
    blocker = state.blockers[result["release_remediation_blocker_id"]]
    assert blocker.reason_code == "release_gate_lineage_convergence"


def _release_review_cohort_fixture() -> tuple[
    TeamState,
    ReleasePackage,
    GitPromotionRecord,
    dict[str, ReviewRequest],
]:
    requirements = [
        ReleaseReviewRequirement(
            reviewer_agent_id=f"reviewer-{suffix}",
            review_profile_id=f"profile-{suffix}",
        )
        for suffix in ("a", "b")
    ]
    release, promotion = _release_and_promotion(review_requirements=requirements)
    release.owner_agent_id = "lead"
    release.candidate_tree_hash = "d" * 40
    assert release.manifest is not None
    release.manifest.candidate_tree_hash = release.candidate_tree_hash
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="lead",
        project_ids=["project"],
    )
    state.roles["lead-role"] = TeamRole(
        id="lead-role",
        team_id="team",
        project_id="project",
        name="Lead",
    )
    state.roles["reviewer-role"] = TeamRole(
        id="reviewer-role",
        team_id="team",
        project_id="project",
        name="Reviewer",
    )
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(review_requirements=requirements)
    )
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
    )
    state.project_assignments["lead-project"] = AgentProjectAssignment(
        id="lead-project",
        team_id="team",
        project_id="project",
        agent_id="lead",
    )
    for suffix in ("a", "b"):
        reviewer_id = f"reviewer-{suffix}"
        profile_id = f"profile-{suffix}"
        state.agents[reviewer_id] = PersistentAgent(
            id=reviewer_id,
            team_id="team",
            role_id="reviewer-role",
            session_id=f"session-{reviewer_id}",
        )
        state.review_profiles[profile_id] = ReviewProfile(
            id=profile_id,
            team_id="team",
            project_id="project",
            name=f"Review {suffix.upper()}",
        )
    state.release_packages[release.id] = release
    state.git_promotions[promotion.id] = promotion
    create_release_reviews_or_approve(
        state,
        release=release,
        release_promotion=promotion,
        result=_result(),
    )
    reviews = {
        review.reviewer_agent_id: review
        for review in (state.review_requests[review_id] for review_id in release.review_request_ids)
    }
    return state, release, promotion, reviews


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
    assert release.review_cohort_id == f"release-review-cohort-{release.id}"
    assert release.review_cohort_sealed_at is not None
    assert review.review_cohort_id == release.review_cohort_id
    handoff = state.inbox[result["created_inbox_item_ids"][0]]
    assert handoff.payload["review_cohort_id"] == release.review_cohort_id


def test_release_review_cohort_waits_then_materializes_one_consolidated_correction() -> None:
    state, release, _promotion, reviews = _release_review_cohort_fixture()
    first = _result()

    _cascade_review_decision(
        state,
        review=reviews["reviewer-a"],
        decision_state=TeamRecordState.REJECTED,
        findings=["Use the retained run origin."],
        projects_to_recalc=set(),
        result=first,
    )

    assert release.state == TeamRecordState.PENDING
    assert release.gate_failure is None
    assert release.review_correction_task_id is None
    assert first.get("created_task_ids") is None
    assert reviews["reviewer-b"].state == TeamRecordState.PENDING
    cohort = release_package_payload_with_derived(state, release)["release_review_cohort"]
    assert cohort["status"] == "collecting"
    assert cohort["resolved"] == 1
    assert cohort["total"] == 2
    assert cohort["pending_review_request_ids"] == [reviews["reviewer-b"].id]

    second = _result()
    _cascade_review_decision(
        state,
        review=reviews["reviewer-b"],
        decision_state=TeamRecordState.REJECTED,
        findings=["Cover the retained origin in the client regression."],
        projects_to_recalc=set(),
        result=second,
    )

    assert release.state == TeamRecordState.REJECTED
    assert release.review_correction_task_id is not None
    correction = state.tasks[release.review_correction_task_id]
    assert "Use the retained run origin." in correction.description
    assert "Cover the retained origin in the client regression." in correction.description
    assert second["created_task_ids"] == [correction.id]
    cohort = release_package_payload_with_derived(state, release)["release_review_cohort"]
    assert cohort["status"] == "changes_requested"
    assert cohort["resolved"] == cohort["total"] == 2
    correction_notices = [
        state.inbox[item_id]
        for item_id in second["created_inbox_item_ids"]
        if state.inbox[item_id].payload.get("kind") == "release_review_rejection_correction_work"
    ]
    assert len(correction_notices) == 1
    assert correction_notices[0].payload["review_request_ids"] == [
        reviews["reviewer-a"].id,
        reviews["reviewer-b"].id,
    ]


def test_rejected_review_exception_preserves_remaining_cohort_correction_work() -> None:
    state, release, _promotion, reviews = _release_review_cohort_fixture()
    decision_result = _result()
    for reviewer_id, finding in (
        ("reviewer-a", "First rejected criterion."),
        ("reviewer-b", "Second rejected criterion."),
    ):
        decision_result = _result()
        _cascade_review_decision(
            state,
            review=reviews[reviewer_id],
            decision_state=TeamRecordState.REJECTED,
            findings=[finding],
            projects_to_recalc=set(),
            result=decision_result,
        )

    correction_task_id = release.review_correction_task_id
    assert correction_task_id is not None
    correction_inbox_ids = [
        item_id
        for item_id in decision_result["created_inbox_item_ids"]
        if state.inbox[item_id].payload.get("kind") == "release_review_rejection_correction_work"
    ]
    assert len(correction_inbox_ids) == 1
    first_rejected_review_id = release.gate_failure.source_id
    assert first_rejected_review_id is not None

    first_retry = _result()
    retry_release_gate(
        state,
        release=release,
        gate_kind="review",
        gate_id=first_rejected_review_id,
        reason="The inherited candidate metadata is explicitly excepted.",
        requested_by="operator:release-admin",
        result=first_retry,
        allow_rejected_review_exception=True,
    )

    remaining_rejected_ids = [
        review.id
        for review in state.review_requests.values()
        if review.release_package_id == release.id
        and review.state == TeamRecordState.REJECTED
        and review.superseded_by_review_request_id is None
    ]
    assert len(remaining_rejected_ids) == 1
    assert release.state == TeamRecordState.REJECTED
    assert release.gate_failure is not None
    assert release.gate_failure.source_id == remaining_rejected_ids[0]
    assert release.review_correction_task_id == correction_task_id
    correction_task = state.tasks[correction_task_id]
    assert correction_task.state == TeamTaskState.PENDING
    assert correction_task.cancellation is None
    assert first_retry["retained_release_review_correction_task_ids"] == [correction_task_id]
    assert first_retry["updated_task_ids"] == [correction_task_id]
    assert first_retry["updated_correction_inbox_item_ids"] == correction_inbox_ids
    remaining_review = state.review_requests[remaining_rejected_ids[0]]
    excepted_review = state.review_requests[first_rejected_review_id]
    assert all(finding in correction_task.description for finding in remaining_review.findings)
    assert all(finding not in correction_task.description for finding in excepted_review.findings)
    correction_inbox = state.inbox[correction_inbox_ids[0]]
    assert correction_inbox.state == TeamInboxItemState.PENDING
    assert correction_inbox.payload["review_request_id"] == remaining_review.id
    assert correction_inbox.payload["review_request_ids"] == [remaining_review.id]
    assert correction_inbox.payload["rejected_reviews"] == [
        {
            "review_request_id": remaining_review.id,
            "reviewer_agent_id": remaining_review.reviewer_agent_id,
            "findings": remaining_review.findings,
        }
    ]
    assert correction_inbox.payload["findings"] == remaining_review.findings

    first_replacement = state.review_requests[str(first_retry["replacement_gate_id"])]
    _cascade_review_decision(
        state,
        review=first_replacement,
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=_result(),
    )

    assert release.state == TeamRecordState.REJECTED
    assert release.review_correction_task_id == correction_task_id
    assert correction_task.state == TeamTaskState.PENDING
    assert state.inbox[correction_inbox_ids[0]].state == TeamInboxItemState.PENDING

    second_retry = _result()
    retry_release_gate(
        state,
        release=release,
        gate_kind="review",
        gate_id=remaining_rejected_ids[0],
        reason="The second inherited criterion is also explicitly excepted.",
        requested_by="operator:release-admin",
        result=second_retry,
        allow_rejected_review_exception=True,
    )

    assert release.state == TeamRecordState.PENDING
    assert release.gate_failure is None
    assert all(review.superseded_by_review_request_id is not None for review in reviews.values())
    assert release.review_correction_task_id is None
    assert correction_task.state == TeamTaskState.CANCELLED
    assert correction_task.cancellation is not None
    assert correction_task.cancellation.reason_code == "release_review_exception"
    assert second_retry["cancelled_task_ids"] == [correction_task_id]
    assert second_retry["retired_release_review_correction_task_ids"] == [correction_task_id]
    assert second_retry["handled_correction_inbox_item_ids"] == correction_inbox_ids
    assert state.inbox[correction_inbox_ids[0]].state == TeamInboxItemState.HANDLED


def test_rejected_review_exception_reactivates_correction_for_replacement_rejection() -> None:
    state, release, _promotion, reviews = _release_review_cohort_fixture()
    _cascade_review_decision(
        state,
        review=reviews["reviewer-a"],
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=_result(),
    )
    initial_rejection = _result()
    _cascade_review_decision(
        state,
        review=reviews["reviewer-b"],
        decision_state=TeamRecordState.REJECTED,
        findings=["Correct the original defect."],
        projects_to_recalc=set(),
        result=initial_rejection,
    )
    correction_task_id = release.review_correction_task_id
    assert correction_task_id is not None
    original_inbox_id = next(
        item_id
        for item_id in initial_rejection["created_inbox_item_ids"]
        if state.inbox[item_id].payload.get("kind")
        == "release_review_rejection_correction_work"
    )

    retry_result = _result()
    retry_release_gate(
        state,
        release=release,
        gate_kind="review",
        gate_id=reviews["reviewer-b"].id,
        reason="The original finding is explicitly excepted.",
        requested_by="operator:release-admin",
        result=retry_result,
        allow_rejected_review_exception=True,
    )

    correction_task = state.tasks[correction_task_id]
    assert correction_task.state == TeamTaskState.CANCELLED
    assert correction_task.cancellation is not None
    assert state.inbox[original_inbox_id].state == TeamInboxItemState.HANDLED
    replacement = state.review_requests[str(retry_result["replacement_gate_id"])]

    replacement_rejection = _result()
    _cascade_review_decision(
        state,
        review=replacement,
        decision_state=TeamRecordState.REJECTED,
        findings=["The replacement review found a different defect."],
        projects_to_recalc=set(),
        result=replacement_rejection,
    )

    assert release.state == TeamRecordState.REJECTED
    assert release.review_correction_task_id == correction_task_id
    assert correction_task.state == TeamTaskState.PENDING
    assert correction_task.cancellation is None
    assert replacement_rejection["reactivated_task_ids"] == [correction_task_id]
    replacement_inbox = next(
        state.inbox[item_id]
        for item_id in replacement_rejection["created_inbox_item_ids"]
        if state.inbox[item_id].payload.get("kind")
        == "release_review_rejection_correction_work"
    )
    assert replacement_inbox.state == TeamInboxItemState.PENDING
    assert replacement_inbox.task_id == correction_task_id
    assert replacement_inbox.payload["review_request_id"] == replacement.id
    TeamState.model_validate(state.model_dump(mode="python"))


def test_rejected_review_exception_refuses_predecessor_with_successor() -> None:
    state, release, _promotion, reviews = _release_review_cohort_fixture()
    _cascade_review_decision(
        state,
        review=reviews["reviewer-a"],
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=_result(),
    )
    _cascade_review_decision(
        state,
        review=reviews["reviewer-b"],
        decision_state=TeamRecordState.REJECTED,
        findings=["Correct the candidate."],
        projects_to_recalc=set(),
        result=_result(),
    )
    rejected_review_id = release.gate_failure.source_id
    assert rejected_review_id is not None
    state.release_packages["successor"] = ReleasePackage(
        id="successor",
        project_id=release.project_id,
        supersedes_release_package_id=release.id,
    )

    with pytest.raises(ValueError, match="already has successor release package.*successor"):
        retry_release_gate(
            state,
            release=release,
            gate_kind="review",
            gate_id=rejected_review_id,
            reason="Except the rejected criterion.",
            requested_by="operator:release-admin",
            result=_result(),
            allow_rejected_review_exception=True,
        )

    assert state.review_requests[rejected_review_id].superseded_by_review_request_id is None
    assert release.state == TeamRecordState.REJECTED


@pytest.mark.parametrize("handoff_kind", ["review", "acceptance"])
def test_rejected_review_exception_retires_active_correction_handoffs(
    handoff_kind: str,
) -> None:
    state, release, _promotion, reviews = _release_review_cohort_fixture()
    _cascade_review_decision(
        state,
        review=reviews["reviewer-a"],
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=_result(),
    )
    _cascade_review_decision(
        state,
        review=reviews["reviewer-b"],
        decision_state=TeamRecordState.REJECTED,
        findings=["Correct the candidate."],
        projects_to_recalc=set(),
        result=_result(),
    )
    rejected_review_id = release.gate_failure.source_id
    correction_task_id = release.review_correction_task_id
    assert rejected_review_id is not None
    assert correction_task_id is not None
    correction_task = state.tasks[correction_task_id]
    correction_task.state = TeamTaskState.WAITING_FOR_REVIEW
    correction_task.deliverable_ids = ["active-correction-deliverable"]
    context = GitWorkContext(
        id="active-correction-context",
        agent_id=correction_task.owner_agent_id,
        task_id=correction_task.id,
        agent_branch="agents/release-correction/active",
        base_branch=release.release_branch or "main",
        base_commit=release.candidate_commit_sha,
    )
    state.git_work_contexts[context.id] = context
    deliverable = Deliverable(
        id="active-correction-deliverable",
        task_id=correction_task.id,
        producer_agent_id=correction_task.owner_agent_id,
        summary="Correction awaiting a handoff decision.",
        state=TeamRecordState.PENDING,
        git_refs=DeliverableGitRefs(
            branch_context_id=context.id,
            agent_branch=context.agent_branch,
            commit_sha="f" * 40,
            submitted_commit_sha="f" * 40,
        ),
    )
    state.deliverables[deliverable.id] = deliverable
    handoff_review: ReviewRequest | None = None
    if handoff_kind == "review":
        handoff_review = ReviewRequest(
            id="active-correction-review",
            deliverable_id=deliverable.id,
            reviewer_agent_id="reviewer-a",
            state=TeamRecordState.IN_PROGRESS,
        )
        deliverable.review_request_ids = [handoff_review.id]
        state.review_requests[handoff_review.id] = handoff_review
        recipient_agent_id = handoff_review.reviewer_agent_id
        payload = {
            "kind": "review_request",
            "review_request_id": handoff_review.id,
            "deliverable_id": deliverable.id,
        }
    else:
        recipient_agent_id = "reviewer-a"
        deliverable.acceptance_recipient_agent_id = recipient_agent_id
        payload = {
            "kind": "superior_acceptance_requested",
            "deliverable_id": deliverable.id,
        }
    state.inbox["active-correction-handoff"] = AgentInboxItem(
        id="active-correction-handoff",
        recipient_agent_id=recipient_agent_id,
        sender_agent_id=correction_task.owner_agent_id,
        task_id=correction_task.id,
        item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
        priority=TeamInboxPriority.HIGH,
        payload=payload,
    )

    result = _result()
    retry_release_gate(
        state,
        release=release,
        gate_kind="review",
        gate_id=rejected_review_id,
        reason="Except the only rejected criterion.",
        requested_by="operator:release-admin",
        result=result,
        allow_rejected_review_exception=True,
    )

    assert correction_task.state == TeamTaskState.CANCELLED
    assert deliverable.state == TeamRecordState.REJECTED
    assert deliverable.acceptance_recipient_agent_id is None
    if handoff_review is not None:
        assert handoff_review.retired_at is not None
        assert handoff_review.retired_by == "operator:release-admin"
    assert state.inbox["active-correction-handoff"].state == TeamInboxItemState.HANDLED
    assert context.state != GitWorkContextState.ACTIVE
    assert result["git_context_finalization"][correction_task.id] == {
        "submitted": 1,
        "abandoned": 0,
    }
    TeamState.model_validate(state.model_dump(mode="python"))


def test_release_review_cohort_all_acceptance_advances_release_once_complete() -> None:
    state, release, _promotion, reviews = _release_review_cohort_fixture()

    _cascade_review_decision(
        state,
        review=reviews["reviewer-a"],
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=_result(),
    )
    assert release.approved_commit_sha is None

    _cascade_review_decision(
        state,
        review=reviews["reviewer-b"],
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=_result(),
    )

    assert release.approved_commit_sha == release.candidate_commit_sha
    assert any(
        promotion.promotion_kind == "canonical_push" for promotion in state.git_promotions.values()
    )


def test_release_review_cohort_blocked_retry_preserves_slot_and_delays_correction() -> None:
    state, release, _promotion, reviews = _release_review_cohort_fixture()
    cohort_id = release.review_cohort_id
    blocked = reviews["reviewer-a"]

    _cascade_review_decision(
        state,
        review=blocked,
        decision_state=TeamRecordState.BLOCKED,
        findings=["The candidate checkout is unavailable."],
        projects_to_recalc=set(),
        result=_result(),
    )

    assert release.state == TeamRecordState.BLOCKED
    assert release.review_correction_task_id is None
    retry_result = _result()
    retry_release_gate(
        state,
        release=release,
        gate_kind="review",
        gate_id=blocked.id,
        reason="The candidate checkout was restored.",
        requested_by="operator",
        result=retry_result,
    )
    replacement = state.review_requests[str(retry_result["replacement_gate_id"])]
    assert replacement.review_cohort_id == cohort_id
    assert replacement.reviewer_agent_id == blocked.reviewer_agent_id
    assert replacement.review_profile_id == blocked.review_profile_id
    assert release.state == TeamRecordState.PENDING
    assert release.review_correction_task_id is None


def test_release_review_cohort_prevalidates_every_member_before_creation() -> None:
    requirements = [
        ReleaseReviewRequirement(reviewer_agent_id="reviewer", review_profile_id="profile"),
        ReleaseReviewRequirement(reviewer_agent_id="missing", review_profile_id="missing-profile"),
    ]
    release, promotion = _release_and_promotion(review_requirements=requirements)
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="lead",
        project_ids=["project"],
    )
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(review_requirements=requirements)
    )
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
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
        name="Review",
    )

    create_release_reviews_or_approve(
        state,
        release=release,
        release_promotion=promotion,
        result=_result(),
    )

    assert release.state == TeamRecordState.REJECTED
    assert release.review_cohort_id is None
    assert release.review_cohort_sealed_at is None
    assert release.review_request_ids == []
    assert state.review_requests == {}


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


def test_candidate_verification_failure_creates_candidate_rooted_correction_task() -> None:
    command = ReleaseVerificationCommand(name="tests", argv=["pytest", "-q"])
    release, _promotion = _release_and_promotion(verification_commands=[command])
    release.owner_agent_id = "lead"
    release.git_base_branch = "team/team/releases/previous"
    release.deliverable_ids = ["source-deliverable"]
    assert release.manifest is not None
    release.manifest.deliverables = [
        ReleaseCandidateDeliverableEvidence(
            deliverable_id="source-deliverable",
            review_manifest=DeliverableReviewManifest(
                base_commit_sha="base-sha",
                reviewed_commit_sha="candidate-sha",
                changed_paths=["M\tSrc/App/OpcWriteStep.cs"],
            ),
        )
    ]
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
    state.project_assignments["coder-project"] = AgentProjectAssignment(
        id="coder-project",
        team_id="team",
        project_id="project",
        agent_id="coder",
    )
    state.deliverables["source-deliverable"] = Deliverable(
        id="source-deliverable",
        task_id="source-task",
        producer_agent_id="coder",
        summary="Source change",
        state=TeamRecordState.ACCEPTED,
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
                log_excerpt=(
                    "/home/agent/worktrees/repo/Src/App/OpcWriteStep.cs(17,1): "
                    "error CS0105: duplicate using"
                ),
            )
        ],
        error="release tests failed",
        result=result,
    )

    assert release.state == TeamRecordState.REJECTED
    assert run.correction_task_id is not None
    task = state.tasks[run.correction_task_id]
    assert task.owner_agent_id == "coder"
    assert task.project_id == release.project_id
    assert task.state.value == "pending"
    assert task.created_by_actor == "system:release-verification-correction"
    assert task.git_base_branch == release.release_branch
    assert task.integration_boundary is False
    assert task.deliverable_contract is not None
    assert task.deliverable_contract.require_git_provenance is True
    assert task.mutation_policy is not None
    assert task.mutation_policy.policy_id == "system-remediation-topology-v1"
    assert task.mutation_policy.allow_child_tasks is False
    assert task.mutation_policy.allow_agent_cancellation is False
    assert set(task.mutation_policy.locked_fields) >= {
        "dependency_task_ids",
        "deliverable_contract",
        "git_base_branch",
        "integration_boundary",
    }
    assert "Candidate commit: candidate-sha" in task.description
    assert "Do not merge or rebase the current project base branch" in task.description
    assert "Do not amend, reset, rebase, or otherwise rewrite" in task.description
    assert "must have the candidate commit as an ancestor" in task.description
    assert "rewriting pinned commit metadata or history" in task.description
    assert "promotion freezes that metadata into the next candidate" in task.description
    assert "submit the changed Git work as the correction deliverable" in task.description
    assert "Team automatically creates the successor release" in task.description
    assert "create a replacement release" not in task.description
    assert "select reviewers" not in task.description
    assert "workflow policy owns" not in task.description
    assert result["created_task_ids"] == [task.id]
    assert result["verification_correction_task_id"] == task.id
    assert result["verification_correction_task_owner_agent_id"] == "coder"
    assert result["verification_correction_assignment_reason"] == "single_failed_path_source_owner"
    assert len(result["created_inbox_item_ids"]) == 1
    item = state.inbox[result["created_inbox_item_ids"][0]]
    assert item.recipient_agent_id == "coder"
    assert item.task_id == task.id
    assert item.priority.value == "high"
    assert item.payload["kind"] == "release_verification_correction_work"
    assert item.payload["verification_run_id"] == run.id
    assert item.payload["candidate_commit_sha"] == "candidate-sha"
    assert item.payload["assignment_reason"] == "single_failed_path_source_owner"
    assert item.payload["failed_command_results"] == [
        {
            "name": "tests",
            "state": "failed",
            "exit_code": 1,
            "log_excerpt": (
                "/home/agent/worktrees/repo/Src/App/OpcWriteStep.cs(17,1): "
                "error CS0105: duplicate using"
            ),
        }
    ]
    assert "rooted at candidate_commit_sha" in item.payload["next_action"]
    assert "Do not merge or rebase" in item.payload["next_action"]
    assert "Team automatically creates the successor release" in item.payload["next_action"]
    assert "reassign this task instead of creating a child" in item.payload["next_action"]
    assert "create a replacement release" not in item.payload["next_action"]
    wake = next(
        event for event in result["events"] if event["event_type"] == "team.agent.wake_requested"
    )
    assert wake["payload"]["agent_id"] == "coder"
    assert wake["payload"]["reason"] == "inbox"

    task.git_base_branch = release.git_base_branch
    manifest_release_branch = release.manifest.release_branch
    release.release_branch = None
    repair_result = _result()
    assert run.finished_at is not None
    _materialize_candidate_verification_correction(
        state,
        release=release,
        run=run,
        error="release tests failed",
        result=repair_result,
        now=run.finished_at,
    )
    assert task.git_base_branch == manifest_release_branch
    assert repair_result["updated_task_ids"] == [task.id]


def test_candidate_verification_failure_falls_back_to_team_manager() -> None:
    command = ReleaseVerificationCommand(name="tests", argv=["pytest", "-q"])
    release, _promotion = _release_and_promotion(verification_commands=[command])
    run = ReleaseVerificationRun(
        id="verification-run",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        session_id="verification-session",
    )
    release.verification_run_ids = [run.id]
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="manager",
        project_ids=["project"],
    )
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(verification_commands=[command])
    )
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
        status=AgentStatus.PAUSED,
    )
    state.agents["manager"] = PersistentAgent(
        id="manager",
        team_id="team",
        role_id="manager-role",
        session_id="session-manager",
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
                log_excerpt="release tests failed",
            )
        ],
        error="release tests failed",
        result=result,
    )

    assert run.correction_task_id is not None
    task = state.tasks[run.correction_task_id]
    assert task.owner_agent_id == "manager"
    assert result["verification_correction_assignment_reason"] == "team_manager_fallback"
    item = state.inbox[result["created_inbox_item_ids"][0]]
    assert item.recipient_agent_id == "manager"


def test_rejected_release_review_creates_candidate_rooted_correction_task() -> None:
    release, _promotion = _release_and_promotion()
    release.owner_agent_id = "lead"
    release.git_base_branch = "team/team/releases/previous"
    release.candidate_tree_hash = "d" * 40
    assert release.manifest is not None
    release.manifest.candidate_tree_hash = release.candidate_tree_hash
    review = ReviewRequest(
        id="release-review",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        reviewed_commit_sha="candidate-sha",
        reviewer_agent_id="reviewer",
    )
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="lead",
        project_ids=["project"],
    )
    state.roles["lead-role"] = TeamRole(
        id="lead-role",
        team_id="team",
        project_id="project",
        name="Lead",
    )
    state.roles["reviewer-role"] = TeamRole(
        id="reviewer-role",
        team_id="team",
        project_id="project",
        name="Reviewer",
    )
    state.projects["project"] = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
    )
    state.agents["reviewer"] = PersistentAgent(
        id="reviewer",
        team_id="team",
        role_id="reviewer-role",
        session_id="session-reviewer",
    )
    state.project_assignments["lead-project"] = AgentProjectAssignment(
        id="lead-project",
        team_id="team",
        project_id="project",
        agent_id="lead",
    )
    state.release_packages[release.id] = release
    state.review_requests[review.id] = review

    result = _result()
    _cascade_review_decision(
        state,
        review=review,
        decision_state=TeamRecordState.REJECTED,
        findings=["PLC device mappings persist opcTagId 0."],
        projects_to_recalc=set(),
        result=result,
    )

    assert release.state == TeamRecordState.REJECTED
    assert release.review_correction_task_id is not None
    task = state.tasks[release.review_correction_task_id]
    assert task.owner_agent_id == "lead"
    assert task.created_by_actor == "system:release-review-correction"
    assert task.git_base_branch == release.release_branch
    assert task.integration_boundary is False
    assert task.deliverable_contract is not None
    assert task.deliverable_contract.require_git_provenance is True
    assert task.mutation_policy is not None
    assert task.mutation_policy.policy_id == "system-remediation-topology-v1"
    assert task.mutation_policy.allow_child_tasks is False
    assert task.mutation_policy.allow_agent_cancellation is False
    assert "Release reviews: release-review" in task.description
    assert "PLC device mappings persist opcTagId 0." in task.description
    assert "Do not amend, reset, rebase, or otherwise rewrite" in task.description
    assert "must have the candidate commit as an ancestor" in task.description
    assert "rewriting pinned commit metadata or history" in task.description
    assert "promotion freezes that metadata into the next candidate" in task.description
    assert "Team automatically creates the successor release" in task.description
    assert "create a replacement release" not in task.description
    assert result["created_task_ids"] == [task.id]
    assert result["release_review_correction_task_id"] == task.id
    assert result["release_review_correction_task_owner_agent_id"] == "lead"
    assert result["release_review_correction_assignment_reason"] == "release_owner"
    inbox = state.inbox[result["created_inbox_item_ids"][0]]
    assert inbox.recipient_agent_id == "lead"
    assert inbox.task_id == task.id
    assert inbox.priority.value == "high"
    assert inbox.payload["kind"] == "release_review_rejection_correction_work"
    assert inbox.payload["review_request_id"] == review.id
    assert "Do not amend, reset, rebase, or otherwise rewrite" in inbox.payload["next_action"]
    assert "must have candidate_commit_sha as an ancestor" in inbox.payload["next_action"]
    assert "promotion freezes it into the next candidate" in inbox.payload["next_action"]
    assert "Team automatically creates the successor release" in inbox.payload["next_action"]
    assert "create a replacement release" not in inbox.payload["next_action"]
    wake = next(
        event for event in result["events"] if event["event_type"] == "team.agent.wake_requested"
    )
    assert wake["payload"]["agent_id"] == "lead"
    assert wake["payload"]["reason"] == "inbox"

    task.git_base_branch = release.git_base_branch
    manifest_release_branch = release.manifest.release_branch
    release.release_branch = None
    repair_result = _result()
    assert review.decision_at is not None
    materialize_release_review_rejection_correction(
        state,
        release=release,
        review=review,
        findings=list(review.findings),
        result=repair_result,
        now=review.decision_at,
    )
    assert task.git_base_branch == manifest_release_branch
    assert repair_result["updated_task_ids"] == [task.id]
    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_task_can_finish(state, task.id)
    assert raised.value.code == "deliverable_contract_direct_finish_forbidden"


def test_multiple_rejected_release_reviews_aggregate_context_and_wake_again() -> None:
    release, _promotion = _release_and_promotion()
    release.owner_agent_id = "lead"
    release.candidate_tree_hash = "d" * 40
    assert release.manifest is not None
    release.manifest.candidate_tree_hash = release.candidate_tree_hash
    reviews = [
        ReviewRequest(
            id=f"release-review-{suffix}",
            release_package_id=release.id,
            candidate_commit_sha="candidate-sha",
            reviewed_commit_sha="candidate-sha",
            reviewer_agent_id=f"reviewer-{suffix}",
        )
        for suffix in ("a", "b")
    ]
    release.review_request_ids = [review.id for review in reviews]
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="lead",
        project_ids=["project"],
    )
    state.roles["lead-role"] = TeamRole(
        id="lead-role",
        team_id="team",
        project_id="project",
        name="Lead",
    )
    state.projects["project"] = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
    )
    state.project_assignments["lead-project"] = AgentProjectAssignment(
        id="lead-project",
        team_id="team",
        project_id="project",
        agent_id="lead",
    )
    for review in reviews:
        state.agents[review.reviewer_agent_id] = PersistentAgent(
            id=review.reviewer_agent_id,
            team_id="team",
            role_id="lead-role",
            session_id=f"session-{review.reviewer_agent_id}",
        )
        state.review_requests[review.id] = review
    state.release_packages[release.id] = release

    first_result = _result()
    _cascade_review_decision(
        state,
        review=reviews[0],
        decision_state=TeamRecordState.REJECTED,
        findings=["First release-blocking finding."],
        projects_to_recalc=set(),
        result=first_result,
    )
    second_result = _result()
    _cascade_review_decision(
        state,
        review=reviews[1],
        decision_state=TeamRecordState.REJECTED,
        findings=["Second release-blocking finding."],
        projects_to_recalc=set(),
        result=second_result,
    )

    task_id = release.review_correction_task_id
    assert task_id is not None
    task = state.tasks[task_id]
    assert "release-review-a" in task.description
    assert "release-review-b" in task.description
    assert "First release-blocking finding." in task.description
    assert "Second release-blocking finding." in task.description
    assert (
        len(
            [
                candidate
                for candidate in state.tasks.values()
                if candidate.created_by_actor == "system:release-review-correction"
            ]
        )
        == 1
    )
    assert len(second_result["created_inbox_item_ids"]) == 1
    second_inbox = state.inbox[second_result["created_inbox_item_ids"][0]]
    assert second_inbox.payload["review_request_ids"] == [
        "release-review-a",
        "release-review-b",
    ]
    assert second_inbox.payload["findings"] == [
        "First release-blocking finding.",
        "Second release-blocking finding.",
    ]
    second_wake = next(
        event
        for event in second_result["events"]
        if event["event_type"] == "team.agent.wake_requested"
    )
    assert second_wake["payload"]["agent_id"] == "lead"


def test_configuration_gate_rejection_routes_high_priority_team_blocker() -> None:
    release, promotion = _release_and_promotion()
    release.owner_agent_id = "lead"
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="lead",
        project_ids=["project"],
    )
    state.roles["lead-role"] = TeamRole(
        id="lead-role",
        team_id="team",
        project_id="project",
        name="Lead",
    )
    project = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    project.release_policy = None
    state.projects[project.id] = project
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
    )
    state.project_assignments["lead-project"] = AgentProjectAssignment(
        id="lead-project",
        team_id="team",
        project_id="project",
        agent_id="lead",
    )
    state.release_packages[release.id] = release
    state.git_promotions[promotion.id] = promotion
    result = _result()

    begin_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        candidate_commit_sha="candidate-sha",
        candidate_tree_hash="candidate-tree",
        result=result,
    )

    assert release.state == TeamRecordState.REJECTED
    blocker = state.blockers[result["release_remediation_blocker_id"]]
    assert blocker.owner_agent_id == "lead"
    assert blocker.priority.value == "high"
    assert blocker.reason_code == "release_gate_configuration_configuration"
    assert "escalate it to an operator" in blocker.details
    inbox = state.inbox[result["created_inbox_item_ids"][0]]
    assert inbox.recipient_agent_id == "lead"
    assert inbox.payload["blocker_id"] == blocker.id
    wake = next(
        event for event in result["events"] if event["event_type"] == "team.agent.wake_requested"
    )
    assert wake["payload"]["agent_id"] == "lead"


def test_new_verification_run_marks_verifier_settings_as_snapshotted() -> None:
    command = ReleaseVerificationCommand(name="tests", argv=["pytest", "-q"])
    release, promotion = _release_and_promotion(verification_commands=[command])
    state = TeamState.empty()
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(
            container_image="verifier:v1",
            resource_limits={"memory": "6g", "cpus": 4, "pids_limit": 1536},
            verification_commands=[command],
        )
    )
    state.release_packages[release.id] = release
    state.git_promotions[promotion.id] = promotion
    result = _result()

    begin_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        candidate_commit_sha="candidate-sha",
        candidate_tree_hash="candidate-tree",
        result=result,
    )

    run = state.release_verification_runs[release.verification_run_ids[0]]
    assert run.verifier_settings_snapshotted is True
    assert run.container_image == "verifier:v1"
    assert run.resource_limits is not None
    assert run.resource_limits.memory == "6g"
    assert run.resource_limits.cpus == 4.0
    assert run.resource_limits.pids_limit == 1536


@pytest.mark.parametrize(
    ("verification_state", "failure_kind"),
    [
        ("failed", "infrastructure"),
        ("timed_out", None),
    ],
)
def test_non_candidate_verification_failure_persists_team_wake(
    tmp_path,
    verification_state: str,
    failure_kind: str | None,
) -> None:
    command = ReleaseVerificationCommand(name="tests", argv=["pytest", "-q"])
    release, promotion = _release_and_promotion(verification_commands=[command])
    release.owner_agent_id = "lead"
    run = ReleaseVerificationRun(
        id="verification-run",
        release_package_id=release.id,
        candidate_commit_sha="candidate-sha",
        session_id="verification-session",
        command_results=[ReleaseVerificationCommandResult(name=command.name)],
    )
    release.verification_run_ids = [run.id]
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="lead",
        project_ids=["project"],
    )
    state.roles["lead-role"] = TeamRole(
        id="lead-role",
        team_id="team",
        project_id="project",
        name="Lead",
    )
    state.projects["project"] = _project(
        release_policy=ProjectReleasePolicy(verification_commands=[command])
    )
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="session-lead",
    )
    state.project_assignments["lead-project"] = AgentProjectAssignment(
        id="lead-project",
        team_id="team",
        project_id="project",
        agent_id="lead",
    )
    state.release_packages[release.id] = release
    state.release_verification_runs[run.id] = run
    state.git_promotions[promotion.id] = promotion
    repository = TeamStateRepository(tmp_path)
    repository.update(lambda _current: state, expected_revision=0)

    updated, result = TeamReleaseGateService(repository).record_verification_result(
        release_package_id=release.id,
        verification_run_id=run.id,
        candidate_commit_sha="candidate-sha",
        verification_state=verification_state,
        failure_kind=failure_kind,
        command_results=[
            {
                "name": command.name,
                "state": verification_state,
                "log_excerpt": "verification worker disconnected",
            }
        ],
        error="verification worker disconnected",
    )
    wake = next(
        event for event in result["events"] if event["event_type"] == "team.agent.wake_requested"
    )
    wake_outbox = repository.list_outbox_records(intent_type="team.agent.wake_requested")

    assert updated.release_packages[release.id].state == TeamRecordState.BLOCKED
    assert wake["payload"]["agent_id"] == "lead"
    assert any(record.idempotency_key == wake["idempotency_key"] for record in wake_outbox)


def test_resolved_verification_correction_can_create_successor_release() -> None:
    release, _promotion = _release_and_promotion()
    release.state = TeamRecordState.REJECTED
    release.candidate_commit_sha = "c" * 40
    release.candidate_tree_hash = "d" * 40
    assert release.manifest is not None
    release.manifest.candidate_commit_sha = release.candidate_commit_sha
    release.manifest.candidate_tree_hash = release.candidate_tree_hash
    release.gate_failure = ReleaseGateFailure(
        stage="verification",
        kind="candidate",
        source_id="verification-run",
        message="release tests failed",
    )
    correction_task = AgentTask(
        id="correction-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Correct release verification",
        description="Fix the failed candidate.",
        created_by_actor="system:release-verification-correction",
        deliverable_ids=["correction-deliverable"],
    )
    correction_deliverable = Deliverable(
        id="correction-deliverable",
        task_id=correction_task.id,
        producer_agent_id="coder",
        summary="Corrected release verification",
        state=TeamRecordState.ACCEPTED,
    )
    verification_run = ReleaseVerificationRun(
        id="verification-run",
        release_package_id=release.id,
        candidate_commit_sha=release.candidate_commit_sha,
        session_id="verification-session",
        state="failed",
        failure_kind="candidate",
        correction_task_id=correction_task.id,
    )
    state = TeamState.empty()
    state.projects["project"] = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    state.release_packages[release.id] = release
    state.release_verification_runs[verification_run.id] = verification_run
    state.tasks[correction_task.id] = correction_task
    state.deliverables[correction_deliverable.id] = correction_deliverable

    error = _successor_release_request_error(
        state,
        project_id="project",
        predecessor=release,
        correction_deliverable_ids=[correction_deliverable.id],
    )

    assert error is None


def test_resolved_release_review_correction_can_create_successor_release() -> None:
    release, _promotion = _release_and_promotion()
    release.state = TeamRecordState.REJECTED
    release.candidate_commit_sha = "c" * 40
    release.candidate_tree_hash = "d" * 40
    assert release.manifest is not None
    release.manifest.candidate_commit_sha = release.candidate_commit_sha
    release.manifest.candidate_tree_hash = release.candidate_tree_hash
    release.gate_failure = ReleaseGateFailure(
        stage="review",
        kind="rejected",
        source_id="release-review",
        message="PLC device mappings persist opcTagId 0.",
    )
    correction_task = AgentTask(
        id="review-correction-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Correct rejected release review",
        description="Fix the review finding.",
        created_by_actor="system:release-review-correction",
        deliverable_ids=["review-correction-deliverable"],
    )
    correction_deliverable = Deliverable(
        id="review-correction-deliverable",
        task_id=correction_task.id,
        producer_agent_id="coder",
        summary="Corrected release review finding",
        state=TeamRecordState.ACCEPTED,
    )
    review = ReviewRequest(
        id="release-review",
        release_package_id=release.id,
        candidate_commit_sha=release.candidate_commit_sha,
        reviewed_commit_sha=release.candidate_commit_sha,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.REJECTED,
        findings=["PLC device mappings persist opcTagId 0."],
    )
    release.review_correction_task_id = correction_task.id
    state = TeamState.empty()
    state.projects["project"] = _project(release_policy=ProjectReleasePolicy(allow_ungated=True))
    state.release_packages[release.id] = release
    state.review_requests[review.id] = review
    state.tasks[correction_task.id] = correction_task
    state.deliverables[correction_deliverable.id] = correction_deliverable

    error = _successor_release_request_error(
        state,
        project_id="project",
        predecessor=release,
        correction_deliverable_ids=[correction_deliverable.id],
    )

    assert error is None

    correction_commit = "e" * 40
    correction_deliverable.git_refs = DeliverableGitRefs(
        agent_branch="agents/coder/review-correction",
        commit_sha=correction_commit,
        submitted_commit_sha=correction_commit,
    )
    correction_deliverable.review_manifest = DeliverableReviewManifest(
        base_commit_sha=release.candidate_commit_sha,
        reviewed_commit_sha=correction_commit,
        tree_hash="f" * 40,
        commit_shas=[correction_commit],
        changed_paths=["M\tsrc/PlcDeviceMapping.cs"],
    )
    correction_deliverable.verification = DeliverableVerification(status="passed")
    correction_review = ReviewRequest(
        id="review-correction-review",
        deliverable_id=correction_deliverable.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=correction_commit,
    )
    correction_deliverable.review_request_ids = [correction_review.id]
    state.review_requests[correction_review.id] = correction_review
    result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }

    successor_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "review-correction-successor",
            "deliverable_ids": [correction_deliverable.id],
            "supersedes_release_package_id": release.id,
        },
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="coder",
        team_id="team",
    )

    assert successor_id == "review-correction-successor"
    assert result["errors"] == []
    successor = state.release_packages[successor_id]
    assert len(successor.git_promotion_record_ids) == 1
    aggregate = state.git_promotions[successor.git_promotion_record_ids[0]]
    assert aggregate.promotion_kind == "correction_aggregate"
    assert aggregate.outputs["required_source_ancestor_commit_sha"] == "c" * 40
    assert result["events"][0]["payload"]["base_commit_sha"] == "c" * 40

    successor.state = TeamRecordState.REJECTED
    successor.candidate_commit_sha = "1" * 40
    successor.candidate_tree_hash = "2" * 40
    successor.gate_failure = ReleaseGateFailure(
        stage="review",
        kind="rejected",
        source_id="second-release-review",
        message="A later review found another issue.",
    )
    second_commit = "3" * 40
    second_task = AgentTask(
        id="second-review-correction-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Correct the successor review",
        description="Fix the later review finding.",
        created_by_agent_id="lead",
        release_base_package_id=successor.id,
        git_base_branch=successor.release_branch,
        git_base_commit_sha=successor.candidate_commit_sha,
        deliverable_ids=["second-review-correction-deliverable"],
    )
    second_deliverable = Deliverable(
        id="second-review-correction-deliverable",
        task_id=second_task.id,
        producer_agent_id="coder",
        summary="Corrected the successor review finding",
        state=TeamRecordState.ACCEPTED,
        git_refs=DeliverableGitRefs(
            agent_branch="agents/coder/second-review-correction",
            commit_sha=second_commit,
            submitted_commit_sha=second_commit,
        ),
        review_manifest=DeliverableReviewManifest(
            base_commit_sha=successor.candidate_commit_sha,
            reviewed_commit_sha=second_commit,
            tree_hash="4" * 40,
            commit_shas=[second_commit],
            changed_paths=["M\tsrc/SecondCorrection.cs"],
        ),
        verification=DeliverableVerification(status="passed"),
        review_request_ids=["second-correction-review"],
    )
    state.tasks[second_task.id] = second_task
    state.deliverables[second_deliverable.id] = second_deliverable
    state.review_requests["second-correction-review"] = ReviewRequest(
        id="second-correction-review",
        deliverable_id=second_deliverable.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=second_commit,
    )
    second_result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }

    second_successor_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "second-review-correction-successor",
            "deliverable_ids": [second_deliverable.id],
            "supersedes_release_package_id": successor.id,
        },
        result=second_result,
        projects_to_recalc=set(),
        actor_agent_id="coder",
        team_id="team",
    )

    assert second_successor_id == "second-review-correction-successor"
    assert second_result["errors"] == []
    assert second_result["correction_deliverable_ids"] == [second_deliverable.id]
    second_successor = state.release_packages[second_successor_id]
    assert second_successor.deliverable_ids == [
        *successor.deliverable_ids,
        second_deliverable.id,
    ]


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
    assert replacement.verifier_settings_snapshotted is True
    assert release.state == TeamRecordState.PENDING


def _candidate_verification_retry_fixture():
    state, release, promotion, _reviews = _release_review_cohort_fixture()
    state.review_requests.clear()
    state.inbox.clear()
    release.review_request_ids = []
    release.review_cohort_id = None
    release.review_cohort_sealed_at = None
    command = ReleaseVerificationCommand(name="tests", argv=["pytest", "-q"])
    release.manifest.verification_commands = [command]
    state.projects["project"].release_policy.verification_commands = [command]
    run = ReleaseVerificationRun(
        id="failed-verification",
        release_package_id=release.id,
        candidate_commit_sha=release.candidate_commit_sha,
        session_id="verifier",
    )
    state.release_verification_runs[run.id] = run
    release.verification_run_ids = [run.id]
    record_release_verification_result(
        state,
        release_package_id=release.id,
        verification_run_id=run.id,
        candidate_commit_sha=release.candidate_commit_sha,
        verification_state="failed",
        failure_kind="candidate",
        error="Missing toolchain manifest",
        command_results=[
            ReleaseVerificationCommandResult(
                name="tests",
                state="failed",
                failure_kind="candidate",
                exit_code=1,
                log_excerpt="NETSDK1042: Could not load PlatformManifest",
            )
        ],
        result=_result(),
    )
    assert run.correction_task_id is not None
    return state, release, run


def test_operator_verification_retry_preserves_evidence_and_persists_recovery(tmp_path: Path):
    state, release, run = _candidate_verification_retry_fixture()
    original_run = run.model_dump(mode="json")
    original_manifest = release.manifest.model_dump(mode="json")
    task_id = run.correction_task_id
    repository = TeamStateRepository(tmp_path)
    saved = repository.update(lambda _state: state)
    admin = TeamAdminService(repository)
    arguments = dict(
        release_package_id=release.id,
        gate_kind="verification",
        gate_id=run.id,
        reason="Retry after the toolchain was repaired.",
        operator_actor_id="operator:jonathan",
        allow_rejected_verification_retry=True,
    )
    with pytest.raises(TeamStateConflictError):
        admin.retry_release_gate_with_result(**arguments, expected_revision=saved.revision - 1)
    assert repository.read().release_packages[release.id].state == TeamRecordState.REJECTED
    _after, result = admin.retry_release_gate_with_result(
        **arguments, expected_revision=saved.revision
    )
    after = repository.read()
    replacement_id = result["replacement_gate_id"]
    assert after.release_verification_runs[run.id].model_dump(mode="json") == {
        **original_run,
        "superseded_by_verification_run_id": replacement_id,
    }
    replacement = after.release_verification_runs[replacement_id]
    assert replacement.candidate_commit_sha == run.candidate_commit_sha
    assert replacement.state == "pending"
    assert replacement.attempt_number == 2
    assert replacement.retry_reason == arguments["reason"]
    assert replacement.retry_requested_by == "operator:jonathan"
    assert replacement.supersedes_verification_run_id == run.id
    assert [command.name for command in replacement.command_results] == ["tests"]
    recovered_release = after.release_packages[release.id]
    assert recovered_release.state == TeamRecordState.PENDING
    assert recovered_release.manifest.model_dump(mode="json") == original_manifest
    assert recovered_release.approved_commit_sha is None
    assert after.tasks[task_id].state == TeamTaskState.CANCELLED
    assert after.tasks[task_id].cancellation.reason_code == "operator_verification_retry"
    assert arguments["reason"] in after.tasks[task_id].cancellation.reason
    assert all(item.state == TeamInboxItemState.HANDLED for item in after.inbox.values())
    events = repository.list_outbox_records()
    assert any(event.intent_type == "team.release.verification_requested" for event in events)
    with pytest.raises(ValueError):
        admin.retry_release_gate_with_result(**arguments, expected_revision=after.revision)
    assert len(repository.read().release_verification_runs) == 2


@pytest.mark.parametrize(
    "obstacle",
    [
        "agent",
        "missing_comment",
        "successor",
        "stale_gate",
        "submitted_work",
        "active_dispatch",
        "dependent_task",
        "wrong_candidate",
        "wrong_task",
        "not_authorized",
    ],
)
def test_operator_verification_retry_refuses_unsafe_recovery_without_mutation(obstacle):
    state, release, run = _candidate_verification_retry_fixture()
    task_id = run.correction_task_id
    kwargs = dict(
        gate_kind="verification",
        gate_id=run.id,
        reason="Retry after repair.",
        requested_by="operator:jonathan",
        allow_rejected_verification_retry=True,
    )
    if obstacle == "agent":
        kwargs["requested_by"] = "agent:lead"
    elif obstacle == "not_authorized":
        kwargs["allow_rejected_verification_retry"] = False
    elif obstacle == "missing_comment":
        kwargs["reason"] = "   "
    elif obstacle == "successor":
        state.release_packages["successor"] = ReleasePackage(
            id="successor",
            project_id="project",
            supersedes_release_package_id=release.id,
        )
    elif obstacle == "stale_gate":
        kwargs["gate_id"] = "old-run"
    elif obstacle == "submitted_work":
        state.deliverables["correction"] = Deliverable(
            id="correction",
            task_id=task_id,
            producer_agent_id="lead",
            summary="Correction work",
        )
    elif obstacle == "active_dispatch":
        state.dispatches["working"] = TeamDispatch(
            id="working",
            agent_id="lead",
            session_id="session-lead",
            cause_kind="task",
            task_ids=[task_id],
            status="preparing",
        )
    elif obstacle == "dependent_task":
        state.tasks["dependent"] = AgentTask(
            id="dependent",
            owner_agent_id="lead",
            project_id="project",
            title="Dependent",
            description="Needs correction",
            priority=0,
            dependency_task_ids=[task_id],
            created_by_agent_id="lead",
        )
    elif obstacle == "wrong_candidate":
        run.candidate_commit_sha = "another-candidate"
    elif obstacle == "wrong_task":
        state.tasks[task_id].release_base_package_id = "another-release"
    before = state.model_dump(mode="json")
    with pytest.raises(ValueError):
        retry_release_gate(state, release=release, result={}, **kwargs)
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("outcome", ["passed", "failed"])
def test_operator_verification_retry_keeps_normal_review_and_correction_gates(outcome):
    state, release, run = _candidate_verification_retry_fixture()
    original_task_id = run.correction_task_id
    result = {}
    retry_release_gate(
        state,
        release=release,
        gate_kind="verification",
        gate_id=run.id,
        reason="Try verification again.",
        requested_by="operator:jonathan",
        result=result,
        allow_rejected_verification_retry=True,
    )
    replacement_id = result["replacement_gate_id"]
    record_release_verification_result(
        state,
        release_package_id=release.id,
        verification_run_id=replacement_id,
        candidate_commit_sha=release.candidate_commit_sha,
        verification_state=outcome,
        failure_kind="candidate" if outcome == "failed" else None,
        error="Still failing" if outcome == "failed" else None,
        command_results=[ReleaseVerificationCommandResult(name="tests", state=outcome)],
        result=_result(),
    )
    assert state.tasks[original_task_id].state == TeamTaskState.CANCELLED
    assert release.approved_commit_sha is None
    if outcome == "failed":
        assert release.state == TeamRecordState.REJECTED
        new_task_id = state.release_verification_runs[replacement_id].correction_task_id
        assert new_task_id and new_task_id != original_task_id
        assert state.tasks[new_task_id].state == TeamTaskState.PENDING
    else:
        assert release.state == TeamRecordState.PENDING
        assert len(release.review_request_ids) == 2
        assert all(
            review.state == TeamRecordState.PENDING for review in state.review_requests.values()
        )


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
