from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import evander_team_addon.services.admin_service as admin_service_module
from evander_team_addon.models import (
    AgentRuntime,
    AgentTask,
    GitPromotionRecord,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    PersistentAgent,
    ReleasePackage,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamDispatch,
    TeamDispatchStatus,
    TeamState,
    TeamWorkLineage,
    WorkLineageChatSourceRef,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.commands import TeamTaskCreationCommandService
from evander_team_addon.services.domain.common import utc_now
from evander_team_addon.services.domain.git_promotion import (
    _materialize_merge_recovery_task,
)
from evander_team_addon.services.domain.work_lineage import (
    ensure_task_work_lineages,
    work_lineage_id_for_record,
)
from evander_team_addon.templates import TeamTemplate


def _seed_project(repository: TeamStateRepository) -> None:
    admin = TeamAdminService(repository)
    admin.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=repository.read().revision,
    )


def _lease_project_dispatch(repository: TeamStateRepository) -> None:
    def _mutate(state: TeamState) -> TeamState:
        now = utc_now()
        expires_at = now + timedelta(minutes=15)
        state.dispatches["dispatch-lineage"] = TeamDispatch(
            id="dispatch-lineage",
            agent_id="lead",
            session_id="session-lead",
            cause_kind="scheduled",
            project_ids=["project"],
            status=TeamDispatchStatus.LEASED,
            leased_at=now,
            lease_expires_at=expires_at,
        )
        state.agent_runtimes["lead"] = AgentRuntime(
            agent_id="lead",
            active_dispatch_id="dispatch-lineage",
            wake_leased_until=expires_at,
        )
        return state

    repository.update(_mutate)


def test_record_resolution_follows_release_and_human_review_lineages() -> None:
    state = TeamState.empty()
    lineage = TeamWorkLineage(id="lineage-release")
    colliding_task_lineage = TeamWorkLineage(id="lineage-colliding-task")
    release = ReleasePackage(
        id="release",
        project_id="project",
        work_lineage_id=lineage.id,
    )
    human_review = HumanReviewRequest(
        id="human-review",
        release_package_id=release.id,
        candidate_commit_sha="a" * 40,
        candidate_tree_hash="b" * 40,
        requirement_snapshot=HumanReviewRequirementSnapshot(
            directory_path="root",
            configured_group_ids=["reviewers"],
            initially_eligible_user_ids=[],
        ),
        state="preparing",
    )
    release_blocker = TeamBlocker(
        id="release-blocker",
        team_id="team",
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=release.id,
        owner_agent_id="owner",
        summary="Release blocked",
    )
    human_review_blocker = TeamBlocker(
        id="human-review-blocker",
        team_id="team",
        source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
        source_id=human_review.id,
        owner_agent_id="owner",
        summary="Human review blocked",
    )
    explicit_legacy_blocker = TeamBlocker(
        id="explicit-legacy-blocker",
        team_id="team",
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id=human_review.id,
        owner_agent_id="owner",
        summary="Legacy explicit source",
    )
    non_work_blocker = TeamBlocker(
        id="agent-stall-blocker",
        team_id="team",
        source_kind=TeamBlockerSourceKind.AGENT_EXECUTION_STALL,
        source_id=human_review.id,
        owner_agent_id="owner",
        summary="Known non-work source with a colliding ID",
    )
    state.work_lineages[lineage.id] = lineage
    state.work_lineages[colliding_task_lineage.id] = colliding_task_lineage
    state.release_packages[release.id] = release
    state.human_review_requests[human_review.id] = human_review
    state.tasks[human_review.id] = AgentTask(
        id=human_review.id,
        owner_agent_id="owner",
        project_id="project",
        work_lineage_id=colliding_task_lineage.id,
        priority=0,
        title="Colliding task",
        description="Uses the same local ID as the human-review request.",
        created_by_actor="test",
    )

    assert work_lineage_id_for_record(state, release_blocker) == lineage.id
    assert work_lineage_id_for_record(state, human_review_blocker) == lineage.id
    assert (
        work_lineage_id_for_record(state, explicit_legacy_blocker)
        == colliding_task_lineage.id
    )
    assert work_lineage_id_for_record(state, non_work_blocker) is None


def test_batch_task_creation_assigns_forward_child_to_parent_lineage(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    _seed_project(repository)
    _lease_project_dispatch(repository)

    state, result = TeamTaskCreationCommandService(repository).create_tasks_command(
        session_id="session-lead",
        request_id="forward-parent-lineage",
        tasks=[
            {
                "task_id": "child",
                "owner_agent_id": "lead",
                "project_id": "project",
                "parent_task_id": "parent",
                "title": "Child",
                "description": "Appears before its parent.",
            },
            {
                "task_id": "parent",
                "owner_agent_id": "lead",
                "project_id": "project",
                "title": "Parent",
                "description": "Owns the shared work lineage.",
            },
        ],
    )

    assert result["success"] is True, result.get("errors")
    assert state.tasks["child"].work_lineage_id == state.tasks["parent"].work_lineage_id
    assert len(state.work_lineages) == 1


def test_forward_parent_assignment_keeps_chat_sources_on_shared_lineage() -> None:
    state = TeamState.empty()
    child = AgentTask(
        id="child",
        owner_agent_id="owner",
        project_id="project",
        parent_task_id="parent",
        priority=0,
        title="Child",
        description="Appears before its parent.",
        created_by_actor="test",
    )
    parent = AgentTask(
        id="parent",
        owner_agent_id="owner",
        project_id="project",
        priority=0,
        title="Parent",
        description="Owns the shared work lineage.",
        created_by_actor="test",
    )
    state.tasks = {child.id: child, parent.id: parent}
    source_ref = WorkLineageChatSourceRef(
        conversation_id="conversation",
        message_ids=["message"],
    )

    ensure_task_work_lineages(
        state,
        [child, parent],
        chat_source_refs=[source_ref],
    )

    assert child.work_lineage_id == parent.work_lineage_id
    assert len(state.work_lineages) == 1
    lineage = state.work_lineages[str(parent.work_lineage_id)]
    assert lineage.chat_source_refs == [source_ref]


def test_agent_action_batch_assigns_forward_child_to_parent_lineage(
    tmp_path: Path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    _seed_project(repository)

    state, result = TeamAgentActionService(repository).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "task_id": "child",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "parent_task_id": "parent",
                    "title": "Child",
                    "description": "Appears before its parent.",
                },
                {
                    "task_id": "parent",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "title": "Parent",
                    "description": "Owns the shared work lineage.",
                },
            ],
        }
    )

    assert result["success"] is True, result.get("errors")
    assert state.tasks["child"].work_lineage_id == state.tasks["parent"].work_lineage_id
    assert len(state.work_lineages) == 1


def test_template_materialization_assigns_forward_child_to_parent_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = TeamTemplate.model_validate(
        {
            "id": "forward-parent-template",
            "name": "Forward parent template",
            "root_role_key": "manager",
            "required_role_keys": ["manager", "project_lead"],
            "roles": [
                {"key": "manager", "label": "Manager"},
                {
                    "key": "project_lead",
                    "label": "Project lead",
                    "reports_to_role_key": "manager",
                },
            ],
            "tasks": [
                {
                    "id": "child",
                    "title": "Child",
                    "description": "Appears before its parent.",
                    "owner_role_key": "project_lead",
                    "created_by_role_key": "project_lead",
                    "parent_task_id": "parent",
                },
                {
                    "id": "parent",
                    "title": "Parent",
                    "description": "Owns the shared work lineage.",
                    "owner_role_key": "project_lead",
                    "created_by_role_key": "project_lead",
                },
            ],
        }
    )
    monkeypatch.setattr(
        admin_service_module,
        "get_template",
        lambda template_id: template.model_copy(deep=True)
        if template_id == template.id
        else None,
    )
    repository = TeamStateRepository(tmp_path)

    state, result = TeamAdminService(repository).materialize_template(
        template.id,
        inputs={
            "team": {"id": "team", "name": "Team"},
            "project": {"id": "project", "name": "Project"},
            "manager": {
                "agent_id": "manager",
                "session_id": "session-manager",
            },
            "project_lead": {
                "agent_id": "lead",
                "session_id": "session-lead",
            },
        },
        expected_revision=0,
    )

    assert result["success"] is True
    child = state.tasks["project-child"]
    parent = state.tasks["project-parent"]
    assert child.work_lineage_id == parent.work_lineage_id
    assert len(state.work_lineages) == 1


def test_merge_recovery_task_uses_affected_release_lineage() -> None:
    state = TeamState.empty()
    state.agents["owner"] = PersistentAgent(
        id="owner",
        team_id="team",
        role_id="role",
        session_id="session-owner",
    )
    lineage = TeamWorkLineage(id="release-lineage")
    release = ReleasePackage(
        id="release",
        project_id="project",
        owner_agent_id="owner",
        work_lineage_id=lineage.id,
    )
    promotion = GitPromotionRecord(
        id="promotion",
        project_id="project",
        release_package_id=release.id,
        actor_agent_id="owner",
        source_branch="agents/owner/conflict",
        target_branch="team/team/features/conflict",
        promotion_kind="feature",
        state="failed",
        error="Merge conflict",
        outputs={"error_code": "merge_conflict"},
    )
    blocker = TeamBlocker(
        id="blocker",
        team_id="team",
        project_id="project",
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=release.id,
        owner_agent_id="owner",
        summary="Release merge conflict",
    )
    state.work_lineages[lineage.id] = lineage
    state.release_packages[release.id] = release
    state.git_promotions[promotion.id] = promotion
    state.blockers[blocker.id] = blocker
    result: dict[str, object] = {}

    _materialize_merge_recovery_task(
        state,
        release=release,
        failed_promotions=[promotion],
        owner_agent_id="owner",
        assignment_reason="single_source_owner",
        blocker=blocker,
        result=result,
    )

    recovery = state.tasks[str(result["merge_recovery_task_id"])]
    assert recovery.release_base_package_id == release.id
    assert recovery.work_lineage_id == lineage.id
    assert len(state.work_lineages) == 1
