from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander.worker.models import WorkerTaskDependencyGitSource
from evander_team_addon.models import (
    AgentRuntime,
    AgentTask,
    Deliverable,
    GitWorkContext,
    HumanReviewFinding,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ReleaseCandidateDeliverableEvidence,
    ReleaseCandidateManifest,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseVerificationRun,
    ReviewRequest,
    Team,
    TeamBlockerRoute,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    TaskDeliverableContract,
)
from evander_team_addon.providers import (
    DependencyGitPreparationError,
    _create_project_repo_git_work_contexts,
    _prepare_project_repo_task_worktree,
    _verified_human_review_correction_git_source,
    _verified_rejected_candidate_git_source,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.correction_git_source import (
    human_review_correction_git_source,
    rejected_deliverable_continuation_git_source,
    rejected_deliverable_continuation_git_sources,
    release_review_correction_git_source,
    release_verification_correction_git_source,
)
from evander_team_addon.services.domain.preparation_failures import (
    create_human_correction_preparation_recovery_inbox,
    create_rejected_candidate_correction_preparation_recovery_inbox,
    record_task_preparation_failure,
    task_preparation_generation,
)
from evander_team_addon.services.domain.wake import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
)


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"
CANDIDATE_COMMIT = "c" * 40
CANDIDATE_TREE = "d" * 40
FEATURE_COMMIT = "e" * 40
RELEASE_BRANCH = "team/team/releases/rejected"
FEATURE_BRANCH = "agents/source-session/tasks/source"


@pytest.mark.parametrize("pin_task_base", [False, True])
@pytest.mark.parametrize("multi_product", [False, True])
def test_reassigned_rework_starts_from_immutable_rejected_deliverable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    pin_task_base: bool,
    multi_product: bool,
) -> None:
    state = _correction_state()
    base_commit = "6" * 40
    base_tree = "5" * 40
    rejected_commit = "7" * 40
    composed_commit = "8" * 40
    composed_tree = "9" * 40
    rejected_branch = "agents/original-session/tasks/rework-task"
    pinned_ref = "refs/evander/team-reviews/rejected-rework"
    rejected_products = [
        (
            "rejected-deliverable",
            rejected_branch,
            rejected_commit,
            pinned_ref,
        )
    ]
    if multi_product:
        rejected_products.append(
            (
                "rejected-deliverable-two",
                "agents/original-session/tasks/rework-task-two",
                "a" * 40,
                "refs/evander/team-reviews/rejected-rework-two",
            )
        )
    state.agents["replacement"] = PersistentAgent(
        id="replacement",
        team_id="team",
        role_id="owner-role",
        session_id="replacement-session",
        reports_to_agent_id="lead",
    )
    state.tasks["rework-task"] = AgentTask(
        id="rework-task",
        owner_agent_id="replacement",
        project_id="project",
        priority=1,
        state=TeamTaskState.IN_PROGRESS,
        title="Revise rejected candidate",
        description="Continue from the reviewed Git candidate.",
        created_by_agent_id="lead",
        deliverable_ids=[product[0] for product in rejected_products],
        git_base_branch="main",
        git_base_commit_sha=base_commit if pin_task_base else None,
    )
    for index, (deliverable_id, branch, commit, candidate_ref) in enumerate(
        rejected_products,
        start=1,
    ):
        context_id = f"rejected-context-{index}"
        state.git_work_contexts[context_id] = GitWorkContext(
            id=context_id,
            agent_id="owner",
            task_id="rework-task",
            agent_branch=branch,
            base_branch="main",
            base_commit=base_commit,
            commit_sha=commit,
            state="submitted",
        )
        state.deliverables[deliverable_id] = Deliverable(
            id=deliverable_id,
            task_id="rework-task",
            producer_agent_id="owner",
            summary=f"Rejected candidate {index}",
            state=TeamRecordState.REJECTED,
            acceptance_policy_version=2,
            git_refs={
                "branch_context_id": context_id,
                "agent_branch": branch,
                "commit_sha": commit,
                "submitted_commit_sha": commit,
                "accepted_pinned_ref": candidate_ref,
            },
        )
    state.agent_runtimes["replacement"] = AgentRuntime(
        agent_id="replacement",
        active_dispatch_id="dispatch-rework",
    )
    repo = TeamStateRepository(tmp_path)
    state = repo.update(lambda _state: state, expected_revision=0)

    class _ManagedGit:
        def resolve_team_review_commit(self, *_args: object, **kwargs: object) -> str:
            return next(
                commit
                for _deliverable_id, _branch, commit, candidate_ref in rejected_products
                if candidate_ref == kwargs["pinned_ref"]
            )

        def resolve_branch_commit(self, *_args: object, **kwargs: object) -> str:
            return next(
                commit
                for _deliverable_id, branch, commit, _candidate_ref in rejected_products
                if branch == kwargs["branch"]
            )

        def verify_branch_commit_ancestry(
            self,
            *_args: object,
            **kwargs: object,
        ) -> dict[str, object]:
            commit = next(
                commit
                for _deliverable_id, branch, commit, _candidate_ref in rejected_products
                if branch == kwargs["branch"]
            )
            assert kwargs["required_ancestor_shas"] == [base_commit, commit]
            return {"commit_sha": commit}

        def reduce_commit_frontier(
            self,
            *_args: object,
            **kwargs: object,
        ) -> dict[str, object]:
            assert kwargs["base_commit_sha"] == base_commit
            return {
                "base_commit_sha": base_commit,
                "base_tree_hash": base_tree,
                "frontier_commit_shas": [
                    source["commit_sha"] for source in kwargs["sources"]
                ],
                "reduced_source_deliverable_ids": [],
            }

    prepared_calls: list[dict[str, object]] = []

    def _prepare(*_args: object, **kwargs: object) -> tuple[str, str]:
        prepared_calls.append(dict(kwargs))
        kwargs["composition_result"].update(
            {
                "root_commit_sha": base_commit,
                "composed_commit_sha": composed_commit,
                "composed_tree_hash": composed_tree,
            }
        )
        return "/home/agent/worktrees/repo/rework-task-continuation", composed_commit

    monkeypatch.setattr(
        "evander_team_addon.providers._prepare_project_repo_task_worktree",
        _prepare,
    )
    monkeypatch.setattr(
        "evander_team_addon.providers._project_repo_task_dependency_git_sources",
        lambda *_args, **_kwargs: [],
    )
    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(tmp_path)},
        admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
    )

    result_state, rows, _metrics = _create_project_repo_git_work_contexts(
        context,
        repo,
        state=state,
        agent_id="replacement",
        session_id="replacement-session",
        project_id="project",
        project_repo={
            "managed_repo_id": MANAGED_REPO_ID,
            "guest_repo_name": "repo",
            "target_dir": "/home/agent/repo",
            "execution_backend": "libvirt_vm",
        },
        dispatch_id="dispatch-rework",
        tasks=[
            {
                "id": "rework-task",
                "project_id": "project",
                "owner_agent_id": "replacement",
                "git_base_branch": "main",
                "git_base_commit_sha": base_commit if pin_task_base else None,
            }
        ],
        base_branch="main",
    )
    result_state, retry_rows, _retry_metrics = _create_project_repo_git_work_contexts(
        context,
        repo,
        state=result_state,
        agent_id="replacement",
        session_id="replacement-session",
        project_id="project",
        project_repo={
            "managed_repo_id": MANAGED_REPO_ID,
            "guest_repo_name": "repo",
            "target_dir": "/home/agent/repo",
            "execution_backend": "libvirt_vm",
        },
        dispatch_id="dispatch-rework",
        tasks=[
            {
                "id": "rework-task",
                "project_id": "project",
                "owner_agent_id": "replacement",
                "git_base_branch": "main",
                "git_base_commit_sha": base_commit if pin_task_base else None,
            }
        ],
        base_branch="main",
    )

    assert (
        rejected_deliverable_continuation_git_source(
            state,
            task_id="rework-task",
        )
        is not None
    )
    assert len(rejected_deliverable_continuation_git_sources(state, task_id="rework-task")) == len(
        rejected_products
    )
    initial_prepared, retry_prepared = prepared_calls
    assert initial_prepared["base_ref"] == "main"
    assert initial_prepared["prior_base_commit"] == base_commit
    assert retry_prepared["prior_base_commit"] == composed_commit
    assert initial_prepared["pinned_base_commit"] == (
        base_commit if pin_task_base else None
    )
    assert initial_prepared["pinned_base_tree"] == (base_tree if pin_task_base else None)
    assert initial_prepared["dependency_git_sources"] == [
        {
            "dependency_task_id": "rework-task",
            "deliverable_id": deliverable_id,
            "branch": branch,
            "commit_sha": commit,
            "relationship_kind": "task_continuation",
            "pinned_ref": candidate_ref,
        }
        for deliverable_id, branch, commit, candidate_ref in rejected_products
    ]
    assert (
        WorkerTaskDependencyGitSource.model_validate(
            initial_prepared["dependency_git_sources"][0]
        ).relationship_kind
        == "task_continuation"
    )
    assert len(rows) == 1
    assert len(retry_rows) == 1
    work_context = next(
        context
        for context in result_state.git_work_contexts.values()
        if context.agent_id == "replacement"
    )
    assert work_context.base_branch == "main"
    assert work_context.base_commit == (base_commit if pin_task_base else composed_commit)
    assert len(work_context.dependency_git_sources) == len(rejected_products)
    assert all(
        source.relationship_kind == "task_continuation"
        for source in work_context.dependency_git_sources
    )
    assert work_context.dependency_composition_manifest is not None
    assert work_context.dependency_composition_manifest.composed_commit_sha == composed_commit


def test_rejected_continuation_uses_precomposition_manifest_root() -> None:
    state = _correction_state()
    root_commit = "1" * 40
    root_tree = "2" * 40
    dependency_commit = "3" * 40
    composed_commit = "4" * 40
    composed_tree = "5" * 40
    rejected_commit = "6" * 40
    state.tasks["rework-task"] = AgentTask(
        id="rework-task",
        owner_agent_id="owner",
        project_id="project",
        priority=1,
        state=TeamTaskState.IN_PROGRESS,
        title="Rework composed product",
        description="Retain the reviewed product and its original task root.",
        created_by_agent_id="lead",
        deliverable_ids=["rejected-deliverable"],
        git_base_branch="main",
        git_base_commit_sha=root_commit,
    )
    state.git_work_contexts["rejected-context"] = GitWorkContext(
        id="rejected-context",
        agent_id="owner",
        task_id="rework-task",
        agent_branch="agents/source/tasks/rework-task",
        base_branch="main",
        base_commit=composed_commit,
        commit_sha=rejected_commit,
        dependency_git_sources=[
            {
                "dependency_task_id": "dependency-task",
                "deliverable_id": "dependency-deliverable",
                "branch": "agents/dependency/tasks/dependency-task",
                "commit_sha": dependency_commit,
            }
        ],
        dependency_composition_manifest={
            "target_task_id": "rework-task",
            "managed_repo_id": MANAGED_REPO_ID,
            "directory_path": "root",
            "base_branch": "main",
            "base_commit_sha": root_commit,
            "base_tree_hash": root_tree,
            "dependency_git_sources": [
                {
                    "dependency_task_id": "dependency-task",
                    "deliverable_id": "dependency-deliverable",
                    "branch": "agents/dependency/tasks/dependency-task",
                    "commit_sha": dependency_commit,
                }
            ],
            "source_fingerprint": "composition-fingerprint",
            "frontier_commit_shas": [dependency_commit],
            "composed_commit_sha": composed_commit,
            "composed_tree_hash": composed_tree,
        },
        state="submitted",
    )
    state.deliverables["rejected-deliverable"] = Deliverable(
        id="rejected-deliverable",
        task_id="rework-task",
        producer_agent_id="owner",
        summary="Rejected composed candidate",
        state=TeamRecordState.REJECTED,
        acceptance_policy_version=2,
        git_refs={
            "branch_context_id": "rejected-context",
            "agent_branch": "agents/source/tasks/rework-task",
            "commit_sha": rejected_commit,
            "submitted_commit_sha": rejected_commit,
            "accepted_pinned_ref": "refs/evander/team-reviews/rejected-composed",
        },
    )

    source = rejected_deliverable_continuation_git_source(
        state,
        task_id="rework-task",
    )

    assert source is not None
    assert source.base_commit_sha == root_commit
    assert source.base_commit_sha != state.git_work_contexts["rejected-context"].base_commit


def _correction_state() -> TeamState:
    now = datetime(2026, 7, 30, tzinfo=UTC)
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
            managed_repo_id=MANAGED_REPO_ID,
            directory_path="root",
            base_branch="main",
        ),
    )
    state.tasks["source-task"] = AgentTask(
        id="source-task",
        owner_agent_id="owner",
        project_id="project",
        priority=1,
        state=TeamTaskState.ACCEPTED,
        title="Accepted source",
        description="Accepted candidate contribution.",
        created_by_agent_id="lead",
        deliverable_ids=["source-deliverable"],
    )
    state.tasks["correction-task"] = AgentTask(
        id="correction-task",
        owner_agent_id="owner",
        project_id="project",
        priority=1,
        state=TeamTaskState.PENDING,
        title="Correct the rejected candidate",
        description="Fix the human finding.",
        created_by_agent_id="lead",
        originating_task_id="source-task",
    )
    state.deliverables["source-deliverable"] = Deliverable(
        id="source-deliverable",
        task_id="source-task",
        producer_agent_id="owner",
        summary="Accepted candidate contribution",
        state=TeamRecordState.ACCEPTED,
    )
    requirement = HumanReviewRequirementSnapshot(
        directory_path="root",
        configured_group_ids=["reviewers"],
        initially_eligible_user_ids=["reviewer"],
    )
    manifest = ReleaseCandidateManifest(
        project_id="project",
        release_package_id="rejected-release",
        candidate_commit_sha=CANDIDATE_COMMIT,
        candidate_tree_hash=CANDIDATE_TREE,
        release_branch=RELEASE_BRANCH,
        deliverables=[
            ReleaseCandidateDeliverableEvidence(
                deliverable_id="source-deliverable",
                feature_branch=FEATURE_BRANCH,
                feature_commit_sha=FEATURE_COMMIT,
            )
        ],
        human_review_requirement=requirement,
    )
    state.release_packages["rejected-release"] = ReleasePackage(
        id="rejected-release",
        project_id="project",
        deliverable_ids=["source-deliverable"],
        release_branch=RELEASE_BRANCH,
        candidate_commit_sha=CANDIDATE_COMMIT,
        candidate_tree_hash=CANDIDATE_TREE,
        manifest=manifest,
        state=TeamRecordState.REJECTED,
    )
    state.human_review_requests["human-review"] = HumanReviewRequest(
        id="human-review",
        release_package_id="rejected-release",
        candidate_commit_sha=CANDIDATE_COMMIT,
        candidate_tree_hash=CANDIDATE_TREE,
        requirement_snapshot=requirement,
        state="changes_requested",
        ready_at=now,
        completed_at=now,
    )
    state.human_review_findings["finding"] = HumanReviewFinding(
        id="finding",
        source_human_review_request_id="human-review",
        text="Fix the candidate.",
        target_deliverable_ids=["source-deliverable"],
        correction_task_id="correction-task",
    )
    return state


def _verification_correction_state() -> TeamState:
    state = _correction_state()
    state.human_review_findings.clear()
    state.human_review_requests.clear()
    release = state.release_packages["rejected-release"]
    release.gate_failure = ReleaseGateFailure(
        stage="verification",
        kind="candidate",
        source_id="verification-run",
        message="release tests failed",
    )
    state.release_verification_runs["verification-run"] = ReleaseVerificationRun(
        id="verification-run",
        release_package_id=release.id,
        candidate_commit_sha=CANDIDATE_COMMIT,
        session_id="verification-session",
        state="failed",
        failure_kind="candidate",
        correction_task_id="correction-task",
        error="release tests failed",
    )
    return state


def _release_review_correction_state() -> TeamState:
    state = _correction_state()
    state.human_review_findings.clear()
    state.human_review_requests.clear()
    release = state.release_packages["rejected-release"]
    release.gate_failure = ReleaseGateFailure(
        stage="review",
        kind="rejected",
        source_id="release-review",
        message="PLC device mappings persist opcTagId 0.",
    )
    release.review_correction_task_id = "correction-task"
    state.review_requests["release-review"] = ReviewRequest(
        id="release-review",
        release_package_id=release.id,
        candidate_commit_sha=CANDIDATE_COMMIT,
        reviewed_commit_sha=CANDIDATE_COMMIT,
        reviewer_agent_id="lead",
        state=TeamRecordState.REJECTED,
        findings=["PLC device mappings persist opcTagId 0."],
    )
    return state


def test_correction_source_is_derived_from_rejected_release_not_main() -> None:
    source = human_review_correction_git_source(
        _correction_state(),
        task_id="correction-task",
    )

    assert source is not None
    assert source.release_branch == RELEASE_BRANCH
    assert source.candidate_commit_sha == CANDIDATE_COMMIT
    assert source.candidate_tree_hash == CANDIDATE_TREE
    assert [item.model_dump(mode="json") for item in source.dependency_git_sources] == [
        {
            "dependency_task_id": "source-task",
            "deliverable_id": "source-deliverable",
            "branch": FEATURE_BRANCH,
            "commit_sha": FEATURE_COMMIT,
            "relationship_kind": "task_dependency",
            "pinned_ref": None,
        }
    ]


def test_verification_correction_source_is_derived_from_failed_candidate() -> None:
    source = release_verification_correction_git_source(
        _verification_correction_state(),
        task_id="correction-task",
    )

    assert source is not None
    assert source.release_verification_run_id == "verification-run"
    assert source.release_package_id == "rejected-release"
    assert source.release_branch == RELEASE_BRANCH
    assert source.candidate_commit_sha == CANDIDATE_COMMIT
    assert source.candidate_tree_hash == CANDIDATE_TREE
    assert [item.model_dump(mode="json") for item in source.dependency_git_sources] == [
        {
            "dependency_task_id": "source-task",
            "deliverable_id": "source-deliverable",
            "branch": FEATURE_BRANCH,
            "commit_sha": FEATURE_COMMIT,
            "relationship_kind": "task_dependency",
            "pinned_ref": None,
        }
    ]


def test_release_review_correction_source_is_derived_from_rejected_candidate() -> None:
    source = release_review_correction_git_source(
        _release_review_correction_state(),
        task_id="correction-task",
    )

    assert source is not None
    assert source.release_review_request_id == "release-review"
    assert source.release_package_id == "rejected-release"
    assert source.release_branch == RELEASE_BRANCH
    assert source.candidate_commit_sha == CANDIDATE_COMMIT
    assert source.candidate_tree_hash == CANDIDATE_TREE
    assert [item.model_dump(mode="json") for item in source.dependency_git_sources] == [
        {
            "dependency_task_id": "source-task",
            "deliverable_id": "source-deliverable",
            "branch": FEATURE_BRANCH,
            "commit_sha": FEATURE_COMMIT,
            "relationship_kind": "task_dependency",
            "pinned_ref": None,
        }
    ]


def test_verification_correction_source_requires_matching_gate_failure() -> None:
    state = _verification_correction_state()
    state.release_packages["rejected-release"].gate_failure = ReleaseGateFailure(
        stage="verification",
        kind="candidate",
        source_id="different-run",
        message="different failure",
    )

    with pytest.raises(ValueError, match="active candidate failure"):
        release_verification_correction_git_source(
            state,
            task_id="correction-task",
        )


def test_verification_correction_link_and_release_owner_round_trip(tmp_path) -> None:
    state = _verification_correction_state()
    state.release_packages["rejected-release"].owner_agent_id = "lead"
    repo = TeamStateRepository(tmp_path)

    repo.update(lambda _state: state, expected_revision=0)
    reloaded = repo.read()

    assert (
        reloaded.release_verification_runs["verification-run"].correction_task_id
        == "correction-task"
    )
    assert reloaded.release_packages["rejected-release"].owner_agent_id == "lead"


def test_release_review_correction_link_round_trips(tmp_path) -> None:
    state = _release_review_correction_state()
    repo = TeamStateRepository(tmp_path)

    repo.update(lambda _state: state, expected_revision=0)
    reloaded = repo.read()

    assert (
        reloaded.release_packages["rejected-release"].review_correction_task_id == "correction-task"
    )


def test_correction_source_fails_closed_on_request_release_tree_mismatch() -> None:
    state = _correction_state()
    state.release_packages["rejected-release"].candidate_tree_hash = "f" * 40

    with pytest.raises(ValueError, match="does not match rejected release.*tree"):
        human_review_correction_git_source(
            state,
            task_id="correction-task",
        )


def test_correction_source_verifies_authoritative_ref_commit_and_tree() -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _ManagedGit:
        def resolve_snapshot(self, *args: object, **kwargs: object) -> dict[str, object]:
            calls.append((args, kwargs))
            return {
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            }

    source = _verified_human_review_correction_git_source(
        EffectRuntimeContext(
            resolved_config={"runtime_dir": "/runtime"},
            admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
        ),
        state=_correction_state(),
        task_id="correction-task",
        managed_repo_id=MANAGED_REPO_ID,
    )

    assert source is not None
    assert calls == [
        (
            (MANAGED_REPO_ID, "root"),
            {
                "source_ref": RELEASE_BRANCH,
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            },
        )
    ]


def test_verification_correction_source_verifies_authoritative_snapshot() -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _ManagedGit:
        def resolve_snapshot(self, *args: object, **kwargs: object) -> dict[str, object]:
            calls.append((args, kwargs))
            return {
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            }

    source = _verified_rejected_candidate_git_source(
        EffectRuntimeContext(
            resolved_config={"runtime_dir": "/runtime"},
            admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
        ),
        state=_verification_correction_state(),
        task_id="correction-task",
        managed_repo_id=MANAGED_REPO_ID,
    )

    assert source is not None
    assert source.release_package_id == "rejected-release"
    assert calls == [
        (
            (MANAGED_REPO_ID, "root"),
            {
                "source_ref": RELEASE_BRANCH,
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            },
        )
    ]


def test_release_review_correction_source_verifies_authoritative_snapshot() -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _ManagedGit:
        def resolve_snapshot(self, *args: object, **kwargs: object) -> dict[str, object]:
            calls.append((args, kwargs))
            return {
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            }

    source = _verified_rejected_candidate_git_source(
        EffectRuntimeContext(
            resolved_config={"runtime_dir": "/runtime"},
            admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
        ),
        state=_release_review_correction_state(),
        task_id="correction-task",
        managed_repo_id=MANAGED_REPO_ID,
    )

    assert source is not None
    assert source.release_package_id == "rejected-release"
    assert calls == [
        (
            (MANAGED_REPO_ID, "root"),
            {
                "source_ref": RELEASE_BRANCH,
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            },
        )
    ]


def test_libvirt_correction_worktree_fetches_and_checks_pinned_candidate() -> None:
    class _VmExecutor:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def vm_exec(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(dict(kwargs))
            return {
                "success": True,
                "stdout": f"EVANDER_BASE_COMMIT={CANDIDATE_COMMIT}\n",
                "stderr": "",
                "exit_code": 0,
            }

    executor = _VmExecutor()

    class _SessionLookup:
        def read_all_sessions(self) -> dict[str, object]:
            return {
                "owner-session": SimpleNamespace(
                    session_id="owner-session",
                    vm_id="vm-owner",
                    vm_provider="libvirt",
                    directory_path="root",
                    metadata={},
                )
            }

    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": "/runtime"},
        vm_executor=executor,
        session_lookup=_SessionLookup(),
    )

    path, base_commit = _prepare_project_repo_task_worktree(
        context,
        project_repo={
            "guest_repo_name": "repo",
            "target_dir": "/home/agent/repo",
            "execution_backend": "libvirt_vm",
        },
        session_id="owner-session",
        task_id="correction-task",
        agent_branch="agents/owner-session/tasks/correction-task",
        base_ref=RELEASE_BRANCH,
        prior_base_commit=CANDIDATE_COMMIT,
        pinned_base_commit=CANDIDATE_COMMIT,
        pinned_base_tree=CANDIDATE_TREE,
    )

    assert path.endswith(f"/correction-task-63eb884d-{CANDIDATE_COMMIT[:12]}")
    assert base_commit == CANDIDATE_COMMIT
    command = str(executor.calls[0]["command"])
    syntax = subprocess.run(
        ["bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert 'git fetch --no-tags origin "refs/heads/$base"' in command
    assert f"pinned_base_commit={CANDIDATE_COMMIT}" in command
    assert f"pinned_base_tree={CANDIDATE_TREE}" in command
    assert 'git worktree add -b "$branch" "$target" "$base_commitish"' in command
    head_capture = 'head_before="$(git -C "$target" rev-parse HEAD)"'
    pinned_ancestry_check = (
        'git -C "$target" merge-base --is-ancestor "$pinned_base_commit" "$head_before"'
    )
    assert pinned_ancestry_check in command
    assert command.index(head_capture) < command.index(pinned_ancestry_check)
    assert command.index(pinned_ancestry_check) < command.index("current_dependency_provenance=")


def test_container_correction_accepts_composed_base_and_validates_candidate_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_commit = "f" * 40
    fix_tree = "9" * 40
    calls: list[dict[str, object]] = []
    composition_root_commit = CANDIDATE_COMMIT

    class _ContainerBackend:
        def prepare_task_worktree(self, **kwargs: object) -> SimpleNamespace:
            calls.append(dict(kwargs))
            return SimpleNamespace(
                target_dir="/home/agent/worktrees/repo/correction-task",
                branch=kwargs["branch"],
                commit_sha=fix_commit,
                base_commit_sha=fix_commit,
                composition_root_commit_sha=composition_root_commit,
                composed_commit_sha=fix_commit,
                composed_tree_hash=fix_tree,
                created=True,
            )

    monkeypatch.setattr(
        "evander_team_addon.providers.selected_execution_backend_for_target",
        lambda *_args, **_kwargs: ("container_worker_vm", SimpleNamespace()),
    )
    monkeypatch.setattr(
        "evander_team_addon.providers.container_backend",
        lambda *_args, **_kwargs: _ContainerBackend(),
    )
    context = SimpleNamespace(metadata={"directory_path": "root"})
    project_repo = {
        "guest_repo_name": "repo",
        "target_dir": "/home/agent/repo",
        "execution_backend": "container_worker_vm",
    }
    dependency_sources = [
        {
            "dependency_task_id": "fix-task",
            "deliverable_id": "fix-deliverable",
            "branch": "agents/owner/tasks/fix-task",
            "commit_sha": fix_commit,
            "relationship_kind": "required_contribution",
        }
    ]
    composition_result: dict[str, object] = {}

    path, base_commit = _prepare_project_repo_task_worktree(
        context,
        project_repo=project_repo,
        session_id="owner-session",
        task_id="correction-task",
        agent_branch="agents/owner/tasks/correction-task",
        base_ref=RELEASE_BRANCH,
        prior_base_commit=CANDIDATE_COMMIT,
        pinned_base_commit=CANDIDATE_COMMIT,
        pinned_base_tree=CANDIDATE_TREE,
        dependency_git_sources=dependency_sources,
        composition_result=composition_result,
    )

    assert path == "/home/agent/worktrees/repo/correction-task"
    assert base_commit == fix_commit
    assert composition_result == {
        "root_commit_sha": CANDIDATE_COMMIT,
        "composed_commit_sha": fix_commit,
        "composed_tree_hash": fix_tree,
    }
    assert calls[0]["expected_base_commit_sha"] == CANDIDATE_COMMIT
    assert calls[0]["expected_base_tree_hash"] == CANDIDATE_TREE

    _prepare_project_repo_task_worktree(
        context,
        project_repo=project_repo,
        session_id="owner-session",
        task_id="correction-task",
        agent_branch="agents/owner/tasks/correction-task",
        base_ref=RELEASE_BRANCH,
        prior_base_commit=fix_commit,
        pinned_base_commit=CANDIDATE_COMMIT,
        pinned_base_tree=CANDIDATE_TREE,
        dependency_git_sources=dependency_sources,
    )

    assert calls[1]["expected_base_commit_sha"] == fix_commit
    assert calls[1]["expected_base_tree_hash"] is None

    composition_root_commit = "a" * 40
    with pytest.raises(
        DependencyGitPreparationError,
        match="composition root .* not pinned rejected candidate",
    ):
        _prepare_project_repo_task_worktree(
            context,
            project_repo=project_repo,
            session_id="owner-session",
            task_id="correction-task",
            agent_branch="agents/owner/tasks/correction-task",
            base_ref=RELEASE_BRANCH,
            prior_base_commit=CANDIDATE_COMMIT,
            pinned_base_commit=CANDIDATE_COMMIT,
            pinned_base_tree=CANDIDATE_TREE,
            dependency_git_sources=dependency_sources,
        )


def test_correction_git_context_records_candidate_base_and_manifest_sources(
    tmp_path,
) -> None:
    state = _correction_state()
    state.agent_runtimes["owner"] = AgentRuntime(
        agent_id="owner",
        active_dispatch_id="dispatch",
    )
    repo = TeamStateRepository(tmp_path)
    state = repo.update(lambda _state: state, expected_revision=0)

    class _ManagedGit:
        def resolve_snapshot(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            }

    class _VmExecutor:
        def vm_exec(self, **_kwargs: object) -> dict[str, object]:
            return {
                "success": True,
                "stdout": f"EVANDER_BASE_COMMIT={CANDIDATE_COMMIT}\n",
                "stderr": "",
                "exit_code": 0,
            }

    class _SessionLookup:
        def read_all_sessions(self) -> dict[str, object]:
            return {
                "owner-session": SimpleNamespace(
                    session_id="owner-session",
                    vm_id="vm-owner",
                    vm_provider="libvirt",
                    directory_path="root",
                    metadata={},
                )
            }

    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(tmp_path)},
        vm_executor=_VmExecutor(),
        session_lookup=_SessionLookup(),
        admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
    )
    result_state, rows, _metrics = _create_project_repo_git_work_contexts(
        context,
        repo,
        state=state,
        agent_id="owner",
        session_id="owner-session",
        project_id="project",
        project_repo={
            "managed_repo_id": MANAGED_REPO_ID,
            "guest_repo_name": "repo",
            "target_dir": "/home/agent/repo",
            "execution_backend": "libvirt_vm",
        },
        dispatch_id="dispatch",
        tasks=[
            {
                "id": "correction-task",
                "project_id": "project",
                "owner_agent_id": "owner",
            }
        ],
        base_branch="main",
    )

    assert len(rows) == 1
    context_model = next(iter(result_state.git_work_contexts.values()))
    assert context_model.base_branch == RELEASE_BRANCH
    assert context_model.base_commit == CANDIDATE_COMMIT
    assert [item.model_dump(mode="json") for item in context_model.dependency_git_sources] == [
        {
            "dependency_task_id": "source-task",
            "deliverable_id": "source-deliverable",
            "branch": FEATURE_BRANCH,
            "commit_sha": FEATURE_COMMIT,
            "relationship_kind": "task_dependency",
            "pinned_ref": None,
        }
    ]


def test_correction_git_context_records_manifest_and_reuses_composed_base(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _verification_correction_state()
    fix_commit = "f" * 40
    fix_tree = "9" * 40
    state.tasks["fix-task"] = AgentTask(
        id="fix-task",
        owner_agent_id="owner",
        project_id="project",
        priority=1,
        state=TeamTaskState.FINISHED,
        title="Fix release verification",
        description="Correct the rejected candidate.",
        created_by_agent_id="lead",
        deliverable_ids=["fix-deliverable"],
    )
    state.deliverables["fix-deliverable"] = Deliverable(
        id="fix-deliverable",
        task_id="fix-task",
        producer_agent_id="owner",
        summary="Release verification correction",
        state=TeamRecordState.ACCEPTED,
        git_refs={
            "agent_branch": "agents/owner/tasks/fix-task",
            "commit_sha": fix_commit,
            "submitted_commit_sha": fix_commit,
            "reviewed_commit_sha": fix_commit,
        },
    )
    correction_task = state.tasks["correction-task"]
    correction_task.dependency_task_ids = ["fix-task"]
    correction_task.integration_boundary = True
    correction_task.deliverable_contract = TaskDeliverableContract(
        require_git_provenance=True,
        required_contribution_task_ids=["fix-task"],
    )
    state.agent_runtimes["owner"] = AgentRuntime(
        agent_id="owner",
        active_dispatch_id="dispatch",
    )
    repo = TeamStateRepository(tmp_path)
    state = repo.update(lambda _state: state, expected_revision=0)

    class _ManagedGit:
        def resolve_snapshot(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "commit_sha": CANDIDATE_COMMIT,
                "tree_hash": CANDIDATE_TREE,
            }

        def resolve_branch_commit(self, *args: object, **kwargs: object) -> str:
            return fix_commit

        def reduce_commit_frontier(self, *args: object, **kwargs: object) -> dict[str, object]:
            assert kwargs["base_commit_sha"] == CANDIDATE_COMMIT
            assert kwargs["sources"] == [
                {
                    "dependency_task_id": "fix-task",
                    "deliverable_id": "fix-deliverable",
                    "branch": "agents/owner/tasks/fix-task",
                    "commit_sha": fix_commit,
                    "relationship_kind": "required_contribution",
                }
            ]
            return {
                "base_tree_hash": CANDIDATE_TREE,
                "frontier_commit_shas": [fix_commit],
                "reduced_source_deliverable_ids": [],
            }

    prepared_calls: list[dict[str, object]] = []

    def _prepare(*_args: object, **kwargs: object) -> tuple[str, str]:
        prepared_calls.append(dict(kwargs))
        composition_result = kwargs["composition_result"]
        assert isinstance(composition_result, dict)
        composition_result.update(
            {
                "root_commit_sha": CANDIDATE_COMMIT,
                "composed_commit_sha": fix_commit,
                "composed_tree_hash": fix_tree,
            }
        )
        return "/home/agent/worktrees/repo/correction-task", fix_commit

    monkeypatch.setattr(
        "evander_team_addon.providers._prepare_project_repo_task_worktree",
        _prepare,
    )
    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(tmp_path)},
        admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
    )
    result_state, rows, _metrics = _create_project_repo_git_work_contexts(
        context,
        repo,
        state=state,
        agent_id="owner",
        session_id="owner-session",
        project_id="project",
        project_repo={
            "managed_repo_id": MANAGED_REPO_ID,
            "guest_repo_name": "repo",
            "target_dir": "/home/agent/repo",
            "execution_backend": "libvirt_vm",
        },
        dispatch_id="dispatch",
        tasks=[
            {
                "id": "correction-task",
                "project_id": "project",
                "owner_agent_id": "owner",
            }
        ],
        base_branch="main",
    )

    assert len(rows) == 1
    prepared = prepared_calls[0]
    assert prepared["prior_base_commit"] == CANDIDATE_COMMIT
    assert prepared["pinned_base_commit"] == CANDIDATE_COMMIT
    assert prepared["dependency_git_sources"] == [
        {
            "dependency_task_id": "fix-task",
            "deliverable_id": "fix-deliverable",
            "branch": "agents/owner/tasks/fix-task",
            "commit_sha": fix_commit,
            "relationship_kind": "required_contribution",
        }
    ]
    context_model = next(iter(result_state.git_work_contexts.values()))
    assert context_model.base_commit == fix_commit
    assert [
        source.deliverable_id for source in context_model.dependency_git_sources
    ] == ["fix-deliverable", "source-deliverable"]
    manifest = context_model.dependency_composition_manifest
    assert manifest is not None
    assert [
        source.model_dump(mode="json") for source in manifest.dependency_git_sources
    ] == [
        {
            "dependency_task_id": "fix-task",
            "deliverable_id": "fix-deliverable",
            "branch": "agents/owner/tasks/fix-task",
            "commit_sha": fix_commit,
            "relationship_kind": "required_contribution",
            "pinned_ref": None,
        }
    ]
    assert manifest.base_commit_sha == CANDIDATE_COMMIT
    assert manifest.composed_commit_sha == fix_commit
    assert manifest.composed_tree_hash == fix_tree

    def _start_next_dispatch(current: TeamState) -> TeamState:
        current.agent_runtimes["owner"].active_dispatch_id = "dispatch-next"
        return current

    state = repo.update(
        _start_next_dispatch,
        expected_revision=result_state.revision,
    )
    _create_project_repo_git_work_contexts(
        context,
        repo,
        state=state,
        agent_id="owner",
        session_id="owner-session",
        project_id="project",
        project_repo={
            "managed_repo_id": MANAGED_REPO_ID,
            "guest_repo_name": "repo",
            "target_dir": "/home/agent/repo",
            "execution_backend": "libvirt_vm",
        },
        dispatch_id="dispatch-next",
        tasks=[
            {
                "id": "correction-task",
                "project_id": "project",
                "owner_agent_id": "owner",
            }
        ],
        base_branch="main",
    )

    assert prepared_calls[1]["prior_base_commit"] == fix_commit
    assert prepared_calls[1]["pinned_base_commit"] == CANDIDATE_COMMIT


def test_operator_preparation_recovery_creates_one_fresh_inbox_and_due_wake(
    tmp_path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.update(lambda _state: _correction_state(), expected_revision=0)

    def _record(state: TeamState) -> TeamState:
        result: dict[str, object] = {}
        blocker = record_task_preparation_failure(
            state,
            task_id="correction-task",
            reporter_agent_id="owner",
            error="candidate checkout unavailable",
            result=result,
        )
        blocker.reason_code = "source_provenance_mismatch"
        blocker.route = TeamBlockerRoute.OPERATOR
        blocker.terminal_reference_ids = ["operator-repair-required"]
        return state

    state = repo.update(_record)
    blocker = next(iter(state.blockers.values()))
    resolved, result = TeamAdminService(repo).resolve_terminal_blocker_as_operator(
        blocker_id=blocker.id,
        note="The pinned candidate source was repaired.",
        operator_actor_id="operator:test",
        expected_revision=state.revision,
    )

    recovery_items = [
        item
        for item in resolved.inbox.values()
        if item.payload.get("kind") == "rejected_candidate_correction_preparation_recovered"
    ]
    assert len(recovery_items) == 1
    assert recovery_items[0].recipient_agent_id == "owner"
    assert recovery_items[0].task_id == "correction-task"
    assert result["created_inbox_item_ids"] == [recovery_items[0].id]
    assert resolved.agent_runtimes["owner"].next_wake_at is None
    assert len(result["events"]) == 1
    assert result["events"][0]["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    assert result["events"][0]["delay_seconds"] == 0
    assert result["events"][0]["payload"]["agent_id"] == "owner"
    wake_outbox = repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE)
    assert len(wake_outbox) == 1
    assert wake_outbox[0].payload["delay_seconds"] == 0
    assert wake_outbox[0].payload["payload"]["agent_id"] == "owner"

    def _replay(state: TeamState) -> TeamState:
        replay_result: dict[str, object] = {"events": []}
        first = create_human_correction_preparation_recovery_inbox(
            state,
            blocker=state.blockers[blocker.id],
            result=replay_result,
        )
        second = create_human_correction_preparation_recovery_inbox(
            state,
            blocker=state.blockers[blocker.id],
            result=replay_result,
        )
        assert first is second
        assert replay_result["events"] == []
        return state

    replayed = repo.update(_replay)
    assert (
        len(
            [
                item
                for item in replayed.inbox.values()
                if item.payload.get("kind") == "rejected_candidate_correction_preparation_recovered"
            ]
        )
        == 1
    )
    assert [
        record.id
        for record in repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE)
    ] == [wake_outbox[0].id]


def test_release_review_correction_preparation_recovery_uses_candidate_route() -> None:
    state = _release_review_correction_state()
    result: dict[str, object] = {"events": []}
    blocker = record_task_preparation_failure(
        state,
        task_id="correction-task",
        reporter_agent_id="owner",
        error="candidate checkout unavailable",
        result=result,
    )

    recovery_result: dict[str, object] = {"events": []}
    item = create_rejected_candidate_correction_preparation_recovery_inbox(
        state,
        blocker=blocker,
        result=recovery_result,
    )

    assert item is not None
    assert item.task_id == "correction-task"
    assert item.recipient_agent_id == "owner"
    assert item.payload["kind"] == "rejected_candidate_correction_preparation_recovered"
    assert len(recovery_result["events"]) == 1
    assert recovery_result["events"][0]["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE


def test_correction_preparation_generation_changes_when_provenance_is_repaired() -> None:
    state = _correction_state()
    release = state.release_packages["rejected-release"]
    release.candidate_tree_hash = "f" * 40
    broken_generation = task_preparation_generation(state, "correction-task")

    release.candidate_tree_hash = CANDIDATE_TREE
    repaired_generation = task_preparation_generation(state, "correction-task")

    assert repaired_generation != broken_generation


def test_correction_preparation_generation_changes_when_manifest_is_repaired() -> None:
    state = _correction_state()
    release = state.release_packages["rejected-release"]
    manifest = release.manifest
    assert manifest is not None
    release.manifest = None
    missing_manifest_generation = task_preparation_generation(
        state,
        "correction-task",
    )

    release.manifest = manifest
    repaired_manifest_generation = task_preparation_generation(
        state,
        "correction-task",
    )

    assert repaired_manifest_generation != missing_manifest_generation


def test_correction_preparation_generation_changes_when_task_base_is_repaired() -> None:
    state = _release_review_correction_state()
    task = state.tasks["correction-task"]
    task.git_base_branch = "team/team/releases/previous"
    stale_generation = task_preparation_generation(state, task.id)

    task.git_base_branch = RELEASE_BRANCH
    repaired_generation = task_preparation_generation(state, task.id)

    assert repaired_generation != stale_generation
