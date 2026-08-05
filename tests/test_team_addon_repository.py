from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import evander_team_addon.services.domain.common as team_domain_common
from evander_team_addon.models import (
    AgentTask,
    AgentAvatar,
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentRuntime,
    AgentStandDownSource,
    AgentStatus,
    AgentVoiceProfile,
    Deliverable,
    DeliverableGitRefs,
    DeliverableInitialReleaseDisposition,
    DeliverableReviewManifest,
    DeliverableVerification,
    EphemeralAgentPolicy,
    EphemeralAssignmentState,
    GitWorkContext,
    GitWorkContextState,
    GitPromotionRecord,
    GovernanceAgendaItemType,
    GovernanceAgendaItemState,
    GovernanceRuntime,
    HumanReviewFinding,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    InitialReleaseDispositionState,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ReleaseReviewRequirement,
    ReleaseVerificationCommand,
    ProjectGoal,
    ProjectStatus,
    ReleasePackage,
    ReviewBlockKind,
    ReviewCriterionResult,
    ReviewRequest,
    ReviewProfileCriterion,
    RolePermission,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamDispatch,
    TeamDispatchReviewRoutingAuthority,
    TeamDispatchStatus,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamCase,
    TeamCaseStatus,
    TeamIdempotencyRecord,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamInboxItemType,
    TeamMemoryScope,
    TeamMemoryStatus,
    TeamOutboxRecord,
    TeamOutboxStatus,
    TeamRecordState,
    SCHEMA_VERSION,
    TeamState,
    TaskCompletionKind,
    TeamTaskKind,
    TeamTaskState,
    TaskDeliverableContract,
    agent_project_ids,
    effective_outcome_kind_definitions,
    project_lead_agent_id,
    runtime_operations_payload,
)
from evander_team_addon.repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from evander_team_addon.services.domain.chat import CHAT_MESSAGE_QUEUED_EVENT_TYPE
from evander_team_addon.services.domain.blockers import open_or_get_blocker
from evander_team_addon.services.domain.common import DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
from evander_team_addon.services.domain.ephemeral import (
    EPHEMERAL_COMPLETED_EVENT_TYPE,
    EPHEMERAL_REQUESTED_EVENT_TYPE,
)
from evander_team_addon.services.domain.git_promotion import GIT_PROMOTION_EVENT_TYPE
from evander_team_addon.services.domain.release_packages import GIT_RELEASE_RECOVERY_EVENT_TYPE
from evander_team_addon.services.domain.review import _review_decision_policy_error
from evander_team_addon.services.domain.task_assessment import (
    TaskAssessmentBoundaryError,
    TaskAssessmentService,
    require_release_scope,
)
from evander_team_addon.services.domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import (
    TeamChatService,
    project_scoped_team_chat_authorization,
)
from evander_team_addon.services.ephemeral_service import TeamEphemeralService
from evander_team_addon.services.git_release_service import TeamGitReleaseService
from evander_team_addon.services.release_gate_service import TeamReleaseGateService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.services.commands import (
    TeamAgentControlCommandService,
    TeamAgentMessageCommandService,
    TeamBlockerCommandService,
    TeamCaseCommandService,
    TeamChatCommandService,
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamDeliverableSubmissionCommandService,
    TeamEphemeralCommandService,
    TeamGitReleaseCommandService,
    TeamGovernanceCommandService,
    TeamMemoryCommandService,
    TeamNoteCommandService,
    TeamOperatorEscalationCommandService,
    TeamOutcomeCatalogCommandService,
    TeamReviewCommandService,
    TeamTaskCreationCommandService,
    TeamTaskUpdateCommandService,
)
from evander_team_addon.sqlite_store import (
    SQLITE_SCHEMA_VERSION,
    TeamIdempotencyConflictError,
    _ensure_team_conversations_operator_scope,
)
from evander_team_addon.services.memory_service import (
    list_memory_records,
    team_memory_collection_name,
    team_memory_point_id,
    team_memory_point_payload,
)


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"


class _TeamCommandTestHarness(
    TeamNoteCommandService,
    TeamAgentMessageCommandService,
    TeamChatCommandService,
    TeamOperatorEscalationCommandService,
    TeamCaseCommandService,
    TeamTaskCreationCommandService,
    TeamTaskUpdateCommandService,
    TeamAgentControlCommandService,
    TeamBlockerCommandService,
    TeamGovernanceCommandService,
    TeamMemoryCommandService,
    TeamDeliverableSubmissionCommandService,
    TeamReviewCommandService,
    TeamGitReleaseCommandService,
    TeamEphemeralCommandService,
    TeamOutcomeCatalogCommandService,
):
    """Local test harness for cases that intentionally span command families."""


def _command_service(repo: TeamStateRepository) -> _TeamCommandTestHarness:
    return _TeamCommandTestHarness(repo)


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_chat_service(repo: TeamStateRepository) -> TeamChatService:
    return TeamChatService(repo)


def _authorized_chat_mutation(repo: TeamStateRepository, agent_id: str):
    state = repo.read()
    return project_scoped_team_chat_authorization(
        state,
        agent=state.agents[agent_id],
        source="internal",
        principal_id="admin",
        recipient_directory_path="root",
        project_is_authorized=lambda _directory_path: True,
    )


def _team_runtime_service(repo: TeamStateRepository) -> TeamRuntimeService:
    return TeamRuntimeService(repo)


def _team_agent_action_service(repo: TeamStateRepository) -> TeamAgentActionService:
    return TeamAgentActionService(repo)


def _team_ephemeral_service(repo: TeamStateRepository) -> TeamEphemeralService:
    return TeamEphemeralService(repo)


def _team_git_release_service(repo: TeamStateRepository) -> TeamGitReleaseService:
    return TeamGitReleaseService(repo)


def _team_release_gate_service(repo: TeamStateRepository) -> TeamReleaseGateService:
    return TeamReleaseGateService(repo)


def _seed_git_work_context(
    repo: TeamStateRepository,
    *,
    context_id: str = "ctx-task",
    agent_id: str = "backend",
    task_id: str = "task",
    dispatch_id: str | None = "dispatch-slice7",
    agent_branch: str = "agents/session-backend/task",
    commit_sha: str | None = "abc123",
    context_state: GitWorkContextState = GitWorkContextState.ACTIVE,
    updated_at: datetime | None = None,
) -> None:
    def _mutate(state_model):
        state_model.git_work_contexts[context_id] = GitWorkContext(
            id=context_id,
            agent_id=agent_id,
            task_id=task_id,
            dispatch_id=dispatch_id,
            worktree_path=f"/worktrees/{context_id}",
            base_branch="main",
            base_commit="base123",
            agent_branch=agent_branch,
            commit_sha=commit_sha,
            state=context_state,
            **({"updated_at": updated_at} if updated_at is not None else {}),
        )

    repo.update(_mutate)


def _seed_hierarchy(tmp_path: Path) -> TeamStateRepository:
    """manager -> lead (project) -> backend -> worker, all in one team/project."""

    repo = TeamStateRepository(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        manager_display_name="  Team Manager  ",
        expected_revision=0,
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_role(
        role_id="project__worker",
        team_id="team",
        project_id="project",
        name="Worker",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        display_name="Backend Agent",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="worker",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-worker",
        reports_to_agent_id="backend",
        display_name="  Worker Agent  ",
        expected_revision=repo.read().revision,
    )
    return repo


def _seed_hidden_memory_targets(repo: TeamStateRepository) -> None:
    admin = _team_admin_service(repo)
    admin.create_project(
        project_id="other-project",
        team_id="team",
        name="Other Project",
        lead_agent_id="other-lead",
        lead_session_id="session-other-lead",
        lead_display_name="Other Lead",
        goals=[ProjectGoal(id="other-goal", description="Ship other work")],
        expected_revision=repo.read().revision,
    )
    admin.create_or_update_memory(
        memory_id="memory-other-project",
        team_id="team",
        project_id="other-project",
        scope=TeamMemoryScope.PROJECT.value,
        kind="decision",
        title="Other project decision",
        summary="Only agents visible to the other project should mutate this.",
        expected_revision=repo.read().revision,
    )
    admin.create_or_update_memory(
        memory_id="memory-worker-private",
        team_id="team",
        agent_id="worker",
        scope=TeamMemoryScope.AGENT_PRIVATE.value,
        kind="fact",
        title="Worker private note",
        summary="Only the worker should mutate this.",
        expected_revision=repo.read().revision,
    )


def _lease_backend(
    repo: TeamStateRepository,
    *,
    dispatch_id: str = "dispatch-finalize",
    wake_reason: str = "scheduled",
    no_progress_count: int = 0,
) -> None:
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id=dispatch_id,
        wake_reason=wake_reason,
    )

    def _set_no_progress(state):
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            active_dispatch_id=dispatch_id,
            wake_leased_until=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
            wake_reason=wake_reason,
            no_progress_count=no_progress_count,
        )
        return state

    repo.update(_set_no_progress)


def _lease_agent_dispatch(
    repo: TeamStateRepository,
    *,
    agent_id: str,
    dispatch_id: str = "dispatch-command",
    wake_reason: str = "inbox",
) -> None:
    def _lease(state):
        agent = state.agents[agent_id]
        assessments = TaskAssessmentService(state)
        project_ids = sorted(
            project.id
            for project in state.projects.values()
            if project.team_id == agent.team_id
        )
        task_ids = sorted(
            task.id
            for task in state.tasks.values()
            if task.project_id in project_ids
            and task.owner_agent_id == agent_id
            and assessments.assess(task.id).readiness.value == "actionable"
        )
        blocker_ids: list[str] = []
        for blocker in state.blockers.values():
            assigned = (
                blocker.route == TeamBlockerRoute.DELEGATED
                and blocker.delegate_agent_id == agent_id
            ) or (
                blocker.route
                in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
                and blocker.owner_agent_id == agent_id
            )
            if blocker.state == TeamBlockerState.OPEN and assigned:
                blocker.active_dispatch_id = dispatch_id
                blocker_ids.append(blocker.id)
        state.dispatches[dispatch_id] = TeamDispatch(
            id=dispatch_id,
            agent_id=agent_id,
            session_id=agent.session_id,
            cause_kind=wake_reason,
            task_ids=task_ids,
            blocker_ids=sorted(blocker_ids),
            review_request_ids=sorted(
                review.id
                for review in state.review_requests.values()
                if review.reviewer_agent_id == agent_id
                and review.state
                in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
            ),
            inbox_item_ids=sorted(
                item.id
                for item in state.inbox.values()
                if item.recipient_agent_id == agent_id
                and item.state == TeamInboxItemState.PENDING
            ),
            project_ids=project_ids,
            managed_repo_ids=sorted(
                {
                    project.git.managed_repo_id
                    for project in state.projects.values()
                    if project.id in project_ids and project.git is not None
                }
            ),
            status=TeamDispatchStatus.LEASED,
            prepared_at=datetime(2098, 12, 31, 23, 59, tzinfo=UTC),
            leased_at=datetime(2098, 12, 31, 23, 59, tzinfo=UTC),
            lease_expires_at=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
        )
        state.agent_runtimes[agent_id] = AgentRuntime(
            agent_id=agent_id,
            active_dispatch_id=dispatch_id,
            wake_leased_until=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
            wake_reason=wake_reason,
        )
        return state

    repo.update(_lease)


def _report_explicit_blocker(
    repo: TeamStateRepository,
    *,
    reporter_agent_id: str,
    reporter_session_id: str,
    source_id: str,
) -> str:
    dispatch_id = f"dispatch-report-{source_id}"
    _lease_agent_dispatch(
        repo,
        agent_id=reporter_agent_id,
        dispatch_id=dispatch_id,
    )
    _state, result = _command_service(repo).report_blocker_command(
        session_id=reporter_session_id,
        source_kind="explicit",
        source_id=source_id,
        summary=f"Blocked on {source_id}",
        details="A local decision or dependency is required.",
        project_id="project",
        request_id=f"request-report-{source_id}",
    )
    assert result["success"] is True
    _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id=reporter_session_id,
        dispatch_id=dispatch_id,
    )
    return result["blocker_id"]


def _register_release_operator(
    repo: TeamStateRepository,
    *,
    role_id: str = "project__release-operator",
    permissions: list[str] | None = None,
) -> None:
    if role_id not in repo.read().roles:
        _team_admin_service(repo).create_role(
            role_id=role_id,
            team_id="team",
            project_id="project",
            name="Release Operator",
            permissions=permissions if permissions is not None else ["request_release"],
            expected_revision=repo.read().revision,
        )
    _team_admin_service(repo).register_agent(
        agent_id="release-operator",
        team_id="team",
        project_id="project",
        role_id=role_id,
        session_id="session-release-operator",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )


def _auto_dev_squad_inputs(
    *,
    suffix: str = "auto-dev-squad",
    duplicate_reviewer_id: str | None = None,
    bad_git: bool = False,
) -> dict[str, object]:
    reviewer_agent_id = duplicate_reviewer_id or f"reviewer-{suffix}"
    project_git: dict[str, object] = {
        "managed_repo_id": MANAGED_REPO_ID,
        "base_branch": "main",
    }
    if bad_git:
        project_git["managed_repo_id"] = "not-a-managed-repository"
    return {
        "team": {
            "id": f"team-{suffix}",
            "name": "Auto-Dev Squad",
            "operator_communication_target_id": "slack://ops-channel",
        },
        "project": {
            "id": f"project-{suffix}",
            "name": "Reviewed Auto-Dev Delivery",
            "git": project_git,
        },
        "manager": {
            "agent_id": f"manager-{suffix}",
            "session_id": f"session-manager-{suffix}",
        },
        "project_lead": {
            "agent_id": f"project-lead-{suffix}",
            "session_id": f"session-project-lead-{suffix}",
        },
        "implementer": {
            "agent_id": f"implementer-{suffix}",
            "session_id": f"session-implementer-{suffix}",
        },
        "reviewer": {
            "agent_id": reviewer_agent_id,
            "session_id": f"session-reviewer-{suffix}",
        },
        "release_operator": {
            "agent_id": f"release-operator-{suffix}",
            "session_id": f"session-release-operator-{suffix}",
        },
    }


def _accounting_department_ui_inputs() -> dict[str, object]:
    return {
        "team": {
            "name": "Accounting Department",
            "operator_communication_target_id": "teams://accounting-ops",
            "escalate_to_governance": True,
        },
        "project": {
            "name": "Bookkeeping Operations",
        },
        "roles": {
            "manager": {"session_id": "session-manager-accounting"},
            "project_lead": {"session_id": "session-bookkeeping-lead-accounting"},
            "bookkeeping_clerk": {"session_id": "session-bookkeeping-clerk-accounting"},
            "invoice_reviewer": {"session_id": "session-invoice-reviewer-accounting"},
            "payment_preparer": {"session_id": "session-payment-preparer-accounting"},
            "tax_compliance_reviewer": {"session_id": "session-tax-compliance-reviewer-accounting"},
        },
    }


def test_repository_initializes_private_addon_state(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.read()

    assert state.revision == 0
    assert state.teams == {}
    assert (tmp_path / "addons" / "team-addon" / "state.db").is_file()
    assert not (tmp_path / "addons" / "team-addon" / "state.json").exists()


def test_repository_upgrades_persisted_state_schema_metadata(
    tmp_path: Path,
) -> None:
    _seed_hierarchy(tmp_path)
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE team_state_metadata
            SET value = '2'
            WHERE key = 'state_schema_version'
            """
        )

    upgraded = TeamStateRepository(tmp_path).read()

    assert upgraded.schema_version == SCHEMA_VERSION
    with sqlite3.connect(db_path) as connection:
        persisted_version = connection.execute(
            """
            SELECT value
            FROM team_state_metadata
            WHERE key = 'state_schema_version'
            """
        ).fetchone()[0]
    assert persisted_version == str(SCHEMA_VERSION)


def test_repository_mcp_storage_tables_preserve_state_projection(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state_before = repo.read().model_dump(mode="json")

    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-1",
            tool_name="team_record_note",
            caller_session_id="session-backend",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id="dispatch-1",
            request_id="request-1",
            correlation_id="correlation-1",
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="context",
            action_record_id="action-1",
        )
    )
    repo.store_idempotency_record(
        TeamIdempotencyRecord(
            id="idem-1",
            tool_name="team_record_note",
            caller_identity="agent:backend",
            scope_kind="dispatch",
            scope_id="dispatch-1",
            request_id="request-1",
            normalized_payload_digest=repo.normalized_payload_digest({"body": "note"}),
            response_payload={"success": True, "activity_id": "activity-1"},
            created_domain_ids=["note-1"],
            created_event_ids=[],
        )
    )
    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-1",
            intent_type="team.ephemeral.start",
            aggregate_type="ephemeral_assignment",
            aggregate_id="assignment-1",
            idempotency_key="team-addon:team.ephemeral.start:assignment-1",
            payload={"assignment_id": "assignment-1"},
        )
    )

    reopened = TeamStateRepository(tmp_path)
    reopened.store.ensure_schema()

    assert reopened.read().model_dump(mode="json") == state_before
    assert [activity.id for activity in reopened.list_dispatch_activity()] == ["activity-1"]
    assert reopened.get_idempotency_record(
        tool_name="team_record_note",
        caller_identity="agent:backend",
        scope_kind="dispatch",
        scope_id="dispatch-1",
        request_id="request-1",
    ).response_payload == {"success": True, "activity_id": "activity-1"}
    assert [record.id for record in reopened.list_outbox_records()] == ["outbox-1"]


def test_repository_records_dispatch_activity_failure_context(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    repo.append_dispatch_activity(
        {
            "id": "activity-failed",
            "tool_name": "team_update_task",
            "caller_session_id": "session-backend",
            "caller_identity": "agent:backend",
            "agent_id": "backend",
            "dispatch_id": "dispatch-1",
            "request_id": "request-1",
            "causation_id": "inbox-1",
            "status": "failure",
            "progress_classification": "none",
            "audit_record_id": "audit-1",
            "error_code": "task_not_owned",
            "error_message": "task task-1 is owned by lead",
            "correction_context_summary": "owner lead; caller backend",
        }
    )

    activities = repo.list_dispatch_activity(
        dispatch_id="dispatch-1",
        request_id="request-1",
    )

    assert len(activities) == 1
    assert activities[0].status == TeamDispatchActivityStatus.FAILURE
    assert activities[0].causation_id == "inbox-1"
    assert activities[0].error_code == "task_not_owned"
    assert activities[0].correction_context_summary == "owner lead; caller backend"


def test_repository_idempotency_returns_original_result_for_identical_replay(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    digest = repo.normalized_payload_digest(
        {"body": "Useful handoff", "metadata": {"b": 2, "a": 1}}
    )
    assert digest == repo.normalized_payload_digest(
        {"metadata": {"a": 1, "b": 2}, "body": "Useful handoff"}
    )

    record = repo.store_idempotency_record(
        TeamIdempotencyRecord(
            id="idem-note-1",
            tool_name="team_record_note",
            caller_identity="agent:backend",
            scope_kind="dispatch",
            scope_id="dispatch-1",
            request_id="request-1",
            normalized_payload_digest=digest,
            response_payload={
                "success": True,
                "applied": True,
                "activity_id": "activity-1",
                "note_id": "note-1",
            },
            created_domain_ids=["note-1"],
            created_event_ids=["event-1"],
        )
    )

    replay = repo.check_idempotency_replay(
        tool_name="team_record_note",
        caller_identity="agent:backend",
        scope_kind="dispatch",
        scope_id="dispatch-1",
        request_id="request-1",
        normalized_payload_digest=digest,
    )
    duplicate_store = repo.store_idempotency_record(
        record.model_copy(update={"id": "idem-note-duplicate"})
    )

    assert replay == record
    assert duplicate_store == record
    assert replay.response_payload["note_id"] == "note-1"
    assert replay.created_domain_ids == ["note-1"]
    assert replay.created_event_ids == ["event-1"]


def test_repository_idempotency_conflict_uses_key_before_digest(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    original_digest = repo.normalized_payload_digest({"body": "first"})
    conflicting_digest = repo.normalized_payload_digest({"body": "second"})
    repo.store_idempotency_record(
        TeamIdempotencyRecord(
            id="idem-note-1",
            tool_name="team_record_note",
            caller_identity="agent:backend",
            scope_kind="dispatch",
            scope_id="dispatch-1",
            request_id="request-1",
            normalized_payload_digest=original_digest,
            response_payload={"note_id": "note-1"},
            created_domain_ids=["note-1"],
        )
    )

    assert (
        repo.check_idempotency_replay(
            tool_name="team_record_note",
            caller_identity="agent:backend",
            scope_kind="dispatch",
            scope_id="dispatch-2",
            request_id="request-1",
            normalized_payload_digest=conflicting_digest,
        )
        is None
    )
    with pytest.raises(TeamIdempotencyConflictError) as excinfo:
        repo.check_idempotency_replay(
            tool_name="team_record_note",
            caller_identity="agent:backend",
            scope_kind="dispatch",
            scope_id="dispatch-1",
            request_id="request-1",
            normalized_payload_digest=conflicting_digest,
        )

    stored = repo.get_idempotency_record(
        tool_name="team_record_note",
        caller_identity="agent:backend",
        scope_kind="dispatch",
        scope_id="dispatch-1",
        request_id="request-1",
    )
    assert excinfo.value.existing_digest == original_digest
    assert excinfo.value.attempted_digest == conflicting_digest
    assert stored.response_payload == {"note_id": "note-1"}
    assert stored.created_domain_ids == ["note-1"]


def test_repository_outbox_enqueue_claim_complete_and_fail(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-1",
            intent_type="team.git.promotion_requested",
            aggregate_type="git_promotion",
            aggregate_id="promotion-1",
            idempotency_key="team-addon:git-promotion:promotion-1",
            payload={"promotion_id": "promotion-1"},
            available_at=now,
        )
    )
    duplicate = repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-duplicate",
            intent_type="team.git.promotion_requested",
            aggregate_type="git_promotion",
            aggregate_id="promotion-1",
            idempotency_key="team-addon:git-promotion:promotion-1",
            payload={"promotion_id": "promotion-1"},
            available_at=now,
        )
    )

    claimed = repo.claim_outbox_records(
        worker_id="worker-a",
        intent_types=["team.git.promotion_requested"],
        claim_token="claim-1",
        now=now,
    )
    replayed_claim = repo.claim_outbox_records(
        worker_id="worker-a",
        claim_token="claim-1",
        now=now + timedelta(seconds=1),
    )
    completed = repo.complete_outbox_record(
        record_id=first.id,
        claim_token="claim-1",
        now=now + timedelta(seconds=2),
    )

    assert duplicate.id == first.id
    assert len(repo.list_outbox_records()) == 1
    assert [record.id for record in claimed] == ["outbox-1"]
    assert claimed[0].status == TeamOutboxStatus.CLAIMED
    assert claimed[0].claimed_by == "worker-a"
    assert claimed[0].claim_token == "claim-1"
    assert claimed[0].attempt_count == 1
    assert replayed_claim == claimed
    assert completed.status == TeamOutboxStatus.COMPLETED

    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-2",
            intent_type="team.ephemeral.start",
            aggregate_type="ephemeral_assignment",
            aggregate_id="assignment-1",
            payload={"assignment_id": "assignment-1"},
            available_at=now,
        )
    )
    claimed_retry = repo.claim_outbox_records(
        worker_id="worker-a",
        intent_types=["team.ephemeral.start"],
        claim_token="claim-2",
        now=now,
    )
    failed = repo.fail_outbox_record(
        record_id=claimed_retry[0].id,
        claim_token="claim-2",
        error_code="worker_unavailable",
        error_message="worker VM is not ready",
        retry_after_seconds=30,
        now=now + timedelta(seconds=3),
    )

    assert failed.status == TeamOutboxStatus.PENDING
    assert failed.claim_token is None
    assert failed.last_error_code == "worker_unavailable"
    assert failed.last_error_message == "worker VM is not ready"
    assert failed.available_at == now + timedelta(seconds=33)


def test_repository_record_team_note_command_preserves_active_dispatch_lease(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    leased_until = datetime(2099, 1, 1, 0, 15, tzinfo=UTC)

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-note",
        wake_reason="inbox",
    )
    state_before = repo.read()

    state, result = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-1",
        correlation_id="corr-1",
        body="Remember to verify the deployment window.",
    )

    assert result["success"] is True
    assert result["applied"] is True
    assert result["progress_made"] is False
    assert result["revision"] == state_before.revision + 1
    assert result["note_id"] == result["action_record_id"]
    runtime = state.agent_runtimes["backend"]
    assert runtime.active_dispatch_id == "dispatch-note"
    assert runtime.wake_leased_until == leased_until
    record = state.action_records[result["action_record_id"]]
    assert record.success is True
    assert record.dispatch_id == "dispatch-note"
    assert record.action["tool_name"] == "team_record_note"
    assert record.action["body"] == "Remember to verify the deployment window."
    activities = repo.list_dispatch_activity(
        dispatch_id="dispatch-note",
        request_id="request-note-1",
    )
    assert [activity.status for activity in activities] == [TeamDispatchActivityStatus.SUCCESS]
    assert activities[0].action_record_id == result["action_record_id"]
    assert activities[0].progress_classification == "context"
    assert repo.list_outbox_records() == []


def test_repository_persists_team_memories_and_canonical_search(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, memory = _team_admin_service(repo).create_or_update_memory(
        memory_id="memory-architecture",
        team_id="team",
        project_id="project",
        codebase_id="repo-main",
        scope=TeamMemoryScope.PROJECT.value,
        kind="decision",
        title="Use Qdrant as a derived memory index",
        summary="Canonical Team memory stays in SQLite and Qdrant is rebuilt from it.",
        body="Long-form rationale belongs in artifacts and explicit memory detail calls.",
        tags=["Architecture", "memory"],
        source_refs=["task:task"],
        artifact_refs=["artifact-space:/teams/team/projects/project/memory/memory-architecture"],
        confidence=0.82,
        created_by="operator",
        expected_revision=repo.read().revision,
    )

    assert state.memories[memory.id].tags == ["architecture", "memory"]
    reloaded = TeamStateRepository(tmp_path).read()
    assert reloaded.memories["memory-architecture"].body == (
        "Long-form rationale belongs in artifacts and explicit memory detail calls."
    )

    search = list_memory_records(
        reloaded,
        team_id="team",
        project_id="project",
        codebase_id="repo-main",
        tags_any=["architecture"],
        query_text="rebuilt",
    )

    assert [record.id for record in search["records"]] == ["memory-architecture"]
    assert team_memory_collection_name("team").startswith("team_memory_team_")
    assert team_memory_collection_name("team") == team_memory_collection_name("team")
    assert team_memory_point_id("memory-architecture") == team_memory_point_id(
        "memory-architecture"
    )
    point_payload = team_memory_point_payload(reloaded.memories["memory-architecture"])
    assert point_payload["memory_id"] == "memory-architecture"
    assert point_payload["_semantic_text"].startswith("Title: Use Qdrant")

    archived_state, archived = _team_admin_service(repo).archive_memory(
        memory_id="memory-architecture",
        expected_revision=reloaded.revision,
    )
    assert archived.status.value == "archived"
    assert not list_memory_records(archived_state, team_id="team")["records"]
    assert (
        list_memory_records(
            archived_state,
            team_id="team",
            statuses=["archived"],
            include_archived=True,
        )["records"][0].id
        == "memory-architecture"
    )


def test_repository_record_memory_command_requires_dispatch_and_persists_memory(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-memory")

    state, result = _command_service(repo).record_memory_command(
        session_id="session-backend",
        request_id="request-memory-1",
        id="memory-command",
        project_id="project",
        codebase_id="repo-main",
        kind="preference",
        title="Keep wake prompts compact",
        summary="Do not inject Team memory into normal wake prompts.",
        body="Agents should call the explicit memory search tool when recall is needed.",
        tags=["Prompt", "memory"],
        artifact_space_ref="artifact-space:/teams/team/projects/project/memory/memory-command",
    )

    assert result["success"] is True
    assert result["progress_made"] is False
    assert result["memory_id"] == "memory-command"
    assert "body" not in result["memory"]
    memory = state.memories["memory-command"]
    assert memory.created_by_agent_id == "backend"
    assert memory.scope == TeamMemoryScope.PROJECT
    assert (
        memory.body == "Agents should call the explicit memory search tool when recall is needed."
    )
    assert memory.tags == ["prompt", "memory"]
    assert memory.artifact_refs == [
        "artifact-space:/teams/team/projects/project/memory/memory-command"
    ]

    state, update = _command_service(repo).record_memory_command(
        session_id="session-backend",
        request_id="request-memory-2",
        id="memory-command",
        project_id="project",
        codebase_id="repo-main",
        kind="preference",
        title="Keep wake prompts explicit",
        summary="Visible memories can be updated by their owning project agent.",
    )

    assert update["success"] is True
    assert state.memories["memory-command"].title == "Keep wake prompts explicit"

    activities = repo.list_dispatch_activity(
        dispatch_id="dispatch-memory",
        request_id="request-memory-1",
    )
    assert [activity.status for activity in activities] == [TeamDispatchActivityStatus.SUCCESS]


def test_repository_record_memory_command_clears_stale_supersession_on_reactivation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    service = _command_service(repo)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-memory-reactivate")

    service.record_memory_command(
        session_id="session-backend",
        request_id="request-memory-original",
        id="memory-original",
        project_id="project",
        kind="decision",
        title="Original decision",
        summary="Original summary.",
    )
    state, replacement = service.record_memory_command(
        session_id="session-backend",
        request_id="request-memory-replacement",
        id="memory-replacement",
        project_id="project",
        kind="decision",
        title="Replacement decision",
        summary="Replacement summary.",
        supersedes_memory_id="memory-original",
    )

    assert replacement["success"] is True
    assert state.memories["memory-original"].status == TeamMemoryStatus.SUPERSEDED
    assert state.memories["memory-original"].superseded_by_memory_id == "memory-replacement"

    state, reactivated = service.record_memory_command(
        session_id="session-backend",
        request_id="request-memory-reactivated",
        id="memory-original",
        project_id="project",
        kind="decision",
        title="Original decision reactivated",
        summary="Recording without supersedes should make this active again.",
    )

    assert reactivated["success"] is True
    reactivated_memory = state.memories["memory-original"]
    assert reactivated_memory.status == TeamMemoryStatus.ACTIVE
    assert reactivated_memory.superseded_by_memory_id is None
    assert reactivated_memory.supersedes_memory_id is None


def test_repository_record_memory_command_rejects_hidden_targets(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_hidden_memory_targets(repo)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-memory-hidden")

    before = repo.read()
    visible_ids = {
        memory.id
        for memory in list_memory_records(
            before,
            actor=before.agents["backend"],
            team_id="team",
            limit=50,
        )["records"]
    }
    assert "memory-other-project" not in visible_ids
    assert "memory-worker-private" not in visible_ids

    _state, hidden_update = _command_service(repo).record_memory_command(
        session_id="session-backend",
        request_id="request-memory-hidden-update",
        id="memory-worker-private",
        kind="fact",
        title="Overwrite private memory",
        summary="This should be rejected before the existing record is re-scoped.",
    )

    assert hidden_update["success"] is False
    assert hidden_update["errors"][0]["code"] == "memory_scope_conflict"
    private_memory = repo.read().memories["memory-worker-private"]
    assert private_memory.title == "Worker private note"
    assert private_memory.scope == TeamMemoryScope.AGENT_PRIVATE
    assert private_memory.agent_id == "worker"

    _state, hidden_supersedes = _command_service(repo).record_memory_command(
        session_id="session-backend",
        request_id="request-memory-hidden-supersedes",
        id="memory-new",
        project_id="project",
        kind="decision",
        title="Replacement decision",
        summary="This should not supersede hidden project memory.",
        supersedes_memory_id="memory-other-project",
    )

    assert hidden_supersedes["success"] is False
    assert hidden_supersedes["errors"][0]["code"] == "memory_scope_conflict"
    reread = repo.read()
    assert "memory-new" not in reread.memories
    hidden_project_memory = reread.memories["memory-other-project"]
    assert hidden_project_memory.status == TeamMemoryStatus.ACTIVE
    assert hidden_project_memory.superseded_by_memory_id is None


def test_repository_archive_memory_command_rejects_hidden_target_and_replacement(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_hidden_memory_targets(repo)
    admin = _team_admin_service(repo)
    admin.create_or_update_memory(
        memory_id="memory-visible",
        team_id="team",
        project_id="project",
        scope=TeamMemoryScope.PROJECT.value,
        kind="decision",
        title="Visible project decision",
        summary="Backend can archive this visible project memory.",
        expected_revision=repo.read().revision,
    )
    admin.create_or_update_memory(
        memory_id="memory-visible-replacement",
        team_id="team",
        project_id="project",
        scope=TeamMemoryScope.PROJECT.value,
        kind="decision",
        title="Visible replacement decision",
        summary="Backend can link this visible replacement.",
        expected_revision=repo.read().revision,
    )
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-memory-archive")

    _state, hidden_target = _command_service(repo).archive_memory_command(
        session_id="session-backend",
        request_id="request-archive-hidden-target",
        memory_id="memory-other-project",
    )

    assert hidden_target["success"] is False
    assert hidden_target["errors"][0]["code"] == "memory_scope_conflict"
    assert repo.read().memories["memory-other-project"].status == TeamMemoryStatus.ACTIVE

    _state, hidden_replacement = _command_service(repo).archive_memory_command(
        session_id="session-backend",
        request_id="request-archive-hidden-replacement",
        memory_id="memory-visible",
        superseded_by_memory_id="memory-other-project",
    )

    assert hidden_replacement["success"] is False
    assert hidden_replacement["errors"][0]["code"] == "memory_scope_conflict"
    visible_memory = repo.read().memories["memory-visible"]
    assert visible_memory.status == TeamMemoryStatus.ACTIVE
    assert visible_memory.superseded_by_memory_id is None

    state, archived = _command_service(repo).archive_memory_command(
        session_id="session-backend",
        request_id="request-archive-visible-replacement",
        memory_id="memory-visible",
        superseded_by_memory_id="memory-visible-replacement",
    )

    assert archived["success"] is True
    assert archived["status"] == TeamMemoryStatus.SUPERSEDED.value
    assert state.memories["memory-visible"].superseded_by_memory_id == (
        "memory-visible-replacement"
    )


def test_repository_record_team_note_command_exact_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-note",
    )

    _state, first = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-1",
        body="Same payload.",
    )
    state, second = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-1",
        body="Same payload.",
    )

    assert second == first
    assert list(state.action_records) == [first["action_record_id"]]
    assert [
        activity.id
        for activity in repo.list_dispatch_activity(
            dispatch_id="dispatch-note",
            request_id="request-note-1",
        )
    ] == [first["activity_id"]]


def test_repository_record_team_note_command_conflicting_replay_fails_before_mutation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-note",
    )

    _state, first = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-1",
        body="First payload.",
    )
    state, conflict = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-1",
        body="Different payload.",
    )

    assert conflict["success"] is False
    assert conflict["applied"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert list(state.action_records) == [first["action_record_id"]]
    assert len(repo.list_dispatch_activity(dispatch_id="dispatch-note")) == 1


def test_repository_record_team_note_validation_failure_records_activity_only(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-note",
    )
    state_before = repo.read()

    state, result = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-empty",
        body=" ",
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert result["progress_made"] is False
    assert result["errors"][0]["code"] == "empty_body"
    assert state.revision == state_before.revision
    assert state.action_records == {}
    assert state.agent_runtimes["backend"].active_dispatch_id == "dispatch-note"
    activities = repo.list_dispatch_activity(
        dispatch_id="dispatch-note",
        request_id="request-note-empty",
    )
    assert len(activities) == 1
    assert activities[0].status == TeamDispatchActivityStatus.FAILURE
    assert activities[0].error_code == "empty_body"
    assert repo.list_outbox_records() == []
    assert (
        repo.get_idempotency_record(
            tool_name="team_record_note",
            caller_identity="agent:backend",
            scope_kind="dispatch",
            scope_id="dispatch-note",
            request_id="request-note-empty",
        )
        is None
    )


def test_repository_record_team_note_handles_inbox_items_atomically(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _prepare_state(state):
        now = datetime(2099, 1, 1, tzinfo=UTC)
        state.inbox["inbox-owned"] = AgentInboxItem(
            id="inbox-owned",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            sender_agent_id="lead",
            payload={"question": "Status?"},
            created_at=now,
            updated_at=now,
        )
        return state

    repo.update(_prepare_state)
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-note",
    )

    state, result = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-inbox",
        body="Handled after checking the status request.",
        handled_inbox_item_ids=["inbox-owned"],
    )

    assert result["success"] is True
    assert result["handled_inbox_item_ids"] == ["inbox-owned"]
    assert state.inbox["inbox-owned"].state == TeamInboxItemState.HANDLED
    assert result["progress_made"] is False


def test_repository_record_team_note_invalid_inbox_item_is_all_or_nothing(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _prepare_state(state):
        now = datetime(2099, 1, 1, tzinfo=UTC)
        state.inbox["inbox-owned"] = AgentInboxItem(
            id="inbox-owned",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            sender_agent_id="lead",
            created_at=now,
            updated_at=now,
        )
        state.inbox["inbox-other"] = AgentInboxItem(
            id="inbox-other",
            recipient_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            sender_agent_id="manager",
            created_at=now,
            updated_at=now,
        )
        return state

    repo.update(_prepare_state)
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-note",
    )
    state_before = repo.read()

    state, result = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-bad-inbox",
        body="This should not land.",
        handled_inbox_item_ids=["inbox-owned", "inbox-other"],
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.revision == state_before.revision
    assert state.action_records == {}
    assert state.inbox["inbox-owned"].state == TeamInboxItemState.PENDING
    assert state.inbox["inbox-other"].state == TeamInboxItemState.PENDING
    activities = repo.list_dispatch_activity(
        dispatch_id="dispatch-note",
        request_id="request-note-bad-inbox",
    )
    assert len(activities) == 1
    assert activities[0].status == TeamDispatchActivityStatus.FAILURE
    assert repo.list_outbox_records() == []


def test_repository_mark_inbox_handled_command_is_all_or_nothing(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    def _prepare_state(state):
        now = datetime(2099, 1, 1, tzinfo=UTC)
        state.inbox["inbox-owned-1"] = AgentInboxItem(
            id="inbox-owned-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            sender_agent_id="lead",
            created_at=now,
            updated_at=now,
        )
        state.inbox["inbox-owned-2"] = AgentInboxItem(
            id="inbox-owned-2",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.QUESTION,
            sender_agent_id="lead",
            created_at=now,
            updated_at=now,
        )
        return state

    repo.update(_prepare_state)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-inbox")
    before = repo.read()

    state, result = _command_service(repo).mark_inbox_handled_command(
        session_id="session-backend",
        inbox_item_ids=["inbox-owned-1", "inbox-owned-2"],
    )

    assert result["success"] is True
    assert result["progress_made"] is True
    assert result["handled_inbox_item_ids"] == ["inbox-owned-1", "inbox-owned-2"]
    assert state.revision == before.revision + 1
    assert state.inbox["inbox-owned-1"].state == TeamInboxItemState.HANDLED
    assert state.inbox["inbox-owned-2"].state == TeamInboxItemState.HANDLED

    replay_state, replay = _command_service(repo).mark_inbox_handled_command(
        session_id="session-backend",
        inbox_item_ids=["inbox-owned-1"],
    )
    assert replay["success"] is False
    assert replay["applied"] is False
    assert replay["errors"][0]["code"] == "dispatch_scope_stale"
    assert replay_state.revision == state.revision


def test_repository_mark_inbox_handled_command_requires_active_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _command_service(repo).mark_inbox_handled_command(
        session_id="session-backend",
        inbox_item_ids=["inbox-missing"],
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "no_active_dispatch"
    assert state.inbox == {}


def test_repository_request_status_command_creates_subordinate_inbox_and_replays(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-status")
    before = repo.read()

    state, first = _command_service(repo).request_status_command(
        session_id="session-backend",
        recipient_agent_id="worker",
        question="What is the worker status?",
        request_id="request-status-1",
        priority="high",
    )
    state, second = _command_service(repo).request_status_command(
        session_id="session-backend",
        recipient_agent_id="worker",
        question="What is the worker status?",
        request_id="request-status-1",
        priority="high",
    )
    state, conflict = _command_service(repo).request_status_command(
        session_id="session-backend",
        recipient_agent_id="worker",
        question="Different status question?",
        request_id="request-status-1",
        priority="high",
    )

    assert second == first
    assert conflict["success"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert first["success"] is True
    assert first["inbox_item_id"] in state.inbox
    assert state.revision == before.revision + 1
    item = state.inbox[first["inbox_item_id"]]
    assert item.recipient_agent_id == "worker"
    assert item.sender_agent_id == "backend"
    assert item.item_type == TeamInboxItemType.STATUS_REQUEST
    assert item.priority == TeamInboxPriority.HIGH
    assert item.payload["question"] == "What is the worker status?"
    assert state.agent_runtimes["worker"].next_wake_at is None
    assert (
        len(
            repo.list_dispatch_activity(
                tool_name="team_request_status",
                request_id="request-status-1",
            )
        )
        == 1
    )


def test_repository_request_status_command_rejects_non_subordinate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-status")
    before = repo.read()

    state, result = _command_service(repo).request_status_command(
        session_id="session-backend",
        recipient_agent_id="lead",
        question="Status?",
        request_id="request-status-denied",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "recipient_not_direct_subordinate"
    assert state.revision == before.revision
    assert state.inbox == {}


def test_repository_question_and_progress_report_are_upward_only(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-upward")

    state, question = _command_service(repo).ask_question_command(
        session_id="session-backend",
        question="Can you clarify the rollout window?",
        request_id="request-question-1",
        summary="Rollout window clarification",
    )
    state, report = _command_service(repo).send_progress_report_command(
        session_id="session-backend",
        message="I finished the first backend slice.",
        request_id="request-progress-1",
    )

    question_item = state.inbox[question["inbox_item_id"]]
    report_item = state.inbox[report["inbox_item_id"]]
    assert question["success"] is True
    assert question_item.recipient_agent_id == "lead"
    assert question_item.item_type == TeamInboxItemType.QUESTION
    assert question_item.payload["summary"] == "Rollout window clarification"
    assert report["success"] is True
    assert report["context_rotation_activity"] is False
    assert report_item.recipient_agent_id == "lead"
    assert report_item.item_type == TeamInboxItemType.PROGRESS_REPORT
    assert report_item.response_requested is False
    assert report_item.priority == TeamInboxPriority.LOW

    _lease_agent_dispatch(repo, agent_id="manager", dispatch_id="dispatch-manager")
    _state, denied = _command_service(repo).ask_question_command(
        session_id="session-manager",
        question="Can an operator decide this?",
        request_id="request-question-top",
    )
    assert denied["success"] is False
    assert denied["errors"][0]["code"] == "top_level_manager_requires_operator_escalation"
    assert denied["correction_context"]["recommended_tool"] == "team_escalate_operator"


def test_repository_question_command_replay_and_conflict(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-question")

    _state, first = _command_service(repo).ask_question_command(
        session_id="session-backend",
        question="Same question?",
        request_id="request-question-replay",
    )
    state, replay = _command_service(repo).ask_question_command(
        session_id="session-backend",
        question="Same question?",
        request_id="request-question-replay",
    )
    _state, conflict = _command_service(repo).ask_question_command(
        session_id="session-backend",
        question="Different question?",
        request_id="request-question-replay",
    )

    assert replay == first
    assert conflict["success"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert [
        item.id for item in state.inbox.values() if item.item_type == TeamInboxItemType.QUESTION
    ] == [first["inbox_item_id"]]


def test_repository_progress_report_command_replay_and_validation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-progress")

    _state, first = _command_service(repo).send_progress_report_command(
        session_id="session-backend",
        message="Same progress.",
        request_id="request-progress-replay",
    )
    state, replay = _command_service(repo).send_progress_report_command(
        session_id="session-backend",
        message="Same progress.",
        request_id="request-progress-replay",
    )
    _state, conflict = _command_service(repo).send_progress_report_command(
        session_id="session-backend",
        message="Different progress.",
        request_id="request-progress-replay",
    )
    _state, validation = _command_service(repo).send_progress_report_command(
        session_id="session-backend",
        message=" ",
        request_id="request-progress-empty",
    )

    assert replay == first
    assert conflict["success"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert validation["success"] is False
    assert validation["errors"][0]["code"] == "empty_message"
    assert [
        item.id
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.PROGRESS_REPORT
    ] == [first["inbox_item_id"]]


def test_repository_reply_to_chat_command_marks_source_replied_without_finalizing(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you reply?",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id="team-chat:slice5",
        lease_seconds=60,
    )

    state, first = _command_service(repo).reply_to_chat_command(
        session_id="session-backend",
        conversation_id=conversation.id,
        message_id=message.id,
        body="Yes, I checked it.",
        request_id="request-chat-reply-1",
    )
    state, replay = _command_service(repo).reply_to_chat_command(
        session_id="session-backend",
        conversation_id=conversation.id,
        message_id=message.id,
        body="Yes, I checked it.",
        request_id="request-chat-reply-1",
    )
    _state, conflict = _command_service(repo).reply_to_chat_command(
        session_id="session-backend",
        conversation_id=conversation.id,
        message_id=message.id,
        body="Different reply.",
        request_id="request-chat-reply-1",
    )
    _state, duplicate = _command_service(repo).reply_to_chat_command(
        session_id="session-backend",
        conversation_id=conversation.id,
        message_id=message.id,
        body="Another new reply should fail.",
        request_id="request-chat-reply-2",
    )

    assert replay == first
    assert conflict["success"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert duplicate["success"] is False
    assert duplicate["errors"][0]["code"] == "chat_message_already_replied"
    assert first["chat_message_id"] in state.conversation_messages
    assert state.conversation_messages[message.id].status == "replied"
    assert state.agent_runtimes["backend"].active_dispatch_id == "team-chat:slice5"


def test_repository_reply_to_chat_command_rejects_non_chat_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you reply?",
        expected_revision=state.revision,
    )
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-not-chat")

    _state, result = _command_service(repo).reply_to_chat_command(
        session_id="session-backend",
        conversation_id=conversation.id,
        message_id=message.id,
        body="This should not land.",
        request_id="request-chat-denied",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "not_chat_dispatch"


def test_external_chat_ingress_is_idempotent_and_queues_normal_team_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    chat = _team_chat_service(repo)

    state, first = chat.enqueue_external_operator_chat_message(
        conversation_id=None,
        agent_id="backend",
        operator_user_id="operator-alice",
        operator_display_name="Operator Alice",
        body="Please check the bridge.",
        provider="discord",
        communication_target_id="codex-discord",
        source_message_id="discord-message-1",
        source_thread_id=None,
        source_author_id="discord-operator",
        source_author_name="Operator Alice",
    )
    replay_state, replay = chat.enqueue_external_operator_chat_message(
        conversation_id=None,
        agent_id="backend",
        operator_user_id="operator-alice",
        operator_display_name="Operator Alice",
        body="Please check the bridge.",
        provider="discord",
        communication_target_id="codex-discord",
        source_message_id="discord-message-1",
        source_thread_id=None,
        source_author_id="discord-operator",
        source_author_name="Operator Alice",
    )

    assert first["duplicate"] is False
    assert replay["duplicate"] is True
    assert replay["message_id"] == first["message_id"]
    assert replay["dispatch_id"] == first["dispatch_id"]
    message = state.conversation_messages[first["message_id"]]
    assert message.status == "queued"
    assert message.dispatch_id == f"team-chat:{message.id}"
    assert message.metadata["external_origin"] == {
        "provider": "discord",
        "communication_target_id": "codex-discord",
        "source_message_id": "discord-message-1",
        "source_thread_id": None,
        "source_author_id": "discord-operator",
        "source_author_name": "Operator Alice",
    }
    assert message.metadata["allow_team_mutations"] is False
    assert len(replay_state.external_chat_ingress) == 1
    records = repo.list_outbox_records(intent_type="team.chat.message_queued")
    assert len(records) == 1
    assert records[0].payload["payload"]["dispatch_id"] == first["dispatch_id"]


def test_external_chat_reply_creates_delivery_event_and_records_failure(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    chat = _team_chat_service(repo)
    state, inbound = chat.enqueue_external_operator_chat_message(
        conversation_id=None,
        agent_id="backend",
        operator_user_id="operator-alice",
        operator_display_name="Operator Alice",
        body="Please reply through Discord.",
        provider="discord",
        communication_target_id="codex-discord",
        source_message_id="discord-message-2",
        source_thread_id=None,
        source_author_id="discord-operator",
        source_author_name="Operator Alice",
    )
    chat.begin_chat_message_dispatch(
        message_id=inbound["message_id"],
        dispatch_id=inbound["dispatch_id"],
    )

    state, reply = _command_service(repo).reply_to_chat_command(
        session_id="session-backend",
        conversation_id=inbound["conversation_id"],
        message_id=inbound["message_id"],
        body="The Team reply is persisted first.",
        request_id="external-chat-reply",
    )

    assert len(state.external_chat_deliveries) == 1
    delivery = next(iter(state.external_chat_deliveries.values()))
    assert delivery.agent_reply_message_id == reply["chat_message_id"]
    assert delivery.status == "pending"
    records = repo.list_outbox_records(intent_type="team.chat.external_reply_requested")
    assert len(records) == 1
    assert records[0].payload["payload"]["delivery_id"] == delivery.id

    state, recorded = chat.record_external_chat_delivery(
        delivery_id=delivery.id,
        success=False,
        error="Discord unavailable",
    )

    assert recorded.status == "failed"
    assert state.conversation_messages[reply["chat_message_id"]].body == (
        "The Team reply is persisted first."
    )
    assert state.conversation_messages[reply["chat_message_id"]].metadata[
        "external_delivery"
    ]["state"] == "failed"


def test_repository_send_operator_chat_message_command_uses_existing_conversation_and_replays(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-operator-chat")
    _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator-alice",
        operator_display_name="Operator Alice",
        expected_revision=repo.read().revision,
    )

    state, first = _command_service(repo).send_operator_chat_message_command(
        session_id="session-backend",
        recipient_operator_user_id="operator-alice",
        body="I need operator visibility on this.",
        summary="Visibility request",
        request_id="request-operator-chat-1",
    )
    state, replay = _command_service(repo).send_operator_chat_message_command(
        session_id="session-backend",
        recipient_operator_user_id="operator-alice",
        body="I need operator visibility on this.",
        summary="Visibility request",
        request_id="request-operator-chat-1",
    )
    _state, conflict = _command_service(repo).send_operator_chat_message_command(
        session_id="session-backend",
        recipient_operator_user_id="operator-alice",
        body="Different operator chat.",
        request_id="request-operator-chat-1",
    )

    assert replay == first
    assert conflict["success"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert first["conversation_created"] is False
    conversation = state.conversations[first["conversation_id"]]
    message = state.conversation_messages[first["chat_message_id"]]
    assert conversation.agent_id == "backend"
    assert conversation.operator_user_id == "operator-alice"
    assert message.metadata["recipient_operator_display_name"] == "Operator Alice"


def test_repository_send_operator_chat_message_rejects_unknown_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-operator-chat")

    state, result = _command_service(repo).send_operator_chat_message_command(
        session_id="session-backend",
        recipient_operator_user_id="unknown-operator",
        body="This should fail.",
        request_id="request-operator-chat-denied",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "operator_chat_not_member"
    assert state.conversation_messages == {}


def test_repository_send_operator_chat_message_rejects_other_agents_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-operator-chat")
    _team_chat_service(repo).create_or_get_conversation(
        agent_id="manager",
        operator_user_id="operator-alice",
        operator_display_name="Operator Alice",
        expected_revision=repo.read().revision,
    )

    state, result = _command_service(repo).send_operator_chat_message_command(
        session_id="session-backend",
        recipient_operator_user_id="operator-alice",
        body="This should fail.",
        request_id="request-operator-chat-other-agent",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "operator_chat_not_member"
    assert result["errors"][0]["correction_context"]["operator_user_ids"] == []
    assert state.conversation_messages == {}


def test_repository_escalate_operator_command_records_direct_operator_outbox(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")
    _lease_agent_dispatch(repo, agent_id="manager", dispatch_id="dispatch-escalation")

    state, first = _command_service(repo).escalate_operator_command(
        session_id="session-manager",
        title="Need a decision",
        message="Blocked waiting on operator input.",
        urgency="high",
        project_id="project",
        request_id="request-escalation-1",
    )
    state, replay = _command_service(repo).escalate_operator_command(
        session_id="session-manager",
        title="Need a decision",
        message="Blocked waiting on operator input.",
        urgency="high",
        project_id="project",
        request_id="request-escalation-1",
    )
    _state, conflict = _command_service(repo).escalate_operator_command(
        session_id="session-manager",
        title="Need a different decision",
        message="Blocked waiting on operator input.",
        urgency="high",
        project_id="project",
        request_id="request-escalation-1",
    )

    assert replay == first
    assert conflict["success"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert first["intent_recorded"] is True
    assert first["async_effect_pending"] is True
    assert first["downstream_effect_completed"] is False
    assert first["delivery_status"] == "pending"
    assert "queued, not delivered" in first["delivery_status_message"]
    assert "intent_recorded alone is not delivery confirmation" in (
        first["delivery_status_message"]
    )
    assert len(first["operator_event_ids"]) == 1
    assert len(first["outbox_record_ids"]) == 1
    outbox_records = repo.list_outbox_records()
    assert first["outbox_record_ids"][0] in {record.id for record in outbox_records}
    outbox = outbox_records[0]
    assert outbox.status == TeamOutboxStatus.PENDING
    assert outbox.intent_type == "team.operator.escalation_requested"
    assert outbox.payload["payload"]["communication_target_id"] == "operator:ops-channel"
    escalation_items = [
        item for item in state.inbox.values() if item.item_type == TeamInboxItemType.ESCALATION
    ]
    assert len(escalation_items) == 1


def test_repository_escalate_operator_command_rejects_non_manager(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-escalation-denied")

    state, result = _command_service(repo).escalate_operator_command(
        session_id="session-lead",
        title="Need a decision",
        message="Blocked waiting on operator input.",
        request_id="request-escalation-denied",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "not_top_level_team_manager"
    assert state.inbox == {}
    assert repo.list_outbox_records() == []


def test_repository_escalate_operator_command_creates_governance_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    def _enable_governance_escalation(state):
        state.teams["team"].escalate_to_governance = True
        return state

    repo.update(_enable_governance_escalation)
    _lease_agent_dispatch(
        repo,
        agent_id="manager",
        dispatch_id="dispatch-governance-escalation",
    )

    state, result = _command_service(repo).escalate_operator_command(
        session_id="session-manager",
        title="Governance decision needed",
        message="The project needs portfolio guidance.",
        request_id="request-governance-escalation",
    )

    assert result["success"] is True
    assert result["delivery_status"] == "pending"
    assert "queued, not delivered" in result["delivery_status_message"]
    assert len(result["governance_agenda_item_ids"]) == 1
    assert result.get("operator_event_ids", []) == []
    agenda = state.governance_agenda[result["governance_agenda_item_ids"][0]]
    assert agenda.item_type == GovernanceAgendaItemType.TEAM_ESCALATION
    assert agenda.team_id == "team"
    assert state.governance_runtimes["portfolio"].next_wake_at is None
    assert repo.list_outbox_records() == []


@pytest.mark.parametrize(
    "command",
    [
        lambda repo: _command_service(repo).record_team_note_command(
            session_id="session-backend",
            body="Note without a lease.",
            request_id="request-note-no-lease",
        ),
        lambda repo: _command_service(repo).request_status_command(
            session_id="session-backend",
            recipient_agent_id="worker",
            question="Status without a lease?",
            request_id="request-status-no-lease",
        ),
        lambda repo: _command_service(repo).ask_question_command(
            session_id="session-backend",
            question="Question without a lease?",
            request_id="request-question-no-lease",
        ),
        lambda repo: _command_service(repo).send_progress_report_command(
            session_id="session-backend",
            message="Progress without a lease.",
            request_id="request-progress-no-lease",
        ),
        lambda repo: _command_service(repo).reply_to_chat_command(
            session_id="session-backend",
            conversation_id="conversation-missing",
            message_id="chatmsg-missing",
            body="Reply without a lease.",
            request_id="request-chat-no-lease",
        ),
        lambda repo: _command_service(repo).send_operator_chat_message_command(
            session_id="session-backend",
            recipient_operator_user_id="operator-alice",
            body="Operator chat without a lease.",
            request_id="request-operator-chat-no-lease",
        ),
        lambda repo: _command_service(repo).escalate_operator_command(
            session_id="session-manager",
            title="Escalation without a lease",
            message="This should be denied before mutation.",
            request_id="request-escalation-no-lease",
        ),
    ],
)
def test_repository_slice5_create_message_commands_require_active_dispatch(
    tmp_path: Path,
    command,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, result = command(repo)

    assert result["success"] is False
    assert result["errors"][0]["code"] == "no_active_dispatch"
    assert state.inbox == {}
    assert state.action_records == {}
    assert state.conversation_messages == {}
    assert repo.list_outbox_records() == []


def test_submit_deliverable_command_publishes_artifacts_once_and_replays(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    _seed_git_work_context(repo)
    published: list[dict[str, object]] = []

    def publish(**kwargs: object) -> dict[str, object]:
        published.append(dict(kwargs))
        return {
            "artifact_ref": "artifact-space:/teams/team/projects/project/deliverables/deliverable-1",
            "artifact_key": "team/team/project/deliverable-1/dispatch-slice7/bundle",
            "source_paths": list(kwargs["source_paths"]),  # type: ignore[index]
        }

    state, first = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-1",
        task_id="task",
        summary="Backend work is ready.",
        artifact_paths=["reports/report.txt"],
        branch_context_id="ctx-task",
        git_refs={"agent_branch": "agents/session-backend/task", "commit_sha": "abc123"},
        verification={
            "status": "not_run",
            "summary": "pytest is unavailable in this VM.",
        },
        request_id="request-submit-deliverable",
        artifact_publisher=publish,
    )
    state, replay = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-1",
        task_id="task",
        summary="Backend work is ready.",
        artifact_paths=["reports/report.txt"],
        branch_context_id="ctx-task",
        git_refs={"agent_branch": "agents/session-backend/task", "commit_sha": "abc123"},
        verification={
            "status": "not_run",
            "summary": "pytest is unavailable in this VM.",
        },
        request_id="request-submit-deliverable",
        artifact_publisher=publish,
    )

    assert replay == first
    assert len(published) == 1
    assert published[0]["deliverable_id"] == "deliverable-1"
    assert published[0]["dispatch_id"] == "dispatch-slice7"
    assert state.deliverables["deliverable-1"].artifact_refs == [
        "artifact-space:/teams/team/projects/project/deliverables/deliverable-1"
    ]
    assert state.deliverables["deliverable-1"].git_refs.branch_context_id == "ctx-task"
    assert state.deliverables["deliverable-1"].git_refs.agent_branch == (
        "agents/session-backend/task"
    )
    assert state.deliverables["deliverable-1"].git_refs.commit_sha == "abc123"
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.SUBMITTED
    assert state.deliverables["deliverable-1"].verification == DeliverableVerification(
        status="not_run",
        summary="pytest is unavailable in this VM.",
    )
    assert repo.read().deliverables["deliverable-1"].verification == (
        state.deliverables["deliverable-1"].verification
    )
    assert first["verification"] == {
        "status": "not_run",
        "summary": "pytest is unavailable in this VM.",
        "evidence_refs": [],
    }
    assert any("provenance only" in hint for hint in first["next_action_hints"])
    assert any("not_run" in hint for hint in first["next_action_hints"])
    assert any("superior acceptance" in hint for hint in first["next_action_hints"])
    assert first["waiting_reason"] == "superior_acceptance"
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert state.agent_runtimes["backend"].active_dispatch_id == "dispatch-slice7"
    notices = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
    ]
    assert len(notices) == 1
    assert notices[0].recipient_agent_id == "lead"
    assert state.deliverables["deliverable-1"].acceptance_recipient_agent_id == "lead"
    assert notices[0].payload["kind"] == "superior_acceptance_requested"
    assert notices[0].payload["available_decisions"] == ["accepted", "rejected"]
    assert notices[0].payload["available_alternatives"] == [
        "request_review",
        "report_blocker",
    ]
    assert notices[0].payload["verification"] == {
        "status": "not_run",
        "summary": "pytest is unavailable in this VM.",
        "evidence_refs": [],
    }
    assert notices[0].priority == TeamInboxPriority.NORMAL
    wake_events = [
        event
        for event in first["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["agent_id"] == "lead"
    outbox_records = repo.list_outbox_records(
        intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE
    )
    assert [record.id for record in outbox_records] == first["outbox_record_ids"]


def test_superior_accepts_direct_deliverable_and_automatically_handles_notice(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _configure_release_policy(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            allow_ungated=True,
        )
        return state

    repo.update(_configure_release_policy)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-accept",
        task_id="task",
        summary="Ready for direct acceptance.",
        artifact_refs=["artifact-space:/teams/team/projects/project/deliverables/accept"],
        request_id="request-submit-accept",
    )
    notice_id = submitted["created_inbox_item_ids"][0]
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-accept")

    state, accepted = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-accept",
        decision="accepted",
        reason="Evidence and verification are sufficient.",
        request_id="request-accept-deliverable",
    )
    _state, replay = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-accept",
        decision="accepted",
        reason="Evidence and verification are sufficient.",
        request_id="request-accept-deliverable",
    )

    assert replay == accepted
    assert accepted["success"] is True
    assert accepted["decision"] == "accepted"
    assert accepted["task_accepted"] is True
    assert accepted["handled_inbox_item_ids"] == []
    assert accepted["automatically_handled_inbox_item_ids"] == [notice_id]
    assert state.inbox[notice_id].state == TeamInboxItemState.HANDLED
    assert state.deliverables["deliverable-accept"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED
    initial_release = state.deliverables[
        "deliverable-accept"
    ].initial_release_disposition
    assert initial_release is not None
    assert initial_release.state == InitialReleaseDispositionState.PENDING
    assert initial_release.decided_at is None
    decision = state.deliverables["deliverable-accept"].acceptance_decision
    assert decision is not None
    assert decision.decision == "accepted"
    assert decision.decided_by_agent_id == "lead"
    persisted = repo.read().deliverables["deliverable-accept"]
    assert persisted.acceptance_recipient_agent_id == "lead"
    assert persisted.acceptance_decision == decision
    assert persisted.initial_release_disposition == initial_release


def test_superior_decision_requires_pending_notice_in_current_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-handled-notice",
        task_id="task",
        summary="Ready after the earlier escalation was resolved.",
        artifact_refs=["artifact-space:/handled-notice"],
        request_id="request-submit-handled-notice",
    )
    notice_id = submitted["created_inbox_item_ids"][0]

    def _handle_notice(state):
        state.inbox[notice_id].state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_notice)
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-decide")

    state, accepted = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-handled-notice",
        decision="accepted",
        request_id="request-decide-handled-notice",
    )

    assert accepted["success"] is False
    assert accepted["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.deliverables["deliverable-handled-notice"].state == (
        TeamRecordState.PENDING
    )


def test_direct_acceptance_decision_survives_later_formal_review(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-late-review",
        task_id="task",
        summary="Ready for direct acceptance.",
        artifact_refs=["artifact-space:/late-review"],
        request_id="request-submit-late-review",
    )
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-accept")
    accepted_state, accepted = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-late-review",
        decision="accepted",
        reason="The direct handoff is acceptable.",
        request_id="request-accept-before-review",
    )
    historical_decision = accepted_state.deliverables[
        "deliverable-late-review"
    ].acceptance_decision

    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable-late-review",
        dispatch_id="dispatch-late-formal-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        id="review-after-direct-acceptance",
        deliverable_id="deliverable-late-review",
        reviewer_agent_id="worker",
        instructions="Perform the later formal review.",
        request_id="request-review-after-direct-acceptance",
    )

    assert accepted["success"] is True
    assert requested["success"] is True
    assert state.deliverables["deliverable-late-review"].state == (
        TeamRecordState.PENDING
    )
    assert state.deliverables["deliverable-late-review"].acceptance_decision == (
        historical_decision
    )
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_directly_rejected_deliverable_cannot_enter_formal_review(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-rejected-review",
        task_id="task",
        summary="Needs rework.",
        artifact_refs=["artifact-space:/rejected-review"],
        request_id="request-submit-rejected-review",
    )
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-reject")
    _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-rejected-review",
        decision="rejected",
        reason="The direct handoff is not acceptable.",
        request_id="request-reject-before-review",
    )

    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable-rejected-review",
        dispatch_id="dispatch-rejected-formal-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        id="review-after-direct-rejection",
        deliverable_id="deliverable-rejected-review",
        reviewer_agent_id="worker",
        instructions="Attempt to override the direct rejection.",
        request_id="request-review-after-direct-rejection",
    )

    assert requested["success"] is False
    assert "cannot enter formal review" in requested["errors"][0]["message"]
    assert "review-after-direct-rejection" not in state.review_requests
    deliverable = state.deliverables["deliverable-rejected-review"]
    assert deliverable.state == TeamRecordState.REJECTED
    assert deliverable.acceptance_decision is not None
    assert deliverable.acceptance_decision.decision == "rejected"


@pytest.mark.parametrize(
    ("persisted_deliverable_state", "review_state"),
    [
        (TeamRecordState.PENDING, TeamRecordState.PENDING),
        (TeamRecordState.ACCEPTED, TeamRecordState.ACCEPTED),
        (TeamRecordState.BLOCKED, TeamRecordState.BLOCKED),
        (TeamRecordState.REJECTED, TeamRecordState.PENDING),
    ],
)
def test_repository_migrates_persisted_direct_rejection_review_overrides(
    tmp_path: Path,
    persisted_deliverable_state: TeamRecordState,
    review_state: TeamRecordState,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable-rejection-override",
        task_id="task",
    )
    admin = _team_admin_service(repo)
    admin.record_review_request(
        review_request_id="review-after-direct-rejection",
        deliverable_id="deliverable-rejection-override",
        reviewer_agent_id="worker",
        expected_revision=repo.read().revision,
    )
    if review_state != TeamRecordState.PENDING:
        admin.record_review_decision(
            review_request_id="review-after-direct-rejection",
            state=review_state,
            findings=(
                ["The formal review environment is unavailable."]
                if review_state == TeamRecordState.BLOCKED
                else []
            ),
            expected_revision=repo.read().revision,
        )

    assignment_id = (
        f"assignment-{persisted_deliverable_state.value}-{review_state.value}"
    )

    def _seed_assignment_and_exhausted_owner(state):
        state.inbox[assignment_id] = AgentInboxItem(
            id=assignment_id,
            recipient_agent_id="worker",
            sender_agent_id="lead",
            task_id="task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            payload={
                "kind": "review_request",
                "review_request_id": "review-after-direct-rejection",
                "deliverable_id": "deliverable-rejection-override",
            },
        )
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            no_progress_count=3,
            next_wake_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
        return state

    repo.update(_seed_assignment_and_exhausted_owner)
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    direct_rejection = {
        "decision": "rejected",
        "decided_by_agent_id": "lead",
        "decided_at": "2026-08-01T12:00:00+00:00",
        "reason": "The direct handoff requires rework.",
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE team_deliverables
            SET state = ?,
                acceptance_recipient_agent_id = 'lead',
                acceptance_decision_json = ?
            WHERE id = 'deliverable-rejection-override'
            """,
            (persisted_deliverable_state.value, json.dumps(direct_rejection)),
        )
        connection.execute(
            """
            UPDATE team_state_metadata
            SET value = ?
            WHERE key = 'sqlite_schema_version'
            """,
            (str(SQLITE_SCHEMA_VERSION - 1),),
        )
        connection.execute(
            """
            UPDATE team_state_metadata
            SET value = ?
            WHERE key = 'state_schema_version'
            """,
            (str(SCHEMA_VERSION - 1),),
        )

    migrated = TeamStateRepository(tmp_path).read()

    deliverable = migrated.deliverables["deliverable-rejection-override"]
    assert deliverable.state == TeamRecordState.REJECTED
    assert deliverable.acceptance_decision is not None
    assert deliverable.acceptance_decision.decision == "rejected"
    review = migrated.review_requests["review-after-direct-rejection"]
    assert review.state == review_state
    assert review.retired_at is not None
    assert review.retired_by == f"system:sqlite-migration:v{SQLITE_SCHEMA_VERSION}"
    assert "authoritative direct rejection" in (review.retirement_reason or "")
    assert migrated.inbox[assignment_id].state == TeamInboxItemState.HANDLED
    assert migrated.tasks["task"].state == TeamTaskState.IN_PROGRESS
    owner_rework = [
        item
        for item in migrated.inbox.values()
        if item.state == TeamInboxItemState.PENDING
        and item.recipient_agent_id == "backend"
        and item.payload.get("kind") == "superior_acceptance_rejected"
        and item.payload.get("deliverable_id") == "deliverable-rejection-override"
    ]
    assert len(owner_rework) == 1
    assert owner_rework[0].payload["reason"] == "The direct handoff requires rework."
    assert migrated.agent_runtimes["backend"].no_progress_count == 0
    assert migrated.agent_runtimes["backend"].next_wake_at is None
    assert all(
        blocker.source_id != "review-after-direct-rejection"
        or blocker.state == TeamBlockerState.RESOLVED
        for blocker in migrated.blockers.values()
    )
    assert migrated.projects["project"].status == ProjectStatus.ACTIVE

    retirement_time = review.retired_at
    reopened = TeamStateRepository(tmp_path).read()
    assert reopened.review_requests[review.id].retired_at == retirement_time
    assert len(
        [
            item
            for item in reopened.inbox.values()
            if item.state == TeamInboxItemState.PENDING
            and item.payload.get("kind") == "superior_acceptance_rejected"
            and item.payload.get("deliverable_id")
            == "deliverable-rejection-override"
        ]
    ) == 1


def test_rejection_override_migration_preserves_release_history_and_blocks_reconciliation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable-published-override",
        task_id="task",
    )
    admin = _team_admin_service(repo)
    admin.record_review_request(
        review_request_id="review-published-override",
        deliverable_id="deliverable-published-override",
        reviewer_agent_id="worker",
        expected_revision=repo.read().revision,
    )
    admin.record_review_decision(
        review_request_id="review-published-override",
        state=TeamRecordState.ACCEPTED,
        expected_revision=repo.read().revision,
    )

    def _seed_release(state):
        state.release_packages["release-published-override"] = ReleasePackage(
            id="release-published-override",
            project_id="project",
            owner_agent_id="lead",
            deliverable_ids=["deliverable-published-override"],
            state=TeamRecordState.ACCEPTED,
        )
        return state

    repo.update(_seed_release)
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE team_deliverables
            SET acceptance_recipient_agent_id = 'lead',
                acceptance_decision_json = ?
            WHERE id = 'deliverable-published-override'
            """,
            (
                json.dumps(
                    {
                        "decision": "rejected",
                        "decided_by_agent_id": "lead",
                        "decided_at": "2026-08-01T12:00:00+00:00",
                        "reason": "The direct handoff requires rework.",
                    }
                ),
            ),
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = ? "
            "WHERE key = 'sqlite_schema_version'",
            (str(SQLITE_SCHEMA_VERSION - 1),),
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = ? "
            "WHERE key = 'state_schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )

    migrated = TeamStateRepository(tmp_path).read()

    assert migrated.release_packages["release-published-override"].state == (
        TeamRecordState.ACCEPTED
    )
    reconciliation_blocker = next(
        blocker
        for blocker in migrated.blockers.values()
        if blocker.source_id
        == "rejected-acceptance-release:release-published-override"
    )
    assert reconciliation_blocker.route == TeamBlockerRoute.OPERATOR
    assert reconciliation_blocker.state == TeamBlockerState.OPEN
    assert reconciliation_blocker.reason_code == (
        "rejected_acceptance_override_reconciliation"
    )
    assert migrated.projects["project"].status == ProjectStatus.BLOCKED


def test_direct_acceptance_decision_survives_promotion_failure(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-promotion",
        task_id="task",
        summary="Ready for direct acceptance and promotion.",
        artifact_refs=["artifact-space:/promotion"],
        request_id="request-submit-promotion",
    )
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-accept")
    accepted_state, accepted = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-promotion",
        decision="accepted",
        request_id="request-accept-before-promotion",
    )
    historical_decision = accepted_state.deliverables[
        "deliverable-promotion"
    ].acceptance_decision

    def _seed_promotion(state):
        state.git_promotions["promotion-direct-deliverable"] = GitPromotionRecord(
            id="promotion-direct-deliverable",
            project_id="project",
            task_id="task",
            actor_agent_id="backend",
            source_branch="agents/backend/task",
            target_branch="team/team/reviews/deliverable-promotion",
            promotion_kind="review",
            deliverable_id="deliverable-promotion",
        )
        return state

    repo.update(_seed_promotion)
    state, failed = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id="promotion-direct-deliverable",
        success=False,
        error="review branch promotion failed",
    )

    assert accepted["success"] is True
    assert failed["success"] is True
    assert state.deliverables["deliverable-promotion"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable-promotion"].acceptance_decision == (
        historical_decision
    )


def test_task_dispatch_allows_additional_products_after_task_starts_waiting(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_review_profile(repo)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _state, direct = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-direct",
        task_id="task",
        summary="Independent direct handoff.",
        artifact_refs=["artifact-space:/direct"],
        request_id="request-submit-direct",
    )
    state, formal = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-formal",
            "task_id": "task",
            "summary": "Independent formal-review handoff.",
            "artifact_refs": ["artifact-space:/formal"],
        },
        reviews=[
            {
                "id": "review-formal",
                "reviewer_agent_id": "worker",
                "review_profile_id": "profile",
            }
        ],
        request_id="request-submit-formal",
    )
    state, second_direct = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-direct-2",
        task_id="task",
        summary="A second independent direct handoff.",
        artifact_refs=["artifact-space:/direct-2"],
        request_id="request-submit-direct-2",
    )

    assert direct["success"] is True
    assert direct["waiting_reason"] == "superior_acceptance"
    assert formal["success"] is True
    assert second_direct["success"] is True
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert state.tasks["task"].deliverable_ids == [
        "deliverable-direct",
        "deliverable-formal",
        "deliverable-direct-2",
    ]
    assert "deliverable-formal" in state.deliverables
    assert "review-formal" in state.review_requests


def test_producer_reports_deliverable_blocker_from_original_task_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-blocked",
        task_id="task",
        summary="The product reached a blocked handoff.",
        artifact_refs=["artifact-space:/blocked"],
        request_id="request-submit-blocked",
    )

    state, reported = _command_service(repo).report_blocker_command(
        session_id="session-backend",
        source_kind=TeamBlockerSourceKind.DELIVERABLE,
        source_id="deliverable-blocked",
        summary="The submitted product cannot advance.",
        request_id="request-report-deliverable-blocker",
    )

    assert submitted["success"] is True
    assert reported["success"] is True
    assert reported["created_blocker_ids"]
    assert state.deliverables["deliverable-blocked"].state == TeamRecordState.BLOCKED


def test_superior_rejects_direct_deliverable_with_actionable_rework(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-reject",
        task_id="task",
        summary="Ready for inspection.",
        artifact_refs=["artifact-space:/teams/team/projects/project/deliverables/reject"],
        request_id="request-submit-reject",
    )
    notice_id = submitted["created_inbox_item_ids"][0]
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-reject")

    with pytest.raises(ValueError, match="reason is required"):
        _command_service(repo).decide_deliverable_command(
            session_id="session-lead",
            deliverable_id="deliverable-reject",
            decision="rejected",
            request_id="request-reject-without-reason",
        )

    state, rejected = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-reject",
        decision="rejected",
        reason="The migration evidence is missing.",
        request_id="request-reject-deliverable",
        handled_inbox_item_ids=[notice_id],
    )

    assert rejected["success"] is True
    assert rejected["created_inbox_item_ids"]
    assert len(rejected["created_inbox_item_ids"]) == 1
    assert rejected["events"][0]["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    assert rejected["events"][0]["payload"]["agent_id"] == "backend"
    assert rejected["outbox_record_ids"]
    assert state.inbox[notice_id].state == TeamInboxItemState.HANDLED
    assert state.deliverables["deliverable-reject"].state == TeamRecordState.REJECTED
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
    decision = state.deliverables["deliverable-reject"].acceptance_decision
    assert decision is not None
    assert decision.decision == "rejected"
    assert decision.reason == "The migration evidence is missing."
    rework = state.inbox[rejected["created_inbox_item_ids"][0]]
    assert rework.recipient_agent_id == "backend"
    assert rework.payload["kind"] == "superior_acceptance_rejected"
    assert rework.payload["supersedes_deliverable_id"] == "deliverable-reject"
    assert rework.payload["reason"] == "The migration evidence is missing."


def test_superior_can_decide_sibling_handoff_after_rejection(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    service = _command_service(repo)
    for deliverable_id in ("deliverable-first", "deliverable-second"):
        _state, submitted = service.submit_deliverable_command(
            session_id="session-backend",
            id=deliverable_id,
            task_id="task",
            summary=f"Independent product {deliverable_id}.",
            artifact_refs=[f"artifact-space:/{deliverable_id}"],
            request_id=f"request-submit-{deliverable_id}",
        )
        assert submitted["success"] is True

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-decide")
    state, rejected = service.decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-first",
        decision="rejected",
        reason="The first product needs rework.",
        request_id="request-reject-first",
    )

    assert rejected["success"] is True
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    state, accepted = service.decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-second",
        decision="accepted",
        request_id="request-accept-second",
    )

    assert accepted["success"] is True
    assert state.deliverables["deliverable-second"].state == TeamRecordState.ACCEPTED


def test_superior_decision_migrates_legacy_notice_recipient(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-legacy",
        task_id="task",
        summary="Submitted before the recipient field existed.",
        artifact_refs=["artifact-space:/legacy"],
        request_id="request-submit-legacy",
    )
    notice_id = submitted["created_inbox_item_ids"][0]

    def _make_legacy(state):
        state.deliverables["deliverable-legacy"].acceptance_recipient_agent_id = None
        notice = state.inbox[notice_id]
        notice.payload.pop("kind", None)
        notice.payload.pop("available_decisions", None)
        notice.payload.pop("available_alternatives", None)

    repo.update(_make_legacy)
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-accept-legacy")

    state, accepted = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-legacy",
        decision="accepted",
        request_id="request-accept-legacy",
        handled_inbox_item_ids=[notice_id],
    )

    assert accepted["success"] is True
    assert state.deliverables["deliverable-legacy"].acceptance_recipient_agent_id == "lead"
    assert state.deliverables["deliverable-legacy"].acceptance_decision is not None


def test_direct_deliverable_decision_rejects_wrong_superior_and_formal_review_path(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    _state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-review",
        task_id="task",
        summary="Ready for routing.",
        artifact_refs=["artifact-space:/teams/team/projects/project/deliverables/review"],
        request_id="request-submit-review",
    )
    notice_id = submitted["created_inbox_item_ids"][0]
    _lease_agent_dispatch(repo, agent_id="manager", dispatch_id="dispatch-manager")
    _state, forbidden = _command_service(repo).decide_deliverable_command(
        session_id="session-manager",
        deliverable_id="deliverable-review",
        decision="accepted",
        request_id="request-manager-accept",
    )
    assert forbidden["success"] is False
    assert forbidden["errors"][0]["code"] == "dispatch_scope_forbidden"

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-route-review")
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        id="review-direct-handoff",
        deliverable_id="deliverable-review",
        reviewer_agent_id="worker",
        instructions="Check the handoff evidence.",
        request_id="request-route-review",
        handled_inbox_item_ids=[notice_id],
    )
    assert requested["success"] is True
    assert state.inbox[notice_id].state == TeamInboxItemState.HANDLED

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-direct-after-review")
    unchanged, blocked = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable-review",
        decision="accepted",
        request_id="request-direct-after-review",
    )
    assert blocked["success"] is False
    assert blocked["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert unchanged.deliverables["deliverable-review"].state == TeamRecordState.PENDING
    assert unchanged.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_submit_deliverable_command_without_direct_superior_defers_review_routing(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="manager-task", owner_agent_id="manager")
    _lease_agent_dispatch(repo, agent_id="manager", dispatch_id="dispatch-manager")
    published: list[dict[str, object]] = []

    def publish(**kwargs: object) -> dict[str, object]:
        published.append(dict(kwargs))
        return {"artifact_ref": "artifact-space:/unexpected"}

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-manager",
        id="manager-deliverable",
        task_id="manager-task",
        summary="Top-level work is ready for workflow-owned review routing.",
        artifact_paths=["reports/manager.txt"],
        request_id="request-manager-deliverable",
        artifact_publisher=publish,
    )

    assert result["success"] is True
    assert result["created_inbox_item_ids"] == []
    assert len(published) == 1
    assert published[0]["agent"].id == "manager"  # type: ignore[union-attr]
    assert published[0]["team_id"] == "team"
    assert published[0]["project_id"] == "project"
    assert published[0]["deliverable_id"] == "manager-deliverable"
    assert published[0]["dispatch_id"] == "dispatch-manager"
    assert published[0]["artifact_paths"] == ["reports/manager.txt"]
    deliverable = state.deliverables["manager-deliverable"]
    assert deliverable.acceptance_recipient_agent_id is None
    assert deliverable.state == TeamRecordState.PENDING
    assert state.tasks["manager-task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert any(
        "team-specific review-routing guidance" in hint
        for hint in result["next_action_hints"]
    )


def test_admin_service_creates_git_work_context_for_task_owner(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    state, context_id = _team_admin_service(repo).create_git_work_context_with_id(
        context_id=None,
        agent_id="backend",
        task_id="task",
        dispatch_id="dispatch-slice7",
        worktree_path="/worktrees/backend-task",
        base_branch="main",
        base_commit="base123",
        agent_branch="agents/session-backend/task",
        commit_sha="abc123",
    )

    context = state.git_work_contexts[context_id]
    assert context.agent_id == "backend"
    assert context.task_id == "task"
    assert context.agent_branch == "agents/session-backend/task"
    assert repo.read().git_work_contexts[context_id].worktree_path == ("/worktrees/backend-task")


@pytest.mark.parametrize(
    "terminal_state",
    [
        TeamTaskState.FINISHED,
        TeamTaskState.REJECTED,
    ],
)
def test_admin_service_rejects_git_work_context_for_terminal_task(
    tmp_path: Path,
    terminal_state: TeamTaskState,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    state = _team_admin_service(repo).update_task(
        task_id="task",
        state=terminal_state,
    )

    with pytest.raises(ValueError, match="cannot have an active Git work context"):
        _team_admin_service(repo).create_git_work_context_with_id(
            context_id="ctx-terminal",
            agent_id="backend",
            task_id="task",
            dispatch_id="dispatch-terminal",
            worktree_path="/worktrees/backend-task",
            base_branch="main",
            agent_branch="agents/session-backend/task",
            expected_revision=state.revision,
        )

    assert repo.read().git_work_contexts == {}


@pytest.mark.parametrize(
    "terminal_state",
    [
        TeamTaskState.FINISHED,
    ],
)
def test_submit_deliverable_command_rejects_active_context_for_terminal_task(
    tmp_path: Path,
    terminal_state: TeamTaskState,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _team_admin_service(repo).update_task(
        task_id="task",
        state=terminal_state,
    )
    _seed_git_work_context(repo, dispatch_id="dispatch-terminal")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-terminal",
    )

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-terminal",
        task_id="task",
        summary="This late result must not reopen terminal work.",
        branch_context_id="ctx-task",
        request_id="request-submit-terminal",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["task"].state == terminal_state
    assert state.deliverables == {}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ACTIVE


def test_submit_deliverable_command_rejects_resolved_task_without_git_context(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-resolved-task",
    )
    _team_admin_service(repo).update_task(
        task_id="task",
        state=TeamTaskState.FINISHED,
    )

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-after-resolution",
        task_id="task",
        summary="A stale dispatch must not reopen resolved work.",
        artifact_refs=["artifact:late-result"],
        request_id="request-submit-after-resolution",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "task_already_resolved"
    assert state.tasks["task"].state == TeamTaskState.FINISHED
    assert state.deliverables == {}


def test_submit_deliverable_command_rejects_git_refs_without_context(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-branchless",
        task_id="task",
        summary="Backend work is ready.",
        git_refs={"agent_branch": "agents/session-backend/task"},
        request_id="request-submit-missing-context",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "branch_context_required"
    assert state.deliverables == {}
    assert repo.read().deliverables == {}


def test_submit_deliverable_for_review_rejects_unknown_git_ref_commit_field(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    _seed_git_work_context(repo, commit_sha=None)

    with pytest.raises(
        ValueError,
        match=(
            r"deliverable\.git_refs contains unsupported field\(s\): commit; "
            r"use commit_sha instead of commit"
        ),
    ):
        _command_service(repo).submit_deliverable_for_review_command(
            session_id="session-backend",
            deliverable={
                "id": "deliverable-wrong-commit-field",
                "task_id": "task",
                "summary": "Backend work is ready.",
                "branch_context_id": "ctx-task",
                "git_refs": {
                    "agent_branch": "agents/session-backend/task",
                    "commit": "candidate-commit",
                },
            },
            reviews=[{"reviewer_agent_id": "worker"}],
            request_id="request-submit-wrong-commit-field",
        )

    state = repo.read()
    assert state.deliverables == {}
    assert state.review_requests == {}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ACTIVE


def test_submit_deliverable_for_review_requires_pinned_git_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    _seed_git_work_context(repo, commit_sha=None)

    state, result = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-unpinned",
            "task_id": "task",
            "summary": "Backend work is ready.",
            "branch_context_id": "ctx-task",
            "git_refs": {"agent_branch": "agents/session-backend/task"},
        },
        reviews=[{"reviewer_agent_id": "worker"}],
        request_id="request-submit-unpinned",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "git_candidate_commit_required"
    assert result["correction_context"]["required_field"] == "git_refs.commit_sha"
    assert state.deliverables == {}
    assert state.review_requests == {}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ACTIVE


def test_submit_deliverable_for_review_pins_candidate_on_deliverable_and_review(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    _seed_git_work_context(repo, commit_sha=None)
    authoritative_commit = "127450960be30a2b7c7966883a09252b70769748"

    state, result = TeamDeliverableSubmissionCommandService(
        repo,
        git_candidate_commit_resolver=lambda **_kwargs: authoritative_commit,
    ).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-pinned",
            "task_id": "task",
            "summary": "Backend work is ready.",
            "branch_context_id": "ctx-task",
            "git_refs": {
                "agent_branch": "agents/session-backend/task",
                "commit_sha": authoritative_commit,
            },
        },
        reviews=[{"id": "review-pinned", "reviewer_agent_id": "worker"}],
        request_id="request-submit-pinned",
    )

    assert result["success"] is True
    refs = state.deliverables["deliverable-pinned"].git_refs
    assert refs.commit_sha == authoritative_commit
    assert refs.submitted_commit_sha == authoritative_commit
    assert state.review_requests["review-pinned"].reviewed_commit_sha == authoritative_commit
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.SUBMITTED


def test_submit_deliverable_for_review_rejects_agent_sha_that_is_not_branch_tip(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    _seed_git_work_context(repo, commit_sha=None)
    supplied_commit = "127450960b913c94b03b20b00b67de93287ac311"
    authoritative_commit = "127450960be30a2b7c7966883a09252b70769748"
    resolver_calls: list[dict[str, str]] = []

    def _resolve(**kwargs: str) -> str:
        resolver_calls.append(kwargs)
        return authoritative_commit

    state, result = TeamDeliverableSubmissionCommandService(
        repo,
        git_candidate_commit_resolver=_resolve,
    ).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-hallucinated-sha",
            "task_id": "task",
            "summary": "Backend work is ready.",
            "branch_context_id": "ctx-task",
            "git_refs": {
                "agent_branch": "agents/session-backend/task",
                "commit_sha": supplied_commit,
            },
        },
        reviews=[{"id": "review-pinned", "reviewer_agent_id": "worker"}],
        request_id="request-submit-hallucinated-sha",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "commit_sha_mismatch"
    assert result["correction_context"]["commit_sha"] == supplied_commit
    assert result["correction_context"]["expected_commit_sha"] == authoritative_commit
    assert resolver_calls == [
        {
            "managed_repo_id": MANAGED_REPO_ID,
            "directory_path": "root",
            "agent_branch": "agents/session-backend/task",
        }
    ]
    assert state.deliverables == {}
    assert state.review_requests == {}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ACTIVE


def test_submit_deliverable_command_rejects_mismatched_git_branch_context(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    _seed_git_work_context(repo, agent_branch="agents/session-backend/task")

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-wrong-branch",
        task_id="task",
        summary="Backend work is ready.",
        branch_context_id="ctx-task",
        git_refs={"agent_branch": "agents/session-backend/other-task"},
        request_id="request-submit-mismatched-context",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "agent_branch_mismatch"
    assert state.deliverables == {}
    assert repo.read().deliverables == {}


def test_request_review_rejects_legacy_git_deliverable_without_pinned_commit(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    def _attach_unpinned_git_refs(state):
        state.deliverables["deliverable"].git_refs = DeliverableGitRefs(
            branch_context_id="legacy-context",
            agent_branch="agents/session-backend/task",
        )
        return state

    repo.update(_attach_unpinned_git_refs)
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-review",
    )

    state, result = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Review the exact Git candidate.",
        request_id="request-review-unpinned-legacy",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "review_request_failed"
    assert "does not have a pinned submitted commit" in result["errors"][0]["message"]
    assert state.review_requests == {}


def test_submit_deliverable_command_allows_artifact_only_without_git_context(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-artifact",
        task_id="task",
        summary="Operations report is ready.",
        artifact_refs=["artifact-space:/teams/team/reports/report-1"],
        request_id="request-submit-artifact-only",
    )

    assert result["success"] is True
    deliverable = state.deliverables["deliverable-artifact"]
    assert deliverable.git_refs.agent_branch is None
    assert deliverable.git_refs.branch_context_id is None
    assert deliverable.artifact_refs == ["artifact-space:/teams/team/reports/report-1"]


def test_submit_deliverable_command_server_binds_advanced_task_branch_when_refs_omitted(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    base_commit = "1" * 40
    submitted_commit = "2" * 40
    _seed_git_work_context(repo, commit_sha=None)

    def _set_base(state):
        state.git_work_contexts["ctx-task"].base_commit = base_commit
        return state

    repo.update(_set_base)
    resolver_calls: list[dict[str, str]] = []

    def _resolve(**kwargs: str) -> str:
        resolver_calls.append(kwargs)
        return submitted_commit

    state, result = TeamDeliverableSubmissionCommandService(
        repo,
        git_candidate_commit_resolver=_resolve,
    ).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-inferred-git",
        task_id="task",
        summary="The implementation and report are ready.",
        artifact_refs=["artifact-space:/teams/team/reports/report-1"],
        request_id="request-submit-inferred-git",
    )

    assert result["success"] is True
    refs = state.deliverables["deliverable-inferred-git"].git_refs
    assert refs.branch_context_id == "ctx-task"
    assert refs.agent_branch == "agents/session-backend/task"
    assert refs.commit_sha == submitted_commit
    assert refs.submitted_commit_sha == submitted_commit
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.SUBMITTED
    assert resolver_calls == [
        {
            "managed_repo_id": MANAGED_REPO_ID,
            "directory_path": "root",
            "agent_branch": "agents/session-backend/task",
        }
    ]


def test_submit_deliverable_command_keeps_artifact_only_when_task_branch_is_unchanged(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    base_commit = "1" * 40
    _seed_git_work_context(repo, commit_sha=None)

    def _set_base(state):
        state.git_work_contexts["ctx-task"].base_commit = base_commit
        return state

    repo.update(_set_base)
    state, result = TeamDeliverableSubmissionCommandService(
        repo,
        git_candidate_commit_resolver=lambda **_kwargs: base_commit,
    ).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-artifact-with-unchanged-worktree",
        task_id="task",
        summary="The external operations report is ready.",
        artifact_refs=["artifact-space:/teams/team/reports/report-1"],
        request_id="request-submit-unchanged-artifact",
    )

    assert result["success"] is True
    refs = state.deliverables["deliverable-artifact-with-unchanged-worktree"].git_refs
    assert refs.branch_context_id is None
    assert refs.agent_branch is None
    assert refs.commit_sha is None
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ACTIVE


def test_submit_deliverable_command_rejects_gitless_bypass_when_branch_resolution_unavailable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    _seed_git_work_context(repo, commit_sha=None)

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-unverifiable-gitless",
        task_id="task",
        summary="Do not let an advanced task branch bypass Git provenance.",
        artifact_refs=["artifact-space:/teams/team/reports/report-1"],
        request_id="request-submit-unverifiable-gitless",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "git_candidate_commit_resolution_unavailable"
    assert result["correction_context"]["required_action"] == (
        "retry_submission_when_git_resolution_is_available"
    )
    assert state.deliverables == {}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ACTIVE


def test_submit_deliverable_command_publish_failure_does_not_mutate_state(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")
    before = repo.read()

    def fail_publish(**_: object) -> dict[str, object]:
        raise RuntimeError("artifact file not found: reports/missing.txt")

    state, result = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable-1",
        task_id="task",
        summary="Backend work is ready.",
        artifact_paths=["reports/missing.txt"],
        request_id="request-submit-fail",
        artifact_publisher=fail_publish,
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "artifact_publication_failed"
    assert state.revision == before.revision
    assert state.deliverables == {}
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.inbox == {}
    assert (
        repo.list_dispatch_activity(
            dispatch_id="dispatch-slice7",
            request_id="request-submit-fail",
        )[0].status
        == TeamDispatchActivityStatus.FAILURE
    )


def test_submit_deliverable_for_review_suppresses_superior_notice(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_review_profile(repo)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")

    state, result = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-1",
            "task_id": "task",
            "summary": "Backend work is ready.",
            "artifact_refs": [
                "artifact-space:/teams/team/projects/project/deliverables/deliverable-1"
            ],
            "verification": {
                "status": "failed",
                "summary": "Unit suite failed before handoff.",
            },
        },
        reviews=[
            {
                "id": "review-1",
                "reviewer_agent_id": "worker",
                "review_profile_id": "profile",
                "instructions": "Use the checklist.",
            }
        ],
        request_id="request-submit-review",
    )

    assert result["success"] is True
    assert result["deliverable_id"] == "deliverable-1"
    assert result["created_review_request_ids"] == ["review-1"]
    assert result["waiting_reason"] == "review"
    assert state.deliverables["deliverable-1"].acceptance_recipient_agent_id is None
    assert [
        item.id
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
    ] == []
    review_items = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == "worker" and item.payload.get("kind") == "review_request"
    ]
    assert len(review_items) == 1
    assert review_items[0].payload["review_profile"]["id"] == "profile"
    assert review_items[0].payload["verification"] == {
        "status": "failed",
        "summary": "Unit suite failed before handoff.",
        "evidence_refs": [],
    }
    assert state.agent_runtimes["worker"].next_wake_at is None


def test_decide_review_command_failure_returns_current_correction_state(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-slice7")

    state, result = _command_service(repo).decide_review_command(
        session_id="session-backend",
        review_request_id="review-1",
        state="accepted",
        request_id="request-review-wrong-agent",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    context = result["correction_context"]
    assert context["scope_kind"] == "review"
    assert context["requested_ids"] == ["review-1"]
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING


def test_repository_finalize_agent_dispatch_closes_after_progress_activity(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_backend(repo, dispatch_id="dispatch-progress", no_progress_count=4)
    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-progress",
            tool_name="team_update_task",
            caller_session_id="session-backend",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id="dispatch-progress",
            request_id="request-progress",
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="progress",
        )
    )

    assert repo.read().agent_runtimes["backend"].active_dispatch_id == "dispatch-progress"

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="dispatch-progress",
    )

    runtime = state.agent_runtimes["backend"]
    assert result["success"] is True
    assert result["finalized"] is True
    assert result["progress_made"] is True
    assert result["activity_ids"] == ["activity-progress"]
    assert runtime.active_dispatch_id is None
    assert runtime.wake_leased_until is None
    assert runtime.no_progress_count == 0
    assert runtime.last_progress_at is not None


def test_repository_finalize_agent_dispatch_without_activity_counts_no_progress(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_backend(repo, dispatch_id="dispatch-empty", no_progress_count=2)
    _seed_git_work_context(repo, dispatch_id="dispatch-empty")

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="dispatch-empty",
    )

    runtime = state.agent_runtimes["backend"]
    assert result["success"] is True
    assert result["progress_made"] is False
    assert result["activity_count"] == 0
    assert runtime.active_dispatch_id is None
    assert runtime.no_progress_count == 3
    assert runtime.last_progress_at is None
    assert state.action_records == {}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ABANDONED


def test_legacy_agent_action_finalizes_every_context_for_ended_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task-a")
    _seed_task(repo, task_id="task-b")
    _lease_backend(repo, dispatch_id="dispatch-batch")
    _seed_git_work_context(
        repo,
        context_id="ctx-task-a",
        task_id="task-a",
        dispatch_id="dispatch-batch",
    )
    _seed_git_work_context(
        repo,
        context_id="ctx-task-b",
        task_id="task-b",
        dispatch_id="dispatch-batch",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": "dispatch-batch",
            "task_updates": [
                {
                    "task_id": "task-a",
                    "state": "finished",
                }
            ],
        },
        dispatch_id="dispatch-batch",
        expected_revision=repo.read().revision,
    )

    assert result["success"] is True
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.git_work_contexts["ctx-task-a"].state == GitWorkContextState.ABANDONED
    assert state.git_work_contexts["ctx-task-b"].state == GitWorkContextState.ABANDONED


def test_legacy_agent_action_mismatch_finalizes_active_dispatch_contexts(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task-a")
    _seed_task(repo, task_id="task-b")
    _lease_backend(repo, dispatch_id="dispatch-batch")
    _seed_git_work_context(
        repo,
        context_id="ctx-task-a",
        task_id="task-a",
        dispatch_id="dispatch-batch",
    )
    _seed_git_work_context(
        repo,
        context_id="ctx-task-b",
        task_id="task-b",
        dispatch_id="dispatch-batch",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": "dispatch-wrong",
        },
        dispatch_id="dispatch-wrong",
        expected_revision=repo.read().revision,
    )

    assert result["success"] is False
    assert result["errors"] == ["dispatch_id does not match active team runtime lease"]
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.git_work_contexts["ctx-task-a"].state == GitWorkContextState.ABANDONED
    assert state.git_work_contexts["ctx-task-b"].state == GitWorkContextState.ABANDONED


def test_repository_reconciles_and_prunes_git_work_context_metadata(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _lease_backend(repo, dispatch_id="dispatch-current")
    now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
    old = now - timedelta(days=31)
    _seed_git_work_context(
        repo,
        context_id="ctx-referenced",
        dispatch_id="dispatch-stale",
        updated_at=old,
    )
    _seed_git_work_context(
        repo,
        context_id="ctx-stale",
        dispatch_id="dispatch-stale",
        updated_at=old,
    )
    _seed_git_work_context(
        repo,
        context_id="ctx-current",
        dispatch_id="dispatch-current",
        updated_at=old,
    )
    _seed_git_work_context(
        repo,
        context_id="ctx-old-abandoned",
        dispatch_id="dispatch-old",
        context_state=GitWorkContextState.ABANDONED,
        updated_at=old,
    )

    def _attach_context_to_deliverable(state):
        state.deliverables["deliverable"].git_refs = DeliverableGitRefs(
            branch_context_id="ctx-referenced",
            agent_branch="agents/session-backend/task",
            commit_sha="abc123",
        )
        return state

    repo.update(_attach_context_to_deliverable)

    state, result = _team_runtime_service(repo).reconcile_git_work_contexts(
        now=now,
        prune_before=now - timedelta(days=30),
    )

    assert result == {"submitted": 1, "abandoned": 1, "pruned": 0}
    assert state.git_work_contexts["ctx-referenced"].state == (
        GitWorkContextState.SUBMITTED
    )
    assert state.git_work_contexts["ctx-stale"].state == GitWorkContextState.ABANDONED
    assert state.git_work_contexts["ctx-current"].state == GitWorkContextState.ACTIVE
    assert "ctx-old-abandoned" in state.git_work_contexts

    state, result = _team_runtime_service(repo).reconcile_git_work_contexts(
        now=now + timedelta(days=31),
        prune_before=now + timedelta(seconds=1),
    )

    assert result == {"submitted": 0, "abandoned": 0, "pruned": 0}
    assert set(state.git_work_contexts) == {
        "ctx-referenced",
        "ctx-stale",
        "ctx-current",
        "ctx-old-abandoned",
    }


@pytest.mark.parametrize(
    "wake_leased_until",
    [
        None,
        datetime(2026, 7, 24, 7, 59, tzinfo=UTC),
    ],
    ids=["missing-lease", "expired-lease"],
)
def test_repository_reconcile_abandons_matching_dispatch_without_live_lease(
    tmp_path: Path,
    wake_leased_until: datetime | None,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)

    def _set_stale_runtime(state):
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            active_dispatch_id="dispatch-stale",
            wake_leased_until=wake_leased_until,
        )
        return state

    repo.update(_set_stale_runtime)
    _seed_git_work_context(repo, dispatch_id="dispatch-stale")

    state, result = _team_runtime_service(repo).reconcile_git_work_contexts(now=now)

    assert result == {"submitted": 0, "abandoned": 1, "pruned": 0}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ABANDONED


def test_blocker_owner_no_progress_transfers_despite_unrelated_progress(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="worker",
        reporter_session_id="session-worker",
        source_id="dependency-a",
    )
    _state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="backend")
    assert len(leases) == 1
    dispatch_id = leases[0]["dispatch_id"]
    assert [row["id"] for row in leases[0]["blockers"]] == [blocker_id]
    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-unrelated-progress",
            tool_name="team_update_task",
            caller_session_id="session-backend",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id=dispatch_id,
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="progress",
        )
    )

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=dispatch_id,
    )

    blocker = state.blockers[blocker_id]
    assert result["progress_made"] is True
    assert blocker.owner_agent_id == "lead"
    assert blocker.route == TeamBlockerRoute.OWNED
    assert blocker.escalation_depth == 1
    assert any(
        event.blocker_id == blocker_id
        and event.event_type == TeamBlockerEventType.OWNER_TRANSFERRED
        and event.from_agent_id == "backend"
        and event.to_agent_id == "lead"
        for event in state.blocker_events.values()
    )


def test_actionable_question_exact_blocker_reference_scopes_only_owned_blocker(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime.now(UTC)
    owned_blocker_id = "blocker-owned-by-backend"
    other_blocker_id = "blocker-owned-by-lead"
    question_id = "inbox-blocker-question"

    def _seed_question(state):
        state.blockers[owned_blocker_id] = TeamBlocker(
            id=owned_blocker_id,
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id="backend-decision",
            reporter_agent_id="worker",
            owner_agent_id="backend",
            summary="Backend decision needed",
            follow_up_at=now + timedelta(hours=1),
        )
        state.blockers[other_blocker_id] = TeamBlocker(
            id=other_blocker_id,
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id="lead-decision",
            reporter_agent_id="worker",
            owner_agent_id="lead",
            summary="Lead decision needed",
            follow_up_at=now + timedelta(hours=1),
        )
        state.inbox[question_id] = AgentInboxItem(
            id=question_id,
            recipient_agent_id="backend",
            sender_agent_id="worker",
            item_type=TeamInboxItemType.QUESTION,
            response_requested=True,
            priority=TeamInboxPriority.HIGH,
            payload={
                "question": (
                    f"Please resolve `{owned_blocker_id}`. Do not touch "
                    f"`{other_blocker_id}`."
                ),
            },
            created_at=now,
            updated_at=now,
        )
        return state

    repo.update(_seed_question)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=now + timedelta(seconds=1),
    )

    assert len(leases) == 1
    dispatch_id = leases[0]["dispatch_id"]
    assert state.dispatches[dispatch_id].blocker_ids == [owned_blocker_id]
    assert [row["id"] for row in leases[0]["blockers"]] == [owned_blocker_id]
    assert state.blockers[owned_blocker_id].active_dispatch_id == dispatch_id
    assert state.blockers[other_blocker_id].active_dispatch_id is None

    _state, forbidden = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=other_blocker_id,
        action="resolve",
        note="This blocker merely appeared in the same question.",
        request_id="resolve-other-question-blocker",
    )
    assert forbidden["success"] is False
    assert forbidden["errors"][0]["code"] == "dispatch_scope_forbidden"

    state, resolved = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=owned_blocker_id,
        action="resolve",
        note="Resolved the explicitly requested backend decision.",
        request_id="resolve-owned-question-blocker",
        handled_inbox_item_ids=[question_id],
    )
    assert resolved["success"] is True
    assert state.blockers[owned_blocker_id].state == TeamBlockerState.RESOLVED


def test_manager_fallback_scopes_future_owned_blocker_without_foreign_access(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="team-work", owner_agent_id="backend")
    now = datetime.now(UTC)
    owned_blocker_id = "blocker-manager-future-follow-up"
    foreign_blocker_id = "blocker-lead-future-follow-up"

    def _seed_future_blockers(state: TeamState) -> TeamState:
        state.blockers[owned_blocker_id] = TeamBlocker(
            id=owned_blocker_id,
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id="manager-deferred-decision",
            reporter_agent_id="backend",
            owner_agent_id="manager",
            summary="Manager can resolve this before its scheduled follow-up.",
            follow_up_at=now + timedelta(days=1),
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        state.blockers[foreign_blocker_id] = TeamBlocker(
            id=foreign_blocker_id,
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id="lead-deferred-decision",
            reporter_agent_id="backend",
            owner_agent_id="lead",
            summary="This blocker remains outside the manager dispatch.",
            follow_up_at=now + timedelta(days=1),
            created_at=now - timedelta(minutes=30),
            updated_at=now - timedelta(minutes=30),
        )
        return state

    repo.update(_seed_future_blockers)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        now=now,
        session_is_busy=lambda session_id: session_id != "session-manager",
    )

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]
    lease = leases[0]
    dispatch_id = lease["dispatch_id"]
    assert lease["reason"] == "manager_fallback"
    assert state.dispatches[dispatch_id].blocker_ids == [owned_blocker_id]
    assert [blocker["id"] for blocker in lease["blockers"]] == [owned_blocker_id]
    assert state.blockers[owned_blocker_id].active_dispatch_id is None
    assert state.blockers[foreign_blocker_id].active_dispatch_id is None

    _state, forbidden = _command_service(repo).update_blocker_command(
        session_id="session-manager",
        blocker_id=foreign_blocker_id,
        action="resolve",
        note="A manager fallback must not confer foreign-blocker access.",
        request_id="resolve-foreign-future-blocker",
    )
    assert forbidden["success"] is False
    assert forbidden["errors"][0]["code"] == "dispatch_scope_forbidden"

    state, resolved = _command_service(repo).update_blocker_command(
        session_id="session-manager",
        blocker_id=owned_blocker_id,
        action="resolve",
        note="The deferred decision is now resolved.",
        request_id="resolve-owned-future-blocker",
    )
    assert resolved["success"] is True
    assert state.blockers[owned_blocker_id].state == TeamBlockerState.RESOLVED
    assert state.blockers[foreign_blocker_id].state == TeamBlockerState.OPEN


def test_assigned_blocker_dispatch_scopes_only_owned_source_task_for_recovery(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="owned-source-task", owner_agent_id="backend")
    _seed_task(repo, task_id="foreign-source-task", owner_agent_id="worker")
    now = datetime.now(UTC)

    def _seed_blocked_sources(state):
        state.tasks["owned-source-task"].state = TeamTaskState.BLOCKED
        state.tasks["foreign-source-task"].state = TeamTaskState.BLOCKED
        for task_id in ("owned-source-task", "foreign-source-task"):
            blocker_id = f"blocker-{task_id}"
            state.blockers[blocker_id] = TeamBlocker(
                id=blocker_id,
                team_id="team",
                project_id="project",
                source_kind=TeamBlockerSourceKind.TASK,
                source_id=task_id,
                owner_agent_id="lead",
                delegate_agent_id="backend",
                route=TeamBlockerRoute.DELEGATED,
                summary=f"Recover {task_id}",
                created_at=now,
                updated_at=now,
            )
        return state

    repo.update(_seed_blocked_sources)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=now + timedelta(seconds=1),
    )

    assert len(leases) == 1
    dispatch = state.dispatches[leases[0]["dispatch_id"]]
    assert dispatch.blocker_ids == [
        "blocker-foreign-source-task",
        "blocker-owned-source-task",
    ]
    assert dispatch.task_ids == ["owned-source-task"]

    _state, foreign = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="foreign-source-task",
        summary="Must not cross the task ownership boundary.",
        request_id="foreign-blocker-recovery",
    )
    assert foreign["success"] is False
    assert foreign["errors"][0]["code"] == "dispatch_scope_forbidden"

    _state, owned = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="owned-source-task",
        summary="Corrective product for the owned task.",
        request_id="owned-blocker-recovery",
    )
    assert owned["success"] is False
    assert owned["errors"][0]["code"] == "task_boundary_blocked"


def test_blocker_specific_progress_keeps_owner_and_sets_follow_up(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="worker",
        reporter_session_id="session-worker",
        source_id="dependency-b",
    )
    _state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="backend")
    dispatch_id = leases[0]["dispatch_id"]

    _state, update_result = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="progress",
        note="Requested the missing decision.",
        follow_up_seconds=300,
        request_id="request-blocker-progress",
    )
    assert update_result["success"] is True
    state, _result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=dispatch_id,
    )

    blocker = state.blockers[blocker_id]
    assert blocker.owner_agent_id == "backend"
    assert blocker.route == TeamBlockerRoute.OWNED
    assert blocker.escalation_depth == 0
    assert blocker.follow_up_at is not None
    assert not any(
        event.blocker_id == blocker_id
        and event.event_type == TeamBlockerEventType.OWNER_TRANSFERRED
        for event in state.blocker_events.values()
    )


@pytest.mark.parametrize(
    ("resolver_agent_id", "resolver_session_id"),
    [
        ("worker", "session-worker"),
        ("backend", "session-backend"),
        ("lead", "session-lead"),
        ("manager", "session-manager"),
    ],
)
def test_manual_blocker_resolve_requires_current_dispatch_assignment(
    tmp_path: Path,
    resolver_agent_id: str,
    resolver_session_id: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="worker",
        reporter_session_id="session-worker",
        source_id=f"resolve-by-{resolver_agent_id}",
    )
    _lease_agent_dispatch(
        repo,
        agent_id=resolver_agent_id,
        dispatch_id=f"dispatch-resolve-{resolver_agent_id}",
    )

    state, result = _command_service(repo).update_blocker_command(
        session_id=resolver_session_id,
        blocker_id=blocker_id,
        action="resolve",
        note=f"{resolver_agent_id} verified the dependency is available.",
        resolution_refs=[f"evidence-{resolver_agent_id}"],
        request_id=f"request-resolve-{resolver_agent_id}",
    )

    blocker = state.blockers[blocker_id]
    if resolver_agent_id == "backend":
        assert result["success"] is True
        assert blocker.state == TeamBlockerState.RESOLVED
        assert blocker.route == TeamBlockerRoute.RESOLVED
        assert any(
            event.blocker_id == blocker_id
            and event.event_type == TeamBlockerEventType.RESOLVED
            and event.actor_agent_id == resolver_agent_id
            for event in state.blocker_events.values()
        )
    else:
        assert result["success"] is False
        assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
        assert blocker.state == TeamBlockerState.OPEN


def test_resolved_blocker_is_stale_for_later_command_in_same_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="worker",
        reporter_session_id="session-worker",
        source_id="idempotent-resolve",
    )
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-resolve-replay")
    state, first = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="resolve",
        note="The dependency is available.",
        resolution_refs=["evidence-1"],
        request_id="request-resolve-first",
    )
    first_revision = state.revision

    replayed_state, replayed = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="resolve",
        note="The dependency is available.",
        resolution_refs=["evidence-1"],
        request_id="request-resolve-replayed",
    )

    assert first["success"] is True
    assert replayed["success"] is False
    assert replayed["applied"] is False
    assert replayed["errors"][0]["code"] == "dispatch_scope_stale"
    assert replayed_state.revision == first_revision
    assert replayed_state.blockers[blocker_id].terminal_reference_ids == ["evidence-1"]


def test_manual_blocker_resolve_rejects_peer_even_when_delegated(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="peer",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-peer",
        reports_to_agent_id="backend",
        display_name="Peer Agent",
        expected_revision=repo.read().revision,
    )
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="worker",
        reporter_session_id="session-worker",
        source_id="peer-resolution-denied",
    )
    _state, owner_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend"
    )
    owner_dispatch_id = owner_leases[0]["dispatch_id"]
    _state, delegated = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="delegate",
        delegate_agent_id="peer",
        note="Check whether the dependency is available.",
        request_id="request-delegate-to-peer",
    )
    assert delegated["success"] is True
    _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=owner_dispatch_id,
    )
    _state, peer_leases = _team_runtime_service(repo).lease_due_agents(agent_id="peer")
    assert len(peer_leases) == 1

    state, result = _command_service(repo).update_blocker_command(
        session_id="session-peer",
        blocker_id=blocker_id,
        action="resolve",
        note="Peer believes the dependency is available.",
        request_id="request-peer-resolve",
    )

    blocker = state.blockers[blocker_id]
    assert result["success"] is False
    assert result["errors"][0]["code"] == "blocker_resolution_line_required"
    assert blocker.state == TeamBlockerState.OPEN
    assert blocker.route == TeamBlockerRoute.DELEGATED
    assert blocker.owner_agent_id == "backend"
    assert blocker.delegate_agent_id == "peer"


def test_automated_blocker_resolution_line_stays_anchored_to_first_owner(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _open_automated_blocker(state):
        open_or_get_blocker(
            state,
            source_kind="explicit",
            source_id="automated-dependency",
            summary="Automated dependency failed",
            details="The source transition could not continue.",
            initial_owner_agent_id="backend",
            team_id="team",
            project_id="project",
        )

    state = repo.update(_open_automated_blocker)
    blocker = next(
        blocker
        for blocker in state.blockers.values()
        if blocker.source_id == "automated-dependency"
    )
    _state, owner_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend"
    )
    state, _result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=owner_leases[0]["dispatch_id"],
    )
    assert state.blockers[blocker.id].owner_agent_id == "lead"

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-resolve-automated-blocker",
    )
    state, resolved = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker.id,
        action="resolve",
        note="The first owner verified the automated dependency recovered.",
        request_id="request-resolve-automated-blocker",
    )

    assert resolved["success"] is False
    assert resolved["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.blockers[blocker.id].state == TeamBlockerState.OPEN


def test_delegation_stall_returns_to_owner_before_next_level_transfer(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="worker",
        reporter_session_id="session-worker",
        source_id="dependency-c",
    )
    _state, owner_leases = _team_runtime_service(repo).lease_due_agents(agent_id="backend")
    owner_dispatch_id = owner_leases[0]["dispatch_id"]
    _state, delegate_result = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker_id,
        action="delegate",
        delegate_agent_id="worker",
        note="Check the dependency locally.",
        request_id="request-blocker-delegate",
    )
    assert delegate_result["success"] is True
    _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=owner_dispatch_id,
    )

    _state, delegate_leases = _team_runtime_service(repo).lease_due_agents(agent_id="worker")
    delegate_dispatch_id = delegate_leases[0]["dispatch_id"]
    state, _result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-worker",
        dispatch_id=delegate_dispatch_id,
    )
    blocker = state.blockers[blocker_id]
    assert blocker.owner_agent_id == "backend"
    assert blocker.delegate_agent_id is None
    assert blocker.route == TeamBlockerRoute.OWNED
    assert not any(
        item.recipient_agent_id == "worker"
        and item.state == TeamInboxItemState.PENDING
        and item.payload.get("blocker_id") == blocker_id
        for item in state.inbox.values()
    )

    _state, returned_owner_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend"
    )
    returned_dispatch_id = returned_owner_leases[0]["dispatch_id"]
    state, _result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=returned_dispatch_id,
    )
    assert state.blockers[blocker_id].owner_agent_id == "lead"
    event_types = [
        event.event_type
        for event in sorted(
            state.blocker_events.values(),
            key=lambda item: (item.created_at, item.id),
        )
        if event.blocker_id == blocker_id
    ]
    assert TeamBlockerEventType.DELEGATED in event_types
    assert TeamBlockerEventType.DELEGATION_STALLED in event_types
    assert event_types[-1] == TeamBlockerEventType.OWNER_TRANSFERRED


def test_expired_top_owner_blocker_routes_to_governance_and_persists_event(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    admin.update_team(
        team_id="team",
        escalate_to_governance=True,
        expected_revision=repo.read().revision,
    )
    admin.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="lead",
        reporter_session_id="session-lead",
        source_id="dependency-governance",
    )
    base_time = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="manager",
        now=base_time,
        lease_seconds=60,
    )
    assert len(leases) == 1
    assert leases[0]["blockers"][0]["id"] == blocker_id

    state, later_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="worker",
        now=base_time + timedelta(seconds=61),
    )

    assert later_leases == []
    blocker = state.blockers[blocker_id]
    assert blocker.route == TeamBlockerRoute.GOVERNANCE
    assert blocker.terminal_reference_ids
    assert not any(
        item.recipient_agent_id == "manager"
        and item.state == TeamInboxItemState.PENDING
        and item.payload.get("blocker_id") == blocker_id
        for item in state.inbox.values()
    )
    assert any(
        record.intent_type == "team.governance.wake_requested"
        and record.payload["payload"]["blocker_id"] == blocker_id
        for record in repo.list_outbox_records()
    )

    agenda_id = blocker.terminal_reference_ids[0]

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-governance-blocker-resolve",
    )
    state, resolved = _command_service(repo).update_blocker_command(
        session_id="session-lead",
        blocker_id=blocker_id,
        action="resolve",
        note="Dependency recovered before governance action.",
        resolution_refs=["recovery-evidence"],
        request_id="request-governance-blocker-resolve",
    )
    assert resolved["success"] is False
    assert resolved["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.governance_agenda[agenda_id].state == GovernanceAgendaItemState.PENDING
    assert state.blockers[blocker_id].terminal_reference_ids == [agenda_id]


def test_top_owner_no_progress_routes_to_operator_and_persists_event(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).update_team(
        team_id="team",
        operator_communication_target_id="operator-target",
        set_operator_communication_target_id=True,
        expected_revision=repo.read().revision,
    )
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="lead",
        reporter_session_id="session-lead",
        source_id="dependency-operator",
    )
    _state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="manager")
    dispatch_id = leases[0]["dispatch_id"]

    state, _result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-manager",
        dispatch_id=dispatch_id,
    )

    blocker = state.blockers[blocker_id]
    assert blocker.route == TeamBlockerRoute.OPERATOR
    assert blocker.terminal_reference_ids
    assert not any(
        item.recipient_agent_id == "manager"
        and item.state == TeamInboxItemState.PENDING
        and item.payload.get("blocker_id") == blocker_id
        for item in state.inbox.values()
    )
    assert any(
        record.intent_type == "team.operator.escalation_requested"
        and record.payload["payload"]["blocker_id"] == blocker_id
        and record.payload["payload"]["communication_target_id"] == "operator-target"
        for record in repo.list_outbox_records()
    )

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-operator-blocker-resolve",
    )
    state, resolved = _command_service(repo).update_blocker_command(
        session_id="session-lead",
        blocker_id=blocker_id,
        action="resolve",
        note="The reporting line fixed the dependency after operator escalation.",
        resolution_refs=["operator-route-recovery-evidence"],
        request_id="request-operator-blocker-resolve",
    )
    assert resolved["success"] is False
    assert resolved["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.blockers[blocker_id].state == TeamBlockerState.OPEN
    assert state.blockers[blocker_id].route == TeamBlockerRoute.OPERATOR


def test_operator_can_resolve_terminal_blocker_with_audited_reason(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).update_team(
        team_id="team",
        operator_communication_target_id="operator-target",
        set_operator_communication_target_id=True,
        expected_revision=repo.read().revision,
    )
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="lead",
        reporter_session_id="session-lead",
        source_id="operator-resolution",
    )
    _state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="manager")
    state, _result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-manager",
        dispatch_id=leases[0]["dispatch_id"],
    )
    assert state.blockers[blocker_id].route == TeamBlockerRoute.OPERATOR

    state, resolved = _team_admin_service(repo).resolve_terminal_blocker_as_operator(
        blocker_id=blocker_id,
        note="Operator verified the external dependency was restored.",
        operator_actor_id="operator:admin",
        resolution_refs=["incident-123"],
        expected_revision=state.revision,
    )

    assert resolved["success"] is True
    assert resolved["route_before_resolution"] == TeamBlockerRoute.OPERATOR.value
    assert state.blockers[blocker_id].state == TeamBlockerState.RESOLVED
    assert any(
        event.id == resolved["event_id"]
        and event.event_type == TeamBlockerEventType.RESOLVED
        and event.actor_agent_id is None
        and event.metadata["operator_actor_id"] == "operator:admin"
        and event.reference_ids == ["incident-123"]
        for event in state.blocker_events.values()
    )


@pytest.mark.parametrize(
    ("source_kind", "source_id", "source_collection"),
    [
        (TeamBlockerSourceKind.TASK, "task", "tasks"),
        (TeamBlockerSourceKind.DELIVERABLE, "deliverable", "deliverables"),
    ],
)
def test_operator_resolution_restores_blocked_source_before_resolving(
    tmp_path: Path,
    source_kind: TeamBlockerSourceKind,
    source_id: str,
    source_collection: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
    )

    def _seed_terminal_blocker(state):
        if source_kind == TeamBlockerSourceKind.TASK:
            state.tasks["task"].state = TeamTaskState.BLOCKED
        else:
            state.deliverables["deliverable"].state = TeamRecordState.BLOCKED
        state.blockers["operator-source-blocker"] = TeamBlocker(
            id="operator-source-blocker",
            team_id="team",
            project_id="project",
            task_id="task",
            source_kind=source_kind,
            source_id=source_id,
            owner_agent_id="manager",
            affected_task_ids=["task"],
            route=TeamBlockerRoute.OPERATOR,
            summary="Source recovery requires operator attention.",
        )
        return state

    state = repo.update(_seed_terminal_blocker)
    state, result = _team_admin_service(repo).resolve_terminal_blocker_as_operator(
        blocker_id="operator-source-blocker",
        note="Operator verified that the source can proceed.",
        operator_actor_id="operator:admin",
        expected_revision=state.revision,
    )

    assert result["success"] is True
    assert state.blockers["operator-source-blocker"].state == TeamBlockerState.RESOLVED
    source = getattr(state, source_collection)[source_id]
    if source_kind == TeamBlockerSourceKind.TASK:
        assert source.state == TeamTaskState.IN_PROGRESS
    else:
        assert source.state == TeamRecordState.PENDING


@pytest.mark.parametrize(
    ("source_kind", "release_review", "expected_guidance"),
    [
        (
            TeamBlockerSourceKind.REVIEW_REQUEST,
            False,
            "team_retry_review",
        ),
        (
            TeamBlockerSourceKind.REVIEW_REQUEST,
            True,
            "team_retry_release_gate",
        ),
        (
            TeamBlockerSourceKind.RELEASE_PACKAGE,
            True,
            "team_recover_release",
        ),
    ],
)
def test_operator_resolution_rejects_immutable_review_and_release_sources_without_mutation(
    tmp_path: Path,
    source_kind: TeamBlockerSourceKind,
    release_review: bool,
    expected_guidance: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
    )
    now = datetime(2026, 7, 25, tzinfo=UTC)

    def _seed_terminal_source(state):
        state.release_packages["release"] = ReleasePackage(
            id="release",
            project_id="project",
            deliverable_ids=["deliverable"],
            release_branch="team/team/releases/release",
            candidate_commit_sha="a" * 40,
            state=TeamRecordState.BLOCKED,
        )
        source_id = "release"
        if source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
            review = ReviewRequest(
                id="review",
                deliverable_id=None if release_review else "deliverable",
                release_package_id="release" if release_review else None,
                candidate_commit_sha="a" * 40 if release_review else None,
                reviewer_agent_id="worker",
                state=TeamRecordState.BLOCKED,
                block_kind=ReviewBlockKind.SYSTEM,
                decision_at=now,
            )
            state.review_requests[review.id] = review
            if release_review:
                state.release_packages["release"].review_request_ids = [review.id]
            else:
                state.deliverables["deliverable"].review_request_ids = [review.id]
                state.deliverables["deliverable"].state = TeamRecordState.BLOCKED
            source_id = review.id
        state.blockers["operator-lifecycle-blocker"] = TeamBlocker(
            id="operator-lifecycle-blocker",
            team_id="team",
            project_id="project",
            task_id="task",
            source_kind=source_kind,
            source_id=source_id,
            owner_agent_id="manager",
            affected_task_ids=["task"],
            route=TeamBlockerRoute.OPERATOR,
            summary="Lifecycle recovery requires a dedicated action.",
        )
        return state

    repo.update(_seed_terminal_source)
    before = repo.read().model_dump(mode="json")

    with pytest.raises(ValueError, match=expected_guidance):
        _team_admin_service(repo).resolve_terminal_blocker_as_operator(
            blocker_id="operator-lifecycle-blocker",
            note="Generic resolution must not rewrite source history.",
            operator_actor_id="operator:admin",
            expected_revision=repo.read().revision,
        )

    assert repo.read().model_dump(mode="json") == before


def test_operator_cannot_resolve_locally_routed_blocker(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    blocker_id = _report_explicit_blocker(
        repo,
        reporter_agent_id="worker",
        reporter_session_id="session-worker",
        source_id="local-resolution-only",
    )

    with pytest.raises(ValueError, match="operator resolution requires"):
        _team_admin_service(repo).resolve_terminal_blocker_as_operator(
            blocker_id=blocker_id,
            note="Operator should not preempt the local escalation line.",
            operator_actor_id="operator:admin",
            expected_revision=repo.read().revision,
        )

    assert repo.read().blockers[blocker_id].state == TeamBlockerState.OPEN


def test_repository_finalize_agent_dispatch_note_only_is_context_not_progress(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_backend(repo, dispatch_id="dispatch-note", no_progress_count=0)

    state, note_result = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-finalize",
        body="Keep this handoff detail in context.",
    )
    assert state.agent_runtimes["backend"].active_dispatch_id == "dispatch-note"
    assert note_result["progress_made"] is False

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="dispatch-note",
    )

    runtime = state.agent_runtimes["backend"]
    assert result["progress_made"] is False
    assert result["context_activity"] is True
    assert runtime.active_dispatch_id is None
    assert runtime.no_progress_count == 1
    assert runtime.last_progress_at is not None
    assert list(state.action_records) == [note_result["action_record_id"]]
    assert state.action_records[note_result["action_record_id"]].success is True


def test_repository_finalize_agent_dispatch_counts_mark_inbox_handled_when_classified_progress(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_backend(repo, dispatch_id="dispatch-inbox", no_progress_count=3)
    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-inbox",
            tool_name="team_mark_inbox_handled",
            caller_session_id="session-backend",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id="dispatch-inbox",
            request_id="request-inbox",
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="handled_pending_inbox",
        )
    )

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="dispatch-inbox",
    )

    assert result["progress_made"] is True
    assert state.agent_runtimes["backend"].no_progress_count == 0


def test_high_priority_inbox_action_emits_debounced_wake_request(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [
                {
                    "recipient_agent_id": "lead",
                    "question": "Can you decide this now?",
                    "priority": "high",
                    "response_requested": True,
                }
            ],
        },
    )

    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["delay_seconds"] == DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    assert wake_events[0]["payload"] == {
        "team_id": "team",
        "agent_id": "lead",
        "session_id": "session-lead",
        "reason": "inbox",
    }


def test_create_task_command_enqueues_debounced_wake_request_outbox(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-create-task")

    _state, result = _command_service(repo).create_task_command(
        session_id="session-lead",
        request_id="request-create-task-wake",
        task_id="task-wake",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Wake backend",
        description="Create work that should wake backend after debounce.",
    )

    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert result["outbox_record_ids"]
    outbox_records = repo.list_outbox_records(
        intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE,
    )
    assert [record.id for record in outbox_records] == result["outbox_record_ids"]
    assert outbox_records[0].status == TeamOutboxStatus.PENDING
    assert outbox_records[0].payload["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    assert outbox_records[0].payload["delay_seconds"] == (
        DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
    )
    assert outbox_records[0].payload["payload"]["agent_id"] == "backend"


def test_create_task_command_reports_stored_debounced_wake_outbox_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-create-task")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(team_domain_common, "utc_now", lambda: now)

    _state, first = _command_service(repo).create_task_command(
        session_id="session-lead",
        request_id="request-create-task-wake-a",
        task_id="task-wake-a",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Wake backend A",
        description="First wake in the debounce bucket.",
    )
    _state, second = _command_service(repo).create_task_command(
        session_id="session-lead",
        request_id="request-create-task-wake-b",
        task_id="task-wake-b",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Wake backend B",
        description="Second wake in the same debounce bucket.",
    )

    outbox_records = repo.list_outbox_records(
        intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE,
    )
    assert len(outbox_records) == 1
    stored_outbox_id = outbox_records[0].id
    assert first["outbox_record_ids"] == [stored_outbox_id]
    assert second["outbox_record_ids"] == [stored_outbox_id]
    idempotency = repo.get_idempotency_record(
        tool_name="team_create_task",
        caller_identity="agent:lead",
        scope_kind="dispatch",
        scope_id="dispatch-create-task",
        request_id="request-create-task-wake-b",
    )
    assert idempotency is not None
    assert idempotency.response_payload["outbox_record_ids"] == [stored_outbox_id]
    assert stored_outbox_id in idempotency.created_domain_ids


def test_low_priority_inbox_action_does_not_emit_wake_request(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "status_reports": [
                {
                    "recipient_agent_id": "lead",
                    "message": "Progress note only.",
                    "priority": "low",
                    "response_requested": False,
                }
            ],
        },
    )

    assert all(event["event_type"] != AGENT_WAKE_REQUESTED_EVENT_TYPE for event in result["events"])


def test_immediate_lease_path_bypasses_wake_interval_for_ready_task(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _seed_task(repo, task_id="task-ready", owner_agent_id="backend")

    def _throttle_by_interval(state):
        runtime = state.agent_runtimes.setdefault(
            "backend",
            AgentRuntime(agent_id="backend"),
        )
        runtime.next_wake_at = now + timedelta(minutes=15)
        return state

    repo.update(_throttle_by_interval)

    _state, cron_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=now,
        respect_wake_interval=True,
    )
    state, immediate_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=now,
        respect_wake_interval=False,
    )

    assert cron_leases == []
    assert [lease["agent"]["id"] for lease in immediate_leases] == ["backend"]
    assert state.agent_runtimes["backend"].wake_reason == "scheduled"


def test_finalize_reemits_wake_request_for_inbox_arriving_during_active_lease(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _lease_backend(repo, dispatch_id="dispatch-active")

    def _add_pending_message(state):
        state.inbox["inbox-during-lease"] = AgentInboxItem(
            id="inbox-during-lease",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.QUESTION,
            response_requested=True,
            priority=TeamInboxPriority.HIGH,
            state=TeamInboxItemState.PENDING,
            created_at=now,
            updated_at=now,
        )
        return state

    repo.update(_add_pending_message)

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="dispatch-active",
    )

    assert state.agent_runtimes["backend"].active_dispatch_id is None
    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["delay_seconds"] == 0
    assert wake_events[0]["idempotency_key"].endswith(":after-dispatch:dispatch-active")
    assert wake_events[0]["payload"]["agent_id"] == "backend"
    assert wake_events[0]["payload"]["reason"] == "inbox"


def test_repository_finalize_agent_dispatch_validation_failure_has_no_action_feedback(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task", owner_agent_id="backend")
    _lease_backend(repo, dispatch_id="dispatch-failure")

    state, command_result = _command_service(repo).record_team_note_command(
        session_id="session-backend",
        request_id="request-note-empty-finalize",
        body=" ",
    )
    assert command_result["success"] is False
    assert state.action_records == {}
    assert len(repo.list_dispatch_activity(dispatch_id="dispatch-failure")) == 1

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="dispatch-failure",
    )

    assert result["progress_made"] is False
    assert result["failed_activity_count"] == 1
    assert state.action_records == {}
    state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2099, 1, 1, 0, 16, tzinfo=UTC),
        max_no_progress_count=-1,
    )
    assert leases
    assert leases[0]["recent_action_feedback"] == []


def test_repository_finalize_team_chat_without_reply_marks_message_failed(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you reply to this?",
        expected_revision=state.revision,
    )
    state, message = _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id="team-chat:finalize-missing",
        lease_seconds=60,
    )

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="team-chat:finalize-missing",
    )

    runtime = state.agent_runtimes["backend"]
    failed_message = state.conversation_messages[message.id]
    assert result["progress_made"] is False
    assert result["failed_chat_message_ids"] == [message.id]
    assert failed_message.status == "failed"
    assert "without a successful team_reply_to_chat" in (failed_message.error or "")
    assert runtime.active_dispatch_id is None
    assert runtime.no_progress_count == 1


def test_repository_finalize_team_chat_reply_activity_marks_source_replied(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you reply to this?",
        expected_revision=state.revision,
    )
    state, message = _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id="team-chat:finalize-replied",
        lease_seconds=60,
    )
    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-chat-reply",
            tool_name="team_reply_to_chat",
            caller_session_id="session-backend",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id="team-chat:finalize-replied",
            request_id="request-chat-reply",
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="chat_reply",
        )
    )

    state, result = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="team-chat:finalize-replied",
    )

    assert result["progress_made"] is True
    assert result["replied_chat_message_ids"] == [message.id]
    assert state.conversation_messages[message.id].status == "replied"
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.agent_runtimes["backend"].no_progress_count == 0


def test_repository_command_framework_resolves_governance_scope(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    before = repo.read()

    def _apply(state, resolved):
        assert resolved.agent is not None
        assert resolved.agent.id == "governance-manager"
        assert resolved.governance_group_id == "portfolio"
        assert resolved.assignment_id is None
        assert resolved.caller_identity == "governance:portfolio:governance-manager"
        assert resolved.scope_kind == "governance_group"
        assert resolved.scope_id == "portfolio"
        assert resolved.dispatch_id is None
        return TeamCommandApplyResult(
            response_payload={"scope": "governance", "progress_made": False}
        )

    state, result = _command_service(repo)._execute_team_command(
        context=TeamCommandContext(
            ambient_session_id="session-governance",
            wake_session_class="governance",
            governance_group_id="portfolio",
            tool_name="team_governance_probe",
            request_id="request-governance-1",
            progress_classification="governance",
        ),
        normalized_payload={"scope": "governance"},
        handled_inbox_item_ids=[],
        apply_command=_apply,
        require_active_dispatch=False,
    )

    assert result["success"] is True
    assert result["scope"] == "governance"
    assert result["revision"] == before.revision + 1
    assert state.action_records == before.action_records
    activities = repo.list_dispatch_activity(
        tool_name="team_governance_probe",
        request_id="request-governance-1",
    )
    assert len(activities) == 1
    assert activities[0].caller_identity == "governance:portfolio:governance-manager"
    assert activities[0].agent_id == "governance-manager"
    assert activities[0].governance_group_id == "portfolio"
    assert activities[0].assignment_id is None
    idem = repo.get_idempotency_record(
        tool_name="team_governance_probe",
        caller_identity="governance:portfolio:governance-manager",
        scope_kind="governance_group",
        scope_id="portfolio",
        request_id="request-governance-1",
    )
    assert idem is not None
    assert idem.response_payload == result

    replay_state, replay = _command_service(repo)._execute_team_command(
        context=TeamCommandContext(
            ambient_session_id="session-governance",
            wake_session_class="governance",
            governance_group_id="portfolio",
            tool_name="team_governance_probe",
            request_id="request-governance-1",
            progress_classification="governance",
        ),
        normalized_payload={"scope": "governance"},
        handled_inbox_item_ids=[],
        apply_command=_apply,
        require_active_dispatch=False,
    )
    assert replay == result
    assert replay_state.revision == state.revision
    assert len(repo.list_dispatch_activity(tool_name="team_governance_probe")) == 1


def test_repository_command_framework_rejects_wrong_governance_manager(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    state, result = _command_service(repo)._execute_team_command(
        context=TeamCommandContext(
            ambient_session_id="session-manager",
            wake_session_class="governance",
            governance_group_id="portfolio",
            tool_name="team_governance_probe",
            request_id="request-governance-denied",
        ),
        normalized_payload={"scope": "governance"},
        handled_inbox_item_ids=[],
        apply_command=lambda _state, _resolved: TeamCommandApplyResult(
            response_payload={"should_not_apply": True}
        ),
        require_active_dispatch=False,
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "governance_group_not_managed_by_caller"
    assert state.action_records == {}
    activities = repo.list_dispatch_activity(
        tool_name="team_governance_probe",
        request_id="request-governance-denied",
    )
    assert len(activities) == 1
    assert activities[0].status == TeamDispatchActivityStatus.FAILURE
    assert activities[0].governance_group_id == "portfolio"
    assert activities[0].agent_id == "manager"


def test_repository_command_framework_resolves_ephemeral_assignment_scope(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable_ephemeral(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable_ephemeral)
    _seed_task(repo, task_id="task")
    _team_ephemeral_service(repo).request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "session-ephemeral",
                "title": "Worker task",
                "prompt": "Report the result.",
            }
        ],
    )

    def _apply(state, resolved):
        assert resolved.agent is None
        assert resolved.assignment_id == "ephemeral-1"
        assert resolved.governance_group_id is None
        assert resolved.caller_identity == "assignment:ephemeral-1"
        assert resolved.scope_kind == "assignment"
        assert resolved.scope_id == "ephemeral-1"
        return TeamCommandApplyResult(
            response_payload={"scope": "assignment", "progress_made": False}
        )

    _state, result = _command_service(repo)._execute_team_command(
        context=TeamCommandContext(
            ambient_session_id="session-ephemeral",
            wake_session_class="ephemeral_worker",
            assignment_id="ephemeral-1",
            tool_name="team_assignment_probe",
            request_id="request-assignment-1",
            progress_classification="worker_result",
        ),
        normalized_payload={"scope": "assignment"},
        handled_inbox_item_ids=[],
        apply_command=_apply,
        require_active_dispatch=False,
    )

    assert result["success"] is True
    activities = repo.list_dispatch_activity(
        tool_name="team_assignment_probe",
        request_id="request-assignment-1",
    )
    assert len(activities) == 1
    assert activities[0].caller_identity == "assignment:ephemeral-1"
    assert activities[0].agent_id is None
    assert activities[0].governance_group_id is None
    assert activities[0].assignment_id == "ephemeral-1"
    idem = repo.get_idempotency_record(
        tool_name="team_assignment_probe",
        caller_identity="assignment:ephemeral-1",
        scope_kind="assignment",
        scope_id="ephemeral-1",
        request_id="request-assignment-1",
    )
    assert idem is not None
    assert idem.response_payload == result


def test_repository_command_framework_rejects_assignment_session_mismatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable_ephemeral(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable_ephemeral)
    _seed_task(repo, task_id="task")
    _team_ephemeral_service(repo).request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "session-ephemeral",
                "title": "Worker task",
                "prompt": "Report the result.",
            }
        ],
    )

    state, result = _command_service(repo)._execute_team_command(
        context=TeamCommandContext(
            ambient_session_id="wrong-session",
            wake_session_class="ephemeral_worker",
            assignment_id="ephemeral-1",
            tool_name="team_assignment_probe",
            request_id="request-assignment-denied",
        ),
        normalized_payload={"scope": "assignment"},
        handled_inbox_item_ids=[],
        apply_command=lambda _state, _resolved: TeamCommandApplyResult(
            response_payload={"should_not_apply": True}
        ),
        require_active_dispatch=False,
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "assignment_session_mismatch"
    assert state.action_records == {}
    activities = repo.list_dispatch_activity(
        tool_name="team_assignment_probe",
        request_id="request-assignment-denied",
    )
    assert len(activities) == 1
    assert activities[0].status == TeamDispatchActivityStatus.FAILURE
    assert activities[0].assignment_id == "ephemeral-1"
    assert activities[0].agent_id is None


def test_repository_create_tasks_command_allows_sibling_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="lead")

    state, result = _command_service(repo).create_tasks_command(
        session_id="session-lead",
        request_id="request-create-task-batch",
        tasks=[
            {
                "task_id": "batch-a",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 1,
                "title": "Batch A",
                "description": "First sibling.",
            },
            {
                "task_id": "batch-b",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 2,
                "title": "Batch B",
                "description": "Second sibling.",
                "dependency_task_ids": ["batch-a"],
            },
        ],
    )

    assert result["success"] is True
    assert result["created_task_ids"] == ["batch-a", "batch-b"]
    assert state.tasks["batch-b"].dependency_task_ids == ["batch-a"]
    replay_state, replay = _command_service(repo).create_tasks_command(
        session_id="session-lead",
        request_id="request-create-task-batch",
        tasks=[
            {
                "task_id": "batch-a",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 1,
                "title": "Batch A",
                "description": "First sibling.",
            },
            {
                "task_id": "batch-b",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 2,
                "title": "Batch B",
                "description": "Second sibling.",
                "dependency_task_ids": ["batch-a"],
            },
        ],
    )
    assert replay == result
    assert replay_state.revision == state.revision


def test_task_git_base_snapshots_default_parent_explicit_and_batch_parent(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id="root-main",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Main workstream",
        description="Start from the project default.",
        created_by_agent_id="lead",
    )

    def _move_default(state):
        state.projects["project"].git.base_branch = "feature/default-next"
        return state

    repo.update(_move_default)
    admin.create_or_reprioritize_task(
        task_id="child-main",
        owner_agent_id="backend",
        project_id="project",
        parent_task_id="root-main",
        priority=1,
        title="Inherited child",
        description="Keep the parent's workstream.",
        created_by_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="root-explicit",
        owner_agent_id="backend",
        project_id="project",
        git_base_branch="feature/explicit",
        priority=1,
        title="Explicit workstream",
        description="Use an explicit base.",
        created_by_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="lineage-only",
        owner_agent_id="backend",
        project_id="project",
        originating_task_id="root-explicit",
        priority=1,
        title="Lineage only",
        description="Origin lineage must not select the Git workstream.",
        created_by_agent_id="lead",
    )
    admin.create_case(
        case_id="ordinary-feature-case",
        team_id="team",
        project_id="project",
        title="Ordinary feature work",
        source="manual",
    )
    admin.update_task(
        task_id="root-explicit",
        case_id="ordinary-feature-case",
        set_case_id=True,
    )
    _lease_agent_dispatch(repo, agent_id="lead")
    state, result = _command_service(repo).create_tasks_command(
        session_id="session-lead",
        request_id="request-batch-git-base",
        tasks=[
            {
                "task_id": "batch-child",
                "owner_agent_id": "backend",
                "project_id": "project",
                "parent_task_id": "batch-parent",
                "priority": 1,
                "title": "Batch child",
                "description": "Resolve its parent inside the batch.",
            },
            {
                "task_id": "batch-parent",
                "owner_agent_id": "backend",
                "project_id": "project",
                "git_base_branch": "feature/batch",
                "priority": 1,
                "title": "Batch parent",
                "description": "Own the batch workstream.",
            },
        ],
    )

    assert result["success"] is True
    assert state.tasks["root-main"].git_base_branch == "main"
    assert state.tasks["child-main"].git_base_branch == "main"
    assert state.tasks["root-explicit"].git_base_branch == "feature/explicit"
    assert state.tasks["lineage-only"].git_base_branch == "feature/default-next"
    assert state.tasks["batch-parent"].git_base_branch == "feature/batch"
    assert state.tasks["batch-child"].git_base_branch == "feature/batch"


def test_task_git_base_snapshot_resolves_unset_managed_repository_default(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _configure_unset_base(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id=MANAGED_REPO_ID,
            directory_path="root/team-project",
            base_branch=None,
        )
        return state

    repo.update(_configure_unset_base)
    lookups: list[tuple[str, str]] = []

    class _ManagedGit:
        def get_repository(
            self,
            managed_repo_id: str,
            *,
            directory_path: str,
        ) -> dict[str, str]:
            lookups.append((managed_repo_id, directory_path))
            return {"default_branch": "trunk"}

    admin = TeamAdminService(repo, managed_git_admin=_ManagedGit())
    state = admin.create_or_reprioritize_task(
        task_id="resolved-admin-default",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Resolve the managed default",
        description="Persist the actual branch on this task.",
        created_by_agent_id="lead",
    )
    assert state.tasks["resolved-admin-default"].git_base_branch == "trunk"
    assert state.projects["project"].git is not None
    assert state.projects["project"].git.base_branch is None

    _lease_agent_dispatch(repo, agent_id="lead")
    command = TeamTaskCreationCommandService(
        repo,
        git_default_branch_resolver=lambda **kwargs: _ManagedGit().get_repository(
            **kwargs
        )["default_branch"],
    )
    state, result = command.create_task_command(
        session_id="session-lead",
        task_id="resolved-command-default",
        owner_agent_id="backend",
        project_id="project",
        title="Resolve through command service",
        description="Use the same managed repository authority.",
        request_id="request-resolve-managed-default",
    )

    assert result["success"] is True
    assert state.tasks["resolved-command-default"].git_base_branch == "trunk"
    assert lookups == [
        (MANAGED_REPO_ID, "root/team-project"),
        (MANAGED_REPO_ID, "root/team-project"),
    ]


@pytest.mark.parametrize("lock_kind", ["context", "deliverable", "promotion"])
def test_task_git_base_edit_locks_after_authoritative_git_work(
    tmp_path: Path,
    lock_kind: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    admin = _team_admin_service(repo)
    state = admin.update_task(
        task_id="task",
        git_base_branch="feature/before-lock",
        set_git_base_branch=True,
    )
    assert state.tasks["task"].git_base_branch == "feature/before-lock"

    if lock_kind == "context":
        admin.create_git_work_context_with_id(
            context_id="ctx-lock",
            agent_id="backend",
            task_id="task",
            base_branch="feature/before-lock",
            base_commit="base-lock",
            agent_branch="agents/session-backend/task-lock",
        )
    elif lock_kind == "deliverable":
        admin.record_deliverable(
            deliverable_id="deliverable-lock",
            task_id="task",
            producer_agent_id="backend",
            summary="Lock the task base.",
        )
    else:
        def _add_promotion(current):
            current.git_promotions["promotion-lock"] = GitPromotionRecord(
                id="promotion-lock",
                project_id="project",
                task_id="task",
                actor_agent_id="backend",
                source_branch="agents/session-backend/task-lock",
                target_branch="team/team/features/task-backend",
                promotion_kind="feature",
            )
            return current

        repo.update(_add_promotion)

    with pytest.raises(ValueError) as excinfo:
        admin.update_task(
            task_id="task",
            git_base_branch="feature/after-lock",
            set_git_base_branch=True,
        )

    assert getattr(excinfo.value, "code", None) == "git_base_locked"
    assert repo.read().tasks["task"].git_base_branch == "feature/before-lock"


def test_task_git_base_lock_is_stable_through_command_surface(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _team_admin_service(repo).create_git_work_context_with_id(
        context_id="ctx-locked-command",
        agent_id="backend",
        task_id="task",
        base_branch="main",
        base_commit="base-main",
        agent_branch="agents/session-backend/task-locked-command",
    )
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-lock")

    state, result = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="task",
        git_base_branch="feature/too-late",
        request_id="request-locked-git-base",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "git_base_locked"
    assert state.tasks["task"].git_base_branch == "main"


def test_repository_create_tasks_command_rejects_cycles_atomically(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="lead")
    before = repo.read()

    state, result = _command_service(repo).create_tasks_command(
        session_id="session-lead",
        request_id="request-create-cycle",
        tasks=[
            {
                "task_id": "cycle-a",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 1,
                "title": "Cycle A",
                "description": "First cycle task.",
                "dependency_task_ids": ["cycle-b"],
            },
            {
                "task_id": "cycle-b",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 1,
                "title": "Cycle B",
                "description": "Second cycle task.",
                "dependency_task_ids": ["cycle-a"],
            },
        ],
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "task_graph_invalid"
    assert "planned_tasks" in result["correction_context"]
    assert "graph_errors" in result["correction_context"]
    assert "cycle-a" not in state.tasks
    assert "cycle-b" not in state.tasks
    assert repo.read().revision == before.revision


def test_repository_update_task_command_reports_cross_project_graph_context(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task")
    _team_admin_service(repo).create_project(
        project_id="other-project",
        team_id="team",
        name="Other Project",
        lead_agent_id="lead",
        lead_session_id=None,
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="other-task",
        owner_agent_id="lead",
        project_id="other-project",
        priority=1,
        title="Other task",
        description="Other project work.",
        created_by_agent_id="lead",
    )
    _lease_agent_dispatch(repo, agent_id="backend")

    state, result = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="backend-task",
        dependency_task_ids=["other-task"],
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "task_graph_invalid"
    context_tasks = result["correction_context"]["current_tasks"]
    assert {item["task_id"] for item in context_tasks} >= {
        "backend-task",
        "other-task",
    }
    assert state.tasks["backend-task"].dependency_task_ids == []
    assert repo.read().tasks["backend-task"].dependency_task_ids == []


def test_repository_update_task_command_owner_finishes_without_deliverable_notice(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task")
    _seed_git_work_context(repo, task_id="backend-task")
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="follow-up-task",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Follow-up",
        description="Starts after the administrative task is finished.",
        created_by_agent_id="backend",
        dependency_task_ids=["backend-task"],
    )
    _lease_agent_dispatch(repo, agent_id="backend")

    state, result = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="backend-task",
        state="finished",
    )

    assert result["success"] is True
    assert state.tasks["backend-task"].state == TeamTaskState.FINISHED
    assert state.dependency_status("follow-up-task") == "satisfied"
    assert state.deliverables == {}
    assert state.git_work_contexts["ctx-task"].state == GitWorkContextState.ABANDONED
    assert not any(
        item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE for item in state.inbox.values()
    )


def test_task_finish_with_proposed_unresolved_dependency_is_rejected(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_task(repo, task_id="dependency")
    _lease_agent_dispatch(repo, agent_id="backend")

    state, result = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="task",
        state="finished",
        dependency_task_ids=["dependency"],
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "task_boundary_blocked"
    assert result["correction_context"]["unsatisfied_dependency_ids"] == [
        "dependency"
    ]
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["task"].dependency_task_ids == []

    with pytest.raises(ValueError, match="unsatisfied dependencies"):
        _team_admin_service(repo).update_task(
            task_id="task",
            state=TeamTaskState.FINISHED,
            dependency_task_ids=["dependency"],
        )

    persisted = repo.read()
    assert persisted.tasks["task"].state == TeamTaskState.PENDING
    assert persisted.tasks["task"].dependency_task_ids == []

    state = _team_admin_service(repo).update_task(
        task_id="task",
        state=TeamTaskState.FINISHED,
        expected_revision=persisted.revision,
    )
    with pytest.raises(ValueError, match="terminal task task cannot change"):
        _team_admin_service(repo).update_task(
            task_id="task",
            dependency_task_ids=["dependency"],
            expected_revision=state.revision,
        )
    persisted = repo.read()
    assert persisted.tasks["task"].state == TeamTaskState.FINISHED
    assert persisted.tasks["task"].dependency_task_ids == []


def test_terminal_parent_rejects_new_and_reparented_children(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id="terminal-parent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Completed parent",
        description="This parent has crossed its terminal boundary.",
        created_by_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="existing-candidate",
        owner_agent_id="lead",
        project_id="project",
        priority=2,
        title="Existing candidate",
        description="This task must remain top-level.",
        created_by_agent_id="lead",
    )
    state = admin.update_task(
        task_id="terminal-parent",
        state=TeamTaskState.FINISHED,
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="task parent terminal-parent is not active"):
        admin.create_or_reprioritize_task(
            task_id="new-child",
            owner_agent_id="lead",
            project_id="project",
            priority=3,
            title="Late child",
            description="This child would invalidate the completed parent.",
            created_by_agent_id="lead",
            parent_task_id="terminal-parent",
            expected_revision=state.revision,
        )

    with pytest.raises(ValueError, match="task parent terminal-parent is not active"):
        admin.update_task(
            task_id="existing-candidate",
            parent_task_id="terminal-parent",
            set_parent_task_id=True,
            expected_revision=state.revision,
        )

    persisted = repo.read()
    assert "new-child" not in persisted.tasks
    assert persisted.tasks["existing-candidate"].parent_task_id is None


def test_legacy_agent_finish_validates_proposed_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_task(repo, task_id="dependency")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [
                {
                    "task_id": "task",
                    "state": "finished",
                    "dependency_task_ids": ["dependency"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("unsatisfied dependencies" in error for error in result["errors"])
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["task"].dependency_task_ids == []
    assert repo.read().tasks["task"].state == TeamTaskState.PENDING


def test_repository_update_task_command_cannot_hide_blocked_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task")
    _seed_deliverable(
        repo,
        deliverable_id="blocked-deliverable",
        task_id="backend-task",
    )

    def _block_deliverable(state):
        state.deliverables["blocked-deliverable"].state = TeamRecordState.BLOCKED
        return state

    repo.update(_block_deliverable)
    _lease_agent_dispatch(repo, agent_id="backend")

    state, result = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="backend-task",
        state="finished",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["backend-task"].state == TeamTaskState.PENDING


def test_terminal_deliverable_action_requires_fresh_dispatch_for_late_high_priority_handoff(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-stale-terminal")
    wake_at = datetime(2099, 1, 1, tzinfo=UTC)

    def _record_wake_snapshot(state):
        state.agent_runtimes["backend"].last_wake_at = wake_at
        return state

    repo.update(_record_wake_snapshot)

    def _add_late_directive(state):
        created_at = wake_at + timedelta(seconds=1)
        state.inbox["late-directive"] = AgentInboxItem(
            id="late-directive",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="backend-task",
            item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
            priority=TeamInboxPriority.HIGH,
            payload={"message": "Use the corrected source document."},
            created_at=created_at,
            updated_at=created_at,
        )
        return state

    repo.update(_add_late_directive)
    state, stale = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="backend-task",
        summary="Prepared from the old source.",
        request_id="request-stale-terminal",
    )
    assert stale["success"] is False
    assert stale["errors"][0]["code"] == "stale_dispatch_context"
    assert "include its id" not in stale["errors"][0]["message"]
    assert stale["correction_context"]["recovery_action"] == "end_current_turn"
    assert stale["correction_context"]["fresh_dispatch_required"] is True
    assert stale["correction_context"]["current_dispatch_scope_unchanged"] is True
    assert stale["correction_context"]["newer_high_priority_inbox_items"][0]["id"] == (
        "late-directive"
    )
    assert state.deliverables == {}

    state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="backend-task",
        summary="Prepared from the corrected source.",
        handled_inbox_item_ids=["late-directive"],
        request_id="request-fresh-terminal",
    )
    assert submitted["success"] is False
    assert submitted["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.inbox["late-directive"].state == TeamInboxItemState.PENDING

    state, finalized = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id="dispatch-stale-terminal",
    )

    assert state.dispatches["dispatch-stale-terminal"].inbox_item_ids == []
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    wake_events = [
        event
        for event in finalized["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["reason"] == "inbox"
    assert finalized["outbox_record_ids"]

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=wake_at + timedelta(seconds=2),
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    assert leases[0]["dispatch_id"] != "dispatch-stale-terminal"
    assert leases[0]["dispatch"]["inbox_item_ids"] == ["late-directive"]
    assert state.inbox["late-directive"].state == TeamInboxItemState.PENDING


def test_decide_review_stale_dispatch_cannot_absorb_late_high_priority_inbox(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="review-task")
    _seed_deliverable(
        repo,
        deliverable_id="review-deliverable",
        task_id="review-task",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="review-deliverable",
        dispatch_id="dispatch-request-stale-review",
    )
    _state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="review-deliverable",
        reviewer_agent_id="worker",
        request_id="request-stale-review",
    )
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-stale-review",
    )
    wake_at = datetime(2099, 1, 1, tzinfo=UTC)

    def _add_late_review_instruction(state):
        runtime = state.agent_runtimes["worker"]
        runtime.last_wake_at = wake_at
        created_at = wake_at + timedelta(seconds=1)
        state.inbox["late-review-instruction"] = AgentInboxItem(
            id="late-review-instruction",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            task_id="review-task",
            item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
            priority=TeamInboxPriority.HIGH,
            payload={"message": "Use the corrected review standard."},
            created_at=created_at,
            updated_at=created_at,
        )
        return state

    repo.update(_add_late_review_instruction)
    state, stale = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=requested["review_request_id"],
        state="accepted",
        request_id="decide-stale-review",
    )

    assert stale["errors"][0]["code"] == "stale_dispatch_context"
    assert stale["correction_context"]["recovery_action"] == "end_current_turn"
    assert state.review_requests[requested["review_request_id"]].state == (
        TeamRecordState.PENDING
    )

    state, widened = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=requested["review_request_id"],
        state="accepted",
        handled_inbox_item_ids=["late-review-instruction"],
        request_id="decide-stale-review-widened",
    )

    assert widened["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.inbox["late-review-instruction"].state == TeamInboxItemState.PENDING
    assert state.dispatches["dispatch-stale-review"].inbox_item_ids == [
        next(
            item.id
            for item in state.inbox.values()
            if item.payload.get("review_request_id") == requested["review_request_id"]
        )
    ]


def test_repository_update_task_command_supervisor_finishes_subordinate_task(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="worker-task", owner_agent_id="worker")
    _lease_agent_dispatch(repo, agent_id="backend")

    state, result = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="worker-task",
        state="finished",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["worker-task"].state == TeamTaskState.PENDING
    assert state.tasks["worker-task"].owner_agent_id == "worker"


def test_repository_update_task_command_supervisor_claims_and_finishes_subordinate_task(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="worker-task", owner_agent_id="worker")
    _lease_agent_dispatch(repo, agent_id="backend")

    state, result = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="worker-task",
        state="finished",
        owner_agent_id="backend",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["worker-task"].state == TeamTaskState.PENDING
    assert state.tasks["worker-task"].owner_agent_id == "worker"


def test_repository_stand_down_command_blocks_held_subtree_commands(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(repo, agent_id="lead")

    state, result = _command_service(repo).stand_down_agent_command(
        session_id="session-lead",
        agent_id="backend",
        reason="Pause backend subtree.",
        request_id="request-stand-down-backend",
    )

    assert result["success"] is True
    assert state.stand_downs[result["stand_down_id"]].source == (AgentStandDownSource.SUPERIOR)

    _lease_agent_dispatch(repo, agent_id="worker")
    state, blocked = _command_service(repo).record_team_note_command(
        session_id="session-worker",
        body="I should not be able to mutate while stood down.",
        request_id="request-worker-note-blocked",
    )

    assert blocked["success"] is False
    assert blocked["errors"][0]["code"] == "agent_stood_down"
    assert state.action_records == {}


def test_repository_direct_agent_stand_down_does_not_hold_subordinates(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-worker-before-direct-hold",
    )
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-lead-direct-hold",
    )

    state, result = _command_service(repo).stand_down_agent_command(
        session_id="session-lead",
        agent_id="backend",
        reason="Pause only the backend manager.",
        request_id="request-stand-down-backend-only",
        scope="agent",
    )

    stand_down = state.stand_downs[result["stand_down_id"]]
    assert result["success"] is True
    assert stand_down.scope == "agent"
    operation_by_agent = {item["id"]: item for item in runtime_operations_payload(state)["agents"]}
    assert operation_by_agent["worker"]["stand_down_active"] is False
    assert operation_by_agent["backend"]["stand_down_active"] is True

    state, allowed = _command_service(repo).record_team_note_command(
        session_id="session-worker",
        body="Worker can continue under a direct manager hold.",
        request_id="request-worker-note-allowed",
    )

    assert allowed["success"] is True
    assert len(state.action_records) == 1


def test_repository_revoke_stand_down_command_leaves_operator_holds_active(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state = _team_runtime_service(repo).stand_down_agent(
        agent_id="backend",
        reason="Operator hold.",
        set_by="operator:test",
    )
    operator_stand_down_id = next(iter(state.stand_downs))
    _lease_agent_dispatch(repo, agent_id="lead")
    state, result = _command_service(repo).stand_down_agent_command(
        session_id="session-lead",
        agent_id="backend",
        reason="Lead hold.",
        request_id="request-lead-stand-down",
    )
    superior_stand_down_id = result["stand_down_id"]

    state, result = _command_service(repo).revoke_stand_down_command(
        session_id="session-lead",
        agent_id="backend",
    )

    assert result["success"] is True
    assert result["revoked_stand_down_ids"] == [superior_stand_down_id]
    assert state.stand_downs[superior_stand_down_id].cleared_by == "lead"
    assert state.stand_downs[operator_stand_down_id].cleared_at is None


def test_repository_round_trips_agent_avatar_metadata(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    avatar = AgentAvatar(
        asset_id="av_0123456789abcdef0123456789abcdef",
        source="uploaded",
        mime_type="image/webp",
        sha256="a" * 64,
    )

    state = _team_admin_service(repo).set_agent_avatar(
        agent_id="backend",
        avatar=avatar,
        expected_revision=repo.read().revision,
    )

    assert state.agents["backend"].avatar == avatar
    reread = TeamStateRepository(tmp_path).read()
    assert reread.agents["backend"].avatar is not None
    assert reread.agents["backend"].avatar.asset_id == avatar.asset_id
    assert reread.agents["worker"].avatar is None


def test_repository_round_trips_agent_voice_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    voice_profile = AgentVoiceProfile(
        voice="cedar",
        speaking_style="Calm, concise, and lightly upbeat.",
    )

    state = _team_admin_service(repo).update_agent(
        agent_id="backend",
        voice_profile=voice_profile,
        set_voice_profile=True,
        expected_revision=repo.read().revision,
    )

    assert state.agents["backend"].voice_profile == voice_profile
    reread = TeamStateRepository(tmp_path).read()
    assert reread.agents["backend"].voice_profile is not None
    assert reread.agents["backend"].voice_profile.voice == "cedar"
    assert reread.agents["backend"].voice_profile.speaking_style == (
        "Calm, concise, and lightly upbeat."
    )

    cleared = _team_admin_service(TeamStateRepository(tmp_path)).update_agent(
        agent_id="backend",
        voice_profile=AgentVoiceProfile(),
        set_voice_profile=True,
        expected_revision=reread.revision,
    )

    assert cleared.agents["backend"].voice_profile is None


def test_repository_round_trips_team_chat_conversation_and_reply(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, conversation, created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    assert created is True
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you summarize the blocker?",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    state, message = _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id="team-chat:test",
        lease_seconds=60,
    )

    assert message.status == "running"
    assert message.dispatch_id == "team-chat:test"
    assert state.agent_runtimes["backend"].active_dispatch_id == "team-chat:test"
    assert state.agent_runtimes["backend"].wake_reason == "team_chat"

    reread = TeamStateRepository(tmp_path).read()
    assert reread.conversations[conversation.id].agent_id == "backend"
    assert reread.conversation_messages[message.id].body == ("Can you summarize the blocker?")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": "team-chat:test",
            "chat_replies": [
                {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "body": "The dependency task is still open.",
                }
            ],
        },
        dispatch_id="team-chat:test",
        expected_revision=repo.read().revision,
    )

    assert result["success"] is True
    assert result["applied"] is True
    assert result["created_chat_reply_message_ids"]
    assert result["replied_chat_message_ids"] == [message.id]
    assert state.conversation_messages[message.id].status == "replied"
    reply = state.conversation_messages[result["created_chat_reply_message_ids"][0]]
    assert reply.sender == "agent"
    assert reply.source_message_id == message.id
    assert reply.body == "The dependency task is still open."
    assert state.conversations[conversation.id].last_message_preview == (
        "The dependency task is still open."
    )
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.agent_runtimes["backend"].wake_leased_until is None


def test_live_replied_chat_dispatch_lease_blocks_normal_inbox_wake(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you summarize the blocker?",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    state, message = _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id="team-chat:live",
        lease_seconds=3600,
    )

    state, result = _command_service(repo).reply_to_chat_command(
        session_id="session-backend",
        conversation_id=conversation.id,
        message_id=message.id,
        body="The dependency task is still open.",
        request_id="request-chat-reply-live",
    )

    def _pin_live_chat_lease(state):
        state.agent_runtimes["backend"].wake_leased_until = now + timedelta(minutes=10)
        state.inbox["normal-note"] = AgentInboxItem(
            id="normal-note",
            recipient_agent_id="backend",
            sender_agent_id="worker",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=False,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
            created_at=now + timedelta(minutes=1),
            updated_at=now + timedelta(minutes=1),
        )
        return state

    repo.update(_pin_live_chat_lease)
    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=now + timedelta(minutes=2),
    )

    assert result["success"] is True
    assert state.conversation_messages[message.id].status == "replied"
    assert leases == []
    assert state.agent_runtimes["backend"].active_dispatch_id == "team-chat:live"
    assert state.agent_runtimes["backend"].wake_reason == "team_chat"


def test_completed_chat_dispatch_lease_does_not_block_normal_inbox_wake(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you summarize the blocker?",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    state, message = _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id="team-chat:stale",
        lease_seconds=3600,
    )

    def _leave_replied_chat_dispatch_stale(state):
        state.conversation_messages[message.id].status = "replied"
        state.conversation_messages[message.id].updated_at = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        state.agent_runtimes["backend"].wake_leased_until = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        state.inbox["normal-note"] = AgentInboxItem(
            id="normal-note",
            recipient_agent_id="backend",
            sender_agent_id="worker",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=False,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
            created_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        )
        return state

    repo.update(_leave_replied_chat_dispatch_stale)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, 0, 3, tzinfo=UTC),
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "inbox"
    assert [item["id"] for item in leases[0]["inbox"]] == ["normal-note"]
    assert state.agent_runtimes["backend"].active_dispatch_id != "team-chat:stale"
    assert state.agent_runtimes["backend"].wake_reason == "inbox"


def test_repository_queues_chat_dispatch_until_active_dispatch_released(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def _mark_backend_busy(state):
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            active_dispatch_id="team-dispatch-active",
            wake_leased_until=now + timedelta(minutes=10),
        )
        return state

    repo.update(_mark_backend_busy)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you check this after the current wake?",
        created_by="operator:alice",
        mutation_authorization=_authorized_chat_mutation(repo, "backend"),
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"

    state, message = _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
        reason="agent backend already has active Team dispatch team-dispatch-active",
    )

    assert message.status == "queued"
    assert message.dispatch_id == dispatch_id
    assert state.agent_runtimes["backend"].active_dispatch_id == "team-dispatch-active"

    _state, claims = _team_chat_service(repo).claim_pending_chat_message_dispatches(now=now)
    assert claims == []

    def _release_backend(state):
        state.agent_runtimes["backend"].wake_leased_until = None
        return state

    repo.update(_release_backend)
    state, claims = _team_chat_service(repo).claim_pending_chat_message_dispatches(
        now=now + timedelta(minutes=1),
    )

    assert claims == [
        {
            "agent_id": "backend",
            "session_id": "session-backend",
            "conversation_id": conversation.id,
            "message_id": message.id,
            "dispatch_id": dispatch_id,
        }
    ]
    assert state.conversation_messages[message.id].status == "running"
    assert state.agent_runtimes["backend"].active_dispatch_id == dispatch_id
    assert state.agent_runtimes["backend"].wake_reason == "team_chat"
    assert state.dispatches[dispatch_id].project_ids == ["project"]
    assert state.dispatches[dispatch_id].managed_repo_ids == []


def test_repository_reply_only_chat_dispatch_has_no_mutation_scope(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please only explain the current state.",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"

    state, _message = _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
    )

    assert state.dispatches[dispatch_id].project_ids == []
    assert state.dispatches[dispatch_id].managed_repo_ids == []


@pytest.mark.parametrize("blocker", ["paused", "stood_down"])
def test_repository_queued_chat_dispatch_respects_current_wake_blockers(
    tmp_path: Path,
    blocker: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please answer when available.",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
    )

    if blocker == "paused":

        def _pause_backend(state):
            state.agents["backend"].status = AgentStatus.PAUSED
            return state

        repo.update(_pause_backend)
    else:
        _team_runtime_service(repo).stand_down_agent(
            agent_id="backend",
            reason="Operator hold.",
            set_by="operator:test",
            expected_revision=repo.read().revision,
        )

    state, claims = _team_chat_service(repo).claim_pending_chat_message_dispatches(
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert claims == []
    assert state.conversation_messages[message.id].status == "queued"
    runtime = state.agent_runtimes.get("backend")
    assert runtime is None or runtime.active_dispatch_id is None


def test_repository_reemits_chat_event_when_active_dispatch_releases(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    active_dispatch_id = "team-dispatch-active"

    def _mark_backend_busy(state):
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            active_dispatch_id=active_dispatch_id,
            wake_leased_until=now + timedelta(minutes=10),
        )
        return state

    repo.update(_mark_backend_busy)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please pick this up as soon as your current wake finishes.",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    queued_dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=queued_dispatch_id,
        reason="agent backend already has active Team dispatch team-dispatch-active",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={"agent_id": "backend", "dispatch_id": active_dispatch_id},
        dispatch_id=active_dispatch_id,
        expected_revision=repo.read().revision,
    )

    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.conversation_messages[message.id].status == "queued"
    assert result["events"] == [
        {
            "event_type": CHAT_MESSAGE_QUEUED_EVENT_TYPE,
            "correlation_key": queued_dispatch_id,
            "idempotency_key": (
                "team-addon:"
                f"{CHAT_MESSAGE_QUEUED_EVENT_TYPE}:agent-ready:"
                f"{active_dispatch_id}:{message.id}"
            ),
            "payload": {
                "team_id": "team",
                "agent_id": "backend",
                "session_id": "session-backend",
                "conversation_id": conversation.id,
                "message_id": message.id,
                "dispatch_id": queued_dispatch_id,
            },
        }
    ]


def test_repository_claim_pending_chat_message_dispatches_honors_limit_zero(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please answer when available.",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
        reason="agent backend already has active Team dispatch team-dispatch-active",
    )

    state, claims = _team_chat_service(repo).claim_pending_chat_message_dispatches(now=now, limit=0)

    assert claims == []
    assert state.conversation_messages[message.id].status == "queued"
    assert state.conversation_messages[message.id].dispatch_id == dispatch_id
    runtime = state.agent_runtimes.get("backend")
    assert runtime is None or runtime.active_dispatch_id is None
    assert runtime is None or runtime.wake_leased_until is None


def test_repository_fails_active_team_chat_message_when_reply_item_is_invalid(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you summarize the blocker?",
        expected_revision=state.revision,
    )
    dispatch_id = "team-chat:test"
    state, message = _team_chat_service(repo).begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
        lease_seconds=60,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": dispatch_id,
            "chat_replies": [
                {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                }
            ],
        },
        dispatch_id=dispatch_id,
        expected_revision=repo.read().revision,
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert result["created_chat_reply_message_ids"] == []
    failed_message = state.conversation_messages[message.id]
    assert failed_message.status == "failed"
    assert "chat_replies[0]" in (failed_message.error or "")
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.conversations[conversation.id].last_message_preview.startswith("Reply failed:")


def test_repository_records_conversation_voice_call_idempotently(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, first_message = _team_chat_service(repo).record_conversation_voice_call(
        conversation_id=conversation.id,
        voice_session_id="voice-1",
        status="running",
        summary="Voice call with Backend Agent",
    )
    revision = state.revision

    state, second_message = _team_chat_service(repo).record_conversation_voice_call(
        conversation_id=conversation.id,
        voice_session_id="voice-1",
        status="running",
        summary="Voice call with Backend Agent",
    )

    assert second_message.id == first_message.id
    assert state.revision == revision
    assert [
        message.id
        for message in state.conversation_messages.values()
        if message.voice_session_id == "voice-1"
    ] == [first_message.id]


def test_repository_gets_existing_team_chat_conversation_without_revision_bump(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    assert created is True
    revision = state.revision

    state, same_conversation, created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=revision,
    )

    assert created is False
    assert same_conversation.id == conversation.id
    assert state.revision == revision
    assert repo.read().revision == revision


def test_repository_creates_separate_team_chat_conversations_per_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, alice_conversation, created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        created_by="operator:alice@example.com",
        expected_revision=repo.read().revision,
    )
    assert created is True

    state, bob_conversation, created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="bob@example.com",
        operator_display_name="Bob",
        created_by="operator:bob@example.com",
        expected_revision=state.revision,
    )

    assert created is True
    assert bob_conversation.id != alice_conversation.id
    assert state.conversations[alice_conversation.id].operator_user_id == "alice@example.com"
    assert state.conversations[bob_conversation.id].operator_user_id == "bob@example.com"


def test_repository_rejects_chat_reply_from_wrong_agent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Are you blocked?",
        expected_revision=state.revision,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "chat_replies": [
                {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "body": "I cannot answer for that agent.",
                }
            ],
        },
        expected_revision=state.revision,
    )

    assert result["success"] is False
    assert not result["created_chat_reply_message_ids"]
    assert "chat_replies[0]" in result["errors"][0]
    assert state.conversation_messages[message.id].status == "created"


def test_repository_agent_action_sends_chat_message_to_known_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, _conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "operator_chat_messages": [
                {
                    "recipient_operator_user_id": "alice@example.com",
                    "body": "I found the issue.",
                    "summary": "Issue found",
                }
            ],
        },
        expected_revision=state.revision,
    )

    assert result["success"] is True
    assert result["created_operator_chat_message_ids"]
    message = state.conversation_messages[result["created_operator_chat_message_ids"][0]]
    conversation = state.conversations[message.conversation_id]
    assert conversation.agent_id == "backend"
    assert conversation.operator_user_id == "alice@example.com"
    assert message.sender == "agent"
    assert message.body == "I found the issue."
    assert message.metadata["recipient_operator_user_id"] == "alice@example.com"


def test_repository_agent_action_rejects_other_agents_operator_chat_recipient(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, _conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="lead",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "operator_chat_messages": [
                {
                    "recipient_operator_user_id": "alice@example.com",
                    "body": "This should fail.",
                }
            ],
        },
        expected_revision=state.revision,
    )

    assert result["success"] is False
    assert result["created_operator_chat_message_ids"] == []
    assert "not a member" in result["errors"][0]
    assert state.conversation_messages == {}


def test_repository_agent_action_rejects_unknown_operator_chat_recipient(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "operator_chat_messages": [
                {
                    "recipient_operator_user_id": "nobody@example.com",
                    "body": "I found the issue.",
                }
            ],
        },
        expected_revision=repo.read().revision,
    )

    assert result["success"] is False
    assert result["created_operator_chat_message_ids"] == []
    assert "operator_chat_messages[0]" in result["errors"][0]
    assert state.conversation_messages == {}


def test_repository_migrates_action_feedback_inbox_and_low_priority_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE team_state_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO team_state_metadata(key, value)
            VALUES ('sqlite_schema_version', '1');
            CREATE TABLE team_action_records (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                dispatch_id TEXT NOT NULL,
                action_json TEXT NOT NULL,
                success INTEGER NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                dismissed_at TEXT,
                dismissed_by TEXT,
                dismissed_reason TEXT
            );
            CREATE TABLE team_review_requests (
                id TEXT PRIMARY KEY,
                deliverable_id TEXT NOT NULL,
                release_package_id TEXT,
                candidate_commit_sha TEXT,
                reviewer_agent_id TEXT NOT NULL,
                state TEXT NOT NULL,
                review_profile_id TEXT,
                criteria_snapshot_json TEXT,
                instructions TEXT,
                reviewed_commit_sha TEXT,
                supersedes_review_request_id TEXT,
                superseded_by_review_request_id TEXT,
                retry_reason TEXT,
                retry_requested_by TEXT,
                block_kind TEXT,
                retired_at TEXT,
                retired_by TEXT,
                retirement_reason TEXT,
                created_at TEXT,
                decision_at TEXT
            );
            """
        )

    repo = TeamStateRepository(tmp_path)

    assert repo.read().action_records == {}
    with sqlite3.connect(db_path) as connection:
        action_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_action_records)")
        }
        inbox_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(team_inbox)")}
        runtime_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_agent_runtimes)")
        }
        agent_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_agents)")
        }
        governance_runtime_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_governance_runtimes)")
        }
        task_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(team_tasks)")}
        deliverable_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_deliverables)")
        }
        review_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_review_requests)")
        }
        review_profile_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_review_profiles)")
        }
        release_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_release_packages)")
        }
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        metadata = dict(connection.execute("SELECT key, value FROM team_state_metadata").fetchall())
    assert "feedback_delivered_at" in action_columns
    assert "feedback_delivered_by_dispatch_id" in action_columns
    assert "response_requested" in inbox_columns
    assert "priority" in inbox_columns
    assert "low_priority_batch_started_at" in runtime_columns
    assert "low_priority_batch_cycle_count" in runtime_columns
    assert "low_priority_batch_due_after_cycles" in runtime_columns
    assert "context_clearing_policy" in runtime_columns
    assert "last_delivered_role_description_fingerprint" in runtime_columns
    assert "voice_profile_json" in agent_columns
    assert "last_cost_signal_fingerprints_json" in governance_runtime_columns
    assert "last_blocked_signal_fingerprints_json" in governance_runtime_columns
    assert "state_entered_at" in task_columns
    assert {"task_kind", "outcome_contract_json", "completion_json"} <= task_columns
    assert "supersedes_deliverable_id" in deliverable_columns
    assert "superseded_by_deliverable_id" in deliverable_columns
    assert "verification_json" in deliverable_columns
    assert "outcomes_json" in deliverable_columns
    assert {"supersession_reason", "superseded_at", "superseded_by_actor"} <= (
        deliverable_columns
    )
    assert {
        "rework_requested_at",
        "rework_requested_by_agent_id",
        "rework_reason",
    } <= deliverable_columns
    assert "supersedes_review_request_id" in review_columns
    assert "superseded_by_review_request_id" in review_columns
    assert "retry_reason" in review_columns
    assert "retry_requested_by" in review_columns
    assert "criteria_snapshot_json" in review_columns
    assert "required_outcome_kind_definitions_json" in review_columns
    assert {
        "requested_by_agent_id",
        "requested_by_actor",
        "block_kind",
        "retired_at",
        "retired_by",
        "retirement_reason",
    } <= review_columns
    assert "required_outcome_kinds_json" in review_profile_columns
    assert "gate_failure_json" in release_columns
    assert "summary" in release_columns
    assert "owner_agent_id" in release_columns
    assert "team_dispatch_activity" in tables
    assert "team_idempotency_records" in tables
    assert "team_outbox" in tables
    assert metadata["sqlite_schema_version"] == str(SQLITE_SCHEMA_VERSION)


def test_repository_migrates_task_release_and_promotion_git_base_snapshots(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id="active-context-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active context",
        description="Backfill from its work context.",
        created_by_agent_id="lead",
    )
    admin.create_git_work_context_with_id(
        context_id="ctx-active-migration",
        agent_id="backend",
        task_id="active-context-task",
        base_branch="main",
        base_commit="base-active",
        agent_branch="agents/session-backend/active-context-task",
    )
    admin.create_or_reprioritize_task(
        task_id="active-default-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active default",
        description="Backfill from the project default.",
        created_by_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="terminal-history-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Terminal history",
        description="Remain nullable for compatibility.",
        created_by_agent_id="lead",
    )
    admin.update_task(task_id="terminal-history-task", state=TeamTaskState.FINISHED)
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="release-main-task",
        deliverable_id="release-main-deliverable",
        git_base_branch="main",
        base_commit_sha="base-main",
        branch_kind="feature",
    )
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="release-feature-task",
        deliverable_id="release-feature-deliverable",
        git_base_branch="feature/foo",
        base_commit_sha="base-feature",
        branch_kind="feature",
    )

    def _seed_legacy_records(state):
        state.release_packages["release-compatible"] = ReleasePackage(
            id="release-compatible",
            project_id="project",
            deliverable_ids=["release-main-deliverable"],
            feature_branches=["team/team/features/release-main-task-backend"],
            policy_version=2,
            state=TeamRecordState.PENDING,
        )
        state.release_packages["release-ambiguous"] = ReleasePackage(
            id="release-ambiguous",
            project_id="project",
            deliverable_ids=[
                "release-main-deliverable",
                "release-feature-deliverable",
            ],
            feature_branches=[
                "team/team/features/release-main-task-backend",
                "team/team/features/release-feature-task-backend",
            ],
            policy_version=2,
            state=TeamRecordState.PENDING,
        )
        state.release_packages["release-terminal"] = ReleasePackage(
            id="release-terminal",
            project_id="project",
            deliverable_ids=["release-main-deliverable"],
            feature_branches=["team/team/features/release-main-task-backend"],
            state=TeamRecordState.ACCEPTED,
        )
        state.git_promotions["promotion-compatible"] = GitPromotionRecord(
            id="promotion-compatible",
            project_id="project",
            release_package_id="release-compatible",
            task_id="release-main-task",
            actor_agent_id="lead",
            source_branch="team/team/features/release-main-task-backend",
            target_branch="team/team/releases/release-compatible",
            promotion_kind="release",
        )
        return state

    repo.update(_seed_legacy_records)
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE team_tasks SET git_base_branch = NULL")
        connection.execute("UPDATE team_release_packages SET git_base_branch = NULL")
        connection.execute(
            "UPDATE team_git_promotions SET git_base_branch = NULL, base_commit_sha = NULL"
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = '43' WHERE key = 'sqlite_schema_version'"
        )

    migrated = TeamStateRepository(tmp_path).read()

    assert migrated.tasks["active-context-task"].git_base_branch == "main"
    assert migrated.tasks["active-default-task"].git_base_branch == "main"
    assert migrated.tasks["terminal-history-task"].git_base_branch is None
    compatible = migrated.release_packages["release-compatible"]
    assert compatible.git_base_branch == "main"
    assert compatible.base_commit_sha == "base-main"
    ambiguous = migrated.release_packages["release-ambiguous"]
    assert ambiguous.state == TeamRecordState.BLOCKED
    assert ambiguous.git_base_branch is None
    assert ambiguous.error == (
        "mixed_git_base_branches: legacy active release has ambiguous Git base provenance"
    )
    assert migrated.release_packages["release-terminal"].git_base_branch is None
    promotion = migrated.git_promotions["promotion-compatible"]
    assert promotion.git_base_branch == "main"
    assert promotion.base_commit_sha == "base-main"


def test_repository_repairs_legacy_correction_base_and_blocks_conflicting_task(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id="legacy-correction-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Correct rejected candidate",
        description="This pre-v44 task incorrectly inherited main.",
        created_by_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="legacy-conflicting-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Conflicting legacy bases",
        description="Require explicit provenance repair.",
        created_by_agent_id="lead",
    )

    release_branch = "team/team/releases/rejected-candidate"
    now = datetime(2026, 8, 2, tzinfo=UTC)

    def _seed_legacy_links(state):
        state.release_packages["release-rejected-candidate"] = ReleasePackage(
            id="release-rejected-candidate",
            project_id="project",
            release_branch=release_branch,
            git_base_branch="main",
            candidate_commit_sha="c" * 40,
            candidate_tree_hash="d" * 40,
            policy_version=2,
            state=TeamRecordState.REJECTED,
        )
        state.human_review_requests["human-review-rejected-candidate"] = (
            HumanReviewRequest(
                id="human-review-rejected-candidate",
                release_package_id="release-rejected-candidate",
                candidate_commit_sha="c" * 40,
                candidate_tree_hash="d" * 40,
                requirement_snapshot=HumanReviewRequirementSnapshot(
                    directory_path="root",
                    configured_group_ids=["release-reviewers"],
                    initially_eligible_user_ids=["reviewer"],
                ),
                state="changes_requested",
                completed_at=now,
            )
        )
        state.human_review_findings["finding-rejected-candidate"] = (
            HumanReviewFinding(
                id="finding-rejected-candidate",
                source_human_review_request_id="human-review-rejected-candidate",
                text="Repair the rejected candidate.",
                correction_task_id="legacy-correction-task",
            )
        )
        state.git_work_contexts["ctx-conflict-main"] = GitWorkContext(
            id="ctx-conflict-main",
            agent_id="backend",
            task_id="legacy-conflicting-task",
            base_branch="main",
            base_commit="1" * 40,
            agent_branch="agents/session-backend/conflict-main",
        )
        state.git_work_contexts["ctx-conflict-feature"] = GitWorkContext(
            id="ctx-conflict-feature",
            agent_id="backend",
            task_id="legacy-conflicting-task",
            base_branch="feature/legacy",
            base_commit="2" * 40,
            agent_branch="agents/session-backend/conflict-feature",
        )
        return state

    repo.update(_seed_legacy_links)
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(db_path) as connection:
        # Simulate the flawed v44 result plus one preparation attempt: both the
        # unprepared correction and the conflicting task were assigned main.
        connection.execute(
            "UPDATE team_tasks SET git_base_branch = 'main' WHERE id = ?",
            ("legacy-conflicting-task",),
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = '44' WHERE key = 'sqlite_schema_version'"
        )

    migrated = TeamStateRepository(tmp_path).read()

    assert migrated.tasks["legacy-correction-task"].git_base_branch == release_branch
    conflicting = migrated.tasks["legacy-conflicting-task"]
    assert conflicting.git_base_branch == "main"
    assert conflicting.state == TeamTaskState.BLOCKED
    blockers = [
        blocker
        for blocker in migrated.blockers.values()
        if blocker.reason_code == "mixed_git_base_branches"
        and conflicting.id in blocker.affected_task_ids
    ]
    assert len(blockers) == 1
    assert blockers[0].route == TeamBlockerRoute.OPERATOR


def test_repository_migrates_active_correction_successor_from_predecessor_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="legacy-source-task",
        deliverable_id="legacy-source-deliverable",
        git_base_branch="main",
        base_commit_sha="1" * 40,
        branch_kind="feature",
    )
    rejected_branch = "team/team/releases/legacy-rejected"
    rejected_commit = "2" * 40
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="legacy-correction-input-task",
        deliverable_id="legacy-correction-deliverable",
        git_base_branch=rejected_branch,
        base_commit_sha=rejected_commit,
        branch_kind="feature",
    )

    def _seed_successor(state):
        state.release_packages["legacy-rejected"] = ReleasePackage(
            id="legacy-rejected",
            project_id="project",
            deliverable_ids=["legacy-source-deliverable"],
            release_branch=rejected_branch,
            canonical_branch="main",
            git_base_branch="main",
            base_commit_sha="1" * 40,
            candidate_commit_sha=rejected_commit,
            candidate_tree_hash="3" * 40,
            policy_version=2,
            state=TeamRecordState.REJECTED,
        )
        state.release_packages["legacy-successor"] = ReleasePackage(
            id="legacy-successor",
            project_id="project",
            supersedes_release_package_id="legacy-rejected",
            canonical_branch="main",
            deliverable_ids=[
                "legacy-source-deliverable",
                "legacy-correction-deliverable",
            ],
            policy_version=2,
            state=TeamRecordState.PENDING,
        )
        state.git_promotions["legacy-canonical-push"] = GitPromotionRecord(
            id="legacy-canonical-push",
            project_id="project",
            release_package_id="legacy-successor",
            actor_agent_id="lead",
            source_branch="team/team/releases/legacy-successor",
            target_branch="main",
            promotion_kind="canonical_push",
            base_commit_sha=rejected_commit,
        )
        state.release_packages["legacy-successor"].git_promotion_record_ids = [
            "legacy-canonical-push"
        ]
        return state

    repo.update(_seed_successor)
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    migration_error = (
        "mixed_git_base_branches: legacy active release has ambiguous Git base provenance"
    )
    with sqlite3.connect(db_path) as connection:
        # Simulate v44 having mixed inherited and correction deliverables and
        # incorrectly blocked the otherwise valid successor.
        connection.execute(
            """
            UPDATE team_release_packages
            SET git_base_branch = NULL,
                base_commit_sha = NULL,
                canonical_base_commit_sha = NULL,
                state = 'blocked',
                error = ?
            WHERE id = 'legacy-successor'
            """,
            (migration_error,),
        )
        connection.execute(
            """
            UPDATE team_release_packages
            SET canonical_base_commit_sha = NULL
            WHERE id = 'legacy-rejected'
            """
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = '44' WHERE key = 'sqlite_schema_version'"
        )

    migrated = TeamStateRepository(tmp_path).read()
    successor = migrated.release_packages["legacy-successor"]

    assert successor.git_base_branch == rejected_branch
    assert successor.base_commit_sha == rejected_commit
    assert successor.canonical_base_commit_sha == "1" * 40
    assert successor.state == TeamRecordState.PENDING
    assert successor.error is None
    assert migrated.git_promotions["legacy-canonical-push"].base_commit_sha == (
        "1" * 40
    )


def test_repository_migrates_legacy_task_and_deliverable_outcome_kinds(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_outcome_kind(repo, "legacy_action")
    _seed_outcome_kind(repo, "legacy_report")
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-create-legacy-outcome-task",
    )
    state, created = _command_service(repo).create_task_command(
        session_id="session-lead",
        task_id="legacy-outcome-task",
        owner_agent_id="backend",
        project_id="project",
        title="Create the legacy action",
        description="Exercise the pre-catalog persisted shape.",
        task_kind="outcome",
        outcome_contract={
            "required_outcome_kinds": ["legacy_action"],
            "forbidden_outcome_kinds": ["legacy_report"],
        },
        request_id="request-create-legacy-outcome-task",
    )
    assert created["success"] is True
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-legacy-outcome",
    )
    state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="legacy-outcome-task",
        summary="Created the legacy action.",
        outcomes=[
            {
                "kind": "legacy_action",
                "reference_ids": ["legacy-action:123"],
            }
        ],
        request_id="request-submit-legacy-outcome",
    )
    assert submitted["success"] is True
    deliverable_id = submitted["deliverable_id"]

    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(db_path) as connection:
        task_contract = json.loads(
            connection.execute(
                "SELECT outcome_contract_json FROM team_tasks WHERE id = ?",
                ("legacy-outcome-task",),
            ).fetchone()[0]
        )
        task_contract.pop("outcome_kind_definitions", None)
        task_contract["forbidden_effect_kinds"] = task_contract.pop(
            "forbidden_outcome_kinds"
        )
        outcomes = json.loads(
            connection.execute(
                "SELECT outcomes_json FROM team_deliverables WHERE id = ?",
                (deliverable_id,),
            ).fetchone()[0]
        )
        for outcome in outcomes:
            outcome.pop("definition_snapshot", None)
        connection.execute("DELETE FROM team_outcome_kind_definitions")
        connection.execute(
            "UPDATE team_tasks SET outcome_contract_json = ? WHERE id = ?",
            (json.dumps(task_contract), "legacy-outcome-task"),
        )
        connection.execute(
            "UPDATE team_deliverables SET outcomes_json = ? WHERE id = ?",
            (json.dumps(outcomes), deliverable_id),
        )
        connection.execute(
            """
            UPDATE team_state_metadata
            SET value = '30'
            WHERE key = 'sqlite_schema_version'
            """
        )

    migrated = TeamStateRepository(tmp_path).read()

    assert {
        definition.kind
        for definition in migrated.outcome_kind_definitions.values()
    } == {"legacy_action", "legacy_report"}
    contract = migrated.tasks["legacy-outcome-task"].outcome_contract
    assert contract is not None
    assert contract.forbidden_outcome_kinds == ["legacy_report"]
    assert {
        definition.kind for definition in contract.outcome_kind_definitions
    } == {"legacy_action", "legacy_report"}
    assert (
        migrated.deliverables[deliverable_id]
        .outcomes[0]
        .definition_snapshot.kind
        == "legacy_action"
    )


def test_repository_migrates_team_review_snapshot_with_reviewed_project_override(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    state = admin.create_outcome_kind_definition(
        definition_id="outcome-kind-team-prepared-action",
        team_id="team",
        project_id=None,
        kind="prepared_action",
        name="Team prepared action",
        description="Team-wide definition.",
        expected_revision=repo.read().revision,
    )
    state = admin.create_outcome_kind_definition(
        definition_id="outcome-kind-project-prepared-action",
        team_id="team",
        project_id="project",
        kind="prepared_action",
        name="Project prepared action",
        description="Project-specific definition.",
        expected_revision=state.revision,
    )
    _seed_task(repo, task_id="project-outcome-review-task")
    state = admin.record_deliverable(
        deliverable_id="project-outcome-review-deliverable",
        task_id="project-outcome-review-task",
        producer_agent_id="backend",
        summary="Prepared the project action.",
        outcomes=[
            {
                "kind": "prepared_action",
                "reference_ids": ["prepared-action:project"],
            }
        ],
        expected_revision=repo.read().revision,
    )
    state = admin.create_review_profile(
        profile_id="team-outcome-review-profile",
        team_id="team",
        name="Team outcome review",
        required_outcome_kinds=["prepared_action"],
        expected_revision=state.revision,
    )
    state = admin.record_review_request(
        review_request_id="project-outcome-review",
        deliverable_id="project-outcome-review-deliverable",
        reviewer_agent_id="worker",
        review_profile_id="team-outcome-review-profile",
        expected_revision=state.revision,
    )
    assert (
        state.review_requests["project-outcome-review"]
        .required_outcome_kind_definitions[0]
        .name
        == "Project prepared action"
    )

    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE team_review_requests
            SET required_outcome_kind_definitions_json = '[]'
            WHERE id = 'project-outcome-review'
            """
        )
        connection.execute(
            """
            UPDATE team_state_metadata
            SET value = '30'
            WHERE key = 'sqlite_schema_version'
            """
        )

    migrated = TeamStateRepository(tmp_path).read()

    assert (
        migrated.review_requests["project-outcome-review"]
        .required_outcome_kind_definitions[0]
        .name
        == "Project prepared action"
    )


def test_repository_migrates_global_chat_conversation_unique_constraint(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE team_state_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO team_state_metadata(key, value)
            VALUES ('sqlite_schema_version', '11');
            CREATE TABLE team_conversations (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                last_message_preview TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_interaction_at TEXT NOT NULL
            );
            """
        )

    TeamStateRepository(tmp_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(team_conversations)")
        }
        unique_indexes = [
            str(row["name"])
            for row in connection.execute("PRAGMA index_list(team_conversations)")
            if int(row["unique"] or 0)
        ]
        unique_index_columns = [
            [
                str(info_row["name"])
                for info_row in connection.execute(f"PRAGMA index_info({index_name})")
            ]
            for index_name in unique_indexes
        ]

    assert "operator_user_id" in columns
    assert "operator_display_name" in columns
    assert ["agent_id"] not in unique_index_columns
    assert ["agent_id", "operator_user_id"] in unique_index_columns


def test_human_finding_and_post_release_case_commands_are_supported(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 2, tzinfo=UTC)

    def _seed(state):
        state.release_packages["human-release"] = ReleasePackage(
            id="human-release",
            project_id="project",
            release_branch="team/team/releases/human-release",
            candidate_commit_sha="a" * 40,
            candidate_tree_hash="b" * 40,
        )
        state.human_review_requests["human-request"] = HumanReviewRequest(
            id="human-request",
            release_package_id="human-release",
            candidate_commit_sha="a" * 40,
            candidate_tree_hash="b" * 40,
            requirement_snapshot=HumanReviewRequirementSnapshot(
                directory_path="root",
                configured_group_ids=["release-reviewers"],
                initially_eligible_user_ids=["reviewer"],
            ),
            state="changes_requested",
            completed_at=now,
        )
        state.cases["human-case"] = TeamCase(
            id="human-case",
            team_id="team",
            project_id="project",
            title="Human review corrections",
            status=TeamCaseStatus.IN_PROGRESS,
            owner_agent_id="lead",
            source="human_release_review",
            source_ref="human-request",
            metadata={
                "human_correction_aggregation_task_id": "human-aggregation",
                "human_correction_ambiguous_finding_ids": ["human-finding"],
            },
            task_ids=["human-aggregation", "human-correction"],
        )
        state.tasks["human-aggregation"] = AgentTask(
            id="human-aggregation",
            owner_agent_id="lead",
            project_id="project",
            case_id="human-case",
            priority=0,
            state=TeamTaskState.PENDING,
            task_kind=TeamTaskKind.SUPPORT,
            title="Coordinate human review corrections",
            description="Track correction children.",
            created_by_actor="system:human-review",
        )
        state.tasks["human-correction"] = AgentTask(
            id="human-correction",
            owner_agent_id="lead",
            project_id="project",
            case_id="human-case",
            parent_task_id="human-aggregation",
            priority=0,
            state=TeamTaskState.PENDING,
            task_kind=TeamTaskKind.SUPPORT,
            title="Release-wide behavior needs correction.",
            description="Release-wide behavior needs correction.",
            created_by_actor="system:human-review",
        )
        state.human_review_findings["human-finding"] = HumanReviewFinding(
            id="human-finding",
            source_human_review_request_id="human-request",
            text="Release-wide behavior needs correction.",
            correction_task_id="human-correction",
        )
        state.cases["post-release-case"] = TeamCase(
            id="post-release-case",
            team_id="team",
            project_id="project",
            title="Post-release report",
            status=TeamCaseStatus.IN_PROGRESS,
            owner_agent_id="lead",
            source="post_release_feedback",
            source_ref="published-release:report-1",
            metadata={
                "release_package_id": "published-release",
                "evidence_refs": ["artifact:report"],
            },
            task_ids=["post-release-triage"],
        )
        state.tasks["post-release-triage"] = AgentTask(
            id="post-release-triage",
            owner_agent_id="lead",
            project_id="project",
            case_id="post-release-case",
            priority=0,
            state=TeamTaskState.FINISHED,
            task_kind=TeamTaskKind.SUPPORT,
            title="Triage post-release report",
            description="The report was verified and corrected.",
            created_by_actor="system:post-release-feedback",
        )
        return state

    repo.update(_seed)
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-human-finding",
    )
    service = _command_service(repo)

    state, created = service.route_human_finding_correction_command(
        session_id="session-lead",
        finding_id="human-finding",
        owner_agent_id="backend",
        request_id="route-human-correction",
    )
    correction_task_id = created["correction_task_id"]
    assert correction_task_id == "human-correction"
    assert state.tasks[correction_task_id].parent_task_id == "human-aggregation"
    assert state.tasks[correction_task_id].case_id == "human-case"
    assert (
        state.human_review_findings["human-finding"].correction_task_id
        == correction_task_id
    )
    assert state.human_review_findings["human-finding"].target_scope.value == (
        "release_candidate"
    )

    replay_state, replay = (
        service.route_human_finding_correction_command(
            session_id="session-lead",
            finding_id="human-finding",
            owner_agent_id="backend",
            request_id="route-human-correction",
        )
    )
    assert replay == created
    assert replay_state.revision == state.revision

    def _accept_correction(current):
        task = current.tasks[correction_task_id]
        task.state = TeamTaskState.FINISHED
        task.deliverable_ids.append("human-correction-deliverable")
        current.deliverables["human-correction-deliverable"] = Deliverable(
            id="human-correction-deliverable",
            task_id=correction_task_id,
            producer_agent_id="backend",
            summary="Corrected release-wide behavior.",
            state=TeamRecordState.ACCEPTED,
        )
        return current

    repo.update(_accept_correction)
    state, proposed = service.propose_human_finding_resolution_command(
        session_id="session-lead",
        finding_id="human-finding",
        proposed_resolution="The verified safe-state behavior is restored.",
        evidence_refs=["test:safe-state"],
        proposed_by_deliverable_ids=["human-correction-deliverable"],
        request_id="propose-human-resolution",
    )
    assert proposed["human_review_finding_id"] == "human-finding"
    assert state.human_review_findings[
        "human-finding"
    ].proposed_by_deliverable_ids == ["human-correction-deliverable"]

    before_close_revision = state.revision
    rejected_state, rejected = service.update_case_command(
        session_id="session-lead",
        case_id="post-release-case",
        status=TeamCaseStatus.CLOSED.value,
        request_id="generic-close-post-release-case",
    )
    assert rejected["success"] is False
    assert "evidence-backed" in rejected["errors"][0]["message"]
    assert rejected_state.revision == before_close_revision
    assert rejected_state.cases["post-release-case"].status == (
        TeamCaseStatus.IN_PROGRESS
    )

    state, closed = service.close_post_release_feedback_case_command(
        session_id="session-lead",
        case_id="post-release-case",
        reason="The correction was verified in production.",
        evidence_refs=["monitoring:recovery"],
        request_id="close-post-release-case",
    )
    assert closed["case"]["status"] == "closed"
    assert state.cases["post-release-case"].metadata[
        "post_release_feedback_resolution"
    ]["evidence_refs"] == ["monitoring:recovery"]


def test_post_release_case_generic_updates_preserve_closure_invariants(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _seed(state):
        state.cases["post-release-case"] = TeamCase(
            id="post-release-case",
            team_id="team",
            project_id="project",
            title="Post-release report",
            status=TeamCaseStatus.IN_PROGRESS,
            owner_agent_id="lead",
            source="post_release_feedback",
            source_ref="published-release:report-1",
            metadata={
                "release_package_id": "published-release",
                "reporter_user_id": "reporter",
                "reporter_display_name": "Reporter",
                "evidence_refs": ["artifact:report"],
            },
            task_ids=["post-release-triage"],
        )
        state.tasks["post-release-triage"] = AgentTask(
            id="post-release-triage",
            owner_agent_id="lead",
            project_id="project",
            case_id="post-release-case",
            priority=0,
            state=TeamTaskState.PENDING,
            task_kind=TeamTaskKind.SUPPORT,
            title="Triage post-release report",
            description="Investigate and correct the reported regression.",
            created_by_actor="system:post-release-feedback",
        )
        return state

    repo.update(_seed)
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-protected-case",
    )
    service = _command_service(repo)

    state, source_bypass = service.update_case_command(
        session_id="session-lead",
        case_id="post-release-case",
        source="manual",
        status=TeamCaseStatus.CLOSED.value,
        request_id="bypass-protected-case-source",
    )

    assert source_bypass["success"] is False
    assert "source is immutable" in source_bypass["errors"][0]["message"]
    assert state.cases["post-release-case"].source == "post_release_feedback"
    assert state.cases["post-release-case"].status == TeamCaseStatus.IN_PROGRESS

    state, task_bypass = service.update_case_command(
        session_id="session-lead",
        case_id="post-release-case",
        task_ids=[],
        request_id="bypass-protected-case-task",
    )

    assert task_bypass["success"] is False
    assert "cannot remove unresolved tasks" in task_bypass["errors"][0]["message"]
    assert state.cases["post-release-case"].task_ids == ["post-release-triage"]
    assert state.tasks["post-release-triage"].case_id == "post-release-case"

    with pytest.raises(ValueError, match="provenance metadata is immutable"):
        _team_admin_service(repo).update_case(
            case_id="post-release-case",
            metadata={
                **state.cases["post-release-case"].metadata,
                "release_package_id": "different-release",
            },
            expected_revision=state.revision,
        )

    persisted = repo.read()
    assert persisted.cases["post-release-case"].metadata["release_package_id"] == (
        "published-release"
    )
    assert persisted.cases["post-release-case"].task_ids == ["post-release-triage"]


def test_generic_case_paths_reject_reserved_post_release_source(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-reserved-case-source",
    )
    service = _command_service(repo)

    state, created = service.create_case_command(
        session_id="session-lead",
        case_id="fabricated-post-release",
        project_id="project",
        title="Fabricated report",
        source="post_release_feedback",
        source_ref="release:request",
        request_id="create-fabricated-post-release",
    )

    assert created["success"] is False
    assert "reserved" in created["errors"][0]["message"]
    assert "fabricated-post-release" not in state.cases

    with pytest.raises(ValueError, match="reserved"):
        _team_admin_service(repo).create_case(
            case_id="admin-fabricated-post-release",
            team_id="team",
            project_id="project",
            title="Admin fabricated report",
            source="post_release_feedback",
            source_ref="release:request",
            metadata={"release_package_id": "release"},
        )

    state = _team_admin_service(repo).create_case(
        case_id="ordinary-case",
        team_id="team",
        project_id="project",
        title="Ordinary case",
        source="manual",
    )
    state, converted = service.update_case_command(
        session_id="session-lead",
        case_id="ordinary-case",
        source="post_release_feedback",
        source_ref="release:request",
        request_id="convert-to-fabricated-post-release",
    )

    assert converted["success"] is False
    assert "reserved" in converted["errors"][0]["message"]
    assert state.cases["ordinary-case"].source == "manual"
    assert state.cases["ordinary-case"].source_ref is None


def test_repository_migrates_legacy_chat_operator_ids_to_lowercase() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE team_conversations (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                last_message_preview TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_interaction_at TEXT NOT NULL
            );
            INSERT INTO team_conversations(
                id, agent_id, created_by, last_message_preview,
                created_at, updated_at, last_interaction_at
            )
            VALUES (
                'conversation-backend',
                'backend',
                'operator:Alice@Example.com',
                '',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'
            );
            """
        )

        _ensure_team_conversations_operator_scope(connection)
        row = connection.execute(
            """
            SELECT operator_user_id
            FROM team_conversations
            WHERE id = 'conversation-backend'
            """
        ).fetchone()

    assert row["operator_user_id"] == "alice@example.com"


def test_repository_normalizes_existing_chat_operator_ids_to_lowercase() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE team_conversations (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                operator_user_id TEXT NOT NULL,
                operator_display_name TEXT,
                created_by TEXT,
                last_message_preview TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_interaction_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_team_conversations_agent_operator
            ON team_conversations(agent_id, operator_user_id);
            INSERT INTO team_conversations(
                id, agent_id, operator_user_id, operator_display_name, created_by,
                last_message_preview, created_at, updated_at, last_interaction_at
            )
            VALUES (
                'conversation-backend',
                'backend',
                'Jonathan',
                'Jonathan',
                'operator:Jonathan',
                '',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'
            );
            """
        )

        _ensure_team_conversations_operator_scope(connection)
        row = connection.execute(
            """
            SELECT operator_user_id, operator_display_name, created_by
            FROM team_conversations
            WHERE id = 'conversation-backend'
            """
        ).fetchone()

    assert row["operator_user_id"] == "jonathan"
    assert row["operator_display_name"] == "Jonathan"
    assert row["created_by"] == "operator:Jonathan"


def test_repository_imports_legacy_json_state_once(tmp_path: Path) -> None:
    legacy_path = tmp_path / "addons" / "team-addon" / "state.json"
    legacy_path.parent.mkdir(parents=True)
    state = _seed_hierarchy(tmp_path / "seed").read()
    legacy_payload = state.model_dump(mode="json")
    legacy_payload["schema_version"] = 2
    legacy_path.write_text(
        json.dumps(legacy_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    repo = TeamStateRepository(tmp_path)

    imported = repo.read()
    assert imported.revision == state.revision
    assert imported.schema_version == SCHEMA_VERSION
    assert imported.teams.keys() == state.teams.keys()
    assert imported.projects.keys() == state.projects.keys()
    assert legacy_path.is_file()
    with sqlite3.connect(tmp_path / "addons" / "team-addon" / "state.db") as connection:
        metadata = dict(connection.execute("SELECT key, value FROM team_state_metadata").fetchall())
    assert metadata["legacy_json_path"] == str(legacy_path)
    assert len(metadata["legacy_json_sha256"]) == 64
    assert metadata["state_schema_version"] == str(SCHEMA_VERSION)


def test_repository_repairs_rejection_override_during_legacy_json_import(
    tmp_path: Path,
) -> None:
    seed_repo = _seed_hierarchy(tmp_path / "seed")
    _seed_task(seed_repo, task_id="task")
    _seed_deliverable(
        seed_repo,
        deliverable_id="deliverable-legacy-json-override",
        task_id="task",
    )
    admin = _team_admin_service(seed_repo)
    admin.record_review_request(
        review_request_id="review-legacy-json-override",
        deliverable_id="deliverable-legacy-json-override",
        reviewer_agent_id="worker",
        expected_revision=seed_repo.read().revision,
    )
    admin.record_review_decision(
        review_request_id="review-legacy-json-override",
        state=TeamRecordState.ACCEPTED,
        expected_revision=seed_repo.read().revision,
    )
    legacy_payload = seed_repo.read().model_dump(mode="json")
    legacy_payload["schema_version"] = SCHEMA_VERSION - 1
    legacy_deliverable = legacy_payload["deliverables"][
        "deliverable-legacy-json-override"
    ]
    legacy_deliverable["acceptance_recipient_agent_id"] = "lead"
    legacy_deliverable["acceptance_decision"] = {
        "decision": "rejected",
        "decided_by_agent_id": "lead",
        "decided_at": "2026-08-01T12:00:00+00:00",
        "reason": "The imported direct handoff requires rework.",
    }
    legacy_path = tmp_path / "addons" / "team-addon" / "state.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps(legacy_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    imported = TeamStateRepository(tmp_path).read()

    assert imported.deliverables["deliverable-legacy-json-override"].state == (
        TeamRecordState.REJECTED
    )
    assert imported.tasks["task"].state == TeamTaskState.IN_PROGRESS
    review = imported.review_requests["review-legacy-json-override"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.retired_by == f"system:sqlite-migration:v{SQLITE_SCHEMA_VERSION}"
    assert any(
        item.state == TeamInboxItemState.PENDING
        and item.payload.get("kind") == "superior_acceptance_rejected"
        and item.payload.get("deliverable_id")
        == "deliverable-legacy-json-override"
        for item in imported.inbox.values()
    )


def test_repository_backfills_git_base_snapshots_after_legacy_json_import(
    tmp_path: Path,
) -> None:
    seed_repo = _seed_hierarchy(tmp_path / "seed")
    _set_project_git(seed_repo)
    _seed_context_backed_accepted_git_deliverable(
        seed_repo,
        task_id="legacy-json-git-task",
        deliverable_id="legacy-json-git-deliverable",
        git_base_branch="main",
        base_commit_sha="4" * 40,
        branch_kind="feature",
    )

    def _seed_release_and_promotion(state):
        state.release_packages["legacy-json-release"] = ReleasePackage(
            id="legacy-json-release",
            project_id="project",
            deliverable_ids=["legacy-json-git-deliverable"],
            feature_branches=[
                "team/team/features/legacy-json-git-task-backend"
            ],
            policy_version=2,
            state=TeamRecordState.PENDING,
        )
        state.git_promotions["legacy-json-promotion"] = GitPromotionRecord(
            id="legacy-json-promotion",
            project_id="project",
            release_package_id="legacy-json-release",
            task_id="legacy-json-git-task",
            actor_agent_id="lead",
            source_branch="team/team/features/legacy-json-git-task-backend",
            target_branch="team/team/releases/legacy-json-release",
            promotion_kind="release",
        )
        return state

    state = seed_repo.update(_seed_release_and_promotion)
    payload = state.model_dump(mode="json")
    payload["tasks"]["legacy-json-git-task"]["state"] = "in_progress"
    payload["tasks"]["legacy-json-git-task"]["git_base_branch"] = None
    payload["release_packages"]["legacy-json-release"]["git_base_branch"] = None
    payload["release_packages"]["legacy-json-release"]["base_commit_sha"] = None
    payload["git_promotions"]["legacy-json-promotion"]["git_base_branch"] = None
    payload["git_promotions"]["legacy-json-promotion"]["base_commit_sha"] = None
    legacy_path = tmp_path / "addons" / "team-addon" / "state.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    imported = TeamStateRepository(tmp_path).read()

    assert imported.tasks["legacy-json-git-task"].git_base_branch == "main"
    release = imported.release_packages["legacy-json-release"]
    assert release.git_base_branch == "main"
    assert release.base_commit_sha == "4" * 40
    promotion = imported.git_promotions["legacy-json-promotion"]
    assert promotion.git_base_branch == "main"
    assert promotion.base_commit_sha == "4" * 40


def test_repository_imports_legacy_json_outcome_records_before_validation(
    tmp_path: Path,
) -> None:
    seed_repo = _seed_hierarchy(tmp_path / "seed")
    admin = _team_admin_service(seed_repo)
    state = admin.create_outcome_kind_definition(
        definition_id="legacy-team-action",
        team_id="team",
        project_id=None,
        kind="legacy_action",
        name="Legacy action",
        expected_revision=seed_repo.read().revision,
    )
    state = admin.create_outcome_kind_definition(
        definition_id="legacy-team-report",
        team_id="team",
        project_id=None,
        kind="legacy_report",
        name="Legacy report",
        expected_revision=state.revision,
    )
    state = admin.create_or_reprioritize_task(
        task_id="legacy-json-outcome-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Produce a legacy action",
        description="Exercise the supported pre-catalog JSON shape.",
        created_by_agent_id="lead",
        task_kind=TeamTaskKind.OUTCOME,
        outcome_contract={
            "required_outcome_kinds": ["legacy_action"],
            "forbidden_outcome_kinds": ["legacy_report"],
        },
        expected_revision=state.revision,
    )
    state = admin.record_deliverable(
        deliverable_id="legacy-json-outcome-deliverable",
        task_id="legacy-json-outcome-task",
        producer_agent_id="backend",
        summary="Created the legacy action.",
        outcomes=[
            {
                "kind": "legacy_action",
                "reference_ids": ["legacy-action:json"],
            }
        ],
        expected_revision=state.revision,
    )
    state = admin.create_review_profile(
        profile_id="legacy-json-review-profile",
        team_id="team",
        name="Legacy JSON review",
        required_outcome_kinds=["legacy_action"],
        expected_revision=state.revision,
    )
    state = admin.record_review_request(
        review_request_id="legacy-json-review",
        deliverable_id="legacy-json-outcome-deliverable",
        reviewer_agent_id="worker",
        review_profile_id="legacy-json-review-profile",
        expected_revision=state.revision,
    )
    payload = state.model_dump(mode="json")
    contract = payload["tasks"]["legacy-json-outcome-task"]["outcome_contract"]
    contract.pop("outcome_kind_definitions")
    contract["forbidden_effect_kinds"] = contract.pop(
        "forbidden_outcome_kinds"
    )
    payload["deliverables"]["legacy-json-outcome-deliverable"]["outcomes"][0].pop(
        "definition_snapshot"
    )
    payload["review_requests"]["legacy-json-review"][
        "required_outcome_kind_definitions"
    ] = []
    payload["outcome_kind_definitions"] = {}

    legacy_path = tmp_path / "addons" / "team-addon" / "state.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    imported = TeamStateRepository(tmp_path).read()

    imported_contract = imported.tasks[
        "legacy-json-outcome-task"
    ].outcome_contract
    assert imported_contract is not None
    assert imported_contract.forbidden_outcome_kinds == ["legacy_report"]
    assert {
        definition.kind
        for definition in imported_contract.outcome_kind_definitions
    } == {"legacy_action", "legacy_report"}
    assert (
        imported.deliverables["legacy-json-outcome-deliverable"]
        .outcomes[0]
        .definition_snapshot.kind
        == "legacy_action"
    )
    assert (
        imported.review_requests["legacy-json-review"]
        .required_outcome_kind_definitions[0]
        .kind
        == "legacy_action"
    )
    assert {
        definition.kind
        for definition in imported.outcome_kind_definitions.values()
    } == {"legacy_action", "legacy_report"}


def test_task_state_entered_at_changes_only_on_state_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    moments = iter(
        [
            datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 0, 1, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 1, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 1, 1, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 2, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 2, 1, tzinfo=UTC),
        ]
    )

    def fake_now() -> datetime:
        return next(moments)

    monkeypatch.setattr(team_domain_common, "utc_now", fake_now)
    monkeypatch.setattr("evander_team_addon.repository.utc_now", fake_now)

    state = _team_admin_service(repo).create_or_reprioritize_task(
        task_id="board-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build board",
        description="Add a task board.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    original_entered_at = state.tasks["board-task"].state_entered_at
    assert original_entered_at == datetime(2026, 6, 21, 10, 0, tzinfo=UTC)

    state = _team_admin_service(repo).update_task(
        task_id="board-task",
        title="Build project board",
        expected_revision=state.revision,
    )
    assert state.tasks["board-task"].state_entered_at == original_entered_at

    state = _team_admin_service(repo).update_task(
        task_id="board-task",
        state=TeamTaskState.WAITING_FOR_REVIEW,
        expected_revision=state.revision,
    )
    assert state.tasks["board-task"].state_entered_at == datetime(
        2026,
        6,
        21,
        10,
        2,
        tzinfo=UTC,
    )


def test_repository_creates_persists_and_updates_cases(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="case-task",
        owner_agent_id="backend",
        project_id="project",
        priority=5,
        title="Investigate ticket",
        description="Reproduce the reported problem.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )

    state = _team_admin_service(repo).create_case(
        case_id="case-1",
        team_id="team",
        project_id="project",
        title="Customer ticket",
        description="External support ticket.",
        status="triage",
        priority="high",
        owner_agent_id="lead",
        source="github",
        source_ref="issue-42",
        tags=["support", "bug"],
        task_ids=["case-task"],
        expected_revision=repo.read().revision,
    )

    case = state.cases["case-1"]
    assert case.tags == ["support", "bug"]
    assert case.task_ids == ["case-task"]
    assert state.tasks["case-task"].case_id == "case-1"

    reread = TeamStateRepository(tmp_path).read()
    assert reread.cases["case-1"].source_ref == "issue-42"
    assert reread.tasks["case-task"].case_id == "case-1"

    state = _team_admin_service(repo).update_case(
        case_id="case-1",
        status="closed",
        owner_agent_id=None,
        set_owner_agent_id=True,
        task_ids=[],
        expected_revision=state.revision,
    )

    assert state.cases["case-1"].status == "closed"
    assert state.cases["case-1"].owner_agent_id is None
    assert state.cases["case-1"].closed_at is not None
    assert state.cases["case-1"].task_ids == []
    assert state.tasks["case-task"].case_id is None


def test_repository_clears_case_description(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    state = _team_admin_service(repo).create_case(
        case_id="case-1",
        team_id="team",
        project_id="project",
        title="Customer ticket",
        description="External support ticket.",
        expected_revision=repo.read().revision,
    )

    state = _team_admin_service(repo).update_case(
        case_id="case-1",
        description="",
        expected_revision=state.revision,
    )

    assert state.cases["case-1"].description == ""
    assert TeamStateRepository(tmp_path).read().cases["case-1"].description == ""


def test_agent_case_update_errors_restore_case_and_task_state(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="case-task",
        owner_agent_id="backend",
        project_id="project",
        priority=5,
        title="Investigate ticket",
        description="Reproduce the reported problem.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_case(
        case_id="case-1",
        team_id="team",
        project_id="project",
        title="Customer ticket",
        description="External support ticket.",
        task_ids=["case-task"],
        expected_revision=repo.read().revision,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "case_updates": [
                {
                    "case_id": "case-1",
                    "title": "Changed title",
                    "task_ids": ["missing-task"],
                },
                {
                    "id": "new-case",
                    "project_id": "project",
                    "title": "New case",
                    "task_ids": ["missing-task"],
                },
            ],
        },
        expected_revision=repo.read().revision,
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert len(result["errors"]) == 2
    assert result["errors"][0] == "case_updates[0]: task missing-task does not exist"
    assert result["errors"][1] == "case_updates[1]: task missing-task does not exist"
    assert "new-case" not in state.cases
    assert state.cases["case-1"].title == "Customer ticket"
    assert state.cases["case-1"].task_ids == ["case-task"]
    assert state.tasks["case-task"].case_id == "case-1"

    reread = TeamStateRepository(tmp_path).read()
    assert "new-case" not in reread.cases
    assert reread.cases["case-1"].title == "Customer ticket"
    assert reread.cases["case-1"].task_ids == ["case-task"]
    assert reread.tasks["case-task"].case_id == "case-1"


def test_repository_rejects_cross_project_case_task_link(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_project(
        project_id="other",
        team_id="team",
        name="Other",
        lead_agent_id="lead",
        lead_session_id=None,
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_case(
        case_id="case-1",
        team_id="team",
        project_id="project",
        title="Customer ticket",
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="cannot be linked to case case-1"):
        _team_admin_service(repo).create_or_reprioritize_task(
            task_id="other-task",
            owner_agent_id="backend",
            project_id="other",
            priority=5,
            title="Wrong project",
            description="Should not link to a project-scoped case.",
            created_by_agent_id="lead",
            case_id="case-1",
            expected_revision=repo.read().revision,
        )


def test_repository_prefers_sqlite_when_legacy_json_also_exists(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="db-team",
        name="DB Team",
        manager_agent_id="db-manager",
        manager_session_id="session-db-manager",
        expected_revision=0,
    )
    legacy_path = tmp_path / "addons" / "team-addon" / "state.json"
    legacy_state = _team_admin_service(TeamStateRepository(tmp_path / "legacy")).create_team(
        team_id="json-team",
        name="JSON Team",
        manager_agent_id="json-manager",
        manager_session_id="session-json-manager",
        expected_revision=0,
    )
    legacy_path.write_text(
        json.dumps(legacy_state.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    reread = TeamStateRepository(tmp_path).read()

    assert set(reread.teams) == {"db-team"}
    assert "json-team" not in reread.teams


def test_repository_writes_increment_revision_and_preserve_hierarchy(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        manager_display_name="  Team Manager  ",
        expected_revision=0,
    )
    assert state.revision == 1
    assert state.agents["manager"].display_name == "Team Manager"
    assert state.roles["team__manager"].permissions == [RolePermission.CREATE_ROOT_TASK]

    state = _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[ProjectGoal(id="goal", description="Complete project")],
        expected_revision=1,
    )
    assert state.revision == 2
    assert state.agents["lead"].display_name == "Project Lead"
    assert state.roles["project__project-lead"].permissions == [RolePermission.CREATE_ROOT_TASK]

    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    state = _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        display_name="  Backend Agent  ",
        expected_revision=3,
    )
    assert state.revision == 4
    assert state.agents["manager"].subordinate_agent_ids == ["lead"]
    assert state.agents["lead"].subordinate_agent_ids == ["backend"]
    assert state.agents["backend"].display_name == "Backend Agent"


def test_create_project_can_assign_manager_as_project_lead_overlay(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    state = _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    manager_before = state.agents["manager"].model_copy(deep=True)

    state = _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="manager",
        lead_session_id=None,
        expected_revision=state.revision,
    )

    assert state.projects["project"].lead_agent_id == "manager"
    assert project_lead_agent_id(state, "project") == "manager"
    assert agent_project_ids(state, "manager") == ["project"]
    assert state.agents["manager"].role_id == manager_before.role_id
    assert state.agents["manager"].reports_to_agent_id == manager_before.reports_to_agent_id
    assert state.agents["manager"].session_id == manager_before.session_id
    assert "manager" not in state.agents["manager"].subordinate_agent_ids
    assert state.roles["project__project-lead"].permissions == [RolePermission.CREATE_ROOT_TASK]


def test_create_project_existing_project_lead_is_assignment_overlay(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    state = _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    state = _team_admin_service(repo).create_role(
        role_id="team__lead-candidate",
        team_id="team",
        name="Lead Candidate",
        expected_revision=state.revision,
    )
    state = _team_admin_service(repo).register_agent(
        agent_id="candidate",
        team_id="team",
        project_id=None,
        role_id="team__lead-candidate",
        session_id="session-candidate",
        reports_to_agent_id="manager",
        expected_revision=state.revision,
    )
    candidate_before = state.agents["candidate"].model_copy(deep=True)

    state = _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="candidate",
        lead_session_id=None,
        expected_revision=state.revision,
    )

    assert state.projects["project"].lead_agent_id == "candidate"
    assert project_lead_agent_id(state, "project") == "candidate"
    assert agent_project_ids(state, "candidate") == ["project"]
    assert state.agents["candidate"].role_id == candidate_before.role_id
    assert state.agents["candidate"].reports_to_agent_id == candidate_before.reports_to_agent_id
    assert state.agents["candidate"].session_id == candidate_before.session_id

    def _add_candidate_inbox(state):
        state.inbox["candidate-inbox"] = AgentInboxItem(
            id="candidate-inbox",
            recipient_agent_id="candidate",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_candidate_inbox, expected_revision=state.revision)
    state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="candidate")
    assert len(leases) == 1
    assert "create_root_task" in leases[0]["agent"]["effective_role_permissions"]

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "candidate",
            "dispatch_id": leases[0]["dispatch_id"],
            "task_creations": [
                {
                    "task_id": "root-task",
                    "owner_agent_id": "candidate",
                    "project_id": "project",
                    "title": "Root task",
                    "description": "Create a root task as project lead.",
                }
            ],
        },
        expected_revision=state.revision,
    )

    assert result["success"] is True
    assert result["created_task_ids"] == ["root-task"]
    assert state.tasks["root-task"].created_by_agent_id == "candidate"


def test_create_project_allows_same_agent_to_lead_multiple_projects(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    state = _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    state = _team_admin_service(repo).create_project(
        project_id="first-project",
        team_id="team",
        name="First Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=state.revision,
    )

    state = _team_admin_service(repo).create_project(
        project_id="second-project",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead",
        lead_session_id=None,
        expected_revision=state.revision,
    )

    assert project_lead_agent_id(state, "first-project") == "lead"
    assert project_lead_agent_id(state, "second-project") == "lead"
    assert agent_project_ids(state, "lead") == ["first-project", "second-project"]


def test_update_team_can_reassign_manager_to_existing_team_agent(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

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
        expected_revision=1,
    )
    _team_admin_service(repo).create_role(
        role_id="team__ops",
        team_id="team",
        name="Ops",
        expected_revision=2,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=3,
    )
    _team_admin_service(repo).register_agent(
        agent_id="ops",
        team_id="team",
        role_id="team__ops",
        session_id="session-ops",
        reports_to_agent_id="manager",
        expected_revision=4,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=5,
    )

    state = _team_admin_service(repo).update_team(
        team_id="team",
        manager_agent_id="ops",
        expected_revision=6,
    )

    assert state.teams["team"].manager_agent_id == "ops"
    assert state.agents["ops"].reports_to_agent_id is None
    assert state.agents["ops"].subordinate_agent_ids == ["lead"]
    assert state.agents["manager"].reports_to_agent_id is None
    assert state.agents["manager"].subordinate_agent_ids == []
    assert state.agents["lead"].reports_to_agent_id == "ops"
    assert state.agents["lead"].subordinate_agent_ids == ["backend"]
    assert RolePermission.CREATE_ROOT_TASK in state.roles["team__ops"].permissions


def test_reassigned_project_lead_manager_can_create_tasks_across_team_projects(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    _team_admin_service(repo).create_project(
        project_id="project-a",
        team_id="team",
        name="Project A",
        lead_agent_id="lead-a",
        lead_session_id="session-lead-a",
        expected_revision=1,
    )
    _team_admin_service(repo).create_project(
        project_id="project-b",
        team_id="team",
        name="Project B",
        lead_agent_id="lead-b",
        lead_session_id="session-lead-b",
        expected_revision=2,
    )
    _team_admin_service(repo).update_team(
        team_id="team",
        manager_agent_id="lead-a",
        expected_revision=3,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead-a",
            "task_creations": [
                {
                    "id": "cross-project-task",
                    "owner_agent_id": "lead-b",
                    "project_id": "project-b",
                    "priority": 1,
                    "title": "Coordinate project B",
                    "description": "Promoted manager assigns the other project lead.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["errors"] == []
    assert state.tasks["cross-project-task"].owner_agent_id == "lead-b"
    assert state.tasks["cross-project-task"].created_by_agent_id == "lead-a"


def test_update_team_rejects_invalid_manager_reassignment(tmp_path: Path) -> None:
    from evander_team_addon.models import AgentStatus

    repo = TeamStateRepository(tmp_path)
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
        expected_revision=3,
    )
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=4,
    )

    with pytest.raises(ValueError, match="manager agent missing"):
        _team_admin_service(repo).update_team(
            team_id="team",
            manager_agent_id="missing",
            expected_revision=5,
        )
    with pytest.raises(ValueError, match="manager agent other-manager is not in team team"):
        _team_admin_service(repo).update_team(
            team_id="team",
            manager_agent_id="other-manager",
            expected_revision=5,
        )
    with pytest.raises(ValueError, match="nested under current manager"):
        _team_admin_service(repo).update_team(
            team_id="team",
            manager_agent_id="backend",
            expected_revision=5,
        )

    def _pause_lead(state):
        state.agents["lead"].status = AgentStatus.PAUSED
        return state

    repo.update(_pause_lead)
    with pytest.raises(ValueError, match="manager agent lead is not active"):
        _team_admin_service(repo).update_team(
            team_id="team",
            manager_agent_id="lead",
            expected_revision=6,
        )


def test_repository_generates_missing_operator_ids_with_collision_suffixes(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    state = _team_admin_service(repo).create_team(
        team_id=None,
        name="Platform Team",
        manager_agent_id=None,
        manager_session_id="session-manager",
        manager_display_name="Platform Manager",
    )
    assert "team-platform-team" in state.teams
    assert "team-platform-team__manager" in state.roles
    assert "agent-team-platform-team-platform-manager" in state.agents

    state = _team_admin_service(repo).create_project(
        project_id=None,
        team_id="team-platform-team",
        name="Runtime Upgrade",
        lead_agent_id=None,
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[{"description": "Ship runtime upgrade"}],
        expected_revision=state.revision,
    )
    project_id = "project-team-platform-team-runtime-upgrade"
    assert project_id in state.projects
    assert state.projects[project_id].lead_agent_id == (
        "agent-project-team-platform-team-runtime-upgrade-project-lead"
    )
    assert state.projects[project_id].goals[0].id == (
        "goal-project-team-platform-team-runtime-upgrade-ship-runtime-upgrade"
    )

    state = _team_admin_service(repo).create_role(
        role_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        name="Backend",
        expected_revision=state.revision,
    )
    state = _team_admin_service(repo).create_role(
        role_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        name="Backend",
        expected_revision=state.revision,
    )
    assert "role-project-team-platform-team-runtime-upgrade-backend" in state.roles
    assert "role-project-team-platform-team-runtime-upgrade-backend-2" in state.roles

    state = _team_admin_service(repo).register_agent(
        agent_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        role_id="role-project-team-platform-team-runtime-upgrade-backend",
        session_id="session-backend",
        reports_to_agent_id="agent-project-team-platform-team-runtime-upgrade-project-lead",
        display_name="Backend Engineer",
        expected_revision=state.revision,
    )
    agent_id = "agent-project-team-platform-team-runtime-upgrade-backend-engineer"
    assert agent_id in state.agents

    state = _team_admin_service(repo).create_or_reprioritize_task(
        task_id=None,
        owner_agent_id=agent_id,
        project_id=project_id,
        priority=10,
        title="Implement API",
        description="Implement the runtime API.",
        created_by_agent_id=state.projects[project_id].lead_agent_id,
        expected_revision=state.revision,
    )
    task_id = "task-project-team-platform-team-runtime-upgrade-implement-api"
    assert task_id in state.tasks

    state = _team_admin_service(repo).record_deliverable(
        deliverable_id=None,
        task_id=task_id,
        producer_agent_id=agent_id,
        summary="API patch",
        expected_revision=state.revision,
    )
    deliverable_id = f"deliverable-{task_id}-api-patch"
    assert deliverable_id in state.deliverables

    state = _team_admin_service(repo).create_review_profile(
        profile_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        name="Standard review",
        expected_revision=state.revision,
    )
    profile_id = "profile-project-team-platform-team-runtime-upgrade-standard-review"
    assert profile_id in state.review_profiles

    state = _team_admin_service(repo).record_review_request(
        review_request_id=None,
        deliverable_id=deliverable_id,
        reviewer_agent_id=state.projects[project_id].lead_agent_id,
        review_profile_id=profile_id,
        expected_revision=state.revision,
    )
    assert any(
        review_id.startswith(f"review-{deliverable_id}") for review_id in state.review_requests
    )


def test_repository_preserves_explicit_operator_ids(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = _team_admin_service(repo).create_team(
        team_id="explicit-team",
        name="Display Name",
        manager_agent_id="explicit-manager",
        manager_session_id="session-manager",
        manager_role_id="explicit-manager-role",
    )

    assert "explicit-team" in state.teams
    assert "explicit-manager" in state.agents
    assert "explicit-manager-role" in state.roles
    assert "team-display-name" not in state.teams


def test_repository_updates_team_project_and_agent_records(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = _team_admin_service(repo).update_team(
        team_id="team",
        name="Renamed Team",
        operator_communication_target_id=None,
        set_operator_communication_target_id=True,
        max_cost_usd="123.45",
        set_max_cost_usd=True,
        archived=True,
        expected_revision=repo.read().revision,
    )

    team = state.teams["team"]
    assert state.revision == 7
    assert team.name == "Renamed Team"
    assert team.operator_communication_target_id is None
    assert str(team.max_cost_usd) == "123.45"
    assert team.archived is True
    assert {
        agent.id for agent in state.agents.values() if agent.status is AgentStatus.ARCHIVED
    } == {
        "backend",
        "lead",
        "manager",
        "worker",
    }

    state = _team_admin_service(repo).update_project(
        project_id="project",
        name="Renamed Project",
        status=ProjectStatus.BLOCKED,
        git=ProjectGitConfig(managed_repo_id=MANAGED_REPO_ID),
        set_git=True,
        goals=[ProjectGoal(id="goal-2", description="Updated goal")],
        expected_revision=repo.read().revision,
    )

    project = state.projects["project"]
    assert state.revision == 8
    assert project.name == "Renamed Project"
    assert project.status is ProjectStatus.BLOCKED
    assert project.git is not None
    assert project.git.managed_repo_id == MANAGED_REPO_ID
    assert [goal.id for goal in project.goals] == ["goal-2"]

    _team_admin_service(repo).create_role(
        role_id="project__qa",
        team_id="team",
        project_id="project",
        name="QA",
        expected_revision=repo.read().revision,
    )
    state = _team_admin_service(repo).update_agent(
        agent_id="worker",
        role_id="project__qa",
        session_id="session-worker-2",
        reports_to_agent_id="lead",
        set_reports_to_agent_id=True,
        display_name=None,
        set_display_name=True,
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=3),
        status=AgentStatus.PAUSED,
        expected_revision=repo.read().revision,
    )

    worker = state.agents["worker"]
    assert state.revision == 10
    assert worker.role_id == "project__qa"
    assert worker.session_id == "session-worker-2"
    assert worker.display_name is None
    assert worker.reports_to_agent_id == "lead"
    assert worker.ephemeral_policy.max_concurrent == 3
    assert worker.status is AgentStatus.PAUSED
    assert state.agents["backend"].subordinate_agent_ids == []
    assert state.agents["lead"].subordinate_agent_ids == ["backend", "worker"]

    state = _team_admin_service(repo).update_team(
        team_id="team",
        max_cost_usd=None,
        set_max_cost_usd=True,
    )
    assert state.teams["team"].max_cost_usd is None

    try:
        _team_admin_service(repo).update_team(
            team_id="team",
            max_cost_usd="-1",
            set_max_cost_usd=True,
        )
    except ValueError as exc:
        assert "max_cost_usd" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("negative team max cost should be rejected")


def test_repository_rejects_agent_update_reporting_cycle_without_writing(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValidationError, match="reporting cycle"):
        _team_admin_service(repo).update_agent(
            agent_id="lead",
            reports_to_agent_id="worker",
            set_reports_to_agent_id=True,
            expected_revision=repo.read().revision,
        )

    state = repo.read()
    assert state.revision == 6
    assert state.agents["lead"].reports_to_agent_id == "manager"
    assert state.agents["manager"].subordinate_agent_ids == ["lead"]


def test_repository_materializes_auto_dev_squad_atomically(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state, result = _team_admin_service(repo).materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(),
        expected_revision=0,
    )

    assert result["success"] is True
    assert result["template_id"] == "auto-dev-squad"
    assert result["template_name"] == "Auto-Dev Squad"
    assert result["team_id"] == "team-auto-dev-squad"
    assert result["project_id"] == "project-auto-dev-squad"
    assert result["agent_ids"] == {
        "manager": "manager-auto-dev-squad",
        "project_lead": "project-lead-auto-dev-squad",
        "implementer": "implementer-auto-dev-squad",
        "reviewer": "reviewer-auto-dev-squad",
        "release_operator": "release-operator-auto-dev-squad",
    }
    assert result["goal_ids"] == ["project-auto-dev-squad-auto-dev-reviewed-delivery"]
    assert result["review_profile_ids"] == ["project-auto-dev-squad-auto-dev-review"]
    assert result["task_ids"] == [
        "project-auto-dev-squad-implement-scoped-slice",
        "project-auto-dev-squad-release-handoff",
    ]
    assert result["revision"] == 1

    assert state.revision == 1
    team = state.teams["team-auto-dev-squad"]
    assert team.manager_agent_id == "manager-auto-dev-squad"
    assert team.operator_communication_target_id == "slack://ops-channel"
    assert team.project_ids == ["project-auto-dev-squad"]

    project = state.projects["project-auto-dev-squad"]
    assert project.team_id == "team-auto-dev-squad"
    assert project.lead_agent_id == "project-lead-auto-dev-squad"
    assert project.git is not None
    assert project.git.managed_repo_id == MANAGED_REPO_ID
    assert [goal.id for goal in project.goals] == [
        "project-auto-dev-squad-auto-dev-reviewed-delivery"
    ]

    assert state.agents["manager-auto-dev-squad"].subordinate_agent_ids == [
        "project-lead-auto-dev-squad"
    ]
    assert state.agents["project-lead-auto-dev-squad"].subordinate_agent_ids == [
        "implementer-auto-dev-squad",
        "reviewer-auto-dev-squad",
        "release-operator-auto-dev-squad",
    ]
    assert (
        state.agents["project-lead-auto-dev-squad"].reports_to_agent_id == "manager-auto-dev-squad"
    )
    assert (
        state.agents["implementer-auto-dev-squad"].reports_to_agent_id
        == "project-lead-auto-dev-squad"
    )
    assert (
        state.agents["reviewer-auto-dev-squad"].reports_to_agent_id == "project-lead-auto-dev-squad"
    )
    assert (
        state.agents["release-operator-auto-dev-squad"].reports_to_agent_id
        == "project-lead-auto-dev-squad"
    )
    assert {
        role_key: state.agents[agent_id].ephemeral_policy.max_concurrent
        for role_key, agent_id in result["agent_ids"].items()
    } == {
        "manager": 0,
        "project_lead": 1,
        "implementer": 2,
        "reviewer": 1,
        "release_operator": 1,
    }

    profile = state.review_profiles["project-auto-dev-squad-auto-dev-review"]
    assert profile.team_id == "team-auto-dev-squad"
    assert profile.project_id == "project-auto-dev-squad"
    assert [criterion.id for criterion in profile.criteria] == [
        "scope",
        "tests",
        "risk",
        "handoff",
    ]

    implement_task = state.tasks["project-auto-dev-squad-implement-scoped-slice"]
    release_task = state.tasks["project-auto-dev-squad-release-handoff"]
    assert implement_task.owner_agent_id == "implementer-auto-dev-squad"
    assert implement_task.created_by_agent_id == "project-lead-auto-dev-squad"
    assert release_task.owner_agent_id == "release-operator-auto-dev-squad"
    assert release_task.dependency_task_ids == ["project-auto-dev-squad-implement-scoped-slice"]


def test_repository_materializes_accounting_department_without_git(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    state, result = _team_admin_service(repo).materialize_template(
        "accounting-department",
        inputs=_accounting_department_ui_inputs(),
        expected_revision=0,
    )

    assert result["success"] is True
    assert result["template_id"] == "accounting-department"
    assert result["template_name"] == "Accounting Department"
    assert set(result["agent_ids"]) == {
        "manager",
        "project_lead",
        "bookkeeping_clerk",
        "invoice_reviewer",
        "payment_preparer",
        "tax_compliance_reviewer",
    }
    assert result["goal_ids"] == [f"{result['project_id']}-bookkeeping-operations"]
    assert result["outcome_kind_definition_ids"] == [
        f"{result['project_id']}-outcome-kind-evidence_match",
        f"{result['project_id']}-outcome-kind-bokio_prepared_action",
    ]
    assert result["review_profile_ids"] == [f"{result['project_id']}-bookkeeping-action-review"]
    assert result["task_ids"] == [
        f"{result['project_id']}-process-inbox-receipts",
        f"{result['project_id']}-reconcile-supplier-invoices",
        f"{result['project_id']}-review-prepared-bookkeeping-actions",
        f"{result['project_id']}-prepare-bank-payments",
        f"{result['project_id']}-review-tax-compliance-items",
    ]

    team = state.teams[result["team_id"]]
    assert team.manager_agent_id == result["agent_ids"]["manager"]
    assert team.operator_communication_target_id == "teams://accounting-ops"
    assert team.escalate_to_governance is True
    assert team.project_ids == [result["project_id"]]

    project = state.projects[result["project_id"]]
    assert project.team_id == result["team_id"]
    assert project.lead_agent_id == result["agent_ids"]["project_lead"]
    assert project.git is None
    assert [goal.id for goal in project.goals] == result["goal_ids"]

    assert state.agents[result["agent_ids"]["manager"]].subordinate_agent_ids == [
        result["agent_ids"]["project_lead"]
    ]
    assert state.agents[result["agent_ids"]["project_lead"]].subordinate_agent_ids == [
        result["agent_ids"]["bookkeeping_clerk"],
        result["agent_ids"]["invoice_reviewer"],
        result["agent_ids"]["payment_preparer"],
        result["agent_ids"]["tax_compliance_reviewer"],
    ]
    for role_key in (
        "bookkeeping_clerk",
        "invoice_reviewer",
        "payment_preparer",
        "tax_compliance_reviewer",
    ):
        assert (
            state.agents[result["agent_ids"][role_key]].reports_to_agent_id
            == result["agent_ids"]["project_lead"]
        )

    assert {
        role_key: state.agents[agent_id].ephemeral_policy.max_concurrent
        for role_key, agent_id in result["agent_ids"].items()
    } == {
        "manager": 0,
        "project_lead": 1,
        "bookkeeping_clerk": 1,
        "invoice_reviewer": 1,
        "payment_preparer": 1,
        "tax_compliance_reviewer": 1,
    }
    assert state.roles[result["role_ids"]["manager"]].project_id is None
    assert state.roles[result["role_ids"]["project_lead"]].project_id == result["project_id"]
    assert state.roles[result["role_ids"]["project_lead"]].permissions == [
        RolePermission.CREATE_ROOT_TASK,
        RolePermission.PREPARE_BOOKKEEPING_ACTION,
    ]
    assert state.roles[result["role_ids"]["bookkeeping_clerk"]].permissions == [
        RolePermission.PREPARE_BOOKKEEPING_ACTION
    ]
    assert state.roles[result["role_ids"]["invoice_reviewer"]].permissions == [
        RolePermission.REVIEW_BOOKKEEPING_ACTION
    ]
    assert state.roles[result["role_ids"]["payment_preparer"]].permissions == [
        RolePermission.PREPARE_BOOKKEEPING_ACTION,
        RolePermission.REQUEST_PAYMENT_PREPARATION,
    ]
    assert state.roles[result["role_ids"]["tax_compliance_reviewer"]].permissions == [
        RolePermission.REVIEW_BOOKKEEPING_ACTION
    ]

    profile = state.review_profiles[result["review_profile_ids"][0]]
    assert profile.team_id == result["team_id"]
    assert profile.project_id == result["project_id"]
    assert profile.applicable_role_ids == [
        result["role_ids"]["invoice_reviewer"],
        result["role_ids"]["tax_compliance_reviewer"],
    ]
    assert profile.required_outcome_kinds == ["bokio_prepared_action"]
    assert {
        state.outcome_kind_definitions[definition_id].kind
        for definition_id in result["outcome_kind_definition_ids"]
    } == {"evidence_match", "bokio_prepared_action"}
    assert [criterion.id for criterion in profile.criteria] == [
        "supporting_documentation",
        "bokio_mapping",
        "tax_treatment",
        "approval_boundary",
    ]

    review_task = state.tasks[f"{result['project_id']}-review-prepared-bookkeeping-actions"]
    payment_task = state.tasks[f"{result['project_id']}-prepare-bank-payments"]
    assert review_task.owner_agent_id == result["agent_ids"]["invoice_reviewer"]
    assert review_task.dependency_task_ids == [f"{result['project_id']}-process-inbox-receipts"]
    assert payment_task.owner_agent_id == result["agent_ids"]["payment_preparer"]
    assert payment_task.dependency_task_ids == [
        f"{result['project_id']}-reconcile-supplier-invoices"
    ]


def test_repository_materializes_auto_dev_squad_with_team_project_and_agents_shape(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    state, result = _team_admin_service(repo).materialize_template(
        "auto-dev-squad",
        inputs={
            "team": {
                "id": "team-auto-dev-squad",
                "name": "Auto-Dev Squad",
                "operator_communication_target_id": "slack://ops-channel",
                "manager_agent_id": "manager-auto-dev-squad",
                "manager_session_id": "session-manager-auto-dev-squad",
            },
            "project": {
                "id": "project-auto-dev-squad",
                "name": "Reviewed Auto-Dev Delivery",
                "lead_agent_id": "project-lead-auto-dev-squad",
                "lead_session_id": "session-project-lead-auto-dev-squad",
                "git": {
                    "managed_repo_id": MANAGED_REPO_ID,
                    "base_branch": "main",
                },
            },
            "agents": {
                "implementer": {
                    "agent_id": "implementer-auto-dev-squad",
                    "session_id": "session-implementer-auto-dev-squad",
                },
                "reviewer": {
                    "agent_id": "reviewer-auto-dev-squad",
                    "session_id": "session-reviewer-auto-dev-squad",
                },
                "release_operator": {
                    "agent_id": "release-operator-auto-dev-squad",
                    "session_id": "session-release-operator-auto-dev-squad",
                },
            },
        },
        expected_revision=0,
    )

    assert result["success"] is True
    assert state.projects["project-auto-dev-squad"].lead_agent_id == "project-lead-auto-dev-squad"
    assert state.agents["manager-auto-dev-squad"].subordinate_agent_ids == [
        "project-lead-auto-dev-squad"
    ]
    assert state.agents["project-lead-auto-dev-squad"].subordinate_agent_ids == [
        "implementer-auto-dev-squad",
        "reviewer-auto-dev-squad",
        "release-operator-auto-dev-squad",
    ]


def test_repository_materialize_template_allows_multiple_unique_squads(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    first_state, first_result = _team_admin_service(repo).materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(suffix="alpha"),
        expected_revision=0,
    )
    second_state, second_result = _team_admin_service(repo).materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(suffix="beta"),
        expected_revision=first_state.revision,
    )

    assert first_result["review_profile_ids"] == ["project-alpha-auto-dev-review"]
    assert second_result["review_profile_ids"] == ["project-beta-auto-dev-review"]
    assert second_state.revision == 2
    assert set(second_state.teams) == {"team-alpha", "team-beta"}
    assert set(second_state.projects) == {"project-alpha", "project-beta"}
    assert {
        "project-alpha-implement-scoped-slice",
        "project-alpha-release-handoff",
        "project-beta-implement-scoped-slice",
        "project-beta-release-handoff",
    }.issubset(second_state.tasks)
    assert second_state.tasks["project-beta-release-handoff"].dependency_task_ids == [
        "project-beta-implement-scoped-slice"
    ]


def test_repository_materialize_template_rejects_duplicate_ids_without_writing(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    with pytest.raises(ValueError, match="materialization ids contains duplicate ids"):
        _team_admin_service(repo).materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(duplicate_reviewer_id="implementer-auto-dev-squad"),
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 0
    assert state.teams == {}
    assert state.projects == {}
    assert state.agents == {}
    assert state.review_profiles == {}
    assert state.tasks == {}


def test_repository_materialize_template_rejects_stale_revision_without_writing(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="existing-team",
        name="Existing Team",
        manager_agent_id="existing-manager",
        manager_session_id="existing-session",
        expected_revision=0,
    )

    with pytest.raises(TeamStateConflictError):
        _team_admin_service(repo).materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(),
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 1
    assert state.projects == {}


def test_repository_materialize_template_rejects_malformed_git_config(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    with pytest.raises(ValidationError, match="managed_repo_id must match mr_<20-hex>"):
        _team_admin_service(repo).materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(bad_git=True),
            expected_revision=0,
        )

    assert repo.read().revision == 0


def test_repository_enforces_parent_ephemeral_concurrency(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
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
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=1),
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
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="max_concurrent=1"):
        _team_ephemeral_service(repo).request_ephemeral_assignments(
            parent_agent_id="backend",
            requests=[
                {"task_id": "task", "title": "One", "prompt": "Do one thing."},
                {"task_id": "task", "title": "Two", "prompt": "Do another thing."},
            ],
        )

    assert repo.read().ephemeral_assignments == {}


def test_repository_slice9_request_ephemeral_agents_is_all_or_nothing(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_backend(repo, dispatch_id="dispatch-ephemeral-request")

    def _enable_ephemeral(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable_ephemeral)

    state, result = _command_service(repo).request_ephemeral_agents_command(
        session_id="session-backend",
        request_id="request-ephemeral-too-many",
        assignments=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "title": "One",
                "prompt": "Do one thing.",
            },
            {
                "id": "ephemeral-2",
                "task_id": "task",
                "title": "Two",
                "prompt": "Do another thing.",
            },
        ],
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "ephemeral_request_failed"
    assert state.ephemeral_assignments == {}
    assert repo.read().ephemeral_assignments == {}
    assert repo.list_outbox_records() == []


def test_repository_slice9_report_replay_does_not_duplicate_notice_or_event(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_backend(repo, dispatch_id="dispatch-ephemeral-request")

    def _enable_ephemeral(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable_ephemeral)
    _state, requested = _command_service(repo).request_ephemeral_agent_command(
        session_id="session-backend",
        id="ephemeral-1",
        task_id="task",
        worker_session_id="session-ephemeral",
        title="Scoped worker",
        prompt="Implement the scoped worker task.",
        request_id="request-ephemeral-start",
    )

    assert requested["success"] is True
    assert requested["intent_recorded"] is True
    assert requested["events"][0]["event_type"] == EPHEMERAL_REQUESTED_EVENT_TYPE
    assert requested["events"][0]["payload"]["allowed_tools"] == ["team_report_ephemeral_result"]
    assert '"assessment": {' in requested["events"][0]["payload"]["prompt"]
    assert '"task_id": "task"' in requested["events"][0]["payload"]["prompt"]

    _state, reported = _command_service(repo).report_ephemeral_result_command(
        session_id="session-ephemeral",
        assignment_id="ephemeral-1",
        summary="Worker result is ready.",
        artifact_refs=["artifact-space:/teams/team/ephemeral/result"],
        agent_branch="agents/session-ephemeral/ephemeral-1",
        commit_sha="abc1234",
        request_id="request-ephemeral-result",
    )
    _state, replayed = _command_service(repo).report_ephemeral_result_command(
        session_id="session-ephemeral",
        assignment_id="ephemeral-1",
        summary="Worker result is ready.",
        artifact_refs=["artifact-space:/teams/team/ephemeral/result"],
        agent_branch="agents/session-ephemeral/ephemeral-1",
        commit_sha="abc1234",
        request_id="request-ephemeral-result",
    )

    assert replayed == reported
    assert reported["success"] is True
    assert reported["state"] == "completed"
    assert reported["created_inbox_item_ids"] == replayed["created_inbox_item_ids"]
    assert reported["events"][0]["event_type"] == EPHEMERAL_COMPLETED_EVENT_TYPE
    final = repo.read()
    notices = [
        item
        for item in final.inbox.values()
        if item.payload.get("ephemeral_assignment_id") == "ephemeral-1"
    ]
    assert len(notices) == 1
    completed_outbox = repo.list_outbox_records(
        intent_type=EPHEMERAL_COMPLETED_EVENT_TYPE,
        aggregate_type="ephemeral_assignment",
        aggregate_id="ephemeral-1",
    )
    assert len(completed_outbox) == 1


def test_repository_slice9_accept_completed_assignment_creates_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_backend(repo, dispatch_id="dispatch-ephemeral-accept")

    def _enable_ephemeral(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable_ephemeral)
    _command_service(repo).request_ephemeral_agent_command(
        session_id="session-backend",
        id="ephemeral-1",
        task_id="task",
        worker_session_id="session-ephemeral",
        title="Scoped worker",
        prompt="Implement the scoped worker task.",
        request_id="request-ephemeral-start",
    )
    _command_service(repo).report_ephemeral_result_command(
        session_id="session-ephemeral",
        summary="Worker result is ready.",
        artifact_refs=["artifact-space:/teams/team/ephemeral/result"],
        request_id="request-ephemeral-result",
    )

    _state, accepted = _command_service(repo).accept_ephemeral_result_command(
        session_id="session-backend",
        assignment_id="ephemeral-1",
        deliverable_id="deliverable-ephemeral",
        request_id="request-ephemeral-accept",
    )

    assert accepted["success"] is True
    assert accepted["assignment_id"] == "ephemeral-1"
    assert accepted["deliverable_id"] == "deliverable-ephemeral"
    final = repo.read()
    assert final.ephemeral_assignments["ephemeral-1"].state == EphemeralAssignmentState.ACCEPTED
    assert final.deliverables["deliverable-ephemeral"].producer_agent_id == "backend"
    assert final.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_repository_stale_revision_rejects_without_writing(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )

    with pytest.raises(TeamStateConflictError):
        _team_admin_service(repo).create_project(
            project_id="project",
            team_id="team",
            name="Project",
            lead_agent_id="lead",
            lead_session_id="session-lead",
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 1
    assert state.projects == {}


def test_repository_rejects_malformed_existing_state(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    with sqlite3.connect(tmp_path / "addons" / "team-addon" / "state.db") as connection:
        connection.execute(
            """
            INSERT INTO team_teams(
                id, name, manager_agent_id, operator_communication_target_id,
                max_cost_usd, created_at, updated_at, archived
            )
            VALUES ('team', 'Team', 'missing', NULL, NULL, '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00', 0)
            """
        )

    with pytest.raises(ValidationError, match="missing manager"):
        repo.read()


def test_deleting_addon_state_does_not_affect_sessions_file(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    sessions_path = runtime_dir / "sessions.json"
    sessions_path.write_text('{"session": {"metadata": {}}}', encoding="utf-8")

    shutil.rmtree(runtime_dir / "addons" / "team-addon")

    assert sessions_path.read_text(encoding="utf-8") == '{"session": {"metadata": {}}}'
    assert not (runtime_dir / "addons" / "team-addon").exists()
    assert repo.runtime_dir == runtime_dir


def test_apply_agent_action_allows_self_and_subordinate_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "id": "task-self",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Lead self task",
                    "description": "Work the lead owns.",
                },
                {
                    "id": "task-sub",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Backend task",
                    "description": "Work for a direct subordinate.",
                },
            ],
        },
    )

    assert result["success"] is True
    assert set(result["created_task_ids"]) == {"task-self", "task-sub"}
    assert state.tasks["task-self"].created_by_agent_id == "lead"
    assert state.tasks["task-self"].state == TeamTaskState.PENDING
    assert state.tasks["task-sub"].owner_agent_id == "backend"


def test_apply_agent_action_task_creation_batch_allows_sibling_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "task_id": "task-scaffold",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Scaffold",
                    "description": "Create the project scaffold.",
                },
                {
                    "task_id": "task-netcode",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Netcode",
                    "description": "Build the authoritative loop.",
                    "dependency_task_ids": ["task-scaffold"],
                },
                {
                    "task_id": "task-review",
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 3,
                    "title": "Review",
                    "description": "Review the completed slice.",
                    "dependency_task_ids": ["task-netcode"],
                },
            ],
        },
    )

    assert result["success"] is True
    assert result["errors"] == []
    assert result["created_task_ids"] == [
        "task-scaffold",
        "task-netcode",
        "task-review",
    ]
    assert state.tasks["task-netcode"].dependency_task_ids == ["task-scaffold"]
    assert state.tasks["task-review"].dependency_task_ids == ["task-netcode"]


def test_apply_agent_action_project_lead_can_target_project_hierarchy(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # worker is in the project but reports to backend, not directly to the lead.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Deep task",
                    "description": "Task for an indirect report in the project.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_task_ids"]) == 1
    created_id = result["created_task_ids"][0]
    assert state.tasks[created_id].owner_agent_id == "worker"


def test_apply_agent_action_manager_can_assign_project_leads(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "task_creations": [
                {
                    "id": "task-lead",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 0,
                    "title": "Lead directive",
                    "description": "Manager assigns the project lead.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.tasks["task-lead"].owner_agent_id == "lead"
    assert state.tasks["task-lead"].created_by_agent_id == "manager"


def test_apply_agent_action_rejects_root_task_without_permission(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "task-root",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Unmandated root",
                    "description": "Backend cannot open a top-level task.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("create_root_task" in error for error in result["errors"])
    assert "task-root" not in state.tasks


def test_operator_stand_down_blocks_subtree_wakes_and_new_task_work(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task", owner_agent_id="backend")

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert len(leases) == 1
    dispatch_id = leases[0]["dispatch_id"]

    state = _team_runtime_service(repo).stand_down_agent(
        agent_id="lead",
        reason="Pause project work.",
        set_by="operator:test",
    )
    assert state.stand_downs
    assert state.stand_downs[next(iter(state.stand_downs))].agent_id == "lead"

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": dispatch_id,
            "task_updates": [{"task_id": "backend-task", "state": "in_progress"}],
        },
        dispatch_id=dispatch_id,
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert any("stood down" in error for error in result["errors"])
    assert state.tasks["backend-task"].state == TeamTaskState.PENDING
    assert state.agent_runtimes["backend"].active_dispatch_id is None

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, minute=5, tzinfo=UTC),
    )
    assert "backend" not in [lease["agent"]["id"] for lease in leases]

    state = _team_runtime_service(repo).revoke_operator_stand_downs(
        agent_id="lead",
        revoked_by="operator:test",
    )
    assert all(stand_down.cleared_at is not None for stand_down in state.stand_downs.values())

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, minute=10, tzinfo=UTC),
    )
    assert len(leases) == 1


@pytest.mark.parametrize(
    ("stand_down_agent_id", "inherited"),
    [
        ("worker", False),
        ("manager", True),
    ],
)
def test_preexisting_review_dispatch_can_record_terminal_decision_during_stand_down(
    tmp_path: Path,
    stand_down_agent_id: str,
    inherited: bool,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="review-task")
    _seed_deliverable(
        repo,
        deliverable_id="review-deliverable",
        task_id="review-task",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="review-deliverable",
        dispatch_id="dispatch-request-review-before-hold",
    )
    _state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="review-deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-before-hold",
    )
    dispatch_id = "dispatch-review-before-hold"
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id=dispatch_id,
    )
    _team_runtime_service(repo).stand_down_agent(
        agent_id=stand_down_agent_id,
        reason="Pause new project work.",
        set_by="operator:test",
    )

    def _order_hold_after_dispatch(state):
        dispatch = state.dispatches[dispatch_id]
        assert dispatch.leased_at is not None
        stand_down = next(
            item
            for item in state.stand_downs.values()
            if item.agent_id == stand_down_agent_id and item.active
        )
        stand_down.set_at = dispatch.leased_at + timedelta(seconds=1)
        return state

    repo.update(_order_hold_after_dispatch)

    state, decided = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=requested["review_request_id"],
        state="rejected",
        findings=["The verification evidence is incomplete."],
        request_id=f"reject-review-during-hold-{stand_down_agent_id}",
    )

    assert decided["success"] is True
    assert state.review_requests[requested["review_request_id"]].state == (
        TeamRecordState.REJECTED
    )
    active_stand_down = next(
        item for item in state.stand_downs.values() if item.active
    )
    assert active_stand_down.agent_id == stand_down_agent_id
    assert (active_stand_down.agent_id != "worker") is inherited


@pytest.mark.parametrize(
    ("stand_down_agent_id", "inherited"),
    [
        ("worker", False),
        ("manager", True),
    ],
)
def test_preexisting_support_task_dispatch_can_finish_during_stand_down(
    tmp_path: Path,
    stand_down_agent_id: str,
    inherited: bool,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="worker-task", owner_agent_id="worker")
    dispatch_id = "dispatch-support-task-before-hold"
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id=dispatch_id,
    )
    _team_runtime_service(repo).stand_down_agent(
        agent_id=stand_down_agent_id,
        reason="Pause new work but let the active support task finish.",
        set_by="operator:test",
    )

    def _order_hold_after_dispatch(state):
        dispatch = state.dispatches[dispatch_id]
        assert dispatch.leased_at is not None
        stand_down = next(
            item
            for item in state.stand_downs.values()
            if item.agent_id == stand_down_agent_id and item.active
        )
        stand_down.set_at = dispatch.leased_at + timedelta(seconds=1)
        return state

    repo.update(_order_hold_after_dispatch)

    state, finished = _command_service(repo).update_task_command(
        session_id="session-worker",
        task_id="worker-task",
        state="finished",
        request_id=f"finish-support-task-during-hold-{stand_down_agent_id}",
    )

    assert finished["success"] is True
    assert state.tasks["worker-task"].state == TeamTaskState.FINISHED
    active_stand_down = next(
        item for item in state.stand_downs.values() if item.active
    )
    assert active_stand_down.agent_id == stand_down_agent_id
    assert (active_stand_down.agent_id != "worker") is inherited


def test_preexisting_dispatch_stand_down_authority_cannot_start_or_widen_work(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="review-task")
    _seed_deliverable(
        repo,
        deliverable_id="review-deliverable",
        task_id="review-task",
    )
    _seed_task(repo, task_id="worker-task", owner_agent_id="worker")
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="review-deliverable",
        dispatch_id="dispatch-request-scoped-review",
    )
    _state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="review-deliverable",
        reviewer_agent_id="worker",
        instructions="Verify only this review.",
        request_id="request-scoped-review",
    )
    dispatch_id = "dispatch-scoped-review"
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id=dispatch_id,
    )
    _seed_task(
        repo,
        task_id="outside-dispatch-task",
        owner_agent_id="worker",
    )
    _seed_task(repo, task_id="outside-review-task")
    _seed_deliverable(
        repo,
        deliverable_id="outside-review-deliverable",
        task_id="outside-review-task",
    )
    _state, outside_review = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-not-in-dispatch",
                    "deliverable_id": "outside-review-deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "This arrived after the active dispatch snapshot.",
                }
            ],
        },
    )
    assert outside_review["success"] is True
    _team_runtime_service(repo).stand_down_agent(
        agent_id="worker",
        reason="Pause new work but let the active review finish.",
        set_by="operator:test",
    )

    def _order_hold_after_dispatch(state):
        dispatch = state.dispatches[dispatch_id]
        assert dispatch.leased_at is not None
        stand_down = next(
            item
            for item in state.stand_downs.values()
            if item.agent_id == "worker" and item.active
        )
        stand_down.set_at = dispatch.leased_at + timedelta(seconds=1)
        return state

    repo.update(_order_hold_after_dispatch)

    state, started = _command_service(repo).update_task_command(
        session_id="session-worker",
        task_id="worker-task",
        state="in_progress",
        request_id="start-task-during-hold",
    )
    assert started["success"] is False
    assert started["errors"][0]["code"] == "agent_stood_down"
    assert state.tasks["worker-task"].state == TeamTaskState.PENDING

    state, outside_finish = _command_service(repo).update_task_command(
        session_id="session-worker",
        task_id="outside-dispatch-task",
        state="finished",
        request_id="finish-outside-dispatch-task-during-hold",
    )
    assert outside_finish["success"] is False
    assert outside_finish["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["outside-dispatch-task"].state == TeamTaskState.PENDING

    state, widened_finish = _command_service(repo).update_task_command(
        session_id="session-worker",
        task_id="worker-task",
        state="finished",
        title="Retitled during the hold",
        request_id="finish-and-retitle-task-during-hold",
    )
    assert widened_finish["success"] is False
    assert widened_finish["errors"][0]["code"] == "agent_stood_down"
    assert state.tasks["worker-task"].state == TeamTaskState.PENDING
    assert state.tasks["worker-task"].title == "Task worker-task"

    state, unrelated = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id="review-not-in-dispatch",
        state="rejected",
        findings=["This must not be recorded."],
        request_id="decide-unrelated-review-during-hold",
    )
    assert unrelated["success"] is False
    assert unrelated["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.review_requests[requested["review_request_id"]].state == (
        TeamRecordState.PENDING
    )


def test_stand_down_cannot_be_bypassed_by_a_new_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="review-task")
    _seed_deliverable(
        repo,
        deliverable_id="review-deliverable",
        task_id="review-task",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="review-deliverable",
        dispatch_id="dispatch-request-review-before-direct-hold",
    )
    _state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="review-deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-before-direct-hold",
    )
    _team_runtime_service(repo).stand_down_agent(
        agent_id="worker",
        reason="Do not lease new work.",
        set_by="operator:test",
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="worker",
        now=datetime(2027, 1, 1, tzinfo=UTC),
        respect_wake_interval=False,
    )
    assert leases == []

    # Simulate a stale or defective lease path writing a dispatch after the hold.
    # Command authorization must still fail closed based on durable timestamps.
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-created-after-hold",
    )
    state, decided = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=requested["review_request_id"],
        state="accepted",
        request_id="accept-review-from-new-dispatch-during-hold",
    )

    assert decided["success"] is False
    assert decided["errors"][0]["code"] == "agent_stood_down"
    assert decided["correction_context"]["dispatch_predates_stand_downs"] is False
    assert state.review_requests[requested["review_request_id"]].state == (
        TeamRecordState.PENDING
    )


def test_governance_group_persists_without_relaxing_same_team_reporting(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )

    state = _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team", "other"],
        goals=[{"description": "Keep teams governed independently"}],
        max_cost_usd="25.00",
        expected_revision=repo.read().revision,
    )

    group = state.governance_groups["portfolio"]
    assert group.member_team_ids == ["team", "other"]
    assert group.goals[0].id.startswith("governance-goal-portfolio")
    assert float(group.max_cost_usd) == 25.0

    reloaded = TeamStateRepository(tmp_path).read()
    assert reloaded.governance_groups["portfolio"].member_team_ids == ["team", "other"]

    with pytest.raises(ValueError, match="superior agent other-manager is not in team team"):
        _team_admin_service(repo).update_agent(
            agent_id="backend",
            reports_to_agent_id="other-manager",
            set_reports_to_agent_id=True,
            expected_revision=repo.read().revision,
        )


def test_governance_agenda_derives_and_validates_reference_team_membership(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_project(
        project_id="other-project",
        team_id="other",
        name="Other Project",
        lead_agent_id="other-lead",
        lead_session_id="session-other-lead",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    state = _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="project-agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review project",
        summary="Review project state.",
        project_id="project",
        expected_revision=repo.read().revision,
    )

    assert state.governance_agenda["project-agenda"].team_id == "team"

    with pytest.raises(ValueError, match="team other is not in governance group portfolio"):
        _team_admin_service(repo).create_governance_agenda_item(
            agenda_item_id="bad-project-agenda",
            governance_group_id="portfolio",
            item_type="operator_directive",
            title="Bad project",
            summary="This project is outside the group.",
            project_id="other-project",
            expected_revision=repo.read().revision,
        )

    with pytest.raises(ValueError, match="agent other-lead does not belong to team team"):
        _team_admin_service(repo).create_governance_agenda_item(
            agenda_item_id="bad-agent-agenda",
            governance_group_id="portfolio",
            item_type="operator_directive",
            title="Bad agent",
            summary="This agent is outside the team.",
            team_id="team",
            agent_id="other-lead",
            expected_revision=repo.read().revision,
        )


def test_governance_group_member_removal_handles_removed_team_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team", "other"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="other-agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review other team",
        summary="Review other team status.",
        team_id="other",
        expected_revision=repo.read().revision,
    )

    state = _team_admin_service(repo).update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["team"],
        set_member_team_ids=True,
        expected_revision=repo.read().revision,
    )

    assert state.governance_groups["portfolio"].member_team_ids == ["team"]
    assert state.governance_agenda["other-agenda"].state == GovernanceAgendaItemState.HANDLED
    membership_agenda = [
        item for item in state.governance_agenda.values() if item.id != "other-agenda"
    ]
    assert len(membership_agenda) == 1
    assert membership_agenda[0].item_type == GovernanceAgendaItemType.POLICY_DECISION
    assert membership_agenda[0].team_id is None
    assert membership_agenda[0].payload["removed_team_ids"] == ["other"]
    assert membership_agenda[0].payload["current_member_team_ids"] == ["team"]


def test_governance_group_member_addition_creates_due_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    def _future_governance_wake(state):
        state.governance_runtimes["portfolio"] = GovernanceRuntime(
            governance_group_id="portfolio",
            next_wake_at=now + timedelta(hours=12),
        )
        return state

    repo.update(_future_governance_wake)

    state = _team_admin_service(repo).update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["team", "other"],
        set_member_team_ids=True,
        expected_revision=repo.read().revision,
    )

    agenda = list(state.governance_agenda.values())
    assert len(agenda) == 1
    assert agenda[0].item_type == GovernanceAgendaItemType.POLICY_DECISION
    assert agenda[0].title == "Governance member teams changed"
    assert agenda[0].payload == {
        "change_type": "member_teams_changed",
        "added_team_ids": ["other"],
        "removed_team_ids": [],
        "previous_member_team_ids": ["team"],
        "current_member_team_ids": ["team", "other"],
    }
    assert state.governance_runtimes["portfolio"].next_wake_at is None

    state, leases = _team_runtime_service(repo).lease_due_governance_managers(now=now)

    assert len(leases) == 1
    assert leases[0]["reason"] == "policy_decision"
    assert leases[0]["agenda"][0]["id"] == agenda[0].id
    assert [item["team"]["id"] for item in leases[0]["member_teams"]] == [
        "team",
        "other",
    ]
    assert state.governance_runtimes["portfolio"].wake_reason == "policy_decision"


def test_governance_group_unarchive_with_member_addition_creates_due_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).update_governance_group(
        governance_group_id="portfolio",
        archived=True,
        expected_revision=repo.read().revision,
    )

    def _future_governance_wake(state):
        state.governance_runtimes["portfolio"] = GovernanceRuntime(
            governance_group_id="portfolio",
            next_wake_at=now + timedelta(hours=12),
        )
        return state

    repo.update(_future_governance_wake)

    state = _team_admin_service(repo).update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["team", "other"],
        set_member_team_ids=True,
        archived=False,
        expected_revision=repo.read().revision,
    )

    agenda = list(state.governance_agenda.values())
    assert state.governance_groups["portfolio"].archived is False
    assert len(agenda) == 1
    assert agenda[0].item_type == GovernanceAgendaItemType.POLICY_DECISION
    assert agenda[0].payload["added_team_ids"] == ["other"]
    assert state.governance_runtimes["portfolio"].next_wake_at is None


def test_governance_group_archive_with_member_removal_does_not_create_due_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    future_wake = now + timedelta(hours=12)
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team", "other"],
        expected_revision=repo.read().revision,
    )

    def _future_governance_wake(state):
        state.governance_runtimes["portfolio"] = GovernanceRuntime(
            governance_group_id="portfolio",
            next_wake_at=future_wake,
        )
        return state

    repo.update(_future_governance_wake)

    state = _team_admin_service(repo).update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["team"],
        set_member_team_ids=True,
        archived=True,
        expected_revision=repo.read().revision,
    )

    assert state.governance_groups["portfolio"].archived is True
    assert state.governance_groups["portfolio"].member_team_ids == ["team"]
    assert state.governance_agenda == {}
    assert state.governance_runtimes["portfolio"].next_wake_at == future_wake


def test_governance_group_member_reorder_does_not_create_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team", "other"],
        expected_revision=repo.read().revision,
    )

    state = _team_admin_service(repo).update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["other", "team"],
        set_member_team_ids=True,
        expected_revision=repo.read().revision,
    )

    assert state.governance_groups["portfolio"].member_team_ids == ["other", "team"]
    assert state.governance_agenda == {}


def test_governance_manager_skips_when_agent_runtime_is_leased(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Review team status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    def _lease_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            wake_leased_until=now + timedelta(minutes=10),
            active_dispatch_id="team-dispatch-manager",
        )
        return state

    repo.update(_lease_manager)

    _state, leases = _team_runtime_service(repo).lease_due_governance_managers(now=now)

    assert leases == []


def test_governance_cost_signal_wakes_only_on_status_change(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    over_cap_costs = {
        "team_costs": {
            "team": {
                "estimated_cost": 12,
                "max_cost_usd": 10,
                "cost_limit_reached": True,
            }
        }
    }
    clear_costs = {
        "team_costs": {
            "team": {
                "estimated_cost": 2,
                "max_cost_usd": 10,
                "cost_limit_reached": False,
            }
        }
    }

    _state, leases = _team_runtime_service(repo).lease_due_governance_managers(
        now=now,
        cost_payload=over_cap_costs,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "cost_cap_reached"
    assert leases[0]["cost_signals"][0]["team_id"] == "team"
    _team_runtime_service(repo).complete_governance_dispatch(
        governance_group_id="portfolio",
        dispatch_id=leases[0]["dispatch_id"],
    )

    state, leases = _team_runtime_service(repo).lease_due_governance_managers(
        now=now + timedelta(hours=2),
        cost_payload=over_cap_costs,
    )

    assert leases == []
    assert state.governance_runtimes["portfolio"].last_cost_signal_fingerprints == [
        "team:cost_cap_reached"
    ]

    state, leases = _team_runtime_service(repo).lease_due_governance_managers(
        now=now + timedelta(hours=3),
        cost_payload=clear_costs,
    )

    assert leases == []
    assert state.governance_runtimes["portfolio"].last_cost_signal_fingerprints == []

    _state, leases = _team_runtime_service(repo).lease_due_governance_managers(
        now=now + timedelta(hours=4),
        cost_payload=over_cap_costs,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "cost_cap_reached"


def test_governance_blocked_signal_wakes_only_on_blocker_change(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="blocked-a",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Blocked A",
        description="Blocked work.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
        expected_revision=repo.read().revision,
    )

    _state, leases = _team_runtime_service(repo).lease_due_governance_managers(now=now)

    assert len(leases) == 1
    assert leases[0]["reason"] == "blocked_team"
    assert leases[0]["blocked_team_signals"][0]["blocked_task_ids"] == ["blocked-a"]
    _team_runtime_service(repo).complete_governance_dispatch(
        governance_group_id="portfolio",
        dispatch_id=leases[0]["dispatch_id"],
    )

    _state, leases = _team_runtime_service(repo).lease_due_governance_managers(
        now=now + timedelta(hours=2)
    )

    assert leases == []

    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="blocked-b",
        owner_agent_id="backend",
        project_id="project",
        priority=2,
        title="Blocked B",
        description="More blocked work.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
        expected_revision=repo.read().revision,
    )

    _state, leases = _team_runtime_service(repo).lease_due_governance_managers(
        now=now + timedelta(hours=3)
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "blocked_team"
    assert leases[0]["blocked_team_signals"][0]["blocked_task_ids"] == [
        "blocked-a",
        "blocked-b",
    ]


def test_superior_action_stand_downs_and_revokes_direct_subordinate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "stand_downs": [{"agent_id": "backend", "reason": "Await revised project plan."}],
        },
    )

    assert result["success"] is True
    assert len(result["created_stand_down_ids"]) == 1
    stand_down_id = result["created_stand_down_ids"][0]
    assert state.stand_downs[stand_down_id].source == AgentStandDownSource.SUPERIOR
    assert state.stand_downs[stand_down_id].set_by == "lead"

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert leases == []

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "stand_down_revocations": [{"agent_id": "backend"}],
        },
    )

    assert result["success"] is True
    assert result["revoked_stand_down_ids"] == [stand_down_id]
    assert state.stand_downs[stand_down_id].cleared_by == "lead"

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, minute=5, tzinfo=UTC),
    )
    assert len(leases) == 1


def test_superior_action_cannot_revoke_operator_stand_down(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    state = _team_runtime_service(repo).stand_down_agent(
        agent_id="backend",
        reason="Operator hold.",
        set_by="operator:test",
    )
    operator_stand_down_id = next(iter(state.stand_downs))

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "stand_down_revocations": [{"agent_id": "backend"}],
        },
    )

    assert result["success"] is False
    assert result["revoked_stand_down_ids"] == []
    assert state.stand_downs[operator_stand_down_id].cleared_at is None
    assert any("no active superior stand-down" in error for error in result["errors"])


def test_apply_agent_action_item_errors_do_not_abort_valid_task_creation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "task_updates": [{"state": "finished"}],
            "task_creations": [
                {
                    "id": "task-lead",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 0,
                    "title": "Lead directive",
                    "description": "Manager assigns the project lead.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["applied"] is True
    assert result["created_task_ids"] == ["task-lead"]
    assert result["updated_task_ids"] == []
    assert result["errors"] == ["task_updates[0]: task_id must be a non-empty string"]
    assert state.tasks["task-lead"].owner_agent_id == "lead"
    assert state.agent_runtimes["lead"].no_progress_count == 0
    action_record = next(iter(state.action_records.values()))
    assert action_record.success is False
    assert action_record.error == "task_updates[0]: task_id must be a non-empty string"


def test_apply_agent_action_rejects_unrelated_agent_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend cannot create a task for its own superior.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Nope",
                    "description": "Backend cannot assign its superior.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert state.tasks == {}
    assert state.agent_runtimes["backend"].no_progress_count == 1


def test_apply_agent_action_rejects_cross_project_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=repo.read().revision,
    )

    # lead leads "project" but tries to place a task on backend inside "project-2".
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project-2",
                    "priority": 1,
                    "title": "Cross project",
                    "description": "Backend does not belong to project-2.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("outside project" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_rejects_cross_project_self_task_creation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=repo.read().revision,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project-2",
                    "priority": 1,
                    "title": "Cross project self",
                    "description": "Backend cannot assign itself into project-2.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("outside project" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_rejects_terminal_task_creation_state(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Terminal",
                    "description": "Cannot be created already accepted.",
                    "state": "accepted",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("terminal" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_accepts_explicit_non_terminal_state(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "id": "task-progress",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 0,
                    "title": "In progress",
                    "description": "Created already in progress.",
                    "state": "in_progress",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.tasks["task-progress"].state == TeamTaskState.IN_PROGRESS


def test_apply_agent_action_creates_status_request_for_subordinate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "status_requests": [
                {"recipient_agent_id": "backend", "question": "Status on the backend slice?"}
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_status_request_ids"]) == 1
    item_id = result["created_status_request_ids"][0]
    item = state.inbox[item_id]
    assert item.recipient_agent_id == "backend"
    assert item.sender_agent_id == "lead"
    assert item.item_type == TeamInboxItemType.STATUS_REQUEST
    assert item.response_requested is True
    assert item.priority == TeamInboxPriority.NORMAL
    assert item.payload["question"] == "Status on the backend slice?"
    assert "response_requested" not in item.payload
    assert "priority" not in item.payload


def test_apply_agent_action_sets_reply_intent_defaults_and_overrides(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [
                {
                    "recipient_agent_id": "lead",
                    "question": "This is just confirming direction.",
                    "response_requested": False,
                    "priority": "high",
                }
            ],
            "status_reports": [
                {
                    "recipient_agent_id": "lead",
                    "summary": "Done with the FYI check.",
                }
            ],
        },
    )

    assert result["success"] is True
    items = [state.inbox[item_id] for item_id in result["created_inbox_item_ids"]]
    question = next(item for item in items if item.item_type == TeamInboxItemType.QUESTION)
    report = next(item for item in items if item.item_type == TeamInboxItemType.PROGRESS_REPORT)
    assert question.response_requested is False
    assert report.response_requested is False
    assert question.priority == TeamInboxPriority.HIGH
    assert report.priority == TeamInboxPriority.LOW
    assert "response_requested" not in question.payload
    assert "response_requested" not in report.payload
    assert "priority" not in question.payload
    assert "priority" not in report.payload

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "status_reports": [
                {
                    "recipient_agent_id": "lead",
                    "summary": "Please confirm this plan.",
                    "response_requested": True,
                    "priority": "normal",
                }
            ],
        },
    )

    assert result["success"] is True
    report = state.inbox[result["created_inbox_item_ids"][0]]
    assert report.response_requested is True
    assert report.priority == TeamInboxPriority.NORMAL


def test_apply_agent_action_rejects_non_boolean_reply_intent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [
                {
                    "recipient_agent_id": "lead",
                    "question": "Can I use a string here?",
                    "response_requested": "false",
                }
            ],
        },
    )

    assert result["success"] is False
    assert state.inbox == {}
    assert any("response_requested must be a boolean" in error for error in result["errors"])


def test_apply_agent_action_rejects_invalid_inbox_priority(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [
                {
                    "recipient_agent_id": "lead",
                    "question": "Can I invent a priority?",
                    "priority": "eventually",
                }
            ],
        },
    )

    assert result["success"] is False
    assert state.inbox == {}
    assert any("priority must be one of: low, normal, high" in error for error in result["errors"])


def test_repository_round_trips_inbox_reply_intent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "status_reports": [
                {
                    "recipient_agent_id": "lead",
                    "summary": "No reply needed.",
                    "priority": "low",
                }
            ],
        },
    )

    item_id = result["created_inbox_item_ids"][0]
    assert state.inbox[item_id].response_requested is False
    assert state.inbox[item_id].priority == TeamInboxPriority.LOW
    reloaded = TeamStateRepository(tmp_path).read()
    assert reloaded.inbox[item_id].response_requested is False
    assert reloaded.inbox[item_id].priority == TeamInboxPriority.LOW


def test_apply_agent_action_rejects_status_request_to_non_subordinate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "status_requests": [{"recipient_agent_id": "lead", "question": "Status?"}],
        },
    )

    assert result["success"] is False
    assert result["created_status_request_ids"] == []
    assert any("direct subordinate" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_rejects_question_to_non_superior(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend's superior is lead; sending to worker (a subordinate) is rejected.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [{"recipient_agent_id": "worker", "question": "?"}],
        },
    )

    assert result["success"] is False
    assert any("direct superior" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_manager_question_redirected_to_escalations(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "questions": [{"recipient_agent_id": "lead", "question": "?"}],
        },
    )

    assert result["success"] is False
    assert any("operator_escalations" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_task_creation_resets_no_progress_count(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")

    _team_agent_action_service(repo).apply_agent_action(
        action={"agent_id": "backend"}, valid=False, error="boom"
    )
    assert repo.read().agent_runtimes["backend"].no_progress_count == 1

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Self serve",
                    "description": "Backend assigns itself follow-up work.",
                    "parent_task_id": "parent",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.agent_runtimes["backend"].no_progress_count == 0


def test_context_clearing_is_disabled_by_default_for_idle_agents(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = _team_admin_service(repo).create_or_reprioritize_task(
        task_id="already-done",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Already done",
        description="Terminal task should not opt in automatically.",
        created_by_agent_id="lead",
        state=TeamTaskState.FINISHED,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is False
    assert runtime.context_rotation_pending is False


def test_manual_context_rotation_marks_pending_even_when_agent_has_work(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="active-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active work",
        description="Manual rotation is allowed with active work.",
        created_by_agent_id="lead",
    )

    state = _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
        request_note="controlled test",
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is False
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "manual"
    assert runtime.context_rotation_requested_by == "operator:test"
    assert runtime.context_rotation_request_note == "controlled test"
    assert runtime.next_wake_at == runtime.context_rotation_requested_at
    assert "Context continuity checkpoint" in str(runtime.last_handoff_summary)
    assert "controlled test" in str(runtime.last_handoff_summary)
    assert "active-work" not in str(runtime.last_handoff_summary)


def test_auto_context_rotation_marks_pending_when_enabled_agent_becomes_idle(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="finish-me",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Finish me",
        description="Work that will make the agent non-idle.",
        created_by_agent_id="lead",
    )

    state = _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )

    assert state.agent_runtimes["backend"].auto_context_clearing_enabled is True
    assert state.agent_runtimes["backend"].context_rotation_pending is False

    state = _team_admin_service(repo).update_task(task_id="finish-me", state=TeamTaskState.FINISHED)

    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "idle"
    assert runtime.next_wake_at == runtime.context_rotation_requested_at
    assert runtime.last_handoff_summary is not None
    assert "Context continuity checkpoint" in runtime.last_handoff_summary
    assert "Active tasks:" not in runtime.last_handoff_summary


def test_auto_context_rotation_waits_for_activity_after_fresh_context(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="finish-me",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Finish me",
        description="Work that will make the agent non-idle.",
        created_by_agent_id="lead",
    )
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    _team_admin_service(repo).update_task(task_id="finish-me", state=TeamTaskState.FINISHED)
    _state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="backend")
    assert len(leases) == 1
    dispatch_id = leases[0]["dispatch_id"]
    _team_runtime_service(repo).record_context_rotation_dispatch_started(
        agent_id="backend",
        dispatch_id=dispatch_id,
        thread_id="fresh-thread",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={"agent_id": "backend"},
        dispatch_id=dispatch_id,
    )

    assert result["success"] is True
    assert result["applied"] is False
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.last_context_rotated_at is not None
    assert runtime.no_progress_count == 1

    state = _team_admin_service(repo).create_or_reprioritize_task(
        task_id="post-rotation-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Post-rotation work",
        description="New work should allow a later idle rotation.",
        created_by_agent_id="lead",
    )
    assert state.agent_runtimes["backend"].context_rotation_pending is False

    state = _team_admin_service(repo).update_task(
        task_id="post-rotation-work",
        state=TeamTaskState.FINISHED,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "idle"


def test_auto_context_rotation_does_not_mark_pending_with_pending_inbox(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_operator_directive(
        team_id="team",
        recipient_agent_id="backend",
        message="Handle this first.",
        created_by="operator:test",
    )

    state = _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is True
    assert runtime.context_clearing_policy == AgentContextClearingPolicy.IDLE
    assert runtime.context_rotation_pending is False


def test_every_wake_context_policy_rotates_ready_task_wakes(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="active-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active work",
        description="Every wake policy should rotate when this wake is leased.",
        created_by_agent_id="lead",
    )
    state = _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )
    assert state.agent_runtimes["backend"].context_rotation_pending is False

    state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="backend", now=base_time)

    assert len(leases) == 1
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
    assert runtime.auto_context_clearing_enabled is True
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "every_wake"
    assert runtime.context_rotation_requested_at == base_time
    assert leases[0]["runtime"]["context_clearing_policy"] == "every_wake"
    assert leases[0]["runtime"]["context_rotation_pending"] is True
    assert leases[0]["runtime"]["context_rotation_reason"] == "every_wake"


def test_every_wake_context_policy_does_not_wake_idle_agent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    state = _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy="every_wake",
        set_context_clearing_policy=True,
    )
    assert state.agent_runtimes["backend"].context_clearing_policy == (
        AgentContextClearingPolicy.EVERY_WAKE
    )

    state, leases = _team_runtime_service(repo).lease_due_agents(agent_id="backend")

    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.context_rotation_reason is None


def test_waiting_review_deliverable_does_not_reschedule_producer(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Backend slice ready for review.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time + timedelta(minutes=20),
    )

    assert leases == []


def test_response_requested_inbox_can_wake_waiting_review_producer(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Backend slice ready for review.",
                }
            ],
        },
    )

    def _add_follow_up(state):
        state.inbox["follow-up"] = AgentInboxItem(
            id="follow-up",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=True,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_follow_up)

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "inbox"
    assert [item["id"] for item in leases[0]["inbox"]] == ["follow-up"]


def test_no_reply_inbox_does_not_wake_waiting_review_producer(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Backend slice ready for review.",
                }
            ],
        },
    )

    def _add_no_reply_feedback(state):
        state.inbox["no-reply"] = AgentInboxItem(
            id="no-reply",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=False,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_no_reply_feedback)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        low_priority_wake_after_cycles=1,
    )

    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at is None
    assert runtime.low_priority_batch_cycle_count == 0


def test_normal_no_reply_inbox_wakes_recipient(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _add_normal_note(state):
        state.inbox["normal-note"] = AgentInboxItem(
            id="normal-note",
            recipient_agent_id="backend",
            sender_agent_id="worker",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=False,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return state

    repo.update(_add_normal_note)

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "inbox"
    assert [item["id"] for item in leases[0]["inbox"]] == ["normal-note"]


def test_manager_wakes_when_team_has_open_tasks_and_no_member_is_wakeable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="stalled-task")
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-stalled",
                    "task_id": "stalled-task",
                    "summary": "Blocked slice for review.",
                }
            ],
        },
    )
    assert result["success"] is True
    state = _team_admin_service(repo).record_review_request(
        review_request_id="review-stalled",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="manager",
        expected_revision=state.revision,
    )
    assert state.tasks["stalled-task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert any(
        item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE and item.task_id == "stalled-task"
        for item in state.inbox.values()
    )

    def _clear_deliverable_notices(state):
        state.inbox = {
            item_id: item
            for item_id, item in state.inbox.items()
            if not (
                item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
                and item.task_id == "stalled-task"
            )
        }
        return state

    def _throttle_manager(state):
        runtime = state.agent_runtimes.get("manager", AgentRuntime(agent_id="manager"))
        runtime.no_progress_count = 10
        state.agent_runtimes["manager"] = runtime
        return state

    def _add_manager_non_wakeable_inbox(state):
        state.inbox["manager-note"] = AgentInboxItem(
            id="manager-note",
            recipient_agent_id="manager",
            sender_agent_id="lead",
            task_id="stalled-task",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=False,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_clear_deliverable_notices)
    repo.update(_throttle_manager)
    repo.update(_add_manager_non_wakeable_inbox)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]
    assert leases[0]["reason"] == "review"
    assert state.agent_runtimes["manager"].wake_reason == "review"
    assert leases[0]["tasks"] == []
    assert leases[0]["inbox"][0]["id"] == "manager-note"
    assert leases[0]["inbox"][0]["response_requested"] is False
    assert [review["id"] for review in leases[0]["reviews"]] == ["review-stalled"]
    assert leases[0]["dispatch"]["review_request_ids"] == ["review-stalled"]
    assert "manager_fallback_summary" not in leases[0]
    json.dumps(leases[0])


def test_manager_fallback_skips_pending_deliverable_on_accepted_task(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="done-task")
    _team_admin_service(repo).record_deliverable(
        deliverable_id="deliverable-pending",
        task_id="done-task",
        producer_agent_id="backend",
        summary="Consolidated feature branch team/team/features/done-task-backend",
        state=TeamRecordState.PENDING,
    )

    def _mark_task_terminal(state):
        state.tasks["done-task"].state = TeamTaskState.ACCEPTED
        return state

    repo.update(_mark_task_terminal)
    lease_at = datetime.now(UTC)
    state, leases = _team_runtime_service(repo).lease_due_agents(
        now=lease_at,
    )

    assert leases == []
    assert state.agent_runtimes["manager"].active_dispatch_id is None
    assert state.agent_runtimes["manager"].wake_reason is None
    assert state.tasks["done-task"].state == TeamTaskState.ACCEPTED
    assert state.deliverables["deliverable-pending"].state == TeamRecordState.PENDING


def test_every_wake_context_policy_preserves_low_priority_batching(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=2,
    )
    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.low_priority_batch_cycle_count == 1

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time + timedelta(minutes=15),
        low_priority_wake_after_cycles=2,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "low_priority_inbox"
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "every_wake"
    assert leases[0]["runtime"]["context_rotation_pending"] is True


def test_low_priority_inbox_wakes_after_configured_wake_cycles(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=3,
    )
    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at == base_time
    assert runtime.low_priority_batch_cycle_count == 1
    assert runtime.low_priority_batch_due_after_cycles == 3

    def _add_second_low_inbox(state):
        state.inbox["low-2"] = AgentInboxItem(
            id="low-2",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_second_low_inbox)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=15),
        low_priority_wake_after_cycles=3,
    )
    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at == base_time
    assert runtime.low_priority_batch_cycle_count == 2

    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=30),
        low_priority_wake_after_cycles=3,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "low_priority_inbox"
    assert [item["id"] for item in leases[0]["inbox"]] == ["low-1", "low-2"]
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at is None
    assert runtime.low_priority_batch_cycle_count == 0
    assert runtime.low_priority_batch_due_after_cycles is None


def test_normal_inbox_cancels_low_priority_batch_countdown(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)
    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=4,
    )
    assert leases == []
    assert state.agent_runtimes["backend"].low_priority_batch_cycle_count == 1

    def _add_normal_inbox(state):
        state.inbox["normal-1"] = AgentInboxItem(
            id="normal-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_normal_inbox)
    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=15),
        low_priority_wake_after_cycles=4,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "inbox"
    assert [item["id"] for item in leases[0]["inbox"]] == ["low-1", "normal-1"]
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at is None
    assert runtime.low_priority_batch_cycle_count == 0


def test_handled_low_priority_inbox_clears_batch_countdown(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)
    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=2,
    )
    assert leases == []
    assert state.agent_runtimes["backend"].low_priority_batch_cycle_count == 1

    def _handle_low_inbox(state):
        state.inbox["low-1"].state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_low_inbox)
    state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=15),
        low_priority_wake_after_cycles=2,
    )

    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at is None
    assert runtime.low_priority_batch_cycle_count == 0


def test_operator_task_creation_resets_no_progress_and_wakes_owner(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _throttle_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            no_progress_count=10,
        )
        return state

    repo.update(_throttle_manager)

    state = _team_admin_service(repo).create_or_reprioritize_task(
        task_id="manager-delegate-prototype",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Delegate prototype",
        description="Create a project-lead task for the prototype.",
        created_by_agent_id="manager",
    )

    runtime = state.agent_runtimes["manager"]
    assert runtime.no_progress_count == 0
    assert runtime.next_wake_at is None

    _state, leases = _team_runtime_service(repo).lease_due_agents(max_no_progress_count=3)

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]


def test_create_operator_directive_wakes_manager_inbox(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _throttle_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            no_progress_count=10,
        )
        return state

    repo.update(_throttle_manager)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="status-sweep",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Status sweep",
        description="Ask implementers for status.",
        created_by_agent_id="manager",
    )

    state, item = _team_admin_service(repo).create_operator_directive(
        team_id="team",
        message="Ask the lead for a status report on next Team dispatch.",
        title="Status report request",
        project_id="project",
        task_id="status-sweep",
        source="discord",
        source_message_id="discord-msg-1",
        created_by="operator:jonathan",
        requesting_session_id="session-manager",
    )

    assert item.item_type == TeamInboxItemType.OPERATOR_DIRECTIVE
    assert item.recipient_agent_id == "manager"
    assert item.task_id == "status-sweep"
    assert item.payload == {
        "title": "Status report request",
        "message": "Ask the lead for a status report on next Team dispatch.",
        "team_id": "team",
        "project_id": "project",
        "source": "discord",
        "source_message_id": "discord-msg-1",
        "created_by": "operator:jonathan",
        "requesting_session_id": "session-manager",
    }
    runtime = state.agent_runtimes["manager"]
    assert runtime.no_progress_count == 0
    assert runtime.next_wake_at is None

    _state, leases = _team_runtime_service(repo).lease_due_agents(max_no_progress_count=3)

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]
    assert leases[0]["reason"] == "inbox"
    assert leases[0]["inbox"][0]["id"] == item.id


def test_create_operator_directive_rejects_non_manager_session(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValueError, match="team manager session"):
        _team_admin_service(repo).create_operator_directive(
            team_id="team",
            message="Ask for status.",
            requesting_session_id="session-lead",
        )


def test_create_operator_directive_accepts_governance_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    state, item = _team_admin_service(repo).create_operator_directive(
        team_id="team",
        message="Confirm the exact blocker and avoid retrying failed promotion.",
        title="Governance blocker check",
        requesting_session_id="session-governance",
    )

    assert item.item_type == TeamInboxItemType.OPERATOR_DIRECTIVE
    assert item.recipient_agent_id == "manager"
    assert item.payload["title"] == "Governance blocker check"
    assert "created_by" not in item.payload
    assert item.payload["requesting_session_id"] == "session-governance"
    assert state.agent_runtimes["manager"].next_wake_at is None


def test_mark_governance_agenda_handled_accepts_governance_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    state, item = _command_service(repo).mark_governance_agenda_handled_by_session(
        agenda_item_id="agenda",
        requesting_session_id="session-governance",
        expected_revision=repo.read().revision,
    )

    assert item.state == GovernanceAgendaItemState.HANDLED
    assert state.governance_agenda["agenda"].state == GovernanceAgendaItemState.HANDLED

    state, item = _command_service(repo).mark_governance_agenda_handled_by_session(
        agenda_item_id="agenda",
        requesting_session_id="session-governance",
        expected_revision=state.revision,
    )
    assert item.state == GovernanceAgendaItemState.HANDLED
    assert state.governance_agenda["agenda"].state == GovernanceAgendaItemState.HANDLED


def test_mark_governance_agenda_handled_rejects_non_governance_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="active governance manager session"):
        _command_service(repo).mark_governance_agenda_handled_by_session(
            agenda_item_id="agenda",
            requesting_session_id="session-manager",
            expected_revision=repo.read().revision,
        )


def test_mark_governance_agenda_handled_rejects_other_group_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="other-governance-manager",
        session_id="session-other-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="other-portfolio",
        name="Other Portfolio",
        manager_agent_id="other-governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="active governance manager session"):
        _command_service(repo).mark_governance_agenda_handled_by_session(
            agenda_item_id="agenda",
            requesting_session_id="session-other-governance",
            expected_revision=repo.read().revision,
        )


@pytest.mark.parametrize("status", [AgentStatus.PAUSED, AgentStatus.ARCHIVED])
def test_mark_governance_agenda_handled_rejects_inactive_governance_manager_session(
    tmp_path: Path,
    status: AgentStatus,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    def _mark_inactive(state):
        state.agents["governance-manager"].status = status
        return state

    repo.update(_mark_inactive)

    with pytest.raises(ValueError, match="active governance manager session"):
        _command_service(repo).mark_governance_agenda_handled_by_session(
            agenda_item_id="agenda",
            requesting_session_id="session-governance",
            expected_revision=repo.read().revision,
        )


@pytest.mark.parametrize("status", [AgentStatus.PAUSED, AgentStatus.ARCHIVED])
def test_create_operator_directive_rejects_inactive_governance_manager_session(
    tmp_path: Path,
    status: AgentStatus,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    def _mark_inactive(state):
        state.agents["governance-manager"].status = status
        return state

    repo.update(_mark_inactive)

    with pytest.raises(ValueError, match="governance manager session"):
        _team_admin_service(repo).create_operator_directive(
            team_id="team",
            message="This should not route after the governance manager is inactive.",
            requesting_session_id="session-governance",
        )


def test_operator_task_creation_can_use_operator_actor(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = _team_admin_service(repo).create_or_reprioritize_task(
        task_id="manager-delegate-prototype",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Delegate prototype",
        description="Create a project-lead task for the prototype.",
        created_by_actor="operator:jonathan",
    )

    task = state.tasks["manager-delegate-prototype"]
    assert task.created_by_agent_id is None
    assert task.created_by_actor == "operator:jonathan"


def _set_operator_target(repo: TeamStateRepository, target: str) -> None:
    def _mutate(state):
        state.teams["team"].operator_communication_target_id = target
        return state

    repo.update(_mutate)


def test_apply_agent_action_manager_operator_escalation_emits_event(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "operator_escalations": [
                {
                    "title": "Need a decision",
                    "message": "Blocked waiting on operator input.",
                    "urgency": "high",
                    "project_id": "project",
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["operator_escalation_event_ids"]) == 1
    assert state.agent_runtimes["manager"].no_progress_count == 0
    event = next(
        event
        for event in result["events"]
        if event["event_type"] == "team.operator.escalation_requested"
    )
    assert event["event_type"] == "team.operator.escalation_requested"
    assert event["payload"] == {
        "team_id": "team",
        "manager_agent_id": "manager",
        "communication_target_id": "operator:ops-channel",
        "title": "Need a decision",
        "message": "Blocked waiting on operator input.",
        "urgency": "high",
        "project_id": "project",
    }
    wake_events = [
        event for event in result["events"] if event["event_type"] == "team.agent.wake_requested"
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["agent_id"] == "manager"
    assert wake_events[0]["payload"]["reason"] == "inbox"
    escalation_items = [
        item for item in state.inbox.values() if item.item_type == TeamInboxItemType.ESCALATION
    ]
    assert len(escalation_items) == 1
    audit = escalation_items[0]
    assert audit.recipient_agent_id == "manager"
    assert audit.payload["communication_target_id"] == "operator:ops-channel"
    assert audit.payload["urgency"] == "high"


def test_apply_agent_action_rejects_non_manager_operator_escalation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "operator_escalations": [{"title": "Hi", "message": "Please look."}],
        },
    )

    assert result["success"] is False
    assert result["operator_escalation_event_ids"] == []
    assert result["events"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert all(item.item_type != TeamInboxItemType.ESCALATION for item in state.inbox.values())


def test_apply_agent_action_escalation_without_target_records_failure(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "operator_escalations": [{"title": "Hi", "message": "Please look."}],
        },
    )

    assert result["success"] is False
    assert result["operator_escalation_event_ids"] == []
    assert result["events"] == []
    assert any("operator_communication_target_id" in error for error in result["errors"])
    assert state.inbox == {}
    record = next(iter(state.action_records.values()))
    assert record.success is False


def _seed_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    owner_agent_id: str = "backend",
) -> None:
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id=owner_agent_id,
        project_id="project",
        priority=1,
        title=f"Task {task_id}",
        description="Work to deliver.",
        created_by_agent_id="lead",
    )


def _seed_deliverable(
    repo: TeamStateRepository,
    *,
    deliverable_id: str,
    task_id: str,
    producer_agent_id: str = "backend",
) -> None:
    _team_admin_service(repo).record_deliverable(
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id=producer_agent_id,
        summary="Deliverable summary.",
    )


def _seed_finished_reviewed_deliverable(
    repo: TeamStateRepository,
    *,
    task_id: str = "task",
    deliverable_id: str = "deliverable",
    review_id: str = "review-original",
    owner_agent_id: str = "backend",
) -> None:
    _seed_task(repo, task_id=task_id, owner_agent_id=owner_agent_id)
    _seed_deliverable(
        repo,
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id=owner_agent_id,
    )

    def _accept(state):
        now = datetime.now(UTC)
        task = state.tasks[task_id]
        deliverable = state.deliverables[deliverable_id]
        review = ReviewRequest(
            id=review_id,
            deliverable_id=deliverable_id,
            reviewer_agent_id="worker",
            requested_by_agent_id="lead",
            state=TeamRecordState.ACCEPTED,
            instructions="Verify the original deliverable.",
            created_at=now,
            decision_at=now,
        )
        state.review_requests[review.id] = review
        deliverable.review_request_ids = [review.id]
        deliverable.state = TeamRecordState.ACCEPTED
        task.state = TeamTaskState.FINISHED
        task.state_entered_at = now
        task.updated_at = now
        return state

    repo.update(_accept)


def test_finished_task_rework_recovery_preserves_history_and_requires_fresh_reviews(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_finished_reviewed_deliverable(repo)
    admin = _team_admin_service(repo)
    admin.create_review_profile(
        profile_id="profile-review",
        team_id="team",
        project_id="project",
        name="Implementation Review",
        expected_revision=repo.read().revision,
    )
    admin.create_review_profile(
        profile_id="profile-testing",
        team_id="team",
        project_id="project",
        name="Testing Review",
        expected_revision=repo.read().revision,
    )

    def _add_required_review_profiles(state):
        now = datetime.now(UTC)
        state.review_requests["review-original"].review_profile_id = (
            "profile-review"
        )
        state.review_requests["review-original-testing"] = ReviewRequest(
            id="review-original-testing",
            deliverable_id="deliverable",
            reviewer_agent_id="lead",
            requested_by_agent_id="lead",
            state=TeamRecordState.ACCEPTED,
            review_profile_id="profile-testing",
            instructions="Verify the original test evidence.",
            created_at=now,
            decision_at=now,
        )
        state.deliverables["deliverable"].review_request_ids.append(
            "review-original-testing"
        )
        return state

    repo.update(_add_required_review_profiles)
    original_review = repo.read().review_requests["review-original"].model_dump(
        mode="json"
    )
    original_testing_review = repo.read().review_requests[
        "review-original-testing"
    ].model_dump(mode="json")
    assert require_release_scope(
        repo.read(),
        project_id="project",
        deliverable_ids=["deliverable"],
    )["valid"] is True

    state, reopened = _command_service(repo).reopen_task_for_rework_command(
        session_id="session-lead",
        task_id="task",
        accepted_deliverable_id="deliverable",
        reason="The accepted artifact has no immutable Git provenance.",
        request_id="request-reopen-finished-task",
    )

    assert reopened["success"] is True
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
    assert (
        TaskAssessmentService(state).assess("task").resolution_reason
        == "accepted_deliverable_requires_replacement"
    )
    predecessor = state.deliverables["deliverable"]
    assert predecessor.state == TeamRecordState.ACCEPTED
    assert predecessor.superseded_by_deliverable_id is None
    assert predecessor.rework_requested_by_agent_id == "lead"
    assert predecessor.rework_reason == (
        "The accepted artifact has no immutable Git provenance."
    )
    assert state.review_requests["review-original"].model_dump(
        mode="json"
    ) == original_review
    assert state.review_requests["review-original-testing"].model_dump(
        mode="json"
    ) == original_testing_review
    assert reopened["required_review_profile_ids"] == [
        "profile-review",
        "profile-testing",
    ]
    assert len(reopened["created_inbox_item_ids"]) == 1
    rework_inbox_id = reopened["created_inbox_item_ids"][0]
    rework_item = state.inbox[rework_inbox_id]
    assert rework_item.recipient_agent_id == "backend"
    assert rework_item.priority == TeamInboxPriority.HIGH
    assert rework_item.payload["kind"] == "accepted_deliverable_rework_requested"
    assert rework_item.payload["supersedes_deliverable_id"] == "deliverable"
    assert rework_item.payload["required_tool"] == "team_submit_deliverable_for_review"
    wake_events = [
        event
        for event in reopened["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
        and event["payload"]["agent_id"] == "backend"
    ]
    assert len(wake_events) == 1
    audit = repo.list_dispatch_activity(
        tool_name="team_reopen_task_for_rework",
        request_id="request-reopen-finished-task",
    )
    assert len(audit) == 1
    assert audit[0].status == TeamDispatchActivityStatus.SUCCESS

    replayed_state, replayed = _command_service(
        repo
    ).reopen_task_for_rework_command(
        session_id="session-lead",
        task_id="task",
        accepted_deliverable_id="deliverable",
        reason="The accepted artifact has no immutable Git provenance.",
        request_id="request-reopen-finished-task",
    )
    assert replayed["success"] is True
    assert replayed["created_inbox_item_ids"] == [rework_inbox_id]
    assert len(
        [
            item
            for item in replayed_state.inbox.values()
            if item.payload.get("kind")
            == "accepted_deliverable_rework_requested"
        ]
    ) == 1

    state, stale_review = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-old-deliverable-again",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Do not reuse the already accepted candidate.",
                }
            ],
        },
    )
    assert stale_review["success"] is False
    assert any(
        "explicitly reopened for same-task replacement" in error
        for error in stale_review["errors"]
    )
    assert "review-old-deliverable-again" not in state.review_requests
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED

    with pytest.raises(TaskAssessmentBoundaryError) as release_error:
        require_release_scope(
            state,
            project_id="project",
            deliverable_ids=["deliverable"],
        )
    assert release_error.value.code == "release_scope_invalid"
    assert (
        "release_task_not_resolved"
        in release_error.value.correction_context["errors"]["deliverable"]
    )

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-finished-task-rework",
    )
    state, direct_finish = _command_service(repo).update_task_command(
        session_id="session-backend",
        task_id="task",
        state="finished",
        request_id="request-bypass-finish",
    )
    assert direct_finish["success"] is False
    assert direct_finish["errors"][0]["code"] == (
        "accepted_deliverable_rework_pending"
    )
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS

    state, unrelated = _command_service(
        repo
    ).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-unrelated",
            "task_id": "task",
            "summary": "An unrelated product cannot close the recovery.",
        },
        reviews=[{"reviewer_agent_id": "worker"}],
        request_id="request-unrelated-recovery-deliverable",
    )
    assert unrelated["success"] is False
    assert unrelated["errors"][0]["code"] == (
        "accepted_deliverable_rework_requires_replacement"
    )
    assert state.deliverables["deliverable"].superseded_by_deliverable_id is None

    state, direct_submission = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="task",
        id="deliverable-direct",
        summary="A direct handoff must not reuse the old review.",
        supersedes_deliverable_id="deliverable",
        request_id="request-direct-recovery-deliverable",
    )
    assert direct_submission["success"] is False
    assert direct_submission["errors"][0]["code"] == (
        "accepted_deliverable_rework_requires_fresh_reviews"
    )
    assert "deliverable-direct" not in state.deliverables

    state, submitted = _command_service(
        repo
    ).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-replacement",
            "task_id": "task",
            "summary": "Replacement with immutable provenance.",
            "supersedes_deliverable_id": "deliverable",
        },
        reviews=[
            {
                "id": "review-replacement",
                "reviewer_agent_id": "worker",
                "review_profile_id": "profile-review",
                "instructions": "Review the replacement independently.",
            }
        ],
        request_id="request-reviewed-recovery-deliverable",
    )
    assert submitted["success"] is False
    assert submitted["errors"][0]["code"] == (
        "accepted_deliverable_rework_review_coverage_incomplete"
    )
    assert submitted["correction_context"]["missing_review_profile_ids"] == [
        "profile-testing"
    ]

    state, submitted = _command_service(
        repo
    ).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-replacement",
            "task_id": "task",
            "summary": "Replacement with immutable provenance.",
            "supersedes_deliverable_id": "deliverable",
        },
        reviews=[
            {
                "id": "review-replacement",
                "reviewer_agent_id": "worker",
                "review_profile_id": "profile-review",
                "instructions": "Review the replacement independently.",
            },
            {
                "id": "review-replacement-testing",
                "reviewer_agent_id": "lead",
                "review_profile_id": "profile-testing",
                "instructions": "Review the replacement test evidence.",
            },
        ],
        request_id="request-reviewed-recovery-deliverable-complete",
    )
    assert submitted["success"] is True
    assert submitted["created_review_request_ids"] == [
        "review-replacement",
        "review-replacement-testing",
    ]
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.deliverables["deliverable"].superseded_by_deliverable_id == (
        "deliverable-replacement"
    )
    assert (
        state.deliverables["deliverable-replacement"].supersedes_deliverable_id
        == "deliverable"
    )
    assert state.review_requests["review-original"].model_dump(
        mode="json"
    ) == original_review
    assert state.review_requests["review-original-testing"].model_dump(
        mode="json"
    ) == original_testing_review
    assert state.review_requests["review-replacement"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-review-replacement",
    )
    state, reviewed = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id="review-replacement",
        state="accepted",
        request_id="request-accept-replacement-review",
    )
    assert reviewed["success"] is True
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-review-replacement-testing",
    )
    state, tested = _command_service(repo).decide_review_command(
        session_id="session-lead",
        review_request_id="review-replacement-testing",
        state="accepted",
        request_id="request-accept-replacement-testing-review",
    )
    assert tested["success"] is True
    assert state.review_requests["review-original"].model_dump(
        mode="json"
    ) == original_review
    assert state.review_requests["review-original-testing"].model_dump(
        mode="json"
    ) == original_testing_review
    assert state.review_requests["review-replacement"].state == TeamRecordState.ACCEPTED
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert (
        state.deliverables["deliverable-replacement"].state
        == TeamRecordState.ACCEPTED
    )
    assert state.tasks["task"].state == TeamTaskState.FINISHED
    assert require_release_scope(
        state,
        project_id="project",
        deliverable_ids=["deliverable-replacement"],
    )["valid"] is True
    with pytest.raises(TaskAssessmentBoundaryError):
        require_release_scope(
            state,
            project_id="project",
            deliverable_ids=["deliverable"],
        )

    persisted = repo.read()
    assert persisted.deliverables["deliverable"].rework_requested_at is not None
    assert (
        persisted.deliverables["deliverable"].rework_requested_by_agent_id
        == "lead"
    )
    assert persisted.deliverables["deliverable"].rework_reason == (
        "The accepted artifact has no immutable Git provenance."
    )


@pytest.mark.parametrize(
    ("caller_agent_id", "caller_session_id", "expected_success"),
    [
        ("backend", "session-backend", True),
        ("lead", "session-lead", True),
        ("manager", "session-manager", True),
        ("worker", "session-worker", False),
        ("other-lead", "session-other-lead", False),
        ("other-manager", "session-other-manager", False),
    ],
)
def test_finished_task_rework_recovery_authorization(
    tmp_path: Path,
    caller_agent_id: str,
    caller_session_id: str,
    expected_success: bool,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    if caller_agent_id == "other-lead":
        admin.create_project(
            project_id="other-project",
            team_id="team",
            name="Other Project",
            lead_agent_id="other-lead",
            lead_session_id="session-other-lead",
            goals=[ProjectGoal(id="other-goal", description="Other work")],
            expected_revision=repo.read().revision,
        )
    elif caller_agent_id == "other-manager":
        admin.create_team(
            team_id="other-team",
            name="Other Team",
            manager_agent_id="other-manager",
            manager_session_id="session-other-manager",
            expected_revision=repo.read().revision,
        )
    _seed_finished_reviewed_deliverable(repo)

    state, result = _command_service(repo).reopen_task_for_rework_command(
        session_id=caller_session_id,
        task_id="task",
        accepted_deliverable_id="deliverable",
        reason=f"Recovery requested by {caller_agent_id}.",
        request_id=f"request-reopen-{caller_agent_id}",
    )

    assert result["success"] is expected_success
    if expected_success:
        assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
        assert (
            state.deliverables["deliverable"].rework_requested_by_agent_id
            == caller_agent_id
        )
    else:
        assert result["errors"][0]["code"] == "task_rework_reopen_forbidden"
        assert state.tasks["task"].state == TeamTaskState.FINISHED
        assert state.deliverables["deliverable"].rework_requested_at is None
        assert state.inbox == {}


def _lease_review_handoff_dispatch(
    repo: TeamStateRepository,
    *,
    deliverable_id: str,
    agent_id: str = "lead",
    dispatch_id: str,
) -> None:
    def _seed_handoff(state):
        deliverable = state.deliverables[deliverable_id]
        task = state.tasks[deliverable.task_id]
        notice_id = f"{dispatch_id}-deliverable-notice"
        payload = {
            "kind": "superior_acceptance_requested",
            "deliverable_id": deliverable.id,
            "task_id": task.id,
            "producer_agent_id": deliverable.producer_agent_id,
            "acceptance_recipient_agent_id": agent_id,
            "summary": deliverable.summary,
            "artifact_refs": list(deliverable.artifact_refs),
            "available_decisions": ["accepted", "rejected"],
            "available_alternatives": ["request_review", "report_blocker"],
        }
        if deliverable.verification is not None:
            payload["verification"] = deliverable.verification.model_dump(mode="json")
        state.inbox[notice_id] = AgentInboxItem(
            id=notice_id,
            recipient_agent_id=agent_id,
            sender_agent_id=deliverable.producer_agent_id,
            task_id=task.id,
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            response_requested=True,
            priority=TeamInboxPriority.NORMAL,
            payload=payload,
        )
        return state

    repo.update(_seed_handoff)
    _lease_agent_dispatch(
        repo,
        agent_id=agent_id,
        dispatch_id=dispatch_id,
    )


def test_apply_agent_action_review_request_creates_pending_review(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    # lead leads the project, so it may request review of backend's deliverable.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Check the slice carefully.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["created_review_request_ids"] == ["review-1"]
    review = state.review_requests["review-1"]
    assert review.reviewer_agent_id == "worker"
    assert review.requested_by_agent_id == "lead"
    assert review.requested_by_actor is None
    assert review.state == TeamRecordState.PENDING
    assert "review-1" in state.deliverables["deliverable"].review_request_ids
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    inbox_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.STATUS_REQUEST
        and item.recipient_agent_id == "worker"
    ]
    assert len(inbox_items) == 1
    assert inbox_items[0].payload["review_request_id"] == "review-1"
    assert inbox_items[0].payload["instructions"] == "Check the slice carefully."


def test_request_review_command_returns_next_action_hint(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-review",
    )
    _state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable",
        task_id="task",
        summary="Ready for formal review.",
        verification={
            "status": "partial",
            "summary": "Only schema checks ran before review.",
        },
        request_id="submit-before-request-review",
    )
    assert submitted["success"] is True

    state, result = _command_service(repo).request_review_command(
        session_id="session-backend",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Check the slice carefully.",
        request_id="request-review",
    )

    assert result["success"] is True
    assert result["created_review_request_ids"] == [result["review_request_id"]]
    assert any("wait for the assigned reviewer" in hint for hint in result["next_action_hints"])
    assert state.review_requests[result["review_request_id"]].reviewer_agent_id == "worker"
    review_item = next(
        item
        for item in state.inbox.values()
        if item.payload.get("review_request_id") == result["review_request_id"]
    )
    assert review_item.payload["verification"] == {
        "status": "partial",
        "summary": "Only schema checks ran before review.",
        "evidence_refs": [],
    }


def test_blocked_review_retry_preserves_attempt_and_reopens_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-retry-source",
    )
    first_review_id = requested["review_request_id"]

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-block-review")
    state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=first_review_id,
        state="blocked",
        block_kind="system",
        findings=["The review environment was unavailable."],
        request_id="request-review-blocked",
    )
    assert blocked["success"] is True
    assert state.review_requests[first_review_id].block_kind == ReviewBlockKind.SYSTEM
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-retry-review")
    state, retried = _command_service(repo).retry_review_command(
        session_id="session-lead",
        review_request_id=first_review_id,
        reason="The review environment has recovered.",
        request_id="request-review-retry",
    )

    successor_id = retried["replacement_review_request_id"]
    original = state.review_requests[first_review_id]
    successor = state.review_requests[successor_id]
    assert original.state == TeamRecordState.BLOCKED
    assert original.superseded_by_review_request_id == successor_id
    assert successor.supersedes_review_request_id == first_review_id
    assert successor.state == TeamRecordState.PENDING
    assert successor.retry_reason == "The review environment has recovered."
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert all(
        blocker.source_id != first_review_id or blocker.state == TeamBlockerState.RESOLVED
        for blocker in state.blockers.values()
    )
    wake_events = [
        event
        for event in retried["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["agent_id"] == "worker"
    assert wake_events[0]["payload"]["reason"] == "inbox"
    wake_outbox = repo.list_outbox_records(
        intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE,
    )
    wake_outbox_by_id = {record.id: record for record in wake_outbox}
    assert len(retried["outbox_record_ids"]) == 1
    retry_wake = wake_outbox_by_id[retried["outbox_record_ids"][0]]
    assert retry_wake.payload["payload"]["agent_id"] == "worker"


def test_accepted_review_wakes_owners_of_newly_ready_dependent_tasks(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="foundation", owner_agent_id="backend")
    _seed_task(repo, task_id="dependent-a", owner_agent_id="backend")
    _seed_task(repo, task_id="dependent-b", owner_agent_id="backend")

    def _add_dependency(state):
        state.tasks["dependent-a"].dependency_task_ids = ["foundation"]
        state.tasks["dependent-b"].dependency_task_ids = ["foundation"]
        return state

    repo.update(_add_dependency)
    _seed_deliverable(
        repo,
        deliverable_id="foundation-deliverable",
        task_id="foundation",
        producer_agent_id="backend",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="foundation-deliverable",
        dispatch_id="dispatch-request-foundation-review",
    )
    _state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="foundation-deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the foundation.",
        request_id="request-foundation-review",
    )
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-accept-foundation-review",
    )

    state, decided = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=requested["review_request_id"],
        state="accepted",
        request_id="accept-foundation-review",
    )

    assert decided["success"] is True
    assert state.tasks["foundation"].state == TeamTaskState.FINISHED
    assert state.dependency_status("dependent-a") == "satisfied"
    assert state.dependency_status("dependent-b") == "satisfied"
    wake_events = [
        event
        for event in decided["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
        and event["payload"]["reason"] == "task_ready"
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["agent_id"] == "backend"
    assert wake_events[0]["payload"]["reason"] == "task_ready"
    wake_outbox = repo.list_outbox_records(
        intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE,
    )
    assert decided["outbox_record_ids"][-1] == wake_outbox[-1].id
    assert wake_outbox[-1].payload["payload"]["agent_id"] == "backend"


def test_blocked_legacy_git_review_retry_pins_repaired_deliverable_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-legacy-source",
    )
    first_review_id = requested["review_request_id"]
    assert state.review_requests[first_review_id].reviewed_commit_sha is None

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-block-review")
    state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=first_review_id,
        state="blocked",
        block_kind="dependency",
        findings=["The candidate commit was not pinned."],
        request_id="request-review-legacy-blocked",
    )
    assert blocked["success"] is True

    candidate_commit = "5ece496c1684273d2502af91655c4d5a143269c4"
    _seed_git_work_context(
        repo,
        context_id="legacy-context",
        dispatch_id=None,
        commit_sha=candidate_commit,
        context_state=GitWorkContextState.SUBMITTED,
    )

    def _repair_deliverable_provenance(state):
        state.deliverables["deliverable"].git_refs = DeliverableGitRefs(
            branch_context_id="legacy-context",
            agent_branch="agents/session-backend/task",
            commit_sha=candidate_commit,
            submitted_commit_sha=candidate_commit,
        )
        return state

    repo.update(_repair_deliverable_provenance)
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-retry-review")
    state, retried = TeamReviewCommandService(
        repo,
        git_candidate_commit_resolver=lambda **_kwargs: candidate_commit,
    ).retry_review_command(
        session_id="session-lead",
        review_request_id=first_review_id,
        reason="The deliverable candidate provenance has been repaired.",
        request_id="request-review-legacy-retry",
    )

    successor = state.review_requests[retried["replacement_review_request_id"]]
    assert retried["success"] is True
    assert successor.reviewed_commit_sha == candidate_commit
    assert successor.supersedes_review_request_id == first_review_id


def test_blocked_git_review_retry_rejects_non_authoritative_pinned_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
    )
    supplied_commit = "127450960b913c94b03b20b00b67de93287ac311"
    authoritative_commit = "127450960be30a2b7c7966883a09252b70769748"
    _seed_git_work_context(
        repo,
        context_id="ctx-task",
        dispatch_id=None,
        commit_sha=supplied_commit,
        context_state=GitWorkContextState.SUBMITTED,
    )

    def _attach_invalid_legacy_provenance(state):
        state.deliverables["deliverable"].git_refs = DeliverableGitRefs(
            branch_context_id="ctx-task",
            agent_branch="agents/session-backend/task",
            commit_sha=supplied_commit,
            submitted_commit_sha=supplied_commit,
        )
        return state

    repo.update(_attach_invalid_legacy_provenance)
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the exact submitted commit.",
        request_id="request-review-invalid-legacy-candidate",
    )
    review_id = requested["review_request_id"]
    assert state.review_requests[review_id].reviewed_commit_sha == supplied_commit

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-block-review")
    state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=review_id,
        state="blocked",
        block_kind="system",
        findings=["The submitted commit cannot be resolved."],
        request_id="block-review-invalid-legacy-candidate",
    )
    assert blocked["success"] is True
    open_blocker_ids = {
        blocker.id
        for blocker in state.blockers.values()
        if blocker.source_id == review_id
        and blocker.state == TeamBlockerState.OPEN
    }
    resolver_calls: list[dict[str, str]] = []

    def _resolve(**kwargs: str) -> str:
        resolver_calls.append(kwargs)
        return authoritative_commit

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-retry-review")
    state, retried = TeamReviewCommandService(
        repo,
        git_candidate_commit_resolver=_resolve,
    ).retry_review_command(
        session_id="session-lead",
        review_request_id=review_id,
        reason="Retry the same candidate now that the review worker is available.",
        request_id="retry-review-invalid-legacy-candidate",
    )

    assert retried["success"] is False
    assert retried["errors"][0]["code"] == "git_review_candidate_mismatch"
    assert retried["correction_context"]["reviewed_commit_sha"] == supplied_commit
    assert retried["correction_context"]["expected_commit_sha"] == authoritative_commit
    assert retried["correction_context"]["required_action"] == (
        "submit_replacement_deliverable"
    )
    assert resolver_calls == [
        {
            "managed_repo_id": MANAGED_REPO_ID,
            "directory_path": "root",
            "agent_branch": "agents/session-backend/task",
        }
    ]
    assert state.review_requests[review_id].superseded_by_review_request_id is None
    assert state.review_requests[review_id].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED
    assert {
        blocker.id
        for blocker in state.blockers.values()
        if blocker.source_id == review_id
        and blocker.state == TeamBlockerState.OPEN
    } == open_blocker_ids


def test_generic_blocker_resolution_requires_formal_review_retry(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review-generic-recovery",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-generic-recovery-source",
    )
    review_id = requested["review_request_id"]

    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-block-review-generic-recovery",
    )
    state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=review_id,
        state="blocked",
        block_kind="system",
        findings=["The review environment was unavailable."],
        request_id="request-review-generic-recovery-blocked",
    )
    assert blocked["success"] is True
    blocker = next(
        blocker
        for blocker in state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST
        and blocker.source_id == review_id
        and blocker.state == TeamBlockerState.OPEN
    )
    original_decision_at = state.review_requests[review_id].decision_at

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-generic-review-recovery",
    )
    state, rejected = _command_service(repo).update_blocker_command(
        session_id="session-backend",
        blocker_id=blocker.id,
        action="resolve",
        note="The review environment is available again.",
        resolution_refs=["evidence:review-environment"],
        request_id="request-generic-review-recovery",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "review_recovery_command_required"
    assert rejected["errors"][0]["correction_context"]["recommended_tools"] == [
        "team_retry_review"
    ]
    assert state.blockers[blocker.id].state == TeamBlockerState.OPEN
    assert state.review_requests[review_id].state == TeamRecordState.BLOCKED
    assert state.review_requests[review_id].decision_at == original_decision_at
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-supported-review-recovery",
    )
    recovered, retried = _command_service(repo).retry_review_command(
        session_id="session-lead",
        review_request_id=review_id,
        reason="The review environment has recovered.",
        request_id="request-supported-review-recovery",
    )

    assert retried["success"] is True
    assert recovered.blockers[blocker.id].state == TeamBlockerState.RESOLVED
    assert recovered.review_requests[review_id].state == TeamRecordState.BLOCKED
    assert recovered.review_requests[review_id].decision_at == original_decision_at
    assert recovered.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert recovered.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_pending_review_withdrawal_preserves_history_and_routes_direct_acceptance(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
    )
    repo.update(
        lambda state: setattr(
            state.deliverables["deliverable"],
            "verification",
            DeliverableVerification(
                status="partial",
                summary="Focused checks passed; the full suite was not run.",
                evidence_refs=["artifact-space:/verification/focused-checks"],
            ),
        )
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-to-withdraw",
    )
    review_id = requested["review_request_id"]
    review_inbox_id = next(
        item.id
        for item in state.inbox.values()
        if item.payload.get("review_request_id") == review_id
    )

    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-withdraw-review")
    state, withdrawn = _command_service(repo).withdraw_review_command(
        session_id="session-backend",
        review_request_id=review_id,
        reason="The specialist review was requested for the wrong deliverable.",
        request_id="request-withdraw-review",
    )

    review = state.review_requests[review_id]
    assert withdrawn["success"] is True
    assert withdrawn["action"] == "withdrawn"
    assert review.state == TeamRecordState.PENDING
    assert review.decision_at is None
    assert review.retired_at is not None
    assert review.retired_by == "agent:backend"
    assert state.inbox[review_inbox_id].state == TeamInboxItemState.HANDLED
    assert state.deliverables["deliverable"].acceptance_recipient_agent_id == "lead"
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-stale-review")
    state, stale_decision = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=review_id,
        state="accepted",
        request_id="request-stale-decision-after-withdrawal",
    )
    assert stale_decision["success"] is False
    assert stale_decision["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.review_requests[review_id].decision_at is None

    notice = next(
        item
        for item in state.inbox.values()
        if item.state == TeamInboxItemState.PENDING
        and item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
        and item.payload.get("deliverable_id") == "deliverable"
    )
    assert notice.payload["verification"] == {
        "status": "partial",
        "summary": "Focused checks passed; the full suite was not run.",
        "evidence_refs": ["artifact-space:/verification/focused-checks"],
    }
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-direct-accept")
    state, accepted = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        decision="accepted",
        request_id="request-direct-accept-after-withdrawal",
        handled_inbox_item_ids=[notice.id],
    )

    assert accepted["success"] is True
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_pending_review_withdrawal_steers_an_already_leased_reviewer_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review-for-steering",
    )
    _state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Run the expensive browser suite.",
        request_id="request-review-for-steering",
    )
    review_id = requested["review_request_id"]
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-active-reviewer",
    )
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-withdraw-active-review",
    )
    steering_calls: list[dict[str, object]] = []

    state, withdrawn = TeamReviewCommandService(
        repo,
        review_dispatch_steering=lambda **kwargs: (
            steering_calls.append(dict(kwargs))
            or {
                "status": "requested",
                "dispatch_id": "dispatch-active-reviewer",
            }
        ),
    ).withdraw_review_command(
        session_id="session-backend",
        review_request_id=review_id,
        reason="The review was attached to the wrong candidate.",
        request_id="request-withdraw-active-review",
    )

    assert withdrawn["success"] is True
    assert withdrawn["review_dispatch_steering"] == {
        "status": "requested",
        "dispatch_id": "dispatch-active-reviewer",
    }
    assert len(steering_calls) == 1
    assert steering_calls[0]["state"] is state
    assert steering_calls[0]["review_request_id"] == review_id
    assert steering_calls[0]["action"] == "withdrawn"
    assert state.review_requests[review_id].retired_at is not None
    active_reviewer_dispatch = state.dispatches["dispatch-active-reviewer"]
    assert active_reviewer_dispatch.status == TeamDispatchStatus.LEASED
    assert review_id in active_reviewer_dispatch.review_request_ids
    assert not any(
        blocker.source_id == review_id
        and blocker.state == TeamBlockerState.OPEN
        for blocker in state.blockers.values()
    )

    def _fail_steering(**_kwargs):
        raise RuntimeError("worker steering is temporarily unavailable")

    replay_state, replayed = TeamReviewCommandService(
        repo,
        review_dispatch_steering=_fail_steering,
    ).withdraw_review_command(
        session_id="session-backend",
        review_request_id=review_id,
        reason="The review was attached to the wrong candidate.",
        request_id="request-withdraw-active-review",
    )

    assert replayed["success"] is True
    assert replayed["review_dispatch_steering"] == {
        "status": "failed",
        "error": "worker steering is temporarily unavailable",
    }
    assert replay_state.revision == state.revision
    assert replay_state.review_requests[review_id].retired_at is not None


def test_project_lead_can_retire_blocked_review_without_clearing_findings(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    _state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-to-retire",
    )
    review_id = requested["review_request_id"]
    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-block-review")
    _state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=review_id,
        state="blocked",
        block_kind="system",
        findings=["This review was attached to the wrong deliverable."],
        request_id="request-block-review-to-retire",
    )
    assert blocked["success"] is True
    blocker = next(
        item
        for item in repo.read().blockers.values()
        if item.source_id == review_id and item.state == TeamBlockerState.OPEN
    )
    assert "Retry this valid review" in blocker.details
    assert "may retire it only if the attempt is erroneous" in blocker.details

    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-denied-retire")
    unchanged, denied = _command_service(repo).retire_review_command(
        session_id="session-backend",
        review_request_id=review_id,
        reason="Producer cannot retire a terminal review.",
        request_id="request-denied-retire",
    )
    assert denied["success"] is False
    assert unchanged.review_requests[review_id].retired_at is None

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-retire-review")
    state, retired = _command_service(repo).retire_review_command(
        session_id="session-lead",
        review_request_id=review_id,
        reason="The review attempt was attached in error.",
        request_id="request-retire-review",
    )

    review = state.review_requests[review_id]
    assert retired["success"] is True
    assert retired["action"] == "retired"
    assert review.state == TeamRecordState.BLOCKED
    assert review.retired_at is not None
    assert review.retired_by == "agent:lead"
    assert review.superseded_by_review_request_id is None
    assert retired["open_finding_ids"]
    assert any(
        finding.state == "open"
        for finding in state.deliverables["deliverable"].carried_findings
    )
    assert all(
        blocker.source_id != review_id or blocker.state == TeamBlockerState.RESOLVED
        for blocker in state.blockers.values()
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS


def test_retiring_rejected_review_preserves_owner_rework_notice(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-before-rejection",
    )
    review_id = requested["review_request_id"]
    assignment_id = requested["created_inbox_item_ids"][0]

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-reject-review")
    state, rejected = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=review_id,
        state="rejected",
        findings=["The evidence is incomplete."],
        request_id="request-reject-review",
    )
    rework_id = rejected["created_inbox_item_ids"][0]
    assert state.inbox[assignment_id].state == TeamInboxItemState.PENDING
    assert state.inbox[rework_id].payload["kind"] == "review_rejected"
    assert state.inbox[rework_id].state == TeamInboxItemState.PENDING

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-retire-rejected")
    state, retired = _command_service(repo).retire_review_command(
        session_id="session-lead",
        review_request_id=review_id,
        reason="The rejected review attempt was attached in error.",
        request_id="request-retire-rejected",
    )

    assert retired["success"] is True
    assert retired["closed_inbox_item_ids"] == [assignment_id]
    assert retired["created_inbox_item_ids"] == []
    assert state.inbox[assignment_id].state == TeamInboxItemState.HANDLED
    assert state.inbox[rework_id].state == TeamInboxItemState.PENDING
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS


def test_retiring_blocked_review_restores_owner_rework_and_wake_due(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Verify the result.",
        request_id="request-review-before-block",
    )
    review_id = requested["review_request_id"]

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-block-review")
    state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=review_id,
        state="blocked",
        block_kind="system",
        findings=["The review environment is unavailable."],
        request_id="request-block-review",
    )
    assert blocked["success"] is True

    def _exhaust_owner_runtime(state_model) -> None:
        runtime = state_model.agent_runtimes.setdefault(
            "backend",
            AgentRuntime(agent_id="backend"),
        )
        runtime.no_progress_count = 3
        runtime.next_wake_at = datetime.now(UTC) + timedelta(hours=1)

    repo.update(_exhaust_owner_runtime)
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-retire-blocked")
    state, retired = _command_service(repo).retire_review_command(
        session_id="session-lead",
        review_request_id=review_id,
        reason="The blocked review attempt was attached in error.",
        request_id="request-retire-blocked",
    )

    assert retired["success"] is True
    assert len(retired["created_inbox_item_ids"]) == 1
    rework = state.inbox[retired["created_inbox_item_ids"][0]]
    assert rework.recipient_agent_id == "backend"
    assert rework.state == TeamInboxItemState.PENDING
    assert rework.payload["kind"] == "review_rework_restored"
    assert rework.payload["review_request_id"] == review_id
    assert rework.payload["open_findings"]
    owner_runtime = state.agent_runtimes["backend"]
    assert owner_runtime.no_progress_count == 0
    assert owner_runtime.next_wake_at is None
    owner_operations = next(
        item
        for item in runtime_operations_payload(state)["agents"]
        if item["id"] == "backend"
    )
    assert owner_operations["wake_due"] is True
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS


def test_operator_review_retirement_is_explicit_and_preserves_the_blocked_attempt(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    state = _team_admin_service(repo).record_review_request(
        review_request_id="review-blocked",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
    )
    state = _team_admin_service(repo).record_review_decision(
        review_request_id="review-blocked",
        state=TeamRecordState.BLOCKED,
        findings=["The record was created from an invalid historical handoff."],
        expected_revision=state.revision,
    )
    reopened = TeamStateRepository(tmp_path)
    assert reopened.read().review_requests["review-blocked"].retired_at is None
    assert reopened.read().projects["project"].status == ProjectStatus.BLOCKED

    state = _team_admin_service(reopened).retire_review_request(
        review_request_id="review-blocked",
        reason="Operator confirmed this review attempt was attached in error.",
        operator_actor_id="operator:admin",
        expected_revision=reopened.read().revision,
    )

    review = state.review_requests["review-blocked"]
    assert review.state == TeamRecordState.BLOCKED
    assert review.retired_at is not None
    assert review.retired_by == "operator:admin"
    assert review.retirement_reason == (
        "Operator confirmed this review attempt was attached in error."
    )
    assert review.superseded_by_review_request_id is None
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
    assert state.projects["project"].status == ProjectStatus.BLOCKED


def test_other_team_manager_cannot_withdraw_or_retire_review(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    admin.create_team(
        team_id="other-team",
        name="Other Team",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    admin.record_review_request(
        review_request_id="review",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        expected_revision=repo.read().revision,
    )

    _lease_agent_dispatch(
        repo,
        agent_id="other-manager",
        dispatch_id="dispatch-cross-team-withdraw",
    )
    unchanged, denied_withdrawal = _command_service(repo).withdraw_review_command(
        session_id="session-other-manager",
        review_request_id="review",
        reason="This review belongs to a different Team.",
        request_id="request-cross-team-withdraw",
    )

    assert denied_withdrawal["success"] is False
    assert "not permitted" in denied_withdrawal["errors"][0]["message"]
    assert unchanged.review_requests["review"].retired_at is None

    admin.record_review_decision(
        review_request_id="review",
        state=TeamRecordState.ACCEPTED,
        expected_revision=repo.read().revision,
    )
    _lease_agent_dispatch(
        repo,
        agent_id="other-manager",
        dispatch_id="dispatch-cross-team-retire",
    )
    unchanged, denied_retirement = _command_service(repo).retire_review_command(
        session_id="session-other-manager",
        review_request_id="review",
        reason="This terminal review also belongs to a different Team.",
        request_id="request-cross-team-retire",
    )

    assert denied_retirement["success"] is False
    assert "project lead or team manager" in denied_retirement["errors"][0]["message"]
    assert unchanged.review_requests["review"].retired_at is None


def test_project_lead_can_retire_admin_created_terminal_review_without_decision_at(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    state = _team_admin_service(repo).record_review_request(
        review_request_id="review-accepted",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        state=TeamRecordState.ACCEPTED,
    )
    assert state.review_requests["review-accepted"].decision_at is None

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-retire-terminal")
    state, retired = _command_service(repo).retire_review_command(
        session_id="session-lead",
        review_request_id="review-accepted",
        reason="The imported terminal review was attached in error.",
        request_id="request-retire-terminal-without-timestamp",
    )

    assert retired["success"] is True
    assert state.review_requests["review-accepted"].state == TeamRecordState.ACCEPTED
    assert state.review_requests["review-accepted"].decision_at is None
    assert state.review_requests["review-accepted"].retired_at is not None


@pytest.mark.parametrize(
    ("blocker_source_kind", "blocker_source_id"),
    [("task", "task"), ("deliverable", "deliverable")],
)
def test_withdrawing_reopened_review_preserves_active_acceptance_blocker(
    tmp_path: Path,
    blocker_source_kind: str,
    blocker_source_id: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit")
    state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        id="deliverable",
        task_id="task",
        summary="Ready for direct acceptance.",
        request_id="request-submit-before-review",
    )
    assert submitted["success"] is True

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-accept")
    state, accepted = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        decision="accepted",
        request_id="request-accept-before-review",
    )
    assert accepted["success"] is True
    assert state.tasks["task"].state == TeamTaskState.FINISHED

    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-request-review",
    )
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Perform a late specialist review.",
        request_id="request-reopen-for-review",
    )
    review_id = requested["review_request_id"]
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    def _seed_acceptance_blocker(current):
        open_or_get_blocker(
            current,
            source_kind=blocker_source_kind,
            source_id=blocker_source_id,
            summary="Acceptance is blocked by unresolved evidence.",
            reporter_agent_id="backend",
            team_id="team",
            project_id="project",
            task_id="task",
        )
        return current

    state = repo.update(_seed_acceptance_blocker)
    blocker_id = next(
        blocker.id
        for blocker in state.blockers.values()
        if blocker.source_kind.value == blocker_source_kind
        and blocker.source_id == blocker_source_id
    )
    assert state.blockers[blocker_id].state == TeamBlockerState.OPEN

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-withdraw-review")
    state, withdrawn = _command_service(repo).withdraw_review_command(
        session_id="session-lead",
        review_request_id=review_id,
        reason="The late specialist review is no longer needed.",
        request_id=f"request-withdraw-with-{blocker_source_kind}-blocker",
    )

    assert withdrawn["success"] is True
    assert state.blockers[blocker_id].state == TeamBlockerState.OPEN
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.BLOCKED


def test_withdrawing_last_review_preserves_sibling_review_task_blocker(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(repo, deliverable_id="deliverable-target", task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable-sibling", task_id="task")
    admin = _team_admin_service(repo)
    state = admin.record_review_request(
        review_request_id="review-target",
        deliverable_id="deliverable-target",
        reviewer_agent_id="worker",
    )
    state = admin.record_review_request(
        review_request_id="review-sibling",
        deliverable_id="deliverable-sibling",
        reviewer_agent_id="worker",
        expected_revision=state.revision,
    )
    state = admin.record_review_decision(
        review_request_id="review-sibling",
        state=TeamRecordState.BLOCKED,
        findings=["The sibling review environment is unavailable."],
        expected_revision=state.revision,
    )
    sibling_blocker = next(
        blocker
        for blocker in state.blockers.values()
        if blocker.source_id == "review-sibling" and blocker.state == TeamBlockerState.OPEN
    )
    assert state.tasks["task"].state == TeamTaskState.BLOCKED

    state = admin.withdraw_review_request(
        review_request_id="review-target",
        reason="The target review is no longer needed.",
        expected_revision=state.revision,
    )

    assert state.blockers[sibling_blocker.id].state == TeamBlockerState.OPEN
    assert state.deliverables["deliverable-target"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert all(
        item.item_type != TeamInboxItemType.DELIVERABLE_NOTICE
        or item.payload.get("deliverable_id") != "deliverable-target"
        for item in state.inbox.values()
    )


def test_withdrawing_review_does_not_accept_deliverable_with_open_carried_findings(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_deliverable(repo, deliverable_id="deliverable-v1", task_id="task")
    admin = _team_admin_service(repo)
    state = admin.record_review_request(
        review_request_id="review-rejected",
        deliverable_id="deliverable-v1",
        reviewer_agent_id="worker",
    )
    state = admin.record_review_decision(
        review_request_id="review-rejected",
        state=TeamRecordState.REJECTED,
        findings=["The original deliverable still has an unresolved finding."],
        expected_revision=state.revision,
    )
    rework_item = next(
        item
        for item in state.inbox.values()
        if item.payload.get("kind") == "review_rejected"
    )
    assert "supersedes_deliverable_id" in rework_item.payload["next_action"]
    assert rework_item.payload["open_findings"]
    state = admin.record_deliverable(
        deliverable_id="deliverable-v2",
        task_id="task",
        producer_agent_id="backend",
        summary="Reworked deliverable.",
        supersedes_deliverable_id="deliverable-v1",
        expected_revision=state.revision,
    )
    assert any(
        finding.state == "open"
        for finding in state.deliverables["deliverable-v2"].carried_findings
    )
    state = admin.record_review_request(
        review_request_id="review-accepted",
        deliverable_id="deliverable-v2",
        reviewer_agent_id="lead",
        expected_revision=state.revision,
    )
    state = admin.record_review_request(
        review_request_id="review-to-retire",
        deliverable_id="deliverable-v2",
        reviewer_agent_id="worker",
        expected_revision=state.revision,
    )
    state = admin.record_review_decision(
        review_request_id="review-accepted",
        state=TeamRecordState.ACCEPTED,
        expected_revision=state.revision,
    )

    state = admin.withdraw_review_request(
        review_request_id="review-to-retire",
        reason="This unrelated review is no longer required.",
        expected_revision=state.revision,
    )

    assert state.deliverables["deliverable-v2"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    with pytest.raises(ValueError, match="was withdrawn"):
        admin.record_review_decision(
            review_request_id="review-to-retire",
            state=TeamRecordState.ACCEPTED,
            expected_revision=state.revision,
        )


def test_outcome_task_rejects_report_only_deliverable_and_completes_from_accepted_outcome(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_outcome_kind(repo, "bokio_prepared_action")
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-create-outcome-task")
    state, created = _command_service(repo).create_task_command(
        session_id="session-lead",
        task_id="outcome-task",
        owner_agent_id="backend",
        project_id="project",
        title="Prepare the Bokio action",
        description="Produce a reviewable bookkeeping action.",
        task_kind="outcome",
        outcome_contract={
            "required_outcome_kinds": ["bokio_prepared_action"],
            "match": "all",
        },
        request_id="request-create-outcome-task",
    )
    assert created["success"] is True
    assert state.tasks["outcome-task"].task_kind == TeamTaskKind.OUTCOME

    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit-outcome")
    unchanged, report_only = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="outcome-task",
        summary="I inspected the evidence.",
        request_id="request-report-only",
    )
    assert report_only["success"] is False
    assert report_only["errors"][0]["code"] == "deliverable_validation_failed"
    assert "outcome contract" in report_only["errors"][0]["message"]
    assert "outcome-task" not in {
        deliverable.task_id for deliverable in unchanged.deliverables.values()
    }

    state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="outcome-task",
        summary="Prepared the Bokio journal entry action.",
        outcomes=[
            {
                "kind": "bokio_prepared_action",
                "reference_ids": ["bokio-prepared-action-123"],
                "evidence_refs": ["bokio-duplicate-check:check-123"],
            }
        ],
        request_id="request-delivered-outcome",
    )
    deliverable_id = submitted["deliverable_id"]
    assert submitted["success"] is True

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-review-outcome")
    state, requested = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id=deliverable_id,
        reviewer_agent_id="worker",
        instructions="Confirm the prepared action and duplicate-check evidence.",
        request_id="request-review-outcome",
    )
    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-accept-outcome")
    state, accepted = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=requested["review_request_id"],
        state="accepted",
        request_id="request-accept-outcome",
    )

    assert accepted["success"] is True
    task = state.tasks["outcome-task"]
    assert task.state == TeamTaskState.ACCEPTED
    assert task.completion is not None
    assert task.completion.kind == TaskCompletionKind.DELIVERED
    assert task.completion.outcome_reference_ids == ["bokio-prepared-action-123"]


def test_outcome_catalog_management_is_grant_and_scope_gated(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    commands = _command_service(repo)
    admin = _team_admin_service(repo)
    _lease_agent_dispatch(
        repo,
        agent_id="manager",
        dispatch_id="dispatch-manager-outcome-catalog",
    )

    unchanged, denied = commands.create_outcome_kind_command(
        session_id="session-manager",
        team_id="team",
        kind="prepared_action",
        name="Prepared action",
        request_id="request-manager-outcome-kind-denied",
    )

    assert denied["success"] is False
    assert denied["errors"][0]["code"] == "outcome_kind_management_forbidden"
    assert unchanged.outcome_kind_definitions == {}

    admin.update_role(
        role_id="team__manager",
        permissions=[
            RolePermission.CREATE_ROOT_TASK,
            RolePermission.MANAGE_OUTCOME_KINDS,
        ],
        expected_revision=repo.read().revision,
    )
    state, created = commands.create_outcome_kind_command(
        session_id="session-manager",
        team_id="team",
        kind="prepared_action",
        name="Team prepared action",
        description="Produce a persistent action that another system can execute.",
        request_id="request-manager-outcome-kind",
    )

    assert created["success"] is True
    team_definition_id = created["outcome_kind_definition_id"]
    inherited = effective_outcome_kind_definitions(
        state,
        team_id="team",
        project_id="project",
    )
    assert inherited["prepared_action"].id == team_definition_id

    admin.update_role(
        role_id="project__backend",
        permissions=[RolePermission.MANAGE_OUTCOME_KINDS],
        expected_revision=repo.read().revision,
    )
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-backend-outcome-catalog",
    )
    unchanged, denied = commands.create_outcome_kind_command(
        session_id="session-backend",
        team_id="team",
        project_id="project",
        kind="backend_only",
        name="Backend-only outcome",
        request_id="request-backend-outcome-kind-denied",
    )
    assert denied["success"] is False
    assert denied["errors"][0]["code"] == "outcome_kind_management_forbidden"
    assert "backend_only" not in effective_outcome_kind_definitions(
        unchanged,
        team_id="team",
        project_id="project",
    )

    admin.update_role(
        role_id="project__project-lead",
        permissions=[
            RolePermission.CREATE_ROOT_TASK,
            RolePermission.MANAGE_OUTCOME_KINDS,
        ],
        expected_revision=repo.read().revision,
    )
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-lead-outcome-catalog",
    )
    state, overridden = commands.create_outcome_kind_command(
        session_id="session-lead",
        team_id="team",
        project_id="project",
        kind="prepared_action",
        name="Project-specific prepared action",
        request_id="request-lead-outcome-kind",
    )
    assert overridden["success"] is True
    project_definition_id = overridden["outcome_kind_definition_id"]
    effective = effective_outcome_kind_definitions(
        state,
        team_id="team",
        project_id="project",
    )
    assert effective["prepared_action"].id == project_definition_id

    state, archived = commands.archive_outcome_kind_command(
        session_id="session-lead",
        definition_id=project_definition_id,
        request_id="request-archive-project-outcome-kind",
    )
    assert archived["success"] is True
    assert "prepared_action" not in effective_outcome_kind_definitions(
        state,
        team_id="team",
        project_id="project",
    )
    assert (
        effective_outcome_kind_definitions(
            state,
            team_id="team",
            project_id="project",
            include_archived=True,
        )["prepared_action"].id
        == project_definition_id
    )


def test_team_wide_outcome_catalog_rejects_project_scoped_manager_grant(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    state = admin.update_role(
        role_id="project__backend",
        permissions=[RolePermission.MANAGE_OUTCOME_KINDS],
        expected_revision=repo.read().revision,
    )
    admin.update_agent(
        agent_id="manager",
        project_id="project",
        set_project_id=True,
        role_id="project__backend",
        set_role_id=True,
        expected_revision=state.revision,
    )
    _lease_agent_dispatch(
        repo,
        agent_id="manager",
        dispatch_id="dispatch-manager-project-only-outcome-grant",
    )

    unchanged, denied = _command_service(repo).create_outcome_kind_command(
        session_id="session-manager",
        team_id="team",
        kind="project_grant_must_not_escape",
        name="Project grant must not escape",
        request_id="request-manager-project-only-outcome-grant",
    )

    assert denied["success"] is False
    assert denied["errors"][0]["code"] == "outcome_kind_management_forbidden"
    assert unchanged.outcome_kind_definitions == {}


def test_outcome_tasks_reject_unknown_catalog_kinds(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValueError, match="unknown or archived outcome kinds"):
        _team_admin_service(repo).create_or_reprioritize_task(
            task_id="unknown-outcome-task",
            owner_agent_id="backend",
            project_id="project",
            priority=10,
            title="Produce an undefined outcome",
            description="This task must not create an ad hoc outcome vocabulary.",
            task_kind=TeamTaskKind.OUTCOME,
            outcome_contract={
                "required_outcome_kinds": ["invented_at_task_creation"],
            },
            expected_revision=repo.read().revision,
        )

    assert "unknown-outcome-task" not in repo.read().tasks


def test_outcome_definition_snapshots_preserve_task_deliverable_and_review_semantics(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _seed_outcome_kind(repo, "prepared_action")
    _seed_outcome_kind(repo, "evidence_match")
    definition_id = "outcome-kind-prepared_action"
    initial_name = repo.read().outcome_kind_definitions[definition_id].name

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-create-snapshotted-task",
    )
    state, created = _command_service(repo).create_task_command(
        session_id="session-lead",
        task_id="snapshotted-task",
        owner_agent_id="backend",
        project_id="project",
        title="Prepare an action",
        description="Produce a durable action using the current definition.",
        task_kind="outcome",
        outcome_contract={
            "required_outcome_kinds": ["prepared_action"],
            "match": "all",
        },
        request_id="request-create-snapshotted-task",
    )
    assert created["success"] is True
    assert (
        state.tasks["snapshotted-task"]
        .outcome_contract.outcome_kind_definitions[0]
        .name
        == initial_name
    )

    state = admin.update_outcome_kind_definition(
        definition_id=definition_id,
        name="Prepared action v2",
        expected_revision=state.revision,
    )
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-snapshotted-outcome",
    )
    state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="snapshotted-task",
        summary="Prepared the durable action.",
        outcomes=[
            {
                "kind": "prepared_action",
                "reference_ids": ["prepared-action:123"],
                "evidence_refs": ["validation:123"],
            }
        ],
        request_id="request-submit-snapshotted-outcome",
    )
    assert submitted["success"] is True
    deliverable_id = submitted["deliverable_id"]
    assert (
        state.deliverables[deliverable_id].outcomes[0].definition_snapshot.name
        == "Prepared action v2"
    )

    state = admin.create_review_profile(
        profile_id="outcome-review-profile",
        team_id="team",
        project_id="project",
        name="Outcome review",
        required_outcome_kinds=["prepared_action"],
        expected_revision=state.revision,
    )
    state = admin.record_review_request(
        review_request_id="snapshotted-review",
        deliverable_id=deliverable_id,
        reviewer_agent_id="worker",
        review_profile_id="outcome-review-profile",
        expected_revision=state.revision,
    )
    state = admin.update_review_profile(
        profile_id="outcome-review-profile",
        required_outcome_kinds=["prepared_action", "evidence_match"],
        expected_revision=state.revision,
    )
    state = admin.update_outcome_kind_definition(
        definition_id=definition_id,
        name="Prepared action v3",
        expected_revision=state.revision,
    )

    assert (
        state.tasks["snapshotted-task"]
        .outcome_contract.outcome_kind_definitions[0]
        .name
        == initial_name
    )
    assert (
        state.deliverables[deliverable_id].outcomes[0].definition_snapshot.name
        == "Prepared action v2"
    )
    assert (
        state.review_requests["snapshotted-review"]
        .required_outcome_kind_definitions[0]
        .name
        == "Prepared action v2"
    )
    assert [
        definition.kind
        for definition in state.review_requests[
            "snapshotted-review"
        ].required_outcome_kind_definitions
    ] == ["prepared_action"]


def test_review_decision_uses_snapshotted_outcome_requirements_after_profile_edit(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _seed_outcome_kind(repo, "prepared_action")
    _seed_outcome_kind(repo, "later_requirement")
    _seed_task(repo, task_id="outcome-review-task")
    state = admin.record_deliverable(
        deliverable_id="outcome-review-deliverable",
        task_id="outcome-review-task",
        producer_agent_id="backend",
        summary="Prepared the durable action.",
        outcomes=[
            {
                "kind": "prepared_action",
                "reference_ids": ["prepared-action:123"],
            }
        ],
        expected_revision=repo.read().revision,
    )
    state = admin.create_review_profile(
        profile_id="outcome-decision-profile",
        team_id="team",
        project_id="project",
        name="Outcome decision review",
        criteria=[
            ReviewProfileCriterion(
                id="tests",
                label="Required tests pass",
                required=True,
            )
        ],
        required_outcome_kinds=["prepared_action"],
        expected_revision=state.revision,
    )
    state = admin.record_review_request(
        review_request_id="outcome-decision-review",
        deliverable_id="outcome-review-deliverable",
        reviewer_agent_id="worker",
        review_profile_id="outcome-decision-profile",
        expected_revision=state.revision,
    )
    assert [
        definition.kind
        for definition in state.review_requests[
            "outcome-decision-review"
        ].required_outcome_kind_definitions
    ] == ["prepared_action"]

    state = admin.update_review_profile(
        profile_id="outcome-decision-profile",
        required_outcome_kinds=["prepared_action", "later_requirement"],
        expected_revision=state.revision,
    )
    state = admin.record_review_decision(
        review_request_id="outcome-decision-review",
        state=TeamRecordState.ACCEPTED,
        criteria=[
            ReviewCriterionResult(
                criterion_id="tests",
                state="passed",
            )
        ],
        expected_revision=state.revision,
    )

    assert (
        state.review_requests["outcome-decision-review"].state
        == TeamRecordState.ACCEPTED
    )


def test_outcome_kind_archive_requires_active_review_profiles_to_release_it(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _seed_outcome_kind(repo, "prepared_action")
    state = admin.create_review_profile(
        profile_id="active-outcome-profile",
        team_id="team",
        project_id="project",
        name="Active outcome profile",
        required_outcome_kinds=["prepared_action"],
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="used by review profile"):
        admin.update_outcome_kind_definition(
            definition_id="outcome-kind-prepared_action",
            archived=True,
            expected_revision=state.revision,
        )

    state = admin.update_review_profile(
        profile_id="active-outcome-profile",
        archived=True,
        expected_revision=repo.read().revision,
    )
    state = admin.update_outcome_kind_definition(
        definition_id="outcome-kind-prepared_action",
        archived=True,
        expected_revision=state.revision,
    )

    assert state.outcome_kind_definitions[
        "outcome-kind-prepared_action"
    ].archived is True


def test_outcome_kind_archive_rejects_nonterminal_task_contract_references(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _seed_outcome_kind(repo, "prepared_action")
    state = admin.create_or_reprioritize_task(
        task_id="active-outcome-task",
        owner_agent_id="backend",
        project_id="project",
        priority=10,
        title="Prepare an action",
        description="Produce the configured durable outcome.",
        created_by_agent_id="lead",
        task_kind=TeamTaskKind.OUTCOME,
        outcome_contract={"required_outcome_kinds": ["prepared_action"]},
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="used by nonterminal task active-outcome-task"):
        admin.update_outcome_kind_definition(
            definition_id="outcome-kind-prepared_action",
            archived=True,
            expected_revision=state.revision,
        )

    assert repo.read().outcome_kind_definitions[
        "outcome-kind-prepared_action"
    ].archived is False


def test_outcome_kind_archive_rejects_rejected_task_recovery_reference(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    _seed_outcome_kind(repo, "prepared_action")
    state = admin.create_or_reprioritize_task(
        task_id="rejected-outcome-task",
        owner_agent_id="backend",
        project_id="project",
        priority=10,
        title="Prepare an action",
        description="Recover this rejected outcome with a replacement.",
        created_by_agent_id="lead",
        task_kind=TeamTaskKind.OUTCOME,
        outcome_contract={"required_outcome_kinds": ["prepared_action"]},
        expected_revision=repo.read().revision,
    )

    def _reject_task(current):
        current.tasks["rejected-outcome-task"].state = TeamTaskState.REJECTED
        return current

    state = repo.update(_reject_task, expected_revision=state.revision)

    with pytest.raises(
        ValueError,
        match="used by nonterminal task rejected-outcome-task",
    ):
        admin.update_outcome_kind_definition(
            definition_id="outcome-kind-prepared_action",
            archived=True,
            expected_revision=state.revision,
        )

    assert repo.read().outcome_kind_definitions[
        "outcome-kind-prepared_action"
    ].archived is False


def test_team_wide_review_profile_protects_project_outcome_override(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    state = admin.create_outcome_kind_definition(
        definition_id="team-prepared-action",
        team_id="team",
        project_id=None,
        kind="prepared_action",
        name="Team prepared action",
        expected_revision=repo.read().revision,
    )
    state = admin.create_outcome_kind_definition(
        definition_id="project-prepared-action",
        team_id="team",
        project_id="project",
        kind="prepared_action",
        name="Project prepared action",
        expected_revision=state.revision,
    )
    state = admin.create_review_profile(
        profile_id="team-outcome-review-profile",
        team_id="team",
        name="Team outcome review",
        required_outcome_kinds=["prepared_action"],
        expected_revision=state.revision,
    )

    with pytest.raises(
        ValueError,
        match="used by review profile team-outcome-review-profile",
    ):
        admin.update_outcome_kind_definition(
            definition_id="project-prepared-action",
            archived=True,
            expected_revision=state.revision,
        )

    assert repo.read().outcome_kind_definitions[
        "project-prepared-action"
    ].archived is False


def test_team_wide_review_profile_snapshots_target_project_outcome_override(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    state = admin.create_outcome_kind_definition(
        definition_id="team-prepared-action",
        team_id="team",
        project_id=None,
        kind="prepared_action",
        name="Team prepared action",
        expected_revision=repo.read().revision,
    )
    state = admin.create_outcome_kind_definition(
        definition_id="project-prepared-action",
        team_id="team",
        project_id="project",
        kind="prepared_action",
        name="Project prepared action",
        expected_revision=state.revision,
    )
    _seed_task(repo, task_id="project-outcome-review-task")
    state = admin.record_deliverable(
        deliverable_id="project-outcome-deliverable",
        task_id="project-outcome-review-task",
        producer_agent_id="backend",
        summary="Prepared the project-specific action.",
        outcomes=[
            {
                "kind": "prepared_action",
                "reference_ids": ["prepared-action:123"],
            }
        ],
        expected_revision=repo.read().revision,
    )
    state = admin.create_review_profile(
        profile_id="team-outcome-review-profile",
        team_id="team",
        name="Team outcome review",
        required_outcome_kinds=["prepared_action"],
        expected_revision=state.revision,
    )
    state = admin.record_review_request(
        review_request_id="project-outcome-review",
        deliverable_id="project-outcome-deliverable",
        reviewer_agent_id="worker",
        review_profile_id="team-outcome-review-profile",
        expected_revision=state.revision,
    )

    snapshot = state.review_requests[
        "project-outcome-review"
    ].required_outcome_kind_definitions[0]
    assert snapshot.name == "Project prepared action"


def test_outcome_task_rejects_evidence_only_deliverable_and_allows_evidenced_noop(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_outcome_kind(repo, "existing_record")
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-create-noop-task")
    state, created = _command_service(repo).create_task_command(
        session_id="session-lead",
        task_id="noop-task",
        owner_agent_id="backend",
        project_id="project",
        title="Create the record if needed",
        description="Do nothing when the durable record already exists.",
        task_kind="outcome",
        outcome_contract={
            "required_outcome_kinds": [],
            "allow_evidenced_noop": True,
        },
        request_id="request-create-noop-task",
    )
    assert created["success"] is True

    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-noop-task")
    unchanged, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="noop-task",
        summary="The target record already exists.",
        outcomes=[
            {
                "kind": "existing_record",
                "evidence_refs": ["duplicate-check:123"],
            }
        ],
        request_id="request-evidence-only-deliverable",
    )

    assert submitted["success"] is False
    assert submitted["errors"][0]["code"] == "deliverable_validation_failed"
    assert "reference_ids" in submitted["errors"][0]["message"]
    assert unchanged.deliverables == {}

    state, submitted = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="noop-task",
        id="noop-deliverable",
        summary="The target record already exists.",
        completion={
            "kind": "evidenced_noop",
            "summary": "The target record already exists.",
            "evidence_refs": ["duplicate-check:123"],
        },
        request_id="request-submit-evidenced-noop",
    )
    assert submitted["success"] is True
    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-accept-noop")
    state, completed = _command_service(repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="noop-deliverable",
        decision="accepted",
        request_id="request-accept-evidenced-noop",
    )

    assert completed["success"] is True
    task = state.tasks["noop-task"]
    assert state.deliverables["noop-deliverable"].state == TeamRecordState.ACCEPTED
    assert task.state == TeamTaskState.ACCEPTED
    assert task.completion is not None
    assert task.completion.kind == TaskCompletionKind.EVIDENCED_NOOP
    assert task.completion.evidence_refs == ["duplicate-check:123"]


def test_request_review_command_applies_matching_profile_and_enqueues_wake(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo, applicable_role_ids=["project__worker"])
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-review",
    )

    state, result = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Check the slice carefully.",
        request_id="request-review-profile-wake",
    )

    assert result["success"] is True
    review = state.review_requests[result["review_request_id"]]
    assert review.review_profile_id == "profile"
    review_item = next(
        item
        for item in state.inbox.values()
        if item.payload.get("review_request_id") == result["review_request_id"]
    )
    assert review_item.payload["review_profile"]["id"] == "profile"
    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["agent_id"] == "worker"
    outbox_records = repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE)
    assert [record.id for record in outbox_records] == result["outbox_record_ids"]
    assert outbox_records[0].status == TeamOutboxStatus.PENDING
    assert outbox_records[0].payload["payload"]["agent_id"] == "worker"


def test_request_review_implicitly_selects_profile_by_deliverable_outcome(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_outcome_kind(repo, "prepared_action")
    _seed_outcome_kind(repo, "evidence_match")
    _team_admin_service(repo).record_deliverable(
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
        summary="Prepared action.",
        outcomes=[
            {
                "kind": "prepared_action",
                "reference_ids": ["prepared-action-1"],
            }
        ],
    )
    _seed_review_profile(
        repo,
        profile_id="action-review",
        applicable_role_ids=["project__worker"],
        required_outcome_kinds=["prepared_action"],
    )
    _seed_review_profile(
        repo,
        profile_id="evidence-review",
        applicable_role_ids=["project__worker"],
        required_outcome_kinds=["evidence_match"],
    )
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-outcome-profile",
    )

    state, result = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Review the matching outcome.",
        request_id="request-outcome-profile",
    )

    assert result["success"] is True
    assert state.review_requests[result["review_request_id"]].review_profile_id == (
        "action-review"
    )


def test_submit_deliverable_for_review_implicitly_selects_profile_by_outcome(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_review_profile(
        repo,
        profile_id="action-review",
        applicable_role_ids=["project__worker"],
        required_outcome_kinds=["prepared_action"],
    )
    _seed_review_profile(
        repo,
        profile_id="evidence-review",
        applicable_role_ids=["project__worker"],
        required_outcome_kinds=["evidence_match"],
    )
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-outcome-profile",
    )

    state, result = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "task_id": "task",
            "summary": "Prepared action.",
            "outcomes": [
                {
                    "kind": "prepared_action",
                    "reference_ids": ["prepared-action-1"],
                }
            ],
        },
        reviews=[
            {
                "reviewer_agent_id": "worker",
                "instructions": "Review the matching outcome.",
            }
        ],
        request_id="request-submit-outcome-profile",
    )

    assert result["success"] is True
    review_id = result["created_review_request_ids"][0]
    assert state.review_requests[review_id].review_profile_id == "action-review"


def test_submit_deliverable_for_review_command_enqueues_reviewer_wake(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-submit-review")

    _state, result = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={"task_id": "task", "summary": "Ready for review."},
        reviews=[{"reviewer_agent_id": "worker", "instructions": "Check the implementation."}],
        request_id="request-submit-review-wake",
    )

    assert result["success"] is True
    assert result["created_review_request_ids"]
    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["agent_id"] == "worker"
    outbox_records = repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE)
    assert [record.id for record in outbox_records] == result["outbox_record_ids"]


def test_request_review_command_is_idempotent_for_active_reviewer_request(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        dispatch_id="dispatch-review",
    )

    _state, first = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Check the slice carefully.",
        request_id="request-review-1",
    )
    state, second = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Please review the same slice again.",
        request_id="request-review-2",
    )

    assert first["success"] is True
    assert second["success"] is True
    assert second["review_request_id"] == first["review_request_id"]
    assert second["created_review_request_ids"] == []
    assert second["existing_review_request_ids"] == [first["review_request_id"]]
    assert second["created_inbox_item_ids"] == []
    assert any("already exists" in hint for hint in second["next_action_hints"])
    assert list(state.review_requests) == [first["review_request_id"]]
    review_inbox_items = [
        item
        for item in state.inbox.values()
        if item.payload.get("review_request_id") == first["review_request_id"]
        and item.payload.get("kind") == "review_request"
    ]
    assert len(review_inbox_items) == 1


def test_duplicate_request_review_command_counts_as_no_progress_on_finalize(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _state, created = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Check the slice carefully.",
                }
            ],
        },
    )
    assert created["success"] is True

    _lease_review_handoff_dispatch(
        repo,
        deliverable_id="deliverable",
        agent_id="lead",
        dispatch_id="dispatch-duplicate-review",
    )

    def _set_no_progress_count(state):
        state.agent_runtimes["lead"].no_progress_count = 2
        return state

    repo.update(_set_no_progress_count)

    _state, duplicate = _command_service(repo).request_review_command(
        session_id="session-lead",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        instructions="Please review the same slice again.",
        request_id="request-review-duplicate",
    )

    assert duplicate["success"] is True
    assert duplicate["progress_made"] is False
    activities = repo.list_dispatch_activity(
        dispatch_id="dispatch-duplicate-review",
        request_id="request-review-duplicate",
    )
    assert len(activities) == 1
    assert activities[0].progress_classification == "none"

    state, finalized = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-lead",
        dispatch_id="dispatch-duplicate-review",
    )

    assert finalized["success"] is True
    assert finalized["progress_made"] is False
    runtime = state.agent_runtimes["lead"]
    assert runtime.no_progress_count == 3
    assert runtime.last_progress_at is None


def test_apply_agent_action_review_request_rejected_for_unrelated_agent(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    # worker neither produced nor supervises the producer (backend) and is not a lead.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "lead",
                    "instructions": "Please review.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_review_request_ids"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_accept_cascades_to_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "findings": ["Looks good."],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["decided_review_request_ids"] == ["review-1"]
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.decision_at is not None
    assert review.findings == ["Looks good."]
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_apply_agent_action_review_decision_reviewer_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    # lead is not the assigned reviewer (worker is).
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_decisions": [{"review_request_id": "review-1", "state": "accepted"}],
        },
    )

    assert result["success"] is False
    assert any("assigned reviewer" in error for error in result["errors"])
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING


def test_apply_agent_action_multi_review_holds_back_deliverable(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "First.",
                },
                {
                    "id": "review-2",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "backend",
                    "instructions": "Second.",
                },
            ],
        },
    )

    # Accepting only the first review must not accept the deliverable yet.
    state, _ = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-1", "state": "accepted"}],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state != TeamTaskState.ACCEPTED

    state, _ = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "review_decisions": [{"review_request_id": "review-2", "state": "accepted"}],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_submit_deliverable_for_review_seals_one_review_cohort(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-cohort",
    )

    state, submitted = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-cohort",
            "task_id": "task",
            "summary": "Review this immutable candidate as one cohort.",
            "artifact_refs": ["artifact-space:/cohort"],
        },
        reviews=[
            {
                "id": "review-cohort-testing",
                "reviewer_agent_id": "worker",
                "instructions": "Test the candidate.",
            },
            {
                "id": "review-cohort-code",
                "reviewer_agent_id": "lead",
                "instructions": "Review the implementation.",
            },
        ],
        request_id="request-submit-cohort",
    )

    assert submitted["success"] is True
    cohort_id = submitted["review_cohort_id"]
    deliverable = state.deliverables["deliverable-cohort"]
    assert deliverable.review_cohort_id == cohort_id
    assert deliverable.review_cohort_sealed_at is not None
    assert {
        state.review_requests[review_id].review_cohort_id
        for review_id in deliverable.review_request_ids
    } == {cohort_id}
    persisted = repo.read()
    assert persisted.deliverables["deliverable-cohort"].review_cohort_id == cohort_id
    assert (
        persisted.deliverables["deliverable-cohort"].review_cohort_sealed_at
        is not None
    )
    assert persisted.review_requests["review-cohort-testing"].review_cohort_id == (
        cohort_id
    )

    state, late = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-cohort-late",
                    "deliverable_id": "deliverable-cohort",
                    "reviewer_agent_id": "manager",
                    "review_cohort_id": cohort_id,
                    "instructions": "Join after the cohort was sealed.",
                }
            ],
        },
    )

    assert late["success"] is False
    assert any(
        "review cohort" in error and "is sealed" in error
        for error in late["errors"]
    ), late["errors"]
    assert "review-cohort-late" not in state.review_requests


def test_submit_deliverable_for_review_rejects_duplicate_cohort_member(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-duplicate-cohort",
    )

    state, submitted = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-duplicate-cohort",
            "task_id": "task",
            "summary": "An invalid cohort with the same reviewer twice.",
            "artifact_refs": ["artifact-space:/duplicate-cohort"],
        },
        reviews=[
            {
                "id": "review-cohort-worker-a",
                "reviewer_agent_id": "worker",
            },
            {
                "id": "review-cohort-worker-b",
                "reviewer_agent_id": "worker",
            },
        ],
        request_id="request-submit-duplicate-cohort",
    )

    assert submitted["success"] is False
    assert submitted["errors"][0]["code"] == "duplicate_review_cohort_members"
    assert "deliverable-duplicate-cohort" not in state.deliverables
    assert "review-cohort-worker-a" not in state.review_requests
    assert "review-cohort-worker-b" not in state.review_requests


def test_sealed_review_cohort_cannot_withdraw_its_final_member(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-single-cohort",
    )
    _state, submitted = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-single-cohort",
            "task_id": "task",
            "summary": "A candidate with one required reviewer.",
            "artifact_refs": ["artifact-space:/single-cohort"],
        },
        reviews=[
            {
                "id": "review-single-cohort",
                "reviewer_agent_id": "worker",
            }
        ],
        request_id="request-submit-single-cohort",
    )
    assert submitted["success"] is True

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-withdraw-single-cohort",
    )
    state, withdrawn = _command_service(repo).withdraw_review_command(
        session_id="session-backend",
        review_request_id="review-single-cohort",
        reason="The specialist assignment was mistaken.",
        request_id="request-withdraw-single-cohort",
    )

    assert withdrawn["success"] is False
    assert withdrawn["errors"][0]["code"] == "review_withdraw_invalid"
    assert "final active member of sealed cohort" in withdrawn["errors"][0]["message"]
    assert state.review_requests["review-single-cohort"].retired_at is None
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_review_profile_outcomes_fail_before_external_artifact_publication(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_review_profile(
        repo,
        required_outcome_kinds=["prepared_action"],
    )
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-profile-mismatch",
    )
    publisher_calls: list[dict[str, object]] = []

    def publish(**kwargs: object) -> dict[str, object]:
        publisher_calls.append(dict(kwargs))
        return {"artifact_ref": "artifact-space:/unexpected-publication"}

    state, submitted = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-profile-mismatch",
            "task_id": "task",
            "summary": "Missing the profile's required outcome.",
            "artifact_paths": ["reports/result.json"],
        },
        reviews=[
            {
                "id": "review-profile-mismatch",
                "reviewer_agent_id": "worker",
                "review_profile_id": "profile",
            }
        ],
        request_id="request-submit-profile-mismatch",
        artifact_publisher=publish,
    )

    assert submitted["success"] is False
    assert submitted["errors"][0]["code"] == (
        "review_profile_outcome_requirements_unsatisfied"
    )
    assert publisher_calls == []
    assert "deliverable-profile-mismatch" not in state.deliverables
    assert "review-profile-mismatch" not in state.review_requests


def test_sealed_review_cohort_collects_all_results_before_rework(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-cohort",
    )
    _state, submitted = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-cohort",
            "task_id": "task",
            "summary": "Review this immutable candidate as one cohort.",
            "artifact_refs": ["artifact-space:/cohort"],
        },
        reviews=[
            {
                "id": "review-cohort-testing",
                "reviewer_agent_id": "worker",
            },
            {
                "id": "review-cohort-code",
                "reviewer_agent_id": "lead",
            },
        ],
        request_id="request-submit-cohort",
    )
    assert submitted["success"] is True

    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-reject-cohort-testing",
    )
    state, rejected = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id="review-cohort-testing",
        state="rejected",
        findings=["The UI workflow fails at the final confirmation."],
        request_id="request-reject-cohort-testing",
    )

    assert rejected["success"] is True
    assert state.deliverables["deliverable-cohort"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert not any(
        item.recipient_agent_id == "backend"
        and item.payload.get("kind") == "review_rejected"
        for item in state.inbox.values()
    )

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-accept-cohort-code",
    )
    state, accepted = _command_service(repo).decide_review_command(
        session_id="session-lead",
        review_request_id="review-cohort-code",
        state="accepted",
        findings=[],
        request_id="request-accept-cohort-code",
    )

    assert accepted["success"] is True
    assert state.deliverables["deliverable-cohort"].state == TeamRecordState.REJECTED
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
    rework_items = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == "backend"
        and item.payload.get("kind") == "review_rejected"
    ]
    assert len(rework_items) == 1
    assert rework_items[0].payload["review_cohort_id"] == submitted["review_cohort_id"]
    assert rework_items[0].payload["review_request_id"] == (
        "review-cohort-testing"
    )
    assert rework_items[0].payload["review_request_ids"] == [
        "review-cohort-testing"
    ]
    assert rework_items[0].sender_agent_id == "worker"
    assert rework_items[0].payload["findings"] == [
        "The UI workflow fails at the final confirmation."
    ]

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-replace-smaller-cohort",
    )
    publisher_calls: list[dict[str, object]] = []

    def publish(**kwargs: object) -> dict[str, object]:
        publisher_calls.append(dict(kwargs))
        return {"artifact_ref": "artifact-space:/unexpected-replacement"}

    state, replacement = _command_service(
        repo
    ).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-cohort-replacement",
            "task_id": "task",
            "summary": "A replacement with a smaller review cohort.",
            "supersedes_deliverable_id": "deliverable-cohort",
            "artifact_paths": ["reports/replacement.json"],
        },
        reviews=[
            {
                "id": "review-replacement-testing-only",
                "reviewer_agent_id": "worker",
            }
        ],
        request_id="request-replace-smaller-cohort",
        artifact_publisher=publish,
    )

    assert replacement["success"] is False
    assert replacement["errors"][0]["code"] == (
        "sealed_review_cohort_replacement_coverage_incomplete"
    )
    assert replacement["correction_context"][
        "missing_unprofiled_reviewer_agent_ids"
    ] == ["lead"]
    assert publisher_calls == []
    assert "deliverable-cohort-replacement" not in state.deliverables

    state, legacy = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        action={
            "deliverables": [
                {
                    "id": "deliverable-cohort-legacy-bypass",
                    "task_id": "task",
                    "summary": "A legacy replacement without a review cohort.",
                    "supersedes_deliverable_id": "deliverable-cohort",
                }
            ]
        },
    )
    assert legacy["success"] is False
    assert "complete fresh review cohort" in legacy["errors"][0]
    assert "deliverable-cohort-legacy-bypass" not in state.deliverables

    with pytest.raises(ValueError, match="complete fresh review cohort"):
        _team_admin_service(repo).record_deliverable(
            deliverable_id="deliverable-cohort-admin-bypass",
            task_id="task",
            producer_agent_id="backend",
            summary="An Admin replacement without a review cohort.",
            supersedes_deliverable_id="deliverable-cohort",
            enforce_task_assessment=True,
            expected_revision=repo.read().revision,
        )
    assert (
        "deliverable-cohort-admin-bypass"
        not in repo.read().deliverables
    )

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-replace-through-workflow",
    )
    state, plain = _command_service(repo).submit_deliverable_command(
        session_id="session-backend",
        task_id="task",
        id="deliverable-cohort-workflow-routed",
        summary="A plain replacement awaiting workflow-owned review routing.",
        supersedes_deliverable_id="deliverable-cohort",
        request_id="request-replace-cohort-workflow-routed",
    )
    assert plain["success"] is True, plain.get("errors")
    assert state.deliverables["deliverable-cohort"].superseded_by_deliverable_id == (
        "deliverable-cohort-workflow-routed"
    )
    replacement = state.deliverables["deliverable-cohort-workflow-routed"]
    assert replacement.review_request_ids == []
    assert replacement.review_cohort_id is None
    assert [finding.text for finding in replacement.carried_findings] == [
        "The UI workflow fails at the final confirmation."
    ]
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_review_cohort_retry_cannot_collapse_onto_another_member(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-retry-collision",
    )
    _state, submitted = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-retry-collision",
            "task_id": "task",
            "summary": "A candidate with two independent review slots.",
            "artifact_refs": ["artifact-space:/retry-collision"],
        },
        reviews=[
            {
                "id": "review-retry-collision-worker",
                "reviewer_agent_id": "worker",
            },
            {
                "id": "review-retry-collision-lead",
                "reviewer_agent_id": "lead",
            },
        ],
        request_id="request-submit-retry-collision",
    )
    assert submitted["success"] is True

    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-block-retry-collision",
    )
    _state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id="review-retry-collision-worker",
        state="blocked",
        block_kind="system",
        findings=["The worker test environment is unavailable."],
        request_id="request-block-retry-collision",
    )
    assert blocked["success"] is True

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-retry-collision",
    )

    # The unresolved cohort makes the task non-actionable, so a normal wake
    # correctly omits it. Give this synthetic dispatch explicit task scope to
    # exercise the mutation guards rather than stopping at dispatch authority.
    def _authorize_unresolved_task(state):
        state.dispatches["dispatch-retry-collision"].task_ids = ["task"]
        return state

    repo.update(_authorize_unresolved_task)

    state, atomic_replacement = _command_service(
        repo
    ).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-unresolved-cohort-atomic",
            "task_id": "task",
            "summary": "Do not replace an unresolved sealed cohort.",
            "supersedes_deliverable_id": "deliverable-retry-collision",
        },
        reviews=[
            {
                "id": "review-unresolved-cohort-worker",
                "reviewer_agent_id": "worker",
            },
            {
                "id": "review-unresolved-cohort-lead",
                "reviewer_agent_id": "lead",
            },
        ],
        request_id="request-unresolved-cohort-atomic",
    )
    assert atomic_replacement["success"] is False
    assert atomic_replacement["errors"]
    assert "deliverable-unresolved-cohort-atomic" not in state.deliverables

    state, plain_replacement = _command_service(
        repo
    ).submit_deliverable_command(
        session_id="session-backend",
        task_id="task",
        id="deliverable-unresolved-cohort-plain",
        summary="Do not bypass unresolved cohort review.",
        supersedes_deliverable_id="deliverable-retry-collision",
        request_id="request-unresolved-cohort-plain",
    )
    assert plain_replacement["success"] is False
    assert plain_replacement["errors"]
    assert "deliverable-unresolved-cohort-plain" not in state.deliverables

    state, legacy_replacement = _team_agent_action_service(
        repo
    ).apply_agent_action(
        session_id="session-backend",
        action={
            "deliverables": [
                {
                    "id": "deliverable-unresolved-cohort-legacy",
                    "task_id": "task",
                    "summary": "Do not bypass through legacy action.",
                    "supersedes_deliverable_id": (
                        "deliverable-retry-collision"
                    ),
                }
            ]
        },
    )
    assert legacy_replacement["success"] is False
    assert legacy_replacement["errors"]
    assert "deliverable-unresolved-cohort-legacy" not in state.deliverables

    with pytest.raises(ValueError, match="unresolved review slots"):
        _team_admin_service(repo).record_deliverable(
            deliverable_id="deliverable-unresolved-cohort-admin",
            task_id="task",
            producer_agent_id="backend",
            summary="Do not bypass through Admin.",
            supersedes_deliverable_id="deliverable-retry-collision",
            expected_revision=repo.read().revision,
        )
    assert (
        "deliverable-unresolved-cohort-admin"
        not in repo.read().deliverables
    )

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-retry-collision-final",
    )
    state, retried = _command_service(repo).retry_review_command(
        session_id="session-backend",
        review_request_id="review-retry-collision-worker",
        reviewer_agent_id="lead",
        reason="Move the test slot to the lead.",
        request_id="request-retry-collision",
    )

    assert retried["success"] is False
    assert retried["errors"][0]["code"] == "review_retry_invalid"
    assert "cannot collapse two review cohort slots" in retried["errors"][0]["message"]
    assert (
        state.review_requests["review-retry-collision-worker"]
        .superseded_by_review_request_id
        is None
    )


def test_review_cohort_retry_inherits_predecessor_finding_authority(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-submit-retry-lineage",
    )
    _state, submitted = _command_service(repo).submit_deliverable_for_review_command(
        session_id="session-backend",
        deliverable={
            "id": "deliverable-retry-lineage",
            "task_id": "task",
            "summary": "A candidate whose blocked slot needs a replacement reviewer.",
            "artifact_refs": ["artifact-space:/retry-lineage"],
        },
        reviews=[
            {
                "id": "review-retry-lineage-worker",
                "reviewer_agent_id": "worker",
            }
        ],
        request_id="request-submit-retry-lineage",
    )
    assert submitted["success"] is True

    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-block-retry-lineage",
    )
    state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id="review-retry-lineage-worker",
        state="blocked",
        block_kind="system",
        findings=["The worker test environment is unavailable."],
        request_id="request-block-retry-lineage",
    )
    assert blocked["success"] is True
    finding_id = state.deliverables[
        "deliverable-retry-lineage"
    ].carried_findings[0].id

    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-retry-lineage",
    )
    state, retried = _command_service(repo).retry_review_command(
        session_id="session-backend",
        review_request_id="review-retry-lineage-worker",
        reviewer_agent_id="lead",
        reason="The lead can perform this review in a working environment.",
        request_id="request-retry-lineage",
    )
    assert retried["success"] is True
    successor_id = retried["replacement_review_request_id"]
    assert state.review_requests[successor_id].reviewer_agent_id == "lead"

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-accept-retry-lineage",
    )
    state, accepted = _command_service(repo).decide_review_command(
        session_id="session-lead",
        review_request_id=successor_id,
        state="accepted",
        findings=[],
        finding_resolutions=[
            {
                "finding_id": finding_id,
                "resolution": "Retried the immutable candidate in a working environment.",
                "evidence_refs": ["artifact-space:/retry-lineage/test-result"],
            }
        ],
        request_id="request-accept-retry-lineage",
    )

    assert accepted["success"] is True
    assert state.deliverables["deliverable-retry-lineage"].state == (
        TeamRecordState.ACCEPTED
    )
    assert state.deliverables[
        "deliverable-retry-lineage"
    ].carried_findings[0].state == "resolved"
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_review_decisions_persist_independently_when_parallel_review_is_blocked(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    admin = _team_admin_service(repo)
    state = admin.record_review_request(
        review_request_id="review-testing",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        expected_revision=repo.read().revision,
    )
    admin.record_review_request(
        review_request_id="review-code",
        deliverable_id="deliverable",
        reviewer_agent_id="lead",
        expected_revision=state.revision,
    )

    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-blocked-testing-review",
    )
    state, blocked = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id="review-testing",
        state="blocked",
        block_kind="system",
        findings=["The test environment is unavailable."],
        request_id="request-block-testing-review",
    )
    assert blocked["success"] is True
    assert state.review_requests["review-testing"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-accepted-code-review",
    )
    state, accepted = _command_service(repo).decide_review_command(
        session_id="session-lead",
        review_request_id="review-code",
        state="accepted",
        findings=["The implementation is correct."],
        request_id="request-accept-code-review",
    )

    assert accepted["success"] is True
    assert state.review_requests["review-code"].state == TeamRecordState.ACCEPTED
    assert state.review_requests["review-code"].decision_at is not None
    assert state.review_requests["review-code"].findings == [
        "The implementation is correct."
    ]
    assert state.review_requests["review-testing"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert any(
        blocker.source_id == "review-testing"
        and blocker.state == TeamBlockerState.OPEN
        for blocker in state.blockers.values()
    )


def test_final_review_acceptance_is_recorded_when_aggregate_boundary_is_blocked(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="unfinished-child",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Unfinished child",
        description="This child must finish before its parent can resolve.",
        created_by_agent_id="lead",
        parent_task_id="task",
        task_kind=TeamTaskKind.SUPPORT,
    )
    _team_admin_service(repo).record_review_request(
        review_request_id="review-code",
        deliverable_id="deliverable",
        reviewer_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-accepted-code-review",
    )

    state, accepted = _command_service(repo).decide_review_command(
        session_id="session-lead",
        review_request_id="review-code",
        state="accepted",
        request_id="request-accept-code-review",
    )

    assert accepted["success"] is True
    assert state.review_requests["review-code"].state == TeamRecordState.ACCEPTED
    assert state.review_requests["review-code"].decision_at is not None
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["unfinished-child"].state == TeamTaskState.PENDING
    assert accepted["task"]["assessment"]["unresolved_descendant_ids"] == [
        "unfinished-child"
    ]


def test_apply_agent_action_late_review_request_reopens_accepted_work(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "First review.",
                }
            ],
        },
    )
    state, _ = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-1", "state": "accepted"}],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-2",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "lead",
                    "instructions": "Late release-blocking review.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["created_review_request_ids"] == ["review-2"]
    assert state.review_requests["review-2"].state == TeamRecordState.PENDING
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    state, _ = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_decisions": [{"review_request_id": "review-2", "state": "accepted"}],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_apply_agent_action_multi_deliverable_holds_back_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable-a", task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable-b", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-a",
                    "deliverable_id": "deliverable-a",
                    "reviewer_agent_id": "worker",
                    "instructions": "A.",
                },
                {
                    "id": "review-b",
                    "deliverable_id": "deliverable-b",
                    "reviewer_agent_id": "worker",
                    "instructions": "B.",
                },
            ],
        },
    )

    state, _ = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-a", "state": "accepted"}],
        },
    )
    assert state.deliverables["deliverable-a"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state != TeamTaskState.ACCEPTED

    state, _ = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-b", "state": "accepted"}],
        },
    )
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_apply_agent_action_review_rejected_returns_task_to_implementer(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                },
                {
                    "id": "review-stale",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "lead",
                    "instructions": "Perform a second review.",
                },
            ],
        },
    )
    stale_assignment_id = next(
        inbox_id
        for inbox_id in requested["created_inbox_item_ids"]
        if state.inbox[inbox_id].payload.get("review_request_id") == "review-stale"
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "rejected",
                    "findings": ["Needs rework."],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["decided_review_request_ids"] == ["review-1"]
    assert len(result["created_inbox_item_ids"]) == 1
    assert state.review_requests["review-1"].state == TeamRecordState.REJECTED
    assert state.review_requests["review-1"].findings == ["Needs rework."]
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
    assert state.projects["project"].status == ProjectStatus.ACTIVE

    rework_item = state.inbox[result["created_inbox_item_ids"][0]]
    assert rework_item.recipient_agent_id == "backend"
    assert rework_item.sender_agent_id == "worker"
    assert rework_item.task_id == "task"
    assert rework_item.item_type == TeamInboxItemType.STATUS_REQUEST
    assert rework_item.response_requested is True
    assert rework_item.payload["kind"] == "review_rejected"
    assert rework_item.payload["review_request_id"] == "review-1"
    assert rework_item.payload["deliverable_id"] == "deliverable"
    assert rework_item.payload["supersedes_deliverable_id"] == "deliverable"
    assert rework_item.payload["findings"] == ["Needs rework."]
    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == "team.agent.wake_requested"
        and event["payload"]["agent_id"] == "backend"
    ]
    assert len(wake_events) == 1
    assert wake_events[0]["payload"]["reason"] == "inbox"

    _state, leases = _team_runtime_service(repo).lease_due_governance_managers(
        now=state.tasks["task"].state_entered_at + timedelta(minutes=1),
    )

    assert leases == []

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-v2",
                    "task_id": "task",
                    "summary": "Reworked deliverable.",
                    "supersedes_deliverable_id": rework_item.payload["supersedes_deliverable_id"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["created_deliverable_ids"] == ["deliverable-v2"]
    assert state.deliverables["deliverable"].superseded_by_deliverable_id == ("deliverable-v2")
    assert state.deliverables["deliverable"].supersession_reason == (
        "Rework deliverable submitted."
    )
    assert state.deliverables["deliverable"].superseded_at is not None
    assert state.deliverables["deliverable"].superseded_by_actor == "agent:backend"
    assert state.deliverables["deliverable-v2"].supersedes_deliverable_id == ("deliverable")
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-2",
                    "deliverable_id": "deliverable-v2",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review the revision.",
                }
            ],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-2", "state": "accepted"}],
        },
    )

    assert result["success"] is True
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.deliverables["deliverable-v2"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-retire-superseded-review",
    )
    state, retirement = _command_service(repo).retire_review_command(
        session_id="session-lead",
        review_request_id="review-1",
        reason="The rejected historical review was attached in error.",
        request_id="request-retire-superseded-review",
    )

    assert retirement["success"] is True
    assert state.review_requests["review-1"].retired_at is not None
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.deliverables["deliverable-v2"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-withdraw-superseded-review",
    )
    state, withdrawal = _command_service(repo).withdraw_review_command(
        session_id="session-lead",
        review_request_id="review-stale",
        reason="The historical handoff is no longer actionable.",
        request_id="request-withdraw-superseded-review",
    )

    assert withdrawal["success"] is True
    assert withdrawal["closed_inbox_item_ids"] == [stale_assignment_id]
    assert state.review_requests["review-stale"].retired_at is not None
    assert state.inbox[stale_assignment_id].state == TeamInboxItemState.HANDLED
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.deliverables["deliverable-v2"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_apply_agent_action_review_rejected_after_handoff_targets_current_owner(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="worker")
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                }
            ],
        },
    )
    assert result["success"] is True
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "lead",
                    "instructions": "Review.",
                }
            ],
        },
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "task", "owner_agent_id": "backend"}],
        },
    )
    assert result["success"] is True
    assert state.tasks["task"].owner_agent_id == "backend"

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "rejected",
                    "findings": ["Needs rework."],
                }
            ],
        },
    )

    assert result["success"] is True
    rework_item = state.inbox[result["created_inbox_item_ids"][0]]
    assert rework_item.recipient_agent_id == "backend"
    assert rework_item.sender_agent_id == "lead"
    assert rework_item.payload["supersedes_deliverable_id"] == "deliverable"
    assert state.tasks["task"].owner_agent_id == "backend"
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-v2",
                    "task_id": "task",
                    "summary": "Reworked by the current owner.",
                    "supersedes_deliverable_id": rework_item.payload["supersedes_deliverable_id"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["created_deliverable_ids"] == ["deliverable-v2"]
    assert state.deliverables["deliverable"].producer_agent_id == "worker"
    assert state.deliverables["deliverable"].superseded_by_deliverable_id == ("deliverable-v2")
    assert state.deliverables["deliverable-v2"].producer_agent_id == "backend"
    assert state.deliverables["deliverable-v2"].supersedes_deliverable_id == ("deliverable")
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    _team_admin_service(repo).register_agent(
        agent_id="worker-2",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-worker-2",
        reports_to_agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "task", "owner_agent_id": "worker-2"}],
        },
    )

    assert result["success"] is True
    assert state.tasks["task"].owner_agent_id == "worker-2"
    assert state.deliverables["deliverable"].superseded_by_deliverable_id == ("deliverable-v2")


def test_apply_agent_action_rework_requires_deliverable_supersession(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-1", "state": "rejected"}],
        },
    )
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-v2",
                    "task_id": "task",
                    "summary": "Reworked deliverable without lineage.",
                }
            ],
        },
    )
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-2",
                    "deliverable_id": "deliverable-v2",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review the revision.",
                }
            ],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-2", "state": "accepted"}],
        },
    )

    assert result["success"] is True
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.deliverables["deliverable"].superseded_by_deliverable_id is None
    assert state.deliverables["deliverable-v2"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_apply_agent_action_rework_rejects_self_superseding_deliverable_id(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-1", "state": "rejected"}],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Invalid self-superseding rework.",
                    "supersedes_deliverable_id": "deliverable",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_deliverable_ids"] == []
    assert result["errors"] == [
        "deliverables[0]: deliverable id cannot match supersedes_deliverable_id"
    ]
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.deliverables["deliverable"].superseded_by_deliverable_id is None


def test_apply_agent_action_review_blocked_blocks_all(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-1", "state": "blocked"}],
        },
    )

    assert result["success"] is True
    assert state.review_requests["review-1"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert state.projects["project"].status == ProjectStatus.BLOCKED
    blocker = next(
        blocker
        for blocker in state.blockers.values()
        if blocker.source_id == "review-1"
    )
    assert blocker.source_kind.value == "review_request"
    assert blocker.owner_agent_id == "backend"
    assert any(
        item.recipient_agent_id == "backend"
        and item.state == TeamInboxItemState.PENDING
        and item.payload.get("blocker_id") == blocker.id
        for item in state.inbox.values()
    )


def test_apply_agent_action_goal_update_permission(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend is neither the project lead nor the team manager.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "goal_updates": [{"project_id": "project", "goal_id": "goal", "state": "accepted"}],
        },
    )
    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.projects["project"].goals[0].state == TeamRecordState.PENDING

    # the project lead may update the goal.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [{"project_id": "project", "goal_id": "goal", "state": "in_progress"}],
        },
    )
    assert result["success"] is True
    assert result["updated_goal_ids"] == ["goal"]
    assert state.projects["project"].goals[0].state == TeamRecordState.IN_PROGRESS

    # the team manager may also update the goal.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "goal_updates": [{"project_id": "project", "goal_id": "goal", "state": "accepted"}],
        },
    )
    assert result["success"] is True
    assert state.projects["project"].goals[0].state == TeamRecordState.ACCEPTED


def test_apply_agent_action_project_status_completes(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-1", "state": "accepted"}],
        },
    )
    # Task is accepted; project not yet complete until the goal is accepted.
    assert repo.read().projects["project"].status == ProjectStatus.ACTIVE

    state, _ = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [{"project_id": "project", "goal_id": "goal", "state": "accepted"}],
        },
    )

    assert state.projects["project"].status == ProjectStatus.COMPLETED


def test_apply_agent_action_goal_update_resets_no_progress_count(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    _team_agent_action_service(repo).apply_agent_action(
        action={"agent_id": "lead"}, valid=False, error="boom"
    )
    assert repo.read().agent_runtimes["lead"].no_progress_count == 1

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [{"project_id": "project", "goal_id": "goal", "state": "in_progress"}],
        },
    )

    assert result["success"] is True
    assert state.agent_runtimes["lead"].no_progress_count == 0


def _set_project_git(
    repo: TeamStateRepository,
) -> None:
    def _mutate(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id=MANAGED_REPO_ID,
            base_branch="main",
        )
        state.projects["project"].release_policy = ProjectReleasePolicy(allow_ungated=True)
        return state

    repo.update(_mutate)


def _seed_completed_ephemeral(
    repo: TeamStateRepository,
    *,
    assignment_id: str = "ephemeral-1",
    session_id: str = "eph-1",
    agent_branch: str | None = None,
) -> None:
    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=2)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    _team_ephemeral_service(repo).request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": assignment_id,
                "task_id": "task",
                "session_id": session_id,
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )
    _team_ephemeral_service(repo).apply_ephemeral_action(
        session_id=session_id,
        action={
            "ephemeral_assignment_id": assignment_id,
            "summary": "Done.",
            "agent_branch": agent_branch
            if agent_branch is not None
            else f"agents/{session_id}/{assignment_id}",
            "commit_sha": "abc1234",
        },
    )


def test_update_feature_branch_command_replay_conflict_and_outbox(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    def _set_review_branch(state):
        state.deliverables["deliverable"].git_refs.review_branch = "team/team/reviews/deliverable"
        return state

    repo.update(_set_review_branch)
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-feature-command",
    )

    state, first = _command_service(repo).update_feature_branch_command(
        session_id="session-backend",
        task_id="task",
        source_review_branches=["team/team/reviews/deliverable"],
        request_id="request-feature-command",
        correlation_id="corr-feature",
        causation_id="cause-feature",
    )
    state, replay = _command_service(repo).update_feature_branch_command(
        session_id="session-backend",
        task_id="task",
        source_review_branches=["team/team/reviews/deliverable"],
        request_id="request-feature-command",
        correlation_id="corr-feature",
        causation_id="cause-feature",
    )
    _state, conflict = _command_service(repo).update_feature_branch_command(
        session_id="session-backend",
        task_id="task",
        source_review_branches=["team/team/reviews/other"],
        request_id="request-feature-command",
        correlation_id="corr-feature",
        causation_id="cause-feature",
    )

    assert replay == first
    assert conflict["success"] is False
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert len(state.git_promotions) == 1
    promotion = state.git_promotions[first["promotion_id"]]
    assert promotion.promotion_kind == "feature"
    assert first["intent_recorded"] is True
    assert first["async_effect_pending"] is True
    assert first["downstream_effect_completed"] is False
    outbox_records = repo.list_outbox_records()
    assert len(outbox_records) == 1
    assert first["outbox_record_ids"] == [outbox_records[0].id]
    assert outbox_records[0].status == TeamOutboxStatus.PENDING
    assert outbox_records[0].intent_type == GIT_PROMOTION_EVENT_TYPE
    assert outbox_records[0].aggregate_id == promotion.id
    assert outbox_records[0].idempotency_key == (
        f"team-addon:{GIT_PROMOTION_EVENT_TYPE}:{promotion.id}"
    )
    assert outbox_records[0].payload["payload"]["target_branch"] == (
        "team/team/features/task-backend"
    )
    activities = repo.list_dispatch_activity(
        dispatch_id="dispatch-feature-command",
        tool_name="team_update_feature_branch",
    )
    assert len(activities) == 1
    assert activities[0].correlation_id == "corr-feature"
    assert activities[0].causation_id == "cause-feature"


def test_publish_review_branch_command_rejects_ambiguous_shape_without_mutation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_completed_ephemeral(repo)
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _lease_agent_dispatch(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-review-command",
    )

    state, result = _command_service(repo).publish_review_branch_command(
        session_id="session-backend",
        assignment_id="ephemeral-1",
        deliverable_id="deliverable",
        request_id="request-review-ambiguous",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "invalid_review_promotion_shape"
    assert state.git_promotions == {}
    assert repo.list_outbox_records() == []


def test_request_release_command_returns_package_events_and_next_action_hints(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    def _set_review_branch(state):
        state.deliverables["deliverable"].git_refs.review_branch = "team/team/reviews/deliverable"
        state.deliverables[
            "deliverable"
        ].initial_release_disposition = DeliverableInitialReleaseDisposition()
        return state

    repo.update(_set_review_branch)
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-release-command",
    )

    state, result = _command_service(repo).request_release_command(
        session_id="session-lead",
        id="rel-1",
        project_id="project",
        deliverable_ids=["deliverable"],
        request_id="request-release-command",
        summary="Release accepted deliverable.",
    )

    assert result["success"] is True
    assert result["release_package_id"] == "rel-1"
    assert result["release_package_ids"] == ["rel-1"]
    assert len(result["promotion_event_ids"]) == 1
    assert result["git_promotion_event_ids"] == result["promotion_event_ids"]
    assert result["release_package"]["id"] == "rel-1"
    assert result["release_package"]["state"] == TeamRecordState.PENDING.value
    assert result["release_package"]["summary"] == "Release accepted deliverable."
    assert any(
        "Prerequisite feature promotion intent recorded" in hint
        for hint in result["next_action_hints"]
    )
    release = state.release_packages["rel-1"]
    assert release.summary == "Release accepted deliverable."
    disposition = state.deliverables["deliverable"].initial_release_disposition
    assert disposition is not None
    assert disposition.state == InitialReleaseDispositionState.RELEASE_REQUESTED
    assert disposition.release_package_id == "rel-1"
    assert disposition.decided_by_actor == "agent:lead"
    feature_promotion_id = result["promotion_event_ids"][0]
    feature_promotion = state.git_promotions[feature_promotion_id]
    assert release.git_promotion_record_ids == [feature_promotion_id]
    assert feature_promotion.promotion_kind == "feature"
    assert feature_promotion.release_package_id == "rel-1"
    outbox_records = repo.list_outbox_records()
    assert [record.aggregate_id for record in outbox_records] == [feature_promotion_id]
    feature_payload = result["events"][0]["payload"]
    assert feature_payload["managed_repo_id"] == MANAGED_REPO_ID
    assert feature_payload["source_branches"] == ["team/team/reviews/deliverable"]

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-release-command-duplicate",
    )
    state, duplicate = _command_service(repo).request_release_command(
        session_id="session-lead",
        id="rel-2",
        project_id="project",
        deliverable_ids=["deliverable"],
        request_id="request-release-command-duplicate",
        summary="Accidental duplicate release request.",
    )

    assert duplicate["success"] is True
    assert duplicate["created"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["existing_release_package_id"] == "rel-1"
    assert duplicate["release_package_id"] == "rel-1"
    assert duplicate["release_package_ids"] == ["rel-1"]
    assert duplicate["progress_made"] is False
    assert duplicate["promotion_event_ids"] == []
    assert duplicate["outbox_record_ids"] == []
    assert list(state.release_packages) == ["rel-1"]
    assert len(repo.list_outbox_records()) == len(outbox_records)
    assert any(
        "already covers this deliverable set" in hint for hint in duplicate["next_action_hints"]
    )


def test_initial_release_not_planned_decision_can_be_reopened_with_audit(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _track_initial_release(state):
        state.deliverables[
            "deliverable"
        ].initial_release_disposition = DeliverableInitialReleaseDisposition()
        return state

    repo.update(_track_initial_release)
    _lease_agent_dispatch(
        repo,
        agent_id="worker",
        dispatch_id="dispatch-initial-release-forbidden",
    )
    unchanged, forbidden = _command_service(repo).decide_initial_release_command(
        session_id="session-worker",
        deliverable_ids=["deliverable"],
        decision="not_planned",
        reason="A project member must not make this decision.",
        request_id="request-initial-release-forbidden",
    )
    assert forbidden["success"] is False
    assert forbidden["errors"][0]["code"] == "initial_release_decision_forbidden"
    assert unchanged.deliverables[
        "deliverable"
    ].initial_release_disposition.state == InitialReleaseDispositionState.PENDING

    _lease_agent_dispatch(
        repo,
        agent_id="manager",
        dispatch_id="dispatch-initial-release-not-planned",
    )
    state, decided = _command_service(repo).decide_initial_release_command(
        session_id="session-manager",
        deliverable_ids=["deliverable"],
        decision="not_planned",
        reason="This internal-only result is not part of a release.",
        request_id="request-initial-release-not-planned",
    )

    assert decided["success"] is True
    assert decided["decision"] == "not_planned"
    disposition = state.deliverables["deliverable"].initial_release_disposition
    assert disposition is not None
    assert disposition.state == InitialReleaseDispositionState.NOT_PLANNED
    assert disposition.decided_by_actor == "agent:manager"
    assert disposition.reason == "This internal-only result is not part of a release."
    assert disposition.decided_at is not None

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-initial-release-reopen",
    )
    unchanged, release_rejected = _command_service(repo).request_release_command(
        session_id="session-lead",
        project_id="project",
        deliverable_ids=["deliverable"],
        request_id="request-release-while-not-planned",
    )
    assert release_rejected["success"] is False
    assert release_rejected["errors"][0]["code"] == "git_release_request_invalid"
    assert "reopen the initial release decision" in release_rejected["errors"][0][
        "message"
    ]
    assert unchanged.release_packages == {}

    state, reopened = _command_service(repo).decide_initial_release_command(
        session_id="session-lead",
        deliverable_ids=["deliverable"],
        decision="reopen",
        reason="The result is now required in the next customer release.",
        request_id="request-initial-release-reopen",
    )

    assert reopened["success"] is True
    assert reopened["decision"] == "reopen"
    disposition = state.deliverables["deliverable"].initial_release_disposition
    assert disposition is not None
    assert disposition.state == InitialReleaseDispositionState.PENDING
    assert disposition.decided_at is None
    assert disposition.decided_by_actor is None
    assert disposition.reason is None
    assert disposition.reopened_by_actor == "agent:lead"
    assert disposition.reopen_reason == (
        "The result is now required in the next customer release."
    )
    assert disposition.reopened_at is not None
    assert repo.read().deliverables["deliverable"].initial_release_disposition == (
        disposition
    )


def test_recover_release_command_records_retryable_recovery_outbox(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-release-recovery-command",
    )
    state, release_result = _command_service(repo).request_release_command(
        session_id="session-lead",
        id="rel-1",
        project_id="project",
        deliverable_ids=["deliverable"],
        request_id="request-release-before-recovery",
    )
    release_promotion_id = release_result["promotion_event_ids"][0]
    state, _failure = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=False,
        error="merge conflict while merging feature",
    )
    assert state.release_packages["rel-1"].state == TeamRecordState.BLOCKED

    state, result = _command_service(repo).recover_release_command(
        session_id="session-lead",
        release_package_id="rel-1",
        agent_branch="refs/heads/agents/lead/release-resolved",
        commit_sha="708b1b89daaa9f47f7bb5f7a96d6935cf4b45932",
        summary="Resolved release merge conflict.",
        request_id="request-release-recovery",
    )
    state, replay = _command_service(repo).recover_release_command(
        session_id="session-lead",
        release_package_id="rel-1",
        agent_branch="refs/heads/agents/lead/release-resolved",
        commit_sha="708b1b89daaa9f47f7bb5f7a96d6935cf4b45932",
        summary="Resolved release merge conflict.",
        request_id="request-release-recovery",
    )

    assert replay == result
    assert result["success"] is True
    assert result["release_package_id"] == "rel-1"
    assert result["promotion_id"] == release_promotion_id
    assert result["agent_branch"] == "agents/lead/release-resolved"
    assert result["repaired_review_manifest_ids"] == []
    assert len(repo.list_outbox_records()) == 3
    recovery_outbox = [
        record
        for record in repo.list_outbox_records()
        if record.intent_type == GIT_RELEASE_RECOVERY_EVENT_TYPE
    ]
    assert len(recovery_outbox) == 1
    assert recovery_outbox[0].aggregate_id == "rel-1"
    assert recovery_outbox[0].idempotency_key == (
        "team-addon:team.git.release_recovery_requested:"
        f"rel-1:{release_promotion_id}:agents/lead/release-resolved:"
        "708b1b89daaa9f47f7bb5f7a96d6935cf4b45932"
    )
    duplicate = repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-duplicate-recovery",
            intent_type=GIT_RELEASE_RECOVERY_EVENT_TYPE,
            aggregate_type="git_release_recovery",
            aggregate_id="rel-1",
            idempotency_key=recovery_outbox[0].idempotency_key,
            payload=recovery_outbox[0].payload,
        )
    )
    assert duplicate.id == recovery_outbox[0].id


def test_failed_promotions_persist_manifests_and_legacy_repair_is_revision_safe(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_review_sources(
        repo,
        task_id="task-a",
        sources={"deliverable-a": "team/team/reviews/deliverable-a"},
    )
    _seed_accepted_review_sources(
        repo,
        task_id="task-b",
        sources={"deliverable-b": "team/team/reviews/deliverable-b"},
    )

    def _seed_retired_leaf_review(current: TeamState) -> TeamState:
        retired_review_id = "review-deliverable-a-retired-tester"
        current.review_requests[retired_review_id] = ReviewRequest(
            id=retired_review_id,
            deliverable_id="deliverable-a",
            reviewer_agent_id="worker",
            state=TeamRecordState.PENDING,
            reviewed_commit_sha="obsolete-tester-commit",
            retired_at=datetime(2026, 7, 29, 20, 4, 33, tzinfo=UTC),
            retired_by="system:leaf-review-policy",
            retirement_reason=(
                "Leaf-review policy changed: tester is reserved for "
                "release-candidate review."
            ),
        )
        current.deliverables["deliverable-a"].review_request_ids.append(
            retired_review_id
        )
        return current

    repo.update(_seed_retired_leaf_review)
    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-package-recovery-command",
    )
    state, release_result = _command_service(repo).request_release_command(
        session_id="session-lead",
        id="rel-package-recovery",
        project_id="project",
        deliverable_ids=["deliverable-a", "deliverable-b"],
        request_id="request-package-before-recovery",
    )
    promotion_ids = release_result["promotion_event_ids"]
    assert len(promotion_ids) == 2
    promotion_by_task = {
        state.git_promotions[promotion_id].task_id: promotion_id
        for promotion_id in promotion_ids
    }
    evidence_by_task = {
        "task-a": (
            "team/team/reviews/deliverable-a",
            "deliverable-a-reviewed",
            "src/a.py",
        ),
        "task-b": (
            "team/team/reviews/deliverable-b",
            "deliverable-b-reviewed",
            "src/b.py",
        ),
    }
    for task_id, promotion_id in promotion_by_task.items():
        source_branch, reviewed_commit_sha, changed_path = evidence_by_task[task_id]
        state, _failure = _team_git_release_service(repo).record_git_promotion_result(
            promotion_id=promotion_id,
            success=False,
            error=f"merge conflict while merging {source_branch}",
            outputs={
                "error_code": "merge_conflict",
                "source_manifests": {
                    source_branch: {
                        "base_commit_sha": "base-reviewed",
                        "reviewed_commit_sha": reviewed_commit_sha,
                        "tree_hash": f"tree-{task_id}",
                        "commit_shas": [reviewed_commit_sha],
                        "changed_paths": [f"M\t{changed_path}"],
                    }
                },
            },
        )
    assert state.release_packages["rel-package-recovery"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable-a"].review_manifest is not None
    assert state.deliverables["deliverable-b"].review_manifest is not None
    assert not [
        record
        for record in repo.list_outbox_records(
            intent_type="team.agent.wake_requested"
        )
        if record.payload["payload"]["agent_id"] == "worker"
    ]

    def _simulate_legacy_missing_manifests(current: TeamState) -> TeamState:
        current.deliverables["deliverable-a"].review_manifest = None
        current.deliverables["deliverable-b"].review_manifest = None
        return current

    state = repo.update(_simulate_legacy_missing_manifests)
    with pytest.raises(TeamStateConflictError):
        _team_git_release_service(repo).repair_release_review_manifests(
            release_package_id="rel-package-recovery",
            expected_revision=state.revision - 1,
        )
    state, repair = _team_git_release_service(repo).repair_release_review_manifests(
        release_package_id="rel-package-recovery",
        expected_revision=state.revision,
    )
    assert repair["success"] is True
    assert repair["applied"] is True
    assert repair["missing_review_manifest_ids"] == []
    assert repair["repaired_review_manifest_ids"] == [
        "deliverable-a",
        "deliverable-b",
    ]
    assert state.deliverables["deliverable-a"].review_manifest.changed_paths == [
        "M\tsrc/a.py"
    ]
    assert state.deliverables["deliverable-b"].review_manifest.changed_paths == [
        "M\tsrc/b.py"
    ]

    second_state, second_repair = _team_git_release_service(
        repo
    ).repair_release_review_manifests(
        release_package_id="rel-package-recovery",
        expected_revision=state.revision,
    )
    assert second_repair["success"] is True
    assert second_repair["applied"] is False
    assert second_repair["repaired_review_manifest_ids"] == []
    assert second_state.deliverables["deliverable-a"].review_manifest is not None
    assert second_state.deliverables["deliverable-b"].review_manifest is not None


def test_apply_ephemeral_action_accepts_matching_agent_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    _team_ephemeral_service(repo).request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "eph-1",
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )

    state, result = _team_ephemeral_service(repo).apply_ephemeral_action(
        session_id="eph-1",
        action={
            "ephemeral_assignment_id": "ephemeral-1",
            "summary": "Done.",
            "agent_branch": "agents/eph-1/ephemeral-1",
            "commit_sha": "deadbeef",
        },
    )

    assert result["success"] is True
    assignment = state.ephemeral_assignments["ephemeral-1"]
    assert assignment.state == EphemeralAssignmentState.COMPLETED
    assert assignment.agent_branch == "agents/eph-1/ephemeral-1"
    assert assignment.commit_sha == "deadbeef"


def test_apply_ephemeral_action_rejects_mismatched_agent_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    _team_ephemeral_service(repo).request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "eph-1",
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )

    state, result = _team_ephemeral_service(repo).apply_ephemeral_action(
        session_id="eph-1",
        action={
            "ephemeral_assignment_id": "ephemeral-1",
            "summary": "Done.",
            "agent_branch": "agents/other/wrong",
        },
    )

    assert result["success"] is False
    assignment = state.ephemeral_assignments["ephemeral-1"]
    assert assignment.state == EphemeralAssignmentState.FAILED
    assert assignment.agent_branch is None
    assert "does not match" in (result["error"] or "")


def test_apply_agent_action_git_review_promotion_parent_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_completed_ephemeral(repo)

    # lead is not the parent of the ephemeral assignment and cannot promote it.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )
    assert result["success"] is False
    assert any("cannot promote" in error for error in result["errors"])
    assert state.git_promotions == {}

    # backend (the parent) may promote its completed assignment's branch.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )
    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    promotion_id = result["git_promotion_event_ids"][0]
    promotion = state.git_promotions[promotion_id]
    assert promotion.promotion_kind == "review"
    assert promotion.state == "pending"
    assert promotion.source_branch == "agents/eph-1/ephemeral-1"
    assert promotion.target_branch == "team/team/reviews/ephemeral-1"
    assert promotion.project_id == "project"
    event = result["events"][0]
    assert event["event_type"] == "team.git.promotion_requested"
    assert event["payload"]["promotion_kind"] == "review"
    assert event["payload"]["managed_repo_id"] == MANAGED_REPO_ID
    assert event["payload"]["source_branch"] == "agents/eph-1/ephemeral-1"
    assert event["payload"]["target_branch"] == "team/team/reviews/ephemeral-1"


def test_apply_agent_action_git_review_promotion_rejects_unrelated_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_completed_ephemeral(repo)
    _seed_task(repo, task_id="other-task")
    _seed_deliverable(
        repo,
        deliverable_id="other-deliverable",
        task_id="other-task",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [
                {
                    "assignment_id": "ephemeral-1",
                    "deliverable_id": "other-deliverable",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("does not belong" in error for error in result["errors"])
    assert result["git_promotion_event_ids"] == []
    assert state.git_promotions == {}


def test_apply_agent_action_git_review_promotion_requires_git_config(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_completed_ephemeral(repo)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )

    assert result["success"] is False
    assert any("no git configuration" in error for error in result["errors"])
    assert state.git_promotions == {}


def test_apply_agent_action_records_deliverable_git_refs(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_git_work_context(
        repo,
        dispatch_id=None,
        agent_branch="agents/session-backend/task",
        commit_sha="deadbeef",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "branch_context_id": "ctx-task",
                    "git_refs": {
                        "agent_branch": "agents/session-backend/task",
                        "commit_sha": "deadbeef",
                    },
                }
            ],
        },
    )

    assert result["success"] is True
    refs = state.deliverables["deliverable"].git_refs
    assert refs.branch_context_id == "ctx-task"
    assert refs.agent_branch == "agents/session-backend/task"
    assert refs.commit_sha == "deadbeef"


def test_apply_agent_action_persistent_git_review_promotion_from_context_branch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_git_work_context(
        repo,
        dispatch_id=None,
        agent_branch="agents/session-backend/task",
        commit_sha="deadbeef",
    )
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "branch_context_id": "ctx-task",
                    "git_refs": {"commit_sha": "deadbeef"},
                }
            ],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is True
    promotion_id = result["git_promotion_event_ids"][0]
    promotion = state.git_promotions[promotion_id]
    assert promotion.promotion_kind == "review"
    assert promotion.deliverable_id == "deliverable"
    assert promotion.task_id == "task"
    assert promotion.source_branch == "agents/session-backend/task"
    assert promotion.target_branch == "team/team/reviews/deliverable"
    assert promotion.commit_sha == "deadbeef"
    assert promotion.git_base_branch == "main"
    assert promotion.base_commit_sha == "base123"
    event = result["events"][0]
    assert event["payload"]["promotion_kind"] == "review"
    assert event["payload"]["deliverable_id"] == "deliverable"
    assert event["payload"]["target_branch"] == "team/team/reviews/deliverable"
    assert event["payload"]["commit_sha"] == "deadbeef"
    assert event["payload"]["base_branch"] == "main"
    assert event["payload"]["base_commit_sha"] == "base123"


def test_apply_agent_action_persistent_git_review_promotion_rejects_missing_source(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is False
    assert any("requires source_branch" in error for error in result["errors"])
    assert state.git_promotions == {}


def test_apply_agent_action_persistent_git_review_promotion_checks_authority(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_git_work_context(
        repo,
        dispatch_id=None,
        agent_branch="agents/session-backend/task",
        commit_sha="deadbeef",
    )
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "branch_context_id": "ctx-task",
                    "git_refs": {"commit_sha": "deadbeef"},
                }
            ],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is False
    assert any("not permitted to promote deliverable" in error for error in result["errors"])
    assert len(state.git_promotions) == 0


def test_apply_agent_action_git_feature_update_default_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_review_sources(
        repo,
        task_id="task",
        sources={
            "ephemeral-1": "team/team/reviews/ephemeral-1",
            "ephemeral-2": "team/team/reviews/ephemeral-2",
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": [
                        "team/team/reviews/ephemeral-1",
                        "team/team/reviews/ephemeral-2",
                    ],
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    promotion = state.git_promotions[result["git_promotion_event_ids"][0]]
    assert promotion.promotion_kind == "feature"
    assert promotion.target_branch == "team/team/features/task-backend"
    event = result["events"][0]
    assert event["payload"]["promotion_kind"] == "feature"
    assert event["payload"]["source_branches"] == [
        "team/team/reviews/ephemeral-1",
        "team/team/reviews/ephemeral-2",
    ]
    assert event["payload"]["expected_source_commits"] == {
        "team/team/reviews/ephemeral-1": "ephemeral-1-reviewed",
        "team/team/reviews/ephemeral-2": "ephemeral-2-reviewed",
    }
    assert event["payload"]["target_branch"] == "team/team/features/task-backend"


def test_apply_agent_action_git_feature_update_supervisor_allowed(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_review_sources(
        repo,
        task_id="task",
        sources={"source-x": "team/team/reviews/x"},
    )

    # lead supervises backend (the task owner) and may consolidate the task branches.
    _state, lead_result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_feature_updates": [
                {"task_id": "task", "source_review_branches": ["team/team/reviews/x"]}
            ],
        },
    )
    assert lead_result["success"] is True

    # worker neither owns nor supervises the task owner.
    _state, worker_result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "git_feature_updates": [
                {"task_id": "task", "source_review_branches": ["team/team/reviews/x"]}
            ],
        },
    )
    assert worker_result["success"] is False
    assert any("not permitted" in error for error in worker_result["errors"])


def test_apply_agent_action_git_feature_update_rejects_bad_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": ["bad:branch"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("invalid branch name" in error for error in result["errors"])
    assert state.git_promotions == {}


def test_record_git_feature_promotion_result_attaches_to_accepted_source_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    def _set_review_branch(state):
        state.deliverables["deliverable"].git_refs.review_branch = "team/team/reviews/deliverable"
        return state

    repo.update(_set_review_branch)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": ["local-evander/team/team/reviews/deliverable"],
                }
            ],
        },
    )
    assert result["success"] is True
    promotion_id = result["git_promotion_event_ids"][0]

    state, promotion_result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=promotion_id,
        success=True,
        outputs={"merged_branches": ["local-evander/team/team/reviews/deliverable"]},
        commit_sha="feedface",
    )

    assert promotion_result["created_deliverable_ids"] == []
    assert promotion_result["updated_deliverable_ids"] == ["deliverable"]
    assert state.git_promotions[promotion_id].deliverable_id == "deliverable"
    assert set(state.deliverables) == {"deliverable"}
    refs = state.deliverables["deliverable"].git_refs
    assert refs.review_branch == "team/team/reviews/deliverable"
    assert refs.feature_branch == "team/team/features/task-backend"
    assert refs.commit_sha == "reviewed-commit"
    assert refs.feature_commit_sha == "feedface"
    assert refs.promotion_record_ids == [promotion_id]


def test_integration_feature_promotion_acknowledges_idempotently_without_synthetic_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _team_admin_service(repo).update_task(
        task_id="task",
        integration_boundary=True,
        deliverable_contract={
            "require_git_provenance": True,
            "required_contribution_task_ids": [],
        },
        expected_revision=repo.read().revision,
    )

    def _seed_promotion(state):
        state.git_promotions["promotion-integration"] = GitPromotionRecord(
            id="promotion-integration",
            project_id="project",
            task_id="task",
            actor_agent_id="backend",
            source_branch="team/team/reviews/integration",
            target_branch="team/team/features/integration",
            promotion_kind="feature",
        )
        return state

    repo.update(_seed_promotion)
    service = _team_git_release_service(repo)
    state, first = service.record_git_promotion_result(
        promotion_id="promotion-integration",
        success=True,
        outputs={"merged_branches": ["team/team/reviews/integration"]},
        commit_sha="a" * 40,
    )
    state, replayed = service.record_git_promotion_result(
        promotion_id="promotion-integration",
        success=True,
        outputs={"merged_branches": ["team/team/reviews/integration"]},
        commit_sha="a" * 40,
    )

    assert first["success"] is True
    assert replayed["success"] is True
    assert first["created_deliverable_ids"] == []
    assert replayed["created_deliverable_ids"] == []
    assert first["diagnostics"][0]["code"] == (
        "integration_deliverable_submission_required"
    )
    assert replayed["diagnostics"] == first["diagnostics"]
    assert state.git_promotions["promotion-integration"].state == "succeeded"
    assert state.git_promotions["promotion-integration"].deliverable_id is None
    assert state.deliverables == {}


def test_record_git_feature_promotion_result_requires_source_branch_match(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    def _set_review_branch(state):
        state.deliverables["deliverable"].git_refs.review_branch = "team/team/reviews/deliverable"
        return state

    repo.update(_set_review_branch)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": ["team/team/reviews/unrelated"],
                }
            ],
        },
    )
    assert result["success"] is False
    assert any("must match exactly one accepted" in error for error in result["errors"])
    assert state.git_promotions == {}
    refs = state.deliverables["deliverable"].git_refs
    assert refs.review_branch == "team/team/reviews/deliverable"
    assert refs.feature_branch is None
    assert refs.commit_sha == "reviewed-commit"
    assert refs.promotion_record_ids == []


def test_repair_feature_promotion_deliverables_removes_safe_synthetic_pending(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    def _seed_stale_synthetic(state):
        state.tasks["task"].state = TeamTaskState.ACCEPTED
        state.deliverables["deliverable"].git_refs.review_branch = "team/team/reviews/deliverable"
        state.deliverables["synthetic"] = Deliverable(
            id="synthetic",
            task_id="task",
            producer_agent_id="backend",
            summary="Consolidated feature branch team/team/features/task-backend",
            state=TeamRecordState.PENDING,
            git_refs=DeliverableGitRefs(
                feature_branch="team/team/features/task-backend",
                commit_sha="feedface",
                promotion_record_ids=["promotion-feature"],
            ),
        )
        state.tasks["task"].deliverable_ids.append("synthetic")
        state.git_promotions["promotion-feature"] = GitPromotionRecord(
            id="promotion-feature",
            project_id="project",
            task_id="task",
            actor_agent_id="backend",
            source_branch="local-evander/team/team/reviews/deliverable",
            target_branch="team/team/features/task-backend",
            promotion_kind="feature",
            deliverable_id="synthetic",
            state="succeeded",
            commit_sha="feedface",
            outputs={"merged_branches": ["local-evander/team/team/reviews/deliverable"]},
        )
        return state

    repo.update(_seed_stale_synthetic)
    state, repair = _team_git_release_service(repo).repair_feature_promotion_deliverables()

    assert repair["success"] is True
    assert repair["applied"] is True
    assert repair["repaired_promotion_ids"] == ["promotion-feature"]
    assert repair["removed_deliverable_ids"] == ["synthetic"]
    assert repair["updated_deliverable_ids"] == ["deliverable"]
    assert "synthetic" not in state.deliverables
    assert state.tasks["task"].deliverable_ids == ["deliverable"]
    promotion = state.git_promotions["promotion-feature"]
    assert promotion.deliverable_id == "deliverable"
    refs = state.deliverables["deliverable"].git_refs
    assert refs.feature_branch == "team/team/features/task-backend"
    assert refs.commit_sha == "reviewed-commit"
    assert refs.feature_commit_sha == "feedface"
    assert refs.promotion_record_ids == ["promotion-feature"]


def _seed_accepted_feature_deliverable(
    repo: TeamStateRepository,
    *,
    deliverable_id: str,
    task_id: str,
    feature: str | None,
    artifact_refs: list[str] | None = None,
) -> None:
    _seed_task(repo, task_id=task_id)
    _team_admin_service(repo).record_deliverable(
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id="backend",
        summary="Deliverable summary.",
        artifact_refs=artifact_refs or [],
    )

    def _mutate(state):
        deliverable = state.deliverables[deliverable_id]
        git_backed = feature is not None or not artifact_refs
        deliverable.state = TeamRecordState.ACCEPTED
        state.tasks[task_id].state = TeamTaskState.FINISHED
        deliverable.verification = DeliverableVerification(
            status="passed",
            summary="Seeded release verification passed.",
        )
        if git_backed:
            deliverable.git_refs.commit_sha = "reviewed-commit"
        review_id = f"review-{deliverable_id}"
        state.review_requests[review_id] = ReviewRequest(
            id=review_id,
            deliverable_id=deliverable_id,
            reviewer_agent_id="worker",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha=("reviewed-commit" if git_backed else None),
        )
        deliverable.review_request_ids = [review_id]
        if git_backed:
            deliverable.review_manifest = DeliverableReviewManifest(
                base_commit_sha="base-commit",
                reviewed_commit_sha="reviewed-commit",
                tree_hash="reviewed-tree",
                commit_shas=["reviewed-commit"],
                changed_paths=["M\tsrc/reviewed.py"],
            )
        if feature is not None:
            deliverable.git_refs.feature_branch = feature
            deliverable.git_refs.feature_commit_sha = "reviewed-commit"
        return state

    repo.update(_mutate)


def _seed_accepted_review_sources(
    repo: TeamStateRepository,
    *,
    task_id: str,
    sources: dict[str, str],
) -> None:
    _seed_task(repo, task_id=task_id)
    for deliverable_id in sources:
        _team_admin_service(repo).record_deliverable(
            deliverable_id=deliverable_id,
            task_id=task_id,
            producer_agent_id="backend",
            summary=f"Reviewed source {deliverable_id}.",
        )

    def _mutate(state):
        for deliverable_id, review_branch in sources.items():
            commit_sha = f"{deliverable_id}-reviewed"
            review_id = f"review-{deliverable_id}"
            deliverable = state.deliverables[deliverable_id]
            deliverable.state = TeamRecordState.ACCEPTED
            state.tasks[task_id].state = TeamTaskState.FINISHED
            deliverable.verification = DeliverableVerification(status="passed")
            deliverable.git_refs.commit_sha = commit_sha
            deliverable.git_refs.review_branch = review_branch
            deliverable.review_request_ids = [review_id]
            state.review_requests[review_id] = ReviewRequest(
                id=review_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id="worker",
                state=TeamRecordState.ACCEPTED,
                reviewed_commit_sha=commit_sha,
            )
            deliverable.review_manifest = DeliverableReviewManifest(
                base_commit_sha="base-commit",
                reviewed_commit_sha=commit_sha,
                tree_hash=f"{deliverable_id}-tree",
                commit_shas=[commit_sha],
                changed_paths=[f"M\tsrc/{deliverable_id}.py"],
            )
        return state

    repo.update(_mutate)


def _seed_context_backed_accepted_git_deliverable(
    repo: TeamStateRepository,
    *,
    task_id: str,
    deliverable_id: str,
    git_base_branch: str,
    base_commit_sha: str,
    branch_kind: str = "review",
    canonical_branch: str | None = None,
) -> None:
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id="backend",
        project_id="project",
        git_base_branch=git_base_branch,
        priority=1,
        title=f"Task {task_id}",
        description="Context-backed Git work.",
        created_by_agent_id="lead",
    )
    context_id = f"ctx-{task_id}"
    agent_branch = f"agents/session-backend/{task_id}"
    admin.create_git_work_context_with_id(
        context_id=context_id,
        agent_id="backend",
        task_id=task_id,
        base_branch=git_base_branch,
        base_commit=base_commit_sha,
        agent_branch=agent_branch,
        commit_sha=f"commit-{task_id}",
    )
    admin.record_deliverable(
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id="backend",
        summary=f"Accepted Git deliverable for {task_id}.",
    )

    def _accept(state):
        commit_sha = f"commit-{task_id}"
        deliverable = state.deliverables[deliverable_id]
        deliverable.state = TeamRecordState.ACCEPTED
        deliverable.verification = DeliverableVerification(status="passed")
        deliverable.git_refs.branch_context_id = context_id
        deliverable.git_refs.agent_branch = agent_branch
        deliverable.git_refs.commit_sha = commit_sha
        if branch_kind == "feature":
            deliverable.git_refs.feature_branch = canonical_branch or (
                f"team/team/features/{task_id}-backend"
            )
            deliverable.git_refs.feature_commit_sha = commit_sha
        else:
            deliverable.git_refs.review_branch = (
                f"team/team/reviews/{deliverable_id}"
            )
        review_id = f"review-{deliverable_id}"
        state.review_requests[review_id] = ReviewRequest(
            id=review_id,
            deliverable_id=deliverable_id,
            reviewer_agent_id="worker",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha=commit_sha,
        )
        deliverable.review_request_ids = [review_id]
        deliverable.review_manifest = DeliverableReviewManifest(
            base_commit_sha=base_commit_sha,
            reviewed_commit_sha=commit_sha,
            tree_hash=f"tree-{task_id}",
            commit_shas=[commit_sha],
            changed_paths=[f"M\tsrc/{task_id}.py"],
        )
        state.tasks[task_id].state = TeamTaskState.FINISHED
        return state

    repo.update(_accept)


def test_feature_promotion_uses_task_context_after_project_default_changes(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="task-main",
        deliverable_id="deliverable-main",
        git_base_branch="main",
        base_commit_sha="base-main",
    )

    def _move_default(state):
        state.projects["project"].git.base_branch = "feature/default-moved"
        return state

    repo.update(_move_default)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task-main",
                    "source_review_branches": [
                        "team/team/reviews/deliverable-main"
                    ],
                }
            ],
        },
    )

    assert result["success"] is True
    promotion = state.git_promotions[result["git_promotion_event_ids"][0]]
    assert promotion.git_base_branch == "main"
    assert promotion.base_commit_sha == "base-main"
    assert result["events"][0]["payload"]["base_branch"] == "main"
    assert result["events"][0]["payload"]["base_commit_sha"] == "base-main"


def test_release_rejects_mixed_task_bases_before_mutation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="task-main",
        deliverable_id="deliverable-main",
        git_base_branch="main",
        base_commit_sha="base-main",
    )
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="task-feature",
        deliverable_id="deliverable-feature",
        git_base_branch="feature/foo",
        base_commit_sha="base-feature",
    )
    before = repo.read()

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-mixed",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable-main", "deliverable-feature"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["error_code"] == "mixed_git_base_branches"
    assert "explicit integration task" in result["errors"][0]
    assert state.release_packages == before.release_packages == {}
    assert state.git_promotions == before.git_promotions == {}

    with pytest.raises(ValueError) as excinfo:
        _team_admin_service(repo).create_release_package_with_result(
            project_id="project",
            deliverable_ids=["deliverable-main", "deliverable-feature"],
            release_id="release-mixed-admin",
        )
    assert getattr(excinfo.value, "code", None) == "mixed_git_base_branches"
    assert repo.read().release_packages == {}

    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="task-integration",
        deliverable_id="deliverable-integration",
        git_base_branch="feature/integration",
        base_commit_sha="base-integration",
        branch_kind="feature",
    )

    def _mark_explicit_integration(current):
        task = current.tasks["task-integration"]
        task.integration_boundary = True
        task.dependency_task_ids = ["task-main", "task-feature"]
        task.deliverable_contract = TaskDeliverableContract(
            require_git_provenance=True,
            required_contribution_task_ids=["task-main", "task-feature"],
        )
        integration_commit = "3" * 40
        integration_deliverable = current.deliverables["deliverable-integration"]
        integration_deliverable.source_deliverable_ids = [
            "deliverable-main",
            "deliverable-feature",
        ]
        integration_deliverable.git_refs.commit_sha = integration_commit
        integration_deliverable.git_refs.submitted_commit_sha = integration_commit
        integration_deliverable.git_refs.feature_commit_sha = integration_commit
        integration_deliverable.review_manifest.reviewed_commit_sha = integration_commit
        integration_deliverable.review_manifest.commit_shas = [integration_commit]
        integration_context = current.git_work_contexts["ctx-task-integration"]
        integration_context.state = GitWorkContextState.SUBMITTED
        integration_context.commit_sha = integration_commit
        current.review_requests[
            "review-deliverable-integration"
        ].reviewed_commit_sha = integration_commit
        return current

    repo.update(_mark_explicit_integration)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-integrated",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable-integration"],
                }
            ],
        },
    )

    assert result["success"] is True, result["errors"]
    integrated_release = state.release_packages["release-integrated"]
    assert integrated_release.deliverable_ids == ["deliverable-integration"]
    assert integrated_release.git_base_branch == "feature/integration"
    assert integrated_release.base_commit_sha == "base-integration"


@pytest.mark.parametrize(
    ("canonical_branch", "expected_destination"),
    [
        (None, "releases/project/release-feature"),
        ("feature/foo", "feature/foo"),
    ],
)
def test_feature_based_release_keeps_publication_destination_explicit(
    tmp_path: Path,
    canonical_branch: str | None,
    expected_destination: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_context_backed_accepted_git_deliverable(
        repo,
        task_id="task-feature",
        deliverable_id="deliverable-feature",
        git_base_branch="feature/foo",
        base_commit_sha="base-feature",
        branch_kind="feature",
    )

    release_request = {
        "id": "release-feature",
        "project_id": "project",
        "deliverable_ids": ["deliverable-feature"],
    }
    if canonical_branch is not None:
        release_request["canonical_branch"] = canonical_branch
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [release_request],
        },
    )

    assert result["success"] is True
    release = state.release_packages["release-feature"]
    assert release.git_base_branch == "feature/foo"
    assert release.base_commit_sha == "base-feature"
    assert release.canonical_branch == expected_destination
    promotion = state.git_promotions[release.git_promotion_record_ids[0]]
    assert promotion.git_base_branch == "feature/foo"
    assert promotion.base_commit_sha == "base-feature"


def test_apply_agent_action_rejects_combined_feature_update_and_release_request(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": ["team/team/reviews/deliverable"],
                }
            ],
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert result["git_promotion_event_ids"] == []
    assert result["release_package_ids"] == []
    assert state.git_promotions == {}
    assert state.release_packages == {}
    error = result["errors"][0]
    assert "cannot combine git_feature_updates and git_release_requests" in error
    action_record = next(iter(state.action_records.values()))
    assert action_record.success is False
    assert action_record.error == error


def test_release_candidate_revalidates_deliverable_after_review_retirement(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-review-retired",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    promotion_id = requested["git_promotion_event_ids"][0]
    assert state.release_packages["release-review-retired"].state == (
        TeamRecordState.PENDING
    )

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-retire-release-evidence",
    )
    state, retired = _command_service(repo).retire_review_command(
        session_id="session-lead",
        review_request_id="review-deliverable",
        reason="The accepted review evidence was attached in error.",
        request_id="request-retire-release-evidence",
    )
    assert retired["success"] is True
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    state, result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=promotion_id,
        success=True,
        commit_sha="candidate-with-retired-evidence",
        outputs={"tree_hash": "candidate-tree"},
    )

    release = state.release_packages["release-review-retired"]
    assert release.state == TeamRecordState.REJECTED
    assert release.manifest is None
    assert release.approved_commit_sha is None
    assert release.gate_failure is not None
    assert release.gate_failure.stage == "configuration"
    assert "release deliverable not accepted" in (release.error or "")
    assert result["git_promotion_event_ids"] == []
    assert not any(
        promotion.promotion_kind == "canonical_push"
        for promotion in state.git_promotions.values()
    )


def test_apply_agent_action_git_release_request_creates_package(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["release_package_ids"] == ["rel-1"]
    release = state.release_packages["rel-1"]
    assert release.state == TeamRecordState.PENDING
    assert release.feature_branches == ["team/team/features/task-backend"]
    assert release.release_branch == "team/team/releases/rel-1"
    assert release.canonical_branch == "releases/project/rel-1"
    assert release.canonical_base_commit_sha == release.base_commit_sha
    assert len(release.git_promotion_record_ids) == 1
    kinds = sorted(
        state.git_promotions[pid].promotion_kind for pid in release.git_promotion_record_ids
    )
    assert kinds == ["release"]
    assert len(result["git_promotion_event_ids"]) == 1
    payloads = {event["payload"]["promotion_kind"]: event["payload"] for event in result["events"]}
    assert payloads["release"]["source_branches"] == ["team/team/features/task-backend"]
    assert payloads["release"]["expected_source_commits"] == {
        "team/team/features/task-backend": "reviewed-commit"
    }
    assert payloads["release"]["allowed_changed_paths"] == ["M\tsrc/reviewed.py"]
    assert payloads["release"]["target_branch"] == "team/team/releases/rel-1"
    assert "canonical_push" not in payloads

    expected_canonical_base = release.canonical_base_commit_sha
    release_promotion_id = release.git_promotion_record_ids[0]
    state, promotion_result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        commit_sha="feedface",
    )
    release = state.release_packages["rel-1"]
    canonical_promotions = [
        state.git_promotions[promotion_id]
        for promotion_id in release.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "canonical_push"
    ]
    assert len(canonical_promotions) == 1
    canonical = canonical_promotions[0]
    assert canonical.source_branch == "team/team/releases/rel-1"
    assert canonical.target_branch == "releases/project/rel-1"
    assert promotion_result["git_promotion_event_ids"] == [canonical.id]
    payloads = {
        event["payload"]["promotion_kind"]: event["payload"] for event in promotion_result["events"]
    }
    assert payloads["canonical_push"]["source_branch"] == "team/team/releases/rel-1"
    assert payloads["canonical_push"]["target_branch"] == "releases/project/rel-1"
    assert payloads["canonical_push"]["managed_repo_id"] == MANAGED_REPO_ID
    assert payloads["canonical_push"]["base_commit_sha"] == expected_canonical_base
    assert "canonical_origin_url" not in payloads["canonical_push"]
    assert "provider" not in payloads["canonical_push"]
    assert state.projects["project"].status == ProjectStatus.ACTIVE


def test_canonical_push_requires_immutable_canonical_base_commit(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-missing-base",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    release_promotion_id = requested["git_promotion_event_ids"][0]

    def _remove_base_provenance(current: TeamState) -> TeamState:
        release = current.release_packages["release-missing-base"]
        release.base_commit_sha = None
        release.canonical_base_commit_sha = None
        current.git_promotions[release_promotion_id].base_commit_sha = None
        return current

    repo.update(_remove_base_provenance)
    state, result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        commit_sha="candidate-without-base",
        outputs={"tree_hash": "candidate-tree"},
    )

    release = state.release_packages["release-missing-base"]
    assert release.state == TeamRecordState.BLOCKED
    assert release.error == (
        "release has no immutable canonical destination base commit for publication"
    )
    assert result["git_promotion_event_ids"] == []
    assert not any(
        promotion.promotion_kind == "canonical_push"
        for promotion in state.git_promotions.values()
    )


def test_canonical_push_rejects_inconsistent_promotion_base(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-inconsistent-base",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    release_promotion_id = requested["git_promotion_event_ids"][0]

    def _add_inconsistent_canonical_promotion(current: TeamState) -> TeamState:
        release = current.release_packages["release-inconsistent-base"]
        promotion = GitPromotionRecord(
            id="promotion-inconsistent-base",
            project_id="project",
            release_package_id=release.id,
            actor_agent_id="lead",
            source_branch=release.release_branch or "team/team/releases/release-inconsistent-base",
            target_branch=release.canonical_branch or "releases/project/release-inconsistent-base",
            promotion_kind="canonical_push",
            git_base_branch=release.git_base_branch,
            base_commit_sha="different-base",
            sources=[
                {
                    "source_branch": release.release_branch
                    or "team/team/releases/release-inconsistent-base",
                    "expected_commit_sha": "candidate-sha",
                }
            ],
        )
        current.git_promotions[promotion.id] = promotion
        release.git_promotion_record_ids.append(promotion.id)
        return current

    repo.update(_add_inconsistent_canonical_promotion)
    state, result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        commit_sha="candidate-sha",
        outputs={"tree_hash": "candidate-tree"},
    )

    release = state.release_packages["release-inconsistent-base"]
    assert release.state == TeamRecordState.BLOCKED
    assert (
        "canonical promotion base different-base does not match canonical destination base"
        in (
        release.error or ""
        )
    )
    assert result["git_promotion_event_ids"] == []


def test_partial_verification_release_requires_audited_lead_waiver(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _make_partial(state):
        state.deliverables["deliverable"].verification = DeliverableVerification(
            status="partial",
            summary="Backend passed; OperatorClient was not run.",
        )
        return state

    repo.update(_make_partial)
    state, missing = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-missing-waiver",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    assert missing["success"] is False
    assert any("authorized verification waiver" in error for error in missing["errors"])
    assert state.release_packages == {}

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-release-missing-waiver",
    )
    _state, command_missing = _command_service(repo).request_release_command(
        session_id="session-lead",
        id="release-command-missing-waiver",
        project_id="project",
        deliverable_ids=["deliverable"],
        request_id="request-release-command-missing-waiver",
    )
    waiver_requirement = command_missing["errors"][0]["correction_context"][
        "verification_waiver_requirements"
    ][0]
    assert waiver_requirement == {
        "deliverable_id": "deliverable",
        "verification_status": "partial",
        "caller_authorized": True,
        "authorized_actor_roles": [
            "project_lead",
            "top_level_team_manager",
            "operator",
        ],
        "required_input": {
            "verification_waivers": [
                {
                    "deliverable_id": "deliverable",
                    "reason": "<concrete evidence-based reason>",
                }
            ]
        },
        "effect": (
            "Admits incomplete deliverable evidence at package intake; configured "
            "release verification and review gates still apply."
        ),
    }

    state, accepted = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-waived",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                    "verification_waivers": [
                        {
                            "deliverable_id": "deliverable",
                            "reason": "OperatorClient environment is unavailable; ship backend only.",
                        }
                    ],
                }
            ],
        },
    )

    assert accepted["success"] is True
    waiver = state.release_packages["release-waived"].verification_waivers[0]
    assert waiver.verification_status == "partial"
    assert waiver.actor_kind == "agent"
    assert waiver.actor_id == "lead"


def test_ordinary_release_role_cannot_authorize_verification_waiver(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _grant_release_only(state):
        state.roles["project__backend"].permissions = [RolePermission.REQUEST_RELEASE]
        state.deliverables["deliverable"].verification = DeliverableVerification(status="partial")
        return state

    repo.update(_grant_release_only)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_release_requests": [
                {
                    "id": "release-unauthorized-waiver",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                    "verification_waivers": [
                        {"deliverable_id": "deliverable", "reason": "Skip it."}
                    ],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("project lead or top-level team manager" in error for error in result["errors"])
    assert state.release_packages == {}


def test_failed_verification_cannot_be_waived(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _make_failed(state):
        state.deliverables["deliverable"].verification = DeliverableVerification(
            status="failed",
            summary="OperatorClient test compilation failed.",
        )
        return state

    repo.update(_make_failed)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-failed",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                    "verification_waivers": [
                        {"deliverable_id": "deliverable", "reason": "Accept the risk."}
                    ],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("failed verification and cannot be released" in error for error in result["errors"])
    assert state.release_packages == {}


def test_operator_verification_waiver_records_authenticated_actor(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _make_not_run(state):
        state.deliverables["deliverable"].verification = DeliverableVerification(status="not_run")
        return state

    repo.update(_make_not_run)
    state, result = _team_admin_service(repo).create_release_package_with_result(
        project_id="project",
        deliverable_ids=["deliverable"],
        release_id="operator-waived",
        verification_waivers=[
            {
                "deliverable_id": "deliverable",
                "reason": "Approved by the authenticated release operator.",
            }
        ],
        operator_actor_id="operator:release-admin",
    )

    assert result["success"] is True
    waiver = state.release_packages["operator-waived"].verification_waivers[0]
    assert waiver.actor_kind == "operator"
    assert waiver.actor_id == "operator:release-admin"


def test_canonical_push_waits_for_candidate_verification_and_release_review(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_review_profile(repo, applicable_role_ids=["project__worker"])
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _configure_release_gates(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            verification_commands=[
                ReleaseVerificationCommand(
                    name="operator-client",
                    cwd="Src/Clients/OperatorClient",
                    argv=["npm", "test", "--", "--runInBand"],
                )
            ],
            review_requirements=[
                ReleaseReviewRequirement(
                    reviewer_agent_id="worker",
                    review_profile_id="profile",
                )
            ],
        )
        return state

    repo.update(_configure_release_gates)
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-gated",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    assert requested["success"] is True
    release_promotion_id = state.release_packages["release-gated"].git_promotion_record_ids[0]

    state, consolidated = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        commit_sha="candidate-feedface",
        outputs={"base_commit_sha": "base-main", "tree_hash": "candidate-tree"},
    )
    release = state.release_packages["release-gated"]
    assert release.candidate_commit_sha == "candidate-feedface"
    assert release.candidate_tree_hash == "candidate-tree"
    assert release.approved_commit_sha is None
    assert len(release.verification_run_ids) == 1
    assert release.review_request_ids == []
    assert all(
        state.git_promotions[promotion_id].promotion_kind != "canonical_push"
        for promotion_id in release.git_promotion_record_ids
    )
    assert consolidated["events"][0]["event_type"] == ("team.release.verification_requested")

    run_id = release.verification_run_ids[0]
    state, verified = _team_release_gate_service(repo).record_verification_result(
        release_package_id="release-gated",
        verification_run_id=run_id,
        candidate_commit_sha="candidate-feedface",
        verification_state="passed",
        command_results=[{"name": "operator-client", "state": "passed", "exit_code": 0}],
    )
    assert verified["success"] is True
    review_id = state.release_packages["release-gated"].review_request_ids[0]
    review = state.review_requests[review_id]
    assert review.release_package_id == "release-gated"
    assert review.candidate_commit_sha == "candidate-feedface"
    assert review.reviewed_commit_sha == "candidate-feedface"
    state.release_packages["release-gated"].candidate_commit_sha = "different-candidate"
    assert _review_decision_policy_error(
        state,
        review=review,
        decision_state=TeamRecordState.REJECTED,
        criterion_results=[],
        finding_resolutions=[],
    ) == (
        f"release review {review.id} targets candidate candidate-feedface, "
        "not the pinned candidate different-candidate"
    )

    state, decided = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": review_id,
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )

    assert decided["success"] is True
    release = state.release_packages["release-gated"]
    assert release.approved_commit_sha == "candidate-feedface"
    canonical = [
        state.git_promotions[promotion_id]
        for promotion_id in release.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "canonical_push"
    ]
    assert len(canonical) == 1
    assert [source.model_dump() for source in canonical[0].sources] == [
        {
            "source_branch": "team/team/releases/release-gated",
            "expected_commit_sha": "candidate-feedface",
        }
    ]


def test_release_review_uses_criteria_snapshot_after_profile_edit(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_review_profile(repo, applicable_role_ids=["project__worker"])
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _configure_release_review(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            review_requirements=[
                ReleaseReviewRequirement(
                    reviewer_agent_id="worker",
                    review_profile_id="profile",
                )
            ]
        )
        return state

    repo.update(_configure_release_review)
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-frozen-review",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    state, _ = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=requested["git_promotion_event_ids"][0],
        success=True,
        commit_sha="candidate-sha",
        outputs={"tree_hash": "candidate-tree"},
    )
    review_id = state.release_packages["release-frozen-review"].review_request_ids[0]
    assert [
        criterion.id for criterion in state.review_requests[review_id].criteria_snapshot or []
    ] == ["tests", "docs"]

    state = _team_admin_service(repo).update_review_profile(
        profile_id="profile",
        criteria=[],
        expected_revision=state.revision,
    )
    assert state.review_profiles["profile"].criteria == []

    state, missing = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": review_id, "state": "accepted"}],
        },
    )
    assert missing["success"] is False
    assert any("missing required criterion results: tests" in error for error in missing["errors"])
    assert state.review_requests[review_id].state == TeamRecordState.PENDING
    assert state.release_packages["release-frozen-review"].approved_commit_sha is None

    state, accepted = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": review_id,
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )
    assert accepted["success"] is True
    assert state.release_packages["release-frozen-review"].approved_commit_sha == "candidate-sha"


def test_release_candidate_runs_ci_before_configured_release_reviews_and_push(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    admin = _team_admin_service(repo)
    admin.create_review_profile(
        profile_id="release-review-profile",
        team_id="team",
        project_id="project",
        name="Release review",
        expected_revision=repo.read().revision,
    )
    admin.create_review_profile(
        profile_id="release-test-profile",
        team_id="team",
        project_id="project",
        name="Release test review",
        expected_revision=repo.read().revision,
    )

    def _configure_policy(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            verification_commands=[
                ReleaseVerificationCommand(
                    name="solution tests",
                    cwd="Src",
                    argv=["dotnet", "test", "./App.slnx"],
                    timeout_seconds=7200,
                )
            ],
            review_requirements=[
                ReleaseReviewRequirement(
                    reviewer_agent_id="backend",
                    review_profile_id="release-review-profile",
                ),
                ReleaseReviewRequirement(
                    reviewer_agent_id="worker",
                    review_profile_id="release-test-profile",
                ),
            ],
        )
        return state

    repo.update(_configure_policy)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )
    state, release_result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-gated",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    release_promotion_id = release_result["git_promotion_event_ids"][0]

    state, promotion_result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        outputs={"tree_hash": "candidate-tree"},
        commit_sha="candidate-sha",
    )

    release = state.release_packages["rel-gated"]
    assert release.candidate_commit_sha == "candidate-sha"
    assert release.candidate_tree_hash == "candidate-tree"
    assert release.approved_commit_sha is None
    assert release.review_request_ids == []
    assert len(release.verification_run_ids) == 1
    assert not any(
        promotion.promotion_kind == "canonical_push" for promotion in state.git_promotions.values()
    )
    assert [event["event_type"] for event in promotion_result["events"]] == [
        "team.release.verification_requested"
    ]

    verification_run_id = release.verification_run_ids[0]
    state, verification_result = TeamReleaseGateService(repo).record_verification_result(
        release_package_id=release.id,
        verification_run_id=verification_run_id,
        candidate_commit_sha="candidate-sha",
        verification_state="passed",
        command_results=[{"name": "solution tests", "state": "passed", "exit_code": 0}],
    )

    release = state.release_packages["rel-gated"]
    assert state.release_verification_runs[verification_run_id].state == "passed"
    assert len(release.review_request_ids) == 2
    assert verification_result["created_review_request_ids"] == release.review_request_ids
    assert release.approved_commit_sha is None
    assert not any(
        promotion.promotion_kind == "canonical_push" for promotion in state.git_promotions.values()
    )

    review_by_agent = {
        state.review_requests[review_id].reviewer_agent_id: review_id
        for review_id in release.review_request_ids
    }
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="dispatch-release-review")
    state, _first_review = _command_service(repo).decide_review_command(
        session_id="session-backend",
        review_request_id=review_by_agent["backend"],
        state="accepted",
        request_id="request-release-review-backend",
    )
    assert state.release_packages["rel-gated"].approved_commit_sha is None

    _lease_agent_dispatch(repo, agent_id="worker", dispatch_id="dispatch-release-test")
    state, second_review = _command_service(repo).decide_review_command(
        session_id="session-worker",
        review_request_id=review_by_agent["worker"],
        state="accepted",
        request_id="request-release-review-worker",
    )

    release = state.release_packages["rel-gated"]
    assert release.approved_commit_sha == "candidate-sha"
    canonical = next(
        promotion
        for promotion in state.git_promotions.values()
        if promotion.promotion_kind == "canonical_push"
    )
    assert canonical.sources[0].expected_commit_sha == "candidate-sha"
    canonical_event = next(
        event
        for event in second_review["events"]
        if event["payload"]["promotion_kind"] == "canonical_push"
    )
    assert canonical_event["payload"]["expected_commit_sha"] == "candidate-sha"


def test_candidate_verification_failure_rejects_unchanged_release_content(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _configure_policy(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            verification_commands=[
                ReleaseVerificationCommand(
                    name="operator-client",
                    cwd="Src/Clients/OperatorClient",
                    argv=["npm", "test"],
                )
            ],
            allow_ungated=True,
        )
        return state

    repo.update(_configure_policy)
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-broken",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    promotion_id = requested["git_promotion_event_ids"][0]
    state, _ = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=promotion_id,
        success=True,
        commit_sha="candidate-broken",
        outputs={"base_commit_sha": "base-main", "tree_hash": "unchanged-tree"},
    )

    release = state.release_packages["release-broken"]
    run_id = release.verification_run_ids[0]
    assert release.manifest is not None
    assert release.manifest.candidate_commit_sha == "candidate-broken"
    assert release.manifest.candidate_tree_hash == "unchanged-tree"
    assert release.manifest.verification_commands[0].name == "operator-client"
    evidence = release.manifest.deliverables[0]
    assert evidence.verification is not None
    assert evidence.verification.status == "passed"
    assert evidence.review_manifest is not None
    assert evidence.review_manifest.reviewed_commit_sha == "reviewed-commit"
    assert evidence.accepted_reviews[0].reviewed_commit_sha == "reviewed-commit"

    state, verification_failure = _team_release_gate_service(repo).record_verification_result(
        release_package_id=release.id,
        verification_run_id=run_id,
        candidate_commit_sha="candidate-broken",
        verification_state="failed",
        failure_kind="candidate",
        command_results=[
            {
                "name": "operator-client",
                "state": "failed",
                "exit_code": 1,
                "log_excerpt": "TS2339: property does not exist",
            }
        ],
        error="operator-client tests failed",
    )
    failed = state.release_packages["release-broken"]
    assert failed.state == TeamRecordState.REJECTED
    assert failed.gate_failure is not None
    assert failed.gate_failure.kind == "candidate"
    assert len(verification_failure["created_inbox_item_ids"]) == 1
    failure_notice = state.inbox[verification_failure["created_inbox_item_ids"][0]]
    assert failure_notice.recipient_agent_id == "lead"
    assert failure_notice.payload["kind"] == "release_verification_correction_work"
    assert (
        failure_notice.task_id
        == state.release_verification_runs[run_id].correction_task_id
    )
    wake_event = next(
        event
        for event in verification_failure["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    )
    assert wake_event["payload"]["agent_id"] == "lead"
    wake_outbox = next(
        record
        for record in repo.list_outbox_records(
            intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE
        )
        if record.idempotency_key == wake_event["idempotency_key"]
    )
    assert wake_outbox.payload["payload"]["agent_id"] == "lead"

    state, replacement = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-unchanged",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    replacement_promotion_id = replacement["git_promotion_event_ids"][0]
    state, unchanged_result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=replacement_promotion_id,
        success=True,
        commit_sha="candidate-new-commit",
        outputs={"base_commit_sha": "base-main", "tree_hash": "unchanged-tree"},
    )
    unchanged = state.release_packages["release-unchanged"]
    assert unchanged.state == TeamRecordState.REJECTED
    assert unchanged.verification_run_ids == []
    assert unchanged.gate_failure is not None
    assert unchanged.gate_failure.kind == "candidate"
    assert "unchanged from failed release release-broken" in (unchanged.error or "")
    remediation_blocker = state.blockers[
        unchanged_result["release_remediation_blocker_id"]
    ]
    assert remediation_blocker.owner_agent_id == "lead"
    assert remediation_blocker.priority == TeamInboxPriority.HIGH
    assert "escalate it to an operator" in remediation_blocker.details
    remediation_wake = next(
        event
        for event in unchanged_result["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    )
    assert any(
        record.idempotency_key == remediation_wake["idempotency_key"]
        for record in repo.list_outbox_records(
            intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE
        )
    )


def test_transient_verification_retry_creates_linked_attempt_for_same_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _configure_policy(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            verification_commands=[
                ReleaseVerificationCommand(
                    name="solution-tests",
                    cwd=".",
                    argv=["dotnet", "test"],
                )
            ],
            allow_ungated=True,
        )
        return state

    repo.update(_configure_policy)
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-transient",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    state, _ = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=requested["git_promotion_event_ids"][0],
        success=True,
        commit_sha="candidate-sha",
        outputs={"tree_hash": "candidate-tree"},
    )
    first_run_id = state.release_packages["release-transient"].verification_run_ids[0]
    state, _ = _team_release_gate_service(repo).record_verification_result(
        release_package_id="release-transient",
        verification_run_id=first_run_id,
        candidate_commit_sha="candidate-sha",
        verification_state="failed",
        failure_kind="infrastructure",
        command_results=[
            {"name": "solution-tests", "state": "failed", "log_excerpt": "worker lost"}
        ],
        error="verification worker disconnected",
    )
    blocked = state.release_packages["release-transient"]
    assert blocked.state == TeamRecordState.BLOCKED
    assert blocked.gate_failure is not None
    assert blocked.gate_failure.kind == "infrastructure"

    _lease_agent_dispatch(repo, agent_id="lead", dispatch_id="dispatch-release-retry")
    state, retried = _command_service(repo).retry_release_gate_command(
        session_id="session-lead",
        release_package_id="release-transient",
        gate_kind="verification",
        gate_id=first_run_id,
        reason="Worker was replaced after a host failure.",
        request_id="request-release-retry",
    )
    release = state.release_packages["release-transient"]
    second_run_id = retried["replacement_gate_id"]
    first_run = state.release_verification_runs[first_run_id]
    second_run = state.release_verification_runs[second_run_id]
    assert release.state == TeamRecordState.PENDING
    assert release.candidate_commit_sha == "candidate-sha"
    assert release.gate_failure is None
    assert first_run.superseded_by_verification_run_id == second_run_id
    assert second_run.supersedes_verification_run_id == first_run_id
    assert second_run.attempt_number == 2
    assert second_run.candidate_commit_sha == "candidate-sha"
    assert second_run.retry_reason == "Worker was replaced after a host failure."
    assert second_run.retry_requested_by == "agent:lead"
    assert retried["retry_reason"] == "Worker was replaced after a host failure."
    assert retried["retry_requested_by"] == "agent:lead"
    assert retried["events"][0]["payload"]["verification_run_id"] == second_run_id
    persisted_second_run = repo.read().release_verification_runs[second_run_id]
    assert persisted_second_run.retry_reason == second_run.retry_reason
    assert persisted_second_run.retry_requested_by == second_run.retry_requested_by

    state, second_failure = _team_release_gate_service(repo).record_verification_result(
        release_package_id="release-transient",
        verification_run_id=second_run_id,
        candidate_commit_sha="candidate-sha",
        verification_state="failed",
        failure_kind="infrastructure",
        command_results=[
            {
                "name": "solution-tests",
                "state": "failed",
                "log_excerpt": "replacement worker also disconnected",
            }
        ],
        error="replacement verification worker disconnected",
    )
    blocker = state.blockers[second_failure["created_blocker_ids"][0]]
    assert "Do not schedule another agent retry" in blocker.details
    assert "concrete infrastructure recovery requirement" in blocker.details

    _lease_agent_dispatch(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-release-second-retry",
    )
    _state, denied_retry = _command_service(repo).retry_release_gate_command(
        session_id="session-lead",
        release_package_id="release-transient",
        gate_kind="verification",
        gate_id=second_run_id,
        reason="Retry the unchanged candidate again.",
        request_id="request-release-second-agent-retry",
    )
    assert denied_retry["success"] is False
    assert denied_retry["errors"][0]["code"] == "release_gate_retry_invalid"
    assert (
        "agent-requested verification retry already failed"
        in denied_retry["errors"][0]["message"]
    )

    state, operator_retry = _team_admin_service(repo).retry_release_gate_with_result(
        release_package_id="release-transient",
        gate_kind="verification",
        gate_id=second_run_id,
        reason="Operator applied and verified a new worker resource policy.",
        operator_actor_id="operator:release-admin",
    )
    third_run = state.release_verification_runs[operator_retry["replacement_gate_id"]]
    assert third_run.attempt_number == 3
    assert third_run.retry_requested_by == "operator:release-admin"


def test_blocked_release_review_retry_creates_linked_review_for_same_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_review_profile(repo, applicable_role_ids=["project__worker"])
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _configure_policy(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            review_requirements=[
                ReleaseReviewRequirement(
                    reviewer_agent_id="worker",
                    review_profile_id="profile",
                )
            ]
        )
        return state

    repo.update(_configure_policy)
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-review-retry",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    state, _ = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=requested["git_promotion_event_ids"][0],
        success=True,
        commit_sha="candidate-sha",
        outputs={"tree_hash": "candidate-tree"},
    )
    first_review_id = state.release_packages["release-review-retry"].review_request_ids[0]
    state, decision = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": first_review_id,
                    "state": "blocked",
                    "findings": ["Review environment could not load the candidate."],
                }
            ],
        },
    )
    assert decision["success"] is True
    release = state.release_packages["release-review-retry"]
    assert release.state == TeamRecordState.BLOCKED
    assert release.gate_failure is not None
    assert release.gate_failure.kind == "blocked"
    review_blocker = next(
        blocker
        for blocker in state.blockers.values()
        if blocker.source_id == first_review_id
    )
    assert review_blocker.state == TeamBlockerState.OPEN

    state, overwritten = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": first_review_id,
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )
    assert overwritten["success"] is False
    assert "immutable blocked decision" in overwritten["errors"][0]
    assert state.release_packages["release-review-retry"].state == TeamRecordState.BLOCKED

    state, retried = _team_admin_service(repo).retry_release_gate_with_result(
        release_package_id=release.id,
        gate_kind="review",
        gate_id=first_review_id,
        reason="Reviewer environment has been repaired.",
        operator_actor_id="operator:release-admin",
        expected_revision=state.revision,
    )
    second_review_id = retried["replacement_gate_id"]
    first_review = state.review_requests[first_review_id]
    second_review = state.review_requests[second_review_id]
    release = state.release_packages["release-review-retry"]
    assert release.state == TeamRecordState.PENDING
    assert release.candidate_commit_sha == "candidate-sha"
    assert first_review.superseded_by_review_request_id == second_review_id
    assert second_review.supersedes_review_request_id == first_review_id
    assert second_review.candidate_commit_sha == "candidate-sha"
    assert second_review.state == TeamRecordState.PENDING
    assert second_review.retry_reason == "Reviewer environment has been repaired."
    assert second_review.retry_requested_by == "operator:release-admin"
    assert review_blocker.id in retried["resolved_blocker_ids"]
    assert state.blockers[review_blocker.id].state == TeamBlockerState.RESOLVED
    assert state.blockers[review_blocker.id].route == TeamBlockerRoute.RESOLVED
    assert any(
        event.blocker_id == review_blocker.id
        and event.event_type == TeamBlockerEventType.RESOLVED
        and event.actor_agent_id is None
        for event in state.blocker_events.values()
    )
    inbox = state.inbox[retried["created_inbox_item_ids"][0]]
    assert inbox.payload["supersedes_review_request_id"] == first_review_id
    assert inbox.payload["release_manifest"]["candidate_commit_sha"] == "candidate-sha"
    persisted_second_review = repo.read().review_requests[second_review_id]
    assert persisted_second_review.retry_reason == second_review.retry_reason
    assert persisted_second_review.retry_requested_by == second_review.retry_requested_by


def test_rejected_release_review_requires_changed_candidate_content(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_review_profile(repo, applicable_role_ids=["project__worker"])
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    def _configure_policy(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            review_requirements=[
                ReleaseReviewRequirement(
                    reviewer_agent_id="worker",
                    review_profile_id="profile",
                )
            ]
        )
        return state

    repo.update(_configure_policy)
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-rejected-review",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    state, _ = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=requested["git_promotion_event_ids"][0],
        success=True,
        commit_sha="candidate-rejected",
        outputs={"tree_hash": "review-rejected-tree"},
    )
    review_id = state.release_packages["release-rejected-review"].review_request_ids[0]
    state, decision = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": review_id,
                    "state": "rejected",
                    "findings": ["The station-tool test does not compile."],
                }
            ],
        },
    )
    assert decision["success"] is True
    rejected = state.release_packages["release-rejected-review"]
    assert rejected.state == TeamRecordState.REJECTED
    assert rejected.gate_failure is not None
    assert rejected.gate_failure.kind == "rejected"

    state, overwritten = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": review_id,
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )
    assert overwritten["success"] is False
    assert "immutable rejected decision" in overwritten["errors"][0]
    rejected = state.release_packages["release-rejected-review"]

    with pytest.raises(ValueError, match="not blocked"):
        _team_admin_service(repo).retry_release_gate_with_result(
            release_package_id=rejected.id,
            gate_kind="review",
            gate_id=review_id,
            reason="Try the rejected review again.",
            operator_actor_id="operator:release-admin",
        )

    state, replacement = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "release-same-rejected-content",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    state, _ = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=replacement["git_promotion_event_ids"][0],
        success=True,
        commit_sha="different-commit-same-tree",
        outputs={"tree_hash": "review-rejected-tree"},
    )
    unchanged = state.release_packages["release-same-rejected-content"]
    assert unchanged.state == TeamRecordState.REJECTED
    assert "unchanged from failed release release-rejected-review" in (unchanged.error or "")


def test_git_release_request_fails_closed_without_project_release_policy(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)

    def _remove_policy(state):
        state.projects["project"].release_policy = None
        return state

    repo.update(_remove_policy)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-unsafe",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("no release policy" in error for error in result["errors"])
    assert state.release_packages == {}


def test_duplicate_git_release_request_points_to_existing_package(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    assert result["success"] is True

    def _block_release(current):
        current.release_packages["rel-1"].state = TeamRecordState.BLOCKED
        current.release_packages[
            "rel-1"
        ].error = "merge conflict while merging feature into team/team/releases/rel-1"
        return current

    repo.update(_block_release)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert state.release_packages["rel-1"].state == TeamRecordState.BLOCKED
    error = result["errors"][0]
    assert "release package rel-1 already exists" in error
    assert "state blocked" in error
    assert "release_branch team/team/releases/rel-1" in error
    assert "do not submit git_release_requests for this id again" in error


def test_git_release_request_reuses_active_package_for_same_deliverables(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    assert result["success"] is True
    assert result["release_package_ids"] == ["rel-1"]
    assert len(state.git_promotions) == 1
    first_outbox_count = len(repo.list_outbox_records())

    state, duplicate = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-2",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert duplicate["success"] is True
    assert duplicate["duplicate"] is True
    assert duplicate["created"] is False
    assert duplicate["existing_release_package_id"] == "rel-1"
    assert duplicate["release_package_ids"] == ["rel-1"]
    assert list(state.release_packages) == ["rel-1"]
    assert len(state.git_promotions) == 1
    assert len(repo.list_outbox_records()) == first_outbox_count
    assert any(
        "already covers this deliverable set" in hint for hint in duplicate["next_action_hints"]
    )


def test_apply_agent_action_git_release_recovery_emits_recovery_event(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    assert result["success"] is True
    release_promotion_id = state.release_packages["rel-1"].git_promotion_record_ids[0]
    state, _failure = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=False,
        error="merge conflict while merging team/team/features/task-backend",
    )
    assert state.release_packages["rel-1"].state == TeamRecordState.BLOCKED

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_recoveries": [
                {
                    "release_package_id": "rel-1",
                    "agent_branch": "refs/heads/agents/lead/release-resolved",
                    "commit_sha": "reviewed-commit",
                    "summary": "Resolved release merge conflict.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["release_package_ids"] == []
    assert result["release_recovery_event_ids"] == ["rel-1"]
    assert state.release_packages["rel-1"].state == TeamRecordState.BLOCKED
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["event_type"] == GIT_RELEASE_RECOVERY_EVENT_TYPE
    assert event["correlation_key"] == "rel-1"
    assert event["idempotency_key"] == (
        "team-addon:team.git.release_recovery_requested:"
        f"rel-1:{release_promotion_id}:agents/lead/release-resolved:"
        "reviewed-commit"
    )
    assert event["payload"] == {
        "release_package_id": "rel-1",
        "promotion_id": release_promotion_id,
        "promotion_kind": "release",
        "project_id": "project",
        "managed_repo_id": MANAGED_REPO_ID,
        "directory_path": "root",
        "agent_branch": "agents/lead/release-resolved",
        "target_branch": "team/team/releases/rel-1",
        "release_branch": "team/team/releases/rel-1",
            "canonical_branch": "releases/project/rel-1",
            "base_branch": "main",
            "base_commit_sha": "base-commit",
            "expected_commit_sha": "reviewed-commit",
        "source_branches": ["agents/lead/release-resolved"],
        "expected_source_commits": {"agents/lead/release-resolved": "reviewed-commit"},
        "allowed_changed_paths": ["M\tsrc/reviewed.py"],
        "summary": "Resolved release merge conflict.",
    }


def test_apply_agent_action_git_release_recovery_continues_failed_feature_promotion(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    def _set_agent_branch(state):
        state.deliverables["deliverable"].git_refs.agent_branch = "agents/backend/work"
        return state

    repo.update(_set_agent_branch)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    feature_promotion_id = result["git_promotion_event_ids"][0]
    feature_promotion = state.git_promotions[feature_promotion_id]
    assert feature_promotion.promotion_kind == "feature"

    def _remove_review_manifest(current):
        current.deliverables["deliverable"].review_manifest = None
        return current

    repo.update(_remove_review_manifest)
    state, _failure = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=feature_promotion_id,
        success=False,
        error="stale git index lock",
    )
    assert state.release_packages["rel-1"].state == TeamRecordState.BLOCKED

    state, recovery = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_recoveries": [
                {
                    "release_package_id": "rel-1",
                    "agent_branch": "refs/heads/agents/backend/work",
                    "commit_sha": "reviewed-commit",
                }
            ],
        },
    )

    assert recovery["success"] is False
    assert recovery["release_recovery_event_ids"] == []
    assert any("cannot bypass missing reviewed manifests" in error for error in recovery["errors"])
    assert recovery["events"] == []
    assert state.release_packages["rel-1"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable"].git_refs.feature_branch is None


def test_release_recovery_keys_each_failed_promotion_and_keeps_remaining_error(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    older_at = datetime(2026, 7, 9, 10, 0, tzinfo=UTC)
    newer_at = older_at + timedelta(minutes=1)

    def _seed_failures(state):
        state.release_packages["rel-1"] = ReleasePackage(
            id="rel-1",
            project_id="project",
            release_branch="team/team/releases/rel-1",
            canonical_branch="releases/project/rel-1",
            git_promotion_record_ids=["promotion-old", "promotion-new"],
            state=TeamRecordState.BLOCKED,
            error="new failure",
        )
        state.git_promotions["promotion-old"] = GitPromotionRecord(
            id="promotion-old",
            project_id="project",
            actor_agent_id="lead",
            source_branch="agents/lead/release-resolved",
            target_branch="team/team/releases/rel-1",
            promotion_kind="release",
            release_package_id="rel-1",
            state="failed",
            error="old failure",
            created_at=older_at,
            updated_at=older_at,
        )
        state.git_promotions["promotion-new"] = GitPromotionRecord(
            id="promotion-new",
            project_id="project",
            actor_agent_id="lead",
            source_branch="agents/lead/release-resolved",
            target_branch="team/team/releases/rel-1",
            promotion_kind="release",
            release_package_id="rel-1",
            state="failed",
            error="new failure",
            created_at=newer_at,
            updated_at=newer_at,
        )
        return state

    repo.update(_seed_failures)
    sha = "708b1b89daaa9f47f7bb5f7a96d6935cf4b45932"
    state, first = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_recoveries": [
                {
                    "release_package_id": "rel-1",
                    "agent_branch": "agents/lead/release-resolved",
                    "commit_sha": sha,
                }
            ],
        },
    )

    first_event = first["events"][0]
    assert first_event["payload"]["promotion_id"] == "promotion-new"
    assert ":rel-1:promotion-new:agents/lead/release-resolved:" in first_event["idempotency_key"]

    state, promotion_result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id="promotion-new",
        success=True,
        commit_sha=sha,
    )

    release = state.release_packages["rel-1"]
    assert release.state == TeamRecordState.BLOCKED
    assert release.error == "old failure"
    assert promotion_result["git_promotion_event_ids"] == []

    _state, second = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_recoveries": [
                {
                    "release_package_id": "rel-1",
                    "agent_branch": "agents/lead/release-resolved",
                    "commit_sha": sha,
                }
            ],
        },
    )

    second_event = second["events"][0]
    assert second_event["payload"]["promotion_id"] == "promotion-old"
    assert ":rel-1:promotion-old:agents/lead/release-resolved:" in second_event["idempotency_key"]
    assert first_event["idempotency_key"] != second_event["idempotency_key"]


def test_apply_agent_action_git_release_recovery_requires_agent_branch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    release_promotion_id = state.release_packages["rel-1"].git_promotion_record_ids[0]
    _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=False,
        error="merge conflict",
    )

    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_recoveries": [
                {
                    "release_package_id": "rel-1",
                    "agent_branch": "team/team/releases/rel-1",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["release_recovery_event_ids"] == []
    assert "agent_branch must be under agents/" in result["errors"][0]


def test_apply_agent_action_git_release_recovery_requires_commit_sha(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )
    assert result["success"] is True
    release_promotion_id = state.release_packages["rel-1"].git_promotion_record_ids[0]
    _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=False,
        error="merge conflict",
    )

    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_recoveries": [
                {
                    "release_package_id": "rel-1",
                    "agent_branch": "agents/lead/release-resolved",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["release_recovery_event_ids"] == []
    assert "commit_sha must be a non-empty string" in result["errors"][0]


def test_apply_agent_action_git_release_request_auto_promotes_review_branch(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    def _set_review_branch(state):
        state.deliverables["deliverable"].git_refs.review_branch = "team/team/reviews/deliverable"
        return state

    repo.update(_set_review_branch)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["release_package_ids"] == ["rel-1"]
    release = state.release_packages["rel-1"]
    assert release.feature_branches == []
    assert release.release_branch == "team/team/releases/rel-1"
    assert release.canonical_branch == "releases/project/rel-1"
    assert len(release.git_promotion_record_ids) == 1
    feature_promotion_id = release.git_promotion_record_ids[0]
    feature_promotion = state.git_promotions[feature_promotion_id]
    assert feature_promotion.promotion_kind == "feature"
    assert feature_promotion.release_package_id == "rel-1"
    assert feature_promotion.task_id == "task"
    assert feature_promotion.source_branch == "team/team/reviews/deliverable"
    assert feature_promotion.target_branch == "team/team/features/task-lead"
    assert result["git_promotion_event_ids"] == [feature_promotion_id]
    payload = result["events"][0]["payload"]
    assert payload["promotion_kind"] == "feature"
    assert payload["release_package_id"] == "rel-1"
    assert payload["source_branches"] == ["team/team/reviews/deliverable"]
    assert payload["managed_repo_id"] == MANAGED_REPO_ID

    state, promotion_result = _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=feature_promotion_id,
        success=True,
        outputs={
            "merged_branches": ["team/team/reviews/deliverable"],
            "source_manifests": {
                "team/team/reviews/deliverable": {
                    "base_commit_sha": "base-commit",
                    "reviewed_commit_sha": "reviewed-commit",
                    "tree_hash": "reviewed-tree",
                    "commit_shas": ["reviewed-commit"],
                    "changed_paths": ["M\tsrc/reviewed.py"],
                }
            },
        },
        commit_sha="feedface",
    )

    deliverable = state.deliverables["deliverable"]
    assert deliverable.git_refs.feature_branch == "team/team/features/task-lead"
    assert deliverable.git_refs.commit_sha == "reviewed-commit"
    assert deliverable.git_refs.feature_commit_sha == "feedface"
    assert deliverable.review_manifest is not None
    assert deliverable.review_manifest.reviewed_commit_sha == "reviewed-commit"
    assert deliverable.review_manifest.tree_hash == "reviewed-tree"
    release = state.release_packages["rel-1"]
    assert release.feature_branches == ["team/team/features/task-lead"]
    release_promotions = [
        state.git_promotions[promotion_id]
        for promotion_id in release.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "release"
    ]
    assert len(release_promotions) == 1
    release_promotion = release_promotions[0]
    assert release_promotion.source_branch == "team/team/features/task-lead"
    assert release_promotion.target_branch == "team/team/releases/rel-1"
    assert promotion_result["git_promotion_event_ids"] == [release_promotion.id]
    payloads = {
        event["payload"]["promotion_kind"]: event["payload"] for event in promotion_result["events"]
    }
    assert payloads["release"]["source_branches"] == ["team/team/features/task-lead"]
    assert payloads["release"]["target_branch"] == "team/team/releases/rel-1"
    assert payloads["release"]["managed_repo_id"] == MANAGED_REPO_ID


def test_apply_agent_action_git_release_request_artifact_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
        artifact_refs=["artifact-space:/teams/team/result"],
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"id": "rel-1", "project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is True
    release = state.release_packages["rel-1"]
    assert release.feature_branches == []
    assert release.artifact_refs == ["artifact-space:/teams/team/result"]
    assert release.release_branch is None
    assert release.canonical_branch is None
    assert release.git_promotion_record_ids == []
    assert result["git_promotion_event_ids"] == []
    assert state.git_promotions == {}


def test_apply_agent_action_git_release_request_mixed_deliverables_requires_git(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="git-deliverable",
        task_id="git-task",
        feature="team/team/features/git-task-backend",
    )
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="artifact-deliverable",
        task_id="artifact-task",
        feature=None,
        artifact_refs=["artifact-space:/teams/team/result"],
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["git-deliverable", "artifact-deliverable"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert state.release_packages == {}
    assert any("no git configuration" in error for error in result["errors"])


def test_apply_agent_action_git_release_request_git_only_requires_git_config(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"id": "rel-1", "project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert state.release_packages == {}
    assert any("no git configuration" in error for error in result["errors"])


def test_apply_agent_action_git_release_request_requires_releasable_ref_or_artifact(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"id": "rel-1", "project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert state.release_packages == {}
    assert any("review branch" in error for error in result["errors"])
    assert any("agent branch" in error for error in result["errors"])
    assert any("artifact refs" in error for error in result["errors"])


def test_apply_agent_action_git_release_request_allows_project_release_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _register_release_operator(repo)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "release-operator",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["release_package_ids"] == ["rel-1"]
    release = state.release_packages["rel-1"]
    assert release.feature_branches == ["team/team/features/task-backend"]
    assert release.release_branch == "team/team/releases/rel-1"


def test_apply_agent_action_git_release_request_rejects_unaccepted(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [{"project_id": "project", "deliverable_ids": ["deliverable"]}],
        },
    )

    assert result["success"] is False
    assert any("not accepted" in error for error in result["errors"])
    assert state.release_packages == {}


def test_apply_agent_action_git_release_request_requires_lead_or_manager(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "git_release_requests": [{"project_id": "project", "deliverable_ids": ["deliverable"]}],
        },
    )

    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.release_packages == {}


def test_apply_agent_action_creates_child_task_with_graph_fields(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")
    _seed_task(repo, task_id="dep", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child task",
                    "description": "Decomposed work.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["dep"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert "child" in result["created_task_ids"]
    child = state.tasks["child"]
    assert child.parent_task_id == "parent"
    assert child.dependency_task_ids == ["dep"]
    assert state.child_task_ids("parent") == ["child"]


def test_apply_agent_action_rejects_batch_child_under_subordinate_parent(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-parent", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "worker-parent",
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Worker parent",
                    "description": "Subordinate-owned parent task.",
                    "parent_task_id": "backend-parent",
                },
                {
                    "id": "worker-child",
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Worker child",
                    "description": "Must not bypass mandate through batch-local parent.",
                    "parent_task_id": "worker-parent",
                },
            ],
        },
    )

    assert result["created_task_ids"] == ["worker-parent"]
    assert any(
        "can only create child tasks under its own active tasks" in error
        for error in result["errors"]
    )
    assert "worker-parent" in state.tasks
    assert "worker-child" not in state.tasks


def test_apply_agent_action_task_creation_rejects_missing_parent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child",
                    "description": "Refers to a missing parent.",
                    "parent_task_id": "ghost",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("missing parent" in error for error in result["errors"])
    assert "child" not in state.tasks


def test_apply_agent_action_task_creation_rejects_cross_project_dependency(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")
    _team_admin_service(repo).create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="other",
        owner_agent_id="lead-2",
        project_id="project-2",
        priority=1,
        title="Other project task",
        description="Lives in project-2.",
        created_by_agent_id="lead-2",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child",
                    "description": "Depends across projects.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["other"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("belongs to project" in error for error in result["errors"])
    assert "child" not in state.tasks


def test_apply_agent_action_task_creation_batch_rejects_sibling_cycle(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "a",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "A",
                    "description": "First task.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["b"],
                },
                {
                    "id": "b",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "B",
                    "description": "Second task.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["a"],
                },
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("dependency cycle" in error for error in result["errors"])
    assert "a" not in state.tasks
    assert "b" not in state.tasks


def test_apply_agent_action_task_creation_rejects_mixed_hierarchy_dependency_cycle(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child",
                    "description": "Must not wait on its own parent.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["parent"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("hierarchy/dependency cycle" in error for error in result["errors"])
    assert "child" not in state.tasks


def test_apply_agent_action_task_creation_rejects_conflicting_task_id_alias(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "task_id": "canonical",
                    "id": "legacy",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "title": "Conflicting ids",
                    "description": "Both id forms disagree.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("conflicting task_id and id" in error for error in result["errors"])
    assert "canonical" not in state.tasks
    assert "legacy" not in state.tasks


def test_apply_agent_action_owner_edits_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="a", owner_agent_id="backend")
    _seed_task(repo, task_id="b", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "a", "dependency_task_ids": ["b"]}],
        },
    )

    assert result["success"] is True
    assert state.tasks["a"].dependency_task_ids == ["b"]
    assert "a" in result["updated_task_ids"]


def test_apply_agent_action_task_update_rejects_dependency_cycle(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="a", owner_agent_id="backend")
    _seed_task(repo, task_id="b", owner_agent_id="backend")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "a", "dependency_task_ids": ["b"]}],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "b", "dependency_task_ids": ["a"]}],
        },
    )

    assert result["success"] is False
    assert any("cycle" in error for error in result["errors"])
    assert state.tasks["b"].dependency_task_ids == []
    assert state.tasks["a"].dependency_task_ids == ["b"]


def test_apply_agent_action_supervisor_edits_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="w1", owner_agent_id="worker")
    _seed_task(repo, task_id="w2", owner_agent_id="worker")

    # backend supervises worker but is neither project lead nor team manager.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "dependency_task_ids": ["w2"]}],
        },
    )

    assert result["success"] is True
    assert state.tasks["w1"].dependency_task_ids == ["w2"]


def test_apply_agent_action_supervisor_claims_subordinate_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="w1", owner_agent_id="worker")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "owner_agent_id": "backend"}],
        },
    )

    assert result["success"] is True
    assert state.tasks["w1"].owner_agent_id == "backend"
    assert "w1" in result["updated_task_ids"]
    assert "backend" in state.agent_runtimes


def test_apply_agent_action_supervisor_reassigns_subordinate_task(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="worker-2",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-worker-2",
        reports_to_agent_id="backend",
        expected_revision=repo.read().revision,
    )
    _seed_task(repo, task_id="w1", owner_agent_id="worker")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "owner_agent_id": "worker-2"}],
        },
    )

    assert result["success"] is True
    assert state.tasks["w1"].owner_agent_id == "worker-2"
    assert "w1" in result["updated_task_ids"]


def test_task_reassignment_reconciles_all_local_and_shared_blockers(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="w1", owner_agent_id="worker")
    _seed_task(repo, task_id="worker-other", owner_agent_id="worker")
    _seed_task(repo, task_id="backend-other", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable-w1",
        task_id="w1",
        producer_agent_id="worker",
    )

    def _seed_blockers(state):
        review = ReviewRequest(
            id="review-w1",
            deliverable_id="deliverable-w1",
            reviewer_agent_id="worker",
            requested_by_agent_id="backend",
        )
        state.review_requests[review.id] = review
        state.deliverables["deliverable-w1"].review_request_ids = [review.id]
        specs = [
            {
                "source_kind": TeamBlockerSourceKind.TASK,
                "source_id": "w1",
                "task_id": "w1",
                "affected_task_ids": [],
            },
            {
                "source_kind": TeamBlockerSourceKind.DELIVERABLE,
                "source_id": "deliverable-w1",
                "task_id": None,
                "affected_task_ids": [],
            },
            {
                "source_kind": TeamBlockerSourceKind.REVIEW_REQUEST,
                "source_id": "review-w1",
                "task_id": None,
                "affected_task_ids": [],
            },
            {
                "source_kind": TeamBlockerSourceKind.EXPLICIT,
                "source_id": "shared-work",
                "task_id": None,
                "affected_task_ids": ["w1", "worker-other"],
            },
            {
                "source_kind": TeamBlockerSourceKind.AGENT_EXECUTION_STALL,
                "source_id": "worker",
                "task_id": None,
                "affected_task_ids": ["w1"],
            },
            {
                "source_kind": TeamBlockerSourceKind.AGENT_EXECUTION_STALL,
                "source_id": "backend",
                "task_id": None,
                "affected_task_ids": ["backend-other"],
            },
            {
                "source_kind": TeamBlockerSourceKind.EXPLICIT,
                "source_id": "unrelated-work",
                "task_id": None,
                "affected_task_ids": ["backend-other"],
            },
        ]
        for spec in specs:
            blocker, _created = open_or_get_blocker(
                state,
                source_kind=spec["source_kind"],
                source_id=spec["source_id"],
                summary=f"Blocker for {spec['source_id']}",
                reporter_agent_id=(
                    "backend"
                    if spec["source_id"] in {"backend", "unrelated-work"}
                    else "worker"
                ),
                team_id="team",
                project_id="project",
                task_id=spec["task_id"],
                affected_task_ids=spec["affected_task_ids"],
                result={"events": []},
            )
            if spec["source_id"] in {"w1", "deliverable-w1", "review-w1"}:
                blocker.delegate_agent_id = "worker"
                blocker.route = TeamBlockerRoute.DELEGATED
                blocker.active_dispatch_id = "dispatch-obsolete"
                blocker.follow_up_at = datetime(2099, 1, 1, tzinfo=UTC)
                blocker.escalation_depth = 3
        return state

    repo.update(_seed_blockers)
    before = repo.read()
    shared_before = next(
        blocker.model_dump(mode="json")
        for blocker in before.blockers.values()
        if blocker.source_id == "shared-work"
    )
    unrelated_before = next(
        blocker.model_dump(mode="json")
        for blocker in before.blockers.values()
        if blocker.source_id == "unrelated-work"
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "owner_agent_id": "backend"}],
        },
    )

    assert result["success"] is True
    assert state.tasks["w1"].owner_agent_id == "backend"
    blockers_by_source = {
        (blocker.source_kind, blocker.source_id): blocker
        for blocker in state.blockers.values()
    }
    for source_kind, source_id in (
        (TeamBlockerSourceKind.TASK, "w1"),
        (TeamBlockerSourceKind.DELIVERABLE, "deliverable-w1"),
        (TeamBlockerSourceKind.REVIEW_REQUEST, "review-w1"),
    ):
        blocker = blockers_by_source[(source_kind, source_id)]
        assert blocker.state == TeamBlockerState.OPEN
        assert blocker.reporter_agent_id == "worker"
        assert blocker.owner_agent_id == "lead"
        assert blocker.route == TeamBlockerRoute.OWNED
        assert blocker.delegate_agent_id is None
        assert blocker.active_dispatch_id is None
        assert blocker.follow_up_at is None
        assert blocker.escalation_depth == 0

    old_owner_stall = blockers_by_source[
        (TeamBlockerSourceKind.AGENT_EXECUTION_STALL, "worker")
    ]
    assert old_owner_stall.state == TeamBlockerState.RESOLVED
    assert old_owner_stall.affected_task_ids == []
    assert old_owner_stall.id in result["resolved_blocker_ids"]
    new_owner_stall = blockers_by_source[
        (TeamBlockerSourceKind.AGENT_EXECUTION_STALL, "backend")
    ]
    assert new_owner_stall.state == TeamBlockerState.OPEN
    assert new_owner_stall.affected_task_ids == ["backend-other", "w1"]
    assert blockers_by_source[
        (TeamBlockerSourceKind.EXPLICIT, "shared-work")
    ].model_dump(mode="json") == shared_before
    assert blockers_by_source[
        (TeamBlockerSourceKind.EXPLICIT, "unrelated-work")
    ].model_dump(mode="json") == unrelated_before


def test_task_cancellation_reconciles_all_local_and_shared_blockers(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id="backend")
    _seed_task(repo, task_id="other", owner_agent_id="backend")
    _seed_deliverable(
        repo,
        deliverable_id="deliverable-task",
        task_id="task",
        producer_agent_id="backend",
    )

    def _seed_blockers(state):
        now = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)
        review = ReviewRequest(
            id="review-task",
            deliverable_id="deliverable-task",
            reviewer_agent_id="worker",
            requested_by_agent_id="lead",
            state=TeamRecordState.BLOCKED,
            block_kind=ReviewBlockKind.SYSTEM,
            findings=["The review environment was unavailable."],
            created_at=now,
            decision_at=now,
        )
        state.review_requests[review.id] = review
        deliverable = state.deliverables["deliverable-task"]
        deliverable.review_request_ids = [review.id]
        deliverable.state = TeamRecordState.BLOCKED
        state.tasks["task"].state = TeamTaskState.BLOCKED
        state.tasks["task"].state_entered_at = now
        specs = [
            (TeamBlockerSourceKind.TASK, "task", "task", []),
            (
                TeamBlockerSourceKind.DELIVERABLE,
                "deliverable-task",
                None,
                [],
            ),
            (
                TeamBlockerSourceKind.REVIEW_REQUEST,
                "review-task",
                None,
                [],
            ),
            (
                TeamBlockerSourceKind.EXPLICIT,
                "shared-multi",
                None,
                ["task", "other"],
            ),
            (
                TeamBlockerSourceKind.EXPLICIT,
                "shared-singleton",
                None,
                ["task"],
            ),
            (
                TeamBlockerSourceKind.EXPLICIT,
                "unrelated",
                None,
                ["other"],
            ),
        ]
        for source_kind, source_id, blocker_task_id, affected_task_ids in specs:
            open_or_get_blocker(
                state,
                source_kind=source_kind,
                source_id=source_id,
                summary=f"Blocker for {source_id}",
                reporter_agent_id=(
                    "worker"
                    if source_kind == TeamBlockerSourceKind.REVIEW_REQUEST
                    else "backend"
                ),
                team_id="team",
                project_id="project",
                task_id=blocker_task_id,
                affected_task_ids=affected_task_ids,
                result={"events": []},
                now=now,
            )
        return state

    repo.update(_seed_blockers)
    before = repo.read()
    unrelated_before = next(
        blocker.model_dump(mode="json")
        for blocker in before.blockers.values()
        if blocker.source_id == "unrelated"
    )

    state = _team_admin_service(repo).cancel_task(
        task_id="task",
        reason_code="superseded",
        reason="The work is no longer required.",
        actor_id="operator:test",
    )

    blockers_by_source = {
        (blocker.source_kind, blocker.source_id): blocker
        for blocker in state.blockers.values()
    }
    for source_kind, source_id in (
        (TeamBlockerSourceKind.TASK, "task"),
        (TeamBlockerSourceKind.DELIVERABLE, "deliverable-task"),
        (TeamBlockerSourceKind.REVIEW_REQUEST, "review-task"),
        (TeamBlockerSourceKind.EXPLICIT, "shared-singleton"),
    ):
        blocker = blockers_by_source[(source_kind, source_id)]
        assert blocker.state == TeamBlockerState.RESOLVED
        assert blocker.route == TeamBlockerRoute.RESOLVED

    shared = blockers_by_source[
        (TeamBlockerSourceKind.EXPLICIT, "shared-multi")
    ]
    assert shared.state == TeamBlockerState.OPEN
    assert shared.affected_task_ids == ["other"]
    assert blockers_by_source[
        (TeamBlockerSourceKind.EXPLICIT, "unrelated")
    ].model_dump(mode="json") == unrelated_before
    assert state.tasks["task"].state == TeamTaskState.CANCELLED
    assert state.deliverables["deliverable-task"].state == TeamRecordState.REJECTED
    assert state.review_requests["review-task"].state == TeamRecordState.BLOCKED
    assert state.review_requests["review-task"].decision_at is not None
    assert state.review_requests["review-task"].retired_at is not None
    assert (
        TaskAssessmentService(state).assess("task").invalid_reason_codes == []
    )


def test_apply_agent_action_supervisor_finishes_subordinate_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="w1", owner_agent_id="worker")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "state": "finished"}],
        },
    )

    assert result["success"] is True
    assert state.tasks["w1"].state == TeamTaskState.FINISHED
    assert state.tasks["w1"].owner_agent_id == "worker"
    assert "w1" in result["updated_task_ids"]


def test_apply_agent_action_task_reassignment_requires_target_project_assignment(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_role(
        role_id="project-2__worker",
        team_id="team",
        project_id="project-2",
        name="Second Project Worker",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="worker-2",
        team_id="team",
        project_id="project-2",
        role_id="project-2__worker",
        session_id="session-worker-2",
        reports_to_agent_id="manager",
        expected_revision=repo.read().revision,
    )
    _seed_task(repo, task_id="w1", owner_agent_id="worker")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "task_updates": [{"task_id": "w1", "owner_agent_id": "worker-2"}],
        },
    )

    assert result["success"] is False
    assert any("outside project project" in error for error in result["errors"])
    assert state.tasks["w1"].owner_agent_id == "worker"


def test_apply_agent_action_task_reassignment_requires_superior(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="b1", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "task_updates": [{"task_id": "b1", "owner_agent_id": "worker"}],
        },
    )

    assert result["success"] is False
    assert any("not permitted to reassign" in error for error in result["errors"])
    assert state.tasks["b1"].owner_agent_id == "backend"


def test_apply_agent_action_rejects_terminal_task_reassignment(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="w1", owner_agent_id="worker")
    _team_admin_service(repo).update_task(task_id="w1", state=TeamTaskState.FINISHED)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "owner_agent_id": "backend"}],
        },
    )

    assert result["success"] is False
    assert any("terminal" in error for error in result["errors"])
    assert state.tasks["w1"].owner_agent_id == "worker"


def test_apply_agent_action_lead_and_manager_edit_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="t1", owner_agent_id="backend")
    _seed_task(repo, task_id="t2", owner_agent_id="backend")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "t1", "dependency_task_ids": ["t2"]}],
        },
    )
    assert result["success"] is True
    assert state.tasks["t1"].dependency_task_ids == ["t2"]

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "task_updates": [{"task_id": "t1", "dependency_task_ids": []}],
        },
    )
    assert result["success"] is True
    assert state.tasks["t1"].dependency_task_ids == []


def test_apply_agent_action_unrelated_agent_cannot_edit_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="b1", owner_agent_id="backend")
    _seed_task(repo, task_id="b2", owner_agent_id="backend")

    # worker reports to backend; it does not supervise backend and is not a lead/manager.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "task_updates": [{"task_id": "b1", "dependency_task_ids": ["b2"]}],
        },
    )

    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.tasks["b1"].dependency_task_ids == []


def test_apply_agent_action_non_owner_state_change_still_rejected(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="b1", owner_agent_id="backend")

    # lead supervises backend and leads the project, but state edits remain owner-only.
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "b1", "state": "in_progress"}],
        },
    )

    assert result["success"] is False
    assert any("cannot update task" in error for error in result["errors"])
    assert state.tasks["b1"].state == TeamTaskState.PENDING


def _seed_review_profile(
    repo: TeamStateRepository,
    *,
    profile_id: str = "profile",
    project_id: str | None = "project",
    applicable_role_ids: list[str] | None = None,
    criteria: list[ReviewProfileCriterion] | None = None,
    required_outcome_kinds: list[str] | None = None,
) -> None:
    for kind in required_outcome_kinds or []:
        _seed_outcome_kind(repo, kind, project_id=project_id)
    _team_admin_service(repo).create_review_profile(
        profile_id=profile_id,
        team_id="team",
        project_id=project_id,
        name="Backend review",
        description="Checklist for backend deliverables.",
        applicable_role_ids=(
            applicable_role_ids
            if applicable_role_ids is not None
            else ["project__backend", "project__worker"]
        ),
        criteria=criteria
        or [
            ReviewProfileCriterion(id="tests", label="Tests pass", required=True),
            ReviewProfileCriterion(
                id="docs", label="Docs updated", description="README/CHANGELOG", required=False
            ),
        ],
        required_outcome_kinds=required_outcome_kinds,
    )


def _seed_outcome_kind(
    repo: TeamStateRepository,
    kind: str,
    *,
    project_id: str | None = "project",
) -> None:
    state = repo.read()
    if any(
        definition.team_id == "team"
        and definition.project_id == project_id
        and definition.kind == kind
        for definition in state.outcome_kind_definitions.values()
    ):
        return
    _team_admin_service(repo).create_outcome_kind_definition(
        definition_id=f"outcome-kind-{kind}",
        team_id="team",
        project_id=project_id,
        kind=kind,
        name=kind.replace("_", " ").title(),
        description=f"Definition for {kind}.",
        reference_requirements="Provide a durable reference.",
        evidence_requirements="Provide supporting evidence.",
    )


def test_create_update_archive_review_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_review_profile(repo, required_outcome_kinds=["prepared_action"])

    state = repo.read()
    profile = state.review_profiles["profile"]
    assert profile.team_id == "team"
    assert profile.name == "Backend review"
    assert [c.id for c in profile.criteria] == ["tests", "docs"]
    assert profile.required_outcome_kinds == ["prepared_action"]
    assert profile.applicable_role_ids == ["project__backend", "project__worker"]
    assert profile.archived is False

    _seed_outcome_kind(repo, "final_action")
    state = _team_admin_service(repo).update_review_profile(
        profile_id="profile",
        name="Renamed review",
        criteria=[ReviewProfileCriterion(id="tests", label="Tests pass")],
        required_outcome_kinds=["final_action"],
        archived=True,
    )
    profile = state.review_profiles["profile"]
    assert profile.name == "Renamed review"
    assert [c.id for c in profile.criteria] == ["tests"]
    assert profile.required_outcome_kinds == ["final_action"]
    assert profile.archived is True


def test_create_review_profile_rejects_duplicate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_review_profile(repo)

    with pytest.raises(ValueError, match="already exists"):
        _seed_review_profile(repo)


def test_apply_agent_action_review_request_with_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                    "instructions": "Use the checklist.",
                }
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.review_profile_id == "profile"
    assert review.instructions == "Use the checklist."
    inbox_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.STATUS_REQUEST
        and item.recipient_agent_id == "worker"
    ]
    assert len(inbox_items) == 1
    payload = inbox_items[0].payload
    assert payload["instructions"] == "Use the checklist."
    assert payload["review_profile"]["id"] == "profile"
    assert payload["review_profile"]["name"] == "Backend review"
    criterion_ids = [c["id"] for c in payload["review_profile"]["criteria"]]
    assert criterion_ids == ["tests", "docs"]
    assert payload["review_profile"]["criteria"][0]["required"] is True


def test_apply_agent_action_review_request_auto_selects_matching_profile(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo, applicable_role_ids=["project__worker"])

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Use the matching checklist.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.review_requests["review-1"].review_profile_id == "profile"
    inbox_item = next(
        item for item in state.inbox.values() if item.payload.get("review_request_id") == "review-1"
    )
    assert inbox_item.payload["review_profile"]["id"] == "profile"


def test_apply_agent_action_review_request_ambiguous_auto_profile_errors(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(
        repo,
        profile_id="profile-a",
        applicable_role_ids=["project__worker"],
    )
    _seed_review_profile(
        repo,
        profile_id="profile-b",
        applicable_role_ids=["project__worker"],
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("multiple review profiles apply" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_archived_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _team_admin_service(repo).update_review_profile(profile_id="profile", archived=True)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("archived" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_cross_project_profile_errors(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_project(
        project_id="project-2",
        team_id="team",
        name="Other project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        goals=[ProjectGoal(id="goal-2", description="Other work")],
    )
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    # Profile is scoped to a different project than the deliverable's task ("project").
    _seed_review_profile(repo, project_id="project-2", applicable_role_ids=[])

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("does not apply to project" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_role_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo, applicable_role_ids=["project__backend"])

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("reviewer role project__worker" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_missing_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "missing",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("missing review profile" in error for error in result["errors"])
    assert state.review_requests == {}


def _request_review_with_profile(repo: TeamStateRepository) -> None:
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                    "instructions": "Use the checklist.",
                }
            ],
        },
    )


def test_apply_agent_action_review_decision_records_criteria(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "findings": ["Looks good."],
                    "criteria": [
                        {"criterion_id": "tests", "state": "passed", "finding": "All green."},
                        {"criterion_id": "docs", "state": "skipped"},
                    ],
                }
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.findings == ["Looks good."]
    assert {c.criterion_id: c.state for c in review.criteria} == {
        "tests": "passed",
        "docs": "skipped",
    }
    tests_result = next(c for c in review.criteria if c.criterion_id == "tests")
    assert tests_result.finding == "All green."
    # Criterion results never alter acceptance cascade.
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.FINISHED


def test_apply_agent_action_review_decision_unknown_criterion_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "nope", "state": "passed"}],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("unknown criterion nope" in error for error in result["errors"])
    review = state.review_requests["review-1"]
    assert review.criteria == []
    assert review.state == TeamRecordState.PENDING


def test_apply_agent_action_review_decision_criteria_without_profile_errors(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                }
            ],
        },
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("no review profile" in error for error in result["errors"])
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING


def test_apply_agent_action_review_decision_requires_profile_criteria(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [{"review_request_id": "review-1", "state": "accepted"}],
        },
    )

    assert result["success"] is False
    assert any("missing required criterion" in error for error in result["errors"])
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.PENDING
    assert review.criteria == []


@pytest.mark.parametrize("criterion_state", ["failed", "skipped"])
def test_accepted_review_requires_required_criteria_to_pass(
    tmp_path: Path,
    criterion_state: str,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": criterion_state}],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("required criteria that did not pass" in error for error in result["errors"])
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING


def test_review_findings_survive_rework_and_require_matching_profile_resolution(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, rejected = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "rejected",
                    "findings": ["PLC administration UI is missing."],
                    "criteria": [
                        {
                            "criterion_id": "tests",
                            "state": "failed",
                            "finding": "OperatorClient spec does not compile.",
                        }
                    ],
                }
            ],
        },
    )
    assert rejected["success"] is True
    assert len(state.deliverables["deliverable"].carried_findings) == 2

    _team_admin_service(repo).record_deliverable(
        deliverable_id="deliverable-v2",
        task_id="task",
        producer_agent_id="backend",
        summary="Reworked deliverable.",
        supersedes_deliverable_id="deliverable",
    )
    _seed_review_profile(
        repo,
        profile_id="wrong-profile",
        applicable_role_ids=["project__backend"],
    )
    state, requested = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "wrong-review",
                    "deliverable_id": "deliverable-v2",
                    "reviewer_agent_id": "backend",
                    "review_profile_id": "wrong-profile",
                }
            ],
        },
    )
    assert requested["success"] is True
    carried_ids = [finding.id for finding in state.deliverables["deliverable-v2"].carried_findings]
    assert len(carried_ids) == 2

    state, wrong = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "review_decisions": [
                {
                    "review_request_id": "wrong-review",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                    "finding_resolutions": [
                        {
                            "finding_id": carried_ids[0],
                            "resolution": "Claimed fixed by another profile.",
                        }
                    ],
                }
            ],
        },
    )
    assert wrong["success"] is False
    assert any("matching review profile" in error for error in wrong["errors"])
    assert all(
        finding.state == "open" for finding in state.deliverables["deliverable-v2"].carried_findings
    )
    state, wrong_accepted = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "review_decisions": [
                {
                    "review_request_id": "wrong-review",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )
    assert wrong_accepted["success"] is True
    assert state.deliverables["deliverable-v2"].state == TeamRecordState.PENDING

    _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-2",
                    "deliverable_id": "deliverable-v2",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )
    state, resolved = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-2",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                    "finding_resolutions": [
                        {
                            "finding_id": finding_id,
                            "resolution": "Fixed and rechecked.",
                            "evidence_refs": ["test://focused"],
                        }
                        for finding_id in carried_ids
                    ],
                }
            ],
        },
    )

    assert resolved["success"] is True
    assert state.deliverables["deliverable-v2"].state == TeamRecordState.ACCEPTED
    assert all(
        finding.state == "resolved" and finding.resolved_by_review_request_id == "review-2"
        for finding in repo.read().deliverables["deliverable-v2"].carried_findings
    )


def test_record_review_decision_repository_stores_criteria(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _team_admin_service(repo).record_review_request(
        review_request_id="review-1",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        review_profile_id="profile",
        instructions="Operator instructions.",
    )

    from evander_team_addon.models import ReviewCriterionResult

    state = _team_admin_service(repo).record_review_decision(
        review_request_id="review-1",
        state=TeamRecordState.ACCEPTED,
        findings=["ok"],
        criteria=[ReviewCriterionResult(criterion_id="tests", state="passed")],
    )

    review = state.review_requests["review-1"]
    assert review.review_profile_id == "profile"
    assert review.instructions == "Operator instructions."
    assert review.state == TeamRecordState.ACCEPTED
    assert [c.criterion_id for c in review.criteria] == ["tests"]


def test_register_teamless_agent(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = _team_admin_service(repo).register_agent(
        agent_id="unassigned",
        session_id="session-unassigned",
        display_name="Unassigned",
        expected_revision=repo.read().revision,
    )

    agent = state.agents["unassigned"]
    assert agent.team_id is None
    assert agent_project_ids(state, "unassigned") == []
    assert agent.role_id is None
    assert agent.display_name == "Unassigned"


def test_remove_agent_from_team_blocks_structural_references(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValueError, match="cannot be removed from team"):
        _team_admin_service(repo).update_agent(
            agent_id="manager",
            team_id=None,
            set_team_id=True,
            expected_revision=repo.read().revision,
        )


def test_remove_unreferenced_agent_from_team(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_role(
        role_id="team__observer",
        team_id="team",
        name="Observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="observer",
        team_id="team",
        role_id="team__observer",
        session_id="session-observer",
        expected_revision=repo.read().revision,
    )

    state = _team_admin_service(repo).update_agent(
        agent_id="observer",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    agent = state.agents["observer"]
    assert agent.team_id is None
    assert agent.role_id is None


def test_terminal_dispatch_authority_preserves_reviewer_team_history(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    admin = _team_admin_service(repo)
    admin.create_role(
        role_id="project__reviewer",
        team_id="team",
        project_id="project",
        name="Reviewer",
        expected_revision=repo.read().revision,
    )
    admin.register_agent(
        agent_id="reviewer",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-reviewer",
        expected_revision=repo.read().revision,
    )
    _seed_task(repo, task_id="review-task")
    _seed_deliverable(
        repo,
        deliverable_id="review-deliverable",
        task_id="review-task",
    )
    leased_at = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_live_dispatch(state):
        state.dispatches["manager-fallback"] = TeamDispatch(
            id="manager-fallback",
            agent_id="manager",
            session_id="session-manager",
            cause_kind="manager_fallback",
            review_routing_authorities=[
                TeamDispatchReviewRoutingAuthority(
                    kind="reviewer_without_assigned_review_work",
                    reviewer_agent_id="reviewer",
                    project_id="project",
                    task_id="review-task",
                    deliverable_id="review-deliverable",
                )
            ],
            status=TeamDispatchStatus.LEASED,
            prepared_at=leased_at,
            leased_at=leased_at,
            lease_expires_at=leased_at + timedelta(minutes=15),
        )
        return state

    repo.update(_add_live_dispatch)

    with pytest.raises(
        ValidationError,
        match="review routing agent reviewer does not belong to project team team",
    ):
        admin.update_agent(
            agent_id="reviewer",
            team_id=None,
            set_team_id=True,
            expected_revision=repo.read().revision,
        )

    assert repo.read().agents["reviewer"].team_id == "team"

    def _complete_dispatch(state):
        dispatch = state.dispatches["manager-fallback"]
        dispatch.status = TeamDispatchStatus.COMPLETED
        dispatch.completed_at = leased_at + timedelta(minutes=1)
        return state

    repo.update(_complete_dispatch)
    state = admin.update_agent(
        agent_id="reviewer",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    assert state.agents["reviewer"].team_id is None
    assert state.dispatches["manager-fallback"].status == TeamDispatchStatus.COMPLETED
    assert (
        state.dispatches["manager-fallback"].review_routing_authorities[0].reviewer_agent_id
        == "reviewer"
    )


def test_remove_agent_from_team_preserves_terminal_task_history(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_role(
        role_id="team__observer",
        team_id="team",
        name="Observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="observer",
        team_id="team",
        role_id="team__observer",
        session_id="session-observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="completed-history",
        owner_agent_id="observer",
        project_id="project",
        priority=1,
        title="Completed history",
        description="This completed task should retain historical ownership.",
        created_by_agent_id="observer",
    )
    repo.update(lambda state: _mark_task_state(state, "completed-history", TeamTaskState.ACCEPTED))

    state = _team_admin_service(repo).update_agent(
        agent_id="observer",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    assert state.agents["observer"].team_id is None
    assert state.tasks["completed-history"].owner_agent_id == "observer"


def test_remove_agent_from_team_preserves_deliverable_and_sender_history(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_role(
        role_id="team__observer",
        team_id="team",
        name="Observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="observer",
        team_id="team",
        role_id="team__observer",
        session_id="session-observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="active-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Active task",
        description="Observer produced an audit artifact for this work.",
        created_by_agent_id="lead",
    )
    _team_admin_service(repo).record_deliverable(
        deliverable_id="audit-deliverable",
        task_id="active-task",
        producer_agent_id="observer",
        summary="Audit report.",
    )
    repo.update(
        lambda state: _mark_deliverable_state(
            state,
            "audit-deliverable",
            TeamRecordState.ACCEPTED,
        )
    )
    repo.update(
        lambda state: _add_inbox_item(
            state,
            item_id="sent-history",
            recipient_agent_id="lead",
            sender_agent_id="observer",
        )
    )

    state = _team_admin_service(repo).update_agent(
        agent_id="observer",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    assert state.agents["observer"].team_id is None
    assert state.deliverables["audit-deliverable"].producer_agent_id == "observer"
    assert state.inbox["sent-history"].sender_agent_id == "observer"


def test_remove_agent_from_team_blocks_active_deliverable_producer(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_role(
        role_id="team__observer",
        team_id="team",
        name="Observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="observer",
        team_id="team",
        role_id="team__observer",
        session_id="session-observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="active-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Active task",
        description="Observer produced an active audit artifact for this work.",
        created_by_agent_id="lead",
    )
    _team_admin_service(repo).record_deliverable(
        deliverable_id="audit-deliverable",
        task_id="active-task",
        producer_agent_id="observer",
        summary="Audit report.",
    )

    with pytest.raises(ValueError, match="produced deliverable audit-deliverable"):
        _team_admin_service(repo).update_agent(
            agent_id="observer",
            team_id=None,
            set_team_id=True,
            expected_revision=repo.read().revision,
        )


def test_remove_agent_from_team_blocks_pending_inbox_recipient(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _team_admin_service(repo).create_role(
        role_id="team__observer",
        team_id="team",
        name="Observer",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="observer",
        team_id="team",
        role_id="team__observer",
        session_id="session-observer",
        expected_revision=repo.read().revision,
    )
    repo.update(
        lambda state: _add_inbox_item(
            state,
            item_id="inbox-to-observer",
            recipient_agent_id="observer",
            sender_agent_id="lead",
        )
    )

    with pytest.raises(ValueError, match="recipient of inbox item inbox-to-observer"):
        _team_admin_service(repo).update_agent(
            agent_id="observer",
            team_id=None,
            set_team_id=True,
            expected_revision=repo.read().revision,
        )


def _mark_task_state(state, task_id: str, state_value: TeamTaskState):
    state.tasks[task_id].state = state_value
    return state


def _mark_deliverable_state(state, deliverable_id: str, state_value: TeamRecordState):
    state.deliverables[deliverable_id].state = state_value
    return state


def _add_inbox_item(
    state,
    *,
    item_id: str,
    recipient_agent_id: str,
    sender_agent_id: str,
):
    state.inbox[item_id] = AgentInboxItem(
        id=item_id,
        recipient_agent_id=recipient_agent_id,
        sender_agent_id=sender_agent_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        state=TeamInboxItemState.PENDING,
    )
    return state
