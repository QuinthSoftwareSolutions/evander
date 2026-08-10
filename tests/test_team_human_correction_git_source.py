from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander_team_addon.models import (
    AgentRuntime,
    AgentTask,
    Deliverable,
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
)
from evander_team_addon.providers import (
    _create_project_repo_git_work_contexts,
    _prepare_project_repo_task_worktree,
    _verified_human_review_correction_git_source,
    _verified_rejected_candidate_git_source,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.correction_git_source import (
    human_review_correction_git_source,
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
        }
    ]


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
