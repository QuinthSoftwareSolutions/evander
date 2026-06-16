from __future__ import annotations

import subprocess
from pathlib import Path

from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from agent_sandbox.events.models import WorkflowNodeEnvelope
from truman_team_addon.models import (
    Deliverable,
    DeliverableGitRefs,
    EphemeralAgentPolicy,
    GitPromotionRecord,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    TeamInboxItemType,
    TeamRecordState,
)
from truman_team_addon.provider_schemas import (
    TeamConsolidateGitBranchesEffectDefinition,
    TeamMarkReleasePublishedEffectDefinition,
    TeamRecordGitPromotionEffectDefinition,
)
from truman_team_addon.providers import (
    TeamConsolidateGitBranchesEffectProvider,
    TeamMarkReleasePublishedEffectProvider,
    TeamRecordGitPromotionEffectProvider,
)
from truman_team_addon.repository import TeamStateRepository


def _seed_team(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=1,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=0),
        expected_revision=2,
    )
    repo.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=3,
    )
    return repo


def _seed_review_promotion(repo: TeamStateRepository) -> None:
    def _mutate(state):
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
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
    assert refs.commit_sha == "deadbeef"
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
        state.projects["project"].git.canonical_origin_url = "https://example.invalid/Truman.git"
        state.projects["project"].git.provider = "azure_devops"
        state.git_promotions["release-promotion"] = GitPromotionRecord(
            id="release-promotion",
            project_id="project",
            release_package_id="release-1",
            actor_agent_id="lead",
            source_branch="team/team/features/task-backend",
            target_branch="team/team/releases/release-1",
            promotion_kind="release",
        )
        state.release_packages["release-1"].git_promotion_record_ids = [
            "release-promotion"
        ]
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
    assert events[0].payload["canonical_origin_url"] == (
        "https://example.invalid/Truman.git"
    )
    assert events[0].payload["provider"] == "azure_devops"


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
        state.release_packages["release-1"].git_promotion_record_ids = [
            "canonical-promotion"
        ]
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
        state.release_packages["release-1"].git_promotion_record_ids = [
            "canonical-promotion"
        ]
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
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
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
            state=TeamRecordState.PENDING,
        )
        return state

    repo.update(_mutate)


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
    state = repo.read()
    release = state.release_packages["release-1"]
    assert release.state == TeamRecordState.ACCEPTED
    assert release.published_commit_sha == "cafef00d"
    assert release.canonical_branch == "releases/project/release-1"
    assert release.published_at is not None


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


# --- consolidation effect -------------------------------------------------


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Team Test")
    (repo / "file.txt").write_text("base\n")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "base")


def _branch_with(repo: Path, branch: str, content: str) -> None:
    _git(repo, "checkout", "-q", "-b", branch, "main")
    (repo / "file.txt").write_text(content)
    _git(repo, "commit", "-q", "-am", branch)
    _git(repo, "checkout", "-q", "main")


def _consolidate_provider(tmp_path: Path, **config: object) -> dict[str, object]:
    provider = TeamConsolidateGitBranchesEffectProvider()
    provider.initialize(
        TeamConsolidateGitBranchesEffectDefinition(
            id="consolidate",
            display_name="Consolidate",
            type="effect:team:consolidate-git-branches",
            **config,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    return provider.execute()


def test_consolidate_git_branches_merges_in_order(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _branch_with(repo, "feat-a", "a\n")

    result = _consolidate_provider(
        tmp_path,
        host_repo_path=str(repo),
        source_branches=["feat-a"],
        target_branch="team/team/releases/release-1",
        base_branch="main",
    )

    assert result["success"] is True
    assert result["merged_branches"] == ["feat-a"]
    assert result["commit_sha"]
    assert result["conflict_branch"] is None


def test_consolidate_git_branches_refuses_dirty_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("uncommitted change\n")

    result = _consolidate_provider(
        tmp_path,
        host_repo_path=str(repo),
        source_branches=["main"],
        target_branch="team/team/releases/release-2",
    )

    assert result["success"] is False
    assert "dirty worktree" in str(result["error"])
    assert result["merged_branches"] == []


def test_consolidate_git_branches_aborts_on_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _branch_with(repo, "feat-a", "a\n")
    _branch_with(repo, "feat-b", "b\n")

    result = _consolidate_provider(
        tmp_path,
        host_repo_path=str(repo),
        source_branches=["feat-a", "feat-b"],
        target_branch="team/team/releases/release-3",
        base_branch="main",
    )

    assert result["success"] is False
    assert result["conflict_branch"] == "feat-b"
    assert result["merged_branches"] == ["feat-a"]
    # The conflicted merge must be aborted, leaving no in-progress merge.
    status = _git(repo, "status", "--porcelain")
    assert "UU" not in status.stdout
