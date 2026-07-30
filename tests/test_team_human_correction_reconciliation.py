from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evander_team_addon.models import (
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DeliverableReviewManifest,
    DeliverableVerification,
    HumanReviewFinding,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ReleaseCandidateDeliverableEvidence,
    ReleaseCandidateManifest,
    ReleaseGateFailure,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamBlockerSourceKind,
    TeamCase,
    TeamCaseStatus,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    TaskCancellation,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.domain.blockers import resolve_blocker
from evander_team_addon.services.domain.human_reviews import (
    HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY,
    route_human_finding_correction,
)
from evander_team_addon.services.human_correction_reconciliation_service import (
    HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY,
    HumanCorrectionReconciliationService,
)


NOW = datetime(2026, 7, 30, tzinfo=UTC)
PREDECESSOR_COMMIT = "a" * 40
PREDECESSOR_TREE = "b" * 40
CORRECTION_COMMIT = "c" * 40


def _reconciliation_state(*, include_parent: bool = True) -> TeamState:
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
            managed_repo_id="mr_0123456789abcdefabcd",
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
        title="Original candidate",
        description="Original candidate contribution.",
        created_by_agent_id="lead",
        deliverable_ids=["source-deliverable"],
    )
    state.deliverables["source-deliverable"] = Deliverable(
        id="source-deliverable",
        task_id="source-task",
        producer_agent_id="owner",
        summary="Original candidate",
        state=TeamRecordState.ACCEPTED,
        verification=DeliverableVerification(status="passed"),
    )
    manifest = ReleaseCandidateManifest(
        project_id="project",
        release_package_id="release-rejected",
        candidate_commit_sha=PREDECESSOR_COMMIT,
        candidate_tree_hash=PREDECESSOR_TREE,
        release_branch="team/team/releases/release-rejected",
        deliverables=[
            ReleaseCandidateDeliverableEvidence(
                deliverable_id="source-deliverable",
                verification=DeliverableVerification(status="passed"),
            )
        ],
        human_review_requirement=HumanReviewRequirementSnapshot(
            directory_path="root",
            configured_group_ids=["human-reviewers"],
            initially_eligible_user_ids=["human@example.test"],
        ),
    )
    state.release_packages["release-rejected"] = ReleasePackage(
        id="release-rejected",
        project_id="project",
        deliverable_ids=["source-deliverable"],
        release_branch=manifest.release_branch,
        canonical_branch="main",
        candidate_commit_sha=PREDECESSOR_COMMIT,
        candidate_tree_hash=PREDECESSOR_TREE,
        manifest=manifest,
        state=TeamRecordState.REJECTED,
        gate_failure=ReleaseGateFailure(
            stage="human_review",
            kind="changes_requested",
            source_id="human-review",
            message="Changes requested.",
        ),
    )
    state.human_review_requests["human-review"] = HumanReviewRequest(
        id="human-review",
        release_package_id="release-rejected",
        candidate_commit_sha=PREDECESSOR_COMMIT,
        candidate_tree_hash=PREDECESSOR_TREE,
        requirement_snapshot=HumanReviewRequirementSnapshot(
            directory_path="root",
            configured_group_ids=["human-reviewers"],
            initially_eligible_user_ids=["human@example.test"],
        ),
        state="changes_requested",
        completed_at=NOW,
    )

    parent_id = "human-correction-aggregate"
    task_ids = ["human-correction-child"]
    metadata = {}
    if include_parent:
        state.tasks[parent_id] = AgentTask(
            id=parent_id,
            owner_agent_id="lead",
            project_id="project",
            case_id="human-case",
            priority=0,
            state=TeamTaskState.PENDING,
            title="Aggregate corrections",
            description="Coordination only.",
            created_by_actor="system:human-review",
        )
        task_ids.insert(0, parent_id)
        metadata[HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY] = parent_id
    state.cases["human-case"] = TeamCase(
        id="human-case",
        team_id="team",
        project_id="project",
        title="Human corrections",
        status=TeamCaseStatus.IN_PROGRESS,
        owner_agent_id="lead",
        source="human_release_review",
        source_ref="human-review",
        metadata=metadata,
        task_ids=task_ids,
    )
    state.tasks["human-correction-child"] = AgentTask(
        id="human-correction-child",
        owner_agent_id="owner",
        project_id="project",
        case_id="human-case",
        parent_task_id=parent_id if include_parent else None,
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Correct the finding",
        description="Correct the finding exactly.",
        created_by_actor="system:human-review",
        deliverable_ids=["correction-deliverable"],
    )
    state.human_review_findings["finding"] = HumanReviewFinding(
        id="finding",
        source_human_review_request_id="human-review",
        text="Correct the finding exactly.",
        correction_task_id="human-correction-child",
        created_at=NOW,
    )
    state.deliverables["correction-deliverable"] = Deliverable(
        id="correction-deliverable",
        task_id="human-correction-child",
        producer_agent_id="owner",
        summary="The exact finding is corrected.",
        state=TeamRecordState.ACCEPTED,
        review_request_ids=["correction-review"],
        git_refs=DeliverableGitRefs(
            feature_branch="team/team/features/human-correction-child-owner",
            feature_commit_sha=CORRECTION_COMMIT,
            reviewed_commit_sha=CORRECTION_COMMIT,
        ),
        review_manifest=DeliverableReviewManifest(
            base_commit_sha=PREDECESSOR_COMMIT,
            reviewed_commit_sha=CORRECTION_COMMIT,
            tree_hash="d" * 40,
            commit_shas=[CORRECTION_COMMIT],
            changed_paths=["src/correction.cs"],
        ),
        verification=DeliverableVerification(
            status="passed",
            evidence_refs=["test:correction"],
        ),
    )
    state.review_requests["correction-review"] = ReviewRequest(
        id="correction-review",
        deliverable_id="correction-deliverable",
        reviewer_agent_id="lead",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=CORRECTION_COMMIT,
    )
    return state


def _repo(tmp_path: Path, *, include_parent: bool = True) -> TeamStateRepository:
    repo = TeamStateRepository(tmp_path / "runtime")
    repo.update(lambda _state: _reconciliation_state(include_parent=include_parent))
    return repo


def test_reconciliation_derives_resolution_and_creates_one_successor(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    first = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()

    finding = state.human_review_findings["finding"]
    assert finding.proposed_resolution == "The exact finding is corrected."
    assert finding.proposed_by_deliverable_ids == ["correction-deliverable"]
    assert finding.resolution_evidence_refs == [
        CORRECTION_COMMIT,
        "correction-deliverable",
        "test:correction",
    ]
    assert first["created_successor_release_package_ids"]
    successor_id = first["created_successor_release_package_ids"][0]
    successor = state.release_packages[successor_id]
    assert successor.supersedes_release_package_id == "release-rejected"
    assert set(successor.deliverable_ids) == {
        "source-deliverable",
        "correction-deliverable",
    }
    assert state.tasks["human-correction-aggregate"].state == TeamTaskState.FINISHED

    second = HumanCorrectionReconciliationService(repo).reconcile()
    assert second["changed"] is False
    assert second["revision"] == state.revision
    assert len(
        [
            release
            for release in repo.read().release_packages.values()
            if release.supersedes_release_package_id == "release-rejected"
        ]
    ) == 1


def test_reconciliation_preserves_cancelled_coordination_parent(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _cancel_parent(state: TeamState) -> TeamState:
        parent = state.tasks["human-correction-aggregate"]
        parent.state = TeamTaskState.CANCELLED
        parent.cancellation = TaskCancellation(
            reason_code="operator_cancelled",
            reason="The coordination task is no longer needed.",
            actor_kind="operator",
            actor_id="operator:lead",
            cancelled_at=NOW,
        )
        parent.updated_at = NOW
        parent.state_entered_at = NOW
        return state

    repo.update(_cancel_parent)
    result = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()
    parent = state.tasks["human-correction-aggregate"]

    assert result["created_successor_release_package_ids"]
    assert parent.state == TeamTaskState.CANCELLED
    assert parent.cancellation is not None
    assert parent.cancellation.reason_code == "operator_cancelled"
    assert parent.cancellation.actor_id == "operator:lead"

    revision = state.revision
    replay = HumanCorrectionReconciliationService(repo).reconcile()
    assert replay["changed"] is False
    assert replay["revision"] == revision


def test_reconciliation_converts_one_active_legacy_case_once(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, include_parent=False)

    result = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()
    case = state.cases["human-case"]
    parent_id = case.metadata[HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY]

    assert result["created_coordination_task_ids"] == [parent_id]
    assert result["reparented_correction_task_ids"] == ["human-correction-child"]
    assert state.tasks["human-correction-child"].parent_task_id == parent_id
    assert state.tasks[parent_id].state == TeamTaskState.FINISHED
    assert len(
        [
            release
            for release in state.release_packages.values()
            if release.supersedes_release_package_id == "release-rejected"
        ]
    ) == 1


@pytest.mark.parametrize(
    "successor_state",
    [TeamRecordState.PENDING, TeamRecordState.REJECTED],
)
def test_existing_successor_permanently_terminates_reconciliation(
    tmp_path: Path,
    successor_state: TeamRecordState,
) -> None:
    repo = _repo(tmp_path)
    service = HumanCorrectionReconciliationService(repo)
    first = service.reconcile()
    successor_id = first["created_successor_release_package_ids"][0]

    def _mutate_after_packaging(state: TeamState) -> TeamState:
        state.release_packages[successor_id].state = successor_state
        late = Deliverable(
            id="late-correction-deliverable",
            task_id="human-correction-child",
            producer_agent_id="owner",
            summary="A late correction that must not reopen the packaged case.",
            state=TeamRecordState.ACCEPTED,
            verification=DeliverableVerification(status="passed"),
        )
        state.deliverables[late.id] = late
        state.tasks[late.task_id].deliverable_ids.append(late.id)
        return state

    state = repo.update(_mutate_after_packaging)
    revision = state.revision

    second = service.reconcile()
    final = repo.read()

    assert second["changed"] is False
    assert second["revision"] == revision
    assert final.blockers == {}
    assert final.tasks["human-correction-aggregate"].state == (
        TeamTaskState.FINISHED
    )
    assert [
        release.id
        for release in final.release_packages.values()
        if release.supersedes_release_package_id == "release-rejected"
    ] == [successor_id]


def test_reconciliation_preserves_authored_resolution_and_unions_evidence(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _author_resolution(state: TeamState) -> TeamState:
        finding = state.human_review_findings["finding"]
        finding.proposed_resolution = (
            "Operator-authored explanation of the verified correction."
        )
        finding.proposed_by_deliverable_ids = ["correction-deliverable"]
        finding.resolution_evidence_refs = ["manual:operator-reproduction"]
        return state

    repo.update(_author_resolution)
    result = HumanCorrectionReconciliationService(repo).reconcile()
    finding = repo.read().human_review_findings["finding"]

    assert result["created_successor_release_package_ids"]
    assert finding.proposed_resolution == (
        "Operator-authored explanation of the verified correction."
    )
    assert finding.proposed_by_deliverable_ids == ["correction-deliverable"]
    assert finding.resolution_evidence_refs == [
        CORRECTION_COMMIT,
        "correction-deliverable",
        "manual:operator-reproduction",
        "test:correction",
    ]


def test_missing_ambiguous_aggregation_parent_opens_recoverable_explicit_blocker(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _break_parent(state: TeamState) -> TeamState:
        case = state.cases["human-case"]
        case.metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] = ["finding"]
        case.task_ids.remove("human-correction-aggregate")
        state.tasks["human-correction-child"].parent_task_id = None
        del state.tasks["human-correction-aggregate"]
        return state

    repo.update(_break_parent)
    first = HumanCorrectionReconciliationService(repo).reconcile()
    blocked = repo.read()

    assert first["changed"] is True
    assert len(blocked.blockers) == 1
    blocker = next(iter(blocked.blockers.values()))
    assert blocker.source_kind == TeamBlockerSourceKind.EXPLICIT
    assert blocker.reason_code == "human_correction_aggregation_failed"
    assert "missing correction aggregation task" in blocker.details

    def _repair_and_resolve(state: TeamState) -> TeamState:
        case = state.cases["human-case"]
        case.metadata.pop(HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY)
        route_human_finding_correction(
            state,
            finding_id="finding",
            owner_agent_id="owner",
            result={"events": []},
        )
        current_blocker = state.blockers[blocker.id]
        resolve_blocker(
            state,
            blocker=current_blocker,
            actor_agent_id=None,
            dispatch_id=None,
            note="The aggregation metadata and routing were repaired.",
            reference_ids=["human-case"],
        )
        return state

    repo.update(_repair_and_resolve)
    recovered = HumanCorrectionReconciliationService(repo).reconcile()
    final = repo.read()

    assert recovered["created_successor_release_package_ids"]
    assert final.blockers[blocker.id].state.value == "resolved"
    parent_id = final.cases["human-case"].metadata[
        HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY
    ]
    assert final.tasks[parent_id].state == TeamTaskState.FINISHED
