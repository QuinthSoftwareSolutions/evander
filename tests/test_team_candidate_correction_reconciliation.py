from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import evander_team_addon.services.candidate_correction_reconciliation_service as reconciliation_module
from evander_team_addon.models import (
    AgentTask,
    AgentRuntime,
    Deliverable,
    DeliverableGitRefs,
    DeliverableReviewManifest,
    DeliverableVerification,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ReleaseCandidateManifest,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseVerificationRun,
    ReviewRequest,
    Team,
    TeamBlockerState,
    TeamDispatch,
    TeamDispatchStatus,
    TeamInboxItemState,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.candidate_correction_reconciliation_service import (
    CandidateCorrectionReconciliationService,
)
from evander_team_addon.services.commands import TeamReviewCommandService


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"
CANDIDATE_COMMIT = "c" * 40
CORRECTION_COMMIT = "e" * 40


def _candidate_correction_state(
    *,
    gate_kind: str,
    reviewed: bool = True,
) -> TeamState:
    state = TeamState.empty()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="lead",
        project_ids=["project"],
    )
    for agent_id in ("lead", "coder", "reviewer"):
        role_id = f"role-{agent_id}"
        state.roles[role_id] = TeamRole(
            id=role_id,
            team_id="team",
            project_id="project",
            name=agent_id.title(),
        )
        state.agents[agent_id] = PersistentAgent(
            id=agent_id,
            team_id="team",
            role_id=role_id,
            session_id=f"session-{agent_id}",
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

    predecessor = ReleasePackage(
        id="release-predecessor",
        project_id="project",
        owner_agent_id="lead",
        release_branch="team/team/releases/release-predecessor",
        candidate_commit_sha=CANDIDATE_COMMIT,
        candidate_tree_hash="d" * 40,
        manifest=ReleaseCandidateManifest(
            project_id="project",
            release_package_id="release-predecessor",
            candidate_commit_sha=CANDIDATE_COMMIT,
            candidate_tree_hash="d" * 40,
            release_branch="team/team/releases/release-predecessor",
        ),
        state=TeamRecordState.REJECTED,
    )
    task_actor = (
        "system:release-verification-correction"
        if gate_kind == "verification"
        else "system:release-review-correction"
    )
    task = AgentTask(
        id="correction-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Correct the rejected release candidate",
        description="Fix the rejected release-gate finding.",
        created_by_actor=task_actor,
        git_base_branch=predecessor.release_branch,
        deliverable_ids=["correction-deliverable"],
    )
    deliverable = Deliverable(
        id="correction-deliverable",
        task_id=task.id,
        producer_agent_id="coder",
        summary="Corrected the rejected release candidate.",
        git_refs=DeliverableGitRefs(
            agent_branch="agents/session-coder/correction",
            commit_sha=CORRECTION_COMMIT,
            submitted_commit_sha=CORRECTION_COMMIT,
        ),
        review_manifest=DeliverableReviewManifest(
            base_commit_sha=CANDIDATE_COMMIT,
            reviewed_commit_sha=CORRECTION_COMMIT,
            tree_hash="f" * 40,
            commit_shas=[CORRECTION_COMMIT],
            changed_paths=["M\tsrc/Correction.cs"],
        ),
        verification=DeliverableVerification(status="passed"),
        state=TeamRecordState.ACCEPTED,
    )
    if reviewed:
        correction_review = ReviewRequest(
            id="correction-review",
            deliverable_id=deliverable.id,
            reviewer_agent_id="reviewer",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha=CORRECTION_COMMIT,
        )
        deliverable.review_request_ids = [correction_review.id]
        state.review_requests[correction_review.id] = correction_review

    if gate_kind == "verification":
        predecessor.gate_failure = ReleaseGateFailure(
            stage="verification",
            kind="candidate",
            source_id="verification-run",
            message="Candidate verification failed.",
        )
        verification_run = ReleaseVerificationRun(
            id="verification-run",
            release_package_id=predecessor.id,
            candidate_commit_sha=CANDIDATE_COMMIT,
            session_id="verification-session",
            state="failed",
            failure_kind="candidate",
            correction_task_id=task.id,
        )
        predecessor.verification_run_ids = [verification_run.id]
        state.release_verification_runs[verification_run.id] = verification_run
    else:
        predecessor.gate_failure = ReleaseGateFailure(
            stage="review",
            kind="rejected",
            source_id="release-review",
            message="Release review rejected the candidate.",
        )
        predecessor.review_correction_task_id = task.id
        state.review_requests["release-review"] = ReviewRequest(
            id="release-review",
            release_package_id=predecessor.id,
            candidate_commit_sha=CANDIDATE_COMMIT,
            reviewed_commit_sha=CANDIDATE_COMMIT,
            reviewer_agent_id="reviewer",
            state=TeamRecordState.REJECTED,
            findings=["Correct the rejected candidate."],
        )

    state.release_packages[predecessor.id] = predecessor
    state.tasks[task.id] = task
    state.deliverables[deliverable.id] = deliverable
    return state


@pytest.mark.parametrize("gate_kind", ["verification", "review"])
def test_resolved_generated_candidate_correction_creates_one_successor(
    tmp_path: Path,
    gate_kind: str,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind=gate_kind)
    repository.update(lambda _state: seeded, expected_revision=0)

    first = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    successor_ids = [
        release.id
        for release in state.release_packages.values()
        if release.supersedes_release_package_id == "release-predecessor"
    ]

    assert first["changed"] is True
    assert successor_ids == first["created_successor_release_package_ids"]
    successor = state.release_packages[successor_ids[0]]
    assert successor.deliverable_ids == ["correction-deliverable"]
    assert len(successor.git_promotion_record_ids) == 1
    promotion = state.git_promotions[successor.git_promotion_record_ids[0]]
    assert promotion.promotion_kind == "correction_aggregate"
    assert promotion.base_commit_sha == CANDIDATE_COMMIT
    notice = next(
        item
        for item in state.inbox.values()
        if item.payload.get("kind") == "candidate_correction_resolution"
    )
    assert notice.state == TeamInboxItemState.HANDLED
    assert notice.response_requested is False
    assert notice.payload["successor_release_package_id"] == successor.id
    assert len(repository.list_outbox_records()) == 1

    replay = CandidateCorrectionReconciliationService(repository).reconcile()
    replayed_state = repository.read()

    assert replay["changed"] is False
    assert len(replayed_state.release_packages) == 2
    assert len(repository.list_outbox_records()) == 1


def test_invalid_generated_candidate_correction_opens_one_durable_blocker(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review", reviewed=False)
    repository.update(lambda _state: seeded, expected_revision=0)

    first = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()

    assert first["changed"] is True
    assert not any(
        release.supersedes_release_package_id == "release-predecessor"
        for release in state.release_packages.values()
    )
    blocker = next(iter(state.blockers.values()))
    assert blocker.state == TeamBlockerState.OPEN
    assert blocker.reason_code == "candidate_correction_successor_failed"
    assert blocker.owner_agent_id == "coder"
    assert "no accepted review" in blocker.details
    outbox_count = len(repository.list_outbox_records())
    assert outbox_count == 1

    replay = CandidateCorrectionReconciliationService(repository).reconcile()

    assert replay["changed"] is False
    assert len(repository.read().blockers) == 1
    assert len(repository.list_outbox_records()) == outbox_count


def test_accepting_generated_correction_review_creates_successor_atomically(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review", reviewed=False)
    task = seeded.tasks["correction-task"]
    task.state = TeamTaskState.WAITING_FOR_REVIEW
    deliverable = seeded.deliverables["correction-deliverable"]
    deliverable.state = TeamRecordState.PENDING
    correction_review = ReviewRequest(
        id="correction-review",
        deliverable_id=deliverable.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.PENDING,
        reviewed_commit_sha=CORRECTION_COMMIT,
    )
    deliverable.review_request_ids = [correction_review.id]
    seeded.review_requests[correction_review.id] = correction_review
    now = datetime.now(tz=UTC)
    seeded.dispatches["dispatch-review"] = TeamDispatch(
        id="dispatch-review",
        agent_id="reviewer",
        session_id="session-reviewer",
        cause_kind="inbox",
        review_request_ids=[correction_review.id],
        project_ids=["project"],
        managed_repo_ids=[MANAGED_REPO_ID],
        status=TeamDispatchStatus.LEASED,
        leased_at=now,
        lease_expires_at=now + timedelta(minutes=15),
    )
    seeded.agent_runtimes["reviewer"] = AgentRuntime(
        agent_id="reviewer",
        active_dispatch_id="dispatch-review",
        wake_leased_until=now + timedelta(minutes=15),
        wake_reason="inbox",
    )
    repository.update(lambda _state: seeded, expected_revision=0)

    state, result = TeamReviewCommandService(repository).decide_review_command(
        session_id="session-reviewer",
        review_request_id=correction_review.id,
        state="accepted",
        request_id="accept-candidate-correction",
    )

    assert result["success"] is True
    assert state.review_requests[correction_review.id].state == TeamRecordState.ACCEPTED
    assert state.tasks[task.id].state == TeamTaskState.FINISHED
    assert len(result["created_successor_release_package_ids"]) == 1
    successor_id = result["created_successor_release_package_ids"][0]
    assert state.release_packages[successor_id].supersedes_release_package_id == (
        "release-predecessor"
    )
    notice = next(
        item
        for item in state.inbox.values()
        if item.payload.get("kind") == "candidate_correction_resolution"
    )
    assert notice.state == TeamInboxItemState.HANDLED
    assert notice.response_requested is False
    assert notice.payload["successor_release_package_id"] == successor_id
    assert not any(
        event["event_type"] == "team.agent.wake_requested"
        for event in result["events"]
    )
    assert any(
        record.intent_type == "team.git.promotion_requested"
        for record in repository.list_outbox_records()
    )


def test_unexpected_successor_packaging_failure_becomes_durable_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="verification")
    repository.update(lambda _state: seeded, expected_revision=0)

    def _raise_packaging_failure(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic packaging failure")

    monkeypatch.setattr(
        reconciliation_module,
        "_build_release_package",
        _raise_packaging_failure,
    )

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()

    assert result["changed"] is True
    assert len(state.release_packages) == 1
    blocker = next(iter(state.blockers.values()))
    assert blocker.reason_code == "candidate_correction_successor_failed"
    assert "RuntimeError: synthetic packaging failure" in blocker.details
