from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from evander.models import utc_now
from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DeliverableOutcome,
    DeliverableReviewManifest,
    DeliverableVerification,
    GitWorkContext,
    GitWorkContextState,
    HumanReviewChecklistItem,
    HumanReviewRequirement,
    OutcomeKindSnapshot,
    ProjectGitConfig,
    ProjectGoal,
    ProjectReleasePolicy,
    ReleaseReviewRequirement,
    ReleaseVerificationCommand,
    ReviewProfileCriterion,
    ReviewRequest,
    TaskOutcomeContract,
    TeamRecordState,
    TeamTaskKind,
    TeamTaskState,
    WorktreeCleanupCandidate,
    WorktreeCleanupState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.deliverables import (
    _accept_deliverable_and_maybe_task,
)
from evander_team_addon.services.domain.human_reviews import (
    record_human_finding_resolution_proposal,
    record_human_review_decision,
    record_human_review_preparation_result,
    resume_human_review,
)
from evander_team_addon.services.domain.release_packages import (
    _build_release_package,
)
from evander_team_addon.services.domain.review import _cascade_review_decision
from evander_team_addon.services.domain.task_assessment import (
    require_deliverable_acceptance,
    require_release_scope,
)
from evander_team_addon.services.git_release_service import TeamGitReleaseService
from evander_team_addon.services.release_gate_service import TeamReleaseGateService
from evander_team_addon.services.worktree_cleanup_service import (
    TeamWorktreeCleanupService,
)


_MANAGED_REPO_ID = "mr_abcdefabcdefabcdefab"
_INITIAL_CANDIDATE = "1" * 40
_SUCCESSOR_CANDIDATE = "2" * 40
_App_OUTCOME_SNAPSHOT = OutcomeKindSnapshot(
    kind="app-result",
    name="App Result",
    description="A verified App behavior or integrated production outcome.",
    reference_requirements="Provide a durable App result reference.",
    evidence_requirements="Provide verification evidence.",
)


class _AppCleanupBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def execution_backend_for_session(
        self,
        *,
        session_id: str,
        directory_path: str,
    ) -> str:
        assert session_id in {"session-ui", "session-engineer"}
        assert directory_path == "root"
        return "container_worker_vm"

    def cleanup(
        self,
        candidate: WorktreeCleanupCandidate,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        self.calls.append((candidate.task_id, dry_run))
        if candidate.task_id == "correction-child-1":
            return {
                "state": "refused",
                "safe_to_remove": False,
                "removed": False,
                "pruned": False,
                "validation_count": 2,
                "failed_check": "tracked_changes",
                "message": "tracked changes preserve this recovery worktree",
                "ignored_paths": [],
                "ignored_bytes": 0,
                "worktree_bytes": 2048,
                "reclaimed_bytes": 0,
            }
        return {
            "state": "removed",
            "safe_to_remove": True,
            "removed": True,
            "pruned": True,
            "validation_count": 2,
            "failed_check": None,
            "message": "eligible task worktree removed",
            "ignored_paths": [".cache/app-build.bin"],
            "ignored_bytes": 64,
            "worktree_bytes": 1024,
            "reclaimed_bytes": 1024,
        }


def _seed_team(repository: TeamStateRepository) -> None:
    admin = TeamAdminService(repository)
    admin.create_team(
        team_id="app-team",
        name="App Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    admin.create_project(
        project_id="app",
        team_id="app-team",
        name="Manufacturing Execution System",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="ship-app", description="Ship the aggregate App outcome.")],
        git=ProjectGitConfig(
            managed_repo_id=_MANAGED_REPO_ID,
            base_branch="main",
        ),
        expected_revision=repository.read().revision,
    )
    admin.create_role(
        role_id="app__engineer",
        team_id="app-team",
        project_id="app",
        name="App Engineer",
        expected_revision=repository.read().revision,
    )
    admin.create_role(
        role_id="app__ui",
        team_id="app-team",
        project_id="app",
        name="App UI",
        expected_revision=repository.read().revision,
    )
    admin.create_role(
        role_id="app__release_reviewer",
        team_id="app-team",
        project_id="app",
        name="App Release Reviewer",
        expected_revision=repository.read().revision,
    )
    admin.register_agent(
        agent_id="engineer",
        team_id="app-team",
        project_id="app",
        role_id="app__engineer",
        session_id="session-engineer",
        reports_to_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.register_agent(
        agent_id="ui",
        team_id="app-team",
        project_id="app",
        role_id="app__ui",
        session_id="session-ui",
        reports_to_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.register_agent(
        agent_id="release-reviewer",
        team_id="app-team",
        project_id="app",
        role_id="app__release_reviewer",
        session_id="session-release-reviewer",
        reports_to_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_review_profile(
        profile_id="app-release-review",
        team_id="app-team",
        project_id="app",
        name="App release review",
        criteria=[
            ReviewProfileCriterion(
                id="app-flow",
                label="App production flow is verified",
            )
        ],
        expected_revision=repository.read().revision,
    )
    admin.create_outcome_kind_definition(
        definition_id="app-result",
        team_id="app-team",
        project_id="app",
        kind=_App_OUTCOME_SNAPSHOT.kind,
        name=_App_OUTCOME_SNAPSHOT.name,
        description=_App_OUTCOME_SNAPSHOT.description,
        reference_requirements=_App_OUTCOME_SNAPSHOT.reference_requirements,
        evidence_requirements=_App_OUTCOME_SNAPSHOT.evidence_requirements,
        expected_revision=repository.read().revision,
    )

    def _configure(state):
        state.projects["app"].release_policy = ProjectReleasePolicy(
            verification_commands=[
                ReleaseVerificationCommand(
                    name="app-smoke",
                    argv=["pytest", "-q", "tests/test_app_smoke.py"],
                )
            ],
            review_requirements=[
                ReleaseReviewRequirement(
                    reviewer_agent_id="release-reviewer",
                    review_profile_id="app-release-review",
                )
            ],
            human_review_requirement=HumanReviewRequirement(
                reviewer_group_ids=["app-human-reviewers"],
                resolved_reviewer_user_ids=["human-reviewer"],
                checklist=[
                    HumanReviewChecklistItem(
                        id="operator-journey",
                        label="Operator journey passed",
                    )
                ],
                test_instructions="Run the aggregate App operator journey.",
                preparation_mode="workflow",
            ),
        )
        return state

    repository.update(_configure)


def _outcome_task(
    *,
    task_id: str,
    owner_agent_id: str,
    parent_task_id: str | None = None,
) -> AgentTask:
    return AgentTask(
        id=task_id,
        owner_agent_id=owner_agent_id,
        project_id="app",
        priority=1,
        task_kind=TeamTaskKind.OUTCOME,
        outcome_contract=TaskOutcomeContract(
            required_outcome_kinds=["app-result"],
            outcome_kind_definitions=[_App_OUTCOME_SNAPSHOT],
        ),
        title=task_id.replace("-", " ").title(),
        description=f"Produce the {task_id} App outcome.",
        created_by_agent_id="lead",
        parent_task_id=parent_task_id,
    )


def _accept_task_deliverable(
    state,
    *,
    task_id: str,
    deliverable_id: str,
    source_deliverable_ids: list[str] | None = None,
    git_commit: str | None = None,
    git_base_commit: str = "0" * 40,
) -> Deliverable:
    task = state.tasks[task_id]
    task.state = TeamTaskState.WAITING_FOR_REVIEW
    deliverable = Deliverable(
        id=deliverable_id,
        task_id=task.id,
        producer_agent_id=task.owner_agent_id,
        artifact_refs=[f"artifact:{deliverable_id}"],
        summary=f"Accepted contribution for {task.title}.",
        outcomes=[
            DeliverableOutcome(
                kind="app-result",
                reference_ids=[f"evidence:{deliverable_id}"],
                definition_snapshot=_App_OUTCOME_SNAPSHOT,
            )
        ],
        source_deliverable_ids=source_deliverable_ids or [],
        acceptance_recipient_agent_id=(
            state.tasks[task.parent_task_id].owner_agent_id
            if task.parent_task_id is not None
            else "lead"
        ),
        verification=DeliverableVerification(
            status="passed",
            evidence_refs=[f"test:{deliverable_id}"],
        ),
    )
    if git_commit is not None:
        deliverable.git_refs = DeliverableGitRefs(
            commit_sha=git_commit,
            feature_branch=f"team/app-team/features/{task.id}",
            feature_commit_sha=git_commit,
        )
        deliverable.review_manifest = DeliverableReviewManifest(
            base_commit_sha=git_base_commit,
            reviewed_commit_sha=git_commit,
            tree_hash=f"tree-{deliverable_id}",
            commit_shas=[git_commit],
            changed_paths=[f"M\tsrc/{task.id}.cs"],
        )
    state.deliverables[deliverable.id] = deliverable
    task.deliverable_ids.append(deliverable.id)
    require_deliverable_acceptance(state, deliverable.id)
    assert _accept_deliverable_and_maybe_task(
        state,
        deliverable=deliverable,
        task=task,
        now=utc_now(),
    )
    if git_commit is not None:
        review_id = f"deliverable-review-{deliverable.id}"
        state.review_requests[review_id] = ReviewRequest(
            id=review_id,
            deliverable_id=deliverable.id,
            reviewer_agent_id="release-reviewer",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha=git_commit,
        )
        deliverable.review_request_ids.append(review_id)
    return deliverable


def _accept_release_review(state, *, release_id: str) -> None:
    release = state.release_packages[release_id]
    review = next(
        state.review_requests[review_id]
        for review_id in release.review_request_ids
        if state.review_requests[review_id].release_package_id == release.id
    )
    assert review.candidate_commit_sha == release.candidate_commit_sha
    _cascade_review_decision(
        state,
        review=review,
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result={"errors": [], "events": [], "git_promotion_event_ids": []},
    )
    assert review.state == TeamRecordState.ACCEPTED


def test_representative_app_hierarchy_human_successor_and_cleanup(
    tmp_path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    _seed_team(repository)
    initial_release_result: dict[str, Any] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }

    def _build_initial_hierarchy(state):
        state.tasks["app-root"] = _outcome_task(
            task_id="app-root",
            owner_agent_id="lead",
        )
        state.tasks["cell-control"] = _outcome_task(
            task_id="cell-control",
            owner_agent_id="engineer",
            parent_task_id="app-root",
        )
        state.tasks["plc-implementation"] = _outcome_task(
            task_id="plc-implementation",
            owner_agent_id="engineer",
            parent_task_id="cell-control",
        )
        state.tasks["operator-ui"] = _outcome_task(
            task_id="operator-ui",
            owner_agent_id="ui",
            parent_task_id="app-root",
        )

        plc = _accept_task_deliverable(
            state,
            task_id="plc-implementation",
            deliverable_id="plc-contribution",
        )
        ui = _accept_task_deliverable(
            state,
            task_id="operator-ui",
            deliverable_id="ui-contribution",
        )
        cell = _accept_task_deliverable(
            state,
            task_id="cell-control",
            deliverable_id="cell-composition",
            source_deliverable_ids=[plc.id],
        )
        root = _accept_task_deliverable(
            state,
            task_id="app-root",
            deliverable_id="app-aggregate",
            source_deliverable_ids=[cell.id, ui.id],
            git_commit="a" * 40,
        )
        scope = require_release_scope(
            state,
            project_id="app",
            deliverable_ids=[root.id],
        )
        assert scope["valid"] is True
        assert state.tasks["cell-control"].owner_agent_id == (
            state.tasks["plc-implementation"].owner_agent_id
        )

        release_id = _build_release_package(
            state,
            project=state.projects["app"],
            request={
                "id": "app-release-1",
                "deliverable_ids": [root.id],
            },
            result=initial_release_result,
            projects_to_recalc=set(),
            actor_agent_id="lead",
            team_id="app-team",
        )
        assert release_id == "app-release-1"
        return state

    state = repository.update(_build_initial_hierarchy)
    assert initial_release_result["errors"] == []
    initial_promotion_id = initial_release_result["git_promotion_event_ids"][0]
    git_service = TeamGitReleaseService(
        repository,
        human_review_eligibility_resolver=lambda _requirement: ["human-reviewer"],
    )
    gate_service = TeamReleaseGateService(
        repository,
        human_review_eligibility_resolver=lambda _requirement: ["human-reviewer"],
    )

    state, _ = git_service.record_git_promotion_result(
        promotion_id=initial_promotion_id,
        success=True,
        commit_sha=_INITIAL_CANDIDATE,
        outputs={"tree_hash": "initial-tree"},
    )
    initial = state.release_packages["app-release-1"]
    assert initial.candidate_commit_sha == _INITIAL_CANDIDATE
    initial_verification_id = initial.verification_run_ids[0]
    state, _ = gate_service.record_verification_result(
        release_package_id=initial.id,
        verification_run_id=initial_verification_id,
        candidate_commit_sha=_INITIAL_CANDIDATE,
        verification_state="passed",
        command_results=[{"name": "app-smoke", "state": "passed", "exit_code": 0}],
    )
    repository.update(
        lambda current: _accept_release_review(current, release_id=initial.id) or current
    )

    def _request_human_changes(current):
        release = current.release_packages[initial.id]
        request = next(
            item
            for item in current.human_review_requests.values()
            if item.release_package_id == release.id
        )
        assert request.state == "preparing"
        record_human_review_preparation_result(
            current,
            human_review_request_id=request.id,
            candidate_commit_sha=request.candidate_commit_sha,
            candidate_tree_hash=request.candidate_tree_hash,
            preparation_generation=request.preparation_generation,
            target={"alias": "app-preview-initial"},
            error=None,
            result={"events": []},
        )
        record_human_review_decision(
            current,
            human_review_request_id=request.id,
            reviewer_user_id="human-reviewer",
            reviewer_display_name="App Operator",
            decision="changes_requested",
            request_id="app-initial-changes",
            expected_revision=request.revision,
            checklist_results={"operator-journey": True},
            summary="Correct the recovery and alarm interactions.",
            findings=[
                {
                    "text": "Recovery leaves the cell in manual mode.",
                    "target_deliverable_ids": ["app-aggregate"],
                    "evidence_refs": ["video:manual-mode"],
                },
                {
                    "text": "Alarm acknowledgement is lost after reconnect.",
                    "target_deliverable_ids": ["app-aggregate"],
                    "evidence_refs": ["video:alarm-reconnect"],
                },
            ],
            result={"events": []},
        )
        return current

    state = repository.update(_request_human_changes)
    initial = state.release_packages[initial.id]
    assert initial.state == TeamRecordState.REJECTED
    assert len(state.human_review_findings) == 2
    case_ids = {
        state.tasks[finding.correction_task_id].case_id
        for finding in state.human_review_findings.values()
    }
    assert len(case_ids) == 1

    successor_result: dict[str, Any] = {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
    }

    def _accept_decomposed_corrections(current):
        correction_deliverable_ids: list[str] = []
        findings = sorted(current.human_review_findings.values(), key=lambda item: item.id)
        for position, finding in enumerate(findings, start=1):
            correction_root = current.tasks[finding.correction_task_id]
            child_id = f"correction-child-{position}"
            current.tasks[child_id] = AgentTask(
                id=child_id,
                owner_agent_id=correction_root.owner_agent_id,
                project_id="app",
                case_id=correction_root.case_id,
                priority=1,
                task_kind=TeamTaskKind.SUPPORT,
                title=f"Implement correction {position}",
                description=f"Implement and test {finding.text}",
                created_by_agent_id="lead",
                parent_task_id=correction_root.id,
                git_base_branch=correction_root.git_base_branch,
            )
            case = current.cases[correction_root.case_id or ""]
            case.task_ids.append(child_id)
            child = _accept_task_deliverable(
                current,
                task_id=child_id,
                deliverable_id=f"correction-child-deliverable-{position}",
            )
            correction = _accept_task_deliverable(
                current,
                task_id=correction_root.id,
                deliverable_id=f"correction-root-deliverable-{position}",
                source_deliverable_ids=[child.id],
                git_commit=str(position + 2) * 40,
                git_base_commit=_INITIAL_CANDIDATE,
            )
            correction_deliverable_ids.append(correction.id)
            record_human_finding_resolution_proposal(
                current,
                finding_id=finding.id,
                proposed_resolution=f"Verified App correction {position}.",
                evidence_refs=[f"test:correction-{position}"],
                proposed_by_deliverable_ids=[correction.id],
            )

        successor_id = _build_release_package(
            current,
            project=current.projects["app"],
            request={
                "id": "app-release-2",
                "deliverable_ids": correction_deliverable_ids,
                "supersedes_release_package_id": initial.id,
            },
            result=successor_result,
            projects_to_recalc=set(),
            actor_agent_id="lead",
            team_id="app-team",
        )
        assert successor_id == "app-release-2"
        return current

    state = repository.update(_accept_decomposed_corrections)
    assert successor_result["errors"] == []
    successor = state.release_packages["app-release-2"]
    assert successor.supersedes_release_package_id == initial.id
    assert successor.base_commit_sha == _INITIAL_CANDIDATE
    assert len(successor_result["correction_deliverable_ids"]) == 2

    aggregate_promotion_id = successor_result["git_promotion_event_ids"][0]
    aggregate_promotion = state.git_promotions[aggregate_promotion_id]
    assert aggregate_promotion.promotion_kind == "correction_aggregate"
    aggregate_manifests = {
        source.source_branch: {
            "base_commit_sha": _INITIAL_CANDIDATE,
            "reviewed_commit_sha": source.expected_commit_sha,
            "tree_hash": f"tree-{index}",
            "commit_shas": [source.expected_commit_sha],
            "changed_paths": [f"M\tsrc/correction-{index}.cs"],
        }
        for index, source in enumerate(aggregate_promotion.sources, start=1)
    }
    state, _ = git_service.record_git_promotion_result(
        promotion_id=aggregate_promotion_id,
        success=True,
        commit_sha="5" * 40,
        outputs={
            "base_commit_sha": _INITIAL_CANDIDATE,
            "source_manifests": aggregate_manifests,
            "tree_hash": "aggregate-tree",
        },
    )
    successor = state.release_packages[successor.id]
    release_promotion_id = next(
        promotion_id
        for promotion_id in successor.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "release"
    )
    state, _ = git_service.record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        commit_sha=_SUCCESSOR_CANDIDATE,
        outputs={
            "base_commit_sha": "9" * 40,
            "tree_hash": "successor-tree",
        },
    )
    successor = state.release_packages[successor.id]
    successor_verification_id = successor.verification_run_ids[0]
    state, _ = gate_service.record_verification_result(
        release_package_id=successor.id,
        verification_run_id=successor_verification_id,
        candidate_commit_sha=_SUCCESSOR_CANDIDATE,
        verification_state="passed",
        command_results=[{"name": "app-smoke", "state": "passed", "exit_code": 0}],
    )
    repository.update(
        lambda current: _accept_release_review(current, release_id=successor.id) or current
    )

    def _block_resume_and_approve_successor(current):
        release = current.release_packages[successor.id]
        request = next(
            item
            for item in current.human_review_requests.values()
            if item.release_package_id == release.id
        )
        original_request_id = request.id
        original_candidate = (
            request.candidate_commit_sha,
            request.candidate_tree_hash,
        )
        record_human_review_preparation_result(
            current,
            human_review_request_id=request.id,
            candidate_commit_sha=request.candidate_commit_sha,
            candidate_tree_hash=request.candidate_tree_hash,
            preparation_generation=request.preparation_generation,
            target=None,
            error="App preview deployment is not ready",
            result={"events": []},
        )
        assert request.state == "blocked"
        resume_human_review(
            current,
            human_review_request_id=request.id,
            result={"events": []},
        )
        assert request.id == original_request_id
        assert (
            request.candidate_commit_sha,
            request.candidate_tree_hash,
        ) == original_candidate
        ready_at = datetime.now(tz=UTC)
        record_human_review_preparation_result(
            current,
            human_review_request_id=request.id,
            candidate_commit_sha=request.candidate_commit_sha,
            candidate_tree_hash=request.candidate_tree_hash,
            preparation_generation=request.preparation_generation,
            target={
                "url": "https://app-preview.example/operator",
                "expires_at": ready_at + timedelta(hours=1),
                "evidence_refs": ["workflow:app-preview-ready"],
            },
            error=None,
            result={"events": []},
            now=ready_at,
        )
        finding_ids = sorted(current.human_review_findings)
        approval_result: dict[str, Any] = {
            "events": [],
            "git_promotion_event_ids": [],
        }
        record_human_review_decision(
            current,
            human_review_request_id=request.id,
            reviewer_user_id="human-reviewer",
            reviewer_display_name="App Operator",
            decision="approved",
            request_id="app-successor-approved",
            expected_revision=request.revision,
            checklist_results={"operator-journey": True},
            confirmed_finding_ids=finding_ids,
            summary="Corrected App journey passed on the prepared preview.",
            evidence_refs=["test:app-operator-journey"],
            result=approval_result,
        )
        assert sorted(approval_result["resolved_human_finding_ids"]) == finding_ids
        return current

    state = repository.update(_block_resume_and_approve_successor)
    successor = state.release_packages[successor.id]
    assert all(finding.state == "resolved" for finding in state.human_review_findings.values())
    assert successor.approved_commit_sha == _SUCCESSOR_CANDIDATE
    canonical = next(
        promotion
        for promotion in state.git_promotions.values()
        if promotion.release_package_id == successor.id
        and promotion.promotion_kind == "canonical_push"
    )
    state, _ = git_service.record_git_promotion_result(
        promotion_id=canonical.id,
        success=True,
        commit_sha=_SUCCESSOR_CANDIDATE,
    )
    state, published = git_service.mark_release_published(
        release_package_id=successor.id,
        success=True,
        canonical_branch=successor.canonical_branch,
        published_commit_sha=_SUCCESSOR_CANDIDATE,
    )
    successor = state.release_packages[successor.id]
    assert published["state"] == TeamRecordState.ACCEPTED.value
    assert (
        successor.candidate_commit_sha
        == successor.approved_commit_sha
        == successor.published_commit_sha
        == _SUCCESSOR_CANDIDATE
    )

    cleanup_now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    def _add_cleanup_contexts(current):
        current.git_work_contexts["cleanup-eligible"] = GitWorkContext(
            id="cleanup-eligible",
            agent_id="ui",
            task_id="operator-ui",
            agent_branch="agents/session-ui/tasks/operator-ui",
            base_branch="main",
            base_commit=_SUCCESSOR_CANDIDATE,
            commit_sha=_SUCCESSOR_CANDIDATE,
            worktree_path="/home/agent/worktrees/app/operator-ui",
            state=GitWorkContextState.ABANDONED,
            created_at=cleanup_now - timedelta(hours=2),
            updated_at=cleanup_now - timedelta(hours=2),
        )
        current.git_work_contexts["cleanup-dirty-recovery"] = GitWorkContext(
            id="cleanup-dirty-recovery",
            agent_id="engineer",
            task_id="correction-child-1",
            agent_branch="agents/session-engineer/tasks/correction-child-1",
            base_branch="main",
            base_commit=_SUCCESSOR_CANDIDATE,
            commit_sha=_SUCCESSOR_CANDIDATE,
            worktree_path="/home/agent/worktrees/app/correction-child-1",
            state=GitWorkContextState.ABANDONED,
            created_at=cleanup_now - timedelta(hours=2),
            updated_at=cleanup_now - timedelta(hours=2),
        )
        return current

    repository.update(_add_cleanup_contexts)
    cleanup_backend = _AppCleanupBackend()
    cleanup = TeamWorktreeCleanupService(repository, cleanup_backend).reconcile(
        now=cleanup_now,
        grace_seconds=0,
        retry_seconds=0,
    )

    assert cleanup.succeeded_count == 1
    assert cleanup.operator_action_required_count == 1
    assert cleanup.recovery_required_count == 0
    assert cleanup.reclaimed_bytes == 1024
    final_state = repository.read()
    assert "cleanup-eligible" not in final_state.git_work_contexts
    assert "cleanup-dirty-recovery" in final_state.git_work_contexts
    cleanup_states = {
        candidate.task_id: candidate.state
        for candidate in final_state.worktree_cleanup_candidates.values()
    }
    assert cleanup_states == {
        "operator-ui": WorktreeCleanupState.SUCCEEDED,
        "correction-child-1": WorktreeCleanupState.OPERATOR_ACTION_REQUIRED,
    }
