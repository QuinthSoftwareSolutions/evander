from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

import evander_team_addon.services.candidate_correction_reconciliation_service as reconciliation_module
from evander_team_addon.models import (
    AgentProjectAssignment,
    AgentTask,
    AgentRuntime,
    DependencyCompositionManifest,
    DependencyGitSource,
    Deliverable,
    DeliverableGitRefs,
    DeliverableReviewManifest,
    DeliverableVerification,
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
    ReviewRequest,
    ReviewProfile,
    ReviewRepositorySnapshot,
    Team,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerState,
    TeamBlockerResolutionScope,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamDispatch,
    TeamDispatchStatus,
    TeamInboxItemState,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    TaskCancellation,
    TaskDeliverableContract,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.candidate_correction_reconciliation_service import (
    CandidateCorrectionReconciliationService,
)
from evander_team_addon.services.commands import TeamReviewCommandService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.review import _cascade_review_decision
from evander_team_addon.services.domain.release_packages import (
    _build_release_package,
    _release_lineage_roots_for_inputs,
)
from evander_team_addon.services.domain.task_assessment import assess_task
from evander_team_addon.services.git_release_service import TeamGitReleaseService
from evander_team_addon.services.release_gate_service import TeamReleaseGateService


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"
CANDIDATE_COMMIT = "c" * 40
CORRECTION_COMMIT = "e" * 40
RECOVERY_COMMIT = "a" * 40


def _independent_remediation(
    state: TeamState,
    *,
    suffix: str = "",
    commit_sha: str = RECOVERY_COMMIT,
) -> Deliverable:
    task_id = f"independent-remediation-task{suffix}"
    deliverable_id = f"independent-remediation-deliverable{suffix}"
    review_id = f"independent-remediation-review{suffix}"
    task = AgentTask(
        id=task_id,
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Repair a second failure on the rejected candidate",
        description="Independent remediation rooted at the exact release candidate.",
        created_by_agent_id="lead",
        git_base_branch="team/team/releases/release-predecessor",
        git_base_commit_sha=CANDIDATE_COMMIT,
        deliverable_ids=[deliverable_id],
    )
    deliverable = Deliverable(
        id=deliverable_id,
        task_id=task.id,
        producer_agent_id="coder",
        summary="Repaired the additional candidate failure.",
        git_refs=DeliverableGitRefs(
            agent_branch=f"agents/session-coder/independent-remediation{suffix}",
            commit_sha=commit_sha,
            submitted_commit_sha=commit_sha,
        ),
        review_manifest=DeliverableReviewManifest(
            base_commit_sha=CANDIDATE_COMMIT,
            reviewed_commit_sha=commit_sha,
            tree_hash="b" * 40,
            commit_shas=[commit_sha],
            changed_paths=["M\tsrc/IndependentRemediation.cs"],
        ),
        verification=DeliverableVerification(status="passed"),
        review_request_ids=[review_id],
        state=TeamRecordState.ACCEPTED,
    )
    state.tasks[task.id] = task
    state.deliverables[deliverable.id] = deliverable
    state.review_requests[review_id] = ReviewRequest(
        id=review_id,
        deliverable_id=deliverable.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=commit_sha,
    )
    return deliverable


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
        description=("Fix the rejected release-gate finding. Release reviews: release-review"),
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
        predecessor.review_request_ids = ["release-review"]
        state.review_requests["release-review"] = ReviewRequest(
            id="release-review",
            release_package_id=predecessor.id,
            candidate_commit_sha=CANDIDATE_COMMIT,
            reviewed_commit_sha=CANDIDATE_COMMIT,
            reviewer_agent_id="reviewer",
            state=TeamRecordState.REJECTED,
            findings=["Correct the rejected candidate."],
            created_at=datetime.now(tz=UTC) - timedelta(minutes=2),
            decision_at=datetime.now(tz=UTC) - timedelta(minutes=1),
        )

    state.release_packages[predecessor.id] = predecessor
    state.tasks[task.id] = task
    state.deliverables[deliverable.id] = deliverable
    return state


def _accepted_composed_candidate_correction_state() -> TeamState:
    state = _candidate_correction_state(gate_kind="review")
    task = state.tasks["correction-task"]
    source_task = AgentTask(
        id="correction-source-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Correction source",
        description="Accepted child correction retained by the aggregate.",
        created_by_agent_id="coder",
        originating_task_id=task.id,
        git_base_branch=task.git_base_branch,
        deliverable_ids=["correction-source-deliverable"],
    )
    source_deliverable = Deliverable(
        id="correction-source-deliverable",
        task_id=source_task.id,
        producer_agent_id="coder",
        summary="Accepted child correction source.",
        state=TeamRecordState.ACCEPTED,
        git_refs=DeliverableGitRefs(
            agent_branch="agents/session-coder/correction-source",
            commit_sha=CORRECTION_COMMIT,
            submitted_commit_sha=CORRECTION_COMMIT,
            reviewed_commit_sha=CORRECTION_COMMIT,
            accepted_pinned_ref="refs/evander/team-reviews/correction-source",
        ),
        verification=DeliverableVerification(status="passed"),
    )
    state.tasks[source_task.id] = source_task
    state.deliverables[source_deliverable.id] = source_deliverable
    task.integration_boundary = True
    task.dependency_task_ids = [source_task.id]
    task.deliverable_contract = TaskDeliverableContract(
        require_git_provenance=True,
        required_contribution_task_ids=[source_task.id],
    )
    deliverable = state.deliverables["correction-deliverable"]
    deliverable.source_deliverable_ids = [source_deliverable.id]
    deliverable.git_refs.branch_context_id = "correction-context"
    dependency_source = DependencyGitSource(
        dependency_task_id=source_task.id,
        deliverable_id=source_deliverable.id,
        branch=source_deliverable.git_refs.agent_branch or "",
        commit_sha=CORRECTION_COMMIT,
        relationship_kind="required_contribution",
        pinned_ref=source_deliverable.git_refs.accepted_pinned_ref,
    )
    manifest = DependencyCompositionManifest(
        target_task_id=task.id,
        managed_repo_id=MANAGED_REPO_ID,
        base_branch=task.git_base_branch or "main",
        base_commit_sha=CANDIDATE_COMMIT,
        base_tree_hash="d" * 40,
        dependency_git_sources=[dependency_source],
        source_fingerprint="1" * 64,
        frontier_commit_shas=[CORRECTION_COMMIT],
        composed_commit_sha=CORRECTION_COMMIT,
        composed_tree_hash="f" * 40,
        verified_candidate_commit_sha=CORRECTION_COMMIT,
    )
    deliverable.dependency_composition_manifest = manifest
    state.git_work_contexts["correction-context"] = GitWorkContext(
        id="correction-context",
        agent_id="coder",
        task_id=task.id,
        agent_branch=deliverable.git_refs.agent_branch or "",
        base_branch=task.git_base_branch,
        base_commit=CORRECTION_COMMIT,
        commit_sha=CORRECTION_COMMIT,
        dependency_git_sources=[dependency_source],
        dependency_composition_manifest=manifest.model_copy(
            update={"verified_candidate_commit_sha": None}
        ),
        state=GitWorkContextState.SUBMITTED,
    )
    return state


def test_independent_exact_base_remediation_auto_joins_release_lineage() -> None:
    state = _candidate_correction_state(gate_kind="review")
    remediation = _independent_remediation(state)
    result: dict[str, object] = {"errors": [], "events": [], "git_promotion_event_ids": []}

    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "release-independent-remediation",
            "deliverable_ids": [remediation.id],
        },
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id == "release-independent-remediation"
    assert result["release_lineage_auto_joined"] is True
    assert result["inferred_predecessor_release_package_id"] == "release-predecessor"
    assert state.tasks["independent-remediation-task"].release_base_package_id == (
        "release-predecessor"
    )
    successor = state.release_packages[release_id]
    assert successor.supersedes_release_package_id == "release-predecessor"
    assert successor.lineage_root_release_package_id == "release-predecessor"
    assert successor.lineage_sequence == 1


def _command_blocked_source_correction_state(*, producer_base: bool = False) -> TeamState:
    state = _candidate_correction_state(gate_kind="verification")
    predecessor = state.release_packages["release-predecessor"]
    command = ReleaseVerificationCommand(
        name="Unit tests", argv=["python3", "-m", "pytest"], command_failure_action="block"
    )
    predecessor.state = TeamRecordState.BLOCKED
    predecessor.gate_failure.kind = "blocked"
    predecessor.manifest.verification_commands = [command]
    run = state.release_verification_runs["verification-run"]
    run.failure_kind = "command"
    run.correction_task_id = None
    run.command_results = [
        ReleaseVerificationCommandResult(
            name="Unit tests", state="failed", failure_kind="command", exit_code=1
        )
    ]
    state.projects["project"].release_policy = ProjectReleasePolicy(
        verification_commands=[command],
        review_requirements=[
            ReleaseReviewRequirement(
                reviewer_agent_id="reviewer", review_profile_id="release-review"
            )
        ],
    )
    state.review_profiles["release-review"] = ReviewProfile(
        id="release-review", team_id="team", project_id="project", name="Release review"
    )
    state.blockers["blocked-verification"] = TeamBlocker(
        id="blocked-verification",
        team_id="team",
        project_id="project",
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=predecessor.id,
        owner_agent_id="lead",
        summary="Unit-test verification failed",
    )
    remediation = _independent_remediation(state)
    if producer_base:
        original = state.deliverables["correction-deliverable"]
        original.git_refs.commit_sha = CANDIDATE_COMMIT
        original.git_refs.submitted_commit_sha = CANDIDATE_COMMIT
        original.review_manifest.reviewed_commit_sha = CANDIDATE_COMMIT
        state.review_requests["correction-review"].reviewed_commit_sha = CANDIDATE_COMMIT
        predecessor.deliverable_ids = [original.id]
        predecessor.manifest.deliverables = [
            ReleaseCandidateDeliverableEvidence(
                deliverable_id=original.id,
                review_manifest=original.review_manifest.model_copy(deep=True),
            )
        ]
        state.tasks[remediation.task_id].git_base_branch = original.git_refs.agent_branch
    return state


@pytest.mark.parametrize("producer_base", [False, True])
def test_command_blocked_source_correction_preserves_lineage_and_reruns_gates(
    tmp_path: Path,
    producer_base: bool,
) -> None:
    seeded = _command_blocked_source_correction_state(producer_base=producer_base)
    seeded.deliverables["independent-remediation-deliverable"].review_manifest = None
    repository = TeamStateRepository(tmp_path)
    before = repository.update(lambda _state: seeded, expected_revision=0)
    original_run = before.release_verification_runs["verification-run"].model_copy(deep=True)
    admin = TeamAdminService(repository)

    state, created = admin.create_release_package_with_result(
        project_id="project",
        deliverable_ids=["independent-remediation-deliverable"],
        release_id="release-source-correction",
        expected_revision=before.revision,
    )
    successor = state.release_packages[created["release_package_ids"][0]]
    assert successor.supersedes_release_package_id == "release-predecessor"
    assert successor.lineage_root_release_package_id == "release-predecessor"
    assert successor.base_commit_sha == CANDIDATE_COMMIT
    assert successor.verification_waivers == []
    promotion = state.git_promotions[successor.git_promotion_record_ids[0]]
    assert [state.git_promotions[i].promotion_kind for i in successor.git_promotion_record_ids] == [
        "correction_aggregate"
    ]
    assert promotion.base_commit_sha == CANDIDATE_COMMIT
    assert promotion.sources[0].expected_commit_sha == RECOVERY_COMMIT
    assert state.blockers["blocked-verification"].state == TeamBlockerState.OPEN

    outbox_before_retry = repository.list_outbox_records()
    with pytest.raises(ValueError, match="already has successor release package"):
        admin.retry_release_gate_with_result(
            release_package_id="release-predecessor",
            gate_kind="verification",
            gate_id="verification-run",
            reason="Retry after creating the reviewed correction successor.",
            operator_actor_id="operator:test",
            expected_revision=state.revision,
        )
    assert TeamStateRepository(tmp_path).read() == state
    assert repository.list_outbox_records() == outbox_before_retry

    source = state.deliverables["independent-remediation-deliverable"]
    state, _ = TeamGitReleaseService(repository).record_git_promotion_result(
        promotion_id=promotion.id,
        commit_sha=RECOVERY_COMMIT,
        outputs={
            "base_commit_sha": CANDIDATE_COMMIT,
            "tree_hash": "b" * 40,
            "source_manifests": {
                source.git_refs.agent_branch: {
                    "base_commit_sha": CANDIDATE_COMMIT,
                    "reviewed_commit_sha": RECOVERY_COMMIT,
                    "tree_hash": "b" * 40,
                    "commit_shas": [RECOVERY_COMMIT],
                    "changed_paths": ["M\tsrc/IndependentRemediation.cs"],
                }
            },
        },
        expected_revision=state.revision,
    )
    successor = state.release_packages[successor.id]
    release_promotion = next(
        state.git_promotions[i]
        for i in successor.git_promotion_record_ids
        if state.git_promotions[i].promotion_kind == "release"
    )
    state, _ = TeamGitReleaseService(repository).record_git_promotion_result(
        promotion_id=release_promotion.id,
        commit_sha=RECOVERY_COMMIT,
        outputs={"base_commit_sha": CANDIDATE_COMMIT, "tree_hash": "b" * 40},
        expected_revision=state.revision,
    )
    reloaded = TeamStateRepository(tmp_path).read()
    successor = reloaded.release_packages[successor.id]
    assert successor.candidate_commit_sha == RECOVERY_COMMIT
    assert successor.manifest.verification_commands[0].command_failure_action == "block"
    assert len(successor.verification_run_ids) == 1
    fresh_run = reloaded.release_verification_runs[successor.verification_run_ids[0]]
    assert fresh_run.state == "pending"
    assert fresh_run.candidate_commit_sha == RECOVERY_COMMIT
    assert [c.state for c in fresh_run.command_results] == ["pending"]
    assert successor.review_request_ids == []
    state, _ = TeamReleaseGateService(repository).record_verification_result(
        release_package_id=successor.id,
        verification_run_id=fresh_run.id,
        candidate_commit_sha=RECOVERY_COMMIT,
        verification_state="passed",
        command_results=[{"name": "Unit tests", "state": "passed", "exit_code": 0}],
    )
    successor = state.release_packages[successor.id]
    assert len(successor.review_request_ids) == 1
    assert state.review_requests[successor.review_request_ids[0]].state == TeamRecordState.PENDING
    assert reloaded.release_verification_runs["verification-run"] == original_run
    assert reloaded.blockers["blocked-verification"].state == TeamBlockerState.OPEN
    assert reloaded.release_packages["release-predecessor"].state == TeamRecordState.BLOCKED


@pytest.mark.parametrize(
    "invalid",
    [
        "infrastructure",
        "retry-in-progress",
        "policy",
        "unreviewed",
        "unchanged",
        "base",
        "producer",
    ],
)
def test_command_blocked_successor_rejects_invalid_source_or_failure(
    tmp_path: Path,
    invalid: str,
) -> None:
    state = _command_blocked_source_correction_state()
    run = state.release_verification_runs["verification-run"]
    correction = state.deliverables["independent-remediation-deliverable"]
    predecessor = state.release_packages["release-predecessor"]
    if invalid == "infrastructure":
        run.failure_kind = "infrastructure"
    elif invalid == "policy":
        predecessor.manifest.verification_commands[0].command_failure_action = "correction"
    elif invalid == "unreviewed":
        state.review_requests["independent-remediation-review"].state = TeamRecordState.PENDING
    elif invalid == "unchanged":
        correction.review_manifest.tree_hash = predecessor.candidate_tree_hash
    elif invalid == "base":
        correction.review_manifest.base_commit_sha = "f" * 40
    elif invalid == "producer":
        state.tasks[correction.task_id].git_base_branch = "agents/other/unrelated"
    repository = TeamStateRepository(tmp_path)
    before = repository.update(lambda _state: state, expected_revision=0)
    if invalid == "retry-in-progress":
        before, _ = TeamAdminService(repository).retry_release_gate_with_result(
            release_package_id=predecessor.id,
            gate_kind="verification",
            gate_id=run.id,
            reason="The operator restored the environment prerequisite.",
            operator_actor_id="operator:test",
            expected_revision=before.revision,
        )

    with pytest.raises(ValueError):
        TeamAdminService(repository).create_release_package_with_result(
            project_id="project",
            deliverable_ids=[correction.id],
            release_id="invalid-successor",
            supersedes_release_package_id=predecessor.id,
            expected_revision=before.revision,
        )

    assert repository.read() == before


def test_source_deliverable_evidence_does_not_imply_correction_lineage() -> None:
    state = _candidate_correction_state(gate_kind="review")
    predecessor = state.release_packages["release-predecessor"]
    predecessor.deliverable_ids = ["correction-deliverable"]
    integration = _independent_remediation(state)
    integration.source_deliverable_ids = ["correction-deliverable"]
    integration_task = state.tasks[integration.task_id]
    integration_task.git_base_branch = "main"
    integration_task.git_base_commit_sha = "b" * 40
    integration.review_manifest = integration.review_manifest.model_copy(
        update={"base_commit_sha": "b" * 40}
    )

    roots = _release_lineage_roots_for_inputs(
        state,
        project_id="project",
        deliverable_ids=[integration.id],
    )

    assert roots == set()
    assert integration.source_deliverable_ids == ["correction-deliverable"]


def test_settled_lineage_wait_work_can_form_single_correction_successor() -> None:
    state = _candidate_correction_state(gate_kind="review")
    predecessor = state.release_packages["release-predecessor"]
    predecessor.gate_failure = ReleaseGateFailure(
        stage="lineage",
        kind="convergence",
        message="release_lineage_waiting_tasks: wait for candidate-bound work.",
    )
    remediation = _independent_remediation(state)
    result: dict[str, object] = {"errors": [], "events": [], "git_promotion_event_ids": []}

    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "release-after-lineage-wait",
            "deliverable_ids": [remediation.id],
        },
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id == "release-after-lineage-wait"
    successor = state.release_packages[release_id]
    assert successor.supersedes_release_package_id == predecessor.id
    assert successor.lineage_root_release_package_id == predecessor.id
    promotions = [
        state.git_promotions[promotion_id] for promotion_id in successor.git_promotion_record_ids
    ]
    assert [promotion.promotion_kind for promotion in promotions] == ["correction_aggregate"]


def test_settled_parallel_remediations_are_combined_into_one_successor() -> None:
    state = _candidate_correction_state(gate_kind="review")
    first = _independent_remediation(state)
    second = _independent_remediation(state, suffix="-second", commit_sha="b" * 40)
    result: dict[str, object] = {"errors": [], "events": [], "git_promotion_event_ids": []}

    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={"id": "release-combined-remediations", "deliverable_ids": [first.id]},
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id == "release-combined-remediations"
    assert result["correction_deliverable_ids"] == [first.id, second.id]
    successor = state.release_packages[release_id]
    assert successor.supersedes_release_package_id == "release-predecessor"
    assert successor.deliverable_ids == [first.id, second.id]
    assert len(
        [
            release
            for release in state.release_packages.values()
            if release.supersedes_release_package_id == "release-predecessor"
        ]
    ) == 1


def test_release_lineage_waits_for_other_exactly_bound_task() -> None:
    state = _candidate_correction_state(gate_kind="review")
    remediation = _independent_remediation(state)
    state.tasks["parallel-remediation-task"] = AgentTask(
        id="parallel-remediation-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.IN_PROGRESS,
        title="Parallel candidate repair",
        description="Another task explicitly bound to the same candidate.",
        created_by_agent_id="lead",
        release_base_package_id="release-predecessor",
        git_base_branch="team/team/releases/release-predecessor",
        git_base_commit_sha=CANDIDATE_COMMIT,
    )
    result: dict[str, object] = {"errors": [], "events": [], "git_promotion_event_ids": []}

    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={
            "id": "release-must-wait",
            "deliverable_ids": [remediation.id],
        },
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id is None
    assert result["error_code"] == "release_lineage_waiting_tasks"
    assert result["release_lineage_waiting_task_ids"] == ["parallel-remediation-task"]
    assert "release-must-wait" not in state.release_packages


def test_ambiguous_exact_candidate_provenance_does_not_auto_join() -> None:
    state = _candidate_correction_state(gate_kind="review")
    remediation = _independent_remediation(state)
    state.release_packages["release-historical-fork"] = ReleasePackage(
        id="release-historical-fork",
        project_id="project",
        release_branch="team/team/releases/release-predecessor",
        candidate_commit_sha=CANDIDATE_COMMIT,
        candidate_tree_hash="d" * 40,
        state=TeamRecordState.REJECTED,
    )
    result: dict[str, object] = {"errors": [], "events": [], "git_promotion_event_ids": []}

    release_id = _build_release_package(
        state,
        project=state.projects["project"],
        request={"id": "release-ambiguous", "deliverable_ids": [remediation.id]},
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="lead",
        team_id="team",
    )

    assert release_id is None
    assert result["error_code"] == "release_lineage_ambiguous"
    assert result["release_lineage_ambiguous_task_ids"] == ["independent-remediation-task"]
    assert "release-ambiguous" not in state.release_packages


def _legacy_reviewed_correction_state() -> TeamState:
    state = _candidate_correction_state(gate_kind="verification", reviewed=False)
    now = datetime(2026, 8, 24, 16, 40, tzinfo=UTC)
    task = state.tasks["correction-task"]
    task.state = TeamTaskState.WAITING_FOR_REVIEW
    task.state_entered_at = now - timedelta(minutes=5)
    task.integration_boundary = True
    task.dependency_task_ids = ["correction-source-task"]
    task.deliverable_contract = TaskDeliverableContract(
        require_git_provenance=True,
        required_contribution_task_ids=["correction-source-task"],
    )
    task.mutation_policy = None

    source_task = AgentTask(
        id="correction-source-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Legacy correction source wrapper",
        description="Historical child work retained by the migration.",
        created_by_agent_id="coder",
        originating_task_id=task.id,
        git_base_branch=state.release_packages["release-predecessor"].release_branch,
        deliverable_ids=["correction-source-deliverable"],
        created_at=now - timedelta(minutes=20),
        updated_at=now - timedelta(minutes=10),
        state_entered_at=now - timedelta(minutes=10),
    )
    source_deliverable = Deliverable(
        id="correction-source-deliverable",
        task_id=source_task.id,
        producer_agent_id="coder",
        summary="Accepted child correction source.",
        state=TeamRecordState.ACCEPTED,
        git_refs=DeliverableGitRefs(
            agent_branch="agents/session-coder/correction-source",
            commit_sha=CORRECTION_COMMIT,
            submitted_commit_sha=CORRECTION_COMMIT,
            reviewed_commit_sha=CORRECTION_COMMIT,
            accepted_pinned_ref="refs/evander/team-reviews/correction-source",
        ),
        verification=DeliverableVerification(status="passed"),
        created_at=now - timedelta(minutes=10),
    )
    state.tasks[source_task.id] = source_task
    state.deliverables[source_deliverable.id] = source_deliverable

    deliverable = state.deliverables["correction-deliverable"]
    deliverable.state = TeamRecordState.PENDING
    deliverable.acceptance_policy_version = 2
    deliverable.source_deliverable_ids = [source_deliverable.id]
    deliverable.review_cohort_id = "correction-review-cohort"
    deliverable.review_cohort_sealed_at = now - timedelta(minutes=4)
    deliverable.review_request_ids = ["correction-review-a", "correction-review-b"]
    deliverable.git_refs = DeliverableGitRefs(
        branch_context_id="correction-context",
        agent_branch="agents/session-coder/correction",
        commit_sha=CORRECTION_COMMIT,
        submitted_commit_sha=CORRECTION_COMMIT,
        reviewed_commit_sha=CORRECTION_COMMIT,
    )
    deliverable.review_manifest = DeliverableReviewManifest(
        base_commit_sha=CANDIDATE_COMMIT,
        reviewed_commit_sha=CORRECTION_COMMIT,
        tree_hash="f" * 40,
        commit_shas=[CORRECTION_COMMIT],
        changed_paths=["M\tsrc/Correction.cs"],
    )
    for index, reviewer_id in enumerate(("reviewer", "lead")):
        review_id = deliverable.review_request_ids[index]
        state.review_requests[review_id] = ReviewRequest(
            id=review_id,
            deliverable_id=deliverable.id,
            reviewer_agent_id=reviewer_id,
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha=CORRECTION_COMMIT,
            review_cohort_id=deliverable.review_cohort_id,
            repository_snapshot=ReviewRepositorySnapshot(
                managed_repo_id=MANAGED_REPO_ID,
                directory_path="root",
                commit_sha=CORRECTION_COMMIT,
                pinned_ref=f"refs/evander/team-reviews/{review_id}",
                source_kind="deliverable_candidate",
                source_ref="agents/session-coder/correction",
            ),
            created_at=now - timedelta(minutes=3 - index),
            decision_at=now - timedelta(minutes=1 - index),
        )

    dependency_source = DependencyGitSource(
        dependency_task_id=source_task.id,
        deliverable_id=source_deliverable.id,
        branch=source_deliverable.git_refs.agent_branch or "",
        commit_sha=CORRECTION_COMMIT,
        relationship_kind="required_contribution",
        pinned_ref=source_deliverable.git_refs.accepted_pinned_ref,
    )
    state.git_work_contexts["correction-context"] = GitWorkContext(
        id="correction-context",
        agent_id="coder",
        task_id=task.id,
        agent_branch="agents/session-coder/correction",
        base_branch=task.git_base_branch,
        base_commit=CANDIDATE_COMMIT,
        commit_sha=CORRECTION_COMMIT,
        dependency_git_sources=[dependency_source],
        dependency_composition_manifest=None,
        state=GitWorkContextState.SUBMITTED,
        created_at=now - timedelta(minutes=8),
        updated_at=now - timedelta(minutes=4),
    )
    return state


def _submit_reviewed_replacement(state: TeamState) -> TeamState:
    original = state.deliverables["correction-deliverable"]
    original.rework_requested_at = datetime.now(UTC)
    original.rework_requested_by_agent_id = "lead"
    original.rework_reason = "Replace the failed correction aggregate source."
    original.superseded_by_deliverable_id = "replacement-deliverable"
    replacement = Deliverable(
        id="replacement-deliverable",
        task_id="correction-task",
        producer_agent_id="coder",
        summary="Reviewed replacement correction.",
        supersedes_deliverable_id=original.id,
        git_refs=DeliverableGitRefs(
            agent_branch="agents/session-coder/replacement",
            commit_sha=RECOVERY_COMMIT,
            submitted_commit_sha=RECOVERY_COMMIT,
        ),
        verification=DeliverableVerification(status="passed"),
        state=TeamRecordState.ACCEPTED,
        review_request_ids=["replacement-review"],
    )
    state.deliverables[replacement.id] = replacement
    state.review_requests["replacement-review"] = ReviewRequest(
        id="replacement-review",
        deliverable_id=replacement.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=RECOVERY_COMMIT,
    )
    state.tasks["correction-task"].deliverable_ids.append(replacement.id)
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


def test_accepted_composed_correction_uses_immutable_candidate_root(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _accepted_composed_candidate_correction_state()
    preserved_context = seeded.git_work_contexts["correction-context"].model_copy(deep=True)
    repository.update(lambda _state: seeded, expected_revision=0)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    reconciled = repository.read()

    successor_ids = [
        release.id
        for release in reconciled.release_packages.values()
        if release.supersedes_release_package_id == "release-predecessor"
    ]
    assert successor_ids == result["created_successor_release_package_ids"]
    successor = reconciled.release_packages[successor_ids[0]]
    assert successor.deliverable_ids == ["correction-deliverable"]
    assert successor.base_commit_sha == CANDIDATE_COMMIT
    promotion = reconciled.git_promotions[successor.git_promotion_record_ids[0]]
    assert promotion.base_commit_sha == CANDIDATE_COMMIT
    assert promotion.sources[0].expected_commit_sha == CORRECTION_COMMIT
    assert reconciled.git_work_contexts["correction-context"] == preserved_context


@pytest.mark.parametrize(
    "mismatch",
    ["candidate_commit", "candidate_tree", "verified_commit", "context_manifest"],
)
def test_accepted_composed_correction_rejects_inconsistent_evidence(
    tmp_path: Path,
    mismatch: str,
) -> None:
    seeded = _accepted_composed_candidate_correction_state()
    deliverable = seeded.deliverables["correction-deliverable"]
    manifest = deliverable.dependency_composition_manifest
    assert manifest is not None
    context = seeded.git_work_contexts["correction-context"]
    if mismatch == "candidate_commit":
        manifest.base_commit_sha = RECOVERY_COMMIT
    elif mismatch == "candidate_tree":
        manifest.base_tree_hash = "b" * 40
    elif mismatch == "verified_commit":
        manifest.verified_candidate_commit_sha = RECOVERY_COMMIT
    else:
        assert context.dependency_composition_manifest is not None
        context.dependency_composition_manifest.composed_tree_hash = "b" * 40

    repository = TeamStateRepository(tmp_path)
    repository.update(lambda _state: seeded, expected_revision=0)
    result = CandidateCorrectionReconciliationService(repository).reconcile()
    after = repository.read()

    assert result.get("created_successor_release_package_ids", []) == []
    assert result["candidate_correction_errors"] == {
        "release-predecessor": (
            "correction deliverable correction-deliverable base commit "
            f"{CORRECTION_COMMIT} does not match rejected candidate {CANDIDATE_COMMIT}"
        )
    }
    assert not any(
        release.supersedes_release_package_id == "release-predecessor"
        for release in after.release_packages.values()
    )


def test_legacy_reviewed_correction_normalizes_and_creates_successor_atomically(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _legacy_reviewed_correction_state()
    preserved_source_task = seeded.tasks["correction-source-task"].model_copy(deep=True)
    preserved_source_deliverable = seeded.deliverables["correction-source-deliverable"].model_copy(
        deep=True
    )
    preserved_reviews = {
        review_id: seeded.review_requests[review_id].model_copy(deep=True)
        for review_id in ("correction-review-a", "correction-review-b")
    }
    preserved_context = seeded.git_work_contexts["correction-context"].model_copy(deep=True)
    state = repository.update(lambda _state: seeded, expected_revision=0)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    normalized = repository.read()

    assert result["changed"] is True
    assert normalized.revision == state.revision + 1
    assert result["normalized_legacy_candidate_correction_task_ids"] == ["correction-task"]
    audit = result["legacy_candidate_correction_normalizations"]["correction-task"]
    assert audit == {
        "correction_deliverable_id": "correction-deliverable",
        "source_task_id": "correction-source-task",
        "source_deliverable_id": "correction-source-deliverable",
        "git_work_context_id": "correction-context",
        "reviewed_commit_sha": CORRECTION_COMMIT,
    }
    task = normalized.tasks["correction-task"]
    deliverable = normalized.deliverables["correction-deliverable"]
    assert task.state == TeamTaskState.FINISHED
    assert task.dependency_task_ids == []
    assert task.integration_boundary is False
    assert task.deliverable_contract == TaskDeliverableContract(require_git_provenance=True)
    assert task.mutation_policy is not None
    assert task.mutation_policy.policy_id == "system-remediation-topology-v1"
    assert deliverable.state == TeamRecordState.ACCEPTED
    assert deliverable.source_deliverable_ids == []
    assert deliverable.git_refs.accepted_pinned_ref in {
        "refs/evander/team-reviews/correction-review-a",
        "refs/evander/team-reviews/correction-review-b",
    }
    successor_ids = [
        release.id
        for release in normalized.release_packages.values()
        if release.supersedes_release_package_id == "release-predecessor"
    ]
    assert successor_ids == result["created_successor_release_package_ids"]
    assert normalized.release_packages[successor_ids[0]].deliverable_ids == [deliverable.id]
    assert normalized.tasks["correction-source-task"] == preserved_source_task
    assert normalized.deliverables["correction-source-deliverable"] == preserved_source_deliverable
    assert {
        review_id: normalized.review_requests[review_id] for review_id in preserved_reviews
    } == preserved_reviews
    assert normalized.git_work_contexts["correction-context"] == preserved_context


def test_legacy_reviewed_correction_migration_is_idempotent(tmp_path: Path) -> None:
    repository = TeamStateRepository(tmp_path)
    repository.update(
        lambda _state: _legacy_reviewed_correction_state(),
        expected_revision=0,
    )
    first = CandidateCorrectionReconciliationService(repository).reconcile()
    first_state = repository.read()

    replay = CandidateCorrectionReconciliationService(repository).reconcile()
    replayed_state = repository.read()

    assert first["normalized_legacy_candidate_correction_task_ids"] == ["correction-task"]
    assert replay["changed"] is False
    assert replayed_state == first_state
    assert (
        len(
            [
                release
                for release in replayed_state.release_packages.values()
                if release.supersedes_release_package_id == "release-predecessor"
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "near_miss",
    [
        "unaccepted_review",
        "differing_source_commit",
        "composition_manifest_present",
        "non_system_task",
        "multiple_ambiguous_sources",
        "mismatched_context_source",
    ],
)
def test_legacy_correction_migration_leaves_near_misses_unchanged(
    tmp_path: Path,
    near_miss: str,
) -> None:
    seeded = _legacy_reviewed_correction_state()
    task = seeded.tasks["correction-task"]
    deliverable = seeded.deliverables["correction-deliverable"]
    source_deliverable = seeded.deliverables["correction-source-deliverable"]
    if near_miss == "unaccepted_review":
        seeded.review_requests["correction-review-b"].state = TeamRecordState.PENDING
        seeded.review_requests["correction-review-b"].decision_at = None
    elif near_miss == "differing_source_commit":
        source_deliverable.git_refs.submitted_commit_sha = RECOVERY_COMMIT
        source_deliverable.git_refs.reviewed_commit_sha = RECOVERY_COMMIT
    elif near_miss == "composition_manifest_present":
        dependency_source = seeded.git_work_contexts["correction-context"].dependency_git_sources[0]
        deliverable.dependency_composition_manifest = DependencyCompositionManifest(
            target_task_id=task.id,
            managed_repo_id=MANAGED_REPO_ID,
            base_branch=task.git_base_branch or "main",
            base_commit_sha=CANDIDATE_COMMIT,
            base_tree_hash="d" * 40,
            dependency_git_sources=[dependency_source],
            source_fingerprint="1" * 64,
            frontier_commit_shas=[CORRECTION_COMMIT],
            reduced_source_deliverable_ids=[source_deliverable.id],
            composed_commit_sha=CORRECTION_COMMIT,
            composed_tree_hash="f" * 40,
            verified_candidate_commit_sha=CORRECTION_COMMIT,
        )
    elif near_miss == "non_system_task":
        task.created_by_actor = "operator:legacy-repair"
    elif near_miss == "multiple_ambiguous_sources":
        second_source = source_deliverable.model_copy(
            deep=True,
            update={"id": "correction-source-deliverable-2"},
        )
        seeded.deliverables[second_source.id] = second_source
        seeded.tasks["correction-source-task"].deliverable_ids.append(second_source.id)
        deliverable.source_deliverable_ids.append(second_source.id)
    else:
        seeded.git_work_contexts["correction-context"].dependency_git_sources[
            0
        ].deliverable_id = deliverable.id

    repository = TeamStateRepository(tmp_path)
    before = repository.update(lambda _state: seeded, expected_revision=0)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    after = repository.read()

    assert result["changed"] is False
    assert after == before
    assert after.tasks["correction-task"].integration_boundary is True
    assert after.tasks["correction-task"].dependency_task_ids == ["correction-source-task"]
    assert after.deliverables["correction-deliverable"].state == TeamRecordState.PENDING
    assert not any(
        release.supersedes_release_package_id == "release-predecessor"
        for release in after.release_packages.values()
    )


def test_reviewed_rework_rebuilds_failed_pre_candidate_successor(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review")
    repository.update(lambda _state: seeded, expected_revision=0)
    created = CandidateCorrectionReconciliationService(repository).reconcile()
    successor_id = created["created_successor_release_package_ids"][0]
    state = repository.read()
    successor = state.release_packages[successor_id]
    failed_promotion_id = successor.git_promotion_record_ids[0]
    TeamGitReleaseService(repository).record_git_promotion_result(
        promotion_id=failed_promotion_id,
        success=False,
        error=(
            "One or more correction sources do not descend from the required "
            "rejected candidate"
        ),
        outputs={
            "error_code": "scope_mismatch",
            "unexpected_commit_shas": [CORRECTION_COMMIT],
            "source_manifests": {},
        },
    )

    def _submit_reviewed_replacement(state: TeamState) -> TeamState:
        original = state.deliverables["correction-deliverable"]
        original.rework_requested_at = datetime.now(UTC)
        original.rework_requested_by_agent_id = "lead"
        original.rework_reason = (
            "The correction commit rewrote the immutable rejected candidate."
        )
        original.superseded_by_deliverable_id = "replacement-deliverable"
        replacement = Deliverable(
            id="replacement-deliverable",
            task_id="correction-task",
            producer_agent_id="coder",
            summary="Replaced the malformed correction commit.",
            supersedes_deliverable_id=original.id,
            git_refs=DeliverableGitRefs(
                agent_branch="agents/session-coder/correction",
                commit_sha=RECOVERY_COMMIT,
                submitted_commit_sha=RECOVERY_COMMIT,
            ),
            verification=DeliverableVerification(status="passed"),
            state=TeamRecordState.ACCEPTED,
            review_request_ids=["replacement-review"],
        )
        state.deliverables[replacement.id] = replacement
        state.review_requests["replacement-review"] = ReviewRequest(
            id="replacement-review",
            deliverable_id=replacement.id,
            reviewer_agent_id="reviewer",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha=RECOVERY_COMMIT,
        )
        state.tasks["correction-task"].deliverable_ids.append(replacement.id)
        return state

    repository.update(_submit_reviewed_replacement)
    recovered = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    successor = state.release_packages[successor_id]

    assert recovered["recovered_successor_release_package_ids"] == [successor_id]
    assert successor.state == TeamRecordState.PENDING
    assert successor.error is None
    assert successor.deliverable_ids == ["replacement-deliverable"]
    assert state.git_promotions[failed_promotion_id].state == "retired"
    active_promotions = [
        state.git_promotions[promotion_id]
        for promotion_id in successor.git_promotion_record_ids
        if state.git_promotions[promotion_id].state != "retired"
    ]
    assert len(active_promotions) == 1
    assert active_promotions[0].promotion_kind == "correction_aggregate"
    assert active_promotions[0].sources[0].expected_commit_sha == RECOVERY_COMMIT
    assert active_promotions[0].base_commit_sha == CANDIDATE_COMMIT
    assert recovered["git_promotion_event_ids"] == [active_promotions[0].id]


@pytest.mark.parametrize(
    "verification_status",
    ["passed", "failed", "partial", "not_run", "unknown"],
)
def test_reviewed_rework_ignores_advisory_verification_status(
    tmp_path: Path,
    verification_status: str,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review")
    repository.update(lambda _state: seeded, expected_revision=0)
    created = CandidateCorrectionReconciliationService(repository).reconcile()
    successor_id = created["created_successor_release_package_ids"][0]
    successor = repository.read().release_packages[successor_id]
    failed_promotion_id = successor.git_promotion_record_ids[0]
    TeamGitReleaseService(repository).record_git_promotion_result(
        promotion_id=failed_promotion_id,
        success=False,
        error="The original correction aggregate failed validation.",
        outputs={"error_code": "scope_mismatch"},
    )

    def _submit_incomplete_replacement(state: TeamState) -> TeamState:
        state = _submit_reviewed_replacement(state)
        state.deliverables["replacement-deliverable"].verification = DeliverableVerification(
            status=verification_status
        )
        return state

    repository.update(_submit_incomplete_replacement)
    recovered = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    successor = state.release_packages[successor_id]

    assert recovered["recovered_successor_release_package_ids"] == [successor_id]
    assert successor.state == TeamRecordState.PENDING
    assert successor.deliverable_ids == ["replacement-deliverable"]
    assert successor.verification_waivers == []
    assert state.git_promotions[failed_promotion_id].state == "retired"


def test_reviewed_rework_records_aggregate_validation_failure_as_actionable_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = TeamStateRepository(tmp_path)
    repository.update(
        lambda _state: _candidate_correction_state(gate_kind="review"),
        expected_revision=0,
    )
    created = CandidateCorrectionReconciliationService(repository).reconcile()
    successor_id = created["created_successor_release_package_ids"][0]
    successor = repository.read().release_packages[successor_id]
    failed_promotion_id = successor.git_promotion_record_ids[0]
    TeamGitReleaseService(repository).record_git_promotion_result(
        promotion_id=failed_promotion_id,
        success=False,
        error="The original correction aggregate failed validation.",
        outputs={"error_code": "scope_mismatch"},
    )

    def _submit_reviewed_replacement(state: TeamState) -> TeamState:
        original = state.deliverables["correction-deliverable"]
        original.rework_requested_at = datetime.now(UTC)
        original.rework_requested_by_agent_id = "lead"
        original.rework_reason = "Replace the invalid correction aggregate source."
        original.superseded_by_deliverable_id = "replacement-deliverable"
        replacement = Deliverable(
            id="replacement-deliverable",
            task_id="correction-task",
            producer_agent_id="coder",
            summary="Reviewed replacement correction.",
            supersedes_deliverable_id=original.id,
            git_refs=DeliverableGitRefs(
                agent_branch="agents/session-coder/replacement",
                commit_sha=RECOVERY_COMMIT,
                submitted_commit_sha=RECOVERY_COMMIT,
            ),
            verification=DeliverableVerification(status="passed"),
            state=TeamRecordState.ACCEPTED,
            review_request_ids=["replacement-review"],
        )
        state.deliverables[replacement.id] = replacement
        state.review_requests["replacement-review"] = ReviewRequest(
            id="replacement-review",
            deliverable_id=replacement.id,
            reviewer_agent_id="reviewer",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha=RECOVERY_COMMIT,
        )
        state.tasks["correction-task"].deliverable_ids.append(replacement.id)
        return state

    repository.update(_submit_reviewed_replacement)

    def _reject_aggregate(*_args, result: dict[str, object], **_kwargs) -> None:
        errors = result["errors"]
        assert isinstance(errors, list)
        errors.append("correction source branch is reused by multiple deliverables")
        return None

    monkeypatch.setattr(
        reconciliation_module,
        "_create_correction_aggregate_promotion",
        _reject_aggregate,
    )

    blocked = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    successor = state.release_packages[successor_id]

    assert blocked["candidate_correction_errors"]["release-predecessor"] == (
        "correction source branch is reused by multiple deliverables"
    )
    assert successor.state == TeamRecordState.BLOCKED
    assert state.git_promotions[failed_promotion_id].state == "failed"
    blocker = next(
        blocker
        for blocker in state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
    )
    assert blocker.state == TeamBlockerState.OPEN
    assert "reused by multiple deliverables" in blocker.details


def test_reviewed_rework_cancels_obsolete_merge_recovery_task(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review")
    seeded.project_assignments["coder-project"] = AgentProjectAssignment(
        id="coder-project",
        team_id="team",
        project_id="project",
        agent_id="coder",
    )
    repository.update(
        lambda _state: seeded,
        expected_revision=0,
    )
    created = CandidateCorrectionReconciliationService(repository).reconcile()
    successor_id = created["created_successor_release_package_ids"][0]
    successor = repository.read().release_packages[successor_id]
    failed_promotion_id = successor.git_promotion_record_ids[0]
    failed_promotion = repository.read().git_promotions[failed_promotion_id]
    TeamGitReleaseService(repository).record_git_promotion_result(
        promotion_id=failed_promotion_id,
        success=False,
        error="Correction aggregate has a merge conflict.",
        outputs={
            "error_code": "merge_conflict",
            "base_commit_sha": CANDIDATE_COMMIT,
            "conflict_branch": failed_promotion.source_branch,
            "source_manifests": {
                failed_promotion.source_branch: {
                    "base_commit_sha": CANDIDATE_COMMIT,
                    "reviewed_commit_sha": CORRECTION_COMMIT,
                    "tree_hash": "f" * 40,
                    "commit_shas": [CORRECTION_COMMIT],
                    "changed_paths": ["M\tsrc/Correction.cs"],
                }
            },
        },
    )
    blocked = repository.read()
    recovery_task = next(
        task
        for task in blocked.tasks.values()
        if task.created_by_actor == "system:git-merge-recovery"
    )
    assert recovery_task.state == TeamTaskState.PENDING
    assert any(
        blocker.state == TeamBlockerState.OPEN
        and blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE
        and blocker.source_id == successor_id
        and recovery_task.id in blocker.affected_task_ids
        for blocker in blocked.blockers.values()
    )
    repository.update(_submit_reviewed_replacement, expected_revision=blocked.revision)

    recovered = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    recovery_task = state.tasks[recovery_task.id]

    assert recovered["cancelled_merge_recovery_task_ids"] == [recovery_task.id]
    assert recovery_task.state == TeamTaskState.CANCELLED
    assert recovery_task.cancellation is not None
    assert recovery_task.cancellation.reason_code == "promotion_boundary_superseded"
    assert all(
        blocker.state != TeamBlockerState.OPEN
        for blocker in state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE
        and blocker.source_id == successor_id
    )
    TeamState.model_validate(state.model_dump(mode="python"))

    replacement_aggregate = next(
        promotion
        for promotion in state.git_promotions.values()
        if promotion.release_package_id == successor_id
        and promotion.promotion_kind == "correction_aggregate"
        and promotion.id != failed_promotion_id
        and promotion.state != "retired"
    )
    _failed_state, repeated_failure = TeamGitReleaseService(
        repository
    ).record_git_promotion_result(
        promotion_id=replacement_aggregate.id,
        success=False,
        error="Replacement correction aggregate also has a merge conflict.",
        outputs={
            "error_code": "merge_conflict",
            "base_commit_sha": CANDIDATE_COMMIT,
            "conflict_branch": replacement_aggregate.source_branch,
            "source_manifests": {
                replacement_aggregate.source_branch: {
                    "base_commit_sha": CANDIDATE_COMMIT,
                    "reviewed_commit_sha": RECOVERY_COMMIT,
                    "tree_hash": "b" * 40,
                    "commit_shas": [RECOVERY_COMMIT],
                    "changed_paths": ["M\tsrc/Correction.cs"],
                }
            },
        },
    )
    state = repository.read()
    recovery_task = state.tasks[recovery_task.id]

    assert repeated_failure["merge_recovery_task_id"] == recovery_task.id
    assert repeated_failure["reactivated_task_ids"] == [recovery_task.id]
    assert recovery_task.state == TeamTaskState.PENDING
    assert recovery_task.cancellation is None
    assert any(
        blocker.state == TeamBlockerState.OPEN
        and blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE
        and blocker.source_id == successor_id
        and recovery_task.id in blocker.affected_task_ids
        for blocker in state.blockers.values()
    )
    TeamState.model_validate(state.model_dump(mode="python"))


def test_release_review_correction_waits_for_every_current_review(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review")
    predecessor = seeded.release_packages["release-predecessor"]
    predecessor.review_request_ids.append("release-review-pending")
    seeded.review_requests["release-review-pending"] = ReviewRequest(
        id="release-review-pending",
        release_package_id=predecessor.id,
        candidate_commit_sha=CANDIDATE_COMMIT,
        reviewed_commit_sha=CANDIDATE_COMMIT,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.PENDING,
        created_at=datetime.now(tz=UTC),
    )
    repository.update(lambda _state: seeded, expected_revision=0)

    pending = CandidateCorrectionReconciliationService(repository).reconcile()

    assert pending["changed"] is False
    assert len(repository.read().release_packages) == 1

    def _accept_sibling_review(state: TeamState) -> TeamState:
        state.review_requests["release-review-pending"].state = TeamRecordState.ACCEPTED
        return state

    repository.update(_accept_sibling_review)
    completed = CandidateCorrectionReconciliationService(repository).reconcile()

    assert completed["changed"] is True
    assert len(completed["created_successor_release_package_ids"]) == 1


def test_review_correction_uses_handled_inbox_evidence(tmp_path):
    from evander_team_addon.models import AgentInboxItem

    repo = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review")
    seeded.tasks["correction-task"].description = "Correct the rejected candidate."
    seeded.inbox["handled-evidence"] = AgentInboxItem(
        id="handled-evidence", recipient_agent_id="coder", task_id="correction-task",
        item_type="status_request", state="handled", response_requested=False,
        payload={"kind": "release_review_rejection_correction_work", "review_request_id": "release-review"},
    )
    repo.update(lambda _state: seeded)
    result = CandidateCorrectionReconciliationService(repo).reconcile()
    assert len(result["created_successor_release_package_ids"]) == 1


def test_successor_creation_preserves_handled_resolution_notice(tmp_path):
    from evander_team_addon.models import AgentInboxItem

    repo = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="verification")
    seeded.inbox["existing-notice"] = AgentInboxItem(
        id="existing-notice", recipient_agent_id="lead", task_id="correction-task",
        item_type="status_request", state="handled", response_requested=False,
        payload={"kind": "root_task_resolution", "operator_annotation": "retain"},
    )
    repo.update(lambda _state: seeded)
    result = CandidateCorrectionReconciliationService(repo).reconcile()
    assert len(result["created_successor_release_package_ids"]) == 1
    notices = [item for item in repo.read().inbox.values()
               if item.payload.get("kind") in {"root_task_resolution", "candidate_correction_resolution"}]
    assert len(notices) == 1
    assert notices[0].id == "existing-notice"
    assert notices[0].payload["operator_annotation"] == "retain"


def test_cancelled_generated_correction_does_not_reconcile_successor(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="verification")
    task = seeded.tasks["correction-task"]
    task.state = TeamTaskState.CANCELLED
    task.cancellation = TaskCancellation(
        reason_code="abandoned",
        reason="The correction was intentionally abandoned.",
        actor_kind="operator",
        actor_id="operator",
    )
    repository.update(lambda _state: seeded, expected_revision=0)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()

    assert result["changed"] is False
    assert len(state.release_packages) == 1
    assert state.blockers == {}


def test_late_release_review_rejection_reopens_accepted_correction(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review")
    predecessor = seeded.release_packages["release-predecessor"]
    task_id = "release-review-correction-" + sha256(predecessor.id.encode("utf-8")).hexdigest()[:24]
    task = seeded.tasks.pop("correction-task")
    task.id = task_id
    seeded.tasks[task.id] = task
    seeded.deliverables["correction-deliverable"].task_id = task.id
    predecessor.review_correction_task_id = task.id
    predecessor.review_request_ids.append("release-review-late")
    late_review = ReviewRequest(
        id="release-review-late",
        release_package_id=predecessor.id,
        candidate_commit_sha=CANDIDATE_COMMIT,
        reviewed_commit_sha=CANDIDATE_COMMIT,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.PENDING,
    )
    seeded.review_requests[late_review.id] = late_review
    decision_result: dict[str, object] = {
        "errors": [],
        "events": [],
        "created_inbox_item_ids": [],
    }

    _cascade_review_decision(
        seeded,
        review=late_review,
        decision_state=TeamRecordState.REJECTED,
        findings=["Correct the late release-review finding."],
        projects_to_recalc=set(),
        result=decision_result,
    )

    task = seeded.tasks[task_id]
    deliverable = seeded.deliverables["correction-deliverable"]
    assert task.state == TeamTaskState.IN_PROGRESS
    assert deliverable.rework_requested_at is not None
    late_notice = seeded.inbox[decision_result["created_inbox_item_ids"][0]]
    assert late_notice.payload["supersedes_deliverable_ids"] == [deliverable.id]
    repository.update(lambda _state: seeded, expected_revision=0)

    result = CandidateCorrectionReconciliationService(repository).reconcile()

    assert result["changed"] is False
    assert len(repository.read().release_packages) == 1


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
    assert blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
    assert blocker.resolution_scope == TeamBlockerResolutionScope.DOWNSTREAM
    assert blocker.reason_code == "candidate_correction_successor_failed"
    assert blocker.owner_agent_id == "coder"
    assert "no accepted review" in blocker.details
    outbox_count = len(repository.list_outbox_records())
    assert outbox_count == 1

    replay = CandidateCorrectionReconciliationService(repository).reconcile()

    assert replay["changed"] is False
    assert len(repository.read().blockers) == 1
    assert len(repository.list_outbox_records()) == outbox_count


def test_downstream_candidate_blocker_does_not_unresolve_correction_and_retries(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review", reviewed=False)
    repository.update(lambda _state: seeded, expected_revision=0)
    CandidateCorrectionReconciliationService(repository).reconcile()

    blocked = repository.read()
    assert assess_task(blocked, "correction-task").resolution_status.value == "resolved"
    deliverable = blocked.deliverables["correction-deliverable"]
    deliverable.review_request_ids = ["correction-review"]
    blocked.review_requests["correction-review"] = ReviewRequest(
        id="correction-review",
        deliverable_id=deliverable.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=CORRECTION_COMMIT,
    )
    repository.update(lambda _state: blocked, expected_revision=blocked.revision)

    retried = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()

    assert retried["changed"] is True
    assert len(retried["created_successor_release_package_ids"]) == 1
    assert next(iter(state.blockers.values())).state == TeamBlockerState.RESOLVED


@pytest.mark.parametrize("gate_kind", ["verification", "review"])
def test_operator_can_create_exact_pinned_replacement_for_owned_blocker(
    tmp_path: Path,
    gate_kind: str,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind=gate_kind, reviewed=False)
    seeded.deliverables["correction-deliverable"].verification = DeliverableVerification(
        status="partial", summary="The full verifier is unavailable."
    )
    repository.update(lambda _state: seeded, expected_revision=0)
    CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    blocker = next(iter(state.blockers.values()))

    state, result = TeamAdminService(repository).recover_candidate_correction_successor_as_operator(
        blocker_id=blocker.id,
        action="create_verified_replacement",
        operator_actor_id="jonathan",
        instruction="Also fix the exact client-test blocker and run the full gate.",
        expected_revision=state.revision,
    )

    recovery = state.tasks[result["created_recovery_task_id"]]
    assert recovery.originating_task_id == "correction-task"
    assert recovery.parent_task_id is None
    assert recovery.dependency_task_ids == []
    assert recovery.git_base_branch == "agents/session-coder/correction"
    assert recovery.git_base_commit_sha == CORRECTION_COMMIT
    assert "Operator instruction from jonathan:" in recovery.description
    assert "Also fix the exact client-test blocker and run the full gate." in recovery.description
    assert state.blockers[blocker.id].state == TeamBlockerState.OPEN
    recovery_event = state.blocker_events[result["created_blocker_event_id"]]
    assert recovery_event.event_type == TeamBlockerEventType.PROGRESS
    assert recovery_event.reference_ids == [recovery.id]
    assert recovery_event.metadata == {
        "kind": "candidate_correction_recovery_task_created",
        "operator_actor_id": "jonathan",
        "recovery_task_id": recovery.id,
        "owner_agent_id": "coder",
        "git_base_branch": "agents/session-coder/correction",
        "git_base_commit_sha": CORRECTION_COMMIT,
    }
    assert state.blockers[blocker.id].last_progress_at == recovery_event.created_at

    progress_event_ids = {
        event.id
        for event in state.blocker_events.values()
        if event.blocker_id == blocker.id and event.event_type == TeamBlockerEventType.PROGRESS
    }
    state, replay = TeamAdminService(repository).recover_candidate_correction_successor_as_operator(
        blocker_id=blocker.id,
        action="create_verified_replacement",
        operator_actor_id="jonathan",
        instruction="A replay cannot replace the task's original instruction.",
        expected_revision=state.revision,
    )
    assert replay["existing_recovery_task_id"] == recovery.id
    assert "created_recovery_task_id" not in replay
    assert "Also fix the exact client-test blocker and run the full gate." in recovery.description
    assert "A replay cannot replace the task's original instruction." not in recovery.description
    assert progress_event_ids == {
        event.id
        for event in state.blocker_events.values()
        if event.blocker_id == blocker.id and event.event_type == TeamBlockerEventType.PROGRESS
    }

    with pytest.raises(ValueError, match="does not match task .* pin"):
        TeamAdminService(repository).create_git_work_context_with_id(
            context_id="wrong-context",
            agent_id=recovery.owner_agent_id,
            task_id=recovery.id,
            agent_branch="agents/session-coder/recovery",
            base_branch=recovery.git_base_branch,
            base_commit="a" * 40,
            expected_revision=state.revision,
        )

    state, context_id = TeamAdminService(repository).create_git_work_context_with_id(
        context_id="pinned-context",
        agent_id=recovery.owner_agent_id,
        task_id=recovery.id,
        agent_branch="agents/session-coder/recovery",
        base_branch=recovery.git_base_branch,
        base_commit=CORRECTION_COMMIT,
        expected_revision=state.revision,
    )
    assert state.git_work_contexts[context_id].base_commit == CORRECTION_COMMIT


@pytest.mark.parametrize("gate_kind", ["verification", "review"])
def test_accepted_downstream_recovery_without_agent_verification_creates_successor(
    tmp_path: Path,
    gate_kind: str,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind=gate_kind, reviewed=False)
    seeded.deliverables["correction-deliverable"].verification = DeliverableVerification(
        status="partial", summary="Needs downstream repair."
    )
    repository.update(lambda _state: seeded, expected_revision=0)
    CandidateCorrectionReconciliationService(repository).reconcile()
    blocked = repository.read()
    blocker = next(iter(blocked.blockers.values()))

    recovery_task = AgentTask(
        id="downstream-recovery-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Repair downstream verifier failure",
        description="Pinned recovery on the accepted correction.",
        created_by_actor="system:candidate-correction-recovery",
        originating_task_id="correction-task",
        git_base_branch="agents/session-coder/correction",
        git_base_commit_sha=CORRECTION_COMMIT,
        deliverable_ids=["downstream-recovery-deliverable"],
    )
    recovery_deliverable = Deliverable(
        id="downstream-recovery-deliverable",
        task_id=recovery_task.id,
        producer_agent_id="coder",
        summary="Verified downstream repair.",
        git_refs=DeliverableGitRefs(
            agent_branch="agents/session-coder/downstream-recovery",
            commit_sha=RECOVERY_COMMIT,
            submitted_commit_sha=RECOVERY_COMMIT,
        ),
        review_manifest=DeliverableReviewManifest(
            base_commit_sha=CORRECTION_COMMIT,
            reviewed_commit_sha=RECOVERY_COMMIT,
            tree_hash="b" * 40,
            commit_shas=[RECOVERY_COMMIT],
            changed_paths=["M\tsrc/VerifierRepair.cs"],
        ),
        review_request_ids=["downstream-recovery-review"],
        state=TeamRecordState.ACCEPTED,
    )
    blocked.tasks[recovery_task.id] = recovery_task
    blocked.deliverables[recovery_deliverable.id] = recovery_deliverable
    blocked.review_requests["downstream-recovery-review"] = ReviewRequest(
        id="downstream-recovery-review",
        deliverable_id=recovery_deliverable.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=RECOVERY_COMMIT,
    )
    repository.update(lambda _state: blocked, expected_revision=blocked.revision)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    successor = next(
        release
        for release in state.release_packages.values()
        if release.supersedes_release_package_id == "release-predecessor"
    )

    assert result["created_successor_release_package_ids"] == [successor.id]
    assert successor.deliverable_ids == ["downstream-recovery-deliverable"]
    assert successor.verification_waivers == []
    assert state.blockers[blocker.id].state == TeamBlockerState.RESOLVED


def test_passed_dependency_backed_recovery_deliverable_creates_successor(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="verification", reviewed=False)
    seeded.deliverables["correction-deliverable"].verification = DeliverableVerification(
        status="partial", summary="A separate client fixture still fails."
    )
    repository.update(lambda _state: seeded, expected_revision=0)
    CandidateCorrectionReconciliationService(repository).reconcile()
    blocked = repository.read()
    blocker = next(iter(blocked.blockers.values()))

    recovery_task = AgentTask(
        id="dependency-backed-recovery-task",
        owner_agent_id="coder",
        project_id="project",
        priority=0,
        state=TeamTaskState.FINISHED,
        title="Repair the downstream client fixture",
        description="Preserve the accepted correction as the sole dependency.",
        created_by_agent_id="lead",
        originating_task_id="correction-task",
        git_base_branch="main",
        dependency_task_ids=["correction-task"],
        deliverable_ids=["dependency-backed-recovery-deliverable"],
    )
    recovery_deliverable = Deliverable(
        id="dependency-backed-recovery-deliverable",
        task_id=recovery_task.id,
        producer_agent_id="coder",
        summary="Verified the correction and downstream fixture together.",
        git_refs=DeliverableGitRefs(
            agent_branch="agents/session-coder/dependency-backed-recovery",
            commit_sha=RECOVERY_COMMIT,
            submitted_commit_sha=RECOVERY_COMMIT,
            branch_context_id="dependency-backed-recovery-context",
        ),
        verification=DeliverableVerification(status="passed"),
        review_request_ids=["dependency-backed-recovery-review"],
        state=TeamRecordState.ACCEPTED,
    )
    blocked.tasks[recovery_task.id] = recovery_task
    blocked.deliverables[recovery_deliverable.id] = recovery_deliverable
    blocked.git_work_contexts["dependency-backed-recovery-context"] = GitWorkContext(
        id="dependency-backed-recovery-context",
        agent_id="coder",
        task_id=recovery_task.id,
        agent_branch=recovery_deliverable.git_refs.agent_branch or "",
        base_branch="main",
        base_commit=CORRECTION_COMMIT,
        commit_sha=RECOVERY_COMMIT,
        dependency_git_sources=[
            DependencyGitSource(
                dependency_task_id="correction-task",
                deliverable_id="correction-deliverable",
                branch="agents/session-coder/correction",
                commit_sha=CORRECTION_COMMIT,
            )
        ],
    )
    blocked.review_requests["dependency-backed-recovery-review"] = ReviewRequest(
        id="dependency-backed-recovery-review",
        deliverable_id=recovery_deliverable.id,
        reviewer_agent_id="reviewer",
        state=TeamRecordState.ACCEPTED,
        reviewed_commit_sha=RECOVERY_COMMIT,
    )
    repository.update(lambda _state: blocked, expected_revision=blocked.revision)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    successor = next(
        release
        for release in state.release_packages.values()
        if release.supersedes_release_package_id == "release-predecessor"
    )

    assert result["created_successor_release_package_ids"] == [successor.id]
    assert successor.deliverable_ids == ["dependency-backed-recovery-deliverable"]
    assert successor.verification_waivers == []
    promotion = state.git_promotions[successor.git_promotion_record_ids[0]]
    assert promotion.base_commit_sha == CANDIDATE_COMMIT
    assert promotion.sources[0].expected_commit_sha == RECOVERY_COMMIT
    assert state.blockers[blocker.id].state == TeamBlockerState.RESOLVED


@pytest.mark.parametrize("verification_status", ["partial", "failed"])
@pytest.mark.parametrize("gate_kind", ["verification", "review"])
def test_candidate_correction_verification_waiver_action_is_retired(
    tmp_path: Path,
    gate_kind: str,
    verification_status: str,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind=gate_kind, reviewed=False)
    seeded.deliverables["correction-deliverable"].verification = DeliverableVerification(
        status=verification_status, summary="Legacy diagnostic status."
    )
    repository.update(lambda _state: seeded, expected_revision=0)
    CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    blocker = next(iter(state.blockers.values()))

    with pytest.raises(ValueError, match="unsupported candidate correction recovery action"):
        TeamAdminService(repository).recover_candidate_correction_successor_as_operator(
            blocker_id=blocker.id,
            action="authorize_verification_waiver",
            reason="Legacy waiver request.",
            operator_actor_id="jonathan",
            expected_revision=state.revision,
        )
    assert repository.read().blockers[blocker.id].state == TeamBlockerState.OPEN


def test_generic_operator_resolution_rejects_typed_candidate_recovery(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="review", reviewed=False)
    repository.update(lambda _state: seeded, expected_revision=0)
    CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()
    blocker = next(iter(state.blockers.values()))
    blocker.route = TeamBlockerRoute.OPERATOR
    state = repository.update(lambda _state: state, expected_revision=state.revision)

    with pytest.raises(ValueError, match="candidate-correction successor recovery"):
        TeamAdminService(repository).resolve_terminal_blocker_as_operator(
            blocker_id=blocker.id,
            note="bypass",
            operator_actor_id="jonathan",
            expected_revision=state.revision,
        )


def test_existing_successor_resolves_recovery_before_task_readiness_check(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="verification", reviewed=False)
    repository.update(lambda _state: seeded, expected_revision=0)
    CandidateCorrectionReconciliationService(repository).reconcile()
    blocked = repository.read()
    recovery_blocker = next(iter(blocked.blockers.values()))
    blocked.release_packages["release-existing-successor"] = ReleasePackage(
        id="release-existing-successor",
        project_id="project",
        owner_agent_id="lead",
        supersedes_release_package_id="release-predecessor",
        deliverable_ids=["correction-deliverable"],
        state=TeamRecordState.PENDING,
    )
    blocked.blockers["unrelated-task-blocker"] = TeamBlocker(
        id="unrelated-task-blocker",
        team_id="team",
        project_id="project",
        task_id="correction-task",
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id="unrelated-task-blocker",
        owner_agent_id="coder",
        affected_task_ids=["correction-task"],
        summary="Unrelated task-scoped blocker",
    )
    repository.update(lambda _state: blocked, expected_revision=blocked.revision)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()

    assert result["existing_successor_release_package_ids"] == ["release-existing-successor"]
    assert state.blockers[recovery_blocker.id].state == TeamBlockerState.RESOLVED
    assert state.blockers["unrelated-task-blocker"].state == TeamBlockerState.OPEN


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
    assert not any(event["event_type"] == "team.agent.wake_requested" for event in result["events"])
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


def test_unrelated_duplicate_release_is_not_recorded_as_successor(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    seeded = _candidate_correction_state(gate_kind="verification")
    seeded.release_packages["release-unrelated"] = ReleasePackage(
        id="release-unrelated",
        project_id="project",
        owner_agent_id="lead",
        deliverable_ids=["correction-deliverable"],
        state=TeamRecordState.PENDING,
    )
    repository.update(lambda _state: seeded, expected_revision=0)

    result = CandidateCorrectionReconciliationService(repository).reconcile()
    state = repository.read()

    assert result["changed"] is True
    assert result.get("created_successor_release_package_ids") is None
    assert not any(
        release.supersedes_release_package_id == "release-predecessor"
        for release in state.release_packages.values()
    )
    blocker = next(iter(state.blockers.values()))
    assert blocker.reason_code == "candidate_correction_successor_failed"
    assert "does not directly supersede" in blocker.details
    assert not any(
        item.payload.get("kind") == "candidate_correction_resolution"
        for item in state.inbox.values()
    )
