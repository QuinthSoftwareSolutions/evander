from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from evander_team_addon.models import (
    AgentStatus,
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentTask,
    Deliverable,
    DeliverableVerification,
    GitPromotionRecord,
    HumanReviewChecklistItem,
    HumanReviewFindingTargetScope,
    HumanReviewFindingSnapshot,
    HumanReviewRequirement,
    HumanReviewRequirementSnapshot,
    HumanReviewTestTarget,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGitIdentity,
    AdoWorkItemAttribution,
    ProjectReleasePolicy,
    ReleaseCandidateDeliverableEvidence,
    ReleaseCandidateManifest,
    ReleaseGateFailure,
    ReleaseGateKind,
    ReleaseGateObligationWaiver,
    ReleasePackage,
    ReleaseReviewRequirement,
    ReleaseVerificationCommand,
    ReleaseVerificationCommandResult,
    ReviewRequest,
    ReviewProfile,
    Team,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamCase,
    TeamInboxItemState,
    TeamOutboxStatus,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain import human_reviews as human_review_domain
from evander_team_addon.services.domain.case_task import (
    _resolve_new_task_git_base_branch,
)
from evander_team_addon.services.domain.human_reviews import (
    HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY,
    close_post_release_feedback_case,
    route_human_finding_correction,
    ensure_human_review_request,
    expire_human_review_targets,
    reassign_human_review,
    reconcile_human_review_assignment,
    record_human_review_decision,
    record_human_review_preparation_result,
    record_human_finding_resolution_proposal,
    report_post_release_feedback,
    resume_human_review,
)
from evander_team_addon.services.domain.release_gates import (
    _candidate_manifest,
    advance_release_gates,
    begin_release_gates,
    record_release_verification_result,
)
from evander_team_addon.services.domain.release_packages import (
    _build_release_package,
    _reviewed_commit_for_release,
)
from evander_team_addon.services.domain.review import _cascade_review_decision
from evander_team_addon.services.git_release_service import (
    TeamGitReleaseService,
    _human_review_publication_validation_error,
)
from evander_team_addon.services.release_gate_service import TeamReleaseGateService


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"


def test_human_review_target_normalizes_naive_expiry_to_utc() -> None:
    target = HumanReviewTestTarget(
        alias="staging",
        expires_at="2026-07-26T10:00:00",
    )

    assert target.expires_at == datetime(2026, 7, 26, 10, 0, tzinfo=UTC)


def _human_state(
    *,
    eligible_user_ids: list[str] | None = None,
    preparation_mode: str = "manual",
) -> tuple[TeamState, ReleasePackage, GitPromotionRecord]:
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
        name="Project lead",
    )
    state.roles["owner-role"] = TeamRole(
        id="owner-role",
        team_id="team",
        project_id="project",
        name="Owner",
    )
    state.agents["lead"] = PersistentAgent(
        id="lead",
        team_id="team",
        role_id="lead-role",
        session_id="lead-session",
    )
    state.agents["owner"] = PersistentAgent(
        id="owner",
        team_id="team",
        role_id="owner-role",
        session_id="owner-session",
        reports_to_agent_id="lead",
    )
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        git=ProjectGitConfig(
            managed_repo_id=MANAGED_REPO_ID,
            base_branch="main",
        ),
        release_policy=ProjectReleasePolicy(allow_ungated=True),
    )
    state.project_assignments["lead-project"] = AgentProjectAssignment(
        id="lead-project",
        team_id="team",
        project_id="project",
        agent_id="lead",
        role_id="lead-role",
        kind=AgentProjectAssignmentKind.PROJECT_LEAD,
    )
    state.project_assignments["owner-project"] = AgentProjectAssignment(
        id="owner-project",
        team_id="team",
        project_id="project",
        agent_id="owner",
        role_id="owner-role",
    )
    state.tasks["source-task"] = AgentTask(
        id="source-task",
        owner_agent_id="owner",
        project_id="project",
        priority=1,
        state=TeamTaskState.FINISHED,
        title="Source work",
        description="Accepted historical work.",
        created_by_agent_id="lead",
        deliverable_ids=["source-deliverable"],
    )
    state.deliverables["source-deliverable"] = Deliverable(
        id="source-deliverable",
        task_id="source-task",
        producer_agent_id="owner",
        summary="Source contribution",
        state=TeamRecordState.ACCEPTED,
    )
    requirement = HumanReviewRequirementSnapshot(
        directory_path="root",
        configured_group_ids=["release-reviewers"],
        initially_eligible_user_ids=eligible_user_ids or [],
        checklist=[
            HumanReviewChecklistItem(
                id="smoke",
                label="Smoke test passed",
            )
        ],
        test_instructions="Run the smoke journey.",
        preparation_mode=preparation_mode,
    )
    manifest = ReleaseCandidateManifest(
        project_id="project",
        release_package_id="release",
        base_commit_sha="0" * 40,
        candidate_commit_sha="a" * 40,
        candidate_tree_hash="b" * 40,
        release_branch="team/team/releases/release",
        deliverables=[
            ReleaseCandidateDeliverableEvidence(
                deliverable_id="source-deliverable",
            )
        ],
        human_review_requirement=requirement,
    )
    release = ReleasePackage(
        id="release",
        project_id="project",
        deliverable_ids=["source-deliverable"],
        release_branch=manifest.release_branch,
        canonical_branch="main",
        base_commit_sha="0" * 40,
        canonical_base_commit_sha="0" * 40,
        candidate_commit_sha=manifest.candidate_commit_sha,
        candidate_tree_hash=manifest.candidate_tree_hash,
        manifest=manifest,
        git_promotion_record_ids=["release-promotion"],
    )
    promotion = GitPromotionRecord(
        id="release-promotion",
        project_id="project",
        release_package_id=release.id,
        actor_agent_id="lead",
        source_branch=manifest.release_branch,
        target_branch=manifest.release_branch,
        promotion_kind="release",
        state="succeeded",
        commit_sha=manifest.candidate_commit_sha,
    )
    state.release_packages[release.id] = release
    state.git_promotions[promotion.id] = promotion
    return state, release, promotion


def _mark_deliverable_as_reviewed_git_correction(
    state: TeamState,
    *,
    deliverable: Deliverable,
    commit_sha: str,
) -> None:
    review_id = f"{deliverable.id}-review"
    deliverable.git_refs.feature_branch = (
        f"team/team/features/{deliverable.task_id}-{deliverable.producer_agent_id}"
    )
    deliverable.git_refs.feature_commit_sha = commit_sha
    deliverable.review_request_ids.append(review_id)
    state.review_requests[review_id] = ReviewRequest(
        id=review_id,
        deliverable_id=deliverable.id,
        reviewer_agent_id="lead",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=commit_sha,
    )


def _successor_manifest(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    release_id: str,
) -> ReleaseCandidateManifest:
    candidate_commit_sha = "c" * 40
    successor = ReleasePackage(
        id=release_id,
        project_id=predecessor.project_id,
        supersedes_release_package_id=predecessor.id,
        deliverable_ids=list(predecessor.deliverable_ids),
        base_commit_sha=predecessor.candidate_commit_sha,
        release_branch=f"team/team/releases/{release_id}",
        candidate_commit_sha=candidate_commit_sha,
        candidate_tree_hash="d" * 40,
    )
    promotion = GitPromotionRecord(
        id=f"{release_id}-promotion",
        project_id=predecessor.project_id,
        release_package_id=successor.id,
        actor_agent_id="lead",
        source_branch=f"team/team/features/{release_id}",
        target_branch=successor.release_branch,
        promotion_kind="release",
        state="succeeded",
        commit_sha=candidate_commit_sha,
    )
    state.release_packages[successor.id] = successor
    state.git_promotions[promotion.id] = promotion
    return _candidate_manifest(
        state,
        release=successor,
        release_promotion=promotion,
    )


def _persist_pending_human_review(
    tmp_path: Path,
    *,
    eligible_user_ids: list[str],
    preparation_mode: str = "manual",
) -> tuple[TeamStateRepository, str]:
    state, release, promotion = _human_state(
        eligible_user_ids=eligible_user_ids,
        preparation_mode=preparation_mode,
    )
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    repository = TeamStateRepository(tmp_path / "runtime")
    repository.update(lambda _current: state)
    return repository, request.id


def test_retired_reviews_are_not_release_candidate_evidence() -> None:
    state, release, _promotion = _human_state(eligible_user_ids=["reviewer"])
    review = ReviewRequest(
        id="retired-review",
        deliverable_id="source-deliverable",
        reviewer_agent_id="lead",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha="reviewed-commit",
        retired_at=datetime(2026, 7, 26, tzinfo=UTC),
        retired_by="agent:lead",
        retirement_reason="The review was attached in error.",
    )
    state.review_requests[review.id] = review
    deliverable = state.deliverables["source-deliverable"]
    deliverable.review_request_ids = [review.id]

    reviewed_commit, error = _reviewed_commit_for_release(state, deliverable)
    successor_manifest = _successor_manifest(
        state,
        predecessor=release,
        release_id="release-after-retirement",
    )

    assert reviewed_commit == ""
    assert error == (
        "git-backed deliverable source-deliverable has no accepted review with an "
        "immutable reviewed commit"
    )
    assert successor_manifest.deliverables[0].accepted_reviews == []


def test_human_gate_creates_pinned_request_before_approval() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    result: dict[str, object] = {"events": []}

    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result=result,
    )

    request = next(iter(state.human_review_requests.values()))
    assert request.state == "pending"
    assert request.candidate_commit_sha == release.candidate_commit_sha
    assert request.candidate_tree_hash == release.candidate_tree_hash
    assert release.approved_commit_sha is None
    assert not any(
        record.promotion_kind == "canonical_push" for record in state.git_promotions.values()
    )
    events = result["events"]
    assert isinstance(events, list)
    assert events[0]["payload"]["recipient_user_id"] == "reviewer"


def test_expired_preview_blocks_the_same_pinned_request() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    request.test_target = HumanReviewTestTarget(
        alias="preview",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    original_candidate = (request.candidate_commit_sha, request.candidate_tree_hash)
    result: dict[str, object] = {"events": []}

    expired_ids = expire_human_review_targets(state, result=result)

    assert expired_ids == [request.id]
    assert request.state == "blocked"
    assert request.test_target is None
    assert (request.candidate_commit_sha, request.candidate_tree_hash) == original_candidate
    assert release.gate_failure is not None
    assert release.gate_failure.source_id == request.id
    assert {
        blocker.reason_code
        for blocker in state.blockers.values()
        if blocker.source_id == request.id
    } == {"human_review_target_expired"}

    resumed = resume_human_review(
        state,
        human_review_request_id=request.id,
        result={"events": []},
    )
    assert resumed.id == request.id
    assert resumed.state == "pending"
    assert resumed.test_target is None
    assert (resumed.candidate_commit_sha, resumed.candidate_tree_hash) == (original_candidate)
    assert len(state.human_review_requests) == 1
    assert release.state == TeamRecordState.PENDING


@pytest.mark.parametrize(
    ("request_id", "candidate_sha", "candidate_tree", "generation", "message"),
    [
        (
            "missing-request",
            "a" * 40,
            "b" * 40,
            1,
            "does not exist",
        ),
        (
            None,
            "c" * 40,
            "b" * 40,
            1,
            "pinned request generation",
        ),
        (
            None,
            "a" * 40,
            "d" * 40,
            1,
            "pinned request generation",
        ),
        (
            None,
            "a" * 40,
            "b" * 40,
            2,
            "pinned request generation",
        ),
    ],
)
def test_preview_result_rejects_request_candidate_tree_and_generation_mismatch(
    request_id: str | None,
    candidate_sha: str,
    candidate_tree: str,
    generation: int,
    message: str,
) -> None:
    state, release, promotion = _human_state(
        eligible_user_ids=["reviewer"],
        preparation_mode="workflow",
    )
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    before = state.model_dump(mode="json")

    with pytest.raises(ValueError, match=message):
        record_human_review_preparation_result(
            state,
            human_review_request_id=request_id or request.id,
            candidate_commit_sha=candidate_sha,
            candidate_tree_hash=candidate_tree,
            preparation_generation=generation,
            target={"url": "https://preview.example"},
            error=None,
            result={"events": []},
        )

    assert state.model_dump(mode="json") == before


def test_successful_preparation_result_replay_is_transactionally_idempotent(
    tmp_path: Path,
) -> None:
    repository, request_id = _persist_pending_human_review(
        tmp_path,
        eligible_user_ids=["reviewer"],
        preparation_mode="workflow",
    )
    service = TeamAdminService(repository)
    command = {
        "human_review_request_id": request_id,
        "recorded_by_user_id": "admin-owner",
        "request_id": "successful-preparation",
        "expected_request_revision": 0,
        "candidate_commit_sha": "a" * 40,
        "candidate_tree_hash": "b" * 40,
        "preparation_generation": 1,
        "target": {
            "url": "https://preview.example",
            "alias": "candidate-preview",
        },
        "error": None,
    }

    first_state, first_result = service.record_human_review_preparation_result_with_result(
        **command
    )
    first_request = first_state.human_review_requests[request_id]
    first_revision = first_state.revision
    first_outbox = repository.list_outbox_records(intent_type="team.release.human_review_requested")
    assert first_result["idempotent_replay"] is False
    assert first_request.state == "pending"
    assert first_request.test_target is not None
    assert first_request.test_target.alias == "candidate-preview"
    assert len(first_outbox) == 1

    replay_state, replay_result = service.record_human_review_preparation_result_with_result(
        **command
    )
    assert replay_result["idempotent_replay"] is True
    assert replay_state.revision == first_revision
    assert replay_state.human_review_requests[request_id] == first_request
    assert {
        record.id
        for record in repository.list_outbox_records(
            intent_type="team.release.human_review_requested"
        )
    } == {first_outbox[0].id}


def test_manual_blocked_review_resumes_same_request_and_candidate() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    pinned_candidate = (
        request.candidate_commit_sha,
        request.candidate_tree_hash,
    )
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="blocked",
        request_id="manual-testing-blocked",
        expected_revision=request.revision,
        checklist_results={"smoke": False},
        summary="The manual test account is unavailable.",
        result={"events": []},
    )
    assert request.state == "blocked"

    resumed = resume_human_review(
        state,
        human_review_request_id=request.id,
        result={"events": []},
    )
    assert resumed.id == request.id
    assert resumed.state == "pending"
    assert (resumed.candidate_commit_sha, resumed.candidate_tree_hash) == (pinned_candidate)
    assert len(state.human_review_requests) == 1
    assert release.state == TeamRecordState.PENDING


def test_human_approval_requires_completed_checklist() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))

    with pytest.raises(
        ValueError,
        match="required human review checklist items are incomplete: smoke",
    ):
        record_human_review_decision(
            state,
            human_review_request_id=request.id,
            reviewer_user_id="reviewer",
            reviewer_display_name="Reviewer",
            decision="approved",
            request_id="incomplete-approval",
            expected_revision=request.revision,
            checklist_results={"smoke": False},
            result={"events": []},
        )

    assert state.human_review_decisions == {}
    assert request.state == "pending"


def test_changes_requested_allows_incomplete_checklist() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))

    decision = record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="incomplete-negative-decision",
        expected_revision=request.revision,
        checklist_results={"smoke": False},
        summary="The smoke test did not pass.",
        findings=[
            {
                "text": "The candidate fails the required smoke test.",
                "target_deliverable_ids": ["source-deliverable"],
            }
        ],
        result={"events": []},
    )

    assert decision.checklist_results == {"smoke": False}
    assert request.state == "changes_requested"


@pytest.mark.parametrize("record_kind", ["case", "task"])
def test_changes_requested_rejects_squatted_system_record_ids(
    tmp_path: Path,
    record_kind: str,
) -> None:
    repository, request_id = _persist_pending_human_review(
        tmp_path,
        eligible_user_ids=["reviewer"],
    )
    case_id = f"human-review-case-{human_review_domain._stable_token(request_id, length=24)}"
    task_id = (
        f"human-correction-aggregation-{human_review_domain._stable_token(request_id, length=24)}"
    )

    def _squat(state: TeamState) -> TeamState:
        if record_kind == "case":
            state.cases[case_id] = TeamCase(
                id=case_id,
                team_id="team",
                project_id="project",
                title="Unrelated operator case",
                owner_agent_id="owner",
                source="operator_case",
            )
        else:
            state.tasks[task_id] = AgentTask(
                id=task_id,
                owner_agent_id="owner",
                project_id="project",
                priority=1,
                title="Unrelated project task",
                description="This task must not receive human-review findings.",
                created_by_agent_id="lead",
            )
        return state

    repository.update(_squat)
    before = repository.read().model_dump(mode="json")

    with pytest.raises(
        ValueError,
        match=rf"reserved human-review {record_kind} id",
    ):
        TeamAdminService(repository).record_human_review_decision_with_result(
            human_review_request_id=request_id,
            reviewer_user_id="reviewer",
            reviewer_display_name="Reviewer",
            decision="changes_requested",
            request_id=f"squatted-{record_kind}-decision",
            expected_request_revision=0,
            checklist_results={"smoke": False},
            summary="The release-wide recovery path needs correction.",
            findings=[
                {
                    "text": "Correct the release-wide recovery path.",
                    "target_scope": "release_candidate",
                }
            ],
        )

    assert repository.read().model_dump(mode="json") == before
    assert repository.list_outbox_records() == []


def test_changes_requested_routes_recursive_contribution_finding_to_owner() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.tasks["child-task"] = AgentTask(
        id="child-task",
        owner_agent_id="owner",
        project_id="project",
        parent_task_id="source-task",
        authored_by=ProjectGitIdentity(name="Child Author", email="child@example.test"),
        priority=1,
        state=TeamTaskState.FINISHED,
        title="Child contribution",
        description="Accepted child work included by the aggregate.",
        created_by_agent_id="lead",
        deliverable_ids=["child-deliverable"],
    )
    state.deliverables["child-deliverable"] = Deliverable(
        id="child-deliverable",
        task_id="child-task",
        producer_agent_id="owner",
        summary="Recursive source contribution",
        state=TeamRecordState.ACCEPTED,
    )
    state.deliverables["source-deliverable"].source_deliverable_ids = ["child-deliverable"]
    assert release.manifest is not None
    release.manifest.contribution_graph = {
        "source-deliverable": ["child-deliverable"],
        "child-deliverable": [],
    }
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))

    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="recursive-contribution-finding",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "Correct the recursively included child contribution.",
                "target_deliverable_ids": ["child-deliverable"],
            }
        ],
        result={"events": []},
    )

    finding = next(iter(state.human_review_findings.values()))
    correction_task = state.tasks[finding.correction_task_id]
    assert finding.target_deliverable_ids == ["child-deliverable"]
    assert correction_task.originating_task_id == "child-task"
    assert correction_task.authored_by == state.tasks["child-task"].authored_by
    assert correction_task.owner_agent_id == "owner"


def test_release_candidate_finding_routes_to_lead_without_ambiguity() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))

    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="release-candidate-finding",
        expected_revision=request.revision,
        checklist_results={"smoke": False},
        findings=[
            {
                "text": "Correct the integrated release behavior.",
                "target_scope": "release_candidate",
                "target_deliverable_ids": [],
            }
        ],
        result={"events": []},
    )

    finding = next(iter(state.human_review_findings.values()))
    correction = state.tasks[finding.correction_task_id]
    case = state.cases[correction.case_id or ""]
    assert finding.target_scope == (HumanReviewFindingTargetScope.RELEASE_CANDIDATE)
    assert finding.target_deliverable_ids == []
    assert correction.owner_agent_id == "lead"
    assert correction.originating_task_id is None
    assert case.metadata["human_correction_ambiguous_finding_ids"] == []


def test_explicit_multi_deliverable_finding_routes_to_lead_without_ambiguity() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.tasks["lead-source-task"] = AgentTask(
        id="lead-source-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        state=TeamTaskState.FINISHED,
        title="Lead-owned source work",
        description="A second accepted candidate contribution.",
        created_by_agent_id="lead",
        deliverable_ids=["lead-source-deliverable"],
    )
    state.deliverables["lead-source-deliverable"] = Deliverable(
        id="lead-source-deliverable",
        task_id="lead-source-task",
        producer_agent_id="lead",
        summary="Lead-owned source contribution",
        state=TeamRecordState.ACCEPTED,
    )
    release.deliverable_ids.append("lead-source-deliverable")
    assert release.manifest is not None
    release.manifest.deliverables.append(
        ReleaseCandidateDeliverableEvidence(
            deliverable_id="lead-source-deliverable",
        )
    )
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))

    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="multi-deliverable-finding",
        expected_revision=request.revision,
        checklist_results={"smoke": False},
        findings=[
            {
                "text": "Correct the behavior spanning both contributions.",
                "target_scope": "deliverables",
                "target_deliverable_ids": [
                    "source-deliverable",
                    "lead-source-deliverable",
                ],
            }
        ],
        result={"events": []},
    )

    finding = next(iter(state.human_review_findings.values()))
    correction = state.tasks[finding.correction_task_id]
    case = state.cases[correction.case_id or ""]
    assert finding.target_scope == HumanReviewFindingTargetScope.DELIVERABLES
    assert finding.target_deliverable_ids == [
        "lead-source-deliverable",
        "source-deliverable",
    ]
    assert correction.owner_agent_id == "lead"
    assert correction.originating_task_id is None
    assert case.metadata["human_correction_ambiguous_finding_ids"] == []


@pytest.mark.parametrize(
    ("finding", "message"),
    [
        (
            {
                "text": "Conflicting release scope.",
                "target_scope": "release_candidate",
                "target_deliverable_ids": ["source-deliverable"],
            },
            "cannot select both the entire release candidate",
        ),
        (
            {
                "text": "Missing deliverable selection.",
                "target_scope": "deliverables",
                "target_deliverable_ids": [],
            },
            "deliverable scope requires at least one affected deliverable",
        ),
        (
            {
                "text": "Missing target scope.",
                "target_deliverable_ids": [],
            },
            "must select the entire release candidate",
        ),
        (
            {
                "text": "Legacy target scope on a new finding.",
                "target_scope": "legacy_unspecified",
                "target_deliverable_ids": [],
            },
            "must select the entire release candidate",
        ),
        (
            {
                "text": "Unknown target scope.",
                "target_scope": "everything",
                "target_deliverable_ids": [],
            },
            "target_scope must be release_candidate or deliverables",
        ),
    ],
)
def test_changes_requested_validates_explicit_finding_scope(
    finding: dict[str, object],
    message: str,
) -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))

    with pytest.raises(ValueError, match=message):
        record_human_review_decision(
            state,
            human_review_request_id=request.id,
            reviewer_user_id="reviewer",
            reviewer_display_name="Reviewer",
            decision="changes_requested",
            request_id="invalid-explicit-finding-scope",
            expected_revision=request.revision,
            checklist_results={"smoke": False},
            findings=[finding],
            result={"events": []},
        )

    assert request.state == "pending"
    assert state.human_review_findings == {}


def test_changes_requested_rejects_deliverables_outside_reviewed_candidate() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.tasks["foreign-task"] = AgentTask(
        id="foreign-task",
        owner_agent_id="owner",
        project_id="project",
        priority=2,
        state=TeamTaskState.FINISHED,
        title="Foreign work",
        description="Existing work outside this release candidate.",
        created_by_agent_id="lead",
        deliverable_ids=["foreign-deliverable"],
    )
    state.deliverables["foreign-deliverable"] = Deliverable(
        id="foreign-deliverable",
        task_id="foreign-task",
        producer_agent_id="owner",
        summary="Not included in the reviewed release.",
        state=TeamRecordState.ACCEPTED,
    )
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    original_revision = request.revision

    with pytest.raises(
        ValueError,
        match=(
            "human finding 1 targets deliverables outside the reviewed release "
            "candidate: foreign-deliverable"
        ),
    ):
        record_human_review_decision(
            state,
            human_review_request_id=request.id,
            reviewer_user_id="reviewer",
            reviewer_display_name="Reviewer",
            decision="changes_requested",
            request_id="foreign-finding-target",
            expected_revision=request.revision,
            checklist_results={"smoke": True},
            findings=[
                {
                    "text": "Change unrelated work.",
                    "target_deliverable_ids": ["foreign-deliverable"],
                }
            ],
            result={"events": []},
        )

    assert request.state == "pending"
    assert request.revision == original_revision
    assert state.human_review_decisions == {}
    assert state.human_review_findings == {}
    assert state.cases == {}


def test_human_approval_queues_exact_candidate_once() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    result: dict[str, object] = {"events": []}

    decision = record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="approved",
        request_id="decision-1",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        result=result,
    )

    assert decision.decision == "approved"
    assert request.state == "approved"
    assert release.approved_commit_sha == release.candidate_commit_sha
    canonical = [
        record
        for record in state.git_promotions.values()
        if record.promotion_kind == "canonical_push"
    ]
    assert len(canonical) == 1
    assert canonical[0].sources[0].expected_commit_sha == release.candidate_commit_sha

    replay = record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="approved",
        request_id="decision-1",
        expected_revision=0,
        checklist_results={"smoke": True},
        result={},
    )
    assert replay.id == decision.id
    assert (
        len(
            [
                record
                for record in state.git_promotions.values()
                if record.promotion_kind == "canonical_push"
            ]
        )
        == 1
    )


def test_combined_gates_wait_for_agent_acceptance_and_keep_human_snapshot() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["human-reviewer"])
    state.agents["release-reviewer"] = PersistentAgent(
        id="release-reviewer",
        team_id="team",
        role_id="reviewer-role",
        session_id="release-reviewer-session",
    )
    state.review_profiles["release-profile"] = ReviewProfile(
        id="release-profile",
        team_id="team",
        project_id="project",
        name="Release acceptance",
    )
    verification_command = ReleaseVerificationCommand(
        name="candidate-tests",
        argv=["pytest", "-q"],
    )
    human_requirement = HumanReviewRequirement(
        reviewer_group_ids=["release-reviewers"],
        resolved_reviewer_user_ids=["human-reviewer"],
        directory_path="root",
        checklist=[HumanReviewChecklistItem(id="smoke", label="Smoke test")],
    )
    state.projects["project"].release_policy = ProjectReleasePolicy(
        verification_commands=[verification_command],
        review_requirements=[
            ReleaseReviewRequirement(
                reviewer_agent_id="release-reviewer",
                review_profile_id="release-profile",
            )
        ],
        human_review_requirement=human_requirement,
    )
    result: dict[str, object] = {
        "events": [],
        "git_promotion_event_ids": [],
    }

    begin_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        candidate_commit_sha=release.candidate_commit_sha or "",
        candidate_tree_hash=release.candidate_tree_hash,
        result=result,
    )

    assert len(state.release_verification_runs) == 1
    assert state.review_requests == {}
    assert state.human_review_requests == {}
    verification_run = next(iter(state.release_verification_runs.values()))
    record_release_verification_result(
        state,
        release_package_id=release.id,
        verification_run_id=verification_run.id,
        candidate_commit_sha=release.candidate_commit_sha or "",
        verification_state="passed",
        failure_kind=None,
        command_results=[
            ReleaseVerificationCommandResult(
                name=verification_command.name,
                state="passed",
                exit_code=0,
            )
        ],
        error=None,
        result=result,
    )

    assert len(state.review_requests) == 1
    assert state.human_review_requests == {}
    frozen_requirement = release.manifest.human_review_requirement.model_copy(deep=True)
    state.projects["project"].release_policy = ProjectReleasePolicy(allow_ungated=True)
    release_review = next(iter(state.review_requests.values()))
    _cascade_review_decision(
        state,
        review=release_review,
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=result,
    )

    request = next(iter(state.human_review_requests.values()))
    assert request.state == "pending"
    assert request.requirement_snapshot == frozen_requirement
    assert release.approved_commit_sha is None
    assert not any(
        record.promotion_kind == "canonical_push" for record in state.git_promotions.values()
    )

    state.projects["project"].release_policy = ProjectReleasePolicy(
        human_review_requirement=HumanReviewRequirement(
            reviewer_group_ids=["replacement-reviewers"],
            resolved_reviewer_user_ids=["replacement-reviewer"],
        )
    )
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    assert list(state.human_review_requests) == [request.id]
    assert state.human_review_requests[request.id].requirement_snapshot == (frozen_requirement)
    assert release.approved_commit_sha is None


@pytest.mark.parametrize(
    ("first_decision", "second_decision"),
    [
        ("approved", "approved"),
        ("changes_requested", "changes_requested"),
        ("approved", "changes_requested"),
    ],
)
def test_concurrent_admin_terminal_decisions_have_one_atomic_outcome(
    tmp_path: Path,
    first_decision: str,
    second_decision: str,
) -> None:
    repository, request_id = _persist_pending_human_review(
        tmp_path,
        eligible_user_ids=["reviewer-one", "reviewer-two"],
    )
    service = TeamAdminService(repository)
    barrier = Barrier(2)

    def _decide(index: int, decision: str):
        barrier.wait()
        return service.record_human_review_decision_with_result(
            human_review_request_id=request_id,
            reviewer_user_id=f"reviewer-{'one' if index == 1 else 'two'}",
            reviewer_display_name=f"Reviewer {index}",
            decision=decision,
            request_id=f"concurrent-decision-{index}",
            expected_request_revision=0,
            checklist_results={"smoke": True},
            summary=(
                "Correct the release behavior."
                if decision == "changes_requested"
                else "The candidate is accepted."
            ),
            findings=(
                [
                    {
                        "text": "The alarm remains latched.",
                        "target_deliverable_ids": ["source-deliverable"],
                    }
                ]
                if decision == "changes_requested"
                else []
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_decide, 1, first_decision),
            executor.submit(_decide, 2, second_decision),
        ]
        successes = []
        failures = []
        for future in futures:
            try:
                successes.append(future.result())
            except ValueError as exc:
                failures.append(exc)

    assert len(successes) == 1
    assert len(failures) == 1
    assert "stale human review revision" in str(failures[0])
    state = repository.read()
    request = state.human_review_requests[request_id]
    assert request.state in {"approved", "changes_requested"}
    assert len(state.human_review_decisions) == 1
    canonical_promotions = [
        record
        for record in state.git_promotions.values()
        if record.promotion_kind == "canonical_push"
    ]
    correction_cases = [
        case for case in state.cases.values() if case.source == "human_release_review"
    ]
    if request.state == "approved":
        assert len(canonical_promotions) == 1
        assert correction_cases == []
        assert state.human_review_findings == {}
    else:
        assert canonical_promotions == []
        assert len(correction_cases) == 1
        assert len(state.human_review_findings) == 1
        assert len(correction_cases[0].task_ids) == 2


def test_changes_requested_transaction_rolls_back_after_consequence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, request_id = _persist_pending_human_review(
        tmp_path,
        eligible_user_ids=["reviewer"],
    )
    service = TeamAdminService(repository)
    before = repository.read()
    before_payload = before.model_dump(mode="json")
    observed_constructed_consequences = False

    def _fail_after_consequences(state, _project_id):
        nonlocal observed_constructed_consequences
        assert len(state.human_review_decisions) == 1
        assert len(state.human_review_findings) == 1
        assert len(state.cases) == 1
        assert len(state.tasks) == 3
        assert len(state.inbox) == 1
        observed_constructed_consequences = True
        raise RuntimeError("injected after human-review consequence construction")

    monkeypatch.setattr(
        human_review_domain,
        "_recalculate_project_status",
        _fail_after_consequences,
    )
    with pytest.raises(
        RuntimeError,
        match="injected after human-review consequence construction",
    ):
        service.record_human_review_decision_with_result(
            human_review_request_id=request_id,
            reviewer_user_id="reviewer",
            reviewer_display_name="Reviewer",
            decision="changes_requested",
            request_id="rollback-decision",
            expected_request_revision=0,
            checklist_results={"smoke": True},
            summary="Correct the release behavior.",
            findings=[
                {
                    "text": "The alarm remains latched.",
                    "target_deliverable_ids": ["source-deliverable"],
                }
            ],
        )

    assert observed_constructed_consequences is True
    assert repository.read().model_dump(mode="json") == before_payload
    assert repository.list_outbox_records() == []
    assert (
        repository.get_idempotency_record(
            tool_name="team.human_review.decision",
            caller_identity="reviewer",
            scope_kind="human_review_request",
            scope_id=request_id,
            request_id="rollback-decision",
        )
        is None
    )


def test_changes_requested_replay_has_exact_consequences_and_no_publication(
    tmp_path: Path,
) -> None:
    repository, request_id = _persist_pending_human_review(
        tmp_path,
        eligible_user_ids=["reviewer"],
    )
    service = TeamAdminService(repository)
    command = {
        "human_review_request_id": request_id,
        "reviewer_user_id": "reviewer",
        "reviewer_display_name": "Reviewer",
        "decision": "changes_requested",
        "request_id": "exact-changes-requested",
        "expected_request_revision": 0,
        "checklist_results": {"smoke": True},
        "summary": "Correct the specific and release-wide failures.",
        "findings": [
            {
                "text": "The alarm remains latched.",
                "target_deliverable_ids": ["source-deliverable"],
                "evidence_refs": ["artifact:alarm"],
            },
            {
                "text": "The release-wide recovery path is unclear.",
                "target_scope": "release_candidate",
                "evidence_refs": ["artifact:recovery"],
            },
        ],
    }

    first_state, first_result = service.record_human_review_decision_with_result(**command)

    assert first_result["idempotent_replay"] is False
    assert len(first_state.human_review_decisions) == 1
    assert len(first_state.human_review_findings) == 2
    correction_cases = [
        case for case in first_state.cases.values() if case.source == "human_release_review"
    ]
    assert len(correction_cases) == 1
    case = correction_cases[0]
    assert len(case.task_ids) == 3
    aggregation_task_id = case.metadata["human_correction_aggregation_task_id"]
    assert first_state.tasks[aggregation_task_id].task_kind == TeamTaskKind.COORDINATION
    assert {first_state.tasks[task_id].owner_agent_id for task_id in case.task_ids} == {
        "lead",
        "owner",
    }
    correction_inbox = [
        item
        for item in first_state.inbox.values()
        if item.payload.get("human_review_request_id") == request_id
    ]
    assert len(correction_inbox) == 2
    assert {item.task_id for item in correction_inbox} == {
        finding.correction_task_id for finding in first_state.human_review_findings.values()
    }
    assert aggregation_task_id not in {item.task_id for item in correction_inbox}
    assert set(first_state.agent_runtimes) == {"lead", "owner"}
    assert all(
        first_state.agent_runtimes[agent_id].next_wake_at is None for agent_id in ("lead", "owner")
    )
    wake_records = repository.list_outbox_records(intent_type="team.agent.wake_requested")
    assert len(wake_records) == 2
    assert {record.payload["payload"]["agent_id"] for record in wake_records} == {"lead", "owner"}
    assert not any(
        record.promotion_kind == "canonical_push" for record in first_state.git_promotions.values()
    )
    first_revision = first_state.revision
    first_outbox_ids = {record.id for record in wake_records}

    replay_state, replay_result = service.record_human_review_decision_with_result(**command)

    assert replay_result["idempotent_replay"] is True
    assert replay_state.revision == first_revision
    assert len(replay_state.human_review_decisions) == 1
    assert len(replay_state.human_review_findings) == 2
    assert (
        len([case for case in replay_state.cases.values() if case.source == "human_release_review"])
        == 1
    )
    assert {record.id for record in repository.list_outbox_records()} == (first_outbox_ids)


def test_empty_assignment_blocks_and_reassignment_resumes_same_request() -> None:
    state, release, promotion = _human_state()
    result: dict[str, object] = {"events": []}

    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result=result,
    )

    request = next(iter(state.human_review_requests.values()))
    assert request.state == "blocked"
    assert release.state == TeamRecordState.BLOCKED
    blocker = next(iter(state.blockers.values()))
    assert blocker.reason_code == "human_review_assignment_unavailable"
    assert blocker.route == TeamBlockerRoute.OPERATOR
    assert blocker.owner_agent_id == "lead"
    blocker_items = [
        item for item in state.inbox.values() if item.payload.get("blocker_id") == blocker.id
    ]
    assert any(
        item.state == TeamInboxItemState.HANDLED and item.payload.get("kind") == "blocker_reported"
        for item in blocker_items
    )
    visibility_item = next(
        item
        for item in blocker_items
        if item.payload.get("kind") == "human_review_operator_recovery_visibility"
    )
    assert visibility_item.state == TeamInboxItemState.PENDING
    assert visibility_item.recipient_agent_id == "lead"
    assert visibility_item.priority.value == "high"
    assert visibility_item.payload["route"] == "operator"
    visibility_wake = next(
        event
        for event in result["events"]
        if event["event_type"] == "team.agent.wake_requested"
        and event["payload"]["agent_id"] == "lead"
    )
    assert visibility_wake["payload"]["reason"] == "inbox"
    routing_event = next(
        event
        for event in state.blocker_events.values()
        if event.blocker_id == blocker.id
        and event.metadata.get("recovery_permission") == "admin.owner"
    )
    assert routing_event.metadata["visibility_agent_id"] == "lead"

    with pytest.raises(
        ValueError,
        match="audited reassignment",
    ):
        resume_human_review(
            state,
            human_review_request_id=request.id,
            result={"events": []},
        )

    reassignment_result: dict[str, object] = {"events": []}
    reassigned = reassign_human_review(
        state,
        human_review_request_id=request.id,
        user_ids=["reviewer"],
        assigned_by_user_id="admin-owner",
        result=reassignment_result,
    )

    assert reassigned.id == request.id
    assert reassigned.assignment_generation == 2
    assert reassigned.state == "pending"
    assert release.state == TeamRecordState.PENDING
    assert state.blockers[blocker.id].state == TeamBlockerState.RESOLVED
    assert [
        event["payload"]["assignment_generation"]
        for event in reassignment_result["events"]
        if event["event_type"] == "team.release.human_review_requested"
    ] == [2]

    reconcile_human_review_assignment(
        state,
        human_review_request_id=request.id,
        decision_eligible_user_ids=[],
        result={"events": []},
    )
    assert request.state == "blocked"
    with pytest.raises(
        ValueError,
        match="audited reassignment",
    ):
        resume_human_review(
            state,
            human_review_request_id=request.id,
            result={"events": []},
        )
    repeated_assignment_result: dict[str, object] = {"events": []}
    repeated = reassign_human_review(
        state,
        human_review_request_id=request.id,
        user_ids=["reviewer"],
        assigned_by_user_id="admin-owner",
        result=repeated_assignment_result,
    )
    assert repeated.id == request.id
    assert repeated.assignment_generation == 3
    assert [
        (
            event["payload"]["recipient_user_id"],
            event["payload"]["assignment_generation"],
        )
        for event in repeated_assignment_result["events"]
        if event["event_type"] == "team.release.human_review_requested"
    ] == [("reviewer", 3)]


def test_human_gate_uses_current_directory_eligibility_not_stale_snapshot() -> None:
    state, release, promotion = _human_state(
        eligible_user_ids=["stale-reviewer"],
    )
    result: dict[str, object] = {
        "events": [],
        HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY: (lambda _requirement: ["current-reviewer"]),
    }

    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result=result,
    )

    request = next(iter(state.human_review_requests.values()))
    assert request.current_assignment.eligible_user_ids == ["current-reviewer"]
    assert request.state == "pending"
    recipients = {
        event["payload"]["recipient_user_id"]
        for event in result["events"]
        if event["event_type"] == "team.release.human_review_requested"
    }
    assert recipients == {"current-reviewer"}

    empty_state, empty_release, empty_promotion = _human_state(
        eligible_user_ids=["stale-reviewer"],
    )
    empty_result: dict[str, object] = {
        "events": [],
        HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY: (lambda _requirement: []),
    }
    advance_release_gates(
        empty_state,
        release=empty_release,
        release_promotion=empty_promotion,
        result=empty_result,
    )
    empty_request = next(iter(empty_state.human_review_requests.values()))
    assert empty_request.state == "blocked"
    assert empty_request.current_assignment.eligible_user_ids == []


@pytest.mark.parametrize("waive_predecessor", [False, True])
def test_disabled_human_gate_completes_publication_with_retained_settings(
    tmp_path: Path,
    waive_predecessor: bool,
) -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    for stage in release.manifest.gate_sequence:
        stage.enabled = False
    if waive_predecessor:
        predecessor = ReleasePackage(
            id="predecessor",
            project_id="project",
            candidate_commit_sha="c" * 40,
            state=TeamRecordState.REJECTED,
            gate_failure=ReleaseGateFailure(
                stage="human_review",
                kind="changes_requested",
                source_id="predecessor-human-review",
                message="Requested a correction.",
            ),
        )
        state.release_packages[predecessor.id] = predecessor
        release.supersedes_release_package_id = predecessor.id
        release.manifest.carried_human_findings = [
            HumanReviewFindingSnapshot(
                finding_id="predecessor-finding",
                text="Requested a correction.",
                target_scope=HumanReviewFindingTargetScope.RELEASE_CANDIDATE,
            )
        ]
    advance_release_gates(state, release=release, release_promotion=promotion, result={"events": []})
    repository = TeamStateRepository(tmp_path)
    repository.update(lambda _current: state)
    if waive_predecessor:
        assert release.state == TeamRecordState.BLOCKED
        TeamAdminService(repository).authorize_release_gate_obligation_waiver_with_result(
            release_package_id=release.id,
            gate_kind=ReleaseGateKind.HUMAN_REVIEW.value,
            candidate_commit_sha=release.candidate_commit_sha,
            reason="Accept the remaining finding for this candidate.",
            operator_actor_id="operator:owner",
        )

    def _record_push_success(current: TeamState) -> TeamState:
        push = next(
            row for row in current.git_promotions.values()
            if row.promotion_kind == "canonical_push"
        )
        push.state = "succeeded"
        push.commit_sha = release.candidate_commit_sha
        return current

    repository.update(_record_push_success)
    published, result = TeamGitReleaseService(repository).mark_release_published(
        release_package_id=release.id,
        canonical_branch=release.canonical_branch,
        published_commit_sha=release.candidate_commit_sha,
    )

    assert result["error"] is None
    completed = published.release_packages[release.id]
    assert completed.state == TeamRecordState.ACCEPTED
    assert completed.published_commit_sha == release.candidate_commit_sha
    assert completed.published_at is not None
    assert completed.manifest.human_review_requirement is not None
    assert not published.human_review_requests
    if waive_predecessor:
        assert completed.gate_obligation_waivers[0].source_ids == ["predecessor-human-review"]
        assert completed.manifest.carried_human_findings[0].finding_id == "predecessor-finding"


@pytest.mark.parametrize(
    "invalid_binding",
    ["missing", "gate_kind", "predecessor_release_package_id", "candidate_commit_sha", "source_ids"],
)
def test_disabled_human_gate_rejects_missing_or_mismatched_waiver(
    invalid_binding: str,
) -> None:
    state, release, _promotion = _human_state(eligible_user_ids=["reviewer"])
    for stage in release.manifest.gate_sequence:
        stage.enabled = False
    release.supersedes_release_package_id = "predecessor"
    state.release_packages["predecessor"] = ReleasePackage(
        id="predecessor",
        project_id="project",
        state=TeamRecordState.REJECTED,
        gate_failure=ReleaseGateFailure(
            stage="human_review",
            kind="changes_requested",
            source_id="predecessor-human-review",
            message="Requested a correction.",
        ),
    )
    waiver = ReleaseGateObligationWaiver(
        id="waiver",
        gate_kind=ReleaseGateKind.HUMAN_REVIEW,
        predecessor_release_package_id="predecessor",
        candidate_commit_sha=release.candidate_commit_sha,
        source_ids=["predecessor-human-review"],
        reason="Accept the remaining finding for this candidate.",
        authorized_by="operator:owner",
    )
    if invalid_binding != "missing":
        invalid_value = {
            "gate_kind": ReleaseGateKind.AGENT_REVIEW,
            "predecessor_release_package_id": "other-predecessor",
            "candidate_commit_sha": "other-candidate",
            "source_ids": ["other-human-review"],
        }[invalid_binding]
        release.gate_obligation_waivers = [
            waiver.model_copy(update={invalid_binding: invalid_value})
        ]

    assert "requires a candidate-bound waiver" in (
        _human_review_publication_validation_error(
            state,
            release=release,
            candidate_commit_sha=release.candidate_commit_sha,
        ) or ""
    )


def test_gate_waiver_schema_migrates_existing_release_history(tmp_path: Path) -> None:
    state, release, _promotion = _human_state(eligible_user_ids=["reviewer"])
    release.state = TeamRecordState.ACCEPTED
    release.approved_commit_sha = release.candidate_commit_sha
    release.published_commit_sha = release.candidate_commit_sha
    release.published_at = release.created_at
    repository = TeamStateRepository(tmp_path)
    before = repository.update(lambda _current: state)
    with sqlite3.connect(tmp_path / "addons" / "team-addon" / "state.db") as connection:
        connection.execute(
            "ALTER TABLE team_release_packages DROP COLUMN gate_obligation_waivers_json"
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = '75' WHERE key = 'sqlite_schema_version'"
        )

    reopened = TeamStateRepository(tmp_path)
    after = reopened.read()
    assert after.release_packages[release.id] == before.release_packages[release.id]
    assert after.release_packages[release.id].gate_obligation_waivers == []
    assert reopened.read().release_packages[release.id] == after.release_packages[release.id]


@pytest.mark.parametrize("current_reviewers", [["current-reviewer"], []])
def test_gate_waiver_resolves_current_human_review_assignment(
    tmp_path: Path,
    current_reviewers: list[str],
) -> None:
    state, release, promotion = _human_state(eligible_user_ids=["former-reviewer"])
    predecessor = ReleasePackage(
        id="predecessor",
        project_id="project",
        candidate_commit_sha="c" * 40,
        state=TeamRecordState.REJECTED,
        gate_failure=ReleaseGateFailure(
            stage="review",
            kind="rejected",
            source_id="predecessor-agent-review",
            message="Requested a correction.",
        ),
    )
    state.release_packages[predecessor.id] = predecessor
    release.supersedes_release_package_id = predecessor.id
    advance_release_gates(state, release=release, release_promotion=promotion, result={"events": []})
    assert release.state == TeamRecordState.BLOCKED
    assert not state.human_review_requests
    repository = TeamStateRepository(tmp_path)
    before = repository.update(lambda _current: state)
    resolved: list[HumanReviewRequirementSnapshot] = []

    def _resolve(requirement: HumanReviewRequirementSnapshot) -> list[str]:
        resolved.append(requirement)
        return current_reviewers

    updated, result = TeamAdminService(
        repository,
        human_review_eligibility_resolver=_resolve,
    ).authorize_release_gate_obligation_waiver_with_result(
        release_package_id=release.id,
        gate_kind=ReleaseGateKind.AGENT_REVIEW.value,
        candidate_commit_sha=release.candidate_commit_sha,
        reason="Delegate this candidate's remaining review to the human gate.",
        operator_actor_id="operator:owner",
        expected_revision=before.revision,
    )

    assert len(resolved) == 1
    assert resolved[0].configured_group_ids == ["release-reviewers"]
    request = next(iter(updated.human_review_requests.values()))
    assert request.current_assignment.eligible_user_ids == current_reviewers
    assert request.state == ("pending" if current_reviewers else "blocked")
    assert request.requirement_snapshot.initially_eligible_user_ids == ["former-reviewer"]
    notifications = repository.list_outbox_records(intent_type="team.release.human_review_requested")
    assert {
        record.payload["payload"]["recipient_user_id"] for record in notifications
    } == set(current_reviewers)
    assert HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY not in result
    json.dumps(result)


def test_changes_requested_is_deterministic_and_preserves_history() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.tasks["source-task"].authored_by = ProjectGitIdentity(
        name="Delegating User", email="delegating@example.test",
    )
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    original_task = state.tasks["source-task"].model_copy(deep=True)
    original_deliverable = state.deliverables["source-deliverable"].model_copy(deep=True)
    result: dict[str, object] = {"events": []}

    decision = record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="changes-1",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        summary="Correct the alarm behavior.",
        findings=[
            {
                "text": "Alarm remains latched after acknowledgement.",
                "target_deliverable_ids": ["source-deliverable"],
                "evidence_refs": ["attachment:alarm-video"],
            }
        ],
        result=result,
    )

    assert decision.decision == "changes_requested"
    assert request.state == "changes_requested"
    assert release.state == TeamRecordState.REJECTED
    assert release.approved_commit_sha is None
    assert state.tasks["source-task"] == original_task
    assert state.deliverables["source-deliverable"] == original_deliverable
    finding = next(iter(state.human_review_findings.values()))
    correction = state.tasks[finding.correction_task_id]
    case = state.cases[correction.case_id or ""]
    assert finding.target_deliverable_ids == ["source-deliverable"]
    assert correction.title == finding.text
    assert correction.description == finding.text
    assert correction.parent_task_id == case.metadata["human_correction_aggregation_task_id"]
    assert correction.originating_task_id == "source-task"
    assert correction.authored_by == original_task.authored_by
    assert state.tasks[correction.parent_task_id].authored_by == original_task.authored_by
    assert correction.owner_agent_id == "owner"
    assert case.source_ref == request.id
    assert len(state.inbox) == 1
    ensure_result: dict[str, object] = {}
    ensured_parent = human_review_domain.ensure_human_correction_aggregation(
        state,
        case=case,
        result=ensure_result,
    )
    assert ensured_parent.id == correction.parent_task_id
    assert "created_task_ids" not in ensure_result
    assert "reparented_correction_task_ids" not in ensure_result

    replay_result: dict[str, object] = {}
    replay = record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="changes-1",
        expected_revision=0,
        checklist_results={"smoke": True},
        findings=[{"text": "ignored on idempotent replay"}],
        result=replay_result,
    )
    assert replay.id == decision.id
    assert len(state.human_review_findings) == 1
    assert len(state.cases) == 1
    assert len(state.inbox) == 1


def test_manifest_only_candidate_branch_pins_human_correction_graph() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    assert release.manifest is not None
    candidate_branch = release.manifest.release_branch
    release.release_branch = None
    release.git_base_branch = "main"
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))

    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="changes-manifest-only-branch",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        summary="Correct the rejected candidate.",
        findings=[
            {
                "text": "Alarm remains latched after acknowledgement.",
                "target_deliverable_ids": ["source-deliverable"],
                "evidence_refs": ["attachment:alarm-video"],
            }
        ],
        result={"events": []},
    )

    finding = next(iter(state.human_review_findings.values()))
    correction = state.tasks[finding.correction_task_id]
    case = state.cases[correction.case_id or ""]
    aggregation = state.tasks[str(case.metadata["human_correction_aggregation_task_id"])]
    assert aggregation.git_base_branch == candidate_branch
    assert correction.git_base_branch == candidate_branch
    assert (
        _resolve_new_task_git_base_branch(
            state,
            project_id=release.project_id,
            explicit_git_base_branch="main",
            case_id=case.id,
        )
        == candidate_branch
    )


def test_specific_finding_routes_to_lead_when_historical_owner_is_detached() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    state.project_assignments.pop("owner-project")
    owner = state.agents["owner"]
    owner.team_id = None
    owner.role_id = None
    owner.reports_to_agent_id = None

    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="changes-detached-owner",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "Correct the historical source behavior.",
                "target_deliverable_ids": ["source-deliverable"],
            }
        ],
        result={"events": []},
    )

    finding = next(iter(state.human_review_findings.values()))
    correction = state.tasks[finding.correction_task_id]
    case = state.cases[correction.case_id or ""]
    assert correction.originating_task_id == "source-task"
    assert correction.owner_agent_id == "lead"
    assert case.metadata["human_correction_ambiguous_finding_ids"] == []
    TeamState.model_validate(state.model_dump(mode="json"))


def test_routed_ambiguous_finding_rejects_artifact_only_successor() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.agents["owner"].status = AgentStatus.PAUSED
    state.deliverables["source-deliverable"].artifact_refs = ["artifact:source"]
    state.deliverables["source-deliverable"].verification = DeliverableVerification(status="passed")
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="ambiguous-finding",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "The release-wide recovery behavior needs correction.",
                "target_scope": "release_candidate",
                "evidence_refs": ["attachment:recovery-video"],
            }
        ],
        result={"events": []},
    )
    finding = next(iter(state.human_review_findings.values()))
    correction_task_id = finding.correction_task_id
    case_id = state.tasks[correction_task_id].case_id
    finding.target_scope = HumanReviewFindingTargetScope.LEGACY_UNSPECIFIED
    state.tasks[correction_task_id].originating_task_id = "source-task"
    state.cases[case_id or ""].metadata[
        human_review_domain.HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY
    ] = [finding.id]
    state.agents["owner"].status = AgentStatus.ACTIVE
    result: dict[str, object] = {"events": []}

    correction = route_human_finding_correction(
        state,
        finding_id=finding.id,
        owner_agent_id="owner",
        result=result,
    )

    aggregation_task_id = state.cases[case_id or ""].metadata[
        "human_correction_aggregation_task_id"
    ]
    assert correction.id == correction_task_id
    assert correction.parent_task_id == aggregation_task_id
    assert correction.originating_task_id == "source-task"
    assert correction.case_id == case_id
    assert finding.correction_task_id == correction.id
    assert correction.id in state.cases[case_id or ""].task_ids
    assert "created_task_ids" not in result
    assert correction.title == finding.text
    assert correction.description == finding.text

    correction.state = TeamTaskState.FINISHED
    deliverable = Deliverable(
        id="ambiguous-correction-deliverable",
        task_id=correction.id,
        producer_agent_id="owner",
        artifact_refs=["artifact:release-wide-correction"],
        summary="Corrected release-wide recovery behavior.",
        state=TeamRecordState.ACCEPTED,
        verification=DeliverableVerification(status="passed"),
    )
    state.deliverables[deliverable.id] = deliverable
    correction.deliverable_ids.append(deliverable.id)
    proposed = record_human_finding_resolution_proposal(
        state,
        finding_id=finding.id,
        proposed_resolution="Recovery now uses the verified safe-state path.",
        evidence_refs=["test:release-wide-recovery"],
        proposed_by_deliverable_ids=[deliverable.id],
    )
    assert proposed.proposed_by_deliverable_ids == [deliverable.id]

    completed_result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }
    assert (
        _build_release_package(
            state,
            project=state.projects["project"],
            request={
                "id": "successor-with-artifact-only-correction",
                "deliverable_ids": [deliverable.id],
                "supersedes_release_package_id": release.id,
                "waiver_actor_kind": "operator",
                "waiver_actor_id": "admin",
            },
            result=completed_result,
            projects_to_recalc=set(),
            actor_agent_id="lead",
            team_id="team",
        )
        is None
    )
    assert completed_result["error_code"] == "candidate_git_inputs_required"
    assert "requires at least one accepted Git-backed deliverable" in completed_result["errors"][0]
    assert "successor-with-artifact-only-correction" not in state.release_packages


def test_routes_every_ambiguous_finding_after_legacy_graph_conversion() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.agents["owner"].status = AgentStatus.PAUSED
    advance_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="legacy-multi-finding",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "Correct the first release-wide behavior.",
                "target_scope": "release_candidate",
            },
            {
                "text": "Correct the second release-wide behavior.",
                "target_scope": "release_candidate",
            },
        ],
        result={"events": []},
    )
    findings = sorted(
        state.human_review_findings.values(),
        key=lambda finding: finding.text,
    )
    case = state.cases[state.tasks[findings[0].correction_task_id].case_id or ""]
    aggregation_task_id = case.metadata.pop(
        human_review_domain.HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY
    )
    case.metadata.pop(human_review_domain.HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY)
    case.metadata.pop("human_correction_graph_version")
    case.task_ids.remove(aggregation_task_id)
    state.tasks.pop(aggregation_task_id)
    for finding in findings:
        state.tasks[finding.correction_task_id].parent_task_id = None

    state.agents["owner"].status = AgentStatus.ACTIVE
    first = route_human_finding_correction(
        state,
        finding_id=findings[0].id,
        owner_agent_id="owner",
        result={"events": []},
    )

    converted_aggregation_id = case.metadata[
        human_review_domain.HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY
    ]
    assert first.id == findings[0].correction_task_id
    assert first.parent_task_id == converted_aggregation_id
    assert case.metadata[human_review_domain.HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] == [
        findings[1].id
    ]

    second = route_human_finding_correction(
        state,
        finding_id=findings[1].id,
        owner_agent_id="owner",
        result={"events": []},
    )

    assert second.id == findings[1].correction_task_id
    assert second.parent_task_id == converted_aggregation_id
    assert case.metadata[human_review_domain.HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] == []
    TeamState.model_validate(state.model_dump(mode="json"))


def test_preview_result_rejects_secrets_and_blocked_resume_reuses_request() -> None:
    state, release, _promotion = _human_state(
        eligible_user_ids=["reviewer"],
        preparation_mode="workflow",
    )
    request = ensure_human_review_request(
        state,
        release=release,
        result={"events": []},
    )
    assert request is not None
    assert request.state == "preparing"

    with pytest.raises(ValueError, match="secret-bearing"):
        record_human_review_preparation_result(
            state,
            human_review_request_id=request.id,
            candidate_commit_sha=request.candidate_commit_sha,
            candidate_tree_hash=request.candidate_tree_hash,
            preparation_generation=request.preparation_generation,
            target={"url": "https://preview.example", "access_token": "secret"},
            error=None,
            result={},
        )
    for credential_url in (
        "https://reviewer:password@preview.example",
        "https://preview.example?access_token=secret",
        "https://preview.example?token=secret",
        "https://preview.example?sig=azure-sas-signature",
        "https://preview.example?X-Amz-Signature=aws-signature",
        "https://preview.example#api_key=secret",
    ):
        with pytest.raises(
            ValueError,
            match="userinfo credentials|secret-bearing parameters",
        ):
            record_human_review_preparation_result(
                state,
                human_review_request_id=request.id,
                candidate_commit_sha=request.candidate_commit_sha,
                candidate_tree_hash=request.candidate_tree_hash,
                preparation_generation=request.preparation_generation,
                target={"url": credential_url},
                error=None,
                result={},
            )

    record_human_review_preparation_result(
        state,
        human_review_request_id=request.id,
        candidate_commit_sha=request.candidate_commit_sha,
        candidate_tree_hash=request.candidate_tree_hash,
        preparation_generation=request.preparation_generation,
        target=None,
        error="preview workflow unavailable",
        result={},
    )
    assert request.state == "blocked"
    generation = request.preparation_generation

    resumed = resume_human_review(
        state,
        human_review_request_id=request.id,
        result={"events": []},
    )
    assert resumed.id == request.id
    assert resumed.state == "preparing"
    assert resumed.preparation_generation == generation + 1

    ready_at = datetime.now(tz=UTC)
    record_human_review_preparation_result(
        state,
        human_review_request_id=request.id,
        candidate_commit_sha=request.candidate_commit_sha,
        candidate_tree_hash=request.candidate_tree_hash,
        preparation_generation=request.preparation_generation,
        target={
            "url": "https://preview.example",
            "expires_at": ready_at + timedelta(hours=1),
            "instructions": "Use the seeded operator account.",
        },
        error=None,
        result={"events": []},
        now=ready_at,
    )
    assert request.state == "pending"
    assert request.test_target is not None
    assert request.test_target.url == "https://preview.example"


def test_successor_rejects_unrelated_work_and_derives_exact_candidate_baseline() -> None:
    state, predecessor, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.deliverables["source-deliverable"].artifact_refs = ["artifact:source"]
    state.deliverables["source-deliverable"].verification = DeliverableVerification(status="passed")
    advance_release_gates(
        state,
        release=predecessor,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="changes-successor",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "Correct the latched alarm.",
                "target_deliverable_ids": ["source-deliverable"],
            }
        ],
        result={"events": []},
    )
    finding = next(iter(state.human_review_findings.values()))
    correction_task = state.tasks[finding.correction_task_id]
    correction_task.state = TeamTaskState.FINISHED
    correction = Deliverable(
        id="correction-deliverable",
        task_id=correction_task.id,
        producer_agent_id=correction_task.owner_agent_id,
        artifact_refs=["artifact:correction"],
        summary="Corrected alarm behavior",
        state=TeamRecordState.ACCEPTED,
        verification=DeliverableVerification(status="passed"),
    )
    state.deliverables[correction.id] = correction
    correction_task.deliverable_ids.append(correction.id)
    _mark_deliverable_as_reviewed_git_correction(
        state,
        deliverable=correction,
        commit_sha="c" * 40,
    )
    record_human_finding_resolution_proposal(
        state,
        finding_id=finding.id,
        proposed_resolution="Reset the latch after acknowledged safe state.",
        evidence_refs=["test:alarm-reset"],
        proposed_by_deliverable_ids=[correction.id],
    )
    unrelated_task = AgentTask(
        id="unrelated-task",
        owner_agent_id="owner",
        project_id="project",
        priority=2,
        state=TeamTaskState.FINISHED,
        title="Unrelated accepted work",
        description="This work was not authorized by the human correction case.",
        created_by_agent_id="lead",
        deliverable_ids=["unrelated-deliverable"],
    )
    unrelated = Deliverable(
        id="unrelated-deliverable",
        task_id=unrelated_task.id,
        producer_agent_id="owner",
        artifact_refs=["artifact:unrelated"],
        summary="An unrelated accepted feature",
        state=TeamRecordState.ACCEPTED,
        verification=DeliverableVerification(status="passed"),
    )
    state.tasks[unrelated_task.id] = unrelated_task
    state.deliverables[unrelated.id] = unrelated
    unrelated_result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }
    assert (
        _build_release_package(
            state,
            project=state.projects["project"],
            request={
                "id": "successor-with-unrelated-work",
                "deliverable_ids": [correction.id, unrelated.id],
                "supersedes_release_package_id": predecessor.id,
                "waiver_actor_kind": "operator",
                "waiver_actor_id": "admin",
            },
            result=unrelated_result,
            projects_to_recalc=set(),
            actor_agent_id="lead",
            team_id="team",
        )
        is None
    )
    correction_case_id = correction_task.case_id
    assert unrelated_result["errors"] == [
        "successor correction deliverable unrelated-deliverable is not "
        f"authorized by human review case {correction_case_id}"
    ]
    result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }

    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "successor",
            "deliverable_ids": [correction.id],
            "supersedes_release_package_id": predecessor.id,
            "waiver_actor_kind": "operator",
            "waiver_actor_id": "admin",
        },
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id == "successor"
    successor = state.release_packages["successor"]
    assert successor.supersedes_release_package_id == predecessor.id
    assert successor.base_commit_sha == predecessor.candidate_commit_sha
    assert successor.deliverable_ids == [
        "source-deliverable",
        "correction-deliverable",
    ]
    assert finding.state == "open"

    duplicate_result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }
    assert (
        _build_release_package(
            state,
            project=state.projects["project"],
            request={
                "id": "duplicate-successor-request",
                "deliverable_ids": [correction.id],
                "supersedes_release_package_id": predecessor.id,
                "waiver_actor_kind": "operator",
                "waiver_actor_id": "admin",
            },
            result=duplicate_result,
            projects_to_recalc=set(),
            actor_agent_id="lead",
            team_id="team",
        )
        == successor.id
    )
    assert duplicate_result["duplicate"] is True

    second_correction = Deliverable(
        id="second-correction-deliverable",
        task_id=correction_task.id,
        producer_agent_id=correction_task.owner_agent_id,
        artifact_refs=["artifact:second-correction"],
        summary="An additional corrected alarm behavior",
        state=TeamRecordState.ACCEPTED,
        verification=DeliverableVerification(status="passed"),
    )
    state.deliverables[second_correction.id] = second_correction
    correction_task.deliverable_ids.append(second_correction.id)
    _mark_deliverable_as_reviewed_git_correction(
        state,
        deliverable=second_correction,
        commit_sha="d" * 40,
    )
    sibling_result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }
    assert (
        _build_release_package(
            state,
            project=state.projects["project"],
            request={
                "id": "different-successor",
                "deliverable_ids": [correction.id, second_correction.id],
                "supersedes_release_package_id": predecessor.id,
                "waiver_actor_kind": "operator",
                "waiver_actor_id": "admin",
            },
            result=sibling_result,
            projects_to_recalc=set(),
            actor_agent_id="lead",
            team_id="team",
        )
        is None
    )
    assert sibling_result["errors"] == [
        "predecessor release release already has active successor successor; "
        "complete or recover it before requesting a different successor"
    ]
    assert "different-successor" not in state.release_packages


def test_human_correction_task_cannot_bypass_finding_resolution_evidence() -> None:
    state, predecessor, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.deliverables["source-deliverable"].artifact_refs = ["artifact:source"]
    state.deliverables["source-deliverable"].verification = DeliverableVerification(status="passed")
    advance_release_gates(
        state,
        release=predecessor,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="changes-missing-evidence",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "Correct the latched alarm.",
                "target_deliverable_ids": ["source-deliverable"],
            }
        ],
        result={"events": []},
    )
    finding = next(iter(state.human_review_findings.values()))
    correction_task = state.tasks[finding.correction_task_id]
    assert correction_task.created_by_actor == "system:human-review"
    assert correction_task.release_base_package_id == predecessor.id
    correction_task.state = TeamTaskState.FINISHED
    correction = Deliverable(
        id="correction-without-finding-evidence",
        task_id=correction_task.id,
        producer_agent_id=correction_task.owner_agent_id,
        artifact_refs=["artifact:correction"],
        summary="Corrected alarm behavior without proposing finding evidence.",
        state=TeamRecordState.ACCEPTED,
        verification=DeliverableVerification(status="passed"),
    )
    state.deliverables[correction.id] = correction
    correction_task.deliverable_ids.append(correction.id)
    _mark_deliverable_as_reviewed_git_correction(
        state,
        deliverable=correction,
        commit_sha="c" * 40,
    )
    result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }

    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "successor-without-finding-evidence",
            "deliverable_ids": [correction.id],
            "supersedes_release_package_id": predecessor.id,
        },
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id is None
    assert result["errors"] == [f"human finding {finding.id} has no proposed resolution"]
    assert "successor-without-finding-evidence" not in state.release_packages


def test_post_release_successor_without_findings_follows_current_human_policy() -> None:
    state, predecessor, _promotion = _human_state(eligible_user_ids=["reviewer"])
    state.deliverables["source-deliverable"].verification = DeliverableVerification(status="passed")
    predecessor.state = TeamRecordState.ACCEPTED
    predecessor.approved_commit_sha = predecessor.candidate_commit_sha
    predecessor.published_commit_sha = predecessor.candidate_commit_sha
    predecessor.published_at = datetime.now(tz=UTC)

    post_release_manifest = _successor_manifest(
        state,
        predecessor=predecessor,
        release_id="post-release-successor",
    )

    assert post_release_manifest.carried_human_findings == []
    assert post_release_manifest.human_review_requirement is None


def test_changes_requested_successor_inherits_review_for_open_findings() -> None:
    state, predecessor, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.deliverables["source-deliverable"].verification = DeliverableVerification(status="passed")
    advance_release_gates(
        state,
        release=predecessor,
        release_promotion=promotion,
        result={"events": []},
    )
    request = next(iter(state.human_review_requests.values()))
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="inherit-open-finding",
        expected_revision=request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "Correct the latched alarm.",
                "target_deliverable_ids": ["source-deliverable"],
            }
        ],
        result={"events": []},
    )

    correction_manifest = _successor_manifest(
        state,
        predecessor=predecessor,
        release_id="changes-requested-successor",
    )

    assert len(correction_manifest.carried_human_findings) == 1
    assert correction_manifest.carried_human_findings[0].target_scope == (
        HumanReviewFindingTargetScope.DELIVERABLES
    )
    assert correction_manifest.human_review_requirement == (
        predecessor.manifest.human_review_requirement
    )


def test_successor_carries_open_findings_from_the_full_release_lineage() -> None:
    state, predecessor, promotion = _human_state(eligible_user_ids=["reviewer"])
    state.projects["project"].release_policy = ProjectReleasePolicy(
        human_review_requirement=HumanReviewRequirement(
            reviewer_group_ids=["release-reviewers"],
            resolved_reviewer_user_ids=["reviewer"],
        ),
    )
    state.deliverables["source-deliverable"].verification = DeliverableVerification(status="passed")
    advance_release_gates(
        state,
        release=predecessor,
        release_promotion=promotion,
        result={"events": []},
    )
    first_request = next(iter(state.human_review_requests.values()))
    record_human_review_decision(
        state,
        human_review_request_id=first_request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="lineage-first-finding",
        expected_revision=first_request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "The original alarm recovery remains incorrect.",
                "target_deliverable_ids": ["source-deliverable"],
            }
        ],
        result={"events": []},
    )

    middle_manifest = _successor_manifest(
        state,
        predecessor=predecessor,
        release_id="lineage-middle",
    )
    middle = state.release_packages["lineage-middle"]
    middle.manifest = middle_manifest
    middle_promotion = state.git_promotions["lineage-middle-promotion"]
    advance_release_gates(
        state,
        release=middle,
        release_promotion=middle_promotion,
        result={"events": []},
    )
    middle_request = next(
        request
        for request in state.human_review_requests.values()
        if request.release_package_id == middle.id
    )
    record_human_review_decision(
        state,
        human_review_request_id=middle_request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="lineage-second-finding",
        expected_revision=middle_request.revision,
        checklist_results={"smoke": True},
        findings=[
            {
                "text": "The new correction still drops acknowledgements.",
                "target_deliverable_ids": ["source-deliverable"],
            }
        ],
        result={"events": []},
    )

    successor_manifest = _successor_manifest(
        state,
        predecessor=middle,
        release_id="lineage-successor",
    )

    assert {finding.text for finding in successor_manifest.carried_human_findings} == {
        "The new correction still drops acknowledgements.",
        "The original alarm recovery remains incorrect.",
    }


@pytest.mark.parametrize(
    ("preparation_mode", "event_type"),
    [
        ("manual", "team.release.human_review_requested"),
        ("workflow", "team.release.human_review_preparation_requested"),
    ],
)
def test_git_promotion_queues_initial_human_review_intent_in_outbox(
    tmp_path: Path,
    preparation_mode: str,
    event_type: str,
) -> None:
    state, release, promotion = _human_state(
        eligible_user_ids=["reviewer"],
        preparation_mode=preparation_mode,
    )
    state.projects["project"].release_policy = ProjectReleasePolicy(
        human_review_requirement=HumanReviewRequirement(
            reviewer_group_ids=["release-reviewers"],
            resolved_reviewer_user_ids=["reviewer"],
            preparation_mode=preparation_mode,
        )
    )
    promotion.state = "pending"
    repository = TeamStateRepository(tmp_path)
    repository.update(lambda _current: state)

    service = TeamGitReleaseService(
        repository,
        human_review_eligibility_resolver=lambda _requirement: ["reviewer"],
    )
    _state, result = service.record_git_promotion_result(
        promotion_id=promotion.id,
        success=True,
        commit_sha=release.candidate_commit_sha,
        outputs={"tree_hash": release.candidate_tree_hash},
    )

    assert any(event["event_type"] == event_type for event in result["events"])
    outbox = repository.list_outbox_records(
        status=TeamOutboxStatus.PENDING,
        intent_type=event_type,
    )
    assert len(outbox) == 1
    request = next(iter(repository.read().human_review_requests.values()))
    assert request.state == ("preparing" if preparation_mode == "workflow" else "pending")
    if preparation_mode == "workflow":
        assert outbox[0].payload["payload"]["preparation_generation"] == 1
    else:
        assert outbox[0].payload["payload"]["recipient_user_id"] == "reviewer"
    if preparation_mode == "workflow":
        _replayed_state, replayed = service.record_git_promotion_result(
            promotion_id=promotion.id,
            success=True,
            commit_sha=release.candidate_commit_sha,
            outputs={"tree_hash": release.candidate_tree_hash},
        )
        assert not any(event["event_type"] == event_type for event in replayed["events"])
        assert (
            len(
                repository.list_outbox_records(
                    status=TeamOutboxStatus.PENDING,
                    intent_type=event_type,
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    ("preparation_mode", "event_type"),
    [
        ("manual", "team.release.human_review_requested"),
        ("workflow", "team.release.human_review_preparation_requested"),
    ],
)
def test_verification_queues_initial_human_review_intent_in_outbox(
    tmp_path: Path,
    preparation_mode: str,
    event_type: str,
) -> None:
    state, release, promotion = _human_state(
        eligible_user_ids=["reviewer"],
        preparation_mode=preparation_mode,
    )
    verification_command = ReleaseVerificationCommand(
        name="candidate-tests",
        argv=["pytest", "-q"],
    )
    state.projects["project"].release_policy = ProjectReleasePolicy(
        verification_commands=[verification_command],
        human_review_requirement=HumanReviewRequirement(
            reviewer_group_ids=["release-reviewers"],
            resolved_reviewer_user_ids=["reviewer"],
            preparation_mode=preparation_mode,
        ),
    )
    begin_release_gates(
        state,
        release=release,
        release_promotion=promotion,
        candidate_commit_sha=release.candidate_commit_sha or "",
        candidate_tree_hash=release.candidate_tree_hash,
        result={"events": [], "git_promotion_event_ids": []},
    )
    verification_run = next(iter(state.release_verification_runs.values()))
    repository = TeamStateRepository(tmp_path)
    repository.update(lambda _current: state)

    service = TeamReleaseGateService(
        repository,
        human_review_eligibility_resolver=lambda _requirement: ["reviewer"],
    )
    command_results = [
        {
            "name": verification_command.name,
            "state": "passed",
            "exit_code": 0,
        }
    ]
    _state, result = service.record_verification_result(
        release_package_id=release.id,
        verification_run_id=verification_run.id,
        candidate_commit_sha=release.candidate_commit_sha or "",
        verification_state="passed",
        command_results=command_results,
    )

    assert any(event["event_type"] == event_type for event in result["events"])
    outbox = repository.list_outbox_records(
        status=TeamOutboxStatus.PENDING,
        intent_type=event_type,
    )
    assert len(outbox) == 1
    request = next(iter(repository.read().human_review_requests.values()))
    assert request.state == ("preparing" if preparation_mode == "workflow" else "pending")
    if preparation_mode == "workflow":
        _replayed_state, replayed = service.record_verification_result(
            release_package_id=release.id,
            verification_run_id=verification_run.id,
            candidate_commit_sha=release.candidate_commit_sha or "",
            verification_state="passed",
            command_results=command_results,
        )
        assert not any(event["event_type"] == event_type for event in replayed["events"])
        assert (
            len(
                repository.list_outbox_records(
                    status=TeamOutboxStatus.PENDING,
                    intent_type=event_type,
                )
            )
            == 1
        )


def test_successor_rejects_unchanged_predecessor_tree() -> None:
    state, predecessor, _promotion = _human_state(eligible_user_ids=["reviewer"])
    predecessor.state = TeamRecordState.REJECTED
    successor = ReleasePackage(
        id="successor",
        project_id=predecessor.project_id,
        supersedes_release_package_id=predecessor.id,
        release_branch="team/team/releases/successor",
        candidate_commit_sha=None,
    )
    state.release_packages[successor.id] = successor
    successor_promotion = GitPromotionRecord(
        id="successor-promotion",
        project_id="project",
        release_package_id=successor.id,
        actor_agent_id="lead",
        source_branch="team/team/features/correction",
        target_branch="team/team/releases/successor",
        promotion_kind="release",
        state="succeeded",
        commit_sha="c" * 40,
    )

    result: dict[str, object] = {"events": []}
    begin_release_gates(
        state,
        release=successor,
        release_promotion=successor_promotion,
        candidate_commit_sha="c" * 40,
        candidate_tree_hash=predecessor.candidate_tree_hash,
        result=result,
    )

    assert successor.state == TeamRecordState.REJECTED
    assert successor.gate_failure is not None
    assert successor.gate_failure.stage == "human_review"
    assert "unchanged" in (successor.error or "")


def test_published_release_successor_requires_explicit_open_feedback_case() -> None:
    state, predecessor, _promotion = _human_state(eligible_user_ids=["reviewer"])
    predecessor.state = TeamRecordState.ACCEPTED
    predecessor.approved_commit_sha = predecessor.candidate_commit_sha
    predecessor.published_commit_sha = predecessor.candidate_commit_sha
    predecessor.published_at = datetime.now(tz=UTC)
    state.deliverables["source-deliverable"].artifact_refs = ["artifact:source"]
    state.deliverables["source-deliverable"].verification = DeliverableVerification(status="passed")

    report_result: dict[str, object] = {"events": []}
    case = report_post_release_feedback(
        state,
        release_package_id=predecessor.id,
        reporter_user_id="reporter",
        reporter_display_name="Reporter",
        report="The published alarm acknowledgement regressed.",
        evidence_refs=["artifact:alarm-regression"],
        request_id="post-release-report-1",
        result=report_result,
    )
    triage_task = state.tasks[str(report_result["triage_task_id"])]
    triage_task.state = TeamTaskState.FINISHED
    correction_task = AgentTask(
        id="post-release-correction-task",
        owner_agent_id="owner",
        project_id="project",
        case_id=case.id,
        priority=1,
        state=TeamTaskState.FINISHED,
        title="Correct the published alarm regression",
        description="Restore acknowledgement after a safe reset.",
        created_by_agent_id="lead",
        deliverable_ids=["post-release-correction"],
    )
    state.tasks[correction_task.id] = correction_task
    case.task_ids.append(correction_task.id)
    correction = Deliverable(
        id="post-release-correction",
        task_id=correction_task.id,
        producer_agent_id="owner",
        artifact_refs=["artifact:alarm-correction"],
        summary="Corrected the published alarm acknowledgement.",
        state=TeamRecordState.ACCEPTED,
        verification=DeliverableVerification(status="passed"),
    )
    state.deliverables[correction.id] = correction
    _mark_deliverable_as_reviewed_git_correction(
        state,
        deliverable=correction,
        commit_sha="c" * 40,
    )

    missing_authorization_result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }
    assert (
        _build_release_package(
            state,
            project=state.projects["project"],
            request={
                "id": "unauthorized-published-successor",
                "deliverable_ids": [correction.id],
                "supersedes_release_package_id": predecessor.id,
                "waiver_actor_kind": "operator",
                "waiver_actor_id": "admin",
            },
            result=missing_authorization_result,
            projects_to_recalc=set(),
            actor_agent_id="lead",
            team_id="team",
        )
        is None
    )
    assert "post_release_feedback_case_id" in str(missing_authorization_result["errors"])

    result: dict[str, object] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }
    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "published-successor",
            "deliverable_ids": [correction.id],
            "supersedes_release_package_id": predecessor.id,
            "post_release_feedback_case_id": case.id,
            "waiver_actor_kind": "operator",
            "waiver_actor_id": "admin",
        },
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id == "published-successor"
    successor = state.release_packages[release_id]
    assert successor.supersedes_release_package_id == predecessor.id
    assert successor.base_commit_sha == predecessor.candidate_commit_sha
    assert successor.deliverable_ids == [
        "source-deliverable",
        correction.id,
    ]
    assert result["post_release_feedback_case_id"] == case.id
    assert predecessor.state == TeamRecordState.ACCEPTED
    assert predecessor.published_commit_sha == predecessor.candidate_commit_sha


def test_post_release_feedback_task_uses_manifest_only_candidate_branch() -> None:
    state, release, _promotion = _human_state(eligible_user_ids=["reviewer"])
    assert release.manifest is not None
    candidate_branch = release.manifest.release_branch
    release.release_branch = None
    release.git_base_branch = "main"
    release.state = TeamRecordState.ACCEPTED
    release.approved_commit_sha = release.candidate_commit_sha
    release.published_commit_sha = release.candidate_commit_sha
    release.published_at = datetime.now(tz=UTC)
    result: dict[str, object] = {"events": []}

    case = report_post_release_feedback(
        state,
        release_package_id=release.id,
        reporter_user_id="reporter",
        reporter_display_name="Reporter",
        report="The published alarm acknowledgement regressed.",
        evidence_refs=["artifact:alarm-regression"],
        request_id="post-release-manifest-only-branch",
        result=result,
    )

    task = state.tasks[str(result["triage_task_id"])]
    assert task.git_base_branch == candidate_branch
    assert (
        _resolve_new_task_git_base_branch(
            state,
            project_id=release.project_id,
            explicit_git_base_branch="main",
            case_id=case.id,
        )
        == candidate_branch
    )


def test_post_release_feedback_rejects_accepted_unpublished_attempt() -> None:
    state, release, _promotion = _human_state(eligible_user_ids=["reviewer"])
    release.state = TeamRecordState.ACCEPTED

    with pytest.raises(
        ValueError,
        match="post-release feedback requires a published release",
    ):
        report_post_release_feedback(
            state,
            release_package_id=release.id,
            reporter_user_id="reporter",
            reporter_display_name="Reporter",
            report="This must not create a case before publication.",
            evidence_refs=["artifact:not-published"],
            request_id="not-published",
            result={"events": []},
        )


def test_post_release_feedback_requires_evidence_and_evidenced_case_closure() -> None:
    state, release, _promotion = _human_state(eligible_user_ids=["reviewer"])
    release.state = TeamRecordState.ACCEPTED
    release.approved_commit_sha = release.candidate_commit_sha
    release.published_commit_sha = release.candidate_commit_sha
    release.published_at = datetime.now(tz=UTC)

    with pytest.raises(
        ValueError,
        match="post-release feedback requires evidence",
    ):
        report_post_release_feedback(
            state,
            release_package_id=release.id,
            reporter_user_id="reporter",
            reporter_display_name="Reporter",
            report="A published behavior regressed.",
            evidence_refs=[],
            request_id="post-release-no-evidence",
            result={"events": []},
        )

    report_result: dict[str, object] = {"events": []}
    case = report_post_release_feedback(
        state,
        release_package_id=release.id,
        reporter_user_id="reporter",
        reporter_display_name="Reporter",
        report="A published behavior regressed.",
        evidence_refs=["artifact:regression"],
        request_id="post-release-with-evidence",
        result=report_result,
    )
    original_published_at = release.published_at
    triage_task = state.tasks[str(report_result["triage_task_id"])]

    with pytest.raises(ValueError, match="must be resolved"):
        close_post_release_feedback_case(
            state,
            case_id=case.id,
            reason="Verified as corrected.",
            evidence_refs=["test:recovery"],
            actor_kind="operator",
            actor_id="admin",
            result={},
        )

    triage_task.state = TeamTaskState.FINISHED
    with pytest.raises(
        ValueError,
        match="post-release case closure requires evidence",
    ):
        close_post_release_feedback_case(
            state,
            case_id=case.id,
            reason="Verified as corrected.",
            evidence_refs=[],
            actor_kind="operator",
            actor_id="admin",
            result={},
        )

    closed = close_post_release_feedback_case(
        state,
        case_id=case.id,
        reason="Verified as corrected.",
        evidence_refs=["test:recovery"],
        actor_kind="operator",
        actor_id="admin",
        result={},
    )

    assert closed.status.value == "closed"
    assert closed.metadata["evidence_refs"] == ["artifact:regression"]
    assert closed.metadata["post_release_feedback_resolution"]["evidence_refs"] == ["test:recovery"]
    assert release.state == TeamRecordState.ACCEPTED
    assert release.published_at == original_published_at


def test_release_wide_human_correction_keeps_common_source_author() -> None:
    state, release, promotion = _human_state(eligible_user_ids=["reviewer"])
    author = ProjectGitIdentity(name="Delegating User", email="user@example.test")
    state.tasks["source-task"].authored_by = author
    attribution = [AdoWorkItemAttribution(organization="org", work_item_id="123", assignee_id="alice")]
    state.tasks["source-task"].work_item_attributions = attribution
    advance_release_gates(
        state, release=release, release_promotion=promotion, result={"events": []}
    )
    request = next(iter(state.human_review_requests.values()))
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="reviewer",
        reviewer_display_name="Reviewer",
        decision="changes_requested",
        request_id="release-wide-author",
        expected_revision=request.revision,
        checklist_results={"smoke": False},
        summary="Correct the release behavior.",
        findings=[{"text": "Correct release-wide behavior.", "target_scope": "release_candidate"}],
        result={"events": []},
    )
    finding = next(iter(state.human_review_findings.values()))
    assert state.tasks[finding.correction_task_id].authored_by == author
    assert state.tasks[finding.correction_task_id].work_item_attributions == attribution
