from __future__ import annotations

import subprocess
import time
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from evander.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from evander.effects.providers.agent_create import AgentCreateEffectProvider
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander.events.models import WorkflowNodeEnvelope
from evander_team_addon.models import (
    AgentInboxItem,
    AgentStandDown,
    AgentTask,
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
    TeamBlocker,
    TeamRecordState,
    TeamTaskState,
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
from evander_team_addon import api as team_addon_api
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.git_release_service import (
    _human_review_publication_validation_error,
)
from evander_team_addon.services.domain.release_packages import (
    _apply_git_release_recovery,
    _release_recovery_permitted,
)
from evander_team_addon.services.release_gate_service import TeamReleaseGateService
from evander_team_addon.services.release_verification_effects import (
    EffectReleaseVerificationExecutor,
)
from evander_team_addon.services.release_verification_runner import (
    ReleaseVerificationTimeoutError,
    TeamReleaseVerificationRunner,
    _bounded_verification_log_excerpt,
)
from evander_team_addon.services.release_verification_singleflight import (
    TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT,
)


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
    assert notice.item_type == TeamInboxItemType.STATUS_REQUEST
    assert notice.payload["kind"] == "blocker_reported"
    blocker = state.blockers[notice.payload["blocker_id"]]
    assert blocker.source_kind.value == "deliverable"
    assert blocker.source_id == "deliverable-1"
    assert blocker.owner_agent_id == "lead"


def test_release_merge_conflicts_create_one_direct_coder_recovery_task(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        release = state.release_packages["release-1"]
        release.git_promotion_record_ids = [
            "merge-promotion-1",
            "merge-promotion-2",
        ]
        for promotion_id, source_branch, target_branch in (
            (
                "merge-promotion-1",
                "agents/backend/tasks/one",
                "team/team/features/one",
            ),
            (
                "merge-promotion-2",
                "agents/backend/tasks/two",
                "team/team/features/two",
            ),
        ):
            state.git_promotions[promotion_id] = GitPromotionRecord(
                id=promotion_id,
                project_id="project",
                task_id="task",
                release_package_id=release.id,
                actor_agent_id="lead",
                source_branch=source_branch,
                target_branch=target_branch,
                promotion_kind="feature",
            )
        return state

    repo.update(_mutate)

    first = _record_promotion_provider(
        tmp_path,
        promotion_id="merge-promotion-1",
        success=False,
        error="Merge conflict while consolidating agents/backend/tasks/one",
        result_outputs={
            "error_code": "merge_conflict",
            "conflict_branch": "agents/backend/tasks/one",
            "output": (
                "Auto-merging src/one.py\n"
                "CONFLICT (content): Merge conflict in src/one.py\n"
                "Automatic merge failed"
            ),
        },
    )

    task_id = first["merge_recovery_task_id"]
    assert first["created_task_ids"] == [task_id]
    assert first["merge_recovery_task_owner_agent_id"] == "backend"
    assert first["merge_recovery_assignment_reason"] == "single_source_owner"
    backend_wakes = [
        event
        for event in first["events"]
        if event["event_type"] == "team.agent.wake_requested"
        and event["payload"]["agent_id"] == "backend"
    ]
    assert len(backend_wakes) == 1
    assert backend_wakes[0]["payload"]["reason"] == "inbox"
    assert not [
        event
        for event in first["events"]
        if event["event_type"] == "team.agent.wake_requested"
        and event["payload"]["agent_id"] == "manager"
    ]

    second = _record_promotion_provider(
        tmp_path,
        promotion_id="merge-promotion-2",
        success=False,
        error="Merge conflict while consolidating agents/backend/tasks/two",
        result_outputs={
            "error_code": "merge_conflict",
            "conflict_branch": "agents/backend/tasks/two",
            "output": "CONFLICT (add/add): Merge conflict in src/two.py",
        },
    )
    replay = _record_promotion_provider(
        tmp_path,
        promotion_id="merge-promotion-2",
        success=False,
        error="Merge conflict while consolidating agents/backend/tasks/two",
        result_outputs={
            "error_code": "merge_conflict",
            "conflict_branch": "agents/backend/tasks/two",
            "output": "CONFLICT (add/add): Merge conflict in src/two.py",
        },
    )

    assert second["merge_recovery_task_id"] == task_id
    assert "created_task_ids" not in second
    assert not [
        event
        for event in second["events"]
        if event["event_type"] == "team.agent.wake_requested"
        and event["payload"]["agent_id"] == "backend"
    ]
    assert replay["merge_recovery_task_id"] == task_id
    assert not replay["events"]
    backend_wake_outbox = [
        record
        for record in repo.list_outbox_records(intent_type="team.agent.wake_requested")
        if record.payload["payload"]["agent_id"] == "backend"
    ]
    assert len(backend_wake_outbox) == 1
    assert backend_wake_outbox[0].idempotency_key is not None
    state = repo.read()
    recovery_tasks = [
        task
        for task in state.tasks.values()
        if task.created_by_actor == "system:git-merge-recovery"
    ]
    assert [task.id for task in recovery_tasks] == [task_id]
    task = recovery_tasks[0]
    assert task.owner_agent_id == "backend"
    assert "release-1" in task.description
    assert "merge-promotion-1" in task.description
    assert "merge-promotion-2" in task.description
    assert "src/one.py" in task.description
    assert "src/two.py" in task.description
    assert "team_recover_release" in task.description
    blockers = [
        blocker
        for blocker in state.blockers.values()
        if blocker.source_kind.value == "release_package" and blocker.source_id == "release-1"
    ]
    assert len(blockers) == 1
    assert blockers[0].affected_task_ids == [task_id]
    blocker_notices = [
        item for item in state.inbox.values() if item.payload.get("blocker_id") == blockers[0].id
    ]
    assert len(blocker_notices) == 1
    assert blocker_notices[0].recipient_agent_id == "backend"
    assert blocker_notices[0].payload["affected_task_ids"] == [task_id]
    assert not [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == "manager" and item.payload.get("blocker_id") == blockers[0].id
    ]
    recovery_state = state.model_copy(deep=True)
    recovery_state.release_packages["release-1"].policy_version = 1
    recovery_intent: dict[str, object] = {
        "errors": [],
        "events": [],
        "release_recovery_event_ids": [],
    }
    recovered_release_id = _apply_git_release_recovery(
        recovery_state,
        actor=recovery_state.agents["backend"],
        request={
            "release_package_id": "release-1",
            "agent_branch": "agents/backend/release-recovery",
            "commit_sha": "deadbeef",
        },
        result=recovery_intent,
    )
    assert recovered_release_id == "release-1"
    assert recovery_intent["errors"] == []
    assert len(recovery_intent["events"]) == 1
    assert recovery_intent["events"][0]["event_type"] == ("team.git.release_recovery_requested")


def test_release_merge_conflict_with_ambiguous_owners_falls_back_to_lead(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        state.tasks["lead-conflict-task"] = AgentTask(
            id="lead-conflict-task",
            owner_agent_id="lead",
            project_id="project",
            priority=1,
            title="Lead-owned contribution",
            description="Create a separate contribution.",
            created_by_agent_id="manager",
        )
        release = state.release_packages["release-1"]
        release.git_promotion_record_ids = [
            "backend-promotion",
            "lead-promotion",
        ]
        state.git_promotions["backend-promotion"] = GitPromotionRecord(
            id="backend-promotion",
            project_id="project",
            task_id="task",
            release_package_id=release.id,
            actor_agent_id="lead",
            source_branch="agents/backend/tasks/conflict",
            target_branch="team/team/features/backend",
            promotion_kind="feature",
        )
        state.git_promotions["lead-promotion"] = GitPromotionRecord(
            id="lead-promotion",
            project_id="project",
            task_id="lead-conflict-task",
            release_package_id=release.id,
            actor_agent_id="lead",
            source_branch="agents/lead/tasks/conflict",
            target_branch="team/team/features/lead",
            promotion_kind="feature",
            state="failed",
            outputs={"error_code": "merge_conflict"},
            error="Merge conflict in lead contribution",
        )
        return state

    repo.update(_mutate)

    result = _record_promotion_provider(
        tmp_path,
        promotion_id="backend-promotion",
        success=False,
        error="Merge conflict in backend contribution",
        result_outputs={"error_code": "merge_conflict"},
    )

    task = repo.read().tasks[result["merge_recovery_task_id"]]
    assert task.owner_agent_id == "lead"
    assert result["merge_recovery_assignment_reason"] == ("ambiguous_source_owners_project_lead")
    assert (
        len(
            [
                event
                for event in result["events"]
                if event["event_type"] == "team.agent.wake_requested"
                and event["payload"]["agent_id"] == "lead"
            ]
        )
        == 1
    )
    ambiguous_blocker = next(
        blocker
        for blocker in repo.read().blockers.values()
        if blocker.source_kind.value == "release_package" and blocker.source_id == "release-1"
    )
    assert ambiguous_blocker.owner_agent_id == "lead"
    assert (
        len(
            [
                item
                for item in repo.read().inbox.values()
                if item.state == TeamInboxItemState.PENDING
                and item.recipient_agent_id == "lead"
                and item.payload.get("blocker_id") == ambiguous_blocker.id
            ]
        )
        == 1
    )
    assert not [
        event
        for event in result["events"]
        if event["event_type"] == "team.agent.wake_requested"
        and event["payload"]["agent_id"] == "manager"
    ]
    assert not [
        item
        for item in repo.read().inbox.values()
        if item.recipient_agent_id == "manager"
        and item.payload.get("blocker_id") == ambiguous_blocker.id
    ]


def test_release_merge_recovery_task_respects_active_stand_down(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        release = state.release_packages["release-1"]
        release.git_promotion_record_ids = ["held-merge-promotion"]
        state.git_promotions["held-merge-promotion"] = GitPromotionRecord(
            id="held-merge-promotion",
            project_id="project",
            task_id="task",
            release_package_id=release.id,
            actor_agent_id="lead",
            source_branch="agents/backend/tasks/held",
            target_branch="team/team/features/held",
            promotion_kind="feature",
        )
        state.stand_downs["hold-lead-subtree"] = AgentStandDown(
            id="hold-lead-subtree",
            agent_id="lead",
            scope="subtree",
            reason="Pause project work during maintenance.",
            set_by="operator:test",
        )
        return state

    repo.update(_mutate)
    result = _record_promotion_provider(
        tmp_path,
        promotion_id="held-merge-promotion",
        success=False,
        error="Merge conflict in held contribution",
        result_outputs={"error_code": "merge_conflict"},
    )

    task_id = result["merge_recovery_task_id"]
    assert repo.read().tasks[task_id].owner_agent_id == "backend"
    blocker = next(
        blocker
        for blocker in repo.read().blockers.values()
        if blocker.source_kind.value == "release_package" and blocker.source_id == "release-1"
    )
    assert blocker.affected_task_ids == [task_id]
    assert not [
        event
        for event in result["events"]
        if event["event_type"] == "team.agent.wake_requested"
        and event["payload"]["agent_id"] == "backend"
    ]
    assert not [
        record
        for record in repo.list_outbox_records(intent_type="team.agent.wake_requested")
        if record.payload["payload"]["agent_id"] == "backend"
    ]


def test_release_recovery_permission_is_scoped_to_active_linked_task_owner(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="other-backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-other-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=0),
        expected_revision=repo.read().revision,
    )
    _seed_release(repo)

    def _mutate(state):
        release = state.release_packages["release-1"]
        release.git_promotion_record_ids = ["permission-merge-promotion"]
        state.git_promotions["permission-merge-promotion"] = GitPromotionRecord(
            id="permission-merge-promotion",
            project_id="project",
            task_id="task",
            release_package_id=release.id,
            actor_agent_id="lead",
            source_branch="agents/backend/tasks/permission",
            target_branch="team/team/features/permission",
            promotion_kind="feature",
        )
        return state

    repo.update(_mutate)
    result = _record_promotion_provider(
        tmp_path,
        promotion_id="permission-merge-promotion",
        success=False,
        error="Merge conflict in permission contribution",
        result_outputs={"error_code": "merge_conflict"},
    )
    state = repo.read()
    project = state.projects["project"]
    release = state.release_packages["release-1"]
    task_id = result["merge_recovery_task_id"]

    assert _release_recovery_permitted(
        state,
        actor=state.agents["backend"],
        project=project,
        release=release,
    )
    assert not _release_recovery_permitted(
        state,
        actor=state.agents["other-backend"],
        project=project,
        release=release,
    )

    terminal_state = state.model_copy(deep=True)
    terminal_state.tasks[task_id].state = TeamTaskState.FINISHED
    assert not _release_recovery_permitted(
        terminal_state,
        actor=terminal_state.agents["backend"],
        project=terminal_state.projects["project"],
        release=terminal_state.release_packages["release-1"],
    )

    unlinked_state = state.model_copy(deep=True)
    for blocker in unlinked_state.blockers.values():
        if blocker.source_kind.value == "release_package" and blocker.source_id == "release-1":
            blocker.affected_task_ids = []
    assert not _release_recovery_permitted(
        unlinked_state,
        actor=unlinked_state.agents["backend"],
        project=unlinked_state.projects["project"],
        release=unlinked_state.release_packages["release-1"],
    )


def test_merge_recovery_replay_retires_legacy_terminal_blocker_route(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        release = state.release_packages["release-1"]
        release.git_promotion_record_ids = ["legacy-terminal-promotion"]
        state.git_promotions["legacy-terminal-promotion"] = GitPromotionRecord(
            id="legacy-terminal-promotion",
            project_id="project",
            task_id="task",
            release_package_id=release.id,
            actor_agent_id="manager",
            source_branch="agents/backend/tasks/legacy-terminal",
            target_branch="team/team/features/legacy-terminal",
            promotion_kind="feature",
        )
        state.blockers["legacy-terminal-blocker"] = TeamBlocker(
            id="legacy-terminal-blocker",
            team_id="team",
            project_id="project",
            source_kind="release_package",
            source_id=release.id,
            reporter_agent_id="manager",
            owner_agent_id="manager",
            route="operator",
            summary="Legacy terminal release route",
        )
        state.inbox["legacy-manager-inbox"] = AgentInboxItem(
            id="legacy-manager-inbox",
            recipient_agent_id="manager",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            payload={
                "kind": "blocker_reported",
                "blocker_id": "legacy-terminal-blocker",
            },
        )
        return state

    repo.update(_mutate)
    result = _record_promotion_provider(
        tmp_path,
        promotion_id="legacy-terminal-promotion",
        success=False,
        error="Merge conflict in legacy terminal contribution",
        result_outputs={"error_code": "merge_conflict"},
    )

    state = repo.read()
    blocker = state.blockers["legacy-terminal-blocker"]
    assert blocker.owner_agent_id == "backend"
    assert blocker.route.value == "owned"
    assert blocker.affected_task_ids == [result["merge_recovery_task_id"]]
    assert state.inbox["legacy-manager-inbox"].state == TeamInboxItemState.HANDLED
    backend_notices = [
        item
        for item in state.inbox.values()
        if item.state == TeamInboxItemState.PENDING
        and item.recipient_agent_id == "backend"
        and item.payload.get("blocker_id") == blocker.id
    ]
    assert len(backend_notices) == 1
    assert (
        len(
            [
                event
                for event in result["events"]
                if event["event_type"] == "team.agent.wake_requested"
                and event["payload"]["agent_id"] == "backend"
            ]
        )
        == 1
    )
    assert any(
        event.blocker_id == blocker.id
        and event.event_type.value == "owner_transferred"
        and event.from_agent_id == "manager"
        and event.to_agent_id == "backend"
        for event in state.blocker_events.values()
    )


@pytest.mark.parametrize(
    ("promotion_kind", "release_package_id", "error_code"),
    [
        ("feature", "release-1", "scope_mismatch"),
        ("canonical_push", "release-1", "merge_conflict"),
        ("feature", None, "merge_conflict"),
    ],
)
def test_nonrecoverable_git_failures_do_not_create_merge_recovery_tasks(
    tmp_path: Path,
    promotion_kind: str,
    release_package_id: str | None,
    error_code: str,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        state.git_promotions["nonrecoverable-promotion"] = GitPromotionRecord(
            id="nonrecoverable-promotion",
            project_id="project",
            task_id="task",
            release_package_id=release_package_id,
            actor_agent_id="lead",
            source_branch="agents/backend/tasks/nonrecoverable",
            target_branch="team/team/features/nonrecoverable",
            promotion_kind=promotion_kind,
        )
        if release_package_id is not None:
            state.release_packages[release_package_id].git_promotion_record_ids = [
                "nonrecoverable-promotion"
            ]
        return state

    repo.update(_mutate)
    result = _record_promotion_provider(
        tmp_path,
        promotion_id="nonrecoverable-promotion",
        success=False,
        error="promotion failed",
        result_outputs={"error_code": error_code},
    )

    assert "merge_recovery_task_id" not in result
    assert not [
        task
        for task in repo.read().tasks.values()
        if task.created_by_actor == "system:git-merge-recovery"
    ]


def test_terminal_release_does_not_create_merge_recovery_task(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        release = state.release_packages["release-1"]
        release.state = TeamRecordState.REJECTED
        release.git_promotion_record_ids = ["late-promotion"]
        state.git_promotions["late-promotion"] = GitPromotionRecord(
            id="late-promotion",
            project_id="project",
            task_id="task",
            release_package_id=release.id,
            actor_agent_id="lead",
            source_branch="agents/backend/tasks/late",
            target_branch="team/team/features/late",
            promotion_kind="feature",
        )
        return state

    repo.update(_mutate)
    result = _record_promotion_provider(
        tmp_path,
        promotion_id="late-promotion",
        success=False,
        error="late merge conflict",
        result_outputs={"error_code": "merge_conflict"},
    )

    assert "merge_recovery_task_id" not in result
    assert not [
        task
        for task in repo.read().tasks.values()
        if task.created_by_actor == "system:git-merge-recovery"
    ]


def test_record_release_promotion_success_publishes_canonical_push(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def _mutate(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(allow_ungated=True)
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
    assert notice.payload["kind"] == "blocker_reported"
    blocker = state.blockers[notice.payload["blocker_id"]]
    assert blocker.source_kind.value == "release_package"
    assert blocker.source_id == "release-1"
    assert blocker.owner_agent_id == "manager"


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
        state.tasks["task"].state = TeamTaskState.FINISHED
        state.release_packages["release-1"] = ReleasePackage(
            id="release-1",
            project_id="project",
            deliverable_ids=["deliverable-1"],
            feature_branches=["team/team/features/task-backend"],
            release_branch="team/team/releases/release-1",
            canonical_branch="releases/project/release-1",
            base_commit_sha="base-sha",
            policy_version=2,
            scope_validation=ReleaseScopeValidation(state="pending"),
            state=TeamRecordState.PENDING,
        )
        return state

    repo.update(_mutate)


def _seed_publishable_release(repo: TeamStateRepository) -> None:
    _seed_release(repo)

    def _mutate(state):
        release = state.release_packages["release-1"]
        release.candidate_commit_sha = "cafef00d"
        release.approved_commit_sha = "cafef00d"
        release.scope_validation.state = "passed"
        release.git_promotion_record_ids = ["canonical-promotion"]
        state.git_promotions["canonical-promotion"] = GitPromotionRecord(
            id="canonical-promotion",
            project_id="project",
            release_package_id=release.id,
            actor_agent_id="lead",
            source_branch=release.release_branch,
            sources=[
                {
                    "source_branch": release.release_branch,
                    "expected_commit_sha": "cafef00d",
                }
            ],
            target_branch=release.canonical_branch,
            promotion_kind="canonical_push",
            state="succeeded",
            commit_sha="cafef00d",
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
            container_image="frozen-verifier:v1",
            resource_limits={
                "memory": "6g",
                "cpus": 4,
                "pids_limit": 1536,
            },
            command_results=[ReleaseVerificationCommandResult(name="frozen-check")],
        )
        state.projects["project"].release_policy = ProjectReleasePolicy(
            container_image="live-verifier:v2",
            resource_limits={
                "memory": "12g",
                "cpus": 8,
                "pids_limit": 3072,
            },
            verification_commands=[
                ReleaseVerificationCommand(
                    name="live-policy-check",
                    cwd="live",
                    argv=["python", "-m", "live_policy_check"],
                )
            ],
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
) -> tuple[list[list[str]], list[str], list[dict[str, object]]]:
    executed_commands: list[list[str]] = []
    create_directories: list[str] = []
    create_metadata: list[dict[str, object]] = []

    def _create(self, **kwargs):
        del kwargs
        create_directories.append(self.effect_definition.directory_path)
        create_metadata.append(dict(self.effect_definition.metadata))
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
    return executed_commands, create_directories, create_metadata


def test_release_verifier_executes_commands_from_frozen_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    executed_commands, create_directories, create_metadata = _stub_release_verifier_setup(
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
    assert create_metadata == [
        {
            "team_release_verification": True,
            "release_package_id": "release-1",
            "candidate_commit_sha": "candidate-sha",
            "container_worker_resource_limits": {
                "memory": "6g",
                "cpus": 4.0,
                "pids_limit": 1536,
            },
        }
    ]
    assert executed_commands[:3] == [
        ["git", "checkout", "--detach", "--force", "candidate-sha"],
        ["git", "reset", "--hard", "candidate-sha"],
        ["git", "clean", "-ffdx"],
    ]
    assert ["python", "-m", "frozen_check"] in executed_commands
    assert ["python", "-m", "live_policy_check"] not in executed_commands
    assert recorded["verification_state"] == "passed"
    assert recorded["command_results"][0]["name"] == "frozen-check"


def test_release_verifier_persists_live_command_state_and_worker_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    _executed_commands, _create_directories, _create_metadata = _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": True},
    )
    progress: list[tuple[str, str | None]] = []
    original_progress = TeamReleaseGateService.record_verification_command_progress

    def _record_progress(self, **kwargs):
        result = dict(kwargs["command_result"])
        progress.append((str(result["state"]), result.get("job_id")))
        return original_progress(self, **kwargs)

    def _execute(self, **kwargs):
        del kwargs
        command = list(self.effect_definition.command)
        callback = getattr(self, "_container_execution_started_callback", None)
        if command == ["python", "-m", "frozen_check"] and callable(callback):
            callback("live-worker-job")
        return {
            "success": True,
            "execution_id": "live-worker-job",
            "exit_code": 0,
            "stdout": "check complete",
        }

    monkeypatch.setattr(
        TeamReleaseGateService,
        "record_verification_command_progress",
        _record_progress,
    )
    monkeypatch.setattr(AgentExecEffectProvider, "execute", _execute)

    result = _verify_release_provider(tmp_path)

    assert result["success"] is True
    assert progress == [
        ("running", None),
        ("running", "live-worker-job"),
        ("passed", "live-worker-job"),
    ]
    run = repo.read().release_verification_runs["verification-1"]
    assert run.state == "passed"
    assert run.command_results[0].job_id == "live-worker-job"


def test_release_verifier_effect_coalesces_overlapping_same_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    executed_commands, create_directories, _create_metadata = _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": True},
    )
    command_started = Event()
    allow_command_to_finish = Event()
    results: dict[str, dict[str, object]] = {}

    def _execute(self, **kwargs):
        del kwargs
        command = list(self.effect_definition.command)
        executed_commands.append(command)
        if command == ["python", "-m", "frozen_check"]:
            command_started.set()
            assert allow_command_to_finish.wait(timeout=2)
        return {
            "success": True,
            "execution_id": f"execution-{len(executed_commands)}",
            "exit_code": 0,
            "stdout": "ok",
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _execute)

    def _run(name: str) -> None:
        results[name] = _verify_release_provider(tmp_path)

    leader = Thread(target=_run, args=("leader",))
    follower = Thread(target=_run, args=("follower",))
    leader.start()
    assert command_started.wait(timeout=2)
    follower.start()
    _wait_for_release_verification_waiter(
        repo,
        verification_run_id="verification-1",
    )

    allow_command_to_finish.set()
    leader.join(timeout=2)
    follower.join(timeout=2)

    assert not leader.is_alive()
    assert not follower.is_alive()
    assert create_directories == ["root/team-a"]
    assert executed_commands.count(["git", "clean", "-ffdx"]) == 1
    assert executed_commands.count(["python", "-m", "frozen_check"]) == 1
    assert results["leader"]["success"] is True
    assert results["follower"]["success"] is True
    assert results["follower"]["coalesced"] is True


def test_release_verifier_rejects_conflicting_followers_before_coalescing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def _add_different_release(state):
        state.release_packages["different-release"] = ReleasePackage(
            id="different-release",
            project_id="project",
            candidate_commit_sha="candidate-sha",
        )
        return state

    repo.update(_add_different_release)
    _executed_commands, _create_directories, _create_metadata = _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": True},
    )
    command_started = Event()
    allow_command_to_finish = Event()
    leader_result: dict[str, dict[str, object]] = {}
    errors: dict[str, Exception] = {}
    follower_finished = {
        "candidate": Event(),
        "release": Event(),
    }

    def _execute(self, **kwargs):
        del kwargs
        command = list(self.effect_definition.command)
        if command == ["python", "-m", "frozen_check"]:
            command_started.set()
            assert allow_command_to_finish.wait(timeout=3)
        return {
            "success": True,
            "execution_id": "execution-1",
            "exit_code": 0,
            "stdout": "ok",
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _execute)

    def _run_leader() -> None:
        leader_result["result"] = _verify_release_provider(tmp_path)

    def _run_conflicting_follower(
        name: str,
        *,
        release_package_id: str,
        candidate_commit_sha: str,
    ) -> None:
        try:
            TeamReleaseVerificationRunner(repo, SimpleNamespace()).run(
                release_package_id=release_package_id,
                verification_run_id="verification-1",
                candidate_commit_sha=candidate_commit_sha,
            )
        except Exception as exc:
            errors[name] = exc
        finally:
            follower_finished[name].set()

    leader = Thread(target=_run_leader)
    candidate_follower = Thread(
        target=_run_conflicting_follower,
        kwargs={
            "name": "candidate",
            "release_package_id": "release-1",
            "candidate_commit_sha": "different-candidate",
        },
    )
    release_follower = Thread(
        target=_run_conflicting_follower,
        kwargs={
            "name": "release",
            "release_package_id": "different-release",
            "candidate_commit_sha": "candidate-sha",
        },
    )
    leader.start()
    assert command_started.wait(timeout=2)
    candidate_follower.start()
    release_follower.start()
    try:
        assert follower_finished["candidate"].wait(timeout=1)
        assert follower_finished["release"].wait(timeout=1)
    finally:
        allow_command_to_finish.set()
        leader.join(timeout=3)
        candidate_follower.join(timeout=3)
        release_follower.join(timeout=3)

    assert not leader.is_alive()
    assert not candidate_follower.is_alive()
    assert not release_follower.is_alive()
    assert leader_result["result"]["success"] is True
    assert str(errors["candidate"]) == (
        "release verification request does not match the pinned candidate"
    )
    assert str(errors["release"]) == (
        "verification run verification-1 belongs to release release-1, not different-release"
    )


def _wait_for_release_verification_waiter(
    repo: TeamStateRepository,
    *,
    verification_run_id: str,
) -> None:
    deadline = time.monotonic() + 2
    database_state = TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT._database_state(repo.store.path)
    while time.monotonic() < deadline:
        with database_state.lock:
            flight = database_state.flights.get(verification_run_id)
            if flight is not None and flight.coalesced_waiter_count >= 1:
                return
        time.sleep(0.001)
    raise AssertionError("release verifier did not reach the expected single-flight wait state")


@pytest.mark.parametrize(
    (
        "command_state",
        "command_failure_kind",
        "expected_release_state",
        "expects_correction",
    ),
    [
        ("failed", "candidate", TeamRecordState.REJECTED, True),
        ("failed", "infrastructure", TeamRecordState.BLOCKED, False),
        ("timed_out", "timeout", TeamRecordState.BLOCKED, False),
    ],
)
def test_release_verifier_resume_finalizes_persisted_terminal_command(
    tmp_path: Path,
    command_state: str,
    command_failure_kind: str,
    expected_release_state: TeamRecordState,
    expects_correction: bool,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def _persist_terminal_command(state):
        run = state.release_verification_runs["verification-1"]
        run.state = "running"
        run.command_results[0] = ReleaseVerificationCommandResult(
            name="frozen-check",
            state=command_state,
            failure_kind=command_failure_kind,
            exit_code=1 if command_state == "failed" else None,
            log_excerpt="persisted verifier failure",
        )
        return state

    repo.update(_persist_terminal_command)

    result = TeamReleaseVerificationRunner(repo, SimpleNamespace()).run(
        release_package_id="release-1",
        verification_run_id="verification-1",
        candidate_commit_sha="candidate-sha",
    )

    state = repo.read()
    run = state.release_verification_runs["verification-1"]
    assert result["success"] is True
    assert result["verification_state"] == command_state
    assert run.state == command_state
    assert run.failure_kind == command_failure_kind
    assert state.release_packages["release-1"].state == expected_release_state
    assert (run.correction_task_id is not None) is expects_correction


def test_release_verification_live_payload_returns_bounded_worker_tail_without_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)

    def _mark_running(state):
        run = state.release_verification_runs["verification-1"]
        run.state = "running"
        run.current_command_index = 0
        run.command_results[0] = ReleaseVerificationCommandResult(
            name="frozen-check",
            state="running",
            job_id="worker-job-1",
        )
        return state

    state = repo.update(_mark_running)
    calls: list[dict[str, object]] = []

    class _WorkerClient:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

        def logs_page(self, job_id: str, **kwargs: object):
            assert job_id == "worker-job-1"
            assert kwargs == {"mode": "raw", "limit": 160}
            return "last verifier line", {"returned_lines": 1, "has_more_before": True}

    class _ProviderConfig:
        def resolve_for_directory(self, directory_path: str):
            assert directory_path == "root/team-a"
            return SimpleNamespace(
                container_worker_base_url="http://worker.internal:11880",
                container_worker_token_env="TEST_WORKER_TOKEN",
                container_worker_request_timeout_seconds=12,
            )

    monkeypatch.setattr(team_addon_api, "ContainerWorkerClient", _WorkerClient)

    payload = team_addon_api._release_verification_live_payload(
        state,
        "release-1",
        "root/team-a",
        _ProviderConfig(),
        160,
    )

    assert payload == {
        "release_package_id": "release-1",
        "run": {
            "id": "verification-1",
            "state": "running",
            "attempt_number": 1,
            "current_command": {
                "index": 0,
                "name": "frozen-check",
                "state": "running",
            },
        },
        "log": {
            "available": True,
            "text": "last verifier line",
            "returned_lines": 1,
            "has_more_before": True,
        },
    }
    assert calls == [
        {
            "base_url": "http://worker.internal:11880",
            "token_env": "TEST_WORKER_TOKEN",
            "timeout_seconds": 12.0,
        }
    ]
    assert "TEST_WORKER_TOKEN" not in str(payload)


@pytest.mark.parametrize("timeout_mode", ["ssh_exception", "container_nonterminal"])
def test_release_verifier_classifies_agent_command_timeouts(
    tmp_path: Path,
    monkeypatch,
    timeout_mode: str,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    executed_commands, _create_directories, _create_metadata = _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": True},
    )
    recorded: dict[str, object] = {}

    def _execute(self, **kwargs):
        del kwargs
        command = list(self.effect_definition.command)
        executed_commands.append(command)
        if command == ["python", "-m", "frozen_check"]:
            if timeout_mode == "ssh_exception":
                raise subprocess.TimeoutExpired(command, timeout=60)
            return {
                "success": False,
                "status": "running",
                "execution_id": "execution-running",
                "failure_kind": "infrastructure",
                "stderr": "container command still running",
            }
        return {
            "success": True,
            "execution_id": f"execution-{len(executed_commands)}",
            "exit_code": 0,
            "stdout": "ok",
        }

    def _record_result(self, **kwargs):
        del self
        recorded.update(kwargs)
        return repo.read(), {"success": True}

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _execute)
    monkeypatch.setattr(
        TeamReleaseGateService,
        "record_verification_result",
        _record_result,
    )

    result = _verify_release_provider(tmp_path)

    assert result["success"] is True
    assert recorded["verification_state"] == "timed_out"
    assert recorded["failure_kind"] == "timeout"
    assert recorded["command_results"][0]["state"] == "timed_out"
    assert recorded["command_results"][0]["failure_kind"] == "timeout"


@pytest.mark.parametrize(
    "stderr",
    [
        """
System.OutOfMemoryException: Exception of type 'System.OutOfMemoryException' was thrown.
   at System.Threading.Thread.StartCore()
   at Microsoft.Build.BackEnd.NodeProviderOutOfProcBase.GetNodes(Int32 count)
"""
        + ("trailing worker output\n" * 600),
        """
Out of memory.
MSBUILD : error MSB4166: Child node "36" exited prematurely. Shutting down.
""",
        "Out of memory.",
        'MSBUILD : error MSB4166: Child node "36" exited prematurely.',
        "Expected 4 items, but found 3.",
    ],
)
def test_release_verifier_does_not_infer_resource_exhaustion_from_command_text(
    tmp_path: Path,
    monkeypatch,
    stderr: str,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    executed_commands, _create_directories, _create_metadata = _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": True},
    )
    recorded: dict[str, object] = {}

    def _execute(self, **kwargs):
        del kwargs
        command = list(self.effect_definition.command)
        executed_commands.append(command)
        if command == ["python", "-m", "frozen_check"]:
            return {
                "success": False,
                "status": "stopped",
                "execution_id": "execution-failed",
                "failure_kind": "command",
                "exit_code": 82,
                "stderr": stderr,
            }
        return {
            "success": True,
            "execution_id": f"execution-{len(executed_commands)}",
            "exit_code": 0,
            "stdout": "ok",
        }

    def _record_result(self, **kwargs):
        del self
        recorded.update(kwargs)
        return repo.read(), {"success": True}

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _execute)
    monkeypatch.setattr(
        TeamReleaseGateService,
        "record_verification_result",
        _record_result,
    )

    result = _verify_release_provider(tmp_path)

    assert result["success"] is True
    assert recorded["verification_state"] == "failed"
    assert recorded["failure_kind"] == "candidate"
    assert recorded["command_results"][0]["state"] == "failed"
    assert recorded["command_results"][0]["failure_kind"] == "candidate"


def test_release_verifier_excerpt_retains_failure_start_and_final_summary() -> None:
    diagnostic = "AdminClient TS1117: duplicate property\n"
    summary = "\nOperatorClient: 40 passing\nShared: 26 passing\n"
    full_log = diagnostic + ("intermediate output\n" * 600) + summary

    excerpt = _bounded_verification_log_excerpt(full_log)

    assert len(excerpt) == 8000
    assert excerpt.startswith(diagnostic)
    assert "... [middle of verifier log omitted] ..." in excerpt
    assert excerpt.endswith(summary)
    assert _bounded_verification_log_excerpt("short output") == "short output"


def test_release_verifier_uses_structured_worker_resource_exhaustion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    executed_commands, _create_directories, _create_metadata = _stub_release_verifier_setup(
        monkeypatch,
        destroy_result={"success": True},
    )
    recorded: dict[str, object] = {}

    def _execute(self, **kwargs):
        del kwargs
        command = list(self.effect_definition.command)
        executed_commands.append(command)
        if command == ["python", "-m", "frozen_check"]:
            return {
                "success": False,
                "status": "failed",
                "execution_id": "execution-failed",
                "failure_kind": "infrastructure",
                "failure_cause": "pids_limit_exhausted",
                "exit_code": 1,
                "stderr": "Out of memory.",
            }
        return {
            "success": True,
            "execution_id": f"execution-{len(executed_commands)}",
            "exit_code": 0,
            "stdout": "ok",
        }

    def _record_result(self, **kwargs):
        del self
        recorded.update(kwargs)
        return repo.read(), {"success": True}

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _execute)
    monkeypatch.setattr(
        TeamReleaseGateService,
        "record_verification_result",
        _record_result,
    )

    result = _verify_release_provider(tmp_path)

    assert result["success"] is True
    assert recorded["verification_state"] == "failed"
    assert recorded["failure_kind"] == "infrastructure"
    assert recorded["command_results"][0]["failure_kind"] == "infrastructure"
    assert recorded["command_results"][0]["failure_cause"] == "pids_limit_exhausted"


def test_release_verification_effect_adapter_translates_subprocess_timeouts(
    monkeypatch,
) -> None:
    command = ReleaseVerificationCommand(
        name="timed-check",
        cwd=".",
        argv=["python", "-m", "timed_check"],
    )

    def _timeout(self, **kwargs):
        del self, kwargs
        raise subprocess.TimeoutExpired(command.argv, timeout=60)

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _timeout)
    executor = EffectReleaseVerificationExecutor(EffectRuntimeContext(resolved_config={}))

    with pytest.raises(ReleaseVerificationTimeoutError) as raised:
        executor.execute_command(
            run_id="verification-1",
            command_index=0,
            session_id="verification-session",
            target_dir="/workspace/repo",
            command=command,
        )

    assert isinstance(raised.value.__cause__, subprocess.TimeoutExpired)


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
    _seed_publishable_release(repo)

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

    retry_result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert retry_result["success"] is True
    assert retry_result["state"] == TeamRecordState.ACCEPTED.value
    assert retry_result["ignored_terminal_state"] is True
    assert retry_result["error"] is None


def test_mark_release_published_revalidates_current_hierarchy_scope(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_publishable_release(repo)

    def _reopen_release_task(state):
        state.tasks["task"].state = TeamTaskState.IN_PROGRESS
        return state

    repo.update(_reopen_release_task)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert result["success"] is True
    assert result["state"] == TeamRecordState.BLOCKED.value
    assert "release publication scope is no longer valid" in result["error"]
    assert "release task not resolved" in result["error"]
    release = repo.read().release_packages["release-1"]
    assert release.published_commit_sha is None
    assert release.published_at is None


def test_mark_release_published_ignores_conflicting_late_result_for_accepted_release(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_publishable_release(repo)
    first_result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )
    published_at = repo.read().release_packages["release-1"].published_at

    late_result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/other",
        published_commit_sha="different-candidate",
    )

    assert first_result["state"] == TeamRecordState.ACCEPTED.value
    assert late_result["success"] is True
    assert late_result["ignored_terminal_state"] is True
    assert "is immutable" in late_result["error"]
    release = repo.read().release_packages["release-1"]
    assert release.state == TeamRecordState.ACCEPTED
    assert release.published_commit_sha == "cafef00d"
    assert release.canonical_branch == "releases/project/release-1"
    assert release.published_at == published_at


def test_mark_release_published_supersedes_older_blocked_duplicate(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_publishable_release(repo)

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
    _seed_publishable_release(repo)

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


@pytest.mark.parametrize(
    ("approved_commit_sha", "error_snippet"),
    [
        (None, "requires approved_commit_sha"),
        ("different-candidate", "does not match release candidate"),
    ],
)
def test_mark_release_published_rejects_missing_or_mismatched_approval(
    tmp_path: Path,
    approved_commit_sha: str | None,
    error_snippet: str,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_publishable_release(repo)

    def _mutate(state):
        state.release_packages["release-1"].approved_commit_sha = approved_commit_sha
        return state

    repo.update(_mutate)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert result["success"] is True
    assert result["state"] == TeamRecordState.BLOCKED.value
    assert error_snippet in result["error"]
    release = repo.read().release_packages["release-1"]
    assert release.published_commit_sha is None
    assert release.published_at is None


def test_mark_release_published_rejects_published_sha_mismatch(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _seed_publishable_release(repo)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="different-candidate",
    )

    assert result["success"] is True
    assert result["state"] == TeamRecordState.BLOCKED.value
    assert "does not match approved candidate" in result["error"]
    release = repo.read().release_packages["release-1"]
    assert release.published_commit_sha is None
    assert release.published_at is None


def test_mark_release_published_requires_succeeded_matching_canonical_promotion(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _seed_publishable_release(repo)

    def _mutate(state):
        state.git_promotions["canonical-promotion"].state = "pending"
        return state

    repo.update(_mutate)

    result = _mark_release_provider(
        tmp_path,
        release_package_id="release-1",
        success=True,
        canonical_branch="releases/project/release-1",
        published_commit_sha="cafef00d",
    )

    assert result["success"] is True
    assert result["state"] == TeamRecordState.BLOCKED.value
    assert "requires a linked succeeded canonical promotion" in result["error"]
    release = repo.read().release_packages["release-1"]
    assert release.published_commit_sha is None
    assert release.published_at is None


def test_human_review_publication_guard_requires_one_approved_pinned_request() -> None:
    release = SimpleNamespace(
        id="release-1",
        candidate_tree_hash="tree-1",
        manifest=SimpleNamespace(
            human_review_requirement=object(),
            carried_human_findings=[],
            candidate_commit_sha="cafef00d",
            candidate_tree_hash="tree-1",
        ),
    )
    state = SimpleNamespace(
        human_review_requests={},
        human_review_decisions={},
    )

    assert "requires exactly one human review request" in (
        _human_review_publication_validation_error(
            state,
            release=release,
            candidate_commit_sha="cafef00d",
        )
        or ""
    )

    request = SimpleNamespace(
        id="human-review-1",
        release_package_id="release-1",
        state="pending",
        candidate_commit_sha="cafef00d",
        candidate_tree_hash="tree-1",
    )
    state.human_review_requests = {"human-review-1": request}
    assert "is not approved" in (
        _human_review_publication_validation_error(
            state,
            release=release,
            candidate_commit_sha="cafef00d",
        )
        or ""
    )

    request.state = "approved"
    assert "requires exactly one durable approval decision" in (
        _human_review_publication_validation_error(
            state,
            release=release,
            candidate_commit_sha="cafef00d",
        )
        or ""
    )
    state.human_review_decisions = {
        "decision-1": SimpleNamespace(
            human_review_request_id="human-review-1",
            decision="approved",
            confirmed_finding_ids=[],
        )
    }
    assert (
        _human_review_publication_validation_error(
            state,
            release=release,
            candidate_commit_sha="cafef00d",
        )
        is None
    )


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
    assert notice.item_type == TeamInboxItemType.STATUS_REQUEST
    assert notice.payload["kind"] == "blocker_reported"
    blocker = state.blockers[notice.payload["blocker_id"]]
    assert blocker.source_kind.value == "release_package"
    assert blocker.source_id == "release-1"
    assert blocker.owner_agent_id == "lead"


# --- managed Git delegation effects -----------------------------------------


class _ManagedGitAdminStub:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def consolidate_branches(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return dict(self.result)


class _AdminRuntimeStub:
    def __init__(
        self, *, promotion_result: dict[str, object], consolidation_result: dict[str, object]
    ) -> None:
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
            required_source_ancestor_commit_sha="a" * 40,
            expected_source_commits={"team/team/features/task-backend": "b" * 40},
            allowed_changed_paths=["src/backend.py"],
            commit_message="Assemble release release-1",
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
            "base_commit_sha": None,
            "required_source_ancestor_commit_sha": "a" * 40,
            "expected_source_commits": {"team/team/features/task-backend": "b" * 40},
            "allowed_changed_paths": ["src/backend.py"],
            "commit_message": "Assemble release release-1",
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
