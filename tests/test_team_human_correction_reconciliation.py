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
    GitPromotionRecord,
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
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamCase,
    TeamCaseStatus,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
    TaskCancellation,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.domain.blockers import resolve_blocker
from evander_team_addon.services.domain.human_reviews import (
    HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY,
    route_human_finding_correction,
)
from evander_team_addon.services.domain.git_promotion import (
    _queue_canonical_push_after_release_success,
)
from evander_team_addon.services.domain.release_packages import (
    _apply_git_release_recovery,
)
from evander_team_addon.services.human_correction_reconciliation_service import (
    HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY,
    HumanCorrectionReconciliationService,
)
from evander_team_addon.services.git_release_service import TeamGitReleaseService


NOW = datetime(2026, 7, 30, tzinfo=UTC)
CANONICAL_BASE_COMMIT = "0" * 40
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
        base_commit_sha=CANONICAL_BASE_COMMIT,
        canonical_base_commit_sha=CANONICAL_BASE_COMMIT,
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
    assert successor.git_base_branch == "team/team/releases/release-rejected"
    assert successor.base_commit_sha == PREDECESSOR_COMMIT
    assert successor.canonical_base_commit_sha == CANONICAL_BASE_COMMIT
    assert successor.canonical_branch == "main"
    assert set(successor.deliverable_ids) == {
        "source-deliverable",
        "correction-deliverable",
    }
    assert state.tasks["human-correction-aggregate"].state == TeamTaskState.FINISHED
    assert state.cases["human-case"].status == TeamCaseStatus.RESOLVED
    assert first["resolved_case_ids"] == ["human-case"]
    assert len(successor.git_promotion_record_ids) == 1
    aggregate = state.git_promotions[successor.git_promotion_record_ids[0]]
    assert aggregate.promotion_kind == "correction_aggregate"
    assert aggregate.source_branch == ("team/team/features/human-correction-child-owner")
    assert aggregate.target_branch == f"team/team/corrections/{successor.id}"
    assert [(source.source_branch, source.expected_commit_sha) for source in aggregate.sources] == [
        ("team/team/features/human-correction-child-owner", CORRECTION_COMMIT)
    ]
    aggregate_event = next(
        event for event in first["events"] if event["payload"].get("promotion_id") == aggregate.id
    )
    assert aggregate_event["payload"]["base_commit_sha"] == PREDECESSOR_COMMIT
    assert aggregate_event["payload"]["required_source_ancestor_commit_sha"] == (PREDECESSOR_COMMIT)
    assert "allowed_changed_paths" not in aggregate_event["payload"]

    second = HumanCorrectionReconciliationService(repo).reconcile()
    assert second["changed"] is False
    assert second["revision"] == state.revision
    assert (
        len(
            [
                release
                for release in repo.read().release_packages.values()
                if release.supersedes_release_package_id == "release-rejected"
            ]
        )
        == 1
    )


def test_reconciliation_self_heals_completed_ambiguous_finding(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _retain_stale_ambiguity(state: TeamState) -> TeamState:
        state.cases["human-case"].metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] = ["finding"]
        return state

    repo.update(_retain_stale_ambiguity)
    result = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()

    assert result["implicitly_routed_human_review_finding_ids"] == ["finding"]
    assert result["created_successor_release_package_ids"]
    assert state.cases["human-case"].metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] == []
    assert state.tasks["human-correction-aggregate"].state == (TeamTaskState.FINISHED)
    assert state.cases["human-case"].status == TeamCaseStatus.RESOLVED


def test_reconciliation_keeps_unresolved_ambiguous_finding_routed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _restore_unresolved_ambiguity(state: TeamState) -> TeamState:
        state.cases["human-case"].metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] = ["finding"]
        state.tasks["human-correction-child"].state = TeamTaskState.IN_PROGRESS
        return state

    repo.update(_restore_unresolved_ambiguity)
    result = HumanCorrectionReconciliationService(repo).reconcile()

    assert result["changed"] is True
    assert result["migrated_coordination_task_ids"] == ["human-correction-aggregate"]
    final = repo.read()
    assert final.tasks["human-correction-aggregate"].task_kind == TeamTaskKind.COORDINATION
    assert final.cases["human-case"].metadata[HUMAN_CORRECTION_AMBIGUOUS_FINDING_IDS_KEY] == [
        "finding"
    ]
    assert not any(
        release.supersedes_release_package_id == "release-rejected"
        for release in final.release_packages.values()
    )
    replay = HumanCorrectionReconciliationService(repo).reconcile()
    assert replay["changed"] is False
    assert replay["revision"] == final.revision


def test_reconciliation_upgrades_existing_system_correction_parent_to_coordination(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _leave_correction_in_flight(state: TeamState) -> TeamState:
        state.tasks["human-correction-child"].state = TeamTaskState.IN_PROGRESS
        state.deliverables["correction-deliverable"].state = TeamRecordState.PENDING
        return state

    initial = repo.update(_leave_correction_in_flight)
    assert initial.tasks["human-correction-aggregate"].task_kind == TeamTaskKind.SUPPORT

    migrated = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()

    assert migrated["changed"] is True
    assert migrated["migrated_coordination_task_ids"] == ["human-correction-aggregate"]
    assert state.tasks["human-correction-aggregate"].task_kind == TeamTaskKind.COORDINATION
    assert state.tasks["human-correction-aggregate"].deliverable_ids == []
    assert state.cases["human-case"].metadata["human_correction_graph_version"] == 2
    assert not any(
        release.supersedes_release_package_id == "release-rejected"
        for release in state.release_packages.values()
    )

    replay = HumanCorrectionReconciliationService(repo).reconcile()
    assert replay["changed"] is False
    assert replay["revision"] == state.revision


def test_reconciliation_carries_manifest_only_candidate_branch_into_successor(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _retain_candidate_branch_only_in_manifest(state: TeamState) -> TeamState:
        predecessor = state.release_packages["release-rejected"]
        assert predecessor.manifest is not None
        candidate_branch = predecessor.manifest.release_branch
        predecessor.release_branch = None
        predecessor.git_base_branch = "main"
        state.tasks["human-correction-aggregate"].git_base_branch = candidate_branch
        state.tasks["human-correction-child"].git_base_branch = candidate_branch
        return state

    repo.update(_retain_candidate_branch_only_in_manifest)
    predecessor = repo.read().release_packages["release-rejected"]
    assert predecessor.manifest is not None
    candidate_branch = predecessor.manifest.release_branch
    assert predecessor.release_branch is None

    result = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()

    successor_id = result["created_successor_release_package_ids"][0]
    successor = state.release_packages[successor_id]
    assert successor.supersedes_release_package_id == predecessor.id
    assert successor.git_base_branch == candidate_branch
    assert successor.base_commit_sha == PREDECESSOR_COMMIT


def test_deduplicated_non_successor_release_keeps_correction_case_active(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _seed_active_package(state: TeamState) -> TeamState:
        state.release_packages["release-existing"] = ReleasePackage(
            id="release-existing",
            project_id="project",
            deliverable_ids=[
                "source-deliverable",
                "correction-deliverable",
            ],
            state=TeamRecordState.PENDING,
        )
        return state

    repo.update(_seed_active_package)

    first = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()

    assert first["waiting_case_ids"] == ["human-case"]
    assert first["deduplicated_release_package_ids"] == ["release-existing"]
    assert "created_successor_release_package_ids" not in first
    assert state.cases["human-case"].status == TeamCaseStatus.IN_PROGRESS
    assert state.tasks["human-correction-aggregate"].state == TeamTaskState.PENDING
    assert not any(
        release.supersedes_release_package_id == "release-rejected"
        for release in state.release_packages.values()
    )

    revision = state.revision
    replay = HumanCorrectionReconciliationService(repo).reconcile()
    assert replay["changed"] is False
    assert replay["revision"] == revision
    assert repo.read().cases["human-case"].status == TeamCaseStatus.IN_PROGRESS


def test_correction_aggregate_success_keeps_rejected_candidate_workstream(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = HumanCorrectionReconciliationService(repo).reconcile()
    successor_id = first["created_successor_release_package_ids"][0]
    state = repo.read()
    aggregate_id = state.release_packages[successor_id].git_promotion_record_ids[0]

    state, result = TeamGitReleaseService(repo).record_git_promotion_result(
        promotion_id=aggregate_id,
        success=True,
        commit_sha="e" * 40,
        outputs={
            "base_commit_sha": PREDECESSOR_COMMIT,
            "source_manifests": {
                "team/team/features/human-correction-child-owner": {
                    "base_commit_sha": PREDECESSOR_COMMIT,
                    "reviewed_commit_sha": CORRECTION_COMMIT,
                    "tree_hash": "d" * 40,
                    "commit_shas": [CORRECTION_COMMIT],
                    "changed_paths": ["M\tsrc/correction.cs"],
                }
            },
            "tree_hash": "f" * 40,
        },
    )

    successor = state.release_packages[successor_id]
    release_promotions = [
        state.git_promotions[promotion_id]
        for promotion_id in successor.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "release"
    ]
    assert len(release_promotions) == 1
    release_promotion = release_promotions[0]
    assert release_promotion.source_branch == f"team/team/corrections/{successor_id}"
    assert release_promotion.sources[0].expected_commit_sha == "e" * 40
    release_event = next(
        event
        for event in result["events"]
        if event["payload"].get("promotion_id") == release_promotion.id
    )
    assert release_promotion.git_base_branch == ("team/team/releases/release-rejected")
    assert release_promotion.base_commit_sha == PREDECESSOR_COMMIT
    assert release_event["payload"]["base_branch"] == ("team/team/releases/release-rejected")
    assert release_event["payload"]["base_commit_sha"] == PREDECESSOR_COMMIT
    assert release_event["payload"]["allowed_changed_paths"] == ["M\tsrc/correction.cs"]
    assert (
        state.deliverables["correction-deliverable"].review_manifest.base_commit_sha
        == PREDECESSOR_COMMIT
    )

    successor.candidate_commit_sha = "e" * 40
    successor.approved_commit_sha = "e" * 40
    assert successor.scope_validation is not None
    successor.scope_validation.state = "passed"
    release_promotion.commit_sha = "e" * 40
    publication = {"events": [], "git_promotion_event_ids": []}
    _queue_canonical_push_after_release_success(
        state,
        release=successor,
        release_promotion=release_promotion,
        result=publication,
    )
    canonical = next(
        state.git_promotions[promotion_id]
        for promotion_id in state.release_packages[successor_id].git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "canonical_push"
    )
    canonical_event = next(
        event
        for event in publication["events"]
        if event["payload"].get("promotion_id") == canonical.id
    )
    assert canonical.base_commit_sha == CANONICAL_BASE_COMMIT
    assert canonical_event["payload"]["base_commit_sha"] == CANONICAL_BASE_COMMIT


def test_correction_aggregate_preserves_non_descendant_source_error(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = HumanCorrectionReconciliationService(repo).reconcile()
    successor_id = first["created_successor_release_package_ids"][0]
    state = repo.read()
    aggregate_id = state.release_packages[successor_id].git_promotion_record_ids[0]
    ancestry_error = (
        "One or more correction sources do not descend from the required rejected candidate"
    )

    state, result = TeamGitReleaseService(repo).record_git_promotion_result(
        promotion_id=aggregate_id,
        success=False,
        error=ancestry_error,
        outputs={
            "error_code": "scope_mismatch",
            "unexpected_commit_shas": [CORRECTION_COMMIT],
            "source_manifests": {},
        },
    )

    assert state.git_promotions[aggregate_id].error == ancestry_error
    assert state.release_packages[successor_id].error == ancestry_error
    assert result["error"] == ancestry_error


def test_reconciliation_migrates_candidate_less_legacy_leaf_promotions_once(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = HumanCorrectionReconciliationService(repo).reconcile()
    successor_id = first["created_successor_release_package_ids"][0]

    def _seed_legacy_failure(state: TeamState) -> TeamState:
        successor = state.release_packages[successor_id]
        aggregate_id = successor.git_promotion_record_ids[0]
        state.git_promotions.pop(aggregate_id)
        legacy_ids = ["legacy-feature-one", "legacy-feature-two"]
        successor.git_promotion_record_ids = legacy_ids
        successor.state = TeamRecordState.BLOCKED
        successor.error = "legacy leaf conflicts"
        for index, promotion_id in enumerate(legacy_ids):
            state.git_promotions[promotion_id] = GitPromotionRecord(
                id=promotion_id,
                project_id="project",
                release_package_id=successor.id,
                task_id="human-correction-child",
                actor_agent_id="lead",
                source_branch=f"agents/owner/tasks/legacy-{index}",
                target_branch=f"team/team/features/legacy-{index}",
                promotion_kind="feature",
                state="failed",
                error="legacy merge conflict",
                outputs={"error_code": "merge_conflict"},
            )
        state.deliverables["correction-deliverable"].review_manifest = DeliverableReviewManifest(
            base_commit_sha="9" * 40,
            reviewed_commit_sha=CORRECTION_COMMIT,
            changed_paths=["M\tbroad/untrusted.cs"],
        )
        recovery_task = AgentTask(
            id="legacy-recovery-task",
            owner_agent_id="owner",
            project_id="project",
            priority=0,
            state=TeamTaskState.PENDING,
            title="Legacy recovery",
            description="Recover each leaf.",
            created_by_actor="system:git-merge-recovery",
        )
        state.tasks[recovery_task.id] = recovery_task
        state.blockers["legacy-release-blocker"] = TeamBlocker(
            id="legacy-release-blocker",
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
            source_id=successor.id,
            owner_agent_id="owner",
            affected_task_ids=[recovery_task.id],
            summary="Legacy release blocker",
        )
        return state

    repo.update(_seed_legacy_failure)
    migrated = HumanCorrectionReconciliationService(repo).reconcile()
    state = repo.read()
    successor = state.release_packages[successor_id]

    assert migrated["migrated_successor_release_package_ids"] == [successor_id]
    assert migrated["retired_legacy_promotion_ids"] == [
        "legacy-feature-one",
        "legacy-feature-two",
    ]
    assert all(
        state.git_promotions[promotion_id].state == "retired"
        for promotion_id in migrated["retired_legacy_promotion_ids"]
    )
    aggregate_rows = [
        state.git_promotions[promotion_id]
        for promotion_id in successor.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "correction_aggregate"
    ]
    assert len(aggregate_rows) == 1
    assert successor.state == TeamRecordState.PENDING
    assert state.tasks["legacy-recovery-task"].state == TeamTaskState.CANCELLED
    assert state.blockers["legacy-release-blocker"].state.value == "resolved"
    aggregate_event = next(
        event
        for event in migrated["events"]
        if event["payload"].get("promotion_id") == aggregate_rows[0].id
    )
    assert aggregate_event["payload"]["base_commit_sha"] == PREDECESSOR_COMMIT
    assert "allowed_changed_paths" not in aggregate_event["payload"]
    assert state.deliverables["correction-deliverable"].review_manifest.base_commit_sha == "9" * 40
    state, late_legacy_result = TeamGitReleaseService(repo).record_git_promotion_result(
        promotion_id="legacy-feature-one",
        success=True,
        commit_sha="6" * 40,
    )
    assert late_legacy_result["promotion_state"] == "retired"
    assert late_legacy_result["events"] == []
    assert state.git_promotions["legacy-feature-one"].state == "retired"

    aggregate = aggregate_rows[0]
    state, failed = TeamGitReleaseService(repo).record_git_promotion_result(
        promotion_id=aggregate.id,
        success=False,
        error="corrections conflict with one another",
        outputs={
            "error_code": "merge_conflict",
            "base_commit_sha": PREDECESSOR_COMMIT,
            "conflict_branch": aggregate.source_branch,
            "source_manifests": {
                aggregate.source_branch: {
                    "base_commit_sha": PREDECESSOR_COMMIT,
                    "reviewed_commit_sha": CORRECTION_COMMIT,
                    "tree_hash": "d" * 40,
                    "commit_shas": [CORRECTION_COMMIT],
                    "changed_paths": ["M\tsrc/correction.cs"],
                }
            },
        },
    )
    assert (
        state.deliverables["correction-deliverable"].review_manifest.base_commit_sha
        == PREDECESSOR_COMMIT
    )
    assert failed["merge_recovery_task_id"] != "legacy-recovery-task"
    assert state.tasks[failed["merge_recovery_task_id"]].state == TeamTaskState.PENDING
    assert "correction aggregation" in state.tasks[failed["merge_recovery_task_id"]].title
    recovery_result = {
        "errors": [],
        "events": [],
        "updated_deliverable_ids": [],
    }
    recovered_id = _apply_git_release_recovery(
        state,
        actor=state.agents["owner"],
        request={
            "release_package_id": successor_id,
            "agent_branch": "agents/owner/tasks/resolved-aggregate",
            "commit_sha": "8" * 40,
        },
        result=recovery_result,
    )
    assert recovered_id == successor_id
    assert recovery_result["errors"] == []
    recovery_payload = recovery_result["events"][0]["payload"]
    assert recovery_payload["promotion_id"] == aggregate.id
    assert recovery_payload["promotion_kind"] == "correction_aggregate"
    assert recovery_payload["base_commit_sha"] == PREDECESSOR_COMMIT
    assert recovery_payload["required_source_ancestor_commit_sha"] == (PREDECESSOR_COMMIT)
    assert recovery_payload["allowed_changed_paths"] == ["M\tsrc/correction.cs"]
    state, recovered = TeamGitReleaseService(repo).record_git_promotion_result(
        promotion_id=aggregate.id,
        success=True,
        commit_sha="8" * 40,
        outputs={
            "base_commit_sha": PREDECESSOR_COMMIT,
            "source_manifests": {
                "agents/owner/tasks/resolved-aggregate": {
                    "base_commit_sha": PREDECESSOR_COMMIT,
                    "reviewed_commit_sha": "8" * 40,
                    "tree_hash": "7" * 40,
                    "commit_shas": ["8" * 40],
                    "changed_paths": ["M\tsrc/correction.cs"],
                }
            },
            "tree_hash": "7" * 40,
        },
    )
    assert recovered["promotion_state"] == "succeeded"
    assert state.tasks[failed["merge_recovery_task_id"]].state == TeamTaskState.FINISHED
    assert (
        len(
            [
                promotion_id
                for promotion_id in state.release_packages[successor_id].git_promotion_record_ids
                if state.git_promotions[promotion_id].promotion_kind == "release"
            ]
        )
        == 1
    )

    replay = HumanCorrectionReconciliationService(repo).reconcile()
    assert replay["changed"] is False


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
    assert (
        len(
            [
                release
                for release in state.release_packages.values()
                if release.supersedes_release_package_id == "release-rejected"
            ]
        )
        == 1
    )


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
    assert final.tasks["human-correction-aggregate"].state == (TeamTaskState.FINISHED)
    assert [
        release.id
        for release in final.release_packages.values()
        if release.supersedes_release_package_id == "release-rejected"
    ] == [successor_id]


def test_existing_successor_resolves_one_stale_active_case_once(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    service = HumanCorrectionReconciliationService(repo)
    first = service.reconcile()
    successor_id = first["created_successor_release_package_ids"][0]

    def _restore_stale_case(state: TeamState) -> TeamState:
        state.cases["human-case"].status = TeamCaseStatus.IN_PROGRESS
        return state

    repo.update(_restore_stale_case)

    repaired = service.reconcile()
    state = repo.read()

    assert repaired["changed"] is True
    assert repaired["resolved_case_ids"] == ["human-case"]
    assert repaired["existing_successor_release_package_ids"] == [successor_id]
    assert state.cases["human-case"].status == TeamCaseStatus.RESOLVED

    replay = service.reconcile()
    assert replay["changed"] is False
    assert replay["revision"] == state.revision


def test_reconciliation_preserves_authored_resolution_and_unions_evidence(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    def _author_resolution(state: TeamState) -> TeamState:
        finding = state.human_review_findings["finding"]
        finding.proposed_resolution = "Operator-authored explanation of the verified correction."
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
    parent_id = final.cases["human-case"].metadata[HUMAN_CORRECTION_AGGREGATION_TASK_METADATA_KEY]
    assert final.tasks[parent_id].state == TeamTaskState.FINISHED
