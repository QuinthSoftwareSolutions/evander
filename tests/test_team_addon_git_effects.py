from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from evander.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from evander.effects.providers.agent_create import AgentCreateEffectProvider
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander.events.models import WorkflowNodeEnvelope
from evander_team_addon.models import (
    AgentInboxItem,
    Deliverable,
    DeliverableGitRefs,
    EphemeralAgentPolicy,
    GitPromotionRecord,
    ProjectGitConfig,
    ProjectGoal,
    ProjectReleasePolicy,
    ProjectStatus,
    ReleaseCandidateDeliverableEvidence,
    ReleaseCandidateManifest,
    ReleasePackage,
    ReleaseScopeValidation,
    ReleaseVerificationCommand,
    ReleaseVerificationCommandResult,
    ReleaseVerificationRun,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
)
from evander_team_addon.provider_schemas import (
    TeamConsolidateGitBranchesEffectDefinition,
    TeamMarkReleasePublishedEffectDefinition,
    TeamPublishReviewBranchEffectDefinition,
    TeamRecordGitPromotionEffectDefinition,
    TeamVerifyReleaseCandidateEffectDefinition,
)
from evander_team_addon.providers import (
    TeamConsolidateGitBranchesEffectProvider,
    TeamMarkReleasePublishedEffectProvider,
    TeamPublishReviewBranchEffectProvider,
    TeamRecordGitPromotionEffectProvider,
    TeamVerifyReleaseCandidateEffectProvider,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.release_gate_service import TeamReleaseGateService


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _seed_team(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=1,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=0),
        expected_revision=3,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=4,
    )
    return repo


def _seed_review_promotion(repo: TeamStateRepository) -> None:
    def _mutate(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
        )
        state.deliverables["deliverable-1"] = Deliverable(
            id="deliverable-1",
            task_id="task",
            producer_agent_id="backend",
            summary="A slice",
            state=TeamRecordState.PENDING,
        )
        state.tasks["task"].deliverable_ids = ["deliverable-1"]
        state.git_promotions["promotion-1"] = GitPromotionRecord(
            id="promotion-1",
            project_id="project",
            task_id="task",
            deliverable_id="deliverable-1",
            actor_agent_id="backend",
            source_branch="agents/eph-1/ephemeral-1",
            target_branch="team/team/reviews/ephemeral-1",
            promotion_kind="review",
        )
        return state

    repo.update(_mutate)


def _record_promotion_provider(
    tmp_path: Path,
    **config: object,
) -> dict[str, object]:
    provider = TeamRecordGitPromotionEffectProvider()
    provider.initialize(
        TeamRecordGitPromotionEffectDefinition(
            id="record",
            display_name="Record",
            type="effect:team:record-git-promotion",
            **config,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    return provider.execute()


def test_record_git_promotion_success_attaches_git_refs(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _seed_review_promotion(repo)

    result = _record_promotion_provider(
        tmp_path,
        promotion_id="promotion-1",
        success=True,
        commit_sha="deadbeef",
        result_outputs={"review_branch": "team/team/reviews/ephemeral-1"},
    )

    assert result["success"] is True
    assert result["promotion_state"] == "succeeded"
    state = repo.read()
    promotion = state.git_promotions["promotion-1"]
    assert promotion.state == "succeeded"
    assert promotion.commit_sha == "deadbeef"
    refs = state.deliverables["deliverable-1"].git_refs
    assert refs.review_branch == "team/team/reviews/ephemeral-1"
    assert refs.agent_branch == "agents/eph-1/ephemeral-1"
    assert refs.commit_sha is None
    assert refs.reviewed_commit_sha == "deadbeef"
    assert "promotion-1" in refs.promotion_record_ids


def test_record_git_promotion_accepts_legacy_outputs_config(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _seed_review_promotion(repo)
    provider = TeamRecordGitPromotionEffectProvider()
    provider.initialize(
        WorkflowNodeEnvelope(
            id="record",
            display_name="Record",
            type="effect:team:record-git-promotion",
            config={
                "promotion_id": "promotion-1",
                "success": True,
                "commit_sha": "deadbeef",
                "outputs": {"review_branch": "team/team/reviews/ephemeral-1"},
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert repo.read().git_promotions["promotion-1"].outputs == {
        "review_branch": "team/team/reviews/ephemeral-1"
    }


def test_record_git_promotion_failure_blocks_and_notifies(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _seed_review_promotion(repo)

    result = _record_promotion_provider(
        tmp_path,
        promotion_id="promotion-1",
        success=False,
        error="branch already exists",
    )

    assert result["success"] is True
    assert result["promotion_state"] == "failed"
    assert result["error"] == "branch already exists"
    assert len(result["created_inbox_item_ids"]) == 1
    state = repo.read()
    assert state.git_promotions["promotion-1"].state == "failed"
    assert state.deliverables["deliverable-1"].state == TeamRecordState.BLOCKED
    inbox_id = result["created_inbox_item_ids"][0]
    notice = state.inbox[inbox_id]
    # backend reports to lead, so the failure notice goes upward to lead.
    assert notice.recipient_agent_id == "lead"
    assert notice.item_type == TeamInboxItemType.PROGRESS_REPORT
    assert notice.payload["kind"] == "git_promotion_failed"
    assert notice.payload["promotion_id"] == "promotion-1"


def test_record_release_promotion_success_publishes_canonical_push(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            allow_ungated=True
        )
        state.projects["project"].git.directory_path = "root/team-a"
        state.git_promotions["release-promotion"] = GitPromotionRecord(
            id="release-promotion",
            project_id="project",
            release_package_id="release-1",
            actor_agent_id="lead",
            source_branch="team/team/features/task-backend",
            target_branch="team/team/releases/release-1",
            promotion_kind="release",
        )
        state.release_packages["release-1"].git_promotion_record_ids = ["release-promotion"]
        return state

    repo.update(_mutate)

    result = _record_promotion_provider(
        tmp_path,
        promotion_id="release-promotion",
        success=True,
        commit_sha="feedface",
    )

    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    assert len(result["published_event_ids"]) == 1
    state = repo.read()
    canonical_id = result["git_promotion_event_ids"][0]
    canonical = state.git_promotions[canonical_id]
    assert canonical.promotion_kind == "canonical_push"
    assert canonical.source_branch == "team/team/releases/release-1"
    assert canonical.target_branch == "releases/project/release-1"
    release = state.release_packages["release-1"]
    assert release.git_promotion_record_ids == ["release-promotion", canonical_id]

    events = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert len(events) == 1
    assert events[0].event_type == "team.git.promotion_requested"
    assert events[0].payload["promotion_id"] == canonical_id
    assert events[0].payload["promotion_kind"] == "canonical_push"
    assert events[0].payload["source_branch"] == "team/team/releases/release-1"
    assert events[0].payload["target_branch"] == "releases/project/release-1"
    assert events[0].payload["managed_repo_id"] == "mr_0123456789abcdefabcd"
    assert events[0].payload["directory_path"] == "root/team-a"
    assert events[0].payload["expected_commit_sha"] == "feedface"
    assert "bare_repo_name" not in events[0].payload
    assert "canonical_origin_url" not in events[0].payload


def test_record_canonical_promotion_success_updates_record(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        state.git_promotions["canonical-promotion"] = GitPromotionRecord(
            id="canonical-promotion",
            project_id="project",
            release_package_id="release-1",
            actor_agent_id="lead",
            source_branch="team/team/releases/release-1",
            target_branch="releases/project/release-1",
            promotion_kind="canonical_push",
        )
        state.release_packages["release-1"].git_promotion_record_ids = ["canonical-promotion"]
        return state

    repo.update(_mutate)

    result = _record_promotion_provider(
        tmp_path,
        promotion_id="canonical-promotion",
        success=True,
        commit_sha="cafef00d",
        result_outputs={"origin_branch": "releases/project/release-1"},
    )

    assert result["success"] is True
    assert result["promotion_state"] == "succeeded"
    state = repo.read()
    promotion = state.git_promotions["canonical-promotion"]
    assert promotion.state == "succeeded"
    assert promotion.commit_sha == "cafef00d"
    assert promotion.outputs == {"origin_branch": "releases/project/release-1"}
    release = state.release_packages["release-1"]
    assert release.canonical_branch == "releases/project/release-1"
    assert release.git_promotion_record_ids == ["canonical-promotion"]


def test_record_canonical_promotion_failure_blocks_release(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        state.git_promotions["canonical-promotion"] = GitPromotionRecord(
            id="canonical-promotion",
            project_id="project",
            release_package_id="release-1",
            actor_agent_id="lead",
            source_branch="team/team/releases/release-1",
            target_branch="releases/project/release-1",
            promotion_kind="canonical_push",
        )
        state.release_packages["release-1"].git_promotion_record_ids = ["canonical-promotion"]
        return state

    repo.update(_mutate)

    result = _record_promotion_provider(
        tmp_path,
        promotion_id="canonical-promotion",
        success=False,
        error="push rejected",
    )

    assert result["success"] is True
    assert result["promotion_state"] == "failed"
    state = repo.read()
    assert state.git_promotions["canonical-promotion"].state == "failed"
    release = state.release_packages["release-1"]
    assert release.state == TeamRecordState.BLOCKED
    assert release.error == "push rejected"
    notice = state.inbox[result["created_inbox_item_ids"][0]]
    assert notice.recipient_agent_id == "manager"
    assert notice.payload["kind"] == "git_promotion_failed"
    assert notice.payload["promotion_kind"] == "canonical_push"


def _seed_release(repo: TeamStateRepository) -> None:
    def _mutate(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
        )
        state.deliverables["deliverable-1"] = Deliverable(
            id="deliverable-1",
            task_id="task",
            producer_agent_id="backend",
            summary="A slice",
            state=TeamRecordState.ACCEPTED,
            git_refs=DeliverableGitRefs(feature_branch="team/team/features/task-backend"),
        )
        state.tasks["task"].deliverable_ids = ["deliverable-1"]
        state.release_packages["release-1"] = ReleasePackage(
            id="release-1",
            project_id="project",
            deliverable_ids=["deliverable-1"],
            feature_branches=["team/team/features/task-backend"],
            release_branch="team/team/releases/release-1",
            canonical_branch="releases/project/release-1",
            policy_version=2,
            scope_validation=ReleaseScopeValidation(state="pending"),
            state=TeamRecordState.PENDING,
        )
        return state

    repo.update(_mutate)


def _seed_release_verification(repo: TeamStateRepository) -> None:
    _seed_release(repo)
    frozen_command = ReleaseVerificationCommand(
        name="frozen-check",
        cwd="frozen",
        argv=["python", "-m", "frozen_check"],
    )

    def _mutate(state):
        release = state.release_packages["release-1"]
        state.projects["project"].git.directory_path = "root/team-a"
        release.candidate_commit_sha = "candidate-sha"
        release.verification_run_ids = ["verification-1"]
        release.manifest = ReleaseCandidateManifest(
            project_id="project",
            release_package_id=release.id,
            candidate_commit_sha="candidate-sha",
            release_branch=release.release_branch,
            deliverables=[ReleaseCandidateDeliverableEvidence(deliverable_id="deliverable-1")],
            verification_commands=[frozen_command],
        )
        state.release_verification_runs["verification-1"] = ReleaseVerificationRun(
            id="verification-1",
            release_package_id=release.id,
            candidate_commit_sha="candidate-sha",
            session_id="verification-session",
            command_results=[ReleaseVerificationCommandResult(name="frozen-check")],
        )
        state.projects["project"].release_policy = ProjectReleasePolicy(
            verification_commands=[
                ReleaseVerificationCommand(
                    name="live-policy-check",
                    cwd="live",
                    argv=["python", "-m", "live_policy_check"],
                )
            ]
        )
        return state

    repo.update(_mutate)


def _verify_release_provider(tmp_path: Path) -> dict[str, object]:
    provider = TeamVerifyReleaseCandidateEffectProvider()
    provider.initialize(
        TeamVerifyReleaseCandidateEffectDefinition(
            id="verify-release",
            display_name="Verify release",
            type="effect:team:verify-release-candidate",
            release_package_id="release-1",
            verification_run_id="verification-1",
            candidate_commit_sha="candidate-sha",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    return provider.execute()


def _stub_release_verifier_setup(
    monkeypatch,
    *,
    destroy_result: dict[str, object],
) -> tuple[list[list[str]], list[str]]:
    executed_commands: list[list[str]] = []
    create_directories: list[str] = []

    def _create(self, **kwargs):
        del kwargs
        create_directories.append(self.effect_definition.directory_path)
        return {"success": True}

    monkeypatch.setattr(AgentCreateEffectProvider, "execute", _create)
    monkeypatch.setattr(
        AgentCloneRepoEffectProvider,
        "execute",
        lambda self, **kwargs: {"success": True, "target_dir": "/workspace/repo"},
    )

    def _execute(self, **kwargs):
        del kwargs
        command = list(self.effect_definition.command)
        executed_commands.append(command)
        return {
            "success": True,
            "execution_id": f"execution-{len(executed_commands)}",
            "exit_code": 0,
            "stdout": "ok",
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _execute)
    monkeypatch.setattr(
        AgentDestroyEffectProvider,
        "execute",
        lambda self, **kwargs: destroy_result,
    )
    return executed_commands, create_directories


def test_release_verifier_executes_commands_from_frozen_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    executed_commands, create_directories = _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": True},
    )
    recorded: dict[str, object] = {}

    def _record_result(self, **kwargs):
        del self
        recorded.update(kwargs)
        return repo.read(), {"success": True}

    monkeypatch.setattr(
        TeamReleaseGateService,
        "record_verification_result",
        _record_result,
    )

    result = _verify_release_provider(tmp_path)

    assert result["success"] is True
    assert create_directories == ["root/team-a"]
    assert ["python", "-m", "frozen_check"] in executed_commands
    assert ["python", "-m", "live_policy_check"] not in executed_commands
    assert recorded["verification_state"] == "passed"
    assert recorded["command_results"][0]["name"] == "frozen-check"


def test_release_verifier_treats_reported_cleanup_failure_as_infrastructure_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def _remove_live_policy(state):
        state.projects["project"].release_policy = None
        return state

    repo.update(_remove_live_policy)
    _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": False, "message": "worker deletion failed"},
    )
    recorded: dict[str, object] = {}

    def _record_result(self, **kwargs):
        del self
        recorded.update(kwargs)
        return repo.read(), {"success": True}

    monkeypatch.setattr(
        TeamReleaseGateService,
        "record_verification_result",
        _record_result,
    )

    result = _verify_release_provider(tmp_path)

    assert result["success"] is True
    assert recorded["verification_state"] == "failed"
    assert recorded["failure_kind"] == "infrastructure"
    assert recorded["error"] == ("failed to destroy release verifier: worker deletion failed")


def _mark_release_provider(tmp_path: Path, **config: object) -> dict[str, object]:
    provider = TeamMarkReleasePublishedEffectProvider()
    provider.initialize(
        TeamMarkReleasePublishedEffectDefinition(
            id="publish",
            display_name="Publish",
            type="effect:team:mark-release-published",
            **config,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    return provider.execute()


def test_mark_release_published_success(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert result["success"] is True
    assert result["state"] == "accepted"
    assert result["superseded_release_package_ids"] == []
    state = repo.read()
    release = state.release_packages["release-1"]
    assert release.state == TeamRecordState.ACCEPTED
    assert release.published_commit_sha == "cafef00d"
    assert release.canonical_branch == "releases/project/release-1"
    assert release.published_at is not None


def test_mark_release_published_supersedes_older_blocked_duplicate(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _add_duplicate(state):
        current = state.release_packages["release-1"]
        state.release_packages["release-old"] = ReleasePackage(
            id="release-old",
            project_id="project",
            deliverable_ids=list(current.deliverable_ids),
            release_branch="team/team/releases/release-old",
            git_promotion_record_ids=["promotion-release-old"],
            policy_version=2,
            scope_validation=ReleaseScopeValidation(state="pending"),
            state=TeamRecordState.BLOCKED,
            error="merge conflict",
            created_at=current.created_at - timedelta(minutes=1),
        )
        state.projects["project"].status = ProjectStatus.BLOCKED
        state.inbox["inbox-release-old"] = AgentInboxItem(
            id="inbox-release-old",
            recipient_agent_id="lead",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            payload={
                "kind": "release_publish_failed",
                "release_package_id": "release-old",
            },
        )
        state.git_promotions["promotion-release-old"] = GitPromotionRecord(
            id="promotion-release-old",
            project_id="project",
            actor_agent_id="lead",
            source_branch="team/team/releases/release-old",
            target_branch="releases/project/release-old",
            promotion_kind="canonical_push",
            release_package_id="release-old",
            state="failed",
            error="push rejected",
        )
        state.inbox["inbox-promotion-old"] = AgentInboxItem(
            id="inbox-promotion-old",
            recipient_agent_id="lead",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            payload={
                "kind": "git_promotion_failed",
                "promotion_id": "promotion-release-old",
            },
        )
        return state

    repo.update(_add_duplicate)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert result["success"] is True
    assert result["superseded_release_package_ids"] == ["release-old"]
    assert set(result["resolved_inbox_item_ids"]) == {
        "inbox-release-old",
        "inbox-promotion-old",
    }
    state = repo.read()
    assert state.release_packages["release-old"].state == TeamRecordState.REJECTED
    assert state.release_packages["release-old"].error is None
    assert state.inbox["inbox-release-old"].state == TeamInboxItemState.HANDLED
    assert state.inbox["inbox-promotion-old"].state == TeamInboxItemState.HANDLED
    assert state.projects["project"].status != ProjectStatus.BLOCKED


def test_mark_release_published_leaves_legacy_duplicate_rejected_by_migration(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _add_pending_duplicate(state):
        current = state.release_packages["release-1"]
        state.release_packages["release-pending"] = ReleasePackage(
            id="release-pending",
            project_id="project",
            deliverable_ids=list(current.deliverable_ids),
            release_branch="team/team/releases/release-pending",
            state=TeamRecordState.PENDING,
            created_at=current.created_at - timedelta(minutes=1),
        )
        return state

    repo.update(_add_pending_duplicate)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert result["success"] is True
    assert result["superseded_release_package_ids"] == []
    legacy_release = repo.read().release_packages["release-pending"]
    assert legacy_release.state == TeamRecordState.REJECTED
    assert legacy_release.error is not None
    assert legacy_release.error.startswith("legacy_unpinned_release:")


def test_mark_release_published_ignores_late_success_for_rejected_release(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _reject(state):
        state.release_packages["release-1"].state = TeamRecordState.REJECTED
        return state

    repo.update(_reject)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert result["success"] is True
    assert result["state"] == TeamRecordState.REJECTED.value
    assert result["ignored_terminal_state"] is True
    release = repo.read().release_packages["release-1"]
    assert release.state == TeamRecordState.REJECTED
    assert release.published_commit_sha is None
    assert release.published_at is None


def test_mark_release_published_failure_notifies_lead(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=False,
        error="push rejected",
    )

    assert result["success"] is True
    assert result["state"] == "blocked"
    assert len(result["created_inbox_item_ids"]) == 1
    state = repo.read()
    release = state.release_packages["release-1"]
    assert release.state == TeamRecordState.BLOCKED
    assert release.error == "push rejected"
    notice = state.inbox[result["created_inbox_item_ids"][0]]
    assert notice.recipient_agent_id == "lead"
    assert notice.payload["kind"] == "release_publish_failed"


# --- managed Git delegation effects -----------------------------------------


class _ManagedGitAdminStub:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def consolidate_branches(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return dict(self.result)


class _AdminRuntimeStub:
    def __init__(self, *, promotion_result: dict[str, object], consolidation_result: dict[str, object]) -> None:
        self.promotion_result = promotion_result
        self.promotion_calls: list[dict[str, object]] = []
        self.managed_git_admin = _ManagedGitAdminStub(consolidation_result)

    def promote_agent_branch(self, **kwargs: object) -> dict[str, object]:
        self.promotion_calls.append(dict(kwargs))
        return dict(self.promotion_result)


def test_publish_review_branch_delegates_pinned_promotion() -> None:
    admin = _AdminRuntimeStub(
        promotion_result={"success": True, "commit_sha": "a" * 40},
        consolidation_result={"success": True},
    )
    provider = TeamPublishReviewBranchEffectProvider()
    provider.initialize(
        TeamPublishReviewBranchEffectDefinition(
            id="publish-review",
            display_name="Publish Review",
            type="effect:team:publish-review-branch",
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/team-a",
            source_branch="agents/session-1/work",
            expected_commit_sha="a" * 40,
            target_branch="team/team/reviews/deliverable",
        ),
        EffectRuntimeContext(admin_service=admin, metadata={"directory_path": "root"}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["managed_repo_id"] == "mr_0123456789abcdefabcd"
    assert result["review_branch"] == "team/team/reviews/deliverable"
    assert admin.promotion_calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "agent_branch": "agents/session-1/work",
            "target_branch": "team/team/reviews/deliverable",
            "expected_commit_sha": "a" * 40,
            "directory_path": "root/team-a",
        }
    ]


def test_publish_review_branch_preserves_source_ref_moved_failure() -> None:
    admin = _AdminRuntimeStub(
        promotion_result={"success": False, "reason": "source_ref_moved"},
        consolidation_result={"success": True},
    )
    provider = TeamPublishReviewBranchEffectProvider()
    provider.initialize(
        TeamPublishReviewBranchEffectDefinition(
            id="publish-review",
            display_name="Publish Review",
            type="effect:team:publish-review-branch",
            managed_repo_id="mr_0123456789abcdefabcd",
            source_branch="agents/session-1/work",
            expected_commit_sha="a" * 40,
            target_branch="team/team/reviews/deliverable",
        ),
        EffectRuntimeContext(admin_service=admin),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["reason"] == "source_ref_moved"


def test_publish_review_branch_rejects_sibling_directory_override() -> None:
    admin = _AdminRuntimeStub(
        promotion_result={"success": True},
        consolidation_result={"success": True},
    )
    provider = TeamPublishReviewBranchEffectProvider()
    provider.initialize(
        TeamPublishReviewBranchEffectDefinition(
            id="publish-review",
            display_name="Publish Review",
            type="effect:team:publish-review-branch",
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/team-b",
            source_branch="agents/session-1/work",
            expected_commit_sha="a" * 40,
            target_branch="team/team/reviews/deliverable",
        ),
        EffectRuntimeContext(
            admin_service=admin,
            metadata={"directory_path": "root/team-a"},
        ),
    )

    result = provider.execute()

    assert result["success"] is False
    assert "outside workflow directory scope" in result["error"]
    assert admin.promotion_calls == []


def test_consolidate_git_branches_delegates_exact_source_commits() -> None:
    expected_result = {
        "success": True,
        "commit_sha": "c" * 40,
        "target_branch": "team/team/releases/release-1",
        "merged_branches": ["team/team/features/task-backend"],
    }
    admin = _AdminRuntimeStub(
        promotion_result={"success": True},
        consolidation_result=expected_result,
    )
    provider = TeamConsolidateGitBranchesEffectProvider()
    provider.initialize(
        TeamConsolidateGitBranchesEffectDefinition(
            id="consolidate",
            display_name="Consolidate",
            type="effect:team:consolidate-git-branches",
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/team-a",
            source_branches=["team/team/features/task-backend"],
            target_branch="team/team/releases/release-1",
            base_branch="main",
            expected_source_commits={"team/team/features/task-backend": "b" * 40},
            allowed_changed_paths=["src/backend.py"],
        ),
        EffectRuntimeContext(admin_service=admin, metadata={"directory_path": "root"}),
    )

    result = provider.execute()

    assert result == expected_result
    assert admin.managed_git_admin.calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "directory_path": "root/team-a",
            "source_branches": ["team/team/features/task-backend"],
            "target_branch": "team/team/releases/release-1",
            "base_branch": "main",
            "expected_source_commits": {"team/team/features/task-backend": "b" * 40},
            "allowed_changed_paths": ["src/backend.py"],
        }
    ]


def test_consolidate_git_branches_rejects_incomplete_commit_map() -> None:
    admin = _AdminRuntimeStub(
        promotion_result={"success": True},
        consolidation_result={"success": True},
    )
    provider = TeamConsolidateGitBranchesEffectProvider()
    provider.initialize(
        TeamConsolidateGitBranchesEffectDefinition(
            id="consolidate",
            display_name="Consolidate",
            type="effect:team:consolidate-git-branches",
            managed_repo_id="mr_0123456789abcdefabcd",
            source_branches=["team/team/features/task-backend"],
            target_branch="team/team/releases/release-1",
            expected_source_commits={},
        ),
        EffectRuntimeContext(admin_service=admin),
    )

    result = provider.execute()

    assert result["success"] is False
    assert "expected_source_commits must contain every source branch" in str(result["error"])
    assert admin.managed_git_admin.calls == []


def test_consolidate_git_branches_rejects_sibling_directory_override() -> None:
    admin = _AdminRuntimeStub(
        promotion_result={"success": True},
        consolidation_result={"success": True},
    )
    provider = TeamConsolidateGitBranchesEffectProvider()
    provider.initialize(
        TeamConsolidateGitBranchesEffectDefinition(
            id="consolidate",
            display_name="Consolidate",
            type="effect:team:consolidate-git-branches",
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/team-b",
            source_branches=["team/team/features/task-backend"],
            target_branch="team/team/releases/release-1",
            expected_source_commits={"team/team/features/task-backend": "b" * 40},
        ),
        EffectRuntimeContext(
            admin_service=admin,
            metadata={"directory_path": "root/team-a"},
        ),
    )

    result = provider.execute()

    assert result["success"] is False
    assert "outside workflow directory scope" in result["error"]
    assert admin.managed_git_admin.calls == []
